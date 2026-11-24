"""Raku-specific LSP discovery and diagnostics wrapper."""

import logging
import os
import shutil

from lsp_client import get_client, path_to_uri, to_lint_results  # noqa: F401

log = logging.getLogger("raku_lsp_client")

DEFAULT_COMMAND = "nimala-raku-lsp"
DEFAULT_ARGS = "--stdio"


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
        uri = path_to_uri(path)
        root = request.cwd or os.path.dirname(path)
    else:
        ident = getattr(request, "uid", None) or str(os.getpid())
        uri = path_to_uri(os.path.join(tempfile.gettempdir(),
                                        "komodo-raku-unsaved-%s.raku" % ident))
        root = request.cwd or tempfile.gettempdir()

    if not root or not os.path.isdir(root):
        root = os.path.dirname(path) if path else tempfile.gettempdir()

    try:
        client = get_client(command, find_args(prefset), path_to_uri(root), env,
                            language_id="raku")
        return client.get_diagnostics(uri, text)
    except Exception:
        log.exception("Raku LSP diagnostics failed")
        return None
