const PATHS = {
  activity: 'M3 12h4l3 8 4-16 3 8h4',
  projects: 'M12 3 2 8l10 5 10-5-10-5Z|M2 13l10 5 10-5|M2 18l10 5 10-5',
  bell: 'M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8|M10.3 21a1.94 1.94 0 0 0 3.4 0',
  alert: 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z|M12 9v4|M12 17h.01',
  files: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z|M14 2v6h6|M8 13h8|M8 17h5',
  globe: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z|M2 12h20|M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10Z',
  settings: 'M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z|M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  mic: 'M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z|M19 10v2a7 7 0 0 1-14 0v-2|M12 19v3',
  micOff: 'M2 2l20 20|M18.89 13.23A7 7 0 0 0 19 12v-2|M5 10v2a7 7 0 0 0 12 5|M15 9.34V5a3 3 0 0 0-5.68-1.33|M9 9v3a3 3 0 0 0 5.12 2.12|M12 19v3',
  message: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z',
  eyeOff: 'M9.88 9.88a3 3 0 1 0 4.24 4.24|M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68|M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61|M2 2l20 20',
  stop: 'M6 6h12v12H6Z',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z|M12 2v2|M12 20v2|M4.93 4.93l1.41 1.41|M17.66 17.66l1.41 1.41|M2 12h2|M20 12h2|M6.34 17.66l-1.41 1.41|M19.07 4.93l-1.41 1.41',
  monitor: 'M20 3H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2Z|M8 21h8|M12 17v4',
  terminal: 'M4 17l6-6-6-6|M12 19h8',
  power: 'M12 2v10|M18.4 6.6a9 9 0 1 1-12.77.04',
  logIn: 'M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4|M10 17l5-5-5-5|M15 12H3',
  check: 'M20 6 9 17l-5-5',
  chevronRight: 'M9 18l6-6-6-6',
  chevronLeft: 'M15 18l-6-6 6-6',
  x: 'M18 6 6 18|M6 6l12 12',
  external: 'M7 17 17 7|M7 7h10v10',
  plus: 'M12 5v14|M5 12h14',
  trash: 'M3 6h18|M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6|M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2',
  refresh: 'M21 12a9 9 0 0 1-15.5 6.2L3 16|M3 12a9 9 0 0 1 15.5-6.2L21 8|M21 3v5h-5|M3 21v-5h5',
  play: 'M6 4l14 8-14 8V4Z',
  save: 'M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z|M17 21v-8H7v8|M7 3v5h8',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z|M12 6v6l4 2',
  cpu: 'M9 2v2|M15 2v2|M9 20v2|M15 20v2|M2 9h2|M2 15h2|M20 9h2|M20 15h2|M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z|M9 9h6v6H9Z',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z|M22 6l-10 7L2 6',
  folder: 'M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z',
  folderPlus: 'M12 10v6|M9 13h6|M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z',
  brain: 'M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z|M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z|M12 5v13',
  radar: 'M19.07 4.93A10 10 0 0 0 6.99 3.34|M4 6h.01|M2.29 9.62A10 10 0 1 0 21.31 8.35|M16.24 7.76A6 6 0 1 0 8.23 16.67|M12 18h.01|M17.99 11.66A6 6 0 0 1 15.77 16.67|M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z|M13.41 10.59l5.66-5.66',
  waves: 'M2 12h2|M6 8v8|M10 4v16|M14 7v10|M18 10v4|M22 12h-2',
  sparkles: 'M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2Z|M5 3v4|M3 5h4|M19 17v4|M17 19h4',
  send: 'M5 12h14|M12 5l7 7-7 7',
  info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z|M12 16v-4|M12 8h.01',
  pin: 'M12 22s-8-4.5-8-11.8A8 8 0 0 1 12 2a8 8 0 0 1 8 8.2c0 7.3-8 11.8-8 11.8Z|M12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  hand: 'M18 11V6a2 2 0 0 0-4 0v1|M14 10V4a2 2 0 0 0-4 0v2|M10 10.5V6a2 2 0 0 0-4 0v8|M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15',
  layers: 'M12 2 2 7l10 5 10-5-10-5Z|M2 17l10 5 10-5|M2 12l10 5 10-5',
};

const NS = 'http://www.w3.org/2000/svg';

export function icon(name, className = '') {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  if (className) svg.setAttribute('class', className);
  for (const data of (PATHS[name] ?? PATHS.info).split('|')) {
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', data);
    svg.append(path);
  }
  return svg;
}

export function glyph(className = 'glyph') {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', className);
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = '<defs><radialGradient id="g" cx="40%" cy="35%" r="70%"><stop offset="0" stop-color="#e9fbff"/>'
    + '<stop offset=".35" stop-color="#3edcff"/><stop offset="1" stop-color="#1d3fd6"/></radialGradient></defs>'
    + '<circle cx="12" cy="12" r="10.5" fill="none" stroke="#3edcff" stroke-opacity=".45" stroke-width="1"/>'
    + '<circle cx="12" cy="12" r="6.5" fill="url(#g)"/>';
  return svg;
}
