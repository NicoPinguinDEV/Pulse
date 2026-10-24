#!/usr/bin/env bash
# O-03: deploy exactly the tested commit on the VPS – backup first, health check after, automatic rollback.
#
#   bash deploy/remote_deploy.sh <commit-sha> [repo-dir]
#
# Runs on the VPS (the GitHub workflow pipes it over SSH). Never deletes runtime data: `git reset --hard`
# only touches tracked files; config/JSON/SQLite/backups/uploads are untracked and git-ignored.
set -euo pipefail

SHA="${1:?Commit-SHA fehlt}"
REPO_DIR="${2:-/opt/pulse/Pulse}"
HEALTH_SECONDS="${PULSE_DEPLOY_HEALTH_SECONDS:-240}"
PULSE_UID="${PULSE_UID:-1000}"

cd "$REPO_DIR"
PREV="$(git rev-parse HEAD)"
echo "Aktuell: $PREV · Ziel: $SHA"

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

# 1) Backup with the running version (format from D-01, verified). A failed backup stops the deploy.
BACKUP_PY='import sys, pulse_backup as b
path, manifest = b.create_full_backup(".", "backups", prefix="predeploy_")
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

# 2) The image runs as an unprivileged user (O-01): the bind-mounted folder must belong to that UID.
if [ "$(stat -c %u .)" != "$PULSE_UID" ]; then
  chown -R "$PULSE_UID:$PULSE_UID" . 2>/dev/null || sudo -n chown -R "$PULSE_UID:$PULSE_UID" . 2>/dev/null \
    || echo "::warning::Ordner gehört nicht UID $PULSE_UID – bitte einmal 'chown -R $PULSE_UID:$PULSE_UID $REPO_DIR' ausführen."
fi

# 3) Exactly the tested commit, then rebuild.
git reset --hard "$SHA"
docker compose up -d --build </dev/null

# 4) /healthz must answer 200 in time – otherwise back to the previous commit.
if wait_healthy; then
  echo "Deploy erfolgreich: $SHA ist gesund."
  docker image prune -f >/dev/null || true
  exit 0
fi

echo "::error::/healthz nicht innerhalb von ${HEALTH_SECONDS}s gesund – Rollback auf $PREV."
docker compose logs --tail 80 pulse </dev/null || true
git reset --hard "$PREV"
docker compose up -d --build </dev/null
if wait_healthy; then
  echo "Rollback erfolgreich: $PREV läuft wieder."
else
  echo "::error::Auch der Rollback ist nicht gesund – bitte sofort prüfen (Backup liegt unter backups/predeploy_*)."
fi
exit 1
