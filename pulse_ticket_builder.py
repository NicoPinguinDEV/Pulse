"""Visual Ticket-Panel-Builder for the Pulse dashboard (/tickets/panels).

Erstelle mehrere unabhängige Ticket-Panels: panel message with thumbnail and banner,
live Discord-style preview, categories with their own welcome texts and forms, and
ticket automation (reminders/auto-close). Saving uses the existing form handlers.
"""
from __future__ import annotations

import json

from fastapi.responses import HTMLResponse

from pulse_ticket_design import (
    AUTOMATION_DEFAULTS,
    IMAGE_KINDS,
    PLACEHOLDER_HELP,
    automation_settings,
    clean_questions,
    normalize_panel,
)

BUILDER_CSS = """
<style>
.tb-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 18px}
.tb-tab{padding:9px 14px;border-radius:12px;border:1px solid var(--border);background:var(--surface);font-size:12px;font-weight:750}
.tb-tab.active{background:linear-gradient(135deg,var(--p),var(--p2));color:#fff;border-color:transparent}
.tb-grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(340px,.95fr);gap:18px;align-items:start}
.tb-sticky{position:sticky;top:90px}
.tb-card{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:18px;margin-bottom:16px;min-width:0;max-width:100%}
.tb-form>*,.tb-q>*{min-width:0}.tb-card summary{flex-wrap:wrap}
.tb-card h3{font-size:14px;font-weight:850;margin:0 0 4px}.tb-card .hint{font-size:11px;color:var(--muted);margin:0 0 14px}
.tb-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.tb-form label{display:grid;gap:6px;font-size:12px;font-weight:700}.tb-form .wide{grid-column:1/-1}
.tb-form input:not([type=checkbox]):not([type=color]):not([type=file]),.tb-form select,.tb-form textarea{width:100%;border:1px solid var(--border);background:var(--surface2);color:var(--text);border-radius:12px;padding:10px 12px;font-size:13px;outline:0}
.tb-form textarea{min-height:110px;resize:vertical;font-family:inherit}
.tb-form input:focus,.tb-form select:focus,.tb-form textarea:focus{border-color:#9296f6;box-shadow:0 0 0 4px rgba(91,92,240,.08)}
.tb-check{display:flex!important;align-items:center;gap:9px!important;font-weight:600!important;padding:10px 12px;border:1px solid var(--border);border-radius:12px;background:var(--surface2);cursor:pointer}
.tb-check input{width:16px;height:16px;accent-color:#5b5cf0}
.tb-cats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.tb-cat{display:flex;gap:10px;align-items:flex-start;padding:11px;border:1px solid var(--border);border-radius:13px;background:var(--surface2);cursor:pointer}
.tb-cat small{display:block;color:var(--muted);font-weight:500;margin-top:2px}.tb-cat .tags{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
.tb-tag{font-size:9px;font-weight:800;border:1px solid var(--border);border-radius:999px;padding:2px 7px;color:var(--muted)}
.tb-img{display:grid;grid-template-columns:96px minmax(0,1fr);gap:12px;align-items:center;padding:12px;border:1px dashed var(--border);border-radius:14px}
.tb-img .thumb{width:96px;height:96px;border-radius:12px;background:var(--surface2) center/cover no-repeat;border:1px solid var(--border);display:grid;place-items:center;font-size:11px;color:var(--muted);text-align:center}
.tb-img.banner{grid-template-columns:1fr}.tb-img.banner .thumb{width:100%;height:120px}
.tb-img>div{min-width:0}.tb-img input{max-width:100%;min-width:0;width:100%}.tb-img input[type=file]{font-size:12px}
.tb-chip{display:inline-flex;font-size:10px;font-family:ui-monospace,monospace;border:1px solid var(--border);border-radius:8px;padding:3px 7px;margin:0 4px 4px 0;cursor:pointer;background:var(--surface2)}
.tb-panel{display:flex;gap:12px;align-items:center;padding:13px;border:1px solid var(--border);border-radius:15px;background:var(--surface);margin-bottom:10px}
.tb-panel.active{border-color:#8f92f7;box-shadow:0 0 0 4px rgba(91,92,240,.08)}
.tb-dot{width:12px;height:42px;border-radius:8px;flex:0 0 auto}
.tb-panel .meta{flex:1;min-width:0}.tb-panel .meta b{display:block;font-size:13px}.tb-panel .meta span{font-size:11px;color:var(--muted)}
.tb-actions{display:flex;gap:6px;flex-wrap:wrap}.tb-actions form{margin:0}
.tb-count{font-size:10px;color:var(--muted);text-align:right}
/* Discord preview */
.dc{background:#313338;color:#dbdee1;border-radius:14px;padding:16px;font-family:"gg sans","Noto Sans",system-ui,sans-serif;font-size:14px;line-height:1.38}
.dc-msg{display:flex;gap:14px}.dc-ava{width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,#5b5cf0,#7c3aed);display:grid;place-items:center;color:#fff;font-weight:800;flex:0 0 auto}
.dc-name{color:#fff;font-weight:600}.dc-bot{background:#5865f2;color:#fff;font-size:10px;font-weight:700;border-radius:4px;padding:1px 5px;margin-left:5px;vertical-align:2px}.dc-time{color:#949ba4;font-size:11px;margin-left:6px}
.dc-embed{margin-top:6px;max-width:520px;background:#2b2d31;border-radius:4px;border-left:4px solid #5865f2;padding:12px 16px 14px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 16px}
.dc-title{color:#fff;font-weight:700;font-size:15px;grid-column:1}.dc-desc{grid-column:1;white-space:normal;overflow-wrap:anywhere}
.dc-desc h1{font-size:20px;margin:6px 0 2px;color:#fff}.dc-desc h2{font-size:17px;margin:6px 0 2px;color:#fff}.dc-desc h3{font-size:15px;margin:6px 0 2px;color:#fff}
.dc-desc code{background:#1e1f22;border-radius:4px;padding:1px 4px;font-size:12px}.dc-desc blockquote{border-left:4px solid #4e5058;margin:2px 0;padding-left:10px}
.dc-thumb{grid-column:2;grid-row:1/span 3;width:80px;height:80px;border-radius:4px;object-fit:cover}
.dc-field{grid-column:1/-1}.dc-field b{color:#fff;font-size:13px;display:block;margin-bottom:2px}
.dc-image{grid-column:1/-1;width:100%;border-radius:4px;max-height:300px;object-fit:cover}
.dc-footer{grid-column:1/-1;color:#b5bac1;font-size:11px;display:flex;gap:6px;align-items:center}
.dc-select{margin-top:8px;max-width:520px;background:#1e1f22;border-radius:4px;padding:10px 12px;color:#949ba4;display:flex;justify-content:space-between}
.dc-buttons{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;max-width:520px}
.dc-btn{border-radius:4px;padding:6px 14px;font-size:13px;font-weight:500;color:#fff;background:#5865f2}
.dc-modern{border-left:0!important;border:1px solid #3f4147;background:#393a41;border-radius:8px;display:block}
.dc-modern .dc-title{font-size:20px;font-weight:800;margin-bottom:6px}.dc-modern .dc-thumb{float:right;margin:0 0 8px 12px}
.dc-modern .dc-sep{height:1px;background:#4e5058;margin:14px 0}.dc-modern .dc-image{margin-top:12px;display:block}
.dc-modern .dc-footer{margin-top:4px}.dc-modern h3{font-size:16px;color:#fff;margin:0 0 6px}
.dc-btn.secondary{background:#4e5058}.dc-btn.success{background:#248046}.dc-btn.danger{background:#da373c}
.tb-q{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto auto;gap:8px;align-items:center;margin-bottom:8px}
@media(max-width:1100px){.tb-grid{grid-template-columns:1fr}.tb-sticky{position:static}}
@media(max-width:700px){.tb-form,.tb-cats,.tb-img{grid-template-columns:1fr}.tb-img .thumb{width:100%;height:110px}.dc{padding:10px}.dc-ava{display:none}.dc-msg{gap:0}.dc-embed{padding:10px 12px}.dc-thumb{width:56px;height:56px}.tb-q{grid-template-columns:1fr 1fr}.tb-panel{flex-wrap:wrap}}
</style>
"""

