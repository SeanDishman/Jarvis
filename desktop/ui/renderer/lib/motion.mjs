// The lock-in visit: Jarvis glides from his corner into the middle of the browser, grows, and later glides home.
// The orb view runs this every frame and the window manager plans it, so both sides share these numbers.
// Screen positions are DIPs. Offsets are CSS pixels inside the orb window.

export const BASE = 132;
export const REST = BASE / 2;
export const VISIT_SCALE = 1.7;
// Big enough for the grown orb plus the small overshoot as it lands.
export const VISIT_BOX = 232;
export const HELD = VISIT_BOX / 2;
// Welcoming the owner back, he grows further, in the middle of the main monitor.
export const WELCOME_SCALE = 2.6;
export const MAX_VISIT_SCALE = 3;

// The window a visit needs: the grown orb plus the pop's overshoot, which peaks about 5% past the growth.
// Always an even number of pixels, so his centre sits on a whole pixel and the held orb never shifts.
export function boxFor(scale = VISIT_SCALE) {
  if (scale === VISIT_SCALE) return VISIT_BOX;
  return 2 * Math.ceil((BASE * (scale + (scale - 1) * 0.09) + 2) / 2);
}

export function visitScale(value) {
  const scale = Number(value);
  return Number.isFinite(scale) && value !== null && value !== undefined
    ? Math.min(Math.max(scale, 1.2), MAX_VISIT_SCALE) : VISIT_SCALE;
}

const clamp01 = (value) => Math.min(1, Math.max(0, value));
const lerp = (from, to, amount) => from + (to - from) * amount;

export function cubicBezier(x1, y1, x2, y2) {
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  const curveX = (t) => ((ax * t + bx) * t + cx) * t;
  const curveY = (t) => ((ay * t + by) * t + cy) * t;
  const slopeX = (t) => (3 * ax * t + 2 * bx) * t + cx;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let step = 0; step < 8; step += 1) {
      const error = curveX(t) - x;
      if (Math.abs(error) < 1e-6) return curveY(t);
      const slope = slopeX(t);
      if (Math.abs(slope) < 1e-6) break;
      t -= error / slope;
    }
    let low = 0;
    let high = 1;
    t = x;
    for (let step = 0; step < 40 && high - low > 1e-7; step += 1) {
      if (curveX(t) < x) low = t;
      else high = t;
      t = (low + high) / 2;
    }
    return curveY(t);
  };
}

export const EASE = {
  // Leaves the corner gently and settles softly in the middle of the page.
  glide: cubicBezier(0.45, 0, 0.2, 1),
  home: cubicBezier(0.4, 0, 0.25, 1),
  shrink: cubicBezier(0.4, 0, 0.6, 1),
  // Grows a little past full size, then settles.
  pop(t) {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    const overshoot = 1.2;
    const u = t - 1;
    return 1 + (overshoot + 1) * u * u * u + overshoot * u * u;
  },
};

export function flightMs(from, to, returning = false) {
  const distance = Math.hypot(to.x - from.x, to.y - from.y);
  const ms = returning ? 360 + distance * 0.2 : 420 + distance * 0.24;
  return Math.round(Math.min(Math.max(ms, returning ? 420 : 480), returning ? 760 : 860));
}

// A gentle upward arc, so he is lifted toward the page rather than slid across the screen.
export function bowPoint(from, to, bow = 0.12) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy);
  const middle = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
  if (length < 1) return middle;
  let nx = -dy / length;
  let ny = dx / length;
  if (ny > 0) {
    nx = -nx;
    ny = -ny;
  }
  if (Math.abs(ny) < 0.2) return middle;
  return { x: middle.x + nx * bow * length, y: middle.y + ny * bow * length };
}

export function along(from, control, to, amount) {
  const rest = 1 - amount;
  return { x: rest * rest * from.x + 2 * rest * amount * control.x + amount * amount * to.x,
           y: rest * rest * from.y + 2 * rest * amount * control.y + amount * amount * to.y };
}

export function plan({ id, from, to, offset, scale, returning = false }) {
  const ms = flightMs(from, to, returning);
  const growing = scale.to > scale.from;
  return {
    id, ms, land: Boolean(returning), ease: returning ? 'home' : 'glide',
    from, to, control: bowPoint(from, to),
    offset: { from: { ...offset.from }, to: { ...offset.to } },
    scale: growing ? { from: scale.from, to: scale.to, delay: Math.round(ms * 0.45), ms: 520, ease: 'pop' }
      : { from: scale.from, to: scale.to, delay: 0, ms: Math.round(ms * 0.75), ease: 'shrink' },
  };
}

export function totalMs(spec) {
  return Math.max(spec.ms, spec.scale.delay + spec.scale.ms);
}

export function sample(spec, elapsed) {
  const progress = clamp01(elapsed / Math.max(1, spec.ms));
  const eased = EASE[spec.ease](progress);
  const grown = EASE[spec.scale.ease](clamp01((elapsed - spec.scale.delay) / Math.max(1, spec.scale.ms)));
  return {
    center: along(spec.from, spec.control, spec.to, eased),
    offset: { x: lerp(spec.offset.from.x, spec.offset.to.x, eased), y: lerp(spec.offset.from.y, spec.offset.to.y, eased) },
    scale: lerp(spec.scale.from, spec.scale.to, grown),
    arrived: progress >= 1,
    done: elapsed >= totalMs(spec),
  };
}

// Where the orb window goes (whole DIPs) and where to draw the orb inside it, so his centre stays exactly on the
// path while the window snaps to whole pixels. The orb is never drawn past the edge of its window.
export function placement(state, box = VISIT_BOX) {
  const x = Math.round(state.center.x - state.offset.x);
  const y = Math.round(state.center.y - state.offset.y);
  const cx = state.center.x - x;
  const cy = state.center.y - y;
  const room = Math.min(cx, cy, box - cx, box - cy) / REST;
  return { x, y, cx, cy, scale: Math.max(0.2, Math.min(state.scale, room)) };
}
