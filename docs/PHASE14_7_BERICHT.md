# Phase 14.7 – Schichthistorie von shifts.json nach SQLite

**Basis:** `e4f3e85874cdd7144ca5ba6d99e3a6438a01f3b3` (Version 7.17.0)  
**Branch:** `phase/14-7-shift-history-sqlite`  
**Status:** umgesetzt und getestet. Die Produktionsumstellung ist **nicht** ausgeführt – sie braucht deine Freigabe (Ablauf in Abschnitt 4).

## 1. Vorherige Architektur

`shifts.json` = `{"active_shifts": {uid: {...}}, "history": [ ... ]}`. Die `history` wird nie gekürzt.
Jedes Schichtende hat die ganze Datei neu geschrieben (5,5 MB bei 20.000 Einträgen, mit fsync).

**Inventur der Zugriffe (gezielte Suche, kein Volltext-Einlesen):**

| Art | Stellen |
|---|---|
| Lesen über `load_shifts()` (zeigt >24-h-Schichten als beendet, schreibt nicht) | rund 40 Aufrufer in `webserver.py`, `pulse_pro.py`, `pulse_ultimate.py`, `pulse_next.py`, `pulse_intelligence.py`, `pulse_home.py`, `pulse_cockpit.py`, `pulse_presence.py`, `cogs/pulse_suite.py`, `cogs/pulse_pro.py`, `cogs/voice_support.py` |
| Lesen über `load_shifts_raw()` | `process_overdue_shifts`, `pulse_phase7.py` (4×), Schichtdauer bestätigen (`pulse_pro.py`) |
| Direktes `load_json(SHIFTS_FILE)` | `commit_shift_event`, `pulse_retention.export_person`, `pulse_consistency.check` |
| Direktes `open("shifts.json")` | `cogs/teamliste.py:186` – liest nur `active_shifts` |
| **Schreiben** (alle über `save_json(SHIFTS_FILE, …)`) | `apply_shift_action` (Start/Pause/Fortsetzen/Ende für Dashboard, Panel und `/dienst`, hängt an), `process_overdue_shifts` (Auto-Abschluss nach 24 h, hängt an), `commit_shift_event` (ändert einen Eintrag: Warn-Status), Schichtdauer bestätigen (`pulse_pro.py:1169`, ändert einen Eintrag), Führungskraft beendet Schicht (`pulse_pro.py:1209`, hängt an), Offboarding (`cogs/pulse_suite.py:1517`, hängt an) |
| Entfernen von Einträgen | keine Stelle im Code |
| Backup/Restore | `pulse_backup` (`json/shifts.json` + `db/pulse.db`), Legacy-Restore in `pulse_next` |

## 2. Neue Architektur

Neues Modul `pulse_shift_history.py`. Nach der Migration gilt:

* **`history` liegt in `pulse.db`**, Tabelle `shift_history(seq INTEGER PRIMARY KEY, payload TEXT, mod_id, shift_date)`.
  `seq` ist die Position in der Liste, damit bleibt die Reihenfolge erhalten.
  `payload` ist der Eintrag als JSON-Text. Alle Felder bleiben erhalten, auch unbekannte, in derselben Reihenfolge und mit Altformaten.
  Duplikate sind getrennte Zeilen; nichts wird zusammengeführt.
  `mod_id`/`shift_date` sind nur ein Index für spätere Abfragen.
  `shift_history_meta` speichert eine Generations-ID der Migration.
* **`active_shifts`** und alle anderen Schlüssel bleiben in `shifts.json`, dazu der Marker
  `"_history_store": {"backend": "sqlite", "rows": N, "generation": …}`. Der Marker entscheidet, wo die Historie liegt.
  Ein altes Backup von `shifts.json` (volle Historie, ohne Marker) bedeutet beim Zurückspielen also automatisch wieder JSON.
* **Eine einzige Lese- und Schreibstelle:** `webserver.load_json(SHIFTS_FILE)` und `save_json(SHIFTS_FILE)` leiten im SQLite-Modus an den Store weiter.
  Deshalb liefern `load_shifts()`, `load_shifts_raw()` und alle anderen Aufrufer unverändert `{"active_shifts": …, "history": [...]}`, ohne den Marker.
  Kein Aufrufer musste geändert werden, und keine zweite Schreibstelle kann den Store umgehen.
  `cogs/teamliste.py` liest weiterhin direkt, aber nur `active_shifts`, und die bleiben in der Datei.
