import { call, ui } from '../lib/bridge.js';
import { h, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { applyState, titlebar } from '../lib/ui.js';
import activity from './console/activity.js';
import alerts from './console/alerts.js';
import browser from './console/browser.js';
import files from './console/files.js';
import projects from './console/projects.js';
import reminders from './console/reminders.js';

const VIEWS = {
  activity: ['Activity', 'activity', activity],
  projects: ['Saved projects', 'projects', projects],
  reminders: ['Reminders and timers', 'bell', reminders],
  alerts: ['Proactive alerts', 'alert', alerts],
  files: ['File results', 'files', files],
  browser: ['Browser controls', 'globe', browser],
};

export default function start(store, params) {
  const counts = { activity: 0, reminders: 0, alerts: 0, files: 0 };
  const nav = h('nav', { class: 'nav', 'aria-label': 'Console sections' });
  const statusText = h('span', { class: 'text' });
  const host = h('div', { class: 'main' }, h('div', { class: 'titlebar' }));
  document.body.append(h('div', { class: 'shell' },
    h('aside', { class: 'sidebar' }, titlebar('Console'), h('div', { class: 'nav-heading' }, 'Workspace'), nav,
      h('div', { class: 'foot' }, h('div', { class: 'side-status' }, h('span', { class: 'dot' }), statusText),
        h('button', { type: 'button', class: 'nav-item', onClick: () => ui('open', { window: 'settings', focus: true }) },
          icon('settings'), h('span', { class: 'label' }, 'Settings')))),
    host));

  let current = null;
  let mounted = null;

  function renderNav() {
    replace(nav, Object.entries(VIEWS).map(([key, [label, name]]) => {
      const count = counts[key];
      return h('button', { type: 'button', class: 'nav-item', 'aria-current': key === current ? 'page' : null, onClick: () => navigate(key) },
        icon(name), h('span', { class: 'label' }, label),
        count ? h('span', { class: ['count', key === 'reminders' && 'warning'] }, String(count)) : null);
    }));
  }

  function navigate(view, select = null) {
    const key = VIEWS[view] ? view : 'activity';
    if (key === current && mounted) {
      if (select) mounted.select?.(select);
      return;
    }
    mounted?.destroy?.();
    host.querySelector('.content')?.remove();
    current = key;
    document.title = 'Jarvis · ' + VIEWS[key][0];
    const container = h('div', { style: { display: 'contents' } });
    replace(host, h('div', { class: 'titlebar' }), container);
    mounted = VIEWS[key][2](container, store, { select, counts: updateCount, navigate });
    renderNav();
  }

  function updateCount(key, value) {
    if (counts[key] === value) return;
    counts[key] = value;
    renderNav();
  }

  function renderState() {
    const state = store.state.state ?? {};
    applyState(state.state);
    statusText.textContent = `${state.name ?? 'Ready'} · ${state.note ?? ''}`;
    const running = (store.state.jobs?.rows ?? []).filter((row) => row.status === 'queued' || row.status === 'running').length;
    updateCount('activity', running);
    updateCount('files', store.state.files?.files?.length ?? 0);
  }

  async function refreshCounts() {
    try {
      const due = (await call('reminders')).reminders.filter((row) => row.state === 'due').length;
      updateCount('reminders', due);
    } catch {
      updateCount('reminders', 0);
    }
    try {
      updateCount('alerts', (await call('alerts')).alerts.length);
    } catch {
      updateCount('alerts', 0);
    }
  }

  store.on((name, data) => {
    if (name === 'navigate') navigate(data?.view ?? current, data?.select ?? null);
    else if (name === 'state' || name === 'jobs' || name === 'files') renderState();
    else if (name === 'reminders_changed' || name === 'cards') refreshCounts();
  });
  renderState();
  navigate(params.get('section') || 'activity', params.get('select'));
  refreshCounts();
}
