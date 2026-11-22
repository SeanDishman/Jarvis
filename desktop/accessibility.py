from __future__ import annotations

import json
import math
import subprocess
import sys
import time
import uuid

from desktop.common import ROOT

CONTROL_ACTIONS = ('invoke', 'click_control', 'set_value', 'toggle', 'select', 'expand', 'collapse', 'scroll_into_view')


def worker_request(payload, check=None):
    if check:
        check()
    process = subprocess.Popen([sys.executable, '-m', 'desktop.accessibility'], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 8
    data = json.dumps(payload)
    try:
        while True:
            if check:
                check()
            try:
                output, _ = process.communicate(data, timeout=.1)
                break
            except subprocess.TimeoutExpired:
                data = None
                if time.monotonic() >= deadline:
                    raise RuntimeError('The app did not answer UI Automation in time. Inspect it again before retrying an action.')
        if process.returncode:
            raise RuntimeError('UI Automation is unavailable for this app.')
        try:
            result = json.loads(output)
        except ValueError:
            raise RuntimeError('UI Automation returned an unreadable response.') from None
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'The control could not be used.'))
        return result
    finally:
        if process.poll() is None:
            import psutil
            try:
                children = psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            process.kill()
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.communicate(timeout=3)


class Accessibility:
    def __init__(self):
        self.controls = {}
        self.target = None
        self.captured = 0
        self.failures = {}
        # Text Jarvis typed in the background that the app has not reported yet. A browser page paused behind other
        # windows keeps reporting a field's old value until it is visible again.
        self.typed = {}

    def inspect(self, target, check=None):
        self.controls, self.target = {}, dict(target)
        try:
            result = worker_request({'action': 'inspect', 'target': target}, check)
        except RuntimeError as exc:
            if check:
                check()
            return {'available': False, 'controls': [], 'error': str(exc)}
        generation = uuid.uuid4().hex[:10]
        public = []
        budget = 16000
        truncated = result.get('truncated', False)
        ordered = sorted(result['controls'], key=lambda row: not bool(set(row['actions']) - {'scroll_into_view', 'click_control'}))
        for index, row in enumerate(ordered):
            row = {**row, 'actions': row['actions'][:]}
            key = (target['hwnd'], target['pid'], tuple(row['_runtime']))
            failed = self.failures.get(key)
            if failed and failed['state'] == self.control_state(row):
                row['actions'] = [action for action in row['actions'] if action not in failed['actions']]
                row['actionErrors'] = failed['actions']
            identifier = f'control:{generation}:{index}'
            entry = {'id': identifier, **{key: value for key, value in row.items() if not key.startswith('_')}}
            typed = self.typed.get(key)
            if typed is not None:
                if row.get('value') == typed:
                    del self.typed[key]
                else:
                    entry['typedByJarvis'] = typed
            size = len(json.dumps(entry)) + 2
            if size > budget:
                truncated = True
                break
            budget -= size
            self.controls[identifier] = row
            public.append(entry)
        self.captured = time.monotonic()
        return {'available': True, 'controls': public, 'truncated': truncated}

    @staticmethod
    def control_state(row):
        return {key: value for key, value in row.items() if key not in ('actions', 'actionErrors')}

    def perform(self, target, identifier, action, text='', check=None, button='left'):
        if check:
            check()
        row = self.controls.get(identifier)
        if row is None or self.target is None or any(target[key] != self.target[key] for key in ('hwnd', 'pid', 'process')):
            raise RuntimeError('Choose a control from the selected app\'s latest inspection.')
        if time.monotonic() - self.captured > 80:
            raise RuntimeError('The control inspection expired. Inspect the app again.')
        if action not in row['actions']:
            raise RuntimeError('That control does not expose this action.')
        if action == 'click_control' and button not in ('left', 'middle', 'right'):
            raise RuntimeError('Choose a left, middle or right control click.')
        if action == 'set_value' and (not isinstance(text, str) or len(text) > 4000 or any(ord(c) < 32 and c not in '\n\r\t' for c in text)):
            raise RuntimeError('A field value must contain at most 4000 ordinary characters.')
        self.controls = {}
        key = (target['hwnd'], target['pid'], tuple(row['_runtime']))
        payload = {'action': action, 'target': target, 'control': row, 'text': text, 'button': button}
        if action == 'set_value' and key in self.typed:
            payload['typed'] = self.typed[key]
        try:
            result = worker_request(payload, check)
        except RuntimeError as exc:
            previous = self.failures.get(key, {})
            self.failures[key] = {'state': self.control_state(row),
                'actions': {**previous.get('actions', {}), action: str(exc)}}
            raise
        if action == 'set_value':
            # Remembered until the field reports it, so it is neither typed twice nor only partly replaced later.
            if result.get('verified') is False:
                self.typed[key] = text
            else:
                self.typed.pop(key, None)
        return result


