# Pulse TeamOS — Arbeitsanweisungen für Claude Code

## 1. Auftrag und Projektidentität

Du arbeitest an **Pulse TeamOS** für den Discord-/Roleplay-Server **© BOCHUM RP | V1**.

- Repository: https://github.com/NicoPinguinDEV/Pulse
- Hauptbranch: `main`
- Stand 23.10.2026: `pulse_version.py` meldet **7.16.0**. Laufende Arbeit auf `verbesserung/phase-8-betrieb` (unveröffentlichte Einträge oben im `CHANGELOG.md`); Fortschritt je Punkt in `docs/VERBESSERUNGSPLAN.md`. Versionsangaben immer aus `pulse_version.py` und `git log` lesen.
- Ziel: ein professionelles, zuverlässiges Discord-Teamverwaltungs- und Moderationssystem mit eigenem FastAPI-Webdashboard. Pulse soll seine Funktionen selbst bereitstellen, nicht von GalaxyBot abhängig sein. Eine ähnliche Panel-Builder-Bedienung ist erwünscht, aber keine Abhängigkeit vom fremden Bot.
- Betreiber arbeitet überwiegend auf Deutsch und erwartet konkrete Umsetzung statt bloßer Ideensammlungen. Antworte auf Deutsch, knapp aber mit konkreten Änderungen, Tests und ehrlicher Restliste.

Lies zusätzlich `docs/CLAUDE_CODE_HANDOFF.md`, bevor du größere Änderungen machst. Es enthält Projektarchitektur, verbindliche Geschäftsregeln, IDs, bekannte Fehlerbilder und offene Prüfungen.

## 2. Nicht verhandelbare Regeln

1. **Keine bestehende Funktion verlieren oder stillschweigend abschwächen.** Vor jeder Änderung die existierende Implementierung, Aufrufer, UI-Formulare, Berechtigungen, Datenstrukturen und Tests prüfen. Kleine, zielgerichtete Änderungen bevorzugen.
2. **Vorhandenen Code verifizieren.** Dokumentation kann hinterherhinken. `pulse_version.py`, laufende Implementierung und Regressionstests haben Vorrang vor veralteten Versionsangaben. IDs/Defaults bei Widersprüchen aus `.env.example`, `config.json`-Logik, Code und Tests abgleichen, nichts raten.
3. **Daten schützen.** Keine SQLite-/JSON-Laufzeitdaten, Backups, Transkripte, Einstellungen oder Teamakten löschen, zurücksetzen oder inkompatibel migrieren. Schemaänderungen rückwärtskompatibel und mit Migrations-/Backupplan durchführen.
4. **Keine Secrets committen oder ausgeben.** `.env`, Tokens, API-Schlüssel, OAuth-Secrets, Session-Geheimnisse, echte Laufzeitdaten und private Transkripte nie in Git, Ausgaben, Tests oder Doku schreiben. `.env.example` enthält nur Platzhalter.
5. **Team-Offboarding ist kein Server-Kick.** Team-Kick entfernt ausschließlich die konfigurierten Teamrollen und lässt die Person auf dem Discord-Server. Echte Server-Kicks sind eine separate, ausdrücklich bestätigte Aktion mit 4-Augen-Freigabe, wo der aktuelle Code dies vorsieht.
6. **Berechtigungen standardmäßig verweigern.** Dashboard-interne Rollenprofile dürfen keine Discord-Rechte oder Teamrollen eigenständig erteilen. Bestehende Deny-by-default-Profile/Permission-Overrides bewahren. Jede sensible Route serverseitig autorisieren; UI-Ausblendung allein reicht nicht.
7. **Keine automatische Sanktion durch KI.** KI-Erkennung darf Verdachtsfälle liefern; Sanktionen erfordern die im bestehenden Ablauf vorgesehene menschliche Bestätigung.
8. **RAM-Budget: maximal 250 MB.** Keine unnötigen Prozesse, Polling-Schleifen, ungebegrenzten Queues/Caches oder großen neuen Abhängigkeiten. Bestehende Connection-Pools, Cache-Limits und RAM-Hysterese beibehalten.
9. **Keine kostenpflichtige KI-Abhängigkeit neu einführen.** Das Projekt muss ohne KI-API-Guthaben funktionieren. Externe KI bleibt optional und konfigurationsgesteuert; regelbasierte/lokale Funktionen bevorzugen.
10. **Keine ungeprüften Deployments oder direkten Produktionsaktionen.** Tests dürfen sich nicht mit echtem Discord verbinden, echte Rollen ändern, Nachrichten verschicken oder Live-Daten manipulieren. Nicht ohne ausdrücklichen Auftrag auf Produktion deployen.
11. **Vor dem Abschluss Tests wirklich ausführen.** Fehler, fehlende Abhängigkeiten und nicht ausführbare Tests ehrlich berichten. Keine Tests als bestanden behaupten, wenn sie nicht gelaufen sind.
12. **Keine blind ausgeführten Groß-Refactorings.** Erst Bestand und Abhängigkeiten kartieren; ein Refactoring nur, wenn es für das Ziel erforderlich und durch Tests abgesichert ist.

