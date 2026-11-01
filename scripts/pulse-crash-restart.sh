#!/usr/bin/env bash
# Starts Pulse again after a crash or OOM kill at runtime – never at boot, never during a deploy.
#
# compose.yaml keeps `restart: "no"` so Docker cannot start an old checkout ahead of pulse-github-update.service
# on boot. This helper (systemd timer pulse-crash-restart.timer) covers the other case: the container stopped by
# itself while the VPS was running. It only runs `docker start` on the existing container – no git, no build.
#
# Install as root (the repository is writable by the container user, so root never runs it from there):
#   install -m 755 scripts/pulse-crash-restart.sh /usr/local/sbin/pulse-crash-restart.sh
# Pause (e.g. maintenance):  touch /var/lib/pulse-crash-restart/paused   · resume: rm that file
set -Eeuo pipefail

CONTAINER="${PULSE_CONTAINER:-pulse-teamos}"
STATE_DIR="${STATE_DIRECTORY:-/var/lib/pulse-crash-restart}"
LOCK_FILE="${PULSE_DEPLOY_LOCK:-/run/lock/pulse-deploy.lock}"
MAX_PER_HOUR="${PULSE_MAX_AUTO_RESTARTS:-3}"
HISTORY="$STATE_DIR/restarts"

log() {
    printf '[pulse-crash-restart] %s\n' "$*"
}

mkdir -p "$STATE_DIR"
if [[ -e "$STATE_DIR/paused" ]]; then
    log "Pausiert ($STATE_DIR/paused) – nichts zu tun."
    exit 0
fi

# The boot updater fetches main and starts Pulse itself; never race it.
update_state="$(systemctl show -p ActiveState --value pulse-github-update.service 2>/dev/null || true)"
if [[ "$update_state" == "activating" ]]; then
    log "Boot-Update läuft noch – nichts zu tun."
    exit 0
fi

# Same lock as deploy/remote_deploy.sh: a running deploy owns the container. Phase 13.1: fail-closed – without
# flock it cannot be proven that no deploy runs, so nothing is started.
if ! command -v flock >/dev/null 2>&1; then
    log "flock fehlt – ohne Deploy-Sperre kein automatischer Neustart."
    exit 0
fi
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "Ein Deploy läuft gerade – nichts zu tun."
    exit 0
fi

if ! info="$(docker inspect --format '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}' "$CONTAINER" 2>/dev/null)"; then
    log "Container $CONTAINER nicht gefunden – nichts zu tun."
    exit 0
fi
read -r status exit_code oom_killed <<<"$info"
if [[ "$status" != "exited" ]]; then
    exit 0
fi
if [[ "$exit_code" == "0" && "$oom_killed" != "true" ]]; then
    exit 0  # clean stop (manual stop, shutdown): intended, leave it stopped
fi
if [[ "$exit_code" == "78" && "$oom_killed" != "true" ]]; then
    # Phase 13: EX_CONFIG – Pulse refused to start (data directory missing, not migrated or migration aborted).
    # Restarting cannot fix that; a person has to look.
    log "KRITISCH: $CONTAINER verweigert den Start wegen der Datenverzeichnis-Prüfung (Exit 78) – kein Neustart. Logs: docker logs --tail 50 $CONTAINER"
    exit 1
fi

# Crash loop guard: at most MAX_PER_HOUR automatic starts per hour, then a person has to look.
now="$(date +%s)"
recent=()
if [[ -f "$HISTORY" ]]; then
    while read -r stamp; do
        stamp="${stamp%$'\r'}"  # tolerate a file edited on Windows
        if [[ "$stamp" =~ ^[0-9]+$ ]] && (( now - stamp < 3600 )); then
            recent+=("$stamp")
        fi
    done <"$HISTORY"
fi
if (( ${#recent[@]} >= MAX_PER_HOUR )); then
    log "KRITISCH: $CONTAINER ist beendet (Exit $exit_code, OOM $oom_killed), aber schon ${#recent[@]} automatische Starts in der letzten Stunde – kein weiterer Versuch. Bitte Logs prüfen: docker logs --tail 200 $CONTAINER"
    exit 1
fi

log "$CONTAINER ist unerwartet beendet (Exit $exit_code, OOM $oom_killed) – starte ihn neu."
docker logs --tail 40 "$CONTAINER" 2>&1 | sed 's/^/  | /' || true
printf '%s\n' "${recent[@]}" "$now" | grep -E '^[0-9]+$' >"$HISTORY.tmp" || true
mv "$HISTORY.tmp" "$HISTORY"
if docker start "$CONTAINER" >/dev/null; then
    log "$CONTAINER gestartet."
else
    log "FEHLER: docker start $CONTAINER ist fehlgeschlagen."
    exit 1
fi
