#

"""TODO: document
"""

import logging
import os

from calltips import CallTipContext
from completions import AbstractMemberCompletionContext, AbstractScopeCompletionContext
from goto_definition import GotoDefinitionContext
from language.common import AbstractScanner
from language.legacy.tcl import tcl_lexer, tcl_parser

from SilverCity.ScintillaConstants import SCE_TCL_DEFAULT, SCE_TCL_IDENTIFIER, SCE_TCL_OPERATOR, SCE_TCL_VARIABLE

log = logging.getLogger("codeintel.tcl")
#log.setLevel(logging.DEBUG)

class TclScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "Tcl"

class TclMemberCompletionContext(AbstractMemberCompletionContext):
    """Implementation of AbstractMemberCompletionContext."""
    @property
    def language(self):
        return "Tcl"

class TclScanner(AbstractScanner):
    def scan(self, filename, env={}):
        """Implementation of AbstractScanner.scan().
        For testing purposes, when filename is None, stdin is scanned.
        """
        if not isinstance(filename, (str, None.__class__)):
            raise TypeError("filename must be a string ('%s' received)" % filename.__class__.__name__)
        if not isinstance(env, dict):
            raise TypeError("env must be a dictionary ('%s' received)" % env.__class__.__name__)

        if filename is not None:
            if os.path.exists(filename):
                try:
                    self.content = self.readFile(filename)
                except IOError:
                    self.content = filename
                    filename = ":untitled:"
            else:
                self.content = filename
                filename = ":untitled:"
        else:
            self.content = __import__("sys").stdin.read()
            filename = ":stdin:"

        self.tokenizer = tcl_lexer.TclLexer(self.content)
        parser = tcl_parser.Parser(self.tokenizer, "Tcl")
        parse_tree = parser.parse()

        return parse_tree.toAbstractSymbol(self._builtInScope)

    def resolveImport(self, import_symbol, scope, filename, env={}):
        """Implementation of AbstractScanner.resolveImport()."""
        pass

    def getCompletionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCompletionContext().
        Backtracks through the token list starting at the given position,
        looking for an appropriate completion context.
        """
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in self.tokenizer.q:
            if token["start_line"] <= line:
                if token["start_line"] < line:
                    if token["style"] != SCE_TCL_DEFAULT:
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if token["style"] != SCE_TCL_DEFAULT or (token["start_column"] + 1 <= column and column <= token["end_column"] + 1):
                        tokens.append(token)
                elif token["start_column"] == column and token["style"] == SCE_TCL_IDENTIFIER:
                    # Keep an identifier token that starts at the current
                    # position.
                    tokens.append(token)
        leading_whitespace = False
        scope = scope.resolveScope(line)
        name_part = "" # the "already typed" part of a symbol completion

        if len(tokens) == 0:
            # If the position is at the beginning of the buffer, provide scope
            # completions.
            return TclScopeCompletionContext(scope)

        # Pre-process the end of the token list.
        if tokens[-1]["style"] == SCE_TCL_DEFAULT:
            # If the position follows whitespace, make a note since some
            # completions after whitespace like "package <|>" are possible,
            # while some like "package<|>" are not.
            leading_whitespace = True
            tokens.pop()
            if len(tokens) == 0:
                return TclScopeCompletionContext(scope)
        elif tokens[-1]["style"] == SCE_TCL_IDENTIFIER:
            # If the position is within a symbol name, or at the beginning of
            # a symbol name, make a note of that name since it should be
            # considered as the "already typed" part of a completion list.
            name_part = tokens[-1]["text"]
            tokens.pop()
            if len(tokens) == 0:
                return TclScopeCompletionContext(scope, name_part)

        # Now look back through the token list and provide an appropriate
        # completion context.
#        if tokens[-1]["style"] == SCE_PL_OPERATOR and tokens[-1]["text"] in ("->", "::"):
#            # If the first significant token behind the position is a '.' or
#            # '::', it's probably part of a larger "foo.bar."- or "foo::"-type
#            # of expression.
#            i = len(tokens) - 1
#            while i >= 1:
#                # Skip back through "name." token pairs in order to construct
#                # the full symbol name.
#                if tokens[i]["style"] != SCE_PL_OPERATOR or tokens[i]["text"] not in ("->", "::") or tokens[i - 1]["style"] != SCE_PL_IDENTIFIER:
#                    break # TODO: CLOSE_PAREN and function return values
#                i -= 2
#            symbol_name = "".join([token["text"] for token in tokens[i+1:-1]])
#            # The expression seems to be a "foo.bar."-type of expression.
#            return TclMemberCompletionContext(scope, symbol_name, name_part)
#        elif leading_whitespace or name_part:
#            # If there is no significant token immediately behind the
#            # position, it's an individual part of a typical expression.
#            return TclScopeCompletionContext(scope, name_part)
#        elif tokens[-1]["style"] == SCE_PL_OPERATOR and tokens[-1]["text"] in ("(",):
#            # If the first significant token behind the position is an operator,
#            # it's an individual part of a typical expression.
#            return TclScopeCompletionContext(scope, name_part)

        return None

    def getGotoDefinitionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getGotoDefinitionContext().
        Backtracks through the token list starting at the given position,
        looking for an appropriate goto definition context.
        """
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        for i in range(len(self.tokenizer.q)):
            token = self.tokenizer.q[i]
            if token["start_line"] <= line and line <= token["end_line"] and token["start_column"] <= column and column <= token["end_column"]:
                if token["style"] == SCE_TCL_IDENTIFIER:
                    # If the entity at the position is an identifier, retrieve
                    # the fully-qualified name up to and including the position.
                    j = i + 1
                    while i > 0:
                        if self.tokenizer.q[i - 1]["style"] == SCE_TCL_OPERATOR and self.tokenizer.q[i - 1]["text"] == "::":
                            i -= 2
                        else:
                            break
                    symbol_name = "".join([token["text"] for token in self.tokenizer.q[i:j]])
                    return GotoDefinitionContext(scope, symbol_name) # TODO: import_resolver
                elif token["style"] == SCE_TCL_VARIABLE:
                    symbol_name = token["text"].lstrip("$")
                    return GotoDefinitionContext(scope, symbol_name) # TODO: import_resolver
                break
            elif token["start_line"] > line:
                break

        return None

    def getCallTipContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCallTipContext()."""
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in self.tokenizer.q:
            if token["start_line"] <= line:
                if token["start_line"] < line:
                    if token["style"] != SCE_TCL_DEFAULT:
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if token["style"] != SCE_TCL_DEFAULT or (token["start_column"] + 1 <= column and column <= token["end_column"] + 1):
                        tokens.append(token)

        # Now look back through the token list for a function call and provide
        # its call tip context.
        i = len(tokens) - 1
        if i < 0:
            return None
        if not (tokens[i]["style"] == SCE_TCL_DEFAULT and len(tokens) > 1 and tokens[i - 1]["style"] == SCE_TCL_IDENTIFIER):
            # TODO: for now, require function calls to be whitespace after
            # identifiers.
            return None
        i -= 1

        # Retrieve the fully-qualified function name.
        j = i + 1
        while i > 0:
            if tokens[i - 1]["style"] == SCE_TCL_OPERATOR and tokens[i - 1]["text"] == "::":
                i -= 2
            else:
                break
        symbol_name = "".join([token["text"] for token in tokens[i:j]])

        scope = scope.resolveScope(line)
        import_resolver = None # TODO:
        return CallTipContext(scope, symbol_name, import_resolver=import_resolver)

    def getFindReferencesContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getFindReferencesContext()."""
        return None # TODO:

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    parser = argparse.ArgumentParser(description="Scan Tcl source files")
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    scanner = TclScanner()
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
