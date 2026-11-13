# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Threaded file scanner for CodeIntel.
The scanner's class methods should be used instead of directly instantiating and
interacting with a scanner class.
This file scanner cannot enqueue directories to scan. Instead, a separate
function should walk a directory and enqueue files to scan.
"""

import threading
import logging
import os
import cProfile

log = logging.getLogger("codeintel.scanner")
#log.setLevel(logging.DEBUG)

from queue import PriorityQueue

from language.common import Scope, AbstractScanner
from db.database import Database
from config import Config

class Scanner(threading.Thread):
    """Threaded file scanner."""

    name = "Scanner"

    # Scan in the background.
    PRIORITY_BACKGROUND = 0
    # Scan immediately.
    PRIORITY_IMMEDIATE = 1

    def __init__(self):
        """Initializes the threaded file scanner.
        This should not be manually invoked.
        """
        super(Scanner, self).__init__()
        self._queue = PriorityQueue()

    def run(self):
        """Starts the threaded file scanner."""
        while True:
            priority, data = self._queue.get()
            if not data:
                break
            if Config.get("profiling"):
                profiler = cProfile.Profile()
                profiler.enable()
            filename, scanner, callback = data
            if callback:
                callback(filename, False)
            try:
                log.debug("Scanning file '%s'", filename)
                Database.writeFileScope(filename, scanner.scan(filename))
            except Exception as e:
                log.exception(e)
                log.error("Previous exception happened while scanning file: %s", filename)
                Database.writeFileScope(filename, Scope()) # write anyway
            if callback:
                callback(filename, True)
            if Config.get("profiling"):
                try:
                    profiler.disable()
                    profiler.dump_stats(os.path.join(Config.get("profiling"), "codeintel.scan.%s.prof" % os.path.basename(filename)))
                except Exception as e:
                    log.exception(e)
            self._queue.task_done()
        self._queue.task_done()

    # Tracks the internal scanner instance.
    _instance = None

    @classmethod
    def startup(cls):
        """Starts the threaded file scanner."""
        if cls._instance:
            return
        log.info("Starting threaded file scanner.")
        cls._instance = cls()
        cls._instance.start()

    @classmethod
    def enqueue(cls, filename, scanner, callback=None, priority=PRIORITY_BACKGROUND):
        """Enqueues a file to be scanned.
        @param filename String full path of the file to scan.
        @param scanner AbstractScanner to scan the file with.
        @param callback Optional function to call when the scan completes.
        @param priority Optional scan priority. The default value is
                        PRIORITY_BACKGROUND.
        """
        if not isinstance(filename, str):
            raise TypeError("filename must be a string (got '%s')" % filename.__class__.__name__)
        if not isinstance(scanner, AbstractScanner):
            raise TypeError("scanner must be an AbstractScanner (got '%s')" % scanner.__class__.__name__)
        if callback and not callable(callback):
            raise TypeError("callback must be callable or None (got '%s')" % callback.__class__.__name__)
        if not isinstance(priority, int):
            raise TypeError("priority must be an integer (got '%s')" % priority.__class__.__name__)
        log.debug("Enqueueing file '%s'", filename)
        cls._instance._queue.put((priority, (filename, scanner, callback)))

    @classmethod
    def process(cls):
        """Blocks until the scan queue is empty."""
        log.debug("Waiting for file queue to empty...")
        cls._instance._queue.join()
        log.debug("File queue is empty.")

    @classmethod
    def shutdown(cls):
        """Stops the threaded file scanner and blocks until it truly halts."""
        log.info("Stopping threaded file scanner...")
        cls._instance._queue.put((Scanner.PRIORITY_IMMEDIATE, None))
        cls._instance._queue.join()
        cls._instance = None
        log.info("Threaded file scanner stopped.")
