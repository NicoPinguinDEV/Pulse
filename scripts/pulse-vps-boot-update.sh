#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="/opt/pulse/Pulse"
SERVICE_NAME="pulse"
UPDATE_HELPER="/usr/local/sbin/pulse-vps-boot-update.sh"
HEALTH_URL="http://127.0.0.1:25095/healthz"
BACKUP_ROOT="/root/pulse-auto-deploy-backups"
# Phase 13: runtime data directory (mounted as /data); same lookup as deploy/remote_deploy.sh.
DATA_HOST_DIR="${PULSE_DATA_HOST_DIR:-}"
PULSE_UID="${PULSE_UID:-1000}"
# Only compose.yaml from git – never a compose.override.yaml or a COMPOSE_FILE from .env, which the container
# (bind mount) could create to start a privileged container through this root script.
export COMPOSE_FILE=compose.yaml

log() {
    printf '[pulse-github-update] %s\n' "$*"
}

wait_for_health() {
    local response attempt
    for attempt in $(seq 1 36); do
        response="$(curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null || true)"
        if printf '%s' "$response" | grep -q '"ok":true' \
            && printf '%s' "$response" | grep -q '"bot_ready":true' \
            && printf '%s' "$response" | grep -q '"guild_ready":true' \
            && printf '%s' "$response" | grep -q '"database_ok":true'; then
            log "Healthcheck erfolgreich: $response"
            return 0
        fi
        sleep 5
    done
    log "Healthcheck nach 180 Sekunden nicht erfolgreich. Letzte Antwort: $response"
    docker compose ps || true
    docker logs --tail 100 pulse-teamos || true
    return 1
}

start_current_version() {
    log "Starte den derzeit ausgecheckten Stand, ohne lokale Änderungen zu überschreiben."
    if uses_data_dir HEAD; then
        prepare_mounts
    fi
    if ! docker compose up -d; then
        log "WARNUNG: Der vorhandene Pulse-Stand konnte nicht gestartet werden."
        return 1
    fi
    wait_for_health
}

