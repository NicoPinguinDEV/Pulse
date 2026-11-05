# Phase 15.6d – Persistente Bewerbungs-Snapshots, Import-Dry-Run und Recovery

**Status:** Implementierung auf Feature-Branch; Review und GitHub-CI stehen noch aus  
**Basis:** `main` @ `a4abebf6da1b9736d351bf42baeb27401a13614e`  
**Branch:** `phase/15-6d-persistent-snapshots-restore`  
**Sicherheitsgrenze:** Ausschließlich synthetische Testdaten und temporäre Dateien/Datenbanken. Keine Produktivdaten, keine Migration, keine Backend-Umschaltung, kein VPS und kein Discord.

## 1. Architektur

`pulse_application_import.py` bleibt der strikte Byte-/Integritätsvertrag: strict UTF-8, Top-Level-Objekt, Ablehnung doppelter Schlüssel und nicht standardisierter Zahlenwerte, SHA-256, Schema-Version, Generation, Revision, Record-Anzahl und exakter Original-Byte-Stream.

`pulse_applications_repository.py` bleibt der JSON-Adapter für das Mapping `application_id → record`. Bestehende Leser und Schreiber werden nicht global auf ein anderes Backend umgestellt.

Die neue Schicht `pulse_application_snapshot_store.py` ergänzt eine explizite, aufrufgesteuerte persistente Snapshot- und Wiederherstellungsverwaltung. Beim Import des Moduls werden keine Dateien oder Datenbanken geöffnet oder angelegt. Die Tabellen werden erst beim expliziten Aufruf eines Manager-Verfahrens in `pulse.db` angelegt. Es entsteht keine weitere Runtime-Datei.

## 2. Datenbankschema und Versionssemantik

Die Verwaltung legt drei Tabellen an:

- `application_snapshot_state`: Singleton mit `generation`, `revision`, optionaler `active_operation_id` und Aktualisierungszeit.
- `application_snapshots`: Snapshot-ID, Zweck, Schema-Version, Generation, Revision, eindeutige Snapshot-Sequenz, SHA-256, Record-Anzahl, Payload-Größe, unveränderte Payload-Bytes und Erstellzeit.
- `application_snapshot_journal`: Operationstyp, Zustand, Vorher-/Ziel-Snapshot-IDs und Prüfsummen, erwartete und resultierende Zähler sowie Zeitstempel.

Snapshot-IDs sind Primärschlüssel. Snapshot-Datensätze werden mit `INSERT` gespeichert und nicht überschrieben. Exporte prüfen Schema-Version, SHA-256, Record-Anzahl und Payload-Größe erneut.

Die Zähler sind bewusst eng definiert:
- **Generation** bezeichnet die Generation des installierten Bewerbungs-JSON-Datenstands. Sie steigt nach einem erfolgreich abgeschlossenen kontrollierten Import oder Restore um eins.
- **Revision** zählt dieselben erfolgreich abgeschlossenen kontrollierten Austauschoperationen monoton und dauerhaft.
- Normale Bewerbungsänderungen durch bestehende JSON-Schreibpfade erhöhen diese Zähler nicht. Sie werden durch den aktuellen SHA-256-Vergleich erkannt.
- Das bloße Speichern eines Snapshots erhöht Generation und Revision nicht. Eine separate, eindeutige `snapshot_sequence` wird bei jeder Snapshot-Anlage in derselben SQLite-Transaktion monoton erhöht. Damit können parallele Snapshot-Erstellungen keine Sequenznummer doppelt vergeben.

Damit wird keine Versionierung jedes einzelnen normalen Bewerbungs-Writes behauptet.

## 3. Import-Dry-Run

`preview_import(payload)` arbeitet ausschließlich lesend und vergleicht explizite Importbytes mit dem aktuellen JSON-Mapping. Das Ergebnis enthält `valid`, `source_sha256`, `current_sha256`, Generation/Revision, Schema-Version, Record-Anzahl, Payload-Größe sowie `new_count`, `changed_count`, `unchanged_count`, `removed_count`, `warnings`, `errors` und `conflicts`.

Jeder Datensatz muss ein JSON-Objekt sein. Unbekannte Felder und Statuswerte bleiben erhalten. Es findet keine Statusnormalisierung statt.

Die Vorschau markiert insbesondere als Konflikt:
- Entfernung bereits vorhandener Bewerbungen;
- Statusänderungen bei bestehenden Bewerbungen;
- einen bereits aktiven Recovery-Fall.

Ungültige JSON-Payloads oder strukturell ungültige Datensätze werden abgelehnt. Der Dry-Run verändert keine Bewerbungsdatei.

## 4. Apply, Restore und Rollback

