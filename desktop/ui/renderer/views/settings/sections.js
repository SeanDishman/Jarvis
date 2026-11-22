import { call, ui } from '../../lib/bridge.js';
import { h, replace } from '../../lib/dom.js';
import { icon } from '../../lib/icons.js';
import { levelMeter } from '../../lib/meter.js';
import { button, empty, failure, folderList, header, prompt, segmented, select, setting, toast, toggle } from '../../lib/ui.js';

const card = (...rows) => h('div', { class: 'card setting-card' }, rows);
const loading = () => h('div', { class: 'card' }, h('div', { class: 'empty' }, h('span', { class: 'spinner' }), h('p', null, 'Loading…')));

function saveBar(onSave) {
  const state = h('span', { class: 'state' }, 'All changes saved');
  const save = button('Save changes', { kind: 'primary', icon: 'save', disabled: true, onClick: async () => {
    save.disabled = true;
    state.textContent = 'Saving…';
    try {
      await onSave();
      bar.set(false);
      toast('Settings saved.');
    } catch (error) {
      bar.set(true);
      failure(error);
    }
  } });
  const bar = h('div', { class: 'savebar' }, state, save);
  bar.set = (dirty) => {
    save.disabled = !dirty;
    state.textContent = dirty ? 'You have unsaved changes' : 'All changes saved';
    state.classList.toggle('dirty', dirty);
  };
  return bar;
}

function folderEditor(paths, { onChange, add, title, emptyText }) {
  const wrap = h('div', { class: 'stack', style: { gap: '10px' } });
  const render = () => replace(wrap,
    folderList(paths, { empty: emptyText, onRemove: (path) => {
      paths.splice(paths.indexOf(path), 1);
      render();
      onChange();
    } }),
    h('div', null, button(add, { icon: 'folderPlus', onClick: async () => {
      const folder = await ui('pickFolder', { title });
      if (!folder || paths.includes(folder)) return;
      paths.push(folder);
      render();
      onChange();
    } })));
  render();
  return wrap;
}

async function preferences(container, body) {
  replace(container, body.head, loading());
  try {
    return await call('preferences');
  } catch (error) {
    replace(container, body.head, h('div', { class: 'card' }, empty('alert', 'Settings are unavailable', error.message)));
    return null;
  }
}

export function general(container, store) {
  const startup = toggle(store.state.startup, async (value, element) => {
    try {
      element.set((await call('set_startup', { enabled: value })).enabled);
    } catch (error) {
      element.set(!value);
      failure(error);
    }
  }, 'Start when I sign in');
  const muted = toggle(store.state.muted, (value, element) => call('set_muted', { muted: value }).catch((error) => {
    element.set(!value);
    failure(error);
  }), 'Mute microphone');
  const popups = toggle(store.state.settings?.showTaskPopups === true, (value, element) => call('set_setting', { key: 'showTaskPopups', value })
    .catch((error) => {
      element.set(!value);
      failure(error);
    }), 'Show task popups');
  const screens = h('div');
  container.append(header('Jarvis', 'General', 'How Jarvis starts, listens and appears on your desktop.'),
    h('div', { class: 'stack' },
      card(setting('Start when I sign in', 'Launch Jarvis automatically when you sign in to Windows.', startup),
        setting('Mute microphone', 'Jarvis stops listening, even for his name, until you unmute him.', muted),
        setting('Show task popups', 'Open Activity, without taking focus, when a background task starts.', popups)),
      card(setting('Orb position', 'The screen whose corner holds the orb. Automatic prefers a laptop screen.', screens)),
      card(setting('Diagnostic logs', 'Timing and status events only. No audio, transcripts or passwords.',
        button('Open logs', { icon: 'terminal', onClick: () => call('open', { target: 'logs' }).catch(failure) })))));

  async function loadScreens() {
    try {
      const choices = await ui('screens');
      replace(screens, select([[null, 'Automatic'], ...choices.map((choice) => [choice.device, `${choice.label} · ${choice.detail}`])],
        store.state.screens?.selected ?? null, (device) => call('select_screen', { device }).then(() => toast('Orb moved.'), failure)));
    } catch (error) {
      replace(screens, h('span', { class: 'muted' }, error.message));
    }
  }
  loadScreens();
  return {
    destroy: store.on((name) => {
      if (name === 'muted') muted.set(store.state.muted);
      else if (name === 'startup') startup.set(store.state.startup);
      else if (name === 'settings') popups.set(store.state.settings?.showTaskPopups === true);
      else if (name === 'screens') loadScreens();
    }),
  };
}

