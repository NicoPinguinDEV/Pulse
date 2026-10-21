# Phasenbericht Phase 5 – Dashboard & Bedienung

Branch `verbesserung/phase-5-dashboard` (von `main` = 7.13.0), **nicht gepusht, nicht gemergt, nicht deployt**. Stand 21.10.2026.

## 1. Erledigt

| ID | Commits | Testnachweis |
|---|---|---|
| U-13 Sicherheits-Header | 1ec9e5e | `tests/test_security_headers.py` – ein Header-Satz, CSP-Basis, fremde Origin/Referer → 403, Logout nur per POST |
| U-04 / U-06 / U-07 / U-09 | 763d587 | `tests/test_ui_basics.py` – Meldungen, Formularschutz, Fokus, Touch-Ziele, relative Zeiten, Leerzustände |
| U-02 Eine Startseite | f8cd03f | `tests/test_start_page.py` (2) |
| U-12 Systemstatus erweitert | 1f8a02c | `tests/test_system_checks.py` (7) |
| Fix /stats (Division durch 0) | 408c952 | `tests/test_stats_page.py` – vorher 500, jetzt 200 |
| U-03 Tailwind ohne Play-CDN | e0c8551, 0556674 | `tests/test_static_css.py` (3), Vorher/Nachher-Screenshots |
| U-11 Benachrichtigungen | d1c0f59 | `tests/test_notify_prefs.py` (9) |
| U-14 Sitzungen & API-Schlüssel | b81ab8f | `tests/test_sessions_api_keys.py` (13) |
| U-05 Listen | da9ba54 | `tests/test_lists.py` (6) |
| U-10 Zeitleiste | cd99f17 | `tests/test_timeline.py` (3) |
| U-08 Live-Ansichten | 0556674 | `tests/test_live_views.py` (5), im Browser gemessen |

**Gates** (venv `Q:\venv`, Python 3.14, ohne Discord):
- `python -m compileall -q .` → OK
- `python tests/feature_smoke.py` → OK
- `python tests/test_pulse_intelligence.py` → OK
- `python tests/smoke_test.py` → OK
- `python -m ruff check .` → „All checks passed!“
- `python -m unittest discover -s tests` → **483 Tests OK** (nach Phase 4: 419)
- Bei jedem Gate-Lauf ab U-05 geprüft, dass keine Laufzeitdatei (`*.db`, `*.json` im Repo) verändert wird → keine Änderung.

**Browser-Prüfung** (lokale Vorschau `tools/preview_server.py`, Temp-Daten, kein Discord) auf Desktop und 375 px: /me, /dashboard, /stats (hell/dunkel), /calendar, /system, /pulse-inbox, /me/sessions, /member/{id}, /tickets, /voice-support, /team-status.

**Review** durch einen Subagenten mit frischem Kontext: **1 hoher, 2 mittlere, 6 geringe Befunde**, alle bis auf zwei Hinweise behoben in **245bc2d** (danach 483 Tests OK, alle Gates grün):
- **Hoch:** Endlose Weiterleitung nach Sitzungsende. `/` prüfte nur die Signatur, die Startseite zusätzlich die Sitzung, und beide schickten den Browser hin und her. Jetzt gibt es eine gemeinsame Prüfung, `/` zeigt die Login-Seite und löscht den toten Cookie. Der Regressionstest folgt der ganzen Kette.
- **Mittel:** „Alle anderen Geräte abmelden“ ließ ältere Cookies ohne Sitzungs-ID gültig. Jetzt werden sie mit beendet.
- **Mittel:** Die Zeitleiste zeigte keine Ereignisse *über* die Person, etwa Teamschutz-Fälle oder Suite-Audits. Jetzt erscheinen sie; Warn- und Notiz-Ereignisse nur mit E-3-Recht.
- **Gering:**
  - Ein Login in derselben Sekunde wie „überall abmelden“ war ungültig.
  - Relative Zeiten erschienen erst nach 60 s.
  - Der Teamstatus-Timer lief nach Sitzungsende weiter.
  - Die Routen-Doku stufte die API-v2-Routen als ungeschützt ein; geschützt waren sie.
  - Der `.env`-Check meldete `SESSION_SECRET` fälschlich als fehlend.
