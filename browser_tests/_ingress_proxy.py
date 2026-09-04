"""A minimal stand-in for Home Assistant's Ingress proxy.

Real Ingress strips its own random path prefix (``/api/hassio_ingress/<token>``)
before forwarding a request to the add-on's container -- the add-on itself never
sees the prefix in an incoming request, only in what it has to *generate* (see
``thermoctl/web/urls.py`` and ``thermoctl.config.Settings.root_path``). A browser
test that wants to prove the interface actually works under that arrangement needs
something in front of the real server doing exactly that stripping -- otherwise a
request to the prefixed address a page renders would 404 against routes that are
only ever registered at their bare paths.

It also adds the one header real Ingress adds and this task's whole change relies
on: ``X-Ingress-Path``, set to `prefix` on every request whose path actually carries
it -- exactly what Home Assistant Core's own Ingress proxy does unconditionally on
every request it forwards (``homeassistant/components/hassio/ingress.py::
_init_header``, both for plain HTTP and the websocket upgrade path). Without it,
``thermoctl.app._ingress_header_prefix`` would treat every request through this
stand-in the same as a direct one and never apply the prefix at all -- which would
make this whole proxy pointless as a stand-in for the real thing. A request that
does *not* carry `prefix` (see the docstring of `start_stripping_proxy` for when that
happens) gets no such header either, for the same reason a real, un-prefixed request
never carries it.

This is deliberately not a general-purpose proxy: no persistent connections, no
streaming, no chunked passthrough (the backend's own response is fully read and
re-sent with a recomputed ``Content-Length``). It only has to survive what
Playwright and a handful of browser tests throw at it.
"""

from __future__ import annotations

import http.client
import http.server
import threading
from urllib.parse import urlparse

_HOP_BY_HOP = {"connection", "transfer-encoding", "content-length", "keep-alive"}


def start_stripping_proxy(prefix: str, backend_base_url: str) -> tuple[http.server.HTTPServer, int]:
    """Starts the proxy on a free local port and returns it, already serving.

    Every request whose path starts with `prefix` is forwarded to `backend_base_url`
    with that prefix removed -- exactly what Home Assistant's Ingress does before
    the request ever reaches the add-on. A request whose path does *not* carry the
    prefix is forwarded unchanged; nothing in this suite relies on that path being
    blocked, and failing closed here would only risk hiding a real assertion behind
    a proxy-shaped false negative.
    """
    backend = urlparse(backend_base_url)
    assert backend.hostname is not None and backend.port is not None

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # one request per connection -- no keep-alive bookkeeping

        def _forward(self) -> None:
            path = self.path
            carries_prefix = path.startswith(prefix)
            backend_path = path[len(prefix) :] if carries_prefix else path
            if not backend_path.startswith("/"):
                backend_path = "/" + backend_path
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_length) if content_length else None

            forward_headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in ({"host"} | _HOP_BY_HOP)
            }
            if carries_prefix:
                # Real Ingress sets this unconditionally -- see the module docstring.
                forward_headers["X-Ingress-Path"] = prefix
            if body is not None:
                forward_headers["Content-Length"] = str(len(body))

            connection = http.client.HTTPConnection(backend.hostname, backend.port, timeout=15)
            try:
                connection.request(self.command, backend_path, body=body, headers=forward_headers)
                response = connection.getresponse()
                response_body = response.read()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() in _HOP_BY_HOP:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)
            finally:
                connection.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = do_PATCH = _forward  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # the pytest capture already has the backend's own request log

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port