def supported(control, auto):
    patterns, actions, state = {}, [], {}
    for name in ('Invoke', 'Value', 'RangeValue', 'Toggle', 'SelectionItem', 'ExpandCollapse', 'ScrollItem'):
        try:
            pattern = control.GetPattern(getattr(auto.PatternId, name + 'Pattern'))
            if pattern:
                patterns[name] = pattern
        except Exception:
            continue
    if 'Invoke' in patterns:
        actions.append('invoke')
    for name in ('Value', 'RangeValue'):
        if name in patterns:
            pattern = patterns[name]
            try:
                state['value'] = str(pattern.Value)[:600]
                if name == 'RangeValue':
                    state.update(minimum=pattern.Minimum, maximum=pattern.Maximum)
                if not pattern.IsReadOnly and 'set_value' not in actions:
                    actions.append('set_value')
            except Exception:
                pass
    for name, action, prop in (('Toggle', 'toggle', 'ToggleState'), ('SelectionItem', 'select', 'IsSelected'),
                               ('ExpandCollapse', 'expand', 'ExpandCollapseState')):
        if name in patterns:
            actions.append(action)
            try:
                state[prop] = getattr(patterns[name], prop)
            except Exception:
                pass
    if 'ExpandCollapse' in patterns:
        actions.append('collapse')
    if 'ScrollItem' in patterns:
        actions.append('scroll_into_view')
    return actions, patterns, state


def describe(control, auto):
    if control.IsPassword:
        return None, {}
    bounds = control.BoundingRectangle
    if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
        return None, {}
    actions, patterns, state = supported(control, auto)
    offscreen, enabled = bool(control.IsOffscreen), bool(control.IsEnabled)
    if offscreen:
        actions = [action for action in actions if action == 'scroll_into_view']
        if not actions:
            return None, {}
    if not enabled:
        actions = []
    return {'_runtime': list(control.GetRuntimeId()), 'name': control.Name[:240],
            'role': control.ControlTypeName, 'automationId': control.AutomationId[:160],
            'rect': [bounds.left, bounds.top, bounds.right, bounds.bottom],
            'enabled': enabled, 'offscreen': offscreen, 'actions': actions, **state}, patterns


def validate_window(target):
    from desktop.computer import require_desktop, window_info, controllable
    require_desktop()
    current = window_info(target['hwnd'])
    if not controllable(current) or any(current[key] != target[key] for key in ('pid', 'process')):
        raise RuntimeError('The selected app closed or changed. Inspect it again.')


