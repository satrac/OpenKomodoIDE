# Copyright 2016-2017 ActiveState, Inc. All rights reserved.

"""Ruby stdlib importer."""

import os

from symbols import AbstractScope, AbstractModule
from language.common import Scope, Keyword, Module
from language.legacy.cix import Cix

from SilverCity.Keywords import ruby_keywords

# The DB file path to store Ruby stdlib symbols under.
RUBY_STDLIB_FILE = ":ruby:"

# The DB file paths to store legacy catalogs under.
CATALOG_RAILS_FILE = ":catalog_rails:"

# Ruby's keywords to add to the stdlib.
KEYWORDS = ruby_keywords.split()

def scan(stdlib_file):
    """Imports the Ruby stdlib into the database."""
    if stdlib_file == RUBY_STDLIB_FILE:
        # Import the Ruby stdlib.
        f = open(os.path.join(os.path.dirname(__file__), "ruby.cix"))
        stdlib_scope = list(Cix(f.read()).parse().values())[0]
        f.close()
        # The stdlib has many modules, including a "*" module. Move it into the main
        # scope.
        stdlib_scope.members.update(stdlib_scope.members["*"].members)
        for symbol in list(stdlib_scope.members["*"].members.values()):
            # Adjust enclosingScope field appropriately.
            if isinstance(symbol, AbstractScope):
                symbol._enclosingScope = stdlib_scope
        del stdlib_scope.members["*"]
        # Since many stdlib modules have "/" in their names (e.g. "net/ftp" and
        # "net/http"), merge them into true Module Symbols (e.g. "net" Module that
        # contains "ftp" and "http" sub-Modules).
        for k, v in list(stdlib_scope.members.items()):
            if "/" in k and isinstance(v, AbstractModule):
                # Find or create the true Module for this stdlib module.
                name_parts = k.split("/")
                #if k.startswith("net/http"):
                #    # The "net/http" module has additional sub-files, but they all
                #    # exist in the same "net/http" namespace; do not create
                #    # sub-namespaces.
                #    name_parts = ["net", "http"]
                symbol = stdlib_scope.resolveMember(name_parts[0])
                if not symbol:
                    symbol = Module(name_parts[0])
                    stdlib_scope.define(symbol)
                for name_part in name_parts[1:]:
                    if not symbol.resolveMember(name_part):
                        symbol = Module(name_part, symbol)
                        symbol.enclosingScope.define(symbol)
                    else:
                        symbol = symbol.resolveMember(name_part)
                # Add the stdlib module's members to the true Module and delete the
                # former.
                symbol.merge(v)
                del stdlib_scope.members[k]
        # Add keywords.
        for keyword in KEYWORDS:
            stdlib_scope.define(Keyword(keyword))
    elif stdlib_file == CATALOG_RAILS_FILE:
        # Import the catalog.
        f = open(os.path.join(os.path.dirname(__file__), "catalogs", "rails.cix"))
        stdlib_scope = Scope()
        parsed = list(Cix(f.read()).parse().values())[0]
        stdlib_scope.members.update(list(parsed.members.values())[0].members)
        f.close()
    else:
        raise RuntimeError("Unknown Ruby stdlib or catalog file: '%s'" % stdlib_file)

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)

if __name__ == "__main__":
    scan(RUBY_STDLIB_FILE)
