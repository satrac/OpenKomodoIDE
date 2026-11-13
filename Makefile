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
KOMODO_VERSION ?= 12.10.0
KOMODO_BUILDNUM ?= 1
CONFIGURE_ARGS ?= --ide --dev

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

PREFIX ?= /usr/local
LOCAL_PREFIX ?= $(HOME)/.local
DESTDIR ?=
INSTALL_NAME ?= komodo-12.10
PACKAGE ?= std
CIX_DIR ?=

.PHONY: help all configure register-mozbuild build app run test \
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
> @echo "  make configure        Configure the Black/Komodo build"
> @echo "  make build            Incremental build of the Komodo app bits"
> @echo "  make run              Run the built IDE"
> @echo "  make test             Run the Komodo self-test target"
> @echo "  make status           Show the resolved build paths"
> @echo ""
> @echo "Full/bootstrap:"
> @echo "  make mozilla-all      Build Mozilla + PyXPCOM + Python silo + deps"
> @echo "  make full             mozilla-all, then the Komodo app build"
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

all: build

configure:
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
> $(BK) run $(RUN_ARGS)

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

mozilla:
> cd "$(MOZ)" && $(PYTHON) build.py mozilla

mozilla-all:
> cd "$(MOZ)" && $(PYTHON) build.py all

full: mozilla-all
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
> cp -a "$(IMAGE_INSTALLDIR)/." "$(LOCAL_PREFIX)/lib/$(INSTALL_NAME)/"
> printf '%s\n' '#!/bin/sh' \
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
> cd "$(ROOT)/src/silvercity" && $(APP_PYTHON) setup.py install --skip-build \
>   --install-lib="$(APP_PKG)" --install-scripts=bitbucket \
>   --install-data=bitbucket --prefix=bitbucket

sgmlop:
> @test -x "$(APP_PYTHON)" || { echo "error: missing $(APP_PYTHON); run 'make silo-python'"; exit 1; }
> cd "$(ROOT)/contrib/sgmlop" && $(APP_PYTHON) setup.py install \
>   --install-lib="$(APP_PKG)" --install-scripts=bitbucket \
>   --install-data=bitbucket --prefix=bitbucket

codeintel-db:
> @test -x "$(APP_PYTHON)" || { echo "error: missing $(APP_PYTHON); run 'make silo-python'"; exit 1; }
> cd "$(ROOT)/src/modules/codeintel/server" && \
>   PYTHONPATH="$(APP_PKG)" $(APP_PYTHON) build_db.py \
>   --db "$(APP_BIN)/extensions/codeintel@activestate.com/pylib/codeintel/codeintel.db" \
>   $(if $(CIX_DIR),--cix-dir "$(CIX_DIR)",)

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

check: xpcom-check dbgp-check codeintel-check

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
