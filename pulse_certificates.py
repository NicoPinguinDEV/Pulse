"""Audit #109/#110: automatic certificates after a passed training and refresher reminders before they expire.

Tables (pulse.db, part of the JSON backup via ``pulse_db.TABLES``):
- ``training_certificates``: one row per passed attempt (``attempt_id`` is unique, so re-processing is idempotent).
  A newer certificate for the same person and training supersedes the older one (``superseded_by``); history is kept.
- ``training_refresher_rules``: optional validity per training (``valid_days``) and warning lead time (``warn_days``).
  Without a rule a certificate never expires.
- ``training_certificate_notices``: one row per (certificate, kind) that was already announced, so the periodic
  check never notifies twice.

Rules:
- Certificates are issued only from a stored, passed attempt – never manually here (manual awards stay in
  ``ultimate_certificates``).
- Validity is computed from ``valid_until`` at read time; a wrong clock cannot hide an expiry.
- Notifications go to the certificate holder only; the overview for leadership lists names and dates, no scores.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pulse_db

MAX_VALID_DAYS = 3650
MAX_WARN_DAYS = 365
DEFAULT_WARN_DAYS = 14

STATUS_VALID = "valid"
STATUS_EXPIRING = "expiring"
STATUS_EXPIRED = "expired"
STATUS_SUPERSEDED = "superseded"
STATUS_LABELS = {
    STATUS_VALID: "gültig",
    STATUS_EXPIRING: "läuft bald ab",
    STATUS_EXPIRED: "abgelaufen",
    STATUS_SUPERSEDED: "ersetzt",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def set_rule(training_id: str, valid_days: int, warn_days: int, actor_id) -> dict | None:
    """Store the refresher rule; ``valid_days`` 0 removes it (certificates never expire). Raises ValueError."""
    if not pulse_db.get_training(training_id):
        raise ValueError("Schulung nicht gefunden.")
    valid_days = int(valid_days)
    warn_days = int(warn_days)
    pulse_db.init_db()
    with pulse_db.connect() as db:
        if valid_days == 0:
            db.execute("DELETE FROM training_refresher_rules WHERE training_id=?", (training_id,))
            return None
        if not 1 <= valid_days <= MAX_VALID_DAYS:
            raise ValueError(f"Die Gültigkeit muss zwischen 1 und {MAX_VALID_DAYS} Tagen liegen (0 = unbegrenzt).")
        if not 1 <= warn_days <= MAX_WARN_DAYS or warn_days >= valid_days:
            raise ValueError("Die Vorwarnzeit muss mindestens 1 Tag und kürzer als die Gültigkeit sein.")
        db.execute(
            "INSERT INTO training_refresher_rules(training_id,valid_days,warn_days,updated_by_id,updated_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(training_id) DO UPDATE SET valid_days=excluded.valid_days,"
            " warn_days=excluded.warn_days, updated_by_id=excluded.updated_by_id, updated_at=excluded.updated_at",
            (training_id, valid_days, warn_days, str(actor_id), _iso(_now())),
        )
    return {"training_id": training_id, "valid_days": valid_days, "warn_days": warn_days}


def get_rule(training_id: str) -> dict | None:
    pulse_db.init_db()
    with pulse_db.connect() as db:
        row = db.execute("SELECT * FROM training_refresher_rules WHERE training_id=?", (training_id,)).fetchone()
    return dict(row) if row else None


def issue_for_attempt(attempt_id: str, now: datetime | None = None) -> dict | None:
    """Issue (or return the existing) certificate for a stored, passed attempt. Failed attempts yield None."""
    now = now or _now()
    pulse_db.init_db()
    with pulse_db.connect() as db:
        existing = db.execute("SELECT * FROM training_certificates WHERE attempt_id=?", (attempt_id,)).fetchone()
        if existing:
            return dict(existing)
        attempt = db.execute("SELECT * FROM training_attempts WHERE id=?", (attempt_id,)).fetchone()
        if not attempt or not attempt["passed"]:
            return None
        training = db.execute("SELECT title FROM trainings WHERE id=?", (attempt["training_id"],)).fetchone()
        if not training:
            return None
        rule = db.execute(
            "SELECT valid_days FROM training_refresher_rules WHERE training_id=?", (attempt["training_id"],)
        ).fetchone()
        valid_until = _iso(now + timedelta(days=int(rule["valid_days"]))) if rule else None
        cert_id = pulse_db.make_id("tcert")
        db.execute(
            "INSERT INTO training_certificates(id,attempt_id,training_id,user_id,user_name,title,score,issued_at,"
            "valid_until,verify_code,superseded_by) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
            (cert_id, attempt_id, attempt["training_id"], attempt["user_id"], attempt["user_name"], training["title"],
             int(attempt["score"]), _iso(now), valid_until, secrets.token_hex(8).upper()),
        )
        db.execute(
            "UPDATE training_certificates SET superseded_by=? WHERE user_id=? AND training_id=? AND id<>?"
            " AND superseded_by IS NULL",
            (cert_id, attempt["user_id"], attempt["training_id"], cert_id),
        )
        row = db.execute("SELECT * FROM training_certificates WHERE id=?", (cert_id,)).fetchone()
    return dict(row)


def status(cert: dict, now: datetime | None = None) -> str:
    now = now or _now()
    if cert.get("superseded_by"):
        return STATUS_SUPERSEDED
    until = _parse(cert.get("valid_until"))
    if until is None:
        return STATUS_VALID
    if now >= until:
        return STATUS_EXPIRED
    rule = get_rule(cert["training_id"])
    warn_days = int(rule["warn_days"]) if rule else DEFAULT_WARN_DAYS
    return STATUS_EXPIRING if now >= until - timedelta(days=warn_days) else STATUS_VALID


def _decorate(cert: dict, now: datetime) -> dict:
    item = dict(cert)
    item["status"] = status(item, now)
    item["status_label"] = STATUS_LABELS[item["status"]]
    return item


def certificates_for_user(user_id, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    pulse_db.init_db()
    with pulse_db.connect() as db:
        rows = db.execute(
            "SELECT * FROM training_certificates WHERE user_id=? ORDER BY issued_at DESC LIMIT 200", (str(user_id),)
        ).fetchall()
    return [_decorate(dict(row), now) for row in rows]


def verify(code: str, now: datetime | None = None) -> dict | None:
    """Look a certificate up by its verification code. The score is not part of the result."""
    code = str(code or "").strip().upper()
    if not code or len(code) > 32:
        return None
    pulse_db.init_db()
    with pulse_db.connect() as db:
        row = db.execute("SELECT * FROM training_certificates WHERE verify_code=?", (code,)).fetchone()
    if not row:
        return None
    item = _decorate(dict(row), now or _now())
    item.pop("score", None)
    return item


def attention_list(now: datetime | None = None, limit: int = 100) -> list[dict]:
    """Current (not superseded) certificates that are expiring or expired, soonest first – for leadership."""
    now = now or _now()
    pulse_db.init_db()
    with pulse_db.connect() as db:
        rows = db.execute(
            "SELECT * FROM training_certificates WHERE superseded_by IS NULL AND valid_until IS NOT NULL"
            " ORDER BY valid_until ASC LIMIT 2000"
        ).fetchall()
    result = []
    for row in rows:
        item = _decorate(dict(row), now)
        if item["status"] in (STATUS_EXPIRING, STATUS_EXPIRED):
            item.pop("score", None)
            result.append(item)
    return result[: max(1, int(limit))]


def process_notices(now: datetime | None = None, notifier=None) -> dict:
    """Notify holders once per certificate about the upcoming and the reached expiry (idempotent)."""
    now = now or _now()
    notifier = notifier or pulse_db.notify
    sent = {"warn": 0, "expired": 0}
    pulse_db.init_db()
    for item in attention_list(now, limit=2000):
        kind = "expired" if item["status"] == STATUS_EXPIRED else "warn"
        with pulse_db.connect() as db:
            claimed = db.execute(
                "INSERT OR IGNORE INTO training_certificate_notices(certificate_id,kind,sent_at) VALUES(?,?,?)",
                (item["id"], kind, _iso(now)),
            ).rowcount
        if not claimed:
            continue
        until = str(item["valid_until"])[:10]
        if kind == "expired":
            title = "🎓 Zertifikat abgelaufen"
            body = f"Dein Zertifikat „{item['title']}“ ist seit {until} abgelaufen. Bitte die Auffrischung ablegen."
            level = "warning"
        else:
            title = "🎓 Auffrischung bald fällig"
            body = f"Dein Zertifikat „{item['title']}“ läuft am {until} ab. Lege die Schulung rechtzeitig erneut ab."
            level = "info"
        notifier(item["user_id"], title, body, level, f"/training/{item['training_id']}")
        sent[kind] += 1
    return sent
