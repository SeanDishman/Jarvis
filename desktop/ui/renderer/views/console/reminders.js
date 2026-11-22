import { call } from '../../lib/bridge.js';
import { h, replace, when } from '../../lib/dom.js';
import { icon } from '../../lib/icons.js';
import { button, empty, failure, header, pill, toast } from '../../lib/ui.js';

// A morning reminder has no clock time of its own: it comes with the greeting when he first sits down that day.
function morningOf(seconds) {
  const text = when(seconds);
  return `${text.slice(0, text.lastIndexOf(', '))} morning, with your greeting`;
}

export default function mount(container, store, { counts }) {
  const input = h('input', { class: 'input', placeholder: 'What should I remind you about, and when?  e.g. “to call Mum at 6pm”', spellcheck: true });
  const listCard = h('div', { class: 'card' });
  const warning = h('div');
  const add = async () => {
    const text = input.value.trim();
    if (!text) {
      input.focus();
      return;
    }
    try {
      await call('reminder_new', { text });
      input.value = '';
      toast('Jarvis is setting that reminder.');
    } catch (error) {
      failure(error);
    }
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') add();
  });
  container.append(h('div', { class: 'content' },
    header('Schedule', 'Reminders and timers', 'Saved across restarts. A reminder waits for you: it comes up the first time you move the mouse or type after its time. One for the morning comes with your greeting.'),
    h('div', { class: 'card new-reminder' }, h('span', { style: { display: 'grid', placeItems: 'center', color: 'var(--accent)', paddingLeft: '4px' } }, icon('bell')),
      input, button('Add reminder', { kind: 'primary', icon: 'plus', onClick: add })),
    warning, listCard,
    h('p', { class: 'caption' }, 'Jarvis must be running. Overdue reminders appear when he next runs. You can also say “Jarvis, remind me…”.')));

  const change = (action, row) => async () => {
    try {
      await call('reminder_change', { action, reminder: row.id });
      toast(action === 'snooze' ? 'Snoozed for 10 minutes.' : action === 'dismiss' ? 'Reminder dismissed.' : 'Reminder cancelled.');
    } catch (error) {
      failure(error);
    }
  };

  async function load() {
    let result;
    try {
      result = await call('reminders');
    } catch (error) {
      result = { reminders: [], error: error.message };
    }
    const rows = result.reminders ?? [];
    counts('reminders', rows.filter((row) => row.state === 'due').length);
    replace(warning, result.error ? h('div', { class: 'notice-bar tone-warning', style: { marginBottom: '14px' } }, icon('alert'), h('span', null, result.error)) : null);
    if (!rows.length) {
      replace(listCard, empty('bell', 'No reminders', 'Add one above or ask Jarvis to remind you about something.'));
      return;
    }
    replace(listCard, rows.map((row) => h('div', { class: 'reminder-row' },
      pill(row.state === 'due' ? 'Due' : 'Scheduled', row.state === 'due' ? 'warning' : 'accent'),
      h('div', { style: { minWidth: 0 } },
        h('div', { class: 'when' }, (row.morning && !row.snoozed_until ? morningOf(row.due) : when(row.snoozed_until ?? row.due))
          + (row.repeat && row.repeat !== 'none' ? ` · repeats ${row.repeat}` : '')),
        h('div', { class: 'what selectable' }, row.text)),
      h('div', { class: 'row-actions' },
        button('Snooze 10 min', { small: true, icon: 'clock', onClick: change('snooze', row) }),
        button('Dismiss', { small: true, icon: 'check', onClick: change('dismiss', row) }),
        button('', { small: true, kind: 'danger', icon: 'trash', title: 'Cancel reminder', onClick: change('cancel', row) })))));
  }

  const off = store.on((name) => {
    if (name === 'reminders_changed' || name === 'cards') load();
  });
  load();
  const ticker = setInterval(load, 30000);
  return {
    destroy() {
      off();
      clearInterval(ticker);
    },
  };
}
