export function h(tag, props = null, ...children) {
  const element = tag instanceof Element ? tag : document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') element.className = Array.isArray(value) ? value.filter(Boolean).join(' ') : value;
    else if (key === 'style' && typeof value === 'object') {
      for (const [name, rule] of Object.entries(value)) {
        if (name.startsWith('--')) element.style.setProperty(name, rule);
        else element.style[name] = rule;
      }
    }
    else if (key === 'dataset') Object.assign(element.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') element.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'text') element.textContent = value;
    else if (key in element && typeof value !== 'string') element[key] = value;
    else element.setAttribute(key, value === true ? '' : String(value));
  }
  append(element, children);
  return element;
}

export function append(element, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

export function replace(element, ...children) {
  element.replaceChildren();
  return append(element, children);
}

export function plural(count, word, many = word + 's') {
  return `${count} ${count === 1 ? word : many}`;
}

export function hudText(text) {
  return String(text ?? '').toUpperCase();
}

const DATE = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
const TIME = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
const DAY = 86400000;

export function when(seconds) {
  const date = new Date(seconds * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const delta = Math.floor((date - today) / DAY);
  const day = delta === 0 ? 'Today' : delta === 1 ? 'Tomorrow' : delta === -1 ? 'Yesterday' : DATE.format(date);
  return `${day}, ${TIME.format(date)}`;
}

export function clock(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : TIME.format(date);
}

export function shortDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso ?? '').slice(0, 10);
  return when(date.getTime() / 1000);
}

export function elapsed(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m ${String(value % 60).padStart(2, '0')}s`;
  return `${Math.floor(value / 3600)}h ${String(Math.floor((value % 3600) / 60)).padStart(2, '0')}m`;
}

export function debounce(callback, delay) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => callback(...args), delay);
  };
}
