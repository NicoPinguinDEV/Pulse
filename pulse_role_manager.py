"""Clear, grouped role manager for one member (/team/{user_id}/roles).

Same form contract as before (add_role_ids / remove_role_ids / action_reason), so the
existing save handler with all its rank and hierarchy checks stays in charge.
"""
from __future__ import annotations

import json

from pulse_role_utils import group_roles, is_separator

ROLE_CSS = """
<style>
.rm-head{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.rm-ava{width:64px;height:64px;border-radius:18px;object-fit:cover;border:1px solid var(--border)}
.rm-chips{display:flex;flex-wrap:wrap;gap:8px}
.rm-chip{display:inline-flex;align-items:center;gap:7px;max-width:100%;padding:7px 10px;border-radius:999px;border:1px solid var(--border);background:var(--surface2);font-size:12px;font-weight:650;cursor:pointer;user-select:none;transition:.15s}
.rm-chip .dot{width:9px;height:9px;border-radius:50%;flex:0 0 auto}
.rm-chip .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rm-chip.locked{cursor:not-allowed;opacity:.6}
.rm-chip.remove{border-color:rgba(240,68,56,.45);background:rgba(240,68,56,.08);text-decoration:line-through}
.rm-chip.add{border-color:rgba(18,183,106,.45);background:rgba(18,183,106,.09)}
.rm-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.rm-tools input[type=search]{flex:1;min-width:200px}
.rm-group{border:1px solid var(--border);border-radius:16px;background:var(--surface);margin-bottom:10px;overflow:hidden}
.rm-group summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 14px;font-size:13px;font-weight:800}
.rm-group summary::-webkit-details-marker{display:none}
.rm-group summary .cnt{font-size:10px;color:var(--muted);font-weight:700}
.rm-rows{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:6px;padding:0 12px 12px}
.rm-row{display:flex;align-items:center;gap:8px;padding:9px 10px;border:1px solid var(--border);border-radius:12px;background:var(--surface2);font-size:12px;cursor:pointer;min-width:0}
.rm-row .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rm-row .dot{width:9px;height:9px;border-radius:50%;flex:0 0 auto}
.rm-row .act{font-size:11px;font-weight:800;color:#079455;white-space:nowrap}
.rm-row.add{border-color:rgba(18,183,106,.5);background:rgba(18,183,106,.1)}
.rm-row.add .act{color:#fff;background:#12b76a;border-radius:8px;padding:2px 7px}
.rm-row.locked{opacity:.5;cursor:not-allowed}.rm-row.locked .act{color:var(--muted)}
.rm-row.sep .nm{font-style:italic;color:var(--muted)}
.rm-bar{position:sticky;bottom:12px;z-index:30;margin-top:16px;border:1px solid var(--border);background:color-mix(in srgb,var(--surface) 92%,transparent);backdrop-filter:blur(14px);border-radius:18px;padding:14px;box-shadow:0 18px 50px rgba(0,0,0,.25)}
.rm-bar .summary{font-size:12px;font-weight:700;margin-bottom:8px}
.rm-quick{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.rm-quick button{font-size:11px;padding:5px 9px;border-radius:999px;border:1px solid var(--border);background:var(--surface2)}
.rm-hidden{display:none!important}
#rmPending{max-height:96px;overflow-y:auto}
@media(max-width:700px){.rm-rows{grid-template-columns:1fr}.rm-bar{bottom:6px;padding:10px}
 #rmPending{max-height:62px}.rm-quick{flex-wrap:nowrap;overflow-x:auto;margin:6px 0;padding-bottom:2px}.rm-quick button{flex:0 0 auto}
 .rm-bar .rm-chip{padding:5px 8px;font-size:11px}}
</style>
"""

