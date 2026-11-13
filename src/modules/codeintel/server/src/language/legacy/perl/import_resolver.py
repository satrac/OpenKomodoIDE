# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Perl resolver for 'use', 'require', and 'no' statements."""

import logging
import os

from symbols import AbstractScope, AbstractSymbol, AbstractFunction, AbstractClass, AbstractModule
from language.common import Scope, Class, Module, AbstractImportResolver, AbstractScannerContext
from language.legacy.perl.stdlib import PERL_STDLIB_FILE

from db.model.helpers import fetchSymbolInFile, fetchSymbolsInFile, fetchFilesInDirectory

log = logging.getLogger("codeintel.perl.import_resolver")
#log.setLevel(logging.DEBUG)

class DummyScannerContext(AbstractScannerContext):
    """Implementation of AbstractScannerContext for imported Perl modules.
    Perl modules cannot be imported directly since a statement like
    "use foo::bar" should only import "foo::bar", not "foo::baz" or "foo::quux"
    if they exist. Thus, empty namespaces need to be created, and then a module
    will need to be "merged" them. For example, given "use foo::bar", create a
    "foo" namespace, "bar" class, and then merge the contents of the imported
    "bar" class with the created "bar" class. (If the imported "bar" class were
    to be defined directly in "foo", bar.enclosingScope would not point to the
    correct "foo". Redefining bar.enclosingScope will not work either.)
    Since a new class needs to be created, it should share the same context as
    the imported class. However, the imported class' ctx field is an
    AbstractSymbolContext, not an AbstractScannerContext. Therefore, this dummy
    class needs to be created.
    """
    def __init__(self, ctx):
        self._ctx = ctx

    @property
    def filename(self):
        """Implementation of AbstractSymbolContext.filename."""
        return self._ctx.filename

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        return self._ctx.line

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation."""
        return self._ctx.documentation

    @property
    def signature(self):
        """Implementation of AbstractSymbolContext.signature."""
        return self._ctx.signature

    def contains(self, position_or_line):
        """Implementation of AbstractScannerContext.contains."""
        return False # N/A

class PerlImportResolver(AbstractImportResolver):
    """Implementation of AbstractImportResolver for Perl."""

    def _getImportableModules(self, module_name='', env={}):
        """Retrieves from the database all importable Perl modules that start
        with the given module name.
        @param module_name Optional module name prefix.
        @param env Optional dictionary of environment variables to use when
                   looking for imports.
        """
        symbols = []
        if not module_name:
            log.debug("Fetching all importable modules from stdlib")
            for symbol in fetchSymbolsInFile(PERL_STDLIB_FILE, AbstractModule):
                symbols.append(symbol)
        else:
            try:
                name_parts = module_name.split("::")
                symbol = fetchSymbolInFile(PERL_STDLIB_FILE, name_parts[0], AbstractModule)
                for name_part in name_parts[1:]:
                    symbol = symbol.resolveMember(name_part)
                for member in list(symbol.members.values()):
                    if isinstance(member, AbstractModule):
                        symbols.append(member)
            except AttributeError:
                pass
        for dirname in env.get("PERL5LIB", "").split(os.pathsep):
            if not dirname:
                continue
            log.debug("Fetching all importable modules from '%s'", dirname)
            for filename in fetchFilesInDirectory(os.path.join(dirname, module_name.replace("::", os.path.sep)), ".pm"):
                symbols.append(Module(filename.rstrip("/")))
        return symbols

    def resolveImport(self, import_symbol, scope):
        """Implementation of AbstractImportResolver.resolveImport()."""
        log.debug("Attempting to import '%s'", import_symbol.type)
        top_level_import = not hasattr(self, "_imported")
        if top_level_import:
            # When it comes to Perl imports, imports are imported immediately,
            # as opposed to on-demand. This allows, for example, the LWP module
            # to auto-import LWP::UserAgent, which in turn auto-imports HTTP,
            # LWP::Protocol, and friends. If this did not happen, HTTP would
            # only be imported when fetching completions for LWP::UserAgent.
            # Regardless, due to immediate imports, it's often the case that
            # there will be import recursion. Track imports in order to prevent
            # this.
            setattr(self, "_imported", {})
        # First, update PERL5LIB to include the directory the given filename is
        # in, since `from` and `import` look in the file's directory first.
        env = self.env.copy()
        env["PERL5LIB"] = "%s%s%s" % (os.path.dirname(self.filename), os.pathsep, env.get("PERL5LIB", ""))
        # Now try to resolve the import, by identifying the module to import and
        # retrieving its package.
        symbol = None
        if import_symbol.type:
            package_name = import_symbol.type.strip("'\"")
            if package_name.endswith(".pm"):
                package_name = package_name[:-3]
            if package_name not in getattr(self, "_imported"):
                getattr(self, "_imported")[package_name] = True
            else:
                return # already done/attempted
            for dirname in env.get("PERL5LIB", "").split(os.pathsep):
                if not dirname:
                    continue
                log.debug("Searching for module in '%s'", dirname)
                if "::" in package_name:
                    try:
                        name_parts = package_name.split("::")
                        symbol = fetchSymbolInFile(os.path.join(dirname, package_name.replace("::", os.path.sep) + ".pm"), name_parts[0], AbstractModule)
                        for name_part in name_parts[1:]:
                            symbol = symbol.resolveMember(name_part)
                        if symbol and not isinstance(symbol, (AbstractClass, AbstractModule)):
                            # use foo::bar 'baz';
                            scope.define(symbol)
                            if "::" in package_name:
                                package_name = "::".join(name_parts[:-1]) # update
                    except AttributeError:
                        pass
                else:
                    symbol = fetchSymbolInFile(os.path.join(dirname, package_name.replace("::", os.path.sep) + ".pm"), package_name, AbstractClass)
                if symbol:
                    break
            if not symbol:
                log.debug("Searching for module in stdlib")
                # Fall back on the Perl stdlib for the module.
                # Note that class names are fully-qualified (e.g.
                # "foo::bar::baz") and not namespaced.
                try:
                    name_parts = package_name.split("::")
                    symbol = fetchSymbolInFile(PERL_STDLIB_FILE, name_parts[0], AbstractModule)
                    for name_part in name_parts[1:-1]:
                        symbol = symbol.resolveMember(name_part)
                    if len(name_parts) > 1:
                        # Since "use foo::bar::baz;" and "use foo::bar 'baz';"
                        # both translate to Import(..., type="foo::bar::baz"),
                        # there is some ambiguity. Attempt to resolve it.
                        if symbol.resolveMember(name_parts[-1]):
                            # For the "use foo::bar::baz;" case, the
                            # "foo::bar::baz" class exists in the "baz" module.
                            # Resolve both.
                            symbol = symbol.resolveMember(name_parts[-1]).resolveMember(package_name)
                        else:
                            # For the "use foo::bar 'baz';" case, "baz" will not
                            # resolve, since the "foo::bar" class exists in the
                            # "bar" module. Resolve "foo::bar" first, then
                            # "baz", then define "baz" in the current scope.
                            if "::" in package_name:
                                package_name = package_name[:package_name.rfind("::")] # update
                            symbol = symbol.resolveMember(package_name).resolveMember(name_parts[-1])
                            if symbol:
                                scope.define(symbol)
                    else:
                        symbol = symbol.resolveMember(package_name)
                    if import_symbol.name.endswith("*") and isinstance(symbol, AbstractClass) and symbol.enclosingScope:
                        # Also import sub-modules/packages into the package.
                        for member in list(symbol.enclosingScope.members.values()):
                            if isinstance(member, AbstractClass) and symbol.name == member.name:
                                # In Perl, a module often contains a class of
                                # the same name with the same contents. Do not
                                # include such classes, as they tend to
                                # interfere with subsequent scope resolutions.
                                continue
                            symbol.define(member)
                except AttributeError:
                    pass
            if not symbol:
                # Fall back on a directory search.
                symbols = self._getImportableModules(import_symbol.type, env)
                if symbols:
                    symbol_name = import_symbol.name.rstrip(":*")
                    if symbol_name:
                        name_parts = symbol_name.split("::")
                        symbol = Module(name_parts[0])
                        scope.define(symbol)
                        for name in name_parts[1:]:
                            symbol = Module(name, symbol)
                            symbol.enclosingScope.define(symbol)
                        scope = symbol
                    for symbol in symbols:
                        scope.define(symbol)
                    return
                log.debug("Unable to import module '%s'; module does not exist in the database" % import_symbol.type)
                # At least make it available since it was imported.
                symbol = Class(package_name.split("::")[-1])
            log.debug("Ultimately found symbol %r", symbol)
            # Import all module members if necessary.
            if import_symbol.name.endswith("**"):
                log.debug("Importing all members")
                for member in list(symbol.members.values()): # TODO: only exportables
                    if isinstance(member, (AbstractModule, AbstractClass, AbstractFunction)):
                        scope.define(member)
            # Merge module with current scope.
            name_parts = package_name.split("::")
            if isinstance(scope, AbstractSymbol) and scope.name == name_parts[0]:
                scope = scope.enclosingScope
            elif scope.resolve(name_parts[0]):
                scope = scope.resolve(name_parts[0]).enclosingScope
                if not scope:
                    log.warn("Enclosing scope for %r.%s unexpectedly None", scope, name_parts[0])
                    return
            else:
                while scope.enclosingScope and scope.enclosingScope.enclosingScope:
                    scope = scope.enclosingScope
            for i in range(0, len(name_parts)):
                name_part = name_parts[i]
                if not scope.resolveMember(name_part):
                    if package_name != "::".join(name_parts[0:i+1]):
                        scope.define(Module(name_part, scope))
                    else:
                        scope.define(Class(name_part, scope, None, DummyScannerContext(symbol.ctx)))
                elif isinstance(scope.resolveMember(name_part), AbstractModule) and package_name == "::".join(name_parts[0:i+1]) and i > 0:
                    # Since a module often contains a class of the same name,
                    # replace the module with the class since imports tend to
                    # import the class object, not the module object. Only do
                    # this for non-top-level modules (e.g. for LWP::Simple but
                    # not LWP).
                    scope.members[name_part] = Class(name_part, scope, None, DummyScannerContext(symbol.ctx))
                scope = scope.resolveMember(name_part)
            scope.merge(symbol)
            self.resolveImports(scope)
            if top_level_import:
                # Done tracking imports in order to avoid import recursion.
                delattr(self, "_imported")
        else:
            # This only happens in empty "use", "require", or "no" statements.
            for symbol in self._getImportableModules("", env):
                scope.define(symbol)
