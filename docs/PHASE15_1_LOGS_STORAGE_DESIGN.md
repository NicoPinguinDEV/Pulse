# Phase 15.1 – Speicher- und Abfragevertrag für logs.json

**Status:** Entwurf auf separatem Branch · kein Produktivcode geändert  
**Basis:** `main` @ `f98d4a8a694795db5e55da389a450423a6dffab2`  
**Datum:** 02.11.2026  
**Vorarbeit:** `docs/PHASE15_0_LOGS_INVENTUR.md`, `docs/PHASE14_7_BERICHT.md`, `pulse_shift_history.py`

> Diese Phase legt den verbindlichen technischen Vertrag für die spätere Umsetzung fest. Sie migriert keine Daten, schaltet kein Backend um und ändert keine Live-Konfiguration. Die produktive Standardquelle bleibt `logs.json`.

---

## 1. Ziel und Grenzen

Die aktive Melonly-Datenhaltung soll von einer vollständig eingelesenen JSON-Liste auf einen transaktionssicheren, indexierten SQLite-Speicher vorbereitet werden. Bestehende Dashboard- und Bot-Aufrufer sollen dabei ihre fachlichen Ergebnisse behalten.

**In Phase 15.1 enthalten:**
- Tabellen- und Indexentwurf;
- Repository-Schnittstelle und Verhalten bestehender Abfragen;
- Anforderungen für Migration, Rückweg, Backup und Wiederherstellung;
- verbindliche Regressionstest-Matrix für Phase 15.2.

**Ausdrücklich nicht enthalten:**
- Implementierung oder Aktivierung des SQLite-Backends;
- Änderung an `logs.json`, `pulse.db`, `.env` oder VPS-Dateien;
- Retention, automatische Bereinigung oder Zusammenführung von Datensätzen;
- Änderung an Discord-Rollen, Melonly-Rechten, URLs, Formularfeldern oder Dashboard-Design;
- Änderung an CSV-Spalten oder an den bestehenden JSON-Daten.

Keine Aufbewahrungsfrist für Melonly-Daten wird aus den Regeln für Bewerbungen oder Audit-Logs abgeleitet.

## 2. Verifizierte Bestandsanforderungen

Die Inventur aus Phase 15.0 weist folgende aktive Datenwege aus:

| Bereich | Benötigtes Verhalten |
|---|---|
| Melonly-Seite | Neueste 50 Einträge, Zähler nach Aktionstyp, vollständiger Spieler-Verlauf und Bearbeitungs-/Löschaktionen |
| Dashboard / Command Center | Gesamtanzahl der Melonly-Einträge |
| Roblox-Spielersuche | Kandidaten aus den letzten 5.000 Einträgen in umgekehrter Reihenfolge |
| Roblox-Spieler-API | Bisherige Vorgänge für einen Spieler zusammenfassen |
| Globale Suche | Volltextähnliche Teilstringsuche über die serialisierten Eintragsfelder; Melonly-Treffer nur für berechtigte Nutzer |
| CSV-Export | Vollständiger Export in bisheriger Reihenfolge und mit bisherigen Spalten |
| Schreiben | Anlegen, Bearbeiten und Löschen über die bestehenden POST-Routen |
| Backup / Restore | `logs.json` und `pulse.db` sind Teil des vollständigen Backups |

Die Melonly-Inventur dokumentiert auch die drei Sicherheitsprüfpunkte für Roblox-Lookups und globale Suche. PR #119 hat die Lesezugriffe bereits an `can_access_melonly(ctx)` gebunden. Phase 15.2 muss diese zentrale Rechteprüfung beibehalten und darf keine parallele Berechtigungslogik einführen.

### 2.1 Fachliche Semantik, die erhalten bleiben muss

