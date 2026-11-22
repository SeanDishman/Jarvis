import ctypes
from ctypes import wintypes
import time


def walk_controls(hwnd, auto, deadline):
    import comtypes.client
    from comtypes.automation import VARIANT

    interfaces = comtypes.client.GetModule('oleacc.dll')
    automation = comtypes.client.GetModule('UIAutomationCore.dll')
    client = comtypes.client.CreateObject('{ff48dba4-60ef-4201-aa87-54103eef594e}',
                                         interface=automation.IUIAutomation)
    accessible = ctypes.POINTER(interfaces.IAccessible)
    ole = ctypes.OleDLL('oleacc')
    ole.AccessibleObjectFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD,
        ctypes.POINTER(comtypes.GUID), ctypes.POINTER(accessible)]
    ole.AccessibleChildren.argtypes = [accessible, wintypes.LONG, wintypes.LONG,
        ctypes.POINTER(VARIANT), ctypes.POINTER(wintypes.LONG)]
    root = accessible()
    ole.AccessibleObjectFromWindow(hwnd, 0xfffffffc, ctypes.byref(interfaces.IAccessible._iid_), ctypes.byref(root))
    pending = [(root, 0, 0)]
    visited = 0
    while pending and visited < 600 and time.monotonic() < deadline:
        node, child_id, depth = pending.pop()
        visited += 1
        try:
            element = client.ElementFromIAccessible(node, child_id)
            yield auto.Control.CreateControlFromElement(element), depth
        except Exception:
            yield None, depth
        if child_id or depth >= 24:
            continue
        try:
            count = min(node.accChildCount, 600 - visited)
            if not count:
                continue
            values = (VARIANT * count)()
            obtained = wintypes.LONG()
            ole.AccessibleChildren(node, 0, count, values, ctypes.byref(obtained))
            children = []
            for variant in values[:obtained.value]:
                try:
                    value = variant.value
                    if hasattr(value, 'QueryInterface'):
                        children.append((value.QueryInterface(interfaces.IAccessible), 0, depth + 1))
                    elif isinstance(value, int) and value > 0:
                        children.append((node, value, depth + 1))
                except Exception:
                    continue
            pending.extend(reversed(children))
        except Exception:
            continue
