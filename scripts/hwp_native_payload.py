"""Closed native UI payload verification; never compile, repair or execute."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from hwp_resources import APP_VERSION, frozen, resource_root

CONTRACT = "hwp-native-payload-v1"
CURRENT_CONTRACT = "hwp-native-current-v1"
WEBVIEW_VERSION = "1.0.4314.35"
XTERM_VERSION = "6.0.0"
FIT_VERSION = "0.11.0"
GRAPHEME_ADDON_VERSION = "0.4.0"
WEBVIEW_SOURCE = "native/Dependencies/WebView2/" + WEBVIEW_VERSION + "/"
TERMINAL_SOURCE = "native/HWPReview.Native/TerminalAssets/"
TERMINAL_FILES = (
    "index.html", "terminal.js", "terminal.css",
    "vendor/xterm/xterm.js", "vendor/xterm/xterm.css", "vendor/xterm/LICENSE",
    "vendor/xterm/package.json", "vendor/addon-fit/addon-fit.js",
    "vendor/addon-fit/LICENSE", "vendor/addon-fit/package.json",
    "vendor/unicode-graphemes/addon-unicode-graphemes.js",
    "vendor/unicode-graphemes/LICENSE", "vendor/unicode-graphemes/package.json",
)
RENDERER_VERSION = "0.8.7"
RENDERER_SOURCE = "native/HWPReview.Native/ViewerAssets/"
RENDERER_FILES = (
    "index.html", "viewer.js", "viewer.css", "svg-safety.js",
    "vendor/rhwp/rhwp.js", "vendor/rhwp/rhwp_bg.wasm",
    "vendor/rhwp/LICENSE", "vendor/rhwp/package.json",
)
ICON_SOURCE = "native/HWPReview.Native/IconAssets/lucide/"
ICON_FILES = (
    "files.svg", "folder.svg", "folder-open.svg", "file-text.svg",
    "rotate-ccw-clock.svg", "circle-check.svg", "plus.svg", "ellipsis.svg",
    "chevron-right.svg", "chevron-down.svg", "shield-check.svg", "LICENSE",
)
ICON_ORIGINAL_SHA256 = {
    "native/HWPReview.Native/IconAssets/lucide/files.svg": "97adf06fc0511e7a40be93ecda04466ed196718ab4a5e3a62b1148cc49697775",
    "native/HWPReview.Native/IconAssets/lucide/folder.svg": "f0d92b94b797a8ab7d4c4ae33c3236c47f64068351b4e14bdb5014ee42898a39",
    "native/HWPReview.Native/IconAssets/lucide/folder-open.svg": "ee57452b40e71df310214091dd9da1941a397c83197b8ff8d7ae66dd4242578b",
    "native/HWPReview.Native/IconAssets/lucide/file-text.svg": "52ca55846c202335fb870c8aa23e9345630ec10489303756d62e750f7109605c",
    "native/HWPReview.Native/IconAssets/lucide/rotate-ccw-clock.svg": "a98ff758157910b9fc9d42d5d5dc80c2f4e037afa5f8ea4dee5f5649f03d438d",
    "native/HWPReview.Native/IconAssets/lucide/circle-check.svg": "9711e045a5999f8a3b0782eb88e09b04e0559bc7b379dcc0a4ae645223e10345",
    "native/HWPReview.Native/IconAssets/lucide/plus.svg": "7f6af73bf1ff6c4bca3f18351c8d1bdec6749c0c2530c4de5da85d520c21df17",
    "native/HWPReview.Native/IconAssets/lucide/ellipsis.svg": "4f495cc72013ffdfec677f03b33a150f7b4dd741979283fd6853a09024bca112",
    "native/HWPReview.Native/IconAssets/lucide/chevron-right.svg": "2758143d7b2434e4aa7307dfd34405c87909ff4052f21b5f3f40d45224b4f19b",
    "native/HWPReview.Native/IconAssets/lucide/chevron-down.svg": "66ea878e72ed3488bb3b464c39dfdccee8d1f78e560dccea40e5e12da0e87e87",
    "native/HWPReview.Native/IconAssets/lucide/shield-check.svg": "8d4fcdbde5bba4abb1a2f6fe53e37ceb6343a1d5449a9d88f6f97c1971d26c4e",
    "native/HWPReview.Native/IconAssets/lucide/LICENSE": "b495047bd93a9b06913511076f504daba17d5bbeb3e0650f3bb53a4220329c57"
}
COPIED_PAYLOAD_SOURCES = {
    **{name: WEBVIEW_SOURCE + name for name in (
        "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.Wpf.dll",
        "WebView2Loader.dll",
    )},
    "Licenses/WebView2/LICENSE.txt": WEBVIEW_SOURCE + "LICENSE.txt",
    "Licenses/WebView2/NOTICE.txt": WEBVIEW_SOURCE + "NOTICE.txt",
    "omp-runtime.json": "packaging/omp-runtime.json",
    "bridge/omp-bridge.mjs": "native/HWPReview.Native/Bridge/omp-bridge.mjs",
    **{"terminal/" + name: TERMINAL_SOURCE + name for name in TERMINAL_FILES},
    "hwpx-renderer-dependencies.json": "packaging/hwpx-renderer-dependencies.json",
    **{"viewer/" + name: RENDERER_SOURCE + name for name in RENDERER_FILES},
    "icon-dependencies.json": "packaging/icon-dependencies.json",
    **{"Licenses/Lucide/" + name: ICON_SOURCE + name for name in ICON_FILES},
}
NATIVE_SOURCE_FILES = (
    "native/NuGet.Config",
    "native/HWPReview.Native/HWPReview.Native.csproj",
    "native/HWPReview.Native/Program.cs",
    "native/HWPReview.Native/OwnedHostClient.cs",
    "native/HWPReview.Native/ReportApi.cs",
    "native/HWPReview.Native/MainWindow.cs",
    "native/HWPReview.Native/AppIcons.cs",
    "native/HWPReview.Native/SourceMentionCompletion.cs",
    "native/HWPReview.Native/ConPtySession.cs",
    "native/HWPReview.Native/OmpTerminalPane.cs",
    "native/HWPReview.Native/HwpxDocumentPane.cs",
    "native/HWPReview.Native/OmpInstalledRuntime.cs",
    "native/HWPReview.Native/OmpSessionBridge.cs",
    "native/HWPReview.Native/Styles.xaml",
    "packaging/terminal-dependencies.json",
    *COPIED_PAYLOAD_SOURCES.values(),
    "scripts/build_native.py",
    "scripts/hwp_native_payload.py",
    "scripts/hwp_resources.py",
)
PAYLOAD_FILES = (
    "HWPReview.Native.dll",
    "HWPReview.Native.deps.json",
    "HWPReview.Native.runtimeconfig.json",
    *COPIED_PAYLOAD_SOURCES,
)
RUNTIME_REQUIREMENT = {
    "name": "Microsoft.WindowsDesktop.App",
    "version": "10.0.0",
    "roll_forward": "LatestPatch",
    "architecture": "x64",
}
MANIFEST_KEYS = {
    "contract", "version", "framework_dependent", "target_framework",
    "runtime_requirement", "files_sha256", "source_sha256",
}


def _plain_path(path: Path, *, directory: bool = False) -> Path:
    """Reject links/reparse points in every component, including Windows junctions."""
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Native payload paths must not contain links/reparse points")
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Native payload requires regular files and directories")
    if not directory and info.st_nlink != 1:
        raise ValueError("Native payload files must not be hard links")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate native manifest JSON key")
        result[key] = value
    return result


def _read_json(path: Path) -> dict:
    path = _plain_path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Native metadata is too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid native metadata JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Native metadata must be an object")
    return value


def _hash_map(value, names):
    if not isinstance(value, dict) or set(value) != set(names) or any(
        not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
        for item in value.values()
    ):
        raise ValueError("Native manifest hash membership is invalid")


def source_hashes(source_root: Path) -> dict:
    root = _plain_path(source_root, directory=True)
    verify_icon_dependencies(root)
    return {name: _sha256(_plain_path(root / name)) for name in NATIVE_SOURCE_FILES}


def verify_renderer_dependencies(source_root: Path) -> dict:
    """Verify original pinned local HWPX engine assets; never fetch or repair."""
    root = _plain_path(source_root, directory=True)
    pins = _read_json(root / "packaging/hwpx-renderer-dependencies.json")
    expected = {
        RENDERER_SOURCE + "vendor/rhwp/" + name
        for name in ("rhwp.js", "rhwp_bg.wasm", "LICENSE", "package.json")
    }
    if (pins.get("schema") != "hwp-local-hwpx-renderer-dependencies-v1"
            or pins.get("name") != "@rhwp/core"
            or pins.get("version") != RENDERER_VERSION
            or pins.get("declared_license") != "MIT"
            or pins.get("integrity") != "sha512-qgNpY9aclCvy9D7cPnpuplu42kUFZCCPhdjAS5HBso4Z4XeHG3uchYdUleQBoas/zEuwfJ7H4/ulepqdL/LXDA=="
            or pins.get("git_head") != "1a76570e833917d15817415a53c09ad61ab3203f"
            or pins.get("tarball") != "https://registry.npmjs.org/@rhwp/core/-/core-0.8.7.tgz"
            or pins.get("tarball_sha256") != "30e8a17400583570afec9169baeef3f0785e023e2a51344ed44eb8539a60e6a6"):
        raise ValueError("Local HWPX renderer provenance/version differs from the pinned engine")
    hashes = pins.get("selected_files_sha256")
    _hash_map(hashes, expected)
    for name, digest in hashes.items():
        if _sha256(_plain_path(root / name)) != digest:
            raise ValueError("Local HWPX renderer original dependency bytes changed")
    package = _read_json(root / (RENDERER_SOURCE + "vendor/rhwp/package.json"))
    if (package.get("name") != "@rhwp/core" or package.get("version") != RENDERER_VERSION
            or package.get("license") != "MIT"):
        raise ValueError("Local HWPX renderer package metadata differs")
    return pins

def verify_icon_dependencies(source_root: Path) -> dict:
    """Require the exact selected Lucide originals and complete upstream notice."""
    root = _plain_path(source_root, directory=True)
    pins = _read_json(root / "packaging/icon-dependencies.json")
    if (pins.get("schema") != "hwp-native-icon-dependencies-v1"
            or pins.get("name") != "lucide" or pins.get("version") != "1.66.0"
            or pins.get("git_head") != "a630cec77eac6568b989e87e4947a8bfe45caacd"
            or pins.get("archive") != "https://github.com/lucide-icons/lucide/releases/download/1.66.0/lucide-icons-1.66.0.zip"
            or pins.get("archive_sha256") != "b11aa5df304cc8dc910e766271804baabf1aa6d6e6771f81360761e019552e77"
            or pins.get("license_url") != "https://raw.githubusercontent.com/lucide-icons/lucide/a630cec77eac6568b989e87e4947a8bfe45caacd/LICENSE"
            or pins.get("declared_license") != "ISC AND MIT"):
        raise ValueError("Local icon provenance differs from the pinned Lucide release")
    hashes = pins.get("selected_files_sha256")
    _hash_map(hashes, {ICON_SOURCE + name for name in ICON_FILES})
    if hashes != ICON_ORIGINAL_SHA256:
        raise ValueError("Local icon original hash pins differ from the approved originals")
    for name, digest in hashes.items():
        if _sha256(_plain_path(root / name)) != digest:
            raise ValueError("Local icon original dependency bytes changed")
    return pins


def verify_inventory(directory: Path, names) -> None:
    """Closed nested file tree; even unknown empty directories are rejected."""
    root = _plain_path(directory, directory=True)
    expected_files = frozenset(names)
    expected_directories = set()
    for name in expected_files:
        parts = Path(name).parts
        if not parts or Path(name).is_absolute() or ".." in parts:
            raise ValueError("Native inventory paths must be fixed relative files")
        expected_directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    found = set()
    pending = [root]
    seen = 0
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            seen += 1
            if seen > len(expected_files) + len(expected_directories):
                raise ValueError("Native payload has unexpected files or directories")
            info = child.lstat()
            relative = child.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                _plain_path(child, directory=True)
                if relative not in expected_directories:
                    raise ValueError("Native payload has an unexpected directory")
                pending.append(child)
            else:
                _plain_path(child)
                if relative not in expected_files:
                    raise ValueError("Native payload has an unexpected file")
                found.add(relative)
    if found != expected_files:
        raise ValueError("Native payload has missing files")



def _validate_runtime(directory: Path):
    config = _read_json(directory / "HWPReview.Native.runtimeconfig.json")
    options = config.get("runtimeOptions")
    if not isinstance(options, dict):
        raise ValueError("Native runtime options are missing")
    frameworks = options.get("frameworks")
    expected = {
        "Microsoft.NETCore.App": "10.0.0",
        "Microsoft.WindowsDesktop.App": "10.0.0",
    }
    if (
        options.get("tfm") != "net10.0"
        or options.get("rollForward") != "LatestPatch"
        or set(options) - {"tfm", "rollForward", "frameworks", "configProperties"}
        or not isinstance(frameworks, list)
        or len(frameworks) != len(expected)
        or any(
            not isinstance(item, dict)
            or set(item) != {"name", "version"}
            or not isinstance(item["name"], str)
            or not isinstance(item["version"], str)
            for item in frameworks
        )
        or {item["name"]: item["version"] for item in frameworks} != expected
    ):
        raise ValueError("Native UI must use the installed .NET 10 Desktop Runtime")
    dependencies = _read_json(directory / "HWPReview.Native.deps.json")
    target = dependencies.get("runtimeTarget")
    if not isinstance(target, dict) or target.get("name") != ".NETCoreApp,Version=v10.0/win-x64":
        raise ValueError("Native dependencies must target framework-dependent win-x64")
    targets = dependencies.get("targets")
    libraries = dependencies.get("libraries")
    references = {
        "Microsoft.Web.WebView2.Core": "Microsoft.Web.WebView2.Core.dll",
        "Microsoft.Web.WebView2.Wpf": "Microsoft.Web.WebView2.Wpf.dll",
    }
    application = "HWPReview.Native/" + APP_VERSION
    reference_libraries = {name + "/" + WEBVIEW_VERSION: filename for name, filename in references.items()}
    expected_libraries = {application, *reference_libraries}
    if (
        not isinstance(targets, dict)
        or target["name"] not in targets
        or not isinstance(libraries, dict)
        or set(libraries) != expected_libraries
        or not isinstance(targets[target["name"]], dict)
        or set(targets[target["name"]]) != expected_libraries
        or any(not isinstance(value, dict) for value in libraries.values())
        or libraries[application].get("type") != "project"
        or any(libraries[name].get("type") != "reference" for name in reference_libraries)
    ):
        raise ValueError("Native dependencies must contain only this app and the pinned WebView2 references")
    application_target = targets[target["name"]][application]
    if (
        not isinstance(application_target, dict)
        or set(application_target) != {"runtime", "dependencies"}
        or application_target["dependencies"] != {name: WEBVIEW_VERSION for name in references}
        or not isinstance(application_target["runtime"], dict)
        or set(application_target["runtime"]) != {"HWPReview.Native.dll"}
    ):
        raise ValueError("Native application dependency references differ from the pinned SDK")
    for library, filename in reference_libraries.items():
        entry = targets[target["name"]][library]
        if (not isinstance(entry, dict) or set(entry) != {"runtime"}
                or not isinstance(entry["runtime"], dict) or set(entry["runtime"]) != {filename}):
            raise ValueError("Native WebView2 dependency paths must name only pinned SDK assemblies")
        metadata = entry["runtime"][filename]
        if (not isinstance(metadata, dict) or set(metadata) - {"assemblyVersion", "fileVersion"}
                or any(value != WEBVIEW_VERSION for value in metadata.values())):
            raise ValueError("Native WebView2 assembly versions differ from the pinned SDK")


def verify_payload(directory: Path, *, source_root: Path | None = None) -> dict:
    """Verify exact runtime files and (in source mode) the complete build inputs."""
    directory = _plain_path(directory, directory=True)
    verify_inventory(directory, (*PAYLOAD_FILES, "manifest.json"))
    manifest = _read_json(directory / "manifest.json")
    if (
        set(manifest) != MANIFEST_KEYS
        or manifest.get("contract") != CONTRACT
        or manifest.get("version") != APP_VERSION
        or manifest.get("framework_dependent") is not True
        or manifest.get("target_framework") != "net10.0-windows"
        or manifest.get("runtime_requirement") != RUNTIME_REQUIREMENT
    ):
        raise ValueError("Native payload contract/version/runtime differs from this application")
    _hash_map(manifest["files_sha256"], PAYLOAD_FILES)
    _hash_map(manifest["source_sha256"], NATIVE_SOURCE_FILES)
    if any(_sha256(directory / name) != expected for name, expected in manifest["files_sha256"].items()):
        raise ValueError("Native payload bytes changed")
    if any(manifest["files_sha256"][name] != manifest["source_sha256"][source]
           for name, source in COPIED_PAYLOAD_SOURCES.items()):
        raise ValueError("Copied native dependency/assets differ from the captured source bytes")
    _validate_runtime(directory)
    if source_root is not None and source_hashes(source_root) != manifest["source_sha256"]:
        raise ValueError("Native UI is stale for the current source; explicitly build a fresh payload")
    return manifest


def _source_payload(root: Path) -> Path:
    root = _plain_path(root, directory=True)
    build = _plain_path(root / "build", directory=True)
    pointer = _read_json(build / "native-current.json")
    name = pointer.get("path")
    if (
        set(pointer) != {"contract", "path"}
        or pointer.get("contract") != CURRENT_CONTRACT
        or not isinstance(name, str)
        or "\\" in name or ":" in name
        or name.startswith("/")
        or any(part in ("", ".", "..") for part in name.split("/"))
        or not name.startswith("build/")
        or not name.endswith("/native-ui")
    ):
        raise ValueError("Native source pointer must name an owned build/native-ui directory")
    directory = _plain_path(root / name, directory=True)
    if not directory.is_relative_to(build):
        raise ValueError("Native source pointer escapes the repository build folder")
    verify_payload(directory, source_root=root)
    return directory


def locate_payload() -> Path:
    """Locate only the registered source payload or the parent's embedded payload."""
    root = resource_root()
    if frozen():
        directory = root / "native-ui"
        verify_payload(directory)
        return directory
    return _source_payload(root)


