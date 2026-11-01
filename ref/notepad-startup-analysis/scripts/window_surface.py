"""Read DWM's actual suppression state before starting window-frame capture."""
import ctypes as C
from ctypes import wintypes as W

DWM = C.WinDLL('dwmapi')
DWM.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
DWM.DwmGetWindowAttribute.restype = C.c_long


def uncloaked(hwnd):
    flags = W.DWORD()
    # DWMWA_CLOAKED is 14 in the Windows SDK DWMWINDOWATTRIBUTE enum.
    result = DWM.DwmGetWindowAttribute(hwnd, 14, C.byref(flags), C.sizeof(flags))
    if result < 0:
        raise RuntimeError(f'DwmGetWindowAttribute failed: {result:#x}')
    return flags.value==0
