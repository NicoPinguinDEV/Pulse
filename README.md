# ⚡ Pulse TeamOS

Pulse ist eine Discord-Teamverwaltung mit Web-Dashboard für Moderation, Dienstzeit, Activity Checks, Tickets, Recruiting, Aufgaben, Schulungen und Audit-Funktionen.

## Kernfunktionen

- Discord-OAuth-Dashboard mit signierten Sessions
- Konfigurierbarer Voice-Support mit gemeinsamem Warteraum zu jeder Uhrzeit (ohne neue Kanäle beim Beitritt), einmaliger Eröffnungsansage pro Supportfall und anschließender Endlosschleife der im Dashboard ausgewählten Wartemusik-Datei, automatischer Wiederherstellung aus untätigen Voice-Verbindungen, persistentem Live-Status für Öffnungszeiten/Warteschlange/laufende Gespräche/Bereitschaft, einmaliger Ansage bei geschlossener Supportzeit, Fall-Buttons zum Übernehmen/Übertragen/Beenden, temporären Voice-Rechten, automatischer Bereinigung bei freiwilligem Verlassen und Abschlussprotokoll in Kanal `1531443370172485703`.
- Honeypot-Schutzkanal `1560397792751657053`: auffälliges Warn-Embed und automatischer Ban bei Nachrichten im verbotenen Kanal
- Konfigurierbares AutoMod im Adminpanel: Spam/Nachrichtenflut, Massen-Erwähnungen, Discord-Einladungen sowie eigene Wort- und Domainfilter; die erste Regelverletzung wird gelöscht und per DM verwarnt, eine Wiederholung innerhalb einer Stunde führt zu einem 10-Minuten-Timeout. Für die Spam-Erkennung bleiben Nachrichteninhalte ausschließlich kurzzeitig im begrenzten Arbeitsspeicher; sie werden nicht dauerhaft gespeichert oder in Moderationsmeldungen ausgegeben. Maßnahmen werden mit Regel, IDs und Ergebnis ohne Nachrichteninhalt im Pulse-Aktivitätsprotokoll festgehalten.
- Anti-Nuke im Adminpanel: überwacht ausgewählte Audit-Log-Ereignisse wie Massenbans/-kicks, Löschungen, kritische Rollenberechtigungs-/Positionsänderungen, Kanalberechtigungen, Webhooks und neue Bots; harmlose Kanalnamen- oder Themenänderungen lösen keinen Sperrversuch aus. Bei konfigurierbarer Aktionsschwelle alarmiert Pulse und sperrt den Akteur automatisch. Serverinhaber, Pulse und ausdrücklich freigestellte Nutzer/Rollen sind geschützt. Erfasste Aktionen und erfolgreiche Sperren erscheinen im Aktivitätsprotokoll. Standardmäßig deaktiviert; benötigt „Audit-Log ansehen“ und „Mitglieder bannen“. Anti-Nuke reagiert auf Audit-Logs nach der Discord-Aktion, verhindert sie nicht im Voraus und kann gelöschte Inhalte oder Strukturen nicht wiederherstellen.
- Geführter Admin-Einrichtungsassistent unter `/setup` mit sicheren Prüfschritten für Discord/OAuth, Rollen, AutoMod, Anti-Nuke und Backups; Änderungen bleiben immer eine bewusste Administratoraktion
- Live-Serverstatistiken als nicht betretbare Sprachkanäle: Mitglieder, Boosts, Bots und Rollen werden in einer bestehenden Kategorie automatisch aktualisiert
- Teamler-Schutz: erkennt massenhafte manuelle Warnungen, sperrt Warn-/Teamaktionen vorübergehend, protokolliert Vorfälle und entfernt bei wiederholtem Missbrauch automatisch Teamrollen (kein Server-Kick)
- Automatische Discord-Namenspräfixe: die feste Pulse-Teamhierarchie entscheidet über den höchsten Rang (auch wenn Bürger-/VIP-Rollen in Discord höher einsortiert sind); Labels u. a. `PL ✗`, `Stv.PL ✗`, `L.DK ✗`, `Stv.SL ✗`, `L.MA ✗`, `L.Supervisor ✗`, `Sr.Mod ✗`; standardmäßig `Bürger ✗`, individuelle Präfixe für VIP- und andere Rollen, automatische Aktualisierung bei Rollenwechseln und Synchronisierung bestehender Mitglieder; manuelle Präfixe bleiben möglich
- Teamliste mit Wochenstunden, Dienststatus, LOA, Warnungen und aktuellem Discord-Presence-Status
- Schichtstart und Activity-Check-Bestätigung bleiben auch während einer eingetragenen Abmeldung möglich. Eine gestartete Schicht beendet die Abmeldung nicht; die Teamliste zeigt den tatsächlichen Dienststatus zusätzlich zur hinterlegten Abmeldung. Nicht bestätigte Activity Checks während einer aktiven Abmeldung führen weiterhin nicht zu einer automatischen Warnung.
- Activity Check mit eingefrorenem Teilnehmer-Snapshot
- Warnsystem 1/2/3/4/5 inklusive Discord-Warnrollen und Rücknahme-Historie
- Beförderungen mit Pflicht-Probezeit (3 oder 7 Tage), automatische Rückstufung auf die vorherige Teamrolle bei Warn während der Probezeit und Team-Kick bei Warn für Test Supporter
- Team-Warnungen mit konfigurierbarer Laufzeit (1–365 Tage), automatischem Ablauf und Warnrollen-Synchronisierung; temporäre Sperrrollen, normale Warn-4-Downranks und Warn-5-Team-Kicks werden aufgehoben, sobald keine aktiven Warnungen mehr bestehen (Test-Supporter-Kick in der Probezeit bleibt bestehen)
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
- Vollständige Discord-Struktur- und Pulse-Backups mit Rollen/Kanalberechtigungen, Servereinstellungen, Mitgliedsrollen, SQLite/JSON-Daten, Ticket-Transkripten, validiertem JSON-Download/-Upload, automatischer Sicherheitskopie, sicherem additive Restore-Modus und optionaler exakter Wiederherstellung; automatische isolierte Restore-Tests prüfen mindestens wöchentlich die Sicherbarkeit
- Sprach-Support: Teamler mit konfigurierter Supportrolle können wartende Fälle auch außerhalb einer aktiven Dienstschicht übernehmen; für die Fallübernahme müssen sie weiterhin einem geeigneten Support-Sprachkanal beitreten.
- Team-Kalender mit Monatsraster, Monatsnavigation und dauerhafter Event-Erstellung (Titel, Beginn/Ende, Kategorie, Ort und Beschreibung); vorhandene Meetings und Abmeldungen werden ebenfalls angezeigt.
- Pulse Intelligence (seit 7.9.0): lokaler regelbasierter Copilot ohne externe KI-API, System-Zwilling mit Abhängigkeits-/Auswirkungsübersicht, read-only Was-wäre-wenn-Simulationen für Ticketlast/Team-Ausfall/RAM sowie Workflow-Release-Sandbox mit JSON-Schema-Prüfung ohne Ausführung.
- Digitale Dashboard-Zwischenrollen direkt in den Pulse-Pro-Einstellungen: interne Rechteprofile erstellen, konfigurieren und vorhandenen Discord-Teamrollen zuordnen, ohne Discord-Rollen oder Serverrechte zu ändern; plus granulare Permission-Overrides und Workflow Center mit Bedingungen/Aktionsketten

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
- AutoMod und Anti-Nuke sind standardmäßig deaktiviert. Vor dem Aktivieren Ausnahmen und Alarmkanal prüfen; automatische Anti-Nuke-Sperren können bei legitimen Massenaktionen auslösen. Die Schwelle, Zeitspanne, Ereignisse und Vertrauensrollen sind konfigurierbar.

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
- Bei der ersten Annahme einer Bewerbung vergibt Pulse ausschließlich die Bewerberolle `1496070682126188604` und lädt zum verpflichtenden Bewerbungsgespräch ein. Die Team-Einstiegsrolle wird erst nach dokumentiertem Gespräch vergeben; anschließend wird die Bewerberolle entfernt.
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

