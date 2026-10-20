"""D-08: retention periods (E-9), protocolled clean-up and data export per Discord ID.

* Retention is **off** until an administrator enables it (Verwaltung → Datenschutz).
* Applications: decided ones (not pending/interview) older than `applications_days` (E-9: 6 months).
* Audit log (`audit_logs.json`): entries older than `audit_days` (E-9: 2 years).
* AI moderation cases: `cogs/ai_moderation.py` already removes decided cases after 30 days – stricter
  than E-9 (3 months), so nothing is changed there.
* Ticket transcripts are kept without limit (E-7).
* A clean-up only runs if a complete backup (pulse_backup) is at most 2 days old, and it is written
  to the audit log with counts. A dry run (`plan`) never changes anything.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pulse_db
from pulse_time import age_days, parse_timestamp

SETTING_KEY = "data_retention"
DEFAULTS = {"enabled": False, "applications_days": 183, "audit_days": 730, "contact": "", "last_run": "", "last_report": {}}
OPEN_APPLICATION_STATES = {"pending", "interview"}
MAX_BACKUP_AGE = timedelta(days=2)


def load_settings() -> dict:
    stored = pulse_db.get_setting(SETTING_KEY, {}) or {}
    settings = dict(DEFAULTS)
    if isinstance(stored, dict):
        settings.update({k: v for k, v in stored.items() if k in DEFAULTS})
    for key, low, high in (("applications_days", 30, 3650), ("audit_days", 90, 3650)):
        try:
            settings[key] = max(low, min(high, int(settings[key])))
        except (TypeError, ValueError):
            settings[key] = DEFAULTS[key]
    settings["enabled"] = bool(settings["enabled"])
    settings["contact"] = str(settings.get("contact") or "")[:300]
    return settings


def save_settings(values: dict) -> dict:
    settings = load_settings()
    settings.update({k: v for k, v in values.items() if k in DEFAULTS})
    pulse_db.set_setting(SETTING_KEY, settings)
    return load_settings()


def _application_stamp(app: dict):
    return app.get("decided_at") or app.get("updated_at") or app.get("created_at")


def expired_applications(apps: dict, days: int, now: datetime | None = None) -> list[str]:
    out = []
    for app_id, app in (apps or {}).items():
        if not isinstance(app, dict) or str(app.get("status")) in OPEN_APPLICATION_STATES:
            continue
        age = age_days(_application_stamp(app), now)
        if age is not None and age > days:
            out.append(app_id)
    return out


def expired_audit_entries(entries: list, days: int, now: datetime | None = None) -> list[int]:
    out = []
    for index, entry in enumerate(entries or []):
        age = age_days(entry.get("timestamp") if isinstance(entry, dict) else None, now)
        if age is not None and age > days:
            out.append(index)
    return out


def plan(ws, settings: dict | None = None, now: datetime | None = None) -> dict:
    """Dry run: how many records the current settings would remove."""
    settings = settings or load_settings()
    apps = ws.load_json(ws.APPS_FILE, {})
    audit = ws.load_json(ws.AUDIT_FILE, [])
    return {"applications": len(expired_applications(apps if isinstance(apps, dict) else {}, settings["applications_days"], now)),
            "audit": len(expired_audit_entries(audit if isinstance(audit, list) else [], settings["audit_days"], now))}


def recent_full_backup(ws, now: datetime | None = None) -> str:
    import pulse_backup
    backups = pulse_backup.list_backups(ws.full_backup_dir())
    if not backups:
        return ""
    newest = datetime.fromtimestamp(backups[0].stat().st_mtime, tz=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return backups[0].name if current - newest <= MAX_BACKUP_AGE else ""


def run(ws, now: datetime | None = None, force: bool = False) -> dict:
    """Apply the retention rules once. Requires `enabled` (or force) and a fresh complete backup."""
    settings = load_settings()
    report = {"ran": False, "applications": 0, "audit": 0, "reason": ""}
    if not (settings["enabled"] or force):
        report["reason"] = "Aufbewahrungsfristen sind ausgeschaltet."
        return report
    backup = recent_full_backup(ws, now)
    if not backup:
        report["reason"] = "Kein Komplett-Backup der letzten 2 Tage – Bereinigung übersprungen."
        ws.log_audit("System", "0", "Datenbereinigung übersprungen", report["reason"])
        save_settings({"last_run": (now or datetime.now(timezone.utc)).date().isoformat(), "last_report": report})  # retry tomorrow
        return report
    with ws._io_lock:  # load, filter and save without anything in between
        apps = ws.load_json(ws.APPS_FILE, {})
        if isinstance(apps, dict):
            gone = expired_applications(apps, settings["applications_days"], now)
            if gone:
                ws.save_json(ws.APPS_FILE, {k: v for k, v in apps.items() if k not in set(gone)})
            report["applications"] = len(gone)
        audit = ws.load_json(ws.AUDIT_FILE, [])
        if isinstance(audit, list):
            drop = set(expired_audit_entries(audit, settings["audit_days"], now))
            if drop:
                ws.save_json(ws.AUDIT_FILE, [e for i, e in enumerate(audit) if i not in drop])
            report["audit"] = len(drop)
    report["ran"] = True
    report["backup"] = backup
    ws.log_audit("System", "0", "Datenbereinigung (Aufbewahrungsfristen)",
                 f"Bewerbungen entfernt: {report['applications']} (> {settings['applications_days']} Tage) · "
                 f"Audit-Einträge entfernt: {report['audit']} (> {settings['audit_days']} Tage) · Backup davor: {backup}")
    save_settings({"last_run": (now or datetime.now(timezone.utc)).date().isoformat(), "last_report": report})
    return report


def due_today(now: datetime | None = None) -> bool:
    settings = load_settings()
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    return settings["enabled"] and settings.get("last_run") != today


def export_person(ws, discord_id: str) -> dict:
    """Auskunft: everything Pulse stores about one Discord ID (read only)."""
    uid = str(discord_id).strip()
    team = ws.load_json(ws.DATA_FILE, {})
    apps = ws.load_json(ws.APPS_FILE, {})
    shifts = ws.load_json(ws.SHIFTS_FILE, {})
    audit = ws.load_json(ws.AUDIT_FILE, [])
    result = {
        "discord_id": uid,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "team_file": team.get(uid) if isinstance(team, dict) else None,
        "applications": {k: v for k, v in (apps or {}).items() if isinstance(v, dict) and str(v.get("user_id")) == uid},
        "shifts": {
            "active": (shifts.get("active_shifts", {}) or {}).get(uid) if isinstance(shifts, dict) else None,
            "history": [h for h in (shifts.get("history", []) if isinstance(shifts, dict) else []) if str(h.get("mod_id")) == uid],
        },
        "audit_as_actor": [e for e in (audit or []) if isinstance(e, dict) and str(e.get("actor_id")) == uid],
        "tickets": [t for t in pulse_db.list_tickets(limit=100000) if str(t.get("user_id")) == uid or str(t.get("claimed_by_id")) == uid],
        "events": [e for e in pulse_db.events(limit=100000) if str(e.get("actor_id")) == uid or str(e.get("target_id")) == uid],
        "absences": [],
    }
    try:
        if os.path.exists(ws.DB_ABMELDUNGEN):
            con = sqlite3.connect(ws.DB_ABMELDUNGEN, timeout=5)
            con.row_factory = sqlite3.Row
            try:
                result["absences"] = [dict(r) for r in con.execute("SELECT * FROM abmeldungen WHERE CAST(user_id AS TEXT)=?", (uid,))]
            finally:
                con.close()
    except sqlite3.Error as exc:
        result["absences_error"] = type(exc).__name__
    return result


def privacy_notice_html(esc) -> str:
    """Notice for /apply: which data, purpose, who, how long, contact."""
    settings = load_settings()
    months = round(settings["applications_days"] / 30.4)
    keep = (f"Nach der Entscheidung wird deine Bewerbung nach etwa {months} Monaten automatisch gelöscht."
            if settings["enabled"] else "Abgeschlossene Bewerbungen werden von der Leitung regelmäßig gelöscht.")
    contact = settings["contact"] or "die Serverleitung über ein Support-Ticket"
    ai = (" Hinweis: Auf dem Discord-Server ist eine KI-gestützte Moderation aktiv; sie liefert nur Verdachtsfälle, "
          "Entscheidungen trifft immer ein Mensch.") if str(os.getenv("PULSE_AI_MODERATION_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"} else ""
    return (
        '<details class="rounded-xl border border-slate-200 dark:border-slate-700 p-3 text-[11px] text-slate-500 dark:text-slate-400">'
        '<summary class="cursor-pointer font-semibold">🔒 Datenschutzhinweis</summary><p class="mt-2">'
        "Gespeichert werden deine Discord-ID, dein Discord-Name und Profilbild, deine Antworten, das automatisch "
        "berechnete Testergebnis und der Zeitpunkt. Zweck ist ausschließlich die Auswahl neuer Teammitglieder. "
        "Einsehen können die Bewerbung nur Personen mit Bewerbungs- oder Leitungsrechten im Team-Dashboard. "
        f"{esc(keep)}{esc(ai)} Auskunft oder Löschung: {esc(contact)}.</p></details>"
    )
