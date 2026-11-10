"""BOT-06: ticket tools – quick replies, handover with a required note, ticket ban with expiry, tags,
rating report and a readable transcript view. Pure data logic (pulse.db); Discord and dashboard call it.

Nothing existing changes shape: tags live in a new table, the rest in pulse.db settings; claims use the
existing tickets columns and the existing ticket_events log.
"""
from __future__ import annotations

import html
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pulse_db
import pulse_paths
from pulse_safe_path import contained_file

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


# ------------------------------------------------------------------ deterministic similar-ticket suggestions
SIMILAR_MAX_CANDIDATES = 80
SIMILAR_MAX_TRANSCRIPT_BYTES = 128 * 1024
SIMILAR_MAX_QUERY_CHARS = 12_000
SIMILAR_STOPWORDS = frozenset({
    "aber", "als", "also", "am", "an", "and", "auf", "aus", "bei", "bin", "bis",
    "bitte", "bochum", "das", "dass", "dein", "deine", "dem", "den", "der", "des",
    "die", "dies", "doch", "du", "durch", "ein", "eine", "einem", "einen", "einer",
    "eines", "er", "es", "euch", "euer", "für", "gegen", "habe", "haben", "hat",
    "hier", "ich", "ihr", "ihre", "ihren", "im", "in", "ist", "ja", "kann",
    "können", "man", "mehr", "mit", "muss", "nach", "nicht", "noch", "oder",
    "ohne", "richtig", "rp", "sein", "seine", "server", "sie", "sind", "support",
    "und", "uns", "unter", "vom", "von", "vor", "war", "warum", "was", "weil",
    "weiter", "wenn", "werden", "wie", "wir", "wird", "wo", "wurde", "zu", "zum",
    "zur", "ticket", "tickets", "hallo", "danke", "guten", "tag",
})


def _ticket_terms(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(text or "")[:SIMILAR_MAX_QUERY_CHARS]).casefold()
    return {
        token for token in re.findall(r"[^\W_]{3,}", normalized)
        if not token.isdigit() and token not in SIMILAR_STOPWORDS
    }


def _read_candidate_transcript(filename: str) -> str:
    """Read only a bounded regular .txt file inside the configured transcript directory."""
    filename = str(filename or "").strip()
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or not filename.lower().endswith(".txt")
    ):
        return ""
    base = os.path.join(str(pulse_paths.DATA_DIR), "transcripts")
    target = contained_file(base, filename)
    if target is None:
        return ""
    try:
        if not os.path.isfile(target) or os.path.getsize(target) > SIMILAR_MAX_TRANSCRIPT_BYTES:
            return ""
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(SIMILAR_MAX_TRANSCRIPT_BYTES)
    except OSError:
        return ""


def similar_tickets(
    ticket_id: str,
    guild_id,
    query_text: str,
    limit: int = 5,
) -> list[dict]:
    """Rank closed same-guild tickets by local transcript term overlap; never call an external AI service."""
    ticket_id = str(ticket_id or "").strip()
    guild_id = str(guild_id or "").strip()
    query_terms = _ticket_terms(query_text)
    if not ticket_id or not guild_id or len(query_terms) < 2:
        return []
    try:
        page_size = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        page_size = 5

    with pulse_db.connect() as db:
        current = db.execute(
            "SELECT category FROM tickets WHERE id=? AND CAST(guild_id AS TEXT)=?",
            (ticket_id, guild_id),
        ).fetchone()
        if current is None:
            return []
        candidates = db.execute(
            """
            SELECT id, category, status, priority, user_name, channel_id,
                   transcript_path, opened_at, closed_at
            FROM tickets
            WHERE CAST(guild_id AS TEXT)=? AND id<>? AND status='closed'
                  AND COALESCE(transcript_path,'')<>''
            ORDER BY COALESCE(closed_at,opened_at) DESC
            LIMIT ?
            """,
            (guild_id, ticket_id, SIMILAR_MAX_CANDIDATES),
        ).fetchall()

    ranked = []
    for row in candidates:
        transcript = _read_candidate_transcript(row["transcript_path"])
        candidate_terms = _ticket_terms(transcript)
        shared = query_terms & candidate_terms
        if len(shared) < 2:
            continue
        union = query_terms | candidate_terms
        lexical_score = len(shared) / max(1, len(union))
        same_category = str(row["category"] or "") == str(current["category"] or "")
        score = min(1.0, lexical_score * 0.9 + (0.1 if same_category else 0.0))
        ranked.append({
            "ticket_id": str(row["id"]),
            "category": str(row["category"] or ""),
            "status": str(row["status"] or ""),
            "priority": str(row["priority"] or "normal"),
            "user_name": str(row["user_name"] or ""),
            "channel_id": str(row["channel_id"] or ""),
            "opened_at": str(row["opened_at"] or ""),
            "closed_at": str(row["closed_at"] or ""),
            "score": round(score, 4),
            "shared_term_count": len(shared),
            "same_category": same_category,
        })
    ranked.sort(
        key=lambda item: (
            -item["score"],
            -item["shared_term_count"],
            item["ticket_id"],
        )
    )
    return ranked[:page_size]


