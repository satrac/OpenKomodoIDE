# Copyright 2017 ActiveState, Inc. All rights reserved.

"""HTML stdlib importer."""

import os

from language.legacy.common.koDTD import DTD

# The DB file paths to store stdlib symbols under.
HTML5_STDLIB_FILE = ":html5:"

HTML2_STDLIB_FILE = ":html2:"
HTML2STRICT_STDLIB_FILE = ":html2strict:"
HTML2LEVEL1_STDLIB_FILE = ":html2level1:"
HTML2LEVEL2_STDLIB_FILE = ":html2level2:"
HTML2STRICTLEVEL1_STDLIB_FILE = ":html2strictlevel1:"
HTML2STRICTLEVEL2_STDLIB_FILE = ":html2strictlevel2:"

HTML_STDLIB_FILE = HTML2_STDLIB_FILE
HTMLSTRICT_STDLIB_FILE = HTML2STRICT_STDLIB_FILE
HTMLLEVEL1_STDLIB_FILE = HTML2LEVEL1_STDLIB_FILE
HTMLLEVEL2_STDLIB_FILE = HTML2LEVEL2_STDLIB_FILE
HTMLSTRICTLEVEL1_STDLIB_FILE = HTML2STRICTLEVEL1_STDLIB_FILE
HTMLSTRICTLEVEL2_STDLIB_FILE = HTML2STRICTLEVEL2_STDLIB_FILE

HTML32_STDLIB_FILE = ":html32:"
HTML32DRAFT_STDLIB_FILE = ":html32draft:"
HTML32FINAL_STDLIB_FILE = ":html32final:"

HTML40_STDLIB_FILE = ":html40:"
HTML40FRAMESET_STDLIB_FILE = ":html40frameset:"
HTML40TRANSITIONAL_STDLIB_FILE = ":html40transitional:"

HTML41_STDLIB_FILE = ":html41:"
HTML41FRAMESET_STDLIB_FILE = ":html41frameset:"
HTML41TRANSITIONAL_STDLIB_FILE = ":html41transitional:"

XHTML10STRICT_STDLIB_FILE = ":xhtml10strict:"
XHTML10FRAMESET_STDLIB_FILE = ":xhtml10frameset:"
XHTML10TRANSITIONAL_STDLIB_FILE = ":xhtml10transitional:"

XHTML11_STDLIB_FILE = ":xhtml11:"
XHTML11PLUSMATHML20_STDLIB_FILE = ":xhtml11plusmathml20:"
XHTML11PLUSMATHML20PLUSSVG11_STDLIB_FILE = ":xhtml11plusmathml20plussvg11:"

# Map of stdlib files to DTDs.
dtd_map = {
    HTML5_STDLIB_FILE: os.path.join("catalogs", "html5", "html5.dtd"),

    HTML_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0.dtd"),
    HTMLSTRICT_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0-Strict.dtd"),
    HTMLLEVEL1_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0-Level_1.dtd"),
    HTMLLEVEL2_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0.dtd"),
    HTMLSTRICTLEVEL1_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0-Strict-Level_1.dtd"),
    HTMLSTRICTLEVEL2_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0-Strict.dtd"),

    HTML2_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0.dtd"),
    HTML2STRICT_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0-Strict.dtd"),
    HTML2LEVEL1_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0-Level_1.dtd"),
    HTML2LEVEL2_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0.dtd"),
    HTML2STRICTLEVEL1_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0-Strict-Level_1.dtd"),
    HTML2STRICTLEVEL2_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "IETF", "HTML-2_0-Strict.dtd"),

    HTML32_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html32-19970114", "HTML32.dtd"),
    HTML32DRAFT_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html32-19970114", "HTML32.dtd"),
    HTML32FINAL_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html32-19970114", "HTML32.dtd"),

    HTML40_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html40-19980424", "strict.dtd"),
    HTML40FRAMESET_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html40-19980424", "frameset.dtd"),
    HTML40TRANSITIONAL_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html40-19980424", "loose.dtd"),

    HTML41_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html401-19991224", "strict.dtd"),
    HTML41FRAMESET_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html401-19991224", "frameset.dtd"),
    HTML41TRANSITIONAL_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-html401-19991224", "loose.dtd"),

    XHTML10STRICT_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-xhtml1-20020801", "xhtml1-strict.dtd"),
    XHTML10FRAMESET_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-xhtml1-20020801", "xhtml1-frameset.dtd"),
    XHTML10TRANSITIONAL_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-xhtml1-20020801", "xhtml1-transitional.dtd"),

    XHTML11_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-xhtml11-20010531", "xhtml11-flat.dtd"),
    XHTML11PLUSMATHML20_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "REC-MathML2-20031021", "xhtml-math11-f.dtd"),
    XHTML11PLUSMATHML20PLUSSVG11_STDLIB_FILE: os.path.join("catalogs", "sgml-lib", "xhtml-math-svg-flat-20020809.dtd"),
}

def scan(stdlib_file):
    """Imports the HTML stdlibs into the database."""
    stdlib_scope = None
    try:
        dtd_file = os.path.join(os.path.dirname(__file__), dtd_map[stdlib_file])
        stdlib_scope = DTD(dtd_file).dataset.toAbstractScope()
    except AttributeError:
        raise RuntimeError("Unknown HTML stdlib file: '%s'" % stdlib_file)

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)

if __name__ == "__main__":
    scan(HTML5_STDLIB_FILE)
