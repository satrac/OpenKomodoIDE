import logging
import select
import os
import time

log = logging.getLogger("SocketServer")
log.setLevel(10)

import socketserver as BaseSocketServer

from .jsonrpc import JsonRPC

class SocketServer:
    
    """This is mainly a placeholder class for now"""
    
    server = None
    thread = None

    def __init__(self, host = "127.0.0.1", port = 0, onDisconnect = None):
        log.debug("Initializing SocketServer")
        
        self.configuredHost = host
        self.configuredPort = port
        self.onDisconnect = onDisconnect

        RequestHandler.socketServer = self
        self.server = BaseSocketServer.TCPServer((self.configuredHost, self.configuredPort), RequestHandler)

    def serve_blocking(self):
        self.server.serve_forever() # blocking

    def stop(self):
        log.debug("SocketServer: stop called")
        self.server.RequestHandlerClass.stop()
        self.server.server_close()
        #SocketServer.server.shutdown() - cannot be called from same thread, this gets called from server.py instead
        
    @property
    def lastRequestTime(self):
        return self.server.RequestHandlerClass.lastRequestTime

class RequestHandler(BaseSocketServer.BaseRequestHandler):
    
    shouldStop = False
    socketServer = None
    lastRequestTime = None
    
    def __init__(self, request, client_address, server):
        self.queue = ""
        self.processor = JsonRPC(self.process_response)
        
        self.request = request
        self.client_address = client_address
        self.server = server
        self.setup()
        
        try:
            self.handle()
        finally:
            self.finish()
        
    @staticmethod
    def stop():
        log.debug("RequestHandler: stop called")
        RequestHandler.shouldStop = True;
    
    def handle(self):
        log.debug("Starting handle loop")
        while True:
            if RequestHandler.shouldStop:
                return
            
            RequestHandler.lastRequestTime = int(time.time())
            
            r, w, e = select.select((self.request,), (), (), 0)
            data = self.request.recv(4096).strip()
            
            if len(data) == 0:
                if r:
                    log.debug("Client disconnected")

                    if (RequestHandler.socketServer.onDisconnect):
                        RequestHandler.socketServer.onDisconnect()

                    return
                else:
                    continue
            
            chunks = data.split("\x00")
            
            for index, chunk in enumerate(chunks):
                self.queue += chunk
                
                if index < len(chunks)-1 or data[-1:] == "\x00":
                    self.process_queue()
    
    def finish(self):
        log.debug("Finish called")
                    
    def process_queue(self):
        if len(self.queue) == 0:
            return
        
        #log.debug("Received: %s" % self.queue)
        self.processor.process(self.queue)
        self.queue = ""
        
    def process_response(self, message):
        #log.debug("Responding: %s" % message)
        
        self.request.sendall("%s\x00" % message)
