"""Read-only WCT sampling, using the installed Windows SDK wct.h layout."""
import ctypes as C
from ctypes import wintypes as W
import threading
import time
from typing import Any
import win32_probe as win


class Lock(C.Structure):
    _fields_ = [('name', W.WCHAR*128), ('timeout', C.c_longlong), ('alertable', W.BOOL)]


class Thread(C.Structure):
    _fields_ = [('pid', W.DWORD), ('tid', W.DWORD), ('wait', W.DWORD), ('switches', W.DWORD)]


class Data(C.Union):
    _fields_ = [('lock', Lock), ('thread', Thread)]


class Node(C.Structure):
    _fields_ = [('type', W.DWORD), ('status', W.DWORD), ('data', Data)]


A = C.WinDLL('advapi32', use_last_error=True)
win.bind(A, 'OpenThreadWaitChainSession', W.HANDLE, W.DWORD, C.c_void_p)
win.bind(A, 'CloseThreadWaitChainSession', None, W.HANDLE)
win.bind(A, 'GetThreadWaitChain', W.BOOL, W.HANDLE, C.c_size_t, W.DWORD, W.DWORD, C.POINTER(W.DWORD), C.POINTER(Node), C.POINTER(W.BOOL))


def sample(tid, started, stop, rows):
    session = A.OpenThreadWaitChainSession(0, None)
    if not session:
        rows.append({'error': C.get_last_error()})
        return
    try:
        while not stop.is_set():
            count = W.DWORD(16)
            nodes = (Node*16)()
            cycle = W.BOOL()
            ok = A.GetThreadWaitChain(session, 0, 7, tid, C.byref(count), nodes, C.byref(cycle))
            row: dict[str, Any] = {'ms': (time.perf_counter()-started)*1000, 'ok': bool(ok), 'nodes': []}
            if ok:
                for node in nodes[:min(count.value, 16)]:
                    item = {'type': node.type, 'status': node.status}
                    if node.type == 8:
                        item.update(pid=node.data.thread.pid, tid=node.data.thread.tid, wait=node.data.thread.wait)
                    row['nodes'].append(item)
            else:
                row['error'] = C.get_last_error()
            rows.append(row)
            if not ok:
                break
            stop.wait(.006)
    finally:
        A.CloseThreadWaitChainSession(session)


def start(tid, started, rows):
    stop = threading.Event()
    worker = threading.Thread(target=sample, args=(tid, started, stop, rows), daemon=True)
    worker.start()
    return stop, worker
