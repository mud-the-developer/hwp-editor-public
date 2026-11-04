"""Required missing-only Windows preparation; importing and inspecting never installs.

The closed bundled manifest nominates reviewed official release assets only.
No detected PowerShell executable is ever run. Failures retain the owned run,
including downloads, notices, metadata/installer logs and a partial receipt.
"""
from base64 import b64encode
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import struct
import subprocess
import unicodedata
import urllib.parse
import urllib.request
import uuid
import zipfile

from hwp_resources import resource_root
from hwp_setup_runtime import RUNTIME_ASSETS

CONTRACT = "hwp-windows-dependencies-v2"
COMPONENT_IDS = ("powershell7", "d2coding_propo", "dotnet10", "webview2", "omp_runtime")
_ASSETS = {
    "powershell7": {
        "name": "PowerShell 7", "version": "7.6.9", "kind": "msi",
        "url": "https://github.com/PowerShell/PowerShell/releases/download/v7.6.9/PowerShell-7.6.9-win-x64.msi",
        "size": 117493760,
        "sha256": "50d33c7494aaf82b987cfd4de3f232d04f91fdd4c0deeca8a669cced3e7cf8f9",
        "release_url": "https://github.com/PowerShell/PowerShell/releases/tag/v7.6.9",
    },
    "d2coding_propo": {
        "name": "D2Coding Nerd Font Propo", "version": "3.5.1", "kind": "font_zip",
        "url": "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.5.1/D2Coding.zip",
        "size": 20845325,
        "sha256": "8d1597b1537afdc8f2f7c75036a8224bc837e6c248062b80013a13258258541d",
        "release_url": "https://github.com/ryanoasis/nerd-fonts/releases/tag/v3.5.1",
    },
    **RUNTIME_ASSETS,
}
_FONT_REGISTRY = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
_RELEASE_HOSTS = frozenset(("github.com", "release-assets.githubusercontent.com",
                            "objects.githubusercontent.com", "github-releases.githubusercontent.com",
                            "download.microsoft.com", "builds.dotnet.microsoft.com",
                            "msedge.sf.dl.delivery.mp.microsoft.com", "registry.npmjs.org"))
_CHUNK = 1024 * 1024
_SIGNATURE_COMMAND = r"""$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) { throw 'Windows PowerShell 5.1 required' }
Microsoft.PowerShell.Core\Import-Module -Name ($PSHOME + '\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$s = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $env:HWP_SETUP_MSI -ErrorAction Stop
if ($s.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or $null -eq $s.SignerCertificate) { throw 'Authenticode trust failed' }
# A product's certificate CN (for example .NET) is not its publisher organization.
if ($s.SignerCertificate.Subject -cnotmatch '(^|,\s*)O=Microsoft Corporation(,|$)') { throw 'Microsoft publisher required' }
[Console]::Out.WriteLine('MICROSOFT-AUTHENTICODE-VALID')
"""


class SetupError(RuntimeError):
    """First failure; receipt and run_directory identify retained evidence."""

    def __init__(self, message, *, receipt=None):
        super().__init__(message)
        self.receipt = deepcopy(receipt) if receipt is not None else None
        self.run_directory = receipt.get("run_directory") if receipt is not None else None


class SetupCancelled(SetupError):
    """The interactive MSI was cancelled; later missing components are not run."""


def _unlinked(path):
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        if os.path.lexists(part):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("설치 경로에 심볼릭 링크나 재분석 지점을 사용할 수 없습니다.")
    return path


def _regular(path, limit=None):
    path = _unlinked(path)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (limit is not None and info.st_size > limit)):
        raise ValueError("설치 입력은 크기가 제한된 실제 일반 파일이어야 합니다.")
    return path


def _directory(path):
    path = _unlinked(path)
    path.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(_unlinked(path).stat().st_mode):
        raise ValueError("설치 저장 위치는 실제 폴더여야 합니다.")
    return path


@contextmanager
def _locked_read(path):
    """Reserve the exact regular file against replacement/writes while consumed."""
    path = _regular(path)
    before = path.stat()
    if os.name != "nt":
        stream = os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")
    else:
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        # Read sharing only: signature inspection/Windows Installer can read;
        # nobody can write or replace the verified payload while it is in use.
        handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            final = kernel.GetFinalPathNameByHandleW
            final.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
            final.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            count = final(handle, buffer, len(buffer), 0)
            if not 0 < count < len(buffer):
                raise ValueError("설치 파일의 최종 경로를 확인할 수 없습니다.")
            actual = buffer.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            if os.path.normcase(actual) != os.path.normcase(str(path)):
                raise ValueError("설치 파일의 최종 경로가 변경되었습니다.")
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close(handle)
            raise
        stream = os.fdopen(descriptor, "rb")
    with stream:
        info = os.fstat(stream.fileno())
        if ((before.st_dev, before.st_ino, before.st_size) != (info.st_dev, info.st_ino, info.st_size)
                or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("설치 입력 파일이 변경되었습니다.")
        yield stream


def _new_file(path):
    path = _unlinked(path)
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600), "wb")


