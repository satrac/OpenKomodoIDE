"""Version comparison helpers.

The ``distutils`` package was removed from the standard library in Python
3.12, taking ``distutils.version`` with it.  These are small, dependency-free
stand-ins with the same public API and comparison semantics, so code that used
``LooseVersion``/``StrictVersion`` keeps working.
"""

import re
from functools import total_ordering

__all__ = ["LooseVersion", "StrictVersion"]


@total_ordering
class LooseVersion(object):
    """Version numbering for anarchists and software realists.

    Matches the behaviour of ``distutils.version.LooseVersion``.
    """

    component_re = re.compile(r'(\d+ | [a-z]+ | \.)', re.VERBOSE)

    def __init__(self, vstring=None):
        self.vstring = ''
        self.version = []
        if vstring is not None:
            self.parse(vstring)

    def parse(self, vstring):
        self.vstring = vstring
        components = [x for x in self.component_re.split(vstring)
                      if x and x != '.']
        for i, obj in enumerate(components):
            try:
                components[i] = int(obj)
            except ValueError:
                pass
        self.version = components

    def __str__(self):
        return self.vstring

    def __repr__(self):
        return "LooseVersion ('%s')" % str(self)

    def __hash__(self):
        return hash(tuple(self.version))

    def __eq__(self, other):
        if self is other:
            return True
        if not isinstance(other, (LooseVersion, str)):
            return NotImplemented
        return self._cmp(self._coerce(other)) == 0

    def __lt__(self, other):
        if not isinstance(other, (LooseVersion, str)):
            return NotImplemented
        return self._cmp(self._coerce(other)) < 0

    @staticmethod
    def _coerce(other):
        return LooseVersion(other) if isinstance(other, str) else other

    def _cmp(self, other):
        if self.version == other.version:
            return 0
        try:
            if self.version < other.version:
                return -1
            return 1
        except TypeError:
            ours = [str(x) for x in self.version]
            theirs = [str(x) for x in other.version]
            return (ours > theirs) - (ours < theirs)


@total_ordering
class StrictVersion(object):
    """Version numbering for anal retentives and software idealists.

    Matches the behaviour of ``distutils.version.StrictVersion``.
    """

    version_re = re.compile(
        r'^(\d+) \. (\d+) (\. (\d+))? ([ab](\d+))?$',
        re.VERBOSE | re.ASCII)

    def __init__(self, vstring=None):
        self.vstring = ''
        self.version = (0, 0, 0)
        self.prerelease = None
        if vstring is not None:
            self.parse(vstring)

    def parse(self, vstring):
        match = self.version_re.match(vstring)
        if not match:
            raise ValueError("invalid version number '%s'" % vstring)
        (major, minor, patch, prerelease, prerelease_num) = \
            match.group(1, 2, 4, 5, 6)
        if patch:
            self.version = tuple(map(int, [major, minor, patch]))
        else:
            self.version = tuple(map(int, [major, minor])) + (0,)
        if prerelease:
            self.prerelease = (prerelease[0], int(prerelease_num))
        else:
            self.prerelease = None
        self.vstring = vstring

    def __str__(self):
        if self.version[2] == 0:
            vstring = '.'.join(map(str, self.version[0:2]))
        else:
            vstring = '.'.join(map(str, self.version))
        if self.prerelease:
            vstring = vstring + self.prerelease[0] + str(self.prerelease[1])
        return vstring

    def __repr__(self):
        return "StrictVersion ('%s')" % str(self)

    def __hash__(self):
        return hash((self.version, self.prerelease))

    def __eq__(self, other):
        if self is other:
            return True
        if not isinstance(other, (StrictVersion, str)):
            return NotImplemented
        return self._cmp(self._coerce(other)) == 0

    def __lt__(self, other):
        if not isinstance(other, (StrictVersion, str)):
            return NotImplemented
        return self._cmp(self._coerce(other)) < 0

    @staticmethod
    def _coerce(other):
        return StrictVersion(other) if isinstance(other, str) else other

    def _cmp(self, other):
        if self.version != other.version:
            return -1 if self.version < other.version else 1
        if not self.prerelease and not other.prerelease:
            return 0
        if self.prerelease and not other.prerelease:
            return -1
        if not self.prerelease and other.prerelease:
            return 1
        if self.prerelease == other.prerelease:
            return 0
        return -1 if self.prerelease < other.prerelease else 1
