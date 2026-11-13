# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for Ruby source code.
CodeIntel v2's original Ruby parser has been adapted to operate with the new
CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla Ruby lexer (via
SilverCity), and walks through the token list, producing something akin to an
Abstract Syntax Tree in the end for database storage.
Completion contexts also utilize the token list.
"""

import logging
import os
import re
import sys

from symbols import (AbstractScope,
                     AbstractGlobalVariable, AbstractInstanceVariable, AbstractClassVariable,
                     AbstractClass,
                     AbstractModule)
from calltips import CallTipContext
from completions import AbstractMemberCompletionContext, AbstractScopeCompletionContext, ApproximateSymbolContext
from goto_definition import GotoDefinitionContext
from find_references import AbstractFindReferencesContext
from language.common import Scope, Constructor, Import, AbstractScanner, FilenameSyntaxDescription, SymbolResolver
from language.legacy.ruby import ruby_lexer, ruby_parser
from language.legacy.ruby.import_resolver import RubyImportResolver
from language.legacy.udl import AbstractUDLSubScanner

from db.model.helpers import fileExists, fetchSymbolsInFile, fetchSymbolsInDirectories, fetchAllFilesInDirectory

import SilverCity
from SilverCity.Lexer import Lexer
from SilverCity import ScintillaConstants
from SilverCity.ScintillaConstants import (
    SCLEX_RUBY, SCE_RB_DEFAULT, SCE_RB_COMMENTLINE,
    SCE_RB_REGEX, SCE_RB_IDENTIFIER, SCE_RB_WORD, SCE_RB_OPERATOR,
    SCE_RB_CLASSNAME, SCE_RB_DEFNAME, SCE_RB_MODULE_NAME,
    SCE_RB_GLOBAL, SCE_RB_INSTANCE_VAR, SCE_RB_CLASS_VAR,
    SCE_RB_NUMBER, SCE_RB_STRING, SCE_RB_CHARACTER,
    SCE_UDL_M_OPERATOR, SCE_UDL_SSL_DEFAULT, SCE_UDL_SSL_IDENTIFIER,
    SCE_UDL_SSL_OPERATOR, SCE_UDL_SSL_VARIABLE, SCE_UDL_SSL_WORD,
    SCE_UDL_SSL_STRING, SCE_UDL_SSL_NUMBER, SCE_UDL_TPL_OPERATOR
)
from SilverCity.Keywords import ruby_keywords

log = logging.getLogger("codeintel.ruby")
#log.setLevel(logging.DEBUG)

class RubyLexer(Lexer):
    def __init__(self):
        self._properties = SilverCity.PropertySet()
        self._lexer = SilverCity.find_lexer_module_by_id(SCLEX_RUBY)
        self._keyword_lists = [
            SilverCity.WordList(ruby_keywords)
        ]

class RubyScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "Ruby"

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions(), but
        auto-imports all appropriate symbols into the current scope first.
        This is particularly helpful for Ruby on Rails partials and other source
        files where symbols are auto-magically imported.
        """
        if self.import_resolver:
            scope = self.scope
            while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                scope = scope.enclosingScope
            dirnames = self.import_resolver.env.get("RUBYLIB", "").split(os.pathsep)
            dirnames = [_f for _f in dirnames if _f]
            name = None
            if self.name_part:
                name = self.name_part+"*"
            for symbol in fetchSymbolsInDirectories(dirnames, name, ext=".rb"):
                if scope is self.scope:
                    scope.define(symbol)
                else:
                    # Merge top-level symbols with this scope's top-level
                    # symbols. This is needed, for example, when within a
                    # module that spans multiple files in order to show all
                    # module members (e.g. class names) in a scope
                    # completion.
                    symbol._enclosingScope = scope
                    resolved = scope.resolveMember(symbol.name)
                    if not isinstance(resolved, AbstractScope) or not isinstance(symbol, AbstractScope):
                        setattr(symbol, "_ctx", ApproximateSymbolContext(symbol)) # TODO: assumes "_ctx" attribute
                        scope.define(symbol)
                    else:
                        resolved.merge(symbol)
        return super(RubyScopeCompletionContext, self).getCompletions()

