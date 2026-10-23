"""Restricted administrator console and in-process live log buffer.

Never executes arbitrary shell commands. Exposed operations are explicit allow-listed
application diagnostics/actions, and all web routes must perform admin authorization.
The structured ring buffer (`records_since`) feeds the Server Control Center live stream; it is
bounded (500 records) for the RAM budget.
"""
from __future__ import annotations

import itertools
import logging
import threading
from collections import deque
from datetime import datetime, timezone


MAX_RECORDS = 500
_LINES: deque[str] = deque(maxlen=MAX_RECORDS)
_RECORDS: deque[dict] = deque(maxlen=MAX_RECORDS)
_SEQ = itertools.count(1)
_LOCK = threading.Lock()


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            moment = datetime.fromtimestamp(record.created, timezone.utc).astimezone()
            message = record.getMessage()[:2000]
            line = f"{moment.strftime('%H:%M:%S')} {record.levelname:<8} {record.name}: {message}"
            with _LOCK:
                _LINES.append(line[:2000])
                _RECORDS.append({"seq": next(_SEQ), "ts": record.created, "time": moment.isoformat(timespec="seconds"),
                                 "level": record.levelname, "logger": record.name[:80], "text": message})
        except Exception:
            self.handleError(record)


def _ensure_handler() -> None:
    # The "pulse" logger does not propagate (pulse_logging) – attach there too, otherwise its errors are missing.
    for logger in (logging.getLogger(), logging.getLogger("pulse")):
        if not any(isinstance(handler, _RingHandler) for handler in logger.handlers):
            handler = _RingHandler()
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)


_ensure_handler()


def recent_lines(limit: int = 160) -> list[str]:
    _ensure_handler()
    try:
        limit = max(1, min(int(limit), MAX_RECORDS))
    except (TypeError, ValueError):
        limit = 160
    with _LOCK:
        return list(_LINES)[-limit:]


def records_since(seq: int = 0, limit: int = 200) -> list[dict]:
    """Structured records newer than `seq` (oldest first, at most `limit`)."""
    _ensure_handler()
    try:
        seq, limit = max(0, int(seq)), max(1, min(int(limit), MAX_RECORDS))
    except (TypeError, ValueError):
        seq, limit = 0, 200
    with _LOCK:
        return [dict(r) for r in _RECORDS if r["seq"] > seq][-limit:]


async def run_command(command: str, app) -> dict:
    """Run one known command; input is never interpreted as shell or Python."""
    allowed = {"help", "status", "health", "version", "logs", "sync"}
    if not command or len(command) > 40 or command not in allowed:
        return {
            "ok": False,
            "output": "Unbekannter Befehl. Erlaubt: help, status, health, version, logs, sync.",
        }

    if command == "help":
        return {
            "ok": True,
            "output": (
                "Verfügbare Befehle:\n"
                "  help    – Befehlsübersicht\n"
                "  status  – Bot-/Discord-Verbindungsstatus\n"
                "  health  – Datenbank- und Webserverstatus\n"
                "  version – installierte Pulse-Version\n"
                "  logs    – letzte Prozessmeldungen\n"
                "  sync    – Discord-Slash-Befehle synchronisieren (mit Rate-Limit)\n\n"
                "Freie Shell-Kommandos sind absichtlich deaktiviert."
            ),
        }

    if command == "logs":
        return {"ok": True, "output": "\n".join(recent_lines(80)) or "Noch keine Prozessmeldungen erfasst."}

    if command == "version":
        from pulse_version import PULSE_VERSION
        return {"ok": True, "output": f"Pulse-Version: {PULSE_VERSION}"}

    if command == "status":
        bot = getattr(app.state, "bot", None)
        if bot is None:
            return {"ok": True, "output": "Bot-Objekt: nicht verfügbar"}
        guild_id = getattr(__import__("webserver"), "GUILD_ID", None)
        guild = bot.get_guild(guild_id) if guild_id else None
        return {
            "ok": True,
            "output": (
                f"Bot: {'online' if getattr(bot, 'is_ready', lambda: False)() else 'startet/nicht bereit'}\n"
                f"Discord-Name: {getattr(bot.user, 'name', '–') if bot.user else '–'}\n"
                f"Guild verbunden: {'ja' if guild else 'nein'}\n"
                f"Mitglieder im Cache: {getattr(guild, 'member_count', '–') if guild else '–'}"
            ),
        }

    if command == "health":
        import pulse_db
        from pulse_version import PULSE_VERSION
        bot = getattr(app.state, "bot", None)
        ready = bool(bot and getattr(bot, "is_ready", lambda: False)())
        guild_id = getattr(__import__("webserver"), "GUILD_ID", None)
        guild_ready = bool(ready and guild_id and bot.get_guild(guild_id))
        try:
            pulse_db.get_setting("__pulse_console_health_probe__", None)
            database_ok = True
        except Exception:
            database_ok = False
        return {
            "ok": database_ok,
            "output": (
                f"Version: {PULSE_VERSION}\n"
                f"Bot bereit: {'ja' if ready else 'nein'}\n"
                f"Discord-Server bereit: {'ja' if guild_ready else 'nein'}\n"
                f"Datenbank erreichbar: {'ja' if database_ok else 'nein'}"
            ),
        }

    if command == "sync":
        bot = getattr(app.state, "bot", None)
        if bot is None or not getattr(bot, "is_ready", lambda: False)():
            return {"ok": False, "output": "Bot ist nicht bereit. Sync wurde nicht ausgeführt."}
        from pulse_command_sync import sync_commands
        try:
            result = await sync_commands(bot.tree, force=True)
        except Exception as exc:
            logging.getLogger(__name__).exception("Live-Konsole: Slash-Command-Sync fehlgeschlagen")
            return {"ok": False, "output": f"Sync fehlgeschlagen: {type(exc).__name__}"}
        return {"ok": True, "output": f"{result.get('count', 0)} Slash-Befehle synchronisiert."}

    return {"ok": False, "output": "Befehl nicht verfügbar."}
