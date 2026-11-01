# Phase 13.2 – VPS Migration Readiness & Documentation Hardening

Stand: 01.11.2026 · Branch `ops/phase13.2-vps-migration-readiness` · Basis `origin/main` @ `8363ab0` · Pulse 7.17.0

**Nicht ausgeführt (bewusst):** keine VPS-Verbindung, keine Migration, kein Deployment, kein Container-Start/-Stopp, keine Änderung an Rechten, Secrets, IDs, Routen, `custom_id`s, Datenbankstrukturen oder Bot-Funktionen, kein automatisches Deployment aktiviert.

## 1. Geprüfter Ausgangszustand

| Prüfung | Ergebnis |
|---|---|
| PR #94 (Phase 13.1) | gemergt 01.11.2026 06:30 UTC, Merge-Commit `8363ab0` |
| PR #95 (Dependabot `python:3.14-slim` → `3.15-slim`) | gemergt 01.11.2026 06:29 UTC, Merge-Commit `26331e5` |
| `origin/main` | `8363ab0` enthält beide (`8363ab0` → `26331e5` → `f857260`) |
| CI auf `8363ab0` | alle Pflichtprüfungen ✅: syntax (Ruff, compileall, feature_smoke, smoke_test, unittest), Docker-Image bauen, **Docker-Compose-Integration**, Dashboard-E2E (Playwright), Abhängigkeiten, Gitleaks, Lock-Datei + pip-audit („Sicherheit & Abdeckung“), CodeQL (actions, javascript-typescript, python) |
| Deploy | „Deploy Pulse VPS“ per `workflow_run` übersprungen; `PULSE_AUTO_DEPLOY` nicht gesetzt → VPS weiterhin auf `d221b6c` (Phase 12) |
| Arbeitsbaum | nur die drei unversionierten `.claude/*`-Dateien des Nutzers (unberührt) |

## 2. Festgestellte Widersprüche und Lücken

| # | Stelle | Problem |
|---|---|---|
| W1 | `VPS_DEPLOYMENT.md` (Phase-13-Abschnitt, Schritt 7) vs. `docs/PHASE13_1_BERICHT.md` §10 | Doku installierte die Root-Helfer **nach** Migration und Deploy, der Bericht **vorher**. Laut Code ist „vorher“ richtig (siehe 5). |
| W2 | `VPS_DEPLOYMENT.md` Schritt 7 | `install -m 755 scripts/…` installierte aus dem **Checkout** – vor dem Deploy ist das die Phase-12-Version. |
| W3 | `VPS_DEPLOYMENT.md` Schritt 5, `docs/BETRIEB.md` | Deploy „wie gewohnt (Actions **oder Server Control Center**)“. Der Host-Agent startet `deploy/remote_deploy.sh` **aus dem VPS-Checkout** (`deploy/pulse_host_agent.py`) – beim ersten Deploy die Phase-12-Version ohne Markierungsprüfung und ohne Layoutwechsel-Schutz. Nur der Actions-Workflow schickt das Skript des geprüften Commits (`< deploy/remote_deploy.sh` nach Checkout der SHA). |
| W4 | `VPS_DEPLOYMENT.md` | Crash-Neustart wurde **vor** der Funktionsprüfung wieder aktiviert (Schritt 7 nach 6, aber ohne Prüf-Gate) und zusammen mit der Helfer-Installation. |
| W5 | `docs/BETRIEB.md` §4 | „**Automatisch** nach einem Merge auf `main`“ – falsch; der Workflow deployt nur manuell oder mit `PULSE_AUTO_DEPLOY=true`. |
| W6 | `VPS_DEPLOYMENT.md` §4 | „Container startet nach einem Serverneustart automatisch“ und „Dashboard am öffentlichen Serverport 25095“ – falsch: `restart: "no"` (Start durch `pulse-github-update.service`), Port standardmäßig an `127.0.0.1` gebunden. |
| W7 | Python-Version | `Dockerfile`-Kommentar „Keep Python 3.14“ bei `FROM python:3.15-slim`; `VPS_DEPLOYMENT.md` „Python 3.14“; `docs/SECURITY_HARDENING.md` R-07 `python:3.14-slim`. |
| W8 | Testabdeckung Python | Alle CI-Testjobs liefen auf 3.14; auf 3.15 (Produktion) nur Image-Build, Imports, Smoke und Compose-Test – **nicht** die ~1.240 Unittests. |
| W9 | Bedienbarkeit | verkettete Befehle (`touch … && cd … && docker compose stop`), `git`-Befehle als root ohne `safe.directory` (Checkout gehört UID 1000 → „dubious ownership“), keine erwarteten Ausgaben, keine Abbruchpunkte. |
| W10 | Rückweg | Phase-13-Bericht beschrieb noch `cp -a` (überschreibend); Rückweg-Befehle nicht einzeln, `git reset` ohne `safe.directory`. |

## 3. Korrigierte Stellen