def _write_new(path, value):
    with _new_file(path) as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("설치 명세에 중복 JSON 키가 있습니다.")
        value[key] = item
    return value


def _safe_member(name):
    if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name:
        raise ValueError("안전하지 않은 ZIP 파일 이름입니다.")
    bare = name[:-1] if name.endswith("/") else name
    parts = bare.split("/")
    if (not bare or PurePosixPath(bare).is_absolute()
            or any(part in ("", ".", "..") or part.endswith((".", " "))
                   or any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part)
                   or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                   for part in parts)):
        raise ValueError("안전하지 않은 ZIP 파일 경로입니다.")
    return bare


def _member_pin(row, *, font=False):
    expected = {"member", "size", "sha256"} | ({"registry_name"} if font else set())
    if (not isinstance(row, dict) or set(row) != expected
            or type(row["size"]) is not int or not 0 < row["size"] <= 64 * _CHUNK
            or not isinstance(row["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None):
        raise ValueError("글꼴 구성원 검증 명세가 올바르지 않습니다.")
    _safe_member(row["member"])
    if row["member"].endswith("/"):
        raise ValueError("글꼴 구성원은 폴더가 아닌 파일이어야 합니다.")
    if font:
        filename = PurePosixPath(row["member"]).name
        match = re.fullmatch(r"D2KodingLigatureNerdFontPropo-(Regular|Bold)\.ttf", filename)
        expected_name = "D2KodingLigature Nerd Font Propo" + (
            " Bold" if match is not None and match[1] == "Bold" else "") + " (TrueType)"
        if match is None or row["registry_name"] != expected_name:
            raise ValueError("정확한 D2KodingLigature Propo Regular/Bold 글꼴만 허용됩니다.")
    elif row["size"] > _CHUNK or not re.search(r"(?i)(license|notice|ofl|readme)", PurePosixPath(row["member"]).name):
        raise ValueError("글꼴 원문 고지 파일 명세가 올바르지 않습니다.")


def _validate_manifest(value):
    if (not isinstance(value, dict) or set(value) != {"contract", "components", "license_files_sha256"}
            or value["contract"] != CONTRACT or not isinstance(value["components"], list)
            or len(value["components"]) != len(COMPONENT_IDS)):
        raise ValueError("필수 준비 명세 형식이 올바르지 않습니다.")
    seen = set()
    for row in value["components"]:
        if not isinstance(row, dict) or row.get("id") not in COMPONENT_IDS or row["id"] in seen:
            raise ValueError("필수 준비 구성원이 누락·중복되었거나 지원 범위와 다릅니다.")
        identifier = row["id"]
        seen.add(identifier)
        keys = {"id", "license_files", *_ASSETS[identifier]}
        if identifier == "d2coding_propo":
            keys.update(("font_family", "font_files", "notice_files"))
        if set(row) != keys or any(row.get(key) != item for key, item in _ASSETS[identifier].items()):
            raise ValueError("공식 설치 자산의 고정 URL·버전·크기·해시가 일치하지 않습니다.")
        if type(row["size"]) is not int:
            raise ValueError("설치 자산 크기는 정수여야 합니다.")
        licenses = row["license_files"]
        if (not isinstance(licenses, list) or not licenses or len(licenses) > 32
                or any(not isinstance(name, str) for name in licenses)
                or len(set(licenses)) != len(licenses)):
            raise ValueError("동봉 라이선스 명세가 올바르지 않습니다.")
        for name in licenses:
            _safe_member(name)
            if not name.startswith("packaging/licenses/") or name.endswith("/"):
                raise ValueError("라이선스는 동봉 packaging/licenses 파일이어야 합니다.")
        if identifier == "d2coding_propo":
            if (row["font_family"] != "D2KodingLigature Nerd Font Propo"
                    or not isinstance(row["font_files"], list) or len(row["font_files"]) != 2
                    or not isinstance(row["notice_files"], list) or not 1 <= len(row["notice_files"]) <= 8):
                raise ValueError("Propo 글꼴 두 개와 원문 고지가 필요합니다.")
            for member in row["font_files"]:
                _member_pin(member, font=True)
            for member in row["notice_files"]:
                _member_pin(member)
            members = row["font_files"] + row["notice_files"]
            names = [unicodedata.normalize("NFC", member["member"]).casefold() for member in members]
            if len(set(names)) != len(names):
                raise ValueError("글꼴 ZIP 구성원 이름이 중복됩니다.")
            fonts = row["font_files"]
            if ({PurePosixPath(member["member"]).stem.rsplit("-", 1)[-1] for member in fonts}
                    != {"Regular", "Bold"}
                    or len({member["registry_name"].casefold() for member in fonts}) != 2):
                raise ValueError("Regular/Bold 글꼴 이름이 중복되거나 누락되었습니다.")
    license_names = {name for component in value["components"] for name in component["license_files"]}
    license_pins = value["license_files_sha256"]
    if (not isinstance(license_pins, dict) or set(license_pins) != license_names
            or any(not isinstance(pin, str) or re.fullmatch(r"[0-9a-f]{64}", pin) is None
                   for pin in license_pins.values())):
        raise ValueError("동봉 라이선스 SHA256 목록이 정확한 파일 목록과 일치하지 않습니다.")
    root = resource_root()
    for name in sorted(license_names):
        path = _regular(root / name, _CHUNK)
        with _locked_read(path) as stream:
            raw = stream.read(_CHUNK + 1)
        if len(raw) > _CHUNK or hashlib.sha256(raw).hexdigest() != license_pins[name]:
            raise ValueError("동봉 원문 라이선스의 SHA256이 일치하지 않습니다.")
    return deepcopy(value)


def load_manifest(path=None):
    """Read/validate the closed manifest; never download, launch or create paths."""
    path = Path(path) if path is not None else resource_root() / "packaging/windows-dependencies.json"
    _regular(path, 65536)
    with _locked_read(path) as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("필수 준비 명세가 너무 큽니다.")
    return _validate_manifest(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object))


def _manifest(manifest):
    return load_manifest() if manifest is None else _validate_manifest(manifest)


def _windows_x64():
    if os.name != "nt" or struct.calcsize("P") != 8 or platform.machine().lower() not in ("amd64", "x86_64"):
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    check = kernel.IsWow64Process2
    check.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT))
    check.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    process, native = wintypes.USHORT(), wintypes.USHORT()
    if not check(kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
        raise ctypes.WinError(ctypes.get_last_error())
    return native.value == 0x8664 and process.value in (0, 0x8664)


def _local_app_data():
    import ctypes
    from ctypes import wintypes

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    get = shell.SHGetKnownFolderPath
    get.argtypes = (ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p))
    get.restype = ctypes.c_long
    identifier = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le)
    result = ctypes.c_wchar_p()
    code = get(ctypes.byref(identifier), 0, None, ctypes.byref(result))
    if code != 0:
        raise OSError(f"LocalAppData 조회 실패: {code:#x}")
    try:
        return _unlinked(result.value)
    finally:
        ole = ctypes.WinDLL("ole32")
        ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
        ole.CoTaskMemFree(result)


