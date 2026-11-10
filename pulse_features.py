import json
import os

from fastapi import Request, Form, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

import pulse_db as db
from pulse_safe_path import contained_file

def register(app):
    import webserver as ws
    # Phase 9 (A-04): tables are created by webserver.init_runtime / pulse_db.connect, not while registering routes.

    def ctx(request, session, perm=None, admin=False):
        return ws.auth(request, session, perm=perm, admin=admin)

    def esc(v): return ws.esc(v)
    def card(body="", cls=""): return f'<div class="{ws.CARD} p-5 {cls}">{body}</div>'
    def badge(text, kind="ok"):
        c = ws.BADGE_OK if kind == "ok" else ws.BADGE_BAD if kind == "bad" else ws.BADGE_WARN
        return f'<span class="text-[10px] border rounded-full px-2.5 py-1 font-semibold {c}">{esc(text)}</span>'

    @app.post("/tasks/create")
    async def task_create(request: Request, title: str=Form(...), description: str=Form(""), assignee_id: str=Form(""), priority: str=Form("normal"), due_at: str=Form(""), user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None); manager=c.perms.get("can_manage_tasks") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not manager: raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        m=c.guild.get_member(int(assignee_id)) if assignee_id.isdigit() else None
        tid=db.create_task(title.strip()[:120],description.strip()[:600],m.id if m else None,m.display_name if m else "",c.user["id"],c.user.get("global_name") or "Team",priority,due_at or None)
        if m: db.notify(m.id,"📋 Neue Aufgabe",f"{c.user.get('global_name')}: {title.strip()[:120]}","task","/tasks")
        ws.log_audit(c.user.get("global_name"),c.user["id"],"Aufgabe Erstellt",f"{title.strip()[:120]} · {tid}")
        return ws.back("/tasks","Aufgabe erstellt.")

    @app.get("/ticket/transcript/{filename}")
    async def ticket_transcript(request: Request, filename: str, user_session: str=Cookie(None)):
        c=ctx(request,user_session)
        # Gleiche Regel wie /tickets/transcript/{ticket_id}: Transkripte nur mit Ticketverwaltung (oder Admin).
        if not (c.perms.get('can_manage_tickets') or c.perms.get('is_admin')): raise HTTPException(403,'Dafür fehlt dir die Berechtigung.')
        safe=os.path.basename(filename)
        path=contained_file(os.path.join(str(ws.DATA_DIR),'transcripts'),safe)
        if not safe.endswith('.txt') or not path or not os.path.isfile(path): raise HTTPException(404,'Transcript nicht gefunden.')
        return __import__('fastapi').responses.FileResponse(path,filename=safe,media_type='text/plain')

    @app.post("/tickets/rate")
    async def ticket_rate(request: Request, ticket_id: str=Form(...), rating: int=Form(...), comment: str=Form(""), user_session: str=Cookie(None)):
        c=ctx(request,user_session); t=db.get_ticket(ticket_id=ticket_id)
        if not t or str(t['user_id'])!=str(c.user['id']): raise HTTPException(403,"Du darfst nur deine eigenen Tickets bewerten.")
        if rating<1 or rating>5: raise HTTPException(400,"Bewertung muss 1 bis 5 sein.")
        # Wie in der UI: nur geschlossene, noch unbewertete Tickets (sonst beliebiges Überschreiben + Benachrichtigungs-Spam).
        if t.get('status')!='closed' or t.get('rating'): return ws.back("/tickets","Dieses Ticket kann nicht (mehr) bewertet werden.",False)
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
        content=esc(p["content"]).replace("\\n","<br>")
        edit=f'<a href="/wiki" class="text-xs text-indigo-600">← Zurück</a>'
        edit_form=""
        if manager:
            edit_form=card(
                f'<h2 class="text-sm font-bold mb-3">✏️ Artikel bearbeiten</h2>'
                f'<form action="/wiki/save" method="post" class="space-y-3 text-xs">'
                f'<input type="hidden" name="page_id" value="{esc(page_id)}">'
                f'<input name="title" required maxlength="120" value="{esc(p["title"])}" class="{ws.INPUT}">'
                f'<input name="category" required maxlength="60" value="{esc(p["category"])}" class="{ws.INPUT}">'
                f'<textarea name="content" required maxlength="10000" class="{ws.INPUT} h-44">{esc(p["content"])}</textarea>'
                f'<button class="{ws.BTN} py-2.5 px-4">Neue Version speichern</button></form>'
            )
        versions=db.wiki_page_versions(page_id,limit=50)
        version_rows=[]
        for version in versions:
            version_content=esc(version["content"]).replace("\\n","<br>")
            created=str(version.get("created_at") or "")[:16].replace("T"," ")
            restored_from=version.get("restored_from_version")
            restored_label=f' · Wiederhergestellt aus Version {int(restored_from)}' if restored_from else ""
            restore_form=""
            if manager and int(version["version_number"]) != max((int(v["version_number"]) for v in versions),default=0):
                restore_form=(
                    f'<form method="post" action="/wiki/{esc(page_id)}/restore/{int(version["version_number"])}" class="mt-3">'
                    f'<button class="{ws.BTN} py-2 px-3" type="submit">Diese Version wiederherstellen</button></form>'
                )
            version_rows.append(
                f'<details class="{ws.CARD} p-4"><summary class="cursor-pointer text-xs font-semibold">'
                f'Version {int(version["version_number"])} · {esc(version.get("author_name") or "Team")} · {esc(created)}{esc(restored_label)}</summary>'
                f'<div class="mt-3 text-sm leading-7">{version_content}</div>{restore_form}</details>'
            )
        history=(
            '<section class="mt-6"><h2 class="text-sm font-bold mb-3">Versionsverlauf</h2>'
            + "".join(version_rows) + "</section>"
        )
        body=(
            f'<div class="max-w-4xl"><div class="flex justify-between gap-3 mb-5"><div>'
            f'<span class="text-[10px] text-indigo-500 font-semibold">{esc(p["category"])}</span>'
            f'<h1 class="text-2xl font-bold">{esc(p["title"])}</h1></div>{edit}</div>'
            f'<div class="{ws.CARD} p-6 text-sm leading-7 whitespace-normal">{content}</div>'
            f'<p class="text-[10px] text-slate-400 mt-3">Zuletzt geändert: {esc(p["updated_at"][:16].replace("T"," "))} · {esc(p["author_name"])}</p>'
            f'{edit_form}{history}</div>'
        )
        return ws.render_page(p["title"],c,"wiki",body)

    @app.post("/wiki/save")
    async def wiki_save(
        request: Request, title: str=Form(...), category: str=Form(...),
        content: str=Form(...), page_id: str=Form(""), user_session: str=Cookie(None),
    ):
        c=ctx(request,user_session,perm=None)
        manager=c.perms.get("can_manage_wiki") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not manager: raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        title=title.strip()[:120]; category=category.strip()[:60]; content=content.strip()[:10000]
        if not title or not category or not content:
            raise HTTPException(400,"Titel, Kategorie und Inhalt dürfen nicht leer sein.")
        page_id=str(page_id or "").strip()
        if page_id and not db.get_wiki(page_id):
            raise HTTPException(404,"Wiki-Seite nicht gefunden.")
        pid=db.save_wiki(
            title,category,content,c.user["id"],c.user.get("global_name") or "Team",
            page_id=page_id or None,
        )
        action="Wiki-Seite Aktualisiert" if page_id else "Wiki-Seite Erstellt"
        ws.log_audit(c.user.get("global_name"),c.user["id"],action,f'{title} · {pid}')
        return ws.back(f"/wiki/{pid}","Neue Wiki-Version gespeichert." if page_id else "Wiki-Seite gespeichert.")

    @app.post("/wiki/{page_id}/restore/{version_number}")
    async def wiki_restore_version(
        request: Request, page_id: str, version_number: int,
        user_session: str=Cookie(None),
    ):
        c=ctx(request,user_session,perm=None)
        manager=c.perms.get("can_manage_wiki") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not manager: raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        if version_number < 1:
            raise HTTPException(400,"Ungültige Wiki-Version.")
        try:
            new_version=db.restore_wiki_version(
                page_id,version_number,c.user["id"],c.user.get("global_name") or "Team",
            )
        except ValueError as exc:
            raise HTTPException(404,str(exc))
        ws.log_audit(
            c.user.get("global_name"),c.user["id"],"Wiki-Version Wiederhergestellt",
            f"{page_id} · Zielversion {version_number} · neue Version {new_version}",
        )
        return ws.back(f"/wiki/{page_id}",f"Version {version_number} wurde als neue Version {new_version} wiederhergestellt.")

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
        def _valid_question(q):
            if not isinstance(q,dict) or not q.get('question') or not isinstance(q.get('options'),list) or len(q['options'])<2: return False
            try: return 0<=int(q.get('answer_index',-1))<len(q['options'])
            except (TypeError,ValueError): return False
        if not isinstance(qs,list) or not qs or not all(_valid_question(q) for q in qs):
            return ws.back('/training','Fragenformat ungültig.',False)
        tid=db.create_training(title.strip()[:120],description.strip()[:500],max(1,min(100,passing_score)),max(1,min(180,time_limit)),c.user['id'],c.user.get('global_name') or 'Team',qs)
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Schulung Erstellt',f'{title.strip()} · {tid}')
        return ws.back('/training','Schulung erstellt.')

    @app.get("/training/{training_id}", response_class=HTMLResponse)
    async def training_detail(request: Request, training_id: str, user_session: str=Cookie(None)):
        c=ctx(request,user_session); t=db.get_training(training_id)
        if not t: raise HTTPException(404,'Schulung nicht gefunden.')
        qs=''.join(f'''<div class="{ws.CARD} p-5"><div class="font-semibold text-xs mb-3">{i+1}. {esc(q["question"])}</div>{"".join(f'<label class="flex gap-2 items-center p-2 rounded-lg bg-slate-50 dark:bg-[#0b0e14] mb-1 text-xs"><input type="radio" name="q_{q["id"]}" value="{j}" required><span>{esc(opt)}</span></label>' for j,opt in enumerate(q['options']))}</div>''' for i,q in enumerate(t['questions']))
        body=f'<div class="max-w-3xl"><h1 class="text-2xl font-bold">🎓 {esc(t["title"])}</h1><p class="text-xs text-slate-500 mt-1 mb-5">Bestehensgrenze: {t["passing_score"]}% · Zeitlimit: {t["time_limit_minutes"]} Minuten</p><form action="/training/{esc(t["id"])}/submit" method="post" class="space-y-3">{qs}<button class="{ws.BTN} px-5 py-3">Test abgeben</button></form></div>'
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
        try:
            from cogs.pulse_suite import mark_training_assignment_result
            mark_training_assignment_result(training_id, c.user["id"], passed)
        except Exception as exc:
            # The quiz result remains valid even when the optional assignment ledger is unavailable.
            print(f"Schulungszuweisung konnte nicht aktualisiert werden: {type(exc).__name__}")
        ws.log_audit(c.user.get('global_name'),c.user['id'],'Schulung Abgelegt',f'{t["title"]}: {score}% · {"bestanden" if passed else "nicht bestanden"}')
        icon='✅' if passed else '❌'; textmsg=f'{icon} Ergebnis: {score}% – {"Bestanden" if passed else "Nicht bestanden"}.'
        return HTMLResponse(ws.simple_page(icon,'Testergebnis',textmsg,'<a href="/training" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zurück zu Schulungen</a>'))

    # Pulse Pro owns the single housekeeping loop. The legacy v4 loop is intentionally not started
    # here to prevent duplicate reminders and notification spam.

# Registration occurs after webserver module has defined its helpers/routes.
register_here = register
