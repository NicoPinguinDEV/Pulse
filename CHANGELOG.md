# Pulse TeamOS – Änderungsprotokoll

## Version 7.15.2 – Live-Konsole stabilisiert (Oktober 2026)

- **JSON-Antworten funktionieren:** Der geschützte Live-Konsolenbereich importiert JSONResponse explizit; Protokollabfrage und freigegebene Befehle stürzen dadurch nicht mehr mit NameError ab.
- **Routenprüfung aktualisiert:** Der Snapshot erfasst die im Adminbereich ergänzten Konsolenrouten und schützt sie weiterhin vor unbeabsichtigten Route-Änderungen.


## Version 7.15.1 – Webbasierte Administrator-Live-Konsole (Oktober 2026)

- **Verwaltung → Live-Konsole:** Geschützter Bereich mit automatisch aktualisierten Python-Anwendungslogs und Befehlsausgabe.
- **Sichere Befehle:** `help`, `status`, `health`, `version`, `logs`, `sync`; keine beliebige Shell-Ausführung über HTTP.
- **Schutz:** Administratorprüfung auf jeder Route, Begrenzung der Befehlsrate, streng begrenzter Logpuffer und Audit-Log für Befehle.
- **Hinweis:** Die Konsole zeigt Anwendungslogs ab Aktivierung. Vollständige Docker-Startlogs bleiben über die Serverkonsole verfügbar.


## Version 7.15.0 – Administrator-Live-Konsole (Oktober 2026)

- **Neue Verwaltungsseite:** Live-Konsole unter Verwaltung → Live-Konsole, ausschließlich für serverseitig autorisierte Administratoren.
- **Befehlszeile:** Freigegebene Befehle `help`, `status`, `health`, `version`, `logs` und `sync`; Slash-Command-Synchronisierung wird begrenzt und auditiert.
- **Live-Protokoll:** Letzte Prozess-Logmeldungen werden in einem begrenzten Ringpuffer angezeigt und automatisch aktualisiert.
- **Sicherheit:** Keine beliebige Shell-Ausführung aus dem Web-Dashboard; Eingaben werden gegen eine feste Allowlist geprüft und pro Benutzer rate-limitiert.
- **Tests:** Zugriffsschutz, Befehls-Allowlist, Version, Status, Sync-Schutz und Logpuffer werden geprüft.

## Version 7.14.7 – Automatisches VPS-Update beim Neustart (Oktober 2026)

- **GitHub wird beim VPS-Start abgeglichen:** Ein systemd-Dienst lädt `main`, übernimmt ausschließlich sichere Fast-Forward-Änderungen und baut Pulse bei neuem Code erneut.
- **Kontrollierter Start:** Docker startet den Bot nicht vor dem Update. Der systemd-Dienst startet Pulse danach und wartet auf den Healthcheck.
- **Sicherheit:** Lokale Änderungen brechen das Update ab, Runtime-Daten und die lokale Wartemusik werden vor Codeänderungen gesichert; bei einem fehlerhaften Update wird der vorherige Commit wiederhergestellt.
- **Manueller Lauf:** `systemctl restart pulse-github-update.service` startet denselben Updateablauf ohne VPS-Neustart.

## Version 7.14.6 – Manuelle Teamnamen & echter Abmeldestatus (Oktober 2026)

- **Eigene Discord-Namen bleiben erhalten:** Manuelle Nickname-Änderungen werden als neue Namensbasis übernommen, auch wenn der Rangpräfix gleich bleibt. Pulse setzt danach weiterhin den richtigen Präfix entsprechend der höchsten Rolle.
- **Abmeldezusatz an Abmeldung gekoppelt:** „| Abgemeldet“ wird anhand des Abmeldungseintrags in Pulse gesetzt oder entfernt und nicht allein aus einem frei editierbaren Nickname abgeleitet.
- **Abmeldung beenden ohne Namens-Rollback:** Beim Zurückmelden wird nur der Abmeldezusatz entfernt. Ein während der Abmeldung geänderter Name wird nicht mehr durch den alten gespeicherten Namen ersetzt.
- **Regressionstests:** Manuelle Namensänderung, Zusatz ohne echte Abmeldung und Namensänderung während einer Abmeldung.

