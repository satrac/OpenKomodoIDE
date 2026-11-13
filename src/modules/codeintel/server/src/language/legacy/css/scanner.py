# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for CSS, Less, and SCSS source code."""

import logging
import operator
import os
import re

from symbols import (AbstractVariable,
                     AbstractFunction,
                     AbstractElement, AbstractAttribute,
                     AbstractCSSClass, AbstractCSSId, AbstractCSSPseudoClass)
from completions import AbstractMemberCompletionContext, AbstractScopeCompletionContext
from goto_definition import GotoDefinitionContext
from language.common import Scope, Variable, Import, Element, CSSClass, CSSId, AbstractScanner, AbstractScannerContext
from language.legacy.css.import_resolver import CSSImportResolver
from language.legacy.udl import is_udl_m_style, is_udl_css_style, AbstractUDLSubScanner

from db.model.helpers import fileExists, fetchSymbolsInFile

import SilverCity
from SilverCity.Lexer import Lexer
from SilverCity import ScintillaConstants

log = logging.getLogger("codeintel.css.scanner")

# Taken from the Scite version 2.0.2 css.properties file
# Silvercity wants the # of wordlists to be the same as the
# number hardwired in the lexer, so that's why there are 5 empty lists.
raw_word_lists = [
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
    ]

class CSSLexer(Lexer):
    lang = "CSS"
    def __init__(self):
        self._properties = SilverCity.PropertySet()
        self._lexer = SilverCity.find_lexer_module_by_id(ScintillaConstants.SCLEX_CSS)
        self._keyword_lists = []
        for i in range(len(raw_word_lists)):
            self._keyword_lists.append(SilverCity.WordList(raw_word_lists[i]))

class _StraightCSSStyleClassifier(object):
    def is_css_style(self, style, accessorCacheBack=None):
        return True

    def is_default(self, style, accessorCacheBack=None):
        return style in self.default_styles

    def is_comment(self, style, accessorCacheBack=None):
        return style in self.comment_styles

    def is_string(self, style, accessorCacheBack=None):
        return style in self.string_styles

    def is_operator(self, style, accessorCacheBack=None):
        return style in self.operator_styles or \
               style == ScintillaConstants.SCE_CSS_IMPORTANT

    def is_identifier(self, style, accessorCacheBack=None):
        return style in self.identifier_styles

    def is_value(self, style, accessorCacheBack=None):
        return style in self.value_styles

    def is_tag(self, style, accessorCacheBack=None):
        return style in self.tag_styles

    def is_class(self, style, accessorCacheBack=None):
        # Normally, the CSS lexer gets class styles correct. However, for nested
        # classes like in Less, the CSS lexer does not get it right. An
        # additional check is needed.
        return style in self.class_styles or style in self.identifier_styles and len(accessorCacheBack) > 1 and accessorCacheBack[-2]["style"] in self.operator_styles and accessorCacheBack[-2]["text"] == "."

    def is_number(self, style, accessorCacheBack=None):
        return style in self.number_styles

    def is_directive(self, style, accessorCacheBack=None):
        return style == ScintillaConstants.SCE_CSS_DIRECTIVE

    def is_pseudoclass(self, style, accessorCacheBack=None):
        return style in (ScintillaConstants.SCE_CSS_PSEUDOCLASS, ScintillaConstants.SCE_CSS_UNKNOWN_PSEUDOCLASS)

    def is_id(self, style, accessorCacheBack=None):
        return style == ScintillaConstants.SCE_CSS_ID

    @property
    def default_styles(self):
        return (ScintillaConstants.SCE_CSS_DEFAULT, )

    @property
    def comment_styles(self):
        return (ScintillaConstants.SCE_CSS_COMMENT,)

    @property
    def string_styles(self):
        return (ScintillaConstants.SCE_CSS_SINGLESTRING,
                ScintillaConstants.SCE_CSS_DOUBLESTRING)

    @property
    def operator_styles(self):
        return (ScintillaConstants.SCE_CSS_OPERATOR, )

    @property
    def identifier_styles(self):
        return (ScintillaConstants.SCE_CSS_IDENTIFIER,
                ScintillaConstants.SCE_CSS_IDENTIFIER2,
                ScintillaConstants.SCE_CSS_UNKNOWN_IDENTIFIER)

    @property
    def value_styles(self):
        return (ScintillaConstants.SCE_CSS_VALUE,
                ScintillaConstants.SCE_CSS_NUMBER)

    @property
    def tag_styles(self):
        return (ScintillaConstants.SCE_CSS_TAG, )

    @property
    def class_styles(self):
        return (ScintillaConstants.SCE_CSS_CLASS, )

    @property
    def number_styles(self):
        return ()

    @property
    def ignore_styles(self):
        return (ScintillaConstants.SCE_CSS_DEFAULT,
                ScintillaConstants.SCE_CSS_COMMENT)

DebugStatus = False

