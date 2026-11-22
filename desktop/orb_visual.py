from __future__ import annotations

import base64
import math
import os
import struct
import threading
import time
import zlib

import numpy as np

TAU = math.tau
STATES = {
    'ready':     {'color': (0.22, 0.80, 1.00), 'deep': (0.17, 0.38, 1.00), 'energy': .80, 'spin': .32, 'flow': .50},
    'listening': {'color': (0.26, 1.00, 0.80), 'deep': (0.08, 0.62, 1.00), 'energy': 1.0, 'spin': .55, 'flow': .95},
    'thinking':  {'color': (0.66, 0.50, 1.00), 'deep': (0.96, 0.34, 0.84), 'energy': .95, 'spin': 2.6, 'flow': 1.8},
    'speaking':  {'color': (0.34, 0.80, 1.00), 'deep': (0.48, 0.38, 1.00), 'energy': 1.0, 'spin': .75, 'flow': 1.3},
    'muted':     {'color': (0.48, 0.54, 0.63), 'deep': (0.28, 0.32, 0.42), 'energy': .42, 'spin': .10, 'flow': .15},
    'starting':  {'color': (0.38, 0.58, 0.86), 'deep': (0.22, 0.30, 0.72), 'energy': .62, 'spin': 1.5, 'flow': .60},
    'error':     {'color': (1.00, 0.70, 0.28), 'deep': (1.00, 0.34, 0.20), 'energy': .82, 'spin': .25, 'flow': .40},
}
STATES['hidden'] = STATES['away'] = STATES['muted']
SATELLITE = np.array((1.00, 0.63, 0.78), np.float32)
SMOKE = np.array((.075, .04, .02), np.float32)
GLASS = np.array((.045, .02, .01), np.float32)


def _band(r, radius, width):
    return np.clip(width / 2 + .5 - np.abs(r - radius), 0, 1)


def _arc(theta, start, extent, radius):
    distance = np.abs((theta - start - extent / 2 + math.pi) % TAU - math.pi)
    return np.clip((extent / 2 - distance) * radius + .5, 0, 1)


