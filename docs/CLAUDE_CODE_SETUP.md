# Claude Code für Pulse: schlank und leistungsfähig

Ziel: möglichst wenig immer geladenen Kontext, aber gründliche Arbeit bei Bugs, Sicherheitsänderungen und Releases. Diese Datei ist eine Empfehlung für die lokale Claude-Code-Oberfläche; sie schaltet persönliche Plugins nicht automatisch um.

## Grundprinzip: Kontext nur laden, wenn er gebraucht wird

- CLAUDE.md enthält nur Regeln, die bei fast jeder Aufgabe gelten. Projekthistorie, vollständige Architekturberichte und mehrstufige Verfahren bleiben in Dokumentation oder manuell aufrufbaren Skills.
- Bevorzugt gezielte Suche, Smart-Explore und Pyright/LSP statt webserver.py, Logs oder den gesamten Handoff in den Kontext zu kopieren.
- Kleine Bugfixes direkt bearbeiten. Für große Änderungen /build verwenden; wenn erst ein Plan geprüft werden soll, /make-plan und danach /do einzeln verwenden. Nicht mehrere Orchestratoren gleichzeitig starten.
- Tests zuerst fokussiert, danach den vollständigen CI-Satz bei Modulen mit mehreren Abhängigkeiten, Datenmigrationen, Berechtigungen oder vor Release/Deployment. Qualität nicht durch Weglassen wichtiger Tests "sparen".
- Subagents nur für unabhängige Recherche/Prüfung oder getrennte Workstreams. Bei kleinen Änderungen kosten sie meist mehr Kontext und Koordination als sie sparen.
- Nach langem Kontext /compact nutzen; bei einem vollständig neuen Thema /clear und bei Sitzungswechsel /handoff. Nicht dauerhaft komplette Logs/Transkripte in CLAUDE.md kopieren.

## Plugins im Projekt

### Behalten bzw. bei Bedarf verwenden

- **Pyright LSP:** behalten. Hilft in der großen Python-Codebasis beim Springen zu Symbolen und beim Finden von Typfehlern, ohne ganze Dateien zu lesen.
- **Security guidance:** behalten, insbesondere bei OAuth, Sessions, Berechtigungen, Admin-Routen, Discord-Rollen und Datenimporten. Sicherheitsreview ist kein Ersatz für Tests.
- **Frontend design:** behalten, aber für echte Dashboard-/UI-Aufgaben einsetzen, nicht bei reinen Backend-Bugs.
- **Context7:** behalten für aktuelle, versionsspezifische Bibliotheksdokumentation; nur bei Bedarf aufrufen.
- **Commit commands:** die einfache Commit-Funktion behalten. PR-Workflows nur aktiv verwenden, wenn wirklich mit Feature-Branches und Pull Requests gearbeitet wird.
- **Claude mem:** optional behalten, wenn gespeicherte Beobachtungen wirklich Wiederholungen vermeiden. Lange oder thematisch irrelevante automatische Memory-Injektionen abschalten bzw. kürzen, falls die installierte Plugin-Konfiguration das erlaubt.

### Deaktiviert lassen bzw. erst bei Bedarf aktivieren

- **Chrome DevTools MCP:** deaktiviert lassen, bis ein konkreter Browser-/Frontendfehler visuell untersucht werden muss.
- **Supabase expert:** deaktiviert lassen; Pulse nutzt keine Supabase-Architektur.
- **Claude Code setup / automation recommender:** beim Einrichten oder bei einer größeren Tool-Änderung einsetzen, nicht für jeden normalen Codeauftrag. Nach abgeschlossener Einrichtung muss er nicht dauerhaft aktiv bleiben.

## Skills: kleine, sinnvolle Auswahl

### Häufig nützlich

- **smart-explore:** gezielte Code-Navigation, besonders in webserver.py.
- **karpathy-guidelines:** kleine, nachvollziehbare Änderungen statt übermäßig komplexer Refactors.
- **make-plan + do:** für umfangreiche Arbeiten, wenn Planung und Umsetzung kontrolliert getrennt laufen sollen.
- **build:** alternative Ein-Kommando-Orchestrierung für größere Aufgaben, wenn Planen, Umsetzen und Prüfen in einem Ablauf gewünscht sind. Nicht zusammen mit make-plan/do separat parallel orchestrieren.
- **handoff:** kompakter Sitzungswechsel.
- **commit:** nur nach Review und wenn ein Commit ausdrücklich gewünscht ist.
- **babysit:** gezielt für einen tatsächlich bestehenden PR-/Reviewzyklus.
- **frontend-design:** nur für UI-Redesigns.
- **agent-cost-report:** manuell verwenden, wenn Token-/Kostenverbrauch analysiert werden soll.

### Spezialskills nur auf Anfrage

- **build-and-release** und **release:** deaktivieren, wenn sie die bevorzugten build-/release-Abläufe duplizieren. Eine Release-Aktion ist nicht dasselbe wie ein normaler Bugfix.
- **commit-push-pr:** nur behalten, wenn der Arbeitsablauf regelmäßig PRs erzeugt; sonst ist commit weniger komplex.
- **clean_gone:** nur nützlich, wenn regelmäßig mit Branches/Worktrees gearbeitet wird.
- **learn-codebase** und **pathfinder:** nur für ein echtes Codebase-Onboarding oder Architekturprojekt; nicht jede Sitzung erneut ausführen. Für die Alltagsnavigation smart-explore verwenden.
- **ccs-align, cloud-sync, mode-creator, knowledge-agent, how-it-works, timeline-report, weekly-digests:** nur aktiv nutzen, wenn die entsprechende claude-mem-Funktion tatsächlich Teil des Workflows ist.
- **screenpipe, wowerpoint, design-is, what-the, oh-my-issues, standup, childhood:** nur bei passendem konkretem Auftrag aktivieren; sonst keine Standardwerkzeuge für Pulse-Fehlerbehebung.
- **agent-cost-report, version-bump:** manuell; version-bump ist für Plugin-Versionierung gedacht und nicht automatisch für Pulse-Python-Releases passend.

Diese Skills müssen nicht pauschal alle gelöscht werden. Deaktiviere zuerst selten gebrauchte oder doppelte Einträge in der UI; so kannst du sie später wieder einschalten. Am meisten spart es, große Referenztexte nicht ständig einzulesen und Orchestrator-Skills nicht unnötig zu starten.

## Lokale Regeln und Sicherheit

- Kein universelles Auto-Approve für Bash/SSH, keine globale Freigabe für gefährliche Tools und kein dangerously-skip-permissions.
- Dieses Repository setzt absichtlich keine erweiterte gemeinsame Permission-Allowlist über .claude/settings.json, weil das die Freigabe-/Trust-Entscheidungen auf dem Rechner des Nutzers verändern würde.
- Produktiv-Deployment ausschließlich nach ausdrücklicher Anweisung. Lokale VPS-Secrets gehören nicht ins Repository.
- Halte das gewählte hochwertige Modell für schwierige Aufgaben bei. Token sparen wir primär durch fokussierten Kontext, weniger redundante Skill-Orchestrierung und gezielte Tests – nicht durch unnötig schwächere Modelle.

## Offizielle Dokumentation

- Skills und manuell aufrufbare Skill-Inhalte: https://code.claude.com/docs/en/skills
- Projekt-/Pfadregeln und Memory: https://code.claude.com/docs/en/memory
- Erweiterungen, MCP und Subagents: https://code.claude.com/docs/en/features-overview