"""Audit #122/#125/#116: one event contract for all Pulse modules.

Pulse already has a central ``events`` table (``pulse_db.record_event``), but its ``event_type`` is free text:
sometimes a slug (``ticket_closed``), sometimes German display text (``AutoMod-Maßnahme``), sometimes a
variable. This module adds a *contract* on top without changing the table, ``record_event`` or any of its
existing callers:

* :func:`emit` writes a new event with ``event_type = "<domain>.<action>"`` and a fixed payload envelope
  (``schema``, ``domain``, ``action``, optional ``case_ref`` and ``correlation_id``). Input is validated.
* :func:`describe` reads *any* stored row – new or legacy – into the same shape, so history shows old and
  new events together. Legacy rows are classified by their type/target, never rewritten.
* :func:`history` returns the events of one case reference or correlation ID. A case reference is only
  readable with the same right its case file already requires (``pulse_case_registry.allowed_kinds``).

``case_ref`` is the typed reference of ``pulse_case_registry`` (``BM:MOD-1A2B3C``); it is *not* a new
numbering system. When a case reference is given and no correlation ID, the reference becomes the
correlation ID, so every surface that emits for the same case is linked.
"""
from __future__ import annotations

import json
import logging
import re

import pulse_case_registry as registry
import pulse_policy as policy

log = logging.getLogger("pulse.events")

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 8192
MAX_HISTORY = 500

DOMAINS = {
    "ticket": "Ticket",
    "moderation": "Moderation",
    "antinuke": "Anti-Nuke",
    "automation": "Automation",
    "task": "Aufgabe",
    "handover": "Übergabe",
    "announcement": "Ankündigung",
    "promotion": "Beförderung",
    "team": "Team",
    "privacy": "Datenschutz",
    "recruiting": "Recruiting",
    "panel": "Dashboard-Audit",
    "system": "System",
    "other": "Sonstiges",
}

_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TYPE = re.compile(r"^([a-z]+)\.([a-z][a-z0-9_]{0,63})$")

# Legacy classification: first matching slug prefix, then target_type. Anything else is "other".
_LEGACY_PREFIX = (
    ("ticket_", "ticket"), ("task_", "task"), ("handover_", "handover"), ("announcement_", "announcement"),
    ("promotion_", "promotion"), ("team_", "team"), ("status_", "team"), ("antinuke", "antinuke"),
)
_LEGACY_TARGET = {
    "ticket": "ticket", "task": "task", "handover": "handover", "announcement": "announcement",
    "promotion": "promotion", "team_member": "team", "privacy_request": "privacy", "automation": "automation",
    "panel": "panel", "suite": "panel", "ultimate": "panel",
}


# Correlation IDs of the form "<domain>:<native id>" link every surface that touches the same object. Readers
# with the matching policy action may follow such an ID without being administrators.
CORRELATION_ACTION = {"ticket": "ticket.manage", "task": "task.manage"}


def correlation_for(domain, native_id) -> str:
    """``correlation_for("ticket", "ticket_ab12")`` -> ``"ticket:ticket_ab12"`` (bounded like all correlation IDs)."""
    if domain not in CORRELATION_ACTION:
        raise ValueError(f"Keine Korrelation für Domäne {domain!r}")
    return f"{domain}:{str(native_id or '').strip()}"[:128]


def slug(text) -> str:
    """Lower-case ASCII slug for legacy display text (``AutoMod-Maßnahme`` → ``automod_massnahme``)."""
    raw = str(text or "").strip().lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    out = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return out[:64] or "unknown"


def classify(event_type, target_type="") -> tuple[str, str]:
    """(domain, action) for any stored ``event_type``; new ``<domain>.<action>`` types are read as written."""
    text = str(event_type or "")
    match = _TYPE.match(text)
    if match and match.group(1) in DOMAINS:
        return match.group(1), match.group(2)
    action = slug(text)
    for prefix, domain in _LEGACY_PREFIX:
        if action.startswith(prefix):
            return domain, action
    return _LEGACY_TARGET.get(str(target_type or ""), "other"), action


# --------------------------------------------------------------------------- writing

