"""Pulse Ultimate TeamOS layer.

This module adds the next-generation management features on top of the existing
Pulse stack while keeping legacy routes and data stores intact.

Implemented domains:
- personal/team records, departments and onboarding status
- cases + Roblox player records + evidence metadata
- handovers, feedback and polls
- calendar events, awards and certificates
- workflow/automation definitions and execution
- dashboards, analytics and JSON APIs
- system diagnostics, branding and feature flags
- attachment metadata with local storage
"""
from __future__ import annotations

import asyncio
import contextvars
import gc
import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from collections import OrderedDict, deque

import discord
from pulse_embed_design import color as _pulse_color
from fastapi import Cookie, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import pulse_db as db
import pulse_sqlite
from pulse_safety import (
    RAM_CRITICAL_MB,
    RAM_CRITICAL_RECOVERY_MB,
    RAM_EMERGENCY_MB,
    RAM_EMERGENCY_RECOVERY_MB,
    RAM_LIMIT_MB,
    RAM_RECOVERY_MB,
    RAM_WARN_MB,
    memory_guard_level as _policy_memory_guard_level,
)
import pulse_ids as _pulse_ids  # A-09: fixed Discord IDs live in one place

BASE = Path(__file__).resolve().parent
ATTACHMENTS_DIR = BASE / "attachments"  # A-04: created where a file is stored (and in webserver.init_runtime), not on import
ULTIMATE_DB = BASE / "pulse.db"  # kept for compatibility; use ultimate_db() (D-05)


def ultimate_db() -> Path:
    """D-05: the same database file as pulse_db (honours PULSE_DB_PATH) – never a second pulse.db."""
    return Path(os.path.abspath(db.DB_PATH))


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
TEAM_UPDATE_CHANNEL_ID = _pulse_ids.TEAM_UPDATE_CHANNEL_ID

_PRIORITY = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
_CASE_STATUSES = ("open", "investigating", "waiting", "resolved", "closed")
_FEEDBACK_STATUS = ("new", "reviewing", "planned", "done", "rejected")
_POLL_TYPES = ("single", "multiple")

_INIT = False
_AUTOMATION_TASK: Optional[asyncio.Task] = None
_RAM_GUARD_LEVEL = 0
_RAM_SAMPLES: deque[tuple[float, float]] = deque(maxlen=180)
_RAM_LAST_TREND_ALERT_AT = 0.0
RAM_TREND_WINDOW_SECONDS = 3600.0
RAM_TREND_MIN_AGE_SECONDS = 1800.0
RAM_TREND_GROWTH_MB_PER_HOUR = 20.0
RAM_TREND_ALERT_COOLDOWN_SECONDS = 3600.0


def memory_guard_level(rss_mb: Optional[float], current_level: Optional[int] = None) -> int:
    """Measure threshold state with hysteresis after a warning has occurred."""
    previous = _RAM_GUARD_LEVEL if current_level is None else current_level
    return _policy_memory_guard_level(
        rss_mb,
        previous,
        warn_mb=RAM_WARN_MB,
        critical_mb=RAM_CRITICAL_MB,
        emergency_mb=RAM_EMERGENCY_MB,
        recovery_mb=RAM_RECOVERY_MB,
        critical_recovery_mb=RAM_CRITICAL_RECOVERY_MB,
        emergency_recovery_mb=RAM_EMERGENCY_RECOVERY_MB,
    )


def ram_trend_snapshot() -> dict:
    """Return a bounded summary of recent RSS slope for the live system monitor."""
    now_mono = time.monotonic()
    latest = _RAM_SAMPLES[-1] if _RAM_SAMPLES else None
    baseline = next(
        (sample for sample in _RAM_SAMPLES if now_mono - sample[0] >= RAM_TREND_MIN_AGE_SECONDS),
        None,
    )
    if latest is None or baseline is None:
        return {
            "samples": len(_RAM_SAMPLES),
            "growth_mb": None,
            "growth_mb_per_hour": None,
            "window_minutes": None,
        }
    elapsed = max(1.0, latest[0] - baseline[0])
    growth = latest[1] - baseline[1]
    return {
        "samples": len(_RAM_SAMPLES),
        "growth_mb": round(growth, 1),
        "growth_mb_per_hour": round(growth * 3600.0 / elapsed, 1),
        "window_minutes": round(elapsed / 60.0, 1),
    }


async def runtime_memory_guard(bot) -> None:
    """Monitor RAM transitions and slow growth using a bounded, low-RAM sample ring.

    Piggybacks on the existing one-minute automation loop; creates no extra task.
    Discord notifications are sent only when a threshold changes or a long-term
    growth trend is detected, and are optional via PULSE_SYSTEM_ALERT_CHANNEL_ID.
    """
    global _RAM_GUARD_LEVEL, _RAM_LAST_TREND_ALERT_AT
    try:
        # Bot-Hosting limits the whole container, not only the Python process.
        # Include child processes such as ffmpeg when cgroup accounting is available.
        rss = container_memory_mb()
        if rss is None:
            return
        now_mono = time.monotonic()
        _RAM_SAMPLES.append((now_mono, rss))
        while _RAM_SAMPLES and now_mono - _RAM_SAMPLES[0][0] > RAM_TREND_WINDOW_SECONDS:
            _RAM_SAMPLES.popleft()

        previous = _RAM_GUARD_LEVEL
        level = memory_guard_level(rss, current_level=previous)
        messages: list[str] = []
        if level >= 1:
            # Freed heap pages (e.g. after image uploads) stay counted until glibc returns
            # them; trimming each guard cycle lets the hysteresis fall back to normal.
            try:
                from pulse_uploads import release_freed_memory
                release_freed_memory()
            except Exception:
                pass

        if level != previous:
            _RAM_GUARD_LEVEL = level
            if level == 0:
                messages.append(f"✅ Pulse RAM-Schutz: Verbrauch wieder im Normalbereich ({rss:.1f} MB).")
            else:
                labels = {1: "WARNUNG", 2: "KRITISCH", 3: "NOTFALLRESERVE"}
                message = (
                    f"⚠️ **Pulse RAM-Schutz – {labels[level]}**\n"
                    f"Gemessener Container-RAM: **{rss:.1f} MB / {RAM_LIMIT_MB:.0f} MB**. "
                    f"Schwellen: {RAM_WARN_MB:.0f} MB Warnung, {RAM_CRITICAL_MB:.0f} MB kritisch, "
                    f"{RAM_EMERGENCY_MB:.0f} MB Notfallreserve."
                )
                if level >= 2:
                    # Drop only expendable lookup cache entries, not team/session data.
                    try:
                        import webserver
                        lookup_cache = getattr(webserver, "_roblox_cache", None)
                        if lookup_cache is not None:
                            lookup_cache.clear()
                    except Exception:
                        pass
                    collected = gc.collect()
                    message += f"\nCache-Bereinigung und GC ausgeführt ({collected} Objekte gefunden)."
                    try:
                        from pulse_uploads import release_freed_memory
                        if release_freed_memory():
                            message += " Freigegebener Speicher wurde an das System zurückgegeben."
                    except Exception:
                        pass
                messages.append(message)

        # Growth alert: at least 30 minutes of samples, normalised to MB/hour.
        # The ring is capped at 180 points and only lives in this process.
        trend_sample = next(
            (
                sample for sample in _RAM_SAMPLES
                if now_mono - sample[0] >= RAM_TREND_MIN_AGE_SECONDS
            ),
            None,
        )
        if trend_sample is not None:
            elapsed = now_mono - trend_sample[0]
            growth = rss - trend_sample[1]
            growth_per_hour = growth * 3600.0 / max(1.0, elapsed)
            if (
                growth_per_hour >= RAM_TREND_GROWTH_MB_PER_HOUR
                and (
                    not _RAM_LAST_TREND_ALERT_AT
                    or now_mono - _RAM_LAST_TREND_ALERT_AT >= RAM_TREND_ALERT_COOLDOWN_SECONDS
                )
            ):
                _RAM_LAST_TREND_ALERT_AT = now_mono
                messages.append(
                    "📈 **Pulse RAM-Trend erkannt**\n"
                    f"Der Verbrauch stieg um {growth:.1f} MB in {elapsed / 60:.0f} Minuten "
                    f"(hochgerechnet {growth_per_hour:.1f} MB/Stunde; aktuell {rss:.1f} MB). "
                    "Bitte Logs und Cache-/Hintergrundaufgaben prüfen."
                )

        if not messages:
            return
        for message in messages:
            print(message.replace("**", ""))

        channel_id_raw = os.getenv("PULSE_SYSTEM_ALERT_CHANNEL_ID", "").strip()
        try:
            channel_id = int(channel_id_raw or "0")
        except (TypeError, ValueError):
            channel_id = 0
        if channel_id and bot:
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    channel = None
            if channel is not None and hasattr(channel, "send"):
                for message in messages:
                    try:
                        await channel.send(
                            message,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        break
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ Pulse RAM-Schutz konnte nicht vollständig prüfen ({type(exc).__name__}).")

def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).isoformat(timespec="seconds")


