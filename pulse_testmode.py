"""Audit #168: Testserver-Modus - Pulse sicher gegen einen isolierten Test-Guild betreiben.

Aktivierung ausschließlich über die Umgebung (nicht über das Dashboard, damit niemand den Modus versehentlich auf dem
Produktivserver einschaltet):

  PULSE_TEST_MODE=1                 Modus an
  PULSE_TEST_GUILD_ID=<id>          die einzige Guild, mit der Pulse im Testmodus arbeiten darf
  PULSE_DATA_DIR / PULSE_DB_PATH    getrennte Datenablage (nie die Produktivdaten)

Wirkung: Banner auf jeder Seite, und :func:`guild_allowed` lässt das Dashboard nur mit dem Test-Guild arbeiten - hängt der
Bot im Testmodus versehentlich an einer anderen Guild, antwortet Pulse mit 503, statt dort Daten zu ändern. Auf der Seite
``/admin/testmodus`` (Administratoren) lassen sich gekennzeichnete Testdaten anlegen. Außerhalb des Testmodus ändert dieses
Modul nichts.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Request
from fastapi.responses import HTMLResponse

MARKER = "[TEST]"
SEED_KEY = "testmode_seeded"
_TRUE = {"1", "true", "yes", "on"}


def active(env=os.environ) -> bool:
    return str(env.get("PULSE_TEST_MODE", "")).strip().lower() in _TRUE


def test_guild_id(env=os.environ) -> int | None:
    raw = str(env.get("PULSE_TEST_GUILD_ID", "")).strip()
    return int(raw) if raw.isdigit() else None


def guild_allowed(guild_id, env=os.environ) -> bool:
    """Ohne Testmodus immer ``True``. Im Testmodus nur für den konfigurierten Test-Guild (fail-closed: ohne ID nie)."""
    if not active(env):
        return True
    expected = test_guild_id(env)
    return expected is not None and str(guild_id) == str(expected)


def status(env=os.environ) -> dict:
    expected = test_guild_id(env)
    return {"active": active(env), "test_guild_id": expected,
            "problems": ([] if not active(env) else
                         (["PULSE_TEST_GUILD_ID fehlt oder ist ungültig - Pulse verweigert im Testmodus jede Guild."] if expected is None else []))}


def banner_html(esc, env=os.environ) -> str:
    if not active(env):
        return ""
    return ('<div role="status" class="pulse-card" style="border-left:4px solid #8b5cf6;padding:12px 16px;margin-bottom:14px">'
            f'<strong>🧪 Testmodus</strong> – getrennte Testdaten, nur der Test-Guild ist erlaubt. Hier entstehen keine echten Daten.</div>')


def seed(db, env=os.environ, now: datetime | None = None) -> dict:
    """Legt gekennzeichnete Testdaten an (nur im Testmodus, höchstens einmal). ``db`` ist das Modul ``pulse_db``."""
    if not active(env):
        raise PermissionError("Testdaten gibt es nur im Testmodus.")
    if db.get_setting(SEED_KEY, None):
        return {"created": 0, "already": True}
    now = now or datetime.now(timezone.utc)
    created = 0
    for i, (title, prio) in enumerate((("Beispielaufgabe: Akte prüfen", "normal"), ("Beispielaufgabe: Support-Warteschlange sichten", "high"),
                                       ("Beispielaufgabe: Schulung wiederholen", "low"))):
        db.create_task(f"{MARKER} {title}", "Automatisch angelegte Testdaten.", None, "", "0", "Testmodus", prio,
                       (now + timedelta(days=2 + i)).date().isoformat())
        created += 1
    db.save_wiki(f"{MARKER} Beispielseite", "Test", "Diese Seite wurde vom Testmodus angelegt und kann gelöscht werden.", "0", "Testmodus")
    created += 1
    db.set_setting(SEED_KEY, {"at": now.isoformat(timespec="seconds"), "created": created})
    return {"created": created, "already": False}


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page
    e = ws.esc

    @app.get("/admin/testmodus", response_class=HTMLResponse)
    async def testmode_page(request: Request, user_session: str = Cookie(None)):
        import pulse_db
        ctx = ws.auth(request, user_session, perm=None, admin=True)
        st = status()
        problems = "".join(f'<p class="text-xs text-red-500">{e(p)}</p>' for p in st["problems"])
        if st["active"]:
            seeded = pulse_db.get_setting(SEED_KEY, None)
            action = ('<p class="text-xs">Testdaten wurden bereits angelegt.</p>' if seeded else
                      '<form method="post" action="/admin/testmodus/daten"><button class="pulse-btn primary">Testdaten anlegen</button></form>')
            head = f'<p class="text-sm">🧪 Testmodus ist <b>aktiv</b> (Test-Guild {e(st["test_guild_id"] or "unbekannt")}).</p>'
        else:
            action = ""
            head = '<p class="text-sm">Der Testmodus ist <b>aus</b>. Pulse arbeitet mit den echten Daten.</p>'
        howto = ('<h2 class="text-sm font-bold mt-4 mb-1">So wird er eingerichtet</h2><ol class="text-xs list-decimal ml-5 space-y-1">'
                 '<li>Zweite Pulse-Instanz mit eigenem Datenordner starten (<code>PULSE_DATA_DIR</code>, <code>PULSE_DB_PATH</code>).</li>'
                 '<li><code>PULSE_TEST_MODE=1</code> und <code>PULSE_TEST_GUILD_ID=&lt;Test-Guild&gt;</code> setzen; eigener Bot-Token und eigene OAuth-App.</li>'
                 '<li>Danach hier die Testdaten anlegen. Das Dashboard arbeitet im Testmodus nur mit dem Test-Guild.</li></ol>')
        body = f'<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › Testmodus</div><section class="pulse-card"><div class="pulse-card-b">{head}{problems}{action}{howto}</div></section>'
        return HTMLResponse(render_pro_page(ws, "Testmodus", ctx, "admin-betrieb", body))

    @app.post("/admin/testmodus/daten")
    async def testmode_seed(request: Request, user_session: str = Cookie(None)):
        import pulse_db
        ctx = ws.auth(request, user_session, perm=None, admin=True)
        if not active():
            return ws.back("/admin/testmodus", "Testdaten gibt es nur im Testmodus.", False)
        result = seed(pulse_db)
        ws.log_audit(ctx.user.get("global_name") or "Admin", ctx.user["id"], "Testdaten angelegt", f"{result['created']} Einträge")
        return ws.back("/admin/testmodus", "Testdaten bereits vorhanden." if result["already"] else f"{result['created']} Testeinträge angelegt.")
