# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanners for Python source code.
CodeIntel v2's original Python parsers have been adapted to operate with the new
CodeIntel v3 framework.
The Python 2 parser uses Python's own (deprecated) "compiler" library for
producing an Abstract Syntax Tree that is walked in order to create objects for
database storage.
The Python 3 parser looks for Python 3-specific code via a series of regular
expressions, and "translates" it on the fly to Python 2 code, before feeding it
into the Python 2 parser.
Completion contexts do not make use of Python's "compiler" library, since it is
quite fragile. Instead, the input stream is tokenized using the Scintilla Python
lexer (via SilverCity), and the resultant token list is analyzed.
"""

import logging
import os
import re
import sys

import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=DeprecationWarning)
    import compiler
from compiler import ast
from compiler.visitor import dumpNode, ExampleASTVisitor
import parser
from io import StringIO

from symbols import AbstractClass, AbstractModule
from calltips import CallTipContext
from completions import AbstractMemberCompletionContext, AbstractScopeCompletionContext
from goto_definition import GotoDefinitionContext
from find_references import AbstractFindReferencesContext
from language.common import Scope, Class, Function, Constructor, Method, StaticMethod, ClassMethod, PrivateMethod, ProtectedMethod, Argument, Variable, InstanceVariable, PrivateInstanceVariable, ProtectedInstanceVariable, ClassVariable, PrivateClassVariable, ProtectedClassVariable, Module, Import, AbstractScanner, AbstractScannerContext, AbstractImportResolver, CommonSyntaxDescription, SymbolResolver
from language.legacy.common import util
from language.legacy.python import tdparser
from language.legacy.python.import_resolver import PythonImportResolver, Python3ImportResolver

from db.model.helpers import fileExists, fetchSymbolsInFile, fetchAllFilesInDirectory

import SilverCity
from SilverCity.Lexer import Lexer
from SilverCity import ScintillaConstants
from SilverCity.Keywords import python_keywords
from SilverCity.ScintillaConstants import SCE_P_DEFAULT, SCE_P_IDENTIFIER, SCE_P_OPERATOR, SCE_P_WORD, SCE_P_CLASSNAME, SCE_P_DEFNAME, SCE_P_STRING, SCE_P_CHARACTER, SCE_P_DECORATOR, SCE_P_TRIPLE, SCE_P_TRIPLEDOUBLE

log = logging.getLogger("codeintel.python")
#log.setLevel(logging.DEBUG)

#---- exceptions

class PythonCILEError(Exception):
    pass

#---- internal routines and classes

def _isclass(namespace):
    return (len(namespace["types"]) == 1
            and "class" in namespace["types"])

def _isfunction(namespace):
    return (len(namespace["types"]) == 1
            and "function" in namespace["types"])

class PythonScannerContext(AbstractScannerContext):
    """Implementation of AbstractScannerContext for the Python parser."""
    def __init__(self, node):
        self._node = node

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        if "line" in self._node:
            return self._node["line"]
        else:
            return None

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation."""
        return "doc" in self._node and self._node["doc"] or None

    @property
    def signature(self):
        """Implementation of AbstractSymbolContext.signature."""
        return "signature" in self._node and self._node["signature"] or None

    def contains(self, line):
        """Implementation of AbstractScannerContext.contains()."""
        if self.line is None:
            return False
        return line >= self.line and ("lineend" not in self._node or line <= self._node["lineend"])

