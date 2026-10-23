#!/usr/bin/env python3
"""Pulse Host-Verwaltungsdienst (Server Control Center) – kleiner, eingeschränkter Dienst auf dem VPS.

Läuft als systemd-Dienst auf dem Host (nicht im öffentlich erreichbaren Webcontainer) und bietet dem
Pulse-Dashboard eine **feste Aktionsliste** über HTTP mit Token-Auth an:

    GET  /v1/ping                 Erreichbarkeit + Agent-Version
    GET  /v1/status               Container-Status, Ressourcen, Host-RAM/Last/Speicher
    GET  /v1/logs?tail=&since=    Container-Logs (docker logs, begrenzt, Geheimnisse geschwärzt)
    GET  /v1/git                  Installierter Commit, lokale Änderungen, Vergleich mit origin/main
    POST /v1/git/fetch            git fetch origin main
    POST /v1/jobs                 {"action": deploy|restart|rollback|healthcheck, ...}
    GET  /v1/jobs/current         laufender bzw. letzter Vorgang
    GET  /v1/jobs/<id>            ein Vorgang
    POST /v1/jobs/<id>/cancel     abbrechen, solange noch nichts verändert wurde

Sicherheitsregeln:
* Kein Shell-Aufruf: jeder Prozess bekommt eine feste Argumentliste (`shell=False`), Parameter werden
  vorher streng validiert (Commit-SHA = 40 Hex-Zeichen und aus einer festen Kandidatenliste).
* Deploy und Rollback verwenden die vorhandenen Werkzeuge `deploy/remote_deploy.sh` (Backup, Ancestor-
  Prüfung, Healthcheck, automatischer Rollback) bzw. `deploy/rollback_code.sh`; es gibt kein zweites
  Deploy-System. Daten (untracked/git-ignored) werden nie überschrieben.
* Bindet nur an Loopback/private Adressen (Standard: Gateway des Compose-Netzes 172.30.0.1).
* Höchstens ein verändernder Vorgang gleichzeitig; alle Puffer sind begrenzt.
* Antworten und Job-Ausgaben werden auf Tokens/Passwörter geprüft und geschwärzt.

Nur Python-Standardbibliothek (Ubuntu-Python 3.10+), keine weiteren Abhängigkeiten.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

AGENT_VERSION = "1.0.0"
ACTIONS = ("deploy", "restart", "rollback", "healthcheck")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
ALLOWED_UNTRACKED = ("assets/support_wait.mp3",)  # wie scripts/pulse-vps-boot-update.sh
MAX_JOB_LINES = 600
MAX_LOG_TAIL = 1000
MAX_LINE = 2000
MAX_BODY = 8192
MAX_HISTORY = 20

# Markers printed by deploy/remote_deploy.sh and deploy/rollback_code.sh – after them the code is being changed.
POINT_OF_NO_RETURN = ("Backup vor dem Deploy erstellt.", "Backup vor dem Rollback erstellt.")

_SECRET_PATTERNS = (
    (re.compile(r"[MNO][A-Za-z\d_-]{23,28}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,}"), "[DISCORD-TOKEN ENTFERNT]"),
    (re.compile(r"(?i)\b(bearer|bot)\s+[A-Za-z0-9._~+/=-]{20,}"), r"\1 [ENTFERNT]"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|WEBHOOK)[A-Z0-9_]*)\s*([=:])\s*\S+"),
     r"\1\2[ENTFERNT]"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[ZUGANG ENTFERNT]@"),
    (re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\S+"), "[WEBHOOK ENTFERNT]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[PRIVATER SCHLÜSSEL ENTFERNT]"),
)


def redact(text, extra_secrets=()) -> str:
    """Remove anything that looks like a credential. Applied to every line that leaves the agent."""
    out = str(text if text is not None else "")
    for secret in extra_secrets:
        if secret and len(secret) >= 8:
            out = out.replace(secret, "[ENTFERNT]")
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out[:MAX_LINE]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConfigError(ValueError):
    pass


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.token = str(env.get("PULSE_AGENT_TOKEN", "")).strip()
        if len(self.token) < 32:
            raise ConfigError("PULSE_AGENT_TOKEN fehlt oder ist kürzer als 32 Zeichen.")
        # Preferred: a Unix socket inside the bind-mounted Pulse folder – no network port, no firewall change.
        self.socket = str(env.get("PULSE_AGENT_SOCKET", "")).strip()
        if self.socket and not os.path.isabs(self.socket):
            raise ConfigError("PULSE_AGENT_SOCKET muss ein absoluter Pfad sein.")
        self.bind = str(env.get("PULSE_AGENT_BIND", "172.30.0.1")).strip()
        try:
            address = ipaddress.ip_address(self.bind)
        except ValueError as exc:
            raise ConfigError("PULSE_AGENT_BIND muss eine IP-Adresse sein.") from exc
        if address.is_unspecified or not (address.is_loopback or address.is_private):
            raise ConfigError("PULSE_AGENT_BIND muss eine Loopback- oder private Adresse sein (nie 0.0.0.0/öffentlich).")
        self.port = int(env.get("PULSE_AGENT_PORT", "25096"))
        if not 1024 <= self.port <= 65535:
            raise ConfigError("PULSE_AGENT_PORT muss zwischen 1024 und 65535 liegen.")
        self.repo = Path(str(env.get("PULSE_REPO_DIR", "/opt/pulse/Pulse"))).resolve()
        self.container = str(env.get("PULSE_CONTAINER", "pulse-teamos"))
        self.service = str(env.get("PULSE_COMPOSE_SERVICE", "pulse"))
        for value in (self.container, self.service):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", value):
                raise ConfigError("Ungültiger Container-/Dienstname.")
        self.health_url = str(env.get("PULSE_HEALTH_URL", "http://127.0.0.1:25095/healthz"))
        parsed = urlparse(self.health_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ConfigError("PULSE_HEALTH_URL muss auf http://127.0.0.1 zeigen.")
        self.state_file = Path(str(env.get("PULSE_AGENT_STATE", "/var/lib/pulse-host-agent/state.json")))
        self.deploy_health_seconds = max(60, min(int(env.get("PULSE_DEPLOY_HEALTH_SECONDS", "240")), 900))


def _child_env(extra=None) -> dict:
    """Minimal environment for child processes – the agent token is never passed on."""
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "USER", "DOCKER_HOST") if key in os.environ}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra or {})
    return env


class Runner:
    """Executes fixed argument lists. Replaced by a fake in the tests."""

    def run(self, args, cwd=None, timeout=30, env=None):
        if not isinstance(args, (list, tuple)) or not all(isinstance(a, str) for a in args):
            raise TypeError("Argumente müssen eine Liste aus Strings sein.")
        try:
            proc = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, timeout=timeout,
                                  env=_child_env(env), shell=False, check=False, errors="replace")
        except subprocess.TimeoutExpired:
            return 124, "", "Zeitüberschreitung"
        except FileNotFoundError:
            return 127, "", f"{args[0]} nicht gefunden"
        return proc.returncode, proc.stdout, proc.stderr

    def stream(self, args, cwd=None, env=None, on_line=None, cancel=None, can_cancel=None, timeout=1800):
        """Run a long command, hand every output line to `on_line`. Returns the exit code."""
        try:
            proc = subprocess.Popen(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    env=_child_env(env), shell=False, bufsize=1, errors="replace")
        except FileNotFoundError:
            if on_line:
                on_line(f"{args[0]} nicht gefunden")
            return 127
        deadline = time.monotonic() + timeout
        watcher_stop = threading.Event()

        def watch():
            while not watcher_stop.wait(0.5):
                if time.monotonic() > deadline:
                    proc.kill()
                    return
                if cancel is not None and cancel.is_set() and (can_cancel is None or can_cancel()):
                    proc.terminate()
                    return

        threading.Thread(target=watch, daemon=True).start()
        try:
            for line in proc.stdout or ():
                if on_line:
                    on_line(line.rstrip("\n"))
            return proc.wait()
        finally:
            watcher_stop.set()


class Agent:
    def __init__(self, config: Config, runner: Runner | None = None, http_get=None, sleep=time.sleep, clock=time.monotonic):
        self.config = config
        self.runner = runner or Runner()
        self.http_get = http_get or self._http_get
        self.sleep = sleep
        self.clock = clock
        self.lock = threading.Lock()
        self.current = None
        self.history = deque(maxlen=MAX_HISTORY)
        self.state = self._load_state()
        self._status_cache = (0.0, None)

    # ------------------------------------------------------------------ state
    def _load_state(self) -> dict:
        try:
            data = json.loads(self.config.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for job in data.get("history", [])[-MAX_HISTORY:]:
                    if isinstance(job, dict):
                        self.history.append(job)
                return data
        except (OSError, ValueError):
            pass
        return {}

    def _save_state(self) -> None:
        self.state["history"] = list(self.history)
        try:
            self.config.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.config.state_file)
        except OSError as exc:
            print(f"Zustand nicht gespeichert: {type(exc).__name__}", file=sys.stderr)

    # ------------------------------------------------------------------ helpers
    def _git(self, *args, timeout=30):
        return self.runner.run(["git", "-c", f"safe.directory={self.config.repo}", *args], cwd=str(self.config.repo), timeout=timeout)

    def _clean(self, text) -> str:
        return redact(text, (self.config.token,))

    def _http_get(self, url, timeout=6):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 – fixed loopback URL
                return response.status, response.read(4096).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(4096).decode("utf-8", "replace") if exc.fp else ""
        except (OSError, ValueError) as exc:
            return 0, type(exc).__name__

    def health_once(self) -> dict:
        status, body = self.http_get(self.config.health_url)
        data = {}
        try:
            data = json.loads(body) if body and body.startswith("{") else {}
        except ValueError:
            data = {}
        healthy = status == 200 and bool(data.get("ok"))
        keys = ("ok", "version", "bot_ready", "guild_ready", "database_ok", "memory_mb", "gateway_latency_ms")
        return {"healthy": healthy, "http_status": status, "at": now_iso(),
                "details": {k: data.get(k) for k in keys if k in data},
                "error": "" if healthy else (f"HTTP {status}" if status else f"nicht erreichbar ({body[:60]})")}

    def wait_healthy(self, seconds: int, needed: int = 3, interval: float = 5.0, log=None, cancel=None) -> dict:
        """Repeated health check: `needed` consecutive successes within `seconds`."""
        streak, attempts, last = 0, 0, {}
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            if cancel is not None and cancel.is_set():
                return {"healthy": False, "attempts": attempts, "last": last, "cancelled": True}
            attempts += 1
            last = self.health_once()
            streak = streak + 1 if last["healthy"] else 0
            if log:
                log(f"Healthcheck {attempts}: {'OK' if last['healthy'] else 'nicht gesund – ' + last['error']} ({streak}/{needed})")
            if streak >= needed:
                return {"healthy": True, "attempts": attempts, "last": last}
            self.sleep(interval)
        return {"healthy": False, "attempts": attempts, "last": last}

    # ------------------------------------------------------------------ read-only information
    def status(self) -> dict:
        stamp, cached = self._status_cache
        if cached is not None and time.monotonic() - stamp < 5:
            return cached
        result = {"agent_version": AGENT_VERSION, "at": now_iso(), "container": None, "resources": None, "host": self._host(),
                  "errors": []}
        code, out, err = self.runner.run(
            ["docker", "inspect", "--format", "{{json .State}}\t{{.RestartCount}}\t{{.Created}}", self.config.container], timeout=10)
        if code == 0 and out.strip():
            try:
                state_raw, restarts, created = (out.strip().split("\t") + ["", ""])[:3]
                state = json.loads(state_raw)
                health = state.get("Health") or {}
                health_log = health.get("Log") or []
                result["container"] = {
                    "name": self.config.container, "status": state.get("Status"), "running": bool(state.get("Running")),
                    "restarting": bool(state.get("Restarting")), "oom_killed": bool(state.get("OOMKilled")),
                    "exit_code": state.get("ExitCode"), "error": self._clean(state.get("Error") or ""),
                    "started_at": state.get("StartedAt"), "finished_at": state.get("FinishedAt"),
                    "health": health.get("Status") or "kein Healthcheck", "failing_streak": health.get("FailingStreak"),
                    "last_health_output": self._clean((health_log[-1].get("Output") or "")[-300:]) if health_log else "",
                    "restart_count": int(restarts) if str(restarts).isdigit() else None, "created": created,
                }
            except (ValueError, AttributeError, TypeError):
                result["errors"].append("Containerstatus nicht lesbar")
        else:
            result["errors"].append(self._clean(f"docker inspect: {(err or out or 'kein Container').strip()[:200]}"))
        code, out, err = self.runner.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", self.config.container], timeout=15)
        if code == 0 and out.strip():
            try:
                stats = json.loads(out.strip().splitlines()[0])
                result["resources"] = {k: stats.get(v) for k, v in (("cpu", "CPUPerc"), ("memory", "MemUsage"), ("memory_percent", "MemPerc"),
                                                                     ("net_io", "NetIO"), ("block_io", "BlockIO"), ("pids", "PIDs"))}
            except (ValueError, IndexError):
                result["errors"].append("Ressourcenwerte nicht lesbar")
        else:
            result["errors"].append(self._clean(f"docker stats: {(err or 'nicht verfügbar').strip()[:200]}"))
        self._status_cache = (time.monotonic(), result)
        return result

    def _host(self) -> dict:
        host = {"load": None, "memory": None, "disk": None, "uptime_seconds": None, "cpus": os.cpu_count()}
        try:
            host["load"] = [float(x) for x in Path("/proc/loadavg").read_text().split()[:3]]
        except (OSError, ValueError):
            pass
        try:
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, value = line.partition(":")
                if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                    info[key] = int(value.split()[0]) // 1024
            host["memory"] = {"total_mb": info.get("MemTotal"), "available_mb": info.get("MemAvailable"),
                              "swap_total_mb": info.get("SwapTotal"), "swap_free_mb": info.get("SwapFree")}
        except (OSError, ValueError, IndexError):
            pass
        try:
            st = os.statvfs(self.config.repo)
            host["disk"] = {"total_gb": round(st.f_blocks * st.f_frsize / 1024 ** 3, 1),
                            "free_gb": round(st.f_bavail * st.f_frsize / 1024 ** 3, 1)}
        except (OSError, AttributeError):
            pass
        try:
            host["uptime_seconds"] = int(float(Path("/proc/uptime").read_text().split()[0]))
        except (OSError, ValueError, IndexError):
            pass
        return host

    def logs(self, tail=200, since=None) -> dict:
        try:
            tail = max(1, min(int(tail), MAX_LOG_TAIL))
        except (TypeError, ValueError):
            raise ValueError("tail muss eine Zahl sein") from None
        args = ["docker", "logs", "--timestamps", "--tail", str(tail)]
        if since not in (None, ""):
            try:
                since_value = float(since)
            except (TypeError, ValueError):
                raise ValueError("since muss ein Unix-Zeitstempel sein") from None
            if not 0 < since_value < 10 ** 11:
                raise ValueError("since außerhalb des gültigen Bereichs")
            args += ["--since", f"{since_value:.6f}"]
        args.append(self.config.container)
        code, out, err = self.runner.run(args, timeout=15)
        if code != 0:
            return {"ok": False, "lines": [], "error": self._clean((err or "docker logs fehlgeschlagen").strip()[:200])}
        # docker writes the container's stdout/stderr to our stdout/stderr – merge and order by timestamp.
        lines = []
        for raw in (out + "\n" + err).splitlines():
            if not raw.strip():
                continue
            stamp, _, text = raw.partition(" ")
            if not re.match(r"^\d{4}-\d\d-\d\dT", stamp):
                stamp, text = "", raw
            lines.append({"ts": stamp, "text": self._clean(text)})
        lines.sort(key=lambda item: item["ts"])
        return {"ok": True, "lines": lines[-tail:]}

    def git_info(self, fetch=False) -> dict:
        repo = self.config.repo
        info = {"at": now_iso(), "repo_ok": (repo / ".git").exists(), "safe": False, "reasons": [], "fetched": False, "fetch_error": ""}
        if not info["repo_ok"]:
            info["reasons"].append("Kein Git-Repository unter dem konfigurierten Pfad.")
            return info
        if fetch:
            code, _, err = self._git("fetch", "--quiet", "origin", "main", timeout=90)
            info["fetched"] = code == 0
            if code != 0:
                info["fetch_error"] = self._clean((err or "git fetch fehlgeschlagen").strip()[:240])
        code, out, _ = self._git("branch", "--show-current")
        info["branch"] = out.strip() if code == 0 else None
        code, out, _ = self._git("log", "-1", "--format=%H%x1f%s%x1f%cI")
        if code == 0 and out.strip():
            sha, subject, date = (out.strip().split("\x1f") + ["", ""])[:3]
            info["head"] = {"sha": sha, "short": sha[:7], "subject": self._clean(subject)[:200], "date": date}
        else:
            info["head"] = None
            info["reasons"].append("Installierter Commit nicht lesbar.")
        code, out, _ = self._git("status", "--porcelain=v1", "--untracked-files=normal")
        changes = [line for line in out.splitlines() if line.strip()] if code == 0 else []
        unexpected = [line for line in changes if not (line.startswith("?? ") and line[3:] in ALLOWED_UNTRACKED)]
        info["local_changes"] = [self._clean(line)[:200] for line in unexpected[:50]]
        info["local_changes_total"] = len(unexpected)
        if code != 0:
            info["reasons"].append("git status fehlgeschlagen – Zustand unbekannt.")
        if unexpected:
            info["reasons"].append(f"{len(unexpected)} unerwartete lokale Änderung(en) – werden nie überschrieben.")
        git_dir = repo / ".git"
        for marker, label in (("MERGE_HEAD", "Merge"), ("rebase-merge", "Rebase"), ("rebase-apply", "Rebase"), ("CHERRY_PICK_HEAD", "Cherry-Pick")):
            if (git_dir / marker).exists():
                info["reasons"].append(f"Unvollständiger {label} im Repository.")
        if info["branch"] not in ("main", ""):
            info["reasons"].append(f"Checkout liegt auf Branch '{info['branch']}', erwartet wird main.")
        code, out, _ = self._git("rev-parse", "--verify", "--quiet", "origin/main")
        origin = out.strip() if code == 0 and SHA_RE.match(out.strip()) else None
        info["origin"] = None
        if origin:
            code, out, _ = self._git("log", "-1", "--format=%s%x1f%cI", origin)
            subject, date = ((out.strip().split("\x1f") + [""])[:2]) if code == 0 else ("", "")
            info["origin"] = {"sha": origin, "short": origin[:7], "subject": self._clean(subject)[:200], "date": date}
        else:
            info["reasons"].append("origin/main unbekannt – bitte zuerst mit GitHub abgleichen.")
        try:
            info["last_fetch_at"] = datetime.fromtimestamp((git_dir / "FETCH_HEAD").stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        except OSError:
            info["last_fetch_at"] = None
        head = (info.get("head") or {}).get("sha")
        info["ahead"] = info["behind"] = None
        info["fast_forward"] = False
        info["pending"], info["files"] = [], []
        if head and origin:
            code, out, _ = self._git("rev-list", "--left-right", "--count", f"{head}...{origin}")
            if code == 0 and len(out.split()) == 2:
                info["ahead"], info["behind"] = (int(x) for x in out.split())
            code, _, _ = self._git("merge-base", "--is-ancestor", head, origin)
            info["fast_forward"] = code == 0
            if code != 0 and head != origin:
                info["reasons"].append("GitHub main ist kein Fast-Forward vom installierten Stand.")
            code, out, _ = self._git("log", "--format=%H%x1f%s%x1f%an%x1f%cI", "-n", "50", f"{head}..{origin}")
            for line in out.splitlines() if code == 0 else []:
                sha, subject, author, date = (line.split("\x1f") + ["", "", ""])[:4]
                info["pending"].append({"sha": sha, "short": sha[:7], "subject": self._clean(subject)[:200], "author": self._clean(author)[:80], "date": date})
            code, out, _ = self._git("diff", "--numstat", head, origin)
            for line in (out.splitlines() if code == 0 else [])[:150]:
                parts = line.split("\t")
                if len(parts) == 3:
                    info["files"].append({"added": parts[0], "deleted": parts[1], "path": self._clean(parts[2])[:200]})
        info["rollback_candidates"] = self.rollback_candidates()
        info["safe"] = not info["reasons"]
        return info

    def rollback_candidates(self) -> list:
        code, out, _ = self._git("log", "--first-parent", "--skip=1", "-n", "15", "--format=%H%x1f%s%x1f%cI", "HEAD")
        result = []
        for line in out.splitlines() if code == 0 else []:
            sha, subject, date = (line.split("\x1f") + ["", ""])[:3]
            if SHA_RE.match(sha):
                result.append({"sha": sha, "short": sha[:7], "subject": self._clean(subject)[:200], "date": date})
        return result

    # ------------------------------------------------------------------ jobs
    def public_job(self, job, lines=True) -> dict | None:
        if job is None:
            return None
        data = {k: v for k, v in job.items() if k not in ("lines", "_cancel", "_point_of_no_return")}
        data["cancellable"] = job.get("status") == "running" and not job.get("_point_of_no_return") and job.get("action") != "restart"
        if lines:
            data["lines"] = list(job.get("lines", []))
        return data

    def find_job(self, job_id):
        if self.current and self.current["id"] == job_id:
            return self.current
        for job in self.history:
            if job.get("id") == job_id:
                return job
        return None

    def start_job(self, action, params, actor="") -> dict:
        if action not in ACTIONS:
            raise ValueError("Unbekannte Aktion.")
        params = params if isinstance(params, dict) else {}
        if action == "deploy":
            sha = str(params.get("expected_sha", ""))
            if not SHA_RE.match(sha):
                raise ValueError("expected_sha muss eine vollständige Commit-SHA sein.")
            params = {"expected_sha": sha}
        elif action == "rollback":
            sha = str(params.get("target_sha", ""))
            if not SHA_RE.match(sha):
                raise ValueError("target_sha muss eine vollständige Commit-SHA sein.")
            if sha not in {c["sha"] for c in self.rollback_candidates()}:
                raise ValueError("Dieser Commit ist kein zulässiges Rollback-Ziel (nur frühere Commits des installierten Verlaufs).")
            params = {"target_sha": sha}
        else:
            params = {}
        actor = re.sub(r"[^\w .@#-]", "", str(actor))[:80]
        with self.lock:
            if self.current is not None and self.current.get("status") == "running":
                raise RuntimeError(f"Es läuft bereits ein Vorgang ({self.current['action']}).")
            job = {"id": uuid.uuid4().hex[:12], "action": action, "params": params, "actor": actor, "status": "running",
                   "phase": "vorbereitung", "started_at": now_iso(), "finished_at": None, "result": {},
                   "lines": deque(maxlen=MAX_JOB_LINES), "_cancel": threading.Event(), "_point_of_no_return": False}
            self.current = job
        threading.Thread(target=self._run_job, args=(job,), daemon=True, name=f"pulse-agent-{action}").start()
        return self.public_job(job)

    def cancel_job(self, job_id) -> dict:
        job = self.find_job(job_id)
        if job is None:
            raise LookupError("Vorgang nicht gefunden.")
        if job.get("status") != "running":
            raise RuntimeError("Der Vorgang läuft nicht mehr.")
        if job.get("_point_of_no_return") or job["action"] == "restart":
            raise RuntimeError("Abbruch nicht mehr möglich: Der Code bzw. Container wird bereits verändert.")
        job["_cancel"].set()
        self._log(job, "Abbruch angefordert …")
        return self.public_job(job)

    def _log(self, job, line) -> None:
        text = self._clean(line)
        job["lines"].append(f"{datetime.now().strftime('%H:%M:%S')} {text}")
        if any(marker in text for marker in POINT_OF_NO_RETURN):
            job["_point_of_no_return"] = True
            job["phase"] = "build & start"
        elif "Rollback auf" in text and job["action"] == "deploy":
            job["phase"] = "automatischer rollback"

    def _finish(self, job, status, message, **extra) -> None:
        job["finished_at"] = now_iso()
        job["phase"] = "abgeschlossen"
        job["result"] = dict(extra, message=message)
        self._log(job, ("✔ " if status == "succeeded" else "✖ ") + message)
        summary = self.public_job(job, lines=False)
        summary.update(status=status, cancellable=False, lines=list(job["lines"])[-80:])
        with self.lock:
            self.history.append(summary)
            if job["action"] in ("deploy", "rollback") and extra.get("previous_commit"):
                self.state["last_code_change"] = {"action": job["action"], "previous": extra.get("previous_commit"),
                                                  "installed": extra.get("installed_commit"), "at": job["finished_at"], "status": status}
            self._save_state()
            job["status"] = status  # last: whoever sees the final status also finds the saved state

    def _run_job(self, job) -> None:
        try:
            getattr(self, f"_job_{job['action']}")(job)
        except Exception as exc:  # the job must always end with a visible result
            self._finish(job, "failed", f"Interner Fehler im Host-Dienst: {type(exc).__name__}")
        finally:
            if job.get("status") == "running":
                self._finish(job, "failed", "Vorgang ohne Ergebnis beendet.")

    def _cancelled(self, job) -> bool:
        if job["_cancel"].is_set() and not job["_point_of_no_return"]:
            self._finish(job, "cancelled", "Vorgang abgebrochen – es wurde nichts verändert.")
            return True
        return False

    def _head(self) -> str:
        code, out, _ = self._git("rev-parse", "HEAD")
        return out.strip() if code == 0 else ""

    def _job_healthcheck(self, job) -> None:
        job["phase"] = "healthcheck"
        result = self.wait_healthy(120, log=lambda line: self._log(job, line), cancel=job["_cancel"])
        if result.get("cancelled"):
            self._finish(job, "cancelled", "Healthcheck abgebrochen.")
        elif result["healthy"]:
            self._finish(job, "succeeded", f"Pulse ist gesund ({result['attempts']} Prüfungen).", health=result["last"])
        else:
            self._finish(job, "failed", f"Pulse ist nicht gesund: {result['last'].get('error', 'unbekannt')}", health=result["last"])

    def _job_restart(self, job) -> None:
        job["phase"] = "neustart"
        job["_point_of_no_return"] = True
        self._log(job, f"docker compose restart {self.config.service}")
        code = self.runner.stream(["docker", "compose", "restart", self.config.service], cwd=str(self.config.repo),
                                  on_line=lambda line: self._log(job, line), timeout=240)
        if code != 0:
            status = self.status()
            container = status.get("container") or {}
            self._finish(job, "failed", f"Neustart fehlgeschlagen (Exit-Code {code}).", container_status=container.get("status"),
                         container_error=container.get("error", ""))
            return
        job["phase"] = "healthcheck"
        result = self.wait_healthy(self.config.deploy_health_seconds, log=lambda line: self._log(job, line))
        if result["healthy"]:
            self._finish(job, "succeeded", "Container neu gestartet und gesund.", health=result["last"])
        else:
            self._finish(job, "failed", "Container neu gestartet, aber der Healthcheck schlägt fehl: "
                         + result["last"].get("error", "unbekannt"), health=result["last"])

    def _preflight(self, job, fetch=True) -> dict | None:
        job["phase"] = "prüfung"
        self._log(job, "Prüfe Git-Zustand " + ("und gleiche mit GitHub ab …" if fetch else "…"))
        info = self.git_info(fetch=fetch)
        if fetch and not info["fetched"]:
            self._finish(job, "failed", "GitHub nicht erreichbar: " + (info["fetch_error"] or "git fetch fehlgeschlagen"))
            return None
        # A rollback does not need GitHub: only local changes, branch and unfinished git operations block it.
        ignore = () if job["action"] == "deploy" else ("GitHub main ist kein Fast-Forward", "origin/main unbekannt")
        blocking = [r for r in info["reasons"] if not r.startswith(ignore)] if ignore else list(info["reasons"])
        if blocking:
            self._finish(job, "failed", "Abbruch wegen unsicherem Git-Zustand: " + " ".join(blocking), git_reasons=blocking)
            return None
        return info

    def _job_deploy(self, job) -> None:
        target = job["params"]["expected_sha"]
        info = self._preflight(job)
        if info is None or self._cancelled(job):
            return
        origin = (info.get("origin") or {}).get("sha")
        head = (info.get("head") or {}).get("sha")
        if origin != target:
            self._finish(job, "failed", "GitHub main hat sich seit der Vorschau geändert – bitte Vorschau neu laden und erneut bestätigen.",
                         expected=target, origin=origin)
            return
        if head == target:
            self._finish(job, "succeeded", "Dieser Stand ist bereits installiert – nichts zu tun.", installed_commit=head)
            return
        if not info["fast_forward"]:
            self._finish(job, "failed", "Kein Fast-Forward möglich – Deploy abgebrochen.")
            return
        script = self.config.repo / "deploy" / "remote_deploy.sh"
        if not script.is_file():
            self._finish(job, "failed", "deploy/remote_deploy.sh fehlt im Repository.")
            return
        job["phase"] = "backup"
        self._log(job, f"Starte deploy/remote_deploy.sh {target[:7]} (Backup → Fast-Forward → Build → Healthcheck)")
        code = self.runner.stream(["bash", str(script), target, str(self.config.repo)], cwd=str(self.config.repo),
                                  env={"PULSE_DEPLOY_HEALTH_SECONDS": str(self.config.deploy_health_seconds)},
                                  on_line=lambda line: self._log(job, line), cancel=job["_cancel"],
                                  can_cancel=lambda: not job["_point_of_no_return"], timeout=1800)
        if job["_cancel"].is_set() and not job["_point_of_no_return"]:
            self._finish(job, "cancelled", "Deploy vor dem Backup-Abschluss abgebrochen – Code und Daten unverändert.")
            return
        installed = self._head()
        text = "\n".join(job["lines"])
        if code != 0:
            if "Rollback erfolgreich" in text:
                rollback = {"attempted": True, "ok": True}
            elif "Auch der Rollback ist nicht gesund" in text:
                rollback = {"attempted": True, "ok": False}
            else:
                rollback = {"attempted": "Rollback auf" in text, "ok": None}
            if not job["_point_of_no_return"]:
                message = f"Deploy vor jeder Änderung abgebrochen (Exit-Code {code}) – siehe Ausgabe."
            elif rollback["ok"]:
                message = f"Deploy fehlgeschlagen; automatischer Rollback auf {installed[:7]} ist gesund."
            elif rollback["ok"] is False:
                message = "Deploy fehlgeschlagen und auch der automatische Rollback ist NICHT gesund – sofort prüfen."
            else:
                message = f"Deploy fehlgeschlagen (Exit-Code {code}) – Zustand prüfen."
            self._finish(job, "failed", message, installed_commit=installed, previous_commit=head, target_commit=target,
                         exit_code=code, rollback=rollback)
            return
        job["phase"] = "healthcheck"
        result = self.wait_healthy(120, log=lambda line: self._log(job, line))
        if installed != target:
            self._finish(job, "failed", f"Skript meldete Erfolg, installiert ist aber {installed[:7]} statt {target[:7]}.",
                         installed_commit=installed, previous_commit=head, target_commit=target, health=result["last"])
        elif not result["healthy"]:
            self._finish(job, "failed", "Neuer Stand installiert, der wiederholte Healthcheck schlägt aber fehl: "
                         + result["last"].get("error", "unbekannt"), installed_commit=installed, previous_commit=head,
                         target_commit=target, health=result["last"])
        else:
            self._finish(job, "succeeded", f"Deploy erfolgreich: {target[:7]} installiert und gesund.", installed_commit=installed,
                         previous_commit=head, target_commit=target, health=result["last"])

    def _job_rollback(self, job) -> None:
        target = job["params"]["target_sha"]
        info = self._preflight(job, fetch=False)
        if info is None or self._cancelled(job):
            return
        head = (info.get("head") or {}).get("sha")
        script = self.config.repo / "deploy" / "rollback_code.sh"
        if not script.is_file():
            self._finish(job, "failed", "deploy/rollback_code.sh fehlt im Repository.")
            return
        job["phase"] = "backup"
        self._log(job, f"Starte deploy/rollback_code.sh {target[:7]} (Backup → Code zurücksetzen → Build → Healthcheck)")
        code = self.runner.stream(["bash", str(script), target, str(self.config.repo)], cwd=str(self.config.repo),
                                  env={"PULSE_DEPLOY_HEALTH_SECONDS": str(self.config.deploy_health_seconds)},
                                  on_line=lambda line: self._log(job, line), cancel=job["_cancel"],
                                  can_cancel=lambda: not job["_point_of_no_return"], timeout=1800)
        if job["_cancel"].is_set() and not job["_point_of_no_return"]:
            self._finish(job, "cancelled", "Rollback vor dem Backup-Abschluss abgebrochen – Code und Daten unverändert.")
            return
        installed = self._head()
        text = "\n".join(job["lines"])
        if code != 0:
            if "Vorheriger Stand wiederhergestellt" in text:
                message = f"Rollback nicht gesund – zurück auf {installed[:7]}, dieser Stand ist gesund."
            elif "KRITISCH" in text:
                message = "Rollback fehlgeschlagen und der vorherige Stand ist ebenfalls nicht gesund – sofort prüfen."
            else:
                message = f"Rollback fehlgeschlagen (Exit-Code {code}) – Code-Stand {installed[:7]}."
            self._finish(job, "failed", message, installed_commit=installed, previous_commit=head, target_commit=target, exit_code=code)
            return
        job["phase"] = "healthcheck"
        result = self.wait_healthy(120, log=lambda line: self._log(job, line))
        ok = installed == target and result["healthy"]
        self._finish(job, "succeeded" if ok else "failed",
                     f"Rollback auf {target[:7]} erfolgreich und gesund. Hinweis: Beim nächsten Boot-Update/Deploy wird wieder "
                     "GitHub main installiert." if ok else
                     f"Rollback-Skript beendet, aber installiert ist {installed[:7]} bzw. der Healthcheck schlägt fehl.",
                     installed_commit=installed, previous_commit=head, target_commit=target, health=result["last"])


# ---------------------------------------------------------------------- HTTP layer
class _RateLimit:
    def __init__(self, per_minute=600):
        self.per_minute, self.hits, self.lock = per_minute, deque(), threading.Lock()

    def hit(self) -> bool:
        now = time.monotonic()
        with self.lock:
            while self.hits and now - self.hits[0] > 60:
                self.hits.popleft()
            if len(self.hits) >= self.per_minute:
                return False
            self.hits.append(now)
            return True


def make_handler(agent: Agent):
    limiter = _RateLimit()

    class Handler(BaseHTTPRequestHandler):
        server_version = "PulseHostAgent/" + AGENT_VERSION
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002 – never log query strings or headers
            print(f"{self.address_string()} {self.command} {urlparse(self.path).path} {args[1] if len(args) > 1 else ''}", file=sys.stderr)

        def _send(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            return bool(supplied) and hmac.compare_digest(supplied.encode(), agent.config.token.encode())

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError("Anfrage zu groß.")
            if not length:
                return {}
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise ValueError("Nur JSON wird akzeptiert.")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON-Objekt erwartet.")
            return data

        def _guard(self) -> bool:
            if not limiter.hit():
                self._send(429, {"ok": False, "error": "Zu viele Anfragen."})
                return False
            if not self._authorized():
                self._send(401, {"ok": False, "error": "Nicht autorisiert."})
                return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/v1/ping":
                    return self._send(200, {"ok": True, "agent_version": AGENT_VERSION, "at": now_iso()})
                if url.path == "/v1/status":
                    return self._send(200, {"ok": True, **agent.status()})
                if url.path == "/v1/logs":
                    return self._send(200, agent.logs(query.get("tail", 200), query.get("since")))
                if url.path == "/v1/git":
                    return self._send(200, {"ok": True, **agent.git_info(fetch=False)})
                if url.path == "/v1/jobs/current":
                    job = agent.current or (agent.history[-1] if agent.history else None)
                    return self._send(200, {"ok": True, "job": agent.public_job(job), "last_code_change": agent.state.get("last_code_change"),
                                            "history": [agent.public_job(j, lines=False) for j in list(agent.history)[-10:]]})
                match = re.fullmatch(r"/v1/jobs/([0-9a-f]{12})", url.path)
                if match:
                    job = agent.find_job(match.group(1))
                    return self._send(200, {"ok": True, "job": agent.public_job(job)}) if job else self._send(404, {"ok": False, "error": "Vorgang nicht gefunden."})
                return self._send(404, {"ok": False, "error": "Unbekannter Endpunkt."})
            except ValueError as exc:
                return self._send(400, {"ok": False, "error": str(exc)[:200]})

        def do_POST(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            try:
                body = self._body()
                actor = self.headers.get("X-Pulse-Actor", "")
                if url.path == "/v1/git/fetch":
                    info = agent.git_info(fetch=True)
                    return self._send(200 if info["fetched"] else 502, {"ok": info["fetched"], **info})
                if url.path == "/v1/jobs":
                    job = agent.start_job(str(body.get("action", "")), body, actor)
                    print(f"Vorgang gestartet: {job['action']} von {job['actor'] or 'unbekannt'}", file=sys.stderr)
                    return self._send(202, {"ok": True, "job": job})
                match = re.fullmatch(r"/v1/jobs/([0-9a-f]{12})/cancel", url.path)
                if match:
                    return self._send(200, {"ok": True, "job": agent.cancel_job(match.group(1))})
                return self._send(404, {"ok": False, "error": "Unbekannter Endpunkt."})
            except LookupError as exc:
                return self._send(404, {"ok": False, "error": str(exc)})
            except RuntimeError as exc:
                return self._send(409, {"ok": False, "error": str(exc)})
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, {"ok": False, "error": str(exc)[:200]})

        def do_PUT(self):
            self._send(405, {"ok": False, "error": "Methode nicht erlaubt."})

        do_DELETE = do_PATCH = do_PUT

    return Handler


def main() -> int:
    try:
        config = Config()
    except (ConfigError, ValueError) as exc:
        print(f"Konfiguration ungültig: {exc}", file=sys.stderr)
        return 2
    agent = Agent(config)
    handler = make_handler(agent)
    if config.socket:
        server = _unix_server(config.socket, handler)
        where = f"Unix-Socket {config.socket}"
    else:
        server = ThreadingHTTPServer((config.bind, config.port), handler)
        where = f"{config.bind}:{config.port}"
    server.daemon_threads = True
    print(f"Pulse Host-Verwaltungsdienst {AGENT_VERSION} hört auf {where} (Repo {config.repo})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if config.socket:
            try:
                os.unlink(config.socket)
            except OSError:
                pass
    return 0


def _unix_server(path: str, handler):
    """HTTP over a Unix socket (mode 0660, folder 0750) – reachable only through the file system."""
    import socketserver

    class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            request, _ = super().get_request()
            return request, ("unix", 0)

    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o750, exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    old_umask = os.umask(0o117)
    try:
        server = UnixHTTPServer(path, handler)
    finally:
        os.umask(old_umask)
    os.chmod(path, 0o660)
    return server


if __name__ == "__main__":
    sys.exit(main())
