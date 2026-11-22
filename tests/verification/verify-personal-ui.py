from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/verification'))
from ui_harness import Interface
from desktop.computer import USER
from desktop.files import FileTools
from desktop.personal import PersonalTools
from desktop.preferences import Preferences

SECTIONS = ('general', 'briefing', 'memory', 'monitoring', 'voice', 'models', 'email')


def main():
    with Interface() as ui:
        folder = ui.temp
        prefs = Preferences(folder / 'settings.json')
        prefs.save({'memoryVault': str(folder / 'Brain'), 'fileWriteRoots': [str(folder / 'Files')], 'monitorFolders': []})
        controller = ui.orb.controller
        controller.preferences = prefs
        controller.files = FileTools(prefs)
        controller.personal = PersonalTools(prefs, controller.files, ui.orb.events)

        ui.ui('open', {'window': 'settings', 'view': 'general', 'focus': False})
        settings = ui.page('settings')
        for section in SECTIONS:
            ui.ui('open', {'window': 'settings', 'view': section, 'focus': False})
            ui.wait(lambda: settings.evaluate("document.querySelector('.view-head .title')?.textContent || ''") != ''
                    and settings.evaluate("!document.querySelector('.content .spinner')"), 15, f'{section} settings did not load')
        ui.ui('open', {'window': 'settings', 'view': 'memory', 'focus': False})
        ui.wait(lambda: settings.evaluate("!!document.querySelector('input.mono')"), 10, 'Memory settings did not load')
        settings.fill('input.mono', str(folder / 'Vault'))
        settings.get_by_role('button', name='Save changes').click()
        ui.wait(lambda: prefs.read()['memoryVault'] == str(folder / 'Vault'), 5, 'Memory settings were not saved')
        ui.wait(lambda: (folder / 'Vault/Jarvis Memories').is_dir(), 5, 'Saving did not create the vault')
        ui.ui('open', {'window': 'settings', 'view': 'monitoring', 'focus': False})
        ui.wait(lambda: settings.evaluate("document.querySelectorAll('.toggle').length") == 2, 10, 'Monitoring settings did not load')
        settings.get_by_role('switch', name='Low battery warning').click()
        settings.get_by_role('button', name='Save changes').click()
        ui.wait(lambda: prefs.read()['monitorBattery'] is False, 5, 'Monitoring settings were not saved')

        target, foreground = ui.focus_target()
        controller.personal.monitor._alert('new_file', 'New test download is ready')
        ui.wait(lambda: ui.state('companion') == 'normal', 5, 'Alert did not appear')
        companion = ui.page('companion')
        ui.wait(lambda: 'New test download is ready' in companion.inner_text('body'), 5, 'Alert text missing')
        target.update()
        assert USER.GetForegroundWindow() == foreground, 'Alert stole focus'
        ui.ui('open', {'window': 'console', 'view': 'alerts', 'focus': False})
        console = ui.page('console')
        ui.wait(lambda: 'New test download is ready' in console.inner_text('.content'), 5, 'Alert history missing')
        ui.orb.toggle_jarvis()
        ui.wait(lambda: ui.state('console') == 'withdrawn' and ui.state('settings') == 'withdrawn', 5, 'Insert left windows visible')
        assert ui.state('companion') == 'withdrawn', 'Insert left the alert visible'
        target.destroy()
        print('PASS: all settings sections, saving memory and monitoring, vault creation, alert without focus stealing, alert history, Insert hides every window')


if __name__ == '__main__':
    main()
