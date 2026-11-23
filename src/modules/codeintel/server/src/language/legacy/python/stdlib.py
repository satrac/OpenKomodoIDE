# Copyright 2016-2017 ActiveState, Inc. All rights reserved.

"""Python 3 stdlib importer."""

import os

from symbols import AbstractScope, AbstractModule
from language.common import Keyword, Module
from language.legacy.cix import Cix

from SilverCity.Keywords import python3_keywords

# The DB file path to store Python 3 stdlib symbols under.
PYTHON3_STDLIB_FILE = ":python3:"

# The DB file path to store the Python 3 pywin32 catalog under.
CATALOG_PYTHON3_PYWIN32_FILE = ":catalog_python3_pywin32:"

# Python 3's keywords to add to the stdlib.
PYTHON3_KEYWORDS = python3_keywords.split()


def scan(stdlib_file):
    """Imports the Python 3 stdlib or the Python 3 pywin32 catalog."""
    if stdlib_file == PYTHON3_STDLIB_FILE:
        # Import the stdlib.
        f = open(os.path.join(os.path.dirname(__file__), "python3.cix"))
        stdlib_scope = list(Cix(f.read()).parse().values())[0]
        f.close()
        # The stdlib has many modules, including a "*" module. Move it into
        # the main scope.
        stdlib_scope.members.update(stdlib_scope.members["*"].members)
        for symbol in list(stdlib_scope.members["*"].members.values()):
            # Adjust enclosingScope field appropriately.
            if isinstance(symbol, AbstractScope):
                symbol._enclosingScope = stdlib_scope
        del stdlib_scope.members["*"]
        # Since some stdlib modules have "." in their names (e.g. "os.path"
        # and "xml.dom"), merge them into true Module Symbols (e.g. "os" and
        # "xml" Modules that contain "path" and "dom" sub-Modules).
        for k, v in list(stdlib_scope.members.items()):
            if "." in k and isinstance(v, AbstractModule):
                # Find or create the true Module for this stdlib module.
                name_parts = k.split(".")
                symbol = stdlib_scope.resolveMember(name_parts[0])
                if not symbol:
                    symbol = Module(name_parts[0])
                    stdlib_scope.define(symbol)
                for name_part in name_parts[1:]:
                    if not isinstance(symbol.resolveMember(name_part), AbstractScope):
                        if symbol.resolveMember(name_part):
                            # Likely from an '<import ... module="foo.bar"/>'
                            # where '<scope ilk="blob" ... name="foo.bar">'
                            # will be processed later. Delete the existing
                            # symbol to make way.
                            del symbol.members[name_part]
                        symbol = Module(name_part, symbol)
                        symbol.enclosingScope.define(symbol)
                    else:
                        symbol = symbol.resolveMember(name_part)
                # Add the stdlib module's members to the true Module and
                # delete the former.
                symbol.merge(v)
                del stdlib_scope.members[k]
        # Add keywords.
        for keyword in PYTHON3_KEYWORDS:
            stdlib_scope.define(Keyword(keyword))
    else:
        catalogs = {
            CATALOG_PYTHON3_PYWIN32_FILE: "python3-pywin32.cix"
        }
        # Import the catalog.
        f = open(os.path.join(os.path.dirname(__file__), "catalogs",
                              catalogs[stdlib_file]))
        parsed = list(Cix(f.read()).parse().values())[0]
        stdlib_scope = list(parsed.members.values())[0]
        f.close()

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)
