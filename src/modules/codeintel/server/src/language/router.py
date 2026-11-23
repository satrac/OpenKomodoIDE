# The legacy Python scanner depends on the removed Python 2 "compiler"
# module.  Use the ast-based scanner for both Python 2 and Python 3 CIX
# groups; the language class selects the matching stdlib file.
from language.legacy.python.ast_scanner import PythonASTScanner
Python3Scanner = PythonASTScanner

from language.legacy.python import stdlib as python_stdlib
from language.legacy.javascript.scanner import JavaScriptScanner, NodeJSScanner
from language.legacy.javascript import stdlib as javascript_stdlib
from language.legacy.perl.scanner import PerlScanner
from language.legacy.perl import stdlib as perl_stdlib
from language.legacy.php.scanner import PHPScanner
from language.legacy.php import stdlib as php_stdlib
from language.legacy.ruby.scanner import RubyScanner
from language.legacy.ruby import stdlib as ruby_stdlib
from language.legacy.tcl.scanner import TclScanner
from language.legacy.tcl import stdlib as tcl_stdlib
from language.legacy.html.scanner import HTMLScanner
from language.legacy.html import stdlib as html_stdlib
from language.legacy.css.scanner import CSSScanner, LessScanner, SCSSScanner
from language.legacy.css import stdlib as css_stdlib
from language.legacy.django.scanner import DjangoScanner
from language.legacy.handlebars.scanner import HandlebarsScanner
from language.legacy.jsx.scanner import JSXScanner
from language.legacy.mustache.scanner import MustacheScanner
from language.legacy.rhtml.scanner import RHTMLScanner
from language.legacy.xul.scanner import XULScanner
from language.legacy.xul import stdlib as xul_stdlib

import inspect

class Language:

    supports = ["completions", "calltips", "symbolbrowser", "symbollist", "symbolscope", "scanner", "gotodef", "jumpsections", "findreferences"]
    scanner = None
    name = None

    completion_prefixes = {}
    completion_suffixes = {}

    completion_word_characters = "\\w_\\-" # Regex (escaped)
    completion_query_characters = None # Uses word_characters if None
    completion_trigger_blacklist = "\\s,;" # Regex(escaped) these characters cannot trigger completions
    completion_trigger_empty_lines = False # Whether to trigger completions on empty lines

    FEATURE_COMPLETIONS = "completions"
    FEATURE_CALLTIPS = "calltips"
    FEATURE_SYMBOLBROWSER = "symbolbrowser"
    FEATURE_SYMBOLLIST = "symbollist"
    FEATURE_SYMBOLSCOPE = "symbolscope"
    FEATURE_SCANNER = "scanner"
    FEATURE_GOTODEF = "gotodef"
    FEATURE_JUMPSECTIONS = "jumpsections"
    FEATURE_FINDREFERENCeS = "findreferences"

