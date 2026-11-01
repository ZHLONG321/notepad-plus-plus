"""Read native top-level z-order without moving or activating any window."""
import ctypes as C
from typing import Any
from ctypes import wintypes as W

import win32_probe as win

win.bind(win.U, 'GetWindowLongPtrW', C.c_ssize_t, W.HWND, C.c_int)
win.bind(win.U, 'GetWindow', W.HWND, W.HWND, W.UINT)


def inspect(pid: int) -> dict[str, Any]:
    ordered = []
    for position, window in enumerate(win.windows()):
        if not window['visible']:
            continue
        owner_pid = W.DWORD()
        win.U.GetWindowThreadProcessId(window['hwnd'], C.byref(owner_pid))
        style = win.U.GetWindowLongPtrW(window['hwnd'], -16) & 0xffffffff
        exstyle = win.U.GetWindowLongPtrW(window['hwnd'], -20) & 0xffffffff
        area = win.rect(window['hwnd'])
        if area[2] <= 0 or area[3] <= 0:
            continue
        ordered.append(dict(window, position=position, pid=owner_pid.value,
                            style=style, exstyle=exstyle, rect=area,
                            owner=win.U.GetWindow(window['hwnd'], 4)))
    own = [w for w in ordered if w['pid'] == pid]
    # Ownerless normal windows are other applications' main surfaces.
    # Desktop wallpaper hosts are intentionally below application windows.
    others = [w for w in ordered if w['pid'] != pid and not w['owner']
              and not w['exstyle'] & 0x80 and not w['style'] & 0x40000000
              and w['class'] not in ('Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd')]
    violations = [dict(own_window=w['hwnd'], other_window=o['hwnd'], other_class=o['class'])
                  for w in own for o in others if w['position'] < o['position']]
    topmost = [w['hwnd'] for w in own if w['exstyle'] & 8]
    return dict(own=own, other_normal_windows=others, ahead_of_other_normal_windows=violations,
                own_topmost_windows=topmost,
                passed=bool(own) and not violations and not topmost)
