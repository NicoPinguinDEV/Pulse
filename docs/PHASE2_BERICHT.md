# Phasenbericht Phase 2 – Datenintegrität, Backups, Datenschutz

Branch `verbesserung/phase-2-daten` (von `main` = 7.10.0), **nicht gepusht, nicht gemergt, nicht deployt**. Stand 20.10.2026.

## 1. Erledigt

| ID | Commits | Testnachweis |
|---|---|---|
| D-05 Ein Datenbankpfad | a25ed3a | `tests/test_db_path.py` (3) – eigener Pfad und `PULSE_DB_PATH` gelten für alle Module |
| D-09 Zeitstempel (teilweise) | 5597f26 | `tests/test_pulse_time.py` (4) – alle Altformate, Berlin-Anzeige inkl. Sommer-/Winterzeit |
| D-01 Komplett-Backup | 924cf5f, 1397a03 | `tests/test_full_backup.py` (12) – Roundtrip identisch, neue `*.db`/`*.json` ohne Backup-Eintrag schlagen fehl |
| D-02 Sicheres Wiederherstellen | 924cf5f, 1397a03 | gleiche Datei – Probelauf schreibt nichts, Manipulation abgelehnt, Fehler beim Schreiben → automatischer Rollback |
| D-03 Backups blockieren nicht | 924cf5f | Verhaltenstest: Discord-Teil im Event-Loop, Dateiarbeit im Thread |
| D-04 Aufbewahrung + Restore-Test | 924cf5f, 1397a03 | Aufbewahrungsschema, Ausdünnen der Sicherheitskopien, Auto-Lauf mit Restore-Test |
| D-08 Datenschutz | ff42797, 46af453, e841115, 04f63b9 | `tests/test_privacy.py` (14) – Teamakte/Suche nach E-3, Fristen aus, Bereinigung, Auskunft |
| D-10 Konsistenz-Diagnose | 8fb2ce1, 04f63b9 | `tests/test_consistency.py` (2) – meldet, korrigiert nichts |
| D-07 Wachstum (Teil) | 8fb2ce1 | Größenwarnung ab 5 MB im Systemstatus |

**Gates** (venv `Q:\venv`, Python 3.14, ohne Discord):
- `python -m compileall -q .` → OK
- `python tests/feature_smoke.py` → „Pulse feature smoke test: OK“
- `python tests/test_pulse_intelligence.py` → OK
- `python tests/smoke_test.py` → „Pulse smoke test: OK“
- `python -m unittest discover -s tests` → **377 Tests OK** (nach Phase 1: 345)

**Review** durch einen Subagenten mit frischem Kontext: 6 mittlere und 8 geringe Befunde. Behoben in e841115, 04f63b9 und 1397a03:
- E-3 galt nur auf einer Seite, Warnungen waren über Suche, `/team`, `/ultimate/*` und Suite-Teamakte sichtbar.
- Die Bereinigung hätte eingestellte Bewerbungen gelöscht, die für „Team seit“ gebraucht werden.
- Ein Restore ohne Rollback hätte bei Fehlern einen Teilzustand hinterlassen.
- Die Sperre während des Restores war unvollständig.
- Der Export war unvollständig und lief im Event-Loop.
- Es gab keine Platzgrenze.
- Kleinere Punkte: Prüfsummen-Zeitpunkt, Art eines Eintrags aus dem Manifest, Umgebungs-Pfade, HTTP 500, Warnstufe über 5, „Team seit“ in UTC, Fehler-Wiederholung der Bereinigung.

**Geänderter `feature_smoke`-Assert:** Er prüfte den alten Aufruf `to_thread(build_backup, …)`. Er prüft jetzt die neue Absicht: Dateiarbeit im Thread, Discord-Teil im Loop. Dazu kommt ein echter Verhaltenstest.

## 2. Verhalten vorher → nachher

**Backup**
- **Vorher:** Zwei Backups, die zusammen trotzdem nicht alles enthielten. Fünf Datenbanken, Uploads, Beweise und große Transkripte fehlten.
- **Nachher:** Unter *Backups* gibt es das **Komplett-Backup**. Ein ZIP enthält alle 8 Datenbanken, alle 11 JSON-Dateien, Uploads, Beweise und Transkripte, jeweils mit Prüfsumme, dazu die Discord-Struktur.
- Was über dem Größenbudget liegt, steht sichtbar als Warnung im Ergebnis.
- Nicht enthalten sind `.env` und `.session_secret`.

**Wiederherstellen**
- **Vorher:** Die laufende Datenbank wurde im Betrieb überschrieben, mit Risiko einer kaputten Datenbank.
- **Nachher:**
  - Ein **Probelauf** zeigt, welche Dateien und Tabellen sich ändern würden.
  - Erst nach Eingabe von `WIEDERHERSTELLEN` geht es los. Pulse legt eine Sicherheitskopie an, prüft alles vorab und kopiert die Datenbanken sicher in die laufenden Dateien.
  - Geht etwas schief, wird automatisch der vorherige Stand zurückgespielt.
  - Danach soll der Bot neu gestartet werden.
- **Discord-Rollen und Kanäle** stellt weiterhin nur das bisherige JSON-Backup wieder her. Diese Funktion ist unverändert.

**Automatik**
- Mit jeder Auto-Sicherung entsteht auch ein Komplett-Backup.
- Aufbewahrt werden 7 tägliche, 4 wöchentliche und 6 monatliche. Von den Sicherheitskopien bleiben die neuesten 3; manuelle Sicherungen werden nie automatisch gelöscht.
- Einmal pro Woche prüft ein **Restore-Test**, ob sich die Sicherung wirklich zurückspielen ließe. Das Ergebnis steht im Systemstatus.
- Ab 4000 MB Gesamtgröße pausiert das Auto-Komplett-Backup sichtbar.