class Languages:

    langmap = {}
    extmap = {}

    @staticmethod
    def getLanguages():
        languages = []

        for prop in dir(Languages):
            value = getattr(Languages, prop)

            if inspect.isclass(value) and issubclass(value, Language):
                languages.append(value)

        return languages

    @staticmethod
    def getLanguage(name):
        for language in Languages.getLanguages():
            if language.name == name:
                Languages.langmap[name] = language()
                return Languages.langmap[name]

        return None


    @staticmethod
    def getLanguageFromExt(ext):
        for language in Languages.getLanguages():
            if ext in language.ext:
                Languages.extmap[ext] = language()
                return Languages.extmap[ext]

        return None

    class Python3(Language):

        name = "Python3"

        stdlib = python_stdlib
        cix = python_stdlib.PYTHON3_STDLIB_FILE
        catalogs = (python_stdlib.CATALOG_PYTHON3_PYWIN32_FILE,)
        keywords = python_stdlib.PYTHON3_KEYWORDS

        ext = ["py"]

        extrapaths = "PYTHONPATH"

        _scanner = None

        @property
        def scanner(self):
            if Python3Scanner is None:
                return None
            Parent = Languages.Python3
            if not Parent._scanner:
                Parent._scanner = Python3Scanner(self.cix)

            return Parent._scanner

    class JavaScript(Language):

        name = "JavaScript"

        completion_word_characters = "\\w_\\-\\$"

        stdlib = javascript_stdlib
        cix = javascript_stdlib.JAVASCRIPT_STDLIB_FILE
        catalogs = (javascript_stdlib.CATALOG_ANGULAR_FILE,
                    javascript_stdlib.CATALOG_DOJO_FILE,
                    javascript_stdlib.CATALOG_EMBER_FILE,
                    javascript_stdlib.CATALOG_JQUERY_FILE,
                    javascript_stdlib.CATALOG_MOCHIKIT_FILE,
                    javascript_stdlib.CATALOG_PROTOTYPE_FILE,
                    javascript_stdlib.CATALOG_XBL_FILE,
                    javascript_stdlib.CATALOG_XPCOM_FILE,
                    javascript_stdlib.CATALOG_YUI_FILE)
        keywords = javascript_stdlib.KEYWORDS

        ext = ["js"]

        extrapaths = "JAVASCRIPTLIB"

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.JavaScript
            if not Parent._scanner:
                Parent._scanner = JavaScriptScanner(self.cix)

            return Parent._scanner

    class NodeJS(Language):

        name = "Node.js"

        completion_word_characters = "\\w_\\-\\$"

        stdlib = javascript_stdlib
        cix = javascript_stdlib.NODEJS_STDLIB_FILE
        catalogs = None
        keywords = javascript_stdlib.KEYWORDS

        ext = [] # .js is used by JavaScript

        extrapaths = "JAVASCRIPTLIB"

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.NodeJS
            if not Parent._scanner:
                Parent._scanner = NodeJSScanner(self.cix)

            return Parent._scanner

    class Perl(Language):

        name = "Perl"

        completion_word_characters = "\\w_\\-\\$"

        stdlib = perl_stdlib
        cix = perl_stdlib.PERL_STDLIB_FILE
        catalogs = None
        keywords = perl_stdlib.KEYWORDS

        ext = ["pl", "pm"]

        extrapaths = "PERL5LIB"

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.Perl
            if not Parent._scanner:
                Parent._scanner = PerlScanner(self.cix)

            return Parent._scanner

    class PHP(Language):

        name = "PHP"

        completion_word_characters = "\\w_\\-\\$"
        completion_query_characters = "\\w_\\-\\$"

        # TODO: determine when to use PHP_5_STDLIB_FILE and PHP_7_STDLIB_FILE
        stdlib = php_stdlib
        cix = (php_stdlib.PHP_7_STDLIB_FILE,
               php_stdlib.PHP_5_STDLIB_FILE)
        catalogs = (php_stdlib.CATALOG_DRUPAL_FILE,
                    php_stdlib.CATALOG_PECL_FILE)
                    #TODO: php_stdlib.CATALOG_PHPUNIT_FILE
        keywords = php_stdlib.KEYWORDS

        ext = ["php"]

        extrapaths = "PHPLIB"

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.PHP
            if not Parent._scanner:
                Parent._scanner = PHPScanner(Languages.HTML.cix[0], self.cix[0])

            return Parent._scanner

    class Ruby(Language):

        name = "Ruby"

        completion_word_characters = "\\w_\\-\\@"

        stdlib = ruby_stdlib
        cix = ruby_stdlib.RUBY_STDLIB_FILE
        catalogs = (ruby_stdlib.CATALOG_RAILS_FILE,)
        keywords = ruby_stdlib.KEYWORDS

        ext = ["rb"]

        extrapaths = "RUBYLIB"

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.Ruby
            if not Parent._scanner:
                Parent._scanner = RubyScanner(self.cix)

            return Parent._scanner

    class Tcl(Language):

        name = "Tcl"

        stdlib = tcl_stdlib
        cix = tcl_stdlib.TCL_STDLIB_FILE
        catalogs = None
        keywords = tcl_stdlib.KEYWORDS

        ext = ["tcl"]

        extrapaths = None # not yet implemented

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.Tcl
            if not Parent._scanner:
                Parent._scanner = TclScanner(self.cix)

            return Parent._scanner

    class HTML(Language):

        name = "HTML"

        completion_query_characters = "\\w_\\-"
        completion_suffixes = { "ELEM": ">" }

        stdlib = html_stdlib
        cix = (html_stdlib.HTML5_STDLIB_FILE,

               html_stdlib.HTML2_STDLIB_FILE,
               html_stdlib.HTML2STRICT_STDLIB_FILE,
               html_stdlib.HTML2LEVEL1_STDLIB_FILE,
               html_stdlib.HTML2LEVEL2_STDLIB_FILE,
               html_stdlib.HTML2STRICTLEVEL1_STDLIB_FILE,
               html_stdlib.HTML2STRICTLEVEL2_STDLIB_FILE,

               html_stdlib.HTML_STDLIB_FILE,
               html_stdlib.HTMLSTRICT_STDLIB_FILE,
               html_stdlib.HTMLLEVEL1_STDLIB_FILE,
               html_stdlib.HTMLLEVEL2_STDLIB_FILE,
               html_stdlib.HTMLSTRICTLEVEL1_STDLIB_FILE,
               html_stdlib.HTMLSTRICTLEVEL2_STDLIB_FILE,

               html_stdlib.HTML32_STDLIB_FILE,
               html_stdlib.HTML32DRAFT_STDLIB_FILE,
               html_stdlib.HTML32FINAL_STDLIB_FILE,

               html_stdlib.HTML40_STDLIB_FILE,
               html_stdlib.HTML40FRAMESET_STDLIB_FILE,
               html_stdlib.HTML40TRANSITIONAL_STDLIB_FILE,

               html_stdlib.HTML41_STDLIB_FILE,
               html_stdlib.HTML41FRAMESET_STDLIB_FILE,
               html_stdlib.HTML41TRANSITIONAL_STDLIB_FILE,

               html_stdlib.XHTML10STRICT_STDLIB_FILE,
               html_stdlib.XHTML10FRAMESET_STDLIB_FILE,
               html_stdlib.XHTML10TRANSITIONAL_STDLIB_FILE,

               html_stdlib.XHTML11_STDLIB_FILE,
               html_stdlib.XHTML11PLUSMATHML20_STDLIB_FILE,
               html_stdlib.XHTML11PLUSMATHML20PLUSSVG11_STDLIB_FILE)
        catalogs = None
        keywords = None

        ext = ["html", "html5"]

        extrapaths = None # N/A

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.HTML
            if not Parent._scanner:
                Parent._scanner = HTMLScanner(self.cix[0])

            return Parent._scanner

    class HTML5(HTML):

        name = "HTML5"

    class CSS(Language):

        name = "CSS"

        completion_suffixes = { "ATTR": ": " }
        completion_word_characters = "!\\w_@#\\.\\-"
        completion_trigger_blacklist = "{\\s;,"

        stdlib = css_stdlib
        cix = css_stdlib.CSS_STDLIB_FILE
        catalogs = None
        keywords = css_stdlib.KEYWORDS

        ext = ["css", "less", "scss"]

        extrapaths = None # N/A

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.CSS
            if not Parent._scanner:
                Parent._scanner = CSSScanner(self.cix)

            return Parent._scanner

    class Less(CSS):

        name = "Less"

        completion_suffixes = { "ATTR": ": " }
        completion_word_characters = "!\\w_@#\\.\\-"
        completion_trigger_blacklist = "{\\s"

        ext = ["less"]

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.Less
            if not Parent._scanner:
                Parent._scanner = LessScanner(self.cix)

            return Parent._scanner

    class SCSS(CSS):

        name = "SCSS"

        completion_suffixes = { "ATTR": ": " }
        completion_word_characters = "!\\w_@\\$#\\.\\-"
        completion_trigger_blacklist = "{\\s"

        cix = css_stdlib.SCSS_STDLIB_FILE

        ext = ["scss"]

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.Less
            if not Parent._scanner:
                Parent._scanner = SCSSScanner(self.cix)

            return Parent._scanner

    class Django(Language):

        name = "Django"

        stdlib = None
        cix = None
        catalogs = None
        keywords = None

        ext = ["django"]

        extrapaths = None # N/A

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.Django
            if not Parent._scanner:
                Parent._scanner = DjangoScanner()

            return Parent._scanner

    class Handlebars(Language):

        name = "Handlebars"

        stdlib = html_stdlib
        cix = html_stdlib.HTML5_STDLIB_FILE
        catalogs = None
        keywords = None

        ext = ["hbs"]

        extrapaths = None # N/A

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.Handlebars
            if not Parent._scanner:
                Parent._scanner = HandlebarsScanner(self.cix)

            return Parent._scanner

    class JSX(Language):

        name = "JSX"

        stdlib = html_stdlib
        cix = html_stdlib.HTML5_STDLIB_FILE
        catalogs = None
        keywords = None

        ext = ["jsx"]

        extrapaths = None # N/A

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.JSX
            if not Parent._scanner:
                Parent._scanner = JSXScanner(self.cix)

            return Parent._scanner

    class Mustache(Language):

        name = "Mustache"

        stdlib = html_stdlib
        cix = html_stdlib.HTML5_STDLIB_FILE
        catalogs = None
        keywords = None

        ext = ["mustache"]

        extrapaths = None # N/A

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.Mustache
            if not Parent._scanner:
                Parent._scanner = MustacheScanner(self.cix)

            return Parent._scanner

    class RHTML(Language):

        name = "RHTML"

        stdlib = html_stdlib
        cix = html_stdlib.HTML5_STDLIB_FILE
        catalogs = None
        keywords = None

        ext = ["rhtml"]

        extrapaths = None # N/A

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.RHTML
            if not Parent._scanner:
                Parent._scanner = RHTMLScanner(self.cix)

            return Parent._scanner

    class XUL(Language):

        supports = [Language.FEATURE_COMPLETIONS, Language.FEATURE_SYMBOLBROWSER, Language.FEATURE_GOTODEF]
        name = "XUL"

        stdlib = xul_stdlib
        cix = xul_stdlib.XUL_STDLIB_FILE
        catalogs = None
        keywords = None

        ext = ["xul"]

        extrapaths = None # N/A

        _scanner = None

        @property
        def scanner(self):
            Parent = Languages.XUL
            if not Parent._scanner:
                Parent._scanner = XULScanner(self.cix)

            return Parent._scanner
