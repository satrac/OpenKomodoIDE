from abc import ABCMeta, abstractmethod
class abstractstaticmethod(staticmethod):
    """A decorator indicating abstract staticmethods."""
    __isabstractmethod__ = True
    def __init__(self, callable):
        callable.__isabstractmethod__ = True
        super(abstractstaticmethod, self).__init__(callable)

from peewee import *
from playhouse.sqlite_ext import ClosureTable

from db import Database
from db.model import Model, File

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

class Symbol(Model):

    parent = ForeignKeyField('self', index=True, null=True)

    file = ForeignKeyField(File, index=True)
    name = CharField()

    # eg. function, variable, property, ..
    symbol_type = CharField()

    # comma separated (if multiple), for vars this would be the var type, for functions this would be the return type
    type_hint = CharField(null=True)

    line = IntegerField(null=True)

    @classmethod
    def Closure(cls):
        return SymbolClosure

    class Meta:
        database = Database.conn
        only_save_dirty = True

        indexes = (
            (('parent', 'file'), False),
            (('name', 'symbol_type', 'parent'), False),
        )

    @classmethod
    def descendants(self, node, depth=None):

        if not node:
            return None

        Closure = self.Closure()

        if depth:
            depthExpression = Closure.depth == depth
        else:
            depthExpression = Closure.depth > 0

        closureQuery = (Closure.select(Closure.id)
                        .where(
                            (Closure.root == node) &
                            (depthExpression)
                        ))

        return (self
               .select(Symbol, File)
               .join(File)
               .where(
                   (Symbol.id << closureQuery)
                ))

    @classmethod
    def ancestors(self, node, depth=None):

        if not node:
            return None

        Closure = self.Closure()

        if depth:
            depthExpression = Closure.depth == depth
        else:
            depthExpression = Closure.depth > 0

        closureQuery = (Closure.select(Closure.id)
                        .where(
                            (Closure.root == node) &
                            (SQL("idcolumn = 'parent_id'")) &
                            (SQL("parentcolumn = 'id'")) &
                            (depthExpression)
                        ))

        return (self
               .select(Symbol, File)
               .join(File)
               .where(
                   (Symbol.id << closureQuery)
                ))

    @classmethod
    def siblings(self, node):

        if not node:
            return None

        Closure = self.Closure()

        closureQuery = (Closure.select(Closure.id)
                        .where(
                            (Closure.root == node.parent_id) &
                            (Closure.depth > 0) &
                            (Closure.id != node.id)
                        ))

        return (self
               .select(Symbol, File)
               .join(File)
               .where(
                   (Symbol.id << closureQuery)
                ))

    @classmethod
    def _getSymbolTypeMapping(cls):
        """Returns a mapping of AbstractSymbol subclasses to their string
        database identifiers and a mapping of string database identifiers to
        their _Symbol subclasses.
        This mapping is based solely on AbstractSymbol subclasses defined in
        this file's scope.
        @see _Symbol.getSymbolTypeID
        """
        if not hasattr(cls, "_symbolTypeMapping"):
            cls._symbolTypeMapping = {}
            klasses = [v for v in list(globals().values()) if type(v) == ABCMeta and issubclass(v, AbstractSymbol) and v != AbstractSymbol and hasattr(v, "getSymbolTypeID")]
            for klass in klasses:
                cls._symbolTypeMapping[klass.__mro__[1]] = klass.getSymbolTypeID()
                cls._symbolTypeMapping[klass.getSymbolTypeID()] = klass
        return cls._symbolTypeMapping

    @classmethod
    def getSymbolType(cls, symbol_cls):
        """Returns the string database identifier for the given AbstractSymbol
        subclass.
        @param symbol_cls AbstractSymbol subclass. This must be a class object,
                          not a symbol instance.
        @usage Symbol.getSymbolType(AbstractClass) -> "CLS"
        @usage Symbol.getSymbolType(_Class) -> "CLS"
        @usage Symbol.getSymbolType(language.common.Class) -> "CLS"
        """
        if type(symbol_cls) != ABCMeta or not issubclass(symbol_cls, AbstractSymbol) or symbol_cls == AbstractSymbol:
            raise TypeError("symbol must be a class derived from AbstractSymbol ('%s' received)" % symbol_cls.__class__.__name__)
        mapping = cls._getSymbolTypeMapping()
        symbol_type = mapping.get(symbol_cls, mapping.get(symbol_cls.__mro__[1]))
        if not symbol_type:
            raise TypeError("AbstractSymbol subclass '%s' is not an immediate subclass of an AbstractSymbol subclass" % symbol_cls.__class__.__name__)
        return symbol_type

    def toAbstractSymbol(self):
        """Returns an AbstractSymbol representation of this database Symbol.
        Based on "Symbol.getSymbolClass()", instantiates one of the "Abstract*"
        implementation classes defined later in this file.
        """
        klass = Symbol._getSymbolTypeMapping().get(self.symbol_type)
        if not klass:
            raise TypeError("Database Symbol '%s' has unexpected symbol_type; expecting a type from 'db.model.symbol' (got '%s')" % (self.name, self.symbol_type))
        return klass(self)

