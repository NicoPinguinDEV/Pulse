"""Pure policy helpers shared by the Discord moderation cogs and their tests."""
from __future__ import annotations

import re
from collections import deque
from typing import Iterable

AUTOMOD_RULES = frozenset({"spam", "mentions", "invites", "keywords", "domains"})
ANTINUKE_EVENTS = frozenset({
    "ban", "kick", "role_delete", "role_update", "channel_delete", "channel_update",
    "webhook_create", "webhook_delete", "webhook_update", "bot_add",
})
DEFAULT_ANTINUKE_EVENTS = frozenset({"ban", "kick", "role_delete", "role_update", "channel_delete", "channel_update"})
ANTINUKE_WINDOW_SECONDS = 10
ANTINUKE_ACTION_LIMIT = 3
ANTINUKE_MAX_WINDOW_SECONDS = 60

_INVITE_RE = re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)", re.IGNORECASE)
_URL_RE = re.compile(r"(?:(?:https?://)?(?:www\.)?)([a-z0-9.-]+\.[a-z]{2,})(?:[/:?#]|\b)", re.IGNORECASE)


def classify_message(
    content: str,
    *,
    rules: Iterable[str],
    mention_count: int = 0,
    mention_limit: int = 5,
    duplicate_count: int = 0,
    message_count: int = 0,
    blocked_terms: Iterable[str] = (),
    blocked_domains: Iterable[str] = (),
) -> str | None:
    """Return the first enabled moderation rule that matches, without judging intent."""
    enabled = set(rules) & AUTOMOD_RULES
    text = str(content or "")
    if "mentions" in enabled and mention_count > max(0, int(mention_limit)):
        return "mentions"
    if "invites" in enabled and _INVITE_RE.search(text):
        return "invites"

    if "keywords" in enabled:
        folded = text.casefold()
        for term in blocked_terms:
            candidate = str(term or "").strip().casefold()
            pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in candidate.split()) + r"(?!\w)"
            if candidate and re.search(pattern, folded):
                return "keywords"

    if "domains" in enabled:
        allowed = {str(domain).strip().lower().lstrip("@") for domain in blocked_domains if str(domain).strip()}
        for match in _URL_RE.finditer(text):
            host = match.group(1).lower().removeprefix("www.")
            if host in allowed or any(host.endswith("." + domain) for domain in allowed):
                return "domains"

    if "spam" in enabled and (duplicate_count >= 3 or message_count >= 6):
        return "spam"
    return None


def is_trusted_actor(
    actor_id: int,
    *,
    owner_id: int,
    bot_id: int,
    role_ids: Iterable[int] = (),
    trusted_user_ids: Iterable[int] = (),
    trusted_role_ids: Iterable[int] = (),
) -> bool:
    """Protect the server owner, Pulse itself, and explicitly allowlisted actors."""
    if int(actor_id) in {int(owner_id), int(bot_id)}:
        return True
    if int(actor_id) in {int(value) for value in trusted_user_ids}:
        return True
    return bool({int(value) for value in role_ids} & {int(value) for value in trusted_role_ids})


def antinuke_window_exceeded(
    events: deque[float],
    timestamp: float,
    *,
    limit: int = ANTINUKE_ACTION_LIMIT,
    window_seconds: int = ANTINUKE_WINDOW_SECONDS,
) -> bool:
    """Append one confirmed audit-log event and report whether the configured threshold fired."""
    window = max(1, min(ANTINUKE_MAX_WINDOW_SECONDS, int(window_seconds)))
    threshold = max(2, min(10, int(limit)))
    cutoff = float(timestamp) - window
    while events and events[0] < cutoff:
        events.popleft()
    events.append(float(timestamp))
    return len(events) >= threshold


def valid_domain(value: str) -> bool:
    """Accept a plain DNS hostname for the configured link blocklist."""
    domain = str(value or "").strip().lower().removeprefix("www.")
    return bool(domain and len(domain) <= 253 and re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
        domain,
    ))
