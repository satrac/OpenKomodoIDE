# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Ruby resolver for 'require' statements."""

import logging
import os

from symbols import AbstractScope, AbstractModule
from language.common import Module, AbstractImportResolver
from language.legacy.ruby.stdlib import RUBY_STDLIB_FILE

from db.model.helpers import fetchSymbolInFile, fetchSymbolsInFile, fetchFilesInDirectory

log = logging.getLogger("codeintel.ruby.import_resolver")
#log.setLevel(logging.DEBUG)

class RubyImportResolver(AbstractImportResolver):
    """Implementation of AbstractImportResolver for Ruby."""

    def _getImportableModules(self, module_name, env={}):
        """Retrieves from the database all importable Ruby modules that start
        with the given module name.
        @param module_name Optional module name prefix.
        @param env Optional dictionary of environment variables to use when
                   looking for imports.
        @return list of AbstractModules
        """
        symbols = []
        log.debug("Searching for module in stdlib")
        if not module_name:
            for symbol in fetchSymbolsInFile(RUBY_STDLIB_FILE, AbstractModule):
                symbols.append(symbol)
        else:
            try:
                name_parts = module_name.split("/")
                symbol = fetchSymbolInFile(RUBY_STDLIB_FILE, name_parts[0], AbstractModule)
                for name_part in name_parts[1:]:
                    symbol = symbol.resolveMember(name_part)
                if symbol and isinstance(symbol, AbstractScope):
                    for member in list(symbol.members.values()):
                        if isinstance(member, AbstractModule):
                            symbols.append(member)
            except AttributeError:
                pass
        for dirname in env.get("RUBYLIB", "").split(os.pathsep):
            if not dirname:
                continue
            log.debug("Searching for module in '%s'", os.path.join(dirname, module_name))
            for filename in fetchFilesInDirectory(os.path.join(dirname, module_name), ".rb"):
                symbols.append(Module(filename))
        return symbols

    def resolveImport(self, import_symbol, scope):
        """Implementation of AbstractScanner.resolveImport()."""
        if import_symbol.name.startswith(".") and "/" not in import_symbol.name:
            log.debug("Attempting to include '%s'", import_symbol.type)
            # "include Foo"
            module = scope.resolve(import_symbol.type)
            if isinstance(module, AbstractScope):
                scope.merge(module)
        elif import_symbol.type and not import_symbol.name.endswith("/*"):
            log.debug("Attempting to import '%s'", import_symbol.type)
            # Search the database for the module and import its symbols.
            symbols = None
            for dirname in self.env.get("RUBYLIB", "").split(os.pathsep):
                if not dirname:
                    continue
                log.debug("Searching for module in '%s'", os.path.join(dirname, import_symbol.type))
                symbols = fetchSymbolsInFile(os.path.join(dirname, import_symbol.type + ".rb"))
                if symbols:
                    break
            if not symbols:
                # Fall back on the Ruby stdlib.
                try:
                    log.debug("Searching for module in stdlib")
                    name_parts = import_symbol.type.split("/")
                    symbol = fetchSymbolInFile(RUBY_STDLIB_FILE, name_parts[0], AbstractModule)
                    for name_part in name_parts[1:]:
                        symbol = symbol.resolveMember(name_part)
                    symbols = [member for member in list(symbol.members.values()) if not isinstance(member, AbstractModule)]
                except AttributeError:
                    pass
            if symbols:
                for symbol in symbols:
                    if not isinstance(scope.resolveMember(symbol.name), AbstractScope) or not isinstance(symbol, AbstractScope):
                        scope.define(symbol)
                    else:
                        scope.resolveMember(symbol.name).merge(symbol)
                log.debug("Ultimately found symbol %r", symbol)
            else:
                log.debug("Unable to require module '%s'; module does not exist in the database" % import_symbol.type)
        elif import_symbol.name.endswith("/*"):
            # This only happens for incomplete, non-empty "require" statements.
            log.debug("Attempting to import '%s'", import_symbol.type)
            symbol_name = import_symbol.name.rstrip("/*")
            if symbol_name:
                name_parts = symbol_name.split("/")
                symbol = Module(name_parts[0])
                scope.define(symbol)
                for name in name_parts[1:]:
                    symbol = Module(name, symbol)
                    symbol.enclosingScope.define(symbol)
                scope = symbol
            for symbol in self._getImportableModules(import_symbol.type or "", self.env):
                scope.define(symbol)
        else:
            # This only happens in an empty "require" statement. Import all
            # importable modules.
            log.debug("Attempting to import '%s'", import_symbol.type)
            for symbol in self._getImportableModules("", self.env):
                scope.define(symbol)
