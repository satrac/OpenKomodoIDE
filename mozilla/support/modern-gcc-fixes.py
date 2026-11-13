#!/usr/bin/env python3
"""Apply the fixes needed to build Firefox 35 / Komodo 12 with a modern GCC.

The Firefox 35 tree is from 2014 and predates C++11/17, glibc's removal of
several headers/functions, and the stricter diagnostics in GCC 14+.  Rather
than maintaining a fragile patch against a generated tree, this script applies
the small, targeted edits idempotently after the Komodo patch set has been
applied and before ``configure`` is run.

Usage:
    modern-gcc-fixes.py <path-to-mozilla-src-tree>

The script is safe to run repeatedly.
"""

import os
import sys


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def replace_once(root, relpath, old, new, label):
    path = os.path.join(root, relpath)
    if not os.path.exists(path):
        print("modern-gcc: missing %s (skipped: %s)" % (relpath, label))
        return
    text = _read(path)
    if new and new in text and old not in text:
        return  # already applied
    if old not in text:
        print("modern-gcc: pattern not found in %s (skipped: %s)"
              % (relpath, label))
        return
    _write(path, text.replace(old, new, 1))
    print("modern-gcc: patched %s (%s)" % (relpath, label))


def replace_all(root, relpath, old, new, label):
    path = os.path.join(root, relpath)
    if not os.path.exists(path):
        print("modern-gcc: missing %s (skipped: %s)" % (relpath, label))
        return
    text = _read(path)
    if old not in text:
        return  # already applied (or nothing to do)
    _write(path, text.replace(old, new))
    print("modern-gcc: patched %s (%s)" % (relpath, label))


def insert_before(root, relpath, anchor, insert, label):
    path = os.path.join(root, relpath)
    if not os.path.exists(path):
        print("modern-gcc: missing %s (skipped: %s)" % (relpath, label))
        return
    text = _read(path)
    if insert in text:
        return
    if anchor not in text:
        print("modern-gcc: anchor not found in %s (skipped: %s)"
              % (relpath, label))
        return
    _write(path, text.replace(anchor, insert + anchor, 1))
    print("modern-gcc: patched %s (%s)" % (relpath, label))


def insert_after(root, relpath, anchor, insert, label):
    path = os.path.join(root, relpath)
    if not os.path.exists(path):
        print("modern-gcc: missing %s (skipped: %s)" % (relpath, label))
        return
    text = _read(path)
    if insert in text:
        return
    if anchor not in text:
        print("modern-gcc: anchor not found in %s (skipped: %s)"
              % (relpath, label))
        return
    _write(path, text.replace(anchor, anchor + insert, 1))
    print("modern-gcc: patched %s (%s)" % (relpath, label))


