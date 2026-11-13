# Copyright 2016-2017 ActiveState, Inc. All rights reserved.

"""Source code completions provider for various contexts.

At a high level, fetching code completions works like this:
    1. Scan the file that contains source code to complete.
    2. Analyze the area immediately behind the source code to complete, and
       create a language-agnostic completion context for it. The context
       provides enough information to fetch the proper completions from the
       database. Example completion contexts are:
           AbstractScopeCompletionContext - provides completions from the
                                            current scope (e.g. class names,
                                            function names).
           AbstractMemberCompletionContext - provides completions for a symbol's
                                             members (e.g. string methods,
                                             namespace members).
    3. Ask the context for its completions, which it will fetch from the
       database as needed.

In principle, fetching completions is as easy as this:
    ctx = scanner.getCompletionContext(filename, position)
    if ctx:
        completions = ctx.getCompletions()
        for name, symbol in completions.members.items():
            # do stuff with the completions
Notice that the language scanner takes care of steps 1 and 2, and that this
module takes care of step 3.

Most completions silently come from the database via db.model.Symbol and its
subclasses, which perform lazy-loading.
"""

import logging
import re
import os

from abc import ABCMeta, abstractproperty, abstractmethod

from symbols import (AbstractScope, AbstractSymbol, AbstractSymbolContext,
                     AbstractClassVariable,
                     AbstractClass,
                     AbstractFunction, AbstractMethod, AbstractStaticMethod, AbstractClassMethod, AbstractConstructor, AbstractAnonymousFunction)
from language.common import Module, AbstractImportResolver, AbstractSyntaxDescription, SymbolResolver

log = logging.getLogger("codeintel.completions")
#log.setLevel(logging.DEBUG)

class Completions(object):
    """Contains a set of AbstractSymbol completions for a symbol name."""

    def __init__(self, symbol_name, members, name_part):
        self._symbol_name = symbol_name
        self._members = members
        self._name_part = name_part

    @property
    def symbol_name(self):
        """String symbol name these completions are for."""
        return self._symbol_name

    @property
    def members(self):
        """Dictionary of completions. Keys are string completion names and
        values are their associated AbstractSymbols.
        """
        return self._members

    @property
    def name_part(self):
        """String trailing part of symbol_name (if any).
        This is typically the partially completed member of symbol_name.
        """
        return self._name_part

class AbstractCompletionContext(object, metaclass=ABCMeta):
    """Base completion context object."""

    @abstractproperty
    def language(self):
        """The string language name for this AbstractCompletionContext."""
        pass

    @abstractmethod
    def getCompletions(self):
        """Returns a Completions object that contains completions for this
        AbstractCompletionContext.
        The AbstractSymbols returned have fully-resolved "type" fields.
        """
        pass

