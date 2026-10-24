"""Pulse TeamOS Suite: unified management, recruiting, time, planning, audit, automation and security."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from contextlib import closing
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import discord
from pulse_interactions import PulseView  # BOT-01: error IDs, double-click guard, timeout
from pulse_discord_style import send_styled
from pulse_embed_design import color as _pulse_color
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import pulse_db as db
import pulse_sqlite
from pulse_jobs import tracked
from pulse_safety import approved_action_operation, rank_role_ids_after_change

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "pulse_suite.db")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
REPORT_HOUR = 20
PROBATION_DAYS = 14
INACTIVITY_NOTICE_DAYS = 8
INACTIVITY_WARNING_DAYS = 14

CSS = """
:root{--a:#6366f1;--a-soft:rgba(99,102,241,.14);--bg:#060a10;--panel:#0d1623;--panel-2:#0a111b;--line:#1d2a3b;--muted:#91a0b5;--text:#f7f9fc;--good:#10b981;--warn:#f59e0b;--bad:#ef4444}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:radial-gradient(1100px 520px at 10% -10%,rgba(99,102,241,.25),transparent 62%),radial-gradient(900px 480px at 100% 8%,rgba(6,182,212,.10),transparent 60%),linear-gradient(180deg,#050810,#08111c 55%,#060b12);color:var(--text);font:14px Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
body:before{content:"";position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(rgba(255,255,255,.015) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.012) 1px,transparent 1px);background-size:42px 42px;mask-image:linear-gradient(to bottom,black,transparent 78%)}
a{color:inherit;text-decoration:none}.shell{display:flex;min-height:100vh}
.side{position:fixed;inset:0 auto 0 0;width:268px;padding:18px 14px;background:rgba(5,9,15,.84);border-right:1px solid rgba(148,163,184,.12);backdrop-filter:blur(22px);z-index:30;display:flex;flex-direction:column}
.brand{display:flex;align-items:center;gap:11px;padding:8px 10px 18px;font-size:20px;font-weight:950;letter-spacing:-.03em}.brand-mark{width:38px;height:38px;border-radius:13px;display:grid;place-items:center;background:linear-gradient(135deg,var(--a),#06b6d4);box-shadow:0 10px 28px rgba(99,102,241,.28);font-size:19px}.brand-main{display:flex;align-items:center;gap:6px}.brand small{display:block;color:var(--muted);font-size:10px;font-weight:650;letter-spacing:.05em;margin-top:3px}
.nav{overflow:auto;padding-right:3px}.nav::-webkit-scrollbar{width:4px}.nav::-webkit-scrollbar-thumb{background:#26364b;border-radius:99px}.nav a{display:flex;align-items:center;gap:9px;padding:10px 11px;margin:4px 0;border:1px solid transparent;border-radius:12px;color:#bdc8d7;font-weight:720;font-size:12px;transition:.18s}.nav a:hover{background:rgba(255,255,255,.04);border-color:rgba(148,163,184,.08);color:#fff;transform:translateX(2px)}.nav a.active{background:linear-gradient(90deg,var(--a-soft),rgba(6,182,212,.05));border-color:rgba(99,102,241,.24);color:#fff;box-shadow:0 10px 24px rgba(0,0,0,.14)}
.side-bottom{margin-top:auto;border-top:1px solid rgba(148,163,184,.10);padding:14px 6px 0}.profile{display:flex;align-items:center;gap:9px;padding:9px;border-radius:12px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.05)}.profile-dot{width:8px;height:8px;border-radius:50%;background:#10b981;box-shadow:0 0 0 4px rgba(16,185,129,.08);margin-left:auto}.side-actions{display:flex;gap:7px;margin-bottom:9px}.side-actions .btn{flex:1;text-align:center;padding:8px 7px}
.main{margin-left:268px;width:calc(100% - 268px);padding:30px;max-width:1700px}
.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:22px;position:sticky;top:0;z-index:20;padding:10px 0 16px;background:linear-gradient(180deg,#050810f5 66%,transparent)}.top h1{margin:0 0 4px;font-size:30px;letter-spacing:-.035em;line-height:1.05}.top .muted{font-size:12px}.top-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.muted,.tiny{color:var(--muted)}.tiny{font-size:10px}.kicker{font-size:10px;text-transform:uppercase;letter-spacing:.18em;font-weight:900;color:#8da0b8;margin-bottom:5px}
.grid{display:grid;gap:14px}.g4{grid-template-columns:repeat(4,1fr)}.g3{grid-template-columns:repeat(3,1fr)}.g2{grid-template-columns:repeat(2,1fr)}
.card{position:relative;background:linear-gradient(180deg,rgba(14,24,38,.92),rgba(9,17,28,.92));border:1px solid rgba(148,163,184,.11);border-radius:18px;padding:17px;box-shadow:0 20px 50px rgba(0,0,0,.24);overflow:hidden}.card:after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(135deg,rgba(255,255,255,.025),transparent 35%,transparent 65%,rgba(255,255,255,.012))}.card:hover{border-color:rgba(148,163,184,.18)}
.metric{font-size:30px;font-weight:950;letter-spacing:-.03em;margin:7px 0}.row{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:10px 12px;border:1px solid rgba(148,163,184,.09);border-radius:12px;background:rgba(4,10,17,.52);margin:7px 0}
.badge{display:inline-flex;align-items:center;gap:5px;border-radius:999px;border:1px solid var(--line);padding:4px 8px;font-size:10px;font-weight:850}.ok{color:#a7f3d0;background:#092319;border-color:#145c40}.warn{color:#fde68a;background:#2a1f0e;border-color:#6b5118}.bad{color:#fecaca;background:#2b1116;border-color:#6f1f2a}.blue{color:#c7d2fe;background:#17183b;border-color:#303380}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;border:1px solid rgba(148,163,184,.14);background:rgba(17,28,43,.84);color:#dce5ef;border-radius:11px;padding:9px 12px;font-weight:800;font-size:11px;cursor:pointer;transition:.17s;box-shadow:0 7px 20px rgba(0,0,0,.12)}.btn:hover{border-color:rgba(99,102,241,.42);transform:translateY(-1px);background:#152338}.primary{background:linear-gradient(135deg,var(--a),#4f46e5);border-color:transparent;color:white;box-shadow:0 10px 26px rgba(99,102,241,.22)}.danger{background:#2b1116;color:#fecaca;border-color:#682331}
.input,.select,.ta{width:100%;padding:10px 11px;border-radius:11px;border:1px solid #29384d;background:rgba(5,12,20,.76);color:#fff;outline:none;transition:.16s}.input:focus,.select:focus,.ta:focus{border-color:rgba(99,102,241,.6);box-shadow:0 0 0 4px rgba(99,102,241,.10)}.ta{min-height:110px;resize:vertical}
.form{display:grid;grid-template-columns:repeat(2,1fr);gap:11px}.span2{grid-column:span 2}.table{width:100%;border-collapse:separate;border-spacing:0}.table th,.table td{padding:11px 10px;border-bottom:1px solid rgba(148,163,184,.08);text-align:left;font-size:11px}.table th{color:#9eacc0;text-transform:uppercase;letter-spacing:.08em;font-size:9px;background:#0a111b}.scroll{overflow:auto}
.kan{display:grid;grid-template-columns:repeat(5,250px);gap:11px;overflow:auto;padding-bottom:2px}.col{background:rgba(6,13,21,.72);border:1px solid var(--line);border-radius:15px;padding:11px;min-height:250px}.notice{border:1px solid var(--line);border-radius:13px;padding:12px;background:rgba(9,19,31,.82);margin:8px 0}.timeline{padding-left:19px;border-left:1px solid #263448}.event{margin-bottom:15px}
.search{position:fixed;inset:0;background:rgba(1,4,8,.72);backdrop-filter:blur(12px);display:none;align-items:flex-start;justify-content:center;padding:10vh 16px;z-index:99}.search>div{width:min(860px,100%);background:linear-gradient(180deg,#0d1827,#09111c);border:1px solid #2f425a;border-radius:18px;padding:14px;box-shadow:0 30px 90px rgba(0,0,0,.5)}.result{display:block;padding:11px 12px;border-radius:11px}.result:hover{background:rgba(255,255,255,.05)}
.hero{border:1px solid rgba(99,102,241,.20);background:radial-gradient(700px 280px at 0% 0%,rgba(99,102,241,.18),transparent 62%),linear-gradient(135deg,rgba(13,23,38,.96),rgba(7,14,24,.96));border-radius:22px;padding:22px;margin-bottom:14px;box-shadow:0 24px 70px rgba(0,0,0,.24)}
@keyframes rise{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media(max-width:1200px){.g4{grid-template-columns:repeat(2,1fr)}.side{width:232px}.main{margin-left:232px;width:calc(100% - 232px);padding:22px}}
@media(max-width:850px){.shell{display:block}.side{position:relative;width:100%;height:auto;border-right:0;border-bottom:1px solid rgba(148,163,184,.10);padding:12px}.brand{padding-bottom:10px}.nav{display:flex;gap:6px;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:6px}.nav a{flex:0 0 auto;margin:0;white-space:nowrap}.nav a:hover{transform:none}.side-bottom{margin-top:9px}.side-bottom .profile{display:none}.main{margin:0;width:100%;padding:15px}.top{position:static;flex-direction:column;padding:4px 0 14px}.top h1{font-size:24px}.top-actions{width:100%}.top-actions .btn{flex:1}.g4{grid-template-columns:repeat(2,minmax(0,1fr))}.g3,.g2,.form{grid-template-columns:1fr}.span2{grid-column:auto}.kan{grid-template-columns:repeat(5,220px)}.row{flex-wrap:wrap}.input,.select,.ta{font-size:16px}}
"""

def cx():
    c = pulse_sqlite.connect(DB_PATH, timeout=3)
    c.execute("PRAGMA busy_timeout=3000")
    return c

def init_db():
    with cx() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS suite_members(user_id TEXT PRIMARY KEY,joined_at TEXT,archived_at TEXT,last_seen TEXT,internal_rating INTEGER DEFAULT 80,notes TEXT DEFAULT '',probation_end TEXT);
        CREATE TABLE IF NOT EXISTS suite_promotions(id TEXT PRIMARY KEY,target_id TEXT,target_name TEXT,current_role TEXT,current_role_id TEXT,target_role TEXT,target_role_id TEXT,reason TEXT,performance TEXT,activity TEXT,warnings TEXT,score INTEGER DEFAULT 0,status TEXT DEFAULT 'pending',required_approvals INTEGER DEFAULT 1,approvals_json TEXT DEFAULT '[]',created_by_id TEXT,created_by_name TEXT,decision_note TEXT DEFAULT '',created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS suite_schedule(id TEXT PRIMARY KEY,guild_id TEXT,date TEXT,shift_name TEXT,start_time TEXT,end_time TEXT,assignee_id TEXT,assignee_name TEXT,created_by_id TEXT,created_by_name TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS suite_events(id TEXT PRIMARY KEY,guild_id TEXT,title TEXT,kind TEXT,start_at TEXT,end_at TEXT,description TEXT,member_id TEXT,created_by_id TEXT,created_by_name TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS suite_interviews(id TEXT PRIMARY KEY,application_id TEXT,applicant_id TEXT,applicant_name TEXT,scores_json TEXT,overall INTEGER,recommendation TEXT,notes TEXT,interviewer_id TEXT,interviewer_name TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS suite_training_assignments(id TEXT PRIMARY KEY,training_id TEXT,user_id TEXT,user_name TEXT,status TEXT,assigned_by_id TEXT,assigned_by_name TEXT,assigned_at TEXT);
        CREATE TABLE IF NOT EXISTS suite_task_meta(task_id TEXT PRIMARY KEY,board_status TEXT DEFAULT 'backlog',assignees_json TEXT DEFAULT '[]',checklist_json TEXT DEFAULT '[]',comments_json TEXT DEFAULT '[]',attachments_json TEXT DEFAULT '[]',updated_at TEXT);
        CREATE TABLE IF NOT EXISTS suite_cases(id TEXT PRIMARY KEY,reporter_id TEXT,reporter_name TEXT,category TEXT,description TEXT,status TEXT DEFAULT 'open',priority TEXT DEFAULT 'normal',assignee_id TEXT,assignee_name TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS suite_actions(id TEXT PRIMARY KEY,kind TEXT,payload_json TEXT,status TEXT,created_by_id TEXT,created_by_name TEXT,approved_by_id TEXT,approved_by_name TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS suite_audit(id TEXT PRIMARY KEY,guild_id TEXT,occurred_at TEXT,actor_id TEXT,actor_name TEXT,target_id TEXT,target_name TEXT,action TEXT,details TEXT,request_id TEXT,prev_hash TEXT,hash TEXT);
        CREATE TABLE IF NOT EXISTS suite_achievements(key TEXT,user_id TEXT,unlocked_at TEXT,PRIMARY KEY(key,user_id));
        CREATE TABLE IF NOT EXISTS suite_settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE INDEX IF NOT EXISTS idx_suite_audit_time ON suite_audit(occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_suite_schedule_date ON suite_schedule(date);
        """)

def setting(key,default=None):
    with cx() as c:r=c.execute("SELECT value FROM suite_settings WHERE key=?",(key,)).fetchone()
    if not r:return default
    try:return json.loads(r[0])
    except Exception:return r[0]

def set_setting(key,value):
    with cx() as c:c.execute("INSERT OR REPLACE INTO suite_settings(key,value) VALUES(?,?)",(key,json.dumps(value,ensure_ascii=False)))

def seed():
    vals={"emergency_lock":False,"command_roles":{},"weekly_goal_hours":3.0,"report_channel_id":int(os.getenv("PULSE_REPORT_CHANNEL_ID","0") or 0),"last_daily":"","last_weekly":""}
    for k,v in vals.items():
        if setting(k,None) is None:set_setting(k,v)

def now():return datetime.now(timezone.utc)
def iso():return now().isoformat(timespec="seconds")
def local(v):
    if not v:return None
    try:
        x=datetime.fromisoformat(str(v).replace("Z","+00:00"));return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except Exception:return None
def esc(v):
    import html
    return html.escape("" if v is None else str(v),quote=True)
def secfmt(s):
    s=max(0,int(s or 0));return f"{s//3600}h {(s%3600)//60}m"

def members(guild,web):
    ids={int(x) for x in web.load_config().get("team_role_ids",[])}
    return [m for m in guild.members if not m.bot and any(r.id in ids for r in m.roles)]

def managers(guild,web):
    out=[]
    for m in members(guild,web):
        p,_=web.compute_perms(guild,m.id,web.load_config())
        if p.get("can_promote") or p.get("is_admin"):out.append(m)
    return out

def member_row(uid):
    with cx() as c:r=c.execute("SELECT * FROM suite_members WHERE user_id=?",(str(uid),)).fetchone()
    return dict(r) if r else None

def upsert_member(m):
    joined = iso()
    with cx() as c:
        if c.execute("SELECT 1 FROM suite_members WHERE user_id=?", (str(m.id),)).fetchone():
            c.execute("UPDATE suite_members SET last_seen=?,archived_at=NULL WHERE user_id=?", (joined, str(m.id)))
        else:
            c.execute(
                "INSERT INTO suite_members(user_id,joined_at,last_seen,internal_rating,notes,probation_end) VALUES(?,?,?,?,?,?)",
                (str(m.id), joined, joined, 80, "", (now()+timedelta(days=PROBATION_DAYS)).isoformat(timespec="seconds"))
            )

def upsert_members_batch(member_rows):
    """Synchronisiert Teammitglieder in einer einzigen SQLite-Transaktion."""
    if not member_rows:
        return 0
    with cx() as c:
        for uid, joined_at, probation_end in member_rows:
            row = c.execute("SELECT 1 FROM suite_members WHERE user_id=?", (str(uid),)).fetchone()
            if row:
                c.execute(
                    "UPDATE suite_members SET last_seen=?,archived_at=NULL WHERE user_id=?",
                    (joined_at, str(uid))
                )
            else:
                c.execute(
                    "INSERT INTO suite_members(user_id,joined_at,last_seen,internal_rating,notes,probation_end) VALUES(?,?,?,?,?,?)",
                    (str(uid), joined_at, joined_at, 80, "", probation_end)
                )
    return len(member_rows)

def warns(m,web,team_data=None):
    """Read a team record from an optional per-request snapshot to avoid repeated file loads."""
    td=team_data if team_data is not None else web.load_json(web.DATA_FILE,{})
    return web.active_warns(web.user_entry(td,str(m.id)))

def total_time(m,web):
    s=web.load_shifts();total=sum(web.credited_shift_seconds(x) for x in s.get("history",[]) if str(x.get("mod_id"))==str(m.id));a=s.get("active_shifts",{}).get(str(m.id))
    return total+(web.shift_elapsed(a) if a else 0)

def weekly(m,web):
    s=web.load_shifts();return web.calculate_weekly_seconds(str(m.id),s.get("history",[]),s.get("active_shifts",{}))

def team_score(m,web,task_rows=None,ticket_rows=None,team_data=None):
    w=weekly(m,web);goal=max(1,float(setting("weekly_goal_hours",3))*3600);activity=min(100,int(w/goal*100))
    ts=(
        db.list_tasks(assignee_id=m.id,limit=1000,include_archived=True)
        if task_rows is None
        else [t for t in task_rows if str(t.get("assignee_id"))==str(m.id)]
    )
    done=sum(t.get("status") in {"done","archived"} for t in ts);reliability=100 if not ts else int(done/len(ts)*100)
    ticket_source=db.list_tickets(limit=2000) if ticket_rows is None else ticket_rows
    tickets=[t for t in ticket_source if str(t.get("claimed_by_id"))==str(m.id) and t.get("status")=="closed"];support=min(100,len(tickets)*5)
    discipline=max(0,100-len(warns(m,web,team_data))*30);training=min(100,len([x for x in db.attempts(user_id=m.id,limit=200) if x.get("passed")])*25)
    return {"score":int(activity*.30+reliability*.20+support*.15+discipline*.20+training*.15),"activity":activity,"reliability":reliability,"support":support,"discipline":discipline,"training":training}

def rank_progress(m,web):
    ids={int(x) for x in web.load_config().get("team_role_ids",[])};rs=sorted([r for r in m.guild.roles if r.id in ids],key=lambda r:r.position)
    if not rs:return 50
    idx=max([i for i,r in enumerate(rs) if r in m.roles],default=0);return min(100,int(idx/max(1,len(rs)-1)*65+team_score(m,web)["score"]*.35))

def audit(gid,actor_id,actor_name,target_id,target_name,action,details):
    rid="ACT-"+uuid.uuid4().hex[:4].upper();eid="audit_"+uuid.uuid4().hex[:10];at=iso()
    with cx() as c:
        p=c.execute("SELECT hash FROM suite_audit ORDER BY rowid DESC LIMIT 1").fetchone();prev=p[0] if p else "GENESIS"
        raw="|".join(map(str,[eid,gid,at,actor_id,actor_name,target_id or "",target_name or "",action,details[:3000],rid,prev]));h=hashlib.sha256(raw.encode()).hexdigest()
        c.execute("INSERT INTO suite_audit VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(eid,str(gid),at,str(actor_id),actor_name,str(target_id or ""),target_name or "",action,details[:3000],rid,prev,h))
    try:db.record_event(action,"suite",target_id,actor_id,actor_name,{"request_id":rid,"details":details})
    except Exception:pass
    return rid

def audit_ok():
    with cx() as c:rows=[dict(x) for x in c.execute("SELECT * FROM suite_audit ORDER BY rowid").fetchall()]
    prev="GENESIS"
    for i,r in enumerate(rows,1):
        raw="|".join(map(str,[r["id"],r["guild_id"],r["occurred_at"],r["actor_id"],r["actor_name"],r["target_id"],r["target_name"],r["action"],r["details"],r["request_id"],prev]))
        if r["prev_hash"]!=prev or r["hash"]!=hashlib.sha256(raw.encode()).hexdigest():return False,i
        prev=r["hash"]
    return True,len(rows)

AUDIT_LIST_LIMIT=5000  # U-05: the page is paginated now, so more history is reachable than the former 1000/500

def audit_rows(q=""):
    """Suite audit chain, newest first, optionally filtered by a search text."""
    with cx() as c:rows=[dict(x) for x in c.execute("SELECT * FROM suite_audit ORDER BY rowid DESC LIMIT ?",(AUDIT_LIST_LIMIT,)).fetchall()]
    q=(q or "").lower()
    return [x for x in rows if not q or q in json.dumps(x,ensure_ascii=False).lower()]

def legacy_audit_rows(q=""):
    """Older dashboard actions from audit_logs.json, newest first."""
    source=__import__("webserver").load_json(__import__("webserver").AUDIT_FILE,[])
    rows=[x for x in source[-AUDIT_LIST_LIMIT:] if isinstance(x,dict)][::-1] if isinstance(source,list) else []
    q=(q or "").lower()
    return [x for x in rows if not q or q in json.dumps(x,ensure_ascii=False).lower()]

def auth(request,session,manager=False,perm="can_view_dashboard"):
    web=__import__("webserver");ctx=web.auth(request,session,perm=None if manager else perm)
    if manager and not (ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):raise HTTPException(403,"Nur Führungskräfte.")
    if ctx.member:upsert_member(ctx.member)
    return ctx

def allowed(i,key,web):
    if not i.guild or not isinstance(i.user,discord.Member):return False
    p,_=web.compute_perms(i.guild,i.user.id,web.load_config())
    if p.get("is_admin"):return True
    # During a warning-abuse lockout, prevent rank changes, kicks and warn withdrawal
    # from being used as an alternate route around the temporary safety restriction.
    if key in {"warn-remove","server-exit","team-exit","kick","team-kick","promote","demote"}:
        safety_state = web.pulse_db.get_team_safety_state(i.guild.id, i.user.id)
        if safety_state and safety_state.get("locked"):
            decision = web.pulse_db.check_team_safety_action(i.guild.id, i.user.id, key)
            # allowed() is synchronous, so schedule the Discord-side lockout alert /
            # automatic team-role removal on the active gateway event loop.
            try:
                from pulse_tasks import spawn  # P-07: referenced and logged, not fire-and-forget
                spawn(
                    web._notify_team_safety_incident(
                        i.guild, i.user, i.user, web.load_config(), decision,
                        source=f"Slash /{key}",
                    ),
                    name="team-safety-incident",
                    critical=True,
                )
            except RuntimeError:
                pass
            return False
    need={"warn":"can_warn","warn-remove":"can_warn","server-exit":"can_promote","team-exit":"can_promote","kick":"can_promote","team-kick":"can_promote","promote":"can_promote","demote":"can_promote","team-ping":"can_view_dashboard","pulse-ticket":"can_manage_tickets","melden":"can_view_dashboard","pulse-panel":"can_view_dashboard","pulse-lock":"is_admin","pulse-unlock":"is_admin","pulse-permission":"is_admin","activity-check":"can_view_dashboard","pulse-suite":"can_view_dashboard"}.get(key,"can_view_dashboard")
    rm=setting("command_roles",{}) or {};ids={int(x) for x in rm.get(key,[]) if str(x).isdigit()}
    return bool(p.get(need)) or any(r.id in ids for r in i.user.roles)

def locked():return bool(setting("emergency_lock",False))

def action_record(kind,payload,actor):
    aid="ACT-"+uuid.uuid4().hex[:8].upper()
    with cx() as c:c.execute("INSERT INTO suite_actions(id,kind,payload_json,status,created_by_id,created_by_name,approved_by_id,approved_by_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(aid,kind,json.dumps(payload,ensure_ascii=False),"awaiting_second",str(actor.id),actor.display_name,"","",iso(),iso()))
    return aid

def nav(active, manager=False):
    items=[("suite","/suite","⌂ Command Center"),("team","/suite/team","👥 Teamakten"),("time","/suite/time","⏱ Dienstzeit"),("schedule","/suite/schedule","🗓 Dienstplan"),("myschedule","/suite/my-schedule","👤 Mein Dienstplan"),("calendar","/suite/calendar","📅 Kalender"),("apps","/suite/applications","📝 Recruiting"),("interviews","/suite/interviews","🎤 Gespräche"),("training","/suite/training","🎓 Schulungen"),("tasks","/suite/tasks","📋 Aufgaben"),("news","/suite/news","📢 Team-News"),("audit","/suite/audit","🛡 Audit"),("search","/suite/search","⌕ Suche"),("cases","/suite/cases","🚨 Meldungen"),("ach","/suite/achievements","🏆 Erfolge")]
    if not manager:
        # Hide restricted personnel files and leadership controls from ordinary dashboard users.
        items=[item for item in items if item[0] not in {"suite","team","apps","interviews","training","audit","cases","ach"}]
    return "".join('<a class="{}" href="{}">{}</a>'.format("active" if k==active else "",p,label) for k,p,label in items)

def page(ctx,active,title,subtitle,body,js=""):
    """Render a Suite page inside the shared Pulse shell (one navigation, light/dark)."""
    web=__import__("webserver")
    from pulse_design import legacy_page
    perms=getattr(ctx,"perms",{}) or {}
    has_leadership=bool(perms.get("can_promote") or perms.get("is_admin"))
    top_actions=(
        '<button class=btn onclick=openSearch()>⌕ Suche</button><a class=btn href=/ultimate>⚡ Command Center</a><a class="btn primary" href=/suite>Führung</a>'
        if has_leadership else
        '<button class=btn onclick=openSearch()>⌕ Suche</button><a class="btn primary" href=/suite/tasks>Meine Aufgaben</a><a class=btn href=/suite/my-schedule>Mein Dienstplan</a>'
    )
    # Suite search stays available via its button; Ctrl+K opens the shared command palette.
    scripts=(
        """<div id=search class=pulse-search-overlay><div><div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px"><div><div class="kicker" style="font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)">Pulse Search</div><b>Alles im TeamOS finden</b></div><button class="pulse-btn ghost" onclick=closeSearch()>Esc</button></div><input id=sq class=pulse-input placeholder="Teamler, ID, Warn-ID, Ticket, Aufgabe, Bewerbung, Audit, Roblox …" oninput=doSearch(this.value)><div id=sr style="margin-top:8px"></div></div></div><script>
function openSearch(){document.getElementById('search').style.display='flex';document.getElementById('sq').focus()}
function closeSearch(){document.getElementById('search').style.display='none'}
document.addEventListener('keydown',e=>{if(e.key=='Escape')closeSearch()})
document.getElementById('search').addEventListener('click',e=>{if(e.target.id=='search')closeSearch()})
async function doSearch(q){if(q.trim().length<2){document.getElementById('sr').innerHTML='';return}document.getElementById('sr').innerHTML='<div class="pulse-empty">Suche…</div>';try{let d=await (await fetch('/suite/api/search?q='+encodeURIComponent(q))).json();const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),u=v=>String(v||'').startsWith('/')&&!String(v).startsWith('//')?String(v):'#';document.getElementById('sr').innerHTML=(d.results||[]).map(x=>'<a class=result href="'+h(u(x.url))+'"><b>'+h(x.title)+'</b><div class="text-[11px] text-slate-400">'+h(x.type)+' · '+h(x.meta)+'</div></a>').join('')||'<div class="pulse-empty">Keine Treffer.</div>'}catch(e){document.getElementById('sr').innerHTML='<div class="pulse-empty">Suche nicht verfügbar.</div>'}}
"""
        + js
        + """</script>"""
    )
    return legacy_page(web,ctx,"s:"+str(active),title,subtitle,body,actions=top_actions,scripts=scripts,kicker="Pulse TeamOS · Operations Suite")


_ROUTES=False
def register_routes():
    global _ROUTES
    if _ROUTES:return
    webserver=__import__("webserver");app=webserver.app;init_db();seed()

    @app.get("/suite",response_class=HTMLResponse)
    async def leadership(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);web=webserver;ms=members(ctx.guild,web);sh=web.load_shifts();today=now().date().isoformat();active=sum(str(m.status) in {"online","idle","dnd"} for m in ms);duty=sum(x.get("status") in {"online","break"} for x in sh.get("active_shifts",{}).values())
        day=sum(web.credited_shift_seconds(x) for x in sh.get("history",[]) if x.get("date")==today)+sum(web.shift_elapsed(x) for x in sh.get("active_shifts",{}).values() if x.get("date")==today)
        apps=web.load_json(web.APPS_FILE,{});pending=sum(str(a.get("status","pending")).lower() in {"pending","new","in_review","in prüfung","review"} for a in apps.values());threes=sum(len(warns(m,web))>=3 for m in ms);ticks=db.list_tickets(limit=3000);overdue=[x for x in db.list_tasks(limit=3000) if x.get("status") not in {"done","archived"} and x.get("due_at") and (local(x.get("due_at")) or now())<=now()];loas=sum(1 for x in web.get_loas().values() if x.get("active"));promos=len([p for p in _promos() if p.get("status") in {"pending","revision"}]);low=[m for m in ms if weekly(m,web)<float(setting("weekly_goal_hours",3))*3600]
        try:
            import pulse_ultimate as runtime_monitor
            ram_now=runtime_monitor.container_memory_mb()
            ram_peak=runtime_monitor.process_peak_rss_mb()
            ram_level=runtime_monitor.memory_guard_level(ram_now)
            ram_trend=runtime_monitor.ram_trend_snapshot()
        except Exception:
            ram_now=None;ram_peak=None;ram_level=0;ram_trend={"growth_mb":None,"window_minutes":None,"growth_mb_per_hour":None}
        red=threes or overdue or any(x.get("priority")=="urgent" and x.get("status")!="closed" for x in ticks) or ram_level>=2
        yellow=bool(low or pending or loas or promos or ram_level==1)
        state=("🔴 Sofort handeln","bad") if red else ("🟡 Aufmerksamkeit nötig","warn") if yellow else ("🟢 Alles normal","ok")
        with cx() as c:last=[dict(x) for x in c.execute("SELECT * FROM suite_audit ORDER BY rowid DESC LIMIT 8").fetchall()]
        body=f'<div class="grid g4"><div class=card><div class=tiny>Aktuell aktiv</div><div class=metric>{active}</div></div><div class=card><div class=tiny>Im Dienst</div><div class=metric>{duty}</div></div><div class=card><div class=tiny>Heute Dienstzeit</div><div class=metric>{secfmt(day)}</div></div><div class=card><div class=tiny>Systemlage</div><div class=metric><span class="badge {state[1]}">{state[0]}</span></div></div></div><div class="grid g4" style="margin-top:13px"><div class=card><h3>🚨 Handlungsbedarf</h3><div class=list><div class=row>Offene Bewerbungen <b>{pending}</b></div><div class=row>3+ aktive Warnungen <b>{threes}</b></div><div class=row>Offene Tickets <b>{sum(x.get("status")!="closed" for x in ticks)}</b></div><div class=row>Überfällige Aufgaben <b>{len(overdue)}</b></div><div class=row>Abmeldungen <b>{loas}</b></div><div class=row>Beförderungen <b>{promos}</b></div></div></div><div class=card><h3>🟡 Wenig Dienstzeit</h3>{''.join(f'<div class=row><a href=/suite/member/{m.id}><b>{esc(m.display_name)}</b></a><span class=tiny>{secfmt(weekly(m,web))}</span></div>' for m in low[:8]) or "Niemand."}</div><div class=card><h3>✅ Heute aktiv</h3>{''.join(f'<div class=row><a href=/suite/member/{m.id}><b>{esc(m.display_name)}</b></a><span class=tiny>{secfmt(weekly(m,web))} Woche</span></div>' for m in ms if str(m.id) in {str(x.get("mod_id")) for x in sh.get("history",[]) if x.get("date")==today} or str(m.id) in sh.get("active_shifts",{})) or "Noch niemand."}</div><div class=card><h3>🛡 Letzte Teamänderungen</h3><div class=timeline>{''.join(f'<div class=event><b>{esc(x["action"])}</b><div class=tiny>{esc(x["occurred_at"])} · {esc(x["actor_name"])} → {esc(x["target_name"])} · {esc(x["request_id"])}</div><div>{esc(x["details"])}</div></div>' for x in last) or "Noch keine Audits."}</div></div></div><div class="grid g2" style="margin-top:13px"><div class=card><h3>📈 Wochenrangliste</h3>{''.join(f'<div class=row>#{i} <b>{esc(m.display_name)}</b><span>{secfmt(weekly(m,web))} · {team_score(m,web)["score"]}/100</span></div>' for i,m in enumerate(sorted(ms,key=lambda x:weekly(x,web),reverse=True)[:10],1))}</div><div class=card><h3>⚡ Schnellzugriff</h3><a class=btn href=/suite/promotions>Beförderungen</a> <a class=btn href=/suite/applications>Bewerbungen</a> <a class=btn href=/suite/tasks>Aufgaben</a> <a class=btn href=/suite/audit>Audit Center</a></div></div>'
        ram_text="Nicht verfügbar" if ram_now is None else f"{ram_now:.1f} MB / {runtime_monitor.RAM_LIMIT_MB:.0f} MB Containerlimit" + (f" · Prozessspitze {ram_peak:.1f} MB" if ram_peak is not None else "")
        if ram_trend.get("growth_mb") is None:
            trend_text="Messwerte werden gesammelt"
        else:
            growth=float(ram_trend["growth_mb"])
            trend_text=f"{growth:+.1f} MB in {float(ram_trend.get('window_minutes') or 0):.0f} Min. · {float(ram_trend.get('growth_mb_per_hour') or 0):+.1f} MB/Stunde"
        ram_badge={0:"ok",1:"warn",2:"bad",3:"bad"}.get(ram_level,"warn")
        body+=f'<div class="grid g2" style="margin-top:13px"><div class=card><h3>🧠 RAM-Schutz</h3><div class=row><span>Aktueller Verbrauch</span><b>{esc(ram_text)}</b></div><div class=row><span>Zustand</span><span class="badge {ram_badge}">{["Normal","Warnung","Kritisch","Notfallreserve"][ram_level]}</span></div><div class=row><span>Trend</span><b>{esc(trend_text)}</b></div><a class=btn href=/ultimate>Systemmonitor öffnen</a></div><div class=card><h3>🧭 Tagesprioritäten</h3><div class=row><span>Überfällige Aufgaben</span><b>{len(overdue)}</b></div><div class=row><span>Offene Tickets</span><b>{sum(x.get("status")!="closed" for x in ticks)}</b></div><div class=row><span>Offene Bewerbungen</span><b>{pending}</b></div><div class=row><span>Offene Beförderungen</span><b>{promos}</b></div><div class=row><span>Teamler mit 3+ aktiven Warnungen</span><b>{threes}</b></div></div></div>'
        return page(ctx,"suite","Führungspanel","Aktive Lage, Personal, Warnungen, Tickets, Aufgaben und Änderungen",body)

    @app.get("/suite/team",response_class=HTMLResponse)
    async def team_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);web=webserver;rows=""
        # Fetch shared workloads and the team record file once for this request.
        team_members=members(ctx.guild,web)
        task_snapshot=db.list_tasks(limit=1000,include_archived=True)
        ticket_snapshot=db.list_tickets(limit=2000)
        team_data=web.load_json(web.DATA_FILE,{})
        for m in team_members:
            s=team_score(m,web,task_rows=task_snapshot,ticket_rows=ticket_snapshot,team_data=team_data);sm=member_row(m.id);cl="ok" if s["score"]>=80 else "warn" if s["score"]>=60 else "bad"
            rows+=f'<tr><td><a href=/suite/member/{m.id}><b>{esc(m.display_name)}</b></a><div class=tiny>{m.id}</div></td><td>{esc(m.top_role.name)}</td><td>{secfmt(total_time(m,web))}</td><td>{secfmt(weekly(m,web))}</td><td><span class="badge {cl}">{s["score"]}/100</span></td><td>{min(5,len(warns(m,web,team_data)))}/5</td><td>{esc(sm.get("probation_end") if sm else "—")}</td><td>{"🟢" if str(m.status) in {"online","idle","dnd"} else "⚪"}</td></tr>'
        body=f'<div class=card><input class=input id=tf placeholder="Name oder Discord-ID…" oninput="f(this.value)"><div class=scroll style="margin-top:10px"><table class=table id=tt><thead><tr><th>Teamler</th><th>Position</th><th>Gesamt</th><th>Woche</th><th>Score</th><th>Warnungen</th><th>Probezeit</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div></div>'
        return page(ctx,"team","Teamakten","Digitale Personalakte mit Score, Dienstzeit, Warnungen und Probezeit",body,"function f(q){q=q.toLowerCase();document.querySelectorAll('#tt tbody tr').forEach(r=>r.style.display=r.innerText.toLowerCase().includes(q)?'':'none')}")

    @app.get("/suite/member/{member_id}",response_class=HTMLResponse)
    async def member_page(request:Request,member_id:int,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);web=webserver;m=ctx.guild.get_member(member_id)
        if not m or m.bot or m not in members(ctx.guild,web):raise HTTPException(404,"Teammitglied nicht gefunden")
        upsert_member(m);sm=member_row(member_id) or {};td=web.load_json(web.DATA_FILE,{});entry=web.user_entry(td,str(member_id));s=team_score(m,web)
        with cx() as c:ps=[dict(x) for x in c.execute("SELECT * FROM suite_promotions WHERE target_id=? ORDER BY created_at DESC",(str(member_id),)).fetchall()];aud=[dict(x) for x in c.execute("SELECT * FROM suite_audit WHERE target_id=? OR actor_id=? ORDER BY rowid DESC LIMIT 80",(str(member_id),str(member_id))).fetchall()]
        ticks=[x for x in db.list_tickets(limit=3000) if str(x.get("user_id"))==str(member_id) or str(x.get("claimed_by_id"))==str(member_id)];tasks_=db.list_tasks(assignee_id=member_id,limit=1000,include_archived=True);sh=web.load_shifts();hist=[x for x in sh.get("history",[]) if str(x.get("mod_id"))==str(member_id)];last=hist[-1].get("ended_at_iso") if hist else "—"
        try:
            with closing(sqlite3.connect(os.path.join(BASE,"activity_check.db"))) as c, c:act=c.execute("SELECT COUNT(*) FROM responses WHERE user_id=?",(member_id,)).fetchone()[0]
        except Exception:act=0
        tabs=[("o","Übersicht"),("t","Dienstzeit"),("w","Warnungen"),("ti","Tickets"),("ta","Aufgaben"),("n","Notizen"),("p","Beförderungen"),("a","Audit")]
        panes=f'<div>{''.join(f"<button class=btn onclick=tab(\'{k}\')>{v}</button> " for k,v in tabs)}</div><div id=o class=pane style="margin-top:13px"><div class="grid g4"><div class=card><div class=tiny>Eintritt</div><b>{esc(sm.get("joined_at","—"))}</b></div><div class=card><div class=tiny>Position</div><b>{esc(m.top_role.name)}</b></div><div class=card><div class=tiny>Beförderungen</div><b>{sum(x.get("status")=="approved" for x in ps)}</b></div><div class=card><div class=tiny>Degradierungen</div><b>{sum(x.get("action")=="demote" for x in aud)}</b></div><div class=card><div class=tiny>Gesamtzeit</div><b>{secfmt(total_time(m,web))}</b></div><div class=card><div class=tiny>Woche</div><b>{secfmt(weekly(m,web))}</b></div><div class=card><div class=tiny>Letzte Aktivität</div><b>{esc(last)}</b></div><div class=card><div class=tiny>Letzter Login</div><b>{esc(sm.get("last_seen","—"))}</b></div><div class=card><div class=tiny>Letzter Activity Check</div><b>{act} bestätigt</b></div><div class=card><div class=tiny>Interne Bewertung</div><b>{esc(sm.get("internal_rating",80))}/100</b></div><div class=card><div class=tiny>Rangfortschritt</div><b>{rank_progress(m,web)}%</b></div><div class=card><div class=tiny>Team Score</div><b>{s["score"]}/100</b><div class=tiny>Aktivität {s["activity"]} · Zuverlässigkeit {s["reliability"]} · Support {s["support"]} · Disziplin {s["discipline"]}</div></div></div></div><div id=t class=pane style="display:none"><div class=card><div class=metric>{secfmt(total_time(m,web))}</div><div class=muted>Woche {secfmt(weekly(m,web))} · Schichten {len(hist)}</div></div></div><div id=w class=pane style="display:none"><div class=card>{(''.join(f"<div class=row><span>{esc(x.get('id'))} · {esc(x.get('date'))}<br>{esc(x.get('reason'))}</span><span class=badge>{'Aktiv' if x.get('active',True) else 'Zurückgezogen'}</span></div>" for x in entry.get("warns_list",[])) or "Keine Warnungen.") if web.can_see_warnings(ctx.perms,ctx.user.get("id"),member_id) else "🔒 Nur für die Person selbst und Verwarnungsberechtigte sichtbar."}</div></div><div id=ti class=pane style="display:none"><div class=card>{''.join(f"<div class=row><b>{esc(x.get('id'))}</b><span>{esc(x.get('status'))} · {esc(x.get('category'))}</span></div>" for x in ticks) or "Keine Tickets."}</div></div><div id=ta class=pane style="display:none"><div class=card>{''.join(f"<div class=row><a href=/suite/task/{x['id']}><b>{esc(x.get('title'))}</b></a><span>{esc(x.get('status'))}</span></div>" for x in tasks_) or "Keine Aufgaben."}</div></div><div id=n class=pane style="display:none"><div class=card><form method=post action=/suite/member/{member_id}/note><textarea class=ta name=note>{esc(sm.get('notes',''))}</textarea><button class="btn primary">Notiz speichern</button></form></div></div><div id=p class=pane style="display:none"><div class=card>{''.join(f"<div class=row><span>{esc(x.get('current_role'))} → {esc(x.get('target_role'))}</span><b>{esc(x.get('status'))}</b></div>" for x in ps) or "Keine Vorgänge."}</div></div><div id=a class=pane style="display:none"><div class=card>{''.join(f"<div class=event><b>{esc(x.get('action'))}</b> · {esc(x.get('occurred_at'))}<div class=tiny>{esc(x.get('details'))} · {esc(x.get('request_id'))}</div></div>" for x in aud) or "Keine Audits."}</div></div>'
        return page(ctx,"team",f"Teamakte · {m.display_name}","Übersicht · Dienstzeit · Warnungen · Tickets · Aufgaben · Notizen · Beförderungen · Audit",panes,"function tab(x){document.querySelectorAll('.pane').forEach(e=>e.style.display=e.id==x?'block':'none')}")

    @app.post("/suite/member/{member_id}/note")
    async def member_note(request:Request,member_id:int,note:str=Form(...),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True)
        target=ctx.guild.get_member(member_id)
        if not target or target.bot or target not in members(ctx.guild,webserver):
            raise HTTPException(404,"Teammitglied nicht gefunden")
        with cx() as c:c.execute("UPDATE suite_members SET notes=?,last_seen=? WHERE user_id=?",(note[:5000],iso(),str(member_id)))
        audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),member_id,target.display_name,"note_updated","Interne Notiz aktualisiert");return RedirectResponse(f"/suite/member/{member_id}",303)

    @app.get("/suite/time",response_class=HTMLResponse)
    async def time_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);web=webserver;sh=web.load_shifts();ms=members(ctx.guild,web);hist=sh.get("history",[]);d=now().date().isoformat();month=now().strftime("%Y-%m")
        day=sum(web.credited_shift_seconds(x) for x in hist if x.get("date")==d)+sum(web.shift_elapsed(x) for x in sh.get("active_shifts",{}).values() if x.get("date")==d);monthsec=sum(web.credited_shift_seconds(x) for x in hist if str(x.get("date","")).startswith(month))
        forms='<form method=post action=/shift/action style="display:inline"><input type=hidden name=shift_action value=start><button class="btn primary">▶ Start</button></form> <form method=post action=/shift/action style="display:inline"><input type=hidden name=shift_action value=break><button class=btn>☕ Pause</button></form> <form method=post action=/shift/action style="display:inline"><input type=hidden name=shift_action value=resume><button class=btn>↻ Fortsetzen</button></form> <form method=post action=/shift/action style="display:inline"><input type=hidden name=shift_action value=end><button class="btn danger">■ Ende</button></form>'
        rows=''.join(f'<tr><td>{i}</td><td><a href=/suite/member/{m.id}><b>{esc(m.display_name)}</b></a></td><td>{secfmt(weekly(m,web))}</td><td>{secfmt(total_time(m,web))}</td><td>{secfmt(max(0,weekly(m,web)-int(float(setting("weekly_goal_hours",3))*3600)))}</td></tr>' for i,m in enumerate(sorted(ms,key=lambda x:weekly(x,web),reverse=True),1))
        return page(ctx,"time","Dienstzeit 2.0","Start → Pause → Fortsetzen → Ende · Tag / Woche / Monat",f'<div class=card>{forms}</div><div class="grid g4" style="margin-top:13px"><div class=card><div class=tiny>Heute</div><div class=metric>{secfmt(day)}</div></div><div class=card><div class=tiny>Aktive Schichten</div><div class=metric>{sum(x.get("status") in {"online","break"} for x in sh.get("active_shifts",{}).values())}</div></div><div class=card><div class=tiny>Wochenziel</div><div class=metric>{float(setting("weekly_goal_hours",3)):.1f}h</div></div><div class=card><div class=tiny>Monat</div><div class=metric>{secfmt(monthsec)}</div></div></div><div class=card style="margin-top:13px"><h3>🏆 Wochenrangliste · Überstunden</h3><div class=scroll><table class=table><tr><th>#</th><th>Teamler</th><th>Woche</th><th>Gesamt</th><th>Überstunden</th></tr>{rows}</table></div></div>')

    @app.get("/suite/my-schedule",response_class=HTMLResponse)
    async def my_schedule(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session)
        with cx() as c:
            rows=[dict(x) for x in c.execute("SELECT * FROM suite_schedule WHERE guild_id=? AND assignee_id=? ORDER BY date,start_time",(ctx.guild.id,str(ctx.user["id"]))).fetchall()]
        cards="".join(f'<div class=card><div class=row><span><b>{esc(x["date"])}</b> · {esc(x["shift_name"])}<div class=tiny>{esc(x["start_time"])}–{esc(x["end_time"])}</div></span></div></div>' for x in rows)
        return page(ctx,"myschedule","Mein Dienstplan","Deine persönlichen Schichten und automatische 30-Minuten-Erinnerungen",cards or "<div class=card>Keine Schichten eingetragen.</div>")

    @app.get("/suite/schedule",response_class=HTMLResponse)
    async def schedule_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);ms=members(ctx.guild,webserver)
        with cx() as c:rows=[dict(x) for x in c.execute("SELECT * FROM suite_schedule WHERE guild_id=? ORDER BY date,start_time",(ctx.guild.id,)).fetchall()]
        opts="".join(f'<option value="{m.id}">{esc(m.display_name)}</option>' for m in ms)
        body=f'<div class="grid g2"><div class=card><h3>➕ Dienst planen</h3><form class=form method=post action=/suite/schedule/create><div class=field><label>Datum</label><input class=input type=date name=date required></div><div class=field><label>Dienst</label><input class=input name=shift_name required></div><div class=field><label>Start</label><input class=input type=time name=start_time required></div><div class=field><label>Ende</label><input class=input type=time name=end_time required></div><div class="field span2"><label>Teamler</label><select class=select name=assignee_id required>{opts}</select></div><div class=span2><button class="btn primary">Planen</button></div></form></div><div class=card><h3>⏰ Nächste Schichten</h3>{''.join(f'<div class=row><span>{esc(x["date"])} · {esc(x["shift_name"])}<div class=tiny>{esc(x["start_time"])}–{esc(x["end_time"])}</div></span><b>{esc(x["assignee_name"])}</b></div>' for x in rows[:15])}</div></div><div class=card style="margin-top:13px"><h3>📅 Wochenplan</h3>{''.join(f'<div class=row>{esc(x["date"])} · {esc(x["shift_name"])} · {esc(x["assignee_name"])} <span>{esc(x["start_time"])}–{esc(x["end_time"])}</span></div>' for x in rows)}</div>'
        return page(ctx,"schedule","Dienstplan","Früh-, Spät- und Sonderdienste · 30-Minuten-Erinnerung",body)

    @app.post("/suite/schedule/create")
    async def schedule_create(request:Request,date:str=Form(...),shift_name:str=Form(...),start_time:str=Form(...),end_time:str=Form(...),assignee_id:int=Form(...),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);m=ctx.guild.get_member(assignee_id)
        if not m:raise HTTPException(404,"Teamler nicht gefunden")
        sid="sch_"+uuid.uuid4().hex[:10]
        with cx() as c:c.execute("INSERT INTO suite_schedule VALUES(?,?,?,?,?,?,?,?,?,?,?)",(sid,str(ctx.guild.id),date,shift_name[:80],start_time,end_time,str(m.id),m.display_name,str(ctx.user["id"]),ctx.user.get("global_name",""),iso()))
        db.notify(m.id,"🗓 Dienst geplant",f"{date} · {shift_name} · {start_time}-{end_time}","info","/suite/schedule",f"schedule:{sid}",86400);audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),m.id,m.display_name,"schedule_created",f"{date} · {shift_name}");return RedirectResponse("/suite/schedule",303)

    @app.get("/suite/calendar",response_class=HTMLResponse)
    async def calendar_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);web=webserver;d=now().date();view=request.query_params.get("view","month")
        with cx() as c:ev=[dict(x) for x in c.execute("SELECT * FROM suite_events WHERE guild_id=? ORDER BY start_at",(ctx.guild.id,)).fetchall()];sch=[dict(x) for x in c.execute("SELECT * FROM suite_schedule WHERE guild_id=? ORDER BY date,start_time",(ctx.guild.id,)).fetchall()]
        items=[(x["start_at"],x["kind"],x["title"],x["description"]) for x in ev]+[(x["date"]+"T"+x["start_time"],"Dienstplan",x["shift_name"],x["assignee_name"]) for x in sch]+[(str(x["von"]),"Abmeldung",x.get("name",""),x.get("grund","")) for x in web.get_loas().values() if x.get("active")]
        if view=="day":items=[x for x in items if str(x[0])[:10]==d.isoformat()]
        elif view=="week":a=(d-timedelta(days=d.weekday())).isoformat();b=(d+timedelta(days=6-d.weekday())).isoformat();items=[x for x in items if a<=str(x[0])[:10]<=b]
        else:items=[x for x in items if str(x[0])[:7]==d.strftime("%Y-%m")]
        kinds=["Meeting","Schulung","Prüfung","Abmeldung","Dienstplan","Event","Bewerbung","Beförderung"]
        form='<div class=card><h3>➕ Kalendereintrag</h3><form class=form method=post action=/suite/calendar/create><div class=field><label>Titel</label><input class=input name=title required></div><div class=field><label>Typ</label><select class=select name=kind>'+''.join(f'<option>{esc(x)}</option>' for x in kinds)+'</select></div><div class=field><label>Start</label><input class=input type=datetime-local name=start_at required></div><div class=field><label>Ende</label><input class=input type=datetime-local name=end_at></div><div class="field span2"><label>Beschreibung</label><textarea class=ta name=description></textarea></div><div class=span2><button class="btn primary">Speichern</button></div></form></div>'
        tabs=" ".join(f'<a class="btn {"primary" if view==x.lower() else ""}" href="/suite/calendar?view={x.lower()}">{x}</a>' for x in ["Tag","Woche","Monat"]);listing="".join(f'<div class=row><span><b>{esc(x[2])}</b><div class=tiny>{esc(x[1])} · {esc(x[0])}</div></span><span>{esc(x[3])}</span></div>' for x in items[:100])
        return page(ctx,"calendar","Team-Kalender 2.0","Tages-, Wochen- und Monatsansicht",form+f'<div class=card style="margin-top:13px"><div>{tabs}</div>{listing or "Keine Termine."}</div>')

    @app.post("/suite/calendar/create")
    async def calendar_create(request:Request,title:str=Form(...),kind:str=Form(...),start_at:str=Form(...),end_at:str=Form(""),description:str=Form(""),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);eid="evt_"+uuid.uuid4().hex[:10]
        with cx() as c:c.execute("INSERT INTO suite_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",(eid,str(ctx.guild.id),title[:160],kind[:50],start_at,end_at,description[:3000],"",str(ctx.user["id"]),ctx.user.get("global_name",""),iso()))
        audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),eid,title,"calendar_created",kind);return RedirectResponse("/suite/calendar",303)

    @app.get("/suite/applications",response_class=HTMLResponse)
    async def applications_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);apps=webserver.load_json(webserver.APPS_FILE,{});rows=''.join(f'<div class=card><div class=row><span><b>{esc(appname(aid,x))}</b><div class=tiny>{esc(aid)} · {esc(x.get("status","pending"))} · {esc(x.get("processor_name","—"))}</div></span><a class=btn href="/suite/applications/{esc(aid)}">Öffnen</a></div></div>' for aid,x in apps.items())
        return page(ctx,"apps","Bewerbungszentrum 2.0","Bewerbungsnummer · Bearbeiter · Status · Gespräch · Testergebnis · Entscheidung",rows or "<div class=card>Keine Bewerbungen.</div>")

    @app.get("/suite/applications/{app_id}",response_class=HTMLResponse)
    async def application_page(request:Request,app_id:str,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);apps=webserver.load_json(webserver.APPS_FILE,{});item=apps.get(app_id)
        if not item:raise HTTPException(404,"Bewerbung nicht gefunden")
        hist=item.get("history",[]);body=f'<div class="grid g2"><div class=card><h3>👤 {esc(appname(app_id,item))}</h3><div class=tiny>{esc(app_id)}</div><pre style="white-space:pre-wrap">{esc(json.dumps(item,ensure_ascii=False,indent=2))}</pre></div><div class=card><form method=post action="/suite/applications/{esc(app_id)}/update"><div class=field><label>Status</label><select class=select name=status><option>pending</option><option>in_review</option><option>interview</option><option>accepted</option><option>rejected</option></select></div><div class=field><label>Testergebnis</label><input class=input name=testergebnis></div><div class=field><label>Interne Notizen</label><textarea class=ta name=note></textarea></div><button class="btn primary">Speichern</button></form><h3>🕘 Historie</h3>{''.join(f'<div class=row><span>{esc(x.get("action"))}</span><span class=tiny>{esc(x.get("at"))} · {esc(x.get("by"))}</span></div>' for x in hist) or "Keine Historie."}</div></div>'
        return page(ctx,"apps",f"Bewerbung · {appname(app_id,item)}","Recruiting-Fall mit Status, Bearbeiter, Testergebnis, Gespräch und Verlauf",body)

    @app.post("/suite/applications/{app_id}/update")
    async def application_update(request:Request,app_id:str,status:str=Form(...),testergebnis:str=Form(""),note:str=Form(""),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);apps=webserver.load_json(webserver.APPS_FILE,{});item=apps.get(app_id)
        if not item:raise HTTPException(404,"Bewerbung nicht gefunden")
        if status not in {"pending","in_review","interview","accepted","rejected"}:raise HTTPException(400,"Ungültiger Status")
        item["status"]=status;item["processor_id"]=ctx.user["id"];item["processor_name"]=ctx.user.get("global_name","");item["testergebnis"]=testergebnis[:1000];item["internal_notes"]=note[:3000];item.setdefault("history",[]).append({"at":iso(),"action":status,"by":ctx.user.get("global_name",""),"details":note[:1000]});item["history"]=item["history"][-100:];webserver.save_json(webserver.APPS_FILE,apps);audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),app_id,appname(app_id,item),"application_updated",f"{status} · {note}");return RedirectResponse(f"/suite/applications/{app_id}",303)

    @app.get("/suite/interviews",response_class=HTMLResponse)
    async def interviews_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session)
        with cx() as c:rows=[dict(x) for x in c.execute("SELECT * FROM suite_interviews ORDER BY created_at DESC LIMIT 200").fetchall()]
        listing=''.join(f'<div class=card><b>{esc(x["applicant_name"])}</b><div class=tiny>{esc(x["application_id"])} · {esc(x["created_at"])}</div><div class=metric>{x["overall"]}% geeignet</div><div>{esc(x["recommendation"])}</div></div>' for x in rows)
        return page(ctx,"interviews","Bewerbungsgespräche","Bewertung von Auftreten, Kommunikation, Regelkenntnis, Aktivität und Teamfähigkeit",f'<div class=card><a class="btn primary" href=/suite/interview/create>🎤 Gespräch starten</a></div><div class="grid g3" style="margin-top:13px">{listing or "Keine Gespräche."}</div>')

    @app.get("/suite/interview/create",response_class=HTMLResponse)
    async def interview_form(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);apps=webserver.load_json(webserver.APPS_FILE,{});opts="".join(f'<option value="{esc(k)}">{esc(appname(k,v))}</option>' for k,v in apps.items())
        body=f'<div class=card><form class=form method=post action=/suite/interview/create><div class=field><label>Bewerbung</label><select class=select name=application_id required>{opts}</select></div><div class=field><label>Auftreten</label><input class=input type=number min=0 max=100 name=auftreten required></div><div class=field><label>Kommunikation</label><input class=input type=number min=0 max=100 name=kommunikation required></div><div class=field><label>Regelkenntnis</label><input class=input type=number min=0 max=100 name=regeln required></div><div class=field><label>Aktivität</label><input class=input type=number min=0 max=100 name=aktivitaet required></div><div class=field><label>Teamfähigkeit</label><input class=input type=number min=0 max=100 name=team required></div><div class="field span2"><label>Notizen</label><textarea class=ta name=notes></textarea></div><div class=span2><button class="btn primary">Gespräch auswerten</button></div></form></div>'
        return page(ctx,"interviews","Bewerbungsgespräch starten","Automatische Eignungsempfehlung",body)

    @app.post("/suite/interview/create")
    async def interview_create(request:Request,application_id:str=Form(...),auftreten:int=Form(...),kommunikation:int=Form(...),regeln:int=Form(...),aktivitaet:int=Form(...),team:int=Form(...),notes:str=Form(""),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);apps=webserver.load_json(webserver.APPS_FILE,{});item=apps.get(application_id)
        if not item:raise HTTPException(404,"Bewerbung nicht gefunden")
        v=[max(0,min(100,x)) for x in (auftreten,kommunikation,regeln,aktivitaet,team)];overall=sum(v)//5;rec="Sehr geeignet" if overall>=85 else "Geeignet" if overall>=70 else "Bedingt geeignet" if overall>=55 else "Nicht empfohlen";iid="int_"+uuid.uuid4().hex[:10]
        with cx() as c:c.execute("INSERT INTO suite_interviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",(iid,application_id,str(item.get("discord_id") or item.get("user_id") or ""),appname(application_id,item),json.dumps(dict(zip(("Auftreten","Kommunikation","Regelkenntnis","Aktivität","Teamfähigkeit"),v)),ensure_ascii=False),overall,rec,notes[:3000],str(ctx.user["id"]),ctx.user.get("global_name",""),iso()))
        item["interview_id"]=iid;item["testergebnis"]=f"{overall}% · {rec}";item["status"]="interview";item.setdefault("history",[]).append({"at":iso(),"action":"interview","by":ctx.user.get("global_name",""),"details":f"{overall}% · {rec}"});webserver.save_json(webserver.APPS_FILE,apps);audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),application_id,appname(application_id,item),"interview_created",f"{overall}% · {rec}");return RedirectResponse("/suite/interviews",303)

    @app.get("/suite/promotions",response_class=HTMLResponse)
    async def promotions_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);web=webserver;ms=members(ctx.guild,web);rids={int(x) for x in web.load_config().get("team_role_ids",[])};roles=sorted([r for r in ctx.guild.roles if r.id in rids],key=lambda r:r.position,reverse=True);opts="".join(f'<option value="{m.id}">{esc(m.display_name)}</option>' for m in ms);ropts="".join(f'<option value="{r.id}">{esc(r.name)}</option>' for r in roles)
        cards=''.join(f'<div class=card><div class=row><b>{esc(p["target_name"])}</b><span class="badge">{esc(p["status"])}</span></div><div class=tiny>{esc(p["current_role"])} → {esc(p["target_role"])} · Score {p["score"]}/100 · {len(json.loads(p["approvals_json"] or "[]"))}/{p["required_approvals"]} Freigaben</div><p>{esc(p["reason"])}</p><form method=post action=/suite/promotions/{p["id"]}/action><button class="btn primary" name=decision value=approve>✅ Genehmigen</button> <button class=btn name=decision value=revision>↩ Bearbeiten</button> <button class="btn danger" name=decision value=reject>❌ Ablehnen</button></form></div>' for p in _promos() if p["status"] in {"pending","revision"})
        body=f'<div class="grid g2"><div class=card><h3>📈 Antrag</h3><form class=form method=post action=/suite/promotions/create><div class=field><label>Mitarbeiter</label><select class=select name=target_id required>{opts}</select></div><div class=field><label>Zielposition</label><select class=select name=target_role_id required>{ropts}</select></div><div class="field span2"><label>Begründung</label><textarea class=ta name=reason required></textarea></div><div class=field><label>Leistung</label><input class=input name=performance></div><div class=field><label>Aktivität</label><input class=input name=activity></div><div class=span2><button class="btn primary">Antrag erstellen</button></div></form></div><div class=card><h3>📋 Offene Vorgänge</h3>{cards or "Keine offenen Vorgänge."}</div></div>'
        return page(ctx,"team","Beförderungen 2.0","Mitarbeiter · aktuelle Position · Zielposition · Begründung · Leistung · Aktivität · Warnungen · Score",body)

    @app.post("/suite/promotions/create")
    async def promotions_create(request:Request,target_id:int=Form(...),target_role_id:int=Form(...),reason:str=Form(...),performance:str=Form(""),activity:str=Form(""),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);web=webserver;m=ctx.guild.get_member(target_id);r=ctx.guild.get_role(target_role_id);ids={int(x) for x in web.load_config().get("team_role_ids",[])}
        if not m or not r or r.id not in ids or m.id==ctx.user["id"] or r.position>=ctx.user.top_role.position:raise HTTPException(403,"Ungültige Zielposition oder Hierarchie.")
        pid="prom_"+uuid.uuid4().hex[:10];required=2 if r.position>=ctx.user.top_role.position-1 else 1
        with cx() as c:c.execute("INSERT INTO suite_promotions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(pid,str(m.id),m.display_name,m.top_role.name,str(m.top_role.id),r.name,str(r.id),reason[:2000],performance[:1000],activity[:1000],str(len(warns(m,web))),team_score(m,web)["score"],"pending",required,"[]",str(ctx.user["id"]),ctx.user.get("global_name",""),"",iso(),iso()))
        audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),m.id,m.display_name,"promotion_created",f"{m.top_role.name} → {r.name} · {reason}");return RedirectResponse("/suite/promotions",303)

    @app.post("/suite/promotions/{pid}/action")
    async def promotions_action(request:Request,pid:str,decision:str=Form(...),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True)
        with cx() as c:r=c.execute("SELECT * FROM suite_promotions WHERE id=?",(pid,)).fetchone()
        if not r:raise HTTPException(404,"Antrag nicht gefunden")
        p=dict(r);a=json.loads(p["approvals_json"] or "[]")
        if p["status"] not in {"pending","revision"}:return RedirectResponse("/suite/promotions",303)
        if decision=="revision":
            with cx() as c:c.execute("UPDATE suite_promotions SET status='revision',decision_note=?,updated_at=? WHERE id=?",(f"Zur Bearbeitung von {ctx.user.get('global_name','')}",iso(),pid))
        elif decision=="reject":
            with cx() as c:c.execute("UPDATE suite_promotions SET status='rejected',decision_note=?,updated_at=? WHERE id=?",(f"Abgelehnt von {ctx.user.get('global_name','')}",iso(),pid));audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),p["target_id"],p["target_name"],"promotion_rejected","Antrag abgelehnt")
        else:
            if any(str(x.get("id"))==str(ctx.user["id"]) for x in a):raise HTTPException(400,"Du hast bereits freigegeben")
            a.append({"id":ctx.user["id"],"name":ctx.user.get("global_name",""),"at":iso()})
            if len(a)>=int(p["required_approvals"]):
                m=ctx.guild.get_member(int(p["target_id"]));role=ctx.guild.get_role(int(p["target_role_id"]))
                if not m or not role or role.position>=ctx.user.top_role.position:raise HTTPException(403,"Rollenhierarchie blockiert.")
                await m.add_roles(role,reason="Pulse Promotion "+pid)
                with cx() as c:c.execute("UPDATE suite_promotions SET approvals_json=?,status='approved',updated_at=? WHERE id=?",(json.dumps(a,ensure_ascii=False),iso(),pid))
                audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),m.id,m.display_name,"promote",f'{p["current_role"]} → {role.name} · Antrag {pid}')
            else:
                with cx() as c:c.execute("UPDATE suite_promotions SET approvals_json=?,updated_at=? WHERE id=?",(json.dumps(a,ensure_ascii=False),iso(),pid))
                audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),p["target_id"],p["target_name"],"promotion_approval",f'{len(a)}/{p["required_approvals"]}')
        return RedirectResponse("/suite/promotions",303)

    @app.get("/suite/training",response_class=HTMLResponse)
    async def training_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);ts=db.training_list();ms=members(ctx.guild,webserver)
        with cx() as c:aa=[dict(x) for x in c.execute("SELECT * FROM suite_training_assignments ORDER BY assigned_at DESC LIMIT 500").fetchall()]
        opts="".join(f'<option value="{m.id}">{esc(m.display_name)}</option>' for m in ms);topts="".join(f'<option value="{esc(t["id"])}">{esc(t["title"])}</option>' for t in ts);cards=''.join(f'<div class=card><h3>{esc(t["title"])}</h3><div class=tiny>{esc(t.get("description"))}</div><div class=metric>{t.get("passing",0)}%</div><span class="badge blue">{len(t.get("questions",[])) if "questions" in t else "MC"} Fragen</span></div>' for t in ts[:40]);assigns=''.join(f'<div class=row><span>{esc(a["user_name"])}<div class=tiny>{esc(a["training_id"])}</div></span><span class=badge>{esc(a["status"])}{" · Zertifikat" if a["status"]=="passed" else ""}</span></div>' for a in aa[:80])
        body=f'<div class="grid g2"><div class=card><h3>🎯 Schulung zuweisen</h3><form method=post action=/suite/training/assign><select class=select name=training_id>{topts}</select><select class=select name=user_id style="margin-top:8px">{opts}</select><button class="btn primary" style="margin-top:8px">Zuweisen</button></form></div><div class=card><h3>📜 Ausbildungsfortschritt</h3>{assigns or "Keine Zuweisungen."}</div></div><div class="grid g3" style="margin-top:13px">{cards or "Keine Kurse."}</div>'
        return page(ctx,"training","Schulungszentrum 2.0","Kurse · Multiple Choice · Mindestpunktzahl · Wiederholungen · Zertifikate",body)

    @app.post("/suite/training/assign")
    async def training_assign(request:Request,training_id:str=Form(...),user_id:int=Form(...),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);m=ctx.guild.get_member(user_id);t=db.get_training(training_id)
        if not m or not t:raise HTTPException(404,"Nicht gefunden")
        aid="asg_"+uuid.uuid4().hex[:10]
        with cx() as c:c.execute("INSERT INTO suite_training_assignments VALUES(?,?,?,?,?,?,?,?)",(aid,training_id,str(m.id),m.display_name,"assigned",str(ctx.user["id"]),ctx.user.get("global_name",""),iso()))
        db.notify(m.id,"🎓 Schulung zugewiesen",t["title"],"info","/suite/training",f"training:{aid}",86400);audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),m.id,m.display_name,"training_assigned",t["title"]);return RedirectResponse("/suite/training",303)

    @app.get("/suite/tasks",response_class=HTMLResponse)
    async def tasks_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);ts=db.list_tasks(limit=3000,include_archived=True);groups={x:[] for x in ["backlog","open","in_progress","control","done"]}
        with cx() as c:meta={str(x["task_id"]):dict(x) for x in c.execute("SELECT * FROM suite_task_meta").fetchall()}
        for t in ts:
            st=meta.get(str(t["id"]),{}).get("board_status") or ("done" if t.get("status") in {"done","archived"} else t.get("status") or "open");groups.setdefault(st,groups["open"]).append(t)
        cols=[]
        for st,label in [("backlog","Backlog"),("open","Offen"),("in_progress","In Arbeit"),("control","Kontrolle"),("done","Fertig")]:
            cols.append(f'<div class=col><b>{label}</b>{"".join(f"<div class=notice><a href=/suite/task/{t["id"]}><b>{esc(t["title"])}</b></a><div class=tiny>{esc(t.get("priority"))} · {esc(t.get("due_at") or "ohne Deadline")}</div></div>" for t in groups[st][:40]) or "Leer"}</div>')
        return page(ctx,"tasks","Aufgabenmanagement 2.0","Backlog → Offen → In Arbeit → Kontrolle → Fertig · Checklisten, Kommentare, Bearbeiter und Anhänge",f'<div class=kan>{"".join(cols)}</div>')

    @app.get("/suite/task/{task_id}",response_class=HTMLResponse)
    async def task_detail(request:Request,task_id:str,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);t=db.get_task(task_id)
        if not t:raise HTTPException(404,"Aufgabe nicht gefunden")
        with cx() as c:r=c.execute("SELECT * FROM suite_task_meta WHERE task_id=?",(task_id,)).fetchone()
        m=dict(r) if r else {"checklist_json":"[]","comments_json":"[]","attachments_json":"[]"};checks=json.loads(m["checklist_json"]);comments=json.loads(m["comments_json"]);atts=json.loads(m["attachments_json"])
        body=f'<div class="grid g2"><div class=card><h3>{esc(t["title"])}</h3><div class=tiny>{esc(t.get("description"))}</div><p>Priorität: <b>{esc(t.get("priority"))}</b> · Deadline: <b>{esc(t.get("due_at") or "—")}</b></p><form method=post action=/suite/tasks/{task_id}/move><select class=select name=status><option>backlog</option><option>open</option><option>in_progress</option><option>control</option><option>done</option></select><button class="btn">Status speichern</button></form><form method=post action=/suite/task/{task_id}/meta><input type=hidden name=mode value=comment><textarea class=ta name=value placeholder="Kommentar …"></textarea><button class=btn>Kommentar</button></form><form method=post action=/suite/task/{task_id}/meta style="margin-top:8px"><input type=hidden name=mode value=check><input class=input name=value placeholder="Checklistenpunkt …"><button class=btn>Hinzufügen</button></form><form method=post action=/suite/task/{task_id}/meta style="margin-top:8px"><input type=hidden name=mode value=attachment><input class=input name=value placeholder="https://…"><button class=btn>Anhang</button></form></div><div class=card><h3>✅ Checkliste</h3>{''.join(f'<div class=row>{"✅" if x.get("done") else "⬜"} {esc(x.get("text"))}</div>' for x in checks) or "Leer"}<h3>💬 Kommentare</h3>{''.join(f'<div class=notice><b>{esc(x.get("by"))}</b><div class=tiny>{esc(x.get("at"))}</div>{esc(x.get("text"))}</div>' for x in comments) or "Leer"}<h3>📎 Anhänge</h3>{''.join(f'<div class=notice><a target=_blank href="{esc(x.get("url"))}">{esc(x.get("url"))}</a></div>' for x in atts) or "Keine"}</div></div>'
        return page(ctx,"tasks",f"Aufgabe · {t['title']}","Zusammenarbeit, Checklisten, Kommentare und Anhänge",body)

    @app.post("/suite/task/{task_id}/meta")
    async def task_meta(request:Request,task_id:str,mode:str=Form(...),value:str=Form(""),user_session:str=Cookie(None)):
        ctx=auth(request,user_session);t=db.get_task(task_id)
        if not t:raise HTTPException(404,"Aufgabe nicht gefunden")
        with cx() as c:
            c.execute("INSERT OR IGNORE INTO suite_task_meta(task_id,updated_at) VALUES(?,?)",(task_id,iso()));r=dict(c.execute("SELECT * FROM suite_task_meta WHERE task_id=?",(task_id,)).fetchone());key={"comment":"comments_json","check":"checklist_json","attachment":"attachments_json"}.get(mode)
            if key:
                a=json.loads(r[key] or "[]")
                if mode=="comment":a.append({"by":ctx.user.get("global_name",""),"at":iso(),"text":value[:1500]})
                elif mode=="check":a.append({"text":value[:200],"done":False})
                elif value.startswith(("https://","http://")):a.append({"url":value[:1000]})
                c.execute(f"UPDATE suite_task_meta SET {key}=?,updated_at=? WHERE task_id=?",(json.dumps(a,ensure_ascii=False),iso(),task_id))
        audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),task_id,t["title"],"task_meta_updated",mode);return RedirectResponse(f"/suite/task/{task_id}",303)

    @app.post("/suite/tasks/{task_id}/move")
    async def task_move(request:Request,task_id:str,status:str=Form(...),user_session:str=Cookie(None)):
        ctx=auth(request,user_session);t=db.get_task(task_id)
        if not t or status not in {"backlog","open","in_progress","control","done"}:raise HTTPException(400,"Ungültig")
        st="done" if status=="done" else "in_progress" if status in {"in_progress","control"} else "open";db.update_task(task_id,status=st,actor_id=ctx.user["id"],actor_name=ctx.user.get("global_name",""),note=f"Board: {status}")
        with cx() as c:c.execute("INSERT OR IGNORE INTO suite_task_meta(task_id,updated_at) VALUES(?,?)",(task_id,iso()));c.execute("UPDATE suite_task_meta SET board_status=?,updated_at=? WHERE task_id=?",(status,iso(),task_id))
        audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),task_id,t["title"],"task_moved",status);return RedirectResponse("/suite/tasks",303)

    @app.get("/suite/news",response_class=HTMLResponse)
    async def news_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);rows=db.announcements(200);body='<div class="grid g2"><div class=card><form method=post action=/suite/news/create><input class=input name=title placeholder="Titel" required><select class=select name=kind style="margin-top:8px"><option>info</option><option>warning</option><option>urgent</option><option>success</option></select><textarea class=ta name=content style="margin-top:8px" placeholder="Nachricht …" required></textarea><button class="btn primary" style="margin-top:8px">Veröffentlichen + Discord</button></form></div><div class=card><h3>📚 Pinnwand</h3>'+''.join(f'<div class=notice><b>{esc(x.get("title"))}</b><div class=tiny>{esc(x.get("created_at"))} · {esc(x.get("author_name"))}</div><div>{esc(x.get("content"))}</div></div>' for x in rows)+'</div></div>';return page(ctx,"news","Team-Kommunikationszentrale","News · Wichtig · Dringend · Erfolge · Informationen",body)

    @app.post("/suite/news/create")
    async def news_create(request:Request,title:str=Form(...),kind:str=Form(...),content:str=Form(...),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);aid=db.create_announcement(title[:160],content[:4000],ctx.user["id"],ctx.user.get("global_name",""),kind);color=_pulse_color("success") if kind=="success" else _pulse_color("danger") if kind=="urgent" else _pulse_color("warning") if kind=="warning" else _pulse_color("info");await webserver.send_team_update_embed(ctx.guild,title,content,color,actor=ctx.user.get("global_name",""),action="Team-News");audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),aid,title,"announcement_created",kind);return RedirectResponse("/suite/news",303)

    @app.get("/suite/audit",response_class=HTMLResponse)
    async def audit_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True)
        import pulse_lists  # U-05: pages instead of up to 1,500 entries at once, CSV export
        ok,count=audit_ok()
        q=request.query_params.get("q","").lower()
        sp=pulse_lists.params(request.query_params); lp=pulse_lists.params(request.query_params,"l")
        rows,meta=pulse_lists.paginate(audit_rows(q),sp["page"],sp["per"])
        listing=''.join(
            f'<div class=event><b>{esc(x["occurred_at"])}</b> · <b>{esc(x["action"])}</b>'
            f'<div class=tiny>👤 {esc(x["target_name"])} · 🛡 {esc(x["actor_name"])} · 🆔 {esc(x["request_id"])}</div>'
            f'<div>{esc(x["details"])}</div></div>'
            for x in rows
        )

        # Several legacy dashboard routes write to audit_logs.json while newer
        # modules use the hash-chained suite_audit table. Show both stores so
        # older management actions are not hidden from the central Audit Center.
        legacy_rows,lmeta=pulse_lists.paginate(legacy_audit_rows(q),lp["page"],lp["per"])
        keep={"q":q,"page":meta["page"],"lpage":lmeta["page"]}
        legacy_listing=''.join(
            f'<div class=event><b>{esc(x.get("timestamp","—"))}</b> · <b>{esc(x.get("action","Aktion"))}</b>'
            f'<div class=tiny>👤 {esc(x.get("actor","Unbekannt"))} · 🆔 {esc(x.get("actor_id","—"))} · Quelle: Legacy-Webpanel</div>'
            f'<div>{esc(x.get("details",""))}</div></div>'
            for x in legacy_rows
        )
        body=(
            f'<div class=card><span class="badge {"ok" if ok else "bad"}">'
            f'{"✅ Suite-Hashkette intakt" if ok else "🚨 Suite-Hashkette: Manipulation erkannt"}</span> '
            f'<span class="badge blue">{count} geprüft</span>'
            f'<form style="margin-top:10px" role="search"><label class="sr-only" for="auditQ">Audit durchsuchen</label><input id="auditQ" class=input name=q value="{esc(q)}" placeholder="Audit, Vorgang, Spieler, Aktion…"></form>'
            f'<a class=btn style="margin-top:8px;display:inline-block" href="/suite/audit.csv{("?q="+quote(q)) if q else ""}">⬇ CSV-Export{" (Suchtreffer)" if q else ""}</a>'
            f'<h3 style="margin-top:14px" id="suite">🔐 Verifizierte Suite-Auditkette</h3>'
            f'<div class=timeline style="margin-top:13px">{listing or "Keine Treffer."}</div>{pulse_lists.pager("/suite/audit",meta,keep,anchor="#suite")}</div>'
            f'<div class=card style="margin-top:13px"><h3 id="legacy">🧾 Zusätzliche ältere Dashboard-Aktionen</h3>'
            f'<div class=tiny>Quelle: audit_logs.json · Diese historischen Einträge besitzen keine Suite-Hashkettenprüfung.</div>'
            f'<div class=timeline style="margin-top:13px">{legacy_listing or "Keine zusätzlichen Treffer."}</div>{pulse_lists.pager("/suite/audit",lmeta,keep,prefix="l",anchor="#legacy")}</div>'
        )
        return page(ctx,"audit","Audit Center 2.0","Verifizierte Suite-Historie und zusätzliche Aktionen aus dem älteren Dashboard",body)

    @app.get("/suite/audit.csv")
    async def audit_csv(request:Request,user_session:str=Cookie(None)):
        """U-05: both audit stores as one CSV (search applies), every cell through csv_safe."""
        ctx=auth(request,user_session,manager=True)
        import pulse_lists
        q=request.query_params.get("q","").lower()
        rows=[(x.get("occurred_at"),"Suite",x.get("action"),x.get("actor_name"),x.get("target_name"),x.get("details"),x.get("request_id")) for x in audit_rows(q)]
        rows+=[(x.get("timestamp"),"Legacy",x.get("action"),x.get("actor"),"",x.get("details"),"") for x in legacy_audit_rows(q)]
        audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),"audit","Audit-Export","audit_exported",q or "alle")
        return pulse_lists.csv_response(f"pulse_audit_{datetime.now().strftime('%Y-%m-%d')}.csv",
                                        ["Zeit","Quelle","Aktion","Ausgeführt von","Betroffen","Details","Vorgang"],rows)

    @app.get("/suite/search",response_class=HTMLResponse)
    async def search_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);q=request.query_params.get("q","");res=search_all(q,ctx);listing=''.join(f'<a class=result href="{esc(x["url"])}"><b>{esc(x["title"])}</b><div class=tiny>{esc(x["type"])} · {esc(x.get("meta",""))}</div></a>' for x in res) or "Noch keine Treffer."
        return page(ctx,"search","Globale Suche","Ctrl+K · Teamler · Warn-ID · Bewerbung · Ticket · Aufgabe · Kalender · Schulung · Audit · Roblox",f'<div class=card><form><input class=input name=q value="{esc(q)}" placeholder="Alles durchsuchen…"></form><div style="margin-top:10px">{listing}</div></div>')

    @app.get("/suite/api/search")
    async def search_api(request:Request,q:str="",user_session:str=Cookie(None)):
        ctx=auth(request,user_session);return JSONResponse({"results":search_all(q,ctx)})

    @app.get("/suite/cases",response_class=HTMLResponse)
    async def cases_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True)
        with cx() as c:rows=[dict(x) for x in c.execute("SELECT * FROM suite_cases ORDER BY created_at DESC LIMIT 300").fetchall()]
        opts=''.join(f'<option value="{m.id}">{esc(m.display_name)}</option>' for m in managers(ctx.guild,webserver));cards=''.join(f'<div class=card><div class=row><b>{esc(x["id"])}</b><span class=badge>{esc(x["status"])}</span></div><div class=tiny>{esc(x["category"])} · {esc(x["created_at"])} · {esc(x["reporter_name"])}</div><p>{esc(x["description"])}</p><form method=post action="/suite/cases/{x["id"]}/action"><select class=select name=status><option>open</option><option>in_progress</option><option>closed</option></select><select class=select name=assignee_id><option value="">Auto</option>{opts}</select><button class=btn>Speichern</button></form></div>' for x in rows) or "Keine Fälle."
        return page(ctx,"cases","Meldungen / Modmail","Interne Fälle mit Bearbeiter, Priorität und Status",cards)

    @app.post("/suite/cases/{case_id}/action")
    async def case_action(request:Request,case_id:str,status:str=Form(...),assignee_id:str=Form(""),user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);m=ctx.guild.get_member(int(assignee_id)) if assignee_id.isdigit() else None
        if status not in {"open","in_progress","closed"}:raise HTTPException(400,"Ungültig")
        with cx() as c:c.execute("UPDATE suite_cases SET status=?,assignee_id=?,assignee_name=?,updated_at=? WHERE id=?",(status,str(m.id) if m else "",m.display_name if m else "",iso(),case_id))
        audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),case_id,case_id,"case_updated",f"{status} · {m.display_name if m else 'Auto'}");return RedirectResponse("/suite/cases",303)

    @app.get("/suite/achievements",response_class=HTMLResponse)
    async def achievements_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);web=webserver;cards=[]
        for m in members(ctx.guild,web):cards.append(f'<div class=card><h3>{esc(m.display_name)}</h3><div>{achievement_text(m,web)}</div></div>')
        return page(ctx,"ach","Team-Erfolge","100 Tickets · 50 Dienststunden · 30 Bewerbungen · Warnfreiheit",f'<div class="grid g3">{"".join(cards)}</div>')

    _ROUTES=True

