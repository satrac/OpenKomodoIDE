# Copyright 2016-2017 ActiveState, Inc. All rights reserved.

"""Common utilities module for language scanners.

This module contains implementations of programming language "scopes" and
"symbols" based on the Abstract classes defined in the top-level "symbols.py"
module. These implementations are suitable for use in language scanners and
store the entire, scanned symbol tree in memory. They do not interact at all
with the database.

This module also contains abstract classes for language scanners used to scan
source code files. A scanner's job is two-fold: to scan source code files and
return their symbol trees, and to provide various contexts for positions given
in source code files in order to produce code completions, go to symbol
definitions, call tips, and the like. Scanners should NOT write symbol trees to
the database.
"""

import logging
import os
import re

from abc import ABCMeta, abstractmethod, abstractproperty

from symbols import (AbstractScope, AbstractSymbol, AbstractSymbolContext,
                     AbstractBuiltIn, AbstractKeyword,
                     AbstractConstant,
                     AbstractVariable, AbstractGlobalVariable, AbstractInstanceVariable, AbstractPrivateInstanceVariable, AbstractProtectedInstanceVariable, AbstractClassVariable, AbstractPrivateClassVariable, AbstractProtectedClassVariable, AbstractSelfVariable, AbstractArgument,
                     AbstractStruct,
                     AbstractClass, AbstractInterface,
                     AbstractFunction, AbstractAnonymousFunction, AbstractConstructor, AbstractMethod, AbstractPrivateMethod, AbstractProtectedMethod, AbstractStaticMethod, AbstractClassMethod,
                     AbstractTrait,
                     AbstractModule, AbstractNamespace, AbstractImport,
                     AbstractElement, AbstractAttribute,
                     AbstractCSSClass, AbstractCSSId, AbstractCSSPseudoClass)

from db.model.helpers import fileExists, fetchSymbolsInFile

log = logging.getLogger("codeintel.language.common")
#log.setLevel(logging.DEBUG)

class Scope(AbstractScope):
    """Implementation of an AbstractScope for use by language scanners."""
    def __init__(self, enclosingScope=None):
        """Creates a new scope with the given enclosing scope.
        @param enclosingScope The AbstractScope that contains this scope.
        """
        if not isinstance(enclosingScope, (AbstractScope, None.__class__)):
            raise TypeError("enclosingScope must be derived from AbstractScope or None (got '%s')" % enclosingScope.__class__.__name__)
        self._enclosingScope = enclosingScope
        self._members = {}
        self._ordered_members = [] # only for pretty-printing

    def define(self, symbol, alias=None):
        """Implementation of AbstractScope.define()."""
        if not isinstance(symbol, AbstractSymbol):
            raise TypeError("symbol must be derived from AbstractSymbol (got '%s')" % symbol.__class__.__name__)
        name = alias or symbol.name
        if name in self.members:
            log.debug("Symbol '%s' already defined in scope", name)
            return # TODO: which symbol to keep?
        self.members[name] = symbol
        self._ordered_members.append(symbol) # only for pretty-printing

    @property
    def enclosingScope(self):
        """Implementation of AbstractScope.enclosingScope."""
        return self._enclosingScope

    @property
    def members(self):
        """Implementation of AbstractScope.members."""
        return self._members

    @property
    def parentScope(self):
        """Implementation of AbstractScope.parentScope."""
        return self.enclosingScope

    # Beginning of custom functions.

    def resolveScope(self, position_or_line):
        """Resolves the inner-most scope within this scope that contains the
        given integer position or line number (depending on the underlying
        AbstractScannerContext implementation).
        This depends on Symbols having a "ctx" field populated by a language
        scanner.
        @param position_or_line Integer position or line number to get the
                                inner-most scope of.
        @see Symbol.ctx
        """
        for member in list(self.members.values()):
            if isinstance(member, Symbol) and member.ctx and member.ctx.contains(position_or_line) and isinstance(member, Scope):
                return member.resolveScope(position_or_line)
        return self

    def prettyPrint(self, indent=""):
        """Pretty-printer for this scope and its members.
        This is used for visual debugging only.
        @param indent The current indentation level for string output.
        """
        line = "%s%s: %s" % (indent, self.__class__.__name__,
                             getattr(self, "name",
                                     not self.enclosingScope and "Global" or
                                     "Local"))
        lines = [line]
        for symbol in self._ordered_members:
            if Scope in symbol.__class__.__mro__:
                for line in symbol.prettyPrint(indent + "  ").split("\n"):
                    lines.append(line)
            else:
                lines.append("%s%s" % (indent + "  ", str(symbol)))
        return "\n".join(lines)

