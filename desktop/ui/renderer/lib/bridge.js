const api = window.jarvis;

export async function call(method, params = {}) {
  const reply = await api.call(method, params);
  if (!reply.ok) throw new Error(reply.error || 'Jarvis could not complete that action.');
  return reply.result;
}

export async function ui(action, params = {}) {
  const reply = await api.ui(action, params);
  if (!reply.ok) throw new Error(reply.error || 'The interface could not complete that action.');
  return reply.result;
}

export function signal(action, params = {}) {
  api.signal(action, params);
}

const FIELDS = {
  state: 'state', conversation: 'conversation', model: 'model', jobs: 'jobs', progress: 'progress',
  muted: 'muted', startup: 'startup', settings: 'settings', screens: 'screens', browser: 'browserNote',
  email_status: 'emailStatus', files: 'files', cards: 'cards', snapshot: 'snapshot',
};

export class Store {
  constructor(initial) {
    this.state = initial;
    this.listeners = new Set();
  }

  receive(name, data) {
    if (name === 'visibility') this.state.hidden = Boolean(data?.hidden);
    else if (FIELDS[name]) this.state[FIELDS[name]] = data;
    for (const listener of this.listeners) listener(name, data, this.state);
  }

  on(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}

export async function connect() {
  const early = [];
  let store = null;
  api.on((name, data) => (store ? store.receive(name, data) : early.push([name, data])));
  store = new Store(await ui('store'));
  for (const [name, data] of early) store.receive(name, data);
  return store;
}

export function openWindow(window, options = {}) {
  return ui('open', { window, focus: true, ...options });
}
