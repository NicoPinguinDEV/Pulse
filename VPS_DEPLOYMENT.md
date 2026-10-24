# Pulse TeamOS auf einem eigenen VPS betreiben

Diese Anleitung lässt GitHub `NicoPinguinDEV/Pulse` als Codequelle bestehen. Der VPS startet Pulse in Docker mit Python 3.14; Bot und FastAPI-Dashboard laufen wie bisher zusammen auf `SERVER_PORT` (Standard: 25095).

## Empfohlener Ausgangspunkt

- Ubuntu 24.04 LTS
- Mindestens 4 GB RAM; für Pulse mit Dashboard, Backups und zahlreichen Discord-Mitgliedern werden 8 GB RAM empfohlen.
- Genügend SSD-Speicher für die wachsenden Backups und Transkripte.
- SSH-Zugang zum Server.

**Hinweis für einen bestehenden 2-GiB-VPS:** Dieser liegt unter der empfohlenen Mindestgröße. Für den laufenden Betrieb ist in `compose.yaml` ein Pulse-Limit von 512 MiB RAM plus maximal 256 MiB zusätzlicher Swap vorgesehen, damit genug Speicher für Ubuntu, Docker, Caddy und andere Hostdienste verbleibt. Bei dauerhaft hoher Auslastung sollte der VPS auf mindestens 4 GiB RAM erweitert werden.

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

## 5. GitHub-Updates per Actions ausrollen (optional)

Das Workflow-Template `.github/workflows/deploy-vps.yml` wird erst aktiv deployen, wenn diese GitHub-Repository-Secrets gesetzt sind:

- `PULSE_VPS_HOST`: öffentliche IP oder Hostname des VPS
- `PULSE_VPS_USER`: SSH-Benutzer mit Zugriff auf `/opt/pulse/Pulse` und Docker
- `PULSE_VPS_SSH_KEY`: privater SSH-Schlüssel des Deploy-Benutzers (niemals committen)
- `PULSE_VPS_KNOWN_HOSTS`: verifizierter SSH-Host-Key-Eintrag des VPS
- `PULSE_VPS_SSH_PORT`: optional, Standard `22`

Die Secrets legst du auf GitHub unter **Repository → Settings → Secrets and variables → Actions** an. Richte einen eigenen SSH-Schlüssel für Deployment ein. Verifiziere den Server-Fingerabdruck, bevor du dessen `known_hosts`-Zeile als Secret speicherst. Fehlen notwendige Secrets, schlägt der Deploy-Workflow sichtbar fehl, statt einen erfolgreichen Lauf vorzutäuschen.

**Kein automatisches Produktions-Deployment allein durch grüne CI oder einen Commit.** Standardmäßig deployt der Workflow nur, wenn er unter **Actions → Deploy Pulse VPS → Run workflow** von Hand gestartet wird (optional mit exakter Commit-SHA); auch dann nur für einen `main`-Commit mit erfolgreichem Lauf von „Pulse Quality and Security Checks“. Der Start nach einer grünen CI (`workflow_run`) deployt nur, wenn die Repository-Variable `PULSE_AUTO_DEPLOY` ausdrücklich auf `true` steht (**Settings → Secrets and variables → Actions → Variables**) – das bitte nur bewusst einschalten.

Empfohlene manuelle GitHub-Einstellungen (lassen sich nicht aus dem Repository heraus setzen; Stand 2026-10-24 geprüft: `main` hat **keinen** Branch-Schutz, keine Rulesets, kein Environment):

- **Settings → Environments → `production`** anlegen und **Required reviewers** (z. B. dich selbst) eintragen. Der Deploy-Job nutzt dieses Environment und wartet dann vor jedem Deployment auf die Freigabe.
- **Settings → Branches → Branch protection rule für `main`** (oder ein Ruleset): Pull Request vor dem Merge verlangen, Force-Push und Löschen verbieten, erforderliche Statuschecks: `syntax`, `Repository auf offengelegte Geheimnisse prüfen`, `Abhängigkeiten auf Sicherheitslücken prüfen`, `E2E nötig? (Pfadfilter)` und `Dashboard-E2E (Playwright, lokal mit Fake-Login)` (wird bei reinen Doku-Änderungen übersprungen; „übersprungen“ gilt bei GitHub als erfüllt) sowie `Lock-Datei (Hashes) + pip-audit`.
- Ob eine Regel aktiv ist, nur in den GitHub-Einstellungen bzw. mit `gh api repos/NicoPinguinDEV/Pulse/branches/main/protection` prüfen – nicht annehmen.

Vor dem ersten Deployment über Actions muss die obige manuelle Einrichtung inklusive Datenübernahme und mindestens eines erfolgreichen Teststarts erledigt sein. Der Workflow aktualisiert den Git-Checkout auf `main` und führt `docker compose up -d --build` aus. Er löscht keine unversionierten Laufzeitdateien; trotzdem ist ein aktuelles Backup vor Updates Pflicht.

## 6. Nach dem Umzug

- Bot online, Slash-Commands, Tickets, Bewerbungen, Datenbankinhalte und Dashboard testen.
- Wenn du eine Domain oder IP geändert hast, die Redirect-URI im Discord Developer Portal sowie `PUBLIC_BASE_URL` und `DISCORD_REDIRECT_URI` aktualisieren.
- Erst nach erfolgreichem Funktionstest das alte Hosting kündigen.
