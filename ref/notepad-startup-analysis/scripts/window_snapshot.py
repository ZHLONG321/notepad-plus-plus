"""Read native window captions and geometry for owned process evidence."""
import ctypes as C
from ctypes import wintypes as W
import win32_probe as win

win.bind(win.U, 'GetWindowTextW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
win.bind(win.U, 'GetAncestor', W.HWND, W.HWND, W.UINT)
win.bind(win.U, 'GetWindow', W.HWND, W.HWND, W.UINT)
win.bind(win.U, 'GetWindowLongPtrW', C.c_ssize_t, W.HWND, C.c_int)
win.bind(win.U, 'IsWindowEnabled', W.BOOL, W.HWND)


def snapshot(pid):
    result = []
    roots = win.windows(pid)
    windows = list(roots)
    for item in roots:
        windows.extend(win.windows(pid, parent=item['hwnd']))
    for item in windows:
        text = C.create_unicode_buffer(1024)
        win.U.GetWindowTextW(item['hwnd'], text, len(text))
        result.append(dict(item, title=text.value, rect=win.rect(item['hwnd']),
                           root=win.U.GetAncestor(item['hwnd'], 2),
                           owner=win.U.GetWindow(item['hwnd'], 4),
                           style=win.U.GetWindowLongPtrW(item['hwnd'], -16),
                           exstyle=win.U.GetWindowLongPtrW(item['hwnd'], -20),
                           enabled=bool(win.U.IsWindowEnabled(item['hwnd']))))
    return result
