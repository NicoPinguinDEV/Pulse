# Pulse TeamOS — vollständige Entwickler-Übergabe für Claude Code

Stand dieser Zusammenfassung: **18. Oktober 2026**. Zusammengeführt aus dem öffentlichen Repository `NicoPinguinDEV/Pulse` auf `main`, der aktuellen Projekt-README sowie bereits bestätigten Anforderungen und Fehlerberichten des Betreibers. Diese Datei ist Kontext, kein Ersatz für die Prüfung des aktuellen Codes.

## 1. Repository-Stand und Versionswahrheit

- Repository: https://github.com/NicoPinguinDEV/Pulse
- Branch: `main`
- `pulse_version.py` meldet aktuell **7.9.7**.
- Zuletzt geprüfter Commit: `b1019dcd49cbefc582157e11638491c9ce5a2e99` — „Measure container RAM for Pulse memory guard“ (18.10.2026).
- Unmittelbar davor wurden Voice-Support-Interaktions-Timeouts korrigiert und Release 7.9.7 veröffentlicht.
- Zu den jüngsten Änderungen zählen außerdem die Roblox-Spielerverlaufssuche in Melonly und die URL-Behandlung von Spielernamen mit Unterstrichen.
- **Dokumentationsabweichung:** `CHANGELOG.md` endet im Kopfbereich bei 7.8.27; die README beschreibt Pulse Intelligence teilweise als 7.9.0. Nicht daraus ableiten, dass der laufende Code noch diese Version hat. Erst `pulse_version.py`, Git-Log und Code prüfen. CHANGELOG/README als mögliche Wartungsaufgabe notieren, aber nicht blind nebenbei umschreiben.

## 2. Produktziel und Arbeitsstil des Betreibers

Pulse ist ein eigenständiges TeamOS für **© BOCHUM RP | V1**: Discord-Bot, Web-Dashboard, Team- und Bewerbungsverwaltung, Dienst-/Schichtsystem, Ticket- und Voice-Support, Moderation, Logs, Backups, Suche und Betriebsdiagnose.

Der Betreiber möchte ein professionelles System, das der Führungsebene gezeigt und im laufenden Serverbetrieb verwendet werden kann. Wünsche sind typischerweise End-to-End-Aufträge. Erwartung:

- Wenn er sagt, etwas sei kaputt oder solle erweitert werden, nach Codeprüfung möglichst direkt reparieren/implementieren und nicht nur eine Liste mit Ideen liefern.
- Vorhandene Funktionen nicht entfernen oder durch eine vereinfachte Demo ersetzen.
- Das bestehende Pulse-Dashboard ausbauen. Es soll **keine** GalaxyBot-Abhängigkeit entstehen. Ein benutzerfreundlicher Panel-Builder wie bei GalaxyBot ist als Vorbild für die Bedienung okay.
- Möglichst ressourcenschonend und ohne kostenpflichtiges KI-Guthaben arbeiten.
- Sprache der Kommunikation: Deutsch. Erkläre bei Bedarf klar, welche Dateien geändert wurden und wie er es testet.
- Nicht nach Dingen fragen, die bereits in dieser Übergabe oder im Repository stehen. Wenn Details unklar sind, erst Code und aktuelle Config untersuchen; nur bei echten Blockern gezielt nachfragen.

## 3. Architektur und Einstiegspunkte

### Start und Hosting

- `main.py`: lädt `.env` vor dem Import von Dashboard-/DB-Modulen, erstellt `CustomBot`, aktiviert Intents `members`, `presences`, `message_content`, setzt eine begrenzte Discord-Message-Cache-Größe, lädt alle `cogs/*.py`, synchronisiert Slash Commands, startet `pulse_ultimate` und den Uvicorn-Webserver im selben Prozess. Beim Shutdown wird der Webserver sauber beendet.
- `SERVER_PORT` defaultet auf `25095`; `PANEL_TIMEZONE` defaultet laut `.env.example` auf `Europe/Berlin`.
- Laufzeit: Python 3.14+, `discord.py`, FastAPI, Uvicorn. Abhängigkeiten sind in `requirements.txt`.
- Deployment: `Dockerfile`, `compose.yaml`, `.github/workflows/`, `VPS_DEPLOYMENT.md`. GitHub Actions hat Tests und optionales VPS-Deployment; Deploy-Secrets werden nicht benötigt, um die Tests auszuführen.
- Dashboard und Bot laufen als ein Dienst. Ein Fehler beim Laden eines Cogs darf nicht versehentlich dazu führen, dass andere Funktionen entfallen; beim Ändern des Loader-/Startup-Flows die Startdiagnose testen.

