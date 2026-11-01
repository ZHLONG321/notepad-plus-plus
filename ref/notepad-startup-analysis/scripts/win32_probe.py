import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import struct
import zlib

U = C.WinDLL('user32', use_last_error=True)
G = C.WinDLL('gdi32', use_last_error=True)
K = C.WinDLL('kernel32', use_last_error=True)
H = W.HANDLE
PTR = C.c_size_t
LPTR = C.c_ssize_t
CALLBACK = C.WINFUNCTYPE(W.BOOL, W.HWND, LPTR)


def bind(dll, name, restype, *args):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = list(args)
    return fn


bind(U, 'EnumWindows', W.BOOL, CALLBACK, LPTR)
bind(U, 'EnumChildWindows', W.BOOL, W.HWND, CALLBACK, LPTR)
bind(U, 'GetWindowThreadProcessId', W.DWORD, W.HWND, C.POINTER(W.DWORD))
bind(U, 'GetClassNameW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
bind(U, 'GetWindowTextW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
bind(U, 'IsWindowVisible', W.BOOL, W.HWND)
bind(U, 'IsWindow', W.BOOL, W.HWND)
bind(U, 'GetWindowRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
bind(U, 'GetClientRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
bind(U, 'ClientToScreen', W.BOOL, W.HWND, C.POINTER(W.POINT))
bind(U, 'GetDC', H, W.HWND)
bind(U, 'ReleaseDC', C.c_int, W.HWND, H)
bind(U, 'SetForegroundWindow', W.BOOL, W.HWND)
bind(U, 'SetWindowPos', W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
bind(U, 'GetForegroundWindow', W.HWND)
bind(U, 'ShowWindow', W.BOOL, W.HWND, C.c_int)
bind(U, 'SetCursorPos', W.BOOL, C.c_int, C.c_int)
bind(U, 'PostMessageW', W.BOOL, W.HWND, W.UINT, PTR, LPTR)
bind(U, 'SendMessageTimeoutW', PTR, W.HWND, W.UINT, PTR, LPTR, W.UINT, W.UINT, C.POINTER(PTR))
bind(G, 'CreateCompatibleDC', H, H)
bind(G, 'CreateCompatibleBitmap', H, H, C.c_int, C.c_int)
bind(G, 'SelectObject', H, H, H)
bind(G, 'BitBlt', W.BOOL, H, C.c_int, C.c_int, C.c_int, C.c_int, H, C.c_int, C.c_int, W.DWORD)
bind(G, 'DeleteObject', W.BOOL, H)
bind(G, 'DeleteDC', W.BOOL, H)
bind(G, 'GetDIBits', C.c_int, H, H, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT)
bind(K, 'OpenProcess', H, W.DWORD, W.BOOL, W.DWORD)
bind(K, 'CloseHandle', W.BOOL, H)
bind(K, 'K32EnumProcessModules', W.BOOL, H, C.POINTER(H), W.DWORD, C.POINTER(W.DWORD))
bind(K, 'K32GetModuleFileNameExW', W.DWORD, H, H, W.LPWSTR, W.DWORD)


class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', PTR)]


class INPUTUNION(C.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ('union',)
    _fields_ = [('type', W.DWORD), ('union', INPUTUNION)]


bind(U, 'SendInput', W.UINT, W.UINT, C.POINTER(INPUT), C.c_int)


def text(hwnd, classname=False):
    buf = C.create_unicode_buffer(1024)
    (U.GetClassNameW if classname else U.GetWindowTextW)(hwnd, buf, len(buf))
    return buf.value


def windows(pid=None, parent=None):
    result = []

    @CALLBACK
    def callback(hwnd, _):
        owner = W.DWORD()
        U.GetWindowThreadProcessId(hwnd, C.byref(owner))
        if pid is None or owner.value == pid:
            result.append({'hwnd': hwnd, 'class': text(hwnd, True), 'visible': bool(U.IsWindowVisible(hwnd))})
        return True

    if parent:
        U.EnumChildWindows(parent, callback, 0)
    else:
        U.EnumWindows(callback, 0)
    return result


def rect(hwnd, client=False):
    r = W.RECT()
    if client:
        U.GetClientRect(hwnd, C.byref(r))
        point = W.POINT()
        U.ClientToScreen(hwnd, C.byref(point))
        return point.x, point.y, r.right, r.bottom
    U.GetWindowRect(hwnd, C.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def capture(area):
    x, y, width, height = area
    if width <= 0 or height <= 0:
        raise ValueError('Empty screen region')
    screen = U.GetDC(None)
    memory = G.CreateCompatibleDC(screen)
    bitmap = G.CreateCompatibleBitmap(screen, width, height)
    previous = G.SelectObject(memory, bitmap)
    try:
        if not G.BitBlt(memory, 0, 0, width, height, screen, x, y, 0x00CC0020):
            raise C.WinError(C.get_last_error())
        G.SelectObject(memory, previous)
        info = struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        buffer = C.create_string_buffer(width * height * 4)
        if not G.GetDIBits(memory, bitmap, 0, height, buffer, info, 0):
            raise C.WinError(C.get_last_error())
        return buffer.raw
    finally:
        G.SelectObject(memory, previous)
        G.DeleteObject(bitmap)
        G.DeleteDC(memory)
        U.ReleaseDC(None, screen)


def png(path, bgra, width, height):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    rgb = bytearray(width * height * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    rows = b''.join(b'\0' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def message(hwnd, msg, wparam=0, lparam=0, timeout=100):
    result = PTR()
    success = U.SendMessageTimeoutW(hwnd, msg, wparam, lparam, 0x2, timeout, C.byref(result))
    return result.value if success else None


def unicode_input(value):
    events = []
    for char in value:
        events.extend([INPUT(type=1, ki=KEYBDINPUT(wScan=ord(char), dwFlags=4)), INPUT(type=1, ki=KEYBDINPUT(wScan=ord(char), dwFlags=6))])
    array = (INPUT * len(events))(*events)
    if U.SendInput(len(events), array, C.sizeof(INPUT)) != len(events):
        raise C.WinError(C.get_last_error())


def click(x, y):
    U.SetCursorPos(x, y)
    events = (INPUT * 2)(INPUT(type=0, mi=MOUSEINPUT(dwFlags=2)), INPUT(type=0, mi=MOUSEINPUT(dwFlags=4)))
    if U.SendInput(2, events, C.sizeof(INPUT)) != 2:
        raise C.WinError(C.get_last_error())


def foreground(hwnd):
    # The screen probe needs an unoccluded test window, including during DWM animation.
    U.SetWindowPos(hwnd, W.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
    if U.GetForegroundWindow() != hwnd:
        # A zero-distance mouse event avoids Alt key combinations with concurrent input.
        events = (INPUT * 1)(INPUT(type=0, mi=MOUSEINPUT(dwFlags=1)))
        U.SendInput(1, events, C.sizeof(INPUT))
        U.SetForegroundWindow(hwnd)
    return U.GetForegroundWindow() == hwnd


def modules(pid):
    process = K.OpenProcess(0x410, False, pid)
    if not process:
        return []
    try:
        handles = (H * 2048)()
        needed = W.DWORD()
        if not K.K32EnumProcessModules(process, handles, C.sizeof(handles), C.byref(needed)):
            return []
        result = []
        for item in handles[:needed.value // C.sizeof(H)]:
            buf = C.create_unicode_buffer(32768)
            if K.K32GetModuleFileNameExW(process, item, buf, len(buf)):
                result.append(Path(buf.value))
        return result
    finally:
        K.CloseHandle(process)
