"""BOT-08: early warning for raids and spam – detection only, never an automatic change.

Signals (all windows and maps are bounded, RAM limit 250 MB):
* join wave: many joins within a minute, or many very new accounts within five minutes;
* mass mentions: one message mentioning many people/roles or trying @everyone/@here;
* copy spam: the same text from the same person in several channels within a minute.
Each kind alerts at most once per COOLDOWN; the alert carries suggestions for the team. Thresholds can be
tuned via environment variables (see .env.example). Nothing here changes channel rights or punishes anyone.
"""
from __future__ import annotations

import hashlib
import os
from collections import OrderedDict, deque


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


JOIN_WAVE = _env_int("PULSE_RAID_JOINS_PER_MINUTE", 10)
NEW_ACCOUNTS = _env_int("PULSE_RAID_NEW_ACCOUNTS_5MIN", 5)
NEW_ACCOUNT_DAYS = _env_int("PULSE_RAID_NEW_ACCOUNT_DAYS", 7)
MASS_MENTIONS = _env_int("PULSE_RAID_MASS_MENTIONS", 8)
COPY_CHANNELS = _env_int("PULSE_RAID_COPY_CHANNELS", 3)
COOLDOWN = 600  # seconds per alert kind
MAX_AUTHORS = 2000

SUGGESTIONS = {
    "join_wave": ("Prüfen, ob die neuen Konten zusammengehören (Namen, Avatare, Beitrittszeit).",
                  "Bei Bedarf in den Servereinstellungen die Verifizierungsstufe vorübergehend erhöhen.",
                  "Einladungslinks prüfen und verdächtige pausieren."),
    "mass_mention": ("Nachricht prüfen und ggf. löschen.",
                     "Verfasser im Blick behalten; Sanktionen nur nach eigener Prüfung."),
    "copy_spam": ("Nachrichten in den genannten Kanälen prüfen und ggf. löschen.",
                  "Konto auf kompromittierten Zugang prüfen (oft gekaperte Accounts mit Links)."),
}


class RaidWatch:
    def __init__(self):
        self.joins: deque = deque(maxlen=500)            # (ts, account_age_days)
        self.recent: OrderedDict = OrderedDict()          # (author_id, text_hash) -> {channel_id: ts}
        self.last_alert: dict[str, float] = {}

    def _alert(self, kind: str, now: float, text: str, details: dict) -> dict | None:
        if now - self.last_alert.get(kind, -COOLDOWN) < COOLDOWN:
            return None
        self.last_alert[kind] = now
        return {"kind": kind, "text": text, "details": details, "suggestions": SUGGESTIONS[kind]}

    def member_joined(self, now: float, account_age_days: float) -> dict | None:
        self.joins.append((now, account_age_days))
        minute = [a for t, a in self.joins if now - t <= 60]
        if len(minute) >= JOIN_WAVE:
            return self._alert("join_wave", now, f"{len(minute)} Beitritte innerhalb einer Minute.",
                               {"joins_60s": len(minute)})
        young = [a for t, a in self.joins if now - t <= 300 and a < NEW_ACCOUNT_DAYS]
        if len(young) >= NEW_ACCOUNTS:
            return self._alert("join_wave", now, f"{len(young)} sehr neue Konten (jünger als {NEW_ACCOUNT_DAYS} Tage) in 5 Minuten.",
                               {"new_accounts_5min": len(young)})
        return None

    def message(self, now: float, author_id: int, channel_id: int, content: str, mentions: int,
                tried_everyone: bool) -> dict | None:
        if mentions >= MASS_MENTIONS or tried_everyone:
            reason = f"{mentions} Erwähnungen in einer Nachricht" if mentions >= MASS_MENTIONS else "Versuch, @everyone/@here zu pingen"
            alert = self._alert("mass_mention", now, reason + ".", {"author_id": author_id, "channel_id": channel_id})
            if alert:
                return alert
        text = " ".join((content or "").lower().split())
        if len(text) < 12:  # short greetings like "hi" are not copy spam
            return None
        key = (int(author_id), hashlib.sha1(text.encode()).hexdigest())
        seen = self.recent.pop(key, {})
        seen = {cid: ts for cid, ts in seen.items() if now - ts <= 60}
        seen[int(channel_id)] = now
        self.recent[key] = seen
        while len(self.recent) > MAX_AUTHORS:
            self.recent.popitem(last=False)
        if len(seen) >= COPY_CHANNELS:
            return self._alert("copy_spam", now, f"Gleiche Nachricht in {len(seen)} Kanälen innerhalb einer Minute.",
                               {"author_id": author_id, "channels": sorted(seen)})
        return None


def alert_text(alert: dict) -> str:
    lines = [f"🚨 **Raid-/Spam-Frühwarnung:** {alert['text']}"]
    d = alert["details"]
    if d.get("author_id"):
        lines.append(f"Verfasser: <@{d['author_id']}>")
    if d.get("channel_id"):
        lines.append(f"Kanal: <#{d['channel_id']}>")
    if d.get("channels"):
        lines.append("Kanäle: " + ", ".join(f"<#{c}>" for c in d["channels"]))
    lines.append("**Vorschläge:**")
    lines.extend(f"• {s}" for s in alert["suggestions"])
    lines.append("_Pulse hat nichts automatisch geändert._")
    return "\n".join(lines)
