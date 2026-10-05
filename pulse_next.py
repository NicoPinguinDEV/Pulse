"""Pulse TeamOS 2.0 extension.

Adds integrated personnel workflow, approvals, announcements, goals, ideas,
achievements, global search, reports/exports, backup/restore, permission
overrides, onboarding/offboarding, workflows, richer analytics and activity
telemetry without replacing legacy Pulse modules.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Cookie, Form, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse

import pulse_db as db
import pulse_ultimate as u

BASE = Path(__file__).resolve().parent
BACKUP_DIR = BASE / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

_INIT = False

PERMISSIONS = (
    "can_view_dashboard", "can_warn", "can_promote", "can_add_notes",
    "can_manage_tickets", "can_manage_applications", "can_manage_tasks",
    "can_manage_training", "can_manage_wiki", "can_view_analytics",
    "can_approve", "can_manage_goals", "can_manage_announcements",
    "can_manage_backups", "can_manage_permissions", "can_manage_workflows",
)

ACHIEVEMENTS = (
    ("hours_10", "⏱ 10 Dienststunden", "10 Dienststunden erreicht.", "10h"),
    ("hours_50", "⏱ 50 Dienststunden", "50 Dienststunden erreicht.", "50h"),
    ("hours_100", "🏅 100 Dienststunden", "100 Dienststunden erreicht.", "100h"),
    ("tickets_10", "🎫 Support-Profi", "10 Tickets abgeschlossen.", "10t"),
    ("tickets_50", "🎫 Support-Veteran", "50 Tickets abgeschlossen.", "50t"),
    ("training_5", "🎓 Lernbereit", "5 Trainings/Prüfungen bestanden.", "5t"),
    ("score_90", "⭐ Elite-Score", "Team-Score von mindestens 90.", "90"),
    ("active_4w", "🔥 4 Wochen aktiv", "Zielerreichung über mehrere Wochen.", "4w"),
)

def esc(value) -> str:
    return u.esc(value)

def iso(dt: Optional[datetime] = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat(timespec="seconds")

def now() -> datetime:
    return datetime.now(timezone.utc)

def cx():
    c = sqlite3.connect(u.ULTIMATE_DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("PRAGMA journal_mode=WAL")
    return c

def setup_next() -> None:
    global _INIT
    if _INIT:
        return
    u.setup()
    with cx() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS next_activity(
                user_id TEXT PRIMARY KEY,
                last_seen_at TEXT NOT NULL,
                last_kind TEXT DEFAULT 'dashboard',
                total_actions INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS next_team_events(
                id TEXT PRIMARY KEY,
                user_id TEXT,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT DEFAULT '',
                actor_id TEXT,
                actor_name TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_next_events_user
                ON next_team_events(user_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS next_promotions(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                from_rank TEXT DEFAULT '',
                to_rank TEXT NOT NULL,
                reason TEXT DEFAULT '',
                target_role_id TEXT DEFAULT '',
                old_role_id TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                requested_by_id TEXT NOT NULL,
                requested_by_name TEXT NOT NULL,
                approved_by_id TEXT,
                approved_by_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS next_approvals(
                id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                action TEXT NOT NULL,
                requester_id TEXT NOT NULL,
                requester_name TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                approver_id TEXT,
                approver_name TEXT,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                decided_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_next_approvals_status
                ON next_approvals(status,created_at DESC);
            CREATE TABLE IF NOT EXISTS next_announcements(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                priority TEXT DEFAULT 'normal',
                audience TEXT DEFAULT 'team',
                pinned INTEGER DEFAULT 0,
                expires_at TEXT,
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS next_announcement_reads(
                announcement_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                read_at TEXT NOT NULL,
                PRIMARY KEY(announcement_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS next_ideas(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                status TEXT DEFAULT 'new',
                author_id TEXT,
                author_name TEXT DEFAULT '',
                manager_note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS next_idea_votes(
                idea_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                vote INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(idea_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS next_goals(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                owner_type TEXT DEFAULT 'team',
                owner_id TEXT DEFAULT '',
                target REAL DEFAULT 100,
                current REAL DEFAULT 0,
                unit TEXT DEFAULT '%',
                due_at TEXT,
                status TEXT DEFAULT 'active',
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS next_goal_updates(
                id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                value REAL NOT NULL,
                note TEXT DEFAULT '',
                actor_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS next_achievements(
                key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                badge TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS next_member_achievements(
                user_id TEXT NOT NULL,
                achievement_key TEXT NOT NULL,
                awarded_at TEXT NOT NULL,
                PRIMARY KEY(user_id,achievement_key)
            );
            CREATE TABLE IF NOT EXISTS next_permission_overrides(
                user_id TEXT NOT NULL,
                permission TEXT NOT NULL,
                allowed INTEGER NOT NULL,
                updated_by_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,permission)
            );
            CREATE TABLE IF NOT EXISTS next_workflows(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                condition_json TEXT DEFAULT '{}',
                actions_json TEXT DEFAULT '[]',
                enabled INTEGER DEFAULT 1,
                cooldown_minutes INTEGER DEFAULT 1440,
                last_run_at TEXT,
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS next_workflow_runs(
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS next_score_snapshots(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                activity_pct INTEGER DEFAULT 0,
                reliability_pct INTEGER DEFAULT 0,
                support_pct INTEGER DEFAULT 0,
                discipline_pct INTEGER DEFAULT 0,
                training_pct INTEGER DEFAULT 0,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_next_score_user
                ON next_score_snapshots(user_id,recorded_at DESC);
            """
        )
        for key, title, desc, badge in ACHIEVEMENTS:
            c.execute(
                "INSERT OR IGNORE INTO next_achievements(key,title,description,badge) VALUES(?,?,?,?)",
                (key,title,desc,badge),
            )
    _INIT = True

def actor_name(ctx) -> str:
    return ctx.user.get("global_name") or ctx.user.get("username") or "Team"

def actor_id(ctx) -> str:
    return str(ctx.user["id"])

def ctx_auth(request: Request, session: str, manager: bool = False, perm: Optional[str] = "can_view_dashboard"):
    ctx = u.ctx_auth(request, session, manager=manager, perm=perm)
    touch_member(actor_id(ctx), "dashboard")
    return ctx

def has_perm(ctx, perm: str) -> bool:
    if ctx.perms.get("is_admin") or ctx.perms.get(perm):
        return True
    setup_next()
    with cx() as c:
        row = c.execute(
            "SELECT allowed FROM next_permission_overrides WHERE user_id=? AND permission=?",
            (str(ctx.user["id"]), perm),
        ).fetchone()
    return bool(row and row["allowed"])

def require_perm(ctx, perm: str):
    if not has_perm(ctx, perm):
        raise HTTPException(403, "Keine Berechtigung.")

def touch_member(user_id: str, kind: str = "dashboard"):
    setup_next()
    n = iso()
    with cx() as c:
        c.execute(
            """INSERT INTO next_activity(user_id,last_seen_at,last_kind,total_actions)
               VALUES(?,?,?,1)
               ON CONFLICT(user_id) DO UPDATE SET
                 last_seen_at=excluded.last_seen_at,
                 last_kind=excluded.last_kind,
                 total_actions=next_activity.total_actions+1""",
            (str(user_id),n,kind),
        )
        # updated_at is intentionally meaningful activity, not page rendering.
        c.execute(
            "UPDATE ultimate_profiles SET updated_at=? WHERE user_id=?",
            (n,str(user_id)),
        )

def event(user_id: str, event_type: str, title: str, details: str = "", actor_id: str = "", actor_name: str = ""):
    setup_next()
    with cx() as c:
        c.execute(
            "INSERT INTO next_team_events VALUES(?,?,?,?,?,?,?,?)",
            (u.uid("te"),str(user_id),event_type,title[:180],details[:4000],
             actor_id or str(user_id),actor_name[:120],iso()),
        )

def seed_profile_for_member(member):
    u.ensure_profile(member)
    with cx() as c:
        c.execute(
            "INSERT OR IGNORE INTO next_activity(user_id,last_seen_at,last_kind,total_actions) VALUES(?,?,?,0)",
            (str(member.id), iso(), "onboarding"),
        )

def members(guild):
    return u.team_members(guild) if guild else []

def role_names(member):
    return [r.name for r in getattr(member,"roles",[]) if r.name != "@everyone"]

def fmt_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")
    except Exception:
        return esc(value)

def progress(current, target):
    try:
        return max(0,min(100,round(float(current)/max(0.01,float(target))*100)))
    except Exception:
        return 0

