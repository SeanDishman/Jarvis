import math
from pathlib import Path
import sys
import threading
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/verification'))
from ui_harness import Interface
from desktop.orb_visual import loudness

SHOTS = ROOT / '.cache/mic-meter'
RATE = 16000
BLOCK = int(RATE * .08)
READ = """() => {
  const meter = document.querySelector('.meter');
  return { level: parseFloat(meter.style.getPropertyValue('--level') || '0'), state: meter.dataset.state,
           now: meter.getAttribute('aria-valuenow'), text: meter.getAttribute('aria-valuetext'),
           note: meter.closest('.setting').querySelector('.desc').textContent };
}"""


def snap(target, name):
    """Pictures are evidence for people; a window Windows has covered cannot paint, so never fail on one."""
    try:
        target.screenshot(path=str(SHOTS / name), timeout=8000)
    except Exception as error:
        print(f'NOTE: no picture of {name}: {type(error).__name__}', flush=True)


def tone(amplitude):
    samples = np.sin(2 * math.pi * 220 * np.arange(BLOCK) / RATE) * amplitude
    return samples.astype('<i2').tobytes()


def expected(amplitude):
    return loudness(amplitude / math.sqrt(2) / 32768) if amplitude else 0.0


class Microphone:
    """Pushes 80 ms blocks through the real listener callback, the way PortAudio does."""

    def __init__(self, listener):
        self.listener, self.amplitude, self.stopped = listener, 0, threading.Event()
        threading.Thread(target=self.run, name='fake-microphone', daemon=True).start()

    def run(self):
        while not self.stopped.wait(.08):
            self.listener.callback(tone(self.amplitude), BLOCK, None, None)


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with Interface() as ui:
        sent = []
        original = ui.orb.send
        ui.orb.send = lambda name, data=None: (sent.append(name), original(name, data))[1]
        microphone = Microphone(ui.orb.listener)
        errors = []
        try:
            ui.ui('open', {'window': 'settings', 'view': 'general', 'focus': False})
            settings = ui.page('settings')
            settings.on('pageerror', lambda error: errors.append(str(error)))
            settings.on('console', lambda message: message.type == 'error' and errors.append(message.text))
            orb_events = ui.page('orb')
            orb_events.evaluate("window.__meterEvents = 0; window.jarvis.on((name) => { if (name === 'mic_level') window.__meterEvents += 1; })")
            assert not sent.count('mic_level'), 'The level stream ran with no settings page asking for it'

            ui.ui('open', {'window': 'settings', 'view': 'voice', 'focus': False})
            ui.wait(lambda: settings.evaluate("!!document.querySelector('.meter')"), 15, 'The Input level bar did not appear')
            row = settings.locator('.setting', has=settings.locator('.meter'))
            assert row.locator('.name').inner_text() == 'Input level'
            assert settings.get_by_role('meter', name='Microphone input level').count() == 1, 'The bar is not exposed as a meter'
            ui.wait(lambda: ui.orb.meter_until > time.monotonic(), 5, 'The page never took the stream lease')
            ui.wait(lambda: 'mic_level' in sent, 5, 'No level reached the settings page')
            idle = settings.evaluate(READ)
            assert idle['state'] == 'live' and idle['level'] == 0 and idle['now'] == '0', idle
            assert 'check that Jarvis can hear you' in idle['note'], idle
            print('PASS: the Input level bar appears in Microphone and speech, is exposed as a meter and is flat in silence', flush=True)

            for label, amplitude in (('normal', 3000), ('quiet', 300), ('loud', 30000)):
                microphone.amplitude = amplitude
                want = expected(amplitude)
                ui.wait(lambda: abs(settings.evaluate(READ)['level'] - min(1, want)) < .03, 5,
                        f'The bar did not settle near {want:.2f} for {label} speech: {settings.evaluate(READ)}')
                got = settings.evaluate(READ)
                assert got['state'] == 'live' and got['now'] == str(round(min(1, want) * 20) * 5), got
                snap(settings, f'{label}.png')
                snap(row, f'{label}-row.png')
                print(f'PASS: {label} speech ({amplitude}) fills the bar to {got["level"]:.2f} (expected {min(1, want):.2f})', flush=True)

            microphone.amplitude = 0
            ui.wait(lambda: settings.evaluate(READ)['level'] == 0, 5, 'The bar did not fall back to flat after silence')
            print('PASS: the bar falls back to flat when the speaker stops', flush=True)

            microphone.amplitude = 30000
            ui.orb.listener.muted = True
            ui.wait(lambda: settings.evaluate(READ)['state'] == 'muted', 5, 'The bar ignored the mute')
            ui.pause(.5)
            muted = settings.evaluate(READ)
            assert muted['level'] == 0 and muted['text'] == 'Muted' and 'muted' in muted['note'], muted
            snap(settings, 'muted.png')
            ui.orb.listener.muted = False
            ui.wait(lambda: settings.evaluate(READ)['state'] == 'live' and settings.evaluate(READ)['level'] > .5, 5, 'The bar did not recover after unmuting')
            print('PASS: a muted microphone shows no level even while loud audio arrives, and recovers on unmute', flush=True)

            ui.orb.controller.microphone_ready = False
            ui.wait(lambda: settings.evaluate(READ)['state'] == 'unavailable', 5, 'The bar did not report an unavailable microphone')
            ui.wait(lambda: settings.evaluate(READ)['level'] == 0, 5, 'The bar did not fall flat for an unavailable microphone')
            unavailable = settings.evaluate(READ)
            assert unavailable['level'] == 0 and 'not available' in unavailable['note'], unavailable
            snap(settings, 'unavailable.png')
            ui.orb.controller.microphone_ready = True
            ui.wait(lambda: settings.evaluate(READ)['state'] == 'live', 5, 'The bar did not recover when the microphone came back')
            print('PASS: an unavailable microphone is reported and the bar recovers', flush=True)

            choice = settings.locator('select.select').first
            options = choice.locator('option').evaluate_all('items => items.map((item) => item.value)')
            if len(options) > 1:
                original_value = choice.input_value()
                other = next(value for value in options if value != original_value)
                choice.select_option(other)
                ui.wait(lambda: 'Save to switch' in settings.evaluate(READ)['note'], 5, 'Choosing another microphone did not explain what the bar shows')
                snap(settings, 'switching.png')
                choice.select_option(original_value)
                ui.wait(lambda: 'check that Jarvis can hear you' in settings.evaluate(READ)['note'], 5, 'Going back did not restore the note')
                print('PASS: choosing another microphone before saving says the bar still shows the one in use', flush=True)

            assert orb_events.evaluate('window.__meterEvents') == 0, 'The level stream leaked to the orb window'
            print('PASS: the stream goes to the settings window only', flush=True)

            ui.ui('open', {'window': 'settings', 'view': 'general', 'focus': False})
            ui.wait(lambda: ui.orb.meter_until == 0.0, 5, 'Leaving the page did not release the stream')
            quiet = sent.count('mic_level')
            ui.pause(.6)
            assert sent.count('mic_level') == quiet, 'The stream kept running after the page was left'
            ui.ui('open', {'window': 'settings', 'view': 'voice', 'focus': False})
            ui.wait(lambda: ui.orb.meter_until > time.monotonic() and sent.count('mic_level') > quiet, 5, 'Coming back did not resume the stream')
            print('PASS: leaving the page stops the stream and coming back resumes it', flush=True)

            settings.evaluate("setTimeout(() => window.jarvis.ui('window', {action: 'close'}), 0)")
            ui.wait(lambda: ui.state('settings') is None, 5, 'Settings did not close')
            ui.wait(lambda: time.monotonic() > ui.orb.meter_until, 8, 'The lease did not lapse after the window closed')
            quiet = sent.count('mic_level')
            ui.pause(.6)
            assert sent.count('mic_level') == quiet, 'The stream kept running after the window closed'
            print('PASS: closing the window lets the lease lapse and the stream stops by itself', flush=True)
            assert not errors, f'The settings page logged errors: {errors}'
            print('PASS: no console errors', flush=True)
        finally:
            microphone.stopped.set()


if __name__ == '__main__':
    main()
