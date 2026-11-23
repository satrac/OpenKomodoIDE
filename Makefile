# Komodo IDE build helper
#
# This is a convenience wrapper around the existing Black/Cons build system
# (util/black/bk) and the Mozilla/PyXPCOM build script (mozilla/build.py).
#
# It is intentionally thin: the real build logic stays in those tools.
# Run `make` or `make help` for the list of targets.
#
# Common usage:
#   make configure
#   make build
#   make run
#   make install-local
#   sudo make install
#
# Parts:
#   make silo-python      # refresh the Python 3 silo
#   make pyxpcom          # rebuild PyXPCOM
#   make python3-deps     # install APSW/Twisted/PyCryptodome/etc.
#   make silvercity       # rebuild SilverCity
#   make sgmlop           # rebuild the sgmlop extension
#   make codeintel-db     # generate codeintel.db (optional)
#
# The Makefile deliberately uses `.RECIPEPREFIX` so recipes are readable and
# do not require literal tab characters.

SHELL := /bin/bash
.DEFAULT_GOAL := help
.RECIPEPREFIX := >
# Cleaning targets remove overlapping staging trees; keep them serial even if
# make is invoked with -j.
.NOTPARALLEL:

ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
PYTHON ?= python3
BK := $(ROOT)/util/black/bk
MOZ := $(ROOT)/mozilla
MOZ_SRC ?= $(MOZ)/build/35.0-ko12.10
PYTHON27 ?= $(ROOT)/mozilla/prebuilt/python2.7/linux-x86_64/bin/python
# The legacy Mozilla IDL tools need Python 2.7.  The tree is shipped as a
# zip in the repository and is ignored after extraction.
PYTHON27_DIR ?= $(patsubst %/bin,%,$(patsubst %/,%,$(dir $(PYTHON27))))
PYTHON27_ZIP ?= $(PYTHON27_DIR).zip
KOMODO_VERSION ?= 12.10.0
KOMODO_BUILDNUM ?= 1
# Mozilla's configure uses the two-part Komodo version (12.10).
MOZ_KOMODO_VERSION ?= 12.10
MOZ_SRC_TARBALL ?= firefox-35.0.source.tar.bz2
CONFIGURE_ARGS ?= --ide --dev

# Some GTK2 themes reference engines that are not shipped by every distro
# (for example Breeze-Dark uses the "adwaita" engine, which Arch's gtk2
# package does not provide).  That produces:
#   Gtk-WARNING: Unable to locate theme engine in module_path: "adwaita"
# Default to Raleigh, which only uses the built-in pixmap engine, when it is
# available.  Override with e.g. GTK2_RC_FILES=/path/to/gtkrc (or set it to an
# empty value to use the desktop theme).
GTK2_RC_FILES ?= $(wildcard /usr/share/themes/Raleigh/gtk-2.0/gtkrc)

# Paths are read from the generated bkconfig.py when it exists.  Override any
# of these on the command line if needed:
#   make build MOZ_DIST=/path/to/dist
MOZ_DIST ?= $(shell cd $(ROOT) && $(PYTHON) -c 'import bkconfig; print(bkconfig.mozDist)' 2>/dev/null)
MOZ_DIST := $(if $(MOZ_DIST),$(MOZ_DIST),$(MOZ_SRC)/mozilla/ko-rel/dist)
APP_BIN := $(MOZ_DIST)/bin
APP_SILO := $(MOZ_DIST)/python
APP_PYTHON := $(APP_SILO)/bin/python3
APP_PKG := $(APP_BIN)/python/komodo
BUILD_STAGE := $(ROOT)/build/release
IMAGE_DIR ?= $(shell cd $(ROOT) && $(PYTHON) -c 'import bkconfig; print(bkconfig.installAbsDir)' 2>/dev/null)
IMAGE_INSTALLDIR := $(if $(IMAGE_DIR),$(IMAGE_DIR)/INSTALLDIR,)

# Set CCACHE=1 to route C/C++ compilation through ccache.  This mainly helps
# repeated clean rebuilds of Scintilla/SciMoz/SilverCity/sgmlop; incremental
# Cons builds do not recompile unchanged files.
CCACHE ?= 0
ifeq ($(CCACHE),1)
export CC := ccache $(CC)
export CXX := ccache $(CXX)
export CCACHE_DIR ?= $(HOME)/.cache/ccache
endif

