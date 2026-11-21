# Copyright 2026, ActiveState Software Inc. All rights reserved.
# See the file LICENSE.txt for licensing information.

"""Minimal Python scanner built on the standard ``ast`` module.

The original CodeIntel Python scanner used the ``compiler`` and ``parser``
modules, which were removed in Python 3.  This replacement implements the
parts needed by the Symbol Browser (and the scanner API in general) using
``ast``.  Completion, call-tip, go-to-definition and find-references contexts
are intentionally not implemented here; returning ``None`` makes the UI fall
back gracefully instead of failing the request.
"""

import ast
import builtins
import keyword
import logging
import os
import re
import sys
import tokenize

from calltips import CallTipContext
from completions import (AbstractMemberCompletionContext,
                         AbstractScopeCompletionContext)
from goto_definition import GotoDefinitionContext
from language.common import (AbstractScanner, AbstractScannerContext,
                             Argument, BuiltIn, Class, ClassMethod, Function,
                             Import, Keyword, Method, Module, PrivateMethod,
                             Scope, StaticMethod, Variable)

log = logging.getLogger("codeintel.python")


class PythonASTContext(AbstractScannerContext):
    """Context for symbols created by the AST scanner."""

    def __init__(self, node, filename=None, signature=None,
                 documentation=None, line=None, lineend=None):
        self._node = node
        self._filename = filename
        self._signature = signature
        self._documentation = documentation
        self._line = line
        self._lineend = lineend

    @property
    def filename(self):
        return self._filename

    @property
    def line(self):
        if self._line is not None:
            return self._line
        return getattr(self._node, "lineno", None)

    @property
    def lineend(self):
        if self._lineend is not None:
            return self._lineend
        return getattr(self._node, "end_lineno", None)

    @property
    def documentation(self):
        return self._documentation

    @property
    def signature(self):
        return self._signature

    def contains(self, line):
        if line is None:
            return False
        start = self.line
        end = self.lineend
        if start is None:
            return False
        return line >= start and (end is None or line <= end)


def _parse_source(source, filename="<buffer>"):
    """Parse source with a little tolerance for buffers being typed."""
    try:
        return ast.parse(source, filename)
    except (SyntaxError, ValueError):
        pass

    for suffix in (")", "]", "}", ")", "\npass\n", "\n"):
        try:
            return ast.parse(source + suffix, filename)
        except (SyntaxError, ValueError):
            continue

    # Fall back to the longest prefix of complete lines that parses.
    lines = source.splitlines()
    while lines:
        try:
            return ast.parse("\n".join(lines), filename)
        except (SyntaxError, ValueError):
            lines.pop()
    return None


def _read_source(filename):
    """Return (source, filename) for a path or an in-memory buffer."""
    if filename is None:
        return sys.stdin.read(), ":stdin:"

    if isinstance(filename, bytes):
        filename = filename.decode("utf-8", "replace")

    if isinstance(filename, str) and os.path.exists(filename):
        try:
            with tokenize.open(filename) as f:
                return f.read(), filename
        except Exception:
            with open(filename, "r", encoding="utf-8", errors="replace") as f:
                return f.read(), filename

    return filename, ":untitled:"


def _module_name(filename):
    if filename in (None, ":stdin:", ":untitled:"):
        return "module"
    base = os.path.basename(filename)
    if base in ("__init__.py", "__init__.pyi"):
        parent = os.path.basename(os.path.dirname(filename))
        return parent or "module"
    name = os.path.splitext(base)[0]
    return name or "module"


def _annotation(node):
    annotation = getattr(node, "annotation", None)
    if annotation is None:
        return None
    try:
        return ast.unparse(annotation)
    except Exception:
        return None


def _target_names(node):
    target = getattr(node, "target", None) or node.targets[0] \
        if getattr(node, "targets", None) else getattr(node, "target", None)
    names = []

    def collect(t):
        if isinstance(t, ast.Name):
            names.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for elt in t.elts:
                collect(elt)

    if target is not None:
        collect(target)
    return names


class PythonScopeCompletionContext(AbstractScopeCompletionContext):
    """Scope completions for Python."""
    @property
    def language(self):
        return "Python"


class PythonMemberCompletionContext(AbstractMemberCompletionContext):
    """Member completions for Python."""
    @property
    def language(self):
        return "Python"


