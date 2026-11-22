import assert from 'node:assert/strict';
import test from 'node:test';
import { spokenAgo, stampOwnerMessage, timingNote } from '../../desktop/conversation-time.mjs';

test('each owner message tells the model when it was said, so a quick repeat is noticed', () => {
  const now = Date.UTC(2026, 10, 19, 20, 0, 0);
  const said = stampOwnerMessage({role: 'user', content: 'open X', timestamp: now - 15000}, now);
  assert.match(said.content, /^\[said \w{3} \d{1,2}:\d{2}:\d{2} (AM|PM), 15 seconds ago\] open X$/);
  assert.match(stampOwnerMessage({role: 'user', content: 'open X', timestamp: now - 3 * 3600e3}, now).content, /3 hours ago\] open X$/);
  const original = {role: 'user', content: 'open X', timestamp: now};
  stampOwnerMessage(original, now);
  assert.equal(original.content, 'open X', 'The stored message is never changed');
});

test('only plain owner messages are stamped', () => {
  const reply = {role: 'assistant', content: [{type: 'text', text: 'Done, sir.'}], timestamp: 1};
  const tool = {role: 'toolResult', content: [{type: 'text', text: '{}'}], timestamp: 1};
  const image = {role: 'user', content: [{type: 'text', text: 'look'}], timestamp: 1};
  for (const message of [reply, tool, image, {role: 'user', content: 'no time'}]) assert.equal(stampOwnerMessage(message), message);
});

test('times are spoken words, and the model is told never to read the brackets aloud', () => {
  assert.deepEqual([3000, 45000, 90000, 600000, 5400000, 18000000, 172800000].map(spokenAgo),
    ['just now', '45 seconds ago', 'a minute ago', '10 minutes ago', 'an hour ago', '5 hours ago', 'over a day ago']);
  assert.match(timingNote, /Never read the brackets/);
});
