#!/usr/bin/env bash
# nanoclaw-amplifier installer
# Installs nanoclaw with Amplifier as the AI backend.
# Usage: bash install.sh [--nanoclaw-dir PATH]
set -euo pipefail

IMAGE="ghcr.io/bkrabach/nanoclaw-amplifier:latest"
NANOCLAW_DIR="${NANOCLAW_DIR:-./nanoclaw}"

GRN='\033[0;32m' YLW='\033[1;33m' CYN='\033[0;36m' NC='\033[0m' BLD='\033[1m'
info()  { echo -e "${GRN}[nanoclaw-amplifier]${NC} $*"; }
warn()  { echo -e "${YLW}[nanoclaw-amplifier]${NC} $*"; }
step()  { echo -e "\n${CYN}${BLD}▸ $*${NC}"; }

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

# ── Helper: find API key from multiple sources ────────────────────────────
# Usage: _get_key ENV_VAR_NAME YAML_MODULE_NAME PROMPT_TEXT
# Checks: (1) env var, (2) ~/.amplifier/settings.yaml, (3) prompts user
_get_key() {
  local env_var="$1"       # e.g. OPENAI_API_KEY
  local yaml_module="$2"   # e.g. provider-openai
  local prompt_text="$3"   # e.g. "OpenAI API key (sk-...)"
  local found_key=""

  # 1. Check env var
  if [[ -n "${!env_var:-}" ]]; then
    info "Found ${env_var} in environment"
    echo "${!env_var}"
    return 0
  fi

  # 2. Check ~/.amplifier/settings.yaml
  local settings_file="${HOME}/.amplifier/settings.yaml"
  if [[ -f "$settings_file" ]] && command -v python3 &>/dev/null; then
    found_key=$(python3 - <<PYEOF 2>/dev/null || true
import sys
try:
    import yaml
    with open("$settings_file") as f:
        data = yaml.safe_load(f) or {}
    for p in data.get("providers", []):
        if p.get("module") == "$yaml_module":
            key = p.get("config", {}).get("api_key", "")
            if key:
                print(key)
                break
except Exception:
    pass
PYEOF
)
    if [[ -n "$found_key" ]]; then
      info "Found ${yaml_module} key in ~/.amplifier/settings.yaml"
      echo "$found_key"
      return 0
    fi
  fi

  # 3. Also check project-local .amplifier/settings.yaml
  local local_settings=".amplifier/settings.yaml"
  if [[ -f "$local_settings" ]] && command -v python3 &>/dev/null; then
    found_key=$(python3 - <<PYEOF 2>/dev/null || true
import sys
try:
    import yaml
    with open("$local_settings") as f:
        data = yaml.safe_load(f) or {}
    for p in data.get("providers", []):
        if p.get("module") == "$yaml_module":
            key = p.get("config", {}).get("api_key", "")
            if key:
                print(key)
                break
except Exception:
    pass
PYEOF
)
    if [[ -n "$found_key" ]]; then
      info "Found ${yaml_module} key in .amplifier/settings.yaml"
      echo "$found_key"
      return 0
    fi
  fi

  # 4. Prompt user (masked input)
  local user_key=""
  read -rsp "$(echo -e "  ${BLD}${prompt_text}:${NC} ")" user_key
  echo "" >&2
  echo "$user_key"
}

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
PROVIDER_API_KEY=""
ANTHROPIC_AUTH_TOKEN_VAR=""

