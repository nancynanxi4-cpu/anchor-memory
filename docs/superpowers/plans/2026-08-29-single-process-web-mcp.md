# Single-Process Web and MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ANCHOR_MODE=both` serve the Web Dashboard on port 8000 and MCP on port 8001 from one Python process and one shared `AnchorMemory` instance.

**Architecture:** Add a combined launcher that owns the only `AnchorMemory`, constructs Waitress and FastMCP around it, runs Waitress in a background thread, and keeps FastMCP in the main thread. Preserve the standalone `web` and `mcp` commands and replace only the `both` entrypoint path.

**Tech Stack:** Python 3.11, Flask 3.1, Waitress 3.0, MCP/FastMCP 1.x, ChromaDB 1.5.9, standard-library `unittest`, Docker.

**Spec:** `docs/superpowers/specs/2026-08-29-single-process-web-mcp-design.md`

## Global Constraints

- Web Dashboard must continue listening on `0.0.0.0:8000`.
- MCP streamable HTTP must continue listening on `0.0.0.0:8001`.
- `ANCHOR_MODE=both` must construct exactly one `AnchorMemory` and one Chroma `PersistentClient`.
- Standalone `ANCHOR_MODE=web` and `ANCHOR_MODE=mcp` behavior must remain compatible.
- The launcher must never delete, rename, or automatically rebuild persisted data.
- The existing daily dream loop must run exactly once in combined mode.
- Tests must use standard-library `unittest`; no new runtime dependency is permitted.

---

### Task 1: Allow the Web App to Consume a Shared Memory Instance

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_anchor_web.py`
- Modify: `anchor_web.py:27-32`

**Interfaces:**
- Consumes: Existing `AnchorMemory` class and Flask `/health` route.
- Produces: `create_app(db_path: str, secret_key: str | None = None, mem: AnchorMemory | None = None) -> Flask`.

- [ ] **Step 1: Write the failing behavioral test**

```python
import tempfile
import unittest

from anchor_web import create_app


class FakeDB:
    def count(self):
        return 7


class FakeMemory:
    db = FakeDB()

    def count(self):
        return 7


class CreateAppTests(unittest.TestCase):
    def test_health_uses_the_supplied_memory_instance(self):
        with tempfile.TemporaryDirectory() as db_path:
            app = create_app(db_path, secret_key="test", mem=FakeMemory())

        response = app.test_client().get("/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "status": "ok",
                "total_memories": 7,
                "index_count": 7,
                "synced": True,
            },
            response.get_json(),
        )


if __name__ == "__main__":
    unittest.main()
```

This test catches the regression where `create_app` ignores or cannot accept the shared object and constructs another persistent client.

- [ ] **Step 2: Run the test and verify RED**

Run in the dependency-equipped Python environment:

```bash
python -m unittest tests.test_anchor_web -v
```

Expected: ERROR with `TypeError: create_app() got an unexpected keyword argument 'mem'`.

- [ ] **Step 3: Implement the minimal shared-memory parameter**

Change the factory to:

```python
def create_app(
    db_path: str,
    secret_key: str | None = None,
    mem: AnchorMemory | None = None,
) -> Flask:
    os.makedirs(db_path, exist_ok=True)
    if mem is None:
        mem = AnchorMemory(db_path=db_path)
```

Do not change route behavior.

- [ ] **Step 4: Run the test and verify GREEN**

```bash
python -m unittest tests.test_anchor_web -v
```

Expected: one test passes.

- [ ] **Step 5: Commit**

```bash
git add anchor_web.py tests/__init__.py tests/test_anchor_web.py
git commit -m "refactor(web): accept a shared memory instance"
```

---

### Task 2: Add the Combined Runtime and Lifecycle

**Files:**
- Create: `anchor_server.py`
- Create: `tests/test_anchor_server.py`

**Interfaces:**
- Consumes: `AnchorMemory`, `anchor_web.create_app`, `anchor_mcp.create_mcp_server`, `anchor_mcp._dream_loop`, and `waitress.create_server`.
- Produces:
  - `CombinedRuntime(memory, web_server, mcp_server, web_host, web_port, mcp_host, mcp_port)` dataclass.
  - `build_runtime(db_path, web_host, web_port, mcp_host, mcp_port, auth_token) -> CombinedRuntime`.
  - `run_runtime(runtime: CombinedRuntime) -> None`.
  - CLI flags `--db-path`, `--web-host`, `--web-port`, `--mcp-host`, `--mcp-port`, and `--auth-token`.

- [ ] **Step 1: Write failing wiring and lifecycle tests**

```python
import threading
import unittest
from unittest.mock import patch

