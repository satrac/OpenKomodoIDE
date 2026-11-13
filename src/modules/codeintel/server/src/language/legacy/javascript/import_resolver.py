# Copyright 2017 ActiveState, Inc. All rights reserved.

"""JavaScript and NodeJS resolver for 'require()' statements."""

import logging
import os

from symbols import AbstractScope, AbstractStruct, AbstractModule
from language.common import Scope, Struct, Module, AbstractImportResolver
from language.legacy.javascript.stdlib import NODEJS_STDLIB_FILE

from db.model.helpers import fileExists, fetchSymbolInFile, fetchSymbolsInFile, fetchFilesInDirectory

log = logging.getLogger("codeintel.javascript.import_resolver")
#log.setLevel(logging.DEBUG)

class JavaScriptImportResolver(AbstractImportResolver):
    """Implementation of AbstractImportResolver for JavaScript."""

    def _importAllImportables(self, import_symbol, scope):
        """Imports into the given scope all importable module names from the
        directory specified in the given import symbol.
        This is only called for incomplete "require()" statements.
        @param import_symbol AbstractImport that specifies which modules to
                             import. Its name should end with "*".
        @param scope The AbstractScope to import modules into.
        """
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
        log.debug("Fetching all importable modules from '%s'", dirname)
        for filename in fetchFilesInDirectory(dirname, ".js"):
            if filename.endswith("/") and isinstance(self, NodeJSImportResolver) and fileExists(os.path.join(dirname, symbol_name, filename[:-1], "index.js")):
                filename = filename[:-1] # chop trailing '/' for most node_modules modules
            scope.define(Module(filename))
        # Also look in a "fake" environment variable for additional library
        # defines.
        # Note: even though NodeJS's "require()" is strictly defined to not
        # allow lib directories, consider them anyway.
        for dirname in self.env.get("JAVASCRIPTLIB", "").split(os.pathsep):
            if not dirname:
                continue
            log.debug("Fetching all importable modules from '%s'", os.path.join(dirname, symbol_name))
            for filename in fetchFilesInDirectory(os.path.join(dirname, symbol_name), ".js"):
                if filename.endswith("/") and isinstance(self, NodeJSImportResolver) and fileExists(os.path.join(dirname, symbol_name, filename[:-1], "index.js")):
                    filename = filename[:-1] # chop trailing '/' for most node_modules modules
                scope.define(Module(filename))

    def _importSymbol(self, module_name):
        """Searches the database for the given module name and returns its
        associated AbstractSymbol for importing.
        @param module_name Full string module name of the module to import.
        """
        # Search the database for the module and its 'exports' variable.
        path = os.path.normpath(module_name + ".js")
        log.debug("Searching for module '%s' and 'exports' variable", path)
        exports = fetchSymbolInFile(path, "exports", AbstractStruct)
        if not exports:
            log.debug("Unable to require module '%s'; module does not exist in the database" % module_name)
            return None
        # Since each entry in "exports" is likely an AbstractStruct whose type
        # needs to be resolved, construct a scope that contains all of the
        # module's top-level symbols to aid in resolution.
        module = Scope()
        for symbol in fetchSymbolsInFile(path):
            module.define(symbol)
        # Copy the contents of the 'exports' variable to a new object with fully
        # resolved members.
        symbol = Struct(module_name.split("/")[-1], None)
        if not exports.members and exports.type and module.resolve(exports.type):
            # Properly handle variables assigned to "exports".
            exports = module.resolve(exports.type)
        for member in list(exports.members.values()):
            if member.type and module.resolve(member.type):
                member = module.resolve(member.type)
                # TODO: "exports.foo = bar.baz.quux"
            symbol.define(member)
        log.debug("Ultimately found symbol %r", symbol)
        return symbol

    def resolveImport(self, import_symbol, scope):
        """Implementation of AbstractImportResolver.resolveImport()."""
        log.debug("Attempting to import '%s'", import_symbol.type)
        if not import_symbol.name.endswith("*"):
            exports = None
            for dirname in self.env.get("JAVASCRIPTLIB", "").split(os.pathsep):
                if not dirname:
                    continue
                module_name = "%s/%s" % (dirname, import_symbol.type)
                exports = self._importSymbol(module_name)
                if exports:
                    break
            if not exports:
                module_name = "%s/%s" % (os.path.dirname(self.filename), import_symbol.type)
                exports = self._importSymbol(module_name)
            symbol = Struct(import_symbol.name, None)
            if isinstance(exports, AbstractScope):
                symbol.merge(exports)
            scope.define(symbol);
        else:
            # This only happens for incomplete "require()" statements.
            self._importAllImportables(import_symbol, scope)

