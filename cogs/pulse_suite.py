"""Pulse TeamOS Suite: unified management, recruiting, time, planning, audit, automation and security."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import pulse_db as db

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "pulse_suite.db")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
REPORT_HOUR = 20
PROBATION_DAYS = 14
INACTIVITY_NOTICE_DAYS = 8
INACTIVITY_WARNING_DAYS = 14

CSS = """
:root{--a:#6366f1;--bg:#070b12;--panel:#0e1725;--line:#202c3d;--muted:#94a3b8;--text:#f8fafc}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(900px 500px at 15% -10%,rgba(99,102,241,.22),transparent 60%),linear-gradient(180deg,#060a10,#09111d);color:var(--text);font:14px Inter,system-ui,sans-serif}
a{color:inherit;text-decoration:none}.shell{display:flex;min-height:100vh}.side{position:fixed;inset:0 auto 0 0;width:250px;padding:20px;background:#060a10ed;border-right:1px solid var(--line);backdrop-filter:blur(14px);z-index:5}.brand{font-size:20px;font-weight:900;margin-bottom:18px}.brand small{display:block;color:var(--muted);font-size:11px;margin-top:4px}.nav a{display:block;padding:10px 12px;margin:4px 0;border-radius:11px;color:#cbd5e1;transition:.18s}.nav a:hover,.nav a.active{background:#6366f122;color:#fff;transform:translateX(2px)}
.main{margin-left:250px;width:calc(100% - 250px);padding:26px;max-width:1700px}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:18px}.top h1{margin:0 0 3px;font-size:27px}.muted,.tiny{color:var(--muted)}.tiny{font-size:11px}.grid{display:grid;gap:13px}.g4{grid-template-columns:repeat(4,1fr)}.g3{grid-template-columns:repeat(3,1fr)}.g2{grid-template-columns:repeat(2,1fr)}
.card{background:linear-gradient(180deg,#0f1928f2,#0a1220f2);border:1px solid #ffffff10;border-radius:16px;padding:16px;box-shadow:0 18px 50px #0004;animation:rise .3s ease}.metric{font-size:29px;font-weight:900;margin:6px 0}.row{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:10px 12px;border:1px solid #ffffff0d;border-radius:11px;background:#08111d;margin:7px 0}.badge{display:inline-flex;align-items:center;gap:5px;border-radius:999px;border:1px solid var(--line);padding:4px 8px;font-size:11px;font-weight:800}.ok{color:#bbf7d0;background:#0c2415}.warn{color:#fde68a;background:#2a2110}.bad{color:#fecaca;background:#2b1116}.blue{color:#c7d2fe;background:#17173d}.btn{display:inline-block;border:1px solid var(--line);background:#111b2a;color:#e2e8f0;border-radius:10px;padding:9px 12px;font-weight:800;cursor:pointer}.btn:hover{border-color:#6366f177;transform:translateY(-1px)}.primary{background:var(--a);border-color:transparent;color:white}.danger{background:#2b1116;color:#fecaca}.input,.select,.ta{width:100%;padding:10px 11px;border-radius:10px;border:1px solid #2a394e;background:#08111d;color:#fff;outline:none}.ta{min-height:100px;resize:vertical}.form{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.span2{grid-column:span 2}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:10px;border-bottom:1px solid #ffffff0c;text-align:left;font-size:12px}.scroll{overflow:auto}.kan{display:grid;grid-template-columns:repeat(5,240px);gap:10px;overflow:auto}.col{background:#08111d;border:1px solid var(--line);border-radius:14px;padding:10px;min-height:230px}.notice{border:1px solid var(--line);border-radius:12px;padding:12px;background:#091320;margin:8px 0}.timeline{padding-left:18px;border-left:1px solid #263244}.event{margin-bottom:14px}.search{position:fixed;inset:0;background:#000b;backdrop-filter:blur(8px);display:none;align-items:flex-start;justify-content:center;padding:9vh 16px;z-index:99}.search>div{width:min(820px,100%);background:#0a1220;border:1px solid #334155;border-radius:16px;padding:14px}.result{display:block;padding:10px;border-radius:10px}.result:hover{background:#ffffff08}@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}@media(max-width:1100px){.g4{grid-template-columns:repeat(2,1fr)}.side{width:220px}.main{margin-left:220px;width:calc(100% - 220px)}}@media(max-width:800px){.shell{display:block}.side{position:static;width:100%;border-right:0;border-bottom:1px solid var(--line)}.main{margin:0;width:100%;padding:15px}.g4,.g3,.g2,.form{grid-template-columns:1fr}.span2{grid-column:auto}.top{flex-direction:column}}
"""

def cx():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with cx() as c:
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
    with cx() as c:
        if c.execute("SELECT 1 FROM suite_members WHERE user_id=?",(str(m.id),)).fetchone():c.execute("UPDATE suite_members SET last_seen=?,archived_at=NULL WHERE user_id=?",(iso(),str(m.id)))
        else:c.execute("INSERT INTO suite_members(user_id,joined_at,last_seen,internal_rating,notes,probation_end) VALUES(?,?,?,?,?,?)",(str(m.id),iso(),iso(),80,"",(now()+timedelta(days=PROBATION_DAYS)).isoformat(timespec="seconds")))

def warns(m,web):
    td=web.load_json(web.DATA_FILE,{})
    return web.active_warns(web.user_entry(td,str(m.id)))

def total_time(m,web):
    s=web.load_shifts();total=sum(int(x.get("duration_seconds",0)) for x in s.get("history",[]) if str(x.get("mod_id"))==str(m.id));a=s.get("active_shifts",{}).get(str(m.id))
    return total+(web.shift_elapsed(a) if a else 0)

def weekly(m,web):
    s=web.load_shifts();return web.calculate_weekly_seconds(str(m.id),s.get("history",[]),s.get("active_shifts",{}))

def team_score(m,web):
    w=weekly(m,web);goal=max(1,float(setting("weekly_goal_hours",3))*3600);activity=min(100,int(w/goal*100))
    ts=db.list_tasks(assignee_id=m.id,limit=1000,include_archived=True);done=sum(t.get("status") in {"done","archived"} for t in ts);reliability=100 if not ts else int(done/len(ts)*100)
    tickets=[t for t in db.list_tickets(limit=3000) if str(t.get("claimed_by_id"))==str(m.id) and t.get("status")=="closed"];support=min(100,len(tickets)*5)
    discipline=max(0,100-len(warns(m,web))*30);training=min(100,len([x for x in db.attempts(user_id=m.id,limit=200) if x.get("passed")])*25)
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

def auth(request,session,manager=False,perm="can_view_dashboard"):
    web=__import__("webserver");ctx=web.auth(request,session,perm=None if manager else perm)
    if manager and not (ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):raise HTTPException(403,"Nur Führungskräfte.")
    if ctx.member:upsert_member(ctx.member)
    return ctx

def allowed(i,key,web):
    if not i.guild or not isinstance(i.user,discord.Member):return False
    p,_=web.compute_perms(i.guild,i.user.id,web.load_config())
    if p.get("is_admin"):return True
    need={"warn":"can_warn","warn-remove":"can_warn","server-exit":"can_promote","team-exit":"can_promote","promote":"can_promote","demote":"can_promote","team-ping":"can_view_dashboard","pulse-ticket":"can_manage_tickets","melden":"can_view_dashboard","pulse-panel":"can_view_dashboard","pulse-lock":"is_admin","pulse-unlock":"is_admin","pulse-permission":"is_admin","activity-check":"can_view_dashboard","pulse-suite":"can_view_dashboard"}.get(key,"can_view_dashboard")
    rm=setting("command_roles",{}) or {};ids={int(x) for x in rm.get(key,[]) if str(x).isdigit()}
    return bool(p.get(need)) or any(r.id in ids for r in i.user.roles)

def locked():return bool(setting("emergency_lock",False))

def action_record(kind,payload,actor):
    aid="ACT-"+uuid.uuid4().hex[:8].upper()
    with cx() as c:c.execute("INSERT INTO suite_actions(id,kind,payload_json,status,created_by_id,created_by_name,approved_by_id,approved_by_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(aid,kind,json.dumps(payload,ensure_ascii=False),"awaiting_second",str(actor.id),actor.display_name,"","",iso(),iso()))
    return aid

def nav(active):
    items=[("suite","/suite","⚡ Führung"),("team","/suite/team","👥 Teamakten"),("time","/suite/time","⏱ Dienstzeit"),("schedule","/suite/schedule","🗓 Dienstplan"),("myschedule","/suite/my-schedule","👤 Mein Dienstplan"),("calendar","/suite/calendar","📅 Kalender"),("apps","/suite/applications","📝 Recruiting"),("interviews","/suite/interviews","🎤 Gespräche"),("training","/suite/training","🎓 Schulungen"),("tasks","/suite/tasks","📋 Aufgaben"),("news","/suite/news","📢 Team-News"),("audit","/suite/audit","🛡 Audit"),("search","/suite/search","⌕ Suche"),("cases","/suite/cases","🚨 Meldungen"),("ach","/suite/achievements","🏆 Erfolge")]
    return "".join(f'<a class="{"active" if k==active else ""}" href="{p}">{label}</a>' for k,p,label in items)

def page(ctx,active,title,subtitle,body,js=""):
    u=esc(ctx.user.get("global_name") or ctx.user.get("username") or ctx.user.get("id"))
    html=f"""<!doctype html><html lang=de><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>{esc(title)} · Pulse</title><style>{CSS}</style></head>
<body><div class=shell><aside class=side><div class=brand>⚡ Pulse <span style="color:var(--a)">TeamOS</span><small>Operations Suite</small></div><nav class=nav>{nav(active)}</nav><div class=tiny style="margin-top:18px">Angemeldet als <b>{u}</b><br><button class=btn onclick=openSearch()>Ctrl+K Suche</button><button class=btn onclick=accent()>Akzent</button></div></aside>
<main class=main><div class=top><div><h1>{esc(title)}</h1><div class=muted>{esc(subtitle)}</div></div><div><a class=btn href=/dashboard>Altes Dashboard</a> <a class="btn primary" href=/suite>Führung</a></div></div>{body}</main></div>
<div id=search class=search><div><input id=sq class=input placeholder="Teamler, ID, Warn-ID, Ticket, Aufgabe, Bewerbung, Audit, Roblox …" oninput=doSearch(this.value)><div id=sr style="margin-top:8px"></div></div></div>
<script>
function openSearch(){document.getElementById('search').style.display='flex';document.getElementById('sq').focus()}function closeSearch(){document.getElementById('search').style.display='none'}
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()=='k'){e.preventDefault();openSearch()}if(e.key=='Escape')closeSearch()})
document.getElementById('search').addEventListener('click',e=>{if(e.target.id=='search')closeSearch()})
async function doSearch(q){if(q.trim().length<2){sr.innerHTML='';return}sr.innerHTML='<div class="card">Suche…</div>';let d=await (await fetch('/suite/api/search?q='+encodeURIComponent(q))).json();sr.innerHTML=(d.results||[]).map(x=>'<a class=result href="'+x.url+'"><b>'+x.title+'</b><div class=tiny>'+x.type+' · '+(x.meta||'')+'</div></a>').join('')||'<div class=card>Keine Treffer.</div>'}
function accent(){let x=localStorage.getItem('pulse-accent')||'indigo';x=x==='indigo'?'cyan':x==='cyan'?'emerald':'indigo';localStorage.setItem('pulse-accent',x);location.reload()}
(function(){let x=localStorage.getItem('pulse-accent'),m={cyan:'#06b6d4',indigo:'#6366f1',emerald:'#10b981'};if(x&&m[x])document.documentElement.style.setProperty('--a',m[x])})();{js}</script></body></html>"""
    return HTMLResponse(html)

_ROUTES=False
def register_routes():
    global _ROUTES
    if _ROUTES:return
    webserver=__import__("webserver");app=webserver.app;init_db();seed()

    @app.get("/suite",response_class=HTMLResponse)
    async def leadership(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);web=webserver;ms=members(ctx.guild,web);sh=web.load_shifts();today=now().date().isoformat();active=sum(str(m.status) in {"online","idle","dnd"} for m in ms);duty=sum(x.get("status") in {"online","break"} for x in sh.get("active_shifts",{}).values())
        day=sum(int(x.get("duration_seconds",0)) for x in sh.get("history",[]) if x.get("date")==today)+sum(web.shift_elapsed(x) for x in sh.get("active_shifts",{}).values() if x.get("date")==today)
        apps=web.load_json(web.APPS_FILE,{});pending=sum(str(a.get("status","pending")).lower() in {"pending","new","in_review","in prüfung","review"} for a in apps.values());threes=sum(len(warns(m,web))>=3 for m in ms);ticks=db.list_tickets(limit=3000);overdue=[x for x in db.list_tasks(limit=3000) if x.get("status") not in {"done","archived"} and x.get("due_at") and (local(x.get("due_at")) or now())<=now()];loas=sum(1 for x in web.get_loas().values() if x.get("active"));promos=len([p for p in _promos() if p.get("status") in {"pending","revision"}]);low=[m for m in ms if weekly(m,web)<float(setting("weekly_goal_hours",3))*3600]
        red=threes or overdue or any(x.get("priority")=="urgent" and x.get("status")!="closed" for x in ticks);yellow=bool(low or pending or loas or promos);state=("🔴 Sofort handeln","bad") if red else ("🟡 Aufmerksamkeit nötig","warn") if yellow else ("🟢 Alles normal","ok")
        with cx() as c:last=[dict(x) for x in c.execute("SELECT * FROM suite_audit ORDER BY rowid DESC LIMIT 8").fetchall()]
        body=f'<div class="grid g4"><div class=card><div class=tiny>Aktuell aktiv</div><div class=metric>{active}</div></div><div class=card><div class=tiny>Im Dienst</div><div class=metric>{duty}</div></div><div class=card><div class=tiny>Heute Dienstzeit</div><div class=metric>{secfmt(day)}</div></div><div class=card><div class=tiny>Systemlage</div><div class=metric><span class="badge {state[1]}">{state[0]}</span></div></div></div><div class="grid g4" style="margin-top:13px"><div class=card><h3>🚨 Handlungsbedarf</h3><div class=list><div class=row>Offene Bewerbungen <b>{pending}</b></div><div class=row>3/3-Warnungen <b>{threes}</b></div><div class=row>Offene Tickets <b>{sum(x.get("status")!="closed" for x in ticks)}</b></div><div class=row>Überfällige Aufgaben <b>{len(overdue)}</b></div><div class=row>Abmeldungen <b>{loas}</b></div><div class=row>Beförderungen <b>{promos}</b></div></div></div><div class=card><h3>🟡 Wenig Dienstzeit</h3>{''.join(f'<div class=row><a href=/suite/member/{m.id}><b>{esc(m.display_name)}</b></a><span class=tiny>{secfmt(weekly(m,web))}</span></div>' for m in low[:8]) or "Niemand."}</div><div class=card><h3>✅ Heute aktiv</h3>{''.join(f'<div class=row><a href=/suite/member/{m.id}><b>{esc(m.display_name)}</b></a><span class=tiny>{secfmt(weekly(m,web))} Woche</span></div>' for m in ms if str(m.id) in {str(x.get("mod_id")) for x in sh.get("history",[]) if x.get("date")==today} or str(m.id) in sh.get("active_shifts",{})) or "Noch niemand."}</div><div class=card><h3>🛡 Letzte Teamänderungen</h3><div class=timeline>{''.join(f'<div class=event><b>{esc(x["action"])}</b><div class=tiny>{esc(x["occurred_at"])} · {esc(x["actor_name"])} → {esc(x["target_name"])} · {esc(x["request_id"])}</div><div>{esc(x["details"])}</div></div>' for x in last) or "Noch keine Audits."}</div></div></div><div class="grid g2" style="margin-top:13px"><div class=card><h3>📈 Wochenrangliste</h3>{''.join(f'<div class=row>#{i} <b>{esc(m.display_name)}</b><span>{secfmt(weekly(m,web))} · {team_score(m,web)["score"]}/100</span></div>' for i,m in enumerate(sorted(ms,key=lambda x:weekly(x,web),reverse=True)[:10],1))}</div><div class=card><h3>⚡ Schnellzugriff</h3><a class=btn href=/suite/promotions>Beförderungen</a> <a class=btn href=/suite/applications>Bewerbungen</a> <a class=btn href=/suite/tasks>Aufgaben</a> <a class=btn href=/suite/audit>Audit Center</a></div></div>'
        return page(ctx,"suite","Führungspanel","Aktive Lage, Personal, Warnungen, Tickets, Aufgaben und Änderungen",body)

    @app.get("/suite/team",response_class=HTMLResponse)
    async def team_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);web=webserver;rows=""
        for m in members(ctx.guild,web):
            s=team_score(m,web);sm=member_row(m.id);cl="ok" if s["score"]>=80 else "warn" if s["score"]>=60 else "bad"
            rows+=f'<tr><td><a href=/suite/member/{m.id}><b>{esc(m.display_name)}</b></a><div class=tiny>{m.id}</div></td><td>{esc(m.top_role.name)}</td><td>{secfmt(total_time(m,web))}</td><td>{secfmt(weekly(m,web))}</td><td><span class="badge {cl}">{s["score"]}/100</span></td><td>{len(warns(m,web))}/3</td><td>{esc(sm.get("probation_end") if sm else "—")}</td><td>{"🟢" if str(m.status) in {"online","idle","dnd"} else "⚪"}</td></tr>'
        body=f'<div class=card><input class=input id=tf placeholder="Name oder Discord-ID…" oninput="f(this.value)"><div class=scroll style="margin-top:10px"><table class=table id=tt><thead><tr><th>Teamler</th><th>Position</th><th>Gesamt</th><th>Woche</th><th>Score</th><th>Warnungen</th><th>Probezeit</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div></div>'
        return page(ctx,"team","Teamakten","Digitale Personalakte mit Score, Dienstzeit, Warnungen und Probezeit",body,"function f(q){q=q.toLowerCase();document.querySelectorAll('#tt tbody tr').forEach(r=>r.style.display=r.innerText.toLowerCase().includes(q)?'':'none')}")

    @app.get("/suite/member/{member_id}",response_class=HTMLResponse)
    async def member_page(request:Request,member_id:int,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);web=webserver;m=ctx.guild.get_member(member_id)
        if not m or m.bot or m not in members(ctx.guild,web):raise HTTPException(404,"Teammitglied nicht gefunden")
        upsert_member(m);sm=member_row(member_id) or {};td=web.load_json(web.DATA_FILE,{});entry=web.user_entry(td,str(member_id));s=team_score(m,web)
        with cx() as c:ps=[dict(x) for x in c.execute("SELECT * FROM suite_promotions WHERE target_id=? ORDER BY created_at DESC",(str(member_id),)).fetchall()];aud=[dict(x) for x in c.execute("SELECT * FROM suite_audit WHERE target_id=? OR actor_id=? ORDER BY rowid DESC LIMIT 80",(str(member_id),str(member_id))).fetchall()]
        ticks=[x for x in db.list_tickets(limit=3000) if str(x.get("user_id"))==str(member_id) or str(x.get("claimed_by_id"))==str(member_id)];tasks_=db.list_tasks(assignee_id=member_id,limit=1000,include_archived=True);sh=web.load_shifts();hist=[x for x in sh.get("history",[]) if str(x.get("mod_id"))==str(member_id)];last=hist[-1].get("ended_at_iso") if hist else "—"
        try:
            with sqlite3.connect(os.path.join(BASE,"activity_check.db")) as c:act=c.execute("SELECT COUNT(*) FROM responses WHERE user_id=?",(member_id,)).fetchone()[0]
        except Exception:act=0
        tabs=[("o","Übersicht"),("t","Dienstzeit"),("w","Warnungen"),("ti","Tickets"),("ta","Aufgaben"),("n","Notizen"),("p","Beförderungen"),("a","Audit")]
        panes=f'<div>{''.join(f"<button class=btn onclick=tab(\'{k}\')>{v}</button> " for k,v in tabs)}</div><div id=o class=pane style="margin-top:13px"><div class="grid g4"><div class=card><div class=tiny>Eintritt</div><b>{esc(sm.get("joined_at","—"))}</b></div><div class=card><div class=tiny>Position</div><b>{esc(m.top_role.name)}</b></div><div class=card><div class=tiny>Beförderungen</div><b>{sum(x.get("status")=="approved" for x in ps)}</b></div><div class=card><div class=tiny>Degradierungen</div><b>{sum(x.get("action")=="demote" for x in aud)}</b></div><div class=card><div class=tiny>Gesamtzeit</div><b>{secfmt(total_time(m,web))}</b></div><div class=card><div class=tiny>Woche</div><b>{secfmt(weekly(m,web))}</b></div><div class=card><div class=tiny>Letzte Aktivität</div><b>{esc(last)}</b></div><div class=card><div class=tiny>Letzter Login</div><b>{esc(sm.get("last_seen","—"))}</b></div><div class=card><div class=tiny>Letzter Activity Check</div><b>{act} bestätigt</b></div><div class=card><div class=tiny>Interne Bewertung</div><b>{esc(sm.get("internal_rating",80))}/100</b></div><div class=card><div class=tiny>Rangfortschritt</div><b>{rank_progress(m,web)}%</b></div><div class=card><div class=tiny>Team Score</div><b>{s["score"]}/100</b><div class=tiny>Aktivität {s["activity"]} · Zuverlässigkeit {s["reliability"]} · Support {s["support"]} · Disziplin {s["discipline"]}</div></div></div></div><div id=t class=pane style="display:none"><div class=card><div class=metric>{secfmt(total_time(m,web))}</div><div class=muted>Woche {secfmt(weekly(m,web))} · Schichten {len(hist)}</div></div></div><div id=w class=pane style="display:none"><div class=card>{''.join(f"<div class=row><span>{esc(x.get('id'))} · {esc(x.get('date'))}<br>{esc(x.get('reason'))}</span><span class=badge>{'Aktiv' if x.get('active',True) else 'Zurückgezogen'}</span></div>" for x in entry.get("warns_list",[])) or "Keine Warnungen."}</div></div><div id=ti class=pane style="display:none"><div class=card>{''.join(f"<div class=row><b>{esc(x.get('id'))}</b><span>{esc(x.get('status'))} · {esc(x.get('category'))}</span></div>" for x in ticks) or "Keine Tickets."}</div></div><div id=ta class=pane style="display:none"><div class=card>{''.join(f"<div class=row><a href=/suite/task/{x['id']}><b>{esc(x.get('title'))}</b></a><span>{esc(x.get('status'))}</span></div>" for x in tasks_) or "Keine Aufgaben."}</div></div><div id=n class=pane style="display:none"><div class=card><form method=post action=/suite/member/{member_id}/note><textarea class=ta name=note>{esc(sm.get('notes',''))}</textarea><button class="btn primary">Notiz speichern</button></form></div></div><div id=p class=pane style="display:none"><div class=card>{''.join(f"<div class=row><span>{esc(x.get('current_role'))} → {esc(x.get('target_role'))}</span><b>{esc(x.get('status'))}</b></div>" for x in ps) or "Keine Vorgänge."}</div></div><div id=a class=pane style="display:none"><div class=card>{''.join(f"<div class=event><b>{esc(x.get('action'))}</b> · {esc(x.get('occurred_at'))}<div class=tiny>{esc(x.get('details'))} · {esc(x.get('request_id'))}</div></div>" for x in aud) or "Keine Audits."}</div></div>'
        return page(ctx,"team",f"Teamakte · {m.display_name}","Übersicht · Dienstzeit · Warnungen · Tickets · Aufgaben · Notizen · Beförderungen · Audit",panes,"function tab(x){document.querySelectorAll('.pane').forEach(e=>e.style.display=e.id==x?'block':'none')}")

    @app.post("/suite/member/{member_id}/note")
    async def member_note(request:Request,member_id:int,note:str=Form(...),user_session:str=Cookie(None)):
        ctx=auth(request,user_session)
        with cx() as c:c.execute("UPDATE suite_members SET notes=?,last_seen=? WHERE user_id=?",(note[:5000],iso(),str(member_id)))
        audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),member_id,"","note_updated","Notiz aktualisiert");return RedirectResponse(f"/suite/member/{member_id}",303)

    @app.get("/suite/time",response_class=HTMLResponse)
    async def time_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session);web=webserver;sh=web.load_shifts();ms=members(ctx.guild,web);hist=sh.get("history",[]);d=now().date().isoformat();month=now().strftime("%Y-%m")
        day=sum(int(x.get("duration_seconds",0)) for x in hist if x.get("date")==d)+sum(web.shift_elapsed(x) for x in sh.get("active_shifts",{}).values() if x.get("date")==d);monthsec=sum(int(x.get("duration_seconds",0)) for x in hist if str(x.get("date","")).startswith(month))
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
        ctx=auth(request,user_session,manager=True);aid=db.create_announcement(title[:160],content[:4000],ctx.user["id"],ctx.user.get("global_name",""),kind);color=discord.Color.green() if kind=="success" else discord.Color.red() if kind=="urgent" else discord.Color.orange() if kind=="warning" else discord.Color.blurple();await webserver.send_team_update_embed(ctx.guild,title,content,color,actor=ctx.user.get("global_name",""),action="Team-News");audit(ctx.guild.id,ctx.user["id"],ctx.user.get("global_name",""),aid,title,"announcement_created",kind);return RedirectResponse("/suite/news",303)

    @app.get("/suite/audit",response_class=HTMLResponse)
    async def audit_page(request:Request,user_session:str=Cookie(None)):
        ctx=auth(request,user_session,manager=True);ok,count=audit_ok();q=request.query_params.get("q","").lower()
        with cx() as c:rows=[dict(x) for x in c.execute("SELECT * FROM suite_audit ORDER BY rowid DESC LIMIT 1000").fetchall()]
        rows=[x for x in rows if not q or q in json.dumps(x,ensure_ascii=False).lower()];listing=''.join(f'<div class=event><b>{esc(x["occurred_at"])}</b> · <b>{esc(x["action"])}</b><div class=tiny>👤 {esc(x["target_name"])} · 🛡 {esc(x["actor_name"])} · 🆔 {esc(x["request_id"])}</div><div>{esc(x["details"])}</div></div>' for x in rows)
        return page(ctx,"audit","Audit Center 2.0","Append-only Historie · SHA-256 Hashkette · Vorgangs-ID",f'<div class=card><span class="badge {"ok" if ok else "bad"}">{"✅ Hashkette intakt" if ok else "🚨 Manipulation erkannt"}</span> <span class="badge blue">{count} geprüft</span><form style="margin-top:10px"><input class=input name=q value="{esc(q)}" placeholder="Audit, Vorgang, Spieler, Aktion…"></form><div class=timeline style="margin-top:13px">{listing or "Keine Treffer."}</div></div>')

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
        if isinstance(e,dict):
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

class Confirm(discord.ui.View):
    def __init__(self,owner,callback):super().__init__(timeout=45);self.owner=owner;self.cb=callback
    @discord.ui.button(label="Bestätigen",style=discord.ButtonStyle.danger,emoji="✅")
    async def yes(self,i,b):
        if i.user.id!=self.owner:return await i.response.send_message("Nur der Ersteller kann bestätigen.",ephemeral=True)
        try:await self.cb(i)
        except Exception as e:
            if i.response.is_done():await i.followup.send("❌ "+str(e),ephemeral=True)
            else:await i.response.send_message("❌ "+str(e),ephemeral=True)
        self.stop()
    @discord.ui.button(label="Abbrechen",style=discord.ButtonStyle.secondary,emoji="❌")
    async def no(self,i,b):
        if i.user.id!=self.owner:return await i.response.send_message("Nur der Ersteller kann abbrechen.",ephemeral=True)
        await i.response.edit_message(content="❌ Abgebrochen.",embed=None,view=None);self.stop()

class TicketView(discord.ui.View):
    def __init__(self,tid):super().__init__(timeout=None);self.tid=tid
    @discord.ui.button(label="Ticket übernehmen",style=discord.ButtonStyle.success,emoji="🎫")
    async def claim(self,i,b):
        web=__import__("webserver")
        if not allowed(i,"pulse-ticket",web):return await i.response.send_message("❌ Keine Berechtigung.",ephemeral=True)
        db.claim_ticket(self.tid,i.user.id,i.user.display_name);audit(i.guild.id,i.user.id,i.user.display_name,self.tid,"","ticket_claimed","Ticket übernommen");await i.response.send_message("✅ Ticket übernommen.",ephemeral=True)
    @discord.ui.button(label="Schließen",style=discord.ButtonStyle.danger,emoji="🔒")
    async def close(self,i,b):
        web=__import__("webserver")
        if not allowed(i,"pulse-ticket",web):return await i.response.send_message("❌ Keine Berechtigung.",ephemeral=True)
        db.close_ticket(self.tid,"Per Discord geschlossen",closed_by_id=i.user.id,closed_by_name=i.user.display_name);audit(i.guild.id,i.user.id,i.user.display_name,self.tid,"","ticket_closed","Ticket geschlossen");await i.response.send_message("✅ Ticket geschlossen.",ephemeral=True)
    @discord.ui.button(label="Übergeben",style=discord.ButtonStyle.secondary,emoji="👤")
    async def transfer(self,i,b):
        web=__import__("webserver");ms=[m for m in managers(i.guild,web) if m.id!=i.user.id]
        if not allowed(i,"pulse-ticket",web) or not ms:return await i.response.send_message("❌ Keine Berechtigung / keine weitere Führung.",ephemeral=True)
        m=min(ms,key=lambda x:sum(t.get("status")!="closed" for t in db.list_tickets(limit=3000,claimed_by_id=x.id)));db.claim_ticket(self.tid,m.id,m.display_name);db.notify(m.id,"👤 Ticket übergeben",f"{self.tid} wurde dir zugewiesen.","info","/tickets",f"transfer:{self.tid}:{m.id}",21600);audit(i.guild.id,i.user.id,i.user.display_name,self.tid,"","ticket_transferred",m.display_name);await i.response.send_message("✅ Ticket übergeben.",ephemeral=True)

class PulseSuite(commands.Cog):
    def __init__(self,bot):self.bot=bot;init_db();seed();register_routes();self.auto.start()

    def guild(self):return self.bot.get_guild(GUILD_ID) if GUILD_ID else (self.bot.guilds[0] if self.bot.guilds else None)

    @tasks.loop(minutes=5)
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
        for m in g.members:
            if not m.bot and any(r.id in ids for r in m.roles):upsert_member(m)

    async def warn_sync(self,g):
        web=__import__("webserver")
        try:
            r=await web.reconcile_warning_roles(g,web.load_config())
            if r.get("failed"):print("⚠️ Warnrollen:",r)
        except Exception as e:print("⚠️ Warnrollensync:",e)

    async def reminders(self,g):
        web=__import__("webserver");n=now()
        for t in db.list_tasks(limit=3000):
            d=local(t.get("due_at"))
            if d and d<=n and t.get("status") not in {"done","archived"} and t.get("assignee_id"):db.notify(t["assignee_id"],"⏰ Aufgabe überfällig",f'{t["title"]} ist überfällig.',"warning","/suite/tasks",f"overdue:{t['id']}",21600)
        for aid,a in web.load_json(web.APPS_FILE,{}).items():
            d=local(a.get("created_at"))
            if d and (n-d).total_seconds()>172800:
                for m in managers(g,web):db.notify(m.id,"📝 Bewerbung wartet",f"{appname(aid,a)} wartet seit über 48h.","warning","/suite/applications",f"staleapp:{aid}",86400)
        for p in _promos():
            if p["status"] in {"pending","revision"}:
                for m in managers(g,web):db.notify(m.id,"📈 Beförderung wartet",f'{p["target_name"]} wartet auf eine Freigabe.',"warning","/suite/promotions",f"promo:{p['id']}",86400)

    async def activity_reminder(self,g):
        path=os.path.join(BASE,"activity_check.db")
        if not os.path.exists(path):return
        try:
            with sqlite3.connect(path) as c:
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
        for uid,s in sh.get("active_shifts",{}).items():
            elapsed=web.shift_elapsed(s)
            if elapsed>=8*3600 and not setting("long_shift:"+str(uid)+":"+str(s.get("date")),False):
                for m in managers(g,web):
                    db.notify(m.id,"⏱ Ungewöhnlich lange Schicht",f"{s.get('mod_name',uid)} ist seit {secfmt(elapsed)} im Dienst. Prüfe bei Bedarf die Schicht.","warning","/suite/member/"+str(uid),f"longshift:{uid}:{s.get('date')}",86400)
                set_setting("long_shift:"+str(uid)+":"+str(s.get("date")),True)

    async def inactivity(self,g):
        web=__import__("webserver");n=now();loas=web.get_loas()
        for m in members(g,web):
            sm=member_row(m.id);last=local(sm.get("last_seen") if sm else None) or n;days=(n-last).days
            if days>=INACTIVITY_NOTICE_DAYS and not loas.get(str(m.id),{}).get("active") and not loas.get(m.id,{}).get("active"):
                level="🔴 Inaktiv" if days>=INACTIVITY_WARNING_DAYS else "🟡 Aufmerksamkeit"
                for x in managers(g,web):db.notify(x.id,"⚠️ Inaktivität",f"{m.display_name}: {days} Tage · {level}.","warning","/suite/member/"+str(m.id),f"inactive:{m.id}:{n.date()}:{level}",86400)
            if sm and sm.get("probation_end") and local(sm["probation_end"])<=n+timedelta(hours=24):
                for x in managers(g,web):db.notify(x.id,"🎯 Probezeit",f"Probezeit von {m.display_name} endet bald.","info","/suite/member/"+str(m.id),f"probation:{m.id}:{sm['probation_end']}",86400)
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
        web=__import__("webserver");ms=members(g,web);sh=web.load_shifts();d=now().date().isoformat();sec=sum(int(x.get("duration_seconds",0)) for x in sh.get("history",[]) if x.get("date")==d)+sum(web.shift_elapsed(x) for x in sh.get("active_shifts",{}).values() if x.get("date")==d)
        e=discord.Embed(title="📊 Pulse Daily Report",color=discord.Color.blurple());e.add_field(name="👥 Team",value=str(len(ms)));e.add_field(name="🟢 Aktiv",value=str(sum(str(m.status) in {"online","idle","dnd"} for m in ms)));e.add_field(name="⏱ Dienstzeit",value=secfmt(sec));e.add_field(name="🎫 Tickets",value=str(sum(x.get("status")!="closed" for x in db.list_tickets(limit=3000))));e.add_field(name="📝 Bewerbungen",value=str(sum(str(a.get("status","pending")) in {"pending","in_review","interview"} for a in web.load_json(web.APPS_FILE,{}).values())));e.add_field(name="⚠ Warnungen",value=str(sum(len(warns(m,web)) for m in ms)));await self._report(g,e)

    async def weekly_report(self,g):
        web=__import__("webserver");ms=sorted(members(g,web),key=lambda m:weekly(m,web),reverse=True);e=discord.Embed(title="📈 Team-Wochenbericht",color=discord.Color.blurple())
        for i,m in enumerate(ms[:10],1):e.add_field(name=("🥇 " if i==1 else "🥈 " if i==2 else "🥉 " if i==3 else "#"+str(i)+" ")+m.display_name,value=f"{secfmt(weekly(m,web))} · Score {team_score(m,web)['score']}/100",inline=False)
        await self._report(g,e)

    async def _report(self,g,e):
        cid=int(setting("report_channel_id",0) or 0) or getattr(__import__("webserver"),"TEAM_UPDATE_CHANNEL_ID",0);ch=g.get_channel(cid) if cid else None
        if ch:
            try:await ch.send(embed=e)
            except Exception:pass

    async def update_presence(self,g):
        x=sum(s.get("status") in {"online","break"} for s in __import__("webserver").load_shifts().get("active_shifts",{}).values());await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,name=f"Pulse · {x} im Dienst"))

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
        if issues and fp!=old:await self._report(g,discord.Embed(title="🛡️ Pulse Rollen-Sicherheitsprüfung",description="\n".join("🔴 "+x for x in issues),color=discord.Color.red()));set_setting("role_health_fp",fp)
        elif not issues and old:set_setting("role_health_fp","")

    def safe_target(self,i,m):
        return i.guild and isinstance(m,discord.Member) and not m.bot and m.id!=i.user.id and m.top_role<i.user.top_role

    @commands.Cog.listener()
    async def on_ready(self):
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
    async def warn_cmd(self,i,member:discord.Member,grund:str):
        web=__import__("webserver")
        if not allowed(i,"warn",web) or locked() or not self.safe_target(i,member) or member not in members(i.guild,web):return await i.response.send_message("❌ Warnung nicht erlaubt.",ephemeral=True)
        async def do(x):
            td=web.load_json(web.DATA_FILE,{});e=web.user_entry(td,str(member.id));w={"id":"warn_"+uuid.uuid4().hex[:10],"reason":grund[:1000],"proof":"","by":i.user.display_name,"date":now().strftime("%d.%m.%Y %H:%M"),"active":True,"revoked_at":None,"revoked_by":None,"revoked_reason":""};e.setdefault("warns_list",[]).append(w);web.save_json(web.DATA_FILE,td);await web.sync_warn_roles(i.guild,member,len(web.active_warns(e)),web.load_config());await web.send_team_update_embed(i.guild,"⚠️ Team-Warnung",f"{member.mention} erhielt eine Warnung.",discord.Color.orange(),actor=i.user,target=member,action="Warnung",fields=[("Grund",grund),("Warn-ID",w["id"])]);audit(i.guild.id,i.user.id,i.user.display_name,member.id,member.display_name,"warning_created",w["id"]+" · "+grund);await x.response.edit_message(content="✅ Warnung "+w["id"]+" erstellt.",embed=None,view=None)
        await i.response.send_message(f"⚠️ {member.display_name}\n{grund}\n\nWirklich verwarnen?",view=Confirm(i.user.id,do),ephemeral=True)

    @app_commands.command(name="warns",description="Zeigt aktive Warnungen eines Teammitglieds.")
    async def warns_cmd(self,i,member:discord.Member):
        web=__import__("webserver")
        if not allowed(i,"warn",web):return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        ws=warns(member,web);e=discord.Embed(title=f"⚠ Warnungen · {member.display_name}",description="\n".join(f'{x["id"]} · {x.get("date")} · {x.get("reason")}' for x in ws) or "Keine aktiven Warnungen.",color=discord.Color.orange());await i.response.send_message(embed=e,ephemeral=True)

    @app_commands.command(name="warn-remove",description="Zieht eine Warnung nach Bestätigung zurück.")
    async def warn_remove(self,i,member:discord.Member,warn_id:str,grund:str=""):
        web=__import__("webserver");td=web.load_json(web.DATA_FILE,{});e=web.user_entry(td,str(member.id));w=next((x for x in e.get("warns_list",[]) if x.get("id")==warn_id and x.get("active",True)),None)
        if not allowed(i,"warn-remove",web) or locked() or not w:return await i.response.send_message("❌ Warnung nicht gefunden / keine Berechtigung.",ephemeral=True)
        async def do(x):
            td=web.load_json(web.DATA_FILE,{});e=web.user_entry(td,str(member.id));w=next((z for z in e.get("warns_list",[]) if z.get("id")==warn_id),None);w["active"]=False;w["revoked_at"]=iso();w["revoked_by"]=i.user.display_name;w["revoked_reason"]=grund[:1000];web.save_json(web.DATA_FILE,td);await web.sync_warn_roles(i.guild,member,len(web.active_warns(e)),web.load_config());audit(i.guild.id,i.user.id,i.user.display_name,member.id,member.display_name,"warning_withdrawn",warn_id+" · "+grund);await x.response.edit_message(content="✅ "+warn_id+" zurückgezogen.",embed=None,view=None)
        await i.response.send_message(f"Warnung {warn_id} von {member.display_name} wirklich zurückziehen?",view=Confirm(i.user.id,do),ephemeral=True)

    @app_commands.command(name="server-exit",description="Kritische Server-Aktion mit Zweitfreigabe.")
    async def server_exit(self,i,member:discord.Member,grund:str=""):
        web=__import__("webserver")
        if not allowed(i,"server-exit",web) or locked() or not self.safe_target(i,member):return await i.response.send_message("❌ Aktion nicht erlaubt.",ephemeral=True)
        async def do(x):
            aid=action_record("server-exit",{"target_id":member.id,"reason":grund},i.user)
            for m in managers(i.guild,web):
                if m.id!=i.user.id:db.notify(m.id,"🔐 Zweite Freigabe",f"Vorgang {aid} für {member.display_name} · /pulse-approve {aid}","warning","/suite/audit","approve:"+aid,21600)
            await x.response.edit_message(content="🔐 Vorgang "+aid+" angelegt. Zweite Führungskraft erforderlich.",embed=None,view=None)
        await i.response.send_message(f"🚨 {member.display_name} soll aus dem Server entfernt werden.\n{grund}\n\nWirklich vorbereiten?",view=Confirm(i.user.id,do),ephemeral=True)

    @app_commands.command(name="team-exit",description="Entfernt Teamrollen mit Zweitfreigabe und startet Offboarding.")
    async def team_exit(self,i,member:discord.Member,grund:str=""):
        web=__import__("webserver")
        if not allowed(i,"team-exit",web) or locked() or not self.safe_target(i,member):return await i.response.send_message("❌ Aktion nicht erlaubt.",ephemeral=True)
        async def do(x):
            aid=action_record("team-exit",{"target_id":member.id,"reason":grund},i.user)
            for m in managers(i.guild,web):
                if m.id!=i.user.id:db.notify(m.id,"🔐 Zweite Freigabe",f"Team-Offboarding {aid} · /pulse-approve {aid}","warning","/suite/audit","approve:"+aid,21600)
            await x.response.edit_message(content="🔐 Vorgang "+aid+" angelegt. Zweite Führungskraft erforderlich.",embed=None,view=None)
        await i.response.send_message(f"🚪 {member.display_name} soll das Team verlassen.\n{grund}\n\nWirklich vorbereiten?",view=Confirm(i.user.id,do),ephemeral=True)

    @app_commands.command(name="promote",description="Befördert mit Bestätigung und Hierarchieschutz.")
    async def promote_cmd(self,i,member:discord.Member,rolle:discord.Role,grund:str=""):
        web=__import__("webserver");ids={int(x) for x in web.load_config().get("team_role_ids",[])}
        if not allowed(i,"promote",web) or locked() or i.user.id==member.id or rolle.id not in ids or rolle.position>=i.user.top_role.position or rolle.position<=member.top_role.position:return await i.response.send_message("❌ Beförderung blockiert.",ephemeral=True)
        async def do(x):
            old=member.top_role;await member.add_roles(rolle,reason="Pulse Promotion");audit(i.guild.id,i.user.id,i.user.display_name,member.id,member.display_name,"promote",f"{old.name} → {rolle.name} · {grund}");await x.response.edit_message(content=f"✅ {member.display_name} → {rolle.name}.",view=None)
        await i.response.send_message(f"📈 {member.display_name} → {rolle.name}\n{grund}\n\nWirklich befördern?",view=Confirm(i.user.id,do),ephemeral=True)

    @app_commands.command(name="demote",description="Degradiert mit Bestätigung und Hierarchieschutz.")
    async def demote_cmd(self,i,member:discord.Member,rolle:discord.Role,grund:str=""):
        web=__import__("webserver");ids={int(x) for x in web.load_config().get("team_role_ids",[])}
        if not allowed(i,"demote",web) or locked() or i.user.id==member.id or rolle.id not in ids or rolle.position>=member.top_role.position or member.top_role.position>=i.user.top_role.position:return await i.response.send_message("❌ Degradierung blockiert.",ephemeral=True)
        async def do(x):
            old=member.top_role;await member.add_roles(rolle,reason="Pulse Demotion");audit(i.guild.id,i.user.id,i.user.display_name,member.id,member.display_name,"demote",f"{old.name} → {rolle.name} · {grund}");await x.response.edit_message(content=f"✅ {member.display_name} → {rolle.name}.",view=None)
        await i.response.send_message(f"📉 {member.display_name} → {rolle.name}\n{grund}\n\nWirklich degradieren?",view=Confirm(i.user.id,do),ephemeral=True)

    @app_commands.command(name="pulse-approve",description="Gibt einen kritischen Vorgang mit Zweitfreigabe frei.")
    async def approve_cmd(self,i,action_id:str):
        if not i.guild:return
        web=__import__("webserver");p,_=web.compute_perms(i.guild,i.user.id,web.load_config())
        if not p.get("can_promote") and not p.get("is_admin"):return await i.response.send_message("❌ Nur Führungskräfte.",ephemeral=True)
        with cx() as c:r=c.execute("SELECT * FROM suite_actions WHERE id=?",(action_id,)).fetchone()
        if not r or r["status"]!="awaiting_second" or r["created_by_id"]==str(i.user.id):return await i.response.send_message("❌ Kein freigabefähiger Vorgang.",ephemeral=True)
        payload=json.loads(r["payload_json"]);target=i.guild.get_member(int(payload["target_id"]))
        if not target or target.top_role>=i.user.top_role:return await i.response.send_message("❌ Ziel nicht mehr in zulässiger Hierarchie.",ephemeral=True)
        if r["kind"]=="server-exit":await target.remove_roles(*[x for x in target.roles if x.id in {int(z) for z in web.load_config().get("team_role_ids",[])} and not x.is_default()],reason="Pulse Server-Exit "+action_id)
        elif r["kind"]=="team-exit":await target.remove_roles(*[x for x in target.roles if x.id in {int(z) for z in web.load_config().get("team_role_ids",[])} and not x.is_default()],reason="Pulse Team-Exit "+action_id)
        else:return await i.response.send_message("❌ Unbekannter Vorgang.",ephemeral=True)
        with cx() as c:c.execute("UPDATE suite_actions SET status='approved',approved_by_id=?,approved_by_name=?,updated_at=? WHERE id=?",(str(i.user.id),i.user.display_name,iso(),action_id))
        audit(i.guild.id,i.user.id,i.user.display_name,target.id,target.display_name,r["kind"],"Zweite Freigabe "+action_id);await i.response.send_message("✅ "+action_id+" ausgeführt.",ephemeral=True)

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
        e=discord.Embed(title=f"🎫 Fall {tid}",description=beschreibung[:3500],color=discord.Color.blurple());e.add_field(name="Kategorie",value=kategorie[:80]);e.add_field(name="Priorität",value=prioritaet);await i.response.send_message(embed=e,view=TicketView(tid));audit(i.guild.id,i.user.id,i.user.display_name,tid,"","ticket_created",kategorie)

    @app_commands.command(name="melden",description="Erstellt einen internen Modmail-/Meldungsfall.")
    async def melden(self,i,kategorie:str,beschreibung:str,prioritaet:str="normal"):
        aid="CASE-"+uuid.uuid4().hex[:4].upper()
        with cx() as c:c.execute("INSERT INTO suite_cases VALUES(?,?,?,?,?,?,?,?,?,?,?)",(aid,str(i.user.id),i.user.display_name,kategorie[:80],beschreibung[:3500],"open",prioritaet,"","",iso(),iso()))
        web=__import__("webserver")
        for m in managers(i.guild,web):db.notify(m.id,"🚨 Neuer Meldefall",f"{aid} · {kategorie}","warning","/suite/cases",f"case:{aid}",86400)
        audit(i.guild.id,i.user.id,i.user.display_name,aid,aid,"case_created",f"{kategorie} · {prioritaet}");await i.response.send_message("✅ Meldung wurde intern an die Führung übergeben.",ephemeral=True)

    @app_commands.command(name="pulse-panel",description="Zeigt das Pulse TeamOS Discord-Control-Panel.")
    async def pulse_panel(self,i):
        web=__import__("webserver")
        if not allowed(i,"pulse-panel",web):return await i.response.send_message("❌ Kein Zugriff.",ephemeral=True)
        if not PUBLIC_BASE_URL:return await i.response.send_message("⚠️ PUBLIC_BASE_URL fehlt. Setze die öffentliche Dashboard-URL für Discord-Linkbuttons.",ephemeral=True)
        v=discord.ui.View(timeout=None)
        for label,path,em in [("Führung","/suite","⚡"),("Team","/suite/team","👥"),("Warnungen","/warns","⚠️"),("Tickets","/tickets","🎫"),("Aufgaben","/suite/tasks","📋"),("Bewerbungen","/suite/applications","📝"),("Audit","/suite/audit","🛡")]:v.add_item(discord.ui.Button(label=label,style=discord.ButtonStyle.link,emoji=em,url=PUBLIC_BASE_URL+path))
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
            for m in managers(after.guild,web):
                if m.id!=after.id:db.notify(m.id,"👋 Neues Teammitglied",f"{after.display_name} wurde automatisch eingerichtet.","info",f"/suite/member/{after.id}",f"newteam:{after.id}",86400)
        elif bt and not at:
            with cx() as c:c.execute("UPDATE suite_members SET archived_at=?,last_seen=? WHERE user_id=?",(iso(),iso(),str(after.id)))
            for m in managers(after.guild,web):db.notify(m.id,"🚪 Offboarding",f"{after.display_name} hat das Team verlassen. Aufgaben und Tickets prüfen.","warning","/suite/team",f"offboard:{after.id}",86400)

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