ROLE_JS = r"""
<script>
(function(){
  const data = JSON.parse(document.getElementById('rmData').textContent);
  const add = new Set(), remove = new Set();
  const byId = Object.fromEntries(data.roles.map(r => [String(r.id), r]));
  const form = document.getElementById('rmForm');
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function sync(){
    document.querySelectorAll('[data-current]').forEach(el => el.classList.toggle('remove', remove.has(el.dataset.current)));
    document.querySelectorAll('[data-add]').forEach(el => {
      const on = add.has(el.dataset.add); el.classList.toggle('add', on);
      el.querySelector('.act').textContent = el.classList.contains('locked') ? '🔒' : (on ? '✓ ausgewählt' : '+ hinzufügen');
    });
    const parts = [...add].map(id => '<span class="rm-chip add"><span class="dot" style="background:'+byId[id].color+'"></span><span class="nm">+ '+esc(byId[id].name)+'</span></span>')
      .concat([...remove].map(id => '<span class="rm-chip remove"><span class="dot" style="background:'+byId[id].color+'"></span><span class="nm">− '+esc(byId[id].name)+'</span></span>'));
    document.getElementById('rmPending').innerHTML = parts.join('') || '<span class="text-[11px] text-slate-400">Noch keine Änderung ausgewählt – Rollen unten anklicken.</span>';
    document.getElementById('rmCount').textContent = add.size || remove.size ? ('+' + add.size + ' / −' + remove.size) : 'Keine Änderungen';
    const reason = form.querySelector('[name=action_reason]').value.trim();
    form.querySelector('button[type=submit]').disabled = !(add.size || remove.size) || !reason;
  }
  document.querySelectorAll('[data-current]').forEach(el => el.addEventListener('click', () => {
    if (el.classList.contains('locked')) return;
    const id = el.dataset.current; remove.has(id) ? remove.delete(id) : remove.add(id); sync();
  }));
  document.querySelectorAll('[data-add]').forEach(el => el.addEventListener('click', () => {
    if (el.classList.contains('locked')) return;
    const id = el.dataset.add; add.has(id) ? add.delete(id) : add.add(id); sync();
  }));
  const rank = document.getElementById('rmRank');
  if (rank) rank.addEventListener('change', () => {
    if (!rank.value) return;
    data.rankIds.forEach(id => { add.delete(String(id)); remove.delete(String(id)); });
    if (!data.currentIds.includes(Number(rank.value))) add.add(rank.value);
    data.currentIds.filter(id => data.rankIds.includes(id) && String(id) !== rank.value && !byId[id].locked).forEach(id => remove.add(String(id)));
    sync();
  });
  const search = document.getElementById('rmSearch'), showLocked = document.getElementById('rmShowLocked');
  function filter(){
    const q = search.value.trim().toLowerCase();
    document.querySelectorAll('.rm-group').forEach(g => {
      let visible = 0;
      g.querySelectorAll('[data-add]').forEach(el => {
        const hide = (q && !el.dataset.name.includes(q)) || (el.classList.contains('locked') && !showLocked.checked);
        el.classList.toggle('rm-hidden', hide); if (!hide) visible++;
      });
      g.classList.toggle('rm-hidden', visible === 0);
      if (q && visible) g.open = true;
      g.querySelector('.cnt').textContent = visible + ' Rollen';
    });
  }
  search.addEventListener('input', filter); showLocked.addEventListener('change', filter);
  document.querySelectorAll('.rm-quick button').forEach(b => b.addEventListener('click', () => {
    const input = form.querySelector('[name=action_reason]'); input.value = b.dataset.reason; input.focus(); sync();
  }));
  form.querySelector('[name=action_reason]').addEventListener('input', sync);
  form.addEventListener('submit', e => {
    form.querySelectorAll('input[name=add_role_ids],input[name=remove_role_ids]').forEach(i => i.remove());
    add.forEach(id => form.insertAdjacentHTML('beforeend', '<input type="hidden" name="add_role_ids" value="'+id+'">'));
    remove.forEach(id => form.insertAdjacentHTML('beforeend', '<input type="hidden" name="remove_role_ids" value="'+id+'">'));
    const lines = [...add].map(id => '+ ' + byId[id].name).concat([...remove].map(id => '− ' + byId[id].name));
    if (!confirm('Diese Rollenänderungen für ' + data.member + ' anwenden?\n\n' + lines.join('\n'))) e.preventDefault();
  });
  filter(); sync();
})();
</script>
"""

