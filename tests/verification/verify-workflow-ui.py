from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/verification'))
from ui_harness import Interface
from desktop.projects import Projects
from desktop.window_layouts import WindowLayouts
from desktop.computer import USER, window_info


def main():
    with tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='workflow-ui-') as folder:
        root = tk.Tk()
        root.title('Jarvis Layout Verification Fixture')
        root.geometry('620x500+100+100')
        root.update()
        hwnd = int(USER.GetAncestor(root.winfo_id(), 2))
        fixture = window_info(hwnd)
        assert fixture and fixture['pid'], 'Could not inspect the fixture window'
        layouts = WindowLayouts()
        context = {'ownerText':'Put "Jarvis Layout Verification Fixture" on the left'}
        result = {}
        with patch('desktop.window_layouts.require_desktop'), \
             patch('desktop.window_layouts.enumerate_windows', side_effect=lambda:[window_info(hwnd)]):
            listing = layouts.listing(context)
            assert len(listing['windows']) == 1
            def move():
                try:
                    listing = layouts.listing(context)
                    result.update(layouts.arrange(context, [{'id':listing['windows'][0]['id'], 'position':'left', 'monitor':'primary'}]))
                except Exception as exc:
                    result.update(error=repr(exc))
            thread = threading.Thread(target=move)
            thread.start()
            deadline = time.monotonic()+5
            while thread.is_alive() and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            thread.join(1)
            assert result.get('ok') and result['windows'][0]['verified'], result
            print('PASS: native window placement and real position readback', flush=True)
        root.destroy()
    verify_projects()


def verify_projects():
    with Interface() as ui:
        controller = ui.orb.controller
        controller.projects = Projects(ui.temp / 'projects')
        saved = controller.projects.save('Laptop research', decisions='Quiet fans', next_steps='Compare weight',
            job={'status':'completed','scope':'web','question':'Compare laptops','summary':'Fixture findings',
                 'sources':[{'title':'Fixture source','url':'https://example.com/'}]})
        commands = []
        ui.orb.submit = commands.append
        ui.ui('open', {'window': 'console', 'view': 'projects', 'focus': False})
        console = ui.page('console')
        ui.wait(lambda: 'Fixture findings' in console.inner_text('.content'), 10, 'Project findings were not shown')
        fields = console.locator('.detail textarea')
        fields.nth(1).fill('Compare weight and portability')
        console.get_by_role('button', name='Save decisions and next steps').click()
        ui.wait(lambda: 'portability' in controller.projects.read(saved['id'])['next_steps'], 5, 'Next steps were not saved')
        console.get_by_role('button', name='Resume project').click()
        ui.wait(lambda: commands and saved['id'] in commands[-1], 5, 'Resume did not send the project to Jarvis')
        assert ui.window('console')['bounds']['width'] >= 760
        print('PASS: saved-project console, detail editing, and resume action', flush=True)

if __name__ == '__main__':
    main()
