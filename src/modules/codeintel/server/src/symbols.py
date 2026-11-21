# Copyright 2016-2017 ActiveState, Inc. All rights reserved.

"""Abstract classes for programming language "scopes" and "symbols" used to
construct symbol trees for source code files.
Scopes are containers for programming language symbols, and symbols are nothing
more than names associated with types.

For example, consider the following Python file:

    class Foo:
        def bar(self, baz):
            self.baz = baz
    class Quux:
        pass

There is a single, global scope that contains two class scopes ("Foo" and
"Quux"), while the "Foo" scope has an additional function scope ("bar") and
variable symbol ("baz"), and the "bar" scope has its own argument symbols
("self" and "baz"). All of this is represented in the following symbol tree:

    Scope (filename)
      +-Foo (class, scope)
      |  +-bar (method, scope)
      |  |  +-self (argument)
      |  |  +-baz (argument)
      |  +-baz (instance variable)
      +-Quux (class, scope)

This tree would be stored in the database so that when a separate Python file
imports the file above, creates an instance of a "Foo" class, and asks for code
completions, the database would respond with the "bar" method and "baz" instance
variable.

This framework for scopes and symbols comes from "Language Implementation
Patterns" (Parr, 2010) in Chapter 6, which describes how to track program
symbols. The ebook can be found on the NAS: \\nas1.activestate.com\\misc\\ebooks.

The abstract classes contained in this module do not care about the
implementation details for scopes and symbols -- that is left up to the
implementing classes. For example, language scanners keep the entire symbol tree
in memory until committing to a database, while the database only keeps track of
directly queried symbols and "lazy-loads" everything else in the symbol
hierarchy on demand.

When adding a new "Abstract*" type, remember to add corresponding implementation
classes to "languages/common.py" and "db/model/symbol.py".
"""

# TODO: implement __slots__ for AbstractScopes and AbstractSymbols?
# https://docs.python.org/2/reference/datamodel.html#slots

import logging
import re

from abc import ABCMeta, abstractmethod, abstractproperty

log = logging.getLogger("codeintel.symbols")
#log.setLevel(logging.DEBUG)

