# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for RHTML source code.
CodeIntel v2's original RHTML parser has been adapted to operate with the new
CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla UDL (via SilverCity),
and walks through the token list, producing something akin to an Abstract Syntax
Tree in the end for database storage.
Completion contexts also utilize the token list.
"""

from language.legacy.udl.udl import UDLLexer
from language.legacy.html.scanner import HTMLScanner
from language.legacy.ruby.scanner import HTMLRubyScanner

class RHTMLLexer(UDLLexer):
    lang = "RHTML"

class RHTMLScanner(HTMLScanner):
    @property
    def udlLexer(self):
        return RHTMLLexer()

    @property
    def sslScanner(self):
        if not hasattr(self, "_sslScanner"):
            self._sslScanner = HTMLRubyScanner()
        return self._sslScanner

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
    parser = argparse.ArgumentParser(description="Scan RHTML source files")
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    scanner = RHTMLScanner(HTML5_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
