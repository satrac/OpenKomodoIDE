#!python
# encoding: utf-8
# Copyright (c) 2004-2006 ActiveState Software Inc.
# See the file LICENSE.txt for licensing information.

"""Code Intelligence: utility functions"""

import re
import textwrap
from pprint import pformat
import codecs

#---- standard module/class/function doc parsing

LINE_LIMIT = 5      # limit full number of lines this number
LINE_WIDTH = 60     # wrap doc summaries to this width

# Examples matches to this pattern:
#    foo(args)
#    foo(args) -> retval
#    foo(args) -- description
#    retval = foo(args)
#    retval = foo(args) -- description
_gPySigLinePat = re.compile(r"^((?P<retval>[^=]+?)\s*=|class)?\s*(?P<head>[\w\.]+\s?\(.*?\))\s*(?P<sep>[:<>=-]*)\s*(?P<tail>.*)$")
_gSentenceSepPat = re.compile(r"(?<=\.)\s+", re.M) # split on sentence bndry

def parseDocSummary(doclines, limit=LINE_LIMIT, width=LINE_WIDTH):
    """Parse out a short summary from the given doclines.

        "doclines" is a list of lines (without trailing newlines) to parse.
        "limit" is the number of lines to which to limit the summary.

    The "short summary" is the first sentence limited by (1) the "limit"
    number of lines and (2) one paragraph. If the first *two* sentences fit
    on the first line, then use both. Returns a list of summary lines.
    """
    # Skip blank lines.
    start = 0
    while start < len(doclines):
        if doclines[start].strip():
            break
        start += 1

    desclines = []
    for i in range(start, len(doclines)):
        if len(desclines) >= limit:
            break
        stripped = doclines[i].strip()
        if not stripped:
            break
        sentences = _gSentenceSepPat.split(stripped)
        if sentences and not sentences[-1].endswith('.'):
            del sentences[-1] # last bit might not be a complete sentence
        if not sentences:
            desclines.append(stripped + ' ')
            continue
        elif i == start and len(sentences) > 1:
            desclines.append(' '.join([s.strip() for s in sentences[:2]]))
        else:
            desclines.append(sentences[0].strip())
        break
    if desclines:
        if desclines[-1][-1] == ' ':
            # If terminated at non-sentence boundary then have extraneous
            # trailing space.
            desclines[-1] = desclines[-1][:-1]
        # Don't break long words, that doesn't do the right thing with
        # multi-byte character sets
        desclines = textwrap.wrap(''.join(desclines), width,
                                  break_long_words=False)
    return desclines


def parsePyFuncDoc(doc, fallbackCallSig=None, scope="?", funcname="?"):
    """Parse the given Python function/method doc-string into call-signature
    and description bits.

        "doc" is the function doc string.
        "fallbackCallSig" (optional) is a list of call signature lines to
            fallback to if one cannot be determined from the doc string.
        "scope" (optional) is the module/class parent scope name. This
            is just used for better error/log reporting.
        "funcname" (optional) is the function name. This is just used for
            better error/log reporting.

    Examples of doc strings with call-signature info:
        close(): explicitly release resources held.
        x.__repr__() <==> repr(x)
        read([s]) -- Read s characters, or the rest of the string
        recv(buffersize[, flags]) -> data
        replace (str, old, new[, maxsplit]) -> string
        class StringIO([buffer])

    Returns a 2-tuple: (<call-signature-lines>, <description-lines>)
    """
    if doc is None or not doc.strip():
        return ([], [])

    limit = LINE_LIMIT
    if not isinstance(doc, str):
        # try to convert from utf8 to unicode; if we fail, too bad.
        try:
            doc = codecs.utf_8_decode(doc)[0]
        except UnicodeDecodeError:
            pass
    doclines = doc.splitlines(0)
    index = 0
    siglines = []
    desclines = []

    # Skip leading blank lines.
    while index < len(doclines):
        if doclines[index].strip():
            break
        index += 1

    # Parse out the call signature block, if it looks like there is one.
    if index >= len(doclines):
        match = None
    else:
        first = doclines[index].strip()
        match = _gPySigLinePat.match(first)
    if match:
        # The 'doc' looks like it starts with a call signature block.
        for i, line in enumerate(doclines[index:]):
            if len(siglines) >= limit:
                index = i
                break
            stripped = line.strip()
            if not stripped:
                index = i
                break
            match = _gPySigLinePat.match(stripped)
            if not match:
                index = i
                break
            # Now parse off what may be description content on the same line.
            #   ":", "-" or "--" separator: tail is description
            #   "-->" or "->" separator: tail if part of call sig
            #   "<==>" separator: tail if part of call sig
            #   other separtor: leave as part of call sig for now
            descSeps = ("-", "--", ":")
            groupd = match.groupdict()
            retval, head, sep, tail = (groupd.get("retval"), groupd.get("head"),
                                       groupd.get("sep"), groupd.get("tail"))
            if retval:
                siglines.append(head + " -> " + retval)
                if tail and sep in descSeps:
                    desclines.append(tail)
            elif tail and sep in descSeps:
                siglines.append(head)
                desclines.append(tail)
            else:
                siglines.append(stripped)
        else:
            index = len(doclines)
    if not siglines and fallbackCallSig:
        siglines = fallbackCallSig

    # Parse out the description block.
    if desclines:
        # Use what we have already. Just need to wrap it.  But don't break long
        # words, that doesn't do the right thing with multi-byte character sets.
        desclines = textwrap.wrap(' '.join(desclines), LINE_WIDTH,
                                  break_long_words=False)
    else:
        doclines = doclines[index:]
        # strip leading empty lines
        while len(doclines) > 0 and not doclines[0].rstrip():
            del doclines[0]
        try:
            skip_first_line = (doclines[0][0] not in (" \t"))
        except IndexError:
            skip_first_line = False # no lines, or first line is empty
        desclines = dedent("\n".join(doclines), skip_first_line=skip_first_line)
        desclines = desclines.splitlines(0)

    ## debug logging
    #f = open("parsePyFuncDoc.log", "a")
    #if 0:
    #    f.write("\n---- %s:\n" % funcname)
    #    f.write(pformat(siglines)+"\n")
    #    f.write(pformat(desclines)+"\n")
    #else:
    #    f.write("\n")
    #    if siglines:
    #        f.write("\n".join(siglines)+"\n")
    #    else:
    #        f.write("<no signature for '%s.%s'>\n" % (scope, funcname))
    #    for descline in desclines:
    #        f.write("\t%s\n" % descline)
    #f.close()

    return (siglines, desclines)

