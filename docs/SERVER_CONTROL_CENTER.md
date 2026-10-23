# Server Control Center

**Verwaltung → Serververwaltung → Server Control Center** (`/admin/server`, ab Pulse 7.17.0)

Hier siehst du Live-Werte, Container-Logs und Diagnosen. Von hier aus startest du auch kontrollierte Neustarts, Deployments mit Backup und Rollback sowie verifizierte Sicherungen. Eine freie Shell gibt es nicht.

## 1. Architektur in einem Satz

Der öffentlich erreichbare Webcontainer bekommt **keinen Docker-Socket und keine Shell**. Alles, was den Host betrifft, übernimmt der kleine **Host-Verwaltungsdienst** `deploy/pulse_host_agent.py`. Er läuft als systemd-Dienst auf dem VPS, nur mit Python-Standardbibliothek. Er hat eine feste Aktionsliste und Token-Auth. Standardmäßig ist er nur über einen **Unix-Socket** im Pulse-Ordner erreichbar, also ohne Netzwerkport und ohne Firewall-Änderung. Er nutzt die vorhandenen Werkzeuge:

| Aktion im Dashboard | Was der Host-Dienst ausführt |
|---|---|
| Deploy | `deploy/remote_deploy.sh <sha>`: verifiziertes `predeploy_`-Backup, Ancestor-/Clean-Prüfung, Fast-Forward, `docker compose up -d --build`, `/healthz`, automatischer Rollback. Danach zusätzlich installierter Commit und ein Healthcheck, der 3× in Folge bestehen muss. |
| Rollback (nur Code) | `deploy/rollback_code.sh <sha>`: verifiziertes `prerollback_`-Backup, `git reset --hard` auf einen früheren Commit des installierten Verlaufs, Build, Healthcheck. Ist der alte Stand ungesund, geht es automatisch zurück. |
| Neustart | `docker compose restart pulse`, danach ein wiederholter Healthcheck |
| Healthcheck | `/healthz` 3× in Folge |
| Status/Logs/Git | `docker inspect`/`docker stats` (nur ausgewählte Felder), `docker logs --tail`, `git status/log/diff` (nur lesend), `git fetch origin main` |

Das Boot-Update `scripts/pulse-vps-boot-update.sh` bleibt unverändert zuständig für den Systemstart. Nach einem Code-Rollback installiert der nächste Boot oder Deploy wieder GitHub `main`.

Ohne Host-Dienst funktionieren Übersicht (Werte aus dem eigenen Prozess), Anwendungsprotokoll, Backups und Diagnose. Container-, Git- und Deploy-Werte erscheinen als **„nicht verfügbar“**. Deploy, Neustart und Rollback werden dann abgelehnt und **nie als Erfolg gemeldet**.

## 2. Rechte

- Neues Recht **`can_manage_server`** („Server Control Center“). Serverinhaber und Discord-Administratoren haben es automatisch.
- Alle anderen nur, wenn es ausdrücklich für eine **Teamrolle** oder ein **Dashboard-Profil** gesetzt ist (Verwaltung → Einstellungen & Rechte). Es gilt deny-by-default, und die Leitung erbt es nicht.
- Wiederherstellen, ZIP-Download und Löschen von Backups bleiben wie bisher **Administratoren** vorbehalten (`/backups`).
- Jede Route prüft das Recht serverseitig. Verändernde Routen nehmen nur JSON an, zusätzlich zur Same-Origin-Prüfung. Es gibt Rate-Limits.
- Bestätigungswörter: `DEPLOY`, `NEUSTART`, `ROLLBACK`.
- Alles landet im Audit-Log: wer, was, wann, Ergebnis und Grund, ohne sensible Daten.

## 3. Installation auf dem VPS (einmalig)

> Voraussetzung: Pulse läuft wie in `VPS_DEPLOYMENT.md` unter `/opt/pulse/Pulse` mit Docker Compose. SSH, Firewall und Caddy bleiben unverändert.

1. **Code mit dem Server Control Center installieren.** Das geschieht über den normalen Weg (Merge nach `main` → Deploy oder Boot-Update). Danach existieren `deploy/pulse_host_agent.py`, `deploy/rollback_code.sh` und `systemd/pulse-host-agent.service`.