import anchor_server


class FakeWebServer:
    def __init__(self):
        self.started = threading.Event()
        self.closed = False

    def run(self):
        self.started.set()

    def close(self):
        self.closed = True


class FakeMCPServer:
    def __init__(self, web_server):
        self.web_server = web_server
        self.transport = None

    def run(self, transport):
        if not self.web_server.started.wait(timeout=1):
            raise AssertionError("Web server did not start before MCP")
        self.transport = transport


class CombinedRuntimeTests(unittest.TestCase):
    def test_build_runtime_shares_the_only_memory_instance(self):
        memory = object()
        flask_app = object()
        web_server = object()
        mcp_server = object()

        with (
            patch("anchor_server.AnchorMemory", return_value=memory) as memory_factory,
            patch("anchor_server.create_app", return_value=flask_app) as web_factory,
            patch("anchor_server.create_server", return_value=web_server),
            patch("anchor_server.create_mcp_server", return_value=mcp_server) as mcp_factory,
        ):
            runtime = anchor_server.build_runtime(
                db_path="/data",
                web_host="0.0.0.0",
                web_port=8000,
                mcp_host="0.0.0.0",
                mcp_port=8001,
                auth_token="secret",
            )

        self.assertIs(memory, runtime.memory)
        self.assertIs(web_server, runtime.web_server)
        self.assertIs(mcp_server, runtime.mcp_server)
        memory_factory.assert_called_once_with(db_path="/data")
        web_factory.assert_called_once_with("/data", mem=memory)
        mcp_factory.assert_called_once_with(
            memory, host="0.0.0.0", port=8001, auth_token="secret"
        )

    def test_run_runtime_starts_both_services_and_closes_web(self):
        web_server = FakeWebServer()
        mcp_server = FakeMCPServer(web_server)
        runtime = anchor_server.CombinedRuntime(
            object(), web_server, mcp_server, "127.0.0.1", 8000, "127.0.0.1", 8001
        )

        with patch("anchor_server._dream_loop", return_value=None):
            anchor_server.run_runtime(runtime)

        self.assertEqual("streamable-http", mcp_server.transport)
        self.assertTrue(web_server.closed)


if __name__ == "__main__":
    unittest.main()
```

The first test catches a second memory construction or a wrong object passed to either adapter. The second catches wrong startup ordering, wrong MCP transport, and missing Waitress cleanup.

- [ ] **Step 2: Run the tests and verify RED**

```bash
python -m unittest tests.test_anchor_server -v
```

Expected: ERROR because `anchor_server` does not exist.

- [ ] **Step 3: Implement the minimal combined launcher**

Create `anchor_server.py` with this structure:

```python
import argparse
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

from waitress import create_server

from anchor_memory import AnchorMemory
from anchor_mcp import _dream_loop, create_mcp_server
from anchor_web import create_app

log = logging.getLogger("anchor_server")


@dataclass
class CombinedRuntime:
    memory: AnchorMemory
    web_server: Any
    mcp_server: Any
    web_host: str
    web_port: int
    mcp_host: str
    mcp_port: int


