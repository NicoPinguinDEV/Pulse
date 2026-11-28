"""Public recruiting flow, applications, interviews, evaluation and reminders."""

from pulse_web._compat import bind_legacy_namespace as _bind_legacy_namespace
_bind_legacy_namespace(globals())
del _bind_legacy_namespace

def apply_draft_key(applicant_id, career_path: str) -> str:
    # Embedded in a <script>: only a numeric Discord ID and a configured career key may end up there.
    applicant = int(applicant_id) if str(applicant_id or "").isdigit() else 0
    return f"pulse:apply-draft:v1:{applicant}:{known_career(career_path)}"


# K-01: browser-side draft (localStorage, per Discord ID + career) and session keep-alive.
APPLY_DRAFT_SCRIPT = """
<div id="applyDraftNote" hidden class="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-[11px]">
  <span id="applyDraftMessage"></span>
  <button id="applyDraftDelete" type="button" hidden class="ml-2 underline font-semibold">Gespeicherten Entwurf löschen</button>
</div>
<div id="applySessionNote" hidden class="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-[11px]"></div>
<script>
(function(){
  const form=document.querySelector('[data-apply-wizard]'); if(!form)return;
  const KEY=__KEY__, LOGIN=__LOGIN__, TTL_MS=__TTL_MS__;
  const note=document.getElementById('applyDraftNote');
  const message=document.getElementById('applyDraftMessage');
  const removeButton=document.getElementById('applyDraftDelete');
  const sess=document.getElementById('applySessionNote');
  const skip=new Set(['action','applicant_id','applicant_name','career_path']);
  const fields=()=>Array.from(form.querySelectorAll('input,textarea,select')).filter(el=>el.name&&!skip.has(el.name));
  function showDraft(text, removable){
    note.hidden=false;
    message.textContent=text;
    removeButton.hidden=!removable;
  }
  function removeStoredDraft(){
    try{localStorage.removeItem(KEY);}catch(_){}
  }
  function store(){
    try{
      const data={};
      fields().forEach(el=>{
        if(el.type==='radio'||el.type==='checkbox'){ if(el.checked)(data[el.name]=data[el.name]||[]).push(el.value); }
        else data[el.name]=el.value;
      });
      localStorage.setItem(KEY,JSON.stringify({at:Date.now(),data:data}));
      showDraft('Dein Entwurf wird nur in diesem Browser gespeichert. Nach 30 Tagen ohne Änderung gilt er als abgelaufen und wird beim nächsten Öffnen des Formulars entfernt.',true);
    }catch(_){}
  }
  function restore(){
    let payload;
    try{
      const raw=localStorage.getItem(KEY);
      if(!raw)return false;
      payload=JSON.parse(raw);
    }catch(_){
      removeStoredDraft();
      return false;
    }
    const savedAt=Number(payload && payload.at);
    if(!Number.isFinite(savedAt)||savedAt<=0||savedAt>Date.now()+5*60*1000||Date.now()-savedAt>TTL_MS){
      removeStoredDraft();
      return false;
    }
    const saved=payload.data&&typeof payload.data==='object'?payload.data:{};
    let n=0;
    fields().forEach(el=>{
      if(!(el.name in saved))return;
      const v=saved[el.name];
      if(el.type==='radio'||el.type==='checkbox')el.checked=Array.isArray(v)&&v.indexOf(el.value)>=0;
      else el.value=String(v??'');
      n++;
    });
    return n>0;
  }
  let timer=null, active=false, restoring=false;
  form.addEventListener('input',()=>{active=true;clearTimeout(timer);timer=setTimeout(store,400);});
  form.addEventListener('change',()=>{if(restoring)return;active=true;store();});
  form.addEventListener('submit',store);
  removeButton.addEventListener('click',()=>{
    clearTimeout(timer);
    active=false;
    removeStoredDraft();
    showDraft('Der lokal gespeicherte Entwurf wurde gelöscht. Bereits abgesendete Bewerbungsdaten sind davon nicht betroffen.',false);
  });
  if(restore()){
    showDraft('Dein gespeicherter Entwurf wurde wiederhergestellt. Wenn du ihn 30 Tage nicht änderst, wird er beim nächsten Öffnen des Formulars entfernt.',true);
    restoring=true;
    form.dispatchEvent(new Event('change'));
    restoring=false;
  }
  async function keepAlive(){
    if(!active)return;
    active=false;
    try{
      const r=await fetch('/apply/keepalive',{method:'POST',credentials:'same-origin'});
      if(r.status===401){
        sess.hidden=false;
        sess.innerHTML='⌛ Deine Sitzung ist abgelaufen. Deine Antworten sind in diesem Browser gespeichert. '
          +'<a class="underline font-semibold" href="'+LOGIN+'">Neu mit Discord verbinden</a> – danach ist alles wieder da.';
      }
    }catch(_){}
  }
  setInterval(keepAlive,10*60*1000);
})();
</script>"""

APPLY_DRAFT_CLEAR_SCRIPT = "<script>try{localStorage.removeItem(__KEY__)}catch(_){}</script>"


