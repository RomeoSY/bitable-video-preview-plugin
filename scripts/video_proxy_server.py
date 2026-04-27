from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

ALLOWED_HOST_SUFFIXES = (
    ".365yg.com",
    ".amemv.com",
    ".byteimg.com",
    ".douyinvod.com",
    ".toutiaoimg.com",
)

PASS_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "Cache-Control",
    "Etag",
    "Last-Modified",
)


def is_allowed_host(hostname: str) -> bool:
    host = hostname.lower()
    return host.endswith(ALLOWED_HOST_SUFFIXES) or host in {"v9-default.365yg.com"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,HEAD,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "range,content-type")
        self.send_header("Access-Control-Max-Age", "3600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._proxy_request(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy_request(send_body=False)

    def _proxy_request(self, send_body: bool) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            if send_body:
                self.wfile.write(b"ok")
            return

        if parsed.path != "/video-proxy":
            self.send_error(404, "Not Found")
            return

        query = parse_qs(parsed.query)
        target = (query.get("url") or [""])[0]
        target_parsed = urlparse(target)
        if target_parsed.scheme not in {"http", "https"} or not target_parsed.hostname:
            self.send_error(400, "Invalid url")
            return
        if not is_allowed_host(target_parsed.hostname):
            self.send_error(403, "Host not allowed")
            return

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": self.headers.get(
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            ),
            # Emulate "direct open" style referer to reduce anti-hotlinking rejections.
            "Referer": target,
        }
        if self.headers.get("Range"):
            headers["Range"] = self.headers["Range"]
        if self.headers.get("If-Range"):
            headers["If-Range"] = self.headers["If-Range"]

        method = "GET" if send_body else "HEAD"
        try:
            with requests.request(
                method,
                target,
                headers=headers,
                stream=True,
                timeout=20,
                allow_redirects=True,
            ) as resp:
                self.send_response(resp.status_code)
                for key in PASS_HEADERS:
                    value = resp.headers.get(key)
                    if value:
                        self.send_header(key, value)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "range,content-type")
                self.send_header("Access-Control-Expose-Headers", "Content-Length,Content-Range,Content-Type")
                self.end_headers()
                if send_body:
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            self.wfile.write(chunk)
        except requests.RequestException as exc:
            self.send_error(502, f"Proxy upstream error: {exc.__class__.__name__}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18175)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
