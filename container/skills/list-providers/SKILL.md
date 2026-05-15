---
name: list-providers
description: Show current AI provider and available options
---

# List Providers Skill

Show the user information about the current AI provider and what's available.

## Steps

1. Read `/workspace/agent/container.json` to get current provider and model
2. Use `send_message` to report:
   - Current provider and model
   - Available providers: Anthropic (Claude), OpenAI (GPT), Gemini, Ollama (local), vLLM (local), Azure OpenAI
   - How to switch: "Tell me 'switch to OpenAI' or run 'ncla add-provider openai'"
3. If provider is "claude" or "anthropic" (default), note it's using the Amplifier Anthropic integration (not Claude Code)
