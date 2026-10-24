"""Verwaltung → Sicherheit: AutoMod and Anti-Nuke management at /admin/security (administrators only).

GET  /admin/security                       overview: readiness checks, statistics, both configurations,
                                           rule tester, quarantine list and incident log
POST /admin/security/automod               save AutoMod (config.json → "pulse_automod")
POST /admin/security/antinuke              save Anti-Nuke (config.json → "pulse_antinuke")
POST /admin/security/test                  dry-run a sample message against the saved AutoMod rules
POST /admin/security/quarantine/restore    give a quarantined person their removed roles back (reason required)
POST /admin/security/quarantine/dismiss    drop a quarantine entry without restoring roles (reason required)

Every route authorizes server-side with admin=True. Values are normalised by pulse_moderation, exactly as
the cogs read them. The old page /security redirects here; its POST form keeps working (webserver.py).
Sample messages from the tester are never stored or logged.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Request
from fastapi.responses import HTMLResponse

import pulse_moderation as pm

INCIDENT_TYPES = (
    "AutoMod-Maßnahme", "Anti-Nuke-Aktion erkannt", "Anti-Nuke ausgelöst", "Anti-Nuke: Akteur gesperrt",
    "Anti-Nuke: Akteur gekickt", "Anti-Nuke: Quarantäne", "Anti-Nuke: Bot entfernt",
    "Anti-Nuke: Quarantäne aufgehoben", "Anti-Nuke: Quarantäne verworfen",
    "Anti-Nuke: Lockdown gestartet", "Anti-Nuke: Lockdown aufgehoben",
)
INCIDENT_LIMIT = 60
REQUIRED_PERMISSIONS = (
    ("view_audit_log", "Audit-Log anzeigen", "Anti-Nuke"),
    ("manage_messages", "Nachrichten verwalten", "AutoMod"),
    ("moderate_members", "Mitglieder im Timeout", "AutoMod"),
    ("manage_roles", "Rollen verwalten", "Anti-Nuke-Quarantäne"),
    ("kick_members", "Mitglieder kicken", "Anti-Nuke"),
    ("ban_members", "Mitglieder bannen", "Anti-Nuke"),
)
_USER_ID = re.compile(r"^[0-9]{15,22}$")

GUARD_CSS = """<style>
.guard-nav{display:flex;flex-wrap:wrap;gap:7px;margin:14px 0 18px}
.guard-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:12px}
.guard-form{display:grid;gap:12px}
.guard-field{display:grid;gap:5px;font-size:12px;font-weight:650}
.guard-hint{font-size:11px;color:var(--muted);font-weight:400;line-height:1.5}
.guard-check{display:flex;align-items:flex-start;gap:9px;font-size:12px;padding:10px;border:1px solid var(--border);border-radius:12px;background:var(--surface2)}
.guard-check input{margin-top:2px;width:16px;height:16px;flex:0 0 auto}
.guard-check small{display:block;color:var(--muted);font-size:11px;margin-top:2px}
.guard-select{min-height:120px}
.guard-steps{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.guard-kv{display:flex;justify-content:space-between;gap:10px;font-size:12px;padding:7px 0;border-bottom:1px dashed var(--border)}
.guard-kv:last-child{border-bottom:0}
.guard-big{font-size:26px;font-weight:850}
section[id]{scroll-margin-top:80px}
</style>"""


def _name(user: dict) -> str:
    return str(user.get("global_name") or user.get("username") or user.get("id") or "?")


def _parse_int(form, field: str, label: str, lo: int, hi: int) -> int:
    raw = str(form.get(field) or "").strip()
    if not re.fullmatch(r"-?\d{1,6}", raw):
        raise ValueError(f"{label}: Bitte eine ganze Zahl zwischen {lo} und {hi} eingeben.")
    value = int(raw)
    if not lo <= value <= hi:
        raise ValueError(f"{label} muss zwischen {lo} und {hi} liegen.")
    return value


def _parse_lines(form, field: str, label: str, *, domains: bool = False) -> list[str]:
    values = list(dict.fromkeys(line.strip().casefold() for line in str(form.get(field) or "").splitlines() if line.strip()))
    if len(values) > pm.MAX_LIST_ITEMS or any(len(value) > 100 for value in values):
        raise ValueError(f"{label}: Maximal {pm.MAX_LIST_ITEMS} Einträge mit höchstens 100 Zeichen sind erlaubt.")
    if domains:
        values = [value.removeprefix("www.") for value in values]
        bad = [value for value in values if not pm.valid_domain(value)]
        if bad:
            raise ValueError(f"{label}: „{bad[0][:60]}“ ist kein gültiger Hostname. Bitte nur z. B. example.com angeben, keine URLs oder Wildcards.")
    return values


def _parse_select(form, field: str, label: str, allowed: set[int]) -> list[int]:
    values = []
    for raw in form.getlist(field):
        text = str(raw).strip()
        if not text:
            continue
        if not text.isdigit() or int(text) not in allowed:
            raise ValueError(f"{label}: Unbekannte Auswahl – bitte die Seite neu laden.")
        if int(text) not in values:
            values.append(int(text))
    if len(values) > pm.MAX_ID_ITEMS:
        raise ValueError(f"{label}: Höchstens {pm.MAX_ID_ITEMS} Einträge.")
    return values


def _parse_user_ids(form, field: str, label: str) -> list[int]:
    values = [v for v in re.split(r"[\s,;]+", str(form.get(field) or "").strip()) if v]
    if len(values) > pm.MAX_ID_ITEMS or any(not _USER_ID.fullmatch(v) for v in values):
        raise ValueError(f"{label}: Bitte nur Discord-IDs (15–22 Ziffern), höchstens {pm.MAX_ID_ITEMS}.")
    return list(dict.fromkeys(int(v) for v in values))


def _channel_id(form, field: str, guild, allowed_extra: set[int]) -> int:
    raw = str(form.get(field) or "").strip()
    if not raw:
        return 0
    if not raw.isdigit():
        raise ValueError("Alarmkanal: Bitte einen Kanal aus der Liste wählen.")
    cid = int(raw)
    if cid in allowed_extra:
        return cid
    if guild.get_channel(cid) is None:
        raise ValueError("Der Alarmkanal wurde auf diesem Discord-Server nicht gefunden.")
    return cid


def _changed(before: dict, after: dict) -> list[str]:
    return [key for key in after if before.get(key) != after.get(key)]


# --------------------------------------------------------------------------- readiness
def readiness(guild, automod: dict, antinuke: dict) -> dict:
    """Live checks against the guild: missing bot rights, role hierarchy gaps, alert channels."""
    me = getattr(guild, "me", None)
    perms = getattr(me, "guild_permissions", None)
    top = int(getattr(getattr(me, "top_role", None), "position", 0) or 0)
    missing = []
    try:  # Anti-Nuke needs exactly what its rules use (pulse_antinuke_admin.needed_permissions)
        from pulse_antinuke_admin import needed_permissions
        antinuke_needed = {key for key, _label, _used in needed_permissions(pm.normalize_antinuke(antinuke))}
    except Exception:
        antinuke_needed = {"view_audit_log"}
    for key, label, used_by in REQUIRED_PERMISSIONS:
        if perms is None or not getattr(perms, key, False):
            if used_by.startswith("Anti-Nuke") and key not in antinuke_needed:
                continue
            missing.append((label, used_by))
    roles = list(getattr(guild, "roles", []) or [])
    gaps = pm.hierarchy_gaps(roles, top) if me is not None else []
    dangerous_members = 0
    for member in getattr(guild, "members", []) or []:
        if getattr(member, "bot", False):
            continue
        if pm.has_dangerous_permission(getattr(member, "guild_permissions", None)):
            dangerous_members += 1
    channels = {}
    for label, section in (("AutoMod", automod), ("Anti-Nuke", antinuke)):
        cid = section.get("alert_channel_id") or 0
        channel = guild.get_channel(cid) if cid else None
        if not cid:
            channels[label] = ("warn", "kein Alarmkanal – Meldungen nur im Server-Log")
        elif channel is None:
            channels[label] = ("bad", f"Kanal {cid} nicht gefunden")
        else:
            can_send = True
            if me is not None and hasattr(channel, "permissions_for"):
                try:
                    p = channel.permissions_for(me)
                    can_send = bool(getattr(p, "send_messages", True) and getattr(p, "view_channel", True))
                except Exception:
                    can_send = True
            channels[label] = ("good" if can_send else "bad", f"#{getattr(channel, 'name', cid)}" + ("" if can_send else " – Bot darf dort nicht schreiben"))
    return {"bot_ready": me is not None, "missing": missing, "gaps": gaps, "dangerous_members": dangerous_members,
            "channels": channels}


def score(ready: dict, automod: dict, antinuke: dict) -> int:
    """0–100 protection score shown on the overview (simple, explainable)."""
    points = 0
    points += 30 if antinuke.get("enabled") else 0
    points += 10 if antinuke.get("enabled") and antinuke.get("mode") == "protect" else 0
    points += 25 if automod.get("enabled") else 0
    points += 5 if automod.get("enabled") and automod.get("mode") == "enforce" else 0
    points += 10 if not ready["missing"] else 0
    points += 10 if not ready["gaps"] else 0
    points += 10 if all(kind == "good" for kind, _ in ready["channels"].values()) else 0
    return min(100, points if ready["bot_ready"] else min(points, 40))


def incidents(guild, limit: int = INCIDENT_LIMIT) -> list[dict]:
    import json
    import pulse_db
    rows = []
    for kind in INCIDENT_TYPES:
        try:
            rows.extend(pulse_db.events(limit=limit, event_type=kind))
        except Exception as exc:
            print(f"⚠️ Sicherheitsvorfälle ({kind}) konnten nicht gelesen werden: {type(exc).__name__}")
    rows.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    out = []
    for row in rows[:limit]:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except ValueError:
            payload = {}
        out.append({**row, "payload": payload if isinstance(payload, dict) else {}})
    return out


def stats(rows: list[dict]) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    week = [r for r in rows if str(r.get("created_at", "")) >= since[:19]]
    by_rule: dict[str, int] = {}
    for r in week:
        if r.get("event_type") == "AutoMod-Maßnahme":
            rule = r["payload"].get("rule", "?")
            by_rule[rule] = by_rule.get(rule, 0) + 1
    return {
        "automod_7d": sum(by_rule.values()),
        "by_rule": by_rule,
        "antinuke_7d": sum(1 for r in week if r.get("event_type", "").startswith("Anti-Nuke") and r.get("event_type") != "Anti-Nuke-Aktion erkannt"),
        "detected_7d": sum(1 for r in week if r.get("event_type") == "Anti-Nuke-Aktion erkannt" and not r["payload"].get("trusted")),
    }


# --------------------------------------------------------------------------- routes
def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page

    e = ws.esc

    def admin_ctx(request, session):
        return ws.auth(request, session, perm=None, admin=True)

    def current(cfg: dict | None = None) -> tuple[dict, dict]:
        cfg = cfg if isinstance(cfg, dict) else ws.load_config()
        return pm.normalize_automod(cfg.get("pulse_automod")), pm.normalize_antinuke(cfg.get("pulse_antinuke"))

    def save_section(key: str, value: dict) -> dict:
        cfg = ws.load_config()
        before = cfg.get(key) if isinstance(cfg.get(key), dict) else {}
        cfg[key] = value
        ws.save_json(ws.CONFIG_FILE, cfg)
        return before

    def role_options(guild, selected: list[int], *, dangerous_hint: bool = False) -> str:
        roles = sorted((r for r in getattr(guild, "roles", []) or [] if not (hasattr(r, "is_default") and r.is_default())),
                       key=lambda r: -int(getattr(r, "position", 0)))
        known = {r.id for r in roles}
        out = []
        for role in roles:
            mark = " ⚠" if dangerous_hint and pm.has_dangerous_permission(getattr(role, "permissions", None)) else ""
            sel = " selected" if role.id in selected else ""
            out.append(f'<option value="{role.id}"{sel}>@{e(role.name)}{mark}</option>')
        out += [f'<option value="{rid}" selected>⚠ Unbekannte Rolle {rid}</option>' for rid in selected if rid not in known]
        return "".join(out)

    def channel_options(guild, selected: list[int], *, categories: bool = False) -> str:
        items = sorted(getattr(guild, "text_channels", []) or [], key=lambda c: (getattr(c, "position", 0), c.name))
        out, known = [], set()
        if categories:
            for cat in sorted(getattr(guild, "categories", []) or [], key=lambda c: getattr(c, "position", 0)):
                known.add(cat.id)
                out.append(f'<option value="{cat.id}"{" selected" if cat.id in selected else ""}>📁 {e(cat.name)} (ganze Kategorie)</option>')
        for ch in items:
            known.add(ch.id)
            out.append(f'<option value="{ch.id}"{" selected" if ch.id in selected else ""}>#{e(ch.name)}</option>')
        out += [f'<option value="{cid}" selected>⚠ Unbekannter Kanal {cid}</option>' for cid in selected if cid not in known]
        return "".join(out)

    def alert_select(guild, name: str, selected: int) -> str:
        opts = ['<option value="">— kein Alarmkanal —</option>']
        found = False
        for ch in sorted(getattr(guild, "text_channels", []) or [], key=lambda c: (getattr(c, "position", 0), c.name)):
            sel = ch.id == selected
            found = found or sel
            opts.append(f'<option value="{ch.id}"{" selected" if sel else ""}>#{e(ch.name)}</option>')
        if selected and not found:
            opts.append(f'<option value="{selected}" selected>⚠ Unbekannter Kanal {selected}</option>')
        return f'<select class="pulse-input" name="{name}">{"".join(opts)}</select>'

    def who(guild, uid) -> str:
        member = guild.get_member(int(uid)) if str(uid).isdigit() else None
        return e(getattr(member, "display_name", None) or str(uid or "–"))

    def where(guild, cid) -> str:
        channel = guild.get_channel(int(cid)) if str(cid).isdigit() else None
        return f"#{e(channel.name)}" if channel is not None else (f"Kanal {e(cid)}" if cid else "–")

    def page(ctx, test_result: str = "", test_text: str = "", **antinuke_kwargs) -> str:
        guild = ctx.guild
        automod, antinuke = current(ctx.config)
        ready = readiness(guild, automod, antinuke)
        rows = incidents(guild)
        st = stats(rows)
        total = score(ready, automod, antinuke)
        tone = "good" if total >= 80 else ("warn" if total >= 50 else "bad")
        try:
            from cogs.antinuke import load_quarantine
            quarantine = load_quarantine()
        except Exception:
            quarantine = {}

        # ---- overview
        checks = []
        if not ready["bot_ready"]:
            checks.append('<div class="pulse-alert"><strong>Bot-Mitglied nicht gefunden.</strong> Rechte und Rollenhierarchie können gerade nicht geprüft werden.</div>')
        for label, used_by in ready["missing"]:
            checks.append(f'<div class="pulse-alert" style="border-color:rgba(240,68,56,.35)"><strong>🔴 Bot-Recht fehlt: {e(label)}</strong>'
                          f'<div class="guard-hint">Benötigt für {e(used_by)}. In Discord: Servereinstellungen → Rollen → Bot-Rolle.</div></div>')
        if ready["gaps"]:
            names = ", ".join(f"@{e(r.name)}" for r in ready["gaps"][:8])
            checks.append(f'<div class="pulse-alert" style="border-color:rgba(247,144,9,.4)"><strong>🟠 Rollen über der Bot-Rolle mit gefährlichen Rechten:</strong> {names}'
                          '<div class="guard-hint">Gegen Personen mit diesen Rollen kann Pulse nichts unternehmen. Bot-Rolle in Discord darüber ziehen '
                          'oder die Rechte dieser Rollen verringern. Serverinhaber und echte Administratoren bleiben immer geschützt.</div></div>')
        if not checks:
            checks.append('<div class="pulse-alert" style="border-color:rgba(18,183,106,.35)"><strong>🟢 Bot-Rechte und Rollenhierarchie passen.</strong></div>')
        channel_rows = "".join(f'<div class="guard-kv"><span>Alarmkanal {e(label)}</span><span class="pulse-pill {kind}">{e(text)}</span></div>'
                               for label, (kind, text) in ready["channels"].items())
        raid_on = __import__("os").getenv("PULSE_RAID_WATCH", "1").strip() != "0"
        by_rule = "".join(f'<span class="pulse-pill">{e(pm.AUTOMOD_RULE_LABELS.get(k, k))}: {v}</span>' for k, v in sorted(st["by_rule"].items(), key=lambda kv: -kv[1])) or '<span class="guard-hint">keine</span>'
        overview = f'''
<section id="overview" class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">🧭 Schutzstatus</div>
<div class="pulse-section-sub">Live geprüft gegen den Discord-Server. Nichts auf dieser Seite ändert Discord, bevor du speicherst.</div></div>
<span class="pulse-pill {tone}">Schutzwert {total}/100</span></div>
<div class="pulse-card-b guard-grid">
 <div><div class="guard-kv"><span>AutoMod</span><span class="pulse-pill {"good" if automod["enabled"] else ""}">{("aktiv · " + ("durchsetzen" if automod["mode"] == "enforce" else "nur beobachten")) if automod["enabled"] else "aus"}</span></div>
  <div class="guard-kv"><span>Anti-Nuke</span><span class="pulse-pill {("good" if antinuke["mode"] == "protect" else "warn") if antinuke["enabled"] else ""}">{("aktiv · " + ("Aktiver Schutz" if antinuke["mode"] == "protect" else "Alarmmodus")) if antinuke["enabled"] else "aus"}</span></div>
  <div class="guard-kv"><span>Raid-Frühwarnung</span><span class="pulse-pill {"good" if raid_on else ""}">{"aktiv (nur Alarm)" if raid_on else "aus"}</span></div>
  {channel_rows}
  <div class="guard-kv"><span>Personen mit gefährlichen Rechten</span><strong>{ready["dangerous_members"]}</strong></div></div>
 <div><div class="guard-kv"><span>AutoMod-Fälle (7 Tage)</span><span class="guard-big">{st["automod_7d"]}</span></div>
  <div class="guard-steps">{by_rule}</div>
  <div class="guard-kv" style="margin-top:8px"><span>Anti-Nuke-Reaktionen (7 Tage)</span><span class="guard-big">{st["antinuke_7d"]}</span></div>
  <div class="guard-kv"><span>Erkannte Aktionen nicht freigegebener Personen</span><strong>{st["detected_7d"]}</strong></div>
  <div class="guard-kv"><span>Aktuell in Quarantäne</span><strong>{len(quarantine)}</strong></div></div>
 <div class="space-y-2">{"".join(checks)}</div>
</div></section>'''

        # ---- AutoMod form
        rule_desc = {
            "spam": "Spam und Nachrichtenflut: zu viele oder wiederholte Nachrichten in kurzer Zeit.",
            "mentions": "Massen-Erwähnungen über der Grenze (inkl. @everyone-Versuch).",
            "invites": "Discord-Einladungen zu anderen Servern.",
            "keywords": "Begriffe aus dem Wortfilter (ganze Wörter, Groß-/Kleinschreibung egal).",
            "domains": "Links zu Domains aus der Sperrliste (inkl. Subdomains).",
            "links": "Jeder Link, der nicht auf der Freigabeliste steht.",
            "caps": "Nachrichten fast nur in GROSSBUCHSTABEN.",
        }
        rules = "".join(
            f'<label class="guard-check"><input type="checkbox" name="automod_rules" value="{key}"{" checked" if key in automod["rules"] else ""}>'
            f'<span><strong>{e(pm.AUTOMOD_RULE_LABELS[key])}</strong><small>{e(rule_desc[key])}</small></span></label>'
            for key in pm.AUTOMOD_RULE_ORDER)
        steps = "".join(f'<span class="pulse-pill">{i}. {e(s)}</span>' for i, s in enumerate(pm.escalation_preview(automod), 1))
        mode_radio = "".join(
            f'<label class="guard-check"><input type="radio" name="mode" value="{key}"{" checked" if automod["mode"] == key else ""}>'
            f'<span><strong>{label}</strong><small>{hint}</small></span></label>'
            for key, label, hint in (("enforce", "Durchsetzen", "Nachricht löschen, warnen, bei Wiederholung Timeout."),
                                     ("observe", "Nur beobachten", "Nichts löschen oder bestrafen – nur protokollieren. Ideal zum Einstellen neuer Regeln.")))

        def num(name, label, lo, hi, value, hint=""):
            return (f'<label class="guard-field">{label}<input class="pulse-input" type="number" name="{name}" min="{lo}" max="{hi}" value="{value}" required>'
                    + (f'<span class="guard-hint">{hint}</span>' if hint else "") + '</label>')

        automod_form = f'''
<section id="automod" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">💬 AutoMod</div>
<div class="pulse-section-sub">Prüft Nachrichten lokal (ohne KI, ohne Kosten). Serverinhaber, Personen mit „Nachrichten verwalten“, Bots und Ausnahmen werden nie geprüft.</div></div>
<span class="pulse-pill {"good" if automod["enabled"] else ""}">{"aktiv" if automod["enabled"] else "aus"}</span></div>
<div class="pulse-card-b"><form method="post" action="/admin/security/automod" class="guard-form">
 <label class="guard-check"><input type="checkbox" name="automod_enabled" value="1"{" checked" if automod["enabled"] else ""}><span><strong>AutoMod aktivieren</strong></span></label>
 <div class="guard-grid">{mode_radio}</div>
 <div class="pulse-section-title" style="margin-top:6px">Regeln</div>
 <div class="guard-grid">{rules}</div>
 <div class="guard-grid">
  {num("mention_limit", "Erwähnungen pro Nachricht (max.)", 3, 30, automod["mention_limit"])}
  {num("flood_messages", "Nachrichtenflut: Anzahl", 3, 20, automod["flood_messages"], "So viele Nachrichten …")}
  {num("flood_seconds", "… innerhalb von Sekunden", 3, 60, automod["flood_seconds"])}
  {num("duplicate_limit", "Gleiche Nachricht wiederholt (ab)", 2, 10, automod["duplicate_limit"])}
  {num("caps_percent", "Großschreibung ab %", 50, 100, automod["caps_percent"])}
  {num("caps_min_letters", "… bei mindestens Buchstaben", 5, 200, automod["caps_min_letters"], "Kurze Rufe wie „OK“ zählen nicht.")}
 </div>
 <div class="guard-grid">
  <label class="guard-field">Wortfilter<textarea class="pulse-input pulse-textarea" name="blocked_terms" rows="5" maxlength="10000" placeholder="Ein Begriff pro Zeile">{e(chr(10).join(automod["blocked_terms"]))}</textarea></label>
  <label class="guard-field">Gesperrte Domains<textarea class="pulse-input pulse-textarea" name="blocked_domains" rows="5" maxlength="5000" placeholder="example.com (ein Hostname pro Zeile)">{e(chr(10).join(automod["blocked_domains"]))}</textarea></label>
  <label class="guard-field">Freigegebene Domains (für „Link außerhalb der Freigabeliste“)<textarea class="pulse-input pulse-textarea" name="allowed_domains" rows="5" maxlength="5000" placeholder="youtube.com&#10;tiktok.com">{e(chr(10).join(automod["allowed_domains"]))}</textarea></label>
 </div>
 <div class="guard-grid">
  <label class="guard-field">Ausgenommene Rollen<select class="pulse-input guard-select" name="automod_exempt_role_ids" multiple>{role_options(guild, automod["exempt_role_ids"])}</select><span class="guard-hint">Strg/⌘ gedrückt halten für Mehrfachauswahl.</span></label>
  <label class="guard-field">Ausgenommene Kanäle/Kategorien<select class="pulse-input guard-select" name="automod_exempt_channel_ids" multiple>{channel_options(guild, automod["exempt_channel_ids"], categories=True)}</select></label>
 </div>
 <div class="pulse-section-title" style="margin-top:6px">Eskalation</div>
 <div class="guard-grid">
  {num("timeout_after_strikes", "Timeout ab Verstoß Nr.", 1, 10, automod["timeout_after_strikes"])}
  {num("timeout_minutes", "Erster Timeout (Minuten)", 1, 1440, automod["timeout_minutes"], "Jeder weitere Verstoß verdoppelt die Dauer.")}
  {num("timeout_max_minutes", "Längster Timeout (Minuten)", 1, 10080, automod["timeout_max_minutes"])}
  {num("strike_window_minutes", "Verstöße zählen für (Minuten)", 5, 1440, automod["strike_window_minutes"])}
 </div>
 <label class="guard-check"><input type="checkbox" name="warn_dm" value="1"{" checked" if automod["warn_dm"] else ""}><span><strong>Bei der ersten Stufe per DM warnen</strong><small>Ohne Haken wird die Nachricht nur entfernt.</small></span></label>
 <div class="guard-hint">Aktuelle Stufen: <span class="guard-steps">{steps}</span></div>
 <label class="guard-field">Alarmkanal AutoMod{alert_select(guild, "alert_channel_id", automod["alert_channel_id"])}</label>
 <div class="guard-hint">Datenschutz: Nachrichteninhalte werden nur kurzzeitig im begrenzten Arbeitsspeicher als Prüfsumme gehalten, nicht gespeichert. Im Protokoll stehen nur Regel, Kanal, Maßnahme und Fallnummer.</div>
 <div><button class="pulse-btn primary" type="submit">💾 AutoMod speichern</button></div>
</form></div></section>'''

        # ---- Anti-Nuke: global settings (the rest of the Control Center comes from pulse_antinuke_admin)
        from pulse_antinuke_admin import global_form_extras, render_sections
        covered = {ev: r["label"] for r in antinuke["rules"] if r["enabled"] for ev in r["events"]}
        events = "".join(
            f'<div><div class="guard-hint" style="font-weight:700;margin:4px 0">{e(title)}</div>' + "".join(
                f'<label class="guard-check"><input type="checkbox" name="antinuke_events" value="{key}"{" checked" if key in antinuke["events"] else ""}>'
                f'<span>{e(pm.ANTINUKE_EVENT_LABELS[key])}' + (f'<small>→ eigene Regel „{e(covered[key])}“</small>' if key in covered else "") + '</span></label>'
                for key in keys) + "</div>"
            for title, keys in pm.ANTINUKE_EVENT_GROUPS)
        responses = "".join(
            f'<label class="guard-check"><input type="radio" name="response" value="{key}"{" checked" if antinuke["response"] == key else ""}>'
            f'<span><strong>{e(label)}</strong></span></label>' for key, label in pm.ANTINUKE_RESPONSE_LABELS.items())
        already_protect = antinuke["enabled"] and antinuke["mode"] == "protect"
        confirm_js = ("var m=this.querySelector('input[name=mode]:checked');"
                      f"if(m&&m.value==='protect'&&{'false' if already_protect else 'true'}){{"
                      "if(!window.confirm('Aktiver Schutz: Pulse führt die konfigurierten Maßnahmen (Rollenentzug, Kick, Bann, Lockdown) automatisch aus. Wirklich einschalten?'))return false;"
                      "this.confirm_protect.value='1';}return true;")
        antinuke_form = render_sections(ws, ctx, rows, **antinuke_kwargs) + f'''
<section id="antinuke" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">⚙️ Anti-Nuke · Globale Einstellungen</div>
<div class="pulse-section-sub">Liest das Discord-Audit-Log und reagiert, wenn eine Person in kurzer Zeit zu viele zerstörerische Aktionen ausführt. Serverinhaber und Pulse selbst sind immer ausgenommen. Nach einer Neuinstallation ist Anti-Nuke aus; beim ersten Einschalten startet es im Alarmmodus.</div></div>
<span class="pulse-pill {"good" if antinuke["enabled"] else ""}">{"aktiv" if antinuke["enabled"] else "aus"}</span></div>
<div class="pulse-card-b"><form method="post" action="/admin/security/antinuke" class="guard-form" onsubmit="{e(confirm_js)}">
 <label class="guard-check"><input type="checkbox" name="antinuke_enabled" value="1"{" checked" if antinuke["enabled"] else ""}><span><strong>Anti-Nuke aktivieren</strong></span></label>
 {global_form_extras(guild, antinuke)}
 <div class="pulse-section-title" style="margin-top:6px">Globale Standardregel: überwachte Ereignisse</div>
 <div class="guard-grid">{events}</div>
 <div class="guard-grid">
  {num("action_limit", "Standard-Schwelle: Aktionen", 2, 10, antinuke["action_limit"], "Rückfallwert für Regeln ohne eigene Schwelle.")}
  {num("window_seconds", "Standard-Zeitfenster (Sekunden)", 5, 60, antinuke["window_seconds"], "Rückfallwert für Regeln ohne eigenes Zeitfenster.")}
 </div>
 <div class="pulse-section-title" style="margin-top:6px">Reaktion der Standardregel</div>
 <div class="guard-grid">{responses}</div>
 <label class="guard-check"><input type="checkbox" name="kick_unverified_bots" value="1"{" checked" if antinuke["kick_unverified_bots"] else ""}><span><strong>Bots sofort entfernen, die eine nicht freigegebene Person hinzufügt</strong><small>Benötigt das Ereignis „Bot hinzugefügt“ und das Recht „Mitglieder kicken“. Im Alarmmodus wird nur gemeldet.</small></span></label>
 <label class="guard-check"><input type="checkbox" name="notify_admins" value="1"{" checked" if antinuke["notify_admins"] else ""}><span><strong>Administratoren in der Pulse-Inbox benachrichtigen (Standardregel)</strong></span></label>
 <label class="guard-field">Sicherheits- und Alarmkanal Anti-Nuke{alert_select(guild, "alert_channel_id", antinuke["alert_channel_id"])}</label>
 <div class="guard-hint">Vertrauenswürdige Personen und Rollen: Abschnitt <a href="#trusted" style="color:#6366f1">🤝 Ausnahmen</a>.</div>
 <div><button class="pulse-btn primary" type="submit">💾 Globale Einstellungen speichern</button></div>
</form></div></section>'''

        # ---- tester
        tester = f'''
<section id="tester" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">🧪 Regeltester</div>
<div class="pulse-section-sub">Prüft eine Beispielnachricht gegen die <strong>gespeicherten</strong> AutoMod-Regeln. Nichts wird gesendet, gespeichert oder protokolliert.</div></div></div>
<div class="pulse-card-b"><form method="post" action="/admin/security/test#tester" class="guard-form">
 <label class="guard-field">Beispielnachricht<textarea class="pulse-input pulse-textarea" name="sample" maxlength="2000" required>{e(test_text)}</textarea></label>
 <label class="guard-field" style="max-width:260px">Anzahl Erwähnungen darin<input class="pulse-input" type="number" name="mentions" min="0" max="100" value="0"></label>
 <div><button class="pulse-btn ghost" type="submit">▶ Prüfen</button></div></form>{test_result}</div></section>'''

        # ---- quarantine
        q_rows = []
        for uid, entry in sorted(quarantine.items(), key=lambda kv: str(kv[1].get("at", "")) if isinstance(kv[1], dict) else "", reverse=True):
            if not isinstance(entry, dict) or not str(uid).isdigit():
                continue
            roles = ", ".join(f"@{e(n)}" for n in entry.get("role_names", [])) or e(", ".join(str(r) for r in entry.get("role_ids", [])))
            q_rows.append(f'''<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{who(guild, uid)} <span class="pulse-row-meta">({e(uid)})</span></div>
<div class="pulse-row-meta">Seit {e(entry.get("at", "?"))} · entzogen: {roles} · Auslöser: {e(pm.ANTINUKE_EVENT_LABELS.get(entry.get("last_action"), entry.get("last_action") or "?"))}</div></div>
<form method="post" action="/admin/security/quarantine/restore" class="flex flex-wrap gap-2" onsubmit="return confirm('Rollen wirklich zurückgeben? Erst sicherstellen, dass das Konto nicht kompromittiert ist.')">
<input type="hidden" name="user_id" value="{e(uid)}"><input class="pulse-input" name="reason" required minlength="5" maxlength="300" placeholder="Grund (Pflicht)" style="max-width:220px">
<button class="pulse-btn good" type="submit">↩ Rollen zurückgeben</button>
<button class="pulse-btn ghost" type="submit" formaction="/admin/security/quarantine/dismiss">✕ Eintrag verwerfen</button></form></div>''')
        quarantine_html = f'''
<section id="quarantine" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">🔒 Quarantäne</div>
<div class="pulse-section-sub">Personen, denen Anti-Nuke gefährliche Rollen entzogen hat. Zurückgeben stellt genau die entzogenen Rollen wieder her.</div></div></div>
<div class="pulse-card-b space-y-2">{"".join(q_rows) or '<div class="pulse-empty">Niemand in Quarantäne.</div>'}</div></section>'''

        # ---- incidents
        inc_rows = []
        for row in rows:
            p = row["payload"]
            kind = row.get("event_type", "")
            if kind == "AutoMod-Maßnahme":
                title = f'💬 {e(pm.AUTOMOD_RULE_LABELS.get(p.get("rule"), p.get("rule", "?")))}'
                meta = f'{who(guild, row.get("target_id"))} · {where(guild, p.get("channel_id"))} · {e(p.get("action", ""))}' + (f' · Fall {e(p.get("strike"))}' if p.get("strike") else "")
            else:
                title = f'🛡️ {e(kind)}' + (f' · {e(pm.ANTINUKE_EVENT_LABELS.get(p.get("action") or p.get("last_action"), ""))}' if (p.get("action") or p.get("last_action")) else "")
                meta = f'{who(guild, row.get("target_id"))}' + (" · freigegeben" if p.get("trusted") else "") + (f' · {e(p.get("result"))}' if p.get("result") else "")
            inc_rows.append(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{title}</div>'
                            f'<div class="pulse-row-meta">{meta}</div></div><span class="pulse-time pulse-row-meta">{e(str(row.get("created_at", ""))[:16].replace("T", " "))}</span></div>')
        incidents_html = f'''
<section id="incidents" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">📜 Vorfallprotokoll</div>
<div class="pulse-section-sub">Die letzten {INCIDENT_LIMIT} AutoMod- und Anti-Nuke-Ereignisse (UTC).</div></div></div>
<div class="pulse-card-b space-y-2">{"".join(inc_rows) or '<div class="pulse-empty">Noch keine Vorfälle.</div>'}</div></section>'''

        nav = "".join(f'<a class="pulse-btn ghost" href="#{a}">{l}</a>' for a, l in (
            ("overview", "🧭 Status"), ("automod", "💬 AutoMod"), ("antinuke-center", "🛡️ Anti-Nuke"), ("rules", "📐 Regeln"),
            ("trusted", "🤝 Ausnahmen"), ("lockdown", "🔒 Lockdown"), ("antinuke", "⚙️ Global"), ("tester", "🧪 Tester"),
            ("quarantine", f"🔒 Quarantäne ({len(quarantine)})"), ("incidents", "📜 Vorfälle")))
        status_line = (f'AutoMod: {"aktiv" if automod["enabled"] else "inaktiv"} · Regeln: '
                       f'{e(", ".join(pm.AUTOMOD_RULE_LABELS[r] for r in automod["rules"]) or "keine")}<br>'
                       f'Anti-Nuke: {"aktiv" if antinuke["enabled"] else "inaktiv"} · Überwacht: '
                       f'{e(", ".join(pm.ANTINUKE_EVENT_LABELS[x] for x in antinuke["events"]) or "keine")}')
        return f'''{GUARD_CSS}
<div class="pulse-crumb" style="font-size:11px;color:#94a3b8;margin-bottom:8px"><a href="/admin" style="color:#6366f1">Verwaltung</a> › Sicherheit</div>
<div class="pulse-hero"><div class="pulse-hero-grid"><div><div class="pulse-kicker">Verwaltung · Sicherheit</div>
<div class="pulse-title">AutoMod &amp; Anti-Nuke</div>
<div class="pulse-sub">Schutz vor Spam, Werbung, Massen-Pings und vor dem Zerstören des Servers durch übernommene oder böswillige Konten. Alles lokal, ohne KI-Kosten; jede Änderung steht im Audit-Log.</div></div>
<div class="pulse-hero-box"><div class="label">Aktueller Stand</div><div class="text-xs mt-2" style="line-height:1.7">{status_line}</div></div></div></div>
<nav class="guard-nav" aria-label="Abschnitte">{nav}</nav>
{overview}{automod_form}{antinuke_form}{tester}{quarantine_html}{incidents_html}'''

    def render_page(ctx, **antinuke_kwargs):
        return HTMLResponse(render_pro_page(ws, "Sicherheit", ctx, "admin-security", page(ctx, **antinuke_kwargs)))

    from pulse_antinuke_admin import register as register_antinuke
    register_antinuke(app, render_page)

    @app.get("/admin/security", response_class=HTMLResponse)
    async def admin_security(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        return HTMLResponse(render_pro_page(ws, "Sicherheit", ctx, "admin-security", page(ctx)))

    @app.post("/admin/security/automod")
    async def admin_security_automod(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        guild = ctx.guild
        old, _ = current()
        role_ids = {r.id for r in getattr(guild, "roles", []) or []} | set(old["exempt_role_ids"])
        channel_ids = ({c.id for c in getattr(guild, "text_channels", []) or []}
                       | {c.id for c in getattr(guild, "categories", []) or []} | set(old["exempt_channel_ids"]))
        try:
            rules = sorted(set(form.getlist("automod_rules")) & pm.AUTOMOD_RULES)
            enabled = form.get("automod_enabled") == "1" or form.get("automod_enabled") == "on"
            if enabled and not rules:
                raise ValueError("Aktiviere mindestens eine AutoMod-Regel oder deaktiviere AutoMod.")
            raw = {
                "enabled": enabled,
                "mode": form.get("mode") if form.get("mode") in pm.AUTOMOD_MODES else "enforce",
                "rules": rules,
                "mention_limit": _parse_int(form, "mention_limit", "Erwähnungsgrenze", 3, 30),
                "flood_messages": _parse_int(form, "flood_messages", "Nachrichtenflut", 3, 20),
                "flood_seconds": _parse_int(form, "flood_seconds", "Zeitfenster Nachrichtenflut", 3, 60),
                "duplicate_limit": _parse_int(form, "duplicate_limit", "Wiederholungen", 2, 10),
                "caps_percent": _parse_int(form, "caps_percent", "Großschreibung", 50, 100),
                "caps_min_letters": _parse_int(form, "caps_min_letters", "Mindestbuchstaben", 5, 200),
                "blocked_terms": _parse_lines(form, "blocked_terms", "Wortfilter"),
                "blocked_domains": _parse_lines(form, "blocked_domains", "Gesperrte Domains", domains=True),
                "allowed_domains": _parse_lines(form, "allowed_domains", "Freigegebene Domains", domains=True),
                "exempt_role_ids": _parse_select(form, "automod_exempt_role_ids", "Ausgenommene Rollen", role_ids),
                "exempt_channel_ids": _parse_select(form, "automod_exempt_channel_ids", "Ausgenommene Kanäle", channel_ids),
                "warn_dm": form.get("warn_dm") == "1",
                "timeout_after_strikes": _parse_int(form, "timeout_after_strikes", "Timeout ab Verstoß", 1, 10),
                "timeout_minutes": _parse_int(form, "timeout_minutes", "Erster Timeout", 1, 1440),
                "timeout_max_minutes": _parse_int(form, "timeout_max_minutes", "Längster Timeout", 1, 10080),
                "strike_window_minutes": _parse_int(form, "strike_window_minutes", "Zählzeitraum", 5, 1440),
                "alert_channel_id": _channel_id(form, "alert_channel_id", guild, {old["alert_channel_id"]} - {0}),
            }
            if raw["timeout_max_minutes"] < raw["timeout_minutes"]:
                raise ValueError("Der längste Timeout darf nicht kürzer als der erste sein.")
            if "links" in rules and not raw["allowed_domains"]:
                raise ValueError("„Link außerhalb der Freigabeliste“ braucht mindestens eine freigegebene Domain – sonst wäre jeder Link verboten.")
        except ValueError as exc:
            return ws.back("/admin/security#automod", str(exc), False)
        value = pm.normalize_automod(raw)
        try:
            before = save_section("pulse_automod", value)
        except ws.JsonWriteBlocked as exc:
            return ws.back("/admin/security#automod", f"Nicht gespeichert: {exc}", False)
        changed = _changed(pm.normalize_automod(before), value)
        ws.log_audit(_name(ctx.user), ctx.user["id"], "AutoMod-Konfiguration geändert",
                     f"{'aktiv' if value['enabled'] else 'aus'} · Modus {value['mode']} · Regeln {', '.join(value['rules']) or 'keine'}"
                     f" · geändert: {', '.join(changed) or 'nichts'}")
        return ws.back("/admin/security#automod", "AutoMod-Einstellungen gespeichert – gelten ab der nächsten Nachricht.", True)

    @app.post("/admin/security/antinuke")
    async def admin_security_antinuke(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        guild = ctx.guild
        from pulse_antinuke_admin import GLOBAL_KEYS, Conflict, form_revision, parse_global_extras, save
        _, old = current()
        v2 = form.get("form_version") == "2"  # Control Center form; older forms keep the new keys unchanged
        role_ids = {r.id for r in getattr(guild, "roles", []) or []} | set(old["trusted_role_ids"])
        rule_events = {ev for r in old["rules"] if r["enabled"] for ev in r["events"]}
        try:
            events = sorted(set(form.getlist("antinuke_events")) & pm.ANTINUKE_EVENTS)
            enabled = form.get("antinuke_enabled") in ("1", "on")
            if enabled and not events and not rule_events:
                raise ValueError("Wähle mindestens ein Anti-Nuke-Ereignis oder deaktiviere Anti-Nuke.")
            response = form.get("response")
            if response not in pm.ANTINUKE_RESPONSES:
                raise ValueError("Bitte eine Reaktion wählen.")
            kick_bots = form.get("kick_unverified_bots") == "1"
            if kick_bots and "bot_add" not in events and "bot_add" not in rule_events:
                raise ValueError("„Bots sofort entfernen“ braucht das Ereignis „Bot hinzugefügt“.")
            # Operating mode: the first activation runs in alert mode; active protection needs an explicit,
            # confirmed choice (confirm_protect) unless it is already active.
            mode = form.get("mode")
            if mode is None:
                mode = old["mode"] if (old["enabled"] or "mode" in (ctx.config.get("pulse_antinuke") or {})) else "alert"
            if mode not in pm.ANTINUKE_MODES:
                raise ValueError("Bitte einen Betriebsmodus wählen.")
            if mode == "protect" and not (old["enabled"] and old["mode"] == "protect") and form.get("mode") is not None \
                    and form.get("confirm_protect") != "1":
                raise ValueError("Der aktive Schutzmodus muss ausdrücklich bestätigt werden – Anti-Nuke bleibt unverändert.")
            raw = {
                "enabled": enabled,
                "events": events,
                "action_limit": _parse_int(form, "action_limit", "Anti-Nuke-Schwelle", 2, 10),
                "window_seconds": _parse_int(form, "window_seconds", "Anti-Nuke-Zeitfenster", 5, 60),
                "response": response,
                "kick_unverified_bots": kick_bots,
                "notify_admins": form.get("notify_admins") == "1",
                "alert_channel_id": _channel_id(form, "alert_channel_id", guild, {old["alert_channel_id"]} - {0}),
                "mode": mode,
            }
            if "trusted_role_ids" in form or "trusted_user_ids" in form:  # older form with the exemptions inline
                raw["trusted_role_ids"] = _parse_select(form, "trusted_role_ids", "Vertrauensrollen", role_ids)
                raw["trusted_user_ids"] = _parse_user_ids(form, "trusted_user_ids", "Freigestellte Personen")
            if v2:
                raw.update(parse_global_extras(form, guild, old))
            value = {k: v for k, v in pm.normalize_antinuke({**old, **raw}).items() if k in GLOBAL_KEYS and k in raw}

            def mutate(stored, _before):
                stored.update(value)
                if "mode" not in stored:
                    stored["mode"] = mode
                return stored
            before, after = save(ws, ctx.user, mutate, expected_revision=form_revision(form))
        except (ValueError, Conflict) as exc:
            return ws.back("/admin/security#antinuke", str(exc), False)
        except ws.JsonWriteBlocked as exc:
            return ws.back("/admin/security#antinuke", f"Nicht gespeichert: {exc}", False)
        changed = [k for k in _changed(before, after) if k in GLOBAL_KEYS]
        ws.log_audit(_name(ctx.user), ctx.user["id"], "Anti-Nuke-Konfiguration geändert",
                     f"{'aktiv' if after['enabled'] else 'aus'} · Modus {after['mode']} · Reaktion {after['response']} · "
                     f"{after['action_limit']} in {after['window_seconds']} s · geändert: {', '.join(changed) or 'nichts'}")
        note = " Alarmmodus: Pulse meldet nur und sanktioniert nicht." if after["enabled"] and after["mode"] != "protect" else ""
        return ws.back("/admin/security#antinuke", f"Anti-Nuke-Einstellungen gespeichert – gelten sofort.{note}", True)

    @app.post("/admin/security/test", response_class=HTMLResponse)
    async def admin_security_test(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        sample = str(form.get("sample") or "")[:2000]
        try:
            mentions = max(0, min(100, int(form.get("mentions") or 0)))
        except ValueError:
            mentions = 0
        automod, _ = current()
        rules = automod["rules"]
        hits = pm.match_rules(
            sample, rules=rules, mention_count=mentions, mention_limit=automod["mention_limit"],
            blocked_terms=automod["blocked_terms"], blocked_domains=automod["blocked_domains"],
            allowed_domains=automod["allowed_domains"], caps_percent=automod["caps_percent"],
            caps_min_letters=automod["caps_min_letters"], duplicate_limit=automod["duplicate_limit"],
            flood_messages=automod["flood_messages"],
        )
        notes = []
        if not automod["enabled"]:
            notes.append("AutoMod ist gerade ausgeschaltet – im Betrieb würde nichts passieren.")
        elif automod["mode"] == "observe":
            notes.append("Modus „Nur beobachten“: im Betrieb würde nur protokolliert.")
        if "spam" in rules:
            notes.append("Spam/Nachrichtenflut hängt von mehreren Nachrichten ab und lässt sich mit einer einzelnen Nachricht nicht testen.")
        if hits:
            first = hits[0]
            items = "".join(f'<li><strong>{e(pm.AUTOMOD_RULE_LABELS[r])}</strong> – {e(reason)}</li>' for r, reason in hits)
            result = (f'<div class="pulse-alert mt-3" style="border-color:rgba(240,68,56,.35)"><strong>🔴 Würde ausgelöst: {e(pm.AUTOMOD_RULE_LABELS[first[0]])}</strong>'
                      f'<ul class="list-disc pl-5 mt-1">{items}</ul></div>')
        else:
            result = '<div class="pulse-alert mt-3" style="border-color:rgba(18,183,106,.35)"><strong>🟢 Keine Regel würde auslösen.</strong></div>'
        result += "".join(f'<div class="guard-hint mt-2">ℹ️ {e(n)}</div>' for n in notes)
        return HTMLResponse(render_pro_page(ws, "Sicherheit", ctx, "admin-security", page(ctx, result, sample)))

    async def quarantine_action(request, user_session, restore: bool):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        uid = str(form.get("user_id") or "").strip()
        reason = str(form.get("reason") or "").strip()[:300]
        if not _USER_ID.fullmatch(uid):
            return ws.back("/admin/security#quarantine", "Ungültige Discord-ID.", False)
        if len(reason) < 5:
            return ws.back("/admin/security#quarantine", "Bitte einen Grund (mindestens 5 Zeichen) angeben.", False)
        from cogs.antinuke import load_quarantine, restore_quarantine, save_quarantine
        import pulse_db
        if restore:
            result = await restore_quarantine(ctx.guild, int(uid), f"Pulse Anti-Nuke: Quarantäne aufgehoben durch {_name(ctx.user)} – {reason}")
            if result["error"]:
                return ws.back("/admin/security#quarantine", result["error"], False)
            title, message = "Anti-Nuke: Quarantäne aufgehoben", f"Rollen zurückgegeben: {', '.join(result['restored']) or 'keine'}"
            if result["missing"]:
                message += f" · {len(result['missing'])} Rolle(n) existieren nicht mehr"
        else:
            data = load_quarantine()
            if uid not in data:
                return ws.back("/admin/security#quarantine", "Für diese Person gibt es keinen Quarantäne-Eintrag.", False)
            data.pop(uid, None)
            save_quarantine(data)
            title, message = "Anti-Nuke: Quarantäne verworfen", "Eintrag entfernt, keine Rollen zurückgegeben"
        try:
            pulse_db.record_event(title, "user", uid, ctx.user["id"], _name(ctx.user), {"reason": reason, "result": message})
        except Exception as exc:
            print(f"⚠️ Quarantäne-Ereignis nicht protokolliert: {type(exc).__name__}")
        ws.log_audit(_name(ctx.user), ctx.user["id"], title, f"Discord-ID {uid} · {message} · Grund: {reason}")
        return ws.back("/admin/security#quarantine", message + ".", True)

    @app.post("/admin/security/quarantine/restore")
    async def admin_security_quarantine_restore(request: Request, user_session: str = Cookie(None)):
        return await quarantine_action(request, user_session, True)

    @app.post("/admin/security/quarantine/dismiss")
    async def admin_security_quarantine_dismiss(request: Request, user_session: str = Cookie(None)):
        return await quarantine_action(request, user_session, False)
