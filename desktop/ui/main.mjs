import { BrowserWindow, Menu, app, dialog, ipcMain, nativeTheme, net, protocol, screen, session } from 'electron';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { format } from 'node:util';
import { connectBackend } from './lib/backend.mjs';
import { Windows } from './lib/windows.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RENDERER = path.join(HERE, 'renderer');
const ROOT = path.resolve(HERE, '..', '..');
const ORIGIN = 'jarvis://ui/';

for (const name of ['log', 'info', 'debug', 'warn']) {
  console[name] = (...args) => process.stderr.write(format(...args) + '\n');
}

app.setPath('userData', process.env.JARVIS_UI_PROFILE || path.join(ROOT, '.cache', 'jarvis-ui', 'profile'));
app.setAppUserModelId('Jarvis.Desktop');
if (process.env.JARVIS_UI_DEBUG_PORT) app.commandLine.appendSwitch('remote-debugging-port', process.env.JARVIS_UI_DEBUG_PORT);
protocol.registerSchemesAsPrivileged([
  { scheme: 'jarvis', privileges: { standard: true, secure: true, supportFetchAPI: true, codeCache: true } },
]);
nativeTheme.themeSource = 'dark';
Menu.setApplicationMenu(null);

const store = {
  state: { state: 'starting', name: 'Starting', note: 'Starting Jarvis' },
  conversation: [], greeting: '', model: 'Automatic model choice',
  jobs: { rows: [], busy: false, note: 'Activity' }, progress: [], hidden: false, muted: false, startup: false,
  screens: { screens: [], selected: null, preferred: null }, settings: { showTaskPopups: false },
  emailEnabled: false, emailStatus: '', browserNote: '', desktop: 'Default', microphoneReady: false,
  files: null, snapshot: null, cards: [],
};
let windows = null;
let quitting = false;
let cardSerial = 0;

const port = Number(process.env.JARVIS_UI_PORT);
if (!port || !process.env.JARVIS_UI_KEY) {
  console.error('Start Jarvis with start-jarvis.cmd. The interface needs the running assistant.');
  app.exit(2);
}
const backend = connectBackend({ port, key: process.env.JARVIS_UI_KEY, onEvent: receive, onClose: quit,
  onRequest(method, params) {
    if (!windows || quitting) throw new Error('The Jarvis interface is unavailable');
    if (method === 'inspect_interface') return windows.interfaceState(params.screens);
    if (method === 'move_orb') {
      const result = windows.moveOrb(params.screens);
      windows.dispatch('screens', store.screens);
      return result;
    }
    if (method === 'visit_orb') return windows.visitOrb(params.rect, params.holdMs, params.scale);
    if (method === 'return_orb') return windows.returnOrb();
    throw new Error('Unknown interface request');
  },
});

function receive(name, data) {
  switch (name) {
    case 'state': store.state = data; break;
    case 'conversation': store.conversation = data ?? []; break;
    case 'model': store.model = data; break;
    case 'jobs': store.jobs = data; break;
    case 'progress': store.progress = data ?? []; break;
    case 'muted': store.muted = Boolean(data); break;
    case 'startup': store.startup = Boolean(data); break;
    case 'settings': store.settings = data; break;
    case 'browser': store.browserNote = data; break;
    case 'email_status': store.emailStatus = data; break;
    case 'files': store.files = data; break;
    case 'screens':
      store.screens = data;
      windows?.placeOrb();
      break;
    case 'snapshot':
      store.snapshot = data;
      windows?.openSnapshot(data);
      return;
    case 'lockin':
      windows?.expectVisit();
      addCard(name, data);
      return;
    case 'agent_pointer':
      windows?.agentPointer(data ?? {});
      return;
    case 'reminder':
    case 'alert':
      addCard(name, data);
      return;
    case 'visibility':
      store.hidden = Boolean(data?.hidden);
      windows?.setHidden(store.hidden);
      break;
    case 'open':
      windows?.open(data?.window, data ?? {});
      return;
    case 'quit':
      quit();
      return;
    case 'level':
    case 'pulse':
      windows?.dispatch(name, data, ['orb', 'panel']);
      return;
    case 'mic_level':
      windows?.dispatch(name, data, ['settings']);
      return;
    default:
      break;
  }
  windows?.dispatch(name, data);
}