2. **Benutzer für den Dienst festlegen.** Er muss dem Repository-Ordner gehören, also die Container-UID haben (Standard 1000), und Mitglied der Gruppe `docker` sein.
   ```bash
   stat -c '%u %U' /opt/pulse/Pulse
   getent passwd 1000 || sudo useradd --system --uid 1000 --no-create-home --shell /usr/sbin/nologin pulse
   sudo usermod -aG docker "$(getent passwd 1000 | cut -d: -f1)"
   ```
   Heißt der Benutzer mit UID 1000 nicht `pulse` (z. B. `ubuntu`), trage seinen Namen in Schritt 4 bei `User=` und `Group=` ein.
   *Hinweis:* Mitglied der Gruppe `docker` zu sein, ist faktisch root-gleich. Deshalb ist der Dienst nur lokal per Socket erreichbar, verlangt ein Token und kennt nur feste Aktionen.

3. **Token erzeugen und Konfiguration anlegen.** Das Token nie ins Git, in Chats oder Tickets kopieren.
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   sudo install -m 600 -o root -g root /opt/pulse/Pulse/deploy/pulse-host-agent.env.example /etc/pulse-host-agent.env
   sudo nano /etc/pulse-host-agent.env
   ```
   In `/etc/pulse-host-agent.env` bei `PULSE_AGENT_TOKEN=` das erzeugte Token eintragen. `PULSE_AGENT_SOCKET=/opt/pulse/Pulse/.pulse-agent/agent.sock` so lassen.

4. **systemd-Unit installieren und starten.**
   ```bash
   sudo install -m 644 /opt/pulse/Pulse/systemd/pulse-host-agent.service /etc/systemd/system/
   sudo systemctl edit --full pulse-host-agent   # nur falls User=/Group= angepasst werden müssen
   sudo systemctl daemon-reload
   sudo systemctl enable --now pulse-host-agent
   systemctl status pulse-host-agent --no-pager
   ls -l /opt/pulse/Pulse/.pulse-agent/agent.sock   # srw-rw---- 1000 …
   ```

5. **Pulse verbinden.** In `/opt/pulse/Pulse/.env` (nicht in Git) eintragen:
   ```
   PULSE_HOST_AGENT_URL=unix:///app/.pulse-agent/agent.sock
   PULSE_HOST_AGENT_TOKEN=<dasselbe Token wie oben>
   ```
   Danach Pulse einmal neu starten, damit die `.env` geladen wird:
   ```bash
   cd /opt/pulse/Pulse && docker compose up -d
   ```

6. **Prüfen:** Öffne das Server Control Center. Der gelbe Hinweis „Host-Verwaltungsdienst nicht eingerichtet“ ist verschwunden. Der Tab **Container** zeigt Status und Ressourcen.

**Alternative ohne Socket:** `PULSE_AGENT_BIND=172.30.0.1` / `PULSE_AGENT_PORT=25096` und `PULSE_HOST_AGENT_URL=http://172.30.0.1:25096`. Das ist nur mit privaten IPs erlaubt, öffentliche Adressen und `0.0.0.0` werden abgelehnt. Bei aktiver UFW (Standard aus `deploy/vps_bootstrap.sh`) blockiert die Firewall den Weg vom Container zum Host. Deshalb ist der Socket der empfohlene Weg.

**Logs des Dienstes:** `journalctl -u pulse-host-agent -n 100`. Der Dienst protokolliert keine Tokens oder Abfrageparameter. Zustand und Verlauf der letzten 20 Vorgänge liegen in `/var/lib/pulse-host-agent/state.json`.

**Abschalten:** `sudo systemctl disable --now pulse-host-agent`. Das Dashboard zeigt dann wieder „nicht verfügbar“.

## 4. Bedienung

- **Übersicht:** echte Werte, alle 20 s aktualisiert, solange der Tab sichtbar ist. Fehlende Werte stehen kursiv als „nicht verfügbar“.
  - Der Commit-Vergleich nutzt die öffentliche GitHub-API, ohne Token und 5 min zwischengespeichert. Abschalten mit `PULSE_GITHUB_REPO=` (leer).
- **Live-Konsole:**
  - Quelle automatisch: Container-Logs, wenn der Host-Dienst eingerichtet ist, sonst das Pulse-Anwendungsprotokoll.
  - Pause/Fortsetzen, Auto-Follow aus und frei scrollen. Bei neuen Zeilen erscheint „Neue Zeilen – zum Ende springen“.
  - Filter, Suche, Zeitstempel, Kopieren und Download.
  - Bei Verbindungsabbruch automatischer Reconnect mit Backoff.
  - Befehle: `help status health logs [n] ram disk version backup update restart sync cancel clear`. `backup`, `update`, `restart` und `cancel` öffnen den Bestätigungsdialog.
