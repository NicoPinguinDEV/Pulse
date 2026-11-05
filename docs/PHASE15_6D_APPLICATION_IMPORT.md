# Phase 15.6d – Bewerbungsrepository und kontrollierter JSON-Import

**Status:** JSON-Repository-Adapter mit PR #145 gemergt · konfliktarme Bewerbungsentscheidungen in PR #146 auf Feature-Branch · Phase 15.6d noch nicht abgeschlossen
**Basis:** main @ d635b8038486ee719bad2a14a5c850da61dac8ae
**Branch:** phase/15-6d-application-conflict-safe-decisions
**Sicherheitsgrenze:** Testumfang: synthetische Fixtures und temporäre Dateien; keine Produktivdaten, Migration oder VPS-Aktion.

## 1. Ziel und bisheriger Vertrag

PR #144 hat mit pulse_application_import.py den nebenwirkungsfreien Import-Snapshot-Vertrag eingeführt. Er akzeptiert explizite JSON-Bytes, prüft strict UTF-8 und ein Top-Level-Objekt, lehnt doppelte Schlüssel sowie NaN/Infinity ab und speichert SHA-256, Schema-Version, Generation, Revision und Record-Anzahl. Der ursprüngliche Byte-Stream lässt sich nach Integritätsprüfung exakt exportieren.

## 2. JSON-Repository-Adapter

Das neue Modul pulse_applications_repository.py kapselt das bisherige applications.json hinter JsonApplicationRepository.

- load() und save() delegieren weiterhin an die bestehenden, sicherheitsbewährten JSON-Datei-Helfer aus webserver.py.
- webserver.load_json() und webserver.save_json() verwenden den Adapter ausschließlich dann, wenn der aufgelöste Pfad APPS_FILE entspricht. Alle anderen JSON-Dateien behalten ihren bisherigen Pfad.
- Die eigentliche Speicherung bleibt bei _write_json_file: temporäre Datei, fsync, atomarer Replace, POSIX-Dateirechte 0600, Schutz bei beschädigter Datei und Wartungsmodus.
- Der Adapter wird verzögert erstellt und berücksichtigt einen in Tests geänderten APPS_FILE-Pfad. Es gibt keine Datei- oder Datenbankanlage beim Import des Moduls.
- mutate() stellt eine synchrone Read-Modify-Write-Transaktion unter derselben RLock bereit. Die Callback-Funktion bearbeitet eine private Kopie; bei einer Exception wird nicht geschrieben. Innerhalb des Callbacks sind keine awaits oder externen I/O-Aufrufe erlaubt.
- PR #146 ergänzt update_entry(): Nur durch die konkrete Aktion geänderte Felder werden in den aktuellen Datensatz übernommen. Parallel geschriebene Reviews, Votes, Reminder-Felder und neue Verlaufsereignisse bleiben erhalten. Ein abweichender Status oder ein gelöschter Datensatz führt zu einem Konflikt statt zu einem stillen Überschreiben.
- Discord-gestützte Bewerbungsentscheidungen und Recruiting-Statusänderungen der Suite werden über einen gemeinsamen Prozess-Lock serialisiert. Die Sperre umfasst die Discord-awaits des Entscheidungsablaufs; Statusänderungen laden danach den aktuellen Datensatz erneut.
- snapshot() prüft die exakten Bytes einer ausdrücklich vorhandenen JSON-Datei. preview_import() prüft ausschließlich explizit übergebene Bytes. Beide Funktionen ändern keine Datei.
- export_snapshot() liefert nach erfolgreicher Integritätsprüfung exakt den ursprünglichen Byte-Stream. snapshot_manifest() gibt nur nicht-personenbezogene Metadaten zurück.

Die bestehende Mapping-API application_id → record, Statuswerte und unbekannten Alt-Felder bleiben erhalten. Die bisherigen Aufrufer können schrittweise weiter migriert werden; dieser PR stellt die gemeinsame Schnittstelle bereit, migriert aber noch nicht pauschal jeden Read-Modify-Write-Aufrufer auf mutate().

## 3. Tests

tests/test_application_repository.py verwendet ausschließlich synthetische Objekte und temporäre Dateien. Abgedeckt sind:

- Laden/Speichern des vorhandenen Mapping-Formats und Default-Verhalten;
- Ablehnung nicht gemappter Save-Payloads;
- atomare Mutationen, Callback-Fehler ohne Schreibversuch und parallele Updates;
- exakte Byte-Parität und Snapshot-Manifest;
- Importvorschau ohne Schreiboperation sowie Ablehnung doppelter Schlüssel;
- verzögerte Integration der webserver.load_json-/save_json-Helfer;
- konfliktfreie Feldzusammenführung, Erhalt gleichzeitiger Reviews/Verlaufsereignisse und Ablehnung veralteter Status-/Löschkonflikte.

tests/test_application_import.py deckt weiterhin den strikten Importvertrag, unbekannte Felder, UTF-8, ungültige JSON-Payloads, Manipulation und Schema-Versionen ab.

## 4. Bewusst noch nicht umgesetzt

- keine dauerhafte Snapshot-Datei und kein persistiertes Manifest;
- keine Generation-/Revision-Verwaltung über Prozessneustarts hinweg;
- kein produktiver Import, Restore oder Rollback;
- keine SQLite-Tabellenanlage und keine Backend-Auswahl;
- keine automatische Umschaltung von applications.json;
- keine Änderung an Bewerbungsformularen, URLs, Statuswerten, Rechten, Team seit oder Retention;
- keine Löschung, Anonymisierung oder Backfill alter accepted-/hired-Datensätze;
- keine Produktionsdaten, keine VPS-Aktion und kein Deployment durch diesen PR.

## 5. Nächste Teilumfänge

1. Weitere schreibende Pfade mit Status-/Entscheidungswirkung und Retention auf den Repository-Vertrag umstellen; Konkurrenztests auch bei Abbruch/Fehler nach externer Nebenwirkung ergänzen.
2. Persistentes Snapshot-/Manifest-Protokoll mit geprüfter Generation/Revision, No-Overwrite-Verhalten und Recovery-Regeln entwerfen.
3. Import-Dry-Run, Mapping-Parität, Konfliktbehandlung und Rollback ausschließlich mit synthetischen Daten und temporären Pfaden testen.
4. Den JSON-Adapter unabhängig reviewen. Erst danach darf eine SQLite-Adapter-Implementierung vorbereitet werden; Backend-Umschaltung und Produktivmigration bleiben separate, ausdrücklich freizugebende Betriebsschritte.

Der Adapter und konfliktarme Entscheidungsablauf sind technische Zwischenschritte. Ein grüner PR-CI-Lauf oder ein gemergter Code-Adapter ist keine Produktionsmigration. Phase 15.6d gilt erst als abgeschlossen, wenn Import, Snapshot-Persistenz, Wiederherstellung, Parität, Fehler-/Abbruchpfade und Review bestanden sind.
