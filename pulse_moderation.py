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
    # Control Center (7.18): further audit-log events, usable in the global list and in individual rules.
    "channel_create", "channel_settings", "role_create", "member_role_grant", "guild_update",
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
    "channel_create": "Kanal erstellt",
    "channel_settings": "Kritische Kanaleinstellung geändert",
    "role_create": "Rolle erstellt",
    "member_role_grant": "Gefährliche Rolle vergeben",
    "guild_update": "Servereinstellungen geändert",
}
# Longer explanation per event for the rule editor (what exactly Pulse sees in the audit log).
ANTINUKE_EVENT_HINTS = {
    "ban": "Jeder Bann eines Mitglieds.",
    "kick": "Jeder Kick (normales Verlassen zählt nicht).",
    "role_delete": "Eine Rolle wurde gelöscht.",
    "role_update": "Rechte oder Position einer Rolle wurden geändert (Umbenennen zählt nicht).",
    "role_create": "Eine neue Rolle wurde erstellt.",
    "member_role_grant": "Ein Mitglied hat eine Rolle mit gefährlichen Rechten (z. B. Administrator) erhalten.",
    "channel_delete": "Ein Kanal oder eine Kategorie wurde gelöscht.",
    "channel_create": "Ein Kanal oder eine Kategorie wurde erstellt.",
    "channel_update": "Berechtigungen (Overwrites) eines Kanals wurden geändert.",
    "channel_settings": "Kategorie, NSFW-Status oder Rechte-Synchronisierung eines Kanals wurden geändert.",
    "webhook_create": "Ein Webhook wurde erstellt.",
    "webhook_delete": "Ein Webhook wurde gelöscht.",
    "webhook_update": "Ein Webhook wurde geändert.",
    "bot_add": "Ein Bot wurde zum Server hinzugefügt.",
    "guild_update": "Name, Verifizierungsstufe, Inhaltsfilter oder 2FA-Pflicht des Servers wurden geändert.",
}
ANTINUKE_EVENT_GROUPS = (
    ("Kanäle", ("channel_delete", "channel_create", "channel_update", "channel_settings")),
    ("Rollen", ("role_delete", "role_update", "role_create", "member_role_grant")),
    ("Mitglieder", ("ban", "kick", "bot_add")),
    ("Webhooks & Server", ("webhook_create", "webhook_update", "webhook_delete", "guild_update")),
)
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
# Operating mode: "alert" counts and reports but never sanctions, "protect" runs the configured actions.
ANTINUKE_MODES = ("alert", "protect")
ANTINUKE_MODE_LABELS = {"alert": "Alarmmodus (nur melden, keine Sanktionen)", "protect": "Aktiver Schutz (konfigurierte Maßnahmen)"}
# Fixed allow-list of rule actions – never arbitrary code. "log" always runs.
RULE_ACTIONS = ("log", "alert", "notify_role", "notify_admins", "lockdown", "quarantine", "kick", "ban")
RULE_ACTION_LABELS = {
    "log": "Vorfall protokollieren",
    "alert": "Alarm in den Sicherheitskanal",
    "notify_role": "Verwaltungsrolle erwähnen",
    "notify_admins": "Administratoren in der Pulse-Inbox benachrichtigen",
    "lockdown": "Notfall-Lockdown der ausgewählten Kanäle",
    "quarantine": "Rollen entziehen (Quarantäne, umkehrbar)",
    "kick": "Verantwortlichen vom Server kicken",
    "ban": "Verantwortlichen vom Server bannen",
}
SANCTION_ACTIONS = ("lockdown", "quarantine", "kick", "ban")  # also the execution order
RULE_SEVERITIES = ("critical", "high", "medium")
RULE_SEVERITY_LABELS = {"critical": "Kritisch", "high": "Hoch", "medium": "Mittel"}
MISSING_PERMISSION_BEHAVIOURS = ("alert", "log")
RULE_MIN_LIMIT, RULE_MAX_LIMIT = 1, 50
RULE_MIN_WINDOW, RULE_MAX_WINDOW = 3, 600
MAX_RULES = 40
MAX_LOCKDOWN_CHANNELS = 25
GLOBAL_RULE_ID = "global"
_RULE_ID = re.compile(r"[a-z0-9_-]{1,32}")
_LEGACY_RESPONSE_ACTIONS = {"alert": [], "quarantine": ["quarantine"], "kick": ["kick"], "ban": ["ban"]}
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
# Labels are capped at 63 characters (DNS limit), so every repetition is bounded for static ReDoS checks too.
_URL_RE = re.compile(r"(?<![a-z0-9.-])([a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+)", re.IGNORECASE)
_EXPLICIT_URL_RE = re.compile(r"(?:https?://|(?<![a-z0-9.-])www\.)([a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+)", re.IGNORECASE)
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


