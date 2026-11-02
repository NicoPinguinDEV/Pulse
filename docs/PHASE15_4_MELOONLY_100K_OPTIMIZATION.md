# Phase 15.4 – Melonly 100k Query-Optimierung

**Status:** Implementierung im Review-PR · keine Produktivmigration  
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

- Neuer deterministischer Ausdrucksindex auf der normalisierten Roblox-ID.
- Spielerhistorie ermittelt zuerst kleine Kandidatenmengen über Indexspalten und lädt danach die benötigten Payloads.
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
- Verwendung des normalisierten SQLite-Ausdrucksindex via EXPLAIN QUERY PLAN.
- Bestehende CRUD-, CSV-, Backup-, Berechtigungs- und Repository-Tests.

## Bewusste Grenzen

- search_payload() im SQLite-Backend hat aktuell noch keine trigram-/FTS-Suche. Der JSON-Pfad vermeidet wiederholte JSON-Serialisierung, aber ein erster Cache-Aufbau durchsucht alle Datensätze.
- Der JSON-Snapshot benötigt zusätzlichen Prozessspeicher. Die 20k-/100k-Benchmarkmessung muss deshalb Laufzeit und Speicher auf derselben Hardware dokumentieren.
- Bereits vorhandene SQLite-Datenbanken ohne den neuen Ausdrucksindex bleiben funktionsfähig; solange der Index dort nicht durch ein separates, gesichertes Offline-Upgrade angelegt wurde, kann SQLite für diese Abfrage auf einen Tabellen-Scan zurückfallen.
- Kein automatisches Upgrade einer bestehenden Datenbank beim Bot-Start.

## Freigabekriterien

1. Normale CI vollständig grün.
2. JSON-/SQLite-Paritätstests vollständig grün.
3. Melonly Storage Benchmark mit 20.000 und 100.000 Einträgen ausführen.
4. Laufzeit und Speicherbedarf vergleichen; keine harten Performance-Versprechen ohne Messwerte.
5. Getrennten PR für weitere Suchoptimierungen erst nach Auswertung der Messwerte erstellen.

**Kein VPS-Deploy, keine Datenbankmigration, keine Backend-Aktivierung und keine Änderung an Produktivdaten sind Bestandteil dieses PR.**
