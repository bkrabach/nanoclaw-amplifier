#!/usr/bin/env bash
# nanoclaw-amplifier installer
# Installs nanoclaw with Amplifier as the AI backend.
# Usage: bash install.sh [--nanoclaw-dir PATH]
set -euo pipefail

REPO="https://github.com/bkrabach/nanoclaw-amplifier"
IMAGE="ghcr.io/bkrabach/nanoclaw-amplifier:latest"
NANOCLAW_DIR="${NANOCLAW_DIR:-./nanoclaw}"

GRN='\033[0;32m' YLW='\033[1;33m' CYN='\033[0;36m' NC='\033[0m' BLD='\033[1m'
info()  { echo -e "${GRN}[nanoclaw-amplifier]${NC} $*"; }
warn()  { echo -e "${YLW}[nanoclaw-amplifier]${NC} $*"; }
step()  { echo -e "\n${CYN}${BLD}▸ $*${NC}"; }
ask()   { echo -e "${BLD}$*${NC}"; }

while [[ $# -gt 0 ]]; do
  case $1 in
    --nanoclaw-dir) NANOCLAW_DIR="$2"; shift 2 ;;
    *) shift ;;
  esac
done

echo ""
echo -e "${CYN}${BLD}nanoclaw-amplifier installer${NC}"
echo -e "Powered by Amplifier — https://github.com/microsoft/amplifier"
echo ""

# ── Step 1: Choose provider ────────────────────────────────────────────────
step "Choose your AI provider"
echo ""
echo "  1) Anthropic (Claude)    — api.anthropic.com"
echo "  2) OpenAI (GPT)          — api.openai.com"
echo "  3) Google Gemini         — generativelanguage.googleapis.com"
echo "  4) Ollama (local/free)   — your machine"
echo "  5) Other / skip for now  — configure after install"
echo ""
read -rp "$(echo -e "${BLD}Your choice [1-5]:${NC} ")" PROVIDER_CHOICE

PROVIDER_KEY=""
PROVIDER_NAME=""
PROVIDER_HOST=""
PROVIDER_HEADER=""
PROVIDER_VALUE_FORMAT=""
PROVIDER_MODEL=""
NANOCLAW_PROVIDER=""
SKIP_AUTH="no"
NANOCLAW_AUTH_TOKEN=""
ANTHROPIC_AUTH_TOKEN_VAR=""

