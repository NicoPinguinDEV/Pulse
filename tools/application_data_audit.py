"""Read-only, privacy-safe audit of Pulse application storage relationships.

The tool reads explicitly supplied fixture/export paths only. It never changes JSON or
SQLite data and never prints applicant names, Discord IDs, answers, notes, or event IDs.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_VERSION = 1
EMPLOYMENT_STATES = {"accepted", "hired"}


def _valid_team_since(value: Any) -> bool:
    """Match the exact legacy team_since_for() accepted formats."""
    raw = str(value or "").strip()
    if not raw:
        return False
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%d.%m.%Y %H:%M"):
        try:
            datetime.strptime(raw, fmt)
            return True
        except ValueError:
            continue
    return False


def _valid_application_timestamp(value: Any) -> bool:
    """Match pulse_time.parse_timestamp() formats used by team_since_for() fallback."""
    if value is None or value == "":
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            datetime.fromtimestamp(float(value), tz=timezone.utc)
            return True
        except (OverflowError, OSError, ValueError):
            return False
    raw = str(value).strip()
    if not raw:
        return False
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return True
    except ValueError:
        pass
    formats = (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y",
    )
    for fmt in formats:
        try:
            datetime.strptime(raw, fmt)
            return True
        except ValueError:
            continue
    return False


def _has_resolvable_team_since(user_id: str, team_data: dict, records: dict[str, dict]) -> bool:
    """Mirror team_since_for(): valid team_data value first, then exact-status application fallback."""
    if not user_id or user_id == "None" or user_id != user_id.strip():
        return False
    entry = team_data.get(user_id)
    if isinstance(entry, dict) and _valid_team_since(entry.get("team_since")):
        return True
    for item in records.values():
        status = item.get("status")
        if str(item.get("user_id")) != user_id or not isinstance(status, str) or status not in EMPLOYMENT_STATES:
            continue
        if _valid_application_timestamp(item.get("decided_at")):
            return True
    return False


def _read_json(path: str | Path, label: str, expected: type) -> Any:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, expected):
        raise ValueError(f"{label} has an unexpected top-level type")
    return value


def _readonly_db(path: str | Path) -> sqlite3.Connection:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"SQLite input for the audit is not a file: {source.name}")
    # mode=ro makes accidental writes impossible; no migrations or table creation happen here.
    uri = source.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _suite_interviews(path: str | Path | None) -> dict[str, Any]:
    empty = {
        "provided": path is not None,
        "table_found": False,
        "schema_valid": False,
        "rows": 0,
        "orphan_application_refs": 0,
        "orphan_interview_records": 0,
        "interview_rows_without_application_id": 0,
        "interview_rows_without_id": 0,
        "duplicate_interview_ids": 0,
        "applications_with_multiple_interviews": 0,
        "application_interview_id_mismatches": 0,
        "_by_application": {},
        "_owners_by_id": {},
    }
    if path is None:
        return empty

    with closing(_readonly_db(path)) as connection:
        if not _table_exists(connection, "suite_interviews"):
            return empty
        columns = {
            str(row["name"]): row
            for row in connection.execute('PRAGMA table_info("suite_interviews")').fetchall()
        }
        if not {"id", "application_id"}.issubset(columns):
            return {**empty, "table_found": True}
        primary_key_columns = sum(int(column["pk"] or 0) > 0 for column in columns.values())
        schema_valid = int(columns["id"]["pk"] or 0) > 0 and primary_key_columns == 1
        rows = connection.execute(
            "SELECT id, application_id FROM suite_interviews"
        ).fetchall()

    by_application: dict[str, list[str]] = defaultdict(list)
    owners_by_id: dict[str, set[str]] = defaultdict(set)
    id_counts: Counter[str] = Counter()
    rows_without_application_id = 0
    rows_without_id = 0

    for row in rows:
        interview_id = str(row["id"] or "").strip()
        application_id = str(row["application_id"] or "").strip()
        if application_id:
            by_application[application_id].append(interview_id)
        else:
            rows_without_application_id += 1
        if interview_id:
            id_counts[interview_id] += 1
            owners_by_id[interview_id].add(application_id)
        else:
            rows_without_id += 1

    return {
        "provided": True,
        "table_found": True,
        "schema_valid": schema_valid,
        "rows": len(rows),
        "orphan_application_refs": 0,
        "orphan_interview_records": 0,
        "interview_rows_without_application_id": rows_without_application_id,
        "interview_rows_without_id": rows_without_id,
        "duplicate_interview_ids": sum(count > 1 for count in id_counts.values()),
        "applications_with_multiple_interviews": sum(
            sum(bool(interview_id) for interview_id in values) > 1
            for values in by_application.values()
        ),
        "application_interview_id_mismatches": 0,
        "_by_application": dict(by_application),
        "_owners_by_id": {key: set(value) for key, value in owners_by_id.items()},
    }


def _calendar_events(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "provided": False, "table_found": False, "schema_valid": False, "rows": 0,
            "rows_without_id": 0, "duplicate_event_ids": 0, "_ids": set(),
        }
    with closing(_readonly_db(path)) as connection:
        if not _table_exists(connection, "calendar_events"):
            return {
                "provided": True, "table_found": False, "schema_valid": False, "rows": 0,
                "rows_without_id": 0, "duplicate_event_ids": 0, "_ids": set(),
            }
        columns = {
            str(row["name"]): row
            for row in connection.execute('PRAGMA table_info("calendar_events")').fetchall()
        }
        if "id" not in columns:
            return {
                "provided": True, "table_found": True, "schema_valid": False, "rows": 0,
                "rows_without_id": 0, "duplicate_event_ids": 0, "_ids": set(),
            }
        primary_key_columns = sum(int(column["pk"] or 0) > 0 for column in columns.values())
        schema_valid = int(columns["id"]["pk"] or 0) > 0 and primary_key_columns == 1
        rows = connection.execute("SELECT id FROM calendar_events").fetchall()
    event_id_counts = Counter(
        str(row["id"]).strip() for row in rows if str(row["id"] or "").strip()
    )
    ids = set(event_id_counts)
    return {
        "provided": True,
        "table_found": True,
        "schema_valid": schema_valid,
        "rows": len(rows),
        "rows_without_id": sum(not str(row["id"] or "").strip() for row in rows),
        "duplicate_event_ids": sum(count > 1 for count in event_id_counts.values()),
        "_ids": ids,
    }


def audit_application_data(
    applications: dict,
    team_data: dict,
    suite_db_path: str | Path | None = None,
    pulse_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize application records and cross-storage links without exposing personal data."""
    if not isinstance(applications, dict) or not isinstance(team_data, dict):
        raise ValueError("applications and team_data must both be JSON objects")

    records = {str(key): item for key, item in applications.items() if isinstance(item, dict)}
    states: Counter[str] = Counter()
    unknown_states = 0
    for item in records.values():
        # Compare against fixed literals only; never reuse input text in report keys.
        # Match runtime status comparisons exactly; whitespace/case variants are not silently accepted.
        state = item.get("status")
        if state == "pending":
            states["pending"] += 1
        elif state == "in_review":
            states["in_review"] += 1
        elif state == "interview":
            states["interview"] += 1
        elif state == "accepted":
            states["accepted"] += 1
        elif state == "rejected":
            states["rejected"] += 1
        elif state == "hired":
            states["hired"] += 1
        else:
            unknown_states += 1
    if unknown_states:
        states["unknown_or_missing"] = unknown_states
    missing_user_ids = sum(
        not str(item.get("user_id") or "").strip()
        or str(item.get("user_id") or "") != str(item.get("user_id") or "").strip()
        for item in records.values()
    )

    employment_records = 0
    employment_without_team_since = 0
    for item in records.values():
        # team_since_for() uses exact status values and only the user_id field.
        status = item.get("status")
        if not isinstance(status, str) or status not in EMPLOYMENT_STATES:
            continue
        employment_records += 1
        user_id = str(item.get("user_id") or "")
        if not _has_resolvable_team_since(user_id, team_data, records):
            employment_without_team_since += 1

    interviews = _suite_interviews(suite_db_path)
    calendar = _calendar_events(pulse_db_path)
    application_ids = set(records)
    interviews_by_application = interviews["_by_application"]

    orphan_application_ids = {
        application_id
        for application_id in interviews_by_application
        if application_id not in application_ids
    }
    orphan_interview_records = (
        sum(len(interviews_by_application[application_id]) for application_id in orphan_application_ids)
        + interviews["interview_rows_without_application_id"]
    )
    interviews["orphan_application_refs"] = len(orphan_application_ids)
    interviews["orphan_interview_records"] = orphan_interview_records

    missing_interview_records = 0
    interview_references = 0
    for application_id, item in records.items():
        interview_id = str(item.get("interview_id") or "").strip()
        if not interview_id:
            continue
        interview_references += 1
        owners = interviews["_owners_by_id"].get(interview_id)
        if interviews["table_found"] and owners is None:
            missing_interview_records += 1
        elif interviews["table_found"] and (len(owners) != 1 or application_id not in owners):
            # An existing interview ID is invalid when it is unowned, duplicated, or belongs elsewhere.
            interviews["application_interview_id_mismatches"] += 1

    appointment_references = 0
    missing_calendar_events = 0
    event_applications: dict[str, set[str]] = defaultdict(set)
    for application_id, item in records.items():
        event_id = str(item.get("interview_event_id") or "").strip()
        if not event_id:
            continue
        appointment_references += 1
        event_applications[event_id].add(application_id)
        if calendar["table_found"] and event_id not in calendar["_ids"]:
            missing_calendar_events += 1
    shared_calendar_event_ids = sum(
        len(application_ids_for_event) > 1 for application_ids_for_event in event_applications.values()
    )

    # Keep the machine report deliberately limited to counts and fixed labels.
    return {
        "report_version": REPORT_VERSION,
        "read_only": True,
        "personal_data_emitted": False,
        "applications": {
            "records": len(applications),
            "valid_object_records": len(records),
            "non_object_records": len(applications) - len(records),
            "states": dict(sorted(states.items())),
            "unknown_or_missing_states": unknown_states,
            "missing_user_ids": missing_user_ids,
        },
        "employment": {
            "accepted_or_hired_records": employment_records,
            "records_without_team_since": employment_without_team_since,
        },
        "interviews": {
            "database_provided": interviews["provided"],
            "table_found": interviews["table_found"],
            "schema_valid": interviews["schema_valid"],
            "rows": interviews["rows"],
            "orphan_application_refs": interviews["orphan_application_refs"],
            "orphan_interview_records": interviews["orphan_interview_records"],
            "interview_rows_without_application_id": interviews["interview_rows_without_application_id"],
            "interview_rows_without_id": interviews["interview_rows_without_id"],
            "duplicate_interview_ids": interviews["duplicate_interview_ids"],
            "applications_with_multiple_interviews": interviews["applications_with_multiple_interviews"],
            "application_interview_id_mismatches": interviews["application_interview_id_mismatches"],
            "application_interview_references": interview_references,
            "application_refs_without_matching_interview": missing_interview_records,
        },
        "calendar": {
            "database_provided": calendar["provided"],
            "table_found": calendar["table_found"],
            "schema_valid": calendar["schema_valid"],
            "rows": calendar["rows"],
            "rows_without_id": calendar["rows_without_id"],
            "duplicate_event_ids": calendar["duplicate_event_ids"],
            "application_appointment_references": appointment_references,
            "references_without_matching_event": missing_calendar_events,
            "event_ids_shared_by_multiple_applications": shared_calendar_event_ids,
        },
        "readiness": {
            "requires_review": bool(
                unknown_states or missing_user_ids or employment_without_team_since
                or len(applications) != len(records)
                or orphan_interview_records
                or interviews["interview_rows_without_id"]
                or interviews["duplicate_interview_ids"]
                or missing_interview_records
                or interviews["application_interview_id_mismatches"]
                or missing_calendar_events
                or calendar["rows_without_id"]
                or calendar["duplicate_event_ids"]
                or shared_calendar_event_ids
                or not interviews["provided"]
                or not calendar["provided"]
                or (interviews["provided"] and not interviews["schema_valid"])
                or (calendar["provided"] and not calendar["schema_valid"])
                or (appointment_references and not calendar["table_found"])
                or (interview_references and not interviews["table_found"])
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit of applications.json, team_data.json and related Pulse SQLite references."
    )
    parser.add_argument("--applications", required=True, help="Explicit path to an applications.json fixture/export")
    parser.add_argument("--team-data", required=True, help="Explicit path to a team_data.json fixture/export")
    parser.add_argument("--suite-db", help="Optional explicit path to pulse_suite.db")
    parser.add_argument("--pulse-db", help="Optional explicit path to pulse.db")
    args = parser.parse_args()

    applications = _read_json(args.applications, "applications", dict)
    team_data = _read_json(args.team_data, "team_data", dict)
    report = audit_application_data(applications, team_data, args.suite_db, args.pulse_db)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
