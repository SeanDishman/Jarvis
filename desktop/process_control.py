import ctypes
from ctypes import wintypes as wt
import os
import time
import uuid

from desktop.processes import KERNEL, enumerate_processes, matches_process, normalize
from desktop.routing import require_capability


class SessionProcess(ctypes.Structure):
    _fields_ = [('session', wt.DWORD), ('pid', wt.DWORD), ('name', wt.LPWSTR), ('sid', wt.LPVOID)]


WTS = ctypes.WinDLL('wtsapi32', use_last_error=True)
WTS.WTSEnumerateProcessesW.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD,
    ctypes.POINTER(ctypes.POINTER(SessionProcess)), ctypes.POINTER(wt.DWORD)]
WTS.WTSEnumerateProcessesW.restype = wt.BOOL
WTS.WTSFreeMemory.argtypes = [wt.LPVOID]
WTS.WTSFreeMemory.restype = None
KERNEL.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
KERNEL.GetProcessTimes.restype = wt.BOOL
KERNEL.IsProcessCritical.argtypes = [wt.HANDLE, ctypes.POINTER(wt.BOOL)]
KERNEL.IsProcessCritical.restype = wt.BOOL
KERNEL.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
KERNEL.TerminateProcess.restype = wt.BOOL
KERNEL.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
KERNEL.WaitForSingleObject.restype = wt.DWORD
KERNEL.ProcessIdToSessionId.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD)]
KERNEL.ProcessIdToSessionId.restype = wt.BOOL


def windows_error(operation):
    code = ctypes.get_last_error()
    return OSError(code, f'Windows could not {operation}: {ctypes.FormatError(code).strip()}')