export function briefing(container) {
  const head = header('Assistant', 'Briefing and files', 'Your morning briefing, and the folders Jarvis may search and read.');
  let alive = true;
  (async () => {
    const prefs = await preferences(container, { head });
    if (!prefs || !alive) return;
    const roots = [...prefs.fileRoots];
    let daily = Boolean(prefs.briefingTime);
    let units = prefs.weatherUnits;
    const bar = saveBar(async () => {
      await call('preferences_save', { values: { briefingTime: daily ? time.value : '', weatherUnits: units, fileRoots: roots } });
    });
    const changed = () => bar.set(true);
    const time = h('input', { class: 'input', type: 'time', value: prefs.briefingTime || '08:00', style: { width: '140px' } });
    time.disabled = !daily;
    time.addEventListener('input', changed);
    const dailyToggle = toggle(daily, (value) => {
      daily = value;
      time.disabled = !value;
      changed();
    }, 'Daily briefing');
    replace(container, head, h('div', { class: 'stack' },
      card(setting('Daily briefing', 'Weather, reminders and important unread email at a set time. Off means on demand only.', h('div', { class: 'control' }, time, dailyToggle)),
        setting('Weather units', 'Weather uses your connection’s approximate location. Calendars are not used.',
          segmented([['fahrenheit', 'Fahrenheit'], ['celsius', 'Celsius']], units, (value) => {
            units = value;
            changed();
          })),
        setting('Brief me now', 'Hear today’s briefing right away.', button('Brief me now', { icon: 'sun', onClick: () => call('briefing').then(() => toast('Jarvis is preparing your briefing.'), failure) }))),
      card(setting('Folders Jarvis may search and read', 'Document text goes to your model only when you ask Jarvis to read or summarize it.',
        folderEditor(roots, { onChange: changed, add: 'Add folder', title: 'Choose a folder Jarvis may search and read', emptyText: 'No search folders yet.' }), { wide: true }))),
    bar);
  })();
  return { destroy: () => { alive = false; } };
}

export function memory(container) {
  const head = header('Local intelligence', 'Memory', 'Say “remember that…” to save a note, or “recall…” to find one. Notes stay on this PC.');
  let alive = true;
  (async () => {
    const prefs = await preferences(container, { head });
    if (!prefs || !alive) return;
    const writes = [...prefs.fileWriteRoots];
    const bar = saveBar(async () => {
      await call('preferences_save', { values: { memoryVault: vault.value.trim(), fileWriteRoots: writes } });
    });
    const changed = () => bar.set(true);
    const vault = h('input', { class: 'input mono', value: prefs.memoryVault, spellcheck: false });
    vault.addEventListener('input', changed);
    replace(container, head, h('div', { class: 'stack' },
      card(setting('Obsidian vault', 'Jarvis keeps his memories in this vault. You can edit them in Obsidian.', h('div', { class: 'stack', style: { gap: '10px' } }, vault,
        h('div', { class: 'control' },
          button('Choose vault', { icon: 'folder', onClick: async () => {
            const folder = await ui('pickFolder', { title: 'Choose your Obsidian vault' });
            if (folder) {
              vault.value = folder;
              changed();
            }
          } }),
          button('Open saved vault', { icon: 'external', onClick: () => call('vault_open').catch(failure) }))), { wide: true })),
      card(setting('Writable folders', 'Jarvis can create, edit and organize documents here. Edits keep a recovery copy; other folders stay read only.',
        folderEditor(writes, { onChange: changed, add: 'Add folder', title: 'Choose a folder Jarvis may edit', emptyText: 'Jarvis cannot edit documents anywhere yet.' }), { wide: true }))),
    bar);
  })();
  return { destroy: () => { alive = false; } };
}

