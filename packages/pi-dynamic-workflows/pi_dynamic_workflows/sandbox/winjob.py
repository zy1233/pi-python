"""Windows Job Object that bounds a workflow script's process (imported on Windows only).

What the job enforces, for the kernel and whatever the script does: the process gets at most
*memory_bytes* of committed memory and *cpu_seconds* of user CPU time, can start no other
process (``ActiveProcessLimit`` 1), cannot touch the desktop, clipboard or system parameters,
and dies with the job, which is closed when the host lets go of it, so a host that crashes
leaves nothing behind.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_UILIMIT_ALL = 0x000000FF
_JOB_OBJECT_BASIC_UI_RESTRICTIONS = 4
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _BasicLimits(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class _IoCounters(ctypes.Structure):
    _fields_ = tuple((name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob"))


class _ExtendedLimits(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class _UiRestrictions(ctypes.Structure):
    _fields_ = (("UIRestrictionsClass", wintypes.DWORD),)


_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
_kernel32.SetInformationJobObject.restype = wintypes.BOOL
_kernel32.SetInformationJobObject.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_kernel32.TerminateJobObject.restype = wintypes.BOOL
_kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def _fail(what: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{what} failed: {ctypes.FormatError(code).strip()}")


class JobObject:
    def __init__(self, *, memory_bytes: int, cpu_seconds: int) -> None:
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise _fail("CreateJobObject")
        self._handle: int | None = handle
        try:
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_PROCESS_TIME
                | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            limits.BasicLimitInformation.PerProcessUserTimeLimit = cpu_seconds * 10_000_000
            limits.BasicLimitInformation.ActiveProcessLimit = 1
            limits.ProcessMemoryLimit = memory_bytes
            if not _kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise _fail("SetInformationJobObject (limits)")
            ui = _UiRestrictions(_JOB_OBJECT_UILIMIT_ALL)
            if not _kernel32.SetInformationJobObject(
                handle, _JOB_OBJECT_BASIC_UI_RESTRICTIONS, ctypes.byref(ui), ctypes.sizeof(ui)
            ):
                raise _fail("SetInformationJobObject (ui)")
        except BaseException:
            self.close()
            raise

    def assign(self, pid: int) -> None:
        """Put the process in the job; from then on its limits apply to it and its children."""
        process = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            raise _fail("OpenProcess")
        try:
            if not _kernel32.AssignProcessToJobObject(self._handle, process):
                raise _fail("AssignProcessToJobObject")
        finally:
            _kernel32.CloseHandle(process)

    def terminate(self) -> None:
        if self._handle is not None:
            _kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            _kernel32.CloseHandle(handle)

    def __del__(self) -> None:
        self.close()
