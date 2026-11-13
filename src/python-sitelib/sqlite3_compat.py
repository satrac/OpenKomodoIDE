"""Import the system sqlite3 extension without Mozilla's SQLite symbols.

Mozilla 35 loads libmozsqlite3.so globally.  Its exported sqlite3_* symbols
have the same names as the system library, so Python's _sqlite3 extension can
bind against Mozilla's much older SQLite even though it is linked against
libsqlite3.so.0.  Python 3.14 then refuses to import sqlite3 because the
runtime version is below its minimum.

On Linux, load _sqlite3 with RTLD_DEEPBIND so its own libsqlite3 dependency
wins over the already-loaded Mozilla library.
"""

import os
import sys

_old_flags = sys.getdlopenflags()
_deepbind = getattr(os, "RTLD_DEEPBIND", 0)
try:
    if _deepbind:
        sys.setdlopenflags(_old_flags | _deepbind)
    import sqlite3 as _sqlite3
finally:
    sys.setdlopenflags(_old_flags)

sqlite3 = _sqlite3