### Wichtige Quelldateien / Verantwortungen

- `webserver.py`: sehr großes FastAPI-Dashboard und Server-API. Enthält viele Routen, Dashboard-Ansichten, Auth-/Session-Logik und Serveraktionen. Vor Änderungen relevante Route, Template/HTML-Builder, Form-Felder, Validierung und Berechtigungsprüfung einzeln suchen. Ein umfassender Rewrite ist unnötig riskant.
- `pulse_db.py`: SQLite-/JSON-nahe Persistenz und Datenzugriffe. Datenkompatibilität beachten.
- `pulse_features.py`, `pulse_next.py`, `pulse_pro.py`, `pulse_ultimate.py`, `pulse_intelligence.py`: Kernfunktionen, Pro-/Suite-/Automation-/Intelligence-Logik. Erst Aufrufer und Überschneidungen untersuchen, bevor Logik dupliziert wird.
- `pulse_safety.py`: kleine dependency-freie Sicherheitsregeln, unter anderem RAM-Hysterese, Ersetzen von Team-Rangrollen und Unterscheidung `team-exit` vs. `server-exit`.
- `pulse_ai_resilience.py`: Verhalten bei KI-API-Ausfällen und Rate-Limits.
- `pulse_support_rules.py`, `pulse_voice_support_utils.py`, `pulse_shift_utils.py`, `pulse_activity_utils.py`, `pulse_nickname_utils.py`, `pulse_announcement_utils.py`: kleine gemeinsame Fach-/Hilfslogik. Vor paralleler Neuentwicklung prüfen.
- `career_applications.py`: Bewerbungslogik.
- `cogs/voice_support.py`: Sprach-Support; wichtige Regressionen sind Musik/Ansage, Wartebereich, Berechtigungen, Fallübernahme und Abschluss.
- `cogs/tickets.py`: Discord-Ticket-Funktionen und Panel-Einrichtung.
- `cogs/activity_check.py`: Activity-Check.
- `cogs/teamliste.py`: Teamliste/-Status.
- `cogs/abmeldung.py`: Abmeldungen/LOA.
- `cogs/buero_system.py`: Büro-/Warteraumlogik.
- `cogs/ai_moderation.py`: optionale KI-Prüfung eingehender Textnachrichten, menschliche Review nötig.
- `cogs/support_assistant.py`: optionaler KI-Support.
- `cogs/pulse_suite.py`, `cogs/pulse_pro.py`: zusätzliche Dashboard-/TeamOS-Funktionen.
- `cogs/role_nickname_prefixes.py`: Namenspräfix-/Rollenhierarchie-Synchronisierung.
- `cogs/announcement_archive.py`: alte Ankündigungen suchen, erklären und erneut veröffentlichen.
- `cogs/backups.py`: Backups.
- `cogs/honeypot.py`: Honeypot-Kanal.
- `cogs/server_stats.py`: Serverstatistik-Sprachkanäle.
- `cogs/social_promo.py`: Social-Media-Promo und Feeds.
- `cogs/rp_verwaltung.py`: RP-Verwaltung.
- `pulse_melonly.py` und Melonly-Routen in `webserver.py`: Roblox-Spielerverlaufssuche.
- `support_knowledge.md`, `team_ki_vorlagen.md`, `SUPPORT_ASSISTANT_SETUP.md`: Serverwissen, Vorlagen und KI-Konfiguration. Regeln niemals dazuerfinden.
- `tests/`: Smoke-, Fach- und Regressionstests. Testnamen vor dem Ausführen ansehen; vorhandene Tests sind die Leitplanken für bereits behobene Fehler.

## 4. Datenhaltung und Sicherheit

