"""A tiny in-memory S3 server for tests: PUT (with If-None-Match), GET and HEAD on path-style keys.

It checks every request's Signature V4 against the same credentials and the body hash, so the
R2 adapter is exercised end to end over real HTTP without any cloud account.
"""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pixelrelay.storage import sigv4_headers

ACCESS_KEY = "test-access-key"
SECRET_KEY = "test-secret-key"


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail_next: int = 0  # answer this many requests with 503 first
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _authorised(self, body: bytes) -> bool:
                auth = self.headers.get("authorization", "")
                if not auth.startswith("AWS4-HMAC-SHA256 "):
                    return False
                parts = dict(p.strip().split("=", 1) for p in auth[len("AWS4-HMAC-SHA256 ") :].split(","))
                names = parts["SignedHeaders"].split(";")
                payload_hash = self.headers.get("x-amz-content-sha256", "")
                if payload_hash != hashlib.sha256(body).hexdigest():
                    return False
                extra = {n: self.headers[n] for n in names if n not in ("host", "x-amz-date", "x-amz-content-sha256")}
                expected = sigv4_headers(
                    method=self.command,
                    host=self.headers["host"],
                    path=self.path,
                    query="",
                    headers=extra,
                    payload_hash=payload_hash,
                    access_key=ACCESS_KEY,
                    secret_key=SECRET_KEY,
                    region="auto",
                    amz_date=self.headers["x-amz-date"],
                )
                return expected["authorization"] == auth

            def _answer(self, status: int, body: bytes = b"") -> None:
                self.send_response(status)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _handle(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                fake.requests.append((self.command, self.path))
                if fake.fail_next:
                    fake.fail_next -= 1
                    return self._answer(503)
                if not self._authorised(body):
                    return self._answer(403, b"SignatureDoesNotMatch")
                key = self.path
                if self.command == "PUT":
                    if self.headers.get("if-none-match") == "*" and key in fake.objects:
                        return self._answer(412)
                    fake.objects[key] = body
                    return self._answer(200)
                if key not in fake.objects:
                    return self._answer(404)
                return self._answer(200, fake.objects[key])

            do_GET = do_HEAD = do_PUT = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
