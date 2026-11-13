# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for PHP source code.
CodeIntel v2's original PHP parser has been adapted to operate with the new
CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla UDL (via SilverCity),
and walks through the token list, producing something akin to an Abstract Syntax
Tree in the end for database storage.
Completion contexts also utilize the token list.
"""

import logging
import os
import re

from symbols import (AbstractScope,
                     AbstractConstant, AbstractVariable, AbstractInstanceVariable,
                     AbstractClass, AbstractInterface,
                     AbstractFunction, AbstractStaticMethod,
                     AbstractTrait,
                     AbstractModule, AbstractNamespace)
from calltips import CallTipContext
from completions import AbstractMemberCompletionContext, AbstractScopeCompletionContext, ApproximateSymbolContext
from goto_definition import GotoDefinitionContext
from find_references import AbstractFindReferencesContext
from language.common import Scope, Constant, Variable, InstanceVariable, PrivateInstanceVariable, ProtectedInstanceVariable, ClassVariable, Argument, Class, Interface, Function, Constructor, Method, StaticMethod, Trait, Namespace, Import, AbstractScanner, AbstractScannerContext, FilenameSyntaxDescription, SymbolResolver
from language.legacy.php.import_resolver import PHPImportResolver
from language.legacy.html.scanner import HTMLScanner
from language.legacy.udl import UDLLexer, AbstractUDLSubScanner

from db.model.helpers import fileExists, fetchSymbolsInFile, fetchSymbolsInDirectories, fetchAllFilesInDirectory

from SilverCity.ScintillaConstants import (SCE_UDL_SSL_DEFAULT,
                                           SCE_UDL_SSL_OPERATOR,
                                           SCE_UDL_SSL_IDENTIFIER,
                                           SCE_UDL_SSL_WORD,
                                           SCE_UDL_SSL_VARIABLE,
                                           SCE_UDL_SSL_STRING,
                                           SCE_UDL_SSL_NUMBER,
                                           SCE_UDL_SSL_COMMENT,
                                           SCE_UDL_SSL_COMMENTBLOCK)
from functools import cmp_to_key


def cmp(a, b):
    """Python 2-style three-way comparison (removed in Python 3)."""
    return (a > b) - (a < b)


log = logging.getLogger("codeintel.php")
# log.setLevel(logging.DEBUG)

class PHPLexer(UDLLexer):
    lang = "PHP"

# States used by PHP scanner when parsing information
S_DEFAULT = 0
S_IN_ARGS = 1
S_IN_ASSIGNMENT = 2
S_IGNORE_SCOPE = 3
S_OBJECT_ARGUMENT = 4
S_GET_HEREDOC_MARKER = 5
S_IN_HEREDOC = 6
S_TRAIT_RESOLUTION = 7
# Special tags for multilang handling (i.e. through UDL)
S_OPEN_TAG  = 10
S_CHECK_CLOSE_TAG = 11
S_IN_SCRIPT = 12

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


def _sortByLineCmp(val1, val2):
    try:
    #if hasattr(val1, "line") and hasattr(val2, "line"):
        return cmp(val1.linestart, val2.linestart)
    except AttributeError:
        return cmp(val1, val2)

def sortByLine(seq):
    seq.sort(key=cmp_to_key(_sortByLineCmp))
    return seq

class PHPScannerContext(AbstractScannerContext):
    """Implementation of AbstractScannerContext for the PHP scanner."""
    def __init__(self, phpThing):
        self._phpThing = phpThing

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        if hasattr(self._phpThing, "linestart"):
            return self._phpThing.linestart
        elif hasattr(self._phpThing, "lineno"):
            return self._phpThing.lineno
        else:
            return None

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation."""
        return getattr(self._phpThing, "doc", None)

    @property
    def signature(self):
        """Implementation of AbstractSymbolContext.signature."""
        return getattr(self._phpThing, "signature", None)

    def contains(self, line):
        """Implementation of AbstractScannerContext.contains()."""
        return line >= self.line and (not getattr(self._phpThing, "lineend", None) or line <= self._phpThing.lineend)

class PHPArg:
    def __init__(self, name, citdl=None, signature=None, default=None):
        """Set details for a function argument"""
        self.name = name
        self.citdl = citdl
        if signature:
            self.signature = signature
        else:
            if citdl:
                self.signature = "%s $%s" % (citdl, name)
            else:
                self.signature = "$%s" % (name, )
        self.default = default

    def __repr__(self):
        return self.signature

    def updateCitdl(self, citdl):
        self.citdl = citdl
        if self.signature.startswith("$") or self.signature.startswith("&") or \
           " " not in self.signature:
            self.signature = "%s %s" % (citdl, self.signature)
        else:
            self.signature = "%s %s" % (citdl, self.signature.split(" ", 1)[1])

    def toElementTree(self, cixelement, linestart=None):
        cixarg = addCixArgument(cixelement, self.name, argtype=self.citdl)
        if self.default:
            cixarg.attrib["default"] = self.default
        if linestart is not None:
            cixarg.attrib["line"] = str(linestart)

    def toAbstractSymbol(self, func):
        symbol = Argument("$" + self.name, self.citdl, PHPScannerContext(func))
        return symbol

class PHPVariable:

    # PHPDoc variable type sniffer.
    _re_var = re.compile(r'^\s*@var\s+(\$(?P<variable>\w+)\s+)?(?P<type>[\w\\]+)(\s+\$(?P<variable2>\w+)\s+)?(?:\s+(?P<doc>.*?))?', re.M|re.U)
    _ignored_php_types = ("object", "mixed")

    def __init__(self, name, line, vartype='', attributes='', doc=None,
                 fromPHPDoc=False, namespace=None):
        self.name = name
        self.types = [(line, vartype, fromPHPDoc)]
        self.linestart = line
        if attributes:
            if not isinstance(attributes, list):
                attributes = attributes.strip().split()
            self.attributes = ' '.join(attributes)
        else:
            self.attributes = None
        self.doc = doc
        self.created_namespace = None
        if namespace:
            self.created_namespace = namespace.name

    def addType(self, line, type, fromPHPDoc=False):
        self.types.append((line, type, fromPHPDoc))

    def __repr__(self):
        return "var %s line %s type %s attributes %s\n"\
               % (self.name, self.linestart, self.types, self.attributes)

    def toElementTree(self, cixblob):
        # Work out the best vartype
        vartype = None
        doc = None
        if self.doc:
            # We are only storing the doc string for cases where we have an
            # "@var" phpdoc tag, we should actually store the docs anyway, but
            # we don't yet have a clean way to ensure the doc is really meant
            # for this specific variable (i.e. the comment was ten lines before
            # the variable definition).
            if "@var" in self.doc:
                doc = uncommentDocString(self.doc)
                # Get the variable citdl type set by "@var". For multiple "@var"
                # declarations, look for the matching name.
                for match in re.finditer(self._re_var, doc):
                    groups = match.groupdict()
                    vartype = groups['type']
                    if vartype and (groups['variable'] and self.name != groups['variable'] or
                                    groups['variable2'] and self.name != groups['variable2']):
                        vartype = None # wrong variable
                        continue
                    if vartype and vartype.lower() in self._ignored_php_types:
                        # Ignore these PHP types, they don't help codeintel.
                        # http://bugs.activestate.com/show_bug.cgi?id=77602
                        vartype = None
                    break # only consider the first @var if name not given

        if not vartype and self.types:
            d = {}
            max_count = 0
            for line, vtype, fromPHPDoc in self.types:
                if vtype:
                    if fromPHPDoc:
                        if vtype.lower() in self._ignored_php_types:
                            # Ignore these PHP types, they don't help codeintel.
                            continue
                        # The doc gets priority.
                        vartype = vtype
                        break
                    count = d.get(vtype, 0) + 1
                    d[vtype] = count
                    if count > max_count:
                        # Best found so far
                        vartype = vtype
                        max_count = count
        cixelement = createCixVariable(cixblob, self.name, vartype=vartype,
                                       attributes=self.attributes)
        if doc:
            setCixDoc(cixelement, doc)
        cixelement.attrib["line"] = str(self.linestart)
        if self.created_namespace:
            # Need to remember that the object was created in a namespace, so
            # that the starting lookup scope can start in the given namespace.
            cixelement.attrib["namespace"] = self.created_namespace
        return cixelement

    def toAbstractSymbol(self, enclosingScope):
        vartype = None
        doc = None
        if self.doc:
            # We are only storing the doc string for cases where we have an
            # "@var" phpdoc tag, we should actually store the docs anyway, but
            # we don't yet have a clean way to ensure the doc is really meant
            # for this specific variable (i.e. the comment was ten lines before
            # the variable definition).
            if "@var" in self.doc:
                doc = uncommentDocString(self.doc)
                # Get the variable citdl type set by "@var". For multiple "@var"
                # declarations, look for the matching name.
                for match in re.finditer(self._re_var, doc):
                    groups = match.groupdict()
                    vartype = groups['type']
                    if vartype and (groups['variable'] and self.name != groups['variable'] or
                                    groups['variable2'] and self.name != groups['variable2']):
                        vartype = None # wrong variable
                        continue
                    if vartype and vartype.lower() in self._ignored_php_types:
                        # Ignore these PHP types, they don't help codeintel.
                        # http://bugs.activestate.com/show_bug.cgi?id=77602
                        vartype = None
                    break # only consider the first @var if name not given

        if not vartype and self.types:
            d = {}
            max_count = 0
            for line, vtype, fromPHPDoc in self.types:
                if vtype:
                    if fromPHPDoc:
                        if vtype.lower() in self._ignored_php_types:
                            # Ignore these PHP types, they don't help codeintel.
                            continue
                        # The doc gets priority.
                        vartype = vtype
                        break
                    count = d.get(vtype, 0) + 1
                    d[vtype] = count
                    if count > max_count:
                        # Best found so far
                        vartype = vtype
                        max_count = count
        if not isinstance(self, PHPConstant):
            if isinstance(enclosingScope, AbstractClass):
                if self.attributes and "static" in self.attributes:
                    symbol = ClassVariable("$" + self.name, vartype, PHPScannerContext(self))
                elif self.attributes and "private" in self.attributes:
                    symbol = PrivateInstanceVariable(self.name, vartype, PHPScannerContext(self))
                elif self.attributes and "protected" in self.attributes:
                    symbol = ProtectedInstanceVariable(self.name, vartype, PHPScannerContext(self))
                else:
                    symbol = InstanceVariable(self.name, vartype, PHPScannerContext(self))
            else:
                self.name = self.name.lstrip("$")
                symbol = Variable("$" + self.name, vartype, PHPScannerContext(self))
        else:
            symbol = Constant(self.name, vartype, PHPScannerContext(self))
        #if self.created_namespace:
        #    pass # TODO:
        return symbol

class PHPConstant(PHPVariable):
    def __init__(self, name, line, vartype=''):
        PHPVariable.__init__(self, name, line, vartype)

    def __repr__(self):
        return "constant %s line %s type %s\n"\
               % (self.name, self.linestart, self.types)

    def toElementTree(self, cixblob):
        cixelement = PHPVariable.toElementTree(self, cixblob)
        cixelement.attrib["ilk"] = "constant"
        return cixelement

#---- javadoc parsing

_javadoc1 = re.compile(r'\s*\/\*(.*)\*\/', re.S)
_javadoc2 = re.compile(r'^(\s*\*)', re.M)
_linedoc = re.compile(r'^(\s*#|\s*\/\/)', re.M)
_indent = re.compile(r'^([ \t]*)', re.M)
_param = re.compile(r'^\s*@param\s+(?P<type>[\w\\]+)\s+\$(?P<name>\w+)(?:\s+?(?P<doc>.*?))?', re.M|re.U)
_return = re.compile(r'^\s*@return\s+(?P<type>[\w\\]+)(?:\s+(?P<doc>.*))?', re.M|re.U)

def uncommentDocString(doc):
    # remove block style leading and end comments
    d = '\n'.join(re.findall(_javadoc1, doc))
    if d:
        # remove starting * if javadoc style
        d = re.sub(_javadoc2,'',d)
    else:
        d = doc
        # remove line style comments
        d = re.sub(_linedoc,'',d)


    # trim preceeding blank lines.  we dont want to trim the first non-blank line
    lines = d.split('\n')
    while len(lines) and not lines[0].strip():
        lines.pop(0)
    d = '\n'.join(lines)

    # trip any blank end lines
    d = d.rstrip()

    # guess the indent size
    spaces = re.findall(_indent, d)
    indent = spaces[0]
    for s in spaces:
        if len(s) and len(s) < indent:
            indent = len(s)

    # dedent the block
    if not indent:
        return d
    dedent = re.compile(r'^([ \t]{%d})' % indent, re.M)
    d = re.sub(dedent, '', d)
    return d

def parseDocString(doc):
    d = uncommentDocString(doc)
    params = re.findall(_param, d)
    result = re.findall(_return, d)
    if result:
        result = result[0]
    return (d, params, result)

class PHPFunction:
    def __init__(self, funcname, phpArgs, lineno, depth=0,
                 attributes=None, doc=None, classname='', classparent='',
                 returnType=None, returnByRef=False):
        self.name = funcname
        self.args = phpArgs
        self.linestart = lineno
        self.lineend = None
        self.depth = depth
        self.classname = classname
        self.classparent = classparent
        self.returnType = returnType
        self.returnByRef = returnByRef
        self.variables = {} # all variables used in class
        # build the signature before we add any attributes that are not part
        # of the signature
        if returnByRef:
            self.signature = '&%s' % (self.name)
        else:
            self.signature = '%s' % (self.name)
        if attributes:
            attrs = ' '.join(attributes)
            self.shortSig = '%s %s' % (attrs, self.name)
        else:
            self.shortSig = self.name
        # both php 4 and 5 constructor methods
        if attributes is not None and (funcname == '__construct' or (classname and funcname.lower() == classname.lower())):
            attributes.append('__ctor__')