- Die Listenposition ist die maßgebliche Reihenfolge. Zeitstempel sind keine verlässliche Sortiergrundlage und können historische Formate enthalten.
- Unbekannte JSON-Felder, optionale Felder, fehlende Felder und alte Datumsformate bleiben erhalten.
- Doppelte oder fehlende Log-IDs dürfen nicht durch einen neuen UNIQUE-Constraint zusammengeführt oder abgewiesen werden.
- Namen werden bei Anzeige und Speicherung nicht still normalisiert. Ein zusätzlicher normalisierter Suchschlüssel ist ausschließlich ein Index-Hilfswert.
- Die Spieler-Verlaufssuche behält ihre aktuelle Fallback-Reihenfolge: bei numerischer Anfrage zuerst Roblox-ID; bei fehlenden ID-Treffern Namensabgleich (exakt vor Teilstring); danach werden die passenden Namen und IDs zu einem vollständigen Verlauf zusammengeführt.
- Die Vorschlagssuche bleibt auf die letzten 5.000 Einträge begrenzt und wertet neuere Einträge zuerst aus.
- Die globale Suche prüft weiterhin die bisherigen Eintragsfelder. Ein FTS-Index darf das Ergebnis nicht durch abweichende Token-, Unicode- oder Teilstring-Semantik verändern.
- Bestehende Bearbeitungs- und Löschsemantik für IDs wird im Verhaltenstest festgehalten. Ein SQL-Update darf nicht versehentlich mehrere Datensätze mit derselben ID ändern, wenn der bisherige Handler nur einen Datensatz ändert.
- CSV-Header, Spaltenreihenfolge, Zellwerte und Zeilenreihenfolge bleiben unverändert.

Vor der Implementierung muss jeder dieser Punkte im aktuellen aktiven Handler und in mindestens einem Verhaltenstest nachvollzogen werden. Bei einem Konflikt zwischen dieser Beschreibung und tatsächlich ausgeführtem Verhalten gilt zunächst der Verhaltenstest bzw. die reproduzierte Bestandssemantik; Abweichungen sind im Phasenbericht zu dokumentieren, nicht still zu ändern.

## 3. Vorgeschlagenes SQLite-Schema

Die Datenbank bleibt `pulse.db`; es wird keine zweite Datenbankdatei eingeführt.

```sql
CREATE TABLE IF NOT EXISTS melonly_logs (
    seq             INTEGER PRIMARY KEY,
    log_id          TEXT,
    target_user     TEXT,
    target_user_key TEXT,
    roblox_id       TEXT,
    action_type     TEXT,
    created_at      TEXT,
    moderator_id    TEXT,
    payload         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_melonly_logs_log_id
    ON melonly_logs(log_id, seq);

CREATE INDEX IF NOT EXISTS idx_melonly_logs_roblox_id
    ON melonly_logs(roblox_id, seq);

CREATE INDEX IF NOT EXISTS idx_melonly_logs_target_user
    ON melonly_logs(target_user_key, seq);

CREATE INDEX IF NOT EXISTS idx_melonly_logs_action
    ON melonly_logs(action_type, seq);

CREATE TABLE IF NOT EXISTS melonly_logs_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

**Verbindliche Eigenschaften:**

1. `seq` ist der technische Primärschlüssel und bildet die ursprüngliche Listenposition ab. Beim Import ist die erste JSON-Position `seq = 1); neue Datensätze erhalten eine höhere Sequenz. Löschungen erzeugen zulässige Lücken.
2. `payload` enthält das vollständige Originalobjekt als JSON-Text. Das ist die maßgebliche Nutzlast; die extrahierten Spalten sind ausschließlich Such-/Indexfelder.
3. `log_id` ist ausdrücklich **nicht UNIQUE**. Vorhandene Duplikate bleiben eigenständige Datensätze.
4. Die Indexfelder werden aus dem Originalobjekt extrahiert, ohne dessen Werte zu verändern. Für `target_user_key` wird eine gesonderte, dokumentierte Normalisierung nur für die Suche verwendet.
5. `created_at` bleibt Text. Es wird kein Datum rückwirkend neu geparst oder normalisiert.
6. Es werden weder Fremdschlüssel zu Discord noch Abhängigkeiten von externen Suchdiensten eingeführt.
7. `melonly_logs_meta` hält mindestens `format_version`, `generation`, `active_backend`, `revision`, `import_source_sha256` und `import_count`. Das Backend-Merkmal wird innerhalb derselben Transaktion wie ein Schreibvorgang aktualisiert.
8. FTS5 ist keine Pflicht. Die globale Suche kann zunächst auf dem gespeicherten Payload laufen. Ein optionaler Suchindex ist nur zulässig, wenn Verfügbarkeit, Ergebnisgleichheit und Update-/Delete-Konsistenz nachgewiesen sind.

