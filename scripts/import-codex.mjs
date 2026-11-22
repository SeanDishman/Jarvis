import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const packageRoot = path.join(root, '.runtime/openclaw/node_modules/openclaw');
if (JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8')).version !== '2026.9.9') {
  throw new Error('This credential importer is pinned to OpenClaw 2026.9.9.');
}
process.env.OPENCLAW_STATE_DIR = path.join(root, '.openclaw');
process.env.OPENCLAW_CONFIG_PATH = path.join(root, '.openclaw/openclaw.json');
const { n: readCodexCliCredentialsCached } = await import(pathToFileURL(path.join(packageRoot, 'dist/cli-credentials-BaCubPeC.mjs')));
const { upsertAuthProfileWithLock, loadAuthProfileStoreWithoutExternalProfiles } = await import(pathToFileURL(path.join(packageRoot, 'dist/auth-profiles-BK4ah3aF.mjs')));
const profileId = 'openai:codex';
const existing = loadAuthProfileStoreWithoutExternalProfiles();
if (existing.profiles?.[profileId]) {
  console.log('OpenClaw already owns the Codex sign-in; existing credentials preserved.');
} else {
  const credential = readCodexCliCredentialsCached({ codexHome: path.join(os.homedir(), '.codex'), ttlMs: 0, allowKeychainPrompt: false });
  if (!credential || credential.type !== 'oauth') throw new Error('No ChatGPT/Codex sign-in found.');
  const updated = await upsertAuthProfileWithLock({ profileId, credential: { ...credential, provider: 'openai' }, stateDir: process.env.OPENCLAW_STATE_DIR });
  if (!updated) throw new Error('Could not save the Codex sign-in in the OpenClaw credential store.');
  console.log('Codex sign-in imported into OpenClaw. No source credentials changed.');
}