def build_payload(domain, action, payload=None, *, case_ref=None, correlation_id=None) -> tuple[dict, str]:
    """Validate and return ``(payload envelope, event_type)``. Raises ``ValueError`` on invalid input."""
    if domain not in DOMAINS:
        raise ValueError(f"Unbekannte Ereignis-Domäne: {domain!r}")
    if not isinstance(action, str) or not _ACTION.match(action):
        raise ValueError(f"Ungültige Ereignis-Aktion: {action!r}")
    if payload is None:
        body: dict = {}
    elif isinstance(payload, dict):
        body = dict(payload)
    else:
        raise ValueError("Der Ereignis-Payload muss ein Objekt sein.")
    reserved = {"schema", "domain", "action", "case_ref", "correlation_id"} & set(body)
    if reserved:
        raise ValueError(f"Reservierte Payload-Felder: {', '.join(sorted(reserved))}")
    ref = None
    if case_ref:
        parsed = registry.parse_ref(case_ref)
        if not parsed:
            raise ValueError("Ungültige Fallreferenz.")
        ref = registry.make_ref(*parsed)
    correlation = str(correlation_id or "").strip()[:128] or ref
    envelope = {"schema": SCHEMA_VERSION, "domain": domain, "action": action, **body}
    if ref:
        envelope["case_ref"] = ref
    if correlation:
        envelope["correlation_id"] = correlation
    if len(json.dumps(envelope, ensure_ascii=False, default=str).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Der Ereignis-Payload ist zu groß.")
    return envelope, f"{domain}.{action}"


def emit(domain, action, *, target_type="", target_id="", actor_id="", actor_name="", payload=None,
         case_ref=None, correlation_id=None, recorder=None):
    """Record one contract event and return its ID. ``recorder`` defaults to ``pulse_db.record_event``."""
    envelope, event_type = build_payload(domain, action, payload, case_ref=case_ref, correlation_id=correlation_id)
    if recorder is None:
        import pulse_db
        recorder = pulse_db.record_event
    event_id = recorder(event_type, target_type, target_id, actor_id, actor_name, envelope)
    try:  # wake live streams (pulse_eventbus); readers also poll, so a failure here only delays delivery
        import pulse_eventbus
        pulse_eventbus.bump()
    except Exception:  # noqa: BLE001
        log.debug("Eventbus nicht erreichbar", exc_info=True)
    return event_id


def emit_safe(*args, **kwargs):
    """Like :func:`emit`, but an audit failure never breaks the action being audited; returns ``None`` then."""
    try:
        return emit(*args, **kwargs)
    except Exception:  # noqa: BLE001 - audit must not take down the caller
        log.warning("Ereignis konnte nicht gespeichert werden", exc_info=True)
        return None


# --------------------------------------------------------------------------- reading

def describe(row) -> dict:
    """Uniform view of a stored ``events`` row (new or legacy)."""
    row = dict(row)
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    domain, action = classify(row.get("event_type"), row.get("target_type"))
    return {
        "id": row.get("id", ""),
        "at": row.get("created_at", ""),
        "domain": domain,
        "domain_label": DOMAINS[domain],
        "action": action,
        "target_type": row.get("target_type", ""),
        "target_id": row.get("target_id", ""),
        "actor_id": row.get("actor_id", ""),
        "actor_name": row.get("actor_name", ""),
        "case_ref": payload.get("case_ref", ""),
        "correlation_id": payload.get("correlation_id", ""),
        "schema": payload.get("schema", 0),
        "legacy": payload.get("schema") != SCHEMA_VERSION,
        "payload": payload,
    }


def _may_follow(correlation, perms) -> bool:
    if not isinstance(perms, dict):
        return False
    if perms.get("is_admin") is True:
        return True
    prefix, _, rest = str(correlation).partition(":")
    action = CORRELATION_ACTION.get(prefix)
    return bool(rest) and action is not None and policy.can(perms, action)


def history(*, case_ref=None, correlation_id=None, domain=None, perms=None, limit=100) -> list[dict]:
    """Newest-first events for a case reference and/or correlation ID.

    At least one of ``case_ref``/``correlation_id`` is required (no unbounded dump of the audit table).
    A ``case_ref`` is only served when ``perms`` allow that case kind; otherwise ``PermissionError``.
    """
    if domain is not None and domain not in DOMAINS:
        raise ValueError(f"Unbekannte Ereignis-Domäne: {domain!r}")
    ref = None
    if case_ref:
        parsed = registry.parse_ref(case_ref)
        if not parsed:
            raise ValueError("Ungültige Fallreferenz.")
        if parsed[0] not in registry.allowed_kinds(perms):
            raise PermissionError("Keine Berechtigung für diese Fallart.")
        ref = registry.make_ref(*parsed)
    correlation = str(correlation_id or "").strip()[:128] or ref
    if not correlation:
        raise ValueError("Fallreferenz oder Korrelations-ID erforderlich.")
    if not ref and not _may_follow(correlation, perms):
        # A bare correlation ID can span every domain; only administrators - or holders of the right of an
        # object-bound ID like "ticket:<id>" - may follow it (fail-closed).
        raise PermissionError("Eine Korrelations-ID ohne Fallreferenz ist nur für Administratoren lesbar.")
    limit = max(1, min(int(limit), MAX_HISTORY))
    import pulse_db
    with pulse_db.connect() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE json_valid(payload_json) AND json_extract(payload_json,'$.correlation_id')=? "
            "ORDER BY created_at DESC LIMIT ?", (correlation, limit * 4)).fetchall()
    out = [describe(r) for r in rows]
    if not ref and not (isinstance(perms, dict) and perms.get("is_admin") is True):
        out = [e for e in out if e["domain"] == str(correlation).partition(":")[0]]  # never beyond the right's own domain
    if domain:
        out = [e for e in out if e["domain"] == domain]
    return out[:limit]
