"""Audit #88: Wochen- und Monatsbericht automatisch in die Pulse-Inbox der Führung (Funktionsschalter ``report_delivery``, aus).

Der Bericht selbst bleibt der bestehende aus ``pulse_teamakte.report`` (letzte *volle* Woche bzw. Monat gegen den davor, mit den
dokumentierten Kennzahlen). Hier wird nur entschieden, *wann* er einmal pro Zeitraum zugestellt wird:

* höchstens einmal je Zeitraum (gemerkt wird der Zeitraumbeginn), auch nach einem Neustart;
* nur mit eingeschaltetem Funktionsschalter, nur an Führungskräfte (Recht ``can_promote`` oder Administrator), nur in die Inbox
  (kein DM, kein Kanal), mit Link zum Bericht;
* ein Fehler beim Zustellen wird nicht als erledigt gemerkt und im nächsten Lauf erneut versucht - nie als Dauerschleife, weil der
  Job höchstens alle 5 Minuten prüft.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pulse_teamakte as ta

log = logging.getLogger("pulse.reports")
STATE_KEY = "report_delivery_state"
CHECK_EVERY_SECONDS = 300
KINDS = (("week", "Wochenbericht"), ("month", "Monatsbericht"))
URL = "/team/planung?ansicht=bericht"
_last_check = 0.0


def due_kinds(state: dict, reports: dict) -> list[str]:
    """Kinds whose last full period has not been delivered yet (``reports``: kind -> ``ta.report`` result)."""
    state = state if isinstance(state, dict) else {}
    return [kind for kind, _label in KINDS if kind in reports and state.get(kind) != reports[kind]["start"]]


def _fmt(value) -> str:
    return "–" if value is None else f"{value:g}"


def summary(report: dict, label: str) -> tuple[str, str]:
    """``(title, body)`` - compact, numbers only (no names)."""
    title = f"📊 {label} {report['start']} bis {report['end']}"
    lines = [f"{k}: {_fmt(report['current'][k])} (vorher {_fmt(report['previous'][k])})" for k in list(report["current"])[:6]]
    return title, "\n".join(lines)[:900]


def run(ws, guild, config, now: datetime | None = None, *, db=None, enabled=None, force=False) -> list[str]:
    """Deliver due reports. Returns the delivered kinds. Never raises."""
    global _last_check
    import time
    try:
        if db is None:
            import pulse_db as db
        if enabled is None:
            import pulse_flags
            enabled = pulse_flags.enabled("report_delivery")
        if not enabled or guild is None:
            return []
        mono = time.monotonic()
        if not force and mono - _last_check < CHECK_EVERY_SECONDS:
            return []
        _last_check = mono
        now = now or datetime.now(timezone.utc)
        shifts = ws.load_shifts_raw()
        history = shifts.get("history", []) if isinstance(shifts, dict) else []
        tickets, tasks = db.list_tickets(limit=2000), db.list_tasks(limit=1000, include_archived=True)
        reports = {kind: ta.report(history, tickets, tasks, now, kind) for kind, _label in KINDS}
        state = db.get_setting(STATE_KEY, {})
        state = dict(state) if isinstance(state, dict) else {}
        due = due_kinds(state, reports)
        if not due:
            return []
        recipients = leadership_ids(guild, config)
        delivered = []
        import pulse_inbox_contract as inbox
        for kind, label in KINDS:
            if kind not in due:
                continue
            if recipients:
                title, body = summary(reports[kind], label)
                def notifier(uid, head, text, kind="info", url="", dedupe_key=None, category=None):
                    return db.notify(uid, head, text, kind, URL, dedupe_key, 7 * 86400, category)
                try:
                    inbox.deliver(recipients, "team", f"report_{kind}", title, body, correlation_id=f"report:{kind}:{reports[kind]['start']}",
                                  notifier=notifier)
                except Exception:  # noqa: BLE001 - retry next time instead of marking it done
                    log.warning("Bericht %s konnte nicht zugestellt werden", kind, exc_info=True)
                    continue
            state[kind] = reports[kind]["start"]  # also when nobody is eligible: do not retry the same period every 5 minutes
            delivered.append(kind)
        db.set_setting(STATE_KEY, state)
        return delivered
    except Exception:  # noqa: BLE001
        log.warning("Berichtszustellung fehlgeschlagen", exc_info=True)
        return []


def leadership_ids(guild, config) -> list[str]:
    import pulse_access
    import pulse_permissions
    ids = []
    for member in pulse_access.team_members(guild, config)[:300]:
        perms, _ = pulse_permissions.compute(guild, member.id, config)
        if perms.get("can_promote") or perms.get("is_admin"):
            ids.append(str(member.id))
    return ids
