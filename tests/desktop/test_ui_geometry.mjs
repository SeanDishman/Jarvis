import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { Backend } from '../../desktop/ui/lib/backend.mjs';
import { COLUMN, besideOrb, centered, companionBounds, fitImage, intersection, matchDisplay, menuPosition, orbHome,
         screenChoices, visibleEnough } from '../../desktop/ui/lib/geometry.mjs';

const primary = { x: 0, y: 0, width: 1920, height: 1032 };
const left = { x: -1920, y: 52, width: 1920, height: 1032 };

test('the orb keeps the original bottom-right corner placement', () => {
  assert.deepEqual(orbHome(primary), { x: 1783, y: 899, width: 132, height: 132 });
});

test('cards sit above the orb and stay inside a negative-coordinate monitor', () => {
  const orb = { x: -145, y: 907, width: 132, height: 132 };
  const bounds = companionBounds(orb, left, { width: 324, height: 560 }, { margin: 8 });
  assert.equal(bounds.anchor, 'above');
  assert.ok(bounds.y + bounds.height <= orb.y + 8);
  assert.ok(bounds.x >= left.x + 8 && bounds.x + bounds.width <= left.x + left.width - 8);
  const high = companionBounds({ x: -1900, y: 60, width: 132, height: 132 }, left, { width: 324, height: 560 });
  assert.equal(high.anchor, 'below');
  assert.ok(high.y >= 60 + 132 - 8);
  assert.ok(high.y + high.height <= left.y + left.height);
});

test('a docked panel never covers the card column above the orb', () => {
  const orb = orbHome(primary);
  const cards = companionBounds(orb, primary, { width: 324, height: 560 }, { margin: -4 });
  const panel = besideOrb(orb, primary, { width: 460, height: 640 });
  assert.equal(intersection(panel, { x: cards.x + 12, y: cards.y, width: COLUMN, height: cards.height }), 0);
  assert.ok(panel.y + panel.height <= orb.y + orb.height);
  const leftOrb = { x: 40, y: 600, width: 132, height: 132 };
  const leftPanel = besideOrb(leftOrb, primary, { width: 460, height: 640 });
  assert.ok(leftPanel.x > leftOrb.x + leftOrb.width / 2, 'Panel opens toward the open side of the screen');
});

test('the menu flips away from screen edges', () => {
  const size = { width: 288, height: 520 };
  assert.deepEqual(menuPosition({ x: 300, y: 200 }, primary, size, 14), { x: 286, y: 186 });
  const corner = menuPosition({ x: 1850, y: 960 }, primary, size, 14);
  assert.equal(corner.x + size.width - 14, 1850);
  assert.equal(corner.y + size.height - 14, 960);
});

test('screens are matched through DPI conversion and labelled for people', () => {
  const displays = [{ id: 1, label: 'DELL U2720Q', bounds: { x: 0, y: 0, width: 1280, height: 720 } },
                    { id: 2, label: '', bounds: { x: -1280, y: 35, width: 1280, height: 720 } }];
  const half = (rect) => ({ x: rect.x / 1.5, y: rect.y / 1.5, width: rect.width / 1.5, height: rect.height / 1.5 });
  const screens = [{ device: '\\\\.\\DISPLAY1', primary: true, internal: false, rect: [0, 0, 1920, 1080] },
                   { device: '\\\\.\\DISPLAY2', primary: false, internal: false, rect: [-1920, 52, 0, 1132] }];
  assert.equal(matchDisplay(screens[1], displays, half).id, 2);
  const choices = screenChoices({ screens, selected: '\\\\.\\DISPLAY2' }, displays, half);
  assert.deepEqual(choices.map((row) => [row.label, row.detail, row.selected]),
                   [['DELL U2720Q', 'Main display', false], ['Display 2', 'Display 2', true]]);
  const laptop = screenChoices({ screens: [{ ...screens[0], internal: true }], selected: null }, displays, half);
  assert.equal(laptop[0].label, 'Laptop screen');
});

test('saved window positions must still be reachable', () => {
  assert.ok(visibleEnough({ x: 100, y: 100, width: 800, height: 600 }, [primary]));
  assert.ok(!visibleEnough({ x: 4000, y: 100, width: 800, height: 600 }, [primary]));
  assert.ok(!visibleEnough({ x: 100, y: -590, width: 800, height: 600 }, [primary]));
  assert.deepEqual(centered(primary, { width: 900, height: 700 }), { x: 510, y: 166, width: 900, height: 700 });
  assert.deepEqual(fitImage({ width: 1920, height: 1080 }, { width: 900, height: 600 }), { width: 900, height: 506 });
});

test('the bridge pairs replies with calls and forwards events', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const events = [];
  let closed = false;
  const backend = new Backend({ onEvent: (name, data) => events.push([name, data]), onClose: () => { closed = true; } });
  const written = [];
  output.on('data', (chunk) => written.push(...chunk.toString().trim().split('\n')));
  const early = backend.call('snapshot');
  backend.attach(input, output);
  await new Promise((resolve) => setImmediate(resolve));
  const request = JSON.parse(written[0]);
  assert.equal(request.method, 'snapshot');
  input.write(JSON.stringify({ t: 'event', name: 'state', data: { state: 'ready' } }) + '\n');
  input.write('not json\n');
  input.write(JSON.stringify({ t: 'reply', id: request.id, ok: true, result: { hidden: false } }) + '\n');
  assert.deepEqual(await early, { hidden: false });
  assert.deepEqual(events, [['state', { state: 'ready' }]]);
  const failing = backend.call('job', { job: 'x' });
  await new Promise((resolve) => setImmediate(resolve));
  const second = JSON.parse(written[1]);
  input.write(JSON.stringify({ t: 'reply', id: second.id, ok: false, error: 'There is no matching task yet' }) + '\n');
  await assert.rejects(failing, /no matching task/);
  const pending = backend.call('models');
  input.end();
  await assert.rejects(pending, /Jarvis stopped/);
  assert.ok(closed);
});
