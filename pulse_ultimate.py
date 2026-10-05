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
import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
from fastapi import Cookie, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import pulse_db as db

BASE = Path(__file__).resolve().parent
ATTACHMENTS_DIR = BASE / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
ULTIMATE_DB = BASE / "pulse.db"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
TEAM_UPDATE_CHANNEL_ID = 1531132354272170115

_PRIORITY = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
_CASE_STATUSES = ("open", "investigating", "waiting", "resolved", "closed")
_FEEDBACK_STATUS = ("new", "reviewing", "planned", "done", "rejected")
_POLL_TYPES = ("single", "multiple")

_INIT = False
_AUTOMATION_TASK: Optional[asyncio.Task] = None


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).isoformat(timespec="seconds")


def uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def cx():
    c = sqlite3.connect(ULTIMATE_DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    global _INIT
    if _INIT:
        return
    with cx() as c:
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


def setup() -> None:
    init_db()
    seed_defaults()


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
        exists = c.execute("SELECT 1 FROM ultimate_profiles WHERE user_id=?", (str(member.id),)).fetchone()
        if exists:
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


def member_stats(member) -> dict:
    import webserver
    ensure_profile(member)
    shifts = webserver.load_shifts()
    uid_s = str(member.id)
    weekly = webserver.calculate_weekly_seconds(uid_s, shifts.get("history", []), shifts.get("active_shifts", {}))
    history = [h for h in shifts.get("history", []) if str(h.get("mod_id")) == uid_s]
    total_seconds = sum(int(h.get("duration_seconds", 0)) for h in history)
    active = shifts.get("active_shifts", {}).get(uid_s)
    if active:
        total_seconds += webserver.shift_elapsed(active)
    tickets = sum(1 for t in db.list_tickets(limit=5000) if str(t.get("claimed_by_id")) == uid_s)
    closed_tickets = sum(1 for t in db.list_tickets(limit=5000) if str(t.get("claimed_by_id")) == uid_s and t.get("status") == "closed")
    tasks = db.list_tasks(assignee_id=member.id, limit=1000, include_archived=True)
    tasks_done = sum(1 for t in tasks if t.get("status") in ("done", "archived"))
    attempts = db.attempts(user_id=member.id, limit=200)
    passed = sum(1 for x in attempts if x.get("passed"))
    profile = get_profile(uid_s) or {}
    warnings = len(webserver.active_warns(webserver.user_entry(webserver.load_json(webserver.DATA_FILE, {}), uid_s)))
    weekly_goal = max(1.0, float(webserver.load_config().get("weekly_goal_hours", 3.0)))
    activity_pct = min(100, round(weekly / 3600 / weekly_goal * 100))
    reliability_pct = 100 if not tasks else round(tasks_done / len(tasks) * 100)
    support_pct = min(100, closed_tickets * 5)
    discipline_pct = max(0, 100 - warnings * 30)
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
    classes = {
        "open": "background:#17173d;color:#c7d2fe",
        "investigating": "background:#2a2110;color:#fde68a",
        "waiting": "background:#17202f;color:#bae6fd",
        "resolved": "background:#0c2415;color:#bbf7d0",
        "closed": "background:#1f2937;color:#cbd5e1",
    }
    return f'<span class="badge" style="{classes.get(status, classes["open"])}">{esc(status)}</span>'


def card(inner: str) -> str:
    return f'<div class="card">{inner}</div>'


def page(ctx, active: str, title: str, subtitle: str, body: str, extra_js: str = "") -> HTMLResponse:
    import webserver
    brand = setting("branding", {}) or {}
    accent = esc(brand.get("accent") or "#6366f1")
    nav = [
        ("ultimate", "/ultimate", "⚡ Command Center"),
        ("team", "/ultimate/team", "👥 Team"),
        ("cases", "/ultimate/cases", "🚨 Fälle"),
        ("roblox", "/ultimate/roblox", "🎮 Roblox"),
        ("departments", "/ultimate/departments", "🏢 Abteilungen"),
        ("calendar", "/ultimate/calendar", "🗓 Kalender"),
        ("polls", "/ultimate/polls", "🗳 Abstimmungen"),
        ("feedback", "/ultimate/feedback", "💬 Feedback"),
        ("handover", "/ultimate/handovers", "🧭 Übergaben"),
        ("automations", "/ultimate/automations", "🤖 Automationen"),
        ("workflows", "/ultimate/workflows", "🧩 Workflows"),
        ("approvals", "/ultimate/approvals", "✅ Freigaben"),
        ("announcements", "/ultimate/announcements", "📢 Team-News"),
        ("ideas", "/ultimate/ideas", "💡 Ideen"),
        ("goals", "/ultimate/goals", "🎯 Ziele"),
        ("analytics", "/ultimate/analytics", "📊 Analytics"),
        ("scoreboard", "/ultimate/scoreboard", "🏆 Team-Score"),
        ("support", "/ultimate/support", "🎫 SLA & Support"),
        ("legacy_tasks", "/tasks", "📋 Aufgaben"),
        ("tasks2", "/ultimate/tasks", "📋 Aufgaben 2.0"),
        ("onboarding", "/ultimate/onboarding", "🧑‍💼 On/Offboarding"),
        ("orgchart", "/ultimate/orgchart", "🏢 Organigramm"),
        ("reports", "/ultimate/reports", "📑 Berichte"),
        ("backup", "/ultimate/backup", "💾 Backup"),
        ("permissions", "/ultimate/permissions", "🔐 Rechte"),
        ("awards", "/ultimate/awards", "🏆 Awards"),
        ("legacy_training", "/training", "🎓 Schulungen"),
        ("legacy_tickets", "/tickets", "🎫 Tickets"),
        ("legacy_apps", "/applications", "📝 Bewerbungen"),
        ("system", "/ultimate/system", "🩺 System"),
    ]
    links = "".join(
        f'<a class="nav-item {"active" if k == active else ""}" href="{p}">{label}</a>'
        for k, p, label in nav
    )
    u = esc(ctx.user.get("global_name") or ctx.user.get("username") or ctx.user.get("id"))
    css = f"""
    <style>
      :root{{--pulse-accent:{accent};--bg:#070b12;--panel:#0d1725;--line:#1e2d40;--text:#f8fafc;--muted:#94a3b8}}
      *{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(900px 500px at 15% -10%,#6366f120,transparent 60%),linear-gradient(180deg,#060a10,#09111d);color:var(--text);font:14px Inter,system-ui,sans-serif}}
      a{{text-decoration:none;color:inherit}}.shell{{display:flex;min-height:100vh}}.side{{position:fixed;inset:0 auto 0 0;width:250px;padding:18px;background:#060a10ee;border-right:1px solid var(--line);backdrop-filter:blur(12px);z-index:5}}
      .brand{{font-size:20px;font-weight:900;margin:4px 0 18px}}.brand small{{display:block;color:var(--muted);font-size:10px;margin-top:3px}}
      .nav-item{{display:block;padding:9px 11px;border-radius:10px;color:#cbd5e1;margin:4px 0}}.nav-item:hover,.nav-item.active{{background:#6366f122;color:#fff}}
      .main{{margin-left:250px;width:calc(100% - 250px);padding:26px;max-width:1800px}}.top{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:18px}}
      h1{{margin:0;font-size:27px}}.muted,.tiny{{color:var(--muted)}}.tiny{{font-size:11px}}.grid{{display:grid;gap:13px}}.g4{{grid-template-columns:repeat(4,1fr)}}.g3{{grid-template-columns:repeat(3,1fr)}}.g2{{grid-template-columns:repeat(2,1fr)}}
      .card{{background:linear-gradient(180deg,#0f1928f0,#0a1220f0);border:1px solid #ffffff0d;border-radius:16px;padding:16px;box-shadow:0 18px 50px #0004}}.metric{{font-size:28px;font-weight:900;margin:5px 0}}
      .row{{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:10px;border:1px solid #ffffff0d;border-radius:10px;background:#08111d;margin:7px 0}}
      .badge{{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:4px 8px;font-size:11px;font-weight:800}}.btn{{display:inline-block;border:1px solid var(--line);background:#111b2a;color:#e2e8f0;border-radius:10px;padding:8px 11px;font-weight:800;cursor:pointer}}.primary{{background:var(--pulse-accent);border-color:transparent;color:white}}
      .danger{{background:#2b1116;color:#fecaca}}.input,.select,.ta{{width:100%;padding:10px;border-radius:10px;border:1px solid #2a394e;background:#08111d;color:#fff;outline:none}}.ta{{min-height:110px;resize:vertical}}
      .form{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}.span2{{grid-column:span 2}}.table{{width:100%;border-collapse:collapse}}.table th,.table td{{padding:9px;border-bottom:1px solid #ffffff0c;text-align:left;font-size:12px}}
      .progress{{height:8px;background:#172234;border-radius:999px;overflow:hidden}}.progress>div{{height:100%;background:var(--pulse-accent)}}.pill{{border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:10px;font-weight:800}}
      @media(max-width:1100px){{.g4{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:800px){{.side{{position:static;width:100%;border-right:0;border-bottom:1px solid var(--line)}}.shell{{display:block}}.main{{margin:0;width:100%;padding:15px}}.g4,.g3,.g2,.form{{grid-template-columns:1fr}}.span2{{grid-column:auto}}.top{{flex-direction:column}}}}
    </style>
    """
    body_html = f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Pulse</title>{css}</head>
    <body><div class="shell"><aside class="side"><div class="brand">⚡ Pulse <span style="color:var(--pulse-accent)">Ultimate</span><small>TeamOS Control Center</small></div>
    <nav>{links}</nav><div class="tiny" style="margin-top:16px">Angemeldet als <b>{u}</b></div></aside>
    <main class="main"><div class="top"><div><h1>{esc(title)}</h1><div class="muted">{esc(subtitle)}</div></div><div><a class="btn" href="/dashboard">Altes Dashboard</a> <a class="btn primary" href="/ultimate">Command Center</a></div></div>{body}</main></div>{extra_js}</body></html>"""
    return HTMLResponse(body_html)


def validate_api_key(token: str) -> bool:
    setup()
    if not token or len(token) > 200:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    with cx() as c:
        row = c.execute(
            "SELECT 1 FROM ultimate_api_keys WHERE token_hash=? AND revoked_at IS NULL",
            (digest,),
        ).fetchone()
    return bool(row)


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
        avg_score = round(sum(member_stats(m)["score"] for m in members) / len(members)) if members else 0
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
            s = member_stats(m)
            if s["warnings"] >= 3:
                warnings.append(f'🚨 <b>{esc(m.display_name)}</b> hat {s["warnings"]}/3 Warnungen.')
            elif s["activity_pct"] < 50:
                warnings.append(f'⚠️ <b>{esc(m.display_name)}</b> liegt bei nur {s["activity_pct"]}% Wochenziel.')
        alerts = card("<h2 style='margin-top:0'>🚨 Aufmerksamkeit</h2>" + "".join(f"<div class='row'>{x}</div>" for x in warnings[:8]) if warnings else "<h2 style='margin-top:0'>🚨 Aufmerksamkeit</h2><div class='tiny'>Aktuell keine kritischen Hinweise.</div>")
        body = f'<div class="grid g4">{metrics}</div><div class="grid g2" style="margin-top:13px">{alerts}{card(f"<h2 style=\'margin-top:0\'>📊 Team-Score</h2><div class=\'metric\'>{avg_score}/100</div><p class=\'tiny\'>Durchschnittlicher Performance-Score aus Aktivität, Zuverlässigkeit, Support, Disziplin und Training.</p><div class=\'progress\'><div style=\'width:{avg_score}%\'></div></div>")}</div><div class="grid g3" style="margin-top:13px">{card(f"<h3 style=\'margin-top:0\'>🏢 Abteilungen</h3><div class=\'metric\'>{departments}</div><a class=\'btn primary\' href=\'/ultimate/departments\'>Verwalten</a>")}{card("<h3 style=\'margin-top:0\'>🤖 Automationen</h3><div class=\'metric\'>"+str(automations)+"</div><a class=\'btn primary\' href=\'/ultimate/automations\'>Regeln öffnen</a>")}{card("<h3 style=\'margin-top:0\'>🧩 Module</h3><div class=\'tiny\'>Cases · Roblox · Feedback · Polls · Kalender · Awards · Zertifikate · Dateien · API</div>")}</div>'
        return page(ctx, "ultimate", "Pulse Ultimate Command Center", "Zentrale Leitstelle für Team, Fälle, Automationen und Leistung.", body)

    @app.get("/ultimate/team", response_class=HTMLResponse)
    async def ultimate_team(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session)
        guild = ctx.guild
        members = sorted(team_members(guild), key=lambda x: x.display_name.lower())
        rows = []
        for m in members:
            s = member_stats(m)
            p = s["profile"]
            status = p.get("internal_status", "available")
            rows.append(
                f'<div class="card"><div class="row"><div><b>{esc(m.display_name)}</b><div class="tiny">@{esc(m.name)}</div></div>'
                f'<span class="pill">{esc(status)}</span></div><div class="tiny">Score</div><div class="metric">{s["score"]}/100</div>'
                f'<div class="tiny">Woche: {s["weekly_seconds"]//3600}h {(s["weekly_seconds"]%3600)//60}m · Warnungen: {s["warnings"]}/3</div>'
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
            {card(f"<div class=tiny>Warnungen</div><div class=metric>{s['warnings']}/3</div>")}
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
                """INSERT INTO ultimate_cases
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "upload.bin")[:120]
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Datei ist größer als 10 MB.")
        digest = hashlib.sha256(data).hexdigest()
        stored = f"{case_id}_{digest[:12]}_{safe}"
        path = ATTACHMENTS_DIR / stored
        path.write_bytes(data)
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_attachments VALUES(?,?,?,?,?,?,?,?,?,?)",
                (uid("file"), "case", case_id, safe, stored, file.content_type or "application/octet-stream",
                 len(data), digest, str(ctx.user["id"]),
                 ctx.user.get("global_name") or ctx.user.get("username") or "Team", iso()),
            )
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
            card(f"<div class=row><div><b>{esc(x['title'])}</b><div class=tiny>{esc(x['kind'])} · {esc(x['start_at'])}</div></div><a class='btn' href='/ultimate/calendar/{x['id']}/delete'>Löschen</a></div><div class=tiny>{esc(x['description'])}</div>")
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
            c.execute("UPDATE ultimate_handovers SET status='closed',archived_at=? WHERE id=?", (iso(), handover_id))
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

    @app.get("/ultimate/automations", response_class=HTMLResponse)
    async def ultimate_automations(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM ultimate_automations ORDER BY enabled DESC,next_run_at").fetchall()]
        opts = ["notify_managers", "notify_team", "create_case", "create_task", "team_report", "notify_user", "ticket_sla_alert", "inactivity_report", "probation_report"]
        cards = "".join(
            card(f"<div class=row><b>{esc(x['name'])}</b><span class='pill'>{'✅' if x['enabled'] else '⏸'}</span></div><div class='tiny'>{esc(x['trigger_type'])} · alle {x['interval_minutes']}min · nächster Lauf {esc(x['next_run_at'])}</div>")
            for x in rows
        )
        body = f"""<div class="card"><h3>🤖 Workflow/Automation</h3><form class="form" method="post" action="/ultimate/automations/create">
          <input class="input" name="name" placeholder="Überfällige Aufgaben melden" required>
          <input class="input" name="trigger_type" value="interval">
          <input class="input" type="number" name="interval_minutes" min="5" value="1440">
          <select class="select" name="action_type">{"".join(f"<option>{esc(x)}</option>" for x in opts)}</select>
          <input class="input span2" name="payload" value='{{}}' placeholder='JSON-Payload'>
          <button class="btn primary span2">Automation erstellen</button></form></div><div class='grid g2' style='margin-top:13px'>{cards or card("<div class=tiny>Keine Automationen.</div>")}</div>"""
        return page(ctx, "automations", "Automationen", "Wiederkehrende Workflows, Erinnerungen, Reports und Eskalationen.", body)

    @app.post("/ultimate/automations/create")
    async def ultimate_automation_create(
        request: Request, name: str = Form(...), trigger_type: str = Form("interval"),
        interval_minutes: int = Form(1440), action_type: str = Form(...),
        payload: str = Form("{}"), user_session: str = Cookie(None),
    ):
        ctx = ctx_auth(request, user_session, manager=True)
        try:
            obj = json.loads(payload)
            if not isinstance(obj, dict):
                raise ValueError
        except Exception:
            raise HTTPException(400, "Payload muss gültiges JSON-Objekt sein.")
        next_run = utcnow() + timedelta(minutes=max(5, min(10080, interval_minutes)))
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_automations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid("auto"), name.strip()[:160], trigger_type[:40], max(5, min(10080, interval_minutes)),
                 action_type[:60], json.dumps(obj, ensure_ascii=False), 1, iso(next_run), None,
                 str(ctx.user["id"]), ctx.user.get("global_name") or ctx.user.get("username") or "Team", iso()),
            )
        audit(ctx, "Automation erstellt", "", name.strip(), action_type)
        return RedirectResponse("/ultimate/automations", status_code=303)

    @app.get("/ultimate/system", response_class=HTMLResponse)
    async def ultimate_system(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        import webserver
        checks = []
        guild = ctx.guild
        checks.append(("🟢", "Discord", "verbunden" if guild else "nicht verfügbar"))
        checks.append(("🟢", "Pulse DB", "SQLite / WAL aktiv"))
        checks.append(("🟢", "OAuth", "konfiguriert" if webserver.CLIENT_ID and webserver.CLIENT_SECRET else "prüfen"))
        checks.append(("🟢" if guild.me and guild.me.guild_permissions.manage_roles else "🔴", "Manage Roles", "bereit" if guild.me and guild.me.guild_permissions.manage_roles else "fehlt"))
        try:
            wh = webserver.warning_role_health(guild, webserver.load_config())
            checks.append(("🟢" if all(x["ok"] for x in wh) else "🔴", "Warnrollen", f"{sum(x['ok'] for x in wh)}/3 bereit"))
        except Exception:
            checks.append(("🟡", "Warnrollen", "nicht prüfbar"))
        body = "<div class='grid g2'>" + "".join(card(f"<div class=row><b>{esc(n)}</b><span>{i} {esc(d)}</span></div>") for i,n,d in checks) + "</div>"
        body += card(f"<h3>🎨 Branding</h3><form method='post' action='/ultimate/system/branding' class='form'><input class='input' name='name' value='{esc((setting('branding',{}) or {}).get('name','Pulse TeamOS'))}'><input class='input' name='accent' value='{esc((setting('branding',{}) or {}).get('accent','#6366f1'))}'><input class='input span2' name='logo_url' value='{esc((setting('branding',{}) or {}).get('logo_url',''))}' placeholder='Logo URL'><button class='btn primary span2'>Branding speichern</button></form>")
        body += card("<h3>🔑 API-Zugriff</h3><form method='post' action='/ultimate/system/api-key' class='form'><input class='input span2' name='name' placeholder='z.B. Mobile App / externe Website' required><button class='btn primary span2'>API-Key erstellen</button></form><div class='tiny' style='margin-top:8px'>Die v2-Endpunkte nutzen den Header X-Pulse-API-Key. Schlüssel werden nur einmal angezeigt.</div><div style='margin-top:8px'><a class='btn' href='/ultimate/system/api-keys'>🔑 Schlüssel verwalten</a></div>")
        body += card("<h3>🛡 Sicherheitsmaßnahmen</h3><div class='tiny'>Rate Limits, signierte Sessions, Sicherheitsheader, Warnrollen-Healthcheck, Audit-Logs, Backups und Health-Endpunkt sind aktiv.</div>")
        return page(ctx, "system", "System & Branding", "Diagnose, Feature Flags und visuelle Serveranpassungen.", body)

    @app.get("/ultimate/system/api-keys", response_class=HTMLResponse)
    async def ultimate_api_keys(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_auth(request, user_session, manager=True)
        with cx() as c:
            keys = [dict(r) for r in c.execute(
                "SELECT id,name,created_at,revoked_at FROM ultimate_api_keys ORDER BY created_at DESC"
            ).fetchall()]
        rows = "".join(
            card(f"<div class=row><div><b>{esc(k['name'])}</b><div class='tiny'>{esc(k['created_at'])}</div></div><span class='pill'>{'widerrufen' if k['revoked_at'] else 'aktiv'}</span></div>{'' if k['revoked_at'] else f"<form method='post' action='/ultimate/system/api-key/{k['id']}/revoke'><button class='btn danger'>Widerrufen</button></form>"}")
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
        for m in team_members(ctx.guild):
            s = member_stats(m)
            rows.append({
                "id": m.id, "name": m.display_name, "discord_status": str(m.status),
                "score": s["score"], "weekly_seconds": s["weekly_seconds"],
                "warnings": s["warnings"], "activity_pct": s["activity_pct"],
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
        stats = [(m, member_stats(m)) for m in members]
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
                "INSERT INTO ultimate_certificates VALUES(?,?,?,?,?,?,?)",
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
        raw = "pulse_" + secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        with cx() as c:
            c.execute(
                "INSERT INTO ultimate_api_keys VALUES(?,?,?,?,?,?)",
                (uid("key"), name.strip()[:100], token_hash, str(ctx.user["id"]), iso(), None),
            )
        # Token is returned exactly once.
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;padding:30px;background:#070b12;color:#fff'>"
            f"<h2>API-Key erstellt</h2><p>Speichere diesen Schlüssel jetzt sicher:</p>"
            f"<code style='display:block;padding:15px;background:#111b2a;border-radius:10px'>{esc(raw)}</code>"
            f"<p>Der Token wird danach nicht erneut angezeigt.</p><a href='/ultimate/system'>Zurück</a></body></html>"
        )

    @app.get("/ultimate/api/v2/overview")
    async def ultimate_api_v2_overview(request: Request):
        api_key = request.headers.get("X-Pulse-API-Key", "")
        if not validate_api_key(api_key):
            raise HTTPException(401, "Ungültiger API-Key.")
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
        api_key = request.headers.get("X-Pulse-API-Key", "")
        if not validate_api_key(api_key):
            raise HTTPException(401, "Ungültiger API-Key.")
        import webserver
        bot = getattr(request.app.state, "bot", None)
        guild = bot.get_guild(webserver.GUILD_ID) if bot else None
        if not guild:
            raise HTTPException(503, "Discord nicht bereit.")
        rows = []
        for m in team_members(guild):
            s = member_stats(m)
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
        for m in members:
            s = member_stats(m)
            data.append({"id":m.id,"name":m.display_name,"score":s["score"],"weekly_hours":round(s["weekly_seconds"]/3600,2),"tickets":s["closed_tickets"],"warnings":s["warnings"]})
        return JSONResponse({"generated_at":iso(),"members":data})

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
        top = sorted(((member_stats(m)["score"], m) for m in members), reverse=True, key=lambda x:x[0])[:10]
        desc = "\n".join(f"{i}. {m.display_name} · {score}/100" for i,(score,m) in enumerate(top,1)) or "Keine Teamdaten"
        channel = guild.get_channel(TEAM_UPDATE_CHANNEL_ID)
        if channel:
            embed = discord.Embed(title="📊 Pulse Team-Report", description=desc, color=discord.Color.blurple())
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


async def automation_loop(bot):
    setup()
    while True:
        try:
            now = utcnow()
            with cx() as c:
                rows = [dict(r) for r in c.execute(
                    "SELECT * FROM ultimate_automations WHERE enabled=1 AND next_run_at<=?",
                    (iso(now),),
                ).fetchall()]
            for automation in rows:
                run_id = uid("run")
                started = iso()
                try:
                    result = await _run_automation(bot, automation)
                    run_status = "success"
                except Exception as exc:
                    result = str(exc)
                    run_status = "error"
                with cx() as c:
                    c.execute(
                        "INSERT INTO ultimate_automation_runs VALUES(?,?,?,?,?,?)",
                        (run_id, automation["id"], run_status, result[:3000], started, iso()),
                    )
                    next_run = now + timedelta(minutes=max(5, int(automation["interval_minutes"])))
                    c.execute(
                        "UPDATE ultimate_automations SET last_run_at=?,next_run_at=? WHERE id=?",
                        (iso(), iso(next_run), automation["id"]),
                    )
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(60)


async def start_automation(bot):
    global _AUTOMATION_TASK
    if _AUTOMATION_TASK and not _AUTOMATION_TASK.done():
        return _AUTOMATION_TASK
    _AUTOMATION_TASK = asyncio.create_task(automation_loop(bot))
    return _AUTOMATION_TASK
