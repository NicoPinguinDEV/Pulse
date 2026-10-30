# Pulse TeamOS – Security Hardening

Stand: 2026-10-30, Basis `11c44e0` (main), Branch `claude/dreamy-bardeen-bo019n`, Version laut `pulse_version.py`: 7.17.0.
Diese Datei beschreibt die Sicherheitsarchitektur, das Bedrohungsmodell, die in dieser Runde behobenen Schwachstellen
(mit Regressionstest) und die verbleibenden bzw. nur manuell umsetzbaren Punkte. Frühere Befunde stehen in
`docs/audits/` und den Phasenberichten; hier werden sie nur referenziert, wenn sie erneut geprüft wurden.

Absolute Sicherheit ist nicht erreichbar. Ziel ist Defense-in-Depth mit sicheren Standards und überprüfbaren Schutzmechanismen.

## 1. Sicherheitsarchitektur (Kurzfassung)

| Schicht | Mechanismus | Ort |
|---|---|---|
| Login | Discord-OAuth (`scope=identify`), `state`-Cookie (HttpOnly, SameSite=Lax, Secure bei HTTPS), Vergleich mit `hmac.compare_digest`, Rate-Limit auf `/login` und `/callback` | `webserver.py` (`login`, `callback`) |
| Sitzung | HMAC-SHA256-signiertes Cookie mit Typ (`dashboard`/`apply`), Ablauf, serverseitige Sitzung (`sid`) mit Idle-Timeout, Widerruf, „überall abmelden“ | `webserver.sign_payload/verify_session_token`, `pulse_sessions.py` |
| Autorisierung | `auth(request, session, perm=…, admin=…)` bei jeder Anfrage: Sitzung gültig → Bot/Guild bereit → Rechte live aus Teamrollen (`pulse_permissions`) → Default Deny | `webserver.auth`, `pulse_pro.cctx`, `pulse_ultimate.ctx_auth`, `pulse_next.ctx_auth` |
| CSRF | Same-Origin-Prüfung (Origin, sonst Referer; `Origin: null` = fremd; Cookie ohne Origin/Referer = fremd) für POST/PUT/PATCH/DELETE; SameSite=Lax | `pulse_security.same_origin_ok` |
| Header | CSP (`frame-ancestors`, `object-src`, `base-uri`, `form-action`), XFO, nosniff, Referrer-Policy, Permissions-Policy, COOP, HSTS bei HTTPS, `no-store` | `pulse_security.apply_headers` |
| Rangmodell | Aktionen auf Teammitglieder nur unterhalb des eigenen Teamrangs (Admins ausgenommen), Bot-Rolle muss über der Zielrolle liegen | `team_rank`, `ordered_team_role_ids`, Rollenmanager, `/action`, Beförderungen |
| Bot-Aktionen | Prüfung von Pulse-Recht **und** Discord-Recht **und** Rollenhierarchie vor Sanktionen; Zweitfreigabe für Server-Kick; Team-Kick entfernt nur Rollen | `pulse_mod_cases`, `cogs/pulse_suite.py`, `pulse_safety.py` |
| Recovery | Refresh-Tokens mit PyNaCl SecretBox (XSalsa20-Poly1305) verschlüsselt, Schlüssel aus `SESSION_SECRET` mit eigenem Label abgeleitet; nur Bürgerrolle im Backup-Server | `pulse_recovery.py` |
| Dateien | `contained_file` (basename + realpath), Typ per Signatur, Größenlimits, Zip-Restore nur über Allowlist (`_safe_member`, `_target_for`) | `pulse_safe_path.py`, `pulse_uploads.py`, `pulse_backup.py` |
| Netzwerk | SSRF-Schutz für Social-Feeds pro Redirect-Hop inkl. DNS-Auflösung; feste Hosts für OAuth/Roblox/Recovery | `cogs/social_promo.py` |
| Host | Host-Agent mit fester Aktionsliste, Token (`compare_digest`), nur Loopback/privat/Unix-Socket, systemd-Sandbox; Code-/Deploy-Dateien im Container schreibgeschützt | `deploy/pulse_host_agent.py`, `systemd/`, `compose.yaml` |
| Container | UID 1000, `cap_drop: ALL`, `no-new-privileges`, `pids_limit`, RAM-Limit, Log-Rotation, Port nur an 127.0.0.1 | `Dockerfile`, `compose.yaml` |
| CI/CD | Workflow-Token `contents: read`, alle Actions auf Commit-SHA gepinnt, Lockfile mit Hashes, pip-audit, gitleaks, CodeQL (Default Setup) | `.github/workflows/`, `requirements*.lock` |