QUICK_REASONS = ("Beförderung", "Degradierung", "Neues Teammitglied", "Bestandene Weiterbildung", "Korrektur einer Fehlvergabe", "Austritt aus dem Team")


def render_role_manager(esc, ctx, member, team_role_ids) -> str:
    e = esc
    guild = member.guild
    bot_top = getattr(guild.me, "top_role", None)
    actor_top = getattr(ctx.member, "top_role", None)
    is_admin = bool(ctx.perms.get("is_admin"))
    current_ids = {r.id for r in member.roles}
    rank_ids = [int(x) for x in team_role_ids]

    roles = []
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        if bot_top and role.position >= bot_top.position:
            lock = "Steht über der Bot-Rolle"
        elif not is_admin and actor_top and role.position >= actor_top.position:
            lock = "Steht auf oder über deinem Rang"
        else:
            lock = ""
        roles.append({
            "id": role.id, "name": role.name, "position": role.position,
            "color": f"#{role.color.value:06x}" if role.color.value else "#94a3b8",
            "locked": bool(lock), "lock": lock, "current": role.id in current_ids,
            "rank": role.id in rank_ids, "separator": is_separator(role.name),
        })
    by_id = {r["id"]: r for r in roles}

    # Current roles as chips (highest first); click marks them for removal.
    current = sorted((r for r in roles if r["current"]), key=lambda r: -r["position"])
    chips = "".join(
        f'<span class="rm-chip {"locked" if r["locked"] else ""}" data-current="{r["id"]}" title="{e(r["lock"] or "Klicken zum Entfernen")}">'
        f'<span class="dot" style="background:{r["color"]}"></span><span class="nm">{e(r["name"])}</span>{" 🔒" if r["locked"] else " ✕"}</span>'
        for r in current
    ) or '<span class="text-xs text-slate-400">Keine Rollen.</span>'

    # Addable roles grouped by the server's own divider roles.
    groups_html = []
    # Group with *all* roles so an assigned divider still closes its group; show only unassigned ones.
    for group in group_roles(roles):
        rows = [r for r in group["roles"] if not r["current"]]
        if group["separator"] and not group["separator"]["current"]:
            rows.append(group["separator"])
        if not rows:
            continue
        has_rank = any(r["rank"] for r in rows)
        items = "".join(
            f'<div class="rm-row {"locked" if r["locked"] else ""} {"sep" if r["separator"] else ""}" data-add="{r["id"]}" data-name="{e(r["name"].lower())}" title="{e(r["lock"] or ("Trennrolle" if r["separator"] else "Klicken zum Hinzufügen"))}">'
            f'<span class="dot" style="background:{r["color"]}"></span><span class="nm">{e(r["name"])}</span><span class="act"></span></div>'
            for r in rows
        )
        groups_html.append(
            f'<details class="rm-group" {"open" if has_rank else ""}><summary><span>{e(group["label"])}</span><span class="cnt"></span></summary>'
            f'<div class="rm-rows">{items}</div></details>'
        )

    # Rank shortcut: set exactly one configured team rank.
    rank_roles = sorted((by_id[i] for i in rank_ids if i in by_id), key=lambda r: -r["position"])
    current_rank = next((r for r in rank_roles if r["current"]), None)
    rank_select = ""
    if rank_roles:
        options = '<option value="">Team-Rang setzen …</option>' + "".join(
            f'<option value="{r["id"]}" {"disabled" if r["locked"] else ""}>{e(r["name"])}{" (aktuell)" if r["current"] else ""}{" 🔒" if r["locked"] else ""}</option>'
            for r in rank_roles
        )
        rank_select = (
            '<section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">⚡ Rang-Schnellwahl</div>'
            '<div class="pulse-section-sub">Setzt genau einen Team-Rang und entfernt die anderen Rang-Rollen – alle übrigen Rollen bleiben.</div></div></div>'
            f'<div class="pulse-card-b"><select id="rmRank" class="pulse-input">{options}</select></div></section>'
        )

    data = {
        "member": member.display_name,
        "roles": [{"id": r["id"], "name": r["name"], "color": r["color"], "locked": r["locked"]} for r in roles],
        "rankIds": rank_ids,
        "currentIds": sorted(current_ids),
    }
    quick = "".join(f'<button type="button" data-reason="{e(q)}">{e(q)}</button>' for q in QUICK_REASONS)
    avatar = e(member.display_avatar.url) if getattr(member, "display_avatar", None) else ""

    body = f'''
<div class="pulse-card mb-5"><div class="pulse-card-b">
 <div class="rm-head">
  {f'<img class="rm-ava" src="{avatar}" alt="">' if avatar else ''}
  <div style="flex:1;min-width:200px">
   <div class="text-[11px] uppercase tracking-widest text-slate-400 font-bold">Team · Rollenverwaltung</div>
   <h1 class="text-2xl font-extrabold">{e(member.display_name)}</h1>
   <div class="text-xs text-slate-400 mt-1">@{e(member.name)} · Rang: <b>{e(current_rank["name"]) if current_rank else "kein Team-Rang"}</b> · {len(current)} Rollen</div>
  </div>
  <div class="flex gap-2 flex-wrap"><a class="pulse-btn ghost" href="/member/{member.id}">👤 Teamakte</a><a class="pulse-btn ghost" href="/team">← Teamliste</a></div>
 </div>
</div></div>
<form id="rmForm" action="/team/{member.id}/roles" method="post">
<div class="pulse-grid">
 <div class="space-y-5">
  <section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">➕ Rollen hinzufügen</div>
   <div class="pulse-section-sub">Gruppiert nach euren Ebenen. Antippen wählt eine Rolle aus.</div></div></div>
   <div class="pulse-card-b">
    <div class="rm-tools mb-3"><input id="rmSearch" type="search" class="pulse-input" placeholder="Rolle suchen, z. B. Supporter, Ping, Waffenschein …">
     <label class="text-xs flex items-center gap-2"><input id="rmShowLocked" type="checkbox"> Gesperrte zeigen</label></div>
    {"".join(groups_html) or '<div class="pulse-empty">Keine weiteren Rollen verfügbar.</div>'}
   </div></section>
 </div>
 <div class="space-y-5">
  {rank_select}
  <section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">🏷️ Aktuelle Rollen</div>
   <div class="pulse-section-sub">Antippen markiert eine Rolle zum Entfernen. 🔒 = nicht verwaltbar.</div></div></div>
   <div class="pulse-card-b"><div class="rm-chips">{chips}</div></div></section>
 </div>
</div>
<div class="rm-bar">
 <div class="flex flex-wrap justify-between items-center gap-2"><div class="summary">Geplante Änderungen · <span id="rmCount"></span></div></div>
 <div class="rm-chips mb-2" id="rmPending"></div>
 <div class="rm-quick">{quick}</div>
 <div class="flex flex-col sm:flex-row gap-2">
  <input type="text" name="action_reason" maxlength="500" required class="pulse-input" placeholder="Grund (Pflicht) – wird im Team-Updates-Kanal protokolliert">
  <button type="submit" class="pulse-btn primary whitespace-nowrap" disabled>💾 Änderungen speichern</button>
 </div>
</div>
</form>
<script type="application/json" id="rmData">{json.dumps(data, ensure_ascii=False).replace("</", "<\\/")}</script>
{ROLE_JS}'''
    return body
