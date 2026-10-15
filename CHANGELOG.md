# Pulse TeamOS – Änderungsprotokoll

## Version 7.7.9 – Verbindliches Bewerbungsgespräch
- Die erste Annahme einer Bewerbung ist jetzt eine Einladung zum verpflichtenden Bewerbungsgespräch und keine Einstellung.
- Dabei wird ausschließlich die Bewerberolle `1496070682126188604` vergeben. Es wird noch keine Team-Einstiegsrolle vergeben, keine Teamakte auf Einstellung gesetzt und keine Teamzugehörigkeit gestartet.
- Eingeladene Bewerber bleiben unter `/applications` in der aktiven Übersicht. Erst über die separate Aktion „Gespräch geführt – einstellen“ kann eine Team-Einstiegsrolle nach dokumentierter Gesprächsnotiz vergeben werden.
- Nach einer Einstellung wird die Bewerberolle entfernt; alle anderen nicht betroffenen Discord-Rollen bleiben erhalten. Eine Ablehnung nach dem Gespräch entfernt die Bewerberolle ebenfalls.
- DM, Team-Update, Bewerbungsstatus und Audit-Protokoll unterscheiden Einladung zum Gespräch von der endgültigen Einstellung.

## Version 7.7.8 – Automatische Einstellungsrolle
- Bei einer angenommenen Bewerbung vergibt Pulse zusätzlich die feste Discord-Rolle `1496070682126188604` zur gewählten Team-Einstiegsrolle.
- Die automatische Rolle wird vorab auf Existenz, Verwaltbarkeit und Bot-Rollenhierarchie geprüft. Fehlt sie oder kann der Bot sie nicht vergeben, wird die Einstellung mit einer klaren Fehlermeldung nicht stillschweigend als erfolgreich markiert.
- Die zusätzliche Rolle bleibt beim Austausch bisheriger Team-Einstiegsrollen erhalten und wird in Bewerbungsakte, Team-Update, DM und Audit-Protokoll aufgeführt.
- Regressionstests sichern die automatische Rollenvergabe ab.

## Version 7.7.7 – Anspruchsvolle Eignungstests
- Low Team: 12 schwierige Fragen, Bestehensgrenze 75 %.
- High Team: 22 schwierige Fragen, Bestehensgrenze 82 %.
- Leitungsebene: 30 sehr schwierige Fragen, Bestehensgrenze 90 %.
- Die Fragen verwenden konkrete Fallbeispiele aus dem IC-/RP-Regelwerk und dem Discord-Regelwerk: unter anderem NLR, Fear-RP/NVL, Meta-/Powergaming, Safezones, Peacetime, Geiselnahmen, Medics, Verkehr, Support, Datenschutz, Doxing, Discord-Tickets, interne Leaks und Berechtigungen.
- Antwortoptionen werden beim Anzeigen gemischt; der Lösungsschlüssel wird nicht in das HTML geschrieben. Bewertung und Pflichtfragenzahl werden serverseitig anhand der gewählten Laufbahn bestimmt.
- Testauswertungen speichern zusätzlich Themenbereich und Regelstellen für eine nachvollziehbare Sichtung in der Bewerbungsakte.

## Version 7.7.6 – Discord-OAuth für Bewerbungen
- Der Bewerbungslogin nutzt standardmäßig dieselbe, bereits registrierte Callback-URL wie das Dashboard. Dadurch ist keine zusätzliche Discord-Redirect-URI nur für `/apply/callback` nötig.
- Der gemeinsame `/callback`-Endpunkt unterscheidet den Bewerbungslogin anhand des separaten OAuth-State-Cookies und gibt Bewerber sicher an `/apply` zurück, ohne ihnen eine Dashboard-Session zu erteilen.
- `DISCORD_APPLICATION_REDIRECT_URI` bleibt als Fallback für Deployments verfügbar, die keine `DISCORD_REDIRECT_URI` gesetzt haben.
- Regressionstests decken die Callback-Auswahl und den getrennten Bewerbungs-Session-Ablauf ab.

## Version 7.7.5 – Bewerbungsportal

