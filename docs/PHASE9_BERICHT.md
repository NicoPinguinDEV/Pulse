# Phase 9 – Startup, Import und Lifecycle Hardening (Pulse 7.17.0)

Stand: 31.10.2026 · Branch `phase9-startup-lifecycle` · Basis `db29bbe` (main, PR #88) · Python 3.14.6 lokal

Grundsatz wie in Phase 8: **nur nachgewiesene Probleme beheben.** Jeder Befund hat einen Test, der mit dem
Code von `db29bbe` fehlschlägt (Gegenprobe: `tests/test_lifecycle.py` auf `main` → 7 Fehlschläge, 4 Fehler von 12).

## 1. Ausgangszustand (geprüft, nicht aus älteren Berichten übernommen)

| Bereich | Zustand vor Phase 9 |
|---|---|
| Startpfad | `python main.py` → beim **Import** von `main`: `os.chdir`, Log-Datei `logs/pulse.log`, `.env` laden, Konfigurationsprüfung, `import webserver`, `CustomBot()` erzeugen. Danach `init_runtime()` und `bot.run()`. |
| `import webserver` | `os.chdir(BASE_DIR)`, `.env` laden; die am Dateiende registrierten Module (`pulse_features`, `pulse_pro`, `pulse_ultimate`, `pulse_next`) legen Tabellen in `pulse.db` an und schreiben Default-Settings (Phase-8-Bericht: „Offen, Freigabe nötig“). |
| Lifespan (FastAPI) | `init_runtime()` → `_start_background_tasks()` → `yield` → `_stop_background_tasks()`; Start **außerhalb** des `try`. |
| Bot-Shutdown | `CustomBot.close()` stoppt uvicorn, dann `commands.Bot.close()` (entlädt alle Extensions/Cogs → `cog_unload`). |
| Hintergrundaufgaben | 21 `@tasks.loop` (alle über `pulse_jobs` erfasst), Automation-Loop (`pulse_ultimate`), Stall-Watchdog-Thread, Backup-Loop (Lifespan), mehrere `create_task`-Aufgaben in Cogs. |
| Tests | 1108 Unittests (12 E2E übersprungen) + 15 E2E, alle grün. |

## 2. Nachgewiesene Probleme

| ID | Schwere | Problem | Nachweis (Test schlägt auf `db29bbe` fehl) |
|---|---|---|---|
| P9-01 | Hoch (Datensicherheit lokal) | `import main` (u. a. durch `tests/test_stability.py`, `test_command_errors.py`) lud die `.env` des Checkouts in den Testprozess (echte Tokens, ggf. `PULSE_DB_PATH` auf echte Daten), wechselte das Arbeitsverzeichnis und legte `logs/` an. | `ImportIsolationTests.test_import_main_has_no_process_side_effects` |
| P9-02 | Hoch (Datensicherheit lokal) | `import webserver` wechselte ins Repository (`os.chdir`), lud die `.env` und öffnete/beschrieb `pulse.db` im Arbeitsverzeichnis (Tabellen + Default-Settings aus `pulse_ultimate.seed_defaults`). Ursache von P8-06. | `test_import_webserver_touches_no_database_and_keeps_cwd` (isolierte Kopie: `git status --ignored` zeigte `pulse.db`) |
| P9-03 | Hoch (Datensicherheit lokal) | 13 Testklassen in 12 Modulen erreichten `./pulse.db` und `.session_secret` im Arbeitsverzeichnis (Sidebar-Zähler, Einstellungen, Token-Signatur) – lokal ist das die **echte** Datenbank. Gefunden per Audit-Hook über den vollen Testlauf. | Voller Lauf in sauberem Worktree hinterließ `pulse.db` und `.session_secret` (auch auf `main`, dort zusätzlich `logs/pulse.log`) |
| P9-04 | Mittel (Shutdown) | Automation-Loop und Stall-Watchdog-Thread liefen nach `bot.close()` weiter (kein Stopp vorhanden). | `BotCloseTests.test_close_stops_automation_and_webserver`, `AutomationLifecycleTests` |
| P9-05 | Mittel (doppelte Jobs) | `PulseSuite.cog_unload` stoppte den 5-Minuten-Loop `auto` nicht: nach Shutdown lief er weiter, nach einem Reload liefen zwei. | `CogUnloadTests.test_suite_unload_stops_its_loop` |
| P9-06 | Mittel (Shutdown, Discord-Aktionen) | Namenspräfix-Massen-Sync (`role_nickname_prefixes`) und Recovery-Komplett-Sync (`recovery`) hatten kein `cog_unload` – sie änderten nach dem Entladen weiter Nicknames bzw. fügten Nutzer hinzu. | `test_role_prefix_unload_stops_bulk_sync`, `test_recovery_unload_stops_background_sync` |
| P9-07 | Niedrig (Ressourcen) | Schlug `_start_background_tasks()` nach dem Öffnen des HTTP-Pools fehl, blieb der Pool offen (Start lag vor dem `try`). | `LifespanTests.test_failed_start_cleans_up_and_restart_works` |
| P9-08 | Niedrig (doppelte Jobs) | Ein zweiter Aufruf des Webserver-Starts hätte einen zweiten uvicorn auf demselben Port gestartet (kein Guard). | `BotCloseTests.test_webserver_is_started_once` |

Kein Problem gefunden (Test bestätigt das Verhalten): wiederholtes Starten/Stoppen des Lifespans ohne Fehler
(`test_repeated_start_stop_leaves_nothing_running` war schon vorher grün), `start_automation` war bereits idempotent,
`init_runtime()` ist idempotent, übrige Cogs mit Loops/Tasks (`abmeldung`, `activity_check`, `ai_moderation`,
`server_stats`, `teamliste`, `tickets`, `verification`, `voice_support`, `social_promo`, `advanced_logs`,
`pulse_pro`, `pulse_next`) beenden ihre Aufgaben in `cog_unload`.

## 3. Geänderte Dateien

| Datei | Änderung |
|---|---|
| `main.py` | `prepare_process()` (chdir, Konsole, Log-Datei, `.env`, Konfigurationsprüfung) und `run()` statt Modul-Code; `webserver` erst in `run()`/`setup_hook` importiert, Bot erst in `run()` erzeugt; `message_cache_size()` liest den Wert nach dem Laden der `.env`. `_start_web_server()` mit Guard. `close()` stoppt zuerst die Automation. Reihenfolge `.env` → `validate_environment()` → `webserver` unverändert. |
| `webserver.py` | Kein `os.chdir` und kein `load_dotenv` beim Import; `init_runtime()` ruft zusätzlich `pulse_next.setup_next()` (Tabellen von `pulse_ultimate`/`pulse_next`, vor den Migrationen wie bisher); Lifespan startet die Hintergrundaufgaben im `try`. |
| `pulse_db.py` | `connect()` legt die Tabellen beim ersten Zugriff auf einen DB-Pfad an (idempotent, `CREATE … IF NOT EXISTS`, gleiche Funktion `init_db`), damit Tools und Tests ohne `init_runtime()` funktionieren. |
| `pulse_features.py`, `pulse_pro.py`, `pulse_ultimate.py`, `pulse_next.py` | `register()` greift nicht mehr auf die Datenbank zu (alle Datenfunktionen rufen `setup()`/`setup_next()` bzw. `pulse_db.connect()` ohnehin vor der Nutzung). |
| `pulse_ultimate.py` | `stop_automation()` (idempotent, beendet Loop und Watchdog, Timeout 5 s); Task-Name `pulse-automation`. |
| `pulse_jobs.py` | `stop_stall_watchdog()`. |
| `cogs/pulse_suite.py` | `cog_unload` stoppt den Loop `auto`. |
| `cogs/role_nickname_prefixes.py` | `cog_unload` bricht den Massen-Sync ab; dessen `finally` speichert den Nickname-Zustand weiter. |
| `cogs/recovery.py` | `cog_unload` setzt `_stop_requested`; der Komplett-Sync endet zwischen zwei Nutzern (kein abgeschnittener Token-Austausch), nach 5 s Abbruch; Audit-Eintrag wie bisher im `finally`. |
| `tools/preview_server.py`, `tools/load_test.py` | Nur Kommentare (der `os.chdir(tmp)`-Guard bleibt, `tests/test_tool_isolation.py` verlangt ihn). |
| `tests/test_lifecycle.py` (neu) | 12 Tests, siehe Abschnitt 5. |
| `tests/runtime_isolation.py` (neu) | `setUpModule`/`tearDownModule`: `pulse_db.DB_PATH` und `webserver.SECRET_FILE` im Temp-Ordner; setzt die Attribute direkt (ein `patch.stopall()` in `tearDown` – z. B. `test_json_safety` – beendete eine `patch`-basierte Isolation). |
| 12 Testmodule | Binden `runtime_isolation` ein: `test_apply_session`, `test_factions`, `test_json_safety`, `test_permissions`, `test_pulse_discord_style`, `test_pulse_embed_design`, `test_pulse_ticket_design`, `test_security_admin`, `test_server_control`, `test_session_tokens`, `test_stability`, `test_ui_basics`. |
| `.github/workflows/python-syntax.yml` | Leckprüfung nach den Tests zusätzlich für `*.db`, `*.db-wal`, `*.db-shm`, `.session_secret`, `logs/`. |

Keine Änderung an URLs, Slash-Commands, Custom IDs, DB-Tabellen/-Feldern, JSON-Schlüsseln oder Laufzeitdaten.
Keine neuen Threads, Queues, Caches oder Hintergrundaufgaben; `pulse_db._SCHEMA_READY` hält einen Eintrag je
verwendetem DB-Pfad (Produktion: einer).

## 4. Commits

1. `82511ff` Import von webserver ohne Arbeitsverzeichniswechsel und ohne Datenbankzugriff
2. `e9bc968` Start und Shutdown wiederholbar: Prozess-Setup erst beim Start, Hintergrundaufgaben sauber beenden
3. `4d66d32` Regressionstests für Import, Start/Stop und Shutdown; Tests schreiben keine DB/Secrets mehr in den Checkout
4. Dokumentation (dieser Bericht, Verbesserungsplan A-04, Changelog)

Nicht gepusht, nicht gemergt, nicht deployt.

## 5. Tatsächlich ausgeführte Tests

Alle Läufe in **separaten Git-Worktrees** im Scratch-Ordner (nicht im lokalen Checkout mit echten Daten);
Python 3.14.6 (Windows), Ruff 0.6.9.

| Prüfung | Ergebnis |
|---|---|
| Gegenprobe `tests/test_lifecycle.py` auf `db29bbe` (main) | 12 Tests: **7 Fehlschläge, 4 Fehler** (alle Befunde reproduziert; 1 Test bestätigt bereits korrektes Verhalten) |
| `tests/test_lifecycle.py` auf dem Phase-9-Stand | **12 OK** |
| `ruff check .` (auf `4d66d32`) | OK |
| `python -m compileall -q .` | OK |
| `tests/feature_smoke.py`, `tests/smoke_test.py` | OK |
| `unittest discover -s tests -p 'test_*.py'` | **1120 Tests OK** (12 übersprungen = E2E). Basis: 1108 OK; +12 neue. |
| CI-Leckprüfung (neues Muster) nach dem vollen Lauf | **keine Dateien** (auf `main`: `pulse.db`, `.session_secret`, `logs/pulse.log`) |
| `PULSE_E2E=1 unittest tests.test_e2e_dashboard` (echter uvicorn-Start/-Stop über `tools/preview_server.py`, Chromium) | **15 OK**, danach keine Dateien im Checkout |
| `python main.py` ohne `DISCORD_TOKEN` | Fehlermeldung wie bisher, Exit 0, keine Discord-Verbindung; legt wie vorher `logs/pulse.log` an (gewollt beim echten Start) |
| Import-Probe (Subprozess, leerer Temp-Ordner, Spione auf `sqlite3.connect`, `os.makedirs`, `Path.mkdir`, `load_dotenv`, `os.getcwd`) | `import webserver` und `import main`: keine Datei, kein Ordner, keine DB-Verbindung, kein `.env`, Arbeitsverzeichnis unverändert |

Abgedeckte Szenarien aus dem Auftrag:

| Szenario | Test |
|---|---|
| Import in isolierter Umgebung / keine unerwarteten Dateisystemänderungen | `ImportIsolationTests` (Subprozess) + CI-Leckprüfung + Worktree-`git status --ignored` |
| Wiederholter Startup | `test_repeated_start_stop_leaves_nothing_running`, `test_start_twice_runs_one_task_and_stop_allows_clean_restart` |
| Shutdown während laufender Hintergrundaufgaben | `test_close_stops_automation_and_webserver`, `CogUnloadTests` (laufende Loops/Syncs), Lifespan mit laufendem Backup-Task |
| Startup-Fehler und sauberer Neustart | `test_failed_start_cleans_up_and_restart_works` |
| Keine doppelten Jobs | `test_start_twice…`, `test_webserver_is_started_once`, `test_suite_unload_stops_its_loop` |
| Keine offenen Ressourcen nach Shutdown | HTTP-Pool geschlossen, Tasks `done()`, Watchdog-Event gesetzt (Lifespan-, Automation-, Cog-Tests) |
| Regressionen aller betroffenen Funktionen | voller CI-Satz + E2E |

## 6. Verbleibende Risiken

- **Andere Startwege als `python main.py`:** `webserver` lädt die `.env` nicht mehr selbst. Wer das Dashboard direkt
  (z. B. `uvicorn webserver:app`) startet, muss die Umgebung selbst setzen. Im Repository gibt es keinen solchen
  Startweg (Dockerfile: `CMD ["python", "main.py"]`; Tools setzen ihre Werte selbst).
- **Arbeitsverzeichnis:** relative Datenpfade (`config.json`, `pulse.db`, `backups/` …) hängen jetzt allein am
  `os.chdir` in `main.prepare_process()` (Docker: `WORKDIR /app` = Repository – identisch zu vorher).
- **Erster DB-Zugriff:** Die Tabellen von `pulse_ultimate`/`pulse_next` entstehen jetzt in `init_runtime()` statt beim
  Import – in Produktion vor `bot.run()` wie bisher vor den Migrationen. `pulse_db.connect()` prüft je Verbindung
  zusätzlich `os.path.exists` (ein `stat`, vernachlässigbar).
- **Recovery-Sync beim Shutdown:** hängt ein Token-Austausch länger als 5 s, wird der Task abgebrochen (wie bisher
  beim Prozessende). Der Lauf ist danach wie bisher als unterbrochen markiert und auditiert.
- **uvicorn-Portfehler:** Kann der Port nicht gebunden werden, beendet uvicorn den Prozess mit `sys.exit(1)` aus dem
  Webserver-Task (unverändert; Docker/Crash-Restart starten neu). Nicht geändert, da kein Fehlverhalten nachgewiesen.
- **Python-Version:** lokal 3.14.6 (Windows); Linux-Container/CI 3.14 nicht lokal ausgeführt – CI läuft beim Push.
- **Vorhandene Testmeldung:** `no such table: ultimate_attachments` erscheint im Testlog (ein erwarteter Fehlerpfad in
  `test_mission_regressions`) – identisch auf `main`, nicht Teil dieser Phase.

## 7. Nicht umgesetzt (mit Grund)

| Punkt | Grund |
|---|---|
| Tabellenanlage der kleinen Alt-Datenbanken der Cogs (`rp_verwaltung.db`, `buero_system.db`, …) beim Cog-Laden | Passiert beim Start (Cog-Laden), nicht beim Import; kein Fehlverhalten nachgewiesen. |
| Reaktion auf einen Portfehler (Bot ohne Dashboard weiterlaufen lassen vs. beenden) | Verhaltensentscheidung, kein nachgewiesener Defekt; heute beendet sich der Prozess und wird neu gestartet. |
| Zentrale Sammelstelle für alle `create_task`-Aufgaben der Cogs | Jede Aufgabe gehört einem Cog und wird jetzt in dessen `cog_unload` beendet; eine zusätzliche Registry wäre neue Infrastruktur ohne Befund. |
| Verbleibende Testmodule, die `pulse.db` gezielt selbst umleiten | Bereits isoliert (eigene `patch.object(pulse_db, "DB_PATH", …)`), der Audit-Hook-Lauf fand keine weiteren Zugriffe. |
| Push, PR, Merge, Deployment | Laut Auftrag ausgeschlossen. |
