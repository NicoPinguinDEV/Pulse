# Pulse Complete Mission – Abschlussbericht 2026-10-24

Branch `fix/deploy-script-stdin`, Ausgangs-HEAD `c24bec8`, Version 7.17.0. **Nichts committet, gepusht oder deployt.**
Fortschritt/Checkliste: `docs/audits/PULSE_COMPLETE_MISSION.md`. Umgebung: Windows 11, Python 3.14.6, lokal,
Fake-Guild und Temp-Daten; keine Discord-, OAuth- oder VPS-Aktion.

## Phasenstatus
| Phase | Status |
|---|---|
| A Sicherheit | abgeschlossen – 6 Befunde behoben; GitHub-Repo-Einstellungen blockiert (nur Inhaber) |
| B Ausfälle | abgeschlossen – 2 Befunde behoben (M-B1, M-D2) |
| C Performance | abgeschlossen – M-C1 behoben, gemessen |
| D Datenintegrität | abgeschlossen – M-D1, M-D2 behoben; Migrationen geprüft |
| E Replay/Simulation | abgeschlossen – 10 Szenarien, kein Defekt |
| F Regressionstests | abgeschlossen – jeder Fix mit vorher rotem, nachher grünem Test |
| G Gesamtverifikation | abgeschlossen – voller CI-Satz + E2E grün, Diff geprüft |

## Befunde nach Schwere
### Hoch
**M-A1-3 – Beförderungsfreigabe degradiert statt befördert; Rollen frei wählbar** (`pulse_next.promote_now`)
- Ursache: `member.add_roles(...)` wurde nie awaited (`awaitable = awaitable`), die alte Rolle wurde aber entfernt und
  „Beförderung angewendet“ gemeldet. `target_role_id`/`old_role_id` sind Freitextfelder – jede Rolle unter der
  Bot-Rolle (z. B. Admin) war vergeb-/entziehbar, ohne Teamrollen- und Rangprüfung des Freigebenden.
- Fix: `await`, Discord 403/HTTP-Fehler → nichts geändert und gemeldet; nur Rollen aus `team_role_ids`; Nicht-Admins
  nur unterhalb des eigenen Rangs (wie `/action promote` und Rollenmanager); Altrolle erst nach Erfolg und nur Teamrolle.
- **Verhaltensänderung:** Nicht-Teamrollen-IDs werden jetzt abgelehnt (Meldung im Audit).
- Test: `PromotionApprovalTests` – vorher 4/4 rot („coroutine … never awaited“), nachher grün.

**M-C1 – `/ultimate/team` blockiert den gemeinsamen Event-Loop ~50 s** (`pulse_ultimate.member_stats` + 9 Schleifen)
- Ursache: N+1 – pro Mitglied 2 × 5000 Tickets, Schichten, Teamdaten, Config; zusätzlich bei jedem `setup()`
  `seed_defaults` (3 Verbindungen) und ein UPDATE je Mitglied pro Seitenaufruf.
- Fix: `stats_context()` einmal pro Seite (identische Zahlen, Einzelaufrufe unverändert); Defaults einmal pro
  initialisierter DB; UPDATE nur bei Änderung; Teamakten-Schleife in `asyncio.to_thread`.
- Test: `TeamStatsQueryTests` (gleiche Werte, 1 statt 2 × N Ticketabfragen) – gegen Altcode rot, nachher grün.

### Mittel
**M-A3-1 – SSRF über Social-Feeds** (`cogs/social_promo.py`): nur Start- und End-URL geprüft; Zwischen-Weiterleitung
auf `http://127.0.0.1:25095/…` wurde angefragt, DNS-Namen mit privater IP passierten. Einstellbar mit
`can_manage_announcements` (nicht nur Admin). Fix: httpx-Request-Hook prüft jeden Hop inkl. DNS-Auflösung
(`is_global`). Test `tests/test_social_ssrf.py` vorher 3/4 rot, nachher 4/4 grün.

**M-A4-1 – Deploy-Skript nicht vom geprüften Commit** (`.github/workflows/deploy-vps.yml`): manueller Lauf checkte
`deploy/remote_deploy.sh` (läuft auf dem VPS) bei `github.sha` (Dispatch-Ref) aus, verifiziert war aber die Eingabe-SHA;
auch die Discord-Meldung nannte die falsche SHA. Fix: alle Schritte nutzen `steps.verified.outputs.sha`.
Test `test_the_deploy_script_comes_from_the_verified_commit` vorher rot, nachher grün; `test_deploy_config`
prüft jetzt die strengere Form (statt nur das Vorkommen von `workflow_run.head_sha`).

**M-D1 – Legacy-Restore ohne Schutz** (`pulse_next.restore_backup`, `/ultimate/backup/{name}/restore`): keine Sperre,
keine Sicherheitskopie, keine JSON-Prüfung, kein Rückfall bei Abbruch. Fix: gleiche Garantien wie
`pulse_backup.restore_full_backup`. Test `tests/test_legacy_backup_restore.py` vorher 4/4 rot, nachher grün.