class AST2CIXVisitor:
    """Generate Code Intelligence XML (CIX) from walking a Python AST tree.

    This just generates the CIX content _inside_ of the <file/> tag. The
    prefix and suffix have to be added separately.

    Note: All node text elements are encoded in UTF-8 format by the Python AST
          tree processing, no matter what encoding is used for the file's
          original content. The generated CIX XML will also be UTF-8 encoded.
    """
    DEBUG = 0
    def __init__(self, moduleName=None, content=None, lang="Python"):
        self.lang = lang
        if self.DEBUG is None:
            self.DEBUG = log.isEnabledFor(logging.DEBUG)
        self.moduleName = moduleName
        if content:
            self.lines = content.splitlines(0)
        else:
            self.lines = None
        # Symbol Tables (dicts) are built up for each scope. The namespace
        # stack to the global-level is maintain in self.nsstack.
        self.st = { # the main module symbol table
            # <scope name>: <namespace dict>
        }
        self.nsstack = []
        #self.cix = et.TreeBuilder()

    def emit_start(self, s, attrs={}):
        self.cix.start(s, _et_attrs(attrs))

    def emit_data(self, data):
        self.cix.data(_et_data(data))

    def emit_end(self, s):
        self.cix.end(s)

    def emit_tag(self, s, attrs={}, data=None):
        self.emit_start(s, _et_attrs(attrs))
        if data is not None:
            self.emit_data(data)
        self.emit_end(s)

    def cix_module(self, node):
        """Emit CIX for the given module namespace."""
        #log.debug("cix_module(%s, level=%r)", '.'.join(node["nspath"]), level)
        assert len(node["types"]) == 1 and "module" in node["types"]
        attrs = _node_attrs(node, lang=self.lang, ilk="blob")
        module = self.emit_start('scope', attrs)
        for import_ in node.get("imports", []):
            self.cix_import(import_)
        self.cix_symbols(node["symbols"])
        self.emit_end('scope')

    def cix_import(self, node):
        #log.debug("cix_import(%s, level=%r)", node["module"], level)
        attrs = node
        self.emit_tag('import', attrs)

    def cix_symbols(self, node, parentIsClass=0):
        # Sort variables by line order. This provide the most naturally
        # readable comparison of document with its associate CIX content.
        vars = sorted(list(node.values()), key=lambda v: v.get("line"))
        for var in vars:
            self.cix_symbol(var, parentIsClass)

    def cix_symbol(self, node, parentIsClass=0):
        if _isclass(node):
            self.cix_class(node)
        elif _isfunction(node):
            self.cix_function(node)
        else:
            self.cix_variable(node, parentIsClass)

    def cix_variable(self, node, parentIsClass=0):
        #log.debug("cix_variable(%s, level=%r, parentIsClass=%r)",
        #          '.'.join(node["nspath"]), level, parentIsClass)
        attrs = _node_attrs(node, citdl=_node_citdl(node))
        if parentIsClass and "is-class-var" not in node:
            # Special CodeIntel <variable> attribute to distinguish from the
            # usual class variables.
            if attrs["attributes"]:
                attrs["attributes"] += " __instancevar__"
            else:
                attrs["attributes"] = "__instancevar__"
        self.emit_tag('variable', attrs)

    def cix_class(self, node):
        #log.debug("cix_class(%s, level=%r)", '.'.join(node["nspath"]), level)

        if node["classrefs"]:
            citdls = (t for t in (_node_citdl(n) for n in node["classrefs"])
                        if t is not None)
            classrefs = " ".join(citdls)
        else:
            classrefs = None

        attrs = _node_attrs(node,
                            lineend=node.get("lineend"),
                            signature=node.get("signature"),
                            ilk="class",
                            classrefs=classrefs)

        self.emit_start('scope', attrs)

        for import_ in node.get("imports", []):
            self.cix_import(import_)

        self.cix_symbols(node["symbols"], parentIsClass=1)

        self.emit_end('scope')

    def cix_argument(self, node):
        #log.debug("cix_argument(%s, level=%r)", '.'.join(node["nspath"]), level)
        attrs = _node_attrs(node, citdl=_node_citdl(node), ilk="argument")
        self.emit_tag('variable', attrs)

    def cix_function(self, node):
        #log.debug("cix_function(%s, level=%r)", '.'.join(node["nspath"]), level)
        # Determine the best return type.
        best_citdl = None
        max_count = 0
        for citdl, count in list(node["returns"].items()):
            if count > max_count:
                best_citdl = citdl

        attrs = _node_attrs(node,
                            lineend=node.get("lineend"),
                            returns=best_citdl,
                            signature=node.get("signature"),
                            ilk="function")

        self.emit_start("scope", attrs)

        for import_ in node.get("imports", []):
            self.cix_import(import_)
        argNames = []
        for arg in node["arguments"]:
            argNames.append(arg["name"])
            self.cix_argument(arg)
        symbols = {} # don't re-emit the function arguments
        for symbolName, symbol in list(node["symbols"].items()):
            if symbolName not in argNames:
                symbols[symbolName] = symbol
        self.cix_symbols(symbols)
        #XXX <returns/> if one is defined
        self.emit_end('scope')

    def getCIX(self, path):
        """Return CIX content for parsed data."""
        log.debug("getCIX")
        moduleNS = self.st[()]
        self.emit_start('file', dict(lang=self.lang, path=path))
        self.cix_module(moduleNS)
        self.emit_end('file')
        file = self.cix.close()
        return file

    def scope_module(self, node, enclosingScope=None):
        """Emit Scope for the given module namespace."""
        assert len(node["types"]) == 1 and "module" in node["types"]
        symbol = Module(node["name"], self._currentScope, PythonScannerContext(node))
        self._currentScope.define(symbol)
        self._currentScope = symbol
        for import_ in node.get("imports", []):
            self.scope_import(import_)
        self.scope_symbols(node["symbols"])
        self._currentScope = self._currentScope.enclosingScope

    def scope_import(self, node):
        module_name = node["module"]
        name = node.get("symbol", module_name.split(".")[0]) # only import top-level module name
        if "symbol" in node:
            if name != "*":
                module_name = "%s.%s" % (module_name, name)
            else:
                name = "%s.*" % module_name
        if "alias" in node:
            name = node["alias"]
        symbol = Import(name, module_name, PythonScannerContext(node))
        self._currentScope.define(symbol)

    def scope_symbols(self, node, parentIsClass=0):
        # Sort variables by line order. This provide the most naturally
        # readable comparison of document with its associate CIX content.
        vars = sorted(list(node.values()), key=lambda v: v.get("line"))
        for var in vars:
            self.scope_symbol(var, parentIsClass)

    def scope_symbol(self, node, parentIsClass=0):
        if _isclass(node):
            self.scope_class(node)
        elif _isfunction(node):
            self.scope_function(node)
        else:
            self.scope_variable(node, parentIsClass)

    def scope_variable(self, node, parentIsClass):
        best_citdl = None
        max_count = 0
        for citdl, count in list(node["types"].items()):
            if count > max_count and citdl:
                best_citdl = citdl.split()[0]

        if parentIsClass:
            if "is-class-var" in node:
                if "private" in node["attributes"]:
                    symbol = PrivateClassVariable(node["name"], best_citdl, PythonScannerContext(node))
                elif "protected" in node["attributes"]:
                    symbol = ProtectedClassVariable(node["name"], best_citdl, PythonScannerContext(node))
                else:
                    if "property" not in node["attributes"]:
                        symbol = ClassVariable(node["name"], best_citdl, PythonScannerContext(node))
                    else:
                        symbol = Method(node["name"], "property", best_citdl, self._currentScope, PythonScannerContext(node))
            elif "private" in node["attributes"]:
                symbol = PrivateInstanceVariable(node["name"], best_citdl, PythonScannerContext(node))
            elif "protected" in node["attributes"]:
                symbol = ProtectedInstanceVariable(node["name"], best_citdl, PythonScannerContext(node))
            else:
                symbol = InstanceVariable(node["name"], best_citdl, PythonScannerContext(node))
        else:
            symbol = Variable(node["name"], best_citdl, PythonScannerContext(node))
        self._currentScope.define(symbol)

    def scope_class(self, node):
        if node["classrefs"]:
            classrefs = [n["name"] for n in node["classrefs"]]
            if len(classrefs) > 1:
                symbol = Class(node["name"], self._currentScope, classrefs, PythonScannerContext(node))
            else:
                symbol = Class(node["name"], self._currentScope, classrefs[0], PythonScannerContext(node))
        else:
            symbol = Class(node["name"], self._currentScope, None, PythonScannerContext(node))
        self._currentScope.define(symbol)
        self._currentScope = symbol

        for import_ in node.get("imports", []):
            self.scope_import(import_)

        self.scope_symbols(node["symbols"], parentIsClass=1)

        self._currentScope = self._currentScope.enclosingScope

    def scope_argument(self, node):
        best_citdl = None
        max_count = 0
        for citdl, count in list(node["types"].items()):
            if count > max_count:
                best_citdl = citdl

        symbol = Argument(node["name"], best_citdl, PythonScannerContext(node))
        self._currentScope.define(symbol)

    def scope_function(self, node):
        best_citdl = None
        max_count = 0
        for citdl, count in list(node["returns"].items()):
            if count > max_count:
                best_citdl = citdl

        if isinstance(self._currentScope, Class):
            if "__ctor__" in node["attributes"]:
                symbol = Constructor(node["name"], "function", self._currentScope.name, self._currentScope, PythonScannerContext(node))
            elif "__staticmethod__" in node["attributes"]:
                symbol = StaticMethod(node["name"], "function", best_citdl, self._currentScope, PythonScannerContext(node))
            elif "__classmethod__" in node["attributes"]:
                symbol = ClassMethod(node["name"], "function", best_citdl, self._currentScope, PythonScannerContext(node))
            elif "private" in node["attributes"]:
                symbol = PrivateMethod(node["name"], "function", best_citdl, self._currentScope, PythonScannerContext(node))
            elif "protected" in node["attributes"]:
                symbol = ProtectedMethod(node["name"], "function", best_citdl, self._currentScope, PythonScannerContext(node))
            else:
                if "(property " not in node["name"]:
                    symbol = Method(node["name"], "function", best_citdl, self._currentScope, PythonScannerContext(node))
                else:
                    symbol = Method(node["name"].split()[0], "property", best_citdl, self._currentScope, PythonScannerContext(node))
        else:
            symbol = Function(node["name"], "function", best_citdl, self._currentScope, PythonScannerContext(node))
        self._currentScope.define(symbol)
        self._currentScope = symbol

        for import_ in node.get("imports", []):
            self.scope_import(import_)
        argNames = []
        for arg in node["arguments"]:
            argNames.append(arg["name"])
            self.scope_argument(arg)
        symbols = {} # don't re-emit the function arguments
        for symbolName, symbol in list(node["symbols"].items()):
            if symbolName not in argNames:
                symbols[symbolName] = symbol
        self.scope_symbols(symbols)

        self._currentScope = self._currentScope.enclosingScope

    def getScope(self, enclosingScope):
        self.scope = Scope(enclosingScope)
        self._currentScope = self.scope
        moduleNS = self.st[()]
        #self.scope_module(moduleNS)
        for import_ in moduleNS.get("imports", []):
            self.scope_import(import_)
        self.scope_symbols(moduleNS["symbols"])
        return self.scope

    def visitModule(self, node):
        log.debug("visitModule")
        nspath = ()
        namespace = {"name": self.moduleName,
                     "nspath": nspath,
                     "types": {"module": 1},
                     "symbols": {}}
        if node.doc:
            summarylines = util.parseDocSummary(node.doc.splitlines(0))
            namespace["doc"] = "\n".join(summarylines)

        if node.lineno:
            namespace["line"] = node.lineno

        self.st[nspath] = namespace
        self.nsstack.append(namespace)
        self.visit(node.node)
        self.nsstack.pop()

    def visitReturn(self, node):
        log.debug("visitReturn: %r", node.value)
        citdl_types = self._guessTypes(node.value)
        for citdl in citdl_types:
            if citdl:
                citdl = citdl.split(None, 1)[0]
                if citdl and citdl not in ("None", "NoneType"):
                    if citdl in ("False", "True"):
                        citdl = "bool"
                    func_node = self.nsstack[-1]
                    t = func_node["returns"]
                    t[citdl] = t.get(citdl, 0) + 1

    def visitClass(self, node):
        log.debug("visitClass:%d: %r", node.lineno,
                  self.lines and self.lines[node.lineno-1])
        locals = self.nsstack[-1]
        name = node.name
        nspath = locals["nspath"] + (name,)
        namespace = {
            "nspath": nspath,
            "name": name,
            "types": {"class": 1},
            #XXX Example of a base class that might surprise: the
            #    __metaclass__ class in
            #    c:\python22\lib\site-packages\ctypes\com\automation.py
            #    Should this be self._getCITDLExprRepr()???
            "classrefs": [],
            "symbols": {},
        }
        namespace["declaration"] = namespace

        if node.lineno: namespace["line"] = node.lineno
        lastNode = node
        childNodes = [n for n in lastNode.getChildNodes() if not isinstance(n, ast.Decorators)]
        while childNodes:
            lastNode = childNodes[-1]
            childNodes = [n for n in lastNode.getChildNodes() if not isinstance(n, ast.Decorators)]
        if lastNode.lineno: namespace["lineend"] = lastNode.lineno

        attributes = []
        if name.startswith("__") and name.endswith("__"):
            pass
        elif name.startswith("__"):
            attributes.append("private")
        elif name.startswith("_"):
            attributes.append("protected")
        namespace["attributes"] = ' '.join(attributes)

        if node.bases:
            for baseNode in node.bases:
                baseName = self._getExprRepr(baseNode)
                classref = {"name": baseName, "types": {}}
                for t in self._guessTypes(baseNode):
                    if t not in classref["types"]:
                        classref["types"][t] = 0
                    classref["types"][t] += 1
                namespace["classrefs"].append(classref)
        if node.doc:
            siglines, desclines = util.parsePyFuncDoc(node.doc)
            if siglines:
                namespace["signature"] = "\n".join(siglines)
            if desclines:
                namespace["doc"] = "\n".join(desclines)
        self.st[nspath] = locals["symbols"][name] = namespace

        self.nsstack.append(namespace)
        self.visit(node.code)
        self.nsstack.pop()

    def visitFunction(self, node):
        log.debug("visitFunction:%d: %r", node.lineno,
                  self.lines and self.lines[node.lineno-1])
        parent = self.nsstack[-1]
        parentIsClass = _isclass(parent)

        namespace = {
            "types": {"function": 1},
            "returns": {},
            "arguments": [],
            "symbols": {},
        }

        namespace["declaration"] = namespace
        if node.lineno: namespace["line"] = node.lineno
        lastNode = node
        while lastNode.getChildNodes():
            lastNode = lastNode.getChildNodes()[-1]
        if lastNode.lineno: namespace["lineend"] = lastNode.lineno

        name = node.name

        # Determine attributes
        attributes = []
        if name.startswith("__") and name.endswith("__"):
            pass
        elif name.startswith("__"):
            attributes.append("private")
        elif name.startswith("_"):
            attributes.append("protected")
        if name == "__init__" and parentIsClass:
            attributes.append("__ctor__")

        # process decorators
        prop_var = None
        if node.decorators:
            for deco in node.decorators.nodes:
                deco_name = getattr(deco, 'name', None)
                prop_mode = None

                if deco_name == 'staticmethod':
                    attributes.append("__staticmethod__")
                    continue
                if deco_name == 'classmethod':
                    attributes.append("__classmethod__")
                    continue
                if deco_name == 'property':
                    prop_mode = 'getter'
                elif hasattr(deco, 'attrname') and deco.attrname in ('getter',
                                                                     'setter',
                                                                     'deleter'):
                    prop_mode = deco.attrname

                if prop_mode:
                    if prop_mode == 'getter':
                        # it's a getter, create a pseudo-var
                        prop_var = parent["symbols"].get(name, None)
                        if prop_var is None:
                            prop_var = dict(name=name,
                                            nspath=parent["nspath"] + (name,),
                                            doc=None,
                                            types={},
                                            symbols={})
                            var_attrs = ['property']
                            if name.startswith("__") and name.endswith("__"):
                                pass
                            elif name.startswith("__"):
                                var_attrs.append("private")
                            elif name.startswith("_"):
                                var_attrs.append("protected")
                            prop_var["attributes"] = ' '.join(var_attrs)
                            prop_var["declaration"] = prop_var
                            parent["symbols"][name] = prop_var

                        if not "is-class-var" in prop_var:
                            prop_var["is-class-var"] = 1

                    # hide the function
                    attributes += ['__hidden__']
                    name += " (property %s)" % prop_mode

                    # only one property decorator makes sense
                    break

        namespace["attributes"] = ' '.join(attributes)

        if parentIsClass and name == "__init__":
            fallbackSig = parent["name"]
        else:
            fallbackSig = name
        namespace["name"] = name

        nspath = parent["nspath"] + (name,)
        namespace["nspath"] = nspath

        # Handle arguments. The format of the relevant Function attributes
        # makes this a little bit of pain.
        defaultArgsBaseIndex = len(node.argnames) - len(node.defaults)
        if node.kwargs:
            defaultArgsBaseIndex -= 1
            if node.varargs:
                defaultArgsBaseIndex -= 1
                varargsIndex = len(node.argnames)-2
            else:
                varargsIndex = None
            kwargsIndex = len(node.argnames)-1
        elif node.varargs:
            defaultArgsBaseIndex -= 1
            varargsIndex = len(node.argnames)-1
            kwargsIndex = None
        else:
            varargsIndex = kwargsIndex = None
        sigArgs = []
        for i in range(len(node.argnames)):
            argOrArgTuple = node.argnames[i]

            if isinstance(argOrArgTuple, tuple):
                # If it is a tuple arg with a default assignment, then we
                # drop that info (except for the sig): too hard and too rare
                # to bother with.
                sigArg = str(argOrArgTuple)
                if i >= defaultArgsBaseIndex:
                    defaultNode = node.defaults[i-defaultArgsBaseIndex]
                    try:
                        default = self._getExprRepr(defaultNode)
                    except PythonCILEError as ex:
                        raise PythonCILEError("unexpected default argument node "
                                              "type for Function '%s': %s"
                                              % (node.name, ex))
                    sigArg += "="+default
                sigArgs.append(sigArg)
                arguments = []
                for argName in argOrArgTuple:
                    argument = {"name": argName,
                                "nspath": nspath+(argName,),
                                "doc": None,
                                "types": {},
                                "line": node.lineno,
                                "symbols": {}}
                    arguments.append(argument)
            else:
                argName = argOrArgTuple
                argument = {"name": argName,
                            "nspath": nspath+(argName,),
                            "doc": None,
                            "types": {},
                            "line": node.lineno,
                            "symbols": {}}
                if i == kwargsIndex:
                    argument["attributes"] = "kwargs"
                    sigArgs.append("**"+argName)
                elif i == varargsIndex:
                    argument["attributes"] = "varargs"
                    sigArgs.append("*"+argName)
                elif i >= defaultArgsBaseIndex:
                    defaultNode = node.defaults[i-defaultArgsBaseIndex]
                    try:
                        argument["default"] = self._getExprRepr(defaultNode)
                    except PythonCILEError as ex:
                        raise PythonCILEError("unexpected default argument node "
                                              "type for Function '%s': %s"
                                              % (node.name, ex))
                    sigArgs.append(argName+'='+argument["default"])
                    for t in self._guessTypes(defaultNode):
                        log.debug("guessed type: %s ::= %s", argName, t)
                        if t not in argument["types"]:
                            argument["types"][t] = 0
                        argument["types"][t] += 1
                else:
                    sigArgs.append(argName)

                if i == 0 and parentIsClass:
                    # If this is a class method, then the first arg is the class
                    # instance.
                    className = self.nsstack[-1]["nspath"][-1]
                    argument["types"][className] = 1
                    argument["declaration"] = self.nsstack[-1]
                arguments = [argument]

            for argument in arguments:
                if "declaration" not in argument:
                    argument["declaration"] = argument # namespace dict of the declaration
                namespace["arguments"].append(argument)
                namespace["symbols"][argument["name"]] = argument
        # Drop first "self" argument from class method signatures.
        # - This is a little bit of a compromise as the "self" argument
        #   should *sometimes* be included in a method's call signature.
        if _isclass(parent) and sigArgs and "__staticmethod__" not in attributes:
            # Delete the first "self" argument.
            del sigArgs[0]
        fallbackSig += "(%s)" % (", ".join(sigArgs))
        if "__staticmethod__" in attributes:
            fallbackSig += " - staticmethod"
        elif "__classmethod__" in attributes:
            fallbackSig += " - classmethod"
        if node.doc:
            siglines, desclines = util.parsePyFuncDoc(node.doc, [fallbackSig])
            namespace["signature"] = "\n".join(siglines)
            if desclines:
                namespace["doc"] = "\n".join(desclines)
        else:
            namespace["signature"] = fallbackSig
        self.st[nspath] = parent["symbols"][name] = namespace

        self.nsstack.append(namespace)
        self.visit(node.code)
        self.nsstack.pop()

        if prop_var:
            # this is a property getter function,
            # copy its return types to the corresponding property variable...
            var_types = prop_var["types"]
            for t in namespace["returns"]:
                if t not in var_types:
                    var_types[t] = 0
                else:
                    var_types[t] += 1
            # ... as well as its line number
            if "line" in namespace:
                prop_var["line"] = namespace["line"]

    def visitImport(self, node):
        log.debug("visitImport:%d: %r", node.lineno,
                  self.lines and self.lines[node.lineno-1])
        imports = self.nsstack[-1].setdefault("imports", [])
        for module, alias in node.names:
            import_ = {"module": module}
            if node.lineno: import_["line"] = node.lineno
            if alias: import_["alias"] = alias
            imports.append(import_)

    def visitFrom(self, node):
        log.debug("visitFrom:%d: %r", node.lineno,
                  self.lines and self.lines[node.lineno-1])
        imports = self.nsstack[-1].setdefault("imports", [])
        module = node.modname
        if node.level > 0:
            module = ("." * node.level) + module
        for symbol, alias in node.names:
            import_ = {"module": module, "symbol": symbol}
            if node.lineno:
                import_["line"] = node.lineno
            if alias:
                import_["alias"] = alias
            imports.append(import_)

    #XXX
    #def visitReturn(self, node):
    #    # set __rettypes__ on Functions
    #    pass
    #def visitGlobal(self, node):
    #    # note for future visitAssign to control namespace
    #    pass
    #def visitYield(self, node):
    #    # modify the Function into a generator??? what are the implications?
    #    pass
    #def visitAssert(self, node):
    #    # support the assert hints that Wing does
    #    pass

    def _assignVariable(self, varName, namespace, rhsNode, line,
                        isClassVar=0):
        """Handle a simple variable name assignment.

            "varName" is the variable name being assign to.
            "namespace" is the namespace dict to which to assign the variable.
            "rhsNode" is the ast.Node of the right-hand side of the
                assignment.
            "line" is the line number on which the variable is being assigned.
            "isClassVar" (optional) is a boolean indicating if this var is
                a class variable, as opposed to an instance variable
        """
        log.debug("_assignVariable(varName=%r, namespace %s, rhsNode=%r, "
                  "line, isClassVar=%r)", varName,
                  '.'.join(namespace["nspath"]), rhsNode, isClassVar)
        variable = namespace["symbols"].get(varName, None)

        new_var = False
        if variable is None:
            new_var = True
            variable = {"name": varName,
                        "nspath": namespace["nspath"]+(varName,),
                        # Could try to parse documentation from a near-by
                        # string.
                        "doc": None,
                        # 'types' is a dict mapping a type name to the number
                        # of times this was guessed as the variable type.
                        "types": {},
                        "symbols": {}}
            # Determine attributes
            attributes = []
            if varName.startswith("__") and varName.endswith("__"):
                pass
            elif varName.startswith("__"):
                attributes.append("private")
            elif varName.startswith("_"):
                attributes.append("protected")
            variable["attributes"] = ' '.join(attributes)

            variable["declaration"] = variable
            if line: variable["line"] = line
            namespace["symbols"][varName] = variable

        if isClassVar and not "is-class-var" in variable:
            variable["is-class-var"] = 1
            # line number of first class-level assignment wins
            if line:
                variable["line"] = line

        if (not new_var and
            _isfunction(variable) and
            isinstance(rhsNode, ast.CallFunc) and
            rhsNode.args and
            isinstance(rhsNode.args[0], ast.Name) and
            variable["name"] == rhsNode.args[0].name
            ):
            # a speial case for 2.4-styled decorators
            return

        varTypes = variable["types"]
        for t in self._guessTypes(rhsNode, namespace):
            log.debug("guessed type: %s ::= %s", varName, t)
            if t not in varTypes:
                varTypes[t] = 0
            varTypes[t] += 1

    def _visitSimpleAssign(self, lhsNode, rhsNode, line):
        """Handle a simple assignment: assignment to a symbol name or to
        an attribute of a symbol name. If the given left-hand side (lhsNode)
        is not an node type that can be handled, it is dropped.
        """
        log.debug("_visitSimpleAssign(lhsNode=%r, rhsNode=%r)", lhsNode,
                  rhsNode)
        if isinstance(lhsNode, ast.AssName):
            # E.g.:  foo = ...
            # Assign this to the local namespace, unless there was a
            # 'global' statement. (XXX Not handling 'global' yet.)
            ns = self.nsstack[-1]
            self._assignVariable(lhsNode.name, ns, rhsNode, line,
                                 isClassVar=_isclass(ns))
        elif isinstance(lhsNode, ast.AssAttr):
            # E.g.:  foo.bar = ...
            # If we can resolve "foo", then we update that namespace.
            variable, citdl = self._resolveObjectRef(lhsNode.expr)
            if variable:
                self._assignVariable(lhsNode.attrname,
                                     variable["declaration"], rhsNode, line)
        else:
            log.debug("could not handle simple assign (module '%s'): "
                      "lhsNode=%r, rhsNode=%r", self.moduleName, lhsNode,
                      rhsNode)

    def visitAssign(self, node):
        log.debug("visitAssign:%d: %r", node.lineno,
                  self.lines and self.lines[node.lineno-1])
        lhsNode = node.nodes[0]
        rhsNode = node.expr
        if isinstance(lhsNode, (ast.AssName, ast.AssAttr)):
            # E.g.:
            #   foo = ...       (AssName)
            #   foo.bar = ...   (AssAttr)
            self._visitSimpleAssign(lhsNode, rhsNode, node.lineno)
        elif isinstance(lhsNode, (ast.AssTuple, ast.AssList)):
            # E.g.:
            #   foo, bar = ...
            #   [foo, bar] = ...
            # If the RHS is a sequence with the same number of elements,
            # then we update each assigned-to variable. Otherwise, bail.
            if isinstance(rhsNode, (ast.Tuple, ast.List)):
                if len(lhsNode.nodes) == len(rhsNode.nodes):
                    for i in range(len(lhsNode.nodes)):
                        self._visitSimpleAssign(lhsNode.nodes[i],
                                                rhsNode.nodes[i],
                                                node.lineno)
            elif isinstance(rhsNode, ast.Dict):
                if len(lhsNode.nodes) == len(rhsNode.items):
                    for i in range(len(lhsNode.nodes)):
                        self._visitSimpleAssign(lhsNode.nodes[i],
                                                rhsNode.items[i][0],
                                                node.lineno)
            elif isinstance(rhsNode, ast.CallFunc):
                for i in range(len(lhsNode.nodes)):
                    self._visitSimpleAssign(lhsNode.nodes[i],
                                            None, # we don't have a good type.
                                            node.lineno)
            else:
                log.debug("visitAssign:: skipping unknown rhsNode type: %r - %r",
                          type(rhsNode), rhsNode)
        elif isinstance(lhsNode, ast.Slice):
            # E.g.:  bar[1:2] = "foo"
            # We don't bother with these: too hard.
            log.debug("visitAssign:: skipping slice - too hard")
            pass
        elif isinstance(lhsNode, ast.Subscript):
            # E.g.:  bar[1] = "foo"
            # We don't bother with these: too hard.
            log.debug("visitAssign:: skipping subscript - too hard")
            pass
        else:
            raise PythonCILEError("unexpected type of LHS of assignment: %r"
                                  % lhsNode)

    def _handleUnknownAssignment(self, assignNode, lineno):
        if isinstance(assignNode, ast.AssName):
            self._visitSimpleAssign(assignNode, None, lineno)
        elif isinstance(assignNode, ast.AssTuple):
            for anode in assignNode.nodes:
                self._visitSimpleAssign(anode, None, lineno)

    def visitFor(self, node):
        log.debug("visitFor:%d: %r", node.lineno,
                  self.lines and self.lines[node.lineno-1])
        # E.g.:
        #   for foo in ...
        # None: don't bother trying to resolve the type of the RHS
        self._handleUnknownAssignment(node.assign, node.lineno)
        self.visit(node.body)

    def visitWith(self, node):
        log.debug("visitWith:%d: %r", node.lineno,
                  self.lines and self.lines[node.lineno-1])
        self._handleUnknownAssignment(node.vars, node.lineno)
        lhsNode = node.vars
        rhsNode = node.expr
        if isinstance(lhsNode, ast.AssName):
            # E.g.:
            #   with x() as foo:
            self._visitSimpleAssign(node.vars, node.expr, node.lineno)
        elif isinstance(lhsNode, (ast.AssTuple, ast.AssList)):
            # E.g.:
            #   with x() as (foo, bar):
            #   with x() as [foo, bar]:
            # If the expression is a sequence with the same number of elements,
            # then we update each assigned-to variable. Otherwise, bail.
            if isinstance(rhsNode, (ast.Tuple, ast.List)):
                if len(lhsNode.nodes) == len(rhsNode.nodes):
                    for i in range(len(lhsNode.nodes)):
                        self._visitSimpleAssign(lhsNode.nodes[i],
                                                rhsNode.nodes[i],
                                                node.lineno)
            elif isinstance(rhsNode, ast.Dict):
                if len(lhsNode.nodes) == len(rhsNode.items):
                    for i in range(len(lhsNode.nodes)):
                        self._visitSimpleAssign(lhsNode.nodes[i],
                                                rhsNode.items[i][0],
                                                node.lineno)
            elif isinstance(rhsNode, ast.CallFunc):
                for i in range(len(lhsNode.nodes)):
                    self._visitSimpleAssign(lhsNode.nodes[i],
                                            None, # we don't have a good type.
                                            node.lineno)
            else:
                log.debug("visitWith:: skipping unknown rhsNode type: %r - %r",
                          type(rhsNode), rhsNode)
        self.visit(node.body)

    def visitTryExcept(self, node):
        log.debug("visitTryExcept:%d: %r", node.lineno,
                  self.lines and self.lines[node.lineno-1])
        self.visit(node.body)
        for handler in node.handlers:
            try:
                if handler[1]:
                    try:
                        lineno = handler[1].lineno
                    except AttributeError:
                        lineno = node.lineno
                    self._handleUnknownAssignment(handler[1], lineno)
                if handler[2]:
                    self.visit(handler[2])
            except IndexError:
                pass
        if node.else_:
            self.visit(node.else_)

    def _resolveObjectRef(self, expr):
        """Try to resolve the given expression to a variable namespace.

            "expr" is some kind of ast.Node instance.

        Returns the following 2-tuple for the object:
            (<variable dict>, <CITDL string>)
        where,
            <variable dict> is the defining dict for the variable, e.g.
                    {'name': 'classvar', 'types': {'int': 1}}.
                This is None if the variable could not be resolved.
            <CITDL string> is a string of CITDL code (see the spec) describing
                how to resolve the variable later. This is None if the
                variable could be resolved or if the expression is not
                expressible in CITDL (CITDL does not attempt to be a panacea).
        """
        log.debug("_resolveObjectRef(expr=%r)", expr)
        if isinstance(expr, ast.Name):
            name = expr.name
            nspath = self.nsstack[-1]["nspath"]
            for i in range(len(nspath), -1, -1):
                ns = self.st[nspath[:i]]
                if name in ns["symbols"]:
                    return (ns["symbols"][name], None)
                else:
                    log.debug("_resolveObjectRef: %r not in namespace %r", name,
                              '.'.join(ns["nspath"]))
        elif isinstance(expr, ast.Getattr):
            obj, citdl = self._resolveObjectRef(expr.expr)
            decl = obj and obj["declaration"] or None # want the declaration
            if (decl #and "symbols" in decl #XXX this "and"-part necessary?
                and expr.attrname in decl["symbols"]):
                return (decl["symbols"][expr.attrname], None)
            elif isinstance(expr.expr, ast.Const):
                # Special case: specifically refer to type object for
                # attribute access on constants, e.g.:
                #   ' '.join
                citdl = "__builtins__.%s.%s"\
                        % ((type(expr.expr.value).__name__), expr.attrname)
                return (None, citdl)
                #XXX Could optimize here for common built-in attributes. E.g.,
                #    we *know* that str.join() returns a string.
        elif isinstance(expr, ast.Const):
            # Special case: specifically refer to type object for constants.
            return (None, "__builtins__.%s" % type(expr.value).__name__)
        elif isinstance(expr, ast.CallFunc):
            #XXX Would need flow analysis to have an object dict for whatever
            #    a __call__ would return.
            pass

        # Fallback: return CITDL code for delayed resolution.
        log.debug("_resolveObjectRef: could not resolve %r", expr)
        scope = '.'.join(self.nsstack[-1]["nspath"])
        exprrepr = self._getCITDLExprRepr(expr)
        if exprrepr:
            if scope:
                citdl = "%s %s" % (exprrepr, scope)
            else:
                citdl = exprrepr
        else:
            citdl = None
        return (None, citdl)

    def _guessTypes(self, expr, curr_ns=None):
        log.debug("_guessTypes(expr=%r)", expr)
        ts = []
        if isinstance(expr, ast.Const):
            ts = [type(expr.value).__name__]
        elif isinstance(expr, ast.Tuple):
            ts = [tuple.__name__]
        elif isinstance(expr, (ast.List, ast.ListComp)):
            ts = [list.__name__]
        elif isinstance(expr, ast.Set):
            ts = [set.__name__]
        elif isinstance(expr, ast.Dict):
            ts = [dict.__name__]
        elif isinstance(expr, (ast.Add, ast.Sub, ast.Mul, ast.Div, ast.Mod,
                               ast.Power)):
            order = ["int", "bool", "long", "float", "complex", "string",
                     "unicode"]
            possibles = self._guessTypes(expr.left)+self._guessTypes(expr.right)
            ts = []
            highest = -1
            for possible in possibles:
                if possible not in order:
                    ts.append(possible)
                else:
                    highest = max(highest, order.index(possible))
            if not ts and highest > -1:
                ts = [order[highest]]
        elif isinstance(expr, (ast.FloorDiv, ast.Bitand, ast.Bitor,
                               ast.Bitxor, ast.RightShift, ast.LeftShift)):
            ts = [int.__name__]
        elif isinstance(expr, (ast.Or, ast.And)):
            ts = []
            for node in expr.nodes:
                for t in self._guessTypes(node):
                    if t not in ts:
                        ts.append(t)
        elif isinstance(expr, (ast.Compare, ast.Not)):
            ts = [type(1==2).__name__]
        elif isinstance(expr, (ast.UnaryAdd, ast.UnarySub, ast.Invert,
                               ast.Not)):
            ts = self._guessTypes(expr.expr)
        elif isinstance(expr, ast.Slice):
            ts = [list.__name__]
        elif isinstance(expr, ast.Backquote):
            ts = [str.__name__]

        elif isinstance(expr, (ast.Name, ast.Getattr)):
            variable, citdl = self._resolveObjectRef(expr)
            if variable:
                if _isclass(variable) or _isfunction(variable):
                    ts = [ '.'.join(variable["nspath"]) ]
                else:
                    ts = list(variable["types"].keys())
            elif citdl:
                ts = [citdl]
        elif isinstance(expr, ast.CallFunc):
            variable, citdl = self._resolveObjectRef(expr.node)
            if variable:
                #XXX When/if we support <returns/> and if we have that
                #    info for this 'variable' we can return an actual
                #    value here.
                # Optmizing Shortcut: If the variable is a class then just
                # call its type that class definition, i.e. 'mymodule.MyClass'
                # instead of 'type(call(mymodule.MyClass))'.

                # Remove the common leading namespace elements.
                scope_parts = list(variable["nspath"])
                if curr_ns is not None:
                    for part in curr_ns["nspath"]:
                        if scope_parts and part == scope_parts[0]:
                            scope_parts.pop(0)
                        else:
                            break
                scope = '.'.join(scope_parts)
                if _isclass(variable):
                    ts = [ scope ]
                else:
                    ts = [scope+"()"]
            elif citdl:
                # For code like this:
                #   for line in lines:
                #       line = line.rstrip()
                # this results in a type guess of "line.rstrip <funcname>".
                # That sucks. Really it should at least be line.rstrip() so
                # that runtime CITDL evaluation can try to determine that
                # rstrip() is a _function_ call rather than _class creation_,
                # which is the current resuilt. (c.f. bug 33493)
                # XXX We *could* attempt to guess based on where we know
                #     "line" to be a module import: the only way that
                #     'rstrip' could be a class rather than a function.
                # TW: I think it should always use "()" no matter if it's
                #     a class or a function. The codeintel handler can work
                #     out which one it is. This gives us the ability to then
                #     distinguish between class methods and instance methods,
                #     as class methods look like:
                #       MyClass.staticmethod()
                #     and instance methods like:
                #       MyClass().instancemethod()
                # Updated to use "()".
                # Ensure we only add the "()" to the type part, not to the
                # scope (if it exists) part, which is separated by a space. Bug:
                #   http://bugs.activestate.com/show_bug.cgi?id=71987
                # citdl in this case looks like "string.split myfunction"
                ts = citdl.split(None, 1)
                ts[0] += "()"
                ts = [" ".join(ts)]
        elif isinstance(expr, (ast.Subscript, ast.Lambda)):
            pass
        else:
            log.debug("don't know how to guess types from this expr: %r" % expr)
        return ts

    def _getExprRepr(self, node):
        """Return a string representation for this Python expression.

        Raises PythonCILEError if can't do it.
        """
        s = None
        if isinstance(node, ast.Name):
            s = node.name
        elif isinstance(node, ast.Const):
            s = repr(node.value)
        elif isinstance(node, ast.Getattr):
            s = '.'.join([self._getExprRepr(node.expr), node.attrname])
        elif isinstance(node, ast.List):
            items = [self._getExprRepr(c) for c in node.getChildren()]
            s = "[%s]" % ", ".join(items)
        elif isinstance(node, ast.Tuple):
            items = [self._getExprRepr(c) for c in node.getChildren()]
            s = "(%s)" % ", ".join(items)
        elif isinstance(node, ast.Set):
            items = [self._getExprRepr(c) for c in node.getChildren()]
            s = "{%s}" % ", ".join(items)
        elif isinstance(node, ast.Dict):
            items = ["%s: %s" % (self._getExprRepr(k), self._getExprRepr(v))
                     for (k, v) in node.items]
            s = "{%s}" % ", ".join(items)
        elif isinstance(node, ast.CallFunc):
            s = self._getExprRepr(node.node)
            s += "("
            allargs = []
            for arg in node.args:
                allargs.append( self._getExprRepr(arg) )
            if node.star_args:
                for arg in node.star_args:
                    allargs.append( "*" + self._getExprRepr(arg) )
            if node.dstar_args:
                for arg in node.dstar_args:
                    allargs.append( "**" + self._getExprRepr(arg) )
            s += ",".join( allargs )
            s += ")"
        elif isinstance(node, ast.Subscript):
            s = "[%s]" % self._getExprRepr(node.expr)
        elif isinstance(node, ast.Backquote):
            s = "`%s`" % self._getExprRepr(node.expr)
        elif isinstance(node, ast.Slice):
            dumpNode(node)
            s = self._getExprRepr(node.expr)
            s += "["
            if node.lower:
                s += self._getExprRepr(node.lower)
            s += ":"
            if node.upper:
                s += self._getExprRepr(node.upper)
            s += "]"
        elif isinstance(node, ast.UnarySub):
            s = "-" + self._getExprRepr(node.expr)
        elif isinstance(node, ast.UnaryAdd):
            s = "+" + self._getExprRepr(node.expr)
        elif isinstance(node, ast.Add):
            s = self._getExprRepr(node.left) + "+" + self._getExprRepr(node.right)
        elif isinstance(node, ast.Sub):
            s = self._getExprRepr(node.left) + "-" + self._getExprRepr(node.right)
        elif isinstance(node, ast.Mul):
            s = self._getExprRepr(node.left) + "*" + self._getExprRepr(node.right)
        elif isinstance(node, ast.Div):
            s = self._getExprRepr(node.left) + "/" + self._getExprRepr(node.right)
        elif isinstance(node, ast.FloorDiv):
            s = self._getExprRepr(node.left) + "//" + self._getExprRepr(node.right)
        elif isinstance(node, ast.Mod):
            s = self._getExprRepr(node.left) + "%" + self._getExprRepr(node.right)
        elif isinstance(node, ast.Power):
            s = self._getExprRepr(node.left) + "**" + self._getExprRepr(node.right)
        elif isinstance(node, ast.LeftShift):
            s = self._getExprRepr(node.left) + "<<" + self._getExprRepr(node.right)
        elif isinstance(node, ast.RightShift):
            s = self._getExprRepr(node.left) + ">>"+ self._getExprRepr(node.right)
        elif isinstance(node, ast.Keyword):
            s = node.name + "=" + self._getExprRepr(node.expr)
        elif isinstance(node, ast.Bitor):
            creprs = []
            for cnode in node.nodes:
                if isinstance(cnode, (ast.Const, ast.Name)):
                    crepr = self._getExprRepr(cnode)
                else:
                    crepr = "(%s)" % self._getExprRepr(cnode)
                creprs.append(crepr)
            s = "|".join(creprs)
        elif isinstance(node, ast.Bitand):
            creprs = []
            for cnode in node.nodes:
                if isinstance(cnode, (ast.Const, ast.Name)):
                    crepr = self._getExprRepr(cnode)
                else:
                    crepr = "(%s)" % self._getExprRepr(cnode)
                creprs.append(crepr)
            s = "&".join(creprs)
        elif isinstance(node, ast.Bitxor):
            creprs = []
            for cnode in node.nodes:
                if isinstance(cnode, (ast.Const, ast.Name)):
                    crepr = self._getExprRepr(cnode)
                else:
                    crepr = "(%s)" % self._getExprRepr(cnode)
                creprs.append(crepr)
            s = "^".join(creprs)
        elif isinstance(node, ast.Lambda):
            s = "lambda"
            defaultArgsBaseIndex = len(node.argnames) - len(node.defaults)
            if node.kwargs:
                defaultArgsBaseIndex -= 1
                if node.varargs:
                    defaultArgsBaseIndex -= 1
                    varargsIndex = len(node.argnames)-2
                else:
                    varargsIndex = None
                kwargsIndex = len(node.argnames)-1
            elif node.varargs:
                defaultArgsBaseIndex -= 1
                varargsIndex = len(node.argnames)-1
                kwargsIndex = None
            else:
                varargsIndex = kwargsIndex = None
            args = []
            for i in range(len(node.argnames)):
                argOrArgTuple = node.argnames[i]
                if isinstance(argOrArgTuple, tuple):
                    arg = "(%s)" % ','.join(argOrArgTuple)
                    if i >= defaultArgsBaseIndex:
                        defaultNode = node.defaults[i-defaultArgsBaseIndex]
                        try:
                            arg += "="+self._getExprRepr(defaultNode)
                        except PythonCILEError:
                            #XXX Work around some trouble cases.
                            arg += arg+"=..."
                else:
                    argname = node.argnames[i]
                    if i == kwargsIndex:
                        arg = "**"+argname
                    elif i == varargsIndex:
                        arg = "*"+argname
                    elif i >= defaultArgsBaseIndex:
                        defaultNode = node.defaults[i-defaultArgsBaseIndex]
                        try:
                            arg = argname+"="+self._getExprRepr(defaultNode)
                        except PythonCILEError:
                            #XXX Work around some trouble cases.
                            arg = argname+"=..."
                    else:
                        arg = argname
                args.append(arg)
            if args:
                s += " " + ",".join(args)
            try:
                s += ": " + self._getExprRepr(node.code)
            except PythonCILEError:
                #XXX Work around some trouble cases.
                s += ":..."
        else:
            raise PythonCILEError("don't know how to get string repr "
                                  "of expression: %r" % node)
        return s

    def _getCITDLExprRepr(self, node, _level=0):
        """Return a string repr for this expression that CITDL processing
        can handle.

        CITDL is no panacea -- it is meant to provide simple delayed type
        determination. As a result, many complicated expressions cannot
        be handled. If the expression is not with CITDL's scope, then None
        is returned.
        """
        s = None
        if isinstance(node, ast.Name):
            s = node.name
        elif isinstance(node, ast.Const):
            s = repr(node.value)
        elif isinstance(node, ast.Getattr):
            exprRepr = self._getCITDLExprRepr(node.expr, _level+1)
            if exprRepr is None:
                pass
            else:
                s = '.'.join([exprRepr, node.attrname])
        elif isinstance(node, ast.List):
            s = "[]"
        elif isinstance(node, ast.Tuple):
            s = "()"
        elif isinstance(node, ast.Set):
            s = "set()"
        elif isinstance(node, ast.Dict):
            s = "{}"
        elif isinstance(node, ast.CallFunc):
            # Only allow CallFunc at the top-level. I.e. this:
            #   spam.ham.eggs()
            # is in scope, but this:
            #   spam.ham().eggs
            # is not.
            if _level != 0:
                pass
            else:
                s = self._getCITDLExprRepr(node.node, _level+1)
                if s is not None:
                    s += "()"
        return s


