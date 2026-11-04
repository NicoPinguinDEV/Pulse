# Phase 15.6b – Daten- und Retention-Matrix für Bewerbungen

**Status:** Dokumentationsentwurf zur Prüfung · keine Implementierung, keine Produktionsdaten gelesen  
**Codebasis:** `main` @ `e7311e7b9bb3fb90706629eaf0fabe2086e4782d` (04.11.2026)  
**Grundlagen:** PR #136 / Phase 15.6a, `tools/application_data_audit.py`, `pulse_retention.py`, `webserver.py`, `cogs/pulse_suite.py`, `pulse_db.py`, `pulse_backup.py`, `docs/PHASE15_3_APPLICATIONS_SECURITY.md`  
**Sicherheitsgrenze:** Nur Quellcode, Dokumentation und synthetische Tests ausgewertet. Keine Produktivdateien, Exporte oder VPS-Daten geöffnet. Keine Löschung, Migration, Retention-Aktivierung oder Backend-Umschaltung.

## 1. Ergebnis in Kürze

Bewerbungsdaten liegen nicht nur in `applications.json`. Sie verteilen sich auf eine Bewerbungsakte, die Teamakte, eine separate Interviewdatenbank, die Kalenderdatenbank und ein Audit-JSON. Die Beziehungen sind überwiegend anwendungsseitig; es gibt keine gemeinsame Transaktion über diese Speicher.

Die aktuelle Retention ist standardmäßig ausgeschaltet. Bei Aktivierung beträgt der Standardwert für Bewerbungsakten 183 Tage und für Audit-Einträge 730 Tage. Der aktuelle Bereinigungslauf löscht jedoch nur ganze Einträge aus `applications.json` und alte Einträge aus `audit_logs.json`. Er löscht keine verknüpften Interviewzeilen oder Kalenderereignisse und erzeugt keine feldweise Anonymisierung.

**Folgerung:** Die 183-Tage-Regel darf nicht unverändert auf die gesamte Bewerbungsakte übertragen oder aktiviert werden, bevor Status, Einstellungsnachweis, Interview-/Kalenderreferenzen, Datenschutzexport und Wiederherstellungsweg gemeinsam behandelt werden.

## 2. Statusmodell und tatsächliche Übergänge

Die Statuswerte werden nicht an nur einer Stelle verwaltet. Der Read-only-Audit erkennt die folgenden sechs festen Werte. Die aktive Hauptentscheidung und die ältere Suite-Verwaltung haben unterschiedliche Übergangsregeln.

| Status | Im Code belegte Verwendung | Aktuelle Retention | Bewertung für die Planung |
|---|---|---|---|
| `pending` | Öffentliche Einreichung legt neue Bewerbung mit `pending` an. Hauptentscheidung erlaubt `accept` oder `reject`. | Geschützt; wird nicht automatisch entfernt. | Offener Vorgang. Erst nach einer nachvollziehbaren Entscheidung in eine Abschlussfrist überführen. |
| `in_review` | `cogs/pulse_suite.py` erlaubt diesen Status in der allgemeinen Bewerbungsaktualisierung. Die Hauptansicht betrachtet nur `pending` und `interview` als aktiv. | Nicht geschützt; nach 183 Tagen löschbar, sofern der verwendete Zeitstempel gültig und alt genug ist. | **Risiko:** Kann als laufende Prüfung missverstanden werden, landet aber in der aktuellen Historienansicht und ist nicht in der Retention-Ausnahmemenge. Produktentscheidung nötig. |
| `interview` | Hauptentscheidung lädt zur verpflichtenden Gesprächsphase ein. Suite-Interviewbewertung und Terminplanung verwenden diesen Status ebenfalls. | Geschützt; wird nicht automatisch entfernt. | Offener Vorgang. Gesprächsnotizen, Termin und Reminder müssen vor einer späteren Statusbereinigung berücksichtigt werden. |
| `accepted` | Von der Suite-Verwaltung als Status zulässig; wird von Team-Board und `team_since_for` als Beschäftigungs-/Teamstatus behandelt. | Dauerhaft geschützt. | Legacy-/Altstatus. Nicht pauschal mit `hired` gleichsetzen oder löschen, bevor der tatsächliche Einstellungsfall und Eintrittszeitpunkt geprüft wurden. |
| `hired` | Hauptflow `decide_app → hire_after_interview` setzt diesen Status nach dem verpflichtenden Gespräch und schreibt `team_data.json.team_since`. | Dauerhaft geschützt. | Beschäftigungsnachweis muss künftig unabhängig von vollständigen Antworten und Interviewnotizen erhalten bleiben. |
| `rejected` | Hauptflow kann aus `pending` oder `interview` ablehnen; Suite-Verwaltung kann den Status ebenfalls setzen. | Nach 183 Tagen löschbar, wenn der maßgebliche Zeitstempel gültig ist. | Kandidat für eine referenzsichere Löschung/Anonymisierung nach Ablauf der beschlossenen Frist. |