# if we add destructor attributes...
#        elif funcname == '__destruct':
#            attributes += ['__dtor__']
        self.attributes = attributes and ' '.join(attributes) or ''
        self.doc = None

        if doc:
            if isinstance(doc, list):
                doc = "".join(doc)
            docinfo = parseDocString(doc)
            self.doc = self.parsePHPDocBlock(docinfo[0])
            # See if there are any PHPDoc arguments defined in the docstring.
            if docinfo[1]:
                for argInfo in docinfo[1]:
                    for phpArg in self.args:
                        if phpArg.name == argInfo[1]:
                            phpArg.updateCitdl(argInfo[0])
                            break
                    else:
                        self.args.append(PHPArg(argInfo[1], citdl=argInfo[0]))
            if docinfo[2]:
                self.returnType = docinfo[2][0]
        if self.returnType:
            self.signature = '%s %s' % (self.returnType.lower(), self.signature, )
        self.signature += "("
        if self.args:
            self.signature += ", ".join([x.signature for x in self.args])
        self.signature += ")"

    def parsePHPDocBlock(self, docblock):
        docstrings = docblock.split('\n')
        docblock_parsed = "\n"
        try:
            for docstr in docstrings:

                # @param entries
                if docstr.startswith("@param"):
                    info = docstr.split()
                    param_type = info[1]
                    param_name = info[2]
                    description = ""
                    if len(info) > 2:
                        description = " - " + " ".join(info[3:])
                    # <param_type> param_name - param_description (if exists)
                    docblock_parsed += '<%s> %s %s\n' % (param_type.lower(), param_name, description, )

                # @return entries
                elif docstr.startswith("@return"):
                    info = docstr.split()
                    return_type = info[1]
                    description = ""
                    if len(info) > 2:
                        description = " - " + " ".join(info[2:])
                    docblock_parsed += "Returns %s %s\n" % (return_type.lower(), description)

                # Misc @ prefixed entries
                elif docstr.startswith("@"):
                    # remove @ and make the first latter uppercase
                    docstr = docstr[1].upper() + docstr[2:]
                    docblock_parsed += "%s\n" % docstr

                # comments which have not to be parsed (skip empty strings)
                elif len(docstr.strip()) > 0:
                    docblock_parsed += "%s\n" % docstr

        except IndexError:
            # Malformed docblock
            return docblock

        return docblock_parsed

    def addReturnType(self, returnType):
        if self.returnType is None:
            self.returnType = returnType

    def __str__(self):
        return self.signature
        # The following is busted and outputting multiple lines from __str__
        # and __repr__ is bad form: make debugging prints hard.
        #if self.doc:
        #    if self.args:
        #        return "%s(%s)\n%s" % (self.shortSig, self.args.argline, self.doc)
        #    else:
        #        return "%s()\n%s" % (self.shortSig, self.doc)
        #return "%s(%s)" % (self.shortSig, self.argline)

    def __repr__(self):
        return self.signature

    def hasArgumentWithName(self, name):
        if self.args:
            for phpArg in self.args:
                if phpArg.name == name:
                    return True
        return False

    def toElementTree(self, cixblob):
        cixelement = createCixFunction(cixblob, self.name,
                                       attributes=self.attributes)
        cixelement.attrib["line"] = str(self.linestart)
        if self.lineend is not None:
            cixelement.attrib['lineend'] = str(self.lineend)
        setCixSignature(cixelement, self.signature)
        if self.doc:
            setCixDoc(cixelement, self.doc)
        if self.args:
            for phpArg in self.args:
                phpArg.toElementTree(cixelement, self.linestart)
        if self.returnType:
            addCixReturns(cixelement, self.returnType)
        # Add a "this" and "self" member for class functions
        #if self.classname:
        #    createCixVariable(cixelement, "this", vartype=self.classname)
        #    createCixVariable(cixelement, "self", vartype=self.classname)
        # Add a "parent" member for class functions that have a parent
        #if self.classparent:
        #    createCixVariable(cixelement, "parent", vartype=self.classparent)

        # XXX for variables inside functions
        for v in list(self.variables.values()):
            v.toElementTree(cixelement)

    def toAbstractSymbol(self, enclosingScope):
        if isinstance(enclosingScope, AbstractClass):
            if "static" in self.attributes:
                symbol = StaticMethod(self.name, None, self.returnType, enclosingScope, PHPScannerContext(self))
            elif "__ctor__" in self.attributes:
                symbol = Constructor(self.name, None, self.returnType, enclosingScope, PHPScannerContext(self))
            else:
                symbol = Method(self.name, None, self.returnType, enclosingScope, PHPScannerContext(self))
        else:
            symbol = Function(self.name, None, self.returnType, enclosingScope, PHPScannerContext(self))
        if self.args:
            for phpArg in self.args:
                symbol.define(phpArg.toAbstractSymbol(self))

        for v in list(self.variables.values()):
            symbol.define(v.toAbstractSymbol(self))
        return symbol

class PHPInterface:
    def __init__(self, name, extends, lineno, depth, doc=None):
        self.name = name
        self.extends = extends
        self.linestart = lineno
        self.lineend = None
        self.depth = depth
        self.constants = {} # declared class constants
        self.members = {} # declared class variables
        self.variables = {} # all variables used in class
        self.functions = {}
        self.doc = None
        if doc:
            self.doc = uncommentDocString(doc)

    def __repr__(self):
        # dump our contents to human readable form
        r = "INTERFACE %s" % self.name
        if self.extends:
            r += " EXTENDS %s" % self.extends
        r += '\n'

        if self.constants:
            r += "Constants:\n"
            for m in self.constants:
                r += "    var %s line %s\n"  % (m, self.constants[m])

        if self.members:
            r += "Members:\n"
            for m in self.members:
                r += "    var %s line %s\n"  % (m, self.members[m])

        if self.functions:
            r += "functions:\n"
            for f in list(self.functions.values()):
                r += "    %r" % f

        if self.variables:
            r += "variables:\n"
            for v in list(self.variables.values()):
                r += "    %r" % v

        return r + '\n'

    def toElementTree(self, cixblob):
        cixelement = createCixInterface(cixblob, self.name)
        cixelement.attrib["line"] = str(self.linestart)
        if self.lineend is not None:
            cixelement.attrib["lineend"] = str(self.lineend)
        signature = "%s" % (self.name)
        if self.extends:
            signature += " extends %s" % (self.extends)
            for name in self.extends.split(","):
                addInterfaceRef(cixelement, name.strip())
            #SubElement(cixelement, "classref", name=self.extends)
        cixelement.attrib["signature"] = signature

        if self.doc:
            setCixDoc(self.doc)

        allValues = list(self.functions.values()) + list(self.constants.values()) + \
                    list(self.members.values()) + list(self.variables.values())
        for v in sortByLine(allValues):
            v.toElementTree(cixelement)

    def toAbstractSymbol(self, enclosingScope):
        if self.extends:
            extends = [name.strip() for name in self.extends.split(",")]
            symbol = Interface(self.name, enclosingScope, extends, PHPScannerContext(self))
        else:
            symbol = Interface(self.name, enclosingScope, None, PHPScannerContext(self))

        allValues = list(self.functions.values()) + list(self.constants.values()) + \
                    list(self.members.values()) + list(self.variables.values())
        for v in sortByLine(allValues):
            symbol.define(v.toAbstractSymbol(symbol))

        return symbol

class PHPClass:

    cixtype = "CLASS"
    # PHPDoc magic property sniffer.
    _re_magic_property = re.compile(r'^\s*@property(-(?P<type>read|write))?\s+((?P<citdl>[\w\\]+)\s+)?(?P<name>\$\w+)(?:\s+(?P<doc>.*?))?', re.M|re.U)
    _re_magic_method = re.compile(r'^\s*@method\s+((?P<citdl>[\w\\]+)\s+)?(?P<name>\w+)(\(\))?(?P<doc>.*?)$', re.M|re.U)

    def __init__(self, name, extends, lineno, depth, attributes=None,
                 interfaces=None, doc=None):
        self.name = name
        self.extends = extends
        self.linestart = lineno
        self.lineend = None
        self.depth = depth
        self.constants = {} # declared class constants
        self.members = {} # declared class variables
        self.variables = {} # all variables used in class
        self.functions = {}
        self.traits = {}
        self.traitOverrides = {}
        if interfaces:
            self.interfaces = interfaces.split(',')
        else:
            self.interfaces = []
        if attributes:
            self.attributes = ' '.join(attributes)
        else:
            self.attributes = None
        self.doc = None
        if doc:
            if isinstance(doc, list):
                doc = "".join(doc)
            self.doc = uncommentDocString(doc)
            if self.doc.find("@property") >= 0:
                all_matches = re.findall(self._re_magic_property, self.doc)
                for match in all_matches:
                    varname = match[4][1:]  # skip "$" in the name.
                    v = PHPVariable(varname, lineno, match[3], doc=match[5])
                    self.members[varname] = v
            if self.doc.find("@method") >= 0:
                all_matches = re.findall(self._re_magic_method, self.doc)
                for match in all_matches:
                    citdl = match[1] or None
                    fnname = match[2]
                    fndoc = match[4]
                    phpArgs = []
                    fn = PHPFunction(fnname, phpArgs, lineno, depth=self.depth+1,
                                     doc=fndoc, returnType=citdl)
                    self.functions[fnname] = fn

    def __repr__(self):
        # dump our contents to human readable form
        r = "%s %s" % (self.cixtype, self.name)
        if self.extends:
            r += " EXTENDS %s" % self.extends
        r += '\n'

        if self.constants:
            r += "Constants:\n"
            for m in self.constants:
                r += "    var %s line %s\n"  % (m, self.constants[m])

        if self.members:
            r += "Members:\n"
            for m in self.members:
                r += "    var %s line %s\n"  % (m, self.members[m])

        if self.functions:
            r += "functions:\n"
            for f in list(self.functions.values()):
                r += "    %r" % f

        if self.variables:
            r += "variables:\n"
            for v in list(self.variables.values()):
                r += "    %r" % v

        if self.traits:
            r += "traits:\n"
            for k, v in list(self.traits.items()):
                r += "    %r" % k
            if self.traitOverrides:
                r += "trait overrides:\n"
                for k, v in list(self.traitOverrides.items()):
                    r += "    %r, %r" % (k, v)

        return r + '\n'

    def addTraitReference(self, name):
        self.traits[name] = []

    def addTraitOverride(self, namelist, alias, visibility=None, insteadOf=False):
        self.traitOverrides[".".join(namelist)] = (alias, visibility, insteadOf)

    def _toElementTree(self, cixblob, cixelement):
        cixelement.attrib["line"] = str(self.linestart)
        if self.lineend is not None:
            cixelement.attrib["lineend"] = str(self.lineend)
        if self.attributes:
            cixelement.attrib["attributes"] = self.attributes

        if self.doc:
            setCixDoc(cixelement, self.doc)

        if self.extends:
            addClassRef(cixelement, self.extends)

        if self.traits:
            cixelement.attrib["traitrefs"] = " ".join(self.traits)
            for citdl, data in list(self.traitOverrides.items()):
                alias, vis, insteadOf = data
                if alias and not insteadOf:
                    name = alias
                else:
                    name = citdl.split(".")[-1]
                override_elem = SubElement(cixelement, "alias", name=name, citdl=citdl)
                if insteadOf:
                    override_elem.attrib["insteadof"] = alias
                if vis:
                    override_elem.attrib["attributes"] = vis

        for i in self.interfaces:
            addInterfaceRef(cixelement, i.strip())

        allValues = list(self.functions.values()) + list(self.constants.values()) + \
                    list(self.members.values()) + list(self.variables.values())
        for v in sortByLine(allValues):
            v.toElementTree(cixelement)

    def toElementTree(self, cixblob):
        cixelement = createCixClass(cixblob, self.name)
        self._toElementTree(cixblob, cixelement)

    def toAbstractSymbol(self, enclosingScope):
        superclassNames = []
        if self.extends:
            superclassNames.append(self.extends)
        if self.traits:
            superclassNames.extend(self.traits)
            # TODO: self.traitOverrides, aliases, insteadof, etc.
        for i in self.interfaces:
            superclassNames.append(i.strip())

        if len(superclassNames) > 1:
            symbol = Class(self.name, enclosingScope, superclassNames, PHPScannerContext(self))
        elif len(superclassNames) == 1:
            symbol = Class(self.name, enclosingScope, superclassNames[0], PHPScannerContext(self))
            symbol.define(InstanceVariable("parent", superclassNames[0]))
        else:
            symbol = Class(self.name, enclosingScope, None, PHPScannerContext(self))
        symbol.define(InstanceVariable("$this", self.name))
        symbol.define(InstanceVariable("self", self.name))

        allValues = list(self.functions.values()) + list(self.constants.values()) + \
                    list(self.members.values()) + list(self.variables.values())
        for v in sortByLine(allValues):
            symbol.define(v.toAbstractSymbol(symbol))
        return symbol

class PHPTrait(PHPClass):
    cixtype = "TRAIT"
    def toElementTree(self, cixblob):
        cixelement = SubElement(cixblob, "scope", ilk="trait", name=self.name)
        self._toElementTree(cixblob, cixelement)

    def toAbstractSymbol(self, enclosingScope):
        symbol = Trait(self.name, enclosingScope, PHPScannerContext(self))
        symbol.merge(PHPClass.toAbstractSymbol(self, enclosingScope))
        return symbol

class PHPImport:
    def __init__(self, name, lineno, alias=None, symbol=None, ilk=None):
        self.name = name
        self.lineno = lineno
        self.alias = alias
        self.symbol = symbol
        self.ilk = ilk

    def __repr__(self):
        # dump our contents to human readable form
        if self.alias:
            return "IMPORT %s as %s\n" % (self.name, self.alias)
        else:
            return "IMPORT %s\n" % self.name

    def toElementTree(self, cixmodule):
        elem = SubElement(cixmodule, "import", module=self.name, line=str(self.lineno))
        if self.alias:
            elem.attrib["alias"] = self.alias
        if self.symbol:
            elem.attrib["symbol"] = self.symbol
        if self.ilk:
            elem.attrib["ilk"] = self.ilk
        return elem

    def toAbstractSymbol(self):
        if not self.symbol:
            # "require 'foo.php'" or "include 'foo.php'"
            symbol = Import(self.name, self.name, PHPScannerContext(self))
        elif not self.name:
            # "use foo"
            symbol = Import(self.alias or self.symbol, self.symbol, PHPScannerContext(self))
        else:
            # "use foo\bar"
            symbol = Import(self.alias or self.symbol, "%s\\%s" % (self.name, self.symbol), PHPScannerContext(self))
        return symbol

def qualifyNamespacePath(namespace_path):
    # Ensure the namespace does not begin or end with a backslash.
    return namespace_path.strip("\\")

