---
name: add-provider
description: Add or switch AI provider (OpenAI, Gemini, Ollama, etc.)
---

# Add Provider Skill

This skill helps configure a new AI provider for this agent.

## Steps

1. Ask the user which provider they want:
   - Use `ask_user_question` with options: ["OpenAI", "Gemini", "Azure OpenAI", "Ollama (local)", "vLLM (local)", "Other"]

2. Based on their choice:

   **For cloud providers (OpenAI, Gemini, Azure, etc.):**
   - Ask for their API key using `ask_user_question` with a single text input option, OR send instructions to enter key securely
   - Generate a setup script at `/workspace/agent/.amplifier-setup.sh`:
     ```bash
     #!/bin/bash
     # nanoclaw-amplifier provider setup
     PROVIDER="openai"  # adjust per choice
     KEY="THE_KEY"      # from user input
     HOST_PATTERN="api.openai.com"
     HEADER="Authorization"
     FORMAT="Bearer {value}"
     
     onecli secrets create \
       --name "$(echo $PROVIDER | tr '[:lower:]' '[:upper:]')" \
       --type generic \
       --value "$KEY" \
       --host-pattern "$HOST_PATTERN" \
       --header-name "$HEADER" \
       --value-format "$FORMAT"
     
     # Get current agent group ID from container.json
     AGENT_ID=$(cat /workspace/agent/container.json | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('agentGroupId',''))")
     onecli agents set-secret-mode --id "$AGENT_ID" --mode all
     echo "Done! Tell your assistant you're ready."
     ```
   - Send the user this message via `send_message`:
     "I've set up the configuration. Please run this command in your terminal:
     `bash ~/nanoclaw/groups/$(hostname)/.../.amplifier-setup.sh`
     Then reply 'done' and I'll switch over."
   - Wait for user to reply "done"
   - Update the agent's provider config via bash: `ncl groups config update --id $AGENT_ID --provider openai --model gpt-4o`
   - Restart: `ncl groups restart --id $AGENT_ID --message "Switching to OpenAI"`

   **For local providers (Ollama, vLLM):**
   - Ask for the host URL (e.g., http://192.168.1.10:11434)
   - Update container.json mcpServers with `_amplifier_config` entry
   - Update provider field via `ncl groups config update`
   - Restart

3. Confirm the switch was successful with a `send_message`.

## Notes

- The actual API key is NEVER stored in the chat or in CLAUDE.local.md
- The setup script handles secure storage in OneCLI vault
- Provider changes take effect after container restart
