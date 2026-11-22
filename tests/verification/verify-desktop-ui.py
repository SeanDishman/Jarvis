import base64
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/verification'))
from ui_harness import Interface
from desktop.computer import USER, encode_png


def main():
    with Interface() as ui:
        menu = ui.page('menu')
        for attempt in range(3):
            ui.ui('menu.open')
            ui.wait(lambda: ui.state('menu') == 'normal', 5, f'Right-click menu did not open (attempt {attempt + 1})')
            if attempt == 0:
                menu.get_by_role('menuitem', name='Settings').click()
                ui.wait(lambda: ui.state('settings') == 'normal', 5, 'Menu did not open Settings')
                ui.wait(lambda: ui.state('menu') == 'withdrawn', 5, 'Menu stayed open after choosing an item')
                settings = ui.page('settings')
                settings.evaluate("setTimeout(() => window.jarvis.ui('window', {action: 'close'}), 0)")
                ui.wait(lambda: ui.state('settings') is None, 5, 'Settings did not close')
            else:
                ui.ui('menu.close')
                ui.wait(lambda: ui.state('menu') == 'withdrawn', 5, 'Menu did not close')
        orb_page = ui.page('orb')
        for attempt in range(2):
            orb_page.mouse.click(66, 66, button='right')
            ui.wait(lambda: ui.state('menu') == 'normal', 5, f'Right-clicking the orb did not open the menu (attempt {attempt + 1})')
            menu.keyboard.press('Escape')
            ui.wait(lambda: ui.state('menu') == 'withdrawn', 5, 'Escape did not close the menu')
        orb_page.mouse.click(4, 4, button='right')
        ui.pause(.5)
        assert ui.state('menu') == 'withdrawn', 'Right-clicking the transparent corner opened the menu'
        orb_page.mouse.dblclick(66, 66)
        ui.wait(lambda: ui.state('panel') == 'normal', 5, 'Double-clicking the orb did not open the panel')
        panel = ui.page('panel')
        panel.fill('textarea', 'What time is it?')
        commands = []
        ui.orb.submit = commands.append
        panel.keyboard.press('Enter')
        ui.wait(lambda: commands == ['What time is it?'], 5, 'Enter in the panel did not send the command')
        print('PASS: the right-click menu reopens after opening and closing a window; orb clicks, double-click panel and typed commands work', flush=True)
        target, foreground = ui.focus_target('Jarvis UI test focus target')
        research = ui.orb.controller.research
        frame = {'image': base64.b64encode(encode_png(120, 60, bytes([255, 80, 30, 255]) * 120 * 60)).decode(),
                 'width': 120, 'height': 60, 'captured': time.monotonic()}
        job = {'id': 'ui-fixture', 'scope': 'computer', 'provider': 'all', 'question': 'Inspect fixture',
               'status': 'running', 'stage': 'looking', 'note': 'Looking at the test window', 'created': time.time(),
               'model': 'gpt-6-luna', 'keywords': [], 'queries': [], 'steps': ['Opened requested app'],
               'sources': [], 'summary': '', 'warnings': [], 'sourcesFound': 0, 'pagesRead': 0,
               'cancel': threading.Event(), '_frame': frame}
        research.jobs[job['id']] = job
        ui.emit('research', research.status(job['id']))
        ui.wait(lambda: ui.state('console') == 'normal', 15, 'Task must open its activity popup')
        console = ui.page('console')
        ui.wait(lambda: console.evaluate("document.querySelector('.preview img')?.naturalWidth") == 120, 10, 'Screenshot preview missing')
        assert 'Opened requested app' in console.inner_text('.detail'), 'Step log missing'
        target.update()
        assert USER.GetForegroundWindow() == foreground, 'Automatic popup stole focus'

        console.evaluate("window.jarvis.ui('window', {action: 'minimize'})")
        ui.wait(lambda: ui.state('console') == 'iconic', 5, 'Minimize did not hide the window')
        ui.emit('research', research.status(job['id']))
        ui.pause(.8)
        assert ui.state('console') == 'iconic', 'Progress update reopened a minimized window'
        ui.ui('open', {'window': 'console', 'view': 'activity', 'focus': True})
        ui.wait(lambda: ui.state('console') == 'normal', 5, 'Activity did not reopen from the menu')
        console.evaluate("setTimeout(() => window.jarvis.ui('window', {action: 'close'}), 0)")
        ui.wait(lambda: ui.state('console') is None, 5, 'Close did not close Activity')
        assert research.status(job['id'])['status'] == 'running', 'Closing popup cancelled task'
        ui.emit('research', research.status(job['id']))
        ui.pause(.8)
        assert ui.state('console') is None, 'Existing task reopened a closed popup'
        ui.ui('open', {'window': 'console', 'view': 'activity', 'focus': True})
        console = ui.page('console')
        console.get_by_role('button', name='Cancel task').click()
        ui.wait(lambda: research.status(job['id'])['status'] == 'cancelled', 5, 'Cancel task did not cancel')
        print('PASS: automatic popup, screenshot, step log, focus preservation, minimize, reopen, close, cancellation', flush=True)

        ui.emit('screen_view', {**frame, 'display': 'current'})
        ui.wait(lambda: ui.state('snapshot') == 'normal', 10, 'Snapshot window did not open')
        snapshot = ui.page('snapshot')
        ui.wait(lambda: snapshot.evaluate("document.querySelector('.frame img')?.naturalWidth") == 120, 10, 'Snapshot preview missing')
        ui.orb.controller.take_break({'delay_seconds': 60, 'wake_at': None})
        ui.wait(lambda: ui.state('orb') == 'withdrawn', 5, 'Break left the orb visible')
        for kind in ('snapshot', 'console', 'companion'):
            assert ui.state(kind) in ('withdrawn', None), f'Break left the {kind} window visible'
        ui.emit('speech_error', 'A late notification must not reopen Jarvis')
        ui.pause(.8)
        assert ui.state('orb') == 'withdrawn' and ui.state('panel') in ('withdrawn', None), 'A notification reopened Jarvis during a break'
        ui.orb.controller.resume()
        ui.wait(lambda: ui.state('orb') == 'normal', 5, 'Resume did not show the orb')
        ui.wait(lambda: ui.state('snapshot') == 'normal' and ui.state('console') == 'normal', 5, 'Resume did not restore open windows')
        print('PASS: snapshot preview, hidden orb/windows during break, suppressed notifications, explicit resume', flush=True)
        target.destroy()


if __name__ == '__main__':
    main()
