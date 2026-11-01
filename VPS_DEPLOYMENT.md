# Pulse TeamOS auf einem eigenen VPS betreiben

Diese Anleitung lässt GitHub `NicoPinguinDEV/Pulse` als Codequelle bestehen. Der VPS startet Pulse in Docker mit Python 3.15 (`FROM python:3.15-slim` im `Dockerfile`, maßgeblich; die CI testet 3.15 und 3.14); Bot und FastAPI-Dashboard laufen wie bisher zusammen auf `SERVER_PORT` (Standard: 25095).

## Empfohlener Ausgangspunkt

- Ubuntu 24.04 LTS
- Mindestens 4 GB RAM; für Pulse mit Dashboard, Backups und zahlreichen Discord-Mitgliedern werden 8 GB RAM empfohlen.
- Genügend SSD-Speicher für die wachsenden Backups und Transkripte.
- SSH-Zugang zum Server.

**Hinweis für den bestehenden 2-GiB-VPS:** In `compose.yaml` sind für Pulse 768 MiB RAM plus maximal 256 MiB zusätzlicher Swap vorgesehen. Damit bleibt nominal etwa 1 GiB Host-RAM für Ubuntu, Docker, SSH und optionale Dienste frei; bei anhaltend hoher Host-Auslastung bleibt ein Upgrade auf mindestens 4 GiB RAM empfehlenswert.

## 0. Schnellstart für Vionity (KVM, Ubuntu 24.04, 2 GiB)

Pulse läuft auf einem Vionity-KVM-Server (Standort Offenbach, Ubuntu 24.04, 2 vCores, 2 GiB RAM, 50 GB SSD, Login als `root`). Das Skript `deploy/vps_bootstrap.sh` erledigt die Schritte aus Abschnitt 1 in einem Durchgang und kann gefahrlos wiederholt werden:

```bash
apt-get update && apt-get install -y git
git clone https://github.com/NicoPinguinDEV/Pulse.git /opt/pulse/Pulse
sudo bash /opt/pulse/Pulse/deploy/vps_bootstrap.sh
```

Es installiert Updates (danach automatische Sicherheitsupdates, Neustarts bleiben manuell), Docker und Compose, legt 2 GiB Swap an (der KVM-Server bringt meist keinen mit, `compose.yaml` erlaubt Pulse 256 MiB Swap), aktiviert die Firewall nur für SSH/80/443 und fail2ban für SSH und setzt die Rechte für den Container-Benutzer (UID 1000). Laufzeitdaten werden nicht angefasst und Pulse wird nicht gestartet – weiter mit Abschnitt 2.

Hinweise zum Vionity-Kundenbereich:

- **Root-Passwort:** Es wird im Kundenbereich angezeigt. Für Deployments einen SSH-Schlüssel nutzen (Abschnitt 5) und danach in `/etc/ssh/sshd_config` `PasswordAuthentication no` setzen – erst, wenn der Schlüssel-Login nachweislich funktioniert.
- **„Updates: verfügbar“:** erledigt das Skript; einen ggf. nötigen Neustart (`/var/run/reboot-required`) außerhalb der RP-Zeiten mit `reboot` ausführen – der Container startet automatisch wieder.
- **„IP Blacklist“-Einträge:** betreffen meist E-Mail-Versand von der IP. Discord und das Dashboard sind davon normalerweise nicht betroffen; nur prüfen, falls Pulse E-Mails verschicken soll.
- **„Backup: Manuell“:** Vionity sichert nicht automatisch. Pulse erstellt vor jedem Deploy `backups/predeploy_*`; zusätzlich regelmäßig einen Vionity-Snapshot anlegen und `backups/` außerhalb des Servers kopieren.
- **Firewall und Docker:** Docker umgeht `ufw` für veröffentlichte Ports. Deshalb bindet `compose.yaml` Pulse standardmäßig an `127.0.0.1`; nur Caddy (Profil `https`) veröffentlicht 80/443. `PULSE_PUBLISH_HOST=0.0.0.0` macht Port 25095 trotz Firewall öffentlich.
- **RAM:** Der Host-Speicher wird im Kundenbereich angezeigt; `curl -s http://127.0.0.1:25095/healthz` zeigt zusätzlich `memory_mb` (Container) und `gateway_latency_ms`.

## 1. Server vorbereiten

Verbinde dich per SSH zum Server und installiere Git und Docker:

```bash
sudo apt update
sudo apt install -y git docker.io docker-compose-v2
sudo systemctl enable --now docker
```

Falls `docker compose version` danach nicht funktioniert, installiere das aktuelle Docker-Compose-Plugin gemäß der offiziellen Docker-Anleitung für Ubuntu.

Lege das Anwendungsverzeichnis an und gib es deinem SSH-Benutzer:

```bash
sudo mkdir -p /opt/pulse
sudo chown "$USER":"$USER" /opt/pulse
git clone https://github.com/NicoPinguinDEV/Pulse.git /opt/pulse/Pulse
cd /opt/pulse/Pulse
```

Falls du die App später automatisiert per GitHub Actions aktualisieren willst, braucht der SSH-Benutzer außerdem die Berechtigung, Docker auszuführen. Der Benutzer der Docker-Gruppe besitzt praktisch weitreichende Systemrechte; vergib diese Berechtigung nur einem vertrauenswürdigen Account.

