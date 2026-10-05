from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import urlencode

import aiohttp_jinja2
from aiohttp import web
from aiohttp_session import get_session, setup
from aiohttp_session.cookie_storage import EncryptedCookieStorage

ADMIN_KEY_ENV = "SLINK_ADMIN_KEY"
ADMIN_COOKIE_SECURE_ENV = "SLINK_ADMIN_COOKIE_SECURE"
ADMIN_ENABLED = web.AppKey("admin_enabled", bool)
_COOKIE_NAME = "slink-admin"
_SESSION_MAX_AGE = 12 * 60 * 60


def _configured_key() -> str:
    return os.environ.get(ADMIN_KEY_ENV, "")


def _secure_cookie() -> bool:
    return os.environ.get(ADMIN_COOKIE_SECURE_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _bearer(request: web.Request) -> str:
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    return value if scheme.lower() == "bearer" else ""


def _safe_next(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else "/"


async def is_admin(request: web.Request) -> bool:
    key = _configured_key()
    if not key:
        return True
    bearer = _bearer(request)
    if bearer and hmac.compare_digest(bearer, key):
        return True
    session = await get_session(request)
    return session.get("admin") is True


async def handle_login_page(request: web.Request) -> web.Response:
    if await is_admin(request):
        raise web.HTTPFound(_safe_next(request.query.get("next", "/")))
    return aiohttp_jinja2.render_template(
        "admin_login.html",
        request,
        {
            "page_title": "Administrator login",
            "theme": "default",
            "next_path": _safe_next(request.query.get("next", "/")),
            "error": "",
            "is_stream": False,
            "hide_chrome": True,
            "body_class": "board",
        },
    )


async def handle_login(request: web.Request) -> web.Response:
    data = await request.post()
    supplied = str(data.get("admin_key", ""))
    next_path = _safe_next(str(data.get("next", "/")))
    key = _configured_key()
    if not key or not hmac.compare_digest(supplied, key):
        return aiohttp_jinja2.render_template(
            "admin_login.html",
            request,
            {
                "page_title": "Administrator login",
                "theme": "default",
                "next_path": next_path,
                "error": "Incorrect administrator key.",
                "is_stream": False,
                "hide_chrome": True,
                "body_class": "board",
            },
            status=401,
        )
    session = await get_session(request)
    session["admin"] = True
    raise web.HTTPSeeOther(next_path)


async def handle_logout(request: web.Request) -> web.Response:
    session = await get_session(request)
    session.invalidate()
    raise web.HTTPSeeOther("/")


def login_url(request: web.Request) -> str:
    return "/admin/login?" + urlencode({"next": request.rel_url.path_qs})


def setup_admin_auth(app: web.Application) -> None:
    key = _configured_key()
    app[ADMIN_ENABLED] = bool(key)
    cookie_key = hashlib.sha256((key or "local-development").encode()).digest()
    setup(
        app,
        EncryptedCookieStorage(
            cookie_key,
            cookie_name=_COOKIE_NAME,
            max_age=_SESSION_MAX_AGE,
            secure=_secure_cookie(),
            httponly=True,
            samesite="Lax",
        ),
    )
    app.router.add_get("/admin/login", handle_login_page)
    app.router.add_post("/admin/login", handle_login)
    app.router.add_post("/admin/logout", handle_logout)
