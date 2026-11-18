# Pulse TeamOS – Security Night Audit (2026-11-17)

Basis: `origin/main` @ `0c639cc` (Version 7.17.0, inkl. PR #274), Branch `security/night-hardening-v2`.
Vorgänger: `docs/SECURITY_HARDENING.md` (Stand 2026-10-30, dort stehen Architektur, Bedrohungsmodell und SEC-01..16).
Diese Datei enthält Audit-Umfang, neue Befunde, Behebungen, offene Risiken und Betreiberhinweise.

Absolute Sicherheit gibt es nicht. Die Aussagen unten unterscheiden ausdrücklich zwischen
**[T]** durch Test bestätigt, **[S]** statisch im Code geprüft, **[D]** dynamisch ausgeführt, **[U]** nicht verifizierbar.

## A. Audit-Umfang

- Bereiche: Auth/OAuth/Sessions/Cookies, CSRF/CORS/Header/CSP, Rate-Limits, Autorisierung/IDOR/Rangmodell
  (alle Routen per AST aufgezählt und gegen `auth()` abgeglichen, Discord-Views/Commands stichprobenartig), SSRF/ausgehendes HTTP,
  XSS (Stichproben, nicht erschöpfend), Command-Injection/Deserialisierung, SQL-Konstruktion (Bandit B608), Dateien/Archive/Backup/Restore,
  Recovery/Bann/Consent, Nebenläufigkeit, CI/CD, Docker/Compose, Deploy-Skripte/systemd, Secrets/Logging, Abhängigkeiten.
- Werkzeuge: Ruff, Bandit (Treffer einzeln bewertet), `pip-audit` gegen `requirements.lock` (keine bekannten Schwachstellen, DB-Stand 2026-11-17),
  manuelle Codeanalyse durch fünf voneinander unabhängige Review-Durchläufe (nur lesend) plus eigene Verifikation.
- **Nicht ausgeführt / nicht verfügbar:** CodeQL, Semgrep, Gitleaks lokal, Trivy, Docker-Build/Container-Laufzeittest (kein Docker-Daemon),
  Fuzzing/Property-Tests (Hypothesis nur installiert, keine Tests geschrieben), Zugriff auf GitHub-Einstellungen/VPS.
- Nicht zeilenweise gelesen: `pulse_privacy_requests_web`, `pulse_factions_dashboard`, `pulse_social_dashboard`, `cogs/teamliste.py`,
  `cogs/abmeldung.py`, `cogs/dienst.py`, `pulse_action_cards.ActionCardView`, Hire-Logik hinter Zeile 13000 in `handle_action`, ca. 40 f-String-SQL-Stellen
  (alle gelesenen: Platzhalter-/Konstantenbau, keine Nutzereingabe), ca. 40 `href="{…}"`-Stellen.

## B. Befunde und Behebungen (diese Runde)

Schweregrade nach eigener Einschätzung, keine CVSS-Werte. Keine CRITICAL/HIGH-Befunde gefunden.

**Unabhängig bestätigt, aber bereits durch PR #274 (`security/repository-hardening-2026-11`) auf `main` behoben** – während dieses Audits parallel gemerged,
deshalb hier nicht erneut umgesetzt (die Main-Version wurde übernommen): Recovery-Einwilligung nicht durch laufenden Sync wiederherstellbar (N-01), Bann-Prüfung im Recovery (R-04),
DNS-Pinning der Social-Feeds (R-02), Rate-Limiter-Verdrängung aktiver Sperren, Transkript-Anhang-Limits (R-08), Rangprüfung im Teamprofil-Update (R-09/AC-01),
`persist-credentials` (R-11), Image-Digests (R-07). Die Night-Audit-Reviews haben diese Lücken im Stand `3396158` jeweils selbständig gefunden.

**In diesem Branch zusätzlich behoben** (nicht in PR #274 enthalten):

| ID | Stufe | Fund | Ort | Status | Tests |
|---|---|---|---|---|---|
| WEB-1 | MEDIUM | Kein Größenlimit für Request-Bodies vor der Authentifizierung (Form/JSON wird gepuffert ⇒ Speicher-DoS im 768-MiB-Container) | `webserver.security_headers`, `deploy/Caddyfile` | **behoben** [T]: 4 MiB (multipart 60 MiB), chunked ohne Content-Length ⇒ 411, Caddy `max_size 60MB`. **Betreiber:** neue Caddyfile übernehmen | `RequestBodyLimitTests` |
| AC-02 | LOW–MED | Fremde Schicht beenden ohne Rangprüfung (Stundenkonto-Manipulation) | `pulse_pro.admin_end_shift` | **behoben** [T] | s. o. |
| AC-03 | LOW | Abmeldungen (LOA) höherrangiger Mitglieder anlegen/löschen ohne Rangprüfung | `webserver.handle_action` (`submit_loa`, `cancel_loa`) | **behoben** [T] | s. o. |
| AC-06 | LOW | Adressierte Übergaben waren für jeden Teamler sichtbar (nur Archivieren war eingeschränkt) | `pulse_ultimate.ultimate_handovers` | **behoben** [T] | s. o. |
| AC-07 | LOW | Mitgliederseite listete Ticket-Metadaten vertraulicher Kategorien ungefiltert | `pulse_pro.member_v5` | **behoben** [S] (`filter_tickets`, derselbe Filter wie die Listen); kein eigener Test | – |
| CI-2 | LOW | `remote_deploy.sh` prüfte die Commit-SHA nicht selbst | `deploy/remote_deploy.sh` | **behoben** [T] | s. o. |
| CI-3 | INFO | Jobs ohne `timeout-minutes`; Caddy-Image nicht von Dependabot erfasst | `python-syntax.yml`, `dependabot.yml` | **behoben** (Timeouts [S]; Dependabot-Ökosystem `docker-compose` [T]) | s. o. |
| MISC | INFO | Sitemap nutzte den (fälschbaren) Host-Header; Redirect-`msg` unkodiert; SHA1-Dedup-Schlüssel (Bandit B324, Falsch-Positiv) | `pulse_public_site`, `pulse_case_report`, `pulse_raid` | **behoben** [S] | Bestandstests grün |

Bandit-Treffer: B608 (SQL) – alle gelesenen Stellen bauen nur aus Konstanten/Platzhaltern, kein Nutzereingang ⇒ Falsch-Positiv (nicht jede der ~60 Stellen einzeln verfolgt [U]);
B314 (`ET.fromstring` in `social_promo`, 2-MB-Limit, expat ohne externe Entities) ⇒ gemildert; B310 (`urlopen` Host-Agent, validierte Loopback-URL) ⇒ Falsch-Positiv;
B104 (Bind 0.0.0.0 in `main.py`, Docker-Netz, Port nur an 127.0.0.1 veröffentlicht) ⇒ Konfiguration; B103 (`chmod 0o660` Host-Agent-Socket, Gruppe `docker`) ⇒ gewollt.

## C. Als stale erkannte Dokumentation

- `docs/SECURITY_HARDENING.md` R-01 („Sitzungsprüfung fail-open“) war veraltet: `dashboard_session` (webserver.py, „AUTH-3“) ist fail-closed, Login ohne speicherbare Sitzung ⇒ 503 [S].
- `pulse_sessions.revoke_all/revoke_unbound` schreiben `sessions_revoked_before:*`, das `is_valid` nicht liest. Folgenlos, weil Tokens ohne `sid` ohnehin abgelehnt werden – der Kommentar suggeriert aber eine Wirkung.

## D. Offene Risiken (nicht behoben)

| ID | Stufe | Risiko | Warum offen / Realismus | Bestehender Schutz | Nötig |
|---|---|---|---|---|---|
| R-03 | LOW | CSP ohne `script-src`/`style-src` | Inline-JS/CSS überall; Nonce-Umstellung ist ein Großumbau | `esc()` überall, Header `frame-ancestors/object-src/base-uri/form-action` | Inline-JS auslagern, dann Nonce-CSP |
| F-04 | LOW | `pip` ungepinnt/ohne Hash in Dockerfile/CI, Tooling (ruff/pip-audit) nicht hash-geprüft | betrifft Build, nicht die Laufzeit-Lock | Runtime-Lock mit `--require-hashes` | pip pinnen, Tooling in `requirements-dev.lock` |
| F-05 | LOW | Dependabot-`pip` regeneriert `requirements.lock` vermutlich nicht [U] | nicht prüfbar ohne reale Dependabot-PRs | wöchentliches `pip-audit` auf dem Lock | Lock-Regeneration als CI-Schritt |
| F-06 | LOW | Caddy-Container nur mit `no-new-privileges` (root, kein `cap_drop`/`read_only`) | Laufzeittest nötig, kein Docker | Port-Binding, Image-Pinning per Tag | `cap_drop: ALL` + `NET_BIND_SERVICE`, `read_only`, `pids_limit` testen |
| F-07/08 | INFO | systemd-Units für Watchdog/Crash-Restart/Update ohne Sandbox; Root-Skripte führen Inhalte aus einem UID-1000-Checkout aus | Designentscheidung (Host-Agent braucht `git reset`) | Container sieht den Checkout nicht (Phase 13) | Unit-Sandboxing, Eigentümerkonzept |
| F-3 | LOW | `apply_session`-Cookie ohne serverseitige Sitzung, nicht widerrufbar (max. 12 h, nur eigene Bewerbung) | geringer Schaden | HMAC, Ablauf | optional `jti`-Tabelle |
| F-7 | INFO | Ungültige API-Schlüssel werden nicht ratenbegrenzt (SHA-256 + 1 SQLite-Lesevorgang; 256-Bit-Schlüssel) | kein Rate-Limit ohne Request-Kontext in `check_api_key` | 256-Bit-Schlüssel | Limit pro Client-IP vor Lookup |
| L-1 | [U] | `is_internal_client` stuft jede private Peer-Adresse als intern ein; falls ein Host-Proxy über das Gateway ohne `X-Forwarded-For` anbindet, könnten `/healthz`-Details (Version, RAM) nach außen gehen | hängt vom VPS-Aufbau ab | Caddy setzt XFF und blockt `/healthz` | auf dem VPS prüfen |
| L-3 | [U] | Cookie-`Secure` folgt der konfigurierten Redirect-URI, nicht `PUBLIC_BASE_URL` | nur bei widersprüchlicher Konfiguration | – | Konfiguration prüfen |
| N-02 | LOW | Zip-Restore: kein Limit für Gesamtgröße/Eintragszahl; Staging in `/tmp` (im gehärteten Container 64-MB-tmpfs ⇒ große Restores scheitern sicher mit ENOSPC) | erfordert Schreibzugriff auf das Backup-Verzeichnis; Funktions-, kein Rechteproblem | Manifest-/SHA-256-Prüfung, Allowlist, Rollback, Sicherheitskopie | Staging auf das Datenvolume verlegen, Limits |
| N-03 | LOW | TOCTOU zwischen `verify_full_backup` und Staging | nur mit Schreibzugriff auf das Backup-Verzeichnis | Dateirechte 0600/0700 | Hash beim Extrahieren |
| AC-04 | LOW | `add_note`: beliebige `user_id`, Schreiben ohne `commit_member_entry` | zwischen Laden und Speichern liegt kein `await`; Race nur mit Thread-Routen | `can_add_notes` | Merge-on-save, Ziel auf Teammitglieder |
| AC-05 | LOW | `/team-ping` für jeden Teamler (bis 30 Pings, kein Cooldown) | Belästigung, keine Eskalation (`allowed_mentions` ohne Rollen/everyone) | – | Recht oder Cooldown |
| AC-x | [U] | Suite-Ticket-Buttons ohne `can_access_ticket`, Discord-Ticketbuttons nur `is_team`; `approval_decide` verbraucht Antrag vor `promote_now`; Rollenmanager ohne Rang-Lock | Kanalrechte/Folgen nicht aus dem Code prüfbar bzw. Funktionsfehler | Discord-Kanalrechte, Rangregeln | prüfen / separat beheben |
| R-10 | INFO | Laufzeitdaten in der Git-Historie (Commit `f1e83a4`) | Bereinigung nur per History-Rewrite (Force-Push) – nicht ohne ausdrückliche Freigabe | – | Entscheidung des Inhabers |
| – | [U] | XSS nur stichprobenartig geprüft | `webserver.py` ist sehr groß | `esc()`, Tests, CSP-Teilschutz | Renderer-weiter Payload-Test (`"><img onerror>` in Tickets/Bewerbungen/Namen) |

## E. Betreibermaßnahmen (außerhalb des Repositorys, nicht ausgeführt)

1. Neue `deploy/Caddyfile` (Body-Limit) auf dem VPS übernehmen und Caddy neu laden – erst nach Review/Merge.
2. GitHub: Branch-Ruleset für `main`, Environment `production` mit Reviewern, Dependabot Security Updates, Secret Scanning/Push Protection (siehe `docs/SECURITY_HARDENING.md` §9) – nicht verifizierbar.
3. VPS: `PULSE_PUBLISH_HOST` ≠ `0.0.0.0`, Deploy-SSH-Schlüssel mit `command=`/`restrict`, Container-Härtung nach dem nächsten Deploy prüfen.
4. Keine Secret-Rotation erforderlich: im Repository wurden keine echten Secrets gefunden (nur Test-Fixtures). Die Rotation von `SESSION_SECRET` bleibt ausdrücklich zu vermeiden (Recovery-Schlüssel hängt daran).

## F. Gesamtbewertung

Das Repository ist gegenüber dem Stand vom 2026-10-30 deutlich gehärtet; in diesem Durchlauf wurden keine kritischen oder hohen Schwachstellen gefunden.
Die behobenen Funde sind überwiegend Mittel/Niedrig und betreffen Zusammenspiel-Fehler (Recovery-Race, DNS-Pinning, fehlende Rangprüfung auf
Nebenpfaden). Verbleibende relevante Schwäche: CSP ohne `script-src` (R-03) – ein künftiger XSS wäre unmittelbar ausnutzbar.
Separat zu testen: Container-Laufzeit/Caddy-Härtung, VPS-Konfiguration, GitHub-Einstellungen, ein echter DNS-Rebinding-Test gegen ein TLS-Ziel in einer Testumgebung.

## G. Testergebnisse

Wird nach dem letzten Lauf auf diesem Branch im Abschlussbericht/PR ergänzt (Windows-Läufe zeigen Datei-Sperr-Fehler `WinError 32` in den SQLite-/Melonly-Modulen, die auch auf `main` bestehen; Linux-CI ist maßgeblich).
Neue Tests: `tests/test_security_night.py` (Body-Limit, Workflow/Deploy/Caddy-Konfiguration), `LeaderRankRuleNightTests` in `tests/test_security_hardening.py` (Profil-Update, fremdes Schichtende, LOA, Übergaben).