## 2. Bestehende Pulse-Daten sichern und übertragen

**Nicht einfach nur den Code klonen und starten.** Die Laufzeitdaten liegen nicht vollständig in GitHub. Erstelle zunächst über das bestehende Bot-Hosting ein Backup und lade vorhandene Laufzeitdateien herunter. Mindestens relevant sind – soweit vorhanden – `config.json`, `team_data.json`, `applications.json`, `shifts.json`, `logs.json`, `audit_logs.json`, `meetings.json`, `pulse.db`, `abmeldungen.db`, `activity_check.db`, der Ordner `backups/` und der Ordner `transcripts/`. Weitere im Hosting vorhandene Datenbank- oder Zustandsdateien ebenfalls sichern.

Stoppe den alten Pulse-Prozess während des abschließenden Datenexports, damit sich die SQLite-Datenbanken und JSON-Dateien während des Kopierens nicht ändern. Kopiere die gesicherten Laufzeitdaten in einen Übergabeordner (z. B. `/root/pulse-import`) und übernimm sie mit `python3 scripts/pulse_migrate_data.py --source /root/pulse-import --target /opt/pulse/data` (erst Probelauf, dann `--execute`). `/opt/pulse/data` wird als `/data` in den Container eingebunden und bleibt bei Code-Updates bestehen. Eine ganz neue Installation ohne Altdaten startet einmal mit `PULSE_DATA_DIR_INIT=1` in `.env`. **Die alte Instanz erst abschalten, wenn die neue Instanz getestet wurde.**

## 3. Secrets und Konfiguration einrichten

```bash
cd /opt/pulse/Pulse
cp .env.example .env
nano .env
```

Trage die echten Discord-Werte ein, insbesondere `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_GUILD_ID`, `PUBLIC_BASE_URL`, `DISCORD_REDIRECT_URI` und einen zufälligen, langen `SESSION_SECRET`. Ändere keine bestehenden Werte blind; übertrage die funktionierenden Einstellungen aus der bisherigen Instanz und aktualisiere URLs nur, wenn sie sich ändern.

Wenn du das Discord-OAuth-Dashboard verwendest, kontrolliere, dass die öffentliche URL und die Redirect-URI exakt mit den Einstellungen im Discord Developer Portal übereinstimmen. Für einen öffentlichen Produktivbetrieb wird eine Domain mit HTTPS dringend empfohlen. Öffne keine Zugangsdaten und keinen `.env`-Inhalt in GitHub-Issues oder öffentlichen Commits.

```bash
chmod 600 .env
```

