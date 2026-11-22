import { h, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { applyState, titlebar } from '../lib/ui.js';
import { briefing, email, focus, general, memory, models, monitoring, voice } from './settings/sections.js';

const SECTIONS = {
  general: ['General', 'settings', general],
  briefing: ['Briefing and files', 'sun', briefing],
  memory: ['Memory', 'brain', memory],
  monitoring: ['Monitoring', 'radar', monitoring],
  focus: ['Lock in', 'hand', focus],
  voice: ['Microphone and speech', 'mic', voice],
  models: ['Models', 'cpu', models],
  email: ['Email', 'mail', email],
};
const PREFERENCE_SECTIONS = new Set(['briefing', 'monitoring', 'focus', 'voice']);

export default function start(store, params) {
  const nav = h('nav', { class: 'nav', 'aria-label': 'Settings sections' });
  const host = h('div', { class: 'main' });
  document.body.append(h('div', { class: 'shell' },
    h('aside', { class: 'sidebar' }, titlebar('Settings'), h('div', { class: 'nav-heading' }, 'Preferences'), nav), host));
  let current = null;
  let mounted = null;

  function navigate(section) {
    const key = SECTIONS[section] ? section : 'general';
    if (key === current) return;
    mounted?.destroy?.();
    current = key;
    document.title = 'Jarvis settings · ' + SECTIONS[key][0];
    const content = h('div', { class: 'content page' });
    replace(host, h('div', { class: 'titlebar' }), content);
    mounted = SECTIONS[key][2](content, store);
    replace(nav, Object.entries(SECTIONS).map(([name, [label, glyph]]) => h('button', {
      type: 'button', class: 'nav-item', 'aria-current': name === current ? 'page' : null, onClick: () => navigate(name),
    }, icon(glyph), h('span', { class: 'label' }, label))));
  }

  applyState(store.state.state?.state);
  store.on((name, data) => {
    if (name === 'navigate') navigate(data?.view ?? current);
    else if (name === 'state') applyState(store.state.state?.state);
    else if (name === 'preferences' && PREFERENCE_SECTIONS.has(current) && !host.querySelector('.savebar .state.dirty')) {
      const section = current;
      current = null;
      navigate(section);
    }
  });
  navigate(params.get('section') || 'general');
}