class AbstractScope(object, metaclass=ABCMeta):
    """Represents a programming language scope.
    Scopes are essentially just containers for programming language symbols and
    often have an enclosing scope. By "walking" up the scope chain, symbols can
    be resolved.
    In addition to global and local scopes, there are also module, class,
    function, etc. scopes.
    """

    @abstractmethod
    def define(self, symbol, alias=None):
        """Defines the given AbstractSymbol in this scope.
        @param symbol The AbstractSymbol to define.
        @param alias String alias for the AbstractSymbol to define. If None, the
                     AbstractSymbol's name should be used.
        """
        pass

    @abstractproperty
    def enclosingScope(self):
        """The enclosing AbstractScope of this scope."""
        pass

    @abstractproperty
    def members(self):
        """Dictionary of AbstractSymbols defined in this scope.
        String symbol names are keys with associated AbstractSymbol values.
        """
        pass

    @abstractproperty
    def parentScope(self):
        """The parent AbstractScope of this scope.
        For most scopes this is the enclosing scope, but for object-oriented
        scopes like classes, this is the superclass' scope, which can be a list
        of superclasses in cases of multiple-inheritence.
        """
        pass

    def resolve(self, name):
        """Resolves the given string name to its AbstractSymbol in this scope
        (or any enclosing or parent scope) and returns that symbol (or None if
        no symbol was found).
        @param name String name of the symbol to resolve. This is not a
                    fully-qualified symbol name.
        @usage any_scope.resolve("variable_name") -> AbstractVariable
        @usage any_scope.resolve("class_name") -> AbstractClass
        """
        log.debug("Attempting to resolve '%s' in %r", name, self)
        if name in self.members:
            log.debug("Resolved '%s' to member %r", name, self.members[name])
            return self.members[name]
        parent_scope = self.parentScope
        if not parent_scope:
            log.debug("No parent scope to look in; cannot resolve")
            return None
        if isinstance(parent_scope, AbstractScope):
            log.debug("Attempting to resolve in parent scope %r", parent_scope)
            if not hasattr(self, "_attempting_resolve"):
                self._attempting_resolve = {}
            if name in self._attempting_resolve:
                log.debug("Recursion detected. Halting.")
                return None
            self._attempting_resolve[name] = True
            symbol = parent_scope.resolve(name)
            if not symbol and self.enclosingScope and self.enclosingScope is not parent_scope:
                symbol = self.enclosingScope.resolve(name)
            del self._attempting_resolve[name]
            log.debug("Resolved '%s' to %r", name, symbol)
            return symbol
        elif isinstance(parent_scope, (list, tuple)):
            if self in parent_scope:
                parent_scope.remove(self) # avoid recursion
            log.debug("Attempting to resolve in parent scopes %r", parent_scope)
            for scope in parent_scope:
                symbol = scope.resolve(name)
                if symbol:
                    log.debug("Resolved '%s' to %r", name, symbol)
                    return symbol
            if self.enclosingScope:
                log.debug("Attempting to resolve in enclosing scope %r", self.enclosingScope)
                return self.enclosingScope.resolve(name)

    def resolveMember(self, name):
        """Resolves the given string name to its AbstractSymbol in this scope
        (and only this scope) and returns that symbol (or None if no symbol was
        found).
        @param name String name of the symbol to resolve. This is not a
                    fully-qualified symbol name.
        @usage class_symbol.resolve("function_name") -> AbstractMethod
        """
        return self.members.get(name)

    # Beginning of custom functions.

    def merge(self, other):
        """Recursively merges the given scope's members into this scope.
        @param other AbstractScope whose members should be merged with this
                     scope's members.
        """
        if not isinstance(other, AbstractScope):
            raise TypeError("other must be derived from AbstractScope (got '%s')" % other.__class__.__name__)
        for k, v in other.members.items():
            if k not in self.members:
                self.define(v)
            elif isinstance(self.members[k], AbstractScope) and isinstance(v, AbstractScope):
                self.members[k].merge(v)
            else:
                pass # TODO: overwrite?

    def __eq__(self, other):
        """Tests for scope equality.
        Scopes are equal if they have the same enclosing scope and member
        symbols.
        """
        if other is None:
            return False
        if not isinstance(other, AbstractScope):
            raise TypeError("other must be derived from AbstractScope for '==' or '!=' operations (got '%s'); otherwise use 'is' or 'is not'" % other.__class__.__name__)
        if self.enclosingScope != other.enclosingScope or len(self.members) != len(other.members):
            return False
        for k, v in self.members.items():
            if not k in other.members or AbstractSymbol.__ne__(v, other.members[k]):
                return False
        return True

    def __ne__(self, other):
        """Tests for scope inequality."""
        return not self.__eq__(other)

class AbstractSymbol(object, metaclass=ABCMeta):
    """Represents a programming language symbol.
    Symbols are nothing more than a string name associated with a string type.
    AbstractSymbol is the base class for symbols of any programming language
    type, from built-in types to modules and classes, to different kinds of
    variables and functions/methods.
    """

    @abstractproperty
    def name(self):
        """The string name of the symbol."""
        pass

    @abstractproperty
    def type(self):
        """The string type of the symbol.
        This is a string instead of an AbstractSymbol because the AbstractSymbol
        may not be defined yet. For example, consider the Python expression
        "foo = Foo()". When the Python scanner comes across this expression, it
        creates an AbstractSymbol with the name "foo", but for the type it must
        use the string "Foo" because the actual "class Foo" definition might not
        come until later in the file (that is, the "Foo" AbstractSymbol is not
        yet defined, so the string "Foo" cannot be resolved yet).
        """
        pass

    # Beginning of custom functions.

    @abstractproperty
    def ctx(self):
        """AbstractSymbolContext of contextual information for the symbol."""
        pass

    def __eq__(self, other):
        """Tests for symbol equality.
        Symbols are equal if they have the same name, type, and subclass of
        AbstractSymbol.
        """
        if other is None:
            return False
        if not isinstance(other, AbstractSymbol):
            raise TypeError("other must be derived from AbstractSymbol (got '%s')" % other.__class__.__name__)
        return self.name == other.name and self.type == other.type and self.__class__.__mro__[1] == other.__class__.__mro__[1]

    def __ne__(self, other):
        """Tests for symbol inequality."""
        return not self.__eq__(other)

    def __repr__(self):
        """Returns the string representation of this symbol.
        Primarily used when debugging.
        """
        return "<%s '%s'>" % (self.__class__.__name__, self.name)