## 4. Pulse starten und prüfen

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=150 pulse
```

Nach einem Serverneustart startet `pulse-github-update.service` den Container (Compose selbst hat `restart: "no"`). Standardmäßig ist der Dashboard-Port 25095 nur an `127.0.0.1` gebunden (`PULSE_PUBLISH_HOST`); öffentlich erreichbar ist das Dashboard über den HTTPS-Reverse-Proxy. Öffne in der Firewall nur benötigte Ports; SSH und Dashboard sollten nach Möglichkeit eingeschränkt bzw. über HTTPS bereitgestellt werden.

### Trennung Container ↔ Host (Code und Laufzeitdaten, Phase 13)

Seit Phase 13 (D-06, SEC R-05) sind Code, Laufzeitdaten, Secrets und Deploy-Dateien getrennt:

| Was | Wo auf dem VPS | Im Container |
|---|---|---|
| Anwendungscode | nur im Docker-Image (gebaut aus `/opt/pulse/Pulse`) | `/app`, **schreibgeschützt** (`read_only: true`, Code gehört root) |
| Laufzeitdaten (SQLite, JSON, Audit-Logs, Bewerbungen, Schichten, `uploads/`, `attachments/`, `transcripts/`, `backups/`, `logs/`, Wartemusik `assets/support_wait.*`, `.session_secret`) | `/opt/pulse/data` (`PULSE_DATA_HOST_DIR`) | `/data` (`PULSE_DATA_DIR`), einziger beschreibbarer Ordner neben `/tmp` (64 MB RAM) |
| Secrets/Konfiguration | `/opt/pulse/Pulse/.env` (`chmod 600`) | nur als Umgebungsvariablen – die Datei selbst ist nicht im Container |
| Deploy-/Systemdateien (`.git`, `deploy/`, `scripts/`, `systemd/`, `compose.yaml`) | `/opt/pulse/Pulse` | nicht sichtbar |
| Socket des Host-Agents | `/opt/pulse/Pulse/.pulse-agent/` | `/app/.pulse-agent` (unverändert, damit `PULSE_HOST_AGENT_URL` gültig bleibt) |

Der installierte Commit für den Systemstatus kommt als Build-Information (`PULSE_GIT_SHA`, gesetzt von `remote_deploy.sh`, `rollback_code.sh` und dem Boot-Update) ins Image. Pulse startet im neuen Layout nur, wenn `/data` die Markierung `.pulse-data.json` mit Zustand `complete` (Migration) oder `initialized` (bewusst neue Installation mit `PULSE_DATA_DIR_INIT=1`) trägt – ein leeres oder halb migriertes Verzeichnis führt zu Exit-Code 78 statt zu einem „leeren“ Server; der Crash-Neustart startet den Container dann nicht erneut. Fehlt `/opt/pulse/data`, startet Compose den Container gar nicht (`create_host_path: false`). Die root-Skripte setzen `COMPOSE_FILE=compose.yaml`, sodass eine `compose.override.yaml` oder ein `COMPOSE_FILE` in `.env` nie gelesen wird.

Ohne `PULSE_DATA_DIR` (Bot-Hosting ohne Docker, lokale Entwicklung) bleibt alles wie bisher im Arbeitsverzeichnis.

### Phase 13.1: einmalige Datenmigration auf dem VPS (Schritt für Schritt)

**Diese Anleitung ist die einzige maßgebliche Reihenfolge** (Phase 13.2). Die Berichte `docs/PHASE13_BERICHT.md` und `docs/PHASE13_1_BERICHT.md` verweisen hierher. Nur mit ausdrücklicher Freigabe und außerhalb der RP-Zeiten; Dauer ca. 30–45 Minuten, Pulse ist ca. 10–15 Minuten offline.

**So ist die Anleitung zu lesen**

- 🪟 **Windows (Browser oder PowerShell)** – auf deinem Windows-11-PC.
- 🐧 **VPS (Bash)** – auf dem Ubuntu-24.04-Server, nachdem du dich per SSH verbunden hast. Diese Befehle **nie** in PowerShell eingeben.
- Jeden Befehl **einzeln** kopieren und mit Enter ausführen. Danach die Zeile *Erwartet* vergleichen.
- ⛔ **STOPP** heißt: nicht weitermachen. Ausgabe sichern (Screenshot oder Text) und erst nach Klärung fortfahren. Bis einschließlich Schritt 29 (Probelauf) sind Pulse-Code (Checkout und Image) und Pulse-Daten unverändert. Geändert sind bis dahin nur die beiden Root-Helfer in `/usr/local/sbin` (Schritt 20) und die Pausen-Datei (Schritt 25). Die neuen Helfer starten den alten Stand weiterhin korrekt und verweigern nur ein Update ins neue Layout ohne Migration. Sie bleiben bei einem Abbruch daher bewusst installiert. Ein Abbruch bis Schritt 29 ist damit für Pulse folgenlos (Container starten, Pausen-Datei entfernen).

**Warum diese Reihenfolge (aus dem Code abgeleitet)**

1. **Root-Helfer zuerst (Schritte 13–20):** systemd startet bei jedem Boot `/usr/local/sbin/pulse-vps-boot-update.sh` (`pulse-github-update.service`). Der dort installierte Phase-12-Helfer kennt weder die Migrationsmarkierung noch die Deploy-Sperre und würde nach einem Neustart `main` (neues Layout) ohne Migration einspielen. Der neue Helfer verweigert das, solange `/opt/pulse/data` nicht migriert ist, und startet dann nur den installierten Stand.
2. **Migration vor dem ersten Deploy (Schritte 26–32):** `deploy/remote_deploy.sh` bricht ohne Markierung `complete` vor jeder Änderung ab; ein Container im neuen Layout ohne Markierung endet mit Exit 78.
3. **Deploy nur über GitHub Actions (Schritt 33):** Der Workflow schickt `remote_deploy.sh` aus dem **geprüften Commit** per SSH. Das Server Control Center (Host-Agent) startet dagegen `deploy/remote_deploy.sh` aus dem VPS-Checkout – beim ersten Deploy ist das noch die **Phase-12-Version** ohne Markierungsprüfung und ohne Layoutwechsel-Schutz. Für diesen ersten Deploy das Server Control Center **nicht** verwenden.
4. **Crash-Neustart erst ganz am Ende wieder an (Schritt 41):** Er würde einen gestoppten oder abgestürzten Container sonst mitten in der Wartung starten.

#### A – Sicherung (Pulse läuft weiter)

1. 🪟 **Browser:** Vionity-Kundenbereich → Server → **Snapshot erstellen**.
   *Erwartet:* Snapshot mit heutigem Datum in der Liste. ⛔ STOPP, wenn der Snapshot fehlschlägt.
2. 🪟 **Browser:** Pulse-Dashboard → Verwaltung → **Server Control Center → Backups → „Backup erstellen + verifizieren“** (oder `/backups` → Komplett-Backup), danach die neue Datei `pulse_full_…zip` **herunterladen**.
   *Erwartet:* Status „verifiziert“, Datei im Download-Ordner. ⛔ STOPP bei Prüffehler.
3. 🪟 **PowerShell:** Größe des heruntergeladenen Backups prüfen:
   ```powershell
   Get-ChildItem "$env:USERPROFILE\Downloads\pulse_full_*.zip" | Select-Object Name, Length, LastWriteTime
   ```
   *Erwartet:* die neue Datei mit einer Größe deutlich über 0. ⛔ STOPP, wenn sie fehlt.

#### B – Verbinden und Zustand prüfen (nur lesen)

4. 🪟 **PowerShell:** mit dem VPS verbinden (Adresse/Port/Benutzer wie gewohnt; Schlüssel-Passphrase eingeben):
   ```powershell
   ssh root@<VPS-IP>
   ```
   *Erwartet:* Ubuntu-Begrüßung und eine Eingabezeile `root@…:~#`.
5. 🐧 **VPS:** prüfen, dass du root bist:
   ```bash
   id -u
   ```
   *Erwartet:* `0`. Sonst ab hier jedem Befehl `sudo ` voranstellen.
