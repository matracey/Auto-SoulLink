from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from tools import inject_full_mocks as injector


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {},
    None,
    {"ok": False},
    {"ok": 1},
    URLError("unavailable"),
    HTTPError("http://127.0.0.1/api/reset", 401, "Unauthorized", {}, None),
    ValueError("invalid JSON"),
])
async def test_reset_failure_prevents_all_fixture_mutations(monkeypatch, capsys, response):
    calls, sent = [], []

    def post(path, body):
        calls.append(path)
        if path == "/api/reset":
            if isinstance(response, Exception):
                raise response
            return response
        return {"ok": True}

    async def send(events):
        sent.extend(events)

    monkeypatch.setattr(injector, "http_post", post)
    monkeypatch.setattr(injector, "send_tcp", send)
    with pytest.raises(SystemExit) as exc:
        await injector.main()
    assert exc.value.code == 1
    assert "reset failed" in capsys.readouterr().err
    assert calls == ["/api/reset"]
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("game", ["gen1", "gen2", "gen3"])
async def test_successful_reset_precedes_fixture_submission(monkeypatch, game):
    calls = []

    def post(path, body):
        calls.append(path)
        return {"ok": True}

    async def send(events):
        assert calls == ["/api/reset", "/api/attempts"]
        assert any(event["event"] == "capture" for event in events)
        calls.append("tcp")

    monkeypatch.setattr(injector, "GAME", game)
    monkeypatch.setattr(injector, "http_post", post)
    monkeypatch.setattr(injector, "send_tcp", send)
    await injector.main()
    assert calls == ["/api/reset", "/api/attempts", "tcp"]


@pytest.mark.parametrize("key", ["", "test-admin-key"])
def test_http_reset_uses_optional_admin_bearer(monkeypatch, key):
    monkeypatch.setenv("SLINK_ADMIN_KEY", key)
    monkeypatch.setattr(injector, "HTTP", "http://127.0.0.1:8080")

    def open_response(request, timeout):
        assert request.full_url == "http://127.0.0.1:8080/api/reset"
        assert request.method == "POST"
        assert request.data == b"{}"
        assert request.get_header("Authorization") == (f"Bearer {key}" if key else None)
        assert "Authorization" not in request.headers
        return BytesIO(b'{"ok":true}')

    monkeypatch.setattr(injector.urllib.request, "urlopen", open_response)
    assert injector.http_post("/api/reset", {}) == {"ok": True}
