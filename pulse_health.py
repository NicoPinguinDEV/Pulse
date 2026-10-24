"""Live health view for the Server Control Center: RAM, DB size, scheduler, Discord rate limits, connections.

Read-only and cheap: no extra loop, no Discord or network calls. Discord 429s are captured from the
``discord.http`` logger that discord.py already writes to (bounded ring buffer, webhook/interaction
tokens redacted). Everything else is read from existing registries (pulse_jobs, pulse_sqlite) and
``os.stat`` on the database files.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pulse_safety import RAM_WARN_MB

RATE_LIMIT_KEEP = 100
RATE_WINDOW_SECONDS = 15 * 60
RATE_WARN_COUNT = 20
WAL_WARN_MB = 64.0
OPEN_CONNECTIONS_WARN = 20
DB_FILES_MAX = 25
RAM_TARGET_MB = RAM_WARN_MB  # Informational target shares the runtime warning threshold

_SEVERITY = {"ok": 0, "info": 1, "warn": 2, "bad": 3}

_TOKEN_PATH = re.compile(r"/(webhooks|interactions)/(\d+)/[^/?#]+")
_SNOWFLAKE = re.compile(r"/\d{15,22}(?=/|$)")


def redact_route(url: str) -> str:
    """Path only, IDs collapsed, webhook/interaction tokens removed."""
    path = re.sub(r"^https?://[^/]+", "", str(url or "")).split("?", 1)[0]
    path = re.sub(r"^/api/v\d+", "", path)
    path = _TOKEN_PATH.sub(lambda m: f"/{m.group(1)}/{{id}}/***", path)
    return _SNOWFLAKE.sub("/{id}", path)[:120] or "/"


class RateLimitRecorder(logging.Handler):
    def __init__(self, keep: int = RATE_LIMIT_KEEP):
        super().__init__(level=logging.WARNING)
        self.events: deque = deque(maxlen=keep)
        self.total = 0
        self.global_total = 0
        self._lock_events = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = str(record.msg)
            args: tuple[Any, ...] = record.args if isinstance(record.args, tuple) else ()
            if msg.startswith("Global rate limit"):
                event = {"at": record.created, "global": True, "method": "", "route": "global",
                         "retry_after": float(args[0]) if args else None, "raised": False}
            elif msg.startswith("We are being rate limited") and len(args) >= 3:
                event = {"at": record.created, "global": False, "method": str(args[0])[:8], "route": redact_route(args[1]),
                         "retry_after": float(args[2]), "raised": "erroring instead" in msg}
            else:
                return
            with self._lock_events:
                self.events.append(event)
                self.total += 1
                if event["global"]:
                    self.global_total += 1
        except Exception:  # a health helper must never break logging
            pass

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock_events:
            events = list(self.events)
            total, global_total = self.total, self.global_total
        recent = [e for e in events if now - e["at"] <= RATE_WINDOW_SECONDS]
        routes: dict[str, int] = {}
        for e in recent:
            if not e["global"]:
                key = f"{e['method']} {e['route']}"
                routes[key] = routes.get(key, 0) + 1
        top = sorted(routes.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        last = events[-1] if events else None
        return {
            "total": total, "global_total": global_total, "window_minutes": RATE_WINDOW_SECONDS // 60,
            "recent": len(recent), "recent_global": sum(1 for e in recent if e["global"]),
            "recent_raised": sum(1 for e in recent if e["raised"]),
            "top_routes": [{"route": r, "count": c} for r, c in top],
            "last": ({**last, "at": _iso(last["at"])} if last else None),
        }


_RECORDER = RateLimitRecorder()
_INSTALL_LOCK = threading.Lock()


def install_rate_limit_recorder() -> RateLimitRecorder:
    """Idempotent. Does not change discord.py's log level unless WARNING would be filtered out."""
    with _INSTALL_LOCK:
        logger = logging.getLogger("discord.http")
        if _RECORDER not in logger.handlers:
            logger.addHandler(_RECORDER)
        if logger.getEffectiveLevel() > logging.WARNING:
            logger.setLevel(logging.WARNING)
    return _RECORDER


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def job_states(jobs: list[dict], now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    out = []
    for j in jobs:
        start, ok, err, paused = (_parse_iso(j.get(k) or "") for k in ("last_start", "last_ok", "last_error_at", "paused_at"))
        interval = j.get("interval")
        if paused and (not start or paused >= start):
            state = "paused"
        elif err and (not ok or err > ok):
            state = "failing"
        elif not j.get("runs") and not start:
            state = "waiting"
        elif interval and start and now - start > max(3 * float(interval), float(interval) + 300):
            state = "overdue"
        else:
            state = "ok"
        out.append({"name": j.get("name"), "state": state, "interval": interval, "runs": j.get("runs", 0),
                    "failures": j.get("failures", 0), "last_start": j.get("last_start") or None,
                    "last_duration": j.get("last_duration"), "last_error": (j.get("last_error") or "")[:200] or None,
                    "age_seconds": round(now - start) if start else None})
    order = {"failing": 0, "overdue": 1, "paused": 2, "waiting": 3, "ok": 4}
    return sorted(out, key=lambda x: (order[x["state"]], str(x["name"])))


def _size_mb(path: Path) -> float:
    try:
        return round(path.stat().st_size / 1024 ** 2, 2)
    except OSError:
        return 0.0


def database_files(dirs) -> list[dict]:
    seen, out = set(), []
    for d in dirs:
        try:
            candidates = sorted(Path(d).glob("*.db"))
        except OSError:
            continue
        for p in candidates:
            key = os.path.normcase(str(p.resolve()))
            if key in seen or not p.is_file():
                continue
            seen.add(key)
            size, wal = _size_mb(p), _size_mb(Path(str(p) + "-wal"))
            out.append({"name": p.name, "size_mb": size, "wal_mb": wal, "total_mb": round(size + wal + _size_mb(Path(str(p) + "-shm")), 2)})
    out.sort(key=lambda x: (-x["total_mb"], x["name"]))
    return out[:DB_FILES_MAX]


def _check(severity: str, component: str, text: str) -> dict:
    return {"severity": severity, "component": component, "text": text}


def evaluate(memory: dict, jobs: list[dict], lag: dict, rates: dict, conns: dict, dbs: list[dict], lag_warn: float) -> list[dict]:
    checks = []
    rss = memory.get("container_mb") or memory.get("process_mb")
    if rss is None:
        checks.append(_check("info", "RAM", "Prozessspeicher nicht messbar."))
    elif rss >= (memory.get("critical_mb") or 1e9):
        checks.append(_check("bad", "RAM", f"{rss:.0f} MB – kritische Grenze erreicht."))
    elif rss >= (memory.get("warn_mb") or 1e9):
        checks.append(_check("warn", "RAM", f"{rss:.0f} MB – Warngrenze erreicht."))
    elif rss > RAM_TARGET_MB:
        checks.append(_check("info", "RAM", f"{rss:.0f} MB – über dem Ziel von {RAM_TARGET_MB:.0f} MB."))
    failing = [j["name"] for j in jobs if j["state"] == "failing"]
    overdue = [j["name"] for j in jobs if j["state"] == "overdue"]
    if failing:
        checks.append(_check("warn", "Scheduler", "Fehlgeschlagen: " + ", ".join(failing[:6])))
    if overdue:
        checks.append(_check("warn", "Scheduler", "Überfällig: " + ", ".join(overdue[:6])))
    if (lag.get("last") or 0) >= lag_warn:
        checks.append(_check("warn", "Event-Loop", f"Letzte Verzögerung {lag['last'] * 1000:.0f} ms."))
    if rates.get("recent_global"):
        checks.append(_check("bad", "Discord", f"Globales Rate-Limit in den letzten {rates['window_minutes']} min."))
    elif rates.get("recent", 0) >= RATE_WARN_COUNT:
        checks.append(_check("warn", "Discord", f"{rates['recent']} Rate-Limits (429) in {rates['window_minutes']} min."))
    if rates.get("ws_ratelimited"):
        checks.append(_check("warn", "Discord", "Gateway-Sendelimit aktiv."))
    if conns.get("open", 0) >= OPEN_CONNECTIONS_WARN:
        checks.append(_check("warn", "Datenbank", f"{conns['open']} gleichzeitig offene Verbindungen."))
    if conns.get("closed_by_gc"):
        checks.append(_check("info", "Datenbank", f"{conns['closed_by_gc']} Verbindung(en) erst vom GC geschlossen (Leck)."))
    big_wal = [d["name"] for d in dbs if d["wal_mb"] >= WAL_WARN_MB]
    if big_wal:
        checks.append(_check("warn", "Datenbank", "Große WAL-Datei: " + ", ".join(big_wal)))
    return sorted(checks, key=lambda c: -_SEVERITY[c["severity"]])


def collect(memory: dict, base_dir, db_path: str, bot=None) -> dict:
    import pulse_jobs
    import pulse_sqlite

    snap = pulse_jobs.snapshot()
    jobs = job_states(snap.get("jobs", []))
    lag = snap.get("lag", {})
    rates = _RECORDER.snapshot()
    try:
        rates["ws_ratelimited"] = bool(bot.is_ws_ratelimited()) if bot is not None else None
    except Exception:
        rates["ws_ratelimited"] = None
    conns = pulse_sqlite.connection_stats()
    dirs = [Path(base_dir), Path(os.path.abspath(db_path)).parent]
    dbs = database_files(dirs)
    checks = evaluate(memory, jobs, lag, rates, conns, dbs, pulse_jobs.LAG_WARN_SECONDS)
    worst = max((_SEVERITY[c["severity"]] for c in checks), default=0)
    status = {0: "ok", 1: "ok", 2: "warn", 3: "bad"}[worst]
    return {
        "status": status, "checks": checks,
        "memory": {**memory, "target_mb": RAM_TARGET_MB, "threads": threading.active_count()},
        "scheduler": {"jobs": jobs, "lag": lag, "counts": {s: sum(1 for j in jobs if j["state"] == s)
                                                            for s in ("ok", "failing", "overdue", "paused", "waiting")}},
        "discord": rates, "connections": conns,
        "databases": {"files": dbs, "total_mb": round(sum(d["total_mb"] for d in dbs), 2)},
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
