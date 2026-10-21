"""U-13: one place for HTTP security headers and the same-origin check of state-changing requests.

Before, two middlewares (webserver.py and pulse_pro.py) set partly contradicting headers with
`setdefault`, so which value won depended on registration order, and the origin check only ran when
the browser sent an `Origin` header. Now:

* Unsafe methods (POST/PUT/PATCH/DELETE) from a browser must come from this site: the `Origin`
  header is checked, otherwise the `Referer`. Requests without both (API clients with key, curl,
  tests) are not blocked here – their routes authenticate themselves.
* Content-Security-Policy starts with the rules that do not break the existing inline UI:
  `frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'`.
  Script/style nonces follow once the inline CSS/JS is moved to static files (U-03).
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSP = "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
CACHEABLE_PATHS = {"/sw.js", "/manifest.json", "/manifest.webmanifest", "/favicon.ico", "/robots.txt"}


def _origin_of(url: str) -> str:
    parsed = urlparse(url or "")
    return f"{parsed.scheme}://{parsed.netloc}".lower() if parsed.scheme and parsed.netloc else ""


def allowed_origins(host: str) -> set[str]:
    allowed = {f"http://{host}".lower(), f"https://{host}".lower()} if host else set()
    public = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if public:
        allowed.add(_origin_of(public))
    return allowed


def same_origin_ok(method: str, headers) -> bool:
    """False only if a browser request clearly comes from another site."""
    if method.upper() not in UNSAFE_METHODS:
        return True
    source = headers.get("origin") or ""
    if not source or source == "null":
        source = _origin_of(headers.get("referer") or "")
    else:
        source = _origin_of(source)
    if not source:
        return True
    return source in allowed_origins(headers.get("host", ""))


def apply_headers(path: str, method: str, response, https: bool) -> None:
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    headers.setdefault("Content-Security-Policy", CSP)
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    # Internal team dashboard: search engines must not index it.
    headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    if path not in CACHEABLE_PATHS and not path.startswith("/static/"):
        headers.setdefault("Cache-Control", "no-store")
    if https:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
