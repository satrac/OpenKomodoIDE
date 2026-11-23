# Forked and preserved dependencies

**This fork targets Komodo IDE only.  Komodo Edit is out of scope and is not
supported.  The fork is experimental, is not supported by ActiveState, and
provides no user support.**

ActiveState retired Komodo IDE in December 2022 and the upstream
`ActiveState/OpenKomodoIDE` repository has had no commits since 2022-12-12.
Almost all of the Komodo-specific repositories under the `Komodo` GitHub
organization are also unmaintained (last upstream push between 2013 and 2023).

To keep this build reproducible, the relevant repositories have been forked
into the `satrac` account and cloned under:

```text
~/Projects/komodo-forks/
```

The main working tree remains `~/Projects/OpenKomodoIDE`.  The mirror
currently contains 42 clones with 42 distinct remotes; there are no duplicate
Komodo project clones.

## Active build/runtime dependencies

| Fork | Upstream | Purpose |
|------|----------|---------|
| `satrac/OpenKomodoIDE` | `ActiveState/OpenKomodoIDE` | Main IDE repository |
| `satrac/pyxpcom` | `thereisonlyxul/pyxpcom` | In-process PyXPCOM extension |
| `satrac/komodo-codeintel2` | `mook/komodo-codeintel2` | CodeIntel server sources and `stdlibs/*.cix` |
| `satrac/komodo-python3-dbgp` | `agroszer/komodo-python3-dbgp` | Python 3 DBGP debugger client |
| `satrac/komodo-python-dbgp` | `agroszer/komodo-python-dbgp` | Legacy Python 2 DBGP debugger client |
| `satrac/komodo-patches-codeintel` | `mook/komodo-patches-codeintel` | CodeIntel patches |
| `satrac/KomodoEdit-CodeIntel` | `Kronuz/KomodoEdit-CodeIntel` | Older CodeIntel implementation |
| `satrac/trackchanges` | `Komodo/trackchanges` | IDE Track Changes extension |
| `satrac/komodo-go` | `Komodo/komodo-go` | Go language support |
| `satrac/komodo-editorconfig` | `Komodo/komodo-editorconfig` | EditorConfig support |
| `satrac/Focus-Mode` | `Komodo/Focus-Mode` | Focus Mode extension |

`make modules` clones the four optional module forks into `src/modules/`.
`mozilla/build.py` fetches PyXPCOM from `satrac/pyxpcom`.  The remaining
Python 3 packages installed by `install-py3-python-deps.py` (APSW, Twisted,
PyCryptodome, zope.interface, peewee) are actively maintained upstream and are
installed from PyPI rather than forked.

The Python 3 DBGP target-side driver is vendored in the main tree under
`src/dbgp/PyDBGP` (`bin/py3_dbgp` plus the pure-Python `python3lib`).  The
PyDBGP Conscript installs it into `support/dbgp/` for development builds, so a
separate clone of `komodo-python3-dbgp` is not required at build time.

## Preserved Komodo repositories

These are not all required by the current build, but they contain Komodo
extensions, skins, documentation, packaging data, and historical source that
would otherwise disappear:

