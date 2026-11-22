import assert from 'node:assert/strict';
import test from 'node:test';
import { ORB_SIZE, orbHome, orbVisit } from '../../desktop/ui/lib/geometry.mjs';
import { BASE, EASE, HELD, REST, VISIT_BOX, VISIT_SCALE, bowPoint, flightMs, placement, plan, sample, totalMs }
  from '../../desktop/ui/renderer/lib/motion.mjs';

const WORK = { x: 0, y: 0, width: 1920, height: 1032 };
const BROWSER = { x: -8, y: -8, width: 1936, height: 1048 };
const home = orbHome(WORK);
const homeCenter = { x: home.x + REST, y: home.y + REST };
const box = orbVisit(BROWSER, WORK, VISIT_BOX);
const middle = { x: box.x + HELD, y: box.y + HELD };

function arrival() {
  return plan({ id: 1, from: homeCenter, to: middle, offset: { from: { x: REST, y: REST }, to: { x: HELD, y: HELD } },
                scale: { from: 1, to: VISIT_SCALE } });
}

function departure() {
  return plan({ id: 2, from: middle, to: homeCenter, offset: { from: { x: HELD, y: HELD }, to: { x: REST, y: REST } },
                scale: { from: VISIT_SCALE, to: 1 }, returning: true });
}

test('the lock in orb lands in the middle of the browser window', () => {
  assert.deepEqual(orbVisit(BROWSER, WORK), { x: 894, y: 450, width: ORB_SIZE, height: ORB_SIZE });
  assert.equal(ORB_SIZE, BASE);
  assert.deepEqual(middle, { x: 960, y: 516 });
});

test('the lock in orb stays fully inside the browser monitor work area', () => {
  const work = { x: -1920, y: 52, width: 1920, height: 1032 };
  for (const target of [{ x: -1990, y: 1000, width: 100, height: 300 }, { x: -60, y: 0, width: 100, height: 60 }]) {
    for (const size of [ORB_SIZE, VISIT_BOX]) {
      const bounds = orbVisit(target, work, size);
      assert.ok(bounds.x >= work.x && bounds.x + bounds.width <= work.x + work.width, JSON.stringify(bounds));
      assert.ok(bounds.y >= work.y && bounds.y + bounds.height <= work.y + work.height, JSON.stringify(bounds));
    }
  }
});

test('the flight starts exactly where the resting orb is, so nothing jumps when the window grows', () => {
  const start = placement(sample(arrival(), 0));
  assert.deepEqual({ x: start.x, y: start.y }, { x: home.x, y: home.y });
  assert.deepEqual({ cx: start.cx, cy: start.cy, scale: start.scale }, { cx: REST, cy: REST, scale: 1 });
});

test('Jarvis glides up into the middle of the page and grows there', () => {
  const spec = arrival();
  const end = placement(sample(spec, totalMs(spec)));
  assert.deepEqual({ x: end.x, y: end.y, width: VISIT_BOX, height: VISIT_BOX }, box);
  assert.deepEqual({ cx: end.cx, cy: end.cy }, { cx: HELD, cy: HELD });
  assert.ok(Math.abs(end.scale - VISIT_SCALE) < 1e-9, `ends at ${end.scale}`);
  const halfway = sample(spec, spec.ms / 2);
  assert.ok(halfway.center.y < homeCenter.y - 150, 'by halfway he has clearly risen off the corner');
  assert.ok(halfway.scale < 1.25, 'he grows as he arrives, not while still in the corner');
});

test('the glide is continuous: no frame teleports and the window never clips the orb', () => {
  for (const spec of [arrival(), departure()]) {
    let before = sample(spec, 0);
    let peak = 0;
    for (let elapsed = 1; elapsed <= totalMs(spec) + 1; elapsed += 1) {
      const state = sample(spec, elapsed);
      const step = Math.hypot(state.center.x - before.center.x, state.center.y - before.center.y);
      assert.ok(step < 6, `moved ${step.toFixed(1)}px in 1ms at ${elapsed}ms`);
      assert.ok(Math.abs(state.scale - before.scale) < 0.01, `scale jumped at ${elapsed}ms`);
      const place = placement(state);
      const radius = REST * place.scale;
      assert.ok(place.cx - radius >= -1e-9 && place.cy - radius >= -1e-9, `clipped top/left at ${elapsed}ms`);
      assert.ok(place.cx + radius <= VISIT_BOX + 1e-9 && place.cy + radius <= VISIT_BOX + 1e-9, `clipped bottom/right at ${elapsed}ms`);
      peak = Math.max(peak, state.scale);
      before = state;
    }
    if (!spec.land) assert.ok(peak > VISIT_SCALE && peak < VISIT_SCALE + 0.06, `pop overshoot ${peak}`);
  }
});

test('he flies home and lands at his normal size exactly where he started', () => {
  const spec = departure();
  const end = sample(spec, totalMs(spec));
  const place = placement(end);
  assert.ok(end.done && spec.land);
  assert.deepEqual({ x: place.x, y: place.y, cx: place.cx, cy: place.cy, scale: place.scale },
                   { x: home.x, y: home.y, cx: REST, cy: REST, scale: 1 });
});

test('the path arcs upward instead of sliding straight across', () => {
  const control = bowPoint(homeCenter, middle);
  const chord = { x: (homeCenter.x + middle.x) / 2, y: (homeCenter.y + middle.y) / 2 };
  assert.ok(control.y < chord.y - 40, JSON.stringify({ control, chord }));
  assert.deepEqual(bowPoint({ x: 10, y: 900 }, { x: 10, y: 100 }), { x: 10, y: 500 }, 'straight up needs no arc');
});

test('flights take long enough to see and are never sluggish', () => {
  for (const [from, to] of [[homeCenter, middle], [homeCenter, homeCenter], [{ x: 0, y: 0 }, { x: 6000, y: 3000 }]]) {
    const out = flightMs(from, to);
    const back = flightMs(to, from, true);
    assert.ok(out >= 480 && out <= 860, String(out));
    assert.ok(back >= 420 && back <= 760, String(back));
  }
  assert.ok(totalMs(arrival()) < 1000, 'the whole arrival including the grow settles within a second');
});

test('the easing curves start at rest, end on target and do not wobble', () => {
  for (const name of ['glide', 'home', 'shrink']) {
    let before = 0;
    for (let index = 0; index <= 100; index += 1) {
      const value = EASE[name](index / 100);
      assert.ok(value >= before - 1e-9, `${name} went backward at ${index}`);
      before = value;
    }
    assert.equal(EASE[name](0), 0);
    assert.equal(EASE[name](1), 1);
  }
  assert.equal(EASE.pop(0), 0);
  assert.equal(EASE.pop(1), 1);
  assert.ok(EASE.pop(0.65) > 1, 'the pop overshoots before settling');
});
