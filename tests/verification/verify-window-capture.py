import base64
import ctypes
from pathlib import Path
import struct
import sys
import time
import tkinter as tk
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from desktop.computer import USER, capture_rect, capture_window, matches_screen_dpi, window_info
from desktop.windows import desktop_name


def pixels(frame):
    data = base64.b64decode(frame['image'])
    width, height = struct.unpack('!II', data[16:24])
    offset, body = 8, b''
    while offset < len(data):
        size, kind = struct.unpack('!I4s', data[offset:offset + 8])
        if kind == b'IDAT':
            body += data[offset + 8:offset + 8 + size]
        offset += size + 12
    raw, stride = zlib.decompress(body), width * 3 + 1
    return [raw[row * stride + 1 + column * 3:row * stride + 4 + column * 3]
            for row in range(0, height, 4) for column in range(0, width, 4)]


def share(rows, colour):
    return sum(1 for value in rows if all(abs(a - b) < 40 for a, b in zip(value, colour))) / len(rows)


if desktop_name().lower() != 'default':
    sys.exit('SKIP: needs the normal Windows desktop')
USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
root = tk.Tk()
root.overrideredirect(True)
root.geometry('260x180+40+40')
canvas = tk.Canvas(root, width=260, height=180, highlightthickness=0, bg='#ff0000')
canvas.pack()
for x in range(0, 260, 26):
    canvas.create_rectangle(x, 0, x + 13, 180, fill='#00ff00', outline='')
cover = tk.Toplevel(root)
cover.overrideredirect(True)
cover.attributes('-topmost', True)
cover.geometry('260x180+40+40')
tk.Frame(cover, bg='#0000ff', width=260, height=180).pack()
try:
    deadline = time.monotonic() + .8
    while time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    target = window_info(USER.GetAncestor(root.winfo_id(), 2))
    if not matches_screen_dpi(target['hwnd']):
        sys.exit('SKIP: display scaling makes this test window DPI-virtualized; Jarvis uses screen pixels for such apps')
    own = pixels(capture_window(target))
    visible = pixels(capture_rect(target['rect']))
    red, green, blue = b'\xff\x00\x00', b'\x00\xff\x00', b'\x00\x00\xff'
    print(f'capture_window: red {share(own, red):.0%} green {share(own, green):.0%} blue {share(own, blue):.0%}')
    print(f'screen pixels:  blue {share(visible, blue):.0%}')
    assert share(own, blue) < .02, 'The covering window leaked into the app capture'
    assert share(own, red) > .3 and share(own, green) > .3, 'The covered app was not rendered'
    assert share(visible, blue) > .9, 'Fixture problem: the cover window was not on top'
    print('PASS: covered app window captured from its own pixels; overlapping window excluded')
finally:
    root.destroy()