Zusätzliche Regeln und Grenzen:

- Die Hauptentscheidung akzeptiert nur `pending → interview/rejected` und `interview → hired/rejected`. Die allgemeine Suite-Aktualisierung lässt dagegen `pending`, `in_review`, `interview`, `accepted` und `rejected` direkt setzen. Die zwei Wege bilden daher keine einheitliche, strikt durchgesetzte Zustandsmaschine.
- `rejected_after_interview` ist ein Nachrichten-/Vorlagenschlüssel, kein zusätzlicher gespeicherter Bewerbungsstatus; gespeichert wird `rejected`.
- Der Audit gruppiert unbekannte, fehlende oder abweichend geschriebene Werte in `unknown_or_missing`. Die Retention schützt solche Werte nicht automatisch: Jeder Status außerhalb von `pending`, `interview`, `accepted` und `hired` kann nach Fristablauf löschbar sein.
- Für einen unbekannten oder nicht normalisierten Status ist eine manuelle Prüfung sicherer als eine automatische Löschung. Statuswerte dürfen beim Import nicht still normalisiert werden.

## 3. Daten- und Speicher-Matrix

| Speicher | Inhalt / personenbezogene oder sensible Daten | Schlüssel und Beziehungen | Aktuelle Aufbewahrung / Backup |
|---|---|---|---|
| `applications.json` | Bewerber-`user_id`, Name, Motivationstext, Laufbahn, Testscore und Testergebnis, Testfragen/-antworten samt richtiger Antwort, Praxisantworten und Prüfhilfen, Votes, Reviews/Notizen, Bearbeiter, Entscheidungsgrund und Verlauf, Interview-/Termin-/Reminder-Felder. | Top-Level-Schlüssel ist die Bewerbungs-ID. Datensatz enthält `user_id`, `interview_id` und `interview_event_id`; Querverweise werden überwiegend in Anwendungscode gepflegt. | Retention: Standard 183 Tage, aber Statusausnahmen. Im Full-Backup als `applications.json` enthalten. |
| `team_data.json` | Teamakte pro Discord-ID: `team_since`, Laufbahn/Label und übernommene Test-Metadaten; weitere Teamdaten wie manuelle Einstellungsangaben und Probezeit. | Top-Level-Schlüssel ist die Discord-ID. Der aktuelle Einstellungsflow schreibt `team_since`; keine zwingende Fremdschlüsselbeziehung zur Bewerbungs-ID. | Keine Bewerbungs-Retention. Im Full-Backup enthalten. Muss erhalten bleiben, soweit es Teamhistorie und Beschäftigungsnachweis trägt. |
| `pulse_suite.db` | `suite_interviews`: Interview-ID, `application_id`, Bewerber-ID und -Name, Bewertungswerte als JSON, Gesamtwert, Empfehlung, Gesprächsnotizen, Interviewer-ID/-Name und Zeitstempel. | `id` ist Primärschlüssel; `application_id` ist Text ohne nachgewiesenen Fremdschlüssel auf `applications.json`. Mehrere Interviewzeilen pro Bewerbung sind möglich. Die Bewerbungsakte speichert nur ein einzelnes Feld `interview_id`. | Keine eigene Bewerbungs-Retention. `pulse_backup.SQLITE_FILES` enthält `pulse_suite.db`; ein vorhandener DB-Snapshot wird vom Full-Backup aufgenommen. Der 100K-Melonly-Rehearsal hatte diese DB aber nicht als Fixture und testete daher keine Interviewdaten. |
| `pulse.db` | `calendar_events`: Titel, Start-/Endzeit, Kategorie, Ort, Beschreibung, Ersteller-ID/-Name und Erstellzeit. Interviewtermine können Bewerbername und Bewerbungs-ID im Titel bzw. Beschreibungstext enthalten. | `id` ist Primärschlüssel. Es gibt keine strukturierte `application_id`. Die Bewerbungsakte hält `interview_event_id`; beim Termin-Neusetzen wird das zuvor referenzierte Event anhand dieser ID gelöscht. | Keine Bewerbungs-Retention. In `pulse_backup.SQLITE_FILES` und damit im Full-Backup enthalten. |
| `audit_logs.json` | Audit-ID, Akteur/Name, `actor_id`, Aktion, Freitextdetails und Zeitstempel. Bewerbungsaktionen können darin Namen, Discord-IDs und Entscheidungsgründe im Freitext hinterlassen. | Kein strukturiertes `application_id`- oder `target_id`-Feld im zentralen `log_audit`-Datensatz. Bezug auf eine Bewerbung ist, soweit vorhanden, nur über Aktionsdetails/Freitext erkennbar. | Retention-Standard 730 Tage; beim Schreiben zusätzlich auf die letzten 5.000 Einträge begrenzt. Im Full-Backup enthalten. |

