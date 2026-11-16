"""BOT-10: application communication – DM templates, interview appointment with calendar entry and reminder.

The built-in texts are exactly the messages Pulse sent before, so nothing changes until an admin edits a
template (Bewerbungen → Einstellungen). New: a receipt DM, an interview invitation with date and a reminder
shortly before the interview (sent by the existing automation loop).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

TEMPLATES_KEY = "application_dm_templates"
REMIND_BEFORE = timedelta(hours=1)
DEFAULTS = {
    "received": "📨 Deine Bewerbung bei **{server}** ist eingegangen (Nr. {id}). Wir melden uns, sobald sie geprüft wurde.",
    "interview": ("📋 Deine Bewerbung bei **{server}** wurde für die nächste Stufe freigegeben. "
                  "Du hast die Bewerberolle **{rolle}** erhalten. "
                  "Vor einer Einstellung muss verpflichtend ein Bewerbungsgespräch stattfinden. "
                  "Du bist damit noch nicht als Teammitglied eingestellt."),
    "scheduled": "🗓️ Dein Bewerbungsgespräch bei **{server}** ist am **{termin}** (deutsche Zeit). Bitte sei pünktlich im Warteraum.",
    "rescheduled": "🔄 Dein Bewerbungsgespräch bei **{server}** wurde auf **{termin}** (deutsche Zeit) verschoben. Bitte sei pünktlich im Warteraum.",
    "cancelled": "❌ Dein Bewerbungsgespräch bei **{server}** wurde abgesagt. Wir melden uns mit einem neuen Termin.",
    "reminder": "⏰ Erinnerung: Dein Bewerbungsgespräch bei **{server}** beginnt um **{termin}**.",
    "hired": ("🎉 Nach deinem Bewerbungsgespräch wurde deine Bewerbung bei **{server}** final angenommen. "
              "Du wurdest als **{rolle}** in das Team aufgenommen; die Bewerberolle wurde entfernt."),
    "rejected": "Deine Bewerbung bei **{server}** wurde leider abgelehnt. Danke für dein Interesse.",
    "rejected_after_interview": "Deine Bewerbung bei **{server}** wurde nach dem Bewerbungsgespräch leider abgelehnt.",
}
LABELS = {"received": "Eingang", "interview": "Freigabe zum Gespräch", "scheduled": "Gesprächstermin",
          "rescheduled": "Gespräch verschoben", "cancelled": "Gespräch abgesagt",
          "reminder": "Erinnerung vor dem Gespräch", "hired": "Angenommen", "rejected": "Abgelehnt",
          "rejected_after_interview": "Abgelehnt nach Gespräch"}
PLACEHOLDERS = "{server}, {id}, {rolle}, {termin}"


def load() -> dict:
    import pulse_db
    stored = pulse_db.get_setting(TEMPLATES_KEY, {}) or {}
    return {k: (str(stored.get(k)).strip() if isinstance(stored, dict) and str(stored.get(k) or "").strip() else v)
            for k, v in DEFAULTS.items()}


def save(values: dict, actor: str = "") -> dict:
    """Only changed texts are stored; an emptied field falls back to the built-in text.

    #103: every effective change of a template is kept as a new version (who/when, max. ``MAX_VERSIONS`` per
    template) so earlier wordings can be restored with :func:`restore`."""
    import pulse_db
    before = load()
    data = {k: str(v).strip()[:1800] for k, v in values.items() if k in DEFAULTS and str(v or "").strip() and str(v).strip() != DEFAULTS[k]}
    pulse_db.set_setting(TEMPLATES_KEY, data)
    after = load()
    for key in DEFAULTS:
        if after[key] != before[key]:
            _add_version(key, after[key], actor)
    return after


MAX_VERSIONS = 20
VERSIONS_KEY = "application_dm_template_versions"


def _add_version(key: str, text: str, actor: str = "") -> None:
    import pulse_db
    all_versions = pulse_db.get_setting(VERSIONS_KEY, {}) or {}
    rows = list(all_versions.get(key) or [])
    number = (rows[-1]["version"] + 1) if rows else 1
    rows.append({"version": number, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "actor": str(actor or "")[:100], "text": text})
    all_versions[key] = rows[-MAX_VERSIONS:]
    pulse_db.set_setting(VERSIONS_KEY, all_versions)


def history(key: str) -> list[dict]:
    """Stored versions of one template, newest first (the built-in default is never listed as a version)."""
    import pulse_db
    if key not in DEFAULTS:
        return []
    rows = (pulse_db.get_setting(VERSIONS_KEY, {}) or {}).get(key) or []
    return [dict(r) for r in reversed(rows)]


def restore(key: str, version: int, actor: str = "") -> str:
    """Make an earlier version the active text again (recorded as a new version). Raises ``ValueError``."""
    row = next((r for r in history(key) if r["version"] == int(version)), None)
    if row is None:
        raise ValueError("Diese Vorlagenversion gibt es nicht.")
    current = load()
    current[key] = row["text"]
    return save(current, actor)[key]


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render(key: str, **values) -> str:
    """Template with placeholders filled; unknown placeholders stay visible instead of failing.
    Only plain {name} is replaced – no format specs such as {server:>99999} (review fix: memory)."""
    known = {k: str(v) for k, v in values.items()}
    return _PLACEHOLDER.sub(lambda m: known.get(m.group(1), m.group(0)), load()[key])[:1900]


def _library_access(perms=None) -> dict:
    """Internal DM rendering is a trusted server-side operation; editing still uses caller permissions."""
    return perms if isinstance(perms, dict) else {"can_manage_applications": True}


def load_for_guild(guild_id, perms=None) -> dict:
    """Current applicant texts for the legacy editor, preferring active central-library versions."""
    current = load()
    try:
        import pulse_templates
        rows = pulse_templates.lookup(_library_access(perms), guild_id, "application_dm",
                                      scopes=list(DEFAULTS), include_archived=True)
        for key in DEFAULTS:
            matching = [row for row in rows if row.get("scope") == key]
            active_rows = [row for row in matching if not row.get("archived_at")]
            active = max(active_rows, key=lambda row: str(row.get("updated_at") or "")) if active_rows else None
            if active is not None:
                current[key] = str(active.get("body") or "")
            elif matching:
                latest = max(matching, key=lambda row: str(row.get("updated_at") or ""))
                # A manual archive means "stop using this custom text". The legacy-takeover marker is
                # different: it explicitly means the old store is the fallback for this template.
                if latest.get("archived_by_id") != pulse_templates.TAKEOVER_ID:
                    current[key] = DEFAULTS[key]
    except Exception:  # noqa: BLE001 - the pre-existing store remains an operational fallback
        pass
    return current


def resolve_for_guild(key: str, guild_id, perms=None, **values) -> dict:
    """Resolve a DM to the central version, with an explicit safe fallback to the former store.

    The returned reference is suitable for the applicant communication journal. A manually archived
    library entry falls back to the built-in text, while an archive marked TAKEOVER_ID uses the legacy
    store because that marker explicitly says the legacy entry took over.
    """
    fallback = {"text": render(key, **values), "template_ref": f"legacy:{key}", "source": "legacy"}
    if key not in DEFAULTS or guild_id in (None, ""):
        return fallback
    try:
        import pulse_embed_design
        import pulse_templates
        access = _library_access(perms)
        rows = pulse_templates.lookup(access, guild_id, "application_dm", scopes=[key], include_archived=True)
        if not rows:
            return fallback
        active_rows = [row for row in rows if not row.get("archived_at")]
        active = max(active_rows, key=lambda row: str(row.get("updated_at") or "")) if active_rows else None
        if active is not None:
            rendered = pulse_templates.render(access, guild_id, active["id"], values)
            return {"text": rendered["text"],
                    "template_ref": f'{rendered["template_id"]}:v{rendered["version"]}', "source": "library"}
        latest = max(rows, key=lambda row: str(row.get("updated_at") or ""))
        if latest.get("archived_by_id") == pulse_templates.TAKEOVER_ID:
            return fallback
        allowed = {name: value for name, value in values.items()
                   if name in pulse_templates.KINDS["application_dm"]["placeholders"]}
        text = pulse_embed_design.render_template(DEFAULTS[key], allowed)
        text = pulse_embed_design.neutralize_mentions(text)
        text = pulse_embed_design.truncate(text, pulse_templates.KINDS["application_dm"]["max_body"])
        return {"text": text, "template_ref": f"builtin:{key}", "source": "builtin"}
    except Exception:  # noqa: BLE001 - a broken optional library must never block an applicant DM
        return fallback


def sync_library(guild_id, perms, actor_id, actor_name, values: dict | None = None) -> dict:
    """Mirror supplied/current editor values as new central versions; no write occurs without app permission."""
    import pulse_templates
    selected = values if isinstance(values, dict) else load()
    entries = [(key, LABELS[key], str(selected.get(key, DEFAULTS[key]))) for key in DEFAULTS]
    return pulse_templates.sync_kind(perms, guild_id, "application_dm", entries, actor_id, actor_name)


def parse_slot(value: str, tz, now: datetime | None = None) -> datetime:
    """'2026-10-24T18:30' from a datetime-local field in German time → aware datetime; must be in the future.

    DST (Europe/Berlin): a wall-clock time that does not exist (spring gap) or exists twice (autumn overlap)
    is refused instead of being silently shifted by an hour."""
    try:
        naive = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("Bitte Datum und Uhrzeit des Gesprächs angeben.") from None
    if naive.tzinfo is not None:
        raise ValueError("Bitte Datum und Uhrzeit ohne Zeitzone angeben (deutsche Zeit).")
    naive = naive.replace(second=0, microsecond=0)
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    if first.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != naive:  # checked first: a gap also has two offsets
        raise ValueError("Diese Uhrzeit gibt es wegen der Zeitumstellung nicht. Bitte eine andere Zeit wählen.")
    if first.utcoffset() != second.utcoffset():
        raise ValueError("Diese Uhrzeit gibt es wegen der Zeitumstellung zweimal. Bitte eine andere Zeit wählen.")
    if first <= (now or datetime.now(timezone.utc)):
        raise ValueError("Der Gesprächstermin muss in der Zukunft liegen.")
    return first


def due_reminders(apps: dict, now: datetime) -> list[str]:
    """IDs of applications whose interview starts within REMIND_BEFORE and that were not reminded yet."""
    due = []
    for app_id, item in (apps or {}).items():
        if not isinstance(item, dict) or item.get("status") != "interview" or item.get("interview_reminded"):
            continue
        try:
            when = datetime.fromisoformat(str(item.get("interview_at")))
        except (TypeError, ValueError):
            continue
        if now <= when <= now + REMIND_BEFORE:
            due.append(app_id)
    return due