class _UDLCSSStyleClassifier(_StraightCSSStyleClassifier):
    def is_css_style(self, style, accessorCacheBack=None):
        return is_udl_css_style(style)

    def is_identifier(self, style, accessorCacheBack=None):
        if style not in self.identifier_styles or self.is_tag(style, accessorCacheBack) or self.is_pseudoclass(style, accessorCacheBack) or self.is_id(style, accessorCacheBack) or self.is_class(style, accessorCacheBack) or self.is_value(style, accessorCacheBack) or self.is_directive(style, accessorCacheBack):
            return False

        return True

    def is_value(self, style, accessorCacheBack=None):
        # It's a value if among prev tokens are ":", a word, and in "{;" or
        # after a "style='" HTML sequence.
        tokens = accessorCacheBack
        i = len(tokens) - 2
        while i >= 2:
            if self.is_operator(tokens[i]["style"]):
                if tokens[i]["text"] != ":":
                    return False
                return self.is_operator(tokens[i - 2]["style"]) and tokens[i - 2]["text"] in "{;" or is_udl_m_style(tokens[i - 2]["style"])
            i -= 1
        return False

    def is_class(self, style, accessorCacheBack=None):
        # It's a class if prev token is ".".
        tokens = accessorCacheBack
        i = len(tokens) - 2
        return self.is_operator(tokens[i]["style"]) and tokens[i]["text"] == "."

    def is_tag(self, style, accessorCacheBack=None):
        # It's a tag if prev token is "}," or HTML style, unless it is a
        # "style='" sequence (in which case it's an identifier).
        tokens = accessorCacheBack
        i = len(tokens) - 2
        if self.is_operator(tokens[i]["style"]):
            return tokens[i]["text"] in "},"
        elif is_udl_m_style(tokens[i]["style"]):
            while i > 0:
                if tokens[i]["text"] == "=":
                    if i > 0 and tokens[i - 1]["text"].lower() == "style":
                        return False # identifier
                    break
                i -= 1
            return True
        else:
            return False

    def is_directive(self, style, accessorCacheBack=None):
        # It's a directive if prev token is "@".
        tokens = accessorCacheBack
        i = len(tokens) - 2
        return self.is_operator(tokens[i]["style"]) and tokens[i]["text"] == "@"

    def is_pseudoclass(self, style, accessorCacheBack=None):
        # It's a pseudoclass if prev tokens are ":" and a tag.
        tokens = accessorCacheBack
        i = len(tokens) - 2
        return self.is_operator(tokens[i]["style"]) and tokens[i]["text"] == ":" and self.is_tag(tokens[i - 1]["style"], tokens[:i-1])

    def is_id(self, style, accessorCacheBack=None):
        # It's an ID if prev token is "#".
        tokens = accessorCacheBack
        i = len(tokens) - 2
        return self.is_operator(tokens[i]["style"]) and tokens[i]["text"] == "#"

    @property
    def default_styles(self):
        return (ScintillaConstants.SCE_UDL_CSS_DEFAULT, )

    @property
    def comment_styles(self):
        return (ScintillaConstants.SCE_UDL_CSS_COMMENT,)

    @property
    def string_styles(self):
        return (ScintillaConstants.SCE_UDL_CSS_STRING, )

    @property
    def operator_styles(self):
        return (ScintillaConstants.SCE_UDL_CSS_OPERATOR, )

    @property
    def identifier_styles(self):
        return (ScintillaConstants.SCE_UDL_CSS_IDENTIFIER,
                ScintillaConstants.SCE_UDL_CSS_WORD)

    @property
    def value_styles(self):
        return (ScintillaConstants.SCE_UDL_CSS_WORD,
                ScintillaConstants.SCE_UDL_CSS_IDENTIFIER,
                ScintillaConstants.SCE_UDL_CSS_NUMBER)

    @property
    def tag_styles(self):
        return (ScintillaConstants.SCE_CSS_TAG, )

    @property
    def number_styles(self):
        return (ScintillaConstants.SCE_UDL_CSS_NUMBER, )

    @property
    def ignore_styles(self):
        return (ScintillaConstants.SCE_UDL_CSS_DEFAULT,
                ScintillaConstants.SCE_UDL_CSS_COMMENT)


StraightCSSStyleClassifier = _StraightCSSStyleClassifier()
UDLCSSStyleClassifier      = _UDLCSSStyleClassifier()

class CSSScannerContext(AbstractScannerContext):
    """Implementation of AbstractScannerContext for the CSS scanner."""
    def __init__(self, selector):
        self._selector = selector

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        return self._selector.line + 1

    def contains(self, line):
        """Implementation of AbstractScannerContext.contains()."""
        return line >= self._selector.line + 1 and (not self._selector.lineend or line <= self._selector.lineend + 1)