### 3.1 Ergänzende Daten außerhalb der fünf Hauptspeicher

- `pulse_suite.db` enthält zusätzlich eine eigenständige `suite_audit`-Tabelle. Sie ist nicht mit `audit_logs.json` identisch und hat eigene Spalten für Akteur, Ziel, Aktion, Details, Request-ID und Hash-Verkettung.
- Das Bewerbungsmodul speichert konfigurierbare DM-Vorlagen in `pulse.db`-Settings. Tatsächlich versendete Discord-DMs liegen nicht als vollständige lokale Nachrichtendatensätze in den genannten fünf Speichern. Eine lokale Löschung kann bereits zugestellte Discord-Nachrichten nicht automatisch zurückholen.
- Full-Backups enthalten außerdem die konfigurierten Ordner `uploads/`, `attachments/` und `transcripts/`. Im geprüften Bewerbungsflow wurde keine eindeutige, strukturierte Bewerbungs-Anhangsreferenz gefunden. Solche Dateien dürfen nicht durch Namens- oder Freitextsuche einer Bewerbung zugeordnet und gelöscht werden.

## 4. Team-Eintrittsdatum: unabhängiger Nachweis

`webserver.team_since_for(member, entry, apps)` verwendet aktuell diese Reihenfolge:

1. Gültiges `team_data.json[discord_id].team_since`.
2. Sonst das älteste gültige `decided_at` aller Bewerbungen derselben `user_id` mit Status exakt `hired` oder `accepted`.
3. Sonst `member.joined_at` aus Discord; wenn auch das fehlt, `unbekannt`.

Der primäre Einstellungspfad nach dem Bewerbungsgespräch setzt `team_data.json.team_since` vor dem Speichern des aktualisierten Bewerbungsdatensatzes. Die manuelle Teameinstellung schreibt ebenfalls `team_since` in die Teamakte. Der Fallback auf `accepted`/`hired` bleibt dennoch für Altbestand relevant.

**Sichere Zielrichtung für Phase 15.6c:** Ein minimiertes, unabhängiges Einstellungsereignis muss mindestens eine eindeutige Nutzerzuordnung, den fachlich bestätigten Eintrittszeitpunkt, die benötigte Laufbahn und eine notwendige Audit-Referenz enthalten. Es darf keine Motivation, Test-/Praxisantworten, Reviews oder Gesprächsnotizen kopieren. Vor einem Backfill sind alte `accepted`- und `hired`-Fälle einzeln auf tatsächliche Einstellung und korrekten Zeitpunkt zu prüfen. Nicht auflösbare Fälle blockieren die Bereinigung.

## 5. Interview- und Kalenderbeziehungen

### 5.1 Interviews

