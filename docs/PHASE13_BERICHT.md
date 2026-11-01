# Phase 13 – Production Architecture & Data Isolation

Stand: 01.11.2026 · Branch `hardening/phase13-data-isolation` (Basis `main` @ `d221b6c`, Pulse 7.17.0) · Bezug: SEC R-05/R-06 (`docs/SECURITY_HARDENING.md`), D-06 (`docs/VERBESSERUNGSPLAN.md`).

**Nicht ausgeführt (bewusst):** keine VPS-Migration, kein Deployment, kein Merge, keine Discord-Aktion. Die Migration auf dem VPS ist ein manueller, freigabepflichtiger Schritt (Abschnitt 9).

## 1. Architektur vorher und nachher

| | Vorher | Nachher |
|---|---|---|
| Code | `./:/app` bind-gemountet, **beschreibbar** für den Container (Datei gehört UID 1000) | nur im Image, gehört root, `read_only: true` (gesamtes Root-Dateisystem) |
| Laufzeitdaten | untracked im Checkout `/opt/pulse/Pulse` zwischen den Code-Dateien | `/opt/pulse/data` → `/data` (`PULSE_DATA_DIR`), einziger beschreibbarer Pfad neben `/tmp` (tmpfs 64 MB, `noexec`) |
| Secrets | `.env` im Container sichtbar **und beschreibbar** (R-06) | nur `env_file` → Umgebungsvariablen; die Datei ist nicht im Container |
| Deploy-/Systemdateien | `.git`, `deploy/`, `scripts/`, `systemd/`, `compose.yaml`, `Dockerfile` als `:ro` im Container | im Container nicht sichtbar |
| Commit im Systemstatus | aus `/app/.git` gelesen | `PULSE_GIT_SHA` (Build-Argument → `ENV`), Fallback `.git` außerhalb von Docker |
| Pfade im Code | gemischt: relativ zum Arbeitsverzeichnis (`"config.json"`, `"buero_system.db"`, `"uploads/…"`) und relativ zu `__file__` | zentral `pulse_paths.DATA_DIR`; Code-Assets (`static/`, `assets/*.b64`, `cogs/`) bleiben `BASE_DIR`/`__file__` |
| Leeres Datenverzeichnis | – | Start verweigert (Exit 78), Compose startet ohne Host-Ordner gar nicht (`create_host_path: false`) |

**Rückwärtskompatibel:** Ohne `PULSE_DATA_DIR` ist `DATA_DIR = Path(".")` – exakt die alten relativen Namen (`"team_data.json"`, `"backups"`, `".session_secret"`, `"pulse.db"`), `main.prepare_process()` wechselt weiter ins Code-Verzeichnis. Bot-Hosting ohne Docker und lokale Entwicklung laufen unverändert. Keine URL, kein Slash-Command, keine `custom_id`, kein Datenformat geändert; gespeicherte relative Pfade (z. B. `assets/support_wait.mp3`, `uploads/voice_support_audio/…`) bleiben gültig und werden jetzt relativ zu `DATA_DIR` aufgelöst.

## 2. Geänderte Module

**Neu:** `pulse_paths.py` (zentrale Pfadverwaltung, Start-Prüfung, Markierung), `scripts/pulse_migrate_data.py` (Migration), `tests/test_phase13_data_isolation.py` (45 Tests), `tests/container_smoke.py` (Start-Smoke im echten Container/CI und lokal), dieser Bericht.

**Anwendung (31 Module):** `main.py`, `webserver.py`, `pulse_db.py`, `pulse_next.py`, `pulse_ultimate.py`, `pulse_panels.py`, `pulse_pro.py`, `pulse_features.py`, `pulse_admin.py`, `pulse_intelligence.py`, `pulse_retention.py`, `pulse_server_control.py`, `pulse_ticket_design.py`, `pulse_transcripts.py`, `pulse_verification.py`, `pulse_welcome.py`, `cogs/abmeldung.py`, `cogs/activity_check.py`, `cogs/ai_moderation.py`, `cogs/announcement_archive.py`, `cogs/buero_system.py`, `cogs/flaggenquiz.py`, `cogs/pulse_suite.py`, `cogs/role_nickname_prefixes.py`, `cogs/rp_verwaltung.py`, `cogs/social_promo.py`, `cogs/teamliste.py`, `cogs/tickets.py`, `cogs/verification.py`, `cogs/voice_support.py`, `cogs/welcome.py`.

