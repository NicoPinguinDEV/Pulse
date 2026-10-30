# Claude Code für Pulse: schlank und leistungsfähig

Ziel: möglichst wenig immer geladenen Kontext, aber gründliche Arbeit bei Bugs, Sicherheitsänderungen und Releases. Diese Datei ist eine Empfehlung für die lokale Claude-Code-Oberfläche; sie schaltet persönliche Plugins nicht automatisch um.

## Grundprinzip: Kontext nur laden, wenn er gebraucht wird

- CLAUDE.md enthält nur Regeln, die bei fast jeder Aufgabe gelten. Projekthistorie, vollständige Architekturberichte und mehrstufige Verfahren bleiben in Dokumentation oder manuell aufrufbaren Skills.
- Bevorzugt gezielte Suche, Smart-Explore und Pyright/LSP statt webserver.py, Logs oder den gesamten Handoff in den Kontext zu kopieren.
- Kleine Bugfixes direkt bearbeiten. Für große Änderungen zuerst /make-plan (manuell), dann schrittweise selbst umsetzen. Orchestratoren wie build/do sind gesperrt.
- Tests zuerst fokussiert, danach den vollständigen CI-Satz bei Modulen mit mehreren Abhängigkeiten, Datenmigrationen, Berechtigungen oder vor Release/Deployment. Qualität nicht durch Weglassen wichtiger Tests "sparen".
- Subagents nur für unabhängige Recherche/Prüfung oder getrennte Workstreams. Bei kleinen Änderungen kosten sie meist mehr Kontext und Koordination als sie sparen.
- Nach langem Kontext /compact nutzen; bei einem vollständig neuen Thema /clear und bei Sitzungswechsel /handoff. Nicht dauerhaft komplette Logs/Transkripte in CLAUDE.md kopieren.

## Plugins im Projekt

### Behalten bzw. bei Bedarf verwenden

- **Pyright LSP:** behalten. Hilft in der großen Python-Codebasis beim Springen zu Symbolen, ohne ganze Dateien zu lesen. `pyrightconfig.json` im Repo setzt `typeCheckingMode: "off"` und meldet nur undefinierte Namen: Im Standardmodus erzeugt allein `webserver.py` rund 120 Typfehler (im Modus `basic` repo-weit über 600), die nach jedem Edit als Diagnose in den Kontext kämen. Für einen gezielten Typcheck `typeCheckingMode` vorübergehend auf `"standard"` stellen, `pyright --level error <datei>` ausführen und die Änderung nicht committen.
- **Security guidance:** behalten (Pattern-Warnungen bei Edits). Die automatischen LLM-Reviews (Stop-Hook und Commit) sind projektweit über `env.ENABLE_CODE_SECURITY_REVIEW=0` in `.claude/settings.json` aus; gezielte Prüfung über `/pulse-security-audit`. Sicherheitsreview ist kein Ersatz für Tests.
- **Playwright (Microsoft, `playwright@claude-plugins-official`, Projekt-Scope):** zum Erkunden/Debuggen des lokalen Dashboards. Dauerhafte Prüfungen gehören als Tests nach `tests/` (siehe `/pulse-e2e`). Startet `npx @playwright/mcp@latest` (Node erforderlich).
- **Frontend design:** behalten, aber für echte Dashboard-/UI-Aufgaben einsetzen, nicht bei reinen Backend-Bugs.
- **Context7:** behalten für aktuelle, versionsspezifische Bibliotheksdokumentation; nur bei Bedarf aufrufen.
- **Commit commands:** die einfache Commit-Funktion behalten. PR-Workflows nur aktiv verwenden, wenn wirklich mit Feature-Branches und Pull Requests gearbeitet wird.
- **Claude mem:** optional behalten, wenn gespeicherte Beobachtungen wirklich Wiederholungen vermeiden. Lange oder thematisch irrelevante automatische Memory-Injektionen abschalten bzw. kürzen, falls die installierte Plugin-Konfiguration das erlaubt.

### Deaktiviert lassen bzw. erst bei Bedarf aktivieren

- **Chrome DevTools MCP:** deaktiviert lassen (Playwright deckt normale Browserautomatisierung ab); nur für Netzwerk-/Performance-Traces vorübergehend aktivieren.
- **Supabase expert:** deaktiviert lassen; Pulse nutzt keine Supabase-Architektur.
- **Claude Code setup / automation recommender:** beim Einrichten oder bei einer größeren Tool-Änderung einsetzen, nicht für jeden normalen Codeauftrag. Nach abgeschlossener Einrichtung muss er nicht dauerhaft aktiv bleiben.

## Skills: kleine, sinnvolle Auswahl

### Häufig nützlich