## 3. Laufzeit und Technik

- Python **3.14+** laut README; `discord.py` mit Cogs/Slash Commands.
- FastAPI + Uvicorn betreiben das Dashboard im selben Prozess wie der Discord-Bot.
- Einstiegspunkt: `main.py`. Es lädt `.env` vor dem Import von `webserver`/Datenbankmodulen, lädt `cogs/*.py`, synchronisiert Slash Commands, startet `pulse_ultimate` und danach Uvicorn.
- Standardport: **25095**, konfigurierbar über `SERVER_PORT`.
- Persistenz: SQLite und JSON-Dateien; `.env`/Runtime-Daten liegen im Deployment außerhalb von Git.
- Voraussetzungen aus `requirements.txt`: `discord.py`, `fastapi`, `uvicorn`, `httpx`, `python-dotenv`, `python-multipart`, `PyNaCl`, `davey`.
- Discord Intents (Members, Presences, Message Content) und Discord-Rollenhierarchie im Hinterkopf behalten. Der Bot benötigt je nach Feature passende Kanal-/Rollenrechte.

## 4. RAM-Leitplanken

Das Projekt muss innerhalb eines **250-MB-Host-Limits** möglichst stabil bleiben. **Aktualisiert durch Entscheidung E-5 (`docs/VERBESSERUNGSPLAN.md`):** auf dem Vionity-VPS gilt ein Containerlimit von 512 MiB (+ max. 256 MiB Swap, `compose.yaml`); der Memory Guard in `pulse_ultimate.py` warnt bei 350/420/470 MB. Die Werte unten sind die Standardparameter von `pulse_safety.memory_guard_level`. Sparsamkeit bleibt Pflicht. Aktueller Safety-Helper `pulse_safety.py` verwendet Hysterese:

- Warnung: 180 MB
- Kritisch: 210 MB
- Notfall: 230 MB
- Kritische Stufe fällt erst unter 200 MB zurück; Notfall unter 220 MB; Normalzustand unter 160 MB.

Der RAM-Guard soll den vorhandenen Automationszyklus wiederverwenden und keine weitere permanente Schleife eröffnen. Caches/Queues immer begrenzen; HTTP-Pools wiederverwenden und beim Shutdown schließen. Bei Performance-Änderungen RAM, CPU, Event-Loop-Blockierung, API-Rate-Limits und Datenkorrektheit gemeinsam betrachten.

## 5. Verbindliche Arbeitsroutine

### Bevor du editierst

