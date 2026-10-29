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

## Offen / nächste Schritte
- Phase C/E: blockierende I/O in async, print vs logging, Shutdown.
- Phase F/G: Dockerfile/Compose/Deploy-Feinschliff (Action-Pinning, Build-RAM auf VPS).
- Phase H: voller CI-Satz, Diff-Review, Abschlussbericht.
