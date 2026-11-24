# Raku support

Raku has a native language service, a dedicated UDL lexer, LSP-backed
CodeIntel, LSP diagnostics and a DAP-to-DBGP debugger bridge.

## Status at a glance

| Area | Status | Notes |
| --- | --- | --- |
| Editing / highlighting | ✅ | Dedicated UDL lexer (`src/udl/udl/raku.udl` → `Raku.lexres`), folding and brace indent |
| File associations | ✅ | `.raku`, `.rakumod`, `.rakutest`, `.rakudoc`, `.nqp`, `.pod6`, `.p6`, `.pm6`, `.t6`; `.t` stays Perl |
| Interpreter discovery | ✅ | PATH, rakubrew and common Perl 6 locations; `rakuDefaultInterpreter` |
| Run | ✅ | `koInvocation;1?type=Raku`, arguments, cwd, env and `RAKULIB` |
| Lint / diagnostics | ✅ | LSP diagnostics, with `raku -c` fallback |
| REPL | ✅ | `raku --repl-mode=process` in the terminal pane |
| Completions | ✅ | LSP (`nimala-raku-lsp`) |
| Symbols in current file | ✅ | LSP document symbols |
| Go to definition | ✅ | LSP |
| Find references | ✅ | LSP, project-wide |
| Calltips / signatures | ✅ | LSP signature help |
| Debugging | ✅ | Launch, breakpoints, stop-on-entry, stepping, stack, variables, evaluate, output |
| Projects / SCC / search | ✅ | Generic IDE features |
| Snippets / templates / icon | ❌ | Not shipped yet |
| Test runner | ❌ | `.rakutest` opens as Raku, but there is no “run test” integration |
| Package manager | ❌ | No `zef` integration |
| Automated GUI tests | ⚠️ | DAP bridge has tests; run/REPL/debug GUI flows are still manual |

## Roadmap

| Priority | Item | Notes |
| --- | --- | --- |
| 1 | Raku snippets, file templates and a language icon | Quick polish win |
| 2 | Test-runner integration for `.rakutest` | Run a file or a `plan`/`done-testing` suite from the IDE |
| 3 | Debugger attach mode, restart and exception filters | Present in the DAP adapter, not exposed in Komodo |
| 4 | Project-wide symbol search and hover popups | LSP supports them; CodeIntel currently exposes document symbols and calltips only |
| 5 | LSP rename, formatting, code actions and semantic tokens | Mediator support exists; UI wiring does not |
| 6 | Lexer refinements | Heredoc terminator heuristic, interpolation inside strings, Unicode metaoperators |
| 7 | Automated GUI tests for run, lint and the REPL | Reuse the headless harness |
| 8 | `zef` package-manager integration | Optional |

## Phase 0 — editor, run, lint, REPL (implemented)

- Native `Raku` language component with file associations:
  `.raku`, `.rakumod`, `.rakutest`, `.rakudoc`, `.nqp`, `.pod6`, `.p6`,
  `.pm6`, `.t6`.  `.t` remains associated with Perl to avoid changing Perl
  test-file behavior.
- `raku`, `rakudo` and `perl6` shebangs, plus the Emacs `raku`/`perl6` modes.
- Syntax highlighting uses the dedicated UDL lexer
  `src/udl/udl/raku.udl`, compiled to `Raku.lexres` by the language build.
  It handles sigils, twigils, comments, POD, strings, regexes, numbers,
  quoting forms and heredoc introducers.
  Known approximations: heredocs end at the first bare-word line, string
  interpolation is painted as part of the string, and some Unicode
  metaoperators are painted as generic operators.
- Raku interpreter preference (`rakuDefaultInterpreter`), discovery through
  PATH and common `rakubrew` / Perl 6 locations, and `RAKULIB` support via
  `rakuExtraPaths`.
- Run support through `koInvocation;1?type=Raku`.
- Background syntax checking uses LSP diagnostics when the language server is
  available and falls back to `raku -c`.