## Version 7.14.5 – Nachholbare Warnrollen-Synchronisierung (Oktober 2026)

- **Discord-Mitglieder werden bei Cache-Lücken nachgeladen:** Vor einem Warnrollen-Abgleich versucht Pulse, das Mitglied direkt von Discord abzurufen. Bereits aus der Datenbank abgelaufene Warnungen bleiben für die Synchronisierung vorgemerkt.
- **Fehler werden nicht still vergessen:** Bei vorübergehenden Discord- oder Rollenfehlern bleibt der Abgleich offen und wird beim nächsten Kontrolllauf erneut versucht. Mitglieder, die den Server nachweislich verlassen haben, werden nachvollziehbar als übersprungen markiert.
- **Regressionstests** prüfen Cache-Lücken und erfolgreiche Wiederholung nach einem vorübergehenden Abruffehler.

## Version 7.14.4 – Zuverlässiger Ablauf von Team-Warnungen (Oktober 2026)

- **Alte Warnungen werden nicht mehr dauerhaft:** Aktive Warnungen ohne gespeichertes Ablaufdatum erhalten bei der Migration automatisch eine Frist. Ist das Ausstellungsdatum bekannt, werden sie anhand dieses Datums eingeordnet; Warnungen ohne eigene Dauer verwenden sieben Kalendertage (Ausstellungstag zählt als Tag 1). Eine Warnung vom 16.10.2026 läuft dadurch am 22.10.2026 ab.
- **Bestehende Fristen bleiben erhalten:** Gespeicherte Ablaufzeitpunkte werden nicht verändert. Bei Datensätzen, die bereits eine Dauer, aber kein Ablaufdatum enthalten, wird die gespeicherte Dauer ausgehend vom Ausstellungsdatum berücksichtigt.
- **Unbekanntes Ausstellungsdatum:** Für aktive Altwarnungen ohne lesbares Datum wird eine einmalige siebentägige Übergangsfrist ab Migration gesetzt und gespeichert, statt das Alter zu erraten.
- **Automatische Folgen:** Der bestehende Fünf-Minuten-Housekeeping-Lauf deaktiviert abgelaufene Warnungen, synchronisiert die Discord-Warnrollen und prüft temporäre Warn-Folgen. Warnungsdatensätze bleiben zur Nachvollziehbarkeit in der Historie; sie zählen nach Ablauf nicht mehr als aktiv.
- **Regressionstests:** Abdeckung für Warnungen vom 16.10., vorhandene Ablaufzeiten, vorhandene Dauer ohne Ablaufzeit und undatierte Altwarnungen.

## Version 7.14.3 – Rollenverwaltung & Team-Kick (Oktober 2026)

- **Rollenverwaltung repariert:** Discord-Rollen-IDs werden im Browser als Zeichenketten verarbeitet. Große Snowflake-IDs werden nicht länger gerundet; Auswahl, geplante Änderungen und Formularübermittlung stimmen wieder überein.
- **Einheitlicher Team-Kick:** Dashboard, freigegebener `/team-kick`/`/team-exit`, automatischer Team-Kick nach fünf Team-Warnungen und Teamler-Schutz entfernen alle verwaltbaren Rollen bis auf die Bürgerrolle `1531303733311307997` und Ex-Teamler-Rolle `1489912887949394082`.
- **Dauerhafte Entfernung bei Warnstufe 5:** Das Auslaufen der Warnungen stellt die vorherige Teamrolle nicht mehr automatisch wieder her. Eine erneute Aufnahme ins Team muss bewusst durch die Führung erfolgen.
- **Sicherheitsprüfung:** Vor dem Rollenreset prüft Pulse Rollenverwaltung und Hierarchie. Wenn eine Rolle nicht entfernt werden kann oder eine Pflichtrolle fehlt, wird der Vorgang abgebrochen und der konkrete Grund angezeigt; der Server-Kick bleibt davon getrennt.

## Version 7.14.2 – VPS-RAM-Budget (Oktober 2026)

