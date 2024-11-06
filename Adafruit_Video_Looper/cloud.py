
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
from hashlib import blake2s
from pathlib import Path
from pythonosc.dispatcher import Dispatcher
from pythonosc import osc_server, udp_client

class CloudGrid:

    REGEX_PARAMETERS = re.compile(r'^q=(?P<q>.+):x=(?P<x>\d+):y=(?P<y>\d+):w=(?P<w>\d+):h=(?P<h>\d+):r=(?P<sw>\d+)x(?P<sh>\d+)$')

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
        self._dispatcher.map(f'/delete', self._cmd_delete)
        self._dispatcher.map(f'/pause', self._cmd_pause)
        self._dispatcher.map(f'/ping', self._cmd_ping)
        self._dispatcher.map(f'/play', self._cmd_play)
        self._dispatcher.map(f'/pull', self._cmd_pull)
        self._dispatcher.map(f'/purge', self._cmd_purge)
        self._dispatcher.map(f'/reboot', self._cmd_reboot)
        self._dispatcher.map(f'/quit', self._cmd_quit)
        self._dispatcher.map(f'/setup', self._cmd_setup)
        
        self._dispatcher.map(f'/{self._id}/delete', self._cmd_delete)
        self._dispatcher.map(f'/{self._id}/diff', self._cmd_diff)
        self._dispatcher.map(f'/{self._id}/ping', self._cmd_ping)
        self._dispatcher.map(f'/{self._id}/pull', self._cmd_pull)
        self._dispatcher.map(f'/{self._id}/purge', self._cmd_purge)
        self._dispatcher.map(f'/{self._id}/quit', self._cmd_quit)
        self._dispatcher.map(f'/{self._id}/reboot', self._cmd_reboot)
        self._dispatcher.map(f'/{self._id}/update', self._cmd_update)
        
#        self._dispatcher.set_default_handler(self._cmd_print)

        # Cloud renderer
        self._cloud = None
        self._cloud_job_id = None
        self._cloud_update_freq = 0.5

        # Router
        self._router = None
        self._router_listen()

        # Drawing
        self._display = pygame.display.get_surface()
        self._display_w, self._display_h = self._display.get_size()
        self._font_huge = pygame.font.Font(None, 500)
        self._font_big = pygame.font.Font(None, 250)

        # Initialize player
        threading.Thread(target=self._player_send, args=[f'%diff={self._player_diff}']).start()

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

        # Player
        self._player_port = config.getint('player', 'port')
        self._player_diff = config.getfloat('player', 'diff')

        # Router
        self._router_host = config.get('router', 'host')
        self._router_port = config.getint('router', 'port')

        # Screen
        self._id = config.getint('screen', 'id')
        self._screen_w = config.getint('screen', 'width')
        self._screen_h = config.getint('screen', 'height')

        # Video
        self._video_length = config.getint('video', 'length')
        self._video_speed = config.getfloat('video', 'speed')

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

        # Player
        config['player'] = {
            'port'          : self._player_port,
            'diff'          : self._player_diff,
        }

        # Router
        config['router'] = {
            'host'          : self._router_host,
            'port'          : self._router_port,
        }

        # Screen
        config['screen'] = {
            'id'            : self._id,
            'width'         : self._screen_w,
            'height'        : self._screen_h,
        }

        # Video
        config['video'] = {
            'length'        : self._video_length,
            'speed'         : self._video_speed,
        }

        # Save
        with open(config_path, 'w') as cfg:
            config.write(cfg)

        self._print(f'Cloud configuration saved to {config_path}')

    def _calc_diff(self):
        self._print(f'_calc_diff(): y={self._crop_y} len={self._video_length} speed={self._video_speed} .. pos={(self._video_length - (self._video_length * (self._crop_y + self._crop_h))) * self._video_speed}')
        return (self._video_length - (self._video_length * (self._crop_y + self._crop_h))) / self._video_speed

    def _router_listen(self):
        # If already connected, shutdown first
        if self._router:
            self._router.shutdown()
            self._router = None

        # Connect
        self._router = osc_server.ThreadingOSCUDPServer((self._router_host, self._router_port), self._dispatcher)
        threading.Thread(target=self._router.serve_forever).start()
        self._print('Router listening at {}:{}'.format(self._router_host, self._router_port))

    def _cloud_wait_for_reply(self, addr, args):

        reply = None

        # Send
        self._cloud.send_message(addr, args)

        # Wait for reply
        for i in range(5):

            try:
                reply = next(self._cloud.get_messages(self._get_scattered_update_freq()))
                # Try to get the last message
