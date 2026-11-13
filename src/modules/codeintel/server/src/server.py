import env
import time
import threading
import sys
import signal
import json
import logging
import os
import platform
import traceback

from argparse import ArgumentParser
from config import Config
from servicethread import ServiceThread

log = logging.getLogger("Server")

threadAge = {}

class Main:
    
    def __init__(self):
        parser = ArgumentParser(description='CodeIntel Server')
        parser.add_argument('-c', "--config", type=str,
                            help='Specify the config file')
        
        parser.add_argument('-i', "--ip", type=str, 
                            help='Specify the host ip')
        
        parser.add_argument('-p', "--port", type=int,
                            help='Specify the port (random if none is specified)')
        
        parser.add_argument('-l', "--log", type=str,
                            help='Specify where to log files to')

        parser.add_argument('-t', "--truncate", type=str,
                            help="Truncate log file")

        parser.add_argument('-d', "--debug", type=str,
                            help='Enable debug logging, possible values: all, peewee, <logger name>')

        parser.add_argument('-P', "--profiler", type=str,
                            help='Enable profiling and store profiler output in the given directory')

        parser.add_argument('-f', "--file", type=str,
                            help='Specify the database file to use')

        # Use provided config file
        args = parser.parse_args()

        # --config
        if args.config:
            try:
                Config.use(args.config)
            except Exception as e:
                log.exception("Failed loading config")
                sys.exit(1)

        # --config
        if args.ip:
            Config.set("socket_host", args.ip)

        # --port
        if args.port:
            Config.set("socket_port", args.port)

        # --log
        if args.log:
            Config.set("logging_file", args.log)

        # --debug
        if args.debug:
            if args.debug == "all":
                Config.set("logging_level", logging.DEBUG)
            else:
                logger = logging.getLogger(args.debug)
                logger.setLevel(logging.DEBUG)
            
        # --profiler
        if args.profiler:
            Config.set("profiling", args.profiler)

        # --file
        if args.file:
            Config.set("database_conn", args.file)
        
        timestamp = int(time.time())
        logFormat = "<"+str(timestamp)+"> [%(asctime)s] [%(levelname)s]  %(name)s: %(message)s"
        if Config.get("logging_file", "stdout") == "stdout":
            logging.basicConfig(stream=sys.stdout, level=Config.get("logging_level", logging.INFO), format=logFormat)
        else:
            filemode = "a"
            if args.truncate:
                filemode = "w"
            logging.basicConfig(filename=Config.get("logging_file"), filemode=filemode, level=Config.get("logging_level", logging.INFO), format=logFormat)

        # We use serviceThread to launch and manage all the thread sensitive services
        self.serviceThread = ServiceThread(server=self)
        self.serviceThread.start()

        log.debug("PID: %d" % os.getpid())
        
        if platform.system() != "Windows":
            log.debug("PPID: %d" % os.getppid())
        
        self.__stopping = False
        self.__stopped = False

        def signal_handler(signal, frame):
            log.info("SIGTERM caught, exiting gracefully")
            self.stop()

        signal.signal(signal.SIGTERM, signal_handler)

        try:
            while self.__stopped is False:
                time.sleep(5)
                self.cycle()
        except KeyboardInterrupt:
            log.info('exiting due to keyboard interrupt')
            self.stop()
        except Exception:
            log.exception("exception occurred during serviceThread Cycle")
            self.stop()
        else:
            log.info("serviceThread Cycle Ended, exiting")
            self.stop()
            
    def cycle(self):
        maxAge = Config.get("thread_max_age")
        self.serviceThread.cycle()

        timestamp = int(time.time())
        threads = threading.enumerate()

        for thread in threads:
            
            name = thread.name or thread.ident
            if name == "MainThread" or name == "Scanner" or name == "ServiceThread":
                continue

            # New thread
            if not thread.ident in threadAge:
                threadAge[thread.ident] = timestamp
                
            # Aged thread that we already warned about
            elif threadAge[thread.ident] == None:
                continue
            
            # Aged thread that we need to warn about
            elif timestamp - threadAge[thread.ident] > maxAge:
                threadAge[thread.ident] = None
                
                stack = None
                for threadId, _stack in list(sys._current_frames().items()):
                    if threadId == thread.ident:
                        stack = _stack
                        break
                    
                debug = ["Thread '%s' still running after %d seconds" % (name, maxAge)]
                debug.append("# ThreadID: %s" % threadId)
                if stack:
                    for filename, lineno, name, line in traceback.extract_stack(stack):
                        debug.append('File: "%s", line %d, in %s' % (filename, lineno, name))
                log.warn("\n".join(debug))
            
    def onStarted(self):
         log.info("Started CodeIntel server on port: %s, pid: %d" % (self.serviceThread.port, os.getpid()))
         sys.stdout.write("\nport:%s\n" % self.serviceThread.port)
         sys.stdout.write("\npid:%s\n" % os.getpid())
         sys.stdout.flush()

    def onStop(self):
        log.info("onStop called")

    def stop(self):
        if self.__stopping:
            log.debug("Already stopping")
            return;

        log.debug("Stop called")
        self.__stopping = True
        self.serviceThread.stop()
        self.__stopped = True

        threadsClosed = False
        attempts = 10
        
        while attempts > 0:
            threads = threading.enumerate()
            if len(threads) == 1:
                threadsClosed = True
                break
            attempts = attempts - 1
            time.sleep(1)

        log.info("Exiting");
        if threadsClosed:
            sys.exit(0)
        else:
            log.warn("Hanging threads detected, forcefully exiting")
            self.forceStop()

    def forceStop(self):
        """
        This should only be used if something went wrong in the child thread
        that would prevent a clean shutdown
        """
        log.debug("forceStop called");
        log.info("Exiting Forcefully");
        os.kill(os.getpid(), 9)

if __name__ == "__main__":
    Main()
