#!/usr/bin/env python3
# Copyright (c) 2026 ActiveState Software Inc.
# See the file LICENSE.txt for licensing information.

"""Minimal DAP-to-DBGP bridge for Raku debugging.

Komodo's debugger speaks DBGP.  Raku's debugger ecosystem speaks DAP.  This
process connects to Komodo's DBGP listener and runs a Raku DAP adapter over
stdio, translating the supported debugger operations between the two
protocols.

The bridge intentionally implements the DBGP behavior Komodo uses rather than
the whole DBGP specification.
"""

import argparse
import base64
import glob
import json
import os
import queue
import shlex
import socket
import tempfile
import subprocess
import sys
import threading
import time
import traceback
import uuid
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape, quoteattr


DBGP_NS = "urn:debugger_protocol_v1"
_LOG_PATH = os.environ.get("RAKU_DAP_BRIDGE_LOG") or os.path.join(
    tempfile.gettempdir(), "komodo-raku-dap-bridge.log")
_LOG_LOCK = threading.Lock()


def _log(message):
    try:
        with _LOG_LOCK:
            with open(_LOG_PATH, "a", encoding="utf-8") as log_file:
                log_file.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                             message))
    except Exception:
        pass


def _attr(value):
    return quoteattr(str(value))


def _text(value):
    return escape(str(value))


def _b64(value):
    if isinstance(value, str):
        value = value.encode("utf-8", "replace")
    return base64.b64encode(value).decode("ascii")


def _unb64(value):
    try:
        return base64.b64decode(value.encode("ascii")).decode("utf-8", "replace")
    except Exception:
        return value


def _path_to_uri(path):
    if not path:
        return ""
    try:
        return "file://" + os.path.abspath(path).replace(os.sep, "/")
    except Exception:
        return path


def _uri_to_path(uri):
    if not uri:
        return uri
    if uri.startswith("file:"):
        parsed = urlparse(uri)
        path = unquote(parsed.path)
        if sys.platform.startswith("win") and path.startswith("/"):
            path = path[1:]
        return path
    return uri


def _option(args, name, default=None):
    try:
        index = args.index(name)
    except ValueError:
        return default
    if index + 1 < len(args):
        return args[index + 1]
    return default


def _flag(args, name):
    return name in args


def _find_executable(name):
    if not name:
        return None
    if os.path.isabs(name) or os.sep in name:
        return name if os.path.exists(name) else None
    paths = os.environ.get("PATH", "").split(os.pathsep)
    home = os.path.expanduser("~")
    for pattern in (
        os.path.join(home, ".rakubrew", "versions", "*", "install", "share",
                     "perl6", "site", "bin"),
        os.path.join(home, ".rakubrew", "versions", "*", "install", "bin"),
        os.path.join(home, ".perl6", "bin"),
        os.path.join(home, "rakudo", "bin"),
        "/opt/rakudo/bin",
    ):
        paths.extend(glob.glob(pattern))
    seen = set()
    for directory in paths:
        if not directory or directory in seen:
            continue
        seen.add(directory)
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class DAPClient:
    """Minimal DAP stdio client."""

    def __init__(self, argv, cwd, env):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._seq = 0
        self._lock = threading.Lock()
        self._pending = {}
        self.events = queue.Queue()
        self._stop = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._stderr_loop,
                                               daemon=True)
        self._stderr_reader.start()

    def _write(self, message):
        body = json.dumps(message).encode("utf-8")
        with self._lock:
            self.proc.stdin.write(
                b"Content-Length: " + str(len(body)).encode("ascii") +
                b"\r\n\r\n" + body)
            self.proc.stdin.flush()

    def _stderr_loop(self):
        try:
            for line in self.proc.stderr:
                sys.stderr.write("[raku-dap] " + line.decode("utf-8", "replace"))
                sys.stderr.flush()
        except Exception:
            pass

    def _read_loop(self):
        try:
            while not self._stop:
                headers = {}
                line = b""
                while True:
                    char = self.proc.stdout.read(1)
                    if not char:
                        return
                    line += char
                    if line.endswith(b"\r\n\r\n"):
                        break
                for header in line.decode("ascii", "replace").split("\r\n"):
                    if ":" in header:
                        key, value = header.split(":", 1)
                        headers[key.strip().lower()] = value.strip()
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                body = self.proc.stdout.read(length)
                if not body:
                    return
                message = json.loads(body.decode("utf-8"))
                if message.get("type") == "response":
                    request_seq = message.get("request_seq")
                    with self._lock:
                        pending = self._pending.get(request_seq)
                    if pending:
                        pending["response"] = message
                        pending["event"].set()
                elif message.get("type") == "event":
                    self.events.put(message)
                elif message.get("type") == "request":
                    # The Raku adapter currently does not need reverse
                    # requests.  Refuse them rather than deadlocking.
                    self._write({
                        "seq": 0,
                        "type": "response",
                        "request_seq": message.get("seq"),
                        "success": False,
                        "command": message.get("command"),
                        "message": "Unsupported reverse request",
                    })
        except Exception:
            traceback.print_exc()
        finally:
            with self._lock:
                pending_items = list(self._pending.values())
            for pending in pending_items:
                pending["event"].set()

    def request(self, command, arguments=None, wait=True, timeout=30):
        with self._lock:
            self._seq += 1
            seq = self._seq
        message = {"seq": seq, "type": "request", "command": command}
        if arguments is not None:
            message["arguments"] = arguments
        pending = None
        if wait:
            pending = {"event": threading.Event(), "response": None}
            with self._lock:
                self._pending[seq] = pending
        self._write(message)
        if not wait:
            return seq
        if not pending["event"].wait(timeout):
            with self._lock:
                self._pending.pop(seq, None)
            raise TimeoutError("DAP request timed out: %s" % command)
        with self._lock:
            self._pending.pop(seq, None)
        response = pending["response"]
        if not response or not response.get("success", False):
            _log("DAP request failed: %s %r" % (command, response))
            message = ""
            if response:
                message = response.get("message") or ""
                error = (response.get("body") or {}).get("error") or {}
                message = message or error.get("format") or ""
            raise RuntimeError(message or ("DAP request failed: %s" % command))
        return response

    def get_event(self, timeout=0.25):
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self._stop = True
        try:
            self.proc.terminate()
        except Exception:
            pass


