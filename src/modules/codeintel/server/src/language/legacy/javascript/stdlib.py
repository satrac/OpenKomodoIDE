# Copyright 2016-2017 ActiveState, Inc. All rights reserved.

"""JavaScript and NodeJS stdlib importer."""

import os
from glob import glob

from symbols import AbstractScope
from language.common import Scope, Keyword, Variable, Module
from language.legacy.cix import Cix

# The DB file path to store JavaScript stdlib symbols under.
JAVASCRIPT_STDLIB_FILE = ":javascript:"
# The DB file path to store NodeJS stdlib symbols under.
NODEJS_STDLIB_FILE = ":nodejs:"

# The DB file paths to store legacy catalogs under.
CATALOG_ANGULAR_FILE = ":catalog_angular:"
CATALOG_DOJO_FILE = ":catalog_dojo:"
CATALOG_EMBER_FILE = ":catalog_ember:"
CATALOG_JQUERY_FILE = ":catalog_jquery:"
CATALOG_MOCHIKIT_FILE = ":catalog_mochikit:"
CATALOG_PROTOTYPE_FILE = ":catalog_prototype:"
CATALOG_XBL_FILE = ":catalog_xbl:"
CATALOG_XPCOM_FILE = ":catalog_xpcom:"
CATALOG_YUI_FILE = ":catalog_yui:"

# JavaScript's keywords to add to the stdlib.
KEYWORDS = list(set([
    # These are the keywords that are used in most JavaScript environments.
    "arguments",
    "await",
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "debugger",
    "default",
    "delete",
    "do",
    "else",
    "enum",
    "eval",
    "export",
    "extends",
    "false",
    "finally",
    "for",
    "function",
    "if",
    "implements",
    "import",
    "in",
    "instanceof",
    "interface",
    "let",
    "new",
    "null",
    "of",
    "package",
    "private",
    "protected",
    "public",
    "return",
    "static",
    "super",
    "switch",
    "this",
    "throw",
    "true",
    "try",
    "typeof",
    "var",
    "void",
    "while",
    "with",
    "yield"
] + [
    # Additional JavaScript reserved keywords.
    "Infinity",
    "NaN",
    "abstract",
    "boolean",
    "break",
    "byte",
    "case",
    "catch",
    "char",
    "class",
    "const",
    "continue",
    "debugger",
    "default",
    "delete",
    "do",
    "double",
    "else",
    "enum",
    "export",
    "extends",
    "false",
    "final",
    "finally",
    "float",
    "for",
    "function",
    "goto",
    "if",
    "implements",
    "import",
    "in",
    "instanceof",
    "int",
    "interface",
    "long",
    "native",
    "new",
    "null",
    "package",
    "private",
    "protected",
    "public",
    "return",
    "short",
    "static",
    "super",
    "switch",
    "synchronized",
    "this",
    "throw",
    "throws",
    "transient",
    "true",
    "try",
    "typeof",
    "undefined",
    "var",
    "void",
    "volatile",
    "while",
    "with"
]))

def scan(stdlib_file):
    """Imports the JavaScript and NodeJS stdlibs into the database."""
    stdlib_scope = None
    if stdlib_file == JAVASCRIPT_STDLIB_FILE:
        # Import the JavaScript stdlib.
        f = open(os.path.join(os.path.dirname(__file__), "javascript.cix"))
        scopes = list(Cix(f.read()).parse().values())
        # The stdlib is split into multiple files. Merge them into a single scope.
        stdlib_scope = scopes[0]
        for scope in scopes[1:]:
            stdlib_scope.members.update(scope.members)
        f.close()
        # The stdlib has two modules ("*" and "html5_api"). Move them into the main
        # scope.
        for k, v in list(stdlib_scope.members.items()):
            for symbol in list(v.members.values()):
                # Adjust enclosingScope field appropriately.
                if isinstance(symbol, AbstractScope):
                    symbol._enclosingScope = stdlib_scope
            for name, member in v.members.items():
                if not stdlib_scope.resolveMember(name):
                    stdlib_scope.define(member)
                else:
                    # Merge members from same symbol in "*" and "html5_api".
                    symbol = stdlib_scope.resolveMember(name)
                    symbol.members.update(member.members)
                    for submember in list(member.members.values()):
                        if isinstance(submember, AbstractScope):
                            # Adjust enclosingScope field appropriately.
                            submember._enclosingScope = symbol
            del stdlib_scope.members[k]
        # Add keywords.
        for keyword in KEYWORDS:
            stdlib_scope.define(Keyword(keyword))
    elif stdlib_file == NODEJS_STDLIB_FILE:
        # Import the NodeJS stdlib.
        f = open(os.path.join(os.path.dirname(__file__), "node.js.cix"))
        stdlib_scope = list(Cix(f.read()).parse().values())[0]
        f.close()
        # The stdlib has a single "*" module. Move it into the main scope.
        stdlib_scope.members.update(stdlib_scope.members["*"].members)
        for symbol in list(stdlib_scope.members["*"].members.values()):
            # Adjust enclosingScope field appropriately.
            if isinstance(symbol, AbstractScope):
                symbol._enclosingScope = stdlib_scope
        del stdlib_scope.members["*"]
        # Read and import from stdlib source files.
        from language.legacy.javascript.scanner import NodeJSScanner
        scanner = NodeJSScanner(None)
        for stdlib_filename in glob(os.path.join(os.path.dirname(__file__), "nodejs_stdlib_src", "*.js")):
            filename = os.path.basename(stdlib_filename)
            module = Module(filename[:-3], stdlib_scope)
            for member in list(scanner.scan(stdlib_filename).members.values()):
                member._enclosingScope = module
                module.define(member)
            if module.name in ("process", "buffer", "timers"):
                stdlib_scope.members[module.name] = module.resolveMember(module.name) # override cix variable
            elif module.name == "console":
                stdlib_scope.define(module.resolveMember("console").resolveMember("Console"))
                stdlib_scope.members["console"] = Variable("console", "Console") # override cix variable
            else:
                stdlib_scope.define(module)
        # Add keywords.
        for keyword in KEYWORDS:
            stdlib_scope.define(Keyword(keyword))
    else:
        catalogs = {
            CATALOG_ANGULAR_FILE: "angular.cix",
            CATALOG_DOJO_FILE: "dojo.cix",
            CATALOG_EMBER_FILE: "ember.cix",
            CATALOG_JQUERY_FILE: "jquery.cix",
            CATALOG_MOCHIKIT_FILE: "mochikit.cix",
            CATALOG_PROTOTYPE_FILE: "prototype.cix",
            CATALOG_XBL_FILE: "xbl.cix",
            CATALOG_XPCOM_FILE: "xpcom.cix",
            CATALOG_YUI_FILE: "yui.cix"
        }
        # Import the catalog.
        # TODO: mochikit is composed of separate files; need to handle them separately
        f = open(os.path.join(os.path.dirname(__file__), "catalogs", catalogs[stdlib_file]))
        stdlib_scope = Scope()
        stdlib_scope.members.update(list(Cix(f.read()).parse().values())[0].members.values()[0].members)
        f.close()

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)

if __name__ == "__main__":
    scan(JAVASCRIPT_STDLIB_FILE)