**M-F1 – Vier Formulare immer HTTP 500**: Fall anlegen, Beweis-Upload (Datei blieb verwaist), Zertifikat,
Beförderungsantrag – Platzhalterzahl ≠ Spaltenzahl. Fix: explizite Spaltenlisten, Upload räumt bei DB-Fehler auf.
Tests vorher rot (`OperationalError`), nachher grün; Wächtertest prüft künftig alle positionsabhängigen INSERTs.

**M-B1 – Automationszyklus** (`_automation_cycle`): eine Ausnahme (z. B. „database is locked“) stoppte alle weiteren
fälligen Automationen und ließ `next_run_at` stehen (Retry jede Minute). Fix: pro Automation abgefangen, Intervall
immer fortgeschrieben. Test vorher rot, nachher grün.

### Niedrig
- **M-D2** `webserver.save_json`: bei Schreibfehler blieb `*.tmp` liegen (Original blieb intakt). Fix + Test.
- **M-A1-1** `pulse_next.ctx_auth`: Aktivität vor Rechteprüfung geschrieben (abgelehnte POSTs veränderten
  `next_activity`). Fix: `require=` prüft vorher (15 Aufrufstellen). Test vorher rot, nachher grün.
- **M-A1-2** Übergabe archivieren: unbekannte/archivierte ID → Erfolg + Audit. Fix: 404. Test vorher rot, nachher grün.

## Tatsächliche Testergebnisse (Endstand)
| Prüfung | Ergebnis |
|---|---|
| `python -m ruff check .` | bestanden |
| `python -m compileall -q .` | bestanden |
| `python tests/feature_smoke.py` | bestanden |
| `python tests/smoke_test.py` | bestanden |
| `python -m unittest discover -s tests -p 'test_*.py'` | bestanden – 916 Tests (Baseline 875), 3 übersprungen |
| `PULSE_E2E=1 python -m unittest tests.test_e2e_dashboard` | bestanden – 12 Tests |
| `git diff --check` | sauber |
| Übersprungen (3) | `test_boot_script_syntax_and_safety_guards`, 2× `test_remote_script_is_valid_bash` – „bash nicht lauffähig“ (lokales WSL ohne Distribution); laufen in CI |
| YAML-Parser-Prüfung `deploy-vps.yml` | nicht ausgeführt (kein PyYAML lokal); nur 3 Ausdrücke geändert, Workflow-Tests grün |

## Performance-Messung (`tools/load_test.py`, 300 Mitglieder, 5000 Tickets, 2000 Aufgaben, 20 000 Ereignisse, 3 Runden, Windows)
| Route | vorher Median | nachher Median |
|---|---|---|
| `/ultimate/team` | 49 437 ms | 3 417 ms (aus dem Event-Loop ausgelagert) |
| `/team` | 436 ms | 486 ms (Messrauschen) |
| `/dashboard` | 92 ms | 118 ms |
| übrige gemessene Routen | 5–260 ms | unverändert |
Python-Heap (tracemalloc): Spitze 80,8 MB vorher wie nachher. Prozess-RSS unter Windows vom Werkzeug nicht ermittelt
(auf Linux über `/proc` – auf dem VPS über das vorhandene Health-Monitoring prüfen).

## Nicht bestätigte Verdachtsfälle
- Anti-Nuke liest das Audit-Log einmal pro Ereignis; falls Discord den Eintrag verzögert liefert, wird das Ereignis nicht
  gezählt. Nicht lokal reproduzierbar (Live-Verhalten); Empfehlung: `on_audit_log_entry_create` prüfen.
- Anti-Nuke führt Ereignis-Automationen vor der Regelauswertung aus (verzögert die Sanktion um deren Laufzeit) – kein
  Fehlverhalten nachgewiesen.
- `total_actions` wird pro Aktion doppelt gezählt (ctx_auth + save_audit), hat aber keinen Leser – nicht geändert.
- DNS-Rebinding zwischen Prüfung und Verbindung (M-A3-1) bleibt theoretisch möglich (Restrisiko, deutlich verkleinert).

## Blockiert (nur Repository-Inhaber, Einstellungsänderung nicht beauftragt)
- `main` ohne Branchschutz/Ruleset (API: „Branch not protected“, `rulesets: []`).
- Environment `production` ohne Schutzregeln (API listet keine Environments) – README/Workflow erwähnen optionale Reviewer.
- Dependabot-Security-Updates deaktiviert (Versionsupdates per `dependabot.yml` sind aktiv).
Bestätigt aktiv: CodeQL Default Setup, Secret Scanning + Push Protection, Workflow-Token `read`, `PULSE_AUTO_DEPLOY` nicht gesetzt.

## Verbleibende Risiken / manuelle Prüfungen
- Beförderungsfreigaben mit Nicht-Teamrollen werden jetzt abgelehnt – vorhandene offene Anträge mit solchen IDs prüfen.
- `/ultimate/team` braucht bei 300 Mitgliedern weiter ~3 s (4 kleine Abfragen je Mitglied, jetzt im Worker-Thread).
- Geänderte Dateien müssen vor einem Merge noch durch `/pulse-review` bzw. `/pulse-security-audit` (nur manuell aufrufbar).
- Kein Commit erstellt; die neuen Dateien sind unversioniert.
