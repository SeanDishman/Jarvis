import { call } from '../../lib/bridge.js';
import { elapsed, h, replace } from '../../lib/dom.js';
import { icon } from '../../lib/icons.js';
import { STATUS_TONES, button, dot, empty, failure, header, pill, prompt, toast } from '../../lib/ui.js';

const SCOPES = { web: 'Web search', email: 'Email search', computer: 'PC task', powershell: 'PowerShell', files: 'Files',
                 apps: 'Apps', browser: 'Browser', reminders: 'Reminders', projects: 'Projects', layout: 'Window layout',
                 network: 'Network', task_status: 'Task update', weather: 'Weather', briefing: 'Briefing' };
// A task waiting for the owner's answer is still under way: it can be cancelled and its time keeps counting.
const ACTIVE = new Set(['queued', 'running', 'waiting']);
const FALLBACK = 'Let me check that, sir.';

const scopeName = (scope) => SCOPES[scope] ?? String(scope ?? 'Task').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const statusName = (status) => String(status ?? '').replace(/^./, (c) => c.toUpperCase());
const age = (row) => (ACTIVE.has(row.status) && row.created ? Date.now() / 1000 - row.created : row.elapsedSeconds);
const titleOf = (row) => (row.acknowledgement && row.acknowledgement !== FALLBACK ? row.acknowledgement : row.question) || 'Your request';