def main(argv):
    if len(argv) != 2:
        sys.exit("usage: modern-gcc-fixes.py <mozilla-src-tree>")
    root = argv[1]
    if not os.path.isdir(root):
        sys.exit("not a directory: %s" % root)

    # ---- glibc no longer provides <sys/sysctl.h> -----------------------
    replace_once(
        root, "memory/mozjemalloc/jemalloc.c",
        "#if !defined(MOZ_MEMORY_SOLARIS) && !defined(MOZ_MEMORY_ANDROID)\n"
        "#include <sys/sysctl.h>\n#endif",
        "#if !defined(MOZ_MEMORY_SOLARIS) && !defined(MOZ_MEMORY_ANDROID) && "
        "defined(MOZ_MEMORY_BSD)\n#include <sys/sysctl.h>\n#endif",
        "jemalloc sysctl include")

    replace_once(
        root, "ipc/chromium/src/third_party/libevent/arc4random.c",
        "#ifdef _EVENT_HAVE_SYS_SYSCTL_H\n#include <sys/sysctl.h>\n#endif",
        "#if defined(_EVENT_HAVE_SYS_SYSCTL_H) && !defined(__linux__)\n"
        "#include <sys/sysctl.h>\n#endif",
        "libevent sysctl include")

    replace_once(
        root, "ipc/chromium/src/third_party/libevent/arc4random.c",
        "#if defined(_EVENT_HAVE_SYS_SYSCTL_H) && "
        "defined(_EVENT_HAVE_SYSCTL)\n#if _EVENT_HAVE_DECL_CTL_KERN",
        "#if defined(_EVENT_HAVE_SYS_SYSCTL_H) && "
        "defined(_EVENT_HAVE_SYSCTL) && !defined(__linux__)\n"
        "#if _EVENT_HAVE_DECL_CTL_KERN",
        "libevent sysctl seed path")

    replace_once(
        root, "media/mtransport/third_party/nICEr/src/stun/addrs.c",
        "#ifndef ANDROID\n#include <sys/sysctl.h>\n#include <sys/syslog.h>\n"
        "#else",
        "#if !defined(ANDROID) && !defined(LINUX)\n"
        "#include <sys/sysctl.h>\n#include <sys/syslog.h>\n#else",
        "nICEr sysctl include")

    # ---- modern glibc also declares the arc4random family ---------------
    evutil_path = os.path.join(
        root, "ipc/chromium/src/third_party/libevent/evutil_rand.c")
    if os.path.exists(evutil_path) and \
            "evutil_arc4random" not in _read(evutil_path):
        insert_after(
            root, "ipc/chromium/src/third_party/libevent/evutil_rand.c",
            "#define ARC4RANDOM_NOUNIFORM\n",
            "\n/* Modern glibc also exposes the arc4random family in <stdlib.h>; "
            "keep\n * the bundled implementation in libevent's namespace. */\n"
            "#if defined(__linux__)\n"
            "#define arc4random evutil_arc4random\n"
            "#define arc4random_buf evutil_arc4random_buf\n"
            "#define arc4random_uniform evutil_arc4random_uniform\n"
            "#define arc4random_stir evutil_arc4random_stir\n"
            "#define arc4random_addrandom evutil_arc4random_addrandom\n"
            "#endif\n",
            "libevent arc4random namespace")

    # ---- mozalloc.h can be included with an extern "C" scope open ------
    mozalloc_path = os.path.join(root, "memory/mozalloc/mozalloc.h")
    if os.path.exists(mozalloc_path):
        mozalloc = _read(mozalloc_path)
        if 'extern "C++" {' not in mozalloc:
            replace_once(
                root, "memory/mozalloc/mozalloc.h",
                "MOZALLOC_EXPORT_NEW MOZALLOC_INLINE\n"
                "void* operator new(size_t size) MOZALLOC_THROW_BAD_ALLOC",
                '/* Force C++ linkage: some consumers leave an extern "C" '
                'scope open. */\nextern "C++" {\n\n'
                "MOZALLOC_EXPORT_NEW MOZALLOC_INLINE\n"
                "void* operator new(size_t size) MOZALLOC_THROW_BAD_ALLOC",
                "mozalloc operator new linkage")
            replace_once(
                root, "memory/mozalloc/mozalloc.h",
                "void operator delete[](void* ptr, const std::nothrow_t&) "
                "MOZALLOC_THROW_IF_HAS_EXCEPTIONS\n"
                "{\n    return moz_free(ptr);\n}\n",
                "void operator delete[](void* ptr, const std::nothrow_t&) "
                "MOZALLOC_THROW_IF_HAS_EXCEPTIONS\n"
                "{\n    return moz_free(ptr);\n}\n"
                '\n} /* extern "C++" */\n',
                "mozalloc operator delete linkage")

    # ---- bool function returning nullptr --------------------------------
    replace_once(
        root, "dom/ipc/Blob.cpp",
        "  // Make sure we can't overflow.\n"
        "  if (NS_WARN_IF(UINT64_MAX - aLength < aStart)) {\n"
        "    ASSERT_UNLESS_FUZZING();\n"
        "    return nullptr;\n"
        "  }\n"
        "\n"
        "  ErrorResult errorResult;\n"
        "  uint64_t blobLength = mBlobImpl->GetSize(errorResult);\n"
        "  MOZ_ASSERT(!errorResult.Failed());\n"
        "\n"
        "  if (NS_WARN_IF(aStart + aLength > blobLength)) {\n"
        "    ASSERT_UNLESS_FUZZING();\n"
        "    return nullptr;\n"
        "  }",
        "  // Make sure we can't overflow.\n"
        "  if (NS_WARN_IF(UINT64_MAX - aLength < aStart)) {\n"
        "    ASSERT_UNLESS_FUZZING();\n"
        "    return false;\n"
        "  }\n"
        "\n"
        "  ErrorResult errorResult;\n"
        "  uint64_t blobLength = mBlobImpl->GetSize(errorResult);\n"
        "  MOZ_ASSERT(!errorResult.Failed());\n"
        "\n"
        "  if (NS_WARN_IF(aStart + aLength > blobLength)) {\n"
        "    ASSERT_UNLESS_FUZZING();\n"
        "    return false;\n"
        "  }",
        "Blob.cpp bool returns")

    # ---- breakpad: glibc renamed struct ucontext and tgkill ------------
    for relpath in (
            "toolkit/crashreporter/google-breakpad/src/client/linux/handler/exception_handler.h",
            "toolkit/crashreporter/google-breakpad/src/client/linux/handler/exception_handler.cc",
            "toolkit/crashreporter/google-breakpad/src/client/linux/minidump_writer/minidump_writer.cc"):
        replace_all(root, relpath, "struct ucontext", "ucontext_t",
                    "breakpad ucontext")
    replace_once(
        root,
        "toolkit/crashreporter/google-breakpad/src/client/linux/handler/exception_handler.cc",
        "static int tgkill(pid_t tgid, pid_t tid, int sig) {",
        "static int breakpad_tgkill(pid_t tgid, pid_t tid, int sig) {",
        "breakpad tgkill")
    eh_path = os.path.join(
        root,
        "toolkit/crashreporter/google-breakpad/src/client/linux/handler/exception_handler.cc")
    if os.path.exists(eh_path) and \
            "breakpad_tgkill(getpid()" not in _read(eh_path):
        replace_once(
            root,
            "toolkit/crashreporter/google-breakpad/src/client/linux/handler/exception_handler.cc",
            "tgkill(getpid(), syscall(__NR_gettid), sig)",
            "breakpad_tgkill(getpid(), syscall(__NR_gettid), sig)",
            "breakpad tgkill call")
    replace_once(
        root,
        "toolkit/crashreporter/google-breakpad/src/client/linux/handler/exception_handler.cc",
        "static const unsigned kSigStackSize = std::max(8192, SIGSTKSZ);",
        "static const size_t kSigStackSize = std::max<size_t>(8192u, "
        "(size_t)SIGSTKSZ);",
        "breakpad SIGSTKSZ")

    # ---- major/minor moved to <sys/sysmacros.h> ------------------------
    insert_after(root, "xpcom/io/nsLocalFileUnix.cpp",
                 "#include <sys/types.h>\n",
                 "#include <sys/sysmacros.h>\n",
                 "nsLocalFileUnix sysmacros")

    # ---- std::numeric_limits needs <limits> ----------------------------
    insert_after(root, "gfx/2d/Matrix.cpp",
                 "#include <algorithm>\n",
                 "#include <limits>\n",
                 "Matrix limits include")

    # ---- GCC may emit only a local ISRA clone of GetShapedWord ---------
    gfxfont_path = os.path.join(root, "gfx/thebes/gfxFont.cpp")
    if os.path.exists(gfxfont_path) and \
            "Explicit instantiations of GetShapedWord" not in _read(gfxfont_path):
        insert_after(
            root, "gfx/thebes/gfxFont.cpp",
            "template bool\ngfxFont::SplitAndInitTextRun(gfxContext *aContext,\n"
            "                             gfxTextRun *aTextRun,\n"
            "                             const char16_t *aString,\n"
            "                             uint32_t aRunStart,\n"
            "                             uint32_t aRunLength,\n"
            "                             int32_t aRunScript,\n"
            "                             bool aVertical);\n",
            "\n// Explicit instantiations of GetShapedWord: newer GCC may "
            "otherwise emit\n"
            "// only a local ISRA clone, leaving gfxTextRun.cpp with an "
            "undefined symbol.\n"
            "template gfxShapedWord*\n"
            "gfxFont::GetShapedWord(gfxContext *aContext, const uint8_t *aText,\n"
            "                       uint32_t aLength, uint32_t aHash, "
            "int32_t aRunScript,\n"
            "                       bool aVertical, "
            "int32_t aAppUnitsPerDevUnit,\n"
            "                       uint32_t aFlags, "
            "gfxTextPerfMetrics *aTextPerf);\n"
            "template gfxShapedWord*\n"
            "gfxFont::GetShapedWord(gfxContext *aContext, const char16_t *aText,\n"
            "                       uint32_t aLength, uint32_t aHash, "
            "int32_t aRunScript,\n"
            "                       bool aVertical, "
            "int32_t aAppUnitsPerDevUnit,\n"
            "                       uint32_t aFlags, "
            "gfxTextPerfMetrics *aTextPerf);\n",
            "gfxFont GetShapedWord instantiation")

    # ---- drop the 2014 STL/system header wrappers and keep symbols hidden
    for relpath in ("configure.in",):
        replace_once(root, relpath,
                     "VISIBILITY_FLAGS='-I$(DIST)/system_wrappers -include "
                     "$(MOZILLA_DIR)/config/gcc_hidden_dso_handle.h'",
                     "VISIBILITY_FLAGS='-fvisibility=hidden'",
                     "configure.in visibility flags")
        replace_once(root, relpath,
                     "VISIBILITY_FLAGS='-I$(DIST)/system_wrappers -include "
                     "$(MOZILLA_DIR)/config/gcc_hidden.h'",
                     "VISIBILITY_FLAGS='-fvisibility=hidden'",
                     "configure.in visibility flags")
        replace_once(root, relpath,
                     "  STL_FLAGS='-I$(DIST)/stl_wrappers'",
                     "  STL_FLAGS=''",
                     "configure.in STL wrappers")

    # ---- GCC 16 removes the zeroing memset in old Mozilla classes -------
    # NS_DECL_AND_IMPL_ZEROING_OPERATOR_NEW relies on the compiler keeping
    # memset(rv, 0, sz).  GCC 16 can delete it when it can see that the
    # constructor initializes only part of the object, leaving pointers such
    # as nsViewManager::mPresShell uninitialized (jemalloc fills them with
    # 0x5a, causing a startup SIGSEGV).
    replace_once(
        root, "xpcom/ds/nsCRT.h",
        "// This macro can be used in a class declaration for classes that want\n"
        "// to ensure that their instance memory is zeroed.\n"
        "#define NS_DECL_AND_IMPL_ZEROING_OPERATOR_NEW   \\\n"
        "  void* operator new(size_t sz) CPP_THROW_NEW { \\\n"
        "    void* rv = ::operator new(sz);              \\\n"
        "    if (rv) {                                   \\\n"
        "      memset(rv, 0, sz);                        \\\n"
        "    }                                           \\\n"
        "    return rv;                                  \\\n"
        "  }                                             \\\n"
        "  void operator delete(void* ptr) {             \\\n"
        "    ::operator delete(ptr);                     \\\n"
        "  }\n",
        "// GCC 16 can eliminate the memset in the zeroing operator new below\n"
        "// when it can see that the constructor only initializes part of the\n"
        "// object.  Old Mozilla code relies on the remaining members being\n"
        "// zeroed, so keep an empty asm barrier after the store.\n"
        "#if defined(__GNUC__)\n"
        "#  define NS_KEEP_ZEROING_STORES(ptr) \\\n"
        "     __asm__ __volatile__(\"\" : : \"r\"(ptr) : \"memory\")\n"
        "#else\n"
        "#  define NS_KEEP_ZEROING_STORES(ptr) do { } while (0)\n"
        "#endif\n"
        "\n"
        "// This macro can be used in a class declaration for classes that want\n"
        "// to ensure that their instance memory is zeroed.\n"
        "#define NS_DECL_AND_IMPL_ZEROING_OPERATOR_NEW   \\\n"
        "  void* operator new(size_t sz) CPP_THROW_NEW { \\\n"
        "    void* rv = ::operator new(sz);              \\\n"
        "    if (rv) {                                   \\\n"
        "      memset(rv, 0, sz);                        \\\n"
        "      NS_KEEP_ZEROING_STORES(rv);               \\\n"
        "    }                                           \\\n"
        "    return rv;                                  \\\n"
        "  }                                             \\\n"
        "  void operator delete(void* ptr) {             \\\n"
        "    ::operator delete(ptr);                     \\\n"
        "  }\n",
        "nsCRT zeroing operator new")
    replace_once(
        root, "xpcom/ds/nsCRT.h",
        "#define NS_IMPL_ZEROING_OPERATOR_NEW(_class)            \\\n"
        "  void* _class::operator new(size_t sz) CPP_THROW_NEW { \\\n"
        "    void* rv = ::operator new(sz);                      \\\n"
        "    if (rv) {                                           \\\n"
        "      memset(rv, 0, sz);                                \\\n"
        "    }                                                   \\\n"
        "    return rv;                                          \\\n"
        "  }                                                     \\\n"
        "  void _class::operator delete(void* ptr) {             \\\n"
        "    ::operator delete(ptr);                             \\\n"
        "  }\n",
        "#define NS_IMPL_ZEROING_OPERATOR_NEW(_class)            \\\n"
        "  void* _class::operator new(size_t sz) CPP_THROW_NEW { \\\n"
        "    void* rv = ::operator new(sz);                      \\\n"
        "    if (rv) {                                           \\\n"
        "      memset(rv, 0, sz);                                \\\n"
        "      NS_KEEP_ZEROING_STORES(rv);                       \\\n"
        "    }                                                   \\\n"
        "    return rv;                                          \\\n"
        "  }                                                     \\\n"
        "  void _class::operator delete(void* ptr) {             \\\n"
        "    ::operator delete(ptr);                             \\\n"
        "  }\n",
        "nsCRT zeroing operator new (out-of-line)")

    # Explicitly initialize the view manager's pointer fields as well; this
    # does not depend on the compiler preserving the zeroing operator new.
    replace_once(
        root, "view/nsViewManager.cpp",
        "nsViewManager::nsViewManager()\n"
        "  : mDelayedResize(NSCOORD_NONE, NSCOORD_NONE)\n"
        "{\n"
        "  mRootViewManager = this;\n",
        "nsViewManager::nsViewManager()\n"
        "  : mPresShell(nullptr)\n"
        "  , mDelayedResize(NSCOORD_NONE, NSCOORD_NONE)\n"
        "  , mRootView(nullptr)\n"
        "  , mRootViewManager(this)\n"
        "  , mRefreshDisableCount(0)\n"
        "  , mPainting(false)\n"
        "  , mRecursiveRefreshPending(false)\n"
        "  , mHasPendingWidgetGeometryChanges(false)\n"
        "  , mInScroll(false)\n"
        "{\n",
        "nsViewManager constructor initialization")
    replace_once(
        root, "view/nsViewManager.cpp",
        "\n"
        "  // NOTE:  we use a zeroing operator new, so all data members are\n"
        "  // assumed to be cleared here.\n"
        "  mHasPendingWidgetGeometryChanges = false;\n"
        "  mRecursiveRefreshPending = false;\n"
        "}\n",
        "\n"
        "  // All data members are explicitly initialized above.  Do not rely on\n"
        "  // the zeroing operator new here: some GCC versions optimize its\n"
        "  // memset away.\n"
        "}\n",
        "nsViewManager constructor body")

    # ---- HarfBuzz assumes a null C++ this pointer is harmless -----------
    # hb_buffer_t::reset() destroys a null unicode funcs pointer on a newly
    # calloc'd buffer.  GCC 16 removes the null-"this" check in the inlined
    # hb_object_destroy, so make the null case explicit.
    replace_once(
        root, "gfx/harfbuzz/src/hb-unicode.cc",
        "void\n"
        "hb_unicode_funcs_destroy (hb_unicode_funcs_t *ufuncs)\n"
        "{\n"
        "  if (!hb_object_destroy (ufuncs)) return;\n",
        "void\n"
        "hb_unicode_funcs_destroy (hb_unicode_funcs_t *ufuncs)\n"
        "{\n"
        "  /* GCC assumes a C++ \"this\" pointer is never null and can remove\n"
        "   * the null check in hb_object_destroy when inlined. */\n"
        "  if (!ufuncs) return;\n"
        "\n"
        "  if (!hb_object_destroy (ufuncs)) return;\n",
        "HarfBuzz null unicode funcs")
    replace_once(
        root, "gfx/harfbuzz/src/hb-buffer.cc",
        "    return;\n"
        "\n"
        "  hb_unicode_funcs_destroy (unicode);\n"
        "  unicode = hb_unicode_funcs_get_default ();\n",
        "    return;\n"
        "\n"
        "  if (unicode)\n"
        "    hb_unicode_funcs_destroy (unicode);\n"
        "  unicode = hb_unicode_funcs_get_default ();\n",
        "HarfBuzz buffer reset unicode")
    replace_once(
        root, "gfx/harfbuzz/src/hb-buffer.cc",
        "  if (!hb_object_destroy (buffer)) return;\n"
        "\n"
        "  hb_unicode_funcs_destroy (buffer->unicode);\n"
        "\n"
        "  free (buffer->info);\n",
        "  if (!hb_object_destroy (buffer)) return;\n"
        "\n"
        "  if (buffer->unicode)\n"
        "    hb_unicode_funcs_destroy (buffer->unicode);\n"
        "\n"
        "  free (buffer->info);\n",
        "HarfBuzz buffer destroy unicode")

    print("modern-gcc: done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