def _system_directory():
    import ctypes
    from ctypes import wintypes

    get = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemDirectoryW
    get.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    get.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    count = get(buffer, len(buffer))
    if not 0 < count < len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return _unlinked(buffer.value)


def _system_executable(path):
    # Windows servicing legitimately hardlinks protected system executables to
    # WinSxS. That exception never applies to owned downloads/staged font files.
    path = _unlinked(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Windows 시스템 실행 파일을 확인할 수 없습니다.")
    return path


def _exe_major(path):
    """File version resource only; never execute a candidate pwsh."""
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.POINTER(wintypes.UINT))
    version.VerQueryValueW.restype = wintypes.BOOL
    unused = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    if not 0 < size <= _CHUNK:
        return None
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, data):
        return None
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        return None
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    return words[2] >> 16 if words[0] == 0xFEEF04BD else None


def _registry_values(hive, key, view=0):
    import winreg

    try:
        handle = winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view)
    except FileNotFoundError:
        return []
    with handle:
        count = winreg.QueryInfoKey(handle)[1]
        return [winreg.EnumValue(handle, index) for index in range(count)]


def _registry_subkeys(hive, key, view=0):
    import winreg

    try:
        handle = winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view)
    except FileNotFoundError:
        return []
    with handle:
        return [winreg.EnumKey(handle, index) for index in range(winreg.QueryInfoKey(handle)[0])]


