---
name: pulse-security-audit
description: Sicherheitsprüfung von Pulse-Änderungen oder eines Bereichs – serverseitige Autorisierung jeder Dashboard-/Admin-Route, Same-Origin/CSRF, Sessions/Cookies, Eingabevalidierung, Pfad-/Upload-Sicherheit, XSS im HTML-Rendering, Secrets und Discord-Aktionen. Verwenden vor Merge sicherheitsrelevanter Änderungen oder bei Verdacht auf eine Lücke. Ändert keine Dateien.
argument-hint: [diff | Datei/Route/Bereich]
disallowed-tools: Edit, Write, NotebookEdit
---

# Pulse-Security-Audit (nur lesen)

Keine Exploits gegen den VPS oder fremde Systeme, keine echten Discord-/OAuth-Aufrufe, `.env` nicht lesen, keine Secrets ausgeben (nur Fundstelle + Art nennen). Befunde mit Testfall belegen, wo möglich (TestClient/Mocks, nur lokal).

## Umfang
- Kein Argument / `diff`: `git diff main...HEAD` + unversionierte Dateien.
- Datei/Route/Bereich: gezielt dort, inkl. aller Registrierungen derselben Route (`grep -n '"<pfad>"' webserver.py pulse_*.py`) – `pulse_pro` u. a. ersetzen Handler zur Laufzeit.

## Prüfliste
1. **Autorisierung (Regel 4):** Jede GET/POST-Route ruft `auth(request, user_session, perm=…, admin=…)` (bzw. `cctx` in `pulse_pro`) **vor** jeder Daten- oder Discord-Aktion. Passender `perm`-Schlüssel aus `PERM_KEYS`; Admin-Aktionen mit `admin=True`. Mehrstufige Aktionen (`/action` mit `action=…`) prüfen die Zuordnung Aktion → Recht (Tabelle bei `"set_weekly_goal": "admin"`). UI-Ausblendung zählt nicht.
2. **Rollenhierarchie:** Rollen vergeben/entziehen nur unterhalb der eigenen und der Bot-Rolle; Team-Kick entfernt nur Rollen (Regel 5).
3. **Same-Origin/CSRF:** `pulse_security.same_origin_ok` greift für zustandsändernde Methoden; keine neuen Ausnahmen ohne Begründung. Keine zustandsändernden GET-Routen.
4. **Sessions/Cookies/API-Keys:** `pulse_sessions` – Ablauf, Widerruf, `HttpOnly`/`Secure`/`SameSite`, konstante Vergleiche (`hmac.compare_digest`, vgl. `webserver.py`), keine Tokens in URLs/Logs.
5. **Eingaben:** Typ-/Bereichsprüfung serverseitig (Browser-`min/max` umgehbar), Längenlimits, IDs gegen die Guild prüfen.
6. **XSS:** Nutzer-/Discord-Inhalte im HTML immer über `esc(...)`; auch Attribute, `onclick`, Inline-`<script>`-Daten (JSON → `json.dumps` + Escape von `</`).
7. **Pfade/Uploads/Backups:** Keine Pfade aus Eingaben ohne Normalisierung + Basisverzeichnis-Prüfung (vgl. CodeQL-Fix `py/path-injection` beim Backup-Pfad); Zip-Entpacken über `pulse_backup._safe_member`; Upload-Typ/Größe begrenzt (`pulse_uploads`).
8. **Befehle/Host-Agent:** Server Control Center / `deploy/pulse_host_agent.py`: feste Befehlsliste, kein `shell=True` mit Eingaben, Bestätigung für Deploy/Rollback/Neustart.
9. **Rate-Limits/DoS:** Login/OAuth/öffentliche Endpunkte begrenzt (`tests/test_oauth_rate_limits.py`), Limiter selbst speicherbegrenzt.
10. **Secrets/Logs:** Keine Tokens/Webhook-URLs in Code, Tests, Logs oder Fehlermeldungen; `.gitignore` deckt Laufzeitdaten ab.
11. **KI/AutoMod:** Keine automatische Sanktion allein durch KI; Features nicht still produktiv aktiviert (Regel 6).

Passende vorhandene Tests: `test_phase1_security`, `test_security_admin`, `test_security_headers`, `test_session_tokens`, `test_sessions_api_keys`, `test_permissions`, `test_privacy`, `test_oauth_rate_limits`, `test_pulse_uploads`, `test_full_backup`, `test_server_control`. Optional ergänzend: `/security-review`.

## Ergebnis (Deutsch)
Je Befund: Schwere (kritisch/hoch/mittel/niedrig), `datei:zeile`, Angriffsszenario (wer, welche Anfrage, welche Wirkung), belegt oder Vermutung, Fix-Vorschlag und Regressionstest (inkl. „keine Schreibaktion bei Ablehnung“). Danach die ausgeführten Tests mit echtem Ergebnis.
