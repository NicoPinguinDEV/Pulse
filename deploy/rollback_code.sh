#!/usr/bin/env bash
# Server Control Center: roll the *code* back to an earlier commit of the installed history.
#
#   bash deploy/rollback_code.sh <commit-sha> [repo-dir]
#
# Started only by the Pulse host agent (deploy/pulse_host_agent.py) after it validated the target
# (40 hex characters, an earlier first-parent commit of HEAD). Same safety rules as remote_deploy.sh:
# verified backup first, never over local changes, only tracked files change – the runtime data (since Phase 13 in
# the data directory, before that untracked in the checkout) stays untouched. A rollback from the data-directory
# layout to a commit with the old layout is refused: it would run on stale data in the checkout (manual way:
# VPS_DEPLOYMENT.md, Phase 13 "Rückweg"). If the older code is not healthy, the previous code is restored.
set -euo pipefail

SHA="${1:?Commit-SHA fehlt}"
REPO_DIR="${2:-/opt/pulse/Pulse}"
HEALTH_SECONDS="${PULSE_DEPLOY_HEALTH_SECONDS:-240}"

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Ungültige Commit-SHA."; exit 1; }
cd "$REPO_DIR"
# Only compose.yaml from git – never a compose.override.yaml or a COMPOSE_FILE from .env, which the container
# (bind mount) could create to start a privileged container through this root script.
export COMPOSE_FILE=compose.yaml
# Root runs this script, but the checkout belongs to the container user (UID 1000, O-01). Without this,
# git refuses every command ("dubious ownership"). Valid only for the git calls of this script.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$(pwd -P)"
# Phase 13.1: same lock as remote_deploy.sh, the boot update and the crash watchdog – never two at once.
LOCK_FILE="${PULSE_DEPLOY_LOCK:-/run/lock/pulse-deploy.lock}"
command -v flock >/dev/null 2>&1 || { echo "Abbruch: flock fehlt (util-linux) – ohne Sperre kein Rollback."; exit 1; }
exec 9>"$LOCK_FILE" || { echo "Abbruch: Sperre $LOCK_FILE nicht anlegbar."; exit 1; }
flock -w 120 9 || { echo "Abbruch: Sperre $LOCK_FILE wird seit 120 s gehalten (Deploy läuft?)."; exit 1; }
PREV="$(git rev-parse HEAD)"
echo "Aktuell: $PREV · Rollback-Ziel: $SHA"

git cat-file -e "${SHA}^{commit}" || { echo "Commit $SHA ist auf dem VPS nicht vorhanden."; exit 1; }
if [ "$PREV" = "$SHA" ]; then
  echo "Dieser Stand läuft bereits – nichts zu tun."
  exit 0
fi
git merge-base --is-ancestor "$SHA" "$PREV" || { echo "Abbruch: Ziel ist kein früherer Commit des installierten Stands."; exit 1; }
uses_data_dir() { git show "$1:compose.yaml" 2>/dev/null | grep -q 'target: /data'; }
if uses_data_dir "$PREV" && ! uses_data_dir "$SHA"; then
  echo "Abbruch: $SHA nutzt noch das alte Layout (Daten im Checkout) – ein Code-Rollback dorthin würde mit veralteten"
  echo "Daten starten. Rückweg nur manuell nach VPS_DEPLOYMENT.md (Phase 13, Rückweg)."
  exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Abbruch: versionierte lokale Änderungen auf dem VPS – bitte erst prüfen."
  exit 1
fi

# 1) Verified backup with the running version (same format as before a deploy). A failed backup stops here.
BACKUP_PY='import os, sys, pulse_backup as b
base = os.getenv("PULSE_DATA_DIR") or "."
path, manifest = b.create_full_backup(base, os.path.join(base, "backups"), prefix="prerollback_")
problems = b.verify_full_backup(path)
print(path)
sys.exit(1 if problems else 0)'
if docker compose ps --status running --services 2>/dev/null | grep -qx pulse; then
  docker compose exec -T pulse python -c "$BACKUP_PY" </dev/null
else
  docker compose run --rm --no-deps pulse python -c "$BACKUP_PY" </dev/null
fi
echo "Backup vor dem Rollback erstellt."

health_ok() {
  docker compose exec -T pulse </dev/null python -c "import os, sys, urllib.request
p = os.getenv('SERVER_PORT', '25095')
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=8).status == 200 else 1)" >/dev/null 2>&1
}

wait_healthy() {
  local waited=0
  while [ "$waited" -lt "$HEALTH_SECONDS" ]; do
    if health_ok; then
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  return 1
}

# 2) Older code, rebuild, health check (the commit is baked into the image for the system status).
git reset --hard "$SHA"
export PULSE_GIT_SHA="$SHA"
if docker compose up -d --build </dev/null && wait_healthy; then
  echo "Rollback erfolgreich: $SHA ist gesund."
  exit 0
fi

echo "::error::Rollback-Stand $SHA nicht innerhalb von ${HEALTH_SECONDS}s gesund – zurück auf $PREV."
docker compose logs --tail 80 pulse </dev/null || true
git reset --hard "$PREV"
export PULSE_GIT_SHA="$PREV"
docker compose up -d --build </dev/null || true
if wait_healthy; then
  echo "Vorheriger Stand wiederhergestellt: $PREV ist gesund."
else
  echo "::error::KRITISCH: Auch der vorherige Stand ist nicht gesund – bitte sofort prüfen (Backup unter backups/pulse_full_prerollback_*)."
fi
exit 1
