# Pulse Complete Mission – Fortschrittscheckliste

Start: 2026-10-24 · Branch `fix/deploy-script-stdin` · Ausgangs-HEAD `c24bec8` · Version 7.17.0 (`pulse_version.py`)
Kein Commit/Push/Deploy (Auftrag Abschnitt 13). Nutzerdateien `.claude/launch.json`, `.claude/pr_body_phase8.md`,
`.claude/settings.json` waren vorher unversioniert und bleiben unangetastet.
Abschlussbericht: `docs/audits/PULSE_COMPLETE_MISSION_BERICHT_2026-10-24.md`.

Statuswerte: `OFFEN` · `IN ARBEIT` · `BEFUND BESTÄTIGT` · `BEHOBEN – TEST BESTANDEN` ·
`GEPRÜFT – KEIN FEHLER NACHWEISBAR` · `BLOCKIERT – <Grund>`

## Ausgangslage (ausgeführt)
- `git status --short`: nur die 3 unversionierten `.claude`-Dateien. `git diff --check`: sauber.
- Baseline-CI lokal (Python 3.14.6, Windows): ruff OK · compileall OK · feature_smoke OK · smoke_test OK ·
  unittest 875 Tests OK (3 übersprungen).
- Skills `pulse-audit`, `pulse-security-audit`, `pulse-performance`, `pulse-review`, `pulse-verify`, `pulse-e2e` sind
  `disable-model-invocation` (nur vom Nutzer aufrufbar) – ihre Prüflisten wurden manuell als Leitfaden befolgt.

## Werkzeuge
- Routen-Matrix (Scratchpad-Skript, nicht versioniert): jede Schreibroute (172) mit Dummy-Formular, echter `auth()`
  (nur `get_current_user` gefälscht), Hash aller Temp-Dateien vor/nach jedem Request.
- `tools/load_test.py` (neu, versioniert): synthetische Last + `--profile`.
- Platzhalter-Wächter (`tests/test_mission_regressions.py::PositionalInsertGuardTests`).

## Phase A – Sicherheit
| Punkt | Status | Nachweis |
|---|---|---|
| A1 Serverseitige Rechte | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Matrix Teamler ohne Rechte: 140× 403, Rest nur eigene Bereiche |
| A1 Session-Ablauf/-Widerruf | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `test_sessions_api_keys`, `test_session_tokens` |
| A1 CSRF/Same-Origin | GEPRÜFT – KEIN FEHLER NACHWEISBAR | fremder Origin mit Admin-Sitzung: 172/172 → 403 |
| A1 IDOR | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Übergaben = gewolltes Teamboard; Fälle/Anhänge nur Führung + Owner-Bindung im SQL; Transkripte `can_manage_tickets` + Dateiname = Ticket-ID |
| A1 Rechteeskalation via Formular/IDs | BEHOBEN – TEST BESTANDEN | M-A1-3 |
| A1 Unsichere Standardrechte | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `test_permissions` + Matrix |
| A1 Rollen-/Bot-Hierarchie | BEHOBEN – TEST BESTANDEN | M-A1-3; übrige Pfade (`/action promote`, Rollenmanager) prüfen Rang + Bot-Rolle |
| A1 Doppelte/überschriebene Routen | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `test_route_snapshot`; nur bekanntes `GET /ultimate` |
| A1 Datenänderung vor Autorisierung | BEHOBEN – TEST BESTANDEN | M-A1-1 |
| A1 Anzeige vs. Serverrecht | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Matrix prüft Server, nicht UI; keine Route vertraut auf UI-Ausblendung |
| A2 Schwellen/Fenster/Doppelzählung/Race | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `tests/test_antinuke_replay.py` (12 gleichzeitige Events vollständig gezählt, 1 Sanktion) + bestehende Tests |
| A2 Ausnahmen/Testmodus/Alarmmodus | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Replay + `test_antinuke_control_center` |
| A2 Unsichere Retries/fehlgeschlagene Aktionen | GEPRÜFT – KEIN FEHLER NACHWEISBAR | 30 Events + 429 → 1 Ban-Versuch, als `failed` protokolliert |
| A2 Unbemerkte Aktivierung / KI-Sanktion | GEPRÜFT – KEIN FEHLER NACHWEISBAR | AutoMod/Anti-Nuke `enabled` default False, neuer Modus default `alert`; KI-Maßnahmen nur mit `reviewer` |
| A3 XSS | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Heuristik über alle f-String-HTML mit Nutzerfeldern: 11 Treffer, alle escaped bzw. kein HTML |
| A3 SQL-Injection | GEPRÜFT – KEIN FEHLER NACHWEISBAR | dynamische SQL nur mit festen Tabellen-/Spaltenlisten |
| A3 Path Traversal / ZIP | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `FileResponse`: basename+Regex+`resolve`; Restore prüft `..`/absolut/`:` |
| A3 Uploads | BEHOBEN – TEST BESTANDEN | M-F1 (Beweis-Upload war defekt, verwaiste Datei); Größenlimit 10 MB vorhanden |
| A3 SSRF | BEHOBEN – TEST BESTANDEN | M-A3-1 |
| A3 Limits/Timeouts/Rate-Limits | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Upload- und Feed-Größe begrenzt, httpx-Timeouts überall, begrenzter Rate-Limiter |
| A3 Secrets in Logs/Fehlern | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Fehler zeigen Typ/Fehler-ID, keine Tokens (Stichprobe der neuen Pfade) |
| A3 Exporte/Transkripte | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Matrix GET: Backups/Transkripte nur Admin bzw. `can_manage_tickets` |
| A3 Host-Befehle | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Live-Konsole allowlisted, Host-Agent nur UDS; keine Shell |
| A4 Gitleaks / pip-audit / Lock-Hashes / Ruff | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Workflows gelesen; `--require-hashes` aktiv |
| A4 Actions-Rechte | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `permissions: contents: read`; Repo-Default `read` (API) |
| A4 Exakt getesteter Commit beim Deploy | BEHOBEN – TEST BESTANDEN | M-A4-1 |
| A4 CodeQL | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Default Setup `configured` (python, js/ts, actions), wöchentlich (API) |
| A4 Branchschutz / Deployment-Freigabe | BLOCKIERT – Repo-Einstellung, Änderung nur durch Inhaber | `main` ungeschützt, keine Rulesets, Environment ohne Reviewer; Dependabot-Security-Updates aus |

