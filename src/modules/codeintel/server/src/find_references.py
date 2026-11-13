# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Find References provider for programming language symbols.

At a high level, finding references of a symbol works like this:
    1. Scan the file that contains the symbol to find references of.
    2. Analyze the area immediately around that symbol, and create a
       language-agnostic find reference context for it. The context provides
       enough information to fetch the symbol's references.
    3. Ask the context for its references.

In principle, find reference for a symbol is as easy as this:
    ctx = scanner.getFindReferenceContext(filename, position)
    if ctx:
        references = ctx.getReferences()
        for reference in references:
            filename = reference.filename
            line = reference.line
            # List filename:line.
            # Note: the reference object may have more relevant properties.
Notice that the language scanner takes care of steps 1 and 2, and that this
module takes care of step 3.
"""

import logging
import os
import re

from abc import ABCMeta, abstractmethod, abstractproperty

from symbols import AbstractScope, AbstractFunction, AbstractConstructor
from language.common import AbstractScanner, AbstractImportResolver, AbstractSyntaxDescription, CommonSyntaxDescription, SymbolResolver

log = logging.getLogger("codeintel.find_references")
#log.setLevel(logging.DEBUG)

class Reference(object):
    """Represents one of a symbol's references."""
    def __init__(self, symbol_name, filename, lineData, line, pos):
        self._symbol_name = symbol_name
        self._filename = filename
        self._lineData = lineData
        self._line = line
        self._pos = pos

    @property
    def symbol_name(self):
        """The symbol's name."""
        return self._symbol_name

    @property
    def filename(self):
        """The filename this symbol reference is in."""
        return self._filename

    @property
    def lineData(self):
        """The line contents for the line this symbol reference is on."""
        return self._lineData

    @property
    def line(self):
        """The line number this symbol reference is on."""
        return self._line

    @property
    def pos(self):
        """The byte position this symbol reference is at."""
        return self._pos

class AbstractFindReferencesContext(object, metaclass=ABCMeta):
    """Find references context for a symbol."""

    def __init__(self, scope, symbol_name, scanner, env={}, import_resolver=None, syntax_description=None, symbol_resolver_class=SymbolResolver):
        """Creates a find references context.
        @param scope AbstractScope of the symbol to get references for.
        @param symbol_name String name of the symbol to get references for.
        @param scanner AbstractScanner TODO
        @param env Optional dictionary of environment variables to use when
            scanning.
            This dictionary is NOT a dictionary of system environment
            variables (although it could contain them). It is a scanning
            environment, a container of variables that language scanners
            can reference during scan operations.
        @param import_resolver Optional AbstractImportResolver used to resolve
                               imports on-demand as they are encountered.
        @param syntax_description Optional AbstractSyntaxDescription to use for
                                  parsing fully-qualified symbol and type names.
                                  The default value is an instance of a
                                  CommonSyntaxDescription.
        @param symbol_resolver_class Optional class to use for resolving
                                     symbols. The default value is
                                     language.common.SymbolResolver.
        @see SymbolResolver
        """
        if not isinstance(scope, AbstractScope):
            raise TypeError("scope must be derived from AbstractScope (got '%s')" % scope.__class__.__name__)
        if not isinstance(symbol_name, str):
            raise TypeError("symbol_name must be a string (got '%s')" % symbol_name.__class__.__name__)
        if not isinstance(scanner, AbstractScanner):
            raise TypeError("scanner must be derived from AbstractScanner (got '%s')" % scanner.__class__.__name__)
        if not isinstance(import_resolver, (AbstractImportResolver, None.__class__)):
            raise TypeError("import_resolver must be derived from AbstractImportResolver or None (got '%s')" % import_resolver.__class__.__name__)
        if syntax_description is not None and (type(syntax_description) != ABCMeta or not issubclass(syntax_description, AbstractSyntaxDescription)):
            raise TypeError("syntax_description must be a class derived from AbstractSyntaxDescription or None (got '%s')" % syntax_description.__class__.__name__)
        self.scope = scope
        self.symbol_name = symbol_name
        self.scanner = scanner
        self.env = env
        self.symbol_resolver = symbol_resolver_class(syntax_description, import_resolver)
        syntax_description = (syntax_description or CommonSyntaxDescription)()
        if not isinstance(syntax_description.symbolSeparator, (list, tuple)):
            self.name_part = symbol_name.split(syntax_description.symbolSeparator)[-1]
        else:
            self.name_part = re.split("|".join([re.escape(sep) for sep in syntax_description.symbolSeparator]), symbol_name)[-1]

    @abstractproperty
    def projectFiles(self):
        """A list of project files to search for symbol references in."""
        pass

    def getReferences(self):
        """Returns a list of Reference objects that represents the references of
        the symbol for this FindUsageContext."""
        log.debug("Fetching references of symbol '%s'", self.symbol_name)
        references = []
        symbol = self.symbol_resolver.resolve(self.scope, self.symbol_name)
        for filename in self.projectFiles:
            if not os.path.exists(filename):
                continue
            try:
                contents = self.scanner.readFile(filename)
                # TODO: current file is read from disk instead of using current
                # contents.
            except Exception:
                log.exception("Error reading file '%s'", filename)
            regex = r"\b%s\b" % re.escape(self.name_part)
            if re.match(r"\W", self.name_part):
                regex = regex[2:] # leading '\b' unnecessary
            for match in re.finditer(regex, contents):
                context = self.scanner.getGotoDefinitionContext(filename, match.start(), self.env)
                if context:
                    resolved_symbol = self.symbol_resolver.resolve(context.scope, context.symbol_name)
                    # TODO: if symbol == resolved_symbol: sometimes symbol types
                    # get lost or do not match up.
                    if resolved_symbol and symbol.name == resolved_symbol.name and symbol.__class__.__mro__[1] == resolved_symbol.__class__.__mro__[1]:
                        lines = contents.split("\n")
                        lineNo = contents[:match.start()].count("\n")
                        references.append(Reference(context.symbol_name, filename, lines[lineNo], lineNo + 1, match.start()))
        return references

    def getReferencesWithCb(self, callback):
        """Returns a list of Reference objects that represents the references of
        the symbol for this FindUsageContext."""
        log.debug("Fetching references of symbol '%s'", self.symbol_name)
        symbol = self.symbol_resolver.resolve(self.scope, self.symbol_name)
        for filename in self.projectFiles:
            references = []
            if not os.path.exists(filename):
                continue
            try:
                contents = self.scanner.readFile(filename)
                # TODO: current file is read from disk instead of using current
                # contents.
            except Exception:
                log.exception("Error reading file '%s'", filename)
            regex = r"\b%s\b" % re.escape(self.name_part)
            if re.match(r"\W", self.name_part):
                regex = regex[2:] # leading '\b' unnecessary
            for match in re.finditer(regex, contents):
                context = self.scanner.getGotoDefinitionContext(filename, match.start(), self.env)
                if context:
                    resolved_symbol = self.symbol_resolver.resolve(context.scope, context.symbol_name)
                    # TODO: if symbol == resolved_symbol: sometimes symbol types
                    # get lost or do not match up.
                    if resolved_symbol and symbol.name == resolved_symbol.name and symbol.__class__.__mro__[1] == resolved_symbol.__class__.__mro__[1]:
                        lines = contents.split("\n")
                        lineNo = contents[:match.start()].count("\n")
                        references.append(Reference(context.symbol_name, filename, lines[lineNo], lineNo + 1, match.start()))
            callback(filename, references)
