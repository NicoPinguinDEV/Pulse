"""BOT-08: early warning for raids and spam – detection only, never an automatic change.

Signals (all windows and maps are bounded; the container RAM budget is defined in compose.yaml):
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


JOIN_WAVE = max(2, _env_int("PULSE_RAID_JOINS_PER_MINUTE", 10))  # legacy/elevated threshold
NEW_ACCOUNTS = max(2, _env_int("PULSE_RAID_NEW_ACCOUNTS_5MIN", 5))  # legacy/elevated threshold
NEW_ACCOUNT_DAYS = _env_int("PULSE_RAID_NEW_ACCOUNT_DAYS", 7)
MASS_MENTIONS = max(2, _env_int("PULSE_RAID_MASS_MENTIONS", 8))  # legacy/elevated threshold
COPY_CHANNELS = max(2, _env_int("PULSE_RAID_COPY_CHANNELS", 3))  # legacy/elevated threshold

LEVEL_ORDER = ("warning", "elevated", "critical")
LEVEL_LABELS = {
    "warning": "WARNUNG",
    "elevated": "ERHÖHT",
    "critical": "KRITISCH",
}


def _thresholds(prefix: str, elevated: int, warning_default: int, critical_default: int) -> dict[str, int]:
    """Build strictly ordered warning/elevated/critical thresholds from bounded environment values."""
    warning = _env_int(f"{prefix}_WARNING", warning_default)
    critical = _env_int(f"{prefix}_CRITICAL", critical_default)
    warning = min(warning, max(1, elevated - 1))
    critical = max(critical, elevated + 1)
    return {"warning": warning, "elevated": elevated, "critical": critical}


def _level_for_count(value: int, thresholds: dict[str, int]) -> str | None:
    level = None
    for candidate in LEVEL_ORDER:
        if value >= thresholds[candidate]:
            level = candidate
    return level


JOIN_LEVELS = _thresholds("PULSE_RAID_JOINS", JOIN_WAVE, max(1, JOIN_WAVE // 2), JOIN_WAVE * 2)
NEW_ACCOUNT_LEVELS = _thresholds("PULSE_RAID_NEW_ACCOUNTS", NEW_ACCOUNTS, max(1, NEW_ACCOUNTS // 2), NEW_ACCOUNTS * 2)
MENTION_LEVELS = _thresholds("PULSE_RAID_MASS_MENTIONS", MASS_MENTIONS, max(1, MASS_MENTIONS // 2), MASS_MENTIONS * 2)
COPY_CHANNEL_LEVELS = _thresholds("PULSE_RAID_COPY_CHANNELS", COPY_CHANNELS, max(1, COPY_CHANNELS - 1), COPY_CHANNELS + 2)
COOLDOWN = 600  # seconds per alert kind and severity level
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

    def _alert(self, kind: str, now: float, text: str, details: dict, level: str = "elevated") -> dict | None:
        if level not in LEVEL_ORDER:
            level = "elevated"
        cooldown_key = f"{kind}:{level}"
        if now - self.last_alert.get(cooldown_key, -COOLDOWN) < COOLDOWN:
            return None
        self.last_alert[cooldown_key] = now
        return {
            "kind": kind,
            "level": level,
            "level_label": LEVEL_LABELS[level],
            "text": text,
            "details": details,
            "suggestions": SUGGESTIONS[kind],
        }

    def member_joined(self, now: float, account_age_days: float) -> dict | None:
        self.joins.append((now, account_age_days))
        minute = [age for timestamp, age in self.joins if now - timestamp <= 60]
        young = [age for timestamp, age in self.joins if now - timestamp <= 300 and age < NEW_ACCOUNT_DAYS]
        candidates = []
        join_level = _level_for_count(len(minute), JOIN_LEVELS)
        if join_level:
            candidates.append((
                LEVEL_ORDER.index(join_level),
                f"{len(minute)} Beitritte innerhalb einer Minute.",
                {"joins_60s": len(minute)},
                join_level,
            ))
        young_level = _level_for_count(len(young), NEW_ACCOUNT_LEVELS)
        if young_level:
            candidates.append((
                LEVEL_ORDER.index(young_level),
                f"{len(young)} sehr neue Konten (jünger als {NEW_ACCOUNT_DAYS} Tage) in 5 Minuten.",
                {"new_accounts_5min": len(young)},
                young_level,
            ))
        if not candidates:
            return None
        _rank, text, details, level = max(candidates, key=lambda item: item[0])
        return self._alert("join_wave", now, text, details, level)

    def message(self, now: float, author_id: int, channel_id: int, content: str, mentions: int,
                tried_everyone: bool) -> dict | None:
        mention_level = _level_for_count(mentions, MENTION_LEVELS)
        if mention_level or tried_everyone:
            level = mention_level or "elevated"
            reason = f"{mentions} Erwähnungen in einer Nachricht" if mention_level else "Versuch, @everyone/@here zu pingen"
            alert = self._alert("mass_mention", now, reason + ".", {"author_id": author_id, "channel_id": channel_id}, level)
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
        copy_level = _level_for_count(len(seen), COPY_CHANNEL_LEVELS)
        if copy_level:
            return self._alert("copy_spam", now, f"Gleiche Nachricht in {len(seen)} Kanälen innerhalb einer Minute.",
                               {"author_id": author_id, "channels": sorted(seen)}, copy_level)
        return None


def alert_text(alert: dict) -> str:
    label = LEVEL_LABELS.get(alert.get("level"), "ERHÖHT")
    lines = [f"🚨 **Raid-/Spam-Frühwarnung · {label}:** {alert['text']}"]
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
