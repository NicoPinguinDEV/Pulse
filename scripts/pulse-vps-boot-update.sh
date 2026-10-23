#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="/opt/pulse/Pulse"
SERVICE_NAME="pulse"
UPDATE_HELPER="/usr/local/sbin/pulse-vps-boot-update.sh"
HEALTH_URL="http://127.0.0.1:25095/healthz"
BACKUP_ROOT="/root/pulse-auto-deploy-backups"

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

log "Update fehlgeschlagen. Stelle den vorherigen Code-Stand $previous wieder her."
git reset --hard "$previous"
install_updated_helper
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