@app.get("/apply", response_class=HTMLResponse)
async def public_apply_page(request: Request, apply_session: str = Cookie(None)):
    if not pulse_public_site.public_access_enabled():
        raise HTTPException(status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"})
    bot = getattr(request.app.state, "bot", None)
    application_guild = bot.get_guild(GUILD_ID) if bot else None
    required_role_obj = application_guild.get_role(APPLICATION_REQUIRED_ROLE_ID) if application_guild else None
    required_role_display = esc(required_role_obj.name if required_role_obj else f"Discord-Rolle {APPLICATION_REQUIRED_ROLE_ID}")
    applicant = verify_session_token(apply_session, TOKEN_APPLY)
    if applicant:
        eligible, eligibility_status, eligibility_role, eligibility_message = await verify_application_member(
            request, applicant.get("id")
        )
        if not eligible:
            retry_link = '<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Erneut prüfen</a>'
            if eligibility_status == 503:
                return HTMLResponse(
                    simple_page("⏳", "Bewerbung gerade nicht verfügbar", eligibility_message, retry_link),
                    status_code=503,
                )
            return HTMLResponse(
                simple_page("🔒", "Bewerbung nicht freigegeben", eligibility_message, retry_link),
                status_code=403,
            )
    if not applicant:
        return f"""
        <!DOCTYPE html><html lang="de"><head>{get_head_html("Team-Bewerbung")}</head>
        <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="{CARD} p-8 shadow-xl w-full max-w-lg text-center space-y-5">
            <div class="text-4xl">📝</div><h1 class="text-xl font-bold">Team-Bewerbung</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400">Dieses Formular ist ausschließlich für Discord-Mitglieder mit der Rolle <strong>{required_role_display}</strong> freigeschaltet. Verbinde deinen Discord-Account, damit Pulse deine Mitgliedschaft prüfen kann. Danach kannst du deine Laufbahn auswählen und den passenden Eignungstest online absolvieren.</p>
            <a href="/apply/login" class="block w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 rounded-xl">Mit Discord verbinden</a>
            <a href="/" class="text-xs text-slate-400 hover:underline">← Zurück</a>
        </div></body></html>"""

    requested_career_path = (request.query_params.get("career") or "").strip()
    module_states = application_module_states(load_config())
    if requested_career_path in APPLICATION_CAREERS and not module_states.get(requested_career_path, True):
        return HTMLResponse(
            simple_page("⏸️", "Bewerbung derzeit geschlossen", "Diese Laufbahn nimmt aktuell keine Bewerbungen an. Bitte wähle eine andere aktive Laufbahn oder schau später wieder vorbei.", '<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Aktive Laufbahnen</a>'),
            status_code=404,
        )
    career_path = requested_career_path if requested_career_path in APPLICATION_CAREERS else ""

    applicant_name = applicant.get("global_name") or applicant.get("username") or "Discord-Mitglied"
    applicant_id = esc(applicant.get("id"))
    applicant_initial = esc((applicant_name.strip()[:1] or "?").upper())

    if not career_path:
        career_cards = ""
        for key, career in APPLICATION_CAREERS.items():
            if not module_states.get(key, True):
                continue
            career_cards += f"""
            <a href="/apply?career={key}" class="block text-left rounded-2xl border border-slate-200 dark:border-slate-700 p-4 hover:border-indigo-500 hover:shadow-md transition">
                <div class="flex items-center justify-between gap-3">
                    <h2 class="font-bold text-base">{esc(career["label"])}</h2>
                    <span class="text-[10px] font-bold rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 px-2.5 py-1">Eignungstest + Praxis</span>
                </div>
                <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">{esc(career["description"])}</p>
                <p class="text-[10px] text-slate-400 mt-3">{len(career["questions"])} Testfragen · {len(APPLICATION_PRACTICAL_TASKS.get(key, []))} Praxisaufgaben · Bestehens-Richtwert: {career["pass_percent"]}%</p>
            </a>"""
        if not career_cards:
            return HTMLResponse(simple_page("⏸️", "Aktuell keine Bewerbungen geöffnet", "Die Leitung hat vorübergehend alle Bewerbungsmodule deaktiviert. Bitte schau später wieder vorbei."), status_code=503)
        return f"""
        <!DOCTYPE html><html lang="de"><head>{get_head_html("Laufbahn wählen · Team-Bewerbung")}</head>
        <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <main class="w-full max-w-2xl space-y-5">
            <div class="{CARD} p-6 sm:p-8 space-y-5">
                <div class="flex items-center gap-3">
                    <span class="w-11 h-11 rounded-full bg-indigo-500/10 text-indigo-500 flex items-center justify-center font-bold" aria-hidden="true">{applicant_initial}</span>
                    <div><div class="font-bold">{esc(applicant_name)}</div><div class="text-[10px] text-slate-400">Discord verifiziert · ID {applicant_id}</div></div>
                </div>
                <div><div class="text-xs uppercase tracking-widest font-bold text-indigo-500">Pulse Recruiting</div>
                    <h1 class="text-2xl font-bold mt-1">Welche Laufbahn möchtest du einschlagen?</h1>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">Wähle die Ebene, für die du dich bewerben möchtest. Jede Laufbahn hat eigene Fragen. Dein Ergebnis wird automatisch ausgewertet und zusammen mit deiner Bewerbung an das Team übermittelt.</p>
                </div>
                <div class="grid gap-3">{career_cards}</div>
                <p class="text-[10px] text-slate-400">Hinweis: Die Auswahl und das Testergebnis vergeben keine Discord-Rollen. Über eine Einstellung und die passende Rolle entscheidet weiterhin die zuständige Führungsebene.</p>
                <div class="text-center"><a href="/" class="text-xs text-slate-400 hover:underline">← Zurück</a></div>
            </div>
        </main></body></html>"""

    career = APPLICATION_CAREERS[career_path]
    question_markup = render_career_test(career_path)
    practical_markup = render_practical_tasks(career_path)
    import pulse_retention
    privacy_notice = pulse_retention.privacy_notice_html(esc)  # D-08
    draft_ttl_ms = pulse_retention.APPLICATION_DRAFT_TTL_DAYS * 24 * 60 * 60 * 1000
    draft_script = (APPLY_DRAFT_SCRIPT
                    .replace("__KEY__", json.dumps(apply_draft_key(applicant.get("id"), career_path)))
                    .replace("__LOGIN__", json.dumps(f"/apply/login?career={quote(career_path)}"))
                    .replace("__TTL_MS__", str(draft_ttl_ms)))
    page = f"""
    <!DOCTYPE html><html lang="de"><head>{get_head_html("Online-Eignungstest · " + career["label"])}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen p-4 sm:p-6 font-sans">
        <main class="mx-auto w-full max-w-3xl space-y-5">
            <div class="{CARD} p-6 sm:p-8 space-y-5">
                <div class="flex items-center gap-3">
                    <span class="w-11 h-11 rounded-full bg-indigo-500/10 text-indigo-500 flex items-center justify-center font-bold" aria-hidden="true">{applicant_initial}</span>
                    <div><div class="font-bold">{esc(applicant_name)}</div><div class="text-[10px] text-slate-400">Discord verifiziert</div></div>
                    <a href="/apply" class="ml-auto text-xs text-indigo-500 hover:underline">Laufbahn ändern</a>
                </div>
                <div class="rounded-2xl bg-indigo-500/10 border border-indigo-500/20 p-4">
                    <div class="text-[10px] uppercase tracking-widest font-bold text-indigo-500">Deine Bewerbung</div>
                    <h1 class="text-2xl font-bold mt-1">{esc(career["label"])}</h1>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">{esc(career["description"])}</p>
                    <p class="text-[10px] text-slate-500 dark:text-slate-400 mt-3">{len(career["questions"])} Fragen · Bestehens-Richtwert: {career["pass_percent"]}%</p>
                </div>
                <form action="/action" method="post" class="space-y-5 text-xs" data-apply-wizard novalidate>
                    <input type="hidden" name="action" value="submit_application">
                    <input type="hidden" name="applicant_id" value="{applicant_id}">
                    <input type="hidden" name="applicant_name" value="{esc(applicant_name)}">
                    <input type="hidden" name="career_path" value="{career_path}">
                    <div class="rounded-2xl border border-indigo-500/20 bg-gradient-to-r from-indigo-500/10 to-violet-500/10 p-4 space-y-3">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <div><p class="text-[10px] uppercase tracking-widest font-bold text-indigo-500">Pulse Candidate Experience</p>
                            <p id="applicationProgressLabel" class="text-sm font-bold mt-1">Dein Fortschritt · Vorbereitung</p></div>
                            <span id="applicationProgressPercent" class="text-lg font-extrabold text-indigo-500">0%</span>
                        </div>
                        <div class="h-2 rounded-full overflow-hidden bg-slate-200 dark:bg-slate-800"><div id="applicationProgressBar" class="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500 transition-all duration-300" style="width:0%"></div></div>
                        <div class="grid grid-cols-3 gap-2">
                            <div data-apply-step-indicator="0" class="rounded-xl border border-indigo-500/30 bg-indigo-500/10 p-2"><p class="text-[10px] font-bold">01 · Profil</p><p class="text-[10px] text-slate-500 mt-1">Motivation</p></div>
                            <div data-apply-step-indicator="1" class="rounded-xl border border-slate-200 dark:border-slate-700 p-2"><p class="text-[10px] font-bold">02 · Wissen</p><p class="text-[10px] text-slate-500 mt-1">Regeltest</p></div>
                            <div data-apply-step-indicator="2" class="rounded-xl border border-slate-200 dark:border-slate-700 p-2"><p class="text-[10px] font-bold">03 · Praxis</p><p class="text-[10px] text-slate-500 mt-1">Fallsimulation</p></div>
                        </div>
                        <p class="text-[10px] text-slate-500 dark:text-slate-400">Deine Angaben bleiben bis zum Absenden in diesem Formular. Die Praxisantworten werden von Menschen geprüft — keine automatische Einstellungsentscheidung.</p>
                    </div>
                    <section data-apply-step="0" class="space-y-2">
                        <h2 class="font-bold text-base">1. Motivation</h2>
                        <label for="applicant_text" class="block text-slate-500 dark:text-slate-400">Warum möchtest du diese Laufbahn einschlagen? Beschreibe kurz deine Erfahrung, Stärken und Motivation.</label>
                        <textarea id="applicant_text" name="applicant_text" maxlength="2000" placeholder="Erzähle etwas über dich, deine Erfahrung und deine Motivation…" required class="{INPUT} h-32"></textarea>
                    </section>
                    <section data-apply-step="1" class="space-y-3">
                        <div><h2 class="font-bold text-base">2. Online-Eignungstest</h2>
                            <p class="text-slate-500 dark:text-slate-400 mt-1">Beantworte alle Fragen. Das System prüft deine Antworten automatisch. Das Ergebnis ist eine Entscheidungshilfe und führt nicht allein automatisch zur Annahme oder Ablehnung.</p>
                        </div>
                        {question_markup}
                    </section>
                    {practical_markup}
                    {privacy_notice}
                    <div class="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-200 dark:border-slate-800">
                        <button type="button" id="applicationPrev" hidden class="px-4 py-3 rounded-xl border border-slate-200 dark:border-slate-700 font-semibold">← Zurück</button>
                        <span id="applicationStepLabel" class="text-[10px] text-slate-500 dark:text-slate-400">Schritt 1 von 3</span>
                        <div class="ml-auto flex gap-2">
                            <button type="button" id="applicationNext" hidden class="px-5 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold">Weiter <span aria-hidden="true">→</span></button>
                            <button type="submit" id="applicationSubmit" class="px-5 py-3 rounded-xl bg-emerald-600 hover:bg-emerald-500 font-semibold text-white">Bewerbung abschicken ✓</button>
                        </div>
                    </div>
                    <script>
                    (function(){{
                        const form=document.querySelector('[data-apply-wizard]');
                        if(!form)return;
                        const panels=Array.from(form.querySelectorAll('[data-apply-step]'));
                        const prev=document.getElementById('applicationPrev');
                        const next=document.getElementById('applicationNext');
                        const submit=document.getElementById('applicationSubmit');
                        const stepLabel=document.getElementById('applicationStepLabel');
                        const progressLabel=document.getElementById('applicationProgressLabel');
                        const progressPercent=document.getElementById('applicationProgressPercent');
                        const progressBar=document.getElementById('applicationProgressBar');
                        const indicators=Array.from(form.querySelectorAll('[data-apply-step-indicator]'));
                        const names=['Profil & Motivation','Regelwissen','Praxis-Simulation'];
                        let active=0;
                        function updateProgress(){{
                            const controls=Array.from(form.querySelectorAll('[required]'));
                            const groups=new Map();
                            controls.forEach((el,index)=>{{
                                const key=el.type==='radio'?'radio:'+el.name:(el.name||'field:'+index);
                                if(!groups.has(key))groups.set(key,[]);
                                groups.get(key).push(el);
                            }});
                            let complete=0;
                            groups.forEach((group)=>{{
                                const first=group[0];
                                const done=first.type==='radio'?group.some(el=>el.checked):
                                    first.tagName==='SELECT'?Boolean(first.value):
                                    Boolean(String(first.value||'').trim())&&first.checkValidity();
                                if(done)complete++;
                            }});
                            const total=Math.max(1,groups.size);
                            const pct=Math.round(complete/total*100);
                            progressPercent.textContent=pct+'%';
                            progressBar.style.width=pct+'%';
                            progressLabel.textContent=complete+' von '+groups.size+' Pflichtfeldern erledigt';
                        }}
                        function showStep(index){{
                            active=Math.max(0,Math.min(index,panels.length-1));
                            panels.forEach((panel,i)=>panel.hidden=i!==active);
                            prev.hidden=active===0;
                            next.hidden=active===panels.length-1;
                            submit.hidden=active!==panels.length-1;
                            stepLabel.textContent='Schritt '+(active+1)+' von '+panels.length;
                            progressLabel.dataset.activeStep=String(active);
                            indicators.forEach((el,i)=>{{
                                el.classList.toggle('border-indigo-500/40',i===active);
                                el.classList.toggle('bg-indigo-500/10',i===active);
                                el.classList.toggle('border-slate-200',i!==active);
                            }});
                            const heading=panels[active].querySelector('h2,legend');
                            const label=heading?heading.textContent.trim():names[active];
                            stepLabel.setAttribute('aria-label',label);
                            window.scrollTo({{top:0,behavior:'smooth'}});
                            updateProgress();
                        }}
                        function firstInvalid(panel){{
                            return Array.from(panel.querySelectorAll('input,textarea,select')).find(el=>el.required&&!el.checkValidity());
                        }}
                        function validatePanel(panel){{
                            const invalid=firstInvalid(panel);
                            if(invalid){{invalid.reportValidity();invalid.focus({{preventScroll:true}});return false;}}
                            return true;
                        }}
                        prev.addEventListener('click',()=>showStep(active-1));
                        next.addEventListener('click',()=>{{
                            if(validatePanel(panels[active]))showStep(active+1);
                        }});
                        form.addEventListener('input',updateProgress);
                        form.addEventListener('change',updateProgress);
                        form.addEventListener('submit',event=>{{
                            for(let i=0;i<panels.length;i++){{
                                if(!validatePanel(panels[i])){{
                                    event.preventDefault();
                                    showStep(i);
                                    const invalid=firstInvalid(panels[i]);
                                    if(invalid)invalid.reportValidity();
                                    return;
                                }}
                            }}
                            submit.disabled=true;
                            submit.textContent='Wird sicher übermittelt …';
                        }});
                        showStep(0);
                    }})();
                    </script>
                </form>
                {draft_script}
                <div class="text-center"><a href="/" class="text-xs text-slate-400 hover:underline">← Zurück</a></div>
            </div>
        </main>
    </body></html>"""
    response = HTMLResponse(page)
    set_apply_session_cookie(response, request, applicant)  # sliding 3-hour session (E-12)
    return response


@app.get("/apply/login")
async def apply_login(request: Request, career: str = ""):
    if not pulse_public_site.public_access_enabled():
        raise HTTPException(status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"})
    if rate_limited(request_rate_key(request, "apply-oauth-login"), limit=15, window=600):
        return rate_limit_response("Zu viele Bewerbungs-Login-Versuche. Bitte in einer Minute erneut versuchen.")
    state = secrets.token_urlsafe(24)
    redirect_apply = oauth_redirect_uri(request, application=True)
    url = (f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
           f"&redirect_uri={quote(redirect_apply, safe='')}"
           f"&response_type=code&scope=identify&state={state}")
    response=RedirectResponse(url=url,status_code=303)
    response.set_cookie("apply_oauth_state",state,max_age=600,httponly=True,samesite="lax",secure=oauth_cookie_secure(request))
    if known_career(career):
        response.set_cookie(APPLY_RETURN_COOKIE,known_career(career),max_age=600,httponly=True,samesite="lax",secure=oauth_cookie_secure(request))
    return response


@app.get("/apply/callback")
async def apply_callback(request: Request, code: str=None, state: str=None, error: str=None, apply_oauth_state: str=Cookie(None)):
    if not pulse_public_site.public_access_enabled():
        raise HTTPException(status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"})
    if rate_limited(request_rate_key(request, "apply-oauth-callback"), limit=30, window=600):
        return rate_limit_response("Zu viele Bewerbungs-Anmeldeversuche. Bitte in einer Minute erneut versuchen.")
    retry='<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zur Bewerbung</a>'
    if error or not code or not state or not apply_oauth_state or not hmac.compare_digest(state,apply_oauth_state):
        return HTMLResponse(simple_page('❌','Bewerbungs-Login fehlgeschlagen','Bitte erneut versuchen.',retry),status_code=400)
    redirect_apply=oauth_redirect_uri(request, application=True)
    ud, problem = await discord_oauth_identify(code, redirect_apply)  # P-12
    if ud is None: return HTMLResponse(simple_page('❌','Bewerbungs-Login fehlgeschlagen',problem,retry),status_code=502)
    session={'id':str(ud.get('id')),'username':ud.get('username'),'global_name':ud.get('global_name') or ud.get('username')}
    response=RedirectResponse(apply_return_path(request),status_code=303); set_apply_session_cookie(response, request, session); response.delete_cookie('apply_oauth_state'); response.delete_cookie(APPLY_RETURN_COOKIE); return response


@app.post("/apply/keepalive")
async def apply_keepalive(request: Request, apply_session: str = Cookie(None)):
    """K-01: renews the 3-hour application session while the applicant is typing (sliding expiry)."""
    if not pulse_public_site.public_access_enabled():
        return JSONResponse({"ok": False, "disabled": True}, status_code=403)
    applicant = verify_session_token(apply_session, TOKEN_APPLY)
    if not applicant:
        return JSONResponse({"ok": False, "expired": True}, status_code=401)
    response = JSONResponse({"ok": True, "expires_in": APPLY_SESSION_SECONDS})
    set_apply_session_cookie(response, request, applicant)
    return response


# =============================================================
# ROUTE: BEWERBUNGEN ÜBERSICHT (offen + Verlauf)
# =============================================================
@app.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None)
    if not (
        ctx.perms.get("can_view_dashboard")
        or ctx.perms.get("can_manage_applications")
        or ctx.perms.get("can_promote")
        or ctx.perms.get("is_admin")
    ):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung für Bewerbungen.")
    uid = ctx.user["id"]
    show_history = request.query_params.get("view") == "history"
    can_review = can_review_applications(ctx)
    can_decide = can_review
    apps = load_json(APPS_FILE, {})

    active_application_statuses = {"pending", "interview"}
    items = [(k, v) for k, v in apps.items() if (v.get("status") not in active_application_statuses) == show_history]
    items.reverse()  # neueste zuerst

    apps_html = ""
    import pulse_application_duplicates as duplicate_detection
    duplicate_index = duplicate_detection.build_duplicate_index(apps) if can_review else {}
    import pulse_application_review as app_review
    review_leadership = bool(ctx.perms.get("can_promote") or ctx.perms.get("is_admin"))
    reviewer_candidates: list[tuple[str, str]] = []
    reviewer_load: dict = {}
    if can_review and review_leadership and not show_history:
        for member in list(getattr(ctx.guild, "members", []) or [])[:500]:
            if getattr(member, "bot", False):
                continue
            try:
                member_perms, _ = compute_perms(ctx.guild, member.id, load_config())
            except Exception:
                continue
            if member_perms.get("can_manage_applications") or member_perms.get("can_promote") or member_perms.get("is_admin"):
                reviewer_candidates.append((str(member.id), str(getattr(member, "display_name", member.id))[:80]))
        reviewer_load = app_review.workload(apps, [rid for rid, _ in reviewer_candidates])
    review_policy = app_review.normalize_policy(get_setting_safe(app_review.POLICY_KEY))
    interview_leads = (_interview_lead_candidates(ctx.guild)
                       if can_decide and not show_history and any(v.get("status") == "interview" for _, v in items) else [])
    for app_id, item in items:
        duplicate_notice = (
            duplicate_detection.render_duplicate_notice(
                apps, app_id, esc, duplicate_index=duplicate_index,
            )
            if can_review else ""
        )
        up, down = item.get("upvotes", []), item.get("downvotes", [])
        voted_up, voted_down = uid in up, uid in down
        career_summary = (
            f'<span class="rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 px-2.5 py-1">🧭 Laufbahn: {esc(item.get("career_label") or "Nicht angegeben")}</span>'
        )
        test_summary = ""
        test_review_html = ""
        if can_review and item.get("test_percent") is not None:
            test_passed = bool(item.get("test_passed"))
            test_badge_class = BADGE_OK if test_passed else BADGE_BAD
            test_status_label = "Richtwert erreicht" if test_passed else "Richtwert nicht erreicht"
            test_summary = (
                f'<span class="rounded-full border px-2.5 py-1 {test_badge_class}">'
                f'🧪 Online-Test: {int(item.get("test_score", 0))}/{int(item.get("test_total", 0))} '
                f'({int(item.get("test_percent", 0))} %) · {test_status_label}</span>'
            )
            detail_parts = []
            for index, detail in enumerate(item.get("test_details", []), start=1):
                correct_answer_html = ""
                if not detail.get("correct"):
                    correct_answer_html = (
                        f'<p class="text-emerald-600 dark:text-emerald-400">'
                        f'Richtige Antwort: {esc(detail.get("correct_answer", "—"))}</p>'
                    )
                detail_parts.append(
                    f'<div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 p-3 space-y-1">'
                    f'<p class="font-semibold">{index}. {esc(detail.get("question", ""))}</p>'
                    f'<p class="text-slate-600 dark:text-slate-300">Antwort: {esc(detail.get("selected_answer", "—"))} '
                    f'{"✅" if detail.get("correct") else "❌"}</p>'
                    f'{correct_answer_html}</div>'
                )
            if detail_parts:
                test_review_html = (
                    f'<details class="mt-3"><summary class="cursor-pointer text-xs font-semibold text-indigo-600 dark:text-indigo-300">'
                    f'Online-Testantworten anzeigen</summary><div class="grid gap-2 mt-3">{"".join(detail_parts)}</div></details>'
                )
        # #135: structured overview for reviewers only – facts and quotes, never a rating or recommendation.
        application_summary_html = pulse_summaries.application_summary_html(item) if can_review else ""
        practical_answers = item.get("practical_answers", []) if can_review else []
        practical_review_html = ""
        if practical_answers:
            practical_parts = []
            for practical_index, detail in enumerate(practical_answers, start=1):
                practical_parts.append(
                    '<div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 p-3 space-y-2">'
                    f'<p class="font-bold">{practical_index}. {esc(detail.get("title", "Praxisaufgabe"))}</p>'
                    f'<p class="text-slate-600 dark:text-slate-300">{esc(detail.get("prompt", ""))}</p>'
                    f'<div class="rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 p-3 whitespace-pre-wrap break-words">{esc(detail.get("answer", ""))}</div>'
                    f'<p class="text-[10px] text-amber-600 dark:text-amber-300">Prüfhilfe: {esc(detail.get("review_guide", ""))}</p>'
                    '</div>'
                )
            practical_review_html = (
                '<details class="mt-3"><summary class="cursor-pointer text-xs font-semibold text-amber-600 dark:text-amber-300">'
                'Praxisaufgaben prüfen · manuelle Bewertung erforderlich</summary><div class="grid gap-2 mt-3">'
                + "".join(practical_parts) + '</div></details>'
            )

        reviews = app_review.visible_reviews(item, uid, review_leadership) if can_review else {}
        own_review_done = str(uid) in app_review.completed_reviews(item)
        all_scores = [
            int(value)
            for review in reviews.values() if isinstance(review, dict)
            for value in (review.get("ratings", {}) or {}).values()
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5
        ]
        if all_scores:
            review_average = sum(all_scores) / len(all_scores)
            review_summary_html = (
                f'<span class="rounded-full border border-violet-500/25 bg-violet-500/10 px-2.5 py-1 text-violet-700 dark:text-violet-300">'
                f'★ Team-Score: {review_average:.1f}/5 · {len(reviews)} Review(s)</span>'
            )
        else:
            blind_note = ("Bewertungen anderer werden nach deiner eigenen Bewertung sichtbar"
                          if can_review and not own_review_done and app_review.completed_reviews(item) else "Noch keine strukturierte Team-Bewertung")
            review_summary_html = (
                '<span class="rounded-full border border-slate-200 dark:border-slate-700 px-2.5 py-1 text-slate-500">'
                + esc(blind_note) + '</span>'
            )
        assignment_html = ""
        if can_review and not show_history:
            progress = app_review.progress(item)
            names = ", ".join(esc(a["reviewer_name"] or a["reviewer_id"]) for a in app_review.assignments(item)) or "niemand zugewiesen"
            tally = ""
            if review_leadership and progress["tally"]:
                tally = " · Empfehlungen: " + ", ".join(f"{esc(app_review.RECOMMENDATIONS[k])} {v}" for k, v in sorted(progress["tally"].items()))
            gate = f' · Entscheidung ab {review_policy["min_reviews"]} Bewertungen' if review_policy["min_reviews"] else ""
            assignment_html = (f'<span class="rounded-full border border-sky-500/25 bg-sky-500/10 px-2.5 py-1 text-sky-700 dark:text-sky-300">'
                               f'👥 Prüfer: {names} · {progress["completed"]} Bewertung(en){tally}{gate}</span>')
            if review_leadership and reviewer_candidates:
                current = {a["reviewer_id"] for a in app_review.assignments(item)}
                suggestion = set(app_review.suggest(apps, [rid for rid, _ in reviewer_candidates], 2, exclude=[str(item.get("user_id"))]))
                options = "".join(
                    f'<option value="{esc(rid)}" {"selected" if rid in current else ""}>{esc(name)} · Last {int(reviewer_load.get(rid, 0))}'
                    f'{" · empfohlen" if rid in suggestion and not current else ""}</option>'
                    for rid, name in reviewer_candidates if rid != str(item.get("user_id")))
                assignment_html += (
                    '<details class="mt-2"><summary class="cursor-pointer text-[11px] font-semibold text-sky-600">Prüfer zuweisen</summary>'
                    '<form action="/applications/assign" method="post" class="mt-2 flex flex-wrap gap-2 items-end">'
                    f'<input type="hidden" name="app_id" value="{esc(app_id)}">'
                    f'<select name="reviewer_ids" multiple size="5" required aria-label="Prüfer" class="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-2 text-xs">{options}</select>'
                    f'<button class="rounded-xl bg-sky-600 px-3 py-2 text-xs font-semibold text-white">Zuweisen (max. {app_review.MAX_ASSIGNED})</button></form></details>')
        review_summary_html += assignment_html

        review_form_html = ""
        if can_decide and not show_history and item.get("status") in {"pending", "interview"}:
            my_review = reviews.get(str(uid), {})
            if not isinstance(my_review, dict):
                my_review = {}
            my_ratings = my_review.get("ratings", {})
            if not isinstance(my_ratings, dict):
                my_ratings = {}
            rating_controls = []
            for criterion_key, criterion_label in APPLICATION_REVIEW_CRITERIA:
                selected_value = my_ratings.get(criterion_key)
                choices = '<option value="">Wert wählen …</option>' + "".join(
                    f'<option value="{score}" {"selected" if selected_value == score else ""}>{score} / 5'
                    f' · {("Unter Erwartung", "Ausbaufähig", "Solide", "Stark", "Hervorragend")[score - 1]}</option>'
                    for score in range(1, 6)
                )
                rating_controls.append(
                    f'<label class="block text-[11px] font-semibold">{esc(criterion_label)}'
                    f'<select name="rating_{esc(criterion_key)}" required class="mt-1 w-full rounded-xl border border-slate-200 '
                    f'dark:border-slate-700 bg-white dark:bg-slate-900 p-2 text-xs">{choices}</select></label>'
                )
            review_notes = esc(str(my_review.get("notes") or "")[:1000])
            review_updated = esc(str(my_review.get("updated_at") or "Noch nicht bewertet"))
            review_form_html = (
                '<details class="mt-3 rounded-2xl border border-violet-500/20 p-4">'
                '<summary class="cursor-pointer text-xs font-bold text-violet-600 dark:text-violet-300">Bewertungsmatrix öffnen · 5 Kompetenzen</summary>'
                '<div class="mt-3 space-y-3"><p class="text-[10px] text-slate-500">Bewerte anhand der Antworten und belegbarer Beobachtungen. 1 = Unter Erwartung, 5 = Hervorragend. Deine letzte Bewertung: '
                + review_updated
                + '</p><form action="/applications/review" method="post" class="space-y-3">'
                f'<input type="hidden" name="app_id" value="{esc(app_id)}">'
                f'<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">{"".join(rating_controls)}</div>'
                '<label class="block text-[11px] font-semibold">Deine Empfehlung (optional)'
                '<select name="recommendation" class="mt-1 w-full rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-2 text-xs">'
                '<option value="">Keine Angabe</option>'
                + "".join(f'<option value="{k}" {"selected" if my_review.get("recommendation") == k else ""}>{esc(v)}</option>' for k, v in app_review.RECOMMENDATIONS.items())
                + '</select></label>'
                '<label class="block text-[11px] font-semibold">Interne Beobachtungen (optional)'
                f'<textarea name="review_notes" maxlength="1000" class="mt-1 w-full min-h-24 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-3 text-xs" placeholder="Stärken, offene Punkte, konkrete Beispiele …">{review_notes}</textarea></label>'
                '<button class="rounded-xl bg-violet-600 hover:bg-violet-500 px-4 py-2.5 text-xs font-semibold text-white">Bewertung für mich speichern</button>'
                '</form></div></details>'
            )
        if show_history:
            hired = item.get("status") in {"hired", "accepted"}  # accepted = Altbestand aus früherer Version
            history_label = "✅ Eingestellt" if hired else "❌ Abgelehnt"
            footer = f'<span class="text-xs font-semibold border px-3 py-1 rounded-full {BADGE_OK if hired else BADGE_BAD}">{history_label}{(" von " + esc(item.get("decided_by"))) if item.get("decided_by") else ""}</span>'
        elif item.get("status") == "interview":
            interview_form = f"""
                <form action="/action" method="post" class="flex flex-wrap gap-2 items-center" onsubmit="return confirm('Wurde das verpflichtende Bewerbungsgespräch durchgeführt?');">
                    <input type="hidden" name="action" value="decide_app"><input type="hidden" name="app_id" value="{esc(app_id)}">
                    <select name="hire_role_id" class="{INPUT} py-1.5 text-[11px] w-auto min-w-44" required>
                        <option value="">Team-Einstiegsrolle nach Gespräch wählen…</option>
                        {''.join(f'<option value="{r.id}">{esc(r.name)}</option>' for r in sorted((ctx.guild.get_role(x) for x in ctx.config.get("team_role_ids", []) if str(x) != str(APPLICATION_ROLE_ID)), key=lambda z: z.position if z else -1) if r and r.id != APPLICATION_ROLE_ID)}
                    </select>
                    <input type="text" name="action_reason" maxlength="500" placeholder="Gesprächsnotiz / Entscheidungsgrund..." required class="{INPUT} py-1.5 min-w-56">
                    <button name="decision" value="hire_after_interview" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">✅ Gespräch geführt – einstellen</button>
                    <button name="decision" value="reject_after_interview" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">Nach Gespräch ablehnen</button>
                </form>"""
            footer = f'<div class="w-full space-y-3"><span class="inline-flex text-xs font-semibold border px-3 py-1 rounded-full border-amber-500/30 text-amber-600">🎙️ Bewerbungsgespräch erforderlich · noch keine Einstellung</span>{interview_slot_html(app_id, item, interview_leads) if can_decide else ""}{interview_form if can_decide else ""}</div>'
        else:
            decide = f"""
                <form action="/action" method="post" class="flex flex-wrap gap-2 items-center" onsubmit="return confirm('Bewerber zum verpflichtenden Bewerbungsgespräch einladen?');">
                    <input type="hidden" name="action" value="decide_app"><input type="hidden" name="app_id" value="{esc(app_id)}">
                    <input type="text" name="action_reason" maxlength="500" placeholder="Grund für Einladung oder Ablehnung..." required class="{INPUT} py-1.5 min-w-56">
                    <button name="decision" value="accept" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">🎙️ Zum Bewerbungsgespräch einladen</button>
                    <button name="decision" value="reject" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">Ablehnen</button>
                </form>""" if can_decide else ""
            footer = f"""
                <form action="/action" method="post" class="flex gap-2">
                    <input type="hidden" name="action" value="vote_app"><input type="hidden" name="app_id" value="{esc(app_id)}">
                    <button name="vote" value="up" class="{'bg-emerald-600 text-white' if voted_up else 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700'} text-xs px-3 py-1.5 rounded-xl transition shadow-sm font-medium">👍 Dafür</button>
                    <button name="vote" value="down" class="{'bg-rose-600 text-white' if voted_down else 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700'} text-xs px-3 py-1.5 rounded-xl transition shadow-sm font-medium">👎 Dagegen</button>
                </form>{decide}"""
        apps_html += f"""
        <div id="app-{esc(app_id)}" class="{CARD} p-5 space-y-3.5">
            <div class="flex justify-between items-center">
                <div>
                    <h3 class="font-bold text-slate-900 dark:text-white text-sm">{esc(item.get('name'))}</h3>
                    <span class="text-[10px] text-slate-400 font-mono">{'ID: ' + esc(item.get('user_id')) + ' · ' if can_review else ''}{esc(item.get('created_at'))}</span>
                    <div class="flex flex-wrap gap-2 mt-2 text-[10px]">{career_summary}{test_summary}</div>
                </div>
                <div class="flex items-center gap-2 text-xs font-semibold">
                    <span class="text-emerald-600 dark:text-emerald-400">👍 {len(up)}</span>
                    <span class="text-rose-600 dark:text-rose-400">👎 {len(down)}</span>
                </div>
            </div>
            {duplicate_notice}
            {f'<p class="text-xs text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800/80 whitespace-pre-wrap break-words">{esc(item.get("text"))}</p>' if can_review else '<p class="text-xs text-slate-500">Motivation, Testantworten und interne Bewertungen sind nur für Recruiting-Berechtigte sichtbar.</p>'}
            {application_summary_html}
            {review_summary_html}
            {test_review_html}
            {practical_review_html}
            {review_form_html}
            <div class="flex flex-wrap justify-between items-center gap-2 pt-2 border-t border-slate-100 dark:border-slate-800/80">{footer}</div>
        </div>"""

    def tab(label, href, active):
        cls = "bg-indigo-600 text-white" if active else "bg-white dark:bg-[#141824] text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-700"
        return f'<a href="{href}" class="px-3.5 py-1.5 rounded-xl text-xs font-semibold {cls}">{label}</a>'

    empty = "Noch keine entschiedenen Bewerbungen." if show_history else "Keine offenen Bewerbungen vorhanden."
    module_settings_link = (
        '<a href="/applications/statistik" class="px-3.5 py-1.5 rounded-xl text-xs font-semibold border border-slate-200 dark:border-slate-700">📊 Statistik</a> <a href="/applications/config" class="px-3.5 py-1.5 rounded-xl text-xs font-semibold bg-indigo-600 text-white">⚙️ Module verwalten</a>'
        if can_decide else ""
    )
    body = f"""
    <div class="flex flex-wrap items-center justify-between gap-3 mb-6 max-w-3xl">
        <h1 class="text-2xl font-bold text-slate-900 dark:text-white">{'Bewerbungs-Verlauf' if show_history else 'Offene Bewerbungen'}</h1>
        <div class="flex flex-wrap gap-2">{module_settings_link}{tab('Offen', '/applications', not show_history)}{tab('Verlauf', '/applications?view=history', show_history)}</div>
    </div>
    <div class="max-w-3xl">{__import__("pulse_views").bar("/applications", ctx.user["id"], request.query_params.multi_items())}</div>
    <div class="space-y-4 max-w-3xl">{apps_html or f"<p class='text-xs text-slate-400 italic {CARD} p-6'>{empty}</p>"}</div>"""
    return render_page("Bewerbungen", ctx, "apps", body)



# BOT-10: UTC for calendar entries and reminder checks (webserver imports no 'timezone' name)
_UTC = __import__("datetime").timezone.utc


def interview_slot_html(app_id: str, item: dict, leads: list | None = None) -> str:
    """BOT-10/#99: set, move or cancel the interview (start, duration, responsible leader, participants overview)."""
    import pulse_interviews as interviews
    tz = now_de().tzinfo
    scheduled = interviews.is_scheduled(item)
    span = interviews.window(item) if scheduled else None
    shown = span[0].astimezone(tz).strftime("%d.%m.%Y %H:%M") if span else ""
    value = span[0].astimezone(tz).strftime("%Y-%m-%dT%H:%M") if span else ""
    minutes = int((span[1] - span[0]).total_seconds() // 60) if span else interviews.DEFAULT_MINUTES
    options = sorted(set(interviews.DURATIONS) | {minutes})
    duration_html = "".join(f'<option value="{m}"{" selected" if m == minutes else ""}>{m} Min.</option>' for m in options)
    current_lead = str(item.get("interview_lead_id") or "")
    lead_list = list(leads or [])
    if current_lead and current_lead not in {rid for rid, _ in lead_list}:
        lead_list.append((current_lead, str(item.get("interview_lead_name") or current_lead)))
    lead_html = '<option value="">– keine –</option>' + "".join(
        f'<option value="{esc(rid)}"{" selected" if rid == current_lead else ""}>{esc(name)}</option>' for rid, name in lead_list
        if rid != str(item.get("user_id")))
    revision = interviews.revision(item)
    people = interviews.participants(item) if scheduled else []
    roles = interviews.ROLE_LABELS
    people_html = ""
    if people:
        people_html = ('<ul class="text-[11px] text-slate-500 flex flex-wrap gap-x-3 gap-y-1" aria-label="Gesprächsteilnehmer">'
                       + "".join(f'<li>{esc(p["name"] or "Unbekannt")} <span class="text-slate-400">({esc(", ".join(roles[r] for r in p["roles"]))})</span></li>' for p in people)
                       + "</ul>")
    notes = ""
    delivery = item.get("interview_delivery") if isinstance(item.get("interview_delivery"), dict) else {}
    for key, label in (("applicant_notice", "Terminnachricht"), ("applicant_reminder", "Erinnerung")):
        if isinstance(delivery.get(key), dict) and not delivery[key].get("ok"):
            notes += f'<span class="text-[11px] text-amber-600" role="status">⚠️ {label} per DM nicht zustellbar</span> '
    if item.get("interview_state") == "cancelled" and not scheduled:
        notes += f'<span class="text-[11px] text-rose-600">Letztes Gespräch abgesagt{(": " + esc(item.get("interview_cancel_reason"))) if item.get("interview_cancel_reason") else ""}</span>'
    cancel_html = ""
    if scheduled:
        cancel_html = (f'<form action="/applications/interview-cancel" method="post" class="flex flex-wrap gap-2 items-center" data-interview-cancel onsubmit="return confirm(\'Gesprächstermin wirklich absagen? Der Bewerber wird benachrichtigt.\');">'
                       f'<input type="hidden" name="app_id" value="{esc(app_id)}"><input type="hidden" name="expected_revision" value="{revision}">'
                       f'<input name="reason" maxlength="200" placeholder="Grund (optional)" aria-label="Grund der Absage" class="{INPUT} py-1.5 w-auto text-xs">'
                       f'<button class="text-xs px-3.5 py-1.5 rounded-xl border border-rose-500/30 text-rose-600 font-semibold">Gespräch absagen</button></form>')
    return (f'<div class="space-y-2" data-interview-box><form action="/applications/interview-slot" method="post" class="flex flex-wrap gap-2 items-end">'
            f'<input type="hidden" name="app_id" value="{esc(app_id)}"><input type="hidden" name="expected_revision" value="{revision}">'
            f'<label class="text-[11px] font-semibold">🗓️ Gesprächstermin <input type="datetime-local" name="interview_at" value="{esc(value)}" required class="{INPUT} py-1.5 w-auto"></label>'
            f'<label class="text-[11px] font-semibold">Dauer <select name="duration" class="{INPUT} py-1.5 w-auto">{duration_html}</select></label>'
            f'<label class="text-[11px] font-semibold">Führungskraft <select name="lead_id" class="{INPUT} py-1.5 w-auto">{lead_html}</select></label>'
            f'<button class="text-xs px-3.5 py-1.5 rounded-xl border border-indigo-500/30 text-indigo-600 font-semibold">{"Termin ändern" if shown else "Termin festlegen"}</button>'
            f'{f"<span class=\"text-[11px] text-slate-400\">aktuell: {esc(shown)} · {minutes} Min.</span>" if shown else ""}</form>'
            f'{people_html}{cancel_html}{f"<div>{notes}</div>" if notes else ""}</div>')


def _template_history_html(key: str) -> str:
    """#103: earlier versions of one template with a restore button each (the section itself is admin-only)."""
    import pulse_app_comms
    rows = pulse_app_comms.history(key)[:5]
    if not rows:
        return ""
    items = "".join(
        f'<li class="flex flex-wrap items-center gap-2"><span>v{r["version"]} · {esc(r["at"][:16].replace("T", " "))} · {esc(r["actor"] or "–")}</span>'
        f'<button formaction="/applications/templates/restore" formmethod="post" name="restore" value="{esc(key)}:{r["version"]}" '
        f'class="underline text-indigo-500" formnovalidate>wiederherstellen</button></li>' for r in rows)
    return f'<details class="text-[11px] text-slate-400"><summary>Frühere Versionen</summary><ul class="mt-1 space-y-1">{items}</ul></details>'


def application_templates_section(ctx) -> str:
    """BOT-10: edit the DM texts (admins); empty field = built-in text."""
    import pulse_app_comms
    if not ctx.perms.get("is_admin"):
        return ""
    current = pulse_app_comms.load_for_guild(getattr(getattr(ctx, "guild", None), "id", None), ctx.perms)
    fields = "".join(
        f'<label class="grid gap-1 text-xs font-semibold">{esc(label)}'
        f'<textarea name="tpl_{key}" rows="2" class="{INPUT} text-xs">{esc(current[key])}</textarea></label>'
        + _template_history_html(key)
        for key, label in pulse_app_comms.LABELS.items())
    return (f'<form method="post" action="/applications/templates" class="{CARD} p-5 mt-5 space-y-3">'
            f'<h2 class="text-sm font-bold">✉️ Nachrichten an Bewerber</h2>'
            f'<p class="text-xs text-slate-400">Platzhalter: {esc(pulse_app_comms.PLACEHOLDERS)}. Ein geleertes Feld nutzt wieder den Standardtext.</p>'
            f'{fields}<button class="{BTN} px-4 py-2 text-xs">Vorlagen speichern</button></form>')


def _interview_staff(ctx) -> bool:
    return bool(ctx.perms.get("can_manage_applications") or ctx.perms.get("can_promote") or ctx.perms.get("is_admin"))


def _interview_lead_candidates(guild) -> list[tuple[str, str]]:
    """Members who may be the responsible leader of an interview: leadership (promote right) or admins."""
    found = []
    config = load_config()
    for member in list(getattr(guild, "members", []) or [])[:500]:
        if getattr(member, "bot", False):
            continue
        try:
            member_perms, _ = compute_perms(guild, member.id, config)
        except Exception:
            continue
        if member_perms.get("can_promote") or member_perms.get("is_admin"):
            found.append((str(member.id), str(getattr(member, "display_name", member.id))[:80]))
    return found


def _interview_notify_team(app_id: str, ids, title: str, text: str, key: str, ttl: int = 86400) -> None:
    """Dashboard notification (personal preferences apply inside pulse_db.notify); one try, no retry loop."""
    for person_id in ids:
        try:
            pulse_db.notify(person_id, title, text, "info", "/calendar", f"interview:{app_id}:{key}:{person_id}", ttl)
        except Exception as exc:
            print(f"⚠️ Interview-Benachrichtigung fehlgeschlagen: {type(exc).__name__}")


async def _interview_dm(request: Request, user_id, template: str, **values) -> bool:
    """One DM attempt to the applicant. The appointment is already saved, so no failure here may turn into an error page."""
    try:
        return bool(await dm_applicant(request, user_id, template, **values))
    except Exception as exc:
        print(f"⚠️ Interview-DM fehlgeschlagen: {type(exc).__name__}")
        return False


def _interview_local(moment) -> str:
    return moment.astimezone(now_de().tzinfo).strftime("%d.%m.%Y %H:%M")


def _interview_record_delivery(app_id: str, revision: int, kind: str, ok: bool) -> None:
    import pulse_interviews as interviews
    fresh = load_json(APPS_FILE, {})  # no await between this load and the save
    if isinstance(fresh.get(app_id), dict) and interviews.revision(fresh[app_id]) == revision:
        interviews.record_delivery(fresh[app_id], kind, ok)
        save_json(APPS_FILE, fresh)


@app.post("/applications/interview-slot")
async def application_interview_slot(request: Request, user_session: str = Cookie(None)):
    """#99: create or change an interview (start, duration, responsible leader) with conflict check and calendar entry."""
    import pulse_app_comms
    import pulse_interviews as interviews
    ctx = auth(request, user_session, perm=None)
    if not _interview_staff(ctx):
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung für Bewerbungen.")
    form = await request.form()
    app_id = str(form.get("app_id") or "")
    target = f"/applications#app-{app_id}"
    try:
        when = pulse_app_comms.parse_slot(str(form.get("interview_at") or ""), now_de().tzinfo)
    except ValueError as exc:
        return back("/applications", str(exc), False)
    apps = load_json(APPS_FILE, {})
    item = apps.get(app_id) if isinstance(apps, dict) else None
    if not isinstance(item, dict) or (item.get("guild_id") not in (None, "") and str(item.get("guild_id")) != str(ctx.guild.id)):
        return back("/applications", "Bewerbung wurde nicht gefunden.", False)
    current_window = interviews.window(item) if interviews.is_scheduled(item) else None
    minutes = form.get("duration") or (int((current_window[1] - current_window[0]).total_seconds() // 60) if current_window else interviews.DEFAULT_MINUTES)
    lead_id, lead_name = str(item.get("interview_lead_id") or ""), str(item.get("interview_lead_name") or "")
    if "lead_id" in form:
        lead_id, lead_name = str(form.get("lead_id") or "").strip(), ""
        if lead_id:
            member = ctx.guild.get_member(int(lead_id)) if lead_id.isdigit() else None
            lead_perms = compute_perms(ctx.guild, member.id, load_config())[0] if member is not None and not getattr(member, "bot", False) else {}
            if not (lead_perms.get("can_promote") or lead_perms.get("is_admin")):
                return back(target, "Die gewählte Führungskraft ist nicht auf dem Server oder hat keine Leitungsrechte.", False)
            lead_name = str(member.display_name)[:80]
    actor = str(ctx.user.get("global_name") or ctx.user.get("username") or "Team")
    try:
        result = interviews.schedule(
            apps, app_id, start=when, minutes=minutes, lead_id=lead_id, lead_name=lead_name, actor_id=str(ctx.user["id"]), actor_name=actor,
            save=lambda data: save_json(APPS_FILE, data), db=pulse_db, expected_revision=form.get("expected_revision"),
            load=lambda: load_json(APPS_FILE, {}))
    except interviews.InterviewConflict as exc:
        log_audit(actor, ctx.user["id"], "Bewerbungsgespräch: Terminkonflikt abgelehnt", f"{app_id} · {len(exc.conflicts)} Konflikt(e)")
        return back(target, interviews.describe_conflicts(exc.conflicts, now_de().tzinfo), False)
    except interviews.InterviewError as exc:
        return back(target, str(exc), False)
    termin = _interview_local(when)
    if result["action"] == "unchanged":
        return back(target, "Der Termin ist unverändert.", True)
    moved = result["action"] == "moved"
    item = result["item"]
    revision = interviews.revision(item)
    _interview_notify_team(app_id, result["notify"], "🎙️ Bewerbungsgespräch verschoben" if moved else "🎙️ Bewerbungsgespräch geplant",
                           f"{item.get('name') or app_id} am {termin}", f"slot{revision}")
    sent = await _interview_dm(request, item.get("user_id"), "rescheduled" if moved else "scheduled", id=app_id, termin=termin,
                               actor=(str(ctx.user["id"]), actor))
    _interview_record_delivery(app_id, revision, "applicant_notice", sent)
    length = int((result["end"] - result["start"].astimezone(_UTC)).total_seconds() // 60)
    log_audit(actor, ctx.user["id"], "Bewerbungsgespräch verschoben" if moved else "Bewerbungsgespräch geplant",
              f"{app_id} · {termin} · {length} Min." + (f" · vorher {_interview_local(result['old_start'])}" if moved and result.get("old_start") else ""))
    return back(target, f"Gesprächstermin {termin} gespeichert" + ("" if sent else " – DM an den Bewerber nicht zustellbar") + ".", sent)


@app.post("/applications/interview-cancel")
async def application_interview_cancel(request: Request, user_session: str = Cookie(None)):
    """#99: cancel an interview – calendar entry removed, no further reminders, applicant informed once."""
    import pulse_interviews as interviews
    ctx = auth(request, user_session, perm=None)
    if not _interview_staff(ctx):
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung für Bewerbungen.")
    form = await request.form()
    app_id = str(form.get("app_id") or "")
    target = f"/applications#app-{app_id}"
    apps = load_json(APPS_FILE, {})
    item = apps.get(app_id) if isinstance(apps, dict) else None
    if not isinstance(item, dict) or (item.get("guild_id") not in (None, "") and str(item.get("guild_id")) != str(ctx.guild.id)):
        return back("/applications", "Bewerbung wurde nicht gefunden.", False)
    actor = str(ctx.user.get("global_name") or ctx.user.get("username") or "Team")
    try:
        result = interviews.cancel(apps, app_id, actor_id=str(ctx.user["id"]), actor_name=actor, save=lambda data: save_json(APPS_FILE, data),
                                   db=pulse_db, reason=str(form.get("reason") or ""), expected_revision=form.get("expected_revision"),
                                   load=lambda: load_json(APPS_FILE, {}))
    except interviews.InterviewError as exc:
        return back(target, str(exc), False)
    termin = _interview_local(result["old_start"]) if result.get("old_start") else ""
    item = result["item"]
    revision = interviews.revision(item)
    _interview_notify_team(app_id, result["notify"], "🎙️ Bewerbungsgespräch abgesagt", f"{item.get('name') or app_id} ({termin})", f"cancel{revision}")
    sent = await _interview_dm(request, item.get("user_id"), "cancelled", id=app_id, actor=(str(ctx.user["id"]), actor))
    _interview_record_delivery(app_id, revision, "applicant_notice", sent)
    log_audit(actor, ctx.user["id"], "Bewerbungsgespräch abgesagt", f"{app_id} · {termin}")
    return back(target, "Gesprächstermin abgesagt." + ("" if sent else " – DM an den Bewerber nicht zustellbar."), sent)


@app.post("/applications/templates")
async def application_templates_save(request: Request, user_session: str = Cookie(None)):
    import pulse_app_comms
    ctx = auth(request, user_session, perm=None, admin=True)
    form = await request.form()
    actor_name = str(ctx.user.get("global_name") or "")
    pulse_app_comms.save({key: form.get(f"tpl_{key}", "") for key in pulse_app_comms.DEFAULTS}, actor=actor_name)
    try:
        pulse_app_comms.sync_library(str(ctx.guild.id), ctx.perms, str(ctx.user["id"]), actor_name)
    except Exception as exc:  # noqa: BLE001 - the legacy editor save remains successful if library sync is unavailable
        print(f"⚠️ Bewerber-Vorlagen konnten nicht in die zentrale Bibliothek gespiegelt werden: {type(exc).__name__}")
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Bewerber-Nachrichten geändert", "Vorlagen")
    return back("/applications/config", "Vorlagen gespeichert.")


@app.post("/applications/templates/restore")
async def application_templates_restore(request: Request, user_session: str = Cookie(None)):
    """#103: restore an earlier version of one applicant message (admins only)."""
    import pulse_app_comms
    ctx = auth(request, user_session, perm=None, admin=True)
    form = await request.form()
    key, _, number = str(form.get("restore") or "").partition(":")
    try:
        restored_text = pulse_app_comms.restore(key, int(number or 0), actor=str(ctx.user.get("global_name") or ""))
    except (ValueError, TypeError):
        return back("/applications/config", "Diese Vorlagenversion gibt es nicht.", False)
    try:
        # Keep central edits to other applicant-message scopes intact when one legacy version is restored.
        current_values = pulse_app_comms.load_for_guild(str(ctx.guild.id), ctx.perms)
        current_values[key] = restored_text
        pulse_app_comms.sync_library(str(ctx.guild.id), ctx.perms, str(ctx.user["id"]),
                                     str(ctx.user.get("global_name") or ""), values=current_values)
    except Exception as exc:  # noqa: BLE001 - restore is already stored in the legacy version history
        print(f"⚠️ Wiederhergestellte Bewerber-Vorlage konnte nicht gespiegelt werden: {type(exc).__name__}")
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Bewerber-Nachricht wiederhergestellt", f"{key} v{number}")
    return back("/applications/config", "Vorlagenversion wiederhergestellt.")


def send_stale_application_reminders() -> int:
    """#101: dashboard reminder (once per day and person, via the notification dedupe) to the assigned reviewers
    of applications that wait too long. Internal notifications only - no Discord message, no retry loop."""
    import pulse_recruiting_stats as rs
    apps = load_json(APPS_FILE, {})
    sent = 0
    for row in rs.stale_applications(apps if isinstance(apps, dict) else {}, now_de().replace(tzinfo=None)):
        label = "wartet auf Prüfung" if row["status"] == "pending" else "wartet auf Entscheidung nach Gespräch"
        for person in row["reviewers"]:
            try:
                pulse_db.notify(person, "📝 Bewerbung überfällig", f"{row['name']} {label} (seit {row['days']} Tagen).",
                                "warning", "/applications", f"app-stale:{row['id']}:{person}", 86400)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ Bewerbungs-Erinnerung fehlgeschlagen: {type(exc).__name__}")
    return sent


async def send_interview_reminders(bot) -> int:
    """BOT-10/#99: called by the existing automation loop. Reminder ≤ 1 h before (applicant DM + team), team heads-up
    ≤ 24 h before. Each is claimed in the record *before* sending, so a slow or failing DM is never repeated."""
    import pulse_app_comms
    import pulse_interviews as interviews
    guild = bot.get_guild(GUILD_ID) if bot else None
    now_utc = datetime.now(_UTC)
    try:  # appointments of applications decided in the meantime, calendar rows without an application
        interviews.reconcile(load_json(APPS_FILE, {}), save=lambda data: save_json(APPS_FILE, data), db=pulse_db, now=now_utc)
    except Exception as exc:
        print(f"⚠️ Interview-Kalenderabgleich fehlgeschlagen: {type(exc).__name__}")
    sent_reminders = 0
    for app_id, kind in interviews.due_notices(load_json(APPS_FILE, {}), now_utc):
        apps = load_json(APPS_FILE, {})
        item = apps.get(app_id)
        if not isinstance(item, dict) or (app_id, kind) not in interviews.due_notices({app_id: item}, now_utc):
            continue
        item["interview_heads_up"] = True
        if kind == "reminder":
            item["interview_reminded"] = True
        save_json(APPS_FILE, apps)  # claim first: no double sending, no retry storm
        span = interviews.window(item)
        local = span[0].astimezone(now_de().tzinfo) if span else None
        who = item.get("name") or app_id
        revision = interviews.revision(item)
        if kind == "heads_up":
            _interview_notify_team(app_id, interviews.team_ids(item), "🎙️ Bewerbungsgespräch bevorstehend",
                                   f"{who} am {local.strftime('%d.%m. %H:%M') if local else ''}", f"headsup{revision}")
            continue
        termin = local.strftime("%H:%M") if local else ""
        import pulse_app_journal
        ok, reason = False, "dm_not_delivered"
        resolved = pulse_app_comms.resolve_for_guild("reminder", getattr(guild, "id", None),
                                                     server=getattr(guild, "name", "dem Server"), id=app_id, termin=termin)
        text = resolved["text"]
        try:
            user = (guild.get_member(int(item.get("user_id"))) if guild else None) or await bot.fetch_user(int(item.get("user_id")))
            ok = bool(await send_dm_notification(user, text))
        except (discord.HTTPException, TypeError, ValueError, AttributeError):
            ok, reason = False, "user_not_found"  # recorded below; never retried
        pulse_app_journal.record_safe(app_id, "reminder", "sent" if ok else "failed", text, detail="" if ok else reason,
                                      actor_id="0", actor_name="System", template_ref=resolved["template_ref"])
        _interview_notify_team(app_id, interviews.team_ids(item), "🎙️ Bewerbungsgespräch gleich", f"{who} um {termin}", f"remind{revision}", 7200)
        _interview_record_delivery(app_id, revision, "applicant_reminder", ok)
        sent_reminders += 1
    return sent_reminders


@app.post("/applications/review")
async def save_application_review(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None)
    can_manage = bool(
        ctx.perms.get("can_manage_applications")
        or ctx.perms.get("can_promote")
        or ctx.perms.get("is_admin")
    )
    if not can_manage:
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung, Bewerbungen zu bewerten.")

    form = await request.form()
    app_id = str(form.get("app_id") or "").strip()
    apps = load_json(APPS_FILE, {})
    item = apps.get(app_id) if isinstance(apps, dict) else None
    if not isinstance(item, dict):
        return back("/applications", "Bewerbung wurde nicht gefunden.", False)
    if item.get("status") not in {"pending", "interview"}:
        return back("/applications", "Abgeschlossene Bewerbungen können nicht mehr bewertet werden.", False)

    ratings = {}
    for criterion_key, criterion_label in APPLICATION_REVIEW_CRITERIA:
        try:
            score = int(str(form.get(f"rating_{criterion_key}") or "").strip())
        except (TypeError, ValueError):
            return back("/applications", f"Bitte bewerte „{criterion_label}“.", False)
        if score not in range(1, 6):
            return back("/applications", f"Ungültiger Wert für „{criterion_label}“.", False)
        ratings[criterion_key] = score

    reviewer_id = str(ctx.user.get("id") or "")
    if not reviewer_id:
        raise HTTPException(status_code=403, detail="Die Reviewer-ID konnte nicht ermittelt werden.")
    reviewer_name = str(ctx.user.get("global_name") or ctx.user.get("username") or "Teammitglied")[:80]
    notes = str(form.get("review_notes") or "").strip()[:1000]
    reviews = item.setdefault("reviews", {})
    if not isinstance(reviews, dict):
        reviews = {}
        item["reviews"] = reviews
    reviews[reviewer_id] = {
        "reviewer_id": reviewer_id,
        "reviewer_name": reviewer_name,
        "ratings": ratings,
        "notes": notes,
        "updated_at": now_de().strftime("%d.%m.%Y %H:%M"),
    }
    import pulse_application_review as app_review
    recommendation = app_review.normalize_recommendation(form.get("recommendation"))
    if recommendation:
        reviews[reviewer_id]["recommendation"] = recommendation
    save_json(APPS_FILE, apps)
    average = sum(ratings.values()) / len(ratings)
    log_audit(
        reviewer_name,
        reviewer_id,
        "Bewerbung bewertet",
        f"{item.get('name', 'Unbekannt')} · {item.get('career_label', 'Laufbahn unbekannt')} · eigener Score {average:.1f}/5",
    )
    return back("/applications", f"Bewertung gespeichert: {average:.1f}/5. Deine Bewertung bleibt separat von der anderer Reviewer.")



@app.post("/applications/assign")
async def assign_application_reviewers(request: Request, user_session: str = Cookie(None)):
    """Audit #96: leadership assigns up to MAX_ASSIGNED reviewers; existing reviews stay untouched."""
    import pulse_application_review as app_review
    ctx = auth(request, user_session, perm=None)
    if not (ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Prüfer zuweisen darf nur die Leitung.")
    form = await request.form()
    app_id = str(form.get("app_id") or "").strip()
    import pulse_board
    app_return = pulse_board.safe_return(form.get("return_to"))
    apps = load_json(APPS_FILE, {})
    item = apps.get(app_id) if isinstance(apps, dict) else None
    if not isinstance(item, dict) or (item.get("guild_id") not in (None, "") and str(item.get("guild_id")) != str(ctx.guild.id)):
        return back(app_return, "Bewerbung wurde nicht gefunden.", False)
    stale_message = pulse_board.stale_reason(item, form.get("expected_status"))
    if stale_message:
        return back(app_return, stale_message, False)
    if item.get("status") not in app_review.OPEN_STATUSES:
        return back(app_return, "Abgeschlossenen Bewerbungen können keine Prüfer mehr zugewiesen werden.", False)
    chosen = []
    for raw_id in form.getlist("reviewer_ids")[: app_review.MAX_ASSIGNED + 1]:
        member = ctx.guild.get_member(int(raw_id)) if str(raw_id).isdigit() else None
        if member is None or getattr(member, "bot", False):
            return back(app_return, "Eine ausgewählte Person ist nicht auf dem Server.", False)
        member_perms, _ = compute_perms(ctx.guild, member.id, load_config())
        if not (member_perms.get("can_manage_applications") or member_perms.get("can_promote") or member_perms.get("is_admin")):
            return back(app_return, f"{member.display_name} darf keine Bewerbungen prüfen.", False)
        if str(member.id) == str(item.get("user_id")):
            return back(app_return, "Bewerber dürfen ihre eigene Bewerbung nicht prüfen.", False)
        chosen.append((str(member.id), member.display_name))
    actor_name = str(ctx.user.get("global_name") or ctx.user.get("username") or "Leitung")[:80]
    try:
        assigned = app_review.assign(item, chosen, actor_name, now_de().strftime("%d.%m.%Y %H:%M"))
    except ValueError as exc:
        return back(app_return, str(exc), False)
    save_json(APPS_FILE, apps)
    for entry in assigned:
        try:
            pulse_db.notify(entry["reviewer_id"], "📝 Bewerbung zugewiesen", f"{item.get('name', 'Bewerbung')} wartet auf deine Bewertung.",
                            "info", "/applications", f"app-assign:{app_id}:{entry['reviewer_id']}", 86400)
        except Exception as exc:
            print(f"⚠️ Zuweisungs-Benachrichtigung fehlgeschlagen: {type(exc).__name__}")
    log_audit(actor_name, ctx.user["id"], "Bewerbung zugewiesen", f"{item.get('name', 'Unbekannt')} → {', '.join(n for _, n in chosen)}")
    return back(app_return, f"{len(assigned)} Prüfer zugewiesen.")


@app.post("/applications/review-policy")
async def save_application_review_policy(request: Request, user_session: str = Cookie(None)):
    import pulse_application_review as app_review
    ctx = auth(request, user_session, perm=None, admin=True)
    form = await request.form()
    try:
        minimum = int(str(form.get("min_reviews") or "0").strip())
    except ValueError:
        return back("/applications", "Bitte eine ganze Zahl angeben.", False)
    if not 0 <= minimum <= app_review.MAX_MIN_REVIEWS:
        return back("/applications", f"Die Mindestzahl muss zwischen 0 und {app_review.MAX_MIN_REVIEWS} liegen (0 = keine Vorgabe).", False)
    pulse_db.set_setting(app_review.POLICY_KEY, {"min_reviews": minimum})
    log_audit(str(ctx.user.get("global_name") or ctx.user.get("username")), ctx.user["id"], "Bewerbungs-Prüfpolicy geändert", f"Mindestbewertungen: {minimum}")
    return back("/applications", "Policy gespeichert.")


@app.get("/applications/statistik", response_class=HTMLResponse)
async def application_stats_page(request: Request, user_session: str = Cookie(None)):
    """#102: recruiting statistics (recruiting staff only); ``?days=30|90|365`` limits the period."""
    ctx = auth(request, user_session, perm=None)
    if not can_review_applications(ctx):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung für Bewerbungen.")
    import pulse_recruiting_stats as rs
    raw = str(request.query_params.get("days") or "")
    days = int(raw) if raw in ("30", "90", "365") else None
    apps = load_json(APPS_FILE, {})
    data = rs.compute(apps if isinstance(apps, dict) else {}, now_de().replace(tzinfo=None), days)

    def hours(block):
        if not block["n"]:
            return "–"
        return (f'Ø {block["avg_h"]} h · Median {block["median_h"]} h · max. {block["max_h"]} h '
                f'<span class="text-slate-400">(n={block["n"]})</span>')

    def pct(value):
        return "–" if value is None else f"{value} %"
    d, c = data["durations"], data["conversion"]
    tiles = "".join(
        f'<div class="{CARD} p-4"><div class="text-[11px] text-slate-400">{esc(label)}</div><div class="text-xl font-bold">{esc(str(value))}</div></div>'
        for label, value in (("Bewerbungen", data["total"]), ("Offen", data["status"]["pending"] + data["status"]["interview"]),
                             ("Angenommen", data["status"]["hired"]), ("Abgelehnt", data["status"]["rejected"])))
    rows = "".join(f'<tr><td class="py-1 pr-3">{esc(r["name"])}</td><td>{r["assigned"]}</td><td>{r["reviewed"]}</td><td>{pct(r["completion_pct"])}</td></tr>'
                   for r in data["reviewers"]) or '<tr><td colspan="4" class="text-slate-400 py-2">Keine Zuweisungen im Zeitraum.</td></tr>'
    nav = " · ".join(f'<a class="underline" href="/applications/statistik{q}">{label}</a>'
                     for q, label in (("", "Gesamt"), ("?days=30", "30 Tage"), ("?days=90", "90 Tage"), ("?days=365", "1 Jahr")))
    body = (f'<div class="max-w-3xl space-y-4"><div class="flex flex-wrap items-center justify-between gap-3"><h1 class="text-2xl font-bold">Recruiting-Statistik</h1>'
            f'<a href="/applications" class="px-3.5 py-2 rounded-xl text-xs font-semibold border border-slate-200 dark:border-slate-700">← Zur Bewerbungsübersicht</a></div>'
            f'<p class="text-xs text-slate-400">Zeitraum: {nav}. Durchschnitte sind Richtwerte und nennen die Zahl der zugrunde liegenden Bewerbungen.</p>'
            f'<div class="grid grid-cols-2 sm:grid-cols-4 gap-3">{tiles}</div>'
            f'<div class="{CARD} p-4 text-xs space-y-1"><div>Bis zum Gespräch gekommen: <b>{pct(c["interview_pct"])}</b> · '
            f'Angenommen (von entschieden): <b>{pct(c["hired_of_decided_pct"])}</b> · Angenommen (von allen): <b>{pct(c["hired_of_total_pct"])}</b></div>'
            f'<div>Wartezeit bis Einladung: {hours(d["to_invite"])}</div><div>Dauer bis Entscheidung: {hours(d["to_decision"])}</div>'
            f'<div>Gespräch bis Entscheidung: {hours(d["interview_to_decision"])}</div><div>Alter offener Bewerbungen: {hours(d["open_age"])}</div></div>'
            f'<div class="{CARD} p-4 overflow-x-auto"><h2 class="text-sm font-bold mb-2">Prüfer-Auslastung</h2><table class="text-xs w-full text-left">'
            f'<thead><tr class="text-slate-400"><th>Prüfer</th><th>Zugewiesen</th><th>Bewertet</th><th>Quote</th></tr></thead><tbody>{rows}</tbody></table></div></div>')
    return render_page("Recruiting-Statistik", ctx, "apps", body)


@app.get("/applications/journal/{app_id}", response_class=HTMLResponse)
async def application_comm_journal_page(app_id: str, request: Request, user_session: str = Cookie(None)):
    """#100: read-only communication journal of one application (recruiting staff only)."""
    ctx = auth(request, user_session, perm=None)
    if not can_review_applications(ctx):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung für Bewerbungen.")
    apps = load_json(APPS_FILE, {})
    if not isinstance(apps, dict) or app_id not in apps:
        raise HTTPException(status_code=404, detail="Bewerbung nicht gefunden.")
    import pulse_app_journal
    rows = pulse_app_journal.entries(app_id, limit=pulse_app_journal.MAX_LIST, newest_first=False)
    check = pulse_app_journal.verify(app_id)
    verdict = ('<span class="text-emerald-600 dark:text-emerald-400">Verkettung geprüft – unverändert</span>' if check["ok"] else
               f'<span class="text-rose-600 dark:text-rose-400">Prüfung fehlgeschlagen ab Eintrag {check["broken_at"]}: {esc(check["reason"])}</span>')
    cards = "".join(
        f'<div class="{CARD} p-4 space-y-1"><div class="flex flex-wrap items-center justify-between gap-2 text-xs">'
        f'<b>#{int(r["seq"])} · {esc(r["kind"])}</b><span class="{BADGE_OK if r["outcome"] == "sent" else BADGE_BAD} rounded-full border px-2 py-0.5">'
        f'{"zugestellt" if r["outcome"] == "sent" else "fehlgeschlagen: " + esc(r["detail"])}</span></div>'
        f'<div class="text-[11px] text-slate-500">{esc(r["at"])} · {esc(r["actor_name"] or "System")}</div>'
        f'<div class="text-xs whitespace-pre-wrap text-slate-700 dark:text-slate-200">{esc(r["text"] or "")}</div></div>'
        for r in rows) or '<p class="text-xs text-slate-500">Noch keine Nachrichten an diese Person protokolliert.</p>'
    body = (f'<div class="max-w-3xl space-y-4"><div class="flex flex-wrap items-center justify-between gap-3">'
            f'<h1 class="text-2xl font-bold">Kommunikationsjournal {esc(app_id)}</h1>'
            f'<a href="/applications" class="px-3.5 py-2 rounded-xl text-xs font-semibold border border-slate-200 dark:border-slate-700">← Zur Bewerbungsübersicht</a></div>'
            f'<p class="text-xs">{verdict}</p>{cards}</div>')
    return render_page("Kommunikationsjournal", ctx, "apps", body)


@app.get("/applications/config", response_class=HTMLResponse)
async def application_modules_config_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None)
    can_manage = bool(
        ctx.perms.get("can_manage_applications")
        or ctx.perms.get("can_promote")
        or ctx.perms.get("is_admin")
    )
    if not can_manage:
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung, Bewerbungsmodule zu verwalten.")

    states = application_module_states(ctx.config)
    module_cards = []
    for key, career in APPLICATION_CAREERS.items():
        enabled = states.get(key, True)
        checkbox = "checked" if enabled else ""
        status_class = "text-emerald-600 dark:text-emerald-400" if enabled else "text-amber-600 dark:text-amber-300"
        status_label = "Aktiv" if enabled else "Pausiert"
        module_cards.append(
            f'<label class="{CARD} p-5 flex items-start gap-4 cursor-pointer hover:border-indigo-400 transition">'
            f'<input type="checkbox" name="module_{esc(key)}" value="1" {checkbox} class="mt-1 h-4 w-4 accent-indigo-600">'
            f'<span class="flex-1 min-w-0"><span class="flex flex-wrap items-center gap-2 font-bold">{esc(career["label"])}'
            f'<span class="text-[10px] {status_class}">{status_label}</span></span>'
            f'<span class="block text-xs text-slate-500 dark:text-slate-400 mt-2">{esc(career["description"])}</span>'
            f'<span class="block text-[10px] text-slate-400 mt-3">{len(career["questions"])} automatische Testfragen · '
            f'{len(APPLICATION_PRACTICAL_TASKS.get(key, []))} manuell geprüfte Praxisaufgaben</span></span></label>'
        )
    body = f"""
    <div class="max-w-3xl space-y-5">
        <div class="flex flex-wrap items-center justify-between gap-3">
            <div><h1 class="text-2xl font-bold text-slate-900 dark:text-white">Bewerbungsmodule</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">Schalte jede Laufbahn unabhängig frei oder pausiere sie, ohne bestehende Bewerbungen zu löschen.</p></div>
            <a href="/applications" class="px-3.5 py-2 rounded-xl text-xs font-semibold border border-slate-200 dark:border-slate-700">← Zur Bewerbungsübersicht</a>
        </div>
        <form action="/applications/config" method="post" class="space-y-4">
            <div class="grid gap-3">{"".join(module_cards)}</div>
            <div class="rounded-2xl bg-indigo-500/10 border border-indigo-500/20 p-4 text-xs text-slate-500 dark:text-slate-300">
                Ausgeschaltete Module verschwinden aus der öffentlichen Auswahl. Direkte Links und manipulierte Einreichungen werden serverseitig blockiert. Bereits eingereichte Bewerbungen bleiben erhalten.
            </div>
            <button class="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-5 py-3 rounded-xl">Änderungen speichern</button>
        </form>
    </div>
    """
    body += application_templates_section(ctx)  # BOT-10
    return render_page("Bewerbungsmodule", ctx, "apps", body)


@app.post("/applications/config")
async def save_application_modules_config(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None)
    can_manage = bool(
        ctx.perms.get("can_manage_applications")
        or ctx.perms.get("can_promote")
        or ctx.perms.get("is_admin")
    )
    if not can_manage:
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung, Bewerbungsmodule zu verwalten.")

    form = await request.form()
    states = {key: bool(form.get(f"module_{key}")) for key in APPLICATION_CAREERS}
    ctx.config["application_modules"] = states
    save_json(CONFIG_FILE, ctx.config)
    enabled_count = sum(states.values())
    log_audit(
        ctx.user.get("global_name") or ctx.user.get("username") or "Unbekannt",
        str(ctx.user.get("id") or ""),
        "Bewerbungsmodule geändert",
        ", ".join(f"{APPLICATION_CAREERS[key]['label']}: {'aktiv' if enabled else 'pausiert'}" for key, enabled in states.items()),
    )
    return back("/applications/config", f"Bewerbungsmodule gespeichert: {enabled_count} von {len(states)} aktiv.")



