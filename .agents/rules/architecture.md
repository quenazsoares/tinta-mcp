---
trigger: manual
---

# Architecture and invariants

## Process and runtime boundaries

```
[ AI Assistant / Client ]
           â”‚
           â–¼ (MCP protocol over stdio)
   [ tintamcp server ]  <-- Pure Python, managed by uv (fastmcp)
           â”‚
           â–¼ (writes request JSON to user IPC folder)
   [ gdbus / session bus ]
           â”‚
           â–¼ (Action activation: org.tinta.mcp)
   [ Inkscape process ]
           â”‚
           â–¼ (runs extension/tinta_mcp.py via internal inkex)
   [ Open SVG document ]
```

- Server process (`tintamcp`): Orchestrates communication, tool definitions, schemas, and parameter validation. Does not touch SVG directly.
- Extension process (`extension`): Inkscape executes this inside its GTK event loop. It stages DOM mutations, serializes the SVG, then publishes the response JSON.

## IPC protocol invariants

- The D-Bus action has no parameters. One process lock protects the shared request slot across server processes; a per-connection thread lock also serializes threads.
- The server atomically publishes `mcp_params.json` and `pending_request.json`; the extension claims the slot by exclusively creating `mcp_processing.json` before it reads the request.
- Request format:
  ```json
  {
    "protocol_version": 1,
    "session_id": "32 lowercase hexadecimal characters",
    "request_id": "32 lowercase hexadecimal characters",
    "request": {
      "operation": "create-element" | "modify-elements" | "get-info" | "...",
      "attributes": { ... }
    }
  }
  ```
- Response path is derived from `request_id` as `mcp_response_<request_id>.json`; the request cannot choose a path. Responses repeat the version and both identifiers. The extension writes a temporary sibling and atomically renames it into place.
- The extension stages document mutations on a deep copy and publishes an error without using that copy if a handler fails. `save_raw()` serializes the SVG before it writes the response.
- Legacy requests with only a top-level `tag`, including `execute-code`, are rejected. There is no arbitrary Python or textual-command execution.
- Server and extension must be deployed together. Nesting the operation means a phase 1 extension sees no top-level operation and cannot execute a stale-format mutation. The server sends protocol version 1 and rejects missing or mismatched response identifiers, including the operation name.
- The server polls the derived response path for 5 seconds after D-Bus activation. Timeout, malformed response, or D-Bus failure means the outcome is unknown, not cancelled. The pending request remains on disk; later calls first reconcile its response and refuse to overwrite unresolved files.
- IPC uses `$XDG_RUNTIME_DIR/tintamcp` only when its owner and permissions are private, otherwise `/tmp/tintamcp-<uid>` with mode 0700. Windows uses the current user's local application data temp directory. Do not add global-temp fallbacks or caller-supplied IPC paths.

## Tool pipeline design

- Prefer atomic operations where possible.
- The `create_elements` tool handles creation, canvas resizing, viewport fitting (`fit_canvas`), selection highlighting (`select_created`), and preview rendering (`return_preview`) in a single round-trip.
- Avoid forcing the LLM to make multiple serial round-trips for common design workflows.

## Native GTK export actions

- Canvas preview exports use Inkscape GTK actions (`export-type`, `export-filename`, `export-overwrite`, `export-area-page`, `export-do`) triggered via `gdbus`.
- Never spawn a secondary headless Inkscape process to render previews: native actions take ~0.5s versus ~3.0s for CLI spawns.
- Every native preview action must be prepared successfully before `export-do`. Use a unique IPC destination, quote GVariant strings, cap the largest PNG dimension, and validate the complete PNG before returning it. A preview failure after an edit is a warning alongside the applied edit result.
- A successful D-Bus transport call is not an operation result. Extension errors must become MCP tool errors; inspection returns all reported metadata and selected elements without silent truncation.