def next_page(ctx, active, title, subtitle, body, search=True):
    extra = """
    <div class="pulse-mobile-actions">
      <a href="/ultimate">⚡</a><a href="/ultimate/search">🔎</a><a href="/ultimate/team">👥</a><a href="/tasks">📋</a><a href="/ultimate/approvals">✅</a>
    </div>
    <style>.pulse-mobile-actions{display:none}@media(max-width:800px){.pulse-mobile-actions{display:flex;position:fixed;left:10px;right:10px;bottom:10px;z-index:20;justify-content:space-around;background:#0b1320f5;border:1px solid #ffffff15;border-radius:15px;padding:8px;backdrop-filter:blur(12px)}.pulse-mobile-actions a{font-size:20px;padding:6px 14px}}</style>
    """
    return u.page(ctx, active, title, subtitle, body, extra)

def save_audit(ctx, action, target="", details=""):
    u.audit(ctx, action, target, target, details)
    touch_member(actor_id(ctx), "action")

def team_score_class(score):
    if score < 40: return ("kritisch","🔴")
    if score < 60: return ("schwach","🟠")
    if score < 75: return ("solide","🟡")
    if score < 90: return ("gut","🟢")
    return ("hervorragend","⭐")

def get_activity(user_id: str):
    setup_next()
    with cx() as c:
        r = c.execute("SELECT * FROM next_activity WHERE user_id=?", (str(user_id),)).fetchone()
    return dict(r) if r else None

def timeline(user_id: str, limit=100):
    setup_next()
    with cx() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM next_team_events WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (str(user_id), max(1,min(200,int(limit)))),
        ).fetchall()]

def score_history(user_id: str, limit=30):
    setup_next()
    with cx() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM next_score_snapshots WHERE user_id=? ORDER BY recorded_at DESC LIMIT ?",
            (str(user_id),max(1,min(100,int(limit)))),
        ).fetchall()]

def award_achievements(member):
    try:
        stats = u.member_stats(member)
    except Exception:
        return 0
    unlocked = []
    rules = {
        "hours_10": stats["total_seconds"] >= 10*3600,
        "hours_50": stats["total_seconds"] >= 50*3600,
        "hours_100": stats["total_seconds"] >= 100*3600,
        "tickets_10": stats["closed_tickets"] >= 10,
        "tickets_50": stats["closed_tickets"] >= 50,
        "training_5": stats["training_passed"] >= 5,
        "score_90": stats["score"] >= 90,
        "active_4w": stats["activity_pct"] >= 100,
    }
    with cx() as c:
        for key, ok in rules.items():
            if not ok:
                continue
            cur = c.execute(
                "INSERT OR IGNORE INTO next_member_achievements VALUES(?,?,?)",
                (str(member.id),key,iso()),
            )
            if cur.rowcount:
                unlocked.append(key)
    return len(unlocked)

def record_score(member):
    try:
        s = u.member_stats(member)
    except Exception:
        return
    today = now().date().isoformat()
    with cx() as c:
        exists = c.execute(
            "SELECT 1 FROM next_score_snapshots WHERE user_id=? AND substr(recorded_at,1,10)=?",
            (str(member.id),today),
        ).fetchone()
        if not exists:
            c.execute(
                """INSERT INTO next_score_snapshots VALUES(?,?,?,?,?,?,?,?,?)""",
                (u.uid("score"),str(member.id),s["score"],s["activity_pct"],s["reliability_pct"],
                 s["support_pct"],s["discipline_pct"],s["training_pct"],iso()),
            )
    award_achievements(member)

def all_stats(guild):
    rows=[]
    for m in members(guild):
        record_score(m)
        try: rows.append((m,u.member_stats(m)))
        except Exception: pass
    return rows

def get_cases_for_search(q):
    with u.cx() as c:
        return [dict(r) for r in c.execute(
            """SELECT id,case_no,title,status,priority,subject_name
               FROM ultimate_cases
               WHERE case_no LIKE ? OR title LIKE ? OR subject_name LIKE ?
               ORDER BY updated_at DESC LIMIT 20""",
            (f"%{q}%",f"%{q}%",f"%{q}%"),
        ).fetchall()]

def get_search_results(ctx, q):
    q = q.strip()[:100]
    if not q:
        return {}
    result={"team":[],"cases":get_cases_for_search(q),"roblox":[],"ideas":[],"announcements":[],"tasks":[],"tickets":[]}
    ql=q.lower()
    for m in members(ctx.guild):
        if ql in m.display_name.lower() or ql in m.name.lower() or q in str(m.id):
            result["team"].append({"id":m.id,"name":m.display_name,"roles":role_names(m)})
    with u.cx() as c:
        result["roblox"]=[dict(r) for r in c.execute(
            "SELECT roblox_id,username,display_name,status FROM ultimate_roblox_profiles WHERE username LIKE ? OR display_name LIKE ? OR roblox_id LIKE ? LIMIT 20",
            (f"%{q}%",f"%{q}%",f"%{q}%"),
        ).fetchall()]
        result["ideas"]=[dict(r) for r in c.execute(
            "SELECT id,title,status,category FROM next_ideas WHERE title LIKE ? OR body LIKE ? ORDER BY updated_at DESC LIMIT 20",
            (f"%{q}%",f"%{q}%"),
        ).fetchall()]
        result["announcements"]=[dict(r) for r in c.execute(
            "SELECT id,title,priority,created_at FROM next_announcements WHERE title LIKE ? OR body LIKE ? ORDER BY created_at DESC LIMIT 20",
            (f"%{q}%",f"%{q}%"),
        ).fetchall()]
    try:
        result["tasks"]=[t for t in db.list_tasks(limit=500,include_archived=True) if ql in str(t.get("title","")).lower()][:20]
    except Exception: pass
    try:
        result["tickets"]=[t for t in db.list_tickets(limit=5000) if ql in str(t).lower()][:20]
    except Exception: pass
    return result

async def promote_now(ctx, row):
    import webserver
    guild = ctx.guild
    member = guild.get_member(int(row["user_id"]))
    if not member:
        return "Teammitglied nicht gefunden."
    role_id = str(row["target_role_id"] or "")
    old_role_id = str(row["old_role_id"] or "")
    if not role_id.isdigit():
        return "Keine gültige Zielrolle hinterlegt; Freigabe wurde trotzdem dokumentiert."
    role = guild.get_role(int(role_id))
    if not role:
        return "Zielrolle nicht gefunden."
    me = guild.me
    if me and role >= me.top_role:
        return "Discord verweigert die Rollenänderung: Zielrolle liegt über der Botrolle."
    try:
        awaitable = member.add_roles(role, reason=f"Pulse Beförderung {row['id']}")
    except Exception as exc:
        return f"Rolle konnte nicht vergeben werden: {exc}"
    import asyncio
    if asyncio.iscoroutine(awaitable):
        awaitable = awaitable
    if old_role_id.isdigit():
        old = guild.get_role(int(old_role_id))
        if old and (not me or old < me.top_role):
            try:
                awaitable2 = member.remove_roles(old, reason=f"Pulse Beförderung {row['id']}")
                if asyncio.iscoroutine(awaitable2):
                    await awaitable2
            except Exception:
                pass
    event(str(member.id),"promotion","Beförderung abgeschlossen",f"{row['from_rank']} → {row['to_rank']}",actor_id(ctx),actor_name(ctx))
    db.notify(member.id,"🏆 Beförderung",f"Du wurdest zu {row['to_rank']} befördert.","success","/ultimate/person/"+str(member.id))
    return "Beförderung angewendet."

def approval_rows(ctx):
    setup_next()
    with cx() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM next_approvals WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()]

def create_approval(object_type, object_id, action, ctx):
    with cx() as c:
        c.execute(
            "INSERT INTO next_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (u.uid("appr"),object_type,object_id,action,actor_id(ctx),actor_name(ctx),"pending",None,None,"",iso(),None),
        )

def create_backup():
    setup_next()
    ts = now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"pulse_backup_{ts}.zip"
    db_path = Path(u.ULTIMATE_DB)
    try:
        with u.cx() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    files = [
        db_path,
        BASE/"team_data.json", BASE/"config.json", BASE/"applications.json",
        BASE/"shifts.json", BASE/"logs.json", BASE/"audit_logs.json",
        BASE/"meetings.json",
    ]
    with zipfile.ZipFile(target,"w",compression=zipfile.ZIP_DEFLATED) as z:
        manifest={"created_at":iso(),"version":getattr(__import__("webserver"),"PULSE_VERSION","unknown")}
        z.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        for p in files:
            if p.exists() and p.is_file():
                z.write(p,arcname=p.name)
        # Include attachments up to a safe total size so normal backups remain practical.
        total = 0
        if u.ATTACHMENTS_DIR.exists():
            for p in u.ATTACHMENTS_DIR.rglob("*"):
                if not p.is_file():
                    continue
                size = p.stat().st_size
                if total + size > 250 * 1024 * 1024:
                    continue
                z.write(p,arcname=f"attachments/{p.relative_to(u.ATTACHMENTS_DIR)}")
                total += size
    return target

