# DBGP debugger client bits

Recovered from the Komodo IDE 12.0.1 Linux installer
(`INSTALLDIR/lib/support/dbgp/`).

The `python3lib/` directory is already Python 3 compatible.  The
`pythonlib/` directory is the legacy Python 2 client.  The language-specific
directories (Perl, Ruby, Tcl, PHP, Node.js, Chrome) are unchanged debugger
clients for those runtimes.

The prebuilt `_client*.so` files target old Python ABIs; the Python client
falls back to the pure-Python implementation when the C accelerator is not
available.
