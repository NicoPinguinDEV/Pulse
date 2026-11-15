"""Audit #118/#119: one delivery contract for inbox notifications and one place for direct links.

Before this module every caller of ``pulse_db.notify`` chose its own title, URL and dedupe key, and the
notification category (which decides muting) was guessed from keywords in the URL/title. This contract
sits on top of ``notify`` without replacing it:

* :func:`deliver` sends one event (``pulse_events`` domain/action) to one or more people. The category comes
  from the domain (never from a keyword guess), the link from :func:`link_for`, the dedupe key from
  correlation ID + action, so the same case step does not notify the same person twice.
* :func:`link_for` is the single source of internal dashboard links for a case reference or a domain target.
  It only returns paths of routes that exist and never an external or protocol-relative URL.
* :func:`absolute_link` turns such a path into a full URL for Discord (bot embeds, DMs) using the public
  base URL, so a Discord message and the dashboard point at the same place.

Permissions are unchanged: a link never grants access, the target page still checks the viewer's rights.
Recipients are passed in by the caller; this module does not decide who may be notified.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote, urlencode

import pulse_case_registry as registry
import pulse_events as events

log = logging.getLogger("pulse.inbox")

INBOX_URL = "/pulse-inbox"
MAX_TITLE = 160
MAX_BODY = 1000
MAX_RECIPIENTS = 100

# Event domain -> notification category (keys of pulse_notify_prefs.CATEGORIES, or "system" = never muted).
DOMAIN_CATEGORY = {
    "ticket": "tickets",
    "moderation": "team",
    "task": "work",
    "handover": "work",
    "announcement": "announcements",
    "promotion": "team",
    "team": "team",
    "recruiting": "applications",
    "privacy": "system",
    "antinuke": "system",
    "automation": "system",
    "panel": "system",
    "system": "system",
    "other": "system",
}
DOMAIN_PAGES = {"task": "/tasks", "recruiting": "/applications", "announcement": "/announcements", "handover": "/handover",
                "promotion": "/promotions", "moderation": "/mod-cases"}
_TARGET_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_RECIPIENT = re.compile(r"^\d{5,25}$")


def category_for_domain(domain) -> str:
    if domain not in events.DOMAINS:
        raise ValueError(f"Unbekannte Ereignis-Domäne: {domain!r}")
    return DOMAIN_CATEGORY[domain]


def is_safe_path(path) -> bool:
    """Internal dashboard path only: starts with a single ``/``, no scheme, no backslash, no control chars."""
    text = str(path or "")
    return (text.startswith("/") and not text.startswith("//") and "\\" not in text
            and not any(ord(ch) < 32 for ch in text) and len(text) <= 300)


def link_for(*, case_ref=None, domain=None, target_id=None) -> str:
    """Internal link for a case reference, else for a domain target; ``/pulse-inbox`` if nothing fits."""
    if case_ref:
        parsed = registry.parse_ref(case_ref)
        if parsed:
            return "/mod-cases/verlauf?" + urlencode({"ref": registry.make_ref(*parsed)})
    tid = str(target_id or "")
    if domain == "ticket" and _TARGET_ID.match(tid):
        return f"/tickets/transcript/{quote(tid, safe='')}/view"
    # #119: one overview page per domain (the page checks the viewer's rights itself; a link never grants access)
    if domain in DOMAIN_PAGES:
        return DOMAIN_PAGES[domain]
    return INBOX_URL


def absolute_link(path, base_url) -> str:
    """Full URL for Discord messages. Refuses unsafe paths and a missing/invalid base URL (returns ``""``)."""
    base = str(base_url or "").strip().rstrip("/")
    if not is_safe_path(path) or not re.match(r"^https?://[^\s/]+$", base):
        return ""
    return base + str(path)


def _recipients(user_ids) -> list[str]:
    seen: list[str] = []
    for raw in user_ids or ():
        uid = str(raw).strip()
        if not _RECIPIENT.match(uid):
            raise ValueError(f"Ungültige Empfänger-ID: {raw!r}")
        if uid not in seen:
            seen.append(uid)
    if not seen:
        raise ValueError("Mindestens ein Empfänger erforderlich.")
    if len(seen) > MAX_RECIPIENTS:
        raise ValueError(f"Zu viele Empfänger (max. {MAX_RECIPIENTS}).")
    return seen


def deliver(user_ids, domain, action, title, body="", *, case_ref=None, correlation_id=None, target_id=None,
            kind="info", notifier=None) -> dict[str, str]:
    """Notify each recipient once for this event step; returns ``{user_id: notification_id}``.

    Raises ``ValueError`` for invalid input before anything is sent. ``notifier`` defaults to
    ``pulse_db.notify``.
    """
    category = category_for_domain(domain)
    payload, _ = events.build_payload(domain, action, None, case_ref=case_ref, correlation_id=correlation_id)
    clean_title = str(title or "").strip()[:MAX_TITLE]
    if not clean_title:
        raise ValueError("Titel erforderlich.")
    recipients = _recipients(user_ids)
    url = link_for(case_ref=payload.get("case_ref"), domain=domain, target_id=target_id)
    thread = payload.get("correlation_id") or str(target_id or "")
    dedupe = f"{domain}.{action}:{thread}"[:200] if thread else None
    if notifier is None:
        import pulse_db
        notifier = pulse_db.notify
    sent: dict[str, str] = {}
    for uid in recipients:
        try:
            sent[uid] = notifier(uid, clean_title, str(body or "")[:MAX_BODY], kind=kind, url=url,
                                 dedupe_key=dedupe, category=category)
        except Exception:  # noqa: BLE001 - one broken recipient must not stop the others
            log.warning("Benachrichtigung für %s fehlgeschlagen", uid, exc_info=True)
    return sent
