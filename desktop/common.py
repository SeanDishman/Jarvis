from __future__ import annotations

import ctypes
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.runtime/python'))
sys.path.insert(0, str(ROOT / '.runtime/features-python'))


def auth_token():
    return json.loads((ROOT / '.openclaw/openclaw.json').read_text(encoding='utf-8-sig'))['gateway']['auth']['token']


def local_request(port, route, payload=None, timeout=10):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{route}',
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + auth_token(), 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def secret_path(name):
    if name not in ('gmail', 'outlook', 'gmail-client', 'outlook-client'):
        raise ValueError('Unknown account')
    return ROOT / '.secrets' / ('desktop-' + name + '.dpapi')


def protect(data, decrypt=False, entropy=None):
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), buffer), Blob()
    entropy_buffer = (ctypes.c_ubyte * len(entropy)).from_buffer_copy(entropy) if entropy else None
    entropy_blob = Blob(len(entropy), entropy_buffer) if entropy else None
    entropy_pointer = ctypes.byref(entropy_blob) if entropy_blob is not None else None
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, entropy_pointer, None, None, 1, ctypes.byref(result))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), 'Jarvis email', entropy_pointer, None, None, 1, ctypes.byref(result))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(result.data, 0, result.size)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(result.data)


def save_secret(name, value):
    target = secret_path(name)
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_bytes(protect(json.dumps(value).encode('utf-8')))
    temporary.replace(target)


def read_secret(name):
    target = secret_path(name)
    return json.loads(protect(target.read_bytes(), decrypt=True)) if target.exists() else None