* **Speichern** läuft unter `_io_lock` in einer SQLite-Transaktion (`BEGIN IMMEDIATE`):
  1. Nur geänderte Zeilen werden geschrieben (UPDATE/INSERT/DELETE nach Position).
  2. Dann wird die kleine `shifts.json` atomar ersetzt.
  3. Dann COMMIT.

  Schlägt ein Schritt fehl, gibt es ROLLBACK, und die vorherige `shifts.json` wird zurückgeschrieben (getestet für Schreib- und COMMIT-Fehler).
  Ein Aufruf ohne `history`-Schlüssel löscht im SQLite-Modus nie die Historie.
* **Schutz gegen falsche Stände:** Das Schreiben wird blockiert (wie bei einer beschädigten Datei, `JsonWriteBlocked`), statt mit falschen Daten weiterzuarbeiten, wenn:
  * pulse.db weniger Zeilen hat als der Marker,
  * die Tabelle fehlt,
  * die Generations-ID nicht passt (z. B. alte Zeilen nach einem Rückweg).

  Mehr Zeilen als erwartet ergeben nur eine Warnung.
* **Backup:** `pulse_backup._write_archive` kopiert `pulse.db` und `shifts.json` jetzt gemeinsam unter dem JSON-Lock.
  Ein Schichtende kann also nicht zwischen den beiden Kopien landen (Review-Befund, getestet).
* **Im JSON-Modus (Standard)** bleibt alles wie bisher. Hinzu kommt nur ein `os.stat` je Lese- und Schreibvorgang auf `shifts.json`.
  Ist der Zwischenspeicher veraltet, folgt eine Textsuche nach dem Marker, aber kein zweites Parsen.

## 3. Migration und Rückweg

Beide laufen über die vorhandene Infrastruktur `pulse_migrations.run` (keine zweite Architektur): pulse.db wird vorher gesichert, alles läuft in einer Transaktion und wird in `schema_migrations` eingetragen.
`MIGRATIONS` bleibt leer, deshalb ändert ein normaler Start nichts.

**Migration** (`migrate`, idempotent):
1. `shifts.json` wird gelesen und geprüft (`history` muss eine Liste sein). Bei schon gesetztem Marker ist das Ergebnis `already`, es wird nichts geändert.
2. `shifts.json` wird nach `backups/shifts_pre_sqlite_<Zeit>.json` kopiert, die Kopie per SHA-256 geprüft.
3. In einer Transaktion: Tabelle anlegen, alte Reste leeren, alle Einträge einfügen, Generations-ID setzen.
   Danach alles zurücklesen und mit dem Original vergleichen (byte-genauer JSON-Text je Zeile); bei Abweichung ROLLBACK.
4. Erst danach wird `shifts.json` mit leerer `history` und Marker atomar geschrieben.
   Dann lädt der Store über den echten Lesepfad und vergleicht mit dem Original. Bei Abweichung wird das Original zurückgeschrieben.
5. Fehlerfall: `shifts.json` bleibt byte-identisch bzw. wird zurückgeschrieben, und der Eintrag in `schema_migrations` wird entfernt. Pulse arbeitet mit JSON weiter.

**Rückweg** (`rollback`): Die Historie wird aus pulse.db wieder vollständig in `shifts.json` geschrieben (altes Format, ohne Marker), vorher gesichert (`shifts_pre_json_*.json` und eine pulse.db-Kopie) und nach dem Schreiben geprüft.
Die Zeilen bleiben in pulse.db (es werden keine Daten gelöscht), sind aber über die neue Generations-ID ungültig.
Eine neue Migration ersetzt sie vollständig, ohne Duplikate.

**Steuerung – nur ausdrücklich:** `PULSE_SHIFT_HISTORY_BACKEND` wird nur beim Start (`init_runtime`) ausgewertet, bevor Bot und Dashboard schreiben, sodass während der Umstellung keine Schicht verloren gehen kann:

