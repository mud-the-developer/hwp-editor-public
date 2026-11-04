"""Read-only byte identity for the pure report path, never Native admission."""

import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID

import hwp_resources as resources
from hwp_native_payload import NATIVE_SOURCE_FILES

CONTRACT = "hwp-report-runtime-v2"
SOURCE_FILES = (
    "hwp_report_runtime.py",
    "hwp_report_harness.py",
    "hwp_template_draft.py",
    "hwp_review.py",
    "hwp_object_captions.py",
    "hwp_resources.py",
    "hwp_report_project.py",
    "hwp_blank_document.py",
    "hwp_report_control.py",
    "hwp_report_writer.py",
    "hwp_pi_chat.py",
    "hwp_pi_probe.py",
    "hwp_pi_context.py",
    "hwp_native_host.py",
    "hwp_workspace.py",
    "hwp_omp_tui.py",
    "hwp_setup.py",
    "hwp_setup_runtime.py",
    "hwp_setup_ui.py",
)
RULE_FILE = "docs/hwp-agent-runtime.md"


def _file_hash(path):
    digest = hashlib.sha256()
    if path.is_symlink() or not path.is_file():
        raise ValueError("Report runtime input must be a regular file")
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _user_sid():
    """Read the process token's real user SID, not an environment/user-name hint."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    security.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    security.ConvertSidToStringSidW.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not security.OpenProcessToken(
        kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if ctypes.get_last_error() != 122 or not 0 < size.value <= 65536:
            raise ValueError("Report runtime cannot read the local user token")
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER begins with SID_AND_ATTRIBUTES, whose first field is PSID.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        value = wintypes.LPWSTR()
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return value.value
        finally:
            kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


def _windows_scope():
    if sys.platform != "win32":
        raise ValueError("Frozen report projects require Windows device/user identity")
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Cryptography",
        0,
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
    ) as key:
        machine, kind = winreg.QueryValueEx(key, "MachineGuid")
    if kind != winreg.REG_SZ or not isinstance(machine, str):
        raise ValueError("Report runtime requires the existing Windows MachineGuid")
    try:
        guid = UUID(machine)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Report runtime requires a valid Windows MachineGuid") from exc
    if not guid.int:
        raise ValueError("Report runtime requires a nonempty Windows MachineGuid")
    sid = _user_sid()
    if not isinstance(sid, str) or not sid.startswith("S-1-"):
        raise ValueError("Report runtime requires a valid local user SID")
    return str(guid), sid


def identity():
    """Capture current bytes once; no cache, source fallback or public identifiers.

    The returned local identity is for bound project data only, not release
    manifests or model context. Frozen modules are certified by the actual EXE
    bytes; their PYZ ``__file__`` values are deliberately never opened.
    """
    try:
        result = {
            "contract": CONTRACT,
            "origin": "frozen" if resources.frozen() else "source",
            "app_version": resources.APP_VERSION,
            "rules_sha256": {
                RULE_FILE: _file_hash(resources.resource_root() / RULE_FILE)
            },
        }
        if result["origin"] == "source":
            scripts = Path(__file__).resolve(strict=True).parent
            paths = {"scripts/" + name: scripts / name for name in SOURCE_FILES}
            paths.update({name: scripts.parent / name for name in NATIVE_SOURCE_FILES})
            hashes = {name: _file_hash(path) for name, path in paths.items()}
            # Bind every named input without repeating the growing inventory in
            # each 8KiB project marker. Build/source manifests retain full maps.
            result["source_sha256"] = hashlib.sha256(json.dumps(
                hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
        else:
            executable = Path(sys.executable)
            machine, user = _windows_scope()
            scope = json.dumps(
                {"contract": CONTRACT, "machine_guid": machine, "user_sid": user},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            result["executable_sha256"] = _file_hash(executable)
            result["local_scope_sha256"] = hashlib.sha256(scope).hexdigest()
        return result
    except (OSError, RuntimeError) as exc:
        raise ValueError(
            "Report runtime identity is unavailable; preserve this project"
        ) from exc
