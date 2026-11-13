"""Audit #11/#12: typed case references and one moderation history across all case files.

Pulse keeps four separate case files (each with its own rules, retention and backup):

* ``BM`` – citizen moderation (/mod), table ``mod_cases`` in pulse.db, IDs ``MOD-XXXXXX``
* ``TF`` – team/player cases, table ``ultimate_cases`` (+ ``ultimate_case_events``), numbers ``PX-<year>-<n>``
* ``TW`` – team warnings, ``warns_list`` in team_data.json, IDs ``warn_xxxxxx``
* ``KI`` – AI moderation reviews, ai_moderation_cases.json, 10-character hex IDs

This module does **not** copy, renumber or migrate anything, and it is not a shared numbering system:
each case file keeps issuing its own IDs. It gives every existing case a typed, collision-free
reference (``<KIND>:<native id>``, e.g. ``BM:MOD-1A2B3C``), resolves such references and merges the
*events* of one person's cases (created, closed, appealed, revoked, decided, …) into a single
chronological history. Cases are only linked through the stored Discord ID of the person – never by
similar names or timestamps. Every source is read-only, shown only with the right its own view
already requires, and a broken source is reported instead of hiding or breaking the others.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

KINDS = {
    "BM": "Bürger-Moderation",
    "TF": "Team-/Spielerfall",
    "TW": "Teamwarnung",
    "KI": "KI-Moderation",
}
KIND_ORDER = ("BM", "TF", "TW", "KI")
MAX_CASES_PER_SOURCE = 200
MAX_EVENTS_PER_CASE = 50

_BARE_PATTERNS = (
    ("BM", re.compile(r"^MOD-[0-9A-F]{6}$", re.I)),
    ("TF", re.compile(r"^PX-\d{4}-\d{1,6}$", re.I)),
    ("TW", re.compile(r"^warn_[0-9a-f]{6,10}$", re.I)),
)
_NATIVE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")

_STATUS_LABELS = {
    "active": "aktiv", "expired": "abgelaufen", "failed": "fehlgeschlagen", "revoked": "widerrufen",
    "open": "offen", "investigating": "in Prüfung", "in_progress": "in Bearbeitung", "waiting": "wartet",
    "closed": "geschlossen", "pending": "offen", "resolved": "entschieden",
}
EVENT_LABELS = {
    "created": "Fall angelegt",
    "closed": "Fall abgeschlossen",
    "failed": "Maßnahme fehlgeschlagen",
    "appeal_submitted": "Einspruch eingereicht",
    "appeal_accepted": "Einspruch angenommen",
    "appeal_rejected": "Einspruch abgelehnt",
    "updated": "Fall aktualisiert",
    "attachment": "Beweis angehängt",
    "player_linked": "Spieler verknüpft",
    "revoked": "Verwarnung widerrufen",
    "expired": "Verwarnung abgelaufen",
    "decided": "Entscheidung getroffen",
}


class SourceUnavailable(Exception):
    """A case file exists but cannot be read (damaged, locked, unexpected format)."""


# --------------------------------------------------------------------------- references

def make_ref(kind: str, native_id) -> str:
    kind = str(kind or "").upper()
    if kind not in KINDS:
        raise ValueError("Unbekannte Fallart.")
    return f"{kind}:{native_id}"


def parse_ref(text) -> tuple[str, str] | None:
    """``BM:MOD-1A2B3C`` → (``BM``, ``MOD-1A2B3C``).

    Bare IDs are only accepted when their format belongs to exactly one case file. A bare AI ID
    (10 hex characters) is ambiguous by design and must be written as ``KI:<id>``.
    """
    raw = str(text or "").strip().strip("`")
    if not raw or len(raw) > 60:
        return None
    if ":" in raw:
        kind, _, native = raw.partition(":")
        kind, native = kind.strip().upper(), native.strip()
        if kind not in KINDS or not _NATIVE_ID.match(native):
            return None
        if kind in ("BM", "TF"):
            native = native.upper()
        elif kind == "TW":
            native = native.lower()
        return kind, native
    for kind, pattern in _BARE_PATTERNS:
        if pattern.match(raw):
            return kind, (raw.upper() if kind in ("BM", "TF") else raw.lower())
    return None


def allowed_kinds(perms: dict | None) -> set[str]:
    """Same rights as the existing views of each case file; nothing is widened. Fail-closed."""
    p = perms if isinstance(perms, dict) else {}
    admin = p.get("is_admin") is True
    kinds: set[str] = set()
    if admin or p.get("can_moderate_kicks") is True or p.get("can_moderate_bans") is True:
        kinds |= {"BM", "KI"}  # /mod-cases and the AI review need a moderation right
    if admin or p.get("can_warn") is True:
        kinds.add("TW")
    if admin or p.get("can_promote") is True:
        kinds.add("TF")  # /ultimate/cases is for leaders only
    return kinds


# --------------------------------------------------------------------------- helpers

def _ts(value) -> datetime | None:
    """Parse ISO (naive = UTC, as Pulse stores it) or the team-warning format (Berlin local time)."""
    raw = str(value or "").strip()
    if not raw or raw.upper() == "N/A":
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    try:  # team warnings store the local Berlin time as "dd.mm.YYYY HH:MM"
        return datetime.strptime(raw, "%d.%m.%Y %H:%M").replace(tzinfo=BERLIN).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _iso(value) -> str:
    parsed = _ts(value)
    return parsed.isoformat(timespec="seconds") if parsed else ""


def _text(value, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _case(kind, native_id, *, title, status, detail="", actor="", created_at="", url="", person_id="", extra=None):
    """One case as shown by ``resolve``."""
    status = str(status or "")
    case = {
        "ref": make_ref(kind, native_id),
        "kind": kind,
        "kind_label": KINDS[kind],
        "native_id": str(native_id),
        "person_id": str(person_id or ""),
        "title": _text(title, 160),
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status or "–"),
        "detail": _text(detail, 300),
        "actor": _text(actor, 100),
        "created_at": _iso(created_at),
        "url": url,
    }
    if extra:
        case.update(extra)
    return case


# Backwards-compatible name used by early callers/tests.
_entry = _case


def _event(case: dict, event: str, at, *, actor="", detail="") -> dict:
    """One history line: the case it belongs to, what happened, when and by whom."""
    return {
        **case,
        "event": event,
        "event_label": EVENT_LABELS.get(event, _text(event, 40) or "Ereignis"),
        "at": _iso(at),
        "event_actor": _text(actor, 100),
        "event_detail": _text(detail, 200),
    }


# --------------------------------------------------------------------------- BM: citizen moderation

def _bm_case(case: dict, appeal: dict | None = None) -> dict:
    import pulse_mod_cases as mc
    extra = {"appeal_status": str(appeal.get("status") or "")} if appeal else {}
    return _case(
        "BM", case["id"],
        title=mc.ACTIONS.get(case.get("action"), case.get("action")),
        status=case.get("status"), detail=case.get("reason"), actor=case.get("actor_name"),
        created_at=case.get("created_at"), url=f"/mod-cases?q={case['id']}", person_id=case.get("target_id"),
        extra=extra,
    )


def _bm_appeal(case_id) -> dict | None:
    import pulse_mod_cases as mc
    try:
        return mc.get_appeal(case_id)
    except Exception:
        return None


def _bm_events(row: dict) -> list[dict]:
    appeal = _bm_appeal(row["id"])
    case = _bm_case(row, appeal)
    events = [_event(case, "created", row.get("created_at"), actor=row.get("actor_name"), detail=row.get("reason"))]
    if row.get("status") == "failed":
        events.append(_event(case, "failed", row.get("closed_at") or row.get("created_at"),
                             actor=row.get("closed_by"), detail=row.get("close_note")))
    elif row.get("closed_at"):
        events.append(_event(case, "closed", row.get("closed_at"), actor=row.get("closed_by"), detail=row.get("close_note")))
    if appeal:
        events.append(_event(case, "appeal_submitted", appeal.get("submitted_at"), actor="Betroffene Person"))
        if appeal.get("status") in ("accepted", "rejected"):
            events.append(_event(case, f"appeal_{appeal['status']}", appeal.get("reviewed_at"),
                                 actor=appeal.get("reviewed_by_name"), detail=appeal.get("decision_note")))
    return events


def load_bm(user_id, guild_id=None) -> list[dict]:
    import pulse_mod_cases as mc
    rows = mc.list_cases(str(user_id), limit=MAX_CASES_PER_SOURCE)
    if guild_id is not None:
        rows = [r for r in rows if str(r.get("guild_id")) == str(guild_id)]
    return [event for row in rows if row.get("id") for event in _bm_events(row)]


# --------------------------------------------------------------------------- TF: team/player cases

_TF_COLUMNS = "id,case_no,title,category,priority,status,subject_id,created_by_name,created_at,closed_at"


def _tf_case(row: dict) -> dict:
    return _case(
        "TF", row["case_no"],
        title=row.get("title"), status=row.get("status"), detail=row.get("category"),
        actor=row.get("created_by_name"), created_at=row.get("created_at"),
        url=f"/ultimate/cases/{row['id']}", person_id=row.get("subject_id"),
        extra={"priority": str(row.get("priority") or "")},
    )


def load_tf(user_id) -> list[dict]:
    import pulse_ultimate
    pulse_ultimate.setup()
    with pulse_ultimate.cx() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT {_TF_COLUMNS} FROM ultimate_cases WHERE subject_id=? ORDER BY created_at DESC LIMIT ?",
            (str(user_id), MAX_CASES_PER_SOURCE),
        ).fetchall()]
        events: list[dict] = []
        for row in rows:
            if not row.get("case_no"):
                continue
            case = _tf_case(row)
            stored = c.execute(
                "SELECT actor_name,event_type,created_at FROM ultimate_case_events "
                "WHERE case_id=? ORDER BY created_at ASC LIMIT ?",
                (row["id"], MAX_EVENTS_PER_CASE),
            ).fetchall()
            types = {str(e["event_type"]) for e in stored}
            if "created" not in types:  # cases created before the event log existed
                events.append(_event(case, "created", row.get("created_at"), actor=row.get("created_by_name")))
            # Event details stay in the case file (they can hold internal notes); the history names the step.
            events.extend(_event(case, str(e["event_type"]), e["created_at"], actor=e["actor_name"]) for e in stored)
            if row.get("closed_at") and "closed" not in types:
                events.append(_event(case, "closed", row.get("closed_at")))
    return events


# --------------------------------------------------------------------------- TW: team warnings

def _warn_status(warn: dict) -> str:
    if warn.get("revoked_at"):
        return "revoked"
    if warn.get("expired_at") or warn.get("active", True) is False:
        return "expired"
    expiry = _ts(warn.get("expires_at"))
    if expiry and expiry <= datetime.now(timezone.utc):
        return "expired"
    return "active"


def _tw_case(user_id, warn: dict) -> dict:
    return _case(
        "TW", str(warn.get("id")).lower(),
        title="Team-Verwarnung", status=_warn_status(warn), detail=warn.get("reason"),
        actor=warn.get("by"), created_at=warn.get("date"), url=f"/member/{user_id}", person_id=user_id,
    )


def _tw_events(user_id, warn: dict) -> list[dict]:
    case = _tw_case(user_id, warn)
    events = [_event(case, "created", warn.get("date"), actor=warn.get("by"), detail=warn.get("reason"))]
    if warn.get("revoked_at"):
        events.append(_event(case, "revoked", warn.get("revoked_at"), actor=warn.get("revoked_by"),
                             detail=warn.get("revoked_reason")))
    elif case["status"] == "expired":
        events.append(_event(case, "expired", warn.get("expired_at") or warn.get("expires_at"),
                             detail=warn.get("expired_reason")))
    return events


def _team_data() -> dict:
    import webserver
    data = webserver.load_json(webserver.DATA_FILE, {})
    if not isinstance(data, dict):
        raise SourceUnavailable("team_data.json hat ein unerwartetes Format.")
    return data


def _warns_of(entry) -> list[dict]:
    warns = entry.get("warns_list") if isinstance(entry, dict) else None
    # Read-only: legacy entries without an ID are skipped instead of being normalized and saved here.
    return [w for w in (warns if isinstance(warns, list) else []) if isinstance(w, dict) and w.get("id")]


def load_tw(user_id) -> list[dict]:
    warns = _warns_of(_team_data().get(str(user_id)))[-MAX_CASES_PER_SOURCE:]
    return [event for warn in warns for event in _tw_events(user_id, warn)]


# --------------------------------------------------------------------------- KI: AI moderation reviews

def _ki_case(case: dict) -> dict:
    # The message content stays in the AI review; the history only shows the assessment and the decision.
    return _case(
        "KI", case.get("id"),
        title=f"Verdachtsfall in #{_text(case.get('channel_name'), 60) or 'unbekannt'}",
        status=case.get("status"),
        detail=f"KI-Einschätzung ({_text(case.get('severity'), 20) or 'unbekannt'}): {_text(case.get('reason'), 200)}",
        actor=case.get("reviewed_by") or "", created_at=case.get("created_at"), url="", person_id=case.get("user_id"),
    )


def _ki_events(raw: dict) -> list[dict]:
    case = _ki_case(raw)
    events = [_event(case, "created", raw.get("created_at"), actor="KI-Vorprüfung", detail=case["detail"])]
    if raw.get("status") == "resolved":
        events.append(_event(case, "decided", raw.get("resolved_at") or raw.get("created_at"),
                             actor=raw.get("reviewed_by"), detail=raw.get("decision")))
    return events


def _ai_cases() -> dict:
    try:
        from cogs.ai_moderation import CASES_PATH
    except Exception as exc:
        raise SourceUnavailable("KI-Moderation ist nicht geladen.") from exc
    try:
        data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}  # no AI case has been stored yet
    except (OSError, ValueError) as exc:
        raise SourceUnavailable("ai_moderation_cases.json ist nicht lesbar.") from exc
    if not isinstance(data, dict):
        raise SourceUnavailable("ai_moderation_cases.json hat ein unerwartetes Format.")
    return data


def load_ki(user_id, guild_id=None) -> list[dict]:
    rows = [
        c for c in _ai_cases().values()
        if isinstance(c, dict) and c.get("id") and str(c.get("user_id")) == str(user_id)
        and (guild_id is None or str(c.get("guild_id")) == str(guild_id))
    ]
    return [event for row in rows[-MAX_CASES_PER_SOURCE:] for event in _ki_events(row)]


# --------------------------------------------------------------------------- public API

def _sort_key(event: dict):
    # Newest first; events without a readable time go to the end instead of being dropped.
    return (bool(event.get("at")), event.get("at") or "", event["ref"], event.get("event") or "")


def person_history(user_id, perms: dict | None, *, guild_id=None, limit: int = 100) -> dict:
    """All case events of one person the viewer may see, newest first.

    Returns ``{"entries": [...], "kinds": [...], "unavailable": [...], "total": n}``. ``kinds`` are the
    case files included for this viewer, ``unavailable`` the ones that could not be read.
    """
    uid = str(user_id or "").strip()
    kinds = allowed_kinds(perms)
    visible = [k for k in KIND_ORDER if k in kinds]
    if not uid.isdigit() or not kinds:
        return {"entries": [], "kinds": visible, "unavailable": [], "total": 0}
    loaders = {
        "BM": lambda: load_bm(uid, guild_id),
        "TF": lambda: load_tf(uid),
        "TW": lambda: load_tw(uid),
        "KI": lambda: load_ki(uid, guild_id),
    }
    entries: list[dict] = []
    unavailable: list[str] = []
    for kind in visible:
        try:
            entries.extend(e for e in loaders[kind]() if e.get("kind") == kind)
        except Exception:
            unavailable.append(kind)
    entries.sort(key=_sort_key, reverse=True)
    try:
        bounded = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        bounded = 100
    return {"entries": entries[:bounded], "kinds": visible, "unavailable": unavailable, "total": len(entries)}


def _resolve(kind: str, native: str, guild_id) -> dict | None:
    if kind == "BM":
        import pulse_mod_cases as mc
        row = mc.get_case(native)
        if not row or (guild_id is not None and str(row.get("guild_id")) != str(guild_id)):
            return None
        return _bm_case(row, _bm_appeal(row["id"]))
    if kind == "TF":
        import pulse_ultimate
        pulse_ultimate.setup()
        with pulse_ultimate.cx() as c:
            row = c.execute(f"SELECT {_TF_COLUMNS} FROM ultimate_cases WHERE case_no=?", (native,)).fetchone()
        return _tf_case(dict(row)) if row else None
    if kind == "TW":
        matches = [
            (member_id, warn) for member_id, entry in _team_data().items()
            for warn in _warns_of(entry) if str(warn.get("id")).lower() == native
        ]
        # A duplicated legacy ID is ambiguous: show nothing rather than possibly the wrong person.
        return _tw_case(*matches[0]) if len(matches) == 1 else None
    row = _ai_cases().get(native)
    if not isinstance(row, dict) or str(row.get("id")) != native:
        return None
    if guild_id is not None and str(row.get("guild_id")) != str(guild_id):
        return None
    return _ki_case(row)


def resolve(ref, perms: dict | None, *, guild_id=None) -> dict | None:
    """Look up one case by its reference.

    ``None`` for unknown references, references the viewer has no right for and unreadable sources,
    so the answer never reveals whether a case exists in a case file the viewer may not see.
    """
    parsed = parse_ref(ref)
    if not parsed:
        return None
    kind, native = parsed
    if kind not in allowed_kinds(perms):
        return None
    try:
        return _resolve(kind, native, guild_id)
    except Exception:
        return None