Der Entwurf ist additiv und unterstützt das bestehende Migrationsmuster. Vor der Implementierung werden Spaltennamen und Typen mit allen tatsächlich vorkommenden historischen Payloads gegengeprüft.

## 4. Repository-Vertrag

Die Fachrouten sollen zukünftig nicht mehr selbst vollständige Listen laden und verändern. Ein neues Modul (vorgeschlagener Name `pulse_melonly_store.py`) kapselt den Speicherzugriff.

Mindest-API:

| Funktion | Zweck / Rückgabevertrag |
|---|---|
| `count()` | Gesamtanzahl ohne Materialisierung aller Payloads |
| `count_by_type()` | Zähler je Aktionstyp; unbekannte Typen bleiben möglich |
| `latest(limit)` | Neueste Datensätze, in der bisherigen Listenreihenfolge zurückgegeben |
| `append(entry)` | Ein vollständiges Objekt anhängen; Sequenz wird im selben Commit vergeben |
| `find_first_by_id(log_id)` | Datensatz gemäß bisheriger ID-Semantik auflösen |
| `update_by_seq(seq, entry)` | Genau einen Datensatz aktualisieren, Reihenfolge bleibt bestehen |
| `delete_by_seq(seq)` | Genau einen Datensatz löschen |
| `player_history(query)` | Bisherige ID-/Namens-Fallback-Regeln und vollständige Treffer |
| `roblox_suggestions(limit=5000)` | Begrenzte Kandidatenbasis, neueste zuerst |
| `search_payload(query)` | Teilstringsuche mit gleicher fachlicher Treffersemantik |
| `export_all()` | Vollständige geordnete Datensätze für CSV |
| `iter_all()` | Speicherbewusster vollständiger Durchlauf, falls ein Export nicht materialisiert werden soll |

Die Namen sind interne Schnittstellen. Bestehende HTTP-Routen, Methoden, Statuscodes, Formularfelder, Redirects und Antwortfelder bleiben unverändert.

### 4.1 Transaktionen und Nebenläufigkeit

- Jede Schreiboperation nutzt eine SQLite-Transaktion mit `BEGIN IMMEDIATE`.
- Append, Update oder Delete und die Aktualisierung aller Indexspalten erfolgen atomar in derselben Transaktion.
- Ein fehlgeschlagener Commit darf keinen halben Datensatz hinterlassen.
- `seq` wird innerhalb der Transaktion vergeben und darf bei parallelen Schreibern nicht doppelt vergeben werden.
- Der bestehende `webserver._io_lock` bleibt für den JSON-/Migrations-Übergang relevant. Die Datenbank-Transaktion ist zusätzlich die verbindliche Schreibsperre im SQLite-Modus.
- Leser bekommen einen konsistenten SQLite-Snapshot. Keine Funktion darf bei DB-Fehlern stillschweigend `[]` oder `0` zurückgeben, weil das einen Datenverlust vortäuschen würde. Fehler werden als kontrollierter Fehler sichtbar gemacht und Schreibzugriffe bleiben gesperrt, bis die Ursache geklärt ist.
- Verbindungen werden pro Operation geöffnet/geschlossen oder über einen bereits etablierten, nachweislich threadsicheren Mechanismus verwaltet. Keine langlebige ungeschützte globale SQLite-Verbindung.

