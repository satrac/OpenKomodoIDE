# Raku support

Komodo's Raku support is being brought up in phases.  The first phase is
usable editor support; DAP debugging and LSP/CodeIntel integration follow.

## Phase 0 — editor, run, lint, REPL (implemented)

- Native `Raku` language component with file associations:
  `.raku`, `.rakumod`, `.rakutest`, `.p6`, `.pm6`, `.t6`.
- `raku`, `rakudo` and `perl6` shebangs, plus the Emacs `raku`/`perl6` modes.
- Syntax highlighting through Scintilla's Perl lexer with a Raku keyword list.
  This is deliberate and provisional: Scintilla 3.x in the Mozilla 35 runtime
  has no dedicated Raku lexer.
- Raku interpreter preference (`rakuDefaultInterpreter`), discovery through
  PATH and common `rakubrew` / Perl 6 locations, and `RAKULIB` support via
  `rakuExtraPaths`.
- Run support through `koInvocation;1?type=Raku`.
- Background syntax checking with `raku -c`.
- A Raku REPL started from the Interactive Shell menu.  It runs
  `raku --repl-mode=process` in Komodo's terminal pane rather than through the
  DBGP debugger.

### Phase 0 follow-ups

- Replace the Perl-lexer fallback with a dedicated Raku lexer or a bundled UDL.
- Ship a Raku icon and Raku snippets/file templates.
- Add automated GUI tests for run, lint and the REPL.

## Phase 1 — DAP bridge

Komodo's debugger is DBGP-based.  Raku's current ecosystem is DAP-based
(`nimala-raku-dap` is a commonly available adapter; `raku-debug` is a CLI
debugger).  Do not make the debugger UI speak a second protocol ad hoc.  Build
a bridge first.

### Option A (recommended): DAP-to-DBGP adapter bridge

Add a small adapter owned by Komodo that:

1. Starts the configured Raku DAP adapter, normally
   `raku-dap`/`nimala-raku-dap`, over stdio.
2. Speaks DAP JSON-RPC to it.
3. Exposes the debugger to Komodo as a normal `koInvocation;1?type=Raku`
   DBGP endpoint.

Advantages:

- Reuses the existing `koIDBGPSession`, stack/variables/watch/evaluate UI,
  breakpoint manager, toolbar and session tabs.
- Keeps the DBGP debugger contract stable while DAP matures.

Required DAP mappings:

| DAP request/event | Komodo/DBGP equivalent |
| --- | --- |
| `initialize` | DBGP `init` |
| `launch` / `attach` | invocation launch/attach |
| `setBreakpoints` | `breakpoint_set` / `breakpoint_remove` |
| `configurationDone` | DBGP `run` |
| `continue`, `next`, `stepIn`, `stepOut`, `pause` | `run`, `step_over`, `step_into`, `step_out`, `break` |
| `threads` | DBGP `status` / session thread list |
| `stackTrace` | DBGP `stack_get` |
| `scopes` + `variables` | DBGP `context_get` / property traversal |
| `evaluate` | DBGP `eval` |
| `stopped` / `continued` / `terminated` events | `status` / `detach` notifications |

### Option B: native DAP client

Add a DAP client directly in the JS/Python side and extend the debugger UI to
consume DAP objects.  This is cleaner long-term but duplicates most of the
debugger session, stack, variable and watch plumbing.  Do this only if the
bridge becomes a limiting abstraction.

## Phase 2 — debugger integration

- Add a `rakuDapCommand` preference (default: `raku-dap` if found, otherwise
  empty) and optionally `rakuDapArgs`.
- Mark `koRakuInvocation` as supporting `INVOKE_DEBUG` once the bridge is
  reliable.
- Add launch/attach preference fields: script, arguments, cwd, environment,
  stop-on-entry, adapter command.
- Map Raku file references and breakpoint verification through the existing
  debugger source/breakpoint model.
- Verify breakpoints in `.raku`, `.rakumod` and `.rakutest` files.

## Phase 3 — DAP tests and hardening

- Adapter contract tests with a recorded/minimal DAP adapter.
- Shared integration test: launch, breakpoint, continue, step, stack trace,
  evaluate, variables, terminate.
- Test adapter failure, missing adapter, malformed DAP messages, and shutdown
  during startup.
- Package/document the adapter as an optional dependency; do not commit a
  vendored adapter binary.

## Phase 4 — LSP / CodeIntel (optional, after DAP)

`nimala-raku-lsp` can provide diagnostics, completion, symbols and
definitions.  Integrate it through a small LSP client:

- LSP `textDocument/publishDiagnostics` -> Komodo lint results.
- `textDocument/completion` -> CodeIntel completion UI.
- `textDocument/hover` + `definition` -> CodeIntel calltips/navigation.
- `workspace/symbol` -> Go to Anything/CodeIntel symbols.
- Map LSP `file://` URIs to Komodo `koIFileEx` paths.

Raku has no bundled CIX database in this repository yet.  LSP is the preferred
route rather than generating and maintaining a Raku CIX.

## Acceptance criteria

- Phase 0: `.raku` files open as Raku, run, syntax-check and start a REPL.
- Phase 1: a Raku DAP adapter can be configured and driven by the bridge.
- Phase 2: Komodo can set breakpoints, step, inspect stacks/variables and
  evaluate expressions in a Raku script.
- Phase 3: the above is covered by repeatable tests and survives adapter
  errors.
- Phase 4: Raku diagnostics/completions appear through LSP without blocking
  the editor when the server is absent.