Laufzeitdaten werden lokal in SQLite und JSON gespeichert und sind absichtlich nicht vollständig Bestandteil des Repositorys. Je nach Instanz gibt es z. B. `config.json`, `team_data.json`, `applications.json`, `shifts.json`, Log-/Audit-Dateien, `pulse.db`, `abmeldungen.db`, `activity_check.db`, Backups und Tickettranskripte. Nicht annehmen, dass die Liste vollständig oder jede Datei vorhanden ist.

Regeln:

- Vor DB-Schemaänderungen Schema, alte Dateien, Startmigrationen und vorhandene Backups prüfen.
- Keine `DELETE`-/Reset-Logik oder Überschreiben von Konfigurationen nur zur Vereinfachung hinzufügen.
- Bestehende JSON-Schlüssel/DB-Spalten möglichst kompatibel weiterführen. Migrationen nachvollziehbar, wiederholbar und abgesichert gestalten.
- `.env`, OAuth-Schlüssel, Discord-Token, Session-Secret, Gemini/OpenAI-API-Key und private Laufzeitdaten dürfen nie committet werden.
- Dashboard-Login nutzt Discord OAuth und signierte Sessions; `PUBLIC_BASE_URL`, `DISCORD_REDIRECT_URI` und die registrierte Discord-Callback-URL müssen exakt zusammenpassen. Nicht aus Versehen neue Callback-URLs voraussetzen.
- Autorisierung auf dem Server für jede sensible Aktion prüfen, einschließlich direkter API-Aufrufe. IDs aus Formulardaten validieren. Nutzerinhalt als nicht vertrauenswürdig behandeln.
- Berechtigungsfehler und Hierarchien (z. B. Bot-Rolle unter Zielrolle) sauber melden statt still fortzufahren.

## 5. Performance- und KI-Leitplanken

### RAM

Projekt-Limit: **250 MB maximal**. Aktuelle Leitlinien:

- 180 MB Warnung
- 210 MB kritisch; nicht benötigte Roblox-Lookup-Caches bereinigen und Garbage Collection nur in der vorhandenen Schutzlogik
- 230 MB Notfallwarnung
- Hysterese zur Vermeidung von Warnungs-Flattern: kritische Stufe geht unter 200 MB zurück, Notfall unter 220 MB, Normalzustand unter 160 MB
- RAM-Trend über begrenzten Messring, Warnmeldung höchstens einmal pro Stunde
- Roblox-Lookup-Cache: README beschreibt 300 Einträge, fünf Minuten TTL und wiederverwendeten HTTP-Pool

Schwellen sind Schutzmaßnahmen, keine harte OS-Garantie. Keine zusätzlichen dauerhaften Scheduler, wenn bestehende Automation erweitert werden kann. Kein unbegrenzter Verlauf oder Queue; bei Nachrichten-/Feed-Verarbeitung maximale Größe und Alter durchsetzen.

### KI / externe Dienste

- KI-Support in `.env.example` standardmäßig aus (`PULSE_SUPPORT_ENABLED=false`). KI-Moderation standardmäßig aus (`PULSE_AI_MODERATION_ENABLED=false`). Diesen sicheren Zustand beibehalten.
- Betreiber hat kein verfügbares OpenAI-API-Guthaben gewünscht. Kein obligatorischer OpenAI-Aufruf und keine kostenpflichtige API voraussetzen. Eine optionale Gemini-Free-Tier-Konfiguration ist dokumentiert, bleibt aber von Googles Quoten/Bedingungen abhängig.
- KI-Fehlerbilder aus dem Betrieb: Provider 429/503, ReadTimeout, API-Pausen. Ein 429 kann auf gemeinsame Quoten hindeuten; nicht mit schnellem Retry-Spam verschlimmern. Bestehende Resilienz, Pausen und verständliche Statusausgaben respektieren.
- TikTok/RSSHub kann 403/503, Anti-Crawler-Sperren oder Timeouts zurückgeben. Eine externe Quelle niemals als zuverlässig garantieren. Fehler isolieren, Verbindungen wiederverwenden und den Dienst nicht im Hintergrund ständig aggressiv abfragen.
- Team-KI und öffentlicher Support müssen getrennte Gesprächskontexte haben; vertrauliche Vorlagen nur im konfigurierten privaten Teamkanal laden. Keine Secrets oder unbestätigten internen Fakten ausgeben.
- KI darf im Support helfen, formulieren und Verdachtsfälle vorbereiten. Sanktionsentscheidungen bleiben menschlich.

