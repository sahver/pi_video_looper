
# Copyright 2015 Adafruit Industries.
# Author: Tony DiCola
# License: GNU GPLv2, see LICENSE.txt

import configparser
import ffmpeg
import os
import pygame
import random
import re
import requests
import signal
import socket
import threading
import time

from datetime import datetime
from pathlib import Path
from pythonosc.dispatcher import Dispatcher
from pythonosc import osc_server, udp_client

class CloudReader:

    REGEX_GENRE = re.compile(r'^x=(?P<x>\d+):y=(?P<y>\d+):w=(?P<w>\d+):h=(?P<h>\d+):q=(?P<q>.+)$')

    def __init__(self, config_parent, config_path='/boot/video_cloud.ini'):
        """Create an instance of a file reader that renders needed videos in the cloud."""

        # Load config
        self._config_parent = config_parent
        self._config_path = config_path
        self._config = self._load_config(self._config_path, self._config_parent)
        self._console_output = config_parent.getboolean('video_looper', 'console_output')

        # Parent config
        self._path = config_parent.get('directory', 'path')
        self._extensions = config_parent.get('omxplayer', 'extensions') \
                                 .translate(str.maketrans('', '', ' \t\r\n.')) \
                                 .split(',')
        self._filecount = self.count_files()

        # Route /addresses
        self._dispatcher = Dispatcher()

        self._dispatcher.map(f'/connect', self._cmd_connect)
        self._dispatcher.map(f'/pull', self._cmd_pull)
        self._dispatcher.map(f'/purge', self._cmd_purge)
        self._dispatcher.map(f'/reboot', self._cmd_reboot)
        self._dispatcher.map(f'/quit', self._cmd_quit)
        
        self._dispatcher.map(f'/{self._id}/pull', self._cmd_pull)
        self._dispatcher.map(f'/{self._id}/purge', self._cmd_purge)
        self._dispatcher.map(f'/{self._id}/quit', self._cmd_quit)
        self._dispatcher.map(f'/{self._id}/reboot', self._cmd_reboot)
        self._dispatcher.map(f'/{self._id}/update', self._cmd_update)
        
        self._dispatcher.set_default_handler(self._cmd_default)

        # Listen
        self._server = None
        self._connect()

        # Cloud renderer
        self._cloud = udp_client.SimpleUDPClient(self._cloud_host, self._cloud_port)
        self._cloud_job_id = None
        self._cloud_update_freq = 0.5

    def _load_config(self, config_path, config_parent):

        config = configparser.ConfigParser()
        if len(config.read(config_path)) == 0:
            raise RuntimeError('Failed to find cloud configuration file at {0}, is the application properly installed?'.format(config_path))
        
        # Cloud
        self._cloud_host = config.get('cloud', 'host')
        self._cloud_port = config.getint('cloud', 'port')

        # Crop
        self._crop_x = config.getfloat('crop', 'x')
        self._crop_y = config.getfloat('crop', 'y')
        self._crop_w = config.getfloat('crop', 'width')
        self._crop_h = config.getfloat('crop', 'height')
        self._quality = config.get('crop', 'quality')

        # Router
        self._router_host = config.get('router', 'host')
        self._router_port = config.getint('router', 'port')

        # Screen
        self._id = config.getint('screen', 'id')
        self._resolution = config.get('screen', 'resolution')

        return config

    def _save_config(self, config, config_path):

        # Cloud
        config['cloud'] = {
            'host'          : self._cloud_host,
            'port'          : self._cloud_port,
        }

        # Crop
        config['crop'] = {
            'x'             : self._crop_x,
            'y'             : self._crop_y,
            'width'         : self._crop_w,
            'height'        : self._crop_h,
            'quality'       : self._quality,
        }

        # Router
        config['router'] = {
            'host'          : self._router_host,
            'port'          : self._router_port,
        }

        # Screen
        config['screen'] = {
            'id'            : self._id,
            'resolution'    : self._resolution
        }

        # Save
        with open(config_path, 'w') as cfg:
            config.write(cfg)

        self._print(f'Cloud configuration saved to {config_path}')

    def _connect(self):
        self._server = osc_server.ThreadingOSCUDPServer((self._router_host, self._router_port), self._dispatcher)
        thread = threading.Thread(target=self._server.serve_forever)
        thread.start()
        self._print('Router listening at {}:{}'.format(self._router_host, self._router_port))

    def _wait_for_cloud_reply(self, addr, args):

        reply = None

        for i in range(5):

            self._cloud.send_message(addr, args)

            try:
                reply = next(self._cloud.get_messages(self._get_scattered_update_freq()))
                break
            except socket.timeout as err:
                self._print(f'Error: {err}')
                time.sleep(self._get_scattered_update_freq())

        # Nonii
        return str(reply).strip() if reply else None

    def _get_scattered_update_freq(self):
        return (self._cloud_update_freq*0.75) + ( (self._cloud_update_freq*0.5) * random.random() )

    #
    # ** RENDERING **
    #

    def _render(self):
        self._print(f'@render')

        # Reset and start cloud render
        self._cloud_job_id = None