export default function mount(container, store, { select, navigate }) {
  let selected = select ?? null;
  let token = 0;
  let frame = { key: null, data: null };
  let lastSignature = '';
  const list = h('div', { class: 'list', role: 'listbox', 'aria-label': 'Background tasks' });
  const detail = h('section', { class: 'detail card hud-corners' });
  container.append(h('div', { class: 'content split' },
    header('Background tasks', 'Activity', 'Keep talking. Closing this window leaves tasks running.'),
    h('div', { class: 'split-body' }, h('div', { class: 'list-pane' }, list), detail)));

  const rows = () => store.state.jobs?.rows ?? [];

  function renderList() {
    const items = rows();
    if (!items.some((row) => row.id === selected)) selected = items[0]?.id ?? null;
    if (!items.length) {
      replace(list, h('div', { class: 'folder-empty' }, 'Nothing has run yet.'));
      return;
    }
    replace(list, items.map((row) => h('button', {
      type: 'button', class: 'list-row', role: 'option', 'aria-selected': String(row.id === selected), dataset: { id: row.id },
      onClick: () => choose(row.id),
    }, dot(STATUS_TONES[row.status] ?? 'dim', row.status === 'running'),
    h('div', { style: { minWidth: 0 } }, h('div', { class: 'primary' }, titleOf(row)),
      h('div', { class: 'secondary' }, `${statusName(row.status)} · ${scopeName(row.scope)}${row.note ? ' · ' + row.note : ''}`)),
    h('span', { class: 'aside', dataset: { created: ACTIVE.has(row.status) ? row.created : '' } }, elapsed(age(row))))));
  }

  function choose(id) {
    if (id === selected) return;
    selected = id;
    frame = { key: null, data: null };
    for (const row of list.querySelectorAll('.list-row')) row.setAttribute('aria-selected', String(row.dataset.id === id));
    loadDetail();
  }

  async function loadDetail() {
    const id = selected;
    const mine = ++token;
    if (!id) {
      replace(detail, empty('activity', 'No background tasks yet', 'Ask Jarvis to search, find an email, or use a desktop app.'));
      return;
    }
    let job;
    try {
      job = await call('job', { job: id });
    } catch (error) {
      job = { ok: false, error: error.message };
    }
    if (mine !== token) return;
    const key = job.ok && job.frameCaptured ? `${id}:${job.frameCaptured}` : null;
    if (key && key !== frame.key) {
      try {
        frame = { key, data: await call('job_frame', { job: id }) };
      } catch {
        frame = { key, data: null };
      }
      if (mine !== token) return;
    } else if (!key) {
      frame = { key: null, data: null };
    }
    renderDetail(job);
  }

  function block(label, ...content) {
    return h('div', { class: 'block' }, h('div', { class: 'section-label' }, label), content);
  }

  function renderDetail(job) {
    if (!job.ok) {
      replace(detail, empty('activity', 'This task is no longer available', job.error));
      return;
    }
    const active = ACTIVE.has(job.status);
    const waiting = job.status === 'waiting';
    const outcome = waiting ? 'Question' : 'Result';
    const facts = [['Elapsed', elapsed(age(job))]];
    if (job.model || job.scope === 'computer' || job.scope === 'web' || job.scope === 'email') facts.push(['Model', job.model || 'Selecting']);
    if (job.reasoning) facts.push(['Reasoning', job.reasoning]);
    if (job.scope === 'web' || job.scope === 'email') facts.push(['Sources found', job.sourcesFound ?? 0], ['Pages read', job.pagesRead ?? 0]);
    const sections = [];
    if (job.scope === 'powershell') {
      sections.push(block('Command', h('pre', { class: 'code' }, job.command || '')));
      sections.push(block('Output', h('pre', { class: 'code' }, job.output || job.stdout || 'Waiting for output.')));
      if (job.stderr) sections.push(block('Error', h('pre', { class: 'code' }, job.stderr)));
      if (job.truncated || job.outputTruncated) sections.push(h('p', { class: 'caption' }, 'Output limited to 200 rows and 12,000 characters.'));
    } else if (job.scope === 'computer') {
      if (job.steps?.length) sections.push(block('Steps', h('ol', { class: 'steps' }, job.steps.map((step) => h('li', null, step)))));
      if (frame.data?.image) {
        sections.push(block('Latest screenshot', h('div', { class: 'preview' },
          h('img', { src: 'data:image/png;base64,' + frame.data.image, alt: 'Latest screenshot from the PC task' }))));
      }
      if (job.summary) sections.push(block(outcome, h('div', { class: 'prose' }, job.summary)));
    } else {
      if (job.keywords?.length) sections.push(block('Keywords', h('div', { class: 'chips' }, job.keywords.map((word) => h('span', { class: 'chip static' }, h('span', null, word))))));
      if (job.queries?.length) sections.push(block('Queries', h('div', { class: 'links' }, job.queries.map((query) => h('div', { class: 'mono muted' }, query)))));
      sections.push(block(active && !waiting ? 'Progress' : outcome, h('div', { class: 'prose' }, job.summary || 'You can keep talking while this runs.')));
    }
    if (job.warnings?.length) {
      sections.push(h('div', { class: 'block notice-bar tone-warning' }, icon('alert'),
        h('div', null, h('strong', null, 'Search limits'), h('div', null, job.warnings.join('\n')))));
    }
    const links = (job.sources ?? []).filter((source) => source.url).slice(0, 8);
    if (links.length) {
      sections.push(block('Sources', h('div', { class: 'links' }, links.map((source) => h('button', {
        type: 'button', class: 'link-row', title: source.url, onClick: () => call('open_url', { url: source.url }).catch(failure),
      }, icon('external'), h('span', null, source.title || source.url))))));
    }
    const save = button('Save as project', { icon: 'save', disabled: job.status !== 'completed',
                                            title: job.status === 'completed' ? 'Keep these findings' : 'Available when the task completes',
                                            onClick: () => saveProject(job) });
    replace(detail,
      h('div', { class: 'detail-scroll selectable' },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px' } }, h('span', { class: 'eyebrow' }, scopeName(job.scope)),
          pill(statusName(job.status), STATUS_TONES[job.status] ?? 'dim')),
        h('h2', null, job.question),
        job.note ? h('div', { class: 'note-line' }, active && !waiting ? h('span', { class: 'spinner' }) : icon('info'), h('span', null, job.note)) : null,
        h('div', { class: 'facts' }, facts.map(([name, value]) => h('div', { class: 'fact' }, h('div', { class: 'k' }, name), h('div', { class: 'v' }, String(value))))),
        sections),
      h('div', { class: 'detail-actions' },
        button('Read aloud', { kind: 'primary', icon: 'waves', title: 'Read the result or progress aloud',
                               onClick: () => call('job_read', { job: job.id }).then(() => toast('Jarvis is reading it aloud.'), failure) }),
        save, h('span', { class: 'spacer' }),
        button('Cancel task', { kind: 'danger', icon: 'stop', disabled: !active, onClick: () => cancel(job) })));
  }

  async function cancel(job) {
    try {
      const result = await call('job_cancel', { job: job.id });
      if (result && result.ok === false) toast(result.error || 'That task is not running.', 'danger');
      else toast('Task cancelled.');
    } catch (error) {
      failure(error);
    }
  }

  async function saveProject(job) {
    const title = await prompt({ eyebrow: 'Projects', title: 'Save as project', text: 'Name these findings so you can resume them later.',
                                 value: String(job.question ?? '').slice(0, 100), confirm: 'Save project' });
    if (!title) return;
    try {
      const saved = await call('job_save', { job: job.id, title });
      toast('Project saved.');
      navigate('projects', saved?.id);
    } catch (error) {
      failure(error);
    }
  }

  function signature() {
    const row = rows().find((item) => item.id === selected);
    return row ? JSON.stringify([row.status, row.note, row.stage, row.steps?.length, row.summary?.length, row.sourcesFound, row.pagesRead, row.model]) : '';
  }

  const ticker = setInterval(() => {
    for (const aside of list.querySelectorAll('.aside[data-created]')) {
      if (aside.dataset.created) aside.textContent = elapsed(Date.now() / 1000 - Number(aside.dataset.created));
    }
  }, 1000);

  const off = store.on((name) => {
    if (name !== 'jobs') return;
    renderList();
    const next = signature();
    if (next !== lastSignature) {
      lastSignature = next;
      loadDetail();
    }
  });
  renderList();
  lastSignature = signature();
  loadDetail();

  return {
    select(id) {
      selected = id;
      frame = { key: null, data: null };
      renderList();
      lastSignature = signature();
      loadDetail();
    },
    destroy() {
      off();
      clearInterval(ticker);
      token++;
    },
  };
}