case "$PROVIDER_CHOICE" in
  1)
    PROVIDER_KEY="anthropic"
    PROVIDER_NAME="Anthropic"
    NANOCLAW_PROVIDER="anthropic"
    PROVIDER_MODEL="claude-opus-4-5"
    echo ""
    if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
      info "Found ANTHROPIC_API_KEY in environment"
      ANTHROPIC_AUTH_TOKEN_VAR="$ANTHROPIC_API_KEY"
    else
      read -rsp "$(echo -e "${BLD}Paste your Anthropic API key (sk-ant-...):${NC} ")" PROVIDER_API_KEY
      echo ""
      ANTHROPIC_AUTH_TOKEN_VAR="$PROVIDER_API_KEY"
    fi
    # For Anthropic: let nanoclaw's own auth handle it via NANOCLAW_ANTHROPIC_AUTH_TOKEN
    SKIP_AUTH="no"
    ;;
  2)
    PROVIDER_KEY="openai"
    PROVIDER_NAME="OpenAI"
    NANOCLAW_PROVIDER="openai"
    PROVIDER_HOST="api.openai.com"
    PROVIDER_HEADER="Authorization"
    PROVIDER_VALUE_FORMAT='Bearer {value}'
    PROVIDER_MODEL="gpt-4o"
    SKIP_AUTH="yes"
    echo ""
    if [[ -n "${OPENAI_API_KEY:-}" ]]; then
      info "Found OPENAI_API_KEY in environment"
      PROVIDER_API_KEY="$OPENAI_API_KEY"
    else
      read -rsp "$(echo -e "${BLD}Paste your OpenAI API key (sk-...):${NC} ")" PROVIDER_API_KEY
      echo ""
    fi
    ;;
  3)
    PROVIDER_KEY="gemini"
    PROVIDER_NAME="Google Gemini"
    NANOCLAW_PROVIDER="gemini"
    PROVIDER_HOST="generativelanguage.googleapis.com"
    PROVIDER_HEADER="x-goog-api-key"
    PROVIDER_VALUE_FORMAT='{value}'
    PROVIDER_MODEL="gemini-2.0-flash"
    SKIP_AUTH="yes"
    echo ""
    if [[ -n "${GEMINI_API_KEY:-}" ]]; then
      info "Found GEMINI_API_KEY in environment"
      PROVIDER_API_KEY="$GEMINI_API_KEY"
    elif [[ -n "${GOOGLE_API_KEY:-}" ]]; then
      info "Found GOOGLE_API_KEY in environment"
      PROVIDER_API_KEY="$GOOGLE_API_KEY"
    else
      read -rsp "$(echo -e "${BLD}Paste your Gemini API key (AIza...):${NC} ")" PROVIDER_API_KEY
      echo ""
    fi
    ;;
  4)
    PROVIDER_KEY="ollama"
    PROVIDER_NAME="Ollama"
    NANOCLAW_PROVIDER="ollama"
    PROVIDER_MODEL="llama3"
    SKIP_AUTH="yes"
    echo ""
    if [[ -n "${OLLAMA_HOST:-}" ]]; then
      info "Found OLLAMA_HOST in environment: $OLLAMA_HOST"
      PROVIDER_API_KEY="$OLLAMA_HOST"
    else
      read -rp "$(echo -e "${BLD}Ollama host URL [http://host.docker.internal:11434]:${NC} ")" OLLAMA_HOST_INPUT
      PROVIDER_API_KEY="${OLLAMA_HOST_INPUT:-http://host.docker.internal:11434}"
    fi
    ;;
  *)
    PROVIDER_KEY="anthropic"
    NANOCLAW_PROVIDER="anthropic"
    PROVIDER_MODEL="claude-opus-4-5"
    SKIP_AUTH="yes"
    PROVIDER_API_KEY=""
    warn "Skipping provider setup — configure later with: ncla add-provider"
    ;;
esac

# ── Step 2: nanoclaw ───────────────────────────────────────────────────────
step "Setting up nanoclaw"
if [[ -d "$NANOCLAW_DIR" ]]; then
  info "Found existing nanoclaw at $NANOCLAW_DIR"
else
  info "Cloning nanoclaw into $NANOCLAW_DIR..."
  git clone https://github.com/nanocoai/nanoclaw.git "$NANOCLAW_DIR"
fi
cd "$NANOCLAW_DIR"

# ── Step 3: Set CONTAINER_IMAGE ────────────────────────────────────────────
step "Configuring Amplifier container image"
if [[ -f .env ]] && grep -q "^CONTAINER_IMAGE=" .env; then
  sed -i "s|^CONTAINER_IMAGE=.*|CONTAINER_IMAGE=${IMAGE}|" .env
else
  echo "CONTAINER_IMAGE=${IMAGE}" >> .env
fi
info "Container image → ${IMAGE}"

# ── Step 4: Run nanoclaw setup ────────────────────────────────────────────
step "Running nanoclaw setup wizard"

if [[ "$SKIP_AUTH" == "yes" ]]; then
  info "Skipping nanoclaw's Anthropic auth step (you chose ${PROVIDER_NAME})"
  # nanoclaw's auth step is skipped; we'll handle credential setup below
  export NANOCLAW_SKIP="${NANOCLAW_SKIP:-auth}"
elif [[ -n "$ANTHROPIC_AUTH_TOKEN_VAR" ]]; then
  # Let nanoclaw's custom-endpoint auth path handle Anthropic API key non-interactively
  export NANOCLAW_ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN_VAR"
  export NANOCLAW_ANTHROPIC_BASE_URL="https://api.anthropic.com"
fi

bash nanoclaw.sh

