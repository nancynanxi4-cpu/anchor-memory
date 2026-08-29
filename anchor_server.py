"""Combined Web Dashboard and MCP server for container deployments.

Both protocol adapters share one AnchorMemory instance so the local Chroma
persistence directory is opened by only one Python process.
"""

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
