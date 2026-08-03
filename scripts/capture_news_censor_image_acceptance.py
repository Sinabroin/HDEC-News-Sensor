#!/usr/bin/env python3
"""Capture full-list News Censor image acceptance evidence with local Chromium.

The input is an actual generated candidate.  A loopback-only HTTP server makes
its same-origin image paths resolvable; no production or external URL is used.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw

from capture_news_censor_reference_visual import CHROME_CANDIDATES


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


class _WebSocket:
    def __init__(self, url: str):
        parsed = urllib.parse.urlparse(url)
        self.socket = socket.create_connection((parsed.hostname, parsed.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(request.encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(self.socket.recv(4096))
        if not response.startswith(b"HTTP/1.1 101"):
            raise RuntimeError(f"Chromium WebSocket handshake failed: {response[:200]!r}")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.socket.close()

    def _exact(self, count: int) -> bytes:
        output = bytearray()
        while len(output) < count:
            chunk = self.socket.recv(count - len(output))
            if not chunk:
                raise RuntimeError("Chromium WebSocket closed")
            output.extend(chunk)
        return bytes(output)

    def send(self, payload: str, *, opcode: int = 1) -> None:
        raw = payload.encode("utf-8")
        mask = os.urandom(4)
        length = len(raw)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(raw))
        self.socket.sendall(bytes(header) + masked)

    def receive(self) -> str:
        while True:
            first, second = self._exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._exact(8))[0]
            mask = self._exact(4) if second & 0x80 else b""
            payload = self._exact(length)
            if mask:
                payload = bytes(
                    value ^ mask[index % 4] for index, value in enumerate(payload)
                )
            if opcode == 8:
                raise RuntimeError("Chromium WebSocket sent close frame")
            if opcode == 9:
                self.send(payload.decode("latin-1"), opcode=10)
                continue
            if opcode == 1:
                return payload.decode("utf-8")


class _PipeCDP:
    def __init__(self, chrome: Path):
        profile = tempfile.mkdtemp(prefix="hdec-image-acceptance-chrome-")
        self.profile = Path(profile)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = int(probe.getsockname()[1])
        self.process = subprocess.Popen(
            [
                str(chrome),
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-features=Translate,MediaRouter",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--user-data-dir={profile}",
                f"--remote-debugging-port={self.port}",
                "--remote-debugging-address=127.0.0.1",
                "--remote-allow-origins=*",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._next_id = 1
        self.websocket: _WebSocket | None = None
        deadline = time.monotonic() + 10
        version_url = f"http://127.0.0.1:{self.port}/json/version"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(version_url, timeout=1) as response:
                    json.load(response)
                break
            except (OSError, ValueError):
                if self.process.poll() is not None:
                    stderr = self.process.stderr.read().decode("utf-8", "replace")
                    raise RuntimeError(f"Chromium failed to start: {stderr[-1000:]}")
                time.sleep(0.05)
        else:
            raise RuntimeError("Chromium debugging endpoint timed out")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.process.terminate()
            self.process.wait(timeout=5)
        with contextlib.suppress(Exception):
            self.process.kill()
        if self.websocket is not None:
            self.websocket.close()
        shutil.rmtree(self.profile, ignore_errors=True)

    def call(self, method: str, params: dict | None = None) -> dict:
        if self.websocket is None:
            raise RuntimeError("Chromium page target is not open")
        identifier = self._next_id
        self._next_id += 1
        request = {"id": identifier, "method": method, "params": params or {}}
        self.websocket.send(json.dumps(request, separators=(",", ":")))
        while True:
            response = json.loads(self.websocket.receive())
            if response.get("id") != identifier:
                continue
            if response.get("error"):
                raise RuntimeError(f"CDP {method}: {response['error']}")
            return response.get("result") or {}

    def open(self, url: str, *, width: int, height: int, mobile: bool = False) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/json/new?{urllib.parse.quote('about:blank', safe='')}",
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            target = json.load(response)
        self.websocket = _WebSocket(target["webSocketDebuggerUrl"])
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride", {
            "width": width,
            "height": height,
            "deviceScaleFactor": 1,
            "mobile": mobile,
        })
        self.call("Page.navigate", {"url": url})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ready = self.evaluate("document.readyState")
            if ready == "complete":
                return
            time.sleep(0.05)
        raise RuntimeError("candidate page load timed out")

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        return (result.get("result") or {}).get("value")

    def screenshot(self, output: Path, *, clip: dict | None = None) -> None:
        params: dict[str, object] = {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": True,
        }
        if clip:
            params["clip"] = clip
        payload = self.call("Page.captureScreenshot", params)["data"]
        output.write_bytes(base64.b64decode(payload))


def _chrome() -> Path:
    configured = shutil.which("chromium") or shutil.which("google-chrome")
    if configured:
        return Path(configured)
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise RuntimeError("deterministic Chromium binary not found")


def _contact_sheet(browser: _PipeCDP, output: Path) -> dict:
    cards = browser.evaluate("""
      (() => {
        document.querySelectorAll('.lead,.nitem').forEach(card => card.classList.remove('hide'));
        const nodes = [...document.querySelectorAll('.lead .thumb,.nitem .thumb')];
        return nodes.map((node, index) => {
          const rect = node.getBoundingClientRect();
          return {
            position:index + 1,
            local:!node.classList.contains('ph'),
            blank:node.classList.contains('ph')
              ? !node.textContent.trim()
              : getComputedStyle(node).backgroundImage === 'none',
            x:rect.left + window.scrollX,
            y:rect.top + window.scrollY,
            width:rect.width,
            height:rect.height
          };
        });
      })()
    """)
    tile_width, tile_height = 208, 164
    columns = 5
    rows = max(1, (len(cards) + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "white")
    draw = ImageDraw.Draw(sheet)
    local_positions: list[int] = []
    fallback_positions: list[int] = []
    blank_positions: list[int] = []
    card_dimensions: list[list[int]] = []
    for card in cards:
        position = int(card["position"])
        clip = {
            "x": max(0, float(card["x"])),
            "y": max(0, float(card["y"])),
            "width": max(1, float(card["width"])),
            "height": max(1, float(card["height"])),
            "scale": 1,
        }
        temporary = io.BytesIO()
        params = {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": True,
            "clip": clip,
        }
        raw = browser.call("Page.captureScreenshot", params)["data"]
        temporary.write(base64.b64decode(raw))
        tile = Image.open(temporary).convert("RGB")
        tile.thumbnail((192, 128), Image.Resampling.LANCZOS)
        column = (position - 1) % columns
        row = (position - 1) // columns
        x = column * tile_width + 8
        y = row * tile_height + 26
        sheet.paste(tile, (x, y))
        status = "LOCAL" if card["local"] else "FALLBACK"
        draw.text((x, row * tile_height + 7), f"#{position:02d} {status}", fill="#111111")
        (local_positions if card["local"] else fallback_positions).append(position)
        if card["blank"]:
            blank_positions.append(position)
        card_dimensions.append([round(card["width"]), round(card["height"])])
    sheet.save(output)
    return {
        "displayed_articles": len(cards),
        "local_positions": local_positions,
        "fallback_positions": fallback_positions,
        "blank_positions": blank_positions,
        "thumbnail_dimensions_pass": all(
            dimensions == [96, 64] for dimensions in card_dimensions[1:]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    if not candidate.is_file() or candidate.name != "latest.html":
        raise SystemExit("candidate must be a generated latest.html")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hdec-image-acceptance-web-") as raw:
        webroot = Path(raw)
        public = webroot / "HDEC-News-Sensor" / "news-censor"
        public.parent.mkdir(parents=True)
        public.symlink_to(candidate.parent, target_is_directory=True)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            lambda *items, **kwargs: _QuietHandler(*items, directory=str(webroot), **kwargs),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/HDEC-News-Sensor/news-censor/latest.html"
        browser = _PipeCDP(_chrome())
        try:
            browser.open(url, width=1440, height=1200)
            browser.evaluate(
                "document.querySelectorAll('.lead,.nitem').forEach(card => card.classList.remove('hide'))"
            )
            page_audit = browser.evaluate(r"""
              (async () => {
                const modelCount = Object.keys(JSON.parse(
                  document.getElementById('article-data').textContent || '{}'
                )).length;
                const nodes = [...document.querySelectorAll('.lead .thumb,.nitem .thumb')];
                const urls = nodes.map(node => {
                  const match = getComputedStyle(node).backgroundImage.match(/^url\(["']?(.*?)["']?\)$/);
                  return match ? new URL(match[1], location.href).href : '';
                }).filter(Boolean);
                const remote = urls.filter(value => new URL(value).origin !== location.origin);
                const broken = (await Promise.all(urls.map(value => new Promise(resolve => {
                  const image = new Image();
                  image.onload = () => resolve('');
                  image.onerror = () => resolve(value);
                  image.src = value;
                })))).filter(Boolean);
                return {modelCount, domCount:nodes.length, remote, broken};
              })()
            """)
            dimensions = browser.evaluate("({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight})")
            full_height = int(dimensions["height"])
            browser.screenshot(args.output_dir / "desktop-full-page.png", clip={
                "x": 0, "y": 0, "width": 1440, "height": full_height, "scale": 1,
            })
            stops = {
                "top": 0,
                "middle": max(0, int(full_height * 0.34)),
                "lower": max(0, int(full_height * 0.67)),
                "bottom": max(0, full_height - 1200),
            }
            for label, y in stops.items():
                browser.evaluate(f"window.scrollTo(0,{y})")
                browser.screenshot(args.output_dir / f"desktop-{label}.png")
            contact = _contact_sheet(browser, args.output_dir / "all-thumbnails-contact-sheet.png")
        finally:
            browser.close()

        mobile = _PipeCDP(_chrome())
        try:
            mobile.open(url, width=390, height=844, mobile=True)
            mobile.screenshot(args.output_dir / "mobile.png")
        finally:
            mobile.close()
            server.shutdown()
            server.server_close()

    report = {
        "contract": "D7_AK_6E_R4_R5_FULL_IMAGE_BROWSER_ACCEPTANCE_V1",
        **contact,
        "model_articles": page_audit["modelCount"],
        "dom_model_accounting_pass": (
            contact["displayed_articles"] == page_audit["modelCount"]
            == page_audit["domCount"]
        ),
        "broken_local_images": len(page_audit["broken"]),
        "remote_runtime_image_requests": len(page_audit["remote"]),
        "screenshots": sorted(path.name for path in args.output_dir.glob("*.png")),
        "external_network_requests": 0,
        "status": "PASS" if (
            contact["displayed_articles"]
            and contact["displayed_articles"] == page_audit["modelCount"]
            == page_audit["domCount"]
            and not contact["blank_positions"]
            and contact["thumbnail_dimensions_pass"]
            and not page_audit["broken"]
            and not page_audit["remote"]
        ) else "FAIL",
    }
    (args.output_dir / "image-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print(f"NEWS_CENSOR_IMAGE_BROWSER_ACCEPTANCE={report['status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
