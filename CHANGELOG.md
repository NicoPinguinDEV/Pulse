# Pulse TeamOS – Änderungsprotokoll

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
