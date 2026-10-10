"""Small threaded HTTP router shared by the tank node and the hub."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    stream: Callable[[Any], None] | None = None


def json_response(payload: Any, status: int = 200) -> Response:
    return Response(
        status=status,
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json; charset=utf-8",
    )


def bad_request(message: str) -> HttpError:
    return HttpError(400, message)


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern[str], Callable]] = []

    def route(self, method: str, pattern: str) -> Callable:
        compiled = _compile(pattern)

        def decorate(fn: Callable) -> Callable:
            self.routes.append((method.upper(), compiled, fn))
            return fn

        return decorate

    def lookup(self, method: str, path: str):
        normalized = path.rstrip("/") or "/"
        for route_method, compiled, fn in self.routes:
            if route_method != method.upper():
                continue
            match = compiled.match(normalized)
            if match:
                return fn, match.groupdict()
        return None


def _compile(pattern: str) -> re.Pattern[str]:
    if pattern == "/":
        return re.compile(r"^/$")
    parts = []
    for part in pattern.strip("/").split("/"):
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1]
            parts.append(rf"(?P<{name}>[^/]+)")
        else:
            parts.append(re.escape(part))
    return re.compile("^/" + "/".join(parts) + "$")


def query_value(query: dict[str, list[str]], name: str, default: str | None = None) -> str | None:
    values = query.get(name)
    if not values or values[0] == "":
        return default
    return values[0]


def parse_json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise bad_request("invalid json") from exc
    if not isinstance(data, dict):
        raise bad_request("json body must be an object")
    return data


class AppServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    app: Any


class ServerHandle:
    def __init__(self, httpd: AppServer, thread: threading.Thread):
        self.httpd = httpd
        self.thread = thread
        self.port = httpd.server_address[1]
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)


def serve(app: Any, host: str, port: int) -> ServerHandle:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", "0") or 0)
            max_body = int(getattr(self.server.app, "max_body", 8 * 1024 * 1024))
            if length > max_body:
                self._write(json_response({"error": "body too large"}, 413))
                return
            body = self.rfile.read(length) if length else b""
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                response = self.server.app.handle(method, parsed.path, query, body, self.headers)
            except HttpError as exc:
                response = json_response({"error": exc.message}, exc.status)
            except Exception as exc:  # last-resort so one bad request cannot kill the server
                logger = getattr(self.server.app, "log_exception", None)
                if logger:
                    logger(exc)
                response = json_response({"error": "internal error"}, 500)
            self._write(response)

        def _write(self, response: Response) -> None:
            try:
                self.send_response(response.status)
                self.send_header("X-Content-Type-Options", "nosniff")
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", response.content_type)
                if response.stream is None:
                    self.send_header("Content-Length", str(len(response.body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(response.body)
                    return
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                response.stream(self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                return

    httpd = AppServer((host, port), Handler)
    httpd.app = app
    thread = threading.Thread(target=httpd.serve_forever, name=f"http-{port}", daemon=True)
    thread.start()
    return ServerHandle(httpd, thread)
