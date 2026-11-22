import { call } from '../../lib/bridge.js';
import { h } from '../../lib/dom.js';
import { icon } from '../../lib/icons.js';
import { button, failure, header, toast } from '../../lib/ui.js';

export default function mount(container, store) {
  const note = h('span');
  const render = () => {
    note.textContent = store.state.browserNote || 'Ask Jarvis to visit a website.';
  };
  container.append(h('div', { class: 'content' },
    header('Browser', 'Your Jarvis browser', 'A dedicated automation window that Jarvis can navigate, read and fill in for you.'),
    h('div', { class: 'stack' },
      h('div', { class: 'notice-bar tone-accent' }, icon('globe'), note),
      h('div', { class: 'card setting-card' },
        h('div', { class: 'setting' }, h('div', null,
          h('div', { class: 'name' }, 'Continue manually'),
          h('div', { class: 'desc' }, 'Jarvis can navigate, read, and fill fields you dictate. Continue manually to submit forms or sign in yourself. This disconnects automation until you close the browser window.')),
        h('div', { class: 'control' }, button('Continue manually', { kind: 'primary', icon: 'hand', onClick: () => call('browser_handoff')
          .then((result) => toast(result?.message || 'Handed over.'), failure) })))))));
  const off = store.on((name) => {
    if (name === 'browser') render();
  });
  render();
  return { destroy: off };
}
