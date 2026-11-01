"""Erzeugt docs/DATENLANDKARTE.md: Laufzeitdaten, Besitzer, Schreibstellen, Backup-Abdeckung.

Aufruf (aus dem Repo-Wurzelverzeichnis):
    python tools/data_map.py

Rein statische Analyse des Quelltexts – liest keine Laufzeitdaten und schreibt nur die Doku.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"tests", "deps", "venv", ".venv", ".git", "__pycache__", "tools"}

# Bekannte Laufzeitablagen (aus dem Code ermittelt; Ordner zusätzlich).
FILES = [
    "pulse.db", "abmeldungen.db", "activity_check.db", "pulse_suite.db", "teamliste.db", "buero_system.db",
    "rp_verwaltung.db", "flaggenquiz.db",
    "config.json", "team_data.json", "applications.json", "shifts.json", "logs.json", "audit_logs.json",
    "meetings.json", "pulse_announcement_archive.json", "role_nickname_state.json", "social_promo_sources.json",
    "ai_moderation_cases.json",
]
FOLDERS = ["uploads", "attachments", "transcripts", "backups"]
SPECIAL = [".session_secret"]
# Python-Konstanten, unter denen die Dateien im Code meist angesprochen werden.
ALIASES = {
    "config.json": ["CONFIG_FILE"], "team_data.json": ["DATA_FILE"], "applications.json": ["APPS_FILE"],
    "shifts.json": ["SHIFTS_FILE"], "logs.json": ["LOGS_FILE"], "audit_logs.json": ["AUDIT_FILE", "AUDIT_LOG_FILE"],
    "meetings.json": ["MEETINGS_FILE"], "abmeldungen.db": ["DB_ABMELDUNGEN", "DB_NAME"],
    "pulse.db": ["DB_PATH", "ULTIMATE_DB"],
}
WRITE_HINTS = re.compile(r"save_json\(|write_text\(|write_bytes\(|open\([^)]*['\"][wa]b?['\"]|\.execute\(\s*['\"](INSERT|UPDATE|DELETE|REPLACE|CREATE)|executemany\(|os\.replace\(|json\.dump\(|set_setting\(|connect\(")


def py_files():
    for path in sorted(ROOT.rglob("*.py")):
        if not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            yield path


def function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    return ""


def main() -> int:
    sources = {p: p.read_text(encoding="utf-8") for p in py_files()}
    rel = lambda p: str(p.relative_to(ROOT)).replace("\\", "/")

    usage: dict[str, dict] = {}
    for name in FILES + FOLDERS + SPECIAL:
        needles = [name] + ALIASES.get(name, [])
        modules, writers = set(), defaultdict(list)
        for path, text in sources.items():
            for lineno, line in enumerate(text.splitlines(), 1):
                if any(n in line for n in needles):
                    modules.add(rel(path))
                    if WRITE_HINTS.search(line):
                        writers[rel(path)].append(lineno)
        usage[name] = {"modules": sorted(modules), "writers": {k: v[:6] for k, v in writers.items()}}

    json_backup = function_source(ROOT / "webserver.py", "build_backup")
    zip_backup = function_source(ROOT / "pulse_next.py", "create_backup")
    db_src = (ROOT / "pulse_db.py").read_text(encoding="utf-8")
    tables_block = re.search(r"^TABLES\s*=\s*\[(.*?)\]", db_src, re.S | re.M)
    exported = set(re.findall(r"['\"]([a-z_0-9]+)['\"]", tables_block.group(1))) if tables_block else set()

    def covered(name, src):
        needles = [name] + ALIASES.get(name, [])
        return any(n in src for n in needles)

    # Alle Tabellen, die irgendwo in pulse.db angelegt werden.
    created = defaultdict(set)
    for path, text in sources.items():
        for table in re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-z_0-9]+)", text, re.I):
            created[table].add(rel(path))

    lines = [
        "# Datenlandkarte",
        "",
        "Automatisch erzeugt von `python tools/data_map.py` (statische Analyse, liest keine Laufzeitdaten).",
        "*Schreibstellen* sind Zeilen mit typischen Schreibaufrufen (`save_json`, `INSERT/UPDATE/DELETE`, `write_text`, `os.replace` …) – als Einstieg, nicht vollständig.",
        "",
        "## Dateien und Ordner",
        "",
        "| Ablage | Module (lesen/schreiben) | Schreibstellen (Auszug) | JSON-Backup `/backups` | ZIP-Backup `/ultimate/backup` |",
        "|---|---|---|---|---|",
    ]
    for name in FILES + FOLDERS + SPECIAL:
        info = usage[name]
        writes = "; ".join(f"{m}:{','.join(map(str, ls))}" for m, ls in sorted(info["writers"].items())) or "—"
        mods = ", ".join(f"`{m}`" for m in info["modules"]) or "—"
        j = "✅" if covered(name, json_backup) else "❌"
        z = "✅" if covered(name, zip_backup) else "❌"
        if name == "pulse.db":
            j = f"⚠️ nur {len(exported)} Tabellen über `export_state`"
            z = "✅ ganze Datei"
        lines.append(f"| `{name}` | {mods} | {writes} | {j} | {z} |")

    lines += [
        "",
        "## Tabellen (CREATE TABLE im Code) und JSON-Backup-Abdeckung von `pulse.db`",
        "",
        f"`pulse_db.export_state()` exportiert {len(exported)} Tabellen (`TABLES`). Tabellen anderer Datenbanken stehen nur informativ dabei.",
        "",
        "| Tabelle | angelegt in | im JSON-Backup (`export_state`) |",
        "|---|---|---|",
    ]
    for table in sorted(created):
        lines.append(f"| `{table}` | {', '.join(f'`{m}`' for m in sorted(created[table]))} | {'✅' if table in exported else '❌'} |")
    missing = sorted(t for t in created if t not in exported)
    lines += ["", f"**Nicht im JSON-Backup:** {len(missing)} von {len(created)} Tabellen.", ""]
    lines += [
        "## Speicherort (Phase 13, `pulse_paths`)",
        "",
        "- Alle oben genannten Ablagen liegen unter **`PULSE_DATA_DIR`** (`pulse_paths.DATA_DIR`): im Docker-Setup `/data`, "
        "auf dem VPS `/opt/pulse/data`. Ohne `PULSE_DATA_DIR` gilt wie bisher das Arbeitsverzeichnis (Bot-Hosting, lokal).",
        "- Zusätzlich dort: `logs/` (`logs/pulse.log`), `tmp/` (`TMPDIR`), die Markierung `.pulse-data.json` (Zustand der Migration) "
        "und die hochgeladene Wartemusik `assets/support_wait.*`. Mitgelieferte Assets (`assets/*.b64`, `static/`) bleiben im Code.",
        "- `.env` liegt nicht im Datenverzeichnis, sondern neben `compose.yaml` auf dem Host und wird als Umgebungsvariablen übergeben.",
        "- Übernahme aus dem alten Checkout: `scripts/pulse_migrate_data.py` (Probelauf, Backup, Prüfsummen, keine Überschreibungen).",
        "",
        "## Komplett-Backup `pulse_backup` (aktueller Stand, maßgeblich)",
        "",
        "- Enthält alle Datenbanken aus `pulse_backup.SQLITE_FILES`, alle JSON-Dateien aus `JSON_FILES`, die Ordner "
        "`uploads/`, `attachments/`, `transcripts/` (im Rahmen von `PULSE_BACKUP_MAX_MB`, Übersprungenes steht im Manifest) "
        "und die hochgeladene Wartemusik `assets/support_wait.{mp3,ogg,wav}`; bewusst nicht: `backups/`, `.env`, `.session_secret`.",
        "- `tests/test_full_backup.py` (CoverageTests) schlägt fehl, wenn eine neue `*.db`/`*.json`-Ablage im Code auftaucht, "
        "die weder gesichert noch bewusst ausgenommen ist.",
        "- Der Restore-Test (`pulse_backup.restore_test`, wöchentlich und im Systemstatus) stellt das ganze Archiv probeweise in ein "
        "Temp-Verzeichnis wieder her: Prüfsummen, Integrität jeder Datenbank, Tabellen/Zeilen, JSON-Gültigkeit und für jeden "
        "Eintrag ein Wiederherstellungsziel.",
        "- Die Abschnitte unten beschreiben den Stand **vor** dem Komplett-Backup und die älteren Backup-Wege (JSON-Backup, ZIP-Backup).",
        "",
        "## Manuell geprüfte Ergänzungen (Stand P0-3, 20.10.2026)",
        "",
        "- **ZIP-Backup und `backups/`:** Der Ordner ist nur das *Ziel* der ZIP-Datei (`pulse_next.create_backup`), kein Inhalt – die Spalte zeigt dort fälschlich ✅.",
        "- **Transkripte im JSON-Backup:** Dateien über 2 MB oder über 10 MB gesamt werden **still übersprungen** (`webserver.build_backup`).",
        "- **ZIP-Backup:** ganze `pulse.db` + 7 JSON-Dateien + `attachments/` bis zu einem Größenlimit; ohne `abmeldungen.db`, `activity_check.db`, die fünf weiteren Datenbanken, `uploads/` und `transcripts/`.",
        "- **In keinem Backup:** `pulse_suite.db`, `teamliste.db`, `buero_system.db`, `rp_verwaltung.db`, `flaggenquiz.db`, `pulse_announcement_archive.json`, `role_nickname_state.json`, `social_promo_sources.json`, `ai_moderation_cases.json`, `uploads/` (Panel-/Banner-Bilder, Voice-Audio), `.session_secret`.",
        "- **Wachstum ohne Grenze:** `shifts.json` (`history` wird nie gekürzt), `logs.json` (kein Kürzen beim Speichern), `applications.json`. Begrenzt: `audit_logs.json` (letzte 5.000), Bewertungen Voice (1.000), Pending-Bewertungen (500), Verifikations-Fehler (10).",
        "- **Pfadabhängigkeit:** `PULSE_DB_PATH` gilt nur in `pulse_db.py`; `pulse_ultimate.py` nutzt fest `BASE/\"pulse.db\"` (D-05).",
        "",
    ]
    (ROOT / "docs" / "DATENLANDKARTE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Ablagen: {len(FILES) + len(FOLDERS) + len(SPECIAL)} · Tabellen: {len(created)} · nicht im JSON-Backup: {len(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