class AbstractSymbolContext(object, metaclass=ABCMeta):
    """Contextual information for an AbstractSymbol."""

    @abstractproperty
    def filename(self):
        """The AbstractSymbol's filename.
        This will be automatically filled in by the database, but may be None
        prior to database insertion. (This is often the case after a language
        scanner runs. In that event, the filename should be implicitly
        understood to be the name of the scanned file.)
        """
        pass

    @abstractproperty
    def line(self):
        """The AbstractSymbol's line number, or None if no line information is
        available.
        """
        pass

    @abstractproperty
    def documentation(self):
        """The AbstractSymbols's documentation string, or None if no
        documentation is available.
        """
        pass

    @abstractproperty
    def signature(self):
        """The AbstractSymbols's function signature string, or None if the
        symbol is not a function.
        """
        pass

    @property
    def approximate(self):
        """Whether or not the AbstractSymbol's context is "approximate", that
        is, it does not necessarily belong to its enclosing scope.
        This will be True primarily in ScopeCompletionContexts that attempt to
        show all possible symbols, rather than just guaranteed ones.
        """
        return False

class AbstractBuiltIn(AbstractSymbol, AbstractScope):
    """Represents a built-in type.
    Often in object-oriented languages, these types will have member methods.
    For example, in Python "str" and "list" are built-in types, and in Ruby
    "String" and "Array" are built-in types.
    """
    pass

class AbstractKeyword(AbstractSymbol):
    """Represents a programming language keyword."""
    pass

class AbstractConstant(AbstractSymbol):
    """Represents a typical constant."""
    pass

class AbstractVariable(AbstractSymbol):
    """Represents a typical variable.
    This is also a base class for more granular variable "types".
    """
    pass

class AbstractGlobalVariable(AbstractVariable):
    """Represents a global variable."""
    pass

