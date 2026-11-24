"""Minimal persistent LSP client for Raku diagnostics.

The Raku language server publishes diagnostics as notifications.  Komodo's
linters are pull-based, so this module keeps one server process alive and
turns the pushed diagnostics into ordinary lint results.
"""

import atexit
import errno
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

log = logging.getLogger("raku_lsp_client")
#log.setLevel(logging.DEBUG)

DEFAULT_COMMAND = "nimala-raku-lsp"
DEFAULT_ARGS = "--stdio"


class LspError(Exception):
    pass


class LspTimeout(LspError):
    pass


def _read_exact(stream, length):
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class RakuLspClient:
    """A single long-lived Raku language server process."""

    def __init__(self, command, args, root, env=None, timeout=30.0):
        self._command = command
        self._args = list(args)
        self._root = root
        self._closed = False
        self._write_lock = threading.Lock()
        self._cond = threading.Condition()
        self._next_id = 0
        self._responses = {}
        self._diagnostics = {}   # uri -> {"version", "diagnostics", "time"}
        self._versions = {}      # uri -> latest version sent
        self._open_docs = {}     # uri -> text

        spawn_env = dict(env or os.environ)
        self._proc = subprocess.Popen(
            [command] + self._args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=spawn_env,
            bufsize=0,
        )
        self._reader = threading.Thread(target=self._read_loop,
                                        name="RakuLspReader", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._stderr_loop,
                                               name="RakuLspStderr", daemon=True)
        self._stderr_reader.start()
        self._initialize(timeout)

    # -- transport ---------------------------------------------------------

    def _send(self, message):
        data = json.dumps(message).encode("utf-8")
        header = b"Content-Length: %d\r\n\r\n" % len(data)
        with self._write_lock:
            if self._proc.poll() is not None:
                raise LspError("language server exited")
            self._proc.stdin.write(header + data)
            self._proc.stdin.flush()

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params, timeout):
        with self._cond:
            self._next_id += 1
            request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id,
                    "method": method, "params": params})

        deadline = time.time() + timeout
        with self._cond:
            while request_id not in self._responses:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise LspTimeout("timeout waiting for %s" % method)
                self._cond.wait(remaining)
            message = self._responses.pop(request_id)

        if "error" in message:
            raise LspError("%s: %r" % (method, message["error"]))
        return message.get("result")

    def _read_loop(self):
        stream = self._proc.stdout
        try:
            while not self._closed:
                headers = {}
                while True:
                    line = stream.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line:
                        break
                    if b":" in line:
                        key, value = line.split(b":", 1)
                        headers[key.strip().lower()] = value.strip()

                try:
                    length = int(headers.get(b"content-length", b"0"))
                except ValueError:
                    continue
                if length <= 0:
                    continue

                body = _read_exact(stream, length)
                if body is None:
                    return
                try:
                    message = json.loads(body.decode("utf-8", "replace"))
                except ValueError:
                    log.warning("Raku LSP sent invalid JSON")
                    continue

                with self._cond:
                    if "id" in message:
                        self._responses[message["id"]] = message
                    elif message.get("method") == "textDocument/publishDiagnostics":
                        params = message.get("params") or {}
                        uri = params.get("uri")
                        if uri:
                            self._diagnostics[uri] = {
                                "version": params.get("version"),
                                "diagnostics": params.get("diagnostics") or [],
                                "time": time.time(),
                            }
                    self._cond.notify_all()
        except Exception:
            log.exception("Raku LSP reader stopped")
        finally:
            with self._cond:
                self._cond.notify_all()

    def _stderr_loop(self):
        try:
            for line in self._proc.stderr:
                log.debug("Raku LSP: %s", line.decode("utf-8", "replace").rstrip())
        except Exception:
            pass

    # -- protocol ----------------------------------------------------------

    def _initialize(self, timeout):
        params = {
            "processId": os.getpid(),
            "rootUri": self._root,
            "capabilities": {
                "workspace": {"workspaceFolders": True},
                "textDocument": {
                    "synchronization": {"dynamicRegistration": False},
                },
            },
            "initializationOptions": {},
        }
        self._request("initialize", params, timeout)
        self._notify("initialized", {})

    def _sync_document(self, uri, text):
        previous = self._open_docs.get(uri)
        version = self._versions.get(uri, 0) + 1
        self._versions[uri] = version

        if previous is None:
            self._notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": uri,
                    "languageId": "raku",
                    "version": version,
                    "text": text,
                }
            })
        else:
            previous_lines = previous.splitlines()
            if previous.endswith("\n"):
                previous_lines.append("")
            end_line = max(len(previous_lines) - 1, 0)
            end_char = len(previous_lines[-1]) if previous_lines else 0
            self._notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": end_line, "character": end_char},
                    },
                    "text": text,
                }],
            })

        self._open_docs[uri] = text
        return version

    def get_diagnostics(self, uri, text, timeout=6.0, settle=0.3):
        """Return the diagnostics for *uri* or None if the server timed out."""
        if self._proc.poll() is not None:
            return None

        try:
            version = self._sync_document(uri, text)
        except (LspError, OSError):
            return None

        marker = time.time()
        deadline = marker + timeout
        with self._cond:
            while True:
                entry = self._diagnostics.get(uri)
                if (entry and entry["time"] >= marker and
                        entry.get("version") in (version, None)):
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    log.debug("timed out waiting for diagnostics: %s", uri)
                    return None
                self._cond.wait(remaining)

            # Servers often publish an empty notification before analysis
            # finishes, so give an empty result a moment to be replaced.
            if not entry["diagnostics"] and settle > 0:
                self._cond.wait(settle)
                entry = self._diagnostics.get(uri, entry)

            return list(entry["diagnostics"])

    def is_alive(self):
        return not self._closed and self._proc.poll() is None

    def stop(self):
        if self._closed:
            return
        self._closed = True
        try:
            for uri in list(self._open_docs):
                self._notify("textDocument/didClose",
                             {"textDocument": {"uri": uri}})
            self._request("shutdown", {}, 3)
            self._notify("exit", {})
        except Exception:
            pass
        try:
            self._proc.terminate()
        except Exception:
            pass
        try:
            self._proc.wait(3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


_client = None
_client_key = None
_client_lock = threading.Lock()


def get_client(command, args, root, env=None):
    """Return a shared client, restarting it if the configuration changed."""
    global _client, _client_key

    key = (command, tuple(args), root)
    with _client_lock:
        if _client is not None and _client_key == key and _client.is_alive():
            return _client
        if _client is not None:
            try:
                _client.stop()
            except Exception:
                pass
        _client = RakuLspClient(command, args, root, env)
        _client_key = key
        return _client


def stop_client():
    global _client, _client_key
    with _client_lock:
        if _client is not None:
            try:
                _client.stop()
            except Exception:
                pass
        _client = None
        _client_key = None


atexit.register(stop_client)


def _path_to_uri(path):
    try:
        from pathlib import Path
        return Path(os.path.abspath(path)).as_uri()
    except Exception:
        return "file://" + path


def find_command(prefset, env):
    """Resolve the configured LSP command, mirroring the JS mediator."""
    command = DEFAULT_COMMAND
    if prefset.hasPref("rakuLspCommand"):
        command = prefset.getStringPref("rakuLspCommand") or DEFAULT_COMMAND

    found = shutil.which(command, path=env.get("PATH"))
    if found:
        return found, command

    raku_path = None
    try:
        import components as xpcomponents
        info = xpcomponents.classes["@activestate.com/koAppInfoEx?app=Raku;1"] \
            .getService(xpcomponents.interfaces.koIAppInfoEx)
        raku_path = info.getExecutableFromPrefs(prefset)
    except Exception:
        pass

    if raku_path:
        base = os.path.basename(command)
        raku_dir = os.path.dirname(raku_path)
        candidates = [
            os.path.join(raku_dir, "..", "share", "perl6", "site", "bin", base),
            os.path.join(raku_dir, base),
        ]
        for candidate in candidates:
            candidate = os.path.normpath(candidate)
            if os.path.exists(candidate):
                return candidate, command

    return None, command


def find_args(prefset):
    args = DEFAULT_ARGS
    if prefset.hasPref("rakuLspArgs"):
        args = prefset.getStringPref("rakuLspArgs")
    args = (args or "").strip()
    return args.split() if args else []


def get_diagnostics_for_request(request, text, env=None):
    """Convenience wrapper used by the Raku linter.

    Returns a list of diagnostics, or None when LSP diagnostics are
    unavailable and the caller should fall back to another linter.
    """
    prefset = request.prefset
    if prefset.hasPref("rakuLspEnabled") and not prefset.getBooleanPref("rakuLspEnabled"):
        return None
    if prefset.hasPref("rakuLspDiagnostics") and not prefset.getBooleanPref("rakuLspDiagnostics"):
        return None

    env = dict(env or os.environ)
    command, _ = find_command(prefset, env)
    if not command:
        log.debug("Raku LSP command not found")
        return None

    # Make the Raku runtime visible to scripts launched by the LSP server.
    try:
        import components as xpcomponents
        info = xpcomponents.classes["@activestate.com/koAppInfoEx?app=Raku;1"] \
            .getService(xpcomponents.interfaces.koIAppInfoEx)
        raku_path = info.getExecutableFromPrefs(prefset)
        if raku_path:
            env["PATH"] = os.path.dirname(raku_path) + os.pathsep + env.get("PATH", "")
    except Exception:
        pass

    path = None
    try:
        if request.koDoc and request.koDoc.file:
            path = request.koDoc.file.path
    except Exception:
        pass

    if path:
        uri = _path_to_uri(path)
        root = request.cwd or os.path.dirname(path)
    else:
        ident = getattr(request, "uid", None) or str(os.getpid())
        uri = _path_to_uri(os.path.join(tempfile.gettempdir(),
                                        "komodo-raku-unsaved-%s.raku" % ident))
        root = request.cwd or tempfile.gettempdir()

    if not root or not os.path.isdir(root):
        root = os.path.dirname(path) if path else tempfile.gettempdir()

    try:
        client = get_client(command, find_args(prefset), _path_to_uri(root), env)
        return client.get_diagnostics(uri, text)
    except Exception:
        log.exception("Raku LSP diagnostics failed")
        return None
