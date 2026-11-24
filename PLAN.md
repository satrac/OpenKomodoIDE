# OpenKomodoIDE next steps

Status snapshot (2026-11-23):

- Python 3.14 runtime port is complete: IDE, PyXPCOM, CodeIntel, DBGP
  debugging, linting and optional modules.
- XUL IDE builds and runs on Python 3.14 / modern GCC.
- Fresh `make full`, `make check`, `make codeintel-db`, `make image` and
  `make install-local` have all been validated.
- The only Python 2 left is the build-only 2.7 shim for the legacy Mozilla 35
  IDL/build tools; it is not an application runtime dependency.  The Python 2
  language, linters, DBGP client and CodeIntel stdlib data have been removed.
- Quickstart, New/Open/Save, Preferences, project pane, toolbox and syntax
  highlighting work.
- Symbol Browser works for Perl, PHP, Ruby, JavaScript, Tcl, HTML, CSS and the
  UDL languages, and for Python through the `ast` scanner.
- Restored-session tabs and Symbol Browser state are fixed.
- `make build` and `make check` pass.

## 1. Seed the CodeIntel database (done)

The server previously logged `stdlib file ':perl:' does not exist` and had no
standard-library symbols.

- Extended the `build_db.py` CIX mapping for the available versioned dumps and
  made PHP fall back to the PHP 5 dump when PHP 7 is unavailable.
- Generated the database from the local dumps:

  ```sh
  make codeintel-db CIX_DIR=~/Projects/komodo-forks/komodo-codeintel2/stdlibs
  ```

- Result: 34.6 MB database, 19 catalog groups (Python 3, Perl, JavaScript,
  PHP 5, Ruby, Tcl, CSS, HTML5, XUL and the catalogs).  Python 2 stdlib and
  pywin32 data were removed when the Python 2 language was dropped.
- A fresh profile copies the seeded database and starts without missing-stdlib
  errors; Symbol Browser works and Perl completions resolve `open`/`opendir`.
- `make codeintel-db` now also stages the database under
  `build/release/modules/codeintel/pylib/codeintel/`, so the following
  `make build`/`make image` packages it.
- Also fixed a Python 3 crash in the import resolver: `Scope` objects were used
  as unhashable dictionary keys during completion resolution.

Acceptance met for Perl and Python symbol resolution. Python's own completion
and goto-definition contexts remain stubs (step 2).

## 2. Finish Python CodeIntel (done)

The `ast` scanner now implements symbol browsing, scope/member completions,
go-to-definition, call tips, find-references and import-aware completions.

- Import-aware member completions: the scanner loads the seeded `:python3:`
  stdlib into its built-in scope when the CodeIntel database is available.
  `import os`, `import os.path as p` and `from os import path` resolve to the
  database modules, so `os.` / `p.` / `path.` complete correctly in the UI.
- `self.` and `cls.` resolve to the enclosing class.  `self.attr = value`
  assignments are recorded as instance variables, and simple constructor
  assignments (`obj = MyClass()`) infer the class type.
- Find-references works for names in the current file and walks `.py` files
  under `PYTHONPATH` for project-wide matches.
- Member completion type resolution now keys its recursion guard by `id()`
  instead of the unhashable symbol objects (Python 3).

Verified directly against the scanner and in the GUI: `os.p` shows `path`,
`pipe`, `popen`, etc., `self.` shows methods and instance attributes, and
find-references returns the definition and call sites.

## 3. Clean-build validation (done)

- `make full` now succeeds end-to-end on the current tree, including the
  Mozilla rebuild, PyXPCOM, Python 3 dependencies and the Komodo app build.