class OrbRenderer:

    def __init__(self, size):
        self.size = size
        self.k = k = size / 132
        center = (size - 1) / 2
        y, x = np.mgrid[0:size, 0:size].astype(np.float32)
        x, y = (x - center).ravel(), (y - center).ravel()
        r = np.hypot(x, y)
        theta = np.arctan2(y, x)
        self.x, self.y = x, y
        self.radial = (r * 4 + .5).astype(np.intp)
        self.radii = np.arange(self.radial.max() + 1, dtype=np.float32) / 4
        self.slices = 2048
        self.angular = ((theta + math.pi) / TAU * self.slices + .5).astype(np.intp) % self.slices
        self.edge = np.clip((size / 2 - 1.5 - self.radii) / (6 * k), 0, 1)
        self.core = np.flatnonzero(r < 31.5 * k)
        self.core_x, self.core_y, self.core_r, self.core_theta = x[self.core], y[self.core], r[self.core], theta[self.core]
        self.zone = np.flatnonzero((r > 17 * k) & (r < 53 * k))
        self.zone_radial, self.zone_angular, self.zone_r = self.radial[self.zone], self.angular[self.zone], r[self.zone]
        self.zone_edge = self.edge[self.zone_radial]
        radii = self.radii
        edge = np.clip((48 * k - radii) / (7 * k), 0, 1)
        self.plate = edge * edge * (3 - 2 * edge) * .5
        self.plate_map = self.plate[self.radial]
        angles = self.angles = np.arange(self.slices, dtype=np.float32) / self.slices * TAU - math.pi
        self.gaps = np.ones_like(angles)
        for gap in range(4):
            self.gaps *= 1 - _arc(angles, gap * TAU / 4, .16, 30 * k)
        phase = (angles / TAU * 72) % 1.0
        self.ticks = np.clip((.17 - np.abs(phase - .5)) * (TAU * 36.5 * k / 72) + .5, 0, 1)
        self.window = .12 + .3 * np.square(.5 + .5 * np.cos(angles))
        self.arcs = _arc(angles, 0, 1.25, 42.5 * k) + _arc(angles, math.pi, .55, 42.5 * k) + _arc(angles, 2.25, .16, 42.5 * k)
        self.trail = np.power(1 - ((-angles) % TAU) / TAU, 6)
        self.tail = np.square(np.clip(1 - ((-angles) % TAU) / .9, 0, 1))
        self.flat = np.ones_like(angles)
        self.tick_band = _band(radii, 36.5 * k, 2.6 * k)
        self.arc_band = _band(radii, 42.5 * k, 1.7 * k)
        self.arc_glow = np.exp(-np.square((radii - 42.5 * k) / (3.2 * k)))
        self.orbit_band = _band(radii, 50 * k, 1.4 * k)
        self.color = np.array(STATES['starting']['color'], np.float32)
        self.deep = np.array(STATES['starting']['deep'], np.float32)
        self.energy, self.spin, self.flow = .62, 1.5, .6
        self.mix = {'listening': 0.0, 'speaking': 0.0, 'thinking': 0.0, 'muted': 0.0, 'busy': 0.0}
        self.turn = self.swirl = 0.0
        self.level = 0.0
        self.flash = 0.0
        self.ripples = []
        self.last_ripple = 0.0
        self.last = None

    def pulse(self):
        self.flash = 1.0
        self.ripples.append((time.monotonic(), 1.0))

    def frame(self, state, now, level=0.0, busy=False, hover=False):
        dt = .04 if self.last is None else min(.12, max(0.0, now - self.last))
        self.last = now
        mood = STATES.get(state, STATES['ready'])
        ease = 1 - math.exp(-dt * 5)
        self.color += (np.array(mood['color'], np.float32) - self.color) * ease
        self.deep += (np.array(mood['deep'], np.float32) - self.deep) * ease
        self.energy += (mood['energy'] + (.12 if hover else 0) - self.energy) * ease
        self.spin += (mood['spin'] - self.spin) * ease
        self.flow += (mood['flow'] - self.flow) * ease
        for key in self.mix:
            goal = float(busy) if key == 'busy' else float(state == key)
            self.mix[key] += (goal - self.mix[key]) * (1 - math.exp(-dt * 6))
        self.turn += self.spin * dt
        self.swirl += self.flow * dt
        self.level += (level - self.level) * (.6 if level > self.level else .15)
        self.flash *= math.exp(-dt * 3)
        if state == 'listening' and now - self.last_ripple > 1.1 - .75 * self.level:
            self.ripples.append((now, .35 + .65 * self.level))
            self.last_ripple = now
        self.ripples = [(born, strength) for born, strength in self.ripples if now - born < 1.5]
        return self.draw(now)

    def draw(self, now):
        k, mix, radii = self.k, self.mix, self.radii
        color, deep = self.color[::-1, None], self.deep[::-1, None]
        hot = color * .35 + .65
        voice = mix['listening'] + mix['speaking']
        breath = .5 + .5 * math.sin(now * (2.2 if voice > .5 else 1.1))
        energy = min(1.25, self.energy + self.flash * .35)
        radius = (24.5 + 1.1 * breath + 3.2 * self.level * voice + 1.5 * self.flash) * k

        outside = np.maximum(radii - radius, 0)
        halo = (np.exp(-np.square(outside / (6.5 * k))) * .5 + np.exp(-np.square(outside / (17 * k))) * .2) * energy * self.edge
        halo = np.take(np.minimum(halo, 1), self.radial)
        shade = self.plate_map * (1 - halo)
        canvas = np.empty((4, halo.size), np.float32)
        canvas[:3] = color * halo + SMOKE[:, None] * shade
        canvas[3] = halo + shade

        x, y, r = self.core_x, self.core_y, self.core_r
        depth = np.minimum(r / radius, 1)
        clouds = []
        for orbit, speed, spread, offset in ((.42, .9, .62, 0.0), (.38, -.7, .56, 2.1), (.3, 1.3, .42, 4.0)):
            angle = self.swirl * speed + offset
            dx, dy = x - math.cos(angle) * orbit * radius, y - math.sin(angle) * orbit * radius
            clouds.append(np.exp((dx * dx + dy * dy) * (-1 / (spread * radius) ** 2)))
        grain = .8 + .2 * np.sin(3 * (self.core_theta + depth * 2.6 - self.swirl) + depth * 4)
        heart = np.exp(-np.square(r / (radius * .3))) * (.5 + .25 * breath + .55 * self.level * voice + .45 * self.flash)
        cube = depth * depth * depth
        rim = cube * cube * cube * .95 + cube * .22
        spot = np.exp(-(np.square((x + radius * .34) / (radius * .36)) + np.square((y + radius * .44) / (radius * .2)))) * .3
        body = ((deep * .1 + GLASS[:, None]) * (1 - .3 * depth)
                + color * ((clouds[0] * .62 * grain + rim) * energy)
                + deep * ((clouds[1] * .7 * grain + rim * .35) * energy)
                + hot * ((clouds[2] * .3 + heart) * energy) + spot)
        if mix['muted'] > .01:
            along = np.clip((x - y) * .7071, -radius * .62, radius * .62)
            slash = np.clip(1.25 * k + .5 - np.hypot(x - along * .7071, y + along * .7071), 0, 1) * mix['muted']
            body = body * (1 - slash) + .8 * slash
        cover = np.clip(radius - r + .5, 0, 1) * .97
        under = self.gather(canvas, self.core)
        under[:3] = body * cover + under[:3] * (1 - cover)
        under[3] = cover + under[3] * (1 - cover)
        self.scatter(canvas, self.core, under)

        arc_radius = 42.5 * k
        arcs = self.spun(self.arcs, self.turn) * (1 - .55 * mix['speaking'])
        layers = [(_band(radii, radius + 5.5 * k, .9 * k), self.spun(self.gaps, self.turn * .5) * .4, 'line'),
                  (self.tick_band, self.spun(self.ticks, self.turn * 1.4 * TAU / 72) * self.spun(self.window, -self.turn * .45), 'line'),
                  (self.arc_band, arcs * .95, 'line'),
                  (self.arc_glow, arcs * .35, 'glow')]
        if self.ripples:
            rings = np.zeros_like(radii)
            halos = np.zeros_like(radii)
            for born, strength in self.ripples:
                age = (now - born) / 1.5
                distance = np.abs(radii - (radius + (3 + age * 30) * k))
                fade = np.square(1 - age) * strength
                rings += np.clip(.7 * k + .5 - distance, 0, 1) * fade * .7
                halos += np.exp(-np.square(distance / (3 * k))) * fade * .3
            layers += [(rings, None, 'line'), (halos, None, 'glow')]
        if mix['thinking'] > .01:
            band = np.clip((radii - radius - 2 * k) / (3 * k), 0, 1) * np.clip((arc_radius - 3 * k - radii) / (3 * k), 0, 1)
            layers.append((band, self.spun(self.trail, self.turn * 1.6) * .45 * mix['thinking'], 'glow'))
        satellite = now * 1.5
        if mix['busy'] > .01:
            layers.append((self.orbit_band, self.spun(self.tail, satellite) * .55 * mix['busy'], 'satellite'))
        light = {kind: np.zeros(self.zone.size, np.float32) for kind in ('line', 'glow', 'satellite')}
        for radial, angular, kind in layers:
            term = np.take(radial, self.zone_radial)
            if angular is not None:
                term *= np.take(angular, self.zone_angular)
            light[kind] += term
        line, glow = light['line'], light['glow']

        if mix['speaking'] > .01:
            amplitude = (1.0 + 5.5 * self.level) * k * mix['speaking']
            for shift, strength in ((0.0, 1.0), (2.4, .45)):
                wave = np.sin(3 * self.angles + now * 3.7 + shift) * .62 + np.sin(5 * self.angles - now * 2.6 + 1.3 + shift) * .38
                distance = np.abs(self.zone_r - np.take(radius + 8 * k + amplitude * wave, self.zone_angular))
                line += np.clip(.85 * k + .5 - distance, 0, 1) * (strength * mix['speaking'])
                glow += np.exp(-np.square(distance / (2.6 * k))) * (.4 * strength * mix['speaking'])

        glow *= self.zone_edge * energy
        line *= self.zone_edge * min(1.0, .45 + .6 * energy)
        lit = self.gather(canvas, self.zone)
        lit[:3] += color * glow + hot * line + SATELLITE[:, None] * light['satellite']
        lit[3] += glow + line * .9 + light['satellite']
        self.scatter(canvas, self.zone, lit)

        if mix['thinking'] > .01:
            for speed, orbit, offset in ((2.3, 31 * k, 0.0), (-1.7, 48 * k, 2.0)):
                self.spark(canvas, now * speed + offset, orbit, 1.6 * k, hot, mix['thinking'])
        if mix['busy'] > .01:
            self.spark(canvas, satellite, 50 * k, 2.3 * k, SATELLITE[:, None], mix['busy'])
        return self.pack(canvas)

    def spun(self, pattern, angle):
        return np.roll(pattern, int(round(angle / TAU * self.slices)) % self.slices)

    @staticmethod
    def gather(canvas, index):
        part = np.empty((4, index.size), np.float32)
        for channel in range(4):
            np.take(canvas[channel], index, out=part[channel])
        return part

    @staticmethod
    def scatter(canvas, index, part):
        for channel in range(4):
            canvas[channel][index] = part[channel]

    def spark(self, canvas, angle, orbit, dot, color, strength):
        center = (self.size - 1) / 2
        px, py = math.cos(angle) * orbit, math.sin(angle) * orbit
        reach = int(6 * self.k) + 2
        columns = np.arange(max(0, int(center + px) - reach), min(self.size, int(center + px) + reach + 2))
        rows = np.arange(max(0, int(center + py) - reach), min(self.size, int(center + py) + reach + 2))
        index = (rows[:, None] * self.size + columns[None, :]).ravel()
        d = np.hypot(self.x[index] - px, self.y[index] - py)
        light = (np.clip(dot + .5 - d, 0, 1) + np.exp(-np.square(d / (4.5 * self.k))) * .55) * strength
        light *= self.edge[self.radial[index]]
        patch = self.gather(canvas, index)
        patch[:3] += color * light
        patch[3] += light
        self.scatter(canvas, index, patch)

    def pack(self, canvas):
        cover = np.clip(canvas[3], 0, 1)
        pixels = np.empty((canvas.shape[1], 4), np.uint8)
        for channel in range(3):
            pixels[:, channel] = np.minimum(np.maximum(canvas[channel], 0), cover) * 255 + .5
        pixels[:, 3] = cover * 255 + .5
        return pixels.reshape(self.size, self.size, 4)