6. 🐧 In den Pulse-Ordner wechseln:
   ```bash
   cd /opt/pulse/Pulse
   ```
7. 🐧 Git für diese SSH-Sitzung den Checkout erlauben (der Ordner gehört UID 1000; ändert keine Datei und keine Git-Konfiguration):
   ```bash
   export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/opt/pulse/Pulse
   ```
8. 🐧 Installierten Stand anzeigen:
   ```bash
   git log -1 --oneline
   ```
   *Erwartet:* `d221b6c Merge pull request #92 …` (Phase 12). ⛔ STOPP bei einem anderen Commit – erst klären, was dort läuft.
9. 🐧 Keine lokalen Änderungen am Code:
   ```bash
   git status --porcelain
   ```
   *Erwartet:* keine Ausgabe oder nur `?? assets/support_wait.mp3`. ⛔ STOPP bei anderen Zeilen.
10. 🐧 Pulse ist gesund:
    ```bash
    curl -s http://127.0.0.1:25095/healthz
    ```
    *Erwartet:* JSON mit `"ok":true`, `"bot_ready":true`, `"guild_ready":true`, `"database_ok":true`.
11. 🐧 Noch kein Datenverzeichnis vorhanden:
    ```bash
    ls -la /opt/pulse/data
    ```
    *Erwartet:* `No such file or directory`. ⛔ STOPP, wenn der Ordner schon existiert – dann nicht überschreiben, erst prüfen (ggf. alter Probelauf).
12. 🐧 Freier Speicher und Größe des Checkouts:
    ```bash
    df -h /opt
    ```
    ```bash
    du -sh /opt/pulse/Pulse
    ```
    *Erwartet:* `Avail` mindestens doppelt so groß wie die `du`-Größe (Migration kopiert + sichert). ⛔ STOPP bei zu wenig Platz.
13. 🐧 `.env` auf Werte prüfen, die im neuen Layout stören (zeigt nur Namen, keine Werte):
    ```bash
    grep -o -E '^(PULSE_AI_MODERATION_CASES_FILE|PULSE_SOCIAL_PROMO_FILE|PULSE_GIT_SHA|PULSE_GIT_BRANCH|PULSE_DATA_HOST_DIR)=' .env
    ```
    *Erwartet:* **keine Ausgabe**. ⛔ STOPP bei Treffern: Pfad-Variablen müssen ins Datenverzeichnis zeigen oder entfernt werden, `PULSE_GIT_*` würden den Commit im Systemstatus überdecken. (`PULSE_DB_PATH` in `.env` ist unkritisch – compose.yaml setzt im Container fest `/data/pulse.db`.)

#### C – Neuen Stand holen und Root-Helfer installieren (Pulse läuft weiter)

14. 🐧 Neuesten `main` holen (ändert keine Datei im Checkout):
    ```bash
    git fetch origin main
    ```
15. 🐧 Geprüften Ziel-Commit anzeigen und **notieren**:
    ```bash
    git log -1 --oneline origin/main
    ```
    *Erwartet:* der freigegebene Merge-Commit (Phase 13.1 = `8363ab0` oder ein neuerer `main`-Commit, z. B. der Merge dieser Anleitung). Die vollständige SHA für Schritt 33:
    ```bash
    git rev-parse origin/main
    ```
16. 🐧 Ziel ist ein reiner Fast-Forward:
    ```bash
    git merge-base --is-ancestor HEAD origin/main && echo fast-forward-ok
    ```
    *Erwartet:* `fast-forward-ok`. ⛔ STOPP ohne diese Ausgabe.
17. 🐧 Neuen Boot-Update-Helfer aus dem geprüften Stand holen:
    ```bash
    git show origin/main:scripts/pulse-vps-boot-update.sh > /root/pulse-vps-boot-update.sh
    ```
18. 🐧 Neuen Crash-Restart-Helfer holen:
    ```bash
    git show origin/main:scripts/pulse-crash-restart.sh > /root/pulse-crash-restart.sh
    ```
19. 🐧 Beide sind gültiges Bash und die neue Version (Deploy-Sperre + Migrationsprüfung):
    ```bash
    bash -n /root/pulse-vps-boot-update.sh && bash -n /root/pulse-crash-restart.sh && echo syntax-ok
    ```
    ```bash
    grep -c -e pulse-deploy.lock -e data_dir_ready /root/pulse-vps-boot-update.sh
    ```
    *Erwartet:* `syntax-ok` und eine Zahl **≥ 3**. ⛔ STOPP sonst.
20. 🐧 Installieren (ersetzt nur die beiden Helfer in `/usr/local/sbin`, die systemd-Units bleiben unverändert):
    ```bash
    install -m 755 /root/pulse-vps-boot-update.sh /usr/local/sbin/pulse-vps-boot-update.sh
    ```
    ```bash
    install -m 755 /root/pulse-crash-restart.sh /usr/local/sbin/pulse-crash-restart.sh
    ```
21. 🐧 Die systemd-Units zeigen auf genau diese Dateien:
    ```bash
    systemctl cat pulse-github-update.service | grep ExecStart
    ```
    *Erwartet:* `ExecStart=/usr/local/sbin/pulse-vps-boot-update.sh`.
    ```bash
    systemctl is-enabled pulse-crash-restart.timer
    ```
    *Erwartet:* `enabled`. Bei `not-found` ist der Crash-Neustart nicht eingerichtet – dann Schritte 25 und 41 auslassen.