function addCard(kind, row) {
  if (!row) return;
  if (kind === 'reminder' && store.cards.some((card) => card.kind === 'reminder' && card.row.id === row.id)) return;
  store.cards = [...store.cards.filter((card) => kind === 'reminder' || card.kind !== kind),
                 { id: `card-${++cardSerial}`, kind, row, at: Date.now() }].slice(-6);
  windows?.dispatch('cards', store.cards);
}

function dismissCard(id) {
  store.cards = store.cards.filter((card) => card.id !== id);
  windows?.dispatch('cards', store.cards);
}

function quit() {
  if (quitting) return;
  quitting = true;
  windows?.saveState();
  if (app.isReady()) app.quit();
  else app.exit(0);
}

function trusted(event) {
  return String(event.senderFrame?.url ?? '').startsWith(ORIGIN);
}

async function local(action, params, win) {
  switch (action) {
    case 'store': return store;
    case 'open': return windows.open(params.window, params);
    case 'window': return windows.windowAction(win, params.action);
    case 'pickFolder': {
      const result = await dialog.showOpenDialog(win, { title: params.title || 'Choose a folder', properties: ['openDirectory'] });
      return result.canceled ? null : result.filePaths[0] ?? null;
    }
    case 'pickFile': {
      const result = await dialog.showOpenDialog(win, { title: params.title || 'Choose a file', properties: ['openFile'],
                                                        filters: params.filters ?? [] });
      return result.canceled ? null : result.filePaths[0] ?? null;
    }
    case 'orb.drag': return windows.orbDrag(params.phase);
    case 'orb.place': return windows.orbPlace(params);
    case 'orb.capture': return windows.capture('orb', Boolean(params.capture));
    case 'menu.open': return windows.openMenu();
    case 'menu.size': return windows.menuSize(params);
    case 'menu.close': return windows.closeMenu();
    case 'companion.state': return windows.companionState(params.visible);
    case 'companion.capture': return windows.capture('companion', Boolean(params.capture));
    case 'card.dismiss': return dismissCard(params.id);
    case 'screens': return windows.screenChoices();
    case 'inspect': return windows.inspect();
    case 'log': return console.error(`Jarvis ${params.view ?? 'window'} view: ${params.message}`);
    default: throw new Error('Unknown interface action');
  }
}

ipcMain.handle('jarvis:call', async (event, method, params) => {
  if (!trusted(event) || typeof method !== 'string') return { ok: false, error: 'Not allowed' };
  try {
    return { ok: true, result: await backend.call(method, params ?? {}) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

ipcMain.handle('jarvis:ui', async (event, action, params) => {
  if (!trusted(event) || !windows) return { ok: false, error: 'Not allowed' };
  try {
    return { ok: true, result: (await local(action, params ?? {}, BrowserWindow.fromWebContents(event.sender))) ?? null };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

ipcMain.on('jarvis:signal', (event, action, params) => {
  if (!trusted(event) || !windows) return;
  Promise.resolve(local(action, params ?? {}, BrowserWindow.fromWebContents(event.sender))).catch((error) => {
    console.error(`Interface action ${action} failed:`, error.message);
  });
});

async function serve(request) {
  const url = new URL(request.url);
  if (url.host !== 'ui') return new Response('Not found', { status: 404 });
  const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '') || 'index.html';
  const target = path.resolve(RENDERER, relative);
  if (!target.startsWith(RENDERER + path.sep)) return new Response('Forbidden', { status: 403 });
  return net.fetch(pathToFileURL(target).toString());
}

async function refreshScreens() {
  try {
    store.screens = await backend.call('screens', {}, 10000);
    windows?.dispatch('screens', store.screens);
  } catch {
    return;
  }
  windows?.keepOrbOnScreen();
}

app.on('window-all-closed', () => {});
app.on('before-quit', () => {
  quitting = true;
});

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  protocol.handle('jarvis', serve);
  try {
    Object.assign(store, await backend.call('snapshot', {}, 30000));
  } catch (error) {
    console.error('Jarvis did not provide its starting state:', error.message);
  }
  if (backend.closed || quitting) {
    app.exit(0);
    return;
  }
  windows = new Windows({ store, icon: process.env.JARVIS_UI_ICON, profile: app.getPath('userData'),
                          isQuitting: () => quitting });
  windows.start();
  for (const name of ['display-added', 'display-removed', 'display-metrics-changed']) {
    screen.on(name, () => refreshScreens());
  }
  backend.ready();
});
