# Copyright 2017 ActiveState, Inc. All rights reserved.

"""PHP stdlib importer."""

import os

from symbols import AbstractScope, AbstractVariable, AbstractStruct, AbstractClass, AbstractModule
from language.common import Keyword, Variable, Struct, Module
from language.legacy.cix import Cix

# The DB file path to store PHP 5 stdlib symbols under.
PHP_5_STDLIB_FILE = ":php5:"
# The DB file path to store PHP 7 stdlib symbols under.
PHP_7_STDLIB_FILE = ":php7:"

# The DB file paths to store legacy catalogs under.
CATALOG_DRUPAL_FILE = ":catalog_drupal:"
CATALOG_PECL_FILE = ":catalog_pecl:"
CATALOG_PHPUNIT_FILE = ":catalog_phpunit:"

# PHP's keywords to add to the stdlib.
KEYWORDS = [
    # new to php 5.5
    "finally", "yield",
    # new to php 5.4
    "insteadof", "trait",
    # new to php 5.3
    "e_deprecated", "e_user_deprecated", "php_maxpathlen",
    # existed in php4
    "bool", "boolean", "catch", "define", "double", "false", "float",
    "int", "integer", "null", "object", "parent", "real",
    "self", "string", "this", "true", "virtual",
    # new to php5
    "abstract", "final", "implements", "instanceof", "interface",
    "public", "private", "protected", "throw", "try",
    # http://www.php.net/manual/en/reserved.php#reserved.keywords
    "__file__", "__line__", "_function_", "_class_",
    "and", "array", "as", "break", "case", "cfunction", "class",
    "const", "continue", "declare", "default", "die", "do", "echo",
    "else", "elseif", "empty", "enddeclare", "endfor", "endforeach",
    "endif", "endswitch", "endwhile", "eval", "exit", "extends",
    "for", "foreach", "function", "global", "if", "include",
    "include_once", "isset", "list", "new", "old_function", "or",
    "print", "require", "require_once", "return", "static",
    "switch", "unset", "use", "var", "while", "xor",
    # new to php 5.3
    "__dir__", "__namespace__", "goto", "namespace",
]

def scan(stdlib_file):
    """Imports the PHP stdlibs into the database."""
    stdlib_files = {
        PHP_5_STDLIB_FILE: "php5.cix",
        PHP_7_STDLIB_FILE: "php7.cix",
        CATALOG_DRUPAL_FILE: os.path.join("catalogs", "drupal.cix"),
        CATALOG_PECL_FILE: os.path.join("catalogs", "pecl.cix"),
        CATALOG_PHPUNIT_FILE: os.path.join("catalogs", "phpunit.cix")
    }

    # Import the stdlib.
    f = open(os.path.join(os.path.dirname(__file__), stdlib_files[stdlib_file]))
    stdlib_scope = list(Cix(f.read()).parse().values())[0]
    f.close()
    # The stdlib has a single "*" blob. Move it into the main scope.
    stdlib_scope.members.update(stdlib_scope.members["*"].members)
    for symbol in list(stdlib_scope.members["*"].members.values()):
        # Adjust enclosingScope field appropriately.
        if isinstance(symbol, AbstractScope):
            symbol._enclosingScope = stdlib_scope
    del stdlib_scope.members["*"]
    # Ensure stdlib variables have a '$' prefix.
    for symbol in list(stdlib_scope.members.values()):
        if isinstance(symbol, AbstractVariable):
            stdlib_scope.members["$" + symbol.name] = Variable("$" + symbol.name, symbol.type)
            del stdlib_scope.members[symbol.name]
        elif isinstance(symbol, AbstractStruct) and not isinstance(symbol, AbstractClass):
            stdlib_scope.members["$" + symbol.name] = Struct("$" + symbol.name, symbol.type, symbol.enclosingScope)
            stdlib_scope.members["$" + symbol.name].members.update(symbol.members)
            del stdlib_scope.members[symbol.name]

    if stdlib_file == PHP_5_STDLIB_FILE or stdlib_file == PHP_7_STDLIB_FILE:
        # Add keywords.
        for keyword in KEYWORDS:
            stdlib_scope.define(Keyword(keyword))

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)

if __name__ == "__main__":
    scan(PHP_7_STDLIB_FILE)
