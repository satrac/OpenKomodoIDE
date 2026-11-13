# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for JSX source code.
CodeIntel v2's original JSX parser has been adapted to operate with the new
CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla UDL (via SilverCity),
and walks through the token list, producing something akin to an Abstract Syntax
Tree in the end for database storage.
Completion contexts also utilize the token list.
"""

from symbols import AbstractNamespace
from language.common import Scope, Namespace

from language.legacy.udl.udl import UDLLexer
from language.legacy.html.scanner import HTMLScanner

class JSXLexer(UDLLexer):
    lang = "JSX"

class JSXScanner(HTMLScanner):
    @property
    def udlLexer(self):
        """Implementation of AbstractUDLScanner.udlLexer."""
        return JSXLexer()

    def scan(self, filename, env={}):
        """Implementation of AbstractScanner.scan().
        For compatibility with CodeIntel v2, JavaScript symbols should be in the
        top-level scope.
        Since HTMLScanner (and by extension AbstractUDLScanner) produce a scope
        like:
            enclosingScope (HTML5_STDLIB_FILE)
                scope (JSX file)
                    JavaScript (namespace) <-- enclosingScope (JAVASCRIPT_STDLIB_FILE)
                    CSS (namespace)
        Create and return a new scope that looks like:
            enclosingScope (JAVASCRIPT_STDLIB_FILE)
                scope (JSX file)
                    JS symbols
                    CSS (namespace)
                    HTML (namespace) <-- enclosingScope (HTML5_STDLIB_FILE)
        """
        scope = super(JSXScanner, self).scan(filename, env)
        js_scope = Scope(scope.members["JavaScript"].enclosingScope)
        for member in list(scope.members["JavaScript"].members.values()):
            js_scope.define(member)
        js_scope.define(scope.members["CSS"])
        html_scope = Namespace(self.namespace, scope.enclosingScope)
        for member in list(scope.members.values()):
            if not isinstance(member, AbstractNamespace):
                html_scope.define(member)
        js_scope.define(html_scope)
        return js_scope

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    from language.legacy.html.stdlib import HTML5_STDLIB_FILE
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    parser = argparse.ArgumentParser(description="Scan JSX source files")
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    scanner = JSXScanner(HTML5_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
