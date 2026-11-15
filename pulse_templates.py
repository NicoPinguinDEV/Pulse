"""Audit #123: one managed template library for ticket replies, applicant messages, announcements and more.

Existing, separate template systems (all left untouched here):

* ``pulse_mod_reason_templates`` – versioned moderation reasons (own tables, own rules),
* ``pulse_app_comms`` – applicant DM texts in the ``application_dm_templates`` setting, own placeholder rules,
* ``pulse_ticket_extras`` – quick replies per category in the ``ticket_quick_replies`` setting,
* ``pulse_welcome`` – welcome/leave texts in the embed design settings.

They differ in storage, placeholder syntax and safety (only some neutralise ``@everyone``, none keeps a
history except the moderation reasons). This library gives every kind the same behaviour:

* versioned: an edit creates a new version, old versions stay readable, nothing is overwritten; two people
  editing at once is detected (``expected_version``),
* archived instead of deleted, titles unique among the active templates of a kind/scope,
* placeholders validated per kind on save (same rules as ``pulse_embed_design``: ``{name}``, lower-case, no
  attribute access or format specs); unknown values render as "–", never as ``{x}`` or an object attribute,
* rendered text has ``@everyone``/``@here``/role pings neutralised and is length-bounded,
* rights per kind through ``pulse_policy`` (fail-closed); every change is a contract event,
* tables are part of the backup (``pulse_db.TABLES``).

:func:`import_legacy` copies the existing application and ticket-reply texts into the library (idempotent, read-only
on the old stores) so the sources can move over step by step. Existing modules still use their own stores.
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone

import pulse_db
import pulse_embed_design as design
import pulse_events as events
import pulse_policy as policy

MAX_TITLE = 80
MAX_SCOPE = 60
MAX_LIST = 200
TAKEOVER_ID = "legacy-sync"  # archived_by_id of a template archived because the legacy text took over (see sync_kind)

# kind -> label, allowed placeholders, body limit, policy action that manages and uses it.
KINDS: dict[str, dict] = {
    "ticket_reply": {"label": "Ticket-Schnellantwort", "placeholders": ("user", "ticket", "category", "staff"),
                     "max_body": 1800, "action": "ticket.manage"},
    "application_dm": {"label": "Bewerber-Nachricht", "placeholders": ("server", "id", "rolle", "termin"),
                       "max_body": 1800, "action": "application.manage"},
    "announcement": {"label": "Ankündigung", "placeholders": ("server", "name", "datum"),
                     "max_body": 1900, "action": "announcement.manage"},
    "moderation_reason": {"label": "Moderationsgrund", "placeholders": ("user", "case", "aktion"),
                          "max_body": 900, "action": "case.view.BM"},
}


class TemplateConflict(ValueError):
    """The template changed since the editor loaded it, or the title is already taken."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_tables(db) -> None:  # same tables as pulse_db.init_db; keeps a pre-existing database usable
    db.execute("CREATE TABLE IF NOT EXISTS message_templates (id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, kind TEXT NOT NULL, "
               "scope TEXT NOT NULL DEFAULT '*', current_version INTEGER NOT NULL, created_by_id TEXT NOT NULL, "
               "created_by_name TEXT NOT NULL, created_at TEXT NOT NULL, archived_at TEXT, archived_by_id TEXT, "
               "archived_by_name TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS message_template_versions (template_id TEXT NOT NULL, version INTEGER NOT NULL, "
               "title TEXT NOT NULL, body TEXT NOT NULL, created_by_id TEXT NOT NULL, created_by_name TEXT NOT NULL, "
               "created_at TEXT NOT NULL, PRIMARY KEY (template_id, version), "
               "FOREIGN KEY (template_id) REFERENCES message_templates(id) ON DELETE CASCADE)")


# --------------------------------------------------------------------------- rights and validation

def _kind(kind) -> dict:
    spec = KINDS.get(kind) if isinstance(kind, str) else None
    if spec is None:
        raise ValueError("Unbekannte Vorlagenart.")
    return spec


def can_use(perms, kind) -> bool:
    spec = KINDS.get(kind) if isinstance(kind, str) else None
    return spec is not None and policy.can(perms, spec["action"])


def _require(perms, kind) -> dict:
    spec = _kind(kind)
    policy.require(perms, spec["action"])
    return spec


def _clean(kind_spec, scope, title, body) -> tuple[str, str, str]:
    scope = str(scope or "*").strip()[:MAX_SCOPE] or "*"
    title = " ".join(str(title or "").split())
    body = str(body or "").strip()
    if not title or len(title) > MAX_TITLE:
        raise ValueError(f"Der Titel muss 1 bis {MAX_TITLE} Zeichen lang sein.")
    if not body or len(body) > kind_spec["max_body"]:
        raise ValueError(f"Der Text muss 1 bis {kind_spec['max_body']} Zeichen lang sein.")
    problems = design.validate_template(body, kind_spec["placeholders"])
    if problems:
        allowed = ", ".join("{" + p + "}" for p in kind_spec["placeholders"])
        raise ValueError(problems[0] + f" Erlaubt: {allowed}.")
    return scope, title, body