- **Docker-Speicherbudget:** Für den VPS mit 2 GiB RAM auf 512 MiB Container-RAM und maximal 768 MiB Gesamtbudget aus RAM plus Swap angehoben.
- **RAM-Schutz und Diagnose:** Warnung bei 350 MB, kritisch bei 420 MB, Notfallreserve bei 470 MB; Erholungsschwellen entsprechend angepasst. Dashboard, Discord-Diagnose und Alerts lesen die Werte aus einer gemeinsamen Konfiguration.
- **Betriebshinweis:** 2 GiB Host-RAM liegen weiterhin unter der empfohlenen Mindestgröße von 4 GiB. Bei dauerhaft hoher Auslastung ist ein Upgrade ratsam.

## Version 7.14.1 – Betrieb & Stabilität (Oktober 2026)

- **Discord-Slash-Befehl /ultimate:** öffnet das Command Center über einen sichtbaren Link und verwendet dieselbe Dashboard-Zugriffsprüfung wie /pulse. Der Befehl ist außerdem in /pulse-hilfe aufgeführt.
- **VPS-Speicherlimit:** Docker begrenzt Pulse auf 250 MiB RAM und maximal 350 MiB Gesamtbudget aus RAM plus Swap, passend zu den vorhandenen RAM-Warnschwellen.
- **Reverse-Proxy-Schutz:** Port 25095 wird nur an localhost gebunden; öffentlicher Dashboard-Zugriff erfolgt über Caddy/HTTPS.
- **Hinweis:** Der Moderations-Log-Kanal bleibt serverseitig in „Verwaltung → Discord-Auftritt“ zu konfigurieren. Er wird bewusst nicht auf einen beliebigen Kanal fest verdrahtet.

## Version 7.14.0 – Phase 5: Dashboard & Bedienung (Oktober 2026)

Details und Testnachweise: `docs/PHASE5_BERICHT.md`, Plan: `docs/VERBESSERUNGSPLAN.md`.

> **Hinweise nach dem Update:**
> - Nach dem Login öffnet sich jetzt **„Mein Pulse“** statt des Command Centers. Wer lieber eine andere Seite möchte, wählt sie unter „Mein Pulse → Meine Startseite“.
> - Dashboard-Sitzungen enden nach **24 h ohne Aktivität** (einstellbar über die neue optionale `.env`-Variable `PULSE_SESSION_IDLE_HOURS`). Bestehende Logins bleiben gültig.
> - Wer das Team verlässt (Team-Kick, Rollen entfernt, Server verlassen), wird beim nächsten Seitenaufruf abgemeldet.
> - Abmelden geht nur noch per Knopf (POST); alte `/logout`-Links zeigen eine Bestätigung.

- **Eigene Startseite (U-02).**
- **Systemstatus (U-12):** zeigt jetzt auch Discord-Latenz und Cogs, die nicht laden. Dazu kommen fehlende Zeitzonendaten, feste Rollen/Kanäle, die es nicht mehr gibt, die Rollenhierarchie des Bots und fehlende Pflicht-Einträge in `.env` (nur Namen, nie Werte).
- **Schneller und ohne Fremd-CDN (U-03):** das Dashboard-CSS ist vorgebaut und wird von Pulse selbst ausgeliefert (40 KB, gecacht). Optisch bleibt alles gleich, das Flackern beim Laden entfällt.
- **Benachrichtigungen (U-11):** in der Inbox Bereiche abwählen, optional Discord-DM, Ruhezeiten, Filter nach Bereich.
- **Sitzungen & API-Schlüssel (U-14):**
  - „Meine Sitzungen“ zeigt Geräte und letzte Aktivität. Man kann einzelne Geräte, alle anderen oder sich überall abmelden.
  - API-Schlüssel haben Bereiche, ein Ablaufdatum, „zuletzt benutzt“ und ein Limit von 120 Anfragen pro Minute. Bestehende Schlüssel funktionieren unverändert.