class RubySymbolResolver(SymbolResolver):
    """Ruby symbol resolver for fully-qualified symbol and type names that
    auto-imports all appropriate symbols into the current scope as needed.
    """
    def resolve(self, scope, symbol_name):
        symbol = super(RubySymbolResolver, self).resolve(scope, symbol_name)
        if not symbol:
            for dirname in self._import_resolver.env.get("RUBYLIB", "").split(os.pathsep):
                if not dirname:
                    continue
                for symbol in fetchSymbolsInDirectories(dirname, None, ext=".rb"):
                    symbol._enclosingScope = scope # override
                    if not isinstance(scope.resolveMember(symbol.name), AbstractScope) or not isinstance(symbol, AbstractScope):
                        scope.define(symbol)
                    else:
                        scope.resolveMember(symbol.name).merge(symbol)
            symbol = super(RubySymbolResolver, self).resolve(scope, symbol_name)
        return symbol

class RubyMemberCompletionContext(AbstractMemberCompletionContext):
    """Implementation of AbstractMemberCompletionContext."""
    @property
    def language(self):
        return "Ruby"

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions(), but
        auto-imports all appropriate symbols into the current scope first.
        """
        if self.import_resolver:
            scope = self.scope
            while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                scope = scope.enclosingScope
            dirnames = self.import_resolver.env.get("RUBYLIB", "").split(os.pathsep)
            dirnames = [_f for _f in dirnames if _f]
            name = None
            if self.name_part:
                name = self.name_part+"*"
            for symbol in fetchSymbolsInDirectories(dirnames, name, ext=".rb"):
                symbol._enclosingScope = scope # override
                resolve = scope.resolveMember(symbol.name)
                if not isinstance(resolve, AbstractScope) or not isinstance(symbol, AbstractScope):
                    scope.define(symbol)
                else:
                    resolve.merge(symbol)
        return super(RubyMemberCompletionContext, self).getCompletions()

class RubyFindReferencesContext(AbstractFindReferencesContext):
    """Implementation of AbstractFindReferencesContext."""
    @property
    def projectFiles(self):
        """Implementation of AbstractFindReferencesContext.projectFiles."""
        filenames = []
        for dirname in self.env.get("RUBYLIB", "").split(os.pathsep):
            if not dirname:
                continue
            for filename in fetchAllFilesInDirectory(dirname, ".rb"):
                filenames.append(filename)
        return filenames

class RubyScanner(AbstractScanner):
    def __init__(self, stdlib_file, lexer_class=RubyLexer):
        """Overrides the default scanner __init__() to load Ruby's stdlib, but
        without AbstractModules.
        """
        self._builtInScope = Scope()
        if fileExists(stdlib_file):
            for symbol in fetchSymbolsInFile(stdlib_file, exclude=AbstractModule):
                self._builtInScope.define(symbol)
        else:
            log.error("stdlib file '%s' does not exist", stdlib_file)
        self._lexerClass = lexer_class

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

        if self._lexerClass == RubyLexer:
            self.tokenizer = ruby_lexer.RubyLexer(self.content)
            parser = ruby_parser.Parser(self.tokenizer, "Ruby")
        else:
            tokens = self._lexerClass().tokenize_by_style(self.content)
            self.tokenizer = ruby_lexer.RubyMultiLangLexer(tokens)
            parser = ruby_parser.Parser(self.tokenizer, "RHTML")
        parse_tree = parser.parse()

        scope = parse_tree.toAbstractSymbol(self.builtInScope)
        def class_constructor_adjustments(scope):
            """Ruby's class constructor methods are defined as 'initialize', but
            used as 'new'. They are also implicit. Ensure classes have a 'new'
            constructor, not 'initialize'.
            """
            for member in list(scope.members.values()):
                if isinstance(member, AbstractClass):
                    initialize = member.members.get("initialize")
                    if initialize:
                        symbol = Constructor("new", initialize.type, member.name, member, initialize.ctx)
                        # Update signature.
                        symbol.ctx._node.signature = symbol.ctx._node.signature.replace("initialize", "new", 1)
                        # Copy existing members to new constructor.
                        symbol.members.update(initialize.members)
                        for submember in list(symbol.members.values()):
                            submember._enclosingScope = symbol # update enclosing scopes
                        del member.members["initialize"]
                        member.define(symbol)
                    elif not member.resolve("new") and not member.resolve("initialize"): # check for both since superclasses may not have been adjusted yet
                        symbol = Constructor("new", "Method", member.name, member)
                        member.define(symbol)
                if isinstance(member, AbstractScope):
                    class_constructor_adjustments(member)
        class_constructor_adjustments(scope)
        return scope

    def _iswhitespace(self, style):
        """Returns whether or not the given style is whitespace, taking into
        account the type of lexer this scanner is using (Ruby vs. UDL).
        """
        if self._lexerClass == RubyLexer:
            return style == SCE_RB_DEFAULT
        else:
            return style == SCE_UDL_SSL_DEFAULT

    def _isidentifier(self, style):
        """Returns whether or not the given style is an identifier, taking into
        account the type of lexer this scanner is using (Ruby vs. UDL).
        """
        if self._lexerClass == RubyLexer:
            return style == SCE_RB_IDENTIFIER
        else:
            return style == SCE_UDL_SSL_IDENTIFIER

    def _isvariable(self, style):
        """Returns whether or not the given style is a variable, taking into
        account the type of lexer this scanner is using (Ruby vs. UDL).
        An identifier is considered to be a variable in this case.
        """
        if self._lexerClass == RubyLexer:
            return style in (SCE_RB_IDENTIFIER, SCE_RB_GLOBAL, SCE_RB_INSTANCE_VAR, SCE_RB_CLASS_VAR)
        else:
            return style in (SCE_UDL_SSL_IDENTIFIER, SCE_UDL_SSL_VARIABLE)

    def _isstring(self, style):
        """Returns whether or not the given style is a string, taking into
        account the type of lexer this scanner is using (Ruby vs. UDL).
        """
        if self._lexerClass == RubyLexer:
            return style in (SCE_RB_STRING, SCE_RB_CHARACTER)
        else:
            return style == SCE_UDL_SSL_STRING

    def _isoperator(self, style):
        """Returns whether or not the given style is an operator, taking into
        account the type of lexer this scanner is using (Ruby vs. UDL).
        """
        if self._lexerClass == RubyLexer:
            return style == SCE_RB_OPERATOR
        else:
            return style == SCE_UDL_SSL_OPERATOR

    def _isnumber(self, style):
        """Returns whether or not the given style is a number, taking into
        account the type of lexer this scanner is using (Ruby vs. UDL).
        """
        if self._lexerClass == RubyLexer:
            return style == SCE_RB_NUMBER
        else:
            return style == SCE_UDL_SSL_NUMBER

    def _isdefinition(self, style):
        """Returns whether or not the given style is the name in a class,
        function, or module definition, taking into account the type of lexer
        this scanner is using (Ruby vs. UDL).
        """
        if self._lexerClass == RubyLexer:
            return style in (SCE_RB_CLASSNAME, SCE_RB_DEFNAME, SCE_RB_MODULE_NAME)
        else:
            return False

    def getCompletionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCompletionContext()."""
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        return self._getCompletionContext(filename, position, env, self.tokenizer.q, line, column, scope)

    def getGotoDefinitionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getGotoDefinitionContext()."""
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        return self._getGotoDefinitionContext(filename, position, env, self.tokenizer.q, line, column, scope)

    def getCallTipContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCallTipContext()."""
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        return self._getCallTipContext(filename, position, env, self.tokenizer.q, line, column, scope)

    def getFindReferencesContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getFindReferencesContext()."""
        context = self.getGotoDefinitionContext(filename, position, env)
        if not context:
            return None
        # For unit tests, any relative paths in RUBYLIB should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "RUBYLIB" in env:
                dirs = env["RUBYLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["RUBYLIB"] = os.pathsep.join(dirs)
        import_resolver = RubyImportResolver(filename, env)
        return RubyFindReferencesContext(context.scope, context.symbol_name, self, env, import_resolver=import_resolver, symbol_resolver_class=RubySymbolResolver)

    def _getCompletionContext(self, filename, position, env, _tokens, line, column, scope):
        """Helper method for fetching completion contexts.
        This method is called by both the stand-alone Ruby scanner and the UDL
        Ruby sub-scanner (via HTML), hence the wide variety of parameters.
        Backtracks through the token list starting at the given position,
        looking for an appropriate completion context.
        @param filename String filename to get the context in. Since the file
                        has already been scanned, this parameter is used for
                        reporting purposes only.
        @param position Integer position to get the context for. Since line and
                        column have already been determined, this parameter is
                        rarely used.
        @param env Dictionary of environment variables used in import
                   resolution.
        @param tokens List of scanned tokens produced by the stand-alone lexer
                      or UDL lexer.
        @param line Integer 1-based line number to get the context for. Tokens
                    often contain line number information rather than position
                    info.
        @param column Integer column number to get the context for. Tokens often
                    contain column number information rather than position info.
        @param scope AbstractScope containing all scanned Ruby symbols.
        @return AbstractCompletionContext or None
        """
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if token["start_line"] <= line:
                if token["start_line"] < line:
                    # Keep all non-whitespace, non-newline tokens prior to the
                    # current line.
                    if not self._iswhitespace(token["style"]) or token["text"].endswith("\n"):
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if not self._iswhitespace(token["style"]) or (token["start_column"] + 1 <= column and (column <= token["end_column"] + 1 or "\n" in token["text"])):
                        tokens.append(token)
                elif token["start_column"] == column and self._isidentifier(token["style"]):
                    # Keep an identifier token that starts at the current
                    # position.
                    tokens.append(token)
        leading_whitespace = False
        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in RUBYLIB should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "RUBYLIB" in env:
                dirs = env["RUBYLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["RUBYLIB"] = os.pathsep.join(dirs)
        import_resolver = RubyImportResolver(filename, env)
        name_part = "" # the "already typed" part of a symbol completion

        if len(tokens) == 0:
            # If the position is at the beginning of the buffer, provide scope
            # completions.
            return RubyScopeCompletionContext(scope, import_resolver=import_resolver)
        elif self._iswhitespace(tokens[-1]["style"]) and tokens[-1]["text"].strip("\n") == "":
            # For now, if the position is at the beginning of a line, provide
            # scope completions.
            return RubyScopeCompletionContext(scope, import_resolver=import_resolver)

        # Pre-process the end of the token list.
        if self._iswhitespace(tokens[-1]["style"]):
            # If the position follows whitespace, make a note since some
            # completions after whitespace like "if <|>" are possible, while
            # some like "if<|>" are not.
            leading_whitespace = True
            tokens.pop()
            if len(tokens) == 0:
                return RubyScopeCompletionContext(scope, import_resolver=import_resolver)
        elif self._isvariable(tokens[-1]["style"]):
            # If the position is within a symbol name, or at the beginning of
            # a symbol name, make a note of that name since it should be
            # considered as the "already typed" part of a completion list.
            name_part = tokens[-1]["text"]
            if tokens[-1]["text"].startswith("$"):
                # A "$foo"-type of expression should provide global variable
                # completions.
                return RubyScopeCompletionContext(scope, name_part, AbstractGlobalVariable, import_resolver=import_resolver)
            elif tokens[-1]["text"].startswith("@@"):
                # A "@@foo"-type of expression should provide class variable
                # completions.
                return RubyScopeCompletionContext(scope, name_part, AbstractClassVariable, import_resolver=import_resolver)
            elif tokens[-1]["text"].startswith("@"):
                # A "@foo"-type of expression should provide instance variable
                # completions.
                return RubyScopeCompletionContext(scope, name_part, AbstractInstanceVariable, import_resolver=import_resolver)
            tokens.pop()
            if len(tokens) == 0:
                return RubyScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif self._isstring(tokens[-1]["style"]):
            if ((len(tokens) > 1 and tokens[-2]["text"] == "require") or (len(tokens) > 2 and self._isoperator(tokens[-2]["style"]) and tokens[-2]["text"] == "(" and tokens[-3]["text"] == "require")) and (column <= tokens[-1]["end_column"] or not re.match("^['\"][^'\"]*['\"]$", tokens[-1]["text"])):
                # If the position is within the string preceded by "require" or
                # "require(", it is part of a "require 'foo"-type of expression.
                dirname = os.path.exists(filename) and os.path.dirname(filename) or ""
                if "/" in tokens[-1]["text"]:
                    if column < tokens[-1]["end_column"]:
                        # It is possible the position comes before the last
                        # member (e.g. "<|>foo/bar" or "foo/<|>bar/baz"). If so,
                        # strip out the trailing members.
                        i = tokens[-1]["text"].find("/", column - tokens[-1]["start_column"])
                        if i != -1:
                            tokens[-1]["text"] = tokens[-1]["text"][:i]
                    tokens[-1]["text"] = tokens[-1]["text"].strip("'\"")
                    symbol_name = "/".join(tokens[-1]["text"].split("/")[:-1])
                    name_part = tokens[-1]["text"].split("/")[-1]
                else:
                    name_part = tokens[-1]["text"].strip("'\"")
                # A "require 'foo"-type of expression should provide scope
                # completions and a "require 'foo/bar"-type of expression should
                # provide member completions.
                if "/" not in tokens[-1]["text"]:
                    scope.define(Import("*", None)) # for import resolver
                    return RubyScopeCompletionContext(scope, name_part, AbstractModule, import_resolver=import_resolver)
                else:
                    scope.define(Import("%s/*" % symbol_name, symbol_name)) # for import resolver
                    return RubyMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, syntax_description=FilenameSyntaxDescription)

        # Now look back through the token list and provide an appropriate
        # completion context.
        if self._isoperator(tokens[-1]["style"]) and tokens[-1]["text"] in (".", "::"):
            # If the first significant token behind the position is a '.' or
            # '::', it's probably part of a larger "foo.bar"- or "foo::bar"-type
            # of expression.
            i = len(tokens) - 1
            while i >= 1:
                # Skip back through "name." token pairs in order to construct
                # the full symbol name.
                if not self._isoperator(tokens[i]["style"]) or tokens[i]["text"] not in (".", "::") or not self._isvariable(tokens[i - 1]["style"]):
                    if i == len(tokens) - 1:
                        if self._isstring(tokens[i - 1]["style"]) and tokens[i - 1]["text"][-1] in "'\"":
                            # Actually, a "'foo'.bar"-type of expression should
                            # provide member completions for "String".
                            return RubyMemberCompletionContext(scope, "String", name_part, import_resolver=import_resolver)
                        elif self._isoperator(tokens[i - 1]["style"]) and tokens[i - 1]["text"].endswith("]"):
                            # Actually, the original '.' token could be part of a
                            # "[].foo"-type of expression, assuming the "[]" is not
                            # part of an indexing expression.
                            count = 1
                            j = i - 2
                            while j > 0:
                                if self._isoperator(tokens[j]["style"]) and tokens[j]["text"] == "]":
                                    count += 1
                                elif self._isoperator(tokens[j]["style"]) and tokens[j]["text"] == "[":
                                    count -= 1
                                    if count == 0:
                                        if self._isvariable(tokens[j - 1]["style"]) or self._isoperator(tokens[j - 1]["style"]) and tokens[j - 1]["text"] == "]":
                                            # A "foo[bar].baz"-type of expression
                                            # should not provide completions at this
                                            # time.
                                            return None
                                        break
                                j -= 1
                            # A "[].foo"-type of expression should provide member
                            # completions for "Array".
                            return RubyMemberCompletionContext(scope, "Array", name_part, import_resolver=import_resolver)
                        elif self._isoperator(tokens[i - 1]["style"]) and tokens[i - 1]["text"].endswith("}"):
                            # Actually, the oiginal '.' token could be part of a
                            # "{}.foo"-type of expression, assuming the "{}" is not
                            # a block.
                            count = 1
                            j = i - 2
                            while j > 0:
                                if self._isoperator(tokens[j]["style"]) and tokens[j]["text"] == "}":
                                    count += 1
                                elif self._isoperator(tokens[j]["style"]) and tokens[j]["text"] == "{":
                                    count -= 1
                                    if count == 0:
                                        if self._isidentifier(tokens[j - 1]["style"]):
                                            # A "foo.bar{|baz| quux}.foo"-type of
                                            # expression should not provide
                                            # completions at this time.
                                            return None
                                        break
                                j -= 1
                            # A "{}.foo"-type of expression should provide member
                            # completions for "Hash".
                            return RubyMemberCompletionContext(scope, "Hash", name_part, import_resolver=import_resolver)
                    break
                i -= 2
            symbol_name = "".join([token["text"] for token in tokens[i+1:-1]])
            if not symbol_name:
                if tokens[-1]["text"] == "::":
                    # A "::foo"-type of expression should provide scope
                    # completions for the enclosing scope if possible.
                    scope = scope.enclosingScope or scope
                else:
                    # The original '.' or '::' token came after an unknown
                    # symbol and should not provide completions.
                    return None
            # A "foo.bar"-type of expression should provide member completions.
            return RubyMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, symbol_resolver_class=RubySymbolResolver)
        elif self._isnumber(tokens[-1]["style"]) and tokens[-1]["text"].endswith("."):
            # If the first significant token behind the position is a number
            # with a trailing '.', it's probably part of a "3.foo"- or
            # "3.14.foo"-type of expression. Determine how many '.' the number
            # contains in order to determine whether to provide Fixnum or Float
            # member completions.
            if tokens[-1]["text"].find(".") == len(tokens[-1]["text"]) - 1 and "e" not in tokens[-1]["text"] and "E" not in tokens[-1]["text"]:
                # A "3.foo"-type of expression should provide member completions
                # for "Fixnum".
                return RubyMemberCompletionContext(scope, "Fixnum", name_part, import_resolver=import_resolver)
            else:
                # A "3.14.foo"-type of expression should provide member
                # completions for "Float".
                return RubyMemberCompletionContext(scope, "Float", name_part, import_resolver=import_resolver)
        elif self._isoperator(tokens[-1]["style"]) and tokens[-1]["text"] == "(":
            # If the first significant token behind the position is a '(',
            # it's probably part of a typical expression, which should provide
            # scope completions.
            return RubyScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif self._isdefinition(tokens[-1]["style"]):
            # A "class Foo "-, "def foo "-, or "module Foo "-type of expression
            # should not provide completions.
            return None
        elif tokens[-1]["text"] == "include":
            # An "include Foo"-type of expression should provide module
            # completions.
            # Note: the Ruby lexer does not consider "include" to be a keyword.
            return RubyScopeCompletionContext(scope, name_part, AbstractModule, import_resolver=import_resolver)
        elif self._isoperator(tokens[-1]["style"]) and tokens[-1]["text"] == "<" and len(tokens) > 1 and tokens[-2]["style"] == SCE_RB_CLASSNAME:
            # A "class Foo < Bar"-type of expression should provide class and
            # module completions.
            return RubyScopeCompletionContext(scope, name_part, (AbstractClass, AbstractModule), import_resolver=import_resolver)
        elif leading_whitespace or name_part:
            # If there is no significant token immediately behind the position,
            # it's probably part of a typical expression, which should provide
            # scope completions. However, this should not be done within block
            # arguments.
            if self._isoperator(tokens[-1]["style"]) and tokens[-1]["text"] in "|," and len(tokens) > 1:
                if tokens[-1]["text"] == ",":
                    # Assume the position is within an argument list. Skip to
                    # its beginning in order to determine whether or not it is
                    # block argument list.
                    tokens.pop()
                    while len(tokens) > 1:
                        if not self._isidentifier(tokens[-1]["style"]) and (not self._isoperator(tokens[-1]["style"]) or tokens[-1]["text"] != ","):
                            break
                        tokens.pop()
                if self._isoperator(tokens[-1]["style"]) and tokens[-1]["text"] == "|":
                    # A "|foo, bar"-type of expression should not provide
                    # completions.
                    return None
            return RubyScopeCompletionContext(scope, name_part, import_resolver=import_resolver)

        return None

    def _getGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Helper method for fetching goto definition contexts.
        This method is called by both the stand-alone Ruby scanner and the UDL
        Ruby sub-scanner (via HTML), hence the wide variety of parameters.
        Backtracks through the token list starting at the given position,
        looking for an appropriate goto definition context.
        @param filename String filename to get the context in. Since the file
                        has already been scanned, this parameter is used for
                        reporting purposes only.
        @param position Integer position to get the context for. Since line and
                        column have already been determined, this parameter is
                        rarely used.
        @param env Dictionary of environment variables used in import
                   resolution.
        @param tokens List of scanned tokens produced by the stand-alone lexer
                      or UDL lexer.
        @param line Integer 1-based line number to get the context for. Tokens
                    often contain line number information rather than position
                    info.
        @param column Integer column number to get the context for. Tokens often
                    contain column number information rather than position info.
        @param scope AbstractScope containing all scanned Ruby symbols.
        @return GotoDefinitionContext or None
        """
        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in RUBYLIB should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "RUBYLIB" in env:
                dirs = env["RUBYLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["RUBYLIB"] = os.pathsep.join(dirs)
        import_resolver = RubyImportResolver(filename, env)

        for i in range(len(tokens)):
            token = tokens[i]
            if token["start_line"] <= line and line <= token["end_line"] and token["start_column"] <= column and column <= token["end_column"]:
                if self._isidentifier(token["style"]) or self._isdefinition(token["style"]) or self._isvariable(token["style"]):
                    # If the entity at the position is an identifier,
                    # definition, or variable, retrieve the fully-qualified name
                    # up to and including the position.
                    j = i + 1
                    while i > 0:
                        if self._isoperator(tokens[i - 1]["style"]) and tokens[i - 1]["text"] in (".", "::"):
                            i -= 2
                        else:
                            break
                    symbol_name = "".join([token["text"] for token in tokens[i:j]])
                    return GotoDefinitionContext(scope, symbol_name, import_resolver=import_resolver, symbol_resolver_class=RubySymbolResolver)
                break
            elif token["start_line"] > line:
                break

        return None

    def _getCallTipContext(self, filename, position, env, _tokens, line, column, scope):
        """Helper method for fetching call tip contexts.
        This method is called by both the stand-alone Ruby scanner and the UDL
        Ruby sub-scanner (via HTML), hence the wide variety of parameters.
        Backtracks through the token list starting at the given position,
        looking for an appropriate call tip context.
        @param filename String filename to get the context in. Since the file
                        has already been scanned, this parameter is used for
                        reporting purposes only.
        @param position Integer position to get the context for. Since line and
                        column have already been determined, this parameter is
                        rarely used.
        @param env Dictionary of environment variables used in import
                   resolution.
        @param tokens List of scanned tokens produced by the stand-alone lexer
                      or UDL lexer.
        @param line Integer 1-based line number to get the context for. Tokens
                    often contain line number information rather than position
                    info.
        @param column Integer column number to get the context for. Tokens often
                    contain column number information rather than position info.
        @param scope AbstractScope containing all scanned Ruby symbols.
        @return CallTipContext or None
        """
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if token["start_line"] <= line:
                if token["start_line"] < line:
                    # Keep all non-whitespace, non-newline tokens prior to the
                    # current line.
                    if not self._iswhitespace(token["style"]) or token["text"].endswith("\n"):
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if not self._iswhitespace(token["style"]) or (token["start_column"] + 1 <= column and column <= token["end_column"] + 1):
                        tokens.append(token)

        # Now look back through the token list for a function call and provide
        # its call tip context.
        i = len(tokens) - 1
        if i < 0:
            return None
        paren_level = -1
        if not (self._iswhitespace(tokens[i]["style"]) and len(tokens) > 1 and self._isidentifier(tokens[i - 1]["style"])):
            while i > 0:
                if self._isoperator(tokens[i]["style"]):
                    paren_level -= tokens[i]["text"].count(")")
                    paren_level += tokens[i]["text"].count("(")
                    if paren_level == 0:
                        i -= 1
                        break
                i -= 1
            if paren_level != 0 or not self._isidentifier(tokens[i]["style"]):
                return None # no function call = no call tip
        else:
            # Ruby makes function call parentheses optional.
            i -= 1

        # Retrieve the fully-qualified function name.
        j = i + 1
        while i > 0:
            if self._isoperator(tokens[i - 1]["style"]) and tokens[i - 1]["text"] in (".", "::"):
                i -= 2
                if self._isstring(tokens[i]["style"]):
                    # An expression like "''.downcase()" should be replaced with
                    # "String.downcase()".
                    tokens[i]["text"] = "String"
                elif self._isoperator(tokens[i]["style"]):
                    if tokens[i]["text"].endswith("]"):
                        tokens[i]["text"] = "Array"
                    elif tokens[i]["text"].endswith("}"):
                        tokens[i]["text"] = "Hash"
                    elif tokens[i]["text"].endswith(")"):
                        pass # TODO: function call
            else:
                break
        symbol_name = "".join([token["text"] for token in tokens[i:j]])

        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in RUBYLIB should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "RUBYLIB" in env:
                dirs = env["RUBYLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["RUBYLIB"] = os.pathsep.join(dirs)
        import_resolver = RubyImportResolver(filename, env)
        return CallTipContext(scope, symbol_name, import_resolver=import_resolver, symbol_resolver_class=RubySymbolResolver)

class HTMLRubyScanner(RubyScanner, AbstractUDLSubScanner):
    from language.legacy.ruby.stdlib import RUBY_STDLIB_FILE
    def __init__(self, stdlib_file=RUBY_STDLIB_FILE):
        from language.legacy.rhtml.scanner import RHTMLLexer
        super(HTMLRubyScanner, self).__init__(stdlib_file, RHTMLLexer)

    @property
    def namespace(self):
        """Implementation of AbstractUDLSubScanner.namespace."""
        return "Ruby"

    def prepForUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.prepForUDLTokens()."""
        self.tokens = []

    def handleUDLToken(self, **kwargs):
        """Implementation of AbstractUDLSubScanner.handleUDLToken()."""
        self.tokens.append(kwargs)

    def doneWithUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.doneWithUDLTokens()."""
        self.tokenizer = ruby_lexer.RubyMultiLangLexer(self.tokens)
        parser = ruby_parser.Parser(self.tokenizer, "RHTML")
        parse_tree = parser.parse()
        return parse_tree.toAbstractSymbol(self.builtInScope)

    def getUDLCompletionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCompletionContext()."""
        # The UDL lexer produces slightly different tokens than expected.
        # Normalize them. Also, The Ruby scanner expects 1-based lines, but UDL
        # gives 0-based lines.
        for token in tokens:
            token["start_line"] = token["start_line"] + 1
            token["end_line"] = token["end_line"] + 1
        return self._getCompletionContext(filename, position, env, tokens, line, column, scope)

    def getUDLGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLGotoDefinitionContext()."""
        # The UDL lexer produces slightly different tokens than expected.
        # Normalize them. Also, The Ruby scanner expects 1-based lines, but UDL
        # gives 0-based lines.
        for token in tokens:
            token["start_line"] = token["start_line"] + 1
            token["end_line"] = token["end_line"] + 1
        return self._getGotoDefinitionContext(filename, position, env, tokens, line, column, scope)

    def getUDLCallTipContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCallTipContext()."""
        # The UDL lexer produces slightly different tokens than expected.
        # Normalize them. Also, The Ruby scanner expects 1-based lines, but UDL
        # gives 0-based lines.
        for token in tokens:
            token["start_line"] = token["start_line"] + 1
            token["end_line"] = token["end_line"] + 1
        return self._getCallTipContext(filename, position, env, tokens, line, column, scope)

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    from language.legacy.ruby.stdlib import RUBY_STDLIB_FILE
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    parser = argparse.ArgumentParser(description="Scan Ruby source files")
    parser.add_argument("-rb", action="store_const", const=True, default=True)
    parser.add_argument("-rhtml", action="store_const", const=True)
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    if args.rhtml:
        scanner = HTMLRubyScanner(RUBY_STDLIB_FILE)
    elif args.rb:
        scanner = RubyScanner(RUBY_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