def _actor(actor_id, actor_name) -> tuple[str, str]:
    uid = str(actor_id or "").strip()
    if not uid:
        raise ValueError("Ausführendes Teammitglied muss angegeben werden.")
    return uid, (str(actor_name or "Teammitglied").strip()[:100] or "Teammitglied")


def _audit(action, template, actor_id, actor_name, **extra) -> None:
    events.emit_safe("system", f"template_{action}", target_type="template", target_id=template["id"],
                     actor_id=actor_id, actor_name=actor_name,
                     payload={"kind": template["kind"], "scope": template["scope"], "version": template.get("version"),
                              "title": template.get("title", ""), **extra})


# --------------------------------------------------------------------------- reading

def _load(db, guild, template_id, version=None) -> dict | None:
    row = db.execute("SELECT * FROM message_templates WHERE id=? AND guild_id=?",
                     (str(template_id or "").strip().upper(), str(guild or "").strip())).fetchone()
    if row is None:
        return None
    number = int(row["current_version"] if version is None else version)
    rev = db.execute("SELECT * FROM message_template_versions WHERE template_id=? AND version=?",
                     (row["id"], number)).fetchone()
    if rev is None:
        return None
    out = dict(row)
    out.update(version=number, title=rev["title"], body=rev["body"], edited_by_name=rev["created_by_name"],
               edited_at=rev["created_at"], is_current=number == int(row["current_version"]))
    return out


def get(perms, guild_id, template_id, version=None) -> dict | None:
    with pulse_db.connect() as db:
        _ensure_tables(db)
        found = _load(db, guild_id, template_id, version)
    if found is None:
        return None
    _require(perms, found["kind"])
    return found


def list_templates(perms, guild_id, kind=None, scope=None, *, include_archived=False, limit=100) -> list[dict]:
    """Current versions of the templates the person may use; other kinds are never included."""
    kinds = [k for k in KINDS if can_use(perms, k) and (kind is None or k == kind)]
    if kind is not None:
        _kind(kind)
    if not kinds:
        return []
    limit = max(1, min(int(limit), MAX_LIST))
    sql = ("SELECT t.*, v.title, v.body, v.created_by_name AS edited_by_name, v.created_at AS edited_at "
           "FROM message_templates t JOIN message_template_versions v ON v.template_id=t.id AND v.version=t.current_version "
           f"WHERE t.guild_id=? AND t.kind IN ({','.join('?' * len(kinds))})")
    args: list = [str(guild_id or "").strip(), *kinds]
    if scope is not None:
        sql += " AND t.scope=?"
        args.append(str(scope))
    if not include_archived:
        sql += " AND t.archived_at IS NULL"
    sql += " ORDER BY t.kind, t.scope, v.title COLLATE NOCASE LIMIT ?"
    args.append(limit)
    with pulse_db.connect() as db:
        _ensure_tables(db)
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def versions(perms, guild_id, template_id, limit=50) -> list[dict]:
    with pulse_db.connect() as db:
        _ensure_tables(db)
        found = _load(db, guild_id, template_id)
        if found is None:
            return []
        _require(perms, found["kind"])
        rows = db.execute("SELECT * FROM message_template_versions WHERE template_id=? ORDER BY version DESC LIMIT ?",
                          (found["id"], max(1, min(int(limit), 200)))).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- writing

def _title_taken(db, guild, kind, scope, title, ignore_id=None) -> bool:
    rows = db.execute(
        "SELECT t.id FROM message_templates t JOIN message_template_versions v ON v.template_id=t.id AND v.version=t.current_version "
        "WHERE t.guild_id=? AND t.kind=? AND t.scope=? AND t.archived_at IS NULL AND v.title=? COLLATE NOCASE",
        (guild, kind, scope, title)).fetchall()
    return any(r["id"] != ignore_id for r in rows)


