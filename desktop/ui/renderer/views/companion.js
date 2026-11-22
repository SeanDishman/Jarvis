import { call, signal, ui } from '../lib/bridge.js';
import { h, plural, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { button } from '../lib/ui.js';

const ALERT_SECONDS = 8;

export default function start(store) {
  const root = h('div', { class: 'companion', 'data-anchor': 'above' });
  document.body.append(root);
  const nodes = new Map();
  const timers = new Map();
  const dismissedProgress = new Set();
  let panelVisible = false;
  let visible = null;
  let captured = false;

  const dismiss = (card) => ui('card.dismiss', { id: card.id }).catch(() => {});

  const closeButton = (onClick, label = 'Dismiss') => h('button', { type: 'button', class: 'close', title: label, 'aria-label': label, onClick }, icon('x'));

  function reminderCard(card) {
    const status = h('div', { class: 'hint', hidden: true });
    const act = (action) => async () => {
      try {
        await call('reminder_change', { action, reminder: card.row.id });
        dismiss(card);
      } catch (error) {
        if (/Choose the reminder/.test(error.message)) dismiss(card);
        else {
          status.hidden = false;
          status.textContent = error.message;
        }
      }
    };
    return h('div', { class: 'hud-card reminder-card tone-warning', role: 'alert' }, h('div', { class: 'accent-bar' }),
      h('div', { class: 'body' },
        h('div', { class: 'top' }, h('span', { class: 'eyebrow' }, 'Reminder'), icon('bell')),
        h('div', { class: 'headline selectable' }, card.row.text),
        h('div', { class: 'actions' }, button('Dismiss', { kind: 'primary', small: true, onClick: act('dismiss') }),
          button('Snooze 10 minutes', { small: true, onClick: act('snooze') })),
        status));
  }

  function alertCard(card) {
    const element = h('div', { class: 'hud-card tone-accent', role: 'status' }, h('div', { class: 'accent-bar' }),
      h('div', { class: 'body' },
        h('div', { class: 'top' }, h('span', { class: 'eyebrow' }, 'Jarvis · Alert'), closeButton(() => dismiss(card))),
        h('div', { class: 'text' }, card.row.text),
        h('button', { type: 'button', class: 'link', onClick: () => {
          ui('open', { window: 'console', view: 'alerts', focus: true }).catch(() => {});
          dismiss(card);
        } }, 'View alerts', icon('chevronRight'))));
    const schedule = (seconds) => {
      clearTimeout(timers.get(card.id));
      timers.set(card.id, setTimeout(() => dismiss(card), seconds * 1000));
    };
    element.addEventListener('mouseenter', () => clearTimeout(timers.get(card.id)));
    element.addEventListener('mouseleave', () => schedule(4));
    schedule(Math.max(1, ALERT_SECONDS - (Date.now() - card.at) / 1000));
    return element;
  }

  function lockInCard(card) {
    const learning = card.row.learning === true;
    const welcome = card.row.welcome === true;
    const cooldown = card.row.cooldown === true;
    // Focus reminders, welcome-back greetings and a lock in cooldown are friendly notes; only a blocked site is an alert.
    const gentle = learning || welcome || cooldown;
    const attempt = Math.max(1, Number(card.row.attempt) || 1);
    const line = String(card.row.text || '').trim() || (gentle ? '' : 'That page is closed, sir.');
    const title = welcome ? `Jarvis · ${card.row.title || 'Welcome back'}` : learning ? `${card.row.name} · Focus reminder`
      : cooldown ? `${card.row.name} · Lock in cooldown` : 'Jarvis · Lock in';
    const element = h('div', { class: `hud-card lock-in-card ${gentle ? 'tone-accent' : 'tone-danger'}`, role: gentle ? 'status' : 'alert' }, h('div', { class: 'accent-bar' }),
      h('div', { class: 'body' },
        h('div', { class: 'top' }, h('span', { class: 'eyebrow' }, title), closeButton(() => dismiss(card))),
        h('div', { class: gentle ? 'text' : 'headline', style: { overflowWrap: 'anywhere' } }, line),
        gentle ? null : h('div', { class: 'hint' }, `${card.row.name ?? 'This site'} is blocked. Attempt ${attempt}.`)));
    const seconds = gentle ? Math.max(ALERT_SECONDS, 2 + line.split(/\s+/).length / 2.5) : ALERT_SECONDS;
    clearTimeout(timers.get(card.id));
    timers.set(card.id, setTimeout(() => dismiss(card), Math.max(1, seconds - (Date.now() - card.at) / 1000) * 1000));
    return element;
  }

  function progressCard() {
    return h('div', { class: 'hud-card progress-card', role: 'status', 'aria-live': 'polite' },
      closeButton(() => {
        for (const item of store.state.progress ?? []) dismissedProgress.add(item.id);
        render();
      }, 'Hide progress (tasks keep running)'),
      h('div', { class: 'rows' }), h('div', { class: 'progress-track' }));
  }

  function renderProgress(element, items) {
    const rows = [];
    items.slice(0, 2).forEach((item, index) => {
      if (index) rows.push(h('div', { class: 'progress-divider' }));
      rows.push(h('div', { class: 'progress-row' }, h('span', { class: 'dot tone-accent live' }),
        h('div', { style: { minWidth: 0 } }, h('div', { class: 'task' }, item.title), h('div', { class: 'detail' }, item.detail))));
    });
    if (items.length > 2) rows.push(h('div', { class: 'progress-more' }, plural(items.length - 2, 'more task')));
    replace(element.querySelector('.rows'), rows);
  }

  function render() {
    const wanted = (store.state.cards ?? []).map((card) => [card.id, card]);
    const progress = (store.state.progress ?? []).filter((item) => !dismissedProgress.has(item.id));
    const showProgress = progress.length > 0 && !panelVisible;
    if (showProgress) wanted.push(['progress', null]);
    const keys = new Set(wanted.map(([key]) => key));
    for (const [key, element] of nodes) {
      if (keys.has(key)) continue;
      element.remove();
      nodes.delete(key);
      clearTimeout(timers.get(key));
      timers.delete(key);
    }
    wanted.forEach(([key, card], index) => {
      let element = nodes.get(key);
      if (!element) {
        element = key === 'progress' ? progressCard()
          : card.kind === 'reminder' ? reminderCard(card) : card.kind === 'lockin' ? lockInCard(card) : alertCard(card);
        nodes.set(key, element);
      }
      if (key === 'progress') renderProgress(element, progress);
      if (root.children[index] !== element) root.insertBefore(element, root.children[index] ?? null);
    });
    const shown = wanted.length > 0;
    if (shown !== visible) {
      visible = shown;
      signal('companion.state', { visible: shown });
      if (!shown) setCapture(false);
    }
  }

  function setCapture(value) {
    if (value === captured) return;
    captured = value;
    signal('companion.capture', { capture: value });
  }

  window.addEventListener('mousemove', (event) => setCapture(Boolean(event.target.closest?.('.hud-card'))));
  document.addEventListener('mouseleave', () => setCapture(false));

  async function pruneReminders() {
    const cards = (store.state.cards ?? []).filter((card) => card.kind === 'reminder');
    if (!cards.length) return;
    try {
      const due = new Set((await call('reminders')).reminders.filter((row) => row.state === 'due').map((row) => row.id));
      for (const card of cards) if (!due.has(card.row.id)) dismiss(card);
    } catch {
      return;
    }
  }

  store.on((name, data) => {
    if (name === 'panel') panelVisible = Boolean(data?.visible);
    else if (name === 'anchor') root.dataset.anchor = data === 'below' ? 'below' : 'above';
    else if (name === 'reminders_changed') pruneReminders();
    if (['panel', 'cards', 'progress'].includes(name)) render();
  });
  render();
}