- `suite_interviews.application_id` referenziert die Bewerbungs-ID in `applications.json`, aber ohne SQL-Fremdschlüssel.
- Jede Interviewbewertung hat eine eigene `id`. Mehrere Einträge können zu derselben Bewerbung existieren; der neueste Schreibpfad speichert nur eine `interview_id` im Bewerbungsobjekt. Für eine Bereinigung müssen daher **alle** Zeilen anhand `application_id` betrachtet werden, nicht nur das eine Feld `interview_id`.
- Das Read-only-Audit prüft verwaiste Bewerbungsreferenzen, fehlende/duplizierte Interview-IDs, nicht passende Einzelreferenzen und mehrere Interviews pro Bewerbung. Es gibt nur aggregierte Zähler aus.

### 5.2 Kalendertermine

- `applications.json.interview_event_id` ist der strukturierte Rückverweis zum Kalender-Primärschlüssel.
- Der aktuelle Terminplaner speichert als Beschreibung `Bewerbung {app_id}`, aber das Feld ist Freitext und keine Datenbankbeziehung.
- Wenn eine Bewerbung bereits entfernt wurde, kann ein danach noch existierender Kalendertermin anhand der heutigen Tabellenstruktur nicht zuverlässig als verwaist identifiziert werden. Ein Titel-/Freitext-Scan ist keine sichere Löschgrundlage.
- Teilen mehrere Bewerbungen dieselbe Event-ID, muss die Bereinigung blockieren und manuelle Prüfung verlangen.

**Konsequenz:** Interviewzeilen dürfen nur über eine eindeutige `application_id`-Zuordnung behandelt werden. Kalendertermine nur über eine vorhandene, eindeutige `interview_event_id` oder eine künftig eingeführte strukturierte Eigentümerreferenz.

## 6. Aktuelle Retention – exakt beobachtetes Verhalten

`pulse_retention.py` verwendet derzeit:

- `enabled = false` als Standard; die Funktion läuft nicht automatisch, solange sie nicht aktiviert ist (außer interner `force=True`-Aufruf).
- `applications_days = 183` als Standard, begrenzt auf 30–3.650 Tage.
- `audit_days = 730` als Standard, begrenzt auf 90–3.650 Tage.
- Bewerbungs-Zeitstempel in dieser Priorität: `decided_at`, sonst `updated_at`, sonst `created_at`.
- Geschützte Statusmenge: `pending`, `interview`, `accepted`, `hired`.
- Alte Audit-Einträge werden nur anhand `timestamp` bewertet.

Vor dem Lauf verlangt die Funktion ein frisches Full-Backup (im Regelfall maximal zwei Tage alt; die erlaubte Frist richtet sich zusätzlich nach dem konfigurierten Auto-Backup-Intervall). Der Lauf hält `ws._io_lock`, schreibt aber mehrere JSON-Dateien nacheinander.

**Wesentliche Grenzen des bestehenden Dry-Runs und Löschlaufs:**

1. `plan()` meldet nur die Anzahl ablaufender Bewerbungsobjekte und Audit-Einträge. Es zeigt weder konkrete Feldgruppen noch Interview- und Kalenderzeilen getrennt an.
2. `run()` entfernt ganze Bewerbungsobjekte aus `applications.json` und alte Audit-Einträge aus `audit_logs.json`. `team_data.json`, `pulse_suite.db` und `pulse.db` werden durch diesen Retention-Pfad nicht bereinigt.
3. Es gibt keine gemeinsame Transaktion über JSON, `pulse_suite.db` und `pulse.db`. Ein Fehler zwischen den einzelnen Schreibvorgängen kann einen Teilzustand hinterlassen; der frische Backup-Schutz ist keine Transaktion und kein automatischer Rollback.
4. Ein Status außerhalb der vier geschützten Werte (einschließlich `in_review` und unbekannter Werte) kann nach Fristablauf gelöscht werden.
5. Die zentrale Audit-Datei enthält Freitextdetails. Eine reine Alterslöschung kann zwar alte Einträge entfernen, sie kann aber keine Bewerbungsspezifische Redaktion oder gezielte Referenzauflösung durchführen.

## 7. Datenschutzexport: festgestellte Abdeckungslücke

