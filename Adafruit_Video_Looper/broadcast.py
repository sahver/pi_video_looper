
#import time
#import math
import threading

from pythonosc import udp_client
from pythonosc.dispatcher import Dispatcher
from pythonosc import osc_server

class Router:

    def __init__(self, config):
        # Load config
        self.host = config.get('osc', 'host')
        self.port = config.getint('osc', 'port')
        # Listen and route /addresses
        self.dispatcher = Dispatcher()
        self.dispatcher.map('/ping', self._print_ping)

    def run(self):
        server = osc_server.ThreadingOSCUDPServer((self.host, self.port), self.dispatcher)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        print('Listen at {}:{}'.format(self.host, self.port))

    def map(self, addr, callback):
        self.dispatcher.map(addr, callback)
    
    #def start_client(ip, port):
    #    print('Starting Client')
    #    client = udp_client.SimpleUDPClient(ip, port)
    #    # print('Sending on {}'.format(client.))
    #    thread = threading.Thread(target=pong(client))
    #    thread.start()

    def _print_ping(self, unused_addr):
        print('(ping)')