def safe_backup_file(name):
    name = os.path.basename(name)
    if not re.fullmatch(r"pulse_backup_[0-9]{8}_[0-9]{6}\.zip",name):
        raise HTTPException(400,"Ungültiges Backup.")
    p=BACKUP_DIR/name
    if not p.exists(): raise HTTPException(404,"Backup nicht gefunden.")
    return p

def restore_backup(path):
    with zipfile.ZipFile(path) as z:
        names=set(z.namelist())
        if "pulse.db" not in names:
            raise HTTPException(400,"Backup enthält keine pulse.db.")
        tmp = BASE/"pulse.restore.tmp.db"
        with z.open("pulse.db") as src, open(tmp,"wb") as dst:
            shutil.copyfileobj(src,dst)
        with cx() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.replace(tmp,u.ULTIMATE_DB)
        # Restore the small JSON runtime stores that were captured with the DB.
        json_names = {"team_data.json","config.json","applications.json","shifts.json","logs.json","audit_logs.json","meetings.json"}
        for name in json_names:
            if name not in names:
                continue
            tmp_json = BASE / f"{name}.restore.tmp"
            with z.open(name) as src, open(tmp_json,"wb") as dst:
                shutil.copyfileobj(src,dst)
            os.replace(tmp_json, BASE / name)
        # Restore attachments if present in the archive.
        prefix = "attachments/"
        for name in names:
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            rel = Path(name[len(prefix):])
            if ".." in rel.parts or rel.is_absolute():
                continue
            target_file = u.ATTACHMENTS_DIR / rel
            target_file.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(target_file,"wb") as dst:
                shutil.copyfileobj(src,dst)
    # Cached initialization is invalid after replacing the DB.
    u._INIT = False
    setup_next()
    return True