| Wert | Wirkung |
|---|---|
| leer (Standard) | nichts ändert sich |
| `sqlite` | Migration (wiederholbar, danach `already`) |
| `json` | Rückweg |

Ein Fehler in der Umstellung bricht den Start nie ab (getestet). Lesend prüfen: `python scripts/shift_history.py status`.

## 4. Vorgeschlagener Produktionsablauf (erst nach deiner Freigabe)

1. Volles Backup (Dashboard oder `deploy/`-Routine) und die Datensicherung laut CLAUDE.md.
2. Diesen Branch ganz normal deployen. Ohne die Variable ändert sich nichts.
3. In der `.env` auf dem VPS `PULSE_SHIFT_HISTORY_BACKEND=sqlite` setzen und den Container neu starten.
4. Im Log nach `✅ Schichthistorie: N Einträge auf sqlite umgestellt` suchen, dann `docker compose exec pulse python scripts/shift_history.py status` ausführen. Erwartet: `consistent: true`, `db_rows == marker_rows`.
5. Rückweg: `PULSE_SHIFT_HISTORY_BACKEND=json` setzen und neu starten.
   **Wichtig:** Vor einem Rückgriff auf eine *ältere Pulse-Version* muss der Rückweg mit dieser Version laufen, denn eine alte Version sieht im SQLite-Modus eine leere Historie.

## 5. Geänderte Dateien

| Datei | Änderung |
|---|---|
| `pulse_shift_history.py` | neu: Store, Migration, Rückweg, Status |
| `webserver.py` | `load_json`/`save_json` leiten `SHIFTS_FILE` im SQLite-Modus um; bisheriger Schreibkörper heißt `_write_json_file` (unverändert); `init_runtime` ruft `apply_configured_backend` |
| `pulse_backup.py` | `pulse.db` und `shifts.json` werden gemeinsam unter dem JSON-Lock gesichert |
| `scripts/shift_history.py` | neu: nur lesende Statusprüfung |
| `tests/test_shift_history_sqlite.py` | neu: 34 Tests |
| `tests/shift_history_sqlite_mode.py` | neu: führt bestehende Testmodule im SQLite-Modus aus |
| `tools/shift_history_bench.py` | neu: Messung Laden/Schreiben/Statistik/Speicher |
| `tools/perf_baseline.py` | Option `--shift-backend sqlite` |
| `.env.example` | `PULSE_SHIFT_HISTORY_BACKEND` dokumentiert (auskommentiert) |
| `docs/VERBESSERUNGSPLAN.md` | D-07/A-05: Teilnachweis eingetragen, **nicht** abgehakt |

Unverändert: `logs.json`, `applications.json`, alle Routen, Slash-Commands, `custom_id`s, Discord-IDs.

## 6. Tests (echte Ergebnisse, Windows, Python 3.14.6)

`tests/test_shift_history_sqlite.py`, nur Temp-Ordner und synthetische Daten:

* leere Historie
* fehlende Datei
* 20.000 Einträge: gleiche Struktur, Reihenfolge, Teamstunden pro Mitglied und Wochenwerte
* Altformate bei Zeitstempel und Datum (`2026-1-5`, naive Zeitstempel, `20.10.2026`, Zahl als Zeitstempel, Mikrosekunden)
* fehlende optionale Felder, leeres Objekt, Nicht-Dict-Eintrag, Feldreihenfolge
* Duplikate und ähnliche Einträge
* Abbruch mitten im Import: JSON byte-identisch, DDL zurückgerollt, Wiederholung erfolgreich
* Fehler beim Umschalten der Datei und bei der Prüfung
* ungültige Struktur
* wiederholte Migration
* Rückweg inklusive Schreiben im SQLite-Modus, danach erneute Migration ohne Duplikate
* Umschalten per Variable in beide Richtungen sowie über `init_runtime`
* Start bricht bei Fehlern nicht ab
* Dienstaktionen und Status
* nur geänderte Zeilen werden geschrieben
* `commit_shift_event`
* Auto-Abschluss-Ansicht schreibt nichts
* Speichern ohne `history`
* fehlende oder teilweise fehlende Zeilen blockieren das Schreiben
* veralteter Marker nach dem Rückweg
* Rollback bei Schreib- und COMMIT-Fehlern
* Datenschutz-Export
* Status-Skript ist nur lesend
* Backup/Restore im SQLite-Modus
* altes JSON-Backup wird in den JSON-Modus zurückgespielt
* Backup unter Lock
* **Dashboard-Ausgaben vor/nach der Migration byte-identisch:** `/stats`, `/dashboard`, `/ultimate/reports.csv`, `/ultimate/export.json` (ohne den Echtzeit-Schlüssel `generated_at`), `/ultimate/api/team`, `/export/team.csv`
* `test_dienst`, `test_shift_confirmation` und `test_stats_page` laufen zusätzlich vollständig im SQLite-Modus

