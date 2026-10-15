# ⚡ Pulse TeamOS

Pulse ist eine Discord-Teamverwaltung mit Web-Dashboard für Moderation, Dienstzeit, Activity Checks, Tickets, Recruiting, Aufgaben, Schulungen und Audit-Funktionen.

## Kernfunktionen

- Discord-OAuth-Dashboard mit signierten Sessions
- Teamliste mit Wochenstunden, Dienststatus, LOA, Warnungen und aktuellem Discord-Presence-Status
- Activity Check mit eingefrorenem Teilnehmer-Snapshot
- Warnsystem 1/2/3/4/5 inklusive Discord-Warnrollen und Rücknahme-Historie
- Roblox-Spielersuche mit ID-Autovervollständigung
- Tickets mit Kategorien, Zuständigkeiten, Prioritäten und Transkripten
- Bewerbungen mit laufbahnspezifischen Online-Eignungstests (Low Team: 12 Fragen/75 %, High Team: 22 Fragen/82 %, Leitungsebene: 30 Fragen/90 %), anspruchsvollen Fallbeispielen zu IC-/RP- und Discord-Regelwerk sowie serverseitiger Bewertung
- Einstellungen, Besprechungen und Team-News
- Aufgabenverwaltung, Pulse Inbox und Benachrichtigungen
- Pulse Suite mit Teamakten, Dienstplan, Recruiting, Audit und Sicherheitsprüfungen
- TeamOS 2.0 mit Mitarbeiter-Timeline, Score-Verlauf, Achievements und On-/Offboarding
- 4-Augen-Freigaben für Beförderungen inklusive optionaler Discord-Rollen-Synchronisierung
- Team-News mit Lesebestätigung, Ideenboard, Team-Ziele und globale Suche
- Support/SLA-Übersicht, Aufgaben-2.0-Board und Performance-Leaderboards
- Monatsberichte, CSV/JSON-Export und detailliertes Health/Security Center mit RAM-, KI-Cache- und Warteschlangen-Monitoring
- Wiederverwendete HTTP-Verbindungspools für KI-/Social-Integrationen und Größenlimits beim Feed-Streaming
- Backup/Restore für SQLite, Laufzeitdaten und Anhänge mit Größenlimit
- Granulare Permission-Overrides und Workflow Center mit Bedingungen und Aktionsketten

## Ressourcen- und RAM-Schutz

Pulse ist für kleine Hosts mit **maximal 250 MB RAM** ausgelegt. Der laufende Systemmonitor zeigt den aktuellen und den bisherigen Spitzenverbrauch. Ein RAM-Schutz nutzt die bereits laufende Automationsschleife und startet keinen zusätzlichen Hintergrundprozess:

- **180 MB:** Warnung
- **210 MB:** kritisch; entbehrlicher Roblox-Lookup-Cache wird geleert und die Python-Garbage-Collection ausgeführt
- **230 MB:** Notfallwarnung
- **220 MB:** Notfallstufe fällt erst unter diesen Wert zurück; das reduziert Warnungs-Flattern
- **200 MB:** kritische Stufe fällt erst unter diesen Wert zurück
- **unter 160 MB:** Rückkehr in den Normalbereich wird protokolliert
- **RAM-Trend:** ein kleiner, begrenzter Messring erkennt anhaltendes Wachstum (mindestens 20 MB/Stunde) und meldet es höchstens einmal pro Stunde

Die Warnschwellen sind Schutzmaßnahmen, keine harte Betriebssystem-Grenze und keine Garantie, dass ein Host-Prozess bei jedem Lastmuster unter 250 MB bleibt. Der Hosting-Anbieter bleibt die letzte Instanz bei Speicherlimits. Der Roblox-Lookup-Cache ist auf 300 Einträge und fünf Minuten Lebensdauer begrenzt; sein HTTP-Verbindungspool wird wiederverwendet und beim Shutdown geschlossen.