## 6. Feste Discord-Regeln und bekannte IDs

Diese IDs sind aus den aktuellen Repository-Konfigurationshinweisen und früheren ausdrücklich genannten Serveranforderungen übernommen. **Vor jeder Änderung, die live Auswirkungen hat, in tatsächlichem Code und aktuellem `config.json` verifizieren.** Wenn Konflikte auftauchen, nichts stillschweigend überschreiben.

### Server / grundlegende Kanäle

- Zielserver: © BOCHUM RP | V1.
- `DISCORD_GUILD_ID` wird über `.env` gesetzt; `main.py` hat als Fallback `1474514929351524616`.
- Team-Updates-Channel: `1531132354272170115`.
- Feedback-/Botvorschläge-Channel: `1560765283872940052`.
- Öffentlicher Pulse-KI-Support: `1493649456086782083`.
- Optionaler Alert-Channel aus früherer Konfiguration: `1510345520529412188`. Ob er aktuell als `PULSE_SYSTEM_ALERT_CHANNEL_ID` eingetragen ist, aus der echten Hosting-Umgebung prüfen; im Repository ist die Variable absichtlich leer.
- Honeypot-Schutzkanal laut README: `1560397792751657053`.

### Warnrollen und Warnfolgen

Warnrollen 1–5 (auch in `.env.example` kommentiert):

Die verbindlichen bekannten Rollen-IDs sind:

- Warnrolle 1: `1489221948348043395`
- Warnrolle 2: `1489222076370780232`
- Warnrolle 3: `1531760107971416135`
- Warnrolle 4: `1556344459422081045`
- Warnrolle 5: `1556344484198088814`
- Uprank-Sperre: `1506055179362107494`, eine Woche ab Warnstufe 2 laut Teamregeln.
- IC-Rechte-Sperre: `1506054747554189472`, eine Woche ab Warnstufe 3 laut Teamregeln.
- Warnstufe 4: Team-Downrank gemäß vorheriger Rangrolle.
- Warnstufe 5: Team-Offboarding/Entfernung der Teamrollen, kein automatischer Kick vom Discord-Server.
- Das aktuelle README nennt für aktives Warnsystem die Folgen ab Warnstufe 2/3/4/5 und automatische Bereinigung temporärer Folgen, wenn keine aktiven Warnungen mehr vorhanden sind. Exakte Dauer/Ausnahmen anhand des laufenden Codes testen.
- Grund ist für Warnrücknahme, Beförderung, Degradierung, Einstellung und Rangwechsel verpflichtend.
- Mehrfach-/massenhafte manuelle Warnung wird durch Teamler-Schutz begrenzt und protokolliert.

### Team-Offboarding vs. Server-Kick

- `/team-kick` und `/team-exit` entfernen nur konfigurierte Teamrollen; Mitglied bleibt im Server.
- Bei Team-Kick sollen die Bürgerrollen `1531303733311307997` und `1489912887949394082` erhalten bzw. nach der Teamrollenentfernung passend vorhanden sein. Vor Implementierung prüfen, wie die aktuelle Funktion mit vorhandenen Rollen umgeht.
- Echte `/kick`-/`/server-exit`-Aktionen sind davon getrennt und benötigen in der aktuellen Sicherheitssystematik die Bestätigung durch eine zweite andere berechtigte Führungskraft.
- Beförderung/Degradierung soll alle Team-Rangrollen durch exakt eine Zielrangrolle ersetzen und andere, nicht zu Teamrängen gehörende Rollen erhalten. `pulse_safety.rank_role_ids_after_change` hilft dabei.
- Atomare Beanspruchung von Freigaben, Grund, Berechtigung und Rollenhierarchie sichern, damit keine doppelten Freigaben/Rennbedingungen entstehen.

### Bewerbungen / Bürgerrechte