Zusätzlich lief einmalig die Gegenprobe mit `tests/shift_history_sqlite_mode.py`: 11 Schicht-Testmodule im SQLite-Modus (Dienst, Bestätigung, Statistik, parallele Schreibvorgänge, Konsistenz, Phase 7, Presence, Datenschutz, beide Performance-Suiten, Discord-Log-Regressionen), Ergebnis `Ran 93 tests – OK`.

**Voller CI-Satz:** siehe Abschnitt 9 (Ergebnis nach den Review-Fixes).

## 7. Performance-Vergleich (gleicher Aufbau, gemessen, nicht geschätzt)

`tools/shift_history_bench.py`, 20.000 Einträge, 200 Mitglieder, Median aus 7 Läufen. „vorher“ = unveränderter `main` (Worktree).

| Messung | vorher | nachher JSON | nachher SQLite |
|---|---|---|---|
| Historie laden (`load_shifts_raw`) | 25,9 ms | 26,6 ms | 29,8–31,4 ms |
| `load_shifts` | 26,4 ms | 25,9 ms | 31,2–31,4 ms |
| Wochenstatistik für 200 Mitglieder (inkl. Laden) | 58,7 ms | 40,6 ms | 49,7–50,2 ms |
| Schicht beenden (Laden + Schreiben) | 185,2 ms | 181,7 ms | 115–119 ms |
| Spitzen-Python-Speicher beim Laden | 16,6 MiB | 16,6 MiB | 14,7 MiB |
| JSON-Parses / SQLite-Verbindungen je Laden | 2 / 0 | 2 / 0 | 3 / 1 |
| je Schichtende | 4 / 3 | 4 / 3 | 8 / 7 |
| `shifts.json` | 5,47 MB | 5,47 MB | 0,0 MB (Marker + aktive Schichten) |
| `pulse.db` | 0,65 MB | 0,65 MB | 5,33 MB |
| Ergebnis-Prüfsumme der Historie, Teamstunden, Wochensumme | `59176033…`, 149.424.107 s, 3.921.144 s | identisch | identisch |

Dashboard (`tools/perf_baseline.py`, 20.000 Einträge, 400 Mitglieder, Median aus 3, 135 GET-Seiten, alle Status gleich):

| Seite | vorher ms | nachher JSON | nachher SQLite | geparste JSON-MB (vorher → SQLite) | SQLite-Verbindungen |
|---|---|---|---|---|---|
| `/cockpit` | 320.5 | 316.9 | 388.8 | 5.58 → 3.84 | 4 → 5 |
| `/team` | 291.2 | 286.9 | 302.0 | 5.58 → 3.84 | 8 → 9 |
| `/stats` | 115.9 | 106.0 | 122.0 | 12.36 → 8.89 | 5 → 7 |
| `/dashboard` | 92.3 | 92.9 | 108.3 | 12.36 → 8.89 | 7 → 9 |
| `/me` | 67.6 | 66.9 | 73.8 | 5.58 → 3.84 | 9 → 10 |
| `/ultimate/reports` | 102.5 | 106.9 | 77.3 | 5.3 → 3.56 | 7 → 8 |
| `/ultimate/analytics` | 73.1 | 71.1 | 84.2 | 5.58 → 3.85 | 7 → 8 |
| `/export/team.csv` | 57.2 | 43.8 | 50.4 | 5.29 → 3.56 | 0 → 1 |
| Summe aller 135 Seiten | 3294 | 3319 | 3596 | | |
| RSS am Ende | 144,6 MB | 142,7 MB | 146,2 MB | | |