22. 🐧 `flock` ist vorhanden (alle Root-Skripte verweigern ohne Sperre jede Aktion):
    ```bash
    command -v flock
    ```
    *Erwartet:* `/usr/bin/flock`. ⛔ STOPP ohne Ausgabe (`apt-get install -y util-linux`, dann erneut prüfen).
23. 🐧 Die gemeinsame Deploy-Sperre ist frei (kein Deploy läuft gerade):
    ```bash
    flock -n /run/lock/pulse-deploy.lock echo sperre-frei
    ```
    *Erwartet:* `sperre-frei`. ⛔ STOPP ohne Ausgabe – dann läuft ein Deploy/Rollback; warten.
24. 🐧 Docker und Python auf dem Host:
    ```bash
    docker info --format '{{.ServerVersion}}'
    ```
    ```bash
    python3 --version
    ```
    *Erwartet:* eine Docker-Versionsnummer und `Python 3.12.x` (Ubuntu 24.04; reicht für das Migrationsskript). ⛔ STOPP bei Fehlermeldung.

#### D – Pulse stoppen und Daten migrieren

25. 🐧 Crash-Neustart pausieren:
    ```bash
    touch /var/lib/pulse-crash-restart/paused
    ```
    *Erwartet:* keine Ausgabe. Prüfen: `ls /var/lib/pulse-crash-restart/paused` zeigt den Pfad.
26. 🐧 Pulse kontrolliert stoppen (40 s Zeit zum sauberen Beenden, wie `stop_grace_period`):
    ```bash
    docker stop --time 40 pulse-teamos
    ```
    *Erwartet:* `pulse-teamos`. **Ab hier ist Pulse offline.**
27. 🐧 Gestoppt?
    ```bash
    docker inspect -f '{{.State.Status}}' pulse-teamos
    ```
    *Erwartet:* `exited`. ⛔ STOPP bei `running`/`restarting`.
28. 🐧 Migrationsskript aus dem geprüften Stand holen:
    ```bash
    git show origin/main:scripts/pulse_migrate_data.py > /root/pulse_migrate_data.py
    ```
29. 🐧 **Probelauf** (verändert nichts):
    ```bash
    python3 /root/pulse_migrate_data.py --source /opt/pulse/Pulse --target /opt/pulse/data
    ```
    *Erwartet:* `Gefundene Laufzeitdateien: <Anzahl>`, `kopieren: <Anzahl> · bereits identisch: 0 · Konflikte: 0 · Größe: … MB`, eine Liste mit u. a. `pulse.db`, `config.json`, `team_data.json` und am Ende `Probelauf – nichts verändert.`
    ⛔ STOPP bei `Konflikte` > 0, `Zu wenig Speicher`, einer `❌`-Zeile oder wenn `pulse.db`/`config.json`/`team_data.json` fehlen. **Abbruch hier ist folgenlos:** `docker start pulse-teamos` und `rm /var/lib/pulse-crash-restart/paused` – der alte Stand läuft weiter.
30. 🐧 **Migration** (fragt nach dem Bestätigungswort):
    ```bash
    python3 /root/pulse_migrate_data.py --source /opt/pulse/Pulse --target /opt/pulse/data --execute
    ```
    *Erwartet:* `Container pulse-teamos: exited – keine Schreibzugriffe möglich.`, dann die Frage `Zum Starten MIGRIEREN eingeben:` → **`MIGRIEREN`** tippen und Enter. Danach `✅ Backup erstellt und geprüft: /root/pulse-migration-backups/pulse-premigration-….tar.gz` und `✅ Migration abgeschlossen: <Anzahl> Dateien geprüft`.
    ⛔ STOPP bei jeder `❌`-Zeile. Das Skript ist fail-closed (läuft nur bei erreichbarem Docker und nachweislich gestopptem Container), überschreibt nichts und ändert die Quelle nicht. Nach Behebung der Ursache darf derselbe Befehl erneut laufen (identische Dateien werden übersprungen); niemals Dateien in `/opt/pulse/data` von Hand löschen.
31. 🐧 **Gegenprobe:**
    ```bash
    python3 /root/pulse_migrate_data.py --source /opt/pulse/Pulse --target /opt/pulse/data --verify
    ```
    *Erwartet:* `✅ Ziel stimmt mit der Quelle überein.` ⛔ STOPP sonst.
32. 🐧 Markierung und Besitzer:
    ```bash
    grep '"state"' /opt/pulse/data/.pulse-data.json
    ```
    *Erwartet:* `"state": "complete",`.
    ```bash
    ls -ln /opt/pulse/data
    ```
    *Erwartet:* Besitzer `1000 1000` bei allen Einträgen. ⛔ STOPP sonst.

**Abbruch nach Schritt 32, aber vor Schritt 33:** `docker start pulse-teamos` startet den alten Stand mit den (unveränderten) Daten im Checkout. Die Kopie in `/opt/pulse/data` ist dann sofort veraltet: vor einem späteren neuen Versuch den Ordner umbenennen (`mv /opt/pulse/data /opt/pulse/data.verworfen-<Datum>`), nicht löschen, und ab Schritt 25 neu beginnen.

#### E – Phase 13.1 deployen (nur über GitHub Actions)

