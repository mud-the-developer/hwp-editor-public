"""Stage a checked release and pinned corresponding-source materials offline."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import sys
from urllib.parse import unquote, urlsplit
import zipfile

from hwp_build_source import sha256, verify_source_archive
from hwp_install_bundle import regular, verify_payload

INDEX = Path(__file__).resolve().parents[1] / "packaging/upstream-source-index.json"
MATERIALS = "SOURCE-MATERIALS.txt"
MANIFEST = "RELEASE-MANIFEST.json"
SUMS = "SHA256SUMS.txt"
HEX = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?")
DEVICE = re.compile(r"(?i:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])")
PRIVATE = re.compile(
    r"(?i)(?:\b[a-z]:[/\\]|\\|(?<![\w/#])/(?!/)(?=[^\s/])|\bfile:|"
    r"\b(?:sk-(?:proj-|ant-)?[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9_]{16,}|"
    r"github_pat_[a-z0-9_]{16,}|AKIA[A-Z0-9]{16}|eyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+)\b|"
    r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----|\bBearer\s+[a-z0-9._~-]+|"
    r"(?:^|/)(?:Users|Documents and Settings|AppData|\.ssh|\.aws|\.venv|_review|work)(?:/|$)|"
    r"(?:^|/)(?:\.env(?:\.[A-Za-z0-9_-]+)?|[^/]+\.(?:pem|pfx|p12|key)|"
    r"(?:approved-plan|lease|install-receipt|native-evidence)\.json|FilePathCheckerModuleExample\.dll)(?:$|/))"
)
PRIVATE_FIELD = re.compile(
    r"(?:^|[_-])(?:password|passwd|secrets?|credentials?|api[_-]?key|private[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|authorization|native[_-]?evidence|"
    r"receipts?|account|pid)(?:$|[_-])", re.I
)


def _fail(message):
    raise ValueError(message)


def _object(raw):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                _fail("JSON에 중복 키가 있습니다.")
            result[name] = value
        return result

    result = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(result, dict):
        _fail("JSON 객체가 필요합니다.")
    return result


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _directory(path):
    path = Path(path).absolute()
    if ".." in path.parts:
        _fail("입력 경로에 상위 폴더 참조가 있습니다.")
    for node in (path, *path.parents):
        info = node.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("일반 디렉터리만 사용할 수 있습니다. 링크와 재분석 지점은 허용되지 않습니다.")
    return path.resolve(strict=True)


def _name(name):
    if not isinstance(name, str) or any(
        not part or part in {".", ".."} or part.endswith((".", " "))
        or any(ord(ch) < 32 or ch in '\\:*?"<>|' for ch in part)
        or DEVICE.fullmatch(part.split(".")[0])
        for part in name.split("/")
    ):
        _fail("안전하지 않은 파일명이 있습니다.")
    return name


def _names(names):
    # Check prefixes too: A/x and a/y alias the same Windows directory.
    seen = {}
    files = set()
    directories = set()
    for name in names:
        parts = _name(name).split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            key = prefix.casefold()
            if seen.setdefault(key, prefix) != prefix or key in files:
                _fail("대소문자를 구분하지 않는 파일명 충돌이 있습니다.")
            if i == len(parts):
                if key in directories:
                    _fail("파일과 디렉터리 이름이 충돌합니다.")
                files.add(key)
            else:
                directories.add(key)


def _zip_names(package):
    names = []
    for item in package.infolist():
        mode = stat.S_IFMT(item.external_attr >> 16)
        if (item.orig_filename != item.filename or item.is_dir()
                or item.external_attr & 0x410 or mode not in (0, stat.S_IFREG)
                or item.flag_bits & 1):
            _fail("ZIP에 일반 파일이 아닌 항목이 있습니다.")
        names.append(item.filename)
    _names(names)
    return set(names)


def _private_check(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
            if (nested is not None and type(nested) is not bool
                    and (PRIVATE_FIELD.search(normalized) or normalized.lower() in {"token", "key"})):
                _fail("설치 ZIP의 BUILD.json에 비공개 정보로 의심되는 필드가 있습니다.")
            _private_check(key)
            _private_check(nested)
    elif isinstance(value, list):
        for item in value:
            _private_check(item)
    elif isinstance(value, str):
        decoded = unquote(value)
        parsed = urlsplit(decoded) if decoded.lower().startswith(("https://", "http://")) else None
        if parsed:
            if not parsed.hostname or any((parsed.username, parsed.password, parsed.query, parsed.fragment)):
                _fail("설치 ZIP의 BUILD.json에 비공개 정보로 의심되는 URL이 있습니다.")
            decoded = parsed.path.lstrip("/")
        if PRIVATE.search(decoded):
            _fail("설치 ZIP의 BUILD.json에 비공개 경로 또는 키로 의심되는 내용이 있습니다.")


def _load_index():
    return _object(INDEX.read_bytes())


def _pins(index):
    records = index.get("artifacts")
    if (set(index) != {"contract", "scope", "artifacts"}
            or index["contract"] != "hwp-upstream-source-index-v1"
            or not isinstance(records, list) or len(records) != 21):
        _fail("고정된 상류 자료 인덱스에 정확히 21개 항목이 필요합니다.")
    pins = {}
    required = {"component", "filename", "bytes", "sha256", "origin_url", "license_scope"}
    optional = {"version", "commit", "integrity"}
    for record in records:
        if not isinstance(record, dict) or not required <= record.keys() <= required | optional:
            _fail("상류 자료 인덱스의 항목 구조가 올바르지 않습니다.")
        url = urlsplit(record["origin_url"])
        if (url.scheme != "https" or not url.hostname
                or any((url.username, url.password, url.query, url.fragment))):
            _fail("상류 자료의 공개 HTTPS 출처가 올바르지 않습니다.")
        name = _name(record["filename"])
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
                or Path(name).suffix not in {".zip", ".xz", ".gz", ".tgz", ".nupkg", ".json"}
                or name in pins or type(record["bytes"]) is not int or record["bytes"] <= 0
                or not HEX.fullmatch(record["sha256"])):
            _fail("잘못된 상류 자료 고정 항목입니다.")
        pins[name] = record
    _names(pins)
    return pins


def _check_snapshot_pins(source, pins):
    def read(name):
        return _object(source.read(f"packaging/{name}.json"))

    def match(name, **expected):
        if name not in pins or any(pins[name].get(key) != value for key, value in expected.items()):
            _fail("앱 스냅샷의 의존성 고정값이 상류 자료 인덱스와 다릅니다.")

    read_sources = read("third-party-sources")
    for component in ("pymupdf", "mupdf"):
        item = read_sources[component]
        match(item["filename"], component=component, version=item["version"], bytes=item["bytes"],
              sha256=item["sha256"], origin_url=item["url"])
    terminal = read("terminal-dependencies")
    for name in ("webview2", "xterm", "addon_fit", "unicode_graphemes"):
        item = terminal[name]
        url = item["package_url"]
        if name == "webview2":
            extra = {"sha256": item["package_sha256"]}
        else:
            extra = {"sha256": item["sha256"], "integrity": item["integrity"]}
        match(url.rsplit("/", 1)[-1], component=name, version=item["version"], origin_url=url, **extra)
    renderer = read("hwpx-renderer-dependencies")
    preferred = renderer["preferred_source_material"]
    commit, origin = preferred["source_commit"], renderer["source"]
    runtime = preferred["runtime_package"]
    match(runtime["filename"], component="rhwp npm", origin_url=renderer["tarball"],
          version=renderer["version"], bytes=runtime["bytes"], sha256=renderer["tarball_sha256"],
          integrity=renderer["integrity"], commit=renderer["git_head"])
    match(runtime["filename"], sha256=runtime["sha256"], integrity=runtime["integrity"], commit=commit)
    for name in ("core_archive", "registry_sources"):
        item = preferred[name]
        match(item["filename"], bytes=item["bytes"], sha256=item["sha256"], commit=commit, origin_url=origin)
        match(item["member_index"], sha256=item["member_index_sha256"], commit=commit, origin_url=origin)
    for name in ("RHWP-CRATE-ARCHIVES.json", "RHWP-CRATE-NOTICES.complete.json"):
        match(name, commit=commit, origin_url=origin)


def _record(path):
    path = regular(path)
    return {"bytes": path.stat().st_size, "sha256": sha256(path)}


def _copy(source, target, expected):
    with regular(source).open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
    if _record(target) != expected:
        _fail("복사본의 크기 또는 SHA-256이 원본 고정값과 다릅니다.")


def _write(path, data):
    with path.open("xb") as stream:
        stream.write(data)


def _materials(app, corresponding):
    return f"""HWPReview 대응 소스 자료