def _powershell_status(component):
    import winreg

    candidates = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            root = r"SOFTWARE\Microsoft\PowerShellCore\InstalledVersions"
            for child in _registry_subkeys(hive, root, view):
                key = root + "\\" + child
                values = {name.casefold(): value for name, value, _ in _registry_values(hive, key, view)}
                location, version = values.get("installlocation"), values.get("semanticversion", "")
                if isinstance(version, str) and re.match(r"^7\.", version):
                    return {"installed": True, "detail": f"기존 PowerShell {version} 등록을 유지합니다. 업그레이드하지 않습니다."}
                if isinstance(location, str):
                    candidates.append(Path(location) / "pwsh.exe")
            uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
            for child in _registry_subkeys(hive, uninstall, view):
                values = {name.casefold(): value for name, value, _ in
                          _registry_values(hive, uninstall + "\\" + child, view)}
                name, version = values.get("displayname", ""), values.get("displayversion", "")
                if (isinstance(name, str) and re.match(r"^PowerShell(?: 7|_7|\s+-\s+7)", name)
                        and isinstance(version, str) and re.match(r"^7\.", version)):
                    return {"installed": True, "detail": f"기존 {name} 등록을 유지합니다. 업그레이드하지 않습니다."}
    packages = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
    for name in _registry_subkeys(winreg.HKEY_CURRENT_USER, packages, winreg.KEY_WOW64_64KEY):
        if re.fullmatch(r"Microsoft\.PowerShell(?:Preview)?_7\.[^\\]+__8wekyb3d8bbwe", name, re.IGNORECASE):
            return {"installed": True, "detail": "기존 Microsoft Store PowerShell 7 등록을 유지합니다. 업그레이드하지 않습니다."}
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            candidates.extend(Path(os.environ[variable]) / "PowerShell" / name / "pwsh.exe"
                              for name in ("7", "7-preview"))
    candidates.extend(Path(entry) / "pwsh.exe" for entry in os.get_exec_path() if entry)
    for path in dict.fromkeys(candidates):
        if path.is_file() and _exe_major(path) == 7:
            return {"installed": True, "detail": f"기존 PowerShell 7 파일을 유지합니다: {path} (실행하지 않음)"}
    return {"installed": False, "detail": "기존 PowerShell 7을 찾지 못했습니다. Windows PowerShell 5.1은 유지됩니다."}


def _font_family_available(family):
    """GDI font enumeration reads installed metadata; it never adds a font."""
    import ctypes
    from ctypes import wintypes

    class LogFont(ctypes.Structure):
        _fields_ = [(name, wintypes.LONG) for name in ("height", "width", "escapement", "orientation", "weight")] + [
            (name, wintypes.BYTE) for name in ("italic", "underline", "strikeout", "charset", "outprecision",
                                             "clipprecision", "quality", "pitchandfamily")
        ] + [("face", wintypes.WCHAR * 32)]

    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetDC.argtypes = (wintypes.HWND,)
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user.ReleaseDC.restype = ctypes.c_int
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.LPARAM)
    found = False

    def receive(logfont, metric, kind, parameter):
        nonlocal found
        actual = ctypes.cast(logfont, ctypes.POINTER(LogFont)).contents.face
        # LOGFONT has only 31 characters plus NUL; the original family has 32.
        if actual.casefold() in (family.casefold(), family[:31].casefold()):
            found = True
            return 0
        return 1

    callback = callback_type(receive)
    request = LogFont()
    request.charset = 1  # DEFAULT_CHARSET: all installed charsets, not ANSI-only.
    request.face = ""  # Enumerate instead of truncating a long family's lookup.
    enum = gdi.EnumFontFamiliesExW
    enum.argtypes = (wintypes.HDC, ctypes.POINTER(LogFont), callback_type, wintypes.LPARAM, wintypes.DWORD)
    enum.restype = ctypes.c_int
    dc = user.GetDC(None)
    if not dc:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        enum(dc, ctypes.byref(request), callback, 0, 0)
    finally:
        user.ReleaseDC(None, dc)
    return found


def _font_status(component):
    import winreg

    family = component["font_family"]
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for name, value, kind in _registry_values(hive, _FONT_REGISTRY, winreg.KEY_WOW64_64KEY):
            if name.casefold().startswith(family.casefold() + " "):
                return {"installed": True, "detail": f"기존 {family} 등록을 유지합니다. 덮어쓰지 않습니다."}
    if _font_family_available(family):
        return {"installed": True, "detail": f"기존 {family} 글꼴을 유지합니다. 덮어쓰지 않습니다."}
    return {"installed": False, "detail": f"{family}가 등록되어 있지 않습니다. 문서 글꼴은 변경하지 않습니다."}


def _component_status(component):
    if component["id"] == "powershell7":
        return _powershell_status(component)
    if component["id"] == "d2coding_propo":
        return _font_status(component)
    from hwp_setup_runtime import component_status
    return component_status(component)


def installed_status(manifest=None):
    """Read-only Windows x64 metadata/registry/font inspection, no process launch."""
    value = _manifest(manifest)
    if not _windows_x64():
        return {identifier: {"installed": False, "detail": "필수 준비는 Windows x64에서만 지원됩니다."}
                for identifier in COMPONENT_IDS}
    return {row["id"]: _component_status(row) for row in value["components"]}


def write_status_report(destination):
    """Write one explicit new JSON report; detection never installs or launches."""
    target = Path(destination)
    if (not target.is_absolute() or target.suffix.lower() != ".json"
            or str(target).startswith("\\\\")
            or (os.name == "nt" and ":" in str(target)[2:])):
        raise ValueError("설치 상태 보고서는 로컬 절대 경로의 새 JSON 파일이어야 합니다.")
    target = _unlinked(target)
    if not target.parent.is_dir():
        raise ValueError("설치 상태 보고서의 실제 상위 폴더가 필요합니다.")
    if os.path.lexists(target):
        raise FileExistsError("기존 설치 상태 보고서를 덮어쓰지 않습니다.")
    report = {"contract": "hwp-requirements-status-v1", "ready": False,
              "components": {}, "reboot_required": False}
    try:
        statuses = installed_status()
        report["components"] = statuses
        report["ready"] = all(item["installed"] is True for item in statuses.values())
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    _write_new(target, _json_bytes(report))
    return 1 if "error" in report else 0 if report["ready"] else 2


