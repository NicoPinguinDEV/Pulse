"""Audit #111: redaktionelle Freigabe (Vier-Augen-Prinzip) für Wiki-Seiten.

Eine Freigabe gehört zu einer **Version** (nicht zur Seite): Wer eine Version einreicht, kann sie nicht selbst freigeben,
und eine neue Version ist wieder ohne Freigabe. Die Freigabe ist eine zusätzliche Information - sie blockiert weder das
Speichern noch das Lesen und ändert keine bestehende Wiki-Funktion.

Zustände je Version: ``none`` (nie eingereicht), ``pending`` (Freigabe offen), ``approved``, ``rejected``.
Speicher: Tabelle ``wiki_version_reviews`` (Teil des Backups). Entscheidungen bleiben bei Menschen.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pulse_db

STATES = ("none", "pending", "approved", "rejected")
LABELS = {"none": "Keine Freigabe angefordert", "pending": "Freigabe offen", "approved": "Freigegeben", "rejected": "Abgelehnt"}
MAX_COMMENT = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version(page_id) -> int:
    versions = pulse_db.wiki_page_versions(page_id, 1)
    return int(versions[0]["version_number"]) if versions else 0


def _row(page_id, version):
    with pulse_db.connect() as db:
        row = db.execute("SELECT * FROM wiki_version_reviews WHERE page_id=? AND version=?", (str(page_id), int(version))).fetchone()
    return dict(row) if row else None


def state(page_id) -> dict:
    """Review state of the *current* version: ``{state, label, version, by, comment, at}``."""
    version = _version(page_id)
    row = _row(page_id, version) if version else None
    if not row:
        return {"state": "none", "label": LABELS["none"], "version": version, "by": "", "comment": "", "at": ""}
    st = row["status"] if row["status"] in STATES else "none"
    by = row["reviewer_name"] if st in ("approved", "rejected") else row["requester_name"]
    return {"state": st, "label": LABELS[st], "version": version, "by": by, "comment": row["comment"],
            "at": row["decided_at"] or row["requested_at"]}


def request_review(page_id, user_id, user_name="") -> tuple[bool, str]:
    """Submit the current version for approval. Returns ``(ok, code)``: ``ok``/``not_found``/``already_pending``/``already_approved``."""
    if pulse_db.get_wiki(page_id) is None:
        return False, "not_found"
    version = _version(page_id)
    current = _row(page_id, version)
    if current and current["status"] == "pending":
        return False, "already_pending"
    if current and current["status"] == "approved":
        return False, "already_approved"
    with pulse_db.connect() as db:
        db.execute("INSERT OR REPLACE INTO wiki_version_reviews (page_id,version,status,requester_id,requester_name,requested_at,"
                   "reviewer_id,reviewer_name,comment,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (str(page_id), version, "pending", str(user_id)[:30], str(user_name)[:100], _now(), "", "", "", ""))
    return True, "ok"


def decide(page_id, reviewer_id, reviewer_name, approve: bool, comment="") -> tuple[bool, str]:
    """Approve or reject the pending current version. Four-eyes: the requester may not decide on their own request,
    nor the author of the version. Codes: ``ok``/``not_pending``/``own_request``/``own_version``/``comment_required``."""
    version = _version(page_id)
    row = _row(page_id, version) if version else None
    if not row or row["status"] != "pending":
        return False, "not_pending"
    reviewer = str(reviewer_id)
    if reviewer == row["requester_id"]:
        return False, "own_request"
    versions = pulse_db.wiki_page_versions(page_id, 1)
    if versions and str(versions[0].get("author_id") or "") == reviewer:
        return False, "own_version"
    text = str(comment or "").strip()[:MAX_COMMENT]
    if not approve and not text:
        return False, "comment_required"
    with pulse_db.connect() as db:
        db.execute("UPDATE wiki_version_reviews SET status=?,reviewer_id=?,reviewer_name=?,comment=?,decided_at=? WHERE page_id=? AND version=?",
                   ("approved" if approve else "rejected", reviewer[:30], str(reviewer_name)[:100], text, _now(), str(page_id), version))
    return True, "ok"


def pending_pages() -> list[dict]:
    """Pages whose *current* version waits for a decision (newest request first)."""
    out = []
    for page in pulse_db.wiki_pages():
        st = state(page["id"])
        if st["state"] == "pending":
            out.append({"id": page["id"], "title": page["title"], "category": page["category"], **st})
    return sorted(out, key=lambda item: item["at"], reverse=True)
