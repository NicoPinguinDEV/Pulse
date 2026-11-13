"""Audit #14: one evidence record for every case file (BM, TF, TW, KI).

Evidence is attached to a case *reference* (``BM:MOD-1A2B3C``, see ``pulse_case_registry``). Existing
evidence is not copied or migrated: ``evidence_for`` shows the new managed records together with what the
case files already hold (citizen-moderation ``proof``, team-warning ``proof``, uploaded case attachments)
as read-only ``legacy`` items.

Rules:
- Every read and write requires the same right as the case's own view (``reg.resolve`` returns None otherwise).
- Only links (http/https, no credentials) and short notes are stored; uploads stay in the team-case attachment flow.
- Removal is a soft delete with a mandatory reason; who added what and who removed it stays visible.
- At most MAX_ACTIVE active items per case; every record is scoped to the guild of the viewer.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pulse_case_registry as reg
import pulse_db

KINDS = {"link": "Link", "text": "Notiz"}
MAX_ACTIVE = 20
MAX_LINK = 500
MAX_TEXT = 500
MAX_NOTE = 200
MIN_REMOVE_REASON = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS case_evidence (id TEXT PRIMARY KEY, case_ref TEXT NOT NULL, guild_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, value TEXT NOT NULL, note TEXT DEFAULT '', added_by_id TEXT NOT NULL, added_by_name TEXT, "
        "added_at TEXT NOT NULL, removed_at TEXT, removed_by_id TEXT, removed_by_name TEXT, remove_reason TEXT)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_case_evidence_ref ON case_evidence(guild_id, case_ref, added_at)")


def safe_link(value) -> str | None:
    text = str(value or "").strip()
    if not text or len(text) > MAX_LINK or any(ch.isspace() or ord(ch) < 32 for ch in text):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    return text


def _legacy_items(entry: dict) -> list[dict]:
    kind, native = entry["kind"], str(entry["native_id"])
    items: list[dict] = []

    def add(kind_, value, label, url=""):
        items.append({"id": "", "source": "legacy", "kind": kind_, "value": value, "note": label, "url": url,
                      "added_by": "", "added_at": ""})

    try:
        if kind == "BM":
            import pulse_mod_cases as mc
            row = mc.get_case(native) or {}
            proof = str(row.get("proof") or "").strip()
            if proof:
                add("link" if safe_link(proof) else "text", proof, "Beweisangabe der Fallakte")
        elif kind == "TW":
            for warn in (
                w for e in reg._team_data().values() for w in reg._warns_of(e)
                if str(w.get("id")).lower() == native
            ):
                proof = str(warn.get("proof") or "").strip()
                if proof:
                    add("link" if safe_link(proof) else "text", proof, "Beweisangabe der Verwarnung")
        elif kind == "TF":
            import pulse_ultimate
            pulse_ultimate.setup()
            with pulse_ultimate.cx() as c:
                case = c.execute("SELECT id FROM ultimate_cases WHERE case_no=?", (native,)).fetchone()
                rows = c.execute(
                    "SELECT id,original_name,created_at,uploaded_by_name FROM ultimate_attachments "
                    "WHERE owner_type='case' AND owner_id=? ORDER BY created_at DESC LIMIT 50", (case["id"],)
                ).fetchall() if case else []
            for row in rows:
                items.append({"id": "", "source": "legacy", "kind": "file", "value": row["original_name"],
                              "note": "Hochgeladene Datei", "url": f"/ultimate/cases/{case['id']}/attachments/{row['id']}",
                              "added_by": row["uploaded_by_name"], "added_at": row["created_at"]})
    except Exception:
        return items
    return items


def _case_entry(ref, perms, guild_id):
    parsed = reg.parse_ref(ref)
    entry = reg.resolve(ref, perms, guild_id=guild_id) if parsed else None
    if not entry:
        return None, None
    return parsed, entry


def evidence_for(ref, perms, *, guild_id) -> dict | None:
    """Managed + legacy evidence of one case, or None for unknown / not permitted references."""
    parsed, entry = _case_entry(ref, perms, guild_id)
    if not parsed:
        return None
    case_ref = reg.make_ref(*parsed)
    with pulse_db.connect() as db:
        _table(db)
        rows = db.execute(
            "SELECT * FROM case_evidence WHERE guild_id=? AND case_ref=? ORDER BY added_at", (str(guild_id), case_ref)
        ).fetchall()
    managed = []
    for row in rows:
        item = dict(row)
        item.update(source="managed", url=item["value"] if item["kind"] == "link" else "",
                    added_by=item.get("added_by_name") or item["added_by_id"], removed=bool(item["removed_at"]))
        managed.append(item)
    legacy = _legacy_items({"kind": parsed[0], "native_id": parsed[1]})
    return {"ref": case_ref, "case": entry, "managed": managed, "legacy": legacy,
            "active_count": sum(1 for item in managed if not item["removed"]) + len(legacy)}


def add_evidence(ref, perms, *, guild_id, kind, value, note, actor_id, actor_name) -> dict:
    """Raises ValueError (message safe to show) or PermissionError."""
    parsed, _ = _case_entry(ref, perms, guild_id)
    if not parsed:
        raise PermissionError("Keine sichtbare Akte zu dieser Referenz.")
    if kind not in KINDS:
        raise ValueError("Unbekannte Beweisart.")
    if kind == "link":
        stored = safe_link(value)
        if not stored:
            raise ValueError("Bitte einen gültigen http(s)-Link ohne Zugangsdaten angeben.")
    else:
        stored = str(value or "").strip()[:MAX_TEXT]
        if len(stored) < 3:
            raise ValueError("Die Notiz ist zu kurz.")
    case_ref = reg.make_ref(*parsed)
    item = {"id": "EV-" + secrets.token_hex(4).upper(), "case_ref": case_ref, "guild_id": str(guild_id), "kind": kind,
            "value": stored, "note": str(note or "").strip()[:MAX_NOTE], "added_by_id": str(actor_id),
            "added_by_name": str(actor_name or "")[:80], "added_at": _now()}
    with pulse_db.connect() as db:
        _table(db)
        active = db.execute(
            "SELECT COUNT(*) FROM case_evidence WHERE guild_id=? AND case_ref=? AND removed_at IS NULL",
            (str(guild_id), case_ref)).fetchone()[0]
        if active >= MAX_ACTIVE:
            raise ValueError(f"Pro Fall sind höchstens {MAX_ACTIVE} aktive Beweise möglich.")
        db.execute(
            "INSERT INTO case_evidence(id,case_ref,guild_id,kind,value,note,added_by_id,added_by_name,added_at) "
            "VALUES (:id,:case_ref,:guild_id,:kind,:value,:note,:added_by_id,:added_by_name,:added_at)", item)
    return item


def remove_evidence(evidence_id, perms, *, guild_id, reason, actor_id, actor_name) -> dict:
    reason = str(reason or "").strip()[:300]
    with pulse_db.connect() as db:
        _table(db)
        row = db.execute("SELECT * FROM case_evidence WHERE id=? AND guild_id=?", (str(evidence_id), str(guild_id))).fetchone()
        if not row:
            raise PermissionError("Beweis nicht gefunden.")
        parsed = reg.parse_ref(row["case_ref"])
        if not parsed or reg.resolve(row["case_ref"], perms, guild_id=guild_id) is None:
            raise PermissionError("Beweis nicht gefunden.")
        if len(reason) < MIN_REMOVE_REASON:
            raise ValueError("Bitte einen Grund für die Entfernung angeben.")
        if row["removed_at"]:
            raise ValueError("Dieser Beweis wurde bereits entfernt.")
        db.execute(
            "UPDATE case_evidence SET removed_at=?, removed_by_id=?, removed_by_name=?, remove_reason=? "
            "WHERE id=? AND removed_at IS NULL", (_now(), str(actor_id), str(actor_name or "")[:80], reason, row["id"]))
    return dict(row)
