#
# Copyright (c) 2005-2006 ActiveState Software Inc.
#
# Contributors:
#   Eric Promislow (EricP@ActiveState.com)
#
#

from language.common import Scope, Class, Function, Method, ClassMethod, Argument, Variable, InstanceVariable, ClassVariable, Namespace, Import, AbstractScannerContext

VAR_KIND_UNKNOWN = 0
VAR_KIND_GLOBAL = 1
VAR_KIND_CLASS = 2
VAR_KIND_CLASSVAR = 3
VAR_KIND_INSTANCE = 4
VAR_KIND_LOCAL = 5
VAR_KIND_ALIAS = 6

class Name_LineNum:
    def __init__(self, name, line_num, type=None):
        self.line_num = line_num
        self.name = name
        self.type = type

class VarInfo:
    def __init__(self, line_num, type=None):
        self.line_num = line_num
        self.type = type

def update_collection(coll, name, line_num, type=None, attributes=None):
    if name not in coll:
        coll[name] = VarInfo(line_num, type)
    elif coll[name].type is None and type is not None:
        coll[name].type = type
    if attributes and coll['attributes'] is None:
        coll['attributes'] = attributes

def sort_by_lines(adict):
    intermed = [(adict[k].line_num, adict[k].type, k) for k in list(adict.keys())]
    intermed.sort()
    return intermed