# ── Step 5: Register provider credentials in OneCLI ───────────────────────
if [[ "$SKIP_AUTH" == "yes" ]] && [[ -n "$PROVIDER_API_KEY" ]]; then
  step "Registering ${PROVIDER_NAME} credentials in OneCLI vault"

  if [[ "$PROVIDER_KEY" == "ollama" ]]; then
    # Ollama doesn't need auth — just configure host via agent settings
    info "Ollama: no auth needed, host=${PROVIDER_API_KEY}"
    OLLAMA_HOST_VAL="$PROVIDER_API_KEY"
  else
    # Register API key in OneCLI vault
    if command -v onecli &>/dev/null; then
      onecli secrets create \
        --name "${PROVIDER_NAME}" \
        --type generic \
        --value "${PROVIDER_API_KEY}" \
        --host-pattern "${PROVIDER_HOST}" \
        --header-name "${PROVIDER_HEADER}" \
        --value-format "${PROVIDER_VALUE_FORMAT}" && \
        info "Secret registered in OneCLI vault for ${PROVIDER_HOST}" || \
        warn "onecli secret creation failed — run manually: onecli secrets create ..."
    else
      warn "onecli not found in PATH — register your API key manually after setup"
    fi
  fi
fi

# ── Step 6: Switch agent provider ─────────────────────────────────────────
if [[ "$PROVIDER_KEY" != "anthropic" ]]; then
  step "Configuring agent to use ${PROVIDER_NAME}"

  # Wait for service to be ready
  sleep 3

  # Get the first agent group ID
  AGENT_ID=""
  if command -v ncl &>/dev/null; then
    AGENT_ID=$(ncl groups list 2>/dev/null | grep -oP '"id"\s*:\s*"\K[^"]+' | head -1 || true)
  fi

  if [[ -n "$AGENT_ID" ]]; then
    info "Found agent: ${AGENT_ID}"

    # Set secret mode to all (picks up any matching OneCLI secrets automatically)
    if command -v onecli &>/dev/null; then
      onecli agents set-secret-mode --id "$AGENT_ID" --mode all 2>/dev/null || true
    fi

    # Update provider
    ncl groups config update \
      --id "$AGENT_ID" \
      --provider "$NANOCLAW_PROVIDER" \
      --model "$PROVIDER_MODEL" 2>/dev/null && \
      info "Agent provider → ${NANOCLAW_PROVIDER} / ${PROVIDER_MODEL}" || \
      warn "Could not update agent provider automatically. Run:"$'\n'"  ncl groups config update --id <agent-id> --provider ${NANOCLAW_PROVIDER} --model ${PROVIDER_MODEL}"

    # Restart
    ncl groups restart --id "$AGENT_ID" 2>/dev/null && \
      info "Agent restarted with new provider" || \
      warn "Restart failed — run: ncl groups restart --id ${AGENT_ID}"
  else
    warn "Could not find agent group ID. After setup, run:"
    echo "  ncl groups list"
    echo "  ncl groups config update --id <id> --provider ${NANOCLAW_PROVIDER} --model ${PROVIDER_MODEL}"
    if command -v onecli &>/dev/null; then
      echo "  onecli agents set-secret-mode --id <id> --mode all"
    fi
    echo "  ncl groups restart --id <id>"
  fi
fi

# ── Step 7: Install ncla CLI ───────────────────────────────────────────────
step "Installing ncla companion CLI"
if command -v npm &>/dev/null; then
  npm install -g "github:bkrabach/nanoclaw-amplifier" --silent 2>/dev/null && \
    info "ncla installed — run 'ncla add-provider' to add more providers" || \
    warn "ncla install failed — install later with: npm install -g github:bkrabach/nanoclaw-amplifier"
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
info "Setup complete!"
if [[ -n "${AGENT_ID:-}" ]]; then
  echo -e "  Provider:  ${BLD}${PROVIDER_NAME} / ${PROVIDER_MODEL}${NC}"
fi
echo ""
echo "  Add more providers:  ncla add-provider openai"
echo "  Check status:        ncla status"
echo ""