class Symbol(AbstractSymbol):
    """Implementation of an AbstractSymbol for use by language scanners.
    Symbols may also have an AbstractScannerContext when created by a scanner.
    """
    def __init__(self, name, type, ctx=None):
        """Creates a new symbol with the given string name, string type, and
        AbstractScannerContext (if any).
        @param name String name of the symbol.
        @param type String type of the symbol. This must be a string instead of
                    a Symbol because the scanner may not have encountered that
                    Symbol yet.
        @param ctx AbstractScannerContext of this symbol (if any). This is used
                   for things like scope resolution.
        """
        if not isinstance(name, str):
            raise TypeError("name must be a string (got '%s')" % name.__class__.__name__)
        self._name = name
        if not isinstance(type, (str, None.__class__)):
            raise TypeError("type must be a string or None (got '%s')" % type.__class__.__name__)
        self._type = type
        if not isinstance(ctx, (AbstractScannerContext, None.__class__)):
            raise TypeError("ctx must be an AbstractScannerContext or None (got '%s')" % ctx.__class__.__name__)
        self._ctx = ctx

    @property
    def name(self):
        """Implementation of AbstractSymbol.name."""
        return self._name

    @property
    def type(self):
        """Implementation of AbstractSymbol.type."""
        return self._type

    # Beginning of custom functions.

    @property
    def ctx(self):
        """Implementation of AbstractSymbol.ctx."""
        return self._ctx

    def __str__(self):
        """Returns a printable version of this symbol.
        Primarily used in pretty-printing scopes.
        @see Scope.prettyPrint()
        """
        return "%s: %s (%s)" % (self.__class__.__name__, self.name, self.type)

class BuiltIn(AbstractBuiltIn, Symbol, Scope):
    """Implementation of AbstractBuiltIn."""
    def __init__(self, name):
        """Creates a new built-in type symbol with the given string name.
        The resulting symbol has no type since it effectively is a type.
        @param name String name of the built-in type.
        """
        Symbol.__init__(self, name, None)
        Scope.__init__(self)

class Keyword(AbstractKeyword, Symbol):
    """Implementation of AbstractKeyword"""
    def __init__(self, name):
        """Creates a new keyword symbol with the given string name.
        The resulting symbol has no type.
        @param name String name of the keyword.
        """
        Symbol.__init__(self, name, None)

class Constant(AbstractConstant, Symbol):
    """Implementation of AbstractConstant."""
    def __init__(self, name, type, ctx=None):
        Symbol.__init__(self, name, type, ctx)

class Variable(AbstractVariable, Symbol):
    """Implementation of AbstractVariable."""
    def __init__(self, name, type, ctx=None):
        Symbol.__init__(self, name, type, ctx)

class GlobalVariable(AbstractGlobalVariable, Variable):
    """Implementation of AbstractGlobalVariable."""
    pass

class InstanceVariable(AbstractInstanceVariable, Variable):
    """Implementation of AbstractInstanceVariable."""
    pass

class PrivateInstanceVariable(AbstractPrivateInstanceVariable, Variable):
    """Implementation of AbstractPrivateInstanceVariable."""
    pass

class ProtectedInstanceVariable(AbstractProtectedInstanceVariable, Variable):
    """Implementation of AbstractProtectedInstanceVariable."""
    pass

class ClassVariable(AbstractClassVariable, Variable):
    """Implementation of AbstractClassVariable."""
    pass

class PrivateClassVariable(AbstractPrivateClassVariable, Variable):
    """Implementation of AbstractPrivateClassVariable."""
    pass

class ProtectedClassVariable(AbstractProtectedClassVariable, Variable):
    """Implementation of AbstractProtectedClassVariable."""
    pass

class SelfVariable(AbstractSelfVariable, Variable):
    """Implementation of AbstractSelfVariable"""
    pass

class Argument(AbstractArgument, Variable):
    """Implementation of AbstractArgument."""
    pass

class Struct(AbstractStruct, Symbol, Scope):
    """Implementation of AbstractStruct."""
    def __init__(self, name, type, enclosingScope=None, ctx=None):
        Symbol.__init__(self, name, type, ctx)
        Scope.__init__(self, enclosingScope)

