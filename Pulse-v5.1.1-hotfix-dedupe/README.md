# Pulse TeamOS 5.1.1

Pulse ist ein internes Team-Management-System für Discord: Bot, Webpanel, Schichten, Tickets, Bewerbungen, LOA, Meetings, Aufgaben und Team-Workflows greifen ineinander.

## Highlights

- modernes Operations Center mit Live-Kennzahlen
- globale Suche und `Ctrl + K` Command Palette
- zentrale Pulse Inbox
- Schichtsystem mit Wochenziel, Pausen und Historie
- Teamstatus + Schicht-Übergaben
- Aufgaben-Kanban mit Prioritäten, Deadlines und Historie
- professionelles Discord-Ticketsystem mit Claim, Freigabe, Priorität, Transcript und Bewertung
- sichere Discord-OAuth-Bewerbung
- Bewerbungs- und Beförderungs-Freigabecenter
- Teamakte pro Mitglied
- Meeting-Historie, Kalender und Schulungen
- Achievement-System und Analytics
- Team-Ankündigungen und automatische Benachrichtigungen
- Backups inklusive zentraler Pulse-Daten und Transcripts
- System-/Health-Check mit Cog-Prüfung
- Dark/Light Theme, Responsive Mobile-Ansicht und PWA-Basis
- signierte Sessions, Same-Origin-Guard und Security-Header

## Installation

1. Python 3.11+ verwenden; der aktuelle Hosting-Stand wurde mit Python 3.14 getestet.
2. `python -m pip install -r requirements.txt` ausführen.
3. `.env.example` nach `.env` kopieren und Werte eintragen.
4. Optional lokal testen: `python tests/smoke_test.py`.
5. Bot starten: `python main.py`.

## Discord OAuth

Für den normalen Team-Login muss `DISCORD_REDIRECT_URI` exakt auf `/callback` zeigen. Für Bewerbungen wird `DISCORD_APPLICATION_REDIRECT_URI` verwendet und sollte exakt auf `/apply/callback` zeigen.

Beispiel:

```text
https://DEINE-DOMAIN/callback
https://DEINE-DOMAIN/apply/callback
```

## Wichtige `.env`-Werte

- `DISCORD_TOKEN` – Bot-Token
- `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` – Discord OAuth
- `DISCORD_GUILD_ID` – Zielserver
- `DISCORD_REDIRECT_URI` – Team-Login Callback
- `DISCORD_APPLICATION_REDIRECT_URI` – Bewerbungs Callback
- `PUBLIC_BASE_URL` – öffentliche HTTPS-Adresse des Panels
- `PULSE_DB_PATH` – optionaler Pfad zur zentralen Pulse-Datenbank (Standard: `pulse.db`)
- `SESSION_SECRET` – dauerhaftes zufälliges Session-Secret
- `TEAM_UPDATE_CHANNEL_ID` – optionaler Kanal für Team-Updates
- `WEEKLY_GOAL_HOURS` bzw. `weekly_goal_hours` – Wochenziel je nach vorhandener Konfiguration

## Bot-Rechte

Je nach aktivierten Modulen werden unter anderem Rollen verwalten, Mitglieder verschieben, Nachrichten verwalten und Kanäle verwalten benötigt. Die Bot-Rolle muss über den Teamrollen liegen, die Pulse verwalten soll.

## Daten und Backups

Pulse 5.1.1 verwendet aus Kompatibilitätsgründen weiterhin die vorhandenen JSON-/SQLite-Dateien und zusätzlich `pulse.db` für die neuen TeamOS-Module. `pulse.db` wird beim Start automatisch initialisiert.

Ein Panel-Backup enthält die alten Panel-Daten, Pulse-Daten, LOAs und – soweit verfügbar – Ticket-Transcripts. Die Wiederherstellung stellt Daten und Historien wieder her; Discord-Rollen und -Kanäle werden bewusst nicht automatisch neu angelegt.

## Bot-Befehle

- `/pulse` – kompakte Teamübersicht
- `/teamstatus` – Dienst- und Erreichbarkeitsstatus
- `/mytasks` – persönliche Aufgaben
- `/teamannounce` – Team-Ankündigung erstellen
- `/teamtask` – Aufgabe zuweisen
- `/taskdone` – Aufgabe per ID erledigen
- `/memberinfo` – kompakte Teamakte
- `/pulsehealth` – Systemstatus für Admins

## Fehlerdiagnose

Wenn der Bot startet, aber ein Cog nicht geladen wird, zeigt `/pulsehealth` die Anzahl geladener Cogs. Das Webpanel unter **Systemstatus** listet zusätzlich fehlende Cogs, Datenbank, OAuth, Session-Secret, Backup-Verzeichnis und freien Speicher.

## Sicherheit

Die Sessions sind serverseitig signiert und enthalten ein Ablaufdatum. State-Parameter schützen OAuth vor CSRF. State-changing Browser-Anfragen werden anhand des Origin-Headers geprüft. Private Dashboard-Seiten werden nicht über den Service Worker offline gecacht.

## Upgrade von älteren Pulse-Versionen

Vor dem Update ein Backup erstellen und die `.env` beibehalten. Alte JSON-/SQLite-Dateien nicht löschen. Beim ersten Start werden die neuen Pulse-Tabellen automatisch angelegt.