def _quietCompilerParse(content):
    oldstderr = sys.stderr
    sys.stderr = StringIO()
    try:
        return compiler.parse(content)
    finally:
        sys.stderr = oldstderr

def _quietCompile(source, filename, kind):
    oldstderr = sys.stderr
    sys.stderr = StringIO()
    try:
        return compile(source, filename, kind)
    finally:
        sys.stderr = oldstderr


def _getAST(content):
    """Return an AST for the given Python content.

    If cannot, raise an error describing the problem.
    """
    # EOL issues:
    # compiler.parse() can't handle '\r\n' EOLs on Mac OS X and can't
    # handle '\r' EOLs on any platform. Let's just always normalize.
    # Unfortunately this is work only for the exceptional case. The
    # problem is most acute on the Mac.
    content = '\n'.join(content.splitlines(0))
    # Is this faster?
    #   content = content.replace('\r\n', '\n').replace('\r', '\n')

    errlineno = None # line number of a SyntaxError
    lasterrlineno = None
    ast_ = None
    try:
        ast_ = _quietCompilerParse(content)
    except SyntaxError as ex:
        errlineno = ex.lineno
        log.debug("compiler parse: syntax error on line %d", errlineno)
    except parser.ParserError as ex:
        log.debug("compiler parse: parse error")
        # Try to get the offending line number.
        # compile() only likes LFs for EOLs.
        lfContent = content.replace("\r\n", "\n").replace("\r", "\n")
        try:
            _quietCompile(lfContent, "dummy.py", "exec")
        except SyntaxError as ex2:
            errlineno = ex2.lineno
        except:
            pass
        if errlineno is None:
            raise # Does this re-raise 'ex' (as we want) or 'ex2'?

    while errlineno is not None:
        if lasterrlineno is not None and errlineno <= lasterrlineno:
            raise ValueError("cannot recover from syntax error: line %d"
                             % errlineno)
        lasterrlineno = errlineno
        # There was a syntax error at this line: try to recover by effectively
        # nulling out the offending line.
        lines = content.splitlines(1)
        offender = lines[errlineno-1]
        log.debug("syntax error on line %d: %r: trying to recover",
                  errlineno, offender)
        indent = ''
        for i in range(0, len(offender)):
            if offender[i] in " \t":
                indent += offender[i]
            else:
                break
        lines[errlineno-1] = indent+"pass"+"\n"
        content = ''.join(lines)

        try:
            ast_ = _quietCompilerParse(content)
        except SyntaxError as ex:
            errlineno = ex.lineno
            log.debug("compiler parse: another syntax error on line %d", errlineno)
        except parser.ParserError as ex:
            log.debug("compiler parse: another parse error")
            # Try to get the offending line number.
            # compile() only likes LFs for EOLs.
            lfContent = content.replace("\r\n", "\n").replace("\r", "\n")
            try:
                _quietCompile(lfContent, "dummy.py", "exec")
            except SyntaxError as ex2:
                errlineno = ex2.lineno
            except:
                pass
            if errlineno is None:
                raise

        if ast_ is not None:
            break

    if ast_ is None:
        raise ValueError("could not generate AST")

    return ast_


