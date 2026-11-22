import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {desktopTools, jarvisSettingsContext, toolsFor} from '../../desktop/llm-tools.mjs';
import {intentFormat} from '../../desktop/intent-prompts.mjs';
import {desktopReply} from '../../desktop/model-output.mjs';

test('Semantic grants use the same actual tool catalog on both hosts', () => {
  const scopes = JSON.parse(fs.readFileSync(new URL('../../desktop/tool-scopes.json', import.meta.url), 'utf8'));
  const tools = desktopTools.map(tool => tool.name).sort();
  assert.deepEqual(Object.values(scopes).flat().sort(), tools);
  assert.deepEqual([...intentFormat.schema.properties.capabilities.items.enum].sort(), tools);
  assert.deepEqual(toolsFor(['windows_control','invented_tool']).map(tool => tool.name), ['windows_control']);
  assert.deepEqual(toolsFor([]), []);
});

test('Jarvis sees his live settings and saved models, and only the owner can grant a settings change', () => {
  const live = 'Jarvis settings, read live from your settings just now.\nLock in mode is on.';
  const block = jarvisSettingsContext(live, {conversationModel: 'gpt-6-luna', searchModel: 'same'});
  assert.ok(block.startsWith('\nJarvis settings, read live'));
  assert.match(block, /Lock in mode is on\./);
  assert.match(block, /Saved models: conversation gpt-6-luna, background search same as conversation\. Settings > Models\./);
  assert.match(jarvisSettingsContext(live, {conversationModel: 'a', searchModel: 'b'}), /background search b\./);
  assert.equal(jarvisSettingsContext(live, null), '\n' + live);
  for (const missing of [undefined, null, '', '   ', 42, {text: live}]) assert.equal(jarvisSettingsContext(missing, {conversationModel: 'a'}), '');
  assert.equal(jarvisSettingsContext('x'.repeat(9000), null).length, 6001);
  const change = desktopTools.find(tool => tool.name === 'change_jarvis_setting');
  assert.deepEqual(change.parameters.properties.setting.enum, ['lock_in_mode','battery_alerts','daily_briefing','weather_units','speech_recognition']);
  assert.deepEqual(toolsFor([]).map(tool => tool.name), [], 'Questions about settings get no write tool');
  const pass = desktopTools.find(tool => tool.name === 'allow_lock_in_visit');
  assert.deepEqual(pass.parameters.properties.site.enum, ['x','instagram','any']);
  assert.deepEqual(pass.parameters.required, ['site']);
});

test('The spoken reply preserves the model\'s explicit failure or clarification outcome', () => {
  for (const status of ['failed','needs_clarification','completed','started']) {
    const result = {reply:'An evidence-based reply.',status};
    assert.deepEqual(desktopReply({content:[{type:'text',text:JSON.stringify(result)}]}),result);
  }
  assert.throws(() => desktopReply({content:[{type:'text',text:'Working on it.'}]}));
  assert.throws(() => desktopReply({content:[{type:'text',text:'{"reply":"Done","status":"invented"}'}]}));
});