#        self._cloud.send_message('/queue', [self._id, self._crop_w, self._crop_h, self._crop_x, self._crop_y, self._quality])

        # Wait for response
        if reply := self._wait_for_cloud_reply('/queue', [self._id, self._crop_x, self._crop_y, self._crop_w, self._crop_h, self._quality, self._resolution]):
            self._print(f'{reply}')
            
            self._cloud_job_id = reply.split('=')[-1]
            self._print(f'Job {self._cloud_job_id} added to queue.')

            # Draw
            screen = pygame.display.get_surface()

            # Ping until done
            while True:

                # How are we doin'?
#                self._cloud.send_message('/status', [self._cloud_job_id])

                # A response!
                if reply := self._wait_for_cloud_reply('/status', [self._cloud_job_id]):
                    
                    self._print(f'{self._cloud_job_id}: {reply}')
                    key, val = reply.split('=', 1)

#                    match key:

                    # Queue
                    if key == 'queue':

                        # bg
                        screen.fill((0, 0, 255))

                        # #
                        label = pygame.font.Font(None, 250).render(f'{"|" * int(val)}', True, (255, 0, 0))
                        #label = pygame.font.Font(None, 250).render(f'q={val}', True, (255, 255, 255))
                        lw, lh = label.get_size()
                        sw, sh = screen.get_size()
                        screen.blit(label, (sw/2-lw/2, sh/2-lh/2))

                        # show
                        pygame.display.update()

                    # Progress
                    elif key == '%':

                        pos = float(val)
                        self._print(f'{self._cloud_job_id}: {pos*100:.1f}%')

                        # bg
                        screen.fill((255, 0, 0))

                        # progress
                        if pos > 0:
                            pygame.draw.rect(
                                screen,
                                (0, 255, 0),
                                pygame.Rect(0, 0, int( pygame.display.Info().current_w*pos ), pygame.display.Info().current_h)
                            )

                            # %
                            label = pygame.font.Font(None, 250).render(f'{(pos*100):3.0f}%', True, (0, 0, 0))
                            lw, lh = label.get_size()
                            sw, sh = screen.get_size()
                            screen.blit(label, (sw/2-lw/2, sh/2-lh/2))
                        
                        # show
                        pygame.display.update()

                    # Download
                    elif key == 'ready':

                        # bg
                        screen.fill((0, 255, 0))
                        pygame.display.update()

                        # Save from
                        url = f'http://{self._cloud_host}:{self._cloud_port-1}/{val}'
                        # Save to
                        out = Path(self._path) / val
                        out = out.with_suffix(out.suffix + '.hidden')
                        # Create dirs if needed
                        out.parent.mkdir(parents=True, exist_ok=True)

                        # Do we have it already?
                        if out.exists():
                            self._print(f'Already cached, renaming {out.as_posix()} -> {out.parent}/{type(self).__name__}{out.with_suffix("").suffix} ..')
                            out.rename(out.parent / f'{type(self).__name__}{out.with_suffix("").suffix}')

                        # If not, then download
                        else:
                            self._print(f'Downloading {url} ..')
                            with open(out, 'wb') as f:
                                with requests.get(url, stream=True) as r:
                                    r.raise_for_status()
                                    block_size = 1024
                                    file_size = int(r.headers.get('content-length', None))
                                    now = then = datetime.now().timestamp()
                                    for i, chunk in enumerate(r.iter_content(chunk_size=block_size)):
                                        f.write(chunk)

                                        now = datetime.now().timestamp()

                                        if (now - then) > self._get_scattered_update_freq(): 

                                            then = now

                                            # Clamp to 100%
                                            pos = min( (i * block_size)/file_size, 1 )
                                            self._print(f'{val}: {int(pos*100)}%')

                                            # bg
                                            screen.fill((0, 255, 0))
