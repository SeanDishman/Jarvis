import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {acknowledgementContext} from '../../desktop/acknowledgement-prompts.mjs';
import {intentContext} from '../../desktop/intent-prompts.mjs';
import {interfaceTools} from '../../desktop/interface-tools.mjs';
import {lockInContext} from '../../desktop/lock-in-prompts.mjs';

const soul = fs.readFileSync(new URL('../../prompts/SOUL.md', import.meta.url), 'utf8');

test('Jarvis speaks about himself in the first person in every spoken prompt', () => {
  assert.match(soul, /Always speak about yourself in the first person/);
  assert.match(soul, /Never refer to yourself as Jarvis in the third person/);
  // These two prompts are sent without SOUL.md, so each carries the rule itself.
  for (const prompt of [acknowledgementContext, lockInContext]) {
    assert.ok(prompt.startsWith('You are Jarvis. Write your'), prompt.slice(0, 80));
    assert.match(prompt, /first person/);
  }
  assert.match(acknowledgementContext, /never moving Jarvis/);
});

test('Task steps and orb tools address Jarvis as you, so replies do not echo his name', () => {
  assert.match(intentContext, /calling it you or yourself rather than Jarvis/);
  for (const tool of interfaceTools) {
    assert.doesNotMatch(tool.description, /\bJarvis\b/i, tool.name);
  }
});
