# Single-Process Web and MCP Design

## Goal

Run the Web Dashboard on port 8000 and the MCP HTTP server on port 8001 in one Zeabur service without opening the same local Chroma persistence directory from two Python processes.

## Context

The current `ANCHOR_MODE=both` branch starts `anchor_web.py` in the background and `anchor_mcp.py` as the container's main process. Each process independently constructs `AnchorMemory`, so each opens `/app/anchor_data/chroma` through a separate Chroma `PersistentClient`. This creates multi-process locking and persistence risks. It also makes startup failures misleading: one endpoint can be healthy while the other process is blocked.

The repaired deployment must retain the existing external interface:

- Web Dashboard: `0.0.0.0:8000`
- MCP streamable HTTP: `0.0.0.0:8001`
- One Zeabur service and one persistent volume
- Existing `web` and `mcp` single-service modes remain compatible

## Architecture

Add a focused `anchor_server.py` launcher for `ANCHOR_MODE=both`. It constructs exactly one `AnchorMemory` instance and passes that same object to both protocol adapters:

1. `anchor_web.create_app()` builds the Flask application around the shared instance.
2. `anchor_mcp.create_mcp_server()` registers MCP tools around the same instance.
3. Waitress serves Flask from a background thread on port 8000.
4. FastMCP runs streamable HTTP on the main thread on port 8001.

FastMCP remains on the main thread so its server and signal handling continue to work normally and Kubernetes tracks the process that owns the MCP endpoint. The Web server is created before its thread starts so bind failures surface synchronously rather than disappearing in a background thread.

## Component Changes

### `anchor_web.py`

Change `create_app` to accept an optional pre-built `AnchorMemory`:

```python
def create_app(
    db_path: str,
    secret_key: str | None = None,
    mem: AnchorMemory | None = None,
) -> Flask:
```

When `mem` is absent, the function preserves current behavior by creating its own instance. When supplied by the combined launcher, it uses the supplied instance and must not construct another one.

### `anchor_server.py`

The combined launcher owns lifecycle and wiring:

- Parse database path, Web host/port, MCP host/port, and authentication token.
- Initialize logging before service construction.
- Create the shared `AnchorMemory` exactly once.
- Create the Flask app and Waitress server with the shared instance.
- Create the FastMCP server with the same instance.
- Start the existing daily dream loop once, associated with the MCP service.
- Start Waitress in a daemon thread.
- Run FastMCP streamable HTTP in the main thread.
- Close the Waitress server in `finally` when FastMCP stops.

The launcher emits explicit startup logs for the shared instance and both listening addresses.

### `entrypoint.sh`

Keep the `web` and `mcp` branches unchanged. Replace the `both` branch's two Python processes with one `exec python anchor_server.py ...` command.

## Data Flow and Concurrency

Both adapters call the same in-memory `AnchorMemory` object and therefore the same Chroma `PersistentClient`. Flask/Waitress and FastMCP may invoke methods from different threads, but they no longer create competing local Chroma systems, run migrations concurrently, or open the same persistence directory from separate processes.

SQLite operations continue using the existing short-lived connection pattern in `AnchorDB`. The combined launcher does not add another dream loop or background consolidation worker.

## Error Handling and Shutdown

- A failure to initialize the embedding provider, Chroma, SQLite, Flask, Waitress, or FastMCP aborts startup with a non-zero exit instead of leaving a partially healthy container.
- Waitress binds before its worker thread starts, so port 8000 conflicts fail synchronously.
- FastMCP stays on the main thread.
- The launcher closes Waitress in a `finally` block when MCP exits.
- The launcher never deletes, renames, or automatically rebuilds Chroma data. Recovery from corrupted persistence remains an explicit, backed-up maintenance operation.

## Testing

Use the standard-library `unittest` framework so the change does not require a new runtime test dependency.

Regression coverage must prove:

1. Passing `mem` to `anchor_web.create_app` does not instantiate `AnchorMemory` again.
2. The combined service factory constructs `AnchorMemory` once and passes the identical object to Web and MCP.
3. The combined runtime starts Waitress and FastMCP and closes Waitress when MCP returns or raises.
4. The `both` entrypoint invokes only `anchor_server.py` and no longer launches separate Web and MCP Python processes.
5. Existing standalone `web` and `mcp` entrypoint commands remain unchanged.

Container-level verification must start `ANCHOR_MODE=both` with a temporary data directory, confirm Web `/health` responds on 8000, and confirm the MCP endpoint accepts a connection on 8001.

## Deployment

After tests and local container verification pass:

1. Deploy the updated image to the existing Zeabur service.
2. Set `ANCHOR_MODE=both`.
3. Keep the persistent volume mounted at `/app/anchor_data`.
4. Bind the Dashboard domain to port 8000 and the MCP domain to port 8001.
5. Verify logs contain both listening messages and the Pod remains at zero restarts.

No data migration is required because the on-disk SQLite and rebuilt Chroma data formats remain unchanged.