- Das externe Bewerbungsformular unter `/apply` ist jetzt ausschließlich für Mitglieder mit Discord-Rolle `1531303733311307997` freigeschaltet.
- Die Rollenzugehörigkeit wird nach Discord-OAuth sowohl beim Öffnen des Formulars als auch serverseitig beim Absenden geprüft. Manipulierte Formularfelder umgehen die Prüfung nicht; bei nicht prüfbarer Mitgliedschaft wird fail-closed abgebrochen.
- Eingereichte Bewerbungen werden weiterhin in `applications.json` gespeichert und sind danach im geschützten Pulse-Bereich unter `/applications` sichtbar.
- Nach erfolgreicher Speicherung sendet Pulse eine Team-Benachrichtigung in Kanal `1558776676731719720` und pingt gezielt Rolle `1531300183088824340`. Die Benachrichtigung enthält Bewerber, Laufbahn, Testergebnis und einen Link zur Bewerbungsübersicht, aber nicht den privaten Motivationstext.
- Benachrichtigungsfehler löschen oder verlieren keine bereits gespeicherte Bewerbung; der Fehler wird in der Hosting-Konsole protokolliert.
- Die Entscheidungsoberfläche wird auch für Teamrollen mit explizitem Recht `can_manage_applications` angezeigt, nicht nur für Beförderungsberechtigte oder Administratoren.
- Neue Regressionstests prüfen die Rollen-Gate auf GET/POST, die Benachrichtigungsziele und die eingeschränkten Discord-Mentions.

## Version 7.7.4

### Stabilität und Betrieb
- Das Dashboard verwendet dieselbe zentrale Versionsquelle wie Pulse Pro.
- Beim Beenden des Bots wird der eingebettete Uvicorn-Webserver kontrolliert beendet; bei einem festhängenden Shutdown wird der Task nach einem begrenzten Zeitfenster abgebrochen.
- Die Webseite stellt ein lokales Favicon bereit, statt auf die automatische Browseranfrage mit einer 404-Antwort zu reagieren.
- Die Teamliste vergleicht alle sichtbaren Embed-Bestandteile, bevor eine Discord-Nachricht bearbeitet wird.
- Bei Discord-HTTP-429-Limits liest Pulse `Retry-After`, wiederholt höchstens einen begrenzten Versuch und beendet den aktuellen Durchlauf bei längeren Limits, statt weitere API-Aufrufe zu stapeln.
- Die Abmeldungsverwaltung prüft Nickname-Berechtigungen und Rollen-Hierarchie vor API-Aufrufen. Wiederholte, vorhersehbar verbotene Änderungen werden nicht bei jedem Abgleich erneut angefordert. Eine geänderte Rollen-/Rechtekonfiguration lässt die Prüfung wieder anlaufen.
- Die Abmeldungsliste überspringt identische Embed-Updates, um unnötige PATCH-Anfragen zu reduzieren.
- Formularaktionen der Website stellen die Scrollposition auf derselben Seite wieder her.

### Rechte und Sicherheit
- Der Rechte-Reset deaktiviert die gespeicherten Pulse-Berechtigungen für Rollen und markiert den Reset als aktiv.
- Solange dieser Reset aktiv ist, werden neue ausgewählte Teamrollen nicht automatisch mit Standardrechten versehen.
- Die automatische Vergabe empfohlener Rechteprofile ist während des aktiven Resets gesperrt. Ein Administrator kann später Rechte gezielt an einzelnen Rollen in deren Rechtekarten setzen.
- Serverinhaber und echte Discord-Administratoren behalten den technischen Adminzugriff. Das ist getrennt von den gespeicherten Pulse-Rollenrechten.

### Tests
GitHub Actions prüft Syntax, bestehende Feature-Regressionen, den Smoke-Test und die Unit-Tests. Die aktuellen Prüfungen für Version 7.7.4 sind erfolgreich.

## Update-Hinweise für Bot-Hosting
1. Repository-Branch `main` abrufen und den Bot-Prozess neu starten.
2. Nach dem ersten Start als Serveradministrator einmal die Pulse-Einstellungen öffnen. Falls der gespeicherte Berechtigungsstand noch eine ältere Profilversion besitzt, wird der Sicherheitsreset dort automatisch ausgeführt.
3. In den Einstellungen prüfen, dass die normalen Rollenrechte leer sind. Änderungen am produktiven Discord-Server und die tatsächliche Verfügbarkeit aller Funktionen müssen anschließend live im eigenen Server geprüft werden.
