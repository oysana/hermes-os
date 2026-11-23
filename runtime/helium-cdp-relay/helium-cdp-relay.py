#!/usr/bin/env python3
"""Persistent single-connection CDP relay for Helium.

Helium asks "Allow remote debugging?" on every NEW browser-level WebSocket
connection. Hermes spawns a fresh chrome-devtools-mcp per gateway start, so
each respawn created another prompt and every tool call hung until timeout.

This daemon holds exactly one upstream WebSocket to Helium's debugging port,
for as long as the browser lives. Downstream tool clients connect to this
relay on plain localhost (no prompt), so gateway/MCP restarts are invisible
to Helium. One human approval per Helium launch covers everything after it.

Endpoints on the listen address (default 127.0.0.1:9223):
  GET /json/version  - proxied from Helium, webSocketDebuggerUrl rewritten here
  GET /json/*        - proxied through to Helium's HTTP listener
  GET /healthz       - relay state JSON
  WS  /devtools/...  - downstream CDP client; forwarded over the one upstream

While the upstream handshake is still awaiting the in-browser approval,
downstream CDP requests fail fast with a synthetic -32000 error naming the
pending approval instead of hanging for the full client timeout.
"""
import asyncio
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.asyncio.client import connect
from websockets.datastructures import Headers
from websockets.http11 import Response as WsResponse
from websockets import ConnectionClosed

