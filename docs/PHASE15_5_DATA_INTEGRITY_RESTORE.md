# Phase 15.5 – Datenintegrität und Wiederherstellung

**Status:** Abgeschlossen am 04.11.2026 · manueller 100K-Backup-/Restore-Rehearsal bestanden · Prozessabbruch-Regressionstest in CI bestanden  
**Sicherheitsgrenze:** ausschließlich synthetische Daten in temporären Verzeichnissen · keine VPS-Daten · keine Produktivmigration

## Ausgangslage im Bestand

Der Backup-Kern in `pulse_backup.py` stellt bereits folgende Schutzmechanismen bereit:

- SQLite-Snapshots über die SQLite-Backup-API, auch wenn WAL aktiv ist.
- Manifest mit Dateigrößen und SHA-256-Prüfsummen.
- SQLite-Integritätsprüfung mit `PRAGMA integrity_check`.
- Zusätzliche Melonly-Metadaten für `pulse.db`: Schema, User-Version, Backend-Marker, Generation, Revision, Import-Metadaten und Zeilenanzahl.
- Prüfung, dass die im Manifest deklarierten SQLite-Metadaten dem archivierten Datenbank-Snapshot entsprechen.
- Restore-Vorschau und isolierter Test-Restore ohne Veränderung der Live-Dateien.
- Vor jedem echten Restore wird eine `pre_restore`-Sicherheitskopie erstellt. Fehler innerhalb des Restore-Prozesses lösen den automatischen Rückweg aus dieser Kopie aus.

Ein harter Prozessabbruch kann den Python-Rollback naturgemäß nicht mehr ausführen. Die vor Beginn angelegte `pre_restore`-Sicherheitskopie bleibt bestehen und muss anschließend geprüft und manuell wiederhergestellt werden. Genau diesen Fall deckt der neue Abbruchtest ab.

## Neue Prüfungen

### 1. 100K-Vollbackup und vollständiger Restore

Der manuelle Workflow **Melonly Backup Restore Rehearsal** führt `tools/melonly_restore_rehearsal.py` in einem isolierten Docker-Container mit 2 vCPU und 2 GB RAM aus.

Der Standardlauf verwendet 100.000 synthetische Datensätze und prüft:

1. JSON-Quelle und SQLite-Testbestand werden im temporären Verzeichnis erzeugt.
2. Der Vollbackup-Manifest-Eintrag enthält `pulse.db`, `logs.json` und die Melonly-Metadaten.
3. Archivprüfung, SHA-256, Restore-Vorschau und isolierter Test-Restore sind fehlerfrei.
4. Der Testbestand wird nach dem Backup absichtlich verändert.
5. Der vollständige Restore stellt Zeilenanzahl, Generation, Revision, Import-Metadaten, SQLite-Integrität und die bytegleiche JSON-Migrationssicherung wieder her.
6. Der explizite SQLite→JSON-Rückexport schreibt die JSON-Datei atomar, prüft den SHA-256-Read-back und setzt den Backend-Marker nur mit passendem Generation-/Revision-Guard auf JSON zurück.

Die Ausgabe dokumentiert Laufzeiten, Archivgröße und – sofern vom Container verfügbar – RSS-/cgroup-Speicherwerte.

### 2. Simulierter harter Prozessabbruch

Der neue Unit-Test `test_abrupt_process_abort_can_be_recovered_from_pre_restore_backup` startet einen Kindprozess. Dieser beginnt einen Restore, spielt absichtlich nur die SQLite-Datenbank ein und beendet sich unmittelbar mit `os._exit`, bevor JSON-Dateien ersetzt werden.

Der Elternprozess prüft anschließend:

- Der Restore wurde tatsächlich mitten in der Anwendung abgebrochen.
- Die automatisch vorher angelegte `pre_restore`-Sicherheitskopie liegt vor und besteht die vollständige Archivprüfung.
- Der Zustand ist absichtlich teilweise wiederhergestellt.
- Die Sicherheitskopie stellt anschließend den vollständigen Zustand vor dem unterbrochenen Restore wieder her.

Damit wird der manuelle Recovery-Weg nach einem Prozessabbruch getestet. Eine automatische Startzeit-Recovery wird nicht behauptet oder neu aktiviert.

## Ausführung nach Merge

Der Code aus PR #131 ist bereits in `main`. Für die tatsächliche Abnahme ist daher der separate manuelle Workflow zu verwenden:

GitHub Actions → **Melonly Backup Restore Rehearsal** → **Run workflow** auf `main`.