class ParserScannerContext(AbstractScannerContext):
    """Implementation of AbstractScannerContext for a parser that makes use of
    this file's parse data.
    """
    def __init__(self, node):
        self._node = node
        self._documentation = False # unset; cannot use None since it is a valid value

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        if hasattr(self._node, "line_num"):
            return self._node.line_num
        else:
            return None

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation."""
        if self._documentation is False:
            self._documentation = hasattr(self._node, "doc_lines") and "\n".join(self._node.doc_lines) or None
        return self._documentation

    @property
    def signature(self):
        return isinstance(self._node, MethodNode) and self._node.signature or None

    def contains(self, line):
        """Implementation of AbstractScannerContext.contains()."""
        if self.line is None:
            return False
        return line >= self.line and (not hasattr(self._node, "lineend") or line <= self._node.lineend)

class Node:
    def __init__(self, line_num, class_name=None):
        self.children = []
        self.line_num = line_num
        self.indentation = 0
        self.doc_lines = []
        self.imports = [] # require and load stmts
        self.includes = []  # include stmts

        self.class_vars = {}
        self.instance_vars = {}
        self.local_vars = {}
        self.aliases = {}
        if class_name: self.class_name = class_name

    def append_node(self, new_node):
        self.children.append(new_node)

    def set_line_end_num(self, line_end_num):
        self.lineend = line_end_num

    def dump_kids(self, indent_level):
        for c in self.children:
            c.dump(indent_level + 2)

    def dump2(self, node_type_name, indent_level, *call_backs):
        print("%s %s %s - line %r:%r" % (" " * indent_level, node_type_name, self.name, self.line_num, getattr(self, 'lineend', '???')))
        if len(self.doc_lines) > 0:
            print("%s Documentation: %s" % (" " * (indent_level + 2), "\n".join(self.doc_lines)))
        if len(self.imports) > 0:
            for m in self.imports:
                print("%s Import: %s" % (" " * (indent_level + 2), m.name))
        if len(self.includes) > 0:
            for m in self.includes:
                print("%s Include: %s" % (" " * (indent_level + 2), m.name))
        self.dump_collection('Globals', 'global_vars', indent_level + 2)
        self.dump_collection('Locals', 'local_vars', indent_level + 2)
        self.dump_collection('Instance vars', 'instance_vars', indent_level + 2)
        self.dump_collection('Class vars', 'class_vars', indent_level + 2)
        for cb in call_backs:
            cb()
        self.dump_kids(indent_level)

    def dump_collection(self, label, attr, indent_level):
        if hasattr(self, attr):
            collection = getattr(self, attr)
            if len(collection) > 0:
                print("%s %s: " % (" " * indent_level, label))
                for name, varInfo in list(collection.items()):
                    line, type = varInfo.line_num, varInfo.type
                    if type:
                        typeString = " [" + type + "]"
                    else:
                        typeString = ""
                    print("%s %s - line %s%s" % (" " * (indent_level + 2), name, line, typeString))

    def toAbstractSymbol(self, enclosingScope=None):
        if isinstance(self, FileNode):
            scope = Scope(enclosingScope)
        elif isinstance(self, ModuleNode):
            scope = Namespace(self.name, enclosingScope, ParserScannerContext(self))
        elif isinstance(self, ClassNode):
            classrefs = [(len(ref) > 2 and ref[2] or ref[0])
                         for ref in self.classrefs]
            if len(classrefs) > 1:
                scope = Class(self.name, enclosingScope, classrefs, ParserScannerContext(self))
            elif len(classrefs) == 1:
                scope = Class(self.name, enclosingScope, classrefs[0], ParserScannerContext(self))
            else:
                scope = Class(self.name, enclosingScope, None, ParserScannerContext(self))
        elif isinstance(self, MethodNode):
            if isinstance(enclosingScope, Class):
                if self.is_classmethod:
                    scope = ClassMethod(self.name, "Method", None, enclosingScope, ParserScannerContext(self))
                else:
                    scope = Method(self.name, "Method", None, enclosingScope, ParserScannerContext(self))
            else:
                scope = Function(self.name, "Method", None, enclosingScope, ParserScannerContext(self))
            for c in self.args:
                symbol = Argument(c.get_full_name(), None, ParserScannerContext(self))
                scope.define(symbol)
        else:
            return None

        # TODO: self.doc_lines
        for imp in self.imports:
            if imp.name:
                # "require 'foo'" imports everything from 'foo' into the current
                # scope.
                symbol = Import("%s.*" % imp.name, imp.name, ParserScannerContext(imp))
                scope.define(symbol)

        for incl in self.includes:
            if incl.name:
                # "include Foo" imports everything from Foo into the current scope.
                # Since the class/module being included might exist in the current
                # scope, prepend "." to avoid a name conflict in the scope.members
                # dictionary.
                symbol = Import(".%s" % incl.name, incl.name, ParserScannerContext(incl))
                scope.define(symbol)

        if isinstance(self, FileNode):
            for line_no, var_type, var_name in sort_by_lines(self.global_vars):
                symbol = Variable(var_name, var_type, ParserScannerContext(self.global_vars[var_name]))
                scope.define(symbol)
        for d in [self.local_vars, self.class_vars, self.instance_vars]:
            for line_no, var_type, var_name in sort_by_lines(d):
                if d == self.local_vars:
                    symbol = Variable(var_name, var_type, ParserScannerContext(d[var_name]))
                elif d == self.class_vars:
                    symbol = ClassVariable(var_name, var_type, ParserScannerContext(d[var_name]))
                elif d == self.instance_vars:
                    symbol = InstanceVariable(var_name, var_type, ParserScannerContext(d[var_name]))
                scope.define(symbol)

        for c in self.children:
            symbol = c.toAbstractSymbol(scope)
            if symbol: # will be None if not Module, Class, or Method
                scope.define(symbol)

        for line_no, var_type, var_name in sort_by_lines(self.aliases):
            member = scope.resolveMember(var_type)
            if member:
                symbol = member.__class__(var_name, member.type, member.returnType, member.enclosingScope, member.ctx)
                scope.define(symbol)

        return scope

class ClassNode(Node):
    def __init__(self, the_name, line_num, unused=False):
        self.name = the_name
        self.classrefs = []
        Node.__init__(self, line_num, "Class")

    def add_classrefs(self, class_ref_name, line_num, classref_type=None):
        if class_ref_name not in [x[0] for x in self.classrefs]:
            self.classrefs.append([class_ref_name, line_num, classref_type])

    def has_classref(self, name):
        return name in [x[0] for x in self.classrefs]

    def dump(self, indent_level):
        def cb():
            classrefs = self.classrefs
            if len(classrefs) > 0:
                for ref in classrefs:
                    print("%sClassref %s - line %s" % (" " * (indent_level + 2),
                                                       ref[0], ref[1]))
        self.dump2("Class", indent_level, cb)

class FileNode(Node):
    def __init__(self):
        Node.__init__(self, 0)
        self.global_vars = {}  # Easy to get at

    def dump(self):
        self.name = ""
        self.dump2("file", 0)
        return
        indent_level = 0
        if len(self.imports) > 0:
            for m in self.imports:
                print("%s Import: %s" % (" " * (indent_level + 2), m.name))
        if len(self.includes) > 0:
            for m in self.includes:
                print("%s Include: %s" % (" " * (indent_level + 2), m.name))
        self.dump_kids(0)

class ArgNode:
    def __init__(self, name, extra_info, arg_attrs):
        self.name = name
        self.extra_info = extra_info
        self.arg_attrs = arg_attrs
    def get_full_name(self):
        if self.extra_info:
            return self.extra_info + self.name
        return self.name

class MethodNode(Node):
    def __init__(self, the_name, line_num, is_constructor=False):
        self.name = the_name
        self.is_constructor = is_constructor
        self.signature = ""
        self.args = []
        self.is_classmethod = False
        Node.__init__(self, line_num, "Method")

        # extra_info for Ruby, arg_attrs for Tcl's "args", like Ruby's "*args"
    def add_arg(self, name, extra_info=None, arg_attrs=None):
        self.args.append(ArgNode(name, extra_info, arg_attrs))

    def dump(self, indent_level):
        def cb():
            args = self.args
            for arg in args:
                print("%sArg %s" % (" " * (indent_level + 2), arg.get_full_name()))
        self.dump2("Method", indent_level, cb)
        if len(self.signature) > 0:
            print("%sSignature %s" % (" " * (indent_level + 2), self.signature))

class ModuleNode(Node):
    def __init__(self, the_name, line_num, unused=False):
        self.name = the_name
        Node.__init__(self, line_num, "Module")

    def dump(self, indent_level):
        self.dump2("Module", indent_level)

class VariableNode(Node):
    def __init__(self, the_name, line_num):
        self.name = the_name
        Node.__init__(self, line_num, "Variable")

    def dump(self, indent_level):
        self.dump2("Module", indent_level)

class BlockNode(Node):
    def __init__(self, the_name, line_num):
        self.name = the_name
        Node.__init__(self, line_num, "Block")

    def dump(self, indent_level):
        self.dump2("Module", indent_level)
