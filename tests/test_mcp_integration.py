"""MCP protocol integration tests — subprocess-based.

Exercises the ``initialize`` handshake plus the two error-result contract
checks via :class:`TestMCPInitialize`, all with a dummy token (no live API
call). Runs on every ``unittest discover``.
"""

import json
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

# Resolve the ``voog-mcp`` console script. ``shutil.which`` finds it on PATH
# (CI's system pip install, or any activated venv). Fallback to the local
# .venv/bin path so tests still work when invoked from an unactivated venv
# but with the project installed in-tree.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VOOG_MCP_BIN = shutil.which("voog-mcp") or str(REPO_ROOT / ".venv" / "bin" / "voog-mcp")


def _readline_with_timeout(stream, timeout: float) -> str:
    """Read a single line from `stream`, returning '' on timeout.

    Avoids hanging the test forever if the server never writes a response.
    """
    q: queue.Queue[str] = queue.Queue()

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
            "VOOG_HOST": "example.voog.com",
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
                    f"Timed out waiting for initialize response from voog-mcp.\nstderr:\n{stderr}"
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

    def test_tool_validation_error_returns_iserror_true(self):
        # Verifies that error_response surfaces as CallToolResult(isError=True)
        # over the wire. We pick page_delete with force omitted: the tool's
        # own validation refuses (so this exercises voog_mcp.errors.error_response,
        # not the SDK's input-validation path) and returns without making any
        # HTTP call — the dummy token is sufficient. Without isError=True a
        # client cannot distinguish a tool-level failure from a successful
        # response (per spec § 7).
        #
        # The new multi-site server loads config from a voog.json file
        # (pointed to via $VOOG_CONFIG). We write a minimal temp config with
        # one site named "test" and supply its API token in the env. The
        # tool call also includes site="test" so dispatch reaches the tool's
        # own force=true check rather than the missing-site guard.
        tmpdir_obj = tempfile.TemporaryDirectory()
        tmpdir = tmpdir_obj.name
        cfg_path = pathlib.Path(tmpdir) / "voog.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "sites": {
                        "test": {
                            "host": "example.voog.com",
                            "api_key_env": "TEST_VOOG_API_KEY",
                        }
                    },
                    "default_site": "test",
                }
            )
        )
        env = {
            **os.environ,
            "VOOG_CONFIG": str(cfg_path),
            "TEST_VOOG_API_KEY": "dummy",
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
            assert proc.stdin is not None
            assert proc.stdout is not None

            # 1. initialize handshake
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0.1"},
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.flush()
            init_line = _readline_with_timeout(proc.stdout, timeout=10.0)
            self.assertTrue(init_line, "no initialize response")
            init = json.loads(init_line)
            self.assertIn("result", init, f"initialize failed: {init}")

            # 2. MCP requires notifications/initialized before further requests
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                )
                + "\n"
            )
            proc.stdin.flush()

            # 3. Call page_delete without force=true → tool returns error_response.
            #    site="test" is required by the new multi-site server; passing
            #    it lets dispatch reach the tool's own validation (force=true check).
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "page_delete",
                            "arguments": {"page_id": 999, "site": "test"},
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.flush()

            response = None
            for _ in range(5):
                line = _readline_with_timeout(proc.stdout, timeout=10.0)
                if not line:
                    break
                msg = json.loads(line)
                if msg.get("id") == 2:
                    response = msg
                    break

            self.assertIsNotNone(response, "no response for id=2")
            self.assertIn("result", response, f"unexpected envelope: {response}")
            result = response["result"]
            self.assertTrue(
                result.get("isError"),
                f"expected isError=True on tool failure, got: {result}",
            )
            content = result.get("content", [])
            self.assertTrue(content, "isError result missing content")
            payload = json.loads(content[0]["text"])
            self.assertIn("error", payload)
            self.assertIn("force=true", payload["error"])
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
            tmpdir_obj.cleanup()

    def test_sdk_input_validation_returns_iserror_true(self):
        # Complement to test_tool_validation_error_returns_iserror_true:
        # exercises the SDK's *input* validation path, not the tool's own
        # error_response. redirect_add inputSchema declares
        # redirect_type ∈ {301, 302, 307, 410} via JSON Schema enum;
        # passing 999 trips jsonschema.validate inside Server.call_tool
        # before the registered tool ever runs. The SDK turns the
        # ValidationError into _make_error_result("Input validation
        # error: ..."), which is a CallToolResult(isError=True). We
        # rely on this path instead of duplicating the enum check in
        # the tool body — the failing assertion below is the canary
        # that protects that decision.
        env = {
            **os.environ,
            "VOOG_HOST": "example.voog.com",
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
            assert proc.stdin is not None
            assert proc.stdout is not None

            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0.1"},
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.flush()
            init_line = _readline_with_timeout(proc.stdout, timeout=10.0)
            self.assertTrue(init_line, "no initialize response")
            init = json.loads(init_line)
            self.assertIn("result", init, f"initialize failed: {init}")

            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                )
                + "\n"
            )
            proc.stdin.flush()

            # 999 is not in the redirect_type enum — SDK rejects it before
            # the tool body runs. No HTTP call hits Voog.
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "redirect_add",
                            "arguments": {
                                "source": "/x",
                                "destination": "/y",
                                "redirect_type": 999,
                            },
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.flush()

            response = None
            for _ in range(5):
                line = _readline_with_timeout(proc.stdout, timeout=10.0)
                if not line:
                    break
                msg = json.loads(line)
                if msg.get("id") == 2:
                    response = msg
                    break

            self.assertIsNotNone(response, "no response for id=2")
            self.assertIn("result", response, f"unexpected envelope: {response}")
            result = response["result"]
            self.assertTrue(
                result.get("isError"),
                f"expected isError=True on SDK validation failure, got: {result}",
            )
            content = result.get("content", [])
            self.assertTrue(content, "isError result missing content")
            # SDK's _make_error_result emits a single TextContent whose body
            # starts with "Input validation error:" — the marker we key on.
            text = content[0].get("text", "")
            self.assertIn("Input validation error", text)
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass


if __name__ == "__main__":
    unittest.main()
