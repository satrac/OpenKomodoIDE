# Copyright 2017 ActiveState, Inc. All rights reserved.

"""XUL stdlib importer."""

import os

from language.legacy.common.koDTD import DTD

# The DB file paths to store stdlib symbols under.
XUL_STDLIB_FILE = ":xul:"
XBL_STDLIB_FILE = ":xbl:"

# Map of stdlib files to DTDs.
dtd_map = {
    XUL_STDLIB_FILE: os.path.join("catalogs", "xul.dtd"),
    XBL_STDLIB_FILE: os.path.join("catalogs", "xbl.dtd"),
}

def scan(stdlib_file):
    """Imports the XUL stdlibs into the database."""
    stdlib_scope = None
    try:
        dtd_file = os.path.join(os.path.dirname(__file__), dtd_map[stdlib_file])
        stdlib_scope = DTD(dtd_file).dataset.toAbstractScope()
    except AttributeError:
        raise RuntimeError("Unknown XUL stdlib file: '%s'" % stdlib_file)

    # Store scope in database.
    # TODO: subject to change
    from db import Database
    Database.writeFileScope(stdlib_file, stdlib_scope)

if __name__ == "__main__":
    scan(XUL_STDLIB_FILE)
