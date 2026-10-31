"""Offline Chrome interaction QA using only stdlib CDP/WebSocket, no product server."""

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from alpha_platform.observability.projector import project
from alpha_platform.observability.replay import build_replay_bundle
from alpha_platform.observability.replay_html import render_replay


class CDP:
    def __init__(self, url):
        address = urlsplit(url)
        if address.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("DevTools must be local")
        self.sock = socket.create_connection((address.hostname, address.port), timeout=15)
        self.buffer = b""
        self.counter = 0
        self.events = []
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {address.path} HTTP/1.1\r\nHost: {address.netloc}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        while b"\r\n\r\n" not in self.buffer:
            self.buffer += self.sock.recv(65536)
        headers, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        )
        if not headers.startswith(b"HTTP/1.1 101") or expected.lower() not in headers.lower():
            raise RuntimeError("DevTools WebSocket handshake failed")

    def read(self, size):
        while len(self.buffer) < size:
            chunk = self.sock.recv(max(65536, size - len(self.buffer)))
            if not chunk:
                raise RuntimeError("DevTools connection closed")
            self.buffer += chunk
        value, self.buffer = self.buffer[:size], self.buffer[size:]
        return value

    def send_frame(self, payload, opcode=1):
        size = len(payload)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", size)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", size)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def receive(self):
        parts = []
        while True:
            first, second = self.read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", self.read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8))[0]
            if size > 16 * 1024 * 1024:
                raise RuntimeError("DevTools frame too large")
            mask = self.read(4) if second & 128 else None
            payload = self.read(size)
            if mask:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
            if first & 15 == 8:
                raise RuntimeError("DevTools WebSocket closed")
            if first & 15 == 9:
                self.send_frame(payload, 10)
                continue
            parts.append(payload)
            if first & 128:
                return json.loads(b"".join(parts))

    def call(self, method, params=None):
        self.counter += 1
        self.send_frame(
            json.dumps({"id": self.counter, "method": method, "params": params or {}}).encode()
        )
        while True:
            response = self.receive()
            if response.get("id") == self.counter:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result", {})
            if "method" in response:
                self.events.append(response)

    def evaluate(self, expression):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in result:
            raise AssertionError("Browser JS exception: " + str(result["exceptionDetails"]))
        return result.get("result", {}).get("value")