33. 🪟 **Browser:** GitHub → Repository `NicoPinguinDEV/Pulse` → **Actions → „Deploy Pulse VPS“ → Run workflow** → Branch `main`, Feld `commit_sha`: die vollständige SHA aus Schritt 15 → **Run workflow**. (Falls das Environment `production` eine Freigabe verlangt: freigeben.) **Nicht** über das Server Control Center und **nicht** per `deploy/remote_deploy.sh` aus dem Checkout – beides wäre beim ersten Deploy noch das Phase-12-Skript.
    *Erwartet im Log:* `Backup vor dem Deploy erstellt.` … `Deploy erfolgreich: <SHA> ist gesund.` (einige Minuten, das Image wird gebaut).
    ⛔ STOPP bei:
    - `noch nicht migriert` → Markierung prüfen (Schritt 32); nichts wurde verändert.
    - `KRITISCH: … Layoutwechsel, KEIN automatischer Rollback` → Pulse ist gestoppt, der Code steht auf dem neuen Commit, die Daten liegen unverändert in `/opt/pulse/data`. Weiter mit **„Fehler nach dem Deploy“** unten. Crash-Neustart **nicht** aktivieren.
    - jeder anderen roten Meldung → Log sichern, nicht erneut starten.

#### F – Funktionsprüfung

34. 🐧 Container gesund:
    ```bash
    docker ps --filter name=pulse-teamos --format '{{.Status}}'
    ```
    *Erwartet:* `Up … (healthy)`.
35. 🐧 Healthcheck mit echter Discord-Anmeldung:
    ```bash
    curl -s http://127.0.0.1:25095/healthz
    ```
    *Erwartet:* `"ok":true`, `"bot_ready":true`, `"guild_ready":true`, `"database_ok":true`, `"version":"7.17.0"` und ein Zahlenwert bei `gateway_latency_ms`.
36. 🐧 Richtiger Commit und Datenverzeichnis im Container:
    ```bash
    docker exec pulse-teamos printenv PULSE_GIT_SHA PULSE_DATA_DIR
    ```
    *Erwartet:* die SHA aus Schritt 15 und `/data`.
37. 🐧 Code ist schreibgeschützt:
    ```bash
    docker exec pulse-teamos touch /app/schreibtest
    ```
    *Erwartet:* Fehlermeldung `Read-only file system`.
38. 🐧 Start-Log ohne Fehler:
    ```bash
    docker logs --tail 80 pulse-teamos
    ```
    *Erwartet:* `📦 Cog geladen: …`-Zeilen, `Slash Commands …`, **keine** Zeile `❌ Datenverzeichnis` und keine `❌ Fehler beim Laden`.
39. 🐧 Pulse schreibt ins Datenverzeichnis:
    ```bash
    ls -l /opt/pulse/data/logs/pulse.log
    ```
    *Erwartet:* Uhrzeit von eben.
40. 🪟 **Discord und Dashboard** (nur ansehen, nichts Riskantes auslösen):
    - [ ] Bot ist online; ein reiner Anzeige-Befehl antwortet (z. B. `/teamstatus`).
    - [ ] Dashboard-Login klappt; **Systemstatus** zeigt Commit (Schritt 15), keine roten Datenverzeichnis-/Datenbankmeldungen.
    - [ ] Teamliste und Teamakten enthalten die bisherigen Mitglieder und Verwarnungen.
    - [ ] Bewerbungen, Schicht-Historie und Audit-Log zeigen die bisherigen Einträge.
    - [ ] Tickets: ein altes Transkript lässt sich herunterladen.
    - [ ] Voice-Support: hinterlegte Wartemusik ist vorhanden.
    - [ ] Backups: die alten Backups stehen in der Liste; ein neues Komplett-Backup lässt sich erstellen und verifizieren.
    - [ ] Server Control Center (falls eingerichtet): Container-Tab erreichbar.
    ⛔ STOPP bei fehlenden Daten → **„Fehler nach dem Deploy“**.
41. 🐧 **Erst jetzt** den Crash-Neustart wieder aktivieren:
    ```bash
    rm /var/lib/pulse-crash-restart/paused
    ```
    ```bash
    systemctl list-timers pulse-crash-restart.timer
    ```
    *Erwartet:* der Timer mit einer nächsten Ausführungszeit.

Die alten Dateien im Checkout bleiben als zusätzliche Sicherung liegen und werden von Pulse nicht mehr benutzt. Erst nach einigen Tagen fehlerfreiem Betrieb und nur bewusst von Hand aufräumen (nie `git clean`). Ab jetzt ist das Server Control Center für Deploys wieder nutzbar (der Checkout enthält die neue `remote_deploy.sh`).

#### Fehler nach dem Deploy

- **Pulse gestoppt nach `KRITISCH … Layoutwechsel`:** Ursache in `docker logs --tail 120 pulse-teamos` suchen. Ist sie behebbar (z. B. Discord-Token, Netzwerk), danach in `/opt/pulse/Pulse` (neue SSH-Sitzung: Schritt 7 wiederholen) nacheinander `export COMPOSE_FILE=compose.yaml`, `export PULSE_GIT_SHA=$(git rev-parse HEAD)` und `docker compose up -d` ausführen und ab Schritt 34 prüfen. Sonst den **Rückweg** gehen.
- **Daten fehlen oder sind falsch:** nicht weiterarbeiten lassen (`docker stop --time 40 pulse-teamos`), Crash-Neustart bleibt pausiert, dann Rückweg.

