# Phase 15.6d – Bewerbungsimport: erster Integritätsvertrag

**Status:** Erster isolierter Implementierungsbaustein · PR-CI und Review ausstehend
**Basis:** main @ bf62017ab4ea992d183ac1ba521ab71e0ee5a0ed
**Abgrenzung:** Keine Laufzeitintegration, keine Produktivdaten und keine Backend-Umschaltung.

## Ziel dieses Teilumfangs

Vor dem Aufbau eines aktiven Application-Repositorys wird der Importvertrag festgelegt. Die erste Implementierung in pulse_application_import.py ist eine reine In-Memory-Komponente: Sie nimmt ausschließlich explizit übergebene JSON-Bytes entgegen und hat keine Datei-, Datenbank-, Netzwerk- oder Laufzeit-Nebenwirkungen.

## Vertrag

- Nur strict UTF-8 und valides JSON werden akzeptiert.
- Das Top-Level muss ein Objekt sein (das bestehende Mapping application_id → record).
- Doppelte JSON-Schlüssel werden auf jeder Objektebene abgelehnt; es gibt kein stilles Last-key-wins.
- NaN und Infinity werden als nicht standardkonforme JSON-Zahlen abgelehnt.
- Unbekannte Felder und Werte werden nicht normalisiert oder entfernt.
- SHA-256 wird über die exakten Quellbytes berechnet.
- Der Snapshot enthält schema_version, generation, revision, sha256, record_count und die unveränderten Bytes.
- Verifikation prüft Schema, Zähler, Digest und Record-Anzahl erneut.
- Export liefert nach erfolgreicher Verifikation exakt dieselben Bytes zurück; JSON wird nicht neu serialisiert.

## Tests

tests/test_application_import.py deckt Byte-Parität, unbekannte Felder, UTF-8, leere Objekte, ungültiges JSON, doppelte Schlüssel, nicht standardkonforme Zahlen, ungültige Generation/Revision, Snapshot-Manipulation und nicht unterstützte Schema-Versionen ab. Alle Fixtures sind synthetisch.

## Bewusst nicht Teil dieses Bausteins

- kein Lesen aus APPS_FILE oder anderen Standard-/Produktivpfaden;
- keine Snapshot-Datei, kein Sidecar und keine Kopie von Bewerberdaten auf Datenträger;
- keine SQLite-Tabellenanlage, Migration, Importausführung oder Backend-Auswahl;
- keine Änderung an URLs, Formularen, Bewerbungsstatus, Rechten, team_since_for oder Retention;
- keine Löschung, Anonymisierung, Backfill oder VPS-Aktion.

## Nächste getrennte Schritte

1. JSON-Repository-Adapter mit unveränderter Mapping-API und kontrollierter Sperr-/Schreibsemantik.
2. Persistentes Snapshot-/Manifest-Protokoll mit Generation/Revision, SHA-256 und Wiederanlaufregeln.
3. Synthetischer Import-Dry-Run, Paritätsvergleich und Rollback-Probe.
4. Erst nach separater Abnahme: mögliche SQLite-Adapter-Implementierung; keine Aktivierung ohne eigenständige Freigabe.

Die Implementierung dieses Vertrags allein bedeutet ausdrücklich **nicht**, dass Phase 15.6d vollständig abgeschlossen oder eine produktive Migration freigegeben ist.
