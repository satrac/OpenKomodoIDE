from xml.etree import ElementTree

from language.common import Scope, Class, Interface, Function, Constructor, Method, ClassMethod, PrivateMethod, ProtectedMethod, StaticMethod, Argument, Struct, Variable, Constant, InstanceVariable, PrivateInstanceVariable, ProtectedInstanceVariable, ClassVariable, Module, Import, AbstractScannerContext, AbstractSymbol, Namespace

import logging
log = logging.getLogger("language.legacy.cix")

# General TODO:
#
# Support or deprecate required_library_name=".." (NodeJS)
# Handle exported, exportable attributes in Perl

class CixScannerContext(AbstractScannerContext):
    """Implementation of AbstractScannerContext for the Cix parser."""
    def __init__(self, elem):
        self.elem = elem

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        return None # no line information in stdlibs

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation."""
        return self.elem.get("doc", None)

    @property
    def signature(self):
        """Implementation of AbstractScannerContext.signature."""
        return self.elem.get("signature", None)

    def contains(self, line):
        """Implementation of AbstractScannerContext.contains()."""
        return False # not applicable to stdlibs, which are already scoped

class Cix:
    """Convert XML Cix to Scope/Symbol structure"""

    def __init__(self, data, sep="."):

        self.result = {}
        self.root = ElementTree.fromstring(data)
        self.sep = sep # separator for module namespaces

    def parse(self):
        """Parse the root ElementTree and return self.result"""

        # We queue elems to process here rather than recurse, so as not
        # to create huge recursion loops (fun to debug and there's probably a limit to those)
        elems = [[self.root, None]]

        for entry in elems:
            elem, scope = entry
            children = self._parse(elem, scope)
            if children:
                elems.extend(children)

        return self.result

    def _parse(self, elem, scope):
        """Parse a single element and return its children, the loop in Cix.parse()
        will take care of iterating over each child.
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        # Generate the handler name
        handlerName = elem.tag
        if elem.tag == "scope":
            handlerName = "scope_%s" % elem.get("ilk")
        elif elem.get("ilk", None):
            handlerName = "%s_%s" % (handlerName, elem.get("ilk"))

        # Cix files dont seem to differentiate between functions and methods,
        # so we need to interpret this for ourselves
        if handlerName == "scope_function" and isinstance(scope, Class):
            handlerName = "scope_method"

        # Same story for properties
        if handlerName == "variable" and isinstance(scope, Class):
            handlerName = "property"

        # Stuff like private, protected, etc is stored in an attribute called
        # "attributes" (yes, really), so take that into consideration for parsing
        if elem.get("attributes", None):
            attributes = elem.get("attributes").split()
            for attribute in attributes:
                attribute = attribute.strip("_")
                if attribute in ["hidden", "classmethod", "exported", "exportable"]: # ignored attributes
                    continue

                # rename certain attributes, because our cix files don't seem to like consistency
                if attribute == "const":
                    attribute = "constant"
                elif attribute == "ctor":
                    attribute = "constructor"

                _handlerName = "%s_%s" % (handlerName, attribute)
                if getattr(self, "_parse_%s" % _handlerName, None):
                    handlerName = _handlerName
                else:
                    log.debug("Possibly missing parser for: %s" % _handlerName)

        # Check if we have a handler function (_parse_foo)
        handler = getattr(self, "_parse_%s" % handlerName, None)
        if not handler:
            if not scope:
                # If scope is not defined then we can ignore the handler not
                # existing (this is probably <codeintel>)
                return self.getChildren(elem, scope)
            else:
                # If we have a scope and can't find a handler then we
                # likely have a problem
                log.warn("Unrecognized tag: %s" % elem.tag)
                return False

        # Turn the XML element into a Symbol
        symbol = handler(elem, scope)

        if not scope and isinstance(symbol, Scope) and elem.tag == "file":
            # If we don't have a scope then this should be a file, and we should
            # add it to our results dict
            self.result[elem.get("path")] = symbol
        elif not scope:
            # This shouldn't happen, file must be at the root of the scope
            raise ExceptionMissingFileScope()
        elif isinstance(symbol, AbstractSymbol):
            # Todo: symbols should always depend on a scope (this should not be a conditonal)
            scope.define(symbol)

        # We're done with this symbol, so return its children so the loop can parse
        # them too
        return self.getChildren(elem, symbol)

    def getChildren(self, elem, scope):
        """Get children for the given element, and return a list with [child,scope]
        This is for use by the loop in Cix.parse()
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """

        children = []
        for child in elem:
            if child is elem:
                continue

            children.append([child, scope])

        return children

    def _parse_file(self, elem, scope):
        """Parse a <file> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return Scope()

    def _parse_variable(self, elem, scope, className = "Variable"):
        """Parse a <variable> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        @param className String name of the class to symbol class to call
        """
        typeValue = None

        # Parse attributes
        if elem.get("attributes", None):
            attributes = elem.get("attributes").split()
            if "kwargs" in attributes:
                typeValue = "kwargs"
            elif "varargs" in attributes:
                typeValue = "varargs"
        if elem.get("citdl", None):
            typeValue = elem.get("citdl")

        if not elem.getchildren():
            return eval(className)(
                name = elem.get("name"),
                type = typeValue
            )
        else:
            # In some languages like JavaScript, "variable" ilks can be
            # containers (i.e. Scopes). Create an AbstractStruct instead of an
            # Abstract*Variable.
            return Struct(
                name = elem.get("name"),
                type = typeValue
            )

    def _parse_variable_constant(self, elem, scope):
        """Parse a <variable attributes="__const__"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_variable(elem, scope, "Constant")

    def _parse_variable_argument(self, elem, scope):
        """Parse a <variable ilk="argument"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_variable(elem, scope, "Argument")

    def _parse_property(self, elem, scope):
        """Parse a <variable> element that belongs to a Class scope
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_variable(elem, scope, "InstanceVariable")

    def _parse_property_const(self, elem, scope):
        """Parse a <variable attributes="__const__"> element that belongs to a Class scope
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_variable(elem, scope, "Constant")

    def _parse_property_private(self, elem, scope):
        """Parse a <variable attributes="private"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_variable(elem, scope, "PrivateInstanceVariable")

    def _parse_property_protected(self, elem, scope):
        """Parse a <variable attributes="protected"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_variable(elem, scope, "ProtectedInstanceVariable")

    def _parse_import(self, elem, scope):
        """Parse a <import> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        if elem.get("module"):
            # Import an external module.
            name = elem.get("module")
            type = elem.get("module")
            if elem.get("symbol"):
                name = elem.get("symbol")
                if not name.startswith("*"):
                    type = "%s%s%s" % (type, self.sep, elem.get("symbol"))
                else:
                    name = "%s%s%s" % (type, self.sep, name)
            if elem.get("alias"):
                name = elem.get("alias")
        else:
            # Include the members of a symbol in the scope.
            # Prepend "." so-as not to overwrite the symbol itself.
            name = "." + elem.get("symbol")
            type = elem.get("symbol")
        return Import(
            name = name,
            type = type
        )

    def _parse_scope_blob(self, elem, scope):
        """Parse a <scope ilk="blob"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return Module(elem.get("name"), scope)

    def _parse_scope_function(self, elem, scope, className = "Function"):
        """Parse a <scope ilk="function"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        @param className String name of the class to symbol class to call
        """
        return eval(className)(
            name = elem.get("name"),
            type = None,
            returnType = elem.get("returns", None),
            enclosingScope = scope,
            ctx = CixScannerContext(elem)
        )

    def _parse_scope_method_constructor(self, elem, scope):
        """Parse a <scope ilk="function" attributes="ctor"> element that belongs
        to a Class scope
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_scope_function(elem, scope, "Constructor")

    def _parse_scope_method(self, elem, scope):
        """Parse a <scope ilk="function"> element that belongs to a Class scope
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        if elem.get("name") != "__construct":
            return self._parse_scope_function(elem, scope, "Method")
        else:
            return self._parse_scope_function(elem, scope, "Constructor") # for PHP

    def _parse_scope_method_protected(self, elem, scope):
        """Parse a <scope ilk="function" attributes="protected"> element that
        belongs to a Class scope
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_scope_function(elem, scope, "ProtectedMethod")

    def _parse_scope_method_private(self, elem, scope):
        """Parse a <scope ilk="function" attributes="private"> element that
        belongs to a Class scope
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_scope_function(elem, scope, "PrivateMethod")

    def _parse_scope_method_static(self, elem, scope):
        """Parse a <scope ilk="function" attributes="static"> element that
        belongs to a Class scope
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return self._parse_scope_function(elem, scope, "StaticMethod")

    def _parse_scope_namespace(self, elem, scope):
        """Parse a <scope ilk="namespace"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return Namespace(
            name = elem.get("name"),
            enclosingScope = scope
        )

    def _parse_scope_class(self, elem, scope):
        """Parse a <scope ilk="class"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        if len(elem.get("classrefs", "").split()) < 2:
            superclass_name = elem.get("classrefs", None)
        else:
            superclass_name = elem.get("classrefs").split()
        return Class(
            name = elem.get("name"),
            enclosingScope = scope,
            superclassName = superclass_name
        )

    def _parse_scope_interface(self, elem, scope):
        """Parse a <scope ilk="interface"> element
        @param elem ElementTree the element we're parsing
        @param scope Scope|Symbol|None the current scope
        """
        return Interface(
            name = elem.get("name"),
            enclosingScope = scope,
            superclassName = elem.get("classrefs", None)
        )

class ExceptionMissingFileScope(Exception):
    message = "Cannot process symbols without a file scope at the root"
