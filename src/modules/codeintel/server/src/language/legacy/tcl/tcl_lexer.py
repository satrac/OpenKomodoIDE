# Copyright (c) 2005-2006 ActiveState Software Inc.
#
# Contributors:
#   Eric Promislow (EricP@ActiveState.com)

"""
Tcl lexing support for codeintel/tclcile.py

Get all the lexed tokens from SilverCity, and then return them
on demand to the caller (usually a Tcl pseudo-parser).

Usage:
import tcl_lexer
lexer = lex_wrapper.Lexer(code)
while 1:
    tok = lexer.get_next_token()
    if tok[0] == EOF_STYLE:
        break;
    # tok is an array of (style, text, start-col, start-line, end-col, end-line)
    # column and line numbers are all zero-based.
"""

import copy
import re
import sys
import string

import SilverCity
from SilverCity import ScintillaConstants
from SilverCity.Lexer import Lexer
from language.legacy.common import shared_lexer
from language.legacy.common.shared_lexer import EOF_STYLE

class TclLexerClassifier:
    """ This classifier is similar to the parser-level classifier, but
    it works on the SilverCity "raw" tokens as opposed to the
    tokens that get created by the lexer layer.  There should be some
    folding though."""

    def is_comment(self, ttype):
        return ttype == ScintillaConstants.SCE_TCL_COMMENT

    @property
    def style_comment(self):
        return ScintillaConstants.SCE_TCL_COMMENT

    @property
    def style_default(self):
        return ScintillaConstants.SCE_TCL_DEFAULT

    @property
    def style_operator(self):
        return ScintillaConstants.SCE_TCL_OPERATOR

keywords = ["after", "append", "apply", "array", "auto_execok",
            "auto_import", "auto_load", "auto_load_index", "auto_mkindex",
            "auto_qualify", "auto_reset", "bgerror", "binary", "break",
            "case", "catch", "cd", "chan", "clock", "close", "concat",
            "continue", "dde", "dict", "else", "then", "elseif",
            "encoding", "eof", "error", "eval", "exec", "exit", "expr",
            "fblocked", "fconfigure", "fcopy", "file", "fileevent",
            "flush", "for", "foreach", "format", "gets", "glob", "global",
            "history", "if", "incr", "info", "interp", "join", "lappend",
            "lassign", "lindex", "linsert", "list", "llength", "load",
            "lrange", "lrepeat", "lreplace", "lreverse", "lsearch", "lset",
            "lsort", "namespace", "open", "package", "parray", "pid",
            "pkg_compareExtension", "pkg_mkIndex", "proc", "puts", "pwd",
            "read", "regexp", "registry", "regsub", "rename", "return",
            "scan", "seek", "set", "socket", "source", "split", "string",
            "subst", "switch", "tcl_findLibrary", "tell", "time", "trace",
            "unknown", "unload", "unset", "update", "uplevel", "upvar",
            "variable", "vwait", "while",
            "bell", "bind", "bindtags", "button", "canvas", "checkbutton",
            "clipboard", "destroy", "entry", "event", "focus", "font",
            "frame", "grab", "grid", "image", "label", "labelframe",
            "listbox", "lower", "menu", "menubutton", "message", "option",
            "pack", "panedwindow", "place", "radiobutton", "raise",
            "scale", "scrollbar", "selection", "spinbox", "text", "tk",
            "tk_chooseColor", "tk_chooseDirectory", "tk_getOpenFile",
            "tk_getSaveFile", "tk_menuSetFocus", "tk_messageBox",
            "tk_popup", "tk_textCopy", "tk_textCut", "tk_textPaste",
            "tkwait", "toplevel", "ttk::button", "ttk::checkbutton",
            "ttk::combobox", "ttk::entry", "ttk::frame", "ttk::label",
            "ttk::labelframe", "ttk::menubutton", "ttk::notebook",
            "ttk::panedwindow", "ttk::progressbar", "ttk::radiobutton",
            "ttk::scrollbar", "ttk::separator", "ttk::sizegrip",
            "ttk::style", "ttk::treeview", "ttk::style", "winfo", "wm"]

# Codeintel will assume we're running v 8.6+
# Other clients can pick and choose the keywords they want.
v8_6_keywords = ["coroutine", "finally", "lmap", "my", "next", "on",
                 "oo::class", "oo::copy", "oo::define", "oo::objdefine",
                 "oo::object", "self", "tailcall", "throw", "trap", "try",
                 "ttk::spinbox", "yield", "yieldto", "zlib",
                 # Commands for oo::define and oo::objdefine
                 "constructor", "deletemethod", "destructor", "export",
                 "filter", "forward", "method", "mixin", "renamemethod",
                 "superclass", "unexport"]

class ScintillaTclLexer(Lexer):
    def __init__(self):
        self._properties = SilverCity.PropertySet()
        self._lexer = SilverCity.find_lexer_module_by_id(ScintillaConstants.SCLEX_TCL)
        self._keyword_lists = [
            SilverCity.WordList(' '.join(sorted(keywords + v8_6_keywords)))
        ]

#---- global data

op_re = re.compile(r'([\\\{\}\[\]])')

class TclLexer(shared_lexer.Lexer):
    def __init__(self, code):
        shared_lexer.Lexer.__init__(self)
        self.q = []
        self.classifier = TclLexerClassifier()
        ScintillaTclLexer().tokenize_by_style(code, self._fix_token_list)
        self.prepare_token_list_for_use()
        self.string_types = [ScintillaConstants.SCE_TCL_STRING,
                         ScintillaConstants.SCE_TCL_CHARACTER,
                         ScintillaConstants.SCE_TCL_LITERAL
                         ]

    def _fix_token_list(self, **tok):
        """ Same as perl_lexer: split op tokens into separate
            recognizable operators.
        """
        if tok['start_column'] > shared_lexer.MAX_REASONABLE_LIMIT:
            return
        tval = tok['text']
        if tok['style'] == ScintillaConstants.SCE_TCL_OPERATOR and len(tval) > 1:
            # In Tcl rely on white-space to separate operators except for braces, brackets, and backslashes
            new_tokens = [x for x in op_re.split(tval) if x] # drop empties
            if len(new_tokens) == 1:
                self.complete_token_push(tok)
            else:
                col = tok['start_column']
                for stxt in new_tokens:
                    new_tok = copy.copy(tok)
                    new_tok['text'] = stxt
                    new_tok['start_column'] = col
                    new_tok['end_column'] = col + len(stxt) - 1
                    col = new_tok['end_column']
                    self.q.append(new_tok)
        else:
            self.complete_token_push(tok)

def provide_sample_code():
    return r"""# ----------------------------------------------------------------------------
#  Command Dialog::create
# ----------------------------------------------------------------------------
proc Dialog::create { path args } {
    global   tcl_platform
    variable _widget

    array set maps [list Dialog {} .bbox {}]
    array set maps [Widget::parseArgs Dialog $args]

    # Check to see if the -class flag was specified
    set dialogClass "Dialog"
    array set dialogArgs $maps(Dialog)
    if { [info exists dialogArgs(-class)] } {
	set dialogClass $dialogArgs(-class)
    }

    puts "Test a long string" ; # proper comment
    puts "bogus comment follows -- no ;" # proper comment
}

"""


if __name__ == "__main__":
    shared_lexer.main(sys.argv, provide_sample_code, TclLexer)
