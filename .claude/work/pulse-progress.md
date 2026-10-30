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

## Nachtrag – Neustart nach Absturz/OOM ✅ (Nutzer: „Ja, Option prüfen“)
- `restart: "no"` bleibt (Boot-Race). Neu: scripts/pulse-crash-restart.sh + systemd/pulse-crash-restart.{service,timer}:
  nur `docker start` bei exited + (Exit≠0 oder OOM); nie bei Boot-Update (activating), Deploy-Sperre
  /run/lock/pulse-deploy.lock (auch in remote_deploy.sh), Pausendatei; max. 3/h. Läuft aus /usr/local/sbin, nicht aus dem Repo.
- Tests: tests/test_crash_restart.py (7). Voller Satz: 999 ok (3 skipped). Installation auf dem VPS: manuell (VPS_DEPLOYMENT.md).

## Erledigt nach Mission 1
- PR #82 gemergt, 5e94a99 deployt (2026-10-29), Crash-Restart-Timer auf dem VPS installiert und aktiv.
- Hinweis bleibt: Dependabot-pip-PRs brauchen eine neu erzeugte requirements.lock (Test meldet das).

# Mission 2 (2026-10-29, Branch refactoring-pulse ab 5e94a99)

## Phase 1 – GitHub-Gates ✅
- API gelesen: main ohne Branchschutz/Rulesets; Environment production ohne Reviewer/Branch-Regel; PULSE_VPS_* sind Repo-Secrets;
  Default-Workflow-Rechte read. → manuelle Schritte in VPS_DEPLOYMENT.md §5 (nicht selbst geändert).
- Fix: blockierendes Audit (`dependency-audit`) prüfte requirements.txt statt der ausgelieferten requirements.lock → jetzt Lock mit Hashes.
  Lokal: `pip-audit -r requirements.lock --require-hashes` → keine bekannten Schwachstellen.
- Härtung: manueller Deploy bricht ab, wenn nicht von refs/heads/main gestartet (Test test_manual_deploy_only_from_main).

## Phase 2 – Backups/Restore ✅
- Befund: Komplett-Backup (D-01) deckt DBs/JSON/uploads/attachments/transcripts ab; Lücke: Wartemusik assets/support_wait.* (Dashboard-Upload,
  nicht in git) fehlte → MEDIA_FILES in pulse_backup (Sicherung + Restore-Ziel).
- Befund: restore_test prüfte nur Hashes + DB-Integrität → jetzt vollständige Probewiederherstellung in Temp-Dir (eigene Ziele, nie
  _target_for, das pulse.db auf den Live-Pfad abbildet), Tabellen/Zeilen, JSON, Datei-Hashes, fehlende Restore-Ziele.
- Tests: test_full_backup (+3), Gegenprobe ohne Fix 4 F/1 E; 73 Tests Backup/Admin/ServerControl ok. DATENLANDKARTE neu erzeugt.

## Phase 3 – Code/Daten-Trennung (Teil 1) ✅
- Befund (Sicherheit, bestätigt im Code): Container schreibt über ./:/app auch .git/, compose.yaml, deploy/, scripts/, systemd/ –
  root führt davon git (Hooks/fsmonitor), compose (Override-Datei) und Skripte aus → möglicher Container→root-Weg.
- Fix: ro-Untermounts in compose.yaml; COMPOSE_FILE=compose.yaml in remote_deploy/rollback_code/boot-update + Host-Agent-Env.
  Pulse liest .git nur (read_git_head), importiert nichts aus deploy/scripts. CI: docker compose config + ro-Prüfung.
- Tests: HostBoundaryTests (3); Deploy/Boot/Crash/ServerControl/HostAgent-Suiten ok. Lokal kein Docker → Compose nur in CI.
- Nicht umgesetzt (begründet): PULSE_DATA_DIR/Datenumzug (~70 Pfadstellen, 22 Module, Produktionsdaten-Umzug) – dokumentiert.
- Wirksam auf dem VPS erst nach dem nächsten Deploy.