case "$PROVIDER_CHOICE" in
  1)
    PROVIDER_KEY="anthropic"
    PROVIDER_NAME="Anthropic"
    NANOCLAW_PROVIDER="anthropic"
    PROVIDER_MODEL="claude-opus-4-5"
    ANTHROPIC_AUTH_TOKEN_VAR=$(_get_key "ANTHROPIC_API_KEY" "provider-anthropic" "Anthropic API key (sk-ant-...)")
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
    PROVIDER_API_KEY=$(_get_key "OPENAI_API_KEY" "provider-openai" "OpenAI API key (sk-...)")
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
    # Check both GEMINI_API_KEY and GOOGLE_API_KEY
    if [[ -n "${GEMINI_API_KEY:-}" ]]; then
      info "Found GEMINI_API_KEY in environment"
      PROVIDER_API_KEY="$GEMINI_API_KEY"
    elif [[ -n "${GOOGLE_API_KEY:-}" ]]; then
      info "Found GOOGLE_API_KEY in environment"
      PROVIDER_API_KEY="$GOOGLE_API_KEY"
    else
      PROVIDER_API_KEY=$(_get_key "_NONE_" "provider-gemini" "Gemini API key (AIza...)")
    fi
    ;;
  4)
    PROVIDER_KEY="ollama"
    PROVIDER_NAME="Ollama"
    NANOCLAW_PROVIDER="ollama"
    PROVIDER_MODEL="llama3"
    SKIP_AUTH="yes"
    if [[ -n "${OLLAMA_HOST:-}" ]]; then
      info "Found OLLAMA_HOST in environment: $OLLAMA_HOST"
      PROVIDER_API_KEY="$OLLAMA_HOST"
    else
      read -rp "$(echo -e "${BLD}  Ollama host URL [http://host.docker.internal:11434]:${NC} ")" OLLAMA_HOST_INPUT
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
# Write to .env as well (used by some nanoclaw versions/configs)
if [[ -f .env ]] && grep -q "^CONTAINER_IMAGE=" .env; then
  sed -i "s|^CONTAINER_IMAGE=.*|CONTAINER_IMAGE=${IMAGE}|" .env
else
  echo "CONTAINER_IMAGE=${IMAGE}" >> .env
fi
info "Container image → ${IMAGE}"
# Note: per-agent image_tag is set in Step 6 after agents are created

# ── Step 4: Run nanoclaw setup ────────────────────────────────────────────
step "Running nanoclaw setup wizard"

if [[ "$SKIP_AUTH" == "yes" ]]; then
  info "Skipping nanoclaw auth step (you chose ${PROVIDER_NAME})"
  export NANOCLAW_SKIP="${NANOCLAW_SKIP:-auth,cli-agent}"
elif [[ -n "${ANTHROPIC_AUTH_TOKEN_VAR:-}" ]]; then
  export NANOCLAW_ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN_VAR"
  export NANOCLAW_ANTHROPIC_BASE_URL="https://api.anthropic.com"
fi

bash nanoclaw.sh

# ── Step 5: Register provider credentials in OneCLI ───────────────────────
if [[ "$SKIP_AUTH" == "yes" ]] && [[ -n "${PROVIDER_API_KEY:-}" ]]; then
  step "Registering ${PROVIDER_NAME} credentials in OneCLI vault"

  if [[ "$PROVIDER_KEY" == "ollama" ]]; then
    info "Ollama: no auth needed, host=${PROVIDER_API_KEY}"
  else
    if command -v onecli &>/dev/null; then
      onecli secrets create \
        --name "${PROVIDER_NAME}" \
        --type generic \
        --value "${PROVIDER_API_KEY}" \
        --host-pattern "${PROVIDER_HOST}" \
        --header-name "${PROVIDER_HEADER}" \
        --value-format "${PROVIDER_VALUE_FORMAT}" && \
        info "✓ Secret registered in OneCLI vault for ${PROVIDER_HOST}" || \
        warn "onecli secret creation failed — run manually: onecli secrets create ..."
    else
      warn "onecli not found in PATH — register your API key manually after setup"
    fi
  fi
fi

# ── Step 6: Switch agent provider ─────────────────────────────
if [[ "$PROVIDER_KEY" != "anthropic" ]]; then
  step "Configuring agent to use ${PROVIDER_NAME}"
  sleep 3

  AGENT_ID=""
  # ncl must be run from the nanoclaw directory
  if command -v ncl &>/dev/null; then
    AGENT_ID=$(cd "$NANOCLAW_DIR" && ncl groups list 2>/dev/null \
      | python3 -c "import sys,json; gs=json.load(sys.stdin); print(gs[0]['id'] if gs else '')" 2>/dev/null || true)
  fi

  if [[ -z "$AGENT_ID" ]]; then
    # Fallback: try pnpm exec ncl from nanoclaw dir
    AGENT_ID=$(cd "$NANOCLAW_DIR" && pnpm exec ncl groups list 2>/dev/null \
      | python3 -c "import sys,json; gs=json.load(sys.stdin); print(gs[0]['id'] if gs else '')" 2>/dev/null || true)
  fi

  if [[ -n "${AGENT_ID:-}" ]]; then
    info "Found agent: ${AGENT_ID}"
    if command -v onecli &>/dev/null; then
      onecli agents set-secret-mode --id "$AGENT_ID" --mode all 2>/dev/null || true
    fi
    # Set image tag AND provider
    (cd "$NANOCLAW_DIR" && ncl groups config update \
      --id "$AGENT_ID" \
      --provider "$NANOCLAW_PROVIDER" \
      --model "$PROVIDER_MODEL" \
      --image-tag "${IMAGE}" 2>/dev/null) && \
      info "Agent configured: image=${IMAGE} provider=${NANOCLAW_PROVIDER} model=${PROVIDER_MODEL}" || \
      warn "Could not update agent config automatically. Run manually:
  cd ${NANOCLAW_DIR}
  ncl groups config update --id ${AGENT_ID} --image-tag ${IMAGE} --provider ${NANOCLAW_PROVIDER} --model ${PROVIDER_MODEL}"
    (cd "$NANOCLAW_DIR" && ncl groups restart --id "$AGENT_ID" 2>/dev/null) && \
      info "Agent restarted with ${PROVIDER_NAME}" || \
      warn "Restart failed — run: cd ${NANOCLAW_DIR} && ncl groups restart --id ${AGENT_ID}"
  else
    warn "Could not find agent group ID. After setup, run:"
    echo "  cd ${NANOCLAW_DIR}"
    echo "  ncl groups list"
    echo "  ncl groups config update --id <id> --provider ${NANOCLAW_PROVIDER} --model ${PROVIDER_MODEL}"
    [[ "$PROVIDER_KEY" != "ollama" ]] && echo "  onecli agents set-secret-mode --id <id> --mode all"
    echo "  ncl groups restart --id <id>"
  fi
fi

# ── Step 7: Install ncla CLI ───────────────────────────────────────────────
step "Installing ncla companion CLI"
if command -v npm &>/dev/null; then
  npm install -g "github:bkrabach/nanoclaw-amplifier" --silent 2>/dev/null && \
    info "ncla installed — run 'ncla add-provider' to add more providers" || \
    warn "ncla install failed — install later: npm install -g github:bkrabach/nanoclaw-amplifier"
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
info "Setup complete!"
echo ""
echo "  Add more providers:  ncla add-provider openai"
echo "  Check status:        ncla status"
echo ""