# ------------------------------------------------------------------ deterministic ticket handover summary
def build_ticket_summary(messages, limit: int = 5) -> dict:
    """Build a local, extractive handover summary from bounded message metadata."""
    clean = []
    participants = []
    attachments = 0
    for item in list(messages or [])[:100]:
        if not isinstance(item, dict) or item.get("is_bot"):
            continue
        text = str(item.get("content") or "").strip()[:2000]
        if not text:
            continue
        author = str(item.get("author") or "Unbekannt").strip()[:80] or "Unbekannt"
        created_at = str(item.get("created_at") or "").strip()[:40]
        try:
            attachments += max(0, min(int(item.get("attachment_count") or 0), 20))
        except (TypeError, ValueError):
            pass
        if author not in participants:
            participants.append(author)
        clean.append({"author": author, "created_at": created_at, "content": text})

    try:
        point_limit = max(1, min(int(limit), 8))
    except (TypeError, ValueError):
        point_limit = 5
    if not clean:
        return {
            "message_count": 0,
            "participants": [],
            "attachment_count": attachments,
            "first_at": "",
            "last_at": "",
            "key_points": [],
        }

    sentences = []
    frequencies: dict[str, int] = {}
    for row_index, row in enumerate(clean):
        parts = re.split(r"(?<=[.!?])\\s+|\\n+", row["content"])
        for part_index, part in enumerate(parts):
            text = part.strip()
            terms = _ticket_terms(text)
            if len(text) < 20 or len(terms) < 2:
                continue
            sentences.append({
                "row_index": row_index,
                "part_index": part_index,
                "author": row["author"],
                "created_at": row["created_at"],
                "content": text[:320],
                "terms": terms,
            })
            for term in terms:
                frequencies[term] = frequencies.get(term, 0) + 1

    scored = []
    for sentence in sentences:
        terms = sentence["terms"]
        rarity = sum(1.0 / (1 + frequencies.get(term, 0)) for term in terms)
        score = rarity + min(len(terms), 12) * 0.05
        scored.append((score, sentence))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["row_index"], pair[1]["part_index"]))

    selected = []
    for _score, sentence in scored:
        current_terms = sentence["terms"]
        too_similar = any(
            len(current_terms & item["terms"]) / max(1, len(current_terms | item["terms"])) > 0.65
            for item in selected
        )
        if too_similar:
            continue
        selected.append(sentence)
        if len(selected) >= point_limit:
            break

    if not selected:
        last = clean[-1]
        selected = [{
            "author": last["author"],
            "created_at": last["created_at"],
            "content": last["content"][:320],
            "row_index": len(clean) - 1,
            "part_index": 0,
            "terms": _ticket_terms(last["content"]),
        }]
    selected.sort(key=lambda item: (item["row_index"], item["part_index"]))
    return {
        "message_count": len(clean),
        "participants": participants[:10],
        "attachment_count": attachments,
        "first_at": clean[0]["created_at"],
        "last_at": clean[-1]["created_at"],
        "key_points": [
            {
                "author": item["author"],
                "created_at": item["created_at"],
                "content": item["content"],
            }
            for item in selected
        ],
    }