- **Listen (U-05):** Audit Center und Verwarnungszentrale sind jetzt seitenweise, mit CSV-Export. Das Audit Center erreicht mehr Historie, und die Warnsuche findet auch Namen mit „&“.
- **Teamakte (U-10):** neue Zeitleiste mit Warnungen, Schichten, Tickets, Aufgaben, Ereignissen und Notizen, filterbar. Sichtbar nur nach den bisherigen Rechten (E-3).
- **Live-Ansichten (U-08):** Ticket-Warteschlange und Voice-Warteraum aktualisieren sich live mit „zuletzt aktualisiert“. Der Teamstatus fragt in Hintergrund-Tabs nicht mehr ab.
- **Bedienung (U-04, U-06, U-07, U-09):**
  - Eingaben bleiben nach einem Fehler erhalten, Doppelklicks sind gesperrt, Fehlermeldungen bleiben stehen.
  - Bessere Tastatur- und Screenreader-Bedienung, größere Touch-Ziele auf dem Handy.
  - Einheitliche Leerzustände und relative Zeiten.
- **Sicherheit (U-13):** eine einheitliche Sicherheits-Middleware mit Content-Security-Policy-Basis und Herkunftsprüfung auch ohne Origin-Header.
- **Fehler behoben:**
  - `/stats` stürzte ab, solange in der Woche noch niemand Dienstzeit hatte.
  - Die Rückfragen „Melonly-Log löschen?“ und „Schicht wirklich beenden?“ erschienen nie.
  - Meldungen nach dem Speichern gingen auf Seiten mit Sprungmarke verloren.

## Version 7.13.0 – Phase 4: Architektur & Wartbarkeit (Oktober 2026)

Details und Testnachweise: `docs/PHASE4_BERICHT.md`, Plan: `docs/VERBESSERUNGSPLAN.md`.

> **⚠️ Rechteänderung – bitte nach dem Update unter „Einstellungen & Rechte“ prüfen:**
> - Dashboard-Rechte kommen **nur noch über Teamrollen** und die ihnen zugeordneten Dashboard-Profile. Bisher wirkten die Zusatzrechte (Ankündigungen, Übergaben, Beförderungsanträge, Teamstatus, Activity Check, Melonly-Kick/Ban/BOLO) auch über Rollen, die keine Teamrolle (mehr) sind – das war eine Sicherheitslücke und ist geschlossen.
> - **Nie konfigurierte Teamrollen** geben nur noch „Dashboard sehen“; „Analytics ansehen“ (inkl. Team-Exporte) nur noch, wenn es ausdrücklich gesetzt ist (Entscheidung E-4).
> - Admins und der Serverinhaber haben weiterhin alle Rechte; die Vererbung über das Beförderungsrecht ist unverändert.

- **Ein Rechte-Modell (A-03):** eine Stelle (`pulse_permissions.py`) berechnet alle Dashboard-Rechte samt Herkunft; kein Überschreiben zur Laufzeit mehr; Test-Matrix: ohne Login liefert keine Seite Inhalt und keine schreibende Route ändert etwas.
- **Fehler behoben:** Speichern einer digitalen Zwischenrolle endete nach dem Speichern mit einer Fehlerseite und ohne Audit-Eintrag.
- **Feste IDs zentral (A-09):** alle Kanal-, Rollen- und Server-IDs in `pulse_ids.py` (Werte unverändert, per Test gesichert).
- **Start ohne Import-Seiteneffekte (A-04, Teil):** Dateien und Tabellen werden beim Start angelegt; FastAPI-`lifespan` statt veralteter Start-Ereignisse.
- **Migrationen (A-05, Grundlage):** versioniert, einmalig, Sicherung vorher, bei Fehlern vollständig zurückgerollt.
- **Qualität (A-07, A-08):** `ruff` mit konservativem Regelset; CI installiert jetzt die Abhängigkeiten und prüft mit ruff; gemeinsame Discord-Testattrappen.
- **Systemstatus:** auch die Auto-Backup-Prüfung erscheint in der Job-Übersicht.
- **Vorbereitet:** Entscheidungsvorlage für doppelte Seiten (`docs/SEITEN_ALT_NEU.md`) – noch nichts zusammengeführt.

## Version 7.12.0 – Phase 3: Performance & Stabilität (Oktober 2026)

Details und Testnachweise: `docs/PHASE3_BERICHT.md`, Plan: `docs/VERBESSERUNGSPLAN.md`.

