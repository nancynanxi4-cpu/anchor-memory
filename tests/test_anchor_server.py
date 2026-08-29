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