export function monitoring(container) {
  const head = header('Monitoring', 'Proactive alerts', 'Top-level files in the folders below, and your battery. Never your screen, inbox or notifications.');
  let alive = true;
  (async () => {
    const prefs = await preferences(container, { head });
    if (!prefs || !alive) return;
    const folders = [...prefs.monitorFolders];
    let enabled = prefs.monitorEnabled;
    let battery = prefs.monitorBattery;
    const bar = saveBar(async () => {
      await call('preferences_save', { values: { monitorEnabled: enabled, monitorBattery: battery, monitorFolders: folders } });
    });
    const changed = () => bar.set(true);
    replace(container, head, h('div', { class: 'stack' },
      card(setting('Proactive alerts', 'Let Jarvis tell you about new files and a low battery. Alerts never take focus.', toggle(enabled, (value) => {
        enabled = value;
        changed();
      }, 'Proactive alerts')),
      setting('Low battery warning', 'Warn when the battery runs low.', toggle(battery, (value) => {
        battery = value;
        changed();
      }, 'Low battery warning'))),
      card(setting('Watched folders', 'New or changed files are announced once their size settles. Subfolders are not watched.',
        folderEditor(folders, { onChange: changed, add: 'Watch folder', title: 'Choose a folder to watch', emptyText: 'No folders are watched.' }), { wide: true }))),
    bar);
  })();
  return { destroy: () => { alive = false; } };
}

export function focus(container) {
  const head = header('Focus', 'Lock in mode', 'Manage distractions while you work.');
  let alive = true;
  (async () => {
    const prefs = await preferences(container, { head });
    if (!prefs || !alive) return;
    const blocker = toggle(prefs.lockInMode, async (value, element) => {
      try {
        element.set((await call('preferences_save', { values: { lockInMode: value } })).lockInMode);
        toast(value ? 'Lock in mode is on.' : 'Lock in mode is off.');
      } catch (error) {
        element.set(!value);
        failure(error);
      }
    }, 'Lock in mode');
    replace(container, head, h('div', { class: 'stack' },
      card(setting('Lock in mode', 'When you open x.com, twitter.com or instagram.com in your browser, Jarvis closes the tab, flies over to it and tells you to lock in with a fresh line each time.', blocker),
        setting('Learning reminders', 'On LinkedIn and YouTube, Jarvis gives one reminder per session, built around a real quote that Wikiquote confirms. It stays quiet while any tab of the site is open, and a new session starts only after every tab of it has been closed for 15 minutes.', null),
        setting('What Jarvis checks', 'The address of the browser tab in front of you, and your open tab titles to see whether LinkedIn or YouTube is still open. The tab title of a learning site goes to your model so the reminder fits the page. Addresses and titles are never saved or logged. The attempt count is kept on this PC. Lock in pauses while Jarvis is on a break.', null))));
  })();
  return { destroy: () => { alive = false; } };
}