**Kein Hänger mehr:** Backups laufen im Hintergrund, der Discord-Heartbeat wird nicht blockiert.

**Teamakte (E-3)**
- **Vorher:** Jeder mit Dashboard-Zugang sah fremde Warnungen samt Grund und interne Notizen, auch über die Suche.
- **Nachher:** Warnungen und ihre Anzahl sehen nur die Person selbst und `can_warn`/Admins. Notizen sehen nur `can_add_notes`, die Leitung und Admins.

**Datenschutz**
- Im Bewerbungsformular steht ein Datenschutzhinweis.
- Neue Seite *Verwaltung → Datenschutz* mit drei Funktionen:
  - **Aufbewahrungsfristen** (E-9), Standard **aus**: abgelehnte bzw. abgeschlossene Bewerbungen nach 183 Tagen, Audit-Log nach 730 Tagen. Offene und eingestellte Bewerbungen bleiben immer.
  - Bereinigt wird nur mit frischem Komplett-Backup, und jede Bereinigung wird protokolliert.
  - **Auskunft**: JSON-Export aller Daten zu einer Discord-ID.

**Ein Datenbankpfad:** Wer `PULSE_DB_PATH` setzt, bekommt keine zweite, halb benutzte `pulse.db` mehr.

**Zeitstempel**
- Alle bisherigen Datumsformate werden verstanden.
- Erinnerungen zu liegengebliebenen Bewerbungen kamen bisher 2 h zu spät (Berliner Zeit wurde als UTC gelesen). Außerdem brach ein unlesbares Datum alle Erinnerungen ab. Beides ist behoben.

**Systemstatus:** Er meldet jetzt auch:
- verwaiste Schichten, Tickets und Voice-Fälle,
- Warnrollen, die nicht zur Warnungszahl passen,
- Abmeldungen mit Ende vor Beginn,
- übergroße Datendateien,
- letztes Komplett-Backup und Restore-Test.

## 3. Verworfen oder verschoben

- **D-07 Auslagern wachsender Dateien:** verschoben zur SQLite-Migration A-05. Die Schicht-Historie wird an 31 Stellen für Gesamtwerte (Gesamtstunden, Achievements) gelesen; ein Archiv würde Zahlen verändern. Bis dahin warnt der Systemstatus ab 5 MB.
- **D-09 Schreibformat:** Neue Daten werden noch in den bisherigen Formaten geschrieben, weil Anzeigen und Leser diese erwarten. Die Umstellung kommt mit A-05.
- **D-06 `PULSE_DATA_DIR`:** verschoben nach Phase 8 (O-03). Bot-Hosting startet aus dem Repo-Ordner ohne Docker-Volume. Die Änderung betrifft die Pfade von rund 20 Modulen.
- **KI-Moderationsfälle:** Pulse löscht entschiedene Fälle schon nach 30 Tagen. Das ist strenger als E-9 (3 Monate), deshalb unverändert.
- **Bewusst offen:** `/ultimate/api/v2/team` liefert Warnzahlen an Integrationen mit API-Schlüssel. Schlüssel vergeben nur Admins.
- **Löschung einer einzelnen Person:** Sie bleibt bewusst Handarbeit. Es gibt nur den Export.

## 4. Risiken und Schritte außerhalb des Codes

- **Speicherplatz auf Bot-Hosting:**
  - Ein Komplett-Backup ist so groß wie alle Datenbanken plus Uploads, also maximal 500 MB Dateien pro Backup. Bei 7/4/6 können bis zu 17 Stück liegen.
  - Bitte den Speicher-Kontingent prüfen. Gegebenenfalls `PULSE_BACKUP_TOTAL_MAX_MB` und `PULSE_BACKUP_MAX_MB` in `.env` kleiner setzen.
  - Vor jedem Backup prüft Pulse, ob mindestens 200 MB plus zweimal die letzte Backup-Größe frei sind.
- **Nach einem Wiederherstellen** den Bot neu starten. Social Media und KI-Moderation halten Daten im Speicher.
- **Während eines Restores** warten Dashboard-Zugriffe auf JSON-Dateien kurz. Ein Restore dauert je nach Datenmenge einige Sekunden.
- **Externe Kopie:** Weiterhin geht nur das kleine JSON-Backup in den Discord-Kanal. Das Komplett-ZIP ist dafür meist zu groß; regelmäßig herunterladen und extern ablegen.
- **Datenschutz-Kontakt:** unter *Verwaltung → Datenschutz* eintragen (offen aus E-9). Ohne Eintrag steht dort „die Serverleitung über ein Support-Ticket“.
- **Teammitglieder ohne `can_warn`** sehen fremde Warnungen nicht mehr, auch nicht die Zahl in der Teamliste. Wer das braucht, bekommt das Recht in den Einstellungen.

## 5. Offene Fragen an dich

1. **Wie viel Speicher hat das Bot-Hosting-Paket?** Danach richten sich Größenbudget und Gesamtgrenze der Sicherungen.
2. **Kontakt für den Datenschutzhinweis:** Welche Person oder welcher Weg soll dort stehen?
3. **Fristen:** Soll die automatische Bereinigung nach dem Deploy eingeschaltet werden (E-9-Werte), oder vorerst aus bleiben?
4. **Freigabe:** Darf ich Phase 2 nach `main` mergen und pushen? Vorschlag: Version 7.11.0 mit CHANGELOG-Eintrag.