#                                            screen.fill((0, 255-int(255*pos), 0))

                                            # progress
                                            if pos > 0:
                                                pygame.draw.rect(
                                                    screen,
                                                    (0, 0, 0),
#                                                    (255-int(255*pos), 255-int(255*pos), 255-int(255*pos)),
#                                                    (0, 0, 255-int(255*pos)),
                                                    pygame.Rect(int((1-pos) * pygame.display.Info().current_w), 0, pygame.display.Info().current_w, pygame.display.Info().current_h)
                                                )

                                            # %
                                            label = pygame.font.Font(None, 250).render(f'{(pos*100):3.0f}%', True, (0, 0, 0))
                                            lw, lh = label.get_size()
                                            sw, sh = screen.get_size()
                                            screen.blit(label, (sw/2-lw/2, sh/2-lh/2))
                                            
                                            # show
                                            pygame.display.update()

                            # Download done.
                            self._print(f'Complete, renaming {out.as_posix()} -> {out.parent}/{type(self).__name__}{out.with_suffix("").suffix} ..')
                            out.rename(out.parent / f'{type(self).__name__}{out.with_suffix("").suffix}')
                            self._print(f'✓')

                        # Job done.
                        self._cloud_job_id = None
                        break

                # Pause
                time.sleep(self._get_scattered_update_freq())

        # Confirm
        self._print('@render done.')

    #
    # Commands
    #

    def _cmd_default(self, unused_addr, *args):
        self._print(f'@: {unused_addr} {args}')

    def _cmd_connect(self, addr, host, port):
        self._print(f'@connect: {addr} {host} {port}')

        if (
            self._cloud_host != host
            or self._cloud_port != port
        ):
            self._cloud_host = host
            self._cloud_port = port
            self._save_config(self._config, self._config_path)

    def _cmd_pull(self, addr):
        self._print(f'@pull: {addr}')

        # Task
        out = Path.cwd() / '.cloud.pull'
        out.write_text(str(datetime.now()))

        # Quit
        self._cmd_quit(addr)

    def _cmd_purge(self, addr):
        self._print(f'@purge: {addr}')

        for f in Path(self._path).iterdir():
            if (
                f.is_file()
                and (
                    f.suffix == '.hidden'
                    or
                    f.suffix[1:] in self._extensions
                )
            ):
                self._print(f'Deleting {f.as_posix()} ..')
                f.unlink()

    def _cmd_quit(self, addr):
        self._print(f'@quit: {addr}')
        self._server.shutdown()
        os.kill(os.getpid(), signal.SIGINT)

    def _cmd_reboot(self, addr):
        self._print(f'@reboot: {addr}')

        # Task
        out = Path.cwd() / '.cloud.reboot'
        out.write_text(str(datetime.now()))

        # Quit
        self._cmd_quit(addr)

    def _cmd_update(self, addr, x, y, w, h, rw, rh, q):
        self._print(f'@update: {addr} {x} {y} {w} {h} {rw} {rh} {q}')

        # Do we need to re-render?
        render = False
        if (
            not self.count_files()
            or self._crop_w != w
            or self._crop_h != h
            or self._crop_x != x
            or self._crop_y != y
            or self._quality != q
        ): render = True

        # Save
        self._crop_w = w
        self._crop_h = h
        self._crop_x = x
        self._crop_y = y
        self._quality = q
        self._resolution = f'{int(rw)}x{int(rh)}'

        self._save_config(self._config, self._config_path)

        # Render if needed
        if render and not self._cloud_job_id:
            self._hide_files()
            self._render()

    #
    # File reader
    #

    def search_paths(self):
        """Return a list of paths to search for files."""
#        print('** search_paths()')
        return [self._path]

    def is_changed(self):
        """Return true if the number of files in the paths have changed."""
        current_count = self.count_files()
#        print(f'** is_changed(): current={current_count} filecount={self._filecount} path={self._path}')
        if current_count != self._filecount:
            self._filecount = current_count
            return True
        else:
            return False

    def idle_message(self):
        """Return a message to display when idle and no files are found."""
#        print('** idle_message()')
        return f'{self._id}'

    def count_files(self):
#        print('** count_files()')
#        print( sorted(filter(lambda path: path.suffix.lower()[1:] in self._extensions, Path(self._path).glob('*'))) )
        return len( sorted(filter(lambda path: path.suffix.lower()[1:] in self._extensions, Path(self._path).glob('*'))) )

    #
    # Utils
    #

    def _hide_files(self):
        for ext in self._extensions:
            for f in Path(self._path).glob(f'**/*.{ext}'):
                query = ffmpeg.probe(f.as_posix())

                # Rename based on metadata
                if (
                    'format' in query
                    and 'tags' in query['format']
                    and 'genre' in query['format']['tags']
                ):
                    if m := CloudReader.REGEX_GENRE.search(query['format']['tags']['genre']):
                        filename = f"{m.group('q').upper()}_y{m.group('y')}_w{m.group('w')}_h{m.group('h')}{f.suffix}.hidden"
                        self._print(f'Caching, renaming {f.as_posix()} -> {f.parent.as_posix()}/{filename} ..')
                        f.rename(f.parent / filename)

                # Delete if required metadata is missing
                else:
                    self._print(f'Failis {f.as_posix()} pole vajalikke metaandmeid, kustutame.')
                    f.unlink()

    def _print(self, message=None, end='\n'):
        if self._console_output:
            print(f'{chr(13) if not end else ""} [{datetime.now()}] {message: <50}', end='\n', flush=True) if message else print()

#
# Called from VideoLooper
#

def create_file_reader(config, screen):
    """Create new file reader based on reading a directory on disk."""
    return CloudReader(config)

