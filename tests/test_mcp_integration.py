"""MCP protocol integration tests — subprocess-based.

Two flavours of tests live here:

1. **Always-on handshake test** — :class:`TestMCPInitialize` exercises the
   ``initialize`` handshake with a dummy token (no API call). Runs on every
   ``unittest discover``.

2. **Live-API smoke tests** — :class:`TestMCPSmokeTools` and
   :class:`TestMCPSmokeResources` are gated behind ``RUN_SMOKE=1``. They
   spawn ``voog-mcp`` against ``runnel.ee`` with a real API token (read
   from ``Claude/.env``'s ``RUNNEL_VOOG_API_KEY``) and exercise one
   representative read-only tool or resource per group. Without the
   ``RUN_SMOKE`` env var the whole class skips, so the regular
   ``unittest discover`` run on CI / dev boxes does not require
   credentials and does not hit the live site.

Why opt-in: live-API tests need credentials, hit production runnel.ee, and
add ~10s of network latency to the suite. They verify the end-to-end MCP
contract (subprocess + JSON-RPC framing + tool dispatch + Voog API + result
shape) which mocks cannot reproduce. Mutating tools (``page_set_hidden``,
``layout_rename``, ``redirect_add``, ...) are deliberately NOT exercised
here — the per-tool unit tests cover those with mocked clients, since
running them against the live site would be destructive.

Test pattern (smoke tests):
1. Spawn ``voog-mcp`` server subprocess with live ``VOOG_HOST`` /
   ``VOOG_API_TOKEN``.
2. Send ``initialize`` request, read response.
3. Send ``notifications/initialized`` (no id, no response — required by
   the MCP protocol before further traffic).
4. Send ``tools/list`` or ``resources/list``, assert expected names.
5. Call one read-only tool / read one resource, assert the result shape.
6. Cleanup subprocess in ``tearDown``.
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

ENV_FILE = pathlib.Path("/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/.env")

SMOKE_HOST = "runnel.ee"


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


def _read_smoke_api_key() -> str | None:
    """Read RUNNEL_VOOG_API_KEY from Claude/.env, or None if unavailable.

    Mirrors the pattern in tests/test_layout_create.py: live-API tests read
    the key from a known dotenv path rather than relying on shell env, so
    the test runner does not need to source it.
    """
    if not ENV_FILE.exists():
        return None
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("RUNNEL_VOOG_API_KEY="):
            return line.split("=", 1)[1].strip()
    return None


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
        env = {
            **os.environ,
            "VOOG_HOST": "runnel.ee",
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
            #    Skip notifications, find the response with id=2.
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "page_delete",
                            "arguments": {"page_id": 999},
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
            "VOOG_HOST": "runnel.ee",
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


# ---------------------------------------------------------------------------
# Smoke-test infrastructure (RUN_SMOKE=1 only)
# ---------------------------------------------------------------------------


SMOKE_REASON = "RUN_SMOKE=1 required (live-API integration test)"


def _smoke_enabled() -> bool:
    return bool(os.environ.get("RUN_SMOKE"))


@unittest.skipUnless(_smoke_enabled(), SMOKE_REASON)
class _LiveMCPSubprocessTestCase(unittest.TestCase):
    """Base class: spawns voog-mcp + does the MCP handshake in setUp.

    Subclasses get ``self.proc`` (the subprocess) and ``self._call_jsonrpc``
    / ``self._send_notification`` helpers. ``tearDown`` always terminates
    the process and dumps stderr on non-clean exits.

    Per-test request ids start at 100 (initialize uses 1) and auto-increment
    via ``self._next_id()`` so subclasses do not have to bookkeep ids.
    """

    READ_TIMEOUT = 30.0  # seconds — generous for cold-cache list endpoints

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._api_key = _read_smoke_api_key()
        if cls._api_key is None:
            raise unittest.SkipTest(f"RUNNEL_VOOG_API_KEY not found in {ENV_FILE}")

    def setUp(self):
        env = {
            **os.environ,
            "VOOG_HOST": SMOKE_HOST,
            "VOOG_API_TOKEN": self._api_key,
        }
        self.proc = subprocess.Popen(
            [str(VOOG_MCP_BIN)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        self._id_counter = 100
        try:
            self._handshake()
        except Exception:
            self._terminate()
            raise

    def tearDown(self):
        self._terminate()

    # ----- internals -----

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    def _write_message(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def _read_response(self, expected_id: int) -> dict:
        """Read a single JSON-RPC response line, asserting on timeout.

        The MCP server emits responses as one JSON object per stdout line.
        Notifications can interleave, so we skip lines without an ``id``
        until we find the matching response.
        """
        assert self.proc.stdout is not None
        deadline_iters = 5  # cap how many notifications we'll skip past
        for _ in range(deadline_iters):
            line = _readline_with_timeout(self.proc.stdout, self.READ_TIMEOUT)
            if not line:
                stderr = self._drain_stderr()
                self.fail(f"Timed out waiting for response id={expected_id}.\nstderr:\n{stderr}")
            msg = json.loads(line)
            if msg.get("id") == expected_id:
                return msg
            # Otherwise: server-initiated notification — ignore and keep reading.
        self.fail(
            f"Did not receive response id={expected_id} after "
            f"{deadline_iters} non-matching messages"
        )

    def _call_jsonrpc(self, method: str, params: dict | None = None) -> dict:
        """Send a request, return the parsed response (asserts no error).

        Returns the full envelope so callers can inspect ``result``.
        """
        request_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        self._write_message(payload)
        response = self._read_response(request_id)
        if "error" in response:
            self.fail(f"JSON-RPC error from {method}: {response['error']}\nparams={params}")
        self.assertIn("result", response, f"{method} returned no result")
        return response

    def _send_notification(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response)."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self._write_message(payload)

    def _handshake(self) -> None:
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0.1"},
            },
        }
        self._write_message(init_request)
        response = self._read_response(1)
        self.assertIn("result", response, f"initialize failed: {response}")
        self._send_notification("notifications/initialized")

    def _drain_stderr(self) -> str:
        """Read the subprocess's stderr to EOF.

        Terminates the process first — ``stderr.read()`` blocks until EOF,
        which only arrives once the process exits. Without forcing EOF,
        callers in the timeout/diagnostic path (e.g. ``_read_response``
        after a stdout timeout) would hang on a still-running server,
        masking the very failure they are trying to diagnose. Idempotent:
        safe to call again from ``_terminate``.
        """
        if self.proc.stderr is None:
            return ""
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            pass
        try:
            return self.proc.stderr.read() or ""
        except Exception:
            return ""

    def _terminate(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            pass
        # Surface stderr on non-clean exits to aid debugging.
        if self.proc.returncode not in (0, -15):
            stderr = self._drain_stderr()
            if stderr:
                sys.stderr.write(f"voog-mcp stderr:\n{stderr}\n")
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    # ----- shared assertion helpers -----

    @staticmethod
    def _tool_call_text(result: dict) -> str:
        """Extract text payload from a tools/call result envelope.

        MCP shape: ``{"content": [{"type": "text", "text": "..."}], ...}``.
        ``isError`` may be present on failures — we assert it is falsy.
        """
        content = result.get("content")
        assert content, f"tools/call result missing content: {result}"
        return "".join(block.get("text", "") for block in content if block.get("type") == "text")

    @staticmethod
    def _resource_read_payloads(result: dict) -> list[dict]:
        """Extract content blocks from a resources/read result envelope.

        MCP shape: ``{"contents": [{"uri": "...", "mimeType": "...",
        "text": "..."}, ...]}``.
        """
        contents = result.get("contents")
        assert contents, f"resources/read result missing contents: {result}"
        return contents


# ---------------------------------------------------------------------------
# Tools — one read-only call per group (pages, redirects, products + a
# second pages tool to verify dispatch on multiple tools in the same group)
# ---------------------------------------------------------------------------


class TestMCPSmokeTools(_LiveMCPSubprocessTestCase):
    """End-to-end checks for representative read-only tools.

    We list tools first to confirm the registry is exposed, then call one
    tool per group.
    """

    # Subset semantics (assertFalse(missing) below): missing names = test
    # failure, but extra names (e.g. tools added by later phase tasks) are
    # tolerated so this suite does not break the moment a new tool group
    # lands. The list below is the v0.1 baseline.
    EXPECTED_TOOLS = {
        # pages group
        "pages_list",
        "page_get",
        # pages_mutate group (we don't *call* mutating tools, just verify
        # they are listed)
        "page_set_hidden",
        "page_set_layout",
        "page_delete",
        # layouts group
        "layout_create",
        "layout_rename",
        "asset_replace",
        # products group
        "products_list",
        "product_get",
        "product_update",
        # redirects group
        "redirects_list",
        "redirect_add",
        # snapshot group
        "site_snapshot",
        "pages_snapshot",
    }

    def test_tools_list_exposes_expected_tools(self):
        result = self._call_jsonrpc("tools/list")["result"]
        names = {t["name"] for t in result.get("tools", [])}
        missing = self.EXPECTED_TOOLS - names
        self.assertFalse(
            missing,
            f"tools/list missing expected tools: {sorted(missing)}",
        )

    def test_pages_list_returns_pages(self):
        result = self._call_jsonrpc(
            "tools/call",
            {"name": "pages_list", "arguments": {}},
        )["result"]
        self.assertFalse(result.get("isError"), f"pages_list errored: {result}")
        text = self._tool_call_text(result)
        # Result body is success_response()'s formatted payload — at minimum
        # the runnel.ee site has a homepage (id, path, title fields).
        self.assertIn("pages", text.lower())
        self.assertIn('"id"', text)
        self.assertIn('"path"', text)

    def test_redirects_list_returns_array(self):
        result = self._call_jsonrpc(
            "tools/call",
            {"name": "redirects_list", "arguments": {}},
        )["result"]
        self.assertFalse(result.get("isError"), f"redirects_list errored: {result}")
        text = self._tool_call_text(result)
        # Even a 0-rule site responds with the summary line "↪️  N redirect rules".
        self.assertIn("redirect", text.lower())

    def test_products_list_returns_array(self):
        result = self._call_jsonrpc(
            "tools/call",
            {"name": "products_list", "arguments": {}},
        )["result"]
        self.assertFalse(result.get("isError"), f"products_list errored: {result}")
        text = self._tool_call_text(result)
        # Empty stores still respond — assert the call succeeded with a
        # JSON body (not an error_response, which would carry a Voog API
        # message and fail the isError check above).
        self.assertTrue(text, "products_list returned empty body")


# ---------------------------------------------------------------------------
# Resources — one read per group; voog://layouts/{id} additionally exercises
# the templated-URI dispatch by picking the smallest layout id seen in the
# voog://layouts response.
# ---------------------------------------------------------------------------


class TestMCPSmokeResources(_LiveMCPSubprocessTestCase):
    # Subset semantics — see TestMCPSmokeTools.EXPECTED_TOOLS for rationale.
    EXPECTED_RESOURCE_URIS = {
        "voog://articles",
        "voog://layouts",
        "voog://pages",
        "voog://products",
        "voog://redirects",
    }

    def test_resources_list_exposes_expected_uris(self):
        result = self._call_jsonrpc("resources/list")["result"]
        uris = {r["uri"] for r in result.get("resources", [])}
        missing = self.EXPECTED_RESOURCE_URIS - uris
        self.assertFalse(
            missing,
            f"resources/list missing expected URIs: {sorted(missing)}",
        )

    def test_voog_pages_returns_simplified_pages(self):
        result = self._call_jsonrpc(
            "resources/read",
            {"uri": "voog://pages"},
        )["result"]
        contents = self._resource_read_payloads(result)
        block = contents[0]
        self.assertEqual(block.get("uri"), "voog://pages")
        pages = json.loads(block["text"])
        self.assertIsInstance(pages, list)
        # runnel.ee has at least the homepage; assert simplified shape.
        self.assertGreater(len(pages), 0, "runnel.ee /pages returned empty list")
        first = pages[0]
        for field in ("id", "path", "title", "hidden", "language_code"):
            self.assertIn(field, first, f"page missing {field}: {first}")

    def test_voog_layouts_returns_layout_metadata(self):
        result = self._call_jsonrpc(
            "resources/read",
            {"uri": "voog://layouts"},
        )["result"]
        contents = self._resource_read_payloads(result)
        block = contents[0]
        layouts = json.loads(block["text"])
        self.assertIsInstance(layouts, list)
        self.assertGreater(len(layouts), 0, "runnel.ee /layouts returned empty")
        # Bodies must be stripped from list view (per resource contract).
        for layout in layouts:
            self.assertNotIn("body", layout, f"body leaked into list: {layout}")
            self.assertIn("id", layout)
            self.assertIn("title", layout)

    def test_voog_layout_by_id_returns_template_text(self):
        # First list to find a real id, then read the body resource.
        listing = self._call_jsonrpc(
            "resources/read",
            {"uri": "voog://layouts"},
        )["result"]
        layouts = json.loads(self._resource_read_payloads(listing)[0]["text"])
        # Pick the smallest id — keeps the test deterministic across runs
        # even as new layouts are added/removed.
        smallest = min(layouts, key=lambda layout: layout["id"])
        layout_uri = f"voog://layouts/{smallest['id']}"

        result = self._call_jsonrpc(
            "resources/read",
            {"uri": layout_uri},
        )["result"]
        block = self._resource_read_payloads(result)[0]
        self.assertEqual(block.get("uri"), layout_uri)
        self.assertEqual(block.get("mimeType"), "text/plain")
        # Body may be empty for a freshly-created layout, but the field
        # MUST be present (text key, even if "").
        self.assertIn("text", block)

    def test_voog_articles_returns_articles_list(self):
        result = self._call_jsonrpc(
            "resources/read",
            {"uri": "voog://articles"},
        )["result"]
        block = self._resource_read_payloads(result)[0]
        articles = json.loads(block["text"])
        self.assertIsInstance(articles, list)
        # Bodies stripped from list view (per articles resource contract).
        for article in articles:
            self.assertNotIn("body", article, f"body leaked: {article}")

    def test_voog_products_returns_products_list(self):
        result = self._call_jsonrpc(
            "resources/read",
            {"uri": "voog://products"},
        )["result"]
        block = self._resource_read_payloads(result)[0]
        products = json.loads(block["text"])
        self.assertIsInstance(products, list)
        # Empty store is fine — the contract is the JSON-array shape.

    def test_voog_redirects_returns_rules_list(self):
        result = self._call_jsonrpc(
            "resources/read",
            {"uri": "voog://redirects"},
        )["result"]
        block = self._resource_read_payloads(result)[0]
        rules = json.loads(block["text"])
        self.assertIsInstance(rules, list)
        # Even a 0-rule site verifies the contract — runnel.ee may have
        # rules or not; we only assert shape.


if __name__ == "__main__":
    unittest.main()