PREFIX ?= /usr/local
LOCAL_PREFIX ?= $(HOME)/.local
DESTDIR ?=
INSTALL_NAME ?= komodo-12.10
PACKAGE ?= std
CIX_DIR ?=
# Application icon style: "classic" (Komodo 9) or "modern" (current Komodo).
ICON_STYLE ?= classic
export KOMODO_ICON_STYLE := $(ICON_STYLE)
ifneq ($(filter $(ICON_STYLE),classic modern),$(ICON_STYLE))
$(error ICON_STYLE must be 'classic' or 'modern')
endif
ifneq ($(filter --edit,$(CONFIGURE_ARGS)),)
$(error This fork supports Komodo IDE only; use CONFIGURE_ARGS=--ide)
endif

.PHONY: help all doctor prebuilt-python modules configure register-mozbuild \
        build app run test \
        silo-python pyxpcom python3-deps deps mozilla mozilla-all full \
        image package package-installer package-remotedebugging package-updates \
        install install-local uninstall uninstall-local \
        silvercity sgmlop codeintel-db \
        check xpcom-check dbgp-check codeintel-check \
        clean clean-all clean-app clean-mozilla clean-pyxpcom \
        clean-silo-python clean-python3-deps \
        clean-scintilla clean-scimoz clean-silvercity clean-sgmlop \
        clean-codeintel clean-codeintel-db clean-dbgp \
        clean-image clean-packages clean-pycache clean-config \
        clean-parts distclean status

help:
> @echo "Komodo IDE build helper"
> @echo ""
> @echo "Core:"
> @echo "  make configure        Configure the Black/Komodo build (IDE only)"
> @echo "  make build            Incremental build of the Komodo app bits"
> @echo "  make run              Run the built IDE"
> @echo "  make test             Run the Komodo self-test target"
> @echo "  make status           Show the resolved build paths"
> @echo ""
> @echo "Full/bootstrap:"
> @echo "  make doctor           Check build prerequisites and report gaps"
> @echo "  make modules          Clone the optional Komodo module sources"
> @echo "  make prebuilt-python  Extract the bundled Python 2.7 build tool"
> @echo "  make mozilla-all      Build Mozilla + PyXPCOM + Python silo + deps"
> @echo "  make full             Bootstrap, configure, then build the IDE"
> @echo ""
> @echo "Parts:"
> @echo "  make silo-python      Refresh the Python 3 silo at dist/python"
> @echo "  make pyxpcom          Rebuild PyXPCOM only"
> @echo "  make python3-deps     Install APSW/Twisted/PyCryptodome/zope/peewee"
> @echo "  make silvercity       Rebuild/install SilverCity"
> @echo "  make sgmlop           Rebuild/install sgmlop"
> @echo "  make codeintel-db     Generate the optional codeintel.db seed"
> @echo "  make dbgp-check       Verify the DBGP Python server imports"
> @echo "  make xpcom-check      Verify the PyXPCOM component loader"
> @echo "  make check            Run xpcom/dbgp/codeintel sanity checks"
> @echo ""
> @echo "Packaging/install:"
> @echo "  make image            Create the install image under install/"
> @echo "  make package          Build the standard packages"
> @echo "  make package-installer"
> @echo "  make package-remotedebugging"
> @echo "  make install-local    Install under ~/.local (PREFIX=$(LOCAL_PREFIX))"
> @echo "  sudo make install     Install under $(PREFIX)"
> @echo "  make uninstall        Remove the system install"
> @echo "  make uninstall-local  Remove the local install"
> @echo ""
> @echo "Cleaning (one target per build portion):"
> @echo "  make clean            Clean the Komodo app build products (bk clean)"
> @echo "  make clean-app        Same as 'make clean'"
> @echo "  make clean-parts      Clean all components rebuilt by 'make build'"
> @echo "  make clean-scintilla  Clean the staged Scintilla build"
> @echo "  make clean-scimoz     Clean SciMoz components/plugin"
> @echo "  make clean-silvercity  Clean SilverCity build and installed module"
> @echo "  make clean-sgmlop     Clean sgmlop build and installed module"
> @echo "  make clean-codeintel  Clean the CodeIntel extension build"
> @echo "  make clean-codeintel-db  Remove the generated codeintel.db"
> @echo "  make clean-dbgp       Clean the DBGP server build"
> @echo "  make clean-python3-deps  Remove pip deps (rerun 'make python3-deps')"
> @echo "  make clean-silo-python   Remove the Python 3 silo (dist/python)"
> @echo "  make clean-pyxpcom    Clean the PyXPCOM build"
> @echo "  make clean-mozilla    Clean the Mozilla object dir"
> @echo "  make clean-image      Remove the install image"
> @echo "  make clean-packages   Remove built packages"
> @echo "  make clean-pycache    Remove __pycache__/*.pyc from src/contrib/util"
> @echo "  make clean-config     Remove generated bkconfig.* files"
> @echo "  make clean-all        Clean app, parts, image, packages and pycache"
> @echo "  make distclean        Remove build trees and generated config"
> @echo ""
> @echo "Useful variables:"
> @echo "  MOZ_DIST=$(MOZ_DIST)"
> @echo "  APP_PYTHON=$(APP_PYTHON)"
> @echo "  IMAGE_DIR=$(IMAGE_DIR)"
> @echo "  PREFIX=$(PREFIX)  LOCAL_PREFIX=$(LOCAL_PREFIX)"
> @echo "  PACKAGE=$(PACKAGE)  CIX_DIR=$(CIX_DIR)"
> @echo "  ICON_STYLE=$(ICON_STYLE)  (classic or modern)"
> @echo "  CCACHE=$(CCACHE)      (set CCACHE=1 for clean-rebuild caching)"

