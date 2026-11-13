# Copyright 2016-2017 ActiveState, Inc. All rights reserved.

"""Perl stdlib importer."""

import os

from symbols import AbstractScope, AbstractModule
from language.common import Keyword, Class, Module, Namespace
from language.legacy.cix import Cix

from SilverCity.Keywords import perl_keywords

# The DB file path to store Perl stdlib symbols under.
PERL_STDLIB_FILE = ":perl:"

# Perl's keywords to add to the stdlib.
KEYWORDS = perl_keywords.split()

def scan(stdlib_file):
    """Imports the Perl stdlib into the database."""
    stdlib_files = {
        PERL_STDLIB_FILE: "perl.cix"
    }

    # Import the Perl stdlib.
    f = open(os.path.join(os.path.dirname(__file__), stdlib_files[stdlib_file]))
    stdlib_scope = list(Cix(f.read(), sep="::").parse().values())[0]
    f.close()
    # The stdlib has many modules, including a "*" module. Move it into the main
    # scope.
    stdlib_scope.members.update(stdlib_scope.members["*"].members)
    for symbol in list(stdlib_scope.members["*"].members.values()):
        # Adjust enclosingScope field appropriately.
        if isinstance(symbol, AbstractScope):
            symbol._enclosingScope = stdlib_scope
    del stdlib_scope.members["*"]
    # Since many stdlib modules have "::" in their names (e.g. "Net::FTP" and
    # "Net::HTTP"), merge them into true Module Symbols (e.g. "Net" Module that
    # contains "FTP" and "HTTP" sub-Modules).
    for k, v in list(stdlib_scope.members.items()):
        if "::" in k and isinstance(v, AbstractModule):
            # Find or create the true Module for this stdlib module.
            name_parts = k.split("::")
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

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)
