# Phase 15.6f – Vorbereitung der kontrollierten SQLite-Migration

**Status:** erster isolierter Staging-/Paritätsbaustein implementiert; PR/CI/Review ausstehend; keine Produktivmigration  
**Aktueller main-Basisstand:** `2f57f717c5a219c50685ab8a94837fb684c5249e` (Merge PR #148)  
**Voraussetzung:** Phase 15.6e ist gemergt; weitere fachliche und technische Freigaben bleiben für Cutover erforderlich  
**Sicherheitsgrenze:** Kein Produktivdatenzugriff, kein Backfill, keine Backend-Umschaltung, keine Produktionsdatei-/Datenbankänderung, kein VPS und kein Discord.

## 1. Zweck und Ausgangspunkt

Die Bewerbungsakte bleibt bis zu einer separat freigegebenen Migration in `applications.json`. Der JSON-Repository-Adapter und der persistente Snapshot-Manager aus Phase 15.6d ändern diese Quelle noch nicht.

Die vollständigen Originalbytes können mit `PersistentApplicationSnapshotManager` in den bereits durch `pulse_backup.py` gesicherten `pulse.db`-Snapshot-/Journal-Tabellen festgehalten werden. Wichtig: Generation/Revision zählen nur kontrollierte Austauschoperationen, keine normalen Legacy-JSON-Writes. Deshalb muss jeder Migrations-Dry-Run die aktuelle Datei erneut hashen und compare-and-swap verwenden.

Die erste SQLite-Migration soll **nur die Bewerbungsakte** betreffen. `suite_interviews` bleibt zunächst in `pulse_suite.db`, `calendar_events` bleibt in `pulse.db`, und `team_data.json` bleibt die bestehende Teamakte. Eine Datenbankzusammenführung ist ausdrücklich kein Bestandteil von 15.6f.

## 2. Zielschema – Entwurf, noch nicht angelegt

Das bereits im älteren Entwurf definierte Prinzip bleibt sinnvoll: vollständiger Anwendungsdatensatz bleibt als JSON-Payload erhalten; nur häufig benötigte Felder werden zusätzlich projiziert und indexiert.

Vorgeschlagenes Schema (im isolierten Staging-Baustein umgesetzt; nur in einer neu angelegten Staging-Datei):

```sql
CREATE TABLE application_records (
    application_id TEXT PRIMARY KEY,
    user_id TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX idx_application_records_user
    ON application_records(user_id, application_id);

CREATE INDEX idx_application_records_status_updated
    ON application_records(status, updated_at);
```

Regeln:
- `payload_json` enthält jeden Datensatz inklusive unbekannter Alt-Felder.
- Projektionen sind Such-/Indexfelder; sie dürfen den Payload nicht ersetzen.
- Statuswerte werden byte-/wertgetreu übernommen und nicht normalisiert.
- Der Repository-Vertrag liefert weiterhin das bestehende Mapping `application_id → record`.
- URLs, Formfelder, `custom_id`, Rechte, Discord-IDs, Formularantworten und Dashboard-Darstellungen bleiben kompatibel.
- Snapshot-/Journal-Metadaten und Migrationsstatus müssen getrennt vom eigentlichen Bewerbungs-Payload geführt werden.

Die Tabellen werden ausschließlich durch den expliziten Staging-Aufruf in einer neu angelegten Datei erstellt. Sie werden weder in `pulse.db` noch in `pulse_suite.db` angelegt.

## 3. Migrationsablauf – verpflichtender späterer Vertrag

### 3.1 Read-only-Vorabprüfung

1. Explizit vorhandene `applications.json` lesen und exakte Bytes hashen.
2. Strikte UTF-8-/JSON-/Mappingprüfung aus Phase 15.6d wiederverwenden.
3. Dubletten, ungültige Datensätze, fehlende IDs und nicht unterstützte Formen als Fehler ausweisen.
4. Quellanzahl, Statusverteilung und Projektionsfelder nur aggregiert dokumentieren.
5. Verknüpfungen zu `suite_interviews`, `calendar_events`, `suite_audit`, `audit_logs.json` und `team_data.json.employment_event` prüfen.
6. Ein gültiges Full-Backup und einen persistenten Vorher-Snapshot verlangen.

Ein Dry-Run darf weder das Zielschema in der aktiven Datenbank anlegen noch die JSON-Quelle verändern.

### 3.2 Isolierter Staging-Import

- Import zunächst in eine neue, temporäre SQLite-Datei außerhalb des Runtime-Datenverzeichnisses.
- Eine Transaktion für Tabellenanlage und den vollständigen Datensatzimport.
- Jede `application_id` genau einmal; Duplikate führen zum Abbruch.
- `payload_json` enthält die vollständige Bewerbungsakte; unbekannte Felder bleiben erhalten.
- Abgeleitete Indexspalten werden aus demselben Payload gebildet und separat gegen den Payload geprüft.
- SQLite-Integrität und erwartete Schema-Version prüfen.
- Bei jedem Fehler temporäre Datenbank verwerfen; die Quell-JSON bleibt unverändert.

### 3.3 Paritätsnachweis

Der Import darf nur als paritätsgleich gelten, wenn mindestens alle folgenden Prüfungen bestanden sind:

- identische Menge der Application-IDs;
- identische Anzahl Datensätze;
- identische Statuswerte, einschließlich unbekannter und historischer Werte;
- strukturelle Gleichheit jedes vollständigen Records inklusive unbekannter Felder;
- identische Werte der projizierten Spalten im Vergleich zum Payload;
- keine doppelten IDs und keine fehlenden Payloads;
- Referenzaudit ohne neue verwaiste Interviews oder Kalenderverweise;
- SHA-256 des unveränderten JSON-Snapshot weiterhin nachprüfbar.

Die exakten ursprünglichen JSON-Bytes werden nicht aus den SQLite-Zeilen rekonstruiert. Der persistente Vorher-Snapshot bewahrt Formatierung, Schlüsselreihenfolge und Byte-Parität für einen exakten Rückweg.

### 3.4 Separate spätere Cutover-Freigabe

Ein Produktions-Cutover ist kein Bestandteil dieser Vorbereitung. Er erfordert einen eigenen PR und eine ausdrückliche Betriebsfreigabe. Mindestablauf:

1. Vollständiges Backup erstellen und Manifest/Restore-Test prüfen.
2. Wartungsfenster und Single-Writer-Grenze sicherstellen.
3. JSON-Datei erneut hashen; Dry-Run-Generation/Revision prüfen.
4. Persistenten Vorher-Snapshot und Journal-Eintrag sichern.
5. Staging-Paritätsnachweis wiederholen.
6. Backend-Marker in einer gesonderten, atomaren Operation umschalten.
7. Read-/Write-Smokes, Regressionen, Team-/Recruiting-Workflows und Audit prüfen.
8. Bei Fehlern Backend-Marker zurücksetzen und Original-JSON-Snapshot wiederherstellen.
9. Erst nach dokumentierter Nachkontrolle den Wartungsmodus beenden.

Kein Dual-Write ohne separat bewiesene Konsistenzstrategie. Kein automatischer Cutover beim Bot-Start.

## 4. Rückfall- und Fehlerregeln

- Die JSON-Quelle bleibt vor erfolgreicher Freigabe autoritativ.
- Ein Dry-Run mit veraltetem SHA, Generation oder Revision ist ungültig.
- Ein Fehler oder Prozessabbruch im Staging-Import lässt die Quelle unverändert.
- Bei einem nicht eindeutig zuordenbaren Zustand keine automatische Überschreibung.
- Backups und persistente Snapshots werden nicht im selben Schritt wie ein Backend-Cutover gelöscht.
- `employment_event` bleibt unabhängig von der vollständigen Bewerbungsakte erhalten; kein Backfill aus `accepted` ohne manuelle fachliche Prüfung.
- Interviews, Kalendertermine und Audit-Ketten werden in 15.6f nicht automatisch verschoben, bereinigt oder zusammengeführt.

## 5. Voraussetzungen aus Phase 15.6e

Vor einer Migrationsfreigabe müssen erfüllt sein:

- Datenschutzexport ordnet Interviewdaten, Kalenderreferenzen und explizite Audit-Refs nachvollziehbar zu.
- Historische Audit-Freitextgrenzen sind dokumentiert und für neue Schreibpfade ist eine strukturierte Subjekt-/Bewerbungsreferenz beschlossen.
- Statussemantik für `in_review`, `accepted`, `hired` und unbekannte Werte ist fachlich freigegeben.
- Retention bleibt fail-closed, bis ein vollständiger Mehrspeicher-Apply-/Recovery-Ablauf vorliegt.
- Synthetische Backup-/Restore-Probe deckt `applications.json`, `team_data.json`, `pulse_suite.db`, `pulse.db` und `audit_logs.json` gemeinsam ab.
- Tests für JSON-/SQLite-Parität, Crash-Recovery, veraltete Vorschau und Rückweg laufen in CI grün.

## 6. Nicht-Ziele

- keine produktive Migration oder Backend-Auswahl;
- keine automatische Schemaanlage beim Start;
- keine automatische Retention, Löschung oder Anonymisierung;
- keine Änderung der Bewerbungsformulare, URLs, custom_id oder Rechte;
- keine Zusammenführung von `pulse.db` und `pulse_suite.db`;
- kein VPS-Deployment und keine Discord-Aktion.

## 7. Erste technische Umsetzung – isolierter Staging-Import

Auf `phase/15-6f-isolated-staging-parity` wird der Staging-Vertrag durch `pulse_application_staging.py` implementiert. Die Implementierung ist ein Offline-Werkzeug und wird nicht vom Bot-Start, vom Dashboard oder von einem API-Endpunkt aufgerufen.

- Eingang ist ausschließlich ein bereits verifizierter `ApplicationImportSnapshot`; das Werkzeug liest keine Quelldatei und ermittelt keinen Runtime-Pfad.
- Ziel muss explizit angegeben sein, das Zielverzeichnis muss bereits existieren und der Dateiname muss auf `.staging.sqlite3` enden. Existierende Dateien werden mit exklusiver Dateierstellung abgelehnt und niemals überschrieben.
- Die Datei wird mit privaten Rechten (POSIX `0600`), SQLite-Rollback-Journal und `synchronous=FULL` angelegt.
- Tabellenanlage, Metadaten und Datensätze laufen in einer einzigen SQLite-Transaktion. Ein Fehler entfernt nur die durch diesen Aufruf neu erzeugte Staging-Datei samt SQLite-Nebendateien.
- `application_records.payload_json` bewahrt den kompletten Datensatz einschließlich unbekannter Legacy-Felder. Unbekannte Statuswerte bleiben unverändert. Fehlende oder nicht als Text darstellbare optionale Projektionsfelder werden als `NULL` abgebildet; der Originalwert bleibt im Payload.
- `verify_application_staging_database()` öffnet die Stage-Datei ausschließlich mit `mode=ro` und `query_only`. Es vergleicht IDs, vollständige kanonische Payloads, projizierte Felder, Quell-SHA-256, Generation/Revision, Schema-Version und `PRAGMA integrity_check`.
- Der Bericht enthält ausschließlich aggregierte Werte und Prüfflags, keine Bewerbungs-IDs, Discord-IDs, Namen, Antworten oder Notizen.

Neue synthetische Tests in `tests/test_application_staging.py` prüfen Payload-Parität, Legacy-/unbekannte Statuswerte, Dateirechte, Read-only-Verifikation, beschädigte Payloads, ungültige Datensätze, Zielpfadschutz, Nichtüberschreiben und Fehlerbereinigung.

Dieser Baustein beweist noch keinen Cross-Store-Referenzabgleich, keinen vollständigen Backup-/Restore-Rehearsal und keinen Produktiv-Cutover. Diese Nachweise bleiben vor einer separaten Freigabe verpflichtend.

Phase 15.6f gilt erst als abgeschlossen, wenn diese Vorbereitung unabhängig reviewed wurde und ein gesonderter, ausdrücklich autorisierter Cutover-PR alle Staging-, Paritäts-, Backup- und Rollback-Gates bestanden hat.
