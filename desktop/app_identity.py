from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from difflib import SequenceMatcher
from functools import lru_cache
import re
import shlex
import winreg

GENERIC = frozenset({'app', 'apps', 'application', 'browser', 'internet', 'web', 'desktop', 'microsoft', 'windows',
                     'player', 'client', 'launcher', 'host', 'service', 'the', 'for', 'and', 'by', 'my', 'x64', 'x86',
                     'beta', 'stable', 'preview', 'edition'})
ROLES = {'browser': 'https', 'web browser': 'https', 'internet browser': 'https', 'internet': 'https', 'the internet': 'https',
         'email': 'mailto', 'e mail': 'mailto', 'mail': 'mailto', 'email app': 'mailto', 'mail app': 'mailto'}
_version = None


def words(text):
    return frozenset(re.findall(r'[a-z0-9+]+', str(text).casefold()))


def significant(text):
    return words(text) - GENERIC


def similarity(name, other):
    first, second = significant(name), significant(other)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _library():
    global _version
    if _version is None:
        library = ctypes.WinDLL('version')
        library.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
        library.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p]
        library.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.UINT)]
        _version = library
    return _version


@lru_cache(maxsize=1024)
def declared_names(path):
    if not path:
        return ()
    library = _library()
    size = library.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ()
    data = ctypes.create_string_buffer(size)
    if not library.GetFileVersionInfoW(path, 0, size, data):
        return ()
    pointer, length = ctypes.c_void_p(), wt.UINT()
    languages = []
    if library.VerQueryValueW(data, '\\VarFileInfo\\Translation', ctypes.byref(pointer), ctypes.byref(length)) and length.value >= 4:
        table = ctypes.cast(pointer, ctypes.POINTER(wt.WORD))
        languages = [(table[index], table[index + 1]) for index in range(0, length.value // 2, 2)]
    names = []
    for language, codepage in languages + [(0x0409, 0x04b0), (0x0409, 0x04e4)]:
        for field in ('FileDescription', 'ProductName'):
            key = f'\\StringFileInfo\\{language:04x}{codepage:04x}\\{field}'
            if library.VerQueryValueW(data, key, ctypes.byref(pointer), ctypes.byref(length)) and length.value > 1:
                value = ' '.join(ctypes.wstring_at(pointer, length.value - 1).split())
                if value and 'operating system' not in value.casefold() and value not in names:
                    names.append(value)
    return tuple(names)


def program_score(name, path):
    return max((similarity(name, declared) for declared in declared_names(path)), default=0.0)


def _registry(root, key, value=''):
    try:
        with winreg.OpenKey(root, key) as handle:
            return winreg.QueryValueEx(handle, value)[0]
    except OSError:
        return None


def command_program(command):
    if not command:
        return None
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    first = parts[0].strip('"') if parts else ''
    if first.casefold().endswith('.exe'):
        return first
    match = re.search(r'([a-z]:\\[^"]*?\.exe)', command, re.I)
    return match.group(1) if match else None


@lru_cache(maxsize=32)
def default_program(scheme):
    choice = _registry(winreg.HKEY_CURRENT_USER,
                       rf'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{scheme}\UserChoice', 'ProgId')
    if not choice:
        return None
    return command_program(_registry(winreg.HKEY_CLASSES_ROOT, rf'{choice}\shell\open\command'))


def role_app(apps, request):
    tokens = [word for word in re.findall(r'[a-z0-9+]+', str(request).casefold()) if word not in {'my', 'the', 'default', 'open', 'a'}]
    scheme = ROLES.get(' '.join(tokens))
    if not scheme:
        return None
    path = default_program(scheme)
    if not path:
        return None
    scored = sorted(((program_score(app['Name'], path), app) for app in apps), key=lambda row: row[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > .5 else None


def close_names(apps, request, cutoff=.85):
    query = ' '.join(re.findall(r'[a-z0-9+]+', request.casefold()))
    scored = sorted(((SequenceMatcher(None, query, ' '.join(re.findall(r'[a-z0-9+]+', app['Name'].casefold()))).ratio(), app)
                     for app in apps), key=lambda row: row[0], reverse=True)
    if not scored or scored[0][0] < cutoff:
        return []
    if len(scored) > 1 and scored[1][0] >= scored[0][0] - .05:
        return []
    return [scored[0][1]]
