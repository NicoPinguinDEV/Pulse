"""Audit #70/#117: one permission-filtered live-change signal on every shared TeamOS page.

The common webserver shell injects this once per page. It listens to the durable event cursor through SSE
and falls back to bounded 15-second polling when EventSource is unavailable or repeatedly fails. Events
are filtered again by the server for the current viewer; this browser layer only filters known domains.
A change shows a refresh hint instead of reloading automatically, so unsaved forms and scroll position survive.
"""
from __future__ import annotations

import json

# Matches the domains for which pulse_eventbus has an explicit viewer policy.
# Unlisted domains remain server-side admin-only and are never exposed by this client.
LIVE_DOMAINS = ("ticket", "moderation", "task", "handover", "announcement", "promotion", "recruiting", "team")

_SCRIPT = """<style>#pulseLiveBanner{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:60;background:#4338ca;color:#fff;padding:10px 14px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.35);font-size:13px;max-width:92vw}#pulseLiveBanner[hidden]{display:none}#pulseLiveBanner button{margin-left:8px;background:#fff;color:#4338ca;border:0;border-radius:8px;padding:4px 10px;font-weight:600;cursor:pointer}</style>
<div id="pulseLiveBanner" role="status" aria-live="polite" hidden><span id="pulseLiveText"></span> <button type="button" id="pulseLiveReload">Aktualisieren</button></div>
<script>(function(){var domains=__DOMAINS__,es=null,timer=null,cursor=null,errors=0,busy=false,n=0;
var banner=document.getElementById('pulseLiveBanner'),text=document.getElementById('pulseLiveText');
function remember(value){var seq=Number(value);if(Number.isFinite(seq)&&seq>=0)cursor=cursor===null?seq:Math.max(cursor,seq);}
function notice(){n++;text.textContent='Neue Änderungen ('+n+')';banner.hidden=false;}
function accept(item){if(!item||typeof item!=='object')return;if(item.seq!==undefined)remember(item.seq);if(domains.indexOf(item.domain)>=0)notice();}
function acceptJSON(raw){try{var data=JSON.parse(raw);if(data&&data.cursor!==undefined)remember(data.cursor);return data;}catch(e){return null;}}
function closeStream(){if(es){es.close();es=null;}}
function stopPolling(){if(timer!==null){window.clearInterval(timer);timer=null;}}
async function poll(){if(busy||document.hidden||!window.fetch)return;busy=true;try{
var url='/api/events/poll'+(cursor===null?'':'?cursor='+encodeURIComponent(cursor));
var response=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
if(!response.ok)return;var batch=await response.json();remember(batch.cursor);
if(batch.reset)notice();(batch.events||[]).forEach(accept);
}catch(e){}finally{busy=false;}}
function startPolling(){if(timer!==null)return;poll();timer=window.setInterval(poll,15000);}
function openStream(){if(es||document.hidden)return;if(!window.EventSource){startPolling();return;}
try{var url='/api/events/stream'+(cursor===null?'':'?cursor='+encodeURIComponent(cursor));es=new EventSource(url);
es.addEventListener('ready',function(ev){var data=acceptJSON(ev.data);if(data&&data.cursor!==undefined)remember(data.cursor);errors=0;stopPolling();});
es.addEventListener('pulse',function(ev){var data=acceptJSON(ev.data);if(data)accept(data);});
es.addEventListener('reset',function(ev){var data=acceptJSON(ev.data);if(data&&data.cursor!==undefined)remember(data.cursor);notice();});
es.addEventListener('expired',function(ev){var data=acceptJSON(ev.data);if(data&&data.cursor!==undefined)remember(data.cursor);closeStream();if(!document.hidden)window.setTimeout(openStream,50);});
es.onerror=function(){errors++;if(errors>=2){closeStream();startPolling();}};
}catch(e){closeStream();startPolling();}}
async function prime(){if(window.fetch){try{var r=await fetch('/api/events/poll',{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});if(r.ok){var d=await r.json();remember(d.cursor);}}catch(e){}}
openStream();}
document.getElementById('pulseLiveReload').addEventListener('click',function(){location.reload();});
document.addEventListener('visibilitychange',function(){if(document.hidden){closeStream();stopPolling();}else openStream();});
window.addEventListener('pagehide',function(){closeStream();stopPolling();});
prime();})();</script>"""


def script_for(page: str = "*") -> str:
    """Universal shared-shell live signal; page remains for backwards-compatible callers."""
    return _SCRIPT.replace("__DOMAINS__", json.dumps(list(LIVE_DOMAINS)))
