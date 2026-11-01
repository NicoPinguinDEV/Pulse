#!/usr/bin/env bash
# Phase 13.1: Docker Compose integration test of the production container layout (GitHub Actions, Linux + Docker).
#
#   bash tests/compose_integration.sh
#
# Uses the real compose.yaml (read_only, /data bind mount with create_host_path: false, tmpfs /tmp, .pulse-agent,
# limits, healthcheck, stop_grace_period) – plus tests/compose.ci.yaml, which only swaps the command for
# tests/compose_probe.py (no Discord connection). Only isolated temp data; never a real .env, never `down -v`.
set -euo pipefail
cd "$(dirname "$0")/.."

WORK="${RUNNER_TEMP:-/tmp}/pulse-compose-it-$$"
mkdir -p "$WORK"
export COMPOSE_PROJECT_NAME=pulseci
export PULSE_GIT_SHA="${PULSE_GIT_SHA:-$(git rev-parse HEAD)}" PULSE_GIT_BRANCH=ci
BASE=compose.yaml
TEST="compose.yaml:tests/compose.ci.yaml"
CONTAINER=pulse-teamos
PORT="${SERVER_PORT:-25095}"
if [ ! -e .env ]; then
  : > .env  # compose requires the env_file; the CI checkout has none and gets an empty one (no secrets)
  CREATED_ENV=1
fi

step() { printf '\n== %s\n' "$*"; }
fail() {
  echo "::error::$*"
  docker logs --tail 120 "$CONTAINER" 2>&1 || true
  exit 1
}
cleanup() {
  COMPOSE_FILE="$TEST" PULSE_DATA_HOST_DIR="$WORK/ok" docker compose down --remove-orphans >/dev/null 2>&1 || true
  if [ "${CREATED_ENV:-0}" = 1 ]; then rm -f .env; fi
}
trap cleanup EXIT

marker() {  # marker <dir> <state|damaged|none>
  case "$2" in
    none) ;;
    damaged) printf '{kaputt' > "$1/.pulse-data.json" ;;
    *) printf '{"format": "pulse-data-dir", "version": 1, "state": "%s"}\n' "$2" > "$1/.pulse-data.json" ;;
  esac
}
own() { sudo chown -R 1000:1000 "$1"; }
state() { docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo missing; }
in_container() { docker exec "$CONTAINER" "$@"; }
wait_exited() {
  for _ in $(seq 1 60); do
    [ "$(state)" = exited ] && return 0
    sleep 1
  done
  return 1
}
wait_healthy() {
  for _ in $(seq 1 120); do
    case "$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo none)" in
      healthy) return 0 ;;
      unhealthy) return 1 ;;
    esac
    [ "$(state)" = exited ] && return 1
    sleep 2
  done
  return 1
}

step "Image bauen (mit PULSE_GIT_SHA=$PULSE_GIT_SHA)"
COMPOSE_FILE="$BASE" PULSE_DATA_HOST_DIR="$WORK" docker compose build

step "1) Fehlendes Datenverzeichnis: Compose darf den Container nicht starten"
if COMPOSE_FILE="$BASE" PULSE_DATA_HOST_DIR="$WORK/fehlt" docker compose up -d --no-build 2>"$WORK/missing.err"; then
  fail "Container startete ohne Datenverzeichnis"
fi
cat "$WORK/missing.err"
[ ! -e "$WORK/fehlt" ] || fail "Docker hat das fehlende Datenverzeichnis angelegt"
[ "$(state)" != running ] || fail "Container läuft trotz fehlendem Datenverzeichnis"
COMPOSE_FILE="$BASE" PULSE_DATA_HOST_DIR="$WORK/fehlt" docker compose rm -sf pulse >/dev/null 2>&1 || true

step "2) Fehlende/falsche Migrationsmarkierung: echter Start (main.py) endet mit Exit 78"
for case in none in_progress damaged rolled_back; do
  dir="$WORK/marker-$case"
  mkdir -p "$dir"
  marker "$dir" "$case"
  own "$dir"
  before="$(sudo ls -A "$dir" | sort | tr '\n' ' ')"
  COMPOSE_FILE="$BASE" PULSE_DATA_HOST_DIR="$dir" docker compose up -d --no-build
  wait_exited || fail "Markierung $case: Container beendet sich nicht"
  code="$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")"
  [ "$code" = 78 ] || fail "Markierung $case: Exit-Code $code statt 78"
  after="$(sudo ls -A "$dir" | sort | tr '\n' ' ')"
  [ "$before" = "$after" ] || fail "Markierung $case: Datenverzeichnis wurde verändert ($before → $after)"
  echo "Markierung $case: Exit 78, Verzeichnis unverändert"
  COMPOSE_FILE="$BASE" PULSE_DATA_HOST_DIR="$dir" docker compose rm -sf pulse >/dev/null