| Fork | Upstream | Notes |
|------|----------|-------|
| `satrac/Abyss-Skin` | `Komodo/Abyss-Skin` | Dark skin |
| `satrac/Addon-Bootstrapper` | `Komodo/Addon-Bootstrapper` | Addon bootstrap helper |
| `satrac/Classic-Skin` | `Komodo/Classic-Skin` | Classic native-looking skin |
| `satrac/dbexplorer_pgsql` | `Komodo/dbexplorer_pgsql` | PostgreSQL Database Explorer support |
| `satrac/developer-extension` | `Komodo/developer-extension` | JavaScript/Python developer tools |
| `satrac/dita` | `Komodo/dita` | DITA autocompletion |
| `satrac/docker-lang` | `Komodo/docker-lang` | Dockerfile syntax highlighting |
| `satrac/dom-inspector` | `Komodo/dom-inspector` | DOM Inspector addon |
| `satrac/klint` | `Komodo/klint` | Syntax-checking pane |
| `satrac/komodo-color-schemes` | `Komodo/komodo-color-schemes` | Color schemes |
| `satrac/komodo-documentation` | `Komodo/komodo-documentation` | Komodo build/website documentation |
| `satrac/Komodo-Sublime-Keybinds` | `Komodo/Komodo-Sublime-Keybinds` | Sublime keybindings |
| `satrac/Komodo-TypeScript-LSP` | `Komodo/Komodo-TypeScript-LSP` | TypeScript LSP support |
| `satrac/komodo-web-deploy` | `Komodo/komodo-web-deploy` | Web deployment support |
| `satrac/komodo-website` | `Komodo/komodo-website` | Official Komodo website |
| `satrac/kopy.io-addon` | `Komodo/kopy.io-addon` | kopy.io integration |
| `satrac/macros` | `Komodo/macros` | Macro collection |
| `satrac/markdown-viewer` | `Komodo/markdown-viewer` | Markdown preview |
| `satrac/Mark-Text-Macro` | `Komodo/Mark-Text-Macro` | Text marking macro |
| `satrac/Mel` | `Komodo/Mel` | MEL language support |
| `satrac/middleman-komodo_resources` | `Komodo/middleman-komodo_resources` | Website resource generator |
| `satrac/MySQL-Database-Explorer` | `Komodo/MySQL-Database-Explorer` | MySQL Database Explorer support |
| `satrac/Oracle-Database-Explorer` | `Komodo/Oracle-Database-Explorer` | Oracle Database Explorer support |
| `satrac/Packages` | `Komodo/Packages` | Community package collection |
| `satrac/Projects-Scope` | `Komodo/Projects-Scope` | Recent Projects Commando scope |
| `satrac/SideBySideDiff` | `Komodo/SideBySideDiff` | Side-by-side diff support |
| `satrac/Spacemodo-Skin` | `Komodo/Spacemodo-Skin` | Spacegray-inspired skin |
| `satrac/Todo` | `Komodo/Todo` | TODO marker addon |
| `satrac/github-commits-widget` | `Komodo/github-commits-widget` | Website commits widget |
| `satrac/kollum` | `Komodo/kollum` | Komodo documentation/wiki fork |
| `satrac/komodo-ru` | `Komodo/komodo-ru` | Russian translation |
| `satrac/mocha-ko-tap-reporter` | `Komodo/mocha-ko-tap-reporter` | Mocha TAP reporter |

## Vendored sources

Several dependencies are already carried directly in this repository and do
not need separate forks:

- `contrib/` (paramiko, ecdsa, watchdog/pathtools, zope descriptors,
  subprocess32, and other historical support code)
- `src/modules/codeintel/server/` (recovered CodeIntel sources)
- `mozilla/support/autoconf-2.13/` and the bundled Python 2.7 build-tool zips

The bundled Python 2.7 tree is **build-only**.  It exists because Mozilla 35's
IDL/build helpers are Python 2 scripts; the Komodo application, PyXPCOM,
CodeIntel, DBGP and all runtime components use Python 3.14.  It is not a
runtime dependency and is not selected as an application interpreter.

These remain preserved even if their original upstream repositories vanish.

Komodo Edit itself is deliberately not mirrored: this project only supports
the Komodo IDE application.  Extension repositories above are shared by both
products and are preserved for the IDE ecosystem.

## Unavailable upstream

- `Komodo/KomodoIDE` now returns 404.  `ActiveState/OpenKomodoIDE` (forked as
  `satrac/OpenKomodoIDE`) is the successor source tree.
- `Komodo/codeintel` now returns 404.  The server sources were recovered from
  the Komodo 12.0.1 installer and are vendored under
  `src/modules/codeintel/server/`.  The `satrac/komodo-codeintel2` fork keeps
  the original layout and the `stdlibs/` CIX dumps.

## Working with the forks

Each fork keeps the original upstream history.  To compare or pull from the
old upstream, add it as a remote in the relevant clone:

```bash
cd ~/Projects/komodo-forks/<name>
git remote add upstream https://github.com/Komodo/<name>.git
git fetch upstream
```

For forks whose upstream is not in the `Komodo` organization, use the
upstream URL from the table above.
