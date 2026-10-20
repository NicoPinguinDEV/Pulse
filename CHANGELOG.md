# Pulse TeamOS – Änderungsprotokoll

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