def wait_until(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError("Browser QA timed out")


def check_page(cdp, path, name, output):
    cdp.call("Page.navigate", {"url": path.resolve().as_uri()})
    wait_until(lambda: cdp.evaluate('typeof window.replayStudio === "object"'))
    data = cdp.evaluate("replayStudio.getBundle()")
    assert cdp.evaluate("document.documentElement.lang") == "zh-CN"
    assert cdp.evaluate("replayStudio.getCursor()") == 0
    initial = cdp.evaluate("replayStudio.getState()")
    assert initial["run"]["state"] == "UNKNOWN"
    assert all(n["state"] == "NOT_OBSERVED_YET" for n in initial["node_states"].values())
    assert not initial["jobs"] and initial["budget"]["used"] is None
    assert cdp.evaluate('document.querySelectorAll(".node.COMPLETED").length') == 0
    if name == "success":
        screenshot(cdp, output / "success-initial.png")
    cdp.evaluate('document.getElementById("next").click()')
    assert cdp.evaluate("replayStudio.getCursor()") == min(1, len(data["frames"]))
    cdp.evaluate('document.getElementById("prev").click()')
    assert cdp.evaluate("replayStudio.getCursor()") == 0
    target = min(3, len(data["frames"]))
    cdp.evaluate(f'document.querySelector("button[data-ordinal=\\"{target}\\"]").click()')
    assert cdp.evaluate("replayStudio.getCursor()") == target
    cdp.evaluate('document.querySelector("button[data-node=validator]").click()')
    assert "表达式校验" in cdp.evaluate('document.getElementById("node-inspector").textContent')
    cdp.evaluate('document.querySelector("#evidence button").click()')
    assert "来源摘要" in cdp.evaluate('document.getElementById("evidence-preview").textContent')
    before_filter = cdp.evaluate("replayStudio.getState()")
    cdp.evaluate('document.getElementById("errors-only").click()')
    assert cdp.evaluate("replayStudio.getState()") == before_filter
    cdp.evaluate('document.getElementById("errors-only").click()')
    cdp.evaluate(
        'const slider=document.getElementById("cursor"); slider.value=2; slider.dispatchEvent(new Event("input",{bubbles:true}));'
    )
    assert cdp.evaluate("replayStudio.getCursor()") == 2
    assert cdp.evaluate("replayStudio.getState()") == project(data["frames"], 2)
    cdp.evaluate(
        'const speed=document.getElementById("speed"); speed.value="4"; speed.dispatchEvent(new Event("change"));'
    )
    cdp.evaluate('document.getElementById("play").click()')
    wait_until(lambda: cdp.evaluate("replayStudio.getCursor()>2"))
    cdp.evaluate('document.getElementById("play").click()')
    assert not cdp.evaluate("replayStudio.isPlaying()")
    paused = cdp.evaluate("replayStudio.getCursor()")
    time.sleep(0.45)
    assert cdp.evaluate("replayStudio.getCursor()") == paused
    if name == "success":
        cdp.evaluate("window.scrollTo(0,450)")
        screenshot(cdp, output / "success-paused.png")
        cdp.evaluate("window.scrollTo(0,0)")
    cdp.evaluate('document.getElementById("home").click()')
    error_frames = [
        f["ordinal"]
        for f in data["frames"]
        if f["event_kind"]
        in {
            "FAILED",
            "UNKNOWN",
            "REJECTED",
            "DUPLICATE",
            "BUDGET_EXHAUSTED",
            "RATE_LIMIT",
            "NODE_FAILED",
        }
    ]
    if error_frames:
        cdp.evaluate('document.getElementById("anomaly-next").click()')
        assert cdp.evaluate("replayStudio.getCursor()") == error_frames[0]
    cdp.evaluate('document.getElementById("end").click()')
    assert cdp.evaluate("replayStudio.getState()") == project(data["frames"], len(data["frames"]))
    end_state = cdp.evaluate("replayStudio.getState()")
    if name == "unknown":
        assert end_state["budget"]["reserved"] == 1 and end_state["budget"]["used"] == 0
        assert all(j.get("remote_id_present") is None for j in end_state["jobs"].values())
    if name == "persistence-failure":
        assert end_state["run"]["state"] == "FAILED"
        assert any(j["state"] == "POLLING" for j in end_state["jobs"].values())
    screenshot(cdp, output / f"{name}-final.png")
    # Jumping backwards and forward must be equivalent at every frame, not just at the end.
    for ordinal in range(len(data["frames"]) + 1):
        cdp.evaluate(f"replayStudio.seek({ordinal})")
        assert cdp.evaluate("replayStudio.getState()") == project(data["frames"], ordinal)
    if name == "success":
        cdp.evaluate('document.getElementById("home").click(); document.activeElement.blur();')
        cdp.call(
            "Input.dispatchKeyEvent", {"type": "keyDown", "key": "ArrowRight", "code": "ArrowRight"}
        )
        cdp.call(
            "Input.dispatchKeyEvent", {"type": "keyUp", "key": "ArrowRight", "code": "ArrowRight"}
        )
        assert cdp.evaluate("replayStudio.getCursor()") == 1
        cdp.evaluate('document.getElementById("end").click()')
        cdp.evaluate('document.getElementById("play").click()')  # explicit replay at the end
        wait_until(lambda: cdp.evaluate("!replayStudio.isPlaying()"), timeout=30)
        assert cdp.evaluate("replayStudio.getCursor()") == len(data["frames"])
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        screenshot(cdp, output / "success-mobile.png")
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1050, "deviceScaleFactor": 1, "mobile": False},
        )
    return {
        "case": name,
        "frames": len(data["frames"]),
        "checks": [
            "initial-history-isolation",
            "step-forward-back",
            "event-click",
            "node-inspector",
            "evidence-inspector",
            "filter-does-not-project",
            "slider",
            "speed-play-pause",
            "anomaly-navigation",
            "every-frame-python-js-parity",
            "last-frame",
        ],
        "result": "PASS",
    }