def parse_exception_filters(value):
    """Turn the Komodo preference into DAP exception filter names.

    "none" is sent as an unknown filter so the adapter installs no exception
    breakpoints (an empty filter list would fall back to "uncaught").
    """
    value = (value or "uncaught").strip().lower()
    if value in ("", "none", "off", "no"):
        return ["none"]
    return [part.strip() for part in value.split(",") if part.strip()]


class RakuDAPBridge:
    def __init__(self, options):
        self.options = options
        self.program = os.path.abspath(options.program)
        self.cwd = os.path.abspath(options.cwd or os.path.dirname(self.program))
        self.raku = options.raku or "raku"
        self.stop_on_entry = options.stop_on_entry
        self.idekey = options.idekey or "komodo"
        self.exception_filters = parse_exception_filters(
            getattr(options, "exception_filters", "uncaught"))
        self.appid = str(uuid.uuid4())
        self.dbgp = None
        self._dbgp_buffer = b""
        self.dap = None
        self._dap_ready = threading.Event()
        self._adapter_initialized = threading.Event()
        self._launched = False
        self._stopped = False
        self._terminated = False
        self._shutdown = False
        self._pending_tid = None
        self._pending_command = None
        self._break_tid = None
        self._current_thread = 1
        self._frames = []
        self._scopes = []
        self._scope_by_context = {}
        self._varref_by_fullname = {}
        self._value_by_fullname = {}
        self._property_xml_by_fullname = {}
        self._next_breakpoint_id = 1
        self._breakpoints = {}
        self._breakpoints_by_path = {}
        self._lock = threading.RLock()

    # ---- DBGP ---------------------------------------------------------

    def connect_dbgp(self):
        self.dbgp = socket.create_connection(
            (self.options.host, self.options.port), timeout=30)
        self.dbgp.settimeout(None)
        attrs = {
            "xmlns": DBGP_NS,
            "appid": self.appid,
            "idekey": self.idekey,
            "thread": "Raku 1",
            "parent": "",
            "session": os.environ.get("DBGP_COOKIE", ""),
            "language": "Raku",
            "protocol_version": "1.0",
            "fileuri": _path_to_uri(self.program),
        }
        self._dbgp_send("<init %s/>" % " ".join(
            "%s=%s" % (key, _attr(value)) for key, value in attrs.items()))

    def _dbgp_send(self, xml):
        body = ('<?xml version="1.0" encoding="utf-8"?>\n' + xml).encode("utf-8")
        packet = str(len(body)).encode("ascii") + b"\0" + body + b"\0"
        with self._lock:
            if self.dbgp:
                self.dbgp.sendall(packet)

    def _dbgp_read_command(self):
        while True:
            end = self._dbgp_buffer.find(b"\0")
            if end >= 0:
                raw = self._dbgp_buffer[:end]
                self._dbgp_buffer = self._dbgp_buffer[end + 1:]
                return raw.decode("utf-8", "replace")
            chunk = self.dbgp.recv(4096)
            if not chunk:
                return None
            self._dbgp_buffer += chunk

    def _response(self, command, tid, attrs="", body=""):
        self._dbgp_send(
            '<response xmlns="%s" command=%s transaction_id=%s%s>%s</response>'
            % (DBGP_NS, _attr(command), _attr(tid if tid is not None else -1),
               attrs, body))

    def _error(self, command, tid, code, message):
        self._response(command, tid, "",
                       "<error code=%s><message>%s</message></error>"
                       % (_attr(code), _text(message)))

    # ---- DAP setup ----------------------------------------------------

    def _start_dap(self):
        adapter = self.options.adapter or os.environ.get(
            "RAKU_DAP_COMMAND", "nimala-raku-dap")
        adapter_parts = shlex.split(adapter)
        if self.options.adapter_args:
            adapter_parts += shlex.split(self.options.adapter_args)
        exe = _find_executable(adapter_parts[0])
        if exe:
            adapter_parts[0] = exe
        env = dict(os.environ)
        if exe:
            env["PATH"] = os.path.dirname(exe) + os.pathsep + env.get("PATH", "")
        if self.options.raku and os.path.dirname(self.raku):
            env["PATH"] = os.path.dirname(self.raku) + os.pathsep + env.get("PATH", "")
        _log("starting DAP adapter: %r cwd=%r PATH=%r" %
             (adapter_parts, self.cwd, env.get("PATH", "")))
        self.dap = DAPClient(adapter_parts, self.cwd, env)
        self.dap.request("initialize", {
            "clientID": "komodo",
            "adapterID": "raku",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "pathFormat": "path",
        })
        self._dap_ready.set()

    def _dap_event_loop(self):
        while not self._shutdown and self.dap is None:
            time.sleep(0.05)
        while not self._shutdown and self.dap is not None:
            event = self.dap.get_event()
            if not event:
                continue
            name = event.get("event")
            body = event.get("body") or {}
            if name == "initialized":
                self._adapter_initialized.set()
                self._send_all_breakpoints()
                try:
                    self.dap.request("setExceptionBreakpoints",
                                     {"filters": self.exception_filters},
                                     wait=False)
                    self.dap.request("configurationDone", {}, wait=False)
                except Exception:
                    traceback.print_exc()
            elif name == "stopped":
                with self._lock:
                    self._stopped = True
                    self._current_thread = body.get("threadId", 1)
                    self._frames = []
                    self._scopes = []
                    self._scope_by_context = {}
                    self._varref_by_fullname = {}
                    self._value_by_fullname = {}
                    self._property_xml_by_fullname = {}
                    tid = self._pending_tid
                    command = self._pending_command
                    self._pending_tid = None
                    self._pending_command = None
                    break_tid = self._break_tid
                    self._break_tid = None
                if tid is not None:
                    self._response(command or "run", tid,
                                   ' status="break" reason="ok"')
                elif break_tid is not None:
                    self._response("break", break_tid,
                                   ' status="break" reason="ok"')
            elif name in ("terminated", "exited"):
                with self._lock:
                    self._terminated = True
                    tid = self._pending_tid
                    command = self._pending_command
                    self._pending_tid = None
                    self._pending_command = None
                    self._break_tid = None
                if tid is not None:
                    self._response(command or "run", tid,
                                   ' status="stopped" reason="ok"')
            elif name == "output":
                category = body.get("category", "stdout")
                if category not in ("stdout", "stderr"):
                    category = "stdout"
                self._dbgp_send(
                    '<stream type="%s" encoding="base64">%s</stream>'
                    % (category, _b64(body.get("output", ""))))

    # ---- breakpoints --------------------------------------------------

    def _send_breakpoints(self, path):
        lines = sorted(set(self._breakpoints_by_path.get(path, [])))
        if not self.dap or not self._dap_ready.is_set():
            return
        try:
            self.dap.request("setBreakpoints", {
                "source": {"path": path},
                "breakpoints": [{"line": line} for line in lines],
            })
        except Exception:
            traceback.print_exc()

    def _send_all_breakpoints(self):
        for path in list(self._breakpoints_by_path):
            self._send_breakpoints(path)

    # ---- DAP launch/execution ----------------------------------------

    def _launch(self, action):
        if not self._dap_ready.wait(30):
            self._send_launch_failure()
            return
        try:
            stop_on_entry = self.stop_on_entry or action == "step_into"
            arguments = {
                "type": "raku",
                "request": "launch",
                "name": "Raku",
                "program": self.program,
                "cwd": self.cwd,
                "args": list(self.options.arg or []),
                "executable": self.raku,
                "stopOnEntry": bool(stop_on_entry),
                "console": "internalConsole",
            }
            if self.options.include_path:
                arguments["includePaths"] = list(self.options.include_path)
            _log("DAP launch: %r" % arguments)
            self.dap.request("launch", arguments, wait=False)
            self._launched = True
        except Exception:
            _log("launch failed: %s" % traceback.format_exc())
            traceback.print_exc()
            self._send_launch_failure()

    def _send_launch_failure(self):
        with self._lock:
            tid = self._pending_tid
            command = self._pending_command
            self._pending_tid = None
            self._pending_command = None
        if tid is not None:
            self._error(command or "run", tid, 998,
                        "Unable to start the Raku DAP adapter")

    def _continue(self, action):
        if not self._launched:
            self._launch(action)
            return
        commands = {
            "run": "continue",
            "step_into": "stepIn",
            "step_over": "next",
            "step_out": "stepOut",
            "interact": "continue",
        }
        command = commands.get(action)
        if not command:
            return
        try:
            with self._lock:
                self._stopped = False
                self._frames = []
                self._scopes = []
                self._scope_by_context = {}
                self._varref_by_fullname = {}
                self._value_by_fullname = {}
                self._property_xml_by_fullname = {}
            self.dap.request(command, {"threadId": self._current_thread},
                             wait=False)
        except Exception:
            traceback.print_exc()

    def _handle_restart(self, tid):
        if not self._launched:
            self._error("restart", tid, 999,
                        "No debug session to restart")
            return
        try:
            self.dap.request("restart", {}, wait=False)
            with self._lock:
                self._stopped = False
                self._terminated = False
                self._frames = []
                self._scopes = []
                self._scope_by_context = {}
                self._varref_by_fullname = {}
                self._value_by_fullname = {}
                self._property_xml_by_fullname = {}
                self._break_tid = None
            self._response("restart", tid, ' status="running" reason="ok"')
        except Exception:
            _log("restart failed: %s" % traceback.format_exc())
            self._error("restart", tid, 998,
                        "Unable to restart the Raku DAP session")

    def _handle_resume(self, argv, tid):
        action = argv[0]
        if action == "break":
            try:
                with self._lock:
                    self._break_tid = tid
                self.dap.request("pause", {"threadId": self._current_thread},
                                 wait=False)
            except Exception as ex:
                with self._lock:
                    self._break_tid = None
                self._error("break", tid, 998, "Unable to pause: %s" % ex)
            return
        with self._lock:
            self._pending_tid = tid
            self._pending_command = action
        threading.Thread(target=self._continue, args=(action,),
                         daemon=True).start()

    # ---- stack and variables -----------------------------------------

    def _stack_frames(self, force=False):
        if self._frames and not force:
            return self._frames
        response = self.dap.request("stackTrace", {
            "threadId": self._current_thread,
            "startFrame": 0,
            "levels": 0,
        })
        raw = response.get("body", {}).get("stackFrames", [])
        frames = []
        for frame in raw:
            source = frame.get("source") or {}
            if source.get("origin") == "internal":
                continue
            if frame.get("presentationHint") == "deemphasize":
                continue
            frames.append(frame)
        if not frames:
            frames = raw[:1]
        self._frames = frames
        return frames

    def _current_dap_frame(self):
        frames = self._stack_frames()
        return frames[0].get("id") if frames else None

    def _scopes_for_current_frame(self, force=False):
        if self._scopes and not force:
            return self._scopes
        frame_id = self._current_dap_frame()
        if not frame_id:
            return []
        response = self.dap.request("scopes", {"frameId": frame_id})
        self._scopes = response.get("body", {}).get("scopes", [])
        self._scope_by_context = {
            index: scope for index, scope in enumerate(self._scopes)
        }
        return self._scopes

    def _property_xml(self, variable, fullname, include_children=False):
        name = variable.get("name", "")
        value = variable.get("value", "")
        var_ref = variable.get("variablesReference", 0)
        num_children = (variable.get("namedVariables", 0) or 0) + \
                       (variable.get("indexedVariables", 0) or 0)
        attrs = {
            "name": name,
            "fullname": fullname,
            "type": variable.get("type") or "string",
            "classname": variable.get("type") or "string",
            "children": "1" if var_ref else "0",
            "numchildren": str(num_children),
            "size": str(len(value)),
            "encoding": "base64",
        }
        if var_ref:
            self._varref_by_fullname[fullname] = var_ref
        self._value_by_fullname[fullname] = value
        child_xml = ""
        if include_children and var_ref:
            child_xml = self._children_xml(var_ref, fullname)
            if child_xml:
                attrs["children"] = "1"
                attrs["numchildren"] = str(
                    child_xml.count("<property "))
        xml = "<property %s>%s%s</property>" % (
            " ".join("%s=%s" % (key, _attr(val)) for key, val in attrs.items()),
            _b64(value), child_xml)
        self._property_xml_by_fullname[fullname] = xml
        return xml

    def _children_xml(self, variable_reference, parent_fullname):
        try:
            variables = self._variables_for_reference(variable_reference)
        except Exception:
            return ""
        return "".join(
            self._property_xml(
                variable,
                "%s.%s" % (parent_fullname, variable.get("name", "")))
            for variable in variables)

    def _variables_for_reference(self, variable_reference):
        response = self.dap.request("variables", {
            "variablesReference": variable_reference,
        })
        return response.get("body", {}).get("variables", [])

    def _context_properties(self, context_id):
        scopes = self._scopes_for_current_frame()
        scope = self._scope_by_context.get(context_id)
        if not scope:
            return ""
        variables = self._variables_for_reference(
            scope.get("variablesReference", 0))
        return "".join(self._property_xml(variable, variable.get("name", ""))
                       for variable in variables)

    # ---- DBGP command handlers ---------------------------------------

    def _handle(self, raw, argv, data):
        command = argv[0] if argv else ""
        tid = _option(argv, "-i")
        _log("DBGP command: %s" % raw)
        try:
            if command == "feature_get":
                name = _option(argv, "-n")
                values = {
                    "supports_async": "1",
                    "language_name": "Raku",
                    "language_version": "6.d",
                    "max_children": "100",
                    "max_data": "10240",
                    "max_depth": "1",
                    "show_hidden": "1",
                    "supports_postmortem": "0",
                    "break": "1",
                    "eval": "1",
                    "detach": "1",
                    "breakpoint_languages": "Raku",
                }
                if name in values:
                    self._response(command, tid, ' feature_name=%s supported="1"'
                                   % _attr(name), _text(values[name]))
                else:
                    self._response(command, tid, ' feature_name=%s supported="0"'
                                   % _attr(name))
            elif command == "feature_set":
                self._response(command, tid, ' feature_name=%s success="1"'
                               % _attr(_option(argv, "-n", "")))
            elif command == "typemap_get":
                self._response(command, tid, "")
            elif command == "run" or command in (
                    "step_into", "step_over", "step_out", "interact", "break"):
                self._handle_resume(argv, tid)
            elif command == "status":
                if self._terminated:
                    status = "stopped"
                elif self._stopped:
                    status = "break"
                elif self._launched:
                    status = "running"
                else:
                    status = "starting"
                self._response(command, tid,
                               ' status=%s reason="ok"' % _attr(status))
            elif command == "stack_depth":
                depth = len(self._stack_frames()) if self._stopped else 0
                self._response(command, tid, ' depth="%d"' % depth)
            elif command == "stack_get":
                if not self._stopped:
                    self._response(command, tid, "")
                else:
                    self._handle_stack_get(argv, tid)
            elif command == "context_names":
                if not self._stopped:
                    body = '<context name="Locals" id="0"/>'
                else:
                    scopes = self._scopes_for_current_frame(force=True)
                    body = "".join(
                        '<context name=%s id="%d"/>' %
                        (_attr(scope.get("name", "Locals")), index)
                        for index, scope in enumerate(scopes))
                    if not body:
                        body = '<context name="Locals" id="0"/>'
                self._response(command, tid, "", body)
            elif command == "context_get":
                if not self._stopped:
                    self._response(command, tid, "")
                else:
                    context_id = int(_option(argv, "-c", "0"))
                    self._response(command, tid, "",
                                   self._context_properties(context_id))
            elif command == "property_get":
                fullname = _option(argv, "-n", "")
                var_ref = self._varref_by_fullname.get(fullname)
                if var_ref:
                    variables = self._variables_for_reference(var_ref)
                    attrs = {
                        "name": fullname.rsplit(".", 1)[-1],
                        "fullname": fullname,
                        "type": "string",
                        "classname": "string",
                        "children": "1" if variables else "0",
                        "numchildren": str(len(variables)),
                        "size": str(len(self._value_by_fullname.get(fullname, ""))),
                        "encoding": "base64",
                    }
                    xml = "<property %s>%s%s</property>" % (
                        " ".join("%s=%s" % (key, _attr(val))
                                 for key, val in attrs.items()),
                        _b64(self._value_by_fullname.get(fullname, "")),
                        self._children_xml(var_ref, fullname))
                    self._response(command, tid, "", xml)
                else:
                    xml = self._property_xml_by_fullname.get(fullname)
                    if xml:
                        self._response(command, tid, "", xml)
                    else:
                        self._error(command, tid, 300,
                                    "No such property: %s" % fullname)
            elif command == "property_value":
                fullname = _option(argv, "-n", "")
                self._response(command, tid, ' encoding="base64"',
                               _b64(self._value_by_fullname.get(fullname, "")))
            elif command == "property_set":
                self._response(command, tid, ' success="0"')
            elif command == "eval":
                expression = data or ""
                self._handle_eval(command, tid, expression)
            elif command == "source":
                self._handle_source(argv, tid)
            elif command == "breakpoint_set":
                self._handle_breakpoint_set(argv, tid)
            elif command == "breakpoint_remove":
                self._handle_breakpoint_remove(argv, tid)
            elif command == "breakpoint_update":
                self._handle_breakpoint_update(argv, tid)
            elif command == "breakpoint_list":
                self._handle_breakpoint_list(tid)
            elif command == "restart":
                self._handle_restart(tid)
            elif command in ("stop", "detach"):
                self._shutdown = True
                try:
                    if self.dap:
                        self.dap.request("disconnect", {"terminateDebuggee": True},
                                         wait=False)
                except Exception:
                    pass
                self._response(command, tid,
                               ' status="stopped" reason="ok"')
            elif command == "stdin" or command == "stdout":
                self._response(command, tid, ' success="1"')
            else:
                self._error(command, tid, 4, "Command not supported: %s" % command)
        except Exception as ex:
            _log("command %s failed: %s" % (command, traceback.format_exc()))
            traceback.print_exc()
            self._error(command, tid, 998, str(ex))

    def _handle_stack_get(self, argv, tid):
        frames = self._stack_frames(force=True)
        depth = _option(argv, "-d")
        selected = frames
        if depth is not None:
            try:
                index = int(depth)
                if index >= 0:
                    selected = frames[index:index + 1]
            except ValueError:
                pass
        body = []
        selected_set = {id(frame) for frame in selected}
        for index, frame in enumerate(frames):
            if id(frame) not in selected_set:
                continue
            source = frame.get("source") or {}
            filename = source.get("path") or source.get("name") or ""
            if source.get("path"):
                filename = _path_to_uri(filename)
            body.append(
                '<stack level="%d" type="file" filename=%s lineno="%d" where=%s/>'
                % (index, _attr(filename), int(frame.get("line") or 0),
                   _attr(frame.get("name", ""))))
        self._response("stack_get", tid, "", "".join(body))

    def _handle_breakpoint_set(self, argv, tid):
        filename = _option(argv, "-f", "")
        path = _uri_to_path(filename)
        line = int(_option(argv, "-n", "1") or 1)
        bpid = str(self._next_breakpoint_id)
        self._next_breakpoint_id += 1
        self._breakpoints[bpid] = (path, line)
        self._breakpoints_by_path.setdefault(path, []).append(line)
        self._send_breakpoints(path)
        self._response(command="breakpoint_set", tid=tid,
                       attrs=' id=%s' % _attr(bpid))

    def _handle_breakpoint_remove(self, argv, tid):
        bpid = _option(argv, "-d", "")
        item = self._breakpoints.pop(bpid, None)
        if item:
            path, line = item
            lines = self._breakpoints_by_path.get(path, [])
            try:
                lines.remove(line)
            except ValueError:
                pass
            self._send_breakpoints(path)
        self._response("breakpoint_remove", tid)

    def _handle_breakpoint_update(self, argv, tid):
        bpid = _option(argv, "-d", "")
        item = self._breakpoints.get(bpid)
        if item:
            path, line = item
            new_line = _option(argv, "-n")
            if new_line is not None:
                lines = self._breakpoints_by_path.get(path, [])
                try:
                    lines.remove(line)
                except ValueError:
                    pass
                line = int(new_line)
                lines.append(line)
                self._breakpoints[bpid] = (path, line)
                self._send_breakpoints(path)
        self._response("breakpoint_update", tid)

    def _handle_breakpoint_list(self, tid):
        body = []
        for bpid, (path, line) in self._breakpoints.items():
            body.append(
                '<breakpoint id=%s type="line" filename=%s lineno="%d" state="enabled"/>'
                % (_attr(bpid), _attr(_path_to_uri(path)), line))
        self._response("breakpoint_list", tid, "", "".join(body))

    def _handle_eval(self, command, tid, expression):
        frame_id = self._current_dap_frame()
        arguments = {"expression": expression, "context": "repl"}
        if frame_id:
            arguments["frameId"] = frame_id
        try:
            response = self.dap.request("evaluate", arguments)
            body = response.get("body", {})
            value = body.get("result", "")
        except Exception as ex:
            value = "<evaluation failed: %s>" % ex
        xml = ('<property name="eval" fullname="eval" type="string" '
               'children="0" numchildren="0" size="%d" encoding="base64">%s'
               '</property>' % (len(value), _b64(value)))
        self._response(command, tid, "", xml)

    def _handle_source(self, argv, tid):
        filename = _uri_to_path(_option(argv, "-f", self.program))
        startline = int(_option(argv, "-b", "0") or 0)
        endline = int(_option(argv, "-e", "-1") or -1)
        try:
            with open(filename, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as ex:
            self._error("source", tid, 100, str(ex))
            return
        if startline > 0 or endline > -1:
            lines = lines[startline:endline]
        content = "".join(lines)
        self._response("source", tid,
                       ' filename=%s startline="%d" endline="%d" encoding="base64"'
                       % (_attr(_path_to_uri(filename)), startline, endline),
                       _b64(content))

    def run(self):
        _log("bridge starting: program=%r cwd=%r raku=%r port=%r" %
             (self.program, self.cwd, self.raku, self.options.port))
        self.connect_dbgp()
        threading.Thread(target=self._start_dap, daemon=True).start()
        threading.Thread(target=self._dap_event_loop, daemon=True).start()
        while not self._shutdown:
            raw = self._dbgp_read_command()
            if raw is None:
                break
            if not raw.strip():
                continue
            try:
                argv = shlex.split(raw)
            except ValueError:
                continue
            data = None
            if "--" in argv:
                index = argv.index("--")
                if index + 1 < len(argv):
                    data = _unb64(argv[index + 1])
                argv = argv[:index]
            if not argv:
                continue
            self._handle(raw, argv, data)
        self.close()

    def close(self):
        self._shutdown = True
        try:
            if self.dap:
                self.dap.close()
        except Exception:
            pass
        try:
            if self.dbgp:
                self.dbgp.close()
        except Exception:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Raku DAP to DBGP bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--program", required=True)
    parser.add_argument("--cwd", default="")
    parser.add_argument("--raku", default="")
    parser.add_argument("--adapter", default="")
    parser.add_argument("--adapter-args", default="")
    parser.add_argument("--arg", action="append", default=[])
    parser.add_argument("--include-path", action="append", default=[])
    parser.add_argument("--stop-on-entry", action="store_true")
    parser.add_argument("--exception-filters", default="uncaught",
                        help="comma list: uncaught, all, none")
    parser.add_argument("--idekey", default="komodo")
    options = parser.parse_args(argv)
    bridge = RakuDAPBridge(options)
    bridge.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
