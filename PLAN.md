# OpenKomodoIDE next steps

Status snapshot (2026-11-21):

- XUL IDE builds and runs on Python 3.14 / modern GCC.
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

- Result: 46.9 MB database, 340,079 symbols, 21 catalog groups (Python 2/3,
  Perl, JavaScript, PHP 5, Ruby, Tcl, CSS, HTML5, XUL and the catalogs).
- A fresh profile copies the seeded database and starts without missing-stdlib
  errors; Symbol Browser works and Perl completions resolve `open`/`opendir`.
- `make codeintel-db` now also stages the database under
  `build/release/modules/codeintel/pylib/codeintel/`, so the following
  `make build`/`make image` packages it.
- Also fixed a Python 3 crash in the import resolver: `Scope` objects were used
  as unhashable dictionary keys during completion resolution.

Acceptance met for Perl and Python symbol resolution. Python's own completion
and goto-definition contexts remain stubs (step 2).

## 2. Finish Python CodeIntel (in progress)

The `ast` scanner now implements symbol browsing, basic scope/member
completions, go-to-definition and call tips. Remaining:

- Find-references for Python names.
- Import-aware member completions (e.g. `os.` using the seeded database).
- Better handling of `self.` and instance attributes.

Verified directly against the scanner: `foo` and `print` appear in scope
completions, go-to-definition resolves a local function to its line, and call
tips show `foo(alpha, beta)` with the function docstring. Incomplete buffers
are parsed tolerantly so call tips still work while typing.
The same changes removed a `Scope`-as-unhashable-key crash in the generic
completion import resolver.

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

## 4. GUI smoke-test matrix

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
  this is cosmetic and tracked as a known minor issue.
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
  - trackchanges: malformed logging call and an SCC fallback to on-disk
    changes when the SCC backend cannot return file contents via PyXPCOM.
- The fixes are stored as patches under `util/module-patches/` and applied by
  `make modules` after cloning, so a fresh checkout reproduces them. The
  clones themselves remain optional/untracked and `make modules` remains part
  of the full-build path.

## 6. Housekeeping

- Consolidate the duplicate `squirt` clone (currently has an uncommitted
  `Makefile.PL` rebrand change).
- Refresh `DEPENDENCIES.md` with the final fork/mirror inventory.
- Remove obsolete stubs and dead Python 2-only build paths.
