# ⚡ Pulse TeamOS

Pulse ist eine Discord-Teamverwaltung mit Web-Dashboard für Moderation, Dienstzeit, Activity Checks, Tickets, Recruiting, Aufgaben, Schulungen und Audit-Funktionen.

## Kernfunktionen

- Discord-OAuth-Dashboard mit signierten Sessions
- Teamliste mit Wochenstunden, Dienststatus, LOA, Warnungen und aktuellem Discord-Presence-Status
- Activity Check mit eingefrorenem Teilnehmer-Snapshot
- Warnsystem 1/2/3 inklusive Discord-Warnrollen und Rücknahme-Historie
- Roblox-Spielersuche mit ID-Autovervollständigung
- Tickets mit Kategorien, Zuständigkeiten, Prioritäten und Transkripten
- Bewerbungen, Einstellungen, Besprechungen und Team-News
- Aufgabenverwaltung, Pulse Inbox und Benachrichtigungen
- Pulse Suite mit Teamakten, Dienstplan, Recruiting, Audit und Sicherheitsprüfungen
- TeamOS 2.0 mit Mitarbeiter-Timeline, Score-Verlauf, Achievements und On-/Offboarding
- 4-Augen-Freigaben für Beförderungen inklusive optionaler Discord-Rollen-Synchronisierung
- Team-News mit Lesebestätigung, Ideenboard, Team-Ziele und globale Suche
- Support/SLA-Übersicht, Aufgaben-2.0-Board und Performance-Leaderboards
- Monatsberichte, CSV/JSON-Export und detailliertes Health/Security Center
- Backup/Restore für SQLite, Laufzeitdaten und Anhänge mit Größenlimit
- Granulare Permission-Overrides und Workflow Center mit Bedingungen und Aktionsketten

## Start

1. Python 3.14+ installieren.
2. Abhängigkeiten installieren:
   `pip install -r requirements.txt`
3. `.env.example` nach `.env` kopieren und Discord-/Dashboard-Werte setzen.
4. Den Bot starten:
   `python main.py`

Der Webserver wird vom Bot-Prozess gestartet. Für Hosting hinter einem Reverse Proxy sollte `PUBLIC_BASE_URL` auf die öffentliche HTTPS-Adresse zeigen.

## Discord-Voraussetzungen

Der Bot benötigt je nach aktivierten Modulen unter anderem die Intent-Berechtigungen für Members, Presences und Message Content. Für automatische Rollenänderungen muss die Bot-Rolle über den Team- und Warnrollen stehen und über „Rollen verwalten“ verfügen.

## Daten

Laufzeitdaten werden lokal in SQLite-Datenbanken und JSON-Dateien gespeichert. Diese Dateien sind absichtlich nicht für das Git-Repository vorgesehen. Regelmäßige Backups sollten außerhalb des Containers bzw. Hosts aufbewahrt werden.

## Entwicklung

Vor dem Deploy:

```bash
python -m compileall -q .
python tests/feature_smoke.py
python tests/smoke_test.py
```

GitHub Actions führt dieselben Prüfungen automatisch für Pushes und Pull Requests gegen `main` aus.