class CSSSelector:
    """A CILE object that represents a CSS selector."""
    ELEMENT = "element"
    CLASS = "class"
    ID = "id"
    # Matcher for multiple ID and Class names in a selector.
    CSS_ID_OR_CLASS = re.compile('([#.][A-Za-z0-9_-]+)(::?[A-Za-z-]+(\\([^\\)]+\\))?|(?:\\[[^\\]]+\\])+)?')
    # Matcher for a single ID or Class name in a selector (at its end).
    CSS_IDENT = re.compile('([#.]?[A-Za-z0-9_-]+|&)(::?[A-Za-z-]+(\\([^\\)]+\\))?|(?:\\[[^\\]]+\\])+)?$')

    def __init__(self, text, line, target):
        """Creates a new representation for a CSS selector with the given text
        and type that occurs on the given line number.
        @param text The text of the CSS selector
        @param line The line number the CSS selector starts on.
        @param target The element, class, or id (i.e. target) of the CSS
               selector. This is the symbol used for autocompletions.
        """
        self.text = text
        self.line = line
        self.lineend = None # to be set later when '}' is encountered

        self.type = self.ELEMENT
        if target.startswith('#'):
            self.type = self.ID
            self.target = target[1:]
        elif target.startswith('.'):
            self.type = self.CLASS
            self.target = target[1:]
        else:
            self.target = target

        self.variables = [] # Less/SCSS only

    def addVariable(self, variable):
        """Adds the given CSSVariable to this CSS selector.
        This is only applicable in Less/SCSS.
        @param variable The CSSVariable to add.
        """
        self.variables.append(variable)

    def appendElementTreeTo(self, cixmodule):
        """Creates a CIX representation of this selector and appends it to the
        given CixModule.
        @param cixmodule The CixModule to append the CIX representation of this
               selector to.
        """
        cixobject = SubElement(cixmodule, "scope", ilk=self.type, name=self.text)
        cixobject.attrib["target"] = self.target
        cixobject.attrib["line"] = str(self.line + 1)

        for variable in self.variables:
            variable.appendElementTreeTo(cixobject)

    def toAbstractSymbol(self, enclosingScope):
        if self.type == CSSSelector.ELEMENT:
            scope = Element(self.text, enclosingScope, CSSScannerContext(self))
        elif self.type == CSSSelector.CLASS:
            scope = CSSClass(self.text, enclosingScope, CSSScannerContext(self))
        elif self.type == CSSSelector.ID:
            scope = CSSId(self.text, enclosingScope, CSSScannerContext(self))
        for variable in self.variables:
            scope.define(variable.toAbstractSymbol())
        return scope

class CSSVariable:
    """A CILE object that represents a Less/SCSS variable."""
    def __init__(self, text, line):
        """Creates a new representation for a Less/SCSS variable with the given
        name that occurs on the given line number.
        @param text The name of the Less/SCSS variable, including the leading
               '@' or '$'.
        @param line The line number the variable starts on.
        """
        self.text = text
        self.line = line
        self.lineend = None # not scoped

    def appendElementTreeTo(self, cixmodule):
        """Creates a CIX representation of this Less/SCSS variable and appends
        it to the given CixModule.
        @param cixmodule The CixModule to append the CIX representation of this
               variable to.
        """
        cixobject = SubElement(cixmodule, "variable", name=self.text)
        cixobject.attrib["name"] = self.text
        cixobject.attrib["line"] = str(self.line + 1)

    def toAbstractSymbol(self, enclosingScope=None):
        # Ignore enclosingScope, as it does not apply to variables, but needs to
        # be present to avoid a runtime TypeError.
        return Variable(self.text, None, CSSScannerContext(self))

class CSSFile:
    """A CILE object that represents a CSS file."""

    def __init__(self, path, lang):
        """Creates a new representation for the CSS file with the given path.
        @param path The path to the CSS file.
        """
        self.path = path
        self.lang = lang
        self.name = os.path.basename(path)
        self.parent = None
        self.cixname = self.__class__.__name__[2:].lower()

        # Selectors defined within this file.
        self.elements = {}
        self.classes = {}
        self.ids = {}
        # Less or SCSS variables defined within this file.
        self.variables = {}

    def addSelector(self, selector):
        """Adds the given CSSSelector to this CSS file.
        @param selector The CSSSelector to add.
        """
        if selector.type == CSSSelector.ELEMENT:
            self.elements[selector.text] = selector
        elif selector.type == CSSSelector.CLASS:
            self.classes[selector.text] = selector
        elif selector.type == CSSSelector.ID:
            self.ids[selector.text] = selector

    def addVariable(self, variable):
        """Adds the given CSSVariable to this CSS file.
        @param variable The CSSVariable to add.
        """
        self.variables[variable.text] = variable

    def appendCixToModule(self, cixmodule):
        """Appends the individual CixElements to the given CixModule.
        @param cixmodule The CixModule to append the CIX representation of this
               file to.
        """
        for v in sorted(list(self.elements.values()) + list(self.classes.values()) +
                        list(self.ids.values()) + list(self.variables.values()),
                        key=operator.attrgetter("line", "text")):
            v.appendElementTreeTo(cixmodule)

    def appendCixFileTo(self, cixtree):
        """Creates a CixFile representation of this file and appends it to the
        given CixRoot.
        @param cixtree The CixRoot to append the CIX representation of this
               file to.
        """
        cixfile = createCixFile(cixtree, self.path, lang=self.lang)
        cixmodule = createCixModule(cixfile, self.name, self.lang, src=self.path)
        self.appendCixToModule(cixmodule)

    def toAbstractSymbol(self, enclosingScope=None):
        scope = Scope(enclosingScope)
        for v in sorted(list(self.elements.values()) + list(self.classes.values()) +
                        list(self.ids.values()) + list(self.variables.values()),
                        key=operator.attrgetter("line", "text")):
            scope.define(v.toAbstractSymbol(scope))
        return scope

class CSSScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "CSS"

class CSSMemberCompletionContext(AbstractMemberCompletionContext):
    """Implementation of AbstractMemberCompletionContext."""
    @property
    def language(self):
        return "CSS"