class AbstractInstanceVariable(AbstractVariable):
    """Represents an instance variable in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractPrivateInstanceVariable(AbstractInstanceVariable):
    """Represents a private instance variable in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractProtectedInstanceVariable(AbstractInstanceVariable):
    """Represents a protected instance variable in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractClassVariable(AbstractVariable):
    """Represents a class variable in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractPrivateClassVariable(AbstractClassVariable):
    """Represents a private class variable in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractProtectedClassVariable(AbstractClassVariable):
    """Represents a protected class variable in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractSelfVariable(AbstractVariable):
    """Represents an object variable that refers to itself.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractArgument(AbstractVariable):
    """Represents an argument in a function."""
    pass

class AbstractStruct(AbstractSymbol, AbstractScope):
    """Represents a structured object, essentially an anonymous class.
    This does not apply only to object-oriented languages, but applies to any
    structured entity. For example, in JavaScript objects can contain arbitrary
    functions.
    """
    pass

class AbstractClass(AbstractStruct):
    """Represents a class in an object-oriented language."""
    @abstractproperty
    def superclassName(self):
        """The string name of the class' superclass, or list of string
        superclass names (if any).
        As with AbstractSymbol.type, this must be a string instead of an
        AbstractSymbol.
        @see superclass
        """
        pass

    @property
    def parentScope(self):
        """Implementation of AbstractScope.parentScope.
        The parent AbstractScope of this scope, taking superclasses into
        consideration.
        """
        return self.superclass or self.enclosingScope

    def resolveMember(self, name):
        """Implementation of AbstractScope.resolveMember().
        Resolves the given string name to its AbstractSymbol in this scope
        (taking superclasses into consideration) and returns that symbol (or
        None if no symbol was found).
        @param name String name of the symbol to resolve. This is not a
                    fully-qualified symbol name.
        @usage class_symbol.resolve("function_name") -> AbstractMethod
        """
        log.debug("Attemping to resolve class member '%s' in %r", name, self)
        if name in self.members:
            log.debug("Resolved '%s' to member %r", name, self.members[name])
            return self.members[name]
        elif self.superclassName:
            if not isinstance(self.superclassName, (tuple, list)):
                # Single-inheritence. Resolve member in superclass.
                superclass = self.superclass
                if superclass:
                    log.debug("Attempting to resolve in superclass %r", superclass)
                    return superclass.resolveMember(name)
            else:
                # Multiple-inheritence. Resolve member in superclasses.
                # Go in reverse order since multiple-inheritence resolution
                # works like that.
                log.debug("Attemping to resolve in superclasses %r", self.superclass)
                for superclass in reversed(self.superclass):
                    member = superclass.resolveMember(name)
                    if member:
                        return member

    # Beginning of custom functions.

    def _resolveFull(self, scope, full_name):
        """Resolves the given fully-qualified string name to its AbstractSymbol
        in the given scope (or any of its enclosing or parent scopes) and
        returns that symbol (or None if no symbol was found).
        @param scope AbstractScope to resolve a fully-qualified string name in.
        @param full_name Fully-qualified string name of the symbol to resolve.
        """
        log.debug("Attempting to fully resolve symbol '%s' in %r", full_name, scope)
        name_parts = re.split("[^\\w_]+", full_name)
        log.debug("Attempting to resolve symbol parts: %r", name_parts)
        if not name_parts[0] and len(name_parts) > 0:
            # Resolve a top-level symbol, which is not necessarily at the top of
            # the scope chain because the first scope could be a built-in scope
            # (e.g. stdlib).
            log.debug("Looking for top-level symbol '%s'", name_parts[1])
            name_parts.pop(0)
            parent_scopes = []
            while scope:
                parent_scopes.insert(0, scope)
                scope = scope.enclosingScope
            for child_scope in parent_scopes[0:2]:
                if child_scope.resolveMember(name_parts[0]):
                    scope = child_scope
                    break
            if not isinstance(scope, AbstractScope):
                log.debug("No top-level symbol found.")
                return None
            log.debug("Found top-level symbol %r", scope)
        symbol = scope.resolve(name_parts[0])
        log.debug("Resolved symbol part '%s' to %r", name_parts[0], symbol)
        try:
            for name_part in name_parts[1:]:
                symbol = symbol.resolveMember(name_part)
            if symbol and (not isinstance(symbol, AbstractSymbol) or not isinstance(symbol, AbstractScope)) and symbol.type:
                # The resolved symbol is actually a "pointer" to the real class
                # (e.g. a variable whose type is the class itself). Resolve it
                # once more. (Note: not calling _resolveFull recursively due to
                # potential infinite recursion issues.)
                log.debug("Attempting to resolve symbol type '%s'", symbol.type)
                name_parts = re.split("[^\\w]+", symbol.type)
                log.debug("Attempting to resolve type parts %r", name_parts)
                symbol = scope.resolve(name_parts[0])
                for name_part in name_parts[1:]:
                    symbol = symbol.resolveMember(name_part)
        except AttributeError:
            pass
        log.debug("Resolved symbol '%s' to %r", full_name, symbol)
        return symbol

    @property
    def superclass(self):
        """The AbstractClass superclass of this class."""
        if not self.superclassName or not self.enclosingScope:
            return None
        if not isinstance(self.superclassName, (tuple, list)):
            log.debug("Attempting to resolve superclass name '%s' in %r", self.superclassName, self.enclosingScope)
            return self._resolveFull(self.enclosingScope, self.superclassName)
        else:
            log.debug("Attempting to resolve superclass names %r in %r", self.superclassName, self.enclosingScope)
            superclasses = []
            for name in self.superclassName:
                symbol = self._resolveFull(self.enclosingScope, name)
                if symbol:
                    superclasses.append(symbol)
            return superclasses

    @property
    def allMembers(self):
        """All members, including superclass members of this class.
        This is used primarily for retrieving all completions for this class.
        """
        log.debug("Fetching all members for %r", self)
        if not self.superclassName:
            return self.members
        else:
            if not hasattr(self, "_fetching_all_members"):
                self._fetching_all_members = {}
            if self.name in self._fetching_all_members:
                log.debug("Recursion detected. Halting.")
                return []
            self._fetching_all_members[self.name] = True
            members = self.members.copy()
            superclass = self.superclass
            log.debug("Including superclass members from %r", superclass)
            if isinstance(superclass, AbstractClass):
                members.update(superclass.allMembers)
            elif isinstance(superclass, AbstractScope):
                members.update(superclass.members)
            elif isinstance(superclass, (tuple, list)):
                for klass in superclass:
                    if isinstance(klass, AbstractClass) and klass is not self:
                        members.update(klass.allMembers)
                    else:
                        members.update(klass.members)
            del self._fetching_all_members[self.name]
            return members

class AbstractInterface(AbstractClass):
    """Represents an interface an object can implement.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractFunction(AbstractSymbol, AbstractScope):
    """Represents a typical function.
    This is also a base class for more granular function "types".
    """
    @abstractproperty
    def returnType(self):
        """The string return type of the symbol.
        As with AbstractSymbol.type, this must be a string instead of an
        AbstractSymbol.
        """
        pass

