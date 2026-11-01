# Phase 15.0 – Read-only-Inventur von logs.json

**Status:** Analyse abgeschlossen · keine Produktivänderung  
**Basis:** `main` @ `690b14bbca79f694c103a5d8d4f0a19e450f84df` (Merge von PR #112)  
**Branch:** `phase/15-0-logs-json-inventory`  
**Datum:** 01.11.2026

> Diese Phase untersucht ausschließlich den versionierten Quellcode und die dazugehörige Dokumentation. Es wurden keine Laufzeitdateien, Discord-Daten, Datenbanken oder VPS-Dateien gelesen oder verändert. Es wurde keine Migration ausgeführt.

---

## 1. Ergebnis in Kürze

`logs.json` ist die persistente Melonly-/Roblox-Moderationsakte. Sie ist aktuell eine JSON-Liste, die von den Dashboard- und API-Pfaden vollständig oder teilweise eingelesen wird. Die aktiven Schreibpfade sind drei Dashboard-Endpunkte in `webserver.py` (Erstellen, Bearbeiten, Löschen).

Die ältere Datenlandkarte überschätzt die Zahl der Fachmodule, die die Datei tatsächlich lesen oder schreiben: Mehrere dort aufgeführte Module erwähnen sie nur in Backup-Listen, Größenwarnungen oder Kommentaren. Die wirklichen Fachzugriffe konzentrieren sich auf `webserver.py`, `pulse_pro.py` und `cogs/pulse_suite.py`. Die Datenlandkarte und ihre statische Erkennung sollten in einer separaten Doku-/Tooling-Aufgabe aktualisiert werden; diese Phase verändert die generierte Karte nicht.

**Wichtigste Befunde:**
1. Mehrere aktive Ansichten und APIs lesen die gesamte Datei, auch wenn sie nur einen Zähler, die letzten Einträge oder eine einzelne Roblox-Spielerakte benötigen. Die globale Suche serialisiert zusätzlich jeden Eintrag für eine Textsuche.
2. Die Schreiboperationen arbeiten derzeit mit Load–Modify–Save auf der gesamten JSON-Liste. Die zentrale Sperre schützt einzelne Lese- und Schreibaufrufe; eine gemeinsame transaktionale Melonly-Schnittstelle für die gesamte Änderung gibt es noch nicht. Ein tatsächlicher Datenverlust durch parallele Anfragen wurde in dieser Inventur nicht reproduziert und wird daher nicht als bestätigter Produktionsfehler behauptet.
3. Die aktive globale Suche und zwei Roblox-API-Handler lesen Melonly-Daten, ohne im jeweiligen Handler eine eigene Melonly-Berechtigung zu prüfen. Das ist vor einer Migration oder Erweiterung mit Verhaltenstests sicherheitsseitig zu klären.
4. Für `logs.json` ist keine Retention-Regel implementiert. Die bestehende Größenprüfung warnt ab 5 MiB, begrenzt den Bestand aber nicht.
5. Das aktuelle Komplett-Backup sichert `logs.json` mit Größe und SHA-256; ältere Backup-Wege und die Konsistenz des gesamten Datenbestands haben andere Garantien.

**Entscheidung:** Noch keine SQLite-Implementierung und keine Bereinigung. Zuerst müssen die Sicherheits- und Datenvertragsfragen festgehalten werden. Danach kann Phase 15.1 das konkrete Tabellenschema und den Migrations-/Rollback-Ablauf festlegen.

## 2. Aktive Leser und Schreiber

Die Zuordnung folgt dem Routen-Snapshot in `docs/routen_snapshot.json` und dem aktuellen Registrierungsverhalten. Frühere Handler, die zwar noch im Quelltext stehen, aber zur Laufzeit ersetzt werden, sind nicht als aktive Leser gezählt.

| Ort | Zugriff | Aktive Funktion | Umfang / Zweck |
|---|---|---|---|
| `webserver.py` | Lesen | `GET /api/roblox-user` | Lädt die Liste vollständig und berechnet für einen Roblox-Nutzer die Anzahl und Zusammenfassung früherer Vorgänge. |
| `webserver.py` | Lesen | `GET /api/roblox-search` | Prüft die letzten 5.000 Einträge rückwärts auf lokale Namensvorschläge und ergänzt Ergebnisse aus Roblox. |
| `pulse_pro.py` | Lesen | `work_stats()` → aktives `GET /dashboard` | Lädt die ganze Liste nur, um die Anzahl der Logs im Command-Center-Zähler zu ermitteln. |
| `pulse_pro.py` | Lesen | aktives `GET /melonly` | Lädt die Liste und zeigt bis zu 120 neueste Einträge. Die Seite prüft Melonly-Zugriffsrechte und filtert die angebotenen Aktionstypen nach Einzelrechten. |
| `cogs/pulse_suite.py` | Lesen | `search_all()` → `GET /suite/search` und `GET /suite/api/search` | Durchsucht jeden Eintrag, indem er zu JSON serialisiert und kleingeschrieben wird; Treffer liefern Zielname und Roblox-ID. |
| `webserver.py` | Schreiben | `POST /log/create` | Hängt einen neuen Vorgang an die Liste an. |
| `webserver.py` | Schreiben | `POST /log/edit` | Sucht den Eintrag per ID und aktualisiert Zielname, Roblox-ID, Aktion und Grund; ergänzt Bearbeitungsfelder. |
| `webserver.py` | Schreiben | `POST /log/delete` | Entfernt den Eintrag anhand seiner ID physisch aus der Liste. |
| `webserver.py` | Lesen | `GET /export/logs` | Exportiert alle Einträge in CSV; die Route verlangt `can_view_analytics`. |
| `webserver.py` | Lesen/Restore | JSON-Panel-Backup und -Wiederherstellung | `add_panel_data()` nimmt `logs.json` in `panel_data.logs` auf; der Admin-Restore schreibt den Eintrag über `save_json` zurück. |
| `pulse_backup.py` | Backup/Restore | Komplett-Backup | `JSON_FILES` enthält `logs.json`; das Manifest speichert Größe und SHA-256. Der Restore validiert JSON und verwendet eine vorherige Sicherheitskopie. |
| `pulse_next.py` | Legacy-Backup/Restore | älterer ZIP-Backup-Pfad | Nimmt `logs.json` in die Dateiliste auf und prüft die JSON-Mitglieder vor dem Restore. Dieser Pfad ist nicht die maßgebliche neue Komplettsicherung. |
| `scripts/pulse_migrate_data.py` | Laufzeitdatei-Übernahme | Datenverzeichnis-Migration | Kopiert die Datei als Laufzeitdatei; verändert ihr internes Schema nicht. |
| `pulse_consistency.py` | Metadaten | Größenwarnung | Prüft nur die Dateigröße, liest keine Log-Datensätze. |
| `tools/data_map.py` | Entwicklerwerkzeug | statische Inventur | Erkennt Dateinamen/Aliase und typische Schreibmuster im Quelltext; kein Laufzeitleser. |

### Nicht aktive bzw. nicht als Fachzugriff gezählte Stellen

- Der frühere `webserver.dashboard_main`-Handler wird durch `pulse_pro.register.<locals>.dashboard_v5` ersetzt.
- Der frühere `webserver.melonly_alias`-Handler wird durch `pulse_pro.register.<locals>.melonly_page` ersetzt.
- Die dort verbliebenen Lesezugriffe sind Legacy-Code und dürfen nicht als aktive Seitenabfragen oder Grundlage für ein neues Datenmodell behandelt werden.
- `pulse_features.py` enthält im aktuellen Stand keine Referenz auf `logs.json` oder `LOGS_FILE`.
- `pulse_retention.py` erwähnt in diesem Zusammenhang nur die Audit-Aufbewahrung; es führt keine Bereinigung von `logs.json` aus.

## 3. Datenvertrag und Kompatibilität

### 3.1 Aktuelle Form

Der aktuelle Speichervertrag ist eine JSON-Liste von Einträgen. Die Reihenfolge der Liste ist fachlich relevant: neue Einträge werden angehängt, neueste Ansichten arbeiten mit umgekehrter Listenreihenfolge, und die Vorschlagssuche begrenzt sich auf die letzten 5.000 Listeneinträge.

Ein neuer Eintrag aus `create_log` enthält derzeit diese Felder:

| Feld | Bedeutung | Aktuelle Behandlung |
|---|---|---|
| `id` | Log-Kennung | `log_` plus sechs zufällige Hex-Zeichen |
| `target_user` | Roblox-Spielername | auf 50 Zeichen begrenzt |
| `roblox_id` | Roblox-ID | 1–15 Ziffern, andernfalls `N/A` |
| `type` | Aktion | `Warn`, `Kick`, `Ban`, `Notiz`, `Ban BOLO` |
| `reason` | Begründung/Notiz | auf 1.000 Zeichen begrenzt |
| `moderator` | Name des Bearbeiters | Text |
| `moderator_id` | Discord-ID des Bearbeiters | wird als Wert gespeichert |
| `created_at` | Erstellzeit | lokale Berlin-Zeit als `TT.MM.JJJJ HH:MM` |

Beim Bearbeiten kommen `edited_at` und `edited_by` hinzu. Unbekannte oder zusätzliche Felder werden vom aktuellen Update-Pfad nicht entfernt. Der Zeitstempel ist kein ISO-Zeitstempel und die Listenposition – nicht ein Zeitstempelvergleich – bestimmt die aktuelle Reihenfolge.

### 3.2 Zwingende Kompatibilitätsregeln

Eine spätere Migration muss daher:

- jedes vorhandene JSON-Objekt vollständig und verlustfrei bewahren, auch unbekannte Felder und alte Datumsformate;
- die ursprüngliche Listenposition speichern und die Reihenfolge exakt wiederherstellen;
- doppelte oder fehlende optionale Felder nicht still zusammenführen oder verwerfen;
- die vorhandene ID als Such-/Anzeigefeld behandeln, nicht ungeprüft als eindeutigen Primärschlüssel;
- die physische Löschsemantik, Bearbeitungsfelder und alle bisherigen CSV-Spalten erhalten;
- keine automatische Normalisierung von Namen, Datumsformaten oder Aktionstypen durchführen;
- die bestehenden URLs, Formularfelder, Berechtigungen und Redirects unverändert lassen.

Für eine SQLite-Tabelle ist deshalb ein technischer Primärschlüssel wie `seq INTEGER PRIMARY KEY` mit einer vollständigen JSON-Payload-Spalte sinnvoll. Extrahierte Spalten für Roblox-ID, normalisierten Benutzernamen, Aktion und Log-ID dienen als nicht-destruktive Suchindizes. Für `id` sollte zunächst kein UNIQUE-Constraint vorausgesetzt werden.

## 4. Performance-Befund

Der aktuelle JSON-Speicher wird bei jedem `load_json` vollständig geparst. Daraus ergeben sich konkrete unnötige Arbeitsschritte:

- Dashboard-Statistik: vollständiger Parse nur für `len(logs)`.
- Melonly: kompletter Parse, obwohl maximal 120 Zeilen angezeigt werden.
- Roblox-Suche: kompletter Parse, bevor die letzten 5.000 Einträge geprüft werden.
- Spielerhistorie: kompletter Parse für eine Abfrage nach Roblox-ID oder Name.
- Globale Suche: kompletter Parse plus JSON-Serialisierung jedes Eintrags für eine Teilstring-Suche.
- CSV-Export: vollständiger Durchlauf ist fachlich erforderlich, kann aber später gestreamt werden.

Das bedeutet nicht, dass jeder einzelne Zugriff sofort langsam ist. Es bedeutet, dass Arbeit und Speicherbedarf mit der Gesamtgröße der Datei wachsen und die selektiven Abfragen derzeit keinen Index nutzen können. Die bestehende Größenwarnung ab 5 MiB in `pulse_consistency.GROWTH_WARN_BYTES` meldet das Wachstum nur; sie begrenzt es nicht.

## 5. Schreibsicherheit und Nebenläufigkeit

`webserver.load_json` und `webserver.save_json` schützen ihre einzelnen Dateizugriffe durch denselben `threading.RLock` (`_io_lock`). Der Schreibpfad nutzt die zentrale atomare JSON-Schreibfunktion. Beschädigte JSON-Dateien werden über `_CORRUPT_JSON` markiert; weitere Schreibversuche werden blockiert, statt Standardwerte über die Datei zu schreiben.

Die drei Melonly-CRUD-Endpunkte führen aktuell synchron `load_json → Änderung → save_json` aus. Zwischen diesen Aufrufen liegt in den untersuchten Handlern kein `await`. Deshalb wird in dieser Inventur kein konkreter Lost-Update-Fehler behauptet. Gleichwohl umfasst der Lock jeweils nur den einzelnen Lade- oder Speichervorgang; die gesamte fachliche Änderung ist keine eigenständige Repository-Transaktion. Für eine künftige Mehrbenutzer-/Thread-sichere Speicherung sollte die CRUD-Operation in einer einzigen Transaktion bzw. einer zentral gesperrten Storage-Methode stattfinden.

## 6. Backup, Restore und Aufbewahrung

### 6.1 Komplett-Backup – maßgeblicher aktueller Weg

`pulse_backup.JSON_FILES` enthält `logs.json`. Das Komplett-Backup schreibt je Datei Größe und SHA-256 in das Manifest. Der Restore:

1. prüft das gesamte Archiv und seine Prüfsummen;
2. validiert die JSON-Datei;
3. erstellt zuerst ein vollständiges Sicherheits-Backup;
4. aktiviert den Wartungsmodus;
5. schreibt JSON-Dateien atomar und kann bei Fehlern den vorherigen Stand aus der Sicherheitskopie zurückspielen.

Beim Webserver-Restore wird der JSON-I/O-Lock an den Restore übergeben. Im Komplett-Backup wird `pulse.db` zusammen mit `shifts.json` unter dem JSON-Lock gesichert; `logs.json` wird anschließend als eigene atomare Datei in das Archiv übernommen. Ein vollständiger, zeitgleicher Snapshot aller JSON-Dateien wird dadurch nicht zugesichert, aber die einzelne Datei wird durch den atomaren Schreibpfad nicht als halbfertige JSON-Datei kopiert.

### 6.2 Weitere Wege

- Das ältere `pulse_next.create_backup()` enthält `logs.json`, hat aber ein einfacheres Manifest ohne per-Datei-SHA-256 und kopiert die Datei nicht unter demselben JSON-Lock. Es ist als Legacy-Backup-Pfad zu behandeln.
- Das JSON-Panel-Backup nimmt die Liste in `panel_data.logs` auf und stellt sie über `save_json` wieder her.
- `scripts/pulse_migrate_data.py` übernimmt die Laufzeitdatei als Datei; es ist keine inhaltliche Datenmigration.

### 6.3 Retention

Es gibt keine Aufbewahrungsfrist und keinen automatischen Retention-Lauf für `logs.json`. Die bestehenden Entscheidungen E-9 zu Bewerbungen (183 Tage) und Audit-Protokollen (730 Tage) gelten nicht automatisch für Melonly-Vorgänge. Vor dem Löschen/Archivieren von Melonly-Einträgen ist eine gesonderte Entscheidung nötig. Die Migration darf keinerlei Einträge abschneiden.

## 7. Sicherheitsbefunde für gesonderte Prüfung

Diese Inventur ändert keine Berechtigungen. Die folgenden aktiven Codepfade sollten jedoch vor bzw. spätestens zusammen mit einer späteren Freigabe der Such-/Speicher-API mit Regressionstests geprüft werden.

### SEC-15.0-1 – Roblox-API gibt Melonly-Zusammenfassung ohne sichtbare Melonly-Prüfung zurück

`GET /api/roblox-user` ruft `get_current_user` auf und berechnet anschließend `previous_total` und `previous_summary` aus `logs.json`. Im Handler ist keine zusätzliche Melonly-Rechteprüfung sichtbar. Der aktuelle Routenbericht beschreibt die Route als „Session (eigene Prüfung)“.

**Folge:** Eine gültige Dashboard-Sitzung reicht nach dem sichtbaren Handler-Code aus, um eine Zusammenfassung vorhandener Melonly-Einträge für einen Roblox-Namen anzufragen. Ob diese Freigabe fachlich beabsichtigt ist, ist nicht dokumentiert.

### SEC-15.0-2 – Roblox-Suchvorschläge lesen die Melonly-Liste nur sitzungsbasiert

`GET /api/roblox-search` prüft die Sitzung und Rate-Limits, scannt aber ebenfalls die lokalen Melonly-Einträge, um Namen und Roblox-IDs für Vorschläge bereitzustellen. Eine ausdrückliche Melonly-Berechtigungsprüfung ist im Handler nicht sichtbar.

### SEC-15.0-3 – Globale Suche gibt Melonly-Treffer an allgemeine Dashboard-Nutzer zurück

`search_all()` in `cogs/pulse_suite.py` durchsucht alle Felder jedes Melonly-Objekts. Bei einem Treffer liefert es Zielname und Roblox-ID zurück. `/suite/search` und `/suite/api/search` rufen `auth()` auf, prüfen im gezeigten Suchpfad aber nicht separat, ob der Nutzer eine Melonly-Berechtigung besitzt.

**Empfehlung:** Diese drei Punkte als eigene Security-Regressionstests behandeln. Vor einem Fix muss feststehen, welche Melonly-Rechte für Namensvorschläge, Vorstrafen-Zusammenfassungen und Suchtreffer gelten. Die korrekte Sperre muss serverseitig erfolgen; eine bloß ausgeblendete UI reicht nicht. Die bereits vorhandenen Rollen-/Rechteentscheidungen bleiben unangetastet, bis die Tests und eine konkrete Änderung vorbereitet sind.

## 8. Migrationsvorschlag für Phase 15.1

Keine Änderung in Phase 15.0. Für die nächste Entwurfsphase wird folgender Ansatz empfohlen:

1. **SQLite-Tabelle als unverlustfreie Ablage:** `seq INTEGER PRIMARY KEY`, vollständiges Originalobjekt als JSON-Payload, dazu nullable extrahierte Indexfelder.
2. **Indizes nach realen Anwendungsfällen:** nicht-eindeutige Log-ID, Roblox-ID, normalisierter Zielname, Aktionstyp und bei Bedarf ein Suchindex. Zeitstempel zunächst unverändert als Text speichern; Sortierung bleibt über `seq`.
3. **Storage-Service statt direkter Dateizugriffe:** zentrale Funktionen für Append, Update, Delete, Latest, Player-History, Roblox-Suggestions, Aggregates, Search und Export. Die CRUD-Änderung läuft jeweils in einer Transaktion.
4. **Migrationsrahmen wiederverwenden:** `pulse_migrations`, Sicherheitskopie vor Start, idempotente Migration, Anzahl-/Reihenfolge-/Payload-Vergleich und geprüfter Rückweg auf JSON.
5. **Kompatibilitätsübergang:** erst den Adapter und die vollständigen Tests einführen; alte Routen/JSON-Semantik bleiben bis zur separaten Freigabe funktionsfähig. Keine Produktionsumschaltung in dieser Phase.
6. **Performance-Messung:** synthetische Datensätze mit mindestens 20.000 und zusätzlich 100.000 Einträgen; Median mehrerer Läufe, Speicherbedarf, Ergebnisgleichheit und tatsächliche Query-Pläne dokumentieren.

### Vorgesehene Tests

- leere Datei, fehlende Datei, beschädigte JSON-Datei und falsche Top-Level-Struktur;
- neue, alte und zusätzliche unbekannte Felder;
- Reihenfolge und Anzahl exakt erhalten; doppelte Log-IDs bleiben getrennte Datensätze;
- Erstellen, Bearbeiten und Löschen einschließlich Berechtigungsfällen;
- gleiche Ergebnisse für Name, Roblox-ID, Vorschläge, letzte 50/120/5.000, Zähler und CSV;
- Zeitstempel und Export bleiben byte-/fachlich kompatibel;
- gleichzeitige Schreibvorgänge und idempotente Wiederholung;
- Backup, Restore, Rückmigration und Prozessabbruch;
- Rechteprüfungen SEC-15.0-1 bis SEC-15.0-3.

## 9. Quellen im Repository

Die folgenden Fundstellen wurden gegen den Ausgangs-Commit dieser Phase geprüft:

- `webserver.py`: `LOGS_FILE`, `_io_lock`, `load_json`, `save_json`, Roblox-API-Handler, Melonly-CRUD, CSV-Export, Panel-Backup und -Restore.
- `pulse_pro.py`: aktive `dashboard_v5`-Statistik und aktive `melonly_page`.
- `cogs/pulse_suite.py`: `search_all`, `/suite/search`, `/suite/api/search`.
- `pulse_backup.py`: `JSON_FILES`, Manifest, Voll-Backup, Restore und Restore-Probelauf.
- `pulse_next.py`: Legacy-Backup-/Restore-Liste.
- `pulse_retention.py`: aktive Retention-Regeln (ohne `logs.json`).
- `pulse_consistency.py`: 5-MiB-Wachstumswarnung.
- `scripts/pulse_migrate_data.py`: Übernahme der Laufzeitdatei.
- `docs/routen_snapshot.json` und `docs/ROUTEN.md`: aktive gegenüber verdrängten Handlern.
- `tests/test_pulse_melonly.py`, `tests/test_permission_sweep.py`, `tests/test_full_backup.py`: vorhandene Testabdeckung.

## 10. Abschluss von Phase 15.0

- [x] Aktive Leser und Schreiber im aktuellen Code eingegrenzt.
- [x] Verdrängte Legacy-Handler von aktiven Routen getrennt.
- [x] Datenvertrag, Reihenfolge und Kompatibilitätsanforderungen dokumentiert.
- [x] Locking, Backup, Restore, Größenwarnung und Retention geprüft.
- [x] Sicherheitsprüfpunkte für die getrennte Berechtigungsphase erfasst.
- [x] Migrationsentwurf und erforderliche Regressionstests vorgeschlagen.
- [ ] Unabhängiges Review des Berichts und der Migrationsanforderungen.
- [ ] Phase 15.1 – konkretes SQLite-Schema und Migration – ist eine eigene Folgephase.

**Tests:** Keine automatisierten Tests ausgeführt. Phase 15.0 ist eine rein lesende Quellcode-Inventur; es wurden keine Implementierung oder Laufzeitdaten geändert.
