"""Native OMP launch metadata and exact installed-runtime admission.

Project guidance is editable Markdown, not a filesystem security boundary.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from hwp_resources import frozen, resource_root

VERSION = "18.15.0"
BUN_VERSION = "1.4.4"
LAUNCHER_SHA256 = "a88b6c93260ec1296613d3be63c9b0173a67bf31ddc44512c74322607a86a0c1"
RUNTIME_MANIFEST = resource_root() / ("native-ui/omp-runtime.json" if frozen() else "packaging/omp-runtime.json")
RUNTIME_MANIFEST_SHA256 = "4eeada4a2dc83c5ca3596bd6ff24631a804cec23f738de3ecf9d486ee8413a2a"
BRIDGE_SOURCE = resource_root() / ("native-ui/bridge/omp-bridge.mjs" if frozen()
                                  else "native/HWPReview.Native/Bridge/omp-bridge.mjs")
BRIDGE_LIMIT = 1024 * 1024
CLI_ROOT = "install/global/node_modules/@oh-my-pi/pi-coding-agent"
NATIVES_ROOT = "install/global/node_modules/@oh-my-pi/pi-natives"
LEAF_ROOT = "install/global/node_modules/@oh-my-pi/pi-natives-win32-x64"
ADDON_NAME = "pi_natives.win32-x64-baseline.node"
ADDON_RELATIVE = LEAF_ROOT + "/" + ADDON_NAME
DIST_FILES = ("CHANGELOG-a7paazjm.md", "cli.js", "docs-index.generated.txt",
              "models.json-3tsbab79.zst", "rules.json-6b4dpgdf.zst")
NATIVE_JS_FILES = ("clipboard.js", "desktop-adapter.js", "desktop.js", "embedded-addon.js",
                   "index.js", "loader-state.js", "path.js", "vcs.js", "version-sentinel.js")
NATIVE_TYPES = tuple(name[:-3] + ".d.ts" for name in NATIVE_JS_FILES if name != "embedded-addon.js")
RUNTIME_FILES = (
    "bin/omp.exe", "bin/omp.bunx", "bin/bun.exe", CLI_ROOT + "/package.json",
    *(CLI_ROOT + "/dist/" + name for name in DIST_FILES),
    NATIVES_ROOT + "/package.json",
    *(NATIVES_ROOT + "/native/" + name for name in NATIVE_JS_FILES),
    LEAF_ROOT + "/package.json", ADDON_RELATIVE,
)
METADATA_LIMIT = 65536
RUNTIME_FILE_LIMIT = 256 * 1024 * 1024
PROFILE = "hwp-report"
PROJECT_GUIDANCE = """# HWPReview 작업 안내

