#!/usr/bin/env bash
# Server Control Center: roll the *code* back to an earlier commit of the installed history.
#
#   bash deploy/rollback_code.sh <commit-sha> [repo-dir]
#
# Started only by the Pulse host agent (deploy/pulse_host_agent.py) after it validated the target
# (40 hex characters, an earlier first-parent commit of HEAD). Same safety rules as remote_deploy.sh:
# verified backup first, never over local changes, only tracked files change – config/JSON/SQLite/
# backups/uploads are untracked and git-ignored and stay untouched. If the older code is not healthy,
# the previous code is restored and checked again.
set -euo pipefail

SHA="${1:?Commit-SHA fehlt}"
REPO_DIR="${2:-/opt/pulse/Pulse}"
HEALTH_SECONDS="${PULSE_DEPLOY_HEALTH_SECONDS:-240}"

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Ungültige Commit-SHA."; exit 1; }
cd "$REPO_DIR"
PREV="$(git rev-parse HEAD)"
echo "Aktuell: $PREV · Rollback-Ziel: $SHA"

git cat-file -e "${SHA}^{commit}" || { echo "Commit $SHA ist auf dem VPS nicht vorhanden."; exit 1; }
if [ "$PREV" = "$SHA" ]; then
  echo "Dieser Stand läuft bereits – nichts zu tun."
  exit 0
fi
git merge-base --is-ancestor "$SHA" "$PREV" || { echo "Abbruch: Ziel ist kein früherer Commit des installierten Stands."; exit 1; }
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Abbruch: versionierte lokale Änderungen auf dem VPS – bitte erst prüfen."
  exit 1
fi

# 1) Verified backup with the running version (same format as before a deploy). A failed backup stops here.
BACKUP_PY='import sys, pulse_backup as b
path, manifest = b.create_full_backup(".", "backups", prefix="prerollback_")
problems = b.verify_full_backup(path)
print(path)
sys.exit(1 if problems else 0)'
if docker compose ps --status running --services 2>/dev/null | grep -qx pulse; then
  docker compose exec -T pulse python -c "$BACKUP_PY"
else
  docker compose run --rm --no-deps pulse python -c "$BACKUP_PY"
fi
echo "Backup vor dem Rollback erstellt."

health_ok() {
  docker compose exec -T pulse python -c "import os, sys, urllib.request
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

# 2) Older code, rebuild, health check.
git reset --hard "$SHA"
if docker compose up -d --build && wait_healthy; then
  echo "Rollback erfolgreich: $SHA ist gesund."
  exit 0
fi

echo "::error::Rollback-Stand $SHA nicht innerhalb von ${HEALTH_SECONDS}s gesund – zurück auf $PREV."
docker compose logs --tail 80 pulse || true
git reset --hard "$PREV"
docker compose up -d --build || true
if wait_healthy; then
  echo "Vorheriger Stand wiederhergestellt: $PREV ist gesund."
else
  echo "::error::KRITISCH: Auch der vorherige Stand ist nicht gesund – bitte sofort prüfen (Backup unter backups/pulse_full_prerollback_*)."
fi
exit 1