- **`VPS_DEPLOYMENT.md`:** Phase-13-Abschnitte vollständig ersetzt durch „Phase 13.1: einmalige Datenmigration auf dem VPS (Schritt für Schritt)“ (41 Schritte, A–F, je Befehl Erklärung, *Erwartet* und ⛔ STOPP), „Fehler nach dem Deploy“ und „Phase 13.1: Rückweg“ (R1–R7). Python 3.15; Neustart-/Port-Aussagen (W6); Crash-Restart-Installationsgrund; Abschnitt 5 nennt die Layoutwechsel-Ausnahme beim Rollback.
- **`docs/BETRIEB.md`:** Deploy „nur von Hand“ / `PULSE_AUTO_DEPLOY`, Verweis auf die Migrationsanleitung, Layoutwechsel-Ausnahme.
- **`docs/PHASE13_BERICHT.md` §9, `docs/PHASE13_1_BERICHT.md` §10:** Hinweis, dass die dortigen Schritte historisch sind und ausschließlich die neue Anleitung gilt.
- **Python:** `Dockerfile`-Kommentar (nur Kommentar), `CLAUDE.md`, `docs/ARCHITEKTUR.md`, `docs/SECURITY_HARDENING.md` (R-07).
- **CI:** neuer Job „Regressionstests mit der Python-Version des Images (3.15)“ in `.github/workflows/python-syntax.yml` (compileall, feature_smoke, smoke_test, unittest auf 3.15 mit `requirements.lock --require-hashes`). Die bestehenden 3.14-Jobs bleiben unverändert (kein Zurückstellen, sondern zusätzliche Abdeckung).
- **Tests:** `tests/test_ops_docs.py` (19 Tests).

## 4. Verifizierte Python-Version