def _release_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in _RELEASE_HOSTS or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or any(ord(character) < 32 or character == "\\" for character in url)):
        raise ValueError("검토한 공식 HTTPS 배포처의 다운로드 경로만 허용됩니다.")
    return parsed


class _ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, original):
        super().__init__()
        self.original = original
        self.redirects = 0

    def redirect_request(self, request, response, code, message, headers, newurl):
        parsed = _release_url(newurl)
        self.redirects += 1
        if self.redirects > 4 or (parsed.hostname == "github.com" and newurl != self.original):
            raise ValueError("공식 다운로드 리디렉션 허용 범위를 벗어났습니다.")
        return super().redirect_request(request, response, code, message, headers, newurl)


def _download(component, destination):
    _release_url(component["url"])
    opener = urllib.request.build_opener(_ReleaseRedirect(component["url"]))
    request = urllib.request.Request(component["url"], headers={"User-Agent": "HwpReview-Setup/2", "Accept-Encoding": "identity"})
    digest, count = hashlib.sha256(), 0
    with _new_file(destination) as target:
        with opener.open(request, timeout=30) as response:
            _release_url(response.geturl())
            if response.status != 200:
                raise ValueError("다운로드 응답이 정상적인 전체 파일이 아닙니다.")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) != component["size"]):
                raise ValueError("다운로드 파일 크기 고정값이 일치하지 않습니다.")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("압축 전송된 설치 파일은 허용되지 않습니다.")
            while True:
                block = response.read(min(_CHUNK, component["size"] - count + 1))
                if not block:
                    break
                target.write(block)
                digest.update(block)
                count += len(block)
                if count > component["size"]:
                    raise ValueError("다운로드가 고정 파일 크기를 초과했습니다. 받은 증거를 보존합니다.")
        target.flush()
        os.fsync(target.fileno())
    if count != component["size"] or digest.hexdigest() != component["sha256"]:
        raise ValueError("다운로드 크기 또는 SHA256이 일치하지 않습니다. 파일을 실행하지 않습니다.")


def _verify_stream(stream, pin):
    stream.seek(0)
    digest, count = hashlib.sha256(), 0
    for block in iter(lambda: stream.read(_CHUNK), b""):
        count += len(block)
        if count > pin["size"]:
            raise ValueError("검증 후 설치 파일 크기가 변경되었습니다.")
        digest.update(block)
    if count != pin["size"] or digest.hexdigest() != pin["sha256"]:
        raise ValueError("설치 직전 SHA256 검증에 실패했습니다.")
    stream.seek(0)


def _child_environment(system):
    # No ambient PowerShell modules, profile hooks, execution-policy variables or
    # executable search path. The one command is static and its input is data.
    windows = system.parent
    return {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
            "PATH": str(system), "PSModulePath": str(system / "WindowsPowerShell/v1.0/Modules")}


