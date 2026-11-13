# Copyright 2017 ActiveState, Inc. All rights reserved.

"""CSS/Less/SCSS stdlib importer."""

from language.common import Scope, Variable, Function, Element, Attribute, CSSPseudoClass
from language.legacy.css.constants import constants_css3 as constants_css, constants_css_microsoft_extensions, constants_css_moz_extensions, constants_css_webkit_extensions

from SilverCity import Keywords

# The DB file path to store CSS stdlib symbols under.
CSS_STDLIB_FILE = ":css:"
# The DB file path to store SCSS stdlib symbols under.
SCSS_STDLIB_FILE = ":scss:"

# CSS's keywords to expose, not add to the stdlib. (They cannot be added to the
# top-level stdlib file, but may need to be nested in sub-scopes, etc.)
# Taken from the Scite version 2.0.2 css.properties file
# Silvercity wants the # of wordlists to be the same as the
# number hardwired in the lexer, so that's why there are 5 empty lists.
KEYWORDS = "".join([
    # CSS1 keywords
    """
    background background-attachment background-color background-image
    background-position background-repeat border border-bottom
    border-bottom-width border-color border-left border-left-width
    border-right border-right-width border-style border-top
    border-top-width border-width
    clear color display float font
    font-family font-size font-style font-variant font-weight height
    letter-spacing line-height list-style list-style-image
    list-style-position list-style-type margin margin-bottom margin-left
    margin-right margin-top padding padding-bottom padding-left
    padding-right padding-top text-align text-decoration text-indent
    text-transform vertical-align white-space width word-spacing
    """,
    # CSS pseudo-classes
    """
    active after before first first-child first-letter first-line
    focus hover lang left link right visited
    """,

    # CSS2 keywords
    """
    ascent azimuth baseline bbox border-bottom-color
    border-bottom-style border-collapse border-color border-left-color
    border-left-style border-right-color border-right-style
    border-spacing border-style border-top-color border-top-style
    bottom cap-height caption-side centerline clip content
    counter-increment counter-reset cue cue-after cue-before cursor
    definition-src descent direction elevation empty-cells
    font-size-adjust font-stretch left marker-offset marks mathline
    max-height max-width min-height min-width orphans outline
    outline-color outline-style outline-width overflow page
    page-break-after page-break-before page-break-inside panose-1
    pause pause-after pause-before pitch pitch-range play-during
    position quotes richness right size slope speak speak-header
    speak-numeral speak-punctuation speech-rate src stemh stemv stress
    table-layout text-shadow top topline unicode-bidi unicode-range
    units-per-em visibility voice-family volume widows widths x-height
    z-index
    """,
    # CSS3 Properties
    """
    border-top-left-radius
    border-top-right-radius
    border-bottom-left-radius
    border-bottom-right-radius
    border-radius
    """,
    # Pseudo-elements
    "",
    # Browser-Specific CSS Properties
    "",
    # Browser-Specific Pseudo-classes
    "",
    # Browser-Specific Pseudo-elements
    "",
]).split()

def scan(stdlib_file):
    """Imports the CSS/Less/SCSS stdlibs into the database."""
    # Import the stdlib.
    stdlib_scope = Scope()
    # HTML tag names.
    for tag_name in Keywords.hypertext_elements.split():
        stdlib_scope.define(Element(tag_name))
    # CSS pseudo-class names.
    for pseudo_class_name in constants_css.CSS_PSEUDO_CLASS_NAMES:
        stdlib_scope.define(CSSPseudoClass(":" + pseudo_class_name))
    # CSS attributes.
    attributes = constants_css.CSS_ATTR_DICT.copy()
    attributes.update(constants_css_microsoft_extensions.CSS_MICROSOFT_SPECIFIC_ATTRS_DICT)
    attributes.update(constants_css_moz_extensions.CSS_MOZ_SPECIFIC_ATTRS_DICT)
    attributes.update(constants_css_webkit_extensions.CSS_WEBKIT_SPECIFIC_ATTRS_DICT)
    for attribute_name, values in attributes.items():
        symbol = Attribute(attribute_name)
        if values:
            for value_name in values:
                symbol.define(Variable(value_name, None))
        stdlib_scope.define(symbol)
    # CSS and SCSS at-rule names.
    for at_rule_name in ["import", "media", "charset", "font-face", "page", "namespace", "keyframes"]:
        stdlib_scope.define(Function("@" + at_rule_name, None, None))
    if stdlib_file == SCSS_STDLIB_FILE:
        for scss_at_rule_name in ["extend", "at-root", "debug", "warn", "error", "if", "for", "each", "while", "mixin", "include", "function"]:
            stdlib_scope.define(Function("@" + scss_at_rule_name, None, None))

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)

if __name__ == "__main__":
    scan(CSS_STDLIB_FILE)
