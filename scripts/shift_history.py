"""Phase 14.7: where is the shift history, and is it complete? Read-only.

    python scripts/shift_history.py status     # Docker: docker compose exec pulse python scripts/shift_history.py status

Prints the backend (json/sqlite), the configured PULSE_SHIFT_HISTORY_BACKEND, the number of entries in
shifts.json/pulse.db and whether every stored row can be read. Changes nothing: switching happens only at the
start of Pulse when PULSE_SHIFT_HISTORY_BACKEND is set (see docs/PHASE14_7_BERICHT.md).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str]) -> int:
    if argv[1:] not in ([], ["status"]):
        print(__doc__)
        return 2
    import pulse_shift_history
    import webserver as ws
    info = pulse_shift_history.status(ws)
    if info.get("backend") == "sqlite" and "error" not in info:
        try:
            info["readable_rows"] = len(pulse_shift_history.read_history())
        except Exception as exc:  # report, never repair
            info["error"] = f"{type(exc).__name__}: {exc}"
    # Phase 14.7b: "pending" = a save committed in pulse.db but not yet in shifts.json (Pulse finishes it at start)
    info["consistent"] = "error" not in info and not info.get("pending") and (
        info.get("backend") != "sqlite" or info.get("marker_rows") == info.get("db_rows") == info.get("readable_rows"))
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0 if info["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
