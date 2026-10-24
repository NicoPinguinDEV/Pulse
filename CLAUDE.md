# Pulse TeamOS — Claude Code

## Ziel und Zusammenarbeit
Du entwickelst Pulse für © BOCHUM RP | V1: Discord-Bot und FastAPI-Dashboard. Arbeite End-to-End, direkt und auf Deutsch. Behebe echte Fehler, statt nur Ideen zu liefern. Erhalte bestehende Funktionen und zeige knapp, was geändert und wirklich getestet wurde.

## Verlässliche Quellen
- Zuerst Branch, HEAD und Arbeitsbaum prüfen. Nutzeränderungen niemals überschreiben.
- Für die aktuelle Version ist pulse_version.py maßgeblich; für Verhalten zählt der ausgeführte Code. Handoff, README und Changelog können historische oder unvollständige Angaben enthalten.
- docs/CLAUDE_CODE_HANDOFF.md ist ein Nachschlagewerk, kein Pflichttext zum vollständigen Einlesen. Suche dort nur nach den Abschnitten, die zur konkreten Aufgabe gehören.
- webserver.py ist sehr groß: verwende gezielte Symbol-/Textsuche und lies kleine, relevante Bereiche statt die gesamte Datei in den Kontext zu laden. Nutze Smart-Explore/LSP, wenn es die Navigation verkürzt.

## Harte Projektregeln
1. Keine vorhandene Funktion still entfernen, vereinfachen oder abschwächen. Vor einer Änderung Aufrufer, tatsächlichen Kontrollfluss, Rechte und Tests nachvollziehen.
2. Keine Laufzeitdaten löschen, zurücksetzen oder inkompatibel migrieren. DB-/JSON-Schemaänderungen rückwärtskompatibel, mit Sicherung und Tests.
3. Nie Secrets, Tokens, private Teamakten, echte .env-Inhalte oder Laufzeitdaten committen oder in Ausgaben zeigen.
4. Dashboard- und Admin-Aktionen serverseitig autorisieren; Berechtigungen standardmäßig verweigern. UI-Ausblendung ist keine Autorisierung.
5. Team-Kick/Team-Exit entfernt Teamrollen, aber kickt niemanden vom Discord-Server. Echter Server-Kick bleibt eine eigenständige, ausdrücklich bestätigte Aktion.
6. AutoMod, Anti-Nuke und KI dürfen nicht unbemerkt produktiv aktiviert werden. Keine automatische Sanktion allein aufgrund einer KI-Einschätzung.
7. RAM-Budget für den aktuellen 2-GiB-VPS: Pulse maximal 768 MiB Container-RAM plus bis zu 256 MiB Swap; Warnung/kritisch/Notfall bei 500/620/700 MB. Neue Queues/Caches begrenzen, vorhandene Scheduler wiederverwenden und Netzwerk-/Discord-Rate-Limits respektieren.
8. Keine obligatorische kostenpflichtige KI-API hinzufügen. Pulse muss ohne KI-API-Guthaben weiterarbeiten können.
9. Niemals produktiv deployen, Serverrollen ändern oder echte Discord-Aktionen ausführen, sofern der Nutzer das nicht ausdrücklich verlangt.

## Arbeitsweise mit niedrigem Tokenverbrauch
- Kleine Änderung: nur relevante Dateien und direkte Aufrufer untersuchen, direkt umsetzen und gezielte Tests ausführen. Keine Planung/Subagents allein aus Gewohnheit.
- Mittlere/größere Änderung: erst knapper Plan, dann zusammenhängende Arbeitsschritte, anschließend Regressionstests.
- Subagents nur für unabhängige, parallel prüfbare Aufgaben oder isolierte Recherche. Keine parallelen Änderungen an denselben Dateien; Ergebnisse kurz zusammenfassen.
- Nicht standardmäßig die gesamte Codebasis, alle Tests, Logs oder den ganzen Handoff laden. Erst zielgerichtet suchen und Ergebnisse auswerten; Kontext bei Bedarf mit /compact verdichten.
- Wiederhole keine bereits bestätigten Erkenntnisse oder langen Zwischenberichte. Bei langen Aufträgen nur bei wichtigen neuen Erkenntnissen berichten.
- Standardprüfung: /pulse-verify targeted. /pulse-e2e, /pulse-security-audit und /pulse-performance nur gezielt bei passender Änderung. Das Playwright-MCP-Plugin ersetzt keine versionierten Tests in tests/.

## Code und Tests
- Python 3.14+, discord.py, FastAPI und Uvicorn; Bot und Webserver laufen im selben Prozess.
- Teste zuerst gezielt. Änderungen an Rechten, Daten, mehreren Modulen, Start/Shutdown oder Deployment zusätzlich mit dem vollen CI-Satz prüfen.
- CI-Referenz: .github/workflows/python-syntax.yml. Sie führt Ruff, compileall, tests/feature_smoke.py, tests/smoke_test.py und unittest discovery aus.
- Tests dürfen nicht unbeabsichtigt Live-Discord, echte OAuth-Dienste, Rollenänderungen, Nachrichten oder Produktionsdaten verwenden.
- Discord 403/404/429, Timeout und externe Provider-Ausfälle explizit berücksichtigen. Kein endloses Retry-Spamming.
- Bei UI-Änderungen Desktop und Mobilgerät sowie Lade-, Fehler- und Leerzustände und die Scrollposition prüfen.
- Vor Abschluss Diff/Status auf Secrets, versehentliche Dateien und unbeabsichtigte Funktionsverluste prüfen. Testresultate ehrlich berichten.

## Betrieb und Veröffentlichung
- Der VPS läuft mit Docker Compose; der Dashboard-Port ist standardmäßig 25095. Nutze für Deployments die Anleitungen und Prüfungen des Repositorys, nicht erfundene Annahmen.
- Vor einem ausdrücklich beauftragten Deployment Teamdaten, Konfigurationen, Datenbanken und lokale Medien wie assets/support_wait.mp3 sichern. Fast-forward und exakten Ziel-Commit prüfen, Container bauen, Healthcheck abwarten und Fehler offen melden.
- Nie git push --force, git clean -fd, docker compose down -v oder eine blinde Runtime-Daten-Wiederherstellung ausführen.

## Abschluss
Kurz auf Deutsch: Ursache/Ziel, geänderte Dateien, Tests mit echten Ergebnissen und verbleibende Risiken. Keine Erfolgsmeldung für Schritte, die nicht ausgeführt oder nicht bestanden wurden.