- Bürger-Bewerbungsrolle/-Zugangsrolle: `1531303733311307997` war ausdrücklich als Voraussetzung genannt.
- Weitere Bürger-/Review-Rolle: `1531459374004834424` ist in `.env.example` als zweite Bürgerrolle für Moderationsausschlüsse kommentiert. Nicht automatisch davon ausgehen, dass sie dieselbe Bewerbungsberechtigung verleiht; aktuellen Bewerbungscode prüfen.
- Recruiting-Benachrichtigungskanal: `1558776676731719720`.
- Recruiting-Ping-Rolle: `1531300183088824340`.
- Bewerberrolle nach erstmaliger Annahme: `1496070682126188604`.
- Bewerbung annehmen löst nur den Einladungsschritt zum verpflichtenden Bewerbungsgespräch aus. Die Team-Einstiegsrolle erst nach dokumentiertem Gespräch vergeben; anschließend Bewerberrolle entfernen.
- Eignungstests: Low Team 12 Fragen/75 %; High Team 22 Fragen/82 %; Führungsebene 30 Fragen/90 % (README: serverseitige Bewertung). Anspruchsvolle Fallbeispiele und offizielle Regeln beibehalten.

### Tickets

- Öffnungsprotokoll: `1543914075308818503`.
- Übernahmeprotokoll: `1535025111722098778`.
- Bekannte native Ticketkanäle/Panel-Ziele: `1511390378752741386`, `1494338404224340179`, `1494397389635719269`, `1488972966774706186`, `1559242374658662602`, `1488699195157254436`.
- Das Dashboard `/tickets` und Panel-Builder sollen mehrere Panels zulassen, jedes mit mehreren Kategorien/Optionen. Bestehende Discord-Ticketfunktionen, Zuständigkeiten, Priorität, private Berechtigungen, Übernahme/Freigabe, Transkript und Schließen behalten.
- Tickets gehören zum Pulse-System; keine Abhängigkeit zu einem separaten GalaxyBot nötig.
- Panel-Erstellung oder Refresh darf nach einer fehlenden/gelöschten Panel-Nachricht nicht endlos Duplikate posten. Vorhandene Reparatur- und Rate-Limit-Logik prüfen.

### Voice-Support

- Aktuell gewünschter gemeinsamer Voice-Warteraum: `1560211874602160218` (in jüngster Änderung genannt). Ältere Wartezimmer-ID `1528821871057965067` ist historisch; nicht ohne Bestätigung zurücksetzen.
- Supportabschluss-Log laut README: `1531443370172485703`.
- Support soll gemeinsame/konfigurierte Wartezone verwenden. Beitritt soll **nicht** jedes Mal einen neuen Kanal erzeugen, wenn die beabsichtigte aktuelle Konfiguration eine gemeinsame Warteschlange vorsieht.
- Mitglieder dürfen auch außerhalb der Öffnungszeiten in den Warteraum kommen. Außerhalb der Zeiten die konfigurierte After-hours-Audio/Ansage abspielen; Nutzer soll selbst gehen können, nicht automatisch gekickt werden. Den Fall als Supportfall zählen und im Status darstellen.
- Onboarding-/Eröffnungsansage **einmal pro Fall** abspielen; danach kann die konfigurierte Wartemusik wiederholt/in Schleife laufen. Nicht Ansage und Musik gegeneinander auswechseln; wenn beide vorgesehen sind, müssen beide korrekt funktionieren. Ansage nicht fortlaufend wiederholen.
- Supportfall-Buttons: Übernehmen, Übertragen, Beenden. Erforderliche Support-/Bereitschaftsrolle prüfen; Übernahme muss in einen geeigneten normalen Support-Sprachkanal übertragen, temporäre Berechtigungen kontrollieren und nach Beenden/Verlassen aufräumen.
- Verlassende Nutzer dürfen keine verwaisten Fälle, Stimmrechte oder leere automatisch erstellte Räume hinterlassen. Abschlussprotokoll soll Case-ID, Nutzer, Supporter, Zeit/Dauer und Grund dokumentieren.
- Dashboard soll Supportkonfiguration, Öffnungszeiten, Wartemusik/Ansage-Dateien, aktuellen Status/Warteschlange sowie Statistiken abgeschlossener Fälle je Teammitglied abbilden.
- Letzte beobachtete Audio-Bugfolge (bereits durch Commits verändert, daher als Regression zu prüfen, nicht ungeprüft als weiterhin offen behandeln): zunächst wurde Ansage wiederholt, aber Musik fehlte; danach spielte Musik, Ansage aber nicht. Auf `main` gab es Commits für „Use configured waiting music in shared support queue (7.9.5)“ und „Einmalige Ansage und Musikschleife“. Bestehende Tests und Audiopfad prüfen, bevor neue Logik geschrieben wird.