- **Produktion:** `FROM python:3.15-slim` (`Dockerfile`, seit PR #95). Das Image installiert `requirements.lock` mit `--require-hashes` unter 3.15 – der Image-Build und der Compose-Integrationstest auf `8363ab0` sind damit grün (alle Pakete installierbar, Import aller 30 Cogs, Backup/Restore, Healthcheck im 3.15-Container).
- **Tests vorher:** syntax/Unittests, Playwright-E2E und pip-audit auf **3.14**; `requirements.lock` wurde mit 3.14 erzeugt (Kopfzeile, sachlich richtig, unverändert).
- **Tests jetzt:** zusätzlich die vollständige Suite auf **3.15** (neuer CI-Job): Python 3.15.1, 1256 Tests OK.
- Kein technischer Grund für eine Rückstellung auf 3.14 gefunden.
- Host-Python auf dem VPS (Ubuntu 24.04: 3.12) ist nur für `scripts/pulse_migrate_data.py` nötig; das Skript nutzt nur die Standardbibliothek und läuft dort (`from __future__ import annotations`).

## 5. Exakte Migrationsreihenfolge (aus dem Code abgeleitet)

| Grund im Code | Folge für die Reihenfolge |
|---|---|
| `systemd/pulse-github-update.service` → `ExecStart=/usr/local/sbin/pulse-vps-boot-update.sh` bei jedem Boot; der installierte Phase-12-Helfer prüft weder `.pulse-data.json` noch die Sperre | **Root-Helfer zuerst** (vor Stopp, Migration, Deploy); der neue Helfer verweigert ein Update ins neue Layout ohne Markierung `complete` und startet dann nur den installierten Stand |
| `scripts/pulse-crash-restart.sh`: Pausen-Datei `/var/lib/pulse-crash-restart/paused`, startet sonst exited Container | **Pausieren vor dem Stopp**, **Reaktivieren erst nach der Funktionsprüfung** |
| `pulse_migrate_data.py --execute`: verlangt erreichbaren Docker-Daemon und Zustand `exited`/`created`/`dead` | **Stopp vor der Migration** |
| `deploy/remote_deploy.sh`: `uses_data_dir "$SHA" && ! data_dir_ready` → Abbruch vor jeder Änderung | **Migration (+ Verify) vor dem Deploy** |
| `.github/workflows/deploy-vps.yml` checkt die geprüfte SHA aus und schickt deren `remote_deploy.sh`; `deploy/pulse_host_agent.py` nutzt `repo/deploy/remote_deploy.sh` | **Erster Deploy nur über GitHub Actions** |
| alle Root-Skripte: `flock` + `/run/lock/pulse-deploy.lock` fail-closed | `flock` und freie Sperre vor dem Stopp prüfen |

Ablauf in `VPS_DEPLOYMENT.md` (Kurzfassung der 41 Schritte): Snapshot (1) → Komplett-Backup herunterladen (2–3) → SSH, root, `safe.directory` für die Sitzung (4–7) → Commit `d221b6c`, sauberer Checkout, `/healthz`, kein `/opt/pulse/data`, Speicher, `.env`-Prüfung (8–13) → `git fetch origin main`, Ziel-SHA, Fast-Forward (14–16) → Helfer per `git show origin/main:` holen, prüfen, `install -m 755` (17–21) → `flock` und freie Sperre, Docker, Python (22–24) → Crash-Neustart pausieren (25) → `docker stop --time 40 pulse-teamos`, Zustand `exited` (26–27) → Migrationsskript aus `origin/main` (28) → Probelauf (29) → `--execute` + `MIGRIEREN` (30) → `--verify` (31) → Markierung `complete`, Besitzer 1000 (32) → Deploy über Actions mit SHA (33) → Container healthy, `/healthz` mit Discord-Gateway, Commit/Datenverzeichnis, Read-only, Log (34–39) → Discord- und Dashboard-Checkliste (40) → Crash-Neustart wieder an (41).

## 6. Rückweg und Abbruchbedingungen

- **Bis Schritt 29:** nichts verändert; Abbruch = `docker start pulse-teamos`, Pausen-Datei entfernen.
- **Nach Schritt 32, vor 33:** alter Stand startbar; `/opt/pulse/data` ist dann veraltet → umbenennen (nicht löschen), später ab Schritt 25 neu.
- **Deploy meldet `noch nicht migriert`:** nichts verändert, Markierung prüfen.
- **Deploy meldet `KRITISCH … Layoutwechsel`:** Pulse gestoppt, Code auf neuem Commit, Daten unverändert in `/opt/pulse/data` → Ursache beheben und starten, sonst Rückweg.
- **Rückweg R1–R7:** pausieren → stoppen/prüfen → `--rollback` (Probelauf) → `--rollback --execute` + `ZURUECK` (Backups beider Orte, veraltete Kopien nach `.pulse-legacy-<Zeit>/`, Markierung `rolled_back`) → `git reset --hard d221b6c` → `COMPOSE_FILE`/`PULSE_GIT_SHA` setzen, `docker compose up -d --build` → prüfen, Pausen-Datei entfernen. Nichts löschen; der neue Boot-Helfer installiert danach nichts, solange `/opt/pulse/data` nicht `complete` ist.

## 7. Testnachweise

**Lokal und in GitHub Actions:** Abschnitt 7a.

**Neue Dokumentationstests (`tests/test_ops_docs.py`, 19 Tests):** Python-Version Dockerfile ↔ `VPS_DEPLOYMENT.md`/`ARCHITEKTUR.md`/`CLAUDE.md`/`SECURITY_HARDENING.md`, kein abweichender Versionskommentar, CI-Job auf der Image-Version mit voller Suite · Schritte 1–41 lückenlos nummeriert · Snapshot/Backup zuerst · Reihenfolge Fetch → Helfer → `flock`/Sperre → pausieren → stoppen → Skript → Probelauf → Migration → Verify → Deploy → Health → Dashboard → Crash-Neustart · Helfer nur aus `origin/main` · erster Deploy nur über Actions · Crash-Neustart erst nach Schritt 40 · jeder kritische Schritt mit *Erwartet* und STOPP · ein Befehl pro Block, keine Heredocs, keine Linux-Befehle in PowerShell-Blöcken · Bestätigungswörter, Flags, Ausgabetexte, Container-Name, systemd-Pfade, Pausen-Datei und Sperre existieren im Code · Host-Agent nutzt tatsächlich das Checkout-Skript · Berichte verweisen auf die Anleitung · Rückweg vollständig und ohne `cp -a` · keine destruktiven Befehle · Deploy-Workflow nur manuell oder mit `PULSE_AUTO_DEPLOY`, Doku behauptet kein automatisches Deploy.

### 7a. Ergebnisse

| Lauf | Ergebnis |
|---|---|
| Lokal: Ruff, compileall, `feature_smoke`, `smoke_test` | ✅ |
| Lokal: `unittest discover` (Python 3.14.6, inkl. `tests/test_ops_docs.py`) | ✅ **1256 Tests OK**, 4 übersprungen; keine Testartefakte im Checkout |
| GitHub PR #96: **Regressionstests mit Python 3.15** (neu) | ✅ Python **3.15.1**, `feature_smoke` + `smoke_test` OK, **1256 Tests OK**, 17 übersprungen (Tests, die Werkzeuge außerhalb dieses Jobs brauchen, z. B. Playwright-Browser) |
| GitHub PR #96: syntax (3.14), Docker-Image, Docker-Compose-Integration, Dashboard-E2E, pip-audit, Abhängigkeiten, Gitleaks, CodeQL | ✅ alle grün |

## 8. Verbleibende Risiken

- Die Anleitung ist geprüft gegen Code und Tests, aber noch **nicht auf dem echten VPS** ausgeführt. Abweichungen (z. B. anderer installierter Commit, fehlender Crash-Timer, Docker-Version) fängt jeweils ein STOPP-Punkt ab.
- Python 3.15 ist neu (Dependabot): Die volle Suite läuft jetzt auch auf 3.15.1 grün; Playwright-E2E und pip-audit laufen weiterhin auf 3.14.
- Die echte Discord-Anmeldung im neuen Container lässt sich nur auf dem VPS prüfen (Schritte 35, 38, 40).
- Downtime ca. 10–15 Minuten (Image-Build auf dem 2-GiB-VPS); bei Fehlern im Layoutwechsel bleibt Pulse bewusst offline statt mit veralteten Daten zu laufen.
- Nach dem Merge dieses PRs ist ein neuerer `main`-Commit das Deploy-Ziel (nur Doku/Tests/CI geändert, Anwendungscode identisch zu `8363ab0`).