all: build

# Check the host-side prerequisites for a from-scratch build.  This is
# intentionally read-only; install missing system packages yourself.
doctor:
> @fail=0; \
> for cmd in gcc g++ make perl git patch hg unzip zip pkg-config; do \
>   if command -v "$$cmd" >/dev/null 2>&1; then \
>     echo "ok    $$cmd"; \
>   else \
>     echo "MISS  $$cmd"; fail=1; \
>   fi; \
> done; \
> if $(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 14) else 1)' 2>/dev/null; then \
>   echo "ok    $(PYTHON) >= 3.14"; \
> else \
>   echo "MISS  $(PYTHON) >= 3.14"; fail=1; \
> fi; \
> if $(PYTHON) -c 'import os, sys, sysconfig; sys.exit(0 if os.path.exists(os.path.join(sysconfig.get_paths()["include"], "Python.h")) else 1)' 2>/dev/null; then \
>   echo "ok    Python.h"; \
> else \
>   echo "MISS  Python.h (install the Python development headers)"; fail=1; \
> fi; \
> if pkg-config --exists gtk+-2.0 2>/dev/null; then \
>   echo "ok    gtk+-2.0"; \
> else \
>   echo "MISS  gtk+-2.0 (install the GTK2 development package)"; fail=1; \
> fi; \
> if [ -x "$(PYTHON27)" ]; then \
>   echo "ok    prebuilt Python 2.7"; \
> else \
>   echo "MISS  prebuilt Python 2.7 (run 'make prebuilt-python')"; fail=1; \
> fi; \
> if [ -x "$(MOZ)/support/autoconf-2.13/bin/autoconf" ]; then \
>   echo "ok    bundled autoconf 2.13"; \
> else \
>   echo "MISS  bundled autoconf 2.13"; fail=1; \
> fi; \
> if [ -f "$(MOZ_DIST)/bin/xpcshell" ]; then \
>   echo "ok    Mozilla build"; \
> else \
>   echo "note  Mozilla build not present (run 'make mozilla-all')"; \
> fi; \
> for d in trackchanges golang editorconfig focusmode; do \
>   if [ -d "$(ROOT)/src/modules/$$d" ]; then \
>     echo "ok    module $$d"; \
>   else \
>     echo "note  module $$d missing (run 'make modules')"; \
>   fi; \
> done; \
> exit $$fail

