"""Audit #44: Umfragen als Discord-Karte mit Buttons (Daten und Regeln; die Buttons liegen in ``cogs/pulse_pro.py``).

Eine Umfrage ist dieselbe Zeile in ``ultimate_polls`` wie im Dashboard (``/ultimate/polls``) und wird mit derselben Funktion
abgestimmt (``pulse_ultimate.cast_poll_vote``: eine Stimme je Person, spätere Stimme ersetzt die frühere, Ende wird erzwungen).
Die Karte zeigt nur Summen - nie, wer wie abgestimmt hat. Maximal fünf Optionen auf einer Karte (eine Button-Zeile).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pulse_ultimate as pu

MAX_OPTIONS = 5
MIN_OPTIONS = 2
MAX_HOURS = 24 * 14
TEMPLATE = r"pulse:poll:(?P<pid>[A-Za-z0-9_-]{1,60}):(?P<idx>\d)"
_POLL_ID = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")


def custom_id(poll_id: str, index: int) -> str:
    if not _POLL_ID.match(str(poll_id or "")) or not 0 <= int(index) < MAX_OPTIONS:
        raise ValueError("Ungültige Karte.")
    return f"pulse:poll:{poll_id}:{int(index)}"


def parse_options(text: str) -> list[str]:
    options = [o.strip()[:80] for o in str(text or "").split("|") if o.strip()]
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"Bitte {MIN_OPTIONS} bis {MAX_OPTIONS} Optionen mit | trennen.")
    if len({o.casefold() for o in options}) != len(options):
        raise ValueError("Optionen dürfen sich nicht wiederholen.")
    return options


def create(question: str, options_text: str, hours: int | None, user_id, user_name: str, now: datetime | None = None) -> dict:
    """Create a single-choice poll in the shared table. Raises ``ValueError`` for invalid input."""
    title = str(question or "").strip()
    if not title or len(title) > 160:
        raise ValueError("Die Frage fehlt oder ist länger als 160 Zeichen.")
    options = parse_options(options_text)
    ends = None
    if hours is not None:
        if not 1 <= int(hours) <= MAX_HOURS:
            raise ValueError(f"Die Dauer muss zwischen 1 und {MAX_HOURS} Stunden liegen.")
        ends = ((now or datetime.now(timezone.utc)) + timedelta(hours=int(hours))).isoformat(timespec="seconds")
    pu.init_db()
    pid = pu.uid("poll")
    with pu.cx() as c:
        c.execute("INSERT INTO ultimate_polls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (pid, title, "", "single", json.dumps(options, ensure_ascii=False), ends, 1, str(user_id), str(user_name)[:100], pu.iso(), 1))
    return get(pid)


def get(poll_id) -> dict | None:
    pu.init_db()
    with pu.cx() as c:
        row = c.execute("SELECT * FROM ultimate_polls WHERE id=?", (str(poll_id or ""),)).fetchone()
    if not row:
        return None
    poll = dict(row)
    poll["options"] = json.loads(poll["options_json"])
    return poll


def card_fields(poll: dict) -> dict:
    counts = pu.poll_results(poll["id"])
    total = sum(counts.values())
    lines = []
    for i, option in enumerate(poll["options"]):
        n = counts.get(i, 0)
        share = round(100 * n / total) if total else 0
        lines.append(f"**{option}** · {n} ({share} %)")
    ends = f"\nEnde: {str(poll['ends_at'])[:16].replace('T', ' ')} UTC" if poll.get("ends_at") else ""
    return {"title": f"🗳 {poll['title']}"[:250], "description": "\n".join(lines)[:3500] + ends,
            "footer": f"{total} Stimme(n) · anonym · eine Stimme je Person, spätere Stimme ersetzt die frühere"}


def vote(poll_id, user_id, index) -> dict:
    """Cast the person's vote with the shared rule and return the poll for re-rendering (raises ``PollError``)."""
    pu.cast_poll_vote(poll_id, user_id, [index])
    return get(poll_id)
