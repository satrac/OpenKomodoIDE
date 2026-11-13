# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Call Tip provider for programming language symbols.

At a high level, fetching the call tip for a symbol works like this:
    1. Scan the file that contains the symbol to fetch the call tip for.
    2. Analyze the area immediately around that symbol, and create a
       language-agnostic call tip context for it. The context provides enough
       information to fetch the symbol's documentation from the database.
    3. Ask the context for its call tip, which it will fetch from the database
       as needed.

In principle, fetching the call tip for a symbol is as easy as this:
    ctx = scanner.getCallTipContext(filename, position)
    if ctx:
        call_tip = ctx.getCallTip()
        summary = call_tip.summary
        signature = call_tip.signature
        # Display summary and signature (in any order).
Notice that the language scanner takes care of steps 1 and 2, and that this
module takes care of step 3.

Call tips for symbols outside the current file (e.g. via inheritence) come from
the database via db.model.Symbol and its subclasses, which perform lazy-loading
when searching for a symbol.
"""

import logging
import re

from abc import ABCMeta

from symbols import AbstractScope, AbstractClass, AbstractFunction, AbstractConstructor
from language.common import AbstractImportResolver, AbstractSyntaxDescription, SymbolResolver

log = logging.getLogger("codeintel.calltips")
#log.setLevel(logging.DEBUG)

class CallTip(object):
    """Contains call tip information for a symbol name."""
    def __init__(self, symbol_name, summary=None, signature=None):
        """Creates call tip information.
        @param symbol_name String name of the symbol the call tip is for.
        @param summary String documentation for the symbol.
        @param signature String function signature for the symbol (if
                         applicable).
        """
        self._symbol_name = symbol_name
        self._summary = summary
        self._signature = signature

    @property
    def symbol_name(self):
        """String symbol name this call tip is for."""
        return self._symbol_name

    @property
    def summary(self):
        """String documentation summary for the symbol, or None if no
        documentation exists."""
        return self._summary

    @property
    def signature(self):
        """String function signature for this symbol, or None if the symbol is
        not a function."""
        return self._signature

class CallTipContext(object):
    """Call tip context for a function symbol."""
    def __init__(self, scope, symbol_name, import_resolver=None, syntax_description=None, symbol_resolver_class=SymbolResolver):
        """Creates a call tip context.
        @param scope AbstractScope of the function symbol to go to the call tip
                     of.
        @param symbol_name String name of the function symbol to go to the call
                           tip of.
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

    def getCallTip(self):
        """Returns the string call tip for the function symbol in this
        CallTipContext.
        """
        log.debug("Fetching call tip for symbol '%s'", self.symbol_name)
        symbol = self.symbol_resolver.resolve(self.scope, self.symbol_name)
        if isinstance(symbol, AbstractClass):
            # When "calling" a class like in Python or JavaScript, look for a
            # constructor method and fetch its documentation.
            for member in list(symbol.members.values()):
                if isinstance(member, AbstractConstructor):
                    log.debug("Class constructor found. Using its call tip.")
                    return CallTip(self.symbol_name, member.ctx.documentation, member.ctx.signature)
        if symbol and (symbol.ctx.documentation or symbol.ctx.signature):
            log.debug("Found call tip.")
            documentation = symbol.ctx.documentation
            if not documentation and isinstance(symbol.enclosingScope, AbstractClass) and symbol.enclosingScope.superclass:
                # Look in superclasses for more substantial documentation.
                superclass = symbol.enclosingScope.superclass
                if not isinstance(superclass, (list, tuple)):
                    member = superclass.resolveMember(symbol.name)
                    if isinstance(member, symbol.__class__) and member.ctx.documentation:
                        log.debug("Inheriting documentation from superclass %r", superclass)
                        documentation = member.ctx.documentation
                else:
                    for klass in superclass:
                        member = klass.resolveMember(symbol.name)
                        if isinstance(member, symbol.__class__) and member.ctx.documentation:
                            log.debug("Inheriting documentation from superclass %r", klass)
                            documentation = member.ctx.documentation
            return CallTip(self.symbol_name, documentation, symbol.ctx.signature)
        else:
            if isinstance(symbol, AbstractFunction):
                log.debug("No documentation or signature found for function. Using default call tip.")
                return CallTip(self.symbol_name, "", "%s(...)" % symbol.name)
            elif isinstance(symbol, AbstractClass) and symbol.superclass:
                log.debug("No documentation or signature found for class. Trying superclass(es).")
                symbol = symbol.superclass
                if not isinstance(symbol, (list, tuple)):
                    self.scope = symbol
                    self.symbol_name = symbol.name
                    return self.getCallTip()
                else:
                    superclasses = symbol
                    for symbol in superclasses:
                        self.scope = symbol
                        self.symbol_name = symbol.name
                        return self.getCallTip()
            log.debug("No documentation or signature found. No call tip.")
            return None
