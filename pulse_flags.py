"""Phase 7: one switch per new function (all off by default) and the maintenance mode (F-04).

Switches live in pulse.db (`settings["phase7_features"]`). A function whose switch is off behaves as if
it did not exist: its pages answer 404, its hooks do nothing. Turning a switch off never deletes data.

Maintenance mode (F-04): while active, every state-changing dashboard request from non-admins is refused
(checked in `webserver.auth`), every page shows a banner and the bot automations skip their runs
(`pulse_jobs.tracked` and the automation cycle ask `automations_paused()`). Discord commands still work –
the mode is meant for backups, restores and migrations, not for locking members out.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

SETTING_KEY = "phase7_features"
MAINTENANCE_KEY = "maintenance_mode"
CACHE_SECONDS = 5.0

# key → (plan id, label, description)
FEATURES: dict[str, tuple[str, str, str]] = {
    "rights_check": ("F-01", "Wer darf was?", "Rechte-Matrix und Simulation für eine Person (nur lesend)."),
    "visibility_check": ("F-02", "Kanal-Sichtprüfung", "Welche Kanäle sehen Unverifizierte? Nur lesend, Warnliste."),
    "settings_history": ("F-03", "Einstellungs-Historie", "Wer hat wann welche Einstellung geändert – mit Diff und Zurücksetzen."),
    "maintenance": ("F-04", "Wartungsmodus", "Banner, Schreibaktionen gesperrt, Bot-Automationen pausiert."),
    "capacity_heatmap": ("F-05", "Kapazitäts-Heatmap", "Ticket- und Voice-Aufkommen gegen Dienstzeiten je Wochentag und Stunde."),
    "promotion_readiness": ("F-06", "Beförderungs-Reife", "Hinweis nach konfigurierbaren Kriterien – entscheiden immer Menschen."),
    "application_board": ("F-07", "Bewerbungs-Board", "Bewerbungen als Spalten mit Fristen."),
    "public_status": ("F-08", "Öffentliche Statusseite", "/status ohne Login: Support offen, Warteschlange, RP-Status. Keine Personendaten."),
    "game_status": ("F-09", "Spiel-Anbindung", "Roblox-Serverstatus über die offizielle öffentliche API (PULSE_ROBLOX_UNIVERSE_ID)."),
    "my_month": ("F-10", "Mein Monat", "Persönlicher Monatsrückblick in Mein Pulse, DM am Monatsanfang nur mit Opt-in."),
}

_cache: dict = {"at": 0.0, "flags": None, "maintenance": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def invalidate() -> None:
    _cache.update(at=0.0, flags=None, maintenance=None)


def _load() -> None:
    if _cache["flags"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return
    import pulse_db
    try:
        flags = pulse_db.get_setting(SETTING_KEY, {}) or {}
        maintenance = pulse_db.get_setting(MAINTENANCE_KEY, {}) or {}
    except Exception:  # database not ready (tests, start-up) → everything off
        flags, maintenance = {}, {}
    _cache.update(at=time.monotonic(), flags=flags if isinstance(flags, dict) else {},
                  maintenance=maintenance if isinstance(maintenance, dict) else {})


def enabled(key: str) -> bool:
    if key not in FEATURES:
        return False
    _load()
    return bool(_cache["flags"].get(key))


def states() -> dict[str, bool]:
    _load()
    return {key: bool(_cache["flags"].get(key)) for key in FEATURES}


def set_enabled(key: str, on: bool) -> None:
    if key not in FEATURES:
        raise ValueError("Unbekannte Funktion.")
    import pulse_db
    flags = dict(pulse_db.get_setting(SETTING_KEY, {}) or {})
    flags[key] = bool(on)
    pulse_db.set_setting(SETTING_KEY, flags)
    if key == "maintenance" and not on:
        set_maintenance(False, "", "")  # switching the function off must never leave the dashboard locked
    invalidate()


def require(key: str) -> None:
    """For routes: a switched-off function does not exist (404)."""
    if not enabled(key):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Diese Funktion ist nicht eingeschaltet (Verwaltung → Neue Funktionen).")


# --------------------------------------------------------------------------- F-04 maintenance
def maintenance() -> dict:
    """{"active": bool, "message": str, "since": iso, "by": name} – inactive while the function is off."""
    _load()
    data = _cache["maintenance"] or {}
    active = bool(data.get("active")) and bool(_cache["flags"].get("maintenance"))
    return {"active": active, "message": str(data.get("message") or ""), "since": str(data.get("since") or ""),
            "by": str(data.get("by") or "")}


def set_maintenance(active: bool, message: str, actor: str) -> dict:
    import pulse_db
    value = {"active": bool(active), "message": str(message or "").strip()[:300],
             "since": _now_iso() if active else "", "by": str(actor or "")[:80] if active else ""}
    pulse_db.set_setting(MAINTENANCE_KEY, value)
    invalidate()
    return maintenance()


def automations_paused() -> bool:
    return maintenance()["active"]


WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Always allowed while in maintenance: signing out and switching the mode itself.
MAINTENANCE_ALLOWED_PATHS = ("/logout", "/admin/features")


def blocks_write(method: str, path: str, is_admin: bool) -> bool:
    if is_admin or str(method).upper() not in WRITE_METHODS:
        return False
    if any(str(path).startswith(p) for p in MAINTENANCE_ALLOWED_PATHS):
        return False
    return maintenance()["active"]


def banner_html(esc) -> str:
    state = maintenance()
    if not state["active"]:
        return ""
    text = state["message"] or "Pulse wird gerade gewartet. Ansehen geht, Änderungen sind kurz gesperrt."
    return ('<div role="status" class="pulse-card" style="border-left:4px solid #f59e0b;padding:12px 16px;margin-bottom:14px">'
            f'<strong>🛠 Wartungsmodus</strong> – {esc(text)}</div>')
