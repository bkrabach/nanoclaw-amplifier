# nanoclaw-amplifier

Drop-in replacement for nanoclaw's Claude Code container, powered by [Amplifier](https://github.com/microsoft/amplifier).

## What this does

Replaces the Claude Code subprocess inside nanoclaw containers with an AmplifierSession. Zero changes to nanoclaw's host — connectors, scheduling, memory, skills all stay intact. You get:

- **All Amplifier providers**: Anthropic (raw API, no subscription), OpenAI, Gemini, Azure, Ollama, vLLM, and more
- **Full Amplifier ecosystem**: hook into providers, tools, hooks as the ecosystem grows
- **Same nanoclaw UX**: same messaging platforms, same scheduling, same skills

## Install (new users)

```bash
bash <(curl -sSL https://raw.githubusercontent.com/bkrabach/nanoclaw-amplifier/main/install.sh)
```

## Upgrade (existing nanoclaw users)

```bash
bash <(curl -sSL https://raw.githubusercontent.com/bkrabach/nanoclaw-amplifier/main/upgrade.sh)
```

## Add providers

```bash
ncla add-provider openai     # OpenAI
ncla add-provider gemini     # Google Gemini
ncla add-provider ollama     # Local Ollama
```

Or tell your agent: *"I want to switch to OpenAI"*

## Per-agent custom config

Mount a custom `bundle.md` via nanoclaw's `additionalMounts`:
```json
{"additionalMounts": [{"hostPath": "~/.amplifier/agents/my-agent/bundle.md", "containerPath": "bundle.md", "readonly": true}]}
```

## Architecture

```
nanoclaw host (Node.js) ←─ unchanged ─→ all connectors, scheduling, memory
        │
    Docker container (this project)
        │
    AmplifierSession
    ├── loop-streaming orchestrator
    ├── context-simple (with file persistence)
    ├── tool-nanoclaw-messaging (send_message, etc.)
    └── tool-nanoclaw-scheduling (schedule_task, etc.)
```