def _verify_microsoft_signature(path, run, record):
    system = _system_directory()
    powershell = _system_executable(system / "WindowsPowerShell/v1.0/powershell.exe")
    environment = _child_environment(system)
    environment["HWP_SETUP_MSI"] = str(path)
    environment["TEMP"] = environment["TMP"] = str(run)
    arguments = [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                 b64encode(_SIGNATURE_COMMAND.encode("utf-16le")).decode("ascii")]
    with _new_file(run / "signature.stdout.txt") as stdout, _new_file(run / "signature.stderr.txt") as stderr:
        process = subprocess.Popen(arguments, executable=str(powershell), cwd=run, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        record["signature_process_id"] = process.pid
        # No timeout/communicate helper that could terminate an uncertain child.
        code = process.wait()
        record["signature_exit_code"] = code
    if code != 0 or (run / "signature.stdout.txt").read_bytes().strip() != b"MICROSOFT-AUTHENTICODE-VALID":
        raise ValueError("Microsoft Authenticode 서명 검증에 실패했습니다. 서명 로그를 보존합니다.")
    record["signature"] = "Microsoft Corporation; Windows Authenticode Valid"


def _install_msi(path, run, record):
    system = _system_directory()
    executable = _system_executable(system / "msiexec.exe")
    log = _unlinked(run / "powershell7-msi.log")
    if os.path.lexists(log):
        raise FileExistsError("MSI 로그 경로가 이미 존재합니다.")
    record["installer_log"] = str(log)
    record["windows_powershell_5_1"] = "retained"
    record["policy"] = {"interactive": True, "automatic_reboot": False, "add_path": False,
                        "enable_remoting": False, "enable_microsoft_update": False}
    arguments = [str(executable), "/i", str(path), "/norestart", "/L*v", str(log),
                 "ADD_PATH=0", "ENABLE_PSREMOTING=0", "ENABLE_MU=0", "USE_MU=0"]
    record["arguments"] = arguments
    process = subprocess.Popen(arguments, executable=str(executable), cwd=run, stdin=subprocess.DEVNULL)
    record["installer_process_id"] = process.pid
    code = process.wait()  # Interactive UI/UAC owns cancellation. Never terminate it.
    record["exit_code"] = code
    if code == 1602:
        raise SetupCancelled("사용자가 MSI 설치를 취소했습니다. 다음 구성원을 설치하지 않습니다.")
    if code not in (0, 3010):
        raise SetupError(f"PowerShell MSI 설치가 실패했습니다 (코드 {code}). 로그를 보존합니다.")
    return code == 3010


def _stage_fonts(path, component, run):
    selected = {row["member"]: row for row in component["font_files"] + component["notice_files"]}
    font_members = {row["member"] for row in component["font_files"]}
    staged = {}
    with _locked_read(path) as payload:
        _verify_stream(payload, component)
        with zipfile.ZipFile(payload) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= 128:
                raise ValueError("글꼴 ZIP 구성원 수가 허용 범위를 벗어났습니다.")
            seen, files, total = set(), set(), 0
            for info in entries:
                name = _safe_member(info.filename)
                normalized = unicodedata.normalize("NFC", name).casefold()
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (info.orig_filename != info.filename or normalized in seen or info.flag_bits & 1
                        or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or info.external_attr & 0x400 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or not 0 <= info.file_size <= 64 * _CHUNK
                        or (info.is_dir() and (kind == stat.S_IFREG or info.file_size != 0))
                        or (not info.is_dir() and kind == stat.S_IFDIR)):
                    raise ValueError("글꼴 ZIP에 중복·암호화·연결·과대 구성원이 있습니다.")
                seen.add(normalized)
                if not info.is_dir():
                    files.add(normalized)
                total += info.file_size
                if total > 128 * _CHUNK:
                    raise ValueError("글꼴 ZIP의 전체 해제 크기가 너무 큽니다.")
            if any("/".join(name.split("/")[:index]) in files
                   for name in seen for index in range(1, len(name.split("/")))):
                raise ValueError("글꼴 ZIP의 파일과 폴더 경로가 충돌합니다.")
            by_name = {info.filename: info for info in entries}
            if not set(selected).issubset(by_name):
                raise ValueError("글꼴 ZIP에 고정된 Propo 글꼴 또는 원문 고지가 없습니다.")
            for name, pin in selected.items():
                if by_name[name].file_size != pin["size"]:
                    raise ValueError("글꼴 ZIP 구성원 크기가 고정값과 다릅니다.")
            # Validate CRC for every entry, but write only the two Propo fonts and
            # original notices. A later bad entry cannot leave registered fonts.
            for info in entries:
                if info.is_dir():
                    continue
                pin = selected.get(info.filename)
                target = None
                if pin is not None:
                    root = "fonts" if info.filename in font_members else "notices"
                    target = run / root / info.filename
                    _directory(target.parent)
                digest, count = hashlib.sha256(), 0
                with archive.open(info, "r") as source:
                    if target is None:
                        for block in iter(lambda: source.read(_CHUNK), b""):
                            count += len(block)
                            if count > info.file_size:
                                raise ValueError("글꼴 ZIP 해제 크기가 초과되었습니다.")
                    else:
                        with _new_file(target) as output:
                            for block in iter(lambda: source.read(_CHUNK), b""):
                                count += len(block)
                                if count > pin["size"]:
                                    raise ValueError("글꼴 구성원 해제 크기가 초과되었습니다.")
                                digest.update(block)
                                output.write(block)
                if count != info.file_size:
                    raise ValueError("글꼴 ZIP 구성원이 잘렸습니다.")
                if pin is not None:
                    if digest.hexdigest() != pin["sha256"]:
                        raise ValueError("글꼴 또는 원문 고지 SHA256이 일치하지 않습니다.")
                    staged[info.filename] = target
    return staged


def _check_font_registry_absent(component):
    import winreg

    values = {name.casefold() for name, _, _ in _registry_values(winreg.HKEY_CURRENT_USER, _FONT_REGISTRY,
                                                                winreg.KEY_WOW64_64KEY)}
    if any(row["registry_name"].casefold() in values for row in component["font_files"]):
        raise FileExistsError("기존 사용자 글꼴 레지스트리 값을 덮어쓸 수 없습니다.")


def _font_targets(component):
    directory = _unlinked(_local_app_data() / "Microsoft/Windows/Fonts")
    if os.path.lexists(directory) and not directory.is_dir():
        raise FileExistsError("사용자 글꼴 경로가 실제 폴더가 아닙니다.")
    targets = [directory / PurePosixPath(row["member"]).name for row in component["font_files"]]
    for target in targets:
        if os.path.lexists(_unlinked(target)):
            raise FileExistsError(f"기존 사용자 글꼴 파일을 덮어쓸 수 없습니다: {target}")
    _check_font_registry_absent(component)
    return targets


def _set_font_registry(name, path):
    import winreg

    # Recheck each value immediately before mutation as well as the all-target
    # preflight. Never delete/replace a pre-existing registration.
    existing = {value.casefold() for value, _, _ in _registry_values(winreg.HKEY_CURRENT_USER, _FONT_REGISTRY,
                                                                    winreg.KEY_WOW64_64KEY)}
    if name.casefold() in existing:
        raise FileExistsError("글꼴 등록 직전에 기존 사용자 값이 발견되었습니다.")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _FONT_REGISTRY, 0,
                           winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(path))
        winreg.FlushKey(key)


