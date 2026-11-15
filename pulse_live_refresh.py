"""Audit #70: Echtzeit-Hinweis auf weiteren Dashboard-Seiten (Tickets, Aufgaben, Übergaben, Ankündigungen, Beförderungen, Besprechungen).

Die Seite lauscht auf den Event-Stream (``/api/events/stream``, Rechte je Person gefiltert) und zeigt bei einem passenden Ereignis einen
unaufdringlichen Hinweis „Neue Änderungen – Aktualisieren“. Es wird **nie automatisch neu geladen**: so gehen weder Eingaben in
Formularen noch die Scrollposition verloren. Ohne ``EventSource`` oder bei voller Verbindungsgrenze passiert nichts (die Seite
funktioniert wie bisher). Ein Stream je Seite wird beim Verstecken des Tabs geschlossen.
"""
from __future__ import annotations

import json

# Seiten-Schlüssel (``render_pro_page``) -> Ereignisbereiche, die diese Seite betreffen.
PAGE_DOMAINS: dict[str, tuple[str, ...]] = {
    "tickets": ("ticket",),
    "tasks": ("task",),
    "handover": ("handover",),
    "announcements": ("announcement",),
    "approvals": ("promotion",),
    "meetings": ("team",),
    "cases": ("moderation",),
}

_SCRIPT = """<style>#pulseLiveBanner{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:60;background:#4338ca;color:#fff;padding:10px 14px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.35);font-size:13px;max-width:92vw}#pulseLiveBanner[hidden]{display:none}#pulseLiveBanner button{margin-left:8px;background:#fff;color:#4338ca;border:0;border-radius:8px;padding:4px 10px;font-weight:600;cursor:pointer}</style>
<div id="pulseLiveBanner" role="status" aria-live="polite" hidden><span id="pulseLiveText"></span> <button type="button" id="pulseLiveReload">Aktualisieren</button></div>
<script>(function(){if(!window.EventSource)return;var domains=__DOMAINS__,es=null,n=0,banner=document.getElementById('pulseLiveBanner'),text=document.getElementById('pulseLiveText');
function open(){if(es||document.hidden)return;try{es=new EventSource('/api/events/stream');
es.addEventListener('pulse',function(ev){try{var d=JSON.parse(ev.data);if(domains.indexOf(d.domain)<0)return;n++;text.textContent='Neue Änderungen ('+n+')';banner.hidden=false;}catch(e){}});
es.onerror=function(){if(es&&es.readyState===2)es=null;};}catch(e){es=null;}}
function close(){if(es){es.close();es=null;}}
document.getElementById('pulseLiveReload').addEventListener('click',function(){location.reload();});
document.addEventListener('visibilitychange',function(){if(document.hidden)close();else open();});window.addEventListener('pagehide',close);open();})();</script>"""


def script_for(page: str) -> str:
    """HTML (hidden banner + script) for a page key, or ``""`` for pages without live domains."""
    domains = PAGE_DOMAINS.get(str(page or ""))
    return _SCRIPT.replace("__DOMAINS__", json.dumps(list(domains))) if domains else ""
