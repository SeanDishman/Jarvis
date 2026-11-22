from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/verification'))
from ui_harness import Interface

ITEMS = ['Type a command', 'Stop current response', 'Mute microphone', 'Hide Jarvis', 'Activity', 'Settings', 'Quit Jarvis']
LABELS = "[...document.querySelectorAll('.menu-item .label')].map((item) => item.textContent)"


def open_menu(ui):
    ui.ui('menu.open')
    ui.wait(lambda: ui.state('menu') == 'normal', 5, 'The right-click menu did not open')


def close_window(ui, kind):
    ui.page(kind).evaluate("setTimeout(() => window.jarvis.ui('window', {action: 'close'}), 0)")
    ui.wait(lambda: ui.state(kind) is None, 5, f'The {kind} window did not close')


def main():
    with Interface() as ui:
        orb = ui.page('orb')
        menu = ui.page('menu')
        cancelled, toggled, quit_calls = [], [], []
        ui.orb.controller.cancel = lambda: cancelled.append(True)
        ui.orb.toggle_jarvis = lambda: toggled.append(True)
        ui.orb.quit = lambda: quit_calls.append(True)

        orb.mouse.move(66, 66)
        ui.pause(1.2)
        assert ui.state('companion') in ('withdrawn', None), 'Hovering the orb opened the Say Jarvis card'
        assert not ui.page('companion').evaluate("!!document.querySelector('.tooltip-card')"), 'The Say Jarvis card is still in the page'
        orb.mouse.move(2, 2)
        print('PASS: hovering the orb opens no Say Jarvis card', flush=True)

        open_menu(ui)
        labels = menu.evaluate(LABELS)
        assert labels == ITEMS, f'The menu lists {labels}'
        assert menu.evaluate("document.querySelectorAll('.menu-item .chev, [data-submenu=\"true\"]').length") == 0, 'The menu still has submenus'
        assert 'Say Jarvis' in menu.inner_text('.menu-status'), 'The menu lost its status line'
        print(f'PASS: the menu lists only {len(ITEMS)} items, with no submenus and the status line kept', flush=True)

        menu.keyboard.press('ArrowDown')
        assert menu.evaluate("document.querySelector('.menu-item.active .label')?.textContent") == ITEMS[0], 'ArrowDown did not reach the first item'
        menu.keyboard.press('ArrowUp')
        assert menu.evaluate("document.querySelector('.menu-item.active .label')?.textContent") == ITEMS[-1], 'ArrowUp did not wrap to the last item'
        menu.keyboard.press('Escape')
        ui.wait(lambda: ui.state('menu') == 'withdrawn', 5, 'Escape did not close the menu')
        print('PASS: arrow keys move through the items and Escape closes the menu', flush=True)

        open_menu(ui)
        menu.get_by_role('menuitem', name='Settings').click()
        ui.wait(lambda: ui.state('settings') == 'normal', 5, 'Settings did not open from the menu')
        ui.wait(lambda: ui.state('menu') == 'withdrawn', 5, 'The menu stayed open after choosing Settings')
        title = ui.page('settings').evaluate('document.title')
        assert title.endswith('General'), f'Settings opened on {title!r} instead of General'
        close_window(ui, 'settings')
        print('PASS: Settings opens the settings window directly', flush=True)

        open_menu(ui)
        menu.get_by_role('menuitem', name='Activity').click()
        ui.wait(lambda: ui.state('console') == 'normal', 5, 'Activity did not open the console')
        title = ui.page('console').evaluate('document.title')
        assert title.endswith('Activity'), f'The console opened on {title!r}'
        nav = ui.page('console').evaluate("[...document.querySelectorAll('.nav .nav-item .label')].map((item) => item.textContent)")
        for view in ('Saved projects', 'Reminders and timers', 'Proactive alerts', 'Browser controls'):
            assert view in nav, f'{view} is no longer one click away in the console: {nav}'
        close_window(ui, 'console')
        print('PASS: Activity opens the console, which still reaches projects, reminders, alerts and browser controls', flush=True)

        open_menu(ui)
        menu.get_by_role('menuitem', name='Type a command').click()
        ui.wait(lambda: ui.state('panel') == 'normal', 5, 'Type a command did not open the panel')
        close_window(ui, 'panel')

        open_menu(ui)
        menu.get_by_role('menuitem', name='Stop current response').click()
        ui.wait(lambda: cancelled, 5, 'Stop current response did not reach the assistant')
        open_menu(ui)
        menu.get_by_role('menuitem', name='Hide Jarvis').click()
        ui.wait(lambda: toggled, 5, 'Hide Jarvis did not reach the assistant')
        open_menu(ui)
        menu.get_by_role('menuitem', name='Quit Jarvis').click()
        ui.wait(lambda: quit_calls, 5, 'Quit Jarvis did not reach the assistant')
        print('PASS: Type a command, Stop current response, Hide Jarvis and Quit Jarvis reach the assistant', flush=True)

        open_menu(ui)
        toggle = menu.get_by_role('menuitemcheckbox', name='Mute microphone')
        assert toggle.get_attribute('aria-checked') == 'false'
        toggle.click()
        ui.wait(lambda: ui.orb.listener.muted, 5, 'Mute microphone did not mute')
        open_menu(ui)
        toggle = menu.get_by_role('menuitemcheckbox', name='Mute microphone')
        ui.wait(lambda: toggle.get_attribute('aria-checked') == 'true', 5, 'The menu does not show that the microphone is muted')
        toggle.click()
        ui.wait(lambda: not ui.orb.listener.muted, 5, 'Mute microphone did not unmute')
        print('PASS: Mute microphone toggles the microphone and the menu shows its state', flush=True)


if __name__ == '__main__':
    main()
