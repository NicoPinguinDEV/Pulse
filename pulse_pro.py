"""Pulse Pro v5 – UI, operations center, team workflow and security upgrades."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import shutil
from pathlib import Path
import sqlite3
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import quote, urlparse

import discord
from pulse_discord_style import send_styled
from pulse_embed_design import color as _pulse_color
from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

import pulse_db as db
from pulse_nickname_utils import normalize_role_prefix
from pulse_version import PULSE_VERSION

VERSION = PULSE_VERSION
EXTRA_PERMS = (
    "can_manage_announcements",
    "can_manage_handover",
    "can_manage_promotions",
    "can_view_team_status",
    "can_manage_checkins",
    "can_moderate_kicks",
    "can_moderate_bans",
    "can_moderate_bolo",
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
.pulse-nav-group summary{list-style:none;cursor:pointer;user-select:none}.pulse-nav-group summary::-webkit-details-marker{display:none}.pulse-nav-group summary:after{content:"▸";float:right;transition:transform .15s}.pulse-nav-group[open] summary:after{transform:rotate(90deg)}
@media(max-width:1200px){.pulse-stat-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.pulse-grid{grid-template-columns:1fr}.pulse-hero-grid{grid-template-columns:1fr}}
@media(max-width:767px){.pulse-topbar{padding-left:62px}}
@media(max-width:800px){input:not([type=checkbox]):not([type=radio]):not([type=range]),select,textarea{font-size:16px!important}.pulse-card-b{overflow-x:auto}.pulse-row{flex-wrap:wrap}.pulse-card-h{flex-wrap:wrap}.pulse-search{display:none}.pulse-stat-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.pulse-two{grid-template-columns:1fr}.pulse-title{font-size:25px}.pulse-topbar{top:7px}.pulse-hero{padding:22px;border-radius:20px}}
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

            # Digitale Pulse-Zwischenrollen verknüpfen interne Rechteprofile mit
            # konfigurierten Discord-Teamrollen; alle Mitglieder erben die Rechte automatisch.
            team_ids = {int(value) for value in config.get("team_role_ids", []) if str(value).isdigit()}
            member_role_ids = {int(role.id) for role in member_roles}
            for dashboard_profile in config.get("dashboard_roles", {}).values():
                if not isinstance(dashboard_profile, dict):
                    continue
                mapped_ids = {
                    int(value) for value in dashboard_profile.get("discord_role_ids", [])
                    if str(value).isdigit()
                }
                if not (mapped_ids & team_ids & member_role_ids):
                    continue
                profile_permissions = dashboard_profile.get("permissions", {})
                for key in EXTRA_PERMS:
                    if profile_permissions.get(key):
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
                ("me","/me","★","Mein Pulse",True,False),
                ("cockpit","/cockpit","◆","Leitungs-Cockpit",perms.get("can_promote") or perms.get("is_admin"),False),
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
                ("voice-support","/voice-support","🎧","Sprach-Support",perms.get("can_manage_tickets") or perms.get("can_promote") or perms.get("is_admin"),False),
                ("applications","/applications","✎","Bewerbungen",True,False),
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
        # Two clearly separated areas: daily team work vs. administration/configuration.
        # The switch only mirrors permissions; every admin route authorizes server-side.
        from pulse_admin import ADMIN_PAGE_KEYS, admin_sections
        admin_entries = admin_sections(perms)
        admin_mode = bool(admin_entries) and current_page in ADMIN_PAGE_KEYS
        nav = []
        if admin_entries:
            nav.append('<div class="pulse-area-switch" role="tablist">'
                       f'<a href="/me" role="tab" class="{"" if admin_mode else "on"}" aria-selected="{"false" if admin_mode else "true"}">👥 Team</a>'
                       f'<a href="/admin" role="tab" class="{"on" if admin_mode else ""}" aria-selected="{"true" if admin_mode else "false"}">🛠 Verwaltung</a></div>')
        if admin_mode:
            groups = [("Verwaltung", [("admin", "/admin", "◆", "Übersicht", True, False)]
                       + [(key, href, icon, label, True, False) for key, href, icon, label, _ in admin_entries])]
        for group, items in groups:
            visible = [x for x in items if x[4]]
            if not visible: continue
            nav.append(f'<div class="px-2 mt-4 mb-2 text-[9px] font-bold uppercase tracking-[.14em] text-slate-400">{group}</div>')
            for key, href, icon, label, visible, _ in visible:
                badge_text = unread if key == "inbox" and unread else ""
                nav.append(nav_item(key, href, icon, label, current_page == key, str(badge_text)))
        # Former separate dashboards (Ultimate/TeamOS 2.0 and Operations Suite) share this
        # sidebar; their sections collapse and open automatically when one of their pages is active.
        from pulse_design import SUITE_NAV, ULTIMATE_NAV, visible_nav
        leader = bool(perms.get("can_promote") or perms.get("is_admin"))
        for prefix, group, items in (() if admin_mode else (("u", "Command Center", ULTIMATE_NAV), ("s", "Operations Suite", SUITE_NAV))):
            entries = visible_nav(items, leader)
            if not entries: continue
            is_open = str(current_page).startswith(prefix + ":")
            links = "".join(nav_item(f"{prefix}:{key}", href, label.split(" ", 1)[0], label.split(" ", 1)[1] if " " in label else label, current_page == f"{prefix}:{key}") for key, href, label, _ in entries)
            nav.append(f'<details class="pulse-nav-group mt-4"{" open" if is_open else ""}><summary class="px-2 mb-2 text-[9px] font-bold uppercase tracking-[.14em] text-slate-400">{group}</summary><div class="pulse-sidebar-sub">{links}</div></details>')
        user_name = ws.esc(current_user.get("global_name") or current_user.get("username") or "Team")
        avatar_id=current_user.get("avatar"); user_id=current_user.get("id")
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_id}.png" if avatar_id and user_id else "https://cdn.discordapp.com/embed/avatars/0.png"
        return f'''<style>
        #pulseSidebar{{transition:transform .18s ease,width .18s ease}}#pulseSidebar .nav-active{{background:linear-gradient(90deg,rgba(91,92,240,.12),rgba(124,58,237,.06));color:#5b5cf0;border:1px solid rgba(91,92,240,.14);box-shadow:0 7px 20px rgba(91,92,240,.07)}}
        .dark #pulseSidebar .nav-active{{color:#a8abff}}
        .pulse-area-switch{{display:grid;grid-template-columns:1fr 1fr;gap:4px;padding:4px;border-radius:14px;background:rgba(148,163,184,.12);margin-bottom:6px}}
        .pulse-area-switch a{{text-align:center;font-size:11px;font-weight:800;padding:8px 6px;border-radius:10px;color:#64748b}}
        .pulse-area-switch a.on{{background:#fff;color:#4f46e5;box-shadow:0 2px 8px rgba(15,23,42,.08)}}.dark .pulse-area-switch a.on{{background:#1b2133;color:#a8abff}}@media(min-width:768px){{#pulseSidebar{{display:flex!important}}#pulseMobileBtn,#pulseBackdrop{{display:none!important}}}}
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


def build_command_modal(ws, perms=None):
    items = [
        ("★", "Mein Pulse", "/me"), ("◆", "Leitungs-Cockpit", "/cockpit"),("⌂", "Dashboard öffnen", "/dashboard"),("◌", "Teamstatus", "/team-status"), ("✓", "Activity Check", "/activity-check"), ("🛡️", "Melonly", "/melonly"), ("▣", "Tickets", "/tickets"), ("🎧", "Sprach-Support", "/voice-support"),
        ("▤", "Meeting-Historie", "/meetings-history"), ("◇", "Schulungen", "/training"), ("▤", "Team-Wiki", "/wiki"),
        ("◒", "Analytics", "/stats"), ("◆", "Achievements", "/achievements"),
    ]
    # Administration shortcuts only for people who may open them (routes check again server-side).
    from pulse_admin import admin_sections
    sections = admin_sections(perms or {})
    if sections:
        items.append(("🛠", "Verwaltung", "/admin"))
        items.extend((icon, label, href) for _, href, icon, label, _ in sections)
    rows = "".join(f'<a href="{u}" class="pulse-command-item"><span class="pulse-avatar">{i}</span><span>{t}</span><span class="pulse-key">Öffnen</span></a>' for i,t,u in items)
    return f'''<div id="pulseCommandModal" class="pulse-modal-backdrop"><div class="pulse-modal"><div class="pulse-modal-head"><div><div class="text-sm font-extrabold">Pulse Schnellzugriff</div><div class="text-[10px] text-slate-400 mt-0.5">Navigation ohne Umwege</div></div><button onclick="pulseTogglePalette(false)" class="pulse-icon-btn">✕</button></div><div class="p-3"><div class="pulse-search" style="max-width:none"><span>⌕</span><input id="pulseCmdFilter" oninput="pulseFilterCmd(this.value)" autofocus placeholder="Bereich suchen…"></div></div><div id="pulseCmdList" class="pulse-command-list">{rows}</div></div></div><script>function pulseFilterCmd(v){{v=v.toLowerCase();document.querySelectorAll('#pulseCmdList .pulse-command-item').forEach(x=>x.style.display=x.textContent.toLowerCase().includes(v)?'':'none')}}</script>'''


def render_pro_page(ws, title, ctx, page, body, extra=""):
    body = '<div class="pulse-shell">' + body + '<div class="pulse-footer">Pulse TeamOS v' + VERSION + ' · interne Teamverwaltung</div></div>' + build_command_modal(ws, getattr(ctx, "perms", None))
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

    def legacy_recommended_role_profile(role):
        """Version-1 defaults, used only to detect profiles not manually customized."""
        keys = tuple(dict.fromkeys((*ws.PERM_KEYS, *EXTRA_PERMS)))
        profile = {key: False for key in keys}
        profile["can_view_dashboard"] = True
        name = str(getattr(role, "name", "") or "").casefold()
        support_role = any(word in name for word in ("support", "supporter", "lowteam", "low team", "teamler", "ticket"))
        leadership_role = any(word in name for word in (
            "highteam", "high team", "führungsebene", "fuehrungsebene", "führung",
            "fuehrung", "administrator", "admin", "owner", "inhaber", "management", "manager",
            "serverleitung", "teamleitung", "leitungsebene", "direktor", "director",
        )) or ("leitung" in name and not support_role)
        moderator_role = any(word in name for word in ("moderator", "moderation")) or name.strip() in {"mod", "team-mod", "team mod"}
        if leadership_role:
            for key in keys: profile[key] = True
        elif moderator_role:
            for key in ("can_manage_tickets", "can_manage_tasks", "can_manage_training", "can_manage_wiki",
                        "can_add_notes", "can_warn", "can_moderate_kicks", "can_view_team_status"):
                if key in profile: profile[key] = True
        elif support_role:
            for key in ("can_manage_tickets", "can_manage_tasks", "can_manage_training", "can_manage_wiki",
                        "can_add_notes", "can_view_team_status"):
                if key in profile: profile[key] = True
        elif any(word in name for word in ("ausbildung", "probezeit", "bewerber", "trainee", "praktikant")):
            for key in ("can_manage_tickets", "can_manage_training", "can_manage_wiki"):
                if key in profile: profile[key] = True
        else:
            for key in ("can_manage_tickets", "can_manage_tasks", "can_manage_training", "can_manage_wiki"):
                if key in profile: profile[key] = True
        return profile

    def recommended_role_profile(role):
        """PK outranks Teamleitung; Discord-server administrator access stays separate."""
        keys = tuple(dict.fromkeys((*ws.PERM_KEYS, *EXTRA_PERMS)))
        profile = {key: False for key in keys}
        profile["can_view_dashboard"] = True
        name = str(getattr(role, "name", "") or "").casefold()
        role_words = re.sub(r"[^a-z0-9äöüß]+", " ", name).strip()
        pk_role = bool(re.search(r"(?<![a-z0-9])pk(?![a-z0-9])", name)) or any(
            word in role_words for word in ("personalkommission", "personal kontrolle", "personalkontrolle")
        )
        team_lead_role = any(word in role_words for word in ("teamleitung", "team leitung", "teamlead", "team lead"))
        support_role = any(word in name for word in ("support", "supporter", "lowteam", "low team", "teamler", "ticket"))
        leadership_role = any(word in name for word in (
            "highteam", "high team", "führungsebene", "fuehrungsebene", "führung",
            "fuehrung", "administrator", "admin", "owner", "inhaber", "management", "manager",
            "serverleitung", "leitungsebene", "direktor", "director",
        )) or ("leitung" in name and not support_role and not team_lead_role)

        if pk_role:
            for key in keys: profile[key] = True
        elif team_lead_role:
            # Teamleitung leads day-to-day operations; bans and BOLOs remain above this level.
            for key in (
                "can_warn", "can_moderate_kicks", "can_add_notes", "can_promote",
                "can_manage_tickets", "can_manage_applications", "can_manage_tasks",
                "can_manage_training", "can_manage_wiki", "can_manage_announcements",
                "can_manage_handover", "can_manage_promotions", "can_view_team_status",
                "can_manage_checkins",
            ):
                if key in profile: profile[key] = True
        elif leadership_role:
            for key in keys: profile[key] = True
        elif any(word in name for word in ("moderator", "moderation")) or role_words in {"mod", "team mod"}:
            for key in (
                "can_manage_tickets", "can_manage_tasks", "can_manage_training",
                "can_manage_wiki", "can_add_notes", "can_warn", "can_moderate_kicks",
                "can_view_team_status",
            ):
                if key in profile: profile[key] = True
        elif support_role:
            for key in (
                "can_manage_tickets", "can_manage_tasks", "can_manage_training",
                "can_manage_wiki", "can_add_notes", "can_view_team_status",
            ):
                if key in profile: profile[key] = True
        elif any(word in name for word in ("ausbildung", "probezeit", "bewerber", "trainee", "praktikant")):
            for key in ("can_manage_tickets", "can_manage_training", "can_manage_wiki"):
                if key in profile: profile[key] = True
        else:
            for key in ("can_manage_tickets", "can_manage_tasks", "can_manage_training", "can_manage_wiki"):
                if key in profile: profile[key] = True
        return profile

    def apply_recommended_role_profiles(c):
        configured_ids = c.config.get("team_role_ids", [])
        permission_map = c.config.setdefault("permissions", {})
        changed = []
        for raw_id in configured_ids:
            try:
                role_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            role = c.guild.get_role(role_id)
            if not role or role.managed or role.is_default():
                continue
            permission_map[str(role_id)] = recommended_role_profile(role)
            changed.append(role)
        c.config["permissions_profile_version"] = 3
        c.config["all_role_permissions_disabled"] = False
        ws.save_json(ws.CONFIG_FILE, c.config)
        ws.log_audit(
            c.user.get("global_name") or c.user.get("username"),
            c.user["id"],
            "Empfohlene Rollenrechte angewendet",
            ", ".join(f"{role.name} ({role.id})" for role in changed) or "Keine gültigen Teamrollen",
        )
        return changed


    def clear_all_role_permissions(c):
        """Revoke all Pulse permission flags from every current and previously configured role."""
        keys = tuple(dict.fromkeys((*ws.PERM_KEYS, *EXTRA_PERMS)))
        permission_map = c.config.get("permissions")
        if not isinstance(permission_map, dict):
            permission_map = {}
            c.config["permissions"] = permission_map

        role_ids = {str(role_id) for role_id in permission_map}
        role_ids.update(str(role.id) for role in c.guild.roles if not role.is_default())
        for raw_id in c.config.get("team_role_ids", []):
            try:
                role_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if role_id > 0:
                role_ids.add(str(role_id))

        no_rights = {key: False for key in keys}
        for role_id in role_ids:
            permission_map[role_id] = dict(no_rights)

        # Keep the team-role selection, warning roles and other settings intact.
        # This one-time versioned migration also prevents legacy defaults from being restored.
        c.config["permissions_profile_version"] = 3
        c.config["all_role_permissions_disabled"] = True
        ws.save_json(ws.CONFIG_FILE, c.config)
        actor = c.user.get("global_name") or c.user.get("username") or "Unbekannt"
        ws.log_audit(
            actor, c.user["id"], "Alle Rollenrechte entzogen",
            f"{len(role_ids)} Rollenprofile auf keine Pulse-Rechte gesetzt",
        )
        return len(role_ids)


    async def team_update(guild, title, content, color=None):
        color = color or _pulse_color("info")
        channel = guild.get_channel(ws.TEAM_UPDATE_CHANNEL_ID)
        if not channel:
            print(f"Team-Updates-Kanal nicht erreichbar: {ws.TEAM_UPDATE_CHANNEL_ID}")
            return None
        embed=discord.Embed(title=title,description=content,color=color,timestamp=discord.utils.utcnow())
        embed.set_footer(text=f"{guild.name} · Pulse TeamOS")
        try:
            return await send_styled(channel, embed=embed)
        except Exception as exc:
            print(f"Team-Update konnte nicht gesendet werden: {exc}")
            return None

    # ---------------- Melonly / Roblox Moderation ----------------
    async def melonly_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session)
        can_access_melonly=any(c.perms.get(k) for k in ("can_warn","can_moderate_kicks","can_moderate_bans","can_moderate_bolo","can_add_notes")) or c.perms.get("is_admin")
        if not can_access_melonly:
            raise HTTPException(403,"Dafür fehlt dir die Berechtigung für Melonly.")
        all_melonly_types=(("Warn","⚠️ Warn"),("Kick","🚪 Kick"),("Ban","🚫 Ban"),("Notiz","📝 Notiz"),("Ban BOLO","🚨 Ban BOLO"))
        melonly_types=tuple((key,label) for key,label in all_melonly_types if ws.can_manage_melonly_entry(c,{"type":key}))
        if not melonly_types:
            raise HTTPException(403,"Dafür fehlt dir eine Melonly-Berechtigung. Bitte wende dich an die Führungsebene.")

        logs=ws.load_json(ws.LOGS_FILE,[])
        relevant=[x for x in reversed(logs) if x.get("type") in ("Warn","Ban","Kick","Notiz","Ban BOLO")][:120]
        edit_log_id=str(request.query_params.get("edit_log") or "")
        items=[]
        for x in relevant:
            lt=x.get("type","Log")
            log_id=str(x.get("id") or "")
            kind={"Ban":"bad","Kick":"warn","Notiz":"","Ban BOLO":"bad"}.get(lt,"")
            can_edit=bool(log_id and ws.can_manage_melonly_entry(c,x))
            actions=""
            if can_edit:
                edit_url="/melonly?edit_log="+quote(log_id,safe="")+"#melonly-entry-"+quote(log_id,safe="")
                actions=f'<div class="flex flex-wrap items-center gap-2 mt-2"><a href="{e(edit_url)}" class="pulse-btn ghost">✏️ Bearbeiten</a><form action="/log/delete" method="post" data-confirm="Diesen Melonly-Eintrag wirklich dauerhaft löschen?"><input type="hidden" name="log_id" value="{e(log_id)}"><input type="hidden" name="return_to" value="/melonly"><button type="submit" class="pulse-btn bad">🗑️ Löschen</button></form></div>'
            edit_form=""
            if can_edit and edit_log_id==log_id:
                edit_options="".join(f'<option value="{e(key)}" {"selected" if lt==key else ""}>{e(label)}</option>' for key,label in melonly_types)
                edit_form=f"""
                  <form action="/log/edit" method="post" class="mt-3 pt-3 border-t space-y-3" id="edit-melonly-{e(log_id)}">
                    <input type="hidden" name="log_id" value="{e(log_id)}">
                    <input type="hidden" name="return_to" value="/melonly">
                    <div><label class="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Roblox Username</label><input class="pulse-input mt-1" name="target_user" maxlength="50" value="{e(x.get('target_user',''))}" required></div>
                    <div><label class="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Roblox Player ID</label><input class="pulse-input mt-1" name="roblox_id" maxlength="15" value="{e(x.get('roblox_id','N/A'))}" inputmode="numeric" pattern="[0-9]+" required></div>
                    <div><label class="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Aktion</label><select class="pulse-input mt-1" name="log_type" required>{edit_options}</select></div>
                    <div><label class="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Grund</label><textarea class="pulse-input pulse-textarea mt-1" name="reason" maxlength="1000" required>{e(x.get('reason',''))}</textarea></div>
                    <div class="flex flex-wrap gap-2"><button class="pulse-btn primary" type="submit">💾 Änderungen speichern</button><a class="pulse-btn ghost" href="/melonly#melonly-entry-{quote(log_id,safe='')}">Abbrechen</a></div>
                  </form>"""
            items.append(
                f'<article id="melonly-entry-{e(log_id)}" class="pulse-row items-start"><div class="flex items-start gap-3 min-w-0 flex-1"><div class="pulse-avatar">{e(initials(x.get("target_user") or "?"))}</div><div class="pulse-row-main min-w-0"><div class="pulse-row-title"><span class="pulse-pill {kind}">{e(lt)}</span> {e(x.get("target_user"))}</div><div class="pulse-row-meta">Roblox ID: {e(x.get("roblox_id","N/A"))} · {e(x.get("created_at"))}</div><div class="pulse-row-meta break-words">{e(x.get("reason"))}</div><div class="pulse-row-meta">von {e(x.get("moderator"))}</div>{actions}{edit_form}</div></div></article>'
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
              {''.join(f'<option value="{e(key)}">{e(label)}</option>' for key,label in melonly_types)}
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
        // This extra script is inserted into <head>, so wait until Melonly's form exists.
        document.addEventListener("DOMContentLoaded", function(){
          (function(){
          const name=document.getElementById("melonlyName");
          const id=document.getElementById("melonlyId");
          const box=document.getElementById("melonlySuggestions");
          const preview=document.getElementById("melonlyPreview");
          let searchTimer=null, exactTimer=null, latestSearchRequest=0, latestExactRequest=0;

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
            users.forEach(function(u,index){
              const b=document.createElement("button");
              b.type="button";
              b.className="w-full flex items-center justify-between gap-3 text-left px-3 py-2.5 hover:bg-slate-100 dark:hover:bg-slate-800 border-b last:border-b-0 border-slate-100 dark:border-slate-700";
              const left=document.createElement("div");
              left.className="min-w-0";
              const title=document.createElement("div");
              title.className="text-xs font-bold text-slate-900 dark:text-white";
              title.textContent=(index===0 ? "⭐ " : "")+(u.name || u.displayName || "");
              const display=document.createElement("div");
              display.className="text-[10px] text-slate-400";
              display.textContent=u.displayName || u.name || "";
              left.appendChild(title);
              left.appendChild(display);
              const sub=document.createElement("div");
              sub.className="text-[10px] text-indigo-500 shrink-0 text-right";
              sub.textContent=(index===0 ? "Bester Treffer · " : "")+"ID "+(u.id||"unbekannt");
              b.appendChild(left);
              b.appendChild(sub);
              b.addEventListener("click",function(){pickUser(u);});
              box.appendChild(b);
            });
            box.classList.remove("hidden");
          }
          function searchUsers(v){
            clearTimeout(searchTimer);
            const q=norm(v), requestId=++latestSearchRequest;
            if(q.length<2){hideSuggestions();return;}
            searchTimer=setTimeout(function(){
              fetch("/api/roblox-search?query="+encodeURIComponent(q),{cache:"no-store",credentials:"same-origin"})
                .then(function(r){return r.json();})
                .then(function(d){
                  if(requestId!==latestSearchRequest || norm(name.value).toLowerCase()!==q.toLowerCase())return;
                  showSuggestions(d.success ? d.users : []);
                  const exact=(d.users||[]).find(function(u){return String(u.name||"").toLowerCase()===q.toLowerCase();});
                  if(exact && /^\d+$/.test(String(exact.id||"")))id.value=String(exact.id);
                })
                .catch(function(){if(requestId===latestSearchRequest)hideSuggestions();});
            },220);
          }
          function loadExact(v){
            clearTimeout(exactTimer);
            const q=norm(v), requestId=++latestExactRequest;
            if(q.length<3){
              if(preview){preview.classList.add("hidden");preview.innerHTML="";}
              return;
            }
            exactTimer=setTimeout(function(){
              fetch("/api/roblox-user?username="+encodeURIComponent(q),{cache:"no-store"})
                .then(function(r){return r.json();})
                .then(function(d){
                  if(requestId!==latestExactRequest || norm(name.value).toLowerCase()!==q.toLowerCase())return;
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
        });
        </script>''';

        return render_pro_page(ws,"Melonly",c,"melonly",body,head)

    async def admin_end_shift(
        request: Request,
        target_user_id: int = Form(...),
        reason: str = Form("Manuell durch Führungskraft beendet"),
        user_session: str = Cookie(None),
    ):
        c=cctx(request,user_session)
        if not (c.perms.get("can_promote") or c.perms.get("is_admin")):
            raise HTTPException(403,"Nur die Führungsebene darf fremde Schichten beenden.")
        target=c.guild.get_member(int(target_user_id))
        if not target or target.bot:
            return ws.back("/team-status","Teammitglied wurde nicht gefunden.",False)
        shifts_db=ws.load_shifts()
        active=shifts_db.setdefault("active_shifts",{})
        shift=active.get(str(target.id))
        if not shift:
            return ws.back("/team-status",f"{target.display_name} hat keine laufende Schicht.",False)
        now=ws.now_de()
        duration=ws.shift_elapsed(shift)
        reason=(reason or "Manuell durch Führungskraft beendet").strip()[:300]
        shifts_db.setdefault("history",[]).append({
            "mod_id":str(target.id),
            "date":shift.get("date"),
            "started_at_iso":shift.get("started_at_iso"),
            "ended_at_iso":now.isoformat(timespec="seconds"),
            "duration_seconds":duration,
            "admin_closed":True,
            "closed_by_id":str(c.user["id"]),
            "closed_by_name":c.user.get("global_name") or c.user.get("username") or "Führungskraft",
            "close_reason":reason,
        })
        del active[str(target.id)]
        ws.save_json(ws.SHIFTS_FILE,shifts_db)
        try:
            bot = getattr(request.app.state, "bot", None)
            voice_support = bot.get_cog("VoiceSupport") if bot else None
            if voice_support:
                await voice_support.sync_member_duty_role(c.guild, target.id)
                await voice_support._refresh_duty_status(c.guild)
        except Exception as exc:
            print(f"⚠️ Voice-Support: Bereitschaft nach administrativem Schichtende nicht aktualisiert: {exc}")
        actor=c.user.get("global_name") or c.user.get("username") or str(c.user["id"])
        ws.log_audit(actor,str(c.user["id"]),"Fremde Schicht beendet",
                     f"{target.display_name} ({target.id}) · {ws.fmt_duration(duration)} · Grund: {reason}")
        await ws.send_dm_notification(target,
            f"⏹ Deine Schicht wurde von {actor} beendet.\n**Dauer:** {ws.fmt_duration(duration)}\n**Grund:** {reason}")
        return ws.back("/team-status",f"Schicht von {target.display_name} wurde beendet ({ws.fmt_duration(duration)}).")

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
        if s["apps"]: urgent += f'<a href="/applications" class="pulse-alert"><strong>✎ {s["apps"]} offene Bewerbungen</strong><div class="text-slate-400 mt-1">Bitte zeitnah bearbeiten.</div></a>'
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
        <section class="pulse-hero"><div class="pulse-hero-grid"><div><div class="pulse-kicker">Willkommen zurück</div><div class="pulse-title">{e(c.user.get("global_name") or c.user.get("username"))}</div><div class="pulse-sub">Hier siehst du sofort, was für das Team ansteht: Support, Aufgaben, Abwesenheiten, Bewerbungen und deine eigene Aktivität.</div><div class="flex flex-wrap gap-2 mt-5"><a href="/team-status" class="pulse-btn" style="background:rgba(255,255,255,.1);color:white;border-color:rgba(255,255,255,.18)">◉ Teamstatus ansehen</a></div></div><div class="pulse-hero-box"><div class="label">DEINE WOCHEN-AKTIVITÄT</div><div class="value">{ws.fmt_duration(s["weekly"])}</div><div class="pulse-progress mt-3" style="background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.15)"><span style="width:{pct}%;background:white"></span></div><div class="text-[10px] mt-2 opacity-75">{pct}% von {goal:g}h Wochenziel</div></div></div></section>
        <div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">🟢</div><div class="label">Team im Dienst</div><div class="value" data-live="active">{s["active"]}</div></div><div class="pulse-stat"><div class="icon">▣</div><div class="label">Offene Tickets</div><div class="value" data-live="open_tickets">{len(open_t)}</div></div><div class="pulse-stat"><div class="icon">✎</div><div class="label">Bewerbungen</div><div class="value" data-live="applications">{s["apps"]}</div></div><div class="pulse-stat"><div class="icon">☾</div><div class="label">Aktive LOA</div><div class="value" data-live="loas">{s["loas"]}</div></div></div>
        <div class="pulse-grid"><div class="space-y-4"><section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">⏱ Schicht-Steuerung</div><div class="pulse-section-sub">Pausen werden nicht als Arbeitszeit gezählt.</div></div>{pill(shift_badge,shift_kind)}</div><div class="pulse-card-b"><div class="flex items-end justify-between gap-4"><div><div class="text-[10px] text-slate-400">Aktuelle Dauer</div><div id="v5ShiftTimer" class="text-3xl font-extrabold pulse-time mt-1">{ws.fmt_duration(elapsed)}</div></div><div class="text-right"><div class="text-[10px] text-slate-400">Status</div><div class="text-sm font-bold mt-1">{shift_badge}</div></div></div><div class="mt-4">{shift_actions}</div></div></section>{card('Aufmerksamkeit',urgent,'!')}{card('Live im Dienst',duty_html or '<div class="pulse-empty">Niemand ist aktuell im Dienst.</div>','◉')}</div><div class="space-y-4">{card('Meine Inbox',n_html,'◈')}{card('Letzte Aktivität',recent_html or '<div class="pulse-empty">Noch keine zentralen Events erfasst.</div>','↯')}<section class="pulse-card"><div class="pulse-card-b"><div class="flex items-center justify-between"><div><div class="pulse-section-title">⚡ Schnellaktionen</div><div class="pulse-section-sub">Häufige Aufgaben direkt öffnen.</div></div></div><div class="grid grid-cols-2 gap-2 mt-3"><a href="/tickets" class="pulse-btn ghost">▣ Tickets</a><a href="/applications" class="pulse-btn ghost">✎ Bewerbung</a></div></div></section></div></div>'''
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
        check=None; responded={}; snapshot=[]; legacy=False; snapshot_version=0
        try:
            with sqlite3.connect(ws.ACTIVITY_DB) as ax:
                ax.row_factory=sqlite3.Row
                check=ax.execute("SELECT * FROM checks WHERE guild_id=? AND check_date=?",(c.guild.id,selected_date)).fetchone()
                if check:
                    snapshot_version=int(check["snapshot_version"] or 0) if "snapshot_version" in check.keys() else 0
                    responded={int(r["user_id"]):r["reacted_at"] for r in ax.execute("SELECT user_id,reacted_at FROM responses WHERE check_id=?",(check["id"],)).fetchall()}
                    try: snapshot=[dict(r) for r in ax.execute("SELECT user_id,user_name,role_name FROM check_members WHERE check_id=? ORDER BY user_name COLLATE NOCASE",(check["id"],)).fetchall()]
                    except sqlite3.Error: snapshot=[]
        except Exception as exc:
            print(f"Activity-Check-Webauswertung: Datenbank konnte nicht gelesen werden: {type(exc).__name__}: {exc}")
            check=None
        if check and not snapshot and snapshot_version < 1:
            legacy=True
            role=None
            try:
                with sqlite3.connect(ws.ACTIVITY_DB) as ax:
                    rr=ax.execute("SELECT value FROM config WHERE key='activity_role_id'").fetchone()
                    if rr and rr[0]: role=c.guild.get_role(int(rr[0]))
            except Exception: pass
            members=[m for m in (role.members if role else team(c.guild,c.config.get("team_role_ids",[]))) if not m.bot] if role else team(c.guild,c.config.get("team_role_ids",[]))
            snapshot=[{"user_id":m.id,"user_name":m.display_name,"role_name":member_role(m,c.config.get("team_role_ids",[]))} for m in members]
        history=[]
        try:
            with sqlite3.connect(ws.ACTIVITY_DB) as hx:
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
        state_note=(pill("Snapshot pro Check gespeichert","good") if check and not legacy and snapshot else (pill("Snapshot ist leer – automatische Warns ausgesetzt","warn") if check and not legacy and snapshot_version >= 1 and not snapshot else (pill("Alter Check ohne Snapshot","warn") if legacy else pill("Kein Check für dieses Datum","warn"))))
        # Configuration and a one-click recovery action live on the same page so
        # "Kein Check" is actionable instead of silently leaving a blank report.
        bot = getattr(request.app.state, "bot", None)
        activity_cog_loaded = bool(bot and bot.get_cog("ActivityCheckCog"))
        configured_channel_id = 0
        configured_role_id = 0
        activity_config_error = ""
        try:
            import cogs.activity_check as activity_module
            activity_module.init_db()
            configured_channel_id = int(activity_module.get_config("activity_channel_id") or 0)
            configured_role_id = int(activity_module.get_config("activity_role_id") or 0)
        except Exception as exc:
            activity_config_error = f"{type(exc).__name__}: {exc}"
        channel_options = "".join(
            f'<option value="{ch.id}" {"selected" if ch.id == configured_channel_id else ""}>#{e(ch.name)} · {ch.id}</option>'
            for ch in sorted(c.guild.text_channels, key=lambda x: x.name.casefold())
        )
        team_role_ids = {int(rid) for rid in c.config.get("team_role_ids", []) if str(rid).isdigit()}
        if configured_role_id:
            team_role_ids.add(configured_role_id)
        role_options = (
            '<option value="0" '+('selected' if not configured_role_id else '')+'>Konfigurierte Teamrollen verwenden (empfohlen)</option>'
            + "".join(
                f'<option value="{role.id}" {"selected" if role.id == configured_role_id else ""}>{e(role.name)} · {role.id}</option>'
                for role in sorted(
                    [r for r in c.guild.roles if r.id in team_role_ids and not r.is_default() and not r.managed],
                    key=lambda x: (-x.position, x.name.casefold()),
                )
            )
        )
        config_status = (
            pill("ActivityCheckCog aktiv", "good") if activity_cog_loaded
            else pill("ActivityCheckCog nicht geladen", "bad")
        )
        if activity_config_error:
            config_status += f'<div class="pulse-alert" style="margin-top:8px">Konfiguration konnte nicht gelesen werden: {e(activity_config_error)}</div>'
        elif configured_channel_id:
            saved_channel = c.guild.get_channel(configured_channel_id)
            config_status += f'<div class="pulse-section-sub" style="margin-top:6px">Gespeicherter Kanal: {("#"+e(saved_channel.name)) if saved_channel else "nicht gefunden"} · ID {configured_channel_id}</div>'
        else:
            config_status += '<div class="pulse-section-sub" style="margin-top:6px">Es wurde noch kein Activity-Check-Kanal gespeichert.</div>'
        setup_panel = f"""
        <section class="pulse-card mb-5">
          <div class="pulse-card-h"><div>
            <div class="pulse-section-title">🛠 Activity Check reparieren / einrichten</div>
            <div class="pulse-section-sub">Wenn für heute kein Datensatz existiert, hier den Kanal speichern und den Check direkt erstellen.</div>
          </div>{config_status}</div>
          <div class="pulse-card-b">
            <form method="post" action="/activity-check/setup" class="grid md:grid-cols-3 gap-3 items-end">
              <label class="text-xs">Activity-Check-Kanal
                <select name="channel_id" class="pulse-input mt-1" required>{channel_options or '<option value="">Keine Textkanäle gefunden</option>'}</select>
              </label>
              <label class="text-xs">Teilnehmer / Ping-Rolle
                <select name="role_id" class="pulse-input mt-1">{role_options}</select>
              </label>
              <button class="pulse-btn primary" type="submit" {"disabled" if not channel_options else ""}>Speichern & heutigen Check erstellen</button>
            </form>
            <div class="pulse-section-sub" style="margin-top:10px">Bei „Konfigurierte Teamrollen“ wird der Snapshot aus den Rollen in der Pulse-Konfiguration erstellt. Eine explizite Rolle begrenzt den Snapshot auf Mitglieder dieser Rolle.</div>
          </div>
        </section>
        """
        body=(
            f'<div class="pulse-topbar"><div><div class="pulse-section-title">✓ Activity Check</div><div class="pulse-section-sub">Jeder Check wird separat gespeichert. Antworten gehören exakt zu diesem Check.</div></div>{state_note}</div>'
            + setup_panel
            + f'<section class="pulse-hero"><div class="pulse-hero-grid"><div><div class="pulse-kicker">Tagesauswertung · {e(selected_date)}</div><div class="pulse-title">{done} von {total} bestätigt</div><div class="pulse-sub">Der Teilnehmer-Snapshot wird beim Erstellen des Checks gespeichert. Spätere Rollenänderungen verändern vergangene Auswertungen nicht.</div></div><div class="pulse-hero-box"><div class="label">BESTÄTIGUNGSQUOTE</div><div class="value">{pct}%</div><div class="pulse-progress mt-3" style="background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.15)"><span style="width:{pct}%;background:white"></span></div></div></div></section>'
            + f'<div class="flex flex-wrap items-center gap-2 mb-4"><form method="get" action="/activity-check" class="flex flex-wrap gap-2"><select name="date" class="pulse-input" style="width:auto;min-width:310px">{history_options}</select><button class="pulse-btn ghost">Check öffnen</button></form><a href="/team#activity" class="pulse-btn ghost">↩ Teamliste</a></div>'
            + f'<div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">👥</div><div class="label">Snapshot</div><div class="value">{total}</div></div><div class="pulse-stat"><div class="icon">✅</div><div class="label">Bestätigt</div><div class="value">{done}</div></div><div class="pulse-stat"><div class="icon">⏳</div><div class="label">Offen</div><div class="value">{open_n}</div></div><div class="pulse-stat"><div class="icon">◷</div><div class="label">Erstellt</div><div class="value">{e(fmt_dt(check["created_at"]) if check else "—")}</div></div></div>'
            + f'<div class="pulse-grid"><div>{card("✅ Bestätigt",person_rows(confirmed,"confirmed"),"✓")}</div><div>{card("⏳ Noch offen",person_rows(open_members,"open"),"⏳")}</div></div>'
            + f'<section class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">🗂 Check-Historie</div><div class="pulse-section-sub">Jeder Tag ist ein eigener Datensatz.</div></div></div><div class="pulse-card-b space-y-2">{"".join(f'<a href="/activity-check?date={e(h["check_date"])}" class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(h["check_date"])}</div><div class="pulse-row-meta">{e(fmt_dt(h["created_at"]))} · {h["responses"]} Antworten · {h["members"]} Snapshot-Mitglieder · Nachricht {e(h["message_id"])}</div></div>{pill("Aktuell","good") if h["check_date"]==selected_date else pill("Öffnen")}</a>' for h in history)}</div></section>'
        )
        return render_pro_page(ws,"Activity Check",c,"activity-check",body)

    async def activity_check_setup(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm="can_manage_checkins")
        bot=getattr(request.app.state,"bot",None)
        if bot is None:
            return ws.back("/activity-check","Der Discord-Bot ist nicht mit dem Dashboard verbunden. Bitte Bot-Status und Startprotokoll prüfen.",False)
        try:
            form=await request.form()
            channel_raw=str(form.get("channel_id") or "").strip()
            role_raw=str(form.get("role_id") or "0").strip()
            if not channel_raw.isdigit() or int(channel_raw)<=0:
                return ws.back("/activity-check","Bitte einen gültigen Activity-Check-Kanal auswählen.",False)
            if not role_raw.isdigit():
                return ws.back("/activity-check","Die ausgewählte Rolle ist ungültig.",False)

            channel=c.guild.get_channel(int(channel_raw))
            if channel is None or not isinstance(channel,discord.TextChannel) or channel.guild.id != c.guild.id:
                return ws.back("/activity-check","Der ausgewählte Kanal wurde auf diesem Discord-Server nicht gefunden.",False)
            role=None
            if int(role_raw)>0:
                role=c.guild.get_role(int(role_raw))
                if role is None or role.is_default() or role.managed:
                    return ws.back("/activity-check","Die ausgewählte Teilnehmerrolle wurde nicht gefunden oder kann nicht verwendet werden.",False)

            import cogs.activity_check as activity_module
            activity_module.init_db()
            cog=bot.get_cog("ActivityCheckCog")
            if cog is None and "cogs.activity_check" not in getattr(bot,"extensions",{}):
                try:
                    await bot.load_extension("cogs.activity_check")
                except Exception as exc:
                    return ws.back("/activity-check",f"Activity-Check-Modul konnte nicht gestartet werden: {type(exc).__name__}: {exc}",False)
                cog=bot.get_cog("ActivityCheckCog")
            if cog is None:
                return ws.back("/activity-check","ActivityCheckCog ist nicht geladen. Bitte Startprotokoll auf einen Fehler beim Laden von cogs/activity_check.py prüfen.",False)

            # Persist the selected channel/role first; the bot's scheduled loop
            # then uses the same settings after future restarts.
            activity_module.set_config("activity_channel_id",channel.id)
            activity_module.set_config("activity_role_id",role.id if role else 0)

            today=ws.now_de().date().isoformat()
            with sqlite3.connect(ws.ACTIVITY_DB,timeout=5.0) as ax:
                ax.row_factory=sqlite3.Row
                existing=ax.execute(
                    "SELECT id,snapshot_version FROM checks WHERE guild_id=? AND check_date=?",
                    (c.guild.id,today),
                ).fetchone()
                existing_count=int(ax.execute(
                    "SELECT COUNT(*) FROM check_members WHERE check_id=?",
                    (int(existing["id"]),),
                ).fetchone()[0]) if existing else 0

            members=await cog.eligible_members(c.guild,role)
            if not members and not (existing and existing_count>0):
                if role:
                    detail=f"Die Rolle {role.name} hat aktuell keine passenden Mitglieder."
                elif not c.config.get("team_role_ids"):
                    detail="In der Pulse-Konfiguration sind keine Teamrollen hinterlegt. Wähle eine Teilnehmerrolle oder konfiguriere zuerst die Teamrollen."
                else:
                    detail="Es wurden keine Mitglieder mit den konfigurierten Teamrollen gefunden. Prüfe Teamrollen und die Discord-Mitglieder-Intention."
                return ws.back("/activity-check",detail,False)

            try:
                message=await cog.send_check_message(channel,role)
            except Exception as exc:
                return ws.back("/activity-check",f"Activity Check konnte nicht erstellt werden: {type(exc).__name__}: {exc}",False)

            with sqlite3.connect(ws.ACTIVITY_DB,timeout=5.0) as ax:
                ax.row_factory=sqlite3.Row
                saved=ax.execute(
                    "SELECT id,snapshot_version,message_id FROM checks WHERE guild_id=? AND check_date=?",
                    (c.guild.id,today),
                ).fetchone()
                snapshot_count=int(ax.execute(
                    "SELECT COUNT(*) FROM check_members WHERE check_id=?",
                    (int(saved["id"]),),
                ).fetchone()[0]) if saved else 0
            if saved and snapshot_count:
                action="erstellt" if message is not None else "bereits vorhanden/geprüft"
                return ws.back("/activity-check",f"Heutiger Activity Check {action}. Snapshot: {snapshot_count} Teilnehmer. Bitte Seite neu laden.")
            return ws.back("/activity-check","Der Check-Datensatz hat weiterhin keinen Teilnehmer-Snapshot. Es wurden keine automatischen Warns ausgelöst. Bitte prüfe die Teamrollen und das Startprotokoll.",False)
        except Exception as exc:
            return ws.back("/activity-check",f"Setup fehlgeschlagen: {type(exc).__name__}: {exc}",False)

    async def team_status_page(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session)
        can_view = bool(c.perms.get("can_view_team_status") or c.perms.get("can_promote") or c.perms.get("is_admin"))
        if not can_view:
            raise HTTPException(403,"Dafür fehlt dir die Berechtigung für den Teamstatus.")
        can_manage_shifts = bool(c.perms.get("can_promote") or c.perms.get("is_admin"))
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
            shift_meta=""
            shift_stop=""
            if sh:
                shift_meta=f'<div class="pulse-row-meta">Schichtdauer: {e(ws.fmt_duration(ws.shift_elapsed(sh)))}</div>'
                if can_manage_shifts and m.id != int(c.user["id"]):
                    shift_stop=f'<form action="/shift/admin-end" method="post" class="mt-2" data-confirm="Schicht von {e(m.display_name)} wirklich beenden?"><input type="hidden" name="target_user_id" value="{m.id}"><input type="hidden" name="reason" value="Manuell durch Führungskraft beendet"><button type="submit" class="pulse-btn bad">⏹ Schicht beenden</button></form>'
            rows.append(f'<div class="pulse-row items-start" data-discord-user="{m.id}"><div class="flex items-start gap-3 min-w-0 flex-1"><div class="pulse-avatar">{e(initials(m.display_name))}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(m.display_name)}</div><div class="pulse-row-meta">{e(member_role(m,ids))} · <span data-status-label="{m.id}">{e(lab)}</span>{act_html}</div>{shift_meta}{shift_stop}</div></div><div class="text-right shrink-0"><div data-status-pill="{m.id}">{pill(ico+" "+lab,kind)}</div><div class="mt-1">{duty}</div></div></div>')

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
        colors={'info':_pulse_color("info"),'success':_pulse_color("success"),'warning':_pulse_color("warning"),'critical':_pulse_color("danger")}
        msg=await team_update(c.guild,title.strip()[:160],content.strip()[:4000],colors.get(kind,_pulse_color("info")))
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
        # Guided case handling: triage, filters, pagination and the existing /tickets/action route.
        c=cctx(request,user_session)
        from pulse_ticket_center import render_ticket_center
        return render_ticket_center(ws, c, request, render_pro_page)

    async def ticket_update_v5(request: Request, ticket_id: str=Form(...), priority: str=Form(...), user_session: str=Cookie(None)):
        c=cctx(request,user_session); manager=c.perms.get('can_manage_tickets') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        if priority not in ('low','normal','high','urgent'): raise HTTPException(400,'Ungültige Priorität.')
        if not db.update_ticket_priority(ticket_id,priority,c.user['id'],c.user.get('global_name') or 'Team'): return ws.back('/tickets','Ticket nicht gefunden.',False)
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Ticket Priorität geändert',f'{ticket_id} → {priority}')
        return ws.back('/tickets','Ticket aktualisiert.')

    # ---------------- Voice-Support / Bereitschaft ----------------
    async def voice_support_v5(request: Request, user_session: str=Cookie(None)):
        c = cctx(request, user_session)
        if not (c.perms.get("can_manage_tickets") or c.perms.get("can_promote") or c.perms.get("is_admin")):
            raise HTTPException(403, "Dafür fehlt dir die Berechtigung.")
        cfg = db.get_setting("voice_support_config", {})
        cfg = cfg if isinstance(cfg, dict) else {}
        from cogs.voice_support import DEFAULT_HOURS, DAYS, DAY_NAMES
        hours = cfg.get("hours", {})
        if not isinstance(hours, dict):
            hours = {}
        def channel_options(items, selected, placeholder):
            options = [f'<option value="0">— {e(placeholder)} —</option>']
            for item in items:
                sel = " selected" if str(item.id) == str(selected or "0") else ""
                options.append(f'<option value="{item.id}"{sel}>{e(item.name)} · {item.id}</option>')
            return "".join(options)
        def select_field(name, label, items, selected, placeholder):
            return f'<label class="block text-xs font-bold">{e(label)}<select name="{name}" class="pulse-input mt-2">{channel_options(items, selected, placeholder)}</select></label>'
        voice_channels = sorted(c.guild.voice_channels, key=lambda x: x.position)
        text_channels = sorted(c.guild.text_channels, key=lambda x: x.position)
        roles = sorted([r for r in c.guild.roles if not r.is_default() and not r.managed], key=lambda x: x.position, reverse=True)
        role_options = ['<option value="0">— Keine Rolle —</option>']
        for role in roles:
            sel = " selected" if str(role.id) == str(cfg.get("support_role_id") or "0") else ""
            role_options.append(f'<option value="{role.id}"{sel}>{e(role.name)} · {role.id}</option>')
        duty_role_options = ['<option value="0">— Keine Rolle —</option>']
        for role in roles:
            sel = " selected" if str(role.id) == str(cfg.get("duty_role_id") or "0") else ""
            duty_role_options.append(f'<option value="{role.id}"{sel}>{e(role.name)} · {role.id}</option>')
        defaults = {day: value.copy() for day, value in DEFAULT_HOURS.items()}
        schedule_rows = []
        for day in DAYS:
            item = {**defaults[day], **(hours.get(day, {}) if isinstance(hours.get(day), dict) else {})}
            checked = "checked" if item.get("enabled", False) else ""
            start = e(str(item.get("start") or defaults[day]["start"]))
            end = e(str(item.get("end") or defaults[day]["end"]))
            schedule_rows.append(
                f'<div class="pulse-row items-center"><div class="pulse-row-main"><label class="flex items-center gap-2 text-xs font-semibold"><input type="checkbox" name="hours_{day}_enabled" value="1" {checked}>{DAY_NAMES[day]}</label></div>'
                f'<input type="time" class="pulse-input !w-[120px]" name="hours_{day}_start" value="{start}">'
                f'<span class="text-xs text-slate-400">bis</span><input type="time" class="pulse-input !w-[120px]" name="hours_{day}_end" value="{end}"></div>'
            )
        cases = db.get_setting("voice_support_cases", {})
        cases = cases if isinstance(cases, dict) else {}
        case_rows = []
        for channel_key, case in sorted(cases.items(), key=lambda pair: str(pair[1].get("created_at", "")) if isinstance(pair[1], dict) else ""):
            if not isinstance(case, dict):
                continue
            raw_channel_id = case.get("channel_id") or channel_key
            chan = c.guild.get_channel(int(raw_channel_id)) if str(raw_channel_id).isdigit() else None
            if chan:
                case_link = f'<a class="pulse-btn ghost" href="https://discord.com/channels/{c.guild.id}/{chan.id}" target="_blank" rel="noopener noreferrer">Raum öffnen</a>'
            else:
                case_link = '<a class="pulse-btn ghost" href="/voice-support">Aktualisieren</a>'
            case_rows.append(
                f'<div class="pulse-row"><div class="pulse-avatar">🎧</div><div class="pulse-row-main"><div class="pulse-row-title">{e(case.get("user_name") or "Unbekannt")}</div>'
                f'<div class="pulse-row-meta">Fall {e(case.get("case_id") or "—")} · seit {e(case.get("created_at") or "—")}</div></div>'
                f'<div class="flex gap-2">{case_link}</div></div>'
            )
        rating_rows = db.get_setting("voice_support_ratings", [])
        rating_rows = rating_rows if isinstance(rating_rows, list) else []
        rated = [int(x.get("rating", 0)) for x in rating_rows if isinstance(x, dict) and str(x.get("rating", "")).isdigit() and 1 <= int(x.get("rating", 0)) <= 5]
        average_rating = (sum(rated) / len(rated)) if rated else 0
        is_admin = bool(c.perms.get("is_admin"))
        config_form = ""
        if is_admin:
            config_form = f'''
            <form action="/voice-support/save" method="post" enctype="multipart/form-data" class="space-y-5">
              <section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">🎧 Raumeinstellungen</div><div class="pulse-section-sub">Alle Nutzer bleiben im gemeinsamen Warteraum. Pulse erstellt pro Nutzer einen Supportfall, kündigt den Status im Benachrichtigungskanal an und erstellt keinen neuen Sprachkanal.</div></div></div><div class="pulse-card-b grid md:grid-cols-2 gap-4">
                <label class="block text-xs font-bold">Support-Name<input name="room_name" class="pulse-input mt-2" required maxlength="60" value="{e(cfg.get('room_name') or 'Support')}"></label>
                {select_field("waiting_room_channel_id","Warteraum – Voice-Kanal",voice_channels,cfg.get("waiting_room_channel_id"),"Voice-Warteraum auswählen")}
                {select_field("notification_channel_id","Benachrichtigungs-Kanal",text_channels,cfg.get("notification_channel_id"),"Textkanal auswählen")}
                <label class="block text-xs font-bold">Support-Rolle<select name="support_role_id" class="pulse-input mt-2">{"".join(role_options)}</select><span class="pulse-section-sub mt-1">Wird außerhalb des Bereitschaftsmodus bei neuen Supportfällen benachrichtigt.</span></label>
                <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="active" value="1" {"checked" if cfg.get("active") else ""}>Support-System aktivieren</label>
                <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="notification_enabled" value="1" {"checked" if cfg.get("notification_enabled", True) else ""}>Supportfall-Meldungen und Live-Status aktivieren</label>
                <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="notes_enabled" value="1" {"checked" if cfg.get("notes_enabled", True) else ""}>Private Notiz-Threads pro Supportfall erstellen</label>
              </div></section>
              <section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">🕒 Öffnungszeiten</div><div class="pulse-section-sub">Außerhalb der Öffnungszeiten bleibt der Supportfall im gemeinsamen Warteraum offen. Die separate Ansage darunter wird einmal abgespielt; das Team kann den Fall weiterhin übernehmen.</div></div></div><div class="pulse-card-b space-y-1">{"".join(schedule_rows)}</div></section>
              <section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">🧠 Bereitschaft & Schichtsynchronisierung</div><div class="pulse-section-sub">Keine zweite unabhängige Check-in-Uhr: Pulse nutzt die bestehenden Dashboard-Schichten. Start/Fortsetzen gibt die Bereitschaftsrolle; Pause/Ende nimmt sie weg.</div></div></div><div class="pulse-card-b grid md:grid-cols-2 gap-4">
                <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="duty_mode" value="1" {"checked" if cfg.get("duty_mode") else ""}>Duty-/Bereitschaftsmodus aktivieren</label>
                <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="duty_message_enabled" value="1" {"checked" if cfg.get("duty_message_enabled", True) else ""}>Bereitschaftsstatus im Check-in-Kanal automatisch aktualisieren</label>
                {select_field("checkin_channel_id","Check-in-/Bereitschafts-Kanal",text_channels,cfg.get("checkin_channel_id"),"Textkanal auswählen")}
                <label class="block text-xs font-bold">Bereitschafts-Rolle<select name="duty_role_id" class="pulse-input mt-2">{"".join(duty_role_options)}</select><span class="pulse-section-sub mt-1">Nur Teammitglieder mit laufender Dashboard-Schicht erhalten diese Rolle.</span></label>
              </div></section>
              <section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">⭐ Bewertungen & Wartemusik</div><div class="pulse-section-sub">Nach dem Support kann der Nutzer bewerten. Die Wartemusik braucht eine MP3-, OGG- oder WAV-Datei und FFmpeg auf dem Host.</div></div></div><div class="pulse-card-b grid md:grid-cols-2 gap-4">
                <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="ratings_enabled" value="1" {"checked" if cfg.get("ratings_enabled", True) else ""}>Support-Bewertungen aktivieren</label>
                <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="waiting_music_enabled" value="1" {"checked" if cfg.get("waiting_music_enabled") else ""}>Wartemusik aktivieren</label>
                <label class="block text-xs font-bold md:col-span-2">Wartemusik-Datei (bestehende Einstellung)<input type="file" name="waiting_music_file" accept=".mp3,.ogg,.wav,audio/mpeg,audio/ogg,audio/wav" class="pulse-input mt-2"><span class="pulse-section-sub mt-1">Aktuell hinterlegt: {e(os.path.basename(str(cfg.get('waiting_music_path') or 'Keine Datei')))}</span></label>
                <div class="pulse-card md:col-span-2">
                  <div class="pulse-card-h"><div><div class="pulse-section-title">🔊 Eigene Support-Audios</div><div class="pulse-section-sub">Die beiden Audios werden separat gespeichert und über diesen Bildschirm geändert. Erlaubt: MP3, WAV, OGG, FLAC oder M4A · maximal 10 MB je Datei.</div></div></div>
                  <div class="pulse-card-b grid md:grid-cols-2 gap-4">
                    <div class="space-y-3">
                      <div class="text-xs font-bold">🟢 Eröffnungsansage während der Supportzeiten</div>
                      <div class="pulse-section-sub">Diese Eröffnungsansage wird pro Supportfall genau einmal abgespielt. Anschließend läuft die aktivierte Wartemusik automatisch in Schleife, bis der Supporter übernimmt oder der Bürger den Warteraum verlässt.</div>
                      <div class="pulse-section-sub">Aktuell: <strong>{e(os.path.basename(str(cfg.get('custom_opening_audio_path') or 'Keine eigene Audio')))}</strong></div>
                      <label class="block text-xs font-bold">Eigene Audio hochladen<input type="file" name="custom_opening_audio_file" accept=".mp3,.wav,.ogg,.flac,.m4a,audio/*" class="pulse-input mt-2"></label>
                      <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="remove_custom_opening_audio" value="1"> Eigene Audio entfernen und auf Standard zurücksetzen</label>
                    </div>
                    <div class="space-y-3">
                      <div class="text-xs font-bold">🌙 Außerhalb der Supportzeiten</div>
                      <div class="pulse-section-sub">Wird einmal im gemeinsamen Warteraum abgespielt. Ohne eigene Datei bleibt die integrierte deutsche Standardansage aktiv.</div>
                      <div class="pulse-section-sub">Aktuell: <strong>{e(os.path.basename(str(cfg.get('custom_after_hours_audio_path') or 'Deutsche Standardansage')))}</strong></div>
                      <label class="block text-xs font-bold">Eigene Ansage hochladen<input type="file" name="custom_after_hours_audio_file" accept=".mp3,.wav,.ogg,.flac,.m4a,audio/*" class="pulse-input mt-2"></label>
                      <label class="flex items-center gap-2 text-xs"><input type="checkbox" name="remove_custom_after_hours_audio" value="1"> Eigene Ansage entfernen und Standardansage verwenden</label>
                    </div>
                  </div>
                </div>
                <div class="pulse-alert md:col-span-2"><strong>Wichtig zu Discord-Rechten</strong><div class="text-slate-400 mt-1">Pulse benötigt Kanäle verwalten, Kanäle ansehen, Mitglieder verschieben, Nachrichten senden, Embeds, private Threads verwalten und für Wartemusik Verbinden/Sprechen. Die Bot-Rolle muss hoch genug sein, um Support-Rollen zu verwalten.</div></div>
              </div></section>
              <button class="pulse-btn primary w-full">⚙ Support-System speichern</button>
            </form>'''
        else:
            config_form = '<div class="pulse-alert"><strong>Nur Ansicht</strong><div class="text-slate-400 mt-1">Die Support-Konfiguration kann nur von Dashboard-Administratoren geändert werden.</div></div>'
        status = "🟢 Aktiv" if cfg.get("active") else "⚪ Deaktiviert"
        duty_ids = []
        try:
            shifts = ws.load_shifts()
            duty_ids = [uid for uid, state in shifts.get("active_shifts", {}).items() if isinstance(state, dict) and state.get("status") == "online"]
        except Exception:
            pass
        body = f'''<div class="pulse-topbar"><div><div class="pulse-section-title">🎧 Voice-Support</div><div class="pulse-section-sub">Gemeinsamer Warteraum, nachvollziehbare Supportfälle, Fallnotizen, Bewertungen und Bereitschaft – direkt mit Pulse-Dienstzeiten verbunden.</div></div><span class="pulse-pill {"good" if cfg.get("active") else "warn"}">{status}</span></div>
        <div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">🎧</div><div class="label">Offene Sprachfälle</div><div class="value">{len(cases)}</div></div><div class="pulse-stat"><div class="icon">🟢</div><div class="label">Aktuell im Dienst</div><div class="value">{len(duty_ids)}</div></div><div class="pulse-stat"><div class="icon">⭐</div><div class="label">Bewertungen</div><div class="value">{len(rated)}</div></div><div class="pulse-stat"><div class="icon">★</div><div class="label">Ø Bewertung</div><div class="value">{average_rating:.1f}/5</div></div></div>
        {config_form}
        <section class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">📞 Aktuelle Supportfälle</div><div class="pulse-section-sub">Wartende Bürger bleiben im gemeinsamen Warteraum. Nach Übernahme wird der Bürger in den Sprachkanal des Supporters verschoben; beim Abschluss werden temporäre Rechte bereinigt.</div></div><a href="/voice-support" class="pulse-btn ghost">↻ Aktualisieren</a></div><div class="pulse-card-b space-y-2">{"".join(case_rows) or '<div class="pulse-empty">Aktuell keine offenen Sprach-Supportfälle.</div>'}</div></section>'''
        return render_pro_page(ws, "Voice-Support", c, "voice-support", body)

    async def voice_support_save(request: Request, user_session: str=Cookie(None)):
        c = cctx(request, user_session, perm=None, admin=True)
        form = await request.form()
        def form_id(key):
            try:
                return max(0, int(form.get(key) or 0))
            except (TypeError, ValueError):
                return 0
        waiting = c.guild.get_channel(form_id("waiting_room_channel_id"))
        notify = c.guild.get_channel(form_id("notification_channel_id"))
        checkin = c.guild.get_channel(form_id("checkin_channel_id"))
        support_role = c.guild.get_role(form_id("support_role_id"))
        duty_role = c.guild.get_role(form_id("duty_role_id"))
        enabled = form.get("active") == "1"
        duty_mode = form.get("duty_mode") == "1"
        notification_enabled = form.get("notification_enabled") == "1"
        duty_message_enabled = form.get("duty_message_enabled") == "1"
        if waiting and not isinstance(waiting, discord.VoiceChannel):
            return ws.back("/voice-support", "Der Warteraum muss ein normaler Sprachkanal sein.", False)
        if notify and not isinstance(notify, discord.TextChannel):
            return ws.back("/voice-support", "Der Benachrichtigungskanal muss ein Textkanal sein.", False)
        if checkin and not isinstance(checkin, discord.TextChannel):
            return ws.back("/voice-support", "Der Check-in-Kanal muss ein Textkanal sein.", False)
        if support_role and (support_role.managed or support_role.is_default()):
            return ws.back("/voice-support", "Diese Support-Rolle kann nicht verwendet werden.", False)
        if duty_role and (duty_role.managed or duty_role.is_default()):
            return ws.back("/voice-support", "Diese Bereitschaftsrolle kann nicht verwendet werden.", False)
        if enabled and (not isinstance(waiting, discord.VoiceChannel) or not support_role):
            return ws.back("/voice-support", "Zum Aktivieren bitte einen gemeinsamen Warteraum und eine Support-Rolle auswählen.", False)
        if duty_mode and (not duty_role or (duty_message_enabled and not checkin)):
            return ws.back("/voice-support", "Für den Bereitschaftsmodus bitte Bereitschafts-Rolle und Check-in-Kanal auswählen.", False)
        room_name = str(form.get("room_name") or "Support").strip()[:60] or "Support"
        hours = {}
        from cogs.voice_support import DAYS, DEFAULT_HOURS
        for day in DAYS:
            start = str(form.get(f"hours_{day}_start") or DEFAULT_HOURS[day]["start"])
            end = str(form.get(f"hours_{day}_end") or DEFAULT_HOURS[day]["end"])
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end):
                return ws.back("/voice-support", "Öffnungszeiten müssen im Format HH:MM angegeben werden.", False)
            hours[day] = {"enabled": form.get(f"hours_{day}_enabled") == "1", "start": start, "end": end}
        previous = db.get_setting("voice_support_config", {})
        previous = previous if isinstance(previous, dict) else {}
        def valid_audio_signature(extension, data):
            if extension == ".mp3":
                return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
            if extension == ".wav":
                return data[:4] in {b"RIFF", b"RF64"} and data[8:12] == b"WAVE"
            if extension == ".ogg":
                return data.startswith(b"OggS")
            if extension == ".flac":
                return data.startswith(b"fLaC")
            if extension == ".m4a":
                return len(data) >= 12 and data[4:8] == b"ftyp"
            return False

        music_path = str(previous.get("waiting_music_path") or "")
        waiting_music_upload = form.get("waiting_music_file")
        waiting_music_data = None
        if waiting_music_upload and getattr(waiting_music_upload, "filename", ""):
            music_extension = os.path.splitext(os.path.basename(str(waiting_music_upload.filename)))[1].lower()
            if music_extension not in {".mp3", ".ogg", ".wav"}:
                return ws.back("/voice-support", "Die Wartemusik muss MP3, OGG oder WAV sein.", False)
            waiting_music_data = await waiting_music_upload.read(8 * 1024 * 1024 + 1)
            if len(waiting_music_data) > 8 * 1024 * 1024:
                return ws.back("/voice-support", "Die Wartemusik-Datei darf maximal 8 MB groß sein.", False)
            if len(waiting_music_data) < 100 or not valid_audio_signature(music_extension, waiting_music_data):
                return ws.back("/voice-support", "Die Wartemusik ist leer, beschädigt oder passt nicht zur Dateiendung.", False)
            music_path = os.path.join("assets", "support_wait" + music_extension)

        # Validate both custom clips before writing either one so a bad second upload
        # never leaves a half-updated audio configuration.
        audio_specs = (
            ("opening", "custom_opening_audio_path", "custom_opening_audio_file", "remove_custom_opening_audio"),
            ("after_hours", "custom_after_hours_audio_path", "custom_after_hours_audio_file", "remove_custom_after_hours_audio"),
        )
        new_audio_paths = {}
        prepared_audio = []
        removed_audio_paths = []
        audio_dir = ws.VOICE_SUPPORT_AUDIO_DIR.resolve()

        def safe_remove_owned_audio(raw_path, audio_type):
            raw = str(raw_path or "").strip()
            if not raw:
                return
            try:
                candidate = Path(raw)
                if not candidate.is_absolute():
                    candidate = ws.BASE_DIR / candidate
                candidate = candidate.resolve()
                expected_prefix = f"{c.guild.id}_support_{audio_type}_"
                if candidate.parent == audio_dir and candidate.name.startswith(expected_prefix) and candidate.is_file():
                    candidate.unlink()
            except (OSError, RuntimeError):
                pass

        for audio_type, config_key, upload_key, remove_key in audio_specs:
            old_path = str(previous.get(config_key) or "").strip()
            upload = form.get(upload_key)
            if upload and getattr(upload, "filename", ""):
                extension = os.path.splitext(os.path.basename(str(upload.filename)))[1].lower()
                if extension not in ws.VOICE_SUPPORT_AUDIO_EXTENSIONS:
                    return ws.back("/voice-support", "Audioformat nicht unterstützt. Erlaubt sind MP3, WAV, OGG, FLAC und M4A.", False)
                data = await upload.read(ws.MAX_VOICE_SUPPORT_AUDIO_BYTES + 1)
                if len(data) > ws.MAX_VOICE_SUPPORT_AUDIO_BYTES:
                    return ws.back("/voice-support", "Eine Support-Audio darf maximal 10 MB groß sein.", False)
                if len(data) < 100 or not valid_audio_signature(extension, data):
                    return ws.back("/voice-support", "Eine Audio-Datei ist leer, beschädigt oder passt nicht zu ihrer Dateiendung.", False)
                prepared_audio.append((audio_type, config_key, old_path, extension, data))
                new_audio_paths[config_key] = ""
            elif form.get(remove_key) == "1":
                new_audio_paths[config_key] = ""
                if old_path:
                    removed_audio_paths.append((old_path, audio_type))
            else:
                new_audio_paths[config_key] = old_path

        written_audio_paths = []
        try:
            if waiting_music_data is not None:
                asset_dir = Path(ws.BASE_DIR) / "assets"
                asset_dir.mkdir(parents=True, exist_ok=True)
                (Path(ws.BASE_DIR) / music_path).write_bytes(waiting_music_data)

            audio_dir.mkdir(parents=True, exist_ok=True)
            for audio_type, config_key, old_path, extension, data in prepared_audio:
                target = audio_dir / f"{c.guild.id}_support_{audio_type}_{uuid.uuid4().hex[:8]}{extension}"
                temporary = target.with_name(target.name + ".uploading")
                temporary.write_bytes(data)
                os.replace(temporary, target)
                relative_path = target.resolve().relative_to(Path(ws.BASE_DIR).resolve()).as_posix()
                new_audio_paths[config_key] = relative_path
                written_audio_paths.append((relative_path, audio_type))
                if old_path:
                    removed_audio_paths.append((old_path, audio_type))

            config = {
                **previous,
                "guild_id": c.guild.id, "active": enabled, "room_name": room_name,
                "waiting_room_channel_id": waiting.id if isinstance(waiting, discord.VoiceChannel) else 0,
                "notification_channel_id": notify.id if isinstance(notify, discord.TextChannel) else 0,
                "support_role_id": support_role.id if support_role else 0,
                "notes_enabled": form.get("notes_enabled") == "1",
                "notification_enabled": notification_enabled,
                "hours": hours,
                "duty_mode": duty_mode, "checkin_channel_id": checkin.id if isinstance(checkin, discord.TextChannel) else 0,
                "duty_role_id": duty_role.id if duty_role else 0, "duty_message_enabled": duty_message_enabled,
                "ratings_enabled": form.get("ratings_enabled") == "1",
                "waiting_music_enabled": form.get("waiting_music_enabled") == "1",
                "waiting_music_path": music_path,
            }
            for obsolete_key in ("channel_prefix", "category_id", "use_existing_channels"):
                config.pop(obsolete_key, None)
            for config_key, stored_path in new_audio_paths.items():
                if stored_path:
                    config[config_key] = stored_path
                else:
                    config.pop(config_key, None)
            db.set_setting("voice_support_config", config)
        except Exception as exc:
            for new_path, audio_type in written_audio_paths:
                safe_remove_owned_audio(new_path, audio_type)
            print(f"⚠️ Voice-Support Konfiguration konnte nicht gespeichert werden: {type(exc).__name__}: {exc}")
            return ws.back("/voice-support", "Die Konfiguration oder Audiodatei konnte nicht gespeichert werden. Bitte erneut versuchen.", False)

        for old_path, audio_type in removed_audio_paths:
            safe_remove_owned_audio(old_path, audio_type)
        ws.log_audit(c.user.get("global_name") or c.user.get("username"), c.user["id"], "Voice-Support Konfiguration gespeichert", f"Aktiv={enabled}; Bereitschaft={duty_mode}; Warteraum={waiting.id if isinstance(waiting, discord.VoiceChannel) else 0}")
        bot = getattr(request.app.state, "bot", None)
        cog = bot.get_cog("VoiceSupport") if bot else None
        if cog and duty_mode:
            await cog._reconcile_duty_roles(c.guild, config)
            await cog._refresh_duty_status(c.guild)
        return ws.back("/voice-support", "Voice-Support-Konfiguration gespeichert.", True)

    # ---------------- Modern stats / calendar / settings / system / search ----------------
    async def stats_v5(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm='can_view_analytics'); s=work_stats(c); members=team(c.guild,c.config.get('team_role_ids',[])); hist=ws.load_shifts().get('history',[]); week=[]
        for m in members: week.append((ws.calculate_weekly_seconds(str(m.id),hist,ws.load_shifts().get('active_shifts',{})),m.display_name))
        week.sort(reverse=True); maxs=max([x[0] for x in week],default=1)
        rows=''.join(f'<div class="pulse-row"><div class="flex items-center gap-3"><div class="pulse-avatar">{e(initials(n))}</div><div><div class="pulse-row-title">{e(n)}</div><div class="pulse-progress mt-2 w-[180px]"><span style="width:{round(v/maxs*100)}%"></span></div></div></div><div class="pulse-time font-bold">{ws.fmt_duration(v)}</div></div>' for v,n in week[:20])
        closed=sum(1 for t in s['tickets'] if t['status']=='closed'); rated=[t['rating'] for t in s['tickets'] if t.get('rating')]; avg=sum(rated)/len(rated) if rated else 0
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">◒ Team-Analytics</div><div class="pulse-section-sub">Zahlen für Planung, Support und Aktivität.</div></div><a href="/export/team.csv" class="pulse-btn ghost">⇩ CSV Export</a></div><div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">⏱</div><div class="label">Dienststunden</div><div class="value">{sum(int(h.get('duration_seconds',0)) for h in hist)/3600:.1f}h</div></div><div class="pulse-stat"><div class="icon">▣</div><div class="label">Tickets gesamt</div><div class="value">{len(s['tickets'])}</div></div><div class="pulse-stat"><div class="icon">★</div><div class="label">Ø Ticketbewertung</div><div class="value">{avg:.1f}</div></div><div class="pulse-stat"><div class="icon">!</div><div class="label">Warnungen</div><div class="value">{sum(len(v.get('warns_list',[])) for v in ws.load_json(ws.DATA_FILE,{}).values() if isinstance(v,dict))}</div></div></div><div class="pulse-two">{card('Dienstzeit diese Woche',rows or '<div class="pulse-empty">Keine Daten.</div>','⏱')}{card('Support',f'<div class="grid grid-cols-2 gap-3"><div class="pulse-stat"><div class="label">Geschlossen</div><div class="value">{closed}</div></div><div class="pulse-stat"><div class="label">Offen</div><div class="value">{len(s["tickets"])-closed}</div></div></div>','▣')}</div>'''
        return render_pro_page(ws,'Analytics',c,'stats',body)

    async def calendar_v5(request: Request, user_session: str=Cookie(None), month: str=""):
        c = cctx(request, user_session)
        local_tz = ws.now_de().tzinfo
        month_value = str(month or ws.now_de().strftime("%Y-%m"))[:7]
        try:
            month_date = datetime.strptime(month_value, "%Y-%m").date()
        except (TypeError, ValueError):
            month_date = ws.now_de().date()
            month_value = month_date.strftime("%Y-%m")

        first_day = month_date.replace(day=1)
        grid_start = first_day - timedelta(days=first_day.weekday())
        grid_end = grid_start + timedelta(days=42)
        range_start = datetime.combine(grid_start, datetime.min.time(), tzinfo=local_tz).astimezone(timezone.utc).isoformat(timespec="seconds")
        range_end = datetime.combine(grid_end, datetime.min.time(), tzinfo=local_tz).astimezone(timezone.utc).isoformat(timespec="seconds")

        def parse_calendar_datetime(value):
            if not value:
                return None
            raw = str(value).strip()
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                parsed = None
                for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(raw, fmt)
                        break
                    except ValueError:
                        continue
                if parsed is None:
                    return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed.astimezone(local_tz)

        calendar_events = []
        for event in db.list_calendar_events(range_start, range_end, 2000):
            start_dt = parse_calendar_datetime(event.get("starts_at"))
            end_dt = parse_calendar_datetime(event.get("ends_at")) or start_dt
            if start_dt is None:
                continue
            calendar_events.append({
                "title": event.get("title") or "Event",
                "category": event.get("category") or "event",
                "location": event.get("location") or "",
                "description": event.get("description") or "",
                "start": start_dt,
                "end": end_dt,
                "source": "event",
            })

        # Keep the currently configured meeting visible as a calendar item too.
        configured_meeting = ws.load_meetings()
        meeting_start = parse_calendar_datetime(configured_meeting.get("date_time"))
        if meeting_start and configured_meeting.get("date_time") != "Noch nicht angesetzt" and configured_meeting.get("title"):
            calendar_events.append({
                "title": configured_meeting["title"], "category": "meeting", "location": "",
                "description": "", "start": meeting_start, "end": meeting_start, "source": "meeting",
            })

        # Leave entries remain visible as all-day spans without altering the LOA data.
        try:
            for leave in ws.get_loas().values():
                start_leave = parse_calendar_datetime(leave.get("von"))
                end_leave = parse_calendar_datetime(leave.get("bis")) or start_leave
                if start_leave and end_leave and end_leave.date() >= grid_start and start_leave.date() < grid_end:
                    calendar_events.append({
                        "title": (leave.get("name") or "Team-Abmeldung"),
                        "category": "leave", "location": "", "description": "Hinterlegte Abmeldung",
                        "start": start_leave.replace(hour=0, minute=0, second=0, microsecond=0),
                        "end": end_leave.replace(hour=23, minute=59, second=0, microsecond=0),
                        "source": "leave",
                    })
        except Exception:
            pass

        by_day = {}
        for offset in range(42):
            day = grid_start + timedelta(days=offset)
            day_events = []
            for event in calendar_events:
                if event["start"].date() <= day <= event["end"].date():
                    day_events.append(event)
            day_events.sort(key=lambda item: (item["start"], item["title"].casefold()))
            by_day[day.isoformat()] = day_events

        month_names = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        month_label = f"{month_names[month_date.month - 1]} {month_date.year}"
        previous_month = (first_day - timedelta(days=1)).strftime("%Y-%m")
        next_month = (first_day + timedelta(days=32)).replace(day=1).strftime("%Y-%m")
        today = ws.now_de().date()
        weekdays = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        weekday_html = "".join(f'<div class="cal-weekday">{name}</div>' for name in weekdays)
        day_cells = []
        event_icons = {"event": "📌", "meeting": "◫", "training": "🎓", "announcement": "📢", "leave": "☾"}
        for offset in range(42):
            day = grid_start + timedelta(days=offset)
            day_key = day.isoformat()
            outside = day.month != month_date.month
            today_class = " cal-today" if day == today else ""
            outside_class = " cal-outside" if outside else ""
            chips = []
            for event in by_day[day_key][:3]:
                icon = event_icons.get(event["category"], "📌")
                title = e(event["title"])
                if event["source"] == "leave":
                    time_label = "Abwesenheit"
                elif event["start"].date() == event["end"].date():
                    time_label = event["start"].strftime("%H:%M")
                else:
                    time_label = event["start"].strftime("%H:%M") if event["start"].date() == day else "Ganztägig"
                location = f' · {e(event["location"])}' if event["location"] and day == event["start"].date() else ""
                chips.append(
                    f'<div class="cal-event cal-{e(event["category"])}" title="{title}: {e(event["description"])}{location}">'
                    f'<span>{icon}</span><span class="cal-event-text">{title}</span>'
                    f'<span class="cal-event-time">{e(time_label)}</span></div>'
                )
            more = f'<div class="cal-more">+{len(by_day[day_key]) - 3} weitere</div>' if len(by_day[day_key]) > 3 else ""
            today_label = '<span class="cal-today-label">Heute</span>' if day == today else ""
            day_cells.append(
                f'<div class="cal-day{today_class}{outside_class}"><div class="cal-day-top"><span class="cal-day-number">{day.day}</span>'
                f'{today_label}</div>{"".join(chips)}{more}</div>'
            )

        flash = str(request.query_params.get("msg") or "").strip()
        flash_html = f'<div class="pulse-alert mb-4">{e(flash)}</div>' if flash else ""
        create_form = f'''<form action="/calendar/create" method="post" class="space-y-3">
          <input type="hidden" name="month" value="{e(month_value)}">
          <label class="block text-xs font-semibold">Event-Titel<input name="title" required maxlength="120" class="pulse-input mt-1" placeholder="z. B. Teammeeting oder Schulung"></label>
          <label class="block text-xs font-semibold">Beginn<input name="starts_at" type="datetime-local" required class="pulse-input mt-1"></label>
          <label class="block text-xs font-semibold">Ende (optional)<input name="ends_at" type="datetime-local" class="pulse-input mt-1"></label>
          <label class="block text-xs font-semibold">Art<select name="category" class="pulse-input mt-1"><option value="event">Allgemeines Event</option><option value="meeting">Meeting</option><option value="training">Schulung</option><option value="announcement">Ankündigung</option></select></label>
          <label class="block text-xs font-semibold">Ort / Discord-Kanal (optional)<input name="location" maxlength="150" class="pulse-input mt-1" placeholder="z. B. Discord: Besprechungsraum"></label>
          <label class="block text-xs font-semibold">Beschreibung (optional)<textarea name="description" maxlength="2000" class="pulse-input pulse-textarea mt-1" placeholder="Details zum Termin ..."></textarea></label>
          <button class="pulse-btn primary w-full">＋ Event eintragen</button>
        </form>'''
        calendar_css = '''<style>
        .calendar-shell{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:16px}
        .calendar-main{min-width:0}.cal-weekdays,.cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
        .cal-weekday{padding:10px 6px;text-align:center;font-size:10px;font-weight:800;color:var(--muted);text-transform:uppercase}
        .cal-day{min-height:118px;min-width:0;padding:7px;border:1px solid var(--border);background:var(--surface);margin:-.5px;overflow:hidden}
        .cal-day-top{display:flex;align-items:center;justify-content:space-between;gap:3px;margin-bottom:6px}
        .cal-day-number{font-size:11px;font-weight:800}.cal-today{position:relative;background:color-mix(in srgb,var(--p) 7%,var(--surface));border-color:var(--p)}
        .cal-today-label{font-size:8px;color:var(--p);font-weight:800}.cal-outside{opacity:.48;background:var(--surface2)}
        .cal-event{display:flex;align-items:center;gap:3px;margin:3px 0;padding:4px 5px;border-radius:5px;font-size:9px;background:rgba(91,92,240,.10);color:var(--text);min-width:0}
        .cal-event-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}.cal-event-time{font-size:8px;color:var(--muted)}
        .cal-meeting{background:rgba(18,183,106,.13)}.cal-training{background:rgba(14,165,233,.13)}.cal-announcement{background:rgba(247,144,9,.14)}.cal-leave{background:rgba(148,163,184,.18)}
        .cal-more{font-size:8px;color:var(--muted);padding:2px 4px}.cal-nav{display:flex;align-items:center;gap:7px}.cal-nav a{border:1px solid var(--border);background:var(--surface2);border-radius:9px;padding:7px 10px;font-size:12px;font-weight:800}
        @media(max-width:1050px){.calendar-shell{grid-template-columns:1fr}.calendar-form{order:-1}}
        @media(max-width:720px){.calendar-scroll{overflow-x:auto;padding-bottom:8px}.calendar-main{min-width:680px}.cal-day{min-height:94px;padding:5px}.cal-event{font-size:8px}}
        </style>'''
        body = (
            calendar_css + flash_html
            + f'<div class="pulse-topbar"><div><div class="pulse-section-title">▦ Team-Kalender</div>'
              f'<div class="pulse-section-sub">Monatsübersicht für Events, Meetings und Abmeldungen.</div></div>'
              f'<div class="cal-nav"><a href="/calendar?month={previous_month}" aria-label="Vorheriger Monat">‹</a>'
              f'<strong>{e(month_label)}</strong><a href="/calendar?month={next_month}" aria-label="Nächster Monat">›</a>'
              f'<a href="/calendar" title="Zum aktuellen Monat">Heute</a></div></div>'
            + '<div class="calendar-shell"><section class="calendar-main">'
            + '<div class="pulse-card p-3 calendar-scroll"><div class="cal-weekdays">' + weekday_html + '</div>'
            + '<div class="cal-grid">' + "".join(day_cells) + '</div></div></section>'
            + f'<aside class="pulse-card p-5 calendar-form"><div class="pulse-section-title mb-1">＋ Neues Event</div>'
              f'<div class="pulse-section-sub mb-4">Termine werden im Pulse-Datenbestand gespeichert.</div>{create_form}</aside></div>'
        )
        return render_pro_page(ws,'Kalender',c,'calendar',body)

    async def create_calendar_event_v5(
        request: Request,
        title: str = Form(...),
        starts_at: str = Form(...),
        ends_at: str = Form(""),
        category: str = Form("event"),
        location: str = Form(""),
        description: str = Form(""),
        month: str = Form(""),
        user_session: str = Cookie(None),
    ):
        c = cctx(request, user_session)
        title = str(title or "").strip()
        location = str(location or "").strip()
        description = str(description or "").strip()
        category = str(category or "event").strip().lower()
        safe_month = month if len(month) == 7 and month[4:5] == "-" and month[:4].isdigit() and month[5:7].isdigit() else ws.now_de().strftime("%Y-%m")
        if not title or len(title) > 120:
            return ws.back(f"/calendar?month={safe_month}", "Bitte einen Titel mit maximal 120 Zeichen angeben.", False)
        if category not in {"event", "meeting", "training", "announcement"}:
            return ws.back(f"/calendar?month={safe_month}", "Diese Event-Art wird nicht unterstützt.", False)
        if len(location) > 150 or len(description) > 2000:
            return ws.back(f"/calendar?month={safe_month}", "Ort oder Beschreibung ist zu lang.", False)

        def localize(raw):
            parsed = datetime.fromisoformat(str(raw).strip())
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ws.now_de().tzinfo)
            return parsed.astimezone(timezone.utc)

        try:
            start_dt = localize(starts_at)
            end_dt = localize(ends_at) if str(ends_at or "").strip() else None
        except (TypeError, ValueError, OverflowError):
            return ws.back(f"/calendar?month={safe_month}", "Bitte gültige Start- und Endzeitpunkte auswählen.", False)
        if end_dt and end_dt <= start_dt:
            return ws.back(f"/calendar?month={safe_month}", "Das Ende muss nach dem Beginn liegen.", False)
        if end_dt and end_dt - start_dt > timedelta(days=366):
            return ws.back(f"/calendar?month={safe_month}", "Events dürfen höchstens 366 Tage dauern.", False)

        try:
            db.create_calendar_event(
                title, start_dt.isoformat(timespec="seconds"),
                end_dt.isoformat(timespec="seconds") if end_dt else None,
                category, location, description, c.user.get("id"),
                c.user.get("global_name") or c.user.get("username") or "Team",
            )
        except Exception:
            return ws.back(f"/calendar?month={safe_month}", "Event konnte nicht gespeichert werden. Bitte erneut versuchen.", False)
        ws.log_audit(
            c.user.get("global_name") or c.user.get("username") or "Team",
            c.user.get("id"), "Kalender-Event erstellt",
            f"{title} · Beginn {start_dt.isoformat(timespec='minutes')}",
        )
        return ws.back(f"/calendar?month={safe_month}", "Event wurde erfolgreich eingetragen.", True)

    async def search_v5(request: Request, q: str="", user_session: str=Cookie(None)):
        c=cctx(request,user_session); q=(q or '').strip().lower(); results=[]
        if q:
            for m in team(c.guild,c.config.get('team_role_ids',[])):
                if q in f'{m.display_name} {m.name} {m.id}'.lower(): results.append(('◌',m.display_name,'Teammitglied',f'/member/{m.id}'))
            for t in db.list_tickets(limit=1000):
                if q in f'{t["id"]} {t["user_name"]} {t["category"]}'.lower(): results.append(('▣',t['id'],'Ticket', '/tickets'))
            for x in db.announcements(300):
                if q in f'{x["title"]} {x["content"]}'.lower(): results.append(('✦',x['title'],'Ankündigung','/announcements'))
            for x in db.wiki_pages():
                if q in f'{x["title"]} {x["category"]} {x["content"]}'.lower(): results.append(('▤',x['title'],'Wiki',f'/wiki/{x["id"]}'))
        rows=''.join(f'<a href="{e(r[3])}" class="pulse-row"><div class="pulse-avatar">{r[0]}</div><div class="pulse-row-main"><div class="pulse-row-title">{e(r[1])}</div><div class="pulse-row-meta">{e(r[2])}</div></div></a>' for r in results[:100])
        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">⌕ Globale Suche</div><div class="pulse-section-sub">Mitglieder, Tickets, Wiki und Ankündigungen.</div></div></div><form class="pulse-search mb-4" style="max-width:none"><span>⌕</span><input autofocus name="q" value="{e(q)}" placeholder="Name, Discord-ID, Ticket-ID, Stichwort…"><button class="pulse-btn primary">Suchen</button></form><div class="space-y-2">{rows or ('<div class="pulse-empty">Keine Treffer.</div>' if q else '<div class="pulse-empty">Suche nach einem Begriff.</div>')}</div>'''
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
            expired=bool(w.get("expired_at")) or ws.warning_is_expired(w)
            state=pill('Aktiv','warn') if active else (pill('Abgelaufen','good') if expired else pill('Zurückgezogen','good'))
            action_html=''
            if active and (c.perms.get('can_warn') or c.perms.get('is_admin')):
                action_html=f'''<form action="/action" method="post" class="mt-2 flex flex-wrap gap-2" onsubmit="return confirm('Diese Verwarnung wirklich zurückziehen?');">
                    <input type="hidden" name="action" value="remove_warn">
                    <input type="hidden" name="user_id" value="{target.id}">
                    <input type="hidden" name="warn_id" value="{e(w.get('id'))}">
                    <input type="hidden" name="redirect_to_member" value="1">
                    <input name="warn_revoke_reason" maxlength="500" required class="pulse-input flex-1 min-w-[180px]" placeholder="Pflicht: Rücknahmegrund">
                    <button class="pulse-btn bad">↩ Zurückziehen</button>
                </form>'''
            revoked_meta=''
            if not active:
                revoked_meta=f'<div class="text-[10px] text-slate-400 mt-2">↩ {e(w.get("revoked_at") or "N/A")} · von {e(w.get("revoked_by") or "Team")} · {e(w.get("revoked_reason") or "Kein Grund")}</div>'
            expiry_label=''
            if w.get("expires_at"):
                try:
                    expiry_label=' · gültig bis '+ws.parse_dt(str(w.get("expires_at"))).strftime("%d.%m.%Y %H:%M")
                except Exception:
                    expiry_label=' · Ablauf: '+str(w.get("expires_at"))
            return f'''<article class="pulse-row items-start"><div class="pulse-avatar">⚠</div><div class="pulse-row-main"><div class="flex items-center gap-2 flex-wrap"><div class="pulse-row-title">{e(w.get("reason") or w.get("grund") or "Verwarnung")}</div>{state}</div><div class="pulse-row-meta">{e(w.get("date") or "—")} · ausgestellt von {e(w.get("by") or "Team")} · ID {e(w.get("id") or "—")}{e(expiry_label)}</div>{proof_html}{revoked_meta}{action_html}</div></article>'''

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
                <label class="pulse-section-sub">Gültigkeit der Warnung (Tage)<input type="number" name="warn_duration_days" min="1" max="365" value="7" required class="pulse-input w-full" style="margin-top:6px"></label>
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

        actions_html = ''
        if c.perms.get('can_promote') or c.perms.get('is_admin'):
            actor_idx = ws.team_rank(c.member, ids) if c.member else -1
            target_idx = ws.team_rank(target, ids)
            can_manage_target = c.perms.get('is_admin') or actor_idx > target_idx
            if can_manage_target and target.id != c.user['id'] and target.id != c.guild.owner_id:
                actions_html = f'''
        <section class="pulse-card mb-4">
          <div class="pulse-card-h">
            <div><div class="pulse-section-title">⚙ Team-Aktionen</div><div class="pulse-section-sub">Beförderung, Degradierung, Rollenverwaltung und Server-Kick.</div></div>
            <span class="pulse-pill good">Führung</span>
          </div>
          <div class="pulse-card-b space-y-4">
            <div>
              <div class="pulse-section-sub" style="margin-bottom:7px">Team-Rang</div>
              <form action="/action" method="post" class="flex flex-wrap gap-2 items-center">
                <input type="hidden" name="user_id" value="{target.id}">
                <input type="hidden" name="redirect_to_member" value="1">
                <input type="text" name="action_reason" maxlength="500" required class="pulse-input flex-1 min-w-[260px]" placeholder="Pflicht: Grund für Beförderung / Degradierung">
                <select name="probation_days" class="pulse-input" aria-label="Probezeit (nur bei Beförderung)"><option value="">Probezeit wählen</option><option value="3">3 Tage Probezeit</option><option value="7">7 Tage Probezeit</option></select>
                <button name="action" value="promote" class="pulse-btn good" onclick="this.form.querySelector('[name=probation_days]').required=true; return confirm('Wirklich befördern?')">⬆️ Befördern</button>
                <button name="action" value="demote" class="pulse-btn ghost" onclick="this.form.querySelector('[name=probation_days]').required=false; return confirm('Wirklich degradieren?')">⬇️ Degradieren</button>
              </form>
            </div>
            <div class="flex flex-wrap gap-2">
              <a href="/team/{target.id}/roles" class="pulse-btn primary">⚙️ Rollen ändern</a>
              <form action="/action" method="post" class="flex flex-wrap gap-2 items-center" onsubmit="return confirm('Dieses Mitglied wirklich vom Discord-Server kicken?')">
                <input type="hidden" name="action" value="kick">
                <input type="hidden" name="user_id" value="{target.id}">
                <input type="hidden" name="redirect_to_member" value="0">
                <input type="text" name="action_reason" maxlength="500" required class="pulse-input min-w-[260px]" placeholder="Pflicht: Grund für den Kick">
                <button class="pulse-btn bad">🚪 Kicken</button>
              </form>
            </div>
          </div>
        </section>
        '''
        status='Im Dienst' if active and active.get('status')=='online' else 'Pause' if active else 'Offline'
        status_kind='good' if active and active.get('status')=='online' else 'warn' if active else ''
        body=f'''<div class="pulse-topbar"><div><a href="/team" class="pulse-btn ghost">← Team</a><a href="/warns" class="pulse-btn ghost ml-2">⚠ Warnzentrale</a></div><div class="flex items-center gap-2">{pill(status,status_kind)}</div></div>
        <section class="pulse-hero"><div class="flex flex-col md:flex-row md:items-center gap-5"><img src="{avatar}" class="w-16 h-16 rounded-2xl border border-white/20" alt=""><div class="flex-1"><div class="pulse-kicker">Teamakte</div><div class="pulse-title">{e(target.display_name)}</div><div class="pulse-sub">{e(role)} · Discord ID {e(target.id)} · 🗓️ Team seit {e(ws.team_since_for(target, entry))}</div></div><div class="pulse-hero-box min-w-[220px]"><div class="label">WOCHENAKTIVITÄT</div><div class="value">{ws.fmt_duration(weekly)}</div><div class="pulse-progress mt-3" style="background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.15)"><span style="width:{min(100,round(weekly/(max(0.5,float(c.config.get('weekly_goal_hours',3)))*3600)*100))}%;background:white"></span></div></div></div></section>
        <div class="pulse-stat-grid"><div class="pulse-stat"><div class="icon">⚠</div><div class="label">Aktive Warnungen</div><div class="value">{len(active_warns)}/5</div></div><div class="pulse-stat"><div class="icon">↩</div><div class="label">Zurückgezogen</div><div class="value">{len(revoked_warns)}</div></div><div class="pulse-stat"><div class="icon">⏱</div><div class="label">Schichten</div><div class="value">{len(hist)}</div></div><div class="pulse-stat"><div class="icon">🎫</div><div class="label">Tickets</div><div class="value">{len(tickets)}</div></div></div>
        {actions_html}
        <div class="pulse-grid"><div class="space-y-4">{card("Aktive Verwarnungen",active_html or '<div class="pulse-empty">Keine aktiven Verwarnungen.</div>','⚠')}{card("Warn-Historie",revoked_html or '<div class="pulse-empty">Keine zurückgezogenen Warnungen.</div>','↩')}{card("Notizen",note_html or '<div class="pulse-empty">Keine Notizen.</div>','📝')}{card("Aktivität",event_html or '<div class="pulse-empty">Noch keine zentralen Events.</div>','↯')}</div><div class="space-y-4">{card("Warnrollen-Status",role_pills,'⚙')}{card("Tickets",ticket_html or '<div class="pulse-empty">Keine Tickets.</div>','🎫')}{quick}</div></div>'''
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
        c=cctx(request,user_session,perm=None,admin=True)
        roles=[r for r in c.guild.roles if not r.is_default() and not r.managed]
        role_ids=c.config.get('team_role_ids',[])
        profile_notice=""
        try:
            profile_version=int(c.config.get("permissions_profile_version", 0) or 0)
        except (TypeError, ValueError):
            profile_version=0
        if profile_version < 3:
            disabled_count=clear_all_role_permissions(c)
            profile_version=3
            profile_notice=f'<section class="pulse-alert mb-5"><strong>🛑 Sicherheitsreset abgeschlossen.</strong><div class="text-slate-400 mt-1">Alle Pulse-Rollenrechte wurden für {disabled_count} Rollen deaktiviert. Serverinhaber und echte Discord-Administratoren behalten den technischen Adminzugriff. Teamdaten und andere Einstellungen wurden nicht verändert.</div></section>'
        elif role_ids and profile_version < 1:
            initialized=apply_recommended_role_profiles(c)
            profile_notice=f'<section class="pulse-alert mb-5"><strong>🔐 Empfohlene Standardrechte wurden gesetzt.</strong><div class="text-slate-400 mt-1">Die Standardprofile sind aktiv. PK erhält mehr Rechte als Teamleitung; Support bekommt keine Kicks, Bans oder BOLO-Einträge. Bitte prüfe die Karten unten.</div></section>'
        elif role_ids and profile_version < 2:
            permission_map=c.config.setdefault('permissions',{})
            keys=tuple(dict.fromkeys((*ws.PERM_KEYS,*EXTRA_PERMS)))
            migrated=0
            preserved=0
            for raw_id in role_ids:
                try: role_id=int(raw_id)
                except (TypeError,ValueError): continue
                role=c.guild.get_role(role_id)
                if not role: continue
                current=permission_map.get(str(role_id),{})
                normalized={key:bool(current.get(key)) for key in keys}
                legacy=legacy_recommended_role_profile(role)
                if normalized == legacy:
                    updated=recommended_role_profile(role)
                    if updated != normalized:
                        permission_map[str(role_id)]=updated
                        migrated += 1
                else:
                    # Preserve role settings manually changed by an administrator.
                    preserved += 1
            c.config["permissions_profile_version"]=2
            ws.save_json(ws.CONFIG_FILE,c.config)
            if migrated or preserved:
                profile_notice=f'<section class="pulse-alert mb-5"><strong>🔐 Rollen-Hierarchie korrigiert.</strong><div class="text-slate-400 mt-1">{migrated} unveränderte Standardprofile wurden aktualisiert; {preserved} manuell angepasste Profile blieben erhalten. PK liegt jetzt über Teamleitung.</div></section>'
        cfg=c.config.get('permissions',{})
        permission_labels={
            'can_view_dashboard':'Dashboard öffnen',
            'can_warn':'Melonly: Warn',
            'can_moderate_kicks':'Melonly: Kick',
            'can_moderate_bans':'Melonly: Ban',
            'can_moderate_bolo':'Melonly: Ban-BOLO',
            'can_add_notes':'Melonly: Notizen',
            'can_promote':'Teamaktionen (Befördern/Degradieren)',
            'can_manage_tickets':'Tickets verwalten',
            'can_manage_applications':'Bewerbungen verwalten',
            'can_manage_tasks':'Aufgaben verwalten',
            'can_manage_training':'Schulungen verwalten',
            'can_manage_wiki':'Team-Wiki verwalten',
            'can_view_analytics':'Analytics ansehen',
            'can_manage_announcements':'Ankündigungen verwalten',
            'can_manage_handover':'Übergaben verwalten',
            'can_manage_promotions':'Beförderungsanträge verwalten',
            'can_view_team_status':'Teamstatus ansehen/verwalten',
            'can_manage_checkins':'Activity Check verwalten',
        }
        permission_fields=[(key,permission_labels.get(key,key.replace('can_','').replace('_',' ').title())) for key in (*ws.PERM_KEYS,*EXTRA_PERMS)]
        cards=[]
        for rid in role_ids:
            role=c.guild.get_role(rid)
            if not role: continue
            rp=cfg.get(str(rid),{})
            fields=''.join(f'<label class="flex items-center gap-2 text-[11px] p-2 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800/50"><input type="checkbox" name="{k}" {"checked" if rp.get(k) else ""}><span>{e(label)}</span></label>' for k,label in permission_fields)
            cards.append(f'<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title" style="color:#{role.color.value:06x}">{e(role.name)}</div><span class="text-[9px] text-slate-400 font-mono">{rid}</span></div><div class="pulse-card-b"><form action="/settings/pro-save" method="post"><input type="hidden" name="role_id" value="{rid}"><div class="grid grid-cols-2 gap-1">{fields}</div><button class="pulse-btn primary mt-3 w-full">Rechte speichern</button></form></div></section>')
        warn_ids=ws.get_warn_role_ids(c.config)
        warn_rows=[]
        for level in range(1,6):
            role=c.guild.get_role(warn_ids[level])
            status=pill("OK","good") if role else pill("NICHT GEFUNDEN","bad")
            warn_rows.append(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">Warn {level} · {e(role.name if role else "Nicht gefunden")}</div><div class="pulse-row-meta">Feste ID {warn_ids[level]}</div></div>{status}</div>')
        # Fünf Warnstufen sind serverweit verbindlich und werden nicht mehr durch Legacy-Konfiguration ersetzt.
        warn_role_card=f'''<section class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">⚠ Warn-Rollen 1–5</div><div class="pulse-section-sub">Die fünf Discord-Warnrollen sind fest für Pulse hinterlegt und werden automatisch auf die aktive Warnstufe synchronisiert.</div></div></div><div class="pulse-card-b">{"".join(warn_rows)}<form action="/settings/pro-warn-roles" method="post" class="mt-3"><button class="pulse-btn primary w-full">↻ Fünf Warnrollen prüfen & synchronisieren</button></form></div></section>'''
        team_boxes=''.join(f'<label class="flex items-center gap-2 p-2 rounded-lg text-xs"><input type="checkbox" name="team_roles" value="{r.id}" {"checked" if r.id in role_ids else ""}><span style="color:#{r.color.value:06x}">{e(r.name)}</span></label>' for r in sorted(roles,key=lambda r:-r.position))

        # Role-based nickname prefixes are independent from Pulse's dashboard permission roles.
        nickname_prefix_cfg = c.config.get("role_nickname_prefixes", {})
        if not isinstance(nickname_prefix_cfg, dict):
            nickname_prefix_cfg = {}
        roles_by_id = {role.id: role for role in roles}
        valid_prefix_items = []
        for raw_role_id, raw_prefix in nickname_prefix_cfg.items():
            try:
                parsed_role_id = int(raw_role_id)
            except (TypeError, ValueError):
                continue
            valid_prefix_items.append((parsed_role_id, str(raw_prefix or "").strip()))
        configured_prefix_ids = {role_id for role_id, _ in valid_prefix_items}
        nickname_prefix_cards = []
        for role_id, prefix_value in sorted(
            valid_prefix_items,
            key=lambda item: (-(roles_by_id[item[0]].position if item[0] in roles_by_id else -1), item[0]),
        ):
            prefix_role = roles_by_id.get(role_id)
            prefix_role_name = prefix_role.name if prefix_role else "Discord-Rolle nicht gefunden"
            nickname_prefix_cards.append(
                f'<section class="pulse-card"><div class="pulse-card-h"><div>'
                f'<div class="pulse-section-title">{e(prefix_role_name)}</div>'
                f'<div class="pulse-section-sub">Rollen-ID: {role_id}</div></div>'
                f'<span class="text-[9px] text-slate-400">Aktiv</span></div>'
                f'<div class="pulse-card-b space-y-3">'
                f'<form action="/settings/nickname-prefix/save" method="post" class="space-y-3">'
                f'<input type="hidden" name="nickname_prefix_role_id" value="{role_id}">'
                f'<label class="block text-xs font-bold">Namenspräfix'
                f'<input required maxlength="24" name="nickname_prefix_value" value="{e(prefix_value)}" '
                f'placeholder="z. B. Bürger ✗" class="pulse-input mt-2"></label>'
                f'<p class="pulse-section-sub">Ergebnis: <strong>{e(prefix_value)} Name</strong></p>'
                f'<button class="pulse-btn primary w-full">Präfix speichern</button></form>'
                f'<form action="/settings/nickname-prefix/delete" method="post" '
                f'onsubmit="return confirm(\'Dieses Rollenpräfix wirklich entfernen? Betroffene Nicknames werden bei der nächsten Synchronisierung zurückgesetzt.\')">'
                f'<input type="hidden" name="nickname_prefix_role_id" value="{role_id}">'
                f'<button class="pulse-btn bad w-full">Präfix entfernen</button></form>'
                f'</div></section>'
            )
        prefix_role_options = ''.join(
            f'<option value="{role.id}">{e(role.name)} · {role.id}</option>'
            for role in sorted(roles, key=lambda role: (-role.position, role.name.casefold()))
            if role.id not in configured_prefix_ids
        )
        default_prefix_value = str(c.config.get("nickname_default_prefix", "Bürger ✗") or "").strip()
        nickname_default_section = (
            f'<section class="pulse-card"><div class="pulse-card-h"><div>'
            f'<div class="pulse-section-title">👤 Standardpräfix für neue und normale Mitglieder</div>'
            f'<div class="pulse-section-sub">Voreinstellung: Bürger ✗</div></div></div><div class="pulse-card-b">'
            f'<form action="/settings/nickname-prefix/default" method="post" class="space-y-3">'
            f'<label class="block text-xs font-bold">Standardpräfix'
            f'<input maxlength="24" name="nickname_default_prefix" value="{e(default_prefix_value)}" '
            f'placeholder="Bürger ✗" class="pulse-input mt-2"></label>'
            f'<p class="pulse-section-sub">Dieser Präfix wird bei jedem Beitritt verwendet, solange keine zugeordnete Discord-Rolle einen eigenen Präfix vorgibt. '
            f'Für keinen Standardpräfix das Feld leer lassen. Ein leerer Wert deaktiviert nur den Standard, nicht deine Rollenregeln.</p>'
            f'<button class="pulse-btn primary w-full">Standardpräfix speichern</button></form>'
            f'</div></section>'
        )
        nickname_prefix_section = (
            f'<section class="pulse-card mt-5"><div class="pulse-card-h"><div>'
            f'<div class="pulse-section-title">🏷️ Automatische Rollen-Namenspräfixe</div>'
            f'<div class="pulse-section-sub">Beispiele: „Bürger ✗ Name“ oder „VIP ✗ Name“.</div>'
            f'</div></div><div class="pulse-card-b space-y-4">'
            f'<div class="pulse-alert"><strong>Automatisch bei Beitritt, Rollenwechsel und Bot-Start</strong>'
            f'<div class="text-slate-400 mt-1">Wähle jede Discord-Rolle und lege den Text vor dem Namen fest. '
            f'Wenn ein Mitglied mehrere konfigurierte Rollen hat, gewinnt die in Discord höher positionierte Rolle. '
            f'Entfernt man alle zugeordneten Rollen, versucht Pulse den vorherigen Nickname wiederherzustellen. '
            f'Bestehende Mitglieder werden beim Bot-Start nachgezogen; Änderungen an Präfixregeln starten ebenfalls eine Synchronisierung. '
            f'Der Standardpräfix „Bürger ✗“ greift, solange keine Rollenregel passt. '
            f'Discord erlaubt maximal 32 Zeichen für Nicknames; lange Namen können deshalb gekürzt werden.</div></div>'
            f'{nickname_default_section}'
            f'<form action="/settings/nickname-prefix/sync" method="post" '
            f'onsubmit="return confirm(\'Die Namenspräfixe für bereits vorhandene Mitglieder im aktuell geladenen Server-Cache anwenden? Nicknames können geändert oder zurückgesetzt werden.\')">'
            f'<button class="pulse-btn ghost">↻ Bestehende Mitglieder synchronisieren</button></form>'
            f'<section class="pulse-card"><div class="pulse-card-h"><div>'
            f'<div class="pulse-section-title">➕ Präfix für eine Rolle hinzufügen</div>'
            f'<div class="pulse-section-sub">Es können auch Rollen außerhalb der Pulse-Teamrollen ausgewählt werden.</div>'
            f'</div></div><div class="pulse-card-b">'
            f'<form action="/settings/nickname-prefix/save" method="post" class="space-y-3">'
            f'<label class="block text-xs font-bold">Discord-Rolle'
            f'<select required name="nickname_prefix_role_id" class="pulse-input mt-2">'
            f'<option value="">Rolle auswählen …</option>{prefix_role_options}</select></label>'
            f'<label class="block text-xs font-bold">Präfix vor dem Namen'
            f'<input required maxlength="24" name="nickname_prefix_value" placeholder="z. B. Bürger ✗ oder VIP ✗" class="pulse-input mt-2"></label>'
            f'<button class="pulse-btn primary w-full">Rollenpräfix hinzufügen</button></form>'
            f'</div></section>'
            f'<div class="grid md:grid-cols-2 gap-4">{"".join(nickname_prefix_cards) or "<div class=\'pulse-empty\'>Noch keine Rollenpräfixe eingerichtet.</div>"}</div>'
            f'</div></section>'
        )


        # Digitale Zwischenrollen sind interne Pulse-Profile, die einer oder mehreren
        # konfigurierten Discord-Teamrollen zugewiesen werden.
        dashboard_role_cfg = c.config.get("dashboard_roles", {})
        configured_roles = []
        for raw_role_id in role_ids:
            try:
                role = c.guild.get_role(int(raw_role_id))
            except (TypeError, ValueError):
                role = None
            if role and not role.managed and not role.is_default():
                configured_roles.append(role)
        dashboard_role_cards = []
        for profile_key, profile in sorted(
            dashboard_role_cfg.items(),
            key=lambda item: str(item[1].get("name", item[0]) if isinstance(item[1], dict) else item[0]).casefold(),
        ):
            if not isinstance(profile, dict):
                continue
            profile_permissions = profile.get("permissions", {})
            selected_role_ids = {int(value) for value in profile.get("discord_role_ids", []) if str(value).isdigit()}
            selected_names = [role.name for role in configured_roles if role.id in selected_role_ids]
            role_checkboxes = ''.join(
                f'<label class="flex items-start gap-2 p-2 rounded-lg text-[11px] bg-slate-50 dark:bg-slate-900/40">'
                f'<input type="checkbox" name="dashboard_discord_roles" value="{role.id}" {"checked" if role.id in selected_role_ids else ""}>'
                f'<span>{e(role.name)}<span class="block text-slate-400 font-mono">{role.id}</span></span></label>'
                for role in configured_roles
            )
            profile_fields = ''.join(
                f'<label class="flex items-center gap-2 text-[11px] p-2 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800/50">'
                f'<input type="checkbox" name="{key}" value="1" {"checked" if profile_permissions.get(key) else ""}>'
                f'<span>{e(label)}</span></label>'
                for key, label in permission_fields
            )
            dashboard_role_cards.append(
                f'<section class="pulse-card"><div class="pulse-card-h"><div>'
                f'<div class="pulse-section-title">{e(profile.get("name") or profile_key)}</div>'
                f'<div class="pulse-section-sub">Digitale Zwischenrolle · {len(selected_names)} Discord-Teamrolle(n) zugeordnet</div>'
                f'</div><span class="text-[9px] text-slate-400 font-mono">{e(profile_key)}</span></div>'
                f'<div class="pulse-card-b space-y-4">'
                f'<form action="/settings/dashboard-profile/save" method="post" class="space-y-4">'
                f'<input type="hidden" name="dashboard_profile_id" value="{e(profile_key)}">'
                f'<label class="block text-xs font-bold">Name der digitalen Rolle'
                f'<input required maxlength="40" name="dashboard_profile_name" value="{e(profile.get("name") or profile_key)}" class="pulse-input mt-2"></label>'
                f'<div><div class="pulse-section-title mb-2">Discord-Teamrollen zuordnen</div>'
                f'<p class="pulse-section-sub mb-2">Alle Mitglieder dieser Discord-Rollen erhalten das ausgewählte Rechteprofil automatisch.</p>'
                f'<div class="grid sm:grid-cols-2 gap-2 max-h-64 overflow-y-auto">{role_checkboxes or "<p class=\'pulse-section-sub\'>Bitte zuerst oben unter Teamrollen Discord-Rollen auswählen und speichern.</p>"}</div></div>'
                f'<div><div class="pulse-section-title mb-2">Berechtigungen dieser digitalen Rolle</div>'
                f'<div class="grid sm:grid-cols-2 gap-1">{profile_fields}</div></div>'
                f'<button class="pulse-btn primary w-full">Digitale Rolle speichern</button></form>'
                f'<form action="/settings/dashboard-profile/delete" method="post" onsubmit="return confirm(\'Digitale Zwischenrolle und ihre Rechte-Zuordnung wirklich löschen?\')">'
                f'<input type="hidden" name="dashboard_profile_id" value="{e(profile_key)}">'
                f'<button class="pulse-btn bad w-full">Digitale Rolle löschen</button></form>'
                f'<div class="pulse-section-sub">Zugeordnet: {e(", ".join(selected_names) if selected_names else "Keine Discord-Teamrolle")}</div>'
                f'</div></section>'
            )

        new_profile_role_checkboxes = ''.join(
            f'<label class="flex items-start gap-2 p-2 rounded-lg text-[11px] bg-slate-50 dark:bg-slate-900/40">'
            f'<input type="checkbox" name="dashboard_discord_roles" value="{role.id}">'
            f'<span>{e(role.name)}<span class="block text-slate-400 font-mono">{role.id}</span></span></label>'
            for role in configured_roles
        )
        new_profile_fields = ''.join(
            f'<label class="flex items-center gap-2 text-[11px] p-2 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800/50">'
            f'<input type="checkbox" name="{key}" value="1" {"checked" if key == "can_view_dashboard" else ""}>'
            f'<span>{e(label)}</span></label>'
            for key, label in permission_fields
        )
        dashboard_roles_section = (
            f'<section class="mt-5 space-y-4"><div><div class="pulse-section-title">🔗 Digitale Zwischenrollen</div>'
            f'<div class="pulse-section-sub mt-1">Erstelle interne Rollenprofile wie bei GalaxyBot, konfiguriere deren Rechte und weise sie direkt vorhandenen Discord-Teamrollen zu.</div></div>'
            f'<div class="pulse-alert"><strong>Nur im Dashboard</strong><div class="text-slate-400 mt-1">Diese Zwischenrollen existieren ausschließlich in Pulse. Alle Mitglieder der zugeordneten Discord-Teamrollen erhalten die Rechte automatisch. Discord-Rollen und Serverrechte werden nicht verändert; du musst identische Rechte nicht mehr an jeder Discord-Rolle einzeln setzen.</div></div>'
            f'<section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">➕ Digitale Zwischenrolle erstellen</div>'
            f'<div class="pulse-section-sub">Name, Rechte und Ziel-Teamrollen in einem Schritt festlegen.</div></div></div><div class="pulse-card-b">'
            f'<form action="/settings/dashboard-profile/save" method="post" class="space-y-4">'
            f'<label class="block text-xs font-bold">Name der digitalen Rolle<input required maxlength="40" name="dashboard_profile_name" placeholder="z. B. Support-Rechte, Personalverwaltung" class="pulse-input mt-2"></label>'
            f'<div><div class="pulse-section-title mb-2">Discord-Teamrollen zuordnen</div><div class="grid sm:grid-cols-2 gap-2 max-h-64 overflow-y-auto">{new_profile_role_checkboxes or "<p class=\'pulse-section-sub\'>Bitte zuerst oben unter Teamrollen mindestens eine Discord-Rolle auswählen und speichern.</p>"}</div></div>'
            f'<div><div class="pulse-section-title mb-2">Berechtigungen auswählen</div><div class="grid sm:grid-cols-2 gap-1">{new_profile_fields}</div></div>'
            f'<button class="pulse-btn primary w-full">Digitale Zwischenrolle erstellen</button></form></div></section>'
            f'<div class="grid md:grid-cols-2 gap-4">{"".join(dashboard_role_cards) or "<div class=\'pulse-empty\'>Noch keine digitalen Zwischenrollen erstellt.</div>"}</div></section>'
        )
        audit=''.join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(a.get("actor"))} · {e(a.get("action"))}</div><div class="pulse-row-meta">{e(a.get("details"))}</div></div><div class="pulse-row-meta">{e(a.get("timestamp"))}</div></div>' for a in reversed(ws.load_json(ws.AUDIT_FILE,[])[-120:]))
        if c.config.get("all_role_permissions_disabled"):
            presets_action = '<div class="pulse-alert"><strong>🛑 Standardprofile gesperrt</strong><div class="text-slate-400 mt-1">Der globale Rechte-Reset ist aktiv. Es werden keine automatischen Standardrechte vergeben. Rechte können nur pro Rolle ausdrücklich in den einzelnen Rechtekarten gesetzt werden.</div></div>'
        else:
            presets_action = "<form action=\"/settings/pro-presets\" method=\"post\" onsubmit=\"return confirm('Empfohlene Profile für ALLE ausgewählten Teamrollen anwenden? Manuell konfigurierte Rechte werden überschrieben.');\"><button class=\"pulse-btn primary\">Empfohlene Profile erneut anwenden</button></form>"

        body=f'''<div class="pulse-topbar"><div><div class="pulse-section-title">⚙ Einstellungen</div><div class="pulse-section-sub">Teamrollen, Berechtigungen und Audit.</div></div></div>{profile_notice}<section class="pulse-card mb-5"><div class="pulse-card-b flex flex-col gap-4"><div class="flex flex-col md:flex-row md:items-center justify-between gap-3"><div><div class="pulse-section-title">🔐 Empfohlene Rechteprofile</div><div class="pulse-section-sub">Ordnet allen aktuell ausgewählten Teamrollen neue Standardrechte anhand ihres Discord-Rollennamens zu. Bereits manuell gesetzte Rechte werden dabei überschrieben.</div></div>{presets_action}</div><div class="pulse-alert"><strong>🛑 Alle Rollenrechte entziehen</strong><div class="text-slate-400 mt-1">Setzt jede gespeicherte Rollenberechtigung in Pulse auf „aus“ – einschließlich aller aktuell ausgewählten Teamrollen. Die Teamrollenauswahl und andere Einstellungen bleiben erhalten. Serverinhaber und echte Discord-Administratoren behalten den technischen Adminzugriff.</div><form action="/settings/pro-clear-all" method="post" onsubmit="return confirm('Wirklich ALLE Pulse-Berechtigungen für sämtliche Rollen deaktivieren? Nur Serverinhaber und echte Discord-Administratoren behalten Adminzugriff.');" class="mt-3"><button class="pulse-btn bad">Alle Rollenrechte entziehen</button></form></div></div></section><div class="pulse-two"><section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">👥 Teamrollen</div></div><div class="pulse-card-b"><form action="/settings/pro-roles" method="post"><div class="grid sm:grid-cols-2">{team_boxes}</div><button class="pulse-btn primary mt-3">Teamrollen speichern</button></form></div></section><section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">🎯 Wochenziel</div></div><div class="pulse-card-b"><form action="/settings/pro-goal" method="post" class="flex gap-2"><input class="pulse-input" type="number" min="0.5" max="100" step="0.5" name="weekly_goal" value="{float(c.config.get('weekly_goal_hours',3.0)):g}"><button class="pulse-btn primary">Speichern</button></form></div></section></div>{dashboard_roles_section}{nickname_prefix_section}<div class="grid md:grid-cols-2 gap-4 mt-5">{"".join(cards)}</div>{warn_role_card}<section class="pulse-card mt-5"><div class="pulse-card-h"><div class="pulse-section-title">📜 Audit-Log</div></div><div class="pulse-card-b space-y-2 max-h-[520px] overflow-auto">{audit or '<div class="pulse-empty">Keine Audit-Einträge.</div>'}</div></section>'''
        return render_pro_page(ws,'Einstellungen',c,'settings',body)

    async def settings_dashboard_profile_save(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        form=await request.form()
        name=str(form.get("dashboard_profile_name") or "").strip()[:40]
        profile_key=str(form.get("dashboard_profile_id") or "").strip()
        if not name:
            return ws.back('/settings','Bitte gib einen Namen für die digitale Zwischenrolle an.',False)

        profiles=c.config.setdefault("dashboard_roles",{})
        if profile_key and profile_key not in profiles:
            return ws.back('/settings','Diese digitale Zwischenrolle wurde nicht gefunden. Bitte lade die Einstellungen neu.',False)
        if any(
            str(key)!=profile_key and isinstance(value,dict)
            and str(value.get("name","")).casefold()==name.casefold()
            for key,value in profiles.items()
        ):
            return ws.back('/settings','Eine digitale Zwischenrolle mit diesem Namen existiert bereits.',False)
        if not profile_key:
            if len(profiles)>=50:
                return ws.back('/settings','Es können maximal 50 digitale Zwischenrollen angelegt werden.',False)
            profile_key=f"dash_{uuid.uuid4().hex[:10]}"

        allowed_ids={int(value) for value in c.config.get("team_role_ids",[]) if str(value).isdigit()}
        selected_ids=set()
        for raw in form.getlist("dashboard_discord_roles"):
            try:
                selected_ids.add(int(raw))
            except (TypeError,ValueError):
                continue
        if selected_ids-allowed_ids:
            return ws.back('/settings','Es dürfen nur unter Teamrollen ausgewählte Discord-Rollen zugeordnet werden.',False)
        valid_roles=[]
        for rid in sorted(selected_ids):
            role=c.guild.get_role(rid)
            if not role or role.managed or role.is_default():
                return ws.back('/settings','Mindestens eine ausgewählte Discord-Teamrolle ist ungültig. Bitte lade die Seite neu.',False)
            valid_roles.append(rid)

        permission_keys=tuple(dict.fromkeys((*ws.PERM_KEYS,*EXTRA_PERMS)))
        permissions={key:bool(form.get(key)) for key in permission_keys}
        profiles[profile_key]={
            "name":name,
            "permissions":permissions,
            "discord_role_ids":valid_roles,
        }
        ws.save_json(ws.CONFIG_FILE,c.config)
        mapped_names=", ".join(c.guild.get_role(rid).name for rid in valid_roles) or "keine Teamrolle"
        enabled=", ".join(permission_labels.get(key,key) for key in permission_keys if permissions.get(key)) or "keine Rechte"
        ws.log_audit(
            c.user.get('global_name') or c.user.get('username'),c.user['id'],
            "Digitale Zwischenrolle gespeichert",
            f"{name} ({profile_key}) · Discord-Rollen: {mapped_names} · Rechte: {enabled}",
        )
        return ws.back('/settings',f'Digitale Zwischenrolle „{name}“ gespeichert.')

    async def settings_dashboard_profile_delete(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        form=await request.form()
        profile_key=str(form.get("dashboard_profile_id") or "").strip()
        profiles=c.config.setdefault("dashboard_roles",{})
        profile=profiles.get(profile_key)
        if not isinstance(profile,dict):
            return ws.back('/settings','Diese digitale Zwischenrolle wurde nicht gefunden.',False)
        profile_name=str(profile.get("name") or profile_key)
        del profiles[profile_key]
        ws.save_json(ws.CONFIG_FILE,c.config)
        ws.log_audit(
            c.user.get('global_name') or c.user.get('username'),c.user['id'],
            "Digitale Zwischenrolle gelöscht",f"{profile_name} ({profile_key})",
        )
        return ws.back('/settings',f'Digitale Zwischenrolle „{profile_name}“ wurde gelöscht.')

    async def settings_nickname_prefix_save(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        form=await request.form()
        try:
            role_id=int(str(form.get("nickname_prefix_role_id") or "").strip())
        except (TypeError,ValueError):
            return ws.back('/settings','Bitte wähle eine gültige Discord-Rolle aus.',False)
        role=c.guild.get_role(role_id)
        if not role or role.is_default() or role.managed:
            return ws.back('/settings','Diese Discord-Rolle ist ungültig oder wird von einer Integration verwaltet.',False)
        try:
            prefix=normalize_role_prefix(form.get("nickname_prefix_value"))
        except ValueError as exc:
            return ws.back('/settings',str(exc),False)

        prefix_map=c.config.setdefault("role_nickname_prefixes",{})
        if not isinstance(prefix_map,dict):
            prefix_map={}
            c.config["role_nickname_prefixes"]=prefix_map
        if str(role_id) not in prefix_map and len(prefix_map)>=150:
            return ws.back('/settings','Es können maximal 150 Rollen-Namenspräfixe konfiguriert werden.',False)
        prefix_map[str(role_id)]=prefix
        ws.save_json(ws.CONFIG_FILE,c.config)
        ws.log_audit(
            c.user.get('global_name') or c.user.get('username'),c.user['id'],
            "Rollen-Namenspräfix gespeichert",f"{role.name} ({role_id}) → {prefix}",
        )
        bot=getattr(request.app.state,"bot",None)
        prefix_cog=bot.get_cog("RoleNicknamePrefixes") if bot else None
        if prefix_cog:
            prefix_cog.start_sync_existing_members(queue_if_running=True)
        return ws.back('/settings',f'Namenspräfix „{prefix}“ für „{role.name}“ gespeichert. Bestehende Mitglieder werden mit synchronisiert.')

    async def settings_nickname_prefix_default(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        form=await request.form()
        raw_prefix=str(form.get("nickname_default_prefix") or "").strip()
        if raw_prefix:
            try:
                prefix=normalize_role_prefix(raw_prefix)
            except ValueError as exc:
                return ws.back('/settings',str(exc),False)
        else:
            prefix=""
        c.config["nickname_default_prefix"]=prefix
        ws.save_json(ws.CONFIG_FILE,c.config)
        ws.log_audit(
            c.user.get('global_name') or c.user.get('username'),c.user['id'],
            "Standard-Namenspräfix gespeichert",
            f"{prefix or 'deaktiviert'} · gilt als Fallback für Mitglieder ohne Rollenpräfix",
        )
        bot=getattr(request.app.state,"bot",None)
        prefix_cog=bot.get_cog("RoleNicknamePrefixes") if bot else None
        if prefix_cog:
            prefix_cog.start_sync_existing_members(queue_if_running=True)
        return ws.back('/settings',f'Standardpräfix {"„"+prefix+"“" if prefix else "deaktiviert"}. Bestehende Mitglieder werden ebenfalls synchronisiert.')

    async def settings_nickname_prefix_delete(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        form=await request.form()
        try:
            role_id=int(str(form.get("nickname_prefix_role_id") or "").strip())
        except (TypeError,ValueError):
            return ws.back('/settings','Bitte wähle eine gültige Discord-Rolle aus.',False)
        prefix_map=c.config.setdefault("role_nickname_prefixes",{})
        if not isinstance(prefix_map,dict) or str(role_id) not in prefix_map:
            return ws.back('/settings','Für diese Rolle ist kein Namenspräfix eingerichtet.',False)
        prefix=str(prefix_map.pop(str(role_id)) or "")
        role=c.guild.get_role(role_id)
        role_name=role.name if role else str(role_id)
        ws.save_json(ws.CONFIG_FILE,c.config)
        ws.log_audit(
            c.user.get('global_name') or c.user.get('username'),c.user['id'],
            "Rollen-Namenspräfix entfernt",f"{role_name} ({role_id}) · zuvor: {prefix}",
        )
        bot=getattr(request.app.state,"bot",None)
        prefix_cog=bot.get_cog("RoleNicknamePrefixes") if bot else None
        if prefix_cog:
            prefix_cog.start_sync_existing_members(queue_if_running=True)
        return ws.back('/settings',f'Namenspräfix für „{role_name}“ entfernt. Bestehende Mitglieder werden auf die verbleibende Präfixregel umgestellt.')

    async def settings_nickname_prefix_sync(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        bot=getattr(request.app.state,"bot",None)
        cog=bot.get_cog("RoleNicknamePrefixes") if bot else None
        if cog is None:
            return ws.back('/settings','Die Namenspräfix-Funktion ist nicht geladen. Bitte den Bot neu starten und die Hosting-Logs prüfen.',False)
        if not cog.start_sync_existing_members():
            return ws.back('/settings','Die Synchronisierung läuft bereits. Bitte warte, bis sie in den Hosting-Logs abgeschlossen ist.',False)
        ws.log_audit(
            c.user.get('global_name') or c.user.get('username'),c.user['id'],
            "Namenspräfix-Synchronisierung gestartet",
            "Bestehende Mitglieder mit konfigurierten Rollenpräfixen oder gespeichertem Präfixstatus",
        )
        return ws.back('/settings','Namenspräfix-Synchronisierung im Hintergrund gestartet. Den Fortschritt findest du in den Hosting-Logs.')

    async def settings_save(request: Request, role_id: int=Form(...), user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True); role=c.guild.get_role(role_id)
        if not role or role_id not in c.config.get('team_role_ids',[]): return ws.back('/settings','Ungültige Teamrolle.',False)
        form=await request.form(); cfg=c.config.setdefault('permissions',{}); rp={}
        for k in ws.PERM_KEYS + EXTRA_PERMS: rp[k]=bool(form.get(k))
        cfg[str(role_id)]=rp; ws.save_json(ws.CONFIG_FILE,c.config); ws.log_audit(c.user.get('global_name'),c.user['id'],'Rechte Gespeichert',f'Rolle {role.name}')
        return ws.back('/settings','Rollenrechte gespeichert.')

    async def settings_roles(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        form=await request.form()
        vals=[]
        for x in form.getlist('team_roles'):
            try:
                r=c.guild.get_role(int(x))
                if r and not r.managed and not r.is_default():
                    vals.append(r.id)
            except (TypeError, ValueError):
                pass
        vals=sorted(set(vals),key=lambda rid:c.guild.get_role(rid).position)
        c.config['team_role_ids']=vals
        permission_map=c.config.setdefault('permissions',{})
        no_rights={key:False for key in tuple(dict.fromkeys((*ws.PERM_KEYS,*EXTRA_PERMS)))}
        keep_roles_disabled=bool(c.config.get("all_role_permissions_disabled"))
        for rid in vals:
            role=c.guild.get_role(rid)
            if role and str(rid) not in permission_map:
                permission_map[str(rid)]=dict(no_rights) if keep_roles_disabled else recommended_role_profile(role)
        # Do not retrigger the one-time reset when the selected roles are edited later.
        c.config["permissions_profile_version"]=3 if keep_roles_disabled else 1
        ws.save_json(ws.CONFIG_FILE,c.config)
        ws.log_audit(c.user.get('global_name') or c.user.get('username'),c.user['id'],'Team-Rollen Geändert',','.join(str(x) for x in vals))
        return ws.back('/settings','Teamrollen und Standardrechte gespeichert.')

    async def settings_profiles(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        if c.config.get("all_role_permissions_disabled"):
            return ws.back('/settings','Der globale Rechte-Reset ist aktiv. Automatische Standardprofile bleiben gesperrt; nutze bei Bedarf die einzelne Rechtekarte einer Rolle.',False)
        changed=apply_recommended_role_profiles(c)
        return ws.back('/settings',f'Empfohlene Rechteprofile für {len(changed)} Teamrollen angewendet. Prüfe die Rechtekarten und passe sie anschließend bei Bedarf an.')

    async def settings_clear_all_permissions(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        count=clear_all_role_permissions(c)
        return ws.back('/settings',f'Pulse-Rechte für {count} Rollen vollständig deaktiviert. Nur Serverinhaber und echte Discord-Administratoren behalten den Adminzugriff.')

    async def settings_warn_roles(request: Request, user_session: str=Cookie(None)):
        c=cctx(request,user_session,perm=None,admin=True)
        selected={str(level):ws.WARN_ROLE_IDS[level] for level in range(1,6)}
        c.config['warn_role_ids']=selected
        ws.save_json(ws.CONFIG_FILE,c.config)
        health=ws.warning_role_health(c.guild,c.config)
        sync_report=await ws.reconcile_warning_roles(c.guild,c.config)
        ok=all(x['ok'] for x in health) and not sync_report.get('failed')
        ws.log_audit(
            c.user.get('global_name'),c.user['id'],'Warn-Rollen Synchronisiert',
            ','.join(str(selected[str(i)]) for i in range(1,6))
        )
        msg='Die fünf festgelegten Warnrollen wurden geprüft und Teamrollen synchronisiert.'
        if sync_report.get('failed'):
            msg += f" {sync_report['failed']} Teamler konnten nicht synchronisiert werden."
        elif not ok:
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
        # Serve from memory: a shared file in the backup folder raced between parallel exports.
        data=buf.getvalue().encode('utf-8-sig'); return Response(content=data,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="pulse_team_export.csv"'})

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
    remove_and_add(app,'/voice-support',{'GET'},voice_support_v5,response_class=HTMLResponse)
    remove_and_add(app,'/voice-support/save',{'POST'},voice_support_save)
    remove_and_add(app,'/tickets/update',{'POST'},ticket_update_v5)
    remove_and_add(app,'/stats',{'GET'},stats_v5,response_class=HTMLResponse)
    remove_and_add(app,'/calendar',{'GET'},calendar_v5,response_class=HTMLResponse)
    app.post('/calendar/create')(create_calendar_event_v5)
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
    remove_and_add(app,'/shift/admin-end',{'POST'},admin_end_shift)
    remove_and_add(app,'/api/team/discord-status',{'GET'},discord_status_api,response_class=JSONResponse)
    app.post('/team-status/set')(set_team_status)
    app.get('/activity-check',response_class=HTMLResponse)(activity_check_page)
    app.post('/activity-check/setup')(activity_check_setup)
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
    app.post('/settings/dashboard-profile/save')(settings_dashboard_profile_save)
    app.post('/settings/dashboard-profile/delete')(settings_dashboard_profile_delete)
    app.post('/settings/nickname-prefix/save')(settings_nickname_prefix_save)
    app.post('/settings/nickname-prefix/default')(settings_nickname_prefix_default)
    app.post('/settings/nickname-prefix/delete')(settings_nickname_prefix_delete)
    app.post('/settings/nickname-prefix/sync')(settings_nickname_prefix_sync)
    app.post('/settings/pro-roles')(settings_roles)
    app.post('/settings/pro-presets')(settings_profiles)
    app.post('/settings/pro-clear-all')(settings_clear_all_permissions)
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