- **Schnelleres Dashboard (P-02, P-03, P-04):** Live-Zahlen höchstens alle 15 s für alle zusammen berechnet, keine Abfragen aus Hintergrund-Tabs, Backoff bei Fehlern; Wochenstunden in einem Durchlauf statt pro Mitglied; `config.json` nur bei Änderung neu gelesen.
- **Lesen ohne Schreiben (P-05):** Seiten speichern keine Schichten mehr; vergessene Schichten werden angezeigt und im Automatiklauf beendet.
- **Event-Loop (P-01):** Verzögerung wird gemessen (Warnung ab 250 ms, Systemstatus); 30 SQLite-Verbindungen werden jetzt sauber geschlossen.
- **Hintergrundjobs (P-06, P-07):** Registry ohne eigene Schleife mit Läufen, Dauer und letztem Fehler im Systemstatus; Automationszyklus verschluckt keine Fehler mehr; Hintergrundaufgaben gehen nicht verloren.
- **Nach Reconnects (P-08):** Start-Arbeit (Warnrollen-Abgleich, Security-Check, Honeypot-Karte) nicht bei jedem Reconnect.
- **Slash-Befehle (P-09):** Sync nur bei geändertem Befehlsbaum; Knopf „Jetzt synchronisieren“ im Systemstatus; `PULSE_FORCE_COMMAND_SYNC`.
- **Fehler (P-10, P-11, P-12):** Fehlerseite mit Fehler-ID statt „Internal Server Error“; robuster Discord-Login (Rate-Limit, Ausfall, Zeitüberschreitung); `logs/pulse.log` (max. 4 MB) und die letzten Fehler im Systemstatus.
- **Tickets (P-13):** Zwei schnelle Klicks erzeugen kein zweites Ticket; indizierte Suche nach offenen Tickets.
- **Speicher (P-14):** Systemstatus zeigt gemessenen Arbeitsspeicher, Mitglieder im Cache und Presence-Status.

## Version 7.11.0 – Phase 2: Datenintegrität, Backups, Datenschutz (Oktober 2026)

Details und Testnachweise: `docs/PHASE2_BERICHT.md`, Plan: `docs/VERBESSERUNGSPLAN.md`.

- **Komplett-Backup (D-01):** ein ZIP mit allen 8 Datenbanken (konsistente SQLite-Kopie), allen JSON-Dateien, Uploads, Beweisen, Transkripten und der Discord-Struktur, mit Prüfsummen-Manifest; Größenbudget sichtbar statt stillem Auslassen; ohne `.env`/`.session_secret`. Neu unter Verwaltung → Backups.
- **Sicheres Wiederherstellen (D-02):** Probelauf mit Vorschau, Bestätigung per Eingabe, Sicherheitskopie davor, alles wird vor dem Schreiben geprüft, bei Fehlern automatischer Rollback; `/ultimate/backup` überschreibt die laufende Datenbank nicht mehr per Dateitausch.
- **Kein Blockieren (D-03):** Backups laufen im Hintergrund-Thread, Discord-Daten werden im Event-Loop gelesen.
- **Aufbewahrung & Restore-Test (D-04):** Auto-Komplett-Backup im bestehenden Backup-Lauf, 7 täglich / 4 wöchentlich / 6 monatlich, wöchentlicher Restore-Test, Gesamtgrenze `PULSE_BACKUP_TOTAL_MAX_MB`.
- **Ein Datenbankpfad (D-05):** `PULSE_DB_PATH` gilt für alle Module.
- **Datenschutz (D-08):** Teamakte, Suche, Teamliste und `/ultimate` zeigen Warnungen nur der Person selbst und Verwarnungsberechtigten, Notizen nur Notizberechtigten und der Leitung (E-3); Datenschutzhinweis im Bewerbungsformular; neue Seite Verwaltung → Datenschutz mit Aufbewahrungsfristen (Standard aus) und Auskunfts-Export je Discord-ID.
- **Zeitstempel (D-09):** ein toleranter Parser für alle gespeicherten Formate; Bewerbungs-Erinnerungen rechnen wieder in Berliner Zeit.
- **Konsistenz-Diagnose (D-10) und Wachstumsüberwachung (D-07, Teil):** Systemstatus meldet verwaiste Schichten/Tickets/Voice-Fälle, unpassende Warnrollen, fehlerhafte Abmeldungen, übergroße Datendateien sowie letztes Backup und Restore-Test.
- **Neue optionale Einstellungen:** `PULSE_BACKUP_MAX_MB` (500), `PULSE_BACKUP_TOTAL_MAX_MB` (4000).

