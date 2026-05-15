#!/usr/bin/env bash
# nanoclaw-amplifier installer
# Installs nanoclaw with Amplifier as the AI backend.
# Usage: bash install.sh [--nanoclaw-dir PATH]
set -euo pipefail

REPO="https://github.com/bkrabach/nanoclaw-amplifier"
IMAGE="ghcr.io/bkrabach/nanoclaw-amplifier:latest"
NANOCLAW_DIR="${NANOCLAW_DIR:-./nanoclaw}"

# Colors
GRN='\033[0;32m' YLW='\033[1;33m' RED='\033[0;31m' NC='\033[0m'
info()  { echo -e "${GRN}[nanoclaw-amplifier]${NC} $*"; }
warn()  { echo -e "${YLW}[nanoclaw-amplifier]${NC} $*"; }
error() { echo -e "${RED}[nanoclaw-amplifier]${NC} $*" >&2; }

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --nanoclaw-dir) NANOCLAW_DIR="$2"; shift 2 ;;
    *) shift ;;
  esac
done

info "nanoclaw-amplifier installer"
echo ""

# Step 1: nanoclaw
if [[ -d "$NANOCLAW_DIR" ]]; then
  info "Found existing nanoclaw at $NANOCLAW_DIR"
else
  info "Cloning nanoclaw into $NANOCLAW_DIR..."
  git clone https://github.com/nanocoai/nanoclaw.git "$NANOCLAW_DIR"
fi

cd "$NANOCLAW_DIR"

# Step 2: set CONTAINER_IMAGE before running nanoclaw setup
info "Configuring container image..."
if [[ -f .env ]] && grep -q "^CONTAINER_IMAGE=" .env; then
  sed -i "s|^CONTAINER_IMAGE=.*|CONTAINER_IMAGE=${IMAGE}|" .env
else
  echo "CONTAINER_IMAGE=${IMAGE}" >> .env
fi
info "Container image set to: ${IMAGE}"

# Step 3: run nanoclaw's own setup wizard
info "Running nanoclaw setup wizard..."
echo "(This handles Docker, OneCLI, Anthropic auth, and your first agent.)"
echo ""
bash nanoclaw.sh

# Step 4: additional providers
echo ""
info "nanoclaw-amplifier: Additional Provider Setup"
echo "Your assistant now runs on Amplifier. You can add more AI providers"
echo "in addition to Anthropic. These will be stored securely in OneCLI."
echo ""

if command -v node >/dev/null 2>&1; then
  # Use our interactive provider setup if Node is available
  SETUP_SCRIPT="$(mktemp /tmp/na-provider-setup.XXXXXX.mjs)"
  curl -sSL "https://raw.githubusercontent.com/bkrabach/nanoclaw-amplifier/main/scripts/setup-providers.mjs" \
    -o "$SETUP_SCRIPT" 2>/dev/null || true
  if [[ -f "$SETUP_SCRIPT" ]] && [[ -s "$SETUP_SCRIPT" ]]; then
    node "$SETUP_SCRIPT" || true
    rm -f "$SETUP_SCRIPT"
  fi
else
  warn "Node.js not found — skipping interactive provider setup."
  warn "You can add providers later by telling your assistant: 'add OpenAI as a provider'"
fi

# Step 5: install ncla CLI
info "Installing ncla companion CLI..."
if command -v npm >/dev/null 2>&1; then
  npm install -g "github:bkrabach/nanoclaw-amplifier" --silent 2>/dev/null || \
    warn "ncla CLI install failed — you can install it later with: npm install -g github:bkrabach/nanoclaw-amplifier"
fi

echo ""
info "Setup complete! Your assistant now runs on Amplifier."
echo "  To add more providers: tell your assistant or run 'ncla add-provider openai'"
echo "  To check status: ncla status"
echo ""
