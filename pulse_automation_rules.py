"""Validation and matching for the automations of /ultimate/automations (pulse_ultimate is the only engine).

Pure helpers – nothing here talks to Discord or the database, so routes, engine and tests share them.

An automation is: WHEN a trigger fires (schedule, Anti-Nuke rule, security event) AND its conditions hold
THEN a chain of steps runs once. Triggers, conditions and step types come from fixed allow-lists; text fields
only support the placeholders in PLACEHOLDERS (plain replacement, no format specs, no code).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict, deque

import pulse_moderation as pm

TRIGGERS = {
    "interval": "Zeitplan (alle X Minuten)",
    "antinuke_rule": "Anti-Nuke-Regel ausgelöst",
    "antinuke_event": "Sicherheitsereignis erkannt (Discord-Audit-Log)",
}
EVENT_TRIGGERS = frozenset({"antinuke_rule", "antinuke_event"})
ADMIN_TRIGGERS = EVENT_TRIGGERS  # security triggers: administrators only
CONDITIONS = {  # schedule conditions (evaluated by pulse_ultimate._run_automation)
    "always": "immer",
    "open_cases_gte": "offene Fälle ≥ Wert",
    "open_tickets_gte": "offene Tickets ≥ Wert",
    "team_online_gte": "Team online ≥ Wert",
    "team_size_gte": "Teamgröße ≥ Wert",
}
STEP_TYPES = {
    "notify_managers": "Führungskräfte benachrichtigen (Pulse-Inbox)",
    "notify_team": "ganzes Team benachrichtigen (Pulse-Inbox)",
    "notify_user": "eine Person benachrichtigen (Pulse-Inbox)",
    "create_task": "Aufgabe anlegen",
    "create_case": "Fall/Vorgang anlegen",
    "activity_log": "Aktivitätsprotokoll-Eintrag",
    "security_alert": "Sicherheitsmeldung in den Anti-Nuke-Alarmkanal",
    "notify_role": "Verwaltungsrolle im Alarmkanal erwähnen",
    "antinuke_lockdown": "Anti-Nuke-Notfall-Lockdown starten",
    "team_report": "Team-Report senden",
    "ticket_sla_alert": "Ticket-Frist überschritten melden",
    "inactivity_report": "Inaktivitätsbericht",
    "probation_report": "Probezeit-Bericht",
}
LEGACY_STEP_TYPES = frozenset({"notify_managers", "notify_team", "notify_user", "create_task", "create_case",
                               "team_report", "ticket_sla_alert", "inactivity_report", "probation_report"})
ADMIN_STEP_TYPES = frozenset({"security_alert", "notify_role", "antinuke_lockdown"})
PRIORITIES = ("low", "normal", "high", "urgent")
PLACEHOLDERS = ("name", "actor_id", "actor_name", "rule_label", "events", "result", "time")
MAX_STEPS = 6
MAX_NAME = 160
MAX_TEXT = 1000
# Retries (#148): opt-in per automation, bounded attempts, exponential backoff with a hard ceiling.
RETRY_MAX_ATTEMPTS = 5
RETRY_BACKOFF_MIN, RETRY_BACKOFF_MAX, RETRY_BACKOFF_DEFAULT = 30, 3600, 120
RETRY_DELAY_CEILING = 6 * 3600
# A late emergency lockdown is worse than none: this step is never repeated automatically, only by a person.
NO_AUTO_RETRY_STEP_TYPES = frozenset({"antinuke_lockdown"})
# Failures that another attempt cannot fix (missing configuration, missing rights, unsupported action).
PERMANENT_FAILURE_MARKERS = ("nicht unterstützte Aktion", "kein Anti-Nuke-Alarmkanal", "keine Verwaltungsrolle",
                             "Discord hat das Senden abgelehnt", "unsupported action", "target unavailable",
                             "assignee unavailable")
# Approvals (#144/#145): ordered stages, each with a responsible group and a quorum.
APPROVER_ROLES = {"manager": "Führungskraft", "admin": "Administrator"}
MAX_APPROVAL_STAGES = 3
MAX_STAGE_APPROVALS = 5
APPROVAL_TIMEOUT_MIN, APPROVAL_TIMEOUT_MAX, APPROVAL_TIMEOUT_DEFAULT = 5, 10080, 1440
DEFINITION_FIELDS = ("name", "trigger_type", "interval_minutes", "trigger_config", "condition", "steps", "test_mode",
                     "retry", "approval")
DEFINITION_LABELS = {"name": "Name", "trigger_type": "Auslöser", "interval_minutes": "Intervall (Minuten)",
                     "trigger_config": "Auslöser-Optionen", "condition": "Bedingung", "steps": "Aktionen",
                     "test_mode": "Testmodus", "retry": "Wiederholungen", "approval": "Freigaben"}
_PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")
_DISCORD_ID = re.compile(r"[0-9]{15,22}")
_RULE_ID = re.compile(r"(?:global|[a-z0-9_-]{1,32})")


def _int(value, label: str, lo: int, hi: int, default: int | None = None) -> int:
    text = str(value if value is not None else "").strip()
    if not text and default is not None:
        return default
    if not re.fullmatch(r"\d{1,7}", text):
        raise ValueError(f"{label}: Bitte eine ganze Zahl zwischen {lo} und {hi}.")
    number = int(text)
    if not lo <= number <= hi:
        raise ValueError(f"{label} muss zwischen {lo} und {hi} liegen.")
    return number


def _text(value, label: str, limit: int = MAX_TEXT, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{label} fehlt.")
    if len(text) > limit:
        raise ValueError(f"{label}: höchstens {limit} Zeichen.")
    return text


def needs_admin(automation: dict) -> bool:
    """Security automations (triggers or steps) may only be read or changed by administrators."""
    steps = automation.get("steps") or []
    return automation.get("trigger_type") in ADMIN_TRIGGERS or any(s.get("type") in ADMIN_STEP_TYPES for s in steps) \
        or automation.get("action_type") in ADMIN_STEP_TYPES


def normalize_step(raw: dict) -> dict:
    """One validated step. Raises ValueError for unknown types or invalid fields."""
    kind = str(raw.get("type") or "").strip()
    if kind not in STEP_TYPES:
        raise ValueError(f"Unbekannte Aktion „{kind[:40]}“.")
    step = {"type": kind}
    if kind in ("notify_managers", "notify_team", "notify_user"):
        step["title"] = _text(raw.get("title"), "Titel", MAX_NAME)
        step["body"] = _text(raw.get("body"), "Text")
        step["urgent"] = bool(raw.get("urgent"))
    if kind == "notify_user":
        target = str(raw.get("user_id") or raw.get("target") or "").strip()
        if not _DISCORD_ID.fullmatch(target):
            raise ValueError("Person benachrichtigen: gültige Discord-ID (15–22 Ziffern) angeben.")
        step["user_id"] = target
    if kind == "create_task":
        target = str(raw.get("assignee_id") or raw.get("target") or "").strip()
        if not _DISCORD_ID.fullmatch(target):
            raise ValueError("Aufgabe anlegen: gültige Discord-ID der zuständigen Person angeben.")
        step["assignee_id"] = target
        step["title"] = _text(raw.get("title"), "Aufgabentitel", MAX_NAME, required=True)
        step["description"] = _text(raw.get("description") or raw.get("body"), "Beschreibung", 2500)
    if kind == "create_case":
        step["title"] = _text(raw.get("title"), "Falltitel", MAX_NAME, required=True)
        step["description"] = _text(raw.get("description") or raw.get("body"), "Beschreibung", 5000)
        step["category"] = _text(raw.get("category") or "automation", "Kategorie", 40)
    if kind in ("create_task", "create_case"):
        priority = str(raw.get("priority") or "normal")
        if priority not in PRIORITIES:
            raise ValueError("Priorität: Unbekannte Auswahl.")
        step["priority"] = priority
    if kind in ("activity_log", "security_alert", "notify_role"):
        step["text"] = _text(raw.get("text") or raw.get("body"), "Text", MAX_TEXT, required=kind != "notify_role")
    if kind == "ticket_sla_alert":
        step["minutes"] = _int(raw.get("minutes") if raw.get("minutes") is not None else raw.get("number"), "Minuten", 5, 1440, 30)
    if kind in ("inactivity_report", "probation_report"):
        step["days"] = _int(raw.get("days") if raw.get("days") is not None else raw.get("number"), "Tage", 1, 365,
                            14 if kind == "inactivity_report" else 7)
    return step


def normalize_trigger_config(trigger: str, raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    if trigger == "antinuke_rule":
        rule_id = str(raw.get("rule_id") or "").strip().lower()
        if rule_id and not _RULE_ID.fullmatch(rule_id):
            raise ValueError("Ungültige Anti-Nuke-Regel.")
        return {"rule_id": rule_id, "include_dry_run": bool(raw.get("include_dry_run", True)),
                "cooldown_seconds": _int(raw.get("cooldown_seconds"), "Cooldown", 0, 86400, 60)}
    if trigger == "antinuke_event":
        events = raw.get("events") if isinstance(raw.get("events"), list) else []
        if not events or any(e not in pm.ANTINUKE_EVENTS for e in events):
            raise ValueError("Bitte mindestens ein unterstütztes Sicherheitsereignis wählen.")
        group = raw.get("group") or "actor"
        if group not in ("actor", "all"):
            raise ValueError("Gruppierung: Unbekannte Auswahl.")
        return {"events": list(dict.fromkeys(events)),
                "min_count": _int(raw.get("min_count"), "Mindestanzahl", 1, 50, 1),
                "window_seconds": _int(raw.get("window_seconds"), "Zeitraum", 3, 3600, 10),
                "group": group,
                "cooldown_seconds": _int(raw.get("cooldown_seconds"), "Cooldown", 0, 86400, 60)}
    return {}


def normalize_retry(raw) -> dict:
    """Retry policy of one automation. max_attempts 0 = no automatic repetition (the default, as before)."""
    raw = raw if isinstance(raw, dict) else {}
    attempts = _int(raw.get("max_attempts"), "Wiederholungen", 0, RETRY_MAX_ATTEMPTS, 0)
    backoff = _int(raw.get("backoff_seconds"), "Wartezeit vor der ersten Wiederholung (Sekunden)",
                   RETRY_BACKOFF_MIN, RETRY_BACKOFF_MAX, RETRY_BACKOFF_DEFAULT)
    return {"max_attempts": attempts, "backoff_seconds": backoff}


def backoff_delay(base_seconds: int, attempt: int) -> int:
    """Seconds to wait before retry number `attempt` (1-based): base, 2×base, 4×base … capped."""
    base = min(max(int(base_seconds or RETRY_BACKOFF_DEFAULT), RETRY_BACKOFF_MIN), RETRY_BACKOFF_MAX)
    return min(base * (2 ** max(0, min(int(attempt), 20) - 1)), RETRY_DELAY_CEILING)


def is_permanent_failure(detail: str) -> bool:
    text = str(detail or "")
    return any(marker in text for marker in PERMANENT_FAILURE_MARKERS)


def normalize_approval(raw, *, security: bool = False) -> dict:
    """Approval stages in their fixed order. Security automations may only be approved by administrators."""
    raw = raw if isinstance(raw, dict) else {}
    stages = []
    for item in (raw.get("stages") or []):
        if not isinstance(item, dict):
            raise ValueError("Ungültige Freigabestufe.")
        role = str(item.get("role") or "").strip()
        if not role:
            continue
        if role not in APPROVER_ROLES:
            raise ValueError("Freigabestufe: Unbekannte Zuständigkeit.")
        if security and role != "admin":
            raise ValueError("Sicherheits-Automationen dürfen nur von Administratoren freigegeben werden.")
        stages.append({"label": _text(item.get("label"), "Name der Freigabestufe", 80) or APPROVER_ROLES[role],
                       "role": role,
                       "min_approvals": _int(item.get("min_approvals"), "Nötige Freigaben", 1, MAX_STAGE_APPROVALS, 1)})
    if len(stages) > MAX_APPROVAL_STAGES:
        raise ValueError(f"Höchstens {MAX_APPROVAL_STAGES} Freigabestufen pro Automation.")
    if not stages:
        return {"stages": [], "timeout_minutes": APPROVAL_TIMEOUT_DEFAULT}
    return {"stages": stages,
            "timeout_minutes": _int(raw.get("timeout_minutes"), "Frist für Freigaben (Minuten)", APPROVAL_TIMEOUT_MIN,
                                    APPROVAL_TIMEOUT_MAX, APPROVAL_TIMEOUT_DEFAULT)}


def definition_snapshot(value: dict) -> dict:
    """The versioned part of an automation (everything except its on/off switch and run times)."""
    return {
        "name": str(value.get("name") or ""),
        "trigger_type": str(value.get("trigger_type") or ""),
        "interval_minutes": int(value.get("interval_minutes") or 1440),
        "trigger_config": value.get("trigger_config") or {},
        "condition": value.get("condition") or {"type": "always"},
        "steps": list(value.get("steps") or []),
        "test_mode": bool(value.get("test_mode")),
        "retry": value.get("retry") or {"max_attempts": 0, "backoff_seconds": RETRY_BACKOFF_DEFAULT},
        "approval": value.get("approval") or {"stages": [], "timeout_minutes": APPROVAL_TIMEOUT_DEFAULT},
    }


def definition_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _show(value) -> str:
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def diff_definitions(before: dict, after: dict) -> list[dict]:
    """Field-by-field comparison of two definition snapshots; steps are compared position by position."""
    changes = []
    for field in DEFINITION_FIELDS:
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        if field == "steps":
            old_steps, new_steps = list(old or []), list(new or [])
            for index in range(max(len(old_steps), len(new_steps))):
                a = old_steps[index] if index < len(old_steps) else None
                b = new_steps[index] if index < len(new_steps) else None
                if a != b:
                    changes.append({"field": f"steps[{index + 1}]", "label": f"Aktion {index + 1}",
                                    "before": "—" if a is None else _show(a), "after": "—" if b is None else _show(b)})
            continue
        changes.append({"field": field, "label": DEFINITION_LABELS[field],
                        "before": "—" if old is None else _show(old), "after": "—" if new is None else _show(new)})
    return changes


def normalize_automation(raw: dict, *, is_admin: bool) -> dict:
    """Validated automation from the editor. Raises ValueError (refuses unknown or manipulated values)."""
    name = _text(raw.get("name"), "Name", MAX_NAME, required=True)
    trigger = str(raw.get("trigger_type") or "")
    if trigger not in TRIGGERS:
        raise ValueError("Unbekannter Auslöser.")
    steps = [normalize_step(step) for step in (raw.get("steps") or [])]
    if not steps:
        raise ValueError("Bitte mindestens eine Aktion wählen.")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"Höchstens {MAX_STEPS} Aktionen pro Automation.")
    condition = {"type": "always"}
    if trigger == "interval":
        ctype = str((raw.get("condition") or {}).get("type") or "always")
        if ctype not in CONDITIONS:
            raise ValueError("Unbekannte Bedingung.")
        if ctype != "always":
            condition = {"type": ctype, "value": _int((raw.get("condition") or {}).get("value"), "Bedingungswert", 0, 100000)}
    value = {
        "name": name,
        "trigger_type": trigger,
        "interval_minutes": _int(raw.get("interval_minutes"), "Intervall (Minuten)", 5, 10080, 1440) if trigger == "interval" else 1440,
        "trigger_config": normalize_trigger_config(trigger, raw.get("trigger_config") or {}),
        "condition": condition,
        "steps": steps,
        "enabled": bool(raw.get("enabled", True)),
        "test_mode": bool(raw.get("test_mode")),
    }
    if needs_admin(value) and not is_admin:
        raise PermissionError("Sicherheits-Automationen dürfen nur Administratoren anlegen oder ändern.")
    value["retry"] = normalize_retry(raw.get("retry"))
    value["approval"] = normalize_approval(raw.get("approval"), security=needs_admin(value))
    return value


def render_text(template: str, context: dict) -> str:
    """Replace the documented {placeholders}; everything else stays literal (no str.format)."""
    return _PLACEHOLDER.sub(lambda m: str(context.get(m.group(1), ""))[:200], str(template or ""))


def trigger_matches(trigger_type: str, config: dict, trigger: str, context: dict) -> bool:
    if trigger_type != trigger:
        return False
    if trigger == "antinuke_rule":
        if config.get("rule_id") and config["rule_id"] != context.get("rule_id"):
            return False
        return bool(config.get("include_dry_run", True)) or not context.get("dry_run")
    if trigger == "antinuke_event":
        return bool(set(config.get("events") or []) & set(context.get("events") or []))
    return False


class EventCounter:
    """Bounded sliding-window counter for "at least N events in T seconds (per actor / overall)"."""

    def __init__(self, max_keys: int = 2000):
        self.max_keys = max_keys
        self._events: OrderedDict[tuple, deque[float]] = OrderedDict()

    def hit(self, key: tuple, now: float, *, min_count: int, window_seconds: int) -> int:
        events = self._events.pop(key, None) or deque()
        cutoff = now - window_seconds
        while events and events[0] < cutoff:
            events.popleft()
        events.append(now)
        while len(events) > max(1, min_count):
            events.popleft()
        self._events[key] = events
        while len(self._events) > self.max_keys:
            self._events.popitem(last=False)
        return len(events)

    def reset(self, key: tuple) -> None:
        self._events.pop(key, None)


def describe(automation: dict, rule_labels: dict | None = None) -> str:
    """WHEN … AND … THEN … in plain German (cards and editor preview)."""
    trigger = automation.get("trigger_type")
    cfg = automation.get("trigger_config") or {}
    if trigger == "interval":
        when = f"Alle {automation.get('interval_minutes', 1440)} Minuten"
    elif trigger == "antinuke_rule":
        rule_id = cfg.get("rule_id") or ""
        label = f"die Anti-Nuke-Regel „{(rule_labels or {}).get(rule_id, rule_id)}“" if rule_id else "eine beliebige Anti-Nuke-Regel"
        when = f"Wenn {label} auslöst" + ("" if cfg.get("include_dry_run", True) else " (nur echte Maßnahmen)")
    elif trigger == "antinuke_event":
        events = " oder ".join(pm.ANTINUKE_EVENT_LABELS.get(e, e) for e in cfg.get("events", []))
        count = int(cfg.get("min_count", 1))
        when = f"Wenn „{events}“ erkannt wird"
        if count > 1:
            who = "von derselben Person" if cfg.get("group") == "actor" else "insgesamt"
            when = f"Wenn „{events}“ mindestens {count}× in {cfg.get('window_seconds', 10)} s {who} erkannt wird"
    else:
        when = f"Auslöser „{trigger}“"
    condition = automation.get("condition") or {"type": "always"}
    cond = "" if condition.get("type", "always") == "always" else f" und {CONDITIONS.get(condition['type'], condition['type']).replace('Wert', str(condition.get('value', 0)))}"
    steps = ", ".join(STEP_TYPES.get(s.get("type"), s.get("type", "?")) for s in automation.get("steps") or []) \
        or STEP_TYPES.get(automation.get("action_type"), automation.get("action_type") or "–")
    text = f"{when}{cond}, dann: {steps}."
    stages = (automation.get("approval") or {}).get("stages") or []
    if stages:
        text += " Vorher Freigabe: " + " → ".join(
            f"{s.get('label') or APPROVER_ROLES.get(s.get('role'), '?')} ({int(s.get('min_approvals', 1))}×)" for s in stages) + "."
    if int((automation.get("retry") or {}).get("max_attempts") or 0) > 0:
        text += f" Fehlgeschlagene Aktionen werden bis zu {int(automation['retry']['max_attempts'])}× wiederholt."
    if automation.get("test_mode"):
        text += " Testmodus: Aktionen werden nur protokolliert, nicht ausgeführt."
    return text
