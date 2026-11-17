"""Missing-only, project-owned .NET SDK preparation for source compilation.

The original pinned archive remains the authority on every cache reuse. Nothing
here installs a runtime, edits the caller's environment, or repairs partial state.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import time
import unicodedata
import urllib.request
import zipfile

from build_native import _owned_environment
from hwp_setup import (_directory, _json_bytes, _locked_read, _new_file, _regular,
                       _safe_member, _unlinked, _unique_object, _windows_x64, _write_new)

SDK_VERSION = "10.0.401"
SDK_URL = "https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.401/dotnet-sdk-10.0.401-win-x64.zip"
SDK_SIZE = 300608304
SDK_SHA512 = ("24b670ad3d923bfcf47df6c3b034152398b42f6dbc388e10d783aee1cfb5e5817d399fc0"
              "ae2a12cfa822a55e61d34830ccb15c50ef6efee437ab874bb7c79430")
_CHUNK = 1024 * 1024
_MAX_MEMBERS = 50000
_MAX_FILE = 512 * _CHUNK
_MAX_EXPANDED = 3 * 1024 * _CHUNK
_OPERATION_SECONDS = 900
_CACHE_NAME = "dotnet-sdk-" + SDK_VERSION
_ARCHIVE_NAME = _CACHE_NAME + "-win-x64.zip"


class SourceSdkError(RuntimeError):
    """First failure; existing input and newly owned partial evidence are retained."""


def _deadline(deadline):
    if time.monotonic() > deadline:
        raise SourceSdkError("SDK 준비 제한 시간을 초과했습니다. 부분 파일을 보존합니다.")


def _provenance():
    return {"version": SDK_VERSION, "architecture": "win-x64", "url": SDK_URL,
            "size": SDK_SIZE, "sha512": SDK_SHA512}


class _PinnedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        # The reviewed URL serves the complete asset directly. No alternate host,
        # path, credential, query, downgrade, or redirect loop is needed.
        raise SourceSdkError("고정 SDK 주소의 리디렉션은 허용되지 않습니다.")


def _download(destination, deadline):
    opener = urllib.request.build_opener(_PinnedRedirect())
    request = urllib.request.Request(SDK_URL, headers={
        "User-Agent": "HwpReview-Source-SDK/1", "Accept-Encoding": "identity"})
    digest, count = hashlib.sha512(), 0
    with _new_file(destination) as target:
        _deadline(deadline)
        with opener.open(request, timeout=30) as response:
            if (response.status != 200 or response.geturl() != SDK_URL
                    or response.headers.get("Content-Length") != str(SDK_SIZE)
                    or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                    or response.headers.get("Content-Range") is not None):
                raise SourceSdkError("공식 SDK 전체 파일의 주소·응답·고정 크기가 일치하지 않습니다.")
            while True:
                _deadline(deadline)
                # One socket-buffer read returns control to the wall-clock guard;
                # read(n) could otherwise keep accepting an endless slow trickle.
                block = response.read1(min(_CHUNK, SDK_SIZE - count + 1))
                if not block:
                    break
                target.write(block)
                count += len(block)
                digest.update(block)
                if count > SDK_SIZE:
                    raise SourceSdkError("SDK 다운로드가 고정 크기를 초과했습니다.")
        target.flush()
        os.fsync(target.fileno())
    if count != SDK_SIZE or digest.hexdigest() != SDK_SHA512:
        raise SourceSdkError("SDK 다운로드 크기 또는 공식 SHA512가 다릅니다. 실행하지 않습니다.")


def _verify_archive(stream, deadline):
    stream.seek(0)
    count, digest = 0, hashlib.sha512()
    while True:
        _deadline(deadline)
        block = stream.read(_CHUNK)
        if not block:
            break
        count += len(block)
        if count > SDK_SIZE:
            raise SourceSdkError("보존된 SDK ZIP의 크기가 변경되었습니다.")
        digest.update(block)
    if count != SDK_SIZE or digest.hexdigest() != SDK_SHA512:
        raise SourceSdkError("보존된 SDK ZIP의 공식 크기·SHA512가 일치하지 않습니다.")
    stream.seek(0)


def _key(name):
    return unicodedata.normalize("NFC", name).casefold()


def _entries(archive, deadline):
    entries = archive.infolist()
    if not 1 <= len(entries) <= _MAX_MEMBERS:
        raise SourceSdkError("SDK ZIP 구성원 수가 허용 범위를 벗어났습니다.")
    paths, explicit, directories, files, total = {}, set(), set(), set(), 0
    for info in entries:
        _deadline(deadline)
        name = _safe_member(info.filename)
        kind = stat.S_IFMT(info.external_attr >> 16)
        if (info.orig_filename != info.filename or _key(name) in explicit
                or info.flag_bits & 1 or info.external_attr & 0x400
                or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or not 0 <= info.file_size <= _MAX_FILE
                or (info.is_dir() and (info.file_size or kind == stat.S_IFREG))
                or (not info.is_dir() and kind == stat.S_IFDIR)):
            raise SourceSdkError("SDK ZIP에 중복·연결·암호화·과대 구성원이 있습니다.")
        parts = name.split("/")
        for part in parts:
            if re.fullmatch(r"(?i:CONIN\$|CONOUT\$|COM[¹²³]|LPT[¹²³])(?:\..*)?", part):
                raise SourceSdkError("SDK ZIP에 Windows 장치 경로가 있습니다.")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            normalized = _key(prefix)
            if normalized in paths and paths[normalized] != prefix:
                raise SourceSdkError("SDK ZIP의 대소문자·유니코드 경로가 충돌합니다.")
            paths[normalized] = prefix
            if index < len(parts) or info.is_dir():
                directories.add(prefix)
        explicit.add(_key(name))
        if not info.is_dir():
            files.add(name)
        total += info.file_size
        if total > _MAX_EXPANDED:
            raise SourceSdkError("SDK ZIP 전체 해제 크기가 허용 범위를 벗어났습니다.")
    if files & directories:
        raise SourceSdkError("SDK ZIP 파일과 폴더 경로가 충돌합니다.")
    required = {"dotnet.exe", "LICENSE.txt", "ThirdPartyNotices.txt",
                "sdk/" + SDK_VERSION + "/.version",
                "sdk/" + SDK_VERSION + "/dotnet.dll",
                "sdk/" + SDK_VERSION + "/dotnet.runtimeconfig.json"}
    if not required.issubset(files):
        raise SourceSdkError("SDK ZIP에 고정 SDK 메타데이터 또는 원문 고지가 없습니다.")
    return entries, files, directories


def _closed_tree(root, files, directories, deadline):
    actual_files, actual_directories = set(), set()
    stack = [root]
    while stack:
        _deadline(deadline)
        parent = stack.pop()
        _plain_directory(parent)
        with os.scandir(parent) as children:
            for child in children:
                path = _unlinked(Path(child.path))
                name = path.relative_to(root).as_posix()
                if child.is_dir(follow_symlinks=False):
                    if name not in directories:
                        raise SourceSdkError("SDK 캐시에 등록되지 않은 폴더가 있습니다.")
                    actual_directories.add(name)
                    stack.append(path)
                else:
                    _regular(path, _MAX_FILE)
                    if name not in files:
                        raise SourceSdkError("SDK 캐시에 등록되지 않은 파일이 있습니다.")
                    actual_files.add(name)
    if actual_files != files or actual_directories != directories:
        raise SourceSdkError("SDK 캐시의 닫힌 파일·폴더 목록이 변경되었습니다.")


def _plain_directory(path):
    path = _unlinked(path)
    if not stat.S_ISDIR(path.stat().st_mode):
        raise SourceSdkError("SDK 경로는 연결이 아닌 실제 폴더여야 합니다.")
    return path


def _consume_archive(stream, root, *, extract, deadline):
    _verify_archive(stream, deadline)
    with zipfile.ZipFile(stream) as archive:
        entries, files, directories = _entries(archive, deadline)
        if extract:
            root.mkdir(exist_ok=False)
            for name in sorted(directories, key=lambda value: (value.count("/"), value)):
                _deadline(deadline)
                _unlinked(root / name).mkdir(exist_ok=False)
        else:
            _closed_tree(root, files, directories, deadline)
        for info in entries:
            if info.is_dir():
                continue
            path = root / info.filename
            count = 0
            with archive.open(info) as original:
                with (_new_file(path) if extract else _locked_read(path)) as output:
                    if not extract and os.fstat(output.fileno()).st_size != info.file_size:
                        raise SourceSdkError("SDK 캐시 파일 크기가 원본과 다릅니다.")
                    while True:
                        _deadline(deadline)
                        block = original.read(_CHUNK)
                        if not block:
                            break
                        count += len(block)
                        if count > info.file_size:
                            raise SourceSdkError("SDK ZIP 구성원의 실제 해제 크기가 초과되었습니다.")
                        if extract:
                            output.write(block)
                        elif output.read(len(block)) != block:
                            raise SourceSdkError("SDK 캐시 파일 내용이 공식 ZIP과 다릅니다.")
                    if extract:
                        output.flush()
                        os.fsync(output.fileno())
            if count != info.file_size:
                raise SourceSdkError("SDK ZIP 구성원이 잘렸습니다.")
        _closed_tree(root, files, directories, deadline)
    _sdk_metadata(root)


def _read_small(path, limit):
    with _locked_read(_regular(path, limit)) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise SourceSdkError("SDK 메타데이터가 크기 제한을 초과했습니다.")
    return raw


def _x64_host(path):
    with _locked_read(_regular(path, 16 * _CHUNK)) as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise SourceSdkError("선택한 dotnet.exe가 정상 PE 파일이 아닙니다.")
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset <= _CHUNK:
            raise SourceSdkError("dotnet.exe PE 헤더 위치가 올바르지 않습니다.")
        stream.seek(offset)
        pe = stream.read(26)
        if (len(pe) != 26 or pe[:4] != b"PE\0\0"
                or struct.unpack_from("<H", pe, 4)[0] != 0x8664
                or struct.unpack_from("<H", pe, 24)[0] != 0x20B):
            raise SourceSdkError("Windows x64 dotnet.exe만 사용할 수 있습니다.")


def _sdk_metadata(root):
    _plain_directory(root)
    _x64_host(root / "dotnet.exe")
    sdk = _plain_directory(root / "sdk" / SDK_VERSION)
    versions = _read_small(sdk / ".version", 4096).decode("utf-8-sig").splitlines()
    if SDK_VERSION not in versions:
        raise SourceSdkError("SDK .version 메타데이터가 고정 버전과 다릅니다.")
    _regular(sdk / "dotnet.dll", _MAX_FILE)
    config = json.loads(_read_small(sdk / "dotnet.runtimeconfig.json", 65536),
                        object_pairs_hook=_unique_object)
    if not isinstance(config, dict) or config.get("runtimeOptions", {}).get("tfm") != "net10.0":
        raise SourceSdkError("SDK 런타임 메타데이터가 .NET 10과 다릅니다.")


def _installed_host():
    # Explicit roots are authoritative, including malformed/missing selections.
    for key in ("DOTNET_ROOT_X64", "DOTNET_ROOT"):
        if key in os.environ:
            value = os.environ[key]
            if not value or not Path(value).is_absolute():
                raise SourceSdkError(f"{key}는 기존 SDK의 절대 경로여야 합니다.")
            return _plain_directory(Path(value)) / "dotnet.exe"
    selected = shutil.which("dotnet.exe")
    if selected is not None:
        return Path(selected)
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")
    if program_files:
        root = _unlinked(Path(program_files) / "dotnet")
        if os.path.lexists(root):
            return _plain_directory(root) / "dotnet.exe"
    return None


def _check_installed(host):
    _x64_host(host)
    root = host.parent
    sdk = _unlinked(root / "sdk" / SDK_VERSION)
    if not os.path.lexists(sdk):
        return None  # A valid other-version/runtime-only installation is not repaired.
    _sdk_metadata(root)
    work = Path(tempfile.mkdtemp(prefix="hwp-source-sdk-probe-")).absolute()
    try:
        environment = _owned_environment(work)
        _write_new(work / "global.json", _json_bytes({"sdk": {
            "version": SDK_VERSION, "rollForward": "disable", "allowPrerelease": False}}))
        log = work / "version.log"
        with _new_file(log) as output:
            result = subprocess.run([str(host), "--version"], cwd=work, env=environment,
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=60, check=False)
        actual = _read_small(log, 65536).decode("utf-8-sig").strip()
        if result.returncode != 0 or actual != SDK_VERSION:
            raise SourceSdkError("설치된 SDK가 격리된 환경에서 고정 버전을 확인하지 못했습니다.")
    except BaseException as exc:
        raise SourceSdkError(f"설치된 SDK 확인이 중단되었습니다. 증거: {work}\n{exc}") from exc
    shutil.rmtree(work)  # Only the new successful probe; never an SDK/cache path.
    return host


def _cache_paths(root):
    base = _unlinked(root / "build/source-tools")
    return (base, base / _CACHE_NAME, base / _ARCHIVE_NAME,
            base / (_CACHE_NAME + ".pending"), base / (_CACHE_NAME + ".complete.json"))


def ensure_sdk(source_root: Path) -> Path:
    """Return a verified exact x64 compiler; missing-only, no retry or repair.

    Existing installed SDKs are trusted toolchains checked in a disposable isolated
    profile. Cached SDKs are checked byte-for-byte against the pinned ZIP without
    execution or mutation. Failure retains all newly owned evidence.
    """
    if not _windows_x64():
        raise SourceSdkError("소스 SDK 준비는 Windows x64에서만 지원됩니다.")
    root = _plain_directory(Path(source_root).absolute())
    base, cache, archive, pending, complete = _cache_paths(root)
    failure = _unlinked(base / (_CACHE_NAME + ".failure.txt"))
    if os.path.lexists(failure):
        raise SourceSdkError(f"SDK 첫 실패 기록이 남아 있습니다. 복구하지 않습니다: {failure}")
    deadline = time.monotonic() + _OPERATION_SECONDS
    artifacts = (cache, archive, pending, complete)
    present = [os.path.lexists(_unlinked(path)) for path in artifacts]
    cached = False
    if any(present):
        if present != [True, True, False, True]:
            raise SourceSdkError(f"SDK 캐시에 부분·동시 준비 상태가 있습니다. 복구하지 않습니다: {base}")
        receipt = json.loads(_read_small(complete, 4096), object_pairs_hook=_unique_object)
        if receipt != _provenance():
            raise SourceSdkError("SDK 캐시 출처 기록이 고정값과 다릅니다.")
        with _locked_read(_regular(archive, SDK_SIZE)) as stream:
            _consume_archive(stream, cache, extract=False, deadline=deadline)
        cached = True
    host = _installed_host()
    if host is not None:
        installed = _check_installed(_regular(host))
        if installed is not None:
            return installed
    if cached:
        return cache / "dotnet.exe"
    _directory(base)
    # The exclusive marker serializes first use. Every collision is a stop, never
    # a reason to delete files or retry with a different SDK/download directory.
    _write_new(pending, _json_bytes(_provenance()))
    try:
        if any(os.path.lexists(_unlinked(path)) for path in (cache, archive, complete)):
            raise SourceSdkError("SDK 준비 직전에 기존 캐시가 발견되었습니다.")
        print(f"소스 UI 빌드용 .NET SDK {SDK_VERSION}을 프로젝트 전용 폴더에 준비합니다. 공식 ZIP 크기·SHA512를 검증합니다.", flush=True)
        _download(archive, deadline)
        with _locked_read(_regular(archive, SDK_SIZE)) as stream:
            _consume_archive(stream, cache, extract=True, deadline=deadline)
        # Re-read the new extraction before marking it reusable; no executable is
        # launched during downloaded-tool preparation, even after successful CRC.
        with _locked_read(_regular(archive, SDK_SIZE)) as stream:
            _consume_archive(stream, cache, extract=False, deadline=deadline)
        if _read_small(pending, 4096) != _json_bytes(_provenance()):
            raise SourceSdkError("SDK 준비 소유권 기록이 변경되었습니다.")
        _write_new(complete, _json_bytes(_provenance()))
        _regular(pending).unlink()
        return cache / "dotnet.exe"
    except BaseException as exc:
        try:
            _write_new(base / (_CACHE_NAME + ".failure.txt"),
                       (type(exc).__name__ + ": " + str(exc) + "\n").encode("utf-8"))
        except (OSError, ValueError):
            pass
        raise SourceSdkError(f"SDK 준비가 중단되었습니다. 재시도·복구하지 않습니다. 증거: {base}\n{exc}") from exc
