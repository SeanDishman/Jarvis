import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { ModelSettings, visibleModels, validateSelection, adaptiveSelection } from '../../desktop/models.mjs';

test('Automatic model choice uses available families without downgrading explicit defaults', () => {
  const rows = ['gpt-6.1-sol','gpt-6-astra','gpt-6-sol','gpt-6-luna'].map(id => ({id}));
  assert.equal(adaptiveSelection(rows, 'gpt-6-luna', 'default').row.id, 'gpt-6-luna');
  assert.equal(adaptiveSelection(rows, 'gpt-6-luna', 'sol').row.id, 'gpt-6.1-sol');
  assert.equal(adaptiveSelection(rows, 'gpt-6-luna', 'astra').row.id, 'gpt-6-astra');
  assert.equal(adaptiveSelection(rows, 'gpt-6-astra', 'sol').row.id, 'gpt-6-astra');
  const absent = adaptiveSelection(rows.filter(row => !row.id.includes('astra')), 'gpt-6-luna', 'astra');
  assert.equal(absent.row.id, 'gpt-6.1-sol');
  assert.equal(absent.fallback, true);
  assert.throws(() => adaptiveSelection(rows, 'not-authorized', 'astra'));
});

const catalog = { models: [
  { slug: 'gpt-6-luna', display_name: 'Luna', visibility: 'list', supported_reasoning_levels: [{effort: 'low'}] },
  { slug: 'gpt-6-sol', display_name: 'Sol', visibility: 'list', supported_reasoning_levels: [{effort: 'medium'}, {effort: 'low'}] },
  { slug: 'hidden-admin', display_name: 'Hidden', visibility: 'hide' },
] };

test('Only visible signed-in models can be selected', () => {
  const models = visibleModels(catalog);
  assert.deepEqual(models.map(row => row.id), ['gpt-6-luna', 'gpt-6-sol']);
  assert.throws(() => validateSelection({conversationModel: 'hidden-admin', searchModel: 'same'}, models));
  assert.throws(() => validateSelection({conversationModel: 'gpt-6-luna', searchModel: 'unknown'}, models));
});

test('Choices persist, research can differ, and invalid choices do not silently replace a model', async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const temp = await fs.mkdtemp(path.join(root, '.cache/model-test-'));
  const catalogFile = path.join(temp, 'catalog.json');
  const directory = path.join(temp, 'desktop');
  const saved = path.join(directory, 'model-settings.json');
  try {
    await fs.mkdir(directory);
    await fs.writeFile(catalogFile, JSON.stringify(catalog));
    const settings = new ModelSettings(temp, catalogFile);
    await settings.save({conversationModel: 'gpt-6-sol', searchModel: 'gpt-6-luna'});
    const reloaded = new ModelSettings(temp, catalogFile);
    assert.equal((await reloaded.resolve()).model.id, 'gpt-6-sol');
    assert.equal((await reloaded.resolve('research')).model.id, 'gpt-6-luna');
    await assert.rejects(reloaded.save({conversationModel: 'made-up-model', searchModel: 'same'}));
    assert.equal((await reloaded.resolve()).model.id, 'gpt-6-sol');
    await reloaded.save({conversationModel: 'gpt-6-luna', searchModel: 'same'});
    const automatic = await reloaded.resolve('conversation', undefined, 'sol');
    assert.equal(automatic.model.id, 'gpt-6-sol');
    assert.equal(automatic.effort, 'medium');
    assert.equal((await reloaded.settings()).conversationModel, 'gpt-6-luna');
    assert.equal((await reloaded.resolve('research', 'gpt-6-sol')).model.id, 'gpt-6-sol');
    assert.equal((await reloaded.resolve('research')).model.id, 'gpt-6-luna');
    await assert.rejects(reloaded.resolve('research', 'unknown'));
    assert.equal((await reloaded.describe()).claudeBackup, true, 'the Claude backup is on until it is turned off');
    await reloaded.save({conversationModel: 'gpt-6-luna', searchModel: 'same', claudeBackup: false});
    await reloaded.save({conversationModel: 'gpt-6-sol', searchModel: 'same'});
    assert.equal((await reloaded.describe()).claudeBackup, false, 'changing only the model keeps the backup choice');
  } finally {
    await fs.unlink(saved).catch(() => {});
    await fs.unlink(catalogFile).catch(() => {});
    await fs.rmdir(directory);
    await fs.rmdir(temp);
  }
});
