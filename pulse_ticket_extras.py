"""BOT-06: ticket tools – quick replies, handover with a required note, ticket ban with expiry, tags,
rating report and a readable transcript view. Pure data logic (pulse.db); Discord and dashboard call it.

Nothing existing changes shape: tags live in a new table, the rest in pulse.db settings; claims use the
existing tickets columns and the existing ticket_events log.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

import pulse_db

REPLIES_KEY = "ticket_quick_replies"   # {"*": [{"title","text"}], "<category>": [...]}
BLOCKS_KEY = "ticket_blocks"           # {"<user_id>": {"until","reason","by","at"}}
MAX_REPLIES = 25                       # Discord autocomplete shows at most 25
MAX_TAGS = 8
TAG_RE = re.compile(r"^[\wäöüÄÖÜß-]{2,24}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ quick replies
def load_replies() -> dict:
    raw = pulse_db.get_setting(REPLIES_KEY, {}) or {}
    out = {}
    for category, items in (raw.items() if isinstance(raw, dict) else []):
        clean = [{"title": str(i.get("title", "")).strip()[:80], "text": str(i.get("text", "")).strip()[:1800]}
                 for i in (items if isinstance(items, list) else []) if isinstance(i, dict)]
        clean = [i for i in clean if i["title"] and i["text"]][:MAX_REPLIES]
        if clean:
            out[str(category)[:60]] = clean
    return out


def save_replies(text_by_category: dict) -> dict:
    """Input per category: lines 'Titel | Text' (one reply per line, '\\n' in text allowed as literal \\n)."""
    data = {}
    for category, block in text_by_category.items():
        items = []
        for line in str(block or "").splitlines():
            if "|" not in line:
                continue
            title, text = line.split("|", 1)
            items.append({"title": title.strip(), "text": text.strip().replace("\\n", "\n")})
        data[category] = items
    pulse_db.set_setting(REPLIES_KEY, data)
    return load_replies()


def replies_for(category: str) -> list[dict]:
    replies = load_replies()
    return (replies.get(str(category), []) + replies.get("*", []))[:MAX_REPLIES]


def replies_as_text(category: str) -> str:
    return "\n".join(f"{r['title']} | {r['text'].replace(chr(10), '\\n')}" for r in load_replies().get(category, []))


# ------------------------------------------------------------------ handover
def handover(ticket_id: str, new_id, new_name: str, actor_id, actor_name: str, note: str) -> tuple[bool, str]:
    note = (note or "").strip()
    if len(note) < 5:
        return False, "Bitte eine Übergabe-Notiz mit mindestens 5 Zeichen angeben."
    ticket = pulse_db.get_ticket(ticket_id=ticket_id)
    if not ticket or ticket.get("status") == "closed":
        return False, "Dieses Ticket ist nicht mehr offen."
    if str(ticket.get("claimed_by_id") or "") == str(new_id):
        return False, "Das Ticket liegt bereits bei dieser Person."
    now = pulse_db._now()
    with pulse_db.connect() as db:
        db.execute("UPDATE tickets SET claimed_by_id=?,claimed_by_name=?,claimed_at=?,status='in_progress' WHERE id=? AND status!='closed'",
                   (str(new_id), new_name, now, ticket_id))
        db.execute("INSERT INTO ticket_events VALUES (?,?,?,?,?,?,?)",
                   (pulse_db.make_id("te"), ticket_id, str(actor_id), actor_name, "handover",
                    f"an {new_name}: {note[:900]}", now))
    pulse_db.notify(new_id, "🎫 Ticket übergeben", f"{actor_name} hat dir {ticket_id} übergeben: {note[:200]}",
                    "info", f"/ticket/{ticket_id}", f"handover:{ticket_id}:{new_id}", 600)
    return True, f"Ticket an {new_name} übergeben."


# ------------------------------------------------------------------ ticket ban
def load_blocks() -> dict:
    raw = pulse_db.get_setting(BLOCKS_KEY, {}) or {}
    return raw if isinstance(raw, dict) else {}


def block_for(user_id, now: datetime | None = None) -> dict | None:
    """The active ban for this person, or None (expired bans count as gone)."""
    entry = load_blocks().get(str(user_id))
    if not isinstance(entry, dict):
        return None
    try:
        until = datetime.fromisoformat(str(entry.get("until")))
    except (TypeError, ValueError):
        return None
    return entry if until > (now or _now()) else None


def block(user_id, days: int, reason: str, actor_name: str) -> dict:
    days = max(1, min(int(days), 365))
    reason = (reason or "").strip()[:300]
    if len(reason) < 5:
        raise ValueError("Bitte einen nachvollziehbaren Grund (mindestens 5 Zeichen) angeben.")
    blocks = load_blocks()
    entry = {"until": (_now() + timedelta(days=days)).isoformat(timespec="seconds"), "reason": reason,
             "by": actor_name, "at": _now().isoformat(timespec="seconds")}
    blocks[str(user_id)] = entry
    # expired entries are dropped while we are writing anyway
    blocks = {uid: e for uid, e in blocks.items() if uid == str(user_id) or block_for_entry(e)}
    pulse_db.set_setting(BLOCKS_KEY, blocks)
    return entry


def block_for_entry(entry) -> bool:
    try:
        return datetime.fromisoformat(str(entry.get("until"))) > _now()
    except (TypeError, ValueError, AttributeError):
        return False


def unblock(user_id) -> bool:
    blocks = load_blocks()
    if str(user_id) not in blocks:
        return False
    blocks.pop(str(user_id))
    pulse_db.set_setting(BLOCKS_KEY, blocks)
    return True


def block_message(entry: dict) -> str:
    until = str(entry.get("until", ""))[:10]
    return (f"🚫 Du kannst bis {until} keine neuen Tickets eröffnen. Grund: {entry.get('reason', '–')}\n"
            "Wenn du das für einen Fehler hältst, wende dich bitte direkt an die Serverleitung.")


# ------------------------------------------------------------------ tags
def _tags_table(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS ticket_tags (ticket_id TEXT NOT NULL, tag TEXT NOT NULL, added_by TEXT, "
               "added_at TEXT, PRIMARY KEY (ticket_id, tag))")


def clean_tag(tag: str) -> str:
    tag = (tag or "").strip().lower().lstrip("#")
    if not TAG_RE.match(tag):
        raise ValueError("Tags: 2–24 Zeichen, nur Buchstaben, Zahlen, - und _.")
    return tag


def add_tag(ticket_id: str, tag: str, actor_name: str = "") -> str:
    tag = clean_tag(tag)
    with pulse_db.connect() as db:
        _tags_table(db)
        count = db.execute("SELECT COUNT(*) FROM ticket_tags WHERE ticket_id=?", (ticket_id,)).fetchone()[0]
        if count >= MAX_TAGS:
            raise ValueError(f"Höchstens {MAX_TAGS} Tags pro Ticket.")
        db.execute("INSERT OR IGNORE INTO ticket_tags VALUES (?,?,?,?)", (ticket_id, tag, actor_name, pulse_db._now()))
    return tag


def remove_tag(ticket_id: str, tag: str) -> bool:
    with pulse_db.connect() as db:
        _tags_table(db)
        return db.execute("DELETE FROM ticket_tags WHERE ticket_id=? AND tag=?", (ticket_id, clean_tag(tag))).rowcount > 0


def tags_for(ticket_ids) -> dict[str, list[str]]:
    ids = [str(t) for t in ticket_ids if t]
    if not ids:
        return {}
    out: dict[str, list[str]] = {}
    with pulse_db.connect() as db:
        _tags_table(db)
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = db.execute(f"SELECT ticket_id, tag FROM ticket_tags WHERE ticket_id IN ({','.join('?' * len(chunk))}) ORDER BY tag",
                              chunk).fetchall()
            for tid, tag in rows:
                out.setdefault(tid, []).append(tag)
    return out


# ------------------------------------------------------------------ rating report
def ratings_report(tickets: list[dict]) -> dict:
    """Average rating and count per category and per staff member (only rated tickets)."""
    def agg(key):
        data: dict[str, list[int]] = {}
        for t in tickets:
            if t.get("rating"):
                data.setdefault(str(t.get(key) or "–"), []).append(int(t["rating"]))
        return sorted(((k, round(sum(v) / len(v), 2), len(v)) for k, v in data.items()), key=lambda r: (-r[2], r[0]))
    rated = [int(t["rating"]) for t in tickets if t.get("rating")]
    return {"count": len(rated), "average": round(sum(rated) / len(rated), 2) if rated else None,
            "by_category": agg("category"), "by_staff": agg("claimed_by_name")}


# ------------------------------------------------------------------ transcript view
_LINE = re.compile(r"^\[(?P<time>[^\]]{4,40})\]\s*(?P<author>[^:]{1,80}):\s?(?P<text>.*)$")


def transcript_html(text: str) -> str:
    """Readable HTML for a stored .txt transcript; everything escaped, no links are made clickable."""
    rows = []
    for raw in (text or "").splitlines():
        match = _LINE.match(raw)
        if match:
            rows.append(f'<div class="tr-msg"><div class="tr-meta"><b>{html.escape(match["author"])}</b> '
                        f'<span>{html.escape(match["time"])}</span></div><div class="tr-text">{html.escape(match["text"])}</div></div>')
        elif raw.strip():
            rows.append(f'<div class="tr-line">{html.escape(raw)}</div>')
    return "".join(rows) or '<div class="pulse-empty">Das Transkript ist leer.</div>'