def click_control(target, control, row, button):
    import ctypes
    from desktop.computer import USER, PTR, Point, window_info
    buttons = {'left': (0x0201, 0x0202, 1), 'middle': (0x0207, 0x0208, 0x10), 'right': (0x0204, 0x0205, 2)}
    if button not in buttons:
        raise RuntimeError('Choose a left, middle or right control click.')
    current = window_info(target['hwnd'])
    if not current or current['rect'] != target['rect'] or USER.IsIconic(target['hwnd']):
        raise RuntimeError('The window moved or minimized. Inspect it again before clicking a control.')
    left, top, right, bottom = row['rect']
    x, y = (left + right) // 2, (top + bottom) // 2
    bounds = current['rect']
    if not left < right or not top < bottom or not (bounds[0] <= x < bounds[2] and bounds[1] <= y < bounds[3]):
        raise RuntimeError('The control has no usable location inside its window.')
    native = control.NativeWindowHandle
    hwnd = native if isinstance(native, int) and native else target['hwnd']
    if USER.GetAncestor(hwnd, 2) != target['hwnd']:
        raise RuntimeError('The control belongs to a different window. Inspect it again.')
    USER.ScreenToClient.argtypes = [ctypes.c_void_p, ctypes.POINTER(Point)]
    point = Point(x, y)
    if not USER.ScreenToClient(hwnd, ctypes.byref(point)):
        raise RuntimeError('Windows could not locate the control in its window.')
    if not (-32768 <= point.x <= 32767 and -32768 <= point.y <= 32767):
        raise RuntimeError('The control is outside the supported window coordinates.')
    location = (point.x & 0xffff) | ((point.y & 0xffff) << 16)
    down, up, flags = buttons[button]
    result = PTR()
    for message, keys in ((0x0200, 0), (down, flags), (up, 0)):
        if not USER.SendMessageTimeoutW(hwnd, message, keys, location, 0x22, 500, ctypes.byref(result)):
            USER.PostMessageW(hwnd, up, 0, location)
            raise RuntimeError('The app did not accept a window control click. Inspect the result before retrying.')


def background_control(target, control, row, action, text, pattern=None, typed=''):
    """invoke, toggle, select or set_value done with Jarvis's own pointer and typing. Chromium apps bring themselves
    to the front when UI Automation invokes or edits their content, which would send the owner's typing into them;
    window messages never take focus."""
    from desktop.computer import background_keys
    click_control(target, control, row, 'left')
    result = {'ok': True, 'status': 'sent_in_background', 'action': action, 'name': row['name'], 'method': 'own_pointer'}
    if action != 'set_value':
        return result
    native = control.NativeWindowHandle
    hwnd = native if isinstance(native, int) and native else target['hwnd']

    # Wherever the click left the caret, deleting the field's length both ways empties it. Only what the field holds is
    # deleted (in tag inputs an extra backspace removes the previous entry): what it reports, or what Jarvis typed
    # there that a paused page has not reported yet.
    length = min(max(len(str(pattern.Value or '')), len(typed or '')), 5000)
    if length:
        for name in ('backspace', 'delete'):
            for _ in range(length):
                background_keys(hwnd, {'action': 'key', 'key': name})
        # Let the app take every key press off its queue before the text follows: Chromium pulls queued text ahead
        # of a queued key press, out of order.
        time.sleep(.2 + .002 * length)
    if text:
        background_keys(hwnd, {'action': 'type', 'text': text})
    # A covered page can take a few seconds to report its value.
    deadline = time.monotonic() + 4
    while str(pattern.Value or '') != text:
        if time.monotonic() > deadline:
            return {**result, 'verified': False, 'reported': str(pattern.Value or '')[:200],
                    'note': 'The text was typed but the field has not reported it yet. Check its value in the fresh '
                            'control tree, using wait if it is not there yet, before typing again.'}
        time.sleep(.1)
    return {**result, 'verified': True, 'value': text[:600]}