{app}: 이 후보의 정확한 앱 빌드 입력과 SHA-256 목록입니다.
{corresponding}: 같은 앱 ZIP, 상류 자료 21개 및 출처·고정값 인덱스를 담습니다.
먼저 SHA256SUMS.txt를 확인하십시오. 앱 ZIP 안의 BUILD-SOURCE.txt와 빌드 스크립트에 따라
Windows x64에서 고정 Python 의존성과 .NET SDK/Windows Desktop 참조 팩 등 별도 도구를 준비하고
python scripts/build_exe.py --candidate-only를 실행할 수 있습니다. 이 수집 도구는 빌드하거나 설치하지 않습니다.
상류 빌드 스크립트·고지와 파일별 라이선스는 해당 원본 자료를 참조하십시오.

프로젝트 라이선스는 AGPL-3.0-only입니다. §6(d) 네트워크 제공을 택한 배포자는
바이너리 옆에 정확한 대응 소스 위치를 명시하고, 모든 바이너리 수령자에게 추가 비용 없이
동등한 다운로드 접근을 유지해야 합니다. 권한 없는 비공개 링크만으로는 충분하지 않습니다.
수령자의 AGPL상 수정·재배포 권리를 추가로 제한할 수 없습니다.

이 자료는 로컬 바이트 일치 확인일 뿐, 수령자 접근·전달, 재배포 권리, 재현 빌드 또는 실제 동작을
검증하지 않습니다. WebView2 SDK 패키지는 전체 구현 소스가 아니며 Runtime 사용권과 별개입니다.
PyInstaller 원본에는 사전 빌드된 부트로더가 있습니다. xterm lockfile과 배포 JS의 재현 및
선별된 rhwp Rust 자료와 배포 WASM의 재현은 미검증이며, rhwp 목록은 실제 링크 구성 목록이 아닙니다.
원본 아카이브 자체의 빌드 자료와 파일별 고지는 보존하지만 설치된 사용자 글꼴, 한글, 보안 DLL,
사용자 문서·계정·영수증은 추가로 동봉하지 않습니다. 정확한 바이너리와의 대응, CRT/MFC 및
파일별 조건, 필요한 소스·설치 정보, 수령자 접근과 게시 허가는 별도 확인이 필요합니다.
""".encode("utf-8")


def _source_kit(target, app, upstream, pins, index, materials):
    files = {app.name: app, **{f"upstream/{name}": upstream / name for name in pins}}
    metadata = {INDEX.name: index, MATERIALS: materials}
    _names([*files, *metadata])
    with zipfile.ZipFile(target, "x") as archive:
        for name, path in sorted(files.items()):
            with regular(path).open("rb") as reader, archive.open(zipfile.ZipInfo(name), "w") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
        for name, data in sorted(metadata.items()):
            archive.writestr(zipfile.ZipInfo(name), data)
    return files, metadata


def _verify_kit(target, files, metadata, app_record, pins):
    """Verify the streamed ZIP copies without extracting any upstream archive."""
    with zipfile.ZipFile(regular(target)) as archive:
        if _zip_names(archive) != set(files) | set(metadata):
            _fail("대응 소스 ZIP 구성 목록이 다릅니다.")
        for name in files:
            expected = pins[name.split("/", 1)[1]] if name.startswith("upstream/") else app_record
            with archive.open(name) as stream:
                if (archive.getinfo(name).file_size != expected["bytes"]
                        or hashlib.file_digest(stream, "sha256").hexdigest() != expected["sha256"]):
                    _fail("대응 소스 ZIP에서 복사한 파일의 크기 또는 SHA-256이 다릅니다.")
        if any(archive.read(name) != data for name, data in metadata.items()):
            _fail("대응 소스 ZIP 안내 또는 인덱스가 변경되었습니다.")


def stage_release(candidate, upstream, output, *, commit):
    """Stage exact local bytes; commit is caller-reviewed provenance, not verified here."""
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        _fail("검토한 전체 Git 커밋 해시(16진수 40자리)가 필요합니다.")
    candidate, upstream = _directory(candidate), _directory(upstream)
    if candidate.name != "checked-release":
        _fail("정확한 checked-release 디렉터리를 지정하십시오.")
    output = Path(output).absolute()
    directory = _directory(output.parent)
    output = directory / _name(output.name)
    if output.is_relative_to(candidate) or output.is_relative_to(upstream):
        _fail("출력 폴더는 후보 및 상류 자료 폴더 밖에 있어야 합니다.")
    for path in candidate.iterdir():
        if path.name == "licenses":
            _directory(path)
        else:
            regular(path)

    index = _load_index()
    pins = _pins(index)
    observed = [path.name for path in upstream.iterdir()]
    _names(observed)
    if set(observed) != set(pins):
        _fail("상류 자료 폴더의 파일명이 고정 목록과 정확히 일치해야 합니다.")
    for name, record in pins.items():
        if _record(upstream / name) != {key: record[key] for key in ("bytes", "sha256")}:
            _fail(f"상류 자료 크기 또는 SHA-256 불일치: {name}")

    build_path = regular(candidate / "BUILD.json")
    build_bytes = build_path.read_bytes()
    build = _object(build_bytes)
    version = build.get("version")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        _fail("후보 버전이 올바르지 않습니다.")
    windows = f"HWPReview-win-x64-{version}.zip"
    app = f"HWPReview-source-{version}.zip"
    corresponding = f"HWPReview-corresponding-source-{version}.zip"
    transport = _record(candidate / windows)
    payload = verify_payload(candidate, version=version)
    if (_record(candidate / windows) != transport
            or payload["files"]["BUILD.json"]["sha256"] != hashlib.sha256(build_bytes).hexdigest()):
        _fail("검사 중 설치 후보가 변경되었습니다.")
    if build_path.read_bytes() != build_bytes:
        _fail("검사 중 BUILD.json이 변경되었습니다.")
    with zipfile.ZipFile(regular(candidate / windows)) as archive:
        _zip_names(archive)
        embedded = archive.read("BUILD.json")
        if embedded != build_bytes:
            _fail("설치 ZIP의 BUILD.json이 후보와 다릅니다.")
        _private_check(_object(embedded))
        embedded_manifest = _object(archive.read("INSTALL-MANIFEST.json"))
        if embedded_manifest != payload:
            _fail("설치 ZIP의 설치 매니페스트가 후보와 다릅니다.")
        _private_check(embedded_manifest)
    inputs = build["input_sha256"]
    if not all(isinstance(value, str) and HEX.fullmatch(value) for value in inputs.values()):
        _fail("잘못된 앱 스냅샷 해시입니다.")
    verify_source_archive(regular(candidate / app), inputs, version=version)
    with zipfile.ZipFile(regular(candidate / app)) as archive:
        _zip_names(archive)
        source_manifest = _object(archive.read("SOURCE-MANIFEST.json"))
        if set(source_manifest) != {"kind", "version", "inputs_sha256", "provenance"}:
            _fail("앱 스냅샷 매니페스트에 예상하지 않은 필드가 있습니다.")
        _private_check(source_manifest)
        _private_check(archive.read("BUILD-SOURCE.txt").decode("utf-8"))
        _check_snapshot_pins(archive, pins)
    app_record = payload["files"][app]

    output.mkdir(exist_ok=False)
    _copy(candidate / windows, output / windows, transport)
    _copy(candidate / app, output / app, app_record)
    materials = _materials(app, corresponding)
    files, metadata = _source_kit(output / corresponding, output / app, upstream, pins, _json_bytes(index), materials)
    _verify_kit(output / corresponding, files, metadata, app_record, pins)
    _write(output / MATERIALS, materials)
    manifest = {
        "contract": "hwp-local-release-materials-v1",
        "version": version,
        "reviewed_commit_provenance_only": commit.lower(),
        "executable": payload["files"]["HWPReview.exe"],
        "assets": {name: _record(output / name) for name in (windows, app, corresponding, MATERIALS)},
        "verification": "local candidate payload, exact app snapshot and pinned source copies only",
    }
    _write(output / MANIFEST, _json_bytes(manifest))
    inventory = {**manifest["assets"], MANIFEST: _record(output / MANIFEST)}
    _write(output / SUMS, "".join(f"{inventory[name]['sha256']}  {name}\n" for name in sorted(inventory)).encode("utf-8"))
    staged = [path.name for path in output.iterdir()]
    _names(staged)
    if set(staged) != set(inventory) | {SUMS}:
        _fail("출력 폴더에 예상하지 않은 파일이 있습니다.")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="검사된 후보와 고정 대응 소스를 새 로컬 폴더에 준비합니다.")
    parser.add_argument("--candidate", type=Path, required=True, help="정확한 checked-release 폴더")
    parser.add_argument("--upstream", type=Path, required=True, help="상류 자료 21개만 포함한 폴더")
    parser.add_argument("--output", type=Path, required=True, help="존재하지 않는 출력 폴더")
    parser.add_argument("--commit", required=True, help="이미 검토한 전체 커밋 해시(출처 기록용)")
    args = parser.parse_args(argv)
    try:
        stage_release(args.candidate, args.upstream, args.output, commit=args.commit)
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        # Do not echo library errors containing local paths or arbitrary input values.
        reason = str(exc) if type(exc) is ValueError and re.match(r"[가-힣]", str(exc)) else "입력 또는 출력 검사에 실패했습니다."
        print(f"대응 소스 준비 중단: {reason}", file=sys.stderr)
        return 1
    print("로컬 대응 소스 자료를 준비했습니다. 게시 허가 또는 수령자 제공을 뜻하지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
