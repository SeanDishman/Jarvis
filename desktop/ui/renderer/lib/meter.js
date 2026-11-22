import { h } from './dom.js';

// Rises quickly so short words register and falls slowly so the bar does not flicker.
const ATTACK = 0.4;
const RELEASE = 0.85;
const SETTLED = 0.002;
const SPOKEN = { muted: 'Muted', unavailable: 'Unavailable' };

export function follow(shown, target, seconds) {
  const frames = Math.min(Math.max(seconds, 0), 0.1) * 30;
  const next = shown + (target - shown) * (1 - Math.pow(target > shown ? ATTACK : RELEASE, frames));
  return Math.abs(next - target) < SETTLED ? target : next;
}

export function levelMeter(label) {
  const element = h('div', { class: 'meter', role: 'meter', 'aria-label': label, 'aria-valuemin': 0, 'aria-valuemax': 100,
                             'aria-valuenow': 0, 'data-state': 'live' }, h('div', { class: 'meter-fill' }));
  let target = 0;
  let shown = 0;
  let before = 0;
  let frame = 0;
  let announced = 0;

  const draw = (now) => {
    shown = follow(shown, target, (now - before) / 1000);
    before = now;
    element.style.setProperty('--level', shown.toFixed(3));
    frame = shown === target ? 0 : requestAnimationFrame(draw);
  };

  element.update = ({ level = 0, status = 'live' } = {}) => {
    const live = status === 'live';
    target = live ? Math.min(1, Math.max(0, Number(level) || 0)) : 0;
    element.dataset.state = status;
    if (SPOKEN[status]) element.setAttribute('aria-valuetext', SPOKEN[status]);
    else element.removeAttribute('aria-valuetext');
    const percent = Math.round(target * 20) * 5;
    if (percent !== announced) {
      announced = percent;
      element.setAttribute('aria-valuenow', String(percent));
    }
    if (!frame) {
      before = performance.now();
      frame = requestAnimationFrame(draw);
    }
  };

  element.destroy = () => {
    cancelAnimationFrame(frame);
    frame = 0;
  };
  return element;
}
