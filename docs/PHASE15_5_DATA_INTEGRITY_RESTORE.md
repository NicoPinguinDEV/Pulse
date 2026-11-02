# Phase 15.5 – Datenintegrität und Wiederherstellung

**Status:** Rehearsal-Code und Abbruch-Regressionstest im Review · 100K-GitHub-Lauf noch auszuführen  
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

## Ausführung

Damit der Test bereits vor dem Merge des Draft-PRs ausführbar ist, wird der bereits in `main` vorhandene manuelle Workflow erweitert:

GitHub Actions → **Melonly Storage Benchmark** → **Run workflow**.

- Branch: `phase/15-5-backup-restore-rehearsal`
- Option `restore_rehearsal`: aktivieren
- Die Eintragszahl ist fest auf `100000` begrenzt.
- Der normale JSON-/SQLite-Benchmark wird bei aktiviertem Restore-Modus übersprungen.
- Das JSON-Ergebnis erscheint im Job-Summary und als Actions-Artefakt.
- Nach Merge steht zusätzlich der separate Workflow **Melonly Backup Restore Rehearsal** zur Verfügung.

Der normale CI-Lauf führt zusätzlich den Prozessabbruch-Regressionstest aus.

## Abnahmekriterien

- [ ] Der Prozessabbruch-Test besteht in CI und belegt Recovery aus der unveränderten Safety-Kopie.
- [ ] 100K-Workflow ist erfolgreich und liefert echte Ergebnisdaten, nicht nur einen grünen Jobstatus.
- [ ] Archivprüfung, isolierter Test-Restore und vollständiger Restore bestehen.
- [ ] Zeilenanzahl, SHA-256, Melonly-Generation/Revision und SQLite-Integrität stimmen.
- [ ] Der SQLite→JSON-Rückexport ist bytegleich und der Backend-Guard lehnt veraltete Generationen/Revisionen ab.
- [ ] Keine Produktivdatei, kein VPS und keine echte Backend-Konfiguration wurde verändert.

## Grenzen und Betriebshinweise

- Der Rehearsal ist ein synthetischer 100K-Test auf einem GitHub-Runner, kein Restore-Test des produktiven VPS.
- Der manuelle Recovery-Weg nach hartem Prozessabbruch benötigt einen Administrator. Vor dem Restore der Safety-Kopie muss deren Manifestprüfung erfolgreich sein.
- Keine Änderung schaltet Melonly im Produktivbetrieb auf SQLite um. JSON bleibt das aktive Produktiv-Backend.
- Eine produktive Wiederherstellung oder Backend-Umschaltung benötigt eine separate ausdrückliche Freigabe und ein aktuelles geprüftes Backup.
