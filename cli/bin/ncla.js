#!/usr/bin/env node
/**
 * ncla - nanoclaw-amplifier companion CLI
 * Usage:
 *   ncla add-provider [openai|gemini|ollama|...]
 *   ncla status
 *   ncla upgrade
 */
import { execSync, spawnSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import * as p from '@clack/prompts';
import k from 'kleur';

const [,, cmd, ...args] = process.argv;

const PROVIDERS = {
  anthropic:  { name: 'Anthropic (Claude)', host: 'api.anthropic.com',                   header: 'x-api-key', fmt: '{value}',       model: 'claude-opus-4-5' },
  openai:     { name: 'OpenAI (GPT)',       host: 'api.openai.com',                       header: 'Authorization', fmt: 'Bearer {value}', model: 'gpt-4o' },
  gemini:     { name: 'Google Gemini',      host: 'generativelanguage.googleapis.com',    header: 'x-goog-api-key', fmt: '{value}',   model: 'gemini-2.0-flash' },
  azure:      { name: 'Azure OpenAI',       host: null, /* custom endpoint */             header: 'api-key', fmt: '{value}',           model: null },
  ollama:     { name: 'Ollama (local)',      host: null, /* local, no auth needed */      header: null, fmt: null,                      model: 'llama3' },
};

async function cmdAddProvider(providerArg) {
  p.intro(k.cyan('nanoclaw-amplifier') + ' / add provider');

  let providerKey = providerArg;
  if (!providerKey || !PROVIDERS[providerKey]) {
    const choice = await p.select({
      message: 'Which provider do you want to add?',
      options: Object.entries(PROVIDERS).map(([k, v]) => ({ value: k, label: v.name })),
    });
    if (p.isCancel(choice)) { p.cancel('Cancelled'); process.exit(0); }
    providerKey = choice;
  }

  const provider = PROVIDERS[providerKey];

  if (providerKey === 'ollama') {
    const host = await p.text({ message: 'Ollama host URL:', placeholder: 'http://host.docker.internal:11434' });
    if (p.isCancel(host)) { p.cancel('Cancelled'); process.exit(0); }
    p.note(
      `Run these in your nanoclaw directory:\n\n` +
      `  AGENT_ID=$(cat groups/*/container.json 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('agentGroupId',''))" 2>/dev/null | head -1)\n` +
      `  ncl groups config update --id $AGENT_ID --provider ollama --model ${provider.model}\n` +
      `  # Then set OLLAMA_HOST in your agent's _amplifier_config MCP entry\n` +
      `  ncl groups restart --id $AGENT_ID`,
      'Next steps'
    );
    p.outro(k.green('Done!'));
    return;
  }

  const apiKey = await p.password({ message: `Enter your ${provider.name} API key:` });
  if (p.isCancel(apiKey)) { p.cancel('Cancelled'); process.exit(0); }

  const s = p.spinner();
  s.start('Registering secret in OneCLI vault...');
  try {
    execSync(
      `onecli secrets create --name "${provider.name}" --type generic ` +
      `--value "${apiKey}" --host-pattern "${provider.host}" ` +
      `--header-name "${provider.header}" --value-format "${provider.fmt}"`,
      { stdio: 'pipe' }
    );
    s.stop('Secret registered');
  } catch (e) {
    s.stop('Failed to register secret');
    p.log.error('onecli command failed. Is OneCLI running?');
    p.log.info('Try: docker ps | grep onecli');
    process.exit(1);
  }

  p.note(
    `Run this in your nanoclaw directory to switch the agent:\n\n` +
    `  AGENT_ID=$(cat groups/*/container.json 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('agentGroupId',''))" 2>/dev/null | head -1)\n` +
    `  onecli agents set-secret-mode --id $AGENT_ID --mode all\n` +
    `  ncl groups config update --id $AGENT_ID --provider ${providerKey} --model ${provider.model || 'YOUR_MODEL'}\n` +
    `  ncl groups restart --id $AGENT_ID`,
    'Switch agent'
  );

  p.outro(k.green('Provider configured! Follow the steps above to activate it.'));
}

async function cmdStatus() {
  p.intro(k.cyan('nanoclaw-amplifier') + ' / status');
  // Read .env for CONTAINER_IMAGE
  const envPath = join(process.cwd(), '.env');
  if (existsSync(envPath)) {
    const env = readFileSync(envPath, 'utf-8');
    const match = env.match(/^CONTAINER_IMAGE=(.+)$/m);
    if (match) p.log.info('Container image: ' + match[1]);
  }
  p.outro('Use ncla add-provider to configure additional providers.');
}

async function cmdUpgrade() {
  p.intro(k.cyan('nanoclaw-amplifier') + ' / upgrade');
  const s = p.spinner();
  s.start('Pulling latest image...');
  const r = spawnSync('docker', ['pull', 'ghcr.io/bkrabach/nanoclaw-amplifier:latest'], { stdio: 'pipe' });
  if (r.status === 0) s.stop('Image updated');
  else { s.stop('Pull failed'); p.log.warn('Could not pull image. Check docker access.'); }
  p.outro('Restart nanoclaw to apply: systemctl restart nanoclaw');
}

switch (cmd) {
  case 'add-provider': await cmdAddProvider(args[0]); break;
  case 'status':       await cmdStatus();             break;
  case 'upgrade':      await cmdUpgrade();            break;
  default:
    console.log(`ncla - nanoclaw-amplifier CLI

Commands:
  add-provider [provider]   Configure an AI provider (openai, gemini, ollama, ...)
  status                    Show current configuration
  upgrade                   Pull latest container image
`);
}