done

step "3) Migriertes Datenverzeichnis: Start, Healthcheck, Rechte, Read-only"
data="$WORK/ok"
mkdir -p "$data" .pulse-agent
marker "$data" complete
own "$data"
sudo chown 1000:1000 .pulse-agent
export COMPOSE_FILE="$TEST" PULSE_DATA_HOST_DIR="$data"
docker compose up -d --no-build
wait_healthy || fail "Container wurde nicht healthy"
curl -fsS "http://127.0.0.1:$PORT/healthz" | tee "$WORK/healthz.json" | grep -q '"ok":true' || fail "/healthz nicht ok"
echo

[ "$(in_container id -u)" = 1000 ] || fail "Container läuft nicht als UID 1000"
if in_container sh -c 'touch /app/pulse_ci_probe' 2>/dev/null; then fail "/app ist beschreibbar"; fi
if in_container sh -c 'touch /pulse_ci_probe' 2>/dev/null; then fail "Root-Dateisystem ist beschreibbar"; fi
in_container sh -c 'touch /data/ci-write-probe' || fail "/data ist nicht beschreibbar"
in_container sh -c 'test "$PULSE_DATA_DIR" = /data && test "$PULSE_DB_PATH" = /data/pulse.db && test "$TMPDIR" = /data/tmp' \
  || fail "Umgebungsvariablen falsch"
[ "$(in_container printenv PULSE_GIT_SHA)" = "$PULSE_GIT_SHA" ] || fail "PULSE_GIT_SHA nicht im Container"
in_container sh -c 'test ! -e /app/.env && test ! -e /app/.git' || fail ".env oder .git im Container sichtbar"
in_container sh -c 'grep -E "^tmpfs /tmp tmpfs .*noexec" /proc/mounts | grep -q "size=65536k"' || fail "/tmp ist kein 64-MB-tmpfs mit noexec"
in_container sh -c 'touch /tmp/ci-tmp-probe' || fail "/tmp nicht beschreibbar"
echo probe > .pulse-agent/ci-host-probe
in_container sh -c 'test -f /app/.pulse-agent/ci-host-probe && touch /app/.pulse-agent/ci-container-probe' \
  || fail ".pulse-agent-Mount fehlt oder ist nicht beschreibbar"
for f in pulse.db .session_secret config.json logs/pulse.log transcripts/T-1.txt assets/support_wait.mp3; do
  sudo test -f "$data/$f" || fail "$f fehlt im Datenverzeichnis"
  [ "$(sudo stat -c %u "$data/$f")" = 1000 ] || fail "$f gehört nicht UID 1000"
done
sudo sh -c "ls '$data'/backups/pulse_full_predeploy_*.zip" >/dev/null || fail "Komplett-Backup fehlt"
docker logs "$CONTAINER" 2>&1 | grep -q "container smoke ok" || fail "Smoke-Test im Container nicht bestanden"
docker logs "$CONTAINER" 2>&1 | grep -q "Cog-Module importiert" || fail "Cog-Import im Container nicht bestanden"

step "4) Shutdown (SIGTERM, stop_grace_period) und Neustart auf vorhandenen Daten"
start="$(date +%s)"
docker compose stop pulse
took=$(( $(date +%s) - start ))
[ "$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")" = 0 ] || fail "Shutdown nicht sauber (Exit-Code)"
[ "$took" -lt 40 ] || fail "Shutdown brauchte $took s (Grace 40 s – SIGKILL?)"
sudo grep -q "shutdown ok" "$data/logs/compose-probe.log" || fail "Lifespan-Shutdown nicht abgeschlossen"
docker compose start pulse
wait_healthy || fail "Neustart auf vorhandenen Daten nicht healthy"
docker compose stop pulse

step "Compose-Integration bestanden"
