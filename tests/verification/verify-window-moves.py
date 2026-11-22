"""Putting one window where another was, on real windows, through the real PC-control loop.

A browser on an isolated profile stands in for the window the owner wants gone; a classic Win32 window stands in for
the one he wants in its place. The owner's own window keeps keyboard focus throughout. A scripted planner, standing
in for the model, minimizes the browser and moves the other window to the place the minimize result recorded:
  * round one: an ordinary-sized browser on the other monitor, the other window minimized;
  * round two: the browser maximized on the other monitor, the other window minimized while it was maximized.
Each round must leave the browser minimized and the other window exactly where the browser was, on top and visible,
with no injected input and neither window ever taking the foreground. Jarvis's pointer must reach the minimize
button, the title bar it grabs and the place it drops the window.

    python tests/verification/verify-window-moves.py [--browser PATH] [--model]

With --model the real model gets the request in words and must work the placement out by itself.
"""
import argparse
import ctypes
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
spec = importlib.util.spec_from_file_location('pointer_check', HERE.with_name('verify-agent-pointer.py'))
pointer_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pointer_check)
Watch = pointer_check.Watch
from ui_harness import ROOT, Interface, wait
from desktop import computer, window_moves
from desktop.computer import USER, Point, window_info


def owner_window(left, top):
    """The window the owner is working in. It is an ordinary window, not an always-on-top one."""
    import tkinter as tk
    root = tk.Tk()
    root.title('Owner window')
    root.geometry(f'380x220+{left}+{top}')
    tk.Label(root, text='The owner keeps working here', font=('Segoe UI', 12)).pack(expand=True)
    root.update()
    print(json.dumps({'hwnd': int(USER.GetAncestor(root.winfo_id(), 2))}), flush=True)
    root.mainloop()


def quiet_state(hwnd, state, rect=None):
    """Put a fixture window into a starting state without giving it focus."""
    window_moves.to_normal_state(hwnd)
    if rect:
        USER.SetWindowPos(hwnd, None, rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1], 0x0004 | 0x0010)
        wait(lambda: window_info(hwnd)['rect'][:2] == rect[:2], 3, f'Fixture window did not move to {rect}')
    if state in ('maximized', 'minimized_maximized'):
        USER.PostMessageW(hwnd, 0x0112, 0xF030, 0)
        wait(lambda: USER.IsZoomed(hwnd), 3, 'Fixture window did not maximize')
    if state in ('minimized', 'minimized_maximized'):
        USER.ShowWindowAsync(hwnd, 7)
        wait(lambda: USER.IsIconic(hwnd), 3, 'Fixture window did not minimize')
    time.sleep(.4)