_rx_cache = {}

def _rx(pattern, flags=0):
    if pattern not in _rx_cache:
        _rx_cache[pattern] = re.compile(pattern, flags)
    return _rx_cache[pattern]

def _convert3to2(src):
    # XXX: this might be much faster to do all this stuff by manipulating
    #      parse trees produced by tdparser

    # except Foo as bar => except (Foo,) bar
    src = _rx(r'(\bexcept\s*)(\S.+?)\s+as\s+(\w+)\s*:').sub(r'\1(\2,), \3:', src)

    # 0o123 => 123
    src = _rx(r'\b0[oO](\d+)').sub(r'\1', src)

    # print(foo) => print_(foo)
    src = _rx(r'\bprint\s*\(').sub(r'print_(', src)

    # change forms of class Foo(metaclass=Cls3) to class Foo
    src = _rx(r'(\bclass\s+\w+\s*)\(\s*\w+\s*=\s*\w+\s*\)\s*:').sub(r'\1:', src)

    # change forms of class Foo(..., arg=Base1, metaclass=Cls3) to class Foo(...)
    src = _rx(r'(\bclass\s+\w+\s*\(.*?),?\s*\w+\s*=.+?\)\s*:').sub(r'\1):', src)

    # Remove return type annotations like def foo() -> int:
    src = _rx(r'(\bdef\s+\w+\s*\(.*?\))\s*->\s*\w+\s*:').sub(r'\1:', src)

    # def foo() -> type: => def foo():
    src = _rx(r'->[^:]+:').sub(':', src)

    # def foo(foo:Bar, baz=lambda x: qoox): => def foo(bar, baz=_lambda(qoox)):
    src = _rx(r'(\bdef\s+\w+\s*\()(.+?)(\)\s*:)').sub(_clean_func_args, src)

    # Remove async keyword from function definitions
    src = _rx(r'\basync\s+(def\s+)').sub(r'\1', src)

    # yield from => yield
    # bug 101404
    src = _rx(r'(\byield\s+from\b)').sub('yield', src)

    return src

