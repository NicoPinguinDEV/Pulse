"""Stdlib-only smoke test for the Pulse 5 database layer.
Run with: python tests/smoke_test.py
"""
from __future__ import annotations

import os
import shutil
import tempfile
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_BOOT = Path(tempfile.mkdtemp(prefix="pulse-smoke-"))
os.environ["PULSE_DB_PATH"] = str(_BOOT / "pulse.db")
import pulse_db as db


def main() -> None:
    try:
        db.init_db()
        first = db.notify(1, "Test", "Inhalt", dedupe_key="smoke")
        assert first == db.notify(1, "Test", "Inhalt", dedupe_key="smoke")

        task = db.create_task("Smoke-Aufgabe", "Test", 2, "Nico", 1, "Admin", "high")
        assert db.update_task(task, status="done", actor_id=1, actor_name="Admin")
        assert db.get_task(task)["archived_at"]

        ticket = db.create_ticket("10", "20", "2", "Nico", "support")
        assert db.claim_ticket(ticket, 1, "Admin")
        assert not db.claim_ticket(ticket, 2, "Andere")
        assert db.unclaim_ticket(ticket, 1, "Admin")
        assert db.close_ticket(ticket, "Smoke-Test", closed_by_id=1, closed_by_name="Admin")

        page = db.save_wiki("Smoke", "Test", "Inhalt", 1, "Admin")
        assert db.get_wiki(page)

        training = db.create_training(
            "Smoke-Test", "Test", 80, 10, 1, "Admin",
            [{"question": "2+2?", "options": ["3", "4"], "answer_index": 1}],
        )
        assert db.get_training(training)["questions"]
        assert db.save_attempt(training, 2, "Nico", 100, True)

        print("Pulse smoke test: OK")
    finally:
        shutil.rmtree(_BOOT, ignore_errors=True)


if __name__ == "__main__":
    main()
