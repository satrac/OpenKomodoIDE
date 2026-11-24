# Lua support

Lua support combines Komodo's native lexer and indentation with the Lua
Language Server for CodeIntel and diagnostics.

## Status at a glance

| Area | Status | Notes |
| --- | --- | --- |
| Editing / highlighting | ✅ | Scintilla Lua lexer, folding and keyword-based smart indent |
| File associations | ✅ | `.lua` and `.rockspec` |
| Interpreter discovery | ✅ | `luaDefaultInterpreter`, otherwise `lua`, `lua5.x` or `luajit` from PATH |
| Run | ✅ | `koInvocation;1?type=Lua`, arguments, cwd, env and `LUA_PATH` via `luaExtraPaths` |
| REPL | ✅ | `lua -i` in the interactive shell |
| Lint / diagnostics | ✅ | LuaLS diagnostics, with `luac -p` fallback |
| Completions | ✅ | LuaLS |
| Symbols in current file | ✅ | LuaLS document symbols |
| Go to definition | ✅ | LuaLS |
| Find references | ✅ | LuaLS |
| Calltips / signatures | ✅ | LuaLS signature help |
| Preferences | ✅ | Lua page for interpreter, `LUA_PATH` and LSP settings |
| Debugging | ❌ | No Lua debug adapter yet |
| Snippets / templates / icon | ⚠️ | A Lua template and print snippet exist; no icon or broader pack |
| Package manager | ❌ | No LuaRocks integration |
| Automated tests | ⚠️ | LSP, lint and fallback flows are verified in the headless harness, not yet in `make check` |

## Roadmap

| Priority | Item | Notes |
| --- | --- | --- |
| 1 | Lua debugger | Add a DAP-to-DBGP bridge or a native DAP client; expose launch and attach |
| 2 | LuaRocks integration | Optional package-manager pane for `luarocks` |
| 3 | Luau dialect | Separate association/highlighting and LSP support for `.luau` |
| 4 | Snippets, templates and icon | Expand the existing Lua template and print snippet |
| 5 | Automated GUI tests | Cover run, lint, LSP navigation and the REPL |
| 6 | LSP UI extras | Rename, formatting, code actions, hover popups and project-wide symbols |
| 7 | Shared LSP process | Reuse one LuaLS instance for CodeIntel and diagnostics |

## Implementation notes

- CodeIntel registers Lua with the shared LSP mediator in
  `src/modules/codeintel/content/sdk/codeintel.js` (`setupLuaLsp`) and uses
  `luaLspCommand`/`luaLspArgs` (default `lua-language-server`).
- Diagnostics use the generic client in `src/lint/lsp_client.py` through
  `src/lint/lua_lsp_client.py`.  The linter tries LSP first and falls back to
  `luac -p` when the server is absent or disabled.
- `luaLspDiagnostics` (default on) controls LSP syntax checking.
- Run support is `koLuaInvocation` in
  `src/invocations/koInvokeInterpreters.py`.
- The Lua preference page is `src/chrome/komodo/content/pref/pref-lua.xul`.

### Known limitations

- LuaLS and the diagnostics client currently run as separate server
  instances.
- The debugger is not implemented; launch debugging is the main missing IDE
  feature.
- Project-wide “Go to Symbol”, hover popups, rename and formatting are not
  wired to the LSP mediator yet.