### 4.2 Bestehende Aufrufer

Phase 15.2 muss mindestens diese Codebereiche auf den Repository-Vertrag umstellen oder ihre Zugriffe ausdrücklich als kompatible JSON-Fallbacks begründen:

- aktive Melonly-Seite in `pulse_pro.py`;
- Dashboard-Zähler in `pulse_pro.py`;
- Roblox-Lookup und Roblox-Suchvorschläge in `webserver.py`;
- Melonly CRUD-Routen in `webserver.py`;
- Melonly-Anteil von `search_all()` in `cogs/pulse_suite.py`;
- CSV-Export in `webserver.py`;
- Voll-Backup und Restore in `pulse_backup.py`.

Keine verdrängten Legacy-Handler werden als aktive Aufrufer behandelt. Der Routen-Snapshot ist vor Änderungen erneut zu prüfen.

## 5. Migration und Rückweg

### 5.1 Startbedingungen

Die Migration ist ein eigener, ausdrücklich aktivierter Betriebsschritt. Ein normaler Start im Standardmodus verändert nichts.

- Vor der ersten Aktivierung ist JSON die Standardquelle; eine Installation ohne Melonly-Migrationsmetadaten bleibt unverändert im JSON-Modus.
- Der zukünftige Schalter `PULSE_LOGS_BACKEND` akzeptiert ausschließlich `json` und `sqlite` und wird nur beim Start ausgewertet, bevor Dashboard oder Bot Schreiboperationen ausführen.
- Nach Aktivierung speichert `melonly_logs_meta.active_backend` die tatsächlich maßgebliche Quelle. `PULSE_LOGS_BACKEND=sqlite` plus Metadaten `active_backend=sqlite` ist ein gültiger Startzustand.
- Bei vorhandenen Metadaten `active_backend=sqlite` und fehlendem/abweichendem Schalter darf Pulse **nicht** still auf die alte JSON-Sicherung zurückfallen. Der Start meldet einen eindeutigen Konfigurationsfehler; die Datenbank bleibt die maßgebliche Quelle.
- `PULSE_LOGS_BACKEND=json` ist ein ausdrücklich angeforderter Rückweg: JSON wird zunächst aus SQLite exportiert, geprüft und atomar ersetzt; erst danach wird `active_backend=json` gespeichert.
- Ein unbekannter Schalterwert oder eine nicht passende Generation führt zu einem sichtbaren Fehler und nicht zu einer stillen Backend-Wahl.
- Die Migration darf nicht allein durch das Vorhandensein der Tabelle oder durch einen Deploy automatisch aktiviert werden.

### 5.2 JSON → SQLite

1. `logs.json` wird unter dem zentralen JSON-I/O-Lock gelesen. Top-Level muss eine Liste sein; jedes Element muss dem bisher unterstützten Datentyp entsprechen. Ungültige oder unerwartete Daten brechen ab.
2. Vor jeder Änderung wird eine separate Datei `backups/logs_pre_sqlite_<timestamp>.json` erzeugt. Größe und SHA-256 werden geprüft. Die Quelldatei bleibt byte-identisch.
3. Die SQLite-Tabellenmigration erhält einen eindeutigen Namen (vorgeschlagen: `melonly_logs_sqlite_v1`) und wird über `pulse_migrations.run` ausgeführt. Die vorhandene DB-Sicherung und `schema_migrations` werden wiederverwendet.
4. Die Migration importiert jeden Eintrag positionsgetreu mit vollständiger Payload. Keine Bereinigung, Deduplizierung, Sortierung nach Datum oder Retention.
5. Vor dem Commit werden Zeilenanzahl, Reihenfolge/Sequenz und jedes dekodierte Payload-Objekt mit dem JSON-Original verglichen. Indexfelder werden gegen die Payload-Werte geprüft.
6. Ein vorhandenes, aber nicht registriertes Zielschema darf nicht blind geleert werden. Ist sein Inhalt nicht eindeutig leer oder identisch zum Quellstand, muss die Migration sicher abbrechen und einen manuellen Prüfhinweis liefern.
7. Nach dem Commit wird die Tabelle erneut über den späteren Lesepfad gelesen und fachlich verglichen. Erst ein gesonderter Start mit explizitem Backend-Schalter darf den Laufzeitpfad auf SQLite umstellen.
8. Bei jedem Fehler bleiben `logs.json` und alle bisherigen Einträge unverändert. Der Fehler wird protokolliert; ein nächster Versuch ist idempotent.