`apply_import(payload, preview)` akzeptiert nur eine gültige und unveränderte Vorschau. Vor dem Schreiben werden aktuelle Dateiprüfsumme, Generation, Revision und Importbytes erneut geprüft. Veraltete Vorschauen werden abgewiesen.

Vor dem Dateiaustausch werden in einer SQLite-Transaktion ein Vorher-Snapshot, ein Ziel-Snapshot, ein Journal-Eintrag im Zustand `prepared` sowie die aktive Operationssperre dauerhaft gespeichert. Danach wird `applying` protokolliert.

Die JSON-Datei wird über eine temporäre Datei im selben Verzeichnis, Datei-`fsync`, atomaren Replace und POSIX-Dateirechte `0600` ersetzt. Nach dem Schreiben wird SHA-256 erneut geprüft. Erst nach erfolgreicher Prüfung werden Generation/Revision fortgeschrieben und das Journal auf `completed` gesetzt.

`restore_snapshot()` stellt exakte Snapshotbytes mit denselben Compare-and-Swap- und Journal-Regeln wieder her. `rollback_operation()` erzeugt einen neuen Restore auf Basis des Vorher-Snapshots einer abgeschlossenen Operation. Beide benötigen den erwarteten aktuellen SHA-256-, Generation- und Revision-Stand.

## 5. Recovery

Journalzustände:
- `prepared`: Vorher-/Ziel-Snapshot und Journal sind gespeichert; der Austausch wurde noch nicht als gestartet markiert.
- `applying`: Dateiaustausch kann begonnen haben.
- `completed`: Zieldatei geprüft und Versionszähler abgeschlossen.
- `rolled_back`: aktuelle Datei entspricht weiterhin exakt dem Vorher-SHA.
- `needs_recovery`: Prüfsumme oder Versionslage ist nicht eindeutig.

`recover_pending()` muss ausdrücklich aufgerufen werden; es gibt keine automatische Recovery beim Modulimport oder Bot-Start. Bei Vorher-SHA wird der Vorgang als `rolled_back` geschlossen. Bei Ziel-SHA kann der Abschluss idempotent nachgetragen werden. Bei jeder anderen Prüfsumme wird `needs_recovery` gesetzt. Die Datei wird in diesem Fall nicht überschrieben.

Fehler vor dem atomaren Replace lassen den Vorher-Stand unverändert. Ein Fehler nach dem Replace hält die Operation für eine explizite Recovery offen.

## 6. Backup-Abdeckung

Die neuen Tabellen liegen in `pulse.db`, das bereits über die SQLite-Backup-API im vollständigen Pulse-Backup enthalten ist. Ein neuer Regressionstest erzeugt einen synthetischen Snapshot, erstellt ein vollständiges Backup, entfernt den Snapshot aus der Testdatenbank und stellt das Backup wieder her. Danach muss der Snapshot wieder mit identischen Bytes exportierbar sein.

## 7. Tests und aktueller Verifikationsstatus

Neue Testdatei: `tests/test_application_snapshot_store.py`. Sie deckt Persistenz über neue Manager-Instanzen, doppelte IDs, Payload-Manipulation, Dry-Run-Zählungen und Konflikte, Nebenwirkungsfreiheit, Apply/Rollback, veraltete Vorschauen, atomare Schreibfehler, Recovery nach simuliertem Prozessabbruch, unbekannte Dateizustände, parallele Applies und vollständiges Backup/Restore ab.

`tests/test_import_side_effects.py` importiert zusätzlich das neue Modul und prüft weiterhin, dass der Import keine Runtime-Verzeichnisse anlegt.

**Die Tests sind mit dem Anlegen der Dateien noch nicht ausgeführt.** Erst erfolgreiche lokale Läufe bzw. grüne GitHub-Actions gelten als Verifikation. Review und CI sind offen.

## 8. Nicht-Ziele und Restrisiken

Nicht umgesetzt sind produktive API-/Dashboard-Routen, Backend-Auswahl, Migration von `applications.json`, Migration beim Start, Retention, Löschung, Anonymisierung oder Änderungen an Teamhistorie, Formularen, URLs, `custom_id`, Rechten, Interviews und Kalenderdaten. Es erfolgte kein VPS-Deployment und keine Discord-Aktion.

Der Snapshot-Manager ist ein technischer Baustein und noch nicht in einen produktiven Admin-Workflow integriert. Bestehende JSON-Schreibpfade werden nicht einzeln versioniert; ihre Änderungen erkennt der SHA-256-CAS-Vergleich. Bei nicht zuordenbaren Dateizuständen ist eine manuelle Prüfung erforderlich.

Phase 15.6d ist erst abgeschlossen, wenn Snapshot-Persistenz, Dry-Run, Apply, Restore, Recovery, Byte-Parität, Backup-Abdeckung und die relevanten Regressionstests nachweislich funktionieren. Ein gemergter PR ist keine Produktionsmigration.
