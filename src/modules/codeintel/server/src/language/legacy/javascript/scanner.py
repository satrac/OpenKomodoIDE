# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for JavaScript source code.
CodeIntel v2's original JavaScript parser has been adapted to operate with the
new CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla C++ lexer (via
SilverCity), and walks through the token list, producing something akin to an
Abstract Syntax Tree in the end for database storage.
Completion contexts also utilize the token list.
The C++ lexer is used because JavaScript's syntax is quite similar after
keyword substitution.
"""

import json
import logging
import operator
import os
import re

from symbols import AbstractScope, AbstractModule
from calltips import CallTipContext
from completions import Completions, AbstractMemberCompletionContext, AbstractScopeCompletionContext, ApproximateSymbolContext
from goto_definition import GotoDefinitionContext
from find_references import AbstractFindReferencesContext
from language.common import Scope, Constant, Struct, Class, ClassVariable, Function, Constructor, Method, PrivateMethod, StaticMethod, AnonymousFunction, Argument, Import, AbstractScanner, AbstractScannerContext, FilenameSyntaxDescription, SymbolResolver
from language.legacy.javascript.jsdoc import JSDoc
from language.legacy.javascript.import_resolver import JavaScriptImportResolver, NodeJSImportResolver
from language.legacy.udl import AbstractUDLSubScanner

from db.model.helpers import fileExists, fetchSymbolsInFile, fetchSymbolsInDirectories, fetchFilesInDirectory, fetchAllFilesInDirectory

import SilverCity
from SilverCity.Lexer import Lexer
from SilverCity import ScintillaConstants
from SilverCity.ScintillaConstants import (
    SCE_C_COMMENT, SCE_C_COMMENTDOC, SCE_C_COMMENTDOCKEYWORD,
    SCE_C_COMMENTDOCKEYWORDERROR, SCE_C_COMMENTLINE,
    SCE_C_COMMENTLINEDOC, SCE_C_DEFAULT, SCE_C_IDENTIFIER, SCE_C_NUMBER,
    SCE_C_OPERATOR, SCE_C_STRING, SCE_C_CHARACTER, SCE_C_STRINGEOL, SCE_C_WORD,
    SCE_UDL_CSL_COMMENT, SCE_UDL_CSL_COMMENTBLOCK, SCE_UDL_CSL_DEFAULT,
    SCE_UDL_CSL_IDENTIFIER, SCE_UDL_CSL_NUMBER, SCE_UDL_CSL_OPERATOR,
    SCE_UDL_CSL_REGEX, SCE_UDL_CSL_STRING, SCE_UDL_CSL_WORD,
)

log = logging.getLogger("codeintel.javascript.lang")
#log.setLevel(logging.DEBUG)

# States used by JavaScriptScanner when parsing information
S_DEFAULT = 0
S_IN_ARGS = 1
S_IN_ASSIGNMENT = 2
S_IGNORE_SCOPE = 3
S_OBJECT_ARGUMENT = 4

# Types used by JavaScriptScanner when parsing information
TYPE_NONE = 0
TYPE_FUNCTION = 1
TYPE_VARIABLE = 2
TYPE_GETTER = 3
TYPE_SETTER = 4
TYPE_MEMBER = 5
TYPE_OBJECT = 6
TYPE_CLASS = 7
TYPE_PARENT = 8
TYPE_ALIAS = 9

class JavaScriptLexer(Lexer):
    def __init__(self):
        self._properties = SilverCity.PropertySet()
        self._lexer = SilverCity.find_lexer_module_by_id(ScintillaConstants.SCLEX_CPP)
        self._keyword_lists = [
            SilverCity.WordList(' '.join(set([
                "arguments", "await", "break", "case", "catch", "class",
                "const", "continue", "debugger", "default", "delete", "do",
                "else", "enum", "eval", "export", "extends", "false", "finally",
                "for", "function", "if", "implements", "import", "in",
                "instanceof", "interface", "let", "new", "null", "of",
                "package", "private", "protected", "public", "return", "static",
                "super", "switch", "this", "throw", "true", "try", "typeof",
                "var", "void", "while", "with", "yield"
            ]).union(set([
                "Infinity", "NaN", "abstract", "boolean", "break", "byte",
                "case", "catch", "char", "class", "const", "continue",
                "debugger", "default", "delete", "do", "double", "else", "enum",
                "export", "extends", "false", "final", "finally", "float",
                "for", "function", "goto", "if", "implements", "import", "in",
                "instanceof", "int", "interface", "long", "native", "new",
                "null", "package", "private", "protected", "public", "return",
                "short", "static", "super", "switch", "synchronized", "this",
                "throw", "throws", "transient", "true", "try", "typeof",
                "undefined", "var", "void", "volatile", "while", "with"
            ])))),
            SilverCity.WordList(),
            SilverCity.WordList(),
            SilverCity.WordList(),
            SilverCity.WordList()
        ]

# Dictionary of known js types and what they map to
known_javascript_types = {
    "object":       "Object",
    "obj":          "Object",
    "function":     "Function",
    "array":        "Array",
    "string":       "String",
    "text":         "String",
    "int":          "Number",
    "integer":      "Number",
    "number":       "Number",
    "numeric":      "Number",
    "decimal":      "Number",
    "short":        "Number",
    "unsigned short": "Number",
    "long":         "Number",
    "unsigned long":"Number",
    "float":        "Number",
    "bool":         "Boolean",
    "boolean":      "Boolean",
    "true":         "Boolean",
    "false":        "Boolean",
    "date":         "Date",
    "regexp":       "RegExp",
    # Dom elements
    "element":      "Element",
    "node":         "Node",
    "domnode":      "DOMNode",
    "domstring":    "DOMString",
    "widget":       "Widget",
    "domwidget":    "DOMWidget",
    "htmlelement":  "HTMLElement",
    "xmldocument":  "XMLDocument",
    "htmldocument": "HTMLDocument",
    # Special
    "xmlhttprequest": "XMLHttpRequest",
    "void":          "",
    # Mozilla special
    "UTF8String":    "String",
    "AString":       "String",
    # JQuery special
    "anything":      "Object",
}

def standardizeJSType(vartype):
    """Return a standardized name for the given type if it is a known type.

    Example1: given vartype of "int", returns "Number"
    Example2: given vartype of "YAHOO.tool", returns "YAHOO.tool"
    """

    if vartype:
        typename = known_javascript_types.get(vartype.lower(), None)
        if typename is None:
            #print "Unknown type: %s" % (vartype)
            return vartype
        return typename

# Everything is JS is an object.... MUMUHAHAHAHAHAHAAAA.......

class JSObject:
    def __init__(self, name, parent, lineno, depth, type=None,
                 doc=None, isLocal=False, isHidden=False, path=None, pos=None):
        self.name = name
        self.parent = parent
        self.cixname = self.__class__.__name__[2:].lower()
        self.line = lineno
        self.lineend = -1
        self.depth = depth
        self.type = type
        self.path = path
        self.pos = pos # Used for argument positions - 0 indexed.
        self._class = None  # Used when part of a class
        self.classes = {} # declared sub-classes
        self.members = {} # all private member variables used in class
        self.variables = {} # all variables used in class
        self.functions = {}
        self.anonymous_functions = [] # anonymous functions declared in scope
        self.attributes = []    # Special attributes for object
        self.returnTypes = []    # List of possible return values
        self.constructor = None
        self.classrefs = []
        self.doc = doc
        self.metadata = None
        self.isHidden = isHidden  # Special case, should not be output to cix
        if isLocal:
            # XXX: TODO: It may be appropriate to just use private..., although
            #            my feeling of the difference between the two names
            #            is that private elements should still be listed in
            #            completions from the class itself, whereas local
            #            should not...
            #
            # Local has a special meaning within the javascript tree evaluator,
            # elements with a "__local__" attribute will not be included in js
            # codeintel completion results.
            self.attributes.append("__local__")
            # Private has a special meaning within the code browser,
            # an element with a "private" attribute shows a small lock icon.
            # Private also has special meaning for jsdoc purposes, where it
            # means not to show documentation for these elements.
            self.attributes.append("private")

        self.doc = doc
        self.jsdoc = None
        if self.doc:
            # Turn the doc list into a JSDoc object
            self.jsdoc = JSDoc("".join(self.doc))

    def setParent(self, parent):
        # Validate the parent/child relationship. This is to avoid possible
        # recursion errors - bug 85481.
        seen_scopes = [self]
        parent_scope = parent
        while parent_scope:
            if parent_scope in seen_scopes:
                log.error("setParent:: recursion error, scope: %r, "
                          "parent: %r" % (self.name, parent_scope.name))
                return
            seen_scopes.append(parent_scope)
            parent_scope = parent_scope.parent
        self.parent = parent

    def getFullPath(self):
        if self.parent:
            return self.parent.getFullPath() + [self.name]
        return [self.name]

    def addAttribute(self, attr):
        if attr not in self.attributes:
            self.attributes.append(attr)

    def removeAttribute(self, attr):
        while attr in self.attributes:
            self.attributes.remove(attr)

    def hasChildren(self):
        return self.variables or self.functions or self.classes or self.members

    def isAnonymous(self):
        return self.name.startswith("(anonymous")

    def addClassRef(self, baseclass):
        assert isinstance(baseclass, str), "baseclass %r is not a str" % (baseclass,)
        if baseclass not in self.classrefs:
            self.classrefs.append(baseclass)

    def _mergeVariables(self, existingvar, newvar):
        # If there is no citdl type yet, assign it the given type
        if newvar.type and not existingvar.type:
            log.debug("marging VAR:%s, setting type: %r", existingvar.name, newvar.type)
            existingvar.type = newvar.type
        # TODO: We could choose the simpler type, i.e. if types were "foo" or
        #       "int", then we should choose "int" as it's a base JS type.
        #if newvar.type and newvar.type != existingvar.type:
        #    print "  %r existing type: %r,  new type: %r" % (existingvar.name, existingvar.type, newvar.type)
        # See if the locality has changed - bug 93726.
        if "__file_local__" in existingvar.attributes and \
           not "__file_local__" in newvar.attributes:
            existingvar.removeAttribute("__file_local__")
            # This should be the main definition of this variable - so prefer
            # other fields (like citdl) too.
            if newvar.type:
                existingvar.type = newvar.type

    def addVariable(self, name, value=None, metadata=None):
        """Add a variable in this scope (possibly as a property)
        @param name {unicode} The name of the variable
        @param value {JSObject} The value of the variable
        @param metadata {dict} Extra metadata about the assignment
        @returns {JSObject} The resulting variable (possibly different from
                |value| if one with the given |name| exists)
        """
        v = self.variables.get(name, None)
        if v is None:
            v = self.members.get(name, None)
        if v is None:
            log.debug("VAR:%s, line:%d, type:%r, scope:%r, meta:%r",
                      name, value.line, value.type, self.name, metadata)
            v = value
            self.variables[name] = v
        else:
            self._mergeVariables(v, value)
        if metadata:
            if v.metadata is None:
                v.metadata = metadata
            else:
                v.metadata.update(metadata)
        return v

    def addMemberVariable(self, name, value):
        """Add a member variable to this object
        @param name {unicode} The name of the member
        @param value {JSObject} The value to add
        @returns {JSObject} The resulting member (possibly different from
                |value| if one with the given |name| exists)
        """
        assert isinstance(value, JSObject), \
            "addMemberVariable: bad value%r" % (value,)
        v = self.members.get(name, None)
        if v is None:
            v = self.variables.get(name, None)
        if v is None:
            log.debug("CLASSMBR: %r, in %s %r", name, self.cixname, self.name)
            self.members[name] = v = value
        else:
            self._mergeVariables(v, value)
        return v

    def getReturnType(self):
        """Get the JS return type for this function, JSDoc gets precedence."""
        bestType = None
        if self.jsdoc and self.jsdoc.returns:
            bestType = self.jsdoc.returns.paramtype
        elif len(self.returnTypes) > 0:
            d = {}
            bestCount = 0
            bestType = None
            for rtype in self.returnTypes:
                if isinstance(rtype, str):
                    count = d.get(rtype, 0) + 1
                    d[rtype] = count
                    if count > bestCount:
                        bestType = rtype
        if bestType:
            bestType = standardizeJSType(bestType)
        return bestType

    def __repr__(self):
        return "\n".join(self.outline())

    def outline(self, depth=0):
        result = []
        if self.cixname == "function":
            s = "%s%s %s(%s)" % (" " * depth, self.cixname, self.name, ", ".join(self.args))
            r = self.getReturnType()
            if r:
                s += " => %s" % (r, )
            result.append(s)
        elif self.cixname == "class" and self.classrefs:
            result.append("%s%s %s [%s]" % (" " * depth, self.cixname, self.name, self.classrefs))
        elif self.cixname == "variable" and (self.type or (self.jsdoc and self.jsdoc.type)):
            result.append("%s%s %s [%s]" % (" " * depth, self.cixname, self.name, self.type or (self.jsdoc and self.jsdoc.type)))
        else:
            result.append("%s%s %s" % (" " * depth, self.cixname, self.name))
        for attrname in ("classes", "members", "functions", "variables"):
            d = getattr(self, attrname, {})
            for v in list(d.values()):
                result += v.outline(depth + 2)
        return result

    def toElementTree(self, cixelement):
        if not self.name:
            log.debug("%s has no name, line: %d, ignoring it.",
                      self.cixname, self.line)
            return None
        if self.cixname == "function":
            cixobject = createCixFunction(cixelement, self.name)
        elif self.cixname in ("object", "variable"):
            cixobject = createCixVariable(cixelement, self.name)
        elif self.cixname in ("class"):
            cixobject = createCixClass(cixelement, self.name)
        #else:
        #    print "self.cixname: %r" %(self.cixname)

        cixobject.attrib["line"] = str(self.line)
        if self.lineend >= 0:
            cixobject.attrib["lineend"] = str(self.lineend)
        if ADD_PATH_CIX_INFO and self.path:
            cixobject.attrib["path"] = self.path

        jsdoc = self.jsdoc
        if jsdoc:
            #print "jsdoc: %r" % (jsdoc)
            # the docstring
            #docElem.text = self.doc
            attributeDocs = []
            if jsdoc.isDeprecated():
                attributeDocs.append("DEPRECATED")
                self.attributes.append("deprecated")
            if jsdoc.isPrivate():
                attributeDocs.append("PRIVATE")
                if "private" not in self.attributes:
                    self.attributes.append("private")
            if jsdoc.isStatic():
                attributeDocs.append("STATIC")
                if "__static__" not in self.attributes:
                    self.attributes.append("__static__")
            if jsdoc.isConstant():
                attributeDocs.append("CONSTANT")
                if "constant" not in self.attributes:
                    self.attributes.append("constant")
            if jsdoc.isConstructor() and self.cixname == "function":
                attributeDocs.append("CONSTRUCTOR")
                if "__ctor__" not in self.attributes:
                    self.attributes.append("__ctor__")
            if jsdoc.is__local__():
                attributeDocs.append("__LOCAL__")
                if "__local__" not in self.attributes:
                    self.attributes.append("__local__")
            if jsdoc.tags:
                cixobject.attrib["tags"] = jsdoc.tags
            if jsdoc.doc:
                if attributeDocs:
                    setCixDoc(cixobject, "%s: %s" % (" ".join(attributeDocs), jsdoc.doc))
                else:
                    setCixDoc(cixobject, jsdoc.doc)

        # Additional one-off attributes
        if self.attributes:
            cixobject.attrib["attributes"] = " ".join(self.attributes)

        # Additional meta-data.
        if self.metadata:
            for key, value in list(self.metadata.items()):
                cixobject.attrib[key] = value

        # Add the type information, JSDoc overrides whatever the ciler found
        if jsdoc and jsdoc.type:
            # Convert the value into a standard name
            addCixType(cixobject, standardizeJSType(jsdoc.type))
        elif self.type:
            assert isinstance(self.type, str), \
                "self.type %r is not a str" % (self.type)
            addCixType(cixobject, standardizeJSType(self.type))

        if isinstance(self, JSFunction):
            signature = "%s(" % (self.name)
            # Add function arguments
            if self.args:
                signature += ", ".join(self.args)
                # Add function arguments to tree
            # Add signature - calltip
            signature += ")"
            cixobject.attrib["signature"] = signature
            # Add return type for functions, JSDoc gets precedence
            returnType = self.getReturnType()
            if returnType:
                addCixReturns(cixobject, returnType)

            # Add a "this" member for class functions
            if self._class:
                createCixVariable(cixobject, "this", vartype=self._class.name)
            elif self.parent and self.parent.cixname in ("object", "variable"):
                createCixVariable(cixobject, "this", vartype=self.parent.name)

        if self.cixname == "class":
            for baseclass in self.classrefs:
                addClassRef(cixobject, baseclass)
        if self.jsdoc and self.jsdoc.baseclasses:
            for baseclass in self.jsdoc.baseclasses:
                if baseclass not in self.classrefs:
                    addClassRef(cixobject, baseclass)

        # Note that arguments must be kept in the order they were defined.
        variables = list(self.variables.values())
        arguments = [x for x in variables if isinstance(x, JSArgument)]
        variables = [x for x in variables if not isinstance(x, JSArgument)]
        allValues = sorted(arguments, key=operator.attrgetter("pos", "name")) + \
                    sorted(list(self.functions.values()) + list(self.members.values()) + \
                           list(self.classes.values()) + variables + \
                           self.anonymous_functions,
                            key=operator.attrgetter("line", "name"))

        # If this is a variable with child elements, yet has a citdl type of
        # something that is not an "Object", don't bother to adding these child
        # elements, as we will just go with what the citdl information holds.
        # http://bugs.activestate.com/show_bug.cgi?id=78484
        # Ideally the ciler should include this information and have the tree
        # handler combine the completions from the citdl and also the child
        # elements, but this is not yet possible.
        if allValues and self.cixname == 'variable' and \
           cixobject.get("citdl") and cixobject.get("citdl") not in ("Object", "require()"):
            log.debug("Variable of type: %r contains %d child elements, "
                      "ignoring them.", cixobject.get("citdl"), len(allValues))
            return None

        # Sort and include contents
        for v in allValues:
            if not v.isHidden:
                v.toElementTree(cixobject)

        return cixobject

    def toAbstractSymbol(self, enclosingScope=None):
        if not self.name:
            log.debug("%s has no name, line: %d, ignoring it.",
                      self.cixname, self.line)
            return None

        symbol = None
        name = self.name
        type = None
        if self.jsdoc and self.jsdoc.type:
            type = standardizeJSType(self.jsdoc.type)
        elif self.type:
            type = standardizeJSType(self.type)

        if isinstance(self, JSFunction):
            returnType = self.getReturnType()
            if self.jsdoc:
                if self.jsdoc.isStatic():
                    symbol = StaticMethod(name, "Function", returnType, enclosingScope, JavaScriptScannerContext(self))
                elif self.jsdoc.isPrivate():
                    symbol = PrivateMethod(name, "Function", returnType, enclosingScope, JavaScriptScannerContext(self))
            # Add a "this" member for class functions.
            if self._class:
                if "__ctor__" in self.attributes:
                    symbol = Constructor(name, "Function", returnType, enclosingScope, JavaScriptScannerContext(self))
                else:
                    symbol = Method(name, "Function", returnType, enclosingScope, JavaScriptScannerContext(self))
                symbol.define(Struct("this", self._class.name, enclosingScope))
            elif self.parent and self.parent.cixname in ("object", "variable"):
                symbol = Method(name, "Function", returnType, enclosingScope, JavaScriptScannerContext(self))
                symbol.define(Struct("this", self.parent.name, enclosingScope))
            elif self.isAnonymous():
                symbol = AnonymousFunction("Function", returnType, enclosingScope, JavaScriptScannerContext(self))
            else:
                symbol = Function(name, "Function", returnType, enclosingScope, JavaScriptScannerContext(self))
        elif isinstance(self, JSClass):
            superclassName = self.classrefs
            if self.jsdoc and self.jsdoc.baseclasses:
                for baseclass in self.jsdoc.baseclasses:
                    superclassName.append(baseclass)
            if len(superclassName) == 1:
                superclassName = superclassName[0]
            symbol = Class(name, enclosingScope, superclassName, JavaScriptScannerContext(self))
            if self.metadata and self.metadata["used_prototype"]:
                symbol.define(ClassVariable("prototype", name))
        elif isinstance(self, JSArgument) and not self.variables:
            symbol = Argument(name, type, JavaScriptScannerContext(self))
        elif isinstance(self, JSAlias) and type == "require()" and self.metadata:
            symbol = Import(name, self.metadata["required_library_name"], JavaScriptScannerContext(self))
        elif isinstance(self, (JSObject, JSVariable)):
            if type != "Object":
                symbol = Struct(name, type, enclosingScope, JavaScriptScannerContext(self))
            else:
                # TODO: Since everything is an object, type resolution gets
                # ugly. Technically an anonymous class that inherits from Struct
                # would be best.
                symbol = Struct(name, None, enclosingScope, JavaScriptScannerContext(self))
        else:
            raise TypeError("Unexpected JS entity type '%s'" % self.__class__.__name__)

        # Note that arguments must be kept in the order they were defined.
        variables = list(self.variables.values())
        arguments = [x for x in variables if isinstance(x, JSArgument)]
        variables = [x for x in variables if not isinstance(x, JSArgument)]
        allValues = sorted(arguments, key=operator.attrgetter("pos", "name")) + \
                    sorted(list(self.functions.values()) + list(self.members.values()) + \
                           list(self.classes.values()) + variables + \
                           self.anonymous_functions,
                            key=operator.attrgetter("line", "name"))

        for v in allValues:
            if not v.isHidden and v.name:
                if isinstance(symbol, AbstractScope):
                    symbol.define(v.toAbstractSymbol(symbol))
                else:
                    log.debug("Attempt to add member %s to non-scope symbol %r", v.name, symbol)

        return symbol

class JSVariable(JSObject):
    def __init__(self, name, parent, line, depth, vartype='', doc=None,
                 isLocal=False, path=None, pos=None):
        if isinstance(vartype, list):
            vartype = ".".join(vartype)
        JSObject.__init__(self, name, parent, line, depth, type=vartype,
                          doc=doc, isLocal=isLocal, path=path, pos=pos)

class JSAlias(JSVariable):
    """An alias, which is a simple assignment from a variable (or possibly a
    class)."""
    def __init__(self, *args, **kwargs):
        """Initialize the alias
        @param target {list of unicode} The type name expression (e.g. ["Array"]
                or ["window", "document"]) to alias to
        @param scope {JSObject} The scope in which the assignment takes place,
                used to resolve the target
        @see JSVariable.__init__ - all other arguments are inherited
        """
        target = kwargs.pop("target", None)
        assert target is not None, "JSAlias requires a target= keyword argument"
        scope = kwargs.pop("scope", None)
        assert scope is not None, "JSAlias requires a scope= keyword argument"
        JSVariable.__init__(self, *args, **kwargs)
        self.cixname = "variable"
        self.target = target
        self.scope = scope

class JSArgument(JSVariable):
    """An argument for a function (or constructor)"""
    def __init__(self, *args, **kwargs):
        JSVariable.__init__(self, *args, **kwargs)
        self.cixname = "variable"
        if self.name.startswith("..."): # ES6 rest parameter (..arg)
            self.type = "Array"
            self.name = self.name[3:]
        if not self.type:# and ENABLE_HEURISTICS:
            if self.name == 'event': # assume that variables named event are Events
                log.debug("JSArgument: assuming argument named event is a Event")
                self.type = "Event"

    def outline(self, depth=0):
        result = []
        if self.type or (self.jsdoc and self.jsdoc.type):
            result.append("%sargument %s [%s]" % (" " * depth, self.name, self.type or (self.jsdoc and self.jsdoc.type)))
        else:
            result.append("%sargument %s" % (" " * depth, self.name))
        for attrname in ("classes", "members", "functions", "variables"):
            d = getattr(self, attrname, {})
            for v in list(d.values()):
                result += v.outline(depth + 2)
        return result

    def toElementTree(self, *args, **kwargs):
        cixelement = JSVariable.toElementTree(self, *args, **kwargs)
        if cixelement is not None:
            cixelement.attrib["ilk"] = "argument"
        return cixelement

class JSFunction(JSObject):
    """A JavaScript function"""
    def __init__(self, funcname, parent, args, lineno, depth=0, doc=None,
                 isLocal=False, isHidden=False, path=None):
        """Initialize the function
        @param funcname {unicode} The name of the function
        @param parent {JSObject} The parent scope
        @param args {JSArgs} The arguments to the function
        @param lineo {int} The line the function starts at
        @param depth {int}
        @param doc {list of unicode} Documentation comment for the function
        @param isLocal {bool} Whether this function is local
        @param isHidden {bool} Whether this function should be hidden (not
            output into the cix file)
        @param path {list of unicode} The path to the function
        """
        # funcname: string
        # args: list (or None)
        JSObject.__init__(self, funcname, parent, lineno, depth,
                          doc=doc, isLocal=isLocal, isHidden=isHidden, path=path)
        if isinstance(parent, JSClass):
            self._class = parent
        self._parent_assigned_vars = []
        self._callers = set()
        self.args = list(args or [])
        for pos, arg in enumerate(self.args):
            self.addVariable(arg, JSArgument(name=arg, parent=self, line=lineno,
                                             depth=depth, pos=pos))

    ##
    # @rtype {string or JSObject} add this possible return type
    def addReturnType(self, rtype):
        self.returnTypes.append(rtype)

    def addCaller(self, caller, pos, line):
        """Add caller information to this function"""
        if isinstance(caller, (list, tuple)):
            caller = ".".join(caller)
        self._callers.add((pos, caller, line))

    def toElementTree(self, cixelement):
        if self.jsdoc:
            # fix up argument info from jsdoc
            for jsdocParam in self.jsdoc.params:
                var = self.variables.get(jsdocParam.paramname)
                if isinstance(var, JSArgument):
                    var.type = standardizeJSType(jsdocParam.paramtype)
                    # fake a JSDoc (since it's already all parsed)
                    var.jsdoc = JSDoc()
                    var.jsdoc.doc = jsdocParam.doc
        cixobject = JSObject.toElementTree(self, cixelement)
        if not cixobject:
            return cixobject
        for pos, caller, line in self._callers:
            SubElement(cixobject, "caller", citdl=caller,
                       pos=str(pos), line=str(line), attributes="__hidden__")
        return cixobject

class JSClass(JSObject):
    """A JavaScript class object (a function with a non-default .prototype)"""
    def __init__(self, name, parent, lineno, depth, doc=None, path=None):
        """Initialize the class
        @see JSObject.__init__
        """
        JSObject.__init__(self, name, parent, lineno, depth, doc=doc, path=path)
        self.constructor = name

class JSFile:
    """CIX specifies that a <file> tag have zero or more <module> children.
    In JavaScript this is a one-to-one relationship, so this class represents both
    (and emits the XML tags for both).
    """
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.parent = None
        self.cixname = self.__class__.__name__[2:].lower()

        self.functions = {} # functions declared in file
        self.anonymous_functions = [] # anonymous functions declared in file
        self.classes = {} # classes declared in file
        self.variables = {} # all variables used in file

    def __repr__(self):
        return "\n".join(self.outline())

    def getFullPath(self):
        return [self.name]

    def isAnonymous(self):
        return False

    def outline(self):
        result = ["File: %r" % (self.name) ]
        for attrname in ("classes", "functions", "variables"):
            d = getattr(self, attrname, {})
            for v in list(d.values()):
                result += v.outline(2)
        return result

    def hasChildren(self):
        return self.variables or self.functions or self.classes

    def _findScopeWithName(self, name, scopeStack, type="variables"):
        """Find a object somewhere on the given scope stack with the given name
        @param name {unicode} The name to look for
        @param scopeStack {list of JSObject} The scope stack, with the outermost
                (and therefore last searched) scope at the lowest index
        @param type {str} The type of property to look for, e.g. "variables",
                "classs", "functions"
        @returns {JSObject or None} The object found, or None
        """
        if not name:
            return None
        log.debug("_findScopeWithName: %r with name:%r in scopeStack:%r", type, name, scopeStack[-1].name)
        # Work up the scope stack looking for the name
        #for scopePos in range(len(scope) - 1, -1, -1):
        #    currentScope = scope[scopePos]
        for scopePos in range(len(scopeStack) - 1, -1, -1):
            currentScope = scopeStack[scopePos]
            #print "Looking in scope %r" % (currentScope.name)
            #print "Looking in %s: %r" % (currentScope.__class__.__name__,
            #                             currentScope.name)
            namesDict = getattr(currentScope, type, None)
            if namesDict:
                foundScope = namesDict.get(name)
                if foundScope:
                    log.debug("Found %r in scope:%r(%s)", name,
                              currentScope.name, currentScope.cixname)
                    return foundScope
        log.debug("NO scope found for: %r", name)
        return None

    def _lookupVariableType(self, varType, jsobject, scopeStack, depth=0):
        """Resolves a variable type string to a JSObject representing a well-
                known JavaScript type
        @param varType {str} The type to look up as a dot-separated string, e.g.
                "Foo.Bar.baz" or "Array"
        @param jsobject {JSObject} The starting JSObject candidate (in case
                varType is well-known)
        @param scopeStack {list} The scope stack, with the outermost (and
                therefore last searched) scope at the lowest index
        @param depth {int} (For internal use to prevent deep recursion)
        @returns {JSObject or None} The variable type, or None
        """
        #print "Looking for varType:%r in scope:%r" % (varType, scopeStack[-1].name)
        assert not varType or isinstance(varType, str), \
            "varType %r is not a string" % varType
        if depth < 10 and varType:
            # Don't look any further if it's a known type
            if varType.lower() in known_javascript_types:
                return jsobject
            sp = varType.split(".")
            #print "sp: %r" % (sp)
            namePos = 0
            while namePos < len(sp):
                name = sp[namePos]
                #print "sp[%d]: %r" % (namePos, name)
                foundScope = self._findScopeWithName(name, scopeStack, type="variables")
                alternateScopeStack = scopeStack
                while foundScope and isinstance(foundScope, JSArgument) and \
                  not foundScope.type and foundScope.parent in alternateScopeStack:
                    log.debug("found untyped argument %r on %r, trying next",
                              foundScope.name, foundScope.parent.name)
                    alternateScopeStack[alternateScopeStack.index(foundScope.parent):] = []
                    foundScope = self._findScopeWithName(name, alternateScopeStack, type="variables")
                if not foundScope:
                    #print "Trying member variables"
                    # Then look for a class members with this name
                    foundScope = self._findScopeWithName(name, scopeStack, type="members")
                    #if foundScope:
                    #    print "Found a member variable with this name"
                if not foundScope:
                    # Then look for a class with this name
                    #print "Trying class"
                    foundScope = self._findScopeWithName(name, scopeStack, type="classes")
                    if foundScope:
                        #print "Found a class with this name"
                        # Only search this scope now
                        scopeStack.append(foundScope)
                if not foundScope:
                    break # returns None
                #print "Found scope"
                if isinstance(foundScope, JSVariable):
                    #print "Recursively searching scope"
                    assert foundScope.type is None or isinstance(foundScope.type, str), \
                        "foundScope %r has invalid type %r" % (foundScope, foundScope.type)
                    foundScope = self._lookupVariableType(foundScope.type, foundScope, scopeStack, depth+1)
                #return self._lookupVariableType(foundType, scopeStack)
                namePos += 1
            #print "Returning: %s" % foundScope
            return foundScope
        return None
        #print "jsobject:%r" % (jsobject)
        #print "jsobject.type:%r" % (jsobject.type)

    def _lookupVariableTypes(self, jstypelist, scopeStack):
        """Work out variable types according to their namespace
        @param jstypelist {list of JSObject} The list of types that shold be
            examined
        @param scopeStack {list} The scope stack, with the outermost (and
            therefore last searched) scope at the lowest index
        @returns {None}
        """

        for jstype in jstypelist:
            if hasattr(jstype, "classes"):
                # Recursive lookup for the class variables
                self._lookupVariableTypes(list(jstype.classes.values()), scopeStack + [jstype])
            if hasattr(jstype, "functions"):
                # Recursive lookup for the function variables
                self._lookupVariableTypes(list(jstype.functions.values()), scopeStack + [jstype])
            if hasattr(jstype, "variables"):
                for jsvariable in list(jstype.variables.values()):
                    varType = jsvariable.type
                    if varType:
                        actualType = self._lookupVariableType(varType, jsvariable, scopeStack + [jstype])
                        if actualType and actualType != jsvariable:
                            if isinstance(actualType, JSVariable) and not actualType.hasChildren():
                                log.debug("variable %r: replacing type %r with %r",
                                          jsvariable.name, jsvariable.type, actualType.type)
                                jsvariable.type = actualType.type
                            else:
                                log.debug("variable %r: replacing type %r with %r",
                                          jsvariable.name, jsvariable.type, actualType.name)
                                jsvariable.type = actualType.name
            # Lookup function return type values
            if isinstance(jstype, JSFunction):
                for i in range(len(jstype.returnTypes)):
                    returnType = jstype.returnTypes[i]
                    #print "Looking up function return type: %r" % (returnType, )
                    if isinstance(returnType, str):
                        actualType = self._lookupVariableType(returnType, jstype, scopeStack + [jstype])
                        if actualType and actualType != jstype:
                            #print "actualType: %r" % (actualType, )
                            # Use the variable name if it's type is "Object"
                            if isinstance(actualType, JSVariable) and \
                               actualType.type != "Object":
                                #print "ActualType is: %r" % (actualType.type)
                                jstype.returnTypes[i] = actualType.type
                            else:
                                #print "ActualType is: %r" % (actualType.name)
                                jstype.returnTypes[i] = actualType.name

    def _updateClassConstructors(self, jsobject):
        """Recursively update the class constructor name of the given class
        @param jsobject {list of JSObject} The JSClasses (or things things
            containing the JSClasses) to find constructors to mark as ctors
        """
        if isinstance(jsobject, JSClass):
            if jsobject.constructor:
                jsfunc = self._findScopeWithName(jsobject.constructor, [jsobject], type='functions')
                if jsfunc and "__ctor__" not in jsfunc.attributes:
                    log.debug("Making function:%r the constructor for class:%r",
                              jsfunc.name, jsobject.name)
                    jsfunc.attributes.append("__ctor__")
        allObjects = list(jsobject.functions.values()) + list(jsobject.classes.values()) + \
                     list(jsobject.variables.values())
        if not isinstance(jsobject, JSFile):
            allObjects += list(jsobject.members.values())
        for subobj in allObjects:
            self._updateClassConstructors(subobj)

    def updateAllScopeNames(self):
        """We've gathered as much information as possible, update all scope
        names as best as possible."""

        log.debug("****************************************")
        log.debug("Finished scanning, updating all scope names")
        self._lookupVariableTypes([self], [])
        log.debug("Updating all class constructor names")
        self._updateClassConstructors(self)

    def addVariable(self, name, value=None, metadata=None):
        """Add the given variable to this file
        @see JSObject.addVariable
        """
        assert value is not None, "no value given"
        v = self.variables.get(name, None)
        if v is None:
            log.debug("VAR: %s on line %d, type:%r (class %r)", name, value.line,
                      value.type, type(value))
            self.variables[name] = v = value
        # Else if there is no citdl type yet, assign it the given type
        elif value.type and not v.type:
            log.debug("existing VAR:%s, setting type: %r", name, value.type)
            v.type = value.type
        if metadata:
            if v.metadata is None:
                v.metadata = metadata
            else:
                v.metadata.update(metadata)
        return v

    def convertToElementTreeModule(self, cixmodule):
        """Convert this file to a <scope> in the cix (DOM) tree
        @param cixmodule {Element} The <scope type="blob"> to write to
        """
        # Sort and include contents
        allValues = list(self.functions.values()) + list(self.variables.values()) + \
                    list(self.classes.values()) + self.anonymous_functions
        for v in sorted(allValues, key=operator.attrgetter("line", "name")):
            if not v.isHidden:
                v.toElementTree(cixmodule)

    def convertToElementTreeFile(self, cixelement, file_lang, module_lang=None):
        """Convert this file to a new .cix file
        @param cixelement {Element} The root <codeintel> element in a CIX file
        @param file_lang The language of this file (possibly not JavaScript in
            a mutli-language file)
        @param module_lang The language of this module (defaults to file_lang)
        """
        if module_lang is None:
            module_lang = file_lang
        cixfile = createCixFile(cixelement, self.path, lang=file_lang)
        cixmodule = createCixModule(cixfile, self.name, lang=module_lang,
                                    src=self.path)
        self.convertToElementTreeModule(cixmodule)

class JavaScriptScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "JavaScript"

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions(), but
        auto-imports all appropriate symbols into the current scope first.
        This is particularly helpful in JS files and frameworks where symbols
        are auto-magically imported.
        """
        if self.import_resolver:
            scope = self.scope
            while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                scope = scope.enclosingScope
            dirnames = self.import_resolver.env.get("JAVASCRIPTLIB", "").split(os.pathsep)
            dirnames = [_f for _f in dirnames if _f]
            name = None
            if self.name_part:
                name = self.name_part+"*"
            for symbol in fetchSymbolsInDirectories(dirnames, name, ext=".js"):
                if scope is self.scope:
                    scope.define(symbol)
                else:
                    # Merge top-level symbols with this scope's top-level
                    # symbols. This is needed, for example, when within a
                    # namespace that spans multiple files in order to show
                    # all namespace members (e.g. class names) in a scope
                    # completion.
                    symbol._enclosingScope = scope # override
                    resolved = scope.resolveMember(symbol.name)
                    if not isinstance(resolved, AbstractScope) or not isinstance(symbol, AbstractScope):
                        setattr(symbol, "_ctx", ApproximateSymbolContext(symbol)) # TODO: assumes "_ctx" attribute
                        scope.define(symbol)
                    else:
                        resolved.merge(symbol)
        return super(JavaScriptScopeCompletionContext, self).getCompletions()