class Class(AbstractClass, Struct):
    """Implementation of AbstractClass."""
    def __init__(self, name, enclosingScope=None, superclassName=None, ctx=None):
        """Creates a new class symbol with the given string name, enclosing
        scope (if any), string superclass name (if any), and
        AbstractScannerContext (if any).
        @param name String name of the class.
        @param enclosingScope AbstractScope this class is defined in (if any).
                              This is not the class' superclass.
        @param superclassName String name of this class' superclass, or list of
                              string superclass names (if any).
                              Superclass names must be strings instead of
                              Symbols because the scanner may not have
                              encountered those Symbols yet.
        @param ctx AbstractScannerContext of this symbol (if any). This is used
                   for things like scope resolution.
        """
        Symbol.__init__(self, name, None, ctx)
        Scope.__init__(self, enclosingScope)
        if superclassName:
            if isinstance(superclassName, (tuple, list)):
                for klass in superclassName:
                    if not isinstance(klass, str):
                        raise TypeError("all superclasses must be strings (one was '%s')" % klass.__class__.__name__)
            elif not isinstance(superclassName, str):
                raise TypeError("superclassName must be a string (got '%s')" % superclassName.__class__.__name__)
        self._superclassName = superclassName

    @property
    def superclassName(self):
        """Implementation of AbstractClass.superclassName"""
        return self._superclassName

    # Beginning of custom functions.

    def prettyPrint(self, indent=""):
        """Pretty-printer for this scope and its members.
        This is used for visual debugging only.
        @param indent The current indentation level for string output.
        @see Scope.prettyPrint()"""
        if not isinstance(self.superclass, (tuple, list)):
            superclass = self.superclass
        else:
            superclass = ", ".join(self.superclassName)
        return Scope.prettyPrint(self, indent).replace("\n", "(%s)\n" % superclass, 1)

class Interface(AbstractInterface, Class):
    """Implementation of AbstractInterface."""
    pass

class Function(AbstractFunction, Symbol, Scope):
    """Implementation of AbstractFunction."""
    def __init__(self, name, type, returnType, enclosingScope=None, ctx=None):
        """Creates a new function symbol with the given string name, symbol
        type, symbol return type, enclosing scope (if any), and
        AbstractScannerContext (if any).
        @param name String name of the function.
        @param type String type of the symbol.
        @param returnType String return type of the symbol. This must be a
                          string instead of a Symbol because the scanner may not
                          have encountered that Symbol yet.
        @param enclosingScope AbstractScope this function is defined in (if
                              any).
        @param ctx AbstractScannerContext of this symbol (if any). This is used
                   for things like scope resolution.
        """
        Symbol.__init__(self, name, type, ctx)
        Scope.__init__(self, enclosingScope)
        if not isinstance(returnType, (str, None.__class__)):
            raise TypeError("returnType must a string or None (got '%s')" % returnType.__class__.__name__)
        self._returnType = returnType

    @property
    def returnType(self):
        """Implementation of AbstractFunction.returnType."""
        return self._returnType

class AnonymousFunction(AbstractAnonymousFunction, Function):
    """Implementation of AbstractAnonymousFunction."""
    def __init__(self, type, returnType, enclosingScope=None, ctx=None):
        count = 1
        if enclosingScope:
            for member in list(enclosingScope.members.values()):
                if isinstance(member, AnonymousFunction):
                    count += 1
        Function.__init__(self, "(anonymous %d)" % count, type, returnType, enclosingScope, ctx)

class Constructor(AbstractConstructor, Function):
    """Implementation of AbstractConstructor."""
    pass

class Method(AbstractMethod, Function):
    """Implementation of AbstractMethod."""
    pass

class PrivateMethod(AbstractPrivateMethod, Function):
    """Implementation of AbstractPrivateMethod."""
    pass

class ProtectedMethod(AbstractProtectedMethod, Function):
    """Implementation of AbstractProtectedMethod."""
    pass

class StaticMethod(AbstractStaticMethod, Function):
    """Implementation of AbstractStaticMethod."""
    pass

class ClassMethod(AbstractClassMethod, Function):
    """Implementation of AbstractClassMethod."""
    pass

class Trait(AbstractTrait, Symbol, Scope):
    """Implementation of AbstractTrait."""
    def __init__(self, name, enclosingScope=None, ctx=None):
        Symbol.__init__(self, name, None, ctx)
        Scope.__init__(self, enclosingScope)