## 2. Bedrohungsmodell

**Geschützte Ressourcen:** Teamakten/Warnungen/Notizen, Bewerbungen, Tickets/Transkripte/Fall-Anhänge, Discord-Rollen und
Moderationsrechte (Admin-Übernahme des Discord-Servers), Recovery-Tokens, `SESSION_SECRET`/`.session_secret`, Bot-Token,
OAuth-Client-Secret, Host (Docker-Gruppe = root-äquivalent), Laufzeitdaten (SQLite/JSON, Backups).

**Angreifer (realistisch):**
1. Anonymer Internetnutzer (Login-Seite, `/apply`, öffentliche Statusseiten, Callback).
2. Bewerber mit Discord-Konto (öffentliches Formular → Bot-Nachricht an das Team).
3. Normales Teammitglied (`can_view_dashboard`) – IDOR, fremde Akten.
4. Teamler mit Teilrechten (`can_warn`, `can_promote`, `can_manage_applications`, `can_manage_announcements`) – Rechteausweitung bis Discord-Admin, Selbstfreigaben.
5. Gestohlenes Sitzungscookie.
6. Kompromittierter Pulse-Prozess im Container (z. B. über eine künftige Lücke) – Ausbruch zum Host.
7. Lieferkette (Actions, Pakete, Base-Images).

**Trust Boundaries:** Browser ↔ Reverse-Proxy (Caddy/Host-Proxy) ↔ Uvicorn (nur 127.0.0.1/Docker-Netz, `PULSE_FORWARDED_ALLOW_IPS`);
Dashboard ↔ Discord-API (Bot-Token); Container ↔ Host (Bind-Mount, schreibgeschützte Code-/Deploy-Pfade; Host-Agent per Token);
GitHub ↔ VPS (Deploy-Workflow, SSH).

## 3. Authentifizierung, Sitzung, Token

- OAuth: `state` 24 Byte zufällig, 10 min, konstantzeitlicher Vergleich; nur `identify`. Discord akzeptiert nur registrierte Redirect-URIs.
- **Neu (SEC-02):** Die öffentliche Origin kommt aus `PUBLIC_BASE_URL`, sonst aus der Origin von `DISCORD_REDIRECT_URI`. Nur wenn beides fehlt, aus dem `Host`-Header (nur `host[:port]`-Syntax), nie mehr aus `X-Forwarded-Host`. `X-Forwarded-Proto` wirkt nur über Uvicorns `proxy_headers` von vertrauenswürdigen Proxys.
- **Neu (SEC-03):** Ein Login ohne speicherbare serverseitige Sitzung wird mit 503 abgelehnt (vorher: 7-Tage-Cookie ohne `sid`, ohne Idle-Timeout, durch Logout nicht beendbar). Logout mit einem alten Cookie ohne `sid` beendet nun alle `sid`-losen Cookies dieser Person (`pulse_sessions.revoke_unbound`), Sitzungen anderer Geräte mit `sid` bleiben.
- Sitzungsrechte werden pro Anfrage live berechnet; wer das Team verlässt, verliert sofort den Zugriff (und Sitzungen werden widerrufen).
- `SESSION_SECRET`: Platzhalter/zu kurz → Fallback auf erzeugte `.session_secret` (0600). **Nicht rotieren** ohne Plan: daraus wird auch der Recovery-Schlüssel abgeleitet (gespeicherte Recovery-Tokens wären danach unlesbar, Nutzer müssten neu freigeben).

## 4. Daten und Backups

Keine Schemaänderung in dieser Runde. Neue Prüfungen lesen vorhandene Felder (`created_by_id`, `from_id`/`to_id`, `content_type`)
und verändern keine gespeicherten Daten; Altdaten mit unsicherem `content_type` werden beim Ausliefern entschärft statt migriert.
Bestehende Garantien (geprüft, unverändert): atomares `save_json`, K-04-Merge bei Teamakten, Restore mit Sperre/Sicherheitskopie/Rollback.

## 5. Netzwerk und Infrastruktur

- Container: **neu** `cap_drop: [ALL]`, `security_opt: no-new-privileges:true`, `pids_limit: 256` (Pulse); `no-new-privileges` für Caddy.
- Boot-Update (`scripts/pulse-vps-boot-update.sh`): **neu** `safe.directory` wie in `remote_deploy.sh` – sonst bricht das als root laufende Skript bei „dubious ownership“ ab und Pulse startet nach einem Reboot nicht (lokal simuliert).
- Host-Agent-Unit: **neu** `ReadWritePaths=… -/run/lock` – unter `ProtectSystem=strict` war die gemeinsame Deploy-Sperre nicht beschreibbar, der Deploy lief ohne Sperre.
- Host-Agent: **neu** negatives `Content-Length` → 400 (vorher blockierte `read(-1)` den Handler-Thread).
- SSRF (Social-Feeds): **neu** nur `is_global`-Adressen, auch in IPv6-Hüllen (`::ffff:a.b.c.d`, NAT64 `64:ff9b::/96`); CGNAT, Benchmark- und Doku-Netze sind gesperrt.

