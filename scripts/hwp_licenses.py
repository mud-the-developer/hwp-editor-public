"""Collect original notices from prepared build inputs, never grant release clearance."""

from importlib.metadata import distribution
import json
from pathlib import Path
import platform
import shutil
import sys

from hwp_build_source import sha256
from hwp_resources import APP_LICENSE, SOURCE_URL
from hwp_native_payload import (
    COPIED_PAYLOAD_SOURCES, WEBVIEW_SOURCE, WEBVIEW_VERSION, TERMINAL_SOURCE,
    XTERM_VERSION, FIT_VERSION, GRAPHEME_ADDON_VERSION, RENDERER_SOURCE,
    RENDERER_VERSION, verify_renderer_dependencies,
    ICON_SOURCE, verify_icon_dependencies,
)

# Include build-tool notices conservatively; their presence is not a claim that
# all these distributions, or all their vendored dependencies, enter the EXE.
PACKAGE_NOTICES = (
    ("PyMuPDF", "runtime", (("COPYING", "PyMuPDF-LICENSE.txt"),)),
    ("olefile", "runtime", (("LICENSE.txt", "olefile-LICENSE.txt"),)),
    ("PyInstaller", "build-tool/bootloader", (("COPYING.txt", "PyInstaller-LICENSE.txt"),)),
    (
        "pyinstaller-hooks-contrib",
        "build-tool",
        (("LICENSE", "pyinstaller-hooks-contrib-LICENSE.txt"),),
    ),
    ("altgraph", "build-tool", (("LICENSE", "altgraph-LICENSE.txt"),)),
    (
        "packaging",
        "build-tool",
        (
            ("LICENSE", "packaging-LICENSE.txt"),
            ("LICENSE.APACHE", "packaging-LICENSE.APACHE.txt"),
            ("LICENSE.BSD", "packaging-LICENSE.BSD.txt"),
        ),
    ),
    ("pefile", "build-tool", (("LICENSE", "pefile-LICENSE.txt"),)),
    ("pywin32-ctypes", "build-tool", (("LICENSE.txt", "pywin32-ctypes-LICENSE.txt"),)),
    ("setuptools", "build-tool", (("LICENSE", "setuptools-LICENSE.txt"),)),
)
TERMINAL_NOTICES = (
    ("webview2", "Microsoft.Web.WebView2", WEBVIEW_VERSION, "BSD-3-Clause", (
        (WEBVIEW_SOURCE + "LICENSE.txt", "WebView2-SDK-LICENSE.txt"),
        (WEBVIEW_SOURCE + "NOTICE.txt", "WebView2-SDK-NOTICE.txt"),
    )),
    ("xterm", "@xterm/xterm", XTERM_VERSION, "MIT", (
        (TERMINAL_SOURCE + "vendor/xterm/LICENSE", "xterm-LICENSE.txt"),
    )),
    ("addon_fit", "@xterm/addon-fit", FIT_VERSION, "MIT", (
        (TERMINAL_SOURCE + "vendor/addon-fit/LICENSE", "xterm-addon-fit-LICENSE.txt"),
    )),
    ("unicode_graphemes", "@xterm/addon-unicode-graphemes", GRAPHEME_ADDON_VERSION, "MIT", (
        (TERMINAL_SOURCE + "vendor/unicode-graphemes/LICENSE", "xterm-unicode-graphemes-LICENSE.txt"),
    )),
)
SETUP_NOTICES = (
    ("powershell7", "PowerShell", "7.6.9", "MIT", (
        ("packaging/licenses/PowerShell-7.6.9-LICENSE.txt", "PowerShell-7.6.9-LICENSE.txt"),
    )),
    ("d2coding_propo", "D2Coding Nerd Font Propo", "3.5.1",
     "OFL-1.1 font; mixed glyph terms; exact Hack extraction revision unverified", (
        ("packaging/licenses/D2Coding-Nerd-Font-3.5.1-LICENSE.txt", "D2Coding-Nerd-Font-3.5.1-LICENSE.txt"),
        ("packaging/licenses/D2Coding-Nerd-Font-3.5.1-README.md", "D2Coding-Nerd-Font-3.5.1-README.md"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-LICENSE.txt", "Nerd-Fonts-3.5.1-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Codicons-LICENSE.txt", "Nerd-Fonts-3.5.1-Codicons-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Devicons-LICENSE.txt", "Nerd-Fonts-3.5.1-Devicons-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Hack-LICENSE.txt", "Nerd-Fonts-3.5.1-Hack-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Font-Awesome-LICENSE.txt", "Nerd-Fonts-3.5.1-Font-Awesome-LICENSE.txt"),
        ("packaging/licenses/Font-Awesome-Extension-09d8024-LICENCE.txt", "Font-Awesome-Extension-09d8024-LICENCE.txt"),
        ("packaging/licenses/Font-Logos-1.3.0-LICENSE.txt", "Font-Logos-1.3.0-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-MaterialDesign-LICENSE.txt", "Nerd-Fonts-3.5.1-MaterialDesign-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-MaterialDesign-Apache-2.0.txt", "Nerd-Fonts-3.5.1-MaterialDesign-Apache-2.0.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Octicons-LICENSE.txt", "Nerd-Fonts-3.5.1-Octicons-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Seti-LICENSE.txt", "Nerd-Fonts-3.5.1-Seti-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Pomicons-LICENSE.txt", "Nerd-Fonts-3.5.1-Pomicons-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Powerline-Extra-LICENSE.txt", "Nerd-Fonts-3.5.1-Powerline-Extra-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Powerline-Symbols-LICENSE.txt", "Nerd-Fonts-3.5.1-Powerline-Symbols-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Power-Symbols-IEC-LICENSE.txt", "Nerd-Fonts-3.5.1-Power-Symbols-IEC-LICENSE.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Weather-Icons-OFL.txt", "Nerd-Fonts-3.5.1-Weather-Icons-OFL.txt"),
        ("packaging/licenses/Nerd-Fonts-3.5.1-Weather-Icons-OFL-FAQ.txt", "Nerd-Fonts-3.5.1-Weather-Icons-OFL-FAQ.txt"),
    )),
    ("dotnet10", ".NET 10 Desktop Runtime x64", "10.0.14",
     "MIT source notice; external Microsoft installer/third-party terms remain applicable", (
        ("packaging/licenses/DotNet-WindowsDesktop-10.0.14-LICENSE.txt", "DotNet-WindowsDesktop-10.0.14-LICENSE.txt"),
    )),
    ("webview2", "Microsoft Edge WebView2 Runtime", "156.0.4314.58",
     "Microsoft Edge WebView2 Runtime EULA; not the SDK license", (
        ("packaging/licenses/WebView2-Runtime-156.0.4314.58-EULA.html", "WebView2-Runtime-156.0.4314.58-EULA.html"),
    )),
    ("omp_runtime", "Bun / oh-my-pi", "1.4.4 / 18.15.0",
     "Bun MIT/LGPL/linked-library inventory; OMP MIT and original third-party notices", (
        ("packaging/licenses/Bun-1.4.4-LICENSE.md", "Bun-1.4.4-LICENSE.md"),
        ("packaging/licenses/OMP-18.15.0-LICENSE.txt", "OMP-18.15.0-LICENSE.txt"),
        ("packaging/licenses/OMP-18.15.0-THIRD-PARTY-NOTICES.txt", "OMP-18.15.0-THIRD-PARTY-NOTICES.txt"),
        ("packaging/licenses/OMP-Natives-18.15.0-LICENSE.txt", "OMP-Natives-18.15.0-LICENSE.txt"),
        ("packaging/licenses/OMP-Natives-18.15.0-THIRD-PARTY-NOTICES.txt", "OMP-Natives-18.15.0-THIRD-PARTY-NOTICES.txt"),
        ("packaging/licenses/OMP-Win32-x64-18.15.0-LICENSE.txt", "OMP-Win32-x64-18.15.0-LICENSE.txt"),
        ("packaging/licenses/OMP-Win32-x64-18.15.0-THIRD-PARTY-NOTICES.txt", "OMP-Win32-x64-18.15.0-THIRD-PARTY-NOTICES.txt"),
    )),
)
SUPPLEMENTAL_NOTICE_FILES = (
    "Expat-2.8.5-COPYING.txt",
    "OpenSSL-3.5.9-LICENSE.txt",
    "LibTomMath-Tcl-9.0.4-LICENSE.txt",
    "zlib-Tcl-1.3.2-LICENSE.txt",
    "zlib-ng-2.2.4-LICENSE.txt",
    "XZ-5.2.5-COPYING.txt",
    "rhwp-0.8.7-THIRD-PARTY-LICENSES.txt",
    "Volexity-hwp-extract-e5f8b5e-LICENSE.txt",
    "rhwp-0.8.7-rust-dependency-NOTICES.txt",
)



def _regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Required original notice is missing or non-regular: " + str(path))
    return path


def _installed_notice(installed, basename):
    matches = [
        installed.locate_file(path)
        for path in installed.files or ()
        if str(path).replace("\\", "/").split("/", 1)[0].endswith(".dist-info")
        and Path(path).name == basename
    ]
    if len(matches) != 1:
        raise RuntimeError("Cannot identify exact installed license: " + installed.metadata["Name"])
    return _regular(matches[0])


def _copy_notice(source, directory, name, hashes):
    if not name or name in (".", "..") or any(character in name for character in "/\\:"):
        raise ValueError("Notice output must be a fixed basename")
    source = _regular(source)
    target = directory / name
    with source.open("rb") as original, target.open("xb") as output:
        shutil.copyfileobj(original, output)
    hashes[name] = sha256(target)


def _package_record(installed, role, files):
    return {
        "name": installed.metadata["Name"],
        "version": installed.version,
        "role": role,
        "declared_license": installed.metadata.get("License-Expression")
        or installed.metadata.get("License"),
        "files": files,
    }


def write_licenses(source_root, directory, pinned_notices, *, app_version):
    """Write one NEW notice directory; retain partial output on the first failure.

    The resulting index describes notice provenance, not the actual frozen
    component inventory or completion of corresponding-source obligations.
    """
    source_root, directory = Path(source_root).resolve(strict=True), Path(directory)
    directory.mkdir()
    pins_path = source_root / "packaging/third-party-sources.json"
    pins = json.loads(_regular(pins_path).read_text(encoding="utf-8"))
    installed = {name: distribution(name) for name, _, _ in PACKAGE_NOTICES}
    pinned_notices = tuple(pinned_notices)
    import pymupdf

    notice = pins["mupdf_notices"]
    if (
        installed["PyMuPDF"].version != pins["pymupdf"]["version"]
        or pymupdf.VersionBind != pins["pymupdf"]["version"]
        or pymupdf.VersionFitz != pins["mupdf"]["version"]
        or notice["path"] != "packaging/licenses/MuPDF-1.26.12-NOTICES.txt"
        or sha256(_regular(source_root / notice["path"])) != notice["sha256"]
        or Path(notice["path"]).name not in pinned_notices
    ):
        raise RuntimeError("Renderer/source/notice versions or hashes differ; do not build")

    supplemental = pins.get("supplemental_notice_files_sha256")
    if (not isinstance(supplemental, dict)
            or set(supplemental) != set(SUPPLEMENTAL_NOTICE_FILES)):
        raise RuntimeError("Supplemental original notice inventory differs")
    for name, digest in supplemental.items():
        if (name not in pinned_notices
                or sha256(_regular(source_root / "packaging/licenses" / name)) != digest):
            raise RuntimeError("Supplemental original notice differs from its pinned bytes")

    terminal_path = source_root / "packaging/terminal-dependencies.json"
    terminal_pins = json.loads(_regular(terminal_path).read_text(encoding="utf-8"))
    if terminal_pins.get("schema") != "hwp-native-terminal-dependencies-v1":
        raise RuntimeError("Terminal dependency notice contract is missing or unsupported")
    terminal_hashes = terminal_pins.get("selected_files_sha256")
    expected_terminal = {
        relative for relative in COPIED_PAYLOAD_SOURCES.values()
        if relative.startswith((WEBVIEW_SOURCE, TERMINAL_SOURCE + "vendor/"))
    }
    if not isinstance(terminal_hashes, dict) or set(terminal_hashes) != expected_terminal:
        raise RuntimeError("Terminal SDK/renderer pinned dependency inventory differs")
    for key, _, version, _, _ in TERMINAL_NOTICES:
        if terminal_pins.get(key, {}).get("version") != version:
            raise RuntimeError("Terminal SDK/renderer version differs from the pinned source")
    for relative, digest in terminal_hashes.items():
        if sha256(_regular(source_root / relative)) != digest:
            raise RuntimeError("Terminal SDK/renderer dependency differs from its pinned bytes")

    # Document renderer pins are deliberately separate from terminal pins.
    verify_renderer_dependencies(source_root)
    renderer_path = source_root / "packaging/hwpx-renderer-dependencies.json"
    verify_icon_dependencies(source_root)

    dependencies_path = source_root / "packaging/windows-dependencies.json"
    dependencies = json.loads(_regular(dependencies_path).read_text(encoding="utf-8"))
    components = dependencies.get("components")
    if (dependencies.get("contract") != "hwp-windows-dependencies-v2"
            or not isinstance(components, list)
            or len(components) != len(SETUP_NOTICES)
            or any(not isinstance(row, dict) for row in components)):
        raise RuntimeError("Required external dependency notice contract is missing or unsupported")
    by_id = {row.get("id"): row for row in components}
    expected_notices = {relative for _, _, _, _, notices in SETUP_NOTICES for relative, _ in notices}
    dependency_hashes = dependencies.get("license_files_sha256")
    if (set(by_id) != {key for key, _, _, _, _ in SETUP_NOTICES}
            or not isinstance(dependency_hashes, dict) or set(dependency_hashes) != expected_notices):
        raise RuntimeError("Required dependency original notice inventory differs")
    for key, _, version, _, notices in SETUP_NOTICES:
        component = by_id[key]
        if (component.get("version") != version
                or component.get("license_files") != [relative for relative, _ in notices]):
            raise RuntimeError("Required dependency version or original notice selection differs")
        for relative, _ in notices:
            if sha256(_regular(source_root / relative)) != dependency_hashes[relative]:
                raise RuntimeError("Required dependency original notice differs from pinned bytes")

    hashes, packages = {}, []
    for name, role, notices in PACKAGE_NOTICES:
        current = installed[name]
        for basename, output_name in notices:
            _copy_notice(_installed_notice(current, basename), directory, output_name, hashes)
        packages.append(_package_record(current, role, [name for _, name in notices]))

    win32 = distribution("pywin32")
    win32_files = []
    for relative in ("win32/license.txt", "win32com/License.txt"):
        name = relative.split("/")[0] + "-LICENSE.txt"
        _copy_notice(win32.locate_file(relative), directory, name, hashes)
        win32_files.append(name)
    packages.append(_package_record(win32, "runtime", win32_files))

    _copy_notice(Path(sys.base_prefix) / "LICENSE.txt", directory, "Python-LICENSE.txt", hashes)
    for name in pinned_notices:
        _copy_notice(source_root / "packaging/licenses" / name, directory, name, hashes)
    _copy_notice(pins_path, directory, "third-party-sources.json", hashes)
    _copy_notice(source_root / "LICENSE", directory, "HWP-Editor-AGPL-3.0.txt", hashes)
    for _, name, version, license_name, notices in TERMINAL_NOTICES:
        for relative, output_name in notices:
            _copy_notice(source_root / relative, directory, output_name, hashes)
        packages.append({
            "name": name, "version": version, "role": "native-terminal-runtime-component",
            "declared_license": license_name, "files": [output_name for _, output_name in notices],
        })
    _copy_notice(terminal_path, directory, "terminal-dependencies.json", hashes)
    _copy_notice(source_root / (RENDERER_SOURCE + "vendor/rhwp/LICENSE"),
                 directory, "rhwp-core-LICENSE.txt", hashes)
    _copy_notice(renderer_path, directory, "hwpx-renderer-dependencies.json", hashes)
    _copy_notice(source_root / (ICON_SOURCE + "LICENSE"),
                 directory, "Lucide-1.66.0-LICENSE.txt", hashes)
    _copy_notice(source_root / "packaging/icon-dependencies.json",
                 directory, "icon-dependencies.json", hashes)
    packages.append({
        "name": "lucide", "version": "1.66.0",
        "role": "native-local-vector-icons",
        "declared_license": "ISC AND MIT",
        "files": ["Lucide-1.66.0-LICENSE.txt"],
    })
    packages.append({
        "name": "@rhwp/core", "version": RENDERER_VERSION,
        "role": "local-read-only-hwpx-preview-runtime-component",
        "declared_license": "MIT", "files": ["rhwp-core-LICENSE.txt"],
    })
    for _, name, version, license_name, notices in SETUP_NOTICES:
        for relative, output_name in notices:
            _copy_notice(source_root / relative, directory, output_name, hashes)
        packages.append({
            "name": name, "version": version, "role": "external-startup-prerequisite-not-bundled",
            "declared_license": license_name, "files": [output_name for _, output_name in notices],
        })
    _copy_notice(dependencies_path, directory, "windows-dependencies.json", hashes)
    _copy_notice(source_root / "packaging/font-glyph-notices.json", directory, "font-glyph-notices.json", hashes)


    notice_text = (
        f"HWP Editor {app_version}: {APP_LICENSE}; no warranty.\n"
        f"Project repository: {SOURCE_URL} (recipient access must be arranged).\n"
        f"Expected companion application-input archive: HWPReview-source-{app_version}.zip.\n"
        "This exact app-input archive is not the complete third-party corresponding source.\n"
        "Provide matching complete corresponding source and required build/install materials\n"
        "to recipients through the chosen AGPL-compliant method; a repository URL alone is not proof.\n"
        "PyMuPDF/MuPDF use the AGPL option. Exact upstream source requirements are in third-party-sources.json.\n"
        "Keep full upstream thirdparty/font terms; this notice collection does not replace them.\n"
        "olefile includes its BSD and PIL terms; pywin32 notices retain their file-specific conditions.\n"
        "Python/Tcl/Tk original notices and PyInstaller COPYING/bootloader exception are included.\n"
        "Supplemental Expat/OpenSSL/LibTomMath/zlib/zlib-ng/liblzma originals and the rhwp upstream third-party notice are included; original notice collection alone does not bind every current frozen binary or close all WASM transitive notice obligations.\n"
        "Additional installed build-tool notices are labeled in THIRD-PARTY-LICENSES.json.\n"
        "That index is not an exact EXE component inventory or redistribution clearance.\n"
        "See docs/licensing.md in the application source archive for scope and unresolved obligations.\n"
        "The pinned WebView2 SDK interop/loader (BSD3 notices) and local xterm/fit/Unicode-graphemes (MIT notices) are included.\n"
        "The pinned @rhwp/core0.8.7 MIT JS/WASM engine and its unmodified original notice are included for local read-only HWPX page previews, not Hancom layout certification. No fonts are bundled with this renderer.\n"
        "The separate WebView2 Runtime and other required external runtimes are NOT bundled.\n"
        "Hancom/security DLLs, .NET runtime/apphost/SDK, native OMP/Bun/Node/Pi executables/accounts,\n"
        "user credentials/configuration/fonts/documents and approvals are NOT bundled.\n"
        "Normal startup prepares missing pinned .NET Desktop Runtime, WebView2 Runtime, Bun/OMP,\n"
        "PowerShell7 and D2Coding Nerd Font Propo through the requirements installer, preserving existing registrations.\n"
        "Their binaries are NOT bundled; installation may require human UAC/terms approval and OMP login.\n"
        "Captured original font/glyph notices and their explicit provenance gaps are retained separately.\n"
        "The font README label unlicensed for Font Logos1.3.0 is clarified by its retained original Unlicense/public-domain dedication. Trademark and other component terms remain separate.\n"
        "Font Awesome Extension0.0.3 has no notice in its tag; a later original MIT notice is retained with identical font-blob evidence. The exact Hack extraction revision remains unresolved. See font-glyph-notices.json.\n"
        "A candidate does not establish Native qualification, source delivery or publication.\n"
    )
    with (directory / "NOTICE.txt").open("x", encoding="utf-8") as output:
        output.write(notice_text)
    hashes["NOTICE.txt"] = sha256(directory / "NOTICE.txt")
    index = {
        "schema": 1,
        "kind": "hwp-collected-license-notices-v1",
        "project": {
            "name": "HWP Editor",
            "version": app_version,
            "license": APP_LICENSE,
            "source_repository": SOURCE_URL,
            "expected_application_source_archive": f"HWPReview-source-{app_version}.zip",
        },
        "scope": "Notice provenance only; not an exact frozen-component inventory or legal clearance.",
        "packages": packages,
        "python_version": platform.python_version(),
        "linked_mupdf_version": pymupdf.VersionFitz,
        "pinned_notice_files": list(pinned_notices),
        "files_sha256": hashes,
        "corresponding_source_provision": "not_verified",
        "runtime_binary_notice_binding": "not_verified_by_collector",
        "redistribution_ready": False,
    }
    with (directory / "THIRD-PARTY-LICENSES.json").open("x", encoding="utf-8") as output:
        json.dump(index, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n")
    return index
