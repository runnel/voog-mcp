"""MCP protocol integration tests — subprocess-based.

Test pattern:
1. Spawn `voog-mcp` server subprocess
2. Send initialize JSON-RPC message via stdin
3. Read response from stdout
4. Send tools/list, expect tool definitions
5. Cleanup subprocess
"""
import json
import os
import subprocess
import sys
import unittest


@unittest.skip("Server skeleton not implemented yet — Task 6")
class TestMCPInitialize(unittest.TestCase):
    def test_server_initialize_handshake(self):
        env = {
            **os.environ,
            "VOOG_HOST": "runnel.ee",
            "VOOG_API_TOKEN": os.environ.get("RUNNEL_VOOG_API_KEY", ""),
        }
        proc = subprocess.Popen(
            ["voog-mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        # Send initialize request
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "0.9.0",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
        }
        proc.stdin.write(json.dumps(init_request) + "\n")
        proc.stdin.flush()
        response_line = proc.stdout.readline()
        response = json.loads(response_line)
        self.assertEqual(response["id"], 1)
        self.assertIn("result", response)
        self.assertIn("capabilities", response["result"])
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