### Schicht / Activity Check / Teamstatus

- Activity Check soll täglich um **06:00 Europe/Berlin** gesendet werden, sofern das im aktuellen Scheduler/Funktionsumfang vorgesehen ist.
- Der Teilnehmer-Snapshot muss zum Check-Zeitpunkt eingefroren bleiben; bestätigte und offene/nicht bestätigte Mitglieder korrekt anzeigen. Historisches UI-Problem: `0/0`, obwohl keine korrekte Teilnehmerliste angezeigt wurde — auf tatsächliche Zähler, Snapshot und leere Zustände regressionsprüfen.
- Während einer aktiven Abmeldung (LOA) bleiben Schichtstart und Activity-Check-Bestätigung möglich. Eine gestartete Schicht beendet LOA nicht; nicht bestätigter Check während LOA darf keine automatische Warnung auslösen.
- Teammitglieder sollen vergessene Schichten anderer Teamler beenden können, sofern berechtigt; automatische Warnung bei einer 24-Stunden-Schicht war gewünscht. `.env.example` hat jedoch `MAX_SHIFT_HOURS=12`; dies könnte eine separate Maximaldauer sein. **Nicht gleichsetzen oder ändern, ohne Scheduler-/Shift-Code und bestehende Absicht zu prüfen.**
- Teamliste soll Wochenstunden, Dienststatus, LOA, Warnungen und tatsächlichen Discord-Presence-Status anzeigen.

### Dashboard-Berechtigungen / Mitarbeiterverwaltung

- Digitale Dashboard-Rollenprofile sind interne UI-Berechtigungen und dürfen Discord-Rechte oder Rollen nicht automatisch verändern.
- Nutzer hatte Standardprofile bewusst auf keine Rechte zurückgesetzt („Standardprofile gesperrt“). Deny-by-default beibehalten.
- Gewünscht ist das Zuordnen eines Mitglieds anhand Discord-ID oder Discord-Name zu einer Teamrolle per Dashboard-Formular. Dabei Discord-Member eindeutig auflösen, Rollenauswahl gegen erlaubte Team-Rangrollen validieren, Grund/Audit nach dem bestehenden Workflow verlangen und fremde Rollen erhalten.
- Globale Suche, Teamakten, Audit, Live-Monitor, Command Center, Aufgaben/Workflow, Meetings, Team-News, Recruiting, Backup/Restore und Health/Security-Bereich sind wesentliche Pulse-Ziele. Keine vorhandenen Tabs/Routes bei UI-Änderungen auslassen.

### Melonly / Roblox-Verlauf

- `/melonly` bzw. Dashboardroute `/melonly` soll eine dedizierte Suchseite sein, um Roblox-Spieler nach ID/Name zu finden und deren Moderationsverlauf (Bans, Kicks, Notes usw.) korrekt zu zeigen.
- Der zuletzt geprüfte Commitverlauf enthält: vollständige Roblox-Spielermoderations-Verlaufssuche, eigenständige Melonly-Suchseite und Fix für Unterstriche in Rückgabe-URLs. Nicht die alte Funktionalität zurückbauen.
- Frühere UI-Meldung zeigte `Bans 0 / Kicks 2 / Notes 0`; bei Problemen tatsächliche Datenquelle, Roblox-ID-Auflösung, URL-Encoding und leere Zustände prüfen.
- Melonly-Aktionen sollen keine normalen Team-Update-Embeds erzeugen, sofern der vorhandene Melonly-Ablauf das ausschließt.

### Team-Namen/Präfixe

