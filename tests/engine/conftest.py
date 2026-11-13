import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.engine.fakes import FakeElevator, FakeWinreg


class _Server:
    def __init__(self):
        self.routes = {}
        self.last_headers = {}
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.last_headers = dict(self.headers)
                owner.requests.append(self.path)
                status, headers, body = owner.routes.get(self.path, (404, {}, b"not found"))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}{path}"

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join()


@pytest.fixture
def http_server():
    """Local HTTP server. Routes: path -> (status, headers, body). The default
    HTTP/1.0 handler sends no Content-Length unless a route sets one."""
    server = _Server()
    body = b"x" * 300_000
    server.routes = {
        "/a": (200, {"Content-Length": "2"}, b"ok"),
        "/sized": (200, {"Content-Length": str(len(body))}, body),
        "/chunked": (200, {}, body),
        "/r": (302, {"Location": "https://agarchive.net/f.zip"}, b""),
    }
    yield server
    server.close()


@pytest.fixture
def fake_elev():
    return FakeElevator()


@pytest.fixture
def fake_winreg():
    return FakeWinreg()
