"""U-13: one place for HTTP security headers and the same-origin check of state-changing requests.

Before, two middlewares (webserver.py and pulse_pro.py) set partly contradicting headers with
`setdefault`, so which value won depended on registration order, and the origin check only ran when
the browser sent an `Origin` header. Now:

* Unsafe methods (POST/PUT/PATCH/DELETE) from a browser must come from this site: the `Origin`
  header is checked, otherwise the `Referer`. `Origin: null` is foreign. Requests without both pass only
  when they carry no session cookie (API clients with key, curl) – their routes authenticate themselves.
* Content-Security-Policy starts with the rules that do not break the existing inline UI:
  `frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self' https://discord.com`.
  Script/style nonces follow once the inline CSS/JS is moved to static files (U-03).
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# form-action also governs the redirect after a form POST: the OAuth starts (/verify/authorize/start,
# /recovery/authorize/start) answer with 303 to Discord, which browsers block unless discord.com is allowed.
CSP = "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self' https://discord.com"
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


SESSION_COOKIES = ("user_session=", "apply_session=")


def same_origin_ok(method: str, headers) -> bool:
    """False if a browser request comes – or may come – from another site.

    * `Origin: null` (sandboxed frame, or a cross-site form on a page with `Referrer-Policy: no-referrer`)
      counts as foreign for state-changing methods – before, it fell through to the missing Referer and passed.
    * A request carrying a Pulse session cookie but neither Origin nor Referer is rejected: browsers always
      send Origin on POST, so only a forged request looks like that. API clients without cookies still pass.
    """
    if method.upper() not in UNSAFE_METHODS:
        return True
    raw_origin = (headers.get("origin") or "").strip()
    if raw_origin.lower() == "null":
        return False
    source = _origin_of(raw_origin) if raw_origin else _origin_of(headers.get("referer") or "")
    if not source:
        cookie = headers.get("cookie") or ""
        return not any(name in cookie for name in SESSION_COOKIES)
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
