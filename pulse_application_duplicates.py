"""Read-only hints for repeat applications by the same Discord account.

No fuzzy name matching is used: names are mutable and can create false positives.
This module never changes application status or writes to applications.json.
"""
from __future__ import annotations

import html
import re
from collections.abc import Mapping

_DISCORD_ID = re.compile(r"^[0-9]{15,22}$")
MAX_DUPLICATE_HINTS = 5


def applicant_discord_id(record) -> str:
    if not isinstance(record, Mapping):
        return ""
    for field in ("user_id", "discord_id", "applicant_id"):
        value = str(record.get(field) or "").strip()
        if _DISCORD_ID.fullmatch(value):
            return value
    return ""


def build_duplicate_index(applications):
    """Build one bounded metadata-only index per page render instead of rescanning all applications per card."""
    if not isinstance(applications, Mapping):
        return {}
    index = {}
    for raw_id, record in applications.items():
        owner_id = applicant_discord_id(record)
        if not owner_id:
            continue
        index.setdefault(owner_id, []).append({
            "application_id": str(raw_id),
            "status": str(record.get("status") or "unbekannt")[:40],
            "created_at": str(record.get("created_at") or "")[:40],
        })
    for records in index.values():
        records.sort(key=lambda row: (row["created_at"], row["application_id"]), reverse=True)
    return index


def find_duplicate_applications(applications, application_id, *, limit=MAX_DUPLICATE_HINTS, duplicate_index=None):
    """Find other submissions with the exact same validated Discord ID.

    The returned records intentionally contain only application ID, status and date;
    motivation, answers, names, reviews and internal notes are never copied.
    """
    if not isinstance(applications, Mapping):
        return []
    current_key = str(application_id or "")
    owner_id = applicant_discord_id(applications.get(current_key))
    if not owner_id:
        return []
    limit = max(1, min(20, int(limit or MAX_DUPLICATE_HINTS)))
    index = duplicate_index if isinstance(duplicate_index, Mapping) else build_duplicate_index(applications)
    return [
        row for row in index.get(owner_id, ())
        if row.get("application_id") != current_key
    ][:limit]


def render_duplicate_notice(applications, application_id, escape=html.escape, *, duplicate_index=None) -> str:
    """Render a small reviewer-only hint. An empty string means no verified match."""
    matches = find_duplicate_applications(
        applications, application_id, duplicate_index=duplicate_index,
    )
    if not matches:
        return ""
    rows = "".join(
        '<li class="flex flex-wrap items-center justify-between gap-2 py-1">'
        f'<span class="font-mono">{escape(row["application_id"])}</span>'
        f'<span>{escape(row["status"])}</span>'
        f'<span class="text-[10px] text-slate-500">{escape(row["created_at"])}</span>'
        '</li>'
        for row in matches
    )
    count = len(matches)
    suffix = "Bewerbung" if count == 1 else "Bewerbungen"
    return (
        '<aside class="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs" '
        'aria-label="Hinweis auf mögliche weitere Bewerbungen">'
        '<div class="font-bold text-amber-700 dark:text-amber-300">⚠ Mögliche weitere Bewerbung</div>'
        f'<p class="mt-1">Unter derselben Discord-ID wurden {count} weitere {suffix} gefunden. '
        'Das ist nur ein Prüfhinweis und trifft keine automatische Entscheidung.</p>'
        f'<ul class="mt-2 divide-y divide-amber-500/15">{rows}</ul>'
        '</aside>'
    )
