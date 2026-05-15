#!/usr/bin/env bash
# Switch an existing nanoclaw install to nanoclaw-amplifier.
set -euo pipefail

IMAGE="ghcr.io/bkrabach/nanoclaw-amplifier:latest"
NANOCLAW_DIR="${NANOCLAW_DIR:-./nanoclaw}"

GRN='\033[0;32m' YLW='\033[1;33m' NC='\033[0m'
info() { echo -e "${GRN}[nanoclaw-amplifier]${NC} $*"; }
warn() { echo -e "${YLW}[nanoclaw-amplifier]${NC} $*"; }

[[ -d "$NANOCLAW_DIR" ]] || { echo "nanoclaw not found at $NANOCLAW_DIR"; exit 1; }
cd "$NANOCLAW_DIR"

info "Switching container image to ${IMAGE}..."
if grep -q "^CONTAINER_IMAGE=" .env 2>/dev/null; then
  sed -i "s|^CONTAINER_IMAGE=.*|CONTAINER_IMAGE=${IMAGE}|" .env
else
  echo "CONTAINER_IMAGE=${IMAGE}" >> .env
fi

info "Pulling latest image..."
docker pull "$IMAGE" || warn "Could not pull image — will use cached version"

info "Restarting nanoclaw service..."
if systemctl is-active nanoclaw >/dev/null 2>&1; then
  systemctl restart nanoclaw
elif launchctl list | grep -q nanoclaw 2>/dev/null; then
  launchctl kickstart -k "gui/$(id -u)/nanoclaw" 2>/dev/null || true
else
  warn "Could not restart service automatically. Please restart nanoclaw manually."
fi

info "Upgrade complete! Your next message will use Amplifier."
echo "  To add providers: ncla add-provider openai"
echo "  To check status:  ncla status"
