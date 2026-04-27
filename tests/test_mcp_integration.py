"""MCP protocol integration tests — subprocess-based.

Test pattern:
1. Spawn `voog-mcp` server subprocess
2. Send initialize JSON-RPC message via stdin
3. Read response from stdout
4. Send initialized notification + tools/list, expect tool definitions
5. Cleanup subprocess
"""
import json
import os
import pathlib
import queue
import subprocess
import sys
import threading
import unittest


# Use the absolute path to the venv-installed console script so the test
# does not depend on whichever shell PATH the test runner inherits.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VOOG_MCP_BIN = REPO_ROOT / ".venv" / "bin" / "voog-mcp"


def _readline_with_timeout(stream, timeout: float) -> str:
    """Read a single line from `stream`, returning '' on timeout.

    Avoids hanging the test forever if the server never writes a response.
    """
    q: "queue.Queue[str]" = queue.Queue()

    def _reader():
        try:
            line = stream.readline()
        except Exception as exc:  # pragma: no cover - defensive
            q.put(f"__ERR__:{exc}")
            return
        q.put(line)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return ""


class TestMCPInitialize(unittest.TestCase):
    def test_server_initialize_handshake(self):
        env = {
            **os.environ,
            "VOOG_HOST": "runnel.ee",
            # Task 6 only checks the initialize handshake — no API call is
            # made. A dummy token is sufficient to satisfy load_config().
            "VOOG_API_TOKEN": "dummy",
        }
        proc = subprocess.Popen(
            [str(VOOG_MCP_BIN)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        try:
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1"},
                },
            }
            assert proc.stdin is not None
            assert proc.stdout is not None
            proc.stdin.write(json.dumps(init_request) + "\n")
            proc.stdin.flush()

            response_line = _readline_with_timeout(proc.stdout, timeout=10.0)
            if not response_line:
                # Surface any server-side stderr to help diagnose hangs.
                try:
                    proc.terminate()
                    _, stderr = proc.communicate(timeout=5)
                except Exception:
                    stderr = ""
                self.fail(
                    "Timed out waiting for initialize response from voog-mcp.\n"
                    f"stderr:\n{stderr}"
                )

            response = json.loads(response_line)
            self.assertEqual(response["id"], 1)
            self.assertIn("result", response)
            self.assertIn("capabilities", response["result"])
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            # If anything went wrong, dump stderr so failures are debuggable.
            stderr_text = ""
            if proc.stderr is not None:
                try:
                    stderr_text = proc.stderr.read()
                except Exception:
                    stderr_text = ""
            if proc.returncode not in (0, -15) and stderr_text:
                sys.stderr.write(f"voog-mcp stderr:\n{stderr_text}\n")
            # Explicitly close the pipes so unittest does not flag
            # ResourceWarnings about unclosed TextIOWrapper handles.
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass


if __name__ == "__main__":
    unittest.main()
