"""Local UI preview (development only): the real dashboard with a fake Discord server and fake login.

    python tools/preview_server.py [--port 8765] [--role admin|team]

* Binds to 127.0.0.1 only, uses temporary copies of all data files, never connects to Discord.
* `auth` is replaced by a fake login (admin or plain team member) – this file is never imported by
  main.py or the bot; it exists so UI changes can be checked in a browser (desktop and mobile).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _register_e2e_hooks(app) -> None:
    """Failure switches for tests/test_e2e_dashboard.py – only with --e2e-hooks, only in this preview process.

    * POST /__preview/maintenance {"on": true|false}: the real restore lock (pulse_backup), so every save_json
      raises JsonWriteBlocked and the real 503 page is shown.
    * GET /__preview/fail: an unexpected exception, rendered by the real 500 handler (error ID).
    """
    import pulse_backup
    from fastapi import Body
    from fastapi.responses import JSONResponse

    @app.post("/__preview/maintenance")
    async def preview_maintenance(on: bool = Body(embed=True)):  # no Request annotation: __future__ annotations
        (pulse_backup._MAINTENANCE.set if on else pulse_backup._MAINTENANCE.clear)()
        return JSONResponse({"ok": True, "maintenance": pulse_backup.maintenance_active()})

    @app.get("/__preview/fail")
    async def preview_fail():
        raise RuntimeError("E2E: absichtlich ausgelöster Serverfehler")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--role", choices=("admin", "team"), default="admin")
    parser.add_argument("--e2e-hooks", action="store_true",
                        help="Test-only routes under /__preview/ to provoke server-side failures (E2E tests)")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to cp1252; the start messages use emoji
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    tmp = tempfile.mkdtemp(prefix="pulse-preview-")
    os.environ["PULSE_DB_PATH"] = str(Path(tmp) / "pulse.db")
    sys.path.insert(0, str(ROOT))
    os.chdir(tmp)  # relative runtime files (backups/, shifts.json …) land in the temp folder

    import uvicorn
    import webserver as ws
    from tests.fakes import FakeChannel, FakeGuild, FakeMember, FakeRole

    for name in ("DATA_FILE", "CONFIG_FILE", "LOGS_FILE", "SHIFTS_FILE", "MEETINGS_FILE", "APPS_FILE", "AUDIT_FILE"):
        setattr(ws, name, str(Path(tmp) / Path(getattr(ws, name)).name))
    ws.BASE_DIR = Path(tmp)
    ws.DB_ABMELDUNGEN = str(Path(tmp) / "abmeldungen.db")

    team = FakeRole(100, "Supporter", position=3)
    lead = FakeRole(101, "Leitung", position=5)
    members = [FakeMember(1, "Admin Anna", roles=[lead], admin=True),
               FakeMember(2, "Max Muster", roles=[team]), FakeMember(3, "Lena Leitung", roles=[lead])]
    for m in members:
        m.status, m.activity, m.activities, m.nick, m.joined_at = "online", None, (), None, None
        m.top_role = m.roles[0] if m.roles else team
        m.color = SimpleNamespace(value=0x5B5CF0)
    guild = FakeGuild(gid=ws.GUILD_ID, members=members, roles=[team, lead],
                      channels=[FakeChannel(10, "allgemein"), FakeChannel(11, "team-logs")], owner_id=1)
    guild.categories = []
    import discord  # Phase 7: @everyone with real permissions for the visibility check, a bot for /status
    guild.default_role = SimpleNamespace(id=guild.id, name="@everyone", permissions=discord.Permissions(view_channel=True))
    for role in guild.roles:
        role.permissions = discord.Permissions.none()
    for channel in guild.channels:
        channel.overwrites = {}
    ws.app.state.bot = SimpleNamespace(get_guild=lambda _: guild, get_user=lambda _: None, cogs={})
    ws.init_runtime()
    ws.save_json(ws.CONFIG_FILE, {"team_role_ids": [100, 101],
                                  "permissions": {"101": {k: True for k in ws.PERM_KEYS}}})

    user = members[0] if args.role == "admin" else members[1]

    def fake_auth(request, session, perm="can_view_dashboard", admin=False):
        from fastapi import HTTPException
        config = ws.load_config()
        perms, member = ws.compute_perms(guild, user.id, config)
        if admin and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="Dieser Bereich ist nur für Administratoren.")
        if perm and not perms.get(perm) and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        q = request.query_params
        return SimpleNamespace(user={"id": str(user.id), "global_name": user.display_name, "username": user.name},
                               guild=guild, guild_name=guild.name, perms=perms, config=config, member=member,
                               msg=q.get("msg", ""), msg_ok=q.get("t", "ok") != "err")

    ws.auth = fake_auth
    if args.e2e_hooks:
        _register_e2e_hooks(ws.app)
    print(f"Pulse-Vorschau: http://127.0.0.1:{args.port}/me  (Rolle: {args.role}, Daten: {tmp})")
    uvicorn.run(ws.app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