- Feste Pulse-Teamhierarchie steuert Präfix/Rang, auch wenn Bürger-/VIP-Rollen in Discord höher einsortiert sind.
- Bestehende Labels wie `PL ✗`, `Stv.PL ✗`, `L.DK ✗`, `Stv.SL ✗`, `L.MA ✗`, `L.Supervisor ✗`, `Sr.Mod ✗` und Bürger-/VIP-Ausnahmen erhalten.
- Bei Rollenwechseln automatische Synchronisierung, aber manuelle Präfixe respektieren, wenn das bestehende Feature sie ausdrücklich unterstützt. Keine blockierenden synchronen Datei-Operationen im Discord-Eventloop hinzufügen.

## 7. Wichtige Konfigurationsvariablen aus `.env.example`

Nicht alle Werte in einem Deployment sind gleich. Fehlende Variablen sind oft absichtlich optional.

- Discord/OAuth: `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_GUILD_ID`, `DISCORD_MESSAGE_CACHE_SIZE`, `DISCORD_REDIRECT_URI`, `DISCORD_APPLICATION_REDIRECT_URI`.
- Dashboard: `PUBLIC_BASE_URL`, `SERVER_PORT`, `PANEL_TIMEZONE`, `SESSION_SECRET`.
- Persistenz/Team: `PULSE_DB_PATH`, `SYNC_WARN_ROLES`, `MAX_SHIFT_HOURS`, `AUTO_BACKUP_HOURS`, `MAX_BACKUPS`, `PULSE_BACKUP_DISCORD_CHANNEL_ID`.
- Support-KI: `PULSE_SUPPORT_ENABLED`, `PULSE_SUPPORT_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_BASE_URL`, `PULSE_SUPPORT_CHANNEL_ID`, `PULSE_SUPPORT_TEAM_CHANNEL_ID`, `PULSE_SUPPORT_TEAM_ROLE_IDS`, `PULSE_SUPPORT_MAX_TOKENS`.
- KI-Moderation: `PULSE_AI_MODERATION_ENABLED`, `PULSE_AI_MODERATION_REVIEW_CHANNEL_ID`, `PULSE_AI_MODERATION_INTERVAL_SECONDS`, `PULSE_AI_MODERATION_CONFIDENCE`, `PULSE_AI_MODERATION_LOW_CONFIDENCE`, `PULSE_AI_MODERATION_MAX_QUEUE_AGE_SECONDS`, `PULSE_AI_MODERATION_EXCLUDE_TEAM_KI`, `PULSE_AI_MODERATION_EXCLUDED_CHANNEL_IDS`, `PULSE_AI_MODERATION_EXCLUDED_CATEGORY_IDS`.
- Social-Feeds: `PULSE_RSSHUB_FALLBACK_URLS`.
- RAM-Warnungen: `PULSE_SYSTEM_ALERT_CHANNEL_ID`.

Never dump the actual `.env` in terminal output or attach it to diagnostics. When sharing configuration snippets, use placeholders only.

## 8. Verifikation / vorhandene Tests

Im README sind diese Gates vorgesehen:

```bash
python -m compileall -q .
python tests/feature_smoke.py
python tests/test_pulse_intelligence.py
python tests/smoke_test.py
```

Es existieren zusätzlich u. a. diese fokussierten Regressionstests:

- `tests/test_pulse_safety.py`
- `tests/test_pulse_intelligence.py`
- `tests/test_pulse_melonly.py`
- `tests/test_voice_support_utils.py`
- `tests/test_support_case_periods.py`
- `tests/test_support_assistant.py`
- `tests/test_team_protection.py` und `tests/test_team_protection_db.py`
- `tests/test_application_eligibility.py` und `tests/test_career_applications.py`
- `tests/test_discord_log_regressions.py`
- `tests/test_role_nickname_utils.py`
- `tests/test_announcement_archive.py`
- `tests/test_pulse_activity_utils.py`
- `tests/test_pulse_shift_utils.py`

Wähle bei einer Änderung mindestens die fachlich relevanten Tests zusätzlich zu den allgemeinen Gates. Änderungen an Voice-Support müssen Verhalten/Util-Tests für Begrüßung, Musikschleife, Warteraum und Fallstatus abdecken. Änderungen an Teamaktionen benötigen Safety-/Rangrollen-/Audittests. Änderungen am Bewerbungssystem benötigen Eligibility- und Interview-/Rollenfluss-Tests.

## 9. Bekannte Probleme, Regressionen und Grenzen

