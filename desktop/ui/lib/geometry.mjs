export const ORB_SIZE = 132;
export const COLUMN = 300;

export function clamp(value, low, high) {
  return high < low ? low : Math.min(Math.max(value, low), high);
}

export function rectFromEdges([left, top, right, bottom]) {
  return { x: left, y: top, width: right - left, height: bottom - top };
}

export function intersection(a, b) {
  const width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return width > 0 && height > 0 ? width * height : 0;
}

export function matchDisplay(screen, displays, toDip = (rect) => rect) {
  if (!screen || !displays.length) return null;
  const rect = toDip(rectFromEdges(screen.rect));
  let best = null;
  let bestArea = 0;
  for (const display of displays) {
    const area = intersection(rect, display.bounds);
    if (area > bestArea) {
      best = display;
      bestArea = area;
    }
  }
  return best;
}

export function screenChoices(payload, displays, toDip) {
  const screens = payload?.screens ?? [];
  return screens.map((screen, index) => {
    const display = matchDisplay(screen, displays, toDip);
    const number = /DISPLAY(\d+)$/i.exec(screen.device)?.[1] ?? String(index + 1);
    const name = screen.internal ? 'Laptop screen' : display?.label?.trim() || `Display ${number}`;
    return { device: screen.device, label: name, detail: screen.primary ? 'Main display' : `Display ${number}`,
             primary: Boolean(screen.primary), internal: Boolean(screen.internal),
             selected: payload.selected === screen.device, displayId: display?.id ?? null };
  });
}

export function orbHome(workArea, size = ORB_SIZE) {
  return { x: Math.round(workArea.x + workArea.width - size - 5), y: Math.round(workArea.y + workArea.height - size - 1),
           width: size, height: size };
}

export function orbVisit(target, work, size = ORB_SIZE) {
  const x = clamp(target.x + target.width / 2 - size / 2, work.x, work.x + work.width - size);
  const y = clamp(target.y + target.height / 2 - size / 2, work.y, work.y + work.height - size);
  return { x: Math.round(x), y: Math.round(y), width: size, height: size };
}

export function companionBounds(orb, work, size, { margin = 8, overlap = 8 } = {}) {
  const width = Math.min(size.width, work.width);
  const center = orb.x + orb.width / 2;
  const x = Math.round(clamp(center - width / 2, work.x + margin, work.x + work.width - width - margin));
  const above = orb.y + overlap - work.y;
  const below = work.y + work.height - (orb.y + orb.height - overlap);
  const anchor = above >= Math.min(size.height, 240) || above >= below ? 'above' : 'below';
  const height = Math.max(0, Math.min(size.height, anchor === 'above' ? above : below));
  const y = anchor === 'above' ? orb.y + overlap - height : orb.y + orb.height - overlap;
  return { x, y: Math.round(y), width, height: Math.round(height), anchor };
}

export function menuPosition(cursor, work, size, pad = 0) {
  let x = cursor.x - pad;
  let y = cursor.y - pad;
  if (x + size.width - pad > work.x + work.width) x = cursor.x - size.width + pad;
  if (y + size.height - pad > work.y + work.height) y = cursor.y - size.height + pad;
  x = clamp(x, work.x - pad, work.x + work.width - size.width + pad);
  y = clamp(y, work.y - pad, work.y + work.height - size.height + pad);
  return { x: Math.round(x), y: Math.round(y) };
}

export function besideOrb(orb, work, size, { margin = 12, gap = 12 } = {}) {
  const width = Math.min(size.width, work.width - margin * 2);
  const height = Math.min(size.height, work.height - margin * 2);
  const center = orb.x + orb.width / 2;
  const left = clamp(center - COLUMN / 2, work.x + 8, work.x + work.width - COLUMN - 8);
  const column = { left, right: left + COLUMN };
  const onRight = center > work.x + work.width / 2;
  let x = onRight ? column.left - gap - width : column.right + gap;
  if (x < work.x + margin || x + width > work.x + work.width - margin) {
    x = onRight ? orb.x - gap - width : orb.x + orb.width + gap;
  }
  x = clamp(x, work.x + margin, work.x + work.width - width - margin);
  const y = clamp(orb.y + orb.height - height - 8, work.y + margin, work.y + work.height - height - margin);
  return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) };
}

export function centered(work, size, margin = 24) {
  const width = Math.min(size.width, work.width - margin * 2);
  const height = Math.min(size.height, work.height - margin * 2);
  return { x: Math.round(work.x + (work.width - width) / 2), y: Math.round(work.y + (work.height - height) / 2),
           width: Math.round(width), height: Math.round(height) };
}

export function visibleEnough(bounds, workAreas, share = 0.35) {
  if (!bounds || !(bounds.width > 0 && bounds.height > 0)) return false;
  const area = bounds.width * bounds.height;
  const shown = workAreas.reduce((total, work) => total + intersection(bounds, work), 0);
  const titleBar = { x: bounds.x + 24, y: bounds.y, width: Math.max(1, bounds.width - 180), height: 32 };
  const reachable = workAreas.some((work) => intersection(titleBar, work) > 0);
  return reachable && shown >= area * share;
}

export function fitImage(image, limit) {
  const scale = Math.min(1, limit.width / Math.max(1, image.width), limit.height / Math.max(1, image.height));
  return { width: Math.max(1, Math.round(image.width * scale)), height: Math.max(1, Math.round(image.height * scale)) };
}
