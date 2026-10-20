# Phasenbericht Phase 1 – Kritische Fehler & Sicherheit

Branch `verbesserung/phase-1-kritisch` (abgezweigt von `verbesserung/phase-0-bestandsaufnahme`), **nicht gepusht, nicht gemergt, nicht deployt**.
Stand: 20.10.2026.

## 1. Erledigt

| ID | Commit | Testnachweis |
|---|---|---|
| K-17 API-Doku nicht öffentlich | 4a0cac3 | `tests/test_phase1_security.py` – `/docs`, `/redoc`, `/openapi.json` → 404 |
| K-14 CSV-Formel-Injection | efea9dc | `tests/test_phase1_security.py` – beide Exporte neutralisieren `=`/`+`/`-`/`@` |
| K-13 Abhängigkeiten | f7730d8 | `tests/test_dependencies.py` – `discord.py>=2.6,<3`, `tzdata`, alle Module importierbar |
| K-15 Laufzeitdaten aus Git/Image | 6db9cf8, 41293d9 | `tests/test_ignore_files.py` – inkl. `*.corrupt-*` |
| K-03 Session-Secret, Token-Typen | 31840e0, 6aacfb5 | `tests/test_session_tokens.py` (7) |
| K-08 Slash-Fehlerhandler | 1d0f5d3 | `tests/test_command_errors.py` (5) |
| K-02 RP-Verwaltung | 823837c, 41293d9 | `tests/test_rp_verwaltung.py` (8) |
| K-12 Moderations-Log | 08cbd5b, 41293d9 | `tests/test_advanced_logs.py` (3) |
| K-05 Kaputte JSON-Datei | 751d491 | `tests/test_json_safety.py` (4) |
| K-06 Ticket-Transkript | fd0177c, 41293d9 | `tests/test_ticket_archive.py` (3) |
| K-07 Buttons nach Neustart | 8a5014b | `tests/test_persistent_buttons.py` (5) |
| K-09 Bestätigungsdialoge | 1470120 | `tests/test_confirm_dialog.py` (4) |
| K-01 Bewerbungsportal | 0bc6e2b | `tests/test_apply_session.py` (6) |
| K-04 Race Conditions (teilweise) | a769a3a, 41293d9 | `tests/test_concurrent_writes.py` (8) |
| K-16 Routen-Snapshot | 1af2fc9 | `tests/test_route_snapshot.py` (3) |
| K-10 ffmpeg/opus | 63a7743 | `tests/test_audio_check.py` (3) |

**Gates (venv `Q:\venv`, Python 3.14, ohne Discord-Verbindung):**
`python -m compileall -q .` → OK · `python tests/feature_smoke.py` → „Pulse feature smoke test: OK“ ·
`python tests/test_pulse_intelligence.py` → OK · `python tests/smoke_test.py` → „Pulse smoke test: OK“ ·
`python -m unittest discover -s tests` → **345 Tests OK** (Phase 0: 275).

Review durch einen Subagenten mit frischem Kontext: 4 mittlere und 7 geringe Befunde, keine kritischen. Behoben in 41293d9: `*.corrupt-*` war nicht ignoriert, Merge arbeitete je Warnung statt je Feld, Activity-Check-Warnlauf war nicht umgestellt, RP-Sendefehler blieb unbeantwortet, mehrfaches Umbenennen, K-12-Validierung. Offen: siehe Abschnitte 3–5.

## 2. Verhalten vorher → nachher