def _modern_style() -> bool:
    try:
        from pulse_embed_design import is_modern
        return is_modern()
    except Exception:
        return False


def _load_intro() -> str:
    from pulse_ticket_design import LOAD_INTRO
    return LOAD_INTRO


PREVIEW_JS = r"""
<script>
(function(){
  const data = JSON.parse(document.getElementById('tbData').textContent);
  const f = document.getElementById('panelForm'); if(!f) return;
  const $ = s => f.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function md(text){
    let t = esc(text).replace(/&lt;a?:(\w+):\d+&gt;/g, ':$1:');
    const lines = t.split('\n').map(l => {
      if (/^### /.test(l)) return '<h3>'+l.slice(4)+'</h3>';
      if (/^## /.test(l)) return '<h2>'+l.slice(3)+'</h2>';
      if (/^# /.test(l)) return '<h1>'+l.slice(2)+'</h1>';
      if (/^&gt; /.test(l)) return '<blockquote>'+l.slice(5)+'</blockquote>';
      if (/^[-*] /.test(l)) return '• '+l.slice(2)+'<br>';
      return l + '<br>';
    });
    return lines.join('').replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/__(.+?)__/g,'<u>$1</u>')
      .replace(/\*(.+?)\*/g,'<i>$1</i>').replace(/~~(.+?)~~/g,'<s>$1</s>').replace(/`([^`]+)`/g,'<code>$1</code>');
  }
  const objectUrls = {};
  function imageSrc(kind){
    const file = $('[name='+kind+'_upload]');
    if (file && file.files && file.files[0]) {
      if (!objectUrls[kind] || objectUrls[kind].file !== file.files[0]) objectUrls[kind] = {file:file.files[0], url:URL.createObjectURL(file.files[0])};
      return objectUrls[kind].url;
    }
    const remove = $('[name=remove_'+kind+']');
    if (data.media[kind] && !(remove && remove.checked)) return data.media[kind];
    const url = ($('[name='+kind+'_url]')||{}).value || '';
    if (/^https:\/\//.test(url)) return url;
    if (kind === 'thumbnail' && $('[name=use_server_icon]') && $('[name=use_server_icon]').checked) return data.serverIcon || '';
    if (kind === 'welcome_thumbnail' && $('[name=welcome_use_avatar]') && $('[name=welcome_use_avatar]').checked) return data.avatar || '';
    return '';
  }
  function render(){
    const color = $('[name=color]').value || '#5865F2';
    const keys = Array.from(f.querySelectorAll('[name=category_keys]:checked')).map(x => x.value);
    const cats = keys.map(k => data.categories[k]).filter(Boolean);
    const style = $('[name=component_style]').value;
    const thumb = imageSrc('thumbnail'), banner = imageSrc('banner');
    let fields = '';
    const pct = c => c.max ? Math.min(100, Math.floor(c.open * 100 / c.max)) : 0;
    const light = c => !c.max ? '🟢' : (pct(c) < 50 ? '🟢' : pct(c) < 80 ? '🟡' : '🔴');
    const loadLine = c => '• '+light(c)+' '+esc(c.emoji)+' <b style="display:inline;color:#fff">'+esc(c.name)+':</b> ' +
      (c.max ? (c.open >= c.max ? 'Ausgelastet' : 'Verfügbar')+' <code>'+c.open+'/'+c.max+'</code> ('+pct(c)+' %)' : '<code>'+c.open+'</code> offen');
    if (data.modern) {
      const load = $('[name=show_load]').checked;
      let block = '';
      if (cats.length && load) block = '<h3>📊 Ticket-Auslastung</h3>'+esc(data.loadIntro)+'<br><br>'+cats.map(loadLine).join('<br>');
      else if (cats.length && $('[name=list_categories]').checked) block = '<h3>📂 Verfügbare Kategorien</h3>'+cats.map(c => '• '+esc(c.emoji)+' <b style="display:inline;color:#fff">'+esc(c.name)+'</b>').join('<br>');
      const box = document.getElementById('dcEmbed');
      box.className = 'dc-embed dc-modern';
      box.style.borderLeftColor = '';
      box.innerHTML = (thumb ? '<img class="dc-thumb" src="'+esc(thumb)+'" alt="">' : '') +
        '<div class="dc-title">'+esc($('[name=title]').value)+'</div>' +
        '<div class="dc-desc">'+md($('[name=description]').value)+'</div>' +
        (block ? '<div class="dc-sep"></div><div class="dc-desc">'+block+'</div>' : '') +
        (banner ? '<img class="dc-image" src="'+esc(banner)+'" alt="">' : '') +
        '<div class="dc-sep"></div><div class="dc-footer">'+esc($('[name=footer]').value)+'</div>';
    }
    if (!data.modern && cats.length && ($('[name=list_categories]').checked || $('[name=show_load]').checked)) {
      const load = $('[name=show_load]').checked;
      fields = '<div class="dc-field"><b>'+(load ? '📊 Ticket-Auslastung' : '📂 Verfügbare Kategorien')+'</b>' +
        cats.map(c => load ? loadLine(c).slice(2) : esc(c.emoji)+' <b style="display:inline;color:#dbdee1">'+esc(c.name)+'</b>').join('<br>') + '</div>';
    }
    if (!data.modern) {
    document.getElementById('dcEmbed').style.borderLeftColor = color;
    document.getElementById('dcEmbed').innerHTML =
      '<div class="dc-title">'+esc($('[name=title]').value)+'</div>' +
      (thumb ? '<img class="dc-thumb" src="'+esc(thumb)+'" alt="">' : '') +
      '<div class="dc-desc">'+md($('[name=description]').value)+'</div>' + fields +
      (banner ? '<img class="dc-image" src="'+esc(banner)+'" alt="">' : '') +
      '<div class="dc-footer">'+esc($('[name=footer]').value)+' • Heute</div>';
    }
    const comp = document.getElementById('dcComponents');
    if (style === 'buttons' && cats.length) {
      comp.className = 'dc-buttons';
      comp.innerHTML = cats.map(c => '<span class="dc-btn '+esc(c.button_style)+'">'+esc(c.emoji)+' '+esc(c.name)+'</span>').join('');
    } else {
      comp.className = 'dc-select';
      comp.innerHTML = '<span>'+esc($('[name=placeholder]').value || 'Wähle die passende Ticket-Kategorie …')+'</span><span>⌄</span>';
    }
    const wt = $('[name=welcome_title]').value || (cats[0] ? cats[0].emoji+' '+cats[0].name : '🎫 Ticket');
    const wm = $('[name=welcome_message]').value || 'Willkommen {user}! Beschreibe dein Anliegen bitte möglichst vollständig.';
    const fill = s => s.replaceAll('{user}','@Max').replaceAll('{user_name}','Max').replaceAll('{server}',data.server).replaceAll('{category}', cats[0] ? cats[0].name : 'Support').replaceAll('{ticket_id}','ticket_ab12cd34');
    document.getElementById('dcWelcome').style.borderLeftColor = color;
    const wThumb = imageSrc('welcome_thumbnail'), wBanner = (cats[0] && cats[0].banner) || imageSrc('welcome_banner');
    if (data.modern) {
      const w = document.getElementById('dcWelcome');
      w.className = 'dc-embed dc-modern';
      w.style.borderLeftColor = '';
      const cat = cats[0] || {emoji: '🎫', name: 'Support'};
      w.innerHTML = (wThumb ? '<img class="dc-thumb" src="'+esc(wThumb)+'" alt="">' : '') +
        '<div class="dc-title">'+esc(fill(wt))+'</div><div class="dc-desc">'+md(fill(wm))+'</div>' +
        '<div class="dc-sep"></div><div class="dc-desc"><h3>📋 So geht es weiter</h3>1. Anliegen erklären<br>2. Rückfragen beantworten<br>3. Lösung bestätigen<br>4. Ticket über <b>Schließen</b> beenden</div>' +
        '<div class="dc-sep"></div><div class="dc-desc"><h3>📌 Ticket-Übersicht</h3><b>Status:</b> 🟢 Offen<br>• 🆔 <b>Ticket-ID:</b> <code>ticket_ab12cd34</code><br>• 🏷️ <b>Kategorie:</b> '+esc(cat.emoji)+' '+esc(cat.name)+'<br>• 👤 <b>Ersteller:</b> @Max<br>• 🚦 <b>Priorität:</b> 🟢 Normal<br>• 🧑‍💼 <b>Bearbeiter:</b> Noch nicht übernommen</div>' +
        (wBanner ? '<img class="dc-image" src="'+esc(wBanner)+'" alt="">' : '') +
        '<div class="dc-buttons" style="margin-top:10px"><span class="dc-btn success">📥 Übernehmen</span><span class="dc-btn secondary">↩️ Freigeben</span><span class="dc-btn secondary">🚦 Priorität</span><span class="dc-btn secondary">🔔 Erinnern</span><span class="dc-btn danger">🔒 Schließen</span></div>' +
        '<div class="dc-sep"></div><div class="dc-footer">Pulse Ticket-System • Ticket-Steuerung</div>';
    } else {
    document.getElementById('dcWelcome').innerHTML = '<div class="dc-title">'+esc(fill(wt))+'</div>' +
      (wThumb ? '<img class="dc-thumb" src="'+esc(wThumb)+'" alt="">' : '') +
      '<div class="dc-desc">'+md(fill(wm))+'<br><b>Ticket-ID:</b> <code>ticket_ab12cd34</code></div>' +
      (wBanner ? '<img class="dc-image" src="'+esc(wBanner)+'" alt="">' : '');
    }
    const d = $('[name=description]'); document.getElementById('descCount').textContent = d.value.length + ' / 4000';
    ['thumbnail','banner','welcome_thumbnail','welcome_banner'].forEach(k => { const el = document.getElementById(k+'Thumb'); const src = imageSrc(k); el.style.backgroundImage = src ? 'url("'+src.replace(/"/g,'%22')+'")' : 'none'; el.textContent = src ? '' : 'Kein Bild'; });
  }
  f.addEventListener('input', render); f.addEventListener('change', render);
  document.querySelectorAll('.tb-chip').forEach(chip => chip.addEventListener('click', () => {
    const ta = $('[name=welcome_message]'); const pos = ta.selectionStart ?? ta.value.length;
    ta.value = ta.value.slice(0,pos) + chip.dataset.value + ta.value.slice(pos); ta.focus(); render();
  }));
  const hex = document.getElementById('colorHex');
  if (hex) { hex.addEventListener('input', () => { if (/^#[0-9a-fA-F]{6}$/.test(hex.value)) { $('[name=color]').value = hex.value; render(); } });
             $('[name=color]').addEventListener('input', () => hex.value = $('[name=color]').value.toUpperCase()); }
  render();
})();
</script>
"""


