"""Operations console ``/admin/betrieb`` (audit #153, #155, #157, #158, #165, #169, #172) - administrators only, read-only.

One page with tabs: overview (bot, memory, disk, errors, slowest route, release, links to the Server Control Center and
backups), error groups, endpoint latency, resource history, admin action history and the retention matrix.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Request
from fastapi.responses import HTMLResponse

import pulse_db
import pulse_ops as ops
import pulse_version

VIEWS = (("uebersicht", "Übersicht"), ("fehler", "Fehler"), ("latenz", "Latenz"), ("ressourcen", "Ressourcen"),
         ("aktionen", "Admin-Aktionen"), ("aufbewahrung", "Aufbewahrung"), ("rechte", "Rechte-Abgleich"))


def _spark(values: list[float], width: int = 420, height: int = 70) -> str:
    """Inline SVG polyline (no script, scales with the card); empty input renders nothing."""
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return '<span class="text-xs text-slate-400">Noch zu wenige Messpunkte.</span>'
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    coords = " ".join(f"{round(i * width / (len(pts) - 1), 1)},{round(height - (v - lo) / span * (height - 6) - 3, 1)}" for i, v in enumerate(pts))
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Verlauf von {lo:g} bis {hi:g}" style="width:100%;max-width:{width}px;height:{height}px">'
            f'<polyline fill="none" stroke="currentColor" stroke-width="2" points="{coords}"/></svg>')


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page
    e = ws.esc

    def tile(label, value, sub=""):
        return (f'<div class="pulse-stat"><div class="label">{e(label)}</div><div class="value">{e(value)}</div>'
                f'<div class="text-[11px] text-slate-400">{e(sub)}</div></div>')

    def overview(request) -> str:
        bot = getattr(ws.app.state, "bot", None)
        ready = bool(bot is not None and getattr(bot, "is_ready", lambda: False)())
        latency = getattr(bot, "latency", None)
        latency_txt = f"{int(latency * 1000)} ms" if isinstance(latency, (int, float)) and latency == latency and latency != float("inf") else "–"
        series = ops.resource_series(24)
        summary = ops.resource_summary(series)
        mem = summary.get("container_mb") or summary.get("process_mb")
        disk = summary.get("disk_free_mb")
        groups = ops.error_groups(100)
        slow = ops.LATENCY.snapshot()
        commit = {}
        try:
            import pulse_server_control
            commit = pulse_server_control.installed_commit(ws.BASE_DIR) or {}
        except Exception:  # noqa: BLE001
            commit = {}
        sha = str(commit.get("sha") or commit.get("commit") or "")[:8] or "unbekannt"
        extras = ops.cockpit_extras(ws.BASE_DIR, getattr(ws, "DB_PATH", None) or pulse_db.DB_PATH)
        tiles = "".join((
            tile("Discord-Bot", "online" if ready else "offline", f"Gateway-Latenz {latency_txt}"),
            tile("Arbeitsspeicher", "–" if not mem else f'{mem["last"]:g} MB', "" if not mem else f'24 h: {mem["min"]:g}–{mem["max"]:g} MB · {mem["trend"]}'),
            tile("Freier Speicher", "–" if not disk else f'{disk["last"]:g} MB', "Datenträger der Daten"),
            tile("Fehlergruppen", len(groups), f'{sum(1 for g in groups if g["recent"])} in der letzten Stunde'),
            tile("Langsamste Route", "–" if not slow else f'{slow[0]["p95"]:g} ms', "" if not slow else slow[0]["route"]),
            tile("Release", f"v{pulse_version.PULSE_VERSION}", f"Commit {sha}"),
            tile("Hintergrundjobs", f'{extras["job_counts"]["ok"]} ok', f'{extras["job_counts"]["failing"]} fehlerhaft · {extras["job_counts"]["overdue"]} überfällig'),
            tile("Datenbanken", f'{extras["database_total_mb"]:g} MB', f'{len(extras["databases"])} Dateien (inkl. WAL)'),
        ))
        problems = "".join(f'<li>{e(str(j["name"]))} – {"fehlerhaft" if j["state"] == "failing" else "überfällig"}'
                           f'{(": " + e(str(j["last_error"])[:100])) if j.get("last_error") else ""}</li>' for j in extras["problem_jobs"])
        problems_html = f'<h2 class="text-sm font-bold mt-4 mb-1">Jobs mit Problemen</h2><ul class="text-xs space-y-1">{problems}</ul>' if problems else ""
        links = " · ".join(f'<a class="underline" href="{e(u)}">{e(t)}</a>' for u, t in (
            ("/admin/server", "Server Control Center"), ("/backups", "Backups"), ("/admin/features", "Funktionsschalter"),
            ("/admin/settings-history", "Einstellungs-Historie"), ("/admin/privacy", "Datenschutz")))
        try:  # #169: running and previous release (rollback reference), recorded by the resource job
            ops.record_release(pulse_version.PULSE_VERSION, commit.get("sha") or commit.get("commit") or "")
            hist = ops.release_history()
        except Exception:  # noqa: BLE001
            hist = []
        releases = "".join(f'<li>v{e(h.get("version"))} · {e((h.get("sha") or "")[:8] or "unbekannt")} · seit {e(str(h.get("first_seen"))[:16])}'
                           f'{" <b>(läuft)</b>" if i == 0 else (" (Rückfall-Stand)" if i == 1 else "")}</li>' for i, h in enumerate(hist[:6]))
        release_html = f'<h2 class="text-sm font-bold mt-4 mb-1">Release-Verlauf</h2><ul class="text-xs space-y-1">{releases}</ul>' if releases else ""
        guild_ready = bool(bot is not None and ws.GUILD_ID and getattr(bot, "get_guild", lambda _g: None)(ws.GUILD_ID))
        checks = ops.release_checks(bot_ready=ready, guild_ready=guild_ready, extras=extras, memory_mb=(mem or {}).get("last") if mem else None,
                                    groups=groups, sha=str(commit.get("sha") or commit.get("commit") or ""))
        mark = {True: "✅", False: "❌", None: "⚪"}
        release_html += ('<h2 class="text-sm font-bold mt-4 mb-1">Prüfergebnisse des laufenden Releases</h2><ul class="text-xs space-y-1">'
                         + "".join(f'<li>{mark[c["ok"]]} {e(c["name"])} – {e(c["detail"])}</li>' for c in checks) + "</ul>")
        return (f'<div class="pulse-stat-grid">{tiles}</div>{problems_html}{release_html}<p class="text-xs text-slate-400 mt-3">Weitere Betriebswerkzeuge: {links}</p>'
                '<p class="text-[11px] text-slate-400 mt-1">Die Werte stammen aus Pulse selbst (Messpunkte alle fünf Minuten, Latenz seit dem letzten Start).</p>')

    def errors_view() -> str:
        groups = ops.error_groups(100)
        if not groups:
            return '<div class="pulse-empty">Keine Fehler seit der Einrichtung der Fehlergruppen.</div>'
        def pills(g):
            return ('<span class="pulse-pill warn">neu</span>' if g["new"] else "") + ('<span class="pulse-pill bad">aktuell</span>' if g["recent"] else "")
        rows = "".join(
            f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(g["sample"][:140])}</div>'
            f'<div class="pulse-row-meta">{e(g["source"])} · {g["count"]}× · zuerst {e(g["first_seen"][:16].replace("T", " "))} · zuletzt {e(g["last_seen"][:16].replace("T", " "))} UTC</div></div>'
            f'{pills(g)}</div>' for g in groups)
        return ('<p class="text-xs text-slate-400 mb-2">Gleiche Fehler mit anderen IDs, Zahlen, Pfaden oder Werten sind eine Gruppe (Zähler und erste/letzte Sichtung). '
                f'Es werden die letzten {ops.MAX_GROUPS} Gruppen behalten.</p>{rows}')

    def latency_view() -> str:
        rows = ops.LATENCY.snapshot()
        if not rows:
            return '<div class="pulse-empty">Noch keine Messwerte seit dem Start.</div>'
        body = "".join(f'<tr><td>{e(r["route"])}</td><td>{r["count"]}</td><td>{r["p50"]:g}</td><td>{r["p95"]:g}</td><td>{r["max"]:g}</td><td>{r["errors"]}</td></tr>' for r in rows[:60])
        return ('<p class="text-xs text-slate-400 mb-2">Antwortzeit je Route (Millisekunden) aus den letzten bis zu 200 Anfragen, sortiert nach p95. Zurückgesetzt beim Neustart.</p>'
                '<div style="overflow-x:auto"><table class="pulse-table"><thead><tr><th>Route</th><th>Anfragen</th><th>p50</th><th>p95</th><th>max</th><th>5xx</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>')

    def resources_view(hours: int) -> str:
        series = ops.resource_series(hours)
        summary = ops.resource_summary(series)
        opts = " ".join(f'<a class="pulse-btn {"primary" if h == hours else "ghost"}" href="/admin/betrieb?ansicht=ressourcen&h={h}">{label}</a>'
                        for h, label in ((6, "6 Std."), (24, "24 Std."), (72, "3 Tage"), (336, "14 Tage")))
        blocks = []
        for key, label, unit in (("container_mb", "Container-Speicher", "MB"), ("process_mb", "Pulse-Prozess", "MB"), ("cpu_pct", "CPU (Prozess)", "%"), ("disk_free_mb", "Freier Datenträger", "MB")):
            s = summary.get(key)
            facts = "keine Daten" if not s else f'zuletzt {s["last"]:g} {unit} · Ø {s["avg"]:g} · min {s["min"]:g} · max {s["max"]:g} · {s["trend"]} ({s["n"]} Messpunkte)'
            blocks.append(f'<section class="pulse-card mb-3"><div class="pulse-card-b"><div class="pulse-section-title">{label}</div>'
                          f'<div class="text-xs text-slate-400 mb-1">{e(facts)}</div>{_spark([r[key] for r in series])}</div></section>')
        return f'<div class="flex flex-wrap gap-2 mb-3">{opts}</div>{"".join(blocks)}'

    def actions_view(request) -> str:
        q = str(request.query_params.get("q") or "")[:100]
        src = str(request.query_params.get("quelle") or "")
        src = src if src in ("audit", "einstellung") else ""
        rows = ops.admin_actions(ws, 200, q, src)
        form = (f'<form method="get" class="flex flex-wrap gap-2 mb-3"><input type="hidden" name="ansicht" value="aktionen"><input class="pulse-input flex-1" name="q" value="{e(q)}" maxlength="100" placeholder="Person, Aktion oder Detail">'
                f'<select class="pulse-input" name="quelle"><option value="">Alle Quellen</option><option value="audit"{" selected" if src == "audit" else ""}>Dashboard-Audit</option>'
                f'<option value="einstellung"{" selected" if src == "einstellung" else ""}>Einstellungen</option></select><button class="pulse-btn primary">Filtern</button></form>')
        body = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(r["action"])}</div>'
                       f'<div class="pulse-row-meta">{e(r["at"])} · {e(r["actor"] or "System")} · {e(r["source"])}{" · " + e(r["detail"]) if r["detail"] else ""}</div></div></div>'
                       for r in rows) or '<div class="pulse-empty">Keine Einträge.</div>'
        return form + body

    def retention_view() -> str:
        rows = ops.retention_matrix(ws)
        body = "".join(
            f'<tr><td>{e(r["label"])}</td><td>{e(r["store"])}</td><td>{e(r["rule"])}</td><td>{"automatisch" if r["automatic"] else "manuell / keine"}</td>'
            f'<td>{"–" if r["count"] is None else r["count"]}</td><td>{e(r["oldest"] or "–")}</td></tr>' for r in rows)
        return ('<p class="text-xs text-slate-400 mb-2">Welche Datenart wo liegt, welche Frist gilt und wie viel gerade vorhanden ist. „Automatisch“ heißt: Pulse setzt die Frist selbst durch. '
                'Die Bereinigung von Bewerbungen und Audit ist bis zur Freigabe der verknüpften Wiederherstellung gesperrt.</p>'
                '<div style="overflow-x:auto"><table class="pulse-table"><thead><tr><th>Datenart</th><th>Speicher</th><th>Frist</th><th>Durchsetzung</th><th>Anzahl</th><th>Ältester Eintrag</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>')

    def person_rights_html(ctx) -> str:
        """#160: Soll/Ist je Person und Recht gegen den gespeicherten Soll-Stand."""
        import pulse_rights_diff
        d = pulse_rights_diff.person_diff(ctx.guild, ctx.config)
        save = ('<form method="post" action="/admin/betrieb/rechte-soll" class="mt-2"><button class="pulse-btn ghost">'
                f'{"Soll-Stand neu festlegen" if d["has_target"] else "Aktuellen Stand als Soll festlegen"}</button></form>')
        if not d["has_target"]:
            return ('<h2 class="text-sm font-bold mt-4 mb-1">Rechte je Person</h2><p class="text-xs text-slate-400">Noch kein Soll-Stand festgelegt. '
                    'Lege den aktuellen, geprüften Stand fest; spätere Änderungen pro Person und Recht erscheinen dann hier.</p>' + save)
        rows = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(c["name"])}</div>'
                       f'<div class="pulse-row-meta">{"Fehlt: " + e(", ".join(c["missing"])) if c["missing"] else ""}'
                       f'{" · " if c["missing"] and c["extra"] else ""}{"Zusätzlich: " + e(", ".join(c["extra"])) if c["extra"] else ""}</div></div>'
                       f'<span class="pulse-pill {"bad" if c["extra"] else "warn"}">{len(c["missing"]) + len(c["extra"])}</span></div>' for c in d["changed"])
        notes = ""
        if d["left"]:
            notes += f'<p class="text-xs mt-2">Im Soll, aber nicht mehr im Team: {e(", ".join(x["user_id"] for x in d["left"][:20]))}</p>'
        if d["new"]:
            notes += f'<p class="text-xs mt-2">Neu im Team (nicht im Soll): {e(", ".join(x["name"] for x in d["new"][:20]))}</p>'
        body = rows or '<div class="pulse-empty">Alle Rechte entsprechen dem Soll-Stand.</div>'
        return (f'<h2 class="text-sm font-bold mt-4 mb-1">Rechte je Person{__import__("pulse_help").tip("rights_target")}</h2><p class="text-[11px] text-slate-400 mb-2">Soll-Stand vom {e(d["saved_at"][:16])} (festgelegt von {e(d["by"] or "unbekannt")}).</p>'
                + body + notes + save)

    def rights_view(ctx) -> str:
        import pulse_rights_diff
        findings = pulse_rights_diff.diff(ctx.guild, ctx.config)
        try:
            person_html = person_rights_html(ctx)
        except Exception:  # noqa: BLE001 - the server-level diff must keep working
            person_html = ""
        if not findings:
            return '<div class="pulse-empty">Keine Abweichungen zwischen Discord-Server und Rechte-Konfiguration gefunden.</div>' + person_html
        pill = {"bad": "bad", "warn": "warn", "info": "good"}
        rows = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(f["title"])}</div>'
                       f'<div class="pulse-row-meta">{e(", ".join(f["items"][:30]))}</div></div><span class="pulse-pill {pill[f["severity"]]}">{len(f["items"])}</span></div>' for f in findings)
        return ('<p class="text-xs text-slate-400 mb-2">Soll/Ist-Abgleich der Rechte-Konfiguration mit dem Discord-Server. Nur Anzeige – Pulse ändert hier keine Rolle und keine Einstellung. '
                'Die Rechte je Person zeigt „Wer darf was?“.</p>' + rows + person_html)

    @app.post("/admin/betrieb/rechte-soll")
    async def save_rights_target(request: Request, user_session: str = Cookie(None)):
        import pulse_rights_diff
        ctx = ws.auth(request, user_session, perm=None, admin=True)
        actor = ctx.user.get("global_name") or "Admin"
        count = pulse_rights_diff.save_target(ctx.guild, ctx.config, actor)
        ws.log_audit(actor, ctx.user["id"], "Rechte-Soll festgelegt", f"{count} Personen")
        return ws.back("/admin/betrieb?ansicht=rechte", f"Soll-Stand für {count} Personen gespeichert.")

    @app.get("/admin/betrieb", response_class=HTMLResponse)
    async def operations_console(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None, admin=True)
        view = request.query_params.get("ansicht", "uebersicht")
        if view not in {k for k, _ in VIEWS}:
            view = "uebersicht"
        try:
            hours = int(request.query_params.get("h") or 24)
        except ValueError:
            hours = 24
        content = {"uebersicht": lambda: overview(request), "fehler": errors_view, "latenz": latency_view,
                   "ressourcen": lambda: resources_view(hours), "aktionen": lambda: actions_view(request), "aufbewahrung": retention_view,
                   "rechte": lambda: rights_view(ctx)}[view]()
        tabs = " ".join(f'<a href="/admin/betrieb?ansicht={k}" class="pulse-btn {"primary" if k == view else "ghost"}">{label}</a>' for k, label in VIEWS)
        stamp = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        body = (f'<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › Betriebskonsole</div><div class="flex flex-wrap gap-2 mb-3">{tabs}</div>'
                f'<section class="pulse-card"><div class="pulse-card-b">{content}</div></section><p class="text-[11px] text-slate-400 mt-2">Stand {stamp}</p>')
        return HTMLResponse(render_pro_page(ws, "Betriebskonsole", ctx, "admin-betrieb", body))