일반 shell/read/write 도구와 기본 OMP 기능을 사용할 수 있습니다.
평소 대화는 자연스러운 한국어로 답하세요.
원본 문서와 서식, 양식, 연결된 리소스를 보존하세요.
승인 절차나 실행 권한을 우회하거나 조작하지 마세요.
GUI 보고서 제안은 사용자가 명시적으로 hwp-report-request-v1 요청을 제공한 경우에만
그 요청의 형식으로 작성하세요. 일반 대화에는 보고서 JSON 형식을 강요하지 마세요.
이 파일은 사용자가 편집할 수 있는 작업 안내이며 OS 보안 경계가 아닙니다.
"""
FOREIGN_KEYS = frozenset(("parentSession", "parentSessionId", "parentSessionPath",
                          "previousSessionFiles", "sessionFile", "sessionPath",
                          "sourceSessionId", "sourceSessionFile"))


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _unlinked(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if os.path.lexists(part):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("OMP 전용 저장 위치에 연결 경로를 사용할 수 없습니다.")
    return path


def _regular(path, maximum=None):
    path = _unlinked(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (maximum is not None and info.st_size > maximum):
        raise ValueError("OMP 전용 일반 파일의 크기와 소유 경로를 확인하세요.")
    return info


def _open_read(path):
    """Hold a non-reparse regular file; concurrent native appends remain possible."""
    if os.name != "nt":
        return os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")
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
    # READ | WRITE sharing, no delete sharing while this exact file is projected.
    handle = create(str(path), 0x80000000, 3, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        final = kernel.GetFinalPathNameByHandleW
        final.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
        final.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        count = final(handle, buffer, len(buffer), 0)
        if not 0 < count < len(buffer):
            raise ValueError("unresolved file")
        actual = buffer.value
        if actual.startswith("\\\\?\\UNC\\"):
            actual = "\\\\" + actual[8:]
        elif actual.startswith("\\\\?\\"):
            actual = actual[4:]
        if os.path.normcase(actual) != os.path.normcase(str(path)):
            raise ValueError("foreign final path")
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    return os.fdopen(descriptor, "rb")


def _directory(path):
    path = _unlinked(path)
    path.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(_unlinked(path).stat().st_mode):
        raise ValueError("OMP 전용 저장 위치는 실제 폴더여야 합니다.")
    return path


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("constant")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 24:
            raise ValueError("depth")
        if isinstance(item, dict):
            if FOREIGN_KEYS.intersection(item):
                raise ValueError("foreign")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    if not isinstance(value, dict):
        raise ValueError("record")
    return value





def _baseline():
    """Only the reviewed exact manifest can nominate installed runtime bytes."""
    before = _regular(RUNTIME_MANIFEST, METADATA_LIMIT)
    with _open_read(RUNTIME_MANIFEST) as stream:
        info = os.fstat(stream.fileno())
        if ((info.st_dev, info.st_ino, info.st_size) != (before.st_dev, before.st_ino, before.st_size)
                or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("OMP runtime manifest must be an unlinked regular file")
        raw = stream.read(METADATA_LIMIT + 1)
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > METADATA_LIMIT or digest != RUNTIME_MANIFEST_SHA256:
        raise ValueError("OMP runtime baseline manifest bytes changed")
    value = _json(raw.decode("utf-8"))
    if (set(value) != {"schema", "package_version", "files", "native_addon_relative_path"}
            or value["schema"] != "hwp-omp-runtime-baseline-v1"
            or value["package_version"] != VERSION
            or value["native_addon_relative_path"] != ADDON_RELATIVE
            or not isinstance(value["files"], list)
            or len(value["files"]) != len(RUNTIME_FILES)):
        raise ValueError("OMP runtime baseline contract differs")
    for row, relative in zip(value["files"], RUNTIME_FILES):
        if (not isinstance(row, dict) or set(row) != {"relative_path", "size", "sha256"}
                or row["relative_path"] != relative or type(row["size"]) is not int
                or not 0 < row["size"] <= RUNTIME_FILE_LIMIT
                or not isinstance(row["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None):
            raise ValueError("OMP runtime baseline membership differs")
    if value["files"][0]["sha256"] != LAUNCHER_SHA256:
        raise ValueError("OMP runtime launcher pin differs")
    return value, digest


def _installed_regular(path):
    """Installed-only hardlinks are allowed; native holds no-write/delete reservations.

    This exception never applies to owned answers, bridge copies or staged addons.
    Package-manager hardlinks are not reparses and cannot evade the byte pins.
    """
    path = _unlinked(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink < 1:
        raise ValueError("OMP installed runtime member must be a regular file")
    return info


def _open_installed(path):
    """Short preflight reservation; the native host rechecks and holds for lifetime."""
    if os.name != "nt":
        return os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")
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
    # Read sharing only: writes through any hardlink and deletion are excluded.
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
            raise ValueError("unresolved installed runtime file")
        actual = buffer.value
        if actual.startswith("\\\\?\\UNC\\"):
            actual = "\\\\" + actual[8:]
        elif actual.startswith("\\\\?\\"):
            actual = actual[4:]
        if os.path.normcase(actual) != os.path.normcase(str(path)):
            raise ValueError("foreign installed runtime final path")
        _unlinked(path)
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    return os.fdopen(descriptor, "rb")


def _verify_runtime_file(path, expected, *, installed=True, metadata=False):
    before = _installed_regular(path) if installed else _regular(path, expected["size"])
    if before.st_size != expected["size"]:
        raise ValueError("OMP runtime file size changed")
    digest = hashlib.sha256()
    raw, total = [], 0
    with _open_installed(path) as stream:
        opened = os.fstat(stream.fileno())
        if ((opened.st_dev, opened.st_ino, opened.st_size)
                != (before.st_dev, before.st_ino, before.st_size)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink < 1
                or (not installed and opened.st_nlink != 1)
                or getattr(opened, "st_file_attributes", 0) & 0x400):
            raise ValueError("OMP runtime file changed while opening")
        if metadata and opened.st_size > METADATA_LIMIT:
            raise ValueError("OMP package metadata exceeds its bound")
        while True:
            block = stream.read(min(1024 * 1024, expected["size"] - total + 1))
            if not block:
                break
            total += len(block)
            if total > expected["size"]:
                raise ValueError("OMP runtime file exceeded its bound")
            digest.update(block)
            if metadata:
                raw.append(block)
    if total != expected["size"] or digest.hexdigest() != expected["sha256"]:
        raise ValueError("OMP runtime file SHA256 changed")
    return _json(b"".join(raw).decode("utf-8")) if metadata else None


def _closed_runtime_directory(path, files, directories=(), *, optional_files=()):
    path = _unlinked(path)
    if not stat.S_ISDIR(path.stat().st_mode):
        raise ValueError("OMP runtime directory is missing")
    expected = set(files) | set(directories)
    allowed = expected | set(optional_files)
    found = set()
    for index, child in enumerate(path.iterdir()):
        if index >= len(allowed) or child.name not in allowed:
            raise ValueError("OMP runtime inventory has an extra member")
        info = _unlinked(child).stat()
        if child.name in directories:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("OMP runtime directory member changed")
        else:
            _installed_regular(child)
        found.add(child.name)
    if not expected.issubset(found):
        raise ValueError("OMP runtime inventory has a missing member")


def _runtime(executable, home):
    """Bound installed baseline, not a sandbox or all-feature transitive closure.

    Missing own staged baseline is trusted native staging, never repaired here.
    Reservations cannot exclude later candidate creation or lock a future copy;
    preflight candidate checks therefore do not promise adversarial immutability.
    """
    baseline, manifest_digest = _baseline()
    bun_root = _unlinked(executable.parent.parent)
    if executable != bun_root / "bin/omp.exe":
        raise ValueError("OMP must use its fixed installed executable layout")
    notices = ("LICENSE", "README.md", "THIRD-PARTY-NOTICES.txt")
    _closed_runtime_directory(bun_root / CLI_ROOT, ("package.json", "CHANGELOG.md", *notices),
                              ("dist", "src", "scripts", "examples"))
    _closed_runtime_directory(bun_root / CLI_ROOT / "dist", DIST_FILES, ("types",))
    _closed_runtime_directory(bun_root / NATIVES_ROOT, ("package.json", *notices), ("native",))
    _closed_runtime_directory(bun_root / NATIVES_ROOT / "native", (*NATIVE_JS_FILES, *NATIVE_TYPES),
                              optional_files=(ADDON_NAME,))
    _closed_runtime_directory(bun_root / LEAF_ROOT, ("package.json", ADDON_NAME, *notices))
    # These nearer package scopes would shadow the bound global native packages.
    shadow = _unlinked(bun_root / "install/global/node_modules/@oh-my-pi/node_modules")
    if os.path.lexists(shadow):
        raise ValueError("OMP runtime package routing is shadowed")
    files, packages = [], {}
    for row in baseline["files"]:
        path = _unlinked(bun_root / row["relative_path"])
        metadata = row["relative_path"].endswith("/package.json")
        value = _verify_runtime_file(path, row, metadata=metadata)
        if metadata:
            packages[row["relative_path"]] = value
        files.append({**row, "path": str(path)})
    cli, natives, leaf = (packages[root + "/package.json"] for root in (CLI_ROOT, NATIVES_ROOT, LEAF_ROOT))
    dependencies = cli.get("dependencies")
    optional = natives.get("optionalDependencies")
    exports = natives.get("exports")
    if (cli.get("name") != "@oh-my-pi/pi-coding-agent" or cli.get("version") != VERSION
            or cli.get("type") != "module" or cli.get("bin") != {"omp": "dist/cli.js"}
            or not isinstance(dependencies, dict) or dependencies.get("@oh-my-pi/pi-natives") != VERSION
            or natives.get("name") != "@oh-my-pi/pi-natives" or natives.get("version") != VERSION
            or natives.get("type") != "module" or natives.get("main") != "./native/index.js"
            or not isinstance(optional, dict) or optional.get("@oh-my-pi/pi-natives-win32-x64") != VERSION
            or not isinstance(exports, dict)
            or exports.get(".") != {"types": "./native/index.d.ts", "import": "./native/index.js",
                                   "default": "./native/index.js"}
            or leaf.get("name") != "@oh-my-pi/pi-natives-win32-x64" or leaf.get("version") != VERSION
            or leaf.get("main") != "./" + ADDON_NAME or leaf.get("os") != ["win32"]
            or leaf.get("cpu") != ["x64"]):
        raise ValueError("OMP runtime package/bin/version metadata differs")
    addon = baseline["files"][-1]
    candidates = []
    for directory in (home / ".omp/natives" / VERSION, bun_root / LEAF_ROOT,
                      bun_root / NATIVES_ROOT / "native", bun_root / "bin"):
        for name in (ADDON_NAME, "pi_natives.win32-x64-modern.node", "pi_natives.win32-x64.node"):
            path = _unlinked(directory / name)
            supported = name == ADDON_NAME
            present = os.path.lexists(path)
            if present:
                if not supported:
                    raise ValueError("OMP runtime has an unsupported native candidate")
                if directory != bun_root / LEAF_ROOT:
                    _verify_runtime_file(path, addon, installed=directory != home / ".omp/natives" / VERSION)
            candidates.append({"path": str(path), "present": present,
                               "size": addon["size"] if supported else 0,
                               "sha256": addon["sha256"] if supported else ""})
    return {"contract": "hwp-native-omp-runtime-v1", "bun_root": str(bun_root),
            "manifest_sha256": manifest_digest, "files": files, "native_candidates": candidates}

def _bridge_bytes():
    """Read the exact app asset; native retains source/copy read-only reservations."""
    before = _regular(BRIDGE_SOURCE, BRIDGE_LIMIT)
    with _open_installed(BRIDGE_SOURCE) as stream:
        opened = os.fstat(stream.fileno())
        if ((opened.st_dev, opened.st_ino, opened.st_size)
                != (before.st_dev, before.st_ino, before.st_size)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or getattr(opened, "st_file_attributes", 0) & 0x400):
            raise ValueError("OMP 앱 브리지 파일의 소유 경로가 바뀌었습니다.")
        raw = stream.read(BRIDGE_LIMIT + 1)
    if not raw or len(raw) > BRIDGE_LIMIT:
        raise ValueError("OMP 앱 브리지 파일 크기를 확인하세요.")
    return raw



def managed_runtime_root():
    """Versioned app-owned dependencies; inspect only, never create or repair."""
    from hwp_setup import _local_app_data
    return _unlinked(_local_app_data() / "HWPReview" / "Dependencies"
                     / f"Bun-{BUN_VERSION}-OMP-{VERSION}")


def installed_executable():
    """Prefer an existing managed root; its errors never fall back to user code."""
    managed = managed_runtime_root()
    if os.path.lexists(managed):
        return _unlinked(managed / "bin/omp.exe")
    return _unlinked(Path.home() / ".bun/bin/omp.exe")


class OmpTui:
    """At most one prepared descriptor. Release never kills or removes anything."""
    def __init__(self, source_identity, *, root=None, executable=None):
        if root is None:
            location = os.environ.get("LOCALAPPDATA")
            if not location:
                raise ValueError("OMP 전용 앱 저장 위치를 확인할 수 없습니다.")
            root = Path(location) / "HWPReview" / "NativeOmp"
        self.root = _unlinked(root)
        self.executable = _unlinked(executable) if executable is not None else installed_executable()
        self.source_identity = deepcopy(source_identity)
        self.launch = None
        self.runtime_sha256 = None

    def _identity(self, runtime, bridge_sha256):
        digest = runtime["files"][0]["sha256"]
        # The host supplies the verified source/frozen identity. A PYZ module
        # does not have a readable __file__ in a packaged executable.
        return _digest({"version": VERSION, "executable": str(self.executable),
                        "executable_sha256": digest, "source_identity": self.source_identity,
                        "profile": PROFILE,
                        "runtime": {key: runtime[key] for key in
                                    ("contract", "bun_root", "manifest_sha256", "files")},
                        "bridge_sha256": bridge_sha256,
                        "app_root": str(self.root)})

    def prepare(self, *, workspace=None):
        if self.launch is not None:
            raise ValueError("이미 준비한 OMP가 있습니다. 해당 터미널에서 종료한 뒤 해제하세요.")
        selected = None
        if workspace is not None:
            from hwp_workspace import work_folder
            selected = work_folder(workspace)
            if str(selected).startswith("\\\\") or (os.name == "nt" and ":" in str(selected)[2:]):
                raise ValueError("로컬 작업 폴더만 사용할 수 있습니다.")
            private = self.root
            installed = self.executable.parent.parent
            if any(selected == blocked or blocked in selected.parents or selected in blocked.parents
                   for blocked in (private, installed)):
                raise ValueError("OMP 전용 저장소와 실행 파일 밖의 작업 폴더를 선택하세요.")
        runtime = _runtime(self.executable, self.root / "home")
        bridge_bytes = _bridge_bytes()
        bridge_sha256 = hashlib.sha256(bridge_bytes).hexdigest()
        identity = self._identity(runtime, bridge_sha256)
        if self.runtime_sha256 is not None and identity != self.runtime_sha256:
            raise ValueError("OMP 실행 파일·소스가 바뀌었습니다. 기존 작업을 보존하고 새 앱에서 시작하세요.")
        self.runtime_sha256 = identity
        home = _directory(self.root / "home")
        profile = _directory(home / ".omp/profiles" / PROFILE / "agent")
        webview = _directory(self.root / "webview")
        identifier = secrets.token_hex(16)
        launch_root = _unlinked(self.root / "launches" / identifier)
        launch_root.mkdir(parents=True, exist_ok=False)
        workspace = selected if selected is not None else _directory(launch_root / "workspace")
        sessions = _directory(launch_root / "sessions")
        temporary = _directory(launch_root / "tmp")
        guidance = _unlinked(workspace / "HWPReview.md")
        try:
            with guidance.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(PROJECT_GUIDANCE)
        except FileExistsError:
            pass
        _regular(guidance)
        extension = launch_root / "omp-bridge.mjs"
        with extension.open("xb") as stream:
            stream.write(bridge_bytes)
        _regular(extension, BRIDGE_LIMIT)
        pipe_name = "HwpReview.Omp." + identifier
        token = secrets.token_hex(32)
        environment = dict(os.environ)
        environment.update({
            "HOME": str(home), "USERPROFILE": str(home),
            "APPDATA": str(_directory(home / "AppData/Roaming")),
            "LOCALAPPDATA": str(_directory(home / "AppData/Local")),
            "XDG_CONFIG_HOME": str(_directory(home / ".config")), "PI_CONFIG_DIR": ".omp",
            "TEMP": str(temporary), "TMP": str(temporary), "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "PATH": str(self.executable.parent) + (os.pathsep + os.environ["PATH"] if os.environ.get("PATH") else ""),
            "BUN_INSTALL": str(self.executable.parent.parent),
            "BUN_INSTALL_BIN": str(self.executable.parent),
            "BUN_INSTALL_GLOBAL_DIR": str(self.executable.parent.parent / "install/global"),
            "HWP_OMP_PIPE": "\\\\.\\pipe\\" + pipe_name,
            "HWP_OMP_TOKEN": token, "HWP_OMP_LAUNCH_ID": identifier,
        })
        arguments = ["--profile", PROFILE, "--cwd", str(workspace), "--session-dir", str(sessions),
                     "--extension", str(extension),
                     "--append-system-prompt", str(guidance)]
        self.launch = {
            "contract": "hwp-native-omp-launch-v6", "launch_id": identifier,
            "launch_directory": str(launch_root),
            "executable": str(self.executable), "executable_sha256": LAUNCHER_SHA256,
            "arguments": arguments, "environment": environment, "working_directory": str(workspace),
            "profile_directory": str(profile), "session_directory": str(sessions),
            "webview_user_data_directory": str(webview),
            "runtime_sha256": _digest({"runtime": identity, "launch_id": identifier,
                                       "working_directory": str(workspace)}),
            "runtime": runtime,
            "bridge": {"pipe_name": pipe_name, "token": token,
                       "extension_path": str(extension), "extension_sha256": bridge_sha256},
        }
        return deepcopy(self.launch)

    def release(self, identifier):
        if (not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier)
                or self.launch is None or identifier != self.launch["launch_id"]):
            raise ValueError("이 앱이 준비한 현재 OMP 식별자만 사용할 수 있습니다.")
        self.launch = None
        return {"released": True}
