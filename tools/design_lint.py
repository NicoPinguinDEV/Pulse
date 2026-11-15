#!/usr/bin/env python3
"""Audit #65: Designsystem-Wächter - zählt feste Farbwerte in Inline-Stilen der Seitenmodule (Ratsche: darf nur sinken).

    python tools/design_lint.py            # Zählung je Datei ausgeben, bei Überschreitung der Grundlinie Exit 1
    python tools/design_lint.py --write    # Grundlinie neu schreiben (nur nach bewusster Bereinigung, nie hochsetzen)

Gezählt werden ``style="…#rrggbb…"``- und ``style='…#rrggbb…'``-Vorkommen in Python-Quelltexten (außerhalb von tests/, tools/ und der
Designsystem-Dateien selbst). Neue Seiten sollen Tokens und Komponenten aus ``pulse_design.py`` / ``static/pulse.css`` nutzen;
die Grundlinie hält fest, wie viele ältere Stellen noch umgestellt werden müssen.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "design_lint_baseline.json"
EXCLUDED_DIRS = {"tests", "tools", "node_modules", ".git", "__pycache__", "static", ".claude", "docs", "deploy", "scripts"}
DESIGN_FILES = {"pulse_design.py", "pulse_pro.py", "pulse_flags.py"}  # Quellen des Designsystems / globale Stile
_STYLE = re.compile(r"""style\s*=\s*(?:\\?["'])[^"'>]*#[0-9a-fA-F]{3,8}""")


def files():
    for path in sorted(ROOT.glob("*.py")) + sorted((ROOT / "cogs").glob("*.py")):
        if path.name in DESIGN_FILES or any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts[:-1]):
            continue
        yield path


def count(path: Path) -> int:
    try:
        return len(_STYLE.findall(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return 0


def measure() -> dict[str, int]:
    return {str(p.relative_to(ROOT)).replace("\\", "/"): n for p in files() if (n := count(p))}


def main(argv) -> int:
    current = measure()
    total = sum(current.values())
    if "--write" in argv:
        BASELINE.write_text(json.dumps({"total": total, "files": current}, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Grundlinie geschrieben: {total} Stellen in {len(current)} Dateien")
        return 0
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"total": 0, "files": {}}
    worse = {f: (base["files"].get(f, 0), n) for f, n in current.items() if n > base["files"].get(f, 0)}
    for f, (old, new) in sorted(worse.items()):
        print(f"MEHR feste Farben: {f}: {old} -> {new}")
    print(f"Gesamt {total} (Grundlinie {base['total']})")
    return 1 if worse or total > base["total"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