backup_runtime_data() {
    local stamp backup file
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup="$BACKUP_ROOT/$stamp"
    install -d -m 700 "$backup"
    shopt -s nullglob
    for file in ./*.json ./.env ./*.db ./*.sqlite ./*.sqlite3 ./*.db-shm ./*.db-wal; do
        if [[ -f "$file" ]]; then
            if ! cp -p -- "$file" "$backup/$(basename "$file")"; then
                log "FEHLER: Sicherung fehlgeschlagen für $file"
                return 1
            fi
        fi
    done
    if [[ -f assets/support_wait.mp3 ]]; then
        if ! cp -p -- assets/support_wait.mp3 "$backup/support_wait.mp3"; then
            log "FEHLER: Wartemusik konnte nicht gesichert werden."
            return 1
        fi
    fi
    # Phase 13: the live data lives in the data directory – back up its databases, JSON stores and waiting music too.
    if [[ -d "$DATA_HOST_DIR" ]]; then
        install -d -m 700 "$backup/data"
        for file in "$DATA_HOST_DIR"/*.json "$DATA_HOST_DIR"/.pulse-data.json "$DATA_HOST_DIR"/*.db "$DATA_HOST_DIR"/*.sqlite \
                    "$DATA_HOST_DIR"/*.sqlite3 "$DATA_HOST_DIR"/*.db-shm "$DATA_HOST_DIR"/*.db-wal "$DATA_HOST_DIR"/assets/support_wait.*; do
            if [[ -f "$file" ]]; then
                if ! cp -p -- "$file" "$backup/data/$(basename "$file")"; then
                    log "FEHLER: Sicherung fehlgeschlagen für $file"
                    return 1
                fi
            fi
        done
    fi
    log "Runtime-Daten und lokale Wartemusik gesichert: $backup"
}

install_updated_helper() {
    if [[ -f scripts/pulse-vps-boot-update.sh ]]; then
        if install -m 755 scripts/pulse-vps-boot-update.sh "$UPDATE_HELPER"; then
            log "Update-Helfer für den nächsten Start aktualisiert."
        else
            log "WARNUNG: Update-Helfer unter $UPDATE_HELPER konnte nicht erneuert werden."
        fi
    fi
}

cd "$REPO_DIR"
if [[ -z "$DATA_HOST_DIR" ]]; then
    DATA_HOST_DIR="$( { sed -n 's/^PULSE_DATA_HOST_DIR=//p' .env 2>/dev/null || true; } | tail -n 1 | tr -d '\r"')"
fi
DATA_HOST_DIR="${DATA_HOST_DIR:-/opt/pulse/data}"
uses_data_dir() { git show "$1:compose.yaml" 2>/dev/null | grep -q 'target: /data'; }
data_dir_ready() { [[ -f "$DATA_HOST_DIR/.pulse-data.json" ]] && grep -Eq '"state": "(complete|initialized)"' "$DATA_HOST_DIR/.pulse-data.json"; }
prepare_mounts() {
    # The compose file mounts the socket folder of the host agent; create it for the container user if missing.
    [[ -d .pulse-agent ]] || install -d -o "$PULSE_UID" -g "$PULSE_UID" -m 750 .pulse-agent || true
}
# Root runs this script, but the checkout belongs to the container user (UID 1000, O-01). Without this, git
# refuses every command ("dubious ownership"), set -e ends the script before start_current_version and Pulse
# stays down after a reboot (same line as deploy/remote_deploy.sh; valid only for this script's git calls).
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$(pwd -P)"
# Phase 13.1: an image built while starting the current version must carry its commit too (system status).
export PULSE_GIT_SHA="$(git rev-parse HEAD 2>/dev/null || true)" PULSE_GIT_BRANCH=main
# Phase 13.1: same lock as remote_deploy.sh/rollback_code.sh – a deploy started over SSH right after a reboot must
# not build and start in parallel.
#  * lock held by someone else (deploy/rollback running): do not touch the container at all – that run owns it.
#  * flock missing: every other root script refuses to run without it, so nothing can run in parallel; only the
#    installed version is started (no update), otherwise Pulse would stay down after every reboot.
LOCK_FILE="${PULSE_DEPLOY_LOCK:-/run/lock/pulse-deploy.lock}"
if ! command -v flock >/dev/null 2>&1; then
    log "WARNUNG: flock fehlt – kein Update; starte nur den installierten Stand (Deploys sind ohne flock gesperrt)."
    start_current_version || true
    exit 1
fi
if ! exec 9>"$LOCK_FILE" || ! flock -w 600 9; then
    log "ABBRUCH: Deploy-Sperre $LOCK_FILE seit 600 s belegt – ein Deploy/Rollback läuft; Container bleibt unberührt."
    exit 1
fi

current_branch="$(git branch --show-current)"
if [[ -n "$current_branch" && "$current_branch" != "main" ]]; then
    log "ABBRUCH: Checkout liegt auf Branch '$current_branch', erwartet wird main oder ein sicherer detached HEAD."
    start_current_version || true
    exit 1
fi

status="$(git status --porcelain)"
unexpected="$(printf '%s\n' "$status" | grep -v '^?? assets/support_wait\.mp3$' || true)"
if [[ -n "$unexpected" ]]; then
    log "ABBRUCH: Unerwartete lokale Änderungen; sie werden nicht überschrieben."
    git status --short
    start_current_version || true
    exit 1
fi

if ! git fetch origin main; then
    log "WARNUNG: GitHub ist nicht erreichbar; starte den vorhandenen VPS-Stand."
    start_current_version || true
    exit 1
fi

target="$(git rev-parse origin/main)"
previous="$(git rev-parse HEAD)"

if ! git merge-base --is-ancestor HEAD "$target"; then
    log "ABBRUCH: GitHub main ist kein sicherer Fast-Forward vom installierten Stand."
    git log -1 --oneline HEAD || true
    git log -1 --oneline "$target" || true
    start_current_version || true
    exit 1
fi

if [[ "$previous" == "$target" ]]; then
    log "GitHub main ist bereits aktuell ($target)."
    install_updated_helper
    if start_current_version; then
        docker exec -w /app pulse-teamos python -c 'import pulse_version; print("Pulse-Version:", pulse_version.PULSE_VERSION)' || true
        exit 0
    fi
    exit 1
fi

log "Neuer GitHub-Stand erkannt: $previous -> $target"
# Phase 13: an update to the data-directory layout needs the separate, confirmed migration first.
if uses_data_dir "$target" && ! data_dir_ready; then
    log "ABBRUCH: $target nutzt das Datenverzeichnis $DATA_HOST_DIR, das noch nicht migriert ist (VPS_DEPLOYMENT.md, Phase 13)."
    start_current_version || true
    exit 1
fi
docker compose stop "$SERVICE_NAME" || true
if ! backup_runtime_data; then
    log "ABBRUCH: Vor dem Update konnte keine vollständige Sicherung erstellt werden."
    start_current_version || true
    exit 1
fi

if ! git merge --ff-only "$target"; then
    log "FEHLER: Fast-Forward fehlgeschlagen; der laufende Stand wird wieder gestartet."
    start_current_version || true
    exit 1
fi

install_updated_helper
export PULSE_GIT_SHA="$target" PULSE_GIT_BRANCH=main
if uses_data_dir "$target"; then
    prepare_mounts
fi
if docker compose up -d --build; then
    if wait_for_health; then
        log "Update erfolgreich."
        git log -1 --format='Installierter Commit: %h %s'
        docker exec -w /app pulse-teamos python -c 'import pulse_version; print("Pulse-Version:", pulse_version.PULSE_VERSION)' || true
        exit 0
    fi
else
    log "Build oder Containerstart fehlgeschlagen."
fi

# Phase 13.1: never start the old layout on outdated checkout data after the switch to the data directory.
if uses_data_dir "$target" && ! uses_data_dir "$previous"; then
    log "KRITISCH: Update auf das Datenverzeichnis-Layout nicht gesund – KEIN automatischer Rückfall auf $previous."
    docker compose stop "$SERVICE_NAME" || true
    log "Pulse ist gestoppt, Code bleibt auf $target, Daten in $DATA_HOST_DIR. Rückweg nur manuell (VPS_DEPLOYMENT.md)."
    exit 2
fi
log "Update fehlgeschlagen. Stelle den vorherigen Code-Stand $previous wieder her."
git reset --hard "$previous"
install_updated_helper
export PULSE_GIT_SHA="$previous"
docker compose up -d --build || {
    log "KRITISCH: Auch der vorherige Code-Stand konnte nicht gestartet werden."
    docker compose ps
    exit 2
}
if wait_for_health; then
    log "Rollback erfolgreich; Pulse läuft mit dem vorherigen Code-Stand."
else
    log "KRITISCH: Auch nach dem Rollback ist Pulse nicht gesund."
fi
exit 1
