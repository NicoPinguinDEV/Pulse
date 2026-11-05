# Phase 15.6 – Bewerbungsdaten: Versionierung, Referenzen und Migration

**Status (05.11.2026):** 15.6a (PR #136), 15.6b (PR #140) und 15.6c (PR #143) abgeschlossen · 15.6d: Snapshot-Vertrag aus PR #144 gemergt; JSON-Repository-Adapter in Feature-PR in Prüfung · keine Produktivmigration  
**Codebasis der Read-only-Inventur 15.6a:** `main` @ `e7311e7b9bb3fb90706629eaf0fabe2086e4782d` (nach PR #136)  
**Aktueller main-Stand bei Branch-Erstellung:** `149b9ad60a6a827bfcdc92d5b57d9829ddc9afc3` (nach PR #144)  
**Datum:** 05.11.2026  
**Nachverfolgung:** [Issue #134](https://github.com/NicoPinguinDEV/Pulse/issues/134)

> Diese Phase ändert noch kein aktives Bewerbungs-Backend. Die vorhandenen JSON- und SQLite-Daten bleiben die maßgeblichen Quellen. Alle Inventurtests verwenden ausschließlich synthetische Fixtures oder ausdrücklich angegebene Exporte.

## Ergänzung: Phase 15.6b

Der Codeabgleich zu Statuswerten, Retention, Einstellungsdatum, Interview-/Kalenderreferenzen, Datenschutzexport und Backup-Grenzen ist in [PHASE15_6B_APPLICATIONS_RETENTION_MATRIX.md](PHASE15_6B_APPLICATIONS_RETENTION_MATRIX.md) dokumentiert. Die technische Review-Korrektur wurde mit PR #140 gemergt. PR #143 ergänzt inzwischen den minimierten Beschäftigungsnachweis in der Teamakte; die Matrix wurde entsprechend um die neue team_since_for-Priorität ergänzt. Offene fachliche Entscheidungen zu Status- und Aufbewahrungsregeln bleiben vor 15.6e/15.6f zu klären.

## 1. Verifizierter Ist-Zustand

Bewerbungen sind derzeit nicht auf eine einzelne Datei oder Datenbank beschränkt:

| Speicher | Inhalt / Referenz | Aktive Quelle |
|---|---|---|
| `applications.json` | Bewerberprofil, Antworten, Test-/Praxisdaten, Status, Reviews, Bearbeiter, Verlauf, Interview- und Reminder-Felder | Primäre Bewerbungsakte |
| `team_data.json` | Teamakte mit `team_since`, Laufbahn und übernommenen Test-Metadaten | Aktuelle Teamhistorie |
| `pulse_suite.db` | Tabelle `suite_interviews` mit `application_id`, Bewerber-ID/-Name, Scores, Empfehlung und Gesprächsnotizen | Zusätzliche Interviewbewertung |
| `pulse.db` | Tabelle `calendar_events` | Interviewtermine über `interview_event_id` |
| `audit_logs.json` | Einstellungs-, Review-, Termin- und Status-Audit | Audit-Nachweis |

Die Beziehungen sind derzeit überwiegend anwendungsseitig. `suite_interviews.application_id` hat keinen nachgewiesenen Fremdschlüssel auf die JSON-Datei; `applications.json` hält die IDs `interview_id` und `interview_event_id`.

### 1.1 Eintrittsdatum und Altbestand

`webserver.team_since_for(member, entry, apps)` liest seit PR #143 in dieser Reihenfolge:

1. Gültiges vorhandenes `team_data.json[discord_id].team_since`;
2. gültiges `employment_event.hired_at`, wenn die gespeicherte user_id zur Discord-Person passt;
3. bei fehlendem/unbrauchbarem Ereignis das älteste gültige `decided_at` eines `hired`-/`accepted`-Bewerbungsdatensatzes (Legacy-Fallback);
4. als letzten Fallback `member.joined_at`.

Der aktuelle Flow `decide_app → hire_after_interview` schreibt `team_data.json.team_since` und danach die Bewerbungsakte. Das belegt einen aktuellen unabhängigen Teamzeit-Wert für diesen Flow. Für Altbestand ist die Abdeckung ohne Produktionsinventur nicht bewiesen. Die Statuswerte `accepted` und `hired` dürfen deshalb nicht pauschal gelöscht oder gleichgesetzt werden.

### 1.2 Festgestellte Retention-Lücke

`pulse_retention.run()` entfernt abgelaufene Bewerbungs-Keys derzeit aus `applications.json` und bereinigt `audit_logs.json`. Der gezeigte Löschpfad entfernt nicht automatisch die strukturiert verknüpften Zeilen aus `pulse_suite.db.suite_interviews` oder die per `interview_event_id` referenzierten Zeilen aus `pulse.db.calendar_events`.

Damit kann eine abgelaufene Bewerbungsakte entfernt sein, während Interviewnotizen, Bewertungen oder ein Termin noch vorhanden sind. Das muss vor einer Aktivierung oder Ausweitung der Retention korrigiert und getestet werden. Es wird hier ausdrücklich **keine** automatische Löschung aktiviert.

## 2. Read-only-Inventur in diesem PR

Neues Werkzeug: `tools/application_data_audit.py`.

Es erwartet explizite Pfade zu `applications.json` und `team_data.json`; die Datenbanken können zusätzlich als Pfade übergeben werden. Es durchsucht keine Standard-/Produktivpfade und führt keine SQL-Schreiboperation aus.

Der Bericht enthält nur:
- Anzahl der Bewerbungsobjekte und feste Status-Buckets (nur bekannte Statusnamen; unbekannte/freie Werte werden zusammengefasst);
- Datensätze mit fehlender Nutzer-ID oder unbekanntem Status;
- `accepted`-/`hired`-Anzahl und nicht auflösbare Team-Eintrittszeitpunkte nach derselben `user_id`-, Status- und Fallback-Logik wie `team_since_for`;
- verwaiste, nicht zugeordnete, doppelte oder nicht passende Interview-Referenzen;
- fehlende Kalenderereignisse und Kalender-IDs, die von mehreren Bewerbungen geteilt werden;
- einen booleschen Prüfhinweis.

Nicht ausgegeben werden Namen, Discord-IDs, Bewerbungs-IDs, Antworten, Bewertungen, Notizen, Termin-IDs oder rohe JSON-Payloads. SQLite wird mit `mode=ro` geöffnet. Die Tests nutzen synthetische Daten und prüfen, dass Datenbank-Metadaten unverändert bleiben. Auch fehlerhafte Interviewzeilen ohne ID/Bewerbungsreferenz, fehlende erwartete Tabellen, ID-Primärschlüssel, doppelte Datenbank-IDs, mehrfach verwendete Kalenderreferenzen und das Schließen der SQLite-Verbindungen werden getestet.

Beispiel für einen lokalen Export-Ordner:

```bash
python tools/application_data_audit.py \
  --applications /path/to/export/applications.json \
  --team-data /path/to/export/team_data.json \
  --suite-db /path/to/export/pulse_suite.db \
  --pulse-db /path/to/export/pulse.db \
  --output /path/to/export/audit-report.json
```

Das Werkzeug verändert keine Eingabe-JSON- oder SQLite-Dateien. Der aggregierte Bericht wird ausschließlich in die ausdrücklich angegebene `--output`-Datei geschrieben; der Zielpfad muss neu sein und vorhandene Dateien werden nicht überschrieben. Die Ausgabedatei wird mit restriktiven Dateirechten (POSIX: `0600`) angelegt. Es erfolgt keine Berichtsausgabe auf Konsole oder in Logs. Ein erfolgreicher Exit-Code bedeutet nur, dass der Bericht lesbar erstellt wurde; `readiness.requires_review` bleibt bei offenen Referenzen oder fehlender Datenbankprüfung gesetzt. Beide Referenzdatenbanken müssen für einen vollständigen Audit explizit angegeben werden – auch wenn keine Bewerbungsreferenzen vorhanden sind, da sonst eigenständige verwaiste Datensätze unentdeckt bleiben können.

## 3. Zielarchitektur für die spätere Umsetzung

### 3.1 Rückwärtskompatibles Application-Repository

Vorgeschlagen wird ein internes Repository-Modul, z. B. `pulse_applications_store.py`, mit einer zunächst unveränderten fachlichen API. Bestehende URLs, Formularnamen, Statuswerte, Discord-IDs, Dashboard-Rechte und JSON-Backupform bleiben erhalten.

Das zukünftige SQLite-Modell in `pulse.db` soll mindestens enthalten:

```sql
CREATE TABLE IF NOT EXISTS application_records (
    application_id TEXT PRIMARY KEY,
    user_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_application_records_user
    ON application_records(user_id, application_id);

CREATE INDEX IF NOT EXISTS idx_application_records_status_updated
    ON application_records(status, updated_at);
```

`payload_json` hält den vollständigen Originaldatensatz einschließlich unbekannter Alt-Felder. Die abgeleiteten Spalten dienen nur Filtern und Indizes. Status- oder Datumswerte werden beim Import nicht still normalisiert. Die fachliche Antwort des Repositorys wird weiterhin als bisheriges Mapping `application_id → record` bereitgestellt.

Die Tabellenanlage allein aktiviert kein SQLite-Backend. Der aktive Backend-Marker, Generation, Revision, Import-SHA-256 und Zeilenanzahl werden innerhalb eines eigenen Migrationsprotokolls geführt. JSON bleibt die Standardquelle, bis Migration und Rückweg separat geprüft und freigegeben sind.

### 3.2 Minimiertes Einstellungsereignis

Erfolgreiche Bewerbungen benötigen einen kleinen, eigenständigen Nachweis, damit `Team seit` nicht von vollständigen Antworten oder Review-Notizen abhängt.

Vorgeschlagene Tabelle `application_employment_events`:
- `application_id` als eindeutige Quellreferenz;
- `user_id` als Discord-Zuordnung;
- `hired_at` als kanonischer Eintrittszeitpunkt;
- `career_path` und `career_label`, soweit für die Teamakte erforderlich;
- `created_by_id` und `audit_ref` für die notwendige Nachvollziehbarkeit;
- `created_at` und `schema_version`.

Keine Bewerbungsantworten, Motivation, Testdetails, Interview-Notizen oder Gesprächs-Payloads werden in dieses Ereignis kopiert. Die Tabelle erhält bewusst keinen löschenden Fremdschlüssel auf `application_records`: Ein später gelöschter Bewerbungsdatensatz darf das minimale Beschäftigungsereignis nicht löschen.

Für alte `accepted`-/`hired`-Fälle muss vor dem Backfill geprüft werden, ob sie tatsächlich eine Einstellung darstellen und welcher Zeitstempel fachlich gilt. Nicht eindeutig zuordenbare Fälle bleiben unverändert und werden zur manuellen Prüfung ausgewiesen.

### 3.3 Interview- und Terminreferenzen

Die erste Migration lässt `suite_interviews` in `pulse_suite.db` und `calendar_events` in `pulse.db`; ein Zusammenführen der Datenbanken ist nicht Teil von 15.6.

**Grenze der heutigen Kalenderdaten:** `calendar_events` besitzt keine strukturierte `application_id`. Der Audit kann deshalb nur noch vorhandene `interview_event_id`-Verweise prüfen. Bereits verwaiste Termine nach einer früheren Löschung der Bewerbungsakte lassen sich nicht sicher identifizieren. Titel- oder Freitextsuche ist ausdrücklich kein zulässiger Ersatz. Die spätere Retention muss die Event-ID vor dem Entfernen des Bewerbungsdatensatzes in einem Löschjournal sichern oder eine strukturierte Eigentümer-Referenz einführen.

Zu jedem noch vorhandenen Bewerbungsdatensatz werden die strukturierten Referenzen explizit verfolgt:
- `interview_id` ↔ `suite_interviews.id` und `suite_interviews.application_id`;
- `interview_event_id` ↔ `calendar_events.id`;
- Reminder-Felder innerhalb der Bewerbungsakte.

Die Retention-Bereinigung muss diese Datensätze berücksichtigen. Sie darf keine Kalenderereignisse anhand von Titel oder Freitext löschen. Nur eine eindeutig referenzierte Termin-ID darf in die Bereinigung einfließen. Falls mehrere Bewerbungen dieselbe Event-ID referenzieren, ist der Vorgang zu blockieren und manuell zu prüfen.

Da die drei Dateien/Datenbanken keine gemeinsame Transaktion besitzen, ist die Löschung über einen nachvollziehbaren, wiederholbaren Ablauf mit vollständigem Backup und Recovery-Status umzusetzen. Ein bloßes nacheinander ausgeführtes `DELETE` ohne Wiederherstellungsstrategie ist nicht akzeptabel.

## 4. Umsetzungsreihenfolge

| Teilphase | Umsetzung | Sperre / Sicherheitsbedingung |
|---|---|---|
| 15.6a | Read-only-Inventur und Ergebnisbericht | Nur explizite Exporte bzw. synthetische Fixtures |
| 15.6b | Migrations- und Retention-Plan pro Status/Feld/Referenz | Keine Löschung und keine Schemaumschaltung |
| 15.6c | Minimiertes Einstellungsereignis mit Legacy-Fallback | **Mit PR #143 gemergt.** Vier bestätigte Schreibpfade; keine automatische Altbestandsbefüllung oder Migration. |
| 15.6d | Versioniertes Repository und JSON-Import | Begonnen mit einem reinen Import-Snapshot-Vertrag; vollständiger Adapter, Snapshot-Persistenz und Import/Restore bleiben offen. JSON-Byte-Parität, SHA-256, Schema-Version, Generation/Revision und Konflikttests sind Abnahmepunkte. |
| 15.6e | Referenzsichere Retention und Löschung | Vollbackup, Dry-Run, idempotenter Ablauf und Recovery-Test |
| 15.6f | Produktive Migration als separater Betriebsschritt | Ausdrückliche Freigabe, geprüftes Backup und Rückweg |

### 4.1 Abnahme von Phase 15.6c

PR #143 implementiert den minimierten employment_event-Nachweis in der bestehenden Teamakte. Die Schreibpfade sind: Bewerbung nach verpflichtendem Gespräch, manuelle Einstellung, erstmaliger Teamrolleneintritt über die Teamrollenverwaltung und Discord-Erkennung. team_since_for priorisiert team_since, danach das passende Ereignis, dann den Legacy-Fallback für hired/accepted und zuletzt Discord joined_at. Das bloße Setzen von accepted in der Suite erzeugt kein Ereignis. Es gibt keinen automatischen Backfill alter Fälle.

### 4.2 Abgrenzung des ersten 15.6d-Bausteins

pulse_application_import.py ist ein reiner, nebenwirkungsfreier Vorabvertrag für einen späteren Import. Er nimmt einen explizit bereitgestellten JSON-Byte-Stream entgegen, prüft UTF-8 und ein JSON-Top-Level-Objekt, lehnt doppelte Schlüssel und NaN/Infinity ab und hält SHA-256, Record-Anzahl, Schema-Version, Generation und Revision fest. Der ursprüngliche Byte-Stream bleibt die Exportquelle; unbekannte Alt-Felder, Werte, Formatierung und Reihenfolge werden nicht neu serialisiert.

Der Snapshot-Vertrag bleibt eine reine Validierungs- und Byte-Paritätskomponente; er erstellt selbst keine Dateien/Tabellen und führt keinen Import aus. Der JSON-Repository-Adapter wird separat mit PR #145 integriert. Ein reproduzierbarer Import-/Snapshot-/Rollback-Ablauf und jede produktive Backend-Umschaltung benötigen weiterhin eigene Tests und Freigaben.

### 4.3 JSON-Repository-Adapter (Feature-Branch)

Der Branch `phase/15-6d-json-repository-adapter` ergänzt `pulse_applications_repository.py`. Der Adapter kapselt die bestehende Mapping-API und delegiert Dateizugriffe an die vorhandenen JSON-Sicherheitshelfer. `webserver.load_json` und `webserver.save_json` leiten Zugriffe auf `APPS_FILE` verzögert an ihn weiter; alle anderen JSON-Dateien bleiben unverändert. `mutate()` bietet eine gemeinsame synchrone Read-Modify-Write-Grenze unter demselben `_io_lock`. Snapshot und Importvorschau bleiben reine Validierungs-/Exportoperationen und schreiben nichts.

Der Adapter ist im Feature-Branch noch nicht gemergt und migriert nicht automatisch alle einzelnen Schreibabläufe auf `mutate()`. Persistente Snapshots, Importausführung, Rollback, SQLite und Produktionsmigration sind weiterhin offen. Der aktuelle PR ist damit ein Adapter-Schritt innerhalb von 15.6d, kein Abschluss der gesamten Teilphase.

## 5. Abnahmekriterien

- [ ] Datenlandkarte deckt alle Leser, Schreiber, Statusübergänge und Referenzen ab.
- [ ] Der Audit-Bericht gibt keine Bewerber-/Interview-Personendaten aus.
- [ ] Altbestand `accepted` und `hired` wird nicht ohne Beweis gleichgesetzt.
- [ ] `Team seit` bleibt für neue und alte Teammitglieder fachlich gleich.
- [ ] Jede `suite_interviews`- und Kalenderreferenz ist eindeutig zuordenbar.
- [ ] Retention-Dry-Run zählt Bewerbungsakten, Interviewnotizen und zugehörige Termine getrennt.
- [ ] Löschen ist idempotent und bei Teilfehlern aus dem Vollbackup wiederherstellbar.
- [ ] JSON-/SQLite-Import und Rückexport erhalten unbekannte Felder, Reihenfolge und Werte.
- [ ] Rechteprüfung, Datenschutzexport, Audit und Backup-/Restore-Tests bestehen.
- [ ] Retention bleibt standardmäßig deaktiviert; keine Produktionsmigration oder Backend-Umschaltung ohne eigene Freigabe.

## 6. Bewusst nicht Teil dieses PRs

- keine Änderung an `applications.json`, `team_data.json`, `pulse.db` oder `pulse_suite.db`;
- keine Änderung an `team_since_for`, Bewerbungsformularen oder Statusübergängen;
- keine automatische Retention, keine Löschung, keine Anonymisierung;
- keine produktive Dateninventur und kein Zugriff auf VPS-Daten;
- keine Änderung an Bewerbungsrechten oder Datenschutzhinweisen;
- kein Merge und kein Deployment.
