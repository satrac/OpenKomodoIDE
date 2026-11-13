# Copyright 2017 ActiveState, Inc. All rights reserved.

"""CSS/Less/SCSS resolver for various symbols and @import directives."""

import logging
import os
import re

from symbols import AbstractVariable, AbstractCSSClass, AbstractCSSId
from language.common import AbstractImportResolver

from db.model.helpers import fetchSymbolsInFile, fetchFilesInDirectory

log = logging.getLogger("codeintel.css.import_resolver")
#log.setLevel(logging.DEBUG)

class CSSImportResolver(AbstractImportResolver):
    """Implementation of AbstractImportResolver for CSS/Less/SCSS."""

    def _classInDirectory(self, dirname):
        """Yields a simple CSS class at a time from a given directory of CSS
        files.
        @param dirname The directory to scan for CSS files in and fetch CSS
                       class symbols from.
        """
        log.debug("Searching for classes in '%s'", dirname)
        for filename in fetchFilesInDirectory(dirname, ".css", strip_ext=False):
            filename = os.path.join(dirname, filename)
            if not filename.endswith("/"): # not os.path.sep
                for symbol in fetchSymbolsInFile(filename, AbstractCSSClass):
                    if re.match(r"^\.\w+$", symbol.name):
                        yield symbol
            else:
                for symbol in self._classInDirectory(filename):
                    yield symbol

    def _idInDirectory(self, dirname):
        """Yields a simple CSS id at a time from a given directory of CSS files.
        @param dirname The directory to scan for CSS files in and fetch CSS
                       id symbols from.
        """
        log.debug("Searching for ids in '%s'", dirname)
        for filename in fetchFilesInDirectory(dirname, ".css", strip_ext=False):
            filename = os.path.join(dirname, filename)
            if not filename.endswith("/"): # not os.path.sep
                for symbol in fetchSymbolsInFile(filename, AbstractCSSId):
                    if re.match(r"^#\w+$", symbol.name):
                        yield symbol
            else:
                for symbol in self._idInDirectory(filename):
                    yield symbol

    def _variableInDirectory(self, dirname, ext):
        """Yields a Less or SCSS variable at a time from a given directory of
        Less/SCSS files.
        @param dirname The directory to scan for Less/SCSS files in and fetch
                       variable symbols from.
        @param ext The extension for LESS or SCSS files to search.
        """
        log.debug("Searching for variables in '%s'", dirname)
        for filename in fetchFilesInDirectory(dirname, ext, strip_ext=False):
            filename = os.path.join(dirname, filename)
            if not filename.endswith("/"): # not os.path.sep
                for symbol in fetchSymbolsInFile(filename, AbstractVariable):
                    yield symbol
            else:
                for symbol in self._variableInDirectory(filename, ext):
                    yield symbol

    def resolveImport(self, import_symbol, scope):
        """Implementation of AbstractImportResolver.resolveImport()."""
        if import_symbol.name.startswith("."):
            for symbol in self._classInDirectory(os.path.dirname(self.filename)):
                scope.define(symbol)
        elif import_symbol.name.startswith("#"):
            for symbol in self._idInDirectory(os.path.dirname(self.filename)):
                scope.define(symbol)
        elif import_symbol.name.startswith("@"):
            for symbol in self._variableInDirectory(os.path.dirname(self.filename), ".less"):
                scope.define(symbol)
        elif import_symbol.name.startswith("$"):
            for symbol in self._variableInDirectory(os.path.dirname(self.filename), ".scss"):
                scope.define(symbol)