class Module(AbstractModule, Symbol, Scope):
    """Implementation of AbstractModule."""
    def __init__(self, name, enclosingScope=None, ctx=None):
        Symbol.__init__(self, name, None, ctx)
        Scope.__init__(self, enclosingScope)

class Namespace(AbstractNamespace, Symbol, Scope):
    """Implementation of AbstractNamespace."""
    def __init__(self, name, enclosingScope=None, ctx=None):
        Symbol.__init__(self, name, None, ctx)
        Scope.__init__(self, enclosingScope)

class Import(AbstractImport, Symbol):
    """Implementation of AbstractImport."""
    def __init__(self, name, type, ctx=None):
        """Creates an imported/included symbol with the given string name and
        type.
        @param name The name of the imported/included symbol in its scope. This
                    is not a fully-qualified name.
        @param type The fully-qualified name of the symbol to import/include.
        """
        Symbol.__init__(self, name, type, ctx)

class Element(AbstractElement, Symbol, Scope):
    """Implementation of AbstractElement."""
    def __init__(self, name, enclosingScope=None, ctx=None):
        Symbol.__init__(self, name, None, ctx)
        Scope.__init__(self, enclosingScope)

class Attribute(AbstractAttribute, Symbol, Scope):
    """Implementation of AbstractAttribute."""
    def __init__(self, name, enclosingScope=None, ctx=None):
        Symbol.__init__(self, name, None, ctx)
        Scope.__init__(self, enclosingScope)

class CSSClass(AbstractCSSClass, Element):
    """Implementation of AbstractCSSClass."""
    pass

class CSSId(AbstractCSSId, Element):
    """Implementation of AbstractCSSId."""
    pass

class CSSPseudoClass(AbstractCSSPseudoClass, Symbol):
    """Implementation of AbstractCSSPseudoClass."""
    def __init__(self, name):
        Symbol.__init__(self, name, None)

