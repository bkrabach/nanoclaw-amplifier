"""
Read /workspace/agent/container.json and optional amplifier-settings.yaml files.
"""
from __future__ import annotations
import json, logging, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONTAINER_JSON = Path(os.environ.get("_NC_CONTAINER_JSON", "/workspace/agent/container.json"))
GLOBAL_SETTINGS = Path(os.environ.get("_NC_GLOBAL_SETTINGS", "/workspace/global/amplifier-settings.yaml"))
AGENT_SETTINGS  = Path(os.environ.get("_NC_AGENT_SETTINGS", "/workspace/agent/amplifier-settings.yaml"))
CONTEXT_FILE    = Path(os.environ.get("_NC_CONTEXT_FILE", "/workspace/agent/.amplifier-context.json"))

PROVIDER_MAP = {
    "claude": "provider-anthropic", "anthropic": "provider-anthropic",
    "openai": "provider-openai",
    "azure": "provider-azure-openai", "azure-openai": "provider-azure-openai",
    "gemini": "provider-gemini",
    "ollama": "provider-ollama",
    "vllm": "provider-vllm",
    "chat-completions": "provider-chat-completions",
    "openai-compat": "provider-chat-completions",
    "copilot": "provider-github-copilot",
    "github-copilot": "provider-github-copilot",
    "bedrock": "provider-bedrock",
    "perplexity": "provider-perplexity",
    "mock": "provider-mock",
}

# Source URLs for provider modules (microsoft/ for first-party)
PROVIDER_SOURCES = {
    "provider-anthropic":       "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "provider-openai":          "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "provider-azure-openai":    "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
    "provider-gemini":          "git+https://github.com/microsoft/amplifier-module-provider-gemini@main",
    "provider-ollama":          "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
    "provider-vllm":            "git+https://github.com/microsoft/amplifier-module-provider-vllm@main",
    "provider-chat-completions":"git+https://github.com/microsoft/amplifier-module-provider-chat-completions@main",
    "provider-github-copilot":  "git+https://github.com/microsoft/amplifier-module-provider-github-copilot@main",
    "provider-bedrock":         "git+https://github.com/brycecutt-msft/amplifier-module-provider-bedrock@main",
    "provider-perplexity":      "git+https://github.com/colombod/amplifier-module-provider-perplexity@main",
    "provider-mock":            "git+https://github.com/microsoft/amplifier-module-provider-mock@main",
}

@dataclass
class ProviderConfig:
    module_id: str
    model: str
    source: str
    extra: dict = field(default_factory=dict)

@dataclass
class NanoclawConfig:
    provider: ProviderConfig
    assistant_name: str = "Assistant"
    agent_group_id: str = "default"
    max_messages_per_prompt: int = 10
    inbound_db:  Path = Path("/workspace/inbound.db")
    outbound_db: Path = Path("/workspace/outbound.db")
    heartbeat:   Path = Path("/workspace/.heartbeat")
    context_file: Path = CONTEXT_FILE

def _load_yaml_safe(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        log.warning(f"Could not load {path}: {e}")
        return {}

def load_config() -> NanoclawConfig:
    raw = json.loads(CONTAINER_JSON.read_text()) if CONTAINER_JSON.exists() else {}

    provider_str = raw.get("provider", "claude")
    module_id = PROVIDER_MAP.get((provider_str or "").lower(), "provider-anthropic")
    model = raw.get("model") or "claude-opus-4-5"
    source = PROVIDER_SOURCES.get(module_id, f"git+https://github.com/microsoft/amplifier-module-{module_id}@main")

    # Load settings files for credentials (global → agent, agent wins)
    global_s = _load_yaml_safe(GLOBAL_SETTINGS)
    agent_s  = _load_yaml_safe(AGENT_SETTINGS)
    extra: dict[str, Any] = {}
    for settings in (global_s, agent_s):
        for entry in settings.get("providers", []):
            if entry.get("module") == module_id:
                extra.update(entry.get("config", {}))

    # Also apply any _amplifier_config MCP env vars (for Ollama host etc.)
    mcp_servers = raw.get("mcpServers", {})
    amplifier_cfg = mcp_servers.get("_amplifier_config", {})
    for k, v in amplifier_cfg.get("env", {}).items():
        os.environ.setdefault(k, v)

    # Allow env-var overrides for paths (useful in tests and alternative deployments)
    inbound_db  = Path(os.environ["_NC_INBOUND_DB"])  if "_NC_INBOUND_DB"  in os.environ else Path("/workspace/inbound.db")
    outbound_db = Path(os.environ["_NC_OUTBOUND_DB"]) if "_NC_OUTBOUND_DB" in os.environ else Path("/workspace/outbound.db")
    heartbeat   = Path(os.environ["_NC_HEARTBEAT"])   if "_NC_HEARTBEAT"   in os.environ else Path("/workspace/.heartbeat")
    context_file = Path(os.environ["_NC_CONTEXT_FILE"]) if "_NC_CONTEXT_FILE" in os.environ else CONTEXT_FILE

    return NanoclawConfig(
        provider=ProviderConfig(module_id=module_id, model=model, source=source, extra=extra),
        assistant_name=raw.get("assistantName", raw.get("groupName", "Assistant")),
        agent_group_id=raw.get("agentGroupId", "default"),
        max_messages_per_prompt=raw.get("maxMessagesPerPrompt", 10),
        inbound_db=inbound_db,
        outbound_db=outbound_db,
        heartbeat=heartbeat,
        context_file=context_file,
    )