def identity(handle):
    times = [wt.FILETIME() for _ in range(4)]
    if not KERNEL.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise windows_error('identify the process')
    path = ctypes.create_unicode_buffer(32768)
    length = wt.DWORD(len(path))
    if not KERNEL.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
        raise windows_error('identify the executable')
    critical = wt.BOOL()
    if not KERNEL.IsProcessCritical(handle, ctypes.byref(critical)):
        raise windows_error('check whether the process is critical')
    return {'created': (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
            'path': path.value, 'critical': bool(critical.value)}


def descendants(rows, roots):
    found = set(roots)
    while True:
        added = {row['pid'] for row in rows if row['parentPid'] in found} - found
        if not added:
            return found
        found.update(added)


def protected_pids(rows):
    current = os.getpid()
    protected = descendants(rows, {current})
    parents = {row['pid']: row['parentPid'] for row in rows}
    visited = {current}
    while current in parents and parents[current] not in visited:
        current = parents[current]
        visited.add(current)
    return protected | visited | {0, 4}


def root_rows(rows, name):
    if name.strip().isdigit():
        return [row for row in rows if str(row['pid']) == name.strip()]
    exact = [row for row in rows if normalize(row['name']) == normalize(name)]
    return exact or [row for row in rows if matches_process(row, name)]


def requested_rows(rows, names):
    current_session = session_id(os.getpid())
    if current_session is None:
        raise windows_error('identify the current Windows session')
    sessions = process_sessions()
    selected_pids = set()
    for name in names:
        roots = {row['pid'] for row in root_rows(rows, name)}
        selected_pids.update(roots if name.strip().isdigit() else descendants(rows, roots))
    selected = []
    for row in rows:
        if names and row['pid'] not in selected_pids:
            continue
        explicit_pid = any(str(row['pid']) == name.strip() for name in names)
        target_session = sessions[row['pid']] if row['pid'] in sessions else session_id(row['pid'])
        if explicit_pid or target_session is None or target_session == current_session:
            selected.append(row)
    return selected


def session_id(pid):
    value = wt.DWORD()
    if not KERNEL.ProcessIdToSessionId(pid, ctypes.byref(value)):
        return None
    return value.value


def process_sessions():
    entries = ctypes.POINTER(SessionProcess)()
    count = wt.DWORD()
    if not WTS.WTSEnumerateProcessesW(None, 0, 1, ctypes.byref(entries), ctypes.byref(count)):
        return {}
    try:
        return {entries[index].pid: entries[index].session for index in range(count.value)}
    finally:
        WTS.WTSFreeMemory(entries)


def request_names(context):
    names = context.get('processNames', [])
    if not isinstance(names, list) or len(names) > 8 or any(
            not isinstance(name, str) or not name.strip() or len(name) > 120 for name in names):
        raise ValueError('The requested apps or process IDs could not be identified.')
    if 'terminate_processes' in context.get('capabilities', []) and not names:
        raise ValueError('Name the apps or process IDs to force quit.')
    return names


def listing(context):
    require_capability(context, 'list_running_processes')
    names = request_names(context)
    rows = enumerate_processes()
    selected = requested_rows(rows, names)
    protected = protected_pids(rows)
    references, processes = {}, []
    snapshot = uuid.uuid4().hex[:8]
    for row in selected[:200]:
        public = {key: row.get(key) for key in ('pid', 'name', 'parentPid', 'path')}
        public.update(id=None, canTerminate=False)
        handle = None
        try:
            if row['pid'] in protected:
                raise ValueError('This process belongs to Jarvis, its host, or Windows itself.')
            handle = KERNEL.OpenProcess(0x1000, False, row['pid'])
            if not handle:
                raise windows_error('inspect this process')
            captured = identity(handle)
            if captured['critical']:
                raise ValueError('Windows identifies this as a critical process.')
            if not captured['path'].casefold().endswith('\\' + row['name'].casefold()):
                raise ValueError('The process changed during discovery; inspect it again.')
            reference = f'process:{snapshot}:{row["pid"]}'
            references[reference] = {**row, **captured}
            public.update(id=reference, canTerminate=True, path=captured['path'])
        except (OSError, ValueError) as exc:
            public['error'] = str(exc)
        finally:
            if handle:
                KERNEL.CloseHandle(handle)
        processes.append(public)
    context['_processReferences'] = references
    groups, queries = {}, []
    for index, name in enumerate(names):
        members = requested_rows(rows, [name])
        pids = {row['pid'] for row in members}
        identifier = f'app:{snapshot}:{index + 1}'
        groups[identifier] = [reference for reference, row in references.items() if row['pid'] in pids]
        queries.append({'id': identifier, 'name': name, 'count': len(members),
                        'terminableCount': len(groups[identifier])})
    context['_processGroups'] = groups
    context['_processScope'] = {row['pid']: row['name'] for row in selected}
    context['_processRoots'] = [{**row, 'byPid': name.strip().isdigit()} for name in names
                                for row in root_rows(selected, name)]
    context['_processSnapshotTime'] = time.monotonic()
    context['processControlResult'] = {'ok': not selected, 'remaining': len(selected)}
    return {'ok': True, 'processes': processes, 'matchingProcesses': len(selected),
            'omittedProcesses': max(0, len(selected) - len(processes)),
            'queries': queries}


def terminate(context, targets, cancelled):
    require_capability(context, 'terminate_processes')
    names = request_names(context)
    references = context.get('_processReferences', {})
    groups = context.get('_processGroups', {})
    if not isinstance(targets, list) or not 1 <= len(targets) <= 200 or any(
            not isinstance(target, str) or target not in references and target not in groups for target in targets):
        raise ValueError('Choose current app or process IDs from list_running_processes for this request. Inspect again if they expired; do not substitute names or PIDs.')
    if time.monotonic() - context.get('_processSnapshotTime', 0) > 60:
        raise ValueError('The process inventory expired. Inspect the running processes again.')
    targets = list(dict.fromkeys(reference for target in targets for reference in groups.get(target, [target])))
    rows = enumerate_processes()
    live = {row['pid']: row for row in rows}
    protected = protected_pids(rows)
    results, opened = [], []
    deadline = time.monotonic() + 5
    try:
        for target in dict.fromkeys(targets):
            captured = references[target]
            result = {'id': target, 'pid': captured['pid'], 'name': captured['name'], 'verified': False}
            results.append(result)
            handle = None
            try:
                if cancelled.is_set():
                    raise ValueError('Force quit was cancelled.')
                if captured['pid'] in protected:
                    raise ValueError('This process belongs to Jarvis, its host, or Windows itself.')
                if captured['pid'] not in live:
                    result.update(verified=True, state='already_exited')
                    continue
                handle = KERNEL.OpenProcess(0x100001 | 0x1000, False, captured['pid'])
                if not handle:
                    raise windows_error('open this process for termination')
                current = identity(handle)
                if current['created'] != captured['created'] or current['path'].casefold() != captured['path'].casefold():
                    raise ValueError('The process ID was reused; inspect the running processes again.')
                if current['critical']:
                    raise ValueError('Windows identifies this as a critical process.')
                opened.append((handle, result))
                handle = None
            except (OSError, ValueError) as exc:
                result.update(state='failed', error=str(exc))
            finally:
                if handle:
                    KERNEL.CloseHandle(handle)
        for handle, result in opened:
            if cancelled.is_set() or time.monotonic() >= deadline:
                result.update(state='failed', error='Force quit was cancelled or timed out.')
                continue
            if KERNEL.WaitForSingleObject(handle, 0) == 0:
                result.update(verified=True, state='already_exited')
                continue
            if not KERNEL.TerminateProcess(handle, 1):
                if KERNEL.WaitForSingleObject(handle, 0) == 0:
                    result.update(verified=True, state='exited')
                else:
                    result.update(state='failed', error=str(windows_error('terminate this process')))
                continue
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if KERNEL.WaitForSingleObject(handle, remaining_ms) == 0:
                result.update(verified=True, state='terminated')
            else:
                result.update(state='failed', error='Windows accepted termination, but process exit was not verified.')
    finally:
        for handle, _result in opened:
            KERNEL.CloseHandle(handle)
    rows = enumerate_processes()
    live_pids = {row['pid'] for row in rows}
    for result in results:
        if not result['verified'] and result['pid'] not in live_pids:
            result.update(verified=True, state='exited')
            result.pop('error', None)
    remaining_pids = set()
    for root in context.get('_processRoots', []):
        if root['byPid']:
            remaining_pids.update(row['pid'] for row in rows if row['pid'] == root['pid'])
        else:
            roots = {row['pid'] for row in rows if row['name'].casefold() == root['name'].casefold()
                     and (not root.get('path') or (row.get('path') or '').casefold() == root['path'].casefold())}
            remaining_pids.update(descendants(rows, roots))
    remaining_pids.update(row['pid'] for row in rows if context.get('_processScope', {}).get(row['pid']) == row['name'])
    remaining = [{key: row[key] for key in ('pid', 'name')} for row in rows if row['pid'] in remaining_pids]
    ok = all(result['verified'] for result in results) and not remaining
    outcome = {'ok': ok, 'verified': ok, 'results': results, 'remaining': remaining,
               'cancelled': cancelled.is_set()}
    if not ok:
        failures = [result['name'] + ': ' + result.get('error', 'exit was not verified')
                    for result in results if not result['verified']]
        if remaining:
            failures.append('Still running: ' + ', '.join(sorted({row['name'] for row in remaining})))
        outcome['error'] = ' '.join(failures) or 'Force quit was not fully verified.'
    context['processControlResult'] = outcome
    return outcome
