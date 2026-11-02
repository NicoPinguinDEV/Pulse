# Phase 15.4 – Melonly 100k Query-Optimierung

**Status:** Code in PR #128 in main gemergt · Performance-Nachweis in PR #130 dokumentiert  
**Produktiv-Backend:** JSON bleibt unverändert aktiv  
**Datenbestand:** keine Payloads umgeschrieben, entfernt oder normalisiert

## Ziel

Die häufigen Lesezugriffe dürfen bei 100.000 Melonly-Einträgen nicht bei jedem Dashboard-Aufruf die gesamte Datei erneut einlesen und parsen. Gleichzeitig müssen die bisherigen Such-, Sortier-, ID- und Exportregeln unverändert bleiben.

## Änderungen

### JSON – aktiver Produktionspfad

- get_melonly_repository() verwendet pro Prozesskonfiguration eine gemeinsame Repository-Instanz.
- JsonMelonlyRepository hält einen Snapshot nur so lange, wie Geräte-ID, Inode, Dateigröße sowie Änderungs- und Erstellungszeitpunkt der logs.json unverändert sind.
- Ein atomarer externer Dateiaustausch invalidiert den Snapshot beim nächsten Zugriff.
- Beim Schreiben wird der Snapshot nach erfolgreichem save_json mit dem neuen Datenbestand aktualisiert.
- In-Memory-Indizes beschleunigen Spielerhistorie und Roblox-Verlauf.
- Die serialisierten Suchtexte für die globale Suche werden bei Bedarf einmalig aufgebaut und bis zur nächsten Dateiänderung wiederverwendet.
- Alle nach außen zurückgegebenen JSON-Datensätze werden kopiert; UI-Aufrufer können den internen Snapshot nicht versehentlich verändern.

### SQLite – künftiger optionaler Pfad

- Neue gespeicherte Spalte `roblox_id_key` plus normaler B-Tree-Index auf der normalisierten Roblox-ID. Kein benutzerdefinierter SQLite-Ausdruck und keine UDF-Abhängigkeit bei Backups oder `PRAGMA integrity_check`.
- Spielerhistorie ermittelt zuerst kleine Kandidatenmengen über Indexspalten und lädt danach die benötigten Payloads. Die neue Spalte wird nur bei expliziter Schema-Initialisierung offline angelegt und aus den vorhandenen Lookup-Werten befüllt.
- Die bestehende Teilstring-Suche nach Namen bleibt erhalten.
- Temporäre Schlüssel-Tabellen verhindern SQL-Parameterlimits bei vielen Treffern.
- Roblox-Verlauf nutzt die vorhandenen ID- und normalisierten Namensindizes und prüft jeden Kandidaten anschließend gegen die ursprüngliche Python-Predicate.

## Unveränderte Kompatibilitätsregeln

- Original-Payload inklusive unbekannter und zusätzlicher Felder.
- Ursprüngliche Listen-/Sequenzreihenfolge.
- Doppelte, fehlende und numerische Log-IDs.
- Roblox-IDs mit Leerzeichen und alte Datentypen.
- Alte Datumsformate und Aktionstypen.
- Bestehende Dashboard-Routen, Formularfelder, Rechteprüfungen, CSV-Header, BOM, Escaping und Zeilenreihenfolge.
- JSON bleibt Standard; PULSE_LOGS_BACKEND und active_backend werden nicht verändert.

## Regressionstests

- Cache-Wiederverwendung und externe atomare Dateiersetzung.
- Defensive Kopien der zurückgegebenen Einträge.
- JSON-/SQLite-Gleichheit bei Spielerhistorie und Roblox-Verlauf.
- Sonderfälle: numerische Null gegenüber String-Null, None, fehlende Felder, gepolsterte Roblox-IDs und Teilnamenssuche.
- Verwendung des normalen `roblox_id_key`-Indexes via `EXPLAIN QUERY PLAN`; Backup-Integritätsprüfung ohne benutzerdefinierte SQLite-Funktionen.
- Bestehende CRUD-, CSV-, Backup-, Berechtigungs- und Repository-Tests.

## Bewusste Grenzen

- search_payload() im SQLite-Backend hat aktuell noch keine trigram-/FTS-Suche. Der JSON-Pfad vermeidet wiederholte JSON-Serialisierung, aber ein erster Cache-Aufbau durchsucht alle Datensätze.
- Der JSON-Snapshot benötigt zusätzlichen Prozessspeicher. Die 20k-/100k-Benchmarkmessung muss deshalb Laufzeit und Speicher auf derselben Hardware dokumentieren.
- Bereits aktivierte SQLite-Datenbanken ohne die neue Spalte bleiben funktionsfähig; solange sie nicht durch ein separates, gesichertes Offline-Schema-Upgrade ergänzt wurden, verwendet die Spielerhistorie den kompatiblen Scan-Pfad.
- Kein automatisches Upgrade einer bestehenden Datenbank beim Bot-Start.

## Performance-Nachweis

Die Messung mit 20.000 und 100.000 synthetischen Einträgen ist abgeschlossen. Beide Paritätsprüfungen waren erfolgreich. Vollständige Laufzeit-, Speicher- und Bewertungsdaten stehen im [Phase-15.4-Performancebericht](PHASE15_4_MELOONLY_PERFORMANCE_REPORT.md). Der Benchmark-Fix aus PR #129 ist in main enthalten.

## Freigabekriterien

1. Normale CI vollständig grün.
2. JSON-/SQLite-Paritätstests vollständig grün.
3. Melonly Storage Benchmark mit 20.000 und 100.000 Einträgen ausgeführt; beide Paritätsprüfungen bestanden.
4. Laufzeit und Speicherbedarf im Performancebericht dokumentiert; Runner-Messwerte ausdrücklich nicht als VPS-Messwerte behandeln.
5. Weitere Suchoptimierungen nur nach separatem Query-Plan-/Profiling-Nachweis umsetzen.

**Kein VPS-Deploy, keine Datenbankmigration, keine Backend-Aktivierung und keine Änderung an Produktivdaten sind Bestandteil dieses PR.**