Der optionale Wert `PULSE_SYSTEM_ALERT_CHANNEL_ID` in der `.env` kann auf einen Textkanal zeigen, in dem Pulse einmalig beim Wechsel der RAM-Warnstufe meldet. Bleibt der Wert leer, werden die Meldungen nur in der Hosting-Konsole protokolliert.

Administratoren können in Discord `/pulse-diagnose` verwenden, um RAM, Datenbanken, Warnrollen und die wichtigsten Team-, Büro- und Social-Kanäle zu prüfen. Der Befehl verändert keine Teamdaten.

## Kritische Teamaktionen

- `/team-kick` und `/team-exit` entziehen nur die konfigurierten Teamrollen. Die Person bleibt im Discord-Server.
- `/kick` und `/server-exit` führen einen echten Server-Kick erst nach einer zweiten, anderen berechtigten Führungskraft aus.
- Server-Kick und Team-Offboarding prüfen Gründe, Berechtigungen und Rollenhierarchie. Vorgänge werden vor Ausführung atomar beansprucht, um doppelte Freigaben zu vermeiden.
- `/promote` und `/demote` ersetzen konfigurierte Rangrollen durch genau eine Zielrangrolle und erhalten alle anderen Rollen.
- Die Kernregeln sind in `pulse_safety.py` ohne externe Abhängigkeiten testbar; `tests/test_pulse_safety.py` prüft Hysterese, Rangrollenplanung sowie die Trennung zwischen Server-Kick und Team-Offboarding.

## Start

1. Python 3.14+ installieren.
2. Abhängigkeiten installieren:
   `pip install -r requirements.txt`
3. `.env.example` nach `.env` kopieren und Discord-/Dashboard-Werte setzen.
4. Den Bot starten:
   `python main.py`

Der Webserver wird vom Bot-Prozess gestartet. Für Hosting hinter einem Reverse Proxy sollte `PUBLIC_BASE_URL` auf die öffentliche HTTPS-Adresse zeigen.

## Öffentliches Bewerbungsportal

- Die externe Bewerbungsseite ist unter `/apply` erreichbar. Bewerber müssen sich per Discord verifizieren und die konfigurierte Serverrolle besitzen.
- Der Zugang wird bei Formularaufruf und erneut beim Absenden serverseitig geprüft.
- Neue Bewerbungen erscheinen unter `/applications` und lösen eine Benachrichtigung im konfigurierten Recruiting-Kanal mit gezieltem Rollen-Ping aus.
- `DISCORD_REDIRECT_URI` muss exakt einer im Discord Developer Portal unter **OAuth2 → Redirects** gespeicherten Adresse entsprechen (typischer Callback-Pfad: `/callback`). Der Bewerbungslogin verwendet diese vorhandene Adresse ebenfalls, statt standardmäßig eine neue `/apply/callback`-Adresse vorauszusetzen.
- `PUBLIC_BASE_URL` sollte die öffentlich erreichbare kanonische HTTPS-Adresse ohne Pfad sein. `DISCORD_APPLICATION_REDIRECT_URI` ist nur ein Fallback, wenn `DISCORD_REDIRECT_URI` leer ist; nach einer Domain- oder HTTPS-Änderung müssen Hosting-Variable und registrierte Discord-Redirect-URI übereinstimmen.

## Pulse-eigenes Ticketsystem

- `/ticketpanels_setup` richtet native Pulse-Ticketpanels in sechs konfigurierten Discord-Kanälen ein.
- Öffnungen werden in Kanal `1543914075308818503`, Übernahmen in Kanal `1535025111722098778` protokolliert.
- Das eigene Dashboard `/tickets` zeigt offene, übernommene und geschlossene Pulse-Tickets und erlaubt berechtigten Teammitgliedern, Tickets zu übernehmen, freizugeben oder mit Pflichtgrund zu schließen.
- Die bestehenden Ticketfunktionen in Discord bleiben aktiv: private Ticketkanäle, Rollenberechtigungen, Priorität, Übernahme, Freigabe, Transkript und Schließen.
- Alle Ticketdaten liegen in der Pulse-Datenbank. Dafür wird kein externer Ticketbot benötigt.

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
