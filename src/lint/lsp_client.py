"""Minimal persistent LSP client for diagnostics.

Language servers publish diagnostics as notifications.  Komodo's linters are
pull-based, so this module keeps one server process alive and turns the pushed
diagnostics into ordinary lint results.
"""

import atexit
import json
import logging
import os
import subprocess
import threading
import time

log = logging.getLogger("lsp_client")
#log.setLevel(logging.DEBUG)

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


class LspClient:
    """A single long-lived language server process."""

    def __init__(self, command, args, root, env=None, language_id="text", timeout=30.0):
        self._command = command
        self._language_id = language_id
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
                                        name="LspReader", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._stderr_loop,
                                               name="LspStderr", daemon=True)
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
                    log.warning("Language server sent invalid JSON")
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
            log.exception("Language server reader stopped")
        finally:
            with self._cond:
                self._cond.notify_all()

    def _stderr_loop(self):
        try:
            for line in self._proc.stderr:
                log.debug("Language server: %s", line.decode("utf-8", "replace").rstrip())
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
                    "languageId": self._language_id,
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


def get_client(command, args, root, env=None, language_id="text"):
    """Return a shared client, restarting it if the configuration changed."""
    global _client, _client_key

    key = (command, tuple(args), root, language_id)
    with _client_lock:
        if _client is not None and _client_key == key and _client.is_alive():
            return _client
        if _client is not None:
            try:
                _client.stop()
            except Exception:
                pass
        _client = LspClient(command, args, root, env, language_id)
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


def path_to_uri(path):
    try:
        from pathlib import Path
        return Path(os.path.abspath(path)).as_uri()
    except Exception:
        return "file://" + path


def to_lint_results(diagnostics):
    """Convert LSP diagnostics into Komodo lint results."""
    from koLintResult import KoLintResult
    from koLintResults import koLintResults

    results = koLintResults()
    for diagnostic in diagnostics:
        range_ = diagnostic.get("range") or {}
        start = range_.get("start") or {}
        end = range_.get("end") or {}
        start_line = int(start.get("line", 0)) + 1
        end_line = max(int(end.get("line", start.get("line", 0))) + 1, start_line)
        start_col = int(start.get("character", 0)) + 1
        end_col = int(end.get("character", start.get("character", 0))) + 1
        if end_col <= start_col:
            end_col = start_col + 1
        severity = {
            1: KoLintResult.SEV_ERROR,
            2: KoLintResult.SEV_WARNING,
        }.get(diagnostic.get("severity"), KoLintResult.SEV_INFO)
        results.addResult(KoLintResult(
            description=diagnostic.get("message", "LSP diagnostic"),
            severity=severity,
            lineStart=start_line,
            lineEnd=end_line,
            columnStart=start_col,
            columnEnd=end_col,
        ))
    return results