SymbolClosure = ClosureTable(Symbol)

class _Scope(AbstractScope):
    """Implementation of an AbstractScope for database symbols.
    These symbols have lazy properties that query the underlying database.
    """
    def __init__(self, symbol):
        """Creates a new scope with the given enclosing scope.
        @param symbol The database Symbol that represents this scope.
        """
        if not isinstance(symbol, Symbol):
            raise TypeError("symbol must be a database Symbol (got '%s')" % symbol.__class__.__name__)
        self._symbol = symbol
        # Lazy properties to be computed on access.
        self._enclosingScope = False # unset; cannot use None since it is a valid value
        self._members = False # unset; cannot use None since it is a valid value

    def define(self, symbol, alias=None):
        """Implementation of AbstractScope.define().
        While Symbol definition is supported, the database is NOT updated.
        Runtime definition of Symbols in database Scopes is useful when
        resolving imports, appending additional member completions, etc.
        """
        if not isinstance(symbol, AbstractSymbol):
            raise TypeError("symbol must be derived from AbstractSymbol (got '%s')" % symbol.__class__.__name__)
        name = alias or symbol.name
        if name in self.members:
            return # TODO: which symbol to keep?
        self.members[name] = symbol

    @property
    def enclosingScope(self):
        """Implementation of AbstractScope.enclosingScope.
        Queries the database if necessary.
        """
        if self._enclosingScope is False:
            parent = Symbol.ancestors(self._symbol, 1).first()
            if parent:
                parent = parent.toAbstractSymbol()
            else:
                parent = _FileScope(self._symbol.file)
            self._enclosingScope = parent
        return self._enclosingScope

    @property
    def members(self):
        """Implementation of AbstractScope.members.
        Queries the database if necessary.
        """
        if self._members is False:
            self._members = {}
            for member in Symbol.descendants(self._symbol, 1).iterator():
                self._members[member.name] = member.toAbstractSymbol()
        return self._members

    @property
    def parentScope(self):
        """Implementation of AbstractScope.parentScope."""
        return self.enclosingScope

class _FileScope(AbstractScope):
    """Implementation of an AbstractScope for database files.
    This is particularly needed for standard library files, as their symbols may
    refer to one another and need a shared enclosing scope for proper symbol
    resolution.
    These scopes have lazy properties that query the underlying database.
    """
    def __init__(self, file):
        if not isinstance(file, File):
            raise TypeError("file must be a database File (got '%s')" % file.__class__.__name__)
        self._file = file
        # Lazy properties to be computed on access.
        self._members = False # unset; cannot use None since it is a valid value

    def define(self, symbol, alias=None):
        """Implementation of AbstractScope.define().
        While Symbol definition is supported, the database is NOT updated.
        Runtime definition of Symbols in database FileScopes is particularly
        useful when resolving imports.
        """
        if not isinstance(symbol, AbstractSymbol):
            raise TypeError("symbol must be derived from AbstractSymbol (got '%s')" % symbol.__class__.__name__)
        name = alias or symbol.name
        if name in self.members:
            return # TODO: which symbol to keep?
        self.members[name] = symbol

    @property
    def enclosingScope(self):
        """Implementation of AbstractScope.enclosingScope.
        File scopes do not have enclosing scopes.
        """
        return None

    @property
    def members(self):
        """Implementation of AbstractScope.members.
        Queries the database if necessary.
        """
        if self._members is False:
            self._members = {}
            for member in Symbol.select(Symbol, File).join(File).where(Symbol.file == self._file, Symbol.parent == None).iterator():
                self._members[member.name] = member.toAbstractSymbol()
        return self._members

    @property
    def parentScope(self):
        """Implementation of AbstractScope.parentScope."""
        return self.enclosingScope

