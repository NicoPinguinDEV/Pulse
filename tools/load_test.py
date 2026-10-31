"""Reproducible local load test (development only): synthetic team, tickets, tasks and events against the real
dashboard routes – fake Discord server, fake login, temporary data. Never touches Discord or production data.

    python tools/load_test.py [--members 300] [--tickets 5000] [--tasks 2000] [--events 20000] [--rounds 3]
    python tools/load_test.py --profile member_stats   (or a route; handlers run in a worker thread)
    python tools/load_test.py --concurrency 4 --soak-seconds 300   (parallel clients, then a soak run)

Prints per route: median/p95/p99/max response time and errors (status >= 500 or exception), plus Python heap peak
(tracemalloc), process RSS (Linux: /proc, Windows: GetProcessMemoryInfo) and – inside a container – the cgroup
memory (memory.current / memory.peak). The soak run repeats all routes and samples RSS to show growth.
Guard thresholds are 500/620/700 MB; Docker container budget is 768 MiB (.github/workflows/perf.yml runs it there).
Measurements on Windows, in CI and on the VPS are not comparable – always report where a number comes from.
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


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (no numpy needed); 0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[rank - 1]


def cgroup_mb() -> dict:
    """cgroup v2 memory of the container this runs in (empty outside a container / on Windows)."""
    out = {}
    for key in ("memory.current", "memory.peak", "memory.max"):
        try:
            raw = Path("/sys/fs/cgroup", key).read_text(encoding="ascii").strip()
        except OSError:
            continue
        out[key] = None if raw == "max" else int(raw) / 1024 / 1024
    return out


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
    parser.add_argument("--concurrency", type=int, default=1, help="parallel clients per route (each its own TestClient)")
    parser.add_argument("--soak-seconds", type=int, default=0, help="afterwards repeat all routes this long and sample RSS")
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
    # Guard (P8-06): webserver.py changed into the repository on import until Phase 9 – stay in the temp folder, so
    # relative runtime files (backups/, logs/, …) never land in the checkout, which may hold real data.
    os.chdir(tmp)
    ws.BACKUP_DIR = str(Path(tmp) / "backups")
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
    from concurrent.futures import ThreadPoolExecutor

    workers = max(1, args.concurrency)
    clients = [client] + [TestClient(ws.app, raise_server_exceptions=False, cookies={"user_session": "load"})
                          for _ in range(workers - 1)]

    def timed(client_index: int, route: str) -> tuple[float, int | None]:
        started = time.perf_counter()
        try:
            status = clients[client_index].get(route, follow_redirects=False).status_code
        except Exception:
            status = None
        return (time.perf_counter() - started) * 1000, status

    total_requests = total_errors = 0
    print(f"Parallele Clients: {workers} · Runden je Client: {args.rounds}")
    print(f"{'Route':32} {'Status':>6} {'Median':>8} {'p95':>8} {'p99':>8} {'Max ms':>8} {'Fehler':>6}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for route in ROUTES:
            jobs = [pool.submit(timed, i % workers, route) for i in range(args.rounds * workers)]
            results = [j.result() for j in jobs]
            times = [t for t, _ in results]
            statuses = [s for _, s in results]
            errors = sum(1 for s in statuses if s is None or s >= 500)
            total_requests += len(results)
            total_errors += errors
            status = max((s for s in statuses if s is not None), default=None)
            print(f"{route:32} {status!s:>6} {statistics.median(times):>8.1f} {percentile(times, 95):>8.1f} "
                  f"{percentile(times, 99):>8.1f} {max(times):>8.1f} {errors:>6}")
        if args.soak_seconds > 0:
            soak_end, samples, soak_times = time.monotonic() + args.soak_seconds, [], []
            next_sample = 0.0
            while time.monotonic() < soak_end:
                if time.monotonic() >= next_sample:
                    samples.append(rss_mb() or 0.0)  # 0 = not measurable (e.g. Windows without access)
                    next_sample = time.monotonic() + 10
                results = list(pool.map(lambda pair: timed(*pair), [(i % workers, r) for i, r in enumerate(ROUTES)]))
                soak_times += [t for t, _ in results]
                total_requests += len(results)
                total_errors += sum(1 for _, s in results if s is None or s >= 500)
            samples.append(rss_mb() or 0.0)  # 0 = not measurable (e.g. Windows without access)
            print(f"Soak {args.soak_seconds} s: {len(soak_times)} Anfragen · p95 {percentile(soak_times, 95):.1f} ms · "
                  f"p99 {percentile(soak_times, 99):.1f} ms · RSS erste/letzte/max {samples[0]:.0f}/{samples[-1]:.0f}/"
                  f"{max(samples):.0f} MB (Wachstum {samples[-1] - samples[0]:+.0f} MB)")
    print(f"Anfragen gesamt {total_requests} · Fehler {total_errors} ({100 * total_errors / max(1, total_requests):.1f} %)")
    current, peak = tracemalloc.get_traced_memory()
    rss_end = rss_mb()
    print(f"Python-Heap aktuell {current / 1024 / 1024:.1f} MB · Spitze {peak / 1024 / 1024:.1f} MB (tracemalloc)")
    if rss_end is not None:
        print(f"Prozess-RSS Start {rss_start or 0:.0f} MB → Ende {rss_end:.0f} MB (Warnschwelle 500 MB)")
    cgroup = cgroup_mb()
    if cgroup:
        print("cgroup: " + " · ".join(f"{k} {'unbegrenzt' if v is None else f'{v:.0f} MB'}" for k, v in cgroup.items()))
    print(f"Temp-Daten: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
