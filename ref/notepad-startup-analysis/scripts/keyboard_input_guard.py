"""Reject real-key measurements contaminated by other keyboard input.

Wait for desktop idle before a foreground trial. Create KeyboardInputGuard before
launch, set target_pid to the owned process, and tag SendInput with INPUT_TAG.
Call check() while measuring and close() in finally. The guard never blocks or
redirects keys; the caller restores the foreground on interruption. No keys from
other applications, or character values from outside input, are retained.
"""
import ctypes as C
from ctypes import wintypes as W
import threading
import time
from typing import cast, final

INPUT_TAG = 0x4E505053
U = C.WinDLL('user32', use_last_error=True)
K = C.WinDLL('kernel32', use_last_error=True)
CALLBACK = C.WINFUNCTYPE(C.c_ssize_t, C.c_int, W.WPARAM, W.LPARAM)


@final
class _Keyboard(C.Structure):
    # ctypes creates descriptors backed by the native structure, not __init__.
    flags: int  # pyright: ignore[reportUninitializedInstanceVariable]
    extra: int  # pyright: ignore[reportUninitializedInstanceVariable]
    _fields_ = [('vk', W.DWORD), ('scan', W.DWORD), ('flags', W.DWORD),
                ('tick', W.DWORD), ('extra', C.c_size_t)]


@final
class _LastInput(C.Structure):
    tick: int  # pyright: ignore[reportUninitializedInstanceVariable]
    _fields_ = [('size', W.UINT), ('tick', W.DWORD)]


U.SetWindowsHookExW.argtypes = [C.c_int, CALLBACK, W.HINSTANCE, W.DWORD]
U.SetWindowsHookExW.restype = W.HANDLE
U.CallNextHookEx.argtypes = [W.HANDLE, C.c_int, W.WPARAM, W.LPARAM]
U.CallNextHookEx.restype = C.c_ssize_t
U.UnhookWindowsHookEx.argtypes = [W.HANDLE]
U.UnhookWindowsHookEx.restype = W.BOOL
U.GetForegroundWindow.restype = W.HWND
U.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
U.GetMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
U.GetMessageW.restype = W.BOOL
U.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
U.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
U.PostThreadMessageW.restype = W.BOOL
U.GetLastInputInfo.argtypes = [C.POINTER(_LastInput)]
U.GetLastInputInfo.restype = W.BOOL
K.GetCurrentThreadId.restype = W.DWORD
K.GetTickCount.restype = W.DWORD


def wait_for_input_idle() -> bool:
    """Wait at most 20 seconds for five seconds without desktop input.

    False means no foreground trial should be launched. The wait is outside the
    measured startup interval; it does not turn a launch into a cold-cache test.
    """
    deadline = time.perf_counter() + 20
    while True:
        last = _LastInput(C.sizeof(_LastInput), 0)
        if not U.GetLastInputInfo(C.byref(last)):
            raise C.WinError(C.get_last_error())
        if ((cast(int, K.GetTickCount()) - last.tick) & 0xffffffff) >= 5000:
            return True
        if time.perf_counter() >= deadline:
            return False
        time.sleep(.1)


@final
class KeyboardInputGuard:
    """Observe the owned foreground PID on a dedicated, promptly pumped thread.

    Counters describe input provenance, not typed content. close() stops and
    unhooks the observer and is safe to call again after success. A failed hook
    or cleanup raises instead of claiming an uncontaminated measurement.
    """
    def __init__(self) -> None:
        self.target_pid = 0
        self.tagged_events = 0
        self.other_events = 0
        self.other_noninjected_events = 0
        self.first_other_qpc: float | None = None
        self.removed = False
        self._ready = threading.Event()
        self._error: str | None = None
        self._thread_id = 0
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        if not self._ready.wait(3):
            raise TimeoutError('Keyboard observer did not start')
        if self._error:
            raise RuntimeError(self._error)

    def _run(self) -> None:
        self._thread_id = cast(int, K.GetCurrentThreadId())
        msg = W.MSG()
        U.PeekMessageW(C.byref(msg), None, 0, 0, 0)

        @CALLBACK
        def callback(code: int, wp: int, lp: int) -> int:
            if code == 0 and self.target_pid:
                owner = W.DWORD()
                U.GetWindowThreadProcessId(U.GetForegroundWindow(), C.byref(owner))
                if owner.value == self.target_pid:
                    key = C.cast(lp, C.POINTER(_Keyboard)).contents
                    if key.extra == INPUT_TAG and key.flags & 0x10:
                        self.tagged_events += 1
                    else:
                        self.other_events += 1
                        self.other_noninjected_events += int(not key.flags & 0x10)
                        if self.first_other_qpc is None:
                            self.first_other_qpc = time.perf_counter()
            return cast(int, U.CallNextHookEx(None, code, wp, lp))

        hook = cast(int | None, U.SetWindowsHookExW(13, callback, None, 0))
        if not hook:
            self._error = 'Keyboard observer hook failed: ' + str(C.get_last_error())
            self._ready.set()
            return
        self._ready.set()
        try:
            while True:
                result = cast(int, U.GetMessageW(C.byref(msg), None, 0, 0))
                if result <= 0:
                    if result < 0:
                        self._error = 'Keyboard observer queue failed: ' + str(C.get_last_error())
                    break
        finally:
            self.removed = cast(int, U.UnhookWindowsHookEx(hook)) != 0

    def check(self) -> None:
        """Reject outside keyboard activity or a failed observer."""
        if self._error:
            raise RuntimeError(self._error)
        if self.other_events:
            raise RuntimeError('Other keyboard input reached the test window; sample interrupted')

    def close(self) -> None:
        if self._worker.is_alive():
            if not U.PostThreadMessageW(self._thread_id, 0x12, 0, 0):
                raise C.WinError(C.get_last_error())
            self._worker.join(3)
            if self._worker.is_alive():
                raise TimeoutError('Keyboard observer did not stop')
        if self._error:
            raise RuntimeError(self._error)
        if not self.removed:
            raise RuntimeError('Keyboard observer could not be removed')

    def report(self) -> dict[str, int | float | bool | None]:
        return dict(tagged_events=self.tagged_events, other_events=self.other_events,
                    other_noninjected_events=self.other_noninjected_events,
                    first_other_qpc_seconds=self.first_other_qpc, hook_removed=self.removed)