#                break
            except socket.timeout as err:
                # No answer yet
                if not reply:
                    self._print(f'_cloud_wait_for_reply(): {err}')
                    self._display_error(err)
                    time.sleep(self._get_scattered_update_freq())
                # We have an answer,
                # so we should be good
                else: break

        # Nonii
        return str(reply).strip() if reply else None

    def _player_send(self, msg):

        player = None
        success = False

        # Retry a few times
        for i in range(5):
            try:
                # Connect
#                self._print('Connecting to player at {}:{} ({})'.format('127.0.0.1', self._player_port, i))
                player = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
                player.settimeout(3)
                player.connect(('127.0.0.1', self._player_port))
                # Send
                player.sendall((msg).encode('utf-8'))
                # Wait for confirmation
                r = player.recvfrom(1024)
#                self._print(r[0].decode('utf-8'))
                # All good
                if r[0].decode('utf-8') == f'OK {msg}':
                    success = True
            # Probably server not up yet, retry
            except ConnectionRefusedError as err:
#                self._print(f'_player_send(): {err}')
                time.sleep(3)
            finally:
                player.close()

            # If we succeeded then no more retries needed
            if success: break

        # Show error if all retries failed
        if not success:
            raise Exception(f'Unable to send data ({msg}) to omxplayer')

    def _get_scattered_update_freq(self):
        return (self._cloud_update_freq*0.9) + ( (self._cloud_update_freq*0.2) * random.random() )

    #
    # ** RENDERING **
    #

    def _render(self, x, y, w, h, sw, sh, q):
        self._print(f'@render: {x} {y} {w} {h} {sw} {sh} {q}')

        # Process in Cloud
        if reply := self._cloud_wait_for_reply('/queue', [self._id, x, y, w, h, sw, sh, q]):

            # Something will change, so be ready
            self._hide_files()

            # Reply
            self._print(f'{reply}')
            key, val = reply.split('=', 1)

            # Already cached in the cloud
            if key == 'cached':
                return self._display_download(val)

            #
            # Lesgo!
            #

            self._cloud_job_id = val
            self._print(f'Job {self._cloud_job_id} added to queue.')

            # Ping until done
            while True:

                # A response!
                if reply := self._cloud_wait_for_reply('/status', [self._cloud_job_id]):
                    
                    self._print(f'{self._cloud_job_id}: {reply}')
                    key, val = reply.split('=', 1)

