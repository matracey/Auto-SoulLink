"""Browser request protections shared by the run server and manager.

SLink also has native HTTP callers (test tools and the calculator bridge), so
requests without browser-origin headers remain supported. Explicit browser
provenance must establish the same origin, including the port. Fetch Metadata
alone is insufficient for the documented plain-HTTP LAN setup.
"""

import ipaddress
import os
import socket
from fnmatch import fnmatchcase
from urllib.parse import urlsplit

from aiohttp import web

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_HOSTS_ENV = "SLINK_ALLOWED_HOSTS"


def allow_hosts(names) -> None:
    """Add ``--allow-host`` names (globs like ``*.<tailnet>.ts.net`` work) to the Host allow list.

    They go into the environment so run servers the Manager spawns inherit them.
    """
    names = [n.strip().lower() for n in names or () if n and n.strip()]
    if names:
        current = os.environ.get(ALLOWED_HOSTS_ENV, "")
        os.environ[ALLOWED_HOSTS_ENV] = ",".join(filter(None, [current, *names]))


_LOCAL_SUFFIXES = ("", ".local", ".lan", ".home", ".home.arpa", ".localdomain", ".internal")


def _host_allowed(host: str) -> bool:
    """Is a Host header one this LAN app answers to? Blocks DNS rebinding."""
    try:
        name = urlsplit(f"//{host}").hostname
    except ValueError:
        return False
    if not name:
        return False
    name = name.rstrip(".")
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name)
        # Any IP literal: rebinding needs a DNS name, and a browser only sends an IP Host
        # to the IP it is already talking to. Covers LAN, Tailscale 100.x, port-forwards.
        return True
    except ValueError:
        pass
    # The machine's own name, bare or under a suffix nobody can register publicly.
    # Not "<name>.anything": <name>.evil.com rebinds to 127.0.0.1 just as well.
    me = socket.gethostname().lower().split(".")[0]
    if name in {me + suffix for suffix in _LOCAL_SUFFIXES}:
        return True
    allowed = os.environ.get(ALLOWED_HOSTS_ENV, "")
    return any(fnmatchcase(name, pattern.strip().lower())
               for pattern in allowed.split(",") if pattern.strip())


def _origin(value: str, *, referer: bool = False) -> tuple[str, str, int] | None:
    """Parse a browser origin without accepting credentials or opaque URLs."""
    if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        if not referer and (parsed.path or parsed.query or parsed.fragment):
            return None
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        if not 1 <= port <= 65535:
            return None
        return parsed.scheme, parsed.hostname.lower(), port
    except ValueError:
        return None


def _request_origin(request: web.Request) -> tuple[str, str, int] | None:
    """Build the public request origin while keeping Host as the authority.

    TLS-terminating proxies forward the original scheme separately. Trusting
    only that scheme cannot bypass the independently validated Host check.
    """
    forwarded_scheme = request.headers.get("X-Forwarded-Proto", "")
    scheme = forwarded_scheme.split(",", 1)[0].strip().lower() or request.scheme
    if scheme not in {"http", "https"}:
        return None
    return _origin(f"{scheme}://{request.host}")


def _same_origin_request(request: web.Request) -> bool:
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None and site not in {"same-origin", "same-site", "none"}:
        return False

    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin is not None or referer is not None:
        # Origin takes precedence; an opaque/malformed Origin must not be
        # rescued by an apparently safe Referer. Ignore forwarded host headers.
        source = _origin(origin) if origin is not None else _origin(referer, referer=True)
        target = _request_origin(request)
        return source is not None and target is not None and source == target

    if site == "same-origin":
        return True
    if site is not None:
        # 'same-site' includes other ports/subdomains; 'none' is not proof of
        # same-origin provenance for a mutation.
        return False
    # Preserve headerless native callers, but don't misclassify incomplete
    # browser Fetch Metadata as a native request.
    return not any(name.lower().startswith("sec-fetch-") for name in request.headers)


@web.middleware
async def csrf_protection(request: web.Request, handler):
    """Reject cross-origin browser mutations before any handler side effects.

    Every method also needs a known Host: under DNS rebinding a hostile page reads
    GETs as same-origin, so the Host header is the only thing that tells.
    """
    host = request.headers.get("Host")
    if host is not None and not _host_allowed(host):
        return web.json_response(
            {"ok": False, "error": "Unknown Host; start the server with --allow-host for this name"},
            status=403,
            headers={"Cache-Control": "no-store"},
        )
    if request.method not in _SAFE_METHODS and not _same_origin_request(request):
        return web.json_response(
            {"ok": False, "error": "Cross-origin requests are not allowed"},
            status=403,
            headers={"Cache-Control": "no-store"},
        )
    return await handler(request)


@web.middleware
async def theme_cache(request: web.Request, handler):
    """Revalidate themed HTML and partition caches by the theme cookie.

    The HTML route families share theme rendering, so cover them centrally.
    Leave JSON, files and already-started streams to their own cache policy.
    Preserve stricter existing cache directives and unrelated Vary tokens.
    """
    response = await handler(request)
    if not response.prepared and response.content_type == "text/html":
        # Anti-framing only; the app's own iframes (stream index) are same-origin.
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
        cache = ", ".join(response.headers.getall("Cache-Control", []))
        if "no-cache" not in {token.strip().lower() for token in cache.split(",")}:
            response.headers["Cache-Control"] = f"{cache}, no-cache" if cache else "no-cache"
        vary = ", ".join(response.headers.getall("Vary", []))
        tokens = {token.strip().lower() for token in vary.split(",")}
        if "cookie" not in tokens and "*" not in tokens:
            response.headers["Vary"] = f"{vary}, Cookie" if vary else "Cookie"
    return response