# Recipe: dedent (0.1.2) in C:\trentm\tm\recipes\cookbook
def _dedentlines(lines, tabsize=8, skip_first_line=False):
    """_dedentlines(lines, tabsize=8, skip_first_line=False) -> dedented lines

        "lines" is a list of lines to dedent.
        "tabsize" is the tab width to use for indent width calculations.
        "skip_first_line" is a boolean indicating if the first line should
            be skipped for calculating the indent width and for dedenting.
            This is sometimes useful for docstrings and similar.

    Same as dedent() except operates on a sequence of lines. Note: the
    lines list is modified **in-place**.
    """
    DEBUG = False
    if DEBUG:
        print("dedent: dedent(..., tabsize=%d, skip_first_line=%r)"\
              % (tabsize, skip_first_line))
    indents = []
    margin = None
    for i, line in enumerate(lines):
        if i == 0 and skip_first_line: continue
        indent = 0
        for ch in line:
            if ch == ' ':
                indent += 1
            elif ch == '\t':
                indent += tabsize - (indent % tabsize)
            elif ch in '\r\n':
                continue # skip all-whitespace lines
            else:
                break
        else:
            continue # skip all-whitespace lines
        if DEBUG: print("dedent: indent=%d: %r" % (indent, line))
        if margin is None:
            margin = indent
        else:
            margin = min(margin, indent)
    if DEBUG: print("dedent: margin=%r" % margin)

    if margin is not None and margin > 0:
        for i, line in enumerate(lines):
            if i == 0 and skip_first_line: continue
            removed = 0
            for j, ch in enumerate(line):
                if ch == ' ':
                    removed += 1
                elif ch == '\t':
                    removed += tabsize - (removed % tabsize)
                elif ch in '\r\n':
                    if DEBUG: print("dedent: %r: EOL -> strip up to EOL" % line)
                    lines[i] = lines[i][j:]
                    break
                else:
                    raise ValueError("unexpected non-whitespace char %r in "
                                     "line %r while removing %d-space margin"
                                     % (ch, line, margin))
                if DEBUG:
                    print("dedent: %r: %r -> removed %d/%d"\
                          % (line, ch, removed, margin))
                if removed == margin:
                    lines[i] = lines[i][j+1:]
                    break
                elif removed > margin:
                    lines[i] = ' '*(removed-margin) + lines[i][j+1:]
                    break
            else:
                if removed:
                    lines[i] = lines[i][removed:]
    return lines

def dedent(text, tabsize=8, skip_first_line=False):
    """dedent(text, tabsize=8, skip_first_line=False) -> dedented text

        "text" is the text to dedent.
        "tabsize" is the tab width to use for indent width calculations.
        "skip_first_line" is a boolean indicating if the first line should
            be skipped for calculating the indent width and for dedenting.
            This is sometimes useful for docstrings and similar.

    textwrap.dedent(s), but don't expand tabs to spaces
    """
    lines = text.splitlines(1)
    _dedentlines(lines, tabsize=tabsize, skip_first_line=skip_first_line)
    return ''.join(lines)