- A Raku REPL started from the Interactive Shell menu.  It runs
  `raku --repl-mode=process` in Komodo's terminal pane rather than through the
  DBGP debugger.

### Phase 0 follow-ups

- Ship a Raku icon and Raku snippets/file templates.
- Add automated GUI tests for run, lint and the REPL.

## Phase 1 — DAP bridge (implemented)

Komodo's debugger is DBGP-based.  Raku's current ecosystem is DAP-based
(`nimala-raku-dap` is a commonly available adapter; `raku-debug` is a CLI
debugger).  Do not make the debugger UI speak a second protocol ad hoc.  Build
a bridge first.

The bridge is `src/dbgp/raku_dap_bridge.py`.  It connects to Komodo's DBGP
listener, starts the configured Raku DAP adapter over stdio, and translates
the debugger operations used by Komodo.  The adapter command and arguments
are configurable with `rakuDapCommand` and `rakuDapArgs`; the default is
`nimala-raku-dap`.

Implemented launch flow: initialize, launch, setBreakpoints,
configurationDone, stop-on-entry, threads, stackTrace, scopes, variables,
evaluate, output, continue, next, stepIn, stepOut, pause and disconnect.
Attach mode is not exposed in the Komodo debugger dialog yet.

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

## Phase 2 — debugger integration (implemented)

- `rakuDapCommand`, `rakuDapArgs` and `rakuStopOnEntry` preferences are in
  the Raku preference page.
- `koRakuInvocation` supports `INVOKE_DEBUG`.
- The debugger uses the normal Komodo breakpoint manager, call stack,
  Locals/Watch panels, Evaluate, step buttons and debug output.
- Verified locally: stop-on-entry, line breakpoints, continue, console
  output, stack frames, variables, child variables and expression evaluation.
- Attach mode, restart and exception-filter controls are not exposed yet.

## Phase 3 — DAP tests and hardening

- Added `src/dbgp/test/test_raku_dap_bridge.py`, an integration test that
  drives a real `nimala-raku-dap` adapter through a mock DBGP listener.
- The test covers init/features, breakpoint set, run/stop, stack, contexts,
  child variables, evaluation and disconnect.
- Adapter failure and missing-adapter paths are handled by the bridge and
  logged to `/tmp/komodo-raku-dap-bridge.log` (or
  `RAKU_DAP_BRIDGE_LOG`).
- Run it with:

  ```sh
  python3 src/dbgp/test/test_raku_dap_bridge.py
  ```

## Phase 4 — LSP / CodeIntel (implemented)

`nimala-raku-lsp` provides completion, document symbols, definitions, references
and signature help.  It is started by CodeIntel when `rakuLspEnabled` is true
(the default), using `rakuLspCommand`/`rakuLspArgs` (default
`nimala-raku-lsp --stdio`).  CodeIntel routes Raku completions, symbols,
go-to-definition, find-references and calltips through the existing LSP
mediator.

Diagnostics are forwarded into Komodo's normal lint view.  Because Komodo's
linters are pull-based and LSP diagnostics are pushed, the Raku linter keeps a
persistent client (`src/lint/raku_lsp_client.py`) and converts
`textDocument/publishDiagnostics` into `koILintResults`.  The
`rakuLspDiagnostics` preference (default on) controls this; when the server is
missing or disabled the linter falls back to `raku -c`.

Current limits:

- Raku has no bundled CIX database; LSP is the preferred route for richer
  CodeIntel support.
- Hover text is shown through the signature-help calltip; there is no
  dedicated editor hover popup.

## Acceptance criteria

- Phase 0: `.raku` files open as Raku, run, syntax-check and start a REPL.
- Phase 1: a Raku DAP adapter can be configured and driven by the bridge.
- Phase 2: Komodo can set breakpoints, step, inspect stacks/variables and
  evaluate expressions in a Raku script.
- Phase 3: the above is covered by repeatable tests and survives adapter
  errors.
- Phase 4: Raku completions and symbols appear through LSP without blocking
  the editor when the server is absent.