def _installed_dotnet(root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError(".NET 설치 위치는 실제 절대 경로여야 합니다.")
    root = _plain_path(root, directory=True)
    executable = _plain_path(root / "dotnet.exe")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        binary = wintypes.DWORD()
        get_type = ctypes.WinDLL("kernel32", use_last_error=True).GetBinaryTypeW
        get_type.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        get_type.restype = wintypes.BOOL
        if not get_type(str(executable), ctypes.byref(binary)) or binary.value != 6:
            raise ValueError("실제 x64 dotnet.exe 설치본이 필요합니다.")
    for framework, marker in (
        ("Microsoft.NETCore.App", "System.Private.CoreLib.dll"),
        ("Microsoft.WindowsDesktop.App", "PresentationFramework.dll"),
    ):
        shared = _plain_path(root / "shared" / framework, directory=True)
        versions = [
            item for item in shared.iterdir()
            if re.fullmatch(r"10\.0\.[0-9]+", item.name) is not None
        ]
        if not versions:
            raise ValueError("이미 설치된 x64 .NET 10 Desktop Runtime이 필요합니다. 앱은 설치·다운로드하지 않습니다.")
        latest = max(versions, key=lambda item: int(item.name.rsplit(".", 1)[1]))
        _plain_path(latest, directory=True)
        _plain_path(latest / marker)
    return executable


def locate_dotnet() -> Path:
    """Select installed runtime metadata once; never run a probe or PATH fallback."""
    for name in ("DOTNET_ROOT_X64", "DOTNET_ROOT"):
        selected = os.environ.get(name)
        if selected:
            return _installed_dotnet(Path(selected))
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\dotnet\Setup\InstalledVersions\x64",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                selected, kind = winreg.QueryValueEx(key, "InstallLocation")
            if kind != winreg.REG_SZ or not isinstance(selected, str):
                raise ValueError(".NET x64 설치 위치 메타데이터를 확인할 수 없습니다.")
        except FileNotFoundError:
            selected = str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "dotnet")
        return _installed_dotnet(Path(selected))
    raise ValueError("이 앱은 이미 설치된 x64 .NET 10 Desktop Runtime이 있는 Windows용입니다.")