- **smart-explore, mem-search:** gezielte Code-Navigation bzw. Erinnerungssuche.
- **karpathy-guidelines:** kleine, nachvollziehbare Änderungen statt übermäßig komplexer Refactors.
- **make-plan:** nur für größere Aufgaben.
- **handoff:** kompakter Sitzungswechsel.
- **commit:** nur wenn ein Commit ausdrücklich gewünscht ist.
- **frontend-design:** nur für UI-Aufgaben.

### Gesperrt (Modellaufruf per `permissions.deny` in der lokalen `.claude/settings.json`)

claude-mem: agent-cost-report, babysit, build, build-and-release, ccs-align, childhood, cloud-sync, design-is, do, how-it-works, knowledge-agent, learn-codebase, mode-creator, oh-my-issues, pathfinder, release, screenpipe, standup, timeline-report, version-bump, weekly-digests, what-the, wowerpoint. commit-commands: clean_gone, commit-push-pr.

**Einschränkung (Claude Code 2.1.303, getestet):** `skillOverrides` blendet nur Projekt-/Nutzer-Skills aus, **keine Plugin-Skills**, und das Plugin-CLI kennt nur ganze Plugins (`claude plugin disable`). Die gesperrten claude-mem-Skills erscheinen daher weiter in der Skill-Liste (~2,5k Tokens immer geladen laut `claude plugin details claude-mem@thedotmack`), können vom Modell aber nicht gestartet werden. Ganz entfernen ginge nur durch Deaktivieren von claude-mem insgesamt – das würde die Memory-Funktion entfernen und ist deshalb nicht gemacht.

### Cloud-Sitzungen (claude.ai/code)

Cloud-Container starten ohne die lokalen Plugins und ohne die lokale `.claude/settings.json`. Da diese Datei bewusst nicht versioniert ist (siehe unten; eine eingecheckte Datei würde beim `git pull` mit der lokalen, unversionierten kollidieren), dort pro Sitzung bei Bedarf:

```bash
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install pyright-lsp@claude-plugins-official --scope user
```

`pyright-langserver` ist im Cloud-Image vorhanden, sonst `npm install -g pyright@1.1.414`. Bewusst **nicht** in der Cloud installieren: `security-guidance` (LLM-Review bei jedem Stop/Commit kostet ohne `ENABLE_CODE_SECURITY_REVIEW=0` laufend Tokens; Ersatz: `/pulse-security-audit` bzw. `/security-review`), Playwright-MCP (`@latest` ungepinnt; Chromium + Playwright sind im Image vorinstalliert, die versionierten E2E-Tests reichen), `code-review`-Plugin (`/code-review` ist bereits eingebaut). Nur Plugins aus `claude-plugins-official` mit Quelle `./plugins/…` (von Anthropic gepflegt) verwenden.

## Lokale Regeln und Sicherheit

- Kein universelles Auto-Approve für Bash/SSH, keine globale Freigabe für gefährliche Tools und kein dangerously-skip-permissions.
- Dieses Repository setzt absichtlich keine erweiterte gemeinsame Permission-Allowlist über .claude/settings.json, weil das die Freigabe-/Trust-Entscheidungen auf dem Rechner des Nutzers verändern würde.
- Produktiv-Deployment ausschließlich nach ausdrücklicher Anweisung. Lokale VPS-Secrets gehören nicht ins Repository.
- Halte das gewählte hochwertige Modell für schwierige Aufgaben bei. Token sparen wir primär durch fokussierten Kontext, weniger redundante Skill-Orchestrierung und gezielte Tests – nicht durch unnötig schwächere Modelle.

## Offizielle Dokumentation

- Skills und manuell aufrufbare Skill-Inhalte: https://code.claude.com/docs/en/skills
- Projekt-/Pfadregeln und Memory: https://code.claude.com/docs/en/memory
- Erweiterungen, MCP und Subagents: https://code.claude.com/docs/en/features-overview

## Werkzeug-Stand (2026-10-24)

### Projekt-Skills

- `/pulse-e2e`: Browser-Workflows mit Playwright testen; nur auf ausdrücklichen Aufruf. E2E läuft ausschließlich gegen den lokalen Preview-Server mit Fake-Login und temporären Daten.
- `/pulse-regression-fix`: Fehler reproduzieren, zuerst einen Regressionstest rot sehen, minimal beheben, danach gezielt und bei Bedarf vollständig testen. Dieser Skill darf Code ändern, aber niemals eigenständig committen, pushen, mergen oder deployen.
- `/pulse-review`: unabhängiges, nur lesendes Review; nur auf ausdrücklichen Aufruf.
- `/pulse-security-audit`: Sicherheitsprüfung, nur lesend; nur auf ausdrücklichen Aufruf.
- `/pulse-performance`: Ressourcen-/Leistungsprüfung, nur lesend; nur auf ausdrücklichen Aufruf.
- `/pulse-audit`: Gesamtprüfung, nur lesend; nur auf ausdrücklichen Aufruf.
- `/pulse-release`: Release-/Merge-Vorbereitung, nur lesend; nur auf ausdrücklichen Aufruf.
- `/pulse-data-safety`: vorhandener Spezialskill für sichere Datenänderungen; bei Datenmigrationen, Backup/Restore und Retention verwenden.

