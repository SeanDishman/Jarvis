import { call, signal, ui } from '../lib/bridge.js';
import { h, hudText, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { applyState } from '../lib/ui.js';

export default function start(store) {
  const menu = h('div', { class: 'menu', role: 'menu', 'aria-label': 'Jarvis menu' });
  const shell = h('div', { class: 'menu-shell' }, menu);
  document.body.append(shell);
  let open = false;
  let buttons = [];

  const close = () => signal('menu.close');
  const run = (action) => () => {
    close();
    Promise.resolve().then(action).catch(() => {});
  };
  const openWindow = (window, view) => () => ui('open', { window, view, focus: true });

  function item(label, name, action, { hint, toggled, danger } = {}) {
    const button = h('button', { type: 'button', class: ['menu-item', danger && 'danger'], role: toggled === undefined ? 'menuitem' : 'menuitemcheckbox',
                                 'aria-checked': toggled === undefined ? null : String(toggled), tabIndex: -1 },
      icon(name), h('span', { class: 'label' }, label),
      hint ? h('span', { class: 'hint' }, hint) : null,
      toggled !== undefined ? h('span', { class: ['switch', toggled && 'on'] }) : null);
    button.addEventListener('click', run(action));
    return button;
  }
  const sep = () => h('div', { class: 'menu-sep', role: 'separator' });

  function status() {
    const state = store.state.state ?? {};
    applyState(state.state);
    return h('div', { class: 'menu-status' }, h('span', { class: 'dot' }),
      h('div', { style: { minWidth: 0 } }, h('div', { class: 'name' }, 'Jarvis · ' + hudText(state.name ?? 'Ready')),
        h('div', { class: 'note' }, state.note || 'Say Jarvis')));
  }

  function render() {
    const { muted, jobs } = store.state;
    const active = (jobs?.rows ?? []).filter((row) => row.status === 'queued' || row.status === 'running').length;
    replace(menu, h('div', { class: 'menu-page' },
      status(), sep(),
      item('Type a command', 'message', openWindow('panel'), { hint: 'Double-click' }),
      item('Stop current response', 'stop', () => call('cancel')),
      item('Mute microphone', muted ? 'micOff' : 'mic', () => call('set_muted', { muted: !muted }), { toggled: Boolean(muted) }),
      item('Hide Jarvis', 'eyeOff', () => call('toggle_jarvis'), { hint: 'Insert' }),
      sep(),
      item('Activity', 'activity', openWindow('console', 'activity'), { hint: active ? `${active} running` : null }),
      item('Settings', 'settings', openWindow('settings')),
      sep(),
      item('Quit Jarvis', 'power', () => call('quit'), { danger: true })));
    buttons = [...menu.querySelectorAll('.menu-item')];
    for (const button of buttons) button.addEventListener('mouseenter', () => focus(buttons.indexOf(button), false));
  }

  function sizeUp() {
    const rect = shell.getBoundingClientRect();
    signal('menu.size', { width: Math.ceil(rect.width), height: Math.ceil(rect.height) });
  }

  function focus(index, keyboard = true) {
    buttons.forEach((button, position) => button.classList.toggle('active', position === index));
    if (keyboard && buttons[index]) buttons[index].focus({ preventScroll: true });
  }

  document.addEventListener('keydown', (event) => {
    if (!open) return;
    const current = buttons.findIndex((button) => button.classList.contains('active'));
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      focus((current + step + buttons.length) % buttons.length);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      buttons[current]?.click();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  });

  store.on((name) => {
    if (name === 'menu') {
      open = true;
      render();
      sizeUp();
    } else if (name === 'menu_closed') {
      open = false;
      buttons.forEach((button) => button.classList.remove('active'));
    } else if (open && ['muted', 'state', 'jobs'].includes(name)) {
      render();
    }
  });
  render();
}