class NodeJSImportResolver(JavaScriptImportResolver):
    """Implementation of AbstractImportResolver for NodeJS."""

    def _importCoreModule(self, module_name):
        """Searches the database for the given core module name and returns its
        associated AbstractSymbol for importing.
        @param module_name String core module name.
        """
        log.debug("Searching for module in stdlib")
        module = fetchSymbolInFile(NODEJS_STDLIB_FILE, module_name, AbstractModule, False)
        if module:
            exports = module.resolveMember("exports")
        else:
            # Consider modules in JAVASCRIPTLIB as core modules for now.
            for dirname in self.env.get("JAVASCRIPTLIB", "").split(os.pathsep):
                lib_module_name = "%s/%s" % (dirname, module_name)
                exports = self._importSymbol(lib_module_name)
                if exports:
                    break
            if not exports:
                return None
        if exports.type:
            exports = module.resolveMember(exports.type)
        return [member for member in list(exports.members.values())]

    def _importFile(self, module_name):
        """Searches the database for the given module name (considered to be a
        file) and returns its associated AbstractSymbol for importing.
        According to https://nodejs.org/api/modules.html#modules_all_together,
          1. If X is a file, load X as JavaScript text.
          2. If X.js is a file, load X.js as JavaScript text.
          3. If X.json is a file, load X.json as JavaScript text.
        The AbstractSymbol returned is the "exports" or "module.exports"
        variable for NodeJS files and the entire JSON object for JSON files.
        @param module_name Full string module name of the module to import.
        """
        log.debug("Searching for module in '%s'", module_name)
        exports = fetchSymbolInFile(module_name, "exports", AbstractStruct, False)
        if not exports:
            module_name = module_name + ".js"
            exports = fetchSymbolInFile(module_name, "exports", AbstractStruct, False)
        if exports:
            # Since each entry in "exports" is likely an AbstractStruct whose type
            # type needs to be resolved, construct a scope that contains all of
            # the module's top-level symbols to aid in resolution.
            module = Scope()
            for symbol in fetchSymbolsInFile(module_name):
                module.define(symbol)
            # Create a copy of the 'exports' variable in the current scope with
            # the appropriate name and with fully resolved members.
            if not exports.members and exports.type and module.resolve(exports.type):
                # Properly handle variables assigned to "exports".
                exports = module.resolve(exports.type)
            symbols = []
            for member in list(exports.members.values()):
                if member.type and module.resolve(member.type):
                    member = module.resolve(member.type)
                    # TODO: "exports.foo = bar.baz.quux"
                symbols.append(member)
            return symbols
        else:
            return fetchSymbolsInFile(module_name[:-3] + ".json") # remove .js added earlier

    def _importDirectory(self, module_name):
        """Searches the database for the given module name (considered to be a
        directory) and returns its associated AbstractSymbol for importing.
        According to https://nodejs.org/api/modules.html#modules_all_together,
            1. If X/package.json is a file,
                a. Parse X/package.json, and look for "main" field.
                b. let M = X + (json main field)
                c. LOAD_AS_FILE(M)
            2. If X/index.js is a file, load X/index.js as JavaScript text.
            3. If X/index.json is a file, load X/index.json as a JavaScript
               object.
        The AbstractSymbol returned is the "exports" or "module.exports"
        variable for '.js' files and the entire JSON object for '.json' files.
        """
        log.debug("Searching for module in '%s'", module_name)
        symbol = fetchSymbolInFile(os.path.join(module_name, "package.json"), "main")
        if symbol:
            module_name = os.path.normpath(os.path.join(module_name, symbol.type)) # JSON scanner stores module_name in type
        else:
            module_name = os.path.join(module_name, "index")
        return self._importFile(module_name)

    def resolveImport(self, import_symbol, scope):
        """Implementation of AbstractImportResolver.resolveImport().
        According to https://nodejs.org/api/modules.html#modules_all_together,
            require(X) from module at path Y
            1. If X is a core module, return core module.
            2. If X begins with './' or '/' or '../'
                a. LOAD_AS_FILE(Y + X)
                b. LOAD_AS_DIRECTORY(Y + X)
            3. LOAD_NODE_MODULES(X, dirname(Y)) while Y = dirname(Y)
        """
        log.debug("Attempting to import '%s'", import_symbol.type)
        if not import_symbol.name.endswith("*"):
            exports = self._importCoreModule(import_symbol.type)
            if not exports:
                dirname = os.path.dirname(self.filename)
                if import_symbol.type.startswith(".") or import_symbol.type.startswith("/"):
                    module_name = os.path.normpath(os.path.join(dirname, import_symbol.type))
                    exports = self._importFile(module_name) or self._importDirectory(module_name)
                else:
                    while not exports:
                        module_name = os.path.join(dirname, "node_modules", import_symbol.type)
                        exports = self._importFile(module_name) or self._importDirectory(module_name)
                        if os.path.dirname(dirname) == dirname:
                            break # at root
                        dirname = os.path.dirname(dirname)
            symbol = Struct(import_symbol.name, None)
            if exports:
                for member in exports:
                    symbol.define(member)
                log.debug("Ultimately found symbol %r", symbol)
            else:
                log.debug("Unable to require module '%s'; module does not exist in the database" % import_symbol.type)
            scope.define(symbol)
        else:
            # This only happens for incomplete "require()" statements.
            self._importAllImportables(import_symbol, scope)
