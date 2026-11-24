#!python
# Copyright (c) 2026 ActiveState Software Inc.
# See the file LICENSE.txt for licensing information.

"""Raku language service implementation.

Raku is the language formerly known as Perl 6.  Syntax highlighting uses a
dedicated UDL lexer (``src/udl/udl/raku.udl``, compiled to ``Raku.lexres``)
rather than Scintilla's Perl lexer.
"""

import re

from xpcom import components
from koUDLLanguageBase import KoUDLLanguage


class KoRakuLanguage(KoUDLLanguage):
    name = "Raku"
    lexresLangName = "Raku"
    lang_from_udl_family = {'SSL': 'Raku'}
    _reg_desc_ = "%s Language" % name
    _reg_contractid_ = "@activestate.com/koLanguage?language=%s;1" % name
    _reg_clsid_ = "{0edd96c3-8bcc-45e1-a391-00e31d4934e3}"
    _reg_categories_ = [("komodo-language", name)]

    modeNames = ['raku', 'perl6']
    shebangPatterns = [
        re.compile(r'\A#!.*(?:raku|perl6|rakudo).*$', re.IGNORECASE | re.MULTILINE),
    ]
    primary = 1
    internal = 0
    accessKey = 'k'

    defaultExtension = ".raku"
    extraFileAssociations = ["*.rakumod", "*.rakutest", "*.rakudoc",
                             "*.nqp", "*.pod6", "*.p6", "*.pm6", "*.t6"]
    downloadURL = "https://raku.org/"
    searchURL = "https://docs.raku.org/search?q=%W"
    commentDelimiterInfo = {"line": ["#"]}
    variableIndicators = "$@%&"
    namedBlockRE = (r"^[ \t]*?(?:sub|method|class|role|grammar|module|package|"
                    r"token|rule|regex)\s+[\w:-]+")
    namedBlockDescription = "Raku routines, classes, roles and grammars"

    _lineup_open_chars = "([{"
    _lineup_close_chars = ")]}"
    _dedenting_statements = ['return', 'return-rw', 'last', 'next', 'redo',
                             'die', 'fail', 'take', 'emit', 'done']

    supportsSmartIndent = "brace"
    importPref = "rakuExtraPaths"

    sample = """#!/usr/bin/env raku
my $name = 'Raku';
say "Hello, $name!";

class Greeter {
    method greet($who) {
        return "Hello, $who!";
    }
}

my @languages = <Raku Perl Python>;
for @languages -> $language {
    say $language;
}
"""

    def get_interpreter(self):
        if self._interpreter is None:
            self._interpreter = components.classes[
                "@activestate.com/koAppInfoEx?app=Raku;1"].getService()
        return self._interpreter

    # Raku keywords, declarators, phasers, routines and common core types.
    _keywords = [
        "BEGIN", "CHECK", "INIT", "END", "ENTER", "LEAVE", "KEEP", "UNDO",
        "FIRST", "NEXT", "LAST", "QUIT",
        "also", "and", "any", "async", "augment", "await",
        "bag", "bags", "but",
        "cancel", "catch", "category", "class", "constant", "continuing",
        "default", "die", "do", "does", "done",
        "else", "elsif", "emit", "enum", "eq", "eqv", "export",
        "fail", "false", "for",
        "gather", "given", "grammar",
        "handles", "has", "hyper", "hyperwhatever",
        "if", "import", "in", "is", "isa",
        "last", "lazy", "leave", "let", "like", "loop",
        "macro", "make", "map", "maybe", "method", "module", "monitor", "multi",
        "my",
        "need", "next", "no", "none", "not", "note", "native",
        "of", "only", "or", "our", "optional",
        "package", "pass", "plan", "proto", "proceed", "prompt", "put",
        "quietly",
        "race", "react", "redo", "regex", "require", "return", "return-rw",
        "reverse", "role", "rule", "rw",
        "say", "self", "slang", "start", "state", "sub", "subset",
        "supersede", "supply",
        "take", "temp", "trait", "true", "try", "twigil",
        "unit", "unless", "until", "use",
        "when", "where", "while", "with", "without",
        "x", "xor",
        "yield",
        # Core types and values
        "Any", "Array", "Associative", "atomicint", "Attribute", "Backtrace",
        "Bag", "BagHash", "Blob", "Bool", "Buf", "Callable", "Capture",
        "Channel", "Code", "Complex", "Cool", "Date", "DateTime", "Duration",
        "Exception", "Failure", "FatRat", "Grammar", "Hash", "Instant", "Int",
        "IntStr", "Iterable", "Iterator", "Junction", "Label", "List", "Lock",
        "Map", "Match", "Mix", "MixHash", "Mu", "NFC", "NFD", "NFKC", "NFKD",
        "Nil", "Num", "Numeric", "NumStr", "ObjAt", "Pair", "Positional",
        "Proc", "Promise", "Proxy", "Range", "Rat", "RatStr", "Real", "Regex",
        "Routine", "Scalar", "Seq", "Set", "SetHash", "Signature", "Slip",
        "Stash", "Str", "Stringy", "Sub", "Submethod", "Supply", "Tap",
        "Thread", "UInt", "UTF8", "Version", "Whatever", "WhateverCode",
        # Common subroutines and methods
        "abs", "all", "antipair", "any", "append", "Array", "AT-POS", "Bag",
        "base", "basename", "batch", "bell", "bind", "bless", "Bool", "bools",
        "categorize", "chars", "chdir", "chmod", "chomp", "chop", "chr",
        "classify", "clone", "close", "comb", "combinations", "contains",
        "copy", "cos", "cpu-cores", "Date", "day", "decode", "deepmap",
        "defined", "dir", "dirname", "dl", "duckmap", "elems", "encode",
        "end", "eof", "exit", "exp", "extension", "first", "flat", "flip",
        "fmt", "fullname", "gist", "grep", "hash", "head", "hh", "index",
        "indices", "IO", "is-absolute", "is-dir", "is-file", "join", "key",
        "keys", "kv", "last", "lc", "lines", "list", "log", "log10", "map",
        "match", "max", "min", "mix", "mkdir", "move", "next", "new", "nl-in",
        "nl-out", "not", "note", "open", "ord", "pair", "pairs", "parse",
        "permutations", "pick", "pop", "pop", "print", "printf", "prompt",
        "push", "put", "rand", "read", "redo", "reduce", "reverse", "rmdir",
        "roll", "roots", "rotate", "round", "run", "samecase", "say",
        "sort", "splice", "split", "sprintf", "sqrt", "squish", "start",
        "subst", "sum", "tail", "tc", "trim", "trim-leading", "trim-trailing",
        "truncate", "uc", "unique", "unlink", "unshift", "value", "values",
        "words", "write", "zip",
    ]
