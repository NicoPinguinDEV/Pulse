"""Performance baseline (development only, Phase 10): measures the real dashboard in-process.

    python tools/perf_baseline.py [--history 20000] [--members 400] [--runs 5] [--json out.json]

* Temporary data folder with synthetic data (no real runtime data is read or written), fake guild and fake
  login exactly like tools/preview_server.py – no Discord, no OAuth, no network.
* For every parameterless GET page of the dashboard: median latency, JSON parses (json.load/json.loads) with the
  parsed bytes, and opened SQLite connections per request. Plus import time and process RSS.
* Numbers depend on the machine; compare only runs from the same machine (before/after).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _rss_mb() -> float | None:
    sys.path.insert(0, str(ROOT / "tools"))
    from load_test import rss_mb  # same measurement as the load test (/proc or GetProcessMemoryInfo)
    value = rss_mb()
    return round(value, 1) if value else None


def p_is_stream(path: str) -> bool:
    """Server-sent event streams never finish – not a page load."""
    return path.endswith("/stream") or "/stream/" in path


class _Counter:
    """Counts JSON parses and SQLite connections while active (wraps the module functions, restores them)."""

    def __init__(self):
        self.parses = self.parsed_bytes = self.connects = 0
        self._orig = (json.load, json.loads, sqlite3.connect)

    def install(self):
        load, loads, connect = self._orig

        def c_load(fp, *a, **k):
            data = fp.read()
            self.parses += 1
            self.parsed_bytes += len(data)
            return loads(data, *a, **k)

        def c_loads(s, *a, **k):
            self.parses += 1
            self.parsed_bytes += len(s)
            return loads(s, *a, **k)

        def c_connect(*a, **k):
            self.connects += 1
            return connect(*a, **k)

        json.load, json.loads, sqlite3.connect = c_load, c_loads, c_connect

    def uninstall(self):
        json.load, json.loads, sqlite3.connect = self._orig

    def reset(self):
        self.parses = self.parsed_bytes = self.connects = 0


def _seed(ws, n_history: int, member_ids: list[int]) -> None:
    rng = random.Random(10)
    now = ws.now_de()
    history = []
    for i in range(n_history):
        day = now - __import__("datetime").timedelta(days=rng.randint(0, 400), hours=rng.randint(0, 23))
        dur = rng.randint(600, 4 * 3600)
        history.append({"mod_id": str(rng.choice(member_ids)), "date": day.strftime("%Y-%m-%d"),
                        "started_at_iso": day.isoformat(timespec="seconds"),
                        "ended_at_iso": (day + __import__("datetime").timedelta(seconds=dur)).isoformat(timespec="seconds"),
                        "duration_seconds": dur, "shift_event_id": f"s{i}"})
    ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": history})
    logs = [{"id": f"log_{i:06x}", "target_user": f"User{i}", "roblox_id": str(1000 + i), "type": rng.choice(["Warn", "Kick", "Ban"]),
             "reason": "Synthetischer Eintrag " * 3, "moderator": "Mod", "moderator_id": str(rng.choice(member_ids)),
             "created_at": now.strftime("%d.%m.%Y %H:%M")} for i in range(n_history // 4)]
    ws.save_json(ws.LOGS_FILE, logs)
    apps = {f"app_{i}": {"id": f"app_{i}", "name": f"Bewerber {i}", "discord_id": str(500000 + i), "status": rng.choice(["pending", "interview", "accepted", "rejected"]),
                         "career": "supporter", "created_at": now.isoformat(timespec="seconds"), "answers": {"q1": "Antwort " * 40}}
            for i in range(max(50, n_history // 40))}
    ws.save_json(ws.APPS_FILE, apps)
    ws.save_json(ws.AUDIT_FILE, [{"id": f"audit_{i:06x}", "actor": "Admin", "actor_id": "1", "action": "Test", "details": "x" * 80,
                                  "timestamp": now.isoformat(timespec="seconds")} for i in range(n_history // 4)])
    ws.save_json(ws.DATA_FILE, {str(m): {"name": f"Member {m}", "warns": rng.randint(0, 2)} for m in member_ids})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=int, default=20000, help="shift history entries (logs/audit get 1/4)")
    parser.add_argument("--members", type=int, default=400)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--json", default="", help="write the result as JSON to this path")
    parser.add_argument("--only", default="", help="comma-separated paths to measure (default: all GET pages)")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    tmp = tempfile.mkdtemp(prefix="pulse-perf-")
    os.environ["PULSE_GITHUB_REPO"] = ""  # Server Control Center: no request to api.github.com
    for secret in ("DISCORD_TOKEN", "PULSE_HOST_AGENT_URL", "PULSE_HOST_AGENT_TOKEN"):
        os.environ.pop(secret, None)
    os.environ["PULSE_DB_PATH"] = str(Path(tmp) / "pulse.db")
    sys.path.insert(0, str(ROOT))
    os.chdir(tmp)
    rss_start = _rss_mb()
    t0 = time.perf_counter()
    import webserver as ws
    import_s = time.perf_counter() - t0
    rss_import = _rss_mb()
    import discord
    from fastapi.testclient import TestClient
    from tests.fakes import FakeChannel, FakeGuild, FakeMember, FakeRole

    for name in ("DATA_FILE", "CONFIG_FILE", "LOGS_FILE", "SHIFTS_FILE", "MEETINGS_FILE", "APPS_FILE", "AUDIT_FILE"):
        setattr(ws, name, str(Path(tmp) / Path(getattr(ws, name)).name))
    ws.BASE_DIR = Path(tmp)
    import pulse_panels
    pulse_panels.BASE_DIR = Path(tmp)  # reads the cogs' own SQLite files (read-only) – never the checkout's local ones
    os.chdir(tmp)  # same guard as preview_server/load_test (tests/test_tool_isolation.py): runtime files stay in tmp
    ws.BACKUP_DIR = str(Path(tmp) / "backups")
    ws.DB_ABMELDUNGEN = str(Path(tmp) / "abmeldungen.db")
    # Phase 10: computed from the checkout on import – /team and /activity-check opened (and created) it there
    ws.ACTIVITY_DB = str(Path(tmp) / "activity_check.db")

    team, lead = FakeRole(100, "Supporter", position=3), FakeRole(101, "Leitung", position=5)
    members = [FakeMember(1, "Admin Anna", roles=[lead], admin=True)]
    members += [FakeMember(10 + i, f"Mitglied {i}", roles=[team] if i % 3 else []) for i in range(args.members)]
    for m in members:
        m.status, m.activity, m.activities, m.nick, m.joined_at = "online", None, (), None, None
        m.top_role = m.roles[0] if m.roles else team
        m.color = SimpleNamespace(value=0x5B5CF0)
    guild = FakeGuild(gid=ws.GUILD_ID, members=members, roles=[team, lead],
                      channels=[FakeChannel(10, "allgemein"), FakeChannel(11, "team-logs")], owner_id=1)
    by_id = {m.id: m for m in members}
    guild.get_member = lambda uid: by_id.get(int(uid))  # discord.py: O(1) dict lookup, like the real cache
    guild.categories = []
    for m in members:
        m.guild = guild
    guild.default_role = SimpleNamespace(id=guild.id, name="@everyone", permissions=discord.Permissions(view_channel=True))
    for role in guild.roles:
        role.permissions = discord.Permissions.none()
    for channel in guild.channels:
        channel.overwrites = {}
    ws.app.state.bot = SimpleNamespace(get_guild=lambda _: guild, get_user=lambda _: None, cogs={},
                                       get_cog=lambda _name: None, is_ready=lambda: False,
                                       is_closed=lambda: False, latency=0.0, guilds=[guild], user=None,
                                       intents=discord.Intents.default())
    ws.init_runtime()
    ws.save_json(ws.CONFIG_FILE, {"team_role_ids": [100, 101], "permissions": {"101": {k: True for k in ws.PERM_KEYS}}})
    _seed(ws, args.history, [m.id for m in members if m.roles])
    user = members[0]

    def fake_auth(request, session, perm="can_view_dashboard", admin=False):
        config = ws.load_config()
        perms, member = ws.compute_perms(guild, user.id, config)
        q = request.query_params
        return SimpleNamespace(user={"id": str(user.id), "global_name": user.display_name, "username": user.name},
                               guild=guild, guild_name=guild.name, perms=perms, config=config, member=member,
                               msg=q.get("msg", ""), msg_ok=q.get("t", "ok") != "err")

    ws.auth = fake_auth
    paths = sorted({r.path for r in ws.app.routes if "GET" in (getattr(r, "methods", None) or set())
                    and "{" not in r.path and not r.path.startswith(("/static", "/docs", "/redoc", "/openapi", "/login",
                                                                     "/callback", "/logout", "/api/github"))
                    and not p_is_stream(r.path)})
    if args.only:
        paths = [p.strip() for p in args.only.split(",") if p.strip()]
    counter = _Counter()
    results = {}
    with TestClient(ws.app, raise_server_exceptions=False) as client:
        for p in paths:  # warm-up: first call imports lazily and fills the caches
            t = time.perf_counter()
            client.get(p, follow_redirects=False)
            print(f"  warm-up {p}: {round((time.perf_counter() - t) * 1000)} ms", file=sys.stderr, flush=True)
        counter.install()
        try:
            for p in paths:
                times, statuses = [], set()
                for _ in range(args.runs):
                    counter.reset()
                    t = time.perf_counter()
                    r = client.get(p, follow_redirects=False)
                    times.append((time.perf_counter() - t) * 1000)
                    statuses.add(r.status_code)
                results[p] = {"ms": round(statistics.median(times), 1), "status": sorted(statuses),
                              "json_parses": counter.parses, "json_mb": round(counter.parsed_bytes / 1048576, 2),
                              "sqlite_connects": counter.connects, "bytes": len(r.content)}
        finally:
            counter.uninstall()
    summary = {"python": sys.version.split()[0], "history": args.history, "members": args.members, "runs": args.runs,
               "import_webserver_s": round(import_s, 2), "rss_start_mb": rss_start, "rss_after_import_mb": rss_import,
               "rss_end_mb": _rss_mb(), "data_mb": {f: round(os.path.getsize(Path(tmp) / f) / 1048576, 2)
                                                   for f in ("shifts.json", "logs.json", "applications.json", "audit_logs.json")},
               "pages": results}
    slow = sorted(results.items(), key=lambda kv: -kv[1]["ms"])
    print(f"Python {summary['python']} · Import webserver {summary['import_webserver_s']} s · RSS {rss_start} → {rss_import} → {summary['rss_end_mb']} MB")
    print(f"Daten (MB): {summary['data_mb']}")
    print(f"{'Seite':40} {'ms':>8} {'Status':>8} {'JSON':>5} {'JSON-MB':>8} {'SQLite':>6}")
    for p, r in slow:
        print(f"{p:40} {r['ms']:>8} {','.join(map(str, r['status'])):>8} {r['json_parses']:>5} {r['json_mb']:>8} {r['sqlite_connects']:>6}")
    total = sum(r["ms"] for r in results.values())
    print(f"Summe Median über {len(results)} Seiten: {round(total)} ms")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