class CSSPseudoClassCompletionContext(CSSScopeCompletionContext):
    """CSSScopeCompletionContext for pseudoclasses that strips pseudoclass
    prefixes that come from the stdlib. (They are there in order to prevent name
    conflicts like "link" is both a tag name and a pseudoclass name.)
    """
    def __init__(self, scope, name_part=""):
        super(CSSPseudoClassCompletionContext, self).__init__(scope, name_part, AbstractCSSPseudoClass)

    def getCompletions(self):
        completions = super(CSSPseudoClassCompletionContext, self).getCompletions()
        for name, symbol in list(completions.members.items()):
            completions.members[name[1:]] = symbol
            del completions.members[name]
        return completions

class CSSScanner(AbstractScanner):
    def __init__(self, stdlib_file, lexer_class=CSSLexer):
        super(CSSScanner, self).__init__(stdlib_file)
        self.lexerClass = lexer_class
        if lexer_class == CSSLexer:
            self.styleClassifier = StraightCSSStyleClassifier
        else:
            self.styleClassifier = UDLCSSStyleClassifier

    def scan(self, filename, env={}):
        """Implementation of AbstractScanner.scan().
        For testing purposes, when filename is None, stdin is scanned.
        """
        if not isinstance(filename, (str, None.__class__)):
            raise TypeError("filename must be a string ('%s' received)" % filename.__class__.__name__)
        if not isinstance(env, dict):
            raise TypeError("env must be a dictionary ('%s' received)" % env.__class__.__name__)

        if filename is not None:
            if not filename.startswith(".") and os.path.exists(filename):
                try:
                    self.content = self.readFile(filename)
                except IOError:
                    self.content = filename
                    filename = ":untitled:"
            else:
                self.content = filename
                filename = ":untitled:"
        else:
            self.content = __import__("sys").stdin.read()
            filename = ":stdin:"

        self.cile = CSSFile(filename, "CSS")

        self.selector = []
        self.selectorStartLine = None
        # Track nested selector names in order to generate fully expanded
        # selector names (Less/SCSS only).
        self.nestedSelectors = []
        # Track nested scopes in order to add declared variables to the
        # appropriate scope (Less/SCSS only).
        self.nestedScopes = []
        self.whitespace = re.compile('^\\s+$')
        self.blockLevel = 0
        self.parenLevel = 0
        self.ignoreStatement = False

        self.tokens = [] # track for use by getCompletionContext()
        self.lexerClass().tokenize_by_style(self.content, self.handle_token)
        return self.cile.toAbstractSymbol(self.builtInScope)

    def getCompletionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCompletionContext()."""
        # scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        return self._getCompletionContext(filename, position, env, self.tokens, line, column, scope)

    def getGotoDefinitionContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getGotoDefinitionContext()."""
        # scan the given source code file.
        scope = self.scan(filename, env)
        if not isinstance(position, int):
            raise TypeError("position must be an int ('%s' received)" % position.__class__.__name__)

        # Determine the line and column number of position.
        lines = self.content[:position].split("\n")
        line, column = len(lines), len(lines[-1])

        return self._getGotoDefinitionContext(filename, position, env, self.tokens, line, column, scope)

    def _getCompletionContext(self, filename, position, env, _tokens, line, column, scope):
        """Helper method for fetching completion contexts.
        This method is called by both the stand-alone CSS scanner and the UDL
        CSS sub-scanner (via HTML), hence the wide variety of parameters.
        Backtracks through the token list starting at the given position,
        looking for an appropriate completion context.
        @param filename String filename to get the context in. Since the file
                        has already been scanned, this parameter is used for
                        reporting purposes only.
        @param position Integer position to get the context for. Since line and
                        column have already been determined, this parameter is
                        rarely used.
        @param env Dictionary of environment variables used in import
                   resolution.
        @param tokens List of scanned tokens produced by the stand-alone lexer
                      or UDL lexer.
        @param line Integer 1-based line number to get the context for. Tokens
                    often contain line number information rather than position
                    info.
        @param column Integer column number to get the context for. Tokens often
                    contain column number information rather than position info.
        @param scope AbstractScope containing all scanned CSS symbols.
        @return AbstractCompletionContext or None
        """
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if token["line"] <= line:
                if token["line"] < line:
                    # Keep all non-whitespace tokens prior to the current line.
                    if not self.styleClassifier.is_default(token["style"]):
                        tokens.append(token)
                elif token["column"] < column:
                    # Keep all non-whitespace tokens in the current line prior
                    # to the current position, unless the current position
                    # exists within a whitespace token.
                    if not self.styleClassifier.is_default(token["style"]) or (token["column"] + 1 <= column and column <= token["column"] + len(token["text"])):
                        tokens.append(token)
                        if self.styleClassifier.is_operator(token["style"]) and column <= token["column"] + len(token["text"]) - 1 and token["column"] != token["column"] + len(token["text"]):
                            # The tokenizer generally groups all operator
                            # characters into a single token. Chop off any
                            # bits that occur after the current position.
                            # Otherwise, something like "(@<|>)" will be
                            # considered to be a "(@)" token instead of '(@'.
                            token["text"] = token["text"][:-(token["column"] + len(token["text"]) - column)]
                elif token["column"] == column and not self.styleClassifier.is_default(token["style"]) and not self.styleClassifier.is_operator(token["style"]) and not self.styleClassifier.is_string(token["style"]):
                    # Keep any non-whitespace token that starts at the current
                    # position.
                    if len(tokens) > 0 and self.styleClassifier.is_default(tokens[-1]["style"]):
                        tokens.pop() # no more leading whitespace
                    tokens.append(token)
        leading_whitespace = False
        scope = scope.resolveScope(line)
        name_part = "" # the "already typed" part of a symbol completion
        import_resolver = CSSImportResolver(filename, env)

        if len(tokens) == 0:
            # If the position is at the beginning of the buffer, provide scope
            # completions.
            return CSSScopeCompletionContext(scope, name_part, AbstractElement)

        # Pre-process the end of the token list.
        if self.styleClassifier.is_default(tokens[-1]["style"]):
            # If the position is immediately after whitespace, make a note since
            # some completions after whitespace are possible, like
            # "foo { bar: baz(quux) <|>" are possible, while some like
            # "foo { bar: baz(quux)<|>" are not.
            leading_whitespace = True
            tokens.pop()
            #if len(tokens) == 0 or tokens[-1]["style"] == SCE_UDL_SSL_OPERATOR and tokens[-1]["text"] in ("<?php", "<?"):
            if len(tokens) == 0 or isinstance(self, HTMLCSSScanner) and is_udl_m_style(tokens[-1]["style"]):
                return CSSScopeCompletionContext(scope, name_part, AbstractElement)
        elif self.styleClassifier.is_directive(tokens[-1]["style"], tokens) and isinstance(self, LessScanner):
            # If the position is within a directive, keep track of this name
            # part in order to help differentiate between at-rules and
            # variables.
            name_part = tokens[-1]["text"]
            tokens.pop()
        elif self.styleClassifier.is_value(tokens[-1]["style"], tokens) and isinstance(self, SCSSScanner) and len(tokens) > 1 and self.styleClassifier.is_operator(tokens[-2]["style"]) and tokens[-2]["text"] == "$":
            # If the position is within a SCSS variable, keep track of this name
            # part in order.
            name_part = tokens[-1]["text"]
            tokens.pop()

        # Now look back through the token list and provide an appropriate
        # completion context.
        if self.styleClassifier.is_operator(tokens[-1]["style"]):
            if tokens[-1]["text"] == "#":
                # A "#foo"-type of expression should provide id completions.
                name_part = tokens[-1]["text"]
                scope.define(Import("#*", "")) # for import resolver
                return CSSScopeCompletionContext(scope, name_part, AbstractCSSId, import_resolver=import_resolver)
            elif tokens[-1]["text"] == ":":
                if len(tokens) > 1 and self.styleClassifier.is_directive(tokens[-2]["style"], tokens[:-1]):
                    # A "@foo:bar"-type of expression should not provide
                    # completions.
                    return None
                if len(tokens) < 2 or not self.styleClassifier.is_identifier(tokens[-2]["style"], tokens[:-1]):
                    if isinstance(self, SCSSScanner) and len(tokens) > 2 and self.styleClassifier.is_operator(tokens[-3]["style"]) and tokens[-3]["text"] == "$":
                        # A "$foo: bar"-type of expression should not provide
                        # completions.
                        return None
                    # A "foo:bar"-type of expression should provide pseudo-class
                    # completions.
                    return CSSPseudoClassCompletionContext(scope, name_part)
                else:
                    # A "foo { bar: baz"-type of expression should provide
                    # attribute member completions.
                    symbol_name = tokens[-2]["text"]
                    return CSSMemberCompletionContext(scope, symbol_name, name_part)
            elif tokens[-1]["text"] == "&:" and isinstance(self, LessScanner):
                # A "&:foo"-type of expression in Less should provide
                # pseudo-class completions.
                return CSSPseudoClassCompletionContext(scope, name_part)
            elif tokens[-1]["text"] == ".":
                # A ".foo"-type of expression should provide class completions.
                name_part = tokens[-1]["text"]
                scope.define(Import(".*", "")) # for import resolver
                return CSSScopeCompletionContext(scope, name_part, AbstractCSSClass, import_resolver=import_resolver)
            elif tokens[-1]["text"].endswith("@"):
                # A "@foo"-type of expression in CSS is an at-rule. However, in
                # Less, it is only an at-rule if it is a bare "@" or a "@" that
                # does not follow a ":" (e.g. not a "@foo: @bar"-type of
                # expression).
                if not isinstance(self, LessScanner) or tokens[-1]["text"] == "@" and not (len(tokens) > 1 and self.styleClassifier.is_operator(tokens[-2]["style"]) and tokens[-2]["text"] == ":"):
                    # A "@foo"-type of expression should provide at-rule
                    # completions. (Note: CSS stdlib defines these as
                    # AbstractFunction symbols.)
                    return CSSScopeCompletionContext(scope, "@" + name_part, AbstractFunction)
                elif isinstance(self, LessScanner):
                    # A "@foo"- or "@foo: @bar"-type of expression should
                    # provide variable completions in Less.
                    return CSSScopeCompletionContext(scope, name_part, AbstractVariable, import_resolver=import_resolver)
            elif tokens[-1]["text"] == ",":
                if len(tokens) > 2 and (self.styleClassifier.is_value(tokens[-2]["style"]) or self.styleClassifier.is_string(tokens[-2]["style"])):
                    i = len(tokens) - 2
                    while i >= 2:
                        if self.styleClassifier.is_operator(tokens[i - 1]["style"]):
                            if tokens[i - 1]["text"] in "{;":
                                # A "foo { bar: baz, quux"-type of expression
                                # should provide attribute member completions.
                                symbol_name = tokens[i]["text"]
                                return CSSMemberCompletionContext(scope, symbol_name, name_part)
                            elif tokens[i - 1]["text"] == "(":
                                # A "foo { bar: baz(quux"-type of expression
                                # should not provide any completions.
                                return None
                        i -= 1
                # A "foo, bar"-type of expression should provide tag
                # completions.
                return CSSScopeCompletionContext(scope, name_part, AbstractElement)
            elif tokens[-1]["text"] == ")" and leading_whitespace:
                # If a ')' operator is immediately behind the position, it is
                # probably part of a "foo: bar(baz) "-type of expression. Search
                # backwards, looking for the corresponding attribute name, which
                # is immediately after '{' or ';'.
                i = len(tokens) - 2
                while i >= 2:
                    if self.styleClassifier.is_operator(tokens[i - 1]["style"]) and tokens[i - 1]["text"] in "{;":
                        # A "foo { bar: baz"- or "foo { bar: baz quux"-type of
                        # expression should provide attribute member
                        # completions.
                        symbol_name = tokens[i]["text"]
                        return CSSMemberCompletionContext(scope, symbol_name, name_part)
                    i -= 1
            elif tokens[-1]["text"] == "{":
                # A "foo { bar"-type of expression should provide attribute
                # completions.
                return CSSScopeCompletionContext(scope, name_part, AbstractAttribute)
            elif tokens[-1]["text"] == "$" and isinstance(self, SCSSScanner):
                if len(tokens) < 2 or not (self.styleClassifier.is_operator(tokens[-2]["style"]) and tokens[-2]["text"] == ":" or self.styleClassifier.is_value(tokens[-2]["style"])):
                    # A bare "$foo"-type of expression should not provide
                    # completions.
                    return None
                # A "foo: $bar"- or "foo: bar $baz"-type of expression should
                # provide variable completions.
                return CSSScopeCompletionContext(scope, name_part, AbstractVariable)
            elif tokens[-1]["text"] == ";":
                # This should only happen within a
                # "<foo style='bar: baz; quux"-type of expression, which should
                # provide attribute completions.
                return CSSScopeCompletionContext(scope, name_part, AbstractAttribute)
        elif self.styleClassifier.is_tag(tokens[-1]["style"], tokens):
            # A "foo"-type of expression should provide tag completions.
            if not leading_whitespace:
                name_part = tokens[-1]["text"]
            return CSSScopeCompletionContext(scope, name_part, AbstractElement)
        elif self.styleClassifier.is_pseudoclass(tokens[-1]["style"], tokens):
            # A "foo:bar"-type of expression should provide pseudo-class
            # completions.
            name_part = tokens[-1]["text"]
            return CSSPseudoClassCompletionContext(scope, name_part)
        elif self.styleClassifier.is_id(tokens[-1]["style"], tokens):
            # A "#foo"-type of expression should provide id completions.
            name_part = "#" + tokens[-1]["text"]
            scope.define(Import("#*", "")) # for import resolver
            return CSSScopeCompletionContext(scope, name_part, AbstractCSSId, import_resolver=import_resolver)
        elif self.styleClassifier.is_class(tokens[-1]["style"], tokens):
            # A ".foo"-type of expression should provide class completions.
            name_part = "." + tokens[-1]["text"]
            scope.define(Import(".*", None)) # for import resolver
            return CSSScopeCompletionContext(scope, name_part, AbstractCSSClass, import_resolver=import_resolver)
        elif self.styleClassifier.is_directive(tokens[-1]["style"], tokens):
            # A "@foo"-type of expression should provide at-rule completions.
            # (Note: CSS stdlib defines these as AbstractFunction symbols.)
            name_part = tokens[-1]["text"]
            return CSSScopeCompletionContext(scope, "@" + name_part, symbol_type=AbstractFunction)
        elif self.styleClassifier.is_identifier(tokens[-1]["style"], tokens):
            # A "foo { bar"-type of expression should provide attribute
            # completions.
            name_part = tokens[-1]["text"]
            return CSSScopeCompletionContext(scope, name_part, AbstractAttribute)
        elif self.styleClassifier.is_value(tokens[-1]["style"], tokens):
            # Search backwards, looking for the corresponding attribute name,
            # which is immediately after '{', ';', or HTML "style='" sequence.
            i = len(tokens) - 2
            while i >= 2:
                if self.styleClassifier.is_operator(tokens[i - 1]["style"]) and tokens[i - 1]["text"] == "{" or ";" in tokens[i - 1]["text"] or isinstance(self, HTMLCSSScanner) and is_udl_m_style(tokens[i - 1]["style"]):
                    # A "foo { bar: baz"- or "foo { bar: baz quux"-type of
                    # expression should provide attribute member completions.
                    symbol_name = tokens[i]["text"]
                    if not leading_whitespace:
                        name_part = tokens[-1]["text"]
                    return CSSMemberCompletionContext(scope, symbol_name, name_part)
                i -= 1

        return None

    def _getGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Helper method for fetching goto definition contexts.
        This method is called by both the stand-alone CSS scanner and the UDL
        CSS sub-scanner (via HTML), hence the wide variety of parameters.
        Backtracks through the token list starting at the given position,
        looking for an appropriate goto definition context.
        @param filename String filename to get the context in. Since the file
                        has already been scanned, this parameter is used for
                        reporting purposes only.
        @param position Integer position to get the context for. Since line and
                        column have already been determined, this parameter is
                        rarely used.
        @param env Dictionary of environment variables used in import
                   resolution.
        @param tokens List of scanned tokens produced by the stand-alone lexer
                      or UDL lexer.
        @param line Integer 1-based line number to get the context for. Tokens
                    often contain line number information rather than position
                    info.
        @param column Integer column number to get the context for. Tokens often
                    contain column number information rather than position info.
        @param scope AbstractScope containing all scanned CSS symbols.
        @return GotoDefinitionContext or None
        """
        scope = scope.resolveScope(line)
        import_resolver = CSSImportResolver(filename, env)

        for i in range(len(tokens)):
            token = tokens[i]
            if token["line"] <= line and line <= token["line"] + token["text"].count("\n") and token["column"] <= column and column <= token["column"] + len(token["text"]) - 1:
                if self.styleClassifier.is_directive(token["style"]) and isinstance(self, LessScanner):
                    # If the entity at the position is an "@"-directive, it
                    # could be a Less variable reference.
                    i -= 1
                    while i > 0:
                        if self.styleClassifier.is_operator(tokens[i]["style"]):
                            if tokens[i]["text"] == ":":
                                # Less @variable reference.
                                scope.define(Import("@*", "")) # for import resolver
                                return GotoDefinitionContext(scope, "@" + token["text"], import_resolver=import_resolver)
                        elif tokens[i]["text"] == ";":
                            # Not a Less @variable reference; either @media
                            # directive or @variable declaration.
                            return None
                        i -= 1
                elif self.styleClassifier.is_value(token["style"], tokens[:i]) and isinstance(self, SCSSScanner) and i > 0 and self.styleClassifier.is_operator(tokens[i - 1]["style"]) and tokens[i - 1]["text"] == "$":
                    # SCSS $variable reference.
                    scope.define(Import("$*", "")) # for import resolver
                    return GotoDefinitionContext(scope, "$" + token["text"], import_resolver=import_resolver)
            elif token["line"] > line:
                break

        return None

    def getCallTipContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getCallTipContext()."""
        return None # TODO: ?

    def getFindReferencesContext(self, filename, position, env={}):
        """Implementation of AbstractScanner.getFindReferences()."""
        return None # TODO: ?

    def handle_token(self, style, text, start_column, start_line, **otherArgs):
        """Called for each token parsed by a Scintilla lexer.
        This CILE only looks for a subset of tokens.
        Parses out element, class, and id selectors as well as Less/SCSS
        variable definitions.
        @param style The style of the parsed token.
        @param text The text of the parsed token.
        @param start_column The column number the parsed token is on.
        @param start_line The line number the parsed token is on.
        @param otherArgs Other token properties.
        """
        self.tokens.append({"style": style, "text": text, "line": start_line + 1, "column": start_column}) # track for use by getCompletionContext()

        if self.styleClassifier.is_operator(style):
            if text == '}' or ';' in text:
                if text == '}':
                    self.blockLevel = max(self.blockLevel - 1, 0)
                    if (len(self.nestedSelectors) > 0):
                        self.nestedSelectors.pop()
                        for css_selector in self.nestedScopes.pop():
                            css_selector.lineend = start_line
                if self.blockLevel == 0 or isinstance(self, (LessScanner, SCSSScanner)):
                    self.selector = [] # start looking for selectors
                    self.selectorStartLine = None # need to reset on ';' (Less, SCSS)
                    self.ignoreStatement = False
                return
            elif (text == '{' or text == ',' or '(' in text) and \
                 self.selector and len(self.selector) > 0 and \
                 (self.blockLevel == 0 or isinstance(self, (LessScanner, SCSSScanner))) and \
                 self.parenLevel == 0 and not self.ignoreStatement and \
                 not (isinstance(self, SCSSScanner) and self.selector[0] == '@' and
                      len(self.selector) > 1 and
                      self.selector[1] == 'include'):
                selectorText = ''.join(self.selector).strip()
                self.nestedSelectors.append(selectorText)
                self.nestedScopes.append([])
                if CSSSelector.CSS_ID_OR_CLASS.search(selectorText):
                    # Parse out each id and class being used in the selector
                    # and create an individual selector for that target.
                    for selector in CSSSelector.CSS_ID_OR_CLASS.findall(selectorText):
                        if isinstance(self, (LessScanner, SCSSScanner)):
                            # Use the fully expanded name.
                            selectorText = ' '.join(self.nestedSelectors).replace('&', '')
                        css_selector = CSSSelector(selectorText,
                                                   self.selectorStartLine,
                                                   selector[0])
                        self.cile.addSelector(css_selector)
                        self.nestedScopes[-1].append(css_selector)
                else:
                    # No ids or classes in the selector; use its last element as
                    # the target.
                    selector = CSSSelector.CSS_IDENT.search(selectorText)
                    if selector:
                        if isinstance(self, (LessScanner, SCSSScanner)):
                            # Use the fully expanded name.
                            selectorText = ' '.join(self.nestedSelectors).replace('&', '')
                        css_selector = CSSSelector(selectorText,
                                                   self.selectorStartLine,
                                                   selector.group(1))
                        self.cile.addSelector(css_selector)
                        self.nestedScopes[-1].append(css_selector)
                    else:
                        log.warn("Unable to process CSS selector '%s'" % selectorText)

                if text == '{':
                    if isinstance(self, (LessScanner, SCSSScanner)):
                        # Continue with the next, nested selector.
                        self.selector = []
                    else:
                        # Stop looking for selectors until encountering '}'.
                        self.selector = None
                    self.blockLevel += 1
                elif text == ',':
                    # Continue with the next selector.
                    self.selector = []
                    self.nestedSelectors.pop() # only nest on '{'
                elif '(' in text:
                    # Less mixin; stop looking for selectors until encountering
                    # ')'.
                    self.selector = None
                    self.parenLevel += 1
                self.selectorStartLine = None
                return
            elif text == '{':
                # '{' encountered without a valid selector.
                self.blockLevel += 1
            elif text == ')':
                self.parenLevel = max(self.parenLevel - 1, 0)
            elif text == ':' and self.selector and \
                 ((isinstance(self, LessScanner) and self.selector[0] == '@') or \
                  (isinstance(self, SCSSScanner) and self.selector[0] == '$')):
                # Less or SCSS variable. Add it to the file scope or local
                # scope(s).
                variable = CSSVariable(''.join(self.selector).strip(),
                                       self.selectorStartLine)
                if len(self.nestedSelectors) == 0:
                    self.cile.addVariable(variable) # file scope
                else:
                    for css_selector in self.nestedScopes[-1]:
                        css_selector.addVariable(variable) # local scope
                # Stop looking for selectors until encountering ';'.
                self.ignoreStatement = True
        elif self.styleClassifier.is_comment(style):
            # Embedded comments within selectors are unlikely, but handle them
            # anyway just in case.
            return

        if self.selector == None:
            return # not looking for selectors right now

        if len(self.selector) > 0 or not re.match(self.whitespace, text):
            if text[0] == ' ' and len(self.selector) > 0 and \
               self.selector[-1] == ':':
                # Less and SCSS allow for nested selectors. "foo:bar" should be
                # considered a selector, while "foo: bar" should not be.
                self.ignoreStatement = True
                return
            self.selector.append(text)
            if not self.selectorStartLine:
                self.selectorStartLine = start_line

    def scan_purelang(self, text):
        """Scans the given CSS buffer text, feeding parsed tokens to this CILE's
        processor.
        Instead of passing in multi-language text (e.g. HTML, CSS, JS),
        pre-parse out the CSS tokens and then feed them directly to
        `handle_token()`.
        @param text The CSS text to parse and process.
        """
        CSSLexer().tokenize_by_style(text, self.handle_token)

    def appendToCixRoot(self, cixroot):
        """Appends to the given CixRoot the CIX representation of the parsed
        CSS.
        @param cixroot The CixRoot to append to.
        """
        self.cile.appendCixFileTo(cixroot)

    def appendToCixModule(self, cixmodule):
        """Appends to the given CixModule the CIX representation of the parsed
        CSS.
        This is used to append parsed CSS to a multi-language file currently
        being processed.
        @param cixmodule The CixModule to append to.
        """
        self.cile.appendCixToModule(cixmodule)