def _add_font(path):
    import ctypes
    from ctypes import wintypes

    add = ctypes.WinDLL("gdi32", use_last_error=True).AddFontResourceExW
    add.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    add.restype = ctypes.c_int
    if add(str(path), 0, None) <= 0:
        raise OSError("사용자 글꼴을 현재 Windows 세션에 등록하지 못했습니다.")


def _font_change():
    import ctypes
    from ctypes import wintypes

    send = ctypes.WinDLL("user32", use_last_error=True).SendMessageTimeoutW
    send.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
    send.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    # A notification timeout never kills or closes an unresponsive application.
    if not send(0xFFFF, 0x001D, 0, 0, 0x0002, 5000, ctypes.byref(result)):
        raise OSError("글꼴 변경 알림을 완료하지 못했습니다. 등록 증거를 보존합니다.")


def _install_fonts(component, staged, record):
    targets = _font_targets(component)  # All targets checked before first mutation.
    _directory(targets[0].parent)
    _font_targets(component)
    record["font_files"] = []
    record["registry_values"] = []
    record["session_fonts"] = []
    for row, target in zip(component["font_files"], targets):
        with _locked_read(staged[row["member"]]) as source:
            _verify_stream(source, row)
            # New-only file creation is the collision boundary, not exists()+write.
            with _new_file(target) as output:
                record["font_files"].append({"path": str(target), "status": "copying"})
                for block in iter(lambda: source.read(_CHUNK), b""):
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            record["font_files"][-1]["status"] = "copied"
    for row, target in zip(component["font_files"], targets):
        with _locked_read(target) as installed:
            _verify_stream(installed, row)
            _set_font_registry(row["registry_name"], target)
            record["registry_values"].append({"name": row["registry_name"], "path": str(target)})
            _add_font(target)
        record["session_fonts"].append(str(target))
    _font_change()
    record["detail"] = "사용자용 Propo Regular/Bold를 설치했습니다. 터미널을 새로 열거나 앱을 다시 시작하세요. 문서 글꼴은 변경하지 않았습니다."


