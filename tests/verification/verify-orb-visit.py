"""Film a lock-in visit on the real interface.

Jarvis must glide from his corner into the middle of the browser window, grow there, show the lock-in card only
once he has landed, then glide home and shrink back. His window grows and shrinks around him, so no frame may
show him anywhere he was not. An ordinary question and answer must leave him in his corner.
"""
import ctypes
import ctypes.wintypes as wt
import json
import threading
import time

import numpy as np

from ui_harness import Interface, wait
from desktop.common import ROOT
from desktop.computer import USER, Rect

GDI = ctypes.WinDLL('gdi32')
GDI.CreateCompatibleDC.argtypes = [wt.HDC]
GDI.CreateCompatibleDC.restype = wt.HDC
GDI.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
GDI.CreateDIBSection.restype = wt.HBITMAP
GDI.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
GDI.SelectObject.restype = wt.HGDIOBJ
GDI.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
GDI.DeleteObject.argtypes = [wt.HGDIOBJ]
GDI.DeleteDC.argtypes = [wt.HDC]
PER_MONITOR = ctypes.c_void_p(-4)
ORB, VISIT_BOX = 132, 232
OUT = ROOT / '.cache' / 'orb-visit'


def physical(handle):
    rect = Rect()
    assert USER.GetWindowRect(handle, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


class Camera:
    """Copies one screen region into a reusable bitmap, fast enough to catch single frames."""

    def __init__(self, region):
        self.left, self.top, right, bottom = region
        self.width, self.height = right - self.left, bottom - self.top
        self.screen = USER.GetDC(None)
        self.memory = GDI.CreateCompatibleDC(self.screen)
        header = (ctypes.c_uint32 * 10)(40, self.width, (-self.height) & 0xffffffff, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        self.bitmap = GDI.CreateDIBSection(self.memory, header, 0, ctypes.byref(bits), None, 0)
        assert self.bitmap and bits.value, 'Could not make a capture bitmap'
        self.old = GDI.SelectObject(self.memory, self.bitmap)
        buffer = (ctypes.c_ubyte * (self.width * self.height * 4)).from_address(bits.value)
        self.pixels = np.ctypeslib.as_array(buffer).reshape(self.height, self.width, 4)
        self.lock = threading.Lock()

    def grab(self):
        with self.lock:
            assert GDI.BitBlt(self.memory, 0, 0, self.width, self.height, self.screen, self.left, self.top, 0x00CC0020 | 0x40000000)
            return self.pixels[:, :, 2::-1].copy()

    def close(self):
        GDI.SelectObject(self.memory, self.old)
        GDI.DeleteObject(self.bitmap)
        GDI.DeleteDC(self.memory)
        USER.ReleaseDC(None, self.screen)


def save(name, image):
    from PIL import Image
    path = OUT / f'{name}.png'
    Image.fromarray(image).save(path)
    return str(path)


def glow(frame, reference, threshold=60):
    """Pixels that changed and are the orb's cyan. The desktop behind may be animated, so plain change is not enough."""
    moved = np.abs(frame.astype(np.int16) - reference.astype(np.int16)).sum(axis=2) > threshold
    red, green, blue = (frame[..., index].astype(np.int16) for index in range(3))
    return moved & (blue > 150) & (green > 110) & (red < 140) & (blue - red > 70)


def blob(mask, keep_out=None, least=120):
    """Centre and extent of the orb's changed glow, ignoring a disk (the resting orbs in the corner)."""
    if keep_out is not None:
        (x, y), radius = keep_out
        rows, cols = np.ogrid[:mask.shape[0], :mask.shape[1]]
        mask = mask & ((cols - x) ** 2 + (rows - y) ** 2 > radius ** 2)
    count = int(mask.sum())
    if count < least:
        return None
    rows, cols = np.nonzero(mask)
    return {'x': float(cols.mean()), 'y': float(rows.mean()), 'pixels': count,
            'width': int(cols.max() - cols.min() + 1), 'height': int(rows.max() - rows.min() + 1)}


def drawn(page):
    """The orb as its own window renders it, on a clear background: its centre and how far out it is drawn."""
    import io
    from PIL import Image
    alpha = np.asarray(Image.open(io.BytesIO(page.screenshot(omit_background=True))).convert('RGBA'))[..., 3]
    rows, cols = np.nonzero(alpha > 60)
    weights = alpha[rows, cols].astype(float)
    centre = (float((cols * weights).sum() / weights.sum()), float((rows * weights).sum() / weights.sum()))
    return {'window': alpha.shape[::-1], 'centre': (round(centre[0], 1), round(centre[1], 1)),
            'radius': round(float(np.percentile(np.hypot(cols - centre[0], rows - centre[1]), 98)), 1)}


def film(camera, seconds, frames, handle):
    USER.SetThreadDpiAwarenessContext(PER_MONITOR)
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        before = time.perf_counter()
        image = camera.grab()
        frames.append(((before + time.perf_counter()) / 2, physical(handle), image))


def watch(handles, stop, trace):
    USER.SetThreadDpiAwarenessContext(PER_MONITOR)
    orb, companion = handles
    while not stop.is_set():
        trace.append((time.perf_counter(), physical(orb), bool(USER.IsWindowVisible(companion))))
        time.sleep(.001)


def positions(rows):
    kept = rows[:1]
    for rect in rows[1:]:
        if rect != kept[-1]:
            kept.append(rect)
    return kept


def dismiss_cards(ui):
    for card in ui.ui('store')['cards']:
        ui.ui('card.dismiss', {'id': card['id']})


def verify():
    USER.SetThreadDpiAwarenessContext(PER_MONITOR)
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    with Interface() as ui:
        windows = ui.windows()
        orb, companion = windows['orb']['handle'], windows['companion']['handle']
        home = physical(orb)
        ratio = (home[2] - home[0]) / ORB
        from desktop.windows import monitors
        screen = next(row for row in monitors() if row['rect'][0] <= home[0] < row['rect'][2] and row['rect'][1] <= home[1] < row['rect'][3])
        left, top, right, bottom = screen['work']
        browser = [left + 260, top + 90, right - 300, bottom - 140]
        target = ((browser[0] + browser[2]) / 2, (browser[1] + browser[3]) / 2)
        region = (max(left, round(target[0] - 260 * ratio)), max(top, round(target[1] - 420 * ratio)), right, bottom)
        camera = Camera(region)
        local = lambda point: (point[0] - region[0], point[1] - region[1])
        home_center = local(((home[0] + home[2]) / 2, (home[1] + home[3]) / 2))
        corner = (home_center, 76 * ratio)
        size = round(VISIT_BOX * ratio)
        stop, trace = threading.Event(), []
        watcher = threading.Thread(target=watch, args=((orb, companion), stop, trace), daemon=True)
        try:
            ui.pause(1.5)
            resting = drawn(ui.page('orb'))
            assert resting['window'] == (ORB, ORB), resting
            reference = camera.grab()
            save('0-before', reference)
            watcher.start()

            # An ordinary question and answer leaves him in his corner.
            ui.emit('question', 'What time is it?')
            ui.orb.controller.update('thinking', 'Thinking')
            ui.pause(.4)
            ui.emit('answer', 'It is noon, sir.', [])
            ui.orb.controller.update('speaking', 'Speaking')
            ui.pause(.6)
            ui.orb.controller.update('ready', 'Say Jarvis')
            ui.pause(1.0)
            assert {rect for _t, rect, _shown in trace} == {home}, 'The orb moved during an ordinary answer'
            report['ordinary_answer'] = {'orb_moved': False}

            frames = []
            row = {'visit': 1, 'site': 'x', 'name': 'X', 'text': 'X is closed, sir. Back to the work.', 'attempt': 1,
                   'rect': browser}
            filming = threading.Thread(target=film, args=(camera, 2.4, frames, orb), daemon=True)
            filming.start()
            time.sleep(.15)
            asked = time.perf_counter()
            ui.emit('lock_in', row)
            filming.join()

            grown = next((t for t, rect, _shown in trace if t > asked and rect[2] - rect[0] != home[2] - home[0]), None)
            assert grown is not None, 'The orb window never grew for the visit'
            first = next(rect for t, rect, _shown in trace if t >= grown)
            assert first[:2] == home[:2], f'The window must grow from its top-left corner, got {first} from {home}'
            flight = positions([rect for t, rect, _shown in trace if t >= grown])
            final = flight[-1]
            steps = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(flight, flight[1:])]
            assert final[2] - final[0] == size and final[3] - final[1] == size, final
            landed_center = ((final[0] + final[2]) / 2, (final[1] + final[3]) / 2)
            assert abs(landed_center[0] - target[0]) <= 2 and abs(landed_center[1] - target[1]) <= 2, (landed_center, target)
            assert len(flight) >= 20, f'The glide only took {len(flight)} window positions; it should move every frame'
            assert max(steps) <= 70 * ratio, f'The window jumped {max(steps)}px in one step'
            landed = next(t for t, rect, _shown in trace if t >= grown and rect == final)
            flight_ms = round((landed - grown) * 1000)
            assert 350 <= flight_ms <= 1100, f'The glide took {flight_ms}ms'
            report['flight'] = {'window_positions': len(flight), 'largest_step_px': max(steps), 'flight_ms': flight_ms,
                                'request_to_first_move_ms': round((grown - asked) * 1000),
                                'landed_center': landed_center, 'browser_center': target}

            early = [(t, image) for t, _rect, image in frames if grown <= t <= grown + .1]
            assert early, 'No frames were captured as the window grew'
            for t, image in early:
                stray = blob(glow(image, reference), corner)
                assert stray is None, f'Something appeared away from the corner {round((t - grown) * 1000)}ms in: {stray}'
            save('1-window-grown', early[-1][1])
            report['no_jump_when_window_grows'] = {'frames_checked': len(early)}

            target_local = local(target)
            path = []
            for t, _rect, image in frames:
                if grown + .12 <= t <= landed + .05:
                    spot = blob(glow(image, reference), corner)
                    if spot:
                        path.append((t, spot, image))
            assert len(path) >= 5, f'Only {len(path)} mid-flight frames showed the orb'
            distance = [float(np.hypot(spot['x'] - target_local[0], spot['y'] - target_local[1])) for _t, spot, _i in path]
            assert all(later <= earlier + 12 * ratio for earlier, later in zip(distance, distance[1:])), distance
            assert distance[-1] < 40 * ratio, distance
            save('2-mid-flight', path[len(path) // 2][2])
            report['visible_glide'] = {'frames_with_orb': len(path), 'distance_to_middle_px': [round(value) for value in distance]}

            hidden_until = [shown for t, _rect, shown in trace if asked <= t < landed - .01]
            assert hidden_until and not any(hidden_until), 'The lock-in card showed before he landed'
            appeared = next((t for t, _rect, shown in trace if t >= landed - .01 and shown), None)
            assert appeared is not None and appeared - landed < .5, 'The lock-in card did not appear once he landed'
            held = camera.grab()
            save('3-held-with-card', held)
            card = wait(lambda: ui.page('companion').evaluate("document.querySelector('.lock-in-card')?.innerText ?? ''"), 3, 'No lock-in card')
            assert 'X is closed, sir.' in card, card
            grown_orb = drawn(ui.page('orb'))
            growth = grown_orb['radius'] / resting['radius']
            assert grown_orb['window'] == (VISIT_BOX, VISIT_BOX), grown_orb
            assert all(abs(value - VISIT_BOX / 2) <= 3 for value in grown_orb['centre']), grown_orb
            assert 1.55 <= growth <= 1.9, f'He grew {growth:.2f}x; he should grow about 1.7x ({resting} -> {grown_orb})'
            card_rect = physical(companion)
            card_bottom = card_rect[3]
            assert abs(card_bottom - (target[1] - 104 * ratio)) <= 6 * ratio, (card_rect, target)
            assert abs((card_rect[0] + card_rect[2]) / 2 - target[0]) <= 4 * ratio, (card_rect, target)
            report['landed'] = {'resting_orb': resting, 'grown_orb': grown_orb, 'growth': round(growth, 2), 'card': card.splitlines(),
                                'card_window': card_rect, 'card_hidden_in_flight': True,
                                'card_shown_ms_after_landing': round((appeared - landed) * 1000)}

            # Home again: the card has timed out, so nothing but Jarvis may move, and he must land exactly home.
            dismiss_cards(ui)
            wait(lambda: not USER.IsWindowVisible(companion), 3, 'The card did not go away')
            ui.pause(.4)
            frames = []
            filming = threading.Thread(target=film, args=(camera, 1.6, frames, orb), daemon=True)
            mark = time.perf_counter()
            filming.start()
            ui.emit('lock_in_finished', 1)
            filming.join()
            wait(lambda: physical(orb) == home, 3, 'The orb did not come home to exactly where it started')
            shrunk = next((t for t, rect, _shown in trace if t > mark and rect == home), None)
            assert shrunk is not None
            homeward = positions([rect for t, rect, _shown in trace if mark <= t <= shrunk])
            assert len(homeward) >= 15, f'The way home only took {len(homeward)} window positions'
            late = [(t, image) for t, _rect, image in frames if shrunk + .03 <= t <= shrunk + .3]
            assert late, 'No frames were captured after landing'
            for t, image in late:
                stray = blob(glow(image, reference), corner)
                assert stray is None, f'Something was left behind {round((t - shrunk) * 1000)}ms after landing: {stray}'
            mid = [image for t, _rect, image in frames if mark + .2 <= t <= shrunk - .1]
            if mid:
                save('4-mid-return', mid[len(mid) // 2])
            save('5-home', late[-1][1])
            settled = drawn(ui.page('orb'))
            assert settled['window'] == (ORB, ORB) and abs(settled['radius'] - resting['radius']) <= 2, (settled, resting)
            report['returned'] = {'window_positions': len(homeward), 'return_ms': round((shrunk - mark) * 1000),
                                  'clean_frames_after_landing': len(late), 'home': home, 'orb_after': settled}

            # A card that is still up rides home with him instead of vanishing and popping up again.
            row = {**row, 'visit': 2, 'attempt': 2, 'text': 'That is twice now, sir. X stays closed.'}
            ui.emit('lock_in', row)
            wait(lambda: USER.IsWindowVisible(companion) and physical(orb)[2] - physical(orb)[0] == size, 4, 'Second visit did not land')
            ui.pause(.3)
            mark = time.perf_counter()
            ui.emit('lock_in_finished', 2)
            wait(lambda: physical(orb) == home, 3, 'The orb did not come home after the second visit')
            ui.pause(.2)
            shown = [visible for t, _rect, visible in trace if t >= mark]
            assert shown and all(shown), 'The card disappeared while he flew home'
            card_rect = physical(companion)
            assert abs(card_rect[3] - (home[1] + 8 * ratio)) <= 4 * ratio, (card_rect, home)
            report['card_rides_home'] = {'stayed_visible': True, 'card_window_at_home': card_rect}
        finally:
            stop.set()
            camera.close()
    print(json.dumps(report, indent=1, default=str), flush=True)


if __name__ == '__main__':
    verify()
