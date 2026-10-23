#!/usr/bin/env bash
# O-08: one-time preparation of a fresh Ubuntu 24.04 VPS (e.g. Vionity KVM, 2 GiB RAM) for Pulse.
#
#   sudo bash deploy/vps_bootstrap.sh [repo-dir]
#
# Idempotent: every step checks first and can be repeated safely. It never touches Pulse runtime data
# (.env, *.db, JSON, backups/, transcripts/) and never starts Pulse – that stays a manual step
# (VPS_DEPLOYMENT.md §3/§4) after the data has been copied over.
set -euo pipefail

REPO_DIR="${1:-/opt/pulse/Pulse}"
REPO_URL="${PULSE_REPO_URL:-https://github.com/NicoPinguinDEV/Pulse.git}"
PULSE_UID="${PULSE_UID:-1000}"
SWAP_SIZE="${PULSE_SWAP_SIZE:-2G}"
# The firewall must never lock out the running SSH daemon: take its real port unless one is given.
SSH_PORT="${PULSE_SSH_PORT:-$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')}"
SSH_PORT="${SSH_PORT:-22}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit root/sudo ausführen: sudo bash deploy/vps_bootstrap.sh"
  exit 1
fi

echo "== 1/6 Systemupdates und Grundpakete"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q git ca-certificates curl ufw fail2ban unattended-upgrades docker.io docker-compose-v2
systemctl enable --now docker
# Security updates install automatically; reboots stay manual (no surprise downtime of the bot).
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null

echo "== 2/6 Swap (2 GiB RAM: Puffer gegen OOM-Kills, compose.yaml erlaubt Pulse 256 MiB Swap)"
if swapon --show --noheadings | grep -q .; then
  echo "Swap bereits aktiv – unverändert."
else
  if [ ! -f /swapfile ]; then
    fallocate -l "$SWAP_SIZE" /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
# Use swap only under real memory pressure.
echo 'vm.swappiness=10' > /etc/sysctl.d/90-pulse-swap.conf
sysctl -q -p /etc/sysctl.d/90-pulse-swap.conf

echo "== 3/6 Firewall: nur SSH, HTTP und HTTPS (Dashboard-Port 25095 bleibt intern)"
ufw allow "${SSH_PORT}/tcp" >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status verbose | sed -n '1,12p'

echo "== 4/6 fail2ban gegen SSH-Brute-Force"
if [ ! -f /etc/fail2ban/jail.d/pulse-sshd.local ]; then
  cat > /etc/fail2ban/jail.d/pulse-sshd.local <<EOF
[sshd]
enabled = true
port = ${SSH_PORT}
maxretry = 5
bantime = 1h
EOF
fi
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

echo "== 5/6 Repository"
mkdir -p "$(dirname "$REPO_DIR")"
if [ -d "$REPO_DIR/.git" ]; then
  echo "Checkout existiert bereits in $REPO_DIR – unverändert (Updates laufen über deploy/remote_deploy.sh)."
else
  git clone "$REPO_URL" "$REPO_DIR"
fi

echo "== 6/6 Rechte für den Container-Benutzer (UID $PULSE_UID, siehe Dockerfile)"
chown -R "$PULSE_UID:$PULSE_UID" "$REPO_DIR"
if [ -f "$REPO_DIR/.env" ]; then
  chmod 600 "$REPO_DIR/.env"
fi

cat <<EOF

Fertig. Nächste Schritte (VPS_DEPLOYMENT.md):
  1. Laufzeitdaten (config.json, *.db, JSON-Dateien, backups/, transcripts/) nach $REPO_DIR kopieren
     und danach erneut: chown -R $PULSE_UID:$PULSE_UID $REPO_DIR
  2. cp $REPO_DIR/.env.example $REPO_DIR/.env && nano $REPO_DIR/.env && chmod 600 $REPO_DIR/.env
  3. cd $REPO_DIR && docker compose up -d --build   (mit Domain: docker compose --profile https up -d --build)
  4. docker compose logs -f --tail=150 pulse
EOF
