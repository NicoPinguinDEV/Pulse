# Phase 9 – Unabhängiges Abschluss-Review

Stand: 31.10.2026 · Branch `phase9-startup-lifecycle` · geprüfter Diff `db29bbe..6b86621` (4 Commits, 31 Dateien)
· Review-Fixes `e6c8af7`, `7d4d578` · Python 3.14.6 (Windows) · nicht gepusht, nicht gemergt, nicht deployt

**Ergebnis: REVIEW BESTANDEN – nach Behebung von zwei Befunden (1× P1, 1× P2).** Eine Prüfung ist nur in der CI
möglich: der Test mit einem echten SIGTERM läuft ausschließlich unter Linux (lokal übersprungen).

## 1. Vorgehen

- Diff gegen `db29bbe` Datei für Datei gelesen, direkte Aufrufer der geänderten Funktionen gesucht
  (`init_db`, `connect`, `register`, `setup`/`setup_next`, `start_automation`, `close`, `cog_unload`, Lifespan).
- Alle Startwege gesucht: `Dockerfile` (`CMD ["python", "main.py"]`), Deploy-/Rollback-/Boot-Skripte
  (`python -c "import pulse_backup …"`, `import pulse_version`), Tools (`route_map`, `preview_server`, `load_test`).
- Signalweg beim Stoppen: `compose.yaml` (`init: true`, `stop_grace_period: 40s`), `discord.Client.run`
  (fängt nur `KeyboardInterrupt`), `scripts/pulse-crash-restart.sh` (Exit-Code-Auswertung).
- Jedes Testmodul **einzeln** in einem sauberen Worktree ausgeführt und danach `git status --ignored` geprüft.
- Produktionsstart-Smoke: echter `main.run()` mit allen Cogs, Automation und uvicorn; Discord-HTTP blockiert,
  kein Gateway-Login.

## 2. Befunde

### P1 – SIGTERM beendete Pulse ohne Shutdown (behoben, `e6c8af7`)

`docker stop` und jeder Deploy (`docker compose up -d` erzeugt den Container neu) senden SIGTERM, tini
(`init: true`) leitet es an Python weiter. Weder Pulse noch `discord.Client.run` installieren einen Handler →
Python endet sofort. Folgen:
- `CustomBot.close()`, alle `cog_unload` und der Lifespan-Shutdown laufen in Produktion **nie**. Die
  Shutdown-Härtung aus Phase 9 war dort damit wirkungslos (Recovery-Sync z. B. mitten im Token-Austausch beendet).
- Exit-Code 143. `scripts/pulse-crash-restart.sh` behandelt nur Exit 0 als „sauberen Stopp (manuell)“ – ein
  bewusst gestoppter Container wurde als Absturz gewertet und wieder gestartet.

Vorbestehend (nicht durch Phase 9 eingeführt), aber direkt Prüfschwerpunkt 3.
**Fix:** `CustomBot._install_signal_handlers()` (in `setup_hook`): SIGTERM startet `close()` genau einmal,
Exit 0. Windows (keine Unterstützung) wird toleriert.
**Tests:** `SigtermTests` – Handler schließt einmal (auch bei zweitem Signal), fehlende Signal-Unterstützung
wirft nicht, echtes `os.kill(SIGTERM)` im Subprozess (nur Linux/CI).

### P2 – Testisolation nur im Gesamtlauf vollständig (behoben, `7d4d578`)

Einzeln ausgeführt legten 7 weitere Module `pulse.db` bzw. `.session_secret` im Arbeitsverzeichnis an:
`test_notify_prefs`, `test_pulse_verification`, `test_runtime_log_findings`, `test_security_hardening`,
`test_sessions_api_keys`, `test_start_page`, `test_ticket_categories`. Im Gesamtlauf war das verdeckt, weil
frühere Module Signaturschlüssel und Schema schon im Prozess geladen hatten. Lokal (`/pulse-verify targeted`
mit einzelnen Modulen) hätte das die echte `pulse.db` berührt.
**Fix:** die Module binden `tests/runtime_isolation.py` ein. **Nachweis:** Einzellauf aller 134 Module – danach
keine Datei im Checkout.

### P3 – kleinere Punkte (nicht geändert, kein Fehlverhalten nachgewiesen)

| Punkt | Bewertung |
|---|---|
| Portfehler: uvicorn ruft `sys.exit(1)` im Webserver-Task; `close()` fängt beim Warten auf diesen Task nur `Exception`, `SystemExit` bricht `close()` vor `super().close()` ab. | Vorbestehend; Prozess endet ohnehin mit Exit ≠ 0 → Crash-Restart. |
| Längste Shutdown-Dauer: Automation ≤ 5 s + Webserver ≤ 8 s + Recovery-Cog ≤ 5 s + Discord. | ≈ 20 s < `stop_grace_period: 40s`. |
| SIGTERM vor `setup_hook` (während des Logins) beendet weiterhin sofort. | Noch nichts gestartet, kein Datenrisiko. |
| Schlägt in einem halb fehlgeschlagenen Lifespan-Start auch das Aufräumen fehl, überdeckt dessen Fehler den ursprünglichen. | Nur Diagnose, kein Ressourcenleck. |
| `pulse_db.connect()` prüft je Verbindung `os.path.exists` (ein `stat`). | Vernachlässigbar. |