- **RP-Panel:** Bisher konnte jeder mit Zugriff auf das Panel `@everyone` auslösen, und Pulse löschte die letzten 10 Nachrichten von wem auch immer. Jetzt dürfen nur Admins, die Leitung (Beförderungsrecht) und Rollen, die unter Verwaltung → Discord-Auftritt freigegeben sind. Es gilt 1 Minute Abstand. Gelöscht wird nur die eigene letzte Ankündigung. Die Uhrzeit muss „14:30“ oder „Offen“ sein. Fehlt der Kanal, gibt es eine klare Meldung statt einer Ankündigung im falschen Kanal.
- **Moderations-Log:** Bisher wurde nie etwas protokolliert. Jetzt ist der Kanal unter Verwaltung → Discord-Auftritt einstellbar. Ohne Kanal erscheint ein Hinweis im Systemstatus.
- **Kaputte Datendatei:** Bisher wurde sie still durch leere Standardwerte ersetzt, Warnungen und Einstellungen waren dann weg. Jetzt legt Pulse eine Sicherungskopie an, warnt laut und speichert diese Datei nicht mehr, bis sie repariert ist. Das Dashboard zeigt dazu eine verständliche Seite.
- **Ticket schließen:** Bisher wurde der Kanal gelöscht, auch wenn das Transkript fehlschlug. Jetzt bleibt der Kanal dann bestehen, wird in `geschlossen-<id>` umbenannt und für den Ersteller gesperrt, und das Ticket bleibt offen. Mit erneutem „Ticket schließen“ wird es noch einmal versucht. Anhänge bis 8 MB werden lokal gesichert, das Transkript geht zusätzlich in den Ticket-Log-Kanal.
- **Buttons nach Neustart:** Bisher kam bei Fall-Buttons aus `/pulse-ticket` und bei „Übernehmen“/„Auftrag erledigt“ im Büro-System nach jedem Neustart „Interaktion fehlgeschlagen“. Jetzt funktionieren sie, auch auf alten Nachrichten.
- **Bestätigungen (Warnung, Kick, Rangwechsel):** Bisher antwortete Discord bei Verzögerung nicht, Doppelklicks waren möglich und Fehler zeigten Rohtexte. Jetzt kommt sofort „⏳ Wird ausgeführt …“, ein Klick löst genau eine Aktion aus und die Rechte werden beim Klick neu geprüft. Nach 45 s zeigt der Dialog „Bestätigung abgelaufen“, Fehler erscheinen mit Fehler-ID.
- **Bewerbung:** Bisher war nach 30 Minuten alles weg. Jetzt läuft die Sitzung 3 Stunden und verlängert sich beim Schreiben. Antworten liegen als Entwurf im Browser. Jede Fehlerseite führt zurück zum ausgefüllten Formular, nach erneutem Discord-Login landet man wieder in der gewählten Laufbahn.
- **Gleichzeitige Aktionen:** Bisher konnte die zweite Aktion die Warnung der ersten löschen, z. B. wenn zwei Leitungen gleichzeitig verwarnen oder Auto-Warnungen parallel laufen. Jetzt schreibt jede Aktion nur ihre eigenen Änderungen zurück.
- **Slash-Fehler:** Bisher gab es keine Antwort, der Fehlerhandler wurde nie aufgerufen. Jetzt gibt es immer eine kurze Erklärung, bei unerwarteten Fehlern mit Fehler-ID.
- **Sicherheit:** Ein Platzhalter- oder zu kurzes Session-Secret wird nicht mehr benutzt. Bewerbungs- und Dashboard-Login sind nicht austauschbar, die API-Doku ist nicht mehr öffentlich, und CSV-Exporte sind gegen Excel-Formeln geschützt.

## 3. Verworfen oder verschoben

- **K-11** verworfen nach Entscheidung E-1: Der Honeypot bleibt wie bisher.
- **K-04 nur teilweise umgesetzt:** Umgestellt sind die Dashboard-Warnung, die Warn-Rücknahme, `/warn`, die automatischen Schicht-Warnungen, der Activity-Check-Warnlauf und die Voice-Support-Übernahme. Noch direkt `save_json` nutzen etwa 31 Stellen in `webserver.py` und 2 in `cogs/pulse_suite.py` (Beförderung/Einstellung, Notizen, Bewerbungen, Meetings, Schichten). Die meisten davon speichern ohne `await` dazwischen und sind damit unkritisch. Die vollständige Prüfung und Umstellung schlage ich für Phase 2 vor.
- **K-06:** Der eigene Knopf „Erneut archivieren“ und eine zusätzliche Team-Meldung fehlen noch. Ersatz ist der vorhandene Schließen-Knopf plus Hinweis im Kanal. Eine optionale HTML-Ansicht des Transkripts ist ebenfalls verschoben.
- **K-07:** Nur die beiden im Plan genannten Views sind umgestellt. Die anderen Views sind bereits registriert: Tickets, Abmeldung, Activity-Check, Verifikation, Flaggenquiz, RP-Panel, KI-Moderation und Voice-Support (Fall- und Bewertungs-Buttons). Die Bestätigungsdialoge laufen bewusst nach 45 s ab.