### 5.3 SQLite → JSON

Der Rückweg wird vor dem Umschalten vorbereitet, nicht durch einen stillen Fallback:

1. Unter demselben Schreibschutz werden alle SQLite-Datensätze in `seq`-Reihenfolge vollständig exportiert.
2. Die JSON-Datei wird in eine temporäre Datei im selben Dateisystem geschrieben, geflusht und atomar mit `os.replace` aktiviert.
3. Die Datei wird erneut eingelesen; Anzahl, Reihenfolge und vollständige Objekte müssen dem SQLite-Stand entsprechen.
4. Erst nach erfolgreicher Prüfung wird beim nächsten kontrollierten Start JSON als Quelle gewählt.
5. Die SQLite-Tabelle bleibt zunächst erhalten; sie wird nicht automatisch gelöscht.
6. Die Metadaten werden erst nach erfolgreichem Export und Read-back auf `active_backend=json` umgestellt; die Generation und letzte SQLite-Revision bleiben für Nachweis und Diagnose erhalten.
7. Schlägt Export oder Prüfung fehl, bleibt SQLite die maßgebliche Quelle und der Prozess meldet den Fehler. Es wird nicht auf eine möglicherweise veraltete JSON-Sicherung zurückgeschaltet.

**Wichtig:** Im SQLite-Modus ist die ursprüngliche `logs.json` nur eine unveränderte Migrationssicherung und kein automatisch aktueller Lese-Fallback. Das muss im Systemstatus und in der Betriebsdokumentation eindeutig sichtbar sein.

## 6. Backup, Restore und Datenschutz

- Das bestehende Voll-Backup muss im SQLite-Modus `pulse.db` und die archivierte `logs.json` gemeinsam erfassen und im Manifest Backend, Generation und Revision aus `melonly_logs_meta` dokumentieren. Die Snapshot-Grenze muss gegen gleichzeitige Melonly-Schreiboperationen geschützt sein.
- Ein Backup weist im Manifest Größe und SHA-256 aus; `pulse.db` wird über SQLite Backup API und mit `PRAGMA integrity_check` geprüft.
- Der Restore validiert den aktiven Backend-Marker innerhalb von `pulse.db` und die dazugehörigen Archiv-Metadaten. Eine alte JSON-Migrationssicherung wird nie als aktuelle Datenquelle interpretiert, wenn der Marker SQLite ausweist.
- Vor Restore wird das aktuelle System gesichert; anschließend werden Wartungsmodus, Integritätskontrolle und Rückweg genutzt.
- Tests laufen ausschließlich mit synthetischen Daten in isolierten temporären Verzeichnissen.
- Keine Retention oder automatische Löschung wird in dieser Phase eingeführt. Die bestehende manuelle Löschfunktion bleibt die einzige fachliche Löschaktion.
- Keine Roblox-Namen, Moderationsgründe oder Discord-IDs in Debug-/Fehlerlogs ausgeben. Diagnosen enthalten technische IDs, Zähler und Fehlerklassen, keine Payload-Inhalte.

## 7. Verbindliche Regressionstest-Matrix für Phase 15.2

