import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/verification'))
from ui_harness import Interface
from desktop.computer import USER
from PIL import ImageGrab


def main():
    output = ROOT / '.cache/task-progress-verification'
    output.mkdir(parents=True, exist_ok=True)
    with Interface({'showTaskPopups': False}) as ui:
        controller = ui.orb.controller
        target, foreground = ui.focus_target('Synthetic task-progress verification')
        work = ui.orb.screens[0]['work']
        orb = ui.handle('orb')
        USER.SetWindowPos(orb, -1, work[0] + 444, work[1] + 366, 0, 0, 0x0001 | 0x0010)
        ui.pause(.4)
        assert ui.state('companion') == 'withdrawn', 'Idle Jarvis should have no progress popup'
        companion = ui.page('companion')

        def card(selector='.progress-card'):
            return companion.evaluate(f"document.querySelector('{selector}')?.innerText || ''")

        def capture(name):
            box = ui.window('companion')['bounds']
            orb_box = ui.window('orb')['bounds']
            ImageGrab.grab(bbox=(box['x'] - 6, box['y'] - 6, box['x'] + box['width'] + 6, orb_box['y'] + orb_box['height'] + 6),
                           include_layered_windows=True, all_screens=True).save(output / name)

        first = controller.tasks.prepare('Check the current time in Nagasaki, Japan', 'web')
        controller.tasks.describe(first['id'], "I'm checking the current time in Nagasaki, Japan.")
        controller.tasks.update(controller.tasks.jobs[first['id']], status='running', stage='working', note='Checking the local time')
        controller.update('thinking', 'Working')
        ui.wait(lambda: ui.state('companion') == 'normal', 5, 'Progress did not appear')
        ui.wait(lambda: 'Nagasaki' in card(), 5, 'Progress does not describe the task')
        target.update()
        assert USER.GetForegroundWindow() == foreground, 'Progress stole keyboard focus'
        bounds, orb_bounds = ui.window('companion')['bounds'], ui.window('orb')['bounds']
        bottom = bounds['y'] + bounds['height']
        assert bottom <= orb_bounds['y'] + 8, 'Progress must sit above the orb'
        capture('single-task.png')
        position = "getComputedStyle(document.querySelector('.progress-track'), '::after').left"
        before = companion.evaluate(position)
        ui.pause(.3)
        assert companion.evaluate(position) != before, 'Activity bar is not moving'
        ui.emit('finished', False)
        ui.pause(.4)
        assert ui.state('companion') == 'normal', 'Finishing the spoken acknowledgement hid unfinished work'

        second = controller.tasks.prepare('Compare the notes from two long documents with a very long name ' + 'X' * 100, 'files')
        third = controller.tasks.prepare('Open Notepad', 'apps')
        ui.wait(lambda: '1 more task' in card(), 5, 'A third task was not summarized')
        height = companion.evaluate("document.querySelector('.progress-card').getBoundingClientRect().height")
        assert height < 230, f'Progress grew into a full activity window ({height}px)'
        capture('multiple-tasks.png')

        USER.SetWindowPos(orb, -1, work[0] + 284, work[1] + 346, 0, 0, 0x0001 | 0x0010)
        ui.wait(lambda: ui.window('companion')['bounds'] != bounds, 5, 'Progress did not follow the orb')
        for identifier, status in ((first['id'], 'completed'), (second['id'], 'cancelled'), (third['id'], 'failed')):
            controller.tasks.update(controller.tasks.jobs[identifier], status=status, finished=time.time())
        ui.wait(lambda: ui.state('companion') == 'withdrawn', 5, 'Finished work left the progress visible')

        pending = controller.tasks.prepare('Find a file', 'files')
        ui.wait(lambda: ui.state('companion') == 'normal', 5, 'Queued work was not shown')
        ui.orb.ui_hidden = True
        ui.wait(lambda: ui.state('companion') == 'withdrawn' and ui.state('orb') == 'withdrawn', 5, 'Hiding Jarvis left progress visible')
        controller.tasks.update(controller.tasks.jobs[pending['id']], status='completed', finished=time.time())
        ui.orb.ui_hidden = False
        ui.wait(lambda: ui.state('orb') == 'normal', 5, 'Showing Jarvis did not restore the orb')
        ui.pause(.6)
        assert ui.state('companion') == 'withdrawn', 'Showing Jarvis resurrected completed progress'

        controller.cancelled.clear()
        controller.request = {'id': 'foreground-fixture', 'ownerText': 'What time is it in Nagasaki?'}
        controller.update('thinking', 'Thinking')
        ui.wait(lambda: ui.state('companion') == 'normal', 5, 'Foreground questions need progress too')
        controller.update('speaking', 'Speaking')
        ui.wait(lambda: ui.state('companion') == 'withdrawn', 5, 'Foreground work ends when the answer is ready')
        controller.request = None
        target.destroy()
        print('PASS: task descriptions, moving bar, multiple tasks, focus, following the orb, completion, cancellation, hidden state, foreground questions')
        print('Synthetic screenshots:', output)


if __name__ == '__main__':
    main()