# Extract the bundled Python 2.7 used by the legacy Mozilla IDL/build tools.
prebuilt-python:
> @if [ -x "$(PYTHON27)" ]; then \
>   echo "prebuilt Python 2.7 already present: $(PYTHON27)"; \
> elif [ ! -f "$(PYTHON27_ZIP)" ]; then \
>   echo "error: prebuilt Python zip not found: $(PYTHON27_ZIP)"; exit 1; \
> else \
>   echo "extracting $(PYTHON27_ZIP) -> $(PYTHON27_DIR)"; \
>   mkdir -p "$(PYTHON27_DIR)"; \
>   if command -v unzip >/dev/null 2>&1; then \
>     unzip -q -o "$(PYTHON27_ZIP)" -d "$(PYTHON27_DIR)"; \
>   else \
>     $(PYTHON) -m zipfile -e "$(PYTHON27_ZIP)" "$(PYTHON27_DIR)"; \
>   fi; \
>   test -x "$(PYTHON27)" || { \
>     echo "error: extraction did not create $(PYTHON27)"; exit 1; \
>   }; \
> fi

# Optional Komodo modules that are not part of the main source tree.  The
# repository's .gitmodules file documents the upstream/fork remotes; these
# directories are absent from a fresh checkout.
modules:
> @set -e; \
> for spec in \
>   "src/modules/trackchanges https://github.com/satrac/trackchanges.git" \
>   "src/modules/golang https://github.com/satrac/komodo-go.git" \
>   "src/modules/editorconfig https://github.com/satrac/komodo-editorconfig.git" \
>   "src/modules/focusmode https://github.com/satrac/Focus-Mode.git"; do \
>   set -- $$spec; dir="$(ROOT)/$$1"; url="$$2"; \
>   if [ -d "$$dir" ]; then \
>     echo "present: $$1"; \
>   else \
>     echo "cloning $$url"; \
>     git clone --depth 1 "$$url" "$$dir"; \
>   fi; \
> done; \
> for name in golang editorconfig trackchanges; do \
>   patch="$(ROOT)/util/module-patches/$$name.patch"; \
>   dir="$(ROOT)/src/modules/$$name"; \
>   if [ -f "$$patch" ] && [ -d "$$dir" ]; then \
>     if git -C "$$dir" apply --reverse --check "$$patch" >/dev/null 2>&1; then \
>       echo "module patch already applied: $$name"; \
>     else \
>       echo "applying module patch: $$name"; \
>       git -C "$$dir" apply "$$patch"; \
>     fi; \
>   fi; \
> done

configure: prebuilt-python
> @test -x "$(BK)" || { echo "error: missing $(BK)"; exit 1; }
> @if [ -x "$(MOZ_DIST)/bin/xpcshell" ]; then \
>   cd "$(MOZ)" && $(PYTHON) build.py regmozbuild; \
> fi
> $(BK) configure --moz-src="$(MOZ_SRC)" --python27="$(PYTHON27)" \
>   -V "$(KOMODO_VERSION)" --komodo-buildnum="$(KOMODO_BUILDNUM)" \
>   $(CONFIGURE_ARGS)

register-mozbuild:
> cd "$(MOZ)" && $(PYTHON) build.py regmozbuild

build:
> @test -x "$(BK)" || { echo "error: missing $(BK)"; exit 1; }
> $(BK) build $(BUILD_ARGS)

app: build

run:
> @test -x "$(BK)" || { echo "error: missing $(BK)"; exit 1; }
> $(if $(GTK2_RC_FILES),GTK2_RC_FILES="$(GTK2_RC_FILES)" )$(BK) run $(RUN_ARGS)

test:
> @test -x "$(BK)" || { echo "error: missing $(BK)"; exit 1; }
> $(BK) test $(TEST_ARGS)

silo-python:
> cd "$(MOZ)" && $(PYTHON) build.py silo_python --force

pyxpcom:
> cd "$(MOZ)" && $(PYTHON) build.py pyxpcom

python3-deps:
> cd "$(MOZ)" && $(PYTHON) build.py python3_deps

deps: silo-python python3-deps pyxpcom

