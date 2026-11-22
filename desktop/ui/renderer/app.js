const OVERLAYS = new Set(['orb', 'companion', 'menu', 'pointer']);
const params = new URLSearchParams(location.search);
const view = params.get('view') || 'panel';

const report = (message) => window.jarvis.signal('log', { view, message: String(message).slice(0, 2000) });
window.addEventListener('error', (event) => report(`${event.message} at ${event.filename}:${event.lineno}`));
window.addEventListener('unhandledrejection', (event) => report(event.reason?.stack || event.reason));

document.body.dataset.view = view;
document.body.classList.add(OVERLAYS.has(view) ? 'overlay' : 'window');

try {
  const { connect } = await import('./lib/bridge.js');
  const store = await connect();
  const module = await import(`./views/${view}.js`);
  module.default(store, params);
  document.documentElement.dataset.ready = 'true';
} catch (error) {
  report(error?.stack || error);
  throw error;
}
