"""Request a native window-rendered still; this is not compositor timing evidence."""
import ctypes as C
import struct
import win32_probe as win

win.bind(win.U, 'PrintWindow', win.W.BOOL, win.W.HWND, win.H, win.W.UINT)


def capture(hwnd, pid, destination):
    owner = win.W.DWORD()
    win.U.GetWindowThreadProcessId(hwnd, C.byref(owner))
    assert owner.value == pid
    _, _, width, height = win.rect(hwnd)
    assert width > 0 and height > 0
    screen = win.U.GetDC(None)
    memory = win.G.CreateCompatibleDC(screen)
    bitmap = win.G.CreateCompatibleBitmap(screen, width, height)
    previous = win.G.SelectObject(memory, bitmap)
    try:
        assert win.U.PrintWindow(hwnd, memory, 2), 'PrintWindow failed'
        win.G.SelectObject(memory, previous)
        info = struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, width*height*4, 0, 0, 0, 0)
        data = C.create_string_buffer(width*height*4)
        assert win.G.GetDIBits(memory, bitmap, 0, height, data, info, 0)==height
        win.png(destination, data.raw, width, height)
        return dict(method='PrintWindow PW_RENDERFULLCONTENT; requested native rendering, not compositor/display timing',
                    width=width, height=height)
    finally:
        win.G.SelectObject(memory, previous)
        win.G.DeleteObject(bitmap)
        win.G.DeleteDC(memory)
        win.U.ReleaseDC(None, screen)