#                    match key:

                    # Queue
                    if key == 'queue':
                        if len(val) == 0:
                            self._print(f'{self._cloud_job_id}: not in queue, stopping.')
                            self._cloud_job_id = None
                            self._display_error('not in queue')
                            return False
                        else:
                            self._display_queue( int(val) )

                    # Loading
                    elif key == '?':
                        self._display_loading( int(val) )

                    # Progress
                    elif key == '%':
                        self._display_progress( float(val) )

                    # Download
                    elif key == 'ready':
                        return self._display_download(val)

                # Pause
                time.sleep(self._get_scattered_update_freq())

    #
    # UI
    #

    def _display_blank(self):

        # bg
        self._display.fill((255, 255, 255))

        # loading
        label = self._font_huge.render(self.idle_message(), True, (0, 0, 0))
        lw, lh = label.get_size()
        self._display.blit(label, (self._display_w/2-lw/2, self._display_h/2-lh/2))

        # show
        pygame.display.update()

    def _display_cache(self):
        # bg
        self._display.fill((255, 255, 0))

        # show
        pygame.display.update()

    def _display_download(self, file):

        # Save to
        out = Path(self._path) / file
        out = out.with_suffix(out.suffix + '.hidden')

        # bg
        self._display.fill((0, 255, 0))
        pygame.display.update()

        # Save from
        url = f'http://{self._cloud_host}:{self._cloud_port-1}/{file}'

        # Create dirs if needed
        out.parent.mkdir(parents=True, exist_ok=True)

        # Start download
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
                        self._print(f'{file}: {int(pos*100)}%')

                        # bg
                        self._display.fill((0, 255, 0))

                        # progress
                        if pos > 0:
                            pygame.draw.rect(
                                self._display,
                                (0, 0, 0),
                                pygame.Rect(int((1-pos) * pygame.display.Info().current_w), 0, pygame.display.Info().current_w, pygame.display.Info().current_h)
                            )

                        # %
                        label = self._font_big.render(f'{(pos*100):3.0f}%', True, (0, 0, 0))
                        lw, lh = label.get_size()
                        self._display.blit(label, (self._display_w/2-lw/2, self._display_h/2-lh/2))

                        # show
                        pygame.display.update()

        # Download done.
        self._print(f'Complete, renaming {out.as_posix()} -> {out.parent}/{type(self).__name__}{out.with_suffix("").suffix} ..')
        out.rename(out.parent / f'{type(self).__name__}{out.with_suffix("").suffix}')
        self._print(f'✓')

        # Download successful
        return True

    def _display_error(self, msg):

        # bg
        self._display.fill((255, 0, 0))

        # loading
        label = self._font_big.render(f'"{msg}"', True, (0, 0, 0))
        lw, lh = label.get_size()
        self._display.blit(label, (self._display_w/2-lw/2, self._display_h/2-lh/2))

        # show
        pygame.display.update()


    def _display_progress(self, percentage):

        self._print(f'{self._cloud_job_id}: {percentage*100:.1f}%')

        # bg
        self._display.fill((255, 0, 0))

        # progress
        if percentage > 0:
            pygame.draw.rect(
                self._display,
                (0, 255, 0),
                pygame.Rect(0, 0, int( pygame.display.Info().current_w*percentage ), pygame.display.Info().current_h)
            )

            # %
            label = self._font_big.render(f'{(percentage*100):3.0f}%', True, (0, 0, 0))
            lw, lh = label.get_size()
            self._display.blit(label, (self._display_w/2-lw/2, self._display_h/2-lh/2))
        
        # show
        pygame.display.update()


    def _display_queue(self, counter):

        # bg
        self._display.fill((0, 0, 255))

        # #
        label = self._font_big.render(f'{"|" * counter}', True, (255, 0, 0))
        lw, lh = label.get_size()
        self._display.blit(label, (self._display_w/2-lw/2, self._display_h/2-lh/2))

        # show
        pygame.display.update()

    def _display_loading(self, counter):

        # bg
        self._display.fill((255, 0, 0))

        # loading
        label = self._font_big.render(f'{" " * (counter%5)}|{" " * (4-counter%5)}', True, (0, 0, 0))
        lw, lh = label.get_size()
        self._display.blit(label, (self._display_w/2-lw/2, self._display_h/2-lh/2))
        
        # show
        pygame.display.update()

    #
    # Commands
    #

    def _cmd_print(self, unused_addr, *args):
        self._print(f'@: {unused_addr} {args}')

    def _cmd_connect(self, addr, host, port):
        if (
            not self._cloud
            or self._cloud_host != host
            or self._cloud_port != port
        ):
            # New cloud server
            self._print(f'@connect: {addr} {host} {port}')
            self._cloud_host = host
            self._cloud_port = port
            self._save_config(self._config, self._config_path)

            # Connect
            self._cloud = udp_client.SimpleUDPClient(self._cloud_host, self._cloud_port)
            self._print('Connecting to cloud at {}:{}'.format(self._cloud_host, self._cloud_port))

    def _cmd_delete(self, addr):
        self._print(f'@delete: {addr}')

        # Current video
        out = Path(self._path) / f'{type(self).__name__}.mp4'

        # Delete if exists
        if out.exists():
            self._print(f'Deleting {out.as_posix()} ..')
            out.unlink()

    def _cmd_diff(self, addr, diff):
        self._print(f'@diff: {addr} {diff}')
        self._player_send(f'%diff={diff}')

    def _cmd_pause(self, addr):
        self._print(f'@pause: {addr}')
        self._player_send(f'%pause')

    def _cmd_ping(self, addr, freq):