def _json_for_script(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def render_ticket_builder(request, ctx) -> HTMLResponse:
    import webserver as ws
    from pulse_design import LEGACY_CSS
    from pulse_pro import render_pro_page

    e = ws.esc
    guild = ctx.guild
    settings = ws.pulse_ticket_config()
    registry = ws.pulse_ticket_category_registry()
    tab = request.query_params.get("tab", "panels")
    if tab not in {"panels", "categories", "automation", "settings"}:
        tab = "panels"
    selected = str(request.query_params.get("panel", "") or "")
    panels = [p for p in settings.get("ticket_panels", []) if isinstance(p, dict)] if isinstance(settings.get("ticket_panels"), list) else []
    text_channels = sorted(guild.text_channels, key=lambda c: c.position)
    channel_names = {c.id: c.name for c in text_channels}
    open_counts = {}
    for ticket in ws.pulse_db.list_tickets(limit=2000):
        if ticket.get("status") != "closed":
            open_counts[str(ticket.get("category"))] = open_counts.get(str(ticket.get("category")), 0) + 1
    active_categories = [(k, m) for k, m in registry.items() if m.get("active", True)]

    tabs = "".join(
        f'<a class="tb-tab {"active" if key == tab else ""}" href="/tickets/panels?tab={key}">{label}</a>'
        for key, label in (("panels", "🎛️ Panels"), ("categories", "📂 Kategorien & Begrüßungen"),
                           ("automation", "⏰ Automatik"), ("settings", "⚙ Protokolle & Rollen"))
    )

    def btn(label, kind=""):
        return f'<button class="pulse-btn {kind}" type="submit">{label}</button>'

    body = ""
    scripts = ""
    if tab == "panels":
        # ---------- Panel list ----------
        items = []
        for raw in panels:
            panel = normalize_panel(raw)
            pid = e(raw.get("id"))
            ch = channel_names.get(int(panel.get("channel_id") or 0) if str(panel.get("channel_id") or "").isdigit() else 0)
            published = bool(raw.get("message_id"))
            items.append(
                f'<div class="tb-panel {"active" if str(raw.get("id")) == selected else ""}"><span class="tb-dot" style="background:{e(panel["color"])}"></span>'
                f'<div class="meta"><b>{e(panel.get("name") or "Panel")}</b><span>#{e(ch or "kein Kanal")} · {len(panel["category_keys"])} Kategorien · '
                f'{"Buttons" if panel["component_style"] == "buttons" else "Dropdown"}</span><br>'
                f'<span class="pulse-pill {"good" if published else ""}" style="margin-top:6px">{"✅ Veröffentlicht" if published else "📝 Entwurf"}</span></div>'
                f'<div class="tb-actions"><a class="pulse-btn ghost" href="/tickets/panels?panel={pid}">Bearbeiten</a>'
                f'<form method="post" action="/tickets/panels/publish"><input type="hidden" name="panel_id" value="{pid}">{btn("🚀 Veröffentlichen", "primary")}</form>'
                f'<form method="post" action="/tickets/panels/duplicate"><input type="hidden" name="panel_id" value="{pid}">{btn("⧉", "ghost")}</form>'
                f'<form method="post" action="/tickets/panels/delete" onsubmit="return confirm(\'Panel wirklich löschen? Die Panel-Nachricht in Discord wird ebenfalls entfernt.\')"><input type="hidden" name="panel_id" value="{pid}">{btn("🗑", "bad")}</form></div></div>'
            )
        panel_list = "".join(items) or '<div class="pulse-empty">Noch keine Panels. Lege dein erstes Panel an – mit eigenem Bild, Banner und Kategorien.</div>'
        body += (
            '<div class="tb-card"><div class="flex flex-wrap justify-between items-center gap-3 mb-3"><div><h3>Deine Panels</h3>'
            '<p class="hint" style="margin:0">Jedes Panel hat eigenen Kanal, eigenes Design, eigene Kategorien und eine eigene Begrüßung.</p></div>'
            '<a class="pulse-btn primary" href="/tickets/panels?panel=new">➕ Neues Panel</a></div>'
            f'{panel_list}</div>'
        )

        editing = None
        if selected == "new":
            editing = {"id": "", "category_keys": [k for k, _ in active_categories][:5]}
        elif selected:
            editing = next((p for p in panels if str(p.get("id")) == selected), None)
        if editing is not None:
            panel = normalize_panel(editing)
            pid = str(editing.get("id") or "")
            selected_keys = set(panel["category_keys"])
            channel_opts = '<option value="">Kanal auswählen …</option>' + "".join(
                f'<option value="{c.id}" {"selected" if str(c.id) == str(panel.get("channel_id")) else ""}>#{e(c.name)}</option>'
                for c in text_channels
            )
            cat_boxes = "".join(
                f'<label class="tb-cat"><input type="checkbox" name="category_keys" value="{e(k)}" {"checked" if k in selected_keys else ""}>'
                f'<span><b>{e(m.get("emoji") or "🎫")} {e(m.get("name") or k)}</b><small>{e(m.get("description") or "")}</small>'
                f'<span class="tags"><span class="tb-tag">{"eigene Begrüßung" if (m.get("opening_message") or m.get("welcome_title")) else "Panel-Begrüßung"}</span>'
                f'{"<span class=tb-tag>" + str(len(clean_questions(m.get("questions")))) + " Fragen</span>" if clean_questions(m.get("questions")) else ""}'
                f'<a class="tb-tag" href="/tickets/panels?tab=categories#cat-{e(k)}">bearbeiten</a></span></span></label>'
                for k, m in active_categories
            )
            media = {
                kind: (f"/tickets/panels/media/{e(panel[kind + '_file'])}" if panel.get(kind + "_file") else "")
                for kind in IMAGE_KINDS
            }

            def image_block(kind, label, hint):
                current = panel.get(kind + "_file")
                remove = (f'<label class="tb-check" style="margin-top:6px"><input type="checkbox" name="remove_{kind}"> Hochgeladenes Bild entfernen</label>' if current else "")
                return (
                    f'<div class="tb-img {"banner" if kind == "banner" else ""}"><div class="thumb" id="{kind}Thumb">Kein Bild</div><div class="grid gap-2">'
                    f'<b class="text-xs">{label}</b><span class="text-[11px] text-slate-400">{hint}</span>'
                    f'<input type="file" name="{kind}_upload" accept="image/png,image/jpeg,image/gif,image/webp">'
                    f'<input name="{kind}_url" value="{e(panel.get(kind + "_url"))}" placeholder="oder Bild-URL (https://…)" maxlength="500">{remove}</div></div>'
                )

            chips = "".join(f'<span class="tb-chip" data-value="{e(k)}" title="{e(v)}">{e(k)}</span>' for k, v in PLACEHOLDER_HELP)
            categories_json = {
                k: {"name": str(m.get("name") or k), "emoji": str(m.get("emoji") or "🎫"), "open": open_counts.get(k, 0),
                    "max": int(m.get("max_open") or 0), "button_style": str(m.get("button_style") or "primary"),
                    "banner": (f"/tickets/panels/media/{m['welcome_banner_file']}" if m.get("welcome_banner_file")
                               else str(m.get("welcome_banner_url") or ""))}
                for k, m in active_categories
            }
            tb_data = {"categories": categories_json, "media": media, "server": guild.name,
                       "serverIcon": guild.icon.url if getattr(guild, "icon", None) else "",
                       "avatar": "https://cdn.discordapp.com/embed/avatars/0.png",
                       "modern": _modern_style(), "loadIntro": _load_intro()}
            publish_box = (
                f'<form method="post" action="/tickets/panels/publish" class="mt-3"><input type="hidden" name="panel_id" value="{e(pid)}">'
                f'<button class="pulse-btn primary w-full" type="submit">🚀 In Discord veröffentlichen / aktualisieren</button></form>'
                if pid else '<p class="text-[11px] text-slate-400 mt-3">Nach dem ersten Speichern kannst du das Panel veröffentlichen.</p>'
            )
            body += f'''
<div class="tb-grid">
 <form id="panelForm" method="post" action="/tickets/panels/save" enctype="multipart/form-data">
  <input type="hidden" name="builder_v2" value="1"><input type="hidden" name="panel_id" value="{e(pid)}">
  <div class="tb-card"><h3>1 · Grundlagen</h3><p class="hint">Interner Name, Zielkanal und wie Nutzer eine Kategorie auswählen.</p>
   <div class="tb-form">
    <label>Interner Panelname<input name="name" value="{e(panel.get("name"))}" maxlength="80" required placeholder="z. B. Weiterbildungen"></label>
    <label>Panel-Kanal<select name="channel_id" required>{channel_opts}</select></label>
    <label>Auswahl-Art<select name="component_style"><option value="select" {"selected" if panel["component_style"] == "select" else ""}>Dropdown-Menü</option><option value="buttons" {"selected" if panel["component_style"] == "buttons" else ""}>Buttons (je Kategorie)</option></select></label>
    <label>Text im Dropdown<input name="placeholder" value="{e(panel["placeholder"])}" maxlength="150"></label>
   </div></div>
  <div class="tb-card"><h3>2 · Panel-Nachricht</h3><p class="hint">Markdown wird unterstützt: **fett**, *kursiv*, __unterstrichen__, ## Überschrift, &gt; Zitat, - Liste, Server-Emojis.</p>
   <div class="tb-form">
    <label class="wide">Titel<input name="title" value="{e(panel["title"])}" maxlength="256" required></label>
    <label class="wide">Beschreibung <span class="tb-count" id="descCount"></span><textarea name="description" maxlength="4000" rows="9" required>{e(panel["description"])}</textarea></label>
    <label>Farbe<span class="flex gap-2"><input type="color" name="color" value="{e(panel["color"])}" style="width:52px;height:42px;border-radius:10px;border:1px solid var(--border);background:none"><input id="colorHex" value="{e(panel["color"])}" maxlength="7"></span></label>
    <label>Fußzeile<input name="footer" value="{e(panel["footer"])}" maxlength="200"></label>
    <label class="tb-check"><input type="checkbox" name="list_categories" {"checked" if editing.get("list_categories", True) else ""}> Kategorien im Panel auflisten</label>
    <label class="tb-check"><input type="checkbox" name="show_load" {"checked" if panel["show_load"] else ""}> Live-Auslastung anzeigen</label>
   </div></div>
  <div class="tb-card"><h3>3 · Bilder</h3><p class="hint">PNG, JPG, GIF oder WEBP bis 8 MB. Hochgeladene Bilder werden direkt an die Panel-Nachricht angehängt.</p>
   <div class="grid gap-3">{image_block("thumbnail", "Bild oben rechts (Thumbnail)", "Quadratisch wirkt am besten, z. B. 512×512.")}
   <label class="tb-check"><input type="checkbox" name="use_server_icon" {"checked" if panel["use_server_icon"] else ""}> Server-Icon verwenden, wenn kein Thumbnail gesetzt ist</label>
   {image_block("banner", "Banner unten", "Breitformat, z. B. 1200×400.")}</div></div>
  <div class="tb-card"><h3>4 · Kategorien im Panel</h3><p class="hint">Bis zu 25. Begrüßung, Fragen, Rollen und Button-Farbe stellst du pro Kategorie unter „Kategorien &amp; Begrüßungen“ ein.</p>
   <div class="tb-cats">{cat_boxes}</div>
   <a class="pulse-btn ghost mt-3" href="/tickets/panels?tab=categories#new-category">➕ Neue Kategorie anlegen</a></div>
  <div class="tb-card"><h3>5 · Begrüßung im Ticket</h3><p class="hint">Standard für alle Kategorien dieses Panels. Hat eine Kategorie eine eigene Begrüßung, gilt diese.</p>
   <div class="tb-form">
    <label class="wide">Titel der Begrüßung<input name="welcome_title" value="{e(panel["welcome_title"])}" maxlength="256" placeholder="z. B. 🎓 Willkommen zur Weiterbildung"></label>
    <label class="wide">Begrüßungsnachricht<textarea name="welcome_message" maxlength="3500" rows="6" placeholder="Willkommen {{user}}! …">{e(panel["welcome_message"])}</textarea></label>
    <div class="wide">{chips}<span class="text-[11px] text-slate-400"> ← Platzhalter anklicken</span></div>
    <div class="wide grid gap-3">{image_block("welcome_thumbnail", "Bild oben rechts in der Begrüßung", "Leer = Profilbild des Ticket-Erstellers (wenn unten aktiviert).")}
    <label class="tb-check"><input type="checkbox" name="welcome_use_avatar" {"checked" if panel["welcome_use_avatar"] else ""}> Profilbild des Erstellers verwenden, wenn kein Bild gesetzt ist</label>
    {image_block("welcome_banner", "Banner unten in der Begrüßung", "Breitformat, z. B. 1200×400 – z. B. dasselbe Banner wie beim Panel.")}</div>
    <label class="tb-check wide"><input type="checkbox" name="ping_roles" {"checked" if panel["ping_roles"] else ""}> Zuständige Rollen beim Öffnen pingen</label>
   </div></div>
  <div class="flex gap-2 flex-wrap"><button class="pulse-btn primary" type="submit">💾 Panel speichern</button><a class="pulse-btn ghost" href="/tickets/panels">Abbrechen</a></div>
 </form>
 <div class="tb-sticky">
  <div class="tb-card"><h3>Live-Vorschau</h3><p class="hint">So sieht das Panel in Discord aus.</p>
   <div class="dc"><div class="dc-msg"><div class="dc-ava">P</div><div style="min-width:0;flex:1"><span class="dc-name">Pulse</span><span class="dc-bot">APP</span><span class="dc-time">Heute</span>
    <div class="dc-embed" id="dcEmbed"></div><div id="dcComponents"></div></div></div></div>
   {publish_box}</div>
  <div class="tb-card"><h3>Vorschau: Begrüßung im Ticket</h3>
   <div class="dc"><div class="dc-msg"><div class="dc-ava">P</div><div style="min-width:0;flex:1"><span class="dc-name">Pulse</span><span class="dc-bot">APP</span>
    <div class="dc-embed" id="dcWelcome"></div></div></div></div></div>
 </div>
</div>
<script type="application/json" id="tbData">{_json_for_script(tb_data)}</script>'''
            scripts = PREVIEW_JS

    elif tab == "categories":
        roles = sorted([r for r in guild.roles if not r.is_default() and not r.managed], key=lambda r: r.position, reverse=True)
        parents = sorted(guild.categories, key=lambda c: c.position)

        def category_form(key, meta):
            is_new = not key
            selected_roles = {str(v) for v in (meta.get("role_ids") or []) if str(v).isdigit()}
            role_opts = "".join(f'<option value="{r.id}" {"selected" if str(r.id) in selected_roles else ""}>{e(r.name)}</option>' for r in roles)
            parent_opts = '<option value="">Standard-Ticketkategorie verwenden</option>' + "".join(
                f'<option value="{c.id}" {"selected" if str(c.id) == str(meta.get("parent_category_id") or "") else ""}>📁 {e(c.name)}</option>' for c in parents)
            claimed_opts = '<option value="">Nicht verschieben</option>' + "".join(
                f'<option value="{c.id}" {"selected" if str(c.id) == str(meta.get("claimed_category_id") or "") else ""}>📁 {e(c.name)}</option>' for c in parents)
            group_opts = "".join(f'<option value="{v}" {"selected" if str(meta.get("group") or "team") == v else ""}>{l}</option>'
                                 for v, l in (("team", "Support-Team"), ("highteam", "Highteam"), ("leadership", "Führungsebene")))
            style_opts = "".join(f'<option value="{v}" {"selected" if str(meta.get("button_style") or "primary") == v else ""}>{l}</option>'
                                 for v, l in (("primary", "Blau"), ("secondary", "Grau"), ("success", "Grün"), ("danger", "Rot")))
            questions = clean_questions(meta.get("questions"))
            q_rows = ""
            for i in range(1, 6):
                q = questions[i - 1] if i <= len(questions) else {}
                q_rows += (
                    f'<div class="tb-q"><input name="q_label_{i}" value="{e(q.get("label", ""))}" maxlength="45" placeholder="Frage {i} (leer = keine)">'
                    f'<input name="q_placeholder_{i}" value="{e(q.get("placeholder", ""))}" maxlength="100" placeholder="Hinweis im Feld">'
                    f'<label class="tb-check"><input type="checkbox" name="q_required_{i}" {"checked" if q.get("required", True) else ""}> Pflicht</label>'
                    f'<label class="tb-check"><input type="checkbox" name="q_long_{i}" {"checked" if q.get("long") else ""}> Lang</label></div>'
                )
            delete = "" if is_new or meta.get("builtin") else (
                f'<form method="post" action="/tickets/categories/delete" onsubmit="return confirm(\'Diese eigene Kategorie wirklich löschen?\')" class="mt-2">'
                f'<input type="hidden" name="category_id" value="{e(key)}"><button class="pulse-btn bad" type="submit">Eigene Kategorie löschen</button></form>')
            current_banner = meta.get("welcome_banner_file")
            banner_src = (f"/tickets/panels/media/{e(current_banner)}" if current_banner
                          else e(meta.get("welcome_banner_url") or ""))
            banner_style = f' style="background-image:url(&quot;{banner_src}&quot;)"' if banner_src else ""
            banner_remove = ('<label class="tb-check" style="margin-top:6px"><input type="checkbox" name="remove_welcome_banner" value="1"> Hochgeladenes Banner entfernen</label>'
                             if current_banner else "")
            banner_block = (
                f'<div class="tb-img banner wide"><div class="thumb"{banner_style}>{"" if banner_src else "Panel-Banner wird verwendet"}</div><div class="grid gap-2">'
                f'<b class="text-xs">🖼️ Banner dieser Kategorie (Begrüßung im Ticket)</b>'
                f'<span class="text-[11px] text-slate-400">Großes Bild unten in der Begrüßung, z. B. „HIGHTEAM TICKET“. PNG, JPG, GIF oder WEBP bis 8 MB. Leer = Banner des Panels.</span>'
                f'<input type="file" name="welcome_banner_upload" accept="image/png,image/jpeg,image/gif,image/webp">'
                f'<input name="welcome_banner_url" value="{e(meta.get("welcome_banner_url") or "")}" placeholder="oder Bild-URL (https://…)" maxlength="500">{banner_remove}</div></div>'
            )
            anchor = "new-category" if is_new else f"cat-{e(key)}"
            title = "➕ Neue Kategorie anlegen" if is_new else f'{e(meta.get("emoji") or "🎫")} {e(meta.get("name") or key)}'
            tags = "" if is_new else (
                f'<span class="tb-tag">{"Standard" if meta.get("builtin") else "Eigene"}</span>'
                f'<span class="tb-tag">{len(questions)} Fragen</span>'
                f'<span class="tb-tag">{"eigene Begrüßung" if (meta.get("opening_message") or meta.get("welcome_title")) else "Panel-Begrüßung"}</span>')
            return f'''
<details class="tb-card" id="{anchor}"><summary class="flex justify-between items-center gap-3 cursor-pointer"><b>{title}</b><span class="flex gap-1">{tags}</span></summary>
<form method="post" action="/tickets/categories/save" class="tb-form mt-4" enctype="multipart/form-data">
 <input type="hidden" name="builder_v2" value="1"><input type="hidden" name="category_id" value="{e(key)}"><input type="hidden" name="return_to" value="/tickets/panels?tab=categories">
 <label>Name<input name="name" value="{e(meta.get("name") or "")}" maxlength="80" required placeholder="z. B. Weiterbildung zum Sup"></label>
 <label>Emoji<input name="emoji" value="{e(meta.get("emoji") or "🎫")}" maxlength="100" required></label>
 <label class="wide">Beschreibung im Auswahlmenü<input name="description" value="{e(meta.get("description") or "")}" maxlength="100" required></label>
 <label>Präfix für Ticketkanäle<input name="prefix" value="{e(meta.get("prefix") or "")}" pattern="[a-zA-Z0-9_-]{{1,24}}" maxlength="24" required placeholder="weiterbildung"></label>
 <label>Max. offene Tickets (0 = unbegrenzt)<input type="number" name="max_open" min="0" max="1000" value="{int(meta.get("max_open") or 0)}"></label>
 <label>Discord-Kategorie für neue Tickets<select name="parent_category_id">{parent_opts}</select></label>
 <label>Discord-Kategorie nach Übernahme<select name="claimed_category_id">{claimed_opts}</select></label>
 <label>Bearbeitungsbereich<select name="group">{group_opts}</select></label>
 <label>Zuständige Rollen<select name="role_ids" multiple size="5">{role_opts}</select></label>
 <label>Button-Farbe (Buttons-Modus)<select name="button_style">{style_opts}</select></label>
 <label class="wide">Begrüßungstitel im Ticket<input name="welcome_title" value="{e(meta.get("welcome_title") or "")}" maxlength="256" placeholder="leer = Panel-Begrüßung"></label>
 {banner_block}
 <label class="wide">Begrüßungsnachricht im Ticket<textarea name="opening_message" maxlength="1800" rows="5" placeholder="leer = Panel-Begrüßung. Platzhalter: {{user}}, {{user_name}}, {{server}}, {{category}}, {{ticket_id}}">{e(meta.get("opening_message") or "")}</textarea></label>
 <div class="wide"><b class="text-xs">Formular vor dem Öffnen (bis zu 5 Fragen)</b><p class="text-[11px] text-slate-400 mb-2">Die Antworten erscheinen in der Begrüßung des Tickets – das Team hat sofort alle Infos.</p>{q_rows}</div>
 <div class="wide"><button class="pulse-btn primary" type="submit">{"Kategorie erstellen" if is_new else "Kategorie speichern"}</button></div>
</form>{delete}</details>'''

        body += '<div class="grid gap-1">' + "".join(category_form(k, m) for k, m in active_categories) + category_form("", {}) + "</div>"
        body += '<script>if(location.hash){const d=document.querySelector(location.hash);if(d&&d.tagName==="DETAILS"){d.open=true;d.scrollIntoView({block:"start"})}}</script>'

    elif tab == "automation":
        auto = automation_settings(settings.get("automation"))
        rows = (
            ("unclaimed_ping_minutes", "Minuten", "Zuständige Rollen erinnern, wenn niemand das Ticket übernimmt", "Ein Ping pro Ticket."),
            ("staff_reply_hours", "Stunden", "Bearbeiter erinnern, wenn der Ersteller auf Antwort wartet", "Einmal pro neuer Nutzernachricht."),
            ("user_reminder_hours", "Stunden", "Ersteller erinnern (Kanal + DM), wenn das Team auf ihn wartet", "Einmal pro Team-Nachricht."),
            ("auto_close_hours", "Stunden", "Nach der Ersteller-Erinnerung automatisch schließen", "Mit Transkript und DM. 0 = nie automatisch schließen."),
        )
        fields = "".join(
            f'<label>{label}<span class="text-[11px] text-slate-400 font-normal">{hint} · 0 = aus</span>'
            f'<span class="flex gap-2 items-center"><input type="number" name="{key}" min="0" max="20160" value="{auto[key]}" style="max-width:140px"><span class="text-xs">{unit}</span></span></label>'
            for key, unit, label, hint in rows
        )
        body += f'''<div class="tb-card"><h3>⏰ Smarte Ticket-Automatik</h3><p class="hint">Läuft im vorhandenen 5-Minuten-Zyklus des Bots (kein zusätzlicher Dienst). Jede Erinnerung wird pro Situation nur einmal gesendet. Standard: {AUTOMATION_DEFAULTS["unclaimed_ping_minutes"]} min / {AUTOMATION_DEFAULTS["staff_reply_hours"]} h / {AUTOMATION_DEFAULTS["user_reminder_hours"]} h / Auto-Close aus.</p>
<form method="post" action="/tickets/automation" class="tb-form">{fields}<div class="wide"><button class="pulse-btn primary" type="submit">Automatik speichern</button></div></form></div>
<div class="tb-card"><h3>🔔 Weitere Ticket-Funktionen</h3><p class="hint" style="margin:0">Im Ticket gibt es jetzt zusätzlich den Button <b>„Erinnern“</b>: Das Team pingt den Ersteller im Ticket und per DM. Formular-Fragen pro Kategorie stellst du unter „Kategorien &amp; Begrüßungen“ ein.</p></div>'''

    else:
        panel_ids = ws.pulse_ticket_panel_channel_ids()
        log_open = ws.pulse_ticket_log_channel_id("created")
        log_claim = ws.pulse_ticket_log_channel_id("claimed")
        panel_opts = "".join(f'<option value="{c.id}" {"selected" if c.id in panel_ids else ""}>#{e(c.name)}</option>' for c in text_channels)
        body += f'''<div class="tb-card"><h3>⚙ Protokolle &amp; Rollen</h3><p class="hint">Protokollkanäle, zuständige Rollen der Standardbereiche und die Kanäle des klassischen Pulse-Panels.</p>
<form method="post" action="/tickets/settings" class="tb-form">
 <label>Protokoll: neue Tickets<select name="open_log_channel_id" required>{ws._ticket_channel_options(guild, [log_open])}</select></label>
 <label>Protokoll: übernommene Tickets<select name="claim_log_channel_id" required>{ws._ticket_channel_options(guild, [log_claim])}</select></label>
 <label>Rollen: Allgemeiner Support, Bug, Entbannung<select name="team_role_ids" multiple size="6">{ws._ticket_role_options(guild, "team_role_ids", settings.get("team_role_ids", ctx.config.get("team_role_ids", [])))}</select></label>
 <label>Rollen: Highteam<select name="highteam_role_ids" multiple size="6">{ws._ticket_role_options(guild, "highteam_role_ids", settings.get("highteam_role_ids", []))}</select></label>
 <label>Rollen: Führungsebene<select name="leadership_role_ids" multiple size="6">{ws._ticket_role_options(guild, "leadership_role_ids", settings.get("leadership_role_ids", []))}</select></label>
 <label>Kanäle des klassischen Pulse-Panels<select name="panel_channel_ids" multiple size="6" required>{panel_opts}</select></label>
 <div class="wide"><button class="pulse-btn primary" type="submit">Einstellungen speichern</button></div>
</form>
<form method="post" action="/tickets/setup-panels" class="mt-3"><button class="pulse-btn ghost" type="submit">Klassische Panels aktualisieren/einrichten</button></form></div>'''

    content = (
        '<div class="pulse-legacy"><div class="pulse-legacy-head"><div><div class="pulse-kicker" style="opacity:1;color:var(--muted)">Pulse Ticket-System</div>'
        '<h1>Ticket-Panel-Builder</h1><div class="muted text-sm mt-1">Erstelle mehrere unabhängige Ticket-Panels – mit eigenem Bild, Banner, Kategorien, Begrüßungen und Formularen.</div></div>'
        '<div class="actions"><a class="btn" href="/tickets">← Ticket-Zentrale</a></div></div>'
        f'<nav class="tb-tabs">{tabs}</nav>{body}</div>{scripts}'
    )
    html = render_pro_page(ws, "Ticket-Panel-Builder", ctx, "ticket-panels", content, LEGACY_CSS + BUILDER_CSS)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