- Eingabe `entries`: Standardwert `100000` beibehalten.
- Der Workflow läuft ausschließlich mit synthetischen Daten in einem isolierten Docker-Container.
- Das Ergebnis muss im Job-Summary und als Actions-Artefakt vorhanden sein.
- Job-Status `success` allein reicht nicht: Summary und JSON-Ausgabe müssen die Restore-, Integritäts- und Paritätsprüfungen tatsächlich belegen.
- Der frühere Lauf [#41436692148](https://github.com/NicoPinguinDEV/Pulse/actions/runs/41436692148) ist **kein** Restore-Nachweis: Er führte nur den JSON-/SQLite-Benchmark aus; der Restore-Job wurde übersprungen.
- Der tatsächliche Nachweis ist der manuelle Lauf [#41817023415](https://github.com/NicoPinguinDEV/Pulse/actions/runs/41817023415) vom 04.11.2026 auf `main` @ `e7311e7b9bb3fb90706629eaf0fabe2086e4782d`.

### Ergebnis des 100K-Laufs vom 04.11.2026

Der Workflow **Melonly Backup Restore Rehearsal** lief mit dem Standardwert `entries=100000`. Der einzige Job **Backup and restore (2 vCPU / 2 GB)** sowie alle sieben Arbeitsschritte wurden erfolgreich abgeschlossen. Das Actions-Artefakt `melonly-restore-rehearsal-41817023415` wurde erstellt und enthält `melonly_restore_rehearsal.json`.

Im JSON-Ergebnis nachgewiesen:

- `synthetic_data_only=true`, `production_data_accessed=false`, `production_backend_changed=false`.
- `entries=100000`, `parity=passed`, `backup_verified=true`.
- Isolierter Test-Restore und vollständiger Restore: `passed`.
- SQLite `PRAGMA integrity_check`: `ok`; Zeilenanzahl nach Restore: `100000`.
- Generation und Revision wurden wiederhergestellt. Der Rehearsal-Code prüft zusätzlich `active_backend`, `import_count` und `import_source_sha256` gegen die Ausgangsmetadaten.
- SQLite→JSON-Rückexport: `count=100000`, `sha256_matches_source=true`, Backend nach kontrolliertem Rollback: `json`.
- Erzeugtes Backup: `9.546.738` Bytes; das Manifest enthielt in diesem synthetischen Fixture zwei Dateien (`pulse.db` und `logs.json`).
- Vollständiger Restore: `2.825,786 ms`; JSON-Rückexport: `1.955,389 ms`; Backup-Erstellung: `1.205,284 ms`; Archivprüfung: `495,258 ms`.
- Container-Spitzenverbrauch laut cgroup: `547,7 MB` bei einem Limit von `2.048 MB`; aktueller Verbrauch am Messpunkt `158,5 MB`, Prozess-HWM `434,2 MB`.

Die Laufzeiten und Speicherwerte stammen vom GitHub-Runner unter den Workflow-Limits, nicht vom Produktions-VPS. Die Rehearsal-Messung ersetzt keinen Restore-Test echter Produktionsdaten und ändert keine Backend-Konfiguration.

### Prozessabbruch-Regressionstest

Der Test `test_abrupt_process_abort_can_be_recovered_from_pre_restore_backup` liegt in `tests/test_full_backup.py` (Zeilen 122–196 auf dem geprüften Stand). Er startet einen Kindprozess, der nach dem Einspielen der SQLite-Datei mit Exit-Code 73 hart beendet wird. Der Elternprozess prüft die automatisch erzeugte `pre_restore`-Sicherung, den absichtlich gemischten Zwischenzustand und die Wiederherstellung des Zustands vor dem Restore.

PR #131 wurde mit Commit `c4b13a9f66b690666abe184afb9c9ab5a3a644f4` integriert. Der CI-Lauf [#41437223612](https://github.com/NicoPinguinDEV/Pulse/actions/runs/41437223612) führte `python -m unittest discover -s tests -p 'test_*.py'` aus: **1.472 Tests, OK, 19 übersprungen**. `tests/test_full_backup.py` wird von diesem Discovery-Muster erfasst; der genannte Test enthält keinen Skip-Pfad. Die CI gibt den Testnamen nicht einzeln aus, daher wird kein separater per-test Actions-Check behauptet.

**Aussagegrenze:** Der 100K-Rehearsal hatte nur `pulse.db` und `logs.json` als Dateien im Manifest. Obwohl `pulse_backup.SQLITE_FILES` auch `pulse_suite.db` aufführt, ist damit kein Interviewdaten-Restore dieser Datenbank in diesem Lauf nachgewiesen. Der anwendungsübergreifende Restore-Nachweis gehört in Phase 15.6e.

Der normale CI-Lauf führt zusätzlich den Prozessabbruch-Regressionstest aus. Ein bestandener Unit-Test ersetzt den separaten 100K-Rehearsal nicht.

## Abnahmekriterien

- [x] Der Prozessabbruch-Test besteht im CI-Discovery-Lauf und belegt Recovery aus der unveränderten Safety-Kopie.
- [x] 100K-Workflow ist erfolgreich und liefert echte Ergebnisdaten, nicht nur einen grünen Jobstatus.
- [x] Archivprüfung, isolierter Test-Restore und vollständiger Restore bestehen.
- [x] Zeilenanzahl, SHA-256, Melonly-Generation/Revision und SQLite-Integrität stimmen.
- [x] Der SQLite→JSON-Rückexport ist bytegleich und der Backend-Guard lehnt veraltete Generationen/Revisionen ab.
- [x] Keine Produktivdatei, kein VPS und keine echte Backend-Konfiguration wurde verändert.

## Grenzen und Betriebshinweise

- Der Rehearsal ist ein synthetischer 100K-Test auf einem GitHub-Runner, kein Restore-Test des produktiven VPS.
- Der manuelle Recovery-Weg nach hartem Prozessabbruch benötigt einen Administrator. Vor dem Restore der Safety-Kopie muss deren Manifestprüfung erfolgreich sein.
- Keine Änderung schaltet Melonly im Produktivbetrieb auf SQLite um. JSON bleibt das aktive Produktiv-Backend.
- Eine produktive Wiederherstellung oder Backend-Umschaltung benötigt eine separate ausdrückliche Freigabe und ein aktuelles geprüftes Backup.


## Abnahmeentscheidung

Phase 15.5 ist für den definierten Melonly-Backup-/Restore-Umfang abgeschlossen. Nicht Teil dieser Abnahme ist eine produktive Wiederherstellung oder der fachübergreifende Bewerbungsdaten-Restore über alle Speicher. Die dokumentierten Grenzen bleiben für Phase 15.6 verbindlich.