**Container/Deploy:** `Dockerfile`, `compose.yaml`, `.dockerignore`, `.gitignore`, `.env.example`, `deploy/remote_deploy.sh`, `deploy/rollback_code.sh`, `deploy/vps_bootstrap.sh`, `scripts/pulse-vps-boot-update.sh`, `scripts/pulse-crash-restart.sh`, `.github/workflows/python-syntax.yml`.

**Tests/Werkzeuge angepasst:** `tests/feature_smoke.py`, `tests/test_deploy_config.py`, `tests/test_full_backup.py` sowie neun Tests und drei Werkzeuge (`tools/load_test.py`, `perf_baseline.py`, `preview_server.py`), die `ws.BASE_DIR` auf einen Temp-Ordner umbiegen und jetzt zusätzlich `ws.DATA_DIR` setzen. `tools/data_map.py` + `docs/DATENLANDKARTE.md` (neu erzeugt).

**Doku:** `VPS_DEPLOYMENT.md`, `docs/BETRIEB.md`, `docs/ARCHITEKTUR.md`, `docs/SECURITY_HARDENING.md`, `docs/VERBESSERUNGSPLAN.md`, `docs/SERVER_CONTROL_CENTER.md`, `CHANGELOG.md`.

## 3. Umgestellte Datenpfade

| Ablage | vorher | jetzt |
|---|---|---|
| `pulse.db` | `PULSE_DB_PATH` oder `"pulse.db"` (cwd); Compose `/app/pulse.db` | `PULSE_DB_PATH` oder `DATA_DIR/pulse.db`; Compose fest `/data/pulse.db` |
| `abmeldungen.db`, `activity_check.db`, `teamliste.db` | `__file__`-Ordner | `DATA_DIR` (Cogs und `webserver.DB_ABMELDUNGEN/ACTIVITY_DB`) |
| `buero_system.db`, `flaggenquiz.db`, `rp_verwaltung.db` | relativ (cwd), teils doppelt (`pulse_features`, `pulse_pro`) | `DATA_DIR` |
| `pulse_suite.db` | `__file__`-Ordner | `DATA_DIR` |
| `team_data.json`, `config.json`, `applications.json`, `shifts.json`, `logs.json`, `audit_logs.json`, `meetings.json` | relativ (cwd) | `str(DATA_DIR / …)` |
| `pulse_announcement_archive.json`, `role_nickname_state.json`, `social_promo_sources.json`, `ai_moderation_cases.json` | `__file__`-Ordner (Env-Overrides bleiben) | `DATA_DIR` |
| `backups/` (JSON-, ZIP-, Komplett-Backups, Migrations-Backups von `pulse_migrations`) | relativ bzw. `__file__` | `DATA_DIR/backups` |
| `transcripts/` (+ Anhänge) | relativ, `os.makedirs("transcripts")` beim Import von `cogs/tickets.py` | `DATA_DIR/transcripts` |
| `uploads/ticket_panels`, `uploads/welcome_banners`, `uploads/voice_support_audio` | teils relativ, teils `BASE_DIR` | `DATA_DIR/uploads/…` |
| `attachments/` (Fall-Beweise) | `__file__`-Ordner | `DATA_DIR/attachments` |
| Wartemusik `assets/support_wait.*` | in den Code-Ordner `assets/` geschrieben | `DATA_DIR/assets/support_wait.*` (Konfigurationswert unverändert relativ) |
| `.session_secret` | relativ | `DATA_DIR/.session_secret` |
| `logs/pulse.log` | `BASE_DIR/logs` | `DATA_DIR/logs` |
| Temp-Dateien (Restore-Staging) | `/tmp` | `TMPDIR=DATA_DIR/tmp` (Plattenplatz statt RAM-tmpfs; 768-MiB-Budget) |
| Speicherplatz-/Wachstumsanzeige, Systemgesundheit, Backup-Job im Server Control Center | `ws.BASE_DIR` | `ws.DATA_DIR` |

