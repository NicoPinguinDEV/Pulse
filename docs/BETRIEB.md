# Pulse TeamOS – Betriebshandbuch

Stand: 30.10.2026 · 7.17.0 (`main`). Grundlage: DOC-03, zuletzt an Backup-/Restore-Test, Container-Grenzen, Crash-Neustart und Job-Überwachung angepasst. Einrichtung eines neuen Servers: `VPS_DEPLOYMENT.md` (§0 Vionity-Schnellstart).

## 1. Vor dem Start

1. `.env` aus `.env.example` anlegen (`chmod 600 .env`). Nie `.env`, `.session_secret`, Datenbanken, Teamdaten oder Transkripte in Git, Issues oder Chats kopieren.
2. Pflicht: `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI` oder `PUBLIC_BASE_URL`. `SESSION_SECRET` mit mindestens 32 zufälligen Zeichen – sonst nutzt Pulse `.session_secret` und warnt.
3. Beim Start prüft Pulse alle gesetzten optionalen Werte (O-06) und schreibt Hinweise wie `⚠️ Konfigurationsprüfung: SESSION_DAYS: erwartet eine ganze Zahl (1–365).` in Konsole und `logs/pulse.log`. Die Werte selbst werden nie ausgegeben. Dieselbe Prüfung steht im Systemstatus unter „Konfigurationswerte“.
4. Discord Developer Portal: Intents Members, Presences und Message Content; die Bot-Rolle muss über allen Rollen stehen, die Pulse vergibt oder entzieht. Der Systemstatus zeigt fehlende IDs, Hierarchielücken, Zeitzonendaten und nicht geladene Cogs.

## 2. Start und Gesundheitscheck

- Lokal: `python main.py` · Server: `docker compose up -d --build` (mit HTTPS: `docker compose --profile https up -d --build`).
- `GET /healthz` antwortet **200** nur, wenn Bot verbunden, Server im Cache und Datenbank erreichbar sind (sonst 503). Zusätzlich: `version`, `gateway_latency_ms`, `memory_mb` (Container-RAM). Auf dem VPS: `curl -s http://127.0.0.1:25095/healthz`.
- 200 heißt nicht, dass jeder Cog fehlerfrei arbeitet – dafür Systemstatus und Logs prüfen.
- Logs: `docker compose logs --tail=200 pulse` und `logs/pulse.log` (1 MB × 3). Die letzten 200 Fehler stehen mit Fehler-ID im Systemstatus.
- Hintergrundjobs: Jede `@tasks.loop` steht im Job-Register (Systemstatus, `/healthz` → `jobs_failing`); auch abgefangene Fehler zählen dort als fehlgeschlagener Lauf.
- Absturz/OOM zur Laufzeit: `pulse-crash-restart.timer` startet den Container nach höchstens 2 Minuten neu (max. 3×/Stunde, nie während Boot-Update oder Deploy) – Einrichtung `VPS_DEPLOYMENT.md`.

## 3. Backups und Restore

1. Dashboard → `/backups` (Administratoren). Vor riskanten Änderungen ein **Komplett-Backup** erstellen; es enthält alle SQLite-Dateien (konsistente Kopie), JSON-Dateien und – im Rahmen von `PULSE_BACKUP_MAX_MB` – Uploads und Transkripte, mit Manifest und Prüfsummen.
2. Automatische Backups alle `AUTO_BACKUP_HOURS` (0 = aus); Aufbewahrung 7 täglich / 4 wöchentlich / 6 monatlich, Gesamtgrenze `PULSE_BACKUP_TOTAL_MAX_MB`. Der Deploy legt zusätzlich `backups/predeploy_*` an.
3. **Restore:** In der Liste „Probelauf / Wiederherstellen“ öffnen. Die Vorschau zeigt Dateien und Tabellenzahlen; ein fehlerhaftes Backup wird blockiert. Erst nach Prüfung `WIEDERHERSTELLEN` eingeben – Pulse sichert vorher den aktuellen Stand und sperrt währenddessen Schreibzugriffe.
4. Danach Bot neu starten (`docker compose restart pulse`) und Dashboard, Tickets, Bewerbungen, Warnrollen und Voice-Support prüfen. Das Ergebnis des letzten Restore-Tests steht im Systemstatus: Er stellt das neueste Komplett-Backup wöchentlich probeweise in ein Temp-Verzeichnis wieder her (Prüfsummen, Integrität, Tabellen/Zeilen, JSON, Dateien, Wiederherstellungsziel je Eintrag) – die Live-Daten bleiben unberührt. Das Komplett-Backup enthält auch die hochgeladene Wartemusik (`assets/support_wait.*`).
5. Vionity sichert nicht automatisch: regelmäßig einen Snapshot im Kundenbereich anlegen und `backups/` außerhalb des Servers kopieren.

Transkripte und Anhänge bis 8 MB werden unbegrenzt aufbewahrt (E-7). Datenschutz-Fristen (E-9) sind standardmäßig aus und laufen nur mit frischem Backup.

## 4. Deployment und Rollback

