#!/usr/bin/env bash
# O-03: deploy exactly the tested commit on the VPS – backup first, health check after, automatic rollback.
#
#   bash deploy/remote_deploy.sh <commit-sha> [repo-dir]
#
# Runs on the VPS (the GitHub workflow pipes it over SSH). Never deletes runtime data: `git reset --hard`
# only touches tracked files. Since Phase 13 the runtime data lives outside the checkout in the data directory
# (PULSE_DATA_HOST_DIR, default /opt/pulse/data, mounted as /data); older layouts kept it untracked in the checkout.
set -euo pipefail

SHA="${1:?Commit-SHA fehlt}"
REPO_DIR="${2:-/opt/pulse/Pulse}"
HEALTH_SECONDS="${PULSE_DEPLOY_HEALTH_SECONDS:-240}"
PULSE_UID="${PULSE_UID:-1000}"

cd "$REPO_DIR"
# Only compose.yaml from git – never a compose.override.yaml or a COMPOSE_FILE from .env, which the container
# (bind mount) could create to start a privileged container through this root script.
export COMPOSE_FILE=compose.yaml
# Root runs this script, but the checkout belongs to the container user (UID 1000, O-01). Without this,
# git refuses every command ("dubious ownership"). Valid only for the git calls of this script.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$(pwd -P)"
# Shared with scripts/pulse-crash-restart.sh: while a deploy runs, the crash watchdog leaves the container alone.
LOCK_FILE="${PULSE_DEPLOY_LOCK:-/run/lock/pulse-deploy.lock}"
# Phase 13.1: fail-closed – without the lock two deploys/rollbacks/boot updates could build and start at once.
command -v flock >/dev/null 2>&1 || { echo "Abbruch: flock fehlt (util-linux) – ohne Sperre kein Deploy."; exit 1; }
exec 9>"$LOCK_FILE" || { echo "Abbruch: Sperre $LOCK_FILE nicht anlegbar."; exit 1; }
flock -w 120 9 || { echo "Abbruch: Sperre $LOCK_FILE wird seit 120 s gehalten (anderer Deploy?)."; exit 1; }
PREV="$(git rev-parse HEAD)"
echo "Aktuell: $PREV · Ziel: $SHA"

# Phase 13: same value compose.yaml interpolates (environment first, then .env, then the default).
DATA_HOST_DIR="${PULSE_DATA_HOST_DIR:-$( { sed -n 's/^PULSE_DATA_HOST_DIR=//p' .env 2>/dev/null || true; } | tail -n 1 | tr -d '\r"')}"
DATA_HOST_DIR="${DATA_HOST_DIR:-/opt/pulse/data}"
# A commit uses the data-directory layout when its compose.yaml mounts /data.
uses_data_dir() { git show "$1:compose.yaml" 2>/dev/null | grep -q 'target: /data'; }
data_dir_ready() { [ -f "$DATA_HOST_DIR/.pulse-data.json" ] && grep -Eq '"state": "(complete|initialized)"' "$DATA_HOST_DIR/.pulse-data.json"; }

git fetch --quiet origin main
git cat-file -e "${SHA}^{commit}" || { echo "Commit $SHA ist auf dem VPS nicht vorhanden."; exit 1; }
if [ "$PREV" = "$SHA" ]; then
  echo "Dieser Stand läuft bereits – nichts zu tun."
  exit 0
fi
# Safety checks (from main): only commits on main, never a downgrade, never over local changes on the VPS.
git merge-base --is-ancestor "$SHA" origin/main || { echo "Commit $SHA liegt nicht auf origin/main."; exit 1; }
git merge-base --is-ancestor "$PREV" "$SHA" || { echo "Abbruch: würde den VPS auf einen älteren Commit zurücksetzen."; exit 1; }
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Abbruch: versionierte lokale Änderungen auf dem VPS – bitte erst prüfen."
  exit 1
fi
# Phase 13: never start the new layout on an empty or half-migrated data directory – that would look like a blank
# server. The migration (scripts/pulse_migrate_data.py) is a separate, explicitly confirmed step.
if uses_data_dir "$SHA" && ! data_dir_ready; then
  echo "Abbruch: $SHA nutzt das Datenverzeichnis $DATA_HOST_DIR, das noch nicht migriert ist (.pulse-data.json fehlt"
  echo "oder steht nicht auf complete). Erst die Datenmigration ausführen: VPS_DEPLOYMENT.md, Abschnitt Phase 13."
  exit 1
fi