export function voice(container, store) {
  const head = header('Voice input', 'Microphone and speech', 'Choose how Jarvis hears you. His speaking voice always stays on this PC.');
  let alive = true;
  let stop = null;
  (async () => {
    replace(container, head, loading());
    let data;
    try {
      data = await call('voice');
    } catch (error) {
      if (alive) replace(container, head, h('div', { class: 'card' }, empty('micOff', 'Microphone settings are unavailable', error.message)));
      return;
    }
    if (!alive) return;
    let device = data.device;
    let activeDevice = device;
    let model = data.model;
    let mode = data.mode;
    let reading = data.microphoneReady === false ? 'unavailable' : 'live';
    const meter = levelMeter('Microphone input level');
    const meterNote = h('div', { class: 'desc' });
    const LEVEL_NOTES = { live: 'Speak to check that Jarvis can hear you.',
                          muted: 'The microphone is muted. Unmute Jarvis to test it.',
                          unavailable: 'The microphone is not available right now.' };
    const explain = () => {
      const note = reading === 'live' && device !== activeDevice
        ? 'Shows the microphone in use. Save to switch.' : LEVEL_NOTES[reading] ?? LEVEL_NOTES.live;
      if (meterNote.textContent !== note) meterNote.textContent = note;
    };
    explain();
    const bar = saveBar(async () => {
      const saved = await call('voice_save', { device, model, mode });
      replace(status, statusView(saved));
      activeDevice = saved.device;
      explain();
    });
    const changed = () => bar.set(true);
    const MODES = { auto: 'Local Whisper small first, then Groq online if it fails.', offline: 'Speech stays on this PC. Basic local commands work without the AI service.',
                    online: 'Groq speech only.' };
    const modeNote = h('div', { class: 'desc' }, MODES[mode]);
    const statusView = (info) => h('div', { class: ['notice-bar', info.configured ? 'tone-success' : 'tone-warning'] },
      icon(info.configured ? 'check' : 'alert'),
      h('div', null, h('div', null, info.status), h('div', { class: 'muted' }, info.offlineInstalled
        ? 'Local Whisper small is installed.' : 'Local Whisper small is not installed. Run scripts/windows/setup-local-features.cmd to add it.'),
      info.microphoneReady === false ? h('div', { class: 'muted' }, 'The microphone is not available right now.') : null));
    const status = h('div', null, statusView(data));
    replace(container, head, h('div', { class: 'stack' }, status,
      card(setting('Microphone', 'The input device Jarvis listens on.', select(data.devices.map((row) => [row.index, row.name]), device, (value) => {
        device = value;
        explain();
        changed();
      })),
      h('div', { class: 'setting' }, h('div', null, h('div', { class: 'name' }, 'Input level'), meterNote), h('div', { class: 'control' }, meter)),
      setting('Speech to text', 'The Groq model for online speech and the automatic backup.', select(data.models.map((name) => [name, name]), model, (value) => {
        model = value;
        changed();
      })),
      h('div', { class: 'setting' }, h('div', null, h('div', { class: 'name' }, 'Speech recognition'), modeNote),
        h('div', { class: 'control' }, segmented([['auto', 'Automatic'], ['offline', 'Offline'], ['online', 'Online']], mode, (value) => {
          mode = value;
          modeNote.textContent = MODES[value];
          changed();
        })))),
      card(setting('Windows microphone privacy', 'Make sure desktop apps may use the microphone.',
        button('Open privacy settings', { icon: 'external', onClick: () => call('open', { target: 'microphone_privacy' }).catch(failure) })))),
    bar);
    const off = store.on((name, sample) => {
      if (name !== 'mic_level') return;
      meter.update(sample);
      if (sample?.status && sample.status !== reading) {
        reading = sample.status;
        explain();
      }
    });
    const beat = () => {
      if (!document.hidden) call('voice_meter', { on: true }).catch(() => {});
    };
    beat();
    const timer = setInterval(beat, 2000);
    document.addEventListener('visibilitychange', beat);
    stop = () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', beat);
      off();
      meter.destroy();
      call('voice_meter', { on: false }).catch(() => {});
    };
  })();
  return { destroy: () => { alive = false; stop?.(); } };
}

export function models(container, store) {
  const head = header('Intelligence', 'Models', 'Uses your existing ChatGPT/Codex sign-in. Your account’s model limits still apply.');
  let alive = true;
  let dirty = false;
  const load = async () => {
    replace(container, head, loading());
    let data;
    try {
      data = await call('models');
    } catch (error) {
      if (alive) replace(container, head, h('div', { class: 'card' }, empty('cpu', 'Models are unavailable', error.message)),
        h('div', null, button('Try again', { icon: 'refresh', onClick: load })));
      return;
    }
    if (!alive) return;
    const rows = data.models ?? [];
    const ids = new Set(rows.map((row) => row.id));
    let conversation = ids.has(data.conversationModel) ? data.conversationModel : rows[0]?.id;
    let search = ids.has(data.searchModel) ? data.searchModel : 'same';
    const bar = saveBar(async () => {
      await call('models_save', { conversationModel: conversation, searchModel: search, claudeBackup: backupOn });
      dirty = false;
    });
    // Claude takes over when OpenAI does not answer: Haiku 5.5 first, which hands harder requests to Opus 5.5 or Fable 5.2.
    const backup = data.backup ?? {};
    let backupOn = backup.enabled !== false;
    const describe = (state) => (!state.configured ? state.message
      : state.ready ? `Ready, using your Anthropic key at ${state.host}.` + (state.active ? ' Claude is answering right now.' : '')
        : state.enabled === false ? 'Turned off.' : state.message || 'Not working right now.');
    const backupStatus = h('p', { class: 'caption' }, describe(backup));
    const tester = button('Test Claude', { small: true, disabled: !backup.configured, onClick: async () => {
      backupStatus.textContent = 'Asking Claude Haiku 5.5 for one word...';
      try {
        const result = await call('models_backup_test');
        backupStatus.textContent = result.ok ? `Working. ${result.model} answered in ${(result.milliseconds / 1000).toFixed(1)} seconds.` : result.error;
      } catch (error) {
        backupStatus.textContent = error.message;
      }
    } });
    const changed = () => {
      dirty = true;
      bar.set(true);
    };
    replace(container, head, h('div', { class: 'stack' },
      card(setting('Conversation', 'Normal replies. Jarvis may still choose a stronger model for difficult requests.',
        select(rows.map((row) => [row.id, row.name]), conversation, (value) => {
          conversation = value;
          changed();
        })),
      setting('Background search and summarizing', 'Web and email research jobs.',
        select([['same', 'Same as conversation'], ...rows.map((row) => [row.id, row.name])], search, (value) => {
          search = value;
          changed();
        }))),
      h('p', { class: 'caption' }, 'Changes apply to new replies and searches. Running searches keep their current model.'),
      card(setting('Claude backup', 'When OpenAI does not answer, Claude Haiku 5.5 takes over and passes harder requests to Opus 5.5 or Fable 5.2. Billed to your Anthropic key.',
        toggle(backupOn, (value) => {
          backupOn = value;
          changed();
        }, 'Claude backup')),
      setting('Check the backup', 'Sends one short request to Claude.', tester)),
      backupStatus),
    bar);
  };
  load();
  const off = store.on((name) => {
    if (name === 'models_changed' && !dirty) load();
  });
  return { destroy: () => { alive = false; off(); } };
}

