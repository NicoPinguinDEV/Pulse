"""Audit #106 and #176: the BOCHUM RP Academy - one learning overview built from what already exists.

Learning paths (per team role), quiz attempts, certificates with refresher status and the must-read wiki pages (#113) are
combined in ``/academy``: everybody sees their own progress and the next open training; leadership additionally sees the
whole team (progress, expired certificates, pending must-reads). Read-only - training, certificates and wiki stay managed
in their own modules.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Request
from fastapi.responses import HTMLResponse

import pulse_certificates as certs
import pulse_db
import pulse_wiki_plus as wiki


def _path_view(path: dict, user_id) -> dict:
    items = path.get("items") or []
    passed = pulse_db.passed_training_ids_for_user(user_id, [i["training_id"] for i in items]) if items else set()
    passed = {str(p) for p in passed}
    steps = [{"id": str(i["training_id"]), "title": str(i.get("title") or ""), "passed": str(i["training_id"]) in passed} for i in items]
    nxt = next((s for s in steps if not s["passed"]), None)
    done = sum(1 for s in steps if s["passed"])
    return {"id": str(path["id"]), "title": str(path.get("title") or ""), "role": str(path.get("role_name") or ""), "steps": steps,
            "done": done, "total": len(steps), "percent": round(done / len(steps) * 100) if steps else 0, "next": nxt}


def overview(user_id, role_ids, now: datetime | None = None) -> dict:
    """Own learning overview. ``role_ids`` = the person's team role IDs."""
    now = now or datetime.now(timezone.utc)
    paths = [_path_view(p, user_id) for p in pulse_db.training_paths_for_roles([str(r) for r in role_ids])]
    certificates = certs.certificates_for_user(user_id, now)
    live = [c for c in certificates if c["status"] != certs.STATUS_SUPERSEDED]
    attention = [c for c in live if c["status"] in (certs.STATUS_EXPIRING, certs.STATUS_EXPIRED)]
    total = sum(p["total"] for p in paths)
    done = sum(p["done"] for p in paths)
    return {"paths": paths, "certificates": live, "attention": attention, "must_read": wiki.pending_for(user_id),
            "percent": round(done / total * 100) if total else None, "done": done, "total": total}


