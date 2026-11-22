from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from datetime import datetime, timezone
import json
import re

from desktop.windows import desktop_name


class ProcessEntry(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wt.DWORD),
                ('cntThreads', wt.DWORD), ('th32ParentProcessID', wt.DWORD),
                ('pcPriClassBase', wt.LONG), ('dwFlags', wt.DWORD), ('szExeFile', wt.WCHAR * 260)]


class MemoryStatus(ctypes.Structure):
    _fields_ = [('dwLength', wt.DWORD), ('dwMemoryLoad', wt.DWORD),
                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wt.DWORD), ('PageFaultCount', wt.DWORD)] + [
        (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


KERNEL = ctypes.WinDLL('kernel32', use_last_error=True)
USER = ctypes.WinDLL('user32', use_last_error=True)
WINDOW_CALLBACK = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
KERNEL.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
KERNEL.CreateToolhelp32Snapshot.restype = wt.HANDLE
for _function in (KERNEL.Process32FirstW, KERNEL.Process32NextW):
    _function.argtypes = [wt.HANDLE, ctypes.POINTER(ProcessEntry)]
    _function.restype = wt.BOOL
KERNEL.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
KERNEL.OpenProcess.restype = wt.HANDLE
KERNEL.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
KERNEL.QueryFullProcessImageNameW.restype = wt.BOOL
KERNEL.CloseHandle.argtypes = [wt.HANDLE]
KERNEL.CloseHandle.restype = wt.BOOL
KERNEL.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
KERNEL.GlobalMemoryStatusEx.restype = wt.BOOL
KERNEL.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wt.DWORD]
KERNEL.K32GetProcessMemoryInfo.restype = wt.BOOL
USER.EnumWindows.argtypes = [WINDOW_CALLBACK, wt.LPARAM]
USER.EnumWindows.restype = wt.BOOL
USER.IsWindowVisible.argtypes = [wt.HWND]
USER.IsWindowVisible.restype = wt.BOOL
USER.IsIconic.argtypes = [wt.HWND]
USER.IsIconic.restype = wt.BOOL
USER.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
USER.GetWindowThreadProcessId.restype = wt.DWORD


def process_path(pid):
    handle = KERNEL.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wt.DWORD(len(buffer))
        return buffer.value if KERNEL.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)) else None
    finally:
        KERNEL.CloseHandle(handle)


def system_memory():
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if (not KERNEL.GlobalMemoryStatusEx(ctypes.byref(status)) or not status.ullTotalPhys
            or status.ullAvailPhys > status.ullTotalPhys):
        return {'available': False, 'error': 'Windows could not report current RAM usage.'}
    total, available = status.ullTotalPhys, status.ullAvailPhys
    used = total - available
    return {'available': True, 'totalBytes': total, 'usedBytes': used, 'availableBytes': available,
            'totalGiB': round(total / 1024 ** 3, 2), 'usedGiB': round(used / 1024 ** 3, 2),
            'availableGiB': round(available / 1024 ** 3, 2), 'usedPercent': round(used * 100 / total, 1)}


def process_memory(pid):
    handle = KERNEL.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if KERNEL.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return {'workingSetBytes': counters.WorkingSetSize}
        return None
    finally:
        KERNEL.CloseHandle(handle)


def memory_totals(rows):
    values = [(row.get('memory') or {}).get('workingSetBytes') for row in rows]
    measured = [value for value in values if isinstance(value, int) and not isinstance(value, bool) and value >= 0]
    known_bytes = sum(measured) if measured or not rows else None
    return {'workingSetBytes': known_bytes,
            'workingSetMiB': round(known_bytes / 1024 ** 2, 1) if known_bytes is not None else None,
            'measuredProcesses': len(measured), 'unavailableProcesses': len(rows) - len(measured),
            'complete': len(rows) == len(measured)}


