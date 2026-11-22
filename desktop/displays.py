import re


def monitor_number(screen, index):
    match = re.search(r'DISPLAY(\d+)$', screen['device'], re.I)
    return int(match.group(1)) if match else index + 1


def display_inventory(screens):
    return [{'id': screen['device'], 'number': monitor_number(screen, index),
             'primary': screen.get('primary', False), 'laptop': screen.get('internal', False),
             'rect': screen['rect'][:], 'workArea': (screen.get('work') or screen['rect'])[:]}
            for index, screen in enumerate(screens)]


def containing_display(rect, screens):
    def area(screen):
        left, top, right, bottom = screen['rect']
        return max(0, min(rect[2], right) - max(rect[0], left)) * max(0, min(rect[3], bottom) - max(rect[1], top))
    return max(screens, key=area) if screens else None


def display_bounds(screens):
    if not screens:
        raise RuntimeError('Windows did not expose a screen to capture, sir.')
    return [min(s['rect'][0] for s in screens), min(s['rect'][1] for s in screens),
            max(s['rect'][2] for s in screens), max(s['rect'][3] for s in screens)]


def select_display(value, screens, current_rect=None):
    if not screens:
        raise RuntimeError('Windows did not expose a screen to capture, sir.')
    value = str(value).strip().casefold()
    if value == 'all':
        return None
    current = (containing_display(current_rect, screens) if current_rect else None)
    current = current or next((s for s in screens if s.get('primary')), screens[0])
    if value == 'current':
        return current
    direct = next((s for s in screens if s['device'].casefold() == value), None)
    if direct:
        return direct
    value = re.sub(r'^(?:monitor|display|screen)\s*', '', value)
    value = {'first': '1', 'second': '2', 'third': '3', 'fourth': '4'}.get(value, value)
    matches = []
    if value.isdigit():
        matches = [s for i, s in enumerate(screens) if monitor_number(s, i) == int(value)]
    elif value in ('primary', 'laptop'):
        matches = [s for s in screens if s.get('primary' if value == 'primary' else 'internal')]
    elif value in ('secondary', 'external'):
        matches = [s for s in screens if not s.get('primary' if value == 'secondary' else 'internal')]
    elif value in ('other', 'next'):
        matches = [s for s in screens if s != current]
    elif value in ('left', 'right', 'top', 'bottom'):
        axis = 0 if value in ('left', 'right') else 1
        centers = [(s['rect'][axis] + s['rect'][axis + 2]) / 2 for s in screens]
        edge = (min if value in ('left', 'top') else max)(centers)
        matches = [s for s, center in zip(screens, centers) if center == edge]
    if len(matches) == 1:
        return matches[0]
    available = ', '.join(str(row['number']) for row in display_inventory(screens))
    if len(matches) > 1:
        raise RuntimeError(f'More than one monitor matches {value}. Choose a monitor number: {available}.')
    raise RuntimeError(f'Monitor {value} is unavailable. Connected monitor numbers: {available}.')