1. Git-Status und Branch prüfen; Nutzeränderungen niemals überschreiben.
2. `README.md`, `CHANGELOG.md`, `pulse_version.py`, `.env.example`, relevante Cogs/Services, `webserver.py`, DB-Helper und vorhandene Tests lesen.
3. Den tatsächlichen Kontrollfluss der Funktion nachvollziehen: Dashboard → Route/API → Domain-Logik → Discord/DB → Protokollierung.
4. Betroffene Regressionen als Tests reproduzieren oder neue Tests schreiben.
5. Bei Widersprüchen die bestehende Runtime-Konfiguration und das tatsächliche Verhalten klären; keinen plausiblen Wert erfinden.

### Während der Änderung

- Bestehende öffentliche Funktionen, Routen, Formularnamen, Slash-Command-Namen, JSON-Schlüssel und Datenbankfelder möglichst kompatibel halten.
- Neue optionale Umgebungsvariablen in `.env.example` dokumentieren; keine echten Schlüssel eintragen.
- Kritische Geschäftslogik in kleine, testbare Helper auslagern, wenn es natürlich passt. Bei reinen Helper-Tests keinen kompletten Discord-Bot starten.
- Discord-API-Fehler, 403/404, Timeouts und 429 explizit behandeln. Kein unendliches Retry-Spamming; Backoff/Rate-Limit-Pausen beachten.
- Fehler im Dashboard sichtbar und verständlich behandeln; keine stillen Ausfälle und keine Erfolgsmeldungen vor bestätigtem Ergebnis.
- Für UI-Änderungen Desktop/Mobilansicht, Lade-/Fehler-/Leerzustände, Scrollposition und die anderen vorhandenen Seiten prüfen.

### Verifikation vor Abschluss

Mindestens die im README aufgeführten Checks ausführen:

```bash
python -m compileall -q .
python tests/feature_smoke.py
python tests/test_pulse_intelligence.py
python tests/smoke_test.py
```

Danach nach Möglichkeit zusätzlich passende fokussierte Regressionstests unter `tests/` ausführen. Wenn ein Test an Umgebung/Abhängigkeiten scheitert, Grund mit Ergebnis angeben und alternative statische Prüfung nur als solche bezeichnen.

### Git und Abschluss

- Änderungen klein und nachvollziehbar halten; nur relevante Dateien anfassen.
- Keine `git push --force`, keine zerstörerischen History-Rewrites, keine Secrets und keine Laufzeitdaten committen.
- Keine Veröffentlichung oder VPS-Deployment auslösen, solange der Nutzer es nicht ausdrücklich verlangt hat.
- **Jeden Commit sofort auf GitHub pushen** (Wunsch des Betreibers, 23.10.2026): nach jedem Commit `git push` auf den aktuellen Arbeitsbranch (beim ersten Mal `git push -u origin <branch>`), danach mit `git ls-remote` prüfen, dass der Commit angekommen ist. Das gilt nur für Feature-/Verbesserungsbranches. Ein Push auf `main` löst das VPS-Deploy aus und braucht weiterhin eine ausdrückliche Freigabe; Force-Push bleibt verboten.
- Abschlussbericht auf Deutsch: Ursache, konkret geänderte Dateien, Verhalten vorher/nachher, ausgeführte Tests und Resultate, offene Risiken, notwendige Hosting-/Dashboard-Schritte.

## 6. Erste Aufgabe bei einer neuen Claude-Code-Session

Nicht sofort Code umschreiben. Erst:

1. Repository-Stand/Branch/Dirty Files prüfen.
2. `docs/CLAUDE_CODE_HANDOFF.md` und die maßgeblichen Quelldateien lesen.
3. Eine kurze Bestandsaufnahme liefern: aktuelle Version, Architektur, relevante Stellen, Tests, echte offene Fehler und mögliche Dokumentationsabweichungen.
4. Danach die konkrete Nutzeranforderung umsetzen, ohne dass der Nutzer bereits dokumentierten Kontext erneut erklären muss.