def appname(aid,item):return str(item.get("name") or item.get("discord_name") or item.get("username") or aid)

def _promos(status=None):
    with cx() as c:
        q="SELECT * FROM suite_promotions";args=[]
        if status:q+=" WHERE status=?";args.append(status)
        q+=" ORDER BY created_at DESC";return [dict(x) for x in c.execute(q,args).fetchall()]

def search_all(q,ctx):
    q=(q or "").lower().strip();web=__import__("webserver");out=[]
    if not q:return out
    for m in members(ctx.guild,web):
        if q in m.display_name.lower() or q in str(m.id):out.append({"type":"Teammitglied","title":m.display_name,"meta":str(m.id),"url":f"/suite/member/{m.id}"})
    td=web.load_json(web.DATA_FILE,{})
    for uid,e in td.items():
        # E-3: warnings (and their reasons) only for the person themself and can_warn/admins.
        if isinstance(e,dict) and web.can_see_warnings(ctx.perms,ctx.user.get("id"),uid):
            for w in e.get("warns_list",[]):
                if q in str(w).lower() or q in str(w.get("id","")).lower():out.append({"type":"Warnung","title":str(w.get("id","Warn")),"meta":str(w.get("reason","")),"url":f"/suite/member/{uid}"})
    apps=web.load_json(web.APPS_FILE,{})
    for aid,a in apps.items():
        if q in json.dumps(a,ensure_ascii=False).lower():out.append({"type":"Bewerbung","title":appname(aid,a),"meta":aid,"url":f"/suite/applications/{aid}"})
    for t in db.list_tickets(limit=3000):
        if q in json.dumps(t,ensure_ascii=False).lower():out.append({"type":"Ticket","title":f"Ticket {t.get('id')}","meta":str(t.get('category','')),"url":"/tickets"})
    for t in db.list_tasks(limit=3000,include_archived=True):
        if q in json.dumps(t,ensure_ascii=False).lower():out.append({"type":"Aufgabe","title":t.get("title","Aufgabe"),"meta":t.get("id"),"url":"/suite/tasks"})
    with cx() as c:
        for table,typ,col,url in [("suite_events","Kalender","title","/suite/calendar"),("suite_audit","Audit","action","/suite/audit"),("suite_interviews","Gespräch","applicant_name","/suite/interviews"),("suite_cases","Fall","id","/suite/cases")]:
            for r in c.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 500").fetchall():
                d=dict(r)
                if q in json.dumps(d,ensure_ascii=False).lower():out.append({"type":typ,"title":str(d.get(col,"")),"meta":str(d.get("request_id") or d.get("id") or ""),"url":url})
    for x in web.load_json(web.LOGS_FILE,[]):
        if q in json.dumps(x,ensure_ascii=False).lower():out.append({"type":"Roblox/MOD","title":str(x.get("target_user","Spieler")),"meta":str(x.get("roblox_id","N/A")),"url":"/melonly"})
    seen=set();res=[]
    for x in out:
        k=(x["type"],x["title"],x.get("meta"))
        if k not in seen:seen.add(k);res.append(x)
    return res[:80]

