"""Zählt die 177 Zeilen von docs/PULSE_FEATURE_AUDIT_177.md automatisch (nie von Hand).

    python tools/audit_counts.py            # Zählung ausgeben, Abweichungen der Übersichtstabellen melden (Exit 1)
    python tools/audit_counts.py --write    # Gesamtübersicht und „Ergebnis nach Bereichen“ aus den Zeilen neu schreiben

Maßgeblich sind nur die Einzelbewertungs-Zeilen `| Nr. | Funktion | **Status** | … |`. Die Kopfzeile
„**N fertig / M teilweise / K offen**“ und docs/PULSE_177_IMPLEMENTATION_PROGRESS.md werden von
tests/test_feature_audit_counts.py gegen diese Zählung geprüft.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "PULSE_FEATURE_AUDIT_177.md"
STATUSES = ("Fertig", "Teilweise", "Offen")
ROW = re.compile(r"^\| (\d+) \| (.+?) \| \*\*(Fertig|Teilweise|Offen)\*\* \|")
AREA = re.compile(r"^### \d+\. (.+?) \((\d+)–(\d+)\)\s*$")


def parse(text: str) -> tuple[dict[int, str], list[tuple[str, int, int]]]:
    rows: dict[int, str] = {}
    areas: list[tuple[str, int, int]] = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match:
            if int(match.group(1)) in rows:
                raise ValueError(f"Funktion {match.group(1)} kommt doppelt vor")
            rows[int(match.group(1))] = match.group(3)
        area = AREA.match(line)
        if area:
            areas.append((area.group(1), int(area.group(2)), int(area.group(3))))
    return rows, areas


def totals(rows: dict[int, str]) -> dict[str, int]:
    count = Counter(rows.values())
    return {status: count.get(status, 0) for status in STATUSES}


def render_totals(rows: dict[int, str]) -> str:
    t = totals(rows)
    return ("| Status | Anzahl |\n|---|---:|\n" + "\n".join(f"| {s} | {t[s]} |" for s in STATUSES)
            + f"\n| **Gesamt** | **{sum(t.values())}** |")


def render_areas(rows: dict[int, str], areas) -> str:
    out = ["| Bereich | Fertig | Teilweise | Offen |", "|---|---:|---:|---:|"]
    for name, first, last in areas:
        count = Counter(rows[n] for n in range(first, last + 1) if n in rows)
        out.append(f"| {name} | {count.get('Fertig', 0)} | {count.get('Teilweise', 0)} | {count.get('Offen', 0)} |")
    return "\n".join(out)


def _replace_table(text: str, heading: str, table: str) -> str:
    start = text.index(heading) + len(heading)
    first = text.index("|", start)
    end = first
    for line in text[first:].split("\n"):
        if not line.startswith("|"):
            break
        end += len(line) + 1
    return text[:first] + table + "\n" + text[end:]


def rewrite(text: str) -> str:
    rows, areas = parse(text)
    text = _replace_table(text, "## Gesamtübersicht", render_totals(rows))
    return _replace_table(text, "## Ergebnis nach Bereichen", render_areas(rows, areas))


def main(argv: list[str]) -> int:
    text = AUDIT.read_text(encoding="utf-8")
    rows, _ = parse(text)
    t = totals(rows)
    print(f"{t['Fertig']} fertig / {t['Teilweise']} teilweise / {t['Offen']} offen (Zeilen: {len(rows)})")
    if len(rows) != 177 or set(rows) != set(range(1, 178)):
        print("FEHLER: erwartet werden genau die Funktionen 1–177")
        return 1
    new = rewrite(text)
    if "--write" in argv:
        if new != text:
            AUDIT.write_text(new, encoding="utf-8")
            print("Übersichtstabellen neu geschrieben.")
        return 0
    if new != text:
        print("FEHLER: Übersichtstabellen weichen von den Zeilen ab (python tools/audit_counts.py --write)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