export function email(container, store) {
  const head = header('Mail', 'Email accounts', 'Gmail and Outlook, read only. Your accounts stay under your control.');
  let alive = true;
  const status = h('div', { class: 'prose' });
  const load = async () => {
    let data;
    try {
      data = await call('email');
    } catch (error) {
      data = { enabled: false, message: error.message };
    }
    if (!alive) return;
    if (!data.enabled) {
      replace(container, head, h('div', { class: 'notice-bar tone-warning' }, icon('mail'), h('span', null, data.message)));
      return;
    }
    status.textContent = store.state.emailStatus || data.status;
    replace(container, head, h('div', { class: 'stack' },
      h('div', { class: 'notice-bar tone-accent' }, icon('mail'), status),
      card(setting('Use accounts in Outlook classic', 'Add Gmail and Outlook in Outlook classic, then refresh. Jarvis reads them through your existing profile.',
        h('div', { class: 'control' }, button('Open Outlook classic', { icon: 'external', onClick: () => call('submit', { text: 'Open Outlook classic' }).catch(failure) }),
          button('Refresh', { icon: 'refresh', onClick: load })))),
      card(setting('Direct connections', 'An alternative if you have a Google or Microsoft app registration.',
        h('div', { class: 'control' }, button('Connect Gmail', { onClick: () => connect('gmail') }), button('Connect Outlook', { onClick: () => connect('outlook') }))),
      setting('Connection instructions', 'Step-by-step setup for both providers.', button('Open instructions', { icon: 'external', onClick: () => call('open', { target: 'email_help' }).catch(failure) }))),
      h('p', { class: 'caption' }, 'Message text goes to your selected model only when you ask about your mail.')));
  };
  async function connect(provider, extra = {}) {
    try {
      const result = await call('email_connect', { provider, ...extra });
      if (result?.needs === 'clientFile') {
        const file = await ui('pickFile', { title: 'Choose your Google desktop OAuth client JSON', filters: [{ name: 'JSON', extensions: ['json'] }] });
        if (file) connect(provider, { clientFile: file });
      } else if (result?.needs === 'clientId') {
        const id = await prompt({ eyebrow: 'Microsoft', title: 'App registration', confirm: 'Connect',
                                  text: 'Application client ID. Set its mobile and desktop redirect to http://localhost first.' });
        if (id) connect(provider, { clientId: id });
      } else if (result?.status) {
        status.textContent = result.status;
      }
    } catch (error) {
      failure(error);
    }
  }
  load();
  const off = store.on((name, data) => {
    if (name === 'email_status') status.textContent = data;
  });
  return { destroy: () => { alive = false; off(); } };
}
