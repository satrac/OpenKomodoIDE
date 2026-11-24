#!python
# Copyright (c) 2026 ActiveState Software Inc.
# See the file LICENSE.txt for licensing information.

"""Raku syntax checking.

Uses diagnostics from the Raku language server when available and falls back
to `raku -c` otherwise.
"""

import os
import re
import sys
import tempfile

from xpcom import components
from koLintResult import KoLintResult, createAddResult
from koLintResults import koLintResults
import process
import koprocessutils
import raku_lsp_client
import logging

log = logging.getLogger("koRakuLinter")
#log.setLevel(logging.DEBUG)


class KoRakuLinter:
    _com_interfaces_ = [components.interfaces.koILinter]
    _reg_desc_ = "Komodo Raku Linter"
    _reg_clsid_ = "{60cb7384-bd8c-4b58-8e9d-c8cdf30e2709}"
    _reg_contractid_ = "@activestate.com/koLinter?language=Raku;1"
    _reg_categories_ = [
        ("category-komodo-linter", "Raku"),
    ]

    def __init__(self):
        self._rakuInfo = components.classes[
            "@activestate.com/koAppInfoEx?app=Raku;1"].getService(
                components.interfaces.koIAppInfoEx)

    def lint(self, request):
        text = request.content
        return self.lint_with_text(request, text)

    def _diagnostics_to_results(self, diagnostics):
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
                description=diagnostic.get("message", "Raku diagnostic"),
                severity=severity,
                lineStart=start_line,
                lineEnd=end_line,
                columnStart=start_col,
                columnEnd=end_col,
            ))
        return results

    def lint_with_text(self, request, text):
        prefset = request.prefset
        if (prefset.hasPref("genericLinter:Raku") and
                not prefset.getBooleanPref("genericLinter:Raku")):
            return None

        env = koprocessutils.getUserEnv()
        if prefset.hasPref("rakuExtraPaths"):
            rakuLib = prefset.getStringPref("rakuExtraPaths")
            if rakuLib:
                if sys.platform.startswith("win"):
                    rakuLib = rakuLib.replace('\\', '/')
                env["RAKULIB"] = rakuLib

        # Prefer the language server's diagnostics when it is available.
        try:
            diagnostics = raku_lsp_client.get_diagnostics_for_request(
                request, text, env)
        except Exception:
            log.exception("Raku LSP diagnostics failed")
            diagnostics = None
        if diagnostics is not None:
            return self._diagnostics_to_results(diagnostics)

        rakuExe = self._rakuInfo.getExecutableFromPrefs(prefset)
        if not rakuExe:
            return None

        tmpfilename = tempfile.mktemp(suffix=".raku")
        try:
            with open(tmpfilename, "wb") as fout:
                fout.write(text.encode(request.encoding.python_encoding_name))

            argv = [rakuExe, "-c", tmpfilename]
            p = process.ProcessOpen(argv, cwd=request.cwd or None, env=env,
                                    stdin=None)
            stdout, stderr = p.communicate()
            returncode = p.returncode
        except Exception:
            log.exception("Failed to run Raku syntax check: %r", argv)
            return None
        finally:
            try:
                os.unlink(tmpfilename)
            except OSError:
                pass

        def _to_text(value):
            if not value:
                return ""
            if isinstance(value, bytes):
                return value.decode("utf-8", "replace")
            return value

        output = _to_text(stdout) + _to_text(stderr)
        if returncode == 0 and "Syntax OK" in output:
            return koLintResults()

        lines = output.splitlines()
        description_parts = []
        line_no = None
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("===SORRY!==="):
                continue
            if stripped.startswith("------>"):
                continue
            match = re.search(r'\bat .*?:(\d+)\b', stripped)
            if match:
                line_no = int(match.group(1))
                continue
            description_parts.append(stripped)

        if not description_parts and returncode == 0:
            return koLintResults()

        description = " ".join(description_parts) or "Raku syntax error"
        if line_no is None:
            line_no = 1

        results = koLintResults()
        createAddResult(results,
                        text.splitlines(),
                        KoLintResult.SEV_ERROR,
                        line_no,
                        description)
        return results
