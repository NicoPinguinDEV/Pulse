"""Pure policy helpers shared by the Discord moderation cogs, the admin page and their tests.

Nothing here talks to Discord. The cogs (cogs/automod.py, cogs/antinuke.py) and the dashboard
(/admin/security, pulse_guard_admin.py) use the same normalisation, so a value the dashboard shows is
exactly the value the bot enforces. Every number is bounded; unknown keys are dropped.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Iterable

AUTOMOD_RULES = frozenset({"spam", "mentions", "invites", "keywords", "domains", "caps", "links"})
# Order in which rules are checked (first match decides the action).
AUTOMOD_RULE_ORDER = ("mentions", "invites", "keywords", "domains", "links", "caps", "spam")
DEFAULT_AUTOMOD_RULES = frozenset({"spam", "mentions", "invites"})
AUTOMOD_RULE_LABELS = {
    "spam": "Spam / Nachrichtenflut",
    "mentions": "Massen-Erwähnungen",
    "invites": "Discord-Einladungslink",
    "keywords": "Gesperrter Begriff",
    "domains": "Gesperrter Link",
    "links": "Link außerhalb der Freigabeliste",
    "caps": "Übermäßige Großschreibung",
}
AUTOMOD_MODES = ("enforce", "observe")

ANTINUKE_EVENTS = frozenset({
    "ban", "kick", "role_delete", "role_update", "channel_delete", "channel_update",
    "webhook_create", "webhook_delete", "webhook_update", "bot_add",
})
DEFAULT_ANTINUKE_EVENTS = frozenset({"ban", "kick", "role_delete", "role_update", "channel_delete", "channel_update"})
ANTINUKE_EVENT_LABELS = {
    "ban": "Ban",
    "kick": "Kick",
    "role_delete": "Rolle gelöscht",
    "role_update": "Rolle geändert",
    "channel_delete": "Kanal gelöscht",
    "channel_update": "Kanal geändert",
    "webhook_create": "Webhook erstellt",
    "webhook_delete": "Webhook gelöscht",
    "webhook_update": "Webhook geändert",
    "bot_add": "Bot hinzugefügt",
}
# alert: only report · quarantine: remove dangerous roles (reversible) · kick · ban.
# "ban" stays the default so existing installations keep their behaviour.
ANTINUKE_RESPONSES = ("alert", "quarantine", "kick", "ban")
ANTINUKE_RESPONSE_LABELS = {
    "alert": "Nur melden",
    "quarantine": "Quarantäne – gefährliche Rollen entziehen (umkehrbar)",
    "kick": "Vom Server kicken",
    "ban": "Vom Server bannen",
}
ANTINUKE_WINDOW_SECONDS = 10
ANTINUKE_ACTION_LIMIT = 3
ANTINUKE_MAX_WINDOW_SECONDS = 60
# Discord permission names that let someone damage the server. Used for quarantine and hierarchy checks.
DANGEROUS_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
    "ban_members", "kick_members", "moderate_members",
)

MAX_LIST_ITEMS = 100
MAX_ID_ITEMS = 50

_INVITE_RE = re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)", re.IGNORECASE)
# CodeQL ReDoS fix: a host is dot-separated labels, matched only from a token start, so the scan is linear
# (the old `[a-z0-9.-]+\.[a-z]{2,}` backtracked quadratically on "www.-www.-…"). The TLD is checked in _hosts().
_URL_RE = re.compile(r"(?<![a-z0-9.-])([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.IGNORECASE)
_EXPLICIT_URL_RE = re.compile(r"(?:https?://|(?<![a-z0-9.-])www\.)([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.IGNORECASE)
_TLD_RE = re.compile(r"[a-z]{2,}")


def _hosts(pattern: re.Pattern, text: str):
    """Hostnames (lowercase, without "www.") whose last label is a letter TLD, in text order."""
    for match in pattern.finditer(text):
        host = match.group(1).lower().removeprefix("www.")
        if "." in host and _TLD_RE.fullmatch(host.rsplit(".", 1)[1]):
            yield host


# --------------------------------------------------------------------------- helpers
def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _ids(values, limit: int = MAX_ID_ITEMS) -> list[int]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    out = []
    for value in values:
        text = str(value).strip()
        if text.isdigit() and int(text) > 0 and int(text) not in out:
            out.append(int(text))
    return out[:limit]


def _strings(values, limit: int = MAX_LIST_ITEMS) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    out = []
    for value in values:
        text = str(value or "").strip().casefold()[:100]
        if text and text not in out:
            out.append(text)
    return out[:limit]


def _domain_set(values: Iterable[str]) -> set[str]:
    return {str(domain).strip().lower().lstrip("@").removeprefix("www.") for domain in values if str(domain).strip()}


def _host_in(host: str, domains: set[str]) -> bool:
    return host in domains or any(host.endswith("." + domain) for domain in domains)


def _caps_ratio(text: str) -> tuple[int, float]:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0, 0.0
    upper = sum(1 for ch in letters if ch.isupper())
    return len(letters), upper / len(letters)


# --------------------------------------------------------------------------- AutoMod
def match_rules(
    content: str,
    *,
    rules: Iterable[str],
    mention_count: int = 0,
    mention_limit: int = 5,
    duplicate_count: int = 0,
    message_count: int = 0,
    blocked_terms: Iterable[str] = (),
    blocked_domains: Iterable[str] = (),
    allowed_domains: Iterable[str] = (),
    caps_percent: int = 70,
    caps_min_letters: int = 12,
    duplicate_limit: int = 3,
    flood_messages: int = 6,
) -> list[tuple[str, str]]:
    """Every enabled rule that matches, in check order, as (rule, human readable reason)."""
    enabled = set(rules) & AUTOMOD_RULES
    text = str(content or "")
    hits: list[tuple[str, str]] = []
    if "mentions" in enabled and mention_count > max(0, int(mention_limit)):
        hits.append(("mentions", f"{mention_count} Erwähnungen (erlaubt: {int(mention_limit)})"))
    if "invites" in enabled and _INVITE_RE.search(text):
        hits.append(("invites", "Enthält einen Discord-Einladungslink"))

    if "keywords" in enabled:
        folded = text.casefold()
        for term in blocked_terms:
            candidate = str(term or "").strip().casefold()
            if not candidate:
                continue
            pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in candidate.split()) + r"(?!\w)"
            if re.search(pattern, folded):
                hits.append(("keywords", f"Gesperrter Begriff „{candidate}“"))
                break

    blocked = _domain_set(blocked_domains)
    if "domains" in enabled and blocked:
        for host in _hosts(_URL_RE, text):
            if _host_in(host, blocked):
                hits.append(("domains", f"Gesperrte Domain {host}"))
                break

    if "links" in enabled:
        allowed = _domain_set(allowed_domains)
        for host in _hosts(_EXPLICIT_URL_RE, text):
            if not _host_in(host, allowed):
                hits.append(("links", f"Link zu {host} ist nicht freigegeben"))
                break

    if "caps" in enabled:
        letters, ratio = _caps_ratio(text)
        if letters >= max(1, int(caps_min_letters)) and ratio * 100 >= int(caps_percent):
            hits.append(("caps", f"{round(ratio * 100)} % Großbuchstaben (Grenze {int(caps_percent)} %)"))

    if "spam" in enabled and (duplicate_count >= int(duplicate_limit) or message_count >= int(flood_messages)):
        reason = (f"{duplicate_count}× dieselbe Nachricht" if duplicate_count >= int(duplicate_limit)
                  else f"{message_count} Nachrichten in kurzer Zeit")
        hits.append(("spam", reason))
    order = {rule: index for index, rule in enumerate(AUTOMOD_RULE_ORDER)}
    return sorted(hits, key=lambda hit: order.get(hit[0], 99))


def classify_message(content: str, *, rules: Iterable[str], **kwargs) -> str | None:
    """Return the first enabled moderation rule that matches, without judging intent."""
    hits = match_rules(content, rules=rules, **kwargs)
    return hits[0][0] if hits else None


def normalize_automod(raw) -> dict:
    """Complete, bounded AutoMod configuration. Missing keys get the documented defaults."""
    raw = raw if isinstance(raw, dict) else {}
    rules = raw.get("rules")
    rules = sorted(set(rules) & AUTOMOD_RULES) if isinstance(rules, list) else sorted(DEFAULT_AUTOMOD_RULES)
    mode = raw.get("mode") if raw.get("mode") in AUTOMOD_MODES else "enforce"
    timeout_minutes = _bounded_int(raw.get("timeout_minutes"), 10, 1, 1440)
    return {
        "enabled": bool(raw.get("enabled")),
        "mode": mode,
        "rules": rules,
        "mention_limit": _bounded_int(raw.get("mention_limit"), 5, 3, 30),
        "flood_messages": _bounded_int(raw.get("flood_messages"), 6, 3, 20),
        "flood_seconds": _bounded_int(raw.get("flood_seconds"), 10, 3, 60),
        "duplicate_limit": _bounded_int(raw.get("duplicate_limit"), 3, 2, 10),
        "caps_percent": _bounded_int(raw.get("caps_percent"), 70, 50, 100),
        "caps_min_letters": _bounded_int(raw.get("caps_min_letters"), 12, 5, 200),
        "blocked_terms": _strings(raw.get("blocked_terms")),
        "blocked_domains": _strings(raw.get("blocked_domains")),
        "allowed_domains": _strings(raw.get("allowed_domains")),
        "exempt_role_ids": _ids(raw.get("exempt_role_ids")),
        "exempt_channel_ids": _ids(raw.get("exempt_channel_ids")),
        "warn_dm": bool(raw.get("warn_dm", True)),
        "timeout_after_strikes": _bounded_int(raw.get("timeout_after_strikes"), 2, 1, 10),
        "timeout_minutes": timeout_minutes,
        "timeout_max_minutes": max(timeout_minutes, _bounded_int(raw.get("timeout_max_minutes"), 60, 1, 10080)),
        "strike_window_minutes": _bounded_int(raw.get("strike_window_minutes"), 60, 5, 1440),
        "alert_channel_id": _ids([raw.get("alert_channel_id")], 1)[0] if _ids([raw.get("alert_channel_id")], 1) else 0,
    }


def automod_step(strike_count: int, config: dict) -> dict:
    """Escalation for the n-th violation inside the strike window.

    Below `timeout_after_strikes`: warning (DM if enabled). From then on: timeout, doubling per further
    violation up to `timeout_max_minutes`. Example with defaults (2 / 10 / 60): warn, 10, 20, 40, 60, 60 …
    """
    strikes = max(1, int(strike_count or 1))
    after = int(config.get("timeout_after_strikes", 2))
    if strikes < after:
        return {"action": "warn", "minutes": 0}
    base = int(config.get("timeout_minutes", 10))
    maximum = max(base, int(config.get("timeout_max_minutes", base)))
    minutes = min(maximum, base * (2 ** min(10, strikes - after)))
    return {"action": "timeout", "minutes": minutes}


def escalation_preview(config: dict, steps: int = 5) -> list[str]:
    out = []
    for strike in range(1, steps + 1):
        step = automod_step(strike, config)
        out.append("Warnung" if step["action"] == "warn" else f"Timeout {step['minutes']} Min.")
    return out


# --------------------------------------------------------------------------- Anti-Nuke
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
    while len(events) > threshold:  # older entries add nothing once the threshold is known
        events.popleft()
    return len(events) >= threshold


def normalize_antinuke(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    events = raw.get("events")
    events = sorted(set(events) & ANTINUKE_EVENTS) if isinstance(events, list) else sorted(DEFAULT_ANTINUKE_EVENTS)
    response = raw.get("response") if raw.get("response") in ANTINUKE_RESPONSES else "ban"
    alert = _ids([raw.get("alert_channel_id")], 1)
    return {
        "enabled": bool(raw.get("enabled")),
        "events": events,
        "action_limit": _bounded_int(raw.get("action_limit"), ANTINUKE_ACTION_LIMIT, 2, 10),
        "window_seconds": _bounded_int(raw.get("window_seconds"), ANTINUKE_WINDOW_SECONDS, 5, 60),
        "response": response,
        "kick_unverified_bots": bool(raw.get("kick_unverified_bots")),
        "notify_admins": bool(raw.get("notify_admins", True)),
        "trusted_role_ids": _ids(raw.get("trusted_role_ids")),
        "trusted_user_ids": _ids(raw.get("trusted_user_ids")),
        "alert_channel_id": alert[0] if alert else 0,
    }


def has_dangerous_permission(permissions) -> bool:
    return any(bool(getattr(permissions, name, False)) for name in DANGEROUS_PERMISSIONS)


def dangerous_names(permissions) -> list[str]:
    return [name for name in DANGEROUS_PERMISSIONS if getattr(permissions, name, False)]


def roles_to_strip(roles: Iterable, bot_top_position: int) -> list:
    """Roles carrying dangerous permissions that the bot is able to remove (below its own top role)."""
    out = []
    for role in roles:
        if getattr(role, "managed", False) or (hasattr(role, "is_default") and role.is_default()):
            continue
        if int(getattr(role, "position", 0)) >= int(bot_top_position):
            continue
        if has_dangerous_permission(getattr(role, "permissions", None)):
            out.append(role)
    return out


def hierarchy_gaps(roles: Iterable, bot_top_position: int) -> list:
    """Roles with dangerous permissions at or above the bot's top role – Pulse cannot act against them."""
    out = []
    for role in roles:
        if getattr(role, "managed", False) or (hasattr(role, "is_default") and role.is_default()):
            continue
        if int(getattr(role, "position", 0)) >= int(bot_top_position) and has_dangerous_permission(getattr(role, "permissions", None)):
            out.append(role)
    return sorted(out, key=lambda r: -int(getattr(r, "position", 0)))


def valid_domain(value: str) -> bool:
    """Accept a plain DNS hostname for the configured link blocklist."""
    domain = str(value or "").strip().lower().removeprefix("www.")
    return bool(domain and len(domain) <= 253 and re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
        domain,
    ))