def achievement_text(m,web):
    tickets=sum(str(t.get("claimed_by_id"))==str(m.id) and t.get("status")=="closed" for t in db.list_tickets(limit=3000));hours=total_time(m,web)/3600;apps=sum(str(a.get("processor_id"))==str(m.id) and str(a.get("status")) in {"accepted","rejected"} for a in web.load_json(web.APPS_FILE,{}).values());clean=not warns(m,web)
    items=[("🏆","100 Tickets",tickets>=100),("🔥","50 Dienststunden",hours>=50),("⭐","30 Bewerbungen",apps>=30),("🛡","0 aktive Warnungen",clean)]
    with cx() as c:
        for key,name,ok in [("tickets",x[1],x[2]) for x in items]:
            if ok:c.execute("INSERT OR IGNORE INTO suite_achievements VALUES(?,?,?)",(key,str(m.id),iso()))
    return " · ".join(("✅ " if ok else "⏳ ")+em+" "+name for em,name,ok in items)

class _AnsweredResponse:
    """K-09: the click is answered at once; later `response.*` calls of a callback edit that answer instead."""
    def __init__(self,i):self._i=i
    def is_done(self):return True
    async def edit_message(self,**kwargs):return await self._i.edit_original_response(**kwargs)
    async def send_message(self,content=None,**kwargs):
        kwargs.pop("ephemeral",None);return await self._i.followup.send(content,ephemeral=True,**kwargs)
    async def defer(self,**_):return None