# 1) Backup with the running version (format from D-01, verified). A failed backup stops the deploy.
# Works in both layouts: the running image tells where its data is (PULSE_DATA_DIR=/data since Phase 13).
BACKUP_PY='import os, sys, pulse_backup as b
base = os.getenv("PULSE_DATA_DIR") or "."
path, manifest = b.create_full_backup(base, os.path.join(base, "backups"), prefix="predeploy_")
problems = b.verify_full_backup(path)
print(path)
sys.exit(1 if problems else 0)'
if docker compose ps --status running --services 2>/dev/null | grep -qx pulse; then
  docker compose exec -T pulse python -c "$BACKUP_PY" </dev/null
else
  docker compose run --rm --no-deps pulse python -c "$BACKUP_PY" </dev/null
fi
echo "Backup vor dem Deploy erstellt."

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

# 2) The image runs as an unprivileged user (O-01): the folders it writes must belong to that UID. Old layout: the
#    bind-mounted checkout (still needed for a rollback to an older commit). Phase 13: the data directory and the
#    socket folder of the host agent – the checkout itself is no longer mounted.
fix_owner() {
  if [ -d "$1" ] && [ "$(stat -c %u "$1")" != "$PULSE_UID" ]; then
    chown -R "$PULSE_UID:$PULSE_UID" "$1" 2>/dev/null || sudo -n chown -R "$PULSE_UID:$PULSE_UID" "$1" 2>/dev/null \
      || echo "::warning::$1 gehört nicht UID $PULSE_UID – bitte einmal 'chown -R $PULSE_UID:$PULSE_UID $1' ausführen."
  fi
}
fix_owner .
# Secrets stay private on the host (compose reads .env as root; the container never sees the file since Phase 13).
if [ -f .env ]; then
  chmod go-rwx .env 2>/dev/null || echo "::warning::.env-Rechte konnten nicht auf 600 gesetzt werden."
fi
if uses_data_dir "$SHA"; then
  fix_owner "$DATA_HOST_DIR"
  [ -d .pulse-agent ] || install -d -o "$PULSE_UID" -g "$PULSE_UID" -m 750 .pulse-agent 2>/dev/null \
    || mkdir -p .pulse-agent || true
fi

# 3) Exactly the tested commit, then rebuild. The build runs while the old container keeps serving; if it fails
#    (network, PyPI, disk), the checkout goes back to the running commit – otherwise the next deploy would see
#    "läuft bereits" and a VPS reboot would start the untested code.
git reset --hard "$SHA"
# Phase 13: the commit shown in the system status is baked into the image (no .git in the container).
export PULSE_GIT_SHA="$SHA" PULSE_GIT_BRANCH=main
if ! docker compose build </dev/null; then
  echo "::error::Image-Build für $SHA fehlgeschlagen – der laufende Stand $PREV bleibt unverändert."
  git reset --hard "$PREV"
  exit 1
fi

# 4) /healthz must answer 200 in time – otherwise back to the previous commit.
if docker compose up -d </dev/null && wait_healthy; then
  echo "Deploy erfolgreich: $SHA ist gesund."
  docker image prune -f >/dev/null || true
  exit 0
fi

docker compose logs --tail 80 pulse </dev/null || true
# Phase 13.1: switching from the old layout to the data directory – the old code would run on the checkout copies,
# which are outdated as soon as the new container has written anything. Never start that automatically: stop
# Pulse, keep the new code checked out (it matches the current data) and leave the decision to a person.
if uses_data_dir "$SHA" && ! uses_data_dir "$PREV"; then
  echo "::error::KRITISCH: /healthz nicht innerhalb von ${HEALTH_SECONDS}s gesund – Layoutwechsel, KEIN automatischer Rollback."
  docker compose stop pulse </dev/null || true
  echo "Pulse ist gestoppt. Aktuelle Daten: $DATA_HOST_DIR (unverändert, nichts gelöscht). Code bleibt auf $SHA."
  echo "Entweder Fehler beheben und Pulse von Hand starten – oder Rückweg: scripts/pulse_migrate_data.py --rollback,"
  echo "danach den alten Stand $PREV starten (VPS_DEPLOYMENT.md, Phase 13 Rückweg)."
  exit 2
fi
echo "::error::/healthz nicht innerhalb von ${HEALTH_SECONDS}s gesund – Rollback auf $PREV."
git reset --hard "$PREV"
export PULSE_GIT_SHA="$PREV"
if docker compose up -d --build </dev/null && wait_healthy; then
  echo "Rollback erfolgreich: $PREV läuft wieder."
else
  echo "::error::Auch der Rollback ist nicht gesund – bitte sofort prüfen (Backup liegt unter backups/predeploy_*)."
fi
exit 1