- **Älterer Fehler, beim Review gefunden:** Die Rückfragen „Melonly-Log löschen?“ und „Schicht wirklich beenden?“ wurden nie gezeigt, weil das Skript vor den Formularen lief. Jetzt kommen sie (im Browser geprüft).
- **Nicht geändert, nur Hinweis:**
  - Die Formular-Wiederherstellung (U-04) hält Freitexte bis zum Schließen des Tabs im Browser-Speicher, wenn nach dem Absenden auf eine andere Seite weitergeleitet wird.
  - Wer nach einem Rechteverlust `/cockpit` als Startseite gewählt hat, landet auf einer 403-Seite mit Link zum Dashboard.

## 2. Verhalten vorher → nachher

- **Startseite (U-02):** Nach dem Login landete jeder im Command Center. Jetzt öffnet sich „Mein Pulse“, oder die Seite, die man unter „Mein Pulse → Meine Startseite“ gewählt hat.
- **Systemstatus (U-12):** Neu sichtbar sind Discord-Latenz, Cogs, die nicht laden (mit Fehlertyp), fehlende Zeitzonendaten, feste Rollen/Kanäle, die es auf dem Server nicht mehr gibt, und ob die Bot-Rolle hoch genug steht und „Rollen verwalten“ hat. Dazu kommt, welche Pflicht-Einträge in `.env` fehlen. Gezeigt werden nur Namen, nie Werte.
- **/stats (Fix):** Stürzte ab, solange in der laufenden Woche noch niemand Dienstzeit hatte (z. B. montags früh). Jetzt zeigt die Seite 0 %.
- **Aussehen (U-03):** Das Dashboard lädt sein CSS nicht mehr bei jedem Seitenaufruf von einem fremden CDN und baut es im Browser zusammen. Pulse liefert jetzt eine fertige Datei (40 KB) selbst aus, der Browser hält sie im Cache. Optisch ist alles gleich, nur das kurze Flackern beim Laden entfällt. Fehlt die Datei einmal, nutzt Pulse wie früher das CDN und schreibt eine Warnung ins Log.
- **Benachrichtigungen (U-11):** In der Inbox lässt sich einstellen, welche Bereiche man sehen will (Tickets, Bewerbungen, Abmeldungen …). Abgewählte Meldungen landen still als „gelesen“ in der Inbox, gehen also nicht verloren. System-Hinweise kommen immer. Optional gibt es jede Meldung zusätzlich als Discord-DM, mit Ruhezeit (z. B. 22–7 Uhr ohne DMs). Die Inbox lässt sich nach Bereich filtern. Ohne Einstellung bleibt alles wie bisher.
- **Sitzungen (U-14):** Unter „Mein Pulse → Meine Sitzungen“ sieht man, auf welchen Geräten man angemeldet ist. Man kann einzelne Geräte, alle anderen oder sich überall abmelden. Nach 24 h ohne Aktivität endet eine Sitzung von selbst (einstellbar über `PULSE_SESSION_IDLE_HOURS`). Wer das Team verlässt (Team-Kick, Rollen weg, Server verlassen), wird beim nächsten Seitenaufruf abgemeldet und kommt auch auf Seiten ohne Einzelrecht nicht mehr hinein. Bisher reichte dort ein noch gültiger Cookie bis zu 7 Tage lang.
- **API-Schlüssel (U-14):** Neue Schlüssel bekommen Bereiche (Übersicht, Teamliste) und eine Gültigkeit (30/90/365 Tage oder unbefristet). Die Liste zeigt „zuletzt benutzt“. Pro Schlüssel sind höchstens 120 Anfragen pro Minute erlaubt. Bestehende Schlüssel funktionieren unverändert weiter, mit Vollzugriff.
- **Listen (U-05):** Audit Center und Verwarnungszentrale zeigen jetzt 50 Einträge pro Seite mit Seitennavigation statt alles auf einmal. Beide haben einen CSV-Export, der die Suche berücksichtigt. Das Audit Center erreicht dadurch mehr Historie als vorher (bis 5.000 statt 1.000/500). Die Suche in der Verwarnungszentrale findet jetzt auch Namen mit „&“ oder Anführungszeichen.
- **Teamakte (U-10):** Unten in der Teamakte steht jetzt eine Zeitleiste mit allem chronologisch: Warnungen und Rücknahmen, Schichten, Tickets, Aufgaben, Ereignisse wie Beförderungen und Notizen. Man kann nach Art filtern. Warnungen und Notizen sieht nur, wer sie auch bisher sehen durfte (E-3).
- **Live-Ansichten (U-08):** Ticket-Zentrale und Voice-Support zeigen oben einen Live-Streifen: offene Tickets, Tickets ohne Bearbeiter, Personen im Warteraum und „zuletzt aktualisiert“. Er aktualisiert sich alle 15 s, aber nur bei sichtbarem Tab, und meldet eine gestörte Verbindung. Der Teamstatus fragte bisher auch in Hintergrund-Tabs alle 10 s Discord-Daten ab. Jetzt passiert das nur noch bei sichtbarem Tab.

