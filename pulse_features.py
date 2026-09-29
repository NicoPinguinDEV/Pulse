import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import Request, Form, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response

import pulse_db as db


def register(app):
    import webserver as ws
    db.init_db()

    def ctx(request, session, perm=None, admin=False):
        return ws.auth(request, session, perm=perm, admin=admin)

    def esc(v): return ws.esc(v)
    def card(body="", cls=""): return f'<div class="{ws.CARD} p-5 {cls}">{body}</div>'
    def badge(text, kind="ok"):
        c = ws.BADGE_OK if kind == "ok" else ws.BADGE_BAD if kind == "bad" else ws.BADGE_WARN
        return f'<span class="text-[10px] border rounded-full px-2.5 py-1 font-semibold {c}">{esc(text)}</span>'

    def team_members(guild, team_ids):
        return sorted([m for m in guild.members if not m.bot and any(r.id in team_ids for r in m.roles)], key=lambda m: m.display_name.lower())

    def priority_label(p):
        return {"urgent": "🚨 Dringend", "high": "🟠 Hoch", "normal": "🟢 Normal", "low": "⚪ Niedrig"}.get(p, "🟢 Normal")

    def task_status_label(s):
        return {"open":"🟡 Offen", "in_progress":"🔵 In Arbeit", "done":"✅ Erledigt"}.get(s,s)

    @app.get("/pulse-inbox", response_class=HTMLResponse)
    async def inbox_page(request: Request, user_session: str = Cookie(None)):
        c=ctx(request,user_session)
        items=db.notifications(c.user["id"],100)
        tasks=db.list_tasks(assignee_id=c.user["id"],limit=20)
        tickets=db.list_tickets(limit=50)
        team_ids=c.config.get("team_role_ids",[])
        open_tickets=[t for t in tickets if t["status"]!="closed"]
        stale_apps=[a for a in ws.load_json(ws.APPS_FILE,{}).values() if a.get("status")=="pending"]
        loas=ws.get_loas()
        today=ws.now_de().date()
        ending=[l for l in loas.values() if l.get("active") and str(l.get("bis"))==today.isoformat()]
        feed="".join(f'''<div class="p-3 rounded-xl border border-slate-200 dark:border-slate-800 {'bg-indigo-500/5' if not n.get('read_at') else 'bg-slate-50 dark:bg-[#0b0e14]'}"><div class="flex justify-between gap-3"><div class="font-semibold text-xs">{esc(n['title'])}</div><span class="text-[10px] text-slate-400">{esc(n['created_at'][:16].replace('T',' '))}</span></div><p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{esc(n['body'])}</p><a class="text-[10px] text-indigo-500 hover:underline" href="{esc(n.get('url') or '#')}">Öffnen →</a></div>''' for n in items)
        task_html="".join(f'<a href="/tasks" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800"><span class="text-xs font-semibold">{esc(t["title"])}</span>{badge(task_status_label(t["status"]),"ok" if t["status"]=="done" else "warn")}</a>' for t in tasks[:8])
        urgent_html=[]
        if open_tickets: urgent_html.append(f'<a href="/tickets" class="block p-3 rounded-xl bg-rose-500/5 border border-rose-500/20 text-xs">🎫 <b>{len(open_tickets)}</b> offene Tickets</a>')
        if stale_apps: urgent_html.append(f'<a href="/applications" class="block p-3 rounded-xl bg-amber-500/5 border border-amber-500/20 text-xs">📝 <b>{len(stale_apps)}</b> offene Bewerbungen</a>')
        if ending: urgent_html.append(f'<a href="/loa" class="block p-3 rounded-xl bg-amber-500/5 border border-amber-500/20 text-xs">🌴 <b>{len(ending)}</b> LOA enden heute</a>')
        body=f'''<div class="flex flex-wrap items-start justify-between gap-4 mb-6"><div><h1 class="text-2xl font-bold">📥 Pulse Inbox</h1><p class="text-xs text-slate-500 mt-1">Alles Wichtige an einem Ort.</p></div><form action="/pulse-inbox/read" method="post"><button class="text-xs px-3 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">✓ Alle als gelesen</button></form></div><div class="grid grid-cols-1 lg:grid-cols-3 gap-5"><div class="lg:col-span-2 space-y-3">{feed or card('<p class="text-xs text-slate-400">Keine neuen Benachrichtigungen.</p>')}</div><div class="space-y-5">{card('<h2 class="text-sm font-bold mb-3">🚨 Aufmerksamkeit</h2>' + (''.join(urgent_html) or '<p class="text-xs text-slate-400">Nichts Dringendes.</p>'))}{card('<h2 class="text-sm font-bold mb-3">📋 Meine Aufgaben</h2>' + (task_html or '<p class="text-xs text-slate-400">Keine Aufgaben.</p>'))}</div></div>'''
        return ws.render_page("Pulse Inbox",c,"inbox",body)

    @app.post("/pulse-inbox/read")
    async def inbox_read(request: Request, user_session: str = Cookie(None)):
        c=ctx(request,user_session); db.mark_notifications_read(c.user["id"]); return ws.back("/pulse-inbox","Benachrichtigungen als gelesen markiert.")

    @app.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request, q: str = "", user_session: str = Cookie(None)):
        c=ctx(request,user_session); q=(q or "").strip().lower(); results=[]
        if q:
            for m in team_members(c.guild,c.config.get("team_role_ids",[])):
                hay=f"{m.display_name} {m.name} {m.id}".lower()
                if q in hay: results.append(("👤",m.display_name,f"Teammitglied · {m.id}",f"/member/{m.id}"))
            for t in db.list_tickets(limit=300):
                hay=f"{t['id']} {t['user_name']} {t['category']} {t['status']}".lower()
                if q in hay: results.append(("🎫",t['id'],f"Ticket · {t['category']} · {t['status']}","/tickets"))
            for t in db.list_tasks(limit=300):
                if q in f"{t['title']} {t['description']} {t['assignee_name'] or ''}".lower(): results.append(("📋",t['title'],f"Aufgabe · {task_status_label(t['status'])}","/tasks"))
            for w in db.wiki_pages():
                if q in f"{w['title']} {w['category']} {w['content']}".lower(): results.append(("📚",w['title'],f"Wiki · {w['category']}",f"/wiki/{w['id']}"))
            for aid,a in ws.load_json(ws.APPS_FILE,{}).items():
                if q in f"{aid} {a.get('name','')} {a.get('user_id','')} {a.get('text','')}".lower(): results.append(("📝",a.get('name','Bewerbung'),f"Bewerbung · {a.get('status')}","/applications"))
        html="".join(f'<a href="{esc(r[3])}" class="block p-4 {ws.CARD} hover:border-indigo-500"><div class="flex gap-3"><div class="text-xl">{r[0]}</div><div><div class="font-bold text-sm">{esc(r[1])}</div><div class="text-[10px] text-slate-400 mt-1">{esc(r[2])}</div></div></div></a>' for r in results[:100])
        body=f'''<div class="max-w-4xl"><h1 class="text-2xl font-bold mb-2">🔎 Globale Suche</h1><p class="text-xs text-slate-500 mb-5">Mitglieder, Tickets, Aufgaben, Bewerbungen und Wiki durchsuchen.</p><form class="mb-5"><input autofocus name="q" value="{esc(q)}" placeholder="Name, Discord-ID, Ticket-ID, Stichwort…" class="{ws.INPUT}"></form><div class="space-y-2">{html or ('<p class="text-xs text-slate-400">Keine Treffer.</p>' if q else '<p class="text-xs text-slate-400">Suche starten.</p>')}</div></div>'''
        return ws.render_page("Suche",c,"search",body)

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks_page(request: Request, user_session: str = Cookie(None)):
        c=ctx(request,user_session); manager=c.perms.get("can_manage_tasks") or c.perms.get("can_promote") or c.perms.get("is_admin")
        tasks=db.list_tasks(limit=300); members=team_members(c.guild,c.config.get("team_role_ids",[]))
        cols={k:[t for t in tasks if t["status"]==k] for k in ("open","in_progress","done")}
        def task_card(t):
            ass=t.get("assignee_name") or "Niemand"; due=t.get("due_at") or "Ohne Deadline"; can_edit=manager or str(t.get("assignee_id"))==str(c.user['id'])
            btns=''
            if can_edit and t['status']!='done': btns += f'<form action="/tasks/update" method="post" class="inline"><input type="hidden" name="task_id" value="{t["id"]}"><input type="hidden" name="status" value="done"><button class="text-[10px] text-emerald-600 hover:underline">✓ Erledigt</button></form>'
            if can_edit and t['status']=='open': btns += f'<form action="/tasks/update" method="post" class="inline"><input type="hidden" name="task_id" value="{t["id"]}"><input type="hidden" name="status" value="in_progress"><button class="text-[10px] text-indigo-600 hover:underline">▶ Start</button></form>'
            return f'<div class="p-4 rounded-xl bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 shadow-sm"><div class="flex justify-between gap-2"><b class="text-xs">{esc(t["title"])}</b>{badge(priority_label(t["priority"]),"bad" if t["priority"]=="urgent" else "warn" if t["priority"]=="high" else "ok")}</div><p class="text-[11px] text-slate-500 mt-1">{esc(t["description"])}</p><div class="flex justify-between items-center gap-2 mt-3 text-[10px] text-slate-400"><span>👤 {esc(ass)} · ⏰ {esc(str(due)[:16])}</span><span class="flex gap-2">{btns}</span></div></div>'
        col_html=[]
        for key,title in (("open","🟡 Offen"),("in_progress","🔵 In Arbeit"),("done","✅ Erledigt")):
            col_html.append(f'<div class="space-y-3"><h2 class="text-sm font-bold">{title} <span class="text-slate-400">({len(cols[key])})</span></h2>{"".join(task_card(t) for t in cols[key]) or "<p class=\'text-xs text-slate-400\'>Leer.</p>"}</div>')
        form=''
        if manager:
            opts=''.join(f'<option value="{m.id}">{esc(m.display_name)}</option>' for m in members)
            form=card(f'''<h2 class="text-sm font-bold mb-3">➕ Aufgabe erstellen</h2><form action="/tasks/create" method="post" class="grid md:grid-cols-2 gap-3 text-xs"><input name="title" required maxlength="120" placeholder="Aufgabe…" class="{ws.INPUT}"><select name="assignee_id" class="{ws.INPUT}"><option value="">Niemand</option>{opts}</select><textarea name="description" maxlength="600" placeholder="Beschreibung…" class="{ws.INPUT} md:col-span-2 h-20"></textarea><select name="priority" class="{ws.INPUT}"><option value="normal">🟢 Normal</option><option value="high">🟠 Hoch</option><option value="urgent">🚨 Dringend</option><option value="low">⚪ Niedrig</option></select><input type="datetime-local" name="due_at" class="{ws.INPUT}"><button class="{ws.BTN} md:col-span-2 py-2.5">Aufgabe erstellen</button></form>''')
        body=f'<div class="flex items-start justify-between gap-4 mb-5"><div><h1 class="text-2xl font-bold">📋 Aufgaben</h1><p class="text-xs text-slate-500 mt-1">Kanban für die tägliche Teamarbeit.</p></div></div>{form}<div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-5">{"".join(col_html)}</div>'
        return ws.render_page("Aufgaben",c,"tasks",body)

    @app.post("/tasks/create")
    async def task_create(request: Request, title: str=Form(...), description: str=Form(""), assignee_id: str=Form(""), priority: str=Form("normal"), due_at: str=Form(""), user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None); manager=c.perms.get("can_manage_tasks") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not manager: raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        m=c.guild.get_member(int(assignee_id)) if assignee_id.isdigit() else None
        tid=db.create_task(title.strip()[:120],description.strip()[:600],m.id if m else None,m.display_name if m else "",c.user["id"],c.user.get("global_name") or "Team",priority,due_at or None)
        if m: db.notify(m.id,"📋 Neue Aufgabe",f"{c.user.get('global_name')}: {title.strip()[:120]}","task","/tasks")
        ws.log_audit(c.user.get("global_name"),c.user["id"],"Aufgabe Erstellt",f"{title.strip()[:120]} · {tid}")
        return ws.back("/tasks","Aufgabe erstellt.")

    @app.post("/tasks/update")
    async def task_update(request: Request, task_id: str=Form(...), status: str=Form(...), user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None); t=next((x for x in db.list_tasks(limit=500) if x['id']==task_id),None)
        if not t: return ws.back("/tasks","Aufgabe nicht gefunden.",False)
        manager=c.perms.get("can_manage_tasks") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not (manager or str(t.get("assignee_id"))==str(c.user['id'])): raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        if status not in ("open","in_progress","done"): raise HTTPException(400,"Ungültiger Status.")
        db.update_task(task_id,status=status); ws.log_audit(c.user.get("global_name"),c.user["id"],"Aufgabe Aktualisiert",f"{task_id} → {status}")
        return ws.back("/tasks","Aufgabe aktualisiert.")

    @app.get("/tickets", response_class=HTMLResponse)
    async def tickets_page(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session); items=db.list_tickets(limit=300); manager=c.perms.get("can_manage_tickets") or c.perms.get("can_promote") or c.perms.get("is_admin")
        rows="".join(f'''<div class="{ws.CARD} p-4"><div class="flex justify-between gap-3"><div><div class="font-bold text-sm">{esc(t['id'])}</div><div class="text-[10px] text-slate-400">{esc(t['user_name'])} · {esc(t['category'])} · {esc(t['opened_at'][:16].replace('T',' '))}</div></div>{badge(('✅ Geschlossen' if t['status']=='closed' else '🔵 In Arbeit' if t['status']=='in_progress' else '🟢 Offen'),'ok' if t['status']=='closed' else 'warn')}</div><div class="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px] text-slate-500"><div>Priorität<br><b>{esc(priority_label(t['priority']))}</b></div><div>Bearbeiter<br><b>{esc(t.get('claimed_by_name') or 'Niemand')}</b></div><div>Bewertung<br><b>{(str(t.get('rating'))+' ⭐') if t.get('rating') else '—'}</b></div><div>Grund<br><b>{esc(t.get('close_reason') or '—')}</b></div></div>{('<form action="/tickets/update" method="post" class="mt-3 flex gap-2 items-center"><input type="hidden" name="ticket_id" value="'+esc(t['id'])+'"><select name="priority" class="'+ws.INPUT+' py-2 text-[10px] w-auto"><option value="low">⚪ Niedrig</option><option value="normal" '+('selected' if t['priority']=='normal' else '')+'>🟢 Normal</option><option value="high" '+('selected' if t['priority']=='high' else '')+'>🟠 Hoch</option><option value="urgent" '+('selected' if t['priority']=='urgent' else '')+'>🚨 Dringend</option></select><button class="'+ws.BTN+' text-[10px] px-3 py-2">Priorität speichern</button></form>') if manager else ''}<div class="mt-3">{(('<form action="/tickets/rate" method="post" class="flex gap-2 items-center"><input type="hidden" name="ticket_id" value="'+esc(t['id'])+'"><select name="rating" class="'+ws.INPUT+' py-2 text-[10px] w-auto"><option value="5">⭐ 5</option><option value="4">⭐ 4</option><option value="3">⭐ 3</option><option value="2">⭐ 2</option><option value="1">⭐ 1</option></select><input name="comment" maxlength="300" placeholder="Kommentar (optional)" class="'+ws.INPUT+' py-2 text-[10px]"><button class="'+ws.BTN+' text-[10px] px-3 py-2">Bewerten</button></form>') if t['status']=='closed' and str(t['user_id'])==str(c.user['id']) and not t.get('rating') else '')}</div></div>''' for t in items)
        open_n=sum(t['status']!='closed' for t in items); rated=[t['rating'] for t in items if t.get('rating')]; avg=sum(rated)/len(rated) if rated else 0
        body=f'<div class="mb-6"><h1 class="text-2xl font-bold">🎫 Tickets</h1><p class="text-xs text-slate-500 mt-1">Live-Übersicht aus dem Discord-Ticketsystem.</p></div><div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">{ws.stat_card("🎫","Gesamt",len(items),"indigo")}{ws.stat_card("🟢","Offen",open_n,"emerald")}{ws.stat_card("✅","Geschlossen",len(items)-open_n,"slate")}{ws.stat_card("⭐","Ø Bewertung",f"{avg:.1f}","amber")}</div><div class="space-y-3">{rows or card("<p class=\'text-xs text-slate-400\'>Noch keine Tickets.</p>")}</div>'
        return ws.render_page("Tickets",c,"tickets",body)


    @app.get("/ticket/transcript/{filename}")
    async def ticket_transcript(request: Request, filename: str, user_session: str=Cookie(None)):
        c=ctx(request,user_session)
        manager=c.perms.get('can_manage_tickets') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        safe=os.path.basename(filename)
        path=os.path.join('transcripts',safe)
        if not safe.endswith('.txt') or not os.path.isfile(path): raise HTTPException(404,'Transcript nicht gefunden.')
        return __import__('fastapi').responses.FileResponse(path,filename=safe,media_type='text/plain')

    @app.post("/tickets/rate")
    async def ticket_rate(request: Request, ticket_id: str=Form(...), rating: int=Form(...), comment: str=Form(""), user_session: str=Cookie(None)):
        c=ctx(request,user_session); t=db.get_ticket(ticket_id=ticket_id)
        if not t or str(t['user_id'])!=str(c.user['id']): raise HTTPException(403,"Du darfst nur deine eigenen Tickets bewerten.")
        if rating<1 or rating>5: raise HTTPException(400,"Bewertung muss 1 bis 5 sein.")
        db.rate_ticket(ticket_id,rating,comment.strip()[:300]); db.notify(t.get('claimed_by_id') or c.user['id'],"⭐ Ticket bewertet",f"Ticket {ticket_id}: {rating}/5", "success", "/tickets")
        return ws.back("/tickets","Bewertung gespeichert.")

    @app.get("/wiki", response_class=HTMLResponse)
    async def wiki_page(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session); pages=db.wiki_pages(); manager=c.perms.get("can_manage_wiki") or c.perms.get("can_promote") or c.perms.get("is_admin")
        groups={}
        for p in pages: groups.setdefault(p['category'],[]).append(p)
        listing=''.join(f'<div class="{ws.CARD} p-4"><h2 class="text-sm font-bold mb-2">{esc(cat)}</h2>{"".join(f"<a class=\'block text-xs text-indigo-600 dark:text-indigo-400 hover:underline py-1\' href=\'/wiki/{x["id"]}\'>📄 {esc(x["title"])}</a>" for x in arr)}</div>' for cat,arr in groups.items())
        form=''
        if manager:
            form=card(f'''<h2 class="text-sm font-bold mb-3">➕ Wiki-Seite</h2><form action="/wiki/save" method="post" class="space-y-3 text-xs"><input name="title" required maxlength="120" placeholder="Titel…" class="{ws.INPUT}"><input name="category" required maxlength="60" placeholder="Kategorie…" class="{ws.INPUT}"><textarea name="content" required maxlength="10000" placeholder="Inhalt…" class="{ws.INPUT} h-44"></textarea><button class="{ws.BTN} py-2.5 px-4">Seite speichern</button></form>''')
        body=f'<h1 class="text-2xl font-bold mb-2">📚 Team-Wiki</h1><p class="text-xs text-slate-500 mb-5">Zentrale Wissensdatenbank für Regeln, Abläufe und Schulungen.</p><div class="grid grid-cols-1 md:grid-cols-2 gap-4">{listing or card("<p class=\'text-xs text-slate-400\'>Noch keine Seiten angelegt.</p>")}</div>{f"<div class=\'mt-5\'>{form}</div>" if form else ""}'
        return ws.render_page("Team-Wiki",c,"wiki",body)

    @app.get("/wiki/{page_id}", response_class=HTMLResponse)
    async def wiki_detail(request: Request, page_id: str, user_session: str=Cookie(None)):
        c=ctx(request,user_session); p=db.get_wiki(page_id)
        if not p: raise HTTPException(404,"Wiki-Seite nicht gefunden.")
        manager=c.perms.get("can_manage_wiki") or c.perms.get("can_promote") or c.perms.get("is_admin")
        content=esc(p['content']).replace('\n','<br>')
        edit=f'<a href="/wiki" class="text-xs text-indigo-600">← Zurück</a>'
        body=f'<div class="max-w-4xl"><div class="flex justify-between gap-3 mb-5"><div><span class="text-[10px] text-indigo-500 font-semibold">{esc(p["category"])}</span><h1 class="text-2xl font-bold">{esc(p["title"])}</h1></div>{edit}</div><div class="{ws.CARD} p-6 text-sm leading-7 whitespace-normal">{content}</div><p class="text-[10px] text-slate-400 mt-3">Zuletzt geändert: {esc(p["updated_at"][:16].replace('T',' '))} · {esc(p["author_name"])}</p></div>'
        return ws.render_page(p['title'],c,'wiki',body)

    @app.post("/wiki/save")
    async def wiki_save(request: Request, title: str=Form(...), category: str=Form(...), content: str=Form(...), user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None); manager=c.perms.get("can_manage_wiki") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not manager: raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        pid=db.save_wiki(title.strip()[:120],category.strip()[:60],content.strip()[:10000],c.user['id'],c.user.get('global_name') or 'Team')
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Wiki-Seite Erstellt',f'{title.strip()[:120]} · {pid}')
        return ws.back(f'/wiki/{pid}','Wiki-Seite gespeichert.')

    @app.get("/training", response_class=HTMLResponse)
    async def training_page(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session); trainings=db.training_list(); manager=c.perms.get("can_manage_training") or c.perms.get("can_promote") or c.perms.get("is_admin")
        cards=''.join((lambda t: f'<a href="/training/{t["id"]}" class="{ws.CARD} p-5 hover:border-indigo-500"><div class="flex justify-between gap-3"><h2 class="font-bold text-sm">🎓 {esc(t["title"])}</h2>{badge(str(t["passing_score"])+"% Bestehen","ok")}</div><p class="text-xs text-slate-500 mt-2">{esc(t["description"])}</p></a>')(t) for t in trainings)
        form=''
        if manager:
            placeholder='[{"question":"Was bedeutet IC?","options":["In Character","Intern","Information Check","Keine Ahnung"],"answer_index":0}]'
            form=card(f'''<h2 class="text-sm font-bold mb-3">➕ Schulung erstellen</h2><form action="/training/create" method="post" class="space-y-3 text-xs"><input name="title" required maxlength="120" placeholder="Titel…" class="{ws.INPUT}"><textarea name="description" maxlength="500" placeholder="Beschreibung…" class="{ws.INPUT} h-20"></textarea><div class="grid grid-cols-2 gap-3"><input type="number" name="passing_score" min="1" max="100" value="80" class="{ws.INPUT}"><input type="number" name="time_limit" min="1" max="180" value="20" class="{ws.INPUT}"></div><textarea name="questions_json" required placeholder='{esc(placeholder)}' class="{ws.INPUT} h-48 font-mono text-[10px]"></textarea><button class="{ws.BTN} py-2.5 px-4">Schulung veröffentlichen</button></form>''')
        body=f'<h1 class="text-2xl font-bold mb-2">🎓 Schulungen</h1><p class="text-xs text-slate-500 mb-5">Prüfungen und Grundkompetenztests für das Team.</p><div class="grid md:grid-cols-2 gap-4">{cards or card("<p class=\'text-xs text-slate-400\'>Noch keine Schulungen.</p>")}</div>{f"<div class=\'mt-5 max-w-3xl\'>{form}</div>" if form else ""}'
        return ws.render_page("Schulungen",c,"training",body)

    @app.post("/training/create")
    async def training_create(request: Request, title: str=Form(...), description: str=Form(""), passing_score: int=Form(80), time_limit: int=Form(20), questions_json: str=Form(...), user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None); manager=c.perms.get("can_manage_training") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not manager: raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        try: qs=json.loads(questions_json)
        except Exception: return ws.back('/training','Ungültiges Fragen-JSON.',False)
        if not isinstance(qs,list) or not qs or any(not q.get('question') or not isinstance(q.get('options'),list) or len(q['options'])<2 or not (0<=int(q.get('answer_index',-1))<len(q['options'])) for q in qs):
            return ws.back('/training','Fragenformat ungültig.',False)
        tid=db.create_training(title.strip()[:120],description.strip()[:500],max(1,min(100,passing_score)),max(1,min(180,time_limit)),c.user['id'],c.user.get('global_name') or 'Team',qs)
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Schulung Erstellt',f'{title.strip()} · {tid}')
        return ws.back('/training','Schulung erstellt.')

    @app.get("/training/{training_id}", response_class=HTMLResponse)
    async def training_detail(request: Request, training_id: str, user_session: str=Cookie(None)):
        c=ctx(request,user_session); t=db.get_training(training_id)
        if not t: raise HTTPException(404,'Schulung nicht gefunden.')
        qs=''.join(f'''<div class="{ws.CARD} p-5"><div class="font-semibold text-xs mb-3">{i+1}. {esc(q["question"])}</div>{"".join(f'<label class="flex gap-2 items-center p-2 rounded-lg bg-slate-50 dark:bg-[#0b0e14] mb-1 text-xs"><input type="radio" name="q_{q["id"]}" value="{j}" required><span>{esc(opt)}</span></label>' for j,opt in enumerate(q['options']))}</div>''' for i,q in enumerate(t['questions']))
        body=f'<div class="max-w-3xl"><h1 class="text-2xl font-bold">🎓 {esc(t["title"])}</h1><p class="text-xs text-slate-500 mt-1 mb-5">Bestehensgrenze: {t["passing_score"]}% · Zeitlimit: {t["time_limit_minutes"]} Minuten</p><form action="/training/{training_id}/submit" method="post" class="space-y-3">{qs}<button class="{ws.BTN} px-5 py-3">Test abgeben</button></form></div>'
        return ws.render_page(t['title'],c,'training',body)

    @app.post("/training/{training_id}/submit")
    async def training_submit(request: Request, training_id: str, user_session: str=Cookie(None)):
        c=ctx(request,user_session); t=db.get_training(training_id)
        if not t: raise HTTPException(404,'Schulung nicht gefunden.')
        form=await request.form(); correct=0
        for q in t['questions']:
            try:
                if int(form.get(f'q_{q["id"]}'))==int(q['answer_index']): correct += 1
            except Exception: pass
        score=round(correct/max(1,len(t['questions']))*100); passed=score>=t['passing_score']
        db.save_attempt(training_id,c.user['id'],c.user.get('global_name') or 'Team',score,passed)
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Schulung Abgelegt',f'{t["title"]}: {score}% · {"bestanden" if passed else "nicht bestanden"}')
        icon='✅' if passed else '❌'; textmsg=f'{icon} Ergebnis: {score}% – {"Bestanden" if passed else "Nicht bestanden"}.'
        return HTMLResponse(ws.simple_page(icon,'Testergebnis',textmsg,'<a href="/training" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zurück zu Schulungen</a>'))

    @app.get("/achievements", response_class=HTMLResponse)
    async def achievements_page(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session); shifts=ws.load_shifts(); all_shifts=[h for h in shifts['history'] if str(h.get('mod_id'))==str(c.user['id'])]; total_user_seconds=sum(int(h.get('duration_seconds',0)) for h in all_shifts)
        active_user=shifts['active_shifts'].get(str(c.user['id']))
        if active_user: total_user_seconds += ws.shift_elapsed(active_user)
        ticket_count=sum(1 for t in db.list_tickets(limit=5000) if str(t.get('claimed_by_id'))==str(c.user['id']) and t.get('status')=='closed')
        meeting_count=0
        for mh in db.meeting_history(500):
            try:
                attendees=json.loads(mh.get('attendees_json') or '{}')
                if attendees.get(str(c.user['id']),{}).get('status')=='accepted': meeting_count += 1
            except Exception: pass
        flag_correct=0
        try:
            with __import__('sqlite3').connect('flaggenquiz.db') as qdb:
                row=qdb.execute('SELECT points FROM quiz_scores WHERE guild_id=? AND user_id=?',(c.guild.id,int(c.user['id']))).fetchone(); flag_correct=int(row[0]) if row else 0
        except Exception: pass
        stats={'shifts':len(all_shifts),'hours':int(total_user_seconds/3600),'tickets_closed':ticket_count,'meetings':meeting_count,'flag_correct':flag_correct}
        ach=[]
        with db.connect() as cx:
            achievements=[dict(r) for r in cx.execute('SELECT * FROM achievements ORDER BY target').fetchall()]
        for a in achievements:
            prog=stats.get(a['metric'],0); pct=min(100,round(prog/max(1,a['target'])*100)); icon='🏆' if pct>=100 else a['icon']
            if pct>=100:
                with db.connect() as cx:
                    existing=cx.execute('SELECT unlocked_at FROM achievement_progress WHERE achievement_id=? AND user_id=?',(a['id'],str(c.user['id']))).fetchone()
                    if not existing or not existing[0]:
                        cx.execute('INSERT OR REPLACE INTO achievement_progress(achievement_id,user_id,progress,unlocked_at) VALUES(?,?,?,?)',(a['id'],str(c.user['id']),prog,datetime.utcnow().isoformat(timespec='seconds')))
                        db.notify(c.user['id'],f"{icon} Achievement freigeschaltet",a['name'],'success','/achievements')
            ach.append(f'<div class="{ws.CARD} p-5"><div class="flex justify-between gap-3"><div><div class="text-2xl">{icon}</div><h2 class="font-bold text-sm mt-2">{esc(a["name"])}</h2><p class="text-xs text-slate-500 mt-1">{esc(a["description"])}</p></div><span class="text-xs font-bold">{prog}/{a["target"]}</span></div><div class="h-2 bg-slate-100 dark:bg-slate-800 rounded-full mt-4 overflow-hidden"><div class="h-full bg-indigo-500" style="width:{pct}%"></div></div></div>')
        body=f'<h1 class="text-2xl font-bold mb-2">🏅 Achievements</h1><p class="text-xs text-slate-500 mb-5">Kleine Meilensteine für Team-Aktivität und Support.</p><div class="grid md:grid-cols-2 xl:grid-cols-3 gap-4">{"".join(ach)}</div>'
        return ws.render_page('Achievements',c,'achievements',body)

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_page(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm='can_view_analytics')
        team=team_members(c.guild,c.config.get('team_role_ids',[])); shifts=ws.load_shifts(); hist=shifts['history'];
        total_secs=sum(int(h.get('duration_seconds',0)) for h in hist); total_tickets=len(db.list_tickets(limit=5000)); closed_tickets=sum(1 for t in db.list_tickets(limit=5000) if t['status']=='closed'); apps=ws.load_json(ws.APPS_FILE,{}); pending_apps=sum(1 for a in apps.values() if a.get('status')=='pending'); warns=sum(len(v.get('warns_list',[])) for v in ws.load_json(ws.DATA_FILE,{}).values() if isinstance(v,dict));
        rows=[]
        for m in team:
            sec=ws.calculate_weekly_seconds(str(m.id),hist,shifts['active_shifts']); rows.append((sec,m.display_name))
        rows.sort(reverse=True)
        leaderboard=''.join(f'<div class="flex justify-between p-3 rounded-xl bg-slate-50 dark:bg-[#0b0e14] text-xs"><span>#{i} {esc(n)}</span><b>{ws.fmt_duration(s)}</b></div>' for i,(s,n) in enumerate(rows[:10],1))
        body=f'<h1 class="text-2xl font-bold mb-2">📊 Team-Statistiken</h1><p class="text-xs text-slate-500 mb-5">Aktuelle Kennzahlen aus Pulse.</p><div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">{ws.stat_card("⏱️","Dienststunden",f"{total_secs/3600:.1f}h","indigo")}{ws.stat_card("🎫","Tickets",total_tickets,"blue")}{ws.stat_card("📝","Offene Bewerbungen",pending_apps,"amber")}{ws.stat_card("⚠️","Warnungen",warns,"rose")}</div><div class="grid lg:grid-cols-2 gap-5"><div>{card('<h2 class="text-sm font-bold mb-3">🏆 Dienstzeit dieser Woche</h2>'+ (leaderboard or '<p class="text-xs text-slate-400">Keine Daten.</p>'))}</div>{card(f'<h2 class="text-sm font-bold mb-3">🎫 Support</h2><div class="text-3xl font-bold">{closed_tickets}</div><p class="text-xs text-slate-500 mt-1">geschlossene Tickets</p>')}</div>'
        return ws.render_page('Statistiken',c,'stats',body)


    @app.get("/meetings-history", response_class=HTMLResponse)
    async def meetings_history_page(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session); items=db.meeting_history(100)
        rows=''.join(f'<div class="{ws.CARD} p-5"><div class="flex justify-between gap-3"><div><h2 class="font-bold text-sm">🎙️ {esc(m["title"])}</h2><div class="text-[10px] text-indigo-500 mt-1">{esc(m["date_time"])}</div></div><span class="text-[10px] text-slate-400">archiviert {esc(m["created_at"][:16].replace("T"," "))}</span></div><p class="text-xs text-slate-500 mt-3">{esc(m["description"])}</p><div class="mt-3 text-xs"><b>Teilnehmer/Antworten:</b> {esc(str(len(json.loads(m["attendees_json"] or "{}"))))}</div></div>' for m in items)
        body=f'<div class="flex items-center justify-between gap-3 mb-5"><div><h1 class="text-2xl font-bold">🎙️ Meeting-Historie</h1><p class="text-xs text-slate-500 mt-1">Automatisch archivierte Besprechungen und ihre Antwortzahlen.</p></div><a href="/meetings" class="text-xs text-indigo-600">← Aktuelles Meeting</a></div><div class="space-y-3">{rows or card("<p class=\'text-xs text-slate-400\'>Noch keine archivierten Meetings.</p>")}</div>'
        return ws.render_page('Meeting-Historie',c,'meetings',body)

    @app.post("/tickets/update")
    async def ticket_update(request: Request, ticket_id: str=Form(...), priority: str=Form(...), user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None); manager=c.perms.get('can_manage_tickets') or c.perms.get('can_promote') or c.perms.get('is_admin')
        if not manager: raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        if priority not in ('low','normal','high','urgent'): raise HTTPException(400,'Ungültige Priorität.')
        with db.connect() as cx: cx.execute('UPDATE tickets SET priority=? WHERE id=?',(priority,ticket_id))
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Ticket Priorität geändert',f'{ticket_id} → {priority}')
        return ws.back('/tickets','Ticket aktualisiert.')

    @app.get("/calendar", response_class=HTMLResponse)
    async def calendar_page(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session); meetings=ws.load_meetings(); loas=ws.get_loas(); items=[]
        if meetings.get('date_time') and meetings.get('date_time')!='Noch nicht angesetzt': items.append(('🎙️',meetings['date_time'],meetings['title']))
        for l in loas.values(): items.append(('🌴',f"{l['von']} → {l['bis']}",l['name']))
        items_html=''.join(f'<div class="{ws.CARD} p-4"><span class="text-xl">{x[0]}</span><div class="font-semibold text-sm mt-2">{esc(x[2])}</div><div class="text-xs text-indigo-500 mt-1">{esc(x[1])}</div></div>' for x in items)
        body=f'<h1 class="text-2xl font-bold mb-2">🗓️ Team-Kalender</h1><p class="text-xs text-slate-500 mb-5">Meetings und Abwesenheiten auf einen Blick.</p><div class="grid md:grid-cols-2 xl:grid-cols-3 gap-4">{items_html or card("<p class=\'text-xs text-slate-400\'>Noch keine Termine.</p>")}</div>'
        return ws.render_page('Team-Kalender',c,'calendar',body)

    @app.get("/system", response_class=HTMLResponse)
    async def system_page(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None,admin=True)
        checks=[]
        checks.append(('🟢','Bot','Online' if getattr(c.guild,'id',0)==c.guild.id else 'Unbekannt'))
        for name,ok,detail in [('Datenbank',os.path.exists(db.DB_PATH),'pulse.db'),('Teamdaten',os.path.exists(ws.DATA_FILE),ws.DATA_FILE),('Backups',os.path.isdir(ws.BACKUP_DIR),ws.BACKUP_DIR),('OAuth',bool(ws.CLIENT_ID and ws.CLIENT_SECRET), 'Discord OAuth'),('Warnrollen',all(c.guild.get_role(r) for r in ws.WARN_ROLE_IDS.values()), 'WARN_ROLE_*')]: checks.append(('🟢' if ok else '🔴',name,'OK' if ok else 'Konfiguration prüfen'))
        rows=''.join(f'<div class="flex justify-between p-3 rounded-xl bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 text-xs"><span>{i} {esc(n)}</span><b>{esc(d)}</b></div>' for i,n,d in checks)
        body=f'<h1 class="text-2xl font-bold mb-2">🩺 Systemstatus</h1><p class="text-xs text-slate-500 mb-5">Konfigurations- und Verbindungscheck.</p><div class="space-y-2">{rows}</div>'
        return ws.render_page('Systemstatus',c,'system',body)

    @app.get("/health", response_class=JSONResponse)
    async def health():
        return {"ok":True,"time":ws.now_de().isoformat(),"db":os.path.exists(db.DB_PATH)}

    @app.get('/manifest.webmanifest')
    async def manifest():
        return JSONResponse({"name":"Pulse TeamOS","short_name":"Pulse","start_url":"/dashboard","display":"standalone","background_color":"#0b0e14","theme_color":"#4f46e5","icons":[]})

    @app.get('/sw.js')
    async def service_worker():
        return Response("self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>clients.claim());",media_type='application/javascript')

    async def housekeeping():
        last_day=''
        while True:
            try:
                await asyncio.sleep(300)
                today=ws.now_de().date(); marker=today.isoformat()
                if marker==last_day: continue
                last_day=marker
                loas=ws.get_loas()
                for l in loas.values():
                    try:
                        if l.get('active') and (datetime.strptime(l['bis'],'%Y-%m-%d').date()-today).days in (0,1):
                            uid=str(l['user_id']); db.notify(uid,'🌴 LOA-Erinnerung',f"Deine Abmeldung endet am {ws.fmt_date(l['bis'])}.",'warning','/loa')
                    except Exception: pass
                apps=ws.load_json(ws.APPS_FILE,{})
                # Erinnerungen für liegengebliebene Bewerbungen an berechtigte Teamleiter.
                managers=[]
                for m in team_members(getattr(app.state,'bot',None).get_guild(ws.GUILD_ID), ws.load_config().get('team_role_ids',[])) if getattr(app.state,'bot',None) and getattr(app.state,'bot',None).get_guild(ws.GUILD_ID) else []:
                    perms,_=ws.compute_perms(app.state.bot.get_guild(ws.GUILD_ID),m.id,ws.load_config())
                    if perms.get('can_manage_applications') or perms.get('can_promote') or perms.get('is_admin'): managers.append(m)
                for aid,a in apps.items():
                    if a.get('status')=='pending':
                        try:
                            created=datetime.strptime(a.get('created_at',''),'%d.%m.%Y %H:%M')
                            if (datetime.now()-created).total_seconds() >= 48*3600:
                                for m in managers: db.notify(m.id,'📝 Bewerbung wartet seit 48h+',f"Bewerbung {aid} von {a.get('name','Unbekannt')} wartet auf Bearbeitung.",'warning','/applications')
                        except Exception:
                            pass
                # Offene Tickets über 30 Minuten einmal täglich markieren.
                for t in db.list_tickets(limit=500):
                    if t.get('status')=='closed': continue
                    try:
                        opened=datetime.fromisoformat(t['opened_at'].replace('Z','+00:00'))
                        if (datetime.now(opened.tzinfo)-opened).total_seconds() >= 30*60:
                            targets=([c.guild.get_member(int(t['claimed_by_id']))] if t.get('claimed_by_id') else managers)
                            for m in [x for x in targets if x]: db.notify(m.id,'🎫 Ticket wartet seit 30+ Minuten',f"{t['id']} · {t['category']} · {t['user_name']}",'warning','/tickets')
                    except Exception:
                        pass
            except asyncio.CancelledError: return
            except Exception as e: print(f'Pulse Housekeeping: {e}')

    # Pulse Pro owns the single housekeeping loop. The legacy v4 loop is intentionally not started
    # here to prevent duplicate reminders and notification spam.

    @app.post("/backup/restore")
    async def restore_backup(request: Request, filename: str=Form(...), user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None,admin=True)
        path=ws.safe_backup_path(filename)
        if not path: return ws.back('/backups','Backup nicht gefunden.',False)
        try:
            payload=ws.load_json(path,{})
            panel=payload.get('panel_data',{})
            # Restore the JSON based legacy panel stores.
            for key, file in (("team_data",ws.DATA_FILE),("config",ws.CONFIG_FILE),("logs",ws.LOGS_FILE),("shifts",ws.SHIFTS_FILE),("meetings",ws.MEETINGS_FILE),("applications",ws.APPS_FILE)):
                if key in panel: ws.save_json(file,panel[key])
            if isinstance(panel.get('pulse_db'),dict): db.import_state(panel['pulse_db'])
            if isinstance(panel.get('loas'),list):
                conn=__import__('sqlite3').connect(ws.DB_ABMELDUNGEN)
                conn.execute('DELETE FROM abmeldungen')
                for row in panel['loas']:
                    conn.execute('INSERT OR REPLACE INTO abmeldungen(user_id,user_name,grund,von,bis,original_nick,guild_id) VALUES (?,?,?,?,?,?,?)',tuple(row.get(k) for k in ('user_id','user_name','grund','von','bis','original_nick','guild_id')))
                conn.commit(); conn.close()
            ws.log_audit(c.user.get('global_name'),c.user['id'],'Backup Wiederhergestellt',filename)
            return ws.back('/backups','Backup vollständig wiederhergestellt.')
        except Exception as e:
            return ws.back('/backups',f'Wiederherstellung fehlgeschlagen: {e}',False)



# Registration occurs after webserver module has defined its helpers/routes.
register_here = register
