import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

export const DEFAULT_MODEL = 'gpt-6-luna';

export function adaptiveSelection(models, baseId, tier = 'default') {
  if (!['default', 'sol', 'astra'].includes(tier)) throw new Error('Invalid model tier');
  const base = models.find(row => row.id === baseId);
  if (!base) throw new Error('The selected model is no longer in your Codex model list. Open Model settings.');
  const rank = id => /(?:^|-)astra(?:-|$)/.test(id) ? 2 : /(?:^|-)sol(?:-|$)/.test(id) ? 1 : 0;
  const desired = tier === 'astra' ? 2 : tier === 'sol' ? 1 : 0;
  if (!desired || rank(base.id) >= desired) return {row: base, fallback: false};
  const row = models.find(row => rank(row.id) === desired)
    || (desired === 2 && models.find(row => rank(row.id) === 1)) || base;
  return {row, fallback: rank(row.id) < desired};
}

export function visibleModels(catalog) {
  return (catalog.models ?? []).filter(row => row.visibility === 'list' && /^[a-z0-9][a-z0-9.-]{1,90}$/.test(row.slug))
    .map(row => ({ id: row.slug, name: row.display_name || row.slug,
      contextWindow: Number(row.context_window) || 272000,
      reasoning: (row.supported_reasoning_levels ?? []).map(level => level.effort) }));
}

export function validateSelection(settings, models) {
  const ids = new Set(models.map(row => row.id));
  if (!ids.has(settings.conversationModel)) throw new Error('Choose a conversation model from your signed-in model list.');
  if (settings.searchModel !== 'same' && !ids.has(settings.searchModel)) throw new Error('Choose a search model from your signed-in model list.');
  // claudeBackup: whether Claude takes over when OpenAI does not answer. On unless it was turned off.
  return { conversationModel: settings.conversationModel, searchModel: settings.searchModel,
    ...(typeof settings.claudeBackup === 'boolean' ? { claudeBackup: settings.claudeBackup } : {}) };
}

export class ModelSettings {
  constructor(root, catalogPath = path.join(os.homedir(), '.codex/models_cache.json'), baseUrl = 'https://chatgpt.com/backend-api') {
    this.file = path.join(root, 'desktop/model-settings.json');
    this.catalogPath = catalogPath;
    this.baseUrl = baseUrl;
    this.writeQueue = Promise.resolve();
  }
  async catalog() {
    try {
      const rows = visibleModels(JSON.parse((await fs.readFile(this.catalogPath, 'utf8')).replace(/^\uFEFF/, '')));
      if (rows.length) return rows;
    } catch {}
    return [{ id: DEFAULT_MODEL, name: 'GPT-6-Luna', contextWindow: 272000, reasoning: ['low'] }];
  }
  async settings() {
    try {
      return JSON.parse((await fs.readFile(this.file, 'utf8')).replace(/^\uFEFF/, ''));
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      return { conversationModel: DEFAULT_MODEL, searchModel: 'same' };
    }
  }
  async describe() {
    const [models, selected] = await Promise.all([this.catalog(), this.settings()]);
    return { models, ...selected, claudeBackup: selected.claudeBackup !== false,
      resolvedSearchModel: selected.searchModel === 'same' ? selected.conversationModel : selected.searchModel,
      authentication: 'Existing ChatGPT/Codex sign-in' };
  }
  async save(selected) {
    // A save that only changes the models, such as switching by voice, keeps the backup choice as it was.
    const kept = await this.settings().then(saved => typeof saved.claudeBackup === 'boolean' ? { claudeBackup: saved.claudeBackup } : {}, () => ({}));
    const clean = { ...kept, ...validateSelection(selected, await this.catalog()) };
    const write = this.writeQueue.catch(() => {}).then(async () => {
      await fs.writeFile(this.file + '.tmp', JSON.stringify(clean, null, 2) + '\n');
      await fs.rename(this.file + '.tmp', this.file);
      return clean;
    });
    this.writeQueue = write;
    return write;
  }
  async resolve(kind = 'conversation', pinned, tier = 'default') {
    const description = await this.describe();
    const id = pinned || (kind === 'research' ? description.resolvedSearchModel : description.conversationModel);
    const {row, fallback} = adaptiveSelection(description.models, id, tier);
    const wanted = tier === 'astra' ? 'high' : tier === 'sol' ? 'medium' : 'low';
    const effort = [wanted, 'medium', 'low', ...row.reasoning].find(value => row.reasoning.includes(value));
    return { model: {
      id: row.id, name: row.name, provider: 'openai', api: 'openai-chatgpt-responses', baseUrl: this.baseUrl,
      reasoning: row.reasoning.length > 0, input: ['text', 'image'], contextWindow: row.contextWindow, maxTokens: 8192,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    }, effort, fallback, requestedTier: tier };
  }
}