### Datenvertrag und Migration
- leere Liste, fehlende Datei, ungültiges JSON, falsche Top-Level-Struktur;
- alle bekannten Felder, optionale Felder, zusätzliche unbekannte Felder und historische Datumsformate;
- doppelte IDs, fehlende IDs, leere/null-Werte und unerwartete Aktionstypen;
- exakte Anzahl, Sequenz, Reihenfolge und Payload-Gleichheit nach Import und Rückweg;
- wiederholte Migration (idempotent), Schema bereits vorhanden, Schema mit fremden/abweichenden Daten;
- Abbruch vor Commit, während Insert, vor Commit-Prüfung und nach Commit vor Backend-Aktivierung;
- JSON-Quelldatei und ihre SHA-256 bleiben bei fehlgeschlagener Migration unverändert;
- Backup vor Migration vorhanden und lesbar.

### CRUD und Parallelität
- Create → Read → Edit → Read → Delete → Read Roundtrip;
- gleichzeitige Append-Aufrufe erzeugen keine verlorenen oder doppelten Datensätze;
- parallele Bearbeitung/Löschung desselben Datensatzes folgt einem dokumentierten Ergebnis;
- Commit-Fehler und Prozessabbruch hinterlassen keinen halben Zustand;
- dieselbe ID mit mehreren Datensätzen verändert oder löscht nur den Datensatz, den die bisherige Implementierung laut Regressionstest adressiert.

### Ergebnisgleichheit
- Melonly: neueste 50, Zähler und Spieler-Verlauf;
- Spielerhistorie: numerische ID, exakter Name, Teilstring-Fallback und mehrere zusammengeführte Treffer;
- Roblox-Vorschläge: letzte 5.000, Reihenfolge und Kandidatenranking identisch;
- API-Summaries und globale Suche: identische Treffer mit derselben Rechteprüfung;
- CSV: identische Header, Spalten, Werte und Reihenfolge;
- Dashboard-Gesamtzähler identisch zu JSON.

### Performance und Betrieb
- 20.000 und 100.000 synthetische Einträge, mehrere Messläufe, Median und Spitzen-RAM;
- `EXPLAIN QUERY PLAN` belegt die Nutzung der vorgesehenen Indizes;
- Ergebnisgleichheit wird vor dem Performancevergleich geprüft;
- Voll-Backup, Restore-Vorschau, Restore, erneuter Integritätscheck und Rückweg;
- kein Test greift auf VPS-Daten, echtes Discord oder produktive Roblox-Endpunkte zu.

## 8. Abnahme und nächste Implementierungsstufe

Phase 15.1 ist erst abgeschlossen, wenn:
- dieses Schema gegen reale historische Payload-Formen aus dem versionierten Code/Testmaterial geprüft ist;
- die bestehenden ID- und Player-History-Semantiken durch Verhaltenstests festgehalten sind;
- der Backup-/Restore-Vertrag einschließlich SQLite-/JSON-Konsistenz geklärt ist;
- ein unabhängiges Review keine ungeklärte Datenverlust-, Datenschutz- oder Kompatibilitätslücke findet.

**Nächster Schritt (Phase 15.2):** Repository-Modul, Migration, kontrollierter Backend-Adapter und Regressionstests implementieren. Die Produktionsumschaltung bleibt ein späterer, separat freizugebender Betriebsschritt.

---

## Quellen im Repository

- `docs/PHASE15_0_LOGS_INVENTUR.md` – aktive Leser/Schreiber, Datenvertrag, Backup, Retention und Befunde.
- `pulse_shift_history.py` und `docs/PHASE14_7_BERICHT.md` – wiederverwendbares Migrations-/Rollback-Muster.
- `pulse_migrations.py` und `tests/test_migrations.py` – Transaktionen, Backup vor Schemaänderung und Idempotenz.
- `pulse_backup.py` und `tests/test_full_backup.py` – Voll-Backup und Restore.
- `docs/ROUTEN.md` / `docs/routen_snapshot.json` – aktive Routen und Handler.
- PR #119 – zentrale Melonly-Leserechte.
