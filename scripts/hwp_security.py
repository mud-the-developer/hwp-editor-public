"""Pinned official Hancom Automation module; no network or COM creation here.

Only prerequisite setup installs the module. Native admission is read-only.
The example suppresses file-access confirmation in the owned HWP instance.
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

MODULE_ALIAS = "FilePathCheckerModuleExample"
MODULE_TYPE = "FilePathCheckDLL"
DLL_NAME = MODULE_ALIAS + ".dll"
REGISTRY_KEY = "Software\\HNC\\HwpAutomation\\Modules"
# Exact official archive/DLL; only the root DLL member may be installed.
ZIP_SHA256 = "5d87292efafd7311cba6d35e4b416ac8bfa78608a64dde1656c8cb827b051bd8"
DLL_SHA256 = "9ac5b97c47ac8aed1e8bca27a3eef39411361d8f68c262509f0c40a8f9d21bb6"
DLL_MACHINE = 0x14C
MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
SECURITY_ASSET = {
    "name": "한글 자동화 보안모듈",
    "version": "2022-08-19 (x86)",
    "kind": "hwp_security_zip",
    "url": "https://raw.githubusercontent.com/hancom-io/devcenter-archive/"
    "64aaf36475004fb86aafe990708e5ef9279c9a56/hwp-automation/"
    "%EB%B3%B4%EC%95%88%EB%AA%A8%EB%93%88(Automation).zip",
    "size": 142064,
    "sha256": ZIP_SHA256,
    "release_url": "https://developer.hancom.com/hwpautomation",
}


class UnsupportedHwpArchitecture(ValueError):
    """Recognized Hancom executable architecture unsupported by the x86 module."""


def _registry(registry):
    if registry is None:
        if os.name != "nt":
            raise RuntimeError("한글 자동화에는 Windows 한글이 필요합니다.")
        import winreg

        return winreg
    return registry


def _canonical_path(path: Path) -> Path:
    # Inspect lexical ancestry BEFORE normalization can hide a linked segment.
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("한글 보안모듈에는 절대 경로가 필요합니다.")
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("한글 보안모듈 경로에 링크나 재분석 지점을 사용할 수 없습니다.")
    # Lazy imports reuse setup's file admission without a module import cycle.
    from hwp_setup import _unlinked

    return _unlinked(_unlinked(path).resolve())


def _checked_module(path: Path) -> Path:
    from hwp_setup import _locked_read, _regular

    path = _canonical_path(path)
    _regular(path, MAX_MEMBER_BYTES)
    with _locked_read(path) as stream:
        checked_dll(stream.read(MAX_MEMBER_BYTES + 1))
    return path


def _module_info(path: Path, *, installed=False, updated=False) -> dict:
    return {
        "alias": MODULE_ALIAS,
        "dll": str(path),
        "dll_sha256": DLL_SHA256,
        "auto_installed": installed,
        "registry_updated": updated,
    }


def _registered_path(registry) -> Path | None:
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER,
            REGISTRY_KEY,
            0,
            registry.KEY_READ | registry.KEY_WOW64_32KEY,
        ) as key:
            value, kind = registry.QueryValueEx(key, MODULE_ALIAS)
    except FileNotFoundError:
        return None
    if kind != registry.REG_SZ or not isinstance(value, str) or not value:
        raise ValueError("한글 보안모듈 등록은 비어 있지 않은 REG_SZ 경로여야 합니다.")
    return _checked_module(Path(value))


def _managed_target(local_appdata=None) -> Path:
    if local_appdata is None:
        from hwp_setup import _local_app_data

        local_appdata = _local_app_data()
    base = _canonical_path(local_appdata)
    return _canonical_path(
        base / "HwpReview" / "SecurityModules" / DLL_SHA256 / DLL_NAME
    )


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
            "한글 보안모듈 DLL이 고정된 공식 파일과 다릅니다. 변경하지 않습니다."
        )
    machine, characteristics = pe_info(data)
    if machine != DLL_MACHINE or not characteristics & 0x2000:
        raise ValueError("Security module is not the authorized 32-bit DLL")
    return data


def read_package(path: Path) -> bytes:
    from hwp_setup import _locked_read, _regular

    path = _canonical_path(path)
    _regular(path, MAX_ZIP_BYTES)
    with _locked_read(path) as stream:
        raw = stream.read(MAX_ZIP_BYTES + 1)
    if len(raw) > MAX_ZIP_BYTES:
        raise ValueError("Security ZIP too large")
    if digest(raw) != ZIP_SHA256:
        raise ValueError(
            "한글 보안모듈 ZIP이 고정된 공식 배포 파일과 다릅니다. 설치하지 않습니다."
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
    # HwpObject is an out-of-process server; match Hwp bitness, not Python.
    from hwp_setup import _locked_read

    for view in (registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY):
        try:
            key = registry.OpenKey(
                registry.HKEY_CLASSES_ROOT,
                "HWPFrame.HwpObject\\CLSID",
                0,
                registry.KEY_READ | view,
            )
        except FileNotFoundError:
            continue
        try:
            with key:
                clsid, kind = registry.QueryValueEx(key, None)
            if (
                kind != registry.REG_SZ
                or not isinstance(clsid, str)
                or re.fullmatch(
                    r"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}",
                    clsid,
                ) is None
            ):
                raise ValueError("설치된 한글 Automation COM의 CLSID가 올바르지 않습니다.")
            with registry.OpenKey(
                registry.HKEY_CLASSES_ROOT,
                "CLSID\\" + clsid + "\\LocalServer32",
                0,
                registry.KEY_READ | view,
            ) as server:
                command, kind = registry.QueryValueEx(server, None)
        except FileNotFoundError as error:
            raise ValueError("설치된 한글 Automation COM 등록이 불완전합니다.") from error
        if kind != registry.REG_SZ or not isinstance(command, str):
            raise ValueError("설치된 한글 Automation 실행 명령은 REG_SZ 문자열이어야 합니다.")
        match = re.match(
            r'\s*(?:"([^"]+\.exe)"|(.+?\.exe))(?=\s|$)', command, re.IGNORECASE
        )
        if not match:
            raise ValueError("설치된 한글 Automation 실행 파일 경로를 해석할 수 없습니다.")
        path = Path(os.path.expandvars(match.group(1) or match.group(2)))
        if not path.is_absolute() or path.name.lower() != "hwp.exe":
            raise ValueError("설치된 한글 Automation 실행 파일이 없거나 예상 경로와 다릅니다.")
        try:
            path = _canonical_path(path)
            with _locked_read(path) as stream:
                machine, characteristics = pe_info(stream.read())
        except FileNotFoundError as error:
            raise ValueError("설치된 한글 Automation 실행 파일이 없습니다.") from error
        if not characteristics & 0x0002 or characteristics & 0x2000:
            raise ValueError("설치된 한글 Automation 파일이 실행 가능한 EXE 형식이 아닙니다.")
        if machine != DLL_MACHINE:
            if machine in (0x8664, 0xAA64, 0x1C0, 0x1C4, 0x200, 0xA641, 0xA64E):
                raise UnsupportedHwpArchitecture(
                    "설치된 한글의 실행 구조는 공식 x86 보안모듈과 호환되지 않습니다."
                )
            raise ValueError("설치된 한글 Automation 실행 파일의 PE 구조를 인식할 수 없습니다.")
        return path
    raise FileNotFoundError("한글 Automation COM 등록이 없습니다.")


def automation_status(*, registry=None) -> dict:
    """Inspect applicability and exact registration without changing any state."""
    registry = _registry(registry)
    # A declared but corrupt module is never hidden by a not-applicable result.
    registered = _registered_path(registry)
    try:
        hwp_executable(registry)
    except FileNotFoundError:
        return {
            "installed": False,
            "required": False,
            "detail": "한글 Automation COM이 없습니다. 순수 HWPX 사용은 가능하지만 한글 자동화는 사용할 수 없습니다.",
        }
    except UnsupportedHwpArchitecture:
        return {
            "installed": False,
            "required": False,
            "detail": "설치된 한글은 공식 x86 보안모듈이 지원하지 않는 실행 구조입니다. 한글 자동화는 사용할 수 없으며 순수 HWPX 사용은 가능합니다.",
        }
    if registered is not None:
        return {
            "installed": True,
            "required": True,
            "detail": "호환되는 한글과 공식 x86 보안모듈 등록을 확인했습니다. 기존 파일과 등록을 유지합니다.",
        }
    target = _managed_target()
    if os.path.lexists(target):
        _checked_module(target)
    return {
        "installed": False,
        "required": True,
        "detail": "한글 자동화 보안모듈이 등록되어 있지 않습니다. 필수 준비에서 사용 안내를 확인하고 명시적으로 동의한 뒤 설정하세요.",
    }


def install_package(target: Path, package: Path) -> bool:
    from hwp_setup import _directory, _new_file

    target = _canonical_path(target)
    if os.path.lexists(target):
        _checked_module(target)
        return False
    data = read_package(package)
    _directory(target.parent)
    if _canonical_path(target) != target:
        raise ValueError("한글 보안모듈 설치 경로가 변경되었습니다.")
    try:
        with _new_file(target) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        # Only identical, unlinked concurrent files are reusable; never replace.
        _checked_module(target)
        return False
    # Keep partial files on every failure; setup's receipt owns failure evidence.
    _checked_module(target)
    return True


def registered_security_module(*, registry=None) -> dict:
    """Read-only desktop preflight. NEVER install a DLL or change registry values."""
    registry = _registry(registry)
    hwp_executable(registry)
    path = _registered_path(registry)
    if path is None:
        raise RuntimeError(
            "한글 자동화 보안모듈이 등록되어 있지 않습니다. 앱의 필수 준비에서 사용 안내를 확인하고 동의한 뒤 설정하세요. 문서 작업 중에는 DLL을 설치하거나 내려받지 않습니다."
        )
    return _module_info(path)


def ensure_security_module(
    *, package: Path, registry=None, local_appdata: Path | None = None
) -> dict:
    """Install only a missing approved module; never repair declared state."""
    from hwp_setup import _locked_read, _regular

    registry = _registry(registry)
    hwp_executable(registry)
    existing = _registered_path(registry)
    if existing is not None:
        return _module_info(existing)
    target = _managed_target(local_appdata)
    installed = install_package(target, package)
    # Recheck both admission and absence after file work, before any registry write.
    hwp_executable(registry)
    if _registered_path(registry) is not None:
        raise ValueError("한글 보안모듈 등록이 설정 중 변경되었습니다. 덮어쓰지 않습니다.")
    # Hold the verified DLL read-only through registration; preserve it on failure.
    if _canonical_path(target) != target:
        raise ValueError("한글 보안모듈 등록 대상 경로가 변경되었습니다.")
    with _locked_read(target) as stream:
        checked_dll(stream.read(MAX_MEMBER_BYTES + 1))
        with registry.CreateKeyEx(
            registry.HKEY_CURRENT_USER,
            REGISTRY_KEY,
            0,
            registry.KEY_READ | registry.KEY_SET_VALUE | registry.KEY_WOW64_32KEY,
        ) as key:
            hwp_executable(registry)
            if _canonical_path(target) != target:
                raise ValueError("한글 보안모듈 등록 대상 경로가 변경되었습니다.")
            _regular(target, MAX_MEMBER_BYTES)
            try:
                registry.QueryValueEx(key, MODULE_ALIAS)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("한글 보안모듈 등록이 설정 직전 변경되었습니다. 덮어쓰지 않습니다.")
            registry.SetValueEx(key, MODULE_ALIAS, 0, registry.REG_SZ, str(target))
    if _registered_path(registry) != target:
        raise ValueError("한글 보안모듈 설정 후 등록 경로가 변경되었습니다.")
    return _module_info(target, installed=installed, updated=True)


def register_security_module(app) -> dict:
    info = registered_security_module()
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
