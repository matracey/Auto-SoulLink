import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.admin_auth import is_admin, setup_admin_auth
from server.templating import setup_templating


async def build_client(monkeypatch, key="", secure=False):
    if key:
        monkeypatch.setenv("SLINK_ADMIN_KEY", key)
    else:
        monkeypatch.delenv("SLINK_ADMIN_KEY", raising=False)
    if secure:
        monkeypatch.setenv("SLINK_ADMIN_COOKIE_SECURE", "true")
    else:
        monkeypatch.delenv("SLINK_ADMIN_COOKIE_SECURE", raising=False)
    app = web.Application()
    setup_templating(app)
    setup_admin_auth(app)
    async def check(request):
        return web.json_response({"admin": await is_admin(request)})

    app.router.add_get("/check", check)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_unconfigured_local_app_treats_requests_as_admin(monkeypatch):
    client = await build_client(monkeypatch)
    try:
        response = await client.get("/check")
        assert await response.json() == {"admin": True}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bearer_key_is_accepted(monkeypatch):
    monkeypatch.setenv("SLINK_ADMIN_KEY", "admin-secret")
    app = web.Application()
    setup_templating(app)
    setup_admin_auth(app)

    async def check(request):
        return web.json_response({"admin": await is_admin(request)})

    app.router.add_get("/check", check)
    async with TestClient(TestServer(app)) as client:
        anonymous = await client.get("/check")
        authorized = await client.get(
            "/check", headers={"Authorization": "Bearer admin-secret"})
        assert await anonymous.json() == {"admin": False}
        assert await authorized.json() == {"admin": True}


@pytest.mark.asyncio
async def test_login_sets_hardened_cookie_without_echoing_key(monkeypatch):
    client = await build_client(monkeypatch, "admin-secret", secure=True)
    try:
        wrong = await client.post(
            "/admin/login",
            data={"admin_key": "wrong", "next": "/new"},
        )
        assert wrong.status == 401
        assert "admin-secret" not in await wrong.text()

        accepted = await client.post(
            "/admin/login",
            data={"admin_key": "admin-secret", "next": "/new"},
            allow_redirects=False,
        )
        cookie = accepted.headers["Set-Cookie"]
        assert accepted.status == 303
        assert accepted.headers["Location"] == "/new"
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie
        assert "admin-secret" not in cookie
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_login_rejects_external_redirect(monkeypatch):
    client = await build_client(monkeypatch, "admin-secret")
    try:
        response = await client.post(
            "/admin/login",
            data={"admin_key": "admin-secret", "next": "//example.com"},
            allow_redirects=False,
        )
        assert response.headers["Location"] == "/"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_plain_http_login_uses_non_secure_cookie_when_not_configured(monkeypatch):
    client = await build_client(monkeypatch, "admin-secret")
    try:
        response = await client.post(
            "/admin/login",
            data={"admin_key": "admin-secret", "next": "/"},
            allow_redirects=False,
        )
        assert "Secure" not in response.headers["Set-Cookie"]
    finally:
        await client.close()
