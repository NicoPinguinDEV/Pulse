"""Verwaltung → Sicherheit: Anti-Nuke Control Center (sections of /admin/security, administrators only).

POST /admin/security/antinuke/rules/save      create or update one individual rule
POST /admin/security/antinuke/rules/toggle    enable/disable one rule
POST /admin/security/antinuke/rules/delete    delete one rule (confirmed in the browser and server-side)
POST /admin/security/antinuke/rules/preset    add a rule from a template
POST /admin/security/antinuke/rules/simulate  dry run of one stored rule – nothing is sent, stored or changed
POST /admin/security/antinuke/trusted         save trusted users/roles
POST /admin/security/antinuke/trusted/check   check IDs and names without saving
POST /admin/security/lockdown/start           manual emergency lockdown (confirmation + reason)
POST /admin/security/lockdown/lift            restore the stored channel permissions (reason)

The global settings keep their route POST /admin/security/antinuke (pulse_guard_admin.py), which saves through
save() here. Every write: admin=True server-side, revision check (no lost updates between two open tabs), one
lock around read-modify-write, audit-log entry with the acting administrator. Invalid stored rules are kept as
they are and shown as problems – Pulse never replaces them silently with defaults.
Trusted users/roles only exempt people from Anti-Nuke rules; they never grant dashboard rights.
"""
from __future__ import annotations

import html
import json
import re
import secrets
from datetime import datetime, timezone

from fastapi import Cookie, Request
from fastapi.responses import HTMLResponse

import pulse_moderation as pm
from pulse_guard_admin import _USER_ID, _name, _parse_int, _parse_select, _parse_user_ids

GLOBAL_KEYS = (
    "enabled", "events", "action_limit", "window_seconds", "response", "kick_unverified_bots", "notify_admins",
    "trusted_role_ids", "trusted_user_ids", "alert_channel_id", "mode", "global_rule_enabled",
    "rule_cooldown_seconds", "action_cooldown_seconds", "on_missing_permissions", "manager_role_id",
    "lockdown_channel_ids", "strip_role_ids",
)
CRITICAL_ACTIONS = frozenset({"ban", "kick", "lockdown"})
# Bot permission per sanction (shown in the overview, checked again by the cog before acting).
ACTION_PERMISSIONS = {
    "quarantine": ("manage_roles", "Rollen verwalten"),
    "lockdown": ("manage_roles", "Rollen verwalten (Kanalberechtigungen)"),
    "kick": ("kick_members", "Mitglieder kicken"),
    "ban": ("ban_members", "Mitglieder bannen"),
}
PRESETS = {
    "channel_delete": {"label": "Massenlöschung von Kanälen", "events": ["channel_delete"], "limit": 3, "window_seconds": 10,
                       "actions": ["log", "alert", "notify_admins", "ban"], "severity": "critical"},
    "channel_create": {"label": "Kanal-Spam", "events": ["channel_create"], "limit": 5, "window_seconds": 15,
                       "actions": ["log", "alert", "quarantine"], "severity": "high"},
    "channel_perms": {"label": "Massenhafte Kanal-Berechtigungsänderungen", "events": ["channel_update", "channel_settings"],
                      "limit": 5, "window_seconds": 15, "actions": ["log", "alert", "quarantine"], "severity": "high"},
    "role_delete": {"label": "Massenlöschung von Rollen", "events": ["role_delete"], "limit": 3, "window_seconds": 10,
                    "actions": ["log", "alert", "notify_admins", "ban"], "severity": "critical"},
    "role_ops": {"label": "Gefährliche Rollenoperationen", "events": ["role_update", "role_create", "member_role_grant"],
                 "limit": 4, "window_seconds": 15, "actions": ["log", "alert", "quarantine"], "severity": "high"},
    "mass_ban": {"label": "Massenbans", "events": ["ban"], "limit": 3, "window_seconds": 10,
                 "actions": ["log", "alert", "quarantine"], "severity": "critical"},
    "mass_kick": {"label": "Massenkicks", "events": ["kick"], "limit": 3, "window_seconds": 10,
                  "actions": ["log", "alert", "quarantine"], "severity": "critical"},
    "webhooks": {"label": "Webhook-Missbrauch", "events": ["webhook_create", "webhook_update", "webhook_delete"],
                 "limit": 3, "window_seconds": 15, "actions": ["log", "alert", "quarantine"], "severity": "high"},
    "bot_add": {"label": "Neuer Bot hinzugefügt", "events": ["bot_add"], "limit": 1, "window_seconds": 60,
                "actions": ["log", "alert", "notify_admins"], "severity": "high"},
    "guild_update": {"label": "Servereinstellungen geändert", "events": ["guild_update"], "limit": 2, "window_seconds": 60,
                     "actions": ["log", "alert"], "severity": "medium"},
}
TRIGGER_TYPES = ("Anti-Nuke ausgelöst", "Anti-Nuke: Akteur gesperrt", "Anti-Nuke: Akteur gekickt", "Anti-Nuke: Quarantäne",
                 "Anti-Nuke: Bot entfernt", "Anti-Nuke: Lockdown gestartet")

CSS = """<style>
.an-banner{display:flex;gap:12px;align-items:flex-start;padding:14px 16px;border-radius:14px;border:1px solid var(--border);background:var(--surface2);margin-bottom:12px}
.an-banner strong{font-size:14px}.an-banner.off{border-color:rgba(148,163,184,.5)}.an-banner.alert{border-color:rgba(247,144,9,.55)}
.an-banner.protect{border-color:rgba(18,183,106,.55)}.an-banner .an-dot{font-size:22px;line-height:1}
.an-rule{border:1px solid var(--border);border-radius:14px;background:var(--surface2);margin-bottom:10px}
.an-rule>summary{cursor:pointer;list-style:none;padding:12px 14px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.an-rule>summary::-webkit-details-marker{display:none}.an-rule[open]>summary{border-bottom:1px dashed var(--border)}
.an-rule .an-body{padding:12px 14px}.an-rule-title{font-weight:750;font-size:13px;flex:1 1 220px;min-width:0;overflow-wrap:anywhere}
.an-preview{font-size:12px;line-height:1.55;padding:10px 12px;border-radius:12px;border:1px dashed var(--border);background:var(--surface)}
.an-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.an-table{width:100%;border-collapse:collapse;font-size:12px}
.an-table td,.an-table th{padding:7px 6px;border-bottom:1px dashed var(--border);text-align:left;vertical-align:top;overflow-wrap:anywhere}
.an-ok{color:#12b76a}.an-bad{color:#f04438}.an-warn{color:#f79009}
@media(max-width:640px){.an-actions .pulse-btn{flex:1 1 auto}.an-rule>summary{padding:10px}}
</style>"""

PREVIEW_JS = """<script>
(function(){
  function sentence(f){
    var data; try{data=JSON.parse(f.getAttribute('data-labels')||'{}');}catch(_){return;}
    var ev=[].slice.call(f.querySelectorAll('input[name=events]:checked')).map(function(i){return data.events[i.value]||i.value;});
    var ac=[].slice.call(f.querySelectorAll('input[name=actions]:checked')).map(function(i){return data.actions[i.value]||i.value;});
    var lim=(f.querySelector('[name=limit]')||{}).value||data.limit, win=(f.querySelector('[name=window_seconds]')||{}).value||data.window;
    var test=(f.querySelector('[name=test_mode]')||{}).checked, sanc=[].slice.call(f.querySelectorAll('input[name=actions]:checked')).some(function(i){return data.sanctions.indexOf(i.value)>=0;});
    var t='Wenn dieselbe Person innerhalb von '+win+' Sekunden mindestens '+lim+'× „'+(ev.join(' oder ')||'–')+'“ auslöst, führt Pulse aus: Vorfall protokollieren'+(ac.length?', '+ac.join(', '):'')+'.';
    if(sanc&&(test||data.mode!=='protect'))t+=' Wegen '+(test?'Testmodus der Regel':'Alarmmodus')+' werden Sanktionen nur gemeldet, nicht ausgeführt.';
    var out=f.querySelector('.an-preview'); if(out)out.textContent=t;
  }
  // After saving, the redirect points at #rule-…: open that rule and bring it into view.
  var target = location.hash && document.getElementById(location.hash.slice(1));
  if (target) {
    if (target.tagName === 'DETAILS') target.open = true;
    var go = function(){ target.scrollIntoView({block: 'start'}); };
    if (document.readyState === 'complete') setTimeout(go, 50); else window.addEventListener('load', function(){ setTimeout(go, 50); });
  }
  document.querySelectorAll('form[data-rule-form]').forEach(function(f){f.addEventListener('input',function(){sentence(f);});f.addEventListener('change',function(){sentence(f);});});
})();
</script>"""


