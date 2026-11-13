# Komodo DBGP server

These files were recovered from the Komodo IDE 12.0.1 Linux installer
(`INSTALLDIR/lib/mozilla/python/komodo/dbgp/`) because the original
`src/dbgp` tree is not present in the OpenKomodoIDE repository.

They implement the IDE-side DBGP server used by `koDBGPManager`
(`src/components/koDBGP.py`) and the `dbgp://` protocol handler.

Status: Python 2 sources. They need to be ported to Python 3 before they can
run under the Python 3 PyXPCOM runtime.