# Generate mozilla/config.py for a fresh checkout.  The Mozilla source and
# build targets need this file before they can run; a normal tree only has it
# because it was configured earlier.
moz-configure:
> @if [ ! -f "$(MOZ)/config.py" ]; then \
>   tarball="$(MOZ_SRC_TARBALL)"; \
>   case "$$tarball" in /*) ;; *) tarball="$(MOZ)/$$tarball";; esac; \
>   test -f "$$tarball" || { \
>     echo "error: missing $$tarball"; \
>     echo "       download the Firefox 35 source tarball or pass MOZ_SRC_TARBALL=/path/to/tarball"; \
>     exit 1; }; \
>   echo "generating Mozilla build configuration"; \
>   cd "$(MOZ)" && $(PYTHON) build.py configure --release \
>     --moz-src="$(MOZ_SRC_TARBALL)" --moz-objdir=ko-rel \
>     -k "$(MOZ_KOMODO_VERSION)" --gcc=gcc --gxx=g++; \
> fi

mozilla: moz-configure
> cd "$(MOZ)" && $(PYTHON) build.py mozilla
> cd "$(MOZ)" && $(PYTHON) build.py pyxpcom
> cd "$(MOZ)" && $(PYTHON) build.py silo_python --force
> cd "$(MOZ)" && $(PYTHON) build.py python3_deps

mozilla-all: moz-configure
> cd "$(MOZ)" && $(PYTHON) build.py all

full: modules prebuilt-python moz-configure
> $(MAKE) mozilla-all
> $(MAKE) configure
> $(MAKE) build

image:
> @test -x "$(BK)" || { echo "error: missing $(BK)"; exit 1; }
> $(BK) image $(IMAGE_ARGS)

package:
> $(BK) package $(PACKAGE)

package-installer:
> $(BK) package installer

package-remotedebugging:
> $(BK) package remotedebugging

package-updates:
> $(BK) package updates

install-local: image
> @test -n "$(IMAGE_INSTALLDIR)" || { echo "error: IMAGE_DIR is not set"; exit 1; }
> @test -d "$(IMAGE_INSTALLDIR)" || { echo "error: install image missing: $(IMAGE_INSTALLDIR)"; exit 1; }
> install -d "$(LOCAL_PREFIX)/lib/$(INSTALL_NAME)" "$(LOCAL_PREFIX)/bin"
> @chmod -R u+w "$(LOCAL_PREFIX)/lib/$(INSTALL_NAME)" 2>/dev/null || true
> cp -a "$(IMAGE_INSTALLDIR)/." "$(LOCAL_PREFIX)/lib/$(INSTALL_NAME)/"
> printf '%s\n' '#!/bin/sh' \
>   'if [ -z "$$GTK2_RC_FILES" ] && [ -f "$(GTK2_RC_FILES)" ]; then GTK2_RC_FILES="$(GTK2_RC_FILES)"; export GTK2_RC_FILES; fi' \
>   'exec "$(LOCAL_PREFIX)/lib/$(INSTALL_NAME)/bin/komodo" "$$@"' \
>   > "$(LOCAL_PREFIX)/bin/komodo"
> chmod +x "$(LOCAL_PREFIX)/bin/komodo"
> @echo "installed to $(LOCAL_PREFIX)/lib/$(INSTALL_NAME)"
> @echo "launcher: $(LOCAL_PREFIX)/bin/komodo"

install: image
> @test -n "$(DESTDIR)$(PREFIX)" || { echo "error: PREFIX is not set"; exit 1; }
> @test -d "$(IMAGE_INSTALLDIR)" || { echo "error: install image missing: $(IMAGE_INSTALLDIR)"; exit 1; }
> install -d "$(DESTDIR)$(PREFIX)/lib/$(INSTALL_NAME)" "$(DESTDIR)$(PREFIX)/bin"
> cp -a "$(IMAGE_INSTALLDIR)/." "$(DESTDIR)$(PREFIX)/lib/$(INSTALL_NAME)/"
> printf '%s\n' '#!/bin/sh' \
>   'if [ -z "$$GTK2_RC_FILES" ] && [ -f "$(GTK2_RC_FILES)" ]; then GTK2_RC_FILES="$(GTK2_RC_FILES)"; export GTK2_RC_FILES; fi' \
>   'exec "$(DESTDIR)$(PREFIX)/lib/$(INSTALL_NAME)/bin/komodo" "$$@"' \
>   > "$(DESTDIR)$(PREFIX)/bin/komodo"
> chmod +x "$(DESTDIR)$(PREFIX)/bin/komodo"
> @echo "installed to $(DESTDIR)$(PREFIX)/lib/$(INSTALL_NAME)"
> @echo "launcher: $(DESTDIR)$(PREFIX)/bin/komodo"

uninstall:
> rm -rf "$(DESTDIR)$(PREFIX)/lib/$(INSTALL_NAME)"
> rm -f "$(DESTDIR)$(PREFIX)/bin/komodo"

uninstall-local:
> rm -rf "$(LOCAL_PREFIX)/lib/$(INSTALL_NAME)"
> rm -f "$(LOCAL_PREFIX)/bin/komodo"

silvercity:
> @test -x "$(APP_PYTHON)" || { echo "error: missing $(APP_PYTHON); run 'make silo-python'"; exit 1; }
> cd "$(ROOT)/src/silvercity" && $(APP_PYTHON) setup.py build
> cd "$(ROOT)/src/silvercity" && $(APP_PYTHON) "$(ROOT)/util/install-setup-build.py" SilverCity "$(APP_PKG)"

sgmlop:
> @test -x "$(APP_PYTHON)" || { echo "error: missing $(APP_PYTHON); run 'make silo-python'"; exit 1; }
> cd "$(ROOT)/contrib/sgmlop" && $(APP_PYTHON) setup.py build
> cd "$(ROOT)/contrib/sgmlop" && $(APP_PYTHON) "$(ROOT)/util/install-setup-build.py" sgmlop "$(APP_PKG)"

codeintel-db:
> @test -x "$(APP_PYTHON)" || { echo "error: missing $(APP_PYTHON); run 'make silo-python'"; exit 1; }
> cd "$(ROOT)/src/modules/codeintel/server" && \
>   PYTHONPATH="$(APP_PKG)" $(APP_PYTHON) build_db.py \
>   --db "$(APP_BIN)/extensions/codeintel@activestate.com/pylib/codeintel/codeintel.db" \
>   $(if $(CIX_DIR),--cix-dir "$(CIX_DIR)",)
> @mkdir -p "$(BUILD_STAGE)/modules/codeintel/pylib/codeintel"
> cp "$(APP_BIN)/extensions/codeintel@activestate.com/pylib/codeintel/codeintel.db" \
>   "$(BUILD_STAGE)/modules/codeintel/pylib/codeintel/codeintel.db"
> @if [ -d "$(BUILD_STAGE)/modules/codeintel/build/xpi/pylib/codeintel" ]; then \
>   cp "$(APP_BIN)/extensions/codeintel@activestate.com/pylib/codeintel/codeintel.db" \
>     "$(BUILD_STAGE)/modules/codeintel/build/xpi/pylib/codeintel/codeintel.db"; \
> fi

xpcom-check:
> @test -x "$(APP_BIN)/mozpython" || { echo "error: missing $(APP_BIN)/mozpython; run 'make build'"; exit 1; }
> MOZILLA_FIVE_HOME="$(APP_BIN)" LD_LIBRARY_PATH="$(APP_BIN)" \
>   PYTHONPATH="$(APP_BIN)/python" $(APP_BIN)/mozpython -c \
>   "from xpcom import components; print('xpcom OK:', len(components.classes), 'classes')"

dbgp-check:
> @test -x "$(APP_PYTHON)" || { echo "error: missing $(APP_PYTHON); run 'make silo-python'"; exit 1; }
> PYTHONPATH="$(APP_PKG)" $(APP_PYTHON) -c \
>   "import dbgp, dbgp.server, dbgp.serverBase; print('dbgp OK')"

codeintel-check:
> @test -x "$(APP_PYTHON)" || { echo "error: missing $(APP_PYTHON); run 'make silo-python'"; exit 1; }
> cd "$(ROOT)/src/modules/codeintel/server/src" && \
>   PYTHONPATH="$$PWD:$(APP_PKG)" $(APP_PYTHON) -c \
>   "import config, scanner, completions, calltips, server; print('codeintel OK')"

# Catch a truncated components/komodo.manifest, which silently disables most
# Python XPCOM components (no window and no Python logging).
manifest-check:
> @f="$(APP_BIN)/components/komodo.manifest"; \
>   test -f "$$f" || { echo "error: missing $$f; run 'make build'"; exit 1; }; \
>   grep -q '^component .* koInitService.py$$' "$$f" || { \
>     echo "error: $$f is incomplete (koInitService is missing)"; \
>     echo "       delete $$f and the .manifest.landmark files in $(BUILD_STAGE)/components, then run 'make build'"; \
>     exit 1; }; \
>   n=$$(grep -c '^component ' "$$f"); \
>   test "$$n" -ge 300 || { \
>     echo "error: $$f only registers $$n components; expected >= 300"; \
>     exit 1; }; \
>   echo "manifest OK: $$n components"

check: xpcom-check dbgp-check codeintel-check manifest-check

# Each build portion below has its own clean target.  They intentionally use
# direct rm rather than `bk clean`, because `bk clean` removes the whole app
# build.  `make clean-all` runs the component cleaners, `make distclean`
# removes the Mozilla tree and generated configuration as well.

clean: clean-app

clean-app:
> @test -x "$(BK)" || { echo "error: missing $(BK)"; exit 1; }
> $(BK) clean $(CLEAN_ARGS)

# Scintilla: staged source tree and compiled libraries.
clean-scintilla:
> rm -rf "$(BUILD_STAGE)/scintilla" "$(BUILD_STAGE)/scintilla-patch-log"
> rm -rf "$(ROOT)/src/scintilla/headless/__pycache__"
> find "$(ROOT)/src/scintilla" -maxdepth 2 \
>   \( -name '*.o' -o -name '*.a' -o -name '*.so' \) -type f -delete 2>/dev/null || true

# SciMoz: XPCOM component, wrapper JS, and NPAPI plugin.
clean-scimoz:
> rm -rf "$(BUILD_STAGE)/SciMoz"
> rm -rf "$(APP_BIN)"/components/ISciMoz* \
>   "$(APP_BIN)"/components/libkoScimozHeadless.so \
>   "$(APP_BIN)"/components/koScintillaController.py \
>   "$(APP_BIN)"/components/scimoz_wrapper.js
> rm -rf "$(APP_BIN)"/plugins/libnpscimoz.so "$(APP_BIN)"/plugins/SciMoz.plugin

# SilverCity: Python extension built from src/silvercity.
clean-silvercity:
> rm -rf "$(BUILD_STAGE)/silvercity"
> rm -rf "$(ROOT)/src/silvercity/build" "$(ROOT)/src/silvercity/dist"
> rm -rf "$(ROOT)/src/silvercity/bitbucket"
> rm -rf "$(ROOT)"/src/silvercity/*.egg-info \
>   "$(ROOT)"/src/silvercity/*/*.egg-info
> rm -rf "$(APP_PKG)/SilverCity" "$(APP_PKG)"/SilverCity-*.egg-info

