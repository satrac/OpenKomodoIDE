# Copyright 2017 ActiveState, Inc. All rights reserved.

"""PHP resolver for 'include' and 'require' statements."""

import logging
import os

from symbols import AbstractScope, AbstractClass, AbstractNamespace
from language.common import Class, Module, AbstractImportResolver, SymbolResolver

from db.model.helpers import fetchSymbolInFile, fetchSymbolsInFile, fetchSymbolsInDirectories, fetchFilesInDirectory, fetchSymbolsInDirectoriesMatchingPath

log = logging.getLogger("codeintel.php.import_resolver")

class PHPImportResolver(AbstractImportResolver):
    """Implementation of AbstractImportResolver for PHP."""

    def __init__(self, filename, env={}):
        super(PHPImportResolver, self).__init__(filename, env)
        self._symbol_resolver = SymbolResolver(import_resolver=self)

    def resolveImport(self, import_symbol, scope):
        """Implementation of AbstractImportResolver.resolveImport()."""
        if not import_symbol.name.endswith("*"):
            if import_symbol.name.endswith(".php"):
                # Note: Since all top-level symbols from PHPLIB are
                # auto-imported, do not process manual imports, as it would
                # duplicate work.
                return
                dirname = os.path.dirname(self.filename)
                log.debug("Fetching all importable symbols from '%s/%s'", dirname, import_symbol.type)
                for symbol in fetchSymbolsInFile(os.path.join(dirname, import_symbol.type)):
                    if not isinstance(scope.resolveMember(symbol.name), AbstractScope) or not isinstance(symbol, AbstractScope):
                        scope.define(symbol)
                    else:
                        scope.resolveMember(symbol.name).merge(symbol)
                # Also look in a "fake" environment variable for additional library
                # defines.
                for dirname in self.env.get("PHPLIB", "").split(os.pathsep):
                    if not dirname:
                        continue
                    log.debug("Fetching all importable symbols from '%s/%s'", dirname, import_symbol.type)
                    for symbol in fetchSymbolsInFile(os.path.join(dirname, import_symbol.type)):
                        if not isinstance(scope.resolveMember(symbol.name), AbstractScope) or not isinstance(symbol, AbstractScope):
                            scope.define(symbol)
                        else:
                            scope.resolveMember(symbol.name).merge(symbol)
            else:
                log.debug("Looking up symbol '%s' in scope %r", import_symbol.type, scope)
                symbol = self._symbol_resolver.resolve(scope, import_symbol.type)
                if not symbol:
                    # Mimic autoloading by attempting to find the class or
                    # namespace in all applicable directories and files.
                    dirnames = [os.path.dirname(self.filename)]
                    for dirname in self.env.get("PHPLIB", "").split(os.pathsep):
                        if dirname:
                            dirnames.append(dirname)
                    name_parts = import_symbol.type.split("\\")
                    matchable = os.path.sep.join(name_parts)
                    for symbol in fetchSymbolsInDirectoriesMatchingPath(dirnames, matchable + ".php", name_parts[-1], AbstractClass, 1):
                        break # assume the first class found is the desired one
                    if not symbol:
                        # Attempt a namespace lookup, which is more expensive.
                        namespace = None
                        for symbol in fetchSymbolsInDirectories(dirnames, name_parts[0], AbstractNamespace, ".php"):
                            for name_part in name_parts[1:]:
                                symbol = symbol.resolveMember(name_part)
                                if not symbol:
                                    break
                                if isinstance(symbol, AbstractClass):
                                    # Prefer class definitions over namespace
                                    # definitions.
                                    break
                            if isinstance(symbol, AbstractClass):
                                break # prefer class defs over namespace defs
                            elif isinstance(symbol, AbstractNamespace):
                                namespace = symbol # store as a fallback
                        if not symbol and namespace:
                            symbol = namespace
                    if not symbol:
                        # Give up.
                        log.debug("Symbol not found; creating dummy class.")
                        scope.define(Class(import_symbol.type.split("\\")[-1], scope))
                        return
                if import_symbol.name == import_symbol.type.split("\\")[-1]:
                    if isinstance(symbol, AbstractNamespace):
                        log.debug("Importing symbols in %r", symbol)
                        scope.merge(symbol)
                    else:
                        log.debug("Importing symbol %r", symbol)
                        scope.define(symbol)
                else:
                    log.debug("Importing symbol %r as '%s'", symbol, import_symbol.name)
                    scope.define(symbol, import_symbol.name)
        else:
            # This only happens for incomplete "include" or "require"
            # statements.
            # TODO: distinguish between include/require and use (perhaps look for '\\'?)
            symbol_name = import_symbol.name.rstrip("/*")
            if symbol_name:
                name_parts = symbol_name.split("/")
                symbol = Module(name_parts[0])
                scope.define(symbol)
                for name in name_parts[1:]:
                    symbol = Module(name, symbol)
                    symbol.enclosingScope.define(symbol)
                scope = symbol
            dirname = os.path.normpath(import_symbol.type)
            for filename in fetchFilesInDirectory(dirname, ".php", strip_ext=False):
                scope.define(Module(filename))
            # Also look in a "fake" environment variable for additional library
            # defines.
            for dirname in self.env.get("PHPLIB", "").split(os.pathsep):
                if not dirname:
                    continue
                for filename in fetchFilesInDirectory(os.path.join(dirname, symbol_name), ".php", strip_ext=False):
                    scope.define(Module(filename))
