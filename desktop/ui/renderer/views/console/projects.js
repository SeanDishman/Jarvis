import { call } from '../../lib/bridge.js';
import { h, replace, shortDate } from '../../lib/dom.js';
import { icon } from '../../lib/icons.js';
import { button, empty, failure, header, toast } from '../../lib/ui.js';

export default function mount(container, store, { select }) {
  let selected = select ?? null;
  let rows = [];
  let current = null;
  let token = 0;
  let dirty = false;
  const list = h('div', { class: 'list', role: 'listbox', 'aria-label': 'Saved projects' });
  const detail = h('section', { class: 'detail card hud-corners' });
  container.append(h('div', { class: 'content split' },
    header('Projects', 'Saved projects', 'Findings you kept from Activity, with your decisions and next steps.'),
    h('div', { class: 'split-body' }, h('div', { class: 'list-pane' }, list), detail)));

  async function load() {
    try {
      rows = (await call('projects')).projects ?? [];
    } catch (error) {
      rows = [];
      failure(error);
    }
    if (!rows.some((row) => row.id === selected)) selected = rows[0]?.id ?? null;
    renderList();
    if (!dirty || current?.id !== selected) open(selected);
  }

  function renderList() {
    if (!rows.length) {
      replace(list, h('div', { class: 'folder-empty' }, 'No saved projects yet.'));
      return;
    }
    replace(list, rows.map((row) => h('button', {
      type: 'button', class: 'list-row plain', role: 'option', 'aria-selected': String(row.id === selected), dataset: { id: row.id },
      onClick: () => {
        if (row.id === selected) return;
        selected = row.id;
        for (const item of list.querySelectorAll('.list-row')) item.setAttribute('aria-selected', String(item.dataset.id === selected));
        open(selected);
      },
    }, h('div', { style: { minWidth: 0 } }, h('div', { class: 'primary' }, row.title),
      h('div', { class: 'secondary' }, row.next_steps ? 'Next: ' + row.next_steps : row.scope === 'web' ? 'Web research' : 'Private notes')),
    h('span', { class: 'aside' }, shortDate(row.updated).split(',')[0]))));
  }

  async function open(id) {
    const mine = ++token;
    dirty = false;
    if (!id) {
      current = null;
      replace(detail, empty('projects', 'Nothing saved yet', 'Save a completed task from Activity, or ask Jarvis to save a project.'));
      return;
    }
    try {
      const project = await call('project', { project: id });
      if (mine !== token) return;
      current = project;
      render();
    } catch (error) {
      if (mine !== token) return;
      replace(detail, empty('projects', 'This project could not be opened', error.message));
    }
  }

  function render() {
    const project = current;
    const area = (value) => {
      const element = h('textarea', { class: 'textarea', rows: 3, spellcheck: true });
      element.value = value ?? '';
      element.addEventListener('input', () => {
        dirty = true;
        saveButton.disabled = false;
      });
      return element;
    };
    const decisions = area(project.decisions);
    const nextSteps = area(project.next_steps);
    const saveButton = button('Save decisions and next steps', { icon: 'save', disabled: true, onClick: async () => {
      try {
        current = await call('project_save', { project: project.id, title: project.title, findings: project.findings,
                                               decisions: decisions.value, next_steps: nextSteps.value, revision: project.revision });
        dirty = false;
        saveButton.disabled = true;
        toast('Project saved.');
      } catch (error) {
        failure(error);
      }
    } });
    const sources = (project.sources ?? []).filter((source) => source.url);
    replace(detail,
      h('div', { class: 'detail-scroll' },
        h('div', { class: 'eyebrow' }, project.scope === 'web' ? 'Web research' : 'Private project'),
        h('h2', { class: 'selectable' }, project.title),
        h('div', { class: 'note-line', style: { color: 'var(--subtle)' } }, icon('clock'), h('span', null, 'Updated ' + shortDate(project.updated))),
        h('div', { class: 'block' }, h('div', { class: 'section-label' }, 'Findings'),
          h('div', { class: 'prose selectable' }, project.findings || 'No findings saved.')),
        sources.length ? h('div', { class: 'block' }, h('div', { class: 'section-label' }, 'Sources'),
          h('div', { class: 'links' }, sources.map((source) => h('button', { type: 'button', class: 'link-row', title: source.url,
            onClick: () => call('open_url', { url: source.url }).catch(failure) }, icon('external'), h('span', null, source.title || source.url))))) : null,
        h('div', { class: 'block' }, h('div', { class: 'section-label' }, 'Decisions'), decisions),
        h('div', { class: 'block' }, h('div', { class: 'section-label' }, 'Next steps'), nextSteps)),
      h('div', { class: 'detail-actions' },
        button('Resume project', { kind: 'primary', icon: 'play', onClick: () => call('project_resume', { project: project.id })
          .then(() => toast('Jarvis is resuming this project.'), failure) }),
        h('span', { class: 'spacer' }), saveButton));
  }

  const off = store.on((name) => {
    if (name === 'projects_changed') load();
  });
  load();
  return {
    select(id) {
      selected = id;
      load();
    },
    destroy() {
      off();
      token++;
    },
  };
}