class AbstractScanner(object, metaclass=ABCMeta):
    """Scanner for a particular language's source code.
    Scanners should attach an implementation instance of AbstractScannerContext
    to each Symbol created in order for various context retrieval to function
    accurately.
    """

    def __init__(self, stdlib_file=None):
        """Initializes a language scanner with the given stdlib file.
        The stdlib file is used to produce a "builtInScope" that contains all of
        the language's built-in AbstractSymbols. Scanner implementations should
        make that scope the enclosingScope of any code scanned in order for
        built-in symbol resolution to work properly.
        @param stdlib_file Optional string filename in the database containing
                           the language's stdlib.
        """
        self._builtInScope = Scope()
        if stdlib_file:
            if fileExists(stdlib_file):
                log.debug("Loading stdlib %s", stdlib_file)
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
            log.warn("No built-in scope found. Call AbstractScanner.__init__().")
            self._builtInScope = Scope()
        return self._builtInScope

    def addToBuiltInScope(self, file):
        """Adds the symbols from the given file to the language's built-in
        symbols. This is useful for importing legacy CodeIntel catalogs like
        JavaScript's jQuery without needing (or attempting) to scan it manually
        and then import it.
        @param file String filename in the database containing the file to add.
        """
        if fileExists(file):
            log.debug("Adding %s to built-in scope", file)
            for symbol in fetchSymbolsInFile(file):
                if not isinstance(self._builtInScope.resolveMember(symbol.name), AbstractScope) or not isinstance(symbol, AbstractScope):
                    self._builtInScope.define(symbol)
                else:
                    self._builtInScope.resolveMember(symbol.name).merge(symbol)

    def readFile(self, filename):
        """Helper method for reading and returning as unicode the contents of
        the given filename.
        Scanners are encouraged to read the contents of files using this method
        in order to avoid handling file encoding issues.
        Any read errors are propagated to the caller.
        @param filename String filename to read. It is assumed this filename
                        exists.
        @return unicode file contents
        """
        f = open(filename)
        content = f.read()
        try:
            return str(content)
        except UnicodeDecodeError:
            return str(content.decode("iso-8859-1"))
        finally:
            f.close()

    @abstractmethod
    def scan(self, filename, env={}):
        """Scans the given source code file or source code, subject to the given
        environment, and returns an AbstractScope to write to the database.
        @param filename String filename or source code to scan.
        @param env Optional dictionary of environment variables to use when
                   scanning. If filename is source code, a "FILENAME" key points
                   to the source code's filename.
                   This dictionary is NOT a dictionary of system environment
                   variables (although it could contain them). It is a scanning
                   environment, a container of variables that language scanners
                   can reference during scan operations.
        @return AbstractScope
        @see builtInScope
        """
        pass

    @abstractmethod
    def getCompletionContext(self, filename, position, env={}):
        """Scans the given source code file or source code, subject to the given
        environment, and returns an AbstractCompletionContext for code
        completions at the given position, or returns None if no appropriate
        completion context exists.
        @param filename String filename or source code to scan.
        @param position Integer position to get the completion context for.
        @param env Optional dictionary of environment variables to use when
                   scanning. If filename is source code, a "FILENAME" key points
                   to the source code's filename.
                   This dictionary is NOT a dictionary of system environment
                   variables (although it could contain them). It is a scanning
                   environment, a container of variables that language scanners
                   can reference when fetching completions.
        @return AbstractCompletionContext or None
        """
        pass

    @abstractmethod
    def getGotoDefinitionContext(self, filename, position, env={}):
        """Scans the given source code file or source code, subject to the given
        environment, and returns a GotoDefinitionContext for the symbol at the
        given position, or returns None if no appropriate goto definition
        context exists.
        @param filename String filename or source code to scan.
        @param position Integer position to get the goto definition context for.
        @param env Optional dictionary of environment variables to use when
                   scanning. If filename is source code, a "FILENAME" key points
                   to the source code's filename.
                   This dictionary is NOT a dictionary of system environment
                   variables (although it could contain them). It is a scanning
                   environment, a container of variables that language scanners
                   can reference when fetching definitions.
        @return GotoDefinitionContext or None
        """
        pass

    @abstractmethod
    def getCallTipContext(self, filename, position, env={}):
        """Scans the given source code file or source code, subject to the given
        environment, and returns a CallTipContext for the symbol at the given
        position, or returns None if no appropriate call tip context exists.
        @param filename String filename or source code to scan.
        @param position Integer position to get the call tip context for.
        @param env Optional dictionary of environment variables to use when
                   scanning. If filename is source code, a "FILENAME" key points
                   to the source code's filename.
                   This dictionary is NOT a dictionary of system environment
                   variables (although it could contain them). It is a scanning
                   environment, a container of variables that language scanners
                   can reference when fetching call tips.
        @return CallTipContext or None
        """
        pass

    @abstractmethod
    def getFindReferencesContext(self, filename, position, env={}):
        """Scans the given source code file or source code, subject to the given
        environment, and returns an AbstractFindReferencesContext instance for
        the symbol at the given position, or returns None of no appropriate find
        references context exists.
        @param filename String filename or source code to scan.
        @param position Integer position to get the find references context for.
        @param env Optional dictionary of environment variables to use when
            scanning. If filename is source code, a "FILENAME" key points
            to the source code's filename.
            This dictionary is NOT a dictionary of system environment
            variables (although it could contain them). It is a scanning
            environment, a container of variables that language scanners
            can reference when fetching call tips.
        @return AbstractFindReferencesContext instance or None
        """
        pass