def rule_window_exceeded(events: deque[float], timestamp: float, *, limit: int, window_seconds: int) -> bool:
    """Like antinuke_window_exceeded, with the wider bounds of individual rules (1–50 actions, 3–600 s)."""
    window = max(RULE_MIN_WINDOW, min(RULE_MAX_WINDOW, int(window_seconds)))
    threshold = max(RULE_MIN_LIMIT, min(RULE_MAX_LIMIT, int(limit)))
    cutoff = float(timestamp) - window
    while events and events[0] < cutoff:
        events.popleft()
    events.append(float(timestamp))
    while len(events) > threshold:
        events.popleft()
    return len(events) >= threshold


def _optional_int(value, minimum: int, maximum: int):
    """None for "use the global fallback" (empty/None), otherwise a bounded int; invalid → ValueError."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,6}", text):
        raise ValueError(text)
    return max(minimum, min(maximum, int(text)))


def normalize_rule(raw) -> dict | None:
    """One individual Anti-Nuke rule, or None if it is unusable (no id or no known event)."""
    if not isinstance(raw, dict):
        return None
    rule_id = str(raw.get("id") or "").strip().lower()
    if not _RULE_ID.fullmatch(rule_id) or rule_id == GLOBAL_RULE_ID:
        return None
    events = raw.get("events")
    if isinstance(raw.get("event"), str):  # single-event shorthand
        events = [raw.get("event")]
    events = [e for e in dict.fromkeys(events) if e in ANTINUKE_EVENTS] if isinstance(events, list) else []
    if not events:
        return None
    try:
        limit = _optional_int(raw.get("limit"), RULE_MIN_LIMIT, RULE_MAX_LIMIT)
        window = _optional_int(raw.get("window_seconds"), RULE_MIN_WINDOW, RULE_MAX_WINDOW)
        cooldown = _optional_int(raw.get("cooldown_seconds"), 0, 86400)
    except ValueError:
        return None
    actions = raw.get("actions")
    actions = [a for a in RULE_ACTIONS if isinstance(actions, list) and a in actions]
    if "kick" in actions and "ban" in actions:
        actions.remove("kick")  # a ban already removes the person
    if "log" not in actions:
        actions.insert(0, "log")
    return {
        "id": rule_id,
        "enabled": bool(raw.get("enabled", True)),
        "label": (str(raw.get("label") or "").strip() or ANTINUKE_EVENT_LABELS[events[0]])[:80],
        "events": events,
        "limit": limit,
        "window_seconds": window,
        "actions": actions,
        "severity": raw.get("severity") if raw.get("severity") in RULE_SEVERITIES else "high",
        "test_mode": bool(raw.get("test_mode")),
        "cooldown_seconds": cooldown,
        "exempt_user_ids": _ids(raw.get("exempt_user_ids")),
        "exempt_role_ids": _ids(raw.get("exempt_role_ids")),
    }


def antinuke_problems(raw) -> list[str]:
    """Readable list of stored values Pulse had to ignore – shown on the dashboard instead of silently dropping."""
    raw = raw if isinstance(raw, dict) else {}
    out = []
    rules = raw.get("rules")
    if rules is not None and not isinstance(rules, list):
        out.append("„rules“ ist keine Liste – individuelle Regeln werden ignoriert, bis sie neu gespeichert werden.")
    for index, item in enumerate(rules if isinstance(rules, list) else [], 1):
        if normalize_rule(item) is None:
            label = item.get("label") if isinstance(item, dict) else None
            out.append(f"Regel {index}{f' „{str(label)[:40]}“' if label else ''} ist ungültig (ID, Ereignis oder Zahlen) und ist nicht aktiv.")
    if isinstance(rules, list) and len(rules) > MAX_RULES:
        out.append(f"Mehr als {MAX_RULES} Regeln gespeichert – nur die ersten {MAX_RULES} sind aktiv.")
    events = raw.get("events")
    if isinstance(events, list) and set(map(str, events)) - ANTINUKE_EVENTS:
        out.append("Unbekannte Ereignisse in der globalen Liste werden ignoriert: "
                   + ", ".join(sorted(set(map(str, events)) - ANTINUKE_EVENTS))[:200])
    if raw.get("mode") not in (None, *ANTINUKE_MODES):
        out.append("Unbekannter Modus gespeichert – Pulse arbeitet sicherheitshalber im Alarmmodus.")
    return out


def normalize_antinuke(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    events = raw.get("events")
    events = sorted(set(events) & ANTINUKE_EVENTS) if isinstance(events, list) else sorted(DEFAULT_ANTINUKE_EVENTS)
    response = raw.get("response") if raw.get("response") in ANTINUKE_RESPONSES else "ban"
    alert = _ids([raw.get("alert_channel_id")], 1)
    manager = _ids([raw.get("manager_role_id")], 1)
    # Existing installations have no "mode": they keep their behaviour (a sanctioning response = protect).
    # An unknown stored value falls back to the safe alert mode.
    if "mode" not in raw:
        mode = "alert" if response == "alert" else "protect"
    else:
        mode = raw.get("mode") if raw.get("mode") in ANTINUKE_MODES else "alert"
    rules = []
    for item in (raw.get("rules") if isinstance(raw.get("rules"), list) else [])[:MAX_RULES]:
        rule = normalize_rule(item)
        if rule is not None and rule["id"] not in {r["id"] for r in rules}:
            rules.append(rule)
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
        "mode": mode,
        "global_rule_enabled": bool(raw.get("global_rule_enabled", True)),
        "rule_cooldown_seconds": _bounded_int(raw.get("rule_cooldown_seconds"), 600, 30, 86400),
        "action_cooldown_seconds": _bounded_int(raw.get("action_cooldown_seconds"), 600, 30, 86400),
        "on_missing_permissions": raw.get("on_missing_permissions") if raw.get("on_missing_permissions") in MISSING_PERMISSION_BEHAVIOURS else "alert",
        "manager_role_id": manager[0] if manager else 0,
        "lockdown_channel_ids": _ids(raw.get("lockdown_channel_ids"), MAX_LOCKDOWN_CHANNELS),
        "strip_role_ids": _ids(raw.get("strip_role_ids")),
        "rules": rules,
        "revision": _bounded_int(raw.get("revision"), 0, 0, 10**9),
        "updated_at": str(raw.get("updated_at") or "")[:32],
        "updated_by": str(raw.get("updated_by") or "")[:80],
    }


def effective_rules(config: dict) -> list[dict]:
    """Rules the cog evaluates, each with concrete limit/window/cooldown.

    The global rule ("Standardregel") is the pre-7.18 behaviour: all globally monitored events share one
    counter per actor and use action_limit/window_seconds/response. An event covered by an enabled individual
    rule is evaluated by that rule only – the individual values override the global fallback.
    """
    out = []
    individual = [r for r in config.get("rules", []) if r.get("enabled")]
    covered = {event for rule in individual for event in rule["events"]}
    global_events = [e for e in config.get("events", []) if e not in covered]
    if config.get("global_rule_enabled", True) and global_events:
        actions = ["log", "alert", *(["notify_admins"] if config.get("notify_admins", True) else []),
                   *_LEGACY_RESPONSE_ACTIONS.get(config.get("response"), ["ban"])]
        out.append({
            "id": GLOBAL_RULE_ID, "enabled": True, "label": "Globale Standardregel", "events": global_events,
            "limit": config["action_limit"], "window_seconds": config["window_seconds"], "actions": actions,
            "severity": "critical", "test_mode": False, "cooldown_seconds": config["rule_cooldown_seconds"],
            "exempt_user_ids": [], "exempt_role_ids": [], "legacy": True,
        })
    for rule in individual:
        out.append({
            **rule,
            "limit": rule["limit"] if rule["limit"] is not None else config["action_limit"],
            "window_seconds": rule["window_seconds"] if rule["window_seconds"] is not None else config["window_seconds"],
            "cooldown_seconds": rule["cooldown_seconds"] if rule["cooldown_seconds"] is not None else config["rule_cooldown_seconds"],
        })
    return out


def monitored_events(config: dict) -> set[str]:
    events = {event for rule in effective_rules(config) for event in rule["events"]}
    if config.get("kick_unverified_bots"):
        events.add("bot_add")
    return events


def rule_is_dry_run(rule: dict, config: dict) -> bool:
    """True when sanctions of this rule are only reported (global alert mode or rule test mode)."""
    return config.get("mode") != "protect" or bool(rule.get("test_mode"))


def describe_rule(rule: dict, config: dict) -> str:
    """The rule in one plain German sentence (preview in the editor and in alerts)."""
    limit = rule["limit"] if rule.get("limit") is not None else config.get("action_limit", ANTINUKE_ACTION_LIMIT)
    window = rule["window_seconds"] if rule.get("window_seconds") is not None else config.get("window_seconds", ANTINUKE_WINDOW_SECONDS)
    events = " oder ".join(ANTINUKE_EVENT_LABELS.get(e, e) for e in rule.get("events", [])) or "–"
    steps = [RULE_ACTION_LABELS[a] for a in rule.get("actions", []) if a in RULE_ACTION_LABELS]
    sanctions = [a for a in rule.get("actions", []) if a in SANCTION_ACTIONS]
    text = (f"Wenn dieselbe Person innerhalb von {window} Sekunden mindestens {limit}× „{events}“ auslöst, "
            f"führt Pulse aus: {', '.join(steps) or 'Vorfall protokollieren'}.")
    if sanctions and rule_is_dry_run(rule, config):
        text += " Wegen " + ("Testmodus der Regel" if rule.get("test_mode") else "Alarmmodus") + " werden Sanktionen nur gemeldet, nicht ausgeführt."
    return text


def simulate_rule(rule: dict, config: dict, count: int, *, actor_protected: bool = False) -> dict:
    """Dry run for the dashboard: would `count` actions in the window trigger, and what would happen?"""
    found = effective_rules({**config, "rules": [rule], "global_rule_enabled": False})
    if not found:
        return {"triggered": False, "steps": [], "note": "Regel ist deaktiviert."}
    effective = found[0]
    triggered = int(count) >= int(effective["limit"])
    if not config.get("enabled"):
        return {"triggered": False, "steps": [], "note": "Anti-Nuke ist ausgeschaltet – im Betrieb würde nichts passieren."}
    if actor_protected:
        return {"triggered": False, "steps": [], "note": "Serverinhaber, Pulse und freigestellte Personen lösen nie aus."}
    if not triggered:
        return {"triggered": False, "steps": [], "note": f"{count} von {effective['limit']} Aktionen – unter der Schwelle, keine Maßnahme."}
    dry = rule_is_dry_run(effective, config)
    steps = []
    for action in effective["actions"]:
        if action in SANCTION_ACTIONS and dry:
            steps.append((action, "nur gemeldet"))
        else:
            steps.append((action, "würde ausgeführt"))
    return {"triggered": True, "steps": steps, "note": "Alarmmodus/Testmodus: keine Sanktionen." if dry else ""}


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