class LessScanner(CSSScanner):
    pass

class SCSSScanner(CSSScanner):
    pass

class HTMLCSSScanner(CSSScanner, AbstractUDLSubScanner):
    from language.legacy.css.stdlib import CSS_STDLIB_FILE
    def __init__(self, stdlib_file=CSS_STDLIB_FILE):
        from language.legacy.html.scanner import HTMLLexer
        super(HTMLCSSScanner, self).__init__(stdlib_file, HTMLLexer)

    @property
    def namespace(self):
        """Implementation of AbstractUDLSubScanner.namespace."""
        return "CSS"

    def prepForUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.prepForUDLTokens()."""
        self.scan("")

    def handleUDLToken(self, **kwargs):
        """Implementation of AbstractUDLSubScanner.handleUDLToken()."""
        self.handle_token(**kwargs)

    def doneWithUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.doneWithUDLTokens()."""
        return self.cile.toAbstractSymbol(self.builtInScope)

    def getUDLCompletionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCompletionContext()."""
        # The UDL lexer produces slightly different tokens than expected.
        # Normalize them. Also, The CSS scanner expects 1-based lines, but UDL
        # gives 0-based lines.
        for token in tokens:
            token["line"] = token["start_line"] + 1
            token["column"] = token["start_column"]
        return self._getCompletionContext(filename, position, env, tokens, line, column, scope)

    def getUDLGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLGotoDefinitionContext()."""
        # The UDL lexer produces slightly different tokens than expected.
        # Normalize them. Also, The CSS scanner expects 1-based lines, but UDL
        # gives 0-based lines.
        for token in tokens:
            token["line"] = token["start_line"] + 1
            token["column"] = token["start_column"]
        return self._getGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope)

    def getUDLCallTipContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLCallTipContext()."""
        return None

    def getUDLFindReferencesContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLSubScanner.getUDLFindReferencesContext()."""
        return None

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    from language.legacy.css.stdlib import CSS_STDLIB_FILE, SCSS_STDLIB_FILE
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    parser = argparse.ArgumentParser(description="Scan CSS source files")
    parser.add_argument("-css", action="store_const", const=True, default=True)
    parser.add_argument("-less", action="store_const", const=True)
    parser.add_argument("-scss", action="store_const", const=True)
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    if args.less:
        scanner = LessScanner(CSS_STDLIB_FILE)
    elif args.scss:
        scanner = SCSSScanner(SCSS_STDLIB_FILE)
    elif args.css:
        scanner = CSSScanner(CSS_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
