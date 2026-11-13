# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Legacy scanner for XUL source code.
CodeIntel v2's original XUL parser has been adapted to operate with the new
CodeIntel v3 framework.
The parser tokenizes an input stream using the Scintilla UDL (via SilverCity),
and walks through the token list, producing something akin to an Abstract Syntax
Tree in the end for database storage.
Completion contexts also utilize the token list.
"""

import logging
import os
import re

from symbols import AbstractElement, AbstractAttribute
from completions import AbstractMemberCompletionContext, AbstractScopeCompletionContext
from language.common import Scope, Import, Element, AbstractScannerContext, AbstractSyntaxDescription

from language.legacy.common import koXMLTreeService
from language.legacy.udl.udl import UDLLexer, is_udl_m_style, is_udl_ssl_style, is_udl_csl_style, is_udl_css_style, is_udl_tpl_style, AbstractUDLScanner
from language.legacy.xul import stdlib
from language.legacy.javascript.scanner import HTMLJavaScriptScanner
from language.legacy.css.scanner import HTMLCSSScanner, CSSScopeCompletionContext

from db.model.helpers import fetchSymbolsInFile

import SilverCity
from SilverCity.ScintillaConstants import SCE_UDL_M_TAGSPACE, SCE_UDL_M_STAGO, SCE_UDL_M_ETAGC, SCE_UDL_M_TAGNAME, SCE_UDL_M_ATTRNAME, SCE_UDL_M_STRING, SCE_UDL_M_DEFAULT, SCE_UDL_M_OPERATOR, SCE_UDL_M_ETAGO

log = logging.getLogger("legacy.xul.scanner")
#log.setLevel(logging.DEBUG)

class XULLexer(UDLLexer):
    lang = "XUL"

class XULScannerContext(AbstractScannerContext):
    """Implementation of AbstractScannerContext for the XUL scanner."""
    def __init__(self, element):
        self._element = element

    @property
    def line(self):
        """Implementation of AbstractSymbolContext.line."""
        return self._element.start[0]

    @property
    def documentation(self):
        """Implementation of AbstractSymbolContext.documentation."""
        return None

    @property
    def signature(self):
        """Implementation of AbstractSymbolContext.signature."""
        return None

    def contains(self, line):
        """Implementation of AbstractScannerContext.contains()."""
        return line >= self.line and (line <= self._element.end[0])

class XULMemberCompletionContext(AbstractMemberCompletionContext):
    """Implementation of AbstractMemberCompletionContext."""
    @property
    def language(self):
        return "XUL"

class XULScopeCompletionContext(AbstractScopeCompletionContext):
    """Implementation of AbstractScopeCompletionContext."""
    @property
    def language(self):
        return "XUL"

class XULSyntaxDescription(AbstractSyntaxDescription):
    """Implementation of AbstractSyntaxDescription for XUL."""
    @property
    def symbolSeparator(self):
        """Implementation of AbstractSyntaxDescription.symbolSeparator."""
        return " "

    @property
    def callOperator(self):
        """Implementation of AbstractSyntaxDescription.callOperator."""
        return None

class XULScanner(AbstractUDLScanner):
    @property
    def namespace(self):
        """Implementation of AbstractUDLScanner.namespace."""
        return "XUL"

    @property
    def udlLexer(self):
        """Implementation of AbstractUDLScanner.udlLexer."""
        return XULLexer()

    @property
    def sslScanner(self):
        """Implementation of AbstractUDLScanner.sslScanner."""
        return None

    @property
    def cslScanner(self):
        """Implementation of AbstractUDLScanner.cslScanner."""
        if not hasattr(self, "_cslScanner"):
            self._cslScanner = HTMLJavaScriptScanner()
        return self._cslScanner

    @property
    def cssScanner(self):
        """Implementation of AbstractUDLScanner.cssScanner."""
        if not hasattr(self, "_cssScanner"):
            self._cssScanner = HTMLCSSScanner()
        return self._cssScanner

    @property
    def tplScanner(self):
        """Implementation of AbstractUDLScanner.tplScanner."""
        return None

    @property
    def namespace(self):
        """Implementation of AbstractUDLSubScanner.namespace."""
        return "XUL"

    def prepForUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.prepForUDLTokens."""
        self.xul_content = ""
        self._prev_end_line = 0

    def handleUDLToken(self, **kwargs):
        """Implementation of AbstractUDLSubScanner.handleUDLToken."""
        if kwargs["text"].startswith("<?xml"):
            return # ignore any legacy XML declaration
        if kwargs["start_line"] > self._prev_end_line:
            self.xul_content += "\n" * (kwargs["start_line"] - self._prev_end_line)
        self.xul_content += kwargs["text"]
        self._prev_end_line = kwargs["start_line"] + kwargs["text"].count("\n")

    def doneWithUDLTokens(self):
        """Implementation of AbstractUDLSubScanner.doneWithUDLTokens."""
        scope = Scope(self.builtInScope)
        try:
            tree = koXMLTreeService.getService().getTreeForContent(self.xul_content)
            if tree and tree.root is not None:
                for elem in tree.root.getiterator():
                    if elem.get("id"):
                        scope.define(Element("%s (%s)" % (elem.get("id"), elem.tag), scope, XULScannerContext(elem)))
        except:
            pass
        return scope

    def getUDLCompletionContext(self, filename, position, env, _tokens, line, column, scope):
        """Implementation of AbstractUDLScanner.getUDLCompletionContext()."""
        # Only keep significant tokens up to the completion position.
        tokens = []
        for token in _tokens:
            if is_udl_m_style(token["style"]):
                if token["start_line"] + 1 <= line: # UDL produces 0-based lines
                    if token["start_line"] + 1 < line: # UDL produces 0-based lines
                        # Keep all tag-related, non-whitespace tokens prior to
                        # the current line.
                        if token["style"] != SCE_UDL_M_TAGSPACE and token["style"] >= SCE_UDL_M_STAGO and token["style"] <= SCE_UDL_M_ETAGC:
                            tokens.append(token)
                        elif token["end_line"] + 1 >= line and token["style"] == SCE_UDL_M_DEFAULT and "<" in token["text"]: # UDL produces 0-based lines
                            token["text"] = token["text"][:position - token["start_index"]]
                            tokens.append(token) # possible bare '<' to give completions for
                    elif token["start_column"] < column:
                        # Keep all non-whitespace tokens in the current line
                        # prior to the current position, unless the current
                        # position exists within a whitespace token.
                        if token["style"] != SCE_UDL_M_TAGSPACE or (token["start_column"] + 1 <= column and column <= token["end_column"] + 1):
                            tokens.append(token)
                    elif token["start_column"] == column and token["style"] in (SCE_UDL_M_TAGNAME, SCE_UDL_M_ATTRNAME):
                        # Keep a tag or attribute name that starts at the
                        # current position.
                        tokens.append(token)
        leading_whitespace = False
        name_part = "" # the "already typed" part of a symbol completion

        if len(tokens) == 0:
            # If the position is at the beginning of the buffer, provide no
            # completions.
            return None

        # Pre-process the end of the token list.
        if tokens[-1]["style"] in (SCE_UDL_M_TAGNAME, SCE_UDL_M_ATTRNAME):
            # If the position is within an element or attribute name, keep track
            # of this name part.
            name_part = tokens[-1]["text"]
            tokens.pop()
        elif tokens[-1]["style"] == SCE_UDL_M_STRING and (tokens[-1]["end_column"] + 1 > column or len(tokens[-1]["text"]) == 1 or (not tokens[-1]["text"].endswith("'") and not tokens[-1]["text"].endswith('"'))):
            name_part = tokens[-1]["text"]
            tokens.pop()
            if tokens[-1]["style"] == SCE_UDL_M_OPERATOR and tokens[-1]["text"] == "=" and tokens[-2]["style"] == SCE_UDL_M_ATTRNAME:
                if tokens[-2]["text"].lower() == "style":
                    name_part = name_part.strip("\"'")
                    scope = scope.resolve("CSS")
                    return CSSScopeCompletionContext(scope, name_part, AbstractAttribute)

        # One more round of pre-processing.
        if tokens[-1]["style"] == SCE_UDL_M_TAGSPACE:
            # If the position is immediately after whitespace, keep track of
            # that since member completions for "<a <|>" are valid, while scope
            # completions for "<a<|>" are not.
            leading_whitespace = True
            tokens.pop()

        # Now look back through the token list and provide an appropriate
        # completion context.
        if tokens[-1]["style"] == SCE_UDL_M_STAGO or tokens[-1]["text"].endswith("<"):
            # A "<foo"-type of expression should produce element completions.
            return XULScopeCompletionContext(scope, name_part, AbstractElement)
        elif tokens[-1]["style"] == SCE_UDL_M_TAGNAME:
            # A "<foo bar"-type of expression should produce attribute
            # completions.
            symbol_name = tokens[-1]["text"]
            return XULMemberCompletionContext(scope, symbol_name, name_part)
        elif tokens[-1]["style"] == SCE_UDL_M_OPERATOR and tokens[-1]["text"] == "=" and (name_part.startswith("'") or name_part.startswith('"')):
            # If the first significant token behind the position is an '=' and
            # the token at the position is a value string, it's probably a
            # "<foo bar='baz"-type of expression, which should provide value
            # completions.
            tokens.pop()
            if tokens[-1]["style"] == SCE_UDL_M_ATTRNAME:
                # Skip back through other attribute-value pairs in order to find
                # the original element and construct the full "symbol" name.
                i = len(tokens) - 1
                while i >= 1:
                    if tokens[i]["style"] == SCE_UDL_M_TAGNAME:
                        symbol_name = "%s.%s" % (tokens[i]["text"], tokens[-1]["text"])
                        name_part = name_part.strip("'\"")
                        # A "<foo bar='baz"-type of expression should provide
                        # value completions.
                        return XULMemberCompletionContext(scope, symbol_name, name_part)
                    i -= 1
        elif tokens[-1]["style"] == SCE_UDL_M_STRING and (leading_whitespace or name_part):
            # If the first significant token behind the position is a value
            # string and there is whitespace between it and the position, it's
            # probably a "<foo bar='baz' quux"-type of expression, which should
            # provide attribute completions.
            # Skip back through other attribute-value pairs in order to find the
            # original element name.
            i = len(tokens) - 1
            while i >= 1:
                if tokens[i]["style"] == SCE_UDL_M_TAGNAME:
                    # A "<foo bar='baz' quux"-type of expression should
                    # provide attribute completions.
                    symbol_name = tokens[i]["text"]
                    return XULMemberCompletionContext(scope, symbol_name, name_part)
                i -= 1
        elif tokens[-1]["style"] == SCE_UDL_M_ETAGO:
            # If the first significant token behind the position is the start of
            # a closing tag, determine which tag should be closed in order to
            # construct a scope with that tag as its sole completion.
            # First, replace all non-XUL content with whitespace.
            content = ""
            for i in range(len(tokens)):
                content += tokens[i]["text"]
                if i < len(tokens) - 1 and tokens[i]["end_index"] + 1 < tokens[i + 1]["start_index"]:
                    content += re.sub("[^\r\n]", " ", filename[tokens[i]["end_index"] + 1:tokens[i + 1]["start_index"]]) # assume filename is content
            # Now parse the XUL and determine which tag to close.
            tree = koXMLTreeService.getService().getTreeForContent(content)
            if tree:
                node = tree.locateNode(line, column)
                last_start = content.rfind('<', 0, position)
                last_end = content.find('>', last_start, position)
                if node is None and last_start >= 0:
                    node = koXMLTreeService.elementFromText(tree, content[last_start:last_end], node)
                if not (node is None or getattr(node, "start", None) is None):
                    lines = content.split("\n")
                    node_pos = 0
                    for i in range(node.start[0] - 2): # count up to line before node line
                        node_pos += len(lines[i])
                    node_pos += node.start[1] # count on node line
                    if last_end == -1 and last_start != node_pos:
                        newnode = koXMLTreeService.elementFromText(tree, content[last_start:position], node)
                        if newnode is not None:
                            node = newnode
                if node is not None:
                    # A "<foo>...</"-type of expression should provide end tag
                    # completions.
                    scope = Scope()
                    scope.define(Element(tree.tagname(node) + ">"))
                    return XULScopeCompletionContext(scope, name_part, AbstractElement)

        return None

    def getUDLGotoDefinitionContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLScanner.getUDLGotoDefinitionContext()."""
        return None # TODO: ?

    def getUDLCallTipContext(self, filename, position, env, tokens, line, column, scope):
        """Implementation of AbstractUDLScanner.getUDLCallTipContext()."""
        return None # TODO: ?

if __name__ == "__main__":
    import argparse
    import sys
    import time
    from config import Config
    from db import Database
    from db.model import File as DBFile, Symbol as DBSymbol, SymbolClosure as DBSymbolClosure
    from language.legacy.xul.stdlib import XUL_STDLIB_FILE
    Database.initialize(":memory:", Config.get("closure_ext_path"))
    Database.conn.create_tables([DBFile, DBSymbol, DBSymbolClosure], True)
    parser = argparse.ArgumentParser(description="Scan XUL source files")
    parser.add_argument("file", nargs='?')
    args = parser.parse_args(sys.argv[1:])
    start = time.time()
    scanner = XULScanner(XUL_STDLIB_FILE)
    scope = scanner.scan(args.file)
    end = time.time()
    print((scope.prettyPrint()))
    if end - start < 1:
        print(("time: %dms" % ((end - start) * 1000)))
    else:
        print(("time: %fs" % (end - start)))
