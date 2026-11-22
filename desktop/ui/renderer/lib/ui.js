import { h } from './dom.js';
import { glyph, icon } from './icons.js';

export const STATE_COLORS = {
  ready: '#38ccff', listening: '#42ffcc', thinking: '#a880ff', speaking: '#57ccff', muted: '#7a8aa1',
  hidden: '#7a8aa1', away: '#7a8aa1', starting: '#6194db', error: '#ffb347',
};

export const STATUS_TONES = { queued: 'accent', running: 'accent', waiting: 'warning', completed: 'success', failed: 'danger', cancelled: 'dim' };

export function applyState(state) {
  document.documentElement.style.setProperty('--state', STATE_COLORS[state] ?? STATE_COLORS.ready);
}

export function button(label, { kind = 'secondary', icon: name, onClick, small = false, title, disabled = false } = {}) {
  return h('button', { type: 'button', class: ['btn', kind, small && 'small', !label && 'icon'], title: title ?? null,
                       'aria-label': label ? null : title, onClick, disabled },
           name ? icon(name) : null, label ? h('span', null, label) : null);
}

export function toggle(checked, onChange, label) {
  const element = h('button', { type: 'button', class: 'toggle', role: 'switch', 'aria-label': label,
                                'aria-checked': String(Boolean(checked)) });
  element.addEventListener('click', () => {
    const next = element.getAttribute('aria-checked') !== 'true';
    element.setAttribute('aria-checked', String(next));
    onChange(next, element);
  });
  element.set = (value) => element.setAttribute('aria-checked', String(Boolean(value)));
  return element;
}

export function segmented(options, value, onChange) {
  const element = h('div', { class: 'segmented', role: 'group' });
  const buttons = options.map(([key, label]) => h('button', {
    type: 'button', 'aria-pressed': String(key === value), onClick: () => {
      element.set(key);
      onChange(key);
    },
  }, label));
  element.append(...buttons);
  element.set = (next) => buttons.forEach((item, index) => item.setAttribute('aria-pressed', String(options[index][0] === next)));
  return element;
}

export function select(options, value, onChange) {
  const element = h('select', { class: 'select' },
    options.map(([key, label]) => h('option', { value: key === null ? '' : String(key) }, label)));
  element.value = value === null || value === undefined ? '' : String(value);
  element.addEventListener('change', () => {
    const match = options.find(([key]) => (key === null ? '' : String(key)) === element.value);
    onChange(match ? match[0] : element.value);
  });
  return element;
}

export function setting(name, description, control, { wide = false } = {}) {
  return h('div', { class: ['setting', wide && 'wide'] },
    h('div', null, h('div', { class: 'name' }, name), description ? h('div', { class: 'desc' }, description) : null),
    control ? h('div', { class: 'control' }, control) : null);
}

export function header(eyebrow, title, caption, actions = []) {
  return h('header', { class: 'view-head' },
    h('div', null, h('div', { class: 'eyebrow' }, eyebrow), h('h1', { class: 'title' }, title),
      caption ? h('p', { class: 'caption' }, caption) : null),
    actions.length ? h('div', { class: 'actions' }, actions) : null);
}

export function titlebar(context = '') {
  return h('div', { class: 'titlebar' }, glyph('glyph'), h('span', { class: 'wordmark' }, 'JARVIS'),
    context ? h('span', { class: 'context' }, '· ' + context) : null);
}

export function empty(name, title, text) {
  return h('div', { class: 'empty' }, icon(name), h('strong', null, title), text ? h('p', null, text) : null);
}

export function pill(text, tone = 'accent') {
  return h('span', { class: ['pill', 'tone-' + tone] }, text);
}

export function dot(tone = 'accent', live = false) {
  return h('span', { class: ['dot', 'tone-' + tone, live && 'live'] });
}

let host = null;

export function toast(message, tone = 'success') {
  host ??= document.body.appendChild(h('div', { class: 'toast-host', role: 'status' }));
  const name = tone === 'success' ? 'check' : tone === 'danger' ? 'alert' : 'info';
  const element = h('div', { class: ['toast', 'tone-' + tone] }, icon(name), h('span', null, message));
  host.append(element);
  setTimeout(() => element.remove(), tone === 'danger' ? 5200 : 2600);
}

export function failure(error) {
  toast(error?.message || String(error), 'danger');
}

export function prompt({ eyebrow = 'Jarvis', title, text = '', placeholder = '', confirm = 'Save', value = '' }) {
  return new Promise((resolve) => {
    const input = h('input', { class: 'input', placeholder, value, spellcheck: true });
    const finish = (result) => {
      backdrop.remove();
      document.removeEventListener('keydown', keys, true);
      resolve(result);
    };
    const submit = () => {
      const result = input.value.trim();
      if (result) finish(result);
      else input.focus();
    };
    const keys = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        finish(null);
      } else if (event.key === 'Enter') {
        event.preventDefault();
        submit();
      }
    };
    const backdrop = h('div', { class: 'modal-backdrop', onMousedown: (event) => event.target === backdrop && finish(null) },
      h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' },
        h('div', { class: 'eyebrow' }, eyebrow), h('h2', null, title), text ? h('p', null, text) : null, input,
        h('div', { class: 'actions' }, button('Cancel', { onClick: () => finish(null) }),
          button(confirm, { kind: 'primary', onClick: submit }))));
    document.addEventListener('keydown', keys, true);
    document.body.append(backdrop);
    input.focus();
    input.select();
  });
}

export function folderList(paths, { onRemove, empty: emptyText = 'No folders yet.' } = {}) {
  if (!paths.length) return h('div', { class: 'folder-list' }, h('div', { class: 'folder-empty' }, emptyText));
  return h('div', { class: 'folder-list' }, paths.map((path) => h('div', { class: 'folder', title: path },
    icon('folder'), h('span', { class: 'path' }, h('bdi', null, path)),
    onRemove ? button('', { kind: 'subtle', icon: 'x', small: true, title: 'Remove ' + path, onClick: () => onRemove(path) }) : null)));
}
