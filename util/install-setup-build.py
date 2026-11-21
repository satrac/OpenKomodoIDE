#!/usr/bin/env python3
"""Install the output of ``setup.py build`` without ``setup.py install``.

Recent setuptools versions deprecate ``setup.py install`` and print a large
warning.  The vendored extensions in this tree only need their built module or
package copied into Komodo's Python library directory, so build with
``setup.py build`` and use this helper to copy the result.

Usage:
    python install-setup-build.py <module> <destination> [--build-dir DIR]

``<module>`` is the top-level module or package name (e.g. ``sgmlop`` or
``SilverCity``).  The newest ``build/lib.*`` directory below ``--build-dir`` is
searched for files whose name is the module or starts with ``<module>.``.
"""

import argparse
import glob
import os
import shutil
import sys


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("module")
    parser.add_argument("destination")
    parser.add_argument("--build-dir", default=".")
    args = parser.parse_args(argv)

    build_root = os.path.join(args.build_dir, "build")
    lib_dirs = sorted(glob.glob(os.path.join(build_root, "lib.*")))
    if not lib_dirs:
        print("error: no build/lib.* directory found in %s" % build_root,
              file=sys.stderr)
        return 1

    src = lib_dirs[-1]
    copied = []
    for name in os.listdir(src):
        if name != args.module and not name.startswith(args.module + "."):
            continue

        source = os.path.join(src, name)
        target = os.path.join(args.destination, name)
        if os.path.isdir(source):
            if not os.path.isdir(target):
                shutil.copytree(source, target)
            else:
                # Copy files into an existing package directory.
                for root, dirs, files in os.walk(source):
                    rel = os.path.relpath(root, source)
                    dest_dir = target if rel == "." else os.path.join(target, rel)
                    os.makedirs(dest_dir, exist_ok=True)
                    for filename in files:
                        shutil.copy2(os.path.join(root, filename),
                                     os.path.join(dest_dir, filename))
        else:
            shutil.copy2(source, target)

        copied.append(name)

    if not copied:
        print("error: no build output for %s in %s" % (args.module, src),
              file=sys.stderr)
        return 1

    print("installed %s from %s to %s"
          % (", ".join(sorted(copied)), src, args.destination))
    return 0


if __name__ == "__main__":
    sys.exit(main())
