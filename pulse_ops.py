"""Audit #155, #157, #158, #165, #172: operations analytics - error groups, endpoint latency, resource history,
admin action history and the retention matrix.

Everything is bounded (RAM budget of the 2 GiB VPS): error groups keep the newest ``MAX_GROUPS``, resource samples
are thinned to ``KEEP_DAYS`` days, latency keeps ``SAMPLES`` timings for at most ``MAX_ROUTES`` routes in memory.
Nothing here changes behavior or data of other modules; every reader tolerates a missing source.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import pulse_db

MAX_GROUPS = 200
KEEP_DAYS = 14
SAMPLE_EVERY_SECONDS = 300
SAMPLES = 200
MAX_ROUTES = 300
_UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(_UTC)


# ------------------------------------------------------------------------------------------ #155 error groups
_HEX = re.compile(r"\b[0-9a-f]{8,}\b", re.I)
_NUM = re.compile(r"\d+")
_QUOTED = re.compile(r"(['\"]).{0,80}?\1")
_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][\w .\-\\/]{3,}")
_SPACE = re.compile(r"\s+")


def signature(source: str, message: str) -> str:
    """Stable group key: the same error with different IDs, numbers, quoted values or paths is one group."""
    text = str(message or "")
    for pattern, repl in ((_PATH, "<pfad>"), (_QUOTED, "<wert>"), (_HEX, "<id>"), (_NUM, "<n>")):
        text = pattern.sub(repl, text)
    return f"{str(source or 'pulse')[:60]}|{_SPACE.sub(' ', text).strip().lower()[:120]}"


def record_error(source: str, message: str, when: datetime | None = None) -> None:
    """Count one error occurrence in its group (upsert, bounded). Never raises."""
    try:
        stamp = (when or _now()).isoformat(timespec="seconds")
        sig = signature(source, message)
        with pulse_db.connect() as db:
            db.execute("INSERT INTO ops_error_groups (sig,source,sample,first_seen,last_seen,count) VALUES (?,?,?,?,?,1) "
                       "ON CONFLICT(sig) DO UPDATE SET last_seen=excluded.last_seen, count=count+1, sample=excluded.sample",
                       (sig, str(source)[:60], str(message)[:300], stamp, stamp))
            db.execute("DELETE FROM ops_error_groups WHERE sig NOT IN (SELECT sig FROM ops_error_groups ORDER BY last_seen DESC LIMIT ?)", (MAX_GROUPS,))
    except Exception:  # noqa: BLE001 - error bookkeeping must never create errors
        pass


def error_groups(limit: int = 50, now: datetime | None = None) -> list[dict]:
    """Groups newest-activity first. ``new`` = first seen within 24 h, ``recent`` = seen within the last hour."""
    now = now or _now()
    with pulse_db.connect() as db:
        rows = db.execute("SELECT * FROM ops_error_groups ORDER BY last_seen DESC LIMIT ?", (max(1, min(int(limit), MAX_GROUPS)),)).fetchall()
    out = []
    for row in rows:
        first = datetime.fromisoformat(row["first_seen"])
        last = datetime.fromisoformat(row["last_seen"])
        out.append({**dict(row), "new": now - first < timedelta(hours=24), "recent": now - last < timedelta(hours=1)})
    return out


class ErrorGroupHandler(logging.Handler):
    """Logging handler that feeds every ERROR record of the ``pulse`` loggers into the error groups."""

    _busy = threading.local()

    def __init__(self):
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._busy, "on", False):
            return
        self._busy.on = True
        try:
            source = record.name.split(".", 1)[1] if "." in record.name else record.name
            record_error(source, record.getMessage())
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._busy.on = False


def install_error_handler(logger_name: str = "pulse") -> ErrorGroupHandler:
    """Idempotent: one handler per logger."""
    logger = logging.getLogger(logger_name)
    for handler in logger.handlers:
        if isinstance(handler, ErrorGroupHandler):
            return handler
    handler = ErrorGroupHandler()
    logger.addHandler(handler)
    return handler


# --------------------------------------------------------------------------------------------- #157 latency
class LatencyRecorder:
    """Per route template: request count, error count and the last ``SAMPLES`` timings (milliseconds)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._routes: dict[str, dict] = {}

    def record(self, route: str, ms: float, status: int) -> None:
        route = str(route or "(unbekannt)")[:120]
        with self._lock:
            row = self._routes.get(route)
            if row is None:
                if len(self._routes) >= MAX_ROUTES:
                    return
                row = self._routes[route] = {"count": 0, "errors": 0, "ms": deque(maxlen=SAMPLES)}
            row["count"] += 1
            row["errors"] += 1 if int(status) >= 500 else 0
            row["ms"].append(float(ms))

    def snapshot(self) -> list[dict]:
        with self._lock:
            items = [(route, row["count"], row["errors"], sorted(row["ms"])) for route, row in self._routes.items()]
        out = []
        for route, count, errors, ms in items:
            if not ms:
                continue
            out.append({"route": route, "count": count, "errors": errors, "p50": round(ms[len(ms) // 2], 1),
                        "p95": round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1), "max": round(ms[-1], 1)})
        return sorted(out, key=lambda r: -r["p95"])

    def reset(self) -> None:
        with self._lock:
            self._routes.clear()


LATENCY = LatencyRecorder()


# -------------------------------------------------------------------------------------- #158 resource history
_cpu_last: tuple[float, float] | None = None


def _container_mb():
    try:
        from pulse_ultimate import container_memory_mb
        return container_memory_mb()
    except Exception:  # noqa: BLE001
        return None


def _process_mb():
    try:
        from pulse_ultimate import process_rss_mb
        return process_rss_mb()
    except Exception:  # noqa: BLE001
        return None


def sample_resources(data_dir=".", now: datetime | None = None, force: bool = False) -> dict | None:
    """Store one sample (container/process memory, process CPU share, free disk, threads) at most every five minutes.
    Returns the stored row or ``None`` (too early, or storage problem). Never raises."""
    global _cpu_last
    try:
        now = now or _now()
        with pulse_db.connect() as db:
            last = db.execute("SELECT at FROM ops_resource_samples ORDER BY at DESC LIMIT 1").fetchone()
        if last and not force and (now - datetime.fromisoformat(last["at"])).total_seconds() < SAMPLE_EVERY_SECONDS:
            return None
        wall, cpu = time.monotonic(), time.process_time()
        cpu_pct = None
        if _cpu_last and wall > _cpu_last[0]:
            cpu_pct = round(max(0.0, (cpu - _cpu_last[1]) / (wall - _cpu_last[0]) * 100), 1)
        _cpu_last = (wall, cpu)
        try:
            free = round(shutil.disk_usage(str(data_dir)).free / (1024 * 1024), 0)
        except OSError:
            free = None
        row = {"at": now.isoformat(timespec="seconds"), "container_mb": _container_mb(), "process_mb": _process_mb(), "cpu_pct": cpu_pct,
               "disk_free_mb": free, "threads": threading.active_count()}
        with pulse_db.connect() as db:
            db.execute("INSERT OR REPLACE INTO ops_resource_samples (at,container_mb,process_mb,cpu_pct,disk_free_mb,threads) "
                       "VALUES (:at,:container_mb,:process_mb,:cpu_pct,:disk_free_mb,:threads)", row)
            db.execute("DELETE FROM ops_resource_samples WHERE at < ?", ((now - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds"),))
        return row
    except Exception:  # noqa: BLE001
        return None


def resource_series(hours: int = 24, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    since = (now - timedelta(hours=max(1, min(int(hours), KEEP_DAYS * 24)))).isoformat(timespec="seconds")
    with pulse_db.connect() as db:
        rows = db.execute("SELECT * FROM ops_resource_samples WHERE at >= ? ORDER BY at", (since,)).fetchall()
    return [dict(r) for r in rows]


def resource_summary(series: list[dict]) -> dict:
    """min/avg/max per metric over the series and a simple trend (second half vs first half average)."""
    out = {}
    for key in ("container_mb", "process_mb", "cpu_pct", "disk_free_mb"):
        values = [r[key] for r in series if r.get(key) is not None]
        if not values:
            out[key] = None
            continue
        half = len(values) // 2
        first, second = values[:half] or values, values[half:] or values
        delta = sum(second) / len(second) - sum(first) / len(first)
        tolerance = max(abs(sum(first) / len(first)) * 0.05, 1.0)
        out[key] = {"min": min(values), "avg": round(sum(values) / len(values), 1), "max": max(values), "last": values[-1], "n": len(values),
                    "trend": "steigend" if delta > tolerance else "fallend" if delta < -tolerance else "stabil"}
    return out


# ------------------------------------------------------------------------------------- #165 Admin-Aktionshistorie
def admin_actions(ws, limit: int = 200, query: str = "", source: str = "") -> list[dict]:
    """Dashboard audit log and settings history in one chronological list (newest first). Read-only; a failing source
    is skipped. ``source`` = ``audit`` | ``einstellung`` | empty (both)."""
    q = str(query or "").strip().casefold()[:100]
    rows = []
    if source in ("", "audit"):
        try:
            entries = ws.load_json(ws.AUDIT_FILE, [])
            for e in (entries if isinstance(entries, list) else [])[-1000:]:
                if isinstance(e, dict):
                    rows.append({"source": "audit", "at": _norm_time(e.get("timestamp")), "actor": str(e.get("actor") or ""),
                                 "action": str(e.get("action") or ""), "detail": str(e.get("details") or "")[:300]})
        except Exception:  # noqa: BLE001
            pass
    if source in ("", "einstellung"):
        try:
            import pulse_settings_history as sh
            for e in sh.entries(300):
                rows.append({"source": "einstellung", "at": _norm_time(e.get("created_at") or e.get("at")), "actor": str(e.get("actor_name") or e.get("actor") or ""),
                             "action": f"Einstellung geändert: {e.get('key', '')}", "detail": str(e.get("source") or "")})
        except Exception:  # noqa: BLE001
            pass
    if q:
        rows = [r for r in rows if q in f"{r['actor']} {r['action']} {r['detail']}".casefold()]
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:max(1, min(int(limit), 500))]


def _norm_time(value) -> str:
    """Sortable ``YYYY-MM-DD HH:MM:SS`` from German (``dd.mm.YYYY HH:MM:SS``) or ISO timestamps; unknown -> empty."""
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


# --------------------------------------------------------------------------------------- #172 Aufbewahrungsmatrix
def retention_matrix(ws) -> list[dict]:
    """Every data kind with its store, the rule that applies, whether it is enforced automatically, how many rows
    exist and how old the oldest is. Counts are read-only; an unreadable store shows ``None`` instead of failing."""
    import pulse_retention
    try:
        settings = pulse_retention.load_settings()
    except Exception:  # noqa: BLE001 - the matrix still shows the stores with the documented defaults
        settings = dict(pulse_retention.DEFAULTS)
    locked = not getattr(pulse_retention, "RETENTION_MUTATIONS_ENABLED", False)

    def count_table(table, col="created_at"):
        def run():
            with pulse_db.connect() as db:
                n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 - fixed table names below
                old = db.execute(f"SELECT MIN({col}) FROM {table}").fetchone()[0]  # noqa: S608
            return n, str(old or "")[:10]
        return run

    def count_json(path_attr, key=None, stamp="created_at"):
        def run():
            data = ws.load_json(getattr(ws, path_attr), {} if key is None else [])
            items = list(data.values()) if isinstance(data, dict) else list(data)
            stamps = [str(i.get(stamp) or "")[:10] for i in items if isinstance(i, dict) and i.get(stamp)]
            return len(items), (min(stamps) if stamps else "")
        return run

    def count_shift_history():
        data = ws.load_json(ws.SHIFTS_FILE, {})
        items = data.get("history", []) if isinstance(data, dict) else []
        stamps = [str(i.get("started_at_iso") or "")[:10] for i in items if isinstance(i, dict) and i.get("started_at_iso")]
        return len(items), (min(stamps) if stamps else "")

    def count_transcripts():
        folder = getattr(ws, "TRANSCRIPT_DIR", None) or os.path.join(os.path.dirname(os.path.abspath(ws.__file__)), "transcripts")
        files = [f for f in os.scandir(folder) if f.is_file()] if os.path.isdir(folder) else []
        oldest = min((f.stat().st_mtime for f in files), default=None)
        return len(files), (datetime.fromtimestamp(oldest, _UTC).strftime("%Y-%m-%d") if oldest else "")

    spec = [
        ("applications", "Bewerbungen", "applications.json", f"Abgelehnte nach {settings['applications_days']} Tagen", not locked, count_json("APPS_FILE")),
        ("audit", "Dashboard-Audit", "audit_logs.json", f"{settings['audit_days']} Tage, höchstens 5000 Einträge", not locked, count_json("AUDIT_FILE", key="list", stamp="timestamp")),
        ("tickets", "Tickets", "pulse.db · tickets", "keine automatische Löschung", False, count_table("tickets", "opened_at")),
        ("notifications", "Inbox-Meldungen", "pulse.db · notifications", "keine automatische Löschung", False, count_table("notifications")),
        ("tasks", "Aufgaben", "pulse.db · tasks", "keine automatische Löschung", False, count_table("tasks")),
        ("mod_cases", "Moderationsfälle", "pulse.db · mod_cases", "keine automatische Löschung (Rechtsgrundlage klären)", False, count_table("mod_cases")),
        ("wiki_versions", "Wiki-Versionen", "pulse.db · wiki_page_versions", "unveränderliche Historie", False, count_table("wiki_page_versions")),
        ("error_groups", "Fehlergruppen", "pulse.db · ops_error_groups", f"höchstens {MAX_GROUPS} Gruppen, älteste fallen weg", True, count_table("ops_error_groups", "first_seen")),
        ("resources", "Ressourcenverlauf", "pulse.db · ops_resource_samples", f"{KEEP_DAYS} Tage", True, count_table("ops_resource_samples", "at")),
        ("events", "Ereignisse (Audit-Vertrag)", "pulse.db · events", "keine automatische Löschung", False, count_table("events")),
        ("shifts", "Schichthistorie", "shifts.json · history", "keine automatische Löschung", False, count_shift_history),
        ("transcripts", "Ticket-Transkripte", "transcripts/ (Dateien)", "keine automatische Löschung", False, count_transcripts),
        ("team_data", "Teamakten", "team_data.json", "keine automatische Löschung (Personalakte, Rechtsgrundlage klären)", False, count_json("DATA_FILE")),
    ]
    out = []
    for key, label, store, rule, auto, loader in spec:
        try:
            count, oldest = loader()
            error = ""
        except Exception:  # noqa: BLE001
            count, oldest, error = None, "", "nicht lesbar"
        out.append({"key": key, "label": label, "store": store, "rule": rule, "automatic": bool(auto), "count": count, "oldest": oldest, "error": error})
    return out


def cockpit_extras(base_dir=".", db_path="pulse.db") -> dict:
    """#153: background jobs and databases for the cockpit, from the existing scheduler/DB diagnostics (read-only).
    Never raises; a missing source yields empty values."""
    from pathlib import Path
    jobs, dbs = [], []
    try:
        import pulse_health
        import pulse_jobs
        jobs = pulse_health.job_states(pulse_jobs.snapshot().get("jobs", []))
        dbs = pulse_health.database_files([Path(base_dir), Path(os.path.abspath(db_path)).parent])
    except Exception:  # noqa: BLE001
        logging.getLogger("pulse.ops").debug("Cockpit-Zusatzdaten nicht verfügbar", exc_info=True)
    counts = {s: sum(1 for j in jobs if j["state"] == s) for s in ("ok", "failing", "overdue", "paused", "waiting")}
    return {"jobs": jobs, "job_counts": counts, "problem_jobs": [j for j in jobs if j["state"] in ("failing", "overdue")][:10],
            "databases": dbs, "database_total_mb": round(sum(d["total_mb"] for d in dbs), 2)}


def release_checks(*, bot_ready: bool, guild_ready: bool, extras: dict, memory_mb=None, groups=None, sha: str = "", memory_warn_mb: float = 500.0) -> list[dict]:
    """#169: Prüfergebnisse des *laufenden* Releases (keine CI-Anbindung): ``[{name, ok, detail}]``. Reine Auswertung vorhandener Werte;
    ein fehlender Wert gilt als „nicht prüfbar“ (``ok=None``), nie als bestanden."""
    def check(name, ok, detail):
        return {"name": name, "ok": ok, "detail": detail}
    out = [check("Discord-Bot verbunden", bool(bot_ready), "bereit" if bot_ready else "noch nicht bereit"),
           check("Server (Guild) geladen", bool(guild_ready), "ja" if guild_ready else "nicht im Cache")]
    try:
        with pulse_db.connect() as db:
            db.execute("SELECT 1")
        out.append(check("Datenbank antwortet", True, "pulse.db"))
    except Exception:  # noqa: BLE001
        out.append(check("Datenbank antwortet", False, "Abfrage fehlgeschlagen"))
    failing = int((extras or {}).get("job_counts", {}).get("failing", 0))
    overdue = int((extras or {}).get("job_counts", {}).get("overdue", 0))
    jobs_known = bool((extras or {}).get("jobs"))
    out.append(check("Hintergrundjobs ohne Fehler", (failing == 0 and overdue == 0) if jobs_known else None,
                     f"{failing} fehlerhaft · {overdue} überfällig" if jobs_known else "noch keine Jobdaten"))
    out.append(check("Arbeitsspeicher unter der Warnschwelle", None if not memory_mb else memory_mb < memory_warn_mb,
                     "nicht messbar" if not memory_mb else f"{memory_mb:g} MB (Warnung ab {memory_warn_mb:g} MB)"))
    recent = [g for g in (groups or []) if g.get("recent")]
    out.append(check("Keine neuen Fehler in der letzten Stunde", not recent, f"{len(recent)} Fehlergruppe(n)" if recent else "keine"))
    out.append(check("Commit bekannt", bool(sha) or None, sha[:8] if sha else "unbekannt"))
    return out


RELEASE_KEY = "ops_release_history"
RELEASE_KEEP = 20


def record_release(version, sha, now: datetime | None = None) -> bool:
    """#169: remember which version/commit ran, newest last (idempotent per version+commit). Returns True if a new
    entry was added. The entry before the current one is the rollback reference shown in the release view."""
    version, sha = str(version or "")[:40], str(sha or "")[:40]
    if not version and not sha:
        return False
    history = pulse_db.get_setting(RELEASE_KEY, [])
    history = history if isinstance(history, list) else []
    if history and isinstance(history[-1], dict) and history[-1].get("version") == version and history[-1].get("sha") == sha:
        return False
    history.append({"version": version, "sha": sha, "first_seen": (now or _now()).isoformat(timespec="seconds")})
    pulse_db.set_setting(RELEASE_KEY, history[-RELEASE_KEEP:])
    return True


def release_history() -> list[dict]:
    """Newest first. ``history[0]`` is the running release, ``history[1]`` the one before (rollback reference)."""
    history = pulse_db.get_setting(RELEASE_KEY, [])
    items = [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
    return list(reversed(items))


def data_dir() -> str:
    return os.environ.get("PULSE_DATA_DIR", ".")