class _AnsweredInteraction:
    def __init__(self,i):self._i=i;self.response=_AnsweredResponse(i)
    def __getattr__(self,name):return getattr(self._i,name)

class Confirm(PulseView):
    def __init__(self,owner,callback,key=None,origin=None):
        super().__init__(timeout=45);self.owner=owner;self.cb=callback;self.key=key;self.origin=origin;self.used=False
    async def on_timeout(self):
        # K-09: expired confirmations say so instead of leaving clickable buttons that fail.
        if self.used or self.origin is None:return
        try:await self.origin.edit_original_response(content="⌛ Bestätigung abgelaufen – es wurde nichts ausgeführt.",view=None)
        except (discord.HTTPException,AttributeError):pass
    @discord.ui.button(label="Bestätigen",style=discord.ButtonStyle.danger,emoji="✅")
    async def yes(self,i,b):
        if i.user.id!=self.owner:return await i.response.send_message("Nur der Ersteller kann bestätigen.",ephemeral=True)
        if self.used:return await i.response.send_message("⏳ Wird bereits ausgeführt.",ephemeral=True)
        self.used=True;self.stop()
        if self.key and (locked() or not allowed(i,self.key,__import__("webserver"))):
            return await i.response.edit_message(content="❌ Abgebrochen: Berechtigung fehlt oder Pulse Emergency Lock ist aktiv.",embed=None,view=None)
        await i.response.edit_message(content="⏳ Wird ausgeführt …",embed=None,view=None)
        try:await self.cb(_AnsweredInteraction(i))
        except Exception as e:
            error_id=uuid.uuid4().hex[:6].upper();print(f"❌ Pulse Suite: Bestätigung fehlgeschlagen [Fehler-ID {error_id}]: {type(e).__name__}: {e}")
            try:await i.edit_original_response(content=f"❌ Dabei ist ein Fehler aufgetreten. Fehler-ID **{error_id}** – das Team findet sie im Log.")
            except discord.HTTPException:pass
    @discord.ui.button(label="Abbrechen",style=discord.ButtonStyle.secondary,emoji="❌")
    async def no(self,i,b):
        if i.user.id!=self.owner:return await i.response.send_message("Nur der Ersteller kann abbrechen.",ephemeral=True)
        if self.used:return await i.response.send_message("⏳ Wird bereits ausgeführt.",ephemeral=True)
        self.used=True;await i.response.edit_message(content="❌ Abgebrochen.",embed=None,view=None);self.stop()

