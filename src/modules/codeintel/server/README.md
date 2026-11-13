# CodeIntel server

The original CodeIntel server was a git submodule:

    src/modules/codeintel/server -> git@github.com:Komodo/codeintel.git

That repository is no longer available.  The sources here were recovered from
the installed extension in the Komodo IDE 12.0.1 Linux installer:

    INSTALLDIR/lib/mozilla/extensions/codeintel@activestate.com/pylib/codeintel/lib

They are placed in the installed package layout under `src/`.  `setup.py` is a
small compatibility shim for the existing `src/modules/codeintel/Conscript`.

Status: Python 2 sources, including the Python 3 target scanner
(`src/language/legacy/python/scanner.py`).  They need to be ported to Python 3
for the in-process Python 3 runtime.  The original C accelerator `closure.so`
and the generated `codeintel.db` are not included.