def screenshot(cdp, output):
    result = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    output.write_bytes(base64.b64decode(result["data"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("examples/observatory"))
    parser.add_argument("--output", type=Path, default=Path("examples/replay-browser-qa"))
    parser.add_argument(
        "--chrome",
        type=Path,
        default=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    profile = Path(".observatory-browser") / ("replay-qa-" + uuid4().hex)
    profile.mkdir(parents=True)
    command = [
        str(args.chrome),
        "--headless",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--no-first-run",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--user-data-dir=" + str(profile.resolve()),
        "about:blank",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    cdp = None
    try:
        portfile = profile / "DevToolsActivePort"
        wait_until(portfile.exists)
        port = int(portfile.read_text().splitlines()[0])
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
            pages = json.load(response)
        page = next(p for p in pages if p["type"] == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        cdp.call("Network.setBlockedURLs", {"urls": ["http://*", "https://*", "ws://*", "wss://*"]})
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1050, "deviceScaleFactor": 1, "mobile": False},
        )
        manifest = json.loads((args.directory / "manifest.json").read_text())
        results = []
        for name, item in manifest.items():
            result = check_page(cdp, args.directory / item["replay_html"], name, args.output)
            results.append(result)
            print(name + ": PASS", flush=True)
        malicious = json.loads((args.directory / manifest["success"]["replay_json"]).read_text())
        malicious["frames"][0]["payload_summary"]["expression"] = (
            '</script><img src=x onerror="window.__replay_injected=1"><script>window.__replay_injected=1</script>'
        )
        xss = args.output / "xss-fixture.html"
        xss.write_text(render_replay(malicious), encoding="utf-8")
        cdp.call("Page.navigate", {"url": xss.resolve().as_uri()})
        wait_until(lambda: cdp.evaluate('typeof replayStudio === "object"'))
        cdp.evaluate("replayStudio.seek(1)")
        assert cdp.evaluate("typeof window.__replay_injected") == "undefined"
        assert "</script>" in cdp.evaluate('document.getElementById("payload").textContent')
        # These are labelled QA fixtures, not invented research examples.
        empty = json.loads(json.dumps(malicious))
        empty.update(
            frames=[], frame_states=[], warnings=["NO_REPLAY_EVENTS", "SYNTHETIC_QA_FIXTURE"]
        )
        empty_path = args.output / "empty-history-fixture.html"
        empty_path.write_text(render_replay(empty), encoding="utf-8")
        cdp.call("Page.navigate", {"url": empty_path.resolve().as_uri()})
        wait_until(lambda: cdp.evaluate('typeof replayStudio === "object"'))
        assert cdp.evaluate('document.getElementById("play").disabled')
        assert "没有足够的历史事件" in cdp.evaluate("document.body.textContent")
        assert cdp.evaluate('document.querySelectorAll(".node.COMPLETED").length') == 0
        capacity = {
            "run_id": malicious["run_id"],
            "run": malicious["final_snapshot"]["run"],
            "jobs": malicious["final_snapshot"]["jobs"],
            "events": [],
            "evidence": malicious["evidence"],
        }
        for index in range(2000):
            ref = f"audit:capacity-fixture:{index}"
            capacity["events"].append(
                {
                    "source_kind": "AUDIT",
                    "sequence": index,
                    "source_ref": ref,
                    "node": "poll",
                    "event_type": "POLL_INTENT",
                    "payload": {},
                    "occurred_at": "2026-10-31T00:00:00.000Z",
                }
            )
            capacity["evidence"][ref] = {"source_kind": "AUDIT", "summary": {"qa_fixture": True}}
        large = build_replay_bundle(capacity, max_events=2000, max_bytes=4194304)
        large["warnings"].append("SYNTHETIC_QA_FIXTURE")
        large_path = args.output / "capacity-2000-fixture.html"
        large_path.write_text(render_replay(large), encoding="utf-8")
        cdp.call("Page.navigate", {"url": large_path.resolve().as_uri()})
        wait_until(lambda: cdp.evaluate('typeof replayStudio === "object"'))
        elapsed = cdp.evaluate(
            "(()=>{const t=performance.now(); replayStudio.seek(2000); return performance.now()-t;})()"
        )
        assert cdp.evaluate("replayStudio.getCursor()") == 2000
        assert cdp.evaluate('document.querySelectorAll("#timeline button").length') <= 100
        assert elapsed < 5000
        external = [
            e
            for e in cdp.events
            if e["method"] == "Network.requestWillBeSent"
            and e["params"]["request"]["url"].startswith(("http:", "https:", "ws:", "wss:"))
        ]
        assert not external
        report = {
            "result": "PASS",
            "cases": results,
            "xss": "PASS",
            "external_requests": 0,
            "dependencies_added": 0,
            "browser": args.chrome.name,
            "empty_history": "PASS",
            "capacity_frames": 2000,
            "capacity_last_seek_ms": round(elapsed, 2),
            "keyboard": "PASS",
        }
        (args.output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Browser interaction and XSS QA: PASS", flush=True)
    finally:
        if cdp is not None:
            try:
                cdp.call("Browser.close")
            except (OSError, RuntimeError):
                pass
            cdp.sock.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
