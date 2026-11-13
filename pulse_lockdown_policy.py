"""Audit #20: timed expiry and a second-person approval for the *manual* emergency lockdown.

Pure logic plus two settings keys in pulse.db (no Discord calls, no new table):

- ``pulse_antinuke_lockdown_policy``: ``{"require_approval": bool, "default_minutes": int | None}``.
  Defaults keep the old behaviour (immediate start, no expiry).
- ``pulse_antinuke_lockdown_request``: one pending start request (``require_approval`` on). It expires after
  REQUEST_TTL_MINUTES, can be approved only by a different administrator and is consumed on approval or cancel.

The automatic Anti-Nuke lockdown (a rule fired) is never delayed by an approval: it is an emergency reaction
the administrators configured beforehand. Both kinds honour the stored expiry.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

POLICY_KEY = "pulse_antinuke_lockdown_policy"
REQUEST_KEY = "pulse_antinuke_lockdown_request"
MIN_MINUTES = 5
MAX_MINUTES = 1440
REQUEST_TTL_MINUTES = 15
MAX_EXPIRY_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_minutes(value) -> int | None:
    """Empty / 0 = no expiry. Otherwise MIN_MINUTES..MAX_MINUTES, else ValueError."""
    if value in (None, "", 0, "0"):
        return None
    try:
        minutes = int(str(value).strip())
    except ValueError:
        raise ValueError("Die Dauer muss eine ganze Zahl in Minuten sein.") from None
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValueError(f"Die Dauer muss zwischen {MIN_MINUTES} und {MAX_MINUTES} Minuten liegen (leer = bis zur Aufhebung).")
    return minutes


def normalize_policy(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    try:
        default_minutes = parse_minutes(raw.get("default_minutes"))
    except ValueError:
        default_minutes = None
    return {"require_approval": raw.get("require_approval") is True, "default_minutes": default_minutes}


def expires_at(now: datetime, minutes: int | None) -> str | None:
    return _iso(now + timedelta(minutes=minutes)) if minutes else None


def is_expired(state: dict, now: datetime | None = None) -> bool:
    """An active lockdown whose stored expiry has passed. Missing/garbled expiry never expires (fail-safe: stay locked)."""
    if not isinstance(state, dict) or not state.get("active"):
        return False
    expiry = _parse(state.get("expires_at"))
    return expiry is not None and (now or _now()) >= expiry


def retry_allowed(state: dict) -> bool:
    try:
        return int(state.get("expiry_attempts") or 0) < MAX_EXPIRY_ATTEMPTS
    except (TypeError, ValueError):
        return False


def new_request(by_id, by_name, reason: str, minutes: int | None, now: datetime | None = None) -> dict:
    reason = str(reason or "").strip()[:300]
    if len(reason) < 5:
        raise ValueError("Bitte einen Grund (mindestens 5 Zeichen) angeben.")
    now = now or _now()
    return {"id": "LDR-" + secrets.token_hex(3).upper(), "by_id": str(by_id), "by": str(by_name or "")[:80],
            "reason": reason, "minutes": minutes, "at": _iso(now),
            "valid_until": _iso(now + timedelta(minutes=REQUEST_TTL_MINUTES))}


def request_is_open(request: dict | None, now: datetime | None = None) -> bool:
    if not isinstance(request, dict) or not request.get("id"):
        return False
    until = _parse(request.get("valid_until"))
    return until is not None and (now or _now()) < until


def check_approval(request: dict | None, approver_id, now: datetime | None = None) -> None:
    """Raises ValueError (safe message) unless a different person approves an open request."""
    if not request_is_open(request, now):
        raise ValueError("Keine offene Lockdown-Anfrage (abgelaufen oder bereits entschieden).")
    if str((request or {}).get("by_id")) == str(approver_id):
        raise ValueError("Die Freigabe muss durch eine andere Administratorin oder einen anderen Administrator erfolgen.")


# ---- persistence (settings table)

def load_policy() -> dict:
    import pulse_db
    return normalize_policy(pulse_db.get_setting(POLICY_KEY, {}))


def save_policy(policy: dict) -> dict:
    import pulse_db
    clean = normalize_policy(policy)
    pulse_db.set_setting(POLICY_KEY, clean)
    return clean


def load_request(now: datetime | None = None) -> dict | None:
    import pulse_db
    request = pulse_db.get_setting(REQUEST_KEY, None)
    return request if request_is_open(request, now) else None


def save_request(request: dict) -> None:
    import pulse_db
    pulse_db.set_setting(REQUEST_KEY, request)


def clear_request() -> None:
    import pulse_db
    pulse_db.set_setting(REQUEST_KEY, {})
