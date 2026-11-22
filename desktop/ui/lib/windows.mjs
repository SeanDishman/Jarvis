import { BrowserWindow, screen } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ORB_SIZE, besideOrb, centered, companionBounds, fitImage, intersection, matchDisplay, menuPosition, orbHome,
         orbVisit, rectFromEdges, screenChoices, visibleEnough } from './geometry.mjs';
import { REST, boxFor, plan, sample, totalMs, visitScale } from '../renderer/lib/motion.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PRELOAD = path.join(HERE, '..', 'preload.cjs');
const PAGE = 'jarvis://ui/index.html';
const BACKGROUND = '#070b14';
const COMPANION = { width: 324, height: 560 };
const COMPANION_PAD = 12;
const MENU_PAD = 14;
const OVERLAYS = new Set(['orb', 'companion', 'menu', 'pointer']);
// Jarvis's own pointer during PC tasks: a click-through overlay whose arrow tip sits at tip inside the window.
export const POINTER = { size: 72, tip: { x: 18, y: 14 }, glideMs: 380 };
const VISIT_MS = { default: 20000, min: 1000, max: 60000 };
// How long the window manager waits past a planned flight before finishing it without the orb view.
const FLIGHT_GRACE_MS = 600;
// A lock-in card arrives just before its visit starts; keep it from flashing beside the corner first.
const CARD_LEAD_MS = 400;
const REMEMBERED = new Set(['panel', 'console', 'settings']);
const KINDS = {
  orb: { width: ORB_SIZE, height: ORB_SIZE, focusable: false },
  companion: { width: COMPANION.width, height: COMPANION.height, focusable: false },
  menu: { width: 288, height: 520, focusable: true },
  panel: { title: 'Jarvis', width: 460, height: 640, minWidth: 380, minHeight: 460, alwaysOnTop: true },
  console: { title: 'Jarvis console', width: 1120, height: 720, minWidth: 820, minHeight: 520 },
  settings: { title: 'Jarvis settings', width: 920, height: 690, minWidth: 760, minHeight: 520 },
  snapshot: { title: 'Jarvis screen snapshot', width: 940, height: 720, minWidth: 480, minHeight: 360 },
  pointer: { width: POINTER.size, height: POINTER.size, focusable: false },
};

export class Windows {
  constructor({ store, icon, profile, isQuitting }) {
    this.store = store;
    this.icon = icon && fs.existsSync(icon) ? icon : undefined;
    this.isQuitting = isQuitting;
    this.statePath = path.join(profile, 'window-state.json');
    this.saved = this.loadState();
    this.windows = new Map();
    this.hidden = false;
    this.restore = [];
    this.drag = null;
    this.menuCursor = null;
    this.companionWanted = false;
    this.saveTimer = null;
    this.visit = null;
    this.flights = 0;
    this.visitExpected = null;
  }

  start() {
    this.create('orb');
    this.create('companion');
    this.create('menu');
    this.placeOrb();
    this.setHidden(Boolean(this.store.hidden), true);
  }

  get(kind) {
    const win = this.windows.get(kind);
    return win && !win.isDestroyed() ? win : null;
  }

  displays() {
    return screen.getAllDisplays();
  }

  toDip(rect) {
    try {
      return screen.screenToDipRect(null, rect);
    } catch {
      return rect;
    }
  }

  orbBounds() {
    if (this.visit) return this.visualBounds();
    return this.get('orb')?.getBounds() ?? orbHome(screen.getPrimaryDisplay().workArea);
  }

  orbDisplay() {
    const payload = this.store.screens;
    const row = payload?.screens?.find((item) => item.device === payload.preferred);
    return matchDisplay(row, this.displays(), (rect) => this.toDip(rect)) ?? screen.getPrimaryDisplay();
  }

  screenChoices() {
    return screenChoices(this.store.screens, this.displays(), (rect) => this.toDip(rect));
  }

  defaultBounds(kind, size) {
    const orb = this.orbBounds();
    const work = screen.getDisplayMatching(orb).workArea;
    if (kind === 'panel') return besideOrb(orb, work, size);
    return centered(work, size);
  }

