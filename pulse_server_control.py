"""Server Control Center (Verwaltung → Serververwaltung): Übersicht, Live-Konsole, Container, Deployment,
Backups und Diagnose in einer Seite unter /admin/server.

Architektur
* Der Webcontainer bekommt **keinen** Docker-Socket und keine Shell. Alles, was den Host betrifft
  (Container-Logs/-Status, Git, Deploy, Neustart, Rollback), fragt dieses Modul beim kleinen Host-
  Verwaltungsdienst `deploy/pulse_host_agent.py` an (Token-Auth, feste Aktionsliste, nur privat erreichbar).
  Ist er nicht eingerichtet, zeigt die Seite die betroffenen Werte ausdrücklich als „nicht verfügbar“ –
  eine Aktion wird dann nie als erfolgreich gemeldet.
* Werte aus dem eigenen Prozess (Bot, Datenbank, RAM per cgroup, Speicherplatz, Backups, Fehlerpuffer)
  sind immer echt und brauchen den Dienst nicht.
* Rechte: neue Berechtigung `can_manage_server` im Deny-by-default-Modell (pulse_permissions); jede
  Route prüft sie serverseitig. Verändernde Routen akzeptieren nur JSON (kein Formular-CSRF) zusätzlich
  zur Same-Origin-Prüfung der Middleware, haben Rate-Limits und schreiben ins Audit-Log.
* RAM: alle Puffer begrenzt, Abfragen nur auf Anfrage bzw. im SSE-Stream alle paar Sekunden, Streams
  pro Person und global begrenzt und zeitlich befristet.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

PERMISSION = "can_manage_server"
PAGE_KEY = "server-control"
CONFIRM_WORDS = {"deploy": "DEPLOY", "restart": "NEUSTART", "rollback": "ROLLBACK"}
AGENT_ACTIONS = ("deploy", "restart", "rollback", "healthcheck")
LOCAL_ACTIONS = ("backup",)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
BACKUP_NAME_RE = re.compile(r"^pulse_full_[a-z_]*\d{8}_\d{6}(?:_\d{6})?\.zip$")
COMMAND_RE = re.compile(r"^([a-z]{2,12})(?: (\d{1,3}))?$")

STREAM_POLL_SECONDS = 2.0
STREAM_HEARTBEAT_SECONDS = 15.0
STREAM_MAX_SECONDS = 15 * 60  # the browser reconnects automatically afterwards
STREAM_MAX_TOTAL = 6
STREAM_MAX_PER_USER = 2
STREAM_BATCH = 200
GITHUB_CACHE_SECONDS = 300
_PROCESS_STARTED = time.time()

_SECRET_PATTERNS = (
    (re.compile(r"[MNO][A-Za-z\d_-]{23,28}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,}"), "[DISCORD-TOKEN ENTFERNT]"),
    (re.compile(r"(?i)\b(bearer|bot)\s+[A-Za-z0-9._~+/=-]{20,}"), r"\1 [ENTFERNT]"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|WEBHOOK)[A-Z0-9_]*)\s*([=:])\s*\S+"),
     r"\1\2[ENTFERNT]"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[ZUGANG ENTFERNT]@"),
    (re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\S+"), "[WEBHOOK ENTFERNT]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[PRIVATER SCHLÜSSEL ENTFERNT]"),
)


def redact(text, limit: int = 2000) -> str:
    """Second line of defence (the host agent redacts too): nothing token-like leaves the dashboard."""
    out = str(text if text is not None else "")
    for secret in (os.getenv("PULSE_HOST_AGENT_TOKEN", ""), os.getenv("DISCORD_TOKEN", ""), os.getenv("DISCORD_CLIENT_SECRET", "")):
        if secret and len(secret) >= 8:
            out = out.replace(secret, "[ENTFERNT]")
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out[:limit]


def classify(text: str, level: str = "") -> str:
    """error / warning / info for the console filter."""
    level = (level or "").upper()
    if level in ("ERROR", "CRITICAL"):
        return "error"
    if level == "WARNING":
        return "warning"
    lowered = str(text).lower()
    if any(m in lowered for m in ("traceback", "error", "exception", "fehler", "fehlgeschlagen", "kritisch", "❌", "🔴", "critical")):
        return "error"
    if any(m in lowered for m in ("warn", "⚠", "timeout", "429", "verzögert", "nicht erreichbar")):
        return "warning"
    return "info"


# --------------------------------------------------------------------------- host agent client
class AgentUnavailable(Exception):
    def __init__(self, reason: str, message: str, status: int = 503):
        super().__init__(message)
        self.reason, self.message, self.status = reason, message, status


def agent_config() -> dict:
    url = os.getenv("PULSE_HOST_AGENT_URL", "").strip().rstrip("/")
    token = os.getenv("PULSE_HOST_AGENT_TOKEN", "").strip()
    if not url or not token:
        return {"configured": False, "url": "", "token": "", "problem": "PULSE_HOST_AGENT_URL/PULSE_HOST_AGENT_TOKEN nicht gesetzt"}
    parsed = urlparse(url)
    problem = ""
    if parsed.scheme == "unix":
        # Preferred: unix:///app/.pulse-agent/agent.sock – the socket lives in the bind-mounted folder.
        path = parsed.path
        if not path.startswith("/") or ".." in Path(path).parts or not path.endswith(".sock"):
            problem = "PULSE_HOST_AGENT_URL unix:// braucht einen absoluten Pfad auf eine .sock-Datei"
        elif len(token) < 32:
            problem = "PULSE_HOST_AGENT_TOKEN ist kürzer als 32 Zeichen"
        if problem:
            return {"configured": False, "url": "", "token": "", "socket": "", "problem": problem}
        return {"configured": True, "url": "http://pulse-host-agent", "socket": path, "token": token, "problem": ""}
    try:
        host_ip = ipaddress.ip_address(parsed.hostname or "")
        if not (host_ip.is_private or host_ip.is_loopback) or host_ip.is_unspecified:
            problem = "PULSE_HOST_AGENT_URL muss auf eine private Adresse zeigen"
    except ValueError:
        problem = "PULSE_HOST_AGENT_URL muss eine IP-Adresse enthalten"
    if parsed.scheme != "http" or parsed.path not in ("", "/"):
        problem = problem or "PULSE_HOST_AGENT_URL muss die Form http://<private-ip>:<port> haben"
    if len(token) < 32:
        problem = problem or "PULSE_HOST_AGENT_TOKEN ist kürzer als 32 Zeichen"
    if problem:
        return {"configured": False, "url": "", "token": "", "socket": "", "problem": problem}
    return {"configured": True, "url": url, "token": token, "socket": "", "problem": ""}


_http_clients: dict = {}


def _client(socket_path: str = ""):
    """One small shared pool per target (GitHub/TCP agent, Unix-socket agent); closed on shutdown."""
    import httpx
    client = _http_clients.get(socket_path)
    if client is None or client.is_closed:
        kwargs = {"limits": httpx.Limits(max_connections=4, max_keepalive_connections=2), "follow_redirects": False}
        if socket_path:
            kwargs["transport"] = httpx.AsyncHTTPTransport(uds=socket_path)
        client = _http_clients[socket_path] = httpx.AsyncClient(**kwargs)
    return client


async def aclose() -> None:
    for client in list(_http_clients.values()):
        if not client.is_closed:
            await client.aclose()
    _http_clients.clear()


async def agent_request(method: str, path: str, *, params=None, payload=None, timeout: float = 8.0, actor: str = "") -> dict:
    cfg = agent_config()
    if not cfg["configured"]:
        raise AgentUnavailable("not_configured", "Host-Verwaltungsdienst nicht eingerichtet (" + cfg["problem"] + ").")
    import httpx
    headers = {"Authorization": f"Bearer {cfg['token']}", "Accept": "application/json"}
    if actor:
        headers["X-Pulse-Actor"] = re.sub(r"[^\w .@#-]", "", actor)[:80]
    try:
        response = await _client(cfg["socket"]).request(method, cfg["url"] + path, params=params, json=payload, headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        raise AgentUnavailable("timeout", "Host-Verwaltungsdienst antwortet nicht (Zeitüberschreitung).") from None
    except httpx.HTTPError as exc:
        raise AgentUnavailable("unreachable", f"Host-Verwaltungsdienst nicht erreichbar ({type(exc).__name__}).") from None
    if response.status_code == 401:
        raise AgentUnavailable("unauthorized", "Host-Verwaltungsdienst lehnt das Token ab – PULSE_HOST_AGENT_TOKEN prüfen.", 502)
    try:
        data = response.json()
    except ValueError:
        raise AgentUnavailable("error", f"Ungültige Antwort des Host-Verwaltungsdienstes (HTTP {response.status_code}).", 502) from None
    if not isinstance(data, dict):
        raise AgentUnavailable("error", "Ungültige Antwort des Host-Verwaltungsdienstes.", 502)
    if response.status_code >= 400:
        raise AgentUnavailable("rejected", redact(data.get("error") or f"HTTP {response.status_code}", 300),
                               response.status_code if response.status_code in (400, 404, 409, 429) else 502)
    return data


def _unavailable(exc: AgentUnavailable) -> dict:
    return {"available": False, "reason": exc.reason, "message": exc.message}


# --------------------------------------------------------------------------- local, read-only facts
def read_git_head(base_dir) -> dict:
    """Installed commit read straight from .git (no git binary in the container). Read-only."""
    git = Path(base_dir) / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        branch = ""
        if head.startswith("ref: "):
            ref = head[5:].strip()
            if not ref.startswith("refs/") or ".." in ref:
                return {}
            branch = ref.rsplit("/", 1)[-1] if ref.startswith("refs/heads/") else ""
            ref_file = git / ref
            sha = ref_file.read_text(encoding="utf-8").strip() if ref_file.is_file() else ""
            if not sha and (git / "packed-refs").is_file():
                for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        sha = line.split(" ", 1)[0]
                        break
        else:
            sha = head
        return {"sha": sha, "short": sha[:7], "branch": branch} if SHA_RE.match(sha) else {}
    except OSError:
        return {}


def installed_commit(base_dir) -> dict:
    """Phase 13: the image carries its commit as build information (PULSE_GIT_SHA, Dockerfile ARG) because
    .git is no longer mounted into the container. Without it (bot hosting, local checkout) .git is read."""
    sha = os.getenv("PULSE_GIT_SHA", "").strip().lower()
    if SHA_RE.match(sha):
        branch = re.sub(r"[^\w./-]", "", os.getenv("PULSE_GIT_BRANCH", "").strip())[:80]
        return {"sha": sha, "short": sha[:7], "branch": branch, "source": "build"}
    head = read_git_head(base_dir)
    return {**head, "source": "git"} if head else {}


def cgroup_memory_limit_mb() -> float | None:
    for raw in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = Path(raw).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if value.isdigit() and int(value) < 1 << 50:
            return round(int(value) / (1024 * 1024), 1)
    return None


_github_cache = {"at": 0.0, "data": None}


async def github_main() -> dict:
    """Latest commit on GitHub main via the public API (cached 5 minutes). Never sends a token."""
    repo = os.getenv("PULSE_GITHUB_REPO", "NicoPinguinDEV/Pulse").strip()
    if not repo:
        return {"available": False, "message": "GitHub-Vergleich abgeschaltet (PULSE_GITHUB_REPO leer)."}
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return {"available": False, "message": "PULSE_GITHUB_REPO ungültig."}
    if _github_cache["data"] is not None and time.time() - _github_cache["at"] < GITHUB_CACHE_SECONDS:
        return _github_cache["data"]
    import httpx
    try:
        response = await _client().get(f"https://api.github.com/repos/{repo}/commits/main", timeout=6,
                                       headers={"Accept": "application/vnd.github+json", "User-Agent": "Pulse-TeamOS"})
        if response.status_code != 200:
            data = {"available": False, "message": f"GitHub antwortet mit HTTP {response.status_code}."}
        else:
            body = response.json()
            sha = str(body.get("sha", ""))
            commit = body.get("commit") or {}
            data = {"available": SHA_RE.match(sha) is not None, "sha": sha, "short": sha[:7],
                    "subject": redact(str(commit.get("message", "")).splitlines()[0] if commit.get("message") else "", 200),
                    "date": (commit.get("committer") or {}).get("date")}
    except (httpx.HTTPError, ValueError) as exc:
        data = {"available": False, "message": f"GitHub nicht erreichbar ({type(exc).__name__})."}
    _github_cache.update(at=time.time(), data=data)
    return data


def backup_kind(name: str) -> str:
    for marker, label in (("_auto_", "automatisch"), ("_pre_restore_", "vor Wiederherstellung"),
                          ("_predeploy_", "vor Deploy"), ("_prerollback_", "vor Rollback")):
        if marker in name:
            return label
    return "manuell"


_VERIFY_CACHE: dict[str, dict] = {}
_VERIFY_LOCK = threading.Lock()


def verify_backup_cached(path: Path) -> dict:
    """Checksums of one full backup (pulse_backup.verify_full_backup), cached by name + mtime."""
    import pulse_backup
    key = f"{path.name}:{path.stat().st_mtime_ns}"
    with _VERIFY_LOCK:
        if key in _VERIFY_CACHE:
            return _VERIFY_CACHE[key]
    problems = pulse_backup.verify_full_backup(path)
    result = {"ok": not problems, "problems": problems[:10], "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    with _VERIFY_LOCK:
        if len(_VERIFY_CACHE) > 60:
            _VERIFY_CACHE.clear()
        _VERIFY_CACHE[key] = result
    return result


def other_verified_backup_exists(backup_dir, excluded_name: str) -> bool:
    """True if at least one *other* full backup passes the checksum verification (newest first)."""
    import pulse_backup
    for path in pulse_backup.list_backups(backup_dir):
        if path.name == excluded_name:
            continue
        try:
            if verify_backup_cached(path)["ok"]:
                return True
        except OSError:
            continue
    return False


def backups_overview(backup_dir) -> list[dict]:
    import pulse_backup
    rows = []
    for path in pulse_backup.list_backups(backup_dir)[:40]:
        try:
            stat = path.stat()
        except OSError:
            continue
        key = f"{path.name}:{stat.st_mtime_ns}"
        with _VERIFY_LOCK:
            verified = _VERIFY_CACHE.get(key)
        rows.append({"name": path.name, "kind": backup_kind(path.name), "size_mb": round(stat.st_size / (1024 * 1024), 2),
                     "created": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
                     "verified": verified})
    return rows


# --------------------------------------------------------------------------- local jobs (backup)
_LOCAL_JOBS: deque = deque(maxlen=10)
_LOCAL_LOCK = threading.Lock()


def local_running() -> dict | None:
    with _LOCAL_LOCK:
        return next((j for j in _LOCAL_JOBS if j["status"] == "running"), None)


def _new_local_job(action: str, actor: str) -> dict:
    with _LOCAL_LOCK:
        if any(j["status"] == "running" for j in _LOCAL_JOBS):
            raise RuntimeError("Es läuft bereits ein Vorgang im Dashboard.")
        job = {"id": uuid.uuid4().hex[:12], "action": action, "actor": actor[:80], "status": "running", "phase": "vorbereitung",
               "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "finished_at": None,
               "result": {}, "lines": [], "cancellable": False, "source": "dashboard"}
        _LOCAL_JOBS.append(job)
        return job


def _local_log(job: dict, line: str) -> None:
    job["lines"] = (job["lines"] + [f"{datetime.now().strftime('%H:%M:%S')} {redact(line, 500)}"])[-200:]


def _local_finish(job: dict, status: str, message: str, **extra) -> None:
    job.update(status=status, phase="abgeschlossen", finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               result=dict(extra, message=message))
    _local_log(job, ("✔ " if status == "succeeded" else "✖ ") + message)


def find_local_job(job_id: str) -> dict | None:
    with _LOCAL_LOCK:
        return next((dict(j) for j in _LOCAL_JOBS if j["id"] == job_id), None)


def run_backup_job(job: dict, create_backup) -> None:
    """Full backup, then checksum verification – success is reported only after the verification passed.
    Runs in its own worker thread (file/ZIP work only; the Discord structure is copied before in the event loop)."""
    import pulse_backup
    try:
        job["phase"] = "backup"
        _local_log(job, "Erstelle Komplett-Backup (Datenbanken, JSON, Uploads, Discord-Struktur) …")
        path, manifest = create_backup()
        _local_log(job, f"Geschrieben: {path.name} · {len(manifest.get('files', []))} Dateien")
        job["phase"] = "verifikation"
        _local_log(job, "Prüfe Prüfsummen aller Dateien im Archiv …")
        problems = pulse_backup.verify_full_backup(path)
        if problems:
            _local_finish(job, "failed", "Backup geschrieben, aber die Verifikation ist FEHLGESCHLAGEN: " + "; ".join(problems[:3]),
                          backup=path.name, problems=problems[:10])
            return
        with _VERIFY_LOCK:
            _VERIFY_CACHE[f"{path.name}:{path.stat().st_mtime_ns}"] = {"ok": True, "problems": [], "at": datetime.now().astimezone().isoformat(timespec="seconds")}
        warnings = manifest.get("warnings") or []
        _local_finish(job, "succeeded", f"Backup {path.name} erstellt und verifiziert" + (f" (Hinweise: {'; '.join(warnings)[:200]})" if warnings else "."),
                      backup=path.name, warnings=warnings[:5])
    except Exception as exc:  # visible result instead of a silent failure
        _local_finish(job, "failed", f"Backup fehlgeschlagen ({type(exc).__name__}). Vorhandene Sicherungen bleiben unverändert.")


# --------------------------------------------------------------------------- overview & diagnosis
def _memory_facts() -> dict:
    try:
        from pulse_ultimate import RAM_CRITICAL_MB, RAM_EMERGENCY_MB, RAM_WARN_MB, container_memory_mb, process_peak_rss_mb, process_rss_mb
        return {"container_mb": container_memory_mb(), "process_mb": process_rss_mb(), "peak_mb": process_peak_rss_mb(),
                "limit_mb": cgroup_memory_limit_mb(), "warn_mb": RAM_WARN_MB, "critical_mb": RAM_CRITICAL_MB, "emergency_mb": RAM_EMERGENCY_MB}
    except Exception:
        from pulse_safety import RAM_CRITICAL_MB, RAM_EMERGENCY_MB, RAM_WARN_MB
        return {"container_mb": None, "process_mb": None, "peak_mb": None, "limit_mb": cgroup_memory_limit_mb(),
                "warn_mb": RAM_WARN_MB, "critical_mb": RAM_CRITICAL_MB, "emergency_mb": RAM_EMERGENCY_MB}


def _disk_facts(base_dir) -> dict:
    try:
        usage = shutil.disk_usage(base_dir)
        return {"total_gb": round(usage.total / 1024 ** 3, 1), "free_gb": round(usage.free / 1024 ** 3, 1),
                "used_percent": round(100 * (usage.total - usage.free) / usage.total, 1) if usage.total else None}
    except OSError:
        return {"total_gb": None, "free_gb": None, "used_percent": None}


def app_facts(ws, app) -> dict:
    import pulse_db
    bot = getattr(app.state, "bot", None)
    try:
        guild = bot.get_guild(ws.GUILD_ID) if bot is not None else None
    except Exception:
        guild = None
    try:
        bot_ready = bool(bot is not None and getattr(bot, "is_ready", lambda: False)())
    except Exception:
        bot_ready = False
    try:
        with pulse_db.connect() as db:
            db.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    latency = getattr(bot, "latency", None) if bot else None
    try:
        import pulse_jobs
        jobs = pulse_jobs.health_summary()
    except Exception:
        jobs = {"last_job_ok": None, "jobs_failing": []}
    try:
        backup = ws.full_backup_status()
    except Exception:
        backup = {"latest": "", "count": 0, "restore_test": {}, "total_mb": None, "total_ok": True}
    latest_age_h = None
    if backup.get("latest"):
        try:
            latest_path = Path(ws.full_backup_dir()) / backup["latest"]
            latest_age_h = round((time.time() - latest_path.stat().st_mtime) / 3600, 1)
        except OSError:
            pass
    return {
        "version": ws.PULSE_VERSION, "commit": installed_commit(ws.BASE_DIR),
        "process_uptime_seconds": int(time.time() - _PROCESS_STARTED),
        "bot_ready": bot_ready, "guild_ready": bool(guild), "guild_name": getattr(guild, "name", None),
        "member_count": getattr(guild, "member_count", None) if guild else None, "database_ok": db_ok,
        "gateway_latency_ms": round(latency * 1000) if isinstance(latency, (int, float)) and latency == latency and latency != float("inf") else None,
        "memory": _memory_facts(), "disk": _disk_facts(ws.DATA_DIR), "jobs_failing": jobs.get("jobs_failing", []),
        "last_job_ok": jobs.get("last_job_ok"), "backup": {**backup, "latest_age_hours": latest_age_h},
    }


def _finding(component, severity, cause, confirmed, recommendation, evidence="", at=None) -> dict:
    return {"at": at or datetime.now().astimezone().isoformat(timespec="seconds"), "component": component, "severity": severity,
            "cause": redact(cause, 400), "confirmed": bool(confirmed), "evidence": redact(evidence, 400), "recommendation": recommendation}


def diagnose(facts: dict, agent_status: dict | None, agent_git: dict | None, agent_jobs: dict | None, errors: list, log_records: list) -> list:
    """Every finding: time, component, cause (confirmed measurement vs. assumption), recommendation."""
    out = []
    if not facts["bot_ready"]:
        out.append(_finding("Discord-Bot", "bad", "Bot ist nicht mit Discord verbunden.", True,
                            "Container-Logs auf Login-/Token-Fehler prüfen; bei 4004 das Token im Developer Portal erneuern."))
    elif not facts["guild_ready"]:
        out.append(_finding("Discord-Bot", "bad", "Bot ist verbunden, sieht den konfigurierten Server aber nicht.", True,
                            "DISCORD_GUILD_ID in .env und die Server-Einladung des Bots prüfen."))
    if not facts["database_ok"]:
        out.append(_finding("Datenbank", "bad", "pulse.db antwortet nicht auf SELECT 1.", True,
                            "Speicherplatz und Dateirechte (UID 1000) prüfen, danach ein verifiziertes Backup wiederherstellen."))
    latency = facts.get("gateway_latency_ms")
    if latency is not None and latency > 1500:
        out.append(_finding("Discord-Gateway", "warn", f"Gateway-Latenz {latency} ms. Vermutung: Netzwerk- oder Discord-Störung.", False,
                            "discordstatus.com prüfen; bei dauerhaft hoher Latenz Host-Last und Netzwerk prüfen.", f"gemessen: {latency} ms"))
    memory = facts["memory"]
    used = memory.get("container_mb")
    if used is not None:
        if used >= memory["emergency_mb"]:
            sev, text = "bad", "RAM im Notfallbereich"
        elif used >= memory["critical_mb"]:
            sev, text = "bad", "RAM kritisch"
        elif used >= memory["warn_mb"]:
            sev, text = "warn", "RAM über der Warnschwelle"
        else:
            sev, text = "", ""
        if sev:
            out.append(_finding("Arbeitsspeicher", sev, f"{text}: {used} MB (Limit {memory.get('limit_mb') or 'unbekannt'} MB).", True,
                                "Ursache in den Logs suchen (große Caches, Voice/ffmpeg); kontrollierter Neustart als Sofortmaßnahme.",
                                f"cgroup memory.current = {used} MB"))
    disk = facts["disk"]
    if disk.get("free_gb") is not None and disk["free_gb"] < 1:
        out.append(_finding("Speicherplatz", "bad", f"Nur noch {disk['free_gb']} GB frei.", True,
                            "Alte manuelle Backups prüfen/herunterladen und entfernen, Docker-Images aufräumen (docker image prune)."))
    for name in facts.get("jobs_failing") or []:
        out.append(_finding("Hintergrundjobs", "warn", f"Letzter Lauf von „{name}“ ist fehlgeschlagen.", True,
                            "Systemstatus → Jobs öffnen und die Fehlermeldung des Jobs prüfen."))
    backup = facts["backup"]
    if not backup.get("count"):
        out.append(_finding("Backups", "bad", "Es gibt noch kein Komplett-Backup.", True, "Im Tab Backups jetzt ein Backup erstellen."))
    elif backup.get("latest_age_hours") is not None and backup["latest_age_hours"] > 48:
        out.append(_finding("Backups", "warn", f"Neuestes Komplett-Backup ist {backup['latest_age_hours']} Stunden alt.", True,
                            "Automatische Sicherung prüfen (Systemstatus) oder manuell ein Backup erstellen."))
    test = backup.get("restore_test") or {}
    if test and not test.get("ok"):
        out.append(_finding("Backups", "bad", "Letzter automatischer Restore-Test ist fehlgeschlagen.", True,
                            "Betroffenes Backup nicht verwenden; neues Backup erstellen und verifizieren.",
                            "; ".join(test.get("problems", []))[:300], test.get("at")))
    if agent_status is not None and not agent_status.get("available", True):
        severity = "info" if agent_status.get("reason") == "not_configured" else "warn"
        out.append(_finding("Host-Verwaltungsdienst", severity, agent_status.get("message", "nicht verfügbar"), True,
                            "docs/SERVER_CONTROL_CENTER.md → Abschnitt Installation; systemctl status pulse-host-agent."))
    elif agent_status is not None:
        container = agent_status.get("container") or {}
        if container.get("oom_killed"):
            out.append(_finding("Container", "bad", "Container wurde wegen Speichermangels beendet (OOMKilled).", True,
                                "RAM-Verlauf prüfen; mem_limit in compose.yaml nur nach Rücksprache erhöhen."))
        if container.get("health") == "unhealthy":
            out.append(_finding("Container", "bad", "Docker meldet den Container als unhealthy.", True,
                                "Letzte Healthcheck-Ausgabe und Container-Logs prüfen.", container.get("last_health_output", "")))
        if container.get("status") and container.get("status") != "running":
            out.append(_finding("Container", "bad", f"Container-Status: {container.get('status')} (Exit-Code {container.get('exit_code')}).", True,
                                "Build-/Startfehler im Tab Container prüfen; danach kontrolliert neu starten.", container.get("error", "")))
        if (container.get("restart_count") or 0) > 0:
            out.append(_finding("Container", "warn", f"Container wurde {container['restart_count']}× automatisch neu gestartet.", True,
                                "Logs rund um den Neustart prüfen (Absturz, OOM)."))
    if agent_git and agent_git.get("available", True) and agent_git.get("reasons"):
        out.append(_finding("Deployment", "warn", "Git-Zustand auf dem Server ist nicht sicher: " + " ".join(agent_git["reasons"]), True,
                            "Lokale Änderungen auf dem VPS prüfen (git status); Deploys bleiben gesperrt, bis der Zustand sauber ist."))
    job = (agent_jobs or {}).get("job") if agent_jobs and agent_jobs.get("available", True) else None
    if job and job.get("status") == "failed":
        out.append(_finding("Deployment", "bad", f"Letzter Vorgang „{job.get('action')}“ fehlgeschlagen: {(job.get('result') or {}).get('message', '')}",
                            True, "Ausgabe des Vorgangs im Tab Deployment prüfen; bei Bedarf Rollback.", at=job.get("finished_at")))
    by_source: dict[str, list] = {}
    for err in errors:
        by_source.setdefault(err.get("source", "unbekannt"), []).append(err)
    for source, items in list(by_source.items())[:8]:
        newest = items[0]
        out.append(_finding(f"Fehlerpuffer · {source}", "warn", f"{len(items)} Fehler seit dem Start, zuletzt: {newest.get('message', '')}",
                            True, "Meldung in der Live-Konsole nach Zeitpunkt suchen.", newest.get("error_id", ""), newest.get("at")))
    rate_hits = [r for r in log_records if "429" in r.get("text", "") or "rate limit" in r.get("text", "").lower()]
    if rate_hits:
        out.append(_finding("Externe Dienste", "warn", f"{len(rate_hits)} Rate-Limit-Meldungen (HTTP 429) im Protokoll. Vermutung: "
                            "Discord- oder KI-Quote erschöpft.", False, "Keine schnellen Wiederholungen auslösen; Quoten/Intervalle prüfen.",
                            rate_hits[-1].get("text", ""), rate_hits[-1].get("time")))
    tracebacks = [r for r in log_records if "traceback" in r.get("text", "").lower()]
    if tracebacks:
        out.append(_finding("Anwendung", "warn", f"{len(tracebacks)} Python-Tracebacks im Protokoll. Vermutung: unbehandelter Fehler.", False,
                            "Traceback in der Live-Konsole öffnen (Filter: Fehler).", tracebacks[-1].get("text", "")[:300], tracebacks[-1].get("time")))
    order = {"bad": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda f: order.get(f["severity"], 3))


# --------------------------------------------------------------------------- console
COMMANDS = {
    "help": "Befehlsübersicht",
    "status": "Bot-, Discord- und Containerstatus",
    "health": "Datenbank-, Bot- und Webserverstatus",
    "logs": "letzte Protokollzeilen (logs 50 = 50 Zeilen, max. 200)",
    "ram": "Arbeitsspeicher von Container, Prozess und Host",
    "disk": "Speicherplatz und Größe der Backups",
    "version": "installierte Version und Commit",
    "backup": "Komplett-Backup erstellen und verifizieren (mit Bestätigung)",
    "update": "Deployment von GitHub main (mit Vorschau und Bestätigung)",
    "restart": "Container kontrolliert neu starten (mit Bestätigung)",
    "sync": "Discord-Slash-Befehle synchronisieren (höchstens alle 10 Minuten)",
    "cancel": "laufenden Vorgang abbrechen, solange noch nichts verändert wurde",
    "clear": "Ausgabe im Browser leeren",
}


def parse_command(raw) -> tuple[str, int | None] | None:
    text = str(raw or "").strip().lower()
    if not text or len(text) > 20:
        return None
    match = COMMAND_RE.fullmatch(text)
    if not match or match.group(1) not in COMMANDS:
        return None
    arg = int(match.group(2)) if match.group(2) else None
    if arg is not None and match.group(1) != "logs":
        return None
    return match.group(1), arg


def _fmt(value, unit="", missing="nicht verfügbar") -> str:
    return missing if value is None else f"{value}{unit}"


# --------------------------------------------------------------------------- routes
def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page

    import pulse_health
    pulse_health.install_rate_limit_recorder()  # Live-Health: count Discord 429s from discord.py's own log

    streams = {"total": 0, "per_user": {}}
    streams_lock = threading.Lock()
    audited_jobs: deque = deque(maxlen=100)

    def ctx_for(request, session):
        return ws.auth(request, session, perm=PERMISSION)

    def actor_of(ctx) -> str:
        return str(ctx.user.get("global_name") or ctx.user.get("username") or "Unbekannt")

    def audit(ctx, action: str, result: str, reason: str = "") -> None:
        details = f"Ergebnis: {result}" + (f" · Grund: {reason}" if reason else "")
        ws.log_audit(actor_of(ctx), ctx.user["id"], f"Server Control Center: {action}", redact(details, 600))

    def limited(ctx, key, limit, window):
        if ws.rate_limited(f"scc:{key}:{ctx.user['id']}", limit=limit, window=window):
            raise HTTPException(status_code=429, detail="Zu viele Anfragen – bitte kurz warten.")

    async def json_body(request) -> dict:
        if "application/json" not in (request.headers.get("content-type") or ""):
            raise HTTPException(status_code=415, detail="Nur JSON-Anfragen sind erlaubt.")
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Ungültiges JSON.") from None
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="JSON-Objekt erwartet.")
        return data

    async def agent_or_unavailable(method, path, **kwargs) -> dict:
        try:
            data = await agent_request(method, path, **kwargs)
            data["available"] = True
            return data
        except AgentUnavailable as exc:
            return _unavailable(exc)

    def maybe_audit_job(ctx, job):
        if job and job.get("status") in ("succeeded", "failed", "cancelled") and job.get("id") not in audited_jobs:
            audited_jobs.append(job.get("id"))
            audit(ctx, f"{job.get('action')} beendet", job.get("status"), (job.get("result") or {}).get("message", ""))

    # ------------------------------------------------------------------ page
    @app.get("/admin/server", response_class=HTMLResponse)
    async def server_control_page(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        cfg = agent_config()
        body = page_html(ws.esc, cfg["configured"], cfg["problem"], bool(ctx.perms.get("is_admin")))
        return HTMLResponse(render_pro_page(ws, "Server Control Center", ctx, PAGE_KEY, body))

    # ------------------------------------------------------------------ read-only APIs
    @app.get("/admin/server/api/overview")
    async def scc_overview(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "read", 240, 60)
        facts = app_facts(ws, request.app)
        agent = await agent_or_unavailable("GET", "/v1/status")
        github = await github_main()
        installed = facts["commit"].get("sha")
        compare = {"installed": facts["commit"] or None, "github": github,
                   "up_to_date": (installed == github.get("sha")) if installed and github.get("available") else None}
        return JSONResponse({"ok": True, "app": facts, "agent": agent, "compare": compare,
                             "agent_configured": agent_config()["configured"], "at": datetime.now().astimezone().isoformat(timespec="seconds")})

    @app.get("/admin/server/api/container")
    async def scc_container(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "read", 240, 60)
        status = await agent_or_unavailable("GET", "/v1/status")
        jobs = await agent_or_unavailable("GET", "/v1/jobs/current")
        local = local_running()
        return JSONResponse({"ok": True, "status": status, "jobs": jobs, "local_job": local})

    @app.get("/admin/server/api/deploy")
    async def scc_deploy_status(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "read", 240, 60)
        git = await agent_or_unavailable("GET", "/v1/git", timeout=20)
        jobs = await agent_or_unavailable("GET", "/v1/jobs/current")
        return JSONResponse({"ok": True, "git": git, "jobs": jobs, "installed_local": installed_commit(ws.BASE_DIR) or None,
                             "github": await github_main()})

    @app.get("/admin/server/api/backups")
    async def scc_backups(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "read", 240, 60)
        rows = await asyncio.to_thread(backups_overview, ws.full_backup_dir())
        status = ws.full_backup_status()
        return JSONResponse({"ok": True, "backups": rows, "status": status, "can_restore": bool(ctx.perms.get("is_admin")),
                             "disk": _disk_facts(ws.full_backup_dir() if os.path.isdir(ws.full_backup_dir()) else ws.DATA_DIR)})

    @app.get("/admin/server/api/health")
    async def scc_health(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "read", 240, 60)
        import pulse_db
        import pulse_health
        bot = getattr(request.app.state, "bot", None)
        data = await asyncio.to_thread(pulse_health.collect, _memory_facts(), ws.DATA_DIR, pulse_db.DB_PATH, bot)
        return JSONResponse({"ok": True, **data})

    @app.get("/admin/server/api/diagnose")
    async def scc_diagnose(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "read", 240, 60)
        import pulse_live_console
        import pulse_logging
        facts = app_facts(ws, request.app)
        status = await agent_or_unavailable("GET", "/v1/status") if agent_config()["configured"] else _unavailable(
            AgentUnavailable("not_configured", "Host-Verwaltungsdienst nicht eingerichtet – Container-, Git- und Deploy-Werte sind nicht verfügbar."))
        git = await agent_or_unavailable("GET", "/v1/git", timeout=20) if status.get("available") else None
        jobs = await agent_or_unavailable("GET", "/v1/jobs/current") if status.get("available") else None
        findings = diagnose(facts, status, git, jobs, pulse_logging.recent(50), pulse_live_console.records_since(0, 500))
        return JSONResponse({"ok": True, "findings": findings, "at": datetime.now().astimezone().isoformat(timespec="seconds")})

    @app.get("/admin/server/api/jobs/{job_id}")
    async def scc_job(request: Request, job_id: str, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "read", 240, 60)
        if not JOB_ID_RE.fullmatch(job_id):
            raise HTTPException(status_code=400, detail="Ungültige Vorgangs-ID.")
        local = find_local_job(job_id)
        if local:
            maybe_audit_job(ctx, local)
            return JSONResponse({"ok": True, "job": local})
        try:
            data = await agent_request("GET", f"/v1/jobs/{job_id}")
        except AgentUnavailable as exc:
            return JSONResponse({"ok": False, **_unavailable(exc)}, status_code=exc.status)
        maybe_audit_job(ctx, data.get("job"))
        job = data.get("job") or {}
        job["lines"] = [redact(line, 2000) for line in job.get("lines", [])][-600:]
        return JSONResponse({"ok": True, "job": job})

    # ------------------------------------------------------------------ logs: snapshot + SSE
    def app_lines(since_seq=0, limit=STREAM_BATCH) -> list[dict]:
        import pulse_live_console
        return [{"id": r["seq"], "ts": r["time"], "level": classify(r["text"], r["level"]), "text": redact(f"{r['logger']}: {r['text']}")}
                for r in pulse_live_console.records_since(since_seq, limit)]

    def container_lines(raw: list, seen: set) -> list[dict]:
        out = []
        for item in raw:
            key = (item.get("ts", ""), item.get("text", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": item.get("ts", ""), "ts": item.get("ts", ""), "level": classify(item.get("text", "")), "text": redact(item.get("text", ""))})
        if len(seen) > 2000:
            seen.clear()
            seen.update((line["ts"], line["text"]) for line in out[-200:])
        return out

    @app.get("/admin/server/api/logs/stream")
    async def scc_log_stream(request: Request, source: str = "auto", since: str = "", user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "stream", 30, 60)
        if source not in ("auto", "container", "app"):
            raise HTTPException(status_code=400, detail="Ungültige Quelle.")
        if since and not re.fullmatch(r"\d{1,12}(?:\.\d{1,9})?|\d{4}-\d\d-\d\dT[\d:.]+Z?", since):
            raise HTTPException(status_code=400, detail="Ungültiger Startpunkt.")
        if source == "auto":
            source = "container" if agent_config()["configured"] else "app"
        uid = str(ctx.user["id"])
        with streams_lock:
            if streams["total"] >= STREAM_MAX_TOTAL or streams["per_user"].get(uid, 0) >= STREAM_MAX_PER_USER:
                raise HTTPException(status_code=429, detail="Zu viele offene Log-Streams – bitte andere Tabs schließen.")
            streams["total"] += 1
            streams["per_user"][uid] = streams["per_user"].get(uid, 0) + 1

        def release():
            with streams_lock:
                streams["total"] = max(0, streams["total"] - 1)
                streams["per_user"][uid] = max(0, streams["per_user"].get(uid, 1) - 1)
                if not streams["per_user"][uid]:
                    streams["per_user"].pop(uid, None)

        def event(name, data, event_id=None) -> str:
            head = f"id: {event_id}\n" if event_id not in (None, "") else ""
            return f"{head}event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        async def generator():
            started = last_beat = time.monotonic()
            seen: set = set()
            cursor = since
            backoff = STREAM_POLL_SECONDS
            try:
                yield "retry: 3000\n\n"
                yield event("status", {"state": "connected", "source": source})
                first = True
                while time.monotonic() - started < STREAM_MAX_SECONDS:
                    if await request.is_disconnected():
                        return
                    lines, error = [], ""
                    if source == "app":
                        lines = app_lines(int(float(cursor)) if cursor and cursor.replace(".", "", 1).isdigit() else 0)
                        if lines:
                            cursor = str(lines[-1]["id"])
                    else:
                        params = {"tail": STREAM_BATCH if first else 500}
                        if cursor:
                            epoch = _ts_to_epoch(cursor)
                            params["since"] = f"{epoch + 0.000001:.6f}" if epoch else cursor
                        try:
                            data = await agent_request("GET", "/v1/logs", params=params, timeout=12)
                            if not data.get("ok"):
                                error = data.get("error", "docker logs fehlgeschlagen")
                            lines = container_lines(data.get("lines", []), seen)
                            if lines and lines[-1]["ts"]:
                                cursor = lines[-1]["ts"]
                            backoff = STREAM_POLL_SECONDS
                        except AgentUnavailable as exc:
                            error = exc.message
                            backoff = min(backoff * 2, 20.0)
                    first = False
                    if error:
                        yield event("source_error", {"message": redact(error, 300)})
                    if lines:
                        yield event("lines", {"lines": lines[-STREAM_BATCH:]}, cursor)
                        last_beat = time.monotonic()
                    elif time.monotonic() - last_beat >= STREAM_HEARTBEAT_SECONDS:
                        yield ": ping\n\n"
                        last_beat = time.monotonic()
                    await asyncio.sleep(backoff)
                yield event("status", {"state": "expired", "message": "Stream nach Zeitlimit beendet – verbinde neu."})
            finally:
                release()

        return StreamingResponse(generator(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ console
    @app.post("/admin/server/api/console")
    async def scc_console(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        limited(ctx, "console", 30, 60)
        body = await json_body(request)
        parsed = parse_command(body.get("command"))
        if parsed is None:
            audit(ctx, "Konsole abgelehnt", "abgelehnt", f"ungültiger Befehl {str(body.get('command'))[:20]!r}")
            return JSONResponse({"ok": False, "output": "Unbekannter oder ungültiger Befehl. Erlaubt: " + ", ".join(COMMANDS) +
                                 ". Freie Shell-Befehle gibt es hier absichtlich nicht."}, status_code=400)
        command, arg = parsed
        result = await run_console_command(command, arg, ctx, request)
        audit(ctx, f"Konsole {command}", "ok" if result.get("ok") else "fehlgeschlagen", "" if result.get("ok") else result.get("output", "")[:200])
        result["output"] = redact(result.get("output", ""), 20000)
        return JSONResponse(result, status_code=200)

    async def run_console_command(command, arg, ctx, request) -> dict:
        import pulse_live_console
        if command == "help":
            width = max(len(k) for k in COMMANDS)
            return {"ok": True, "output": "Verfügbare Befehle:\n" + "\n".join(f"  {k.ljust(width)}  {v}" for k, v in COMMANDS.items())
                    + "\n\nFreie Shell-Befehle sind absichtlich deaktiviert."}
        if command == "clear":
            return {"ok": True, "output": "", "client": "clear"}
        if command in ("backup", "update", "restart"):
            action = {"backup": "backup", "update": "deploy", "restart": "restart"}[command]
            return {"ok": True, "requires_confirmation": True, "action": action,
                    "output": "Diese Aktion braucht eine ausdrückliche Bestätigung – der Bestätigungsdialog wird geöffnet."}
        if command == "cancel":
            jobs = await agent_or_unavailable("GET", "/v1/jobs/current")
            job = (jobs.get("job") or {}) if jobs.get("available") else {}
            if job.get("status") != "running":
                return {"ok": False, "output": "Kein laufender Vorgang." if jobs.get("available") else "Host-Verwaltungsdienst: " + jobs.get("message", "")}
            return {"ok": True, "requires_confirmation": True, "action": "cancel", "job_id": job.get("id"),
                    "output": f"Abbruch von „{job.get('action')}“ anfordern?"}
        if command == "sync":
            if ws.rate_limited(f"scc:sync:{ctx.user['id']}", limit=1, window=600):
                return {"ok": False, "output": "Synchronisierung ist auf einmal je 10 Minuten begrenzt."}
            return await pulse_live_console.run_command("sync", request.app)
        if command == "logs":
            lines = app_lines(0, arg or 50)
            return {"ok": True, "output": "\n".join(f"{line['ts']} {line['level'].upper():<7} {line['text']}" for line in lines)
                    or "Noch keine Protokollzeilen erfasst."}
        facts = app_facts(ws, request.app)
        if command == "version":
            commit = facts["commit"]
            return {"ok": True, "output": f"Pulse TeamOS {facts['version']}\nCommit: {commit.get('short', 'nicht verfügbar')}"
                    + (f" (Branch {commit['branch']})" if commit.get("branch") else "")}
        if command == "disk":
            disk = facts["disk"]
            backups = facts["backup"]
            return {"ok": True, "output": f"Datenträger: {_fmt(disk['free_gb'], ' GB')} frei von {_fmt(disk['total_gb'], ' GB')} "
                    f"({_fmt(disk['used_percent'], ' %')} belegt)\nKomplett-Backups: {backups.get('count', 0)} · {_fmt(backups.get('total_mb'), ' MB')}"}
        if command == "ram":
            mem = facts["memory"]
            text = (f"Container (cgroup): {_fmt(mem['container_mb'], ' MB')} von {_fmt(mem['limit_mb'], ' MB')}\n"
                    f"Pulse-Prozess: {_fmt(mem['process_mb'], ' MB')} · Spitze {_fmt(mem['peak_mb'], ' MB')}\n"
                    f"Schwellen: Warnung {mem['warn_mb']} · kritisch {mem['critical_mb']} · Notfall {mem['emergency_mb']} MB")
            status = await agent_or_unavailable("GET", "/v1/status")
            host = (status.get("host") or {}).get("memory") if status.get("available") else None
            text += ("\nHost: " + (f"{_fmt(host.get('available_mb'), ' MB')} frei von {_fmt(host.get('total_mb'), ' MB')}" if host
                                   else "nicht verfügbar (" + status.get("message", "Host-Dienst") + ")"))
            return {"ok": True, "output": text}
        if command in ("status", "health"):
            text = (f"Version: {facts['version']}\nBot bereit: {'ja' if facts['bot_ready'] else 'nein'}\n"
                    f"Discord-Server: {facts['guild_name'] or 'nicht verfügbar'}\nDatenbank: {'erreichbar' if facts['database_ok'] else 'NICHT erreichbar'}\n"
                    f"Gateway-Latenz: {_fmt(facts['gateway_latency_ms'], ' ms')}")
            if command == "status":
                status = await agent_or_unavailable("GET", "/v1/status")
                container = status.get("container") if status.get("available") else None
                text += ("\nContainer: " + (f"{container.get('status')} · Health {container.get('health')} · Neustarts {_fmt(container.get('restart_count'))}"
                                            if container else "nicht verfügbar (" + status.get("message", "Host-Dienst") + ")"))
            return {"ok": facts["database_ok"], "output": text}
        return {"ok": False, "output": "Befehl nicht verfügbar."}

    # ------------------------------------------------------------------ actions
    @app.post("/admin/server/api/deploy/fetch")
    async def scc_fetch(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        await json_body(request)
        limited(ctx, "fetch", 6, 60)
        try:
            data = await agent_request("POST", "/v1/git/fetch", payload={}, timeout=100, actor=actor_of(ctx))
        except AgentUnavailable as exc:
            audit(ctx, "GitHub-Abgleich", "fehlgeschlagen", exc.message)
            return JSONResponse({"ok": False, **_unavailable(exc)}, status_code=exc.status)
        audit(ctx, "GitHub-Abgleich", "ok")
        return JSONResponse({"ok": True, "git": {**data, "available": True}})

    @app.post("/admin/server/api/actions")
    async def scc_action(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        body = await json_body(request)
        action = str(body.get("action", ""))
        if action not in AGENT_ACTIONS + LOCAL_ACTIONS:
            audit(ctx, "Aktion abgelehnt", "abgelehnt", f"unbekannte Aktion {action[:20]!r}")
            raise HTTPException(status_code=400, detail="Unbekannte Aktion.")
        word = CONFIRM_WORDS.get(action)
        if word and str(body.get("confirm", "")).strip() != word:
            audit(ctx, action, "abgelehnt", "Bestätigung fehlt")
            raise HTTPException(status_code=400, detail=f"Bitte zur Bestätigung {word} eingeben.")
        params = {}
        if action == "deploy":
            sha = str(body.get("expected_sha", ""))
            if not SHA_RE.fullmatch(sha):
                raise HTTPException(status_code=400, detail="Deploy braucht die Commit-SHA aus der Vorschau.")
            params["expected_sha"] = sha
        if action == "rollback":
            sha = str(body.get("target_sha", ""))
            if not SHA_RE.fullmatch(sha):
                raise HTTPException(status_code=400, detail="Rollback braucht eine vollständige Ziel-Commit-SHA.")
            params["target_sha"] = sha
        limited(ctx, "action", 6, 600)
        if ws.rate_limited(f"scc:action-dup:{action}:{ctx.user['id']}", limit=1, window=5):
            raise HTTPException(status_code=409, detail="Diese Aktion wurde gerade erst ausgelöst (Doppelklick-Schutz).")
        running_local = local_running()
        if running_local:
            audit(ctx, action, "abgelehnt", f"läuft bereits: {running_local['action']}")
            raise HTTPException(status_code=409, detail=f"Es läuft bereits ein Vorgang ({running_local['action']}).")
        if action in LOCAL_ACTIONS:
            agent_jobs = await agent_or_unavailable("GET", "/v1/jobs/current") if agent_config()["configured"] else {}
            if (agent_jobs.get("job") or {}).get("status") == "running":
                raise HTTPException(status_code=409, detail=f"Es läuft bereits ein Vorgang ({agent_jobs['job']['action']}).")
            try:
                job = _new_local_job(action, actor_of(ctx))
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            import pulse_backup
            try:  # D-03: the Discord structure is read in the event loop, the ZIP work happens in the worker thread
                structure = ws.build_discord_structure(ctx.guild) if ctx.guild is not None else None
            except Exception as exc:
                structure = None
                _local_log(job, f"Discord-Struktur nicht lesbar ({type(exc).__name__}) – Backup ohne Struktur.")
            base_dir, backup_dir = ws.DATA_DIR, ws.full_backup_dir()
            threading.Thread(target=run_backup_job, name="pulse-scc-backup", daemon=True, args=(job, lambda: pulse_backup.create_full_backup(
                base_dir, backup_dir, discord_structure=structure))).start()
            audit(ctx, action, "gestartet")
            return JSONResponse({"ok": True, "job": find_local_job(job["id"])}, status_code=202)
        try:
            data = await agent_request("POST", "/v1/jobs", payload={"action": action, **params}, timeout=15, actor=actor_of(ctx))
        except AgentUnavailable as exc:
            audit(ctx, action, "nicht gestartet", exc.message)
            return JSONResponse({"ok": False, **_unavailable(exc)}, status_code=exc.status)
        audit(ctx, action, "gestartet", f"Ziel {params.get('expected_sha', params.get('target_sha', ''))[:7]}".strip())
        return JSONResponse({"ok": True, "job": data.get("job")}, status_code=202)

    @app.post("/admin/server/api/jobs/{job_id}/cancel")
    async def scc_cancel(request: Request, job_id: str, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        await json_body(request)
        limited(ctx, "cancel", 10, 60)
        if not JOB_ID_RE.fullmatch(job_id):
            raise HTTPException(status_code=400, detail="Ungültige Vorgangs-ID.")
        if find_local_job(job_id):
            raise HTTPException(status_code=409, detail="Ein laufendes Backup kann nicht abgebrochen werden (es verändert keine Daten).")
        try:
            data = await agent_request("POST", f"/v1/jobs/{job_id}/cancel", payload={}, actor=actor_of(ctx))
        except AgentUnavailable as exc:
            audit(ctx, "Abbruch", "abgelehnt", exc.message)
            return JSONResponse({"ok": False, **_unavailable(exc)}, status_code=exc.status)
        audit(ctx, "Abbruch", "angefordert", job_id)
        return JSONResponse({"ok": True, "job": data.get("job")})

    @app.post("/admin/server/api/backups/verify")
    async def scc_backup_verify(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        body = await json_body(request)
        limited(ctx, "verify", 10, 300)
        name = os.path.basename(str(body.get("filename", "")))
        path = ws.safe_full_backup_path(name) if BACKUP_NAME_RE.fullmatch(name) else None
        if not path:
            raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
        with _VERIFY_LOCK:
            for key in [k for k in _VERIFY_CACHE if k.startswith(name + ":")]:
                _VERIFY_CACHE.pop(key, None)
        result = await asyncio.to_thread(verify_backup_cached, Path(path))
        audit(ctx, "Backup verifiziert", "ok" if result["ok"] else "FEHLERHAFT", name + ("" if result["ok"] else " · " + "; ".join(result["problems"])[:200]))
        return JSONResponse({"ok": True, "name": name, "verified": result})


def _ts_to_epoch(value: str) -> float | None:
    """RFC3339(Nano) from docker logs → Unix seconds (microsecond precision)."""
    try:
        text = str(value).strip().replace("Z", "+00:00")
        match = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(.*)$", text)
        if not match:
            return None
        frac = (match.group(2) or ".0")[:7]
        return datetime.fromisoformat(match.group(1) + frac + (match.group(3) or "+00:00")).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------- page
def page_html(esc, agent_configured: bool, agent_problem: str, is_admin: bool) -> str:
    banner = "" if agent_configured else (
        '<div class="scc-banner warn" role="status"><strong>Host-Verwaltungsdienst nicht eingerichtet.</strong> '
        f'Container-Logs, Container-Status, Git-Vergleich, Deploy, Neustart und Rollback sind nicht verfügbar ({esc(agent_problem)}). '
        'Die Live-Konsole zeigt bis dahin das Pulse-Anwendungsprotokoll. Einrichtung: <code>docs/SERVER_CONTROL_CENTER.md</code>.</div>')
    return SCC_TEMPLATE.replace("%%BANNER%%", banner).replace("%%IS_ADMIN%%", "true" if is_admin else "false") \
        .replace("%%AGENT%%", "true" if agent_configured else "false").replace("%%COMMANDS%%", esc(", ".join(COMMANDS)))


SCC_TEMPLATE = r"""
<style>
.scc-tabs{display:flex;flex-wrap:wrap;gap:6px;margin:18px 0 14px}
.scc-tab{padding:9px 14px;border-radius:12px;border:1px solid var(--border);background:var(--surface);font-size:12px;font-weight:750;cursor:pointer}
.scc-tab[aria-selected=true]{background:linear-gradient(135deg,var(--p),var(--p2));color:#fff;border-color:transparent}
.scc-panel[hidden],#scc-newlines[hidden],#scc-job[hidden]{display:none!important}
.scc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.scc-kv{display:grid;grid-template-columns:minmax(0,42%) minmax(0,1fr);gap:6px 14px;font-size:12px}.scc-kv dt{color:var(--muted)}.scc-kv dd{margin:0;font-weight:650;word-break:break-word}
.scc-na{color:var(--muted);font-style:italic;font-weight:500}
.scc-banner{border-radius:14px;padding:12px 14px;font-size:12px;margin-top:14px;border:1px solid var(--border)}
.scc-banner.warn{background:rgba(247,144,9,.08);border-color:rgba(247,144,9,.25)}.scc-banner.bad{background:rgba(240,68,56,.08);border-color:rgba(240,68,56,.25)}.scc-banner.good{background:rgba(18,183,106,.08);border-color:rgba(18,183,106,.25)}
.scc-term{background:#0b1020;color:#cbd5e1;border-radius:12px;font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;height:460px;overflow:auto;padding:10px 12px;white-space:pre-wrap;word-break:break-word}
.scc-term .l-error{color:#fca5a5}.scc-term .l-warning{color:#fcd34d}.scc-term .ts{color:#64748b;margin-right:8px}.scc-term mark{background:#facc15;color:#111}
.scc-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
.scc-toolbar .pulse-input,.scc-toolbar select{max-width:220px}
.scc-dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px;background:#94a3b8}.scc-dot.on{background:#12b76a}.scc-dot.wait{background:#f79009}.scc-dot.off{background:#f04438}
.scc-out{background:#0b1020;color:#d1fae5;border-radius:12px;font:12px/1.5 ui-monospace,monospace;padding:12px;white-space:pre-wrap;max-height:300px;overflow:auto;margin-top:10px}
.scc-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}.scc-bar{height:8px;border-radius:99px;background:var(--border);overflow:hidden;margin-top:6px}.scc-bar>i{display:block;height:100%;border-radius:99px;background:#12b76a}.scc-bar.warn>i{background:#f79009}.scc-bar.bad>i{background:#f04438}
.scc-table{width:100%;font-size:12px;border-collapse:collapse}.scc-table td,.scc-table th{padding:7px 8px;border-top:1px solid var(--border);text-align:left;vertical-align:top}
.scc-finding{border:1px solid var(--border);border-radius:14px;padding:12px;margin-bottom:10px;background:var(--surface2)}
.scc-finding.bad{border-color:rgba(240,68,56,.35)}.scc-finding.warn{border-color:rgba(247,144,9,.35)}
.scc-modal{position:fixed;inset:0;background:rgba(2,6,23,.6);display:none;align-items:center;justify-content:center;z-index:90;padding:16px}.scc-modal.open{display:flex}
.scc-modal-box{background:var(--surface);border:1px solid var(--border);border-radius:18px;max-width:560px;width:100%;padding:20px;max-height:90vh;overflow:auto}
.scc-busy{opacity:.55;pointer-events:none}
@media(max-width:800px){.scc-term{height:360px}.scc-kv{grid-template-columns:1fr}}
</style>
<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › Serververwaltung › Server Control Center</div>
<div class="pulse-hero"><div class="pulse-hero-grid"><div><div class="pulse-kicker">Serververwaltung</div><div class="pulse-title">Server Control Center</div>
<div class="pulse-sub">Live-Werte, Container-Logs, kontrollierter Neustart, Deployment mit Backup und Rollback, verifizierte Sicherungen und Diagnose. Keine Root-Shell – nur freigegebene Aktionen mit Bestätigung und Audit-Log.</div></div>
<div class="pulse-hero-box"><div class="label">Stand</div><div class="value" id="scc-hero-state" style="font-size:18px">wird geladen …</div><div class="label" id="scc-hero-at" style="margin-top:6px"></div></div></div></div>
%%BANNER%%
<div class="scc-tabs" role="tablist" aria-label="Bereiche des Server Control Centers">
 <button class="scc-tab" role="tab" data-tab="overview" aria-selected="true">Übersicht</button>
 <button class="scc-tab" role="tab" data-tab="health" aria-selected="false">Live-Health</button>
 <button class="scc-tab" role="tab" data-tab="console" aria-selected="false">Live-Konsole</button>
 <button class="scc-tab" role="tab" data-tab="container" aria-selected="false">Container</button>
 <button class="scc-tab" role="tab" data-tab="deploy" aria-selected="false">Deployment</button>
 <button class="scc-tab" role="tab" data-tab="backups" aria-selected="false">Backups</button>
 <button class="scc-tab" role="tab" data-tab="diagnose" aria-selected="false">Diagnose</button>
</div>
<div id="scc-job" class="scc-banner" hidden></div>

<section class="scc-panel" data-panel="overview" role="tabpanel">
 <div id="scc-overview" class="scc-grid"><div class="pulse-card"><div class="pulse-card-b">Werte werden geladen …</div></div></div>
</section>

<section class="scc-panel" data-panel="health" role="tabpanel" hidden>
 <div class="pulse-card" style="margin-bottom:12px"><div class="pulse-card-h"><div class="pulse-section-title">Live-Health <span id="scc-health-state"></span></div><div class="flex gap-2" style="align-items:center"><span class="text-xs text-slate-400" id="scc-health-at"></span><button class="pulse-btn ghost" type="button" data-refresh="health">Aktualisieren</button></div></div><div class="pulse-card-b" id="scc-health-checks">Werte werden geladen …</div></div>
 <div id="scc-health" class="scc-grid"></div>
</section>
<section class="scc-panel pulse-card" data-panel="console" role="tabpanel" hidden><div class="pulse-card-b">
 <div class="scc-toolbar">
  <span id="scc-conn"><span class="scc-dot"></span>getrennt</span>
  <select id="scc-source" class="pulse-input" aria-label="Quelle"><option value="auto">Quelle: automatisch</option><option value="container">Container-Logs</option><option value="app">Pulse-Anwendungsprotokoll</option></select>
  <button id="scc-pause" class="pulse-btn ghost" type="button">⏸ Pause</button>
  <label class="pulse-check"><input id="scc-follow" type="checkbox" checked> Auto-Follow</label>
  <label class="pulse-check"><input id="scc-stamps" type="checkbox" checked> Zeitstempel</label>
  <select id="scc-level" class="pulse-input" aria-label="Filter"><option value="">Alle Stufen</option><option value="error">Nur Fehler</option><option value="warning">Warnungen + Fehler</option><option value="info">Nur Info</option></select>
  <input id="scc-search" class="pulse-input" placeholder="Suchen …" aria-label="Logs durchsuchen">
  <button id="scc-copy" class="pulse-btn ghost" type="button">Kopieren</button>
  <button id="scc-download" class="pulse-btn ghost" type="button">Download</button>
  <span id="scc-count" class="pulse-pill">0 Zeilen</span>
 </div>
 <div id="scc-term" class="scc-term" tabindex="0" aria-label="Live-Protokoll" aria-live="off"></div>
 <div id="scc-newlines" class="pulse-pill warn" hidden style="margin-top:8px;cursor:pointer">Neue Zeilen – zum Ende springen</div>
 <form id="scc-cmd-form" class="flex gap-2" style="margin-top:12px" autocomplete="off">
  <input id="scc-cmd" class="pulse-input" maxlength="20" placeholder="Befehl, z. B. help" aria-label="Konsolenbefehl" list="scc-cmd-list">
  <datalist id="scc-cmd-list"></datalist>
  <button class="pulse-btn primary" type="submit" id="scc-cmd-run">Ausführen</button>
  <button class="pulse-btn ghost" type="button" id="scc-cmd-abort" disabled>Abbrechen</button>
 </form>
 <div class="text-xs text-slate-400" style="margin-top:6px">Erlaubt: %%COMMANDS%%. ↑/↓ blättert durch den Verlauf dieser Sitzung.</div>
 <pre id="scc-cmd-out" class="scc-out" aria-live="polite">Bereit.</pre>
</div></section>

<section class="scc-panel" data-panel="container" role="tabpanel" hidden>
 <div class="pulse-two"><div class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">Container</div><button class="pulse-btn ghost" type="button" data-refresh="container">Aktualisieren</button></div><div class="pulse-card-b" id="scc-container">Wird geladen …</div></div>
 <div class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">Aktionen</div></div><div class="pulse-card-b">
  <p class="text-xs text-slate-400">Der Neustart läuft über den Host-Dienst (<code>docker compose restart</code>) mit anschließendem wiederholtem Healthcheck. Das Dashboard ist währenddessen kurz nicht erreichbar und verbindet sich automatisch neu.</p>
  <div class="flex flex-wrap gap-2" style="margin-top:10px"><button class="pulse-btn bad" type="button" data-action="restart">↻ Kontrolliert neu starten</button><button class="pulse-btn ghost" type="button" data-action="healthcheck">♥ Healthcheck wiederholen</button></div>
  <div id="scc-container-jobs" style="margin-top:14px"></div></div></div></div>
</section>

<section class="scc-panel" data-panel="deploy" role="tabpanel" hidden>
 <div class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">Lokal vs. GitHub</div><div class="flex gap-2"><button class="pulse-btn ghost" type="button" id="scc-fetch">Mit GitHub abgleichen</button><button class="pulse-btn ghost" type="button" data-refresh="deploy">Aktualisieren</button></div></div><div class="pulse-card-b" id="scc-deploy">Wird geladen …</div></div>
 <div class="pulse-card" style="margin-top:14px"><div class="pulse-card-h"><div class="pulse-section-title">Rollback (nur Code)</div></div><div class="pulse-card-b" id="scc-rollback">Wird geladen …</div></div>
</section>

<section class="scc-panel" data-panel="backups" role="tabpanel" hidden>
 <div class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">Komplett-Backups</div><div class="flex gap-2"><button class="pulse-btn primary" type="button" data-action="backup">Backup erstellen + verifizieren</button><button class="pulse-btn ghost" type="button" data-refresh="backups">Aktualisieren</button></div></div><div class="pulse-card-b" id="scc-backups">Wird geladen …</div></div>
</section>

<section class="scc-panel" data-panel="diagnose" role="tabpanel" hidden>
 <div class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">Diagnose</div><button class="pulse-btn ghost" type="button" data-refresh="diagnose">Neu prüfen</button></div><div class="pulse-card-b" id="scc-diagnose">Wird geladen …</div></div>
</section>

<div id="scc-modal" class="scc-modal" role="dialog" aria-modal="true" aria-labelledby="scc-modal-title"><div class="scc-modal-box">
 <div class="pulse-section-title" id="scc-modal-title"></div><div id="scc-modal-body" class="text-xs" style="margin-top:10px;line-height:1.6"></div>
 <label id="scc-modal-confirm-wrap" class="text-xs font-semibold" style="display:block;margin-top:12px">Zur Bestätigung <code id="scc-modal-word"></code> eingeben<input id="scc-modal-confirm" class="pulse-input" autocomplete="off" style="margin-top:6px"></label>
 <div class="flex gap-2" style="margin-top:16px;justify-content:flex-end"><button class="pulse-btn ghost" type="button" id="scc-modal-cancel">Abbrechen</button><button class="pulse-btn bad" type="button" id="scc-modal-ok">Ausführen</button></div>
</div></div>

<script>
(()=>{
const IS_ADMIN=%%IS_ADMIN%%, AGENT=%%AGENT%%, MAX_LINES=2000, $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const na=(v,u='')=>v===null||v===undefined||v===''?'<span class="scc-na">nicht verfügbar</span>':esc(v)+u;
const when=s=>{if(!s)return'<span class="scc-na">nicht verfügbar</span>';const d=new Date(s);return isNaN(d)?esc(s):d.toLocaleString('de-DE');};
const dur=s=>{if(s==null)return'<span class="scc-na">nicht verfügbar</span>';const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return (d?d+' T ':'')+h+' h '+m+' min';};
async function api(url,opt={}){const r=await fetch(url,Object.assign({credentials:'same-origin',cache:'no-store'},opt));let d={};try{d=await r.json();}catch(_){d={detail:'Ungültige Antwort (HTTP '+r.status+')'};}if(!r.ok&&!d.message)d.message=d.detail||('HTTP '+r.status);d._status=r.status;return d;}
const post=(url,body)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
const card=(title,inner)=>`<div class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">${title}</div></div><div class="pulse-card-b"><dl class="scc-kv">${inner}</dl></div></div>`;
const kv=(k,v)=>`<dt>${esc(k)}</dt><dd>${v}</dd>`;
const pill=(t,k)=>`<span class="pulse-pill ${k||''}">${esc(t)}</span>`;
const unavailable=d=>`<div class="scc-banner warn">Nicht verfügbar: ${esc(d&&d.message||'unbekannter Grund')}</div>`;
let active='overview',timers={},jobId=null,jobTimer=null,lastDeploy=null;

// ---------------- tabs
function show(tab){active=tab;document.querySelectorAll('.scc-tab').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===tab)));
 document.querySelectorAll('.scc-panel').forEach(p=>p.hidden=p.dataset.panel!==tab);history.replaceState(null,'','#'+tab);load(tab);
 if(tab==='console')connect();}
document.querySelectorAll('.scc-tab').forEach(b=>b.addEventListener('click',()=>show(b.dataset.tab)));
document.querySelectorAll('[data-refresh]').forEach(b=>b.addEventListener('click',()=>load(b.dataset.refresh)));
function schedule(){clearInterval(timers.poll);timers.poll=setInterval(()=>{if(document.visibilityState!=='visible')return;if(active==='overview')load('overview');if(active==='container')load('container');},20000);
 clearInterval(timers.health);timers.health=setInterval(()=>{if(document.visibilityState==='visible'&&active==='health')load('health');},10000);}

async function load(tab){try{
 if(tab==='overview')return renderOverview(await api('/admin/server/api/overview'));
 if(tab==='container')return renderContainer(await api('/admin/server/api/container'));
 if(tab==='deploy')return renderDeploy(await api('/admin/server/api/deploy'));
 if(tab==='backups')return renderBackups(await api('/admin/server/api/backups'));
 if(tab==='diagnose')return renderDiagnose(await api('/admin/server/api/diagnose'));
 if(tab==='health')return renderHealth(await api('/admin/server/api/health'));
}catch(e){if(tab==='health'){$('scc-health-state').innerHTML=pill('nicht erreichbar','bad');$('scc-health-checks').innerHTML='<span class="scc-na">Keine aktuellen Werte – ältere Befunde wurden ausgeblendet.</span>';}const el=$('scc-'+(tab==='overview'?'overview':tab));if(el)el.innerHTML=`<div class="scc-banner bad">Laden fehlgeschlagen: ${esc(e.message)} – das Dashboard ist evtl. gerade nicht erreichbar (Neustart?).</div>`;}}

// ---------------- overview
function renderOverview(d){if(!d.ok){$('scc-overview').innerHTML=unavailable(d);return;}
 const a=d.app,m=a.memory,disk=a.disk,ag=d.agent||{},c=ag.container||{},r=ag.resources||{},h=ag.host||{},cmp=d.compare||{};
 const agentNA=ag.available?'':'<span class="scc-na">nicht verfügbar</span>';
 $('scc-hero-state').textContent=a.bot_ready&&a.database_ok?'Pulse läuft':'Störung';$('scc-hero-at').textContent='Stand '+new Date(d.at).toLocaleTimeString('de-DE');
 const up=cmp.up_to_date===true?pill('aktuell','good'):cmp.up_to_date===false?pill('GitHub ist neuer','warn'):pill('Vergleich nicht verfügbar');
 $('scc-overview').innerHTML=[
  card('Pulse',kv('Version',na(a.version))+kv('Commit',a.commit&&a.commit.short?esc(a.commit.short)+(a.commit.branch?' · '+esc(a.commit.branch):''):na(null))+kv('Prozess läuft seit',dur(a.process_uptime_seconds))+kv('GitHub main',cmp.github&&cmp.github.available?esc(cmp.github.short)+' '+up:'<span class="scc-na">nicht verfügbar</span> '+esc(cmp.github&&cmp.github.message||''))),
  card('Discord & Datenbank',kv('Bot',a.bot_ready?pill('verbunden','good'):pill('nicht bereit','bad'))+kv('Server',na(a.guild_name))+kv('Mitglieder',na(a.member_count))+kv('Gateway-Latenz',na(a.gateway_latency_ms,' ms'))+kv('Datenbank',a.database_ok?pill('erreichbar','good'):pill('Fehler','bad'))),
  card('Arbeitsspeicher',kv('Container (cgroup)',na(m.container_mb,' MB'))+kv('Limit',na(m.limit_mb,' MB'))+kv('Pulse-Prozess',na(m.process_mb,' MB'))+kv('Spitze',na(m.peak_mb,' MB'))+kv('Host frei',ag.available&&h.memory?na(h.memory.available_mb,' MB'):agentNA)),
  card('Speicher & Last',kv('Datenträger frei',na(disk.free_gb,' GB'))+kv('Belegt',na(disk.used_percent,' %'))+kv('Host-Last (1/5/15)',ag.available&&h.load?esc(h.load.join(' / ')):agentNA)+kv('CPU Container',ag.available?na(r.cpu):agentNA)+kv('Host-Uptime',ag.available?dur(h.uptime_seconds):agentNA)),
  card('Container',kv('Status',ag.available?na(c.status):agentNA)+kv('Health',ag.available?na(c.health):agentNA)+kv('Gestartet',ag.available?when(c.started_at):agentNA)+kv('Neustarts',ag.available?na(c.restart_count):agentNA)+kv('RAM laut Docker',ag.available?na(r.memory):agentNA)),
  card('Backups & Jobs',kv('Komplett-Backups',na(a.backup.count))+kv('Neuestes',a.backup.latest?esc(a.backup.latest)+' ('+esc(a.backup.latest_age_hours)+' h)':na(null))+kv('Restore-Test',a.backup.restore_test&&a.backup.restore_test.at?(a.backup.restore_test.ok?pill('bestanden','good'):pill('fehlgeschlagen','bad')):na(null))+kv('Fehlerhafte Jobs',a.jobs_failing.length?esc(a.jobs_failing.join(', ')):pill('keine','good')))
 ].join('')+(ag.available?'':`<div class="scc-banner warn" style="grid-column:1/-1">Host-Werte nicht verfügbar: ${esc(ag.message)}</div>`);}

// ---------------- jobs (shared by container/deploy/backups)
function renderJob(job){const box=$('scc-job');if(!job){box.hidden=true;return;}
 const st=job.status,k=st==='succeeded'?'good':st==='running'?'warn':'bad';box.className='scc-banner '+k;box.hidden=false;
 const label={running:'läuft',succeeded:'erfolgreich',failed:'fehlgeschlagen',cancelled:'abgebrochen'}[st]||st;
 const cancel=st==='running'&&job.cancellable?`<button class="pulse-btn ghost" type="button" id="scc-job-cancel" style="margin-left:8px">Abbrechen</button>`:'';
 box.innerHTML=`<strong>Vorgang ${esc(job.action)}:</strong> ${esc(label)} · Phase ${esc(job.phase)}${cancel}<div>${esc((job.result||{}).message||'')}</div>`+
  `<details ${st!=='succeeded'?'open':''} style="margin-top:6px"><summary>Ausgabe (${(job.lines||[]).length} Zeilen)</summary><pre class="scc-out">${esc((job.lines||[]).join('\n'))}</pre></details>`;
 const b=$('scc-job-cancel');if(b)b.onclick=()=>confirmAction('cancel',{job_id:job.id});
 document.querySelectorAll('[data-action]').forEach(x=>x.classList.toggle('scc-busy',st==='running'));}
async function pollJob(id){jobId=id;clearTimeout(jobTimer);let failures=0;
 const tick=async()=>{const d=await api('/admin/server/api/jobs/'+id).catch(()=>({ok:false,message:'Verbindung unterbrochen'}));
  if(d.ok){failures=0;renderJob(d.job);if(d.job.status==='running'){jobTimer=setTimeout(tick,2500);}else{jobId=null;load(active);}}
  else{failures++;$('scc-job').hidden=false;$('scc-job').className='scc-banner warn';$('scc-job').innerHTML='Status vorübergehend nicht abrufbar ('+esc(d.message)+') – neuer Versuch … ('+failures+')';
   if(failures<120)jobTimer=setTimeout(tick,Math.min(2500*failures,10000));}};
 tick();}

// ---------------- confirmation + actions
const ACTIONS={
 restart:{title:'Container kontrolliert neu starten?',word:'NEUSTART',body:'Pulse (Bot und Dashboard) ist für etwa 1–3 Minuten nicht erreichbar. Danach wird der Healthcheck mehrfach wiederholt. Laufende Sprach-Supports werden getrennt.'},
 healthcheck:{title:'Healthcheck wiederholen?',word:'',body:'Fragt /healthz dreimal hintereinander ab. Verändert nichts.'},
 backup:{title:'Komplett-Backup erstellen?',word:'',body:'Sichert alle Datenbanken, JSON-Dateien, Uploads und die Discord-Struktur und prüft danach jede Prüfsumme. Erfolg wird erst nach bestandener Verifikation gemeldet.'},
 deploy:{title:'Deployment starten?',word:'DEPLOY'},
 rollback:{title:'Code-Rollback starten?',word:'ROLLBACK'},
 cancel:{title:'Vorgang abbrechen?',word:'',body:'Ein Abbruch ist nur möglich, solange noch nichts verändert wurde (vor Abschluss des Backups).'}};
let pending=null;
function confirmAction(action,extra){const a=ACTIONS[action];if(!a)return;let body=a.body||'';
 if(action==='deploy'){const g=lastDeploy&&lastDeploy.git;if(!g||!g.available||!g.origin){toastOut('Bitte zuerst den Tab Deployment laden und mit GitHub abgleichen.');show('deploy');return;}
  if(!g.safe){toastOut('Deploy gesperrt: '+(g.reasons||[]).join(' '));show('deploy');return;}
  extra={expected_sha:g.origin.sha};body=`Installiert: <code>${esc(g.head&&g.head.short)}</code> → GitHub main <code>${esc(g.origin.short)}</code> (${(g.pending||[]).length} Commit(s), ${(g.files||[]).length} Datei(en)).<br>Ablauf: verifiziertes Backup → nur Fast-Forward → Build → wiederholter Healthcheck → bei Fehler automatischer Rollback. Daten werden nicht überschrieben.`;}
 if(action==='rollback'){body=`Code wird auf <code>${esc(extra.short)}</code> zurückgesetzt („${esc(extra.subject)}“). Vorher verifiziertes Backup, danach Build und Healthcheck. <strong>Daten werden nicht zurückgesetzt</strong> – Datenbank-Migrationen der neueren Version bleiben bestehen. Beim nächsten Boot-Update/Deploy wird wieder GitHub main installiert.`;extra={target_sha:extra.sha};}
 pending={action,extra};$('scc-modal-title').textContent=a.title;$('scc-modal-body').innerHTML=body;
 $('scc-modal-confirm-wrap').style.display=a.word?'block':'none';$('scc-modal-word').textContent=a.word;$('scc-modal-confirm').value='';
 $('scc-modal').classList.add('open');(a.word?$('scc-modal-confirm'):$('scc-modal-ok')).focus();}
function closeModal(){$('scc-modal').classList.remove('open');pending=null;}
$('scc-modal-cancel').onclick=closeModal;$('scc-modal').addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});
$('scc-modal-ok').onclick=async()=>{if(!pending)return;const {action,extra}=pending,a=ACTIONS[action],ok=$('scc-modal-ok');
 if(a.word&&$('scc-modal-confirm').value.trim()!==a.word){$('scc-modal-confirm').focus();return;}
 ok.disabled=true;ok.textContent='Wird gestartet …';
 try{let d;if(action==='cancel')d=await post('/admin/server/api/jobs/'+extra.job_id+'/cancel',{});
  else d=await post('/admin/server/api/actions',Object.assign({action,confirm:a.word},extra||{}));
  closeModal();if(!d.ok){toastOut('Nicht gestartet: '+(d.message||d.detail));}else{if(d.job){renderJob(d.job);pollJob(d.job.id);}}}
 finally{ok.disabled=false;ok.textContent='Ausführen';}};
document.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',()=>{if(b.classList.contains('scc-busy'))return;confirmAction(b.dataset.action);}));
function toastOut(t){const o=$('scc-cmd-out');o.textContent=t;const box=$('scc-job');box.hidden=false;box.className='scc-banner bad';box.textContent=t;}

// ---------------- container
function renderContainer(d){const s=d.status||{},el=$('scc-container');
 if(!s.available){el.innerHTML=unavailable(s);}else{const c=s.container||{},r=s.resources||{};
  el.innerHTML='<dl class="scc-kv">'+kv('Name',na(c.name))+kv('Status',na(c.status))+kv('Health',na(c.health))+kv('Gestartet',when(c.started_at))+kv('Neustarts',na(c.restart_count))+
  kv('OOM beendet',c.oom_killed?pill('ja','bad'):pill('nein','good'))+kv('Exit-Code',na(c.exit_code))+kv('CPU',na(r.cpu))+kv('RAM',na(r.memory))+kv('RAM %',na(r.memory_percent))+kv('Netz I/O',na(r.net_io))+kv('Block I/O',na(r.block_io))+kv('Prozesse',na(r.pids))+
  kv('Start-/Laufzeitfehler',c.error?esc(c.error):pill('keiner','good'))+kv('Letzter Healthcheck',c.last_health_output?'<code>'+esc(c.last_health_output)+'</code>':na(null))+'</dl>'+
  ((s.errors||[]).length?`<div class="scc-banner warn">${esc(s.errors.join(' · '))}</div>`:'');}
 const j=d.jobs||{},h=(j.history||[]).slice().reverse();
 $('scc-container-jobs').innerHTML=j.available?(h.length?'<div class="pulse-section-title" style="font-size:12px">Letzte Vorgänge</div><table class="scc-table">'+h.map(x=>`<tr><td>${esc(x.action)}</td><td>${esc(x.status)}</td><td>${when(x.finished_at||x.started_at)}</td><td>${esc((x.result||{}).message||'')}</td></tr>`).join('')+'</table>':'<div class="text-xs text-slate-400">Noch keine Vorgänge.</div>'):unavailable(j);
 if(j.available&&j.job&&j.job.status==='running'&&jobId!==j.job.id)pollJob(j.job.id);
 if(d.local_job&&jobId!==d.local_job.id)pollJob(d.local_job.id);}

// ---------------- deploy
function renderDeploy(d){lastDeploy=d;const g=d.git||{},el=$('scc-deploy');
 const local=d.installed_local,gh=d.github||{};
 let head=`<dl class="scc-kv">${kv('Installiert (lokal gelesen)',local?'<code>'+esc(local.short)+'</code>'+(local.branch?' · '+esc(local.branch):''):na(null))}${kv('GitHub main (API)',gh.available?'<code>'+esc(gh.short)+'</code> '+esc(gh.subject||''):'<span class="scc-na">nicht verfügbar</span> '+esc(gh.message||''))}</dl>`;
 if(!g.available){el.innerHTML=head+unavailable(g)+'<div class="scc-banner warn">Ohne Host-Verwaltungsdienst ist kein Deploy aus dem Dashboard möglich. Das wird nie als Erfolg gemeldet.</div>';$('scc-rollback').innerHTML=unavailable(g);return;}
 const reasons=(g.reasons||[]).map(r=>'<li>'+esc(r)+'</li>').join('');
 const changes=(g.local_changes||[]).map(x=>'<li><code>'+esc(x)+'</code></li>').join('');
 const pending=(g.pending||[]).map(c=>`<tr><td><code>${esc(c.short)}</code></td><td>${esc(c.subject)}</td><td>${esc(c.author)}</td><td>${when(c.date)}</td></tr>`).join('');
 const files=(g.files||[]).map(f=>`<tr><td><code>${esc(f.path)}</code></td><td style="color:#12b76a">+${esc(f.added)}</td><td style="color:#f04438">-${esc(f.deleted)}</td></tr>`).join('');
 const canDeploy=g.safe&&g.origin&&g.head&&g.origin.sha!==g.head.sha&&g.fast_forward;
 el.innerHTML=head+`<dl class="scc-kv">${kv('Installierter Commit',g.head?'<code>'+esc(g.head.short)+'</code> '+esc(g.head.subject)+' · '+when(g.head.date):na(null))}${kv('Branch',g.branch===''?'detached HEAD':na(g.branch))}${kv('origin/main (letzter Abgleich)',g.origin?'<code>'+esc(g.origin.short)+'</code> '+esc(g.origin.subject):na(null))}${kv('Letzter Abgleich',when(g.last_fetch_at))}${kv('Rückstand / Vorsprung',g.behind==null?na(null):esc(g.behind)+' / '+esc(g.ahead))}${kv('Git-Zustand',g.safe?pill('sicher','good'):pill('unsicher – Deploy gesperrt','bad'))}</dl>`+
  (reasons?`<div class="scc-banner bad"><strong>Gründe:</strong><ul style="margin:6px 0 0 18px;list-style:disc">${reasons}</ul></div>`:'')+
  (changes?`<details open style="margin-top:10px"><summary>Lokale Änderungen auf dem Server (${esc(g.local_changes_total)})</summary><ul style="margin-left:18px">${changes}</ul></details>`:'')+
  `<div class="pulse-section-title" style="font-size:12px;margin-top:14px">Änderungsvorschau (${(g.pending||[]).length} Commits, ${(g.files||[]).length} Dateien)</div>`+
  (pending?`<table class="scc-table">${pending}</table><details style="margin-top:8px"><summary>Geänderte Dateien</summary><table class="scc-table">${files}</table></details>`:'<div class="text-xs text-slate-400">Keine neuen Commits gegenüber dem letzten Abgleich.</div>')+
  `<div class="flex gap-2" style="margin-top:14px"><button class="pulse-btn primary" type="button" id="scc-deploy-btn" ${canDeploy?'':'disabled'}>⇪ Deploy starten (Backup → Fast-Forward → Build → Healthcheck)</button></div>`;
 const btn=$('scc-deploy-btn');if(btn)btn.onclick=()=>confirmAction('deploy');
 const cands=g.rollback_candidates||[];
 $('scc-rollback').innerHTML=`<p class="text-xs text-slate-400">Setzt nur den Code auf einen früheren Commit des installierten Verlaufs zurück (vorher verifiziertes Backup, danach Build und Healthcheck; ist der alte Stand nicht gesund, wird automatisch der vorherige wiederhergestellt). Daten bleiben unverändert.</p>`+
  (cands.length?`<table class="scc-table">${cands.map((c,i)=>`<tr><td><code>${esc(c.short)}</code></td><td>${esc(c.subject)}</td><td>${when(c.date)}</td><td><button class="pulse-btn ghost" type="button" data-rollback="${i}" ${g.safe||!(g.reasons||[]).some(r=>!r.startsWith('GitHub main ist kein')&&!r.startsWith('origin/main unbekannt'))?'':'disabled'}>Zurücksetzen</button></td></tr>`).join('')}</table>`:'<div class="text-xs text-slate-400">Keine früheren Commits gefunden.</div>');
 document.querySelectorAll('[data-rollback]').forEach(b=>b.onclick=()=>confirmAction('rollback',cands[Number(b.dataset.rollback)]));
 const j=d.jobs||{};if(j.available&&j.job&&j.job.status==='running'&&jobId!==j.job.id)pollJob(j.job.id);}
$('scc-fetch').onclick=async()=>{const b=$('scc-fetch');b.disabled=true;b.textContent='Gleiche ab …';
 try{const d=await post('/admin/server/api/deploy/fetch',{});if(!d.ok)toastOut('Abgleich fehlgeschlagen: '+(d.message||d.detail));await load('deploy');}
 finally{b.disabled=false;b.textContent='Mit GitHub abgleichen';}};

// ---------------- backups
function renderBackups(d){const el=$('scc-backups');if(!d.ok){el.innerHTML=unavailable(d);return;}
 const s=d.status||{},t=s.restore_test||{};
 const rows=(d.backups||[]).map(b=>{const v=b.verified;const vs=v?(v.ok?pill('verifiziert','good'):pill('fehlerhaft','bad')):pill('nicht geprüft');
  return `<tr><td><code>${esc(b.name)}</code><div class="text-xs text-slate-400">${esc(b.kind)} · ${esc(b.size_mb)} MB · ${when(b.created)}</div>${v&&!v.ok?'<div class="text-xs" style="color:#f04438">'+esc(v.problems.join('; '))+'</div>':''}</td><td>${vs}</td>`+
  `<td><div class="flex flex-wrap gap-2"><button class="pulse-btn ghost" type="button" data-verify="${esc(b.name)}">Prüfen</button>${IS_ADMIN?`<a class="pulse-btn ghost" href="/backup/full/download/${encodeURIComponent(b.name)}">ZIP</a><a class="pulse-btn ghost" href="/backup/full/preview/${encodeURIComponent(b.name)}">Vorschau / Wiederherstellen</a>`:''}</div></td></tr>`;}).join('');
 el.innerHTML=`<dl class="scc-kv">${kv('Anzahl',na(s.count))}${kv('Gesamtgröße',na(s.total_mb,' MB')+(s.total_ok===false?' '+pill('über Grenze','warn'):''))}${kv('Datenträger frei',na(d.disk&&d.disk.free_gb,' GB'))}${kv('Letzter Restore-Test',t.at?when(t.at)+' '+(t.ok?pill('bestanden','good'):pill('fehlgeschlagen','bad')):na(null))}</dl>`+
  `<p class="text-xs text-slate-400" style="margin-top:8px">Wiederherstellen läuft über die vorhandene Probelauf-Vorschau (mit Sicherheitskopie des aktuellen Stands und Bestätigung WIEDERHERSTELLEN)${IS_ADMIN?'':' und ist Administratoren vorbehalten'}. Das Server Control Center löscht keine Backups; die einzige geprüfte Sicherung lässt sich auch unter /backups nicht löschen.</p>`+
  (rows?`<table class="scc-table">${rows}</table>`:'<div class="scc-banner warn">Noch kein Komplett-Backup vorhanden.</div>');
 document.querySelectorAll('[data-verify]').forEach(b=>b.onclick=async()=>{b.disabled=true;b.textContent='Prüft …';const r=await post('/admin/server/api/backups/verify',{filename:b.dataset.verify});if(!r.ok)toastOut('Prüfung fehlgeschlagen: '+(r.message||r.detail));load('backups');});}

// ---------------- diagnose
// ---------------- live health
const SEV={bad:['Fehler','bad'],warn:['Warnung','warn'],info:['Hinweis',''],ok:['ok','good']};
const JOBSTATE={ok:['läuft','good'],failing:['fehlgeschlagen','bad'],overdue:['überfällig','warn'],paused:['pausiert','warn'],waiting:['wartet','']};
function bar(v,max,warn,crit){if(v==null||!max)return'';const p=Math.max(2,Math.min(100,100*v/max));const k=v>=crit?'bad':v>=warn?'warn':'';return `<div class="scc-bar ${k}" role="img" aria-label="${Math.round(p)} % von ${esc(max)} MB"><i style="width:${p}%"></i></div>`;}
function renderHealth(d){const el=$('scc-health');if(!d.ok){el.innerHTML=unavailable(d);$('scc-health-checks').innerHTML='';return;}
 const st={ok:['Alles in Ordnung','good'],warn:['Auffällig','warn'],bad:['Störung','bad']}[d.status]||['unbekannt',''];
 $('scc-health-state').innerHTML=pill(st[0],st[1]);$('scc-health-at').textContent='Stand '+new Date(d.at).toLocaleTimeString('de-DE')+' · alle 10 s';
 const ch=d.checks||[];$('scc-health-checks').innerHTML=ch.length?ch.map(c=>`<div class="scc-finding ${esc(c.severity)}"><div class="flex flex-wrap gap-2" style="align-items:center">${pill((SEV[c.severity]||SEV.info)[0],(SEV[c.severity]||SEV.info)[1])}<strong>${esc(c.component)}</strong><span class="text-xs">${esc(c.text)}</span></div></div>`).join(''):'<div class="scc-banner good">Keine Auffälligkeiten bei RAM, Scheduler, Discord-Limits und Datenbank.</div>';
 const m=d.memory||{},s=d.scheduler||{},lag=s.lag||{},cn=s.counts||{},r=d.discord||{},c=d.connections||{},db=d.databases||{};const rss=m.process_mb??m.container_mb;
 const cap=m.limit_mb||m.emergency_mb||m.critical_mb;const ms=v=>v==null?null:Math.round(v*1000);
 const jobs=(s.jobs||[]).map(j=>{const js=JOBSTATE[j.state]||[j.state,''];return `<tr><td>${esc(j.name)}</td><td>${pill(js[0],js[1])}</td><td>${j.age_seconds==null?na(null):dur(j.age_seconds)}</td><td>${esc(j.runs)} / ${esc(j.failures)}</td><td>${j.last_error?'<code>'+esc(j.last_error)+'</code>':''}</td></tr>`;}).join('');
 const files=(db.files||[]).map(f=>`<tr><td>${esc(f.name)}</td><td>${esc(f.size_mb)} MB</td><td>${f.wal_mb>=64?pill(f.wal_mb+' MB','warn'):esc(f.wal_mb)+' MB'}</td></tr>`).join('');
 const routes=(r.top_routes||[]).map(x=>`<tr><td><code>${esc(x.route)}</code></td><td>${esc(x.count)}</td></tr>`).join('');
 el.innerHTML=[
  card('Arbeitsspeicher',kv('Pulse-Prozess',na(rss,' MB')+bar(rss,cap,m.warn_mb,m.critical_mb))+kv('Ziel',na(m.target_mb,' MB'))+kv('Spitze',na(m.peak_mb,' MB'))+kv('Warn / kritisch',esc(m.warn_mb)+' / '+esc(m.critical_mb)+' MB')+kv('Container-Limit',na(m.limit_mb,' MB'))+kv('Threads',na(m.threads))),
  card('Discord-Rate-Limits',kv('429 in '+esc(r.window_minutes)+' min',r.recent?pill(r.recent,r.recent>=20?'warn':''):pill('0','good'))+kv('Global (Fenster)',r.recent_global?pill(r.recent_global,'bad'):pill('0','good'))+kv('Abgebrochen (zu lang)',na(r.recent_raised))+kv('Seit Start',esc(r.total??0)+' · global '+esc(r.global_total??0))+kv('Gateway-Sendelimit',r.ws_ratelimited==null?na(null):r.ws_ratelimited?pill('aktiv','warn'):pill('frei','good'))+kv('Zuletzt',r.last?when(r.last.at)+' · <code>'+esc(r.last.route)+'</code> ('+esc(r.last.retry_after)+' s)':pill('nie','good'))),
  card('Datenbank-Verbindungen',kv('Jetzt offen',(c.open||0)>=20?pill(c.open,'warn'):esc(c.open??0))+kv('Spitze',na(c.peak))+kv('Geöffnet seit Start',na(c.opened_total))+kv('Vom GC geschlossen (Leck)',c.closed_by_gc?pill(c.closed_by_gc,'warn'):pill('0','good'))),
  card('Scheduler & Event-Loop',kv('Jobs',esc(cn.ok||0)+' ok · '+esc(cn.failing||0)+' Fehler · '+esc(cn.overdue||0)+' überfällig · '+esc(cn.paused||0)+' pausiert')+kv('Loop-Verzögerung',na(ms(lag.last),' ms'))+kv('Maximum',na(ms(lag.max),' ms')+(lag.max_at?' · '+when(lag.max_at):''))+kv('Verzögerungs-Warnungen',na(lag.warnings))),
  `<div class="pulse-card" style="grid-column:1/-1"><div class="pulse-card-h"><div class="pulse-section-title">Hintergrundjobs</div></div><div class="pulse-card-b scc-scroll">${jobs?`<table class="scc-table"><tr><th>Job</th><th>Status</th><th>Letzter Start vor</th><th>Läufe / Fehler</th><th>Letzter Fehler</th></tr>${jobs}</table>`:'<div class="text-xs text-slate-400">Noch kein Hintergrundjob gelaufen.</div>'}</div></div>`,
  `<div class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">Datenbanken · ${esc(db.total_mb??0)} MB</div></div><div class="pulse-card-b scc-scroll">${files?`<table class="scc-table"><tr><th>Datei</th><th>Größe</th><th>WAL</th></tr>${files}</table>`:'<div class="text-xs text-slate-400">Keine Datenbankdateien gefunden.</div>'}</div></div>`,
  `<div class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">Meistbegrenzte Routen</div></div><div class="pulse-card-b scc-scroll">${routes?`<table class="scc-table"><tr><th>Route</th><th>429</th></tr>${routes}</table>`:'<div class="text-xs text-slate-400">Keine Rate-Limits im aktuellen Fenster.</div>'}</div></div>`,
 ].join('');}
function renderDiagnose(d){const el=$('scc-diagnose');if(!d.ok){el.innerHTML=unavailable(d);return;}
 const f=d.findings||[];el.innerHTML=(f.length?f.map(x=>`<div class="scc-finding ${esc(x.severity)}"><div class="flex flex-wrap gap-2" style="align-items:center">${pill(x.severity==='bad'?'Fehler':x.severity==='warn'?'Warnung':'Hinweis',x.severity==='info'?'':x.severity)}<strong>${esc(x.component)}</strong>${pill(x.confirmed?'bestätigt (gemessen)':'Vermutung',x.confirmed?'good':'warn')}<span class="text-xs text-slate-400">${when(x.at)}</span></div>`+
  `<div style="margin-top:6px;font-size:12px">${esc(x.cause)}</div>${x.evidence?`<div class="text-xs text-slate-400" style="margin-top:4px"><code>${esc(x.evidence)}</code></div>`:''}<div class="text-xs" style="margin-top:6px"><strong>Empfehlung:</strong> ${esc(x.recommendation)}</div></div>`).join(''):'<div class="scc-banner good">Keine Auffälligkeiten gefunden.</div>')+`<div class="text-xs text-slate-400">Geprüft ${when(d.at)}</div>`;}

// ---------------- live console
const term=$('scc-term');let buf=[],es=null,paused=false,retry=0,reconnectTimer=null,cursor='',newWhilePaused=0;
function setConn(state,text){$('scc-conn').innerHTML=`<span class="scc-dot ${state}"></span>${esc(text)}`;}
function lineVisible(l){const lvl=$('scc-level').value,q=$('scc-search').value.trim().toLowerCase();
 if(lvl==='error'&&l.level!=='error')return false;if(lvl==='warning'&&l.level==='info')return false;if(lvl==='info'&&l.level!=='info')return false;
 return !q||l.text.toLowerCase().includes(q);}
function lineHtml(l){const q=$('scc-search').value.trim();let t=esc(l.text);if(q){const r=new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi');t=t.replace(r,m=>'<mark>'+m+'</mark>');}
 return `<div class="l-${l.level}">${$('scc-stamps').checked&&l.ts?'<span class="ts">'+esc(String(l.ts).replace('T',' ').slice(0,19))+'</span>':''}${t}</div>`;}
function atBottom(){return term.scrollHeight-term.scrollTop-term.clientHeight<40;}
function render(){const follow=$('scc-follow').checked;const keep=term.scrollTop;
 const vis=buf.filter(lineVisible);term.innerHTML=vis.map(lineHtml).join('')||'<div class="scc-na">Noch keine Zeilen.</div>';
 $('scc-count').textContent=vis.length+(vis.length!==buf.length?' / '+buf.length:'')+' Zeilen';
 if(follow){term.scrollTop=term.scrollHeight;$('scc-newlines').hidden=true;}else{term.scrollTop=keep;}}
function append(lines){buf.push(...lines);if(buf.length>MAX_LINES)buf=buf.slice(-MAX_LINES);
 if(paused){newWhilePaused+=lines.length;$('scc-pause').textContent='▶ Fortsetzen ('+newWhilePaused+' neu)';return;}
 const wasBottom=atBottom();const vis=lines.filter(lineVisible);
 if(buf.length>=MAX_LINES||!vis.length){if(vis.length)render(false);return;}
 if(term.firstChild&&term.firstChild.classList&&term.firstChild.classList.contains('scc-na'))term.innerHTML='';
 term.insertAdjacentHTML('beforeend',vis.map(lineHtml).join(''));while(term.childElementCount>MAX_LINES)term.firstChild.remove();
 $('scc-count').textContent=buf.filter(lineVisible).length+' Zeilen';
 if($('scc-follow').checked&&wasBottom)term.scrollTop=term.scrollHeight;else $('scc-newlines').hidden=false;}
function connect(){if(es)return;clearTimeout(reconnectTimer);setConn('wait','verbinde …');
 const src=$('scc-source').value;es=new EventSource('/admin/server/api/logs/stream?source='+encodeURIComponent(src)+(cursor?'&since='+encodeURIComponent(cursor):''));
 es.addEventListener('status',e=>{const d=JSON.parse(e.data);if(d.state==='connected'){retry=0;if(!buf.length)render();setConn('on','verbunden · '+(d.source==='container'?'Container-Logs':'Anwendungsprotokoll'));}
  if(d.state==='expired'){disconnect();reconnectTimer=setTimeout(connect,500);}});
 es.addEventListener('lines',e=>{const d=JSON.parse(e.data);if(e.lastEventId)cursor=e.lastEventId;append(d.lines||[]);});
 es.addEventListener('source_error',e=>{const d=JSON.parse(e.data);setConn('wait','Quelle gestört: '+d.message);});
 es.onerror=()=>{disconnect();retry++;const wait=Math.min(30000,1000*2**Math.min(retry,5));setConn('off','getrennt – neuer Versuch in '+Math.round(wait/1000)+' s');reconnectTimer=setTimeout(connect,wait);};}
function disconnect(){if(es){es.close();es=null;}}
$('scc-source').onchange=()=>{disconnect();cursor='';buf=[];render(true);connect();};
$('scc-pause').onclick=()=>{paused=!paused;$('scc-pause').textContent=paused?'▶ Fortsetzen':'⏸ Pause';if(!paused){newWhilePaused=0;render(true);}};
['scc-level','scc-stamps'].forEach(id=>$(id).addEventListener('change',()=>render(true)));
let st;$('scc-search').addEventListener('input',()=>{clearTimeout(st);st=setTimeout(()=>render(true),200);});
$('scc-follow').onchange=()=>{if($('scc-follow').checked){term.scrollTop=term.scrollHeight;$('scc-newlines').hidden=true;}};
$('scc-newlines').onclick=()=>{term.scrollTop=term.scrollHeight;$('scc-newlines').hidden=true;};
term.addEventListener('scroll',()=>{if(atBottom())$('scc-newlines').hidden=true;});
const plain=()=>buf.filter(lineVisible).map(l=>($('scc-stamps').checked&&l.ts?l.ts+' ':'')+l.text).join('\n');
$('scc-copy').onclick=async()=>{try{await navigator.clipboard.writeText(plain());$('scc-copy').textContent='Kopiert ✓';}catch(_){$('scc-copy').textContent='Kopieren nicht erlaubt';}setTimeout(()=>$('scc-copy').textContent='Kopieren',1500);};
$('scc-download').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([plain()],{type:'text/plain'}));a.download='pulse-log-'+new Date().toISOString().replace(/[:.]/g,'-')+'.txt';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);};
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'&&active!=='console'){disconnect();}});
window.addEventListener('beforeunload',disconnect);

