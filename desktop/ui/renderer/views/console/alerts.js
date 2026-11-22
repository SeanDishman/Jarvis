import { call } from '../../lib/bridge.js';
import { clock, h, replace } from '../../lib/dom.js';
import { icon } from '../../lib/icons.js';
import { button, empty, failure, header, pill, toast } from '../../lib/ui.js';

export default function mount(container, store, { counts }) {
  const body = h('div');
  const status = h('span');
  container.append(h('div', { class: 'content' },
    header('Monitoring', 'Proactive alerts', null, [
      button('Refresh', { icon: 'refresh', onClick: () => load() }),
      button('Dismiss all', { kind: 'danger', icon: 'x', onClick: async () => {
        try {
          await call('alerts_dismiss');
          toast('Alerts dismissed.');
          load();
        } catch (error) {
          failure(error);
        }
      } }),
    ]),
    h('div', { style: { margin: '-8px 0 18px' } }, status), body));

  async function load() {
    let data;
    try {
      data = await call('alerts');
    } catch (error) {
      replace(body, empty('alert', 'Alerts are unavailable', error.message));
      return;
    }
    const alerts = [...(data.alerts ?? [])].reverse();
    counts('alerts', alerts.length);
    replace(status, data.enabled ? pill('Watching', 'success') : pill('Paused', 'dim'));
    replace(body,
      data.warning ? h('div', { class: 'notice-bar tone-warning', style: { marginBottom: '14px' } }, icon('alert'), h('span', null, data.warning)) : null,
      alerts.length
        ? h('div', { class: 'card' }, alerts.map((row) => h('div', { class: 'alert-row' },
          h('div', { class: 'time' }, clock(row.time)), h('div', { class: 'text selectable' }, row.text))))
        : h('div', { class: 'card' }, empty('radar', 'No current alerts', 'New activity appears here while Jarvis is running.')),
      h('p', { class: 'caption' }, data.scope || ''));
  }

  const off = store.on((name) => {
    if (name === 'cards') load();
  });
  load();
  return { destroy: off };
}