- Fixed the issues exposed by the full build:
  - `patchtree.py` opened `.patchtree-state` with `a+` and read from the end,
    so it always thought no patches were applied.  It now seeks to the start.
  - `install-py3-python-deps.py` creates `bin/python/komodo` when the app has
    not been built yet, and uses the build/copy helper for sgmlop.
  - pathtools, watchdog and zope.cachedescriptors use the same helper instead
    of deprecated `setup.py install`.
  - The helper also handles pure-Python `build/lib` output (no platform
    suffix).
  - Core-extension reinstalls preserve an existing CodeIntel database.
  - `chromereg.py` now locks `components/komodo.manifest` with the same
    `<manifest>.lck` protocol as Mozilla's `buildlist.py`.  Without this,
    parallel registration reads the manifest, then a concurrent writer
    rewrites it and silently drops most Python component entries, which
    leaves Komodo without a window or Python logging.
  - `make check` runs `manifest-check` to catch a truncated manifest early.
  - `URIlib.FileHandler.write` converts a PyXPCOM text result back to bytes,
    so saving files no longer fails with
    `TypeError: a bytes-like object is required, not 'str'`.
- After `make clean-app` (or a fresh app tree) run:

  ```sh
  make python3-deps
  make codeintel-db CIX_DIR=~/Projects/komodo-forks/komodo-codeintel2/stdlibs
  make check
  ```

- A full build with a completely fresh clone and fresh Mozilla source tree is
  still the final packaging validation, but the current full-build path is
  green.

## 4. GUI smoke-test matrix (done)

Use a fresh profile and exercise:

- New File, New File from Template, Open File, Open Project.
- Save and Save As.
- Preferences and project/file preferences.
- Project pane, toolbox, Commando, split views.
- Notifications and file-change detection.
- Debugger/DBGP service startup.

Acceptance: no startup exceptions that leave a pane blank or a spinner stuck.

Results so far:

- New file, Save and Save As work.  Save As appends the language extension
  when the entered name already has it (`name.txt` becomes `name.txt.txt`);
  this is the only known cosmetic issue.
- Split View works from `View > Split View` (two panes share one document).
- Notifications work: a failed Go to Definition shows
  "Could not find definition" in the notification area.
- Python linting works after installing `py3compile.py` and fixing
  `tabnanny`'s Python 3 `SystemExit` on tokenization errors.
- Python 3 debugging works end to end.  `src/dbgp/PyDBGP/Conscript` now
  installs the target-side `py3_dbgp` driver and pure-Python `python3lib`
  into `support/dbgp`, and the IDE-side DBGP transport handles Python 3
  bytes/str, base64 and terminal decoding.  A sample script runs under the
  debugger, prints its output in the Debug Output pane and enters Break Mode.
- Remaining minor issue: the Save As file chooser appends the language
  extension even when the typed name already has it.

## 5. Optional modules (done for the current build)

- `make modules` now clones and installs `golang`, `editorconfig`,
  `focusmode` and `trackchanges`.
- Fixed Python 3 issues found during the smoke test:
  - golang: `has_key`, `except ... , e`, `print e` and regex escapes.
  - editorconfig: Python 3.11+ inline regex flags and old `.next()` iterators.
  - trackchanges: malformed logging call, PyXPCOM async-callback adapter
    (the SCC `cat` path now works instead of falling back to disk), and the
    change-list marshalling fix.
- The fixes are stored as patches under `util/module-patches/` and applied by
  `make modules` after cloning, so a fresh checkout reproduces them. The
  clones themselves remain optional/untracked and `make modules` remains part
  of the full-build path.

## 6. Housekeeping (done)

- No duplicate `squirt` clone remains; the fork inventory has 42 clones with
  42 distinct remotes.
- `DEPENDENCIES.md` documents the vendored PyDBGP driver and the optional
  module build path.
- Removed the dead legacy Python scanner (`scanner.py`, `import_resolver.py`
  and `tdparser.py`); the `ast` scanner is now the only Python scanner.
- Removed the Python 2 language/lexer, Python 2 linter components, preference
  page, sample tools and CodeIntel `:python2:` data.  `.py` now maps directly
  to `Python3`.
- Remaining: keep pruning unused Python 2-only files as they are encountered.

## 7. Hardening and packaging validation (done for the current tree)