#        self._print(f'@ping: {addr} {freq}')
        time.sleep( (float(freq)/2. * 0.8) + ( (float(freq)/2. * 0.4) * random.random() ) )
        return f'/pong {self._id}'

    def _cmd_play(self, addr):
        self._print(f'@play: {addr}')
        self._player_send(f'%play')

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
        self._router.shutdown()
        os.kill(os.getpid(), signal.SIGINT)

    def _cmd_reboot(self, addr):
        self._print(f'@reboot: {addr}')

        # Task
        out = Path.cwd() / '.cloud.reboot'
        out.write_text(str(datetime.now()))

        # Quit
        self._cmd_quit(addr)

    def _cmd_setup(self, addr, speed, length):
        self._print(f'@setup: {addr} {speed} {length}')

        self._video_length = length
        self._video_speed = speed

        # Calculate diff
        self._player_diff = self._calc_diff()

        # Save
        self._save_config(self._config, self._config_path)

        # Update
        self._player_send(f'%diff={self._player_diff}')

    def _cmd_update(self, addr, x, y, w, h, sw, sh, q):
        self._print(f'@update: {addr} {x} {y} {w} {h} {sw} {sh} {q}')

        # Are there any changes?
        if (
            not self.count_files()
            or self._crop_w != w
            or self._crop_h != h
            or self._crop_x != x
            or self._crop_y != y
            or self._quality != q
            or self._screen_w != sw
            or self._screen_h != sh
        ):
            update = False

            #
            # Get it from cache or render new
            #

            # Use locally cached version if available
            if cached := self._is_cached(x, y, w, h, sw, sh, q):
                self._display_cache()
                self._hide_files(change_to=cached)
                update = True

            # If not in cache, then process if another job is not in progress
            elif not self._cloud_job_id:

                # Render successful!
                if self._render(x, y, w, h, sw, sh, q):
                    self._print(f'{self._cloud_job_id}: done.')
                    self._cloud_job_id = None
                    update = True

            # Job in progress
            else: self._print(f'{self._cloud_job_id}: already in progress.')

            #
            # Save new configuration
            #

            # Everything OK?
            if update:

                # Parameters
                self._crop_w = w
                self._crop_h = h
                self._crop_x = x
                self._crop_y = y
                self._quality = q
                self._screen_w = sw
                self._screen_h = sh

                # Calculate diff
                self._player_diff = self._calc_diff()

                # Save
                self._save_config(self._config, self._config_path)

                # Update
                self._player_send(f'%diff={self._player_diff}')

        # No changes to current configuration
        else: self._print('No changes to configuration, do nothing.')

    #
    # File reader
    #

    def search_paths(self):
        """Return a list of paths to search for files."""
        return [self._path]

    def is_changed(self):
        """Return true if the number of files in the paths have changed."""
        current_count = self.count_files()
        if current_count != self._filecount:
            self._filecount = current_count
            return True
        else:
            return False

    def idle_message(self):
        """Return a message to display when idle and no files are found."""
        return f'{self._id}'

    def count_files(self):
        return len( sorted(filter(lambda path: path.suffix.lower()[1:] in self._extensions, Path(self._path).glob('*'))) )

    #
    # Utils
    #

    def _hide_files(self, change_to=None):
        # Hide known files
        for ext in self._extensions:
            for f in Path(self._path).glob(f'**/*.{ext}'):
                try:
                    # Get details
                    query = ffmpeg.probe(f.as_posix())

                    # Rename based on metadata
                    if (
                        'format' in query
                        and 'tags' in query['format']
                        and 'render' in query['format']['tags']
                    ):
                        if m := CloudGrid.REGEX_PARAMETERS.search(query['format']['tags']['render']):
                            filename = f"{m.group('q').upper()}_x{m.group('x')}_y{m.group('y')}_w{m.group('w')}_h{m.group('h')}_{m.group('sw')}x{m.group('sh')}{f.suffix}.hidden"
                            self._print(f'Caching, renaming {f.as_posix()} -> {f.parent.as_posix()}/{filename} ..')
                            f.rename(f.parent / filename)

                    # Delete if required metadata is missing
                    else:
                        self._print(f'Required metadata (render) not found in {f.as_posix()}, deleting.')
                        f.unlink()
                
                # Problem with the file probably
                except ffmpeg._run.Error:
                    self._print(f'Unable to probe {f.as_posix()}, deleting.')
                    f.unlink()

        # If specified, change to
        if change_to:

            # Allow some time for changes to be discovered
            time.sleep(1)

            # Then rename
            out = Path(self._path) / change_to
            self._print(f'Renaming {out.as_posix()} -> {out.parent}/{type(self).__name__}{out.with_suffix("").suffix} ..')
            out.rename(out.parent / f'{type(self).__name__}{out.with_suffix("").suffix}')

    def _is_cached(self, x, y, w, h, sw, sh, q):
        # Hash of parameters to identify files
        hash = blake2s(f'{x} {y} {w} {h} {sw} {sh} {q}'.encode()).hexdigest()
        # Loop through to find a match
        for f in Path(self._path).iterdir():
            if (
                f.is_file()
                and (
                    f.suffix == '.hidden'
                    or
                    f.suffix[1:] in self._extensions
                )
            ):


                try:
                    # Get details
                    query = ffmpeg.probe(f.as_posix())

                    # Find hash
                    if (
                        'format' in query
                        and 'tags' in query['format']
                        and 'hash' in query['format']['tags']
                    ):

                        # Found it!
                        if hash == query['format']['tags']['hash']:
                            self._print(f'Found in cache, {hash} -> {f.as_posix()}')
                            return f.name

                    # Delete if required metadata is missing
                    else:
                        self._print(f'Required metadata (hash) not found in {f.as_posix()}, deleting.')
                        f.unlink()

                # Problem with the file probably
                except ffmpeg._run.Error:
                    self._print(f'Unable to probe {f.as_posix()}, deleting.')
                    f.unlink()

        # No match found
        self._print(f'Not found in cache, {hash}')
        return None

    def _print(self, message=None, end='\n'):
        if self._console_output:
            print(f'{chr(13) if not end else ""} [{datetime.now()}] {message: <50}', end='\n', flush=True) if message else print()

#
# Called from VideoLooper
#

def create_file_reader(config, screen):
    """Create new file reader based on reading a directory on disk."""
    return CloudGrid(config)