class JavaScriptSymbolResolver(SymbolResolver):
    """JavaScript symbol resolver for fully-qualified symbol and type names that
    auto-imports all appropriate symbols into the current scope as needed.
    """
    def resolve(self, scope, symbol_name):
        symbol = super(JavaScriptSymbolResolver, self).resolve(scope, symbol_name)
        if not symbol:
            for dirname in self._import_resolver.env.get("JAVASCRIPTLIB", "").split(os.pathsep):
                if not dirname:
                    continue
                for symbol in fetchSymbolsInDirectories(dirname, None, ext=".js"):
                    symbol._enclosingScope = scope # override
                    if not isinstance(scope.resolveMember(symbol.name), AbstractScope) or not isinstance(symbol, AbstractScope):
                        scope.define(symbol)
                    else:
                        scope.resolveMember(symbol.name).merge(symbol)
            symbol = super(JavaScriptSymbolResolver, self).resolve(scope, symbol_name)
        return symbol

class JavaScriptMemberCompletionContext(AbstractMemberCompletionContext):
    """Implementation of AbstractMemberCompletionContext."""
    @property
    def language(self):
        return "JavaScript"

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions(), but
        auto-imports all appropriate symbols into the current scope first.
        """
        if self.import_resolver:
            scope = self.scope
            while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                scope = scope.enclosingScope
            dirnames = self.import_resolver.env.get("JAVASCRIPTLIB", "").split(os.pathsep)
            dirnames = [_f for _f in dirnames if _f]
            name = None
            if self.name_part:
                name = self.name_part+"*"
            for symbol in fetchSymbolsInDirectories(dirnames, name, ext=".js"):
                symbol._enclosingScope = scope # override
                resolve = scope.resolveMember(symbol.name)
                if not isinstance(resolve, AbstractScope) or not isinstance(symbol, AbstractScope):
                    scope.define(symbol)
                else:
                    resolve.merge(symbol)
        return super(JavaScriptMemberCompletionContext, self).getCompletions()

_jsDocSymbolNames = [
    "author", "constructor", "deprecated", "exception", "exports", "param",
    "private", "return", "returns", "see", "todo", "this", "throws", "version"
]
_jsDocSymbols = {}
for name in _jsDocSymbolNames:
    _jsDocSymbols[name] = Constant(name, None)

class JavaScriptDocCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext for JSDoc symbols."""
    @property
    def language(self):
        return "JavaScript"

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions() for
        JSDoc symbols.
        """
        jsdoc_symbols = {}
        for name, symbol in _jsDocSymbols.items():
            if name.startswith(self.name_part):
                jsdoc_symbols[name] = symbol
        return Completions("", jsdoc_symbols, self.name_part)

class JavaScriptScannerContext(AbstractScannerContext):
    """Implementation of AbstractScannerContext for the JavaScript scanner."""
    def __init__(self, jsObject):
        self._jsObject = jsObject
        self._signature = False # unset; cannot use None since it is a valid value

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        return self._jsObject.line

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation."""
        return self._jsObject.jsdoc and self._jsObject.jsdoc.doc or None

    @property
    def signature(self):
        """Implementation of AbstractSymbolContext.signature."""
        if not isinstance(self._jsObject, JSFunction):
            return None
        elif self._signature is False:
            signature = "%s(" % (self._jsObject.name)
            # Add function arguments
            if self._jsObject.args:
                signature += ", ".join(self._jsObject.args)
            # Add signature - calltip
            signature += ")"
            self._signature = signature
        return self._signature

    def contains(self, line):
        """Implementation of AbstractScannerContext.contains()."""
        if line >= self.line and (line <= self._jsObject.lineend or self._jsObject.lineend == -1 and (self._jsObject.hasChildren() or isinstance(self._jsObject, (JSFunction, JSClass)))):
            return True
        elif self._jsObject.hasChildren():
            # Since JS objects can be split over multiple lines (e.g. class
            # instance methods), check if members contain the given line.
            for obj in list(self._jsObject.functions.values()) + list(self._jsObject.classes.values()):
                if line >= obj.line and (line <= obj.lineend or obj.lineend == -1):
                    return True
        return False