## 6. Security-Tests

Regressionstests: `tests/test_security_hardening.py` (Angriffssimulationen mit Fakes, ohne Netzwerk/Discord). Jeder Test wurde gegen den
Code vor dem Fix ausgeführt und schlug dort fehl (rot → grün). Weitere angepasste Tests: `tests/test_ci_workflows.py` (alle Actions per SHA),
`tests/test_team_warning_domain.py` (Rücknahme braucht jetzt den handelnden Akteur – Default Deny).

Ausführen:
```
python -m ruff check .
python -m compileall -q .
python tests/feature_smoke.py
python tests/smoke_test.py
python -m unittest discover -s tests -p "test_*.py"
PULSE_E2E=1 python -m unittest tests.test_e2e_dashboard -v
pip-audit -r requirements.lock --require-hashes
```

## 7. Aufgabenliste

Legende: `[x]` behoben und getestet · `[ ]` offen · `[!]` blockiert · `[-]` nicht zutreffend

### P0 – Kritisch
- [x] **SEC-01** Beförderungsantrag vergab beliebige Rollen (auch Discord-Admin) und erlaubte Selbstfreigabe (`pulse_pro.create_promotion/decide_promotion`, aktive Routen `/promotions/create|decide`). Fix: nur nicht verwaltete Teamrollen ohne Administratorrecht, Nicht-Admins nur unterhalb des eigenen Teamrangs, keine Selbstbeförderung, Vier-Augen-Prinzip bei Freigabe, Sperre gegen parallele Doppelfreigabe, HTTP-Fehler werden gemeldet. Tests: `RoleEscalationTests.test_promotion_*`, `test_own_request_cannot_be_self_approved_and_stored_bad_role_is_refused`, `test_valid_promotion_still_works`.

### P1 – Hoch
- [x] **SEC-06** KI-Moderation: Review-Buttons erlaubten mit `can_warn`/`can_promote` Timeout/Kick/Bann ohne Moderationsrecht, Discord-Recht und Hierarchie; Team-Warn umging Rang-/Selbst-/Missbrauchssperren. Fix: `_action_problem` (Pulse-Recht + Discord-Recht + `target_problem`), Team-Warn über `issue_team_warning` (Gültigkeit 7 Tage wie Dashboard-Standard). Tests: `AiReviewRightsTests`.
- [x] **SEC-07** Einstellung nach Bewerbungsgespräch ohne Rangprüfung (`/action decide_app hire_after_interview`). Fix: gleiche Rangregel wie `action=hire`, keine Selbsteinstellung. Test: `test_hire_after_interview_respects_own_rank`.
- [x] **SEC-08** Rollenmanager (`POST /team/{id}/roles`) vergab Nicht-Teamrollen mit Serverrechten (Admin, Ban, Rollen verwalten …) unterhalb der eigenen Top-Rolle. Fix: solche Rollen nur durch Admins; Teamrollen behalten ihre Rangregel; Administrator nie delegierbar. Tests: `test_role_manager_refuses_roles_with_server_rights`, `test_role_manager_still_assigns_plain_and_team_roles`.
- [x] **SEC-11** Recovery: 401/`invalid_client` (z. B. rotiertes Client-Secret) löschte bei jedem Sync alle Einwilligungen; begrenzter und kompletter Sync konnten parallel laufen (doppelte Refresh-Token-Nutzung). Fix: Löschen nur bei `invalid_grant`, Abbruch bei Konfigurationsfehler, kein paralleler Lauf. Tests: `RecoveryConsentTests`.