def process_rss_mb() -> Optional[float]:
    """Return the current process resident memory in MB when Linux exposes it."""
    try:
        statm = Path("/proc/self/statm").read_text(encoding="ascii").split()
        resident_pages = int(statm[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return round((resident_pages * page_size) / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def container_memory_mb() -> Optional[float]:
    """Return container-wide memory in MiB when cgroup accounting is available.

    Bot-Hosting's limit applies to the container, which may include ffmpeg or
    other child processes that are not reflected in /proc/self/statm.
    Fall back to the Python process RSS on hosts without readable cgroup files.
    """
    for raw_path in (
        "/sys/fs/cgroup/memory.current",              # cgroup v2
        "/sys/fs/cgroup/memory/memory.usage_in_bytes", # cgroup v1
    ):
        try:
            value = int(Path(raw_path).read_text(encoding="ascii").strip())
            if value > 0:
                return round(value / (1024 * 1024), 1)
        except (OSError, ValueError):
            continue
    return process_rss_mb()


def process_peak_rss_mb() -> Optional[float]:
    """Return Linux VmHWM (peak resident memory) in MB, without extra packages."""
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    return None


def uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def cx():
    c = pulse_sqlite.connect(ultimate_db(), timeout=3)
    c.execute("PRAGMA busy_timeout=3000")
    return c


def init_db() -> None:
    global _INIT
    if _INIT:
        return
    with cx() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS ultimate_profiles(
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                bio TEXT DEFAULT '',
                internal_status TEXT DEFAULT 'available',
                internal_message TEXT DEFAULT '',
                rating INTEGER DEFAULT 80,
                probation_end TEXT,
                joined_team_at TEXT,
                archived_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ultimate_departments(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                color TEXT DEFAULT '#6366f1',
                leader_id TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ultimate_department_members(
                department_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT DEFAULT 'member',
                joined_at TEXT NOT NULL,
                PRIMARY KEY(department_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS ultimate_cases(
                id TEXT PRIMARY KEY,
                case_no TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                priority TEXT DEFAULT 'normal',
                status TEXT DEFAULT 'open',
                subject_type TEXT DEFAULT 'team',
                subject_id TEXT,
                subject_name TEXT DEFAULT '',
                description TEXT DEFAULT '',
                assignee_id TEXT,
                assignee_name TEXT DEFAULT '',
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ultimate_cases_state
                ON ultimate_cases(status,priority,updated_at DESC);
            CREATE TABLE IF NOT EXISTS ultimate_case_events(
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ultimate_roblox_profiles(
                roblox_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                avatar_url TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                status TEXT DEFAULT 'clear',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ultimate_case_players(
                case_id TEXT NOT NULL,
                roblox_id TEXT NOT NULL,
                relation TEXT DEFAULT 'subject',
                added_at TEXT NOT NULL,
                PRIMARY KEY(case_id,roblox_id)
            );
            CREATE TABLE IF NOT EXISTS ultimate_attachments(
                id TEXT PRIMARY KEY,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                content_type TEXT DEFAULT 'application/octet-stream',
                size_bytes INTEGER DEFAULT 0,
                sha256 TEXT DEFAULT '',
                uploaded_by_id TEXT NOT NULL,
                uploaded_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ultimate_attach_owner
                ON ultimate_attachments(owner_type,owner_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS ultimate_handovers(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                priority TEXT DEFAULT 'normal',
                from_id TEXT NOT NULL,
                from_name TEXT NOT NULL,
                to_id TEXT,
                to_name TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                created_at TEXT NOT NULL,
                archived_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ultimate_feedback(
                id TEXT PRIMARY KEY,
                category TEXT DEFAULT 'general',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                anonymous INTEGER DEFAULT 0,
                author_id TEXT,
                author_name TEXT DEFAULT '',
                status TEXT DEFAULT 'new',
                manager_note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ultimate_polls(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                poll_type TEXT DEFAULT 'single',
                options_json TEXT NOT NULL,
                ends_at TEXT,
                anonymous INTEGER DEFAULT 0,
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS ultimate_poll_votes(
                poll_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                option_index INTEGER NOT NULL,
                voted_at TEXT NOT NULL,
                PRIMARY KEY(poll_id,user_id,option_index)
            );
            CREATE TABLE IF NOT EXISTS ultimate_calendar(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                kind TEXT DEFAULT 'event',
                start_at TEXT NOT NULL,
                end_at TEXT,
                description TEXT DEFAULT '',
                location TEXT DEFAULT '',
                member_id TEXT,
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ultimate_calendar_start
                ON ultimate_calendar(start_at);
            CREATE TABLE IF NOT EXISTS ultimate_awards(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                icon TEXT DEFAULT '🏆',
                period TEXT DEFAULT '',
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ultimate_certificates(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                score INTEGER DEFAULT 0,
                issuer_id TEXT NOT NULL,
                issuer_name TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                valid_until TEXT
            );
            CREATE TABLE IF NOT EXISTS ultimate_automations(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                interval_minutes INTEGER DEFAULT 1440,
                action_type TEXT NOT NULL,
                action_payload TEXT DEFAULT '{}',
                enabled INTEGER DEFAULT 1,
                next_run_at TEXT NOT NULL,
                last_run_at TEXT,
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ultimate_automation_runs(
                id TEXT PRIMARY KEY,
                automation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ultimate_settings(
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ultimate_api_keys(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                created_by_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            """
        )
        # U-14: scopes (NULL = all, like keys created before), expiry, last use – additive columns only.
        columns = {row[1] for row in c.execute("PRAGMA table_info(ultimate_api_keys)").fetchall()}
        for column in ("scopes", "expires_at", "last_used_at"):
            if column not in columns:
                c.execute(f"ALTER TABLE ultimate_api_keys ADD COLUMN {column} TEXT")
        # 7.18 automation editor: trigger options, step chain, test mode and per-step run results.
        # Additive columns only – automations and runs created before keep working unchanged.
        for table, additions in (
            ("ultimate_automations", (("trigger_config", "TEXT DEFAULT '{}'"), ("steps_json", "TEXT DEFAULT ''"),
                                      ("test_mode", "INTEGER DEFAULT 0"), ("updated_at", "TEXT"), ("updated_by_name", "TEXT"))),
            ("ultimate_automation_runs", (("trigger_info", "TEXT DEFAULT ''"), ("steps_json", "TEXT DEFAULT ''"),
                                          ("duration_ms", "INTEGER DEFAULT 0"), ("actor_name", "TEXT DEFAULT ''"))),
        ):
            columns = {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, kind in additions:
                if column not in columns:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    _INIT = True


def setting(key: str, default=None):
    init_db()
    with cx() as c:
        row = c.execute("SELECT value_json FROM ultimate_settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return row[0]


def set_setting(key: str, value) -> None:
    init_db()
    with cx() as c:
        c.execute(
            "INSERT OR REPLACE INTO ultimate_settings(key,value_json) VALUES(?,?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def seed_defaults() -> None:
    defaults = {
        "branding": {"name": "Pulse TeamOS", "accent": "#6366f1", "logo_url": ""},
        "features": {
            "cases": True, "roblox_records": True, "departments": True,
            "feedback": True, "polls": True, "calendar": True,
            "automations": True, "awards": True, "certificates": True,
            "attachments": True, "api": True,
        },
        "organization_name": "Pulse TeamOS",
    }
    for key, value in defaults.items():
        if setting(key, None) is None:
            set_setting(key, value)


_SEEDED_DB = None


def setup() -> None:
    """Tables + default settings. M-C1: the defaults are seeded once per initialised database, not on every call
    (setup() runs for every profile read – before, 3 extra connections per team member on each page)."""
    global _SEEDED_DB
    first = not _INIT
    init_db()
    key = str(ultimate_db())
    if first or _SEEDED_DB != key:
        seed_defaults()
        _SEEDED_DB = key


def ctx_auth(request: Request, user_session: str, manager: bool = False, perm: Optional[str] = "can_view_dashboard"):
    import webserver
    ctx = webserver.auth(request, user_session, perm=None if manager else perm)
    if manager and not (ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Nur Führungskräfte.")
    return ctx


def team_members(guild):
    import webserver
    ids = {int(x) for x in webserver.load_config().get("team_role_ids", [])}
    return [m for m in guild.members if not m.bot and any(r.id in ids for r in m.roles)]


def audit(ctx, action: str, target_id: str = "", target_name: str = "", details: str = ""):
    import webserver
    webserver.log_audit(
        ctx.user.get("global_name") or ctx.user.get("username") or "Team",
        str(ctx.user["id"]),
        action,
        f"{target_name or target_id}: {details}"[:3000],
    )
    try:
        db.record_event(
            action,
            "ultimate",
            str(target_id),
            str(ctx.user["id"]),
            ctx.user.get("global_name") or ctx.user.get("username") or "Team",
            {"details": details},
        )
    except Exception:
        pass


def case_number() -> str:
    year = utcnow().year
    prefix = f"PX-{year}-%"
    with cx() as c:
        row = c.execute(
            "SELECT COALESCE(MAX(CAST(SUBSTR(case_no, 9) AS INTEGER)), 0) FROM ultimate_cases WHERE case_no LIKE ?",
            (prefix,),
        ).fetchone()
        n = int(row[0] or 0) + 1
    return f"PX-{year}-{n:04d}"


def ensure_profile(member) -> None:
    setup()
    now_s = iso()
    with cx() as c:
        exists = c.execute("SELECT display_name, archived_at FROM ultimate_profiles WHERE user_id=?", (str(member.id),)).fetchone()
        if exists:
            if exists[0] == member.display_name and exists[1] is None:
                return  # M-C1: unchanged – no write transaction per member on every page view
            c.execute(
                "UPDATE ultimate_profiles SET display_name=?,archived_at=NULL WHERE user_id=?",
                (member.display_name, str(member.id)),
            )
        else:
            c.execute(
                """INSERT INTO ultimate_profiles
                (user_id,display_name,joined_team_at,updated_at) VALUES(?,?,?,?)""",
                (str(member.id), member.display_name, now_s, now_s),
            )


def get_profile(user_id: str):
    setup()
    with cx() as c:
        row = c.execute("SELECT * FROM ultimate_profiles WHERE user_id=?", (str(user_id),)).fetchone()
    return dict(row) if row else None


def stats_context() -> dict:
    """M-C1: the member-independent inputs of member_stats, read once per page instead of once per member
    (before: 2 × 5000 tickets, shifts, team data and config for every single team member)."""
    import webserver
    claimed, closed = {}, {}
    for t in db.list_tickets(limit=5000):
        key = str(t.get("claimed_by_id"))
        claimed[key] = claimed.get(key, 0) + 1
        if t.get("status") == "closed":
            closed[key] = closed.get(key, 0) + 1
    return {"shifts": webserver.load_shifts(), "claimed": claimed, "closed": closed,
            "team_data": webserver.load_json(webserver.DATA_FILE, {}), "config": webserver.load_config()}


def member_stats(member, context: dict | None = None) -> dict:
    """Score and figures of one team member. Loops over many members pass one stats_context()."""
    import webserver
    ensure_profile(member)
    context = context if context is not None else stats_context()
    shifts = context["shifts"]
    uid_s = str(member.id)
    weekly = webserver.calculate_weekly_seconds(uid_s, shifts.get("history", []), shifts.get("active_shifts", {}))
    history = [h for h in shifts.get("history", []) if str(h.get("mod_id")) == uid_s]
    total_seconds = sum(webserver.credited_shift_seconds(h) for h in history)  # E-2
    active = shifts.get("active_shifts", {}).get(uid_s)
    if active:
        total_seconds += webserver.shift_elapsed(active)
    tickets = context["claimed"].get(uid_s, 0)
    closed_tickets = context["closed"].get(uid_s, 0)
    tasks = db.list_tasks(assignee_id=member.id, limit=1000, include_archived=True)
    tasks_done = sum(1 for t in tasks if t.get("status") in ("done", "archived"))
    attempts = db.attempts(user_id=member.id, limit=200)
    passed = sum(1 for x in attempts if x.get("passed"))
    profile = get_profile(uid_s) or {}
    warnings = len(webserver.active_warns(webserver.user_entry(context["team_data"], uid_s)))
    weekly_goal = max(1.0, float(context["config"].get("weekly_goal_hours", 3.0)))
    activity_pct = min(100, round(weekly / 3600 / weekly_goal * 100))
    reliability_pct = 100 if not tasks else round(tasks_done / len(tasks) * 100)
    support_pct = min(100, closed_tickets * 5)
    discipline_pct = max(0, 100 - warnings * 20)
    training_pct = min(100, passed * 25)
    score = round(
        activity_pct * 0.30
        + reliability_pct * 0.20
        + support_pct * 0.15
        + discipline_pct * 0.20
        + training_pct * 0.15
    )
    return {
        "weekly_seconds": weekly,
        "total_seconds": total_seconds,
        "shifts": len(history),
        "tickets": tickets,
        "closed_tickets": closed_tickets,
        "tasks": len(tasks),
        "tasks_done": tasks_done,
        "training_passed": passed,
        "warnings": warnings,
        "score": score,
        "activity_pct": activity_pct,
        "reliability_pct": reliability_pct,
        "support_pct": support_pct,
        "discipline_pct": discipline_pct,
        "training_pct": training_pct,
        "profile": profile,
    }


def case_rows(limit=100):
    setup()
    with cx() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM ultimate_cases
               ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
               updated_at DESC LIMIT ?""",
            (max(1, min(int(limit), 500)),),
        ).fetchall()]


def poll_results(poll_id: str):
    setup()
    with cx() as c:
        rows = c.execute(
            "SELECT option_index,COUNT(*) AS votes FROM ultimate_poll_votes WHERE poll_id=? GROUP BY option_index",
            (poll_id,),
        ).fetchall()
    return {int(r["option_index"]): int(r["votes"]) for r in rows}


def add_case_event(case_id: str, ctx, event_type: str, details: str):
    with cx() as c:
        c.execute(
            "INSERT INTO ultimate_case_events VALUES(?,?,?,?,?,?,?)",
            (uid("ce"), case_id, str(ctx.user["id"]),
             ctx.user.get("global_name") or ctx.user.get("username") or "Team",
             event_type, details[:3000], iso()),
        )


def status_badge(status: str) -> str:
    # Theme-aware classes from the shared design system (readable in light and dark mode).
    classes = {"open": "blue", "investigating": "warn", "waiting": "blue", "resolved": "ok", "closed": ""}
    return f'<span class="badge {classes.get(status, "blue")}">{esc(status)}</span>'


def card(inner: str) -> str:
    return f'<div class="card">{inner}</div>'


def page(ctx, active: str, title: str, subtitle: str, body: str, extra_js: str = "") -> HTMLResponse:
    """Render an Ultimate/TeamOS page inside the shared Pulse shell (one navigation, light/dark)."""
    import webserver
    from pulse_design import legacy_page

    actions = '<a class="btn" href="/me">★ Mein Pulse</a><a class="btn primary" href="/ultimate">⚡ Command Center</a>'
    # legacy_page inserts `scripts` as raw HTML: bare JavaScript was shown as page text and never ran
    # (the live system monitor on /ultimate/system stayed at "wird geladen").
    if extra_js.strip() and not extra_js.lstrip().startswith("<"):
        extra_js = f"<script>{extra_js}</script>"
    return legacy_page(
        webserver, ctx, f"u:{active}", title, subtitle, body,
        actions=actions, scripts=extra_js, kicker="Pulse TeamOS · Command Center",
    )


# U-14: what a key may read. Keys from before U-14 have scopes NULL and keep access to everything.
API_SCOPES = {"overview": "Übersicht (/ultimate/api/v2/overview)", "team": "Teamliste (/ultimate/api/v2/team)"}
API_RATE_LIMIT = (120, 60)  # requests per key and window in seconds
API_EXPIRY_CHOICES = {"30": 30, "90": 90, "365": 365, "0": 0}  # 0 = unbefristet


def check_api_key(token: str, scope: Optional[str] = None) -> int:
    """HTTP status for this key: 200 ok, 401 unknown/revoked/expired, 403 scope missing, 429 too many requests."""
    setup()
    if not token or len(token) > 200:
        return 401
    digest = hashlib.sha256(token.encode()).hexdigest()
    with cx() as c:
        row = c.execute(
            "SELECT id,scopes,expires_at,last_used_at FROM ultimate_api_keys WHERE token_hash=? AND revoked_at IS NULL",
            (digest,),
        ).fetchone()
    if not row:
        return 401
    if row["expires_at"] and row["expires_at"] <= iso():
        return 401
    if scope and row["scopes"] is not None and scope not in row["scopes"].split(","):
        return 403
    import webserver
    if webserver.rate_limited(f"apikey:{row['id']}", *API_RATE_LIMIT):
        return 429
    now = iso()
    if (row["last_used_at"] or "")[:16] != now[:16]:  # written at most once a minute
        with cx() as c:
            c.execute("UPDATE ultimate_api_keys SET last_used_at=? WHERE id=?", (now, row["id"]))
    return 200


def require_api_key(request: Request, scope: str) -> None:
    status = check_api_key(request.headers.get("X-Pulse-API-Key", ""), scope)
    if status != 200:
        raise HTTPException(status, {401: "Ungültiger oder abgelaufener API-Key.", 403: "Dieser API-Key darf diesen Bereich nicht lesen.",
                                     429: "Zu viele Anfragen – bitte kurz warten."}[status])


def validate_api_key(token: str) -> bool:
    return check_api_key(token) == 200


def register(app) -> None:
    setup()
    if getattr(app.state, "pulse_ultimate_registered", False):
        return

    @app.get("/ultimate", response_class=HTMLResponse)
    async def ultimate_home(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        import webserver
        guild = ctx.guild
        members = team_members(guild)
        for m in members:
            ensure_profile(m)
        cases = case_rows(500)
        open_cases = sum(x["status"] not in ("closed", "resolved") for x in cases)
        urgent = sum(x["priority"] == "urgent" and x["status"] not in ("closed", "resolved") for x in cases)
        feedback = 0
        with cx() as c:
            feedback = c.execute("SELECT COUNT(*) FROM ultimate_feedback WHERE status IN ('new','reviewing')").fetchone()[0]
            handovers = c.execute("SELECT COUNT(*) FROM ultimate_handovers WHERE status='open'").fetchone()[0]
            polls = c.execute("SELECT COUNT(*) FROM ultimate_polls WHERE active=1").fetchone()[0]
            automations = c.execute("SELECT COUNT(*) FROM ultimate_automations WHERE enabled=1").fetchone()[0]
            departments = c.execute("SELECT COUNT(*) FROM ultimate_departments WHERE active=1").fetchone()[0]
        active_shifts = webserver.load_shifts().get("active_shifts", {})
        online = sum(str(m.status) in {"online", "idle", "dnd"} for m in members)
        shared = stats_context()
        avg_score = round(sum(member_stats(m, shared)["score"] for m in members) / len(members)) if members else 0
        metrics = "".join(
            card(f'<div class="tiny">{lab}</div><div class="metric">{val}</div>')
            for lab, val in (
                ("👥 Teammitglieder", len(members)), ("🟢 Online", online),
                ("⏱ Im Dienst", len(active_shifts)), ("🚨 Offene Fälle", open_cases),
                ("🔥 Kritische Fälle", urgent), ("💬 Offenes Feedback", feedback),
                ("🧭 Übergaben", handovers), ("🗳 Aktive Abstimmungen", polls),
            )
        )
        warnings = []
        for m in members:
            s = member_stats(m, shared)
            if s["warnings"] >= 5:
                warnings.append(f'🚨 <b>{esc(m.display_name)}</b> hat {s["warnings"]}/5 Warnungen.')
            elif s["activity_pct"] < 50:
                warnings.append(f'⚠️ <b>{esc(m.display_name)}</b> liegt bei nur {s["activity_pct"]}% Wochenziel.')
        alerts = card("<h2 style='margin-top:0'>🚨 Aufmerksamkeit</h2>" + "".join(f"<div class='row'>{x}</div>" for x in warnings[:8]) if warnings else "<h2 style='margin-top:0'>🚨 Aufmerksamkeit</h2><div class='tiny'>Aktuell keine kritischen Hinweise.</div>")
        try:
            import pulse_next
            with pulse_next.cx() as nc:
                pending_approvals_count = nc.execute(
                    "SELECT COUNT(*) FROM next_approvals WHERE status='pending' AND requester_id!=?",
                    (str(ctx.user["id"]),),
                ).fetchone()[0]
                unread_news_count = nc.execute(
                    """SELECT COUNT(*) FROM next_announcements a
                       WHERE NOT EXISTS(
                         SELECT 1 FROM next_announcement_reads r
                         WHERE r.announcement_id=a.id AND r.user_id=?
                       )""",
                    (str(ctx.user["id"]),),
                ).fetchone()[0]
                due_goals_count = nc.execute(
                    """SELECT COUNT(*) FROM next_goals
                       WHERE status='active' AND due_at IS NOT NULL AND due_at<=?""",
                    ((datetime.now(timezone.utc)+timedelta(days=3)).isoformat(),),
                ).fetchone()[0]
            overdue_tasks_count = 0
            for task in db.list_tasks(limit=1000, include_archived=False):
                if str(task.get("status") or "") in {"done","archived"}:
                    continue
                due = str(task.get("due_date") or task.get("deadline") or "")
                if due:
                    try:
                        if datetime.fromisoformat(due.replace("Z","+00:00")) < datetime.now(timezone.utc):
                            overdue_tasks_count += 1
                    except Exception:
                        pass
            overdue_ticket_count = 0
            for ticket in db.list_tickets(limit=5000):
                if ticket.get("status") == "closed":
                    continue
                try:
                    opened = datetime.fromisoformat(str(ticket.get("opened_at","")).replace("Z","+00:00"))
                    sla = {"urgent":30,"high":240,"normal":1440}.get(str(ticket.get("priority") or "normal"),1440)
                    if (datetime.now(timezone.utc)-opened).total_seconds() >= sla*60:
                        overdue_ticket_count += 1
                except Exception:
                    pass
        except Exception:
            pending_approvals_count = unread_news_count = due_goals_count = overdue_tasks_count = overdue_ticket_count = 0
        today_actions = "".join(
            f"<a class='row' href='{href}'><span>{icon} <b>{esc(label)}</b></span><span class='pill'>{count}</span></a>"
            for icon,label,count,href in (
                ("✅","Offene Freigaben",pending_approvals_count,"/ultimate/approvals"),
                ("📢","Ungelesene Team-News",unread_news_count,"/ultimate/announcements"),
                ("🎯","Ziele mit naher Frist",due_goals_count,"/ultimate/goals"),
                ("📋","Überfällige Aufgaben",overdue_tasks_count,"/ultimate/tasks"),
                ("🎫","SLA-überfällige Tickets",overdue_ticket_count,"/ultimate/support"),
            )
            if count
        ) or "<div class='tiny'>Für heute stehen aktuell keine dringenden Pulse-Aufgaben an.</div>"
        ops = card("<h2 style='margin-top:0'>🧭 Heute erledigen</h2>"+today_actions)
        body = ops + f'<div class="grid g4">{metrics}</div><div class="grid g2" style="margin-top:13px">{alerts}{card(f"<h2 style=\'margin-top:0\'>📊 Team-Score</h2><div class=\'metric\'>{avg_score}/100</div><p class=\'tiny\'>Durchschnittlicher Performance-Score aus Aktivität, Zuverlässigkeit, Support, Disziplin und Training.</p><div class=\'progress\'><div style=\'width:{avg_score}%\'></div></div>")}</div><div class="grid g3" style="margin-top:13px">{card(f"<h3 style=\'margin-top:0\'>🏢 Abteilungen</h3><div class=\'metric\'>{departments}</div><a class=\'btn primary\' href=\'/ultimate/departments\'>Verwalten</a>")}{card("<h3 style=\'margin-top:0\'>🤖 Automationen</h3><div class=\'metric\'>"+str(automations)+"</div><a class=\'btn primary\' href=\'/ultimate/automations\'>Regeln öffnen</a>")}{card("<h3 style=\'margin-top:0\'>🧩 Module</h3><div class=\'tiny\'>Cases · Roblox · Feedback · Polls · Kalender · Awards · Zertifikate · Dateien · API</div>")}</div>'
        return page(ctx, "ultimate", "Pulse Ultimate Command Center", "Zentrale Leitstelle für Team, Fälle, Automationen und Leistung.", body)

    @app.get("/ultimate/team", response_class=HTMLResponse)
    async def ultimate_team(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        guild = ctx.guild
        members = sorted(team_members(guild), key=lambda x: x.display_name.lower())
        rows = []

        def compute():  # M-C1: SQLite/JSON work for every member – off the event loop the bot shares
            shared = stats_context()
            return [member_stats(m, shared) for m in members]
        all_stats = await asyncio.to_thread(compute)
        for m, s in zip(members, all_stats):
            p = s["profile"]
            status = p.get("internal_status", "available")
            warn_text = f'{s["warnings"]}/5' if __import__("webserver").can_see_warnings(ctx.perms, ctx.user.get("id"), m.id) else "—"  # E-3
            rows.append(
                f'<div class="card"><div class="row"><div><b>{esc(m.display_name)}</b><div class="tiny">@{esc(m.name)}</div></div>'
                f'<span class="pill">{esc(status)}</span></div><div class="tiny">Score</div><div class="metric">{s["score"]}/100</div>'
                f'<div class="tiny">Woche: {s["weekly_seconds"]//3600}h {(s["weekly_seconds"]%3600)//60}m · Warnungen: {warn_text}</div>'
                f'<div style="margin-top:10px"><div class="progress"><div style="width:{s["activity_pct"]}%"></div></div></div>'
                f'<div style="margin-top:10px"><a class="btn primary" href="/ultimate/team/{m.id}">Teamakte</a></div></div>'
            )
        own = get_profile(str(ctx.user["id"])) or {}
        own_form = f"""<div class="card" style="margin-bottom:13px">
          <h3>🟢 Mein Teamstatus</h3>
          <form method="post" action="/ultimate/team/status" class="form">
            <select class="select" name="internal_status">
              {''.join(f"<option {'selected' if s==own.get('internal_status','available') else ''}>{s}</option>" for s in ('available','service','break','training','admin','unavailable'))}
            </select>
            <input class="input" name="internal_message" maxlength="300" value="{esc(own.get('internal_message',''))}" placeholder="Kurze interne Info">
            <button class="btn primary">Status speichern</button>
          </form>
        </div>"""
        body = own_form + f'<div class="grid g3">{"".join(rows) or card("<div class=tiny>Keine Teammitglieder.</div>")}</div>'
        return page(ctx, "team", "Teamakten", "Leistung, Disziplin, Probezeit, Status, Abteilungen und Historie.", body)

    @app.get("/ultimate/team/{user_id}", response_class=HTMLResponse)
    async def ultimate_member(request: Request, user_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        member = ctx.guild.get_member(int(user_id)) if str(user_id).isdigit() else None
        if not member:
            raise HTTPException(404, "Teammitglied nicht gefunden.")
        s = member_stats(member)
        p = s["profile"]
        with cx() as c:
            deps = c.execute(
                """SELECT d.name,dm.role FROM ultimate_department_members dm
                   JOIN ultimate_departments d ON d.id=dm.department_id WHERE dm.user_id=? AND d.active=1""",
                (str(member.id),),
            ).fetchall()
            awards = c.execute(
                "SELECT * FROM ultimate_awards WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
                (str(member.id),),
            ).fetchall()
            certs = c.execute(
                "SELECT * FROM ultimate_certificates WHERE user_id=? ORDER BY issued_at DESC LIMIT 20",
                (str(member.id),),
            ).fetchall()
            case_count = c.execute(
                "SELECT COUNT(*) FROM ultimate_cases WHERE subject_id=?",
                (str(member.id),),
            ).fetchone()[0]
        deps_html = "".join(f"<span class='pill'>{esc(d['name'])} · {esc(d['role'])}</span> " for d in deps) or "<span class='tiny'>Keine Abteilung</span>"
        awards_html = "".join(f"<div class='row'>{esc(a['icon'])} <b>{esc(a['title'])}</b><span class='tiny'>{esc(a['period'])}</span></div>" for a in awards) or "<div class='tiny'>Noch keine Awards.</div>"
        cert_html = "".join(f"<div class='row'>🎓 <b>{esc(c['title'])}</b><span>{int(c['score'])}%</span></div>" for c in certs) or "<div class='tiny'>Keine Zertifikate.</div>"
        can_edit = ctx.perms.get("can_promote") or ctx.perms.get("is_admin")
        cert_form = f"""<div class="card"><h3>🎓 Zertifikat ausstellen</h3><form class="form" method="post" action="/ultimate/certificates/create">
          <input class="input span2" name="title" placeholder="z.B. Support-Grundausbildung" required>
          <input class="input" type="number" name="score" min="0" max="100" value="100">
          <input class="input" name="valid_until" placeholder="optional: 2027-10-05">
          <input type="hidden" name="user_id" value="{member.id}">
          <button class="btn primary span2">Zertifikat ausstellen</button>
        </form></div>""" if can_edit else ""
        form = f"""<div class="card"><h3>Profil bearbeiten</h3><form class="form" method="post" action="/ultimate/team/{member.id}/update">
            <div><label>Status</label><select class="select" name="internal_status"><option>available</option><option>service</option><option>break</option><option>training</option><option>admin</option><option>unavailable</option></select></div>
            <div><label>Interne Bewertung (0-100)</label><input class="input" type="number" name="rating" min="0" max="100" value="{int(p.get('rating',80))}"></div>
            <div class="span2"><label>Interne Nachricht</label><input class="input" name="internal_message" maxlength="300" value="{esc(p.get('internal_message',''))}"></div>
            <div class="span2"><button class="btn primary">Speichern</button></div></form></div>""" if can_edit else ""
        body = f"""<div class="grid g4">
            {card(f"<div class=tiny>Team-Score</div><div class=metric>{s['score']}/100</div>")}
            {card(f"<div class=tiny>Wochenzeit</div><div class=metric>{s['weekly_seconds']//3600}h {(s['weekly_seconds']%3600)//60}m</div>")}
            {card(f"<div class=tiny>Warnungen</div><div class=metric>{s['warnings'] if __import__('webserver').can_see_warnings(ctx.perms, ctx.user.get('id'), member.id) else '—'}{'/5' if __import__('webserver').can_see_warnings(ctx.perms, ctx.user.get('id'), member.id) else ''}</div>")}
            {card(f"<div class=tiny>Fälle</div><div class=metric>{case_count}</div>")}
        </div>
        <div class="grid g2" style="margin-top:13px">
          {card(f"<h3>🏢 Abteilungen</h3><div>{deps_html}</div>")}
          {card(f"<h3>🎯 Probezeit</h3><div class=tiny>{esc(p.get('probation_end') or 'Nicht gesetzt')}</div><div class=metric>{esc(p.get('internal_status','available'))}</div>")}
          {card(f"<h3>🏆 Awards</h3>{awards_html}")}
          {card(f"<h3>🎓 Zertifikate</h3>{cert_html}")}
        </div>{cert_form}{form}"""
        return page(ctx, "team", f"Teamakte · {member.display_name}", "Digitale Personalakte mit Leistungs- und Karrieredaten.", body)

    @app.post("/ultimate/team/{user_id}/update")
    async def ultimate_member_update(
        request: Request, user_id: str,
        internal_status: str = Form(...),
        rating: int = Form(80),
        internal_message: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            row = c.execute("SELECT * FROM ultimate_profiles WHERE user_id=?", (str(user_id),)).fetchone()
            if not row:
                member = ctx.guild.get_member(int(user_id))
                if not member:
                    raise HTTPException(404, "Mitglied nicht gefunden.")
                ensure_profile(member)
            c.execute(
                "UPDATE ultimate_profiles SET internal_status=?,rating=?,internal_message=?,updated_at=? WHERE user_id=?",
                (internal_status[:30], max(0,min(100,rating)), internal_message[:300], iso(), str(user_id)),
            )
        audit(ctx, "Profil aktualisiert", user_id, "", f"Status={internal_status}; rating={rating}")
        return RedirectResponse(f"/ultimate/team/{user_id}", status_code=303)

    @app.get("/ultimate/cases", response_class=HTMLResponse)
    async def ultimate_cases(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        rows = case_rows(200)
        cards = []
        for x in rows:
            cards.append(
                card(f"""<div class="row"><div><b>{esc(x['case_no'])} · {esc(x['title'])}</b><div class="tiny">{esc(x['subject_name'])} · {esc(x['category'])}</div></div>{status_badge(x['status'])}</div>
                <div class="tiny">Priorität: {esc(x['priority'])} · Verantwortlich: {esc(x['assignee_name'] or 'Niemand')}</div>
                <p class="tiny">{esc(x['description'][:400])}</p>
                <a class="btn primary" href="/ultimate/cases/{esc(x['id'])}">Fall öffnen</a>""")
            )
        body = f"""<div class="card"><h3>🚨 Neuen Fall anlegen</h3><form class="form" method="post" action="/ultimate/cases/create">
            <input class="input" name="title" placeholder="Falltitel" maxlength="160" required>
            <select class="select" name="category"><option>general</option><option>moderation</option><option>team</option><option>support</option><option>recruiting</option><option>security</option><option>roblox</option></select>
            <select class="select" name="priority"><option>normal</option><option>low</option><option>high</option><option>urgent</option></select>
            <input class="input" name="subject_name" placeholder="Betroffene Person / Spieler">
            <input class="input" name="subject_id" placeholder="Discord-/Roblox-ID">
            <textarea class="ta span2" name="description" maxlength="5000" placeholder="Sachverhalt..."></textarea>
            <button class="btn primary span2">Fall erstellen</button></form></div>
            <div class="grid g2" style="margin-top:13px">{"".join(cards) or card("<div class=tiny>Keine Fälle.</div>")}</div>"""
        return page(ctx, "cases", "Fallverwaltung", "PX-Fälle mit Priorität, Verantwortlichen, Timeline und Beweisen.", body)

    @app.post("/ultimate/cases/create")
    async def ultimate_case_create(
        request: Request,
        title: str = Form(...), category: str = Form("general"),
        priority: str = Form("normal"), subject_name: str = Form(""),
        subject_id: str = Form(""), description: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        if priority not in _PRIORITY or not title.strip():
            raise HTTPException(400, "Ungültige Falldaten.")
        now_s = iso()
        case_id = uid("case")
        number = case_number()
        with cx() as c:
            c.execute(
                """INSERT INTO ultimate_cases(id,case_no,title,category,priority,status,subject_type,subject_id,
                subject_name,description,assignee_id,assignee_name,created_by_id,created_by_name,created_at,updated_at,closed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (case_id, number, title.strip()[:160], category[:40], priority, "open",
                 "player" if category == "roblox" else "team", subject_id[:100],
                 subject_name[:160], description[:5000], None, "", str(ctx.user["id"]),
                 ctx.user.get("global_name") or ctx.user.get("username") or "Team",
                 now_s, now_s, None),
            )
        add_case_event(case_id, ctx, "created", description[:1000] or "Fall erstellt")
        audit(ctx, "Fall erstellt", case_id, number, f"{category}/{priority}")
        return RedirectResponse("/ultimate/cases", status_code=303)

    @app.get("/ultimate/cases/{case_id}", response_class=HTMLResponse)
    async def ultimate_case_detail(request: Request, case_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            case = c.execute("SELECT * FROM ultimate_cases WHERE id=?", (case_id,)).fetchone()
            events = c.execute(
                "SELECT * FROM ultimate_case_events WHERE case_id=? ORDER BY created_at DESC LIMIT 100",
                (case_id,),
            ).fetchall()
            attachments = c.execute(
                "SELECT * FROM ultimate_attachments WHERE owner_type='case' AND owner_id=? ORDER BY created_at DESC",
                (case_id,),
            ).fetchall()
            players = c.execute(
                """SELECT p.*,cp.relation FROM ultimate_case_players cp
                   JOIN ultimate_roblox_profiles p ON p.roblox_id=cp.roblox_id
                   WHERE cp.case_id=?""",
                (case_id,),
            ).fetchall()
        if not case:
            raise HTTPException(404, "Fall nicht gefunden.")
        ev = "".join(f"<div class='row'><div><b>{esc(x['event_type'])}</b><div class='tiny'>{esc(x['details'])}</div></div><span class='tiny'>{esc(x['created_at'])}</span></div>" for x in events)
        att = "".join(f"<div class='row'><a href='/ultimate/cases/{esc(case_id)}/attachments/{x['id']}'>📎 {esc(x['original_name'])}</a><span class='tiny'>{x['size_bytes']} B · {esc(x['sha256'][:12])}</span></div>" for x in attachments) or "<div class='tiny'>Keine Beweise/Dateien.</div>"
        pls = "".join(f"<div class='row'><span>🎮 {esc(x['display_name'])} (@{esc(x['username'])})</span><span class='pill'>{esc(x['status'])}</span></div>" for x in players) or "<div class='tiny'>Keine Roblox-Spieler verknüpft.</div>"
        body = f"""<div class="grid g2">
          {card(f"<div class=row><b>{esc(case['case_no'])}</b>{status_badge(case['status'])}</div><h2>{esc(case['title'])}</h2><p class=tiny>{esc(case['description'])}</p><div class=tiny>Priorität: {esc(case['priority'])} · Bearbeiter: {esc(case['assignee_name'] or 'Niemand')}</div>")}
          {card(f"""<h3>🎮 Roblox-Akte</h3>{pls}
            <form method="post" action="/ultimate/cases/{esc(case_id)}/players/add" class="form" style="margin-top:10px">
              <input class="input" name="roblox_id" placeholder="Roblox-ID" required>
              <select class="select" name="relation"><option>subject</option><option>witness</option><option>reporter</option></select>
              <button class="btn primary">Verknüpfen</button>
            </form>""")}
        </div>
        <div class="grid g2" style="margin-top:13px">
          {card(f"<h3>🕵 Timeline</h3>{ev or '<div class=tiny>Keine Ereignisse.</div>'}")}
          {card(f"<h3>📎 Beweise</h3>{att}<a class='btn primary' href='/ultimate/cases/{esc(case_id)}/upload'>Datei hinzufügen</a>")}
        </div>
        {card(f"""<form class="form" method="post" action="/ultimate/cases/{esc(case_id)}/update">
          <select class="select" name="status">{"".join(f"<option {'selected' if s==case['status'] else ''}>{s}</option>" for s in _CASE_STATUSES)}</select>
          <select class="select" name="priority">{"".join(f"<option {'selected' if p==case['priority'] else ''}>{p}</option>" for p in _PRIORITY)}</select>
          <input class="input" name="assignee_name" value="{esc(case['assignee_name'])}" placeholder="Bearbeitername">
          <button class="btn primary">Fall aktualisieren</button>
        </form>""")}"""
        return page(ctx, "cases", f"Fall {case['case_no']}", "Fallakte mit Timeline und Beweisverwaltung.", body)

    @app.post("/ultimate/cases/{case_id}/update")
    async def ultimate_case_update(
        request: Request, case_id: str, status: str = Form(...),
        priority: str = Form(...), assignee_name: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        if status not in _CASE_STATUSES or priority not in _PRIORITY:
            raise HTTPException(400, "Ungültiger Fallstatus.")
        with cx() as c:
            exists = c.execute("SELECT 1 FROM ultimate_cases WHERE id=?", (case_id,)).fetchone()
            if not exists:
                raise HTTPException(404, "Fall nicht gefunden.")
            closed_at = iso() if status == "closed" else None
            c.execute(
                "UPDATE ultimate_cases SET status=?,priority=?,assignee_name=?,updated_at=?,closed_at=? WHERE id=?",
                (status, priority, assignee_name[:160], iso(), closed_at, case_id),
            )
        add_case_event(case_id, ctx, "updated", f"Status={status}; Priorität={priority}; Bearbeiter={assignee_name[:160]}")
        audit(ctx, "Fall aktualisiert", case_id, "", f"{status}/{priority}")
        return RedirectResponse(f"/ultimate/cases/{case_id}", status_code=303)

    @app.get("/ultimate/cases/{case_id}/upload", response_class=HTMLResponse)
    async def ultimate_upload_page(request: Request, case_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        body = f"""<div class="card"><h2>📎 Beweis anhängen</h2><form method="post" action="/ultimate/cases/{esc(case_id)}/upload" enctype="multipart/form-data">
        <input class="input" type="file" name="file" required><p class="tiny">Maximal 10 MB.</p><button class="btn primary">Hochladen</button></form></div>"""
        return page(ctx, "cases", "Datei hinzufügen", "Beweise werden mit Hash und Metadaten gespeichert.", body)

    @app.post("/ultimate/cases/{case_id}/upload")
    async def ultimate_upload(
        request: Request, case_id: str, file: UploadFile = File(...),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            if not c.execute("SELECT 1 FROM ultimate_cases WHERE id=?", (case_id,)).fetchone():
                raise HTTPException(404, "Fall nicht gefunden.")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "upload.bin")[:120]
        # Read at most limit+1 bytes; the per-file cap keeps this temporary buffer bounded.
        try:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
        finally:
            await file.close()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Datei ist größer als 10 MB.")
        digest = hashlib.sha256(data).hexdigest()
        stored = f"{case_id}_{digest[:12]}_{safe}"
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        path = ATTACHMENTS_DIR / stored
        existed = path.exists()  # same case + same content = same file of an earlier upload
        path.write_bytes(data)
        try:
            with cx() as c:
                c.execute(
                    "INSERT INTO ultimate_attachments(id,owner_type,owner_id,original_name,stored_name,content_type,size_bytes,"
                    "sha256,uploaded_by_id,uploaded_by_name,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (uid("file"), "case", case_id, safe, stored, file.content_type or "application/octet-stream",
                     len(data), digest, str(ctx.user["id"]),
                     ctx.user.get("global_name") or ctx.user.get("username") or "Team", iso()),
                )
        except Exception:
            if not existed:  # M-F1: no orphaned evidence file without a database entry
                path.unlink(missing_ok=True)
            raise
        add_case_event(case_id, ctx, "attachment", safe)
        audit(ctx, "Beweis hochgeladen", case_id, safe, f"{len(data)} Bytes · {digest[:16]}")
        return RedirectResponse(f"/ultimate/cases/{case_id}", status_code=303)

    @app.get("/ultimate/roblox", response_class=HTMLResponse)
    async def ultimate_roblox(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            players = c.execute(
                "SELECT * FROM ultimate_roblox_profiles ORDER BY updated_at DESC LIMIT 200"
            ).fetchall()
        rows = "".join(
            card(f"<div class=row><div><b>{esc(x['display_name'])}</b><div class=tiny>@{esc(x['username'])} · ID {esc(x['roblox_id'])}</div></div><span class='pill'>{esc(x['status'])}</span></div><div class=tiny>{esc(x['notes'][:250])}</div>")
            for x in players
        )
        body = f"""<div class="card"><h3>🎮 Roblox-Spielerakte</h3>
        <form class="form" method="post" action="/ultimate/roblox/save">
          <input class="input" name="username" placeholder="Roblox Username" required>
          <input class="input" name="roblox_id" placeholder="Roblox ID">
          <input class="input span2" name="display_name" placeholder="Display Name">
          <input class="input" name="avatar_url" placeholder="Avatar URL">
          <select class="select" name="status"><option>clear</option><option>watch</option><option>bolo</option><option>banned</option></select>
          <textarea class="ta span2" name="notes" placeholder="Interne Notizen..."></textarea>
          <button class="btn primary span2">Spielerakte speichern</button>
        </form></div><div class="grid g2" style="margin-top:13px">{rows or card("<div class=tiny>Keine Spielerakten.</div>")}</div>"""
        return page(ctx, "roblox", "Roblox-Spielerakten", "Moderationsakte, BOLO-Status und interne Notizen.", body)

    @app.post("/ultimate/roblox/save")
    async def ultimate_roblox_save(
        request: Request, username: str = Form(...), roblox_id: str = Form(""),
        display_name: str = Form(""), avatar_url: str = Form(""),
        status: str = Form("clear"), notes: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        clean_id = str(roblox_id).strip()
        if not clean_id.isdigit():
            raise HTTPException(400, "Roblox-ID muss numerisch sein.")
        if status not in {"clear", "watch", "bolo", "banned"}:
            raise HTTPException(400, "Ungültiger Spielerstatus.")
        with cx() as c:
            c.execute(
                """INSERT OR REPLACE INTO ultimate_roblox_profiles
                (roblox_id,username,display_name,avatar_url,notes,status,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (clean_id, username.strip()[:50], (display_name.strip() or username.strip())[:80],
                 avatar_url.strip()[:500], notes.strip()[:3000], status, iso()),
            )
        audit(ctx, "Roblox-Akte aktualisiert", clean_id, username.strip(), f"Status={status}")
        return RedirectResponse("/ultimate/roblox", status_code=303)

    @app.post("/ultimate/cases/{case_id}/players/add")
    async def ultimate_case_player_add(
        request: Request, case_id: str, roblox_id: str = Form(...),
        relation: str = Form("subject"), user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        rid = roblox_id.strip()
        with cx() as c:
            case = c.execute("SELECT 1 FROM ultimate_cases WHERE id=?", (case_id,)).fetchone()
            profile = c.execute("SELECT 1 FROM ultimate_roblox_profiles WHERE roblox_id=?", (rid,)).fetchone()
        if not case:
            raise HTTPException(404, "Fall nicht gefunden.")
        if not rid.isdigit():
            raise HTTPException(400, "Roblox-ID muss numerisch sein.")
        if not profile:
            raise HTTPException(404, "Roblox-Spielerakte zuerst unter Roblox anlegen.")
        if relation not in {"subject","witness","reporter"}:
            relation = "subject"
        with cx() as c:
            c.execute(
                "INSERT OR REPLACE INTO ultimate_case_players VALUES(?,?,?,?)",
                (case_id, rid, relation, iso()),
            )
        add_case_event(case_id, ctx, "player_linked", f"Roblox {rid} · {relation}")
        audit(ctx, "Roblox-Spieler mit Fall verknüpft", case_id, rid, relation)
        return RedirectResponse(f"/ultimate/cases/{case_id}", status_code=303)

    @app.get("/ultimate/departments", response_class=HTMLResponse)
    async def ultimate_departments(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        members = team_members(ctx.guild)
        with cx() as c:
            deps = [dict(r) for r in c.execute("SELECT * FROM ultimate_departments WHERE active=1 ORDER BY name").fetchall()]
        cards = []
        opts = "".join(f"<option value='{m.id}'>{esc(m.display_name)}</option>" for m in members)
        for d in deps:
            with cx() as c:
                count = c.execute("SELECT COUNT(*) FROM ultimate_department_members WHERE department_id=?", (d["id"],)).fetchone()[0]
            cards.append(card(f"<div class=row><b>{esc(d['name'])}</b><span class='pill'>{count} Mitglieder</span></div><div class=tiny>{esc(d['description'])}</div><div style='margin-top:8px'><a class='btn' href='/ultimate/departments/{d['id']}'>Öffnen</a></div>"))
        body = f"""<div class="card"><h3>🏢 Abteilung anlegen</h3><form class="form" method="post" action="/ultimate/departments/create">
          <input class="input" name="name" placeholder="Support / Recruiting / Moderation" required>
          <input class="input" name="color" placeholder="#6366f1">
          <textarea class="ta span2" name="description" placeholder="Aufgabe und Zuständigkeit"></textarea>
          <select class="select" name="leader_id"><option value="">Keine Leitung</option>{opts}</select>
          <button class="btn primary">Abteilung erstellen</button></form></div><div class="grid g2" style="margin-top:13px">{"".join(cards) or card("<div class=tiny>Keine Abteilungen.</div>")}</div>"""
        return page(ctx, "departments", "Abteilungen", "Organigramm, Zuständigkeiten und Teamzuordnung.", body)

    @app.post("/ultimate/departments/create")
    async def ultimate_department_create(
        request: Request, name: str = Form(...), description: str = Form(""),
        color: str = Form("#6366f1"), leader_id: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color.strip()):
            color = "#6366f1"
        try:
            with cx() as c:
                c.execute(
                    "INSERT INTO ultimate_departments VALUES(?,?,?,?,?,?,?)",
                    (uid("dep"), name.strip()[:80], description[:1000], color,
                     leader_id[:32] or None, 1, iso()),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Diese Abteilung existiert bereits.")
        audit(ctx, "Abteilung erstellt", "", name.strip(), "")
        return RedirectResponse("/ultimate/departments", status_code=303)

    @app.get("/ultimate/departments/{department_id}", response_class=HTMLResponse)
    async def ultimate_department_detail(request: Request, department_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            dep = c.execute("SELECT * FROM ultimate_departments WHERE id=?", (department_id,)).fetchone()
            rows = c.execute(
                "SELECT user_id,role FROM ultimate_department_members WHERE department_id=? ORDER BY joined_at",
                (department_id,),
            ).fetchall()
        if not dep:
            raise HTTPException(404, "Abteilung nicht gefunden.")
        member_map = {str(m.id): m for m in team_members(ctx.guild)}
        people = "".join(
            f"<div class='row'><b>{esc(member_map.get(str(x['user_id'])).display_name if member_map.get(str(x['user_id'])) else x['user_id'])}</b><span class='pill'>{esc(x['role'])}</span><form method='post' action='/ultimate/departments/{esc(department_id)}/remove'><input type='hidden' name='user_id' value='{esc(x['user_id'])}'><button class='btn danger'>Entfernen</button></form></div>"
            for x in rows
        )
        opts = "".join(f"<option value='{m.id}'>{esc(m.display_name)}</option>" for m in team_members(ctx.guild))
        body = f"""<div class="card"><h3>👥 Mitglied hinzufügen</h3><form method="post" action="/ultimate/departments/{esc(department_id)}/add" class="form">
          <select class="select" name="user_id" required>{opts}</select><select class="select" name="role"><option>member</option><option>lead</option><option>specialist</option></select><button class="btn primary">Hinzufügen</button></form></div>
          {card(f"<h3>{esc(dep['name'])}</h3><p class='tiny'>{esc(dep['description'])}</p>{people or '<div class=tiny>Keine Mitglieder.</div>'}")}"""
        return page(ctx, "departments", f"Abteilung · {dep['name']}", "Mitglieder und Zuständigkeiten.", body)

    @app.post("/ultimate/departments/{department_id}/add")
    async def ultimate_department_add(
        request: Request, department_id: str, user_id: str = Form(...),
        role: str = Form("member"), user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        if role not in {"member", "lead", "specialist"}:
            raise HTTPException(400, "Ungültige Abteilungsrolle.")
        with cx() as c:
            dep = c.execute("SELECT 1 FROM ultimate_departments WHERE id=? AND active=1", (department_id,)).fetchone()
            if not dep:
                raise HTTPException(404, "Abteilung nicht gefunden.")
            c.execute(
                "INSERT OR REPLACE INTO ultimate_department_members VALUES(?,?,?,?)",
                (department_id, str(user_id), role, iso()),
            )
        audit(ctx, "Abteilungsmitglied hinzugefügt", user_id, "", f"{department_id}/{role}")
        return RedirectResponse(f"/ultimate/departments/{department_id}", status_code=303)

    @app.post("/ultimate/departments/{department_id}/remove")
    async def ultimate_department_remove(
        request: Request, department_id: str, user_id: str = Form(...),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            c.execute(
                "DELETE FROM ultimate_department_members WHERE department_id=? AND user_id=?",
                (department_id, str(user_id)),
            )
        audit(ctx, "Abteilungsmitglied entfernt", user_id, "", department_id)
        return RedirectResponse(f"/ultimate/departments/{department_id}", status_code=303)

    @app.get("/ultimate/calendar", response_class=HTMLResponse)
    async def ultimate_calendar(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        with cx() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM ultimate_calendar WHERE start_at>=? ORDER BY start_at LIMIT 100",
                (iso(),),
            ).fetchall()]
        items = "".join(
            card(f"<div class=row><div><b>{esc(x['title'])}</b><div class=tiny>{esc(x['kind'])} · {esc(x['start_at'])}</div></div><form method='post' action='/ultimate/calendar/{esc(x['id'])}/delete' style='margin:0'><button class='btn' type='submit'>Löschen</button></form></div><div class=tiny>{esc(x['description'])}</div>")
            for x in rows
        )
        body = f"""<div class="card"><h3>🗓 Termin erstellen</h3><form class="form" method="post" action="/ultimate/calendar/create">
          <input class="input" name="title" placeholder="Meeting / Schulung / Event" required><input class="input" name="kind" placeholder="meeting">
          <input class="input" name="start_at" placeholder="2026-10-05T19:00:00+02:00" required><input class="input" name="end_at" placeholder="optional">
          <input class="input span2" name="location" placeholder="Ort / Discord-Kanal">
          <textarea class="ta span2" name="description" placeholder="Beschreibung"></textarea>
          <button class="btn primary span2">Termin speichern</button></form></div>
          <div class="grid g2" style="margin-top:13px">{items or card("<div class=tiny>Keine kommenden Termine.</div>")}</div>"""
        return page(ctx, "calendar", "Team-Kalender", "Meetings, Schulungen, Events, Fristen und Übergaben.", body)

    @app.post("/ultimate/calendar/create")
    async def ultimate_calendar_create(
        request: Request, title: str = Form(...), kind: str = Form("event"),
        start_at: str = Form(...), end_at: str = Form(""),
        location: str = Form(""), description: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        try:
            datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Ungültiges Startdatum.")
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_calendar VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid("cal"), title.strip()[:160], kind[:40], start_at.strip()[:80],
                 end_at.strip()[:80] or None, description[:2000], location[:300],
                 None, str(ctx.user["id"]),
                 ctx.user.get("global_name") or ctx.user.get("username") or "Team", iso()),
            )
        audit(ctx, "Kalendertermin erstellt", "", title.strip(), start_at)
        return RedirectResponse("/ultimate/calendar", status_code=303)

    @app.get("/ultimate/calendar/{event_id}/delete")
    async def ultimate_calendar_delete_link(request: Request, event_id: str, user_session: str = Cookie(None)):
        # Old bookmarks/links must not delete anything: deleting via GET is CSRF-prone.
        ctx_auth(request, user_session, manager=True)
        return RedirectResponse("/ultimate/calendar", status_code=303)

    @app.post("/ultimate/calendar/{event_id}/delete")
    async def ultimate_calendar_delete(request: Request, event_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            c.execute("DELETE FROM ultimate_calendar WHERE id=?", (event_id,))
        audit(ctx, "Kalendertermin gelöscht", event_id, "", "")
        return RedirectResponse("/ultimate/calendar", status_code=303)

    @app.get("/ultimate/polls", response_class=HTMLResponse)
    async def ultimate_polls(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        with cx() as c:
            c.execute(
                "UPDATE ultimate_polls SET active=0 WHERE active=1 AND ends_at IS NOT NULL AND ends_at != '' AND ends_at <= ?",
                (iso(),),
            )
            polls = [dict(r) for r in c.execute(
                "SELECT * FROM ultimate_polls WHERE active=1 ORDER BY created_at DESC LIMIT 100"
            ).fetchall()]
        cards = []
        for p in polls:
            options = json.loads(p["options_json"])
            result = poll_results(p["id"])
            opts = ""
            for i, option in enumerate(options):
                opts += f"<label class='row'><input type='checkbox' name='option' value='{i}'> {esc(option)} <span class='pill'>{result.get(i,0)}</span></label>"
            cards.append(card(f"<h3>{esc(p['title'])}</h3><div class='tiny'>{esc(p['description'])}</div><form method='post' action='/ultimate/polls/{p['id']}/vote'>{opts}<button class='btn primary'>Abstimmen</button></form>"))
        create_form = ""
        if ctx.perms.get("can_promote") or ctx.perms.get("is_admin"):
            create_form = """<div class="card"><h3>🗳 Abstimmung erstellen</h3><form class="form" method="post" action="/ultimate/polls/create">
            <input class="input" name="title" placeholder="Frage" required><input class="input" name="poll_type" value="single">
            <input class="input span2" name="options" placeholder="Option A | Option B | Option C" required>
            <input class="input" name="ends_at" placeholder="optional ISO-Datum"><label><input type="checkbox" name="anonymous"> anonym</label>
            <button class="btn primary span2">Erstellen</button></form></div>"""
        body = f"{create_form}<div class='grid g2' style='margin-top:13px'>{''.join(cards) or card('<div class=tiny>Keine aktiven Abstimmungen.</div>')}</div>"
        return page(ctx, "polls", "Abstimmungen", "Teamentscheidungen, Feedback und Beteiligung.", body)

    @app.post("/ultimate/polls/create")
    async def ultimate_poll_create(
        request: Request, title: str = Form(...), poll_type: str = Form("single"),
        options: str = Form(...), ends_at: str = Form(""),
        anonymous: bool = Form(False), user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        opts = [x.strip()[:200] for x in options.split("|") if x.strip()]
        if poll_type not in _POLL_TYPES or len(opts) < 2 or len(opts) > 10:
            raise HTTPException(400, "Bitte 2 bis 10 Optionen angeben.")
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_polls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid("poll"), title.strip()[:160], "", poll_type, json.dumps(opts, ensure_ascii=False),
                 ends_at.strip()[:80] or None, int(anonymous), str(ctx.user["id"]),
                 ctx.user.get("global_name") or ctx.user.get("username") or "Team", iso(), 1),
            )
        audit(ctx, "Abstimmung erstellt", "", title.strip(), "")
        return RedirectResponse("/ultimate/polls", status_code=303)

    @app.post("/ultimate/polls/{poll_id}/vote")
    async def ultimate_poll_vote(request: Request, poll_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        form = await request.form()
        choices = form.getlist("option")
        with cx() as c:
            p = c.execute("SELECT * FROM ultimate_polls WHERE id=? AND active=1", (poll_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Abstimmung nicht gefunden.")
        if p["ends_at"]:
            try:
                if datetime.fromisoformat(str(p["ends_at"]).replace("Z","+00:00")) <= utcnow():
                    with cx() as c:
                        c.execute("UPDATE ultimate_polls SET active=0 WHERE id=?", (poll_id,))
                    raise HTTPException(410, "Diese Abstimmung ist bereits beendet.")
            except ValueError:
                pass
        options = json.loads(p["options_json"])
        selected = sorted({int(x) for x in choices if str(x).isdigit() and int(x) < len(options)})
        if not selected:
            raise HTTPException(400, "Keine Option ausgewählt.")
        if p["poll_type"] == "single":
            selected = selected[:1]
        with cx() as c:
            c.execute("DELETE FROM ultimate_poll_votes WHERE poll_id=? AND user_id=?", (poll_id, str(ctx.user["id"])))
            for idx in selected:
                c.execute(
                    "INSERT INTO ultimate_poll_votes VALUES(?,?,?,?)",
                    (poll_id, str(ctx.user["id"]), idx, iso()),
                )
        audit(ctx, "Abstimmung abgegeben", poll_id, "", f"Optionen={selected}")
        return RedirectResponse("/ultimate/polls", status_code=303)

    @app.get("/ultimate/feedback", response_class=HTMLResponse)
    async def ultimate_feedback(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        can_manage = ctx.perms.get("can_promote") or ctx.perms.get("is_admin")
        with cx() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM ultimate_feedback ORDER BY created_at DESC LIMIT 200"
            ).fetchall()] if can_manage else []
        list_html = "".join(
            card(f"""<div class=row><div><b>{esc(x['title'])}</b><div class='tiny'>{esc(x['category'])} · {esc(x['author_name'] or 'Anonym')}</div></div><span class='pill'>{esc(x['status'])}</span></div>
            <p class='tiny'>{esc(x['content'][:500])}</p>
            {f"<form method='post' action='/ultimate/api/feedback/{x['id']}' class='form'><select class='select' name='status'>{''.join(f"<option {'selected' if s==x['status'] else ''}>{s}</option>" for s in _FEEDBACK_STATUS)}</select><input class='input' name='manager_note' value='{esc(x['manager_note'])}' placeholder='Interne Antwort / Notiz'><button class='btn primary span2'>Status speichern</button></form>" if can_manage else ""}""")
            for x in rows
        )
        body = f"""<div class="card"><h3>💬 Feedback senden</h3><form class="form" method="post" action="/ultimate/feedback/create">
          <input class="input" name="title" placeholder="Titel" required><input class="input" name="category" value="general">
          <textarea class="ta span2" name="content" placeholder="Was möchtest du verbessern?" required></textarea>
          <label class='span2'><input type='checkbox' name='anonymous'> anonym senden</label><button class='btn primary span2'>Absenden</button></form></div>
          {f"<div class='grid g2' style='margin-top:13px'>{list_html or card('<div class=tiny>Kein Feedback.</div>')}</div>" if can_manage else ""}"""
        return page(ctx, "feedback", "Feedback", "Anonyme oder normale Verbesserungsvorschläge und Führungsauswertung.", body)

    @app.post("/ultimate/feedback/create")
    async def ultimate_feedback_create(
        request: Request, title: str = Form(...), category: str = Form("general"),
        content: str = Form(...), anonymous: bool = Form(False),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session)
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid("fb"), category[:50], title.strip()[:160], content.strip()[:4000], int(anonymous),
                 None if anonymous else str(ctx.user["id"]),
                 "Anonym" if anonymous else (ctx.user.get("global_name") or ctx.user.get("username") or "Team"),
                 "new", "", iso(), iso()),
            )
        db.notify(ctx.user["id"], "💬 Feedback gespeichert", "Danke für dein Feedback.", "success", "/ultimate/feedback")
        return RedirectResponse("/ultimate/feedback", status_code=303)

    @app.get("/ultimate/handovers", response_class=HTMLResponse)
    async def ultimate_handovers(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        with cx() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM ultimate_handovers WHERE status='open' ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, created_at DESC"
            ).fetchall()]
        team = team_members(ctx.guild)
        opts = "".join(f"<option value='{m.id}'>{esc(m.display_name)}</option>" for m in team if str(m.id) != str(ctx.user["id"]))
        items = "".join(
            card(f"<div class=row><b>{esc(x['title'])}</b><span class='pill'>{esc(x['priority'])}</span></div><p class='tiny'>{esc(x['content'])}</p><div class='tiny'>Von {esc(x['from_name'])} → {esc(x['to_name'] or 'offen')}</div><form method='post' action='/ultimate/handovers/{x['id']}/close'><button class='btn'>Archivieren</button></form>")
            for x in rows
        )
        body = f"""<div class="card"><h3>🧭 Übergabe erstellen</h3><form class="form" method="post" action="/ultimate/handovers/create">
          <input class="input" name="title" placeholder="Übergabe-Titel" required><select class="select" name="priority"><option>normal</option><option>high</option><option>urgent</option></select>
          <select class="select" name="to_id"><option value="">Keine feste Person</option>{opts}</select>
          <textarea class="ta span2" name="content" placeholder="Was muss die nächste Person wissen?" required></textarea>
          <button class="btn primary span2">Übergabe speichern</button></form></div>
          <div class="grid g2" style="margin-top:13px">{items or card('<div class=tiny>Keine offenen Übergaben.</div>')}</div>"""
        return page(ctx, "handover", "Übergaben", "Saubere Übergaben zwischen Schichten und Führungskräften.", body)

    @app.post("/ultimate/handovers/create")
    async def ultimate_handover_create(
        request: Request, title: str = Form(...), content: str = Form(...),
        priority: str = Form("normal"), to_id: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session)
        target = ctx.guild.get_member(int(to_id)) if to_id.isdigit() else None
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_handovers VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid("ho"), title.strip()[:160], content.strip()[:5000], priority,
                 str(ctx.user["id"]), ctx.user.get("global_name") or ctx.user.get("username") or "Team",
                 str(target.id) if target else None, target.display_name if target else "",
                 "open", iso(), None),
            )
        if target:
            db.notify(target.id, "🧭 Neue Übergabe", title.strip(), "warning", "/ultimate/handovers")
        audit(ctx, "Übergabe erstellt", "", title.strip(), f"An {target.display_name if target else 'offen'}")
        return RedirectResponse("/ultimate/handovers", status_code=303)

    @app.post("/ultimate/handovers/{handover_id}/close")
    async def ultimate_handover_close(request: Request, handover_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        with cx() as c:
            closed = c.execute("UPDATE ultimate_handovers SET status='closed',archived_at=? WHERE id=? AND status='open'",
                               (iso(), handover_id)).rowcount
        if not closed:  # M-A1-2: unknown or already archived – no second "archiviert" audit entry
            raise HTTPException(status_code=404, detail="Übergabe nicht gefunden oder bereits archiviert.")
        audit(ctx, "Übergabe archiviert", handover_id)
        return RedirectResponse("/ultimate/handovers", status_code=303)

    @app.get("/ultimate/awards", response_class=HTMLResponse)
    async def ultimate_awards(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        team = sorted(team_members(ctx.guild), key=lambda m: m.display_name.lower())
        opts = "".join(f"<option value='{m.id}'>{esc(m.display_name)}</option>" for m in team)
        with cx() as c:
            rows = c.execute("SELECT * FROM ultimate_awards ORDER BY created_at DESC LIMIT 200").fetchall()
        body = f"""<div class="card"><h3>🏆 Award vergeben</h3><form class="form" method="post" action="/ultimate/awards/create">
          <select class="select" name="user_id" required>{opts}</select><input class="input" name="icon" value="🏆">
          <input class="input" name="title" placeholder="Mitarbeiter des Monats" required><input class="input" name="period" placeholder="Oktober 2026">
          <textarea class="ta span2" name="description" placeholder="Begründung"></textarea><button class="btn primary span2">Award vergeben</button></form></div>
          <div class='grid g3' style='margin-top:13px'>{''.join(card(f"<div class=metric>{esc(x['icon'])}</div><b>{esc(x['title'])}</b><div class=tiny>{esc(x['description'])} · {esc(x['period'])}</div>") for x in rows) or card("<div class=tiny>Keine Awards.</div>")}</div>"""
        return page(ctx, "awards", "Awards & Auszeichnungen", "Mitarbeiter des Monats, Meilensteine und besondere Leistungen.", body)

    @app.post("/ultimate/awards/create")
    async def ultimate_award_create(
        request: Request, user_id: str = Form(...), title: str = Form(...),
        description: str = Form(""), icon: str = Form("🏆"), period: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_awards VALUES(?,?,?,?,?,?,?,?,?)",
                (uid("award"), str(user_id), title.strip()[:160], description[:1000],
                 icon[:4] or "🏆", period[:80], str(ctx.user["id"]),
                 ctx.user.get("global_name") or ctx.user.get("username") or "Team", iso()),
            )
        db.notify(user_id, "🏆 Neue Auszeichnung", title.strip(), "success", "/ultimate/team/" + str(user_id))
        audit(ctx, "Award vergeben", user_id, title.strip(), period)
        return RedirectResponse("/ultimate/awards", status_code=303)

    # ------------------------------------------------------------------ automations (one engine, 7.18 editor)
    LEGACY_ACTIONS = ["notify_managers", "notify_team", "create_case", "create_task", "team_report", "notify_user",
                      "ticket_sla_alert", "inactivity_report", "probation_report"]

    def automation_view(row: dict) -> dict:
        return {"trigger_type": row.get("trigger_type"), "steps": automation_steps(row), "action_type": row.get("action_type"),
                "trigger_config": automation_trigger_config(row), "interval_minutes": row.get("interval_minutes"),
                "test_mode": bool(row.get("test_mode")),
                "condition": (json.loads(row.get("action_payload") or "{}") or {}).get("condition") if str(row.get("action_payload") or "").startswith("{") else None}

    def is_admin(ctx) -> bool:
        return bool(ctx.perms.get("is_admin"))

    def load_automation(automation_id: str, ctx) -> dict:
        with cx() as c:
            row = c.execute("SELECT * FROM ultimate_automations WHERE id=?", (automation_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Automation nicht gefunden.")
        record = dict(row)
        if _rules().needs_admin(automation_view(record)) and not is_admin(ctx):
            raise HTTPException(403, "Sicherheits-Automationen sind nur für Administratoren.")
        return record

    def refresh_triggers() -> None:
        try:
            refresh_event_triggers()
        except Exception as exc:
            print(f"⚠️ Ereignis-Automationen konnten nicht aktualisiert werden: {type(exc).__name__}")

    def antinuke_rule_labels() -> dict:
        import webserver
        import pulse_moderation as pm
        cfg = pm.normalize_antinuke(webserver.load_config().get("pulse_antinuke"))
        return {"global": "Globale Standardregel", **{r["id"]: r["label"] for r in cfg["rules"]}}

    def editor(ctx, record: dict | None) -> str:
        rules = _rules()
        import pulse_moderation as pm
        view = automation_view(record) if record else {"trigger_type": "interval", "steps": [], "trigger_config": {},
                                                       "interval_minutes": 1440, "condition": None, "test_mode": False}
        if record and not view["steps"] and record.get("action_type"):  # legacy → editable as one step
            try:
                legacy_payload = json.loads(record.get("action_payload") or "{}")
            except ValueError:
                legacy_payload = {}
            view["steps"] = [{"type": record["action_type"], **{k: v for k, v in legacy_payload.items() if k != "condition"}}]
        admin = is_admin(ctx)
        cfg = view["trigger_config"]
        cond = view.get("condition") or {"type": "always"}
        triggers = "".join(f"<option value='{k}'{' selected' if view['trigger_type'] == k else ''}>{esc(v)}</option>"
                           for k, v in rules.TRIGGERS.items() if admin or k not in rules.ADMIN_TRIGGERS)
        conditions = "".join(f"<option value='{k}'{' selected' if cond.get('type') == k else ''}>{esc(v)}</option>" for k, v in rules.CONDITIONS.items())
        labels = antinuke_rule_labels() if admin else {}
        rule_opts = "<option value=''>— jede Anti-Nuke-Regel —</option>" + "".join(
            f"<option value='{esc(k)}'{' selected' if cfg.get('rule_id') == k else ''}>{esc(v)}</option>" for k, v in labels.items())
        event_boxes = "".join(
            f"<label class='tiny' style='display:inline-flex;gap:5px;margin:3px 10px 3px 0'><input type='checkbox' name='events' value='{k}'"
            f"{' checked' if k in (cfg.get('events') or []) else ''}>{esc(v)}</label>" for k, v in pm.ANTINUKE_EVENT_LABELS.items())
        step_rows = []
        for i in range(1, rules.MAX_STEPS + 1):
            step = view["steps"][i - 1] if i <= len(view["steps"]) else {}
            types = "<option value=''>— keine —</option>" + "".join(
                f"<option value='{k}'{' selected' if step.get('type') == k else ''}>{esc(v)}</option>"
                for k, v in rules.STEP_TYPES.items() if admin or k not in rules.ADMIN_STEP_TYPES)
            prio = "".join(f"<option{' selected' if step.get('priority', 'normal') == p else ''}>{p}</option>" for p in rules.PRIORITIES)
            body = step.get("body") or step.get("text") or step.get("description") or ""
            number = step.get("minutes") or step.get("days") or ""
            step_rows.append(
                f"<div class='card' style='padding:10px;margin-top:8px'><b class='tiny'>Aktion {i}</b><div class='form' style='margin-top:6px'>"
                f"<select class='select' name='step{i}_type' aria-label='Aktion {i}'>{types}</select>"
                f"<input class='input' name='step{i}_title' maxlength='160' placeholder='Titel (Benachrichtigung, Aufgabe, Fall)' value='{esc(step.get('title', ''))}'>"
                f"<input class='input span2' name='step{i}_body' maxlength='1000' placeholder='Text / Beschreibung – Platzhalter: {{actor_id}} {{rule_label}} {{events}} {{result}} {{name}}' value='{esc(body)}'>"
                f"<input class='input' name='step{i}_target' maxlength='22' placeholder='Discord-ID (Person / zuständig)' value='{esc(step.get('user_id') or step.get('assignee_id') or '')}'>"
                f"<input class='input' type='number' name='step{i}_number' min='1' max='1440' placeholder='Minuten (SLA) / Tage (Berichte)' value='{esc(number)}'>"
                f"<select class='select' name='step{i}_priority' aria-label='Priorität'>{prio}</select>"
                f"<label class='tiny'><input type='checkbox' name='step{i}_urgent' value='1'{' checked' if step.get('urgent') else ''}> dringend</label>"
                "</div></div>")
        enabled = record is None or bool(record.get("enabled"))
        return f"""<div class="card" id="editor"><h3>{'✏️ Automation bearbeiten' if record else '🤖 Neue Automation'}</h3>
<form class="form" method="post" action="/ultimate/automations/save">
 <input type="hidden" name="automation_id" value="{esc(record['id'] if record else '')}">
 <input class="input span2" name="name" maxlength="160" required placeholder="Name, z. B. Anti-Nuke – Massenlöschung" value="{esc(record['name'] if record else '')}">
 <label class="tiny span2"><b>WENN</b> – Auslöser</label>
 <select class="select" name="trigger_type" aria-label="Auslöser">{triggers}</select>
 <input class="input" type="number" name="interval_minutes" min="5" max="10080" value="{esc(view.get('interval_minutes') or 1440)}" aria-label="Intervall in Minuten" placeholder="Intervall (Minuten, nur Zeitplan)">
 {"" if not admin else f'''<div class="span2 tiny" style="padding:8px;border:1px dashed var(--border);border-radius:10px">
  <b>Nur Anti-Nuke-Regel:</b> <select class="select" name="rule_id" aria-label="Anti-Nuke-Regel">{rule_opts}</select>
  <label><input type="checkbox" name="include_dry_run" value="1"{" checked" if cfg.get("include_dry_run", True) else ""}> auch im Alarm-/Testmodus</label><br>
  <b>Nur Sicherheitsereignis:</b> {event_boxes}<br>
  mindestens <input class="input" style="max-width:90px;display:inline-block" type="number" name="min_count" min="1" max="50" value="{esc(cfg.get("min_count", 1))}"> Ereignisse in
  <input class="input" style="max-width:90px;display:inline-block" type="number" name="window_seconds" min="3" max="3600" value="{esc(cfg.get("window_seconds", 10))}"> s,
  <select class="select" name="group" style="max-width:220px;display:inline-block"><option value="actor"{" selected" if cfg.get("group", "actor") == "actor" else ""}>gleiche verantwortliche Person</option><option value="all"{" selected" if cfg.get("group") == "all" else ""}>alle zusammen</option></select>
  · Cooldown <input class="input" style="max-width:100px;display:inline-block" type="number" name="cooldown_seconds" min="0" max="86400" value="{esc(cfg.get("cooldown_seconds", 60))}"> s</div>'''}
 <label class="tiny span2"><b>UND</b> – Bedingung (Zeitplan)</label>
 <select class="select" name="condition_type" aria-label="Bedingung">{conditions}</select>
 <input class="input" type="number" name="condition_value" min="0" max="100000" value="{esc(cond.get('value', 0))}" aria-label="Bedingungswert">
 <label class="tiny span2"><b>DANN</b> – Aktionen (werden nacheinander genau einmal ausgeführt; ein Fehler stoppt die übrigen nicht)</label>
 <div class="span2">{"".join(step_rows)}</div>
 <label class="tiny"><input type="checkbox" name="enabled" value="1"{" checked" if enabled else ""}> aktiv</label>
 <label class="tiny"><input type="checkbox" name="test_mode" value="1"{" checked" if view.get('test_mode') else ""}> Testmodus (nur protokollieren)</label>
 <button class="btn primary span2">💾 Automation speichern</button></form>
 <div class="tiny" style="margin-top:8px">Nur Auslöser und Aktionen aus dieser Liste sind möglich – kein freier Code. Sicherheits-Auslöser und -Aktionen sind Administratoren vorbehalten.</div></div>"""

    @app.get("/ultimate/automations", response_class=HTMLResponse)
    async def ultimate_automations(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        rules = _rules()
        q = request.query_params
        f_trigger, f_action, f_status = q.get("trigger", ""), q.get("action", ""), q.get("status", "")
        sort = q.get("sort", "next") if q.get("sort") in ("next", "name", "last_run", "status") else "next"
        with cx() as c:
            rows = [dict(r) for r in c.execute(
                """SELECT a.*,
                    (SELECT r.status FROM ultimate_automation_runs r
                     WHERE r.automation_id=a.id ORDER BY r.started_at DESC LIMIT 1) AS last_status,
                    (SELECT r.result FROM ultimate_automation_runs r
                     WHERE r.automation_id=a.id ORDER BY r.started_at DESC LIMIT 1) AS last_result
                   FROM ultimate_automations a
                   ORDER BY a.enabled DESC,a.next_run_at"""
            ).fetchall()]
            run_rows = [dict(r) for r in c.execute(
                """SELECT r.*,a.name AS automation_name,a.trigger_type AS automation_trigger,a.steps_json AS automation_steps,
                          a.action_type AS automation_action
                   FROM ultimate_automation_runs r
                   LEFT JOIN ultimate_automations a ON a.id=r.automation_id
                   ORDER BY r.started_at DESC LIMIT 120"""
            ).fetchall()]
        admin = is_admin(ctx)
        visible = [x for x in rows if admin or not rules.needs_admin(automation_view(x))]
        if f_trigger:
            visible = [x for x in visible if x["trigger_type"] == f_trigger]
        if f_action:
            visible = [x for x in visible if f_action in {s.get("type") for s in automation_steps(x)} | {x.get("action_type")}]
        if f_status:
            visible = [x for x in visible if (x.get("last_status") or "never") == f_status
                       or (f_status == "disabled" and not x["enabled"]) or (f_status == "test" and x.get("test_mode"))]
        order = {"error": 0, "test": 1, "skipped": 2, "success": 3}
        if sort == "name":
            visible.sort(key=lambda x: str(x["name"]).casefold())
        elif sort == "last_run":
            visible.sort(key=lambda x: str(x.get("last_run_at") or ""), reverse=True)
        elif sort == "status":
            visible.sort(key=lambda x: order.get(x.get("last_status") or "", 4))
        labels = antinuke_rule_labels() if admin else {}
        status_pill = {"success": "✅ erfolgreich", "error": "❌ Fehler", "test": "🧪 Testlauf", "skipped": "⏭️ übersprungen"}
        cards = "".join(
            card(
                f"<div class=row><b>{esc(x['name'])}</b><span class='pill'>{'⏸ Deaktiviert' if not x['enabled'] else ('🧪 Testmodus' if x.get('test_mode') else '✅ Aktiv')}</span></div>"
                f"<div class='tiny' style='margin-top:4px'>{esc(rules.describe({**automation_view(x)}, labels))}</div>"
                f"<div class='tiny'>{esc(rules.TRIGGERS.get(x['trigger_type'], x['trigger_type']))}"
                + (f" · alle {x['interval_minutes']} min · nächster Lauf {esc(x['next_run_at'])}" if x['trigger_type'] not in rules.EVENT_TRIGGERS else "")
                + (" · Legacy-JSON" if not automation_steps(x) else "") + "</div>"
                f"<div class='tiny' style='margin-top:6px'>Letzter Lauf: {esc(x.get('last_run_at') or 'Noch nicht ausgeführt')} · "
                f"{status_pill.get(x.get('last_status'), '—')}</div>"
                f"<div class='tiny'>{esc(str(x.get('last_result') or '')[:220])}</div>"
                f"<div style='display:flex;flex-wrap:wrap;gap:6px;margin-top:10px'>"
                f"<a class='btn' href='/ultimate/automations?edit={esc(x['id'])}#editor'>✏️ Bearbeiten</a>"
                f"<form method='post' action='/ultimate/automations/{esc(x['id'])}/toggle'>"
                f"<button class='btn {'danger' if x['enabled'] else 'primary'}'>{'⏸ Automation pausieren' if x['enabled'] else '▶ Automation fortsetzen'}</button></form>"
                f"<form method='post' action='/ultimate/automations/{esc(x['id'])}/test'><button class='btn'>🧪 Testlauf</button></form>"
                f"<form method='post' action='/ultimate/automations/{esc(x['id'])}/run' onsubmit=\"if(!window.confirm('Automation jetzt wirklich ausführen? Die Aktionen werden tatsächlich ausgeführt.'))return false;this.confirm.value='1';\">"
                f"<input type='hidden' name='confirm' value='0'><button class='btn'>▶ Jetzt ausführen</button></form>"
                f"<form method='post' action='/ultimate/automations/{esc(x['id'])}/delete' onsubmit=\"if(!window.confirm('Automation „{esc(x['name'])}“ endgültig löschen? Der Ausführungsverlauf bleibt erhalten.'))return false;this.confirm.value='1';\">"
                f"<input type='hidden' name='confirm' value='0'><button class='btn danger'>🗑 Löschen</button></form></div>"
            )
            for x in visible
        )
        visible_ids = {x["id"] for x in rows if admin or not rules.needs_admin(automation_view(x))}
        run_rows = [r for r in run_rows if r["automation_id"] in visible_ids or (admin and r["automation_id"] not in {x["id"] for x in rows})][:30]

        def run_steps(r):
            try:
                steps = json.loads(r.get("steps_json") or "[]")
            except ValueError:
                steps = []
            icons = {"ok": "✅", "failed": "❌", "skipped": "⏭️", "test": "🧪", "success": "✅", "error": "❌"}
            return "".join(f"<li>{icons.get(s.get('status'), '•')} {esc(rules.STEP_TYPES.get(s.get('type'), s.get('type')))}: {esc(s.get('detail', ''))}</li>"
                           for s in steps if isinstance(s, dict))

        def run_trigger(r):
            try:
                info = json.loads(r.get("trigger_info") or "{}")
            except ValueError:
                info = {}
            if not isinstance(info, dict) or not info:
                return ""
            bits = [rules.TRIGGERS.get(info.get("trigger"), {"manual": "manuell", "manual_test": "Testlauf"}.get(info.get("trigger"), info.get("trigger") or ""))]
            if info.get("rule_label"):
                bits.append(f"Regel „{info['rule_label']}“")
            if info.get("events"):
                bits.append("Ereignis " + ", ".join(info["events"]) if isinstance(info["events"], list) else str(info["events"]))
            if info.get("actor_id"):
                bits.append(f"Akteur {info['actor_id']}")
            return " · ".join(str(b) for b in bits if b)
        def run_name(r):
            if r.get("automation_name"):
                return r["automation_name"]
            try:
                info = json.loads(r.get("trigger_info") or "{}")
            except ValueError:
                info = {}
            return (info.get("automation_name") if isinstance(info, dict) else None) or r["automation_id"]

        def run_html(r):
            meta = status_pill.get(r["status"], r["status"])
            meta += f" · beendet: {esc(r.get('finished_at') or '—')}"
            if r.get("duration_ms"):
                meta += f" · {int(r['duration_ms'])} ms"
            if r.get("actor_name"):
                meta += f" · durch {esc(r['actor_name'])}"
            trig, steps = run_trigger(r), run_steps(r)
            out = f"<div class='event'><b>{esc(r['started_at'])}</b> · {esc(run_name(r))}<div class='tiny'>{meta}</div>"
            if trig:
                out += f"<div class='tiny'>Auslöser: {esc(trig)}</div>"
            if steps:
                out += f"<ul class='tiny' style='margin:4px 0 0 16px'>{steps}</ul>"
            else:
                out += f"<div>{esc(str(r.get('result') or '')[:500])}</div>"
            return out + "</div>"
        run_history = "".join(run_html(r) for r in run_rows)
        failing = [x for x in visible if x.get("last_status") == "error"]
        problems = "".join(f"<div class='tiny'>❌ {esc(x['name'])}: {esc(str(x.get('last_result') or '')[:160])}</div>" for x in failing)
        if admin:
            known_rules = set(labels)
            for x in rows:
                cfg = automation_trigger_config(x)
                if x["trigger_type"] == "antinuke_rule" and cfg.get("rule_id") and cfg["rule_id"] not in known_rules:
                    problems += f"<div class='tiny'>⚠ {esc(x['name'])}: Anti-Nuke-Regel {esc(cfg['rule_id'])} existiert nicht mehr.</div>"
        for x in visible:
            if not automation_steps(x) and x.get("action_type") not in LEGACY_ACTIONS:
                problems += f"<div class='tiny'>⚠ {esc(x['name'])}: unbekannte Aktion „{esc(x.get('action_type'))}“ – bitte bearbeiten.</div>"
        trig_opts = "<option value=''>alle Auslöser</option>" + "".join(
            f"<option value='{k}'{' selected' if f_trigger == k else ''}>{esc(v)}</option>" for k, v in rules.TRIGGERS.items() if admin or k not in rules.ADMIN_TRIGGERS)
        act_opts = "<option value=''>alle Aktionen</option>" + "".join(
            f"<option value='{k}'{' selected' if f_action == k else ''}>{esc(v)}</option>" for k, v in rules.STEP_TYPES.items() if admin or k not in rules.ADMIN_STEP_TYPES)
        st_opts = "".join(f"<option value='{k}'{' selected' if f_status == k else ''}>{v}</option>" for k, v in (
            ("", "jeder Status"), ("success", "zuletzt erfolgreich"), ("error", "zuletzt fehlgeschlagen"), ("test", "Testmodus/Testlauf"),
            ("skipped", "übersprungen"), ("never", "noch nie ausgeführt"), ("disabled", "deaktiviert")))
        sort_opts = "".join(f"<option value='{k}'{' selected' if sort == k else ''}>{v}</option>" for k, v in (
            ("next", "nächster Lauf"), ("name", "Name"), ("last_run", "letzte Ausführung"), ("status", "letztes Ergebnis (Fehler zuerst)")))
        edit_id = q.get("edit", "")
        record = load_automation(edit_id, ctx) if edit_id else None
        body = f"""{editor(ctx, record)}
          <div class="card" style="margin-top:13px"><form class="form" method="get" action="/ultimate/automations">
           <select class="select" name="trigger" aria-label="Auslöser filtern">{trig_opts}</select>
           <select class="select" name="action" aria-label="Aktion filtern">{act_opts}</select>
           <select class="select" name="status" aria-label="Status filtern">{st_opts}</select>
           <select class="select" name="sort" aria-label="Sortierung">{sort_opts}</select>
           <button class="btn span2">🔎 Filtern &amp; sortieren</button></form>
           {('<div style="margin-top:8px"><b class="tiny">Fehlerprüfung</b>' + problems + '</div>') if problems else '<div class="tiny" style="margin-top:8px">✅ Keine fehlerhaften Automationen gefunden.</div>'}</div>
          <div class="grid g2" style="margin-top:13px">{cards or card("<div class=tiny>Keine Automationen.</div>")}</div>
          <div class="card" style="margin-top:13px"><h3>🧾 Letzte 30 Ausführungen</h3>
          <div class="tiny">Gespeichert werden höchstens {MAX_RUNS_PER_AUTOMATION} Ausführungen pro Automation.</div>
          <div class="timeline">{run_history or "<div class=tiny>Noch keine Ausführungen protokolliert.</div>"}</div></div>"""
        return page(ctx, "automations", "Automationen", "Wenn → Und → Dann: Automationen anlegen, bearbeiten, testen und Ausführungen nachvollziehen.", body)

    def editor_payload(form) -> dict:
        rules = _rules()
        steps = []
        for i in range(1, rules.MAX_STEPS + 1):
            kind = str(form.get(f"step{i}_type") or "").strip()
            if not kind:
                continue
            number = str(form.get(f"step{i}_number") or "").strip() or None
            body = str(form.get(f"step{i}_body") or "")
            target = str(form.get(f"step{i}_target") or "").strip()
            steps.append({"type": kind, "title": form.get(f"step{i}_title") or "", "body": body, "text": body, "description": body,
                          "user_id": target, "assignee_id": target, "minutes": number, "days": number,
                          "priority": form.get(f"step{i}_priority") or "normal", "urgent": form.get(f"step{i}_urgent") == "1"})
        trigger = str(form.get("trigger_type") or "")
        return {
            "name": form.get("name"), "trigger_type": trigger, "interval_minutes": form.get("interval_minutes"),
            "trigger_config": {"rule_id": form.get("rule_id") or "", "include_dry_run": form.get("include_dry_run") == "1",
                               "events": form.getlist("events"), "min_count": form.get("min_count"),
                               "window_seconds": form.get("window_seconds"), "group": form.get("group") or "actor",
                               "cooldown_seconds": form.get("cooldown_seconds")},
            "condition": {"type": form.get("condition_type") or "always", "value": form.get("condition_value")},
            "steps": steps, "enabled": form.get("enabled") == "1", "test_mode": form.get("test_mode") == "1",
        }

    @app.post("/ultimate/automations/save")
    async def ultimate_automation_save(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        form = await request.form()
        automation_id = str(form.get("automation_id") or "").strip()
        existing = load_automation(automation_id, ctx) if automation_id else None  # 403 for security automations
        try:
            value = _rules().normalize_automation(editor_payload(form), is_admin=is_admin(ctx))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            import webserver
            return webserver.back("/ultimate/automations" + (f"?edit={automation_id}" if automation_id else "") + "#editor", str(exc), False)
        name = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
        first = value["steps"][0]["type"]
        payload = json.dumps({"condition": value["condition"]} if value["condition"]["type"] != "always" else {}, ensure_ascii=False)
        next_run = iso(utcnow() + timedelta(minutes=value["interval_minutes"]))
        fields = (value["name"], value["trigger_type"], value["interval_minutes"], first, payload, 1 if value["enabled"] else 0,
                  json.dumps(value["trigger_config"], ensure_ascii=False), json.dumps(value["steps"], ensure_ascii=False),
                  1 if value["test_mode"] else 0, iso(), name)
        with cx() as c:
            if existing:
                c.execute("""UPDATE ultimate_automations SET name=?,trigger_type=?,interval_minutes=?,action_type=?,action_payload=?,
                             enabled=?,trigger_config=?,steps_json=?,test_mode=?,updated_at=?,updated_by_name=?,next_run_at=? WHERE id=?""",
                          (*fields, next_run if existing["trigger_type"] != value["trigger_type"] or existing["interval_minutes"] != value["interval_minutes"]
                           else existing["next_run_at"], automation_id))
            else:
                automation_id = uid("auto")
                c.execute("""INSERT INTO ultimate_automations(name,trigger_type,interval_minutes,action_type,action_payload,enabled,
                             trigger_config,steps_json,test_mode,updated_at,updated_by_name,next_run_at,id,last_run_at,created_by_id,
                             created_by_name,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (*fields, next_run, automation_id, None, str(ctx.user["id"]), name, iso()))
        refresh_triggers()
        audit(ctx, "Automation geändert" if existing else "Automation erstellt", automation_id, value["name"],
              _rules().describe(value, antinuke_rule_labels() if is_admin(ctx) else {}))
        import webserver
        return webserver.back("/ultimate/automations", f"Automation „{value['name']}“ gespeichert" + (" (Testmodus)" if value["test_mode"] else "") + ".", True)

    @app.post("/ultimate/automations/create")
    async def ultimate_automation_create(
        request: Request, name: str = Form(...), trigger_type: str = Form("interval"),
        interval_minutes: int = Form(1440), action_type: str = Form(...),
        payload: str = Form("{}"), user_session: str = Cookie(None),
    ):
        # Kept for existing forms/scripts: one scheduled action with a JSON payload. Only the actions the
        # engine really supports are accepted (an unknown action used to be stored and fail on every run).
        ctx = ctx_auth(request, user_session, manager=True)
        if trigger_type != "interval":
            raise HTTPException(400, "Dieser Weg unterstützt nur den Auslöser „interval“ – Ereignis-Auslöser im Editor anlegen.")
        if action_type not in LEGACY_ACTIONS:
            raise HTTPException(400, "Unbekannte Aktion.")
        try:
            obj = json.loads(payload)
            if not isinstance(obj, dict):
                raise ValueError
        except Exception:
            raise HTTPException(400, "Payload muss gültiges JSON-Objekt sein.")
        next_run = utcnow() + timedelta(minutes=max(5, min(10080, interval_minutes)))
        with cx() as c:
            c.execute(
                """INSERT INTO ultimate_automations(id,name,trigger_type,interval_minutes,action_type,action_payload,enabled,
                   next_run_at,last_run_at,created_by_id,created_by_name,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid("auto"), name.strip()[:160], trigger_type[:40], max(5, min(10080, interval_minutes)),
                 action_type[:60], json.dumps(obj, ensure_ascii=False), 1, iso(next_run), None,
                 str(ctx.user["id"]), ctx.user.get("global_name") or ctx.user.get("username") or "Team", iso()),
            )
        audit(ctx, "Automation erstellt", "", name.strip(), action_type)
        return RedirectResponse("/ultimate/automations", status_code=303)

    @app.post("/ultimate/automations/{automation_id}/toggle")
    async def ultimate_automation_toggle(
        request: Request, automation_id: str, user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        record = load_automation(automation_id, ctx)
        with cx() as c:
            enabled = 0 if record["enabled"] else 1
            next_run_at = (
                iso(utcnow() + timedelta(minutes=max(5, int(record["interval_minutes"]))))
                if enabled else record["next_run_at"]
            )
            c.execute(
                "UPDATE ultimate_automations SET enabled=?,next_run_at=? WHERE id=?",
                (enabled, next_run_at, automation_id),
            )
        refresh_triggers()
        audit(
            ctx, "Automation fortgesetzt" if enabled else "Automation pausiert",
            automation_id, record["name"], f"enabled={enabled}",
        )
        return RedirectResponse("/ultimate/automations", status_code=303)

    @app.post("/ultimate/automations/{automation_id}/delete")
    async def ultimate_automation_delete(request: Request, automation_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        record = load_automation(automation_id, ctx)
        form = await request.form()
        import webserver
        if form.get("confirm") != "1":
            return webserver.back("/ultimate/automations", "Löschen bitte bestätigen.", False)
        with cx() as c:
            c.execute("DELETE FROM ultimate_automations WHERE id=?", (automation_id,))
        refresh_triggers()
        audit(ctx, "Automation gelöscht", automation_id, record["name"], "Ausführungsverlauf bleibt erhalten")
        return webserver.back("/ultimate/automations", f"Automation „{record['name']}“ gelöscht.", True)

    async def manual_run(request, automation_id, user_session, dry_run: bool):
        ctx = ctx_auth(request, user_session, manager=True)
        record = load_automation(automation_id, ctx)
        form = await request.form()
        import webserver
        if not dry_run and form.get("confirm") != "1":
            return webserver.back("/ultimate/automations", "Ausführen bitte bestätigen.", False)
        name = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
        bot = getattr(request.app.state, "bot", None)
        context = {"actor_id": "", "actor_name": name, "rule_label": "manueller Test", "events": [], "result": ""}
        result = await execute_automation(bot, record, trigger_info={"trigger": "manual_test" if dry_run else "manual"},
                                          context=context, actor_name=name, dry_run=dry_run)
        audit(ctx, "Automation getestet" if dry_run else "Automation manuell ausgeführt", automation_id, record["name"],
              f"{result['status']}: {result['summary'][:500]}")
        ok = result["status"] in ("success", "test", "skipped")
        return webserver.back("/ultimate/automations", f"{'Testlauf' if dry_run else 'Ausführung'} „{record['name']}“: "
                              f"{result['status']} – {result['summary'][:300]}", ok)

    @app.post("/ultimate/automations/{automation_id}/test")
    async def ultimate_automation_test(request: Request, automation_id: str, user_session: str = Cookie(None)):
        return await manual_run(request, automation_id, user_session, True)

    @app.post("/ultimate/automations/{automation_id}/run")
    async def ultimate_automation_run(request: Request, automation_id: str, user_session: str = Cookie(None)):
        return await manual_run(request, automation_id, user_session, False)

    @app.get("/ultimate/system", response_class=HTMLResponse)
    async def ultimate_system(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        import webserver
        checks = []
        guild = ctx.guild
        checks.append(("🟢", "Discord", "verbunden" if guild else "nicht verfügbar"))
        pulse_db_ok = False
        try:
            with db.connect() as pulse_conn:
                pulse_conn.execute("SELECT 1").fetchone()
            pulse_db_ok = True
        except Exception:
            pulse_db_ok = False
        checks.append(("🟢" if pulse_db_ok else "🔴", "Pulse DB", "SQLite erreichbar" if pulse_db_ok else "Datenbankfehler"))
        checks.append(("🟢" if webserver.CLIENT_ID and webserver.CLIENT_SECRET else "🟡", "OAuth", "konfiguriert" if webserver.CLIENT_ID and webserver.CLIENT_SECRET else "prüfen"))
        bot_member = guild.me if guild else None
        manage_roles_ok = bool(bot_member and bot_member.guild_permissions.manage_roles)
        checks.append(("🟢" if manage_roles_ok else "🔴", "Manage Roles", "bereit" if manage_roles_ok else "fehlt"))
        try:
            wh = webserver.warning_role_health(guild, webserver.load_config())
            checks.append(("🟢" if all(x["ok"] for x in wh) else "🔴", "Warnrollen", f"{sum(x['ok'] for x in wh)}/5 bereit"))
        except Exception:
            checks.append(("🟡", "Warnrollen", "nicht prüfbar"))
        body = "<div class='grid g2'>" + "".join(card(f"<div class=row><b>{esc(n)}</b><span>{i} {esc(d)}</span></div>") for i,n,d in checks) + "</div>"
        body += card("<h3>🛰 Live-Systemmonitor</h3><div id='pulse-monitor' class='tiny'>Systemdaten werden geladen …</div><div class='tiny' style='margin-top:8px'>Aktualisierung alle 15 Sekunden · nur für berechtigte Führungskräfte</div>")
        body += card(f"<h3>🎨 Branding</h3><form method='post' action='/ultimate/system/branding' class='form'><input class='input' name='name' value='{esc((setting('branding',{}) or {}).get('name','Pulse TeamOS'))}'><input class='input' name='accent' value='{esc((setting('branding',{}) or {}).get('accent','#6366f1'))}'><input class='input span2' name='logo_url' value='{esc((setting('branding',{}) or {}).get('logo_url',''))}' placeholder='Logo URL'><button class='btn primary span2'>Branding speichern</button></form>")
        body += card("<h3>🔑 API-Zugriff</h3><form method='post' action='/ultimate/system/api-key' class='form'><input class='input span2' name='name' placeholder='z.B. Mobile App / externe Website' required><input type='hidden' name='scopes_sent' value='1'>" + "".join(f"<label class='tiny'><input type='checkbox' name='scopes' value='{k}' checked> {esc(v)}</label>" for k, v in API_SCOPES.items()) + "<select class='select span2' name='expires_days' aria-label='Gültigkeit'><option value='30'>30 Tage gültig</option><option value='90'>90 Tage gültig</option><option value='365' selected>1 Jahr gültig</option><option value='0'>unbefristet</option></select><button class='btn primary span2'>API-Key erstellen</button></form><div class='tiny' style='margin-top:8px'>Die v2-Endpunkte nutzen den Header X-Pulse-API-Key. Schlüssel werden nur einmal angezeigt.</div><div style='margin-top:8px'><a class='btn' href='/ultimate/system/api-keys'>🔑 Schlüssel verwalten</a></div>")
        body += card("<h3>🛡 Sicherheitsmaßnahmen</h3><div class='tiny'>Rate Limits, signierte Sessions, Sicherheitsheader, Warnrollen-Healthcheck, Audit-Logs, Backups und Health-Endpunkt sind aktiv.</div>")
        return page(ctx, "system", "System & Branding", "Diagnose, Feature Flags und visuelle Serveranpassungen.", body, """
async function loadPulseMonitor() {
  const out = document.getElementById('pulse-monitor');
  if (!out) return;
  try {
    const response = await fetch('/ultimate/api/monitor', {cache: 'no-store', headers: {'Accept': 'application/json'}});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const d = await response.json();
    const ok = v => v ? '🟢 OK' : '🔴 Störung';
    const mb = v => (Number(v || 0)).toLocaleString('de-DE') + ' MB';
    const rows = [
      ['Zeitpunkt', d.timestamp || '—'],
      ['Bot / Discord', ok(d.discord_ready)],
      ['Pulse TeamOS DB', ok(d.ultimate_db_ok)],
      ['Datenbank-Kern', ok(d.pulse_db_ok)],
      ['RAM des Pulse-Prozesses', d.process_rss_mb == null ? 'Nicht verfügbar' : Number(d.process_rss_mb).toLocaleString('de-DE') + ' MB'],
      ['RAM-Spitzenwert seit Prozessstart', d.process_peak_rss_mb == null ? 'Nicht verfügbar' : Number(d.process_peak_rss_mb).toLocaleString('de-DE') + ' MB'],
      ['RAM-Schutz', !d.ram_guard ? 'Nicht verfügbar' : Number(d.ram_guard.level || 0) >= 3 ? '🔴 Notfallreserve (' + Number(d.ram_guard.emergency_mb || 470) + ' MB)' : Number(d.ram_guard.level || 0) >= 2 ? '🟠 Kritisch (' + Number(d.ram_guard.critical_mb || 420) + ' MB)' : Number(d.ram_guard.level || 0) >= 1 ? '🟡 Warnung (' + Number(d.ram_guard.warn_mb || 350) + ' MB)' : '🟢 Normal (unter ' + Number(d.ram_guard.warn_mb || 350) + ' MB)'],
      ['RAM-Trend', !d.ram_trend || d.ram_trend.growth_mb == null ? 'Sammle Messwerte' : (Number(d.ram_trend.growth_mb) >= 0 ? '+' : '') + Number(d.ram_trend.growth_mb).toLocaleString('de-DE') + ' MB / ' + Number(d.ram_trend.window_minutes || 0).toLocaleString('de-DE') + ' Min.'],
      ['Discord-Nachrichten-Cache', String(d.discord_cached_messages || 0) + ' / ' + String(d.discord_message_cache_limit || 'unbegrenzt')],
      ['Datenträger frei', mb(d.disk_free_mb)],
      ['Datenträger belegt', (Number(d.disk_used_percent || 0)).toFixed(1) + '%'],
      ['Letztes Backup', d.backup_last || 'Kein Backup gefunden'],
      ['Backups vorhanden', String(d.backup_count || 0)],
      ['Aktive Automationen', String(d.automations_enabled || 0)],
      ['Automationsfehler / 24 h', String(d.automation_failures_24h || 0)],
      ['KI-Moderation', d.ai_moderation && d.ai_moderation.enabled ? 'Aktiv' : 'Deaktiviert / nicht konfiguriert'],
      ['KI-Prüfwarteschlange', String((d.ai_moderation && d.ai_moderation.queue) || 0) + ' / ' + String((d.ai_moderation && d.ai_moderation.queue_max) || '—')],
      ['KI-Nachrichten wegen API-Limit verworfen', String((d.ai_moderation && d.ai_moderation.paused_queue_drops) || 0)],
      ['KI-Warteschlangenverluste', String((d.ai_moderation && d.ai_moderation.queue_drops) || 0)],
      ['KI-Moderationspause', String((d.ai_moderation && d.ai_moderation.paused_seconds) || 0) + ' s'],
      ['KI-Support-Verläufe im RAM', String((d.ai_support && d.ai_support.history_count) || 0) + ' / 200'],
      ['KI-Support-Nutzer im Cache', String((d.ai_support && d.ai_support.tracked_users) || 0) + ' / 500'],
      ['KI-Support-API-Pause', String((d.ai_support && d.ai_support.paused_seconds) || 0) + ' s'],
      ['WebSocket-Latenz', d.discord_latency_ms == null ? 'Nicht verfügbar' : Number(d.discord_latency_ms).toFixed(0) + ' ms'],
      ['Pulse-Version', d.version || 'unbekannt'],
      ['Python', d.python || 'unbekannt']
    ];
    out.innerHTML = rows.map(([k, v]) => '<div class="row"><b>' + k + '</b><span>' + String(v) + '</span></div>').join('');
  } catch (e) {
    out.textContent = 'Live-Diagnose aktuell nicht erreichbar. Bitte Berechtigung, Bot-Status und Serverlogs prüfen.';
  }
}
loadPulseMonitor();
window.setInterval(loadPulseMonitor, 15000);
""")

    @app.get("/ultimate/system/api-keys", response_class=HTMLResponse)
    async def ultimate_api_keys(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            keys = [dict(r) for r in c.execute(
                "SELECT id,name,created_at,revoked_at,scopes,expires_at,last_used_at FROM ultimate_api_keys ORDER BY created_at DESC"
            ).fetchall()]
        rows = "".join(
            card(f"<div class=row><div><b>{esc(k['name'])}</b><div class='tiny'>erstellt {esc(k['created_at'])} · Bereiche: {esc(k['scopes'] or 'alle (älterer Schlüssel)')} · {('gültig bis ' + esc(k['expires_at'][:10])) if k['expires_at'] else 'unbefristet'} · zuletzt benutzt: {esc(k['last_used_at'] or 'nie')}</div></div><span class='pill'>{'widerrufen' if k['revoked_at'] else ('abgelaufen' if k['expires_at'] and k['expires_at'] <= iso() else 'aktiv')}</span></div>{'' if k['revoked_at'] else f"<form method='post' action='/ultimate/system/api-key/{k['id']}/revoke'><button class='btn danger'>Widerrufen</button></form>"}")
            for k in keys
        ) or card("<div class='tiny'>Keine API-Keys.</div>")
        body = f"<div class='grid g2'>{rows}</div><div style='margin-top:13px'><a class='btn primary' href='/ultimate/system'>← System</a></div>"
        return page(ctx, "system", "API-Keys", "Verwaltung externer Pulse-Integrationen.", body)

    @app.post("/ultimate/system/api-key/{key_id}/revoke")
    async def ultimate_api_key_revoke(request: Request, key_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            c.execute("UPDATE ultimate_api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (iso(), key_id))
        audit(ctx, "API-Key widerrufen", key_id)
        return RedirectResponse("/ultimate/system/api-keys", status_code=303)

    @app.get("/ultimate/cases/{case_id}/attachments/{attachment_id}")
    async def ultimate_attachment_download(
        request: Request, case_id: str, attachment_id: str, user_session: str = Cookie(None)
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        from fastapi.responses import FileResponse
        with cx() as c:
            row = c.execute(
                "SELECT * FROM ultimate_attachments WHERE id=? AND owner_type='case' AND owner_id=?",
                (attachment_id, case_id),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Datei nicht gefunden.")
        path = ATTACHMENTS_DIR / row["stored_name"]
        if not path.exists():
            raise HTTPException(404, "Datei fehlt auf dem Datenträger.")
        return FileResponse(path, filename=row["original_name"], media_type=row["content_type"] or "application/octet-stream")

    @app.post("/ultimate/system/branding")
    async def ultimate_branding(
        request: Request, name: str = Form(...), accent: str = Form("#6366f1"),
        logo_url: str = Form(""), user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent.strip()):
            raise HTTPException(400, "Ungültige Akzentfarbe.")
        set_setting("branding", {"name": name.strip()[:80], "accent": accent.strip(), "logo_url": logo_url.strip()[:500]})
        audit(ctx, "Branding aktualisiert", "", name.strip(), accent.strip())
        return RedirectResponse("/ultimate/system", status_code=303)

    @app.get("/ultimate/api/overview")
    async def ultimate_api_overview(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        members = team_members(ctx.guild)
        cases = case_rows(500)
        with cx() as c:
            feedback = c.execute("SELECT COUNT(*) FROM ultimate_feedback WHERE status IN ('new','reviewing')").fetchone()[0]
            active_polls = c.execute("SELECT COUNT(*) FROM ultimate_polls WHERE active=1").fetchone()[0]
            handovers = c.execute("SELECT COUNT(*) FROM ultimate_handovers WHERE status='open'").fetchone()[0]
        return JSONResponse({
            "version": getattr(__import__("webserver"), "PULSE_VERSION", "unknown"),
            "team_total": len(members),
            "online": sum(str(m.status) in {"online","idle","dnd"} for m in members),
            "open_cases": sum(x["status"] not in ("closed","resolved") for x in cases),
            "urgent_cases": sum(x["priority"]=="urgent" and x["status"] not in ("closed","resolved") for x in cases),
            "open_feedback": feedback,
            "active_polls": active_polls,
            "open_handovers": handovers,
        })

    @app.get("/ultimate/api/team")
    async def ultimate_api_team(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        rows = []
        shared = stats_context()
        for m in team_members(ctx.guild):
            s = member_stats(m, shared)
            rows.append({
                "id": m.id, "name": m.display_name, "discord_status": str(m.status),
                "score": s["score"], "weekly_seconds": s["weekly_seconds"],
                "warnings": s["warnings"] if __import__("webserver").can_see_warnings(ctx.perms, ctx.user.get("id"), m.id) else None,  # E-3
                "activity_pct": s["activity_pct"],
                "internal_status": s["profile"].get("internal_status", "available"),
            })
        rows.sort(key=lambda x: (-x["score"], x["name"].lower()))
        return JSONResponse({"ok": True, "members": rows})

    @app.get("/ultimate/api/case/{case_id}")
    async def ultimate_api_case(request: Request, case_id: str, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            case = c.execute("SELECT * FROM ultimate_cases WHERE id=?", (case_id,)).fetchone()
            events = c.execute("SELECT * FROM ultimate_case_events WHERE case_id=? ORDER BY created_at", (case_id,)).fetchall()
            attachments = c.execute("SELECT original_name,size_bytes,sha256,created_at FROM ultimate_attachments WHERE owner_type='case' AND owner_id=? ORDER BY created_at", (case_id,)).fetchall()
        if not case:
            raise HTTPException(404, "Fall nicht gefunden.")
        return JSONResponse({"case":dict(case),"events":[dict(x) for x in events],"attachments":[dict(x) for x in attachments]})

    @app.post("/ultimate/api/feedback/{feedback_id}")
    async def ultimate_api_feedback(
        request: Request, feedback_id: str,
        status: str = Form(...), manager_note: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        if status not in _FEEDBACK_STATUS:
            raise HTTPException(400, "Ungültiger Feedbackstatus.")
        with cx() as c:
            c.execute(
                "UPDATE ultimate_feedback SET status=?,manager_note=?,updated_at=? WHERE id=?",
                (status, manager_note[:2000], iso(), feedback_id),
            )
        audit(ctx, "Feedback bearbeitet", feedback_id, "", f"{status}: {manager_note[:300]}")
        return RedirectResponse("/ultimate/feedback", status_code=303)

    @app.get("/ultimate/analytics", response_class=HTMLResponse)
    async def ultimate_analytics(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, perm="can_view_analytics")
        import webserver
        members = team_members(ctx.guild)
        shared = stats_context()
        stats = [(m, member_stats(m, shared)) for m in members]
        stats.sort(key=lambda x: (-x[1]["score"], x[0].display_name.lower()))
        avg_score = round(sum(s["score"] for _, s in stats) / len(stats)) if stats else 0
        total_hours = round(sum(s["total_seconds"] for _, s in stats) / 3600, 1)
        tickets = len(db.list_tickets(limit=5000))
        closed_tickets = sum(1 for t in db.list_tickets(limit=5000) if t.get("status") == "closed")
        apps = webserver.load_json(webserver.APPS_FILE, {})
        active_apps = sum(str(a.get("status")) in {"pending","in_review","interview"} for a in apps.values())
        top = "".join(
            f"<div class='row'><b>#{i} {esc(m.display_name)}</b><span>{s['score']}/100 · {s['total_seconds']//3600}h</span></div>"
            for i,(m,s) in enumerate(stats[:10],1)
        ) or "<div class='tiny'>Keine Teamdaten.</div>"
        body = f"""<div class='grid g4'>
          {card(f"<div class='tiny'>Team-Score</div><div class='metric'>{avg_score}/100</div>")}
          {card(f"<div class='tiny'>Gesamtdienstzeit</div><div class='metric'>{total_hours}h</div>")}
          {card(f"<div class='tiny'>Tickets</div><div class='metric'>{tickets}</div>")}
          {card(f"<div class='tiny'>Bewerbungen offen</div><div class='metric'>{active_apps}</div>")}
        </div>
        <div class='grid g2' style='margin-top:13px'>
          {card("<h3 style='margin-top:0'>🏆 Team-Leaderboard</h3>"+top)}
          {card(f"<h3 style='margin-top:0'>🎫 Ticket-Leistung</h3><div class='metric'>{closed_tickets}</div><div class='tiny'>geschlossene Tickets im aktuellen Datenbestand</div><div class='progress' style='margin-top:10px'><div style='width:{round(closed_tickets/max(1,tickets)*100)}%'></div></div>")}
        </div>"""
        return page(ctx, "analytics", "Analytics", "Leistung, Support, Dienstzeit und Team-Trends.", body)

    @app.post("/ultimate/team/status")
    async def ultimate_team_status(
        request: Request,
        internal_status: str = Form(...),
        internal_message: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session)
        allowed_status = {"available","service","break","training","admin","unavailable"}
        if internal_status not in allowed_status:
            raise HTTPException(400, "Ungültiger Status.")
        ensure_profile(ctx.guild.get_member(int(ctx.user["id"])))
        with cx() as c:
            c.execute(
                "UPDATE ultimate_profiles SET internal_status=?,internal_message=?,updated_at=? WHERE user_id=?",
                (internal_status, internal_message[:300], iso(), str(ctx.user["id"])),
            )
        audit(ctx, "Eigener Teamstatus geändert", str(ctx.user["id"]), "", internal_status)
        return RedirectResponse("/ultimate/team", status_code=303)

    @app.post("/ultimate/certificates/create")
    async def ultimate_certificate_create(
        request: Request,
        user_id: str = Form(...),
        title: str = Form(...),
        score: int = Form(0),
        valid_until: str = Form(""),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_certificates(id,user_id,title,score,issuer_id,issuer_name,issued_at,valid_until)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (uid("cert"), str(user_id), title.strip()[:160], max(0,min(100,score)),
                 str(ctx.user["id"]), ctx.user.get("global_name") or ctx.user.get("username") or "Team",
                 iso(), valid_until.strip()[:80] or None),
            )
        db.notify(user_id, "🎓 Neues Zertifikat", title.strip(), "success", f"/ultimate/team/{user_id}")
        audit(ctx, "Zertifikat vergeben", user_id, title.strip(), f"{score}%")
        return RedirectResponse(f"/ultimate/team/{user_id}", status_code=303)

    @app.get("/ultimate/status")
    async def ultimate_public_status(request: Request):
        import webserver
        bot = getattr(request.app.state, "bot", None)
        guild = bot.get_guild(webserver.GUILD_ID) if bot else None
        db_ok = True
        try:
            db.init_db()
            with db.connect() as c:
                c.execute("SELECT 1")
        except Exception:
            db_ok = False
        return JSONResponse({
            "service": "Pulse TeamOS",
            "version": getattr(webserver, "PULSE_VERSION", "unknown"),
            "status": "operational" if guild and db_ok else "degraded",
            "bot_ready": bool(bot and bot.is_ready()),
            "guild_ready": bool(guild),
            "database": "ok" if db_ok else "error",
        })

    @app.post("/ultimate/system/api-key")
    async def ultimate_api_key_create(
        request: Request,
        name: str = Form(...),
        user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        form = await request.form()
        scopes = [x for x in form.getlist("scopes") if x in API_SCOPES] if "scopes_sent" in form else list(API_SCOPES)
        if not scopes:
            return HTMLResponse("<p>Bitte mindestens einen Bereich wählen. <a href='/ultimate/system'>Zurück</a></p>", status_code=400)
        days = API_EXPIRY_CHOICES.get(str(form.get("expires_days") or "365"), 365)
        expires = iso(utcnow() + timedelta(days=days)) if days else None
        raw = "pulse_" + secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_api_keys(id,name,token_hash,created_by_id,created_at,revoked_at,scopes,expires_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (uid("key"), name.strip()[:100], token_hash, str(ctx.user["id"]), iso(), None, ",".join(scopes), expires),
            )
        audit(ctx, "API-Key erstellt", f"{name.strip()[:100]} · {','.join(scopes)} · " + (f"bis {expires[:10]}" if expires else "unbefristet"))
        # Token is returned exactly once.
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;padding:30px;background:#070b12;color:#fff'>"
            f"<h2>API-Key erstellt</h2><p>Speichere diesen Schlüssel jetzt sicher:</p>"
            f"<code style='display:block;padding:15px;background:#111b2a;border-radius:10px'>{esc(raw)}</code>"
            f"<p>Der Token wird danach nicht erneut angezeigt.</p><a href='/ultimate/system'>Zurück</a></body></html>"
        )

    @app.get("/ultimate/api/v2/overview")
    async def ultimate_api_v2_overview(request: Request):
        require_api_key(request, "overview")  # U-14
        import webserver
        guild = getattr(request.app.state, "bot", None).get_guild(webserver.GUILD_ID) if getattr(request.app.state, "bot", None) else None
        if not guild:
            raise HTTPException(503, "Discord nicht bereit.")
        members = team_members(guild)
        cases = case_rows(500)
        with cx() as c:
            feedback = c.execute("SELECT COUNT(*) FROM ultimate_feedback WHERE status IN ('new','reviewing')").fetchone()[0]
            polls = c.execute("SELECT COUNT(*) FROM ultimate_polls WHERE active=1").fetchone()[0]
        return JSONResponse({
            "version": getattr(webserver, "PULSE_VERSION", "unknown"),
            "team_total": len(members),
            "online": sum(str(m.status) in {"online","idle","dnd"} for m in members),
            "in_service": len(webserver.load_shifts().get("active_shifts", {})),
            "open_cases": sum(x["status"] not in ("closed","resolved") for x in cases),
            "open_feedback": feedback,
            "active_polls": polls,
        })

    @app.get("/ultimate/api/v2/team")
    async def ultimate_api_v2_team(request: Request):
        require_api_key(request, "team")  # U-14
        import webserver
        bot = getattr(request.app.state, "bot", None)
        guild = bot.get_guild(webserver.GUILD_ID) if bot else None
        if not guild:
            raise HTTPException(503, "Discord nicht bereit.")
        rows = []
        shared = stats_context()
        for m in team_members(guild):
            s = member_stats(m, shared)
            rows.append({
                "id": m.id, "name": m.display_name, "status": str(m.status),
                "score": s["score"], "weekly_hours": round(s["weekly_seconds"]/3600, 2),
                "warnings": s["warnings"], "activity_pct": s["activity_pct"],
            })
        return JSONResponse({"ok": True, "members": rows})

    @app.get("/ultimate/metrics")
    async def ultimate_metrics(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, perm="can_view_analytics")
        members = team_members(ctx.guild)
        data = []
        shared = stats_context()
        for m in members:
            s = member_stats(m, shared)
            data.append({"id":m.id,"name":m.display_name,"score":s["score"],"weekly_hours":round(s["weekly_seconds"]/3600,2),"tickets":s["closed_tickets"],"warnings":s["warnings"]})
        return JSONResponse({"generated_at":iso(),"members":data})

    @app.get("/ultimate/api/monitor")
    async def ultimate_api_monitor(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        import platform
        import shutil
        import webserver

        bot = getattr(request.app.state, "bot", None)
        guild = ctx.guild
        ultimate_db_ok = False
        pulse_db_ok = False
        enabled_automations = 0
        automation_failures_24h = 0
        try:
            with cx() as c:
                c.execute("SELECT 1").fetchone()
            ultimate_db_ok = True
            with cx() as c:
                enabled_automations = int(c.execute(
                    "SELECT COUNT(*) FROM ultimate_automations WHERE enabled=1"
                ).fetchone()[0])
                cutoff = iso(utcnow() - timedelta(hours=24))
                automation_failures_24h = int(c.execute(
                    "SELECT COUNT(*) FROM ultimate_automation_runs WHERE status='error' AND started_at>=?",
                    (cutoff,),
                ).fetchone()[0])
        except Exception:
            ultimate_db_ok = False
        try:
            with db.connect() as c:
                c.execute("SELECT 1").fetchone()
            pulse_db_ok = True
        except Exception:
            pulse_db_ok = False

        backup_dir = Path(webserver.BACKUP_DIR)
        backup_files = []
        try:
            backup_files = sorted(
                (name for name in os.listdir(backup_dir)
                 if webserver.BACKUP_NAME_RE.fullmatch(name)),
                key=lambda name: (backup_dir / name).stat().st_mtime,
                reverse=True,
            )
        except OSError:
            backup_files = []

        disk_free_mb = 0
        disk_used_percent = 100.0
        try:
            disk = shutil.disk_usage(str(webserver.BASE_DIR))
            disk_free_mb = round(disk.free / (1024 * 1024))
            disk_used_percent = round((disk.total - disk.free) * 100 / max(1, disk.total), 1)
        except OSError:
            pass

        mod = bot.get_cog("AIModeration") if bot else None
        mod_queue = getattr(mod, "queue", None)
        ai_moderation = {
            "enabled": bool(mod and getattr(mod, "enabled", False)
                            and getattr(mod, "api_key", "")
                            and getattr(mod, "review_channel_id", 0)),
            "queue": int(mod_queue.qsize()) if mod_queue else 0,
            "queue_max": int(getattr(mod, "max_queue_size", 0)) if mod else 0,
            "queue_drops": int(getattr(mod, "_queue_drops", 0)) if mod else 0,
            "paused_queue_drops": int(getattr(mod, "_paused_queue_drops", 0)) if mod else 0,
            "paused_seconds": max(0, int(getattr(mod, "_paused_until", 0) - time.monotonic())) if mod else 0,
            "rate_limit_failures": int(getattr(mod, "_rate_limit_failures", 0)) if mod else 0,
            "review_channel_ready": bool(guild and mod and guild.get_channel(getattr(mod, "review_channel_id", 0))),
        }
        support = bot.get_cog("SupportAssistant") if bot else None
        ai_support = {
            "enabled": bool(support and getattr(support, "enabled", False) and getattr(support, "api_key", "")),
            "history_count": len(getattr(support, "_history", {})) if support else 0,
            "tracked_users": len(getattr(support, "_user_last_request", {})) if support else 0,
            "paused_seconds": max(0, int(getattr(support, "_api_paused_until", 0) - time.monotonic())) if support else 0,
        }
        discord_ready = bool(bot and bot.is_ready() and guild)
        return JSONResponse({
            "ok": discord_ready and ultimate_db_ok and pulse_db_ok and disk_used_percent < 95,
            "timestamp": iso(),
            "version": getattr(webserver, "PULSE_VERSION", "unknown"),
            "python": platform.python_version(),
            "process_rss_mb": process_rss_mb(),
            "container_memory_mb": container_memory_mb(),
            "process_peak_rss_mb": process_peak_rss_mb(),
            "ram_guard": {
                "level": memory_guard_level(container_memory_mb()),
                "warn_mb": RAM_WARN_MB,
                "critical_mb": RAM_CRITICAL_MB,
                "emergency_mb": RAM_EMERGENCY_MB,
                "recovery_mb": RAM_RECOVERY_MB,
            },
            "ram_trend": ram_trend_snapshot(),
            "discord_cached_messages": len(getattr(bot, "cached_messages", ())) if bot else 0,
            "discord_message_cache_limit": getattr(bot, "pulse_message_cache_limit", None) if bot else None,
            "discord_ready": discord_ready,
            "discord_latency_ms": round(float(bot.latency) * 1000, 1) if bot and bot.is_ready() else None,
            "ultimate_db_ok": ultimate_db_ok,
            "pulse_db_ok": pulse_db_ok,
            "disk_free_mb": disk_free_mb,
            "disk_used_percent": disk_used_percent,
            "backup_count": len(backup_files),
            "backup_last": backup_files[0] if backup_files else None,
            "automations_enabled": enabled_automations,
            "automation_failures_24h": automation_failures_24h,
            "ai_moderation": ai_moderation,
            "ai_support": ai_support,
        })

    app.state.pulse_ultimate_registered = True


async def _run_automation(bot, automation: dict):
    import webserver
    action = automation["action_type"]
    payload = json.loads(automation["action_payload"] or "{}")
    guild = bot.get_guild(webserver.GUILD_ID)
    condition = payload.get("condition") if isinstance(payload, dict) else None

    if condition:
        team = team_members(guild) if guild else []
        open_cases_count = sum(x["status"] not in ("closed","resolved") for x in case_rows(500))
        open_tickets_count = sum(x.get("status") != "closed" for x in db.list_tickets(limit=5000))
        cond_type = str(condition.get("type","always"))
        threshold = float(condition.get("value",0) or 0)
        current = {
            "open_cases_gte": open_cases_count,
            "open_tickets_gte": open_tickets_count,
            "team_online_gte": sum(str(m.status) in {"online","idle","dnd"} for m in team),
            "team_size_gte": len(team),
        }.get(cond_type, 1)
        if cond_type != "always" and float(current) < threshold:
            return "condition_not_met"
    if not guild:
        return "guild unavailable"

    if action == "notify_user":
        target_id = str(payload.get("user_id") or "")
        target = guild.get_member(int(target_id)) if target_id.isdigit() else None
        if not target:
            return "target unavailable"
        title = str(payload.get("title") or automation["name"])[:160]
        body = str(payload.get("body") or "Automatische Pulse-Benachrichtigung.")[:1000]
        db.notify(target.id, title, body, "warning" if payload.get("urgent") else "info", payload.get("url") or "/ultimate")
        return f"notification {target.id}"

    if action in {"notify_managers", "notify_team"}:
        target_members = team_members(guild)
        if action == "notify_managers":
            filtered = []
            for m in target_members:
                perms, _ = webserver.compute_perms(guild, m.id, webserver.load_config())
                if perms.get("can_promote") or perms.get("is_admin"):
                    filtered.append(m)
            target_members = filtered
        title = str(payload.get("title") or automation["name"])[:160]
        body = str(payload.get("body") or "Automatische Pulse-Benachrichtigung.")[:1000]
        for m in target_members:
            db.notify(m.id, title, body, "warning" if payload.get("urgent") else "info", payload.get("url") or "/ultimate")
        return f"{len(target_members)} notifications"

    if action == "create_task":
        assignee_id = payload.get("assignee_id")
        title = str(payload.get("title") or automation["name"])[:160]
        target = guild.get_member(int(assignee_id)) if str(assignee_id).isdigit() else None
        if not target:
            return "assignee unavailable"
        tid = db.create_task(title, str(payload.get("description") or "")[:2500], target.id, target.display_name,
                             "0", "Pulse Automation", str(payload.get("priority") or "normal"))
        db.notify(target.id, "📋 Automatische Aufgabe", title, "info", "/tasks")
        return f"task {tid}"

    if action == "create_case":
        title = str(payload.get("title") or automation["name"])[:160]
        case_id = uid("case")
        with cx() as c:
            c.execute(
                """INSERT INTO ultimate_cases
                (id,case_no,title,category,priority,status,subject_type,subject_id,subject_name,description,
                 assignee_id,assignee_name,created_by_id,created_by_name,created_at,updated_at,closed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (case_id, case_number(), title, str(payload.get("category") or "automation")[:40],
                 str(payload.get("priority") or "normal"), "open", "automation", str(payload.get("subject_id") or ""),
                 str(payload.get("subject_name") or ""), str(payload.get("description") or "")[:5000],
                 None, "", "0", "Pulse Automation", iso(), iso(), None),
            )
        return f"case {case_id}"

    if action == "team_report":
        import webserver
        members = team_members(guild)
        shared = stats_context()
        top = sorted(((member_stats(m, shared)["score"], m) for m in members), reverse=True, key=lambda x:x[0])[:10]
        desc = "\n".join(f"{i}. {m.display_name} · {score}/100" for i,(score,m) in enumerate(top,1)) or "Keine Teamdaten"
        channel = guild.get_channel(TEAM_UPDATE_CHANNEL_ID)
        if channel:
            embed = discord.Embed(title="📊 Pulse Team-Report", description=desc, color=_pulse_color("info"))
            await channel.send(embed=embed)
        return f"report {len(top)}"

    if action == "ticket_sla_alert":
        import webserver
        minutes = max(5, min(1440, int(payload.get("minutes", 30))))
        overdue = []
        now_dt = utcnow()
        for ticket in db.list_tickets(limit=5000):
            if ticket.get("status") == "closed":
                continue
            try:
                opened = datetime.fromisoformat(str(ticket["opened_at"]).replace("Z","+00:00"))
                if (now_dt - opened).total_seconds() >= minutes * 60:
                    overdue.append(ticket)
            except Exception:
                continue
        managers = []
        for m in team_members(guild):
            perms, _ = webserver.compute_perms(guild, m.id, webserver.load_config())
            if perms.get("can_promote") or perms.get("is_admin"):
                managers.append(m)
        for m in managers:
            db.notify(m.id, "🚨 Ticket-SLA überschritten", f"{len(overdue)} Tickets warten länger als {minutes} Minuten.", "warning", "/tickets", f"sla:{minutes}:{len(overdue)}", 3600)
        return f"sla {len(overdue)}"

    if action == "inactivity_report":
        import webserver
        threshold_days = max(1, min(365, int(payload.get("days", 14))))
        warnings = []
        now_dt = utcnow()
        for m in team_members(guild):
            profile = get_profile(str(m.id)) or {}
            seen = profile.get("updated_at") or ""
            try:
                days = (now_dt - datetime.fromisoformat(seen.replace("Z","+00:00"))).days
            except Exception:
                continue
            if days >= threshold_days:
                warnings.append((m, days))
        for m in team_members(guild):
            perms, _ = webserver.compute_perms(guild, m.id, webserver.load_config())
            if perms.get("can_promote") or perms.get("is_admin"):
                for target, days in warnings:
                    db.notify(m.id, "⚠️ Inaktivität", f"{target.display_name} ist seit ca. {days} Tagen ohne Pulse-Aktivität.", "warning", f"/ultimate/team/{target.id}", f"inactive-report:{target.id}:{days}", 86400)
        return f"inactive {len(warnings)}"

    if action == "probation_report":
        due = []
        now_dt = utcnow()
        for m in team_members(guild):
            p = get_profile(str(m.id)) or {}
            try:
                end = datetime.fromisoformat(str(p.get("probation_end","")).replace("Z","+00:00"))
                if now_dt <= end <= now_dt + timedelta(days=int(payload.get("days",7) or 7)):
                    due.append((m,end))
            except Exception:
                continue
        for manager in team_members(guild):
            perms, _ = webserver.compute_perms(guild, manager.id, webserver.load_config())
            if perms.get("can_promote") or perms.get("is_admin"):
                for target,end in due:
                    db.notify(manager.id, "🎯 Probezeit endet bald", f"{target.display_name}: {end.strftime('%d.%m.%Y')}", "info", f"/ultimate/team/{target.id}", f"probation-report:{target.id}:{end.isoformat()}", 86400)
        return f"probation {len(due)}"

    return "unsupported action"


# --------------------------------------------------------------------------- automation engine (7.18)
# One engine for schedule and event automations: execute_automation() runs a step chain (or a legacy single
# action), stores one run row with per-step results and keeps at most MAX_RUNS_PER_AUTOMATION rows each.
MAX_RUNS_PER_AUTOMATION = 500
_LEGACY_FAILURES = ("guild unavailable", "target unavailable", "assignee unavailable", "unsupported action")
_EVENT_TRIGGER_CACHE: dict = {"db": None, "triggers": frozenset()}  # filled by refresh_event_triggers()
_EVENT_COUNTER = None  # pulse_automation_rules.EventCounter, created on first use
_EVENT_COOLDOWNS: OrderedDict[tuple, float] = OrderedDict()
_EVENT_SEEN: OrderedDict[tuple, bool] = OrderedDict()
_RUNNING: set = set()
_DISPATCH_DEPTH = contextvars.ContextVar("pulse_automation_dispatch_depth", default=0)


def automation_steps(automation: dict) -> list[dict]:
    try:
        steps = json.loads(automation.get("steps_json") or "[]")
    except (TypeError, ValueError):
        return []
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def automation_trigger_config(automation: dict) -> dict:
    try:
        data = json.loads(automation.get("trigger_config") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def refresh_event_triggers() -> None:
    """Remember which event triggers have enabled automations (so Discord events skip the DB otherwise)."""
    init_db()
    with cx() as c:
        rows = c.execute("SELECT DISTINCT trigger_type FROM ultimate_automations WHERE enabled=1").fetchall()
    _EVENT_TRIGGER_CACHE.update(db=str(ultimate_db()), triggers=frozenset(r[0] for r in rows) & _rules().EVENT_TRIGGERS)


def _rules():
    import pulse_automation_rules
    return pulse_automation_rules


async def _run_step(bot, guild, automation: dict, step: dict, context: dict) -> tuple[str, str]:
    """One step → (ok|failed|skipped, detail). Never raises for Discord errors."""
    rules = _rules()
    kind = step.get("type")
    text_ctx = {"name": automation.get("name", ""), "time": iso(), **{k: v for k, v in context.items() if isinstance(v, (str, int, float))},
                "events": ", ".join(context.get("events") or []) if isinstance(context.get("events"), list) else context.get("events", "")}
    if kind in rules.LEGACY_STEP_TYPES:
        payload = {k: (rules.render_text(v, text_ctx) if isinstance(v, str) and k in ("title", "body", "description") else v)
                   for k, v in step.items() if k != "type"}
        result = await _run_automation(bot, {**automation, "action_type": kind, "action_payload": json.dumps(payload)})
        return ("failed" if result in _LEGACY_FAILURES else "ok"), result
    if kind not in rules.STEP_TYPES:
        return "failed", "nicht unterstützte Aktion"
    import webserver
    import pulse_moderation as pm
    config = pm.normalize_antinuke(webserver.load_config().get("pulse_antinuke"))
    if kind == "activity_log":
        db.record_event("Automation: Protokoll", "automation", automation.get("id", ""), "0", "Pulse Automation",
                        {"automation": automation.get("name", ""), "text": rules.render_text(step.get("text"), text_ctx)[:1000],
                         "trigger": {k: v for k, v in context.items() if k in ("rule_id", "rule_label", "events", "actor_id", "result")}})
        return "ok", "Eintrag gespeichert"
    if guild is None:
        return "failed", "Server nicht verfügbar"
    if kind in ("security_alert", "notify_role"):
        channel = guild.get_channel(config["alert_channel_id"]) if config["alert_channel_id"] else None
        if channel is None:
            return "failed", "kein Anti-Nuke-Alarmkanal konfiguriert oder gefunden"
        text = rules.render_text(step.get("text") or "", text_ctx) or f"🤖 Automation „{automation.get('name', '')}“ ausgelöst."
        mentions = discord.AllowedMentions.none()
        if kind == "notify_role":
            if not config["manager_role_id"]:
                return "failed", "keine Verwaltungsrolle konfiguriert"
            text = f"<@&{config['manager_role_id']}> {text}"
            mentions = discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(id=config["manager_role_id"])])
        try:
            await channel.send(f"🤖 {text}"[:1900], allowed_mentions=mentions)
        except discord.Forbidden:
            return "failed", "Discord hat das Senden abgelehnt (Rechte im Alarmkanal)"
        except discord.HTTPException as exc:
            return "failed", f"Discord-Fehler HTTP {exc.status}"
        return "ok", f"gesendet in #{getattr(channel, 'name', channel.id)}"
    if kind == "antinuke_lockdown":
        if not config["enabled"] or config["mode"] != "protect":
            return "skipped", "Anti-Nuke ist nicht im aktiven Schutzmodus – kein Lockdown"
        from cogs.antinuke import start_lockdown
        result = await start_lockdown(guild, config["lockdown_channel_ids"], by=f"Automation {automation.get('name', '')}"[:80],
                                      reason=f"Pulse-Automation „{automation.get('name', '')}“")
        return ("ok" if result["status"] in ("ok", "partial") else result["status"]), result["detail"]
    return "failed", "nicht unterstützte Aktion"


def _store_run(automation: dict, status: str, summary: str, *, started: str, duration_ms: int,
               trigger_info: dict, steps: list, actor_name: str) -> str:
    run_id = uid("run")
    with cx() as c:
        c.execute(
            "INSERT INTO ultimate_automation_runs(id,automation_id,status,result,started_at,finished_at,trigger_info,steps_json,duration_ms,actor_name)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, automation["id"], status, summary[:3000], started, iso(),
             json.dumps({**trigger_info, "automation_name": automation.get("name", "")}, ensure_ascii=False)[:3000],
             json.dumps(steps, ensure_ascii=False)[:6000], int(duration_ms), actor_name[:80]),
        )
        c.execute(
            """DELETE FROM ultimate_automation_runs WHERE automation_id=? AND id NOT IN
               (SELECT id FROM ultimate_automation_runs WHERE automation_id=? ORDER BY started_at DESC LIMIT ?)""",
            (automation["id"], automation["id"], MAX_RUNS_PER_AUTOMATION),
        )
    return run_id


async def execute_automation(bot, automation: dict, *, trigger_info: dict, context: dict | None = None,
                             actor_name: str = "", dry_run: bool = False) -> dict:
    """Run one automation once and store the result. dry_run (or test mode) only records what would run."""
    context = context or {}
    started, t0 = iso(), time.monotonic()
    key = automation["id"]
    if key in _RUNNING:
        return {"status": "skipped", "summary": "läuft bereits – doppelte Ausführung verhindert", "steps": []}
    _RUNNING.add(key)
    token = _DISPATCH_DEPTH.set(_DISPATCH_DEPTH.get() + 1)  # steps never re-trigger automations
    steps_done: list[dict] = []
    try:
        steps = automation_steps(automation)
        dry = dry_run or bool(automation.get("test_mode"))
        import webserver
        guild = bot.get_guild(webserver.GUILD_ID) if bot else None
        if not steps:  # automation from before 7.18: one action with a JSON payload
            if dry:
                status, summary = "test", f"Testlauf: würde „{automation.get('action_type')}“ ausführen"
            else:
                try:
                    result = await _run_automation(bot, automation)
                    status = "skipped" if result == "condition_not_met" else ("error" if result in _LEGACY_FAILURES else "success")
                    summary = result
                except Exception as exc:
                    status, summary = "error", str(exc)
            steps_done = [{"type": automation.get("action_type"), "status": status, "detail": summary[:300]}]
        else:
            condition = None
            try:
                condition = json.loads(automation.get("action_payload") or "{}").get("condition")
            except (TypeError, ValueError, AttributeError):
                condition = None
            if condition and condition.get("type", "always") != "always" and guild is not None:
                probe = await _run_automation(bot, {**automation, "action_type": "_condition_only",
                                                    "action_payload": json.dumps({"condition": condition})})
                if probe == "condition_not_met":
                    summary = "Bedingung nicht erfüllt – keine Aktion"
                    await asyncio.to_thread(_store_run, automation, "skipped", summary, started=started,
                                            duration_ms=int((time.monotonic() - t0) * 1000), trigger_info=trigger_info,
                                            steps=[], actor_name=actor_name)
                    return {"status": "skipped", "summary": summary, "steps": []}
            for step in steps[:_rules().MAX_STEPS]:
                if dry:
                    steps_done.append({"type": step.get("type"), "status": "test", "detail": "Testmodus – nicht ausgeführt"})
                    continue
                try:
                    state, detail = await _run_step(bot, guild, automation, step, context)
                except Exception as exc:  # a failing step never stops the remaining steps
                    state, detail = "failed", f"Fehler: {type(exc).__name__}"
                steps_done.append({"type": step.get("type"), "status": state, "detail": str(detail)[:300]})
            failed = [s for s in steps_done if s["status"] == "failed"]
            status = "test" if dry else ("error" if failed else "success")
            summary = " · ".join(f"{_rules().STEP_TYPES.get(s['type'], s['type'])}: {s['detail']}" for s in steps_done)
        duration = int((time.monotonic() - t0) * 1000)
        await asyncio.to_thread(_store_run, automation, status, summary, started=started, duration_ms=duration,
                                trigger_info=trigger_info, steps=steps_done, actor_name=actor_name)
        return {"status": status, "summary": summary, "steps": steps_done, "duration_ms": duration}
    finally:
        _DISPATCH_DEPTH.reset(token)
        _RUNNING.discard(key)


async def dispatch_event(bot, trigger: str, context: dict) -> int:
    """Run enabled automations whose event trigger matches. Bounded, deduplicated, no recursion."""
    global _EVENT_COUNTER
    if _DISPATCH_DEPTH.get() > 0:
        return 0  # an automation step caused this event – never start another chain from it
    if _EVENT_TRIGGER_CACHE["db"] != str(ultimate_db()) or trigger not in _EVENT_TRIGGER_CACHE["triggers"]:
        return 0
    rules = _rules()
    if _EVENT_COUNTER is None:
        _EVENT_COUNTER = rules.EventCounter()
    with cx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM ultimate_automations WHERE enabled=1 AND trigger_type=? LIMIT 50", (trigger,)).fetchall()]
    ran, now = 0, time.monotonic()
    for automation in rows:
        cfg = automation_trigger_config(automation)
        if not rules.trigger_matches(trigger, cfg, trigger, context):
            continue
        dedupe = (automation["id"], context.get("audit_entry_id") or context.get("rule_id"), context.get("actor_id"), trigger)
        if dedupe in _EVENT_SEEN:
            continue
        _EVENT_SEEN[dedupe] = True
        while len(_EVENT_SEEN) > 2000:
            _EVENT_SEEN.popitem(last=False)
        group = (automation["id"], context.get("actor_id") if cfg.get("group", "actor") == "actor" else "*")
        if trigger == "antinuke_event" and int(cfg.get("min_count", 1)) > 1:
            count = _EVENT_COUNTER.hit(group, now, min_count=int(cfg["min_count"]), window_seconds=int(cfg.get("window_seconds", 10)))
            if count < int(cfg["min_count"]):
                continue
            _EVENT_COUNTER.reset(group)
        if _EVENT_COOLDOWNS.get(group, 0) > now:
            continue
        _EVENT_COOLDOWNS[group] = now + int(cfg.get("cooldown_seconds", 60))
        while len(_EVENT_COOLDOWNS) > 2000:
            _EVENT_COOLDOWNS.popitem(last=False)
        info = {"trigger": trigger, **{k: context.get(k) for k in ("rule_id", "rule_label", "events", "actor_id", "dry_run") if k in context}}
        await execute_automation(bot, automation, trigger_info=info, context=context)
        ran += 1
    return ran


async def _automation_cycle(bot):
    """One run of the 60-second automation work (everything except the memory guard)."""
    # U-11: opt-in notification DMs, bounded batch per run on this existing loop.
    try:
        import pulse_notify_prefs
        if bot and pulse_notify_prefs.pending_dms():
            await pulse_notify_prefs.drain_dms(bot, os.getenv("PUBLIC_BASE_URL", ""))
    except Exception as exc:
        print(f"⚠️ Benachrichtigungs-DMs konnten nicht gesendet werden: {type(exc).__name__}")
    # F-10: monthly review DMs for people who opted in (first days of a month, once; same loop).
    try:
        import webserver
        import pulse_phase7
        pulse_phase7.queue_month_reviews(webserver)
    except Exception as exc:
        print(f"⚠️ Monatsrückblick konnte nicht vorbereitet werden: {type(exc).__name__}")
    # BOT-09: lift time-limited citizen bans whose expiry has passed (same loop, no new worker).
    try:
        from cogs.mod_cases import lift_expired_bans
        if bot:
            await lift_expired_bans(bot)
    except Exception as exc:
        print(f"⚠️ Befristete Bans konnten nicht geprüft werden: {type(exc).__name__}")
    # BOT-10: reminder DM about an hour before a scheduled application interview (same loop).
    try:
        import webserver
        if bot:
            await webserver.send_interview_reminders(bot)
    except Exception as exc:
        print(f"⚠️ Gesprächserinnerungen konnten nicht gesendet werden: {type(exc).__name__}")
    # Warn-Folgerollen regelmäßig prüfen, damit 7-Tage-Sperren
    # auch nach einem Neustart zuverlässig automatisch enden.
    try:
        import webserver
        guild = bot.get_guild(webserver.GUILD_ID) if bot else None
        if guild:
            await webserver.cleanup_expired_warn_restrictions(guild, webserver.load_config())
    except Exception as exc:
        print(f"⚠️ Warn-Folgerollen konnten nicht bereinigt werden: {exc}")

    now = utcnow()
    with cx() as c:
        # Event automations (Anti-Nuke) run through dispatch_event, never on the schedule.
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM ultimate_automations WHERE enabled=1 AND next_run_at<=? "
            "AND trigger_type NOT IN ('antinuke_rule','antinuke_event')",
            (iso(now),),
        ).fetchall()]
    for automation in rows:
        # The run is stored by execute_automation (status, result, per-step results, duration).
        try:
            await execute_automation(bot, automation, trigger_info={"trigger": "interval"})
        except Exception as exc:  # M-B1: one failing automation (e.g. locked DB) must not stop the others
            print(f"⚠️ Automation {automation.get('id')} fehlgeschlagen: {type(exc).__name__}")
        # Always move on to the next interval – no retry every 60 s after a failure.
        try:
            with cx() as c:
                next_run = now + timedelta(minutes=max(5, int(automation["interval_minutes"] or 0)))
                c.execute(
                    "UPDATE ultimate_automations SET last_run_at=?,next_run_at=? WHERE id=?",
                    (iso(), iso(next_run), automation["id"]),
                )
        except sqlite3.Error as exc:
            print(f"⚠️ Nächster Lauf für Automation {automation.get('id')} nicht gespeichert: {type(exc).__name__}")


async def automation_loop(bot):
    import pulse_jobs  # P-06/P-01: registry and loop-lag measurement piggyback on this existing loop
    setup()
    pulse_jobs.start_stall_watchdog(asyncio.get_running_loop())  # names the code behind "Event-Loop verzögert"
    try:
        refresh_event_triggers()  # enables dispatch_event for Anti-Nuke automations
    except Exception as exc:
        print(f"⚠️ Ereignis-Automationen konnten nicht geladen werden: {type(exc).__name__}")
    while True:
        try:
          async with pulse_jobs.job("Automation (60 s)", 60):
            # Memory guard piggybacks on this existing loop; no extra worker is added.
            await runtime_memory_guard(bot)
            # O-07: queued system alerts (job/backup/database errors) – also during maintenance mode.
            try:
                import pulse_alerts
                await pulse_alerts.flush(bot)
            except Exception as exc:
                print(f"⚠️ Systemalarme konnten nicht gesendet werden: {type(exc).__name__}")
            # F-04: maintenance mode pauses the bot automations; the memory guard above keeps running.
            import pulse_flags
            if not pulse_flags.automations_paused():
                await _automation_cycle(bot)
          started = asyncio.get_running_loop().time()
          await asyncio.sleep(60)
          pulse_jobs.record_loop_lag(60, asyncio.get_running_loop().time() - started)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            pulse_jobs.record_error("Automation (60 s)", exc, already_counted=getattr(exc, "_pulse_job_counted", False))  # was swallowed without any log before
            await asyncio.sleep(60)


async def start_automation(bot):
    global _AUTOMATION_TASK
    if _AUTOMATION_TASK and not _AUTOMATION_TASK.done():
        return _AUTOMATION_TASK
    _AUTOMATION_TASK = asyncio.create_task(automation_loop(bot))
    return _AUTOMATION_TASK
