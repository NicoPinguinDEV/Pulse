"""Shared shift actions for the dashboard and Discord controls (BOT-03/BOT-05)."""
from __future__ import annotations

import threading
from datetime import datetime

_ACTION_LOCK = threading.RLock()

_ACTION_ALIASES = {
    "start": "start",
    "break": "break",
    "pause": "break",
    "resume": "resume",
    "weiter": "resume",
    "end": "end",
    "ende": "end",
}


def apply_shift_action(user_id: str | int, requested_action: str, *, now: datetime | None = None) -> dict:
    """Apply one validated self-service shift action through the canonical data path.

    Dashboard and Discord both call this function. The lock prevents those two
    entry points from overwriting each other's read-modify-write operation.
    Role synchronisation and user-facing responses stay with the respective caller.
    """
    import webserver as web

    mod_id = str(user_id)
    action = _ACTION_ALIASES.get(str(requested_action or "").strip().casefold())
    if action is None:
        return {"ok": False, "message": "Diese Aktion ist gerade nicht möglich.", "status": "offline"}

    with _ACTION_LOCK:
        shifts_db = web.load_shifts()
        team_db = web.load_json(web.DATA_FILE, {})
        active = shifts_db.setdefault("active_shifts", {})
        shifts_db.setdefault("history", [])
        shift = active.get(mod_id)
        current = now or web.now_de()

        if action == "start":
            if shift:
                return {"ok": False, "message": "Du hast bereits eine laufende Schicht.",
                        "status": str(shift.get("status", "offline"))}
            if len(web.active_warns(web.user_entry(team_db, mod_id))) >= 5:
                return {"ok": False,
                        "message": "Schicht-Start gesperrt: Du hast bereits 5 aktive Verwarnungen!",
                        "status": "offline"}
            loa = web.get_loas().get(mod_id)
            leave_active = bool(loa and loa.get("active"))
            active[mod_id] = {
                "status": "online",
                "started_at_iso": current.isoformat(),
                "segment_start_iso": current.isoformat(),
                "date": current.strftime("%Y-%m-%d"),
                "accumulated_seconds": 0,
            }
            message = (
                f"Schicht gestartet. Deine Abmeldung bleibt bis {web.fmt_date(loa['bis'])} hinterlegt "
                "und wird nicht automatisch beendet."
                if leave_active
                else "Schicht gestartet – viel Erfolg!"
            )

        elif action == "break" and shift and shift.get("status") == "online":
            shift["accumulated_seconds"] = web.shift_elapsed(shift)
            shift["status"] = "break"
            shift["segment_start_iso"] = None
            message = "Pause gestartet."

        elif action == "resume" and shift and shift.get("status") == "break":
            shift["status"] = "online"
            shift["segment_start_iso"] = current.isoformat()
            message = "Schicht fortgesetzt."

        elif action == "end" and shift:
            duration = web.shift_elapsed(shift)
            shifts_db["history"].append({
                "mod_id": mod_id,
                "date": shift.get("date"),
                "duration_seconds": duration,
                "started_at_iso": shift.get("started_at_iso"),
                "ended_at_iso": current.isoformat(),
            })
            del active[mod_id]
            message = f"Schicht beendet – {web.fmt_duration(duration)} gutgeschrieben."
            try:
                message += web.shift_end_open_items_text(mod_id, current)
            except Exception as exc:
                print(f"⚠️ Schichtende-Übersicht nicht verfügbar: {type(exc).__name__}")
        else:
            return {"ok": False, "message": "Diese Aktion ist gerade nicht möglich.",
                    "status": str(shift.get("status", "offline")) if shift else "offline"}

        web.save_json(web.SHIFTS_FILE, shifts_db)
        final_shift = active.get(mod_id)
        return {
            "ok": True,
            "message": message,
            "status": str(final_shift.get("status", "offline")) if final_shift else "offline",
            "action": action,
        }


def get_shift_status(user_id: str | int) -> dict:
    """Read one member's current shift without creating or changing any records."""
    import webserver as web

    mod_id = str(user_id)
    with _ACTION_LOCK:
        shifts_db = web.load_shifts()
        shift = shifts_db.get("active_shifts", {}).get(mod_id)
        if not isinstance(shift, dict):
            return {"active": False, "status": "offline", "duration_seconds": 0}
        return {
            "active": True,
            "status": str(shift.get("status", "offline")),
            "duration_seconds": max(0, int(web.shift_elapsed(shift) or 0)),
            "started_at_iso": str(shift.get("started_at_iso") or ""),
        }
