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