### P2 – Mittel
- [x] **SEC-02** Gefälschter `X-Forwarded-Host`/`Host` landete als Link in der Team-Benachrichtigung (Phishing) bzw. in der Fallback-Redirect-URI. Tests: `ForgedHostHeaderTests`.
- [x] **SEC-03** Unwiderrufbare Sitzung bei fehlgeschlagener Sitzungsspeicherung; Logout beendete Cookies ohne `sid` nicht. Tests: `SessionWithoutSidTests`.
- [x] **SEC-04** Discord-Markdown-Injection (maskierte Links, Mentions) über Bewerbernamen in den Team-Alert; außerdem HTML-Escaping statt Discord-Escaping. Fix: `discord_text()`, ID nur Ziffern. Tests: `ApplicationAlertMarkdownTests`.
- [x] **SEC-09** Eigene oder ranghöhere Verwarnungen konnten zurückgezogen werden (Dashboard `remove_warn`, `/warn-remove`). Test: `test_warning_of_higher_or_own_rank_cannot_be_withdrawn`.
- [x] **SEC-10** `/pulse-approve` ignorierte Emergency Lock und Schutzsperre; für Server-Kick kein `kick_members` des Freigebenden. Tests: `SecondApprovalTests`.
- [x] **SEC-05** GitHub Actions (`actions/*`) nur per Tag gepinnt, obwohl der Deploy-Workflow SSH-Secrets nutzt. Fix: alle auf Commit-SHA (Tag als Kommentar, Dependabot aktualisiert). Test: `test_third_party_actions_are_pinned_to_a_commit` (verschärft).
- [x] **Infra** Boot-Update ohne `safe.directory`; Deploy-Sperre unter `ProtectSystem=strict` wirkungslos; Container ohne `cap_drop`/`no-new-privileges`/`pids_limit`. Tests: bestehende Deploy-/Boot-Tests grün, `docker compose config` geprüft; `safe.directory` lokal simuliert. Laufzeittest des gehärteten Containers **nicht ausgeführt** (kein Docker-Daemon in der Prüfumgebung).

### P3 – Niedrig
- [x] **SEC-12** `/ultimate/person/{id}` zeigte Warnungsanzahl und Timeline fremder Teamakten jedem Teamler (Export war bereits geschützt). Fix: `can_see_warnings`. Test: `test_person_page_hides_warnings_of_others`.
- [x] **SEC-13** Jede offene Übergabe konnte von jedem Teamler archiviert werden. Fix: Autor, Empfänger, offene Übergabe (ohne feste Person) oder Leitung. Test: `test_foreign_handover_cannot_be_archived_by_a_bystander`.
- [x] **SEC-14** SSRF-Prüfung ließ CGNAT/NAT64/IPv4-mapped-Literale durch. Tests: `FeedAddressTests`.
- [x] **SEC-15** Fall-Anhänge mit vom Client behauptetem Typ (z. B. `text/html`) ausgeliefert (durch `Content-Disposition: attachment` + `nosniff` bereits entschärft). Fix: Allowlist sicherer Typen. Test: `AttachmentTypeTests`.
- [x] **SEC-16** Host-Agent: negatives `Content-Length` blockierte einen Handler-Thread (nur mit gültigem Token). Test: `HostAgentBodyTests`.

### Offen / bewusst nicht geändert
- [ ] **R-01** `dashboard_session` ist bei einer Ausnahme der Sitzungsprüfung (DB gesperrt/defekt) fail-open (dokumentierte Entscheidung: DB-Schluckauf soll nicht alle aussperren; Rechte werden trotzdem live geprüft). Empfehlung: für Admin-/Schreibrouten fail-closed – erfordert Produktentscheidung.
- [ ] **R-02** DNS-Rebinding zwischen Prüfung und Verbindung bei Social-Feeds bleibt theoretisch möglich; praktisch stark begrenzt durch HTTPS-Pflicht mit Zertifikatsprüfung (ein internes Ziel bräuchte ein gültiges Zertifikat für den Angreifer-Host) und Einstellbarkeit nur durch Admins/`can_manage_announcements`.
- [ ] **R-03** CSP ohne `script-src`/`style-src` (Inline-UI). Jede künftige HTML-Injection wäre direkt ausnutzbar; Nonces erst nach Auslagerung des Inline-JS (U-03). Escaping (`esc`) bleibt die primäre Verteidigung.
- [ ] **R-04** Gebannte Mitglieder behalten ihre Recovery-Einwilligung (kein `unlink` bei Bann). Policy-Entscheidung nötig (Datenschutz vs. Wiederherstellung).
- [ ] **R-05** `./:/app` ist im Container beschreibbar → ein kompromittierter Prozess kann `*.py` dauerhaft ändern (kein Host-Ausbruch: `.git`, `deploy`, `scripts`, `systemd`, `compose.yaml`, `Dockerfile` sind `:ro`). Saubere Lösung (Code im Image, nur Datenpfade mounten) ist eine Deploy-Umstellung und wurde nicht ohne Freigabe vorgenommen.
- [ ] **R-06** `.env` ist vom Container beschreibbar und steuert die Compose-Interpolation (z. B. `PULSE_PUBLISH_HOST=0.0.0.0`). Nicht automatisch überschrieben, weil eine bewusste Direktfreigabe sonst nach einem Reboot verschwände; Empfehlung: Port-Bindung fest eintragen, wenn keine Direktfreigabe benötigt wird.
- [ ] **R-07** Base-Images nur per Tag (`python:3.14-slim`, `caddy:2.8-alpine`), kein Digest; Caddy-Image in `compose.yaml` wird von Dependabot (`docker`) vermutlich nicht erfasst.
- [ ] **R-08** Transkripte: kein Gesamtlimit für Anhänge pro Ticket (je Datei 8 MB). Speicher-DoS durch Ticket-Ersteller möglich.
- [ ] **R-09** `/ultimate/team/{id}/update`: Führungskräfte (`can_promote`) können Profilfelder (internes Rating/Status) auch höherrangiger Mitglieder ändern (keine Rangprüfung, niedriger Schaden).
- [ ] **R-10** Laufzeitdaten in der Git-Historie (Commit `f1e83a4`: `pulse.db`, `audit_logs.json`, in `2103599` entfernt). Bereinigung nur per History-Rewrite (Force-Push) – nicht ohne ausdrückliche Freigabe.
- [ ] **R-11** `actions/checkout` ohne `persist-credentials: false` (Token nur `contents: read`).