def on_top_at(hwnd):
    left, top, right, bottom = window_info(hwnd)['rect']
    return int(USER.GetAncestor(USER.WindowFromPoint(Point((left + right) // 2, (top + bottom) // 2)), 2) or 0) == hwnd


def outcome(browser, other, was, screens):
    final = window_info(other)
    return {'browser_minimized': bool(USER.IsIconic(browser)), 'browser_was': was,
            'other_rect': final['rect'], 'other_maximized': bool(USER.IsZoomed(other)),
            'other_monitor': computer.window_display(final, screens)['device'], 'other_on_top': on_top_at(other)}


def check_outcome(result, label):
    was = result['browser_was']
    assert result['browser_minimized'], f'{label}: the browser was not minimized'
    assert result['other_monitor'] == was['monitor'], f'{label}: the window is on {result["other_monitor"]}, not {was["monitor"]}'
    if was['maximized']:
        assert result['other_maximized'], f'{label}: the window is not maximized where the browser was'
    else:
        assert not result['other_maximized'] and result['other_rect'] == was['rect'], f'{label}: {result["other_rect"]} is not {was["rect"]}'
    assert result['other_on_top'], f'{label}: the window is not showing on top in its new place'


def scripted_round(engine, label, browser, other, owner, screens, watches):
    recorded = {}

    def move(scene, _frame):
        was = scene['lastAction']['result']['was']
        recorded.update(was)
        left, top, right, bottom = was['rect']
        return {'action': 'move_window', 'target': f'window:{other}', 'left': left, 'top': top, 'right': right, 'bottom': bottom,
                'maximized': was['maximized']}

    plan = [lambda scene, frame: {'action': 'minimize_window', 'target': f'window:{browser}'}, move]
    results, _ = pointer_check.run_task(engine, 'Minimize the browser and put the other window where it was', plan, owner, watches)
    result = outcome(browser, other, recorded, screens)
    result['steps'] = [(row['action'], row['result'].get('state'), row['result'].get('verified')) for row in results]
    check_outcome(result, label)
    assert [step[2] for step in result['steps']] == [True, True], result['steps']
    return result


def model_round(browser, other, owner, screens, watches, pointer):
    spec = importlib.util.spec_from_file_location('interaction_check', HERE.with_name('verify-app-interaction.py'))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    was = window_moves.resting_place(window_info(browser), screens)
    real_windows = computer.visible_windows
    listed = lambda: [row for row in real_windows() if row['hwnd'] in (browser, other, owner)]
    apps = Mock(apps=[{'Name': 'Google Chrome', 'AppID': 'fixture-browser'}, {'Name': 'Jarvis pointer check', 'AppID': 'fixture-app'}])
    question = 'Minimize my Chrome and put the Jarvis pointer check window where the Chrome was.'
    computer.bring_to_front(owner)
    time.sleep(.3)
    with helper.model_service() as request, patch.object(computer, 'visible_windows', side_effect=listed), \
         patch.object(computer.ComputerControl, 'pointer', staticmethod(pointer)), \
         patch.object(computer, 'apply_action', side_effect=AssertionError('The model used foreground input')):
        watch = pointer_check.Watch()
        try:
            actions = helper.run(question, request, apps)
        finally:
            watches.append(watch.stop())
    result = outcome(browser, other, was, screens)
    result['actions'] = actions
    check_outcome(result, 'model')
    assert 'move_window' in actions and 'minimize_window' in actions, actions
    return result


def verify(browser_path, model):
    import psutil
    folder = Path(tempfile.mkdtemp(prefix='window-moves-', dir=ROOT / '.cache'))
    screens = computer.monitors()
    main = next(row for row in screens if row['primary'])
    side = next((row for row in screens if not row['primary']), main)
    home = [main['work'][0] + 140, main['work'][1] + 120, main['work'][0] + 760, main['work'][1] + 520]
    there = [side['work'][0] + 260, side['work'][1] + 160, side['work'][0] + 1160, side['work'][1] + 780]
    if side is main:
        there = [main['work'][2] - 1000, main['work'][1] + 60, main['work'][2] - 100, main['work'][1] + 680]
    processes, owners, report, moves, problems, watches = [], [], {'monitors': [main['device'], side['device']]}, [], [], []
    try:
        owner_process = subprocess.Popen([sys.executable, str(HERE), '--owner', str(main['work'][2] - 460), str(main['work'][3] - 320)],
                                         stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append(owner_process)
        owner = json.loads(owner_process.stdout.readline())['hwnd']
        state = folder / 'native.json'
        processes.append(subprocess.Popen([sys.executable, str(HERE.with_name('verify-agent-pointer.py')), '--native-app', str(state)],
                                          stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW))
        # Its first state file can take several seconds while Windows scans the new interpreter.
        other = wait(lambda: json.loads(state.read_text())['hwnd'] if state.exists() else None, 40, 'The stand-in app did not start')
        page = folder / 'page.html'
        page.write_text('<title>Lecture stand-in</title><h1>The window the owner wants out of the way</h1>', encoding='utf-8')
        profile = folder / 'profile'
        processes.append(subprocess.Popen([browser_path, '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check',
                                           '--disable-default-apps', '--window-size=900,620', f'--window-position={there[0]},{there[1]}',
                                           page.as_uri()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

        def find():
            nonlocal owners
            owners = [p for p in psutil.process_iter(['pid', 'cmdline']) if '--user-data-dir=' + str(profile) in (p.info['cmdline'] or [])]
            row = next((row for row in computer.visible_windows() if row['pid'] in {p.pid for p in owners}
                        and row['title'].startswith('Lecture stand-in')), None)
            return row and row['hwnd']
        browser = wait(find, 25, 'The isolated browser did not open')
        time.sleep(1)
        with Interface() as ui:
            pointer = pointer_check.overlay_pointer(ui, moves, problems)
            if model:
                quiet_state(browser, 'maximized', there)
                quiet_state(other, 'normal', home)
                report['model'] = model_round(browser, other, owner, screens, watches, pointer)
            else:
                engine = computer.ComputerControl(Mock(apps=[{'Name': 'Fixture', 'AppID': 'fixture'}]))
                engine.pointer = pointer
                quiet_state(browser, 'normal', there)
                quiet_state(other, 'minimized', home)
                report['ordinary'] = scripted_round(engine, 'ordinary', browser, other, owner, screens, watches)
                quiet_state(browser, 'maximized', there)
                quiet_state(other, 'minimized_maximized', home)
                report['maximized'] = scripted_round(engine, 'maximized', browser, other, owner, screens, watches)
        report['injected_input'] = {kind: sum(row['injected'][kind] for row in watches) for kind in ('mouse', 'keyboard')}
        names = {owner: 'owner', browser: 'browser', other: 'other'}
        report['foreground_sequences'] = [[names.get(hwnd) or row['names'].get(hwnd, hex(hwnd)) for hwnd in row['foregrounds']] for row in watches]
        # The first entry is whatever was in front when the round began; only a change during the round is Jarvis's doing.
        report['fixtures_took_focus'] = [name for name, hwnd in (('browser', browser), ('other', other))
                                         if any(pointer_check.Watch.taken_by(row, hwnd) for row in watches)]
        report['owner_presses_during_rounds'] = [len(row['presses']) for row in watches]
        # Rounds the owner sat out show what Jarvis alone does to keyboard focus.
        quiet = [row for row in watches if not row['presses']]
        report['quiet_rounds'] = len(quiet)
        report['fixture_spells_in_front_in_quiet_rounds'] = [Watch.held(row, hwnd) for row in quiet for hwnd in (browser, other)]
        report['owner_in_front_at_end_of_quiet_rounds'] = [row['changes'][-1][1] == owner for row in quiet]
        report['fixture_focus_after_owner_press'] = [name for name, hwnd in (('browser', browser), ('other', other))
                                                     if any(hwnd in row['foregrounds'][1:] for row in watches)
                                                     and name not in report['fixtures_took_focus']]
        report['owner_in_front_at_start'] = [bool(row['foregrounds']) and row['foregrounds'][0] == owner for row in watches]
        report['pointer'] = {'actions': [row.get('action', 'hide') for row in moves], 'problems': problems}
        print(json.dumps(report, indent=1))
        assert report['injected_input'] == {'mouse': 0, 'keyboard': 0}, 'Jarvis injected real input'
        if not model:
            assert report['quiet_rounds'], 'The owner was busy in every round, so focus could not be judged. Run it again.'
            assert all(report['owner_in_front_at_end_of_quiet_rounds']), 'Keyboard focus did not end with the owner'
            assert all(spell <= .6 for spells in report['fixture_spells_in_front_in_quiet_rounds'] for spell in spells),                 'A moved or minimized window kept keyboard focus'
            assert report['pointer']['actions'] == ['click', 'grab', 'drop', 'hide'] * 2, report['pointer']['actions']
        assert not problems, problems
        print('PASS: the browser was minimized and the other window put exactly where it was, on top, without taking focus; '
              "Jarvis's pointer pressed minimize, grabbed the title bar and dropped the window in place.")
    finally:
        for process in owners:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        for process in processes:
            process.terminate()
        psutil.wait_procs(owners, timeout=5)
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner', nargs=2, type=int)
    parser.add_argument('--browser', default=next((path for path in pointer_check.BROWSERS if Path(path).exists()), None))
    parser.add_argument('--model', action='store_true')
    args = parser.parse_args()
    USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if args.owner:
        owner_window(*args.owner)
    else:
        assert args.browser, 'Pass --browser with a Chromium browser'
        verify(args.browser, args.model)
