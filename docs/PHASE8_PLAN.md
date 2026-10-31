# Phase 8 – Plan und Bericht (Pulse 7.17.0)

Stand: 31.10.2026 · Branch `claude/zen-hopper-oe18q5` · Basis `52aa1f0` (main, PR #87)

Grundsatz dieser Phase: **nur nachgewiesene Probleme beheben.** Jeder Befund unten hat einen reproduzierbaren
Test, der vor dem Fix fehlschlug. Ideen ohne Nachweis stehen getrennt unter „Offen“.

## 1. Bestandsaufnahme (tatsächlicher Zustand, nicht aus älteren Berichten übernommen)

| Bereich | Zustand (geprüft) | Verbesserungspotenzial |
|---|---|---|
| Dashboard | FastAPI, 338 Routen (inkl. pulse_pro/ultimate/next/suite/phase7), Admin-Bereich, Server Control Center, Mobile-Layouts mit E2E-Prüfung | Probelauf-Seite stürzte bei beschädigten Backups ab (500); Restore-Dialog wirkungslos (JS-Fehler) → **behoben** |
| Discord-Bot | 30 Cogs, 21 `@tasks.loop`, alle über `pulse_jobs` erfasst (Test erzwingt `@tracked`) | Keine neuen Befunde in dieser Phase |
| Sicherheit | Zentrale `auth()`-Prüfung, Deny-by-default-Rechtemodell, CSRF/Same-Origin, Sweep „ohne Login“ für alle Routen | Es fehlte ein Sweep **mit** Login ohne Rechte → **ergänzt**, keine Eskalation gefunden |
| Datenintegrität | Atomare JSON-Writes mit `_io_lock`, SQLite-WAL, Komplett-Backup mit Manifest/SHA-256, Restore mit Sicherheitskopie und Rollback, wöchentlicher Probe-Restore | Abbruch des ganzen Backups durch eine einzelne verschwundene Upload-Datei, verwaiste `*.zip.tmp` → **behoben** |
| Performance | RAM-Budget 768 MiB + 256 MiB Swap (`compose.yaml`), Schwellen 500/620/700 MB, Event-Loop-Lag-/Stall-Watchdog | Keine messbare Optimierung in dieser Phase (keine Messung ohne Last-VPS) |
| Automatisierung | Workflow-/Automation-Center, Jobs mit Pausen-/Wartungsmodus | Keine neuen Automationen (siehe Offen) |
| Benutzerfreundlichkeit | Toasts, Leer-/Lade-/Fehlerzustände, mobile Navigation durch E2E abgedeckt | Restore-Bestätigungsdialog erschien nie → **behoben** |
| Tests | 1099 Unittests + 12 Playwright-E2E (Basis) | +6 Unittests, +3 E2E; Testisolation verbessert |
| Deployment | Docker Compose, Deploy-Gate in CI, Rollback-Skripte | CI prüft jetzt, dass Tests keine Laufzeit-JSON im Checkout anlegen |

## 2. Bestätigte Probleme

| ID | Schwere | Problem | Nachweis |
|---|---|---|---|
| P8-01 | Hoch (Betrieb) | Eine Upload-/Transkriptdatei, die zwischen Auflisten und Kopieren gelöscht wird (Retention, Ticket-Bereinigung), bricht das **gesamte** Komplett-Backup mit `FileNotFoundError` ab. | `tests/test_full_backup.py::test_file_vanishing_during_backup_is_reported_not_fatal` |
| P8-02 | Mittel (Speicher) | Ein abgebrochenes Backup hinterlässt `pulse_full_*.zip.tmp`. `list_backups()` ignoriert diese Dateien, sie werden nie bereinigt und füllen bei wiederholten Fehlern (z. B. Disk voll) die 50-GB-SSD. | `test_failed_backup_leaves_no_partial_file` |
| P8-03 | Mittel (Recovery) | Der Restore-Probelauf eines beschädigten Backups (DB mit passender Prüfsumme oder fehlender Archiv-Eintrag) endete mit HTTP 500 (`sqlite3.DatabaseError`/`KeyError`) statt die Probleme anzuzeigen und die Wiederherstellung zu sperren. | `test_preview_of_damaged_backup_reports_problems_instead_of_crashing`, `test_preview_route_locks_restore_for_damaged_backup` |
| P8-04 | Mittel (Schutz) | Restore-Formular: `onsubmit="return confirm(…)"` mit einem Feld `name="confirm"`. Im Inline-Handler verdeckt das Feld die globale Funktion → `TypeError: confirm is not a function`, der Sicherheitsdialog erschien **nie**, das Formular wurde direkt abgeschickt (serverseitig blieb das Bestätigungswort Pflicht). | E2E `BackupFlowE2ETests.test_create_preview_and_guarded_restore` (schlägt mit altem Code fehl) |
| P8-05 | Hoch (Datensicherheit lokal) | `tests/test_security_hardening.py` (Beförderung, Rollen, Privatsphäre) schrieb `team_data.json` und `audit_logs.json` **ins aktuelle Verzeichnis**. In einem Checkout mit echten Teamdaten (`/pulse-verify` lokal) verändert der Testlauf echte Daten. | Lauf je Testmodul + `git status --ignored` |
| P8-06 | Hoch (Datensicherheit lokal) | `tools/preview_server.py` (E2E) und `tools/load_test.py` wechseln vor `import webserver` ins Temp-Verzeichnis – `webserver.py` wechselt beim Import aber per `os.chdir(BASE_DIR)` zurück ins Repository. Relative Laufzeitpfade (vor allem `backups/`) landeten im Checkout. | E2E-Backup-Test fand die ZIPs in `<repo>/backups/` |
| P8-07 | Info (Testlücke) | Kein Test rief schreibende Routen **mit gültigem Login, aber ohne Rechte** direkt auf. | Neuer Sweep; Mutationsprobe (Admin-Prüfung von `/backup/full/delete` entfernt) wird erkannt |

## 3. Umgesetzte Änderungen

| Datei | Änderung |
|---|---|
| `pulse_backup.py` | Quelle wird pro Datei geöffnet; nicht lesbare/verschwundene Dateien → `skipped` (`nicht lesbar`) + Warnung im Manifest. Schreibfehler am Archiv (Disk voll) brechen weiter ab, aber ohne `.tmp`-Rest. Probelauf meldet beschädigte/fehlende DB-Einträge als Problem (Restore gesperrt) und eine unlesbare **aktuelle** DB als Warnung. Format, Manifest-Schlüssel und Restore-Ablauf unverändert. |
| `webserver.py` | Probelauf-Route fängt zusätzlich `KeyError`/`sqlite3.Error` ab und zeigt Live-DB-Warnungen; Restore-Formular nutzt `window.confirm`. |
| `tools/preview_server.py`, `tools/load_test.py` | Nach `import webserver` zurück ins Temp-Verzeichnis, `BACKUP_DIR` absolut im Temp-Ordner. |
| `tests/fakes.py` | Additiv: `FakeRole.permissions/hoist`, `FakeMember.nick`, `FakeChannel.type/category/category_id/overwrites` (für den Discord-Struktur-Export im Backup). |
| `tests/test_security_hardening.py` | `TempDb` leitet alle JSON-Speicher (`DATA_FILE` … `MEETINGS_FILE`) ins Temp-Verzeichnis um. |
| `tests/test_full_backup.py` | 4 Regressionstests (P8-01…03). |
| `tests/test_permission_sweep.py` (neu) | Sweep über alle POST/PUT/PATCH/DELETE-Routen als Teammitglied mit nur `can_view_dashboard`; alle Feature-Flags an; nur eine explizite Liste persönlicher/teamweiter Aktionen darf gelingen, alle anderen müssen 401/403 liefern (Ausnahmeliste für Routen mit Eingabeprüfung vor dem Rechte-Check, die trotzdem nie gelingen dürfen). Keine 500er. |
| `tests/test_tool_isolation.py` (neu) | Sichert P8-06 ab. |
| `tests/test_e2e_dashboard.py` | Neue E2E: Backup erstellen → Probelauf → Dialog erscheint, Abbrechen sendet nichts, falsches Wort wird abgewiesen; Backup-Seiten mobil ohne Überlauf; Teammitglied kann Backup weder per POST erstellen noch wiederherstellen (403, keine Datei). |
| `.github/workflows/python-syntax.yml` | Schritt nach den Tests: keine neue/ignorierte `*.json` im Checkout. |

Keine Änderung an URLs, Slash-Commands, Custom IDs, DB-Feldern oder JSON-Schlüsseln. Keine Laufzeitdaten migriert.

## 4. Tests (tatsächlich ausgeführt, Python 3.13.18 im Container; CI nutzt 3.14)

- `ruff check .` – OK · `compileall` – OK · `tests/feature_smoke.py` – OK · `tests/smoke_test.py` – OK
- `unittest discover` – **1093 Tests OK** (4 übersprungen = die 4 E2E-Klassen, die separat liefen). Basis vorher: 1099 OK mit 12 einzeln übersprungenen E2E-Tests; 1099 − 12 + 6 neue = 1093.
- `PULSE_E2E=1 unittest tests.test_e2e_dashboard` – **15 Tests OK** (vorher 12). Chromium aus `/opt/pw-browsers` (Revision 1243 per Symlink im Scratch-Ordner als 1248 bereitgestellt, nichts im Repo geändert).
- Gegenproben: jeder neue Regressionstest schlägt mit dem alten Code fehl; Sweep erkennt eine entfernte Admin-Prüfung.
- CI-Leckprüfung lokal nachgestellt: keine `*.json` im Checkout nach dem vollen Lauf.

## 5. Sicherheit

- Rechte-Sweep: keine Route erlaubt einem Teammitglied ohne Rechte eine Verwaltungsaktion; gelingen dürfen nur eigene Inbox/Status/Sitzungen/Monats-DM, Übergaben, Ideen/Feedback, Ankündigung als gelesen, Copilot.
- Restore bleibt doppelt geschützt (Dialog + Bestätigungswort serverseitig), Admin-Pflicht unverändert.
- Verbleibend: siehe Offen.

## 6. Offen / bewusst nicht umgesetzt (mit Grund)

| Punkt | Grund |
|---|---|
| `import webserver` legt `pulse.db` und `.session_secret` im aktuellen Verzeichnis an (Import-Nebenwirkung, `os.chdir(BASE_DIR)`). | Betrifft Start-/Importverhalten des Produktionsprozesses (Regel: Start/Shutdown nur mit voller Prüfung und Freigabe). Datenverändernd ist nur `CREATE TABLE IF NOT EXISTS`. **Freigabe nötig.** |
| Performance-Optimierungen | Ohne Messungen auf dem VPS keine belastbaren Vorher/Nachher-Werte; Lasttest-Workflow existiert (manuell). |
| Neue Automationen (Eskalation, Onboarding …) | Vorhandenes Workflow-/Automation-Center deckt die Grundfälle ab; neue Automationen nur mit konkretem Bedarf und Freigabe (Regel 6). |
| Restore-Test-Alter im Health-Center | Backup-Alter (> 48 h) und fehlgeschlagener Restore-Test werden bereits im Server Control Center gemeldet; der Restore-Test läuft mit jedem Auto-Backup ≥ 7 Tage. Kein nachgewiesener Mangel. |
| GitHub-Repo-Einstellungen (Branch-Schutz, Pflicht-Checks inkl. neuem Leck-Schritt) | Nicht über Code verwaltbar → manuell in GitHub prüfen. |
| Python-Version | Container hier 3.13.18, CI/Image 3.14 – lokale Ergebnisse sind gegen 3.13 gelaufen. |
