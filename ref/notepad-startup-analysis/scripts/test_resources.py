"""Keep the owned UI probes and their child processes below foreground work."""
import ctypes as C
from ctypes import wintypes as W
import json

K = C.WinDLL('kernel32', use_last_error=True)
K.GetCurrentProcess.restype = W.HANDLE
K.GetProcessAffinityMask.argtypes = [W.HANDLE, C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)]
K.SetProcessAffinityMask.argtypes = [W.HANDLE, C.c_size_t]
K.GetPriorityClass.argtypes = [W.HANDLE]
K.SetPriorityClass.argtypes = [W.HANDLE, W.DWORD]
_limits = None


def process_limits(handle):
    available, system = C.c_size_t(), C.c_size_t()
    if not K.GetProcessAffinityMask(handle, C.byref(available), C.byref(system)):
        raise C.WinError(C.get_last_error())
    return dict(priority_class=K.GetPriorityClass(handle), affinity_mask=available.value,
        logical_processors=available.value.bit_count(), system_logical_processors=system.value.bit_count())


def limit_test_resources():
    global _limits
    if _limits is None:
        handle = K.GetCurrentProcess()
        mask = process_limits(handle)['affinity_mask']
        first = mask & -mask
        remaining = mask & ~first
        selected = first | (remaining & -remaining)
        if not K.SetProcessAffinityMask(handle, selected):
            raise C.WinError(C.get_last_error())
        if not K.SetPriorityClass(handle, 0x4000):
            raise C.WinError(C.get_last_error())
        _limits = process_limits(handle)
        print('TEST_RESOURCE_LIMITS', json.dumps(_limits), flush=True)
    return dict(_limits)
