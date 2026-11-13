#!/usr/bin/env python3
"""Build shim for the Komodo CodeIntel server.

The original CodeIntel server repository used a custom ``setup.py`` with
``--pyexe`` support to build its C extensions and copy the Python package into
the Komodo extension tree.  The upstream repository is no longer available,
so the recovered server sources are shipped in their installed layout under
``src/``.  This shim implements the small part of the original build that the
Komodo Conscript still invokes: copy ``src/`` into the requested
``--build-purelib`` directory.

Usage (as invoked by src/modules/codeintel/Conscript):

    python setup.py build -b <build-base> --build-purelib <dest> --pyexe <python>
"""

import argparse
import os
import shutil
import sys


def _parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?")
    parser.add_argument("-b", "--build-base", default=None)
    parser.add_argument("--build-purelib", default=None)
    parser.add_argument("--pyexe", default=sys.executable)
    parser.add_argument("--debug", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parse_args(argv)

    if args.command not in (None, "build", "install"):
        # Be forgiving: the Conscript only needs the copy step.
        pass

    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "src")
    if not os.path.isdir(src):
        print("codeintel: source directory is missing: %s" % (src,),
              file=sys.stderr)
        return 1

    dest = args.build_purelib
    if not dest:
        base = args.build_base or os.path.join(here, "build")
        dest = os.path.join(base, "lib")
    dest = os.path.abspath(dest)

    os.makedirs(dest, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.pyc", "__pycache__"))
    print("codeintel: copied %s -> %s" % (src, dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
