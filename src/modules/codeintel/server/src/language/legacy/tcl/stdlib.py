# Copyright 2016-2017 ActiveState, Inc. All rights reserved.

"""Tcl stdlib importer."""

from language.common import Scope, Keyword

# The DB file path to store Ruby stdlib symbols under.
TCL_STDLIB_FILE = ":tcl:"

# Tcl's keywords to add to the stdlib.
# Keep KEYWORDS synced with komodo/src/languages/koTclLanguage.py
KEYWORDS = (["after", "append", "apply", "array", "auto_execok",
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
             "ttk::style", "ttk::treeview", "ttk::style", "winfo", "wm"] +
            # Tcl 8.6 keywords.
            ["coroutine", "finally", "lmap", "my", "next", "on",
             "oo::class", "oo::copy", "oo::define", "oo::objdefine",
             "oo::object", "self", "tailcall", "throw", "trap", "try",
             "ttk::spinbox", "yield", "yieldto", "zlib",
             # Commands for oo::define and oo::objdefine
             "constructor", "deletemethod", "destructor", "export",
             "filter", "forward", "method", "mixin", "renamemethod",
             "superclass", "unexport"])

def scan(stdlib_file):
    """Imports the Tcl stdlib into the database."""
    stdlib_scope = Scope()

    # Add keywords.
    for keyword in KEYWORDS:
        stdlib_scope.define(Keyword(keyword))

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)
