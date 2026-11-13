# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for Django source code.
CodeIntel v2's original Django parser has been adapted to operate with the new
CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla UDL (via SilverCity),
and walks through the token list, producing something akin to an Abstract Syntax
Tree in the end for database storage.
Completion contexts also utilize the token list.
"""

from symbols import AbstractKeyword, AbstractFunction
from completions import AbstractScopeCompletionContext
from language.common import Scope, Keyword, Function

from language.legacy.udl.udl import UDLLexer, is_udl_tpl_style, AbstractUDLSubScanner
from language.legacy.html.scanner import HTMLScanner

import SilverCity
from SilverCity.ScintillaConstants import SCE_UDL_TPL_DEFAULT, SCE_UDL_TPL_IDENTIFIER, SCE_UDL_TPL_OPERATOR, SCE_UDL_TPL_WORD

class DjangoLexer(UDLLexer):
    lang = "Django"

class DjangoScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "Django"

class DjangoScanner(HTMLScanner):
    @property
    def udlLexer(self):
        """Implementation of AbstractUDLScanner.udlLexer."""
        return DjangoLexer()

    @property
    def tplScanner(self):
        """Implementation of AbstractUDLScanner.tplScanner."""
        if not hasattr(self, "_tplScanner"):
            self._tplScanner = HTMLDjangoScanner()
        return self._tplScanner

django_tags = [
    "autoescape",
    "block",
    "blocktrans",
    "comment",
    "cycle",
    "debug",
    "elif",
    "else",
    "empty",   # used with for loop
    "extends",
    "filter",
    "firstof",
    "for",
    "if",
    "ifchanged",
    "ifequal",
    "ifnotequal",
    "include",
    "load",
    "lorem",
    "now",
    "regroup",
    "reversed",
    "spaceless",
    "ssi",
    "templatetag",
    "url",
    "verbatim",
    "widthratio",
    "with",

    # end tags
    "endautoescape",
    "endblock",
    "endblocktrans",
    "endcomment",
    "endfilter",
    "endfor",
    "endif",
    "endifchanged",
    "endifequal",
    "endifnotequal",
    "endspaceless",
    "endverbatim",
    "endwith",

    # Escape keywords
    "openblock",
    "closeblock",
    "openvariable",
    "closevariable",
    "openbrace",
    "closebrace",
]

django_default_filter_names = [
    # These are default filter names in django
    "add",
    "addslashes",
    "capfirst",
    "center",
    "cut",
    "date",
    "default",
    "default_if_none",
    "dictsort",
    "dictsortreversed",
    "divisibleby",
    "escape",
    "escapejs",
    "filesizeformat",
    "first",
    "floatformat",
    "force_escape",
    "get_digit",
    "iriencode",
    "join",
    "last",
    "length",
    "length_is",
    "linebreaks",
    "linebreaksbr",
    "linenumbers",
    "ljust",
    "lower",
    "make_list",
    "phone2numeric",
    "pluralize",
    "pprint",
    "random",
    "removetags",
    "rjust",
    "safe",
    "safeseq",
    "slice",
    "slugify",
    "stringformat",
    "striptags",
    "time",
    "timesince",
    "timeuntil",
    "title",
    "truncatechars",
    "truncatechars_html",
    "truncatewords",
    "truncatewords_html",
    "unordered_list",
    "upper",
    "urlencode",
    "urlize",
    "urlizetrunc",
    "wordcount",
    "wordwrap",
    "yesno",
]

class HTMLDjangoScanner(AbstractUDLSubScanner):
    def __init__(self):
        super(HTMLDjangoScanner, self).__init__(None)
        for tag_name in django_tags:
            self.builtInScope.define(Keyword(tag_name))
        for filter_name in django_default_filter_names:
            self.builtInScope.define(Function(filter_name, None, None))

    @property
    def namespace(self):
        """Implementation of AbstractUDLSubScanner.namespace."""
        return "Django"

    def prepForUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.prepForUDLTokens()."""
        # Django does not define functions, variables, etc., so it does not need
        # to record tokens.
        pass

    def handleUDLToken(self, **kwargs):
        """Implementation of AbstractUDLSubScanner.handleUDLToken()."""
        # Django does not define functions, variables, etc., so it does not need
        # to record tokens.
        pass

    def doneWithUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.doneWithUDLTokens()."""
        # Django does not define functions, variables, etc., so it should return
        # an empty scope
        return Scope()

    def getUDLCompletionContext(self, filename, position, env, _tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCompletionContext().
        Backtracks through the token list starting at the given position,
        looking for an appropriate completion context.
        """
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if is_udl_tpl_style(token["style"]):
                if token["start_line"] + 1 <= line: # UDL produces 0-based lines
                    if token["start_line"] + 1 < line: # UDL produces 0-based lines
                        # Keep all non-whitespace tokens prior to the current
                        # line.
                        if token["style"] != SCE_UDL_TPL_DEFAULT:
                            tokens.append(token)
                        elif token["end_line"] >= line and token["style"] == SCE_UDL_M_DEFAULT and "<" in token["text"]:
                            token["text"] = token["text"][:position - token["start_index"]]
                            tokens.append(token) # possible bare '<' to give completions for
                    elif token["start_column"] < column:
                        # Keep all non-whitespace tokens in the current line
                        # prior to the current position, unless the current
                        # position exists within a whitespace token.
                        if token["style"] != SCE_UDL_TPL_DEFAULT or (token["start_column"] + 1 <= column and column <= token["end_column"] + 1):
                            tokens.append(token)
                    elif token["start_column"] == column and token["style"] in (SCE_UDL_TPL_IDENTIFIER, SCE_UDL_TPL_WORD):
                        # Keep an identifier token that starts at the current
                        # position.
                        tokens.append(token)
        name_part = "" # the "already typed" part of a symbol completion

        # Pre-process the end of the token list.
        if tokens[-1]["style"] == SCE_UDL_TPL_DEFAULT:
            # If the position follows whitespace, some completions after
            # whitespace like "{% <|>" are possible, while some like
            # "{% if <|> %}" are not. This conditional just prevents the next
            # one from running.
            tokens.pop()
        elif tokens[-1]["style"] in (SCE_UDL_TPL_IDENTIFIER, SCE_UDL_TPL_WORD):
            # If the position is within a symbol name, or at the beginning of
            # a symbol name, make a note of that name since it should be
            # considered as the "already typed" part of a completion list.
            name_part = tokens[-1]["text"]
            tokens.pop()

        # Now look back through the token list and provide an appropriate
        # completion context.
        if tokens[-1]["style"] == SCE_UDL_TPL_OPERATOR:
            if tokens[-1]["text"] == "{%":
                # A "{% foo"-type of expression should produce keyword
                # completions.
                return DjangoScopeCompletionContext(self.builtInScope, name_part, AbstractKeyword)
            elif tokens[-1]["text"].endswith("|"):
                # A "foo | bar"-type of expression should produce filter
                # completions.
                return DjangoScopeCompletionContext(self.builtInScope, name_part, AbstractFunction)

        return None

    def getUDLGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractScanner.getGotoDefinitionContext()."""
        return None

    def getUDLCallTipContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractScanner.getCallTipContext()."""
        return None # TODO: ?

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
    parser = argparse.ArgumentParser(description="Scan Django source files")
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    scanner = DjangoScanner(HTML5_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