`pulse_retention.export_person()` durchsucht JSON-Speicher und SQLite-Tabellen anhand einer festen Liste von ID-Spalten (`user_id`, `member_id`, `actor_id`, `target_id`, u. a.). Diese Liste enthält **nicht** `applicant_id` und **nicht** `interviewer_id`. In `suite_interviews` heißen die beteiligten Spalten aber genau `applicant_id` und `interviewer_id`.

Damit wird `suite_interviews` durch den aktuellen generischen ID-Spaltenfilter nicht zuverlässig im personenbezogenen Export gefunden. Der vorhandene Exporttest prüft eine synthetische Tabelle mit `user_id`, nicht die Bewerber-/Interviewerreferenzen der Interviewtabelle.

**Blocker vor 15.6e/15.6f:** Exportabdeckung für Bewerber- und Interviewer-ID korrigieren und mit getrennten synthetischen Testfällen belegen. Kein personenbezogener Datenexport oder Löschprozess darf auf eine unvollständige Referenzsuche vertrauen.

## 8. Backup-Abgleich und Aussagegrenze von Phase 15.5

Der Quellcode von `pulse_backup.py` listet `pulse_suite.db` sowie `pulse.db` und weitere Datenbanken in `SQLITE_FILES`; vorhandene Dateien werden durch den Full-Backup-Pfad aufgenommen. `applications.json`, `team_data.json` und `audit_logs.json` stehen in `JSON_FILES`.

Die erzeugte `docs/DATENLANDKARTE.md` markiert den ZIP-Backup-Status mehrerer dieser Datenbanken jedoch weiterhin mit `❌`. Das widerspricht der aktuellen `pulse_backup.SQLITE_FILES`-Liste. Für die Backup-Abdeckung ist deshalb bis zur Korrektur des Data-Map-Generators der ausführbare Backup-Code maßgeblich. Die automatisierte Datenlandkarte muss in einer getrennten Dokumentations-/Tooling-Aufgabe wieder mit dem Code in Einklang gebracht werden.

Der erfolgreiche 100K-Rehearsal aus Phase 15.5 hatte im synthetischen Fixture `pulse.db` und `logs.json` als zwei Manifestdateien. Er beweist den Melonly-Backup-/Restore-Pfad, aber **keinen** Ende-zu-Ende-Restore von Bewerbungsinterviews aus `pulse_suite.db`, Kalenderterminen aus `pulse.db` und den zugehörigen JSON-Referenzen. Diese fachübergreifende Prüfung gehört in die spätere 15.6e-Abnahme.

## 9. Vorgeschlagene Retention-Matrix – noch keine Produktentscheidung

| Datentyp | Sichere Zielrichtung | Voraussetzung / Sperre |
|---|---|---|
| Offene `pending`-Bewerbung | Nicht automatisch löschen. | Status bleibt offen; nur manuelle Entscheidung oder ein separat festgelegter Abbruchstatus beendet den Vorgang. |
| `interview` samt Termin/Reminder | Nicht automatisch löschen, solange das Gesprächsverfahren offen ist. | Statuswechsel und Reminder-Status müssen eindeutig abgeschlossen sein. |
| `in_review` | Bis zur Produktentscheidung nicht automatisch löschen; als offenen bzw. ungeklärten Status behandeln. | Hauptansicht und Suite-Statusmodell vereinheitlichen oder explizite Altstatusregel beschließen. |
| Abgelehnte Bewerbung | Nach festgelegter Frist (aktuell 183 Tage) Löschung/Anonymisierung der vollständigen Antwort- und Reviewdaten prüfen. | Dry-Run muss Bewerbung, Interviewnotizen, Kalenderreferenz, Audit-Spuren und Backup getrennt ausweisen. |
| `accepted`/`hired` | Vollständige Bewerbungsantworten nicht unbegrenzt als Personalnachweis behalten. | Erst nach unabhängigem, validiertem Beschäftigungsereignis und geprüftem Legacy-Backfill; bis dahin schützen. |
| Interviewbewertungen und Notizen | Nicht länger als die zugehörige Bewerbungsakte ohne begründete eigene Frist aufbewahren. | Zuordnung über sämtliche `suite_interviews.application_id`-Zeilen; sichere Exportabdeckung; Full-Backup/Restore-Probe. |
| Interview-Kalendertermin | Nur bei eindeutiger Eigentümerreferenz gemeinsam entfernen oder anonymisieren. | Event-ID muss eindeutig und ungeteilt sein; kein Löschen anhand Titel/Freitext. |
| Team-Eintrittsnachweis | Minimalen Nachweis behalten, nicht die vollständige Bewerbungsakte. | `team_since_for` muss für neue und historische Teammitglieder vor/nach Bereinigung fachlich identisch bleiben. |
| Audit | Separat nach festgelegter Frist aufbewahren; Details minimieren, strukturierte Referenz statt Bewerberdaten im Freitext. | Entscheidung über 730 Tage, Nachvollziehbarkeit und notwendige Pflichtinformationen; keine stillschweigende Änderung des bestehenden Werts. |
| DM-/Discord-Kommunikation und Dateianhänge | Eigene Speicher-/Löschgrenze ermitteln; nicht als lokal gelöscht behandeln. | Discord-Nachrichten sind nicht automatisch durch JSON-/DB-Löschung entfernbar; Dateien nur anhand expliziter Referenzen behandeln. |

