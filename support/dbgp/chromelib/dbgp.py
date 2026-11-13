# Copyright (c) 2015-2016 ActiveState Software Inc.
"""
DBGP client for debugging JavaScript with a remote instance of Chrome.

This client is a stand-alone Python application that acts as a proxy between an
IDE and a remote Chrome instance, facilitating communication between the two
during a debug session.

The IDE first needs to start Chrome with remote debugging turned on (via the
'--remote-debugging-port' switch) and have Chrome open the filename or website
with the filename to start debugging with. Then the IDE should start this proxy
client with the appropriate filename, host, and port command line parameters
(see the end of this script for details).

The DBGP client establishes a connection with Chrome and sends an initialization
packet to the IDE, indicating it is ready to receive instructions. The client
then awaits said instructions from the IDE and, when one is received, translates
that instruction into Chrome's remote debugging protocol format prior to issuing
the command to Chrome. The client typically waits for a response from Chrome
before translating that response into DBGP's format and sending it to the IDE.

Note that upon connecting to Chrome, the debugger is already in the 'run' state,
so there is no way to "start debugging" in the normal sense. As a result,
breakpoints can only be triggered in code executed during JavaScript events,
rather than on "page startup". (Refreshing the page has no effect.)

While execution is paused, the DBGP client is continuously waiting on the IDE
for instructions. As instructions come in, the client process them, sends them
to Chrome, receives a response, processes the response, sends it back to the
IDE, and waits for the next instruction. This is all done in a synchronous
manner. The only time asynchronous communication can happen is when the debugger
in the 'run' state. At that time, the DBGP client polls both Chrome and the IDE
for instructions on an interval. Instructions are still processed synchronously.

For more information on the Chrome remote debugging protocol, see:
    https://developer.chrome.com/devtools/docs/protocol/1.1/index
For more information on the DBGP protocol, see:
    http://xdebug.org/docs-dbgp.php
    
This DBGP client was developed against v1.1 of the Chrome RDP and against v1.0
of the DBGP protocol.
"""

import argparse
import base64
import json
import logging
import os
import re
import shlex
import socket
import urllib
import urllib2
import webkit
import webkit.Console
import webkit.Debugger
import webkit.Page
import webkit.Runtime
import websocket

from Queue import Queue

logging.basicConfig()

class ChromeClient:
    """
    Client connected to a debug-able page in a remote instance of Chrome.
    Sends debugger commands (in the Chrome Remote Debugger Protocol format) and
    forwards responses (as Python objects).
    """
    log = logging.getLogger("chrome.dbgp.ChromeClient")
    log.setLevel(logging.INFO)
    
    def __init__(self, url, on_close=None, timeout=0.5):
        """
        Initializes a connection to the given URL (a debug-able page in a remote
        instance of Chrome) for debugging.
        @param url WebSocket URL provided by the remote Chrome instance.
        @param on_close Optional callback function for when the connection is
            closed and debugging stopped.
        @param timeout Optional socket timeout when receiving batches of
            messages from Chrome. When asked to, the client waits indefinitely
            for a message from Chrome. When that message is received, the client
            will wait for any other queued messages, but up until this timeout,
            before passing control back to the caller.
        """
        self.next_id = 0
        self.commands = {}
        self.listeners = {}
        self.onClose = on_close
        self.timeout = timeout
        
        self.log.debug("Connecting to %s", url)
        websocket.enableTrace(False)
        try:
            self.socket = websocket.create_connection(url)
            self.log.info("Connection to Chrome established.")
        except socket.error:
            self.log.exception("Unable to connect.")
            
    def isConnected(self):
        """
        Returns whether or not the connection to Chrome is still available.
        """
        return self.socket is not None
        
    def addListener(self, notification, callback):
        """
        Adds the given callback function for the given Chrome notification.
        When Chrome emits a notification, the callback function (if it exists)
        will be called.
        Note: only one listener can be added for a given notification.
        @param notification The Chrome notification to listen for. Notifications
            are defined in the 'webkit.*' modules.
        @param callback The callback function to call when the notification is
            received.
        """
        self.log.debug("Registered listener for '%s'", notification.name)
        notification.callback = callback
        self.listeners[notification.name] = notification
        
    def removeListener(self, notification):
        """
        Removes the callback function for the given Chrome notification.
        @param notification The Chrome notification listened for.
        """
        self.log.debug("Unregistered listener for '%s'", notification.name)
        del self.listeners[notification.name]
        
    def processMessages(self, timeout=False):
        """
        Waits for and processes messages coming from Chrome.
        @param timeout Flag indicating whether or not to timeout when attempting
            to read a message from Chrome. The default value is False, as most
            calls to this function are expecting a response, and should block
            until one is received. However, when the debugger is in the 'run'
            state, Chrome may never yield anything, and asynchronous commands
            (e.g. 'break' or 'status') from the IDE may need to be processed. In
            that case, this parameter should be set to True.
        @return True if this function processed messages, and False if it did
            not (due to a socket timeout).
        """
        self.log.debug("Waiting for messages from Chrome...")
        processed = False
        while self.socket is not None:
            try:
                if timeout:
                    self.socket.settimeout(self.timeout)
                message = self.socket.recv()
                if message is None:
                    # Finished. Chrome was probably closed.
                    self.socket.close()
                    self.socket = None
                    return False
                self.log.debug("Received message: '%s'", message)
                processed = True
                parsed = json.loads(message)
                if 'method' in parsed:
                    self.log.debug("Identified as method '%s'",
                                   parsed['method'])
                    if parsed['method'] in self.listeners:
                        listener = self.listeners[parsed['method']]
                        if 'params' in parsed:
                            data = listener.parser(parsed['params'])
                        else:
                            data = None
                        self.log.debug("Notifying listener with data '%s'",
                                       data)
                        listener.callback(data, listener)
                    else:
                        self.log.debug("No listener to notify.")
                elif parsed['id'] in self.commands:
                    self.log.debug("Identified as response for command %d",
                                   parsed['id'])
                    command = self.commands[parsed['id']]
                    if 'error' in parsed:
                        self.log.debug("Error message detected.")
                        message = parsed['error']['message']
                        # TODO: send error message
                    elif 'result' in parsed:
                        command.data = command.parser(parsed['result'])
                    else:
                        command.data = None
                        
                    if command.callback:
                        self.log.debug("Notifying listener with data '%s'",
                                       command.data)
                        command.callback(command)
                self.socket.settimeout(self.timeout) # do not block next read
            except socket.timeout:
                self.socket.settimeout(None) # block next read
                break
        return processed
        
    def send(self, command, callback=None):
        """
        Sends a Chrome remote debugging command to Chrome, calling the given
        callback function when a response is obtained.
        This function also waits for *a* response from Chrome (not necessarily
        *the* response for the command issued -- Chrome does not guarantee it
        will issue a response for a given command), since only the first batch
        of response messages is processed due to socket timeouts. The only way
        to definitively block (wait) for a response from Chrome is to trigger a
        state change in the 'callback' function and repeatedly call
        'processMessages()' while that state change has not happened. This
        appears to be exceedingly rare in practice.
        @param command The Chrome command to issue. Commands are defined in the
            'webkit.*' modules.
        @param callback Optional callback function to call when Chrome issues a
            response for the command.
        """
        command.id = self.next_id
        command.callback = callback
        self.commands[command.id] = command
        self.next_id += 1
        request = json.dumps(command.request)
        self.log.debug("Sending message: '%s'", request)
        self.socket.send(request)
        self.processMessages() # retrieve response
        
    def disconnect(self):
        """
        Disconnects from the debug-able page in the remote instance of Chrome.
        """
        self.socket.close()
        self.socket = None
        self.onClose()