def enumerate_processes():
    handle = KERNEL.CreateToolhelp32Snapshot(2, 0)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise RuntimeError('Windows could not provide a process list. I cannot tell what is running right now.')
    rows = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not KERNEL.Process32FirstW(handle, ctypes.byref(entry)):
            raise RuntimeError('Windows could not read the process list. Please try again.')
        while True:
            rows.append({'pid': entry.th32ProcessID, 'name': entry.szExeFile,
                         'parentPid': entry.th32ParentProcessID, 'path': process_path(entry.th32ProcessID),
                         'memory': process_memory(entry.th32ProcessID)})
            if not KERNEL.Process32NextW(handle, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:
                    raise RuntimeError('Windows returned an incomplete process list. Please try again.')
                break
        return rows
    finally:
        KERNEL.CloseHandle(handle)


def window_inventory():
    if desktop_name().lower() != 'default':
        return {'available': False, 'reason': 'Jarvis is not running on the normal Windows desktop.', 'byPid': {}}
    by_pid = {}

    @WINDOW_CALLBACK
    def collect(hwnd, _):
        if USER.IsWindowVisible(hwnd):
            pid = wt.DWORD()
            if USER.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
                counts = by_pid.setdefault(pid.value, {'open': 0, 'minimized': 0})
                counts['open'] += 1
                counts['minimized'] += int(bool(USER.IsIconic(hwnd)))
        return True

    if not USER.EnumWindows(collect, 0):
        return {'available': False, 'reason': 'Windows could not enumerate desktop windows.', 'byPid': {}}
    return {'available': True, 'byPid': by_pid}


APP_NAMES = {'edge': 'msedge', 'microsoftedge': 'msedge', 'chrome': 'chrome',
             'googlechrome': 'chrome', 'firefox': 'firefox', 'mozillafirefox': 'firefox',
             'fileexplorer': 'explorer', 'windowsexplorer': 'explorer',
             'taskmanager': 'taskmgr', 'visualstudiocode': 'code', 'vscode': 'code'}


def normalize(value):
    return re.sub(r'[^a-z0-9]', '', value.lower().removesuffix('.exe'))


def matches_process(row, name):
    key, executable = normalize(name), normalize(row['name'])
    if key in APP_NAMES:
        return executable == APP_NAMES[key]
    return bool(key) and (str(row['pid']) == name.strip() or key == executable
                          or (not name.lower().endswith('.exe') and key in executable))


def summarize_processes(rows, windows, names=(), physical_memory=None):
    if not isinstance(names, (list, tuple)) or len(names) > 8 or any(
            not isinstance(name, str) or not name.strip() or len(name) > 120 for name in names):
        raise RuntimeError('I could not interpret which running apps to check. Please try again.')
    selected = [row for row in rows if not names or any(matches_process(row, name) for name in names)]
    groups = {}
    group_rows = {}
    for row in selected:
        group_rows.setdefault(row['name'].lower(), []).append(row)
        group = groups.setdefault(row['name'].lower(), {
            'name': row['name'], 'processCount': 0, 'pids': [], 'paths': [], 'pathsUnavailable': 0,
            'openWindows': 0 if windows['available'] else None,
            'minimizedWindows': 0 if windows['available'] else None})
        group['processCount'] += 1
        if len(group['pids']) < 8:
            group['pids'].append(row['pid'])
        if row.get('path'):
            if row['path'] not in group['paths'] and len(group['paths']) < 2:
                group['paths'].append(row['path'][:500])
        else:
            group['pathsUnavailable'] += 1
        if windows['available']:
            counts = windows['byPid'].get(row['pid'], {})
            group['openWindows'] += counts.get('open', 0)
            group['minimizedWindows'] += counts.get('minimized', 0)
    for key, group in groups.items():
        group['memory'] = memory_totals(group_rows[key])
    ordered = sorted(groups.values(), key=lambda group: (
        -(group['memory']['workingSetBytes'] if group['memory']['workingSetBytes'] is not None else -1),
        -group['processCount'], group['name'].lower()))
    snapshot = {
        'capturedAt': datetime.now(timezone.utc).isoformat(), 'source': 'Windows Tool Help snapshot and memory usage counters',
        'processInventoryAvailable': True,
        'systemMemory': physical_memory if physical_memory is not None else {'available': False, 'error': 'RAM was not sampled.'},
        'processMemoryCoverage': {'measuredProcesses': memory_totals(rows)['measuredProcesses'],
                                  'unavailableProcesses': memory_totals(rows)['unavailableProcesses']},
        'memoryMetric': 'Working set: current resident RAM, summed across each executable group. Shared pages may be counted '
                        'more than once. These totals are not exclusive memory and do not add up to system RAM usage. '
                        'Incomplete groups report only measured processes, a lower bound. Groups are sorted by measured RAM, largest first.',
        'scope': 'System processes; window counts cover only the current Windows desktop.',
        'windowsAvailable': windows['available'], 'windowsError': windows.get('reason'),
        'totalProcesses': len(rows), 'matchingProcesses': len(selected), 'matchingGroups': len(groups),
        'queries': [{'name': name, 'processCount': sum(matches_process(row, name) for row in rows),
                     'memory': memory_totals([row for row in rows if matches_process(row, name)])} for name in names],
        'groups': [], 'omittedGroups': len(groups),
        'limitations': 'A point-in-time inventory, not a malware or rootkit scan. No-window processes are often normal. '
                      'Paths and PIDs are samples; missing paths may mean protected or exited processes. '
                      'Open windows include minimized or covered windows; no window titles or screen contents were read.',
    }
    for group in ordered:
        snapshot['groups'].append(group)
        if len(snapshot['groups']) > 120 or len(json.dumps(snapshot, ensure_ascii=False)) > 35000:
            snapshot['groups'].pop()
            break
    snapshot['omittedGroups'] = len(groups) - len(snapshot['groups'])
    return snapshot


def inspect_processes(names=()):
    physical_memory = system_memory()
    try:
        rows = enumerate_processes()
    except RuntimeError as error:
        if not physical_memory['available']:
            raise
        return {'capturedAt': datetime.now(timezone.utc).isoformat(), 'systemMemory': physical_memory,
                'processInventoryAvailable': False, 'processError': str(error), 'groups': [], 'queries': []}
    return summarize_processes(rows, window_inventory(), names, physical_memory)