// command line with session history and abort
const hist=[];let hpos=0,ctl=null;
$('scc-cmd-list').innerHTML='%%COMMANDS%%'.split(', ').map(c=>`<option value="${c}">`).join('');
$('scc-cmd').addEventListener('keydown',e=>{if(e.key==='ArrowUp'&&hist.length){hpos=Math.max(0,hpos-1);$('scc-cmd').value=hist[hpos];e.preventDefault();}
 if(e.key==='ArrowDown'&&hist.length){hpos=Math.min(hist.length,hpos+1);$('scc-cmd').value=hist[hpos]||'';e.preventDefault();}});
$('scc-cmd-abort').onclick=()=>{if(ctl)ctl.abort();};
$('scc-cmd-form').addEventListener('submit',async e=>{e.preventDefault();const v=$('scc-cmd').value.trim();if(!v||ctl)return;
 if(hist[hist.length-1]!==v)hist.push(v);if(hist.length>50)hist.shift();hpos=hist.length;
 const out=$('scc-cmd-out');out.textContent='> '+v+'\nwird ausgeführt …';ctl=new AbortController();$('scc-cmd-run').disabled=true;$('scc-cmd-abort').disabled=false;
 try{const d=await api('/admin/server/api/console',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:v}),signal:ctl.signal});
  if(d.client==='clear'){buf=[];render(true);out.textContent='Ausgabe geleert.';}
  else out.textContent='> '+v+'\n'+(d.output||d.message||'')+(d.ok?'':'\n[nicht erfolgreich]');
  if(d.requires_confirmation)confirmAction(d.action,d.job_id?{job_id:d.job_id}:undefined);
  $('scc-cmd').value='';}
 catch(err){out.textContent='> '+v+'\n'+(err.name==='AbortError'?'Abgebrochen (Anzeige; ein bereits gestarteter Lesebefehl verändert nichts).':'Fehler: '+err.message);}
 finally{ctl=null;$('scc-cmd-run').disabled=false;$('scc-cmd-abort').disabled=true;}});

// ---------------- start
const TABS=['overview','health','console','container','deploy','backups','diagnose'];
const fromHash=()=>{const t=(location.hash||'#overview').slice(1);return TABS.includes(t)?t:'overview';};
window.addEventListener('hashchange',()=>{const t=fromHash();if(t!==active)show(t);});
show(fromHash());if(fromHash()!=='overview')load('overview');schedule();
api('/admin/server/api/container').then(d=>{const j=d.jobs||{};if(j.available&&j.job&&j.job.status==='running')pollJob(j.job.id);if(d.local_job)pollJob(d.local_job.id);}).catch(()=>{});
})();
</script>
"""