# ------------------------------------------------------------------ related ticket links (staff-only)
MAX_TICKET_LINK_NOTE_LENGTH = 500


def _ticket_links_table(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS ticket_links ("
        "ticket_id_a TEXT NOT NULL, ticket_id_b TEXT NOT NULL, "
        "created_by_id TEXT NOT NULL, created_by_name TEXT NOT NULL, "
        "note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
        "PRIMARY KEY (ticket_id_a,ticket_id_b), CHECK (ticket_id_a < ticket_id_b), "
        "FOREIGN KEY (ticket_id_a) REFERENCES tickets(id) ON DELETE CASCADE, "
        "FOREIGN KEY (ticket_id_b) REFERENCES tickets(id) ON DELETE CASCADE)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_links_b ON ticket_links(ticket_id_b)")


def _ticket_pair(first_id: str, second_id: str) -> tuple[str, str]:
    first = str(first_id or "").strip()
    second = str(second_id or "").strip()
    if not first or not second:
        raise ValueError("Bitte zwei Ticket-IDs angeben.")
    if first == second:
        raise ValueError("Ein Ticket kann nicht mit sich selbst verknüpft werden.")
    return tuple(sorted((first, second)))


def link_tickets(
    first_id: str,
    second_id: str,
    actor_id,
    actor_name: str,
    note: str = "",
) -> bool:
    """Create one bidirectional, same-guild relationship; True only when newly inserted."""
    ticket_a, ticket_b = _ticket_pair(first_id, second_id)
    actor_id = str(actor_id or "").strip()
    actor_name = str(actor_name or "Teammitglied").strip()[:100] or "Teammitglied"
    note = str(note or "").strip()
    if not actor_id:
        raise ValueError("Das ausführende Teammitglied fehlt.")
    if len(note) > MAX_TICKET_LINK_NOTE_LENGTH:
        raise ValueError(f"Die Verknüpfungsnotiz darf höchstens {MAX_TICKET_LINK_NOTE_LENGTH} Zeichen enthalten.")

    with pulse_db.connect() as db:
        _ticket_links_table(db)
        rows = db.execute(
            "SELECT id,guild_id FROM tickets WHERE id IN (?,?)",
            (ticket_a, ticket_b),
        ).fetchall()
        tickets = {str(row["id"]): str(row["guild_id"]) for row in rows}
        if ticket_a not in tickets or ticket_b not in tickets:
            raise ValueError("Mindestens eines der Tickets wurde nicht gefunden.")
        if tickets[ticket_a] != tickets[ticket_b]:
            raise ValueError("Tickets aus unterschiedlichen Discord-Servern können nicht verknüpft werden.")
        cursor = db.execute(
            "INSERT OR IGNORE INTO ticket_links "
            "(ticket_id_a,ticket_id_b,created_by_id,created_by_name,note,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (ticket_a, ticket_b, actor_id, actor_name, note, pulse_db._now()),
        )
        return cursor.rowcount == 1


def linked_tickets(ticket_id: str, limit: int = 10, offset: int = 0) -> list[dict]:
    """List a bounded page of related tickets in either direction; caller authorizes staff access."""
    ticket_id = str(ticket_id or "").strip()
    if not ticket_id:
        return []
    try:
        page_size = max(1, min(int(limit), 11))
        page_offset = max(0, int(offset))
    except (TypeError, ValueError):
        page_size, page_offset = 10, 0
    with pulse_db.connect() as db:
        _ticket_links_table(db)
        rows = db.execute(
            """
            SELECT
                CASE WHEN link.ticket_id_a=? THEN link.ticket_id_b ELSE link.ticket_id_a END AS related_ticket_id,
                ticket.category, ticket.status, ticket.priority, ticket.user_name,
                ticket.opened_at, ticket.channel_id,
                link.created_by_id, link.created_by_name, link.note AS link_note, link.created_at
            FROM ticket_links AS link
            JOIN tickets AS ticket
              ON ticket.id = CASE WHEN link.ticket_id_a=? THEN link.ticket_id_b ELSE link.ticket_id_a END
            WHERE link.ticket_id_a=? OR link.ticket_id_b=?
            ORDER BY link.created_at DESC, related_ticket_id
            LIMIT ? OFFSET ?
            """,
            (ticket_id, ticket_id, ticket_id, ticket_id, page_size, page_offset),
        ).fetchall()
    return [dict(row) for row in rows]


def unlink_tickets(first_id: str, second_id: str) -> bool:
    """Remove the bidirectional relationship regardless of the supplied ID order."""
    ticket_a, ticket_b = _ticket_pair(first_id, second_id)
    with pulse_db.connect() as db:
        _ticket_links_table(db)
        cursor = db.execute(
            "DELETE FROM ticket_links WHERE ticket_id_a=? AND ticket_id_b=?",
            (ticket_a, ticket_b),
        )
        return cursor.rowcount == 1


# ------------------------------------------------------------------ internal notes (staff-only; never written to public ticket events)
MAX_INTERNAL_NOTE_LENGTH = 1500
MAX_INTERNAL_NOTES_PAGE_SIZE = 5


def _internal_notes_table(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS ticket_internal_notes ("
        "note_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, author_id TEXT NOT NULL, "
        "author_name TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticket_internal_notes_ticket "
        "ON ticket_internal_notes(ticket_id, created_at DESC)"
    )


def add_internal_note(ticket_id: str, author_id, author_name: str, note: str) -> dict:
    """Store a team-only note separately from ticket_events and all public ticket messages."""
    ticket_id = str(ticket_id or "").strip()
    author_id = str(author_id or "").strip()
    author_name = str(author_name or "Teammitglied").strip()[:100] or "Teammitglied"
    note = str(note or "").strip()
    if not ticket_id or not author_id:
        raise ValueError("Ticket und Teammitglied müssen angegeben werden.")
    if len(note) < 5:
        raise ValueError("Die interne Notiz muss mindestens 5 Zeichen enthalten.")
    if len(note) > MAX_INTERNAL_NOTE_LENGTH:
        raise ValueError(f"Die interne Notiz darf höchstens {MAX_INTERNAL_NOTE_LENGTH} Zeichen enthalten.")

    with pulse_db.connect() as db:
        _internal_notes_table(db)
        ticket = db.execute("SELECT status FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if ticket is None or str(ticket["status"] or "").lower() == "closed":
            raise ValueError("Interne Notizen können nur in einem vorhandenen, offenen Ticket gespeichert werden.")
        saved = {
            "note_id": pulse_db.make_id("tn"),
            "ticket_id": ticket_id,
            "author_id": author_id,
            "author_name": author_name,
            "note": note,
            "created_at": pulse_db._now(),
        }
        db.execute(
            "INSERT INTO ticket_internal_notes (note_id,ticket_id,author_id,author_name,note,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (saved["note_id"], ticket_id, author_id, author_name, note, saved["created_at"]),
        )
    return saved


def internal_notes_for(ticket_id: str, limit: int = 2, offset: int = 0) -> list[dict]:
    """Return newest staff-only notes for the caller to render after its permission check."""
    ticket_id = str(ticket_id or "").strip()
    if not ticket_id:
        return []
    try:
        page_size = max(1, min(int(limit), MAX_INTERNAL_NOTES_PAGE_SIZE))
        page_offset = max(0, int(offset))
    except (TypeError, ValueError):
        page_size, page_offset = 2, 0
    with pulse_db.connect() as db:
        _internal_notes_table(db)
        rows = db.execute(
            "SELECT note_id,ticket_id,author_id,author_name,note,created_at "
            "FROM ticket_internal_notes WHERE ticket_id=? "
            "ORDER BY created_at DESC, note_id DESC LIMIT ? OFFSET ?",
            (ticket_id, page_size, page_offset),
        ).fetchall()
    return [dict(row) for row in rows]


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