def _clean_func_args(defn):
    argdef = defn.group(2)

    parser = tdparser.PyExprParser()
    try:
        arglist = parser.parse_bare_arglist(argdef)

        seen_args = False
        seen_kw = False
        py2 = []
        for arg in arglist:
            name, value, type = arg
            if name.id == "*":
                if not seen_kw:
                    name.value = "**kwargs"
                    py2.append(arg)
                    seen_kw = True
                    seen_args = True
            elif name.value[:2] == "**":
                if not seen_kw:
                    py2.append(arg)
                    seen_kw = True
                    seen_args = True
            elif name.value[0] == "*":
                if not seen_args:
                    seen_args = True
                    py2.append(arg)
            else:
                if seen_args or seen_kw:
                    break
                else:
                    py2.append(arg)

        cleared = tdparser.arg_list_py(py2)
    except tdparser.ParseError as ex:
        cleared = argdef
        log.exception("Couldn't parse (%r)" % argdef)

    return defn.group(1) + cleared + defn.group(3)

class PythonLexer(Lexer):
    def __init__(self):
        self._properties = SilverCity.PropertySet()
        self._lexer = SilverCity.find_lexer_module_by_id(ScintillaConstants.SCLEX_PYTHON)
        self._keyword_lists = [
            SilverCity.WordList(python_keywords),
            SilverCity.WordList(""), # hilighted identifiers
        ]

class PythonScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "Python"

class PythonMemberCompletionContext(AbstractMemberCompletionContext):
    """Implementation of AbstractMemberCompletionContext."""
    @property
    def language(self):
        return "Python"

class PythonFindReferencesContext(AbstractFindReferencesContext):
    """Implementation of AbstractFindReferencesContext."""
    @property
    def projectFiles(self):
        """Implementation of AbstractFindReferencesContext.projectFiles."""
        filenames = []
        for dirname in self.env.get("PYTHONPATH", "").split(os.pathsep):
            if not dirname:
                continue
            for filename in fetchAllFilesInDirectory(dirname, ".py"):
                filenames.append(filename)
        return filenames

#---- public module interface

class PythonScanner(AbstractScanner):
    def __init__(self, stdlib_file):
        """Overrides the default scanner __init__() to load Python's stdlib, but
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
            try:
                ast_ = _getAST(self.content)
            except UnicodeEncodeError:
                ast_ = _getAST(self.content.encode(errors="ignore"))
        except:
            log.debug("Unable to generate AST for parsing.")
            self.tokens = PythonLexer().tokenize_by_style(self.content) # only used by getCompletionContext()
            return Scope(self.builtInScope)

        moduleName = os.path.splitext(os.path.basename(filename))[0]
        visitor = AST2CIXVisitor(moduleName, content=self.content, lang="Python")
        compiler.walk(ast_, visitor, None)

        try:
            self.tokens = PythonLexer().tokenize_by_style(self.content) # only used by getCompletionContext()
        except UnicodeDecodeError:
            self.tokens = PythonLexer().tokenize_by_style(self.content.decode(errors="ignore")) # only used by getCompletionContext()

        return visitor.getScope(self.builtInScope)

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
        # Note: The SilverCity tokenizer returns tokens with 0-based lines.
        # While many CodeIntel legacy parsers perform adjustments such that
        # tokens use 1-based lines, since we are using the Python parser
        # directly, no adjustment is made, so compute a 0-based line based on
        # position.
        lines = self.content[:position].split("\n")
        line, column = len(lines) - 1, len(lines[-1])

        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in self.tokens:
            if token["start_line"] <= line:
                if token["start_line"] < line:
                    # Keep all non-whitespace tokens prior to the current line.
                    if token["style"] != SCE_P_DEFAULT:
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if token["style"] != SCE_P_DEFAULT or (token["start_column"] + 1 <= column and column <= token["end_column"] + 1):
                        tokens.append(token)
                        if token["style"] == SCE_P_OPERATOR and column <= token["end_column"] and token["start_column"] != token["end_column"]:
                            # The tokenizer generally groups all operator
                            # characters into a single token. Chop off any
                            # bits that occur after the current position.
                            # Otherwise, something like "(<|>)" will be
                            # considered to be a "()" token instead of '('.
                            token["text"] = token["text"][:-(token["end_column"] - column + 1)]
                elif token["start_column"] == column and token["style"] == SCE_P_IDENTIFIER:
                    # Keep an identifier token that starts at the current
                    # position.
                    tokens.append(token)
        leading_whitespace = False
        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in PYTHONPATH should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PYTHONPATH" in env:
                dirs = env["PYTHONPATH"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PYTHONPATH"] = os.pathsep.join(dirs)
        if self.__class__ == PythonScanner:
            import_resolver = PythonImportResolver(filename, env)
        elif self.__class__ == Python3Scanner:
            import_resolver = Python3ImportResolver(filename, env)
        else:
            raise RuntimeError("'%s' scanner does not have an AbstractImportResolver" % self.__class__.__name__)
        name_part = "" # the "already typed" part of a symbol completion

        if len(tokens) == 0:
            # If the position is at the beginning of the buffer, provide scope
            # completions.
            return PythonScopeCompletionContext(scope)

        # Pre-process the end of the token list.
        if tokens[-1]["style"] == SCE_P_DEFAULT:
            # If the position follows whitespace, make a note since some
            # completions after whitespace like "if <|>" are possible, while
            # some like "if<|>" are not.
            leading_whitespace = True
            tokens.pop()
            if len(tokens) == 0:
                return PythonScopeCompletionContext(scope)
        elif tokens[-1]["style"] == SCE_P_IDENTIFIER:
            # If the position is within a symbol name, or at the beginning of
            # a symbol name, make a note of that name since it should be
            # considered as the "already typed" part of a completion list.
            name_part = tokens[-1]["text"]
            tokens.pop()
            if len(tokens) == 0:
                return PythonScopeCompletionContext(scope, name_part)

        # Now look back through the token list and provide an appropriate
        # completion context.
        if tokens[-1]["style"] == SCE_P_OPERATOR and tokens[-1]["text"] == ".":
            # If the first significant token behind the position is a '.', it's
            # probably part of a larger "foo.bar"-type of expression.
            i = len(tokens) - 1
            while i >= 1:
                # Skip back through "name." token pairs in order to construct
                # the full symbol name.
                if tokens[i]["style"] != SCE_P_OPERATOR or tokens[i]["text"] != "." or tokens[i - 1]["style"] != SCE_P_IDENTIFIER:
                    if tokens[i]["style"] == SCE_P_OPERATOR and tokens[i]["text"] in ("(", ","):
                        # Actually, the original '.' token could be part of a
                        # "from (foo.bar"-, "import (foo.bar"-,
                        # "from foo import (bar.baz"-, or
                        # "from foo import (bar, baz.quux"-type of expression,
                        # which is invalid and should not provide completions.
                        # Skip over any potential "foo, bar.baz" import
                        # names, looking for the incriminating "import" and
                        # "from" keywords.
                        j = i - 1
                        if tokens[i]["text"] == ",":
                            while j >= 1:
                                if tokens[j]["style"] != SCE_P_IDENTIFIER and (tokens[j]["style"] != SCE_P_OPERATOR or tokens[j]["text"] not in ("(", ",", ".")):
                                    break
                                j -= 1
                        if tokens[j + 1]["style"] == SCE_P_OPERATOR and tokens[j + 1]["text"] == "(" and tokens[j]["style"] == SCE_P_WORD and tokens[j]["text"] in ("from", "import"):
                            # Any "from (foo.bar"-, "import (foo.bar"-,
                            # "from foo import (bar.baz"-, or
                            # "from foo import (bar, baz.quux"-type of
                            # expression is invalid and should not provide
                            # completions.
                            return None
                        elif tokens[j]["style"] == SCE_P_WORD and tokens[j]["text"] == "import":
                            # The original '.' token is part of an
                            # "import foo, bar.baz"-type of expression. Look
                            # back for illegal "from" keyword.
                            j -= 1
                            while j >= 1:
                                if tokens[j]["style"] != SCE_P_IDENTIFIER and (tokens[j]["style"] != SCE_P_OPERATOR or tokens[j]["text"] not in (".")):
                                    break
                                j -= 1
                            if tokens[j]["style"] == SCE_P_WORD and tokens[j]["text"] == "from":
                                # A "from foo import bar.baz"- or
                                # "from foo import bar, baz.quux"-type of
                                # expression is invalid and should not provide
                                # completions.
                                return None
                    break
                i -= 2
            symbol_name = "".join([token["text"] for token in tokens[i+1:-1]])
            if not symbol_name:
                if i > 0 and tokens[i - 1]["style"] in (SCE_P_STRING, SCE_P_CHARACTER, SCE_P_TRIPLE, SCE_P_TRIPLEDOUBLE):
                    # A "'foo'.bar"-type of expression should provide string
                    # member completions.
                    return PythonMemberCompletionContext(scope, "str", name_part)
                # The original '.' token came after an unknown symbol and should
                # not provide completions.
                return None
            elif tokens[i]["style"] == SCE_P_WORD and tokens[i]["text"] in ("import", "from"):
                # The original '.' token could be part of an
                # "import foo.bar"- or "from foo.bar"-type of expression, which
                # should provide member completions. However, it could also be
                # part of a "from foo import bar.baz"-type of expression, which
                # is invalid and should not provide completions.
                if tokens[i]["text"] == "import":
                    j = i - 1
                    while j >= 1:
                        if tokens[j]["style"] != SCE_P_IDENTIFIER and (tokens[j]["style"] != SCE_P_OPERATOR or tokens[j]["text"] != "."):
                            break
                        j -= 1
                    if tokens[j]["style"] == SCE_P_WORD and tokens[j]["text"] == "from":
                        # A "from foo import bar.baz"-type of expression is
                        # invalid and should not provide completions.
                        return None
                # A "import foo.bar"- or "from foo.bar"-type of expression
                # should provide member completions. However, in order for this
                # to work, the top-level symbol needs to be imported into the
                # current scope. This is necessary because the import resolver
                # only imports the bits after "import", so the "from" context is
                # lost.
                # For example: "from foo.bar import baz" imports "baz" into the
                # current scope, and "foo" and "bar" are ignored. Thus
                # "from foo.<|>" cannot fetch member completions for "foo"
                # without importing "foo" into the current scope first.
                top_level_name = tokens[i+1]["text"]
                scope.define(Import(top_level_name, top_level_name))
                return PythonMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver)
            else:
                # A "foo.bar"-type of expression should provide member
                # completions.
                return PythonMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver)
        elif tokens[-1]["style"] == SCE_P_OPERATOR and tokens[-1]["text"] in ("(", ","):
            # If the first significant token behind the position is a '(' or a
            # ',', it's probably part of a typical expression, which should
            # provide scope completions. However, no completions should be
            # provided within a function definition's argument list, and a
            # "from foo import (bar, baz"-type of expression should provide
            # member completions.
            if len(tokens) > 1:
                i = len(tokens) - 2
                while i >= 1:
                    if tokens[i]["style"] == SCE_P_DEFNAME:
                        # A "def foo(bar"- or "def foo(bar, baz"-type of
                        # expression should not provide completions.
                        return None
                    elif tokens[i]["style"] == SCE_P_CLASSNAME:
                        # A "class Foo("- or "class Foo(Bar, Baz"-type of
                        # expression should provide class completions.
                        return PythonScopeCompletionContext(scope, name_part, AbstractClass, import_resolver=import_resolver)
                    elif tokens[i]["style"] == SCE_P_WORD and tokens[i]["text"] == "import":
                        # The original '(' or ',' token could be part of a
                        # "from foo import (bar, baz"-type of expression, which
                        # should provide member completions. Look for "from"
                        # and construct the full symbol name.
                        j = i - 1
                        while j >= 1:
                            if tokens[j]["style"] != SCE_P_IDENTIFIER and (tokens[j]["style"] != SCE_P_OPERATOR or tokens[j]["text"] != "."):
                                # The "import" token came after an unknown
                                # symbol and thus the original '(' or ',' token
                                # should not provide completions.
                                return None
                            j -= 1
                        if tokens[j]["style"] != SCE_P_WORD or tokens[j]["text"] != "from":
                            # The "import" token was not preceded by a "from"
                            # token and thus the original '(' or ',' token
                            # should not provide completions.
                            return None
                        # A "from foo import (bar, baz"-type of expression
                        # should provide member completions. However, in order
                        # for this to work, the top-level symbol needs to be
                        # imported into the current scope. This is necessary
                        # because the import resolver only imports the bits
                        # after "import", so the "from" context is lost.
                        # For example: "from foo import (bar" imports "bar" into
                        # the current scope, and "foo" is ignored. Thus
                        # "from foo import (bar, <|>" cannot fetch member
                        # completions for "foo" without importing "foo" into the
                        # current scope first.
                        top_level_name = tokens[j+1]["text"]
                        scope.define(Import(top_level_name, top_level_name))
                        symbol_name = "".join([token["text"] for token in tokens[j+1:i]])
                        return PythonMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver)
                    elif tokens[i]["style"] != SCE_P_IDENTIFIER and (tokens[i]["style"] != SCE_P_OPERATOR or tokens[i]["text"] not in (",", "(")):
                        # Unexpected token encountered. The original '(' or ','
                        # token is probably part of a typical expression.
                        break
                    i -= 1
                if tokens[i]["style"] == SCE_P_WORD and tokens[i]["text"] in ("import", "from") and tokens[i + 1]["style"] == SCE_P_OPERATOR and tokens[i + 1]["text"] == "(":
                    # A "from (foo"-, "import (foo"-, or "import (foo, bar"-type
                    # of expression is invalid and should not provide
                    # completions.
                    return None
            return PythonScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif leading_whitespace or name_part:
            if tokens[-1]["style"] == SCE_P_WORD and tokens[-1]["text"] == "import":
                # If the first significant token behind the position is an
                # "import" token, it's either a "from foo import bar"- or
                # "import foo"-type of expression. Figure out which one in order
                # to provide member or scope completions, respectively.
                if len(tokens) >= 3 and tokens[-2]["style"] == SCE_P_IDENTIFIER:
                    i = len(tokens) - 3
                    while i >= 1:
                        # Skip back through "name." token pairs in order to
                        # construct the full "from" import name.
                        if tokens[i]["style"] != SCE_P_OPERATOR or tokens[i]["text"] != "." or tokens[i - 1]["style"] != SCE_P_IDENTIFIER:
                            break
                        i -= 2
                    if tokens[i]["style"] == SCE_P_WORD and tokens[i]["text"] == "from":
                        # A "from foo import bar"-type of expression should
                        # provide member completions. However, in order for this
                        # to work, the top-level symbol needs to be imported
                        # into the current scope. This is necessary because the
                        # import resolver only imports the bits after "import",
                        # so the "from" context is lost.
                        # For example: "from foo import bar" imports "bar" into
                        # the current scope, and "foo" is ignored. Thus
                        # "from foo import bar, <|>" cannot fetch member
                        # completions for "foo" without importing "foo" into the
                        # current scope first.
                        top_level_name = tokens[i+1]["text"]
                        scope.define(Import(top_level_name, top_level_name))
                        symbol_name = "".join([token["text"] for token in tokens[i+1:-1]])
                        return PythonMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver)
                scope.define(Import("*", None)) # for import resolver
                return PythonScopeCompletionContext(scope, name_part, AbstractModule, import_resolver=import_resolver)
            elif tokens[-1]["style"] == SCE_P_WORD and tokens[-1]["text"] == "from":
                # A "from foo"-type of expression should provide module
                # completions.
                scope.define(Import("*", None)) # for import resolver
                return PythonScopeCompletionContext(scope, name_part, AbstractModule, import_resolver=import_resolver)
            elif tokens[-1]["style"] == SCE_P_IDENTIFIER and tokens[-1]["end_line"] == line:
                # A "foo.bar baz"-type of expression should not provide
                # completions.
                return None
            elif tokens[-1]["style"] == SCE_P_WORD and tokens[-1]["text"] == "except":
                # An "except Foo"-type of expression should provide class
                # completions.
                # TODO: limit to classes that inherit from Exception?
                return PythonScopeCompletionContext(scope, name_part, AbstractClass, import_resolver=import_resolver)
            elif tokens[-1]["style"] == SCE_P_WORD and tokens[-1]["text"] in ("class", "def"):
                # A "class Foo"- or "def bar"-type of expression should not
                # provide completions.
                return None
            else:
                # If there is no significant token immediately behind the
                # position, it's probably part of a typical expression, which
                # should provide scope completions.
                return PythonScopeCompletionContext(scope, name_part, import_resolver=import_resolver)

        return None

    def getGotoDefinitionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getGotoDefinitionContext()."""
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        # Note: The SilverCity tokenizer returns tokens with 0-based lines.
        # While many CodeIntel legacy parsers perform adjustments such that
        # tokens use 1-based lines, since we are using the Python parser
        # directly, no adjustment is made, so compute a 0-based line based on
        # position.
        lines = self.content[:position].split("\n")
        line, column = len(lines) - 1, len(lines[-1])

        scope = scope.resolveScope(line)
        if self.__class__ == PythonScanner:
            import_resolver = PythonImportResolver(filename, env)
        elif self.__class__ == Python3Scanner:
            import_resolver = Python3ImportResolver(filename, env)
        else:
            raise RuntimeError("'%s' scanner does not have an AbstractImportResolver" % self.__class__.__name__)

        for i in range(len(self.tokens)):
            token = self.tokens[i]
            if token["start_line"] <= line and line <= token["end_line"] and token["start_column"] <= column and column <= token["end_column"]:
                if token["style"] == SCE_P_IDENTIFIER:
                    # If the entity at the position is an identifier, retrieve
                    # the fully-qualified name up to and including the position.
                    j = i + 1
                    while i > 0:
                        if self.tokens[i - 1]["style"] == SCE_P_OPERATOR and self.tokens[i - 1]["text"] == ".":
                            i -= 2
                        else:
                            break
                    symbol_name = "".join([token["text"] for token in self.tokens[i:j]])
                    return GotoDefinitionContext(scope, symbol_name, import_resolver=import_resolver)
                elif token["style"] in (SCE_P_CLASSNAME, SCE_P_DEFNAME):
                    # If the entity at the position is a class or function name,
                    # use it.
                    symbol_name = token["text"]
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
        # Note: The SilverCity tokenizer returns tokens with 0-based lines.
        # While many CodeIntel legacy parsers perform adjustments such that
        # tokens use 1-based lines, since we are using the Python parser
        # directly, no adjustment is made, so compute a 0-based line based on
        # position.
        lines = self.content[:position].split("\n")
        line, column = len(lines) - 1, len(lines[-1])

        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in self.tokens:
            if token["start_line"] <= line:
                if token["start_line"] < line:
                    # Keep all non-whitespace tokens prior to the current line.
                    if token["style"] != SCE_P_DEFAULT:
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position.
                    if token["style"] != SCE_P_DEFAULT:
                        tokens.append(token)
                        if token["style"] == SCE_P_OPERATOR and column <= token["end_column"] and token["start_column"] != token["end_column"]:
                            # The tokenizer generally groups all operator
                            # characters into a single token. Chop off any
                            # bits that occur after the current position.
                            # Otherwise, something like "(<|>)" will be
                            # considered to be a "()" token instead of '('.
                            token["text"] = token["text"][:-(token["end_column"] - column + 1)]

        # Now look back through the token list for a function call and provide
        # its call tip context.
        i = len(tokens) - 1
        paren_level = -1
        while i > 0:
            if tokens[i]["style"] == SCE_P_OPERATOR:
                paren_level -= tokens[i]["text"].count(")")
                paren_level += tokens[i]["text"].count("(")
                if paren_level == 0:
                    i -= 1
                    break
            i -= 1
        if paren_level != 0 or tokens[i]["style"] not in (SCE_P_IDENTIFIER, SCE_P_DECORATOR):
            return None # no function call = no call tip

        # Retrieve the fully-qualified function name.
        j = i + 1
        while i > 0:
            if tokens[i - 1]["style"] == SCE_P_OPERATOR and tokens[i - 1]["text"] == ".":
                i -= 2
                if tokens[i]["style"] in (SCE_P_STRING, SCE_P_CHARACTER):
                    # An expression like "''.join()" should be replaced with
                    # "str.join()".
                    tokens[i]["text"] = "str"
                elif tokens[i]["style"] == SCE_P_OPERATOR:
                    if tokens[i]["text"].endswith("]"):
                        # An expression like "[].append()" should be replaced
                        # with "list.append()".
                        tokens[i]["text"] = "list"
                    elif tokens[i]["text"].endswith("}"):
                        # An expression like "{}.values()" should be replaced
                        # with "dic.values()".
                        tokens[i]["text"] = "dict" # TODO: or set
                    elif tokens[i]["text"].endswith(")"):
                        pass # TODO: function call
            else:
                break
        symbol_name = "".join([token["text"] for token in tokens[i:j]])
        symbol_name = symbol_name.lstrip("@")

        scope = scope.resolveScope(line)
        if self.__class__ == PythonScanner:
            import_resolver = PythonImportResolver(filename, env)
        elif self.__class__ == Python3Scanner:
            import_resolver = Python3ImportResolver(filename, env)
        else:
            raise RuntimeError("'%s' scanner does not have an AbstractImportResolver" % self.__class__.__name__)
        return CallTipContext(scope, symbol_name, import_resolver=import_resolver)

    def getFindReferencesContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getFindReferencesContext()."""
        context = self.getGotoDefinitionContext(filename, position, env)
        if not context:
            return None
        # For unit tests, any relative paths in PYTHONPATH should be resolved at
        # this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PYTHONPATH" in env:
                dirs = env["PYTHONPATH"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PYTHONPATH"] = os.pathsep.join(dirs)
        if self.__class__ == PythonScanner:
            import_resolver = PythonImportResolver(filename, env)
        elif self.__class__ == Python3Scanner:
            import_resolver = Python3ImportResolver(filename, env)
        else:
            raise RuntimeError("'%s' scanner does not have an AbstractImportResolver" % self.__class__.__name__)
        return PythonFindReferencesContext(context.scope, context.symbol_name, self, env, import_resolver=import_resolver)

class Python3Scanner(PythonScanner):
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

        self.content = _convert3to2(self.content)

        return super(Python3Scanner, self).scan(self.content, env)

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    from language.legacy.python.stdlib import PYTHON2_STDLIB_FILE, PYTHON3_STDLIB_FILE
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    _parser = argparse.ArgumentParser(description="Scan Python source files")
    _parser.add_argument("-2", action="store_const", const=True, default=True)
    _parser.add_argument("-3", action="store_const", const=True)
    _parser.add_argument("file", nargs='?')
    args = _parser.parse_args(sys.argv[1:])
    start = time.time()
    if getattr(args, "3"):
        scanner = Python3Scanner(PYTHON3_STDLIB_FILE)
    elif getattr(args, "2"):
        scanner = PythonScanner(PYTHON2_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