LISTEN_HOST = os.environ.get("CDP_RELAY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("CDP_RELAY_PORT", "9223"))
PROFILE_DIR = Path(os.environ.get(
    "CDP_RELAY_PROFILE_DIR",
    str(Path.home() / ".config/net.imput.helium")))
ACTIVE_PORT_FILE = PROFILE_DIR / "DevToolsActivePort"
UPSTREAM_RETRY_SECONDS = float(os.environ.get("CDP_RELAY_RETRY", "20"))
HTTP_TIMEOUT = 5
LOG_PREFIX = "helium-cdp-relay"


def log(msg):
    print(f"{LOG_PREFIX} [{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


FIXED_WS_URL = os.environ.get("CDP_RELAY_UPSTREAM_URL")
FIXED_HTTP_BASE = os.environ.get("CDP_RELAY_UPSTREAM_HTTP")


def read_debug_target():
    if FIXED_WS_URL:
        rest = FIXED_WS_URL.split("://", 1)[1]
        hostport, ws_path = rest.split("/", 1)
        return int(hostport.rsplit(":", 1)[1]), "/" + ws_path
    try:
        lines = ACTIVE_PORT_FILE.read_text().splitlines()
        return int(lines[0].strip()), lines[1].strip()
    except (OSError, ValueError, IndexError):
        return None, None


def upstream_http(path, timeout=HTTP_TIMEOUT):
    port, _ = read_debug_target()
    if port is None:
        return 503, b'{"error":"no DevToolsActivePort"}'
    base = FIXED_HTTP_BASE or f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 503, b'{"error":"upstream unreachable"}'


def synthesized_version():
    port, ws_path = read_debug_target()
    if port is None:
        return None
    return json.dumps({
        "Browser": "Helium",
        "Protocol-Version": "1.3",
        "webSocketDebuggerUrl":
            f"ws://{LISTEN_HOST}:{LISTEN_PORT}{ws_path}",
    }).encode()


class Relay:
    def __init__(self):
        self.upstream = None            # open upstream websocket
        self.upstream_state = "disconnected"
        self.upstream_url = None
        self.pending_started = None     # epoch when current connect began
        self.downstream = None          # single active client ws
        self.downstream_since = None
        self.lock = asyncio.Lock()

    async def upstream_loop(self):
        """Keep one browser-level WebSocket to Helium alive for its lifetime."""
        while True:
            port, ws_path = read_debug_target()
            if port is None:
                self.upstream_state = "waiting-browser"
                await asyncio.sleep(10)
                continue
            url = f"ws://127.0.0.1:{port}{ws_path}"
            self.upstream_state = "connecting"
            self.pending_started = time.time()
            self.upstream_url = url
            log(f"connecting upstream {url} (approval may be pending in Helium UI)")
            try:
                async with connect(
                        url, open_timeout=None, ping_interval=None,
                        max_queue=None, close_timeout=10) as ws:
                    self.upstream = ws
                    self.upstream_state = "connected"
                    self.pending_started = None
                    log("upstream connected (approval granted)")
                    await self._pump_upstream_to_downstream(ws)
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                log(f"upstream ended: {type(e).__name__} {e}")
            finally:
                self.upstream = None
                self.upstream_state = "disconnected"
                down = self.downstream
                if down is not None:
                    await down.close(1013, "upstream lost")
                    self.downstream = None
            await asyncio.sleep(UPSTREAM_RETRY_SECONDS)

    async def _pump_upstream_to_downstream(self, ws):
        """Forward upstream frames to the single downstream client."""
        try:
            async for msg in ws:
                down = self.downstream
                if down is not None:
                    try:
                        await down.send(msg)
                    except ConnectionClosed:
                        self.downstream = None
        finally:
            pass

    async def handle_downstream(self, ws):
        """One downstream CDP client at a time; a new one supersedes the old."""
        async with self.lock:
            old = self.downstream
            if old is not None:
                await old.close(1013, "superseded by newer client")
            self.downstream = ws
            self.downstream_since = time.time()
        peer = getattr(ws, "remote_address", None)
        log(f"downstream connected from {peer}")
        try:
            async for msg in ws:
                up = self.upstream
                if up is not None and self.upstream_state == "connected":
                    try:
                        await up.send(msg)
                    except ConnectionClosed:
                        await self._send_pending_error(ws, msg, "upstream lost")
                else:
                    await self._send_pending_error(ws, msg, None)
        except ConnectionClosed:
            pass
        finally:
            async with self.lock:
                if self.downstream is ws:
                    self.downstream = None
            log("downstream disconnected")

    async def _send_pending_error(self, ws, msg, detail):
        try:
            req_id = json.loads(msg).get("id")
        except (ValueError, AttributeError):
            req_id = None
        why = detail or ("helium remote-debugging approval pending - "
                         "click Allow in the Helium window once")
        payload = json.dumps({"id": req_id, "error": {
            "code": -32000, "message": f"cdp-relay: {why}"}})
        try:
            await ws.send(payload)
        except ConnectionClosed:
            pass

    def process_request(self, connection, request):
        """Serve HTTP (json endpoints + healthz); pass WS upgrades through."""
        path = request.path
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None  # websocket: continue to handler
        if path == "/healthz":
            return http_response(200, json.dumps({
                "upstream_state": self.upstream_state,
                "upstream_url": self.upstream_url,
                "pending_seconds": (
                    round(time.time() - self.pending_started)
                    if self.pending_started else None),
                "downstream_connected": self.downstream is not None,
                "downstream_since": self.downstream_since,
                "profile_dir": str(PROFILE_DIR),
            }).encode())
        if path.startswith("/json/"):
            status, body = upstream_http(path)
            if path == "/json/version":
                if status == 200:
                    try:
                        doc = json.loads(body)
                        ws_url = doc.get("webSocketDebuggerUrl")
                        if ws_url:
                            ws_path = "/" + ws_url.split("/", 3)[3]
                            doc["webSocketDebuggerUrl"] = (
                                f"ws://{LISTEN_HOST}:{LISTEN_PORT}{ws_path}")
                            body = json.dumps(doc).encode()
                    except (ValueError, IndexError, KeyError):
                        pass
                else:
                    # Helium disables HTTP discovery; synthesize the discovery
                    # doc from DevToolsActivePort so --browserUrl clients work.
                    body = synthesized_version()
                    status = 200 if body else 503
                    body = body or b'{"error":"no DevToolsActivePort"}'
            return http_response(status, body)
        return http_response(404, b"not found\n", "text/plain")


def http_response(status, body, content_type="application/json"):
    reason = {200: "OK", 404: "Not Found",
              503: "Service Unavailable"}.get(status, "ERROR")
    headers = Headers({"Content-Type": content_type,
                       "Content-Length": str(len(body))})
    return WsResponse(status, reason, headers, body)


async def amain():
    relay = Relay()
    upstream_task = asyncio.create_task(relay.upstream_loop())
    async with serve(relay.handle_downstream, LISTEN_HOST, LISTEN_PORT,
                     process_request=relay.process_request,
                     ping_interval=None):
        log(f"listening on http://{LISTEN_HOST}:{LISTEN_PORT} "
            f"(upstream profile {PROFILE_DIR})")
        await asyncio.Future()  # run forever


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