### Geprüft – kein Befund (Auswahl)
- [-] OAuth-State/Login-CSRF, Open Redirect (`local_path`/`back`), `msg`-Parameter, JSON in `<script>`: geschützt.
- [-] API-Keys: 256 Bit, nur SHA-256 gespeichert, Lookup per Hash, Scopes/Ablauf/Rate-Limit.
- [-] Recovery-Krypto: SecretBox (AEAD) mit zufälliger Nonce, Schlüsseltrennung über Label; nur Bürgerrolle, gefährliche Rechte werden abgelehnt.
- [-] Pfade/Uploads/Zip-Restore: `contained_file`, Signaturprüfung, Allowlist; Full-Backups nicht hochladbar.
- [-] Host-Agent: kein `shell=True`, feste Argumentlisten, SHA-/Job-ID-Validierung, Fast-Forward-Pflicht, Token-Vergleich konstantzeitlich.
- [-] Workflows: keine `${{ github.event.* }}` in `run:`, kein `pull_request_target`, minimale `permissions`.
- [-] Git-Historie: keine Discord-Token-, Webhook-, Google-/GitHub-Key-Muster gefunden (nur Test-Fixtures).
- [-] Anti-Nuke/AutoMod/KI standardmäßig aus; KI sanktioniert nie automatisch. Honeypot ist deterministisch (fester Kanal) und gewollt aktiv.

## 8. Manuelle Sicherheitseinstellungen (nur Repository-Inhaber / VPS)

Nicht verifiziert in dieser Runde (kein GitHub-Zugriff aus der Prüfumgebung, kein VPS-Zugriff). Stand des letzten Berichts (2026-10-24):

1. **GitHub → Settings → Branches/Rulesets:** Ruleset für `main` – Pull Request erforderlich, Status-Checks „Pulse Quality and Security Checks“ erforderlich, Force-Push und Löschen verbieten.
2. **GitHub → Settings → Environments → `production`:** Required reviewers und Deployment-Branch-Policy „nur `main`“.
3. **GitHub → Settings → Code security:** Dependabot security updates aktivieren (Versionsupdates sind per `dependabot.yml` aktiv); Secret Scanning + Push Protection und CodeQL aktiv lassen.
4. **VPS:** Deploy-SSH-Schlüssel in `authorized_keys` mit `command=`/`restrict` auf das Deploy-Skript beschränken; prüfen, dass `PULSE_PUBLISH_HOST` in `.env` nicht `0.0.0.0` ist (außer gewollt); nach dem nächsten Deploy `docker inspect pulse-teamos --format '{{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}} {{.HostConfig.PidsLimit}}'` prüfen und Healthcheck abwarten.
5. **VPS:** geänderte Unit übernehmen: `install -m 644 systemd/pulse-host-agent.service /etc/systemd/system/ && systemctl daemon-reload && systemctl restart pulse-host-agent`; das Boot-Skript installiert sich beim nächsten Boot-Update selbst nach `/usr/local/sbin`.
6. **Discord:** offene Beförderungsanträge mit Nicht-Teamrollen prüfen (werden jetzt abgelehnt); prüfen, ob Rollen mit Admin-/Moderationsrechten unterhalb der Teamleitungsrollen liegen.
