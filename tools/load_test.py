"""Reproducible local load test (development only): synthetic team, tickets, tasks and events against the real
dashboard routes – fake Discord server, fake login, temporary data. Never touches Discord or production data.

    python tools/load_test.py [--members 300] [--tickets 5000] [--tasks 2000] [--events 20000] [--rounds 3]
    python tools/load_test.py --profile member_stats   (or a route; handlers run in a worker thread)

Prints per route: median/max response time, plus Python heap peak (tracemalloc) and process RSS (Linux: /proc,
Windows: GetProcessMemoryInfo). RAM target of Pulse: 250 MB for the whole process.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ("/dashboard", "/team", "/tickets", "/ultimate", "/ultimate/team", "/ultimate/cases", "/suite/search?q=ticket",
          "/ultimate/search?q=max", "/pulse-inbox", "/tasks", "/admin/security", "/ultimate/automations", "/health")


def rss_mb() -> float | None:
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / 1024 / 1024
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--members", type=int, default=300)
    parser.add_argument("--tickets", type=int, default=5000)
    parser.add_argument("--tasks", type=int, default=2000)
    parser.add_argument("--events", type=int, default=20000)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--profile", default="", help="route to profile once with cProfile (top 25 by cumulative time)")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    tmp = tempfile.mkdtemp(prefix="pulse-load-")
    os.environ["PULSE_DB_PATH"] = str(Path(tmp) / "pulse.db")
    sys.path.insert(0, str(ROOT))
    os.chdir(tmp)
    rss_start = rss_mb()
    tracemalloc.start()

    import discord
    from fastapi.testclient import TestClient

    import pulse_db
    import webserver as ws
    from tests.fakes import FakeChannel, FakeGuild, FakeMember, FakeRole

    for name in ("DATA_FILE", "CONFIG_FILE", "LOGS_FILE", "SHIFTS_FILE", "MEETINGS_FILE", "APPS_FILE", "AUDIT_FILE"):
        setattr(ws, name, str(Path(tmp) / Path(getattr(ws, name)).name))
    ws.BASE_DIR = Path(tmp)
    ws.DB_ABMELDUNGEN = str(Path(tmp) / "abmeldungen.db")
    team, lead = FakeRole(100, "Supporter", position=3), FakeRole(101, "Leitung", position=5)
    members = [FakeMember(1, "Admin Anna", roles=[lead], admin=True)]
    members += [FakeMember(1000 + i, f"Mitglied {i}", roles=[team if i % 10 else lead]) for i in range(args.members)]
    for m in members:
        m.status, m.activity, m.activities, m.nick, m.joined_at = "online", None, (), None, None
        m.top_role = m.roles[0]
        m.color = SimpleNamespace(value=0x5B5CF0)
    guild = FakeGuild(gid=ws.GUILD_ID, members=members, roles=[team, lead],
                      channels=[FakeChannel(10, "allgemein"), FakeChannel(11, "team-logs")], owner_id=1)
    guild.categories = []
    guild.default_role = SimpleNamespace(id=guild.id, name="@everyone", permissions=discord.Permissions(view_channel=True))
    for role in guild.roles:
        role.permissions = discord.Permissions.none()
    for channel in guild.channels:
        channel.overwrites = {}
    ws.app.state.bot = SimpleNamespace(get_guild=lambda _: guild, get_user=lambda _: None, cogs={}, get_cog=lambda _: None,
                                       get_channel=lambda _: None, user=None, latency=0.05, guilds=[guild], is_ready=lambda: True)
    ws.init_runtime()
    ws.save_json(ws.CONFIG_FILE, {"team_role_ids": [100, 101], "permissions": {"101": {k: True for k in ws.PERM_KEYS}}})

    t0 = time.perf_counter()
    for i in range(args.tickets):
        tid = pulse_db.create_ticket(9_000_000 + i, guild.id, 1000 + i % max(1, args.members), f"Nutzer {i}", "support")
        if i % 3 == 0:
            pulse_db.close_ticket(tid, "erledigt")
    for i in range(args.tasks):
        pulse_db.create_task(f"Aufgabe {i}", "Beschreibung", str(1000 + i % max(1, args.members)), "M", "1", "Admin")
    with pulse_db.connect() as db:  # bulk insert: the same rows record_event writes, in one transaction
        db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                       [(f"event_load_{i}", "Lasttest", "user", str(1000 + i % max(1, args.members)), "1", "Admin", "{}",
                         "2026-10-24T12:00:00+00:00") for i in range(args.events)])
    seed_seconds = time.perf_counter() - t0
    admin = members[0]
    ws.get_current_user = lambda _session=None: {"id": str(admin.id), "username": "anna", "global_name": admin.display_name}
    client = TestClient(ws.app, raise_server_exceptions=False, cookies={"user_session": "load"})

    print(f"Daten: {args.members} Mitglieder, {args.tickets} Tickets, {args.tasks} Aufgaben, {args.events} Ereignisse "
          f"(Aufbau {seed_seconds:.1f} s) · Python {sys.version.split()[0]} · {sys.platform}")
    if args.profile:
        import cProfile
        import pstats
        profiler = cProfile.Profile()
        if args.profile == "member_stats":  # the team loop itself (the HTTP handler runs in another thread)
            import pulse_ultimate
            team = pulse_ultimate.team_members(guild)
            shared = pulse_ultimate.stats_context()
            profiler.runcall(lambda: [pulse_ultimate.member_stats(m, shared) for m in team])
        else:
            profiler.runcall(client.get, args.profile, follow_redirects=False)
        pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)
        return 0
    print(f"{'Route':32} {'Status':>6} {'Median ms':>10} {'Max ms':>8}")
    for route in ROUTES:
        times, status = [], None
        for _ in range(args.rounds):
            started = time.perf_counter()
            status = client.get(route, follow_redirects=False).status_code
            times.append((time.perf_counter() - started) * 1000)
        print(f"{route:32} {status:>6} {statistics.median(times):>10.1f} {max(times):>8.1f}")
    current, peak = tracemalloc.get_traced_memory()
    rss_end = rss_mb()
    print(f"Python-Heap aktuell {current / 1024 / 1024:.1f} MB · Spitze {peak / 1024 / 1024:.1f} MB (tracemalloc)")
    if rss_end is not None:
        print(f"Prozess-RSS Start {rss_start or 0:.0f} MB → Ende {rss_end:.0f} MB (Ziel ≤ 250 MB)")
    print(f"Temp-Daten: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
