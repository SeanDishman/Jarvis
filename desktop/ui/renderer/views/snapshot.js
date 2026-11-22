import { ui } from '../lib/bridge.js';
import { h } from '../lib/dom.js';
import { button, titlebar } from '../lib/ui.js';

export default function start(store) {
  document.title = 'Jarvis screen snapshot';
  const image = h('img', { alt: 'The screen Jarvis is looking at' });
  const render = () => {
    const data = store.state.snapshot;
    if (data?.image) image.src = 'data:image/png;base64,' + data.image;
  };
  document.body.append(h('div', { class: 'snapshot' }, titlebar('Vision'),
    h('div', { class: 'page-head' }, h('div', { class: 'eyebrow' }, 'Vision'), h('h1', { class: 'title' }, 'The screen Jarvis is looking at')),
    h('div', { class: 'frame hud-corners' }, image),
    h('div', { class: 'foot' }, h('span', { class: 'caption', style: { margin: 0 } }, 'A fresh snapshot for your request, kept in memory only.'),
      button('Close', { onClick: () => ui('window', { action: 'close' }) }))));
  store.on((name) => {
    if (name === 'snapshot') render();
  });
  render();
}
