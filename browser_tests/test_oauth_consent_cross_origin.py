"""Zustimmung mit Redirect-URI auf einen anderen Ursprung: echter Seitenwechsel, kein XHR."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page

from browser_tests.live_server import LiveServer
from thermoctl.domain.oauth import register_client, start_request

pytestmark = pytest.mark.browser


class _Callback(HTTPServer):
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []  # (Methode, Pfad mit Query)
        self.arrived = threading.Event()
        super().__init__(("127.0.0.1", 0), _CallbackHandler)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _Callback

    def _answer(self) -> None:
        self.server.requests.append((self.command, self.path))
        if self.command == "GET":
            self.server.arrived.set()
        body = b"<html><body><h1>Callback erreicht</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_OPTIONS = _answer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.mark.parametrize("action", ["allow", "deny"])
def test_consent_navigates_to_foreign_origin(
    admin_page: Page, live_server: LiveServer, action: str
) -> None:
    callback = _Callback()
    thread = threading.Thread(target=callback.serve_forever, daemon=True)
    thread.start()
    try:
        redirect_uri = f"http://127.0.0.1:{callback.server_port}/auth_callback"
        with live_server.session() as session:
            client = register_client(session, "Fremder-Ursprung", [redirect_uri])
            request_id = start_request(
                session,
                client,
                redirect_uri,
                "x" * 43,
                "https://mcp.example",
                "zustand-1",
                "https://mcp.example",
            )
            session.commit()

        admin_page.goto(f"/oauth/consent?request={request_id}")
        admin_page.locator(f'button[name="action"][value="{action}"]').click()
        assert callback.arrived.wait(10), "Der Browser ist nie beim Client angekommen."
        admin_page.wait_for_url(f"{redirect_uri}*")
        gets = [path for method, path in callback.requests if method == "GET"]
        assert [m for m, _ in callback.requests] == ["GET"], callback.requests  # kein Preflight
        query = parse_qs(urlsplit(gets[0]).query)
        assert query["state"] == ["zustand-1"]
        if action == "allow":
            assert len(query["code"]) == 1
        else:
            assert query["error"] == ["access_denied"]
    finally:
        callback.shutdown()
        callback.server_close()