## 3. Prüfschwerpunkte

| # | Frage | Ergebnis |
|---|---|---|
| 1 | Keine Dateien/Logs/DBs beim Import | ✅ Subprozess-Probe (`import webserver`, `import main`): kein chdir, kein `.env`, keine SQLite-Verbindung, kein Ordner. Logs entstehen nur in `prepare_process()`. |
| 2 | Produktionsstart vollständig | ✅ Smoke: 30/30 Cogs, Automation läuft, uvicorn antwortet, `/healthz` meldet `database_ok: true`, `jobs_failing: []` (503 nur wegen fehlendem Discord-Login). Reihenfolge `.env` → Prüfung → `init_runtime` → Bot → `setup_hook` unverändert. Pre-Deploy-Backup-Befehl der Deploy-Skripte funktioniert. |
| 3 | Hintergrundaufgaben beim Shutdown beendet | ✅ nach P1-Fix. Smoke: nach `close()` keine offenen Tasks, keine Threads, 0 Cogs. |
| 4 | Doppelte Tasks bei Cog-Reload | ✅ Alle Cogs mit Loops/Tasks beenden sie in `cog_unload` (Suite neu). Es gibt keinen Reload-Befehl; einziges Laufzeit-`load_extension` (`pulse_pro`, Activity-Check) lädt nur, wenn nicht geladen. Automation und Webserver haben Guards. |
| 5 | Ressourcen bei Startfehlern | ✅ Lifespan räumt HTTP-Pools/Backup-Task auf; Neustart danach sauber (Test). |
| 6 | DB-Init/Lazy-Schema kompatibel | ✅ Gleiche `init_db` (nur `CREATE/ALTER … IF NOT EXISTS`, `INSERT OR IGNORE`); `init_runtime` legt alles in der bisherigen Reihenfolge vor den Migrationen an; keine Tabellenabhängigkeiten zwischen `pulse_db` und `pulse_ultimate`/`pulse_next`; niemand schaltet `DB_PATH` zur Laufzeit um. |
| 7 | Module erreichen ihre DBs | ✅ `pulse_ultimate`/`pulse_next`-Datenfunktionen rufen `setup()`/`setup_next()` selbst; Cogs laden nach `init_runtime`; voller Testlauf + E2E grün. |
| 8 | Testisolation | ✅ nach P2-Fix, auch beim Einzellauf. `runtime_isolation` übersteht `patch.stopall()`. |
| 9 | Importreihenfolge | ✅ `import main` lädt nur `pulse_command_errors`, `pulse_interactions`, `pulse_logging` (wie vorher vor `load_dotenv`); `webserver` erst nach `.env`. Kein Nicht-Test-Code importiert `main`. |
| 10 | Funktionen, URLs, Slash-Commands, custom_ids, Daten | ✅ Diff ändert keine Routen, Commands, IDs, Tabellen oder JSON-Schlüssel; Routen-Snapshot-, Command- und ID-Tests grün. |

## 4. Fehlende Regressionstests / Vorschläge

- **Einzelmodul-Leckprüfung automatisieren:** die Gesamtlauf-Prüfung der CI erkennt P2-artige Lecks nicht.
  Vorschlag: optionaler, manueller CI-Job, der jedes Modul einzeln ausführt (Laufzeit ≈ 15 min).
- **Echter Signal-Test** läuft nur in der Linux-CI – Ergebnis nach dem Push prüfen.
- **`main.run()`-Ablauf** ist nur per manuellem Smoke geprüft (Skript im Review-Scratch, nicht versioniert);
  ein versionierter Test bräuchte umfangreiche Discord-Fakes.
- Optional: Portfehler gezielt behandeln (Meldung + `close()` vor dem Exit) – Verhaltensentscheidung.

## 5. Kompatibilitätsrisiken

- **Exit-Code beim Stoppen 143 → 0.** Ein manuell gestoppter Container bleibt jetzt gestoppt (so war das
  Crash-Restart-Skript gemeint); vorher hätte der Timer ihn wieder gestartet.
- **Deploy/Stop dauert länger** (geordnetes Beenden, typischerweise wenige Sekunden, höchstens ≈ 20 s statt sofort).
- Direkter Start ohne `main.py` (`uvicorn webserver:app`) braucht die Umgebung von außen (kein Startweg im Repo;
  im Container setzt `env_file` die Werte).

## 6. Tests (tatsächlich ausgeführt, HEAD `7d4d578`, frischer Worktree)

| Prüfung | Ergebnis |
|---|---|
| `ruff check .` | OK |
| `compileall` | OK |
| `tests/feature_smoke.py`, `tests/smoke_test.py` | OK |
| `unittest discover` | **1123 OK**, 13 übersprungen (12 E2E, 1 Linux-only SIGTERM) |
| E2E (`PULSE_E2E=1`) | **15 OK** |
| CI-Leckprüfung + vollständiges `git status --ignored` | keine Dateien |
| Einzellauf aller Testmodule | vor Fix 7 Lecks, nach Fix 0 (betroffene + isolierte Module erneut einzeln geprüft) |
| Produktionsstart-Smoke / Pre-Deploy-Backup | siehe Abschnitt 3 |