## 4. Risiken und Schritte außerhalb des Codes

- **Hosting (E-5, Bot-Hosting.net):** Die ffmpeg-/opus-Änderung im Dockerfile wirkt nur bei Docker-Betrieb. Ob Bot-Hosting ffmpeg bereitstellt, zeigen nach dem Deploy der Systemstatus und `/pulse-diagnose` (Zeile „Voice-Audio“). Lokal fehlt ffmpeg, opus ist vorhanden. Das Prüfskript im gebauten Container konnte ich nicht ausführen, weil auf dem Rechner kein Docker installiert ist.
- **`SESSION_SECRET`:** Ist auf dem Host ein Platzhalter oder weniger als 32 Zeichen gesetzt, nutzt Pulse ab jetzt `.session_secret`. Alle bestehenden Dashboard-Logins werden dann einmalig ungültig. Empfehlung: ein eigenes Secret mit mindestens 32 Zeichen in `.env` setzen.
- **Nach dem Deploy im Dashboard:**
  - Unter Verwaltung → Discord-Auftritt → „Moderations-Log & RP-Panel“ den Log-Kanal und gegebenenfalls RP-Rollen wählen.
  - Bisher durfte jeder das RP-Panel bedienen; ohne freigegebene Rollen dürfen jetzt nur Admins und die Leitung.
- **Discord-Rechte:**
  - RP-Kanal: „@everyone erwähnen“ und „Nachrichten verwalten“ (für die eigene alte Ankündigung).
  - Ticket-Kanäle: „Kanäle verwalten“ und „Berechtigungen verwalten“, damit das Sperren und Umbenennen klappt.
  - Log-Kanal: Senden, Einbetten und Dateien anhängen.
- **Speicherplatz:** Ticket-Anhänge bis 8 MB werden laut E-7 unbegrenzt unter `transcripts/<id>_anhaenge/` aufbewahrt. `transcripts/` ist laut Datenlandkarte in keinem Backup, das ist Thema für Phase 2.
- **Auto-Warnläufe** schreiben jetzt pro Mitglied frisch nach. Die Warnstufe wird aus dem eigenen Stand berechnet; eine Warnung, die in derselben Sekunde parallel entsteht, kann die Stufe um eins verfehlen. Verloren geht keine Warnung.
- **Bewerbungsentwürfe** liegen unverschlüsselt im Browser-Speicher (Risiko an geteilten PCs). Die gleitende Sitzung endet spätestens 12 h nach dem Login.

## 5. Entscheidungen (beantwortet am 20.10.2026) und offene Frage

- **Transkripte im Log-Kanal:** alle hochladen, auch vertrauliche Kategorien – bleibt wie umgesetzt. Voraussetzung: den Protokollkanal „übernommene Tickets“ nur für die Leitung sichtbar halten.
- **RP-Cooldown:** 60 s **je Aktion** – „RP Stop“ direkt nach „RP Start“ ist möglich (Commit „Phase 1 Entscheidungen“, Test `test_stop_right_after_start_is_allowed`).
- **Bewerbungssitzung:** gleitend 3 h, **höchstens 12 h nach dem Discord-Login**; danach erneuter Login, der Entwurf bleibt im Browser (Test `test_renewal_never_exceeds_twelve_hours_after_login`).
- **Offen – Freigabe:** Darf ich den Branch nach `main` mergen und pushen? Bot-Hosting übernimmt `main` beim nächsten Neustart. Vorschlag laut DOC-04: danach Version 7.10.0 und CHANGELOG-Eintrag.