class _Symbol(AbstractSymbol):
    """Implementation of an AbstractSymbol for database symbols."""
    def __init__(self, symbol):
        """Creates a new symbol from the given database Symbol.
        @param symbol The database symbol that represents this _Symbol.
        """
        if not isinstance(symbol, Symbol):
            raise TypeError("symbol must be a database Symbol (got '%s')" % symbol.__class__.__name__)
        self._name = symbol.name
        self._type = symbol.type_hint
        self._ctx = _SymbolContext(symbol)

    @property
    def name(self):
        """Implementation of AbstractSymbol.name."""
        return self._name

    @property
    def type(self):
        """Implementation of AbstractSymbol.type."""
        return self._type

    @property
    def ctx(self):
        """Implementation of AbstractSymbol.ctx."""
        return self._ctx

    @abstractstaticmethod
    def getSymbolTypeID():
        """Returns the string identifier of an AbstractSymbol subclass's type in
        a database.
        This has nothing to do with the type instance property.
        String representations ideally would be 8 characters (8 bytes) or less
        since typical databases use 64-bit (8-byte) integers by default and
        being conscious of space is a good thing.
        Perhaps <8-byte integers could be used (if the current database supports
        it), but that would make database inspection by hand a bit more
        difficult.
        This method should be a @staticmethod.
        """
        pass

class _SymbolContext(AbstractSymbolContext):
    """Implementation of an AbstractSymbolContext for database symbols."""
    def __init__(self, symbol):
        """Creates a new symbol context from the given database Symbol.
        @param symbol The database symbol that represents this _SymbolContext.
        """
        if not isinstance(symbol, Symbol):
            raise TypeError("symbol must be a database Symbol (got '%s')" % symbol.__class__.__name__)
        self._symbol = symbol
        self._line = symbol.line
        # Lazy properties to be computed on access.
        self._filename = None
        self._documentation = False # unset; cannot use None since it is a valid value
        self._signature = False # unset; cannot use None since it is a valid value

    @property
    def filename(self):
        """Implementation of AbstractSymbolContext.filename.
        Queries the database if necessary.
        """
        if not self._filename:
            self._filename = self._symbol.file.path
        return self._filename

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        return self._line

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation.
        Queries the database if necessary.
        """
        if self._documentation is False:
            try:
                from db.model import Doc
                self._documentation = Doc.get(Doc.symbol == self._symbol.id).summary
            except Doc.DoesNotExist:
                self._documentation = None
        return self._documentation

    @property
    def signature(self):
        """Implementation of AbstractSymbolContext.signature.
        Queries the database if necessary.
        """
        if self._signature is False:
            try:
                from db.model import Doc
                self._signature = Doc.get(Doc.symbol == self._symbol.id).signature
            except Doc.DoesNotExist:
                self._signature = None
        return self._signature

class _BuiltIn(AbstractBuiltIn, _Symbol, _Scope):
    """Implementation of AbstractBuiltIn."""
    def __init__(self, symbol):
        """Creates a new built-in type symbol from the given database Symbol.
        @param symbol The database symbol that represents this _Symbol.
        """
        _Symbol.__init__(self, symbol)
        _Scope.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "BI"

class _Keyword(AbstractKeyword, _Symbol):
    """Implementation of AbstractKeyword"""
    def __init__(self, symbol):
        """Creates a new keyword symbol from the given database Symbol.
        @param symbol The database symbol that represents this _Symbol.
        """
        _Symbol.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "KW"

class _Constant(AbstractConstant, _Symbol):
    """Implementation of AbstractConstant."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "CNST"

class _Variable(AbstractVariable, _Symbol):
    """Implementation of AbstractVariable."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "VAR"

class _GlobalVariable(AbstractGlobalVariable, _Variable):
    """Implementation of AbstractGlobalVariable"""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "GVAR"

class _InstanceVariable(AbstractInstanceVariable, _Variable):
    """Implementation of AbstractInstanceVariable"""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "IVAR"

class _PrivateInstanceVariable(AbstractPrivateInstanceVariable, _Variable):
    """Implementation of AbstractPrivateInstanceVariable."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "PVIV"

class _ProtectedInstanceVariable(AbstractProtectedInstanceVariable, _Variable):
    """Implementation of AbstractProtectedInstanceVariable."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "PTIV"

class _ClassVariable(AbstractClassVariable, _Variable):
    """Implementation of AbstractClassVariable."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "CVAR"