## Phase B – Ausfälle/Fehlersimulation
| Punkt | Status | Nachweis |
|---|---|---|
| Discord 403/404/429, Timeout (Automationen) | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `tests/test_failure_simulation.py::DiscordFailureTests` |
| Discord 429/403 Anti-Nuke | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Replay-Tests |
| Gesperrte SQLite-DB | BEHOBEN – TEST BESTANDEN | M-B1; `LockedDatabaseTests` (Daten konsistent, danach wieder nutzbar) |
| Beschädigte/unvollständige JSON | BEHOBEN – TEST BESTANDEN | M-D2; `BrokenJsonTests` |
| Fehlende Channels/Mitglieder/Rollen | GEPRÜFT – KEIN FEHLER NACHWEISBAR | fehlender Alarmkanal → `failed`; Lockdown mit fehlendem Kanal (bestehend) |
| Doppelte/gleichzeitige Aktionen | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `_RUNNING`-Sperre, Restore-Sperre (M-D1), Replay gather |
| Abgebrochene Hintergrundjobs / Shutdown | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Loop fängt `CancelledError`, `pulse_jobs`; bestehende `test_stability`, `test_sqlite_closing` |

## Phase C – Performance
| Punkt | Status | Nachweis |
|---|---|---|
| N+1 Teamstatistiken | BEHOBEN – TEST BESTANDEN | M-C1: `/ultimate/team` 49 437 ms → 3 417 ms (Median, 300 Mitglieder/5000 Tickets) |
| Blockierender Event-Loop | BEHOBEN – TEST BESTANDEN | M-C1: Statistikschleife in `asyncio.to_thread` |
| Unbegrenzte Caches/Queues | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Anti-Nuke (2000/1000), Automation-Dedupe (2000), AutoMod (maxlen), Rate-Limiter (5000) |
| RAM | GEPRÜFT – KEIN FEHLER NACHWEISBAR | Lasttest: Python-Heap Spitze 80,8 MB (tracemalloc); RSS unter Windows nicht ermittelt |
| Übrige Seiten | GEPRÜFT – KEIN FEHLER NACHWEISBAR | alle anderen Testrouten < 500 ms |