class DBGPClient:
    """
    The proxy client that facilitates communication between the IDE and remote
    instance of Chrome for debugging.
    Both the IDE and Chrome client connect to this proxy.
    """
    # Status types.
    STATUS_STARTING = 0
    STATUS_STOPPING = 1
    STATUS_STOPPED = 2
    STATUS_RUNNING = 3
    STATUS_BREAK = 4
    STATUS_INTERACT = 5
    STATUS_NAMES = ['starting', 'stopping', 'stopped', 'running', 'break',
                    'interactive']
    # Reason types.
    REASON_OK = 0
    REASON_ERROR = 1
    REASON_ABORTED = 2
    REASON_EXCEPTION = 3
    REASON_NAMES = ['ok', 'error', 'aborted', 'exception']
    # Breakpoint types.
    BREAKPOINT_TYPE_LINE = 0
    BREAKPOINT_TYPE_CONDITIONAL = 1
    BREAKPOINT_TYPE_NAMES = ['line', 'conditional']
    # Breakpoint states.
    BREAKPOINT_STATE_DISABLED = 0
    BREAKPOINT_STATE_ENABLED = 1
    BREAKPOINT_STATE_NAMES = ['disabled', 'enabled']
    # Context types.
    CONTEXT_LOCALS = 0
    CONTEXT_GLOBALS = 1
    CONTEXT_NAMES = ['Locals', 'Globals']
    # Type map.
    TYPEMAP = {
        'boolean': 'bool',
        'function': 'function',
        'number': 'float',
        'object': 'object',
        'string': 'string',
        'undefined': 'undefined'
    }
    # Stdout/Stderr stream options.
    STREAM_DISABLE = 0
    STREAM_COPY = 1
    STREAM_REDIRECT = 2
    
    log = logging.getLogger("chrome.dbgp.DBGPClient")
    log.setLevel(logging.INFO)
    
    def __init__(self, filename_or_url, host='localhost', port=9000,
                       chrome_host='127.0.0.1', chrome_port=9222,
                       timeout=0.1):
        """
        Initializes the proxy client on the given host and port, connects to the
        remote Chrome instance with its given host and port, and starts
        debugging for the given filename or URL.
        @param filename_or_url The URI of the file or URL of the webpage to
            debug (file://full/path/to/file or http://hostname/).
            The file or webpage must already be open in Chrome, otherwise
            Chrome's remote debugging server will not list it as a debug-able
            page.
        @param host Optional host to run this proxy client on. This is the host
            the IDE will connect to. The default value is 'localhost'.
        @param port Optional port to run this proxy client on. This is the port
            the IDE will connect to. The default value is 9000.
        @param chrome_host Optional host that the remote Chrome instance is
            running on. The default value is '127.0.0.1' (the local machine).
        @param chrome_port Optional port that the remote Chrome instance is
            running on (via Chrome's '--remote-debugging-port' flag). The
            default value is 9222.
        @param timeout Optional socket timeout. This should only be set in
            testing. Normally when asked to retrieve messages from the IDE or
            Chrome, the proxy client waits indefinitely for a message. When that
            message is received, the client will wait for any other queued
            messages, but up until this timeout, before passing control back to
            the caller.
        """
        self.status = self.STATUS_STARTING
        self.reason = self.REASON_OK
        self.timeout = timeout
        
        # Connect to the IDE.
        self.log.debug("Connecting to IDE.")
        try:
            self.socket = socket.create_connection((host, port))
            self.log.info("Connection to IDE established.")
        except:
            self.log.exception("Failed to connect.")
            return
        self.commandQueue = []
        
        # Initialize read-only features.
        self.features = {
            # The following features MUST be available.
            'language_supports_threads': '0',
            'language_name': 'JavaScript',
            'language_version': 'V8', # cannot retrieve JS version from Chrome
            'encoding': 'UTF-8',
            'protocol_version': '1',
            'supports_async': '1',
            'data_encoding': 'base64',
            'breakpoint_languages': '0',
            'breakpoint_types': 'line',
            'multiple_sessions': '0', # read-only instead of read-write
            'extended_properties': '0', # read-only instead of read-write
            # The following features MAY be available.
            'supports_postmortem': '0',
            'show_hidden': '0',
        }
        # Initialize configurable options.
        self.options = {
            # The following options MUST be available.
            'max_children': '10',
            'max_data': '256',
            'max_depth': '1',
        }
        
        # Connect to Chrome and identify the WebSocket of the file or URL to be
        # debugged.
        url = "http://%s:%d/json" % (chrome_host, chrome_port)
        self.log.debug("Connecting to Chrome instance via '%s'", url)
        if __import__('sys').platform[0:3].lower() == 'win' and \
           filename_or_url.startswith('file://'):
            filename_or_url = filename_or_url.replace('\\', '/')
            if re.match(r'file://[a-zA-Z]:/', filename_or_url):
                # Ensure local file URI schemes have a leading slash.
                filename_or_url = filename_or_url.replace('file://', 'file:///')
        ws_url = None
        attempts = 0
        while not ws_url and attempts < 5:
            try:
                for page in json.loads(urllib2.urlopen(url).read()):
                    self.log.debug('Connected.')
                    if (filename_or_url.startswith('file://') and
                        page['url'].endswith(filename_or_url)) or \
                       (filename_or_url.startswith('http://') and
                        page['url'].startswith(filename_or_url)):
                        fileuri = page['url']
                        ws_url = page['webSocketDebuggerUrl']
                        self.log.debug("Found websocket for debugging: '%s'",
                                       ws_url)
            except:
                self.log.debug('Unable to connect. Retrying...')
                __import__('time').sleep(1)
            attempts += 1 # keep trying
        if not ws_url:
            self.log.error("File URI or URL '%s' unavailable for debugging.",
                           filename_or_url)
            # TODO: notify exit or more graceful exit?
            return
        self.log.debug("Opening websocket...")
        self.scripts = []
        self.breakpoints = {}
        self.nextBreakpointId = 0
        self.redirectStdout = False
        self.redirectStderr = False
        self.stack = None
        self.lastContinuationCommand = None
        self.chrome = ChromeClient(ws_url, on_close=self.onChromeDisconnect,
                                   timeout=timeout)
        if self.chrome.isConnected():
            self.onChromeConnect(fileuri)
    
    def processCommands(self):
        """
        Reads and processes commands from the IDE.
        Blocks until data is read unless the debugger is in the 'run' state.
        Commands are delimited by NUL bytes ('\0').
        The syntax of a command is:
            command [args] -- data
        For each command, the corresponding 'self.onX' method is called, where
        'X' is the command name converted from snake_case to CamelCase.
        """
        while self.socket:
            self.log.debug("Waiting for commands from IDE...")
            # Queue up previous commands first. This can happen, for example,
            # when a 'run' command is immediately proceeded by a 'status'
            # command. While both get queued up, the 'run' command does not
            # return immediately, but instead calls back into this function.
            data = '\0'.join(self.commandQueue)
            while True:
                try:
                    if self.status == self.STATUS_RUNNING:
                        # Looking for an asynchronous command.
                        self.socket.settimeout(self.timeout)
                    data += self.socket.recv(1024)
                    if not data:
                        self.log.info("IDE hung up.")
                        self.socket.close()
                        self.socket = None
                        self.status = self.STATUS_STOPPED
                        return
                    self.socket.settimeout(self.timeout) # don't block next read
                except socket.timeout:
                    self.socket.settimeout(None) # block next read
                    if self.status == self.STATUS_RUNNING and not data:
                        self.log.debug("No async command issued. Moving on.")
                        return # no asynchronous command issued
                    break # no more data to read; now process read data
                except socket.error:
                    self.log.exception("Unable to read commands from IDE.")
                    break
            
            self.log.debug("Received data from IDE: '%s'", data)
            self.commandQueue = data.split('\0')
            while len(self.commandQueue) > 0:
                command = self.commandQueue.pop(0)
                if not command: continue
                self.log.debug("Processing command '%s'", command)
                if command.find(' -- ') != -1:
                    args, data = command.split(' -- ')
                else:
                    args, data = command, ''
                argv = shlex.split(args)
                command, argv = argv[0], argv[1:]
                
                # If the debugger is in the 'run' state, only asynchronous
                # commands (e.g. 'break' or 'status') are available.
                if self.status == self.STATUS_RUNNING and \
                   command != 'break' and command != 'stop' and \
                   command != 'status':
                    self.log.debug("Command '%s' not available.", command)
                    e = CommandError(command, argv[argv.index('-i') + 1],
                                     CommandError.COMMAND_NOT_AVAILABLE,
                                     "Command '%s' not available" % command)
                    self.sendResponse(str(e))
                    return
                
                handler = 'on' + \
                          ''.join(part.capitalize() \
                                  for part in command.split('_'))
                if hasattr(self, handler):
                    try:
                        getattr(self, handler)(argv, data)
                    except argparse.ArgumentTypeError as e:
                        self.log.exception("Bad argument list for '%s'" %
                                           command)
                        e = CommandError(command, argv[argv.index('-i') + 1],
                                         CommandError.INVALID_ARGS, e.message)
                        self.sendResponse(str(e))
                    except CommandError as e:
                        self.sendResponse(str(e))
                    except Exception as e:
                        self.log.exception("Exception occurred in '%s'" %
                                           command)
                        e = CommandError(command, argv[argv.index('-i') + 1],
                                         CommandError.EXCEPTION, e.message)
                        self.sendResponse(str(e))
                else:
                    self.log.error("Unknown command '%s'", command)
                    e = CommandError(command, argv[argv.index('-i') + 1],
                                     CommandError.COMMAND_NOT_SUPPORTED,
                                     "Unknown command '%s'" % command)
                    self.sendResponse(str(e))
            
    def sendResponse(self, response):
        """
        Sends an XML response to the IDE.
        Note: response is assumed to be encoded in UTF-8 since the Chrome remote
        debugging protocol is assumed to be encoded in UTF-8.
        """
        header = u'<?xml version="1.0" encoding="UTF-8"?>\n'
        try:
            response = header + response
            self.log.debug("Sending response to IDE: '%s'", response)
            self.socket.send("%d\0%s\0" % (len(response), response))
        except socket.error:
            self.log.exception("Failed to send response to IDE.")
            
    # Chrome callbacks.
    
    def onChromeConnect(self, fileuri):
        """
        Callback for when the DBGPClient has connected to the remote Chrome
        instance. Make the connection to the IDE now and then listen for
        instructions.
        @param fileuri The file being debugged according to Chrome.
        """
        self.log.debug("Websocket connected.")
        
        # Register event listeners.
        chrome = self.chrome
        chrome.addListener(webkit.Console.messageAdded(), self.onMessageAdded)
        chrome.addListener(webkit.Debugger.scriptParsed(), self.onScriptParsed)
        chrome.addListener(webkit.Debugger.scriptFailedToParse(),
                           self.onScriptFailedToParse)
        chrome.addListener(webkit.Debugger.paused(), self.onPaused)
        chrome.addListener(webkit.Debugger.resumed(), self.onResumed)
        chrome.send(webkit.Console.enable())
        chrome.send(webkit.Debugger.enable())
        chrome.send(webkit.Debugger.setPauseOnExceptions('uncaught'))
        
        # Ready to debug. Send initialization packet to IDE.
        self.log.debug("Sending initialization packet...")
        attrs = {
            'xmlns': 'urn:debugger_protocol_v1',
            'appid': os.getpid(),
            'idekey': os.getenv('DBGP_IDEKEY',
                                os.getenv('USER', os.getenv('USERNAME', ''))),
            'session': os.getenv('DBGP_COOKIE', ''),
            'thread': __import__('thread').get_ident(),
            'parent': os.getenv('DEBUGGER_APPID', ''),
            'language': 'javascript',
            'protocol_version': '1.0',
            'fileuri': fileuri,
        }
        from xml.sax.saxutils import quoteattr as q
        xml = ' '.join(['%s=%s' % (k, q(str(v))) for k, v in attrs.items()])
        self.sendResponse('<init %s/>' % xml)
        
        # Process incoming commands.
        self.processCommands()
        
    def onChromeDisconnect(self):
        """
        Callback for when the DBGPClient has disconnected from the remote Chrome
        instance.
        """
        self.log.info("Disconnected from Chrome.")
        self.status = self.STATUS_STOPPED
        
    def onMessageAdded(self, data, notification):
        """
        Callback for when a message is printed to the Chrome console.
        If the appropriate stdout or stderr stream is redirected, forwards the
        message to the IDE.
        @param data Object containing the printed message.
        @param notification Unused.
        """
        if self.status != self.STATUS_RUNNING or \
           (data.level != 'error' and not self.redirectStdout) or \
           (data.level == 'error' and not self.redirectStderr):
            return
        
        template = '<stream xmlns="urn:debugger_protocol_v1" type="%s"' \
                   '>%s</stream>'
        
        filename = (data.url or '[?]').split('/')[-1]
        line = data.line or 0
        text = len(data.parameters) > 0 and \
               ' '.join([str(p) for p in data.parameters]) or data.text
        message = '%s:%d: %s\n' % (filename, line, text)
        if data.level == 'error' and data.stackTrace:
            # Append stack trace.
            trace = []
            for frame in data.stackTrace:
                filename = frame.url.split('/')[-1]
                line = frame.lineNumber
                function = frame.functionName
                trace.append('%s:%d: %s' % (filename, line, function))
            message += '\n%s\n' % '\n'.join(trace)
        self.sendResponse(template % (data.level != 'error' and 'stdout'
                                                            or 'stderr',
                                      base64.encodestring(message)))
        
    def onScriptParsed(self, data, notification):
        """
        Callback for when a Chrome successfully parses a script.
        At this time pending breakpoints can be added.
        Note: Debugging top-level Javascript looks to be impossible with Chrome
        remote debugging because the 'onScriptParsed' notifications come too
        late and setting breakpoints also happen too late (Chrome runs in its
        own thread and does not block for responses from the remote debugger
        client).
        @param data Object containing the script parsed.
        @param notification Unused.
        """
        filename = data['url']
        script_id = data['scriptId'].value
        start_line = data['startLine']
        end_line = data['endLine']
        if filename:
            self.log.debug("Script '%s' parsed with id of '%s'", filename,
                           script_id)
            self.scripts.append([filename, script_id, start_line, end_line])
            # Add pending breakpoints.
            # Chrome can only add breakpoints on scripts it has parsed. If a
            # breakpoint could not be added at the time the IDE requested, that
            # breakpoint needs to be added once its script is parsed. This is
            # our chance.
            # Note: allow file basename matching prior to adding breakpoints.
            # This is due to the fact that the path to a JS file on a website is
            # different than the filesystem path to that JS file. For example:
            # "/path/to/project/app/main.js" might be "http://foo/main.js". A
            # breakpoint put in "/path/to/project/app/main.js" should be put on
            # the website's "main.js" file.
            self.log.debug("Adding any pending breakpoints...")
            for breakpoint in self.breakpoints.values():
                line = breakpoint['lineno']
                if (breakpoint['filename'] == filename or
                    filename.endswith(os.path.basename(filename))) and \
                   line >= start_line and line <= end_line:
                    self.log.debug("Adding pending breakpoint on line %d", line)
                    location = webkit.Debugger.Location({'scriptId': script_id,
                                                         'lineNumber': line})
                    expression = breakpoint['expression']
                    self.chrome.send(webkit.Debugger.setBreakpoint(location,
                                                                   expression),
                                     lambda response : (
                                         breakpoint.update({
                                            'chrome_id': response.data['breakpointId'].value,
                                            'script_id': script_id
                                         }),
                                         self.log.debug("Breakpoint added.")
                                     ))
            self.log.debug("Finished adding pending breakpoints.")
        
    def onScriptFailedToParse(self, data, notification):
        """
        Callback for when a Chrome fails to parse a script.
        If the stderr stream is redirected, forwards the error message to the
        IDE.
        @param data Object containing the script that could not be parsed along
            with error information.
        @param notification Unused.
        """
        if self.redirectStderr:
            template = '<stream xmlns="urn:debugger_protocol_v1" ' \
                       'type="stderr">%s</stream>'
            
            message = '%s:%s: %s' % (data['url'], data['errorLine'],
                                     data['errorMessage'])
            self.sendResponse(template % base64.encodestring(message))
        
    def onPaused(self, data, notification):
        """
        Callback for when Chrome pauses at a breakpoint or exception.
        @param data Object containing the call stack.
        @param notification Unused
        """
        self.log.debug("Debugger paused.")
        self.status = self.STATUS_BREAK
        self.stack = data['callFrames']
        
        # Send IDE a response for the last continuation command ('run',
        # 'step_over', etc.).
        if self.lastContinuationCommand:
            template = '<response xmlns="urn:debugger_protocol_v1" ' \
                       'command="%s" status="%s" reason="%s" ' \
                       'transaction_id="%d"/>'
            self.sendResponse(template %
                              (self.lastContinuationCommand['name'],
                               self.STATUS_NAMES[self.status],
                               self.REASON_NAMES[self.REASON_OK],
                               self.lastContinuationCommand['transaction_id']))
            self.lastContinuationCommand = None
        else:
            self.log.warn("Debugger paused, but no corresponding continuation "
                          "command issued by IDE.")
    
    def onResumed(self, data, notification):
        """
        Callback for when Chrome resumes from a breakpoint.
        @param data Unused.
        @param notification Unused.
        """
        self.log.debug("Debugger resumed.")
        self.status = self.STATUS_RUNNING
        self.stack = None
        
    # DBGP callbacks.
    
    def onStatus(self, argv, data):
        """
        Responds to the IDE 'status' command.
        Provides a way for the IDE to find out whether execution may be
        continued or not.
        @param argv The argument string for 'status', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="status" status="%s" reason="%s" ' \
                   'transaction_id="%d"/>'
        parser = ArgumentParser(description='status')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        self.sendResponse(template % (self.STATUS_NAMES[self.status],
                                      self.REASON_NAMES[self.reason], args.i))
    
    def onFeatureGet(self, argv, data):
        """
        Responds to the IDE 'feature_get' command.
        Used to request feature support and discover values for various
        features, such as the language version or name.
        Note: 'supported' does not mean the feature is supported, just that the
        feature is recognized by 'feature_get'.
        @param argv The argument string for 'feature_get', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="feature_get" feature_name="%s" supported="%d" ' \
                   'transaction_id="%d">%s</response>'
        
        parser = ArgumentParser(description='feature_get')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-n', required=True, help='feature name')
        args = parser.parse_args(argv)
        if args.n in self.features:
            self.log.debug("'%s' is a read-only feature", args.n)
            self.sendResponse(template % (args.n, 1, args.i,
                                          self.features[args.n]))
        elif args.n in self.options:
            self.log.debug("'%s' is a configurable feature", args.n)
            self.sendResponse(template % (args.n, 1, args.i,
                                          self.options[args.n]))
        else:
            command = 'on' + ''.join(s.capitalize() for s in args.n.split('_'))
            if hasattr(self, command):
                self.log.debug("'%s' is a command", args.n)
                self.sendResponse(template % (args.n, 1, args.i, ''))
            else:
                self.log.debug("'%s' is unknown", args.n)
                self.sendResponse(template % (args.n, 0, args.i, ''))
        
    def onFeatureSet(self, argv, data):
        """
        Responds to the IDE 'feature_set' command.
        Allows the IDE to indicate what additional capabilities it has. The
        response issued to the IDE indicates whether or not that feature has
        been enabled.
        This can be called at any time during a debug session.
        @param argv The argument string for 'feature_set', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="feature_set" feature_name="%s" success="%d" ' \
                   'transaction_id="%d"/>'
        
        parser = ArgumentParser(description='feature_set')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-n', required=True, help='feature name')
        parser.add_argument('-v', required=True, help='value')
        args = parser.parse_args(argv)
        if args.n in self.options:
            self.log.debug("'%s' is configurable; setting to '%s'", args.n,
                           args.v)
            self.options[args.n] = args.v
            self.sendResponse(template % (args.n, 1, args.i))
        else:
            self.log.debug("'%s' is not configurable; ignoring", args.n)
            self.sendResponse(template % (args.n, 0, args.i))
        
    def _wait(self):
        """
        Wait for messages issued by Chrome, but on a timeout, as Chrome may not
        issue anything for an indefinite period of time. After each timeout,
        check for any asynchronous commands (e.g. 'break' or 'status') from the
        IDE and process them.
        """
        while self.status == self.STATUS_RUNNING:
            if not self.chrome.processMessages(True):
                self.processCommands()
        
    def onRun(self, argv, data):
        """
        Responds to the IDE 'run' command.
        Starts or resumes the script until a new breakpoint is reached, or the
        end of the script is reached.
        @param argv The argument string for 'run', according to the DBGP
            protocol.
        @param data Unused.
        """
        if not self.chrome.isConnected():
            raise CommandError('run', argv[argv.index('-i') + 1],
                               CommandError.COMMAND_NOT_AVAILABLE,
                               "Debugger disconnected.")
        
        parser = ArgumentParser(description='run')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        if self.status == self.STATUS_STARTING:
            # Technically, debugging has already started, but the IDE has
            # now consented to it.
            self.log.info("Debugging started.")
            self.lastContinuationCommand = {'name': 'run',
                                            'transaction_id': args.i}
            self.status = self.STATUS_RUNNING
        elif self.status == self.STATUS_BREAK:
            self.log.debug("Resuming from breakpoint.")
            self.lastContinuationCommand = {'name': 'run',
                                            'transaction_id': args.i}
            self.chrome.send(webkit.Debugger.resume(),
                             # Normally the 'onResumed' callback is called, but
                             # this is not always the case. Ensure it is.
                             lambda _ : self.onResumed(None, None))
        else:
            self.log.warn("Inconsistent state: %s",
                          self.STATUS_NAMES[self.status])
            template = '<response xmlns="urn:debugger_protocol_v1" ' \
                       'command="run" status="%s" reason="%s" ' \
                       'transaction_id="%d"/>'
            self.sendResponse(template % (self.STATUS_NAMES[self.status],
                                          self.REASON_NAMES[self.REASON_OK],
                                          args.i))
        self._wait()
        
    def onStepInto(self, argv, data):
        """
        Responds to the IDE 'step_into' command.
        Steps into the next statement. If there is a function call involved,
        breaks on the first statement of that function.
        @param argv The argument string for 'step_into', according to the DBGP
            protocol.
        @param data Unused.
        """
        parser = ArgumentParser(description='step_into')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        self.lastContinuationCommand = {'name': 'step_into',
                                        'transaction_id': args.i}
        self.chrome.send(webkit.Debugger.stepInto())
        self._wait()
        
    def onStepOver(self, argv, data):
        """
        Responds to the IDE 'step_over' command.
        Steps to the next statement. If there is a function call on the line
        from which 'step_over' is issued, breaks at the next statement after the
        function call in the same scope as from where that command was issued.
        @param argv The argument string for 'step_over', according to the DBGP
            protocol.
        @param data Unused.
        """
        parser = ArgumentParser(description='step_over')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        self.lastContinuationCommand = {'name': 'step_over',
                                        'transaction_id': args.i}
        self.chrome.send(webkit.Debugger.stepOver())
        self._wait()
        
    def onStepOut(self, argv, data):
        """
        Responds to the IDE 'step_out' command.
        Steps out of the current scope and breaks on the statement after
        returning from the current function.
        @param argv The argument string for 'step_out', according to the DBGP
            protocol.
        @param data Unused.
        """
        parser = ArgumentParser(description='step_out')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        self.lastContinuationCommand = {'name': 'step_out',
                                        'transaction_id': args.i}
        self.chrome.send(webkit.Debugger.stepOut())
        self._wait()
        
    def onStop(self, argv, data):
        """
        Responds to the IDE 'stop' command.
        Stops interaction with the Chrome debugger.
        TODO: Terminate Chrome?
        @param argv The argument string for 'stop', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="stop" status="%s" reason="%s" ' \
                   'transaction_id="%d"/>'
        
        parser = ArgumentParser(description='stop')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        self.sendResponse(template % (self.STATUS_NAMES[self.STATUS_STOPPED],
                                      self.REASON_NAMES[self.REASON_OK],
                                      args.i))
        self.chrome.disconnect()
        
    def onDetach(self, argv, data):
        """
        Responds to the IDE 'detach' command.
        Stops interaction with the Chrome debugger. This does not end execution
        of the script, but detaches from debugging.
        @param argv The argument string for 'detach', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="detach" status="%s" reason="%s" ' \
                   'transaction_id="%d"/>'
        
        parser = ArgumentParser(description='detach')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        self.sendResponse(template % (self.STATUS_NAMES[self.STATUS_STOPPED],
                                      self.REASON_NAMES[self.REASON_OK],
                                      args.i))
        self.chrome.disconnect()
        
    def onBreakpointSet(self, argv, data):
        """
        Responds to the IDE 'breakpoint_set' command.
        Sets a breakpoint, where the execution is paused, the IDE is notified,
        and further instructions from the IDE are processed.
        @param argv The argument string for 'breakpoint_set', according to the
            DBGP protocol.
        @param data Optional conditional expression for 'conditional' type
            breakpoints (encoded in base64).
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="breakpoint_set" transaction_id="%d" state="%s" ' \
                   'id="%s"/>'
        
        parser = ArgumentParser(description='breakpoint_set', add_help=False)
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-t', required=True,
                            choices=self.BREAKPOINT_TYPE_NAMES,
                            help='breakpoint type')
        parser.add_argument('-s', choices=self.BREAKPOINT_STATE_NAMES,
                            default=self.BREAKPOINT_STATE_NAMES[self.BREAKPOINT_STATE_ENABLED],
                            help='breakpoint state')
        parser.add_argument('-f', required=True, help='breakpoint filename')
        parser.add_argument('-n', required=True, type=int,
                            help='breakpoint line number')
        parser.add_argument('-m', help='breakpoint function name')
        parser.add_argument('-x', help='breakpoint exception name')
        parser.add_argument('-h', type=int, default=0,
                            help='breakpoint hit value')
        parser.add_argument('-o', choices=['>=', '==', '%'], default='>=',
                            help='hit condition string')
        parser.add_argument('-r', type=int, choices=[0, 1], default=0,
                            help='temporary breakpoint')
        args = parser.parse_args(argv)
        expression = base64.decodestring(data) if args.t == self.BREAKPOINT_TYPE_NAMES[self.BREAKPOINT_TYPE_CONDITIONAL] else None
        breakpoint = {
            'id': self.nextBreakpointId,
            'type': args.t,
            'state': args.s,
            'filename': args.f,
            'lineno': args.n - 1, # Chrome breakpoints have 0-based lines
            'temporary': args.r,
            'expression': expression,
            'chrome_id': None, # will be updated by Chrome
            'script_id': None # will be updated by Chrome
        }
        self.breakpoints[breakpoint['id']] = breakpoint
        self.nextBreakpointId += 1
        self.log.debug("Attempting to add breakpoint on '%s:%d'", args.f,
                       args.n)
        for filename, script_id, start_line, end_line in self.scripts:
            # Note: allow file basename matching prior to adding breakpoints.
            # This is due to the fact that the path to a JS file on a website is
            # different than the filesystem path to that JS file. For example:
            # "/path/to/project/app/main.js" might be "http://foo/main.js". A
            # breakpoint put in "/path/to/project/app/main.js" should be put on
            # the website's "main.js" file.
            if (filename == args.f or filename.endswith(os.path.basename(args.f))) and \
               breakpoint['lineno'] >= start_line and \
               breakpoint['lineno'] <= end_line:
                self.log.debug("File has scriptId of '%s'", script_id)
                location = webkit.Debugger.Location({'scriptId': script_id,
                                                     'lineNumber': args.n - 1})
                self.chrome.send(webkit.Debugger.setBreakpoint(location,
                                                               expression),
                                 lambda response : (
                                     breakpoint.update({
                                        'chrome_id': response.data['breakpointId'].value,
                                        'script_id': script_id
                                     }),
                                     self.log.debug("Breakpoint added.")
                                 ))
        self.sendResponse(template % (args.i, args.s, breakpoint['id']))
        if breakpoint['chrome_id'] is None:
            self.log.debug("File not loaded yet; "
                           "breakpoint will be added later.")
        
    def onBreakpointGet(self, argv, data):
        """
        Responds to the IDE 'breakpoint_get' command.
        Retrieves a particular breakpoint's information.
        @param argv The argument string for 'breakpoint_get', according to the
            DBGP protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="breakpoint_get" transaction_id="%d">' \
                   '<breakpoint id="%s" type="%s" state="%s" filename="%s" ' \
                   'lineno="%s" temporary="%s">%s</breakpoint></response>'
        expr_template = '<expression>%s</expression>'
        
        parser = ArgumentParser(description='breakpoint_get')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, required=True,
                            choices=self.breakpoints.keys(),
                            help='breakpoint id')
        args = parser.parse_args(argv)
        bp = self.breakpoints[args.d]
        self.log.debug("Retrieved breakpoint '%s'", args.d)
        expr = bp['expression']
        # Note: Chrome breakpoints have 0-based lines and DBGP breakpoints have
        # 1-based lines.
        self.sendResponse(template %
                          (args.i, args.d, bp['type'], bp['state'],
                           bp['filename'], bp['lineno'] + 1, bp['temporary'],
                           expr_template % base64.encodestring(expr) if expr else ''))
        
    def onBreakpointUpdate(self, argv, data):
        """
        Responds to the IDE 'breakpoint_update' command.
        Updates one or more attributes of a breakpoint that has already been set
        via the 'breakpoint_set' command.
        @param argv The argument string for 'breakpoint_update', according to
            the DBGP protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="breakpoint_update" transaction_id="%d"/>'
        
        parser = ArgumentParser(description='breakpoint_update', add_help=False)
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, required=True,
                            choices=self.breakpoints.keys(),
                            help='breakpoint id')
        parser.add_argument('-s', choices=self.BREAKPOINT_STATE_NAMES,
                            help='breakpoint state')
        parser.add_argument('-n', type=int, help='breakpoint line number')
        parser.add_argument('-h', type=int, help='hit value')
        parser.add_argument('-o', choices=['>=', '==', '%'],
                            help='hit condition')
        args = parser.parse_args(argv)
        bp = self.breakpoints[args.d]
        self.log.debug("Retrieved breakpoint '%s'; updating it.", args.d)
        if args.s:
            bp['state'] = args.s
        if args.n:
            bp['lineno'] = args.n - 1 # Chrome breakpoints are 0-based
            chrome_id, script_id = bp['chrome_id'], bp['script_id']
            if chrome_id and script_id:
                self.chrome.send(webkit.Debugger.removeBreakpoint(chrome_id))
                location = webkit.Debugger.Location({'scriptId': script_id,
                                                     'lineNumber': args.n - 1})
                self.chrome.send(webkit.Debugger.setBreakpoint(location,
                                                               bp['expression']),
                                 lambda response : (
                                     bp.update({
                                        'chrome_id': response.data['breakpointId'].value
                                     }),
                                     self.log.debug("Breakpoint updated.")
                                 ))
        self.sendResponse(template % args.i)
        
    def onBreakpointRemove(self, argv, data):
        """
        Responds to the IDE 'breakpoint_remove' command.
        Removes a breakpoint.
        @param argv The argument string for 'breakpoint_remove', according to
            the DBGP protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="breakpoint_remove" transaction_id="%d"/>'
        
        parser = ArgumentParser(description='breakpoint_remove')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, required=True,
                            choices=self.breakpoints.keys(),
                            help='breakpoint id')
        args = parser.parse_args(argv)
        chrome_id = self.breakpoints[args.d]['chrome_id']
        if chrome_id:
            self.chrome.send(webkit.Debugger.removeBreakpoint(chrome_id))
        del self.breakpoints[args.d]
        self.sendResponse(template % args.i)
        
    def onBreakpointList(self, argv, data):
        """
        Responds to the IDE 'breakpoint_list' command.
        Retrieves breakpoint information for all known breakpoints.
        @param argv The argument string for 'breakpoint_list', according to
            the DBGP protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="breakpoint_list" transaction_id="%d"' \
                   '>%s</response>'
        bp_template = '<breakpoint id="%s" type="%s" state="%s" ' \
                      'filename="%s" lineno="%s" temporary="%s"' \
                      '>%s</breakpoint>'
        expr_template = '<expression>%s</expression>'
        
        parser = ArgumentParser(description='breakpoint_list')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        breakpoints_xml = []
        for bp in self.breakpoints.values():
            expr = bp['expression']
            # Note: Chrome breakpoints have 0-based lines and DBGP breakpoints
            # have 1-based lines.
            breakpoints_xml.append(bp_template %
                                   (bp['id'], bp['type'], bp['state'],
                                    bp['filename'], bp['lineno'] + 1,
                                    bp['temporary'],
                                    expr_template % base64.encodestring(expr) if expr else ''))
        self.log.debug("Listing %d breakpoints.", len(breakpoints_xml))
        self.sendResponse(template % (args.i, ''.join(breakpoints_xml)))
        
    def onStackDepth(self, argv, data):
        """
        Responds to the IDE 'stack_depth' command.
        Returns the maximum stack depth that is available.
        @param argv The argument string for 'stack_depth', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="stack_depth" depth="%d" transaction_id="%d"/>'
                   
        parser = ArgumentParser(description='stack_depth')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        self.sendResponse(template % (len(self.stack), args.i))
        
    def onStackGet(self, argv, data):
        """
        Responds to the IDE 'stack_get' command.
        Returns stack information for a given stack depth or for the entire
        stack.
        @param argv The argument string for 'stack_get', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="stack_get" depth="%d" transaction_id="%s"' \
                   '>%s</response>'
        stack_template = '<stack level="%d" type="file" filename="%s" ' \
                         'lineno="%d" where="%s"/>'
                         
        parser = ArgumentParser(description='stack_get')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, choices=range(len(self.stack)),
                            help='stack depth')
        args = parser.parse_args(argv)
        stack = []
        for level in xrange(len(self.stack)):
            frame = self.stack[level]
            # Note: Chrome breakpoints have 0-based lines and DBGP breakpoints
            # have 1-based lines.
            lineno = frame.location.lineNumber + 1
            where = frame.functionName
            for filename, script_id, _, _ in self.scripts:
                if script_id == frame.location.scriptId.value:
                    stack.append(stack_template % (level, filename, lineno,
                                                   where))
            if len(stack) <= level:
                self.log.warn("Unknown scriptId '%s'",
                              frame.location.scriptId)
                stack.append(stack_template % (level, '', lineno, where))
        self.sendResponse(template % (len(self.stack), args.i,
                                      not args.d and ''.join(stack) or
                                                     stack[args.d]))
        
    def onContextNames(self, argv, data):
        """
        Responds to the IDE 'context_names' command.
        Returns a list of the names of currently available contexts variables
        can belong to (e.g. "Locals" and "Globals").
        @param argv The argument string for 'context_names', according to the
            DBGP protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="context_names" transaction_id="%s">%s</response>'
        context_template = '<context name="%s" id="%d"/>'
        
        parser = ArgumentParser(description='context_names')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, help='stack depth')
        args = parser.parse_args(argv)
        contexts = []
        for i in xrange(len(self.CONTEXT_NAMES)):
            contexts.append(context_template % (self.CONTEXT_NAMES[i], i))
        self.sendResponse(template % (args.i, ''.join(contexts)))
        
    def _getProperties(self, stack_depth, context_id):
        """
        Returns a list of Chrome properties at the given stack depth and in the
        given context.
        @param stack_depth Call stack depth to retrieve properties from. 0 is
            the current level.
        @param context_id Context to retrieve properties from. Currently can be
            either 'self.CONTEXT_LOCALS' or 'self.CONTEXT_GLOBALS'.
        """
        frame = self.stack[stack_depth]
        properties = []
        for scope in frame.scopeChain:
            # Get properties for 'local' or 'global' context in the current
            # stack frame, based on the requested context.
            if scope.type == self.CONTEXT_NAMES[context_id][:-1].lower():
                self.chrome.send(webkit.Runtime.getProperties(scope.object.objectId,
                                                              True),
                                 lambda response :
                                     properties.extend(response.data))
                break
        if context_id == self.CONTEXT_LOCALS:
            # Include 'this' in the local context.
            prop = webkit.Runtime.PropertyDescriptor({'name': 'this'})
            prop.value = frame.this
            properties.append(prop)
        self.log.debug("Fetched %d properties.", len(properties))
        return properties
        
    def _getPropertyXml(self, prop, depth=1, max_data=None, key=None, page=0):
        """
        Returns the XML property tag for the given Chrome property, suitable
        for sending to the IDE (after wrapping it in the proper response tag).
        @param prop The property returned by Chrome.
        @param depth The current depth when fetching child properties, or 'None'
            to not include child properties (useful for only listing context
            variables' names and values). The default value includes children.
            Be careful setting this externally, as it is used internally too.
        @param max_data Optional size to limit property value text to. The
            default value is 'self.options["max_data"]'.
        @param key Optional parent key to use for child properties.
        @param page The current page when fetching child properties. The default
            value is 0, the first page.
        """
        template = '<property %s>%s%s</property>'
        
        # Property attributes.
        attrs = {
            'name': prop.name,
            'fullname': getattr(prop, 'fullname', prop.name),
            'type': self.TYPEMAP[prop.value.type],
            'size': len(str(prop.value)),
            'children': prop.value.type == 'object' and \
                        int(self.options['max_children']) and '1' or '0',
        }
        if prop.value.className:
            attrs['classname'] = prop.value.className
        children = []
        if int(attrs['children']):
            # Fetch children.
            self.chrome.send(webkit.Runtime.getProperties(prop.value.objectId,
                                                          True),
                             lambda response :
                                 children.extend(response.data or []))
            children.sort(key=lambda prop: prop.name)
        if children:
            attrs['page'] = page
            attrs['pagesize'] = int(self.options['max_children'])
        if prop.value.subtype:
            attrs['facet'] = prop.value.subtype
        if children:
            attrs['numchildren'] = len(children)
            if depth and depth <= int(self.options['max_depth']):
                # Transform paged children to XML properties.
                max_children = int(self.options['max_children'])
                start, end = page * max_children, page * max_children + max_children
                children = children[start:end]
                for i in xrange(len(children)):
                    if re.match(r'^[a-zA-Z_]\w*$', children[i].name):
                        setattr(children[i], 'fullname',
                                "%s.%s" % (prop.name, children[i].name))
                    else:
                        setattr(children[i], 'fullname',
                                "%s[%s]" % (prop.name, children[i].name))
                        # TODO: what about object keys?
                    children[i] = self._getPropertyXml(children[i], depth + 1,
                                                       max_data,
                                                       prop.value.objectId)
            else:
                # Do not display children in XML, but keep the 'numchildren'
                # attribute.
                children = []
        if key:
            attrs['key'] = key
        from xml.sax.saxutils import quoteattr as q
        xml = ' '.join(['%s=%s' % (k, q(str(v))) for k, v in attrs.items()])
        # Property value data (trimmed, if necessary).
        value = str(prop.value)
        if (max_data or int(self.options['max_data'])) > 0:
            value = value[:(max_data or int(self.options['max_data']))]
        value = '<value encoding="base64">%s</value>' % \
                base64.encodestring(value)
        return template % (xml, value, ''.join(children))
        
    def onContextGet(self, argv, data):
        """
        Responds to the IDE 'context_get' command.
        Returns an array of properties in a given context at at given stack
        depth. The default context is 'self.CONTEXT_LOCALS' and the default
        stack depth is the current one. (Thus current local variables are
        fetched by default.)
        @param argv The argument string for 'context_get', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="context_get" transaction_id="%s">%s</response>'
        property_template = '<property %s>%s</property>'
        
        parser = ArgumentParser(description='context_get')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, default=0,
                            choices=range(len(self.stack)), help='stack depth')
        parser.add_argument('-c', type=int, default=0,
                            choices=range(len(self.CONTEXT_NAMES)),
                            help='context id')
        args = parser.parse_args(argv)
        properties = []
        for prop in self._getProperties(args.d, args.c):
            properties.append(self._getPropertyXml(prop, None)) # no children
            self.log.debug("Found property: '%s'", properties[-1])
        self.sendResponse(template % (args.i, ''.join(properties)))
        
    def onTypemapGet(self, argv, data):
        """
        Responds to the IDE 'typemap_get' command.
        Returns all supported data types. This allows the IDE to get information
        on how to map language-specific type names (as received in the property
        element returned by the 'context_get', and 'property_*' commands).
        @param argv The argument string for 'typemap_get', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="typemap_get" transaction_id="%d" ' \
                   'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ' \
                   'xmlns:xsd="http://www.w3.org/2001/XMLSchema" ' \
                   '>%s</response>'
        map_template = '<map type="%s" name="%s"/>'
        
        parser = ArgumentParser(description='typemap_get')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        types_xml = []
        for name, type in self.TYPEMAP.items():
            types_xml.append(map_template % (type, name))
        self.sendResponse(template % (args.i, ''.join(types_xml)))
        
    def _getProperty(self, name, stack_depth=None, context_id=None, key=None):
        """
        Fetches and returns a particular named Chrome property given either a
        stack depth and context or a Chrome objectId key.
        @param name The name of the Chrome property to fetch.
        @param stack_depth Optional stack depth the property exists at. If
            specified, 'context_id' must also be specified.
        @param context_id Optional context of the property. Currently can be
            either 'self.CONTEXT_LOCALS' or 'self.CONTEXT_GLOBALS'. If
            specified, 'stack_depth' must also be specified.
        @param key Optional parent key to use for child properties. If
            specified, 'stack_depth' and 'context_id' are ignored.
        """
        properties = []
        if not key:
            properties = self._getProperties(stack_depth, context_id)
        else:
            self.chrome.send(webkit.Runtime.getProperties(key, True),
                             lambda response : properties.extend(response.data))
        for prop in properties:
            if prop.name == name:
                self.log.debug("Found requested property '%s'", name)
                return prop
        self.log.debug("Unknown property '%s'", name)
        return None
        
    def onPropertyGet(self, argv, data):
        """
        Responds to the IDE 'property_get' command.
        Gets a property value.
        The maximum data returned is defined by 'self.options["max_data"]',
        which can be configured via 'feature_set'. If the size of the property's
        data is larger than that, the IDE should send 'property_value' to get
        the entire data. The IDE can determine if there is more data by
        inspecting the property's 'size' attribute.
        The depth of nested elements is defined by
        'self.options["max_children"]', which can also be configured via
        'feature_set'.
        @param argv The argument string for 'property_get', according to the
            DBGP protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="property_get" transaction_id="%d">%s</response>'
        
        parser = ArgumentParser(description='property_get')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, default=0,
                            choices=range(len(self.stack)), help='stack depth')
        parser.add_argument('-c', type=int, default=0,
                            choices=range(len(self.CONTEXT_NAMES)),
                            help='context id')
        parser.add_argument('-n', required=True, help='property long name')
        parser.add_argument('-m', type=int,
                            default=int(self.options['max_data']),
                            help='max data size to retrieve')
        parser.add_argument('-p', type=int, default=0, help='data page')
        parser.add_argument('-k', help='property key as retrieved in a '
                                       'property element')
        args = parser.parse_args(argv)
        if not args.k or not re.match(r'^\w+$', args.n):
            # Evaluate the property as an expression and consider the returned
            # value as the property. Global variables and closure variables in
            # particular are not accessible via 'self._getProperty()'.
            # Some IDEs may use 'property_get' to evaluate watch expressions
            # instead of using 'eval'.
            frame_id = self.stack[args.d].callFrameId
            prop = webkit.Runtime.PropertyDescriptor({'name': args.n})
            self.chrome.send(webkit.Debugger.evaluateOnCallFrame(frame_id, args.n),
                             lambda response :
                                 setattr(prop, 'value', response.data))
        else:
            # The property name is an identifier. Lookup the property.
            prop = self._getProperty(args.n, args.d, args.c, args.k)
        if prop and getattr(prop.value, 'className', None) != 'ReferenceError':
            prop = self._getPropertyXml(prop, page=args.p, max_data=args.m)
        else:
            raise CommandError('property_get', args.i,
                               CommandError.PROPERTY_DOES_NOT_EXIST,
                               "Unknown property '%s'" % args.n)
        self.sendResponse(template % (args.i, prop))
        
    def onPropertySet(self, argv, data):
        """
        Responds to the IDE 'property_set' command.
        Sets a property value.
        @param argv The argument string for 'property_set', according to the
            DBGP protocol.
        @param data Variable value to set (encoded in base64).
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="property_set" success="%d" transaction_id="%d"/>'
        
        parser = ArgumentParser(description='property_set')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, default=0, help='stack depth')
        parser.add_argument('-c', type=int, default=0, help='context id')
        parser.add_argument('-n', required=True, help='property long name')
        parser.add_argument('-t', help='data type')
        parser.add_argument('-k', help='property key as retrieved in a '
                                       'property element')
        parser.add_argument('-a', type=int, help='property address as '
                                                 'retrieved in a property '
                                                 'element')
        args = parser.parse_args(argv)
        frame_id = self.stack[args.d].callFrameId
        expr = "%s=%s" % (args.n, base64.decodestring(data))
        self.chrome.send(webkit.Debugger.evaluateOnCallFrame(frame_id, expr),
                         lambda response :
                             self.sendResponse(template %
                                               (response.data.subtype != 'error' and 1 or 0,
                                                args.i)))
        
    def onPropertyValue(self, argv, data):
        """
        Responds to the IDE 'property_value' command.
        Gets a property's full data value. This is called when the size of the
        property's data is larger than the maximum data returned (defined by
        'self.options["max_data"]').
        @param argv The argument string for 'property_value', according to the
            DBGP protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="property_value" size="%d" encoding="base64" ' \
                   'transaction_id="%d">%s</response>'
        
        parser = ArgumentParser(description='property_value')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-d', type=int, default=0, help='stack depth')
        parser.add_argument('-c', type=int, default=0, help='context id')
        parser.add_argument('-n', required=True, help='property long name')
        parser.add_argument('-m', type=int,
                            default=int(self.options['max_data']),
                            help='max data size to retrieve')
        parser.add_argument('-p', type=int, default=0, help='data page')
        parser.add_argument('-k', help='property key as retrieved in a '
                                       'property element')
        parser.add_argument('-a', type=int, help='property address as '
                                                 'retrieved in a property '
                                                 'element')
        args = parser.parse_args(argv)
        prop = self._getProperty(args.n, args.d, args.c, args.k)
        if not prop:
            raise CommandError('property_value', args.i,
                               CommandError.PROPERTY_DOES_NOT_EXIST,
                               "Unknown property '%s'" % args.n)
        self.sendResponse(template % (len(str(prop.value)), args.i,
                                      base64.encodestring(str(prop.value))))
        
    def onSource(self, argv, data):
        """
        Responds to the IDE 'source' command.
        Returns the data contents of the given URI or file for the current
        context.
        Note: Chrome only returns the source code for stand-alone chunks of code
        (based primarily on the current context) rather than entire files.
        @param argv The argument string for 'source', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="source" success="%d" transaction_id="%d" ' \
                   '>%s</response>'
        
        parser = ArgumentParser(description='source')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-b', type=int, help='begin line')
        parser.add_argument('-e', type=int, help='end line')
        parser.add_argument('-f', help='file URI')
        args = parser.parse_args(argv)
        script_id = None
        if not args.f:
            script_id = self.stack[0].location.scriptId.value
        else:
            for i in xrange(len(self.scripts), 0, -1):
                if self.scripts[0] == args.f:
                    script_id = self.scripts[1]
                    break
            if not script_id:
                raise CommandError('source', args.i, CommandError.FILE_ACCESS,
                                   "File '%s' not loaded." % args.f)
        # Note: only the source code for the current script is returned, not the
        # source for an entire file.
        self.chrome.send(webkit.Debugger.getScriptSource(script_id),
                         lambda response :
                             self.sendResponse(template %
                                               (1, args.i,
                                                base64.encodestring(response.data))))
        
    def onStdout(self, argv, data):
        """
        Responds to the IDE 'stdout' command.
        Enables or disables the sending of stdout output to the IDE.
        @param argv The argument string for 'stdout', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="stdout" success="%d" transaction_id="%d"/>'
                   
        parser = ArgumentParser(description='stdout')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-c', type=int, required=True,
                            choices=[self.STREAM_DISABLE, self.STREAM_COPY,
                                     self.STREAM_REDIRECT],
                            help='redirect request')
        args = parser.parse_args(argv)
        self.redirectStdout = args.c != self.STREAM_DISABLE
        self.sendResponse(template % (1, args.i))
        
    def onStderr(self, argv, data):
        """
        Responds to the IDE 'stderr' command.
        Enables or disables the sending of stderr output to the IDE.
        @param argv The argument string for 'stderr', according to the DBGP
            protocol.
        @param data Unused.
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="stderr" success="%d" transaction_id="%d"/>'
                   
        parser = ArgumentParser(description='stderr')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-c', type=int, required=True,
                            choices=[self.STREAM_DISABLE, self.STREAM_COPY,
                                     self.STREAM_REDIRECT],
                            help='redirect request')
        args = parser.parse_args(argv)
        self.redirectStderr = args.c != self.STREAM_DISABLE
        self.sendResponse(template % (1, args.i))
        
    def onBreak(self, argv, data):
        """
        Responds to the IDE 'break' command.
        Interrupts execution of the debugger while it is in the 'run' state.
        @param argv The argument string for 'break', according to the DBGP
            protocol.
        @param data Unused.
        """
        parser = ArgumentParser(description='break')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        self.lastContinuationCommand = {'name': 'break',
                                        'transaction_id': args.i}
        if self.status == self.STATUS_RUNNING:
            self.log.debug("Debugger pausing.")
            self.chrome.send(webkit.Debugger.pause())
        else:
            self.log.warn("Cannot break from state: '%s'",
                          self.STATUS_NAMES[self.status])
            raise CommandError('break', args.i,
                               CommandError.COMMAND_NOT_AVAILABLE,
                               "Cannot break from state '%s'" %
                               self.STATUS_NAMES[self.status])
        
    def onEval(self, argv, data):
        """
        Responds to the IDE 'eval' command.
        Evaluates a given string in the current execution context, potentially
        returning a resultant property.
        @param argv The argument string for 'eval', according to the DBGP
            protocol.
        @param data Expression to evaluate (encoded in base64).
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="eval" success="%d" transaction_id="%d"' \
                   '>%s</response>'
        
        parser = ArgumentParser(description='eval')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        args = parser.parse_args(argv)
        frame_id = self.stack[0].callFrameId
        expr = base64.decodestring(data)
        prop = webkit.Runtime.PropertyDescriptor({'name': '<eval>'})
        self.chrome.send(webkit.Debugger.evaluateOnCallFrame(frame_id, expr),
                         lambda response :
                             setattr(prop, 'value', response.data))
        if prop.value.subtype == 'error':
            raise CommandError('eval', args.i, CommandError.EVAL_FAILED,
                               str(prop.value).split('\n')[0])
        self.sendResponse(template % (1, args.i, self._getPropertyXml(prop)))
        
    def onInteract(self, argv, data):
        """
        Responds to the IDE 'interact' command.
        Runs the given string as code and any output returned is sent to the IDE
        (via stdout).
        At this time, code cannot be sent in piecemeal for running, as
        compilation takes place in Chrome without the ability to determine if
        syntax errors are recoverable (e.g. finishing a statement) or not.
        @param argv The argument string for 'interact', according to the DBGP
            protocol.
        @param data Expression to evaluate (encoded in base64).
        """
        template = '<response xmlns="urn:debugger_protocol_v1" ' \
                   'command="interact" transaction_id="%d" status="%s" ' \
                   'reason="%s" more="0" prompt="%s"/>'
        stream_template = '<stream xmlns="urn:debugger_protocol_v1" type="%s"' \
                          '>%s</stream>'
        
        parser = ArgumentParser(description='interact')
        parser.add_argument('-i', type=int, required=True,
                            help='transaction id')
        parser.add_argument('-m', type=int,
                            help='When 0, finish interactive mode')
        args = parser.parse_args(argv)
        if self.status != self.STATUS_BREAK and \
           self.status != self.STATUS_INTERACT:
            self.log.warn("Cannot interact with state: '%s'",
                          self.STATUS_NAMES[self.status])
            raise CommandError('interact', args.i,
                               CommandError.COMMAND_NOT_AVAILABLE,
                               "Cannot interact with state '%s'" %
                               self.STATUS_NAMES[self.status])
        if args.m == 0:
            self.log.debug("Stopping interactive session.")
            self.status = self.STATUS_BREAK
            self.sendResponse(template % (args.i,
                                          self.STATUS_NAMES[self.status],
                                          self.REASON_NAMES[self.REASON_OK],
                                          ''))
            return
        else:
            self.log.debug("%s interactive session.",
                           self.status == self.STATUS_BREAK and "Starting" or
                                                                "Continuing")
            self.status = self.STATUS_INTERACT
        if data:
            frame_id = self.stack[0].callFrameId
            expr = base64.decodestring(data)
            prop = webkit.Runtime.PropertyDescriptor({'name': '<eval>'})
            self.chrome.send(webkit.Debugger.evaluateOnCallFrame(frame_id, expr),
                             lambda response :
                                 setattr(prop, 'value', response.data))
            if prop.value.subtype == 'error':
                message = str(prop.value).split('\n')[0] + '\n'
                self.sendResponse(stream_template % ('stderr',
                                                     base64.encodestring(message)))
            elif prop.value != 'undefined':
                result = str(prop.value) + '\n'
                self.sendResponse(stream_template % ('stdout',
                                                     base64.encodestring(result)))
        self.sendResponse(template % (args.i, self.STATUS_NAMES[self.status],
                                      self.REASON_NAMES[self.REASON_OK], '>'))
        