## 3. Verworfen oder verschoben

- **U-01 Navigation aufräumen:** wartet auf E-6 (`docs/SEITEN_ALT_NEU.md`).
- **Seiten zusammenlegen** (Systemseiten aus U-12, vier Personen-Seiten aus U-10, Bewerbungs-Historie/Melonly-Listen aus U-05): ebenfalls nach E-6. Die neuen Bausteine (`pulse_system_checks`, `pulse_timeline`, `pulse_lists`) sind dafür schon wiederverwendbar.
- **U-04 Fehler direkt am Feld:** verschoben. Dafür müsste jede Formular-Route Feldfehler statt einer Meldung zurückgeben, also ein großer Umbau. Eingaben bleiben aber erhalten, Fehler bleiben sichtbar und Doppelklicks sind gesperrt.
- **U-03 PRO_CSS/Inline-JS in statische Dateien und CSP-Nonces:** verschoben. Mehrere Tests und Seiten erwarten die Inline-Blöcke, das ist ein eigener Schritt.
- **U-08 SSE:** verworfen zugunsten von sichtbarkeitsabhängigem Polling. SSE hält pro offenem Tab eine Verbindung dauerhaft offen, das passt nicht zum 250-MB-Limit (E-5).

## 4. Risiken und Schritte außerhalb des Codes

- **Neue optionale `.env`-Variable:** `PULSE_SESSION_IDLE_HOURS` (Standard 24). Ohne Eintrag gilt der Standard.
- **Abmeldung bei Team-Austritt:** Die Sitzungen werden nur widerrufen, wenn das Mitglied im Cache gefunden wird oder die Mitgliederliste vollständig geladen ist. Ein unvollständiger Cache direkt nach einem Neustart meldet also niemanden ab. Ein Zugriff wird in diesem Moment trotzdem verweigert, das war schon bisher so.
- **Nach dem Deploy:** Alle bestehenden Logins bleiben gültig. Die Sitzungsliste füllt sich erst mit neuen Logins. Bis dahin steht dort „älterer Login“, und „Überall abmelden“ funktioniert trotzdem.
- **Discord-DMs (U-11):** gehen nur an Personen, die das selbst einschalten. Wer DMs vom Server gesperrt hat, bekommt keine; die Inbox-Meldung bleibt.
- **Neue Klassen im Dashboard:** Wer neue Tailwind-Klassen einbaut, muss `python tools/build_css.py <pfad-zur-tailwindcss.exe>` ausführen und `static/pulse.css` mit committen. Die CLI liegt nicht im Repo.
- **Lokale Laufzeitdatei:** Ein Testlauf hat einmal 60 Testzeilen in die lokale `pulse_suite.db` geschrieben, bevor der DB-Patch griff (die Tabelle war vorher leer). Die Zeilen habe ich nach einer Sicherung entfernt, Zustand und Hashkette sind wie vorher. Der Test isoliert die Datei inzwischen, und jeder spätere Gate-Lauf hat bestätigt, dass keine Laufzeitdatei mehr angefasst wird. Produktion war nicht betroffen.

## 5. Offene Fragen

- **E-6** (Tabelle „alt → neu“ in `docs/SEITEN_ALT_NEU.md`) blockiert weiterhin U-01 und das Zusammenlegen der Seiten.
- Ist 24 h Leerlauf-Timeout für das Dashboard passend, oder lieber kürzer, z. B. 12 h?
- Merge nach `main` als **Version 7.14.0** mit CHANGELOG-Eintrag?
