# Pulse TeamOS – Änderungsprotokoll

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
