# Pulse TeamOS auf einem eigenen VPS betreiben

Diese Anleitung lässt GitHub `NicoPinguinDEV/Pulse` als Codequelle bestehen. Der VPS startet Pulse in Docker mit Python 3.14; Bot und FastAPI-Dashboard laufen wie bisher zusammen auf `SERVER_PORT` (Standard: 25095).

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

Stoppe den alten Pulse-Prozess während des abschließenden Datenexports, damit sich die SQLite-Datenbanken und JSON-Dateien während des Kopierens nicht ändern. Kopiere die gesicherten Laufzeitdaten in `/opt/pulse/Pulse/`. Der Ordner wird in den Container eingebunden und bleibt deshalb auch nach Code-Updates bestehen. **Die alte Instanz erst abschalten, wenn die neue Instanz getestet wurde.**

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

Der Container startet nach einem Serverneustart automatisch. Standardmäßig ist das Dashboard am öffentlichen Serverport 25095 erreichbar, solange die Firewall diesen Port zulässt. Öffne in der Firewall nur benötigte Ports; SSH und Dashboard sollten nach Möglichkeit eingeschränkt bzw. über HTTPS bereitgestellt werden.

### Trennung Container ↔ Host (Code und Laufzeitdaten)

Der Container sieht den Checkout über `./:/app` – Laufzeitdaten (Datenbanken, JSON, `uploads/`, `attachments/`, `transcripts/`, `backups/`, `.env`) liegen unversioniert und git-ignoriert im selben Ordner und überstehen Image-Updates, `git reset --hard` und Rollbacks (sie sind nicht versioniert). Damit ein kompromittierter Pulse-Prozess nichts verändern kann, was **root** auf dem Host ausführt, sind im Container schreibgeschützt: `.git/`, `compose.yaml`, `Dockerfile`, `deploy/`, `scripts/`, `systemd/` (Pulse liest `.git` nur, um den installierten Commit anzuzeigen). Die root-Skripte (`remote_deploy.sh`, `rollback_code.sh`, `pulse-vps-boot-update.sh`) und der Host-Agent setzen `COMPOSE_FILE=compose.yaml`, sodass eine im Ordner angelegte `compose.override.yaml` oder ein `COMPOSE_FILE` in `.env` nie gelesen wird. Wirksam wird das beim nächsten Deploy (der Container wird mit den neuen Mounts neu erstellt); Daten werden dabei nicht verschoben.

Nächster, noch **nicht** umgesetzter Schritt (D-06): eigenes Datenverzeichnis (`PULSE_DATA_DIR`, z. B. `/opt/pulse/data`) und Code nur noch aus dem Image. Das betrifft rund 70 Pfadstellen in 22 Modulen (teils relativ zum Arbeitsverzeichnis) und eine einmalige Datenverschiebung auf dem VPS – erst mit Backup, Probelauf auf einem Testsystem und ausdrücklicher Freigabe.

### Neustart nach Absturz oder OOM-Kill (optional, empfohlen)

`compose.yaml` bleibt bei `restart: "no"`, damit Docker beim Boot keinen alten Stand vor `pulse-github-update.service` startet. Damit Pulse nach einem Absturz oder OOM-Kill **zur Laufzeit** wieder hochkommt, prüft ein systemd-Timer alle 2 Minuten (frühestens 10 Minuten nach dem Boot) den Container und führt nur `docker start pulse-teamos` aus – kein Git, kein Build. Er greift nie, solange das Boot-Update läuft oder ein Deploy die Sperre `/run/lock/pulse-deploy.lock` hält, ignoriert sauber gestoppte Container (Exit 0) und startet höchstens 3-mal pro Stunde (danach Meldung `KRITISCH` im Journal).

Einmalig als root installieren (das Skript wird bewusst nicht aus dem Repository ausgeführt, weil der Container dort schreiben darf):

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

Vor dem ersten Deployment über Actions muss die obige manuelle Einrichtung inklusive Datenübernahme und mindestens eines erfolgreichen Teststarts erledigt sein. Der Workflow setzt den Git-Checkout auf den geprüften `main`-Commit, baut das Image (`docker compose build`, bei Fehler zurück auf den laufenden Commit) und startet es (`docker compose up -d`, bei ungesundem `/healthz` automatischer Rollback). Er löscht keine unversionierten Laufzeitdateien; trotzdem ist ein aktuelles Backup vor Updates Pflicht.

## 6. Nach dem Umzug

- Bot online, Slash-Commands, Tickets, Bewerbungen, Datenbankinhalte und Dashboard testen.
- Wenn du eine Domain oder IP geändert hast, die Redirect-URI im Discord Developer Portal sowie `PUBLIC_BASE_URL` und `DISCORD_REDIRECT_URI` aktualisieren.
- Erst nach erfolgreichem Funktionstest das alte Hosting kündigen.