Bleiben im Code: `static/`, `assets/voice_support_after_hours.mp3.b64`, `support_knowledge.md`, `team_ki_vorlagen.md`, `cogs/`-Liste im Systemstatus. `.env` bleibt auf dem Host neben `compose.yaml`.

## 4. Sicherheitsverbesserungen

- **R-05 erledigt:** Ein kompromittierter Pulse-Prozess kann keine `*.py`-Datei mehr verändern (root-eigener Code, read-only Root-FS, im CI geprüft: Schreibversuch in `/app` muss scheitern).
- **R-06 erledigt:** `.env` ist im Container weder lesbar noch schreibbar; Compose-Interpolation kann vom Container nicht mehr beeinflusst werden.
- Checkout (`.git`, Deploy-Skripte, systemd-Units) ist im Container unsichtbar statt nur `:ro`.
- `/tmp` als tmpfs mit `noexec,nosuid,nodev`, 64 MB.
- Bestehende Härtung unverändert: Nicht-root (UID 1000), `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit: 256`, `mem_limit 768m`/`memswap_limit 1024m`, Healthcheck, Log-Rotation, `restart: "no"`, `COMPOSE_FILE=compose.yaml` in allen root-Skripten.
- `PULSE_DB_PATH`, `PULSE_AI_MODERATION_CASES_FILE`, `PULSE_SOCIAL_PROMO_FILE` müssen im Datenverzeichnis-Layout innerhalb von `DATA_DIR` liegen, sonst Start-Abbruch (kein Schreiben in den Code-Ordner oder an Backups vorbei). Compose setzt `PULSE_DATA_DIR`/`PULSE_DB_PATH` fest, eine alte `PULSE_DB_PATH=/app/pulse.db` in `.env` wirkt nicht mehr.
- Byte-Code wird beim Build kompiliert (`compileall`), da `/app` zur Laufzeit nicht beschreibbar ist.

## 5. Migration und Rückfallkonzept

`scripts/pulse_migrate_data.py` (nur Standardbibliothek, läuft auf dem Host):

- **Probelauf ist Standard:** listet Dateien, Größe, identische Dateien, Konflikte, prüft freien Speicher – schreibt nichts (getestet).
- **Echte Migration nur mit `--execute` + Bestätigung `MIGRIEREN`** (interaktiv oder `--confirm MIGRIEREN`); Abbruch, wenn der Container `pulse-teamos` läuft.
- **Vorher Backup:** `tar.gz` aller migrierten Dateien + Manifest, danach Rücklese-Prüfung per SHA-256; erst dann wird kopiert.
- **Keine Überschreibung:** identische Zieldatei → übersprungen (idempotent), abweichende → Konflikt, Abbruch vor jeder Kopie. Kopie über exklusive Temp-Datei + `fsync`.
- **Prüfung:** Anzahl und SHA-256 jeder Datei, `PRAGMA integrity_check` jeder `.db`; WAL/SHM-Dateien werden mitkopiert.
- **Rechte:** UID/GID 1000, Ordner 0750, Dateien 0640, `.session_secret` und Markierung 0600.
- **Abbruch erkennbar:** Markierung `.pulse-data.json` steht während des Kopierens auf `in_progress`, erst nach erfolgreicher Prüfung `complete`. Pulse (`pulse_paths.verify_data_dir`) und alle Deploy-Wege (`remote_deploy.sh`, Boot-Update) verweigern Start/Deploy bei `in_progress` oder fehlender Markierung. Erneuter Lauf setzt fort.
- **Quelle bleibt unverändert** (nur gelesen; getestet per Prüfsummenvergleich). `.env` und versionierte Dateien werden nie migriert.
- **Rückweg:** `rollback_code.sh` lehnt Code-Rollbacks auf einen Commit vor Phase 13 ab (würde mit veralteten Daten starten). Manueller Rückweg mit Sicherung beider Orte und Zurückkopieren: `VPS_DEPLOYMENT.md` → „Phase 13: Rückweg“. Der automatische Rollback in `remote_deploy.sh` beim *ersten* Umstieg geht auf das alte Layout mit den unveränderten Altdaten im Checkout zurück und weist darauf hin, dass im kurzen Fenster nach `/data` Geschriebenes dort erhalten bleibt.