class Conflict(RuntimeError):
    """The stored configuration changed since the form was loaded."""


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def form_revision(form):
    raw = str(form.get("revision") or "").strip()
    return int(raw) if raw.isdigit() else None


def raw_section(cfg: dict) -> dict:
    raw = cfg.get("pulse_antinuke")
    return dict(raw) if isinstance(raw, dict) else {}


def save(ws, user: dict, mutate, *, expected_revision=None) -> tuple[dict, dict]:
    """Read-modify-write of config.json → pulse_antinuke under the file lock. Returns (before, after) normalised.

    `mutate(raw, before)` returns the new raw section; only what it changes is changed. A form loaded at an
    older revision is refused (Conflict) instead of overwriting a colleague's change.
    """
    with ws._io_lock:
        cfg = ws.load_config()
        raw = raw_section(cfg)
        before = pm.normalize_antinuke(raw)
        if expected_revision is not None and expected_revision != before["revision"]:
            raise Conflict("Die Anti-Nuke-Einstellungen wurden inzwischen von jemand anderem geändert. "
                           "Bitte die Seite neu laden und die Änderung erneut vornehmen.")
        new_raw = mutate(dict(raw), before)
        new_raw["revision"] = before["revision"] + 1
        new_raw["updated_at"] = _now()
        new_raw["updated_by"] = _name(user)[:80]
        cfg["pulse_antinuke"] = new_raw
        ws.save_json(ws.CONFIG_FILE, cfg)
    return before, pm.normalize_antinuke(new_raw)


def needed_permissions(config: dict) -> list[tuple[str, str, str]]:
    """(permission, label, used by) the current configuration needs; view_audit_log always."""
    out = [("view_audit_log", "Audit-Log anzeigen", "Erkennung")]
    used: dict[str, tuple[str, list[str]]] = {}
    for rule in pm.effective_rules(config):
        for action in rule["actions"]:
            if action in ACTION_PERMISSIONS:
                key, label = ACTION_PERMISSIONS[action]
                used.setdefault(key, (label, []))[1].append(rule["label"])
    if config.get("kick_unverified_bots"):
        used.setdefault("kick_members", ("Mitglieder kicken", []))[1].append("Bots sofort entfernen")
    for key, (label, users) in used.items():
        out.append((key, label, ", ".join(dict.fromkeys(users))[:120]))
    return out


def status_of(config: dict) -> tuple[str, str, str]:
    if not config["enabled"]:
        return "off", "Deaktiviert", "Anti-Nuke ist ausgeschaltet – Pulse überwacht keine Serveraktionen."
    if config["mode"] != "protect":
        return "alert", "Alarmmodus", ("Pulse zählt und meldet verdächtige Aktionen, führt aber KEINE automatischen Sanktionen "
                                       "(Rollen entziehen, Kick, Bann, Lockdown) aus.")
    return "protect", "Aktiver Schutz", "Pulse führt die konfigurierten Schutzmaßnahmen bei Erreichen einer Schwelle automatisch aus."


def config_hints(guild, config: dict, raw: dict) -> list[tuple[str, str]]:
    """(tone, text) – problems with the stored values and settings that would make an action fail."""
    out = [("bad", text) for text in pm.antinuke_problems(raw)]
    rules = pm.effective_rules(config)
    actions = {a for r in rules for a in r["actions"]}
    if "lockdown" in actions and not config["lockdown_channel_ids"]:
        out.append(("bad", "Eine Regel nutzt „Notfall-Lockdown“, aber es sind keine Lockdown-Kanäle ausgewählt."))
    if "notify_role" in actions and not config["manager_role_id"]:
        out.append(("bad", "Eine Regel soll die Verwaltungsrolle erwähnen, aber es ist keine Verwaltungsrolle ausgewählt."))
    if config["enabled"] and not rules:
        out.append(("warn", "Anti-Nuke ist aktiv, aber keine Regel überwacht ein Ereignis."))
    if config["mode"] != "protect" and actions & set(pm.SANCTION_ACTIONS) and config["enabled"]:
        out.append(("warn", "Alarmmodus: konfigurierte Sanktionen werden nur gemeldet. Für echten Schutz in den aktiven Schutzmodus wechseln."))
    roles = {r.id for r in getattr(guild, "roles", []) or []}
    for label, ids in (("Vertrauensrolle", config["trusted_role_ids"]), ("Entziehbare Rolle", config["strip_role_ids"])):
        for rid in ids:
            if roles and rid not in roles:
                out.append(("warn", f"{label} {rid} existiert auf dem Server nicht mehr."))
    if config["manager_role_id"] and roles and config["manager_role_id"] not in roles:
        out.append(("warn", f"Verwaltungsrolle {config['manager_role_id']} existiert nicht mehr."))
    for cid in config["lockdown_channel_ids"]:
        if hasattr(guild, "get_channel") and guild.get_channel(cid) is None:
            out.append(("warn", f"Lockdown-Kanal {cid} wurde nicht gefunden."))
    return out


