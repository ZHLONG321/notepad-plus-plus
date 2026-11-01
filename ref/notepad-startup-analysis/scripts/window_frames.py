"""Consume compositor frames from the native HWND observer."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import win32_probe as win
from test_resources import limit_test_resources

_capture_dll = C.CDLL(str(Path(__file__).with_suffix('.dll')))


class WindowFrames:
    @staticmethod
    def prepare():
        """Initialize the recording device before timing the target process."""
        limit_test_resources()
        _capture_dll.capture_prepare.argtypes = []
        _capture_dll.capture_prepare.restype = C.c_int
        result = _capture_dll.capture_prepare()
        if result:
            raise RuntimeError(f'Window capture preparation failed: {result:#x}')

    @staticmethod
    def resource_limits():
        fps, priority = C.c_int(), C.c_int()
        _capture_dll.capture_resource_limits.argtypes = [C.POINTER(C.c_int), C.POINTER(C.c_int)]
        _capture_dll.capture_resource_limits.restype = C.c_int
        result = _capture_dll.capture_resource_limits(C.byref(fps), C.byref(priority))
        if result:
            raise RuntimeError(f'Window capture limits query failed: {result:#x}')
        return dict(max_recorded_fps=fps.value, gpu_thread_priority=priority.value)

    def __init__(self, hwnd: int, deferred: bool = False):
        self.hwnd = hwnd
        self.deferred = deferred
        self.deferred_bounds = None
        self.dll = _capture_dll
        self.dll.capture_start.argtypes = [C.c_uint64]
        self.dll.capture_start.restype = C.c_int
        self.dll.capture_start_deferred.argtypes = [C.c_uint64]
        self.dll.capture_start_deferred.restype = C.c_int
        self.dll.capture_pause.argtypes = []
        self.dll.capture_pause.restype = None
        self.dll.capture_storage_state.argtypes = [C.POINTER(C.c_int)]*3
        self.dll.capture_storage_state.restype = C.c_int
        self.dll.capture_next.argtypes = [C.c_void_p, C.c_size_t, C.POINTER(C.c_int),
            C.POINTER(C.c_int), C.POINTER(C.c_int64), C.POINTER(C.c_int64)]
        self.dll.capture_next.restype = C.c_int
        self.dll.capture_stop.argtypes = []
        self.dll.capture_stop.restype = None
        self.buffer = C.create_string_buffer(2560*1440*4)
        self.dwm = C.WinDLL('dwmapi')
        self.dwm.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
        self.dwm.DwmGetWindowAttribute.restype = W.LONG
        win.bind(win.K, 'QueryPerformanceFrequency', W.BOOL, C.POINTER(C.c_int64))
        frequency = C.c_int64()
        assert win.K.QueryPerformanceFrequency(C.byref(frequency))
        self.frequency = frequency.value
        result = (self.dll.capture_start_deferred if deferred else self.dll.capture_start)(hwnd)
        if result:
            raise RuntimeError(f'Window capture start failed: {result:#x}')

    def next(self):
        width, height, compositor, received = C.c_int(), C.c_int(), C.c_int64(), C.c_int64()
        result = self.dll.capture_next(self.buffer, len(self.buffer), C.byref(width),
            C.byref(height), C.byref(compositor), C.byref(received))
        if result == 0:
            return None
        if result != 1:
            raise RuntimeError(f'Window capture frame failed: {result:#x}')
        bounds = self.deferred_bounds or W.RECT()
        if self.deferred_bounds is None:
            assert self.dwm.DwmGetWindowAttribute(self.hwnd, 9, C.byref(bounds), C.sizeof(bounds))==0
        assert width.value == bounds.right-bounds.left and height.value == bounds.bottom-bounds.top, (
            width.value, height.value, bounds.left, bounds.top, bounds.right, bounds.bottom)
        return (compositor.value/10_000_000, received.value/self.frequency,
                (bounds.left, bounds.top, width.value, height.value),
                C.string_at(self.buffer, width.value*height.value*4))

    def close(self):
        self.dll.capture_stop()

    def pause(self):
        self.dll.capture_pause()
        if self.deferred:
            bounds = W.RECT()
            assert self.dwm.DwmGetWindowAttribute(self.hwnd, 9, C.byref(bounds), C.sizeof(bounds))==0
            self.deferred_bounds = bounds

    def storage_state(self):
        copied, readbacks, retained = C.c_int(), C.c_int(), C.c_int()
        result = self.dll.capture_storage_state(C.byref(copied), C.byref(readbacks), C.byref(retained))
        if result:
            raise RuntimeError(f'Window capture storage query failed: {result:#x}')
        return dict(gpu_copies=copied.value, cpu_readbacks=readbacks.value, retained_frames=retained.value)
