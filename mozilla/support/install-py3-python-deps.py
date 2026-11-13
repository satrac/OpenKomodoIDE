#!/usr/bin/env python3
"""Install Python 3 runtime dependencies into the Komodo dist.

The old Komodo distribution bundled Python 2-era copies of APSW, Twisted,
PyCrypto, zope.interface, and sgmlop.  The in-process Python 3 port uses
modern, Python 3-compatible releases instead.  This script makes that step
reproducible after a clean build.

Usage:
    python3 install-py3-python-deps.py --app-dir <dist>
    python3 install-py3-python-deps.py --app-dir <dist> --offline
"""

import argparse
import os
import shutil
import subprocess
import sys


REQUIREMENTS = [
    "apsw",
    "twisted",
    "pycryptodome",
    "zope.interface",
    "peewee<4",
]


def _run(cmd, cwd=None):
    printable = " ".join(cmd)
    print("+ %s" % (printable,), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True,
                        help="Komodo dist directory (contains bin/python)")
    parser.add_argument("--python", default=sys.executable,
                        help="Python 3 interpreter used to run pip")
    parser.add_argument("--offline", action="store_true",
                        help="skip pip and only build/install local packages")
    args = parser.parse_args(argv)

    app_dir = os.path.abspath(args.app_dir)
    target = os.path.join(app_dir, "bin", "python", "komodo")
    if not os.path.isdir(target):
        print("error: Python package directory does not exist: %s" % (target,),
              file=sys.stderr)
        return 1

    if not args.offline:
        _run([args.python, "-m", "pip", "install", "--target", target,
              "--upgrade"] + REQUIREMENTS +
             ["--disable-pip-version-check", "--no-warn-script-location"])

    # The silo Python is the interpreter the app actually embeds.
    silo_python = os.path.join(app_dir, "python", "bin", "python3")
    if not os.path.exists(silo_python):
        silo_python = args.python

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    sgmlop_src = os.path.join(repo_root, "contrib", "sgmlop")
    if os.path.isdir(sgmlop_src):
        _run([silo_python, "setup.py", "install",
              "--install-lib", target,
              "--install-scripts", os.path.join(app_dir, "support", "dbgp", "bin"),
              "--install-data", os.path.join(app_dir, "support", "dbgp"),
              "--prefix", "bitbucket"], cwd=sgmlop_src)

    # zope.cachedescriptors is still vendored in contrib/.
    cached = os.path.join(repo_root, "contrib", "zope", "cachedescriptors",
                          "src", "zope", "cachedescriptors")
    cached_dst = os.path.join(target, "zope", "cachedescriptors")
    if os.path.isdir(cached):
        if os.path.isdir(cached_dst):
            shutil.rmtree(cached_dst)
        shutil.copytree(cached, cached_dst)

    print("Installed Python 3 runtime dependencies into %s" % (target,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
