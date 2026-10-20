"""D-10: consistency diagnosis (report only – nothing is corrected automatically) and D-07 growth watch.

Findings are shown in the system status. Runs in the event loop because it reads discord.py caches;
all checks are cheap lookups (no Discord API calls).
"""
from __future__ import annotations

import os

from pulse_time import parse_timestamp

GROWTH_WARN_BYTES = 5 * 1024 * 1024  # JSON files are parsed on almost every page (P-03/P-05)
GROWTH_FILES = ("shifts.json", "logs.json", "applications.json", "audit_logs.json", "team_data.json")
MAX_EXAMPLES = 5


def _finding(area: str, items: list[str], ok_text: str) -> dict:
    return {"area": area, "ok": not items, "count": len(items),
            "text": ok_text if not items else f"{len(items)} Auffälligkeit(en): " + "; ".join(items[:MAX_EXAMPLES])
            + (" …" if len(items) > MAX_EXAMPLES else "")}


def _member(guild, uid):
    try:
        return guild.get_member(int(uid))
    except (TypeError, ValueError):
        return None


def check(ws, guild) -> list[dict]:
    results = []
    if guild is None:
        return [{"area": "Konsistenz", "ok": False, "count": 0, "text": "Discord-Server nicht verfügbar – Prüfung übersprungen"}]

    shifts = ws.load_json(ws.SHIFTS_FILE, {})
    active = shifts.get("active_shifts", {}) if isinstance(shifts, dict) else {}
    results.append(_finding("Aktive Schichten", [f"Schicht von {uid} ohne Mitglied auf dem Server" for uid in active if _member(guild, uid) is None],
                            "alle aktiven Schichten gehören zu Mitgliedern"))

    import pulse_db
    orphan_tickets = []
    for ticket in pulse_db.list_tickets(limit=5000):
        if str(ticket.get("status")) == "closed":
            continue
        cid = str(ticket.get("channel_id") or "")
        if cid.isdigit() and guild.get_channel(int(cid)) is None:
            orphan_tickets.append(f"Ticket {ticket.get('id')} ohne Kanal")
    results.append(_finding("Offene Tickets", orphan_tickets, "alle offenen Tickets haben einen Kanal"))

    role_ids = ws.get_warn_role_ids(ws.load_config())
    team = ws.load_json(ws.DATA_FILE, {}) if getattr(ws, "SYNC_WARN_ROLES", True) else {}
    mismatches = []
    for uid, entry in (team.items() if isinstance(team, dict) else []):
        member = _member(guild, uid)
        if member is None or not isinstance(entry, dict):
            continue
        count = len(ws.active_warns(dict(entry)))
        held = {level for level, rid in role_ids.items() if any(r.id == rid for r in getattr(member, "roles", []))}
        expected = {count} if count in role_ids else set()
        if held != expected:
            mismatches.append(f"{getattr(member, 'display_name', uid)}: {count} aktive Warnung(en), Warnrolle(n) {sorted(held) or 'keine'}")
    results.append(_finding("Warnrollen", mismatches, "Warnrollen passen zu den aktiven Warnungen"))

    bad_absences = []
    for uid, loa in (ws.get_loas() or {}).items():
        start, end = parse_timestamp(loa.get("von")), parse_timestamp(loa.get("bis"))
        if start and end and end < start:
            bad_absences.append(f"Abmeldung von {loa.get('user_name') or uid}: Ende vor Beginn")
    results.append(_finding("Abmeldungen", bad_absences, "keine Abmeldung mit Ende vor Beginn"))

    orphan_cases = []
    try:
        from cogs.voice_support import get_cases
        for key, case in (get_cases() or {}).items():
            if not isinstance(case, dict):
                continue
            if _member(guild, case.get("user_id")) is None:
                orphan_cases.append(f"Fall {case.get('case_id')}: Bürger nicht mehr auf dem Server")
            elif not str(key).startswith("waiting:") and str(key).isdigit() and guild.get_channel(int(key)) is None:
                orphan_cases.append(f"Fall {case.get('case_id')}: Sprachkanal fehlt")
    except Exception as exc:
        orphan_cases.append(f"nicht prüfbar ({type(exc).__name__})")
    results.append(_finding("Voice-Support-Fälle", orphan_cases, "keine verwaisten Voice-Fälle"))
    return results


def growth(base_dir) -> list[dict]:
    """D-07: size of the JSON files that grow without limit."""
    out = []
    for name in GROWTH_FILES:
        path = os.path.join(str(base_dir), name)
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        out.append({"name": name, "size": size, "ok": size < GROWTH_WARN_BYTES})
    return out
