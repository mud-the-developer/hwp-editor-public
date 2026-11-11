"""Prepare only a missing source .venv; existing environments are never repaired."""

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import venv


class SetupError(RuntimeError):
    pass


def read_requirements(root):
    raw = (root / "requirements.txt").read_bytes()
    pins = {}
    for row in raw.decode("utf-8-sig").splitlines():
        row = row.strip()
        if not row or row.startswith("#"):
            continue
        requirement, separator, marker = row.partition(";")
        if separator:
            if not re.fullmatch(r"sys_platform\s*==\s*['\"]win32['\"]", marker.strip()):
                raise SetupError("지원하지 않는 requirements 조건입니다. 자동 설치하지 않습니다.")
            if sys.platform != "win32":
                continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)", requirement.strip())
        if match is None:
            raise SetupError("requirements.txt에는 정확히 고정한 패키지 버전이 필요합니다.")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in pins:
            raise SetupError("중복된 패키지 항목입니다. 자동 설치하지 않습니다.")
        pins[name] = match[2]
    if not pins:
        raise SetupError("설치할 고정 의존성이 없습니다.")
    return raw, pins


PROBE = r'''
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve(strict=True)
if sys.version_info < (3, 11) or Path(sys.prefix).resolve() != root or sys.prefix == sys.base_prefix:
    raise SystemExit("Python3.11 이상인 해당 프로젝트 가상환경이 필요합니다.")
for name, expected in json.loads(sys.argv[2]).items():
    try:
        actual = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit("가상환경에 필요한 패키지가 없습니다: " + name)
    if actual != expected:
        raise SystemExit("가상환경 패키지 버전이 다릅니다: " + name + " " + actual + " != " + expected)
    module = {"olefile": "olefile", "pymupdf": "pymupdf", "pywin32": "win32api"}.get(name)
    if module:
        importlib.import_module(module)
'''


def check_environment(environment, pins):
    info = environment.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or environment.is_symlink():
        raise SetupError(".venv는 연결 경로가 아닌 실제 프로젝트 폴더여야 합니다.")
    python = environment / "Scripts/python.exe"
    if not python.is_file() or not (environment / "pyvenv.cfg").is_file():
        raise SetupError("기존 .venv가 불완전합니다. 덮어쓰기·재설치 없이 중단합니다.")
    subprocess.run(
        [str(python), "-I", "-B", "-X", "utf8", "-c", PROBE, str(environment), json.dumps(pins)],
        check=True, timeout=60,
    )


def prepare(root):
    root = Path(root).resolve(strict=True)
    if sys.platform != "win32" or sys.version_info < (3, 11) or sys.maxsize <= 2 ** 32:
        raise SetupError("Windows x64 Python3.11 이상이 필요합니다. 검증 기준은 Python3.14.8입니다.")
    raw, pins = read_requirements(root)
    environment = root / ".venv"
    if environment.exists() or environment.is_symlink():
        check_environment(environment, pins)
        print("기존 .venv를 확인했습니다. 패키지·설정 변경 없이 재사용합니다.", flush=True)
        return "reused"
    environment.mkdir()
    print("새 .venv를 생성하고 고정 실행 의존성을 설치합니다.", flush=True)
    venv.EnvBuilder(with_pip=True, clear=False, symlinks=False).create(environment)
    snapshot = environment / "hwp-source-requirements.txt"
    snapshot.write_bytes(raw)
    child_environment = os.environ.copy()
    child_environment["PIP_CONFIG_FILE"] = os.devnull
    subprocess.run(
        [str(environment / "Scripts/python.exe"), "-I", "-B", "-X", "utf8", "-m", "pip", "--isolated",
         "--disable-pip-version-check", "--no-input", "install", "--retries", "0", "--timeout", "30",
         "--no-cache-dir", "--only-binary=:all:", "--index-url", "https://pypi.org/simple",
         "--requirement", str(snapshot)],
        env=child_environment, check=True, timeout=600,
    )
    if (root / "requirements.txt").read_bytes() != raw:
        raise SetupError("준비 중 requirements.txt가 변경됐습니다. 환경을 보존하고 중단합니다.")
    check_environment(environment, pins)
    print("새 .venv 준비가 완료됐습니다. 실행 의존성만 설치했습니다.", flush=True)
    return "created"


def main():
    try:
        if len(sys.argv) != 1:
            raise SetupError("이 스크립트는 인수 없이 소스 저장소에서 실행합니다.")
        prepare(Path(__file__).resolve().parents[1])
    except (OSError, UnicodeError, ValueError, SetupError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            detail = f"의존성 설치/검사가 종료 코드 {error.returncode}로 실패했습니다. 위 메시지를 확인하세요."
        elif isinstance(error, subprocess.TimeoutExpired):
            detail = "의존성 설치/검사의 제한 시간을 초과했습니다."
        else:
            detail = str(error)
        print("소스 환경 준비 중단: " + detail, file=sys.stderr)
        print("기존·부분 .venv를 자동 삭제·복구·재시도하지 않습니다.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