# sgmlop: Python extension built from contrib/sgmlop.
clean-sgmlop:
> rm -rf "$(BUILD_STAGE)/contrib/sgmlop"
> rm -rf "$(ROOT)/contrib/sgmlop/build" "$(ROOT)/contrib/sgmlop/dist"
> rm -rf "$(ROOT)"/contrib/sgmlop/*.egg-info
> rm -rf "$(APP_PKG)"/sgmlop*.so "$(APP_PKG)"/sgmlop-*.egg-info \
>   "$(APP_PKG)"/sgmlop*.dist-info

# CodeIntel: the XPI/extension staged by koext plus its build tree.
clean-codeintel:
> rm -rf "$(BUILD_STAGE)/modules/codeintel"
> rm -rf "$(APP_BIN)/extensions/codeintel@activestate.com"
> rm -rf "$(ROOT)/src/modules/codeintel/build"

# The optional seed database (generated, never committed).
clean-codeintel-db:
> rm -f "$(APP_BIN)/extensions/codeintel@activestate.com/pylib/codeintel/codeintel.db"
> rm -f "$(ROOT)/src/modules/codeintel/server/codeintel.db"

# DBGP: the IDE-side Python DBGP server package.
clean-dbgp:
> rm -rf "$(BUILD_STAGE)/dbgp" "$(APP_PKG)/dbgp"

# Python 3 runtime dependencies installed by install-py3-python-deps.py.
clean-python3-deps:
> rm -rf "$(APP_PKG)"/apsw "$(APP_PKG)"/apsw-*.dist-info \
>   "$(APP_PKG)"/apsw-*.egg-info \
>   "$(APP_PKG)"/twisted "$(APP_PKG)"/twisted-*.dist-info \
>   "$(APP_PKG)"/Twisted-*.egg-info "$(APP_PKG)"/Twisted_*.egg-info \
>   "$(APP_PKG)"/Crypto "$(APP_PKG)"/pycryptodome-*.dist-info \
>   "$(APP_PKG)"/pycrypto-*.egg-info \
>   "$(APP_PKG)"/zope "$(APP_PKG)"/zope.*-*.egg-info \
>   "$(APP_PKG)"/zope_*.dist-info \
>   "$(APP_PKG)"/peewee "$(APP_PKG)"/peewee.py "$(APP_PKG)"/peewee-*.dist-info
> @for site in "$(APP_SILO)"/lib/python*/site-packages; do \
>   [ -d "$$site" ] || continue; \
>   rm -rf "$$site"/apsw* "$$site"/twisted* "$$site"/Twisted* \
>     "$$site"/Crypto "$$site"/pycryptodome* "$$site"/zope* \
>     "$$site"/peewee*; \
> done

