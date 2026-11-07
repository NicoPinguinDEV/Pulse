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
from contextlib import closing
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pulse_db
from pulse_time import age_days

SETTING_KEY = "data_retention"
APPLICATION_DRAFT_TTL_DAYS = 30
DEFAULTS = {"enabled": False, "applications_days": 183, "audit_days": 730, "contact": "", "last_run": "", "last_report": {}}
OPEN_APPLICATION_STATES = {"pending", "in_review", "interview"}
# Only an exact, explicitly completed rejection is currently retention-eligible.
# Unknown/missing statuses are protected until a product decision defines their meaning.
RETENTION_ELIGIBLE_APPLICATION_STATES = {"rejected"}
# Accepted/hired remain protected because they can be used as legacy employment evidence.
KEPT_APPLICATION_STATES = OPEN_APPLICATION_STATES | {"hired", "accepted"}
# Phase 15.6e: all destructive retention remains fail-closed until linked multi-store
# cleanup, journaled rollback and a separately approved policy are implemented.
RETENTION_MUTATIONS_ENABLED = False
MAX_BACKUP_AGE = timedelta(days=2)
EXPORT_ROW_LIMIT = 5000
_ID_COLUMNS = ("user_id", "member_id", "actor_id", "target_id", "mod_id", "creator_id", "assignee_id",
               "claimed_by_id", "closed_by_id", "staff_id", "reporter_id", "discord_id",
               "applicant_id", "interviewer_id", "created_by_id", "approved_by_id")


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
    """Return only explicitly eligible rejected records; ambiguous states are protected."""
    out = []
    for app_id, app in (apps or {}).items():
        if not isinstance(app, dict) or app.get("status") not in RETENTION_ELIGIBLE_APPLICATION_STATES:
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


