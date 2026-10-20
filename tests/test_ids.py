"""A-09: all fixed Discord IDs live in pulse_ids.py, the modules use exactly those values."""
import json
import re
import unittest
from pathlib import Path

import pulse_ids

ROOT = Path(__file__).resolve().parents[1]


def normalise(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, dict):
        return {str(k): normalise(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [normalise(v) for v in value]
    return value


class IdTests(unittest.TestCase):
    def test_values_match_the_snapshot(self):
        snapshot = json.loads((ROOT / "tests" / "ids_snapshot.json").read_text(encoding="utf-8"))
        current = {k: normalise(v) for k, v in vars(pulse_ids).items() if k.isupper()}
        self.assertEqual(current, {k: normalise(v) if not isinstance(v, dict) else {str(a): b for a, b in v.items()}
                                   for k, v in snapshot.items()})

    def test_modules_use_the_central_values(self):
        import webserver
        from cogs import honeypot, tickets
        import pulse_verification
        self.assertEqual(webserver.WARN_ROLE_IDS, pulse_ids.WARN_ROLE_IDS)
        self.assertEqual(webserver.TEAM_KICK_KEEP_ROLE_IDS, (1531303733311307997, 1489912887949394082))
        self.assertEqual(tickets.DEFAULT_TICKET_OPEN_LOG_CHANNEL_ID, 1543914075308818503)
        self.assertEqual(tickets.DEFAULT_TICKET_CLAIM_LOG_CHANNEL_ID, 1535025111722098778)
        self.assertEqual(honeypot.HONEYPOT_CHANNEL_ID, pulse_ids.HONEYPOT_CHANNEL_ID)
        self.assertEqual(pulse_verification.UNVERIFIED_ROLE_ID, 1532762237284913363)

    def test_no_new_module_level_id_literals(self):
        offenders = []
        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT)
            if set(rel.parts) & {"tests", "tools", "deps", "venv", ".venv", ".git"} or rel.name == "pulse_ids.py":
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.match(r"^[A-Z_]+\s*(:[^=]+)?=\s*.*\b\d{17,20}\b", line):
                    offenders.append(f"{rel}:{lineno}")
        self.assertEqual(offenders, [], "feste Discord-ID bitte in pulse_ids.py eintragen")


if __name__ == "__main__":
    unittest.main()