### Phase 13.1: Rückweg zur bisherigen Struktur

Ein Code-Rollback per `deploy/rollback_code.sh` auf einen Commit vor Phase 13 wird abgelehnt (er würde mit den veralteten Daten im Checkout starten). Scheitert der **erste** Deploy ins neue Layout, rollen `remote_deploy.sh` und das Boot-Update **nicht** automatisch zurück, sondern stoppen Pulse (Exit 2) – lieber kurz offline als mit veralteten Daten online. Der Rückweg läuft immer mit dem Werkzeug:

R1. 🐧 Crash-Neustart pausiert lassen bzw. pausieren:
   ```bash
   touch /var/lib/pulse-crash-restart/paused
   ```
R2. 🐧 Pulse stoppen und prüfen:
   ```bash
   docker stop --time 40 pulse-teamos
   ```
   ```bash
   docker inspect -f '{{.State.Status}}' pulse-teamos
   ```
   *Erwartet:* `exited`.
R3. 🐧 Probelauf des Rückwegs (verändert nichts):
   ```bash
   python3 /root/pulse_migrate_data.py --source /opt/pulse/Pulse --target /opt/pulse/data --rollback
   ```
   *Erwartet:* `neu: … · veraltet (werden beiseitegelegt): … · identisch: …` und `Probelauf – nichts verändert.`
R4. 🐧 Rückweg ausführen (fragt nach **`ZURUECK`**):
   ```bash
   python3 /root/pulse_migrate_data.py --source /opt/pulse/Pulse --target /opt/pulse/data --rollback --execute
   ```
   *Erwartet:* zwei `✅ Backup …`-Zeilen (Datenverzeichnis und veraltete Checkout-Kopien), `✅ Rückweg abgeschlossen`. Die veralteten Kopien liegen danach in `/opt/pulse/Pulse/.pulse-legacy-<Zeit>/` (nichts gelöscht); `/opt/pulse/data` ist als `rolled_back` markiert, der neue Stand startet darauf nicht mehr.
R5. 🐧 Alten Code-Stand zurückholen (Phase 12). In einer neuen SSH-Sitzung vorher Schritt 7 wiederholen:
   ```bash
   cd /opt/pulse/Pulse
   ```
   ```bash
   git reset --hard d221b6c
   ```
   *Erwartet:* `HEAD is now at d221b6c …` (ändert nur versionierte Dateien).
R6. 🐧 Alten Stand starten:
   ```bash
   export COMPOSE_FILE=compose.yaml
   ```
   ```bash
   export PULSE_GIT_SHA=$(git rev-parse HEAD)
   ```
   ```bash
   docker compose up -d --build
   ```
R7. 🐧 Prüfen wie Schritt 34/35; danach `rm /var/lib/pulse-crash-restart/paused`.

`/opt/pulse/data`, `.pulse-legacy-*` und `/root/pulse-migration-backups` nicht löschen, bis der alte Stand nachweislich läuft. Der neue Boot-Update-Helfer installiert nach einem Neustart nichts, solange `/opt/pulse/data` nicht `complete` ist. Für einen späteren neuen Versuch `/opt/pulse/data` erst umbenennen (er ist `rolled_back`) und ab Schritt 25 neu migrieren.

### Neustart nach Absturz oder OOM-Kill (optional, empfohlen)

`compose.yaml` bleibt bei `restart: "no"`, damit Docker beim Boot keinen alten Stand vor `pulse-github-update.service` startet. Damit Pulse nach einem Absturz oder OOM-Kill **zur Laufzeit** wieder hochkommt, prüft ein systemd-Timer alle 2 Minuten (frühestens 10 Minuten nach dem Boot) den Container und führt nur `docker start pulse-teamos` aus – kein Git, kein Build. Er greift nie, solange das Boot-Update läuft oder ein Deploy die Sperre `/run/lock/pulse-deploy.lock` hält, ignoriert sauber gestoppte Container (Exit 0) und startet höchstens 3-mal pro Stunde (danach Meldung `KRITISCH` im Journal).

Einmalig als root installieren (das Skript läuft bewusst aus `/usr/local/sbin`, nicht aus dem Checkout, den Deploys per `git reset` ändern; bei der Phase-13.1-Migration geschieht das in Schritt 20):

```bash
install -m 755 scripts/pulse-crash-restart.sh /usr/local/sbin/pulse-crash-restart.sh
install -m 644 systemd/pulse-crash-restart.service systemd/pulse-crash-restart.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pulse-crash-restart.timer
```

- Protokoll: `journalctl -u pulse-crash-restart.service -n 50 --no-pager`
- Pausieren (Wartung): `touch /var/lib/pulse-crash-restart/paused` · fortsetzen: `rm /var/lib/pulse-crash-restart/paused`
- Nach Änderungen am Skript im Repository die `install`-Zeile erneut ausführen.

## 5. GitHub-Updates per Actions ausrollen (optional)

Das Workflow-Template `.github/workflows/deploy-vps.yml` wird erst aktiv deployen, wenn diese GitHub-Repository-Secrets gesetzt sind:

