import assert from 'node:assert/strict';
import test from 'node:test';
import { follow } from '../../desktop/ui/renderer/lib/meter.js';

const FRAME = 1 / 30;

test('the bar rises faster than it falls', () => {
  const rise = follow(0, 1, FRAME);
  const fall = follow(1, 0, FRAME);
  assert.ok(Math.abs(rise - 0.6) < 1e-9, `expected 60% of the way up, got ${rise}`);
  assert.ok(Math.abs(fall - 0.85) < 1e-9, `expected 15% of the way down, got ${fall}`);
  assert.ok(1 - rise < fall, 'a rise must close more of the gap than a fall');
});

test('the bar settles exactly on its target so the animation can stop', () => {
  assert.equal(follow(0.999, 1, FRAME), 1);
  assert.equal(follow(0.001, 0, FRAME), 0);
  let shown = 1;
  for (let step = 0; step < 120 && shown !== 0; step += 1) shown = follow(shown, 0, 1 / 60);
  assert.equal(shown, 0, 'a falling bar must reach zero within two seconds');
});

test('a stalled frame cannot jump the bar and a bad clock cannot move it', () => {
  assert.equal(follow(0, 1, 5), follow(0, 1, 0.1));
  assert.equal(follow(0.3, 1, 0), 0.3);
  assert.equal(follow(0.3, 1, -2), 0.3);
});