class AbstractScannerContext(AbstractSymbolContext):
    """The context for a Symbol that was produced by a language scanner.
    Scanner contexts provide additional context around symbols. This is used for
    things like scope resolution.
    @see Symbol.ctx
    """
    @property
    def filename(self):
        """Implementation of AbstractSymbolContext.filename.
        This will automatically be filled in by the database. In the meantime,
        it is understood that this is the name of the file being scanned.
        """
        return None

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation."""
        return None

    @property
    def signature(self):
        """Implementation of AbstractSymbolContext.signature."""
        return None

    @abstractmethod
    def contains(self, position_or_line):
        """Returns whether or not this context's Symbol contains the given
        integer position or line number.
        @param position Integer position or line number to test, depending on
                        the implementing class.
        @see Scope.resolveScope()
        """
        pass

class AbstractImportResolver(object, metaclass=ABCMeta):
    """The import resolver for a programming language source file.
    Import resolvers should be passed to AbstractCompletionContexts in order to
    resolve imports on-demand for completions.
    """

    def __init__(self, filename, env={}):
        """Creates a new import resolver for the given filename or source code,
        subject to the given environment.
        @param filename String filename or source code import resolutions will
                        take place in.
        @param env Optional dictionary of environment variables aiding in import
                   resolution. If filename is source code, a "FILENAME" key
                   points to the source code's filename.
                   This dictionary is NOT a dictionary of system environment
                   variables (although it could contain them). It is a resolving
                   environment, a container of variables that import resolvers
                   can reference when fetching imports.
        """
        if os.path.exists(filename):
            self._filename = filename
        elif env.get("FILENAME", ""): # client may assign None
            self._filename = env.get("FILENAME")
        else:
            self._filename = filename
            log.warn("filename for import resolver '%s' does not exist and was not specified by 'FILENAME' environment variable", self.__class__.__name__)
        self._env = env
        self._attempting_resolve = {} # for recursion detection

    @abstractmethod
    def resolveImport(self, import_symbol, scope):
        """Resolves the given AbstractImport symbol in-place within the given
        AbstractScope, with this resolver's filename and environment available
        for context.
        The given import symbol has already been removed from the given scope.
        @param import_symbol AbstractImport symbol to resolve.
        @param scope AbstractScope that contained import_symbol.
        """
        pass

    @property
    def filename(self):
        """The string filename to resolve imports in."""
        return self._filename

    @property
    def env(self):
        """Dictionary of environment variables that may aid in import
        resolution.
        """
        return self._env

    def _hasUnresolvedImports(self, scope):
        """Returns whether or not the given scope has any AbstractImport symbols
        in it.
        @param scope AbstractScope to check for unresolved imports in.
        """
        for symbol in list(scope.members.values()):
            if isinstance(symbol, AbstractImport):
                return True
        return False

    def resolveImports(self, scope):
        """Resolves all AbstractImport symbols in the given scope and its
        enclosing scopes.
        @param scope AbstractScope to resolve all AbstractImport symbols in.
        """
        if not isinstance(scope, AbstractScope):
            raise TypeError("scope must be an AbstractScope (got '%s')" % scope.__class__.__name__)

        log.debug("Resolving imports in %r", scope)
        if hasattr(scope, "name"):
            # When attempting to detect recursion, using scope as the key can be
            # unreliable since repeated database queries return different scope
            # objects. Key by class and name instead when possible.
            key = (scope.__class__, scope.name)
        else:
            key = scope
        if key in self._attempting_resolve:
            log.debug("Recursion detected. Halting.")
            return
        self._attempting_resolve[key] = True

        parent_scopes = []
        while scope:
            parent_scopes.insert(0, scope)
            scope = scope.enclosingScope
        for scope in parent_scopes:
            resolved = {}
            while self._hasUnresolvedImports(scope):
                # Note: use dict.items() instead of dict.items() because the
                # former allows runtime changes during iteration.
                # First resolve imports (as opposed to includes).
                log.debug("Resolving sub imports in %r", scope)
                for name, symbol in list(scope.members.items()):
                    if isinstance(symbol, AbstractImport) and not symbol.name.startswith("."):
                        if name in scope.members:
                            # It is possible that subclasses' resolveImport()
                            # methods may do additional processing that removes
                            # 'scope.members[name]' before this loop can process
                            # it. For example, Perl's import resolver invokes
                            # resolveImports for each imported symbol, which can
                            # process this scope again.
                            del scope.members[name]
                        if symbol.type not in resolved:
                            log.debug("Importing '%s'", symbol.type)
                            self.resolveImport(symbol, scope)
                            resolved[symbol.type] = True
                # Now resolve includes (which may not have been available
                # without the imports).
                log.debug("Resolving includes in %r", scope)
                for name, symbol in list(scope.members.items()):
                    if isinstance(symbol, AbstractImport):
                        if name in scope.members:
                            # It is possible that subclasses' resolveImport()
                            # methods may do additional processing that removes
                            # 'scope.members[name]' before this loop can process
                            # it. For example, Perl's import resolver invokes
                            # resolveImports for each imported symbol, which can
                            # process this scope again.
                            del scope.members[name]
                        if symbol.type not in resolved:
                            log.debug("Including '%s'", symbol.type)
                            self.resolveImport(symbol, scope)
                            resolved[symbol.type] = True
            if isinstance(scope, AbstractClass) and scope.superclass:
                # Resolve imports in superclass's enclosing scopes. This is
                # necessary in order to resolve superclasses of superclasses.
                if isinstance(scope.superclass, AbstractScope):
                    self.resolveImports(scope.superclass)
                elif isinstance(scope.superclass, (list, tuple)):
                    for superclass in scope.superclass:
                        if isinstance(superclass, AbstractScope):
                            self.resolveImports(superclass)

        del self._attempting_resolve[key]

class AbstractSyntaxDescription(object, metaclass=ABCMeta):
    """The basic syntax description for a language when resolving a
    fully-qualified symbol string to its resulting AbstractSymbol object.
    """

    @abstractproperty
    def symbolSeparator(self):
        """The string that delineates symbol parts.
        A fully-qualified symbol name is split using this separator and each
        component is resolved with respect to its predecessor.
        """
        pass

    @abstractproperty
    def callOperator(self):
        """The string that indicates a function call.
        The resolved type from a function call is the function's return type,
        not its inherent symbol type.
        """
        pass

    # TODO: variable prefixes?

class CommonSyntaxDescription(AbstractSyntaxDescription):
    """Implementation of AbstractSyntaxDescription for most programming
    languages.
    """
    @property
    def symbolSeparator(self):
        """Implementation of AbstractSyntaxDescription.symbolSeparator."""
        return (".", "::", "->", "\\")

    @property
    def callOperator(self):
        """Implementation of AbstractSyntaxDescription.callOperator."""
        return "()"

class FilenameSyntaxDescription(AbstractSyntaxDescription):
    """Implementation of AbstractSyntaxDescription for filenames.
    This is primarily used when resolving filenames (e.g. "require('foo/<|>')").
    """
    @property
    def symbolSeparator(self):
        """Implementation of AbstractSyntaxDescription.symbolSeparator."""
        return "/"

    @property
    def callOperator(self):
        """Implementation of AbstractSyntaxDescription.callOperator."""
        return None

class SymbolResolver(object):
    """Symbol resolver for fully-qualified symbol and type names."""
    def __init__(self, syntax_description=None, import_resolver=None):
        """Creates a new symbol resolver.
        @param syntax_description Optional AbstractSyntaxDescription class to
                                  use for parsing fully-qualified symbol and
                                  type names. The default value is
                                  CommonSyntaxDescription.
        @param import_resolver Optional AbstractImportResolver used to resolve
                               imports on-demand as they are encountered.
        """
        if syntax_description is not None and (type(syntax_description) != ABCMeta or not issubclass(syntax_description, AbstractSyntaxDescription)):
            raise TypeError("syntax_description must be a class derived from AbstractSyntaxDescription or None (got '%s')" % syntax_description.__class__.__name__)
        if not isinstance(import_resolver, (AbstractImportResolver, None.__class__)):
            raise TypeError("import_resolver must be derived from AbstractImportResolver or None (got '%s')" % import_resolver.__class__.__name__)
        self._syntax_description = (syntax_description or CommonSyntaxDescription)()
        self._import_resolver = import_resolver
        self._attempting_resolve = {} # for recursion detection

    def _resolveCall(self, scope, symbol_name):
        """Resolves a function call's return type (e.g. "list() -> list").
        @param scope The AbstractScope to resolve the function call in.
        @param symbol_name The string function call, including the call
                           operator.
        @usage self._resolveCall("list()", scope)
        """
        initial_scope = scope # store for any needed type resolutions

        # First, resolve the function symbol itself.
        scope = scope.resolve(symbol_name[:-len(self._syntax_description.callOperator)])
        log.debug("Resolved symbol part '%s' to %r", symbol_name, scope)

        # If the resolved symbol is not a function (e.g. a variable), it
        # should still be resolve-able since it is being called.
        if scope and not isinstance(scope, AbstractFunction) and scope.type:
            while scope and scope.type:
                log.debug("Resolving symbol type '%s'", scope.type)
                scope = self.resolve(isinstance(scope, AbstractScope) and scope or initial_scope, scope.type)

        # Now, fetch the resolved function's return type, then resolve it
        # (subject to the function's scope).
        if isinstance(scope, AbstractFunction):
            if not scope.returnType:
                log.debug("No return type found for function call. Aborting.")
                return None # cannot move forward
            log.debug("Resolving function return type '%s'", scope.returnType)
            scope = self.resolve(scope, scope.returnType)
            while scope and scope.type:
                scope = self.resolve(isinstance(scope, AbstractScope) and scope or initial_scope, scope.type)
            log.debug("Resolved function return type '%s' to %r", symbol_name, scope)

        return scope

    def resolve(self, scope, symbol_name):
        """Resolves the given fully-qualified string name to its AbstractSymbol
        in the given scope (or any of its enclosing or parent scopes) and
        returns that symbol (or None if no symbol was found).
        Note an extra step of fully resolving the returned symbol's type may be
        necessary since the symbol itself may be a variable or pointer to a
        scoped symbol (e.g. AbstractClass).
        @param scope AbstractScope to resolve a fully-qualified string name in.
        @param symbol_name Fully-qualified string name of the symbol to resolve.
        @usage resolver.resolve(scope, "mod_name.class_name") -> AbstractClass
        """
        if not isinstance(scope, AbstractScope):
            raise TypeError("scope must be derived from AbstractScope (got '%s')" % scope.__class__.__name__)
        if not isinstance(symbol_name, str):
            raise TypeError("symbol_name must be a string (got '%s')" % symbol_name.__class__.__name__)

        log.debug("Attempting to fully resolve symbol '%s' in %r", symbol_name, scope)
        if (scope, symbol_name) in self._attempting_resolve:
            log.debug("Recursion detected. Halting.")
            return None
        self._attempting_resolve[(scope, symbol_name)] = True
        initial_scope = scope # store for later use

        if self._import_resolver:
            self._import_resolver.resolveImports(scope)

        if not isinstance(self._syntax_description.symbolSeparator, (list, tuple)):
            name_parts = symbol_name.split(self._syntax_description.symbolSeparator)
        else:
            name_parts = re.split("|".join([re.escape(sep) for sep in self._syntax_description.symbolSeparator]), symbol_name)
        log.debug("Attempting to resolve symbol parts: %r", name_parts)

        if not name_parts[0] and len(name_parts) > 1:
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

        # Resolve the first part of the symbol name.
        if self._syntax_description.callOperator and name_parts[0].endswith(self._syntax_description.callOperator):
            scope = self._resolveCall(scope, name_parts[0])
        else:
            if not isinstance(scope, AbstractMethod) or not isinstance(scope.resolve(name_parts[0]), (AbstractStaticMethod, AbstractClassMethod, AbstractClassVariable)):
                scope = scope.resolve(name_parts[0])
            else:
                # If the resolution in an instance method is a class object,
                # look in an enclosing scope for a non-class object.
                while scope.enclosingScope:
                    if not isinstance(scope.enclosingScope.resolve(name_parts[0]), (AbstractStaticMethod, AbstractClassMethod, AbstractClassVariable)):
                        scope = scope.enclosingScope.resolve(name_parts[0])
                        break
                    scope = scope.enclosingScope
        log.debug("Resolved symbol part '%s' to %r", name_parts[0], scope)

        # Now resolve each additional part.
        for name_part in name_parts[1:]:
            # If the resolved symbol is a class constructor, it's likely
            # that the class itself should have been resolved, since classes
            # and constructors often share the same name.
            if isinstance(scope, AbstractConstructor):
                scope = scope.enclosingScope
            # If the currently resolved symbol is not a container, or if the
            # symbol does not contain the next symbol part, try resolving the
            # symbol's type in order to look in it.
            if scope and (not isinstance(scope, AbstractScope) or not scope.resolveMember(name_part)) and scope.type:
                log.debug("'%s' not found in symbol part. Resolving symbol type '%s' and looking there", name_part, scope.type)
                while scope and scope.type:
                    scope = self.resolve(isinstance(scope, AbstractScope) and scope or initial_scope, scope.type)
                # If the resolved symbol is a class constructor, it's likely
                # that the class itself should have been resolved, since classes
                # and constructors often share the same name.
                if isinstance(scope, AbstractConstructor):
                    scope = scope.enclosingScope
                log.debug("Resolved symbol type to %r", scope)

            if not scope or not isinstance(scope, AbstractScope):
                scope = None
                break
            elif self._import_resolver:
                self._import_resolver.resolveImports(scope)

            # Try to resolve the part.
            if self._syntax_description.callOperator and name_part.endswith(self._syntax_description.callOperator):
                scope = self._resolveCall(scope, name_part)
            else:
                scope = scope.resolveMember(name_part)
            log.debug("Resolved symbol part '%s' to %r", name_part, scope)

        # If the fully resolved symbol is a class constructor, it's likely that
        # the class itself should have been resolved, since classes and
        # constructors often share the same name.
        if isinstance(scope, AbstractConstructor):
            scope = scope.enclosingScope

        log.debug("Fully resolved symbol '%s' to %r", symbol_name, scope)
        del self._attempting_resolve[(initial_scope, symbol_name)]
        return scope