# The Python 3 silo is a set of symlinks into the system Python.
clean-silo-python:
> rm -rf "$(APP_SILO)"

clean-pyxpcom:
> cd "$(MOZ)" && $(PYTHON) build.py pyxpcom_distclean

clean-mozilla:
> cd "$(MOZ)" && $(PYTHON) build.py clean

clean-image:
> @if [ -n "$(IMAGE_DIR)" ]; then rm -rf "$(IMAGE_DIR)"; \
> else rm -rf "$(ROOT)/install"; fi

clean-packages:
> rm -rf "$(ROOT)/packages"

clean-pycache:
> find "$(ROOT)/src" "$(ROOT)/contrib" "$(ROOT)/util" \
>   -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
> find "$(ROOT)/src" "$(ROOT)/contrib" "$(ROOT)/util" \
>   \( -name '*.pyc' -o -name '*.pyo' \) -type f -delete 2>/dev/null || true

clean-config:
> rm -f "$(ROOT)/bkconfig.py" "$(ROOT)/bkconfig.pm" \
>   "$(ROOT)/bkconfig.sh"

clean-parts: clean-scintilla clean-scimoz clean-silvercity clean-sgmlop \
    clean-codeintel clean-codeintel-db clean-dbgp

clean-all: clean-app clean-parts clean-image clean-packages clean-pycache

