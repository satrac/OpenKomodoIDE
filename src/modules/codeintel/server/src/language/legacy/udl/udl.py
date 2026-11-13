#!python
# Copyright (c) 2004-2006 ActiveState Software Inc.
# See the file LICENSE.txt for licensing information.

"""UDL (User-Defined Language) support for codeintel."""

import os
from os.path import dirname, join, abspath, normpath, basename, exists
import sys
import re
import logging
import threading
import operator
import string
import traceback
from pprint import pprint, pformat

import SilverCity
from SilverCity import ScintillaConstants
from SilverCity.ScintillaConstants import * #XXX import only what we need
from SilverCity.Lexer import Lexer

from language.legacy.udl import styles

from abc import ABCMeta, abstractmethod, abstractproperty

from symbols import AbstractScope
from language.common import Scope, Namespace, AbstractScanner

from db.model.helpers import fileExists, fetchSymbolsInFile

log = logging.getLogger("codeintel.udl")
#log.setLevel(logging.DEBUG)

#---- module interface

# Test 'udl/general/is_udl_x_style' tests these.
def is_udl_m_style(style):
    return (ScintillaConstants.SCE_UDL_M_DEFAULT <= style
            <= ScintillaConstants.SCE_UDL_M_UPPER_BOUND)
def is_udl_css_style(style):
    return (ScintillaConstants.SCE_UDL_CSS_DEFAULT <= style
            <= ScintillaConstants.SCE_UDL_CSS_UPPER_BOUND)
def is_udl_csl_style(style):
    return (ScintillaConstants.SCE_UDL_CSL_DEFAULT <= style
            <= ScintillaConstants.SCE_UDL_CSL_UPPER_BOUND)
def is_udl_ssl_style(style):
    return (ScintillaConstants.SCE_UDL_SSL_DEFAULT <= style
            <= ScintillaConstants.SCE_UDL_SSL_UPPER_BOUND)
def is_udl_tpl_style(style):
    return (ScintillaConstants.SCE_UDL_TPL_DEFAULT <= style
            <= ScintillaConstants.SCE_UDL_TPL_UPPER_BOUND)

#XXX Redundant code from koUDLLanguageBase.py::KoUDLLanguage
# Necessary because SilverCity.WordList splits input on white-space

_re_bad_filename_char = re.compile(r'([% 	\x80-\xff])')
def _lexudl_path_escape(m):
    return '%%%02X' % ord(m.group(1))
def _urlescape(s):
    return _re_bad_filename_char.sub(_lexudl_path_escape, s)

class UDLLexer(Lexer):
    """LexUDL wants the path to the .lexres file as the first element of
    the first keywords list.
    """
    _lock = threading.Lock()
    _lexresfile_from_lang = None
    _extra_lexer_dirs = set()

    def __init__(self):
        self._properties = SilverCity.PropertySet()
        self._lexer = SilverCity.find_lexer_module_by_id(ScintillaConstants.SCLEX_UDL)
        lexres_path = _urlescape(self._get_lexres_path())
        log.debug("escaped lexres_path: %r", lexres_path)
        self._keyword_lists = [
            SilverCity.WordList(lexres_path),
        ]

    def tokenize_by_style(self, text, call_back=None):
        """LexUDL.cxx currently isn't thread-safe."""
        self._lock.acquire()
        try:
            return Lexer.tokenize_by_style(self, text, call_back)
        finally:
            self._lock.release()

    @staticmethod
    def add_extra_lexer_dirs(dirs):
        UDLLexer._extra_lexer_dirs.update(dirs)
        UDLLexer._lexresfile_from_lang = None

    @staticmethod
    def _generate_lexer_mapping():
        """Return dict {name > filename} of all lexer resource files (i.e.
        those ones that can include compiled UDL .lexres files).

        It yields directories that should "win" first.
        """
        from glob import glob
        lexresfile_from_lang = {}

        # Find all possible lexer dirs.
        lexer_dirs = []
        lexer_dirs.append(join(dirname(__file__), "generated"))
        for extra_dir in UDLLexer._extra_lexer_dirs:
            lexer_dirs.append(extra_dir)

        # Find all .lexeres files in these lexer dirs.
        for d in reversed(lexer_dirs):  # first come, first served
            lexer_files = glob(join(d, "*.lexres"))
            for f in lexer_files:
                # Get lowered name without the ".lexres" extension.
                name = basename(f).lower().rsplit(".", 1)[0]
                lexresfile_from_lang[name] = f
        return lexresfile_from_lang

    def _get_lexres_path(self):
        lexresfile_from_lang = UDLLexer._lexresfile_from_lang
        if lexresfile_from_lang is None:
            # Generate and cache it.
            lexresfile_from_lang = self._generate_lexer_mapping()
            UDLLexer._lexresfile_from_lang = lexresfile_from_lang

        lexres_file = lexresfile_from_lang.get(self.lang.lower())
        if lexres_file is None:
            raise RuntimeError("could not find lexres file for %s: "
                               "`%s.lexres' does not exist in any "
                               "of the lexer dirs"
                               % (self.lang, self.lang))
        return lexres_file

