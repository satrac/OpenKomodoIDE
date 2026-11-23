# CodeIntel server

The original CodeIntel server was a git submodule:

    src/modules/codeintel/server -> git@github.com:Komodo/codeintel.git

That repository is no longer available.  The sources here were recovered from
the installed extension in the Komodo IDE 12.0.1 Linux installer:

    INSTALLDIR/lib/mozilla/extensions/codeintel@activestate.com/pylib/codeintel/lib

They are placed in the installed package layout under `src/`.  `setup.py` is a
small compatibility shim for the existing `src/modules/codeintel/Conscript`.

## codeintel.db

`codeintel.db` is generated, not source.  It is a SQLite seed of stdlib and
catalog symbols built by `ciximport.py`/`build_db.py`.  It is not committed:
`db/database.py` creates an empty database and logs a warning if the seed is
absent, so the server still runs.

To build a seed database:

    pip install -r requirements.txt
    python3 build_db.py --db codeintel.db [--cix-dir DIR]

The script imports CSS, HTML, XUL, Tcl and the catalogs in
`src/modules/codeintel/codeintel-catalogs/catalogs` without extra inputs.
The language stdlib CIX dumps (`python3.cix`, `javascript.cix`,
`perl.cix`, `php7.cix`, `ruby.cix`) were removed from the installed
extension.  Old copies are available from the
`mook/komodo-codeintel2` repository's `stdlibs/` directory; use
`--cix-dir` to import them.

## Status

The server and Python scanner run on Python 3.14.  Python source scanning uses
the `ast`-based scanner; the legacy `compiler`/`parser` scanner has been
removed.  Python 2 stdlib symbols are no longer imported.
