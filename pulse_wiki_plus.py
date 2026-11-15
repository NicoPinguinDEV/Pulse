"""Audit #111 and #113: wiki full-text search and must-read acknowledgements for service instructions.

* **Search** over title, category and text of every wiki page (readable by every dashboard user, like ``/wiki``) with a
  short snippet around the first match.
* **Must-read (Pflichtlektüre)**: a wiki manager flags a page as binding. Every team member has to confirm the *current
  version*; saving a new version automatically makes the page pending again for everybody (the confirmation belongs to a
  version, not to the page). Managers see who has confirmed which version and who is missing.

Storage: ``wiki_required_reads`` (flag + who/when) and ``wiki_read_acks`` (page, version, person, time), both part of the backup.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pulse_db

MAX_RESULTS = 30
SNIPPET = 160


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", str(query or "").casefold().strip()[:100]) if len(t) >= 2][:6]


def search(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """Pages containing *all* terms, best first (title 5, category 2, each occurrence in the text 1)."""
    terms = _terms(query)
    if not terms:
        return []
    out = []
    for page in pulse_db.wiki_pages():
        title, category, content = str(page.get("title", "")), str(page.get("category", "")), str(page.get("content", ""))
        low_t, low_c, low_x = title.casefold(), category.casefold(), content.casefold()
        if not all(t in low_t or t in low_c or t in low_x for t in terms):
            continue
        score = sum(5 * (t in low_t) + 2 * (t in low_c) + min(low_x.count(t), 10) for t in terms)
        pos = min((low_x.find(t) for t in terms if t in low_x), default=-1)
        if pos < 0:
            snippet = content[:SNIPPET]
        else:
            start = max(0, pos - 40)
            snippet = ("…" if start else "") + content[start:start + SNIPPET] + ("…" if start + SNIPPET < len(content) else "")
        out.append({"id": page["id"], "title": title, "category": category, "snippet": " ".join(snippet.split()), "score": score})
    out.sort(key=lambda r: (-r["score"], r["title"].casefold()))
    return out[:max(1, min(int(limit), MAX_RESULTS))]


def current_version(page_id) -> int:
    versions = pulse_db.wiki_page_versions(page_id, 1)
    return int(versions[0]["version_number"]) if versions else 0


def is_required(page_id) -> bool:
    with pulse_db.connect() as db:
        return db.execute("SELECT 1 FROM wiki_required_reads WHERE page_id=?", (str(page_id),)).fetchone() is not None


def set_required(page_id, required: bool, actor_id="", actor_name="") -> bool:
    """Flag or unflag a page; ``False`` if the page does not exist. Existing confirmations are kept (history)."""
    if pulse_db.get_wiki(page_id) is None:
        return False
    with pulse_db.connect() as db:
        if required:
            db.execute("INSERT OR REPLACE INTO wiki_required_reads (page_id,set_by_id,set_by_name,set_at) VALUES (?,?,?,?)",
                       (str(page_id), str(actor_id)[:30], str(actor_name)[:100], _now()))
        else:
            db.execute("DELETE FROM wiki_required_reads WHERE page_id=?", (str(page_id),))
    return True


def acknowledge(page_id, user_id, user_name="") -> int:
    """Confirm the *current* version of a must-read page. Returns the confirmed version, 0 if the page is not binding."""
    if not is_required(page_id):
        return 0
    version = current_version(page_id)
    if version <= 0:
        return 0
    with pulse_db.connect() as db:
        db.execute("INSERT OR IGNORE INTO wiki_read_acks (page_id,version,user_id,user_name,at) VALUES (?,?,?,?,?)",
                   (str(page_id), version, str(user_id), str(user_name)[:100], _now()))
    return version


def pending_for(user_id) -> list[dict]:
    """Must-read pages whose current version this person has not confirmed yet."""
    with pulse_db.connect() as db:
        required = [str(r["page_id"]) for r in db.execute("SELECT page_id FROM wiki_required_reads").fetchall()]
    out = []
    for page_id in required:
        page = pulse_db.get_wiki(page_id)
        version = current_version(page_id)
        if page is None or version <= 0:
            continue
        with pulse_db.connect() as db:
            done = db.execute("SELECT 1 FROM wiki_read_acks WHERE page_id=? AND version=? AND user_id=?", (page_id, version, str(user_id))).fetchone()
        if not done:
            out.append({"id": page_id, "title": page["title"], "category": page["category"], "version": version})
    return sorted(out, key=lambda r: r["title"].casefold())


def coverage(page_id, team) -> dict:
    """For managers: ``team`` = [(id, name)]; who confirmed the current version and who is still missing."""
    version = current_version(page_id)
    with pulse_db.connect() as db:
        rows = db.execute("SELECT user_id,at FROM wiki_read_acks WHERE page_id=? AND version=?", (str(page_id), version)).fetchall()
    done = {str(r["user_id"]): r["at"] for r in rows}
    confirmed = [(i, n, done[str(i)]) for i, n in team if str(i) in done]
    missing = [(i, n) for i, n in team if str(i) not in done]
    return {"version": version, "confirmed": confirmed, "missing": missing}


def required_pages() -> list[dict]:
    with pulse_db.connect() as db:
        ids = [str(r["page_id"]) for r in db.execute("SELECT page_id FROM wiki_required_reads").fetchall()]
    out = []
    for pid in ids:
        page = pulse_db.get_wiki(pid)
        if page is not None:
            out.append({"id": pid, "title": page["title"], "category": page["category"], "version": current_version(pid)})
    return sorted(out, key=lambda r: r["title"].casefold())