def team_overview(members, now: datetime | None = None) -> list[dict]:
    """Leadership table. ``members`` = [(user_id, name, [role_ids])]; sorted by lowest progress first."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for uid, name, roles in members:
        view = overview(uid, roles, now)
        rows.append({"id": str(uid), "name": str(name), "percent": view["percent"], "done": view["done"], "total": view["total"],
                     "expired": sum(1 for c in view["attention"] if c["status"] == certs.STATUS_EXPIRED),
                     "expiring": sum(1 for c in view["attention"] if c["status"] == certs.STATUS_EXPIRING), "must_read": len(view["must_read"])})
    return sorted(rows, key=lambda r: ((r["percent"] if r["percent"] is not None else 101), r["name"].casefold()))


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page
    e = ws.esc

    def lead(perms):
        return bool((perms or {}).get("is_admin") or (perms or {}).get("can_promote") or (perms or {}).get("can_manage_training"))

    @app.get("/academy", response_class=HTMLResponse)
    async def academy_page(request: Request, user_session: str = Cookie(None)):
        import pulse_access
        ctx = ws.auth(request, user_session)
        team_ids = {int(x) for x in ctx.config.get("team_role_ids", []) if str(x).isdigit()}
        mine = [r.id for r in getattr(ctx.member, "roles", []) if r.id in team_ids] if ctx.member else []
        own = overview(ctx.user["id"], mine)
        path_cards = "".join(
            f'<section class="pulse-card mb-3"><div class="pulse-card-h"><div class="pulse-section-title">{e(p["title"])}</div><span class="pulse-pill {"good" if p["percent"] == 100 else ""}">{p["percent"]} %</span></div>'
            f'<div class="pulse-card-b">{"".join(f"<div class=text-xs>{chr(9989) if s['passed'] else chr(9723)} {e(s['title'])}</div>" for s in p["steps"])}'
            f'{"<a class=underline href=/training>Nächste Schulung: " + e(p["next"]["title"]) + " →</a>" if p["next"] else ""}</div></section>' for p in own["paths"]
        ) or '<div class="pulse-empty">Für deine Teamrolle gibt es noch keinen Lernpfad.</div>'
        cert_rows = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(c["title"])}</div>'
                            f'<div class="pulse-row-meta">{e(c["status_label"])} · {c["score"]} % · ausgestellt {e(str(c["issued_at"])[:10])}'
                            f'{" · gültig bis " + e(str(c["valid_until"])[:10]) if c.get("valid_until") else ""}</div></div></div>' for c in own["certificates"]) or '<div class="pulse-empty">Noch keine Zertifikate.</div>'
        read_rows = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><a href="/wiki/{e(p["id"])}">{e(p["title"])}</a></div>'
                            f'<div class="pulse-row-meta">Pflichtlektüre · Version {p["version"]}</div></div></div>' for p in own["must_read"]) or '<div class="pulse-empty">Alles gelesen.</div>'
        team = ""
        if lead(ctx.perms):
            rows = team_overview([(str(m.id), m.display_name, [r.id for r in m.roles if r.id in team_ids]) for m in pulse_access.team_members(ctx.guild, ctx.config)][:200])
            trs = "".join(f'<tr><td><a href="/member/{e(r["id"])}">{e(r["name"])}</a></td><td>{"–" if r["percent"] is None else str(r["percent"]) + " %"}</td><td>{r["done"]}/{r["total"]}</td>'
                          f'<td>{r["expiring"]}</td><td>{r["expired"]}</td><td>{r["must_read"]}</td></tr>' for r in rows)
            team = ('<section class="pulse-card mt-4"><div class="pulse-card-h"><div class="pulse-section-title">👥 Team-Fortschritt</div><a class="text-[11px] text-indigo-500" href="/training/matrix">Kompetenzmatrix →</a></div>'
                    '<div class="pulse-card-b" style="overflow-x:auto"><table class="pulse-table"><thead><tr><th>Person</th><th>Lernpfade</th><th>Schulungen</th><th>Läuft ab</th><th>Abgelaufen</th><th>Pflichtlektüre offen</th></tr></thead>'
                    f'<tbody>{trs}</tbody></table></div></section>')
        pct = "–" if own["percent"] is None else f'{own["percent"]} %'
        body = (f'<div class="pulse-crumb"><a href="/training">Schulungen</a> › Academy</div><h1 class="text-2xl font-bold mb-3">🎓 BOCHUM RP Academy</h1>'
                f'<div class="pulse-stat-grid mb-4"><div class="pulse-stat"><div class="label">Lernfortschritt</div><div class="value">{pct}</div><div class="text-[11px] text-slate-400">{own["done"]} von {own["total"]} Schulungen</div></div>'
                f'<div class="pulse-stat"><div class="label">Zertifikate</div><div class="value">{len(own["certificates"])}</div><div class="text-[11px] text-slate-400">{len(own["attention"])} mit Handlungsbedarf</div></div>'
                f'<div class="pulse-stat"><div class="label">Pflichtlektüre offen</div><div class="value">{len(own["must_read"])}</div></div></div>'
                f'<div class="pulse-grid"><div>{path_cards}</div><div class="space-y-4"><section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">🏅 Meine Zertifikate</div></div><div class="pulse-card-b">{cert_rows}</div></section>'
                f'<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">📌 Pflichtlektüre</div><a class="text-[11px] text-indigo-500" href="/wissen">Wiki →</a></div><div class="pulse-card-b">{read_rows}</div></section></div></div>{team}')
        return HTMLResponse(render_pro_page(ws, "Academy", ctx, "training", body))