async def suite_ticket_action(i,action,tid):
    web=__import__("webserver")
    if action=="claim":
        if not allowed(i,"pulse-ticket",web):return await i.response.send_message("❌ Keine Berechtigung.",ephemeral=True)
        db.claim_ticket(tid,i.user.id,i.user.display_name);audit(i.guild.id,i.user.id,i.user.display_name,tid,"","ticket_claimed","Ticket übernommen");await i.response.send_message("✅ Ticket übernommen.",ephemeral=True)
    elif action=="close":
        if not allowed(i,"pulse-ticket",web):return await i.response.send_message("❌ Keine Berechtigung.",ephemeral=True)
        db.close_ticket(tid,"Per Discord geschlossen",closed_by_id=i.user.id,closed_by_name=i.user.display_name);audit(i.guild.id,i.user.id,i.user.display_name,tid,"","ticket_closed","Ticket geschlossen");await i.response.send_message("✅ Ticket geschlossen.",ephemeral=True)
    elif action=="transfer":
        ms=[m for m in managers(i.guild,web) if m.id!=i.user.id]
        if not allowed(i,"pulse-ticket",web) or not ms:return await i.response.send_message("❌ Keine Berechtigung / keine weitere Führung.",ephemeral=True)
        m=min(ms,key=lambda x:sum(t.get("status")!="closed" for t in db.list_tickets(limit=3000,claimed_by_id=x.id)));db.claim_ticket(tid,m.id,m.display_name);db.notify(m.id,"👤 Ticket übergeben",f"{tid} wurde dir zugewiesen.","info","/tickets",f"transfer:{tid}:{m.id}",21600);audit(i.guild.id,i.user.id,i.user.display_name,tid,"","ticket_transferred",m.display_name);await i.response.send_message("✅ Ticket übergeben.",ephemeral=True)

# K-07: the case ID lives in the custom_id, so the buttons keep working after a restart.
SUITE_TICKET_BUTTONS={"claim":("Ticket übernehmen",discord.ButtonStyle.success,"🎫"),"close":("Schließen",discord.ButtonStyle.danger,"🔒"),"transfer":("Übergeben",discord.ButtonStyle.secondary,"👤")}
SUITE_TICKET_LABEL_ACTIONS={label:action for action,(label,_,_) in SUITE_TICKET_BUTTONS.items()}
SUITE_TICKET_TITLE="🎫 Fall "

