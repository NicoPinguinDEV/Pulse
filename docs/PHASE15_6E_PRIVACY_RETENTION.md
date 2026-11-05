# Phase 15.6e – Bewerbungsdaten, Datenschutzexport und sichere Retention

**Status:** Implementierung auf Feature-Branch `phase/15-6e-privacy-retention-audit`; GitHub-CI und Review stehen aus  
**Basis:** `main` @ `385e235978f976df361ecc75bb1597e6995684e4` (Merge PR #147)  
**Sicherheitsgrenze:** Nur Quellcode, synthetische Fixtures und temporäre Dateien/Datenbanken. Keine Produktionsdaten, keine echten Exporte, keine Löschung, keine Anonymisierung, keine Migration, kein VPS und kein Discord.

## 1. Ziel und Statusentscheidung

Phase 15.6e schließt zuerst die belegbaren Datenschutz-/Referenzlücken, ohne eine irreversible Mehrspeicher-Bereinigung zu aktivieren.

| Status | Behandlung in Phase 15.6e | Grund |
|---|---|---|
| `pending` | geschützt | Vorgang offen |
| `in_review` | geschützt | wird von Recruiting als laufende Prüfung verwendet; nicht als erledigt behandeln |
| `interview` | geschützt | Gesprächs-/Terminprozess kann noch aktiv sein |
| `rejected` | einziger Kandidat für eine spätere Fristprüfung; noch keine Löschung | abgeschlossener Vorgang, Referenzen und Recovery müssen trotzdem geprüft sein |
| `accepted` | geschützt | Suite kann den Status direkt setzen; er ist allein kein bestätigter Beschäftigungsnachweis |
| `hired` | geschützt | kann Legacy-Fallback und Beschäftigungsnachweis berühren; minimiertes `employment_event` bleibt separat erhalten |
| unbekannt/fehlend/abweichend | geschützt und manuell zu prüfen | keine implizite Statusnormalisierung oder Löschfreigabe |

Die bestehende Fristkonfiguration (183 Tage Bewerbungen / 730 Tage Audit) wird nicht als fachlich vollständige Retention-Matrix neu bestätigt. Sie bleibt Konfiguration, während die Datenbereinigung gesperrt ist.

## 2. Datenschutzexport

`pulse_retention.export_person()` erweitert die deterministische Zuordnung:

- SQLite-Spalten `applicant_id` und `interviewer_id` werden für die Personensuche berücksichtigt.
- `suite_interviews` wird zusätzlich über die `application_id` aller Bewerbungen der betroffenen Person einbezogen. Dadurch gehen Interviewnotizen nicht verloren, wenn `applicant_id` in einem Legacy-Datensatz abweicht oder fehlt.
- `suite_audit` wird zusätzlich über `target_id = application_id` der eigenen Bewerbungen einbezogen.
- `pulse.db.calendar_events` wird über die explizit gespeicherten `interview_event_id`-Referenzen einbezogen; eine Suche in Titel oder Beschreibung findet nicht statt.
- Explizite `audit_ref`-Verweise aus Bewerbungshistorie und `team_data.json.employment_event` werden auf `audit_logs.json.id` aufgelöst.
- Der Zeilenhöchstwert von 5.000 je SQLite-Tabelle wird nicht mehr still verborgen: gekürzte Tabellen werden in `database_export_truncated` aufgeführt.

Der Export bleibt read-only. Er verwendet keine Textsuche in Interviewnotizen, Auditdetails, Motivationen oder anderen Freitextfeldern. Solche Suchen könnten falsche Treffer oder fremde personenbezogene Daten in einen Export aufnehmen.

**Verbleibende Grenze:** Historische Einträge in `audit_logs.json` enthalten standardmäßig `actor_id`, aber kein strukturiertes `subject_id`-/ `application_id`-Feld. Der Export kann diese Einträge daher nur über Akteurs-ID oder explizite `audit_ref`-IDs eindeutig zuordnen. Die fehlende historische Subjektbeziehung wird als Einschränkung ausgegeben, nicht erraten.

## 3. Read-only-Referenzaudit

`tools/application_data_audit.py` prüft jetzt zusätzlich:

- fehlende `applicant_id` und `interviewer_id` in Interviewzeilen;
- Abweichung zwischen `applications.json.user_id` und `suite_interviews.applicant_id`;
- Interviewer-IDs mit/ohne passende Teamakte als aggregierte Zähler;
- `suite_audit.target_id`-Referenzen auf bekannte Bewerbungs-IDs;
- Recruiting-Audit-Einträge ohne strukturiertes Ziel;
- zentrale `audit_logs.json`-Einträge, explizite Audit-Verweise und Recruiting-Aktionen ohne strukturierte Subjektbeziehung.

Das Werkzeug öffnet SQLite weiterhin mit `mode=ro`, erzeugt keine Tabellen und schreibt nur den ausdrücklich angegebenen, neuen aggregierten Report. Es gibt keine Bewerbernamen, Discord-IDs, Bewerbungs-IDs, Interviewnotizen, Antworten, Titel oder Event-IDs im Auditbericht aus.

Beispiel mit allen fünf Speicherquellen:

```bash
python tools/application_data_audit.py \
  --applications /path/to/export/applications.json \
  --team-data /path/to/export/team_data.json \
  --suite-db /path/to/export/pulse_suite.db \
  --pulse-db /path/to/export/pulse.db \
  --audit-logs /path/to/export/audit_logs.json \
  --output /path/to/export/audit-report.json
```

Ein fehlender Datenbank- oder Audit-Input lässt `readiness.requires_review` gesetzt. Ein erfolgreich erstellter Report ist kein Beweis dafür, dass Retention bereits sicher ist.

## 4. Retention – Fail-Closed

Die bestehende Löschfunktion ist für diesen Zwischenstand hart gesperrt:

- `RETENTION_MUTATIONS_ENABLED = False`.
- Die Sperre gilt auch bei `force=True`; eine force-Option kann die fehlende Mehrspeicher-Transaktion nicht umgehen.
- `run()` beendet sich vor jeder Änderung an `applications.json` oder `audit_logs.json`.
- Nur der exakte Status `rejected` wird noch als Kandidat für eine spätere Fristprüfung betrachtet.
- `in_review`, `pending`, `interview`, `accepted`, `hired`, unbekannte und fehlende Statuswerte werden nicht als abgelaufen zurückgegeben.
- Der öffentliche Bewerbungshinweis verspricht während der Sperre keine automatische Löschung; er informiert transparent, dass die automatische Bereinigung derzeit nicht aktiviert ist.

`plan_linked_retention()` ist eine zusätzliche ausschließlich lesende Vorschau. Sie zählt Kandidaten und explizite Interview-, Kalender- und Suite-Audit-Referenzen, prüft geteilte Kalender-IDs, Employment-Event-Kollisionen und das Alter des Komplett-Backups. Das Ergebnis enthält immer `deletion_supported=false` und `deletion_allowed=false`. Die Funktion ist kein versteckter Löschpfad und führt keine Mutation aus.

Die Retention bleibt damit auch bei versehentlicher Aktivierung der Einstellung geschützt. Die alte Mehrdatei-Bereinigung wird erst wieder freigegeben, wenn ein separates, journalisiertes Apply-/Rollback-Verfahren für alle betroffenen Speicher vorliegt und fachlich geprüft wurde.

## 5. Warum noch keine Löschung möglich ist

Folgende Punkte bleiben echte Blocker:

1. `audit_logs.json` hat für historische Einträge keine strukturierte Subjekt-/Bewerbungsreferenz. Freitext ist kein verlässlicher Foreign Key.
2. `suite_audit` ist hash-verkettet. Selektives Löschen verändert die Kette und benötigt eine fachlich bestätigte Archiv-/Ketten-Neuberechnung.
3. `suite_interviews` kann mehrere Datensätze pro Bewerbung enthalten und zusätzlich nur über die Bewerber-ID auf eine Bewerbung hinweisen.
4. `calendar_events` besitzt keine strukturierte `application_id`; nur eine eindeutige `interview_event_id` in der Bewerbungsakte ist eine zulässige Referenz. Geteilte oder fehlende IDs blockieren.
5. `team_data.json.employment_event` und `team_since` dürfen nicht durch das Entfernen einer Bewerbungsakte beschädigt werden.
6. Die Speicher liegen in mehreren Dateien/Datenbanken; `_io_lock` ist keine dateiübergreifende Transaktion.
7. Vollständiges Backup muss pro konkretem Archiv anhand des Manifests geprüft werden. Backup-Erstellung allein ist keine erfolgreiche Wiederherstellungsprobe.
8. Uploads/Anhänge und bereits zugestellte Discord-DMs haben keine durchgehend strukturierte Bewerbungszuordnung; sie dürfen nicht anhand von Dateinamen oder Freitext gelöscht werden.

## 6. Tests und Abnahme

Neue bzw. erweiterte synthetische Regressionstests prüfen:

- Export von Interviewzeilen für Bewerber und Interviewer;
- Export von Interviewzeilen über `application_id`, wenn die direkte Bewerber-ID nicht stimmt;
- Export von Kalenderereignissen über `interview_event_id`;
- Einbezug von `suite_audit`-Zielen und expliziten Audit-Referenzen;
- keine automatische Zuordnung über unstrukturierten Freitext;
- sichtbare Exportkürzung bei mehr als 5.000 Zeilen;
- Schutz von `in_review`, `accepted`, `hired`, unbekannten und fehlenden Statuswerten;
- Fail-Closed der Retention auch bei aktivierter Einstellung;
- read-only Linked-Retention-Preview ohne Änderung an JSON- oder SQLite-Dateien;
- fehlende, doppelte, geteilte und verwaiste Referenzen.

**Verifikation:** Die Testdateien wurden geschrieben, aber die GitHub-Actions müssen die Ausführung für den aktuellen PR-HEAD bestätigen. Bis dahin sind die Tests nicht als bestanden zu melden.

## 7. Nicht-Ziele

- keine echten Bewerbungsdaten oder Produktivexports;
- keine Löschung oder Anonymisierung;
- keine Änderung der Retention-Fristen in der Benutzeroberfläche;
- keine Änderungen an Bewerbungsformularen, URLs, `custom_id`, Rollen oder Berechtigungen;
- keine Änderungen an Team-Einstellungsereignissen, Interviews oder Kalenderdaten;
- keine Backend-Umschaltung und keine SQLite-Migration;
- kein Merge ohne Freigabe und kein VPS-Deployment.

## 8. Freigabekriterien für die nächste Teilphase

Phase 15.6e ist erst abgeschlossen, wenn alle synthetischen Regressionstests und CI-Gates grün sind, die Audit- und Exportgrenzen unabhängig reviewed wurden und eine fachlich bestätigte Retention-/Statusmatrix dokumentiert ist. Danach kann ein gesonderter Entwurf für strukturierte Subjekt-Auditfelder und ein journalisiertes Mehrspeicher-Apply erstellt werden. Bis zu diesem Nachweis bleibt jede Retention-Mutation gesperrt.