class AbstractUDLSubScanner(object, metaclass=ABCMeta):
    """Scanner mixin for UDL sub-language scanners.
    UDL parent scanners hand off sub-language tokens to sub-language scanners
    for processing since normal AbstractScanners scan and process filenames.
    """

    def __init__(self, stdlib_file=None):
        """Initializes a UDL sub-scanner with the given stdlib file.
        The stdlib file is used to produce a "builtInScope" that contains all of
        the language's built-in AbstractSymbols. Sub-scanner implementations
        should make that scope the enclosingScope of any code scanned in order
        for built-in symbol resolution to work properly.
        @param stdlib_file Optional string filename in the database containing
                           the language's stdlib.
        """
        if isinstance(self, AbstractUDLScanner):
            raise TypeError("'%s' cannot be both an AbstractUDLScanner and an AbstractUDLSubScanner at once", self.__class__.__name__)

        self._builtInScope = Scope()
        if stdlib_file:
            if fileExists(stdlib_file):
                for symbol in fetchSymbolsInFile(stdlib_file):
                    self._builtInScope.define(symbol)
            else:
                log.error("stdlib file '%s' does not exist in database", stdlib_file)

    @property
    def builtInScope(self):
        """The AbstractScope that contains the language's stdlib of symbols.
        This scope should be the enclosingScope of any code scanned.
        """
        if not hasattr(self, "_builtInScope"):
            # This only happens if an implementation overrides __init__().
            log.debug("No built-in scope found. Call AbstractUDLSubScanner.__init__().")
            self._builtInScope = Scope()
        return self._builtInScope

    def addToBuiltInScope(self, file):
        """Adds the symbols from the given file to the language's built-in
        symbols. This is useful for importing legacy CodeIntel catalogs like
        PHP's Drupal without needing (or attempting) to scan it manually and
        then import it.
        @param file String filename in the database containing the file to add.
        """
        if fileExists(file):
            log.debug("Adding %s to built-in scope", file)
            for symbol in fetchSymbolsInFile(file):
                if not isinstance(self._builtInScope.resolveMember(symbol.name), AbstractScope) or not isinstance(symbol, AbstractScope):
                    self._builtInScope.define(symbol)
                else:
                    self._builtInScope.resolveMember(symbol.name).merge(symbol)

    @abstractproperty
    def namespace(self):
        """The string name of this sub-language. It will be used by the parent
        lexer to construct an AbstractNamespace with sub-language
        AbstractSymbols.
        """
        pass

    @abstractmethod
    def prepForUDLTokens(self):
        """Prepares the sub-language scanner for scanning a set of tokens.
        Preparation may involve setting up scanner variables, switching style
        recognition from Scintilla lexer styles to UDL lexer styles, etc."""
        pass

    @abstractmethod
    def handleUDLToken(self, **kwargs):
        """Process a single UDL token. The arguments are the same as those given
        to SilverCity's lexer.tokenize_by_style() callback function.
        """
        pass

    @abstractmethod
    def doneWithUDLTokens(self):
        """Finishes processing all UDL tokens and returns an AbstractScope that
        contains all AbstractSymbols identified.
        """
        pass

    @abstractmethod
    def getUDLCompletionContext(self, filename, position, env, tokens, line, column, scope):
        """Returns an AbstractCompletionContext for code completions given a
        token list, position, and AbstractScope.
        @param filename String filename to get the context in. Since the file
                        has already been scanned by the UDL lexer, this
                        parameter is used for reporting purposes only.
        @param position Integer position to get the context for. Since line and
                        column have already been determined, this parameter is
                        rarely used.
        @param env Dictionary of environment variables used in import
                   resolution.
        @param tokens List of scanned tokens produced by the UDL lexer. Token's
                      line numbers are 0-based, not 1-based.
        @param line Integer 1-based line number to get the context for. Tokens
                    often contain line number information rather than position
                    info.
        @param column Integer column number to get the context for. Tokens often
                    contain column number information rather than position info.
        @param scope AbstractScope containing all scanned symbols for this
                     language.
        @return AbstractCompletionContext or None
        """
        pass

    @abstractmethod
    def getUDLGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Returns a GotoDefinitionContext for goto definition given a token
        list, position, and AbstractScope.
        @param filename String filename to get the context in. Since the file
                        has already been scanned by the UDL lexer, this
                        parameter is used for reporting purposes only.
        @param position Integer position to get the context for. Since line and
                        column have already been determined, this parameter is
                        rarely used.
        @param env Dictionary of environment variables used in import
                   resolution.
        @param tokens List of scanned tokens produced by the UDL lexer. Token's
                      line numbers are 0-based, not 1-based.
        @param line Integer 1-based line number to get the context for. Tokens
                    often contain line number information rather than position
                    info.
        @param column Integer column number to get the context for. Tokens often
                    contain column number information rather than position info.
        @param scope AbstractScope containing all scanned symbols for this
                     language.
        @return GotoDefinitionContext or None
        """
        pass

    @abstractmethod
    def getUDLCallTipContext(self, filename, position, env, tokens, line, column, scope):
        """Returns a CallTipContext for call tips given a token list, position,
        and AbstractScope.
        @param filename String filename to get the context in. Since the file
                        has already been scanned by the UDL lexer, this
                        parameter is used for reporting purposes only.
        @param position Integer position to get the context for. Since line and
                        column have already been determined, this parameter is
                        rarely used.
        @param env Dictionary of environment variables used in import
                   resolution.
        @param tokens List of scanned tokens produced by the UDL lexer. Token's
                      line numbers are 0-based, not 1-based.
        @param line Integer 1-based line number to get the context for. Tokens
                    often contain line number information rather than position
                    info.
        @param column Integer column number to get the context for. Tokens often
                    contain column number information rather than position info.
        @param scope AbstractScope containing all scanned symbols for this
                     language.
        @return CallTipContext or None
        """
        pass

class AbstractUDLScanner(AbstractScanner, AbstractUDLSubScanner, metaclass=ABCMeta):
    """AbstractScanner for UDL languages.
    This scanner implements AbstractScanner.scan(), which scans sub-languages.
    """

    @abstractproperty
    def udlLexer(self):
        """The UDLLexer this scanner uses for tokenizing source code."""
        pass

    @abstractproperty
    def sslScanner(self):
        """The AbstractUDLSubScanner this scanner users for processing the UDL
        language's server-side language.
        """
        pass

    @abstractproperty
    def cslScanner(self):
        """The AbstractUDLSubScanner this scanner uses for processing the UDL
        language's client-side language.
        """
        pass

    @abstractproperty
    def cssScanner(self):
        """The AbstractUDLSubScanner this scanner uses for processing the UDL
        language's CSS language.
        """
        pass

    @abstractproperty
    def tplScanner(self):
        """The AbstractUDLSubScanner this scanner uses for processing the UDL
        language's template language."""
        pass

    def scan(self, filename, env={}):
        """Implementation of AbstractScanner.scan().
        For testing purposes, when filename is None, stdin is scanned.
        Returns an AbstractScope that contains namespaced sub-languages.
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

        # Prepare this scanner for markup language(m) and prepare sub-scanners
        # for server-side language (ssl), client-side language (csl), style
        # language (css), and template language (tpl).
        self.prepForUDLTokens()
        if self.sslScanner:
            if not isinstance(self.sslScanner, AbstractUDLSubScanner):
                raise TypeError("%s's sslScanner must be an instance of AbstractUDLSubScanner (got '%s')" % (self.__class__.__name__, self.sslScanner.__class__.__name__))
            self.sslScanner.prepForUDLTokens()
        if self.cslScanner:
            if not isinstance(self.cslScanner, AbstractUDLSubScanner):
                raise TypeError("%s's cslScanner must be an instance of AbstractUDLSubScanner (got '%s')" % (self.__class__.__name__, self.cslScanner.__class__.__name__))
            self.cslScanner.prepForUDLTokens()
        if self.cssScanner:
            if not isinstance(self.cssScanner, AbstractUDLSubScanner):
                raise TypeError("%s's cssScanner must be an instance of AbstractUDLSubScanner (got '%s')" % (self.__class__.__name__, self.cssScanner.__class__.__name__))
            self.cssScanner.prepForUDLTokens()
        if self.tplScanner:
            if not isinstance(self.tplScanner, AbstractUDLSubScanner):
                raise TypeError("%s's tplScanner must be an instance of AbstractUDLSubScanner (got '%s')" % (self.__class__.__name__, self.tplScanner.__class__.__name__))
            self.tplScanner.prepForUDLTokens()

        # Scan the source file and for each sub-language token, pass it to the
        # appropriate sub-scanner for processing.
        self._tokens = self.udlLexer.tokenize_by_style(self.content) # for use by getCompletionContext()
        for token in self._tokens:
            if is_udl_m_style(token["style"]):
                self.handleUDLToken(**token)
            elif is_udl_ssl_style(token["style"]):
                if self.sslScanner:
                    self.sslScanner.handleUDLToken(**token)
            elif is_udl_csl_style(token["style"]):
                if self.cslScanner:
                    self.cslScanner.handleUDLToken(**token)
            elif is_udl_css_style(token["style"]):
                if self.cssScanner:
                    self.cssScanner.handleUDLToken(**token)
            elif is_udl_tpl_style(token["style"]):
                if self.tplScanner:
                    self.tplScanner.handleUDLToken(**token)

        # Compute the final scope to return. Each sub-language is namespaced.
        scope = self.doneWithUDLTokens()
        if self.sslScanner:
            # Add the server-side language's tokens into a separate namespace.
            ssl_scope = self.sslScanner.doneWithUDLTokens()
            symbol = Namespace(self.sslScanner.namespace, ssl_scope.enclosingScope)
            symbol.merge(ssl_scope)
            scope.define(symbol)
        if self.cslScanner:
            # Add the client-side language's tokens into a separate namespace.
            csl_scope = self.cslScanner.doneWithUDLTokens()
            symbol = Namespace(self.cslScanner.namespace, csl_scope.enclosingScope)
            symbol.merge(csl_scope)
            scope.define(symbol)
        if self.cssScanner:
            # Add the style language's tokens into a separate namespace.
            css_scope = self.cssScanner.doneWithUDLTokens()
            symbol = Namespace(self.cssScanner.namespace, css_scope.enclosingScope)
            symbol.merge(css_scope)
            scope.define(symbol)
        if self.tplScanner:
            # Add the template language's tokens into a separate namespace.
            tpl_scope = self.tplScanner.doneWithUDLTokens()
            symbol = Namespace(self.tplScanner.namespace, tpl_scope.enclosingScope)
            symbol.merge(tpl_scope)
            scope.define(symbol)
        return scope

    def _getNamedContext(self, name, filename, position, env):
        """Helper method for fetching contexts.
        Depending on what sub-language the given position is in, invoke that
        sub-language's context fetching method.
        """
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        # Note: The SilverCity tokenizer returns tokens with 0-based lines.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        # Determine which scanner to hand the completion context request off to.
        n_tokens = len(self._tokens)
        for i in range(n_tokens):
            token = self._tokens[i]
            if position <= token["end_index"] or i == n_tokens - 1:
                if is_udl_m_style(token["style"]):
                    scanner = self
                elif is_udl_ssl_style(token["style"]):
                    if not self.sslScanner:
                        log.debug("sslScannerClass not defined for scanner '%s'" % self.__class__.__name__)
                        continue
                    scanner = self.sslScanner
                elif is_udl_csl_style(token["style"]):
                    scanner = self.cslScanner
                elif is_udl_css_style(token["style"]):
                    scanner = self.cssScanner
                elif is_udl_tpl_style(token["style"]):
                    if not self.tplScanner:
                        log.debug("tplScannerClass not defined for scanner '%s'" % self.__class__.__name__)
                        continue
                    scanner = self.tplScanner
                else:
                    continue
                if scope.resolveMember(scanner.namespace):
                    scope = scope.resolveMember(scanner.namespace)
                scope = scope.resolveScope(line)
                return getattr(scanner, "getUDL%sContext" % name)(filename, position, env, self._tokens[:], line, column, scope)

        log.debug("Could not determine scanner to hand %s.getCompletionContext() off to" % self.__class__.__name__)
        return None

    def getCompletionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCompletionContext()."""
        return self._getNamedContext("Completion", filename, position, env)

    def getGotoDefinitionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getGotoDefinitionContext()."""
        return self._getNamedContext("GotoDefinition", filename, position, env)

    def getCallTipContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCallTipContext()."""
        return self._getNamedContext("CallTip", filename, position, env)

    def getFindReferencesContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getFindReferencesContext()."""
        return self._getNamedContext("FindReferences", filename, position, env)