async def run_workflows(bot=None):
    setup_next()
    with cx() as c:
        workflows=[dict(r) for r in c.execute("SELECT * FROM next_workflows WHERE enabled=1").fetchall()]
    out=0
    for w in workflows:
        try:
            if w["last_run_at"]:
                last=datetime.fromisoformat(str(w["last_run_at"]).replace("Z","+00:00"))
                if (now()-last).total_seconds() < int(w["cooldown_minutes"])*60:
                    continue
            condition=json.loads(w["condition_json"] or "{}")
            actions=json.loads(w["actions_json"] or "[]")
            guild = getattr(bot,"get_guild",lambda _ : None)(
                int(os.getenv("DISCORD_GUILD_ID","1474514929351524616"))
            ) if bot else None
            if not guild:
                continue
            team=members(guild)
            if condition.get("type")=="team_online_gte":
                online=sum(str(m.status) in {"online","idle","dnd"} for m in team)
                if online < float(condition.get("value",0)): continue
            elif condition.get("type")=="inactivity_gte":
                days=int(condition.get("value",14))
                found=[]
                for m in team:
                    a=get_activity(str(m.id))
                    if a:
                        try:
                            age=(now()-datetime.fromisoformat(a["last_seen_at"].replace("Z","+00:00"))).days
                            if age>=days: found.append((m,age))
                        except Exception: pass
                if not found: continue
            for action in actions[:10]:
                kind=str(action.get("type",""))
                if kind=="notify_managers":
                    for m in team:
                        perms,_=__import__("webserver").compute_perms(guild,m.id,__import__("webserver").load_config())
                        if perms.get("can_promote") or perms.get("is_admin"):
                            db.notify(m.id,str(action.get("title") or w["name"]),str(action.get("body") or "Pulse Workflow"),"warning",str(action.get("url") or "/ultimate"))
                elif kind=="create_task":
                    uid=action.get("assignee_id")
                    target=guild.get_member(int(uid)) if str(uid).isdigit() else None
                    if target:
                        db.create_task(str(action.get("title") or w["name"])[:160],str(action.get("description") or "")[:2500],target.id,target.display_name,"0","Pulse Workflow",str(action.get("priority") or "normal"))
                elif kind=="create_case":
                    with u.cx() as c:
                        c.execute(
                            """INSERT INTO ultimate_cases
                            (id,case_no,title,category,priority,status,subject_type,subject_id,subject_name,description,
                             assignee_id,assignee_name,created_by_id,created_by_name,created_at,updated_at,closed_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (u.uid("case"),u.case_number(),str(action.get("title") or w["name"])[:160],
                             str(action.get("category") or "workflow")[:40],str(action.get("priority") or "normal"),
                             "open","workflow",str(action.get("subject_id") or ""),str(action.get("subject_name") or ""),
                             str(action.get("description") or "")[:5000],None,"","0","Pulse Workflow",iso(),iso(),None),
                        )
            with cx() as c:
                c.execute(
                    "UPDATE next_workflows SET last_run_at=? WHERE id=?",(iso(),w["id"])
                )
                c.execute(
                    "INSERT INTO next_workflow_runs VALUES(?,?,?,?,?)",
                    (u.uid("wr"),w["id"],"success",f"{len(actions)} Aktionen",iso()),
                )
            out+=1
        except Exception as exc:
            with cx() as c:
                c.execute(
                    "INSERT INTO next_workflow_runs VALUES(?,?,?,?,?)",
                    (u.uid("wr"),w["id"],"error",str(exc)[:3000],iso()),
                )
    return out

def register(app):
    setup_next()
    if getattr(app.state,"pulse_next_registered",False):
        return

    @app.get("/ultimate/person/{user_id}", response_class=HTMLResponse)
    async def person(request: Request, user_id: str, user_session: str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        member=ctx.guild.get_member(int(user_id)) if str(user_id).isdigit() else None
        if not member or member.bot or member not in members(ctx.guild):
            raise HTTPException(404,"Teammitglied nicht gefunden.")
        u.ensure_profile(member)
        stats=u.member_stats(member)
        cls,icon=team_score_class(stats["score"])
        hist=score_history(str(member.id),20)
        bars="".join(f"<div style='display:inline-block;width:7px;height:{max(8,int(x['score'])*1.1)}px;background:var(--pulse-accent);margin-right:3px;border-radius:4px' title='{fmt_dt(x['recorded_at'])}: {x['score']}'></div>" for x in reversed(hist)) or "<span class=tiny>Noch keine Score-Historie.</span>"
        ach=[]
        with cx() as c:
            ach=[dict(r) for r in c.execute(
                """SELECT a.title,a.description,a.badge,ma.awarded_at
                   FROM next_member_achievements ma JOIN next_achievements a ON a.key=ma.achievement_key
                   WHERE ma.user_id=? ORDER BY ma.awarded_at DESC""",(str(member.id),)
            ).fetchall()]
        timeline_rows=timeline(str(member.id),50)
        events_html="".join(f"<div class=row><div><b>{esc(x['title'])}</b><div class=tiny>{esc(x['details'])}</div></div><span class=tiny>{fmt_dt(x['created_at'])}</span></div>" for x in timeline_rows) or "<div class=tiny>Noch keine Pulse-Events.</div>"
        roles=", ".join(role_names(member)) or "Keine Rollen"
        body=f"""
        <div class='grid g4'>
          {u.card(f"<div class=tiny>Score</div><div class=metric>{stats['score']}/100</div><span class=pill>{icon} {cls}</span>")}
          {u.card(f"<div class=tiny>Dienstzeit gesamt</div><div class=metric>{stats['total_seconds']//3600}h</div><div class=tiny>{stats['shifts']} Schichten</div>")}
          {u.card(f"<div class=tiny>Tickets</div><div class=metric>{stats['closed_tickets']}</div><div class=tiny>abgeschlossen</div>")}
          {u.card(f"<div class=tiny>Warnungen</div><div class=metric>{stats['warnings']}/3</div>")}
        </div>
        <div class='grid g2' style='margin-top:13px'>
          {u.card(f"<h3 style='margin-top:0'>👤 Teamprofil</h3><div class=row><b>{esc(member.display_name)}</b><span class=pill>{esc(str(member.status))}</span></div><div class=tiny>Rollen</div><p>{esc(roles)}</p><div class=tiny>Aktuelle Aktivität</div><p>{esc((get_activity(str(member.id)) or {}).get('last_kind','unbekannt'))}</p><a class='btn' href='/ultimate/person/{member.id}/export'>Profil exportieren</a>")}
          {u.card(f"<h3 style='margin-top:0'>📈 Score-Verlauf</h3><div style='height:130px;display:flex;align-items:end'>{bars}</div><div class='tiny'>Tages-Snapshots</div>")}
        </div>
        <div class='grid g2' style='margin-top:13px'>
          {u.card("<h3 style='margin-top:0'>🏅 Achievements</h3>"+("".join(f"<div class=row><span>{esc(x['badge'])} <b>{esc(x['title'])}</b></span><span class=tiny>{fmt_dt(x['awarded_at'])}</span></div>" for x in ach) or "<div class=tiny>Noch keine Achievements.</div>"))}
          {u.card("<h3 style='margin-top:0'>🕒 Zeitleiste</h3>"+events_html)}
        </div>
        <div class='card' style='margin-top:13px'><h3>🏆 Beförderung vorbereiten</h3>
          <form class='form' method='post' action='/ultimate/promotions/create'>
            <input type=hidden name=user_id value='{member.id}'>
            <input class=input name=from_rank placeholder='Aktueller Rang'>
            <input class=input name=to_rank placeholder='Zielrang' required>
            <input class=input name=target_role_id placeholder='Discord Zielrollen-ID'>
            <input class=input name=old_role_id placeholder='Bisherige Rollen-ID'>
            <textarea class='ta span2' name=reason placeholder='Begründung'></textarea>
            <button class='btn primary span2'>4-Augen-Beförderung beantragen</button>
          </form>
        </div>
        """
        return next_page(ctx,"team",f"Teamakte · {member.display_name}","Leistungsprofil, Entwicklung und Führungsaktionen.",body)

    @app.get("/ultimate/person/{user_id}/export")
    async def person_export(request: Request,user_id: str,user_session: str=Cookie(None)):
        ctx=ctx_auth(request,user_session,perm="can_view_dashboard")
        member=ctx.guild.get_member(int(user_id)) if str(user_id).isdigit() else None
        if not member: raise HTTPException(404,"Teammitglied nicht gefunden.")
        s=u.member_stats(member); p=u.get_profile(str(member.id)) or {}
        payload={"profile":p,"discord":{"id":member.id,"name":member.display_name,"status":str(member.status),"roles":role_names(member)},"stats":s,"activity":get_activity(str(member.id)),"timeline":timeline(str(member.id),200)}
        return HTMLResponse(json.dumps(payload,ensure_ascii=False,indent=2),headers={"Content-Type":"application/json","Content-Disposition":f"attachment; filename=pulse_person_{member.id}.json"})

    @app.get("/ultimate/approvals", response_class=HTMLResponse)
    async def approvals(request: Request,user_session: str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=False,perm="can_view_dashboard")
        require_perm(ctx,"can_approve")
        rows=approval_rows(ctx)
        html_rows=[]
        with cx() as c:
            for a in rows:
                target=c.execute("SELECT * FROM next_promotions WHERE id=?",(a["object_id"],)).fetchone() if a["object_type"]=="promotion" else None
                label=(f"Beförderung · {target['user_name'] if target else a['object_id']} → {target['to_rank'] if target else ''}" if target else f"{a['action']} · {a['object_id']}")
                html_rows.append(u.card(f"<div class=row><div><b>{esc(label)}</b><div class=tiny>Antrag von {esc(a['requester_name'])} · {fmt_dt(a['created_at'])}</div></div><form method=post action='/ultimate/approvals/{a['id']}/decide' class=form><input class=input name=note placeholder='Freigabebegründung'><select class=select name=decision><option value=approve>Freigeben</option><option value=reject>Ablehnen</option></select><button class='btn primary'>Entscheiden</button></form></div>"))
        body=u.card("<h3 style='margin-top:0'>✅ Offene Freigaben</h3>"+"".join(html_rows) or "<div class=tiny>Keine offenen Freigaben.</div>")
        return next_page(ctx,"approvals","Freigaben","4-Augen-Prinzip für sensible Teamaktionen.",body)

    @app.post("/ultimate/promotions/create")
    async def promotions_create(request: Request,user_id:str=Form(...),from_rank:str=Form(""),to_rank:str=Form(...),reason:str=Form(""),target_role_id:str=Form(""),old_role_id:str=Form(""),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_promote")
        member=ctx.guild.get_member(int(user_id)) if user_id.isdigit() else None
        if not member: raise HTTPException(404,"Teammitglied nicht gefunden.")
        pid=u.uid("promo"); t=iso()
        with cx() as c:
            c.execute("INSERT INTO next_promotions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid,str(member.id),member.display_name,from_rank[:80],to_rank[:80],reason[:3000],target_role_id[:32],old_role_id[:32],
                 "pending",actor_id(ctx),actor_name(ctx),t,t))
        create_approval("promotion",pid,"promotion",ctx)
        event(str(member.id),"promotion_request","Beförderung beantragt",f"{from_rank} → {to_rank}",actor_id(ctx),actor_name(ctx))
        db.notify(member.id,"🏆 Beförderung in Prüfung",f"Ein Beförderungsantrag für {to_rank} wurde gestellt.","info","/ultimate/person/"+str(member.id))
        save_audit(ctx,"Beförderung beantragt",str(member.id),f"{from_rank}->{to_rank}")
        return RedirectResponse("/ultimate/approvals",status_code=303)

    @app.post("/ultimate/approvals/{approval_id}/decide")
    async def approval_decide(request: Request,approval_id:str,decision:str=Form(...),note:str=Form(""),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_approve")
        with cx() as c:
            appr=c.execute("SELECT * FROM next_approvals WHERE id=? AND status='pending'",(approval_id,)).fetchone()
        if not appr: raise HTTPException(404,"Freigabe nicht gefunden.")
        if str(appr["requester_id"])==actor_id(ctx): raise HTTPException(403,"Antragsteller darf nicht selbst freigeben.")
        final="approved" if decision=="approve" else "rejected"
        target_row=None
        if appr["object_type"]=="promotion":
            with cx() as c:
                target_row=c.execute("SELECT * FROM next_promotions WHERE id=?",(appr["object_id"],)).fetchone()
            if not target_row: raise HTTPException(404,"Beförderung nicht gefunden.")
        with cx() as c:
            c.execute("UPDATE next_approvals SET status=?,approver_id=?,approver_name=?,note=?,decided_at=? WHERE id=?",
                      (final,actor_id(ctx),actor_name(ctx),note[:2000],iso(),approval_id))
            if target_row:
                c.execute("UPDATE next_promotions SET status=?,approved_by_id=?,approved_by_name=?,updated_at=? WHERE id=?",
                          (final,actor_id(ctx),actor_name(ctx),iso(),target_row["id"]))
        result="Freigabe gespeichert."
        if final=="approved" and target_row:
            result=await promote_now(ctx,dict(target_row))
        elif target_row:
            event(str(target_row["user_id"]),"promotion_rejected","Beförderung abgelehnt",note,actor_id(ctx),actor_name(ctx))
        save_audit(ctx,"Freigabe entschieden",approval_id,result)
        return RedirectResponse("/ultimate/approvals",status_code=303)

    @app.get("/ultimate/search", response_class=HTMLResponse)
    async def global_search(request: Request,q:str=Query(""),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        result=get_search_results(ctx,q) if q else {}
        sections=[]
        sections.append(u.card(f"<form method=get class=form><input autofocus class='input span2' name=q value='{esc(q)}' placeholder='Name, ID, Fall, Ticket, Roblox-Spieler, Aufgabe...'><button class='btn primary span2'>🔎 Suchen</button></form>"))
        labels=[("team","👥 Team"),("cases","🚨 Fälle"),("roblox","🎮 Roblox"),("ideas","💡 Ideen"),("announcements","📢 News"),("tasks","📋 Aufgaben"),("tickets","🎫 Tickets")]
        for key,title in labels:
            arr=result.get(key,[])
            if not arr: continue
            rows=[]
            for x in arr[:20]:
                if key=="team": link=f"/ultimate/person/{x['id']}"; text=f"{x['name']} · {', '.join(x['roles'])}"
                elif key=="cases": link=f"/ultimate/cases/{x['id']}"; text=f"{x['case_no']} · {x['title']} · {x['status']}"
                elif key=="roblox": link=f"/ultimate/roblox"; text=f"{x['display_name']} (@{x['username']}) · {x['status']}"
                elif key=="ideas": link=f"/ultimate/ideas"; text=f"{x['title']} · {x['status']}"
                elif key=="announcements": link=f"/ultimate/announcements"; text=f"{x['title']} · {x['priority']}"
                else: link=f"/ultimate/{key}"; text=str(x.get("title") or x.get("id") or x)
                rows.append(f"<div class=row><a href='{link}'><b>{esc(text)}</b></a></div>")
            sections.append(u.card(f"<h3>{title}</h3>{''.join(rows)}"))
        body="".join(sections) or u.card("<div class=tiny>Keine Treffer.</div>")
        return next_page(ctx,"search","Globale Suche","Ein Einstiegspunkt für Team, Fälle, Aufgaben und Roblox.",body)

    @app.get("/ultimate/announcements", response_class=HTMLResponse)
    async def announcements(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        with cx() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM next_announcements ORDER BY pinned DESC,created_at DESC LIMIT 100").fetchall()]
        cards=[]
        for a in rows:
            read=bool(next((x for x in [1] if True),False))
            with cx() as c: read=bool(c.execute("SELECT 1 FROM next_announcement_reads WHERE announcement_id=? AND user_id=?",(a["id"],actor_id(ctx))).fetchone())
            read_button = "" if read else f"<form method='post' action='/ultimate/announcements/{a['id']}/read'><button class='btn primary'>Als gelesen markieren</button></form>"
            cards.append(u.card(
                f"<div class=row><div><b>{'📌 ' if a['pinned'] else ''}{esc(a['title'])}</b>"
                f"<div class=tiny>{esc(a['priority'])} · {fmt_dt(a['created_at'])}</div></div>"
                f"<span class=pill>{'gelesen' if read else 'ungelesen'}</span></div>"
                f"<p>{esc(a['body'])}</p>{read_button}"
            ))
        create = ""
        if has_perm(ctx,"can_manage_announcements"):
            create=u.card("""<h3 style='margin-top:0'>📢 News erstellen</h3><form method=post action='/ultimate/announcements/create' class=form>
            <input class=input name=title placeholder='Titel' required><select class=select name=priority><option>normal</option><option>important</option><option>critical</option></select>
            <textarea class='ta span2' name=body placeholder='Nachricht' required></textarea><input class=input name=expires_at placeholder='optional: 2026-10-31T23:59:00+00:00'><label><input type=checkbox name=pinned> Anheften</label><button class='btn primary'>Veröffentlichen</button></form>""")
        return next_page(ctx,"announcements","Team-News","Wichtige Informationen mit Lesebestätigung.",create+"".join(cards) or u.card("<div class=tiny>Keine Team-News.</div>"))

    @app.post("/ultimate/announcements/create")
    async def announcements_create(request:Request,title:str=Form(...),body:str=Form(...),priority:str=Form("normal"),expires_at:str=Form(""),pinned:str=Form(""),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session); require_perm(ctx,"can_manage_announcements")
        if priority not in {"normal","important","critical"}: priority="normal"
        with cx() as c:
            c.execute("INSERT INTO next_announcements VALUES(?,?,?,?,?,?,?,?,?,?)",(u.uid("news"),title[:180],body[:8000],priority,"team",1 if pinned else 0,expires_at[:80] or None,actor_id(ctx),actor_name(ctx),iso()))
        save_audit(ctx,"Team-News veröffentlicht","",title)
        for m in members(ctx.guild):
            db.notify(m.id,"📢 "+title,body[:300],"critical" if priority=="critical" else "info","/ultimate/announcements")
        return RedirectResponse("/ultimate/announcements",status_code=303)

    @app.post("/ultimate/announcements/{announcement_id}/read")
    async def announcement_read(request:Request,announcement_id:str,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        with cx() as c:
            c.execute("INSERT OR REPLACE INTO next_announcement_reads VALUES(?,?,?)",(announcement_id,actor_id(ctx),iso()))
        touch_member(actor_id(ctx),"announcement_read")
        return RedirectResponse("/ultimate/announcements",status_code=303)

    @app.get("/ultimate/ideas", response_class=HTMLResponse)
    async def ideas(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        with cx() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM next_ideas ORDER BY updated_at DESC LIMIT 100").fetchall()]
        blocks=[]
        for x in rows:
            with cx() as c:
                votes=c.execute("SELECT COALESCE(SUM(vote),0) FROM next_idea_votes WHERE idea_id=?",(x["id"],)).fetchone()[0]
            blocks.append(u.card(f"<div class=row><div><b>{esc(x['title'])}</b><div class=tiny>{esc(x['category'])} · {esc(x['status'])}</div></div><span class=metric style='font-size:20px'>{votes}</span></div><p>{esc(x['body'])}</p><form method=post action='/ultimate/ideas/{x['id']}/vote'><button class='btn'>👍 Unterstützen</button><button class='btn' name=vote value='-1'>👎 Dagegen</button></form>"))
        form=u.card("""<h3 style='margin-top:0'>💡 Idee einreichen</h3><form method=post action='/ultimate/ideas/create' class=form>
        <input class=input name=title placeholder='Titel' required><input class=input name=category placeholder='Dashboard / Bot / Team'>
        <textarea class='ta span2' name=body placeholder='Beschreibung' required></textarea><button class='btn primary span2'>Idee senden</button></form>""")
        return next_page(ctx,"ideas","Ideen & Verbesserungen","Teammitglieder können Vorschläge einreichen und priorisieren.",form+"".join(blocks))

    @app.post("/ultimate/ideas/create")
    async def ideas_create(request:Request,title:str=Form(...),body:str=Form(...),category:str=Form("general"),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        with cx() as c:
            c.execute("INSERT INTO next_ideas VALUES(?,?,?,?,?,?,?,?,?,?)",(u.uid("idea"),title[:180],body[:5000],category[:80],"new",actor_id(ctx),actor_name(ctx),"",iso(),iso()))
        event(actor_id(ctx),"idea","Neue Idee eingereicht",title,actor_id(ctx),actor_name(ctx))
        return RedirectResponse("/ultimate/ideas",status_code=303)

    @app.post("/ultimate/ideas/{idea_id}/vote")
    async def idea_vote(request:Request,idea_id:str,vote:int=Form(1),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        vote=max(-1,min(1,int(vote or 1)))
        with cx() as c:
            c.execute("INSERT OR REPLACE INTO next_idea_votes VALUES(?,?,?,?)",(idea_id,actor_id(ctx),vote,iso()))
        return RedirectResponse("/ultimate/ideas",status_code=303)

    @app.get("/ultimate/goals", response_class=HTMLResponse)
    async def goals(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        with cx() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM next_goals WHERE status='active' ORDER BY due_at IS NULL,due_at LIMIT 100").fetchall()]
        cards=[]
        for g in rows:
            pct=progress(g["current"],g["target"])
            cards.append(u.card(f"<div class=row><b>{esc(g['title'])}</b><span class=pill>{pct}%</span></div><div class=progress><div style='width:{pct}%'></div></div><div class=tiny>{esc(g['current'])} / {esc(g['target'])} {esc(g['unit'])} · fällig {fmt_dt(g['due_at']) if g['due_at'] else 'offen'}</div><form method=post action='/ultimate/goals/{g['id']}/update' class=form style='margin-top:8px'><input class=input name=value type=number step=0.1 value='{esc(g['current'])}'><input class=input name=note placeholder='Update-Notiz'><button class='btn'>Fortschritt speichern</button></form>"))
        create=""
        if has_perm(ctx,"can_manage_goals"):
            create=u.card("""<h3 style='margin-top:0'>🎯 Ziel erstellen</h3><form method=post action='/ultimate/goals/create' class=form>
            <input class=input name=title placeholder='Ziel' required><input class=input name=unit value='%' placeholder='Einheit'>
            <input class=input name=target type=number step=0.1 value=100><input class=input name=due_at placeholder='2026-10-31T23:59:00+00:00'>
            <textarea class='ta span2' name=description placeholder='Beschreibung'></textarea><button class='btn primary'>Ziel erstellen</button></form>""")
        return next_page(ctx,"goals","Team-Ziele","Monatsziele, Fortschritt und Verantwortlichkeit.",create+"".join(cards) or u.card("<div class=tiny>Keine aktiven Ziele.</div>"))

    @app.post("/ultimate/goals/create")
    async def goals_create(request:Request,title:str=Form(...),description:str=Form(""),target:float=Form(100),unit:str=Form("%"),due_at:str=Form(""),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session); require_perm(ctx,"can_manage_goals")
        with cx() as c:
            c.execute("INSERT INTO next_goals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(u.uid("goal"),title[:180],description[:3000],"team","",max(0,target),0,unit[:20],due_at[:80] or None,"active",actor_id(ctx),actor_name(ctx),iso()))
        save_audit(ctx,"Team-Ziel erstellt","",title)
        return RedirectResponse("/ultimate/goals",status_code=303)

    @app.post("/ultimate/goals/{goal_id}/update")
    async def goals_update(request:Request,goal_id:str,value:float=Form(...),note:str=Form(""),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session); require_perm(ctx,"can_manage_goals")
        with cx() as c:
            row=c.execute("SELECT * FROM next_goals WHERE id=? AND status='active'",(goal_id,)).fetchone()
            if not row: raise HTTPException(404,"Ziel nicht gefunden.")
            status="done" if float(value)>=float(row["target"]) else "active"
            c.execute("UPDATE next_goals SET current=?,status=? WHERE id=?",(float(value),status,goal_id))
            c.execute("INSERT INTO next_goal_updates VALUES(?,?,?,?,?,?,?)",(u.uid("gu"),goal_id,float(value),note[:1000],actor_id(ctx),actor_name(ctx),iso()))
        return RedirectResponse("/ultimate/goals",status_code=303)

    @app.get("/ultimate/achievements", response_class=HTMLResponse)
    async def achievements(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        rows=[]
        for m in members(ctx.guild):
            award_achievements(m)
            with cx() as c:
                n=c.execute("SELECT COUNT(*) FROM next_member_achievements WHERE user_id=?",(str(m.id),)).fetchone()[0]
            rows.append((n,m))
        rows.sort(reverse=True,key=lambda x:x[0])
        body=u.card("<h3>🏅 Achievement-Leaderboard</h3>"+"".join(f"<div class=row><a href='/ultimate/person/{m.id}'><b>#{i} {esc(m.display_name)}</b></a><span class=pill>{n} Achievements</span></div>" for i,(n,m) in enumerate(rows,1)) or "<div class=tiny>Keine Teammitglieder.</div>")
        return next_page(ctx,"achievements","Achievements","Leistung, Meilensteine und Team-Motivation.",body)

    @app.get("/ultimate/permissions", response_class=HTMLResponse)
    async def permissions(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_manage_permissions")
        people=members(ctx.guild)
        blocks=[]
        with cx() as c:
            for m in people:
                overrides={r["permission"]:bool(r["allowed"]) for r in c.execute("SELECT permission,allowed FROM next_permission_overrides WHERE user_id=?",(str(m.id),)).fetchall()}
                checks="".join(f"<label style='display:block;margin:4px 0'><input type=checkbox name='{m.id}:{perm}' {'checked' if overrides.get(perm) else ''}> {perm}</label>" for perm in PERMISSIONS)
                blocks.append(u.card(f"<b>{esc(m.display_name)}</b><div class=tiny>Direkte User-Overrides; Discord-Admin bleibt übergeordnet.</div><form method=post action='/ultimate/permissions/save' style='margin-top:10px'>{checks}<button class='btn primary' style='margin-top:8px'>Speichern</button></form>"))
        return next_page(ctx,"permissions","Permission Center","Granulare Rechte zusätzlich zur Discord-Rollenlogik.", "".join(blocks))

    @app.post("/ultimate/permissions/save")
    async def permissions_save(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_manage_permissions")
        form=await request.form()
        people={str(m.id) for m in members(ctx.guild)}
        with cx() as c:
            for uid in people:
                for perm in PERMISSIONS:
                    key=f"{uid}:{perm}"
                    if key in form:
                        c.execute("INSERT OR REPLACE INTO next_permission_overrides VALUES(?,?,?,?,?)",(uid,perm,1,actor_id(ctx),iso()))
                    else:
                        c.execute("DELETE FROM next_permission_overrides WHERE user_id=? AND permission=?",(uid,perm))
        save_audit(ctx,"Permission-Overrides aktualisiert","",actor_name(ctx))
        return RedirectResponse("/ultimate/permissions",status_code=303)

    @app.get("/ultimate/onboarding", response_class=HTMLResponse)
    async def onboarding(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=False,perm="can_view_dashboard")
        with cx() as c:
            pending=[dict(r) for r in c.execute(
                "SELECT p.user_id,p.display_name,p.joined_team_at,p.archived_at,a.last_seen_at FROM ultimate_profiles p LEFT JOIN next_activity a ON a.user_id=p.user_id ORDER BY p.archived_at IS NOT NULL,p.joined_team_at DESC"
            ).fetchall()]
        rows=[]
        for x in pending:
            m=ctx.guild.get_member(int(x["user_id"])) if str(x["user_id"]).isdigit() else None
            status="aktiv" if m else "ausgeschieden"
            rows.append(f"<div class=row><div><b>{esc(x['display_name'])}</b><div class=tiny>{status} · Einstieg {fmt_dt(x['joined_team_at'])}</div></div><span class=pill>{esc((x['last_seen_at'] or 'noch nie'))}</span></div>")
        body=u.card("<h3>🧑‍💼 Onboarding / Offboarding</h3>"+"".join(rows) or "<div class=tiny>Keine Teamprofile.</div>")
        return next_page(ctx,"onboarding","Onboarding & Offboarding","Teambeitritt und Austritt werden automatisch protokolliert.",body)

    @app.get("/ultimate/orgchart", response_class=HTMLResponse)
    async def orgchart(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        with u.cx() as c:
            deps=[dict(r) for r in c.execute("SELECT * FROM ultimate_departments WHERE active=1 ORDER BY name").fetchall()]
            memrows=[dict(r) for r in c.execute("SELECT * FROM ultimate_department_members").fetchall()]
        leaders = [m for m in members(ctx.guild) if any(x.name.lower() in {"owner","admin","leitung"} for x in m.roles)]
        leader_html = "".join(
            f"<div class=row><b>{esc(m.display_name)}</b><span class=pill>{esc(', '.join(role_names(m))[-80:])}</span></div>"
            for m in leaders
        ) or "<div class=tiny>Führung über Discord-Rollen ermittelt.</div>"
        team_html = u.card("<h3>👑 Führung</h3>" + leader_html)
        blocks=[]
        bydep={}
        for x in memrows: bydep.setdefault(x["department_id"],[]).append(x)
        mm={str(m.id):m for m in members(ctx.guild)}
        for d in deps:
            people=bydep.get(d["id"],[])
            inner="".join(f"<div class=row><span>{esc(mm.get(str(x['user_id'])).display_name if mm.get(str(x['user_id'])) else x['user_id'])}</span><span class=pill>{esc(x['role'])}</span></div>" for x in people)
            blocks.append(u.card(f"<h3>{esc(d['name'])}</h3>{inner or '<div class=tiny>Keine Zuordnung.</div>'}"))
        return next_page(ctx,"departments","Organigramm","Führung, Abteilungen und Verantwortlichkeiten.",team_html+u.card("".join(blocks) or "<div class=tiny>Keine Abteilungen.</div>"))

    @app.get("/ultimate/reports", response_class=HTMLResponse)
    async def reports(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,perm="can_view_analytics"); require_perm(ctx,"can_view_analytics")
        stats=all_stats(ctx.guild); avg=round(sum(s["score"] for _,s in stats)/len(stats)) if stats else 0
        total_hours=round(sum(s["total_seconds"] for _,s in stats)/3600,1)
        with cx() as c:
            promotions=c.execute("SELECT COUNT(*) FROM next_promotions WHERE status='approved' AND substr(updated_at,1,7)=substr(?,1,7)",(iso(),)).fetchone()[0]
            news=c.execute("SELECT COUNT(*) FROM next_announcements WHERE substr(created_at,1,7)=substr(?,1,7)",(iso(),)).fetchone()[0]
            ideas=c.execute("SELECT COUNT(*) FROM next_ideas WHERE substr(created_at,1,7)=substr(?,1,7)",(iso(),)).fetchone()[0]
        leader=sorted(stats,key=lambda x:x[1]["score"],reverse=True)[:5]
        report=u.card(f"<h2 style='margin-top:0'>📊 Pulse Monatsbericht</h2><div class='grid g4'><div><div class=metric>{avg}</div><div class=tiny>Team-Score</div></div><div><div class=metric>{total_hours}</div><div class=tiny>Dienststunden</div></div><div><div class=metric>{promotions}</div><div class=tiny>Beförderungen</div></div><div><div class=metric>{news}</div><div class=tiny>Team-News</div></div></div><p class=tiny>Ideen eingegangen: {ideas}</p>")
        rows="".join(f"<div class=row><span>#{i} {esc(m.display_name)}</span><span class=pill>{s['score']}/100</span></div>" for i,(m,s) in enumerate(leader,1))
        body=report+u.card("<h3>🏆 Top-Teamler</h3>"+rows)+u.card("<a class='btn primary' href='/ultimate/reports.csv'>CSV exportieren</a> <a class='btn' href='/ultimate/backup'>Backup Center</a>")
        return next_page(ctx,"reports","Berichte & Exporte","Monatliche Management-Auswertung und Datenexport.",body)

    @app.get("/ultimate/reports.csv")
    async def reports_csv(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,perm="can_view_analytics"); require_perm(ctx,"can_view_analytics")
        output=io.StringIO(); writer=csv.writer(output); writer.writerow(["ID","Name","Status","Score","Wochenstunden","Gesamtstunden","Tickets","Warnungen","Training"])
        for m,s in all_stats(ctx.guild):
            writer.writerow([m.id,m.display_name,str(m.status),s["score"],round(s["weekly_seconds"]/3600,2),round(s["total_seconds"]/3600,2),s["closed_tickets"],s["warnings"],s["training_passed"]])
        return HTMLResponse(output.getvalue(),headers={"Content-Type":"text/csv; charset=utf-8","Content-Disposition":"attachment; filename=pulse_team_report.csv"})

    @app.get("/ultimate/backup", response_class=HTMLResponse)
    async def backup(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_manage_backups")
        files=sorted(BACKUP_DIR.glob("pulse_backup_*.zip"),key=lambda x:x.stat().st_mtime,reverse=True)[:50]
        rows="".join(f"<div class=row><div><b>{esc(f.name)}</b><div class=tiny>{round(f.stat().st_size/1024,1)} KB · {datetime.fromtimestamp(f.stat().st_mtime).strftime('%d.%m.%Y %H:%M')}</div></div><span><a class='btn' href='/ultimate/backup/{esc(f.name)}'>Download</a> <form method='post' action='/ultimate/backup/{esc(f.name)}/restore' style='display:inline' onsubmit="return confirm('Dieses Backup wirklich wiederherstellen?');"><input type='hidden' name='confirm' value='RESTORE'><button class='btn danger'>Wiederherstellen</button></form></span></div>" for f in files) or "<div class=tiny>Keine Backups.</div>"
        body=u.card("""<h3>💾 Backup erstellen</h3><form method=post action='/ultimate/backup/create'><button class='btn primary'>Jetzt sichern</button></form><p class=tiny>Gesichert werden pulse.db und die vorhandenen JSON-Laufzeitdateien. Große Anhänge werden bewusst nicht in jeden Backup-Zip gepackt.</p>""")+u.card("<h3>Vorhandene Backups</h3>"+rows)
        return next_page(ctx,"system","Backup Center","Sicherung und Wiederherstellung des Pulse-Laufzeitbestands.",body)

    @app.post("/ultimate/backup/create")
    async def backup_create(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_manage_backups")
        p=create_backup(); save_audit(ctx,"Backup erstellt","",p.name)
        return RedirectResponse("/ultimate/backup",status_code=303)

    @app.get("/ultimate/backup/{name}")
    async def backup_download(request:Request,name:str,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_manage_backups")
        from fastapi.responses import FileResponse
        p=safe_backup_file(name)
        return FileResponse(p,filename=p.name,media_type="application/zip")

    @app.post("/ultimate/backup/{name}/restore")
    async def backup_restore(request:Request,name:str,confirm:str=Form(...),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_manage_backups")
        if confirm!="RESTORE": raise HTTPException(400,"Bestätigung muss RESTORE lauten.")
        p=safe_backup_file(name)
        restore_backup(p); save_audit(ctx,"Backup wiederhergestellt","",p.name)
        return RedirectResponse("/ultimate/system",status_code=303)

    @app.get("/ultimate/workflows", response_class=HTMLResponse)
    async def workflows(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_manage_workflows")
        with cx() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM next_workflows ORDER BY created_at DESC LIMIT 100").fetchall()]
        cards="".join(u.card(f"<div class=row><b>{esc(w['name'])}</b><span class=pill>{esc(w['trigger_type'])} · {'aktiv' if w['enabled'] else 'aus'}</span></div><div class=tiny>{esc(w['condition_json'])}</div><div class=tiny>{esc(w['actions_json'])}</div>") for w in rows) or u.card("<div class=tiny>Keine Workflows.</div>")
        form=u.card("""<h3>🧩 Workflow Builder</h3><form method=post action='/ultimate/workflows/create' class=form>
        <input class=input name=name placeholder='Workflow Name' required><select class=select name=trigger_type><option>daily</option><option>interval</option></select>
        <select class=select name=condition_type><option>always</option><option>team_online_gte</option><option>inactivity_gte</option></select><input class=input name=condition_value placeholder='Schwellwert, z. B. 3 oder 14'>
        <textarea class='ta span2' name=actions placeholder='Eine JSON-Aktion pro Zeile, z. B. {"type":"notify_managers","title":"Hinweis","body":"..."}'></textarea>
        <input class=input name=cooldown_minutes type=number value=1440><button class='btn primary'>Workflow aktivieren</button></form>""")
        return next_page(ctx,"automations","Workflow Center","Bedingungen, Aktionsketten und wiederkehrende Prozesse.",form+cards)

    @app.post("/ultimate/workflows/create")
    async def workflows_create(request:Request,name:str=Form(...),trigger_type:str=Form("daily"),condition_type:str=Form("always"),condition_value:str=Form("0"),actions:str=Form(...),cooldown_minutes:int=Form(1440),user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True); require_perm(ctx,"can_manage_workflows")
        parsed=[]
        for line in actions.splitlines():
            line=line.strip()
            if not line: continue
            try: parsed.append(json.loads(line))
            except json.JSONDecodeError: raise HTTPException(400,f"Ungültiges JSON: {line[:100]}")
        condition={"type":condition_type,"value":float(condition_value or 0)} if condition_type!="always" else {"type":"always"}
        with cx() as c:
            c.execute("INSERT INTO next_workflows VALUES(?,?,?,?,?,?,?,?,?,?,?)",(u.uid("wf"),name[:180],trigger_type, json.dumps(condition), json.dumps(parsed),1,max(5,int(cooldown_minutes)),None,actor_id(ctx),actor_name(ctx),iso()))
        save_audit(ctx,"Workflow erstellt","",name)
        return RedirectResponse("/ultimate/workflows",status_code=303)

    @app.get("/ultimate/shifts", response_class=HTMLResponse)
    async def shifts(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        import webserver
        active=webserver.load_shifts().get("active_shifts",{})
        rows=[]
        for m in members(ctx.guild):
            sh=active.get(str(m.id))
            rows.append(f"<div class=row><span><b>{esc(m.display_name)}</b><div class=tiny>{'Im Dienst' if sh else 'Außer Dienst'}</div></span><span class=pill>{webserver.shift_elapsed(sh)//60 if sh else 0} min</span></div>")
        body=u.card("<h3>🕒 Dienstübersicht</h3>"+"".join(rows))+u.card("<p class=tiny>Für Dienst starten/stoppen bleibt das bestehende Pulse-Dienstsystem aktiv. Diese Ansicht bündelt die aktuellen Schichten.</p><a class='btn primary' href='/dashboard'>Zum Dienstmodul</a>")
        return next_page(ctx,"team","Dienstübersicht","Aktuelle Schichten und Teamstatus.",body)

    @app.get("/ultimate/health-detail", response_class=HTMLResponse)
    async def health_detail(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True)
        import webserver
        checks=[]
        try:
            with u.cx() as c: c.execute("SELECT 1")
            checks.append(("🟢","Ultimate SQLite","OK"))
        except Exception as e: checks.append(("🔴","Ultimate SQLite",str(e)))
        bot=getattr(request.app.state,"bot",None)
        guild=bot.get_guild(webserver.GUILD_ID) if bot else None
        checks.append(("🟢" if bot and bot.is_ready() else "🔴","Discord Bot","bereit" if bot and bot.is_ready() else "nicht bereit"))
        checks.append(("🟢" if guild else "🔴","Discord Guild","bereit" if guild else "nicht gefunden"))
        if guild:
            me=guild.me
            checks.append(("🟢" if me and me.guild_permissions.manage_roles else "🟡","Manage Roles","vorhanden" if me and me.guild_permissions.manage_roles else "fehlt"))
            if me:
                top=max((r.position for r in me.roles),default=0)
                checks.append(("🟢" if top else "🟡","Bot-Rang","Position "+str(top)))
        checks.append(("🟢" if u.ATTACHMENTS_DIR.exists() else "🔴","Attachments","verfügbar" if u.ATTACHMENTS_DIR.exists() else "fehlt"))
        body=u.card("<h3>🩺 Detaillierter Systemcheck</h3>"+"".join(f"<div class=row><span>{icon} {esc(name)}</span><span class=pill>{esc(val)}</span></div>" for icon,name,val in checks))
        return next_page(ctx,"system","Health Center","Live-Prüfung von Bot, Guild, Datenbank und Dateisystem.",body)


    @app.get("/ultimate/scoreboard", response_class=HTMLResponse)
    async def scoreboard(request: Request, user_session: str=Cookie(None)):
        ctx=ctx_auth(request,user_session,perm="can_view_analytics")
        stats=all_stats(ctx.guild)
        stats.sort(key=lambda x:x[1]["score"], reverse=True)
        rows=[]
        for i,(m,s) in enumerate(stats,1):
            cls,icon=team_score_class(s["score"])
            rows.append(f"<div class='row'><span><b>#{i} {esc(m.display_name)}</b><div class='tiny'>{esc(cls)} · {s['activity_pct']}% Aktivität · {s['closed_tickets']} Tickets</div></span><span class='pill'>{icon} {s['score']}/100</span></div>")
        body=u.card("<h3>🏆 Team-Score Leaderboard</h3>"+"".join(rows) or "<div class=tiny>Keine Teamdaten.</div>")
        return next_page(ctx,"analytics","Team-Score","Bewertung, Rangliste und Leistungsentwicklung.",body)

    @app.get("/ultimate/support", response_class=HTMLResponse)
    async def support(request: Request, user_session: str=Cookie(None)):
        ctx=ctx_auth(request,user_session,perm="can_view_analytics")
        import webserver
        tickets=db.list_tickets(limit=5000)
        now_dt=now()
        overdue=[]
        waiting=[]
        durations={}
        for t in tickets:
            if t.get("status")=="closed":
                continue
            try:
                opened=datetime.fromisoformat(str(t.get("opened_at","")).replace("Z","+00:00"))
                age=(now_dt-opened).total_seconds()/60
                sla={"urgent":30,"high":240,"normal":1440}.get(str(t.get("priority") or "normal"),1440)
                (overdue if age>=sla else waiting).append((t,age,sla))
            except Exception:
                continue
        perf={}
        for t in tickets:
            if t.get("status")=="closed" and t.get("claimed_by_id"):
                uid_s=str(t.get("claimed_by_id"))
                perf[uid_s]=perf.get(uid_s,0)+1
        names={str(m.id):m.display_name for m in members(ctx.guild)}
        leaders=sorted(perf.items(),key=lambda x:x[1],reverse=True)[:10]
        rows="".join(f"<div class=row><span>{esc(names.get(uid_s,uid_s))}</span><span class=pill>{n} abgeschlossen</span></div>" for uid_s,n in leaders)
        body=u.card(f"<div class='grid g3'><div><div class=metric>{len(tickets)}</div><div class=tiny>Tickets gesamt</div></div><div><div class=metric>{len(waiting)}</div><div class=tiny>innerhalb SLA</div></div><div><div class=metric>{len(overdue)}</div><div class=tiny>über SLA</div></div></div>")+u.card("<h3>🚨 SLA-Ausreißer</h3>"+"".join(f"<div class=row><span><b>{esc(str(t.get('title') or t.get('id')))}</b><div class=tiny>{round(age)} min offen · SLA {sla:g} min</div></span><span class=pill>überfällig</span></div>" for t,age,sla in sorted(overdue,key=lambda x:x[1],reverse=True)[:20]) or "<div class=tiny>Keine SLA-Verstöße.</div>")+u.card("<h3>🎫 Support-Leaderboard</h3>"+(rows or "<div class=tiny>Keine abgeschlossenen Tickets.</div>"))
        return next_page(ctx,"analytics","Support & SLA","Ticket-Wartezeiten, SLA und Support-Leistung.",body)

    @app.get("/ultimate/tasks", response_class=HTMLResponse)
    async def taskboard(request: Request,user_session: str=Cookie(None)):
        ctx=ctx_auth(request,user_session)
        tasks=db.list_tasks(limit=1000,include_archived=False)
        today=now()
        overdue=[]
        open_rows=[]
        for t in tasks:
            status=str(t.get("status") or "")
            due=str(t.get("due_date") or t.get("deadline") or "")
            if status in {"done","archived"}:
                continue
            is_overdue=False
            if due:
                try:
                    dd=datetime.fromisoformat(due.replace("Z","+00:00"))
                    is_overdue=dd < today
                except Exception:
                    is_overdue=False
            (overdue if is_overdue else open_rows).append(t)
        def task_row(t, danger=False):
            title=str(t.get("title") or t.get("id") or "Aufgabe")
            assignee=str(t.get("assignee_name") or t.get("assigned_to_name") or "Nicht zugewiesen")
            priority=str(t.get("priority") or "normal")
            due=str(t.get("due_date") or t.get("deadline") or "keine")
            return f"<div class='row'><span><b>{esc(title)}</b><div class='tiny'>{esc(assignee)} · {esc(priority)} · {esc(due)}</div></span><span class='pill'>{'🔴 überfällig' if danger else esc(str(t.get('status') or 'offen'))}</span></div>"
        body=u.card(f"<div class='grid g3'><div><div class=metric>{len(tasks)}</div><div class=tiny>aktive Aufgaben</div></div><div><div class=metric>{len(overdue)}</div><div class=tiny>überfällig</div></div><div><div class=metric>{sum(str(t.get('status'))=='done' for t in tasks)}</div><div class=tiny>heute im Bestand erledigt</div></div></div>")+u.card("<h3>🔴 Überfällige Aufgaben</h3>"+("".join(task_row(t,True) for t in overdue[:30]) or "<div class=tiny>Keine überfälligen Aufgaben.</div>"))+u.card("<h3>📋 Offene Aufgaben</h3>"+("".join(task_row(t) for t in open_rows[:50]) or "<div class=tiny>Keine offenen Aufgaben.</div>"))
        return next_page(ctx,"team","Aufgaben 2.0","Zentrale Übersicht für Deadlines, Prioritäten und Verantwortlichkeiten.",body)

    @app.get("/ultimate/team-trends", response_class=HTMLResponse)
    async def team_trends(request: Request,user_session: str=Cookie(None)):
        ctx=ctx_auth(request,user_session,perm="can_view_analytics")
        stats=all_stats(ctx.guild)
        with cx() as c:
            days=[dict(r) for r in c.execute(
                """SELECT substr(recorded_at,1,10) AS day,ROUND(AVG(score),1) AS avg_score,
                          ROUND(AVG(activity_pct),1) AS activity
                   FROM next_score_snapshots GROUP BY day ORDER BY day DESC LIMIT 30"""
            ).fetchall()]
        days=list(reversed(days))
        bars="".join(f"<div style='display:inline-block;width:12px;height:{max(8,int(d['avg_score'] or 0))}px;background:var(--pulse-accent);margin:0 2px;border-radius:4px' title='{esc(d['day'])}: {d['avg_score']}'></div>" for d in days)
        lowest=sorted(stats,key=lambda x:x[1]["score"])[:5]
        body=u.card(f"<h3>📈 Team-Score-Trend</h3><div style='height:140px;display:flex;align-items:end'>{bars or '<span class=tiny>Noch keine täglichen Snapshots.</span>'}</div><div class=tiny>Gespeicherte Tagesschnitte</div>")+u.card("<h3>⚠️ Aktueller Verbesserungsbedarf</h3>"+"".join(f"<div class=row><span>{esc(m.display_name)}</span><span class=pill>{s['score']}/100</span></div>" for m,s in lowest) or "<div class=tiny>Keine Daten.</div>")
        return next_page(ctx,"analytics","Team-Trends","Entwicklung des Teams und mögliche Problemfelder.",body)

    @app.get("/ultimate/export.json")
    async def export_json(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,perm="can_view_analytics")
        payload={"generated_at":iso(),"team":[],"goals":[],"ideas":[],"announcements":[]}
        for m in members(ctx.guild):
            s=u.member_stats(m)
            payload["team"].append({"id":m.id,"name":m.display_name,"status":str(m.status),"score":s["score"],"weekly_hours":round(s["weekly_seconds"]/3600,2),"total_hours":round(s["total_seconds"]/3600,2),"tickets_closed":s["closed_tickets"],"warnings":s["warnings"],"training_passed":s["training_passed"]})
        with cx() as c:
            payload["goals"]=[dict(r) for r in c.execute("SELECT * FROM next_goals ORDER BY created_at DESC").fetchall()]
            payload["ideas"]=[dict(r) for r in c.execute("SELECT * FROM next_ideas ORDER BY created_at DESC").fetchall()]
            payload["announcements"]=[dict(r) for r in c.execute("SELECT * FROM next_announcements ORDER BY created_at DESC").fetchall()]
        raw=json.dumps(payload,ensure_ascii=False,indent=2)
        return HTMLResponse(raw,headers={"Content-Type":"application/json; charset=utf-8","Content-Disposition":"attachment; filename=pulse_export.json"})

    @app.get("/ultimate/security", response_class=HTMLResponse)
    async def security_center(request:Request,user_session:str=Cookie(None)):
        ctx=ctx_auth(request,user_session,manager=True)
        import webserver
        checks=[]
        bot=getattr(request.app.state,"bot",None)
        guild=ctx.guild
        me=guild.me if guild else None
        checks.append(("🔐","HTTPS","aktiv" if str(request.url).startswith("https://") or webserver.PUBLIC_BASE_URL.startswith("https://") else "Reverse Proxy prüfen"))
        checks.append(("🛡️","Manage Roles","OK" if me and me.guild_permissions.manage_roles else "FEHLT"))
        checks.append(("👥","Members Intent","aktiv" if bot and bot.intents.members else "FEHLT"))
        checks.append(("🟣","Presence Intent","aktiv" if bot and bot.intents.presences else "FEHLT"))
        checks.append(("🔑","API Keys","hash-basiert gespeichert"))
        checks.append(("📜","Audit","Legacy + Pulse Events"))
        body=u.card("<h3>🛡️ Security Center</h3>"+"".join(f"<div class=row><span>{icon} {esc(name)}</span><span class=pill>{esc(val)}</span></div>" for icon,name,val in checks))+u.card("<p class=tiny>Für besonders sensible Aktionen empfiehlt Pulse das 4-Augen-Prinzip. API-Schlüssel werden nicht im Klartext gespeichert.</p>")
        return next_page(ctx,"system","Security Center","Berechtigungen, Sessions, API und kritische Voraussetzungen.",body)

    app.state.pulse_next_registered=True