## Version 7.10.0 – Phase 1: Kritische Fehler & Sicherheit (Oktober 2026)

Details und Testnachweise: `docs/PHASE1_BERICHT.md`, Plan: `docs/VERBESSERUNGSPLAN.md`.

- **Bewerbungsportal (K-01):** Sitzung 3 h gleitend (höchstens 12 h nach dem Login), Entwurf wird im Browser gespeichert, Fehlerseiten und erneuter Login führen zurück zum ausgefüllten Formular.
- **RP-Verwaltung (K-02):** nur Admins, Leitung und freigegebene Rollen; kein Löschen fremder Nachrichten mehr, nur die eigene letzte Ankündigung; 60 s Abstand je Aktion; Uhrzeit wird geprüft; kein Ausweichen in den falschen Kanal.
- **Sicherheit (K-03, K-14, K-17):** Platzhalter- oder zu kurzes Session-Secret wird nicht genutzt; Dashboard- und Bewerbungs-Login sind getrennte Token-Typen; CSV-Exporte gegen Formel-Injection geschützt; `/docs`, `/redoc`, `/openapi.json` nicht mehr öffentlich.
- **Gleichzeitige Aktionen (K-04):** Warnungen, Rücknahmen, Schicht- und Activity-Check-Warnungen sowie Voice-Support-Übernahmen schreiben nur noch ihre eigenen Änderungen zurück.
- **Datendateien (K-05):** beschädigte JSON-Datei wird gesichert (`.corrupt-…`) statt mit Standardwerten überschrieben; Schreiben mit `fsync`.
- **Tickets (K-06):** ohne gespeichertes Transkript wird der Kanal nicht gelöscht (umbenannt und gesperrt); Anhänge bis 8 MB werden gesichert; Transkript zusätzlich im Ticket-Log-Kanal.
- **Buttons nach Neustart (K-07):** Fall-Buttons aus `/pulse-ticket` und Büro-Anfragen funktionieren nach Neustarts, auch auf alten Nachrichten.
- **Fehlermeldungen (K-08, K-09):** jeder Slash-Fehler wird beantwortet (Fehler-ID); Bestätigungsdialoge antworten sofort, führen genau eine Aktion aus, prüfen Rechte erneut und zeigen den Ablauf an.
- **Betrieb (K-10, K-12, K-13, K-15, K-16):** ffmpeg/libopus im Docker-Image und Audio-Diagnose; Moderations-Log-Kanal im Dashboard einstellbar; `discord.py>=2.6` und `tzdata`; Laufzeitdaten aus Git/Image ausgeschlossen; Routen-Snapshot-Test.
- **Neue Einstellungen:** Verwaltung → Discord-Auftritt → „Moderations-Log & RP-Panel“.

## Verifikation neuer Mitglieder und Datenreparatur (Oktober 2026)

- Neues Verifikationssystem (`cogs/verification.py`, `pulse_verification.py`): Unverifiziert-Rolle beim Beitritt, persistenter Button vergibt die Bürgerrolle (sichere Reihenfolge, idempotent, Rechte-/Hierarchieprüfung, Fehlerprotokoll). Konfiguration, Vorschau und Panel-Veröffentlichung unter Verwaltung → Discord-Auftritt. Standardmäßig aus.
- Einmalige, idempotente Reparatur von Dashboard-Abmeldungen mit vertauschten Spalten (`pulse_absence_repair.py`), mit Dateisicherung vor der ersten Änderung.
- Namenspräfix „Unverifiziert ✗“ stapelt nach der Verifikation nicht.

## Discord-Auftritt, Willkommen/Abschied und Verwaltungsbereich (Oktober 2026)