## Phase 4 – Routen (Schritt 1) ✅
- Laufzeit-Analyse: 372 Routen, 1 Doppelregistrierung (GET /ultimate, bekannt, Löschen wartet auf E-6); Test fängt neue Duplikate.
- /healthz aus webserver.py nach pulse_health_api.py (flach via add_api_route). Befund: include_router (FastAPI 0.162) fügt
  _IncludedRouter ohne .path ein → bricht pulse_pro.register; APIRouter-Umstieg braucht erst angepasste Route-Walker.
- Tests: Snapshot/Permissions/Healthz/Alerts ok, beide Smoke-Tests ok. Snapshot + ROUTEN.md neu erzeugt.
- Vorfall: fehlgeschlagenes `git stash push` → `pop` wendete alten Nutzer-Stash teilweise an; per `git restore --source=HEAD`
  der 4 Dateien zurückgenommen, stash@{0} unverändert erhalten. Künftig keine stash-Vergleiche (Memory-Regel).

## Phase 5/6 – Persistenz + Jobs ✅
- AST-Suche load_json→await→save_json: 3 echte Lost-Updates (cleanup_expired_warn_restrictions, process_probation_warning,
  decide_app) + 1 Fehlalarm (send_interview_reminders lädt frisch). Fix: track_member_entry / Einzelschreiben unter _io_lock.
- Jobs: 6 @tasks.loop ohne Register → @tracked(pausable=False) + record_error; Bug in pulse_jobs.job(): last_ok trotz
  record_error im Lauf → verdeckte Fehler. Test: jede @tasks.loop muss @tracked haben.
- Tests: test_concurrent_writes (+3, Gegenprobe im Worktree 2F/1E), test_job_coverage (3); 166 verwandte Tests ok.
- Rechte (6A): Route-Matrix (Phase B, Mission 1) + bestehende Admin-/Rechte-Tests; kein neuer Befund, keine Änderung.

## Phase 7 – Performance (teilweise) ⏸ Abbruch wegen Nutzungslimit
- tools/load_test.py: --concurrency, --soak-seconds, p95/p99, Fehlerrate, cgroup-Speicher (rückwärtskompatibel).
- Befund durch Parallel-Test: GET /ultimate/team → 500 (IntegrityError UNIQUE ultimate_profiles.user_id, Prüfen-dann-Einfügen
  in pulse_ultimate.ensure_profile, Worker-Threads). Fix: INSERT OR IGNORE. Lokal (Windows, 50 Mitglieder, 3 Clients): 0 Fehler.
- Messwerte nur lokal/Windows, nicht vergleichbar mit VPS.

## Phase 7 Abschluss + Phase 8–10 (2026-10-30) ✅
- Race-Test tests/test_ultimate_profiles.py (alter Stand rot, neu grün). Manueller Workflow .github/workflows/perf.yml
  (nur workflow_dispatch, 768m/1024m/2 CPUs, kein Secret, kein Deploy-Gate).
- Phase 8: keine Änderung – logs.json = Moderationsakten (Nachweise), shifts.json-Historie = Gesamtzeiten; Größenwarnung
  (D-07) und pulse_retention (Dry-Run, aus, nur mit frischem Backup) decken den Bedarf.
- Phase 9: BETRIEB.md (Stand, Restore-Test, Jobs, Crash-Neustart), Protokoll bereinigt.
- Phase 10: keine neue Funktion – Incident Center/Deploy-Vorabprüfung/Kapazität werden von Systemstatus, Server Control
  Center, pulse_alerts, deploy-vps.yml und dem Lasttest abgedeckt; Policy-Simulator existiert (Anti-Nuke-Simulation).
- Danach: voller CI-Satz, E2E, Push, PR, Merge, Deploy (Nutzer am 2026-10-30: „mach alle Änderungen fertig und ziehe sie
  dann auf den Server“).