class _PrivateClassVariable(AbstractPrivateClassVariable, _Variable):
    """Implementation of AbstractClassVariable."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "PVCV"

class _ProtectedClassVariable(AbstractProtectedClassVariable, _Variable):
    """Implementation of AbstractProtectedClassVariable."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "PTCV"

class _SelfVariable(AbstractSelfVariable, _Variable):
    """Implementation of AbstractSelfVariable."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "SELF"

class _Argument(AbstractArgument, _Variable):
    """Implementation of AbstractArgument."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "ARG"

class _Struct(AbstractStruct, _Symbol, _Scope):
    """Implementation of AbstractStruct"""
    def __init__(self, symbol):
        """Creates a new object symbol from the given database Symbol.
        @param symbol The database symbol that represents this _Symbol.
        """
        _Symbol.__init__(self, symbol)
        _Scope.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "STRU"

class _Class(AbstractClass, _Struct):
    """Implementation of AbstractClass."""
    def __init__(self, symbol):
        """Creates a new class symbol from the given database Symbol.
        @param symbol The database symbol that represents this _Symbol.
        """
        _Struct.__init__(self, symbol)
        if self._type:
            # Superclass name is stored in type field.
            if "," not in self._type:
                self._superclassName = self._type
            else:
                self._superclassName = self._type.split(",")
            self._type = None
        else:
            self._superclassName = None

    @property
    def superclassName(self):
        """Implementation of AbstractClass.superclassName"""
        return self._superclassName

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "CLS"

class _Interface(AbstractInterface, _Class):
    """Implementation of AbstractInterface."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "IFC"

class _Function(AbstractFunction, _Symbol, _Scope):
    """Implementation of AbstractFunction."""
    def __init__(self, symbol):
        """Creates a new function symbol from the given database Symbol.
        @param symbol The database symbol that represents this _Symbol.
        """
        _Symbol.__init__(self, symbol) # TODO: proper type?
        _Scope.__init__(self, symbol)
        self._returnType = symbol.type_hint

    @property
    def returnType(self):
        """Implementation of AbstractFunction.returnType."""
        return self._returnType

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "FUN"

class _AnonymousFunction(AbstractAnonymousFunction, _Function):
    """Implementation of AbstractAnonymousFunction."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "AFUN"

class _Contructor(AbstractConstructor, _Function):
    """Implementation of AbstractConstructor."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "CTOR"

class _Method(AbstractMethod, _Function):
    """Implementation of AbstractMethod."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "MTH"

class _PrivateMethod(AbstractPrivateMethod, _Function):
    """Implementation of AbstractPrivateMethod."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "PMTH"

class _ProtectedMethod(AbstractProtectedMethod, _Function):
    """Implementation of AbstractProtectedMethod."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "RMTH"

class _StaticMethod(AbstractStaticMethod, _Function):
    """Implementation of AbstractStaticMethod."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "SMTH"

class _ClassMethod(AbstractClassMethod, _Function):
    """Implementation of AbstractClassMethod."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "CMTH"

class _Trait(AbstractTrait, _Symbol, _Scope):
    """Implementation of AbstractTrait."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)
        _Scope.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "TRT"

class _Module(AbstractModule, _Symbol, _Scope):
    """Implementation of AbstractModule."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)
        _Scope.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "MOD"

class _Namespace(AbstractNamespace, _Symbol, _Scope):
    """Implementation of AbstractNamespace."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)
        _Scope.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "NMSP"

class _Import(AbstractImport, _Symbol):
    """Implementation of AbstractImport."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "IMP"

class _Element(AbstractElement, _Symbol, _Scope):
    """Implementation of AbstractElement."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)
        _Scope.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "ELEM"

class _Attribute(AbstractAttribute, _Symbol, _Scope):
    """Implementation of AbstractAttribute."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)
        _Scope.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "ATTR"

class _CSSClass(AbstractCSSClass, _Element):
    """Implementation of AbstractCSSClass."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "CSSC"

class _CSSId(AbstractCSSId, _Element):
    """Implementation of AbstractCSSId."""
    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "CSSI"

class _CSSPseudoClass(AbstractCSSPseudoClass, _Symbol):
    """Implementation of AbstractCSSPseudoClass."""
    def __init__(self, symbol):
        _Symbol.__init__(self, symbol)

    @staticmethod
    def getSymbolTypeID():
        """Implementation of _Symbol.getSymbolTypeID()."""
        return "CSSP"