- `PULSE_VPS_HOST`: öffentliche IP oder Hostname des VPS
- `PULSE_VPS_USER`: SSH-Benutzer mit Zugriff auf `/opt/pulse/Pulse` und Docker
- `PULSE_VPS_SSH_KEY`: privater SSH-Schlüssel des Deploy-Benutzers (niemals committen)
- `PULSE_VPS_KNOWN_HOSTS`: verifizierter SSH-Host-Key-Eintrag des VPS
- `PULSE_VPS_SSH_PORT`: optional, Standard `22`

Die Secrets legst du auf GitHub unter **Repository → Settings → Secrets and variables → Actions** an. Richte einen eigenen SSH-Schlüssel für Deployment ein. Verifiziere den Server-Fingerabdruck, bevor du dessen `known_hosts`-Zeile als Secret speicherst. Fehlen notwendige Secrets, schlägt der Deploy-Workflow sichtbar fehl, statt einen erfolgreichen Lauf vorzutäuschen.

**Kein automatisches Produktions-Deployment allein durch grüne CI oder einen Commit.** Standardmäßig deployt der Workflow nur, wenn er unter **Actions → Deploy Pulse VPS → Run workflow** von Hand gestartet wird (optional mit exakter Commit-SHA); auch dann nur für einen `main`-Commit mit erfolgreichem Lauf von „Pulse Quality and Security Checks“. Der Start nach einer grünen CI (`workflow_run`) deployt nur, wenn die Repository-Variable `PULSE_AUTO_DEPLOY` ausdrücklich auf `true` steht (**Settings → Secrets and variables → Actions → Variables**) – das bitte nur bewusst einschalten.

Empfohlene manuelle GitHub-Einstellungen (lassen sich nicht aus dem Repository heraus setzen; Stand **2026-10-29** per API geprüft: `main` hat **keinen** Branch-Schutz und keine Rulesets; das Environment `production` existiert, aber **ohne** Reviewer und **ohne** Branch-Regel; die vier `PULSE_VPS_*`-Secrets liegen als **Repository**-Secrets vor, sind also für Workflows auf jedem Branch lesbar):

- **Settings → Environments → `production`**: **Deployment branches and tags → Selected branches → `main`** setzen und optional **Required reviewers** (z. B. dich selbst) eintragen. Erst die Branch-Regel garantiert, dass ein Workflow von einem anderen Branch (dessen Datei geändert sein könnte) nicht deployen kann; der Workflow bricht einen manuellen Start außerhalb von `main` zusätzlich selbst ab.
- **Secrets in das Environment verschieben:** `PULSE_VPS_HOST`, `PULSE_VPS_USER`, `PULSE_VPS_SSH_KEY`, `PULSE_VPS_KNOWN_HOSTS` (und ggf. `PULSE_VPS_SSH_PORT`, `PULSE_DEPLOY_WEBHOOK_URL`) unter **Environments → production → Environment secrets** neu anlegen und danach die gleichnamigen Repository-Secrets löschen. Der Deploy-Job nutzt bereits `environment: production`, am Workflow ändert sich nichts.
- Optional **Settings → Actions → General → Require actions to be pinned to a full-length commit SHA**: Drittanbieter-Actions sind bereits gepinnt (Test `test_third_party_actions_are_pinned_to_a_commit`); die Einstellung würde auch `actions/*`-Tags verbieten und Dependabot-PRs nötig machen.
- **Settings → Branches → Branch protection rule für `main`** (oder ein Ruleset): Pull Request vor dem Merge verlangen, Force-Push und Löschen verbieten, erforderliche Statuschecks: `syntax`, `Repository auf offengelegte Geheimnisse prüfen`, `Abhängigkeiten auf Sicherheitslücken prüfen`, `E2E nötig? (Pfadfilter)` und `Dashboard-E2E (Playwright, lokal mit Fake-Login)` (wird bei reinen Doku-Änderungen übersprungen; „übersprungen“ gilt bei GitHub als erfüllt) sowie `Docker-Image bauen (wie auf dem VPS, ohne Push)` und `Lock-Datei (Hashes) + pip-audit`.
- Ob eine Regel aktiv ist, nur in den GitHub-Einstellungen bzw. mit `gh api repos/NicoPinguinDEV/Pulse/branches/main/protection` prüfen – nicht annehmen.

Vor dem ersten Deployment über Actions muss die obige manuelle Einrichtung inklusive Datenübernahme und mindestens eines erfolgreichen Teststarts erledigt sein. Der Workflow setzt den Git-Checkout auf den geprüften `main`-Commit, baut das Image (`docker compose build`, bei Fehler zurück auf den laufenden Commit) und startet es (`docker compose up -d`, bei ungesundem `/healthz` automatischer Rollback – **außer** beim Wechsel ins Datenverzeichnis-Layout: dann wird Pulse gestoppt statt mit veralteten Daten zurückgerollt). Vor dem ersten Phase-13.1-Deploy: Migration nach der Schritt-für-Schritt-Anleitung oben. Er löscht keine unversionierten Laufzeitdateien; trotzdem ist ein aktuelles Backup vor Updates Pflicht.

## 6. Nach dem Umzug

- Bot online, Slash-Commands, Tickets, Bewerbungen, Datenbankinhalte und Dashboard testen.
- Wenn du eine Domain oder IP geändert hast, die Redirect-URI im Discord Developer Portal sowie `PUBLIC_BASE_URL` und `DISCORD_REDIRECT_URI` aktualisieren.
- Erst nach erfolgreichem Funktionstest das alte Hosting kündigen.
