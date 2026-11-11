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

    @app.post("/tasks/distribute")
    async def tasks_distribute(request: Request, user_session: str=Cookie(None)):
        c=ctx(request,user_session,perm=None)
        manager=c.perms.get("can_manage_tasks") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not manager:
            raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        configured_role_ids={
            str(value) for value in (c.config.get("team_role_ids",[]) or [])
            if str(value).isdigit()
        }
        guild=getattr(c,"guild",None)
        candidates=[]
        for member in getattr(guild,"members",[])[:500]:
            if getattr(member,"bot",False):
                continue
            member_roles={
                str(getattr(role,"id","")) for role in getattr(member,"roles",[])
            }
            if member_roles & configured_role_ids:
                candidates.append({
                    "id":str(getattr(member,"id","")),
                    "name":str(getattr(member,"display_name",getattr(member,"name","Teammitglied"))),
                })
        assignments=db.auto_distribute_unassigned_tasks(
            candidates,c.user["id"],c.user.get("global_name") or "Team",limit=100,
        )
        if not assignments:
            return ws.back("/tasks","Keine unzugewiesenen aktiven Aufgaben oder keine geeigneten Teammitglieder gefunden.",False)
        by_member={}
        for item in assignments:
            by_member[item["assignee_name"]]=by_member.get(item["assignee_name"],0)+1
        summary=", ".join(f"{name}: {count}" for name,count in sorted(by_member.items()))
        ws.log_audit(
            c.user.get("global_name"),c.user["id"],"Automatische Arbeitslastverteilung",
            f'{len(assignments)} neue Aufgaben verteilt · {summary}',
        )
        return ws.back("/tasks",f'{len(assignments)} unzugewiesene Aufgabe(n) verteilt: {summary}.',True)

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
        c=ctx(request,user_session)
        trainings=[item for item in db.training_list() if item.get("published")]
        manager=c.perms.get("can_manage_training") or c.perms.get("can_promote") or c.perms.get("is_admin")

        cards=''.join(
            f'<a href="/training/{t["id"]}" class="{ws.CARD} p-5 hover:border-indigo-500">'
            f'<div class="flex justify-between gap-3"><h2 class="font-bold text-sm">🎓 {esc(t["title"])}</h2>'
            f'{badge(str(t["passing_score"])+"% Bestehen","ok")}</div>'
            f'<p class="text-xs text-slate-500 mt-2">{esc(t["description"])}</p></a>'
            for t in trainings
        )

        guild=getattr(c,"guild",None)
        user_id=str(c.user["id"])
        member=None
        if guild and user_id.isdigit():
            try:
                member=guild.get_member(int(user_id))
            except Exception:
                member=None
        user_role_ids=[str(role.id) for role in getattr(member,"roles",[]) if getattr(role,"id",None)]
        assigned_paths=db.training_paths_for_roles(user_role_ids)
        path_training_ids=[
            item["training_id"] for path in assigned_paths for item in path["items"]
        ]
        passed_training_ids=db.passed_training_ids_for_user(user_id,path_training_ids)
        path_cards=[]
        for path in assigned_paths:
            progress=db.training_path_progress(path["id"],user_id)
            steps=[]
            for item in path["items"]:
                is_complete=str(item["training_id"]) in passed_training_ids
                steps.append(
                    f'<li class="flex items-center justify-between gap-3 py-2 border-b border-slate-200 dark:border-slate-800 last:border-0">'
                    f'<a class="text-xs hover:underline" href="/training/{esc(item["training_id"])}">{esc(item["title"])}</a>'
                    f'<span class="text-[10px] font-semibold {"text-emerald-600" if is_complete else "text-slate-400"}">{"✅ Bestanden" if is_complete else "○ Offen"}</span></li>'
                )
            path_cards.append(
                f'<section class="{ws.CARD} p-5"><div class="flex flex-wrap items-center justify-between gap-2">'
                f'<div><div class="text-[10px] uppercase tracking-wide text-indigo-500">{esc(path["role_name"])}</div>'
                f'<h2 class="text-sm font-bold">{esc(path["title"])}</h2>'
                f'<p class="text-xs text-slate-500 mt-1">{esc(path["description"])}</p></div>'
                f'<div class="text-right"><div class="text-lg font-bold tabular-nums">{progress["percent"]}%</div>'
                f'<div class="text-[10px] text-slate-500">{progress["completed"]}/{progress["total"]} Schulungen</div></div></div>'
                f'<div class="h-2 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden mt-3">'
                f'<div class="h-full bg-emerald-500" style="width:{progress["percent"]}%"></div></div>'
                f'<ol class="mt-3">{"".join(steps) or "<li class=\'text-xs text-slate-400\'>Keine veröffentlichten Schulungen im Lernpfad.</li>"}</ol></section>'
            )
        learning_paths_html=(
            '<section class="mb-6"><h2 class="text-lg font-bold mb-3">🧭 Deine Lernpfade</h2>'
            + "".join(path_cards)
            + '</section>'
            if path_cards else
            '<section class="mb-6"><h2 class="text-lg font-bold mb-2">🧭 Deine Lernpfade</h2>'
            '<p class="text-xs text-slate-500">Für deine aktuellen Teamrollen ist noch kein Lernpfad hinterlegt.</p></section>'
        )

        form=''
        path_admin=''
        if manager:
            placeholder='[{"question":"Was bedeutet IC?","options":["In Character","Intern","Information Check","Keine Ahnung"],"answer_index":0}]'
            form=card(
                f'<h2 class="text-sm font-bold mb-3">➕ Schulung erstellen</h2>'
                f'<form action="/training/create" method="post" class="space-y-3 text-xs">'
                f'<input name="title" required maxlength="120" placeholder="Titel…" class="{ws.INPUT}">'
                f'<textarea name="description" maxlength="500" placeholder="Beschreibung…" class="{ws.INPUT} h-20"></textarea>'
                f'<div class="grid grid-cols-2 gap-3"><input type="number" name="passing_score" min="1" max="100" value="80" class="{ws.INPUT}">'
                f'<input type="number" name="time_limit" min="1" max="180" value="20" class="{ws.INPUT}"></div>'
                f'<textarea name="questions_json" required placeholder="{esc(placeholder)}" class="{ws.INPUT} h-48 font-mono text-[10px]"></textarea>'
                f'<button class="{ws.BTN} py-2.5 px-4">Schulung veröffentlichen</button></form>'
            )

            configured_role_ids={
                str(role_id) for role_id in (c.config.get("team_role_ids",[]) or [])
                if str(role_id).isdigit()
            }
            team_roles=sorted(
                (role for role in getattr(guild,"roles",[]) if str(getattr(role,"id","")) in configured_role_ids),
                key=lambda role:(int(getattr(role,"position",0) or 0),str(getattr(role,"name","")).casefold()),
                reverse=True,
            )
            role_by_id={str(role.id):role for role in team_roles}

            def path_editor(path=None):
                role_id=str(path["role_id"]) if path else ""
                role=role_by_id.get(role_id)
                selected_ids={str(item["training_id"]) for item in path["items"]} if path else set()
                options=''.join(
                    f'<label class="flex items-center gap-2 rounded-lg border border-slate-200 dark:border-slate-700 p-2 text-xs">'
                    f'<input type="checkbox" name="training_ids" value="{esc(t["id"])}" {"checked" if str(t["id"]) in selected_ids else ""}>'
                    f'<span>{esc(t["title"])}</span></label>'
                    for t in trainings
                )
                if path and not role:
                    return card(
                        f'<div class="text-xs font-semibold">{esc(path["role_name"])} · Rolle nicht mehr als Teamrolle konfiguriert</div>'
                        f'<p class="text-[10px] text-slate-500 mt-1">Pfad-ID: {esc(path["id"])}</p>'
                    )
                if path:
                    role_control=(
                        f'<input type="hidden" name="role_id" value="{esc(role_id)}">'
                        f'<div class="text-xs font-semibold">{esc(role.name)} · Bestehenden Lernpfad bearbeiten</div>'
                    )
                    title_value=path["title"]
                    desc_value=path["description"]
                    button="Lernpfad aktualisieren"
                else:
                    role_options=''.join(
                        f'<option value="{esc(item.id)}">{esc(item.name)}</option>'
                        for item in team_roles
                    )
                    role_control=(
                        f'<label class="block text-xs font-semibold">Teamrolle'
                        f'<select name="role_id" required class="{ws.INPUT} mt-1">{role_options}</select></label>'
                    )
                    title_value=""
                    desc_value=""
                    button="Lernpfad erstellen"
                return card(
                    f'<form action="/training/paths/save" method="post" class="space-y-3 text-xs">'
                    f'{role_control}'
                    f'<input name="title" required maxlength="120" value="{esc(title_value)}" placeholder="Titel des Lernpfads" class="{ws.INPUT}">'
                    f'<textarea name="description" maxlength="500" placeholder="Ziel und Beschreibung…" class="{ws.INPUT} h-16">{esc(desc_value)}</textarea>'
                    f'<div class="font-semibold">Schulungen in Reihenfolge auswählen (1–30)</div>'
                    f'<div class="grid sm:grid-cols-2 gap-2">{options or "<p class=\'text-slate-400\'>Erstelle zuerst veröffentlichte Schulungen.</p>"}</div>'
                    f'<button class="{ws.BTN} py-2.5 px-4" {"disabled" if not trainings else ""}>{button}</button></form>'
                )

            existing_paths=db.training_paths()
            editors=''.join(path_editor(path) for path in existing_paths)
            create_form=path_editor(None) if team_roles else card(
                '<p class="text-xs text-slate-500">Es sind keine Teamrollen konfiguriert. Konfiguriere zuerst die vorhandenen Teamrollen.</p>'
            )
            path_admin=(
                '<section class="mt-6 space-y-3"><h2 class="text-lg font-bold">🛠 Rollen-Lernpfade verwalten</h2>'
                '<p class="text-xs text-slate-500">Jeder Lernpfad gehört zu einer konfigurierten Teamrolle. '
                'Aktualisieren ersetzt nur die Reihenfolge dieses Rollenpfads.</p>'
                + editors + create_form + '</section>'
            )

        body=(
            f'<h1 class="text-2xl font-bold mb-2">🎓 Schulungen</h1>'
            f'<p class="text-xs text-slate-500 mb-5">Prüfungen, Grundkompetenzen und rollenbezogene Ausbildungswege.</p>'
            f'{learning_paths_html}<h2 class="text-lg font-bold mb-3">Alle Schulungen</h2>'
            f'<div class="grid md:grid-cols-2 gap-4">{cards or card("<p class=\'text-xs text-slate-400\'>Noch keine Schulungen.</p>")}</div>'
            f'{f"<div class=\'mt-5 max-w-3xl\'>{form}</div>" if form else ""}{path_admin}'
        )
        return ws.render_page("Schulungen",c,"training",body)

    @app.post("/training/paths/save")
    async def training_path_save(
        request: Request, role_id: str=Form(...), title: str=Form(...),
        description: str=Form(""), training_ids: list[str]=Form([]),
        user_session: str=Cookie(None),
    ):
        c=ctx(request,user_session,perm=None)
        manager=c.perms.get("can_manage_training") or c.perms.get("can_promote") or c.perms.get("is_admin")
        if not manager:
            raise HTTPException(403,"Dafür fehlt dir die Berechtigung.")
        configured_role_ids={
            str(value) for value in (c.config.get("team_role_ids",[]) or [])
            if str(value).isdigit()
        }
        role_id=str(role_id or "").strip()
        if role_id not in configured_role_ids:
            raise HTTPException(400,"Nur konfigurierte BOCHUM-RP-Teamrollen dürfen Lernpfade erhalten.")
        guild=getattr(c,"guild",None)
        role=guild.get_role(int(role_id)) if guild and role_id.isdigit() else None
        if not role:
            raise HTTPException(404,"Teamrolle auf dem Server nicht gefunden.")
        try:
            path_id=db.save_training_path(
                role_id,role.name,title,description,c.user["id"],
                c.user.get("global_name") or "Team",training_ids,
            )
        except ValueError as exc:
            raise HTTPException(400,str(exc))
        ws.log_audit(
            c.user.get("global_name"),c.user["id"],"Rollen-Lernpfad gespeichert",
            f"{role.name} · {title.strip()[:120]} · {path_id}",
        )
        return ws.back("/training",f"Lernpfad für {role.name} gespeichert.")

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
        if not t: raise HTTPException(404,"Schulung nicht gefunden.")
        qs="".join(
            f'<div class="{ws.CARD} p-5"><div class="font-semibold text-xs mb-3">{i+1}. {esc(q["question"])}</div>'
            + "".join(
                f'<label class="flex gap-2 items-center p-2 rounded-lg bg-slate-50 dark:bg-[#0b0e14] mb-1 text-xs">'
                f'<input type="radio" name="q_{q["id"]}" value="{j}" required><span>{esc(opt)}</span></label>'
                for j,opt in enumerate(q["options"])
            )
            + "</div>"
            for i,q in enumerate(t["questions"])
        )
        training_id_safe=esc(t["id"])
        body=(
            f'<div class="max-w-3xl"><h1 class="text-2xl font-bold">🎓 {esc(t["title"])}</h1>'
            f'<p class="text-xs text-slate-500 mt-1 mb-5">Bestehensgrenze: {t["passing_score"]}% · Zeitlimit: {t["time_limit_minutes"]} Minuten</p>'
            f'<form action="/training/{training_id_safe}/submit" method="post" class="space-y-3">{qs}'
            f'<button class="{ws.BTN} px-5 py-3">Test abgeben</button></form>'
        )
        feedback_summary=db.training_feedback_summary(training_id)
        if feedback_summary["count"]:
            rating_line=f'⭐ {feedback_summary["average"]:.2f}/5 · {feedback_summary["count"]} Bewertungen'
        else:
            rating_line="Noch keine Bewertungen."
        body+=(
            f'<section class="mt-8 {ws.CARD} p-5"><h2 class="text-sm font-bold">💬 Schulungsfeedback</h2>'
            f'<p class="text-xs text-slate-500 mt-2">{rating_line}</p>'
        )
        my_feedback=db.get_training_feedback(training_id,c.user["id"])
        if db.has_training_attempt(training_id,c.user["id"]):
            selected=int(my_feedback["rating"]) if my_feedback else 0
            options="".join(
                f'<option value="{rating}" {"selected" if rating==selected else ""}>{rating} von 5 Sternen</option>'
                for rating in range(5,0,-1)
            )
            old_comment=esc(my_feedback["feedback"]) if my_feedback else ""
            submit_label="Feedback aktualisieren" if my_feedback else "Feedback abgeben"
            body+=(
                f'<form method="post" action="/training/{training_id_safe}/feedback" class="mt-3 space-y-3">'
                f'<label class="block text-xs font-semibold" for="trainingRating">Deine Bewertung</label>'
                f'<select id="trainingRating" name="rating" required class="{ws.INPUT}">{options}</select>'
                f'<label class="block text-xs font-semibold" for="trainingFeedback">Was können wir verbessern? (optional)</label>'
                f'<textarea id="trainingFeedback" name="feedback" maxlength="500" class="{ws.INPUT} h-24" placeholder="Dein Feedback bleibt ohne Namen in der Auswertung.">{old_comment}</textarea>'
                f'<button class="{ws.BTN} px-4 py-2">{submit_label}</button></form>'
                '<p class="text-[10px] text-slate-400 mt-2">Die Auswertung zeigt nur Durchschnitt und anonyme Kommentare.</p>'
            )
        else:
            body+='<p class="text-xs text-slate-500 mt-3">Nach deiner ersten absolvierten Schulung kannst du hier eine Bewertung abgeben.</p>'
        if c.perms.get("can_manage_training") or c.perms.get("can_promote") or c.perms.get("is_admin"):
            comments=feedback_summary["comments"]
            comments_html="".join(
                f'<div class="border-t border-slate-200 dark:border-slate-700 py-3">'
                f'<div class="text-xs font-semibold">{"⭐" * int(row["rating"])} · {int(row["rating"])}/5</div>'
                f'<p class="text-xs mt-1 whitespace-pre-wrap">{esc(row["feedback"])}</p></div>'
                for row in comments
            ) or '<p class="text-xs text-slate-400 mt-3">Noch keine Kommentare.</p>'
            body+=f'<div class="mt-4"><h3 class="text-xs font-bold">Anonyme Rückmeldungen</h3>{comments_html}</div>'
        body+="</section></div>"
        return ws.render_page(t["title"],c,"training",body)

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

    @app.post("/training/{training_id}/feedback")
    async def training_feedback_submit(
        request: Request, training_id: str, user_session: str=Cookie(None),
    ):
        c=ctx(request,user_session)
        training=db.get_training(training_id)
        if not training: raise HTTPException(404,"Schulung nicht gefunden.")
        if not db.has_training_attempt(training_id,c.user["id"]):
            raise HTTPException(403,"Feedback ist erst nach einer absolvierten Schulung möglich.")
        form=await request.form()
        try:
            rating=int(form.get("rating"))
        except (TypeError,ValueError):
            raise HTTPException(400,"Bitte eine Bewertung zwischen 1 und 5 auswählen.")
        comment=str(form.get("feedback") or "")
        try:
            db.save_training_feedback(training_id,c.user["id"],rating,comment)
        except ValueError as exc:
            raise HTTPException(400,str(exc))
        ws.log_audit(
            c.user.get("global_name"),c.user["id"],"Schulungsfeedback gespeichert",
            f'{training["title"]}: {rating}/5',
        )
        return ws.back(f"/training/{training_id}","Danke für dein Schulungsfeedback.")

    # Pulse Pro owns the single housekeeping loop. The legacy v4 loop is intentionally not started
    # here to prevent duplicate reminders and notification spam.

# Registration occurs after webserver module has defined its helpers/routes.
register_here = register