## Phase D – Datenintegrität
| Punkt | Status | Nachweis |
|---|---|---|
| Legacy-Restore | BEHOBEN – TEST BESTANDEN | M-D1 |
| Nicht atomare JSON-Schreibvorgänge | BEHOBEN – TEST BESTANDEN | M-D2 (.tmp-Rest); Atomik selbst war vorhanden |
| Migrationen alt/doppelt | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `SchemaUpgradeTests` + `test_migrations` |
| Nebenläufige Schreibvorgänge | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `test_concurrent_writes` |

## Phase E – Replay/Simulation
GEPRÜFT – KEIN FEHLER NACHWEISBAR: `tests/test_antinuke_replay.py` (10 Szenarien). Kein neues Produktivmodul nötig –
vorhandene Schutzlogik (Dedupe, Cooldown, Rekursionssperre, Testmodus) hielt allen Szenarien stand.
Schutzstopp bei gehäuften Fehlern: Event-Automationen sind durch Cooldown/Dedupe, geplante durch ≥ 5-min-Intervall
begrenzt; nach M-B1 kein Minuten-Retry mehr. Ein zusätzlicher Auto-Abschalter wurde bewusst nicht gebaut (würde eine
Schutz-Automation still deaktivieren).

## Phase F/G
| Punkt | Status | Nachweis |
|---|---|---|
| Regressionstest je Befund vor/nach Fix | BEHOBEN – TEST BESTANDEN | alle 10 Befunde: vorher rot, nachher grün |
| Voller CI-Satz | BEHOBEN – TEST BESTANDEN | ruff, compileall, beide Smokes, 916 Unittests OK (3 übersprungen: bash fehlt lokal) |
| E2E Playwright | BEHOBEN – TEST BESTANDEN | 12/12 |
| Diff/Status/Secrets | GEPRÜFT – KEIN FEHLER NACHWEISBAR | `git diff --check` sauber, keine Secrets/Laufzeitdaten, LF in `deploy-vps.yml` erhalten |
| Geänderte Bestandstests | GEPRÜFT | `feature_smoke` (Suchmuster), `test_deploy_config` (strengere Assertion) – keine Abschwächung |

## Befunde
| ID | Schwere | Fundstelle | Ursache |
|---|---|---|---|
| M-A1-3 | Hoch | `pulse_next.promote_now` | `add_roles` nie awaited (Beförderung = Degradierung); Ziel-/Altrolle ungeprüft aus Formular |
| M-C1 | Hoch | `pulse_ultimate.member_stats` + Schleifen | N+1, 50 s im gemeinsamen Event-Loop |
| M-A3-1 | Mittel | `cogs/social_promo.py` | SSRF über Weiterleitung/DNS |
| M-A4-1 | Mittel | `deploy-vps.yml` | Deploy-Skript vom Dispatch-Ref statt vom geprüften Commit |
| M-D1 | Mittel | `pulse_next.restore_backup` | Restore ohne Sperre/Sicherheitskopie/Prüfung/Rückfall |
| M-F1 | Mittel | `pulse_ultimate`/`pulse_next` | 4 Formulare immer HTTP 500 (Platzhalterzahl) |
| M-B1 | Mittel | `pulse_ultimate._automation_cycle` | eine Fehl-Automation stoppt alle, Minuten-Retry |
| M-D2 | Niedrig | `webserver.save_json` | `.tmp`-Rest bei Schreibfehler |
| M-A1-1 | Niedrig | `pulse_next.ctx_auth` | Aktivität vor Rechteprüfung geschrieben |
| M-A1-2 | Niedrig | Übergabe archivieren | unbekannte ID → Erfolg + Audit |

## Geänderte Dateien
`pulse_next.py`, `pulse_ultimate.py`, `cogs/social_promo.py`, `webserver.py`, `.github/workflows/deploy-vps.yml`,
`tests/feature_smoke.py` (Suchmuster an neue Signatur), `tests/test_deploy_config.py` (strengere Assertion),
`tests/test_deploy_workflow.py`; neu: `tests/test_legacy_backup_restore.py`, `tests/test_mission_regressions.py`,
`tests/test_social_ssrf.py`, `tests/test_failure_simulation.py`, `tests/test_antinuke_replay.py`, `tools/load_test.py`,
`docs/audits/*`.

## Nächster Schritt / Wiederaufnahme
Alle durchführbaren Punkte erledigt. Offen nur Nutzerentscheidungen: Commit/Push, Repo-Einstellungen (Branchschutz,
Environment-Reviewer, Dependabot-Security-Updates), manuelles `/pulse-review` vor dem Merge.