distclean:
> @test -x "$(BK)" || { echo "error: missing $(BK)"; exit 1; }
> $(BK) distclean
> cd "$(MOZ)" && $(PYTHON) build.py distclean
> $(MAKE) clean-pycache
> $(MAKE) clean-config

status:
> @echo "ROOT              = $(ROOT)"
> @echo "PYTHON            = $(PYTHON)"
> @echo "MOZ_SRC           = $(MOZ_SRC)"
> @echo "MOZ_DIST          = $(MOZ_DIST)"
> @echo "APP_BIN           = $(APP_BIN)"
> @echo "APP_SILO          = $(APP_SILO)"
> @echo "APP_PYTHON        = $(APP_PYTHON)"
> @echo "APP_PKG           = $(APP_PKG)"
> @echo "BUILD_STAGE       = $(BUILD_STAGE)"
> @echo "IMAGE_DIR         = $(IMAGE_DIR)"
> @echo "IMAGE_INSTALLDIR  = $(IMAGE_INSTALLDIR)"
> @echo "PREFIX            = $(PREFIX)"
> @echo "LOCAL_PREFIX      = $(LOCAL_PREFIX)"
> @echo "INSTALL_NAME      = $(INSTALL_NAME)"
> @echo "ICON_STYLE        = $(ICON_STYLE)"