  create(kind, query = {}) {
    const spec = KINDS[kind];
    const overlay = OVERLAYS.has(kind);
    let bounds = { width: spec.width, height: spec.height };
    if (!overlay) {
      const size = kind === 'snapshot' && query.image ? snapshotSize(query.image) : bounds;
      const saved = REMEMBERED.has(kind) ? this.saved[kind] : null;
      const workAreas = this.displays().map((display) => display.workArea);
      bounds = saved && visibleEnough(saved, workAreas) ? saved : this.defaultBounds(kind, size);
    }
    const common = {
      ...bounds, show: false, title: spec.title ?? 'Jarvis', icon: this.icon, autoHideMenuBar: true,
      backgroundColor: overlay ? '#00000000' : BACKGROUND,
      webPreferences: {
        preload: PRELOAD, contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true,
        spellcheck: kind === 'panel' || kind === 'console', backgroundThrottling: !overlay,
      },
    };
    const options = overlay ? {
      ...common, frame: false, transparent: true, resizable: false, minimizable: false, maximizable: false,
      fullscreenable: false, skipTaskbar: true, alwaysOnTop: true, hasShadow: false, thickFrame: false,
      roundedCorners: false, focusable: spec.focusable,
    } : {
      ...common, minWidth: spec.minWidth, minHeight: spec.minHeight, alwaysOnTop: Boolean(spec.alwaysOnTop),
      titleBarStyle: 'hidden', titleBarOverlay: { color: BACKGROUND, symbolColor: '#8a9fba', height: 40 },
    };
    const win = new BrowserWindow(options);
    this.windows.set(kind, win);
    win.webContents.on('will-navigate', (event) => event.preventDefault());
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('render-process-gone', (_event, details) => {
      console.error(`Jarvis ${kind} view stopped: ${details.reason}`);
      if (!this.isQuitting() && !win.isDestroyed()) setTimeout(() => !win.isDestroyed() && win.reload(), 500);
    });
    if (overlay) {
      win.setAlwaysOnTop(true, kind === 'menu' ? 'pop-up-menu' : 'screen-saver');
      win.on('close', (event) => {
        if (!this.isQuitting()) {
          event.preventDefault();
          win.hide();
        }
      });
    }
    if (kind === 'orb' || kind === 'companion') win.setIgnoreMouseEvents(true, { forward: true });
    // The owner clicks straight through Jarvis's pointer.
    if (kind === 'pointer') win.setIgnoreMouseEvents(true);
    if (kind === 'orb') win.on('move', () => this.layoutCompanion());
    if (kind === 'menu') win.on('blur', () => this.closeMenu());
    if (REMEMBERED.has(kind)) {
      const remember = () => this.remember(kind, win);
      win.on('resize', remember);
      win.on('move', remember);
    }
    win.on('closed', () => {
      if (this.windows.get(kind) === win) this.windows.delete(kind);
      this.restore = this.restore.filter((row) => row.kind !== kind);
      if (kind === 'snapshot') this.store.snapshot = null;
      if (kind === 'panel') this.dispatch('panel', { visible: false }, ['companion']);
    });
    if (kind === 'panel') {
      for (const name of ['show', 'restore']) win.on(name, () => this.dispatch('panel', { visible: true }, ['companion']));
      for (const name of ['hide', 'minimize']) win.on(name, () => this.dispatch('panel', { visible: false }, ['companion']));
    }
    const search = new URLSearchParams({ view: kind });
    for (const key of ['view', 'select']) if (query[key]) search.set(key === 'view' ? 'section' : key, String(query[key]));
    win.loadURL(`${PAGE}?${search}`);
    return win;
  }

  open(kind, { view, select, focus = true, automatic = false } = {}) {
    if (kind === 'orb') {
      if (!this.hidden) {
        this.get('orb')?.showInactive();
        this.get('orb')?.moveTop();
      }
      return;
    }
    if (!KINDS[kind] || OVERLAYS.has(kind)) return;
    let win = this.get(kind);
    if (automatic && win) return;
    const created = !win;
    if (created) {
      win = this.create(kind, { view, select, image: kind === 'snapshot' ? this.store.snapshot : null });
    } else if (view || select) {
      win.webContents.send('jarvis:event', 'navigate', { view, select });
    }
    if (this.hidden) {
      if (!this.restore.some((row) => row.kind === kind)) this.restore.push({ kind, minimized: false });
      return;
    }
    const reveal = () => {
      if (win.isDestroyed() || this.hidden) return;
      if (focus) {
        if (win.isMinimized()) win.restore();
        win.show();
        win.moveTop();
        win.focus();
      } else if (!win.isVisible() || win.isMinimized()) {
        win.showInactive();
      }
    };
    if (created) win.once('ready-to-show', reveal);
    else reveal();
  }

