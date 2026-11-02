"""Reproducible, offline benchmark for the Melonly JSON/SQLite repositories.

Examples:
    python tools/melonly_bench.py
    python tools/melonly_bench.py --entries 20000,100000 --repeats 3

All records and databases are synthetic and live in a TemporaryDirectory. The
benchmark verifies result parity before recording timings. It never switches
the production backend or reads the configured Pulse data directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from pulse_melonly_repository import JsonMelonlyRepository
from pulse_melonly_store import MelonlyStore


ACTION_TYPES = ("Warn", "Ban", "Kick", "Notiz")


def synthetic_entries(count: int, players: int) -> list[dict[str, Any]]:
    """Create deterministic entries with repeatable user and ID histories."""
    rows: list[dict[str, Any]] = []
    for index in range(count):
        player = index % players
        rows.append({
            "id": f"melonly-{index:08d}",
            "target_user": f"Spieler-{player:05d}",
            "roblox_id": str(1_000_000_000 + player),
            "type": ACTION_TYPES[index % len(ACTION_TYPES)],
            "reason": f"Synthetischer Fall {index:08d} marker-{index:08d}",
            "moderator": f"Moderator-{index % 30:02d}",
            "moderator_id": str(2_000_000_000 + index % 30),
            "created_at": f"2026-10-{(index % 28) + 1:02d} 12:00",
            "fixture_sequence": index,
        })
    return rows


def _json_repository(path: Path) -> JsonMelonlyRepository:
    def load_json(source: str, default: Any) -> Any:
        try:
            return json.loads(Path(source).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def save_json(target: str, payload: Any) -> None:
        Path(target).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return JsonMelonlyRepository(path, load_json, save_json, threading.RLock())


def _operations(rows: list[dict[str, Any]]) -> dict[str, Callable[[Any], Any]]:
    middle = len(rows) // 2
    player_slot = rows[middle]["fixture_sequence"] % 5_000
    player_name = f"Spieler-{player_slot:05d}"
    player_id = str(1_000_000_000 + player_slot)
    unique_marker = f"marker-{len(rows) - 1:08d}"
    record_id = rows[-1]["id"]
    return {
        "count": lambda repo: repo.count(),
        "count_by_type": lambda repo: repo.count_by_type(),
        "latest_100": lambda repo: repo.latest(100),
        "latest_matching_types_100": lambda repo: repo.latest_matching_types(("Warn", "Ban"), 100),
        "find_first_by_id": lambda repo: repo.find_first_by_id(record_id),
        "player_history_by_name": lambda repo: repo.player_history(player_name),
        "player_history_by_roblox_id": lambda repo: repo.player_history(player_id),
        "roblox_user_history": lambda repo: repo.roblox_user_history(player_id, player_name),
        "roblox_suggestions_1000": lambda repo: repo.roblox_suggestions(1_000),
        "search_payload_unique": lambda repo: repo.search_payload(unique_marker),
        "export_all": lambda repo: repo.export_all(),
        "iter_all_500": lambda repo: list(repo.iter_all(500)),
    }


def _measure(operation: Callable[[], Any], repeats: int) -> float:
    operation()
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        samples.append((time.perf_counter() - started) * 1_000)
        del result
    return round(statistics.median(samples), 3)


def benchmark_size(entry_count: int, repeats: int, players: int) -> dict[str, Any]:
    rows = synthetic_entries(entry_count, players)
    with tempfile.TemporaryDirectory(prefix="pulse-melonly-bench-") as temporary:
        root = Path(temporary)
        json_path = root / "logs.json"
        database_path = root / "pulse.db"

        json_bytes = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        json_path.write_bytes(json_bytes)
        source_hash = hashlib.sha256(json_bytes).hexdigest()

        store = MelonlyStore(database_path)
        store.initialize()
        import_started = time.perf_counter()
        imported = store.import_entries(rows, source_sha256=source_hash)
        sqlite_import_ms = round((time.perf_counter() - import_started) * 1_000, 3)
        if imported != entry_count or store.count() != entry_count:
            raise RuntimeError("SQLite-Import lieferte eine abweichende Zeilenanzahl.")

        json_repo = _json_repository(json_path)
        operations = _operations(rows)
        timings: dict[str, dict[str, float]] = {}

        for name, operation in operations.items():
            json_result = operation(json_repo)
            sqlite_result = operation(store)
            if json_result != sqlite_result:
                raise RuntimeError(
                    f"Ergebnis-Parität fehlgeschlagen: {name} bei {entry_count} Einträgen."
                )
            del json_result, sqlite_result

            json_ms = _measure(lambda: operation(json_repo), repeats)
            sqlite_ms = _measure(lambda: operation(store), repeats)
            timings[name] = {
                "json_median_ms": json_ms,
                "sqlite_median_ms": sqlite_ms,
                "json_to_sqlite_ratio": round(json_ms / sqlite_ms, 3) if sqlite_ms else 0.0,
            }

        return {
            "entries": entry_count,
            "players": players,
            "repeats": repeats,
            "parity": "passed",
            "json_bytes": len(json_bytes),
            "sqlite_bytes": database_path.stat().st_size,
            "sqlite_import_ms": sqlite_import_ms,
            "operations": timings,
        }


def run_benchmark(entries: tuple[int, ...], repeats: int = 3, players: int = 5_000) -> dict[str, Any]:
    if not entries or any(value < 1 or value > 100_000 for value in entries):
        raise ValueError("Eintragszahlen müssen zwischen 1 und 100000 liegen.")
    if repeats < 1 or repeats > 10:
        raise ValueError("Wiederholungen müssen zwischen 1 und 10 liegen.")
    if players < 1 or players > 100_000:
        raise ValueError("Spielerzahl muss zwischen 1 und 100000 liegen.")
    return {
        "benchmark": "Pulse Melonly JSON vs SQLite",
        "synthetic_data_only": True,
        "production_backend_changed": False,
        "results": [benchmark_size(size, repeats, players) for size in entries],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline-Benchmark für Melonly JSON und SQLite.")
    parser.add_argument("--entries", default="20000,100000",
                        help="Kommagetrennte Datensatzgrößen (jeweils 1 bis 100000).")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--players", type=int, default=5_000)
    parser.add_argument("--json", default="", help="Optionaler Pfad für die JSON-Ergebnisdatei.")
    args = parser.parse_args()
    try:
        entries = tuple(int(value.strip()) for value in args.entries.split(",") if value.strip())
        result = run_benchmark(entries, args.repeats, args.players)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json:
        Path(args.json).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
