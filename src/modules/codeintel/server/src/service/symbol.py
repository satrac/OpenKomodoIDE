from db.model import Model
from db.model.symbol import Symbol
from symbols import AbstractScope, AbstractSymbol
from language.router import Languages
import os
import re

cixRe = re.compile("^:\w+:$")

class ServiceSymbol(object):

    api = "codeintel"

    def __init__(self, symbol, alt_name=None):
        if isinstance(symbol, Model):
            symbol = symbol.toAbstractSymbol()

        self._symbol = symbol

        self._ctx = getattr(symbol, "ctx", {})
        filename = getattr(self._ctx, "filename", None)

        self._source = "buffer"
        if filename and cixRe.match(filename):
            self._source = "cix"
        elif filename:
            self._source = "external"

        self._altName = alt_name

    @property
    def name(self):
        """Returns the name of this symbol.
        Normally this is the AbstractSymbol's name, but sometimes it needs to be
        in a different form. For example: in Perl modules, variables are
        identified with a '$' prefix, but when referencing them, the '$' prefix
        is dropped. Perl's completion provider gives the alternate name in its
        completion list, but does not alter the symbol's original name.
        """
        return self._altName or self._symbol.name

    @property
    def typehint(self):
        return self._symbol.type

    @property
    def type(self):
        return Symbol.getSymbolType(self._symbol.__class__)

    @property
    def filename(self):
        if not self.path:
            return None
        return os.path.basename(self.path)

    @property
    def path(self):
        return getattr(self._ctx, "filename", None)

    @property
    def language(self):
        if self._source == "cix":
            return None

        if not self.path:
            return None

        ext = os.path.splitext(self.path)
        if not ext:
            return None

        ext = ext[1]
        ext = ext[1:] # we don't want the dot

        return Languages.getLanguageFromExt(ext).name

    @property
    def source(self):
        return self._source

    @property
    def line(self):
        return getattr(self._ctx, "line", None)

    @property
    def pos(self):
        return -1

    @property
    def active(self):
        return False

    @property
    def weight(self):
        return -1

    @property
    def isScope(self):
        return isinstance(self._symbol, AbstractScope)

    @property
    def isApproximate(self):
        return getattr(self._ctx, "approximate", False)

    @property
    def members(self):
        return []

    @property
    def active(self):
        return False

    def __iter__(self):
        attrs = dir(self)
        for attr in attrs:
            if attr.startswith('_') or callable(getattr(self, attr)):
                continue
            yield attr, getattr(self, attr)

    def toDict(self):
        return dict(self);

