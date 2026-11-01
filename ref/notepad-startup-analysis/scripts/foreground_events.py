"""Observe foreground changes without injecting input or changing focus."""
import ctypes as C
from ctypes import wintypes as W
import win32_probe as win
from test_resources import limit_test_resources

CALLBACK = C.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, W.LONG, W.LONG, W.DWORD, W.DWORD)
win.bind(win.U, 'SetWinEventHook', W.HANDLE, W.DWORD, W.DWORD, W.HMODULE, CALLBACK, W.DWORD, W.DWORD, W.DWORD)
win.bind(win.U, 'UnhookWinEvent', W.BOOL, W.HANDLE)
win.bind(win.U, 'PeekMessageW', W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
win.bind(win.U, 'DispatchMessageW', C.c_ssize_t, C.POINTER(W.MSG))


class Observer:
    def __init__(self, process_id=0):
        self.resource_limits = limit_test_resources()
        self.process_id = process_id
        self.events = []
        self.callback = CALLBACK(self.record)
        self.hook = win.U.SetWinEventHook(3, 3, None, self.callback, process_id, 0, 0)
        if not self.hook:
            raise C.WinError(C.get_last_error())

    def record(self, hook, event, hwnd, object_id, child_id, thread, stamp):
        pid = W.DWORD()
        win.U.GetWindowThreadProcessId(hwnd, C.byref(pid))
        name = C.create_unicode_buffer(256)
        win.U.GetClassNameW(hwnd, name, len(name))
        self.events.append({'tick_ms': stamp, 'hwnd': hwnd, 'pid': pid.value or (self.process_id if hwnd else 0),
                            'class': name.value, 'visible': bool(win.U.IsWindowVisible(hwnd))})

    def pump(self):
        msg = W.MSG()
        while win.U.PeekMessageW(C.byref(msg), None, 0, 0, 1):
            win.U.DispatchMessageW(C.byref(msg))

    def close(self):
        self.pump()
        win.U.UnhookWinEvent(self.hook)