Diese Liste trennt historisch beobachtete Probleme von bereits per Commit angefassten Pfaden. Zuerst verifizieren, dann behandeln:

1. **Voice-Support-Audio und Warteraum:** neueste Commits enthalten Anpassungen, die gleichzeitig einmalige Ansage und Musikschleife ermöglichen sollen; den aktuellen Main-Stand als Regression testen, nicht die Funktion überschreiben. Gemeinsamer Warteraum darf keine unerwünschten neuen Räume pro Beitritt erzeugen.
2. **Discord 429:** historisch bei wiederholten Channel-PATCH-Änderungen beobachtet. Kanalstatistik/Status nicht öfter als erforderlich aktualisieren; bei Rate Limits Discord-Retry-After und Backoff respektieren.
3. **KI-API 429/503/Timeout:** bei fehlendem/limitiertem API-Zugang muss der Restbot normal laufen; optionaler KI-Teil pausiert, ohne das gesamte Dashboard zu blockieren.
4. **Social Promo/TikTok:** externe Feeds können Anti-Crawler-/RSSHub-Ausfälle zurückgeben. Status/Fehler darstellen, nicht so tun, als wäre das zuverlässig reparierbar durch Retry-Spam.
5. **Activity Check leere Zahlen:** `0/0` und Teilnehmer-/Bestätigungslisten mit echten Snapshots prüfen.
6. **Melonly-Spieler-Suche:** bei ID/Namenssuche, Unterstrichen, Ergebnis-URLs und Darstellung alter Bans/Kicks/Notes Regressionstests nutzen.
7. **Changelog/README-Staleness:** mögliche Doku-Wartung, aber nicht Teil jeder Feature-Änderung, solange sie nicht sachlich betroffen ist.
8. **Git-Hygiene:** in der GitHub-Baumansicht lagen historische `__pycache__/*.pyc`-Dateien. Keine weiteren Bytecode-Dateien hinzufügen. Entfernen bestehender getrackter Artefakte nur als bewusst abgegrenzte Hygieneänderung nach Status-/Build-Prüfung, nicht zusammen mit sensiblen Bugfixes verstecken.

## 10. Gewünschte Arbeitsweise bei einem neuen Auftrag

1. Auftrag auf den tatsächlichen existierenden Code abbilden und beteiligte Dateien identifizieren.
2. Git-Status und aktuelle Änderungen prüfen, uncommitted Arbeit erhalten.
3. Kurz notieren, was vermutlich Ursache ist und wie Regression verhindert wird — keine langen Status-Reden.
4. Zielgerichtet implementieren, dabei vorhandene UI-/API-/DB-/Discord-Kette vollständig verfolgen.
5. Passende Regressionstests hinzufügen/aktualisieren und relevante Testkommandos ausführen.
6. Diff kontrollieren: keine Secrets, keine unbeabsichtigten Konfigurations-/Daten-Löschungen, keine entfernten Funktionen, keine formatierungsbedingten Mammut-Diffs.
7. Auf Deutsch zusammenfassen: Ursache, Dateien, Verhalten, Tests (mit echten Resultaten), bekannte Einschränkungen. Keine Erfolgsmeldung, wenn ein wichtiger Schritt übersprungen wurde.

## 11. Was Claude Code beim ersten Durchlauf tun soll

Noch keine Änderungen vornehmen. Zuerst:

- Repo auf aktuellem `main` einlesen und `git status` kontrollieren.
- `CLAUDE.md`, diese Übergabe, `README.md`, `CHANGELOG.md`, `pulse_version.py`, `.env.example`, `main.py`, die relevanten `cogs/`, `webserver.py`, `pulse_db.py`, `pulse_safety.py` und die passenden Tests lesen.
- Versions- und Dokuabweichungen benennen, ohne sie selbständig zu „korrigieren“.
- Eine kurze Baseline melden: was Pulse heute tatsächlich kann, wie der Dienst startet, welche Tests laufen und welche oben genannten Problemstellen nach Code-/Teststand offen oder bereits abgedeckt sind.
- Danach auf den konkreten Änderungsauftrag des Betreibers warten bzw. ihn ausführen, wenn er mit der Initialnachricht mitgegeben wurde.