def build_runtime(
    db_path: str,
    web_host: str = "0.0.0.0",
    web_port: int = 8000,
    mcp_host: str = "0.0.0.0",
    mcp_port: int = 8001,
    auth_token: str | None = None,
) -> CombinedRuntime:
    os.makedirs(db_path, exist_ok=True)
    memory = AnchorMemory(db_path=db_path)
    log.info("Shared AnchorMemory instance initialized")
    web_app = create_app(db_path, mem=memory)
    web_server = create_server(web_app, host=web_host, port=web_port)
    mcp_server = create_mcp_server(
        memory,
        host=mcp_host,
        port=mcp_port,
        auth_token=auth_token,
    )
    return CombinedRuntime(
        memory,
        web_server,
        mcp_server,
        web_host,
        web_port,
        mcp_host,
        mcp_port,
    )


def run_runtime(runtime: CombinedRuntime) -> None:
    dream_thread = threading.Thread(
        target=_dream_loop,
        args=(runtime.memory,),
        name="anchor-dream",
        daemon=True,
    )
    web_thread = threading.Thread(
        target=runtime.web_server.run,
        name="anchor-web",
        daemon=True,
    )
    dream_thread.start()
    web_thread.start()
    log.info(
        "Web Dashboard listening on %s:%d", runtime.web_host, runtime.web_port
    )
    log.info(
        "MCP Server listening on %s:%d", runtime.mcp_host, runtime.mcp_port
    )
    try:
        runtime.mcp_server.run(transport="streamable-http")
    finally:
        runtime.web_server.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Anchor Memory Web + MCP Server")
    parser.add_argument("--db-path", default="./anchor_data")
    parser.add_argument("--web-host", default="0.0.0.0")
    parser.add_argument("--web-port", type=int, default=8000)
    parser.add_argument("--mcp-host", default="0.0.0.0")
    parser.add_argument("--mcp-port", type=int, default=8001)
    parser.add_argument("--auth-token", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    runtime = build_runtime(
        db_path=args.db_path,
        web_host=args.web_host,
        web_port=args.web_port,
        mcp_host=args.mcp_host,
        mcp_port=args.mcp_port,
        auth_token=args.auth_token or os.environ.get("ANCHOR_API_KEY"),
    )
    run_runtime(runtime)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all unit tests and verify GREEN**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass with no errors.

- [ ] **Step 5: Commit**

```bash
git add anchor_server.py tests/test_anchor_server.py
git commit -m "feat(server): share memory between web and mcp"
```

---

### Task 3: Route `both` Mode Through the Combined Launcher

**Files:**
- Create: `tests/test_entrypoint.py`
- Modify: `entrypoint.sh:13-16`

**Interfaces:**
- Consumes: `anchor_server.py` CLI.
- Produces: `ANCHOR_MODE=both` executes one Python process with `anchor_server.py`.

- [ ] **Step 1: Write a failing entrypoint behavior test**

```python
import os
import pathlib
import stat
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class EntrypointTests(unittest.TestCase):
    def _run_mode(self, mode):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = pathlib.Path(temp_dir)
            log_path = temp / "python.log"
            fake_python = temp / "python"
            fake_python.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" > "$PYTHON_LOG"\n',
                encoding="utf-8",
            )
            fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env["ANCHOR_MODE"] = mode
            env["PYTHON_LOG"] = str(log_path)
            env["PATH"] = f"{temp}{os.pathsep}{env['PATH']}"
            result = subprocess.run(
                ["/bin/sh", str(ROOT / "entrypoint.sh")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result, log_path.read_text(encoding="utf-8").strip()

    def test_both_mode_executes_the_combined_launcher(self):
        result, invocation = self._run_mode("both")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "anchor_server.py --db-path /app/anchor_data "
            "--web-host 0.0.0.0 --web-port 8000 "
            "--mcp-host 0.0.0.0 --mcp-port 8001",
            invocation,
        )

    def test_standalone_modes_keep_their_existing_launchers(self):
        web_result, web_invocation = self._run_mode("web")
        mcp_result, mcp_invocation = self._run_mode("mcp")

        self.assertEqual(0, web_result.returncode, web_result.stderr)
        self.assertEqual(0, mcp_result.returncode, mcp_result.stderr)
        self.assertTrue(web_invocation.startswith("anchor_web.py "))
        self.assertTrue(mcp_invocation.startswith("anchor_mcp.py "))


if __name__ == "__main__":
    unittest.main()
```

This executes the shell entrypoint with a controlled fake Python binary; it does not merely inspect source text.

- [ ] **Step 2: Run the test and verify RED**

```bash
python -m unittest tests.test_entrypoint.EntrypointTests.test_both_mode_executes_the_combined_launcher -v
```

Expected: FAIL because the current `both` branch invokes `anchor_mcp.py` and backgrounds `anchor_web.py`.

- [ ] **Step 3: Implement the one-process `both` command**

Replace the branch with:

```sh
  both)
    exec python anchor_server.py \
      --db-path /app/anchor_data \
      --web-host 0.0.0.0 --web-port 8000 \
      --mcp-host 0.0.0.0 --mcp-port 8001
    ;;
```

- [ ] **Step 4: Run all tests and verify GREEN**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add entrypoint.sh tests/test_entrypoint.py
git commit -m "fix(docker): run web and mcp in one process"
```

---

### Task 4: Correct Deployment Documentation and Perform Container Verification

**Files:**
- Modify: `docs/部署文档.md`

**Interfaces:**
- Consumes: Updated Docker entrypoint and Zeabur two-port service configuration.
- Produces: Deployment instructions that describe one shared process, port 8000 for Dashboard, and port 8001 for MCP.

- [ ] **Step 1: Update deployment documentation**

Replace stale nginx/port-80 descriptions with the implemented runtime:

```text
ANCHOR_MODE=both starts one Python process, one AnchorMemory instance,
Waitress on 8000, and FastMCP on 8001. Bind separate Zeabur domains to
8000 and 8001. Do not run separate web and mcp processes against the same
local Chroma volume.
```

Document the startup messages operators should see and retain the `/app/anchor_data` volume requirement.

- [ ] **Step 2: Run static and unit verification**

```bash
python -m compileall -q anchor_server.py anchor_web.py anchor_mcp.py
python -m unittest discover -s tests -v
git diff --check
```

Expected: every command exits 0; unit output contains no failures or errors.

- [ ] **Step 3: Build the production image**

```bash
docker build --progress=plain -t anchor-memory:single-process .
```

Expected: build exits 0 and installs the pinned dependencies.

- [ ] **Step 4: Start the image in combined mode with temporary storage**

```bash
docker run -d --name anchor-memory-single-process-check \
  -e ANCHOR_MODE=both \
  -p 18000:8000 -p 18001:8001 \
  anchor-memory:single-process
```

Expected: container remains running.

- [ ] **Step 5: Verify both endpoints and logs**

```bash
curl --fail --silent http://127.0.0.1:18000/health
docker exec anchor-memory-single-process-check python -c \
  "import socket; s=socket.create_connection(('127.0.0.1',8001), 3); s.close(); print('MCP_PORT_OPEN')"
docker logs anchor-memory-single-process-check
```

Expected:

- `/health` returns JSON with `status` equal to `ok`.
- The port probe prints `MCP_PORT_OPEN`.
- Logs contain both configured listening addresses and no traceback.

- [ ] **Step 6: Verify the process model**

```bash
docker exec anchor-memory-single-process-check sh -c \
  'for p in /proc/[0-9]*/cmdline; do tr "\\0" " " < "$p"; echo; done'
```

Expected: one application Python command for `anchor_server.py`; no independent `anchor_web.py` or `anchor_mcp.py` process.

- [ ] **Step 7: Remove only the named test container**

```bash
docker rm -f anchor-memory-single-process-check
```

Expected: only the disposable verification container is removed; no volumes are targeted.

- [ ] **Step 8: Commit documentation**

```bash
git add docs/部署文档.md
git commit -m "docs: describe single-process Zeabur deployment"
```

---

## Final Verification

- [ ] Run the complete unit suite fresh:

```bash
python -m unittest discover -s tests -v
```

- [ ] Confirm the complete diff is clean:

```bash
git status --short
git diff --check HEAD~4..HEAD
```