## Eigenes VPS-Hosting

Die Docker-Konfiguration und sichere Umzugs-Checkliste liegen in [`VPS_DEPLOYMENT.md`](VPS_DEPLOYMENT.md). Der VPS nutzt weiterhin dieses GitHub-Repository als Codequelle; GitHub Actions kann exakt den Commit ausrollen, für den die Sicherheits- und Qualitätstests auf `main` erfolgreich waren. Manuelle Deployments prüfen ebenfalls den erfolgreichen CI-Lauf. Deployments laufen nacheinander, brechen bei lokalen versionierten VPS-Änderungen ab, verhindern automatische Rücksetzungen auf ältere Commits und stellen nach einem fehlgeschlagenen Gesundheitscheck den vorherigen Stand wieder her. Docker zeigt den Zustand über `/healthz` an; die Prüfung bestätigt Dashboard, Datenbank, Discord-Bot und Serververbindung. Runtime-Konfiguration, Mitglieder-/Teamdaten, Datenbanken, Backups und Transkripte müssen separat übertragen und gesichert werden.

## Entwicklung

Vor dem Deploy:

```bash
python -m ruff check .
python -m compileall -q .
python tests/feature_smoke.py
python tests/smoke_test.py
python -m unittest discover -s tests -p 'test_*.py'
```

GitHub Actions führt die Prüfungen für Pushes und Pull Requests gegen `main` aus. `pip-audit` prüft
Python-Abhängigkeiten bei Änderungen und wöchentlich; CodeQL analysiert Python und JavaScript/TypeScript,
und Gitleaks scannt Commits auf versehentlich eingecheckte Geheimnisse. Dependabot erstellt wöchentlich
gruppierte Aktualisierungs-PRs für Python-Abhängigkeiten und GitHub Actions; größere Versionssprünge bleiben
zur gezielten Prüfung separate PRs. Der Systemstatus überwacht außerdem bereits Job-Fehler, Datenbank-/Dateiwachstum,
RAM und wöchentliche Backup-Restore-Tests.
