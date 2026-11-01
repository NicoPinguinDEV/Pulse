# Phase 15.2 – Melonly-Migration und Rückexport

**Status:** Implementierung auf separatem Branch · nicht aktiviert · nicht auf dem VPS ausgeführt

## Umfang

- **pulse_melonly_migration.py** kapselt den expliziten JSON→SQLite-Import und den geprüften SQLite→JSON-Rückexport.
- **scripts/migrate_melonly.py** stellt Dry-Run, Migration und Rückexport als manuelle Offline-Befehle bereit.
- Der Import wird über **pulse_migrations.run** mit dem Migrationsnamen **melonly_logs_sqlite_v1** ausgeführt.
- Die Datenbank bleibt **pulse.db**. Es wird keine zusätzliche produktive Datenbankdatei angelegt.
- Die Migration erhält die vollständige Payload und ursprüngliche Listenreihenfolge. Doppelte IDs werden nicht zusammengeführt.
- **active_backend** bleibt nach dem Import ausdrücklich **json**. Es gibt keine automatische Aktivierung.

## Sicherheitsablauf

1. Dry-Run prüft UTF-8, JSON-Syntax, Top-Level-Liste und Objektform jedes Eintrags.
2. Vor dem Schema-/Datenbankeingriff wird **logs.json** bytegenau gesichert und per SHA-256 geprüft.
3. **pulse_migrations.run** erzeugt vor der ersten Migration eine SQLite-Sicherheitskopie.
4. Schema, geordnete Einträge und Importmetadaten werden in einer Transaktion angelegt.
5. Nach dem Commit werden Anzahl, Sequenz, alle Indexfelder, dekodierte Payloads und Reihenfolge gegen das JSON-Original geprüft.
6. Die Quelldatei wird erneut gehasht. Eine Änderung während des Vorgangs führt zum Fehler.
7. Wiederholung ist nur dann erfolgreich, wenn Migrationsmarker, Quellhash, Anzahl und vollständiger Datenbestand übereinstimmen. Fremde oder abweichende SQLite-Daten werden nicht überschrieben.

Bei einem Fehler bleibt logs.json unverändert. Datenimport, Melonly-Schema und Migrationsmarker werden innerhalb derselben Transaktion zurückgerollt. Die bereits vor der Migration angelegte Tabelle schema_migrations kann bestehen bleiben; sie enthält dann keinen erfolgreichen Melonly-Marker.

## Manuelle Verwendung

Alle Schreibbefehle nur ausführen, wenn Bot und Dashboard gestoppt sind. Die Pfade müssen auf dasselbe Pulse-Datenverzeichnis zeigen.

Dry-Run (schreibt nichts):

~~~bash
python scripts/migrate_melonly.py \
  --json /opt/pulse/data/logs.json \
  --database /opt/pulse/data/pulse.db \
  --backup-dir /opt/pulse/data/backups \
  --dry-run
~~~

Import (erst nach Prüfung und bei gestopptem Pulse):

~~~bash
python scripts/migrate_melonly.py \
  --json /opt/pulse/data/logs.json \
  --database /opt/pulse/data/pulse.db \
  --backup-dir /opt/pulse/data/backups \
  --migrate --confirm-offline
~~~

Der Import stellt Daten in SQLite bereit, schaltet aber keine Route und kein Backend um. Der normale Betrieb liest weiterhin JSON.

SQLite→JSON-Rückexport ist nur zulässig, wenn die Metadaten SQLite bereits als aktives Backend ausweisen:

~~~bash
python scripts/migrate_melonly.py \
  --json /opt/pulse/data/logs.json \
  --database /opt/pulse/data/pulse.db \
  --backup-dir /opt/pulse/data/backups \
  --rollback-export --confirm-offline
~~~

Der Rückexport schreibt atomar, prüft den Read-back und sichert eine vorhandene JSON-Zieldatei vorher separat. Er ändert active_backend nicht. Das Umschalten der Backend-Metadaten und der Laufzeitadapter ist eine separate, noch nicht freigegebene Implementierungsstufe.

## Noch offen in Phase 15.2

- Adapter und kontrollierte Backend-Auswahl für alle aktiven Melonly-Leser und -Schreiber.
- Ergebnisgleiche Spielerhistorie, Vorschläge, globale Suche, CSV und Dashboard-Zähler im aktiven Adapter.
- Synchronisierung mit dem vollständigen Backup/Restore inklusive Generation und Revision.
- Verhaltenstests gegen die tatsächlich aktiven Routen und Berechtigungsprüfungen.
- Last-/Performance-Messungen mit 20.000 und 100.000 synthetischen Einträgen.

**Keine Produktivmigration, keine Backend-Aktivierung und kein VPS-Deploy sind Bestandteil dieser PR.**