- Replaced the remaining `Thread.isAlive()` calls with `is_alive()` in
  `koFileStatusService` and the DBGP clients (dev and packaged copies).
  Shutdown no longer logs the `Thread` attribute error.
- Save As no longer duplicates the extension (`name.txt` stays `name.txt`).
- `make image` now works.  It follows the Mozilla dist's relative symlinks
  while skipping genuinely dangling `dist-info` links, and the image contains
  `libxul`, `libmozsandbox`, `libnspr4`, `libpyxpcom`, the CodeIntel database,
  the Python 3 packages and the DBGP support files.
- `make install-local` works with an alternate `LOCAL_PREFIX`; the installed
  launcher starts the IDE.
- `make check` passes.

- Fresh-checkout support: `make` now generates `mozilla/config.py` via
  `moz-configure` when it is missing.  The Firefox 35 source tarball must be
  supplied at `mozilla/firefox-35.0.source.tar.bz2` (or via
  `MOZ_SRC_TARBALL=/path/to/tarball`); it is not tracked because it is 169 MB.
- The first fresh-tree build exposed a missing breakpad compatibility patch:
  modern glibc no longer provides the `ucontext` typedef.  Added
  `breakpad_ucontext.patch` so the fresh source uses `ucontext_t`.

Fresh-worktree validation (done):

- `make full` succeeded from a clean worktree after fixing:
  - missing generated `mozilla/config.py` (`moz-configure`);
  - Python 3 `target_configure` falling through to the Python 2 prebuilt
    path;
  - modern glibc's missing `ucontext` typedef (`breakpad_ucontext.patch`);
  - detached-HEAD handling in `bklocal.py`;
  - ignored `src/releasetime.txt` required by `bk configure`.
- `make check` passed in the fresh worktree.
- `make codeintel-db` built the 45 MB database from the CIX dumps.
- The fresh app started, seeded CodeIntel and opened a Python file.
- `make image` from the fresh worktree produced a working image.

Python 2 target debugging removed:

- Only `py3_dbgp` and `support/dbgp/python3lib` are installed.
- The Python 2 `pydbgp`/`pydbgpproxy` drivers and `support/dbgp/pythonlib`
  were removed.
- Selecting a Python 2 interpreter now reports that Python 2 debugging is
  not supported.

Build-only Python 2.7 decision:

- Keep the bundled Python 2.7 build-tool shim for the Mozilla 35 IDL/build
  helpers.  It is not an application runtime dependency.  Porting those
  helpers to Python 3 is optional and is not required for the IDE.

Remaining optional follow-ups:

- The `underscore.js` `n is undefined` error remains; it comes from a
  throttled callback invoked after its view disappears and does not affect
  normal operation.

## Raku support

Phase 0 is implemented: native `Raku` language detection, `.raku`/
`.rakumod`/`.rakutest`/`.p6`/`.pm6`/`.t6` associations, highlighting via the
Perl lexer, `raku -c` syntax checking, run support, and a
`raku --repl-mode=process` REPL.  See [RAKU.md](RAKU.md).

Raku debugging now uses a DAP-to-DBGP bridge in
`src/dbgp/raku_dap_bridge.py`, so it reuses Komodo's existing debugger UI.
Breakpoints, stop-on-entry, stepping, stack/variables and evaluation are
working.  Phase 4 (LSP completions, symbols, go-to-definition,
find-references, calltips and LSP diagnostics) is implemented.  Editing uses
the dedicated UDL lexer in `src/udl/udl/raku.udl`, and `.rakudoc`, `.nqp` and
`.pod6` are associated with Raku.  Snippets, script/module templates and a
`raku.svg` file icon ship with the language.  The remaining roadmap details
are in [RAKU.md](RAKU.md).

## Lua support

Lua now uses the Lua Language Server for completions, symbols,
go-to-definition, find-references and calltips, with LSP diagnostics and a
`luac -p` fallback.  Run support, a Lua preference page and `.rockspec`
associations are included; a debugger is the main remaining gap.  See
[LUA.md](LUA.md).