class SuiteTicketButton(discord.ui.DynamicItem[discord.ui.Button],template=r"pulse:suite-ticket:(?P<action>claim|close|transfer):(?P<tid>[A-Za-z0-9_-]{1,60})"):
    def __init__(self,action,tid):
        label,style,emoji=SUITE_TICKET_BUTTONS[action]
        super().__init__(discord.ui.Button(label=label,style=style,emoji=emoji,custom_id=f"pulse:suite-ticket:{action}:{tid}"))
        self.action=action;self.tid=tid
    @classmethod
    async def from_custom_id(cls,interaction,item,match):return cls(match["action"],match["tid"])
    async def callback(self,i):await suite_ticket_action(i,self.action,self.tid)

class TicketView(PulseView):
    def __init__(self,tid):
        super().__init__(timeout=None);self.tid=tid
        for action in SUITE_TICKET_BUTTONS:self.add_item(SuiteTicketButton(action,tid))

def legacy_suite_ticket_action(message,custom_id):
    """(action, case id) for a button of a case posted before K-07 (random custom_id), else None."""
    embeds=getattr(message,"embeds",None) or []
    title=str(getattr(embeds[0],"title","") or "") if embeds else ""
    if not title.startswith(SUITE_TICKET_TITLE) or str(custom_id).startswith("pulse:"):return None
    for row in getattr(message,"components",None) or []:
        for child in getattr(row,"children",None) or []:
            if getattr(child,"custom_id",None)==custom_id and getattr(child,"label",None) in SUITE_TICKET_LABEL_ACTIONS:
                return SUITE_TICKET_LABEL_ACTIONS[child.label],title[len(SUITE_TICKET_TITLE):].strip()
    return None