## 6. Tatsächlich ausgeführte Tests und Ergebnisse (lokal, Windows 11, Python 3.14.6)

| Prüfung | Ergebnis |
|---|---|
| `python -m ruff check .` | ✅ All checks passed |
| `python -m compileall -q .` | ✅ |
| `python tests/feature_smoke.py` | ✅ OK |
| `python tests/smoke_test.py` | ✅ OK |
| `python -m unittest discover -s tests -p 'test_*.py'` (mit `PULSE_TEST_BASH` = Git Bash) | ✅ 1190 Tests OK, 4 übersprungen; danach 1 neuer Crash-Restart-Test (Exit 78) ergänzt, Modul einzeln ✅ 8/8 |
| `tests/test_phase13_data_isolation.py` (45 Tests) | ✅ inkl. echter Bash-Läufe von `remote_deploy.sh` gegen Fake-git/docker |
| `tests/container_smoke.py` lokal mit `PULSE_DATA_DIR`=Temp | ✅ Start-Prüfung, `init_runtime`, JSON, Transkript, Upload, Wartemusik, Komplett-Backup + Verify + Restore-Test + echter Restore, Commit aus `PULSE_GIT_SHA`; `git status --ignored` vorher/nachher identisch (nichts in den Code-Ordner geschrieben) |
| `python main.py` mit leerem `PULSE_DATA_DIR` | ✅ Exit 78, Ordner bleibt leer |
| Migration Probelauf gegen den lokalen Checkout | ✅ 13 Dateien erkannt, nichts geschrieben |

Gefunden und behoben durch die neuen Bash-Läufe: Die neue `.env`-Auswertung in `remote_deploy.sh`/Boot-Update hätte ohne `.env` wegen `set -e` + `pipefail` mit Exit 2 abgebrochen (eigener Zwischenstand, nie committet).

### 6a. CI auf GitHub (Branch, manuell gestartet: Run 41256460686)

| Job | Ergebnis |
|---|---|
| syntax (Ruff, compileall, Smoke-Tests, unittest discovery unter Linux) | ✅ |
| **Docker-Image bauen** – Compose-Layout-Prüfung, Build mit `PULSE_GIT_SHA`, Imports/ffmpeg/Nicht-root, **Read-only-Container mit `/data`**: `tests/container_smoke.py` im Container, Schreibversuch in `/app` scheitert, leeres `/data` → Exit 78 und bleibt leer | ✅ |
| Dashboard-E2E (Playwright) | ✅ |
| Abhängigkeiten auf Sicherheitslücken | ✅ |
| Geheimnis-Scan (Gitleaks) | ❌ – **nicht durch Phase 13**: 3 bekannte Fehlalarme (Variablennamen) in alten Commits `82f3180`, `1094de3`, `f392d6a`. Beim manuellen Start scannt Gitleaks die gesamte Historie, bei Push/PR nur den neuen Bereich (dort ist `main` grün). |

Zusätzlich lokal mit Git Bash ausgeführt (unter Windows sonst übersprungen): die echten Bash-Läufe von `remote_deploy.sh` und `pulse-crash-restart.sh` gegen Fake-`git`/`docker`.

## 7. Nicht ausgeführte Prüfungen

