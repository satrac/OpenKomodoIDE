# Forked dependencies

ActiveState's Komodo-specific repositories are disappearing from GitHub.  To
keep the build reproducible, the following dependencies have been mirrored
into the `satrac` account.

| Fork | Upstream | Purpose |
|------|----------|---------|
| `satrac/OpenKomodoIDE` | `ActiveState/OpenKomodoIDE` | Main IDE repository (this repo) |
| `satrac/komodo-codeintel2` | `mook/komodo-codeintel2` | CodeIntel server sources and `stdlibs/*.cix` |
| `satrac/pyxpcom` | `thereisonlyxul/pyxpcom` | In-process PyXPCOM extension |
| `satrac/komodo-python3-dbgp` | `agroszer/komodo-python3-dbgp` | Python 3 DBGP debugger client |
| `satrac/komodo-python-dbgp` | `agroszer/komodo-python-dbgp` | Legacy Python 2 DBGP debugger client |
| `satrac/komodo-patches-codeintel` | `mook/komodo-patches-codeintel` | CodeIntel patches |
| `satrac/KomodoEdit-CodeIntel` | `Kronuz/KomodoEdit-CodeIntel` | Older CodeIntel implementation |
| `satrac/trackchanges` | `Komodo/trackchanges` | IDE Track Changes extension |
| `satrac/komodo-go` | `Komodo/komodo-go` | Go language support |
| `satrac/komodo-editorconfig` | `Komodo/komodo-editorconfig` | EditorConfig support |
| `satrac/Focus-Mode` | `Komodo/Focus-Mode` | Focus Mode extension |

## Unavailable upstream

`Komodo/codeintel` (the original CodeIntel `server` submodule) now returns
404.  Its sources were recovered from the Komodo 12.0.1 installer and are
vendored in this repository under `src/modules/codeintel/server/`.  The
`mook/komodo-codeintel2` fork retains the original source layout and the
`stdlibs/` CIX dumps needed to regenerate `codeintel.db`.

## Build URLs

`mozilla/build.py` now fetches PyXPCOM from `satrac/pyxpcom`, and
`.gitmodules` points at the forked submodule URLs.  The forked repositories
are exact copies; upstream URLs remain available for comparison.