def execute_provider(payload, provider):
    import uiautomation as auto
    target, action = payload['target'], payload['action']
    validate_window(target)
    expected = payload.get('control')
    rows, visited = [], 0
    deadline = time.monotonic() + 3
    if provider == 'msaa':
        from desktop.accessibility_msaa import walk_controls
        controls = walk_controls(target['hwnd'], auto, deadline)
    else:
        root = auto.ControlFromHandle(target['hwnd'])
        controls = auto.WalkControl(root, includeTop=True, maxDepth=24)
    ancestors = []
    for control, depth in controls:
        visited += 1
        if visited >= 600 or time.monotonic() > deadline or len(rows) >= 180:
            break
        ancestors = ancestors[:depth]
        if control is None:
            ancestors.append(None)
            continue
        try:
            if expected and list(control.GetRuntimeId()) != expected['_runtime']:
                continue
            row, patterns = describe(control, auto)
            if row and row['enabled'] and not row['offscreen'] and row['rect'][2] > row['rect'][0] and row['rect'][3] > row['rect'][1]:
                row['actions'].append('click_control')
        except Exception:
            if expected:
                raise RuntimeError('The control is no longer available. Inspect the app again.') from None
            ancestors.append(None)
            continue
        if row is None:
            ancestors.append(None)
            continue
        if action == 'inspect':
            row['_provider'] = provider
            row['ancestors'] = [entry for entry in ancestors if entry][-2:]
            ancestors.append({'name': row['name'][:120], 'role': row['role']} if row['name'] else None)
            if row['name'] or set(row['actions']) - {'scroll_into_view', 'click_control'}:
                rows.append(row)
            continue
        if not expected or any(row[key] != expected[key] for key in ('_runtime', 'name', 'role', 'automationId')):
            raise RuntimeError('The control changed since inspection. Inspect the app again.')
        if action not in row['actions']:
            raise RuntimeError('The control no longer allows that action.')
        validate_window(target)
        if action in ('invoke', 'toggle', 'select', 'set_value'):
            from desktop.computer import chromium_window
            ranged = action == 'set_value' and 'RangeValue' in patterns and not patterns['RangeValue'].IsReadOnly
            if not ranged and chromium_window(target['hwnd']):
                return background_control(target, control, row, action, payload.get('text', ''), patterns.get('Value'),
                                          payload.get('typed', ''))
        if action == 'click_control':
            click_control(target, control, row, payload.get('button', 'left'))
            succeeded = True
        elif action == 'set_value':
            value = payload.get('text', '')
            if 'RangeValue' in patterns and not patterns['RangeValue'].IsReadOnly:
                value = float(value)
                pattern = patterns['RangeValue']
                if not math.isfinite(value) or not pattern.Minimum <= value <= pattern.Maximum:
                    raise RuntimeError('The value is outside this control\'s range.')
            else:
                pattern = patterns['Value']
            succeeded = pattern.SetValue(value, waitTime=0)
        else:
            name, method = {'invoke': ('Invoke', 'Invoke'), 'toggle': ('Toggle', 'Toggle'),
                'select': ('SelectionItem', 'Select'), 'expand': ('ExpandCollapse', 'Expand'),
                'collapse': ('ExpandCollapse', 'Collapse'), 'scroll_into_view': ('ScrollItem', 'ScrollIntoView')}[action]
            succeeded = getattr(patterns[name], method)(waitTime=0)
        if not succeeded:
            raise RuntimeError('The app refused the control action. Inspect it again before choosing another action.')
        return {'ok': True, 'status': 'action_requested', 'action': action, 'name': row['name']}
    if action == 'inspect':
        return {'ok': True, 'controls': rows, 'truncated': visited >= 600 or len(rows) >= 180 or time.monotonic() > deadline}
    raise RuntimeError('The control is no longer in the app. Inspect it again.')


def execute(payload):
    if payload['action'] != 'inspect':
        return execute_provider(payload, payload.get('control', {}).get('_provider', 'uia'))
    result, error = None, None
    try:
        result = execute_provider(payload, 'uia')
        if any(set(row['actions']) - {'scroll_into_view', 'click_control'} for row in result['controls']):
            return result
    except Exception as exc:
        error = exc
    try:
        fallback = execute_provider(payload, 'msaa')
        if fallback['controls']:
            return fallback
    except Exception:
        if result is None:
            raise RuntimeError('The app did not expose readable accessibility controls.') from error
    if result is not None:
        return result
    raise RuntimeError('The app did not expose readable accessibility controls.') from error


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        import ctypes
        from desktop.computer import USER
        USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        result = execute(json.load(sys.stdin))
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)[:350]}
    print(json.dumps(result, ensure_ascii=True))
