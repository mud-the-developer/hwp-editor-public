"""Read-only Windows process identity/exit handles. No COM, shell or termination.

Identity is evidence, NOT document ownership or permission to Clear/Quit. A caller
must separately bind a fresh process/window and verify its known document scope.
"""

import ctypes
import os
from pathlib import Path

_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_MAX_PID = 0xFFFFFFFF


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def _ticks(value):
    return (int(value.high) << 32) | int(value.low)


def _kernel():
    if os.name != "nt":
        raise RuntimeError("Windows process metadata requires Windows")
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = ctypes.c_void_p
    dword = ctypes.c_uint32
    dll.OpenProcess.argtypes = [dword, ctypes.c_int, dword]
    dll.OpenProcess.restype = handle
    dll.GetProcessId.argtypes = [handle]
    dll.GetProcessId.restype = dword
    dll.GetProcessTimes.argtypes = [handle, *[ctypes.POINTER(_FileTime)] * 4]
    dll.GetProcessTimes.restype = ctypes.c_int
    dll.QueryFullProcessImageNameW.argtypes = [
        handle,
        dword,
        ctypes.c_wchar_p,
        ctypes.POINTER(dword),
    ]
    dll.QueryFullProcessImageNameW.restype = ctypes.c_int
    dll.GetSystemTimeAsFileTime.argtypes = [ctypes.POINTER(_FileTime)]
    dll.GetSystemTimeAsFileTime.restype = None
    dll.WaitForSingleObject.argtypes = [handle, dword]
    dll.WaitForSingleObject.restype = dword
    dll.CloseHandle.argtypes = [handle]
    dll.CloseHandle.restype = ctypes.c_int
    return dll


def system_filetime():
    """Current UTC FILETIME ticks, for a future caller's fresh-creation interval."""
    value = _FileTime()
    _kernel().GetSystemTimeAsFileTime(ctypes.byref(value))
    result = _ticks(value)
    if result <= 0:
        raise RuntimeError("System creation-time reference unavailable")
    return result


def window_process_id(window_handle):
    """Read the PID of a specific HWND; never use worker/Popen PID as a substitute."""
    maximum = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
    if type(window_handle) is not int or not 0 < window_handle <= maximum:
        raise ValueError("Typed positive bounded window handle required")
    user = _user()
    pid = ctypes.c_uint32()
    if (
        not user.GetWindowThreadProcessId(window_handle, ctypes.byref(pid))
        or not pid.value
    ):
        raise RuntimeError("Window process identity unavailable")
    return pid.value


def _user():
    if os.name != "nt":
        raise RuntimeError("Windows window metadata requires Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    user.GetWindowThreadProcessId.restype = ctypes.c_uint32
    return user


def bind_window_process(window_handle, started_filetime, expected_image):
    """Hold matching fresh window/process evidence, NOT permission to close a document.

    The expected image must come from trusted caller configuration, not the image
    just observed or a serialized report. Caller must close the returned handle.
    """
    if type(started_filetime) is not int or started_filetime <= 0:
        raise ValueError("Typed positive creation-time reference required")
    expected = Path(expected_image)
    if not expected.is_absolute():
        raise ValueError("Absolute trusted process image required")
    expected = expected.resolve()
    pid = window_process_id(window_handle)
    held = ProcessIdentity(pid)
    try:
        snapshot = held.snapshot()
        created, image = snapshot["creation_filetime"], snapshot["image"]
        if (
            type(snapshot["pid"]) is not int
            or snapshot["pid"] != pid
            or type(created) is not int
            or not started_filetime <= created <= system_filetime()
            or not isinstance(image, str)
            or not os.path.isabs(image)
            or Path(image).resolve() != expected
        ):
            raise RuntimeError("Window is not bound to the expected fresh process")
        if window_process_id(window_handle) != pid:
            raise RuntimeError("Window process changed during ownership observation")
        return held
    except BaseException:
        held.close()
        raise


class ProcessIdentity:
    """Hold exactly one query/synchronize handle; PID reuse cannot retarget it."""

    def __init__(self, pid):
        if type(pid) is not int or not 0 < pid <= _MAX_PID:
            raise ValueError("Typed positive bounded process ID required")
        self.pid = pid
        self._handle = None
        self._api = _kernel()
        value = self._api.OpenProcess(
            _QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid
        )
        if not value:
            raise RuntimeError("Read-only process handle unavailable")
        self._handle = value
        try:
            actual_pid = self._api.GetProcessId(value)
            if type(actual_pid) is not int or actual_pid != pid:
                raise RuntimeError("Process handle identity differs from requested PID")
            self._snapshot = self._read_identity()
        except BaseException:
            self.close()
            raise

    def _require_handle(self):
        if self._handle is None:
            raise RuntimeError("Process handle already closed")
        return self._handle

    def _read_identity(self):
        handle = self._require_handle()
        created, exited, kernel, user = (_FileTime() for _ in range(4))
        if not self._api.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise RuntimeError("Process creation-time metadata unavailable")
        ticks = _ticks(created)
        if ticks <= 0:
            raise RuntimeError("Invalid process creation-time metadata")
        capacity = ctypes.c_uint32(32768)
        image = ctypes.create_unicode_buffer(capacity.value)
        if not self._api.QueryFullProcessImageNameW(
            handle, 0, image, ctypes.byref(capacity)
        ):
            raise RuntimeError("Process image metadata unavailable")
        units = len(image.value.encode("utf-16-le", "surrogatepass")) // 2
        if (
            not 0 < capacity.value < len(image)
            or units != capacity.value
            or not os.path.isabs(image.value)
        ):
            raise RuntimeError("Invalid/truncated process image metadata")
        return {"pid": self.pid, "creation_filetime": ticks, "image": image.value}

    def snapshot(self):
        self._require_handle()
        return dict(self._snapshot)

    def wait_for_exit(self):
        """Bounded wait for THIS handle only; never kills or opens another PID."""
        result = self._api.WaitForSingleObject(self._require_handle(), 5000)
        if type(result) is not int:
            raise RuntimeError("Untyped process exit result")
        if result == _WAIT_TIMEOUT:
            raise RuntimeError("Owned process exit unconfirmed; stop and inspect")
        if result != _WAIT_OBJECT_0:
            raise RuntimeError("Process exit wait failed; no retry or termination")
        return True

    def close(self):
        value, self._handle = self._handle, None
        if value is not None and not self._api.CloseHandle(value):
            raise RuntimeError("Process handle release unconfirmed")

    def __enter__(self):
        self._require_handle()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