Automatisch nach einem Merge auf `main` (`.github/workflows/deploy-vps.yml` → `deploy/remote_deploy.sh`), nur wenn die `PULSE_VPS_*`-Secrets gesetzt sind:

1. Es wird genau der Commit ausgerollt, auf dem die Qualitäts- und Sicherheitsprüfungen erfolgreich liefen; nie zwei Deploys gleichzeitig.
2. Abbruch ohne Änderung, wenn der Commit nicht auf `origin/main` liegt, älter als der laufende Stand ist oder auf dem VPS versionierte lokale Änderungen existieren.
3. Backup mit der laufenden Version (`predeploy_*`) – schlägt es fehl, bricht der Deploy ab.
4. `git reset --hard <sha>` (fasst nur versionierte Dateien an) und `docker compose up -d --build`.
5. `/healthz` muss innerhalb von `PULSE_DEPLOY_HEALTH_SECONDS` (Standard 240 s) 200 liefern, sonst automatischer Rollback auf den vorherigen Commit; Ergebnis optional per `PULSE_DEPLOY_WEBHOOK_URL` in Discord.

**Aus dem Dashboard (ab 7.17.0):** Verwaltung → Serververwaltung → Server Control Center startet dasselbe `remote_deploy.sh` (plus Code-Rollback über `deploy/rollback_code.sh`, kontrollierten Neustart, Container-Logs) über den eingeschränkten Host-Verwaltungsdienst – Einrichtung und Testschritte: `docs/SERVER_CONTROL_CENTER.md`.

**Manueller Notfall-Rollback:** `cd /opt/pulse/Pulse && git rev-parse HEAD` notieren, Komplett-Backup erstellen, dann `git reset --hard <letzter guter Commit>` und `docker compose up -d --build`. Nie `git clean`, `rm` auf Datendateien oder `docker compose down -v`.

## 5. Wartungsmodus (F-04)

Verwaltung → Neue Funktionen (`/admin/features`, nur Administratoren): Wartungsmodus mit Grund starten. Dann sieht jeder ein Banner, schreibende Dashboard-Anfragen von Nicht-Admins werden mit 503 abgelehnt und Bot-Automationen pausieren; Voice-Support und RAM-Schutz laufen weiter. Für Migrationen und größere Restores verwenden, danach wieder beenden.

## 6. Schichten: automatisch beendete Dienste (E-2)

Eine nicht beendete Schicht wird nach 24 h automatisch geschlossen und mit einer Team-Verwarnung versehen. Sie zählt **0 Stunden**, bis eine höherrangige Führungskraft (Recht „Befördern“ oder Admin) in der Teamakte unter „Automatisch beendete Schichten“ die echte Dauer mit Pflichtgrund bestätigt – höchstens `MAX_SHIFT_HOURS` (Standard 12 h). Die Bestätigung ist einmalig und steht im Audit-Log. Ältere Einträge, die vor E-2 bis zu 24 h gutgeschrieben hatten, zählen ebenfalls 0 h, bis sie bestätigt sind.

## 7. Fehlersuche

- **Bot offline / Befehle fehlen:** Gateway, Token, Cog-Ladefehler im Systemstatus; Slash-Befehle bei Bedarf über den Knopf im Systemstatus oder `PULSE_FORCE_COMMAND_SYNC=1` neu synchronisieren.
- **Rollenänderung scheitert:** Bot-Rolle über der Zielrolle und „Rollen verwalten“ nötig; IDs nie aus Screenshots ableiten.
- **Voice-Support ohne Audio:** `docker compose exec pulse python pulse_audio_check.py` (ffmpeg/Opus), Warteraum-ID und Voice-Rechte prüfen.
- **Beschädigte JSON-Datei:** Pulse sichert sie als `*.corrupt-*` und zeigt einen 503-Hinweis; die Sicherung bis zur Klärung behalten, aus dem letzten Backup wiederherstellen.
- **Datenbankprobleme:** Bot stoppen, Dateikopie anlegen, Komplett-Backup/Restore-Vorschau nutzen – nie DB-Dateien löschen, um einen Start zu erzwingen.
- **Konsistenz:** Der Systemstatus meldet verwaiste Schichten, Tickets ohne Kanal, abweichende Warnrollen und Ähnliches (D-10) – nur Bericht, keine automatische Korrektur.
- **RAM:** Containerlimit 768 MiB (+256 MiB Swap); RAM-Schutz meldet ab 500 MB (Warnung), 620 MB (kritisch), 700 MB (Notfall), optional im `PULSE_SYSTEM_ALERT_CHANNEL_ID`. Die Schwellen haben Hysterese; keine zusätzlichen Dauerschleifen oder unbegrenzten Caches einbauen.

## 8. Freigabegrenzen

- Kein Merge nach `main` und damit kein Deploy ohne ausdrückliche Freigabe des Betreibers.
- KI liefert nur Verdachtsfälle; Sanktionen bestätigt immer ein Mensch. Team-Kick entfernt nur Teamrollen, ein Server-Kick ist eine eigene, bestätigte Aktion.
- URLs, Slash-Befehle, `custom_id`s und Datenschlüssel bleiben rückwärtskompatibel.
