import env
import time
import threading
import sys
import signal
import json
import logging
import os
import traceback
log = logging.getLogger("ServiceThread")
#log.setLevel(10)

from scanner import Scanner
from service import Service
from server import SocketServer
from server.jsonrpc import JsonRPC
from config import Config
from db import Database

class ServiceThread:

    """
    Manages calls between main thread and the service thread, public methods
    should always be called from the main thread, private methods should always
    be called from the child thread

    This is currently the only thread codeintel runs, but we might want a separate
    thread for scanning at some point.
    """

    def __init__(self, server):
        self.server = server
        self.__stopping = False
        self.__stopped = False
        self.__initializing = True
        self.socketServer = None

    # Public methods - MUST be called from main thread

    def cycle(self):
        if self.__initializing and self.socketServer and self.socketServer.server.server_address:
            self.host, self.port = self.socketServer.server.server_address
            self.__initializing = False
            self.server.onStarted()

        if self.__stopping:
            log.debug("Stop called from serviceThread cycle")
            self.socketServer.server.shutdown()
            self.server.stop()

        if not self.__initializing and not self.__stopped and \
           self.socketServer and self.socketServer.lastRequestTime:
            secondsAgo = int(time.time()) - self.socketServer.lastRequestTime
            if secondsAgo > Config.get("keepalive_timeout"):
                # We're going to stop the entire codeintel process as there
                # is no safe way of just killing a thread
                # The client will be responsible for restarting codeintel
                log.warn("keepalive timeout reached, force stopping codeintel process")
                
                try:
                    debug = []
                    for threadId, stack in list(sys._current_frames().items()):
                        debug.append("\n# ThreadID: %s" % threadId)
                        for filename, lineno, name, line in traceback.extract_stack(stack):
                            debug.append('File: "%s", line %d, in %s' % (filename, lineno, name))
                except:
                    log.warn("Failed grabbing backtraces")
                else:
                    log.warn("\n".join(debug))
                            
                self.server.forceStop()

    def start(self):
        log.debug("Starting serviceThread")

        self.thread = threading.Thread(target=self.__start, name="ServiceThread")
        self.thread.daemon = False
        self.thread.start()

    def stop(self):
        log.debug("Stopping serviceThread")
        # Todo: figure out how to boot any connected clients, cause if clients
        # are connected then this will just hang, yey Python! Good job!
        self.socketServer.server.shutdown()

    # Private methods - MUST be called from child thread

    def __start(self):
        Database.initialize(Config.get("database_conn"), Config.get("closure_ext_path"))

        # Load file scanner
        Scanner.startup()

        # Load service
        self.service = Service()
        JsonRPC.registerService(self.service)

        self.socketServer = SocketServer(
            host = Config.get("socket_host", "127.0.0.1"),
            port = Config.get("socket_port", 0),
            onDisconnect = self.__onSocketClientDisconnect)

        self.socketServer.serve_blocking()

        self.__onSocketServerShutdown() # will run when serve_blocking stops
        self.__stopped = True

    def __onSocketClientDisconnect(self):
        log.info("Client Disconnected")
        self.__stopping = True # Next cycle will pick this up

    def __onSocketServerShutdown(self):
        log.debug("onSocketServerShutdown")

        self.socketServer.stop()

        Scanner.shutdown()

        if (Database.conn.get_conn()):
            log.debug("Closing Database")
            Database.conn.close()
            log.debug("Database closed")
