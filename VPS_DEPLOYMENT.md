# Pulse TeamOS auf einem eigenen VPS betreiben

Diese Anleitung lässt GitHub `NicoPinguinDEV/Pulse` als Codequelle bestehen. Der VPS startet Pulse in Docker mit Python 3.14; Bot und FastAPI-Dashboard laufen wie bisher zusammen auf `SERVER_PORT` (Standard: 25095).

## Empfohlener Ausgangspunkt

- Ubuntu 24.04 LTS
- Mindestens 4 GB RAM; für Pulse mit Dashboard, Backups und zahlreichen Discord-Mitgliedern werden 8 GB RAM empfohlen.
- Genügend SSD-Speicher für die wachsenden Backups und Transkripte.
- SSH-Zugang zum Server.

**Hinweis für einen bestehenden 2-GiB-VPS:** Dieser liegt unter der empfohlenen Mindestgröße. Für den laufenden Betrieb ist in `compose.yaml` ein Pulse-Limit von 512 MiB RAM plus maximal 256 MiB zusätzlicher Swap vorgesehen, damit genug Speicher für Ubuntu, Docker, Caddy und andere Hostdienste verbleibt. Bei dauerhaft hoher Auslastung sollte der VPS auf mindestens 4 GiB RAM erweitert werden.

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

## 5. GitHub-Updates automatisch ausrollen (optional)

Das Workflow-Template `.github/workflows/deploy-vps.yml` wird erst aktiv deployen, wenn diese GitHub-Repository-Secrets gesetzt sind:

- `PULSE_VPS_HOST`: öffentliche IP oder Hostname des VPS
- `PULSE_VPS_USER`: SSH-Benutzer mit Zugriff auf `/opt/pulse/Pulse` und Docker
- `PULSE_VPS_SSH_KEY`: privater SSH-Schlüssel des Deploy-Benutzers (niemals committen)
- `PULSE_VPS_KNOWN_HOSTS`: verifizierter SSH-Host-Key-Eintrag des VPS
- `PULSE_VPS_SSH_PORT`: optional, Standard `22`

Die Secrets legst du auf GitHub unter **Repository → Settings → Secrets and variables → Actions** an. Richte einen eigenen SSH-Schlüssel für Deployment ein. Verifiziere den Server-Fingerabdruck, bevor du dessen `known_hosts`-Zeile als Secret speicherst. Das Workflow-Template startet nur nach erfolgreichem Syntax- und Regressionstest auf einem Push nach `main`; ohne die notwendigen Secrets überspringt es das Deployment.

Vor dem ersten automatischen Deployment muss die obige manuelle Einrichtung inklusive Datenübernahme und mindestens eines erfolgreichen Teststarts erledigt sein. Der Workflow aktualisiert den Git-Checkout auf `main` und führt `docker compose up -d --build` aus. Er löscht keine unversionierten Laufzeitdateien; trotzdem ist ein aktuelles Backup vor Updates Pflicht.

## 6. Nach dem Umzug

- Bot online, Slash-Commands, Tickets, Bewerbungen, Datenbankinhalte und Dashboard testen.
- Wenn du eine Domain oder IP geändert hast, die Redirect-URI im Discord Developer Portal sowie `PUBLIC_BASE_URL` und `DISCORD_REDIRECT_URI` aktualisieren.
- Erst nach erfolgreichem Funktionstest das alte Hosting kündigen.
