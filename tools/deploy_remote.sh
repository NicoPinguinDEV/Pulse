#!/usr/bin/env bash
# Runs on the VPS through SSH stdin. Not executed by local CI.
set -Eeuo pipefail
umask 077

TARGET_SHA="${1:-}"
DEPLOY_DIR="${PULSE_DEPLOY_DIR:-/opt/pulse/Pulse}"

if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "FEHLER: Ziel-Commit ist kein vollständiger SHA."
  exit 2
fi
if [[ ! -d "$DEPLOY_DIR/.git" ]]; then
  echo "FEHLER: Git-Arbeitsverzeichnis nicht gefunden: $DEPLOY_DIR"
  exit 2
fi
cd "$DEPLOY_DIR"
git fetch --no-tags origin "$TARGET_SHA"
git cat-file -e "$TARGET_SHA^{commit}"

PREVIOUS_SHA="$(git rev-parse HEAD)"
APP_PORT="$(sed -n 's/^SERVER_PORT=//p' .env 2>/dev/null | tail -n 1 | tr -d '\r\"' | xargs || true)"
if [[ ! "$APP_PORT" =~ ^[0-9]{1,5}$ ]] || (( APP_PORT < 1 || APP_PORT > 65535 )); then
  APP_PORT=25095
fi

# Backup includes private runtime state, so permissions are restricted.
mkdir -p backups
chmod 700 backups
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="backups/predeploy-${STAMP}-${PREVIOUS_SHA:0:12}.tar.gz"
shopt -s nullglob
candidates=(.env .session_secret *.db *.sqlite *.sqlite3 *.json uploads attachments transcripts)
backup_items=()
for item in "${candidates[@]}"; do
  [[ -e "$item" ]] && backup_items+=("$item")
done
if (( ${#backup_items[@]} == 0 )); then
  echo "FEHLER: Keine Laufzeitdaten für das Deployment-Backup gefunden."
  exit 1
fi
if ! command -v tar >/dev/null || ! command -v df >/dev/null || ! command -v du >/dev/null; then
  echo "FEHLER: tar, df oder du fehlen; Deployment abgebrochen."
  exit 1
fi
SOURCE_KB="$(du -sk "${backup_items[@]}" | awk '{s += $1} END {print s+0}')"
NEEDED_KB=$(( SOURCE_KB + SOURCE_KB / 5 + 65536 ))
FREE_KB="$(df -Pk . | awk 'END {print $4}')"
if [[ ! "$FREE_KB" =~ ^[0-9]+$ ]] || (( FREE_KB < NEEDED_KB )); then
  echo "FEHLER: Zu wenig freier Speicher für das Pre-Deploy-Backup."
  exit 1
fi
if ! tar -czf "$BACKUP_FILE" "${backup_items[@]}"; then
  rm -f "$BACKUP_FILE"
  echo "FEHLER: Pre-Deploy-Backup fehlgeschlagen; Deployment gestoppt."
  exit 1
fi
chmod 600 "$BACKUP_FILE"
if [[ ! -s "$BACKUP_FILE" ]]; then
  echo "FEHLER: Pre-Deploy-Backup ist leer; Deployment gestoppt."
  exit 1
fi
echo "Pre-Deploy-Backup erstellt: $BACKUP_FILE"

if ! command -v curl >/dev/null; then
  echo "FEHLER: curl fehlt für den Health-Check; Deployment gestoppt."
  exit 1
fi

wait_for_health() {
  local attempts="${1:-30}"
  local i
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  return 1
}

rollback() {
  echo "Zielstand ${TARGET_SHA} nicht gesund. Rollback auf ${PREVIOUS_SHA} startet."
  if ! git reset --hard "$PREVIOUS_SHA"; then
    echo "KRITISCH: Git-Rollback fehlgeschlagen. Backup: $BACKUP_FILE"
    return 1
  fi
  if ! docker compose up -d --build; then
    echo "KRITISCH: Alter Stand startet nicht. Backup: $BACKUP_FILE"
    return 1
  fi
  if wait_for_health 30; then
    echo "Rollback erfolgreich; vorheriger Stand antwortet auf /healthz."
    return 0
  fi
  echo "KRITISCH: Rollback ausgeführt, /healthz bleibt fehlerhaft. Backup: $BACKUP_FILE"
  return 1
}

echo "Deploye geprüften Commit $TARGET_SHA (vorher: $PREVIOUS_SHA)."
if ! git reset --hard "$TARGET_SHA"; then
  echo "Git-Wechsel schlug fehl; Rollback wird versucht."
  rollback || true
  exit 1
fi
if ! docker compose up -d --build; then
  echo "Build/Start fehlgeschlagen; Rollback wird versucht."
  rollback || true
  exit 1
fi
if ! wait_for_health 30; then
  echo "/healthz antwortet nicht; Rollback wird versucht."
  rollback || true
  exit 1
fi
echo "Deployment erfolgreich: $TARGET_SHA; Backup: $BACKUP_FILE"