  openSnapshot(data) {
    if (!data?.image) return;
    const existing = this.get('snapshot');
    if (existing) existing.webContents.send('jarvis:event', 'snapshot', data);
    this.open('snapshot', { focus: false });
  }

  dispatch(name, data, kinds = null) {
    for (const [kind, win] of this.windows) {
      if (win.isDestroyed() || (kinds && !kinds.includes(kind))) continue;
      if (name === 'snapshot' && kind !== 'snapshot') continue;
      win.webContents.send('jarvis:event', name, data);
    }
  }

  setHidden(hidden, initial = false) {
    if (hidden === this.hidden && !initial) return;
    this.hidden = hidden;
    this.closeMenu();
    const orb = this.get('orb');
    if (hidden) {
      if (this.visit) this.collapse(this.visit.home);
      this.restore = [];
      for (const [kind, win] of this.windows) {
        if (win.isDestroyed() || OVERLAYS.has(kind) || !win.isVisible()) continue;
        this.restore.push({ kind, minimized: win.isMinimized() });
        win.hide();
      }
      orb?.hide();
      this.get('companion')?.hide();
      this.agentPointer({ hide: true });
      return;
    }
    if (orb) {
      orb.showInactive();
      orb.moveTop();
    }
    for (const { kind, minimized } of this.restore) {
      const win = this.get(kind);
      if (!win) continue;
      if (minimized) win.minimize();
      else win.showInactive();
    }
    this.restore = [];
    this.syncCompanion();
  }

  placeOrb() {
    const visit = this.endVisit();
    const orb = this.get('orb');
    if (!orb) return;
    orb.setBounds(orbHome(this.orbDisplay().workArea));
    if (visit) {
      orb.webContents.send('jarvis:event', 'orb_motion', { reset: true });
      this.syncCompanion();
    }
    this.layoutCompanion();
  }

  // Jarvis's own pointer during a PC task. It glides to each point he acts on (physical screen pixels), shows what
  // he does there, and never moves or replaces the owner's pointer. The first move starts from the orb.
  agentPointer({ x, y, action = 'move', hide = false } = {}) {
    if (hide || this.hidden) {
      clearInterval(this.pointerGlide);
      this.pointerGlide = null;
      this.get('pointer')?.hide();
      return null;
    }
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    const win = this.get('pointer') ?? this.create('pointer');
    let point;
    try {
      point = screen.screenToDipPoint({ x, y });
    } catch {
      point = { x, y };
    }
    const to = { x: Math.round(point.x - POINTER.tip.x), y: Math.round(point.y - POINTER.tip.y) };
    const shown = win.isVisible();
    const orb = this.orbBounds();
    const from = shown ? win.getBounds()
      : { x: Math.round(orb.x + orb.width / 2 - POINTER.tip.x), y: Math.round(orb.y + orb.height / 2 - POINTER.tip.y) };
    const size = { width: POINTER.size, height: POINTER.size };
    clearInterval(this.pointerGlide);
    win.setBounds({ x: from.x, y: from.y, ...size });
    if (!shown) win.showInactive();
    win.moveTop();
    const started = Date.now();
    const step = () => {
      if (win.isDestroyed()) return clearInterval(this.pointerGlide);
      const t = Math.min(1, (Date.now() - started) / POINTER.glideMs);
      const k = 1 - (1 - t) ** 3;
      win.setBounds({ x: Math.round(from.x + (to.x - from.x) * k), y: Math.round(from.y + (to.y - from.y) * k), ...size });
      if (t >= 1) {
        clearInterval(this.pointerGlide);
        this.pointerGlide = null;
        win.webContents.send('jarvis:event', 'pointer_action', { action });
      }
      return null;
    };
    this.pointerGlide = setInterval(step, 16);
    return { from, to };
  }