class ArgumentParser(argparse.ArgumentParser):
    """
    Wrapper for 'argparse.ArgumentParser' that does not exit on error and raises
    a catch-able exception instead for sending back to the IDE.
    """
    def error(self, message):
        """
        Raise a catch-able exception to send back to the IDE.
        @param message The argument parsing error message.
        """
        raise argparse.ArgumentTypeError(message)
        
    def exit(self, status=0, message=None):
        """Prevent the default call to 'sys.exit()'."""
        pass
        
class CommandError(Exception):
    """
    An error raised when executing a command issued by the IDE.
    The string representation of this error is XML in the format required by the
    DBGP protocol and can be sent directly to the IDE as a command response.
    """
    template = '<response xmlns="urn:debugger_protocol_v1" ' \
               'command="%s" transaction_id="%s">' \
               '<error code="%d"><message><![CDATA[%s]]></message></error>' \
               '</response>'
    NONE                      = 0   # no error
    COMMAND_PARSE             = 1   # parse error
    DUPLICATE_ARGS            = 2   # duplicate arguments in command
    INVALID_ARGS              = 3   # invalid options
    COMMAND_NOT_SUPPORTED     = 4   # unimplemented command
    COMMAND_NOT_AVAILABLE     = 5   # command not available
    FILE_ACCESS               = 100 # can not open file
    STREAM_REDIRECT_FAILED    = 101 # stream redirect failed
    BREAKPOINT_INVALID        = 200 # breakpoint could not be set
    BREAKPOINT_TYPE           = 201 # breakpoint type not supported
    BREAKPOINT_INVALID_LINE   = 202 # invalid breakpoint
    BREAKPOINT_NOT_REACHABLE  = 203 # no code on breakpoint line
    BREAKPOINT_STATE          = 204 # invalid breakpoint state
    BREAKPOINT_DOES_NOT_EXIST = 205 # no such breakpoint
    EVAL_FAILED               = 206 # error evaluating code
    INVALID_EXPRESSION        = 207 # invalid expression
    PROPERTY_DOES_NOT_EXIST   = 300 # cannot get property
    STACK_DEPTH               = 301 # stack depth invalid
    CONTEXT_INVALID           = 302 # context invalid
    ENCODING                  = 900 # encoding not supported
    EXCEPTION                 = 998 # internal exception occurred
    UNKNOWN                   = 999 # unknown error
    
    def __init__(self, command, transaction_id, error_code, message):
        """
        Creates a new error object for a command issued by the IDE.
        @param command The string name of the command issued.
        @param transaction_id The transaction ID of the command issued.
        @param error_code The DBGP error code for the error that occurred.
        @param message The error text to send back to the IDE.
        """
        self.command = command
        self.transactionId = transaction_id
        self.errorCode = error_code
        self.message = message
        
    def __str__(self):
        """
        Returns the DBGP error in XML format suitable for sending to the IDE.
        """
        return self.template % (self.command, self.transactionId,
                                self.errorCode, self.message)

if __name__ == '__main__':
    log = logging.getLogger("chrome.dbgp.main")
    log.setLevel(logging.INFO)
    parser = argparse.ArgumentParser(description='dbgp')
    parser.add_argument('file_or_url', help='File or URL to debug')
    parser.add_argument('--host', default='localhost',
                        help='DBGPClient proxy host')
    parser.add_argument('--port', type=int, default=9000,
                        help='DBGPClient proxy host')
    parser.add_argument('--chrome-host', default='127.0.0.1',
                        help='Remote Chrome host')
    parser.add_argument('--chrome-port', type=int, default=9222,
                        help='Remote Chrome port')
    args = parser.parse_args(__import__('sys').argv[1:])
    log.info("Debugging '%s' over %s:%d on Chrome %s:%d", args.file_or_url,
             args.host, args.port, args.chrome_host, args.chrome_port)
    DBGPClient(args.file_or_url, args.host, args.port, args.chrome_host,
               args.chrome_port)