class AbstractAnonymousFunction(AbstractFunction):
    """Represents an anonymous function."""
    pass

class AbstractConstructor(AbstractFunction):
    """Represents a class constructor.
    Constructors do not appear in object member completions.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractMethod(AbstractFunction):
    """Represents a method in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractPrivateMethod(AbstractMethod):
    """Represents a private method in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractProtectedMethod(AbstractMethod):
    """Represents a protected method in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractStaticMethod(AbstractFunction):
    """Represents a static method in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractClassMethod(AbstractFunction):
    """Represents a class method in an object.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractTrait(AbstractSymbol, AbstractScope):
    """Represents a trait an object can use.
    This really only applies in object-oriented programming languages.
    """
    pass

class AbstractModule(AbstractSymbol, AbstractScope):
    """Represents a module."""
    pass

class AbstractNamespace(AbstractSymbol, AbstractScope):
    """Represents a namespace."""
    pass

class AbstractImport(AbstractSymbol):
    """Represents an imported symbol or an included symbol.
    This really only applies in programming languages with importable standard
    libraries, filesystem modules, etc., and/or include-able modules, classes,
    etc.
    While AbstractImports are not technically programming language symbols, they
    are still represented as AbstractSymbols in the database. How they are
    resolved to true AbstractSymbols is up to the implementation. However, there
    is a convention we will use.
    For imported symbols, the AbstractImport's "name" field is the name of the
    imported symbol in the current scope (or if multiple symbols are imported,
    "name" should have a '*' suffix, which indicates multiple imports) and the
    "type" field is the fully qualified symbol name to lookup in the database.
        Python examples:
            import os                      => name="os", type="os"
            from os import path            => name="path", type="os.path"
            from os import path as os_path => name="os_path", type="os.path"
            from os import *               => name="os.*", type="os"
            from os.path import *          => name="os.path.*", type="os.path"
        Ruby examples:
            require 'set'                  => name="set.*", type="set"
            require 'net/http'             => name="net/http.*", type="net/http"
        Note: As can be seen in the Ruby case, the contents of "name" really
        does not matter as long as there is a '*' on the end. "name" just needs
        to be unique in the current scope, as "type" contains the real import
        information.
    For included symbols, the AbstractImport's "name" field is the name of the
    included symbol with a '.' prepended to it (this is to avoid name clashes
    with the actual symbol to include if it is in the current scope), and the
    "type" field is the fully qualified symbol name.
        Ruby examples:
            "include Comparable" => name=".Comparable", type="Comparable"
            "include Foo::Bar"   => name=".Foo::Bar", type="Foo::Bar"
        Note: Like with imported symbols in Ruby, the contents of "name" just
        needs to have a '.' prepended to it and be unique in the current scope.
    """
    pass

class AbstractElement(AbstractSymbol, AbstractScope):
    """Represents a markup or CSS element.
    This really only applies in markup, CSS, or CSS-derived languages.
    """
    pass

class AbstractAttribute(AbstractSymbol, AbstractScope):
    """Represents an attribute in a markup or CSS element.
    This really only applies in markup, CSS, or CSS-derived languages.
    """
    pass

class AbstractCSSClass(AbstractElement):
    """Represents a CSS class style.
    This really only applies in CSS or CSS-derived languages.
    """
    pass

class AbstractCSSId(AbstractElement):
    """Represents a CSS id style.
    This really only applies in CSS or CSS-derived languages.
    """
    pass

class AbstractCSSPseudoClass(AbstractSymbol):
    """Represents a CSS element pseudoclass.
    This really only applies in CSS or CSS-derived languages.
    """
    pass