def create(perms, guild_id, kind, scope, title, body, actor_id, actor_name) -> dict:
    spec = _require(perms, kind)
    guild = str(guild_id or "").strip()
    if not guild:
        raise ValueError("Server muss angegeben werden.")
    scope, title, body = _clean(spec, scope, title, body)
    uid, name = _actor(actor_id, actor_name)
    template_id = "TPL-" + secrets.token_hex(4).upper()
    now = _now()
    with pulse_db.connect() as db:
        _ensure_tables(db)
        if _title_taken(db, guild, kind, scope, title):
            raise TemplateConflict("Es gibt schon eine aktive Vorlage mit diesem Titel.")
        db.execute("INSERT INTO message_templates (id,guild_id,kind,scope,current_version,created_by_id,created_by_name,created_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (template_id, guild, kind, scope, 1, uid, name, now))
        db.execute("INSERT INTO message_template_versions (template_id,version,title,body,created_by_id,created_by_name,created_at) "
                   "VALUES (?,?,?,?,?,?,?)", (template_id, 1, title, body, uid, name, now))
    created = get(perms, guild, template_id)
    _audit("created", created, uid, name)
    return created


def update(perms, guild_id, template_id, title, body, actor_id, actor_name, *, expected_version=None) -> dict:
    uid, name = _actor(actor_id, actor_name)
    guild = str(guild_id or "").strip()
    with pulse_db.connect() as db:
        _ensure_tables(db)
        current = _load(db, guild, template_id)
        if current is None:
            raise ValueError("Vorlage nicht gefunden.")
        spec = _require(perms, current["kind"])
        if current["archived_at"]:
            raise ValueError("Archivierte Vorlagen können nicht bearbeitet werden.")
        _, title, body = _clean(spec, current["scope"], title, body)
        if expected_version is not None and int(expected_version) != current["version"]:
            raise TemplateConflict("Die Vorlage wurde inzwischen von jemand anderem geändert. Bitte neu laden.")
        if title == current["title"] and body == current["body"]:
            raise ValueError("Keine Änderung.")
        if _title_taken(db, guild, current["kind"], current["scope"], title, ignore_id=current["id"]):
            raise TemplateConflict("Es gibt schon eine aktive Vorlage mit diesem Titel.")
        version = current["version"] + 1
        try:
            db.execute("INSERT INTO message_template_versions (template_id,version,title,body,created_by_id,created_by_name,created_at) "
                       "VALUES (?,?,?,?,?,?,?)", (current["id"], version, title, body, uid, name, _now()))
        except sqlite3.IntegrityError:
            raise TemplateConflict("Die Vorlage wurde gleichzeitig geändert. Bitte neu laden.") from None
        db.execute("UPDATE message_templates SET current_version=? WHERE id=?", (version, current["id"]))
    updated = get(perms, guild, current["id"])
    _audit("updated", updated, uid, name)
    return updated


def _set_archived(perms, guild_id, template_id, actor_id, actor_name, archive: bool, *, takeover: bool = False) -> bool:
    uid, name = _actor(actor_id, actor_name)
    stored_id = TAKEOVER_ID if takeover and archive else uid
    guild = str(guild_id or "").strip()
    with pulse_db.connect() as db:
        _ensure_tables(db)
        current = _load(db, guild, template_id)
        if current is None:
            return False
        _require(perms, current["kind"])
        if bool(current["archived_at"]) == archive:
            return False
        if not archive and _title_taken(db, guild, current["kind"], current["scope"], current["title"], ignore_id=current["id"]):
            raise TemplateConflict("Es gibt schon eine aktive Vorlage mit diesem Titel.")
        if archive:
            db.execute("UPDATE message_templates SET archived_at=?, archived_by_id=?, archived_by_name=? WHERE id=?",
                       (_now(), stored_id, name, current["id"]))
        else:
            db.execute("UPDATE message_templates SET archived_at=NULL, archived_by_id=NULL, archived_by_name=NULL WHERE id=?",
                       (current["id"],))
    _audit("archived" if archive else "restored", current, uid, name, **({"legacy_takeover": True} if takeover and archive else {}))
    return True


def archive(perms, guild_id, template_id, actor_id, actor_name) -> bool:
    return _set_archived(perms, guild_id, template_id, actor_id, actor_name, True)


def restore(perms, guild_id, template_id, actor_id, actor_name) -> bool:
    return _set_archived(perms, guild_id, template_id, actor_id, actor_name, False)


# --------------------------------------------------------------------------- consumer lookup and sync

def lookup(perms, guild_id, kind, scopes=None, *, include_archived=False) -> list[dict]:
    """Current versions of one kind, optionally limited to ``scopes`` (no row-count cap, unlike the list view).

    For consumers that must know about *archived* templates too (so an archived text is not resurrected from a
    legacy store). Rights are those of the kind; a person without them gets ``PermissionDenied``.
    """
    _require(perms, kind)
    sql = ("SELECT t.*, v.title, v.body FROM message_templates t JOIN message_template_versions v "
           "ON v.template_id=t.id AND v.version=t.current_version WHERE t.guild_id=? AND t.kind=?")
    args: list = [str(guild_id or "").strip(), kind]
    if scopes is not None:
        scopes = [str(x) for x in scopes]
        sql += f" AND t.scope IN ({','.join('?' * len(scopes))})" if scopes else " AND 0"
        args += scopes
    if not include_archived:
        sql += " AND t.archived_at IS NULL"
    sql += " ORDER BY t.scope, v.title COLLATE NOCASE"
    with pulse_db.connect() as db:
        _ensure_tables(db)
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def sync_kind(perms, guild_id, kind, entries, actor_id, actor_name) -> dict:
    """Make the active templates of ``kind`` equal to ``entries`` = ``[(scope, title, body), ...]``.

    For stores that remain the editing surface (e.g. the ticket tools page): changed text becomes a new
    *version*, new titles are created, titles that disappeared are *archived* (history stays), unchanged ones are
    left alone. An entry that breaks the placeholder rules is not stored; an older library template with that
    title is archived so the legacy text takes over instead of being shadowed by an outdated version.
    Returns ``{"created", "updated", "archived", "unchanged", "skipped": [...]}``.
    """
    spec = _require(perms, kind)
    result = {"created": 0, "updated": 0, "archived": 0, "unchanged": 0, "skipped": []}
    wanted: dict[tuple[str, str], tuple[str, str]] = {}
    invalid: set[tuple[str, str]] = set()
    for scope, title, body in entries:
        try:
            clean_scope, clean_title, clean_body = _clean(spec, scope, title, body)
        except ValueError as exc:
            invalid.add((str(scope or "*").strip()[:MAX_SCOPE] or "*", " ".join(str(title or "").split()).casefold()))
            result["skipped"].append(f"{scope}/{title}: {exc}")
            continue
        wanted[(clean_scope, clean_title.casefold())] = (clean_title, clean_body)
    existing = {(r["scope"], r["title"].casefold()): r for r in lookup(perms, guild_id, kind)}
    for key, (title, body) in wanted.items():
        found = existing.get(key)
        try:
            if found is None:
                create(perms, guild_id, kind, key[0], title, body, actor_id, actor_name)
                result["created"] += 1
            elif found["body"] != body or found["title"] != title:
                update(perms, guild_id, found["id"], title, body, actor_id, actor_name, expected_version=found["current_version"])
                result["updated"] += 1
            else:
                result["unchanged"] += 1
        except TemplateConflict as exc:
            result["skipped"].append(f"{key[0]}/{title}: {exc}")
    for key, found in existing.items():
        if key not in wanted:
            if _set_archived(perms, guild_id, found["id"], actor_id, actor_name, True, takeover=key in invalid):
                result["archived"] += 1
    return result


# --------------------------------------------------------------------------- rendering

def render(perms, guild_id, template_id, values=None, *, version=None) -> dict:
    """Fill a template. Returns ``{"text", "template_id", "version", "kind"}``; archived templates are refused."""
    found = get(perms, guild_id, template_id, version)
    if found is None:
        raise ValueError("Vorlage nicht gefunden.")
    if found["archived_at"]:
        raise ValueError("Diese Vorlage ist archiviert.")
    spec = KINDS[found["kind"]]
    allowed = {k: v for k, v in (values or {}).items() if k in spec["placeholders"]}
    text = design.neutralize_mentions(design.render_template(found["body"], allowed))
    return {"text": design.truncate(text, spec["max_body"]), "template_id": found["id"], "version": found["version"],
            "kind": found["kind"]}


# --------------------------------------------------------------------------- legacy import

def import_legacy(perms, guild_id, actor_id, actor_name, kinds=("application_dm", "ticket_reply")) -> dict:
    """Copy customised applicant texts and ticket quick replies into the library. Idempotent (existing active
    titles are skipped), read-only on the old stores, texts that break the new placeholder rules are skipped and
    reported. Returns ``{"created": n, "skipped": [...]}``."""
    result = {"created": 0, "skipped": []}
    candidates: list[tuple[str, str, str, str]] = []
    if "application_dm" in kinds:
        _require(perms, "application_dm")
        import pulse_app_comms
        for key, text in pulse_app_comms.load().items():
            candidates.append(("application_dm", key, pulse_app_comms.LABELS.get(key, key), text))
    if "ticket_reply" in kinds:
        _require(perms, "ticket_reply")
        import pulse_ticket_extras
        for category, items in pulse_ticket_extras.load_replies().items():
            for item in items:
                candidates.append(("ticket_reply", category, item["title"], item["text"]))
    for kind, scope, title, body in candidates:
        try:
            create(perms, guild_id, kind, scope, title, body, actor_id, actor_name)
            result["created"] += 1
        except TemplateConflict:
            result["skipped"].append(f"{kind}/{scope}/{title}: vorhanden")
        except ValueError as exc:
            result["skipped"].append(f"{kind}/{scope}/{title}: {exc}")
    return result