- **Container:** Status, Health, Neustarts, OOM, Exit-Code, Start-/Laufzeitfehler, CPU/RAM/IO, letzte Vorgänge. Dazu ein kontrollierter Neustart und ein wiederholter Healthcheck.
- **Deployment:** „Mit GitHub abgleichen“ (`git fetch`), dann siehst du:
  - installierter Commit und `origin/main`, Rückstand
  - lokale Änderungen auf dem Server
  - Git-Zustand: sicher oder unsicher, mit Gründen
  - Änderungsvorschau: Commits und Dateien

  Der Deploy-Knopf ist nur aktiv, wenn der Zustand sicher ist und ein Fast-Forward möglich ist. Hat sich GitHub seit der Vorschau geändert, bricht der Deploy ab.
- **Rollback:** setzt den Code auf einen der letzten 15 Commits zurück. **Daten werden nicht zurückgesetzt.** Datenbank-Migrationen der neueren Version bleiben bestehen.
- **Backups:** Erstellen mit anschließender Prüfsummen-Verifikation. Erfolg wird erst danach gemeldet. Jedes Backup lässt sich einzeln „Prüfen“. Wiederherstellen (nur Admin) läuft über die vorhandene Probelauf-Vorschau.
- **Diagnose:** Befunde mit Zeitpunkt, Komponente, Ursache, „bestätigt (gemessen)“ oder „Vermutung“, sowie einer Empfehlung.

## 5. Manuelle Testschritte nach der Installation

1. Melde dich als Teammitglied **ohne** `can_manage_server` an. Der Menüpunkt fehlt, und `/admin/server` sowie `/admin/server/api/overview` antworten mit 403.
2. Melde dich als Administrator an. Die Übersicht zeigt Version, Commit, Bot verbunden, Datenbank erreichbar, RAM (cgroup), Container `running/healthy` und Host-Last.
3. Live-Konsole: Status „verbunden · Container-Logs“. Pause → neue Zeilen werden gezählt. Auto-Follow aus, nach oben scrollen → die Position bleibt. Filter „Nur Fehler“, Suche, Kopieren und Download prüfen.
4. Gib `whoami; cat .env` ein. Erwartet: „Unbekannter oder ungültiger Befehl“ und ein Audit-Eintrag „Konsole abgelehnt“.
5. Gib `ram`, `disk`, `version` und `status` ein. Plausible Werte, keine Tokens.
6. Backups → „Backup erstellen + verifizieren“. Erwartet: Vorgang „erfolgreich … verifiziert“, das Backup steht in der Liste als „verifiziert“.
7. Container → „Healthcheck wiederholen“. Erwartet: 3 OK-Prüfungen.
8. Container → „Kontrolliert neu starten“ und `NEUSTART` eingeben. Das Dashboard ist kurz weg, die Seite verbindet sich selbst neu und zeigt danach „Container neu gestartet und gesund“. Klicke noch während des Laufs ein zweites Mal: erwartet wird die Meldung „läuft bereits“.
9. Deployment → „Mit GitHub abgleichen“. Ist der Stand aktuell, ist der Knopf deaktiviert. Nach einem neuen Commit auf `main` erscheinen Vorschau, Deploy, Fortschritt, „Deploy erfolgreich … installiert und gesund“ und der neue Commit in der Übersicht.
10. Test des unsicheren Zustands: auf dem VPS `touch /opt/pulse/Pulse/probe.txt` ausführen. Deployment zeigt „unsicher – Deploy gesperrt“. Danach `rm /opt/pulse/Pulse/probe.txt`.
11. Rollback (optional, in einem ruhigen Moment) auf den vorherigen Commit, `ROLLBACK` eingeben. Erwartet: „Rollback … erfolgreich und gesund“. Danach wieder per Deploy auf `main`.
12. Diagnose zeigt keine roten Befunde. Im Audit-Log stehen alle Aktionen mit Ergebnis.

## 6. Grenzen / bewusst nicht enthalten

- **Keine freie Terminal-Sitzung.** Sie ist nicht implementiert. Wartung außerhalb der festen Aktionen läuft weiter per SSH.
- Ein laufendes Deploy oder Rollback ist nur **vor Abschluss des Backups** abbrechbar, danach nicht mehr, denn dann wird der Code verändert. Ein Neustart ist nicht abbrechbar.
- Das Dashboard läuft im Pulse-Container. Während Deploy oder Neustart ist es kurz nicht erreichbar. Den Fortschritt verfolgt der Host-Dienst, und die Seite holt den Status nach dem Reconnect nach.
- Der GitHub-Workflow `deploy-vps.yml` beendet sich weiterhin mit einem Hinweis (Status grün), wenn die `PULSE_VPS_*`-Secrets fehlen. Das Server Control Center wertet diesen Workflow nicht aus. Es meldet Erfolg nur nach eigener Prüfung von Commit und Healthcheck.