def icon_png(size=64):
    renderer = OrbRenderer(size * 132 // 92)
    renderer.color = np.array(STATES['ready']['color'], np.float32)
    renderer.deep = np.array(STATES['ready']['deep'], np.float32)
    renderer.energy = 1.0
    pixels = renderer.draw(1.0)
    trim = (pixels.shape[0] - size) // 2
    pixels = pixels[trim:trim + size, trim:trim + size]
    alpha = pixels[..., 3:4].astype(np.float32)
    rgb = pixels[..., 2::-1].astype(np.float32) * 255 / np.maximum(alpha, 1)
    rgba = np.concatenate([np.clip(rgb + .5, 0, 255).astype(np.uint8), pixels[..., 3:4]], axis=2)
    rows = b''.join(b'\0' + rgba[row].tobytes() for row in range(size))
    def chunk(name, data):
        return struct.pack('!I', len(data)) + name + data + struct.pack('!I', zlib.crc32(name + data) & 0xffffffff)
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))
    return base64.b64encode(png).decode('ascii')


def loudness(rms):
    if rms <= 0:
        return 0.0
    return max(0.0, min(1.0, (20 * math.log10(rms) + 50) / 38))


class VoiceLevels:

    def __init__(self):
        self.microphone = 0.0
        self.microphone_at = 0.0
        self.speech = 0.0
        self.speech_at = 0.0
        self.speaking = False
        self.watcher = None

    def tap(self, listener):
        original = listener.callback
        def callback(data, frames, time_info, status):
            try:
                samples = np.frombuffer(data, np.int16).astype(np.float32)
                if samples.size:
                    self.microphone = loudness(math.sqrt(float(np.dot(samples, samples)) / samples.size) / 32768)
                    self.microphone_at = time.monotonic()
            except Exception:
                pass
            return original(data, frames, time_info, status)
        listener.callback = callback

    def input_level(self, now):
        return self.microphone if now - self.microphone_at < .4 else 0.0

    def level(self, state, now):
        self.speaking = state == 'speaking'
        if state == 'listening':
            return self.input_level(now)
        if not self.speaking:
            return 0.0
        if self.watcher is None:
            self.watcher = threading.Thread(target=self.watch_speech, name='orb-speech-meter', daemon=True)
            self.watcher.start()
        if now - self.speech_at < .4:
            return self.speech
        return .3 + .28 * abs(math.sin(now * 8.3)) * (.6 + .4 * math.sin(now * 2.1))

    def watch_speech(self):
        try:
            import comtypes
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
            comtypes.CoInitialize()
        except Exception:
            return
        meter, next_search = None, 0.0
        while True:
            if not self.speaking:
                time.sleep(.1)
                continue
            if meter is None:
                if time.monotonic() < next_search:
                    time.sleep(.1)
                    continue
                next_search = time.monotonic() + 2
                try:
                    meter = next((session._ctl.QueryInterface(IAudioMeterInformation)
                                  for session in AudioUtilities.GetAllSessions() if session.ProcessId == os.getpid()), None)
                except Exception:
                    meter = None
                continue
            try:
                peak = float(meter.GetPeakValue())
                self.speech = min(1.0, peak ** .6 * 1.25)
                self.speech_at = time.monotonic()
            except Exception:
                meter = None
            time.sleep(.03)
