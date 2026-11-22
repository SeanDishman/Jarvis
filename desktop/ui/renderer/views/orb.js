import { call, signal, ui } from '../lib/bridge.js';
import { h } from '../lib/dom.js';
import { REST, VISIT_BOX, placement, sample } from '../lib/motion.mjs';
import { OrbRenderer } from '../orb/orb-renderer.js';

const SIZE = 132;
const REACH = 52;

export default function start(store) {
  const canvas = h('canvas', { class: 'orb-canvas', 'aria-label': 'Jarvis' });
  document.body.append(canvas);
  const orb = new OrbRenderer(canvas, { size: SIZE, fill: true });
  // The orb keeps its place in the window when the window grows for a lock-in visit, so nothing jumps.
  orb.setLayout({ cx: REST, cy: REST, scale: 1 });
  const sync = () => {
    orb.setState(store.state.state?.state);
    orb.setBusy(store.state.jobs?.busy);
  };
  sync();
  if (!store.state.hidden) orb.start();

  // A lock-in visit: the window manager plans the flight, this view draws it every frame and tells the window
  // manager where the window should be, so the window moves in step with the screen refresh.
  let motion = null;
  const settle = (layout) => {
    motion = null;
    orb.moving = false;
    orb.setLayout(layout);
  };
  orb.onFrame = (time) => {
    if (!motion) return;
    if (motion.start === null) motion.start = time;
    const state = sample(motion.spec, time - motion.start);
    const place = placement(state, motion.spec.box || VISIT_BOX);
    orb.setLayout({ cx: place.cx, cy: place.cy, scale: place.scale });
    if (state.arrived && !motion.spec.land && !motion.pulsed) {
      motion.pulsed = true;
      orb.pulse();
    }
    if (place.x !== motion.x || place.y !== motion.y || state.done) {
      motion.x = place.x;
      motion.y = place.y;
      signal('orb.place', { id: motion.spec.id, x: place.x, y: place.y, done: state.done });
    }
    if (state.done) settle({ cx: place.cx, cy: place.cy, scale: place.scale });
  };
  const move = (data) => {
    if (data?.reset) settle({ cx: REST, cy: REST, scale: 1 });
    else if (data?.hold) settle({ cx: data.hold.cx, cy: data.hold.cy, scale: data.hold.scale });
    else if (data?.id && data.from && data.to) {
      motion = { spec: data, start: null, x: null, y: null, pulsed: false };
      orb.moving = true;
    }
  };

  store.on((name, data) => {
    if (name === 'state' || name === 'jobs') sync();
    else if (name === 'level') orb.setLevel(data);
    else if (name === 'pulse') orb.pulse();
    else if (name === 'orb_motion') move(data);
    else if (name === 'visibility') data?.hidden ? orb.stop() : orb.start();
  });
  window.addEventListener('resize', () => orb.resize());

  let captured = false;
  let hovering = false;
  let press = null;
  let pending = false;

  const inside = (event) => Math.hypot(event.clientX - orb.centerX, event.clientY - orb.centerY) <= REACH * orb.scale;
  const setCapture = (value) => {
    if (value === captured) return;
    captured = value;
    canvas.classList.toggle('pointer', value);
    signal('orb.capture', { capture: value });
  };
  const setHover = (value) => {
    if (value === hovering) return;
    hovering = value;
    orb.setHover(value);
  };

  window.addEventListener('mousemove', (event) => {
    if (press) return;
    const over = inside(event);
    setCapture(over);
    setHover(over);
  });
  document.addEventListener('mouseleave', () => {
    if (press) return;
    setCapture(false);
    setHover(false);
  });
  canvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || !inside(event)) return;
    press = { x: event.screenX, y: event.screenY, moved: false };
    canvas.setPointerCapture(event.pointerId);
    signal('orb.drag', { phase: 'start' });
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!press) return;
    if (!press.moved && Math.abs(event.screenX - press.x) + Math.abs(event.screenY - press.y) > 5) {
      press.moved = true;
      canvas.classList.add('dragging');
      setHover(false);
    }
    if (press.moved && !pending) {
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        signal('orb.drag', { phase: 'move' });
      });
    }
  });
  const release = (event, cancelled = false) => {
    if (!press) return;
    const { moved } = press;
    press = null;
    canvas.classList.remove('dragging');
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    signal('orb.drag', { phase: moved ? 'move' : 'end' });
    if (moved) signal('orb.drag', { phase: 'end' });
    if (!moved && !cancelled) call('activate', { source: 'orb' }).catch(() => {});
    const over = inside(event);
    setCapture(over);
    setHover(over);
  };
  canvas.addEventListener('pointerup', (event) => release(event));
  canvas.addEventListener('pointercancel', (event) => release(event, true));
  canvas.addEventListener('dblclick', (event) => {
    if (inside(event)) ui('open', { window: 'panel', focus: true }).catch(() => {});
  });
  window.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    if (!inside(event)) return;
    setHover(false);
    signal('menu.open');
  });
}
