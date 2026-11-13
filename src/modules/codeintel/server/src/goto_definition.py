# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Goto Definition provider for programming language symbols.

At a high level, finding the definition of a symbol works like this:
    1. Scan the file that contains the symbol to go to the definition of.
    2. Analyze the area immediately around that symbol, and create a
       language-agnostic goto definition context for it. The context provides
       enough information to fetch the symbol's definition from the database.
    3. Ask the context for its definition, which it will fetch from the database
       as needed.

In principle, goto definition for a symbol is as easy as this:
    ctx = scanner.getGotoDefinitionContext(filename, position)
    if ctx:
        definition = ctx.getDefinition()
        filename = definition.filename
        line = definition.line
        # Go to filename:line.
        # Note: the definition object may have more relevant properties.
Notice that the language scanner takes care of steps 1 and 2, and that this
module takes care of step 3.

Definitions outside the current file come from the database via db.model.Symbol
and its subclasses, which perform lazy-loading when searching for a symbol
definition.
"""

import logging

from abc import ABCMeta

from symbols import AbstractScope, AbstractFunction, AbstractConstructor
from language.common import AbstractImportResolver, AbstractSyntaxDescription, SymbolResolver

log = logging.getLogger("codeintel.goto_definition")
#log.setLevel(logging.DEBUG)

class Definition(object):
    """Represents a symbol's definition."""
    def __init__(self, symbol):
        self._symbol = symbol

    @property
    def filename(self):
        """The filename the symbol is defined in."""
        return self._symbol.ctx.filename

    @property
    def line(self):
        """The line number the symbol is defined on."""
        return self._symbol.ctx.line

    @property
    def name(self):
        """The symbol's name."""
        return self._symbol.name

    @property
    def type(self):
        """The symbol's type."""
        return self._symbol.type

class GotoDefinitionContext(object):
    """Goto definition context for a symbol."""
    def __init__(self, scope, symbol_name, import_resolver=None, syntax_description=None, symbol_resolver_class=SymbolResolver):
        """Creates a goto definition context.
        @param scope AbstractScope of the symbol to go to the definition of.
        @param symbol_name String name of the symbol to go to the definition of.
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
        if not isinstance(import_resolver, (AbstractImportResolver, None.__class__)):
            raise TypeError("import_resolver must be derived from AbstractImportResolver or None (got '%s')" % import_resolver.__class__.__name__)
        if syntax_description is not None and (type(syntax_description) != ABCMeta or not issubclass(syntax_description, AbstractSyntaxDescription)):
            raise TypeError("syntax_description must be a class derived from AbstractSyntaxDescription or None (got '%s')" % syntax_description.__class__.__name__)
        self.scope = scope
        self.symbol_name = symbol_name
        self.symbol_resolver = symbol_resolver_class(syntax_description, import_resolver)

    def getDefinition(self):
        """Returns a Definition object that represents the symbol definition for
        this GotoDefinitionContext."""
        log.debug("Fetching definition for symbol '%s'", self.symbol_name)
        symbol = self.symbol_resolver.resolve(self.scope, self.symbol_name)
        if symbol:
            log.debug("Definition found.")
            return Definition(symbol)
        else:
            log.debug("No definition found.")
            return None
