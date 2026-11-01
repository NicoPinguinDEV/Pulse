# Phase 13.1 – Final Hardening & Production Readiness

Stand: 01.11.2026 · Branch `hardening/phase13.1-production-readiness` · PR [#94](https://github.com/NicoPinguinDEV/Pulse/pull/94) · Basis `main` @ `6bcb515` (Merge von PR #93) · Pulse 7.17.0

**Nicht ausgeführt (bewusst):** keine VPS-Migration, kein Deployment, kein Merge, keine Discord-Aktion, keine Historienänderung, kein Force-Push.

## 1. Geprüfter Ausgangszustand

| Prüfung | Befund |
|---|---|
| Branch/HEAD/Arbeitsbaum | Start auf `hardening/phase13-data-isolation`, nur die drei bekannten unversionierten `.claude/*`-Dateien des Nutzers (unberührt). Neuer Branch von `origin/main`. |
| PR #93 | **gemergt** am 01.11.2026 00:28 UTC, Merge-Commit `6bcb515`. |
| GitHub Actions auf `main` @ `6bcb515` | Quality and Security Checks ✅, Sicherheit & Abdeckung ✅, CodeQL ✅. „Deploy Pulse VPS“ per `workflow_run` **übersprungen** → der VPS läuft weiter auf `d221b6c` (Phase 12); Phase 13 ist **nicht** deployt. |
| Offene PRs/Issues | keine (vor PR #94). |
| Bekannte offene Punkte | Gitleaks rot bei vollständigem Historien-Scan (3 Altbefunde, Phase-13-Bericht); Docker-Compose nur statisch geprüft; Migrations-Containerprüfung fail-open (siehe 2). |

## 2. Gefundene Probleme

| # | Problem | Schwere | Ort |
|---|---|---|---|
| P1 | `container_running()` war **fail-open**: Docker fehlt, Daemon down oder `inspect` scheitert → „läuft nicht“ → Migration startet trotz möglicherweise laufendem Pulse. Dazu Bypass-Flag `--allow-running`. | kritisch | `scripts/pulse_migrate_data.py` |
| P2 | `copy_file()`: `dst.exists()` und danach `os.replace()` – Race-Window, in dem eine parallel entstandene Datei **überschrieben** würde; fester Temp-Name wurde bei Altresten gelöscht. | hoch | dito |
| P3 | Beschädigte/fremde Markierung im Ziel wurde als „keine“ gelesen und **ersetzt**. | hoch | dito |
| P4 | Kein Rückweg-Werkzeug; der dokumentierte `cp -a` überschrieb Checkout-Dateien ohne Sicherung der veralteten Kopien. | mittel | Doku |
| P5 | SQLite-Integritätsprüfung öffnete die Zieldatenbank direkt (kann `-shm` anlegen, scheitert bei WAL ohne `-shm`). | mittel | dito |
| P6 | `rollback_code.sh` und Boot-Update nahmen **keine Deploy-Sperre**; alle Skripte liefen ohne `flock` einfach ungesperrt weiter. | hoch | `deploy/`, `scripts/` |
| P7 | Scheiterte der **erste** Deploy ins Datenverzeichnis-Layout, startete der automatische Rollback das alte Layout mit den **veralteten** Checkout-Daten (nur ein Hinweistext). Gleiches im Boot-Update. | kritisch | `remote_deploy.sh`, Boot-Update |
| P8 | Boot-Update baute beim Start der installierten Version ohne `PULSE_GIT_SHA` (fehlende Build-Information, falls das Image neu gebaut werden muss). | niedrig | Boot-Update |
| P9 | `.env`-Rechte wurden nirgends erzwungen. | niedrig | `remote_deploy.sh` |
| P10 | Kein echter Docker-Compose-Test (nur `docker run`/statisch). | hoch (Nachweis) | CI |
| P11 (Review) | `write_marker()` schrieb root-seitig über einen **festen Temp-Namen im Container-beschreibbaren `/data`** – ein kompromittierter Container hätte dort einen Symlink anlegen und root zum Schreiben auf beliebige Dateien bringen können. | hoch | `pulse_migrate_data.py` |
| P12 (Review) | Rückweg verwendete den Plan von **vor** der Sperre; Boot-Update startete Pulse auch bei belegter Sperre. | mittel | dito, Boot-Update |
| P13 | Rückweg-Kopien (`.pulse-legacy-*`) wären untracked im Checkout gelandet (Boot-Update bricht bei unerwarteten Dateien ab) und **nicht** von `.dockerignore` erfasst (Daten im Image). | mittel | `.gitignore`, `.dockerignore` |

## 3. Durchgeführte Verbesserungen

**Migration (`scripts/pulse_migrate_data.py`)**
- *Fail-closed:* echte Läufe nur, wenn `docker info` den Daemon nachweist **und** `docker inspect` den Container im Zustand `created`/`exited`/`dead` meldet. Docker fehlt, Daemon nicht erreichbar, `inspect` schlägt fehl, Zustand `running`/`restarting`/`paused`/unbekannt → Abbruch ohne Änderung. Ein nachweislich nicht existierender Container (neuer Server) nur mit `--container-absent`. `--allow-running` entfernt. Probelauf und `--verify` brauchen kein Docker.
- *No-Overwrite:* Inhalt in eindeutige Temp-Datei (`O_EXCL`), `fsync`, Veröffentlichung per **`os.link`** (atomar, scheitert, wenn der Name existiert – auch bei einem parallelen Schreiber); kein `os.replace`/`rename` auf das Ziel; Verzeichnis-`fsync`. Abbruch hinterlässt höchstens `*.pulse-migrate.tmp`, das ignoriert und nie gelöscht wird.
- Laufsperre `.pulse-migrate.lock` (`O_EXCL`), Plan wird unter der Sperre neu berechnet.
- `safe_path()`: keine absoluten Pfade, kein `..`, keine Symlinks auf dem Weg; Inventar folgt keinen Symlinks, löst nur ein symlinktes Wurzelverzeichnis auf. Scheitert `git ls-files` in einem Checkout, wird abgebrochen statt geraten.
- Markierung: beschädigt/fremd → Abbruch, nie ersetzt; `rolled_back` → keine neue Migration darüber. Schreiben mit eindeutigem Temp-Namen und `O_EXCL|O_NOFOLLOW`; Rechte per `lchown`.
- Backups mit Mikrosekunden-Zeitstempel, Rücklese-Prüfung (`verify_backup`), Veröffentlichung per `os.link`; ein defektes Backup stoppt vor jeder Kopie.
- SQLite-Prüfung auf einer privaten Kopie von `db`+`-wal`+`-shm` – die geprüften Dateien werden nie geöffnet.
- **Neuer Rückweg** `--rollback` (Probelauf-Standard, `--execute` + Bestätigung `ZURUECK`, gleiche Containerprüfung): Backup des Datenverzeichnisses **und** der veralteten Checkout-Kopien, veraltete Kopien per `rename` nach `.pulse-legacy-<Zeit>/` (nichts gelöscht), aktuelle Daten ohne Überschreiben zurück, Prüfsummen, Markierung `rolled_back` (Pulse im neuen Layout und jeder Deploy verweigern dieses Verzeichnis).

**Deploy-Kette**
- Deploy-Sperre `/run/lock/pulse-deploy.lock` in `remote_deploy.sh`, `rollback_code.sh`, Boot-Update und Crash-Restart, jeweils **fail-closed**. Boot-Update: belegte Sperre → Container unberührt; fehlt `flock`, startet es nur den installierten Stand (alle anderen Wege sind dann gesperrt).
- Layoutwechsel alt → Datenverzeichnis nicht gesund: **kein** automatischer Rollback, Pulse wird gestoppt, Code bleibt auf dem neuen Stand (passt zu den aktuellen Daten), Exit 2 mit Anleitung. Im Boot-Update ebenso.
- Boot-Update exportiert `PULSE_GIT_SHA` für jeden Build; `remote_deploy.sh` setzt `.env` auf `go-rwx`.
- `pulse_paths.verify_data_dir()` meldet `rolled_back`/`rollback_in_progress` ausdrücklich.

**CI/Tests**
- Neuer Job **„Docker-Compose-Integration“** (`tests/compose_integration.sh`) mit der echten `compose.yaml` (+ `tests/compose.ci.yaml`, ersetzt nur das Kommando durch `tests/compose_probe.py` – keine Discord-Verbindung).
- `.gitleaksignore` mit drei exakt begründeten Fingerprints (Abschnitt 8).

## 4. Geänderte Dateien

`scripts/pulse_migrate_data.py`, `pulse_paths.py`, `deploy/remote_deploy.sh`, `deploy/rollback_code.sh`, `scripts/pulse-vps-boot-update.sh`, `scripts/pulse-crash-restart.sh`, `.gitignore`, `.dockerignore`, `.gitleaksignore` (neu), `.github/workflows/python-syntax.yml`, `tests/compose_integration.sh` (neu), `tests/compose_probe.py` (neu), `tests/compose.ci.yaml` (neu), `tests/test_phase13_1_hardening.py` (neu), `tests/test_phase13_data_isolation.py`, `tests/test_deploy_config.py`, `VPS_DEPLOYMENT.md`, `docs/VERBESSERUNGSPLAN.md`, `docs/PHASE13_1_BERICHT.md` (neu). Keine Anwendungsroute, keine Datenstruktur, keine ID, kein `custom_id` geändert.

## 5. Sicherheitsverbesserungen

- Migration/Rückweg können nicht mehr laufen, während Pulse schreibt (fail-closed Docker-Nachweis).
- Kein Überschreiben, auch nicht bei parallelen Schreibern (atomares `link`).
- **Root folgt keinen Symlinks im Container-beschreibbaren `/data`** (Markierung, Rechte, Pfade) – schließt einen Weg vom kompromittierten Container zu root-Schreibzugriffen.
- Keine parallelen Deploys/Rollbacks/Boot-Updates; Crash-Restart startet ohne nachweisbare Sperre nicht.
- Kein unbemerkter Start auf veralteten Daten (Layoutwechsel-Rollback, `rolled_back`-Markierung).
- `.env` 600; Rückweg-Kopien weder in Git noch im Image.

## 6. Migrationstests (lokal ausgeführt)

`tests/test_phase13_1_hardening.py` (45 Tests) und `tests/test_phase13_data_isolation.py` (46 Tests), u. a.:
Docker fehlt / Daemon (Exit 1, leere Ausgabe, Timeout, OSError) / `inspect`-Fehler / Zustände `running`, `restarting`, `paused`, `removing`, `unknown` → Abbruch; `exited`/`created`/`dead` → erlaubt; `absent` nur mit Flag · Probelauf und Verify ohne Docker · echter Lauf ohne Docker ändert nichts · bestehende Datei nie überschrieben · **parallel entstandene Datei gewinnt** · Abbruch beim `fsync` hinterlässt kein Teilziel · Traversal (`../x`, `/etc/passwd`, `uploads/../../x`) · Symlinks (Linux-CI; unter Windows teils übersprungen) · beschädigte/fremde Markierungen bleiben unverändert · zu wenig Speicher · defektes Backup stoppt vor dem Kopieren, Manipulation erkannt · Laufsperre · `rolled_back` blockiert · Temp-Reste ignoriert · **nicht gecheckpointeter WAL-Inhalt** wird migriert und geprüft · Integritätsprüfung berührt die Dateien nicht · defekte Datenbank → `in_progress` · Abbruch mitten im Kopieren + Wiederaufnahme · Rückweg: Probelauf, Bestätigung, laufender Container, veraltete Kopien beiseite, `.env` unberührt, Markierung `rolled_back`, ohne gültige Markierung verweigert · Marker-Symlink-Angriff · Fake-Läufe von `remote_deploy.sh` (Git Bash): unmigriert → Abbruch vor Backup/Reset, migriert → Deploy mit Build-Commit, **gescheiterter Layoutwechsel → Stop statt Start auf veralteten Daten**.

## 7. Docker-Compose-Ergebnisse (GitHub Actions, PR #94)

Job „Docker-Compose-Integration (read-only, /data, ohne Discord)“ auf Ubuntu mit Docker – **✅ bestanden** (Head `6128950`):
1. Fehlendes Datenverzeichnis → Compose verweigert (`bind source path does not exist`), Docker legt nichts an.
2. Markierung fehlt / `in_progress` / beschädigt / `rolled_back` → echter `main.py`-Start endet mit **Exit 78**, Verzeichnis unverändert.
3. Migriertes Verzeichnis → **healthy** (Compose-Healthcheck), `/healthz` 200; UID 1000; Schreiben auf `/app` und `/` scheitert; `/data` und `/tmp` beschreibbar; `PULSE_DATA_DIR`, `PULSE_DB_PATH`, `TMPDIR` korrekt; `PULSE_GIT_SHA` = Commit; `.env`/`.git` nicht sichtbar; `/tmp` = tmpfs 64 MB `noexec`; `.pulse-agent`-Mount in beide Richtungen; Datenbank, Secret, Config, Log, Transkript, Wartemusik, Komplett-Backup im Host-Ordner mit Besitzer UID 1000; Smoke-Test (Backup/Restore) und Import aller 30 Cogs im Container bestanden.
4. `docker compose stop` → Exit 0 innerhalb der Grace-Zeit, Lifespan-Shutdown protokolliert; Neustart auf vorhandenen Daten wieder healthy.

Der erste Lauf scheiterte an einem Testfehler (Runner-UID durfte nicht in den UID-1000-Ordner `.pulse-agent` schreiben) – behoben in `3f3ed5e`.

## 8. Vollständige CI-Ergebnisse

**Lokal (Windows 11, Python 3.14.6, Git Bash für Shell-Tests):** Ruff ✅ · compileall ✅ · `feature_smoke` ✅ · `smoke_test` ✅ · `unittest discover` **1237 Tests OK, 4 übersprungen** (finaler Stand inkl. Review-Fixes; übersprungen: Symlink-Tests ohne Windows-Symlinkrecht und umgebungsabhängige Tests) · keine Testartefakte im Checkout.

**GitHub, PR #94 (Head `6128950`):** syntax ✅ · Docker-Image bauen ✅ · **Docker-Compose-Integration ✅** · Dashboard-E2E (Playwright) ✅ · Lock-Datei + pip-audit ✅ · Abhängigkeiten ✅ · Gitleaks (PR-Bereich) ✅ · CodeQL (actions, javascript-typescript, python) ✅.

**Gitleaks – vollständige Historie (manueller Lauf 41257956846):** ✅ mit `.gitleaksignore`. Bewertung der drei Altbefunde (alle ohne Geheimnis, keine Historienänderung):

| Fingerprint | Inhalt | Bewertung |
|---|---|---|
| `82f3180…:pulse_env_validation.py:discord-client-secret:47` | Variablenname `DISCORD_APPLICATION_REDIRECT_URI` in einem Tupel von URL-Variablennamen | 32 Zeichen nach `DISCORD_` passen ins Regel-Muster; kein Wert |
| `1094de3…:cogs/support_assistant.py:generic-api-key:252` | `provider_request_slot(self.api_base, self.api_key, min_interval=2.0)` | Attributzugriff, kein Schlüssel |
| `f392d6a…:cogs/support_assistant.py:generic-api-key:252` | dieselbe Zeile in einem Folgecommit | wie oben |

Keine Regel, kein Pfad pauschal ausgenommen; neue Funde an derselben Stelle (anderer Commit) würden weiter gemeldet. Ein abschließender manueller Gesamtlauf auf dem finalen Stand ist angestoßen (Run 41280246602); sein Ergebnis steht in der PR-Beschreibung.

## 9. Verbleibende Risiken

- **Erster Umstieg auf dem VPS** bleibt der kritische Moment; jetzt aber abgesichert: ohne Migration kein Deploy; scheitert der Start, bleibt Pulse gestoppt (Ausfall statt veralteter Daten).
- Der auf dem VPS **installierte** Boot-Update-Helfer und Crash-Restart sind bis zur Neuinstallation die Versionen vor Phase 13.1 (ohne Sperre im Boot-Update, ohne Layoutwechsel-Schutz). Neuinstallation ist Teil der VPS-Schritte (Abschnitt 10).
- `os.link` setzt Hardlink-Unterstützung des Dateisystems voraus (ext4 auf dem VPS: ja). Ohne sie bricht die Migration mit `in_progress` ab – sicher, aber manuell zu klären.
- Der Compose-Test ersetzt das Kommando: `bot.run()` und die echte Discord-Anmeldung im Container sind nur auf dem VPS prüfbar.
- `.pulse-agent` bleibt ein vom Container beschreibbarer Ordner im Checkout (nur Socket; root führt dort nichts aus).
- Graphify-Hinweise zur Kopplung (`migrate`, `rollback`) bewusst nicht weiter zerlegt: lineare, einzeln getestete Schrittfolge (Begründung im PR).

## 10. Konkrete Vorbereitung für die VPS-Migration (erst nach Freigabe)

> **Phase 13.2:** Die maßgebliche, geprüfte Reihenfolge der VPS-Migration (Root-Helfer zuerst, Deploy nur über GitHub Actions, Crash-Neustart zuletzt) steht ausschließlich in `VPS_DEPLOYMENT.md` → „Phase 13.1: einmalige Datenmigration auf dem VPS (Schritt für Schritt)“. Die Schritte in diesem Abschnitt sind historisch.

Voraussetzungen: PR #94 gemergt; Wartungsfenster außerhalb der RP-Zeiten; Vionity-Snapshot.

1. `cd /opt/pulse/Pulse && git fetch origin main` – **noch nicht deployen**.
2. Neue Root-Helfer installieren: `install -m 755 <(git show origin/main:scripts/pulse-crash-restart.sh) /usr/local/sbin/pulse-crash-restart.sh` und analog `pulse-vps-boot-update.sh`; `command -v flock` muss etwas ausgeben.
3. Komplett-Backup im Dashboard erstellen und herunterladen.
4. `touch /var/lib/pulse-crash-restart/paused && docker compose stop pulse` (`docker inspect -f '{{.State.Status}}' pulse-teamos` → `exited`).
5. `git show origin/main:scripts/pulse_migrate_data.py > /root/pulse_migrate_data.py`
6. Probelauf: `python3 /root/pulse_migrate_data.py` – Konflikte 0, Speicher ausreichend.
7. Migration: `python3 /root/pulse_migrate_data.py --execute` → `MIGRIEREN`; danach `--verify`.
8. Deploy (Actions „Deploy Pulse VPS“ oder Server Control Center) → Healthcheck abwarten.
9. Prüfen: Systemstatus (Commit, Datenverzeichnis), Tickets, Bewerbungen, Schichten, Transkripte, Wartemusik, Backups; `docker compose exec pulse touch /app/x` muss scheitern.
10. `rm /var/lib/pulse-crash-restart/paused`.
11. Rückweg bei Bedarf: `python3 /root/pulse_migrate_data.py --rollback` (Probelauf), dann `--rollback --execute` (`ZURUECK`), alten Commit starten (`VPS_DEPLOYMENT.md`).

## 11. Technische Roadmap für Phase 14

Bestandsaufnahme (Messwerte dieses Stands): `webserver.py` 11.981 Zeilen mit 78 eigenen Routen (+ `pulse_ultimate` 57, `pulse_next` 36, `pulse_features` 26, `pulse_admin` 22); Routen-Snapshot 372 Einträge; `pulse_pro.remove_and_add` an 32 Stellen plus Massen-Entfernung (`pulse_pro.py:464`); 3 Laufzeit-Monkeypatches (`ws.get_head_html`, `ws.get_sidebar_html`, `ws.log_audit` in `make_shell_helpers`); `POST /action` (`webserver.py:10974`) als Sammelendpunkt; JSON-Lesestellen: `team_data.json` 39×, `applications.json` 30×, `logs.json` 9×, `shifts.json` 11×; 8 SQLite-Dateien, `pulse_migrations` vorhanden, aber ohne JSON-Umzüge.

Grundsatz: jeder Schritt hinter dem Routen-Snapshot (K-16), der Rechte-Matrix (A-03) und dem Compose-Test; keine URL, keine Datenstruktur, keine ID, kein `custom_id` ändert sich.

| Prio | Paket | Inhalt | Absicherung | Aufwand |
|---|---|---|---|---|
| 1 | **A-01a Laufzeit-Patches beenden** | `make_shell_helpers` ersetzt `get_head_html`/`get_sidebar_html`/`log_audit` → Ausgabe-Hooks als explizite Registrierung in `webserver` (eine Implementierung, kein Überschreiben) | Snapshot-Tests der HTML-Köpfe, Audit-Tests | S |
| 2 | **A-01b `remove_and_add` abbauen** | Für jede der 32 Ersetzungen: aktive Implementierung laut Snapshot behalten, tote laut `docs/SEITEN_ALT_NEU.md` (Entscheidung E-6) entfernen; feste Registrierungsreihenfolge; `_IncludedRouter`-Problem (FastAPI 0.162) durch flache Registrierung je Modul lösen | Routen-Snapshot unverändert, Rechte-Matrix, E2E | M |
| 3 | **A-02a Auth/Session** | `pulse_auth.py`: Login/OAuth, `auth()`, Sitzungen, CSRF – reine Verschiebung mit Re-Exports aus `webserver` | `test_permissions`, `test_privacy`, Login-E2E | M |
| 4 | **A-02b Persistenz-Helfer** | `pulse_store.py` als einziger Ort für `load_json`/`save_json`, Locks, Wartungsmodus, Korruptionsschutz; `webserver` re-exportiert | `test_json_safety`, Nebenläufigkeit (K-04) | S |
| 5 | **A-02c `POST /action` aufteilen** | je Aktion ein Endpunkt; `/action` nimmt alte Formulare weiter an und delegiert | Rechte-Sweep (alle schreibenden Routen), Formular-E2E | M |
| 6 | **A-02d UI-Bausteine und Routen je Bereich** | `render_page`, Sidebar, Komponenten → `pulse_ui.py`; Routen nach Fachbereich in Module mit `register(app)` | Snapshot, visuelle Tests | L |
| 7 | **A-05a Migrationsrahmen für JSON** | `pulse_migrations` um „JSON → Tabelle“ erweitern: Backup, Lese-Fallback auf JSON, Doppelschreiben in einer Übergangsversion, Rückweg | Migrations-Roundtrip-Tests, Backup/Restore | M |
| 8 | **A-05b `logs.json` und `shifts.json`-Historie** | wachsende Dateien zuerst (D-07, P-03/P-05): Tabellen in `pulse.db`, Gesamtwerte unverändert | Zahlenvergleich alt/neu je Teammitglied | M |
| 9 | **A-05c kleine Datenbanken** | `rp_verwaltung`, `buero_system`, `flaggenquiz`, `teamliste`, `pulse_suite` → `pulse.db` (je Datei, mit Rückweg) | Komplett-Backup-Coverage-Test, Restore-Test | L |
| 10 | **D-09 Zeitstempel** | Schreibstellen auf ein Format, sobald A-05a steht | `test_pulse_time` | S |

Empfohlener Zuschnitt Phase 14: Pakete 1–4 (geringes Risiko, große Entkopplung), Paket 7 als Grundlage; 5–6 und 8–9 in Phase 15 ff. Die VPS-Migration aus Abschnitt 10 sollte **vor** Phase 14 stattfinden, damit A-05-Migrationen bereits auf `/opt/pulse/data` laufen.