**Ehrliche Einordnung:**
* Schreiben wird deutlich schneller (−36 % je Schichtende), weil nicht mehr 5,5 MB neu geschrieben und mit fsync gesichert werden.
* Lesen wird leicht langsamer (+4–5 ms je Laden der Historie, Dashboard-Summe etwa +9 %; die Einzelwerte streuen bei 3 Läufen stark).
* Der Speicher bleibt praktisch gleich.
* Eine Leseverbesserung wird **nicht** behauptet. Sie wäre der nächste Schritt (Abfragen über den `mod_id`/`shift_date`-Index statt der ganzen Liste) und würde Aufrufer ändern; das war in dieser Phase bewusst ausgeschlossen.

## 8. Datenintegrität

* Gleichheit wurde dreifach nachgewiesen: Struktur (`==`), JSON-Text je Zeile beim Import und Prüfsumme der ganzen Historie im Benchmark.
* Auch die abgeleiteten Werte stimmen überein: Teamstunden, Wochenstatistik, Dashboard-Ausgaben.
* Die alte `shifts.json` wird nie gelöscht, nur nach erfolgreicher Prüfung durch die Marker-Version ersetzt. Die Vollkopie liegt geprüft in `backups/`; davon werden die 5 neuesten Kopien je Richtung behalten.
* Alte Backups bleiben wiederherstellbar (getestet), neue Backups enthalten ein zusammengehöriges Paar aus `pulse.db` und `shifts.json`.

## 9. Qualitätssicherung

| Prüfung | Ergebnis |
|---|---|
| Ruff (`python -m ruff check .`) | All checks passed |
| compileall | OK |
| `tests/feature_smoke.py`, `tests/smoke_test.py` | beide OK |
| unittest discovery | `Ran 1345 tests – OK (skipped=18)` (vor den Review-Fixes: 1340, OK) |
| Playwright-E2E (`tests.test_e2e_dashboard`) | `Ran 16 tests – OK` (betrifft das Dashboard nur indirekt; Standardmodus JSON) |
| Docker-Compose-Integration | **lokal nicht ausführbar** (kein Docker in dieser Umgebung); läuft im CI-Job „container“ des PR |
| Unabhängiges Review (separater Agent, nur lesend) | 7 Befunde; umgesetzt: Start bricht nie ab (hoch), Backup unter Lock (mittel), Teilverlust blockiert (mittel), Generations-ID gegen veraltete Zeilen (niedrig), Stat vor dem Lesen beim Mode-Cache (niedrig), kein zweites Parsen im JSON-Modus (niedrig), fehlender COMMIT-Fehlertest ergänzt |

## 10. Verbleibende Risiken

* **Absturzfenster beim Speichern** (Review-Befund 4): `shifts.json` wird vor dem COMMIT ersetzt.
  Stirbt der Prozess genau zwischen fsync der kleinen Datei und COMMIT, fehlt der letzte Historieneintrag, und das Schreiben bleibt blockiert, bis jemand eingreift (Restore oder Rückweg). Nichts geht dabei still verloren.
  Das Fenster ist sehr klein; im JSON-Modus gab es ein vergleichbares Fenster nicht.
* **Lesen etwas langsamer** (siehe 7). Bei 2 GiB RAM unkritisch.
* **RAM:** Beim Speichern werden alte und neue Zeilentexte kurz parallel gehalten, rund 2× die Historie in Textform (bei 20.000 Einträgen etwa 8 MB).
* **Ältere Pulse-Version nach der Migration:** Diese sieht eine leere Historie. Deshalb vor einem Code-Rollback zuerst den Rückweg ausführen (Abschnitt 4).
* **Restore nur von `pulse.db`** (z. B. Legacy-Restore) bei markierter `shifts.json`: Pulse blockiert das Schreiben der Schichtdaten, statt falsch weiterzuschreiben. Das muss dann bewusst repariert werden.
* D-07 und A-05 sind **nicht** abgehakt: `logs.json`, `applications.json` und die Produktionsumstellung sind offen.