class PulseSuite(commands.Cog):
    def __init__(self,bot):self.bot=bot;init_db();seed();register_routes();self.auto.start()

    async def cog_load(self):
        self.bot.add_dynamic_items(SuiteTicketButton)

    async def cog_unload(self):
        self.bot.remove_dynamic_items(SuiteTicketButton)

    @commands.Cog.listener()
    async def on_interaction(self,i):
        # K-07 fallback: case messages from before the update carry random custom_ids that no view knows after a restart.
        if i.type!=discord.InteractionType.component or i.message is None or getattr(i.message.author,"id",None)!=getattr(self.bot.user,"id",None):return
        legacy=legacy_suite_ticket_action(i.message,(i.data or {}).get("custom_id",""))
        if not legacy:return
        try:await suite_ticket_action(i,*legacy)
        except discord.HTTPException as e:print("⚠️ Pulse Suite: alter Fall-Button fehlgeschlagen:",type(e).__name__)

    def guild(self):return self.bot.get_guild(GUILD_ID) if GUILD_ID else (self.bot.guilds[0] if self.bot.guilds else None)

    @tasks.loop(minutes=5)
    @tracked("Suite-Automatik (5 min)",300)  # P-06
    async def auto(self):
        g=self.guild()
        if not g:return
        try:
            await self.sync_members(g);await self.warn_sync(g);await self.reminders(g);await self.activity_reminder(g);await self.long_shift_check(g);await self.inactivity(g);await self.reports(g);await self.shift_reminders(g);await self.update_presence(g)
        except Exception as e:print("⚠️ Pulse Suite:",e)

    @auto.before_loop
    async def before_auto(self):await self.bot.wait_until_ready()

    async def sync_members(self,g):
        web=__import__("webserver");ids={int(x) for x in web.load_config().get("team_role_ids",[])}
        stamp=iso()
        rows=[]
        for m in g.members:
            if not m.bot and any(r.id in ids for r in m.roles):
                rows.append((str(m.id), stamp, (now()+timedelta(days=PROBATION_DAYS)).isoformat(timespec="seconds")))
        await asyncio.to_thread(upsert_members_batch, rows)

    async def warn_sync(self,g):
        web=__import__("webserver")
        try:
            r=await web.reconcile_warning_roles(g,web.load_config())
            if r.get("failed"):print("⚠️ Warnrollen:",r)
        except Exception as e:print("⚠️ Warnrollensync:",e)

    async def reminders(self,g):
        web=__import__("webserver");n=now()
        # Resolve role permissions once per pass instead of once per stale application/promotion.
        manager_cache=managers(g,web)
        for t in db.list_tasks(limit=1000):
            d=local(t.get("due_at"))
            if d and d<=n and t.get("status") not in {"done","archived"} and t.get("assignee_id"):db.notify(t["assignee_id"],"⏰ Aufgabe überfällig",f'{t["title"]} ist überfällig.',"warning","/suite/tasks",f"overdue:{t['id']}",21600)
        for aid,a in web.load_json(web.APPS_FILE,{}).items():
            d=local(a.get("created_at"))
            if d and (n-d).total_seconds()>172800:
                for m in manager_cache:db.notify(m.id,"📝 Bewerbung wartet",f"{appname(aid,a)} wartet seit über 48h.","warning","/suite/applications",f"staleapp:{aid}",86400)
        for p in _promos():
            if p["status"] in {"pending","revision"}:
                for m in managers(g,web):db.notify(m.id,"📈 Beförderung wartet",f'{p["target_name"]} wartet auf eine Freigabe.',"warning","/suite/promotions",f"promo:{p['id']}",86400)

    async def activity_reminder(self,g):
        path=os.path.join(BASE,"activity_check.db")
        if not os.path.exists(path):return
        try:
            with closing(sqlite3.connect(path)) as c, c:
                c.row_factory=sqlite3.Row;x=c.execute("SELECT * FROM checks WHERE guild_id=? ORDER BY id DESC LIMIT 1",(g.id,)).fetchone()
                if not x:return
                created=local(x["created_at"]);age=(now()-created).total_seconds() if created else 0;opened={r[0] for r in c.execute("SELECT user_id FROM check_members WHERE check_id=? AND user_id NOT IN (SELECT user_id FROM responses WHERE check_id=?)",(x["id"],x["id"])).fetchall()}
            if age>=900:
                for uid in opened:db.notify(uid,"⏳ Activity Check offen","Bitte bestätige deinen Activity Check.","warning","/dashboard",f"activity:{x['id']}:{uid}",21600)
            if age>=1800 and opened and not setting("activity_report:"+str(x["id"]),False):
                for m in managers(g,__import__("webserver")):db.notify(m.id,"🚨 Activity Check Abschluss",f"Noch {len(opened)} Teamler offen.","warning","/dashboard",f"activity-report:{x['id']}",21600)
                set_setting("activity_report:"+str(x["id"]),True)
        except Exception:pass

    async def long_shift_check(self,g):
        web=__import__("webserver")
        sh=web.load_shifts()
        manager_cache=managers(g,web)
        for uid,s in sh.get("active_shifts",{}).items():
            elapsed=web.shift_elapsed(s)
            if elapsed>=8*3600 and not setting("long_shift:"+str(uid)+":"+str(s.get("date")),False):
                # Shift records carry no name; resolve it so leaders don't see a raw ID.
                member=g.get_member(int(uid)) if str(uid).isdigit() else None
                name=member.display_name if member else s.get("mod_name",uid)
                for m in manager_cache:
                    db.notify(m.id,"⏱ Ungewöhnlich lange Schicht",f"{name} ist seit {secfmt(elapsed)} im Dienst. Prüfe bei Bedarf die Schicht.","warning","/suite/member/"+str(uid),f"longshift:{uid}:{s.get('date')}",86400)
                set_setting("long_shift:"+str(uid)+":"+str(s.get("date")),True)
                # One friendly DM per shift: most long shifts are simply forgotten clock-outs.
                if member:
                    db.notify(member.id,"⏱ Noch im Dienst?",f"Deine Schicht läuft seit {secfmt(elapsed)}. Vergessen auszustempeln?","warning","/me",f"longshift-self:{uid}:{s.get('date')}",86400)
                    link=f"\n👉 {PUBLIC_BASE_URL}/me" if PUBLIC_BASE_URL else ""
                    # BOT-12: through the DM queue – not at night (quiet hours), bundled with other notes
                    import pulse_notify_prefs
                    if not pulse_notify_prefs.load(member.id).get("dm"):  # with DMs on, notify() above already queued it
                        pulse_notify_prefs.queue_dm(member.id,"⏱ Noch im Dienst?",f"Hey {member.display_name}, deine Pulse-Schicht läuft schon seit **{secfmt(elapsed)}**. Falls du vergessen hast auszustempeln, beende sie mit `/dienst ende` oder in Pulse.{link}","")
            # BOT-03: a second, clear reminder well before the automatic end (inbox; DM per the person's settings, U-11)
            limit_h=int(getattr(web,"AUTO_SHIFT_END_HOURS",24))
            if elapsed>=(limit_h-4)*3600 and not setting("long_shift_late:"+str(uid)+":"+str(s.get("date")),False):
                set_setting("long_shift_late:"+str(uid)+":"+str(s.get("date")),True)
                if str(uid).isdigit():
                    db.notify(int(uid),"⏱ Schicht endet bald automatisch",f"Deine Schicht läuft seit {secfmt(elapsed)} und wird nach {limit_h} h automatisch beendet. Beende sie mit /dienst ende oder im Dashboard.","warning","/me",f"longshift-late:{uid}:{s.get('date')}",86400)

    async def inactivity(self,g):
        # Alle Operationen in dieser Prüfung sind synchron (Discord-Cache,
        # JSON-Dateien und SQLite). In einem Worker ausführen, damit langsame
        # Datenbankzugriffe keinen Discord-Gateway-Heartbeat blockieren.
        await asyncio.to_thread(self._inactivity_sync, g)

    def _inactivity_sync(self,g):
        web=__import__("webserver");n=now();loas=web.get_loas()
        team_members = members(g,web)
        team_managers = managers(g,web)
        for m in team_members:
            sm=member_row(m.id);last=local(sm.get("last_seen") if sm else None) or n;days=(n-last).days
            if days>=INACTIVITY_NOTICE_DAYS and not loas.get(str(m.id),{}).get("active") and not loas.get(m.id,{}).get("active"):
                level="🔴 Inaktiv" if days>=INACTIVITY_WARNING_DAYS else "🟡 Aufmerksamkeit"
                for x in team_managers:db.notify(x.id,"⚠️ Inaktivität",f"{m.display_name}: {days} Tage · {level}.","warning","/suite/member/"+str(m.id),f"inactive:{m.id}:{n.date()}:{level}",86400)
            if sm and sm.get("probation_end") and local(sm["probation_end"])<=n+timedelta(hours=24):
                for x in team_managers:db.notify(x.id,"🎯 Probezeit",f"Probezeit von {m.display_name} endet bald.","info","/suite/member/"+str(m.id),f"probation:{m.id}:{sm['probation_end']}",86400)
            achievement_text(m,web)

    async def reports(self,g):
        web=__import__("webserver");tz=getattr(web,"TZ",None);n=datetime.now(tz) if tz else now()
        if n.hour>=REPORT_HOUR and setting("last_daily","")!=n.date().isoformat():await self.daily(g);set_setting("last_daily",n.date().isoformat())
        week=n.isocalendar().week
        if n.weekday()==0 and n.hour>=9 and str(setting("last_weekly",""))!=str(week):await self.weekly_report(g);set_setting("last_weekly",str(week))

    async def shift_reminders(self,g):
        web=__import__("webserver");tz=getattr(web,"TZ",None) or timezone.utc;n=datetime.now(tz)
        with cx() as c:rows=[dict(x) for x in c.execute("SELECT * FROM suite_schedule WHERE guild_id=?",(str(g.id),)).fetchall()]
        for x in rows:
            try:s=datetime.strptime(x["date"]+" "+x["start_time"],"%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except Exception:continue
            if n<=s<=n+timedelta(minutes=30):db.notify(x["assignee_id"],"⏰ Schicht beginnt bald",f'{x["shift_name"]} beginnt um {x["start_time"]}.',"info","/suite/schedule",f"shift30:{x['id']}",86400)

    async def daily(self,g):
        web=__import__("webserver");ms=members(g,web);sh=web.load_shifts();d=now().date().isoformat();sec=sum(web.credited_shift_seconds(x) for x in sh.get("history",[]) if x.get("date")==d)+sum(web.shift_elapsed(x) for x in sh.get("active_shifts",{}).values() if x.get("date")==d)
        e=discord.Embed(title="📊 Pulse Daily Report",color=_pulse_color("info"));e.add_field(name="👥 Team",value=str(len(ms)));e.add_field(name="🟢 Aktiv",value=str(sum(str(m.status) in {"online","idle","dnd"} for m in ms)));e.add_field(name="⏱ Dienstzeit",value=secfmt(sec));e.add_field(name="🎫 Tickets",value=str(sum(x.get("status")!="closed" for x in db.list_tickets(limit=3000))));e.add_field(name="📝 Bewerbungen",value=str(sum(str(a.get("status","pending")) in {"pending","in_review","interview"} for a in web.load_json(web.APPS_FILE,{}).values())));e.add_field(name="⚠ Warnungen",value=str(sum(len(warns(m,web)) for m in ms)));await self._report(g,e)

    async def weekly_report(self,g):
        # Sent on Monday morning: summarise the last *complete* week (the running week
        # has barely started) and compare it with the week before.
        from pulse_cockpit_utils import previous_full_weeks,seconds_per_member,trend
        web=__import__("webserver");ms=members(g,web);ids={str(m.id) for m in ms}
        today=web.now_de().date();(ls,le),(ps,pe)=previous_full_weeks(today)
        history=web.load_shifts().get("history",[])
        last=seconds_per_member(history,ls,le);prev=seconds_per_member(history,ps,pe)
        total=sum(v for k,v in last.items() if k in ids);total_prev=sum(v for k,v in prev.items() if k in ids)
        active=sum(1 for k,v in last.items() if k in ids and v>0);active_prev=sum(1 for k,v in prev.items() if k in ids and v>0)
        def closed_in(start,end):
            n=0
            for t in db.list_tickets(limit=3000):
                d=local(t.get("closed_at"))
                if d and start<=d.date()<=end:n+=1
            return n
        closed=closed_in(ls,le);closed_prev=closed_in(ps,pe)
        goal=float(web.load_config().get("weekly_goal_hours",setting("weekly_goal_hours",3)) or 0)*3600
        reached=sum(1 for m in ms if goal and last.get(str(m.id),0)>=goal)
        def tr(a,b):arrow,label=trend(a,b);return f"{arrow} {label}"
        e=discord.Embed(title="📈 Team-Wochenbericht",description=f"Woche **{ls.strftime('%d.%m.')} – {le.strftime('%d.%m.%Y')}** im Vergleich zur Vorwoche.",color=_pulse_color("info"))
        e.add_field(name="⏱ Teamstunden",value=f"**{secfmt(total)}**\n{tr(total,total_prev)}")
        e.add_field(name="👥 Aktive Teamler",value=f"**{active}/{len(ms)}**\n{tr(active,active_prev)}")
        e.add_field(name="🎫 Tickets geschlossen",value=f"**{closed}**\n{tr(closed,closed_prev)}")
        e.add_field(name="🎯 Wochenziel erreicht",value=f"**{reached}/{len(ms)}**",inline=True)
        ranked=sorted((m for m in ms if last.get(str(m.id),0)>0),key=lambda m:last.get(str(m.id),0),reverse=True)[:5]
        if ranked:e.add_field(name="🏆 Top 5 der Woche",value="\n".join(f"{['🥇','🥈','🥉','4.','5.'][i]} **{m.display_name}** · {secfmt(last.get(str(m.id),0))}" for i,m in enumerate(ranked)),inline=False)
        inactive=sum(1 for m in ms if not last.get(str(m.id)) and not web.get_loas().get(str(m.id),{}).get("active"))
        if inactive:e.add_field(name="💤 Ohne Schicht",value=f"{inactive} Teamler {'hatte' if inactive==1 else 'hatten'} letzte Woche keine Schicht (Abgemeldete ausgenommen).",inline=False)
        if PUBLIC_BASE_URL:e.add_field(name="🔗 Details",value=f"[Leitungs-Cockpit öffnen]({PUBLIC_BASE_URL}/cockpit)",inline=False)
        e.set_footer(text="Pulse TeamOS · automatischer Wochenbericht")
        await self._report(g,e)

    async def _report(self,g,e):
        cid=int(setting("report_channel_id",0) or 0) or getattr(__import__("webserver"),"TEAM_UPDATE_CHANNEL_ID",0);ch=g.get_channel(cid) if cid else None
        if ch:
            try:await send_styled(ch, embed=e)
            except Exception:pass

    async def update_presence(self,g):
        import pulse_presence  # BOT-13: one place, at most every 5 min, only when the text changed
        await pulse_presence.update(self.bot)

    async def security_check(self,g):
        web=__import__("webserver");issues=[]
        if not g.me or not g.me.guild_permissions.manage_roles:issues.append("Bot hat keine Rollen verwalten-Berechtigung.")
        if g.me:
            for rid in web.load_config().get("team_role_ids",[]):
                r=g.get_role(int(rid))
                if not r:issues.append("Teamrolle "+str(rid)+" fehlt.")
                elif r.managed or r.is_default():issues.append("Teamrolle "+r.name+" ist nicht verwaltbar.")
                elif r.position>=g.me.top_role.position:issues.append("Teamrolle "+r.name+" steht über/gleich dem Bot.")
        try:
            for r in web.warning_role_health(g,web.load_config()):
                if not r["ok"]:issues.append(f"Warn {r['level']}: {r['detail']}")
        except Exception as e:issues.append("Warnrollentest: "+str(e))
        fp=hashlib.sha256("|".join(sorted(issues)).encode()).hexdigest();old=setting("role_health_fp","")
        if issues and fp!=old:await self._report(g,discord.Embed(title="🛡️ Pulse Rollen-Sicherheitsprüfung",description="\n".join("🔴 "+x for x in issues),color=_pulse_color("danger")));set_setting("role_health_fp",fp)
        elif not issues and old:set_setting("role_health_fp","")

    def safe_target(self,i,m):
        return i.guild and isinstance(m,discord.Member) and not m.bot and m.id!=i.user.id and m.top_role<i.user.top_role

    def _rank_change_error(self, interaction, member, role, role_ids, action):
        if not interaction.guild or not self.safe_target(interaction, member):
            return "Zielmitglied ist nicht in deiner zulässigen Rollen-Hierarchie."
        if role.id not in role_ids or role.is_default() or role.managed:
            return "Bitte wähle eine verwaltbare Team-Rangrolle aus."
        bot_member = interaction.guild.me
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            return "Dem Bot fehlt die Berechtigung „Rollen verwalten“."
        if member.top_role >= bot_member.top_role:
            return "Das Zielmitglied liegt auf oder über der höchsten Bot-Rolle."
        if role.position >= bot_member.top_role.position:
            return "Der Bot kann diese Rangrolle nicht verwalten (Rollenhierarchie prüfen)."
        current_team_roles = [r for r in member.roles if r.id in role_ids and not r.is_default()]
        if any(r.position >= bot_member.top_role.position for r in current_team_roles):
            return "Mindestens eine bestehende Teamrolle liegt auf oder über der Bot-Rolle."
        current_rank = max((r.position for r in current_team_roles), default=0)
        if action == "promote" and role.position <= current_rank:
            return "Die Zielrolle ist nicht höher als der aktuelle Teamrang."
        if action == "demote" and (not current_team_roles or role.position >= current_rank):
            return "Die Zielrolle ist nicht niedriger als der aktuelle Teamrang."
        return ""

    async def _confirm_rank_change(self, interaction, member, role, grund, action, web, role_ids, probation_days=None):
        if interaction.guild:
            member = interaction.guild.get_member(member.id) or member
        error = self._rank_change_error(interaction, member, role, role_ids, action)
        if error or locked() or not allowed(interaction, action, web):
            await interaction.response.edit_message(
                content="❌ Rangwechsel abgebrochen: " + (error or "Berechtigung fehlt oder Pulse Emergency Lock ist aktiv."),
                embed=None,
                view=None,
            )
            return
        old_team_roles = [r for r in member.roles if r.id in role_ids and not r.is_default()]
        old_names = ", ".join(r.name for r in old_team_roles) or "Kein Teamrang"
        planned_ids = set(
            rank_role_ids_after_change(
                [r.id for r in member.roles],
                role_ids,
                role.id,
            )
        )
        desired_roles = [r for r in member.roles if r.id in planned_ids and not r.is_default()]
        if not any(r.id == role.id for r in desired_roles):
            desired_roles.append(role)
        try:
            await member.edit(
                roles=desired_roles,
                reason=f"Pulse {action} · {grund}"[:512],
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.response.edit_message(
                content=f"❌ Rangwechsel fehlgeschlagen ({type(exc).__name__}). Die bestehende Rollenliste wurde nicht absichtlich verändert.",
                embed=None,
                view=None,
            )
            return

        label = "Beförderung" if action == "promote" else "Degradierung"
        audit(
            interaction.guild.id,
            interaction.user.id,
            interaction.user.display_name,
            member.id,
            member.display_name,
            action,
            f"{old_names} → {role.name} · Grund: {grund}",
        )
        # BOT-05: same follow-up as the dashboard – probation (was missing for /promote), DM, team update, audit log.
        previous_role = max(old_team_roles, key=lambda r: r.position) if old_team_roles else None
        probation_note = ""
        try:
            if action == "promote":
                done = await web.finish_promotion(
                    interaction.guild, member=member, previous_role=previous_role, new_role=role,
                    probation_days=probation_days or 7, actor_name=interaction.user.display_name,
                    actor_id=str(interaction.user.id), reason=grund, source="Slash /promote",
                )
                probation_note = f" Probezeit: {done['probation_text']}."
            else:
                await web.finish_demotion(
                    interaction.guild, member=member, previous_role=previous_role, new_role=role,
                    actor_name=interaction.user.display_name, actor_id=str(interaction.user.id), reason=grund,
                    source="Slash /demote",
                )
        except Exception as exc:
            print(f"⚠️ Pulse {label}: Nacharbeiten (Probezeit/DM/Team-Update) fehlgeschlagen ({type(exc).__name__}).")
            probation_note = " ⚠️ Probezeit/Team-Update konnten nicht vollständig gespeichert werden – bitte prüfen."
        await interaction.response.edit_message(
            content=f"✅ {label} ausgeführt: **{member.display_name}** → {role.mention}. Alle anderen Team-Rangrollen wurden ersetzt, übrige Rollen bleiben erhalten.{probation_note}",
            embed=None,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.Cog.listener()
    async def on_ready(self):
        from pulse_ready import first_ready
        if not first_ready(self.bot,"suite-security-check"):return  # P-08
        g=self.guild()
        if g:
            try:await self.security_check(g)
            except Exception as e:print("⚠️ Pulse Security:",e)

    @app_commands.command(name="pulse-suite",description="Öffnet die neue Pulse Operations Suite.")
    async def pulse_suite(self,i):
        web=__import__("webserver")
        if not allowed(i,"pulse-suite",web):return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        await i.response.send_message("⚡ Pulse Operations Suite\n"+(PUBLIC_BASE_URL+"/suite" if PUBLIC_BASE_URL else "/suite"),ephemeral=True)

    @app_commands.command(name="warn",description="Verwarnt ein Teammitglied nach Bestätigung.")
    @app_commands.describe(grund="Pflichtgrund (mind. 5 Zeichen)",dauer_tage="Gültigkeit in Tagen (1–365, Standard 7)",beweis="Link zum Beweis (optional)")
    async def warn_cmd(self,i,member:discord.Member,grund:str,dauer_tage:app_commands.Range[int,1,365]=7,beweis:str=""):
        web=__import__("webserver")
        if not allowed(i,"warn",web) or locked() or not self.safe_target(i,member) or member not in members(i.guild,web):return await i.response.send_message("❌ Warnung nicht erlaubt.",ephemeral=True)
        grund = str(grund or "").strip()
        if len(grund) < 5:
            return await i.response.send_message("❌ Bitte gib einen nachvollziehbaren Pflichtgrund mit mindestens 5 Zeichen an.",ephemeral=True)
        async def do(x):
            # BOT-05: the same domain function as the dashboard (checks, K-04 save, roles, consequences,
            # team update, DM to the person, 5/5 escalation). Before, /warn had its own copy without DM,
            # escalation, duration or proof.
            config=web.load_config()
            actor_perms,_=web.compute_perms(i.guild,i.user.id,config)
            result=await web.issue_team_warning(
                i.guild,actor_member=i.user,actor_name=i.user.display_name,actor_id=str(i.user.id),member=member,
                reason=grund,duration_days=dauer_tage,proof=beweis,config=config,
                actor_is_admin=bool(actor_perms.get("is_admin")),source="Slash /warn",
            )
            if not result.get("stored"):
                return await x.response.edit_message(content="❌ "+result["message"],embed=None,view=None)
            w=result["warn"]
            audit(i.guild.id,i.user.id,i.user.display_name,member.id,member.display_name,"warning_created",w["id"]+" · "+grund)
            prefix="✅ " if result["ok"] else "⚠️ "
            await x.response.edit_message(content=prefix+"Warnung "+w["id"]+": "+result["message"]+" Gültig bis "+w["expires_at"][:10]+".",embed=None,view=None)
        await i.response.send_message(f"⚠️ {member.display_name}\n{grund}\nGültigkeit: {dauer_tage} Tage\n\nWirklich verwarnen?",view=Confirm(i.user.id,do,key="warn",origin=i),ephemeral=True)

    @app_commands.command(name="warns",description="Zeigt aktive Warnungen eines Teammitglieds.")
    async def warns_cmd(self,i,member:discord.Member):
        web=__import__("webserver")
        if not allowed(i,"warn",web):return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        ws=warns(member,web);e=discord.Embed(title=f"⚠ Warnungen · {member.display_name}",description="\n".join(f'{x["id"]} · {x.get("date")} · {x.get("reason")}' for x in ws) or "Keine aktiven Warnungen.",color=_pulse_color("warning"));await i.response.send_message(embed=e,ephemeral=True)

    @app_commands.command(name="warn-remove",description="Zieht eine Warnung nach Bestätigung zurück.")
    async def warn_remove(self,i,member:discord.Member,warn_id:str,grund:str=""):
        if not grund.strip():
            return await i.response.send_message("❌ Bitte gib einen Pflichtgrund für die Rücknahme an.",ephemeral=True)
        web=__import__("webserver")
        td=web.load_json(web.DATA_FILE,{})
        entry=web.user_entry(td,str(member.id))
        warn_entry=next((x for x in entry.get("warns_list",[]) if x.get("id")==warn_id and x.get("active",True)),None)
        if not allowed(i,"warn-remove",web) or locked() or not warn_entry:
            return await i.response.send_message("❌ Warnung nicht gefunden / keine Berechtigung.",ephemeral=True)
        async def do(x):
            # BOT-05: same withdrawal as the dashboard – before, the slash path did not lift the temporary
            # consequences (uprank/IC lock), rewrote the whole team file and posted no team update.
            result=await web.revoke_team_warning(
                i.guild,member=member,warn_id=warn_id,revoke_reason=grund,actor_name=i.user.display_name,
                actor_id=str(i.user.id),config=web.load_config(),source="Slash /warn-remove",
            )
            if not result.get("stored"):
                return await x.response.edit_message(content="❌ "+result["message"],embed=None,view=None)
            audit(i.guild.id,i.user.id,i.user.display_name,member.id,member.display_name,"warning_withdrawn",warn_id+" · "+grund.strip())
            await x.response.edit_message(content=("✅ " if result["ok"] else "⚠️ ")+warn_id+": "+result["message"],embed=None,view=None)
        await i.response.send_message(
            f"Warnung {warn_id} von {member.display_name} wirklich zurückziehen?\nGrund: {grund.strip()}",
            view=Confirm(i.user.id,do,key="warn-remove",origin=i),
            ephemeral=True,
        )

    @app_commands.command(name="kick",description="Server-Kick mit verpflichtendem Grund und Zweitfreigabe.")
    async def kick_cmd(self,i,member:discord.Member,grund:str=""):
        if not grund.strip():
            return await i.response.send_message("❌ Bitte gib einen Pflichtgrund für den Server-Kick an.",ephemeral=True)
        web=__import__("webserver")
        if not allowed(i,"kick",web) or locked() or not self.safe_target(i,member):
            return await i.response.send_message("❌ Aktion nicht erlaubt.",ephemeral=True)
        async def do(x):
            approvers=[m for m in managers(i.guild,web) if m.id!=i.user.id]
            if not approvers:
                return await x.response.edit_message(content="❌ Keine andere berechtigte Führungskraft kann den Vorgang freigeben. Es wurde nichts angelegt.",embed=None,view=None)
            aid=action_record("server-exit",{"target_id":member.id,"reason":grund.strip()[:1000]},i.user)
            for manager in approvers:
                if manager.id!=i.user.id:
                    db.notify(manager.id,"🔐 Zweite Freigabe",f"Server-Kick {aid} für {member.display_name} · /pulse-approve {aid}","warning","/suite/audit","approve:"+aid,21600)
            await x.response.edit_message(content="🔐 Server-Kick "+aid+" vorbereitet. Eine andere berechtigte Führungskraft muss ihn mit /pulse-approve freigeben.",embed=None,view=None)
        await i.response.send_message(
            f"🚨 **Server-Kick**\n{member.display_name} wird nach einer zweiten Freigabe vom Discord-Server entfernt.\nGrund: {grund.strip()}\n\nVorgang vorbereiten?",
            view=Confirm(i.user.id,do,key="kick",origin=i),
            ephemeral=True,
        )

    @app_commands.command(name="team-kick",description="Entfernt alle Rollen außer Bürgerrolle und Ex-Teamler; kein Server-Kick.")
    async def team_kick_cmd(self,i,member:discord.Member,grund:str=""):
        if not grund.strip():
            return await i.response.send_message("❌ Bitte gib einen Pflichtgrund für den Team-Kick an.",ephemeral=True)
        web=__import__("webserver")
        if not allowed(i,"team-kick",web) or locked() or not self.safe_target(i,member):
            return await i.response.send_message("❌ Aktion nicht erlaubt.",ephemeral=True)
        if member not in members(i.guild,web):
            return await i.response.send_message("❌ Das Zielmitglied besitzt aktuell keine konfigurierte Teamrolle.",ephemeral=True)
        async def do(x):
            approvers=[m for m in managers(i.guild,web) if m.id!=i.user.id]
            if not approvers:
                return await x.response.edit_message(content="❌ Keine andere berechtigte Führungskraft kann den Vorgang freigeben. Es wurde nichts angelegt.",embed=None,view=None)
            aid=action_record("team-exit",{"target_id":member.id,"reason":grund.strip()[:1000]},i.user)
            for manager in approvers:
                if manager.id!=i.user.id:
                    db.notify(manager.id,"🔐 Zweite Freigabe",f"Team-Offboarding {aid} für {member.display_name} · /pulse-approve {aid}","warning","/suite/audit","approve:"+aid,21600)
            await x.response.edit_message(content="🔐 Team-Kick "+aid+" vorbereitet. Die Person bleibt auf dem Discord-Server; eine andere berechtigte Führungskraft muss freigeben.",embed=None,view=None)
        await i.response.send_message(
            f"🚪 **Team-Kick**\n{member.display_name} behält nach einer zweiten Freigabe nur Bürgerrolle und Ex-Teamler-Rolle. Alle anderen Rollen werden entfernt; kein Server-Kick.\nGrund: {grund.strip()}\n\nVorgang vorbereiten?",
            view=Confirm(i.user.id,do,key="team-kick",origin=i),
            ephemeral=True,
        )

    @app_commands.command(name="server-exit",description="Server-Kick mit Pflichtgrund und Zweitfreigabe.")
    async def server_exit(self,i,member:discord.Member,grund:str=""):
        if not grund.strip():
            return await i.response.send_message("❌ Bitte gib einen Pflichtgrund für den Server-Kick an.",ephemeral=True)
        web=__import__("webserver")
        if not allowed(i,"server-exit",web) or locked() or not self.safe_target(i,member):
            return await i.response.send_message("❌ Aktion nicht erlaubt.",ephemeral=True)
        async def do(x):
            approvers=[m for m in managers(i.guild,web) if m.id!=i.user.id]
            if not approvers:
                return await x.response.edit_message(content="❌ Keine andere berechtigte Führungskraft kann den Vorgang freigeben. Es wurde nichts angelegt.",embed=None,view=None)
            aid=action_record("server-exit",{"target_id":member.id,"reason":grund.strip()[:1000]},i.user)
            for manager in approvers:
                if manager.id!=i.user.id:
                    db.notify(manager.id,"🔐 Zweite Freigabe",f"Server-Kick {aid} für {member.display_name} · /pulse-approve {aid}","warning","/suite/audit","approve:"+aid,21600)
            await x.response.edit_message(content="🔐 Server-Kick "+aid+" vorbereitet. Eine andere berechtigte Führungskraft muss ihn mit /pulse-approve freigeben.",embed=None,view=None)
        await i.response.send_message(
            f"🚨 **Server-Kick**\n{member.display_name} wird nach einer zweiten Freigabe vom Discord-Server entfernt.\nGrund: {grund.strip()}\n\nVorgang vorbereiten?",
            view=Confirm(i.user.id,do,key="server-exit",origin=i),
            ephemeral=True,
        )

    @app_commands.command(name="team-exit",description="Entfernt bei Zweitfreigabe alle Rollen außer Bürgerrolle und Ex-Teamler; kein Server-Kick.")
    async def team_exit(self,i,member:discord.Member,grund:str=""):
        if not grund.strip():
            return await i.response.send_message("❌ Bitte gib einen Pflichtgrund für das Team-Offboarding an.",ephemeral=True)
        web=__import__("webserver")
        if not allowed(i,"team-exit",web) or locked() or not self.safe_target(i,member):
            return await i.response.send_message("❌ Aktion nicht erlaubt.",ephemeral=True)
        if member not in members(i.guild,web):
            return await i.response.send_message("❌ Das Zielmitglied besitzt aktuell keine konfigurierte Teamrolle.",ephemeral=True)
        async def do(x):
            approvers=[m for m in managers(i.guild,web) if m.id!=i.user.id]
            if not approvers:
                return await x.response.edit_message(content="❌ Keine andere berechtigte Führungskraft kann den Vorgang freigeben. Es wurde nichts angelegt.",embed=None,view=None)
            aid=action_record("team-exit",{"target_id":member.id,"reason":grund.strip()[:1000]},i.user)
            for manager in approvers:
                if manager.id!=i.user.id:
                    db.notify(manager.id,"🔐 Zweite Freigabe",f"Team-Offboarding {aid} · /pulse-approve {aid}","warning","/suite/audit","approve:"+aid,21600)
            await x.response.edit_message(content="🔐 Team-Offboarding "+aid+" vorbereitet. Die Person bleibt auf dem Server; eine andere berechtigte Führungskraft muss freigeben.",embed=None,view=None)
        await i.response.send_message(
            f"🚪 **Team-Offboarding**\n{member.display_name} behält nach einer zweiten Freigabe nur Bürgerrolle und Ex-Teamler-Rolle. Alle anderen Rollen werden entfernt.\nGrund: {grund.strip()}\n\nVorgang vorbereiten?",
            view=Confirm(i.user.id,do,key="team-exit",origin=i),
            ephemeral=True,
        )

    @app_commands.command(name="promote",description="Befördert mit Pflichtgrund, Hierarchieschutz und Bestätigung.")
    @app_commands.describe(probezeit="Pflicht wie im Dashboard: 3 oder 7 Tage",grund="Pflichtgrund")
    @app_commands.choices(probezeit=[app_commands.Choice(name="3 Tage",value=3),app_commands.Choice(name="7 Tage",value=7)])
    async def promote_cmd(self,i,member:discord.Member,rolle:discord.Role,probezeit:app_commands.Choice[int],grund:str=""):
        if not grund.strip():
            return await i.response.send_message("❌ Bitte gib einen Pflichtgrund für die Beförderung an.",ephemeral=True)
        web=__import__("webserver")
        role_ids={int(x) for x in web.load_config().get("team_role_ids",[]) if str(x).isdigit()}
        error=self._rank_change_error(i,member,rolle,role_ids,"promote")
        if not allowed(i,"promote",web) or locked() or error:
            return await i.response.send_message("❌ Beförderung blockiert: "+(error or "Berechtigung fehlt oder Pulse Emergency Lock ist aktiv."),ephemeral=True)
        async def do(x):
            await self._confirm_rank_change(x,member,rolle,grund.strip()[:1000],"promote",web,role_ids,probation_days=probezeit.value)
        await i.response.send_message(
            f"📈 **Beförderung vorbereiten**\n{member.display_name}: **{rolle.name}**\nProbezeit: {probezeit.value} Tage\nGrund: {grund.strip()}\n\nBestehende Team-Rangrollen werden durch den neuen Rang ersetzt. Fortfahren?",
            view=Confirm(i.user.id,do,key="promote",origin=i),
            ephemeral=True,
        )

    @app_commands.command(name="demote",description="Degradiert mit Pflichtgrund, Hierarchieschutz und Bestätigung.")
    async def demote_cmd(self,i,member:discord.Member,rolle:discord.Role,grund:str=""):
        if not grund.strip():
            return await i.response.send_message("❌ Bitte gib einen Pflichtgrund für die Degradierung an.",ephemeral=True)
        web=__import__("webserver")
        role_ids={int(x) for x in web.load_config().get("team_role_ids",[]) if str(x).isdigit()}
        error=self._rank_change_error(i,member,rolle,role_ids,"demote")
        if not allowed(i,"demote",web) or locked() or error:
            return await i.response.send_message("❌ Degradierung blockiert: "+(error or "Berechtigung fehlt oder Pulse Emergency Lock ist aktiv."),ephemeral=True)
        async def do(x):
            await self._confirm_rank_change(x,member,rolle,grund.strip()[:1000],"demote",web,role_ids)
        await i.response.send_message(
            f"📉 **Degradierung vorbereiten**\n{member.display_name}: **{rolle.name}**\nGrund: {grund.strip()}\n\nBestehende Team-Rangrollen werden durch den neuen Rang ersetzt. Fortfahren?",
            view=Confirm(i.user.id,do,key="demote",origin=i),
            ephemeral=True,
        )

    @app_commands.command(name="pulse-approve",description="Führt einen freizugebenden Server-Kick oder Team-Austritt aus.")
    async def approve_cmd(self,i,action_id:str):
        if not i.guild:
            return await i.response.send_message("Dieser Befehl funktioniert nur auf dem Server.",ephemeral=True)
        web=__import__("webserver")
        p,_=web.compute_perms(i.guild,i.user.id,web.load_config())
        if not p.get("can_promote") and not p.get("is_admin"):
            return await i.response.send_message("❌ Nur berechtigte Führungskräfte dürfen freigeben.",ephemeral=True)

        team_role_ids={int(x) for x in web.load_config().get("team_role_ids",[]) if str(x).isdigit()}
        with cx() as c:
            row=c.execute("SELECT * FROM suite_actions WHERE id=?",(action_id,)).fetchone()
            if not row or row["status"]!="awaiting_second" or row["created_by_id"]==str(i.user.id):
                return await i.response.send_message("❌ Kein freigabefähiger Vorgang oder du bist dessen Ersteller.",ephemeral=True)
            try:
                payload=json.loads(row["payload_json"])
                target_id=int(payload["target_id"])
                reason=str(payload.get("reason","")).strip()
            except (ValueError,TypeError,KeyError,json.JSONDecodeError):
                return await i.response.send_message("❌ Der Vorgang enthält ungültige Daten.",ephemeral=True)
            operation=approved_action_operation(str(row["kind"]))
            target=i.guild.get_member(target_id)
            if operation is None or not target or not reason:
                return await i.response.send_message("❌ Vorgang, Zielmitglied oder Pflichtgrund ist ungültig.",ephemeral=True)
            if target.top_role>=i.user.top_role:
                return await i.response.send_message("❌ Zielmitglied liegt auf oder über deiner höchsten Rolle.",ephemeral=True)

            bot_member=i.guild.me
            if bot_member is None:
                return await i.response.send_message("❌ Bot-Mitglied konnte nicht ermittelt werden.",ephemeral=True)
            if target.top_role>=bot_member.top_role:
                return await i.response.send_message("❌ Das Zielmitglied liegt auf oder über der höchsten Bot-Rolle; die Aktion wurde blockiert.",ephemeral=True)
            if operation=="kick_member" and not bot_member.guild_permissions.kick_members:
                return await i.response.send_message("❌ Dem Bot fehlt die Berechtigung „Mitglieder kicken“.",ephemeral=True)
            if operation=="remove_team_roles" and not bot_member.guild_permissions.manage_roles:
                return await i.response.send_message("❌ Dem Bot fehlt die Berechtigung „Rollen verwalten“.",ephemeral=True)
            team_roles=[r for r in target.roles if r.id in team_role_ids and not r.is_default()]
            if operation=="remove_team_roles":
                if not team_roles:
                    return await i.response.send_message("❌ Das Mitglied besitzt keine konfigurierten Teamrollen mehr.",ephemeral=True)
                if any(r.position>=bot_member.top_role.position for r in team_roles):
                    return await i.response.send_message("❌ Mindestens eine Teamrolle liegt auf oder über der Bot-Rolle.",ephemeral=True)

            claimed=c.execute(
                "UPDATE suite_actions SET status='executing',approved_by_id=?,approved_by_name=?,updated_at=? WHERE id=? AND status='awaiting_second'",
                (str(i.user.id),i.user.display_name,iso(),action_id),
            ).rowcount
            if claimed!=1:
                return await i.response.send_message("❌ Dieser Vorgang wird bereits ausgeführt oder wurde schon freigegeben.",ephemeral=True)

        await i.response.defer(ephemeral=True,thinking=True)
        try:
            if operation=="kick_member":
                await target.kick(reason=f"Pulse Server-Kick {action_id} · {reason}"[:512])
                outcome=f"{target.display_name} wurde nach Zweitfreigabe vom Discord-Server gekickt."
            else:
                kick_result = await web.apply_team_kick_role_reset(
                    i.guild,
                    target,
                    web.load_config(),
                    f"Pulse Team-Kick {action_id} · {reason}",
                )
                if not kick_result.get("ok"):
                    failure = str(kick_result.get("message") or "Rollen konnten nicht sicher zurückgesetzt werden.")
                    with cx() as c:
                        c.execute(
                            "UPDATE suite_actions SET status='failed',updated_at=? WHERE id=? AND status='executing'",
                            (iso(), action_id),
                        )
                    audit(i.guild.id, i.user.id, i.user.display_name, target.id, target.display_name,
                          "action_failed", f"{action_id} · {failure}")
                    await i.followup.send("❌ " + failure, ephemeral=True)
                    return
                keep_roles = kick_result.get("keep_roles", [])
                keep_text = ", ".join(role.mention for role in keep_roles) or "Bürgerrolle und Ex-Teamler-Rolle"
                outcome = (
                    f"{target.display_name} wurde aus dem Team entfernt. "
                    f"Alle anderen Rollen wurden entfernt; behalten: {keep_text}. "
                    "Die Person bleibt auf dem Discord-Server."
                )

            with cx() as c:
                c.execute(
                    "UPDATE suite_actions SET status='approved',updated_at=? WHERE id=? AND status='executing'",
                    (iso(),action_id),
                )
            audit(i.guild.id,i.user.id,i.user.display_name,target.id,target.display_name,row["kind"],f"Zweitfreigabe {action_id} · Grund: {reason}")
            await i.followup.send("✅ "+action_id+" ausgeführt. "+outcome,ephemeral=True)
        except Exception as exc:
            with cx() as c:
                c.execute(
                    "UPDATE suite_actions SET status='failed',updated_at=? WHERE id=? AND status='executing'",
                    (iso(),action_id),
                )
            audit(i.guild.id,i.user.id,i.user.display_name,target.id,target.display_name,"action_failed",f"{action_id} · {type(exc).__name__}")
            await i.followup.send(
                f"❌ Ausführung von {action_id} fehlgeschlagen oder Ergebnis unklar ({type(exc).__name__}). Es erfolgt kein automatischer Wiederholungsversuch. Bitte Mitgliedschaft und Rollen prüfen, bevor ein neuer Vorgang angelegt wird.",
                ephemeral=True,
            )

    @app_commands.command(name="team-ping",description="Ping für alle, Führung, Aktive oder Diensthabende.")
    async def team_ping(self,i,scope:str="all",rolle:discord.Role=None):
        web=__import__("webserver")
        if not allowed(i,"team-ping",web):return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        ms=members(i.guild,web)
        if rolle:ms=[m for m in ms if rolle in m.roles]
        if scope=="leadership":ms=managers(i.guild,web)
        elif scope=="active":ms=[m for m in ms if str(m.status) in {"online","idle","dnd"}]
        elif scope=="duty":ms=[m for m in ms if str(m.id) in web.load_shifts().get("active_shifts",{})]
        await i.response.send_message("📣 "+" ".join(m.mention for m in ms[:30]) or "📣 Keine passenden Teamler.")

    @app_commands.command(name="pulse-ticket",description="Erstellt ein internes Pulse-Ticket.")
    async def pulse_ticket(self,i,kategorie:str,beschreibung:str,prioritaet:str="normal"):
        web=__import__("webserver")
        if not allowed(i,"pulse-ticket",web):return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        tid=db.create_ticket(str(i.channel.id),str(i.guild.id),str(i.user.id),i.user.display_name,kategorie[:80],prioritaet);ms=sorted(managers(i.guild,web),key=lambda m:sum(t.get("status")!="closed" for t in db.list_tickets(limit=3000,claimed_by_id=m.id)))
        if ms:db.notify(ms[0].id,"🎫 Ticket zugewiesen",f"{tid} · {kategorie}","warning","/tickets",f"auto:{tid}",21600)
        e=discord.Embed(title=f"🎫 Fall {tid}",description=beschreibung[:3500],color=_pulse_color("info"));e.add_field(name="Kategorie",value=kategorie[:80]);e.add_field(name="Priorität",value=prioritaet);await i.response.send_message(embed=e,view=TicketView(tid));audit(i.guild.id,i.user.id,i.user.display_name,tid,"","ticket_created",kategorie)

    @app_commands.command(name="melden",description="Erstellt einen internen Modmail-/Meldungsfall.")
    async def melden(self,i,kategorie:str,beschreibung:str,prioritaet:str="normal"):
        aid="CASE-"+uuid.uuid4().hex[:4].upper()
        with cx() as c:c.execute("INSERT INTO suite_cases VALUES(?,?,?,?,?,?,?,?,?,?,?)",(aid,str(i.user.id),i.user.display_name,kategorie[:80],beschreibung[:3500],"open",prioritaet,"","",iso(),iso()))
        web=__import__("webserver")
        for m in managers(i.guild,web):db.notify(m.id,"🚨 Neuer Meldefall",f"{aid} · {kategorie}","warning","/suite/cases",f"case:{aid}",86400)
        audit(i.guild.id,i.user.id,i.user.display_name,aid,aid,"case_created",f"{kategorie} · {prioritaet}");await i.response.send_message("✅ Meldung wurde intern an die Führung übergeben.",ephemeral=True)

    @app_commands.command(name="pulse-home",description="Öffnet das Pulse TeamOS Command Center.")
    async def pulse_home(self,i):
        web=__import__("webserver")
        if not allowed(i,"pulse-suite",web):
            return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        if not PUBLIC_BASE_URL:
            return await i.response.send_message("⚠️ PUBLIC_BASE_URL fehlt. Setze die öffentliche Dashboard-URL für Discord-Linkbuttons.",ephemeral=True)
        url=PUBLIC_BASE_URL+"/ultimate"
        v=discord.ui.View(timeout=120)
        v.add_item(discord.ui.Button(label="Command Center öffnen",style=discord.ButtonStyle.link,emoji="⚡",url=url))
        await i.response.send_message("⚡ Pulse TeamOS Command Center",view=v,ephemeral=True)

    @app_commands.command(name="pulse-panel",description="Zeigt das Pulse TeamOS Discord-Control-Panel.")
    async def pulse_panel(self,i):
        web=__import__("webserver")
        if not allowed(i,"pulse-panel",web):return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        if not PUBLIC_BASE_URL:return await i.response.send_message("⚠️ PUBLIC_BASE_URL fehlt. Setze die öffentliche Dashboard-URL für Discord-Linkbuttons.",ephemeral=True)
        v=discord.ui.View(timeout=None)
        for label,path,em in [("Command Center","/ultimate","⚡"),("Führung","/suite","🎯"),("Team","/suite/team","👥"),("Warnungen","/warns","⚠️"),("Tickets","/tickets","🎫"),("Aufgaben","/suite/tasks","📋"),("Bewerbungen","/suite/applications","📝"),("Audit","/suite/audit","🛡")]:v.add_item(discord.ui.Button(label=label,style=discord.ButtonStyle.link,emoji=em,url=PUBLIC_BASE_URL+path))
        await i.response.send_message("⚡ Pulse TeamOS Control Panel",view=v,ephemeral=True)

    @app_commands.command(name="pulse-lock",description="Sperrt kritische Pulse-Teamaktionen.")
    async def pulse_lock(self,i):
        web=__import__("webserver");p,_=web.compute_perms(i.guild,i.user.id,web.load_config())
        if not p.get("is_admin"):return await i.response.send_message("❌ Nur Administratoren.",ephemeral=True)
        set_setting("emergency_lock",True);audit(i.guild.id,i.user.id,i.user.display_name,"","Pulse","emergency_lock","Emergency Lock aktiviert");await i.response.send_message("🚨 Emergency Lock aktiv.",ephemeral=True)

    @app_commands.command(name="pulse-unlock",description="Entsperrt kritische Pulse-Teamaktionen.")
    async def pulse_unlock(self,i):
        web=__import__("webserver");p,_=web.compute_perms(i.guild,i.user.id,web.load_config())
        if not p.get("is_admin"):return await i.response.send_message("❌ Nur Administratoren.",ephemeral=True)
        set_setting("emergency_lock",False);audit(i.guild.id,i.user.id,i.user.display_name,"","Pulse","emergency_unlock","Emergency Lock deaktiviert");await i.response.send_message("✅ Emergency Lock deaktiviert.",ephemeral=True)

    @app_commands.command(name="pulse-permission",description="Ordnet eine zusätzliche Rolle einem Pulse-Befehl zu.")
    async def pulse_permission(self,i,befehl:str,rolle:discord.Role):
        web=__import__("webserver");p,_=web.compute_perms(i.guild,i.user.id,web.load_config())
        if not p.get("is_admin"):return await i.response.send_message("❌ Nur Administratoren.",ephemeral=True)
        r=setting("command_roles",{}) or {};r.setdefault(befehl,[]);r[befehl]=sorted(set(r[befehl]+[rolle.id]));set_setting("command_roles",r);audit(i.guild.id,i.user.id,i.user.display_name,"",befehl,"permission_changed",rolle.name);await i.response.send_message(f"✅ {rolle.mention} darf /{befehl} verwenden.",ephemeral=True)

    @app_commands.command(name="activity-check",description="Startet sofort einen Activity Check 2.0.")
    async def activity_check(self,i):
        web=__import__("webserver")
        if not allowed(i,"activity-check",web):return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        cog=self.bot.get_cog("ActivityCheckCog")
        if not cog:return await i.response.send_message("❌ Activity Check Cog nicht geladen.",ephemeral=True)
        msg=await cog.send_check_message(i.channel);await i.response.send_message("✅ Activity Check gestartet." if msg else "⚠️ Heute existiert bereits einer.",ephemeral=True)

    @commands.Cog.listener()
    async def on_member_update(self,before,after):
        web=__import__("webserver");ids={int(x) for x in web.load_config().get("team_role_ids",[])};bt=any(r.id in ids for r in before.roles);at=any(r.id in ids for r in after.roles)
        if at and not bt:
            upsert_member(after);ts=db.training_list()[:1]
            if ts:
                with cx() as c:
                    if not c.execute("SELECT 1 FROM suite_training_assignments WHERE training_id=? AND user_id=?",(ts[0]["id"],str(after.id))).fetchone():c.execute("INSERT INTO suite_training_assignments VALUES(?,?,?,?,?,?,?,?)",(f"asg_{uuid.uuid4().hex[:10]}",ts[0]["id"],str(after.id),after.display_name,"assigned","0","Pulse Onboarding",iso()))
            try:
                db.create_task("Pulse-Onboarding · "+after.display_name,"Profil prüfen, Rollen/Channels prüfen, erste Schulung abschließen und Teamregeln bestätigen.",str(after.id),after.display_name,"0","Pulse Onboarding")
            except Exception:
                pass
            db.notify(after.id,"🎉 Willkommen im Team",f"Dein Pulse-Onboarding für {after.guild.name} wurde eingerichtet.","info",f"/suite/member/{after.id}",f"onboard:{after.id}",86400)
            try:  # BOT-11: onboarding checklist (roles, channels, training, mentor, probation, rules)
                import pulse_checklists
                pulse_checklists.start(after.id,after.display_name,"onboarding")
            except Exception as exc:
                print(f"⚠️ Onboarding-Checkliste nicht angelegt: {type(exc).__name__}")
            for m in managers(after.guild,web):
                if m.id!=after.id:db.notify(m.id,"👋 Neues Teammitglied",f"{after.display_name} wurde automatisch eingerichtet.","info",f"/suite/member/{after.id}",f"newteam:{after.id}",86400)
        elif bt and not at:
            with cx() as c:c.execute("UPDATE suite_members SET archived_at=?,last_seen=? WHERE user_id=?",(iso(),iso(),str(after.id)))
            ms=managers(after.guild,web);replacement=ms[0] if ms else None
            if replacement:
                for t in db.list_tickets(limit=3000):
                    if str(t.get("claimed_by_id"))==str(after.id) and t.get("status")!="closed":
                        db.claim_ticket(t["id"],replacement.id,replacement.display_name)
            sh=web.load_shifts()
            live=sh.get("active_shifts",{}).pop(str(after.id),None)
            if live:
                live["duration_seconds"]=web.shift_elapsed(live);live["auto_closed"]=True;live["auto_closed_reason"]=web.OFFBOARDING_CLOSE_REASON
                sh.setdefault("history",[]).append(live);web.save_json(web.SHIFTS_FILE,sh)
            for m in ms:db.notify(m.id,"🚪 Offboarding",f"{after.display_name} hat das Team verlassen. Offene Tickets wurden übertragen und die laufende Schicht beendet.","warning","/suite/team",f"offboard:{after.id}",86400)
            try:  # BOT-11: offboarding checklist; what Pulse just did is ticked already
                import pulse_checklists
                pulse_checklists.start(after.id,after.display_name,"offboarding",
                                       {"roles_removed":True,"tickets":bool(replacement),"shift":True})
            except Exception as exc:
                print(f"⚠️ Offboarding-Checkliste nicht angelegt: {type(exc).__name__}")

    @commands.Cog.listener()
    async def on_member_remove(self,member):
        if member.bot:return
        with cx() as c:c.execute("UPDATE suite_members SET archived_at=?,last_seen=? WHERE user_id=?",(iso(),iso(),str(member.id)))
        web=__import__("webserver");ms=managers(member.guild,web);replacement=ms[0] if ms else None
        if replacement:
            for t in db.list_tickets(limit=3000):
                if str(t.get("claimed_by_id"))==str(member.id) and t.get("status")!="closed":db.claim_ticket(t["id"],replacement.id,replacement.display_name)
        for m in ms:db.notify(m.id,"🚪 Teammitglied offline",f"{member.display_name} hat den Server verlassen. Offene Vorgänge prüfen.","warning","/suite/team",f"left:{member.id}",86400)

async def setup(bot):await bot.add_cog(PulseSuite(bot))
