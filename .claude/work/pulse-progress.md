# Pulse – Arbeitsprotokoll Master-Workflow

Branch: `refactoring-pulse` (von `origin/main` 84fb86e, ohne Upstream – kein Push laut Auftrag).
Fremde, unversionierte Dateien (nie mitcommitten): `.claude/launch.json`, `.claude/pr_body_phase8.md`, `.claude/settings.json`.

## Phase A – Baseline (2026-10-29) ✅
- Python 3.14.6 lokal (Windows). Ruff ok, compileall ok, feature_smoke ok, smoke_test ok, unittest: 983 Tests ok (3 skipped).
- Stand: Projekt mehrfach auditiert (docs/audits/, PR #76/#77). Deploy (deploy-vps.yml + deploy/remote_deploy.sh) hat bereits:
  Concurrency, verifizierte SHA, Known-Hosts-Pflicht, Backup, Healthcheck, Rollback, minimale Permissions, Auto-Deploy nur bei Variable.

## Phase B – Sicherheit/Datenintegrität ✅
- Route-Matrix: GET-Routen mit Pfadparametern wurden im Test ohne Login übersprungen → jetzt mit Dummywert geprüft
  (alle verweigern; keine Lücke gefunden, nur Testlücke geschlossen). `tests/test_permissions.py`
- JSON: `load_json` sichert beschädigte Dateien und sperrt Schreiben (ok). Cogs mit eigenen Lesern lesen nur, fail-closed.
- HTTP-Clients: alle mit Timeout, Limits, aclose (ok).

## Phase D – Speicher (teilweise) ✅
- `cogs/voice_support.py`: `_case_action_locks` wuchs pro Fall unbegrenzt → begrenzt auf 200, nur freie Locks werden entfernt.
  Test: `tests/test_voice_support_locks.py`.
- Geprüft ok: Modul-Caches (_rate_hits, _last_click, _VERIFY_CACHE, _JOBS), verification-Locks, welcome._warned.
- Bewusst gelassen: `recovery._last_sync_by_user` (nur Admin-Befehl, winzig).

## Phase C/E – Architektur, Logging ✅ (keine Änderung nötig)
- AST-Suche: keine time.sleep/subprocess/requests in async-Funktionen; 2 „create_task“-Treffer sind DB-Funktionen.
- Logging: RotatingFileHandler 1 MB × 3, Docker json-file 10 MB × 5. /healthz = Readiness (503 bis bereit), /health ähnlich.
- Kein Architekturumbau: kein belegter Nutzen gegenüber dem Risiko (CLAUDE.md Regel 1).

## Phase F/G – Docker, CI, Deploy ✅
- Bug: remote_deploy.sh ließ nach fehlgeschlagenem Build den Checkout auf dem Ziel-Commit → Build separat, Reset auf PREV.
  Laufzeittests `RemoteDeployRunTests` (lokal mit `PULSE_TEST_BASH="C:/Program Files/Git/bin/bash.exe"`).
- Dockerfile + CI-Job syntax installieren requirements.lock mit Hashes; Test: Lock erfüllt Bereiche aus requirements.txt.
  Linux-Wheels cp314 für alle 34 Pins per `pip download --platform manylinux…` geprüft (lokal kein Docker).
- CI-Job `image` (docker build, Imports, ffmpeg, Nicht-Root), gitleaks auf SHA gepinnt.
- Entscheidung offen (Nutzer): compose `restart: "no"` → nach Absturz/OOM kein Neustart (bewusst wegen Boot-Update-Race).
- Nicht umgesetzt: Registry-Image (GHCR) – braucht Zugangsdaten auf dem VPS; Build-Last ist gering (nur Wheels).

## Phase H – Abschluss ✅
- Voller CI-Satz lokal: ruff ok, compileall ok, feature_smoke ok, smoke_test ok, unittest 992 ok (3 skipped).
- E2E (Playwright, PULSE_E2E=1): 12 Tests ok.
- Nicht ausgeführt: Docker-Build (kein Docker lokal; läuft im neuen CI-Job `image`), kein Push, kein Deploy.

## Offen (Entscheidung Nutzer)
- compose `restart: "no"` vs. Neustart nach Absturz/OOM.
- Branch `refactoring-pulse` pushen / PR – laut Auftrag nicht ausgeführt.
- Dependabot-pip-PRs brauchen ab jetzt eine neu erzeugte requirements.lock (Test meldet das).