- Zentrales Embed-Designsystem (`pulse_embed_design.py`) mit semantischer, im Dashboard änderbarer Farbpalette; alle fest verdrahteten Embed-Farben in 19 Dateien umgestellt.
- Neues Willkommen-/Abschiedssystem (`cogs/welcome.py`, `pulse_welcome.py`): Profilbild oben rechts, eigenes Banner unten (Upload oder https-URL), Platzhalter mit Validierung, kontrollierte Erwähnung, Testnachricht.
- Neuer Verwaltungsbereich `/admin` mit Sidebar-Umschalter „Team | Verwaltung“; Discord-Auftritt unter `/admin/discord`. Bestehende URLs bleiben gültig.
- Schnellsuche zeigt Verwaltungslinks nur noch berechtigten Personen.
- Details: `docs/PULSE_DISCORD_DESIGN.md`.

## Version 7.8.27 – Alte Ankündigungen finden, erklären und wiederholen

- Neuer Slash-Command-Bereich /ankuendigung mit Admin-Einrichtung des ursprünglichen Ankündigungskanals.
- Alte Nachrichten und Embed-Inhalte lassen sich anhand von Stichwörtern durchsuchen; der Suchbereich ist zwischen 100 und 5.000 Nachrichten einstellbar.
- /ankuendigung erklaeren bereitet die wichtigsten Aussagen in einfacher Sprache auf, verlinkt das Original und ergänzt keine neuen Serverregeln.
- /ankuendigung wiederholen veröffentlicht den ausgewählten Originaltext mit vorhandenen Embeds/Anhängen erneut, markiert ihn sichtbar als Wiederholung und löst keine Erwähnungen aus.
- Die Funktionen brauchen keine zusätzliche KI-API und laden den Nachrichtenverlauf erst auf ausdrückliche Anfrage.
- Neue Utility- und Regressionstests decken die Suche nach Fahrzeugfarben, verständliche Aufbereitung und Markdown-Bereinigung ab.

## Version 7.8.23 – Übernehmen, Übertragen und Abschließen von Voice-Supportfällen

- Supportmeldungen enthalten nun dauerhafte Buttons zum Übernehmen, Übertragen und Beenden eines Falls.
- Übernehmen und Übertragen ist nur für die konfigurierte Support-/Bereitschaftsrolle möglich. Der Supporter muss bereits in einem normalen Sprachkanal sein; der Bürger wird dorthin verschoben.
- Für den Bürger werden temporär Anzeigen, Verbinden, Sprechen, Sprachaktivierung ohne Push-to-Talk und Streamen freigeschaltet. Beim Übertragen werden die Rechte im vorherigen Supportkanal zurückgesetzt.
- Beim Beenden wird der Bürger aus dem Sprachkanal entfernt und sein ursprünglicher individueller Berechtigungs-Overwrite wiederhergestellt. Verlässt der Bürger den Supportkanal selbst, wird der Fall automatisch geschlossen und die Rechte werden bereinigt.
- Das Abschlussprotokoll wird im Kanal 1531443370172485703 als Embed mit Case-ID, Erstellungszeit, Nutzer, Supporter, Dauer und Abschlussgrund veröffentlicht.
- Die Bereitschaftsnachricht wird im Verlauf wiedererkannt, aktualisiert und bei vorhandenen Duplikaten auf eine Nachricht reduziert.

## Version 7.8.22 – Konfigurierbarer Voice-Support mit Schicht-Bereitschaft

- Eigener Dashboard-Menüpunkt Sprach-Support mit separater Konfigurations- und Fallübersicht.
- Wenn ein Mitglied während der eingestellten Öffnungszeiten den Voice-Warteraum betritt, legt Pulse einen privaten Support-Sprachraum an und verschiebt das Mitglied hinein. Leere vom Bot erstellte Räume werden wieder entfernt.
- Unterstützt eigene, passende leere Prefix-Kanäle mit temporären Berechtigungen; die ursprünglichen Rechte werden beim Schließen wiederhergestellt.
- Konfigurierbare Wochentage/Zeiten, Support- und Bereitschaftsrollen, Benachrichtigungskanal, Kategorie, Kanal-Prefix, private Notiz-Threads, Öffnen/Schließen-Meldungen, Bewertungen und optionale Wartemusik-Datei.
- Die Bereitschaftsrolle wird mit dem bestehenden Pulse-Schichtsystem synchronisiert: Start/Fortsetzen vergibt sie, Pause/Ende entfernt sie. Der Status im Check-in-Kanal wird automatisch aus den realen laufenden Dashboard-Schichten aktualisiert.
- Regressionstests decken Öffnungszeiten und die Erkennung des aktiven Dienststatus ab.

