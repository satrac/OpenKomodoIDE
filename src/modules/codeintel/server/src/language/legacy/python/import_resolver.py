# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Python 2 and Python 3 resolver for 'import' statements."""

import logging
import os

from symbols import AbstractScope, AbstractSymbol, AbstractModule, AbstractImport
from language.common import Module, Import, AbstractImportResolver
from language.legacy.python.stdlib import PYTHON2_STDLIB_FILE, PYTHON3_STDLIB_FILE

from db.model.helpers import fileExists, fetchSymbolInFile, fetchSymbolsInFile, fetchFilesInDirectory
from db.model.symbol import File as DBFile, Symbol as DBSymbol

log = logging.getLogger("codeintel.python.import_resolver")
#log.setLevel(logging.DEBUG)

class PythonImportResolver(AbstractImportResolver):
    """Implementation of AbstractImportResolver for Python 2."""
    PYTHON_STDLIB_FILE = PYTHON2_STDLIB_FILE

    def resolveImport(self, import_symbol, scope):
        """Implementation of AbstractImportResolver.resolveImport()."""
        if not isinstance(scope, AbstractSymbol) or not scope.ctx or scope.ctx.filename != self.PYTHON_STDLIB_FILE:
            # First, update PYTHONPATH to include the directory the filename is
            # in, since `from` and `import` look in the file's directory first.
            env = self.env.copy()
            env["PYTHONPATH"] = "%s%s%s" % (os.path.dirname(self.filename), os.pathsep, env.get("PYTHONPATH", ""))

            # Check for an empty import.
            if import_symbol.name.endswith("*") and not import_symbol.type:
                # This only happens when requesting completions for incomplete
                # "import foo" statements. Import all available modules from
                # PYTHONPATH and from Python's stdlib.
                for dirname in env.get("PYTHONPATH", "").split(os.pathsep):
                    if not dirname:
                        continue
                    log.debug("Fetching all importable modules from '%s'", dirname)
                    for name in fetchFilesInDirectory(dirname, ".py"):
                        if not name.endswith("/"): # not os.path.sep
                            if name != "__init__":
                                scope.define(Module(name))
                        else:
                            if fileExists(os.path.join(dirname, name, "__init__.py")):
                                scope.define(Module(name[:-1]))
                log.debug("Fetching all importable modules from stdlib")
                for symbol in fetchSymbolsInFile(self.PYTHON_STDLIB_FILE, AbstractModule):
                    scope.define(symbol)
                return

            # Now try to resolve the import.
            # The Python scanner never produces an import target that contains a
            # os.pathsep (':' or ';') character, but the import resolver does
            # when it encounters imports from within imported modules. Python
            # 2's implicit relative imports causes ambiguity in that case, so
            # multiple targets must be tried.
            for import_target in import_symbol.type.split(os.pathsep):
                log.debug("Attempting to import '%s'", import_target)
                name_parts = import_target.split(".")
                symbol = None
                # Search the PYTHONPATH.
                for dirname in env.get("PYTHONPATH", "").split(os.pathsep):
                    if not dirname:
                        continue
                    log.debug("Searching for module in '%s'", dirname)
                    i = 0
                    while i < len(name_parts) and fileExists(os.path.join(dirname, os.path.sep.join(name_parts[:i+1]), "__init__.py")):
                        i += 1 # match as many package directories as possible
                    filename = None
                    if fileExists(os.path.join(dirname, os.path.sep.join(name_parts[:i+1]) + ".py")):
                        filename = os.path.join(dirname, os.path.sep.join(name_parts[:i+1]) + ".py")
                    else:
                        if not import_symbol.name.endswith("*"):
                            # Backtrack by one in name_parts since name_parts[i]
                            # points to the symbol in '__init__.py' to initially
                            # look for instead of being a '.py' file.
                            i -= 1
                        if i < 0:
                            continue # next PYTHONPATH entry
                        if fileExists(os.path.join(dirname, os.path.sep.join(name_parts[:i+1]), "__init__.py")):
                            filename = os.path.join(dirname, os.path.sep.join(name_parts[:i+1]), "__init__.py")
                    if filename:
                        if len(name_parts) > i + 1:
                            # Resolve each remaining member.
                            log.debug("Found module '%s'; searching for %r", filename, name_parts[i + 1:])
                            symbol = fetchSymbolInFile(filename, name_parts[i + 1])
                            if len(name_parts) > i + 2:
                                for name_part in name_parts[i + 2:]:
                                    if not isinstance(symbol, AbstractScope):
                                        symbol = None
                                        break
                                    symbol = symbol.resolveMember(name_part)
                        else:
                            # Resolve the file as a module with symbols.
                            log.debug("Found module '%s'; reading its symbols", filename)
                            symbol = Module(name_parts[0])
                            for name_part in name_parts[1:]:
                                symbol = Module(name_part, symbol)
                                symbol.enclosingScope.define(symbol)
                            for member in fetchSymbolsInFile(filename):
                                if isinstance(member, AbstractImport):# and not member.type.startswith(import_target):
                                    # Python 2 allows for implicit relative
                                    # imports. Due to the ambiguity, list the
                                    # two places the import target could exist
                                    # at. This import resolver will attempt both
                                    # as necessary.
                                    member = Import(member.name, "%s.%s%s%s" % (import_target, member.type, os.pathsep, member.type))
                                symbol.define(member)
                            if filename.endswith("__init__.py"):
                                # Include immediate sub-modules.
                                log.debug("Reading all sub-modules from '%s' too", os.path.dirname(filename))
                                for name in fetchFilesInDirectory(os.path.dirname(filename), ".py"):
                                    if not name.endswith("/"): # not os.path.sep
                                        if name != "__init__":
                                            symbol.define(Module(name, symbol))
                                            symbol.resolveMember(name).define(Import("*", "%s.%s" % (import_target, name)))
                                    else:
                                        if fileExists(os.path.join(dirname, name, "__init__.py")):
                                            symbol.define(Module(name[:-1], symbol))
                        break
                # Search the Python stdlib.
                if not symbol:
                    log.debug("Searching for module in stdlib")
                    symbol = fetchSymbolInFile(self.PYTHON_STDLIB_FILE, name_parts[0])
                    if len(name_parts) > 1:
                        # Resolve each remaining member.
                        for name_part in name_parts[1:]:
                            if not isinstance(symbol, AbstractScope):
                                symbol = None
                                break
                            symbol = symbol.resolveMember(name_part)
                if symbol:
                    break
        else:
            # Optimization: when resolving imports from the stdlib, no need to
            # search PYTHONPATH and friends.
            log.debug("Searching for module in stdlib")
            top_scope = scope
            while top_scope.enclosingScope:
                top_scope = top_scope.enclosingScope
            name_parts = import_symbol.type.split(".")
            symbol = top_scope.resolveMember(name_parts[0])
            if len(name_parts) > 1:
                # Resolve each remaining member.
                for name_part in name_parts[1:]:
                    if not isinstance(symbol, AbstractScope):
                        symbol = None
                        break
                    symbol = symbol.resolveMember(name_part)

        if import_symbol.name.endswith("*"):
            if isinstance(symbol, AbstractScope):
                log.debug("Ultimately found symbol %r; importing all of its members", symbol)
                scope.merge(symbol)
        else:
            if not symbol:
                log.debug("Module not found; creating empty one instead")
                symbol = Module(name_parts[0])
                for name_part in name_parts[1:]:
                    symbol = Module(name_part, symbol)
                    symbol.enclosingScope.define(symbol)

            log.debug("Ultimately found symbol %r", symbol)
            from_import = "." in import_symbol.type and not import_symbol.type.startswith("%s." % import_symbol.name)

            # If not importing the symbol directly (e.g. "import foo.bar" as
            # opposed to "from foo import bar"), import the top-level module
            # that contains the specified symbol.
            if not from_import:
                while isinstance(symbol.enclosingScope, AbstractModule):
                    symbol = symbol.enclosingScope

            # Finally, define the imported symbol in scope.
            if from_import and import_symbol.name == import_symbol.type.split(".")[-1]:
                # "from foo import bar"
                log.debug("Importing symbol %r", symbol)
                scope.define(symbol)
            else:
                # "import foo.bar", "import foo.bar as baz", or
                # "from foo import bar as baz"
                log.debug("Importing symbol %r as '%s'", symbol, import_symbol.name)
                scope.define(symbol, import_symbol.name)

class Python3ImportResolver(PythonImportResolver):
    """Implementation of AbstractImportResolver for Python 3."""
    PYTHON_STDLIB_FILE = PYTHON3_STDLIB_FILE