def _payload(row: dict) -> dict:
    payload = row.get("payload")
    if isinstance(payload, dict):
        return payload
    try:
        data = json.loads(row.get("payload_json") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def last_events(rows: list[dict]) -> tuple[dict | None, dict | None]:
    threat = next((r for r in rows if r.get("event_type") == "Anti-Nuke-Aktion erkannt" and not _payload(r).get("trusted")), None)
    action = next((r for r in rows if r.get("event_type") in TRIGGER_TYPES), None)
    return threat, action


def audit_history(ws, limit: int = 8) -> list[dict]:
    try:
        data = ws.load_json(ws.AUDIT_FILE, [])
    except Exception:
        return []
    rows = [r for r in data if isinstance(r, dict) and "Anti-Nuke" in str(r.get("action", ""))] if isinstance(data, list) else []
    return list(reversed(rows[-limit:]))


# --------------------------------------------------------------------------- rendering helpers
def _role_options(guild, selected, *, blank: str = "") -> str:
    roles = sorted((r for r in getattr(guild, "roles", []) or [] if not (hasattr(r, "is_default") and r.is_default())),
                   key=lambda r: -int(getattr(r, "position", 0)))
    selected = set(selected if isinstance(selected, (list, set, tuple)) else [selected])
    out = [f'<option value="">{e(blank)}</option>'] if blank else []
    known = set()
    for role in roles:
        known.add(role.id)
        mark = " ⚠" if pm.has_dangerous_permission(getattr(role, "permissions", None)) else ""
        out.append(f'<option value="{role.id}"{" selected" if role.id in selected else ""}>@{e(role.name)}{mark}</option>')
    out += [f'<option value="{rid}" selected>⚠ Unbekannte Rolle {rid}</option>' for rid in selected if rid and rid not in known]
    return "".join(out)


def _lockdown_options(guild, selected) -> str:
    out, known = [], set()
    channels = list(getattr(guild, "text_channels", []) or []) + list(getattr(guild, "voice_channels", []) or [])
    for ch in sorted(channels, key=lambda c: (getattr(c, "position", 0), str(getattr(c, "name", "")))):
        known.add(ch.id)
        icon = "🔊" if str(getattr(ch, "type", "")) in ("voice", "stage_voice") else "#"
        out.append(f'<option value="{ch.id}"{" selected" if ch.id in selected else ""}>{icon}{e(ch.name)}</option>')
    out += [f'<option value="{cid}" selected>⚠ Unbekannter Kanal {cid}</option>' for cid in selected if cid not in known]
    return "".join(out)


def _member_name(guild, uid) -> tuple[str, bool]:
    member = guild.get_member(int(uid)) if str(uid).isdigit() and hasattr(guild, "get_member") else None
    if member is None:
        return "nicht auf dem Server", False
    return str(getattr(member, "display_name", None) or getattr(member, "name", uid)), True


def _role_name(guild, rid) -> tuple[str, bool]:
    role = guild.get_role(int(rid)) if str(rid).isdigit() and hasattr(guild, "get_role") else None
    if role is None:
        role = next((r for r in getattr(guild, "roles", []) or [] if r.id == rid), None)
    return (f"@{role.name}", True) if role is not None else ("unbekannte Rolle", False)


def _num(name, label, lo, hi, value, hint="", required=True) -> str:
    val = "" if value is None else value
    return (f'<label class="guard-field">{label}<input class="pulse-input" type="number" name="{name}" min="{lo}" max="{hi}" '
            f'value="{val}"{" required" if required else ""}>' + (f'<span class="guard-hint">{hint}</span>' if hint else "") + "</label>")


def _short(rule: dict, config: dict) -> str:
    limit = rule["limit"] if rule.get("limit") is not None else config["action_limit"]
    window = rule["window_seconds"] if rule.get("window_seconds") is not None else config["window_seconds"]
    sanctions = [pm.RULE_ACTION_LABELS[a].split(" (")[0] for a in rule["actions"] if a in pm.SANCTION_ACTIONS]
    events = ", ".join(pm.ANTINUKE_EVENT_LABELS.get(x, x) for x in rule["events"])
    return f"{limit}× {events} in {window} s → {', '.join(sanctions) or 'nur melden'}"


def _rule_pill(rule: dict, config: dict) -> str:
    if not rule["enabled"]:
        return '<span class="pulse-pill">⏸ deaktiviert</span>'
    if rule.get("test_mode"):
        return '<span class="pulse-pill warn">🧪 Testmodus</span>'
    if config["mode"] != "protect" and any(a in pm.SANCTION_ACTIONS for a in rule["actions"]):
        return '<span class="pulse-pill warn">🔔 aktiv · Alarmmodus</span>'
    return '<span class="pulse-pill good">✅ aktiv</span>'


def rule_form(guild, config: dict, rule: dict | None) -> str:
    new = rule is None
    rule = rule or {"id": "", "enabled": True, "label": "", "events": [], "limit": None, "window_seconds": None,
                    "actions": ["log", "alert"], "severity": "high", "test_mode": False, "cooldown_seconds": None,
                    "exempt_user_ids": [], "exempt_role_ids": []}
    labels = json.dumps({"events": pm.ANTINUKE_EVENT_LABELS, "actions": {k: v for k, v in pm.RULE_ACTION_LABELS.items() if k != "log"},
                         "sanctions": list(pm.SANCTION_ACTIONS), "mode": config["mode"],
                         "limit": config["action_limit"], "window": config["window_seconds"]}, ensure_ascii=False)
    groups = "".join(
        f'<div><div class="guard-hint" style="font-weight:700;margin:4px 0">{e(title)}</div>' + "".join(
            f'<label class="guard-check"><input type="checkbox" name="events" value="{key}"{" checked" if key in rule["events"] else ""}>'
            f'<span><strong>{e(pm.ANTINUKE_EVENT_LABELS[key])}</strong><small>{e(pm.ANTINUKE_EVENT_HINTS[key])}</small></span></label>'
            for key in keys) + "</div>"
        for title, keys in pm.ANTINUKE_EVENT_GROUPS)
    actions = "".join(
        f'<label class="guard-check"><input type="checkbox" name="actions" value="{key}"{" checked" if key in rule["actions"] else ""}>'
        f'<span><strong>{e(label)}</strong>{"<small>Sanktion – nur im aktiven Schutzmodus</small>" if key in pm.SANCTION_ACTIONS else ""}</span></label>'
        for key, label in pm.RULE_ACTION_LABELS.items() if key != "log")
    severity = "".join(f'<option value="{k}"{" selected" if rule["severity"] == k else ""}>{e(v)}</option>' for k, v in pm.RULE_SEVERITY_LABELS.items())
    existing_critical = ",".join(sorted(CRITICAL_ACTIONS & set(rule["actions"]))) if not new else ""
    confirm_js = ("var c=[].slice.call(this.querySelectorAll('input[name=actions]:checked')).map(function(i){return i.value;})"
                  ".filter(function(v){return ['ban','kick','lockdown'].indexOf(v)>=0;});"
                  f"var o='{existing_critical}'.split(',');var n=c.filter(function(v){{return o.indexOf(v)<0;}});"
                  "if(n.length&&!window.confirm('Diese Regel darf Personen automatisch kicken/bannen oder Kanäle sperren ('+n.join(', ')+'). Wirklich speichern?'))return false;"
                  "this.confirm_critical.value=n.length?'1':'0';return true;")
    buttons = ['<button class="pulse-btn primary" type="submit">💾 Regel speichern</button>']
    if not new:
        buttons += [
            '<label class="guard-field" style="max-width:150px">Testen mit Aktionen<input class="pulse-input" type="number" name="count" min="1" max="100" '
            f'value="{rule["limit"] if rule["limit"] is not None else config["action_limit"]}"></label>',
            '<button class="pulse-btn ghost" type="submit" formnovalidate formaction="/admin/security/antinuke/rules/simulate">🧪 Regel testen</button>',
            f'<button class="pulse-btn ghost" type="submit" formnovalidate formaction="/admin/security/antinuke/rules/toggle">{"⏸ Deaktivieren" if rule["enabled"] else "▶ Aktivieren"}</button>',
            '<button class="pulse-btn ghost" type="submit" formnovalidate formaction="/admin/security/antinuke/rules/delete" '
            "onclick=\"if(!window.confirm('Regel wirklich löschen? Das kann nicht rückgängig gemacht werden.'))return false;this.form.confirm_delete.value='1';\">🗑 Löschen</button>",
        ]
    return f'''<form method="post" action="/admin/security/antinuke/rules/save" class="guard-form" data-rule-form data-labels="{e(labels)}" onsubmit="{e(confirm_js)}">
 <input type="hidden" name="rule_id" value="{e(rule["id"])}"><input type="hidden" name="revision" value="{config["revision"]}">
 <input type="hidden" name="confirm_critical" value="0"><input type="hidden" name="confirm_delete" value="0">
 <div class="guard-grid">
  <label class="guard-field">Bezeichnung<input class="pulse-input" name="label" maxlength="80" required value="{e(rule["label"])}" placeholder="z. B. Massenlöschung von Kanälen"></label>
  <label class="guard-field">Schweregrad<select class="pulse-input" name="severity">{severity}</select></label>
 </div>
 <label class="guard-check"><input type="checkbox" name="enabled" value="1"{" checked" if rule["enabled"] else ""}><span><strong>Regel aktiv</strong></span></label>
 <div class="pulse-section-title" style="margin-top:4px">1 · Überwachtes Ereignis</div>
 <div class="guard-grid">{groups}</div>
 <div class="pulse-section-title" style="margin-top:4px">2 · Bedingung</div>
 <div class="guard-grid">
  {_num("limit", "Anzahl Aktionen (Schwelle)", pm.RULE_MIN_LIMIT, pm.RULE_MAX_LIMIT, rule["limit"], f"Leer = globaler Wert ({config['action_limit']}).", required=False)}
  {_num("window_seconds", "… innerhalb von Sekunden", pm.RULE_MIN_WINDOW, pm.RULE_MAX_WINDOW, rule["window_seconds"], f"Leer = globaler Wert ({config['window_seconds']} s).", required=False)}
  {_num("cooldown_seconds", "Cooldown nach Auslösung (Sekunden)", 0, 86400, rule["cooldown_seconds"], f"Leer = global ({config['rule_cooldown_seconds']} s). So lange läuft die Kette pro Person nicht erneut.", required=False)}
 </div>
 <div class="guard-hint">Gezählt wird pro verantwortlicher Person (aus dem Discord-Audit-Log), jede Audit-Log-Aktion genau einmal.</div>
 <div class="pulse-section-title" style="margin-top:4px">3 · Reaktion (Reihenfolge: Protokoll → Lockdown → Rollen entziehen → Kick/Bann → Alarm)</div>
 <div class="guard-grid">{actions}</div>
 <label class="guard-check"><input type="checkbox" name="test_mode" value="1"{" checked" if rule["test_mode"] else ""}><span><strong>Testmodus für diese Regel</strong><small>Sanktionen werden nur gemeldet – auch wenn der aktive Schutzmodus eingeschaltet ist.</small></span></label>
 <div class="guard-grid">
  <label class="guard-field">Ausgenommene Rollen (nur diese Regel)<select class="pulse-input guard-select" name="exempt_role_ids" multiple>{_role_options(guild, rule["exempt_role_ids"])}</select></label>
  <label class="guard-field">Ausgenommene Personen (Discord-IDs, nur diese Regel)<textarea class="pulse-input" name="exempt_user_ids" rows="3" placeholder="Eine ID pro Zeile">{e(chr(10).join(str(v) for v in rule["exempt_user_ids"]))}</textarea></label>
 </div>
 <div class="an-preview" aria-live="polite">{e(pm.describe_rule(rule, config))}</div>
 <div class="an-actions">{"".join(buttons)}</div>
</form>'''


def render_sections(ws, ctx, rows: list[dict], *, notice: str = "", rules_notice: str = "", trusted_notice: str = "") -> str:
    """HTML of the Anti-Nuke parts of /admin/security: control center, rules, trusted people, lockdown."""
    guild = ctx.guild
    raw = raw_section(ctx.config if isinstance(ctx.config, dict) else ws.load_config())
    config = pm.normalize_antinuke(raw)
    kind, label, explanation = status_of(config)
    rules = config["rules"]
    effective = pm.effective_rules(config)
    try:
        from cogs.antinuke import load_lockdown
        lockdown = load_lockdown()
    except Exception:
        lockdown = {}
    since = (datetime.now(timezone.utc).timestamp() - 7 * 86400)

    def recent(row):
        try:
            return datetime.fromisoformat(str(row.get("created_at", "")).replace("Z", "+00:00")).timestamp() >= since
        except ValueError:
            return False
    incidents_7d = sum(1 for r in rows if r.get("event_type") in TRIGGER_TYPES and recent(r))
    detected_7d = sum(1 for r in rows if r.get("event_type") == "Anti-Nuke-Aktion erkannt" and not _payload(r).get("trusted") and recent(r))
    threat, last_action = last_events(rows)

    def when(row):
        return e(str(row.get("created_at", ""))[:16].replace("T", " ")) + " UTC"
    threat_html = (f'{when(threat)} · {e(pm.ANTINUKE_EVENT_LABELS.get(_payload(threat).get("action"), _payload(threat).get("action") or "?"))}'
                   f' · {e(_member_name(guild, threat.get("target_id"))[0])} ({e(threat.get("target_id"))})') if threat else "keine"
    action_html = (f'{when(last_action)} · {e(last_action.get("event_type"))}'
                   + (f' · {e(_payload(last_action).get("rule_label"))}' if _payload(last_action).get("rule_label") else "")
                   + (f'<div class="guard-hint">{e(str(_payload(last_action).get("result") or "")[:200])}</div>' if _payload(last_action).get("result") else "")
                   ) if last_action else "keine"
    me = getattr(guild, "me", None)
    perms = getattr(me, "guild_permissions", None)
    perm_rows = "".join(
        f'<div class="guard-kv"><span>{e(plabel)} <span class="guard-hint">({e(used)})</span></span>'
        f'<span class="{"an-ok" if perms is not None and getattr(perms, key, False) else "an-bad"}">{"✓ vorhanden" if perms is not None and getattr(perms, key, False) else "✗ fehlt"}</span></div>'
        for key, plabel, used in needed_permissions(config))
    channel = guild.get_channel(config["alert_channel_id"]) if config["alert_channel_id"] and hasattr(guild, "get_channel") else None
    alert_text = (f"#{e(channel.name)}" if channel is not None else
                  (f'<span class="an-bad">Kanal {config["alert_channel_id"]} nicht gefunden</span>' if config["alert_channel_id"]
                   else '<span class="an-warn">keiner – nur Server-Log</span>'))
    manager = _role_name(guild, config["manager_role_id"])[0] if config["manager_role_id"] else "keine"
    hints = config_hints(guild, config, raw)
    hint_html = "".join(f'<div class="pulse-alert" style="border-color:{"rgba(240,68,56,.4)" if tone == "bad" else "rgba(247,144,9,.4)"}">'
                        f'{"🔴" if tone == "bad" else "🟠"} {e(text)}</div>' for tone, text in hints) \
        or '<div class="pulse-alert" style="border-color:rgba(18,183,106,.35)">🟢 Keine fehlerhaften Einstellungen gefunden.</div>'
    history = "".join(f'<div class="guard-kv"><span>{e(h.get("timestamp"))} · {e(h.get("actor"))}</span><span class="guard-hint" style="text-align:right">{e(h.get("action"))}: {e(str(h.get("details", ""))[:160])}</span></div>'
                      for h in audit_history(ws)) or '<div class="guard-hint">Noch keine protokollierten Änderungen.</div>'
    active_rules = len(effective) if config["enabled"] else 0  # nothing is evaluated while switched off
    overview = f'''
<section id="antinuke-center" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">🛡️ Anti-Nuke Control Center</div>
<div class="pulse-section-sub">Status, Regeln, Ausnahmen und Lockdown. Discord meldet eine Aktion erst, nachdem sie passiert ist – gelöschte Kanäle oder Rollen stellt Pulse nicht wieder her.</div></div>
<span class="pulse-pill {"good" if kind == "protect" else ("warn" if kind == "alert" else "")}">{e(label)}</span></div>
<div class="pulse-card-b">{notice}
 <div class="an-banner {kind}" role="status"><span class="an-dot">{"🟢" if kind == "protect" else ("🟠" if kind == "alert" else "⚪")}</span>
  <div><strong>{e(label)}</strong><div class="guard-hint" style="font-size:12px">{e(explanation)}</div></div></div>
 <div class="guard-grid">
  <div><div class="guard-kv"><span>Aktive Schutzregeln</span><span class="guard-big">{active_rules}</span></div>
   <div class="guard-kv"><span>Individuelle Regeln (aktiv / gesamt)</span><strong>{sum(1 for r in rules if r["enabled"])} / {len(rules)}</strong></div>
   <div class="guard-kv"><span>Ausgelöste Vorfälle (7 Tage)</span><strong>{incidents_7d}</strong></div>
   <div class="guard-kv"><span>Erkannte Aktionen nicht freigegebener Personen (7 Tage)</span><strong>{detected_7d}</strong></div></div>
  <div><div class="guard-kv"><span>Zuletzt erkannte Bedrohung</span><span style="text-align:right">{threat_html}</span></div>
   <div class="guard-kv"><span>Zuletzt ausgelöste Maßnahme</span><span style="text-align:right">{action_html}</span></div>
   <div class="guard-kv"><span>Alarmkanal</span><span>{alert_text}</span></div>
   <div class="guard-kv"><span>Verwaltungsrolle</span><span>{e(manager)}</span></div>
   <div class="guard-kv"><span>Lockdown</span><span class="pulse-pill {"bad" if lockdown.get("active") else ""}">{"aktiv seit " + e(lockdown.get("at", "")[:16].replace("T", " ")) if lockdown.get("active") else "inaktiv"}</span></div>
   <div class="guard-kv"><span>Letzte Konfigurationsänderung</span><span>{e(config["updated_at"][:16].replace("T", " ") + " UTC · " + config["updated_by"]) if config["updated_at"] else "–"}</span></div></div>
  <div><div class="pulse-section-title" style="font-size:12px">Benötigte Bot-Berechtigungen</div>{perm_rows}</div>
 </div>
 <div class="space-y-2 mt-3">{hint_html}</div>
 <details class="an-rule mt-3"><summary><span class="an-rule-title">🧾 Änderungsverlauf (Audit-Log)</span></summary><div class="an-body">{history}</div></details>
</div></section>'''

    # ---- rules
    global_card = ""
    for rule in effective:
        if rule["id"] == pm.GLOBAL_RULE_ID:
            global_card = (f'<div class="an-rule" style="padding:12px 14px"><div class="an-actions"><span class="pulse-pill good">✅ aktiv</span>'
                           f'<span class="an-rule-title">🌐 Globale Standardregel (Rückfall)</span><span class="guard-hint">{e(_short(rule, config))}</span></div>'
                           f'<div class="an-preview mt-2">{e(pm.describe_rule(rule, config))} Ereignisse mit eigener Regel zählen hier nicht mit. '
                           'Bearbeiten unter „Globale Einstellungen“.</div></div>')
    cards = []
    for rule in rules:
        cards.append(f'''<details class="an-rule" id="rule-{e(rule["id"])}"><summary>{_rule_pill(rule, config)}
<span class="an-rule-title">{e(rule["label"])}</span><span class="pulse-pill">{e(pm.RULE_SEVERITY_LABELS[rule["severity"]])}</span>
<span class="guard-hint">{e(_short(rule, config))}</span></summary><div class="an-body">{rule_form(guild, config, rule)}</div></details>''')
    presets = "".join(f'<option value="{k}">{e(v["label"])} – {e(_short(pm.normalize_rule({**v, "id": "x"}), config))}</option>' for k, v in PRESETS.items())
    rules_html = f'''
<section id="rules" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">📐 Individuelle Regeln</div>
<div class="pulse-section-sub">Jede Regel hat eigenen Zähler, eigene Schwelle, eigenes Zeitfenster und eigene Reaktion. Änderungen gelten sofort nach dem Speichern.</div></div>
<span class="pulse-pill">{len(rules)}/{pm.MAX_RULES}</span></div>
<div class="pulse-card-b">{rules_notice}{global_card}{"".join(cards) or '<div class="pulse-empty">Noch keine individuellen Regeln – Vorlage wählen oder neue Regel anlegen.</div>'}
 <form method="post" action="/admin/security/antinuke/rules/preset" class="an-actions mt-3"><input type="hidden" name="revision" value="{config["revision"]}">
  <label class="guard-field" style="flex:1 1 260px">Vorlage<select class="pulse-input" name="preset">{presets}</select></label>
  <button class="pulse-btn ghost" type="submit">➕ Aus Vorlage anlegen</button></form>
 <details class="an-rule mt-3" id="rule-new"><summary><span class="an-rule-title">➕ Neue Regel selbst anlegen</span></summary><div class="an-body">{rule_form(guild, config, None)}</div></details>
</div></section>'''

    # ---- trusted
    bot_id = getattr(me, "id", 0) or 0
    trusted_rows = [f'<tr><td>👑 {e(_member_name(guild, getattr(guild, "owner_id", 0))[0])}</td><td>{e(getattr(guild, "owner_id", ""))}</td><td>Serverinhaber – immer geschützt</td></tr>',
                    f'<tr><td>🤖 Pulse</td><td>{e(bot_id or "–")}</td><td>Bot selbst – immer geschützt</td></tr>']
    for uid in config["trusted_user_ids"]:
        name, found = _member_name(guild, uid)
        trusted_rows.append(f'<tr><td>{"👤" if found else "⚠"} {e(name)}</td><td>{uid}</td><td>freigestellte Person</td></tr>')
    for rid in config["trusted_role_ids"]:
        name, found = _role_name(guild, rid)
        trusted_rows.append(f'<tr><td>{"🏷" if found else "⚠"} {e(name)}</td><td>{rid}</td><td>Vertrauensrolle</td></tr>')
    trusted_html = f'''
<section id="trusted" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">🤝 Vertrauenswürdige Personen &amp; Rollen</div>
<div class="pulse-section-sub">Gelten nur für Anti-Nuke: wer hier steht, löst keine Regel aus. Sie geben keinerlei Dashboard-Rechte. Ein übernommenes Konto mit Vertrauensstellung wird nicht gestoppt – sparsam vergeben.</div></div></div>
<div class="pulse-card-b">{trusted_notice}
 <div style="overflow-x:auto"><table class="an-table"><thead><tr><th>Name</th><th>ID</th><th>Art</th></tr></thead><tbody>{"".join(trusted_rows)}</tbody></table></div>
 <form method="post" action="/admin/security/antinuke/trusted" class="guard-form mt-3"><input type="hidden" name="revision" value="{config["revision"]}">
  <div class="guard-grid">
   <label class="guard-field">Vertrauensrollen<select class="pulse-input guard-select" name="trusted_role_ids" multiple>{_role_options(guild, config["trusted_role_ids"])}</select>
    <span class="guard-hint">⚠ = Rolle mit gefährlichen Rechten. Strg/⌘ für Mehrfachauswahl.</span></label>
   <label class="guard-field">Freigestellte Personen (Discord-IDs)<textarea class="pulse-input" name="trusted_user_ids" rows="4" placeholder="Eine ID pro Zeile oder durch Komma getrennt">{e(chr(10).join(str(v) for v in config["trusted_user_ids"]))}</textarea></label>
  </div>
  <div class="an-actions"><button class="pulse-btn ghost" type="submit" formaction="/admin/security/antinuke/trusted/check">🔎 Vor dem Speichern prüfen</button>
   <button class="pulse-btn primary" type="submit">💾 Ausnahmen speichern</button></div></form>
</div></section>'''

    # ---- lockdown
    if lockdown.get("active"):
        names = ", ".join(f"#{e(v.get('name', k))}" for k, v in (lockdown.get("channels") or {}).items() if isinstance(v, dict))
        lock_body = (f'<div class="pulse-alert" style="border-color:rgba(240,68,56,.45)"><strong>🔒 Lockdown aktiv</strong> seit {e(lockdown.get("at", "")[:16].replace("T", " "))} UTC · '
                     f'durch {e(lockdown.get("by"))}<div class="guard-hint">Gesperrt: {names or "–"}</div><div class="guard-hint">Grund: {e(lockdown.get("reason"))}</div></div>'
                     '<form method="post" action="/admin/security/lockdown/lift" class="an-actions mt-3" '
                     "onsubmit=\"return window.confirm('Lockdown aufheben und die gespeicherten Kanalrechte wiederherstellen?')\">"
                     '<input class="pulse-input" name="reason" required minlength="5" maxlength="300" placeholder="Grund (Pflicht)" style="max-width:320px">'
                     '<button class="pulse-btn good" type="submit">🔓 Lockdown aufheben</button></form>')
    else:
        configured = ", ".join(("#" + e(guild.get_channel(cid).name)) if hasattr(guild, "get_channel") and guild.get_channel(cid) is not None else f"⚠ {cid}"
                               for cid in config["lockdown_channel_ids"]) or "keine – unter „Globale Einstellungen“ auswählen"
        lock_body = (f'<div class="guard-kv"><span>Lockdown-Kanäle</span><span>{configured}</span></div>'
                     + (f'<div class="guard-hint">Zuletzt aufgehoben: {e(lockdown.get("lifted_at", "")[:16].replace("T", " "))} UTC durch {e(lockdown.get("lifted_by"))}</div>' if lockdown.get("lifted_at") else "")
                     + ('<form method="post" action="/admin/security/lockdown/start" class="an-actions mt-3" '
                        "onsubmit=\"if(!window.confirm('Notfall-Lockdown jetzt starten? @everyone kann in den ausgewählten Kanälen nicht mehr schreiben.'))return false;this.confirm.value='1';\">"
                        '<input type="hidden" name="confirm" value="0"><input class="pulse-input" name="reason" required minlength="5" maxlength="300" placeholder="Grund (Pflicht)" style="max-width:320px">'
                        '<button class="pulse-btn ghost" type="submit">🔒 Lockdown manuell starten</button></form>' if config["lockdown_channel_ids"] else ""))
    lockdown_html = f'''
<section id="lockdown" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">🔒 Notfall-Lockdown</div>
<div class="pulse-section-sub">Entzieht @everyone in den ausgewählten Kanälen Schreiben, Reaktionen, Threads bzw. Verbinden/Sprechen. Die vorherigen Rechte werden gespeichert und beim Aufheben genau so wiederhergestellt.</div></div></div>
<div class="pulse-card-b">{lock_body}</div></section>'''
    return CSS + overview + rules_html + trusted_html + lockdown_html + PREVIEW_JS


def global_form_extras(guild, config: dict) -> str:
    """Fields of the global form that are new in the Control Center (posted with form_version=2)."""
    mode = "".join(
        f'<label class="guard-check"><input type="radio" name="mode" value="{key}"{" checked" if config["mode"] == key else ""}>'
        f'<span><strong>{e(text)}</strong><small>{"Sicherer Start: zählt und meldet, sanktioniert nie." if key == "alert" else "Führt Rollenentzug, Kick, Bann und Lockdown laut Regeln wirklich aus."}</small></span></label>'
        for key, text in pm.ANTINUKE_MODE_LABELS.items())
    missing = "".join(f'<option value="{k}"{" selected" if config["on_missing_permissions"] == k else ""}>{v}</option>'
                      for k, v in (("alert", "Alarm senden und übrige Schritte fortsetzen"), ("log", "Nur protokollieren und fortsetzen")))
    return f'''<input type="hidden" name="form_version" value="2"><input type="hidden" name="revision" value="{config["revision"]}">
 <input type="hidden" name="confirm_protect" value="0">
 <div class="pulse-section-title" style="margin-top:6px">Betriebsmodus</div>
 <div class="guard-grid">{mode}</div>
 <label class="guard-check"><input type="checkbox" name="global_rule_enabled" value="1"{" checked" if config["global_rule_enabled"] else ""}><span><strong>Globale Standardregel verwenden</strong><small>Zählt alle unten gewählten Ereignisse gemeinsam (bisheriges Verhalten). Ereignisse mit eigener Regel zählen nur dort.</small></span></label>
 <div class="guard-grid">
  {_num("rule_cooldown_seconds", "Regel-Cooldown (Sekunden)", 30, 86400, config["rule_cooldown_seconds"], "Standard für Regeln ohne eigenen Cooldown.")}
  {_num("action_cooldown_seconds", "Aktions-Cooldown (Sekunden)", 30, 86400, config["action_cooldown_seconds"], "Verhindert doppelte Banns/Kicks/Rollenentzüge derselben Person über mehrere Regeln.")}
  <label class="guard-field">Bei fehlenden Bot-Rechten<select class="pulse-input" name="on_missing_permissions">{missing}</select></label>
 </div>
 <div class="guard-grid">
  <label class="guard-field">Verwaltungsrolle (für „Verwaltungsrolle erwähnen“)<select class="pulse-input" name="manager_role_id">{_role_options(guild, config["manager_role_id"], blank="— keine —")}</select></label>
  <label class="guard-field">Lockdown-Kanäle (max. {pm.MAX_LOCKDOWN_CHANNELS})<select class="pulse-input guard-select" name="lockdown_channel_ids" multiple>{_lockdown_options(guild, config["lockdown_channel_ids"])}</select></label>
  <label class="guard-field">Entziehbare Rollen bei „Rollen entziehen“<select class="pulse-input guard-select" name="strip_role_ids" multiple>{_role_options(guild, config["strip_role_ids"])}</select>
   <span class="guard-hint">Leer = alle Rollen mit gefährlichen Rechten unterhalb der Bot-Rolle (bisheriges Verhalten). Sonst ausschließlich diese Rollen.</span></label>
 </div>'''


def parse_global_extras(form, guild, old: dict) -> dict:
    """Validated values of global_form_extras. Raises ValueError with a readable message."""
    role_ids = {r.id for r in getattr(guild, "roles", []) or []}
    channels = {c.id for c in list(getattr(guild, "text_channels", []) or []) + list(getattr(guild, "voice_channels", []) or [])}
    manager_raw = str(form.get("manager_role_id") or "").strip()
    if manager_raw and (not manager_raw.isdigit() or int(manager_raw) not in role_ids | {old["manager_role_id"]} - {0}):
        raise ValueError("Verwaltungsrolle: Unbekannte Auswahl – bitte die Seite neu laden.")
    lockdown = _parse_select(form, "lockdown_channel_ids", "Lockdown-Kanäle", channels | set(old["lockdown_channel_ids"]))
    if len(lockdown) > pm.MAX_LOCKDOWN_CHANNELS:
        raise ValueError(f"Lockdown-Kanäle: höchstens {pm.MAX_LOCKDOWN_CHANNELS}.")
    missing = form.get("on_missing_permissions") or "alert"
    if missing not in pm.MISSING_PERMISSION_BEHAVIOURS:
        raise ValueError("Verhalten bei fehlenden Rechten: Unbekannte Auswahl.")
    return {
        "global_rule_enabled": form.get("global_rule_enabled") == "1",
        "rule_cooldown_seconds": _parse_int(form, "rule_cooldown_seconds", "Regel-Cooldown", 30, 86400),
        "action_cooldown_seconds": _parse_int(form, "action_cooldown_seconds", "Aktions-Cooldown", 30, 86400),
        "on_missing_permissions": missing,
        "manager_role_id": int(manager_raw) if manager_raw else 0,
        "lockdown_channel_ids": lockdown,
        "strip_role_ids": _parse_select(form, "strip_role_ids", "Entziehbare Rollen", role_ids | set(old["strip_role_ids"])),
    }


def _optional(form, field: str, label: str, lo: int, hi: int):
    if not str(form.get(field) or "").strip():
        return None
    return _parse_int(form, field, label, lo, hi)


def parse_rule(form, guild, config: dict, existing: dict | None) -> dict:
    """Validated raw rule from the editor form. Raises ValueError."""
    label = str(form.get("label") or "").strip()
    if not 1 <= len(label) <= 80:
        raise ValueError("Bezeichnung: 1 bis 80 Zeichen.")
    events = [x for x in form.getlist("events")]
    if not events or any(x not in pm.ANTINUKE_EVENTS for x in events):
        raise ValueError("Bitte mindestens ein unterstütztes Ereignis wählen.")
    actions = [x for x in form.getlist("actions")]
    if any(x not in pm.RULE_ACTIONS for x in actions):
        raise ValueError("Unbekannte Aktion – bitte die Seite neu laden.")
    if "kick" in actions and "ban" in actions:
        raise ValueError("Bitte entweder Kick oder Bann wählen, nicht beides.")
    if "lockdown" in actions and not config["lockdown_channel_ids"]:
        raise ValueError("Für „Notfall-Lockdown“ zuerst unter „Globale Einstellungen“ Lockdown-Kanäle auswählen.")
    if "notify_role" in actions and not config["manager_role_id"]:
        raise ValueError("Für „Verwaltungsrolle erwähnen“ zuerst unter „Globale Einstellungen“ eine Verwaltungsrolle auswählen.")
    severity = form.get("severity") or "high"
    if severity not in pm.RULE_SEVERITIES:
        raise ValueError("Schweregrad: Unbekannte Auswahl.")
    role_ids = {r.id for r in getattr(guild, "roles", []) or []} | set((existing or {}).get("exempt_role_ids", []))
    new_critical = CRITICAL_ACTIONS & set(actions) - set((existing or {}).get("actions", []))
    if new_critical and form.get("confirm_critical") != "1":
        raise ValueError("Kick, Bann und Lockdown müssen beim Speichern ausdrücklich bestätigt werden.")
    return {
        "id": (existing or {}).get("id") or f"r_{secrets.token_hex(4)}",
        "enabled": form.get("enabled") == "1",
        "label": label,
        "events": list(dict.fromkeys(events)),
        "limit": _optional(form, "limit", "Anzahl Aktionen", pm.RULE_MIN_LIMIT, pm.RULE_MAX_LIMIT),
        "window_seconds": _optional(form, "window_seconds", "Zeitfenster", pm.RULE_MIN_WINDOW, pm.RULE_MAX_WINDOW),
        "cooldown_seconds": _optional(form, "cooldown_seconds", "Cooldown", 0, 86400),
        "actions": ["log", *[a for a in pm.RULE_ACTIONS if a in actions and a != "log"]],
        "severity": severity,
        "test_mode": form.get("test_mode") == "1",
        "exempt_role_ids": _parse_select(form, "exempt_role_ids", "Ausgenommene Rollen", role_ids),
        "exempt_user_ids": _parse_user_ids(form, "exempt_user_ids", "Ausgenommene Personen"),
    }


def check_trusted(guild, form) -> str:
    """HTML report for „Vor dem Speichern prüfen“ – nothing is stored."""
    items = []
    for value in [v for v in re.split(r"[\s,;]+", str(form.get("trusted_user_ids") or "").strip()) if v]:
        if not _USER_ID.fullmatch(value):
            items.append(f'<li class="an-bad">✗ „{e(value[:40])}“ ist keine gültige Discord-ID (15–22 Ziffern).</li>')
            continue
        name, found = _member_name(guild, value)
        if int(value) == getattr(guild, "owner_id", 0):
            items.append(f"<li>ℹ️ {e(value)} ist der Serverinhaber – ohnehin immer geschützt.</li>")
        elif found:
            items.append(f'<li class="an-ok">✓ {e(value)} → {e(name)}</li>')
        else:
            items.append(f'<li class="an-warn">⚠ {e(value)} ist gültig, aber nicht (mehr) auf dem Server.</li>')
    roles = {r.id: r for r in getattr(guild, "roles", []) or []}
    for raw in form.getlist("trusted_role_ids"):
        role = roles.get(int(raw)) if str(raw).isdigit() else None
        if role is None:
            items.append(f'<li class="an-bad">✗ Rolle {e(raw)} existiert nicht.</li>')
            continue
        members = len(getattr(role, "members", []) or [])
        warn = " – hat gefährliche Rechte" if pm.has_dangerous_permission(getattr(role, "permissions", None)) else ""
        items.append(f'<li class="{"an-warn" if warn or members > 10 else "an-ok"}">{"⚠" if warn or members > 10 else "✓"} @{e(role.name)} ({members} Mitglieder){warn}</li>')
    body = "".join(items) or "<li>Keine Ausnahmen eingetragen – nur Serverinhaber und Pulse sind geschützt.</li>"
    return f'<div class="pulse-alert mb-3"><strong>🔎 Prüfergebnis (nicht gespeichert)</strong><ul class="mt-1" style="padding-left:18px;line-height:1.7">{body}</ul></div>'


# --------------------------------------------------------------------------- routes
def register(app, render_page) -> None:
    """render_page(ctx, **antinuke_kwargs) -> HTMLResponse of /admin/security (from pulse_guard_admin)."""
    import webserver as ws

    def admin_ctx(request, session):
        return ws.auth(request, session, perm=None, admin=True)

    def audit(ctx, action: str, details: str) -> None:
        ws.log_audit(_name(ctx.user), ctx.user["id"], action, details[:1500])

    def find(raw_rules, rule_id):
        for index, item in enumerate(raw_rules):
            if isinstance(item, dict) and str(item.get("id", "")).lower() == rule_id:
                return index
        return None

    def rule_id_of(form) -> str:
        value = str(form.get("rule_id") or "").strip().lower()
        if value and not re.fullmatch(r"[a-z0-9_-]{1,32}", value):
            raise ValueError("Ungültige Regel-ID.")
        return value

    def stored_rules(raw: dict) -> list:
        return list(raw.get("rules")) if isinstance(raw.get("rules"), list) else []

    @app.post("/admin/security/antinuke/rules/save")
    async def antinuke_rule_save(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        try:
            rule_id = rule_id_of(form)
            current = pm.normalize_antinuke(raw_section(ws.load_config()))
            existing = next((r for r in current["rules"] if r["id"] == rule_id), None) if rule_id else None
            if rule_id and existing is None:
                raise ValueError("Diese Regel gibt es nicht (mehr) – bitte die Seite neu laden.")
            if not rule_id and len(current["rules"]) >= pm.MAX_RULES:
                raise ValueError(f"Höchstens {pm.MAX_RULES} Regeln.")
            value = parse_rule(form, ctx.guild, current, existing)

            def mutate(raw, _before):
                rules = stored_rules(raw)
                index = find(rules, value["id"])
                if index is None:
                    rules.append(value)
                else:
                    rules[index] = value
                raw["rules"] = rules
                return raw
            _, after = save(ws, ctx.user, mutate, expected_revision=form_revision(form))
        except (ValueError, Conflict) as exc:
            return ws.back("/admin/security#rules", str(exc), False)
        except ws.JsonWriteBlocked as exc:
            return ws.back("/admin/security#rules", f"Nicht gespeichert: {exc}", False)
        saved = next(r for r in after["rules"] if r["id"] == value["id"])
        audit(ctx, "Anti-Nuke-Regel geändert" if existing else "Anti-Nuke-Regel erstellt",
              f"{saved['label']} ({saved['id']}) · {'aktiv' if saved['enabled'] else 'aus'}{' · Testmodus' if saved['test_mode'] else ''} · "
              + pm.describe_rule(saved, after))
        return ws.back(f"/admin/security#rule-{value['id']}", f"Regel „{saved['label']}“ gespeichert – gilt sofort.", True)

    async def simple_rule_change(request, user_session, change: str):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        try:
            rule_id = rule_id_of(form)
            if not rule_id:
                raise ValueError("Keine Regel ausgewählt.")
            if change == "delete" and form.get("confirm_delete") != "1":
                raise ValueError("Löschen bitte bestätigen.")
            holder = {}

            def mutate(raw, _before):
                rules = stored_rules(raw)
                index = find(rules, rule_id)
                if index is None:
                    raise ValueError("Diese Regel gibt es nicht (mehr) – bitte die Seite neu laden.")
                holder["label"] = str(rules[index].get("label") or rule_id)[:80]
                if change == "delete":
                    rules.pop(index)
                else:
                    holder["enabled"] = not bool(rules[index].get("enabled", True))
                    rules[index] = {**rules[index], "enabled": holder["enabled"]}
                raw["rules"] = rules
                return raw
            save(ws, ctx.user, mutate, expected_revision=form_revision(form))
        except (ValueError, Conflict) as exc:
            return ws.back("/admin/security#rules", str(exc), False)
        except ws.JsonWriteBlocked as exc:
            return ws.back("/admin/security#rules", f"Nicht gespeichert: {exc}", False)
        if change == "delete":
            audit(ctx, "Anti-Nuke-Regel gelöscht", f"{holder['label']} ({rule_id})")
            return ws.back("/admin/security#rules", f"Regel „{holder['label']}“ gelöscht.", True)
        audit(ctx, "Anti-Nuke-Regel " + ("aktiviert" if holder["enabled"] else "deaktiviert"), f"{holder['label']} ({rule_id})")
        return ws.back(f"/admin/security#rule-{rule_id}", f"Regel „{holder['label']}“ {'aktiviert' if holder['enabled'] else 'deaktiviert'}.", True)

    @app.post("/admin/security/antinuke/rules/toggle")
    async def antinuke_rule_toggle(request: Request, user_session: str = Cookie(None)):
        return await simple_rule_change(request, user_session, "toggle")

    @app.post("/admin/security/antinuke/rules/delete")
    async def antinuke_rule_delete(request: Request, user_session: str = Cookie(None)):
        return await simple_rule_change(request, user_session, "delete")

    @app.post("/admin/security/antinuke/rules/preset")
    async def antinuke_rule_preset(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        preset = PRESETS.get(str(form.get("preset") or ""))
        if preset is None:
            return ws.back("/admin/security#rules", "Unbekannte Vorlage.", False)
        value = {**preset, "id": f"r_{secrets.token_hex(4)}", "enabled": True, "test_mode": False}

        def mutate(raw, before):
            if len(before["rules"]) >= pm.MAX_RULES:
                raise ValueError(f"Höchstens {pm.MAX_RULES} Regeln.")
            raw["rules"] = [*stored_rules(raw), value]
            return raw
        try:
            _, after = save(ws, ctx.user, mutate, expected_revision=form_revision(form))
        except (ValueError, Conflict) as exc:
            return ws.back("/admin/security#rules", str(exc), False)
        except ws.JsonWriteBlocked as exc:
            return ws.back("/admin/security#rules", f"Nicht gespeichert: {exc}", False)
        saved = next(r for r in after["rules"] if r["id"] == value["id"])
        audit(ctx, "Anti-Nuke-Regel erstellt", f"Vorlage {saved['label']} ({saved['id']}) · {pm.describe_rule(saved, after)}")
        hint = " Achtung: Alarmmodus – Sanktionen werden nur gemeldet." if after["mode"] != "protect" else ""
        return ws.back(f"/admin/security#rule-{value['id']}", f"Regel „{saved['label']}“ angelegt – gilt sofort.{hint}", True)

    @app.post("/admin/security/antinuke/rules/simulate", response_class=HTMLResponse)
    async def antinuke_rule_simulate(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        config = pm.normalize_antinuke(raw_section(ws.load_config()))
        rule = next((r for r in config["rules"] if r["id"] == str(form.get("rule_id") or "").strip().lower()), None)
        try:
            count = _parse_int(form, "count", "Anzahl Testaktionen", 1, 100)
        except ValueError as exc:
            return ws.back("/admin/security#rules", str(exc), False)
        if rule is None:
            return ws.back("/admin/security#rules", "Diese Regel gibt es nicht (mehr).", False)
        result = pm.simulate_rule(rule, config, count)
        steps = "".join(f"<li><strong>{e(pm.RULE_ACTION_LABELS[a])}</strong> – {e(s)}</li>" for a, s in result["steps"])
        tone = "rgba(240,68,56,.4)" if result["triggered"] else "rgba(18,183,106,.35)"
        html_out = (f'<div class="pulse-alert mb-3" style="border-color:{tone}"><strong>🧪 Test „{e(rule["label"])}“ mit {count} Aktion(en): '
                    f'{"würde auslösen" if result["triggered"] else "würde nicht auslösen"}</strong>'
                    + (f'<ul class="mt-1" style="padding-left:18px">{steps}</ul>' if steps else "")
                    + (f'<div class="guard-hint">{e(result["note"])}</div>' if result["note"] else "")
                    + '<div class="guard-hint">Simulation – es wurde nichts an Discord gesendet, gespeichert oder protokolliert.</div></div>')
        return render_page(ctx, rules_notice=html_out)

    @app.post("/admin/security/antinuke/trusted")
    async def antinuke_trusted_save(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        current = pm.normalize_antinuke(raw_section(ws.load_config()))
        try:
            role_ids = _parse_select(form, "trusted_role_ids", "Vertrauensrollen",
                                     {r.id for r in getattr(ctx.guild, "roles", []) or []} | set(current["trusted_role_ids"]))
            user_ids = _parse_user_ids(form, "trusted_user_ids", "Freigestellte Personen")

            def mutate(raw, _before):
                raw["trusted_role_ids"], raw["trusted_user_ids"] = role_ids, user_ids
                return raw
            before, _ = save(ws, ctx.user, mutate, expected_revision=form_revision(form))
        except (ValueError, Conflict) as exc:
            return ws.back("/admin/security#trusted", str(exc), False)
        except ws.JsonWriteBlocked as exc:
            return ws.back("/admin/security#trusted", f"Nicht gespeichert: {exc}", False)
        changes = []
        for label, old, new in (("Personen", before["trusted_user_ids"], user_ids), ("Rollen", before["trusted_role_ids"], role_ids)):
            added, removed = [x for x in new if x not in old], [x for x in old if x not in new]
            if added:
                changes.append(f"{label} hinzugefügt: {', '.join(map(str, added))}")
            if removed:
                changes.append(f"{label} entfernt: {', '.join(map(str, removed))}")
        audit(ctx, "Anti-Nuke-Ausnahmen geändert", " · ".join(changes) or "keine Änderung")
        return ws.back("/admin/security#trusted", "Ausnahmen gespeichert – gelten sofort.", True)

    @app.post("/admin/security/antinuke/trusted/check", response_class=HTMLResponse)
    async def antinuke_trusted_check(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        return render_page(ctx, trusted_notice=check_trusted(ctx.guild, form))

    async def lockdown_action(request, user_session, start: bool):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        reason = str(form.get("reason") or "").strip()[:300]
        if len(reason) < 5:
            return ws.back("/admin/security#lockdown", "Bitte einen Grund (mindestens 5 Zeichen) angeben.", False)
        from cogs.antinuke import lift_lockdown, start_lockdown
        import pulse_db
        if start:
            if form.get("confirm") != "1":
                return ws.back("/admin/security#lockdown", "Lockdown bitte ausdrücklich bestätigen.", False)
            config = pm.normalize_antinuke(raw_section(ws.load_config()))
            result = await start_lockdown(ctx.guild, config["lockdown_channel_ids"], by=_name(ctx.user),
                                          reason=f"Pulse: Notfall-Lockdown durch {_name(ctx.user)} – {reason}")
            ok = result["status"] in ("ok", "partial")
            title, message = "Anti-Nuke: Lockdown gestartet", result["detail"]
        else:
            result = await lift_lockdown(ctx.guild, by=_name(ctx.user))
            if result["error"]:
                return ws.back("/admin/security#lockdown", result["error"], False)
            ok = not result["failed"]
            title = "Anti-Nuke: Lockdown aufgehoben"
            message = f"Wiederhergestellt: {', '.join(result['restored']) or 'keine'}" + (f" · fehlgeschlagen: {', '.join(result['failed'])}" if result["failed"] else "")
        if ok or not start:
            try:
                pulse_db.record_event(title, "guild", getattr(ctx.guild, "id", ""), ctx.user["id"], _name(ctx.user),
                                      {"reason": reason, "result": message[:500], "manual": True})
            except Exception as exc:
                print(f"⚠️ Lockdown-Ereignis nicht protokolliert: {type(exc).__name__}")
        audit(ctx, title if ok else title + " (fehlgeschlagen)", f"{message} · Grund: {reason}")
        return ws.back("/admin/security#lockdown", message[:400], ok)

    @app.post("/admin/security/lockdown/start")
    async def antinuke_lockdown_start(request: Request, user_session: str = Cookie(None)):
        return await lockdown_action(request, user_session, True)

    @app.post("/admin/security/lockdown/lift")
    async def antinuke_lockdown_lift(request: Request, user_session: str = Cookie(None)):
        return await lockdown_action(request, user_session, False)