### E2E-Tests

Reproduzierbar (wie die CI, Hash-geprüft): `python -m pip install --require-hashes -r requirements-dev.lock`, anschließend `python -m playwright install chromium`. `requirements-dev.lock` enthält exakt die Runtime-Versionen aus `requirements.lock` plus `playwright`/`greenlet`/`pyee`; Playwright gehört **nicht** in `requirements.txt` (Docker-Image). Nach einer Änderung an `requirements.lock` oder `requirements-dev.txt` neu erzeugen (Befehl steht im Kopf der Lock-Datei); `tests/test_ci_workflows.py` schlägt fehl, wenn die Locks auseinanderlaufen.

Abgedeckt (`tests/test_e2e_dashboard.py`, 12 Tests, drei Preview-Server: Admin ×2, Teammitglied): Navigation über die Sidebar, Wochenziel speichern/neu laden (Desktop, Mobil), serverseitige Bereichsprüfung, Zwischenrolle mit ungültigem Namen (abgelehnt, nichts gespeichert) und gültigem Namen (gespeichert, Scrollposition bleibt), Speichern im Wartungsmodus (503, Daten unverändert), 500-/404-Fehlerseiten, Leerzustände, Server Control Center (Laden → geladen, API-500, Netzwerkfehler), Mobil ohne horizontalen Überlauf, Teammitglied: 403 auf Admin-Seiten und direkter POST ohne Schreibwirkung. Serverfehler löst der Preview-Server nur mit `--e2e-hooks` über `/__preview/…` aus (nur dieser Prozess, nur 127.0.0.1).

Gezielter lokaler Lauf:

```powershell
$env:PULSE_E2E = "1"
python -m unittest tests.test_e2e_dashboard -v
Remove-Item Env:PULSE_E2E
```

Die CI führt den E2E-Job in `.github/workflows/python-syntax.yml` mit `PULSE_E2E=1` aus. Der Job `e2e-scope` (`tools/ci_e2e_needed.py`) überspringt ihn nur, wenn ausschließlich Doku/Metadaten (`*.md` außer den zur Laufzeit gelesenen Support-Dateien, `docs/`, `.claude/`, reine Unittests, Security-/Deploy-Workflow) geändert wurden; alles andere – auch unbekannte Pfade, Zeitplan und manueller Start – führt die Browser-Suite aus. Ruff, compileall, Smoke-Tests und Unittests laufen immer. Ein übersprungener Test ist kein bestandener Test; wenn E2E explizit angefordert wird, muss ein fehlendes Playwright/Chromium als Fehler sichtbar werden.

### Sicherheitsgrenzen und Ergebnisberichte

- Skills mit `disable-model-invocation: true` laufen nur auf ausdrücklichen Aufruf; das gilt für Audit, Review, E2E, Performance, Security und Release-Vorbereitung.
- Die Regression-Fix-Automatisierung bleibt auf Bugfix-Aufgaben beschränkt. Sie darf keinen Commit, Push, Merge, Release oder Produktiv-Deploy ausführen.
- Keine persönlichen `.claude/settings.local.json`, lokalen Launch-Konfigurationen, Tokens oder individuellen Freigaben ins Repository übernehmen.
- Tests nur als bestanden melden, wenn sie im aktuellen Arbeitsgang tatsächlich ausgeführt wurden. Bisherige lokale Basis: 791 Unittests bestanden (3 übersprungen) und 3 Playwright-E2E-Tests bestanden; diese Zahlen sind historische Referenzwerte, keine Aussage über einen späteren Commit.
- Vor Merge den tatsächlichen CI-Status des exakten HEAD prüfen. Eine grüne CI auf `main` deployt nur, wenn die Repository-Variable `PULSE_AUTO_DEPLOY=true` gesetzt ist (Standard: nicht gesetzt → nur manueller „Run workflow“); Details und empfohlene Branch-/Environment-Regeln in `VPS_DEPLOYMENT.md` Abschnitt 5.
- Das Playwright-MCP-Plugin startet `npx @playwright/mcp@latest` aus der Plugin-Konfiguration (nicht aus diesem Repository). Es dient nur dem interaktiven Erkunden; die reproduzierbaren, gepinnten Tests sind `tests/test_e2e_dashboard.py` mit `requirements-dev.lock`.