class PythonASTScanner(AbstractScanner):
    """Python scanner that builds CodeIntel symbols from an ``ast`` tree."""

    def __init__(self, stdlib_file=None):
        # Do not try to load a CIX stdlib; it is unavailable in this build.
        AbstractScanner.__init__(self, None)
        for name in keyword.kwlist:
            self._builtInScope.define(Keyword(name))
        for name in dir(builtins):
            if not name.startswith("_"):
                self._builtInScope.define(BuiltIn(name))

    def scan(self, filename, env={}):
        source, filename = _read_source(filename)
        self.content = source
        self.filename = filename
        tree = _parse_source(
            source, filename if filename != ":untitled:" else "<buffer>")
        if tree is None:
            log.debug("Could not parse %s", filename)
            return Scope()

        top = Scope(self.builtInScope)
        line_count = len(source.splitlines()) or 1
        module = Module(_module_name(filename), top,
                        PythonASTContext(tree, filename,
                                         documentation=ast.get_docstring(tree),
                                         line=1, lineend=line_count))
        top.define(module)
        self._walk(tree.body, module, filename, in_class=False, in_function=False)
        return top

    # -- contexts ---------------------------------------------------------

    def _scan_state(self, filename, position):
        scope = self.scan(filename, env={})
        content = getattr(self, "content", "")
        line = content.count("\n", 0, max(0, position)) + 1
        return scope.resolveScope(line), content

    def getCompletionContext(self, filename, position, env={}):
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)"
                            % position.__class__.__name__)

        scope, content = self._scan_state(filename, position)
        before = content[:position]

        # Member completion (obj. or module.).
        match = re.search(
            r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.([A-Za-z_]\w*)?$',
            before)
        if match:
            receiver = match.group(1)
            name_part = match.group(2) or ""
            return PythonMemberCompletionContext(scope, receiver, name_part)

        match = re.search(r'([A-Za-z_]\w*)$', before)
        name_part = match.group(1) if match else ""
        return PythonScopeCompletionContext(scope, name_part)

    def getCallTipContext(self, filename, position, env={}):
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)"
                            % position.__class__.__name__)

        scope, content = self._scan_state(filename, position)
        before = content[:position]
        depth = 0
        for i in range(len(before) - 1, -1, -1):
            char = before[i]
            if char == ")":
                depth += 1
            elif char == "(":
                if depth > 0:
                    depth -= 1
                    continue
                match = re.search(r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*$',
                                  before[:i])
                if match:
                    return CallTipContext(scope, match.group(1))
                break
        return None

    def getGotoDefinitionContext(self, filename, position, env={}):
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)"
                            % position.__class__.__name__)

        scope, content = self._scan_state(filename, position)
        before = content[:position]
        after = content[position:]

        match = re.search(r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)$', before)
        if not match:
            match = re.match(r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)', after)
        if not match:
            return None

        return GotoDefinitionContext(scope, match.group(1))

    def getFindReferencesContext(self, filename, position, env={}):
        return None

    # -- tree walking -----------------------------------------------------

    def _walk(self, body, scope, filename, in_class=False, in_function=False):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_function(node, scope, filename, in_class)
            elif isinstance(node, ast.ClassDef):
                self._add_class(node, scope, filename)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                self._add_assign(node, scope, filename, in_class, in_function)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                self._add_import(node, scope, filename)
            elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                   ast.With, ast.AsyncWith, ast.Try)):
                for child in self._child_blocks(node):
                    self._walk(child, scope, filename, in_class, in_function)

    @staticmethod
    def _child_blocks(node):
        blocks = []
        for attr in ("body", "orelse", "finalbody"):
            value = getattr(node, attr, None)
            if value:
                blocks.append(value)
        for handler in getattr(node, "handlers", []) or []:
            blocks.append(handler.body)
        return blocks

    def _add_import(self, node, scope, filename):
        ctx = PythonASTContext(node, filename)
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                scope.define(Import(name, alias.name, ctx))
        else:
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    name = module + "::*"
                else:
                    name = alias.asname or alias.name
                scope.define(Import(name, module + "." + alias.name, ctx))

    def _add_assign(self, node, scope, filename, in_class, in_function):
        type_hint = _annotation(node)
        names = _target_names(node)
        for name in names:
            if in_class and not in_function and name.startswith("__"):
                continue
            symbol = Variable(name, type_hint,
                              PythonASTContext(node, filename))
            scope.define(symbol)

    def _add_class(self, node, scope, filename):
        bases = []
        for base in node.bases:
            try:
                bases.append(ast.unparse(base))
            except Exception:
                pass
        ctx = PythonASTContext(node, filename,
                               documentation=ast.get_docstring(node))
        symbol = Class(node.name, scope, bases or None, ctx)
        scope.define(symbol)
        self._walk(node.body, symbol, filename, in_class=True, in_function=False)

    def _add_function(self, node, scope, filename, in_class):
        decorators = []
        for decorator in node.decorator_list:
            try:
                decorators.append(ast.unparse(decorator))
            except Exception:
                pass

        if "staticmethod" in decorators:
            klass = StaticMethod
        elif "classmethod" in decorators:
            klass = ClassMethod
        elif in_class and node.name.startswith("__") and not node.name.endswith("__"):
            klass = PrivateMethod
        elif in_class:
            klass = Method
        else:
            klass = Function

        sig = self._signature(node)
        ctx = PythonASTContext(node, filename, signature=sig,
                               documentation=ast.get_docstring(node))
        symbol = klass(node.name, None, None, scope, ctx)
        scope.define(symbol)

        last_line = getattr(node, "end_lineno", None)
        for arg in self._argument_nodes(node.args):
            arg_ctx = PythonASTContext(arg, filename)
            if last_line is not None:
                # Argument contexts cover the function, like the legacy
                # scanner did, so the active scope resolves correctly.
                arg_ctx._node = node
            symbol.define(Argument(arg.arg, _annotation(arg), arg_ctx))

        self._walk(node.body, symbol, filename, in_class=False, in_function=True)

    @staticmethod
    def _argument_nodes(args):
        result = []
        for name in ("posonlyargs", "args", "kwonlyargs"):
            result.extend(getattr(args, name, []) or [])
        if args.vararg:
            result.append(args.vararg)
        if args.kwarg:
            result.append(args.kwarg)
        return result

    @staticmethod
    def _signature(node):
        args = []
        for arg in PythonASTScanner._argument_nodes(node.args):
            if arg.arg in ("self", "cls"):
                continue
            args.append(arg.arg)
        return "%s(%s)" % (node.name, ", ".join(args))
