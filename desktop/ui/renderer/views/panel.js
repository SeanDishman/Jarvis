import { call, ui } from '../lib/bridge.js';
import { h, hudText, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { applyState, button, failure } from '../lib/ui.js';
import { OrbRenderer } from '../orb/orb-renderer.js';

const SUGGESTIONS = ['Give me my morning briefing', 'Search the web for the latest space news', 'Remind me in 30 minutes to stretch'];
const BUSY = new Set(['thinking', 'speaking']);

export default function start(store) {
  document.title = 'Jarvis';
  const canvas = h('canvas', { class: 'mini-orb', 'aria-hidden': 'true' });
  const note = h('div', { class: 'status-note' });
  const badge = h('span', { class: 'pill state-badge' });
  const model = h('span');
  const thread = h('div', { class: 'thread selectable', role: 'log', 'aria-live': 'polite' });
  const activity = h('button', { type: 'button', class: 'activity-link', hidden: true,
                                 onClick: () => ui('open', { window: 'console', view: 'activity', focus: true }) });
  const input = h('textarea', { rows: 1, placeholder: 'Ask Jarvis anything…', 'aria-label': 'Ask Jarvis', spellcheck: true });
  const stop = button('Stop', { icon: 'stop', small: false, title: 'Stop the current response', onClick: () => call('cancel').catch(failure) });
  const ask = button('Ask', { kind: 'primary', icon: 'send', onClick: () => send() });
  stop.hidden = true;

  document.body.append(h('div', { class: 'panel' },
    h('header', { class: 'panel-head' }, canvas,
      h('div', { style: { minWidth: 0 } }, h('div', { class: 'brand' }, 'JARVIS'), note), badge),
    h('div', { class: 'model-line' }, icon('cpu'), model),
    h('div', { class: 'rule' }),
    thread,
    activity,
    h('div', { class: 'composer' }, input, stop, ask),
    h('div', { class: 'panel-foot' }, 'Say “Jarvis” to wake me · Right-click the orb for the menu')));

  const orb = new OrbRenderer(canvas, { size: 56, interactive: false });
  orb.start();
  let pinned = true;
  thread.addEventListener('scroll', () => {
    pinned = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40;
  });

  function fill(text) {
    input.value = text;
    grow();
    input.focus();
    input.setSelectionRange(text.length, text.length);
  }

  async function send() {
    const text = input.value.trim();
    if (!text) {
      input.focus();
      return;
    }
    input.value = '';
    grow();
    pinned = true;
    try {
      await call('submit', { text });
    } catch (error) {
      input.value = text;
      grow();
      failure(error);
    }
  }

  function grow() {
    input.style.height = 'auto';
    input.style.height = Math.min(120, input.scrollHeight) + 'px';
  }

  input.addEventListener('input', grow);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });

  const sources = (rows) => rows?.length ? h('div', { class: 'sources' }, rows.slice(0, 3).map((source) =>
    h('button', { type: 'button', class: 'chip', title: source.url, onClick: () => call('open_url', { url: source.url }).catch(failure) },
      icon('external'), h('span', null, source.title || source.url)))) : null;

  const jarvisLabel = (text = 'JARVIS') => h('div', { class: 'who' }, h('span', { class: 'spark' }), text);

  function exchange(entry) {
    const parts = [];
    if (entry.question) {
      parts.push(h('div', { class: 'msg you' }, h('div', { class: 'who' }, 'YOU'), h('div', { class: 'bubble' }, entry.question)));
    }
    if (entry.pending) {
      parts.push(h('div', { class: 'msg jarvis' }, jarvisLabel(),
        h('div', { class: 'pending' }, h('span', { class: 'typing' }, h('i'), h('i'), h('i')), 'One moment, sir.')));
    } else if (entry.answer) {
      parts.push(h('div', { class: 'msg jarvis' }, jarvisLabel(),
        h('div', { class: ['text', entry.tone === 'error' && 'error'] }, entry.answer), sources(entry.sources)));
    }
    return parts;
  }

  function notice(entry) {
    const tone = entry.tone === 'error' ? 'warning' : 'accent';
    return h('div', { class: ['msg notice', 'tone-' + tone] },
      h('div', { class: 'line' }, icon(entry.tone === 'error' ? 'alert' : 'info'), h('span', null, entry.text)));
  }

  function result(entry) {
    const failed = entry.status === 'failed';
    const waiting = entry.status === 'waiting';
    return h('div', { class: 'msg jarvis result' }, h('div', { class: 'card' },
      h('div', { class: 'eyebrow', style: { color: failed ? 'var(--danger)' : waiting ? 'var(--warning)' : 'var(--success)' } },
        failed ? 'Task failed' : waiting ? 'Needs your answer' : 'Task complete'),
      entry.question ? h('div', { class: 'question' }, entry.question) : null,
      h('div', { class: 'text' }, entry.answer), sources(entry.sources)));
  }

  function renderThread() {
    const entries = store.state.conversation ?? [];
    if (!entries.length) {
      replace(thread, h('div', { class: 'greeting' }, jarvisLabel(),
        h('div', { class: 'text' }, store.state.greeting || 'At your service, sir.'),
        h('div', { class: 'chips' }, SUGGESTIONS.map((text) =>
          h('button', { type: 'button', class: 'chip', onClick: () => fill(text) }, icon('sparkles'), h('span', null, text))))));
      return;
    }
    replace(thread, entries.map((entry) => entry.kind === 'notice' ? notice(entry) : entry.kind === 'result' ? result(entry) : exchange(entry)));
    if (pinned) thread.scrollTop = thread.scrollHeight;
  }

  function renderState() {
    const state = store.state.state ?? {};
    applyState(state.state);
    orb.setState(state.state);
    note.textContent = state.note || '';
    const busy = BUSY.has(state.state);
    replace(badge, h('span', { class: ['dot', busy && 'live'] }), hudText(state.name ?? 'Ready'));
    stop.hidden = !busy;
  }

  function renderJobs() {
    const jobs = store.state.jobs ?? {};
    orb.setBusy(jobs.busy);
    const rows = jobs.rows ?? [];
    activity.hidden = !rows.length;
    replace(activity, jobs.busy ? h('span', { class: 'spinner' }) : icon('activity'),
      h('span', { class: 'label' }, jobs.note || 'Activity'), icon('chevronRight', 'chev'));
  }

  renderState();
  renderJobs();
  renderThread();
  model.textContent = store.state.model || 'Automatic model choice';
  requestAnimationFrame(() => input.focus());

  store.on((name, data) => {
    if (name === 'state') renderState();
    else if (name === 'conversation') renderThread();
    else if (name === 'jobs') renderJobs();
    else if (name === 'model') model.textContent = data || 'Automatic model choice';
    else if (name === 'level') orb.setLevel(data);
    else if (name === 'pulse') orb.pulse();
    else if (name === 'visibility') data?.hidden ? orb.stop() : orb.start();
  });
  window.addEventListener('focus', () => input.focus());
}