class JavaScriptFindReferencesContext(AbstractFindReferencesContext):
    """Implementation of AbstractFindReferencesContext."""
    @property
    def projectFiles(self):
        """Implementation of AbstractFindReferencesContext.projectFiles."""
        if isinstance(self.scanner, NodeJSScanner):
            ext = ".js"
        else:
            ext = (".js", ".html")
        filenames = []
        for dirname in self.env.get("JAVASCRIPTLIB", "").split(os.pathsep):
            if not dirname:
                continue
            for filename in fetchAllFilesInDirectory(dirname, ext):
                filenames.append(filename)
        return filenames

class JavaScriptScanner(AbstractScanner):
    JS_COMMENT_STYLES = (SCE_C_COMMENT,
                        SCE_C_COMMENTDOC,
                        SCE_C_COMMENTLINE,
                        SCE_C_COMMENTLINEDOC,
                        SCE_C_COMMENTDOCKEYWORD,
                        SCE_C_COMMENTDOCKEYWORDERROR)
    UDL_COMMENT_STYLES = (SCE_UDL_CSL_COMMENT,
                          SCE_UDL_CSL_COMMENTBLOCK)

    def __init__(self, stdlib_file, lexer_class=JavaScriptLexer):
        super(JavaScriptScanner, self).__init__(stdlib_file)
        self.lexerClass = lexer_class
        if not isinstance(self, NodeJSScanner):
            # Assume JS is running in the browser, where top-level scope is
            # Window.
            self._builtInScope = self.builtInScope.resolve("Window")

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

        self.path = filename
        self.lang = "JavaScript"
        # hook up the lexical matches to a function that handles the token

        # Working variables, used in conjunction with state
        self.lineno = 0
        self.last_lineno = 0
        self.depth = 0
        self.styles = []
        self.text = []
        self.in_variable_definition = False  # for multi variable assignment
        self.comment = []
        self.last_comment_and_jsdoc = [None, None]
        self.argumentPosition = 0
        self.argumentTextPosition = 0  # keep track of arg position in self.text
        self.objectArguments = []

        # state : used to store the current JS lexing state
        # state_stack : used to store JS state to return to
        self.state = S_DEFAULT
        self.state_stack = []

        # JScile will store all references for what we scan in
        self.cile = JSFile(filename)
        # Cile information, used to store code structure
        self.currentScope = self.cile
        self._scopeStack = [self.currentScope]
        self.objectStack = [self.currentScope]
        self.currentClass = None
        # Used for determining Javascript closures
        self.bracket_depth = 0
        self.lastText = []
        self.lastScope = None

        self._metadata = {}
        self._anonid = 0
        self.anonFuncPending = False

        # Document styles used for deciding what to do
        # Note: Can be customized by calling setStyleValues()
        self.JS_DEFAULT     = SCE_C_DEFAULT
        self.JS_WORD        = SCE_C_WORD
        self.JS_IDENTIFIER  = SCE_C_IDENTIFIER
        self.JS_OPERATOR    = SCE_C_OPERATOR
        self.JS_STRINGS     = (SCE_C_STRING, SCE_C_CHARACTER, )
        self.JS_NUMBER      = SCE_C_NUMBER
        # js_cile styles are styles that the ciler uses
        self.JS_CILE_STYLES = self.JS_STRINGS + \
                              (self.JS_WORD, self.JS_IDENTIFIER,
                               self.JS_OPERATOR, self.JS_NUMBER)
        if self.lexerClass != JavaScriptLexer:
            self.setStyleValues(defaultStyle=SCE_UDL_CSL_DEFAULT,
                                wordStyle=SCE_UDL_CSL_WORD,
                                identiferStyle=SCE_UDL_CSL_IDENTIFIER,
                                operatorStyle=SCE_UDL_CSL_OPERATOR,
                                stringStyles=(SCE_UDL_CSL_STRING, ),
                                numberStyle=SCE_UDL_CSL_NUMBER,
                                commentStyles=self.UDL_COMMENT_STYLES)

        #XXX Should eventually use lang_javascript.JavaScriptLexer()
        #    because (1) it's word lists might differ and (2) the
        #    codeintel system manages one instance of it.
        self.tokens = [] # track for use by getCompletionContext()
        self.lexerClass().tokenize_by_style(self.content, self.token_next)
        # Ensure we take notice of any text left in the ciler
        self._endOfScanReached()
        #if updateAllScopeNames:
            # We've parsed up the JavaScript, fix any variables types
            #self.cile.updateAllScopeNames()
        self.cile.updateAllScopeNames()

        scope = Scope(self.builtInScope)
        allValues = list(self.cile.functions.values()) + list(self.cile.variables.values()) + \
                    list(self.cile.classes.values()) + self.cile.anonymous_functions
        for v in sorted(allValues, key=operator.attrgetter("line", "name")):
            if not v.isHidden:
                scope.define(v.toAbstractSymbol(scope))
        return scope

    def getCompletionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCompletionContext()."""
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        return self._getCompletionContext(filename, position, env, self.tokens, line, column, scope)

    def getGotoDefinitionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getGotoDefinitionContext()."""
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        return self._getGotoDefinitionContext(filename, position, env, self.tokens, line, column, scope)

    def getCallTipContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCallTipContext()."""
        # Scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        return self._getCallTipContext(filename, position, env, self.tokens, line, column, scope)

    def getFindReferencesContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getFindReferencesContext()."""
        context = self.getGotoDefinitionContext(filename, position, env)
        if not context:
            return None
        # For unit tests, any relative paths in the "fake" JAVASCRIPTLIB
        # should be resolved at this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "JAVASCRIPTLIB" in env:
                dirs = env["JAVASCRIPTLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["JAVASCRIPTLIB"] = os.pathsep.join(dirs)
        if isinstance(self, NodeJSScanner):
            import_resolver = NodeJSImportResolver(filename, env)
        elif isinstance(self, JavaScriptScanner):
            import_resolver = JavaScriptImportResolver(filename, env)
            # Include all symbols from files in the current directory.
            if os.path.exists(filename):
                dirname = os.path.dirname(filename)
                for basename in fetchFilesInDirectory(dirname, ".js", False, False):
                    for symbol in fetchSymbolsInFile(os.path.join(dirname, basename)):
                        context.scope.define(symbol)
        else:
            raise RuntimeError("'%s' scanner does not have an AbstractImportResolver" % self.__class__.__name__)
        return JavaScriptFindReferencesContext(context.scope, context.symbol_name, self, env, import_resolver=import_resolver, symbol_resolver_class=JavaScriptSymbolResolver)

    def _getCompletionContext(self, filename, position, env, _tokens, line, column, scope):
        """Helper method for fetching completion contexts.
        This method is called by both the stand-alone JavaScript scanner and the
        UDL JavaScript sub-scanner (via HTML), hence the wide variety of
        parameters.
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
        @param scope AbstractScope containing all scanned JavaScript symbols.
        @return AbstractCompletionContext or None
        """
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if token["line"] <= line:
                if token["line"] < line:
                    # Keep all non-whitespace tokens prior to the current line.
                    if token["style"] != self.JS_DEFAULT:
                        tokens.append(token)
                elif token["column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if token["style"] != self.JS_DEFAULT or (token["column"] + 1 <= column and column <= token["column"] + len(token["text"])):
                        tokens.append(token)
                        if token["style"] == self.JS_OPERATOR and column <= token["column"] + len(token["text"]) - 1 and token["column"] != token["column"] + len(token["text"]):
                            # The tokenizer generally groups all operator
                            # characters into a single token. Chop off any
                            # bits that occur after the current position.
                            # Otherwise, something like "(<|>)" will be
                            # considered to be a "()" token instead of '('.
                            token["text"] = token["text"][:-(token["column"] + len(token["text"]) - column)]
                elif token["column"] == column and token["style"] == self.JS_IDENTIFIER:
                    # Keep an identifier token that starts at the current
                    # position.
                    tokens.append(token)
        leading_whitespace = False
        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in the "fake" JAVASCRIPTLIB
        # should be resolved at this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "JAVASCRIPTLIB" in env:
                dirs = env["JAVASCRIPTLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["JAVASCRIPTLIB"] = os.pathsep.join(dirs)
        if isinstance(self, NodeJSScanner):
            import_resolver = NodeJSImportResolver(filename, env)
        elif isinstance(self, JavaScriptScanner):
            import_resolver = JavaScriptImportResolver(filename, env)
            # Include all symbols from files in the current directory.
            if os.path.exists(filename):
                dirname = os.path.dirname(filename)
                for basename in fetchFilesInDirectory(dirname, ".js", False, False):
                    for symbol in fetchSymbolsInFile(os.path.join(dirname, basename)):
                        scope.define(symbol)
        else:
            raise RuntimeError("'%s' scanner does not have an AbstractImportResolver" % self.__class__.__name__)
        name_part = "" # the "already typed" part of a symbol completion

        if len(tokens) == 0:
            # If the position is at the beginning of the buffer, provide scope
            # completions.
            return JavaScriptScopeCompletionContext(scope, import_resolver=import_resolver)

        # Pre-process the end of the token list.
        if tokens[-1]["style"] == self.JS_DEFAULT:
            # If the position follows whitespace, make a note since some
            # completions after whitespace like "if <|>" are possible, while
            # some like "if<|>" are not.
            leading_whitespace = True
            tokens.pop()
            if len(tokens) == 0:
                return JavaScriptScopeCompletionContext(scope, import_resolver=import_resolver)
        elif tokens[-1]["style"] == self.JS_IDENTIFIER:
            # If the position is within a symbol name, or at the beginning of
            # a symbol name, make a note of that name since it should be
            # considered as the "already typed" part of a completion list.
            name_part = tokens[-1]["text"]
            tokens.pop()
            if len(tokens) == 0:
                return JavaScriptScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif tokens[-1]["style"] == SCE_C_STRINGEOL or (tokens[-1]["style"] in self.JS_STRINGS and tokens[-1]["column"] + len(tokens[-1]["text"]) > column):
            if len(tokens) > 1 and tokens[-2]["style"] == self.JS_OPERATOR and tokens[-2]["text"] == "[":
                # If the position is within a string preceded by "[", it may be
                # part of a "foo.bar['baz"-type of expression. Keep track of the
                # string key, and replace the "[" token with a ".". That way a
                # JavaScriptMemberCompletionContext for "foo.bar" will be recognized
                # later.
                name_part = tokens[-1]["text"].strip("'\"")
                tokens[-2]["text"] = "."
                tokens.pop() # string
            elif len(tokens) > 2 and tokens[-2]["style"] == self.JS_OPERATOR and tokens[-2]["text"] == "(" and tokens[-3]["text"] == "require":
                # If the position is within the string preceded by "require(",
                # it is part of a "require('foo"-type of expression.
                dirname = os.path.exists(filename) and os.path.dirname(filename) or env.get("FILENAME", "")
                if isinstance(self, NodeJSScanner) and "./" not in tokens[-1]["text"]:
                    # Look for 'node_modules' and use that instead of dirname
                    # as the directory to look for modules in.
                    testdir = dirname
                    while testdir:
                        node_modules_dir = os.path.join(testdir, "node_modules")
                        if fetchFilesInDirectory(node_modules_dir): # test for existence
                            dirname = node_modules_dir
                        if os.path.dirname(testdir) == testdir:
                            break # at root
                        testdir = os.path.dirname(testdir)
                    if "node_modules" not in dirname and not env.get("JAVASCRIPTLIB"):
                        # A "require('foo"-type of expression with no
                        # "node_modules" directory or module path should not
                        # provide completions.
                        return None
                if "/" in tokens[-1]["text"]:
                    if column < tokens[-1]["column"] + len(tokens[-1]["text"]):
                        # It is possible the position comes before the last
                        # member (e.g. "<|>foo/bar" or "foo/<|>bar/baz"). If so,
                        # strip out the trailing members.
                        i = tokens[-1]["text"].find("/", column - tokens[-1]["column"])
                        if i != -1:
                            tokens[-1]["text"] = tokens[-1]["text"][:i]
                    tokens[-1]["text"] = tokens[-1]["text"].strip("'\"")
                    symbol_name = "/".join(tokens[-1]["text"].split("/")[:-1])
                    dirname = os.path.join(dirname, symbol_name)
                    name_part = tokens[-1]["text"].split("/")[-1]
                else:
                    name_part = tokens[-1]["text"].strip("'\"")
                # A "require('foo"-type of expression should provide scope
                # completions and a "require('foo/bar"-type of expression should
                # provide member completions.
                if "/" not in tokens[-1]["text"]:
                    scope.define(Import("*", dirname)) # for import resolver
                    return JavaScriptScopeCompletionContext(scope, name_part, AbstractModule, import_resolver=import_resolver)
                else:
                    scope.define(Import("%s/*" % symbol_name, dirname)) # for import resolver
                    return JavaScriptMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, syntax_description=FilenameSyntaxDescription)

        # Now look back through the token list and provide an appropriate
        # completion context.
        if tokens[-1]["style"] == self.JS_OPERATOR and tokens[-1]["text"] == ".":
            # If the first significant token behind the position is a '.', it's
            # probably part of a larger "foo.bar"-type of expression.
            i = len(tokens) - 1
            while i >= 1:
                # Skip back through "name." token pairs in order to construct
                # the full symbol name.
                if tokens[i]["style"] != self.JS_OPERATOR or tokens[i]["text"] != "." or tokens[i - 1]["style"] != self.JS_IDENTIFIER and not (tokens[i - 1]["style"] == self.JS_WORD and tokens[i - 1]["text"] == "this"):
                    break
                i -= 2
            symbol_name = "".join([token["text"] for token in tokens[i+1:-1]])
            if not symbol_name:
                if i > 0 and tokens[i - 1]["style"] in self.JS_STRINGS:
                    # A "'foo'.bar"-type of expression should provide string
                    # member completions.
                    return JavaScriptMemberCompletionContext(scope, "String", name_part)
            # A "foo.bar"-type of expression should provide member completions.
            return JavaScriptMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, symbol_resolver_class=JavaScriptSymbolResolver)
        elif tokens[-1]["style"] == self.JS_OPERATOR and tokens[-1]["text"].endswith(")."):
            # If the first significant tokens behind the position are ").",
            # it's probably part of a function call expression. Look back for
            # the function name.
            if tokens[-1]["text"].startswith("(") and len(tokens) > 1 and tokens[-2]["style"] == self.JS_IDENTIFIER:
                symbol_name = tokens[-2]["text"]
                # A "foo().bar"-type of expression should provide member
                # completions for the function's return value.
                return JavaScriptMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, symbol_resolver_class=JavaScriptSymbolResolver)
            open_parens = 1
            i = len(tokens) - 2
            while i >= 1:
                if tokens[i]["style"] == self.JS_OPERATOR and ")" in tokens[i]["text"]:
                    open_parens += 1
                if tokens[i]["style"] == self.JS_OPERATOR and "(" in tokens[i]["text"]:
                    open_parens -= 1
                if open_parens == 0:
                    if tokens[i - 1]["style"] == self.JS_IDENTIFIER:
                        symbol_name = tokens[i - 1]["text"]
                        # A "foo(...).bar"-type of expression should provide
                        # member completions for the function's return value.
                        return JavaScriptMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, symbol_resolver_class=JavaScriptSymbolResolver)
                    break
                i -= 1
            # A "this(foo).bar"- or "foo).bar"-type of expression should not
            # provide completions.
            return None
        elif tokens[-1]["style"] == self.JS_OPERATOR and tokens[-1]["text"] == "(":
            # If the first significant token behind the position is a '(',
            # it's probably part of a typical expression. However, if it is
            # within the argument list of a function definition, there should be
            # no completion context.
            tokens.pop()
            while len(tokens) > 0:
                # Look for a "function foo.bar(baz"-type of expression.
                if tokens[-1]["style"] == self.JS_WORD and tokens[-1]["text"] == "function":
                    # A "function foo.bar(baz"-type of expression should not
                    # provide completions.
                    return None
                elif tokens[-1]["style"] == self.JS_IDENTIFIER:
                    tokens.pop()
                    if len(tokens) > 2 and tokens[-1]["style"] == self.JS_OPERATOR and tokens[-1]["text"] == "." and tokens[-2]["style"] == self.JS_IDENTIFIER:
                        tokens.pop() # "."
                        tokens.pop() # identifier
                else:
                    # The original '(' token does not appear to be within the
                    # argument list of a function definition.
                    break
            # A typical "(foo"-type of expression should provide scope
            # completions.
            return JavaScriptScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif tokens[-1]["style"] == self.JS_WORD and tokens[-1]["text"] == "function":
            # A "function foo"-type of expression should not provide
            # completions.
            return None
        elif leading_whitespace or name_part:
            # If there is no significant token immediately behind the position,
            # it's probably part of a typical expression, which should provide
            # scope completions.
            return JavaScriptScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif tokens[-1]["style"] in self.JS_COMMENT_STYLES:
            match = re.search(r"\*\s*@(\w*)$", tokens[-1]["text"])
            if match:
                # A "* @foo"-type of expression in a comment should provide
                # JSDoc completions.
                return JavaScriptDocCompletionContext(scope, match.group(1))

        return None

    def _getGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Helper method for fetching goto definition contexts.
        This method is called by both the stand-alone JavaScript scanner and the
        UDL JavaScript sub-scanner (via HTML), hence the wide variety of
        parameters.
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
        @param scope AbstractScope containing all scanned JavaScript symbols.
        @return GotoDefinitionContext or None
        """
        scope = scope.resolveScope(line)
        if isinstance(self, NodeJSScanner):
            import_resolver = NodeJSImportResolver(filename, env)
        elif isinstance(self, JavaScriptScanner):
            import_resolver = JavaScriptImportResolver(filename, env)
            # Include all symbols from files in the current directory.
            dirname = os.path.dirname(filename)
            for basename in fetchFilesInDirectory(dirname, ".js", False, False):
                for symbol in fetchSymbolsInFile(os.path.join(dirname, basename)):
                    scope.define(symbol)
        else:
            raise RuntimeError("'%s' scanner does not have an AbstractImportResolver" % self.__class__.__name__)

        for i in range(len(tokens)):
            token = tokens[i]
            if token["line"] <= line and line <= token["line"] + token["text"].count("\n") and token["column"] <= column and column <= token["column"] + len(token["text"]) - 1:
                if token["style"] == self.JS_IDENTIFIER:
                    # If the entity at the position is an identifier, retrieve
                    # the fully-qualified name up to and including the position.
                    j = i + 1
                    while i > 0:
                        if tokens[i - 1]["style"] == self.JS_OPERATOR and tokens[i - 1]["text"] == ".":
                            i -= 2
                        else:
                            break
                    symbol_name = "".join([token["text"] for token in tokens[i:j]])
                    return GotoDefinitionContext(scope, symbol_name, import_resolver=import_resolver, symbol_resolver_class=JavaScriptSymbolResolver)
                elif token["style"] not in self.JS_CILE_STYLES and "\n" in token["text"]:
                    continue # straddling a newline; keep going
                break
            elif token["line"] > line:
                break

        return None

    def _getCallTipContext(self, filename, position, env, _tokens, line, column, scope):
        """Helper method for fetching call tip contexts.
        This method is called by both the stand-alone JavaScript scanner and the
        UDL JavaScript sub-scanner (via HTML), hence the wide variety of
        parameters.
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
        @param scope AbstractScope containing all scanned JavaScript symbols.
        @return CallTipContext or None
        """
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if token["line"] <= line:
                if token["line"] < line:
                    # Keep all non-whitespace tokens prior to the current line.
                    if token["style"] != self.JS_DEFAULT:
                        tokens.append(token)
                elif token["column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position.
                    if token["style"] != self.JS_DEFAULT:
                        tokens.append(token)
                        if token["style"] == self.JS_OPERATOR and column <= token["column"] + len(token["text"]) - 1 and token["column"] != token["column"] + len(token["text"]):
                            # The tokenizer generally groups all operator
                            # characters into a single token. Chop off any
                            # bits that occur after the current position.
                            # Otherwise, something like "(<|>)" will be
                            # considered to be a "()" token instead of '('.
                            token["text"] = token["text"][:-(token["column"] + len(token["text"]) - column)]

        # Now look back through the token list for a function call and provide
        # its call tip context.
        i = len(tokens) - 1
        paren_level = -1
        while i > 0:
            if tokens[i]["style"] == self.JS_OPERATOR:
                paren_level -= tokens[i]["text"].count(")")
                paren_level += tokens[i]["text"].count("(")
                if paren_level == 0:
                    i -= 1
                    break
            i -= 1
        if paren_level != 0 or tokens[i]["style"] != self.JS_IDENTIFIER:
            return None # no function call = no call tip

        # Retrieve the fully-qualified function name.
        j = i + 1
        while i > 0:
            if tokens[i - 1]["style"] == self.JS_OPERATOR and tokens[i - 1]["text"] == ".":
                i -= 2
                if tokens[i]["style"] in self.JS_STRINGS:
                    # An expression like "''.charAt()" should be replaced with
                    # "String.charAt()".
                    tokens[i]["text"] = "String"
            else:
                break
        if i > 0 and tokens[i - 1]["style"] == self.JS_WORD and tokens[i - 1]["text"] == "function":
            return None # no call tip for a function definition
        symbol_name = "".join([token["text"] for token in tokens[i:j]])

        scope = scope.resolveScope(line)
        if isinstance(self, NodeJSScanner):
            import_resolver = NodeJSImportResolver(filename, env)
        elif isinstance(self, JavaScriptScanner):
            import_resolver = JavaScriptImportResolver(filename, env)
            # Include all symbols from files in the current directory.
            dirname = os.path.dirname(filename)
            for basename in fetchFilesInDirectory(dirname, ".js", False, False):
                for symbol in fetchSymbolsInFile(os.path.join(dirname, basename)):
                    scope.define(symbol)
        else:
            raise RuntimeError("'%s' scanner does not have an AbstractImportResolver" % self.__class__.__name__)
        return CallTipContext(scope, symbol_name, import_resolver=import_resolver, symbol_resolver_class=JavaScriptSymbolResolver)

    # Allows to change styles used by scanner
    # Needed for UDL languages etc... where the style bits are different
    def setStyleValues(self, defaultStyle   = SCE_C_DEFAULT,
                             wordStyle      = SCE_C_WORD,
                             identiferStyle = SCE_C_IDENTIFIER,
                             operatorStyle  = SCE_C_OPERATOR,
                             stringStyles   = (SCE_C_STRING, SCE_C_CHARACTER, ),
                             numberStyle    = SCE_C_NUMBER,
                             commentStyles  = None):
        self.JS_DEFAULT     = defaultStyle
        self.JS_WORD        = wordStyle
        self.JS_IDENTIFIER  = identiferStyle
        self.JS_OPERATOR    = operatorStyle
        self.JS_STRINGS     = stringStyles
        self.JS_NUMBER      = numberStyle
        self.JS_CILE_STYLES = self.JS_STRINGS + \
                              (self.JS_WORD, self.JS_IDENTIFIER,
                               self.JS_OPERATOR, self.JS_NUMBER)
        if commentStyles:
            self.JS_COMMENT_STYLES = commentStyles

    def _logVariables(self):
        """Helper method to log about the current state"""
        if log.level >= logging.DEBUG:
            log.debug("    lineno:%r, state:%r, depth:%r", self.lineno,
                      self.state, self.depth)
            log.debug("    currentScope: %r", self.currentScope)
            log.debug("")

    def incBlock(self):
        """Increment the block (scope) count"""
        self.depth = self.depth+1
        log.debug("incBlock: depth:%d, line:%d, currentScope:%r", self.depth, self.lineno, self.currentScope.name)
        if not self.currentScope:
            log.debug("incBlock:: No currentScope available. Defaulting to global file scope.")
            # Use the global file scope then
            self.currentScope = self.cile
        if len(self.objectStack) == 0 or self.currentScope != self.objectStack[-1]:
            # Not the same scope...
            self.objectStack.append(self.currentScope)
        self._scopeStack.append(self.currentScope)

    def decBlock(self):
        """Decrement the block (scope) count"""
        log.debug("decBlock: depth:%d, line:%d, leavingScope:%r", self.depth, self.lineno, self.currentScope.name)
        if self.depth > 0:
            self.depth = self.depth-1
            self.lastScope = self.currentScope
            # Update lineend for scope
            if hasattr(self.currentScope, "lineend"):
                self.currentScope.lineend = self.lineno
                if isinstance(self.currentScope, JSClass) and \
                   len(self.currentScope.functions) == 1:
                    jsfunc = list(self.currentScope.functions.values())[0]
                    if jsfunc.depth == self.depth and jsfunc.lineend == -1:
                        jsfunc.lineend = self.lineno
                        log.debug("Setting lineend: %d for scope %r",
                                 self.lineno, jsfunc)
                elif isinstance(self.currentScope, JSFunction) and \
                    self.currentScope.isAnonymous():
                    # Anonymous functions may be immediately invoked.
                    # In these cases the contents actually belong to the
                    # previous scope. For example, a top-level, invoked
                    # anonymous function's contents belong in the file scope.
                    self.anonFuncPending = True
                    log.debug("Anonymous function %r's contents may apply to prior scope. Analyzing subsequent tokens for invocation...",
                              self.currentScope.name)
                log.debug("Setting lineend: %d for scope %r",
                         self.lineno, self.currentScope.name)
            else:
                log.debug("Current scope does not have a lineend: %r",
                         self.currentScope.name)
            self._scopeStack.pop()
            #assert(len(self._scopeStack) > 0)
            if self._scopeStack[-1] != self.objectStack[-1]:
                self.objectStack.pop()
                #assert(len(self.objectStack) > 0)
            self.currentScope = self._scopeStack[-1]
            log.debug("decBlock: currentScope:%r", self.currentScope.name)
            if not self.currentScope:
                log.debug("decBlock:: No currentScope available. Defaulting to global file scope.")
                # Use the global file scope then
                self.currentScope = self.cile
                return
            # Update currentClass variable
            oldCurrentClass = self.currentClass
            if isinstance(self.currentScope, JSClass):
                self.currentClass = self.currentScope
                log.debug("Currentclass now: %r", self.currentClass.name)
            elif isinstance(self.currentScope, JSFunction):
                self.currentClass = self.currentScope._class
                if self.currentClass:
                    log.debug("Currentclass now: %r", self.currentClass.name)
                else:
                    log.debug("Currentclass now: %r", self.currentClass)
            else:
                self.currentClass = None
                log.debug("Currentclass now: %r", self.currentClass)
            # Update line number for the current class if it doesn't have one already
            if oldCurrentClass and oldCurrentClass.lineend == -1 and \
               oldCurrentClass != self.currentClass:
                oldCurrentClass.lineend = self.lineno
        else: # Likely there is a syntax error in the document
            log.debug("decBlock:: Scope already at 0. Document has syntax errors.")

    def _findInScope(self, name, attrlist=("variables", ), scope=None):
        """Find the object of the given name in the given scope
        @param name {str} The name of the object to look for
        @param attrlist {seq of str} The attributes to look into (e.g.,
            "variables", "functions", "classes")
        @param scope {JSObject} The scope in which to find the object
        @returns {JSObject or None} The found object, an immediate child of the
            scope.
        """
        assert scope is not None, "Missing scope"
        for attr in attrlist:
            namesDict = getattr(scope, attr, None)
            if namesDict:
                subscope = namesDict.get(name)
                if subscope:
                    log.debug("_findInScope: Found a scope for: %r in %s.%s:",
                              name, scope.name, attr)
                    return subscope
        # Not found
        return None

    def _resolveAlias(self, namelist, scope=None):
        """Resolve the given alias
        @param namelist {seq of str} The path to the alias to resolve
        @param scope {JSObject} The starting scope; defaults to the current scope
        @returns {tuple of scope, {seq of str}} The scope and namelist for the
            (recursively) resolved namelist; alternatively, tuple (None, None)
            if the alias could not be resolved (e.g. not an alias)
        """
        if not namelist:
            return (None, None)
        namelist = namelist[:]
        if scope is None:
            scope = self.currentScope
        lastScope, lastNamelist = scope, namelist[:]
        log.debug("_resolveAlias: Resolving alias %r in scope %r", namelist, scope.name)
        aliasDepth = 0 # prevent infinite loop
        found = False
        while namelist:
            log.debug("_resolveAlias: Looking for %r in scope %r", namelist, scope.name)
            namesDict = getattr(scope, "variables", None)
            if not namesDict:
                # this scope has no variables
                log.debug("_resolveAlias: scope %r has no variables", scope.name)
                break
            name = namelist.pop(0)
            foundVar = namesDict.get(name)
            if foundVar is None:
                # can't find the wanted name
                log.debug("_resolveAlias: scope %r does not have variable %r", scope.name, name)
                break
            if isinstance(foundVar, JSAlias) and aliasDepth < 10:
                lastScope = scope = foundVar.scope
                namelist = foundVar.target + namelist
                if namelist[-1].endswith("()"):
                    # resolved to a function call; drop the call syntax
                    namelist[-1] = namelist[-1][:-2]
                lastNamelist = namelist[:]
                aliasDepth += 1
                log.debug("_resolveAlias: found alias %r on %r; new names %r",
                          foundVar.name, scope.name, namelist)
            else:
                # found a non-alias variable
                scope = foundVar
        log.debug("_resolveAlias: finished resolve: scope %r namelist %r depth %r",
                  lastScope.name, lastNamelist, aliasDepth)
        if aliasDepth == 0:
            return (None, None)
        return (lastScope, lastNamelist)

    def _locateScopeForName(self, namelist, attrlist=("variables", ), scope=None):
        """Find the scope referenced by namelist, or if it is not itself a scope,
        the containing scope
        @param namelist {seq of str} The path to the object, e.g. ["foo", "bar"]
        @param attrlist {seq of str} The types of attributes to look at
        @param scope {JSObject} The starting scope; defaults to the current scope
        @returns {JSObject} The object, if it is a scope; otherwise, the scope
            containing the object, if it is a variable.
        """

        if not namelist:
            return None
        namelist = namelist[:] # copy
        if scope is None:
            scope = self.currentScope
        log.debug("Finding in scope: %s.%r with names: %r", scope.name, attrlist, namelist)
        # If variables are defined, also search members, which is the
        # correstponding group for class scopes.
        if "variables" in attrlist:
            attrlist += ("members", )
            noVariables = False
        else:
            # add variables because aliases are stored there
            attrlist += ("variables", )
            noVariables = True
        # Work up the scope stack looking for the classname
        aliasDepth = 0
        firstAliasFound = None
        while scope:
            currentScope = scope
            log.debug("Looking in scope %r", currentScope.name)
            foundScope = None
            namePos = -1;
            while namePos < len(namelist) - 1:
                namePos = namePos + 1
                name = namelist[namePos]
                #attrToLookIn = "variables"
                #if len(namelist) == 0:
                for attrToLookIn in attrlist:
                    log.debug("Looking for name:%r in %s of scope with name:%r",
                              name, attrToLookIn, currentScope.name)
                    namesDict = getattr(currentScope, attrToLookIn, None)
                    if namesDict:
                        foundScope = namesDict.get(name)
                        if isinstance(foundScope, JSAlias) and aliasDepth < 10:
                            if firstAliasFound is None:
                                firstAliasFound = foundScope
                            # This is an alias; look again with its target
                            namelist[:namePos+1] = foundScope.target
                            currentScope = scope = foundScope.scope
                            namePos = -1
                            aliasDepth += 1
                            log.debug("_locateScopeForName: encountered alias %r, restarting with %r on %r",
                                      name, namelist, scope.name)
                            break # continue outer for loop
                        elif attrToLookIn == "variables" and noVariables:
                            # we didn't originally want to pick up variables
                            continue
                        if foundScope:
                            log.debug("_locateScopeForName: Found scope %r for: %r", foundScope.name, name)
                            # Look in this sub-scope if we have more names to check
                            if type != "variables" or namePos < len(namelist) - 1:
                                currentScope = foundScope
                            # else we've located the scope we want
                            break # goes to else: of outer for loop at end of namelist
                else:
                    # Not found
                    break
            else:
                log.debug("Found %r in scope:%s.%s", namelist, currentScope.name, attrToLookIn)
                return currentScope
            # Try parent scope
            scope = scope.parent
        if firstAliasFound:
            log.debug("Found alias, but no alias target, returning just the alias: %r", firstAliasFound.name)
            return firstAliasFound
        log.debug("NO scope found for: %r", namelist)
        return None

    ##
    # Create a JSFunction and add it to the current scope
    # @param namelist {list} list of names for the function
    # @param args {list} list of arguments for the function
    # @param doc {list} list of comment strings for given scope
    #
    def addFunction(self, namelist, args=None, doc=None, isLocal=False,
                    isHidden=False):
        log.debug("AddFunction: %s(%s)", namelist, args)
        funcName = namelist[-1]
        toScope = self.currentScope
        if len(namelist) > 1:
            isLocal = False
            scopeNames = namelist[:-1]
            if "prototype" in namelist:
                pIndex = namelist.index("prototype")
                scopeNames = namelist[:pIndex]
                jsclass = self._addClassPart(funcName, self.ADD_CLASS_FUNCTION, scopeNames, args=args, doc=doc, path=self.path)
                jsclass.metadata = {"used_prototype": True}
                # Ensure we onte the currentClass which we'll be working with
                self.currentClass = jsclass
                return
            else:
                toScope = self._findOrCreateScope(namelist[:-1], ('variables', 'classes', 'functions'))
        elif isinstance(toScope, JSFile):
            isLocal = False
        log.debug("FUNC: %s(%s) isLocal:%r adding to %s %r", funcName, args,
                  isLocal, toScope.cixname, toScope.name)
        #log.debug("jsdoc: %r", JSDoc("".join(doc)))
        fn = JSFunction(funcName, toScope, args, self.lineno, self.depth,
                        doc=doc, isLocal=isLocal, isHidden=isHidden)
        toScope.functions[fn.name] = fn
        self.currentScope = fn
        # Special jsdoc parameter telling us explicitly that it's a class
        jsdoc_says_class = False
        if fn.jsdoc and fn.jsdoc.isClass():
            jsdoc_says_class = True
        # Also check the last comment, sometimes it's meant for this scope
        if not jsdoc_says_class and self.last_comment_and_jsdoc[0]:
            last_jsdoc = self.last_comment_and_jsdoc[1]
            if last_jsdoc is None:
                last_jsdoc = JSDoc("".join(self.last_comment_and_jsdoc[0]))
                self.last_comment_and_jsdoc[1] = last_jsdoc
                if last_jsdoc.isClass() and \
                   fn.name == last_jsdoc.classname:
                    # Name is same, check the namespace as well if it exists
                    nspc = reversed(last_jsdoc.namespace.split("."))
                    scope = fn.parent
                    for name in nspc:
                        if scope is None or name != scope.name:
                            break
                        scope = scope.parent
                    else:
                        jsdoc_says_class = True
                        fn.jsdoc = last_jsdoc
                        log.debug("last_jsdoc classname: %r, namespace: %r",
                                  last_jsdoc.classname, last_jsdoc.namespace)
        if fn.name and jsdoc_says_class:
            # Ick, this is really a class constructor
            jsclass = self._convertFunctionToClass(fn)
            jsclass.doc = None
            jsclass.jsdoc = None

    def _createAnonymousFunctionName(self):
        self._anonid += 1
        return "(anonymous %d)" % (self._anonid, )

    ##
    # Create an anonymous JSFunction and add it to the current scope.
    # @param args {list} list of arguments for the function
    # @param doc {list} list of comment strings for given scope
    #
    def addAnonymousFunction(self, args=None, doc=None, isHidden=False):
        name = self._createAnonymousFunctionName()
        log.debug("addAnonymousFunction: %s(%s)", name, args)
        toScope = self.currentScope
        fn = JSFunction(name, toScope, args, self.lineno, self.depth,
                        doc=doc, isLocal=True, isHidden=isHidden,
                        path=self.path)
        toScope.anonymous_functions.append(fn)
        self.currentScope = fn
        return fn

    ##
    # Create a JSFunction and add it to the current scope
    # @param namelist {list} list of names for the function
    # @param args {list} list of arguments for the function
    # @param doc {list} list of comment strings for given scope
    #
    def addClassFunction(self, namelist, args=None, doc=None):
        log.debug("AddClassFunction: %s(%s)", namelist, args)
        toScope = self.currentClass
        if not toScope:
            # See if it's a function, we'll convert it into a class then
            if isinstance(self.currentScope, JSFunction):
                toScope = self._convertFunctionToClass(self.currentScope)
        if not toScope or len(namelist) > 1:
            self.addFunction(namelist, args, doc)
        else:
            funcName = namelist[-1]
            log.debug("FUNC: %s(%s) on line %d", funcName, args, self.lineno)
            fn = JSFunction(funcName, toScope, args, self.lineno, self.depth, doc=doc)
            toScope.functions[fn.name] = fn
            self.currentScope = fn

    ADD_CLASS = 0
    ADD_CLASS_MEMBER = 1
    ADD_CLASS_VARIABLE = 2
    ADD_CLASS_FUNCTION = 3
    ADD_CLASS_PARENT = 4
    ADD_CLASS_CONSTRUCTOR = 5
    def _addClassPart(self, partName, addType, scopeNames=None, args=None, doc=None, path=None, varCtor=JSVariable):
        """Add something to this class
        @param partName {unicode} The name of the thing to add
        @param addType {int} What sort of thing to add; can be one of ADD_CLASS
                (add a new child class), ADD_CLASS_MEMBER (a member varaible),
                ADD_CLASS_VARIABLE (a member variable), ADD_CLASS_FUNCTION (a
                method), ADD_CLASS_PARENT (an object on this class's prototype
                chain), ADD_CLASS_CONSTRUCTOR (a constructor function)
        @param scopeNames {list of unicode} The expression to locate the part
        @param args {JSArguments} The arguments to a method when using
                ADD_CLASS_FUNCTION
        @param doc {list of unicode} The documentation comment for this part
        @param path {list of unicode}
        @param varCtor {callable} The constructor function to create a variable
                for use with ADD_CLASS_MEMBER / ADD_CLASS_VARIABLE; the
                signature should match that of JSVariable
        """
        log.debug("_addClassPart: partName:%r, addType:%r, scopeNames:%r, args:%r",
                  partName, addType, scopeNames, args)
        jsclass = None
        fn = None
        # Find the class to place this part into
        #jsclass = self._findClassWithNames(scopeNames)
        if scopeNames:
            # Look for the class first, then if we don't find it look for
            # a function or variable: bug 70324
            jsclass = self._locateScopeForName(scopeNames, attrlist=("classes", ))
            # Note: We could have an alias object, in that case we look for
            #       something better, see test "variable_aliasing_komodo.js"
            if jsclass is None or not isinstance(jsclass, JSClass):
                jsclass = self._locateScopeForName(scopeNames, attrlist=("classes", "functions", "variables", ))
                if isinstance(jsclass, JSFunction):
                    # Convert it to a class
                    jsclass = self._convertFunctionToClass(jsclass)
        else:
            jsclass = self.currentClass
        if not jsclass and scopeNames:
            if len(scopeNames) > 1:
                toScope = self._findOrCreateScope(scopeNames, attrlist=("classes", "functions", "variables", ))
            else:
                toScope = self.currentScope
            className = scopeNames[-1]
            jsclass = JSClass(className, toScope, self.lineno, self.depth, doc=doc, path=path)
            self.currentScope.classes[jsclass.name] = jsclass
            log.debug("CLASS: %r on line %d in %r at depth %d", jsclass.name,
                      jsclass.line, self.currentScope.name, self.depth)
            self.currentScope = jsclass

        if addType == self.ADD_CLASS_FUNCTION:
            log.debug("CLASS_FUNC: %s(%s) on line %d", partName, args, self.lineno)
            fn = JSFunction(partName, jsclass, args, self.lineno, self.depth, doc=doc)
            fn._class = jsclass
            jsclass.functions[fn.name] = fn
            #print "num functions: %d" % (len(jsclass.functions))
            self.currentScope = fn
        elif addType == self.ADD_CLASS_MEMBER:
            if partName not in jsclass.variables:
                log.debug("CLASS_MBR added: %r", partName)
                v = varCtor(partName, jsclass, self.lineno, self.depth, doc=doc, path=self.path)
                jsclass.variables[partName] = v
            else:
                log.debug("CLASS_MBR already exists: %r", partName)
        elif addType == self.ADD_CLASS_VARIABLE:
            if partName not in jsclass.variables:
                log.debug("CLASS_VBR added: %r", partName)
                v = varCtor(partName, jsclass, self.lineno, self.depth, doc=doc, path=self.path)
                jsclass.variables[partName] = v
            else:
                log.debug("CLASS_MBR already exists: %r", partName)
        elif addType == self.ADD_CLASS_PARENT:
            log.debug("CLASS_PARENT: %r", partName)
            jsclass.addClassRef(partName)
        elif addType == self.ADD_CLASS_CONSTRUCTOR:
            log.debug("CLASS_CTOR: %r", partName)
            jsclass.constructor = partName

        if jsclass:
            self.currentClass = jsclass
            if addType == self.ADD_CLASS:
                self.currentScope = jsclass
            elif addType == self.ADD_CLASS_PARENT and partName == "Object":
                self.currentScope = jsclass
        return jsclass

    # a class part using prototype.name = function
    #def addClassPart(self):
    #    self._addClassPart()

    # a class using classname.prototype = { ... }
    def addClass(self, namelist, doc=None, path=None, varCtor=JSVariable):
        """Add a new class using scope.classname.prototype = {...}
        @param namelist {list of unicode} The path to get to the class; in the
                example above, ["scope", "classname"]
        @param doc {list of unicode} Documentation comment associated with this class
        @param varCtor {callable} The constructor function to create a variable
                for use with ADD_CLASS_MEMBER / ADD_CLASS_VARIABLE; the
                signature should match that of JSVariable
        """
        jsclass = self._addClassPart(namelist[-1], self.ADD_CLASS, scopeNames=namelist, doc=doc, path=path, varCtor=varCtor)
        return jsclass

    def addAnonymousClass(self, namelist, doc=None):
        # Example syntax: c.prototype = { rows: { return this._rows.length; } }
        self.addClass(namelist[:-1], doc=doc)

    def addClassOrVariableMember(self, namelist, typeNames, scope=None, doc=None,
                                 assignAsCurrentScope=False,
                                 isLocal=False, varCtor=JSVariable):
        """Add the variable to the given scope or current scope

        If the scope is a
          * variable or object: add as a variable to this scope.
          * class: add as a member to this class.
          * function: then this is a little more tricky:
            * If it's class function, then add as a member for the class.
            * If it's a function inside a variable/object, then add as a
              variable to the variable/object.
            * If it's just a function on it's own, turn the function into a
              class and then add a member variable for the class.
        """
        if not scope:
            scope = self.currentScope

        log.debug("addClassOrVariableMember: namelist:%r, type:%r, isLocal:%r, scope (%s):%s",
                  namelist, typeNames, isLocal, scope.cixname, scope.name)
        memberName = namelist[-1]

        if len(namelist) > 2 and "prototype" in namelist:
            pIndex = namelist.index("prototype")
            scopeNames = namelist[:pIndex]
            log.debug("Adding class prototype. class name: %r, variable: %r",
                      scopeNames, memberName)
            scope = self._addClassPart(memberName, self.ADD_CLASS_MEMBER,
                                       scopeNames=scopeNames, args=None, doc=doc,
                                       path=self.path, varCtor=varCtor)
            scope.metadata = {"used_prototype": True}
            v = scope

        elif len(namelist) >= 2:
            # Find the scope to apply to.
            scope = self._findOrCreateScope(namelist[:-1],
                                            attrlist=("variables", "classes"),
                                            fromScope=scope)
            v = varCtor(memberName, scope, self.lineno, self.depth,
                        vartype=typeNames, doc=doc, isLocal=isLocal, path=self.path)
            v = scope.addVariable(memberName, value=v)
            if assignAsCurrentScope:
                self.currentScope = v

        elif scope.cixname in ("object", "variable"):
            if isLocal:
                log.warn("addClassOrVariableMember: %s:%d Trying to add %r as "
                         "a local member variable??",
                         self.cile.name, self.lineno, namelist)
                return
            v = varCtor(memberName, scope, self.lineno, self.depth,
                        vartype=typeNames, doc=doc, isLocal=isLocal, path=self.path)
            v = scope.addVariable(memberName, value=v)
            if assignAsCurrentScope:
                self.currentScope = v

        # Special case - classes and anonymous functions, but not anonymous
        # classes (which as a parent of an anon function will have the same
        # name).
        elif isinstance(scope, JSClass) or \
             (scope.isAnonymous() and not scope.parent.name == scope.name):
            v = varCtor(memberName, scope, self.lineno, self.depth,
                        vartype=typeNames, doc=doc, isLocal=isLocal, path=self.path)
            v = scope.addMemberVariable(memberName, value=v)
            if assignAsCurrentScope:
                self.currentScope = v

        elif isinstance(scope, JSFunction):
            # If it's a function already within a class, then thats okay
            parentScope = scope.parent
            if not parentScope:
                log.debug("addClassOrVariableMember: ignoring assignment %r "
                          "into a dummy function", namelist)
                return None
            log.debug("ParentScope is: %s (%s)", parentScope.name, parentScope.cixname)
            if isinstance(parentScope, JSClass):
                self.currentClass = parentScope
                log.debug("Assigning to parent class: %r:%r",
                          parentScope.cixname, parentScope.name)
                v = varCtor(memberName, parentScope, self.lineno, self.depth,
                            typeNames, doc=doc, isLocal=isLocal, path=self.path)
                v = parentScope.addMemberVariable(memberName, value=v)
                if assignAsCurrentScope:
                    self.currentScope = v
            # If it's a function within a variable, then thats okay too
            elif parentScope and parentScope.cixname in ("object", "variable"):
                log.debug("Assigning to parent scope: %r:%r",
                          parentScope.cixname, parentScope.name)
                v = varCtor(memberName, parentScope, self.lineno, self.depth,
                            vartype=typeNames, doc=doc, isLocal=isLocal, path=self.path)
                v = parentScope.addVariable(memberName, value=v)
                # We need to keep track of what we assign in this particular
                # case, as we may later turn this function into it's own class,
                # and then we'll need to grab these "this." variables back!
                # Example code:
                #   var ko = {}
                #   ko.f1 = function() { this.x = 1; }   // x assigned to ko
                #   ko.f1.prototype.run = function() {}  // convert f1 to class
                #   // Now we want ko.x to move into class ko.f1
                scope._parent_assigned_vars.append(v)

                if assignAsCurrentScope:
                    self.currentScope = v
            # Convert the function to class then
            else:
                # If the class name exists already, assign to that class
                func = scope
                funcName = func.name
                jsclass = self._locateScopeForName([funcName], attrlist=("classes", ), scope=scope)
                if not jsclass:
                    log.debug("Creating class %r, function %r now ctor", funcName, funcName)
                    # Turn function into a constructor for the class
                    jsclass = self._convertFunctionToClass(func)
                else:
                    # Update the function class information
                    self._convertFunctionToClassContructor(func, jsclass)
                v = varCtor(memberName, jsclass, self.lineno, self.depth,
                            typeNames, doc=doc, isLocal=isLocal, path=self.path)
                v = jsclass.addMemberVariable(memberName, value=v)
                if assignAsCurrentScope:
                    self.currentScope = v
        elif isinstance(scope, JSFile):
            v = self.addVariable(namelist, typeNames, scope, doc,
                                 assignAsCurrentScope, isLocal,
                                 varCtor=varCtor)
        else:
            log.debug("addClassOrVariableMember:: Invalid scope type. Could not add %r to scope: %r - %r",
                      namelist, scope.cixname, scope.name)
            v = None
        return v

    def addClassParent(self, namelist, typeNames):
        log.debug("addClassParent: namelist:%r, typeNames:%r", namelist, typeNames)
        self._addClassPart(".".join(typeNames), self.ADD_CLASS_PARENT, namelist[:-1], path=self.path)

    def addGetter(self, namelist, typeNames, scopeNames=None, doc=None):
        log.debug("addGetter: namelist:%r, type: %r, scopeNames: %r", namelist,
                  typeNames, scopeNames)
        if scopeNames:
            toScope = self._locateScopeForName(scopeNames, attrlist=("variables", "classes"))
            if not toScope:
                log.debug("addGetter:: Not adding getter. Could not find scope for: %r",
                         scopeNames)
                return
            self.currentScope = toScope
        else:
            toScope = self.currentScope
        self.addClassOrVariableMember(namelist, typeNames, toScope, doc=doc)

    def addSetter(self, namelist, scopeNames=None, doc=None):
        log.debug("addSetter: namelist:%r, scopeNames: %r", namelist, scopeNames)
        if scopeNames:
            toScope = self._locateScopeForName(scopeNames, attrlist=("variables", "classes"))
            if not toScope:
                log.debug("addSetter:: Not adding setter. Could not find scope for: %r",
                         scopeNames)
                return
            self.currentScope = toScope
        else:
            toScope = self.currentScope
        self.addClassOrVariableMember(namelist, [], toScope, doc=doc)

    def _convertFunctionToClassContructor(self, jsfunc, jsclass):
        # Mark it as a constructor if it's not already so marked
        funcName = jsfunc.name
        # Copy attributes across, except for "__ctor__"
        class_attributes = jsfunc.attributes[:]
        if "__ctor__" not in jsfunc.attributes:
            jsfunc.attributes.append("__ctor__")
        else:
            class_attributes.remove("__ctor__")
        jsclass.attributes = class_attributes
        # Might already be the contructor for the class
        if funcName not in jsclass.functions:
            parentScope = jsfunc.parent
            log.debug("Converting function: %r into a class contructor for: %r",
                      funcName, jsclass.name)
            jsclass.functions[funcName] = jsfunc
            # Update references
            if jsfunc.isAnonymous():
                parentScope.anonymous_functions.remove(jsfunc)
                parentScope.anonymous_functions.append(jsclass)
            else:
                parentScope.functions.pop(funcName)
                parentScope.classes[funcName] = jsclass
            # Fix starting line number
            if jsfunc.line < jsclass.line:
                jsclass.line = jsfunc.line
            # Copy over non-argument variables from the function to the class.
            # All the local variables are closures and should be in the class
            # scope.
            for varName, v in list(jsfunc.variables.items()):
                if not isinstance(v, JSArgument):
                    # Add to class and remove from the function
                    v.setParent(jsclass)
                    jsclass.variables[varName] = v
                    del jsfunc.variables[varName]
        parent = jsfunc.parent
        for var in jsfunc._parent_assigned_vars:
            log.debug("Converting function: Moved parent assigned variable %r "
                      "into the class instance", var.name)
            jsclass.members[var.name] = var
            parent.variables.pop(var.name, None)

        # Copy across all non-local members, bug 88549.
        for name, jsobject in list(jsfunc.variables.items()):
            if '__local__' not in jsobject.attributes and not isinstance(jsobject, JSArgument):
                jsclass.variables[name] = jsobject
                jsfunc.variables.pop(name, None)
        for name, jsobject in list(jsfunc.functions.items()):
            if '__local__' not in jsobject.attributes:
                jsclass.functions[name] = jsobject
                jsfunc.functions.pop(name, None)
        for name, jsobject in list(jsfunc.classes.items()):
            if '__local__' not in jsobject.attributes:
                jsclass.classes[name] = jsobject
                jsfunc.classes.pop(name, None)

        jsfunc._parent_assigned_vars = []
        jsfunc._class = jsclass
        jsfunc.setParent(jsclass)
        return jsclass

    def _convertFunctionToClass(self, jsfunc):
        """Convert the provided JSFunction into a JSClass and return it."""
        funcName = jsfunc.name
        log.debug("Creating class %r, from function %r", funcName, funcName)
        jsclass = JSClass(funcName, jsfunc.parent, jsfunc.line, self.depth - 1, jsfunc.doc)
        self._convertFunctionToClassContructor(jsfunc, jsclass)
        if self.currentScope == jsfunc:
            self.currentClass = jsclass
        # Copy across the classrefs from the jsdoc (if any).
        if jsfunc.jsdoc and jsfunc.jsdoc.baseclasses:
            for baseclass in jsfunc.jsdoc.baseclasses:
                jsclass.addClassRef(baseclass)
            jsfunc.jsdoc.baseclasses = []
        return jsclass

    def _convertFunctionToClosureVariable(self, jsfunc):
        funcName = jsfunc.name
        log.debug("Creating variable %r, from function closure %r", funcName, funcName)
        jsvariable = JSVariable(funcName, jsfunc.parent, jsfunc.line,
                                jsfunc.depth, jsfunc.type, jsfunc.doc, path=self.path)
        if jsfunc.returnTypes:
            jsro = jsfunc.returnTypes[0]
            #print jsro
            if isinstance(jsro, JSVariable):
                # Convert this object into the variable
                jsro.setParent(jsfunc.parent)
                jsro.line = jsfunc.line
                jsro.name = funcName
                jsvariable = jsro
        parent = jsfunc.parent
        if not jsfunc.isAnonymous():
            parent.functions.pop(funcName)
            parent.variables[funcName] = jsvariable
        return jsvariable

    def _findOrCreateScope(self, namelist, attrlist=("variables", ),
                           fromScope=None, isLocal=False):
        # Don't create a window scope - bug 87442.
        global_var = {
            "JavaScript": "window",
            "Node.js":    "global",
        }.get(self.lang)
        if namelist[0] == global_var:
            fromScope = self.cile
            namelist =  namelist[1:]
            if not namelist:
                return fromScope
        # Ensure the scope exists, else create it
        # Find the base scope first
        if fromScope is None:
            fromScope = self.currentScope
        log.debug("_findOrCreateScope: %r, attrlist: %r, from scope: %s",
                  namelist, attrlist, fromScope.name)
        name = namelist[0]

        # Determine where variables get added when they are not found
        if isLocal:
            applyToScope = fromScope
        else:
            applyToScope = self.cile   # Global file level

        resolvedScope, resolvedNamelist = self._resolveAlias(namelist, fromScope)
        if resolvedScope is not None and resolvedNamelist:
            # don't use the resolved namelist if it resolves to an undeclared
            # global object; doing so can cause us to shadow things found in the
            # standard library
            if resolvedScope is not self.cile or \
              self._findInScope(resolvedNamelist[0], attrlist, resolvedScope) is not None:
                fromScope, namelist = resolvedScope, resolvedNamelist

        isTheFirstName = True
        for name in namelist:
            # When looking for the first name of the scope, traverse the parent
            # chain, subsequent names *must* reside within the current scope
            # being checked!
            if isTheFirstName:
                isTheFirstName = False
                scope = self._locateScopeForName([name], attrlist, fromScope)
            else:
                scope = self._findInScope(name, attrlist, fromScope)
            if not scope:
                v = JSVariable(name, applyToScope, self.lineno, self.depth,
                               vartype="Object", path=self.path)
                scope = applyToScope.addVariable(name, value=v)
                log.debug("Could not find %r in scope: %r, creating variable (type=Object) for it on scope %r!!!",
                          name, fromScope.name, applyToScope.name)
                scope.attributes.append("__file_local__")
            fromScope = scope
            applyToScope = scope
        return fromScope

    def addVariable(self, namelist, typeNames, toScope=None, doc=None,
                    assignAsCurrentScope=False, isLocal=False, value=None,
                    varCtor=JSVariable):
        varName = namelist[-1]
        if toScope is None:
            toScope = self.currentScope
        log.debug("addVariable: %r, typeNames:%r, isLocal: %r, scope: %r",
                  namelist, typeNames, isLocal, toScope.name)

        if len(namelist) > 1:
            if namelist[-2] == "prototype":
                # Adding to an existing class then
                toScope = self._locateScopeForName(namelist[:-2], attrlist=("classes", ))
                if not toScope:
                    # Create a class for it then
                    log.debug("Creating class now: %r", namelist[:-2])
                    toScope = self.addClass(namelist[:-2], doc=doc, path=self.path,
                                            varCtor=varCtor)
                    toScope.metadata = {"used_prototype": True}
                    #raise CodeIntelError("Could not find scope for: %r" % (namelist[:-2], ))
                if varName == "constructor":
                    if isinstance(typeNames, JSObject):
                        ctorName = typeNames.type
                    else:
                        ctorName = ".".join(typeNames)
                    func = self._locateScopeForName([ctorName], attrlist=("functions", ))
                    if func:
                        return self._convertFunctionToClassContructor(func, toScope)
                    else:
                        return self._addClassPart(ctorName, self.ADD_CLASS_CONSTRUCTOR,
                                                  namelist[:-2], doc=doc, path=self.path,
                                                  varCtor=varCtor)
                else:
                    return self._addClassPart(varName, self.ADD_CLASS_VARIABLE,
                                              namelist[:-2], doc=doc, path=self.path,
                                              varCtor=varCtor)
            else:
                # Find or create the parent scope
                toScope = self._findOrCreateScope(namelist[:-1],
                                                  ('variables', 'classes',
                                                   'functions'),
                                                  fromScope=toScope,
                                                  isLocal=isLocal)
        elif not isLocal:
            # Try and find the scope we are assigning to, should be in
            # a parent scope somewhere!
            #print("addVariable: namelist:%r, typeNames:%r, isLocal: %r, line: %d" % (namelist, typeNames, isLocal, self.lineno))
            fromscope = toScope
            toScope = self._locateScopeForName(namelist,
                                               ('variables', 'classes',
                                                'functions'),
                                               toScope)
            if toScope is None:
                #if self.text[0] not in ("var", "const"):
                #    sys.stderr.write("Undeclared var in %s:%d, %r in %s %r\n" % (
                #            self.cile.name,
                #            self.lineno, varName, fromscope.cixname, fromscope.name))
                # Place it at the global level then
                toScope = self.cile
            else:
                toScope = toScope.parent

        # Add it to scope if it's not already in there
        if toScope:
            if isinstance(toScope, JSVariable):
                # toScope.type can be None if it's an implicitly defined scope
                # (i.e. ones we picked up by observing properties being set on it)
                if toScope.type is not None and toScope.type.lower() not in ("object", ):
                    # Not going to add sub-variables, as it's likely a class
                    # object already, which has this variable information set
                    #if not toScope.type:
                    #    msg = "Assignment to a unknown type, %s:%d, %r (%s)" % (self.filename, self.lineno, ".".join(namelist), toScope.type)
                    #    print >> sys.stderr, msg
                    return None
            #if not isLocal and varName not in toScope.variables:
            #    print("addVariable: namelist:%r, typeNames:%r, isLocal: %r, line: %d" % (namelist, typeNames, isLocal, self.lineno))
            if value is None:
                value = varCtor(varName, toScope, self.lineno, self.depth,
                                vartype=typeNames, doc=doc, isLocal=isLocal, path=self.path)
            v = toScope.addVariable(varName, value=value, metadata=self._metadata)
            if assignAsCurrentScope:
                self.currentScope = v
            return v

        # We kinda lost the scope somewhere
        return None

    def addObjectVariable(self, namelist, toScope=None, doc=None,
                          isLocal=False):
        if not toScope:
            toScope = self.currentScope
        log.debug("addObjectVariable: namelist:%r, scope:%r", namelist,
                  toScope.name)
        varName = namelist[-1]

        if len(namelist) > 1:
            # Ensure the scope exists, else create it
            if "prototype" in namelist:
                classnames = namelist[:namelist.index("prototype")]
                toScope = self.addClass(classnames, doc=self.comment)
                toScope.metadata = {"used_prototype": True}
            else:
                toScope = self._findOrCreateScope(namelist[:-1], ("variables", "classes", "functions"), toScope)
            # Assignment to a function, outside the function scope... create a class for it
            if isinstance(toScope, JSFunction):
                toScope = self._convertFunctionToClass(toScope)
        # Add it to scope if it's not already in there
        v = JSVariable(varName, toScope, self.lineno, self.depth,
                       vartype=["Object"], doc=doc, isLocal=isLocal, path=self.path)
        v = toScope.addVariable(varName, value=v)
        self.currentScope = v

    def addReturnObject(self, doc=None):
        log.debug("addReturnObject: scope:%r", self.currentScope.name)
        jsro = JSVariable("", self.currentScope, self.lineno, self.depth, vartype="Object", doc=doc, path=self.path)
        if isinstance(self.currentScope, JSFunction):
            self.currentScope.addReturnType(jsro)
        # else:
        #   TODO: This is ignoring the return type for function getters.
        self.currentScope = jsro
        return jsro

    def addFunctionReturnType(self, typeNames, doc=None):
        if isinstance(self.currentScope, JSFunction):
            log.debug("addFunctionReturnType: type: %r, scope:%r", typeNames, self.currentScope.name)
            self.currentScope.addReturnType(".".join(typeNames))

    ##
    # Read everything up to and including the matching close paren
    # @param styles list
    # @param text list
    # @param p int position in the styles and text list
    # @param paren string type of parenthesis
    def _getParenArguments(self, styles, text, p, paren=None):
        # Examples:
        #  (arg1, arg2) {
        #   => [(arg1, arg2)]
        #  [row][this.columns[column][0]];
        #   => [row]

        if paren is None:
            paren = text[p]
        parenMatches = { '{': '}', '[': ']', '(': ')' }
        args = []
        oppParen = parenMatches.get(paren)
        if oppParen is None:
            log.debug("_getParenArguments:: No matching paren for: %r, " \
                      "ignoring arguments.", paren)
            return args, p
        parenCount = 0
        while p < len(styles):
            args.append(text[p])
            if styles[p] == self.JS_OPERATOR:
                if text[p] == paren:
                    parenCount += 1
                elif text[p] == oppParen:
                    parenCount -= 1
                    if parenCount <= 0:
                        p += 1
                        break
            p += 1
        return args, p

    def _skipOverParenArguments(self, styles, text, p, paren="("):
        args, p = self._getParenArguments(styles, text, p, paren)
        return p

    # Skip over all the variable assignment details. Returns position at
    # the end of the assignment, usually a "," or a ";" character.
    #
    # Examples:
    #    var nGroupIndex = typeof <|>p_nGroupIndex=="number" ?p_nGroupIndex :0,
    #        aGroup = this._getItemGroup(nGroupIndex);
    # should skip to "aGroup = this._getItemGroup(nGroupIndex);"
    def _skipToEndOfVariableAssignment(self, styles, text, p):
        old_p = p
        while p < len(styles):
            style = styles[p]
            if style == self.JS_OPERATOR:
                t = text[p]
                if t in '([{':
                    p = self._skipOverParenArguments(styles, text, p, t)
                    continue
                elif t in ',;':
                    break
            p += 1
        if old_p == p:
            # Ensure we at least move somewhere (avoid recursion)
            p += 1
        log.debug("_skipToEndOfVariableAssignment:: skipped text %r, p: %d",
                  text[old_p:p], p)
        return p

    def _getArgumentsFromPos(self, styles, text, pos):
        log.debug("_getArgumentsFromPos: text: %r", text[pos:])
        if pos < len(styles) and styles[pos] == self.JS_OPERATOR and text[pos] == "(":
            ids = []
            pos += 1
            start_pos = pos
            while pos < len(styles):
                if styles[pos] == self.JS_IDENTIFIER:
                    ids.append(text[pos])
                    if text[pos - 1] == '.':
                        ids[-1] = '...' + ids[-1] # ES6 rest operator (...arg)
                elif styles[pos] == self.JS_OPERATOR and text[pos] == '=':
                    # Skip over default argument value.
                    # TODO: complicated default values that include commas will
                    # not be parsed correctly.
                    while pos < len(styles) and not \
                          (styles[pos] == self.JS_OPERATOR and \
                           (text[pos] == ',' or text[pos] == ')')):
                        pos += 1
                    continue
                elif styles[pos] == self.JS_OPERATOR and text[pos] == '.' and \
                     pos + 2 <= len(styles) and text[pos + 1] == '.' and \
                     text[pos + 2] == '.':
                    pos += 2 # handle ES6 rest operator (...arg)
                elif styles[pos] != self.JS_OPERATOR or text[pos] != ",":
                    break
                pos += 1
            return ids, pos
        return None, pos

    def _getIdentifiersFromPos(self, styles, text, pos):
        log.debug("_getIdentifiersFromPos: text: %r", text[pos:])
        start_pos = pos
        ids = []
        last_style = self.JS_OPERATOR
        while pos < len(styles):
            style = styles[pos]
            if style in (self.JS_IDENTIFIER, self.JS_WORD):
                if last_style != self.JS_OPERATOR:
                    break
                ids.append(text[pos])
            elif style == self.JS_OPERATOR and text[pos] == "[" and \
                len(styles) - pos > 2 and styles[pos + 1] in self.JS_STRINGS and \
                text[pos + 2] == "]":
                # blah["string here"]
                part = text[pos + 1]
                if len(part) > 1 and part[0] in ("'", '"') and part[-1] == part[0]:
                    part = part[1:-1] # trim quotes
                ids.append(part)
                pos += 2
            elif style != self.JS_OPERATOR or text[pos] != "." or \
                last_style != self.JS_IDENTIFIER:
                break
            pos += 1
            last_style = style
        return ids, pos

    ##
    # Grab all necessary citdl information from the given text
    # @param styles list
    # @param text list
    # @param p int position in the styles and text list
    # @return the citdl list and the position after the last item swallowed
    def _getCitdlTypeInfo(self, styles, text, p):
        log.debug("_getCitdlTypeInfo:: text: %r", text[p:])
        citdl = []
        last_style = self.JS_OPERATOR
        while p < len(styles):
            style = styles[p]
            #log.debug("p: %d, text[p]: %r", p, text[p])
            #print "style: %d, last_style: %d" % (style, last_style)
            if style in (self.JS_IDENTIFIER, self.JS_WORD):
                if last_style != self.JS_OPERATOR:
                    break
                citdl.append(text[p])
                style = self.JS_IDENTIFIER
            elif style == self.JS_OPERATOR and last_style == self.JS_IDENTIFIER:
                if text[p] == ".":
                    pass
                elif text[p] == ")":
                    # Collect after the closing brace - bug 80581.
                    style = last_style
                elif text[p] == "(":
                    paren_pos = p
                    if citdl == ['require']:
                        # Deal with CommonJS (NodeJS) require statements.
                        args, p = self._getParenArguments(styles, text, p)
                        if len(args) >= 3 and styles[paren_pos+1] in self.JS_STRINGS:
                            self._metadata['required_library_name'] = self._unquoteJsString(args[1])
                            log.debug("Dealing with CommonJS require(%s)",
                                      self._metadata['required_library_name'])
                    else:
                        p = self._skipOverParenArguments(styles, text, p)
                    if citdl:
                        if len(citdl) > 1 and 'QueryInterface' == citdl[-1]:
                            # QueryInterface is specific to xpcom interfaces.
                            citdl = []
                            # Don't want the "." items in the citdl
                            for t in text[paren_pos+1:p-1]:
                                if t != ".":
                                    citdl.append(t)
                        else:
                            citdl[-1] = citdl[-1] + "()"
                    style = self.JS_IDENTIFIER
                    p -= 1   # Are at the pos after the paren, move back to it
                elif text[p] == "[":
                    # Arrays, just read in the arguments and add it to the citdl
                    args, p = self._getParenArguments(styles, text, p, "[")
                    if args and citdl:
                        # Check if this is an xpcom component.
                        if citdl in  (["CC"], ["Cc"],
                                      ["Components", "classes"]) and \
                           (p+2) < len(styles) and \
                           text[p] == "." and \
                           text[p+1] in ("getService", "createInstance") and \
                           text[p+2] == "(":
                            # Add the xpcom interface information.
                            # TODO: Change this once array completions are
                            #       supported
                            citdl, p = self._getArgumentsFromPos(styles, text,
                                                                 p+2)
                        else:
                            citdl[-1] = citdl[-1] + "".join(args)
                    style = self.JS_IDENTIFIER
                    p -= 1  # We are are after the last "]", move back
                else:
                    break
            else:
                break
            p += 1
            last_style = style
        return citdl, p

    def _getVariableType(self, styles, text, p, assignmentChar="="):
        """
        Get the type of the variable
        @param styles {list} The (scintilla) styles for the text
        @param text {list} The tokens to examine
        @param p {int} Offset into text/styles to start looking
        @param assignmentChar {str} The assignment character used
        @returns {tuple} {list} type names,
                         {int} new offset (p),
                         {bool} true if this is a new instance
        """
        log.debug("_getVariableType: text: %r, assign char: %s", text[p:],
                  assignmentChar)
        typeNames = []
        if p >= len(styles):
            # Nothing left to examine
            return typeNames, p, False

        if assignmentChar and styles[p] == self.JS_OPERATOR and \
           text[p] == assignmentChar:
            # Assignment to the variable
            p += 1
        if p < len(styles) and styles[p] == self.JS_OPERATOR and \
           text[p] in "+-":
            # Example: var x = -1;
            # Skip over + and -, commonly used with strings and integers
            p += 1

        isNew = False
        isAlias = False

        if p < len(styles):
            if styles[p] == self.JS_WORD:
                # Keyword
                keyword = text[p]
                if keyword == "new":
                    typeNames, p = self._getCitdlTypeInfo(styles, text, p+1)
                    p -= 1   # We are already at the next position, step back
                    # When resolving a class, we want to drop the function call
                    # from the citdl and just use the identifier itself.
                    for i, t in enumerate(typeNames):
                        if t.endswith("()"):
                            typeNames[i] = t[:-2]
                            break
                    #if not typeNames:
                    #    typeNames = ["object"]
                    isNew = True
                elif keyword in ("true", "false"):
                    typeNames = ["boolean"]
                elif keyword == "this":
                    typeNames, p = self._getCitdlTypeInfo(styles, text, p)
                    p -= 1   # We are already at the next position, step back
                # Don't record null, as it doesn't help us with anything
                #elif keyword == "null":
                #    typeNames = ["null"]
                p += 1
            elif styles[p] in self.JS_STRINGS:
                typeNames = ["string"]
                p += 1
            elif styles[p] == self.JS_NUMBER:
                typeNames = ["int"]
                p += 1
            elif styles[p] == self.JS_IDENTIFIER:
                typeNames, p = self._getCitdlTypeInfo(styles, text, p)
                if not isNew:
                    # this refers to an identifier without "new", it's an alias
                    isAlias = True
            elif styles[p] == self.JS_OPERATOR:
                if text[p] == "{":
                    # This is actually a newly created object
                    typeNames = ["Object"]
                    p += 1
                elif text[p] == "[":
                    while p+1 < len(styles):
                        if text[p] == "]" and styles[p] == self.JS_OPERATOR:
                            break
                        p += 1
                    typeNames = ["Array"]
                    p += 1
        return typeNames, p, isAlias

    def _unquoteJsString(self, s):
        """Return the string without quotes around it"""
        return Utils.unquoteJsString(s)

    def _getVariableDetail(self, namelist, styles, text, p, assignmentChar="="):
        # this.myname = "123";
        # myclass.prototype.list = function () {
        # this.myname = new function(x, y) {
        # var num = mf.field1;
        # names = { "myname": 1, "yourname": 2 }

        log.debug("_getVariableDetail: namelist: %r, text:%r", namelist, text[p:])

        if len(namelist) > 1 and "prototype" in namelist:
            # Check for special class prototypes
            protoName = namelist[-1]
            if protoName == "prototype":
                typeNames, p, isAlias = self._getVariableType(styles, text, p, assignmentChar)
                return (TYPE_PARENT, typeNames, None, p)
            elif namelist[-2] == "prototype":
                typeNames = []
                if p+1 < len(styles) and styles[p+1] in self.JS_STRINGS:
                    typeNames = [self._unquoteJsString(text[p+1])]
                if protoName == "__defineGetter__":
                    return (TYPE_GETTER, typeNames, None, p)
                elif protoName == "__defineSetter__":
                    return (TYPE_SETTER, typeNames, None, p)
        elif len(namelist) == 1 and p+1 < len(styles) and \
             styles[p] == self.JS_IDENTIFIER:
            keyword = namelist[0]
            if keyword == "get":
                # get log() {
                newnamelist, p = self._getIdentifiersFromPos(styles, text, p)
                namelist.pop()
                for name in newnamelist:
                    namelist.append(name)
                log.debug("Found getter:%r", namelist)
                return (TYPE_GETTER, [], None, p)
            elif keyword == "set":
                # set application(value) {
                newnamelist, p = self._getIdentifiersFromPos(styles, text, p)
                namelist.pop()
                for name in newnamelist:
                    namelist.append(name)
                log.debug("Found setter:%r", namelist)
                return (TYPE_SETTER, [], None, p)

        if p+1 < len(styles) and styles[p+1] == self.JS_OPERATOR and text[p+1] == "{":
            # This is actually a newly created object
            return (TYPE_OBJECT, [], None, p+2)

        if p+2 < len(styles) and styles[p+1] == self.JS_WORD and \
             text[p+1] == "function":
            # Skip over any function name
            # Example:  var f = function my_f(a, b) { }
            p += 2
            while p < len(styles):
                if text[p] == "(":
                    break
                p += 1
            args, p = self._getArgumentsFromPos(styles, text, p)
            return (TYPE_FUNCTION, [], args, p)

        if p+3 < len(styles) and styles[p+1] == self.JS_WORD and \
             text[p+1] == "new" and text[p+2] == "function":
            # Skip over any function name
            # Example:  var f = new function my_f(a, b) { }
            p += 3
            while p < len(styles):
                if text[p] == "(":
                    break
                p += 1
            args, p = self._getArgumentsFromPos(styles, text, p)
            return (TYPE_FUNCTION, [], args, p)

        if "=>" in text[p:]:
            # Check for ES6 fat arrow function
            # Example: var f = (arg1, arg2) => { body }
            #          var g = arg_only => expr

            arrow_index = text.index("=>")
            maybe_args = text[p + 1:arrow_index]
            if maybe_args and len(maybe_args) > 0 and maybe_args[0] == "(" and maybe_args[-1] == ")":
                # Maybe have args...
                args, new_p = self._getArgumentsFromPos(styles, text, p + 1)
                if args is not None:
                    return (TYPE_FUNCTION, [], args, arrow_index)
            elif arrow_index == p + 2 and styles[p+1] == self.JS_IDENTIFIER:
                # single argument
                return (TYPE_FUNCTION, [], [text[p+1]], arrow_index)

        typeNames, p, isAlias = self._getVariableType(styles, text, p, assignmentChar)
        if len(namelist) > 2 and namelist[-2:] == ["prototype", "constructor"]:
            # Foo.prototype.constructor = bar; don't treat as an alias
            pass
        elif isAlias and typeNames != namelist:
            log.debug("_getVariableDetail: %r is an alias to %r",
                      namelist, typeNames)
            return (TYPE_ALIAS, typeNames, None, p)
        return (TYPE_VARIABLE, typeNames, None, p)

    def _variableHandler(self, lineno, styles, text, p, namelist,
                         allowedAssignmentChars="=",
                         isLocal=False):
        log.debug("_variableHandler:: namelist:%r, p:%d, isLocal: %r",
                  namelist, p, isLocal)
        #print "p:", p
        #print "text:", text[p:]

        # The while loop is used to handle multiple variable assignments.
        # Example1:
        #   var x = 1, y = 2, z = 3;
        #     namelist: ['x']
        #     text:     ['=', '1', ',', 'y', '=', '2', ',', 'z', '=', '3', ';']
        #
        # Example2:
        #   var x = y = z = 1;
        #     namelist: ['x']
        #     text:     ['=', 'y', '=', 'z', '=', '1', ';']
        #
        # Example3:
        #   var x, y, z = 1;
        #     namelist: ['x']
        #     text:     [',', 'y', ',', 'z', '=', '1', ';']
        #
        # Example4:
        #  this.ab['xyz']={one:1,"two":2};
        #    namelist: ['this', 'ab']
        #    text:     ['[', "'xyz'", ']', '=', '{']]
        #
        already_looped = False
        while p < len(styles):
            self._metadata = {}
            #log.debug("_variableHandler:: p: %d, text: %r", p, text[p:])
            if already_looped:
                # We've already done one loop, need to get a new namelist
                #     text:     [',', 'y', '=', '2', ',', 'z', '=', '3']
                #log.debug("_variableHandler:: already_looped:: text:%r, p:%d", text[p:], p)
                if text[p] == "=" and styles[p] == self.JS_OPERATOR and len(typeNames) > 0:
                    # Assignment to an assignment (aka Example 2)
                    namelist = typeNames
                elif text[p] != "," or styles[p] != self.JS_OPERATOR:
                    p = self._skipToEndOfVariableAssignment(styles, text, p)
                    if p < len(styles) and text[p] == ";":
                        p += 1
                    continue
                else:
                    # Multiple assignment (aka Example 1)
                    namelist, p = self._getIdentifiersFromPos(styles, text, p+1)

                # The namelist may contain array assignments that we cannot
                # deal with, check to ensure we have a variable assignment that
                # we can deal with.
                #   Handled array assignment examples:
                #     this['field1'] =
                #   Unhandled array assignment examples:
                #     this[0] =
                #     myvar[unknownvar] =
                #
                new_namelist = []
                for name in namelist:
                    if '[' in name:
                        name_parts = name.split('[')
                        for subname in name_parts:
                            if subname.endswith("]"):
                                subname = subname[:-1]
                                # ensure the array reference is a string, otherwise
                                # we cannot do anything with it.
                                if not subname or subname[0] not in "'\"":
                                    log.debug("_variableHandler:: Ignoring non-string indexed array assignment: %r", namelist)
                                    return
                                subname = self._unquoteJsString(subname)
                            new_namelist.append(subname)
                    else:
                        new_namelist.append(name)
                namelist = new_namelist
                log.debug("_variableHandler:: already_looped:: namelist now:%r, p:%d", namelist, p)

            if len(namelist) < 1:
                log.debug("_variableHandler:: Invalid namelist! Text: %r", text)
                return

            # Whether the namelist includes an array piece whose name/scope
            # could not be determined (i.e. foo[myname] = 1), as myname is
            # an unknown.
            unknown_array_namelist = False

            if p >= len(styles) or text[p] in ",;":
                # It's a uninitialized variable?
                already_known = False
                if len(namelist) == 1:
                    for varType in ("variables", "classes", "functions", "members"):
                        if namelist[0] in getattr(self.currentScope, varType, {}):
                            # it's a variable we already know about, don't shadow it
                            log.debug("uninitialized variable %r is already known, skipping",
                                      namelist)
                            already_known = True
                            break
                if not already_known:
                    log.debug("Adding uninitialized variable: %r, line: %d",
                              namelist, lineno)
                    self.addVariable(namelist, [],
                                     doc=self.comment,
                                     isLocal=isLocal)
                already_looped = True
                continue

            if p+1 < len(styles) and text[p] == '[' and \
                 styles[p] == self.JS_OPERATOR:
                # It may be an  array assignment (Example4 above).
                unknown_array_namelist = True
                array_args, p = self._getParenArguments(styles, text, p, '[')
                if len(array_args) == 3 and array_args[1][0] in "'\"":
                    unknown_array_namelist = False
                    namelist.append(self._unquoteJsString(array_args[1]))
                    log.debug("Namelist includes an array scope, now: %r",
                              namelist)
                if p >= len(styles):
                    already_looped = True
                    continue

            typeNames = []
            name_prefix = namelist[0]
            assignChar = text[p]
            try_getter_setter = False

            addToClass = False
            assignToCurrentScope = False
            if assignChar == ":" or name_prefix == "this":
                assignToCurrentScope = True
                if name_prefix == "this" or \
                   isinstance(self.currentScope, JSClass):
                    addToClass = True

            if p+1 < len(styles) and len(namelist) == 1 and \
               name_prefix in ("get", "set") and styles[p] == self.JS_IDENTIFIER:
                log.debug("First element in namelist is a getter/setter")
                try_getter_setter = True

            if p+1 < len(styles) and (try_getter_setter or
                                        (styles[p] == self.JS_OPERATOR and
                                         assignChar in allowedAssignmentChars)):
                if name_prefix == "this":
                    namelist = namelist[1:]
                    if len(namelist) < 1:
                        log.debug("_variableHandler:: No namelist for 'this'! Text: %r", text)
                        return
                    name_prefix = namelist[0]
                elif name_prefix[0] in "'\"":
                    # String assignment  { "myfield" : 123, ....
                    name_prefix = self._unquoteJsString(name_prefix)
                    namelist = [name_prefix]
                    # Treat it like a variable/object assignment
                    assignToCurrentScope = True

                # Assignment to the scope
                #print "text[p:]", text[p:]
                varType, typeNames, args, p = self._getVariableDetail(namelist, styles, text, p, assignmentChar=assignChar)
                if varType == TYPE_ALIAS:
                    if len(typeNames) < 1 or len(typeNames) == 1 and typeNames[0] in known_javascript_types:
                        # "alias" to a primitive
                        varType = TYPE_VARIABLE
                varType_mapping = dict([(v, k) for k,v in list(globals().items()) if k.startswith("TYPE_")])
                log.debug("_variableHandler:: varType:%r, typeNames:%r, args:%r, p: %d", varType_mapping.get(varType, varType), typeNames, args, p)
                if varType == TYPE_FUNCTION:
                    if addToClass:
                        log.debug("_variableHandler:: Line %d, class function: %r(%r)",
                                  lineno, namelist, args)
                        self.addClassFunction(namelist, args, doc=self.comment)
                    else:
                        log.debug("_variableHandler:: Line %d, function: %r(%r)",
                                  lineno, namelist, args)
                        self.addFunction(namelist, args, doc=self.comment,
                                         isLocal=(not assignToCurrentScope))
                elif varType == TYPE_VARIABLE or varType == TYPE_ALIAS:
                    if varType == TYPE_VARIABLE:
                        varCtor = JSVariable
                        typeString = "type"
                    else: # TYPE_ALIAS
                        varCtor = lambda *args, **kwargs: \
                            JSAlias(target=typeNames, scope=self.currentScope,
                                    *args, **kwargs)
                        typeString = "alias"

                    if assignToCurrentScope:
                        log.debug("_variableHandler:: Line %d, class member variable: %r (%s=%r)",
                                  lineno, namelist, typeString, typeNames)
                        self.addClassOrVariableMember(namelist, typeNames,
                                                      doc=self.comment,
                                                      isLocal=isLocal,
                                                      varCtor=varCtor)
                    else:
                        if len(namelist) > 1:
                            log.debug("_variableHandler:: Line %d, scoped assignment: %r, %s=%r scope %r",
                                      lineno, namelist, typeString, typeNames, self.currentScope.name)
                        else:
                            log.debug("_variableHandler:: Line %d, local variable assignment: %r, %s=%r scope %r",
                                      lineno, namelist, typeString, typeNames, self.currentScope.name)
                        # XXX - Check this, do we need this hack?
                        if typeNames == ["Object"] and text[-1] == "{":
                            # Turn it into a class
                            log.debug("_variableHandler:: Turning Object into class: %r", namelist)
                            #self.addVariable(namelist, typeNames)
                            self.addClass(namelist, doc=self.comment, path=self.path, varCtor=varCtor)
                        else:
                            self.addVariable(namelist, typeNames,
                                             doc=self.comment,
                                             isLocal=isLocal,
                                             varCtor=varCtor)
                    # We ignore any defined functions, as we gain no value from them
                elif varType == TYPE_PARENT:
                    if len(typeNames) > 0:
                        self.addClassParent(namelist, typeNames)
                    else:
                        self.addAnonymousClass(namelist, doc=self.comment)
                elif varType == TYPE_GETTER:
                    log.debug("_variableHandler:: Found getter:%r", namelist)
                    self.addGetter(namelist, [], doc=self.comment)
                elif varType == TYPE_SETTER:
                    log.debug("_variableHandler:: Found setter:%r", namelist)
                    self.addSetter(namelist, doc=self.comment)
                elif varType == TYPE_OBJECT:
                    # var obj = { observer: function() { ... }, count: 10 }
                    if not typeNames:
                        typeNames = ["Object"]
                    if unknown_array_namelist:
                        # Create a dummy object that will then accept any
                        # subsequent scope assignments. This object will *not*
                        # be included in the cix output.
                        log.debug("_variableHandler:: Line: %d, unknown array "
                                  "assingment, creating a dummy variable: %r",
                                  lineno, namelist)
                        self.currentScope = JSObject(None, self.currentScope,
                                                     self.lineno, self.depth,
                                                     "Object", path=self.path)
                    elif assignToCurrentScope:
                        log.debug("_variableHandler:: Line %d, class object variable: %r", lineno,
                                  namelist)
                        self.addClassOrVariableMember(namelist, typeNames,
                                                      doc=self.comment,
                                                      assignAsCurrentScope=True)
                    else:
                        log.debug("_variableHandler:: Line %d, object variable: %r", lineno,
                                  namelist)
                        self.addObjectVariable(namelist, doc=self.comment,
                                               isLocal=isLocal)
                else:
                    log.debug("_variableHandler:: Ignoring. Unhandled assignment type: %r",
                              text)
                    return
            else:
                log.debug("_variableHandler:: Line %d, calling scoped variable: %r",
                          lineno, namelist)
                for pos, obj in self.objectArguments:
                    if not isinstance(obj, JSFunction):
                        continue
                    # we have a function, tell it about the caller
                    obj.addCaller(caller=namelist, pos=pos, line=lineno)
            already_looped = True

    def createObjectArgument(self, styles, text):
        log.debug("createObjectArgument")
        #obj = toScope.addVariable(varName, self.lineno, self.depth, "Object", doc=doc)
        obj = JSObject(None, None, self.lineno, self.depth, "Object", path=self.path)
        return obj

    def _addCodePiece(self, styles, text, pos=0):
        if pos >= len(styles):
            return
        lineno = self.lineno

        log.debug("*** Line: %d ********************************", lineno)
        #log.debug("Styles: %r", self.styles)
        log.debug("Text: %r", self.text[pos:])
        log.debug("currentScope: %s %r", self.currentScope.cixname,
                  self.currentScope.name)
        if self.currentClass:
            log.debug("currentClass: %r", self.currentClass.name)
        if self.in_variable_definition:
            log.debug("in_variable_definition: %r", self.in_variable_definition)
        #print "%d: %r" % (lineno, " ".join(self.text[pos:]))
        #log.debug("Comment: %r", self.comment)
        #log.debug("")

        firstStyle = styles[pos]
        if firstStyle == self.JS_WORD:
            # Keyword
            keyword = text[pos]
            if keyword == "function":
                isLocal = not isinstance(self.currentScope, JSFile)
                namelist, p = self._getIdentifiersFromPos(styles, text, pos+1)
                if namelist:
                    args, p = self._getArgumentsFromPos(styles, text, p)
                    log.debug("Line %d, function: %r(%r)",
                              lineno, namelist, args)
                    self.addFunction(namelist, args, doc=self.comment,
                                     isLocal=isLocal)
                else:
                    # We shall add the function, but without a name as it does
                    # not really have one... it's anonymous.
                    args, p = self._getArgumentsFromPos(styles, text, p)
                    self.addAnonymousFunction(args, doc=self.comment)
            elif keyword == "this":
                # Member variable of current object
                p = pos+1
                if p < len(styles) and styles[p] == self.JS_OPERATOR and \
                   text[p] == ".":
                    namelist, p = self._getIdentifiersFromPos(styles, text, p+1)
                    self._variableHandler(lineno, styles, text, p, ["this"] + namelist)
            elif keyword in ("let", "var", "const"):
                # Variable of current scope
                self.in_variable_definition = True
                namelist, p = self._getIdentifiersFromPos(styles, text, pos+1)
                # if in the global/file scope the variable is global also,
                # if the scope is something else, add as a local variable
                if namelist:
                    isLocal = not isinstance(self.currentScope, JSFile)
                    if p < len(styles):
                        self._variableHandler(lineno, styles, text, p, namelist,
                                              isLocal=isLocal)
                    else:
                        log.debug("Adding uninitialized variable: %r, line: %d",
                                  namelist, lineno)
                        self.addVariable(namelist, [],
                                         doc=self.comment,
                                         isLocal=isLocal)
            elif keyword == "return":
                p = pos+1
                if p < len(styles) and styles[p] == self.JS_OPERATOR and \
                   text[p] == "{":
                    # Returning a new object
                    self.addReturnObject(doc=self.comment)
                    ## XXX - Fixme to allow variables with sub-elements
                    #log.debug("Ignoring scope due to return of object")
                    #newstate = S_IGNORE_SCOPE
                else:
                    # Return types are only valid in functions
                    if isinstance(self.currentScope, JSFunction):
                        typeNames, p, isAlias = self._getVariableType(styles, text, pos+1, assignmentChar=None)
                        #varType, typeNames, args, p = self._getVariableDetail([], styles, text, pos, assignmentChar="return")
                        log.debug("Return type: %r", typeNames)
                        self.addFunctionReturnType(typeNames)
            elif keyword == "if":
                # if (....) xyz
                p = self._skipOverParenArguments(styles, text, pos+1)
                self._addCodePiece(styles, text, p)
            elif keyword == "else":
                pos += 1
                # Check for: 'else if (....) xyz'
                if pos < len(styles) and styles[pos] == self.JS_WORD and \
                   text[pos] == "if":
                    pos = self._skipOverParenArguments(styles, text, pos+1)
                self._addCodePiece(styles, text, pos)
            else:
                log.debug("_addCodePiece: Unhandled keyword:%r", keyword)
        elif firstStyle == self.JS_IDENTIFIER:
            isLocal = False
            if self.in_variable_definition:
                if self.currentScope != self.cile and \
                   ((pos > 0 and text[pos-1] == ",") or
                    (self.lastText and self.lastText[-1] == ",")):
                    isLocal = True
                else:
                    self.in_variable_definition = False
            # Defining scope for action
            namelist, p = self._getIdentifiersFromPos(styles, text, pos)
            self._variableHandler(lineno, styles, text, p, namelist,
                                  allowedAssignmentChars=":=",
                                  isLocal=isLocal)
        elif firstStyle == self.JS_OPERATOR:
            if self.lastText and self.lastText[-1] == "{" and \
               text[:2] == ['(', ')'] and isinstance(self.lastScope, JSFunction):
                # It's a closure
                log.debug("Found a closure: function: %r", self.lastScope.name)
                self._convertFunctionToClosureVariable(self.lastScope)
            else:
                # We don't do anything here
                log.debug("Ignoring when starting with an operator")
        elif firstStyle in self.JS_STRINGS:
            # Check for object string names, see below:
            #   "element1": [ 1, "one" ],
            #   "field1": "name",
            #print "String assignment: %r" % (text[pos], )
            #print "Text: %r" % (text, )
            if pos+1 < len(styles) and \
               styles[pos+1] == self.JS_OPERATOR and text[pos+1] == ":":
                self._variableHandler(lineno, styles, text, pos+1,
                                      text[pos:pos+1],
                                      allowedAssignmentChars=":",
                                      isLocal=False)
        else:
            log.debug("Unhandled first style:%d", firstStyle)

        self._resetState()
        #if log.level == logging.DEBUG:
        #    print
        #    print '\n'.join(self.cile.outline())
        #    print

    def _chooseBestVariable(self, jsvar1, jsvar2):
        # 1. Choose the one with a jsdoc.
        if jsvar1.jsdoc and not jsvar2.jsdoc:
            return jsvar1
        if jsvar2.jsdoc and not jsvar1.jsdoc:
            return jsvar2
        # 2. Choose the one with the a citdl.
        if jsvar1.type and not jsvar2.type:
            return jsvar1
        if jsvar2.type and not jsvar1.type:
            return jsvar2
        # 3. Choose the one with the best citdl. We prefer the one
        #    that is not a standard type, because standard types
        #    can be null or boring :D
        citdl1 = standardizeJSType(jsvar1.type)
        citdl2 = standardizeJSType(jsvar2.type)
        if citdl1 in known_javascript_types and \
           not citdl2 in known_javascript_types:
            return jsvar2
        if citdl2 in known_javascript_types and \
           not citdl1 in known_javascript_types:
            return jsvar1
        # 4. Default to the first one given.
        return jsvar1

    def _copyObjectToAnother(self, jsobject, jsother):
        #print
        #print "Full outline:"
        #print '\n'.join(self.cile.outline())
        #print
        #print "jsobject:"
        #print '\n'.join(jsobject.outline())
        #print
        #print "jsother:"
        #print '\n'.join(jsother.outline())

        appliedToGlobalScope = False
        if jsother == self.cile:
            appliedToGlobalScope = True

        for fieldname in ('classes', 'members', 'variables', 'functions', ):
            d_obj = getattr(jsobject, fieldname, {})
            d_oth = getattr(jsother, fieldname, {})
            for name, jsobj in list(d_obj.items()):
                # Check the parents are not the same.
                if jsobj.parent == jsother:
                    parent = jsobj.parent
                    log.warn("%s %r has parent %s %r, file: %s#%d",
                             parent.cixname, parent.name, jsother.cixname,
                             jsother.name, self.cile.name, self.lineno)
                jsobj.setParent(jsother)
                if appliedToGlobalScope:
                    # Remove the __local__ and private attributes
                    if "__local__" in jsobj.attributes:
                        jsobj.attributes.remove("__local__")
                    if "private" in jsobj.attributes:
                        jsobj.attributes.remove("private")
                if fieldname == 'functions' and jsobj._class:
                    jsobj._class = jsobj.parent
            d_oth.update(d_obj)
        # Ensure the variables are not already known as member variables.
        d_members = getattr(jsother, "members", {})
        d_variables = getattr(jsother, "variables", {})

        for name, jsobj in list(d_variables.items()):
            if name in d_members:
                # Decide which one to keep then, remove the variable and then
                # replace the member with the best choice.
                del d_variables[name]
                d_members[name] = self._chooseBestVariable(d_members[name],
                                                           jsobj)
                log.debug("Dupe found: %s %r has both variable and member %r, "
                          "keeping %r", jsother.cixname, jsother.name, name,
                          d_members[name])

        if jsobject.anonymous_functions:
            jsother.anonymous_functions = jsobject.anonymous_functions
            jsobject.anonymous_functions = []

    def _handleDefineProperty(self, styles, text, p):
        # text example:
        #   ['(', 'namespace', ',', '"propname"', ',', '{', ')']
        namelist, p = self._getIdentifiersFromPos(styles, text, p+1)
        if namelist and p+3 < len(styles) and styles[p+1] in self.JS_STRINGS:
            propertyname = self._unquoteJsString(text[p+1])
            if namelist == ["this"]:
                scope = self.currentScope
            else:
                scope = self._findOrCreateScope(namelist, ('variables', 'classes', 'functions'))
            v = JSVariable(propertyname, scope, self.lineno, self.depth)
            if self.lastScope and isinstance(self.lastScope, JSFunction):
                v.type = "%s()" % (self.lastScope.name, )
            scope.addVariable(propertyname, value=v)

    def _handleYAHOOExtension(self, styles, text, p):
        # text example:
        #   ['(', 'Dog', ',', 'Mammal', ',', '{', ')']
        #print "YAHOO!!!!!!"
        #print "len(self.objectArguments): %d" % (len(self.objectArguments), )
        if p+5 < len(styles) and text[p] == "(" and len(self.objectArguments) == 1:
            extendClassNamelist, p = self._getIdentifiersFromPos(styles, text, p+1)
            log.debug("_handleYAHOOExtension:: extendClassNamelist: %r", extendClassNamelist)
            parentClassNamelist, p = self._getIdentifiersFromPos(styles, text, p+1)
            if extendClassNamelist and parentClassNamelist:
                # Add class parent reference
                #print "Extending %r, parent %r" % (extendClassNamelist, parentClassNamelist)
                jsclass = self._addClassPart(".".join(parentClassNamelist), self.ADD_CLASS_PARENT, extendClassNamelist, path=self.path)
                # Now add all information from objectArguments
                self._copyObjectToAnother(self.objectArguments[0][1], jsclass)
        #log.setLevel(logging.WARN)

    def _handleDojoExtension(self, type, styles, text, p):
        if p+4 < len(styles) and text[p] == "(" and len(self.objectArguments) == 1:
            if type=='declare':
                extendClassNamelist = self._unquoteJsString(text[p+1]).split('.')
                p+=2
                parentClassNamelist, p = self._getIdentifiersFromPos(styles, text, p+1)
                if len(extendClassNamelist)>1:
                    scope = self._findOrCreateScope(extendClassNamelist[:-1], ('variables', 'classes', 'functions'))
                else:
                    scope = self.currentScope
                #TODO: should use the lineno of dojo.declare rather than self.objectArguments[0][1].line below
                jsclass = JSClass(extendClassNamelist[-1], scope, self.objectArguments[0][1].line, self.depth)
                scope.classes[jsclass.name] = jsclass
                if parentClassNamelist:
                    jsclass = self._addClassPart(".".join(parentClassNamelist), self.ADD_CLASS_PARENT, extendClassNamelist, path=self.path)
                else:
                    args,p = self._getParenArguments(styles,text,p,'[')
                    parentClassNamelists=['']
                    for arg in args[1:-1]:
                        if arg=='{': #super class is null
                        	break
                        if arg==',':
                            parentClassNamelists.append('')
                            continue
                        parentClassNamelists[-1] += arg
                    if len(parentClassNamelists[0]):
                        self._addClassPart(' '.join(parentClassNamelists), self.ADD_CLASS_PARENT, extendClassNamelist, path=self.path)

            else: #extend
                extendClassNamelist, p = self._getIdentifiersFromPos(styles, text, p+1)
                jsclass = self._locateScopeForName(extendClassNamelist, attrlist=("classes", "functions", "variables", ))
                if not jsclass:
                    return
                if isinstance(jsclass, JSFunction):
                    jsclass=self._convertFunctionToClass(jsclass)

            if jsclass:
                obj=self.objectArguments[0][1]
                self._copyObjectToAnother(obj, jsclass)
                for f in jsclass.functions:
                    jsclass.functions[f]._class = jsclass
                for f in jsclass.anonymous_functions:
                    f._class = jsclass
                # Change function constructor name to the class name so that it
                # is correctly recognized by Komodo as the constructor.
                if 'constructor' in jsclass.functions:
                    func=jsclass.functions.pop('constructor')
                    func.name=jsclass.name
                    jsclass.functions[jsclass.name]=func

    def _removeObjectFromScope(self, jsobject):
        removename = jsobject.name
        parent = jsobject.parent
        if parent:
            searchScopeNames = ("variables", "functions", "classes",)
            if not isinstance(parent, JSFile):
                searchScopeNames += ("members", )
            for scopeName in searchScopeNames:
                scope = getattr(parent, scopeName)
                if removename in scope and scope[removename] == jsobject:
                    log.debug("Removing %r from scope: %s in %r",
                              removename, scopeName, parent.name)
                    scope.pop(removename)
            if jsobject in parent.anonymous_functions:
                parent.anonymous_functions.remove(jsobject)

    def _handleFunctionApply(self, namelist=None):
        """Everything in the function is applied to the supplied scope/namelist"""
        # XXX : TODO
        #       Not everything should be applied. Only the "this." items get
        #       applied!
        # Examples:
        #   (function() { this.xyz = 1; }).apply(namelist);
        #   // Giving namelist an xyz member.

        if namelist is None:
            scope = self.cile
        else:
            # Find the scope
            scope = self._findOrCreateScope(namelist, attrlist=('variables', 'classes', 'functions', ))
        if self.lastScope and isinstance(self.lastScope, JSFunction):
            applyFrom = self.lastScope
            parent = applyFrom.parent
            if isinstance(parent, JSClass) and \
               parent.name == applyFrom.name:
                # We apply everything from the parent then, except the function
                # itself, start by copying everything inside the function.
                self._copyObjectToAnother(applyFrom, scope)
                # Remove the function now
                del parent.functions[applyFrom.name]
                # The class/parent becomes our next target to copy
                applyFrom = parent
            # Copy across everything in the applyFrom object
            self._copyObjectToAnother(applyFrom, scope)
            # Update line counts for proper scope resolution.
            if namelist is not None:
                for name in namelist:
                    if scope.line > self.lastScope.line:
                        scope.line = self.lastScope.line
                        scope.lineend = self.lastScope.lineend
                        scope = scope.parent
            # We need to remove the applyFrom object, it's life is done
            self._removeObjectFromScope(applyFrom)
        elif self.lastScope and isinstance(self.lastScope, JSVariable):
            self._copyObjectToAnother(self.lastScope, scope)
        elif self.objectArguments and isinstance(self.objectArguments[0][1], JSObject):
            self._copyObjectToAnother(self.objectArguments[0][1], scope)

    def _handleFunctionWithArguments(self):
        styles = self.styles
        if len(styles) == 0:
            return
        text = self.text
        lineno = self.lineno

        log.debug("*** _handleFunctionWithArguments line: %d ***", lineno)
        #log.debug("Styles: %r", self.styles)
        log.debug("Text: %r", self.text)
        #log.debug("Comment: %r", self.comment)
        #log.debug("")

        pos = 0
        firstStyle = styles[pos]
        getsetPos = None
        try:
            getsetPos = text.index("__defineGetter__")
        except ValueError:
            try:
                getsetPos = text.index("__defineSetter__")
            except ValueError:
                pass
        if getsetPos is not None and len(styles) > getsetPos+3 and \
           styles[getsetPos+2] in self.JS_STRINGS:
            scopeNames, p = self._getIdentifiersFromPos(styles, text, pos)
            namelist = [ self._unquoteJsString(text[getsetPos+2]) ]
            if scopeNames and scopeNames[0] != "this":
                namelist = scopeNames[:-1] + namelist
            if text[getsetPos] == "__defineSetter__":
                self.addSetter(namelist, doc=self.comment)
            else:
                # Getter is different, it can have a type.
                citdl = None
                for i, scope in self.objectArguments:
                    if isinstance(scope, JSFunction):
                        self.lineno = scope.line
                        citdl = scope.getReturnType()
                        break
                if citdl:
                    self.addGetter(namelist, [citdl], doc=self.comment)
                else:
                    self.addGetter(namelist, [], doc=self.comment)
        elif firstStyle == self.JS_IDENTIFIER:
            namelist, p = self._getIdentifiersFromPos(styles, text, pos)
            if not namelist:
                return
            #print "namelist: %r" % (namelist, )
            if namelist == ["Object", "defineProperty"]:
                # Defines a property on a given scope.
                self._handleDefineProperty(styles, text, p)
            elif namelist == ["XPCOMUtils", "defineLazyGetter"]:
                # Mozilla way to define a property on a given scope.
                self._handleDefineProperty(styles, text, p)
            elif namelist[0] == "YAHOO" and \
               namelist[1:] in (["extend"], ["lang", "extend"]):
                # XXX - Should YAHOO API catalog be enabled then?
                self._handleYAHOOExtension(styles, text, p)
            elif namelist[0] == "dojo" and \
               namelist[1:] in (["extend"], ["declare"]):
                self._handleDojoExtension(namelist[1], styles, text, p)
            elif namelist == ["Ext", "extend"]:
                # Same as what YAHOO does.
                self._handleYAHOOExtension(styles, text, p)
            elif namelist == ["Ext", "apply"] and text[p:p+1] == ["("]:
                # Similar to the regular function apply (see below)
                namelist, p = self._getIdentifiersFromPos(styles, text, p+1)
                if namelist:
                    log.debug("Handling Ext.apply on: %r, line: %d", namelist, self.lineno)
                    self._handleFunctionApply(namelist)
        elif firstStyle == self.JS_OPERATOR:
            if text[:4] == [")", ".", "apply", "("]:
                # Special case for function apply
                namelist, p = self._getIdentifiersFromPos(styles, text, pos+4)
                if namelist:
                    self._handleFunctionApply(namelist)
                elif text[3:5] == ["(", ")"]:
                    # Applied to the global namespace
                    self._handleFunctionApply()

    def _handleAnonFunctionCall(self, lineno):
        """
        The contents of anonymous functions like:
            (function() {...})();
            (function() {...}).call(this);
        should be exported to the prior scope for proper codeintel support.
        This is particularly relevant for top-level anonymous functions.
        This method is called when such a call is made for the anonymous
        function that ends on line number `lineno`.
        @param lineno The line number invoking an anonymous function.
        """
        scope = self.currentScope
        for func in scope.anonymous_functions:
            if func.lineend == lineno:
                if func.cixname == "class":
                    break # do not export anonymous classes
                for attrname in ("classes", "members", "functions", "variables"):
                    d = getattr(func, attrname, {})
                    if attrname == "members":
                        # Scopes do not have members, so transform them into
                        # variables instead.
                        attrname = "variables"
                    for k, v in list(d.items()):
                        getattr(scope, attrname, {})[k] = v
                for v in getattr(func, "anonymous_functions", []):
                    getattr(scope, "anonymous_functions", []).append(v)
                scope.anonymous_functions.remove(func)
        self.anonFuncPending = False

    def _findScopeFromContext(self, styles, text):
        """Determine from the text (a namelist) what scope the text is referring
        to. Returns the scope found or None.
        """
        log.debug("_findScopeFromContext: %r" % (text, ))
        scope = None
        try:
            idx = text.index("prototype")
        except ValueError:
            pass
        else:
            # We have a class prototype, find the class and return with that
            # as the current scope. If it's a function that is not part of a
            # class, then convert the function into a class.
            if idx >= 2 and text[idx-1] == ".":
                namelist, p = self._getIdentifiersFromPos(styles[:idx-1], text[:idx-1], 0)
                if namelist:
                    scope = self._locateScopeForName(namelist, attrlist=("classes", ))
                    if not scope:
                        self._locateScopeForName(namelist, attrlist=("functions", ))
                        if isinstance(scope, JSFunction):
                            # Scope is a function, it should be a class,
                            # convert it now.
                            scope = self._convertFunctionToClass(scope)
                    if scope:
                        log.debug("_findScopeFromContext: found %s %r",
                                  scope.cixname, scope.name)
        return scope

    def _resetState(self, newstate=S_DEFAULT):
        self.state = newstate
        self.bracket_depth = 0
        self.styles = []
        self.lastText = self.text
        self.text = []
        if self.comment:
            self.last_comment_and_jsdoc = [self.comment, None]
        self.comment = []
        self.argumentPosition = 0
        self.argumentTextPosition = 0
        self.objectArguments = []
        #log.debug("Set state %d, line: %d", self.state, self.lineno, )

    def _popPreviousState(self, keep_style_and_text=False):
        current_styles = self.styles
        current_text = self.text
        current_arguments = self.objectArguments
        previous_state = self.state
        if len(self.state_stack) >= 1:
            # Reset to previous state
            self.state, self.bracket_depth, self.styles, \
                self.text, self.lastText, self.comment, \
                self.argumentPosition, self.argumentTextPosition, \
                self.objectArguments, \
                self.in_variable_definition = self.state_stack.pop()
        else:
            # Reset them all
            self._resetState()
        log.debug("_popPreviousState:: previous: %d, current: %d",
                  previous_state, self.state)
        if keep_style_and_text:
            self.styles += current_styles
            self.text += current_text
            self.objectArguments = current_arguments

    def _pushAndSetState(self, newstate=S_DEFAULT):
        self.state_stack.append((self.state, self.bracket_depth, self.styles,
                                 self.text, self.lastText, self.comment,
                                 self.argumentPosition,
                                 self.argumentTextPosition,
                                 self.objectArguments,
                                 self.in_variable_definition))
        self._resetState(newstate)
        self.in_variable_definition = False

    def _endOfScanReached(self):
        """Ensure any remaining text is included in the cile"""
        if len(self.styles) > 0:
            self._addCodePiece(self.styles, self.text, pos=0)

    def token_next(self, style, text, start_column, start_line, **other_args):
        """Loops over the styles in the document and stores important info.

        When enough info is gathered, will perform a call to analyze the code
        and generate subsequent language structures. These language structures
        will later be used to generate XML output for the document."""

        self.tokens.append({"style": style, "text": text, "line": start_line + 1, "column": start_column}) # track for use by getCompletionContext()

        if style in self.JS_CILE_STYLES:
            # We keep track of these styles and the text associated with it.
            # When we gather enough info, these will be sent to the
            # _addCodePiece() function which will analyze the info.

            # We want to use real line numbers starting from 1 (not 0)
            start_line += 1
            #print "state: %d, text: %r" % (self.state, self.text, )
            #log.debug("state: %d, line: %d, self.text: %r, text: %r", self.state, start_line, self.text, text)

            if self.state == S_DEFAULT and len(self.styles) > 0 and \
               self.last_lineno < start_line:
                # We have moved down line(s) and we have data, check if we
                # need to add code from the previous line(s)
                # XXX: Need to be careful with e4x!
                if ((style != self.JS_OPERATOR or text[0] not in "({[.,=") and
                    (self.styles[-1] != self.JS_OPERATOR or
                     self.text[-1] not in "({[.,=") and
                    # We need to ignore certains cases, such as:
                    #   "new \n function", see bug 82569.
                    (self.styles[-1] != self.JS_WORD or self.text[-1] != "new")):
                    self._addCodePiece(self.styles, self.text, pos=0)
                    self.in_variable_definition = False
            self.lineno = start_line
            if style != self.JS_OPERATOR:
                self.styles.append(style)
                self.text.append(text)
                if self.anonFuncPending and text != "call":
                    # There was a '(function() {...}).' construct, but the
                    # method after the trailing '.' was not 'call'. Therefore
                    # that function is nothing special.
                    self.anonFuncPending = False
                    log.debug("...Anonymous function not invoked.")
            else:
                if text == "(": # Only the "(", it's like above
                    # Check if this is of the form "(function { .... })"
                    # This is a fix for self invoking functions/closures
                    #   http://bugs.activestate.com/show_bug.cgi?id=63297
                    if not self.text:
                        log.debug("Ignoring initial brace: '(' on line %d",
                                  self.lineno)
                        return
                if self.anonFuncPending and text[0] != "(":
                    # There was a '(function() {...}).call' construct, but for
                    # whatever reason, there is no invoking '('; the anonymous
                    # function is nothing special.
                    self.anonFuncPending = False
                    log.debug("...Anonymous function not invoked.")
                #log.debug("token_next: line %d, %r, text: %r" % (self.lineno, text, self.text))
                next_op_index = 0
                while next_op_index < len(text):
                    op = text[next_op_index]
                    next_op_index += 1
                    self.styles.append(style)
                    self.text.append(op)
                    #if self.state == S_OBJECT_ARGUMENT:
                    #    if op not in "{}":
                    #        continue
                    if op == "(":
                        if self.bracket_depth == 0:
                            # We can start defining arguments now
                            log.debug("Entering S_IN_ARGS state, line: %d, col: %d", start_line, start_column)
                            newscope = self._findScopeFromContext(self.styles, self.text)
                            self._pushAndSetState(S_IN_ARGS)
                            if newscope and self.currentScope != newscope:
                                log.debug("Adjusting scope to: %r %r",
                                          newscope.cixname, newscope.name)
                                # Need to temporarily adjust the scope to deal
                                # with getters, setters and class prototypes.
                                self.currentScope = newscope
                            self.argumentTextPosition = len(self.text)
                            if self.anonFuncPending:
                                self._handleAnonFunctionCall(start_line)
                        self.bracket_depth += 1
                    elif op == ")":
                        self.bracket_depth -= 1
                        if self.bracket_depth <= 0:
                            # Pop the state, but keep the style and text of
                            # the arguments
                            last_state = self.state
                            self._popPreviousState(keep_style_and_text=True)
                            if self.state != S_IN_ARGS and last_state == S_IN_ARGS:
                                self._handleFunctionWithArguments()
                            log.debug("Entering state %d, line: %d, col: %d", self.state, start_line, start_column)
                        elif isinstance(self.lastScope, JSFunction) and self.text[-3:] == ['{', '(', ')']:
                            # It's a function argument closure.
                            self.lastScope = self._convertFunctionToClosureVariable(self.lastScope)
                    elif op == "=":
                        try:
                            next_ch = text[next_op_index]
                        except IndexError:
                            continue
                        if next_ch == ">":
                            # ES6, => fat arrow function
                            self.text[-1] = "=>"
                            next_op_index += 1
                    #elif op == "=":
                    #    if text == op:
                    #        log.debug("Entering S_IN_ASSIGNMENT state, line: %d, col: %d", start_line, start_column)
                    #        self.state = S_IN_ASSIGNMENT
                    elif op == "{":
                        # Increasing depth/scope, could be an argument object
                        if self.state == S_IN_ARGS:
                            # __defineGetter__("num", function() { return this._num });
                            argTextPos = self.argumentTextPosition
                            if len(self.text) >= 2 and self.text[-2] == ")":
                                # foo( ... ( ... ) {
                                # this really only makes sense as a function expression definition
                                # foo( ... function (...) {
                                # this function may have multiple arguments, so we can't trust
                                # self.argumentTextPosition (which was clobbered)
                                try:
                                    argsStart = len(self.text) - list(reversed(self.text)).index("(")
                                    if argsStart > 1:
                                        functionPos = argsStart - 2 # position of "function" keyword
                                        if self.styles[functionPos] == self.JS_IDENTIFIER: # named function
                                            functionPos -= 1
                                        if functionPos > -1 and \
                                           self.styles[functionPos] == self.JS_WORD and \
                                           self.text[functionPos] == "function":
                                            # this is indeed a function; check arguments for sanity
                                            args = self.text[argsStart:-2]
                                            if all(x == ',' for x in args[1::2]):
                                                # Passing a function as one of the arguments,
                                                # need to create a JSFunction scope for this,
                                                # as various information may be needed, i.e.
                                                # a getter function return type.
                                                obj = self.addAnonymousFunction(args=args[::2])
                                                # don't append to self.objectArguments here, we do
                                                # it later when we see the closing brace
                                                self._pushAndSetState(S_DEFAULT)
                                except ValueError:
                                    # no "(" found in self.text
                                    pass
                            elif len(self.text) >= 2 and \
                               ((self.text[-2] == "(" and
                                 self.argumentPosition == 0) or
                                (self.text[-2] == "," and
                                 self.argumentPosition > 0)):
                                # It's an object argument
                                log.debug("Entering S_OBJECT_ARGUMENT state, line: %d, col: %d", start_line, start_column)
                                #print "Entering S_OBJECT_ARGUMENT state, line: %d, col: %d" % (start_line, start_column)
                                obj = self.createObjectArgument(self.styles, self.text)
                                self.currentScope = obj
                                self._pushAndSetState(S_OBJECT_ARGUMENT)
                            else:
                                self._pushAndSetState(S_IN_ARGS)
                        else:
                            self._addCodePiece(self.styles, self.text, pos=0)
                            self._pushAndSetState(S_DEFAULT)
                        self.incBlock()
                    elif op == "}":
                        # Decreasing depth/scope
                        previous_state = self.state
                        if self.state != S_IN_ARGS:
                            # only add this piece if we're not in an arg state
                            self._addCodePiece(self.styles, self.text, pos=0)
                        self._popPreviousState()
                        if self.state == S_IN_ARGS:
                            self.objectArguments.append((self.argumentPosition, self.currentScope))
                            log.debug("Leaving S_OBJECT_ARGUMENT state, entering S_IN_ARGS state, line: %d, col: %d", start_line, start_column)
                            #print "Leaving S_OBJECT_ARGUMENT state, entering S_IN_ARGS state, line: %d, col: %d" % (start_line, start_column)
                        self.decBlock()
                        if self.anonFuncPending:
                            # Check to see if the function is immediately
                            # invoked or has a trailing '.', which may indicate
                            # a '.call()'. That case must be handled later.
                            index = next_op_index
                            if index < len(text) and text[index] == ')':
                                index += 1 # skip trailing ')' in '(function() {...})'
                            if index >= len(text) or (text[index] != '(' and \
                                                      text[index] != '.'):
                                self.anonFuncPending = False
                                log.debug("...Anonymous function not invoked.")
                    elif op == "," and self.text[0] not in ("let", "var", "const"):
                        # Ignore when it's inside arguments
                        if self.state == S_IN_ARGS:
                            self.argumentPosition += 1
                            self.argumentTextPosition = len(self.text)
                        else:
                            self._addCodePiece(self.styles, self.text, pos=0)
                    elif op == ";":
                        # Statement is done
                        if self.state != S_IN_ARGS:
                            # only add this piece if we're not in an arg state
                            self._addCodePiece(self.styles, self.text, pos=0)
                        self.in_variable_definition = False
            # Remember the last code line we looked at
            self.last_lineno = self.lineno
        elif style in self.JS_COMMENT_STYLES:
            self.comment.append(text)

    def convertToElementTreeFile(self, cixelement, file_lang, module_lang=None):
        """Store JS information into the cixelement as a file(s) sub element"""
        self.cile.convertToElementTreeFile(cixelement, file_lang, module_lang)

    def convertToElementTreeModule(self, cixmodule):
        """Store JS information into already created cixmodule"""
        self.cile.convertToElementTreeModule(cixmodule)

class NodeJSScanner(JavaScriptScanner):
    def scan(self, filename, env={}):
        """Implementation of AbstractScanner.scan().
        For testing purposes, when filename is None, stdin is scanned.
        Also process JSON files for NodeJS module-related purposes.
        """
        if not filename or not filename.endswith(".json"):
            return super(NodeJSScanner, self).scan(filename, env)

        scope = Scope()
        f = open(filename)
        try:
            obj = json.load(f)
            for k, v in obj.items():
                if isinstance(v, str):
                    # NodeJS modules only care about string key-value pairs.
                    symbol = Struct(k, v)
                    scope.define(symbol)
        except json.JSONDecodeError:
            pass
        f.close()
        return scope

class HTMLJavaScriptScanner(JavaScriptScanner, AbstractUDLSubScanner):
    from language.legacy.javascript.stdlib import JAVASCRIPT_STDLIB_FILE
    def __init__(self, stdlib_file=JAVASCRIPT_STDLIB_FILE):
        from language.legacy.html.scanner import HTMLLexer
        super(HTMLJavaScriptScanner, self).__init__(stdlib_file, HTMLLexer)

    @property
    def namespace(self):
        """Implementation of AbstractUDLSubScanner.namespace."""
        return "JavaScript"

    def prepForUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.prepForUDLTokens()."""
        self.scan("")

    def handleUDLToken(self, **kwargs):
        """Implementation of AbstractUDLSubScanner.handleUDLToken()."""
        self.token_next(**kwargs)

    def doneWithUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.doneWithUDLTokens()."""
        # Ensure we take notice of any text left in the ciler
        self._endOfScanReached()
        #if updateAllScopeNames:
            # We've parsed up the JavaScript, fix any variables types
            #self.cile.updateAllScopeNames()
        self.cile.updateAllScopeNames()

        scope = Scope(self.builtInScope)
        allValues = list(self.cile.functions.values()) + list(self.cile.variables.values()) + \
                    list(self.cile.classes.values()) + self.cile.anonymous_functions
        for v in sorted(allValues, key=operator.attrgetter("line", "name")):
            if not v.isHidden:
                scope.define(v.toAbstractSymbol(scope))
        if "document" in self.cile.variables:
            # The JavaScript scanner creates variables as they are encountered
            # when resolving types (e.g. 'var foo = document.getElementById();
            # var bar = foo.getContext();'). Assume any 'document' was created
            # this way and delete it in order to resolve the correct stdlib
            # symbol.
            del scope.members["document"]
        return scope

    def getUDLCompletionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCompletionContext()."""
        # The UDL lexer produces slightly different tokens than expected.
        # Normalize them. Also, The JavaScript scanner expects 1-based lines,
        # but UDL gives 0-based lines.
        for token in tokens:
            token["line"] = token["start_line"] + 1
            token["column"] = token["start_column"]
        return self._getCompletionContext(filename, position, env, tokens, line, column, scope)

    def getUDLGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLGotoDefinitionContext()."""
        # The UDL lexer produces slightly different tokens than expected.
        # Normalize them. Also, The JavaScript scanner expects 1-based lines,
        # but UDL gives 0-based lines.
        for token in tokens:
            token["line"] = token["start_line"] + 1
            token["column"] = token["start_column"]
        return self._getGotoDefinitionContext(filename, position, env, tokens, line, column, scope)

    def getUDLCallTipContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCallTipContext()."""
        # The UDL lexer produces slightly different tokens than expected.
        # Normalize them. Also, The JavaScript scanner expects 1-based lines,
        # but UDL gives 0-based lines.
        for token in tokens:
            token["line"] = token["start_line"] + 1
            token["column"] = token["start_column"]
        return self._getCallTipContext(filename, position, env, tokens, line, column, scope)

class Utils(object):
    @staticmethod
    def unquoteJsString(s):
        """Return the string without quotes around it"""
        if len(s) >= 2 and s[0] in "\"'":
            return s[1:-1]
        return s

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    from language.legacy.javascript.stdlib import JAVASCRIPT_STDLIB_FILE, NODEJS_STDLIB_FILE
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    parser = argparse.ArgumentParser(description="Scan JavaScript source files")
    parser.add_argument("-js", action="store_const", const=True, default=True)
    parser.add_argument("-nodejs", action="store_const", const=True)
    parser.add_argument("-html", action="store_const", const=True)
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    if args.nodejs:
        scanner = NodeJSScanner(NODEJS_STDLIB_FILE)
    elif args.html:
        scanner = HTMLJavaScriptScanner(JAVASCRIPT_STDLIB_FILE)
    elif args.js:
        scanner = JavaScriptScanner(JAVASCRIPT_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
