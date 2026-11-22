import { call } from '../../lib/bridge.js';
import { h, plural, replace } from '../../lib/dom.js';
import { icon } from '../../lib/icons.js';
import { button, empty, failure, header, toast } from '../../lib/ui.js';

export default function mount(container, store) {
  const body = h('div');
  container.append(h('div', { class: 'content' },
    header('Files', 'Files in your selected folders', 'Results from your latest file search. Summaries are read only when you ask.'), body));

  function render() {
    const result = store.state.files;
    if (!result) {
      replace(body, h('div', { class: 'card' }, empty('files', 'No file search yet', 'Ask Jarvis to find a file in your selected folders.')));
      return;
    }
    const found = result.files ?? [];
    const note = result.error || (result.partial ? 'Some locations were skipped or the search reached its limit.'
      : plural(result.matches ?? found.length, 'matching file'));
    replace(body,
      h('div', { class: ['notice-bar', result.error ? 'tone-warning' : 'tone-accent'], style: { marginBottom: '14px' } },
        icon(result.error ? 'alert' : 'info'), h('span', null, note)),
      found.length ? h('div', { class: 'card' }, found.map((item) => h('div', { class: 'file-row' }, icon('files'),
        h('div', { style: { minWidth: 0 } }, h('div', { class: 'name selectable' }, item.name), h('div', { class: 'folder-path', title: item.folder }, item.folder)),
        button('Summarize', { small: true, icon: 'sparkles', onClick: () => call('file_summarize', { file: item.id, name: item.name })
          .then(() => toast('Jarvis is summarizing ' + item.name + '.'), failure) })))) : null);
  }

  const off = store.on((name) => {
    if (name === 'files') render();
  });
  render();
  return { destroy: off };
}
