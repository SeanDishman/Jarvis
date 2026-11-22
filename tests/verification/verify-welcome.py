"""Film a welcome-back greeting on the real interface.

After hours away, Jarvis must leave his corner on whichever monitor he lives on, glide to the middle of the main
monitor, grow there to about 2.6 times his resting size with the welcome card beside him, and glide home to
exactly where he started once the greeting is over.
"""
import ctypes
import importlib.util
import json
from pathlib import Path
import threading
import time

from ui_harness import Interface, wait
from desktop.computer import USER
from desktop.welcome import WELCOME_SCALE
from desktop.windows import monitors

spec = importlib.util.spec_from_file_location('orb_visit', Path(__file__).with_name('verify-orb-visit.py'))
visit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(visit)
ORB, OUT, physical, drawn, save, watch = visit.ORB, visit.OUT, visit.physical, visit.drawn, visit.save, visit.watch
PER_MONITOR = ctypes.c_void_p(-4)


def verify():
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    USER.SetThreadDpiAwarenessContext(PER_MONITOR)
    OUT.mkdir(parents=True, exist_ok=True)
    screens = monitors()
    main = next(row for row in screens if row['primary'])
    others = [row for row in screens if not row['primary']]
    # Live on another monitor, as the owner's orb does, so the greeting has to cross to the main one.
    settings = {'monitor': others[0]['device']} if others else {}
    report = {'main_monitor': main['device'], 'orb_monitor': settings.get('monitor', main['device'])}
    with Interface(settings) as ui:
        windows = ui.windows()
        orb, companion = windows['orb']['handle'], windows['companion']['handle']
        home = physical(orb)
        ratio = (home[2] - home[0]) / ORB
        left, top, right, bottom = main['work']
        middle = ((left + right) / 2, (top + bottom) / 2)
        stop, trace = threading.Event(), []
        watcher = threading.Thread(target=watch, args=((orb, companion), stop, trace), daemon=True)
        try:
            ui.pause(1.5)
            resting = drawn(ui.page('orb'))
            watcher.start()
            asked = time.perf_counter()
            text = 'Good morning, sir. As Seneca wrote, “While we are postponing, life speeds by.” Let us get to work.'
            ui.emit('welcome', {'visit': 1, 'text': text, 'title': 'Good morning', 'rect': main['work']})

            def landed():
                rect = physical(orb)
                center = ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
                return rect if rect[2] - rect[0] > 300 * ratio and abs(center[0] - middle[0]) <= 2 and abs(center[1] - middle[1]) <= 2 else None
            final = wait(landed, 4, 'Jarvis did not land in the middle of the main monitor')
            arrived = time.perf_counter()
            ui.pause(.8)
            flight = visit.positions([rect for t, rect, _shown in trace if asked <= t <= arrived])
            steps = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(flight, flight[1:])]
            assert len(flight) >= 20, f'The glide only took {len(flight)} window positions'
            assert max(steps) <= 90 * ratio, f'The window jumped {max(steps)}px in one step'
            grown = drawn(ui.page('orb'))
            growth = grown['radius'] / resting['radius']
            assert 2.3 <= growth <= 2.9, f'He grew {growth:.2f}x; a welcome should make him about {WELCOME_SCALE}x ({resting} -> {grown})'
            assert all(abs(value - grown['window'][0] / 2) <= 3 for value in grown['centre']), grown
            card = wait(lambda: ui.page('companion').evaluate("document.querySelector('.lock-in-card')?.innerText ?? ''"), 3, 'No welcome card')
            # The eyebrow is styled in capitals, so compare without case.
            assert 'jarvis · good morning' in card.casefold() and 'Let us get to work.' in card, card
            assert USER.IsWindowVisible(companion), 'The welcome card is not showing'
            card_rect = physical(companion)
            assert main['rect'][0] <= card_rect[0] and card_rect[2] <= main['rect'][2], ('The card left the main monitor', card_rect)
            image = visit.Camera(main['work'])
            try:
                save('welcome-held', image.grab())
            finally:
                image.close()
            report['landed'] = {'window': final, 'main_monitor_middle': middle, 'window_positions': len(flight),
                                'largest_step_px': max(steps), 'flight_ms': round((arrived - asked) * 1000),
                                'resting_orb': resting, 'grown_orb': grown, 'growth': round(growth, 2), 'card': card.splitlines()}

            visit.dismiss_cards(ui)
            mark = time.perf_counter()
            ui.emit('welcome_finished', 1)
            wait(lambda: physical(orb) == home, 4, 'Jarvis did not come home to exactly where he started')
            settled = drawn(ui.page('orb'))
            assert settled['window'] == (ORB, ORB) and abs(settled['radius'] - resting['radius']) <= 2, (settled, resting)
            report['returned'] = {'home': home, 'return_ms': round((time.perf_counter() - mark) * 1000), 'orb_after': settled}
        finally:
            stop.set()
    print(json.dumps(report, indent=1, default=str), flush=True)


if __name__ == '__main__':
    verify()
