"""Audit #100: revision-proof communication journal for applicants.

Every message Pulse sends to an applicant (receipt, interview approval, appointment, reschedule, cancellation,
reminder, hiring, rejection) is recorded with its outcome - *including failed and undeliverable messages*, so the
team can later answer "was the applicant told, when, by whom, and what did the text say?".

Properties
* append-only: this module has no function that changes or removes a single entry (only :func:`purge`, the
  deliberate end-of-retention removal of *all* entries of an application);
* tamper-evident: each entry carries the SHA-256 of its predecessor in the same application (a hash chain over the
  fixed fields and the hash of the text); :func:`verify` recomputes it and reports the first broken entry.
  A chain proves that stored entries were not edited, reordered or removed *in the middle*; it cannot prove that
  the newest entries were not cut off - that needs an external anchor and is not claimed;
* complete for the paths that send: failures to record never block sending (:func:`record_safe` logs and returns
  ``None``), and recording happens after the attempt so the stored outcome is the real one;
* privacy: only the text actually sent (bounded) and the actor are stored, no raw error messages; entries are
  part of the personal-data export of the applicant and are removed together with the application by the
  retention run.

The journal is written by the existing sending paths in ``webserver.py``; it does not send anything itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from datetime import datetime, timezone

import pulse_db

log = logging.getLogger("pulse.app_journal")

GENESIS = "0" * 64
MAX_TEXT = 1900
MAX_LIST = 200
KINDS = ("received", "interview", "scheduled", "rescheduled", "cancelled", "reminder", "hired", "rejected",
         "rejected_after_interview")
OUTCOMES = ("sent", "failed")
CHANNELS = ("dm",)
# What failed, as a fixed vocabulary - never raw exception text (it can contain names or IDs).
FAILURES = ("bot_unavailable", "no_discord_id", "user_not_found", "dm_not_delivered", "error")
_RETRIES = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entry_hash(row: dict) -> str:
    """Hash over every fixed field of an entry (``text`` is covered through ``text_sha256``)."""
    payload = [row["app_id"], int(row["seq"]), row["at"], row["channel"], row["kind"], row["outcome"], row["detail"],
               row["actor_id"], row["template_ref"], row["text_sha256"], row["prev_hash"]]
    return _sha(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def record(app_id, kind, outcome, text, *, detail="", actor_id="", actor_name="", channel="dm", template_ref="",
           now=None) -> dict:
    """Append one entry to the application's chain and return it. Raises ``ValueError`` for invalid input."""
    app = str(app_id or "").strip()
    if not app or len(app) > 80:
        raise ValueError("Bewerbungs-ID fehlt oder ist zu lang.")
    if kind not in KINDS:
        raise ValueError(f"Unbekannte Nachrichtenart: {kind!r}")
    if outcome not in OUTCOMES:
        raise ValueError(f"Unbekanntes Ergebnis: {outcome!r}")
    if channel not in CHANNELS:
        raise ValueError(f"Unbekannter Kanal: {channel!r}")
    detail = str(detail or "")
    if outcome == "sent":
        detail = ""
    elif detail not in FAILURES:
        raise ValueError(f"Unbekannter Fehlergrund: {detail!r}")
    body = str(text or "")[:MAX_TEXT]
    base = {"app_id": app, "at": now or _now(), "channel": channel, "kind": kind, "outcome": outcome, "detail": detail,
            "actor_id": str(actor_id or "")[:40], "actor_name": str(actor_name or "")[:100],
            "template_ref": str(template_ref or "")[:80], "text": body, "text_sha256": _sha(body)}
    for _ in range(_RETRIES):  # two writers racing for the same sequence number: the loser retries on the new head
        try:
            with pulse_db.connect() as db:
                head = db.execute("SELECT seq, entry_hash FROM application_comm_journal WHERE app_id=? "
                                  "ORDER BY seq DESC LIMIT 1", (app,)).fetchone()
                row = {**base, "id": "acj_" + secrets.token_hex(6), "seq": (head["seq"] + 1) if head else 1,
                       "prev_hash": head["entry_hash"] if head else GENESIS}
                row["entry_hash"] = entry_hash(row)
                db.execute("INSERT INTO application_comm_journal (id,app_id,seq,at,channel,kind,outcome,detail,actor_id,"
                           "actor_name,template_ref,text,text_sha256,prev_hash,entry_hash) VALUES "
                           "(:id,:app_id,:seq,:at,:channel,:kind,:outcome,:detail,:actor_id,:actor_name,:template_ref,"
                           ":text,:text_sha256,:prev_hash,:entry_hash)", row)
            return row
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Journal-Eintrag konnte wegen gleichzeitiger Schreibzugriffe nicht angehängt werden.")


def record_safe(*args, **kwargs):
    """Like :func:`record`, but a journal problem must never stop or fail the message flow; returns ``None``."""
    try:
        return record(*args, **kwargs)
    except Exception:  # noqa: BLE001
        log.warning("Bewerber-Journal: Eintrag konnte nicht gespeichert werden", exc_info=True)
        return None


def entries(app_id, limit=50, *, newest_first=True) -> list[dict]:
    limit = max(1, min(int(limit), MAX_LIST))
    order = "DESC" if newest_first else "ASC"
    with pulse_db.connect() as db:
        rows = db.execute(f"SELECT * FROM application_comm_journal WHERE app_id=? ORDER BY seq {order} LIMIT ?",
                          (str(app_id or "").strip(), limit)).fetchall()
    return [dict(r) for r in rows]


def verify(app_id) -> dict:
    """Recompute the chain: ``{"ok", "count", "broken_at", "reason", "head"}`` (``broken_at`` = first bad ``seq``)."""
    with pulse_db.connect() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM application_comm_journal WHERE app_id=? ORDER BY seq",
                                            (str(app_id or "").strip(),)).fetchall()]
    prev, expected = GENESIS, 1
    for row in rows:
        problem = None
        if row["seq"] != expected:
            problem = "Eintrag fehlt oder Reihenfolge verändert"
        elif row["prev_hash"] != prev:
            problem = "Verkettung unterbrochen"
        elif row["text"] is not None and _sha(row["text"]) != row["text_sha256"]:
            problem = "Text verändert"
        elif entry_hash(row) != row["entry_hash"]:
            problem = "Eintrag verändert"
        if problem:
            return {"ok": False, "count": len(rows), "broken_at": row["seq"], "reason": problem, "head": prev}
        prev, expected = row["entry_hash"], expected + 1
    return {"ok": True, "count": len(rows), "broken_at": None, "reason": "", "head": prev}


def export_for(app_ids) -> dict[str, list[dict]]:
    """All entries per application (oldest first) for the personal-data export; read-only."""
    ids = [str(i) for i in app_ids if str(i or "").strip()]
    if not ids:
        return {}
    with pulse_db.connect() as db:
        rows = db.execute(f"SELECT * FROM application_comm_journal WHERE app_id IN ({','.join('?' * len(ids))}) "
                          "ORDER BY app_id, seq", ids).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["app_id"], []).append(dict(row))
    return out


def purge(app_ids) -> int:
    """Remove *all* entries of the given applications (retention: they leave with the application). Returns the count."""
    ids = [str(i) for i in app_ids if str(i or "").strip()]
    if not ids:
        return 0
    with pulse_db.connect() as db:
        return db.execute(f"DELETE FROM application_comm_journal WHERE app_id IN ({','.join('?' * len(ids))})", ids).rowcount

