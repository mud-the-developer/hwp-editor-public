"""Prepare an exact source Native UI, then start only the existing desktop entry."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

# The entry is launched with -I; add only its own trusted source modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hwp_native_payload as payload
from hwp_resources import frozen
from hwp_setup import _locked_read, _new_file

PENDING_NAME = "source-native-setup.pending"
_BUILD_TIMEOUT = 2000


def _pending_error(marker: Path) -> RuntimeError:
    return RuntimeError(
        "이전 또는 진행 중인 소스 Native UI 준비 기록이 있습니다: " + str(marker)
        + "\n기록과 빌드 출력을 확인하세요. 자동 삭제·재빌드·재시도하지 않습니다."
    )


def _identity(info) -> tuple:
    return info.st_dev, info.st_ino


def _remove_owned_pending(marker: Path, content: bytes, identity: tuple) -> None:
    with _locked_read(marker) as stream:
        if _identity(os.fstat(stream.fileno())) != identity or stream.read(len(content) + 1) != content:
            raise RuntimeError("소스 Native UI 준비 기록이 변경되어 보존합니다.")
    if _identity(payload._plain_path(marker).stat()) != identity:
        raise RuntimeError("소스 Native UI 준비 기록의 소유권이 변경되어 보존합니다.")
    marker.unlink()


def _build_environment(dotnet: Path) -> dict:
    # This PATH belongs only to the Python builder child. The builder gives its
    # dotnet children an already isolated HOME/NuGet/TEMP/MSBuild environment.
    environment = os.environ.copy()
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join((
        str(dotnet.parent), str(windows / "System32"), str(windows),
    ))
    environment["PATHEXT"] = ".EXE;.COM;.BAT;.CMD"
    environment["NoDefaultCurrentDirectoryInExePath"] = "1"
    return environment


def prepare_native(source_root: Path) -> Path:
    """Reuse verified registration, or own one missing-only preparation attempt.

    A pending marker or any invalid existing registration is a hard failure.
    Failed attempts retain their marker, SDK evidence, output and builder log.
    """
    if frozen():
        raise RuntimeError("배포 실행 파일에서는 소스 Native UI를 준비할 수 없습니다.")
    root = payload._plain_path(Path(source_root), directory=True)
    build = root / "build"
    if not os.path.lexists(build):
        build.mkdir(exist_ok=False)
    build = payload._plain_path(build, directory=True)
    marker = build / PENDING_NAME
    pointer = build / "native-current.json"
    if os.path.lexists(marker):
        raise _pending_error(marker)
    if os.path.lexists(pointer):
        directory = payload._source_payload(root)
        if os.path.lexists(marker):
            raise _pending_error(marker)
        return directory

    token = uuid.uuid4().hex
    owned = build / ("source-native-" + token)
    destination = owned / "native-ui"
    content = (json.dumps({
        "contract": "hwp-source-native-setup-v1",
        "owner": token,
        "path": destination.relative_to(root).as_posix(),
    }, sort_keys=True) + "\n").encode("utf-8")
    with _new_file(marker) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
        identity = _identity(os.fstat(stream.fileno()))
    # No finally cleanup: the exclusive marker is also the failed-attempt record.
    if os.path.lexists(pointer):
        raise RuntimeError("소스 Native UI 등록이 동시에 변경되었습니다. 준비 기록을 보존합니다.")
    owned.mkdir(exist_ok=False)
    payload._plain_path(owned, directory=True)
    original = payload.source_hashes(root)

    # A valid registration never imports the SDK bootstrapper or selects a tool.
    from hwp_source_sdk import ensure_sdk

    dotnet = payload._plain_path(Path(ensure_sdk(root)))
    if dotnet.name.lower() != "dotnet.exe":
        raise ValueError("소스 빌드에는 검증된 x64 dotnet.exe가 필요합니다.")
    if payload.source_hashes(root) != original:
        raise ValueError("SDK 준비 중 소스 바이트가 변경되었습니다. 자동 재빌드하지 않습니다.")
    recipe = payload._plain_path(root / "scripts/build_native.py")
    with _new_file(owned / "builder.log") as log:
        subprocess.run(
            [sys.executable, "-E", "-s", "-B", "-X", "utf8", str(recipe), "--output", str(destination)],
            cwd=root, env=_build_environment(dotnet), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, timeout=_BUILD_TIMEOUT, check=True,
        )
    if payload.source_hashes(root) != original:
        raise ValueError("Native UI 빌드 중 소스 바이트가 변경되었습니다. 준비 기록을 보존합니다.")
    payload.verify_payload(destination, source_root=root)

    from build_native import _register_source

    _register_source(root, destination, replace=False)
    if payload._source_payload(root) != destination or payload.source_hashes(root) != original:
        raise ValueError("등록한 Native UI 또는 소스가 변경되었습니다. 준비 기록을 보존합니다.")
    _remove_owned_pending(marker, content, identity)
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="소스 Native UI만 준비하고 앱은 실행하지 않습니다.")
    args = parser.parse_args(argv)
    root = Path(os.path.abspath(__file__)).parents[1]
    try:
        print("소스 Native UI를 확인합니다. 최초 준비에는 .NET SDK 10.0.401과 로컬 빌드가 필요할 수 있습니다.", flush=True)
        directory = prepare_native(root)
        if args.prepare_only:
            print("소스 Native UI 준비 완료: " + str(directory), flush=True)
            return 0
        pythonw = payload._plain_path(root / ".venv/Scripts/pythonw.exe")
        desktop = payload._plain_path(root / "scripts/hwp_desktop.py")
        # Deliberately inherit the original app environment: the private compiler
        # is not an installed Desktop Runtime, an OMP runtime or an app PATH entry.
        subprocess.Popen([str(pythonw), "-X", "utf8", str(desktop)], cwd=root)
        return 0
    except Exception as error:
        print("소스 데스크톱 시작에 실패했습니다: " + str(error), file=sys.stderr, flush=True)
        print("기존 파일과 준비 기록을 보존합니다. 자동 복구나 재시도는 하지 않습니다.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
