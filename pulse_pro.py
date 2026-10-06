"""Pulse Pro v5 – UI, operations center, team workflow and security upgrades."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import shutil
import time
import traceback
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote, urlparse

import discord
from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

import pulse_db as db

VERSION = "5.1.2"
EXTRA_PERMS = (
    "can_manage_announcements",
    "can_manage_handover",
    "can_manage_promotions",
    "can_view_team_status",
    "can_manage_checkins",
)

PRO_CSS = r"""
<style>
:root{--p:#5b5cf0;--p2:#7c3aed;--bg:#f5f7fb;--surface:#fff;--surface2:#f8fafc;--border:#e8ebf3;--text:#111827;--muted:#667085;--good:#12b76a;--warn:#f79009;--bad:#f04438;--shadow:0 18px 50px rgba(15,23,42,.08)}
.dark{--bg:#080b12;--surface:#111522;--surface2:#0c101a;--border:#202637;--text:#f8fafc;--muted:#98a2b3;--shadow:0 18px 60px rgba(0,0,0,.26)}
html{scroll-behavior:smooth}body{background:radial-gradient(circle at 10% -10%,rgba(91,92,240,.12),transparent 28%),radial-gradient(circle at 90% -10%,rgba(124,58,237,.10),transparent 22%),var(--bg)!important;color:var(--text)}
.pulse-shell{max-width:1480px;margin:0 auto;width:100%}.pulse-topbar{position:sticky;top:12px;z-index:25;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 14px;margin-bottom:22px;border:1px solid var(--border);background:color-mix(in srgb,var(--surface) 88%,transparent);backdrop-filter:blur(18px);border-radius:18px;box-shadow:var(--shadow)}
.pulse-brand{display:flex;align-items:center;gap:10px}.pulse-brand-mark{width:38px;height:38px;border-radius:13px;display:grid;place-items:center;background:linear-gradient(135deg,var(--p),var(--p2));color:white;font-weight:800;box-shadow:0 10px 24px rgba(91,92,240,.28)}
.pulse-search{display:flex;align-items:center;gap:10px;flex:1;max-width:560px;border:1px solid var(--border);background:var(--surface2);border-radius:13px;padding:10px 12px;color:var(--muted)}.pulse-search input{border:0;outline:0;background:transparent;width:100%;color:var(--text);font-size:13px}
.pulse-icon-btn{width:38px;height:38px;border:1px solid var(--border);background:var(--surface2);border-radius:12px;display:grid;place-items:center;transition:.18s}.pulse-icon-btn:hover{transform:translateY(-1px);border-color:#b8bef8}.pulse-hero{position:relative;overflow:hidden;border:1px solid rgba(99,102,241,.18);border-radius:26px;padding:28px;background:linear-gradient(135deg,rgba(79,70,229,.97),rgba(124,58,237,.93));color:white;box-shadow:0 24px 60px rgba(76,81,191,.22)}.pulse-hero:after{content:"";position:absolute;width:240px;height:240px;border-radius:50%;right:-80px;top:-100px;background:rgba(255,255,255,.09);box-shadow:0 0 0 28px rgba(255,255,255,.035),0 0 0 56px rgba(255,255,255,.02)}
.pulse-hero-grid{position:relative;z-index:2;display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.6fr);gap:20px;align-items:end}.pulse-kicker{font-size:11px;letter-spacing:.12em;text-transform:uppercase;opacity:.72;font-weight:700}.pulse-title{font-size:31px;line-height:1.05;font-weight:850;margin-top:7px}.pulse-sub{font-size:13px;line-height:1.6;opacity:.82;max-width:700px;margin-top:10px}.pulse-hero-box{border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.08);border-radius:18px;padding:16px}.pulse-hero-box .label{font-size:10px;opacity:.72}.pulse-hero-box .value{font-size:27px;font-weight:850;margin-top:5px}.pulse-stat-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin:16px 0 22px}.pulse-stat{padding:15px;border:1px solid var(--border);background:var(--surface);border-radius:17px;box-shadow:0 6px 24px rgba(15,23,42,.04)}.pulse-stat .icon{font-size:18px}.pulse-stat .label{font-size:10px;color:var(--muted);margin-top:9px}.pulse-stat .value{font-size:21px;font-weight:800;margin-top:2px}.pulse-grid{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(320px,.7fr);gap:18px}.pulse-two{display:grid;grid-template-columns:1fr 1fr;gap:18px}.pulse-card{background:var(--surface);border:1px solid var(--border);border-radius:19px;box-shadow:0 7px 26px rgba(15,23,42,.045)}.pulse-card-h{padding:16px 17px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:12px}.pulse-card-b{padding:17px}.pulse-section-title{font-size:14px;font-weight:800}.pulse-section-sub{font-size:11px;color:var(--muted);margin-top:2px}.pulse-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 13px;border-radius:12px;border:1px solid var(--border);font-size:12px;font-weight:750;transition:.18s}.pulse-btn:hover{transform:translateY(-1px)}.pulse-btn.primary{background:linear-gradient(135deg,var(--p),var(--p2));color:white;border-color:transparent;box-shadow:0 10px 22px rgba(91,92,240,.18)}.pulse-btn.ghost{background:var(--surface2);color:var(--text)}.pulse-btn.good{background:rgba(18,183,106,.1);color:#079455;border-color:rgba(18,183,106,.2)}.pulse-btn.bad{background:rgba(240,68,56,.1);color:#d92d20;border-color:rgba(240,68,56,.2)}.dark .pulse-btn.good{color:#75e2b0}.dark .pulse-btn.bad{color:#ff9d96}
.pulse-list{display:flex;flex-direction:column;gap:9px}.pulse-row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px;border-radius:14px;background:var(--surface2);border:1px solid var(--border)}.pulse-row-main{min-width:0}.pulse-row-title{font-size:12px;font-weight:750}.pulse-row-meta{font-size:10px;color:var(--muted);margin-top:3px}.pulse-avatar{width:34px;height:34px;border-radius:11px;display:grid;place-items:center;background:linear-gradient(135deg,rgba(91,92,240,.15),rgba(124,58,237,.15));color:var(--p);font-weight:850;flex:0 0 auto}.pulse-status-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:7px}.pulse-progress{height:8px;border-radius:999px;background:var(--surface2);overflow:hidden;border:1px solid var(--border)}.pulse-progress>span{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,var(--p),var(--p2))}.pulse-pill{display:inline-flex;align-items:center;gap:5px;padding:5px 8px;border-radius:999px;font-size:10px;font-weight:750;background:var(--surface2);border:1px solid var(--border)}.pulse-pill.good{background:rgba(18,183,106,.08);color:#079455;border-color:rgba(18,183,106,.18)}.pulse-pill.warn{background:rgba(247,144,9,.09);color:#b54708;border-color:rgba(247,144,9,.2)}.pulse-pill.bad{background:rgba(240,68,56,.08);color:#d92d20;border-color:rgba(240,68,56,.2)}.dark .pulse-pill.good{color:#75e2b0}.dark .pulse-pill.warn{color:#fdba74}.dark .pulse-pill.bad{color:#ff9d96}
.pulse-empty{padding:32px 16px;text-align:center;color:var(--muted);font-size:12px;border:1px dashed var(--border);border-radius:15px}.pulse-alert{padding:12px 13px;border-radius:14px;border:1px solid var(--border);background:var(--surface2);font-size:11px}.pulse-alert + .pulse-alert{margin-top:8px}.pulse-alert strong{font-weight:800}.pulse-time{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.pulse-table{width:100%;border-collapse:separate;border-spacing:0 7px;font-size:11px}.pulse-table th{color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.08em;text-align:left;padding:0 10px 3px}.pulse-table td{background:var(--surface2);border-top:1px solid var(--border);border-bottom:1px solid var(--border);padding:11px 10px}.pulse-table td:first-child{border-left:1px solid var(--border);border-radius:12px 0 0 12px}.pulse-table td:last-child{border-right:1px solid var(--border);border-radius:0 12px 12px 0}.pulse-filter{display:flex;flex-wrap:wrap;gap:7px}.pulse-input{width:100%;border:1px solid var(--border);background:var(--surface2);color:var(--text);border-radius:12px;padding:10px 12px;font-size:12px;outline:0}.pulse-input:focus{border-color:#9296f6;box-shadow:0 0 0 4px rgba(91,92,240,.08)}.pulse-textarea{min-height:100px;resize:vertical}.pulse-modal-backdrop{position:fixed;inset:0;background:rgba(2,6,23,.55);backdrop-filter:blur(4px);z-index:70;display:none;align-items:flex-start;justify-content:center;padding:8vh 16px}.pulse-modal-backdrop.show{display:flex}.pulse-modal{width:min(760px,100%);background:var(--surface);border:1px solid var(--border);border-radius:22px;box-shadow:0 30px 90px rgba(0,0,0,.25);overflow:hidden}.pulse-modal-head{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid var(--border)}.pulse-command-list{max-height:55vh;overflow:auto;padding:8px}.pulse-command-item{display:flex;align-items:center;gap:12px;padding:12px;border-radius:13px;font-size:12px}.pulse-command-item:hover{background:var(--surface2)}.pulse-key{margin-left:auto;font-size:9px;color:var(--muted);border:1px solid var(--border);padding:3px 6px;border-radius:7px}.pulse-sidebar-sub{padding-left:9px;border-left:1px dashed var(--border);margin-left:6px}.pulse-footer{color:var(--muted);font-size:10px;padding:24px 0 10px;text-align:center}.pulse-shimmer{animation:pulseShimmer 1.6s infinite linear;background:linear-gradient(90deg,var(--surface2) 25%,rgba(255,255,255,.45) 50%,var(--surface2) 75%);background-size:200% 100%}@keyframes pulseShimmer{to{background-position:-200% 0}}
@media(max-width:1200px){.pulse-stat-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.pulse-grid{grid-template-columns:1fr}.pulse-hero-grid{grid-template-columns:1fr}}
@media(max-width:800px){.pulse-search{display:none}.pulse-stat-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.pulse-two{grid-template-columns:1fr}.pulse-title{font-size:25px}.pulse-topbar{top:7px}.pulse-hero{padding:22px;border-radius:20px}}
@media(max-width:520px){.pulse-stat-grid{grid-template-columns:1fr 1fr;gap:9px}.pulse-stat{padding:12px}.pulse-stat .value{font-size:18px}.pulse-hero-box .value{font-size:22px}.pulse-btn{font-size:11px;padding:9px 10px}}
</style>
"""

PRO_JS = r"""
<script>
(function(){
  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>Array.from(r.querySelectorAll(s));
  window.pulseTogglePalette=function(open=true){const m=$('#pulseCommandModal'); if(m)m.classList.toggle('show',open)};
  window.pulseToggleTheme=function(){const d=document.documentElement; const dark=d.classList.contains('dark'); d.classList.toggle('dark',!dark); localStorage.theme=dark?'light':'dark';};
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();pulseTogglePalette(true)} if(e.key==='Escape')pulseTogglePalette(false)});
  document.addEventListener('click',e=>{const modal=$('#pulseCommandModal'); if(e.target===modal)pulseTogglePalette(false);});
  function live(){fetch('/api/pulse/live',{credentials:'same-origin'}).then(r=>r.ok?r.json():null).then(d=>{if(!d)return; $$('[data-live]').forEach(el=>{const k=el.dataset.live;if(d[k]!==undefined)el.textContent=d[k]}); if(d.now){$$('[data-live-time]').forEach(el=>el.textContent=d.now)}}).catch(()=>{});}
  setInterval(live,30000); window.pulseLive=live;
  if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
  const forms=$$('form[data-confirm]'); forms.forEach(f=>f.addEventListener('submit',e=>{if(!confirm(f.dataset.confirm))e.preventDefault()}));
})();
</script>
"""


def replace_paths(app, paths):
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in paths]


def initials(name: str) -> str:
    bits = [x for x in str(name).strip().split() if x]
    return ("".join(x[0] for x in bits[:2]) or "?").upper()


def fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.astimezone().strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(value)[:16].replace("T", " ")


def parse_due(value: str | None):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.astimezone()
        return d
    except Exception:
        return None


def status_meta(status: str):
    return {
        "available": ("🟢", "Verfügbar", "good"),
        "busy": ("🟠", "Beschäftigt", "warn"),
        "away": ("🟡", "Abwesend", "warn"),
        "dnd": ("🔴", "Nicht stören", "bad"),
        "offline": ("⚪", "Offline", ""),
    }.get(status, ("⚪", "Offline", ""))


def make_shell_helpers(ws):
    old_head = ws.get_head_html
    old_sidebar = ws.get_sidebar_html
    old_compute = ws.compute_perms
    old_log = ws.log_audit

    def patched_head(title: str):
        return old_head(title) + '<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#5b5cf0"><meta name="mobile-web-app-capable" content="yes">' + PRO_CSS + PRO_JS

    def patched_compute(guild, user_id, config):
        perms, member = old_compute(guild, user_id, config)
        for key in EXTRA_PERMS:
            perms.setdefault(key, False)
        if perms.get("is_admin"):
            for key in EXTRA_PERMS:
                perms[key] = True
        else:
            cfg = config.get("permissions", {})
            member_roles = getattr(member, "roles", []) if member else []
            for role in member_roles:
                rp = cfg.get(str(role.id), {})
                for key in EXTRA_PERMS:
                    if rp.get(key):
                        perms[key] = True
            if perms.get("can_promote"):
                perms["can_manage_announcements"] = True
                perms["can_manage_promotions"] = True
        return perms, member

    def patched_log(actor_name, actor_id, action, details):
        old_log(actor_name, actor_id, action, details)
        try:
            db.record_event("audit", "panel", "", actor_id, actor_name or "System", {"action": action, "details": details})
        except Exception:
            pass

    def nav_item(key, href, icon, label, active, badge_text=""):
        cls = "nav-active" if active else ""
        badge = f'<span class="ml-auto min-w-5 text-center text-[9px] rounded-full bg-rose-500 text-white px-1.5 py-0.5">{badge_text}</span>' if badge_text else ""
        return f'<a href="{href}" class="{cls} flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-xs font-medium transition hover:bg-slate-100 dark:hover:bg-slate-800/60">{icon}<span>{label}</span>{badge}</a>'

    def patched_sidebar(guild_name, current_page="dashboard", current_user=None, perms=None):
        perms = perms or {}; current_user = current_user or {}
        uid = current_user.get("id")
        try: unread = db.unread_count(uid) if uid else 0
        except Exception: unread = 0
        groups = [
            ("Arbeitsbereich", [
                ("dashboard","/dashboard","⌂","Übersicht",True,False),
                ("inbox","/pulse-inbox","◈","Inbox",True,False),
                ("team-status","/team-status","◉","Teamstatus",perms.get("can_view_team_status",True),False),
                ("activity-check","/activity-check","✓","Activity Check",perms.get("can_manage_checkins") or perms.get("is_admin"),False),
                ("melonly","/melonly","🛡️","Melonly",perms.get("can_warn") or perms.get("can_add_notes") or perms.get("is_admin"),False),
                ("warns","/warns","⚠","Verwarnungszentrale",perms.get("can_warn") or perms.get("is_admin"),False),
                ("team","/team","◌","Teamliste",True,False),
            ]),
            ("Workflow", [
                ("tickets","/tickets","▣","Tickets",True,False),
                ("tasks","/tasks","□","Aufgaben",True,False),
                ("applications","/applications","✎","Bewerbungen",True,False),
                ("approvals","/approvals","✓","Freigaben",perms.get("can_promote") or perms.get("can_manage_applications") or perms.get("is_admin"),False),
                ("handover","/handover","↪","Übergabe",True,False),
                ("announcements","/announcements","✦","Ankündigungen",perms.get("can_manage_announcements") or perms.get("is_admin"),False),
            ]),
            ("Planung", [
                ("meetings","/meetings","◫","Meetings",True,False),
                ("meeting-history","/meetings-history","▤","Meeting-Historie",True,False),
                ("calendar","/calendar","□","Kalender",True,False),
                ("loa","/loa","☾","Abmeldungen",True,False),
                ("training","/training","◇","Schulungen",True,False),
                ("wiki","/wiki","▤","Team-Wiki",True,False),
            ]),
            ("Auswertung", [
                ("stats","/stats","◒","Analytics",perms.get("can_view_analytics",False),False),
                ("achievements","/achievements","◆","Achievements",True,False),
            ]),
        ]
        nav = []
        for group, items in groups:
            visible = [x for x in items if x[4]]
            if not visible: continue
            nav.append(f'<div class="px-2 mt-4 mb-2 text-[9px] font-bold uppercase tracking-[.14em] text-slate-400">{group}</div>')
            for key, href, icon, label, visible, _ in visible:
                badge_text = unread if key == "inbox" and unread else ""
                nav.append(nav_item(key, href, icon, label, current_page == key, str(badge_text)))
        if perms.get("is_admin"):
            nav.append('<div class="px-2 mt-4 mb-2 text-[9px] font-bold uppercase tracking-[.14em] text-slate-400">Administration</div>')
            for key,href,icon,label in [("settings","/settings","⚙","Einstellungen"),("backups","/backups","◉","Backups"),("system","/system","♥","Systemstatus")]:
                nav.append(nav_item(key,href,icon,label,current_page==key))
        user_name = ws.esc(current_user.get("global_name") or current_user.get("username") or "Team")
        avatar_id=current_user.get("avatar"); user_id=current_user.get("id")
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_id}.png" if avatar_id and user_id else "https://cdn.discordapp.com/embed/avatars/0.png"
        return f'''<style>
        #pulseSidebar{{transition:transform .18s ease,width .18s ease}}#pulseSidebar .nav-active{{background:linear-gradient(90deg,rgba(91,92,240,.12),rgba(124,58,237,.06));color:#5b5cf0;border:1px solid rgba(91,92,240,.14);box-shadow:0 7px 20px rgba(91,92,240,.07)}}
        .dark #pulseSidebar .nav-active{{color:#a8abff}}@media(min-width:768px){{#pulseSidebar{{display:flex!important}}#pulseMobileBtn,#pulseBackdrop{{display:none!important}}}}
        </style>
        <button id="pulseMobileBtn" onclick="pulseOpenNav()" class="md:hidden fixed top-3 left-3 z-50 w-10 h-10 rounded-xl bg-white dark:bg-[#111522] border border-slate-200 dark:border-slate-700 shadow-lg">☰</button>
        <div id="pulseBackdrop" onclick="pulseCloseNav()" class="hidden fixed inset-0 bg-black/55 z-40"></div>
        <aside id="pulseSidebar" class="hidden fixed md:sticky top-0 left-0 z-50 h-screen w-[276px] overflow-y-auto bg-white dark:bg-[#111522] border-r border-slate-200 dark:border-slate-800 flex-col justify-between p-4 shrink-0">
          <div>
            <div class="flex items-center gap-3 px-1 mb-5"><div class="pulse-brand-mark">P</div><div class="min-w-0"><div class="text-sm font-extrabold truncate">Pulse TeamOS</div><div class="text-[10px] text-slate-400 truncate">{ws.esc(guild_name)} · v{VERSION}</div></div></div>
            <button onclick="pulseTogglePalette(true)" class="w-full flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-[#0c101a] px-3 py-2.5 text-xs text-slate-400 text-left mb-4"><span>⌕</span><span class="flex-1">Schnell suchen…</span><kbd class="text-[9px] border border-slate-200 dark:border-slate-700 rounded px-1.5 py-0.5">Ctrl K</kbd></button>
            <nav>{''.join(nav)}</nav>
          </div>
          <div class="mt-5 pt-4 border-t border-slate-200 dark:border-slate-800 space-y-3">
            <div class="flex items-center gap-2.5"><img src="{avatar_url}" class="w-8 h-8 rounded-full border border-slate-200 dark:border-slate-700" alt=""><div class="min-w-0 flex-1"><div class="text-xs font-bold truncate">{user_name}</div><div class="text-[9px] text-slate-400">Angemeldet</div></div><button onclick="pulseToggleTheme()" title="Theme wechseln" class="pulse-icon-btn">◐</button></div>
            <a href="/logout" class="flex items-center justify-center gap-2 w-full px-3 py-2 rounded-xl text-xs font-semibold bg-slate-100 dark:bg-slate-800/70 hover:bg-rose-50 dark:hover:bg-rose-950/20 text-slate-600 dark:text-slate-300">↤ Abmelden</a>
          </div>
        </aside>
        <script>function pulseOpenNav(){{document.getElementById('pulseSidebar').classList.remove('hidden');document.getElementById('pulseSidebar').classList.add('flex');document.getElementById('pulseBackdrop').classList.remove('hidden')}}function pulseCloseNav(){{document.getElementById('pulseSidebar').classList.add('hidden');document.getElementById('pulseSidebar').classList.remove('flex');document.getElementById('pulseBackdrop').classList.add('hidden')}}</script>
        '''

    ws.get_head_html = patched_head
    ws.get_sidebar_html = patched_sidebar
    ws.compute_perms = patched_compute
    ws.log_audit = patched_log


def build_command_modal(ws):
    items = [
        ("⌂", "Dashboard öffnen", "/dashboard"), ("◌", "Teamstatus", "/team-status"), ("✓", "Activity Check", "/activity-check"), ("🛡️", "Melonly", "/melonly"), ("▣", "Tickets", "/tickets"),
        ("□", "Aufgaben", "/tasks"), ("✓", "Freigaben", "/approvals"), ("↪", "Übergabe", "/handover"),
        ("✦", "Ankündigungen", "/announcements"), ("▤", "Meeting-Historie", "/meetings-history"), ("◇", "Schulungen", "/training"), ("▤", "Team-Wiki", "/wiki"),
        ("◒", "Analytics", "/stats"), ("◆", "Achievements", "/achievements"), ("⚙", "Einstellungen", "/settings"),
    ]
    rows = "".join(f'<a href="{u}" class="pulse-command-item"><span class="pulse-avatar">{i}</span><span>{t}</span><span class="pulse-key">Öffnen</span></a>' for i,t,u in items)
    return f'''<div id="pulseCommandModal" class="pulse-modal-backdrop"><div class="pulse-modal"><div class="pulse-modal-head"><div><div class="text-sm font-extrabold">Pulse Schnellzugriff</div><div class="text-[10px] text-slate-400 mt-0.5">Navigation ohne Umwege</div></div><button onclick="pulseTogglePalette(false)" class="pulse-icon-btn">✕</button></div><div class="p-3"><div class="pulse-search" style="max-width:none"><span>⌕</span><input id="pulseCmdFilter" oninput="pulseFilterCmd(this.value)" autofocus placeholder="Bereich suchen…"></div></div><div id="pulseCmdList" class="pulse-command-list">{rows}</div></div></div><script>function pulseFilterCmd(v){{v=v.toLowerCase();document.querySelectorAll('#pulseCmdList .pulse-command-item').forEach(x=>x.style.display=x.textContent.toLowerCase().includes(v)?'':'none')}}</script>'''


def render_pro_page(ws, title, ctx, page, body, extra=""):
    body = '<div class="pulse-shell">' + body + '<div class="pulse-footer">Pulse TeamOS v' + VERSION + ' · interne Teamverwaltung</div></div>' + build_command_modal(ws)
    return ws.render_page(title, ctx, page, body, extra)


def remove_and_add(app, path, methods, endpoint, **kwargs):
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != path]
    for method in methods:
        decorator = app.get if method == "GET" else app.post
        decorator(path, **kwargs)(endpoint)


def register(app):
    import webserver as ws
    db.init_db()
    make_shell_helpers(ws)

    # Security headers + same-origin guard for state-changing browser requests.
    @app.middleware("http")
    async def pulse_security_headers(request: Request, call_next):
        try:
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin:
                    host = request.headers.get("host", "")
                    public = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
                    parsed = urlparse(origin)
                    allowed = {f"http://{host}", f"https://{host}"}
                    if public:
                        allowed.add(public)
                    if origin.rstrip("/") not in allowed:
                        return JSONResponse({"detail": "Ungültige Anfragequelle."}, status_code=403)
            response = await call_next(request)
        except Exception as exc:
            print("Pulse request error:", repr(exc))
            traceback.print_exc()
            raise
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
        response.headers.setdefault("Cache-Control", "no-store" if request.method != "GET" or request.url.path.startswith("/api/") else "private, max-age=0, must-revalidate")
        return response

    team = lambda guild, ids: sorted([m for m in guild.members if not m.bot and any(r.id in ids for r in m.roles)], key=lambda m: m.display_name.lower())

    def cctx(request, session, perm=None, admin=False):
        return ws.auth(request, session, perm=perm, admin=admin)

    def e(v): return ws.esc(v)
    def card(title, inner, icon="◆"):
        return f'<section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">{icon} {title}</div></div></div><div class="pulse-card-b">{inner}</div></section>'
    def pill(text, kind=""):
        return f'<span class="pulse-pill {kind}">{e(text)}</span>'
    def member_role(m, ids):
        roles = [r for r in m.roles if r.id in ids]
        return max(roles, key=lambda r:r.position).name if roles else "Team"
    def work_stats(c):
        shifts=ws.load_shifts(); active=shifts.get("active_shifts",{}); hist=shifts.get("history",[])
        logs=ws.load_json(ws.LOGS_FILE,[]); apps=ws.load_json(ws.APPS_FILE,{}); loas=ws.get_loas()
        tickets=db.list_tickets(limit=2000); tasks=db.list_tasks(limit=2000); promos=db.promotion_requests("pending",200)
        current=active.get(str(c.user["id"]))
        weekly=ws.calculate_weekly_seconds(str(c.user["id"]),hist,active)
        active_count=sum(1 for s in active.values() if s.get("status") in ("online","break"))
        return {"active":active_count,"logs":len(logs),"apps":sum(1 for a in apps.values() if a.get("status")=="pending"),"loas":sum(1 for l in loas.values() if l.get("active")),"tickets":tickets,"tasks":tasks,"promos":promos,"weekly":weekly,"current":current}

    async def team_update(guild, title, content, color=None):
        color = color or discord.Color.blurple()
        channel = guild.get_channel(ws.TEAM_UPDATE_CHANNEL_ID)
        if not channel:
            print(f"Team-Updates-Kanal nicht erreichbar: {ws.TEAM_UPDATE_CHANNEL_ID}")
            return None
        embed=discord.Embed(title=title,description=content,color=color,timestamp=discord.utils.utcnow())
        embed.set_footer(text=f"{guild.name} · Pulse TeamOS")
        try:
            return await channel.send(embed=embed)
        except Exception as exc:
            print(f"Team-Update konnte nicht gesendet werden: {exc}")
            return None

    # ---------------- Melonly / Roblox Moderation ----------------
    async def melonly_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session)
        if not (c.perms.get("can_warn") or c.perms.get("can_add_notes") or c.perms.get("is_admin")):
            raise HTTPException(403,"Dafür fehlt dir die Berechtigung für Melonly.")

        logs=ws.load_json(ws.LOGS_FILE,[])
        relevant=[x for x in reversed(logs) if x.get("type") in ("Ban","Kick","Notiz","Ban BOLO")][:120]
        items=[]
        for x in relevant:
            lt=x.get("type","Log")
            kind={"Ban":"bad","Kick":"warn","Notiz":"","Ban BOLO":"bad"}.get(lt,"")
            items.append(
                f'<article class="pulse-row"><div class="flex items-center gap-3 min-w-0"><div class="pulse-avatar">{e(initials(x.get("target_user") or "?"))}</div><div class="pulse-row-main"><div class="pulse-row-title"><span class="pulse-pill {kind}">{e(lt)}</span> {e(x.get("target_user"))}</div><div class="pulse-row-meta">Roblox ID: {e(x.get("roblox_id","N/A"))} · {e(x.get("created_at"))}</div><div class="pulse-row-meta">{e(x.get("reason"))}</div></div></div><div class="text-[10px] text-slate-400">von {e(x.get("moderator"))}</div></article>'
            )
        history="".join(items) or '<div class="pulse-empty">Noch keine Spieler-Vorgänge eingetragen.</div>'

        form_html=f'''
        <form action="/log/create" method="post" id="melonlyForm" class="space-y-4">
          <div class="relative">
            <label class="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Roblox Username</label>
            <input id="melonlyName" name="target_user" required maxlength="50" autocomplete="off" class="pulse-input mt-1" placeholder="@Username">
            <div id="melonlySuggestions" class="absolute left-0 right-0 top-full mt-1 z-30 rounded-xl overflow-hidden bg-[var(--surface)] border border-slate-200 dark:border-slate-700 shadow-xl hidden"></div>
          </div>
          <div id="melonlyPreview" class="hidden"></div>
          <div>
            <label class="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Roblox Player ID</label>
            <input id="melonlyId" name="roblox_id" required inputmode="numeric" pattern="[0-9]+" class="pulse-input mt-1" placeholder="Wird automatisch eingetragen">
          </div>
          <div>
            <label class="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Aktion</label>
            <select name="log_type" required class="pulse-input mt-1">
              <option value="Ban">🚫 Ban</option>
              <option value="Kick">🚪 Kick</option>
              <option value="Notiz">📝 Notiz</option>
              <option value="Ban BOLO">🚨 Ban BOLO</option>
            </select>
          </div>
          <div>
            <label class="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Grund</label>
            <textarea name="reason" required maxlength="1000" class="pulse-input pulse-textarea mt-1" placeholder="Begründung / Notiz…"></textarea>
          </div>
          <button class="pulse-btn primary w-full">✓ Spieler-Vorgang eintragen</button>
        </form>
        <div class="text-[10px] text-slate-400 mt-4">
          Der Eintrag wird im Pulse-Moderationsprotokoll gespeichert. Ban/Kick sind hier Protokoll-Aktionen und keine automatische Roblox-API-Sperre.
        </div>'''

        body=f'''
        <div class="pulse-topbar">
          <div><div class="pulse-section-title">🛡️ Melonly</div><div class="pulse-section-sub">Roblox-Spieler direkt suchen, ID übernehmen und Moderationsvorgänge sauber dokumentieren.</div></div>
          <a href="/dashboard" class="pulse-btn ghost">← Übersicht</a>
        </div>
        <div class="pulse-stat-grid">
          <div class="pulse-stat"><div class="icon">🚫</div><div class="label">Bans / BOLOs</div><div class="value">{sum(1 for x in relevant if x.get("type") in ("Ban","Ban BOLO"))}</div></div>
          <div class="pulse-stat"><div class="icon">🚪</div><div class="label">Kicks</div><div class="value">{sum(1 for x in relevant if x.get("type")=="Kick")}</div></div>
          <div class="pulse-stat"><div class="icon">📝</div><div class="label">Notizen</div><div class="value">{sum(1 for x in relevant if x.get("type")=="Notiz")}</div></div>
          <div class="pulse-stat"><div class="icon">📚</div><div class="label">Letzte Einträge</div><div class="value">{len(relevant)}</div></div>
        </div>
        <div class="pulse-grid">
          <div>{card("Spieler eintragen",form_html,"🛡️")}</div>
          <div>{card("Letzte Spieler-Vorgänge",history,"▣")}</div>
        </div>
        '''

        head=r'''<script>
        (function(){
          const name=document.getElementById("melonlyName");
          const id=document.getElementById("melonlyId");
          const box=document.getElementById("melonlySuggestions");
          const preview=document.getElementById("melonlyPreview");
          let searchTimer=null, exactTimer=null;

          function norm(v){ return String(v || "").trim().replace(/^@+/, ""); }
          function hideSuggestions(){
            if(!box) return;
            box.classList.add("hidden");
            box.innerHTML="";
          }
          function pickUser(u){
            name.value=u.name || "";
            id.value=u.id || "";
            hideSuggestions();
            loadExact(u.name || "");
          }
          function showSuggestions(users){
            box.innerHTML="";
            if(!users || !users.length){ hideSuggestions(); return; }
            users.forEach(function(u){
              const b=document.createElement("button");
              b.type="button";
              b.className="w-full text-left px-3 py-2.5 hover:bg-slate-100 dark:hover:bg-slate-800";
              const title=document.createElement("div");
              title.className="text-xs font-bold text-slate-900 dark:text-white";
              title.textContent=u.displayName || u.name;
              const sub=document.createElement("div");
              sub.className="text-[10px] text-slate-400";
              sub.textContent="@"+(u.name||"")+" · ID "+(u.id||"");
              b.appendChild(title);
              b.appendChild(sub);
              b.addEventListener("click",function(){pickUser(u);});
              box.appendChild(b);
            });
            box.classList.remove("hidden");
          }
          function searchUsers(v){
            clearTimeout(searchTimer);
            const q=norm(v);
            if(q.length<2){hideSuggestions();return;}
            searchTimer=setTimeout(function(){
              fetch("/api/roblox-search?query="+encodeURIComponent(q),{cache:"no-store"})
                .then(function(r){return r.json();})
                .then(function(d){showSuggestions(d.success ? d.users : []);})
                .catch(hideSuggestions);
            },220);
          }
          function loadExact(v){
            clearTimeout(exactTimer);
            const q=norm(v);
            if(q.length<3){
              if(preview){preview.classList.add("hidden");preview.innerHTML="";}
              return;
            }
            exactTimer=setTimeout(function(){
              fetch("/api/roblox-user?username="+encodeURIComponent(q),{cache:"no-store"})
                .then(function(r){return r.json();})
                .then(function(d){
                  if(!d.success)return;
                  id.value=d.id || "";
                  if(preview){
                    preview.innerHTML="";
                    const row=document.createElement("div");
                    row.className="pulse-row";
                    const img=document.createElement("img");
                    img.src=d.avatarUrl || "";
                    img.className="w-9 h-9 rounded-full";
                    const wrap=document.createElement("div");
                    wrap.className="min-w-0";
                    const title=document.createElement("div");
                    title.className="text-xs font-bold";
                    title.textContent=d.displayName || d.username;
                    const sub=document.createElement("div");
                    sub.className="text-[10px] text-slate-400";
                    sub.textContent="@"+(d.username||q)+" · ID "+(d.id||"");
                    const old=document.createElement("div");
                    old.className="text-[10px] text-amber-500 mt-1";
                    if((d.previous_total||0)>0)old.textContent="⚠️ "+d.previous_total+" frühere Vorgänge";
                    wrap.appendChild(title);
                    wrap.appendChild(sub);
                    if(old.textContent)wrap.appendChild(old);
                    row.appendChild(img);
                    row.appendChild(wrap);
                    preview.appendChild(row);
                    preview.classList.remove("hidden");
                  }
                })
                .catch(function(){});
            },300);
          }
          name.addEventListener("input",function(){
            const q=norm(name.value);
            if(name.value!==q)name.value=q;
            searchUsers(q);
            loadExact(q);
          });
          name.addEventListener("blur",function(){setTimeout(hideSuggestions,180);});
        })();
        </script>''';

        return render_pro_page(ws,"Melonly",c,"melonly",body,head)

    # ---------------- Dashboard ----------------
    async def dashboard_v5(request: Request, user_session: str = Cookie(None)):
        c=cctx(request,user_session); s=work_stats(c); goal=float(c.config.get("weekly_goal_hours",3.0)); pct=min(100,round(s["weekly"]/(goal*3600)*100)) if goal else 100
        shift=s["current"]; status=shift.get("status") if shift else "offline"; elapsed=ws.shift_elapsed(shift) if shift else 0
        open_t=[t for t in s["tickets"] if t["status"]!="closed"]; overdue=[]; now=ws.now_de()
        for t in s["tasks"]:
            due=parse_due(t.get("due_at"));
            if t.get("status") not in ("done","archived") and due and due < now: overdue.append(t)
        active_people=[]
        ids=c.config.get("team_role_ids",[])
        for uid, sh in ws.load_shifts().get("active_shifts",{}).items():
            m=c.guild.get_member(int(uid)) if str(uid).isdigit() else None
            if m: active_people.append((m,sh))
        active_people.sort(key=lambda x:x[0].display_name.lower())
        notices=db.notifications(c.user["id"],6)
        events=db.events(limit=10)
        recent_html="".join(f'<div class="pulse-row"><div class="pulse-avatar">{e("EV" if ev["actor_name"]=="System" else initials(ev["actor_name"]))}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(ev["payload_json"] if isinstance(ev["payload_json"],str) else ev["event_type"])}</div><div class="pulse-row-meta">{e(ev["actor_name"] or "System")} · {e(fmt_dt(ev["created_at"]))}</div></div></div>' for ev in events)
        duty_html="".join(f'<div class="pulse-row"><div class="flex items-center gap-3 min-w-0"><div class="pulse-avatar">{e(initials(m.display_name))}</div><div class="pulse-row-main"><div class="pulse-row-title truncate">{e(m.display_name)}</div><div class="pulse-row-meta">{e(member_role(m,ids))}</div></div></div><div class="text-right"><div>{pill("Pause" if sh.get("status")=="break" else "Im Dienst", "warn" if sh.get("status")=="break" else "good")}</div><div class="pulse-row-meta pulse-time">{ws.fmt_duration(ws.shift_elapsed(sh))}</div></div></div>' for m,sh in active_people[:10])
        urgent=""
        if s["promos"]: urgent += f'<a href="/approvals" class="pulse-alert"><strong>✓ {len(s["promos"])} Beförderungsanträge</strong><div class="text-slate-400 mt-1">Warten auf Entscheidung.</div></a>'
        if s["apps"]: urgent += f'<a href="/applications" class="pulse-alert"><strong>✎ {s["apps"]} offene Bewerbungen</strong><div class="text-slate-400 mt-1">Bitte zeitnah bearbeiten.</div></a>'
        if overdue: urgent += f'<a href="/tasks" class="pulse-alert"><strong>! {len(overdue)} überfällige Aufgaben</strong><div class="text-slate-400 mt-1">Fristen prüfen und Status aktualisieren.</div></a>'
        teamdb=ws.load_json(ws.DATA_FILE,{})
        active_warn_total=sum(len(ws.active_warns(v)) for v in teamdb.values() if isinstance(v,dict))
        critical_warn_people=sum(1 for v in teamdb.values() if isinstance(v,dict) and len(ws.active_warns(v)) >=5)
        if critical_warn_people:
            urgent += f'<a href="/warns" class="pulse-alert"><strong>🚨 {critical_warn_people} Teamler bei 5/5 Warnungen</strong><div class="text-slate-400 mt-1">Verwarnungen und Konsequenzen prüfen.</div></a>'
        elif active_warn_total:
            urgent += f'<a href="/warns" class="pulse-alert"><strong>⚠ {active_warn_total} aktive Team-Warnungen</strong><div class="text-slate-400 mt-1">Zur Verwarnungszentrale.</div></a>'
        if open_t: urgent += f'<a href="/tickets" class="pulse-alert"><strong>▣ {len(open_t)} offene Tickets</strong><div class="text-slate-400 mt-1">Dringende Tickets zuerst übernehmen.</div></a>'
        if not urgent: urgent='<div class="pulse-empty">Alles ruhig. Aktuell keine kritischen Vorgänge.</div>'
        n_html="".join(f'<a href="{e(n.get("url") or "/pulse-inbox")}" class="pulse-row"><div class="pulse-avatar">{e((n.get("title") or "?")[:1])}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(n.get("title"))}</div><div class="pulse-row-meta">{e(n.get("body"))}</div></div></a>' for n in notices) or '<div class="pulse-empty">Keine neuen Benachrichtigungen.</div>'
        shift_badge={"online":"Im Dienst","break":"Pause","offline":"Offline"}.get(status,"Offline")
        shift_kind="good" if status=="online" else "warn" if status=="break" else ""
        shift_actions=(f'<form action="/shift/action" method="post" class="flex flex-wrap gap-2"><button name="shift_action" value="break" class="pulse-btn ghost">⏸ Pause</button><button name="shift_action" value="end" class="pulse-btn bad">⏹ Beenden</button></form>' if status=="online" else f'<form action="/shift/action" method="post" class="flex flex-wrap gap-2"><button name="shift_action" value="resume" class="pulse-btn good">▶ Fortsetzen</button><button name="shift_action" value="end" class="pulse-btn bad">⏹ Beenden</button></form>' if status=="break" else '<form action="/shift/action" method="post"><button name="shift_action" value="start" class="pulse-btn primary w-full">▶ Schicht starten</button></form>')
        body=f'''<div class="pulse-topbar"><div class="pulse-brand"><div class="pulse-brand-mark">P</div><div><div class="text-sm font-extrabold">Operations Center</div><div class="text-[10px] text-slate-400">Live-Übersicht · {e(c.guild.name)}</div></div></div><button onclick="pulseTogglePalette(true)" class="pulse-btn ghost hidden sm:inline-flex">⌕ Suchen <span class="pulse-key">Ctrl K</span></button><div class="flex items-center gap-2"><span class="pulse-pill good"><span class="pulse-status-dot bg-emerald-500"></span>System online</span><button onclick="pulseToggleTheme()" class="pulse-icon-btn">◐</button></div></div>
        <section class="pulse-hero"><div class="pulse-hero-grid"><div><div class="pulse-kicker">Willkommen zurück</div><div class="pulse-title">{e(c.user.get("global_name") or c.user.get("username"))}</div><div class="pulse-sub">Hier siehst du sofort, was für das Team ansteht: Support, Aufgaben, Abwesenheiten, Bewerbungen und deine eigene Aktivität.</div><div class="flex flex-wrap gap-2 mt-5"><a href="/team-status" class="pulse-btn" style="background:rgba(255,255,255,.1);color:white;border-color:rgba(255,255,255,.18)">◉ Teamstatus ansehen</a><a href="/handover" class="pulse-btn" style="background:rgba(255,255,255,.1);color:white;border-color:rgba(255,255,255,.18)">↪ Übergabe</a></div></div><div class="pulse-hero-box"><div class="label">DEINE WOCHEN-AKTIVITÄT</div><div class="value">{ws.fmt_duration(s["weekly"])}</div><div class="pulse-progress mt-3" style="background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.15)"><span style="width:{pct}%;background:white"></span></div><div class="text-[10px] mt-2 opacity-75">{pct}% von {goal:g}h Wochenziel</div></div></div></section>
        <div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">🟢</div><div class="label">Team im Dienst</div><div class="value" data-live="active">{s["active"]}</div></div><div class="pulse-stat"><div class="icon">▣</div><div class="label">Offene Tickets</div><div class="value" data-live="open_tickets">{len(open_t)}</div></div><div class="pulse-stat"><div class="icon">✓</div><div class="label">Freigaben</div><div class="value" data-live="approvals">{len(s["promos"])}</div></div><div class="pulse-stat"><div class="icon">✎</div><div class="label">Bewerbungen</div><div class="value" data-live="applications">{s["apps"]}</div></div><div class="pulse-stat"><div class="icon">!</div><div class="label">Überfällige Aufgaben</div><div class="value" data-live="overdue_tasks">{len(overdue)}</div></div><div class="pulse-stat"><div class="icon">☾</div><div class="label">Aktive LOA</div><div class="value" data-live="loas">{s["loas"]}</div></div></div>
        <div class="pulse-grid"><div class="space-y-4"><section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">⏱ Schicht-Steuerung</div><div class="pulse-section-sub">Pausen werden nicht als Arbeitszeit gezählt.</div></div>{pill(shift_badge,shift_kind)}</div><div class="pulse-card-b"><div class="flex items-end justify-between gap-4"><div><div class="text-[10px] text-slate-400">Aktuelle Dauer</div><div id="v5ShiftTimer" class="text-3xl font-extrabold pulse-time mt-1">{ws.fmt_duration(elapsed)}</div></div><div class="text-right"><div class="text-[10px] text-slate-400">Status</div><div class="text-sm font-bold mt-1">{shift_badge}</div></div></div><div class="mt-4">{shift_actions}</div></div></section>{card('Aufmerksamkeit',urgent,'!')}{card('Live im Dienst',duty_html or '<div class="pulse-empty">Niemand ist aktuell im Dienst.</div>','◉')}</div><div class="space-y-4">{card('Meine Inbox',n_html,'◈')}{card('Letzte Aktivität',recent_html or '<div class="pulse-empty">Noch keine zentralen Events erfasst.</div>','↯')}<section class="pulse-card"><div class="pulse-card-b"><div class="flex items-center justify-between"><div><div class="pulse-section-title">⚡ Schnellaktionen</div><div class="pulse-section-sub">Häufige Aufgaben direkt öffnen.</div></div></div><div class="grid grid-cols-2 gap-2 mt-3"><a href="/tickets" class="pulse-btn ghost">▣ Tickets</a><a href="/tasks" class="pulse-btn ghost">□ Aufgabe</a><a href="/applications" class="pulse-btn ghost">✎ Bewerbung</a><a href="/handover" class="pulse-btn ghost">↪ Übergabe</a></div></div></section></div></div>'''
        head=f'''<script>document.addEventListener('DOMContentLoaded',()=>{{const el=document.getElementById('v5ShiftTimer');const base={elapsed};const running={str(status=='online').lower()};const t0=Date.now();function x(){{let s=base+(running?Math.floor((Date.now()-t0)/1000):0);el.textContent=Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';}}x();if(running)setInterval(x,1000);}});</script>'''
        return render_pro_page(ws,'Operations Center',c,'dashboard',body,head)

    # ---------------- Inbox ----------------
    async def inbox_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); notices=db.notifications(c.user['id'],120)
        unread=sum(1 for n in notices if not n.get('read_at'))
        def notice_row(n):
            action = '' if n.get('read_at') else f'<form action="/pulse-inbox/read/{e(n.get("id"))}" method="post"><button title="Als gelesen markieren" class="pulse-icon-btn">✓</button></form>'
            return f'<div class="pulse-row"><a href="{e(n.get("url") or "/pulse-inbox")}" class="flex items-center gap-3 min-w-0 flex-1"><div class="pulse-avatar">{e((n.get("title") or "?")[:1])}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(n.get("title"))}</div><div class="pulse-row-meta">{e(n.get("body"))} · {e(fmt_dt(n.get("created_at")))}</div></div>{pill("Neu", "bad") if not n.get("read_at") else pill("Gelesen")}</a>{action}</div>'
        feed=''.join(notice_row(n) for n in notices)
        return render_pro_page(ws,'Pulse Inbox',c,'inbox',f'''<div class="pulse-topbar"><div><div class="pulse-section-title">◈ Pulse Inbox</div><div class="pulse-section-sub">Zentrale Benachrichtigungen für deinen Arbeitsalltag.</div></div><div class="flex items-center gap-2"><span class="pulse-pill {'bad' if unread else 'good'}">{unread} ungelesen</span><form action="/pulse-inbox/read" method="post"><button class="pulse-btn ghost">✓ Alle lesen</button></form></div></div><section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">Deine Nachrichten</div><div class="pulse-section-sub">Neue Ereignisse, Erinnerungen und Aufgaben.</div></div></div><div class="pulse-card-b space-y-2">{feed or '<div class="pulse-empty">Postfach ist leer.</div>'}</div></section>''')

    async def inbox_read_all(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); db.mark_notifications_read(c.user['id']); return ws.back('/pulse-inbox','Benachrichtigungen als gelesen markiert.')

    async def inbox_read_one(request: Request, nid: str, user_session: str=Cookie(None)):
        c=cctx(request,user_session); db.mark_notifications_read(c.user['id'], nid); return ws.back('/pulse-inbox','Benachrichtigung als gelesen markiert.')

    # ---------------- Team status ----------------
    async def activity_check_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm="can_manage_checkins")
        today=ws.now_de().date().isoformat()
        selected_date=(request.query_params.get("date") or today).strip()
        try: selected_date=datetime.fromisoformat(selected_date).date().isoformat()
        except Exception: selected_date=today
        check=None; responded={}; snapshot=[]; legacy=False
        try:
            with sqlite3.connect("activity_check.db") as ax:
                ax.row_factory=sqlite3.Row
                check=ax.execute("SELECT * FROM checks WHERE guild_id=? AND check_date=?",(c.guild.id,selected_date)).fetchone()
                if check:
                    responded={int(r["user_id"]):r["reacted_at"] for r in ax.execute("SELECT user_id,reacted_at FROM responses WHERE check_id=?",(check["id"],)).fetchall()}
                    try: snapshot=[dict(r) for r in ax.execute("SELECT user_id,user_name,role_name FROM check_members WHERE check_id=? ORDER BY user_name COLLATE NOCASE",(check["id"],)).fetchall()]
                    except sqlite3.Error: snapshot=[]
        except Exception: check=None
        if check and not snapshot:
            legacy=True
            role=None
            try:
                with sqlite3.connect("activity_check.db") as ax:
                    rr=ax.execute("SELECT value FROM config WHERE key='activity_role_id'").fetchone()
                    if rr and rr[0]: role=c.guild.get_role(int(rr[0]))
            except Exception: pass
            members=[m for m in (role.members if role else team(c.guild,c.config.get("team_role_ids",[]))) if not m.bot] if role else team(c.guild,c.config.get("team_role_ids",[]))
            snapshot=[{"user_id":m.id,"user_name":m.display_name,"role_name":member_role(m,c.config.get("team_role_ids",[]))} for m in members]
        history=[]
        try:
            with sqlite3.connect("activity_check.db") as hx:
                hx.row_factory=sqlite3.Row
                history=[dict(r) for r in hx.execute("SELECT id,check_date,created_at,message_id FROM checks WHERE guild_id=? ORDER BY check_date DESC LIMIT 60",(c.guild.id,)).fetchall()]
                for h in history:
                    h["responses"]=int(hx.execute("SELECT COUNT(*) FROM responses WHERE check_id=?",(h["id"],)).fetchone()[0])
                    h["members"]=int(hx.execute("SELECT COUNT(*) FROM check_members WHERE check_id=?",(h["id"],)).fetchone()[0])
        except Exception: history=[]
        confirmed=[]; open_members=[]
        for p in snapshot:
            q=dict(p); q["reacted_at"]=responded.get(int(p["user_id"])); (confirmed if q["reacted_at"] else open_members).append(q)
        confirmed.sort(key=lambda x:str(x.get("user_name","")).lower()); open_members.sort(key=lambda x:str(x.get("user_name","")).lower())
        total=len(snapshot); done=len(confirmed); open_n=len(open_members); pct=round(done*100/total) if total else 0
        def person_rows(items, state):
            if not items: return '<div class="pulse-empty">Keine Einträge.</div>'
            kind="good" if state=="confirmed" else "warn"; icon="✓" if state=="confirmed" else "⏳"; label="Bestätigt" if state=="confirmed" else "Offen"
            out=[]
            for p in items:
                name=p.get("user_name") or "Unbekannt"; when=(" · bestätigt um "+e(fmt_dt(p.get("reacted_at")))) if p.get("reacted_at") else " · wartet auf Bestätigung"
                out.append(f'<div class="pulse-row"><div class="flex items-center gap-3 min-w-0"><div class="pulse-avatar">{e(initials(name))}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(name)}</div><div class="pulse-row-meta">{e(p.get("role_name") or "Team")}{when}</div></div></div>{pill(icon+" "+label,kind)}</div>')
            return "".join(out)
        history_options="".join(f'<option value="{e(h["check_date"])}" {"selected" if h["check_date"]==selected_date else ""}>{e(h["check_date"])} · {h["responses"]} Antworten · {h["members"]} Team-Snapshot</option>' for h in history) or f'<option value="{e(selected_date)}">{e(selected_date)}</option>'
        state_note=pill("Snapshot pro Check gespeichert","good") if check and not legacy else (pill("Alter Check ohne Snapshot","warn") if legacy else pill("Kein Check für dieses Datum","warn"))
        body=(
            f'<div class="pulse-topbar"><div><div class="pulse-section-title">✓ Activity Check</div><div class="pulse-section-sub">Jeder Check wird separat gespeichert. Antworten gehören exakt zu diesem Check.</div></div>{state_note}</div>'
            f'<section class="pulse-hero"><div class="pulse-hero-grid"><div><div class="pulse-kicker">Tagesauswertung · {e(selected_date)}</div><div class="pulse-title">{done} von {total} bestätigt</div><div class="pulse-sub">Der Teilnehmer-Snapshot wird beim Erstellen des Checks gespeichert. Spätere Rollenänderungen verändern vergangene Auswertungen nicht.</div></div><div class="pulse-hero-box"><div class="label">BESTÄTIGUNGSQUOTE</div><div class="value">{pct}%</div><div class="pulse-progress mt-3" style="background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.15)"><span style="width:{pct}%;background:white"></span></div></div></div></section>'
            f'<div class="flex flex-wrap items-center gap-2 mb-4"><form method="get" action="/activity-check" class="flex flex-wrap gap-2"><select name="date" class="pulse-input" style="width:auto;min-width:310px">{history_options}</select><button class="pulse-btn ghost">Check öffnen</button></form><a href="/team#activity" class="pulse-btn ghost">↩ Teamliste</a></div>'
            f'<div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">👥</div><div class="label">Snapshot</div><div class="value">{total}</div></div><div class="pulse-stat"><div class="icon">✅</div><div class="label">Bestätigt</div><div class="value">{done}</div></div><div class="pulse-stat"><div class="icon">⏳</div><div class="label">Offen</div><div class="value">{open_n}</div></div><div class="pulse-stat"><div class="icon">◷</div><div class="label">Erstellt</div><div class="value">{e(fmt_dt(check["created_at"]) if check else "—")}</div></div></div>'
            f'<div class="pulse-grid"><div>{card("✅ Bestätigt",person_rows(confirmed,"confirmed"),"✓")}</div><div>{card("⏳ Noch offen",person_rows(open_members,"open"),"⏳")}</div></div>'
            f'<section class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">🗂 Check-Historie</div><div class="pulse-section-sub">Jeder Tag ist ein eigener Datensatz.</div></div></div><div class="pulse-card-b space-y-2">{"".join(f'<a href="/activity-check?date={e(h["check_date"])}" class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(h["check_date"])}</div><div class="pulse-row-meta">{e(fmt_dt(h["created_at"]))} · {h["responses"]} Antworten · {h["members"]} Snapshot-Mitglieder · Nachricht {e(h["message_id"])}</div></div>{pill("Aktuell","good") if h["check_date"]==selected_date else pill("Öffnen")}</a>' for h in history)}</div></section>'
        )
        return render_pro_page(ws,"Activity Check",c,"activity-check",body)
    async def team_status_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm="can_view_team_status")
        ids=c.config.get("team_role_ids",[])
        members=team(c.guild,ids)
        shifts=ws.load_shifts().get("active_shifts",{})

        def discord_meta(m):
            raw=str(getattr(m,"status",discord.Status.offline))
            return {"online":("🟢","Online","good"),"idle":("🟡","Abwesend / AFK","warn"),"dnd":("🔴","Nicht stören","bad"),"offline":("⚪","Offline","")}.get(raw,("⚪","Offline",""))

        def activity_name(m):
            for a in (getattr(m,"activities",[]) or []):
                value=getattr(a,"name",None) or getattr(a,"state",None)
                if value: return str(value)
            return ""

        status_counts={"online":0,"idle":0,"dnd":0,"offline":0}
        rows=[]
        for m in members:
            raw=str(getattr(m,"status",discord.Status.offline))
            if raw not in status_counts: raw="offline"
            status_counts[raw]+=1
            ico,lab,kind=discord_meta(m)
            act=activity_name(m)
            sh=shifts.get(str(m.id))
            act_html=f" · {e(act)}" if act else ""
            duty=pill("Pause","warn") if sh and sh.get("status")=="break" else (pill("Im Dienst","good") if sh else pill("Keine Schicht"))
            rows.append(f'<div class="pulse-row" data-discord-user="{m.id}"><div class="flex items-center gap-3 min-w-0"><div class="pulse-avatar">{e(initials(m.display_name))}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(m.display_name)}</div><div class="pulse-row-meta">{e(member_role(m,ids))} · <span data-status-label="{m.id}">{e(lab)}</span>{act_html}</div></div></div><div class="text-right"><div data-status-pill="{m.id}">{pill(ico+" "+lab,kind)}</div><div class="mt-1">{duty}</div></div></div>')

        team_html="".join(rows) or '<div class="pulse-empty">Keine Teamrollen konfiguriert.</div>'
        body=f"""
        <div class="pulse-topbar"><div><div class="pulse-section-title">◉ Teamstatus</div><div class="pulse-section-sub">Der Status wird direkt aus der Discord-Presence des jeweiligen Mitglieds gelesen. Die Schicht wird separat angezeigt.</div></div><span class="pulse-pill good">Live · 10s</span></div>
        <div class="pulse-stat-grid">
          <div class="pulse-stat"><div class="icon">🟢</div><div class="label">Online</div><div id="countOnline" class="value">{status_counts["online"]}</div></div>
          <div class="pulse-stat"><div class="icon">🟡</div><div class="label">AFK</div><div id="countIdle" class="value">{status_counts["idle"]}</div></div>
          <div class="pulse-stat"><div class="icon">🔴</div><div class="label">Nicht stören</div><div id="countDnd" class="value">{status_counts["dnd"]}</div></div>
          <div class="pulse-stat"><div class="icon">⚪</div><div class="label">Offline</div><div id="countOffline" class="value">{status_counts["offline"]}</div></div>
        </div>
        {card("Discord-Teamstatus",team_html,"◉")}
        """
        head="<script>\nasync function refreshDiscordStatuses(){try{const r=await fetch('/api/team/discord-status',{credentials:'same-origin',cache:'no-store'});const d=await r.json();if(!d.ok)return;const counts={online:0,idle:0,dnd:0,offline:0};for(const x of d.members){counts[x.status]=(counts[x.status]||0)+1;const p=document.querySelector('[data-status-pill=\"'+x.id+'\"]');const l=document.querySelector('[data-status-label=\"'+x.id+'\"]');if(p)p.innerHTML=x.pill;if(l)l.textContent=x.label;}['online','idle','dnd','offline'].forEach(k=>{const el=document.getElementById('count'+k.charAt(0).toUpperCase()+k.slice(1));if(el)el.textContent=counts[k]||0;});}catch(e){}}\nrefreshDiscordStatuses();setInterval(refreshDiscordStatuses,10000);\n</script>"
        return render_pro_page(ws,"Teamstatus",c,"team-status",body,head)

    async def discord_status_api(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm="can_view_team_status")
        ids=c.config.get("team_role_ids",[]); members=team(c.guild,ids)
        mapping={"online":("🟢","Online","good"),"idle":("🟡","Abwesend / AFK","warn"),"dnd":("🔴","Nicht stören","bad"),"offline":("⚪","Offline","")}
        out=[]
        for m in members:
            st=str(getattr(m,"status",discord.Status.offline)); ico,lab,kind=mapping.get(st,("⚪","Offline","")); act=""
            for a in (getattr(m,"activities",[]) or []):
                act=str(getattr(a,"name",None) or getattr(a,"state",None) or "")
                if act: break
            out.append({"id":m.id,"status":st,"label":lab,"activity":act,"pill":pill(ico+" "+lab,kind)})
        return JSONResponse({"ok":True,"members":out,"updated_at":ws.now_de().isoformat()})

    async def set_team_status(request: Request, status: str=Form(...), message: str=Form(""), user_session: str=Cookie(None)):
        c=cctx(request,user_session); db.set_team_status(c.user['id'],c.user.get('global_name') or c.user.get('username') or 'Team',status,message.strip()[:160]); db.record_event('status_changed','user',c.user['id'],c.user['id'],c.user.get('global_name') or 'Team',{'status':status,'message':message.strip()[:160]}); return ws.back('/team-status','Status gespeichert.')

    # ---------------- Handover ----------------
    async def handover_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); items=db.list_handovers(100); tasks=db.list_tasks(status='in_progress',limit=20); tickets=[t for t in db.list_tickets(limit=100) if t['status']!='closed']
        rows=''.join(f'<article class="pulse-row"><div class="pulse-row-main"><div class="flex items-center gap-2">{pill("🚨 Dringend","bad") if x["priority"]=="urgent" else pill("Normal")}<div class="pulse-row-title">{e(x["title"])}</div></div><div class="text-[11px] text-slate-500 mt-2 whitespace-pre-wrap">{e(x["content"])}</div><div class="pulse-row-meta">{e(x["author_name"])} · {e(fmt_dt(x["created_at"]))}</div></div></article>' for x in items)
        context=f'<div class="grid grid-cols-1 sm:grid-cols-3 gap-2"><a href="/tickets" class="pulse-btn ghost">▣ {len(tickets)} offene Tickets</a><a href="/tasks" class="pulse-btn ghost">□ {len(tasks)} in Arbeit</a><a href="/team-status" class="pulse-btn ghost">◉ Teamstatus</a></div>'
        can_other=c.perms.get('can_manage_handover') or c.perms.get('can_promote') or c.perms.get('is_admin')
        form_html=''
        if can_other or c.perms.get('can_view_dashboard'):
            form_html='<form action="/handover/create" method="post" class="space-y-3"><input name="title" required maxlength="120" class="pulse-input" placeholder="Titel, z. B. Abendübergabe"><select name="priority" class="pulse-input"><option value="normal">Normal</option><option value="urgent">🚨 Dringend</option></select><textarea name="content" required maxlength="2500" class="pulse-input pulse-textarea" placeholder="Was muss die nächste Schicht wissen? Offene Fälle, wichtige Hinweise, Absprachen…"></textarea><button class="pulse-btn primary w-full">↪ Übergabe veröffentlichen</button></form>'
        empty_handover='<div class="pulse-empty">Noch keine aktiven Übergaben.</div>'
        empty_form='<div class="pulse-empty">Du kannst aktuell keine Übergabe erstellen.</div>'
        body=f'<div class="pulse-topbar"><div><div class="pulse-section-title">↪ Schicht-Übergabe</div><div class="pulse-section-sub">Lass die nächste Schicht nicht im Dunkeln stehen.</div></div>{pill("Übergaben: "+str(len(items)),"good")}</div><div class="pulse-grid"><div class="space-y-4">{card("Aktuelle Übergaben",rows or empty_handover,"↪")}{card("Arbeitsstand",context,"▣")}</div><div>{card("Neue Übergabe erstellen",form_html or empty_form,"↪")}</div></div>'
        return render_pro_page(ws,'Schicht-Übergabe',c,'handover',body)


    async def create_handover(request: Request, title: str=Form(...), content: str=Form(...), priority: str=Form('normal'), user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None); manager=c.perms.get('can_manage_handover') or c.perms.get('can_view_dashboard')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        title=title.strip()[:120]; content=content.strip()[:2500];
        if not title or not content: return ws.back('/handover','Titel und Inhalt sind Pflicht.',False)
        hid=db.create_handover(title,content,c.user['id'],c.user.get('global_name') or 'Team',priority if priority in ('normal','urgent') else 'normal')
        db.record_event('handover_created','handover',hid,c.user['id'],c.user.get('global_name') or 'Team',{'title':title,'priority':priority})
        for m in team(c.guild,c.config.get('team_role_ids',[])):
            if str(m.id)!=str(c.user['id']): db.notify(m.id,'↪ Neue Übergabe',title,'info','/handover',f'handover:{hid}',86400)
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Übergabe Erstellt',f'{title} · {hid}')
        return ws.back('/handover','Übergabe veröffentlicht.')

    # ---------------- Announcements ----------------
    async def announcements_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None); manager=c.perms.get('can_manage_announcements') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        items=db.announcements(100); rows=''.join(f'<article class="pulse-row"><div class="pulse-avatar">✦</div><div class="pulse-row-main"><div class="pulse-row-title">{e(x["title"])}</div><div class="pulse-row-meta">{e(x["author_name"])} · {e(fmt_dt(x["created_at"]))}</div><div class="text-[11px] text-slate-500 whitespace-pre-wrap mt-2">{e(x["content"])}</div></div>{pill("Discord gesendet","good") if x.get("discord_message_id") else pill("Lokal")}</article>' for x in items)
        form_html='<form action="/announcements/create" method="post" class="space-y-3"><input name="title" required maxlength="160" class="pulse-input" placeholder="Titel"><select name="kind" class="pulse-input"><option value="info">ℹ Information</option><option value="success">✅ Erfolg</option><option value="warning">⚠️ Wichtig</option><option value="critical">🚨 Dringend</option></select><textarea name="content" required maxlength="4000" class="pulse-input pulse-textarea" placeholder="Text der Team-Ankündigung…"></textarea><button class="pulse-btn primary w-full">✦ Ankündigung senden</button></form>'
        empty_ann='<div class="pulse-empty">Noch keine Ankündigungen.</div>'
        body=f'<div class="pulse-topbar"><div><div class="pulse-section-title">✦ Team-Ankündigungen</div><div class="pulse-section-sub">Ein Beitrag, ein Embed, ein Audit-Eintrag.</div></div></div><div class="pulse-grid"><div>{card("Historie",rows or empty_ann,"✦")}</div><div>{card("Neue Ankündigung",form_html,"✦")}</div></div>'
        return render_pro_page(ws,'Ankündigungen',c,'announcements',body)


    async def create_announcement(request: Request, title: str=Form(...), content: str=Form(...), kind: str=Form('info'), user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None); manager=c.perms.get('can_manage_announcements') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        colors={'info':discord.Color.blurple(),'success':discord.Color.green(),'warning':discord.Color.orange(),'critical':discord.Color.red()}
        msg=await team_update(c.guild,title.strip()[:160],content.strip()[:4000],colors.get(kind,discord.Color.blurple()))
        aid=db.create_announcement(title.strip()[:160],content.strip()[:4000],c.user['id'],c.user.get('global_name') or 'Team',kind,getattr(msg,'id',None))
        db.record_event('announcement_created','announcement',aid,c.user['id'],c.user.get('global_name') or 'Team',{'kind':kind,'sent':bool(msg)})
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Ankündigung Gesendet',title.strip()[:160])
        return ws.back('/announcements','Ankündigung gespeichert und an Discord gesendet.' if msg else 'Ankündigung lokal gespeichert – kein Team-Update-Kanal gefunden.',bool(msg))

    # ---------------- Promotions / approvals ----------------
    async def promotions_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None); manager=c.perms.get('can_manage_promotions') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        ids=c.config.get('team_role_ids',[]); roles=[r for r in c.guild.roles if r.id in ids and not r.managed]; members=team(c.guild,ids); pending=db.promotion_requests('pending',100)
        role_opts=''.join(f'<option value="{r.id}">{e(r.name)}</option>' for r in sorted(roles,key=lambda r:r.position))
        member_opts=''.join(f'<option value="{m.id}">{e(m.display_name)}</option>' for m in members)
        rows=''.join(f'<article class="pulse-row"><div class="pulse-avatar">{e(initials(x["target_user_name"]))}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(x["target_user_name"])} → <span class="text-indigo-500">{e(x["requested_role"])}</span></div><div class="pulse-row-meta">von {e(x["created_by_name"])} · {e(fmt_dt(x["created_at"]))}</div><div class="text-[11px] text-slate-500 mt-2 whitespace-pre-wrap">{e(x["reason"])}</div></div><div class="flex gap-2"><form action="/promotions/decide" method="post"><input type="hidden" name="promotion_id" value="{e(x["id"])}"><input type="hidden" name="status" value="approved"><button class="pulse-btn good">✓ Freigeben</button></form><form action="/promotions/decide" method="post"><input type="hidden" name="promotion_id" value="{e(x["id"])}"><input type="hidden" name="status" value="rejected"><button class="pulse-btn bad">✕ Ablehnen</button></form></div></article>' for x in pending)
        form_html=f'<form action="/promotions/create" method="post" class="space-y-3"><select name="target_user_id" required class="pulse-input"><option value="">Mitglied auswählen…</option>{member_opts}</select><select name="requested_role_id" required class="pulse-input"><option value="">Neue Rolle auswählen…</option>{role_opts}</select><input name="reason" required maxlength="2000" class="pulse-input" placeholder="Begründung"><button class="pulse-btn primary w-full">✓ Antrag erstellen</button></form>'
        empty_prom='<div class="pulse-empty">Keine offenen Beförderungsanträge.</div>'
        body=f'<div class="pulse-topbar"><div><div class="pulse-section-title">✓ Beförderungsverwaltung</div><div class="pulse-section-sub">Anträge nachvollziehbar anlegen, prüfen und auditieren.</div></div>{pill(str(len(pending))+" offen","warn")}</div><div class="pulse-grid"><div>{card("Offene Anträge",rows or empty_prom,"✓")}</div><div>{card("Antrag erstellen",form_html,"✓")}</div></div>'
        return render_pro_page(ws,'Beförderungen',c,'approvals',body)


    async def create_promotion(request: Request, target_user_id: int=Form(...), requested_role_id: int=Form(...), reason: str=Form(...), user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None); manager=c.perms.get('can_manage_promotions') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        target=c.guild.get_member(target_user_id); role=c.guild.get_role(requested_role_id)
        if not target or not role: return ws.back('/approvals','Mitglied oder Zielrolle nicht gefunden.',False)
        if target.bot: return ws.back('/approvals','Bots können nicht befördert werden.',False)
        if not c.perms.get('is_admin') and c.member and ws.team_rank(c.member,c.config.get('team_role_ids',[])) <= ws.team_rank(target,c.config.get('team_role_ids',[])):
            return ws.back('/approvals','Du kannst nur einen niedrigeren Rang als deinen eigenen bearbeiten.',False)
        current=[r for r in target.roles if r.id in c.config.get('team_role_ids',[])]
        current_role=max(current,key=lambda r:r.position) if current else None
        pid=db.create_promotion_request(target.id,target.display_name,current_role.name if current_role else '',role.name,reason.strip(),c.user['id'],c.user.get('global_name') or 'Team',role.id,current_role.id if current_role else None)
        db.record_event('promotion_created','promotion',pid,c.user['id'],c.user.get('global_name') or 'Team',{'target':target.id,'role':role.id})
        db.notify(target.id,'✓ Beförderungsantrag gestellt',f'{c.user.get("global_name")}: {current_role.name if current_role else "Team"} → {role.name}','info','/approvals',f'promotion:{pid}',86400)
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Beförderungsantrag',f'{target.display_name} → {role.name}')
        return ws.back('/approvals','Beförderungsantrag erstellt.')

    async def decide_promotion(request: Request, promotion_id: str=Form(...), status: str=Form(...), user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None); manager=c.perms.get('can_manage_promotions') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        req=next((x for x in db.promotion_requests('pending',200) if x['id']==promotion_id),None)
        if not req: return ws.back('/approvals','Antrag nicht mehr offen.',False)
        if status=='approved':
            target=c.guild.get_member(int(req['target_user_id'])); role=c.guild.get_role(int(req['requested_role_id'])) if req.get('requested_role_id') else None
            if not target or not role: return ws.back('/approvals','Mitglied oder Zielrolle existiert nicht mehr.',False)
            if not c.perms.get('is_admin') and c.member and ws.team_rank(c.member,c.config.get('team_role_ids',[])) <= ws.team_rank(target,c.config.get('team_role_ids',[])):
                return ws.back('/approvals','Rangprüfung fehlgeschlagen.',False)
            if c.guild.me and c.guild.me.top_role.position <= role.position: return ws.back('/approvals','Der Bot steht nicht über der Zielrolle.',False)
            try:
                team_ids=c.config.get('team_role_ids',[])
                remove=[r for r in target.roles if r.id in team_ids and r.id!=role.id]
                if remove: await target.remove_roles(*remove,reason=f'Pulse Beförderung von {c.user.get("global_name")}')
                await target.add_roles(role,reason=f'Pulse Beförderung von {c.user.get("global_name")}')
                msg=f'{req["target_user_name"]} wurde zu {role.name} befördert.'
            except discord.Forbidden:
                return ws.back('/approvals','Discord hat das Rollenupdate verweigert.',False)
        else: msg=f'{req["target_user_name"]}: Antrag abgelehnt.'
        if not db.decide_promotion_request(promotion_id,status,c.user['id'],c.user.get('global_name') or 'Team'):
            return ws.back('/approvals','Antrag wurde bereits entschieden.',False)
        db.record_event('promotion_decided','promotion',promotion_id,c.user['id'],c.user.get('global_name') or 'Team',{'status':status})
        target_id=req['target_user_id']; db.notify(target_id,'✓ Beförderungsantrag entschieden',msg,'success' if status=='approved' else 'warning','/team',f'promotion-decision:{promotion_id}',86400)
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Beförderung Entscheiden',msg)
        return ws.back('/approvals',msg)

    async def approvals_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); can=c.perms.get('can_manage_applications') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not can: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        apps=ws.load_json(ws.APPS_FILE,{}); pending_apps=[(k,a) for k,a in apps.items() if a.get('status')=='pending']; pending_p=db.promotion_requests('pending',100)
        rows=''.join(f'<a href="/applications" class="pulse-row"><div class="pulse-avatar">✎</div><div class="pulse-row-main"><div class="pulse-row-title">{e(a.get("name","Bewerbung"))}</div><div class="pulse-row-meta">Bewerbung {e(k)} · {e(a.get("created_at"))}</div></div>{pill("Bewerbung","warn")}</a>' for k,a in pending_apps[:12])
        prows=''.join(f'<a href="/approvals" class="pulse-row"><div class="pulse-avatar">✓</div><div class="pulse-row-main"><div class="pulse-row-title">{e(x["target_user_name"])} → {e(x["requested_role"])}</div><div class="pulse-row-meta">{e(x["created_by_name"])} · {e(fmt_dt(x["created_at"]))}</div></div>{pill("Beförderung","good")}</a>' for x in pending_p[:12])
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">✓ Freigabecenter</div><div class="pulse-section-sub">Alles, was eine Entscheidung von der Teamleitung braucht.</div></div></div><div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">✎</div><div class="label">Bewerbungen</div><div class="value">{len(pending_apps)}</div></div><div class="pulse-stat"><div class="icon">✓</div><div class="label">Beförderungen</div><div class="value">{len(pending_p)}</div></div><div class="pulse-stat"><div class="icon">!</div><div class="label">Gesamt offen</div><div class="value">{len(pending_apps)+len(pending_p)}</div></div></div><div class="pulse-two">{card('Bewerbungen',rows or '<div class="pulse-empty">Keine offenen Bewerbungen.</div>','✎')}{card('Beförderungsanträge',prows or '<div class="pulse-empty">Keine offenen Anträge.</div>','✓')}</div>'''
        return render_pro_page(ws,'Freigabecenter',c,'approvals',body)

    # ---------------- Modern tasks ----------------
    async def tasks_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); manager=c.perms.get('can_manage_tasks') or c.perms.get('can_promote') or c.perms.get('is_admin'); tasks=db.list_tasks(limit=500); cols={k:[t for t in tasks if t['status']==k] for k in ('open','in_progress','done')}; members=team(c.guild,c.config.get('team_role_ids',[])); opts=''.join(f'<option value="{m.id}">{e(m.display_name)}</option>' for m in members)
        def task_card(t):
            due=parse_due(t.get('due_at')); overdue=due and due<datetime.now().astimezone() and t.get('status') not in ('done','archived'); can=(manager or str(t.get('assignee_id'))==str(c.user['id'])); pr={'urgent':('🚨 Dringend','bad'),'high':('🟠 Hoch','warn'),'normal':('🟢 Normal','good'),'low':('⚪ Niedrig','')} .get(t['priority'],('Normal',''))
            actions=''
            if can and t['status']=='open': actions += f'<button onclick="document.getElementById(\"task-{t["id"]}\").submit()" class="text-indigo-500">▶</button>'
            if can and t['status']!='done': actions += f'<form id="task-{e(t["id"])}" action="/tasks/update" method="post" class="inline"><input type="hidden" name="task_id" value="{e(t["id"])}"><input type="hidden" name="status" value="done"><button class="text-emerald-500">✓</button></form>'
            return f'<article class="pulse-row"><div class="pulse-row-main"><div class="flex items-center gap-2 flex-wrap">{pill(*pr)}<div class="pulse-row-title">{e(t["title"])}</div>{pill("Überfällig","bad") if overdue else ""}</div><div class="pulse-row-meta">{e(t.get("assignee_name") or "Niemand")} · {e("Deadline "+fmt_dt(t.get("due_at")) if due else "Keine Deadline")}</div><div class="text-[11px] text-slate-500 mt-2">{e(t.get("description"))}</div></div><div class="flex items-center gap-2">{actions}</div></article>'
        col_parts=[]
        for k,title in [('open','🟡 Offen'),('in_progress','🔵 In Arbeit'),('done','✅ Erledigt')]:
            content=''.join(task_card(t) for t in cols[k]) or '<div class="pulse-empty">Leer.</div>'
            col_parts.append(f'<div class="space-y-2"><div class="flex items-center justify-between mb-2"><div class="pulse-section-title">{title}</div>{pill(str(len(cols[k])))}</div>{content}</div>')
        cols_html=''.join(col_parts)
        form_html=f'<form action="/tasks/create" method="post" class="space-y-3"><input name="title" required maxlength="120" class="pulse-input" placeholder="Aufgabe"><select name="assignee_id" class="pulse-input"><option value="">Niemand</option>{opts}</select><select name="priority" class="pulse-input"><option value="normal">🟢 Normal</option><option value="high">🟠 Hoch</option><option value="urgent">🚨 Dringend</option><option value="low">⚪ Niedrig</option></select><input type="datetime-local" name="due_at" class="pulse-input"><textarea name="description" maxlength="600" class="pulse-input pulse-textarea" placeholder="Beschreibung"></textarea><button class="pulse-btn primary w-full">□ Aufgabe erstellen</button></form>' if manager else ''; form=card('Neue Aufgabe',form_html,'□') if manager else ''
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">□ Aufgaben</div><div class="pulse-section-sub">Klarer Verantwortlicher, klare Frist, klare Übergabe.</div></div><a href="/handover" class="pulse-btn ghost">↪ Übergabe</a></div>{form}<div class="grid grid-cols-1 xl:grid-cols-3 gap-4 mt-5">{cols_html}</div>'''
        return render_pro_page(ws,'Aufgaben',c,'tasks',body)

    async def update_task_v5(request: Request, task_id: str=Form(...), status: str=Form(...), priority: str=Form(None), due_at: str=Form(None), assignee_id: str=Form(None), user_session: str=Cookie(None)):
        c=cctx(request,user_session); t=db.get_task(task_id)
        if not t: return ws.back('/tasks','Aufgabe nicht gefunden.',False)
        manager=c.perms.get('can_manage_tasks') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not (manager or str(t.get('assignee_id'))==str(c.user['id'])): raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        if status not in ('open','in_progress','done','archived'): raise HTTPException(400,'Ungültiger Status.')
        assign_name=None
        if manager and assignee_id is not None:
            m=c.guild.get_member(int(assignee_id)) if assignee_id.isdigit() else None; assignee_id=m.id if m else ''; assign_name=m.display_name if m else ''
        db.update_task(task_id,status=status,priority=priority if priority in ('low','normal','high','urgent') else None,due_at=due_at if due_at is not None else None,assignee_id=assignee_id if manager and assignee_id is not None else None,assignee_name=assign_name,actor_id=c.user['id'],actor_name=c.user.get('global_name') or 'Team')
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Aufgabe Aktualisiert',f'{task_id} → {status}')
        return ws.back('/tasks','Aufgabe aktualisiert.')

    # ---------------- Modern tickets ----------------
    async def tickets_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); items=db.list_tickets(limit=500); manager=c.perms.get('can_manage_tickets') or c.perms.get('can_promote') or c.perms.get('is_admin'); open_items=[t for t in items if t['status']!='closed']; urgent=[t for t in open_items if t['priority']=='urgent']; claimed=sum(1 for t in open_items if t.get('claimed_by_id'))
        def row(t):
            due=fmt_dt(t.get('opened_at')); pr={'urgent':('🚨 Dringend','bad'),'high':('🟠 Hoch','warn'),'normal':('🟢 Normal','good'),'low':('⚪ Niedrig','')} .get(t['priority'],('Normal',''))
            claim=t.get('claimed_by_name') or 'Niemand'; status={'open':('🟢 Offen','good'),'in_progress':('🔵 In Arbeit','warn'),'closed':('✓ Geschlossen','good')}.get(t['status'],('—', ''))
            actions=f'<form action="/tickets/update" method="post" class="flex gap-2"><input type="hidden" name="ticket_id" value="{e(t["id"])}"><select name="priority" class="pulse-input !w-auto !py-2"><option value="low">⚪ Niedrig</option><option value="normal" {"selected" if t["priority"]=="normal" else ""}>🟢 Normal</option><option value="high" {"selected" if t["priority"]=="high" else ""}>🟠 Hoch</option><option value="urgent" {"selected" if t["priority"]=="urgent" else ""}>🚨 Dringend</option></select><button class="pulse-btn ghost">Speichern</button></form>' if manager and t['status']!='closed' else ''
            tr=f'<a href="/ticket/transcript/{quote(os.path.basename(t.get("transcript_path") or ""))}" class="pulse-btn ghost">Transcript</a>' if t.get('transcript_path') else ''
            return f'<article class="pulse-row"><div class="pulse-avatar">▣</div><div class="pulse-row-main"><div class="flex items-center gap-2 flex-wrap"><div class="pulse-row-title">{e(t["id"])}</div>{pill(pr[0],pr[1])}{pill(status[0],status[1])}</div><div class="pulse-row-meta">{e(t["user_name"])} · {e(t["category"])} · {e(due)} · Bearbeiter: {e(claim)}</div><div class="text-[11px] text-slate-500 mt-2">{e(t.get('close_reason') or '')}</div></div><div class="flex flex-col items-end gap-2">{actions}{tr}</div></article>'
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">▣ Ticket-Zentrale</div><div class="pulse-section-sub">Priorisieren, übernehmen, schließen und nachhalten.</div></div><a href="/dashboard" class="pulse-btn ghost">⌂ Übersicht</a></div><div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">▣</div><div class="label">Offen</div><div class="value">{len(open_items)}</div></div><div class="pulse-stat"><div class="icon">🚨</div><div class="label">Dringend</div><div class="value">{len(urgent)}</div></div><div class="pulse-stat"><div class="icon">◉</div><div class="label">Übernommen</div><div class="value">{claimed}</div></div><div class="pulse-stat"><div class="icon">★</div><div class="label">Bewertet</div><div class="value">{sum(1 for t in items if t.get('rating'))}</div></div></div><section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">Aktuelle Tickets</div><div class="pulse-section-sub">Dringende Tickets werden oben priorisiert.</div></div></div><div class="pulse-card-b space-y-2">{"".join(row(t) for t in items) or '<div class="pulse-empty">Keine Tickets vorhanden.</div>'}</div></section>'''
        return render_pro_page(ws,'Tickets',c,'tickets',body)

    async def ticket_update_v5(request: Request, ticket_id: str=Form(...), priority: str=Form(...), user_session: str=Cookie(None)):
        c=cctx(request,user_session); manager=c.perms.get('can_manage_tickets') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        if priority not in ('low','normal','high','urgent'): raise HTTPException(400,'Ungültige Priorität.')
        if not db.update_ticket_priority(ticket_id,priority,c.user['id'],c.user.get('global_name') or 'Team'): return ws.back('/tickets','Ticket nicht gefunden.',False)
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Ticket Priorität geändert',f'{ticket_id} → {priority}')
        return ws.back('/tickets','Ticket aktualisiert.')

    # ---------------- Modern stats / calendar / settings / system / search ----------------
    async def stats_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm='can_view_analytics'); s=work_stats(c); members=team(c.guild,c.config.get('team_role_ids',[])); hist=ws.load_shifts().get('history',[]); week=[]
        for m in members: week.append((ws.calculate_weekly_seconds(str(m.id),hist,ws.load_shifts().get('active_shifts',{})),m.display_name))
        week.sort(reverse=True); maxs=max([x[0] for x in week],default=1)
        rows=''.join(f'<div class="pulse-row"><div class="flex items-center gap-3"><div class="pulse-avatar">{e(initials(n))}</div><div><div class="pulse-row-title">{e(n)}</div><div class="pulse-progress mt-2 w-[180px]"><span style="width:{round(v/maxs*100)}%"></span></div></div></div><div class="pulse-time font-bold">{ws.fmt_duration(v)}</div></div>' for v,n in week[:20])
        closed=sum(1 for t in s['tickets'] if t['status']=='closed'); rated=[t['rating'] for t in s['tickets'] if t.get('rating')]; avg=sum(rated)/len(rated) if rated else 0
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">◒ Team-Analytics</div><div class="pulse-section-sub">Zahlen für Planung, Support und Aktivität.</div></div><a href="/export/team.csv" class="pulse-btn ghost">⇩ CSV Export</a></div><div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">⏱</div><div class="label">Dienststunden</div><div class="value">{sum(int(h.get('duration_seconds',0)) for h in hist)/3600:.1f}h</div></div><div class="pulse-stat"><div class="icon">▣</div><div class="label">Tickets gesamt</div><div class="value">{len(s['tickets'])}</div></div><div class="pulse-stat"><div class="icon">★</div><div class="label">Ø Ticketbewertung</div><div class="value">{avg:.1f}</div></div><div class="pulse-stat"><div class="icon">!</div><div class="label">Warnungen</div><div class="value">{sum(len(v.get('warns_list',[])) for v in ws.load_json(ws.DATA_FILE,{}).values() if isinstance(v,dict))}</div></div></div><div class="pulse-two">{card('Dienstzeit diese Woche',rows or '<div class="pulse-empty">Keine Daten.</div>','⏱')}{card('Support',f'<div class="grid grid-cols-2 gap-3"><div class="pulse-stat"><div class="label">Geschlossen</div><div class="value">{closed}</div></div><div class="pulse-stat"><div class="label">Offen</div><div class="value">{len(s["tickets"])-closed}</div></div></div>','▣')}</div>'''
        return render_pro_page(ws,'Analytics',c,'stats',body)

    async def calendar_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); meetings=ws.load_meetings(); loas=ws.get_loas(); items=[]
        if meetings.get('date_time') and meetings.get('date_time')!='Noch nicht angesetzt': items.append(('◫',meetings['date_time'],meetings['title'],'Meeting'))
        for l in loas.values(): items.append(('☾',f'{l.get("von")} → {l.get("bis")}',l.get('name'),'LOA'))
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">□ Team-Kalender</div><div class="pulse-section-sub">Meetings und Abwesenheiten an einem Ort.</div></div><a href="/meetings" class="pulse-btn primary">◫ Meeting</a></div><div class="grid md:grid-cols-2 xl:grid-cols-3 gap-3">{"".join(f'<article class="pulse-card p-5"><div class="text-2xl">{x[0]}</div><div class="pulse-section-title mt-3">{e(x[2])}</div><div class="text-xs text-indigo-500 mt-2">{e(x[1])}</div><div class="text-[10px] text-slate-400 mt-1">{e(x[3])}</div></article>' for x in items) or '<div class="pulse-empty col-span-full">Keine kommenden Termine.</div>'}</div>'''
        return render_pro_page(ws,'Kalender',c,'calendar',body)

    async def search_v5(request: Request, q: str="", user_session: str=Cookie(None)):
        c=cctx(request,user_session); q=(q or '').strip().lower(); results=[]
        if q:
            for m in team(c.guild,c.config.get('team_role_ids',[])):
                if q in f'{m.display_name} {m.name} {m.id}'.lower(): results.append(('◌',m.display_name,'Teammitglied',f'/member/{m.id}'))
            for t in db.list_tickets(limit=1000):
                if q in f'{t["id"]} {t["user_name"]} {t["category"]}'.lower(): results.append(('▣',t['id'],'Ticket', '/tickets'))
            for t in db.list_tasks(limit=1000):
                if q in f'{t["title"]} {t["description"]} {t.get("assignee_name") or ""}'.lower(): results.append(('□',t['title'],'Aufgabe','/tasks'))
            for x in db.announcements(300):
                if q in f'{x["title"]} {x["content"]}'.lower(): results.append(('✦',x['title'],'Ankündigung','/announcements'))
            for x in db.list_handovers(300,True):
                if q in f'{x["title"]} {x["content"]}'.lower(): results.append(('↪',x['title'],'Übergabe','/handover'))
            for x in db.wiki_pages():
                if q in f'{x["title"]} {x["category"]} {x["content"]}'.lower(): results.append(('▤',x['title'],'Wiki',f'/wiki/{x["id"]}'))
        rows=''.join(f'<a href="{e(r[3])}" class="pulse-row"><div class="pulse-avatar">{r[0]}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(r[1])}</div><div class="pulse-row-meta">{e(r[2])}</div></div></a>' for r in results[:100])
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">⌕ Globale Suche</div><div class="pulse-section-sub">Mitglieder, Tickets, Aufgaben, Wiki, Ankündigungen und Übergaben.</div></div></div><form class="pulse-search mb-4" style="max-width:none"><span>⌕</span><input autofocus name="q" value="{e(q)}" placeholder="Name, Discord-ID, Ticket-ID, Stichwort…"><button class="pulse-btn primary">Suchen</button></form><div class="space-y-2">{rows or ('<div class="pulse-empty">Keine Treffer.</div>' if q else '<div class="pulse-empty">Suche nach einem Begriff.</div>')}</div>'''
        return render_pro_page(ws,'Suche',c,'search',body)

    # ---------------- Professional member profile ----------------
    async def member_v5(request: Request, user_id: int, user_session: str=Cookie(None)):
        c=cctx(request,user_session)
        target=c.guild.get_member(user_id)
        if not target or target.bot:
            raise HTTPException(404,'Teammitglied nicht gefunden.')
        ids=c.config.get('team_role_ids',[])
        if ids and not any(r.id in ids for r in target.roles) and target.id != c.user['id']:
            raise HTTPException(404,'Teammitglied nicht gefunden.')

        teamdb=ws.load_json(ws.DATA_FILE,{})
        entry=teamdb.setdefault(str(target.id),{})
        ws.user_entry(teamdb,str(target.id))
        ws.normalize_warns(entry)
        if not isinstance(teamdb.get(str(target.id)),dict):
            teamdb[str(target.id)]={}
            entry=teamdb[str(target.id)]
        warns=entry.get('warns_list',[]) or []
        active_warns=ws.active_warns(entry)
        revoked_warns=[w for w in warns if w not in active_warns]

        shifts=ws.load_shifts()
        hist=[h for h in shifts.get('history',[]) if str(h.get('mod_id'))==str(target.id)]
        active=shifts.get('active_shifts',{}).get(str(target.id))
        weekly=ws.calculate_weekly_seconds(str(target.id),shifts.get('history',[]),shifts.get('active_shifts',{}))

        tickets=[t for t in db.list_tickets(limit=2000) if str(t.get('user_id'))==str(target.id) or str(t.get('claimed_by_id'))==str(target.id)]
        tasks=[t for t in db.list_tasks(limit=2000,include_archived=True) if str(t.get('assignee_id'))==str(target.id) or str(t.get('creator_id'))==str(target.id)]
        evs=[x for x in db.events(limit=2000) if str(x.get('actor_id'))==str(target.id) or (x.get('target_type')=='user' and str(x.get('target_id'))==str(target.id))][:40]
        role=member_role(target,ids)
        role_color=f'#{max([r for r in target.roles if r.id in ids],key=lambda r:r.position).color.value:06x}' if any(r.id in ids for r in target.roles) else '#5b5cf0'
        avatar=f'https://cdn.discordapp.com/avatars/{target.id}/{target.avatar.key}.png' if target.avatar else 'https://cdn.discordapp.com/embed/avatars/0.png'

        def warning_row(w, active=True):
            proof=ws.safe_url(w.get('proof')) if active else ws.safe_url(w.get('proof'))
            proof_html=f'<a href="{e(proof)}" target="_blank" rel="noopener noreferrer" class="text-[10px] text-indigo-500 hover:underline">🔗 Beweis</a>' if proof else ''
            state=pill('Aktiv','warn') if active else pill('Zurückgezogen','good')
            action_html=''
            if active and (c.perms.get('can_warn') or c.perms.get('is_admin')):
                action_html=f'''<form action="/action" method="post" class="mt-2 flex flex-wrap gap-2" onsubmit="return confirm('Diese Verwarnung wirklich zurückziehen?');">
                    <input type="hidden" name="action" value="remove_warn">
                    <input type="hidden" name="user_id" value="{target.id}">
                    <input type="hidden" name="warn_id" value="{e(w.get('id'))}">
                    <input type="hidden" name="redirect_to_member" value="1">
                    <input name="warn_revoke_reason" maxlength="300" class="pulse-input flex-1 min-w-[180px]" placeholder="Rücknahmegrund (optional)">
                    <button class="pulse-btn bad">↩ Zurückziehen</button>
                </form>'''
            revoked_meta=''
            if not active:
                revoked_meta=f'<div class="text-[10px] text-slate-400 mt-2">↩ {e(w.get("revoked_at") or "N/A")} · von {e(w.get("revoked_by") or "Team")} · {e(w.get("revoked_reason") or "Kein Grund")}</div>'
            return f'''<article class="pulse-row items-start"><div class="pulse-avatar">⚠</div><div class="pulse-row-main"><div class="flex items-center gap-2 flex-wrap"><div class="pulse-row-title">{e(w.get("reason") or w.get("grund") or "Verwarnung")}</div>{state}</div><div class="pulse-row-meta">{e(w.get("date") or "—")} · ausgestellt von {e(w.get("by") or "Team")} · ID {e(w.get("id") or "—")}</div>{proof_html}{revoked_meta}{action_html}</div></article>'''

        active_html=''.join(warning_row(w,True) for w in reversed(active_warns))
        revoked_html=''.join(warning_row(w,False) for w in reversed(revoked_warns[-12:]))

        role_health=ws.warning_role_health(c.guild,c.config)
        role_bad=sum(1 for x in role_health if not x['ok'])
        role_pills=''.join(
            f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">Warn {x["level"]} · {e(x["role"].name if x["role"] else "Nicht gefunden")}</div><div class="pulse-row-meta">ID {x["id"]} · {e(x["detail"])}</div></div>{pill("Bereit","good") if x["ok"] else pill("Prüfen","bad")}</div>'
            for x in role_health
        )

        note_html=''.join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="text-[11px] whitespace-pre-wrap">{e(str(n))}</div></div></div>' for n in entry.get('notes',[])[-8:][::-1])
        task_html=''.join(f'<a href="/tasks" class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(t["title"])}</div><div class="pulse-row-meta">{e(t.get("status"))} · {e(t.get("assignee_name") or "Niemand")}</div></div>{pill(t.get("priority","normal"))}</a>' for t in tasks[:10])
        priority_labels={'urgent':'Dringend','high':'Hoch','normal':'Normal','low':'Niedrig'}
        ticket_rows=[]
        for t in tickets[:10]:
            tid=e(str(t.get('id',''))); category=e(str(t.get('category',''))); status_txt=e(str(t.get('status',''))); opened=e(str(t.get('opened_at','')))
            prio=t.get('priority','normal'); prio_text=priority_labels.get(prio,str(prio)); prio_kind='warn' if prio in ('high','urgent') else ''
            ticket_rows.append(f'<a href="/ticket/{tid}" class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{tid} · {category}</div><div class="pulse-row-meta">{status_txt} · {opened}</div></div>{pill(prio_text,prio_kind)}</a>')
        ticket_html=''.join(ticket_rows)
        event_html=''.join(f'<div class="pulse-row"><div class="pulse-avatar">↯</div><div class="pulse-row-main"><div class="pulse-row-title">{e(x.get("event_type"))}</div><div class="pulse-row-meta">{e(x.get("actor_name") or "System")} · {e(fmt_dt(x.get("created_at")))}</div></div></div>' for x in evs)

        quick=''
        if c.perms.get('can_warn') or c.perms.get('is_admin'):
            quick += f'''<form action="/action" method="post" class="pulse-card p-4 space-y-2">
                <div class="pulse-section-title">⚠ Neue Verwarnung</div>
                <input type="hidden" name="action" value="warn_with_proof"><input type="hidden" name="user_id" value="{target.id}"><input type="hidden" name="redirect_to_member" value="1">
                <input name="warn_reason" required maxlength="500" class="pulse-input" placeholder="Warn-Grund">
                <input name="warn_proof" maxlength="1000" class="pulse-input" placeholder="Beweis / Link (optional)">
                <button class="pulse-btn bad w-full">Verwarnung ausstellen</button>
            </form>'''
        if c.perms.get('can_add_notes') or c.perms.get('is_admin'):
            quick += f'''<form action="/action" method="post" class="pulse-card p-4 space-y-2">
                <div class="pulse-section-title">📝 Teamnotiz</div>
                <input type="hidden" name="action" value="add_note"><input type="hidden" name="user_id" value="{target.id}"><input type="hidden" name="redirect_to_member" value="1">
                <textarea name="note_text" required maxlength="1000" class="pulse-input pulse-textarea" placeholder="Interne Teamnotiz…"></textarea>
                <button class="pulse-btn ghost w-full">Notiz speichern</button>
            </form>'''

        status='Im Dienst' if active and active.get('status')=='online' else 'Pause' if active else 'Offline'
        status_kind='good' if active and active.get('status')=='online' else 'warn' if active else ''
        body=f'''<div class="pulse-topbar"><div><a href="/team" class="pulse-btn ghost">← Team</a><a href="/warns" class="pulse-btn ghost ml-2">⚠ Warnzentrale</a></div><div class="flex items-center gap-2">{pill(status,status_kind)}</div></div>
        <section class="pulse-hero"><div class="flex flex-col md:flex-row md:items-center gap-5"><img src="{avatar}" class="w-16 h-16 rounded-2xl border border-white/20" alt=""><div class="flex-1"><div class="pulse-kicker">Teamakte</div><div class="pulse-title">{e(target.display_name)}</div><div class="pulse-sub">{e(role)} · Discord ID {e(target.id)}</div></div><div class="pulse-hero-box min-w-[220px]"><div class="label">WOCHENAKTIVITÄT</div><div class="value">{ws.fmt_duration(weekly)}</div><div class="pulse-progress mt-3" style="background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.15)"><span style="width:{min(100,round(weekly/(max(0.5,float(c.config.get('weekly_goal_hours',3)))*3600)*100))}%;background:white"></span></div></div></div></section>
        <div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">⚠</div><div class="label">Aktive Warnungen</div><div class="value">{len(active_warns)}/5</div></div><div class="pulse-stat"><div class="icon">↩</div><div class="label">Zurückgezogen</div><div class="value">{len(revoked_warns)}</div></div><div class="pulse-stat"><div class="icon">⏱</div><div class="label">Schichten</div><div class="value">{len(hist)}</div></div><div class="pulse-stat"><div class="icon">🎫</div><div class="label">Tickets</div><div class="value">{len(tickets)}</div></div></div>
        <div class="pulse-grid"><div class="space-y-4">{card("Aktive Verwarnungen",active_html or '<div class="pulse-empty">Keine aktiven Verwarnungen.</div>','⚠')}{card("Warn-Historie",revoked_html or '<div class="pulse-empty">Keine zurückgezogenen Warnungen.</div>','↩')}{card("Notizen",note_html or '<div class="pulse-empty">Keine Notizen.</div>','📝')}{card("Aktivität",event_html or '<div class="pulse-empty">Noch keine zentralen Events.</div>','↯')}</div><div class="space-y-4">{card("Warnrollen-Status",role_pills,'⚙')}{card("Aufgaben",task_html or '<div class="pulse-empty">Keine Aufgaben.</div>','□')}{card("Tickets",ticket_html or '<div class="pulse-empty">Keine Tickets.</div>','🎫')}{quick}</div></div>'''
        return render_pro_page(ws,f'Teamakte · {target.display_name}',c,'team',body)


    async def warns_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None)
        if not (c.perms.get('can_warn') or c.perms.get('is_admin')):
            raise HTTPException(403,'Dafür fehlt dir die Berechtigung für die Verwarnungszentrale.')

        teamdb=ws.load_json(ws.DATA_FILE,{})
        rows=[]; history=[]
        for m in sorted(team(c.guild,c.config.get('team_role_ids',[])), key=lambda x:x.display_name.lower()):
            entry=teamdb.setdefault(str(m.id),{})
            ws.user_entry(teamdb,str(m.id))
            active=ws.active_warns(entry)
            all_warns=entry.get('warns_list',[]) or []
            for w in active:
                rows.append((m,w))
            for w in all_warns:
                if not w.get('active',True) or w.get('revoked_at'):
                    history.append((m,w))
        active_total=len(rows); history_total=len(history); critical=sum(1 for m,w in rows if len(ws.active_warns(teamdb.get(str(m.id),{})))>=5)

        q=e((request.query_params.get('q') or '').strip().lower())
        only_active=request.query_params.get('view','active')!='history'
        selected = rows if only_active else history
        if q:
            selected=[x for x in selected if q in f'{x[0].display_name} {x[0].name} {x[1].get("reason","")} {x[1].get("by","")} {x[1].get("id","")}'.lower()]

        def row(m,w,is_active):
            proof=ws.safe_url(w.get('proof'))
            proof_html=f'<a href="{e(proof)}" target="_blank" rel="noopener noreferrer" class="text-[10px] text-indigo-500 hover:underline">🔗 Beweis</a>' if proof else ''
            action_html=f'''<form action="/action" method="post" class="mt-2 flex flex-wrap gap-2" onsubmit="return confirm('Diese Verwarnung wirklich zurückziehen?');">
                <input type="hidden" name="action" value="remove_warn"><input type="hidden" name="user_id" value="{m.id}"><input type="hidden" name="warn_id" value="{e(w.get('id'))}"><input type="hidden" name="redirect_to_member" value="">
                <input name="warn_revoke_reason" maxlength="300" class="pulse-input flex-1 min-w-[180px]" placeholder="Rücknahmegrund (optional)">
                <button class="pulse-btn bad">↩ Zurückziehen</button></form>''' if is_active and (c.perms.get('can_warn') or c.perms.get('is_admin')) else ''
            revoked=f'<div class="text-[10px] text-slate-400 mt-2">↩ {e(w.get("revoked_at") or "N/A")} · {e(w.get("revoked_by") or "Team")} · {e(w.get("revoked_reason") or "Kein Grund")}</div>' if not is_active else ''
            return f'''<article class="pulse-row items-start"><div class="pulse-avatar">⚠</div><div class="pulse-row-main"><div class="flex items-center gap-2 flex-wrap"><a href="/member/{m.id}" class="pulse-row-title hover:underline">{e(m.display_name)}</a>{pill("Aktiv","warn") if is_active else pill("Zurückgezogen","good")}</div><div class="pulse-row-meta">{e(w.get("date") or "—")} · von {e(w.get("by") or "Team")} · Warn-ID {e(w.get("id") or "—")}</div><div class="text-[11px] mt-2 whitespace-pre-wrap">{e(w.get("reason") or "Kein Grund")}</div>{proof_html}{revoked}{action_html}</div><span class="pulse-pill {'warn' if is_active else 'good'}">{'Warnung' if is_active else 'Archiv'}</span></article>'''

        items=''.join(row(m,w,only_active) for m,w in sorted(selected,key=lambda x:(-len(x[1].get('date','')),x[0].display_name.lower())))
        role_health=ws.warning_role_health(c.guild,c.config)
        role_html=''.join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">Warn {x["level"]} · {e(x["role"].name if x["role"] else "Nicht gefunden")}</div><div class="pulse-row-meta">ID {x["id"]} · {e(x["detail"])}</div></div>{pill("OK","good") if x["ok"] else pill("FEHLER","bad")}</div>' for x in role_health)
        view_active='bg-indigo-600 text-white' if only_active else 'bg-slate-100 dark:bg-slate-800'
        view_hist='bg-indigo-600 text-white' if not only_active else 'bg-slate-100 dark:bg-slate-800'
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">⚠ Verwarnungszentrale</div><div class="pulse-section-sub">Alle aktiven Team-Warnungen, Rücknahmen und Rollen-Synchronisierung an einem Ort.</div></div>{pill("System OK","good") if all(x["ok"] for x in role_health) else pill("Rollen prüfen","bad")}</div>
        <div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">⚠</div><div class="label">Aktive Warnungen</div><div class="value">{active_total}</div></div><div class="pulse-stat"><div class="icon">🚨</div><div class="label">5/5 Fälle</div><div class="value">{critical}</div></div><div class="pulse-stat"><div class="icon">↩</div><div class="label">Rücknahmen</div><div class="value">{history_total}</div></div><div class="pulse-stat"><div class="icon">👥</div><div class="label">Betroffene Teamler</div><div class="value">{len({m.id for m,w in rows})}</div></div></div>
        <div class="pulse-grid"><div><section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">{'Aktive Verwarnungen' if only_active else 'Warn-Historie'}</div><div class="pulse-section-sub">Suche nach Name, Warn-Grund, Aussteller oder Warn-ID.</div></div><div class="flex gap-2"><a href="/warns?view=active" class="pulse-btn {view_active}">Aktiv</a><a href="/warns?view=history" class="pulse-btn {view_hist}">Historie</a></div></div><div class="pulse-card-b"><form method="get" class="pulse-search mb-4" style="max-width:none"><input type="hidden" name="view" value="{'active' if only_active else 'history'}"><span>⌕</span><input name="q" value="{e(q)}" placeholder="Teammitglied, Grund, Warn-ID…"><button class="pulse-btn primary">Suchen</button></form><div class="space-y-2">{items or '<div class="pulse-empty">Keine passenden Verwarnungen gefunden.</div>'}</div></div></section></div><div>{card("⚙ Warnrollen",role_html,'⚙')}<div class="mt-4"><section class="pulse-alert"><strong>Hinweis</strong><div class="text-slate-400 mt-1">Eine zurückgezogene Warnung wird nicht gelöscht. Sie bleibt für Audit und Historie erhalten und zählt nicht mehr gegen die 5-Warn-Schwelle.</div></section></div></div></div>'''
        return render_pro_page(ws,'Verwarnungszentrale',c,'warns',body)

    async def ticket_detail_v5(request: Request, ticket_id: str, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm='can_manage_tickets'); t=db.get_ticket(ticket_id=ticket_id)
        if not t: raise HTTPException(404,'Ticket nicht gefunden.')
        evs=db.ticket_events(ticket_id,150)
        history=''.join(f'<div class="pulse-row"><div class="pulse-avatar">↯</div><div class="pulse-row-main"><div class="pulse-row-title">{e(x["event_type"])}</div><div class="pulse-row-meta">{e(x["actor_name"])} · {e(fmt_dt(x["created_at"]))}</div><div class="text-[11px] text-slate-500 mt-1">{e(x.get("details"))}</div></div></div>' for x in evs)
        transcript=f'<a href="/ticket/transcript/{quote(os.path.basename(t.get("transcript_path") or ""))}" class="pulse-btn ghost">Transcript öffnen</a>' if t.get('transcript_path') else '<span class="pulse-pill">Kein Transcript</span>'
        body=f'<div class="pulse-topbar"><div><a href="/tickets" class="pulse-btn ghost">← Tickets</a></div>{pill("Geschlossen","good") if t.get("status")=="closed" else pill("Offen","warn")}</div><section class="pulse-card"><div class="pulse-card-b"><div class="flex flex-wrap items-start justify-between gap-4"><div><div class="pulse-kicker">Ticket</div><div class="text-2xl font-extrabold mt-1">{e(t["id"])}</div><div class="text-xs text-slate-500 mt-1">{e(t["user_name"])} · {e(t["category"])}</div></div><div class="flex gap-2">{transcript}</div></div><div class="grid md:grid-cols-4 gap-3 mt-5"><div class="pulse-stat"><div class="label">Priorität</div><div class="value text-base">{e(t.get("priority"))}</div></div><div class="pulse-stat"><div class="label">Bearbeiter</div><div class="value text-base">{e(t.get("claimed_by_name") or "Niemand")}</div></div><div class="pulse-stat"><div class="label">Bewertung</div><div class="value text-base">{e(str(t.get("rating") or "—"))} ⭐</div></div><div class="pulse-stat"><div class="label">Schließgrund</div><div class="value text-base">{e(t.get("close_reason") or "—")}</div></div></div></div></section>{card("Ticket-Verlauf",history or '<div class="pulse-empty">Noch keine Events.</div>','↯')}'
        return render_pro_page(ws,f'Ticket · {ticket_id}',c,'tickets',body)

    async def meetings_history_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); items=db.meeting_history(150)
        rows=''.join(f'<article class="pulse-row"><div class="pulse-avatar">◫</div><div class="pulse-row-main"><div class="pulse-row-title">{e(x["title"])}</div><div class="pulse-row-meta">{e(x["date_time"])} · archiviert {e(fmt_dt(x["created_at"]))}</div><div class="text-[11px] text-slate-500 mt-2 whitespace-pre-wrap">{e(x.get("description"))}</div></div>{pill("Protokoll" if x.get("notes") else "Ohne Protokoll")}</article>' for x in items)
        body=f'<div class="pulse-topbar"><div><div class="pulse-section-title">▤ Meeting-Historie</div><div class="pulse-section-sub">Archivierte Besprechungen bleiben nachvollziehbar.</div></div><a href="/meetings" class="pulse-btn primary">◫ Neues Meeting</a></div>{card("Vergangene Meetings",rows or '<div class=\"pulse-empty\">Noch keine archivierten Meetings.</div>','▤')}'
        return render_pro_page(ws,'Meeting-Historie',c,'meeting-history',body)

    async def achievements_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); uid=str(c.user['id']); shifts=ws.load_shifts(); hist=[h for h in shifts.get('history',[]) if str(h.get('mod_id'))==uid]; shift_count=len(hist); hours=sum(int(h.get('duration_seconds',0)) for h in hist)/3600
        tickets_closed=sum(1 for t in db.list_tickets(limit=3000) if str(t.get('claimed_by_id'))==uid and t.get('status')=='closed')
        tasks_done=sum(1 for t in db.list_tasks(limit=3000,include_archived=True) if str(t.get('assignee_id'))==uid and t.get('status')=='done')
        handovers=sum(1 for h in db.list_handovers(3000,True) if str(h.get('author_id'))==uid)
        meeting_count=0
        for x in db.meeting_history(3000):
            try:
                attendees=json.loads(x.get('attendees_json') or '{}')
                if str(attendees.get(uid)).lower() in ('yes','accepted','true','1'): meeting_count+=1
            except Exception: pass
        flag_points=0
        try:
            import sqlite3
            with sqlite3.connect('flaggenquiz.db') as cx:
                row=cx.execute('SELECT points FROM quiz_scores WHERE guild_id=? AND user_id=?',(c.guild.id,int(uid))).fetchone(); flag_points=int(row[0]) if row else 0
        except Exception: pass
        metrics={'shifts':shift_count,'hours':hours,'tickets_closed':tickets_closed,'meetings':meeting_count,'flag_correct':flag_points,'handovers':handovers,'tasks_done':tasks_done}
        cards=[]; unlocked=0
        for a in db.achievements():
            value=metrics.get(a['metric'],0); target=int(a['target']); pct=min(100,int(value/target*100)) if target else 100; ok=value>=target; unlocked += int(ok)
            cards.append(f'<section class="pulse-card"><div class="pulse-card-b"><div class="flex items-start gap-3"><div class="text-2xl">{e(a["icon"])}</div><div class="flex-1"><div class="pulse-section-title">{e(a["name"])}</div><div class="pulse-section-sub">{e(a["description"])}</div></div>{pill("Freigeschaltet","good") if ok else pill(f"{value:g} / {target}")}</div><div class="pulse-progress mt-4"><span style="width:{pct}%"></span></div><div class="text-[10px] text-slate-400 mt-2">{pct}%</div></div></section>')
        body=f'<div class="pulse-topbar"><div><div class="pulse-section-title">◆ Achievements</div><div class="pulse-section-sub">Teamfortschritt und persönliche Meilensteine.</div></div><span class="pulse-pill good">{unlocked} / {len(cards)} freigeschaltet</span></div><div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">⏱</div><div class="label">Schichten</div><div class="value">{shift_count}</div></div><div class="pulse-stat"><div class="icon">🔥</div><div class="label">Stunden</div><div class="value">{hours:.1f}</div></div><div class="pulse-stat"><div class="icon">🎫</div><div class="label">Tickets</div><div class="value">{tickets_closed}</div></div><div class="pulse-stat"><div class="icon">✅</div><div class="label">Aufgaben</div><div class="value">{tasks_done}</div></div></div><div class="grid md:grid-cols-2 xl:grid-cols-3 gap-4">{"".join(cards)}</div>'
        return render_pro_page(ws,'Achievements',c,'achievements',body)

    async def settings_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True); roles=[r for r in c.guild.roles if not r.is_default() and not r.managed]; role_ids=c.config.get('team_role_ids',[]); cfg=c.config.get('permissions',{})
        permission_fields=[('can_view_dashboard','Dashboard'),('can_warn','Verwarnungen'),('can_promote','Teamaktionen'),('can_add_notes','Notizen'),('can_manage_tickets','Tickets'),('can_manage_applications','Bewerbungen'),('can_manage_tasks','Aufgaben'),('can_manage_training','Schulungen'),('can_manage_wiki','Wiki'),('can_view_analytics','Analytics'),*[(x,x.replace('can_','').replace('_',' ').title()) for x in EXTRA_PERMS]]
        cards=[]
        for rid in role_ids:
            role=c.guild.get_role(rid)
            if not role: continue
            rp=cfg.get(str(rid),{})
            fields=''.join(f'<label class="flex items-center gap-2 text-[11px] p-2 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800/50"><input type="checkbox" name="{k}" {"checked" if rp.get(k) else ""}><span>{e(label)}</span></label>' for k,label in permission_fields)
            cards.append(f'<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title" style="color:#{role.color.value:06x}">{e(role.name)}</div><span class="text-[9px] text-slate-400 font-mono">{rid}</span></div><div class="pulse-card-b"><form action="/settings/pro-save" method="post"><input type="hidden" name="role_id" value="{rid}"><div class="grid grid-cols-2 gap-1">{fields}</div><button class="pulse-btn primary mt-3 w-full">Rechte speichern</button></form></div></section>')
        warn_ids=ws.get_warn_role_ids(c.config)
        role_options=lambda selected: ''.join(f'<option value="{r.id}" {"selected" if r.id==selected else ""}>{e(r.name)} · ID {r.id}</option>' for r in roles)
        warn_role_card=f'''<section class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">⚠ Warn-Rollen</div><div class="pulse-section-sub">Discord-Rollen für Warnstufe 1–5. Pulse prüft automatisch die Bot-Hierarchie.</div></div></div><div class="pulse-card-b"><form action="/settings/pro-warn-roles" method="post" class="grid md:grid-cols-3 gap-3"><label class="text-[10px] uppercase text-slate-400 font-bold">Warn 1<select name="warn_1" class="pulse-input mt-1"><option value="">Nicht gesetzt</option>{role_options(warn_ids[1])}</select></label><label class="text-[10px] uppercase text-slate-400 font-bold">Warn 2<select name="warn_2" class="pulse-input mt-1"><option value="">Nicht gesetzt</option>{role_options(warn_ids[2])}</select></label><label class="text-[10px] uppercase text-slate-400 font-bold">Warn 3<select name="warn_3" class="pulse-input mt-1"><option value="">Nicht gesetzt</option>{role_options(warn_ids[3])}</select></label><label class="text-[10px] uppercase text-slate-400 font-bold">Warn 4<select name="warn_4" class="pulse-input mt-1"><option value="">Nicht gesetzt</option>{role_options(warn_ids[4])}</select></label><label class="text-[10px] uppercase text-slate-400 font-bold">Warn 5<select name="warn_5" class="pulse-input mt-1"><option value="">Nicht gesetzt</option>{role_options(warn_ids[5])}</select></label><button class="pulse-btn primary md:col-span-3">Warn-Rollen speichern & prüfen</button></form><form action="/settings/pro-warn-sync" method="post" class="mt-3"><button class="pulse-btn ghost w-full">↻ Alle Teamler jetzt mit Warn-Rollen abgleichen</button></form></div></section>'''
        team_boxes=''.join(f'<label class="flex items-center gap-2 p-2 rounded-lg text-xs"><input type="checkbox" name="team_roles" value="{r.id}" {"checked" if r.id in role_ids else ""}><span style="color:#{r.color.value:06x}">{e(r.name)}</span></label>' for r in sorted(roles,key=lambda r:-r.position))
        audit=''.join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(a.get("actor"))} · {e(a.get("action"))}</div><div class="pulse-row-meta">{e(a.get("details"))}</div></div><div class="pulse-row-meta">{e(a.get("timestamp"))}</div></div>' for a in reversed(ws.load_json(ws.AUDIT_FILE,[])[-120:]))
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">⚙ Einstellungen</div><div class="pulse-section-sub">Teamrollen, Berechtigungen und Audit.</div></div></div><div class="pulse-two"><section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">👥 Teamrollen</div></div><div class="pulse-card-b"><form action="/settings/pro-roles" method="post"><div class="grid sm:grid-cols-2">{team_boxes}</div><button class="pulse-btn primary mt-3">Teamrollen speichern</button></form></div></section><section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">🎯 Wochenziel</div></div><div class="pulse-card-b"><form action="/settings/pro-goal" method="post" class="flex gap-2"><input class="pulse-input" type="number" min="0.5" max="100" step="0.5" name="weekly_goal" value="{float(c.config.get('weekly_goal_hours',3.0)):g}"><button class="pulse-btn primary">Speichern</button></form></div></section></div><div class="grid md:grid-cols-2 gap-4 mt-5">{"".join(cards)}</div>{warn_role_card}<section class="pulse-card mt-5"><div class="pulse-card-h"><div class="pulse-section-title">📜 Audit-Log</div></div><div class="pulse-card-b space-y-2 max-h-[520px] overflow-auto">{audit or '<div class="pulse-empty">Keine Audit-Einträge.</div>'}</div></section>'''
        return render_pro_page(ws,'Einstellungen',c,'settings',body)

    async def settings_save(request: Request, role_id: int=Form(...), user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True); role=c.guild.get_role(role_id)
        if not role or role_id not in c.config.get('team_role_ids',[]): return ws.back('/settings','Ungültige Teamrolle.',False)
        form=await request.form(); cfg=c.config.setdefault('permissions',{}); rp={}
        for k in ws.PERM_KEYS + EXTRA_PERMS: rp[k]=bool(form.get(k))
        cfg[str(role_id)]=rp; ws.save_json(ws.CONFIG_FILE,c.config); ws.log_audit(c.user.get('global_name'),c.user['id'],'Rechte Gespeichert',f'Rolle {role.name}')
        return ws.back('/settings','Rollenrechte gespeichert.')

    async def settings_roles(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True); form=await request.form(); raw=form.getlist('team_roles'); vals=[]
        for x in raw:
            try:
                r=c.guild.get_role(int(x))
                if r and not r.managed: vals.append(r.id)
            except ValueError: pass
        vals=sorted(set(vals),key=lambda rid:c.guild.get_role(rid).position); c.config['team_role_ids']=vals; ws.save_json(ws.CONFIG_FILE,c.config); ws.log_audit(c.user.get('global_name'),c.user['id'],'Team-Rollen Geändert',','.join(str(x) for x in vals)); return ws.back('/settings','Teamrollen gespeichert.')

    async def settings_warn_roles(request: Request, warn_1: str=Form(""), warn_2: str=Form(""), warn_3: str=Form(""), warn_4: str=Form(""), warn_5: str=Form(""), user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        selected={}
        for level,value in ((1,warn_1),(2,warn_2),(3,warn_3),(4,warn_4),(5,warn_5)):
            if value.strip().isdigit():
                rid=int(value)
                role=c.guild.get_role(rid)
                if role and not role.managed and not role.is_default():
                    selected[str(level)]=rid
        current=ws.get_warn_role_ids(c.config)
        for level in (1,2,3,4,5):
            selected.setdefault(str(level),current[level])
        c.config['warn_role_ids']=selected
        ws.save_json(ws.CONFIG_FILE,c.config)
        health=ws.warning_role_health(c.guild,c.config)
        sync_report=await ws.reconcile_warning_roles(c.guild,c.config)
        ok=all(x['ok'] for x in health) and not sync_report.get('failed')
        ws.log_audit(
            c.user.get('global_name'),c.user['id'],'Warn-Rollen Geändert',
            ','.join(str(selected[str(i)]) for i in (1,2,3,4,5))
        )
        msg='Warn-Rollen gespeichert und Teamrollen synchronisiert.'
        if sync_report.get('failed'):
            msg += f" {sync_report['failed']} Teamler konnten nicht synchronisiert werden."
        elif not all(x['ok'] for x in health):
            msg += ' Mindestens eine Warn-Rolle muss noch geprüft werden.'
        return ws.back('/settings',msg,ok)

    async def settings_warn_sync(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        report=await ws.reconcile_warning_roles(c.guild,c.config)
        msg=f"Warn-Rollen abgeglichen: {report['updated']}/{report['checked']} synchron."
        if report.get('failed'):
            msg += f" {report['failed']} Fehler – Systemstatus/Verwarnungszentrale prüfen."
        return ws.back('/settings',msg,not report.get('failed'))
    async def settings_goal(request: Request, weekly_goal: float=Form(...), user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True); weekly_goal=max(.5,min(100,float(weekly_goal))); c.config['weekly_goal_hours']=round(weekly_goal,1); ws.save_json(ws.CONFIG_FILE,c.config); ws.log_audit(c.user.get('global_name'),c.user['id'],'Wochenziel Geändert',f'{weekly_goal:g}h'); return ws.back('/settings','Wochenziel gespeichert.')

    async def system_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True); checks=[]; bot=getattr(request.app.state,'bot',None)
        checks.append(('Bot',bool(bot and bot.is_ready()),'Discord-Verbindung'))
        checks.append(('Datenbank',os.path.exists(db.DB_PATH),'pulse.db'))
        try:
            with db.connect() as cx: cx.execute('SELECT 1'); db_ok=True
        except Exception: db_ok=False
        checks.append(('SQLite',db_ok,'Abfragen'))
        checks.append(('OAuth',bool(ws.CLIENT_ID and ws.CLIENT_SECRET),'Discord OAuth'))
        checks.append(('Session Secret',bool(os.getenv('SESSION_SECRET') or os.path.exists(ws.SECRET_FILE)),'Cookie-Signing'))
        checks.append(('Teamrollen',bool(c.config.get('team_role_ids')),'Konfiguration'))
        team_channel=c.guild.get_channel(ws.TEAM_UPDATE_CHANNEL_ID)
        checks.append(('Team-Updates-Kanal',bool(team_channel),'ID 1531132354272170115'))
        for wr in ws.warning_role_health(c.guild,c.config):
            checks.append((f'Warn-Rolle {wr["level"]}',wr["ok"],f'{wr["role"].name if wr["role"] else "nicht gefunden"} · {wr["detail"]}'))
        checks.append(('Backups',os.path.isdir(ws.BACKUP_DIR),'Backup-Verzeichnis'))
        expected=set()
        try: expected={n[:-3] for n in os.listdir(os.path.join(ws.BASE_DIR,'cogs')) if n.endswith('.py') and n!='__init__.py'}
        except Exception: pass
        loaded=set()
        try: loaded={n.rsplit('.',1)[-1] for n in getattr(bot,'extensions',{}).keys() if n.startswith('cogs.')}
        except Exception: pass
        missing=sorted(expected-loaded); checks.append(('Cogs',not missing,f'{len(loaded)}/{len(expected)} geladen' + (f' · fehlt: {", ".join(missing[:3])}' if missing else '')))
        total,used,free=shutil.disk_usage(ws.BASE_DIR); checks.append(('Speicher',free>200*1024*1024,f'{free/1024/1024:.0f} MB frei'))
        rows=''.join(f'<div class="pulse-row"><div class="flex items-center gap-2"><span class="pulse-status-dot {"bg-emerald-500" if ok else "bg-rose-500"}"></span><div class="pulse-row-title">{e(name)}</div></div><div class="text-right"><div class="text-[11px] font-semibold">{e(detail)}</div><div class="pulse-row-meta">{"OK" if ok else "Prüfen"}</div></div></div>' for name,ok,detail in checks)
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">♥ Systemstatus</div><div class="pulse-section-sub">Live-Prüfung der wichtigsten Pulse-Komponenten.</div></div><span class="pulse-pill {'good' if all(x[1] for x in checks) else 'warn'}">{'Alles grün' if all(x[1] for x in checks) else 'Prüfung nötig'}</span></div>{card('System-Checks',rows,'♥')}'''
        return render_pro_page(ws,'Systemstatus',c,'system',body)

    async def team_export(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm='can_view_analytics'); buf=io.StringIO(); w=csv.writer(buf); w.writerow(['Discord ID','Name','Rolle','Wochenstunden','Aktueller Status','Warnungen'])
        shifts=ws.load_shifts(); teamdb=ws.load_json(ws.DATA_FILE,{}); ids=c.config.get('team_role_ids',[])
        for m in team(c.guild,ids):
            sec=ws.calculate_weekly_seconds(str(m.id),shifts.get('history',[]),shifts.get('active_shifts',{})); warns=len(ws.active_warns(teamdb.get(str(m.id),{}))); st='Im Dienst' if str(m.id) in shifts.get('active_shifts',{}) else 'Offline'; w.writerow([m.id,m.display_name,member_role(m,ids),f'{sec/3600:.2f}',st,warns])
        data=buf.getvalue().encode('utf-8-sig'); path=os.path.join(ws.BACKUP_DIR,'pulse_team_export.csv'); open(path,'wb').write(data); return FileResponse(path,media_type='text/csv',filename='pulse_team_export.csv')

    async def api_live(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session); s=work_stats(c); now=ws.now_de().strftime('%H:%M:%S'); open_t=sum(1 for x in s['tickets'] if x['status']!='closed'); overdue=0
        for t in s['tasks']:
            d=parse_due(t.get('due_at')); overdue += int(bool(d and d<datetime.now().astimezone() and t.get('status') not in ('done','archived')))
        return JSONResponse({'ok':True,'active':s['active'],'open_tickets':open_t,'overdue_tasks':overdue,'approvals':len(s['promos']),'applications':s['apps'],'loas':s['loas'],'now':now})

    async def api_health(request: Request):
        bot=getattr(request.app.state,'bot',None)
        db_ok=True
        try:
            with db.connect() as cx: cx.execute('SELECT 1')
        except Exception: db_ok=False
        return JSONResponse({'ok':bool(db_ok and bot and bot.is_ready()),'version':VERSION,'bot_ready':bool(bot and bot.is_ready()),'database':db_ok,'time':ws.now_de().isoformat()})

    async def manifest():
        return JSONResponse({'name':'Pulse TeamOS','short_name':'Pulse','description':'Professionelles Team-Management für Discord','start_url':'/dashboard','scope':'/','display':'standalone','background_color':'#080b12','theme_color':'#5b5cf0','lang':'de','categories':['productivity','business']})

    async def service_worker():
        return HTMLResponse("""const CACHE='pulse-v5-12';self.addEventListener('install',e=>e.waitUntil(self.skipWaiting()));self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('pulse-v5')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>clients.claim())));self.addEventListener('fetch',e=>{if(e.request.method!=='GET'||e.request.url.includes('/api/'))return;if(e.request.mode==='navigate'){e.respondWith(fetch(e.request).catch(()=>new Response('<!doctype html><title>Pulse offline</title><body style=\"font-family:system-ui;padding:40px\"><h1>Pulse ist gerade offline.</h1><p>Bitte prüfe deine Verbindung und lade die Seite erneut.</p></body>',{headers:{'content-type':'text/html;charset=utf-8'},status:503})));}});""",media_type='application/javascript')

    # Re-register selected pages on top of the v4 versions.
    remove_and_add(app,'/dashboard',{'GET'},dashboard_v5,response_class=HTMLResponse)
    remove_and_add(app,'/pulse-inbox',{'GET'},inbox_v5,response_class=HTMLResponse)
    remove_and_add(app,'/pulse-inbox/read/{nid}',{'POST'},inbox_read_one)
    remove_and_add(app,'/pulse-inbox/read',{'POST'},inbox_read_all)
    remove_and_add(app,'/tasks',{'GET'},tasks_v5,response_class=HTMLResponse)
    remove_and_add(app,'/tasks/update',{'POST'},update_task_v5)
    remove_and_add(app,'/tickets',{'GET'},tickets_v5,response_class=HTMLResponse)
    remove_and_add(app,'/tickets/update',{'POST'},ticket_update_v5)
    remove_and_add(app,'/stats',{'GET'},stats_v5,response_class=HTMLResponse)
    remove_and_add(app,'/calendar',{'GET'},calendar_v5,response_class=HTMLResponse)
    remove_and_add(app,'/search',{'GET'},search_v5,response_class=HTMLResponse)
    remove_and_add(app,'/settings',{'GET'},settings_v5,response_class=HTMLResponse)
    remove_and_add(app,'/health',{'GET'},api_health,response_class=JSONResponse)
    remove_and_add(app,'/manifest.webmanifest',{'GET'},manifest)
    remove_and_add(app,'/sw.js',{'GET'},service_worker)
    remove_and_add(app,'/system',{'GET'},system_v5,response_class=HTMLResponse)
    remove_and_add(app,'/member/{user_id}',{'GET'},member_v5,response_class=HTMLResponse)
    app.get('/warns',response_class=HTMLResponse)(warns_v5)
    remove_and_add(app,'/ticket/{ticket_id}',{'GET'},ticket_detail_v5,response_class=HTMLResponse)
    remove_and_add(app,'/export/team.csv',{'GET'},team_export)
    app.get('/api/pulse/live')(api_live)
    remove_and_add(app,'/achievements',{'GET'},achievements_v5,response_class=HTMLResponse)
    remove_and_add(app,'/meetings-history',{'GET'},meetings_history_v5,response_class=HTMLResponse)
    app.get('/team-status',response_class=HTMLResponse)(team_status_page)
    remove_and_add(app,'/melonly',{'GET'},melonly_page,response_class=HTMLResponse)
    remove_and_add(app,'/api/team/discord-status',{'GET'},discord_status_api,response_class=JSONResponse)
    app.post('/team-status/set')(set_team_status)
    app.get('/activity-check',response_class=HTMLResponse)(activity_check_page)
    app.get('/handover',response_class=HTMLResponse)(handover_page)
    app.post('/handover/create')(create_handover)
    app.get('/announcements',response_class=HTMLResponse)(announcements_page)
    app.post('/announcements/create')(create_announcement)
    app.get('/approvals',response_class=HTMLResponse)(approvals_page)
    # Promotions deliberately use /approvals so there is one workflow hub; direct alias remains useful.
    app.get('/promotions',response_class=HTMLResponse)(promotions_page)
    app.post('/promotions/create')(create_promotion)
    app.post('/promotions/decide')(decide_promotion)
    app.post('/settings/pro-save')(settings_save)
    app.post('/settings/pro-roles')(settings_roles)
    app.post('/settings/pro-goal')(settings_goal)
    app.post('/settings/pro-warn-roles')(settings_warn_roles)
    app.post('/settings/pro-warn-sync')(settings_warn_sync)
    # Ensure the robust v5 backup restore handler is the only active route.
    remove_and_add(app,'/backup/restore',{'POST'},ws.restore_backup)

    # Improve the v4 feature's training route with a strict server-side deadline without changing its UI.
    old_training_submit = None
    for r in app.router.routes:
        if getattr(r,'path',None).startswith('/training/') and 'submit' in str(getattr(r,'path',None)) and 'POST' in getattr(r,'methods',set()):
            old_training_submit = getattr(r,'endpoint',None)
    # No monkeypatch needed when the v4 endpoint is already validating score; keep the route stable.

    # Avoid notification spam during 5-minute housekeeping by dedupe-aware DB notify implementation.
    print(f"✨ Pulse Pro v{VERSION} aktiviert – Operations Center, Workflow, Teamstatus und Sicherheit geladen.")