def _read_only_connection(path):
    """Open an existing SQLite store without creating files or applying migrations."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source.name)
    uri = source.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def plan_linked_retention(ws, settings: dict | None = None, now: datetime | None = None) -> dict:
    """Read-only cross-store preview for expired applications and their explicit references.

    This function never removes or updates records. It intentionally reports blockers
    instead of claiming a deletion is safe when audit links are only free text.
    """
    settings = settings or load_settings()
    raw_apps = ws.load_json(ws.APPS_FILE, {})
    apps = raw_apps if isinstance(raw_apps, dict) else {}
    audit = ws.load_json(ws.AUDIT_FILE, [])
    team = ws.load_json(ws.DATA_FILE, {})
    audit_entries = audit if isinstance(audit, list) else []
    unstructured_recruiting_audit = sum(
        isinstance(entry, dict)
        and any(token in str(entry.get("action") or "").casefold()
                for token in ("bewerb", "application", "interview", "recruit"))
        and not any(str(entry.get(field) or "").strip()
                    for field in ("application_id", "subject_id", "target_id", "audit_ref"))
        for entry in audit_entries
    )
    candidate_ids = expired_applications(apps, settings["applications_days"], now)
    candidate_set = set(candidate_ids)
    candidate_users = {
        str(apps[app_id].get("user_id") or "").strip()
        for app_id in candidate_ids
        if isinstance(apps.get(app_id), dict) and str(apps[app_id].get("user_id") or "").strip()
    }
    event_ids = {
        str(apps[app_id].get("interview_event_id") or "").strip()
        for app_id in candidate_ids
        if isinstance(apps.get(app_id), dict) and str(apps[app_id].get("interview_event_id") or "").strip()
    }
    all_event_owners: dict[str, set[str]] = {}
    for app_id, app in apps.items():
        if isinstance(app, dict):
            event_id = str(app.get("interview_event_id") or "").strip()
            if event_id:
                all_event_owners.setdefault(event_id, set()).add(str(app_id))

    result = {
        "read_only": True,
        "deletion_performed": False,
        "deletion_supported": False,
        "deletion_allowed": False,
        "candidate_applications": len(candidate_ids),
        "linked_interview_rows": 0,
        "interview_rows_by_applicant": 0,
        "linked_calendar_events": 0,
        "shared_calendar_event_references": sum(
            len(all_event_owners.get(event_id, set())) > 1 for event_id in event_ids
        ),
        "unmatched_calendar_references": 0,
        "linked_suite_audit_rows": 0,
        "legacy_audit_entries_total": len(audit_entries),
        "legacy_recruiting_audit_entries_unstructured": unstructured_recruiting_audit,
        "employment_event_collisions": sum(
            isinstance(team, dict)
            and isinstance(team.get(user_id), dict)
            and isinstance(team[user_id].get("employment_event"), dict)
            for user_id in candidate_users
        ),
        "full_backup": "",
        "blockers": [],
        "warnings": [],
    }
    if not candidate_ids:
        result["warnings"].append("no_expired_application_candidates")
    if candidate_ids:
        result["blockers"].append("legacy_audit_subject_linkage_unstructured")
    if result["shared_calendar_event_references"]:
        result["blockers"].append("calendar_event_shared_by_multiple_applications")
    if result["employment_event_collisions"]:
        result["blockers"].append("candidate_user_has_employment_event")

    suite_path = Path(ws.DATA_DIR) / "pulse_suite.db"
    try:
        with closing(_read_only_connection(suite_path)) as connection:
            tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            if "suite_interviews" not in tables:
                result["blockers"].append("suite_interviews_table_unavailable")
            else:
                columns = {
                    str(row[1]) for row in connection.execute('PRAGMA table_info("suite_interviews")').fetchall()
                }
                if not {"application_id", "applicant_id"}.issubset(columns):
                    result["blockers"].append("suite_interviews_schema_incomplete")
                else:
                    if candidate_set:
                        marks = ",".join("?" for _ in candidate_set)
                        result["linked_interview_rows"] = int(connection.execute(
                            f'SELECT COUNT(*) FROM suite_interviews WHERE application_id IN ({marks})',
                            sorted(candidate_set),
                        ).fetchone()[0])
                    if candidate_users:
                        marks = ",".join("?" for _ in candidate_users)
                        result["interview_rows_by_applicant"] = int(connection.execute(
                            f'SELECT COUNT(*) FROM suite_interviews WHERE applicant_id IN ({marks})',
                            sorted(candidate_users),
                        ).fetchone()[0])
            if "suite_audit" not in tables:
                result["blockers"].append("suite_audit_table_unavailable")
            elif candidate_set:
                marks = ",".join("?" for _ in candidate_set)
                result["linked_suite_audit_rows"] = int(connection.execute(
                    f'SELECT COUNT(*) FROM suite_audit WHERE target_id IN ({marks})',
                    sorted(candidate_set),
                ).fetchone()[0])
    except (OSError, sqlite3.Error) as exc:
        result["blockers"].append(f"suite_database_unavailable:{type(exc).__name__}")

    import pulse_backup
    pulse_path = pulse_backup.sqlite_path(ws.DATA_DIR, "pulse.db")
    try:
        with closing(_read_only_connection(pulse_path)) as connection:
            tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            if "calendar_events" not in tables:
                result["blockers"].append("calendar_events_table_unavailable")
            elif event_ids:
                marks = ",".join("?" for _ in event_ids)
                result["linked_calendar_events"] = int(connection.execute(
                    f'SELECT COUNT(*) FROM calendar_events WHERE id IN ({marks})',
                    sorted(event_ids),
                ).fetchone()[0])
                result["unmatched_calendar_references"] = max(
                    0, len(event_ids) - result["linked_calendar_events"]
                )
                if result["unmatched_calendar_references"]:
                    result["blockers"].append("calendar_references_missing_or_unresolvable")
    except (OSError, sqlite3.Error) as exc:
        result["blockers"].append(f"pulse_database_unavailable:{type(exc).__name__}")

    if candidate_ids and (result["linked_interview_rows"] or result["interview_rows_by_applicant"]):
        result["blockers"].append("linked_interview_data_requires_separate_retention_decision")
    if candidate_ids and result["linked_calendar_events"]:
        result["blockers"].append("linked_calendar_data_requires_explicit_ownership")
    if candidate_ids:
        result["blockers"].append("multi_store_apply_and_recovery_not_implemented")
    try:
        result["full_backup"] = recent_full_backup(ws, now)
    except Exception:
        result["warnings"].append("full_backup_status_unavailable")
    if candidate_ids and not result["full_backup"]:
        result["blockers"].append("fresh_full_backup_missing")

    # This phase is preview-only by design. Even a blocker-free report is not a delete token.
    result["blockers"] = sorted(set(result["blockers"]))
    return result


def recent_full_backup(ws, now: datetime | None = None) -> str:
    import pulse_backup
    backups = pulse_backup.list_backups(ws.full_backup_dir())
    if not backups:
        return ""
    newest = datetime.fromtimestamp(backups[0].stat().st_mtime, tz=timezone.utc)
    current = now or datetime.now(timezone.utc)
    # Long auto-backup intervals must not block the clean-up forever.
    allowed = max(MAX_BACKUP_AGE, timedelta(hours=float(getattr(ws, "AUTO_BACKUP_HOURS", 24) or 24) + 24))
    return backups[0].name if current - newest <= allowed else ""


def run(ws, now: datetime | None = None, force: bool = False) -> dict:
    """Apply the retention rules once. Requires `enabled` (or force) and a fresh complete backup."""
    settings = load_settings()
    report = {"ran": False, "applications": 0, "audit": 0, "reason": ""}
    if not (settings["enabled"] or force):
        report["reason"] = "Aufbewahrungsfristen sind ausgeschaltet."
        return report
    if not RETENTION_MUTATIONS_ENABLED:
        report["reason"] = (
            "Automatische Bereinigung ist bis zur referenzsicheren Phase 15.6e gesperrt. "
            "Es wurde nichts gelöscht oder anonymisiert."
        )
        save_settings({
            "last_run": (now or datetime.now(timezone.utc)).date().isoformat(),
            "last_report": report,
        })
        return report
    backup = recent_full_backup(ws, now)
    if not backup:
        report["reason"] = "Kein Komplett-Backup der letzten 2 Tage – Bereinigung übersprungen."
        ws.log_audit("System", "0", "Datenbereinigung übersprungen", report["reason"])
        save_settings({"last_run": (now or datetime.now(timezone.utc)).date().isoformat(), "last_report": report})  # retry tomorrow
        return report
    try:
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
    except Exception as exc:  # e.g. file locked or marked corrupt – try again tomorrow, never every loop pass
        report["reason"] = f"Bereinigung fehlgeschlagen ({type(exc).__name__})"
        try:
            ws.log_audit("System", "0", "Datenbereinigung fehlgeschlagen", report["reason"])
        except Exception:
            print(f"⚠️ {report['reason']} – Audit-Eintrag nicht möglich.")
        save_settings({"last_run": (now or datetime.now(timezone.utc)).date().isoformat(), "last_report": report})
        return report
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


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _rows_for(
    path, uid: str, *, extra_values_by_table: dict[str, dict[str, set[str]]] | None = None
) -> tuple[dict, list[str]]:
    """Return all matching rows and a per-table truncation list from one read-only DB."""
    out = {}
    truncated = []
    con = _read_only_connection(path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        for table in tables:
            quoted_table = _quote_identifier(str(table))
            columns = {str(r[1]) for r in con.execute(f"PRAGMA table_info({quoted_table})")}
            clauses, params = [], []
            matches = [c for c in columns if c in _ID_COLUMNS]
            for column in matches:
                clauses.append(f"CAST({_quote_identifier(column)} AS TEXT)=?")
                params.append(uid)
            for column, values in (extra_values_by_table or {}).get(str(table), {}).items():
                if column not in columns:
                    continue
                clean = sorted({str(value) for value in values if str(value)})
                if clean:
                    marks = ",".join("?" for _ in clean)
                    clauses.append(f"{_quote_identifier(column)} IN ({marks})")
                    params.extend(clean)
            if not clauses:
                continue
            rows = [dict(r) for r in con.execute(
                f"SELECT DISTINCT * FROM {quoted_table} WHERE ({' OR '.join(clauses)}) LIMIT ?",
                [*params, EXPORT_ROW_LIMIT + 1],
            ).fetchall()]
            if len(rows) > EXPORT_ROW_LIMIT:
                truncated.append(str(table))
                rows = rows[:EXPORT_ROW_LIMIT]
            if rows:
                out[str(table)] = rows
    finally:
        con.close()
    return out, sorted(truncated)


def _application_audit_references(applications: dict, team_entry: dict | None) -> set[str]:
    """Collect only explicit audit IDs; never search arbitrary details or notes text."""
    references: set[str] = set()
    for application in applications.values():
        if not isinstance(application, dict):
            continue
        direct = application.get("audit_ref")
        if direct:
            references.add(str(direct))
        history = application.get("history", [])
        if isinstance(history, list):
            for event in history:
                if isinstance(event, dict) and event.get("audit_ref"):
                    references.add(str(event["audit_ref"]))
    employment = team_entry.get("employment_event") if isinstance(team_entry, dict) else None
    if isinstance(employment, dict) and employment.get("audit_ref"):
        references.add(str(employment["audit_ref"]))
    return references


def export_person(ws, discord_id: str) -> dict:
    """Read-only personal-data export with explicit recruiting cross-reference resolution."""
    import pulse_backup
    uid = str(discord_id).strip()
    team = ws.load_json(ws.DATA_FILE, {})
    raw_apps = ws.load_json(ws.APPS_FILE, {})
    apps = raw_apps if isinstance(raw_apps, dict) else {}
    own_apps = {
        str(key): value for key, value in apps.items()
        if isinstance(value, dict) and str(value.get("user_id") or "") == uid
    }
    team_entry = team.get(uid) if isinstance(team, dict) else None
    application_ids = set(own_apps)
    event_ids = {
        str(value.get("interview_event_id") or "").strip()
        for value in own_apps.values()
        if str(value.get("interview_event_id") or "").strip()
    }
    audit_refs = _application_audit_references(own_apps, team_entry)
    shifts = ws.load_json(ws.SHIFTS_FILE, {})
    audit = ws.load_json(ws.AUDIT_FILE, [])
    audit_entries = audit if isinstance(audit, list) else []
    result = {
        "discord_id": uid,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "team_file": team_entry,
        "applications": own_apps,
        "shifts": {
            "active": (shifts.get("active_shifts", {}) or {}).get(uid) if isinstance(shifts, dict) else None,
            "history": [
                h for h in (shifts.get("history", []) if isinstance(shifts, dict) else [])
                if isinstance(h, dict) and str(h.get("mod_id")) == uid
            ],
        },
        "audit_as_actor": [
            entry for entry in audit_entries
            if isinstance(entry, dict) and str(entry.get("actor_id") or "") == uid
        ],
        "audit_as_explicit_application_reference": [
            entry for entry in audit_entries
            if isinstance(entry, dict) and str(entry.get("id") or "") in audit_refs
        ],
        "databases": {},
        "database_export_truncated": {},
        "linked_recruiting_data": {
            "application_records": len(application_ids),
            "explicit_calendar_references": len(event_ids),
            "explicit_audit_references": len(audit_refs),
        },
        "notice": (
            f"Höchstens {EXPORT_ROW_LIMIT} Zeilen je SQLite-Tabelle; gekürzte Tabellen werden separat ausgewiesen. "
            "Interviewzeilen werden über applicant_id/interviewer_id und die eigenen application_id-Referenzen "
            "einbezogen. Kalendertermine werden über interview_event_id einbezogen. Legacy-Audit-Freitext wird "
            "nicht per Textsuche einer Person zugeordnet, da dies unzuverlässige Fehlzuordnungen erzeugen würde."
        ),
        "audit_linkage_limitations": [
            "audit_logs.json besitzt für historische Einträge kein strukturiertes subject_id/application_id-Feld.",
            "Nur actor_id und explizit gespeicherte audit_ref-Verweise sind eindeutig exportierbar.",
            "Freitextdetails werden nicht automatisch nach Discord-IDs oder Bewerbungs-IDs durchsucht.",
        ],
    }
    for name in pulse_backup.SQLITE_FILES:
        path = pulse_backup.sqlite_path(ws.DATA_DIR, name)
        if not path.is_file():
            continue
        extras: dict[str, dict[str, set[str]]] = {}
        if name == "pulse_suite.db":
            extras = {
                "suite_interviews": {"application_id": application_ids},
                "suite_audit": {"target_id": application_ids},
            }
        elif name == "pulse.db":
            extras = {"calendar_events": {"id": event_ids}}
        try:
            rows, truncated = _rows_for(path, uid, extra_values_by_table=extras)
        except (sqlite3.Error, OSError) as exc:
            rows, truncated = {"_fehler": type(exc).__name__}, []
        if rows:
            result["databases"][name] = rows
        if truncated:
            result["database_export_truncated"][name] = truncated

    ai_cases = ws.load_json(
        os.getenv("PULSE_AI_MODERATION_CASES_FILE", str(Path(ws.DATA_DIR) / "ai_moderation_cases.json")), {}
    )
    result["ai_moderation_cases"] = [
        case for case in (ai_cases.values() if isinstance(ai_cases, dict) else [])
        if isinstance(case, dict)
        and uid in {str(value) for key, value in case.items() if str(key).endswith("_id")}
    ]
    try:
        cases = pulse_db.get_setting("voice_support_cases", {}) or {}
        result["voice_support_open_cases"] = [
            case for case in cases.values()
            if isinstance(case, dict) and str(case.get("user_id")) == uid
        ]
    except Exception as exc:
        result["voice_support_open_cases_error"] = type(exc).__name__
    return result

def privacy_notice_html(esc) -> str:
    """Notice for /apply: which data, purpose, who, how long, contact."""
    settings = load_settings()
    months = round(settings["applications_days"] / 30.4)
    if not RETENTION_MUTATIONS_ENABLED:
        keep = (
            "Eine automatische serverseitige Löschung abgeschlossener Bewerbungen ist derzeit nicht aktiviert. "
            "Die zentrale Bereinigung ist fail-closed/deaktiviert; Aufbewahrung und Löschanfragen werden durch die Leitung geprüft."
        )
    elif settings["enabled"]:
        keep = f"Nach der Entscheidung wird deine Bewerbung nach etwa {months} Monaten automatisch gelöscht."
    else:
        keep = "Abgeschlossene Bewerbungen werden nicht automatisch gelöscht; wende dich bei Fragen an die Leitung."
    contact = settings["contact"] or "die Serverleitung über ein Support-Ticket"
    ai = (" Hinweis: Auf dem Discord-Server ist eine KI-gestützte Moderation aktiv; sie liefert nur Verdachtsfälle, "
          "Entscheidungen trifft immer ein Mensch.") if str(os.getenv("PULSE_AI_MODERATION_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"} else ""
    return (
        '<details class="rounded-xl border border-slate-200 dark:border-slate-700 p-3 text-[11px] text-slate-500 dark:text-slate-400">'
        '<summary class="cursor-pointer font-semibold">🔒 Datenschutzhinweis</summary><p class="mt-2">'
        "Gespeichert werden deine Discord-ID, dein Discord-Name, deine Motivation, deine Test- und Praxisantworten, "
        f"das automatisch berechnete Testergebnis sowie Zeitpunkte und der Bearbeitungsverlauf. Nicht abgesendete Entwürfe werden nur lokal im Browser gespeichert und nach {APPLICATION_DRAFT_TTL_DAYS} Tagen ohne Änderung automatisch entfernt. Im internen Verfahren "
        "können zusätzlich Bewertungen, Gesprächsnotizen, Termin- und Entscheidungsdaten entstehen. Zweck ist ausschließlich "
        "die Auswahl und Einstellung neuer Teammitglieder. Vollständige Bewerbungsdaten sehen nur Personen mit der Berechtigung "
        "„Bewerbungen verwalten“, Leitungsrechte oder Administratorrechte. Teammitglieder mit reiner Dashboard-/Abstimmungsberechtigung "
        "sehen nur eine reduzierte Übersicht ohne Antworten, interne Bewertungen oder Nutzer-ID. "
        f"{esc(keep)}{esc(ai)} Auskunft oder Löschung: {esc(contact)}. <a href=\"/datenschutz\" class=\"underline\">Vollständige Datenschutzerklärung</a>.</p></details>"
    )
