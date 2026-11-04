"""User-authorized Hancom Automation module, pinned to the supplied local ZIP.
No network, elevated/system installs, generic DLL loading or approval-file writes.
The supplied example allows all file access in the owned HWP instance.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import struct
import sys
from threading import RLock, local
from typing import NoReturn
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULE_ALIAS = "FilePathCheckerModuleExample"
MODULE_TYPE = "FilePathCheckDLL"
DLL_NAME = MODULE_ALIAS + ".dll"
REGISTRY_KEY = "Software\\HNC\\HwpAutomation\\Modules"
ZIP_PATH = ROOT / "보안모듈(Automation).zip"
# Exact archive/DLL supplied and explicitly authorized by this project's owner.
ZIP_SHA256 = "5d87292efafd7311cba6d35e4b416ac8bfa78608a64dde1656c8cb827b051bd8"
DLL_SHA256 = "9ac5b97c47ac8aed1e8bca27a3eef39411361d8f68c262509f0c40a8f9d21bb6"
DLL_MACHINE = 0x14C
MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pe_info(data: bytes) -> tuple[int, int]:
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("Not a Windows PE file")
    offset = struct.unpack_from("<I", data, 60)[0]
    if offset < 64 or offset + 24 > len(data) or data[offset : offset + 4] != b"PE\0\0":
        raise ValueError("Invalid PE header")
    return struct.unpack_from("<H", data, offset + 4)[0], struct.unpack_from(
        "<H", data, offset + 22
    )[0]


def checked_dll(data: bytes) -> bytes:
    if len(data) > MAX_MEMBER_BYTES or digest(data) != DLL_SHA256:
        raise ValueError(
            "Security DLL differs from the explicitly authorized module; stopped"
        )
    machine, characteristics = pe_info(data)
    if machine != DLL_MACHINE or not characteristics & 0x2000:
        raise ValueError("Security module is not the authorized 32-bit DLL")
    return data


def read_package(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(
            "Security DLL missing and local approved ZIP not found: " + str(path)
        )
    if path.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("Security ZIP too large")
    raw = path.read_bytes()
    if digest(raw) != ZIP_SHA256:
        raise ValueError(
            "Security ZIP differs from the owner-authorized archive; stopped"
        )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) > 64 or sum(i.file_size for i in members) > 8 * 1024 * 1024:
            raise ValueError("Security ZIP exceeds bounded package size")
        seen, candidates = set(), []
        for member in members:
            name = member.filename.replace("\\", "/")
            parts = name.rstrip("/").split("/")
            mode = member.external_attr >> 16
            if (
                PurePosixPath(name).is_absolute()
                or PureWindowsPath(name).drive
                or any(p in ("", ".", "..") for p in parts)
                or stat.S_ISLNK(mode)
                or member.flag_bits & 1
                or member.file_size > MAX_MEMBER_BYTES
                or member.compress_type
                not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            ):
                raise ValueError("Unsafe security ZIP member: " + member.filename)
            folded = name.casefold()
            if folded in seen:
                raise ValueError("Duplicate security ZIP member")
            seen.add(folded)
            if name == DLL_NAME and not member.is_dir():
                candidates.append(member)
        if len(candidates) != 1:
            raise ValueError(
                "Security ZIP must have exactly one root-level approved DLL"
            )
        # Read only the DLL. Never extract/run bundled source, scripts or executables.
        return checked_dll(archive.read(candidates[0]))


def hwp_executable(registry) -> Path:
    # HwpObject is an out-of-process server; module bitness must match Hwp, not Python.
    for view in (registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY):
        try:
            with registry.OpenKey(
                registry.HKEY_CLASSES_ROOT,
                "HWPFrame.HwpObject\\CLSID",
                0,
                registry.KEY_READ | view,
            ) as key:
                clsid = registry.QueryValue(key, None)
            with registry.OpenKey(
                registry.HKEY_CLASSES_ROOT,
                "CLSID\\" + clsid + "\\LocalServer32",
                0,
                registry.KEY_READ | view,
            ) as key:
                command = registry.QueryValue(key, None)
        except FileNotFoundError:
            continue
        match = re.match(
            r'\s*(?:"([^"]+\.exe)"|(.+?\.exe))(?=\s|$)', command, re.IGNORECASE
        )
        if not match:
            raise ValueError("Cannot parse installed Hwp COM executable")
        path = Path(os.path.expandvars(match.group(1) or match.group(2)))
        if (
            not path.is_absolute()
            or path.name.lower() != "hwp.exe"
            or not path.is_file()
        ):
            raise ValueError("Installed Hwp COM executable is missing or unexpected")
        if pe_info(path.read_bytes())[0] != DLL_MACHINE:
            raise ValueError(
                "Installed Hwp bitness differs from the supplied 32-bit security DLL"
            )
        return path
    raise FileNotFoundError("HwpObject Automation COM registration not found")


def install_package(target: Path, package: Path) -> bool:
    if target.is_symlink():
        raise ValueError("Security cache must not be a symlink")
    if target.is_file():
        checked_dll(target.read_bytes())
        return False
    data = read_package(package)
    target.parent.mkdir(parents=True, exist_ok=True)
    if any(p.is_symlink() for p in [target.parent, *target.parent.parents]):
        raise ValueError("Security install path must not cross a symlink")
    try:
        with target.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        checked_dll(
            target.read_bytes()
        )  # concurrent identical install is safe, no overwrite
        return False
    checked_dll(target.read_bytes())
    return True


def registered_security_module(*, registry=None) -> dict:
    """Read-only desktop preflight. NEVER install a DLL or change registry values."""
    if registry is None:
        if os.name != "nt":
            raise RuntimeError("문서 준비에는 Windows 한글이 필요합니다.")
        import winreg

        registry = winreg
    hwp_executable(registry)
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER,
            REGISTRY_KEY,
            0,
            registry.KEY_READ | registry.KEY_WOW64_32KEY,
        ) as key:
            value, kind = registry.QueryValueEx(key, MODULE_ALIAS)
    except FileNotFoundError as error:
        raise RuntimeError(
            "승인된 한글 보안 모듈이 등록되어 있지 않습니다. 원본 도구의 보안 모듈 설정을 먼저 완료하세요. 앱은 DLL을 설치하거나 내려받지 않습니다."
        ) from error
    if (
        kind != registry.REG_SZ
        or not isinstance(value, str)
        or not Path(value).is_absolute()
    ):
        raise ValueError("한글 보안 모듈 등록 경로가 올바르지 않습니다.")
    path = Path(value)
    checked_dll(path.read_bytes())
    return {
        "alias": MODULE_ALIAS,
        "dll": str(path),
        "dll_sha256": DLL_SHA256,
        "auto_installed": False,
        "registry_updated": False,
    }


def ensure_security_module(
    *, registry=None, local_appdata: Path | None = None, package: Path | None = None
) -> dict:
    if registry is None:
        if os.name != "nt":
            raise RuntimeError("Hancom security module requires Windows")
        import winreg

        registry = winreg
    hwp_executable(
        registry
    )  # fail before installation/registry writes on incompatible Hwp
    access = registry.KEY_READ | registry.KEY_WOW64_32KEY
    value = None
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER, REGISTRY_KEY, 0, access
        ) as key:
            value, kind = registry.QueryValueEx(key, MODULE_ALIAS)
        if kind != registry.REG_SZ or not isinstance(value, str) or not value:
            raise ValueError("Module registry value must be a nonempty REG_SZ DLL path")
    except FileNotFoundError:
        pass
    if value is not None:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Module registry path must be absolute")
        if path.is_file():
            checked_dll(path.read_bytes())
            return {
                "alias": MODULE_ALIAS,
                "dll": str(path),
                "dll_sha256": DLL_SHA256,
                "auto_installed": False,
                "registry_updated": False,
            }
    if local_appdata is None:
        location = os.environ.get("LOCALAPPDATA")
        if not location:
            raise RuntimeError(
                "LOCALAPPDATA is missing; no alternate/system install attempted"
            )
        local_appdata = Path(location)
    if not local_appdata.is_absolute():
        raise ValueError(
            "Security install requires an absolute user-local app data path"
        )
    target = local_appdata / "HwpReview" / "SecurityModules" / DLL_SHA256 / DLL_NAME
    installed = install_package(target, package or ZIP_PATH)
    # Set ONLY this declared module alias in HKCU; no HKLM/system policy/other values.
    with registry.CreateKeyEx(
        registry.HKEY_CURRENT_USER,
        REGISTRY_KEY,
        0,
        registry.KEY_SET_VALUE | registry.KEY_WOW64_32KEY,
    ) as key:
        registry.SetValueEx(key, MODULE_ALIAS, 0, registry.REG_SZ, str(target))
    return {
        "alias": MODULE_ALIAS,
        "dll": str(target),
        "dll_sha256": DLL_SHA256,
        "auto_installed": installed,
        "registry_updated": True,
    }


def register_security_module(app) -> dict:
    info = ensure_security_module()
    if not app.RegisterModule(MODULE_TYPE, MODULE_ALIAS):
        raise RuntimeError(
            "Hancom rejected the explicitly authorized FilePathCheckDLL module; no document opened"
        )
    return {**info, "registered": True}


class _CacheMutationBlocked(BaseException):
    """Do not let pywin32 catch an OSError and try another generation path."""


class _CacheState(local):
    def __init__(self):
        self.active = False
        self.roots: tuple[Path, ...] | None = None
        self.blocked = False


_cache_state = _CacheState()
_cache_lock = RLock()
_cache_hook_installed = False
_CACHE_PROBE = "hwp.automation_cache.probe"
_CACHE_MESSAGE = "한글 COM 캐시의 자동 변경을 차단했습니다. 재시도/복구하지 않습니다."
_HWP_INTERFACE = "{5E6A8276-CF1C-42B8-BCED-319548B02AF6}"
_HWP_STARTUP_INTERFACES = (
    ("IHwpObject", _HWP_INTERFACE),
    ("IXHwpWindows", "{74099719-83AB-416E-98BE-A199D1A74CDC}"),
    ("IXHwpWindow", "{D87D278F-9D76-45BE-9AA1-A93A7AA410C9}"),
    ("IXHwpDocuments", "{FBA628D5-0C64-4BBF-81DF-47FEAF65BF2B}"),
    ("IXHwpDocument", "{AEB10CD9-3A09-4752-B2AB-947137273436}"),
)
_CACHE_MUTATIONS = {
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.rename": (0, 1),
    "os.replace": (0, 1),
    "os.link": (0, 1),
    "os.symlink": (0, 1),
    "os.chmod": (0,),
    "os.truncate": (0,),
    "os.utime": (0,),
    "os.chown": (0,),
}


def _block_cache_mutation(event) -> NoReturn:
    _cache_state.blocked = True
    raise _CacheMutationBlocked(event)


def _cache_audit(event, args):
    if not _cache_state.active:
        return
    if event == _CACHE_PROBE:
        raise _CacheMutationBlocked("probe")
    if event == "open":
        flags = args[2]
        if type(flags) is not int:
            _block_cache_mutation("unknown open flags")
        if not flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        ):
            return
        paths = (args[0],)
    elif event in _CACHE_MUTATIONS:
        paths = tuple(args[index] for index in _CACHE_MUTATIONS[event])
    else:
        return
    roots = _cache_state.roots
    for path in paths:
        if roots is None:
            _block_cache_mutation(event)
        try:
            target = Path(os.fsdecode(path)).resolve()
        except (TypeError, ValueError, OSError):
            return _block_cache_mutation("unresolved mutation path")
        if roots is not None and any(target.is_relative_to(root) for root in roots):
            _block_cache_mutation(event)


@contextmanager
def automation_cache():
    """Use existing wrappers only; no generation/repair, COM creation or grant here.

    Guard synchronous Python cache writes through caller cleanup. Other worker
    output/trace paths remain writable; this is not a global cache lock/sandbox.
    """
    global _cache_hook_installed
    with _cache_lock:
        previous = (
            _cache_state.active,
            _cache_state.roots,
            sys.dont_write_bytecode,
            _cache_state.blocked,
        )
        if previous[0] and previous[3]:
            raise RuntimeError(_CACHE_MESSAGE)
        _cache_state.active, _cache_state.roots, _cache_state.blocked = (
            True,
            None,
            False,
        )
        sys.dont_write_bytecode = True
        try:
            if not _cache_hook_installed:
                sys.addaudithook(_cache_audit)
                _cache_hook_installed = True
            try:
                sys.audit(_CACHE_PROBE)
            except _CacheMutationBlocked:
                pass
            else:
                raise RuntimeError("COM 캐시 변경 차단을 확인할 수 없어 중단했습니다.")
            import win32com

            paths = [win32com.__gen_path__, *win32com.gen_py.__path__]
            if not paths or any(
                not isinstance(path, str) or not os.path.isabs(path) for path in paths
            ):
                raise RuntimeError("한글 COM 캐시 위치가 불명확해 중단했습니다.")
            roots = tuple(dict.fromkeys(Path(path).resolve() for path in paths))
            if previous[0] and previous[1] is None:
                _cache_state.roots = None
            else:
                _cache_state.roots = tuple(
                    dict.fromkeys([*(previous[1] or ()), *roots])
                )
            if "win32com.client" not in sys.modules and not hasattr(
                win32com.__loader__, "archive"
            ):
                try:
                    with (Path(win32com.__gen_path__) / "dicts.dat").open(
                        "rb"
                    ) as stream:
                        if not stream.read(1):
                            raise OSError("Empty cache index")
                except OSError as error:
                    raise RuntimeError(
                        "한글 COM 캐시가 준비되지 않았습니다. 자동 생성/복구하지 않습니다."
                    ) from error
            import win32com.client
            from win32com.client import gencache

            # Validate nested startup wrappers before DispatchEx can leave an unowned app.
            for name, iid in _HWP_STARTUP_INTERFACES:
                wrapper = gencache.GetClassForCLSID(iid)
                if _cache_state.blocked:
                    raise RuntimeError(_CACHE_MESSAGE)
                if (
                    not isinstance(wrapper, type)
                    or str(getattr(wrapper, "CLSID", "")) != iid
                ):
                    raise RuntimeError(
                        f"기존 한글 COM wrapper({name})가 없습니다. 자동 생성/복구하지 않습니다."
                    )
            yield
            if _cache_state.blocked:
                raise RuntimeError(_CACHE_MESSAGE)
        except _CacheMutationBlocked as error:
            raise RuntimeError(_CACHE_MESSAGE) from error
        finally:
            blocked = _cache_state.blocked
            (
                _cache_state.active,
                _cache_state.roots,
                sys.dont_write_bytecode,
                _cache_state.blocked,
            ) = previous
            if previous[0]:
                _cache_state.blocked = previous[3] or blocked
