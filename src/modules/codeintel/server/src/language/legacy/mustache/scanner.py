# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for Mustache source code.
CodeIntel v2's original Mustache parser has been adapted to operate with the new
CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla UDL (via SilverCity),
and walks through the token list, producing something akin to an Abstract Syntax
Tree in the end for database storage.
Completion contexts also utilize the token list.
"""

from language.common import Scope

from language.legacy.udl.udl import UDLLexer, AbstractUDLSubScanner
from language.legacy.html.scanner import HTMLScanner

class MustacheLexer(UDLLexer):
    lang = "Mustache"

class MustacheScanner(HTMLScanner):
    @property
    def udlLexer(self):
        """Implementation of AbstractUDLScanner.udlLexer."""
        return MustacheLexer()

    @property
    def tplScanner(self):
        """Implementation of AbstractUDLScanner.tplScanner."""
        if not hasattr(self, "_tplScanner"):
            self._tplScanner = HTMLMustacheScanner()
        return self._tplScanner

class HTMLMustacheScanner(AbstractUDLSubScanner):
    @property
    def namespace(self):
        """Implementation of AbstractUDLSubScanner.namespace."""
        return "Mustache"

    def prepForUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.prepForUDLTokens()."""
        # Mustache does not define functions, variables, etc., so it does not
        # need to record tokens.
        pass

    def handleUDLToken(self, **kwargs):
        """Implementation of AbstractUDLSubScanner.handleUDLToken()."""
        # Mustache does not define functions, variables, etc., so it does not
        # need to record tokens.
        pass

    def doneWithUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.doneWithUDLTokens()."""
        # Mustache does not define functions, variables, etc., so it should
        # return an empty scope
        return Scope()

    def getUDLCompletionContext(self, filename, position, env, _tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCompletionContext()."""
        return None

    def getUDLGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractScanner.getGotoDefinitionContext()."""
        return None

    def getUDLCallTipContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractScanner.getCallTipContext()."""
        return None

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
    parser = argparse.ArgumentParser(description="Scan Mustache source files")
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    scanner = MustacheScanner(HTML5_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
