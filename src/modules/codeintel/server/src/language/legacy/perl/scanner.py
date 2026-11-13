# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for Perl source code.
CodeIntel v2's original Perl parser has been adapted to operate with the new
CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla Perl lexer (via
SilverCity), and walks through the token list, producing something akin to an
Abstract Syntax Tree in the end for database storage.
Completion contexts also utilize the token list.
"""

import logging
import os

from symbols import AbstractVariable, AbstractClass, AbstractFunction, AbstractModule
from calltips import CallTipContext
from completions import AbstractMemberCompletionContext, AbstractScopeCompletionContext
from goto_definition import GotoDefinitionContext
from find_references import AbstractFindReferencesContext
from language.common import Scope, Import, AbstractScanner
from language.legacy.perl import perl_lexer, perl_parser
from language.legacy.perl.import_resolver import PerlImportResolver

from db.model.helpers import fileExists, fetchSymbolInFile, fetchSymbolsInFile, fetchFilesInDirectory, fetchAllFilesInDirectory

from SilverCity import ScintillaConstants
from SilverCity.ScintillaConstants import (
    SCE_PL_DEFAULT, SCE_PL_ERROR, SCE_PL_COMMENTLINE, SCE_PL_POD,
    SCE_PL_NUMBER, SCE_PL_WORD, SCE_PL_STRING, SCE_PL_CHARACTER,
    SCE_PL_PUNCTUATION, SCE_PL_PREPROCESSOR, SCE_PL_OPERATOR,
    SCE_PL_IDENTIFIER, SCE_PL_SCALAR, SCE_PL_ARRAY, SCE_PL_HASH,
    SCE_PL_SYMBOLTABLE, SCE_PL_VARIABLE_INDEXER, SCE_PL_REGEX,
    SCE_PL_REGSUBST, SCE_PL_LONGQUOTE, SCE_PL_BACKTICKS, SCE_PL_DATASECTION,
    SCE_PL_HERE_DELIM, SCE_PL_HERE_Q, SCE_PL_HERE_QQ, SCE_PL_HERE_QX,
    SCE_PL_STRING_Q, SCE_PL_STRING_QQ, SCE_PL_STRING_QX, SCE_PL_STRING_QR,
    SCE_PL_STRING_QW, SCE_PL_POD_VERB, SCE_PL_SUB_PROTOTYPE,
    SCE_PL_FORMAT_IDENT, SCE_PL_FORMAT, SCE_PL_STRING_VAR, SCE_PL_XLAT,
    SCE_PL_REGEX_VAR, SCE_PL_REGSUBST_VAR, SCE_PL_BACKTICKS_VAR,
    SCE_PL_HERE_QQ_VAR, SCE_PL_HERE_QX_VAR, SCE_PL_STRING_QQ_VAR,
    SCE_PL_STRING_QX_VAR, SCE_PL_STRING_QR_VAR,
)

log = logging.getLogger("codeintel.perl")
#log.setLevel(logging.DEBUG)

class PerlScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "Perl"

class PerlMemberCompletionContext(AbstractMemberCompletionContext):
    """Implementation of AbstractMemberCompletionContext."""
    @property
    def language(self):
        return "Perl"

class PerlVariableMemberCompletionContext(PerlMemberCompletionContext):
    """PerlMemberCompletionContext for variables that correctly handles variable
    prefixes.
    """
    def __init__(self, scope, symbol_name, name_part="", symbol_type=None, import_resolver=None, syntax_description=None):
        super(PerlVariableMemberCompletionContext, self).__init__(scope, symbol_name[1:], name_part, None, import_resolver, syntax_description)
        self._post_symbol_type = symbol_type # apply after the fact

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions()."""
        completions = super(PerlVariableMemberCompletionContext, self).getCompletions()
        for name, symbol in list(completions.members.items()):
            if isinstance(symbol, (AbstractClass, AbstractModule)):
                continue
            elif isinstance(symbol, self._post_symbol_type) and name[0] in "$@%":
                completions.members[name[1:]] = symbol
            if completions.members[name] == symbol:
                # A variable '$foo' may overwrite a 'foo' function. If the 'foo'
                # function comes later, don't accidentally delete the new
                # variable.
                del completions.members[name]
        return completions

