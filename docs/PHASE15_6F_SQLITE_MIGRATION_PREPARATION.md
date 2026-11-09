# Phase 15.6f – Vorbereitung der kontrollierten SQLite-Migration

**Status (09.11.2026):** PR #149 (isoliertes SQLite-Staging) und PR #153 (Härtung des Schema-Vertrags) sind gemergt; die zugehörigen Quality-/Security-CI-Läufe waren erfolgreich. Die aktuelle Nachprüfung ergänzt explizites `NOT NULL` für `application_id` samt Regressionstest; deren CI steht noch aus. Für #149/#153 ist kein unabhängiges GitHub-Review dokumentiert. Phase 15.6f bleibt bis zur grünen CI des aktuellen Branches und einem unabhängigen Review offen. Keine Produktivmigration.  
**Prüf-/Branch-Basis:** `main` @ `fc246984c12bfd99b1bf38904833f6f616bbb8ca` (Merge PR #173; 07.11.2026)  
**Sicherheitsgrenze:** Kein Zugriff auf Produktionsbewerbungen oder VPS-Daten, kein Backfill, keine Backend-Umschaltung, keine Produktionsdatei-/Datenbankänderung, kein Deployment und keine Discord-Aktion.

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
    record_schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX idx_application_records_user
    ON application_records(user_id, application_id);

CREATE INDEX idx_application_records_status_updated
    ON application_records(status, updated_at);

CREATE TABLE application_staging_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_generation INTEGER NOT NULL,
    source_revision INTEGER NOT NULL,
    source_record_count INTEGER NOT NULL
);
```

Die Staging-Implementierung hält den globalen Migrations-/Quell-Snapshot-Stand in `application_staging_meta`. `record_schema_version` markiert die Projektion jeder Zeile. Ein fehlender oder nicht-textueller Status bleibt im Payload erhalten und wird in der optionalen Indexspalte als `NULL` abgebildet.

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

Der Staging-Vertrag wurde mit [PR #149](https://github.com/NicoPinguinDEV/Pulse/pull/149) in `pulse_application_staging.py` implementiert und mit [PR #153](https://github.com/NicoPinguinDEV/Pulse/pull/153) gegen abgeschwächte SQLite-DDL-Verträge gehärtet. Die Implementierung ist ein Offline-Werkzeug. Eine Code-Suche auf dem Prüfstand `fc246984c12bfd99b1bf38904833f6f616bbb8ca` findet die Erzeugungs-/Verifikationsfunktionen nur im Staging-Modul und in dessen Tests; ein Bot-, Dashboard- oder API-Aufrufer wurde nicht gefunden.

- Eingang ist ausschließlich ein bereits verifizierter `ApplicationImportSnapshot`; das Werkzeug liest keine Quelldatei und ermittelt keinen Runtime-Pfad.
- Ziel muss explizit angegeben sein, das Zielverzeichnis muss bereits existieren und der Dateiname muss auf `.staging.sqlite3` enden. Existierende Dateien werden mit exklusiver Dateierstellung abgelehnt und niemals überschrieben.
- Die Datei wird mit privaten Rechten (POSIX `0600`), SQLite-Rollback-Journal und `synchronous=FULL` angelegt.
- Tabellenanlage, Metadaten und Datensätze laufen in einer einzigen SQLite-Transaktion. Ein Fehler entfernt nur die durch diesen Aufruf neu erzeugte Staging-Datei samt SQLite-Nebendateien.
- `application_records.payload_json` bewahrt den kompletten Datensatz einschließlich unbekannter Legacy-Felder. Unbekannte Statuswerte bleiben unverändert. Fehlende oder nicht als Text darstellbare optionale Projektionsfelder werden als `NULL` abgebildet; der Originalwert bleibt im Payload.
- `verify_application_staging_database()` öffnet die Stage-Datei ausschließlich mit `mode=ro` und `query_only`. Es vergleicht IDs, vollständige kanonische Payloads, projizierte Felder, Quell-SHA-256, Generation/Revision, die erwarteten Tabellen-/Indexnamen und -spalten, Schema-Version und `PRAGMA integrity_check`.
- Der Bericht enthält ausschließlich aggregierte Werte und Prüfflags, keine Bewerbungs-IDs, Discord-IDs, Namen, Antworten oder Notizen.

Das Modul und `tests/test_application_staging.py` sind ausdrücklich in `ruff.toml` aufgenommen. Der Testbestand umfasst auf dem Ausgangsstand 14 gezielte Staging-Testmethoden; der aktuelle Review-Branch ergänzt eine 15. Regression. Geprüft werden Payload-/ID-Parität, unbekannte Statuswerte und Felder, Metadaten, vollständige DDL-/Index-Verträge, POSIX-Dateirechte, Read-only-Verifikation, beschädigte Payloads, ungültige Datensätze, Zielpfadschutz, Nichtüberschreiben und Fehlerbereinigung. Die neue Regression prüft zusätzlich, dass SQLite einen NULL-Wert für `application_id` ablehnt.

Dieser Baustein beweist noch keinen Cross-Store-Referenzabgleich, keinen vollständigen Backup-/Restore-Rehearsal und keinen Produktiv-Cutover. Diese Nachweise bleiben vor einer separaten Freigabe verpflichtend.

Die Staging-Vorbereitung kann nach grüner CI des aktuellen Härtungs-Branches und dokumentiertem unabhängigem Review abgeschlossen werden. Ein produktiver Cutover ist ausdrücklich nicht Teil dieser Vorbereitung: Er benötigt später einen eigenen, ausdrücklich autorisierten PR mit vollständigen Backup-, Cross-Store-Paritäts-, Wartungsfenster-, Read-/Write-Smoke- und Rollback-Nachweisen. Bis dahin bleibt `applications.json` die maßgebliche Quelle; kein Dual-Write, keine Retention-Mutation und keine Backend-Umschaltung.

## 8. Nachprüfung am 09.11.2026

### Repository- und Implementierungsstand

- Geprüfter `main`-HEAD: `fc246984c12bfd99b1bf38904833f6f616bbb8ca` (Merge PR #173).
- [PR #149](https://github.com/NicoPinguinDEV/Pulse/pull/149) wurde am 05.11.2026 gemergt; Merge-Commit `9fc88e9262dd96a7f930796f189d1aab2212908f`.
- [PR #153](https://github.com/NicoPinguinDEV/Pulse/pull/153) wurde am 05.11.2026 gemergt; Merge-Commit `4d0b161464752d7c2a37261b3a8f37df6e6667b8`.
- `pulse_application_import.py` validiert explizite UTF-8-JSON-Bytes, doppelte Schlüssel, Schema-Version, Zähler, SHA-256 und Record-Anzahl. Der unveränderte Original-Byte-Stream bleibt im Snapshot erhalten.
- `pulse_application_staging.py` akzeptiert ausschließlich einen verifizierten Snapshot und einen expliziten neuen Zielpfad mit Endung `.staging.sqlite3`. Es liest keine Runtime-Datei, wählt kein Backend und wird nicht durch Bot, Dashboard oder API aufgerufen.
- Die Stage-Datei wird exklusiv erstellt; vorhandene Ziele werden abgewiesen. POSIX-Dateirechte sind `0600`; SQLite verwendet Rollback-Journal und `synchronous=FULL`. Schema, Metadaten und Records entstehen transaktional. Die Prüfung öffnet SQLite mit `mode=ro` und `query_only`.
- Vollständige Payloads bewahren unbekannte Bewerbungsfelder und Statuswerte. Projektionen werden nur für Textwerte gebildet. Der Prüfbericht enthält ausschließlich aggregierte Prüfergebnisse.
- Die Code-Suche nach `create_application_staging_database`, `verify_application_staging_database` und `pulse_application_staging` fand keinen produktiven Aufrufer. `JsonApplicationRepository` bleibt der aktive JSON-Adapter; das Staging-Modul ist kein Runtime-Backend.

### Gefundene Schema-Lücke und Korrektur auf dem Review-Branch

SQLite erzwingt bei einer gewöhnlichen Rowid-Tabelle für eine Nicht-`INTEGER`-Primärschlüsselspalte nicht zuverlässig automatisch `NOT NULL`. Die bisherige Definition `application_id TEXT PRIMARY KEY` ließ daher eine nicht-nullbare ID-Einschränkung unklar. Im aktuellen Branch `phase/15-6f-finalize-staging-review` wurde die Lücke gezielt geschlossen:

- DDL: `application_id TEXT PRIMARY KEY NOT NULL`.
- Der erwartete vollständige DDL-Vertrag in `verify_application_staging_database()` verlangt dieselbe Definition.
- Neue Regression `test_staging_schema_rejects_null_application_ids` prüft `PRAGMA table_info` und versucht zusätzlich einen echten NULL-INSERT, der mit `sqlite3.IntegrityError` scheitern muss.
- Diese Änderung ist noch nicht durch GitHub Actions validiert. Dafür darf noch kein Testerfolg behauptet werden.

### Verifizierte CI-Nachweise der gemergten Vorarbeiten

| PR | Workflow / Umgebung | Ergebnis |
|---|---|---|
| #149 | Pulse Quality and Security Checks, Python 3.14 | success; 1.541 Tests, 18 übersprungen ([Run 41947029947](https://github.com/NicoPinguinDEV/Pulse/actions/runs/41947029947)) |
| #149 | Pulse Sicherheit & Abdeckung | success ([Run 41947030006](https://github.com/NicoPinguinDEV/Pulse/actions/runs/41947030006)) |
| #153 | Pulse Quality and Security Checks, Python 3.14 | success; 1.543 Tests, 18 übersprungen ([Run 41995477077](https://github.com/NicoPinguinDEV/Pulse/actions/runs/41995477077)) |
| #153 | Regressionstests mit Image-Python 3.15 | success; 1.543 Tests, 19 übersprungen (Job im Run 41995477077) |
| #153 | Pulse Sicherheit & Abdeckung | success ([Run 41995477207](https://github.com/NicoPinguinDEV/Pulse/actions/runs/41995477207)) |

Die erforderlichen Syntax-, Feature-Smoke-, vollständigen Unittest-, Docker-, Compose-, Secret-Scan- und Dashboard-E2E-Jobs waren in den genannten Quality-Runs erfolgreich. Der optionale 20K-Schichthistorien-Benchmark wurde aufgrund seines Workflow-Pfadfilters übersprungen; er ist kein Staging-Test. Die CI-Läufe beziehen sich auf die jeweiligen PR-Heads vor dem aktuellen `NOT NULL`-Fix.

Für PR #149 und #153 liegen in GitHub keine eingereichten Review-Datensätze vor. Sichtbare Graphify-Kommentare melden lediglich, dass automatische Reviews pausiert waren. Grüne CI ersetzt kein unabhängiges Review.

### Noch offene Abnahmepunkte

1. Grüne CI für den aktuellen Review-Branch inklusive der neuen NULL-Primärschlüssel-Regression.
2. Dokumentiertes unabhängiges Review des aktuellen Staging-DDL, der Verifikation und der Migrationsgrenzen.
3. Vollständiger Cross-Store-Referenznachweis über Bewerbungsakten, Interviewdaten in `pulse_suite.db`, Kalenderereignisse in `pulse.db`, Team-/Employment-Events und Audit-Verknüpfungen.
4. Gemeinsamer synthetischer Backup-/Restore-Rehearsal für `applications.json`, `team_data.json`, `pulse_suite.db`, `pulse.db` und `audit_logs.json`. Der Melonly-100K-Rehearsal aus Phase 15.5 ist kein Nachweis für diesen Bewerbungs-Mehrspeicher-Verbund.
5. Fachliche Status-/Retention-Freigabe sowie sichere Behandlung historischer Audit-Freitexte, nicht vollständig strukturierter DMs und Anhänge.

`RETENTION_MUTATIONS_ENABLED` bleibt `False`. Phase 15.6f ist keine Produktionsmigration. Issue [#134](https://github.com/NicoPinguinDEV/Pulse/issues/134) bleibt offen und wird nicht durch diesen Dokumentationsabschluss geschlossen.
