# Pulse TeamOS – Betriebshandbuch

Stand: 21.10.2026 · main meldet 7.14.0. Die strengere Deploy-Variante in Draft-PR #40 ist erst nach CI-Prüfung und Merge aktiv. Von diesem Branch aus wurde kein Deployment ausgeführt.

## 1. Vor dem Start

1. Python 3.14 und Abhängigkeiten installieren: python -m pip install -r requirements.txt.
2. Eine private .env im Arbeitsverzeichnis erstellen; .env.example enthält nur Musterwerte. Nie .env, .session_secret, Laufzeitdatenbanken oder Teamdaten in Git übernehmen.
3. DISCORD_TOKEN, DISCORD_GUILD_ID, OAuth-Daten/Callback-URL und PUBLIC_BASE_URL korrekt setzen. SESSION_SECRET muss mindestens 32 zufällige Zeichen haben; ein Platzhalter löst einen Hinweis und den Fallback auf .session_secret aus.
4. Discord Developer Portal: benötigte Intents und Bot-Berechtigungen prüfen. /system bzw. die Systemstatus-Seite zeigt unter anderem fehlende IDs, Rollen-Hierarchie, Zeitzonendaten und nicht geladene Cogs.

## 2. Start und Gesundheitscheck

- Lokal: python main.py.
- Container: docker compose up -d --build.
- Port standardmäßig 25095 (über SERVER_PORT konfigurierbar).
- GET /healthz bestätigt, dass der Web-Prozess erreichbar ist, aber nicht automatisch, dass Discord, Rollen und Voice-Audio vollständig funktionieren.
- Containerlogs: docker compose logs --tail=200 pulse. Tokens und .env-Werte nie in Logs oder Issues kopieren.

## 3. Backups und Restore

1. Dashboard-Route /backups als Admin öffnen.
2. Vor riskanten Änderungen ein vollständiges Backup erstellen und prüfen, ob es ohne Warnungen abgeschlossen wurde.
3. In der Backup-Liste Probelauf / Wiederherstellen öffnen. Ein fehlerhaftes Backup wird vor dem Restore blockiert.
4. Erst nach Review des Diffs und Eingabe von WIEDERHERSTELLEN bestätigen. Pulse erstellt zuvor eine Sicherheitskopie des aktuellen Stands.
5. Nach Restore den Bot kontrolliert neu starten und Dashboard, Tickets, Bewerbungen, Warnrollen sowie Voice-Support prüfen. Das Ergebnis des letzten Restore-Tests wird im Systemstatus angezeigt.

Transkripte und Anlagen haben gemäß E-7 unbegrenzte Aufbewahrung; Anlagen bis 8 MB werden lokal gesichert. Datenschutz-Bereinigung bleibt standardmäßig deaktiviert, bis sie ausdrücklich aktiviert wird.

## 4. Deployment und Rollback

Wichtig: Der veröffentlichte Deploy-Workflow deployt derzeit origin/main direkt. Die strengere Variante in Draft-PR #40 verwendet die SHA eines erfolgreichen CI-Laufs, erzeugt ein Pre-Deploy-Backup, prüft danach GET /healthz und versucht bei Build-/Health-Fehlern auf den vorherigen Commit zurückzurollen. Solange der Draft nicht gemergt ist, darf nicht davon ausgegangen werden, dass diese Absicherung im Produktivbetrieb aktiv ist.

Nach Freigabe der Deploy-Änderung:

1. Prüfen, dass der erfolgreiche Pulse Python Syntax Check zum vorgesehenen main-Commit gehört.
2. Das Pre-Deploy-Backup muss vor dem git reset erstellt werden. Wenn das Backup fehlschlägt oder der freie Speicher nicht reicht, muss der Deploy abbrechen.
3. Nach dem Container-Neustart muss GET /healthz lokal auf dem VPS innerhalb der Frist erfolgreich antworten.
4. Bei Build-/Health-Fehlern wird auf den vorherigen Commit zurückgestellt und der Container neu gebaut. Backup-Pfad und Resultat stehen in den Deployment-Logs.
5. Danach Systemstatus-Seite und Hostinglogs kontrollieren. Ein erfolgreicher Healthcheck allein beweist nicht, dass jeder Discord-Cog funktioniert.

Manueller Notfall-Rollback: aktuellen Commit notieren (git rev-parse HEAD), vor dem Zurückstellen Laufzeitdaten sichern und den letzten nachweislich funktionierenden Commit wiederherstellen. Keine git clean- oder Datenlöschbefehle gegen das Arbeitsverzeichnis verwenden.

## 5. Wartung und Fehlersuche

- Bot offline oder Slash-Befehle fehlen: Gateway-Status, Token, Cog-Ladefehler und Command-Sync im Systemstatus prüfen.
- Rollenänderung funktioniert nicht: Bot-Rolle muss über der zu ändernden Rolle stehen; Manage Roles muss vorhanden sein. IDs nicht aus Screenshots ableiten.
- Voice-Support ohne Audio: Warteraum-ID, Voice-Rechte, ffmpeg/Opus-Prüfung und Audio-Datei kontrollieren. Kein neues Sprachchannel-Verhalten als Workaround hinzufügen.
- Beschädigte JSON-Daten: Pulse sollte die Datei sichern und nicht still einen leeren Standard speichern. *.corrupt-*-Sicherungen bis zur Ursachenprüfung nicht löschen.
- Datenbankprobleme: Bot stoppen, Dateikopie erstellen, Integrität und Backups prüfen; keine DB-Dateien löschen, um den Start zu erzwingen.
- RAM-Budget: 250 MB bleibt verbindlich. Die Warnschwellen 180/210/230 MB beachten; keine zusätzlichen Dauerschleifen oder großen dauerhaften In-Memory-Caches hinzufügen.
- Ein zentraler Dashboard-Wartungsmodus (F-04) ist noch nicht implementiert. Bis dahin Schreibarbeiten mit kontrollierter Betriebspause und geprüftem Backup ausführen.

## 6. Freigabegrenzen

- Kein Merge nach main und kein Deployment ohne ausdrückliche Freigabe.
- Nie echte .env-Inhalte, OAuth-Secrets, Discord-Tokens, Transkripte oder private Teamakten in Doku, Issues oder Logs kopieren.
- URLs, Slash-Commands, custom_ids und Laufzeit-Datenschlüssel müssen rückwärtskompatibel bleiben.