  // Lock-in and welcome-back visits: Jarvis glides from wherever he rests into the middle of the target, a browser
  // window or the main monitor, and grows there. Lock-in uses the default scale; a welcome grows him further.
  visitOrb(rect, holdMs = VISIT_MS.default, scale) {
    const orb = this.get('orb');
    if (!orb) throw new Error('The Jarvis orb is unavailable');
    if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(Number.isFinite) || rect[2] <= rect[0] || rect[3] <= rect[1]) {
      throw new Error('The browser window position is unavailable');
    }
    const grow = visitScale(scale);
    const target = this.toDip(rectFromEdges(rect));
    const work = screen.getDisplayMatching(target).workArea;
    this.visitExpected = null;
    if (!this.visit) {
      const home = orb.getBounds();
      const size = boxFor(grow);
      this.visit = { home, timer: null, flight: null, fallback: null, box: size, held: size / 2, scale: grow,
                     rest: { center: { x: home.x + REST, y: home.y + REST }, offset: { x: REST, y: REST }, scale: 1 } };
      // Grow the window toward the bottom right only, so the orb drawn in its top-left corner does not move.
      orb.setBounds({ x: home.x, y: home.y, width: size, height: size });
    } else if (boxFor(grow) > this.visit.box) {
      // A bigger visit arriving during a smaller one: widen the window in place, so the orb drawn in it stays put.
      const size = boxFor(grow);
      const now = orb.getBounds();
      this.visit.box = size;
      this.visit.held = size / 2;
      orb.setBounds({ x: now.x, y: now.y, width: size, height: size });
    }
    this.visit.scale = grow;
    const held = this.visit.held;
    const box = orbVisit(target, work, this.visit.box);
    const center = { x: box.x + held, y: box.y + held };
    clearTimeout(this.visit.timer);
    const hold = Math.min(Math.max(Number(holdMs) || VISIT_MS.default, VISIT_MS.min), VISIT_MS.max);
    this.visit.timer = setTimeout(() => this.returnOrb(), hold);
    const heading = this.visit.flight ? this.visit.flight.spec : null;
    const there = heading ? !heading.land && near(heading.to, center) && heading.scale.to === grow
      : near(this.visit.rest.center, center) && this.visit.rest.scale === grow;
    if (this.hidden) this.hold(center);
    else if (!there) this.fly(center, grow, { x: held, y: held }, false);
    if (!this.hidden) {
      orb.showInactive();
      orb.moveTop();
    }
    return { orb: { bounds: box, visible: orb.isVisible() }, target,
             verified: intersection(box, work) === box.width * box.height && intersection(box, target) > 0 };
  }

  returnOrb() {
    const visit = this.visit;
    const orb = this.get('orb');
    if (!visit || !orb) return { returned: false };
    clearTimeout(visit.timer);
    visit.timer = null;
    if (this.hidden) {
      this.collapse(visit.home);
      return { returned: true, home: visit.home };
    }
    if (!visit.flight?.spec.land) {
      this.fly({ x: visit.home.x + REST, y: visit.home.y + REST }, 1, { x: REST, y: REST }, true);
    }
    return { returned: true, home: visit.home };
  }

  endVisit() {
    const visit = this.visit;
    this.visit = null;
    if (visit) {
      clearTimeout(visit.timer);
      clearTimeout(visit.fallback);
    }
    return visit;
  }

  // Where Jarvis is on screen right now during a visit, by the same timeline the orb view is drawing.
  visitState(now = performance.now()) {
    const flight = this.visit?.flight;
    if (!flight) return this.visit?.rest ?? null;
    const state = sample(flight.spec, now - flight.start);
    return { center: state.center, offset: state.offset, scale: state.scale };
  }

  visualBounds() {
    const state = this.visitState();
    const size = Math.round(ORB_SIZE * state.scale);
    return { x: Math.round(state.center.x - size / 2), y: Math.round(state.center.y - size / 2), width: size, height: size };
  }

  // Cards wait while he flies in and appear once he lands; on the way home they stay with him.
  arriving() {
    const flight = this.visit?.flight;
    return Boolean(flight && !flight.spec.land) || (this.visitExpected !== null && Date.now() < this.visitExpected);
  }

  // The lock-in card is on its way; its visit request follows right behind it.
  expectVisit() {
    if (this.visit || this.hidden) return;
    this.visitExpected = Date.now() + CARD_LEAD_MS;
    setTimeout(() => {
      if (this.visitExpected !== null && Date.now() >= this.visitExpected) {
        this.visitExpected = null;
        this.syncCompanion();
      }
    }, CARD_LEAD_MS + 20);
  }

  fly(center, scale, offset, returning) {
    const visit = this.visit;
    const orb = this.get('orb');
    if (!visit || !orb) return;
    const now = performance.now();
    const from = this.visitState(now);
    const spec = { ...plan({ id: ++this.flights, from: from.center, to: center, offset: { from: from.offset, to: offset },
                             scale: { from: from.scale, to: scale }, returning }), box: visit.box };
    visit.flight = { spec, start: now, x: null, y: null };
    clearTimeout(visit.fallback);
    visit.fallback = setTimeout(() => this.finishFlight(spec.id, true), totalMs(spec) + FLIGHT_GRACE_MS);
    orb.webContents.send('jarvis:event', 'orb_motion', spec);
    this.syncCompanion();
  }

  // Jump straight to the held pose; used while the interface is hidden, when nobody can see a flight.
  hold(center) {
    const visit = this.visit;
    const orb = this.get('orb');
    if (!visit || !orb) return;
    clearTimeout(visit.fallback);
    visit.flight = null;
    const { box, held, scale } = visit;
    visit.rest = { center, offset: { x: held, y: held }, scale };
    orb.setBounds({ x: Math.round(center.x - held), y: Math.round(center.y - held), width: box, height: box });
    orb.webContents.send('jarvis:event', 'orb_motion', { hold: { cx: held, cy: held, scale } });
  }

  // The orb view reports each frame where the window has to be for the orb it just drew.
  orbPlace({ id, x, y, done } = {}) {
    const flight = this.visit?.flight;
    const orb = this.get('orb');
    if (!flight || !orb || flight.spec.id !== id || !Number.isFinite(x) || !Number.isFinite(y)) return;
    const left = Math.round(x);
    const top = Math.round(y);
    if (left !== flight.x || top !== flight.y) {
      flight.x = left;
      flight.y = top;
      orb.setBounds({ x: left, y: top, width: this.visit.box, height: this.visit.box });
    }
    if (done === true) this.finishFlight(id, false);
  }

  finishFlight(id, forced) {
    const visit = this.visit;
    const flight = visit?.flight;
    const orb = this.get('orb');
    if (!flight || flight.spec.id !== id) return;
    clearTimeout(visit.fallback);
    visit.flight = null;
    const end = sample(flight.spec, Infinity);
    visit.rest = { center: end.center, offset: end.offset, scale: end.scale };
    if (flight.spec.land) {
      this.collapse(visit.home);
      return;
    }
    if (forced && orb) {
      const { box, held } = visit;
      orb.setBounds({ x: Math.round(end.center.x - held), y: Math.round(end.center.y - held), width: box, height: box });
      orb.webContents.send('jarvis:event', 'orb_motion', { hold: { cx: held, cy: held, scale: end.scale } });
    }
    this.syncCompanion();
  }

  // End the visit at once and put the resting orb at bounds.
  collapse(bounds) {
    const orb = this.get('orb');
    this.endVisit();
    if (orb) {
      orb.setBounds(bounds);
      orb.webContents.send('jarvis:event', 'orb_motion', { reset: true });
    }
    this.syncCompanion();
  }

  interfaceState(payload = this.store.screens) {
    const orb = this.get('orb');
    if (!orb) throw new Error('The Jarvis orb is unavailable');
    const bounds = orb.getBounds();
    const display = screen.getDisplayMatching(bounds);
    const choices = screenChoices(payload, this.displays(), (rect) => this.toDip(rect));
    const current = choices.find((choice) => choice.displayId === display.id);
    if (!current) throw new Error('The orb monitor could not be identified');
    return { orb: { monitor: current.device, bounds, visible: orb.isVisible() } };
  }

  moveOrb(payload) {
    const row = payload?.screens?.find((item) => item.device === payload.selected);
    const target = matchDisplay(row, this.displays(), (rect) => this.toDip(rect));
    if (!target) throw new Error('The requested monitor is no longer connected');
    this.store.screens = payload;
    this.placeOrb();
    const result = this.interfaceState(payload);
    if (result.orb.monitor !== row.device) throw new Error('The orb did not reach the requested monitor');
    return { ...result, verified: true };
  }

  keepOrbOnScreen() {
    const orb = this.get('orb');
    if (!orb) return;
    const bounds = orb.getBounds();
    const center = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
    const inside = this.displays().some(({ workArea: w }) => center.x >= w.x && center.x < w.x + w.width
                                                           && center.y >= w.y && center.y < w.y + w.height);
    if (inside) this.layoutCompanion();
    else this.placeOrb();
  }

  orbDrag(phase) {
    const orb = this.get('orb');
    if (!orb) return;
    const cursor = screen.getCursorScreenPoint();
    if (phase === 'start') {
      this.drag = { cursor, bounds: orb.getBounds() };
    } else if (phase === 'move' && this.drag) {
      if (this.visit) {
        // Grabbing Jarvis during a visit ends it where he is, back at his normal size.
        const { center } = this.visitState();
        const bounds = { x: Math.round(center.x - REST), y: Math.round(center.y - REST), width: ORB_SIZE, height: ORB_SIZE };
        this.collapse(bounds);
        this.drag.bounds = bounds;
      }
      const { cursor: start, bounds } = this.drag;
      orb.setBounds({ x: bounds.x + cursor.x - start.x, y: bounds.y + cursor.y - start.y, width: ORB_SIZE, height: ORB_SIZE });
      this.layoutCompanion();
    } else if (phase === 'end') {
      this.drag = null;
    }
  }

  capture(kind, capture) {
    const win = this.get(kind);
    if (!win) return;
    if (capture) win.setIgnoreMouseEvents(false);
    else win.setIgnoreMouseEvents(true, { forward: true });
  }

  layoutCompanion() {
    const companion = this.get('companion');
    if (!companion || this.arriving()) return;
    const orb = this.orbBounds();
    const work = screen.getDisplayMatching(orb).workArea;
    const bounds = companionBounds(orb, work, COMPANION, { margin: 8 - COMPANION_PAD, overlap: 8 });
    companion.setBounds({ x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height });
    if (bounds.anchor !== this.anchor) {
      this.anchor = bounds.anchor;
      companion.webContents.send('jarvis:event', 'anchor', bounds.anchor);
    }
  }

  companionState(visible) {
    this.companionWanted = Boolean(visible);
    this.syncCompanion();
  }

  syncCompanion() {
    const companion = this.get('companion');
    if (!companion) return;
    if (this.companionWanted && !this.hidden && !this.arriving()) {
      this.layoutCompanion();
      if (!companion.isVisible()) companion.showInactive();
      companion.moveTop();
    } else if (companion.isVisible()) {
      companion.hide();
    }
  }

  openMenu() {
    const menu = this.get('menu');
    if (!menu || this.hidden) return;
    this.menuCursor = screen.getCursorScreenPoint();
    menu.webContents.send('jarvis:event', 'menu', null);
  }

  menuSize(size) {
    const menu = this.get('menu');
    if (!menu || !this.menuCursor) return;
    const work = screen.getDisplayNearestPoint(this.menuCursor).workArea;
    const width = Math.ceil(size.width);
    const height = Math.min(Math.ceil(size.height), work.height + MENU_PAD * 2);
    const position = menuPosition(this.menuCursor, work, { width, height }, MENU_PAD);
    menu.setBounds({ ...position, width, height });
    if (!menu.isVisible()) {
      menu.show();
      menu.focus();
    }
  }

  closeMenu() {
    const menu = this.get('menu');
    this.menuCursor = null;
    if (menu?.isVisible()) {
      menu.hide();
      menu.webContents.send('jarvis:event', 'menu_closed', null);
    }
  }

  windowAction(win, action) {
    if (!win || win.isDestroyed()) return;
    if (action === 'close') win.close();
    else if (action === 'minimize') win.minimize();
  }

  inspect() {
    const rows = {};
    for (const [kind, win] of this.windows) {
      if (win.isDestroyed()) continue;
      rows[kind] = { visible: win.isVisible(), minimized: win.isMinimized(), focused: win.isFocused(),
                     bounds: win.getBounds(), title: win.getTitle(), pid: win.webContents.getOSProcessId(),
                     handle: Number(win.getNativeWindowHandle().readBigUInt64LE(0)) };
    }
    return { hidden: this.hidden, restore: this.restore, windows: rows };
  }

  loadState() {
    try {
      const data = JSON.parse(fs.readFileSync(this.statePath, 'utf8'));
      return data && typeof data === 'object' ? data : {};
    } catch {
      return {};
    }
  }

  remember(kind, win) {
    if (win.isDestroyed() || win.isMinimized() || win.isMaximized() || !win.isVisible()) return;
    this.saved[kind] = win.getBounds();
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.saveState(), 600);
  }

  saveState() {
    clearTimeout(this.saveTimer);
    try {
      fs.mkdirSync(path.dirname(this.statePath), { recursive: true });
      const temporary = this.statePath + '.tmp';
      fs.writeFileSync(temporary, JSON.stringify(this.saved, null, 2));
      fs.renameSync(temporary, this.statePath);
    } catch (error) {
      console.error('Could not save window positions:', error.message);
    }
  }
}

function near(a, b, tolerance = 2) {
  return Boolean(a && b) && Math.abs(a.x - b.x) <= tolerance && Math.abs(a.y - b.y) <= tolerance;
}

function snapshotSize(image) {
  const fitted = fitImage(image, { width: 900, height: 600 });
  return { width: Math.max(560, fitted.width + 48), height: Math.max(420, fitted.height + 190) };
}
