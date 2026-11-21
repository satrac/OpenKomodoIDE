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

## 3. Clean-build validation

- Run `make modules` to populate optional module sources.
- Run `make full` from a clean worktree/clone with no existing build artifacts.
- Verify `make check` and a fresh-profile GUI launch afterwards.

Acceptance: a clean checkout builds end-to-end and launches with a working
Symbol Browser.

## 4. GUI smoke-test matrix

Use a fresh profile and exercise:

- New File, New File from Template, Open File, Open Project.
- Save and Save As.
- Preferences and project/file preferences.
- Project pane, toolbox, Commando, split views.
- Notifications and file-change detection.
- Debugger/DBGP service startup.

Acceptance: no startup exceptions that leave a pane blank or a spinner stuck.

## 5. Optional modules

- Decide whether `golang`, `editorconfig`, `focusmode` and `trackchanges`
  should be installed by default.
- Verify `make modules` plus a rebuild installs them correctly.

## 6. Housekeeping

- Consolidate the duplicate `squirt` clone (currently has an uncommitted
  `Makefile.PL` rebrand change).
- Refresh `DEPENDENCIES.md` with the final fork/mirror inventory.
- Remove obsolete stubs and dead Python 2-only build paths.