class AbstractScopeCompletionContext(AbstractCompletionContext):
    """Completion context for scopes.
    Completions may be class names, function names, etc. -- basically any valid
    symbol in a given scope.
    """
    def __init__(self, scope, name_part="", symbol_type=None, import_resolver=None):
        """Creates a scope completion context.
        @param scope AbstractScope for the source code.
        @param name_part The "already typed text" of a symbol in the scope.
        @param symbol_type AbstractSymbol or list of AbstractSymbols completions
                           are restricted to.
        @param import_resolver Optional AbstractImportResolver used to resolve
                               imports on-demand as they are encountered.
        """
        if not isinstance(scope, AbstractScope):
            raise TypeError("scope must be derived from AbstractScope (got '%s')" % scope.__class__.__name__)
        if not isinstance(name_part, str):
            raise TypeError("name_part must be a string (got '%s')" % name_part.__class__.__name__)
        if symbol_type is not None:
            if not isinstance(symbol_type, (list, tuple)):
                if type(symbol_type) != ABCMeta or not issubclass(symbol_type, AbstractSymbol) or symbol_type == AbstractSymbol:
                    raise TypeError("symbol_type must be a class or list of classes derived from AbstractSymbol or None ('%s' received)" % symbol_type.__class__.__name__)
            else:
                for t in symbol_type:
                    if type(t) != ABCMeta or not issubclass(t, AbstractSymbol) or t == AbstractSymbol:
                        raise TypeError("Each element in symbol_type must be a class derived from AbstractSymbol (one was '%s')" % t.__class__.__name__)
        if not isinstance(import_resolver, (AbstractImportResolver, None.__class__)):
            raise TypeError("import_resolver must be derived from AbstractImportResolver or None (got '%s')" % import_resolver.__class__.__name__)
        self.scope = scope
        self.name_part = name_part
        self.symbol_type = symbol_type
        self.import_resolver = import_resolver

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions()."""
        log.debug("Fetching scope completions for %r", self.scope)
        symbols = {}
        scope = self.scope
        if self.import_resolver:
            self.import_resolver.resolveImports(scope)
        while scope:
            for k, v in scope.members.items():
                if (not self.symbol_type or isinstance(v, self.symbol_type)) and k.startswith(self.name_part) and not isinstance(v, AbstractAnonymousFunction):
                    symbols[k] = v
            scope = scope.enclosingScope

        return Completions("", symbols, self.name_part)

class AbstractMemberCompletionContext(AbstractCompletionContext):
    """Completion context for member symbols.
    Completions may be class methods, namespace members, etc. -- basically any
    valid member of a given symbol.
    """
    def __init__(self, scope, symbol_name, name_part="", symbol_type=None, import_resolver=None, syntax_description=None, symbol_resolver_class=SymbolResolver):
        """Creates a symbol member completion context.
        @param scope AbstractScope of the symbol to complete members for.
        @param symbol_name String name of the symbol to complete members for.
        @param name_part Optional "already typed text" of a completion member
                         name.
        @param symbol_type Optional AbstractSymbol or list of AbstractSymbols
                           completions are restricted to.
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
        if not isinstance(name_part, str):
            raise TypeError("name_part must be a string (got '%s')" % name_part.__class__.__name__)
        if symbol_type is not None:
            if not isinstance(symbol_type, (list, tuple)):
                if type(symbol_type) != ABCMeta or not issubclass(symbol_type, AbstractSymbol) or symbol_type == AbstractSymbol:
                    raise TypeError("symbol_type must be a class or list of classes derived from AbstractSymbol or None ('%s' received)" % symbol_type.__class__.__name__)
            else:
                for t in symbol_type:
                    if type(t) != ABCMeta or not issubclass(t, AbstractSymbol) or t == AbstractSymbol:
                        raise TypeError("Each element in symbol_type must be a class derived from AbstractSymbol (one was '%s')" % t.__class__.__name__)
        if not isinstance(import_resolver, (AbstractImportResolver, None.__class__)):
            raise TypeError("import_resolver must be derived from AbstractImportResolver or None (got '%s')" % import_resolver.__class__.__name__)
        if syntax_description is not None and (type(syntax_description) != ABCMeta or not issubclass(syntax_description, AbstractSyntaxDescription)):
            raise TypeError("syntax_description must be a class derived from AbstractSyntaxDescription or None (got '%s')" % syntax_description.__class__.__name__)
        self.scope = scope
        self.symbol_name = symbol_name
        self.name_part = name_part
        self.symbol_type = symbol_type
        self.import_resolver = import_resolver
        self.symbol_resolver = symbol_resolver_class(syntax_description, import_resolver)

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions()."""
        log.debug("Fetching completions for '%s' in %r", self.symbol_name, self.scope)
        members = {}
        symbol = self.symbol_resolver.resolve(self.scope, self.symbol_name)
        initially_class = isinstance(symbol, AbstractClass)
        resolved = {}
        while symbol and symbol.type and symbol not in resolved:
            if isinstance(symbol, AbstractScope) and not isinstance(symbol, AbstractClass):
                log.debug("Found completions from %r", symbol)
                members.update(symbol.members)
            resolved_symbol = self.symbol_resolver.resolve(self.scope, symbol.type)
            resolved[symbol] = True
            symbol = resolved_symbol
        if isinstance(symbol, AbstractScope):
            if self.import_resolver:
                self.import_resolver.resolveImports(symbol)
            parent = symbol
            log.debug("Found completions from %r", symbol)
            if isinstance(symbol, AbstractClass):
                members.update(symbol.allMembers)
                if not initially_class:
                    # The initially resolved symbol was not a class, but the
                    # fully resolved symbol is. This means class member
                    # completions are being requested, so remove class
                    # constructor functions.
                    for member in list(members.values()):
                        if isinstance(member, AbstractConstructor):
                            del members[member.name]
            else:
                members.update(symbol.members)
            for member in list(members.values()):
                #continue
                if not member.name.startswith(self.name_part) or self.symbol_type and not isinstance(member, self.symbol_type):
                    del members[member.name]
                continue
                ## The following section is disabled as it significantly slows down
                ## completions and in my testing I found no difference in having this
                ## turned on
                # log.debug("Fully resolving each found completion...")
                # if member.type:
                #     # Fully resolve the type field.
                #     resolved = {}
                #     symbol = member
                #     while symbol and symbol.type and symbol not in resolved:
                #         resolved_symbol = self.symbol_resolver.resolve(parent, symbol.type)
                #         resolved[symbol] = True
                #         symbol = resolved_symbol
                #     if symbol:
                #         # TODO: own implementation of Abstract* or use of common ones.
                #         # .__class__ may yield db Symbol implementations.
                #         #members[member.name] = member.__class__(member.name, symbol.name)
                #         pass
            return Completions(self.symbol_name, members, self.name_part)
        log.debug("No completions found.")
        return Completions(self.symbol_name, {}, self.name_part)

class ApproximateSymbolContext(AbstractSymbolContext):
    """Replacement AbstractSymbolContext for "approximate" symbols, symbols that
    do not necessarily belong to their enclosing scopes.
    These contexts are used in ScopeCompletionContexts that attempt to show all
    possible symbols, rather than just guaranteed ones.
    """

    def __init__(self, symbol):
        self._ctx = symbol.ctx

    @property
    def filename(self):
        """Implementation of AbstractSymbolContext.filename"""
        if self._ctx:
            return self._ctx.filename
        return None

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line"""
        if self._ctx:
            return self._ctx.line
        return None

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation"""
        if self._ctx:
            return self._ctx.documentation
        return None

    @property
    def signature(self):
        """Implementation of AbstractSymbolContext.signature"""
        if self._ctx:
            return self._ctx.signature
        return None

    @property
    def approximate(self):
        """Overrides AbstractSymbolContext.approximate to return True."""
        return True