class PHPNamespace:
    def __init__(self, name, lineno, depth, doc=None):
        assert not name.startswith("\\")
        assert not name.endswith("\\")
        self.name = name
        self.linestart = lineno
        self.lineend = None
        self.depth = depth
        self.doc = None
        if doc:
            self.doc = uncommentDocString(doc)

        self.functions = {} # functions declared in file
        self.classes = {} # classes declared in file
        self.constants = {} # all constants used in file
        self.interfaces = {} # interfaces declared in file
        self.includes = [] # imported files/namespaces

    def __repr__(self):
        # dump our contents to human readable form
        r = "NAMESPACE %s\n" % self.name

        for v in self.includes:
            r += "    %r" % v

        r += "constants:\n"
        for v in list(self.constants.values()):
            r += "    %r" % v

        r += "interfaces:\n"
        for v in list(self.interfaces.values()):
            r += "    %r" % v

        r += "functions:\n"
        for f in list(self.functions.values()):
            r += "    %r" % f

        r += "classes:\n"
        for c in list(self.classes.values()):
            r += repr(c)

        return r + '\n'

    def toElementTree(self, cixblob):
        cixelement = createCixNamespace(cixblob, self.name)
        cixelement.attrib["line"] = str(self.linestart)
        if self.lineend is not None:
            cixelement.attrib["lineend"] = str(self.lineend)

        if self.doc:
            setCixDoc(cixelement, self.doc)

        for v in self.includes:
            v.toElementTree(cixelement)

        allValues = list(self.functions.values()) + list(self.constants.values()) + \
                    list(self.interfaces.values()) + list(self.classes.values())
        for v in sortByLine(allValues):
            v.toElementTree(cixelement)

    def toAbstractSymbol(self, enclosingScope):
        name_parts = self.name.split("\\")
        scope = enclosingScope
        while scope.enclosingScope and scope.enclosingScope.enclosingScope:
            scope = scope.enclosingScope
        while len(name_parts) > 0:
            if not scope.resolveMember(name_parts[0]):
                break
            scope = scope.resolveMember(name_parts.pop(0))
            if self.linestart < scope.ctx._phpThing.linestart:
                scope.ctx._phpThing.linestart = self.linestart # update
            if self.lineend is None or self.lineend > scope.ctx._phpThing.lineend:
                scope.ctx._phpThing.lineend = self.lineend # update
        if len(name_parts) > 0:
            for name_part in name_parts:
                symbol = Namespace(name_part, scope, PHPScannerContext(self))
                if not self.name.startswith(name_part):
                    scope.define(symbol) # only define in namespace scopes, not enclosingScope
                scope = symbol
        else:
            symbol = scope

        for v in self.includes:
            symbol.define(v.toAbstractSymbol())

        allValues = list(self.functions.values()) + list(self.constants.values()) + \
                    list(self.interfaces.values()) + list(self.classes.values())
        for v in sortByLine(allValues):
            symbol.define(v.toAbstractSymbol(symbol))

        if not enclosingScope.resolveMember(self.name.split("\\")[0]):
            while symbol.enclosingScope is not enclosingScope:
                symbol = symbol.enclosingScope
        else:
            symbol = enclosingScope.resolveMember(self.name.split("\\")[0])

        return symbol

class PHPFile:
    """CIX specifies that a <file> tag have zero or more
    <scope ilk="blob"> children.  In PHP this is a one-to-one
    relationship, so this class represents both (and emits the XML tags
    for both).
    """
    def __init__(self, filename):
        self.filename = filename
        self.error = None

        self.functions = {} # functions declared in file
        self.classes = {} # classes declared in file
        self.variables = {} # all variables used in file
        self.constants = {} # all constants used in file
        self.includes = [] # imported files/namespaces
        self.interfaces = {} # interfaces declared in file
        self.namespaces = {} # namespaces declared in file

    def __repr__(self):
        # dump our contents to human readable form
        r = "FILE %s\n" % self.filename

        for v in self.includes:
            r += "    %r" % v

        r += "constants:\n"
        for v in list(self.constants.values()):
            r += "    %r" % v

        r += "interfaces:\n"
        for v in list(self.interfaces.values()):
            r += "    %r" % v

        r += "functions:\n"
        for f in list(self.functions.values()):
            r += "    %r" % f

        r += "variables:\n"
        for v in list(self.variables.values()):
            r += "    %r" % v

        r += "classes:\n"
        for c in list(self.classes.values()):
            r += repr(c)

        r += "namespaces:\n"
        for v in list(self.namespaces.values()):
            r += "    %r" % v

        return r + '\n'

    def convertToElementTreeModule(self, cixmodule):
        for v in self.includes:
            v.toElementTree(cixmodule)

        allValues = list(self.constants.values()) + list(self.functions.values()) + \
                    list(self.interfaces.values()) + list(self.variables.values()) + \
                    list(self.classes.values()) + list(self.namespaces.values())
        for v in sortByLine(allValues):
            v.toElementTree(cixmodule)

    def convertToElementTreeFile(self, cix):
        if sys.platform.startswith("win"):
            path = self.filename.replace('\\', '/')
        else:
            path = self.filename
        cixfile = createCixFile(cix, path, lang="PHP", mtime=str(self.mtime))
        if self.error:
            cixfile.attrib["error"] = self.error
        cixmodule = createCixModule(cixfile, os.path.basename(self.filename),
                                    "PHP")
        self.convertToElementTreeModule(cixmodule)

    def toAbstractScope(self, enclosingScope=None):
        scope = enclosingScope or Scope()
        for v in self.includes:
            scope.define(v.toAbstractSymbol())

        allValues = list(self.constants.values()) + list(self.functions.values()) + \
                    list(self.interfaces.values()) + list(self.variables.values()) + \
                    list(self.classes.values()) + list(self.namespaces.values())
        for v in sortByLine(allValues):
            scope.define(v.toAbstractSymbol(scope))
        return scope

class PHPcile:
    def __init__(self):
        # filesparsed contains all files parsed
        self.filesparsed={}

    def clear(self, filename):
        # clear include links from the cache
        if filename not in self.filesparsed:
            return
        del self.filesparsed[filename]

    def __repr__(self):
        r = ''
        for f in self.filesparsed:
            r += repr(self.filesparsed[f])
        return r + '\n'

    #def toElementTree(self, cix):
    #    for f in self.filesparsed.values():
    #        f.toElementTree(cix)

    def convertToElementTreeModule(self, cixmodule):
        for f in list(self.filesparsed.values()):
            f.convertToElementTreeModule(cixmodule)

    def convertToElementTreeFile(self, cix):
        for f in list(self.filesparsed.values()):
            f.convertToElementTreeFile(cix)

    def toAbstractScope(self, enclosingScope=None):
        scope = Scope(enclosingScope)
        for f in list(self.filesparsed.values()):
            scope.merge(f.toAbstractScope(scope))
        return scope

class PHPScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "PHP"

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions(), but
        auto-imports all appropriate symbols into the current scope first, and
        then ultimately ignores sub-language namespaces.
        """
        if self.import_resolver:
            scope = self.scope
            while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                scope = scope.enclosingScope
            dirnames = self.import_resolver.env.get("PHPLIB", "").split(os.pathsep)
            dirnames = [_f for _f in dirnames if _f]
            name = None
            if self.name_part:
                name = self.name_part+"*"
            for symbol in fetchSymbolsInDirectories(dirnames, name, ext=".php"):
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
                        scope.define(symbol)
                    else:
                        resolved.merge(symbol)
        completions = super(PHPScopeCompletionContext, self).getCompletions()
        if isinstance(completions.members.get("HTML"), AbstractNamespace):
            del completions.members["HTML"]
        if isinstance(completions.members.get("CSS"), AbstractNamespace):
            del completions.members["CSS"]
        if isinstance(completions.members.get("JavaScript"), AbstractNamespace):
            del completions.members["JavaScript"]
        return completions

class PHPSymbolResolver(SymbolResolver):
    """PHP symbol resolver for fully-qualified symbol and type names that
    auto-imports all appropriate symbols into the current scope as needed.
    """
    def resolve(self, scope, symbol_name):
        symbol = super(PHPSymbolResolver, self).resolve(scope, symbol_name)
        if not symbol:
            for dirname in self._import_resolver.env.get("PHPLIB", "").split(os.pathsep):
                if not dirname:
                    continue
                for symbol in fetchSymbolsInDirectories(dirname, None, ext=".php"):
                    symbol._enclosingScope = scope # override
                    if not isinstance(scope.resolveMember(symbol.name), AbstractScope) or not isinstance(symbol, AbstractScope):
                        scope.define(symbol)
                    else:
                        scope.resolveMember(symbol.name).merge(symbol)
            symbol = super(PHPSymbolResolver, self).resolve(scope, symbol_name)
        return symbol

class PHPMemberCompletionContext(AbstractMemberCompletionContext):
    """Implementation of AbstractMemberCompletionContext."""
    @property
    def language(self):
        return "PHP"

    def getCompletions(self):
        """Implementation of AbstractCompletionContext.getCompletions(), but
        auto-imports all appropriate symbols into the current scope first.
        Additionally removes invalid member completions (e.g. "$this" and "self"
        for "$foo-><|>").
        """
        if self.import_resolver:
            scope = self.scope
            while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                scope = scope.enclosingScope
            dirnames = self.import_resolver.env.get("PHPLIB", "").split(os.pathsep)
            dirnames = [_f for _f in dirnames if _f]
            name = None
            if self.name_part:
                name = self.name_part+"*"
            for symbol in fetchSymbolsInDirectories(dirnames, name, ext=".php"):
                symbol._enclosingScope = scope # override
                resolve = scope.resolveMember(symbol.name)
                if not isinstance(resolve, AbstractScope) or not isinstance(symbol, AbstractScope):
                    scope.define(symbol)
                else:
                    resolve.merge(symbol)
        completions = super(PHPMemberCompletionContext, self).getCompletions()
        for name, symbol in list(completions.members.items()):
            if isinstance(symbol, AbstractVariable):
                if symbol.name in ("$this", "self", "parent") or symbol.name.startswith("$") and self.symbol_name not in ("self", "parent") and "::" not in self.symbol_name:
                    del completions.members[name]
                elif self.symbol_name in ("self", "parent") and isinstance(symbol, AbstractInstanceVariable):
                    del completions.members[name]
            elif isinstance(symbol, AbstractConstant) and self.symbol_name.startswith("$"):
                del completions.members[name]
        return completions

class PHPFindReferencesContext(AbstractFindReferencesContext):
    """Implementation of AbstractFindReferencesContext."""
    @property
    def projectFiles(self):
        """Implementation of AbstractFindReferencesContext.projectFiles."""
        filenames = []
        for dirname in self.env.get("PHPLIB", "").split(os.pathsep):
            if not dirname:
                continue
            for filename in fetchAllFilesInDirectory(dirname, ".php"):
                filenames.append(filename)
        return filenames

class PHPScanner(HTMLScanner):
    def __init__(self, html_stdlib_file, php_stdlib_file):
        super(PHPScanner, self).__init__(html_stdlib_file)
        self._sslScanner = PHPHTMLScanner(php_stdlib_file)
        setattr(self._sslScanner, "_udlScanner", self) # for PHPFindReferencesContext

    def addToBuiltInScope(self, file):
        """Overrides AbstractScanner.addToBuiltInScope() in order to add to
        PHP's built-in scope, not HTML's.
        """
        self._sslScanner.addToBuiltInScope(file)

    @property
    def udlLexer(self):
        """Implementation of AbstractUDLScanner.udlLexer."""
        return PHPLexer()

    @property
    def sslScanner(self):
        """Implementation of AbstractUDLScanner.tplScanner."""
        return self._sslScanner

    def scan(self, filename, env={}):
        """Implementation of AbstractScanner.scan().
        For compatibility with CodeIntel v2, PHP symbols should be in the
        top-level scope.
        Since HTMLScanner (and by extension AbstractUDLScanner) produce a scope
        like:
            enclosingScope (HTML5_STDLIB_FILE)
                scope (PHP file)
                    JavaScript (namespace)
                    CSS (namespace)
                    PHP (namespace) <-- enclosingScope (PHP*_STDLIB_FILE)
                        php symbols
        Create and return a new scope that looks like:
            enclosingScope (PHP*_STDLIB_FILE)
                scope (PHP file)
                    JavaScript (namespace)
                    CSS (namespace)
                    HTML (namespace) <-- enclosingScope (HTML5_STDLIB_FILE)
                    php symbols
        """
        scope = super(PHPScanner, self).scan(filename, env)
        php_scope = Scope(scope.members["PHP"].enclosingScope)
        for member in list(scope.members["PHP"].members.values()):
            member._enclosingScope = php_scope # override
            php_scope.define(member)
        php_scope.define(scope.members["JavaScript"])
        php_scope.define(scope.members["CSS"])
        html_scope = Namespace(self.namespace, scope.enclosingScope)
        for member in list(scope.members.values()):
            if not isinstance(member, AbstractNamespace):
                member._enclosingScope = html_scope # override
                html_scope.define(member)
        php_scope.define(html_scope)
        return php_scope

class PHPHTMLScanner(AbstractUDLSubScanner):
    PHP_COMMENT_STYLES = (SCE_UDL_SSL_COMMENT, SCE_UDL_SSL_COMMENTBLOCK)
    # lastText, lastStyle are use to remember the previous tokens.
    lastText = None
    lastStyle = None

    @property
    def namespace(self):
        """Implementation of AbstractUDLSubScanner.namespace."""
        return "PHP"

    def prepForUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.prepForUDLTokens()."""
        self.filename = ":N/A:" # no longer applicable
        self.cile = PHPcile()
        self.fileinfo = PHPFile(self.filename)

        # Working variables, used in conjunction with state
        self.classStack = []
        self.currentClass = None
        self.currentNamespace = None
        self.currentFunction = None
        self.csl_tokens = []
        self.css_tokens = []
        self.lineno = 0
        self.depth = 0
        self.styles = []
        self.linenos = []
        self.text = []
        self.comment = None
        self.comments = []
        self.heredocMarker = None
        self._anonid = 0

        # state : used to store the current JS lexing state
        # return_to_state : used to store JS state to return to
        # multilang_state : used to store the current UDL lexing state
        self.state = S_DEFAULT
        self.return_to_state = S_DEFAULT
        self.multilang_state = S_DEFAULT

        self.PHP_WORD        = SCE_UDL_SSL_WORD
        self.PHP_IDENTIFIER  = SCE_UDL_SSL_IDENTIFIER
        self.PHP_VARIABLE    = SCE_UDL_SSL_VARIABLE
        self.PHP_OPERATOR    = SCE_UDL_SSL_OPERATOR
        self.PHP_STRINGS     = (SCE_UDL_SSL_STRING,)
        self.PHP_NUMBER      = SCE_UDL_SSL_NUMBER

        # XXX bug 44775
        # having the next line after scanData below causes a crash on osx
        # in python's UCS2 to UTF8.  leaving this here for later
        # investigation, see bug 45362 for details.
        self.cile.filesparsed[self.filename] = self.fileinfo

        self.tokens = [] # track for use by getCompletionContext()

    def handleUDLToken(self, **kwargs):
        """Implementation of AbstractUDLSubScanner.handleUDLToken()."""
        self.token_next(**kwargs)

    def doneWithUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.doneWithUDLTokens()."""
        return self.cile.toAbstractScope(self.builtInScope)

    def getUDLCompletionContext(self, filename, position, env, _tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCompletionContext()."""
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if token["start_line"] + 1 <= line: # UDL produces 0-based lines
                if token["start_line"] + 1 < line: # UDL produces 0-based lines
                    # Keep all non-whitespace tokens prior to the current line.
                    if token["style"] != SCE_UDL_SSL_DEFAULT:
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if token["style"] != SCE_UDL_SSL_DEFAULT or (token["start_column"] + 1 <= column and column <= token["start_column"] + len(token["text"])):
                        tokens.append(token)
                        if token["style"] == SCE_UDL_SSL_OPERATOR and column <= token["start_column"] + len(token["text"]) - 1 and token["start_column"] != token["start_column"] + len(token["text"]):
                            # The tokenizer generally groups all operator
                            # characters into a single token. Chop off any bits
                            # that occur after the current position. Otherwise,
                            # something like "(<|>)" will be considered to be a
                            # "()" token instead of '('.
                            token["text"] = token["text"][:-(token["start_column"] + len(token["text"]) - column)]
                elif token["start_column"] == column and token["style"] == SCE_UDL_SSL_IDENTIFIER:
                    # Keep an identifier token that starts at the current
                    # position.
                    if tokens[-1]["style"] == SCE_UDL_SSL_DEFAULT:
                        tokens.pop() # no more leading whitespace
                    tokens.append(token)
        leading_whitespace = False
        scope = scope.resolveScope(line)
        name_part = "" # the "already typed" part of a symbol completion
        # For unit tests, any relative paths in the "fake" PHPLIB should be
        # resolved at this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PHPLIB" in env:
                dirs = env["PHPLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PHPLIB"] = os.pathsep.join(dirs)
        import_resolver = PHPImportResolver(filename, env)

        # Pre-process the end of the token list.
        if tokens[-1]["style"] == SCE_UDL_SSL_DEFAULT:
            if tokens[-1]["text"].endswith("$"):
                # For whatever reason a lone '$' is lumped in with whitespace.
                # A '$' expression should provide variable completions.
                return PHPScopeCompletionContext(scope, name_part, AbstractVariable, import_resolver=import_resolver)
            # If the position is immediately after whitespace, keep track of
            # that since scope completions for "if <|>" are valid, while
            # scope completions for "if<|>" are not.
            leading_whitespace = True
            tokens.pop()
            if len(tokens) == 0 or tokens[-1]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-1]["text"] in ("<?php", "<?"):
                return PHPScopeCompletionContext(scope, import_resolver=import_resolver)
        elif tokens[-1]["style"] in (SCE_UDL_SSL_IDENTIFIER, SCE_UDL_SSL_VARIABLE, SCE_UDL_SSL_WORD):
            # If the position is within a symbol name or keyword, keep track of
            # this name part.
            name_part = tokens[-1]["text"]
            if tokens[-1]["style"] == SCE_UDL_SSL_VARIABLE:
                if len(tokens) > 1 and tokens[-2]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-2]["text"] == "::":
                    # A "Foo::$bar"-type of expression should not provide
                    # completions.
                    return None
                # A "$foo"-type of expression should provide variable
                # completions.
                return PHPScopeCompletionContext(scope, name_part, AbstractVariable, import_resolver=import_resolver)
            tokens.pop()
        elif tokens[-1]["style"] == SCE_UDL_SSL_STRING:
            if (len(tokens) > 1 and tokens[-2]["text"] in ("require", "include")) or (len(tokens) > 2 and tokens[-2]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-2]["text"] == "(" and tokens[-3]["text"] in ("require", "include")):
                # If the position is within the string preceded by "require",
                # "include", "require(", or "include(", it is part of a
                # "require 'foo"- or "include 'foo"-type of expression.
                dirname = os.path.exists(filename) and os.path.dirname(filename) or ""
                if "/" in tokens[-1]["text"]:
                    if column < tokens[-1]["start_column"] + len(tokens[-1]["text"]):
                        # It is possible the position comes before the last
                        # member (e.g. "<|>foo/bar" or "foo/<|>bar/baz"). If so,
                        # strip out the trailing members.
                        i = tokens[-1]["text"].find("/", column - tokens[-1]["start_column"])
                        if i != -1:
                            tokens[-1]["text"] = tokens[-1]["text"][:i]
                    tokens[-1]["text"] = tokens[-1]["text"].strip("'\"")
                    symbol_name = "/".join(tokens[-1]["text"].split("/")[:-1])
                    dirname = os.path.join(dirname, symbol_name)
                    name_part = tokens[-1]["text"].split("/")[-1]
                else:
                    name_part = tokens[-1]["text"].strip("'\"")
                # A "require 'foo"-type of expression should provide scope
                # completions and a "require 'foo/bar"-type of expression should
                # provide member completions.
                if "/" not in tokens[-1]["text"]:
                    scope.define(Import("*", dirname)) # for import resolver
                    return PHPScopeCompletionContext(scope, name_part, AbstractModule, import_resolver=import_resolver)
                else:
                    scope.define(Import("%s/*" % symbol_name, dirname)) # for import resolver
                    return PHPMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, syntax_description=FilenameSyntaxDescription)
            elif len(tokens) > 1 and tokens[-2]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-2]["text"] == "[":
                # If the position is within a string preceded by "[", it may be
                # part of a "foo['bar"-type of expression. Keep track of the
                # string key, and replace the "[" token with a "::". That way a
                # PHPMemberCompletionContext for "foo::bar" will be recognized
                # later.
                name_part = tokens[-1]["text"].strip("'\"")
                tokens[-2]["text"] = "::"
                tokens.pop() # string

        # Now look back through the token list and provide an appropriate
        # completion context.
        if tokens[-1]["style"] == SCE_UDL_SSL_OPERATOR and (tokens[-1]["text"] in ("->", "::", "\\") or tokens[-1]["text"].endswith("->")):
            # If the first significant token behind the position is a "->",
            # "::", or '\' it's probably part of a larger "foo->bar"-,
            # "foo::bar"-, or "foo\bar"-type of expression.
            i = len(tokens) - 1
            while i >= 1:
                # Skip back through "name->" token pairs in order to construct
                # the full symbol name.
                if tokens[i]["style"] != SCE_UDL_SSL_OPERATOR or tokens[i]["text"] not in ("->", "::", "\\") or (tokens[i - 1]["style"] not in (SCE_UDL_SSL_IDENTIFIER, SCE_UDL_SSL_VARIABLE) and not (tokens[i - 1]["style"] == SCE_UDL_SSL_WORD and tokens[i - 1]["text"] == "parent")):
                    # While the expression is not a "foo->bar"-type of
                    # expression, it may be a "foo->bar()->baz"-type of
                    # expression (with a method call). Check for this.
                    if tokens[i]["style"] == SCE_UDL_SSL_OPERATOR and tokens[i]["text"].endswith("->"):
                        # The PHP tokenizer likes to group multiple operators
                        # into a single token. If the token ends with "->",
                        # strip it in order to propertly check if there is a
                        # method call immediately behind it.
                        tokens[i]["text"] = tokens[i]["text"][:-2]
                    if tokens[i]["style"] == SCE_UDL_SSL_OPERATOR and tokens[i]["text"] == "()" and tokens[i- 1]["style"] in (SCE_UDL_SSL_IDENTIFIER, SCE_UDL_SSL_VARIABLE):
                        # A "foo->bar()->baz"-type of expression should
                        # ultimately provide member completions for
                        # "foo->bar()", so append "()".
                        tokens[i - 1]["text"] += "()"
                    elif tokens[i]["style"] == SCE_UDL_SSL_OPERATOR and tokens[i]["text"] == ")":
                        # A "foo->bar(baz)->quux"-type of expression should
                        # ultimately provide member completions for
                        # "foo->bar()", so iterate back through token list and
                        # look for the matching '(' in order to continue
                        # constructing the full symbol name.
                        j = i - 1
                        count = 1
                        while j >= 1:
                            if tokens[j]["style"] == SCE_UDL_SSL_OPERATOR and tokens[j]["text"] == "(":
                                count -= 1
                                if count == 0:
                                    break # found matching '('; done
                            elif tokens[j]["style"] == SCE_UDL_SSL_OPERATOR and tokens[j]["text"] == ")":
                                count += 1
                            j -= 1
                        if tokens[j]["style"] == SCE_UDL_SSL_OPERATOR and tokens[j]["text"] == "(" and tokens[j - 1]["style"] in (SCE_UDL_SSL_IDENTIFIER, SCE_UDL_SSL_VARIABLE):
                            # A "foo->bar(baz)->quux"-type of expression should
                            # ultimately provide member completions for
                            # "foo->bar()", so append "()".
                            tokens[j - 1]["text"] += "()"
                            # Due to how the full symbol name is constructed,
                            # delete the method's argument list and matching '('
                            # from the token list.
                            del tokens[j:i]
                            i = j
                    else:
                        break
                i -= 2
            symbol_name = "".join([token["text"] for token in tokens[i+1:-1]])
            if not symbol_name:
                if tokens[-1]["text"] == "\\":
                    # A "\foo"-type of expression should provide global scope
                    # completions.
                    while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                        scope = scope.enclosingScope
                    return PHPScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
            elif tokens[-1]["text"] == "\\" or "\\" in symbol_name:
                if tokens[i]["style"] == SCE_UDL_SSL_WORD and tokens[i]["text"] == "namespace":
                    # A "namespace foo\bar"-type of expression should not
                    # provide completions.
                    return None
                elif "\\" in symbol_name and not (tokens[i]["style"] == SCE_UDL_SSL_WORD and tokens[i]["text"] == "use"):
                    if tokens[i]["style"] != SCE_UDL_SSL_OPERATOR or tokens[i]["text"] != "\\":
                        # The expression is a "foo\bar"- or "foo\bar::baz"-type
                        # of expression. Determine the current namespace.
                        while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                            if isinstance(scope, AbstractNamespace):
                                symbol_name = "%s\\%s" % (scope.name, symbol_name)
                                if tokens[-1]["text"] != "\\":
                                    # The full expression is a
                                    # "foo\bar\baz::quux"-type of expression.
                                    # The "baz" part is not a namespace member,
                                    # so the symbol name should look like
                                    # "foo\bar::baz::quux".
                                    name_parts = symbol_name.split("\\")
                                    symbol_name = "%s%s%s" % ("\\".join(name_parts[:-1]), tokens[-1]["text"], name_parts[-1])
                                # A "foo\bar"- or "foo\bar::baz"-type of
                                # expression should provide member completions
                                # in the current namespace.
                                return PHPMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, symbol_resolver_class=PHPSymbolResolver)
                            scope = scope.enclosingScope
                        # A "foo\bar"-type of expression without a current namespace
                        # should not provide completions.
                        return None
                    elif tokens[-1]["text"] != "\\":
                        # The expression is a "\foo\bar\baz::quux"-type of
                        # expression. The "baz" part is not a namespace member,
                        # so the symbol name should look like
                        # "\foo\bar::baz::quux".
                        name_parts = symbol_name.split("\\")
                        symbol_name = "%s%s%s" % ("\\".join(name_parts[:-1]), tokens[-1]["text"], name_parts[-1])
            if i > 0 and tokens[i - 1]["style"] == SCE_UDL_SSL_WORD and tokens[i - 1]["text"] == "static":
                if i + 1 < len(tokens):
                    # TODO: A "static::foo::?"-type of expression should ?
                    return PHPMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, symbol_resolver_class=PHPSymbolResolver)
                else:
                    # A "static::foo"-type of expression should provide static
                    # member completions.
                    return PHPScopeCompletionContext(scope, name_part, AbstractStaticMethod, import_resolver=import_resolver)
            #if (symbol_name == "this" or symbol_name.startswith("this->")) and isinstance(scope, AbstractMethod):
            #    # Define "$this" within an instance method.
            #    scope.define(InstanceVariable("this", scope.enclosingScope.name))
            #elif symbol_name in ("self", "parent") or symbol_name.startswith("self::") or symbol_name.startswith("parent::"):
            #    # Define "self" and "parent" in applicable class scopes.
            #    if isinstance(scope, AbstractClass):
            #        scope.define(InstanceVariable(symbol_name.split("::")[0], scope.name))
            #    elif isinstance(scope, AbstractFunction) and isinstance(scope.enclosingScope, AbstractClass):
            #        scope.define(InstanceVariable(symbol_name.split("::")[0], scope.enclosingScope.name))
            # A "foo->bar"-, "foo::bar"-, or "foo\bar"-type of expression should
            # provide member completions.
            # TODO: discern between InstanceVariables and ClassVariables.
            return PHPMemberCompletionContext(scope, symbol_name, name_part, import_resolver=import_resolver, symbol_resolver_class=PHPSymbolResolver)
        elif tokens[-1]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-1]["text"] == ",":
            # If the first significant token behind the position is a ',', it's
            # probably part of a typical expression, which should provide scope
            # completions. However, a "class Foo extends Bar, Baz"-, "class Foo
            # implements Bar, Baz"-, or "use Foo, Bar"-type of expression should
            # provide class, interface, or trait completions, respectively.
            if len(tokens) > 1:
                i = len(tokens) - 2
                while i >= 1:
                    if tokens[i]["style"] == SCE_UDL_SSL_WORD and tokens[i]["text"] == "extends":
                        return PHPScopeCompletionContext(scope, name_part, AbstractClass, import_resolver=import_resolver)
                    elif tokens[i]["style"] == SCE_UDL_SSL_WORD and tokens[i]["text"] == "implements":
                        return PHPScopeCompletionContext(scope, name_part, AbstractInterface, import_resolver=import_resolver)
                    elif tokens[i]["style"] == SCE_UDL_SSL_WORD and tokens[i]["text"] == "use":
                        return PHPScopeCompletionContext(scope, name_part, AbstractTrait, import_resolver=import_resolver)
                    elif tokens[i]["style"] != SCE_UDL_SSL_IDENTIFIER and (tokens[i]["style"] != SCE_UDL_SSL_OPERATOR or tokens[i]["text"] != ","):
                        # Unexpected token encountered. The original ',' token
                        # is probably part of a typical expression.
                        break
                    i -= 1
            return PHPScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif leading_whitespace or name_part:
            if tokens[-1]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-1]["text"] == ".":
                # A "foo.bar"-type of expression should not provide completions.
                return None
            elif tokens[-1]["style"] == SCE_UDL_SSL_WORD and tokens[-1]["text"] in ("new", "extends"):
                # A "new Foo"- or "class Foo extends Bar"-type of expression
                # should provide class completions.
                return PHPScopeCompletionContext(scope, name_part, AbstractClass, import_resolver=import_resolver)
            elif tokens[-1]["style"] == SCE_UDL_SSL_WORD and tokens[-1]["text"] == "implements":
                # A "class Foo implements Bar"-type of expression should provide
                # interface completions.
                return PHPScopeCompletionContext(scope, name_part, AbstractInterface, import_resolver=import_resolver)
            elif tokens[-1]["style"] == SCE_UDL_SSL_WORD and tokens[-1]["text"] in ("function", "class"):
                # A "function foo"- or "class Foo"-type of expression should not
                # provide completions.
                return None
            elif tokens[-1]["style"] == SCE_UDL_SSL_WORD and tokens[-1]["text"] == "use":
                if not isinstance(scope, AbstractClass):
                    # A "use foo"-type of expression should provide namespace or
                    # class completions.
                    return PHPScopeCompletionContext(scope, name_part, (AbstractNamespace, AbstractClass), import_resolver=import_resolver)
                else:
                    # A "use foo"-type of expression within a class should
                    # provide trait completions.
                    return PHPScopeCompletionContext(scope, name_part, AbstractTrait, import_resolver=import_resolver)
            elif tokens[-1]["style"] == SCE_UDL_SSL_IDENTIFIER:
                # A "foo bar"-type of expression should not provide completions.
                return None
            elif tokens[-1]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-1]["text"] == "@":
                # A "@foo"-type of expression should provide function
                # completions.
                return PHPScopeCompletionContext(scope, name_part, AbstractFunction, import_resolver=import_resolver)
            elif not leading_whitespace and tokens[-1]["text"] == "<?":
                # A "<?foo"-type of expression should not provide completions.
                # TODO: "<? foo"-type of expression should. `tokens` does not
                # contain the whitespace tokens needed in order to differentiate
                # between "<?foo" and "<? foo".
                return None
            # If there is no significant token immediately behind the
            # position, it's an individual part of a typical expression.
            return PHPScopeCompletionContext(scope, name_part, import_resolver=import_resolver)
        elif tokens[-1]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-1]["text"] == "(":
            # If the first significant token behind the position is an operator,
            # it's an individual part of a typical expression.
            return PHPScopeCompletionContext(scope, name_part, import_resolver=import_resolver)

        return None

    def getUDLGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLGotoDefinitionContext()."""
        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in the "fake" PHPLIB should be
        # resolved at this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PHPLIB" in env:
                dirs = env["PHPLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PHPLIB"] = os.pathsep.join(dirs)
        import_resolver = PHPImportResolver(filename, env)

        for i in range(len(tokens)):
            token = tokens[i]
            if token["start_line"] + 1 <= line and line <= token["start_line"] + 1 + token["text"].count("\n") and token["start_column"] <= column and column <= token["start_column"] + len(token["text"]) - 1: # UDL produces 0-based lines
                if token["style"] in (SCE_UDL_SSL_IDENTIFIER, SCE_UDL_SSL_VARIABLE):
                    # If the entity at the position is an identifier or
                    # variable, retrieve the fully-qualified name up to and
                    # including the position.
                    j = i + 1
                    while i > 0:
                        if tokens[i - 1]["style"] == SCE_UDL_SSL_OPERATOR and tokens[i - 1]["text"] in ("->", "::"):
                            i -= 2
                        else:
                            break
                    symbol_name = "".join([token["text"] for token in tokens[i:j]])
                    return GotoDefinitionContext(scope, symbol_name, import_resolver=import_resolver, symbol_resolver_class=PHPSymbolResolver)
                elif token["style"] == SCE_UDL_SSL_DEFAULT and "\n" in token["text"]:
                    continue # straddling a newline; keep going
                break
            elif token["start_line"] + 1 > line: # UDL produces 0-based lines
                break

        return None

    def getUDLCallTipContext(self, filename, position, env, _tokens, line, column, scope):
        """Implementation of AbstractScanner.getCallTipContext()."""
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if token["start_line"] + 1 <= line: # UDL produces 0-based lines
                if token["start_line"] + 1 < line: # UDL produces 0-based lines
                    # Keep all non-whitespace tokens prior to the current line.
                    if token["style"] != SCE_UDL_SSL_DEFAULT:
                        tokens.append(token)
                elif token["start_column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position.
                    if token["style"] != SCE_UDL_SSL_DEFAULT:
                        tokens.append(token)
                        if token["style"] == SCE_UDL_SSL_OPERATOR and column <= token["start_column"] + len(token["text"]) - 1 and token["start_column"] != token["start_column"] + len(token["text"]):
                            # The tokenizer generally groups all operator
                            # characters into a single token. Chop off any
                            # bits that occur after the current position.
                            # Otherwise, something like "(<|>)" will be
                            # considered to be a "()" token instead of '('.
                            token["text"] = token["text"][:-(token["start_column"] + len(token["text"]) - column)]

        # Now look back through the token list for a function call and provide
        # its call tip context.
        i = len(tokens) - 1
        paren_level = -1
        while i > 0:
            if tokens[i]["style"] == SCE_UDL_SSL_OPERATOR:
                paren_level -= tokens[i]["text"].count(")")
                paren_level += tokens[i]["text"].count("(")
                if paren_level == 0:
                    i -= 1
                    break
            i -= 1
        if paren_level != 0 or tokens[i]["style"] not in (SCE_UDL_SSL_WORD, SCE_UDL_SSL_IDENTIFIER):
            return None # no function call = no call tip

        # Retrieve the fully-qualified function name.
        j = i + 1
        while i > 0:
            if tokens[i - 1]["style"] == SCE_UDL_SSL_OPERATOR and tokens[i - 1]["text"] in ("->", "::"):
                i -= 2
            else:
                break
        if tokens[i]["style"] == SCE_UDL_SSL_WORD and tokens[i]["text"] in ("if", "elseif", "for", "foreach", "while"):
            return None # no call tip for keywords
        elif i > 0 and tokens[i - 1]["style"] == SCE_UDL_SSL_WORD and tokens[i - 1]["text"] == "function":
            return None # no call tip for function declaration
        elif tokens[i]["style"] == SCE_UDL_SSL_WORD and tokens[i]["text"] == "static" and i + 2 < j:
            i += 2 # ignore 'static::' prefix
        symbol_name = "".join([token["text"] for token in tokens[i:j]])

        scope = scope.resolveScope(line)
        # For unit tests, any relative paths in the "fake" PHPLIB should be
        # resolved at this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PHPLIB" in env:
                dirs = env["PHPLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PHPLIB"] = os.pathsep.join(dirs)
        import_resolver = PHPImportResolver(filename, env)
        return CallTipContext(scope, symbol_name, import_resolver=import_resolver, symbol_resolver_class=PHPSymbolResolver)

    def getUDLFindReferencesContext(self, filename, position, env, _tokens, line, column, scope):
        """Implementation of AbstractScanner.getFindReferencesContext()."""
        context = self.getUDLGotoDefinitionContext(filename, position, env, _tokens, line, column, scope)
        if not context:
            return None
        # For unit tests, any relative paths in the "fake" PHPLIB should be
        # resolved at this point. (Make a copy first.)
        if os.path.exists(filename):
            env = env.copy()
            if "PHPLIB" in env:
                dirs = env["PHPLIB"].split(os.pathsep)
                for i in range(len(dirs)):
                    if dirs[i].startswith("."):
                        dirs[i] = os.path.normpath(os.path.dirname(filename) + os.path.sep + dirs[i])
                env["PHPLIB"] = os.pathsep.join(dirs)
        import_resolver = PHPImportResolver(filename, env)
        return PHPFindReferencesContext(context.scope, context.symbol_name, getattr(self, "_udlScanner"), env, import_resolver=import_resolver, symbol_resolver_class=PHPSymbolResolver)

    # parses included files
    def include_file(self, filename):
        # XXX Very simple prevention of include looping.  Really should
        # recurse the indices to make sure we are not creating a loop
        if self.filename == filename:
            return ""

        # add the included file to our list of included files
        self.fileinfo.includes.append(PHPImport(filename, self.lineno))

    def incBlock(self):
        self.depth = self.depth+1
        # log.debug("depth at %d", self.depth)

    def decBlock(self):
        self.depth = self.depth-1
        # log.debug("depth at %d", self.depth)
        if self.currentClass and self.currentClass.depth == self.depth:
            # log.debug("done with class %s at depth %d", self.currentClass.name, self.depth)
            self.currentClass.lineend = self.lineno
            log.debug("done with %s %s at depth %r",
                      isinstance(self.currentClass, PHPInterface) and "interface" or "class",
                      self.currentClass.name, self.depth)
            self.currentClass = self.classStack.pop()
        if self.currentNamespace and self.currentNamespace.depth == self.depth:
            log.debug("done with namespace %s at depth %r", self.currentNamespace.name, self.depth)
            self.currentNamespace.lineend = self.lineno
            self.currentNamespace = None
        elif self.currentFunction and self.currentFunction.depth == self.depth:
            self.currentFunction.lineend = self.lineno
            # XXX stacked functions used to work in php, need verify still is
            self.currentFunction = None

    def addFunction(self, name, phpArgs=None, attributes=None, doc=None,
                    returnByRef=False):
        log.debug("FUNC: %s(%r) on line %d", name, phpArgs, self.lineno)
        classname = ''
        extendsName = ''
        if self.currentClass:
            classname = self.currentClass.name
            extendsName = self.currentClass.extends
        self.currentFunction = PHPFunction(name,
                                           phpArgs,
                                           self.lineno,
                                           self.depth,
                                           attributes=attributes,
                                           doc=doc,
                                           classname=classname,
                                           classparent=extendsName,
                                           returnByRef=returnByRef)
        if self.currentClass:
            self.currentClass.functions[self.currentFunction.name] = self.currentFunction
        elif self.currentNamespace:
            self.currentNamespace.functions[self.currentFunction.name] = self.currentFunction
        else:
            self.fileinfo.functions[self.currentFunction.name] = self.currentFunction
        if isinstance(self.currentClass, PHPInterface) or self.currentFunction.attributes.find('abstract') >= 0:
            self.currentFunction.lineend = self.lineno
            self.currentFunction = None

    def addReturnType(self, typeName):
        if self.currentFunction:
            log.debug("RETURN TYPE: %r on line %d", typeName, self.lineno)
            self.currentFunction.addReturnType(typeName)
        else:
            log.debug("addReturnType: No current function for return value!?")

    def addClass(self, name, extends=None, attributes=None, interfaces=None, doc=None, isTrait=False):
        toScope = self.currentNamespace or self.fileinfo
        if name not in toScope.classes:
            # push the current class onto the class stack
            self.classStack.append(self.currentClass)
            # make this class the current class
            cixClass = isTrait and PHPTrait or PHPClass
            self.currentClass = cixClass(name,
                                         extends,
                                         self.lineno,
                                         self.depth,
                                         attributes,
                                         interfaces,
                                         doc=doc)
            toScope.classes[self.currentClass.name] = self.currentClass
            log.debug("%s: %s extends %s interfaces %s attributes %s on line %d in %s at depth %d\nDOCS: %s",
                     self.currentClass.cixtype,
                     self.currentClass.name, self.currentClass.extends,
                     self.currentClass.interfaces, self.currentClass.attributes,
                     self.currentClass.linestart, self.filename, self.depth,
                     self.currentClass.doc)
        else:
            # shouldn't ever get here
            pass

    def addClassMember(self, name, vartype, attributes=None, doc=None, forceToClass=False):
        if self.currentFunction and not forceToClass:
            if name not in self.currentFunction.variables:
                phpVariable = self.currentClass.members.get(name)
                if phpVariable is None:
                    log.debug("Class FUNC variable: %r", name)
                    self.currentFunction.variables[name] = PHPVariable(name,
                                                                       self.lineno,
                                                                       vartype,
                                                                       doc=doc)
                elif vartype:
                    log.debug("Adding type information for VAR: %r, vartype: %r",
                              name, vartype)
                    phpVariable.addType(self.lineno, vartype)
        elif self.currentClass:
            phpVariable = self.currentClass.members.get(name)
            if phpVariable is None:
                log.debug("CLASSMBR: %r", name)
                self.currentClass.members[name] = PHPVariable(name, self.lineno,
                                                              vartype,
                                                              attributes,
                                                              doc=doc)
            elif vartype:
                log.debug("Adding type information for CLASSMBR: %r, vartype: %r",
                          name, vartype)
                phpVariable.addType(self.lineno, vartype)

    def addClassConstant(self, name, vartype, doc=None):
        """Add a constant variable into the current class."""
        if self.currentClass:
            phpConstant = self.currentClass.constants.get(name)
            if phpConstant is None:
                log.debug("CLASS CONST: %r", name)
                self.currentClass.constants[name] = PHPConstant(name, self.lineno,
                                                              vartype)
            elif vartype:
                log.debug("Adding type information for CLASS CONST: %r, "
                          "vartype: %r", name, vartype)
                phpConstant.addType(self.lineno, vartype)

    def addInterface(self, name, extends=None, doc=None):
        toScope = self.currentNamespace or self.fileinfo
        if name not in toScope.interfaces:
            # push the current interface onto the class stack
            self.classStack.append(self.currentClass)
            # make this interface the current interface
            self.currentClass = PHPInterface(name, extends, self.lineno, self.depth)
            toScope.interfaces[name] = self.currentClass
            log.debug("INTERFACE: %s extends %s on line %d, depth %d",
                      name, extends, self.lineno, self.depth)
        else:
            # shouldn't ever get here
            pass

    def setNamespace(self, namelist, usesBracketedStyle, doc=None):
        """Create and set as the current namespace."""
        if self.currentNamespace:
            # End the current namespace before starting the next.
            self.currentNamespace.lineend = self.lineno -1

        if not namelist:
            # This means to use the global namespace, i.e.:
            #   namespace { // global code }
            #   http://ca3.php.net/manual/en/language.namespaces.definitionmultiple.php
            self.currentNamespace = None
        else:
            depth = self.depth
            if not usesBracketedStyle:
                # If the namespacing does not uses brackets, then there is no
                # good way to find out when the namespace end, we can only
                # guarentee that the namespace ends if another namespace starts.
                # Using None as the depth will ensure these semantics hold.
                depth = None
            namespace_path = qualifyNamespacePath("\\".join(namelist))
            namespace = self.fileinfo.namespaces.get(namespace_path)
            if namespace is None:
                namespace = PHPNamespace(namespace_path, self.lineno, depth,
                                         doc=doc)
                self.fileinfo.namespaces[namespace_path] = namespace
            self.currentNamespace = namespace
            log.debug("NAMESPACE: %r on line %d in %s at depth %r",
                      namespace_path, self.lineno, self.filename, depth)

    def addNamespaceImport(self, namespace, alias, ilk=None):
        """Import the namespace."""
        namelist = namespace.split("\\")
        namespace_path = "\\".join(namelist[:-1])
        if namespace.startswith("\\") and not namespace_path.startswith("\\"):
            namespace_path = "\\%s" % (namespace_path, )
        symbol = namelist[-1]
        toScope = self.currentNamespace or self.fileinfo
        toScope.includes.append(PHPImport(namespace_path, self.lineno,
                                          alias=alias, symbol=symbol, ilk=ilk))
        log.debug("IMPORT NAMESPACE: %s\%s as %r on line %d",
                  namespace_path, symbol, alias, self.lineno)

    def addVariable(self, name, vartype='', attributes=None, doc=None,
                    fromPHPDoc=False):
        log.debug("VAR: %r type: %r on line %d", name, vartype, self.lineno)
        phpVariable = None
        already_existed = True
        if self.currentFunction:
            phpVariable = self.currentFunction.variables.get(name)
            # Also ensure the variable is not a function argument.
            if phpVariable is None and \
               not self.currentFunction.hasArgumentWithName(name):
                phpVariable = PHPVariable(name, self.lineno, vartype,
                                          attributes, doc=doc,
                                          fromPHPDoc=fromPHPDoc)
                self.currentFunction.variables[name] = phpVariable
                already_existed = False
        elif self.currentClass and \
             not (self.currentClass.name.startswith('(anonymous ') and \
                  self.currentClass.name == vartype):
            pass
            # XXX this variable is local to a class method, what to do with it?
            #if m.group('name') not in self.currentClass.variables:
            #    self.currentClass.variables[m.group('name')] =\
            #        PHPVariable(m.group('name'), self.lineno)
        else:
            # Variables cannot get defined in a namespace, so if it's not a
            # function or a class, then it goes into the global scope.
            phpVariable = self.fileinfo.variables.get(name)
            if phpVariable is None:
                phpVariable = PHPVariable(name, self.lineno, vartype,
                                          attributes, doc=doc,
                                          fromPHPDoc=fromPHPDoc,
                                          namespace=self.currentNamespace)
                self.fileinfo.variables[name] = phpVariable
                already_existed = False

        if phpVariable and already_existed:
            if doc:
                if phpVariable.doc:
                    phpVariable.doc += doc
                else:
                    phpVariable.doc = doc
            if vartype:
                log.debug("Adding type information for VAR: %r, vartype: %r",
                          name, vartype)
                phpVariable.addType(self.lineno, vartype, fromPHPDoc=fromPHPDoc)
        return phpVariable

    def addConstant(self, name, vartype='', doc=None):
        """Add a constant at the global or namelisted scope level."""

        log.debug("CONSTANT: %r type: %r on line %d", name, vartype, self.lineno)
        toScope = self.currentNamespace or self.fileinfo
        phpConstant = toScope.constants.get(name)
        # Add it if it's not already defined
        if phpConstant is None:
            if vartype and isinstance(vartype, (list, tuple)):
                vartype = ".".join(vartype)
            toScope.constants[name] = PHPConstant(name, self.lineno, vartype)

    def addDefine(self, name, vartype='', doc=None):
        """Add a define at the global or namelisted scope level."""

        log.debug("DEFINE: %r type: %r on line %d", name, vartype, self.lineno)
        # Defines always go into the global scope unless explicitly defined
        # with a namespace:
        #   http://ca3.php.net/manual/en/language.namespaces.definition.php
        toScope = self.fileinfo
        namelist = name.split("\\")
        if len(namelist) > 1:
            namespace_path = "\\".join(namelist[:-1])
            namespace_path = qualifyNamespacePath(namespace_path)
            log.debug("defined in namespace: %r", namespace_path)
            namespace = toScope.namespaces.get(namespace_path)
            # Note: This does not change to the namespace, it just creates
            #       it when it does not already exist!
            if namespace is None:
                namespace = PHPNamespace(namespace_path, self.lineno,
                                         self.depth)
                self.fileinfo.namespaces[namespace_path] = namespace
            toScope = namespace
        const_name = namelist[-1]
        phpConstant = toScope.constants.get(const_name)
        # Add it if it's not already defined
        if phpConstant is None:
            if vartype and isinstance(vartype, (list, tuple)):
                vartype = ".".join(vartype)
            toScope.constants[const_name] = PHPConstant(const_name, self.lineno, vartype)

    def _parseOneArgument(self, styles, text):
        """Create a PHPArg object from the given text"""

        # Arguments can be of the form:
        #  foo($a, $b, $c)
        #  foo(&$a, &$b, &$c)
        #  foo($a, &$b, $c)
        #  foo($a = "123")
        #  makecoffee($types = array("cappuccino"), $coffeeMaker = NULL)
        # Arguments can be statically typed declarations too, bug 79003:
        #  foo(MyClass $a)
        #  foo(string $a = "123")
        #  foo(MyClass &$a)
        # References the inner class:
        #  static function bar($x=self::X)

        pos = 0
        name = None
        citdl = None
        default = None
        sig_parts = []
        log.debug("_parseOneArgument: text: %r", text)
        while pos < len(styles):
            sig_parts.append(text[pos])
            if name is None:
                if styles[pos] == self.PHP_VARIABLE:
                    name = self._removeDollarSymbolFromVariableName(text[pos])
                elif styles[pos] in (self.PHP_IDENTIFIER, self.PHP_WORD):
                    # Statically typed argument.
                    citdl = text[pos]
                    sig_parts.append(" ")
                elif text[pos] == '&':
                    sig_parts.append(" ")
            elif not citdl:
                if text[pos] == "=":
                    sig_parts[-1] = " = "
                    # It's an optional argument
                    default = "".join(text[pos+1:])
                    valueType, pos = self._getVariableType(styles, text, pos+1)
                    if valueType:
                        citdl = valueType[0]
                    break
            else:
                pos += 1
                break
            pos += 1
        sig_parts += text[pos:]
        if name is not None:
            return PHPArg(name, citdl=citdl, signature="".join(sig_parts),
                          default=default)

    def _getArgumentsFromPos(self, styles, text, pos):
        """Return a list of PHPArg objects"""

        p = pos
        log.debug("_getArgumentsFromPos: text: %r", text[p:])
        phpArgs = []
        if p < len(styles) and styles[p] == self.PHP_OPERATOR and text[p] == "(":
            p += 1
            paren_count = 0
            start_pos = p
            while p < len(styles):
                if styles[p] == self.PHP_OPERATOR:
                    if text[p] == "(":
                        paren_count += 1
                    elif text[p] == ")":
                        if paren_count <= 0:
                            # End of the arguments.
                            break
                        paren_count -= 1
                    elif text[p] == "," and paren_count == 0:
                        # End of the current argument.
                        phpArg = self._parseOneArgument(styles[start_pos:p],
                                                       text[start_pos:p])
                        if phpArg:
                            phpArgs.append(phpArg)
                        start_pos = p + 1
                p += 1
            if start_pos < p:
                phpArg = self._parseOneArgument(styles[start_pos:p],
                                               text[start_pos:p])
                if phpArg:
                    phpArgs.append(phpArg)
        return phpArgs, p

    def _getOneIdentifierFromPos(self, styles, text, pos, identifierStyle=None):
        if identifierStyle is None:
            identifierStyle = self.PHP_IDENTIFIER
        log.debug("_getIdentifiersFromPos: text: %r", text[pos:])
        start_pos = pos
        ids = []
        last_style = self.PHP_OPERATOR
        isNamespace = False
        while pos < len(styles):
            style = styles[pos]
            #print "Style: %d, Text[%d]: %r" % (style, pos, text[pos])
            if style == identifierStyle or \
               (style == self.PHP_WORD and text[pos] == 'string'): # only PHP 7 reserves "string"
                if last_style != self.PHP_OPERATOR:
                    break
                if isNamespace:
                    ids[-1] += text[pos]
                else:
                    ids.append(text[pos])
            elif style == self.PHP_OPERATOR:
                t = text[pos]
                isNamespace = False
                if t == "\\":
                    isNamespace = True
                    if ids:
                        ids[-1] += "\\"
                    else:
                        ids.append("\\")
                    if pos + 1 < len(styles) and text[pos + 1] == "{":
                        # Skip over "{" in grouped "use" statements.
                        # (Of the form: 'use function foo\{bar, baz};')
                        # It is handled separately.
                        pos += 1
                elif ((t != "&" or last_style != self.PHP_OPERATOR) and \
                      (t != ":" or last_style != identifierStyle)):
                    break
            else:
                break
            pos += 1
            last_style = style
        return ids, pos

    def _getIdentifiersFromPos(self, styles, text, pos, identifierStyle=None):
        typeNames, p = self._getOneIdentifierFromPos(styles, text, pos, identifierStyle)
        #if typeNames:
        #    typeNames[0] = self._removeDollarSymbolFromVariableName(typeNames[0])
        log.debug("typeNames: %r, p: %d, text left: %r", typeNames, p, text[p:])
        # Grab additional fields
        # Example: $x = $obj<p>->getFields()->field2
        while p+2 < len(styles) and styles[p] == self.PHP_OPERATOR and \
              text[p] in (":->\\"):
            isNamespace = False
            if text[p] == "\\":
                isNamespace = True
            p += 1
            log.debug("while:: p: %d, text left: %r", p, text[p:])
            if styles[p] == self.PHP_IDENTIFIER or \
               (styles[p] == self.PHP_VARIABLE and text[p-1] == ":"):
                additionalNames, p = self._getOneIdentifierFromPos(styles, text, p, styles[p])
                log.debug("p: %d, additionalNames: %r", p, additionalNames)
                if additionalNames:
                    if isNamespace:
                        if typeNames:
                            typeNames[-1] += "\\%s" % (additionalNames[0])
                        else:
                            typeNames.append("\\%s" % (additionalNames[0]))
                    else:
                        typeNames.append(additionalNames[0])
                    if p < len(styles) and \
                       styles[p] == self.PHP_OPERATOR and text[p][0] == "(":
                        typeNames[-1] += "()"
                        p = self._skipPastParenArguments(styles, text, p+1)
                        log.debug("_skipPastParenArguments:: p: %d, text left: %r", p, text[p:])
        return typeNames, p

    def _skipPastParenArguments(self, styles, text, p):
        paren_count = 1
        while p < len(styles):
            if styles[p] == self.PHP_OPERATOR:
                if text[p] == "(":
                    paren_count += 1
                elif text[p] == ")":
                    if paren_count == 1:
                        return p+1
                    paren_count -= 1
            p += 1
        return p

    _citdl_type_from_cast = {
        "int":       "int",
        "integer":   "int",
        "bool":      "boolean",
        "boolean":   "boolean",
        "float":     "int",
        "double":    "int",
        "real":      "int",
        "string":    "string",
        "binary":    "string",
        "array":     "array()",   # array(), see bug 32896.
        "object":    "object",
    }
    def _getVariableType(self, styles, text, p, assignmentChar="="):
        """Set assignmentChar to None to skip over looking for this char first"""

        log.debug("_getVariableType: text: %r", text[p:])
        typeNames = []
        if p+1 < len(styles) and (assignmentChar is None or \
                                  (styles[p] == self.PHP_OPERATOR and \
                                   text[p] == assignmentChar)):
            # Assignment to the variable
            if assignmentChar is not None:
                p += 1
                if p+1 >= len(styles):
                    return typeNames, p

            if styles[p] == self.PHP_OPERATOR and text[p] == '&':
                log.debug("_getVariableType: skipping over reference char '&'")
                p += 1
                if p+1 >= len(styles):
                    return typeNames, p

            elif p+3 <= len(styles) and styles[p] == self.PHP_OPERATOR and \
                 text[p+2] == ')' and text[p+1] in self._citdl_type_from_cast:
                # Looks like a casting:
                # http://ca.php.net/manual/en/language.types.type-juggling.php#language.types.typecasting
                #   $bar = (boolean) $foo;
                typeNames = [self._citdl_type_from_cast.get(text[p+1])]
                log.debug("_getVariableType: casted to type: %r", typeNames)
                p += 3
                return typeNames, p

            if styles[p] == self.PHP_WORD:
                # Keyword
                keyword = text[p].lower()
                p += 1
                if keyword == "new":
                    typeNames, p = self._getIdentifiersFromPos(styles, text, p)
                    if not typeNames and styles[p] == self.PHP_WORD and \
                       text[p] == "class":
                        # Anonymous classes: new in PHP 7.
                        p += 1
                        extends = self._getExtendsArgument(styles, text, p)
                        implements = self._getImplementsArgument(styles, text, p)
                        #print "extends: %r" % (extends)
                        #print "implements: %r" % (implements)
                        self._anonid += 1
                        self.addClass("(anonymous %d)" % self._anonid,
                                      extends=extends, attributes=[],
                                      interfaces=implements, doc=self.comment,
                                      isTrait=False)
                        typeNames = ["(anonymous %d)" % self._anonid]
                    #if not typeNames:
                    #    typeNames = ["object"]
                elif keyword in ("true", "false"):
                    typeNames = ["boolean"];
                elif keyword == "array":
                    typeNames = ["array()"];
                elif keyword == "clone":
                    # clone is a special method - bug 85534.
                    typeNames, p = self._getIdentifiersFromPos(styles, text, p,
                                            identifierStyle=self.PHP_VARIABLE)
            elif styles[p] in self.PHP_STRINGS:
                p += 1
                typeNames = ["string"]
            elif styles[p] == self.PHP_NUMBER:
                p += 1
                typeNames = ["int"]
            elif styles[p] == self.PHP_IDENTIFIER:
                # PHP Uses mixed upper/lower case for boolean values.
                if text[p].lower() in ("true", "false"):
                    p += 1
                    typeNames = ["boolean"]
                else:
                    typeNames, p = self._getIdentifiersFromPos(styles, text, p)
                    # Don't record null, as it doesn't help us with anything
                    if typeNames == ["NULL"]:
                        typeNames = []
                    elif typeNames and p < len(styles) and \
                       styles[p] == self.PHP_OPERATOR and text[p][0] == "(":
                        typeNames[-1] += "()"
            elif styles[p] == self.PHP_VARIABLE:
                typeNames, p = self._getIdentifiersFromPos(styles, text, p, self.PHP_VARIABLE)
            elif styles[p] == self.PHP_OPERATOR and text[p] == "\\":
                typeNames, p = self._getIdentifiersFromPos(styles, text, p, self.PHP_IDENTIFIER)

        return typeNames, p

    def _getKeywordArguments(self, styles, text, p, keywordName):
        arguments = None
        while p < len(styles):
            if styles[p] == self.PHP_WORD and text[p] == keywordName:
                # Grab the definition
                p += 1
                arguments = []
                last_style = self.PHP_OPERATOR
                namespaced = False
                while p < len(styles):
                    if styles[p] == self.PHP_IDENTIFIER and \
                       last_style == self.PHP_OPERATOR:
                        if namespaced:
                            arguments[-1] += text[p]
                            namespaced = False
                        else:
                            arguments.append(text[p])
                    elif styles[p] == self.PHP_OPERATOR and text[p] == "\\":
                        if not arguments or last_style != self.PHP_IDENTIFIER:
                            arguments.append(text[p])
                        else:
                            arguments[-1] += text[p]
                        namespaced = True
                    elif styles[p] != self.PHP_OPERATOR or text[p] != ",":
                        break
                    last_style = styles[p]
                    p += 1
                arguments = ", ".join(arguments)
                break
            p += 1
        return arguments

    def _getExtendsArgument(self, styles, text, p):
        return self._getKeywordArguments(styles, text, p, "extends")

    def _getImplementsArgument(self, styles, text, p):
        return self._getKeywordArguments(styles, text, p, "implements")

    def _unquoteString(self, s):
        """Return the string without quotes around it"""
        if len(s) >= 2 and s[0] in "\"'":
            return s[1:-1]
        return s

    def _removeDollarSymbolFromVariableName(self, name):
        if name[0] == "$":
            return name[1:]
        return name

    def _getIncludePath(self, styles, text, p):
        """Work out the include string and return it (without the quotes)"""

        # Some examples (include has identical syntax):
        #   require 'prepend.php';
        #   require $somefile;
        #   require ('somefile.txt');
        # From bug: http://bugs.activestate.com/show_bug.cgi?id=64208
        # We just find the first string and use that
        #   require_once(CEON_CORE_DIR . 'core/datatypes/class.CustomDT.php');
        # Skip over first brace if it exists
        if p < len(styles) and \
           styles[p] == self.PHP_OPERATOR and text[p] == "(":
            p += 1
        while p < len(styles):
            if styles[p] in self.PHP_STRINGS:
                requirename = self._unquoteString(text[p])
                if requirename:
                    # Return with the first string found, we could do better...
                    return requirename
            p += 1
        return None

    def _unescape_string(self, s):
        """Unescape a PHP string."""
        return s.replace("\\\\", "\\")

    def _getConstantNameAndType(self, styles, text, p):
        """Work out the constant name and type is, returns these as tuple"""

        # Some examples (include has identical syntax):
        #   define('prepend', 1);
        #   define ('somefile', "file.txt");
        #   define('\namespace\CONSTANT', True);
        #   define(__NAMESPACE__ . '\CONSTANT', True);
        constant_name = ""
        constant_type = None
        if styles[p] == self.PHP_OPERATOR and text[p] == "(":
            p += 1
        while p < len(styles):
            if styles[p] in self.PHP_STRINGS:
                constant_name += self._unquoteString(text[p])
            elif styles[p] == self.PHP_WORD and \
                 text[p] == "__NAMESPACE__" and self.currentNamespace:
                # __NAMESPACE__ is a special constant - we can expand this as we
                # know what the current namespace is.
                constant_name += self.currentNamespace.name
            elif text[p] == ",":
                constant_type, p = self._getVariableType(styles, text, p+1,
                                                         assignmentChar=None)
                break
            p += 1
        # We must ensure the name (which came from a PHP string is unescaped),
        # bug 90795.
        return self._unescape_string(constant_name), constant_type

    def _addAllVariables(self, styles, text, p):
        while p < len(styles):
            if styles[p] == self.PHP_VARIABLE:
                namelist, p = self._getIdentifiersFromPos(styles, text, p, self.PHP_VARIABLE)
                if len(namelist) == 1:
                    name = self._removeDollarSymbolFromVariableName(namelist[0])
                    # Don't add special internal variable names
                    if name in ("this", "self"):
                        # Lets see what we are doing with this
                        if p+3 < len(styles) and "".join(text[p:p+2]) in ("->", "::"):
                            # Get the variable the code is accessing
                            namelist, p = self._getIdentifiersFromPos(styles, text, p+2)
                            typeNames, p = self._getVariableType(styles, text, p)
                            if len(namelist) == 1 and typeNames:
                                log.debug("Assignment through %r for variable: %r", name, namelist)
                                self.addClassMember(namelist[0],
                                                    ".".join(typeNames),
                                                    doc=self.comment,
                                                    forceToClass=True)
                    elif name is not "parent":
                        # If next text/style is not an "=" operator, then add
                        # __not_defined__, which means the variable was not yet
                        # defined at the position it was ciled.
                        attributes = None
                        if p < len(styles) and text[p] != "=":
                            attributes = "__not_yet_defined__"
                        self.addVariable(name, attributes=attributes)
            p += 1

    def _handleVariableComment(self, namelist, comment):
        """Determine any necessary information from the provided comment.
        Returns true when the comment was used to apply variable info, false
        otherwise.
        """
        log.debug("_handleVariableComment:: namelist: %r, comment: %r",
                  namelist, comment)
        if "@var" in comment:
            doc = uncommentDocString(comment)
            # get the variable citdl type set by "@var"
            all_matches = re.findall(PHPVariable._re_var, doc)
            if len(all_matches) >= 1:
                #print all_matches[0]
                varname = all_matches[0][1]
                vartype = all_matches[0][2]
                php_variable = None
                if varname:
                    # Optional, defines the variable this is applied to.
                    php_variable = self.addVariable(varname, vartype,
                                                    doc=comment,
                                                    fromPHPDoc=True)
                    return True
                elif namelist:
                    php_variable = self.addVariable(namelist[0], vartype,
                                                    doc=comment,
                                                    fromPHPDoc=True)
                    return True
        return False

    def _variableHandler(self, styles, text, p, attributes, doc=None,
                         style="variable"):
        log.debug("_variableHandler:: style: %r, text: %r, attributes: %r",
                  style, text[p:], attributes)
        classVar = False
        if attributes:
            classVar = True
            if "var" in attributes:
                attributes.remove("var")  # Don't want this in cile output
        if style == "const":
            if self.currentClass is not None:
                classVar = True
            elif self.currentNamespace is not None:
                classVar = False
            else:
                log.debug("Ignoring const %r, as not defined in a "
                          "class or namespace context.", text)
                return
        looped = False
        while p < len(styles):
            if looped:
                if text[p] != ",":  # Variables need to be comma delimited.
                    p += 1
                    continue
                p += 1
            else:
                looped = True
            if style == "const":
                namelist, p = self._getIdentifiersFromPos(styles, text, p,
                                                          self.PHP_IDENTIFIER)
            elif text[p:p+3] == ["self", ":", ":"]:
                # Handle things like: "self::$instance = FOO", bug 92813.
                classVar = True
                namelist, p = self._getIdentifiersFromPos(styles, text, p+3,
                                                          self.PHP_VARIABLE)
            else:
                namelist, p = self._getIdentifiersFromPos(styles, text, p,
                                                          self.PHP_VARIABLE)
            if not namelist:
                break
            log.debug("namelist:%r, p:%d", namelist, p)
            # Remove the dollar sign
            name = self._removeDollarSymbolFromVariableName(namelist[0])
            # Parse special internal variable names
            if name == "parent":
                continue
            thisVar = False
            if name in ("this", "self", ):
                classVar = True
                thisVar = True # need to distinguish between class var types.
                if len(namelist) <= 1:
                    continue
                # We don't need the this/self piece of the namelist.
                namelist = namelist[1:]
                name = namelist[0]
            if len(namelist) != 1:
                # Example:  "item->foo;"  translates to namelist: [item, foo]
                if self.comment:
                    # We may be able to get some PHPDoc out of the comment.
                    if self._handleVariableComment(namelist, self.comment):
                        self.comment = None
                log.debug("multiple part variable namelist (ignoring): "
                          "%r, line: %d in file: %r", namelist,
                          self.lineno, self.filename)
                continue
            if name.endswith("()"):
                # Example:  "foo(x);"  translates to namelist: [foo()]
                if self.comment:
                    # We may be able to get some PHPDoc out of the comment.
                    if self._handleVariableComment(namelist, self.comment):
                        self.comment = None
                log.debug("variable is making a method call (ignoring): "
                          "%r, line: %d in file: %r", namelist,
                          self.lineno, self.filename)
                continue

            assignChar = text[p]
            typeNames = []
            mustCreateVariable = False
            # Work out the citdl, we also ensure this is not just a comparison,
            # i.e. not "$x == 2".
            if p+1 < len(styles) and styles[p] == self.PHP_OPERATOR and \
                                         assignChar in "=" and \
               (p+2 >= len(styles) or text[p+1] != "="):
                # Assignment to the variable
                mustCreateVariable = True
                typeNames, p = self._getVariableType(styles, text, p, assignChar)
                log.debug("typeNames: %r", typeNames)
                # Skip over paren arguments from class, function calls.
                if typeNames and p < len(styles) and \
                   styles[p] == self.PHP_OPERATOR and text[p] == "(":
                    p = self._skipPastParenArguments(styles, text, p+1)

            # Create the variable cix information.
            if mustCreateVariable or (not thisVar and p < len(styles) and
                                      styles[p] == self.PHP_OPERATOR and \
                                      text[p] in ",;"):
                log.debug("Line %d, variable definition: %r",
                         self.lineno, namelist)
                if style == "const":
                    if classVar:
                        self.addClassConstant(name, ".".join(typeNames),
                                              doc=self.comment)
                    else:
                        self.addConstant(name, ".".join(typeNames),
                                         doc=self.comment)
                elif classVar and self.currentClass is not None:
                    self.addClassMember(name, ".".join(typeNames),
                                        attributes=attributes, doc=self.comment,
                                        forceToClass=classVar)
                else:
                    self.addVariable(name, ".".join(typeNames),
                                     attributes=attributes, doc=self.comment)

    def _useKeywordHandler(self, styles, text, p):
        log.debug("_useKeywordHandler:: text: %r", text[p:])
        text = text[:] # copy since this might be modified in place
        original_p = p
        looped = False
        while p < len(styles):
            if looped:
                if text[p] != ",":  # Use statements need to be comma delimited.
                    p += 1
                    continue
                elif "{" in text and text.index("{") < p:
                    # Grouped "use" statements: new in PHP 7.
                    # Simply remove the last identifier parsed and start over.
                    # For example, given: 'use foo\{bar, baz}'
                    # 'foo\bar' will be parsed out, the "," will be detected
                    # here, and 'foo\{baz}' will be parsed, returning 'foo\baz'.
                    while text[p] != "{":
                        text.pop(p)
                        styles.pop(p)
                        p -= 1
                    p = original_p
                else:
                    p += 1
            else:
                looped = True

            # Catch PHP 5.6 "use function" or "use const" definitions.
            ilk = None
            if styles[p] == self.PHP_WORD:
                ilk = text[p]
                p += 1

            namelist, p = self._getIdentifiersFromPos(styles, text, p)
            log.debug("use:%r, p:%d", namelist, p)
            if namelist:
                alias = None
                if p+1 < len(styles):
                    if styles[p] == self.PHP_WORD and \
                       text[p] == "as":
                        # Uses an alias
                        alias, p = self._getIdentifiersFromPos(styles, text, p+1)
                        if alias:
                            alias = alias[0]
                if self.currentClass:
                    # Must be a trait.
                    self.currentClass.addTraitReference(namelist[0])
                else:
                    # Must be a namespace reference.
                    self.addNamespaceImport(namelist[0], alias, ilk=ilk)

    def _foreachKeywordHandler(self, styles, text, p):
        log.debug("_foreachKeywordHandler:: text: %r", text[p:])
        if "as" not in text:
            return
        typeNames, p = self._getVariableType(styles, text, p, assignmentChar=None)
        if typeNames:
            if "(" in typeNames[0]:
                p = self._skipPastParenArguments(styles, text, p+1)
            # Note: It's an item of the array, not an array itself.
            typeNames[-1] += "[]"
            log.debug("typeNames:%r", typeNames)
        p = text.index("as") + 1
        if p < len(text):
            # Two formats:
            #   as $value
            #   as $key => $value
            namelist1, p = self._getIdentifiersFromPos(styles, text, p,
                                                      self.PHP_VARIABLE)
            namelist2 = None
            if p+3 < len(text) and text[p:p+2] == ["=", ">"]:
                p += 2
                namelist2, p = self._getIdentifiersFromPos(styles, text, p,
                                                          self.PHP_VARIABLE)
            log.debug("namelist1:%r, namelist2:%r", namelist1, namelist2)
            if namelist2 and namelist1:
                self.addVariable(namelist1[0],
                                 #vartype="string|integer",
                                 doc=self.comment)
                self.addVariable(namelist2[0],
                                 vartype=".".join(typeNames),
                                 doc=self.comment)
            elif namelist1:
                self.addVariable(namelist1[0],
                                 vartype=".".join(typeNames),
                                 doc=self.comment)

    def _handleTraitResolution(self, styles, text, p, doc=None):
        log.debug("_handleTraitResolution:: text: %r", text[p:])
        # Examples:
        #       B::smallTalk insteadof A;
        #       B::bigTalk as talk;
        #       sayHello as protected;
        #       sayHello as private myPrivateHello;

        # Can only be defined on a trait or a class.
        if not self.currentClass:
            log.warn("_handleTraitResolution:: not in a class|trait definition")
            return

        # Look for the identifier first.
        #
        namelist, p = self._getIdentifiersFromPos(styles, text, p,
                                                  self.PHP_IDENTIFIER)
        log.debug("namelist:%r, p:%d", namelist, p)
        if not namelist or p+2 >= len(text):
            log.warn("Not enough arguments in trait use statement: %r", text)
            return

        # Get the keyword "as", "insteadof"
        keyword = text[p]
        log.debug("keyword:%r", keyword)
        p += 1

        # Get the settings.
        alias = None
        visibility = None
        # Get special attribute keywords.
        if keyword == "as" and \
           text[p] in ("public", "protected", "private"):
            visibility = text[p]
            p += 1
            log.debug("_handleTraitResolution: visibility %r", visibility)
        if p < len(text):
            # Get the alias name.
            names, p = self._getIdentifiersFromPos(styles, text, p)
            if names:
                alias = names[0]
                if len(names) > 1:
                    log.warn("Ignoring multiple alias identifiers in text: %r",
                             text)
        if alias or visibility:
            # Set override.
            self.currentClass.addTraitOverride(namelist, alias,
                                               visibility=visibility,
                                               insteadOf=(keyword=="insteadof"))
        else:
            self.warn("Unknown trait resolution: %r", text)

    def _addCodePiece(self, newstate=S_DEFAULT, varnames=None):
        styles = self.styles
        if len(styles) == 0:
            return
        text = self.text
        lines = self.linenos

        log.debug("*** Line: %d ********************************", self.lineno)
        #log.debug("Styles: %r", self.styles)
        log.debug("Text: %r", self.text)
        #log.debug("Comment: %r", self.comment)
        #log.debug("")

        pos = 0
        attributes = []
        firstStyle = styles[pos]

        try:
            # We may be able to get some PHPDoc out of the comment already,
            # such as targeted "@var " comments.
            # http://bugs.activestate.com/show_bug.cgi?id=76676
            if self.comment and self._handleVariableComment(None, self.comment):
                self.comment = None

            # Eat special attribute keywords
            while firstStyle == self.PHP_WORD and \
                  text[pos] in ("var", "public", "protected", "private",
                                "final", "static", "abstract"):
                attributes.append(text[pos])
                pos += 1
                firstStyle = styles[pos]

            if firstStyle == self.PHP_WORD:
                keyword = text[pos].lower()
                pos += 1
                if pos >= len(lines):
                    # Nothing else here, go home
                    return
                self.lineno = lines[pos]
                if keyword in ("require", "include", "require_once", "include_once"):
                    # Some examples (include has identical syntax):
                    # require 'prepend.php';
                    # require $somefile;
                    # require ('somefile.txt');
                    # XXX - Below syntax is not handled...
                    # if ((include 'vars.php') == 'OK') {
                    namelist = None
                    if pos < len(styles):
                        requirename = self._getIncludePath(styles, text, pos)
                        if requirename:
                            self.include_file(requirename)
                        else:
                            log.debug("Could not work out requirename. Text: %r",
                                      text[pos:])
                elif keyword == "define":
                    # Defining a constant
                    #   define("FOO",     "something");
                    #   define('TEST_CONSTANT', FALSE);
                    name, citdl = self._getConstantNameAndType(styles, text, pos)
                    if name:
                        self.addDefine(name, citdl)

                elif keyword == "const":
                    # Defining a class constant
                    #   const myconstant = x;
                    self._variableHandler(styles, text, pos, attributes,
                                          doc=self.comment, style="const")

                elif keyword == "function":
                    namelist, p = self._getIdentifiersFromPos(styles, text, pos)
                    log.debug("namelist:%r, p:%d", namelist, p)
                    if namelist:
                        returnByRef = (text[pos] == "&")
                        phpArgs, p = self._getArgumentsFromPos(styles, text, p)
                        log.debug("Line %d, function: %r(%r)",
                                 self.lineno, namelist, phpArgs)
                        if len(namelist) != 1:
                            log.debug("warn: invalid function name (ignoring): "
                                      "%r, line: %d in file: %r", namelist,
                                      self.lineno, self.filename)
                            return
                        self.addFunction(namelist[0], phpArgs, attributes,
                                         doc=self.comment,
                                         returnByRef=returnByRef)
                elif keyword == "class" or keyword == "trait":
                    # Examples:
                    #   class SimpleClass {
                    #   class SimpleClass2 extends SimpleClass {
                    #   class MyClass extends AbstractClass implements TestInterface, TestMethodsInterface {
                    #
                    namelist, p = self._getIdentifiersFromPos(styles, text, pos)
                    if namelist and "{" in text:
                        if len(namelist) != 1:
                            log.debug("warn: invalid class name (ignoring): %r, "
                                      "line: %d in file: %r", namelist,
                                      self.lineno, self.filename)
                            return
                        extends = self._getExtendsArgument(styles, text, p)
                        implements = self._getImplementsArgument(styles, text, p)
                        #print "extends: %r" % (extends)
                        #print "implements: %r" % (implements)
                        self.addClass(namelist[0], extends=extends,
                                      attributes=attributes,
                                      interfaces=implements, doc=self.comment,
                                      isTrait=(keyword == "trait"))
                elif keyword == "interface":
                    # Examples:
                    #   interface Foo {
                    #   interface SQL_Result extends SeekableIterator, Countable {
                    #
                    namelist, p = self._getIdentifiersFromPos(styles, text, pos)
                    if namelist and "{" in text:
                        if len(namelist) != 1:
                            log.debug("warn: invalid interface name (ignoring): "
                                      "%r, line: %d in file: %r", namelist,
                                      self.lineno, self.filename)
                            return
                        extends = self._getExtendsArgument(styles, text, p)
                        self.addInterface(namelist[0], extends, doc=self.comment)
                elif keyword in ("return", "yield"):
                    # Returning value for a function call
                    #   return 123;
                    #   return $x;
                    typeNames, p = self._getVariableType(styles, text, pos, assignmentChar=None)
                    log.debug("typeNames:%r", typeNames)
                    if typeNames:
                        self.addReturnType(".".join(typeNames))
                elif keyword == "catch" and pos+3 >= len(text):
                    # catch ( Exception $e)
                    pos += 1   # skip the paren
                    typeNames, p = self._getVariableType(styles, text, pos, assignmentChar=None)
                    namelist, p = self._getIdentifiersFromPos(styles, text, p, self.PHP_VARIABLE)
                    if namelist and typeNames:
                        self.addVariable(namelist[0], ".".join(typeNames))
                elif keyword == "namespace":
                    namelist, p = self._getIdentifiersFromPos(styles, text, pos)
                    log.debug("namelist:%r, p:%d", namelist, p)
                    if namelist:
                        usesBraces = "{" in text
                        self.setNamespace(namelist, usesBraces,
                                          doc=self.comment)
                elif keyword == "use":
                    self._useKeywordHandler(styles, text, pos)
                    if text and text[-1] == "{":
                        self.return_to_state = newstate
                        newstate = S_TRAIT_RESOLUTION
                elif keyword == "foreach":
                    self._foreachKeywordHandler(styles, text, pos+1)
                elif keyword == "if" and "(" in text:
                    # Skip over the if statement and use the rest.
                    p = text.index("(")
                    p = self._skipPastParenArguments(styles, text, p+1)
                    if re.search(r"[^!]=[^=]", "".join(text[:p])):
                        # Variable assignment like "if ($var = ...) {"
                        self.text = text[text.index("(") + 1:p - 1]
                        self.styles = styles[text.index("(") + 1:p - 1]
                        self._addCodePiece()
                    self.text = text[p:]
                    self.styles = styles[p:]
                    self._addCodePiece()
                else:
                    log.debug("Ignoring keyword: %s", keyword)
                    self._addAllVariables(styles, text, pos)

            elif firstStyle == self.PHP_IDENTIFIER:
                if text[0] == "self":
                    self._variableHandler(styles, text, pos, attributes,
                                          doc=self.comment)
                elif self.state == S_TRAIT_RESOLUTION:
                    self._handleTraitResolution(styles, text, pos, doc=self.comment)
                    log.debug("Trait resolution: text: %r, pos: %d", text, pos)
                    # Stay in this state.
                    newstate = S_TRAIT_RESOLUTION
                elif "new" in text and "class" in text and \
                     text.index("new") + 1 == text.index("class"):
                    # Anonymous classes: new in PHP 7.
                    p = text.index("class") + 1
                    extends = self._getExtendsArgument(styles, text, p)
                    implements = self._getImplementsArgument(styles, text, p)
                    #print "extends: %r" % (extends)
                    #print "implements: %r" % (implements)
                    self._anonid += 1
                    self.addClass("(anonymous %d)" % self._anonid, extends=extends,
                                  attributes=attributes,
                                  interfaces=implements, doc=self.comment,
                                  isTrait=False)
                elif "function" in text:
                    # Anonymous function (likely a callback).
                    p = text.index("function") + 1
                    phpArgs, p = self._getArgumentsFromPos(styles, text, p)
                    self._anonid += 1
                    self.addFunction("(anonymous %d)" % self._anonid, phpArgs, attributes)
                else:
                    log.debug("Ignoring when starting with identifier")
            elif firstStyle == self.PHP_VARIABLE:
                # Defining scope for action
                self._variableHandler(styles, text, pos, attributes,
                                      doc=self.comment)
            else:
                log.debug("Unhandled first style:%d", firstStyle)
        finally:
            self._resetState(newstate)

    def _resetState(self, newstate=S_DEFAULT):
        self.state = newstate
        self.styles = []
        self.linenos = []
        self.text = []
        self.comment = None
        self.comments = []

    def token_next(self, style, text, start_column, start_line, **other_args):
        """Loops over the styles in the document and stores important info.

        When enough info is gathered, will perform a call to analyze the code
        and generate subsequent language structures. These language structures
        will later be used to generate XML output for the document."""
        #log.debug("text: %r", text)
        #print "text: %r, style: %r" % (text, style)

        self.tokens.append({"style": style, "text": text, "line": start_line + 1, "column": start_column}) # track for use by getCompletionContext()

        if self.state == S_GET_HEREDOC_MARKER:
            if not text.strip():
                log.debug("Ignoring whitespace after <<<: %r", text)
                return
            self.heredocMarker = self._unquoteString(text)
            log.debug("getting heredoc marker: %r, now in heredoc state", text)
            self._resetState(S_IN_HEREDOC)

        elif self.state == S_IN_HEREDOC:
            # Heredocs *must* be on the start of a newline
            if text == self.heredocMarker and self.lastText and \
               self.lastText[-1] in "\r\n":
                log.debug("end of heredoc: %r", self.heredocMarker)
                self._resetState(self.return_to_state)
            else:
                log.debug("ignoring heredoc material")

        elif (style in (self.PHP_WORD, self.PHP_IDENTIFIER,
                      self.PHP_OPERATOR, self.PHP_NUMBER, self.PHP_VARIABLE) or
            style in (self.PHP_STRINGS)):
            # We keep track of these styles and the text associated with it.
            # When we gather enough info, these will be sent to the
            # _addCodePiece() function which will analyze the info.
            self.lineno = start_line + 1

            if style != self.PHP_OPERATOR:
                # Have to trim whitespace, as the identifier style is
                # also the default whitespace style... ugly!
                if style == self.PHP_IDENTIFIER:
                    text = text.strip()
                if text:
                    self.text.append(text)
                    self.styles.append(style)
                    self.linenos.append(self.lineno)
                    #print "Text:", text
            else:
                # Do heredoc parsing, since UDL cannot as yet
                if text == "<<<":
                    self.return_to_state = self.state
                    self.state = S_GET_HEREDOC_MARKER
                # Remove out any "<?php" and "?>" tags, see syntax description:
                #   http://www.php.net/manual/en/language.basic-syntax.php
                elif text.startswith("<?"):
                    if text[:5].lower() == "<?php":
                        text = text[5:]
                    elif text.startswith("<?="):
                        text = text[len("<?="):]
                    else:
                        text = text[len("<?"):]
                elif text.startswith("<%"):
                    if text.startswith("<%="):
                        text = text[len("<%="):]
                    else:
                        text = text[len("<%"):]
                if text.endswith("?>"):
                    text = text[:-len("?>")]
                elif text.endswith("<%"):
                    text = text[:-len("%>")]

                col = start_column + 1
                #for op in text:
                #    self.styles.append(style)
                #    self.text.append(op)
                #log.debug("token_next: line %d, %r" % (self.lineno, text))
                brace_count = 0

                for i, op in enumerate(text):
                    self.styles.append(style)
                    self.text.append(op)
                    self.linenos.append(self.lineno)

                    # Skip double-colan, class/variables accesses, like "self::"
                    if op == ":":
                        if i+1 < len(text) and text[i+1] == ":":
                            continue
                        if i > 0 and text[i-1] == ":":
                            continue

                    if op == "(":
                        # We can start defining arguments now
                        #log.debug("Entering S_IN_ARGS state")
                        brace_count += 1
                        self.return_to_state = self.state
                        self.state = S_IN_ARGS
                    elif op == ")":
                        #log.debug("Entering state %d", self.return_to_state)
                        brace_count -= 1
                        self.state = self.return_to_state
                        # If, else and elseif can be a one-liner, so parse now.
                        if brace_count == 0 and self.text[0] in ("if", "elseif", "else"):
                            self._addCodePiece()
                    elif op == "=":
                        if text == op:
                            #log.debug("Entering S_IN_ASSIGNMENT state")
                            self.state = S_IN_ASSIGNMENT
                    elif op == "{" and \
                         (self.text[0] != "use" or self.text[-2] != "\\"):
                        # Increasing depth/scope, could be an argument object
                        # (Grouped "use" statements need to be handed below in '}'.)
                        self._addCodePiece()
                        self.incBlock()
                    elif op == "}":
                        # Decreasing depth/scope
                        if len(self.text) == 1:
                            self._resetState()
                        else:
                            self._addCodePiece()
                        self.decBlock()
                    elif op == ":":
                        # May be an alternative syntax
                        if len(self.text) > 0 and \
                           self.styles[0] == self.PHP_WORD and \
                           self.text[0].lower() in ("if", "elseif", "else", "while", "for", "foreach", "switch"):
                            #print "Alt syntax? text: %r" % (self.text, )
                            self._addCodePiece()
                        elif "case" in self.text or "default" in self.text:
                            # Part of a switch statement - bug 86927.
                            self._addCodePiece()
                    elif op == ";":
                        # Statement is done
                        if len(self.text) > 0 and \
                           self.styles[0] == self.PHP_WORD and \
                           self.text[-1].lower() in ("endif", "endwhile", "endfor", "endforeach", "endswitch"):
                            # Alternative syntax, remove this from the text.
                            self.text = self.text[:-1]
                        self._addCodePiece()
                    col += 1
        elif style in self.PHP_COMMENT_STYLES:
            # Use rstrip to remove any trailing spaces or newline characters.
            comment = text.rstrip()
            # Check if it's a continuation from the last comment. If we have
            # already collected text then this is a comment in the middle of a
            # statement, so do not set self.comment, but rather just add it to
            # the list of known comment sections (self.comments).
            if not self.text:
                if style == SCE_UDL_SSL_COMMENT and self.comment and \
                   start_line <= (self.comments[-1][2] + 1) and \
                   style == self.comments[-1][1]:
                    self.comment += comment
                else:
                    self.comment = comment
            self.comments.append([comment, style, start_line, start_column])
        elif style == SCE_UDL_SSL_DEFAULT and \
             self.lastStyle in self.PHP_COMMENT_STYLES and text[0] in "\r\n":
            # This is necessary as line comments are supplied to us without
            # the newlines, so check to see if this is a newline and if the
            # last line was a comment, append it the newline to it.
            if self.comment:
                self.comment += "\n"
            self.comments[-1][0] += "\n"
        self.lastText = text
        self.lastStyle = style

    def scan_multilang_content(self, content):
        """Scan the given PHP content, only processes SSL styles"""
        PHPLexer().tokenize_by_style(content, self.token_next)
        return self.csl_tokens, self.css_tokens

    def convertToElementTreeFile(self, cixelement):
        """Store PHP information into the cixelement as a file(s) sub element"""
        self.cile.convertToElementTreeFile(cixelement)

    def convertToElementTreeModule(self, cixblob):
        """Store PHP information into already created cixblob"""
        self.cile.convertToElementTreeModule(cixblob)

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    from language.legacy.html.stdlib import HTML5_STDLIB_FILE
    from language.legacy.php.stdlib import PHP_7_STDLIB_FILE
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    parser = argparse.ArgumentParser(description="Scan PHP source files")
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    scanner = PHPScanner(HTML5_STDLIB_FILE, PHP_7_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
