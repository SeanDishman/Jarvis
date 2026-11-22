// Jarvis's own pointer, shown while he works on the computer. The window manager moves this little overlay; the
// arrow tip sits at (18, 14) inside it, and each action he performs there shows briefly around the tip.
const TIP = { x: 18, y: 14 };

const STYLE = `
  html, body { background: transparent; overflow: hidden; }
  .agent-pointer { position: fixed; inset: 0; pointer-events: none; }
  .agent-pointer svg { position: absolute; left: ${TIP.x - 3}px; top: ${TIP.y - 2}px; width: 30px; height: 38px;
    filter: drop-shadow(0 0 6px rgba(56, 214, 255, .75)) drop-shadow(0 2px 3px rgba(0, 0, 0, .55)); }
  .agent-pointer .ring { position: absolute; left: ${TIP.x}px; top: ${TIP.y}px; width: 0; height: 0; border-radius: 50%;
    border: 2px solid rgba(56, 214, 255, .95); transform: translate(-50%, -50%); opacity: 0; }
  .agent-pointer .ring.right { border-color: rgba(255, 176, 64, .95); }
  .agent-pointer .ring.go { animation: agent-ring 520ms cubic-bezier(.2, .7, .3, 1) forwards; }
  .agent-pointer .ring.late { animation-delay: 150ms; }
  .agent-pointer .wheel { position: absolute; left: ${TIP.x + 8}px; top: ${TIP.y + 14}px; font: 700 13px/1 system-ui;
    color: rgba(56, 214, 255, .95); opacity: 0; text-shadow: 0 0 6px rgba(56, 214, 255, .8); }
  .agent-pointer .wheel.go { animation: agent-wheel 600ms ease-out forwards; }
  .agent-pointer .hold { position: absolute; left: ${TIP.x}px; top: ${TIP.y}px; width: 24px; height: 24px; border-radius: 50%;
    border: 2px solid rgba(56, 214, 255, .95); background: rgba(56, 214, 255, .22); opacity: 0;
    transform: translate(-50%, -50%) scale(.3); transition: opacity 120ms ease-out, transform 160ms ease-out; }
  .agent-pointer .hold.on { opacity: 1; transform: translate(-50%, -50%) scale(1); }
  @keyframes agent-ring { 0% { width: 4px; height: 4px; opacity: 1; } 100% { width: 30px; height: 30px; opacity: 0; } }
  @keyframes agent-wheel { 0% { opacity: 1; transform: translateY(4px); } 100% { opacity: 0; transform: translateY(-6px); } }
`;

const ARROW = `<svg viewBox="0 0 30 38" aria-hidden="true">
  <defs><linearGradient id="jarvis-pointer" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#9ff0ff"/><stop offset="1" stop-color="#1fb8e8"/></linearGradient></defs>
  <path d="M3 2 L3 30 L10.5 23 L15.5 34.5 L20.5 32.3 L15.6 21.2 L25.5 21.2 Z" fill="url(#jarvis-pointer)"
        stroke="#06243a" stroke-width="1.8" stroke-linejoin="round"/>
  <circle cx="22.5" cy="31.5" r="3.2" fill="#ffffff" stroke="#1fb8e8" stroke-width="1.6"/>
</svg>`;

export default function start(store) {
  const style = document.createElement('style');
  style.textContent = STYLE;
  document.head.append(style);
  const root = document.createElement('div');
  root.className = 'agent-pointer';
  root.setAttribute('aria-label', 'Jarvis pointer');
  root.innerHTML = ARROW;
  const rings = [0, 1].map((index) => {
    const ring = document.createElement('span');
    ring.className = 'ring' + (index ? ' late' : '');
    root.append(ring);
    return ring;
  });
  const wheel = document.createElement('span');
  wheel.className = 'wheel';
  root.append(wheel);
  // Shown while he holds something, such as a window's title bar during a drag.
  const hold = document.createElement('span');
  hold.className = 'hold';
  root.append(hold);
  document.body.append(root);

  const replay = (element) => {
    element.classList.remove('go');
    void element.offsetWidth;
    element.classList.add('go');
  };
  const show = (action) => {
    hold.classList.toggle('on', action === 'grab');
    if (action === 'grab') return;
    if (action === 'scroll') {
      wheel.textContent = '⇅';
      replay(wheel);
      return;
    }
    if (action === 'type' || action === 'key' || action === 'set_value') {
      wheel.textContent = '⌨';
      replay(wheel);
      return;
    }
    const right = action === 'right_click';
    for (const ring of rings) ring.classList.toggle('right', right);
    replay(rings[0]);
    if (action === 'double_click') replay(rings[1]);
  };
  store.on((name, data) => {
    if (name === 'pointer_action') show(data?.action);
  });
}