## 10. Blocker und Entscheidungen vor Implementierung

1. **Statusverträge:** `in_review` als aktiv, beendet oder Legacy definieren. Für `accepted` und `hired` muss klar sein, welche Fälle tatsächlich eine Einstellung bedeuten.
2. **Beschäftigungsnachweis:** minimalen Ereignisdatensatz und Backfill-Regel festlegen; alle alten Datensätze ohne auflösbares Eintrittsdatum ausweisen.
3. **Exportabdeckung:** `suite_interviews.applicant_id` und `interviewer_id` in den personenbezogenen Export aufnehmen und testen.
4. **Referenzsicherheit:** vollständige Zuordnung aller Interviewzeilen und Kalendertermine; vorhandene verwaiste Kalendertermine bleiben ohne zusätzliche Daten nicht sicher identifizierbar.
5. **Backup-Abnahme:** 15.5 nicht als Nachweis für Bewerbungsdaten-Restore auslegen. Eine eigene synthetische 15.6e-Probe muss `applications.json`, `team_data.json`, `pulse_suite.db`, `pulse.db` und `audit_logs.json` gemeinsam abdecken.
6. **Mehrdateien-Löschung:** Journal/Recovery-Zustand, Idempotenz, Dry-Run und Teilfehler-Wiederherstellung definieren; `_io_lock` ersetzt keine dateiübergreifende Transaktion.
7. **Aufbewahrung und Hinweis:** Frist je Datenart/Status beschließen und verantwortlichen Kontakt für den Datenschutzhinweis festlegen. Bis dahin bestehende Werte und Hinweis nicht verändern.

## 11. Abnahme für Phase 15.6b

- [x] Reale Statuswerte, Übergänge und Retention-Ausnahmen anhand des Codes abgeglichen.
- [x] Alle fünf angeforderten Speicher mit Datenarten, Referenzen und aktuellem Backup-/Retention-Verhalten erfasst.
- [x] `Team seit`-Priorität und Legacy-Fallback exakt dokumentiert.
- [x] Interview-, Kalender- und Audit-Beziehungen sowie bekannte Grenzen beschrieben.
- [x] Datenschutzexport und Abdeckungslücke bei `suite_interviews` benannt.
- [x] 15.5-Melonly-Ergebnis von einem anwendungsübergreifenden Bewerbungs-Restore-Nachweis abgegrenzt.
- [x] Nur Quellcode und synthetische Tests geprüft; keine Produktionsdaten oder Laufzeitdateien geöffnet.
- [x] Keine Codeänderung, Migration, Retention-Aktivierung, Löschung oder VPS-Aktion vorgenommen.

**Nächster Schritt:** fachliches Review dieser Matrix und Entscheidung über die offenen Punkte. Erst danach Phase 15.6c (minimiertes Einstellungsereignis) implementieren. 15.6d/15.6e bleiben getrennte PRs; eine Produktivmigration in 15.6f benötigt weiterhin eine eigene ausdrückliche Freigabe.
