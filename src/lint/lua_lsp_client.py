"""Lua-specific LSP discovery and diagnostics wrapper."""

import logging
import os
import shutil
import tempfile

from lsp_client import get_client, path_to_uri

log = logging.getLogger("lua_lsp_client")

DEFAULT_COMMAND = "lua-language-server"
DEFAULT_ARGS = ""

_COMMON_COMMANDS = ("/usr/bin/lua-language-server",
                    "/usr/local/bin/lua-language-server")


def find_command(prefset, env):
    command = DEFAULT_COMMAND
    if prefset.hasPref("luaLspCommand"):
        command = prefset.getStringPref("luaLspCommand") or DEFAULT_COMMAND

    found = shutil.which(command, path=env.get("PATH"))
    if found:
        return found

    for candidate in _COMMON_COMMANDS:
        if os.path.exists(candidate):
            return candidate

    return None


def find_args(prefset):
    args = DEFAULT_ARGS
    if prefset.hasPref("luaLspArgs"):
        args = prefset.getStringPref("luaLspArgs")
    args = (args or "").strip()
    return args.split() if args else []


def get_diagnostics_for_request(request, text, env=None):
    """Return LSP diagnostics for a Lua lint request, or None to fall back."""
    prefset = request.prefset
    if prefset.hasPref("luaLspEnabled") and not prefset.getBooleanPref("luaLspEnabled"):
        return None
    if prefset.hasPref("luaLspDiagnostics") and not prefset.getBooleanPref("luaLspDiagnostics"):
        return None

    env = dict(env or os.environ)
    command = find_command(prefset, env)
    if not command:
        log.debug("Lua language server not found")
        return None

    path = None
    try:
        if request.koDoc and request.koDoc.file:
            path = request.koDoc.file.path
    except Exception:
        pass

    if path:
        uri = path_to_uri(path)
        root = request.cwd or os.path.dirname(path)
    else:
        ident = getattr(request, "uid", None) or str(os.getpid())
        uri = path_to_uri(os.path.join(tempfile.gettempdir(),
                                       "komodo-lua-unsaved-%s.lua" % ident))
        root = request.cwd or tempfile.gettempdir()

    if not root or not os.path.isdir(root):
        root = os.path.dirname(path) if path else tempfile.gettempdir()

    try:
        client = get_client(command, find_args(prefset), path_to_uri(root),
                            env, language_id="lua")
        return client.get_diagnostics(uri, text)
    except Exception:
        log.exception("Lua LSP diagnostics failed")
        return None