class PerlFindReferencesContext(AbstractFindReferencesContext):
    """Implementation of AbstractFindReferencesContext."""
    @property
    def projectFiles(self):
        """Implementation of AbstractFindReferencesContext.projectFiles."""
        filenames = []
        for dirname in self.env.get("PERL5LIB", "").split(os.pathsep):
            if not dirname:
                continue
            for filename in fetchAllFilesInDirectory(dirname, (".pm", ".pl")):
                filenames.append(filename)
        return filenames

class PerlScanner(AbstractScanner):
    def __init__(self, stdlib_file):
        """Overrides the default scanner __init__() to load Perl's stdlib, but
        without AbstractModules.
        """
        self._builtInScope = Scope()
        if fileExists(stdlib_file):
            for symbol in fetchSymbolsInFile(stdlib_file, exclude=AbstractModule):
                self._builtInScope.define(symbol)
        else:
            log.error("stdlib file '%s' does not exist", stdlib_file)

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

        try:
            self.tokenizer = perl_lexer.PerlLexer(self.content, False)
        except UnicodeDecodeError:
            self.tokenizer = perl_lexer.PerlLexer(self.content.decode(errors="ignore"), False)
        parser = perl_parser.Parser(self.tokenizer, provide_full_docs=False)
        parser.parse()

        return parser.moduleInfo.toAbstractSymbol(self.builtInScope)

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
                    if token["style"] != SCE_PL_DEFAULT:
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if token["style"] != SCE_PL_DEFAULT or (token["start_column"] + 1 <= column and column <= token["end_column"] + 1):
                        tokens.append(token)
                elif token["start_column"] == column and token["style"] == SCE_PL_IDENTIFIER:
                    # Keep an identifier token that starts at the current
                    # position.
                    tokens.append(token)
        leading_whitespace = False
        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in PERL5LIB should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PERL5LIB" in env:
                dirs = env["PERL5LIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PERL5LIB"] = os.pathsep.join(dirs)
        import_resolver = PerlImportResolver(filename, env)
        name_part = "" # the "already typed" part of a symbol completion

        if len(tokens) == 0:
            # If the position is at the beginning of the buffer, provide scope
            # completions.
            return PerlScopeCompletionContext(scope, import_resolver=import_resolver)

        # Pre-process the end of the token list.
        if tokens[-1]["style"] == SCE_PL_DEFAULT:
            # If the position follows whitespace, make a note since some
            # completions after whitespace like "if <|>" are possible, while
            # some like "if<|>" are not.
            leading_whitespace = True
            tokens.pop()
            if len(tokens) == 0:
                return PerlScopeCompletionContext(scope, import_resolver=import_resolver)
        elif tokens[-1]["style"] == SCE_PL_IDENTIFIER:
            # If the position is within a symbol name, or at the beginning of
            # a symbol name, make a note of that name since it should be
            # considered as the "already typed" part of a completion list.
            name_part = tokens[-1]["text"]
            tokens.pop()
            if len(tokens) == 0:
                return PerlScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif tokens[-1]["style"] in (SCE_PL_SCALAR, SCE_PL_ARRAY, SCE_PL_HASH, SCE_PL_SYMBOLTABLE):
            # If the position is within a variable, it's part of either a
            # "[$@%*]foo"- or "[$@%*]foo::bar"-type of expression.
            if "::" in tokens[-1]["text"]:
                if column < tokens[-1]["end_column"]:
                    # Since the Perl tokenizer considers an expression like
                    # "$foo::bar::baz" a single token, it is possible the
                    # position comes before the last member (e.g. "$<|>foo::bar"
                    # or "$foo::<|>bar::baz"). If so, strip out the trailing
                    # members.
                    i = tokens[-1]["text"].find("::", column - tokens[-1]["start_column"])
                    if i != -1:
                        tokens[-1]["text"] = tokens[-1]["text"][:i]
                symbol_name = "::".join(tokens[-1]["text"].split("::")[:-1])
                name_part = tokens[-1]["text"].split("::")[-1]
                if symbol_name:
                    # A "$foo::bar"-, "@foo::bar"-, "%foo::bar"-, or
                    # "*foo::bar"-type of expression should provide member
                    # completions.
                    return PerlVariableMemberCompletionContext(scope, symbol_name, name_part, symbol_type=AbstractVariable, import_resolver=import_resolver)
            else:
                name_part = tokens[-1]["text"]
            return PerlScopeCompletionContext(scope, name_part, symbol_type=AbstractVariable, import_resolver=import_resolver)

        # Now look back through the token list and provide an appropriate
        # completion context.
        if tokens[-1]["style"] == SCE_PL_OPERATOR and tokens[-1]["text"] in ("->", "::"):
            # If the first significant token behind the position is a '->' or
            # '::', it's probably part of a larger "foo->bar"- or
            # "foo::bar"-type of expression.
            i = len(tokens) - 1
            while i >= 1:
                # Skip back through "name->" token pairs in order to construct
                # the full symbol name.
                if tokens[i]["style"] != SCE_PL_OPERATOR or tokens[i]["text"] not in ("->", "::") or tokens[i - 1]["style"] not in (SCE_PL_IDENTIFIER, SCE_PL_SCALAR):
                    break
                i -= 2
            symbol_name = "".join([token["text"] for token in tokens[i+1:-1]])
            if not symbol_name:
                # The original '->' or '::' token came after an unknown symbol
                # and should not provide completions.
                return None
            if tokens[i]["text"] in ("use", "require", "no"):
                # A "use foo::bar"- "require foo::bar"-, or "no foo::bar"-type
                # of expression should provide member module completions.
                scope.define(Import("%s::*" % symbol_name, symbol_name))
                return PerlMemberCompletionContext(scope, symbol_name, name_part, AbstractModule, import_resolver=import_resolver)
            # A "foo->bar"- or "foo::bar"-type of expression should provide
            # member completions.
            return PerlMemberCompletionContext(scope, symbol_name, name_part, symbol_type=(AbstractModule, AbstractClass, AbstractFunction), import_resolver=import_resolver)
        elif tokens[-1]["style"] == SCE_PL_OPERATOR and tokens[-1]["text"] in ("(",):
            # If the first significant token behind the position is a '(',
            # it's probably part of a typical expression, which should provide
            # scope completions.
            return PerlScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif leading_whitespace or name_part:
            if tokens[-1]["style"] == SCE_PL_WORD:
                if tokens[-1]["text"] in ("package", "sub"):
                    # A "package foo"- or "sub foo"-type of expression should
                    # not provide completions.
                    return None
                elif tokens[-1]["text"] in ("use", "require", "no"):
                    # A "use foo"-, "require foo"-, or "no foo"-type of
                    # expression should provide import completions.
                    scope.define(Import("*", None)) # for import resolver
                    return PerlScopeCompletionContext(scope, name_part, symbol_type=AbstractModule, import_resolver=import_resolver)
            elif tokens[-1]["style"] == SCE_PL_OPERATOR and tokens[-1]["text"] == ".":
                # A "foo.bar"-type of expression should not provide completions.
                return None
            elif tokens[-1]["style"] in (SCE_PL_IDENTIFIER, SCE_PL_SCALAR, SCE_PL_ARRAY, SCE_PL_HASH, SCE_PL_SYMBOLTABLE):
                # A "foo bar"-, "$foo bar"-, "@foo:: bar"-, or "%foo bar"-type
                # of expression should not provide completions.
                return None
            # If there is no significant token immediately behind the position,
            # it's probably part of a typical expression, which should provide
            # scope completions.
            return PerlScopeCompletionContext(scope, name_part, import_resolver=import_resolver)

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

        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in PERL5LIB should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PERL5LIB" in env:
                dirs = env["PERL5LIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PERL5LIB"] = os.pathsep.join(dirs)
        import_resolver = PerlImportResolver(filename, env)

        for i in range(len(self.tokenizer.q)):
            token = self.tokenizer.q[i]
            if token["start_line"] <= line and line <= token["end_line"] and token["start_column"] <= column and column <= token["end_column"]:
                if token["style"] in (SCE_PL_IDENTIFIER, SCE_PL_SCALAR, SCE_PL_ARRAY, SCE_PL_HASH, SCE_PL_SYMBOLTABLE):
                    # If the entity at the position is an identifier or
                    # variable, retrieve the fully-qualified name up to and
                    # including the position.
                    j = i + 1
                    while i > 0:
                        if self.tokenizer.q[i - 1]["style"] == SCE_PL_OPERATOR and self.tokenizer.q[i - 1]["text"] in ("->", "::"):
                            i -= 2
                        else:
                            break
                    symbol_name = "".join([token["text"] for token in self.tokenizer.q[i:j]])
                    return GotoDefinitionContext(scope, symbol_name, import_resolver=import_resolver)
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
                    if token["style"] != SCE_PL_DEFAULT:
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if token["style"] != SCE_PL_DEFAULT or (token["start_column"] + 1 <= column and column <= token["end_column"] + 1):
                        tokens.append(token)

        # Now look back through the token list for a function call and provide
        # its call tip context.
        i = len(tokens) - 1
        if i < 0:
            return None
        if not (tokens[i]["style"] == SCE_PL_DEFAULT and len(tokens) > 1 and tokens[i - 1]["style"] in (SCE_PL_WORD, SCE_PL_IDENTIFIER)):
            paren_level = -1
            while i > 0:
                if tokens[i]["style"] == SCE_PL_OPERATOR:
                    paren_level -= tokens[i]["text"].count(")")
                    paren_level += tokens[i]["text"].count("(")
                    if paren_level == 0:
                        i -= 1
                        break
                i -= 1
            if paren_level != 0 or tokens[i]["style"] not in (SCE_PL_WORD, SCE_PL_IDENTIFIER):
                return None # no function call = no call tip
        else:
            # Perl makes function call parentheses optional.
            i -= 1

        # Retrieve the fully-qualified function name.
        j = i + 1
        while i > 0:
            if tokens[i - 1]["style"] == SCE_PL_OPERATOR and tokens[i - 1]["text"] in ("->", "::"):
                i -= 2
            else:
                break
        if tokens[i]["style"] == SCE_PL_WORD and tokens[i]["text"] in ("if", "else", "elsif", "while", "for", "sub", "unless", "my", "our", "package"):
            return None # no call tip for keywords
        elif i > 0 and tokens[i - 1]["style"] == SCE_PL_WORD and tokens[i - 1]["text"] == "sub":
            return None # no call tip for function declaration
        symbol_name = "".join([token["text"] for token in tokens[i:j]])

        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in PERL5LIB should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PERL5LIB" in env:
                dirs = env["PERL5LIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PERL5LIB"] = os.pathsep.join(dirs)
        import_resolver = PerlImportResolver(filename, env)
        return CallTipContext(scope, symbol_name, import_resolver=import_resolver)

    def getFindReferencesContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getFindReferencesContext()."""
        context = self.getGotoDefinitionContext(filename, position, env)
        if not context:
            return None
        # For unit tests, any relative paths in PERL5LIB should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PERL5LIB" in env:
                dirs = env["PERL5LIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PERL5LIB"] = os.pathsep.join(dirs)
        import_resolver = PerlImportResolver(filename, env)
        return PerlFindReferencesContext(context.scope, context.symbol_name, self, env, import_resolver=import_resolver)

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    from language.legacy.perl.stdlib import PERL_STDLIB_FILE
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    parser = argparse.ArgumentParser(description="Scan Perl source files")
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    scanner = PerlScanner(PERL_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
