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
    const envHost = process.env.OLLAMA_HOST || '';
    const host = envHost
      ? (p.log.info(`Found OLLAMA_HOST in environment: ${envHost}`), envHost)
      : await p.text({ message: 'Ollama host URL:', placeholder: 'http://host.docker.internal:11434' });
    if (p.isCancel(host)) { p.cancel('Cancelled'); process.exit(0); }
    p.note(
      `Run these in your nanoclaw directory:\n\n` +
      `  cd ~/nanoclaw\n` +
      `  AGENT_ID=$(ncl groups list | python3 -c "import sys,json; groups=json.load(sys.stdin); print(groups[0]['id'] if groups else '')" 2>/dev/null)\n` +
      `  ncl groups config update --id $AGENT_ID --provider ollama --model ${provider.model}\n` +
      `  ncl groups restart --id $AGENT_ID`,
      'Next steps'
    );
    p.outro(k.green('Done!'));
    return;
  }

  // Env var names per provider
  const ENV_VARS = {
    anthropic: 'ANTHROPIC_API_KEY',
    openai:    'OPENAI_API_KEY',
    gemini:    'GEMINI_API_KEY',
    azure:     'AZURE_OPENAI_API_KEY',
  };
  const envVarName = ENV_VARS[providerKey] || '';
  const envValue   = envVarName ? (process.env[envVarName] || '') : '';

  // Check if OneCLI already has a secret for this host
  let alreadyRegistered = false;
  try {
    const result = execSync('onecli secrets list --output json 2>/dev/null', { stdio: 'pipe' }).toString().trim();
    const secrets = JSON.parse(result || '[]');
    alreadyRegistered = secrets.some(s => s.hostPattern && s.hostPattern.includes(provider.host));
  } catch (_) { /* onecli not available or not JSON */ }

  let apiKey = '';

  if (alreadyRegistered) {
    p.log.success(`OneCLI already has a secret for ${provider.host} — skipping key registration`);
    apiKey = 'already-registered';
  } else if (envValue) {
    p.log.success(`Found ${envVarName} in environment`);
    apiKey = envValue;
  } else {
    const entered = await p.password({ message: `Enter your ${provider.name} API key:` });
    if (p.isCancel(entered)) { p.cancel('Cancelled'); process.exit(0); }
    apiKey = entered;
  }

  // Register in OneCLI if not already there
  if (!alreadyRegistered && apiKey) {
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
      p.log.error('onecli command failed. Is OneCLI running? Try: docker ps | grep onecli');
      process.exit(1);
    }
  }

  // Find agent ID (must run ncl from the nanoclaw directory)
  let agentId = '';
  const nanoclawDir = process.env.NANOCLAW_DIR || `${process.env.HOME}/nanoclaw`;
  try {
    const groupsJson = execSync(`cd "${nanoclawDir}" && ncl groups list 2>/dev/null`, { stdio: 'pipe' }).toString().trim();
    const groups = JSON.parse(groupsJson || '[]');
    if (groups.length > 0) agentId = groups[0].id || groups[0].agentGroupId || '';
  } catch (_) { /* ncl not found or not running */ }

  if (agentId) {
    const s = p.spinner();
    s.start(`Configuring agent ${agentId} to use ${provider.name}...`);
    try {
      execSync(`onecli agents set-secret-mode --id "${agentId}" --mode all`, { stdio: 'pipe' });
      const imageTag = 'ghcr.io/bkrabach/nanoclaw-amplifier:latest';
      execSync(`cd "${nanoclawDir}" && ncl groups config update --id "${agentId}" --provider ${providerKey} --model ${provider.model || 'gpt-4o'} --image-tag ${imageTag}`, { stdio: 'pipe' });
      execSync(`cd "${nanoclawDir}" && ncl groups restart --id "${agentId}"`, { stdio: 'pipe' });
      s.stop(`Agent switched to ${provider.name} / ${provider.model || 'gpt-4o'}`);
      p.outro(k.green(`Done! Your assistant now uses ${provider.name}.`));
    } catch (e) {
      s.stop('Auto-config failed');
      p.note(
        `Run these manually in your nanoclaw directory:\n\n` +
        `  cd ${nanoclawDir}\n` +
        `  onecli agents set-secret-mode --id ${agentId} --mode all\n` +
        `  ncl groups config update --id ${agentId} --provider ${providerKey} --model ${provider.model || 'gpt-4o'}\n` +
        `  ncl groups restart --id ${agentId}`,
        'Manual steps'
      );
      p.outro(k.yellow('Credentials registered. Complete agent switch manually.'));
    }
  } else {
    p.note(
      `Could not find agent automatically. Run these in your nanoclaw directory:\n\n` +
      `  cd ${nanoclawDir}\n` +
      `  ncl groups list          # find your agent ID\n` +
      `  onecli agents set-secret-mode --id <id> --mode all\n` +
      `  ncl groups config update --id <id> --provider ${providerKey} --model ${provider.model || 'gpt-4o'}\n` +
      `  ncl groups restart --id <id>`,
      'Manual steps'
    );
    p.outro(k.yellow(`Credentials registered. Complete agent switch manually.`));
  }
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