- **Docker lokal nicht verfügbar** (kein Docker/WSL auf dem Entwicklungsrechner): Image-Build und Read-only-Container liefen nur in der GitHub-CI (✅, Abschnitt 6a). Ein `docker compose up` mit dem kompletten Compose-Stack (inkl. Mount `./.pulse-agent` und Healthcheck mit Discord) wurde nirgends ausgeführt – das passiert erst beim Deploy auf dem VPS.
- Kein Test auf dem echten VPS, keine echte Migration, kein Deployment, kein Discord-Start (kein Token).
- Playwright-E2E (`tests/test_e2e_dashboard.py`) nicht lokal ausgeführt; keine UI-Änderung in dieser Phase.

## 8. Verbleibende Risiken

- **Erster Umstieg auf dem VPS** ist der kritische Moment: Wird nach der Migration versehentlich der alte Container gestartet, schreibt er wieder in den Checkout; die neue Version sähe diese Änderungen nicht. Gegenmaßnahme: Crash-Neustart pausieren, Migration und Deploy direkt nacheinander (Anleitung).
- Der installierte Boot-Update-Helfer (`/usr/local/sbin/…`) ist bis zur Neuinstallation die **alte** Version: Er prüft die Markierung nicht selbst. Ein Boot vor der Migration würde den neuen Stand bauen; der Container verweigert dann den Start (Exit 78, ungesund) und der Helfer rollt auf den alten Commit zurück. Sicher, aber mit Ausfallzeit – Helfer nach dem Merge neu installieren.
- Bind-Mount `./.pulse-agent` bleibt beschreibbar (wie vorher der ganze Ordner); enthält nur den Socket, nichts, was root ausführt.
- Docker legt den Mountpunkt `/app/.pulse-agent` im Container an; bei `read_only: true` funktioniert das in aktuellen Docker-Versionen – im CI-Lauf mit `docker compose config` nur statisch geprüft, ein `compose up` erfolgt erst auf dem VPS.
- Umgebungsvariablen in `.env`, die auf alte absolute Pfade unter `/app` zeigen (z. B. `SUPPORT_KNOWLEDGE_FILE`), werden nur für die drei Datenablagen geprüft.
- `TMPDIR` liegt im Datenverzeichnis: abgebrochene Restores können dort Reste hinterlassen (werden nicht automatisch gelöscht – Regel „keine Laufzeitdaten löschen“).

## 9. Erforderliche manuelle VPS-Schritte (erst nach Freigabe und Merge)

> **Phase 13.2:** Die maßgebliche, geprüfte Reihenfolge der VPS-Migration (Root-Helfer zuerst, Deploy nur über GitHub Actions, Crash-Neustart zuletzt) steht ausschließlich in `VPS_DEPLOYMENT.md` → „Phase 13.1: einmalige Datenmigration auf dem VPS (Schritt für Schritt)“. Die Schritte in diesem Abschnitt sind historisch.

Ausführlich in `VPS_DEPLOYMENT.md` → „Phase 13: einmalige Datenmigration auf dem VPS“. Kurzfassung:

1. Vionity-Snapshot + Komplett-Backup im Dashboard.
2. `touch /var/lib/pulse-crash-restart/paused`; `docker compose stop pulse`.
3. Probelauf `python3 pulse_migrate_data.py --source /opt/pulse/Pulse --target /opt/pulse/data`.
4. `--execute` (Bestätigung `MIGRIEREN`), danach `--verify`.
5. Deploy des Phase-13-Commits (Actions oder Server Control Center).
6. Prüfen: `/healthz`, Systemstatus (Commit), Tickets/Bewerbungen/Schichten/Transkripte/Wartemusik/Backups; `touch /app/x` im Container muss scheitern.
7. Crash-Neustart reaktivieren; `pulse-crash-restart.sh` und `pulse-vps-boot-update.sh` neu nach `/usr/local/sbin` installieren; falls der Host-Agent genutzt wird: `.pulse-agent` gehört UID 1000.
