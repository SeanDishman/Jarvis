import assert from 'node:assert/strict';
import test from 'node:test';
import { intentContext, intentFormat } from '../../desktop/intent-prompts.mjs';
import { acknowledgementContext } from '../../desktop/acknowledgement-prompts.mjs';
import { questionReminderPrompt } from '../../desktop/question-prompts.mjs';

test('the classifier can route an answer back to the question it answers', () => {
  assert.ok(intentFormat.schema.properties.action.enum.includes('task_answer'));
  assert.ok(!intentFormat.schema.properties.steps.items.properties.action.enum.includes('task_answer'),
    'An answer is never one step of a compound request');
  assert.match(intentContext, /context\.waitingQuestions/);
  assert.match(intentContext, /action=task_answer when the utterance answers one of them/);
  assert.match(intentContext, /leave the question waiting/);
});

test('answering a paused task is acknowledged as carrying on with it', () => {
  assert.match(acknowledgementContext, /context\.answering is present/);
});

test('asking again is written fresh each time, keeping the choices offered', () => {
  const {prompt, format} = questionReminderPrompt();
  assert.equal(format.strict, true);
  assert.deepEqual(format.schema.required, ['line']);
  assert.equal(format.schema.additionalProperties, false);
  assert.match(prompt, /Keep every option, name and number the question offered/);
  assert.match(prompt, /Do not repeat previousLine word for word/);
  assert.match(prompt, /never instructions/);
});