def install_missing(*, manifest=None, download_root=None, progress=None):
    """Prepare only absent required components; no retry, rollback or child kill.

    Existing compatible metadata is preserved without upgrades. When all required
    components are present, no run or other path is created. download_root
    overrides only the run base, never installed destinations or executables.
    SetupError/SetupCancelled carries the preserved partial receipt.
    """
    value = _manifest(manifest)
    if progress is not None and not callable(progress):
        raise ValueError("진행 알림은 호출 가능한 함수여야 합니다.")
    if not _windows_x64():
        raise SetupError("필수 준비는 Windows x64에서만 지원됩니다.")
    components = {row["id"]: row for row in value["components"]}
    statuses = {identifier: _component_status(components[identifier]) for identifier in COMPONENT_IDS}
    missing = [identifier for identifier in COMPONENT_IDS if not statuses[identifier]["installed"]]
    receipt = {"run_directory": None, "components": [], "reboot_required": False,
               "windows_powershell_5_1": "retained", "automatic_reboot": False}
    if not missing:
        receipt["components"] = [{"id": identifier, "status": "skipped", "detail": statuses[identifier]["detail"]}
                                 for identifier in COMPONENT_IDS]
        return receipt
    base = _directory(Path(download_root) if download_root is not None else _local_app_data() / "HwpReview/SetupRuns")
    run = _unlinked(base / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex))
    run.mkdir(exist_ok=False)
    receipt["run_directory"] = str(run)
    current = None
    try:
        _write_new(run / "request.json", _json_bytes({"required": list(COMPONENT_IDS), "missing": missing,
                                                       "manifest": value}))
        for identifier in COMPONENT_IDS:
            component = components[identifier]
            current = {"id": identifier, "status": "checking", "detail": "필수 구성원의 설치 여부를 다시 확인합니다."}
            receipt["components"].append(current)
            status = statuses[identifier] if identifier not in missing else _component_status(component)
            if status["installed"]:
                current.update(status="skipped", detail=status["detail"])
                if progress:
                    progress(status["detail"])
                continue
            license_directory = _directory(run / "notices" / identifier)
            current["license_notices"] = []
            for name in component["license_files"]:
                with _locked_read(_regular(resource_root() / name, _CHUNK)) as stream:
                    original = stream.read(_CHUNK + 1)
                if len(original) > _CHUNK or hashlib.sha256(original).hexdigest() != value["license_files_sha256"][name]:
                    raise ValueError("설치 전에 원문 고지의 크기·SHA256이 바뀌었습니다. 설치하지 않고 기록을 보존합니다.")
                notice_path = license_directory / Path(name).name
                _write_new(notice_path, original)
                current["license_notices"].append(str(notice_path))
            if identifier == "d2coding_propo":
                _font_targets(component)
            if progress:
                progress(f"{component['name']} 공식 파일 다운로드 및 검증 중…")
            payload = run / PurePosixPath(urllib.parse.urlsplit(component["url"]).path).name
            current.update(status="downloading", download=str(payload))
            _download(component, payload)
            payloads = {"primary": payload}
            if component["kind"] == "bun_omp":
                current["downloads"] = [{"id": "primary", "path": str(payload)}]
                for asset in component["runtime_assets"]:
                    extra = run / PurePosixPath(urllib.parse.urlsplit(asset["url"]).path).name
                    current["downloads"].append({"id": asset["id"], "path": str(extra)})
                    _download(asset, extra)
                    payloads[asset["id"]] = extra
            current["status"] = "verified"
            staged = _stage_fonts(payload, component, run) if identifier == "d2coding_propo" else None
            if staged is not None:
                current["notices"] = [str(staged[row["member"]]) for row in component["notice_files"]]
            # Another installer/user may have completed setup during the download.
            status = _component_status(component)
            if status["installed"]:
                current.update(status="skipped", detail=status["detail"])
                continue
            if progress:
                progress(f"{component['name']} 필수 준비 중. 설치 창과 UAC 승인은 사용자가 직접 진행합니다.")
            current["status"] = "installing"
            reboot = False
            if identifier == "d2coding_propo":
                _install_fonts(component, staged, current)
            elif component["kind"] in ("msi", "exe"):
                with _locked_read(payload) as source:
                    _verify_stream(source, component)
                    signature_run = _directory(run / "signatures" / identifier)
                    current["signature_directory"] = str(signature_run)
                    _verify_microsoft_signature(payload, signature_run, current)
                    # Never replace an intervening compatible installation.
                    status = _component_status(component)
                    if status["installed"]:
                        current.update(status="skipped", detail=status["detail"])
                        continue
                    if identifier == "powershell7":
                        reboot = _install_msi(payload, run, current)
                    else:
                        from hwp_setup_runtime import install_component
                        reboot = install_component(component, payloads, run, current)
            else:
                from hwp_setup_runtime import install_component
                reboot = install_component(component, payloads, run, current)
            receipt["reboot_required"] |= reboot
            status = _component_status(component)
            current["metadata_confirmed"] = status["installed"]
            if not status["installed"] and not reboot:
                raise SetupError(f"{component['name']} 설치 뒤 실제 준비 상태를 확인하지 못했습니다. 성공으로 표시하지 않고 증거를 보존합니다.")
            current["detail"] = (
                f"{component['name']} 설치기가 Windows 재부팅을 요구했습니다. 자동으로 재부팅하거나 다음 항목을 실행하지 않습니다."
                if reboot else status["detail"]
            )
            current["status"] = "reboot_required" if reboot else "installed"
            if progress:
                progress(current["detail"])
            if reboot:
                break
        _write_new(run / "receipt.json", _json_bytes(receipt))
        return deepcopy(receipt)
    except BaseException as exc:
        cancelled = isinstance(exc, (SetupCancelled, KeyboardInterrupt))
        if current is not None and current["status"] not in ("installed", "skipped", "reboot_required"):
            current.update(status="cancelled" if cancelled else "failed", detail=str(exc))
        receipt["error"] = {"type": type(exc).__name__, "detail": str(exc), "cancelled": cancelled}
        # Do not remove partial copies, registrations, logs or running processes.
        # Evidence-writing errors must not mask the original first failure.
        for path, raw in ((run / "failure.txt", (type(exc).__name__ + ": " + str(exc) + "\n").encode("utf-8")),
                          (run / "receipt.json", _json_bytes(receipt))):
            try:
                _write_new(path, raw)
            except (OSError, ValueError):
                pass
        error = SetupCancelled if cancelled else SetupError
        raise error(f"필수 준비가 중단되었습니다. 다시 시도하거나 복구하지 않습니다. 증거: {run}\n{exc}",
                    receipt=receipt) from exc
