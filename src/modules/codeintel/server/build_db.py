#!/usr/bin/env python3
"""Build the CodeIntel seed database (codeintel.db).

The database is a generated SQLite catalog of stdlib/catalog symbols.  The
original build created it with ciximport.py from a set of CIX dumps and DTD
catalogs.  Those CIX dumps are not part of the installed Komodo extension
(the build removes *.cix), so this script imports whatever CIX/DTD inputs are
present and skips the missing ones.

The server does not require this file: if it is absent, Database.initialize()
creates an empty database and logs a warning.  Committing the generated
~55 MB database is unnecessary.

Usage:
    python3 build_db.py [--db codeintel.db] [--cix-dir DIR]

Place optional stdlib CIX files in the language directories expected by the
stdlib modules, for example:

    language/legacy/python/python2.cix
    language/legacy/python/python3.cix
    language/legacy/javascript/javascript.cix
    language/legacy/javascript/node.js.cix
    language/legacy/perl/perl.cix
    language/legacy/php/php5.cix
    language/legacy/php/php7.cix
    language/legacy/ruby/ruby.cix
    language/legacy/tcl/tcl.cix

Catalogs from src/modules/codeintel/codeintel-catalogs/catalogs are copied
into the language catalogs/ directories automatically.
"""

import argparse
import logging
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
CATALOG_SRC = os.path.abspath(os.path.join(
    HERE, "..", "codeintel-catalogs", "catalogs"))

sys.path.insert(0, SRC)

log = logging.getLogger("build_db")


def _copy_catalogs(dest_subdir, names):
    dest = os.path.join(SRC, "language", "legacy", dest_subdir, "catalogs")
    os.makedirs(dest, exist_ok=True)
    for name in names:
        src = os.path.join(CATALOG_SRC, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, name))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.path.join(HERE, "codeintel.db"))
    parser.add_argument("--cix-dir", default=None,
                        help="optional directory containing stdlib CIX files")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.cix_dir:
        # Copy any supplied CIX files into the expected language directories
        # using the conventional names.
        mapping = {
            "python2.cix": ("python", "python2.cix"),
            "python3.cix": ("python", "python3.cix"),
            "python-2.7.cix": ("python", "python2.cix"),
            "python3-3.3.cix": ("python", "python3.cix"),
            "javascript.cix": ("javascript", "javascript.cix"),
            "node.js.cix": ("javascript", "node.js.cix"),
            "perl.cix": ("perl", "perl.cix"),
            "perl-5.16.cix": ("perl", "perl.cix"),
            "php5.cix": ("php", "php5.cix"),
            "php7.cix": ("php", "php7.cix"),
            # Versioned dumps from komodo-codeintel2/stdlibs.
            "php-5.4.cix": ("php", "php5.cix"),
            "ruby.cix": ("ruby", "ruby.cix"),
            "ruby-1.9.3.cix": ("ruby", "ruby.cix"),
            "tcl.cix": ("tcl", "tcl.cix"),
        }
        for name in os.listdir(args.cix_dir):
            if name not in mapping:
                continue
            lang, target = mapping[name]
            dest = os.path.join(SRC, "language", "legacy", lang, target)
            shutil.copy2(os.path.join(args.cix_dir, name), dest)

    _copy_catalogs("javascript", [
        "angular.cix", "dojo.cix", "ember.cix", "jquery.cix",
        "mochikit.cix", "prototype.cix", "yui.cix",
    ])
    _copy_catalogs("php", ["drupal.cix", "pecl.cix"])
    _copy_catalogs("ruby", ["rails.cix"])
    _copy_catalogs("python", ["pywin32.cix", "python3-pywin32.cix"])

    from config import Config
    from db import Database

    db_path = os.path.abspath(args.db)
    if os.path.exists(db_path):
        os.unlink(db_path)
    Database.initialize(db_path, Config.get("closure_ext_path"))

    # Use PHP 7 symbols when available, otherwise the PHP 5 dump.
    php_stdlib_attr = "PHP_7_STDLIB_FILE"
    php7_cix = os.path.join(SRC, "language", "legacy", "php", "php7.cix")
    if not os.path.exists(php7_cix):
        php_stdlib_attr = "PHP_5_STDLIB_FILE"

    # (module, stdlib constant attribute, catalog constant attributes)
    targets = [
        ("language.legacy.python.stdlib", "PYTHON2_STDLIB_FILE",
         ("CATALOG_PYWIN32_FILE",)),
        ("language.legacy.python.stdlib", "PYTHON3_STDLIB_FILE",
         ("CATALOG_PYTHON3_PYWIN32_FILE",)),
        ("language.legacy.javascript.stdlib", "JAVASCRIPT_STDLIB_FILE",
         ("CATALOG_ANGULAR_FILE", "CATALOG_DOJO_FILE", "CATALOG_EMBER_FILE",
          "CATALOG_JQUERY_FILE", "CATALOG_MOCHIKIT_FILE",
          "CATALOG_PROTOTYPE_FILE", "CATALOG_YUI_FILE")),
        ("language.legacy.perl.stdlib", "PERL_STDLIB_FILE", ()),
        ("language.legacy.php.stdlib", php_stdlib_attr,
         ("CATALOG_DRUPAL_FILE", "CATALOG_PECL_FILE")),
        ("language.legacy.ruby.stdlib", "RUBY_STDLIB_FILE",
         ("CATALOG_RAILS_FILE",)),
        ("language.legacy.tcl.stdlib", "TCL_STDLIB_FILE", ()),
        ("language.legacy.css.stdlib", "CSS_STDLIB_FILE", ()),
        ("language.legacy.html.stdlib", "HTML5_STDLIB_FILE", ()),
        ("language.legacy.xul.stdlib", "XUL_STDLIB_FILE", ()),
    ]

    imported = 0
    for modname, stdlib_attr, catalog_attrs in targets:
        try:
            mod = __import__(modname, fromlist=["*"])
        except Exception as ex:
            log.info("Skipping %s: cannot import (%s)", modname, ex)
            continue
        constants = []
        if hasattr(mod, stdlib_attr):
            constants.append(getattr(mod, stdlib_attr))
        constants.extend(getattr(mod, a) for a in catalog_attrs if hasattr(mod, a))
        for constant in constants:
            try:
                mod.scan(constant)
                imported += 1
                log.info("Imported %s (%s)", constant, modname)
            except FileNotFoundError as ex:
                log.info("Skipping %s (%s): missing input %s",
                         constant, modname, ex.filename)
            except Exception as ex:
                log.warning("Failed to import %s: %s", constant, ex)

    try:
        Database.conn.close()
    except Exception:
        pass

    size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    print("Built %s (%d bytes, %d catalog groups imported)" %
          (db_path, size, imported))
    return 0


if __name__ == "__main__":
    sys.exit(main())