## Version 7.8.21 – Zuverlässige Antworten in beiden KI-Chats

- Der öffentliche KI-Support und die Team-KI haben keine gemeinsame globale 2-Sekunden-/8-Sekunden-Sperre mehr; Anfragen werden pro Kanal und Nutzer begrenzt, sodass Aktivität in einem Chat den anderen nicht still blockiert.
- Während einer API-Pause oder bei fehlender Konfiguration zeigt Pulse eine begrenzte Statusmeldung statt Fragen kommentarlos zu ignorieren.
- Nachrichten, die während einer laufenden Anfrage eintreffen, warten auf die interaktive Anfrage-Sperre und bekommen nach einem zwischenzeitlichen API-Ausfall eine Statusmeldung.
- Vorübergehende HTTP-5xx-/Verbindungsfehler der KI-Moderation pausieren nur noch die Moderation und blockieren nicht automatisch beide interaktiven KI-Chats. Gemeinsame Pausen bleiben für geteilte Quotenlimits (429) und Zugangsdatenfehler (401/403) aktiv.
- Fehler im Support-Nachrichtenablauf werden protokolliert und führen – soweit Discord das Senden erlaubt – zu einem klaren Hinweis statt zu einem stillen Abbruch.

## Version 7.8.20 – Ticket-Panels reparieren sich automatisch

- Wenn eine gespeicherte Ticket-Panel-Nachricht gelöscht wurde, veröffentlicht Pulse beim nächsten Refresh automatisch eine Ersatznachricht im konfigurierten Kanal und speichert die neue Nachrichten-ID.
- Das gilt sowohl für das ältere Standard-Panel als auch für eigenständige Panels aus dem Panel-Builder. Berechtigungs- und API-Fehler werden weiterhin protokolliert, statt endlos neue Nachrichten zu erstellen.
- Die vorhandenen Korrekturen für Probezeit-Auswahl (3 oder 7 Tage), Warn-Gültigkeitsdauer und automatische Bereinigung abgelaufener Warnungen/Folgesanktionen bleiben erhalten.

## Version 7.8.19 – Warn-Sperren und Backups repariert

- Absturz bei der automatischen Entfernung abgelaufener Warn-Folgerollen behoben: Jede gespeicherte Folgerolle wird jetzt anhand ihrer eigenen Rollen-ID aufgelöst. Fehlerhafte Datensätze bleiben zur Prüfung erhalten, statt die komplette Bereinigung abzubrechen.
- Manuelle und automatische Server-Backups funktionieren wieder, wenn ein Server-Icon ausgelesen wurde; der zuvor falsche Variablenname hat den Erstellungsprozess mit NameError abgebrochen.
- Regressionstests sichern beide Korrekturen ab. Die aktuelle Namenspräfix-Synchronisierung hält Datei-I/O aus dem Discord-Event-Loop heraus, damit der Gateway-Heartbeat bei großen Mitgliedslisten nicht blockiert wird.

## Version 7.8.18 – Supportfälle zwischen Teammeetings

- Die Teamliste und jede Teamakte zeigen jetzt die Zahl der abgeschlossenen Pulse-Tickets pro Teammitglied seit dem letzten bestätigten Teammeeting-Zeitraum.
- Unter Teambesprechung kann die Teamleitung nach dem Meeting den bisherigen Zeitraum archivieren und eine neue Zählung starten.
- Die abgeschlossene Wochenzählung wird intern gespeichert; die aktuelle Periode ist startinklusive und endexklusiv, damit Tickets nicht in zwei Zeiträumen gezählt werden.
- Es werden echte, abgeschlossene Tickets gezählt, die bei Abschluss einem Teammitglied zugeordnet waren; offene oder nicht übernommene Tickets erhöhen den Wert nicht.

## Version 7.8.17 – Wiederherstellung mit robuster API-Kompatibilität
