"""Stage a versioned Windows EXE. No install, signing, upload or Native.

Uses pinned, already installed build tools and allowlisted assets only. A passed
synthetic self-test is not Native qualification or redistribution clearance.
"""

from datetime import datetime, timezone
import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from struct import Struct
import sys
import uuid
import zipfile

import hwp_build_source as build_source
import hwp_install_bundle as install_bundle
import build_native
import hwp_native_payload as native_payload
import hwp_licenses as license_bundle
from hwp_resources import APP_LICENSE, APP_VERSION, SOURCE_URL

ROOT = Path(__file__).resolve().parents[1]
ASSETS = (
    "docs/template-review-draft.md",
    "docs/report-harness.md",
    "docs/user-guide.md",
    "docs/desktop-app.md",
    "docs/releases.md",
    "docs/licensing.md",
    "packaging/README-release.txt",
    "packaging/Setup-Requirements.cmd",
    "packaging/windows-dependencies.json",
    "packaging/licenses/PowerShell-7.6.9-LICENSE.txt",
    "packaging/licenses/DotNet-WindowsDesktop-10.0.14-LICENSE.txt",
    "packaging/licenses/WebView2-Runtime-156.0.4314.58-EULA.html",
    "packaging/licenses/Hancom-Automation-Usage-Notice.txt",
    "packaging/licenses/Bun-1.4.4-LICENSE.md",
    "packaging/licenses/OMP-18.15.0-LICENSE.txt",
    "packaging/licenses/OMP-18.15.0-THIRD-PARTY-NOTICES.txt",
    "packaging/licenses/OMP-Natives-18.15.0-LICENSE.txt",
    "packaging/licenses/OMP-Natives-18.15.0-THIRD-PARTY-NOTICES.txt",
    "packaging/licenses/OMP-Win32-x64-18.15.0-LICENSE.txt",
    "packaging/licenses/OMP-Win32-x64-18.15.0-THIRD-PARTY-NOTICES.txt",
    "packaging/licenses/D2Coding-Nerd-Font-3.5.1-LICENSE.txt",
    "packaging/licenses/D2Coding-Nerd-Font-3.5.1-README.md",
    "packaging/licenses/Font-Logos-1.3.0-LICENSE.txt",
    "packaging/font-glyph-notices.json",
    "packaging/licenses/Nerd-Fonts-3.5.1-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Codicons-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Font-Awesome-LICENSE.txt",
    "packaging/licenses/Font-Awesome-Extension-09d8024-LICENCE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-MaterialDesign-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Octicons-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Pomicons-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Powerline-Extra-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Powerline-Symbols-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Weather-Icons-OFL.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Weather-Icons-OFL-FAQ.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Devicons-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Seti-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Power-Symbols-IEC-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-Hack-LICENSE.txt",
    "packaging/licenses/Nerd-Fonts-3.5.1-MaterialDesign-Apache-2.0.txt",
    "LICENSE",
    "scripts/pi_probe.mjs",
    "scripts/pi_chat.mjs",
    "docs/hwp-agent-runtime.md",
)
HIDDEN = (
    "hwp_form_draft",
    "hwp_fill_plan",
    "hwp_fixed_layout",
    "hwp_native_plan",
    "packaged_smoke_runtime",
    "hwp_project",
    "hwp_prepare_native",
    "hwp_worker_trace",
    "hwp_security",
    "hwp_process_identity",
    "hwp_native_owner",
    "hwp_text_apply",
    "hwp_native_buffer",
    "hwp_native_cells",
    "hwp_contract_apply",
    "hwp_contract_control",
    "hwp_review_session",
    "hwp_workflow",
    "hwp_agents",
    "hwp_pi_probe",
    "hwp_pi_chat",
    "hwp_pi_context",
    "hwp_template_draft",
    "hwp_object_captions",
    "hwp_report_harness",
    "hwp_report_runtime",
    "hwp_report_project",
    "hwp_blank_document",
    "hwp_report_ui",
    "hwp_legacy_client",
    "hwp_legacy_ui",
    "hwp_synthetic_report",
    "hwp_report_smoke",
    "hwp_report_control",
    "hwp_report_writer",
    "hwp_native_host",
    "hwp_native_payload",
    "hwp_native_smoke",
    "hwp_omp_tui",
    "hwp_setup",
    "hwp_setup_ui",
    "hwp_setup_runtime",
    "pythoncom",
    "win32com.client",
)
EXCLUDED = (
    "native_hwp",
    "win32gui",
    "win32ui",  # Unused Pythonwin GUI extension, not the pywin32 COM runtime.
)  # generic edit/apply helper is NOT an EXE entry point
EXPECTED = {
    "PyInstaller": "6.22.3",
    "pyinstaller-hooks-contrib": "2026.8",
    "PyMuPDF": "1.26.7",
    "olefile": "0.47",
    "pywin32": "311",
}
# Standard Visual C++/MFC/UCRT filenames, debug and localized variants, and
# installers/merge modules, as Windows path components (not substring matches).
_EXTERNAL_VC_COMPONENT = re.compile(
    r"(?:^|[\\/:])(?:"
    r"(?:(?:msvc[mpr]|vcruntime|concrt|vccorlib|mfc[mno]?|vcomp|vcamp|atl)"
    r"[0-9][a-z0-9_]*|msvcrtd?|msvcp_win|ucrtbased?|api-ms-win-crt-[a-z0-9-]+)\.dll"
    r"|(?:vc_?redist(?:[._-][a-z0-9._-]+)?|vc_runtime(?:minimum|additional)(?:_[a-z0-9]+)?)\.(?:exe|msi)"
    r"|microsoft_vc[0-9]+_[a-z0-9_]+\.msm"
    r")[ .]*(?::[^\\/]*)?\Z",
    re.IGNORECASE | re.ASCII,
)
# Unsigned Windows onefile CArchive format written by pinned PyInstaller.
_CARCHIVE_MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
_CARCHIVE_COOKIE = Struct("!8sIIII64s")
_CARCHIVE_ENTRY = Struct("!IIIIBc")

SOURCE_FILES = (
    "scripts/hwp_desktop.py",
    "scripts/hwp_resources.py",
    "scripts/hwp_review.py",
    "scripts/hwp_native_plan.py",
    "scripts/hwp_fixed_layout.py",
    "scripts/hwp_fill_plan.py",
    "scripts/hwp_form_draft.py",
    "scripts/packaged_smoke_runtime.py",
    "scripts/build_exe.py",
    "scripts/hwp_build_source.py",
    "scripts/hwp_install_bundle.py",
    "scripts/build_native.py",
    "scripts/hwp_native_payload.py",
    "scripts/hwp_native_host.py",
    "scripts/hwp_native_smoke.py",
    "scripts/hwp_omp_tui.py",
    "scripts/hwp_setup.py",
    "scripts/hwp_setup_ui.py",
    "scripts/hwp_setup_runtime.py",
    "packaging/Install-HWPReview.cmd",
    "packaging/Install-HWPReview.ps1",
    "packaging/Uninstall-HWPReview.ps1",
    "packaging/AI-INSTALL.md",
    "packaging/Quick-Start-ko.txt",
    "scripts/hwp_licenses.py",
    "requirements-build.txt",
    "requirements.txt",
    "scripts/hwp_project.py",
    "scripts/hwp_prepare_native.py",
    "scripts/hwp_worker_trace.py",
    "scripts/hwp_security.py",
    "scripts/hwp_process_identity.py",
    "scripts/hwp_native_owner.py",
    "scripts/hwp_desktop_ui.py",
    "scripts/hwp_workspace.py",
    "scripts/hwp_library_ui.py",
    "scripts/hwp_text_proposal.py",
    "scripts/hwp_text_apply.py",
    "scripts/hwp_native_buffer.py",
    "scripts/hwp_native_cells.py",
    "scripts/hwp_contract_apply.py",
    "scripts/hwp_contract_control.py",
    "scripts/hwp_review_session.py",
    "scripts/hwp_workflow.py",
    "scripts/hwp_agents.py",
    "scripts/hwp_pi_probe.py",
    "scripts/hwp_pi_chat.py",
    "scripts/hwp_pi_context.py",
    "packaging/third-party-sources.json",
    "scripts/hwp_template_draft.py",
    "scripts/hwp_object_captions.py",
    "scripts/hwp_report_harness.py",
    "scripts/hwp_report_runtime.py",
    "scripts/hwp_report_project.py",
    "scripts/hwp_blank_document.py",
    "scripts/hwp_report_ui.py",
    "scripts/hwp_legacy_client.py",
    "scripts/hwp_legacy_ui.py",
    "scripts/hwp_release_sources.py",
    "packaging/upstream-source-index.json",
    "scripts/hwp_synthetic_report.py",
    "scripts/hwp_report_smoke.py",
    "scripts/hwp_report_control.py",
    "scripts/hwp_report_writer.py",
    "README.md",
    "CHANGELOG.md",
    "THIRD_PARTY_REFERENCES.md",
    "docs/exe-personal.md",
) + tuple(name for name in native_payload.NATIVE_SOURCE_FILES if not name.startswith("scripts/"))
LEGAL_FILES = (
    "Tcl-9.0.4-license.terms",
    "Tk-9.0.4-license.terms",
    "MuPDF-1.26.12-NOTICES.txt",
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


def is_external_vc_component(name):
    return bool(_EXTERNAL_VC_COMPONENT.search(name))


def spec_with_external_vc_runtime(spec):
    """Filter the pinned onefile recipe after all hooks and dependency analysis."""
    marker = "pyz = PYZ(a.pure)\n"
    if spec.splitlines(keepends=True).count(marker) != 1:
        raise RuntimeError("Pinned PyInstaller onefile spec changed; review the runtime filter")
    return spec.replace(
        marker,
        "import re\n"
        f"_external_vc = re.compile({_EXTERNAL_VC_COMPONENT.pattern!r}, re.IGNORECASE | re.ASCII)\n"
        "a.binaries = [row for row in a.binaries if not any(_external_vc.search(n) for n in row[:2])]\n"
        "a.datas = [row for row in a.datas if not any(_external_vc.search(n) for n in row[:2])]\n"
        + marker,
        1,
    )


def assert_external_vc_archive(executable):
    """Check the pinned unsigned onefile layout's inventory without extracting it."""
    invalid = "Expected a valid unsigned PyInstaller onefile CArchive"
    # Windows builds append the PKG to the PE and leave its cookie at EOF;
    # accepting a cookie found by scanning backwards would allow trailing data.
    with executable.open("rb") as stream:
        length = stream.seek(0, os.SEEK_END)
        stream.seek(0)
        if length < 2 + _CARCHIVE_COOKIE.size or stream.read(2) != b"MZ":
            raise ValueError(invalid)
        stream.seek(-_CARCHIVE_COOKIE.size, os.SEEK_END)
        magic, size, toc_offset, toc_size, _, library = _CARCHIVE_COOKIE.unpack(
            stream.read(_CARCHIVE_COOKIE.size)
        )
        library = library.rstrip(b"\0")
        if (
            magic != _CARCHIVE_MAGIC
            or size > length - 2
            or size != toc_offset + toc_size + _CARCHIVE_COOKIE.size
            or not 0 < toc_size <= 16 * 1024 * 1024
            or not re.fullmatch(rb"python3[0-9]+t?\.dll", library, re.IGNORECASE)
        ):
            raise ValueError(invalid)
        stream.seek(length - size + toc_offset)
        toc = stream.read(toc_size)
    if len(toc) != toc_size:
        raise ValueError(invalid)
    python_name = library.decode("ascii").lower()
    position = 0
    has_pyz = has_python = False
    while position < toc_size:
        if toc_size - position < _CARCHIVE_ENTRY.size:
            raise ValueError(invalid)
        entry, offset, stored, raw, compressed, kind = _CARCHIVE_ENTRY.unpack_from(toc, position)
        end = position + entry
        if (
            entry <= _CARCHIVE_ENTRY.size or entry % 16 or end > toc_size
            or offset + stored > toc_offset or compressed not in (0, 1)
            or (not compressed and stored != raw)
            or kind not in (b"b", b"m", b"M", b"n", b"o", b"s", b"x", b"z")
        ):
            raise ValueError(invalid)
        name, separator, padding = toc[position + _CARCHIVE_ENTRY.size:end].partition(b"\0")
        if not name or not separator or padding.strip(b"\0"):
            raise ValueError(invalid)
        name = name.decode("utf-8")
        if is_external_vc_component(name):
            raise ValueError("External Visual C++ component in frozen archive: " + name)
        has_python |= kind == b"b" and name.casefold() == python_name
        has_pyz |= kind == b"z" and name == "PYZ.pyz"
        position = end
    if not has_python or not has_pyz:
        raise ValueError(invalid)


def assets(root):
    result = []
    for name in ASSETS:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing/non-regular allowed asset: " + name)
        result.append((path, str(Path(name).parent)))
    return result


def clean_environment():
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "PYINSTALLER"))
        and key.upper() not in ("TCL_LIBRARY", "TK_LIBRARY", "VIRTUAL_ENV")
    }
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
    return environment


def version_file(path):
    number = r"(0|[1-9][0-9]*)"
    match = re.fullmatch(
        rf"{number}\.{number}\.{number}(?:-rc\.([1-9][0-9]*))?", APP_VERSION
    )
    if not match:
        raise ValueError("Use SemVer MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-rc.N")
    main = tuple(int(value) for value in match.groups()[:3])
    # Stable sorts after every RC within the Windows four-word version resource.
    revision = int(match[4]) if match[4] is not None else 65535
    if (
        any(value > 65535 for value in main)
        or not 1 <= revision <= 65535
        or match[4] is not None
        and revision == 65535
    ):
        raise ValueError("SemVer must fit the Windows four-word version resource")
    parts = (*main, revision)
    number = ".".join(map(str, parts))
    text = (
        """VSVersionInfo(
 ffi=FixedFileInfo(filevers=(0,1,0,1), prodvers=(0,1,0,1), mask=0x3f, flags=0x0,
                  OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
 kids=[StringFileInfo([StringTable('040904b0',[
  StringStruct('FileDescription','HWP Review'),
  StringStruct('FileVersion','0.1.0.1'),
  StringStruct('InternalName','HWPReview'),
  StringStruct('OriginalFilename','HWPReview.exe'),
  StringStruct('ProductName','HWP Review'),
  StringStruct('ProductVersion','"""
        + APP_VERSION
        + """')])]),
  VarFileInfo([VarStruct('Translation',[1033,1200])])])
"""
    )
    text = text.replace("(0,1,0,1)", str(parts)).replace("'0.1.0.1'", repr(number))
    if match[4] is not None:
        text = text.replace("flags=0x0", "flags=0x2")
    path.write_text(text, encoding="utf-8")


def build_record(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RuntimeError(
            "Build-only JSON record missing or malformed: " + str(path)
        ) from error
    if not isinstance(data, dict):
        raise ValueError("Build-only record must be an object")
    return data


def frozen_self_test(executable, copy_to):
    copy_to.mkdir()
    copied = copy_to / "HWPReview.exe"
    shutil.copyfile(executable, copied)
    output = copy_to / "새 가상 시험 결과"
    result = subprocess.run(
        [str(copied), "--self-test-dir", str(output)],
        cwd=copy_to,
        env=clean_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    report_path = output / "SELF-TEST-ONLY.json"
    if result.returncode or not report_path.is_file():
        detail = (
            report_path.read_text(encoding="utf-8")
            if report_path.is_file()
            else "No self-test report"
        )
        raise RuntimeError(
            f"Frozen EXE self-test failed ({result.returncode}): {detail}"
        )
    report = build_record(report_path)
    return validate_frozen_report(report)


def validate_frozen_report(report):
    if (
        report.get("status") != "passed"
        or report.get("version") != APP_VERSION
        or report.get("frozen") is not True
        or report.get("native_export_blocked") is not True
        or report.get("workspace_unchanged") is not True
        or report.get("runtime_modules_bundled") is not True
    ):
        raise RuntimeError("Incomplete frozen-EXE self-test evidence")
    native = report.get("native_ui_payload")
    if (
        not isinstance(native, dict)
        or set(native) != {
            "verified", "framework_dependent", "runtime_bundled",
            "runtime_requirement", "files_sha256", "ui_exercised",
        }
        or native.get("verified") is not True
        or native.get("framework_dependent") is not True
        or native.get("runtime_bundled") is not False
        or native.get("ui_exercised") is not False
        or native.get("runtime_requirement") != native_payload.RUNTIME_REQUIREMENT
        or not isinstance(native.get("files_sha256"), dict)
        or set(native["files_sha256"]) != set(native_payload.PAYLOAD_FILES)
        or any(not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
               for value in native["files_sha256"].values())
    ):
        raise RuntimeError("Incomplete or inconsistent frozen native UI payload evidence")
    expected_debug = {
        "callback_visible_without_console",
        "wheel_preserves_contract",
        "native_review_page_renders",
        "source_failure_revokes_ready",
        "shutdown_bounded_without_forced_close",
    }
    debug = report.get("debug_checks", {})
    if set(debug) != expected_debug or not all(
        value is True for value in debug.values()
    ):
        raise RuntimeError("Incomplete frozen-EXE debug regression evidence")
    project = report.get("project_checks", {})
    expected_project = {
        "empty_folder",
        "exclusive_copy",
        "import_not_prepared",
        "declined_prepare_no_native",
    }
    if set(project) != expected_project or not all(
        value is True for value in project.values()
    ):
        raise RuntimeError("Incomplete frozen empty-folder workflow evidence")
    usability = report.get("usability_checks", {})
    expected_usability = {
        "advanced_collapsed",
        "document_picker_copy",
        "single_next_action",
        "progress_nonterminal",
        "declined_stop_noop",
        "safe_stop_no_cleanup",
    }
    if set(usability) != expected_usability or not all(
        value is True for value in usability.values()
    ):
        raise RuntimeError("Incomplete frozen usability regression evidence")
    library = report.get("library_checks", {})
    expected_library = {
        "readonly_listing",
        "filtered_states",
        "revalidated_open",
        "saved_notes_export",
        "no_overwrite",
    }
    if set(library) != expected_library or not all(
        value is True for value in library.values()
    ):
        raise RuntimeError("Incomplete frozen library/export regression evidence")
    text = report.get("text_proposal_checks", {})
    expected_text = {
        "ui_available",
        "unapproved_save",
        "strict_block_no_fallback",
        "conflict_preserves_plan",
        "source_unchanged",
    }
    if set(text) != expected_text or not all(value is True for value in text.values()):
        raise RuntimeError("Incomplete frozen manual proposal regression evidence")
    apply = report.get("text_apply_checks", {})
    expected_apply = {
        "ui_apply_control",
        "unapproved_never_starts",
        "unsafe_request_denied",
        "invalid_worker_cannot_export",
        "immutable_inputs",
    }
    if set(apply) != expected_apply or not all(
        value is True for value in apply.values()
    ):
        raise RuntimeError("Incomplete frozen bounded text-apply evidence")
    downloads = report.get("result_download_checks", {})
    expected_downloads = {
        "ui_control",
        "token_required",
        "verified_snapshot_matches",
        "drift_blocked_no_reseal",
        "no_approval_or_native",
    }
    if set(downloads) != expected_downloads or not all(
        value is True for value in downloads.values()
    ):
        raise RuntimeError("Incomplete frozen result snapshot evidence")
    recovery = report.get("recovery_checks", {})
    expected_recovery = {
        "roundtrip_data_only",
        "cas_conflict",
        "clear_keeps_history",
        "no_document_or_approval",
        "ui_control",
    }
    if set(recovery) != expected_recovery or not all(
        value is True for value in recovery.values()
    ):
        raise RuntimeError("Incomplete frozen recovery snapshot evidence")
    workflow = report.get("workflow_checks", {})
    expected_workflow = {
        "note_hash_bound",
        "evidence_snapshot",
        "changed_evidence_blocked",
        "no_document_or_submission",
    }
    if set(workflow) != expected_workflow or not all(
        value is True for value in workflow.values()
    ):
        raise RuntimeError("Incomplete frozen batch workflow evidence")
    pi = report.get("pi_checks", {})
    expected_pi = {
        "ui_consent_controls",
        "stream_inactive_without_send",
        "token_origin_required",
        "closed_session_and_scope",
        "no_sdk_profile_or_model",
        "no_document_approval_or_apply",
    }
    if set(pi) != expected_pi or not all(value is True for value in pi.values()):
        raise RuntimeError("Incomplete frozen Pi consent/scope boundary evidence")
    expected_report = {
        "unsigned_inputs_prepared",
        "runtime_and_scope_bound",
        "raw_frozen_template_blocked",
        "owned_desktop_session_ready",
        "unpermitted_draft_blocked",
        "document_workspace_ready",
        "unpermitted_request_blocked",
        "no_model_native_or_permission",
    }
    report_checks = report.get("report_checks", {})
    if set(report_checks) != expected_report or not all(
        value is True for value in report_checks.values()
    ):
        raise RuntimeError("Incomplete frozen report runtime boundary evidence")
    return report


def promote_release(release, dist, artifact_set):
    if artifact_set.parent.is_symlink():
        raise ValueError("Release-set folder must not be a symlink")
    if artifact_set.exists():
        raise FileExistsError("Refusing to replace an existing release set")
    artifact_set.parent.mkdir(parents=True, exist_ok=True)
    # Publish one COMPLETE directory on the same volume before updating optional
    # convenience copies. Its manifest/checksums always refer to this exact set.
    os.replace(release, artifact_set)
    # Only the versioned artifact-set/ZIP is an installable closed directory.
    # Legacy top-level convenience copies intentionally remain EXE/archive-only.
    files = [artifact_set / "HWPReview.exe", artifact_set / "BUILD.json"] + [
        path for path in artifact_set.iterdir()
        if path.is_file() and (path.suffix == ".zip" or path.name.endswith(".zip.sha256"))
    ]
    for file in files:
        staged = dist / ("." + file.name + ".checked-part")
        if staged.is_symlink():
            raise ValueError("Convenience output must not be a symlink")
        shutil.copyfile(file, staged)
        # EXE first: if an existing app keeps it locked, no checksum/manifest
        # convenience copy is replaced. Never force-close that app or retry.
        os.replace(staged, dist / file.name)


def validate_native_evidence(evidence, executable, *, schema=1):
    """READ ONLY exact toy/worker evidence; not a signature or submission approval."""
    from hwp_contract_apply import PROPOSALS, contract_gate
    from hwp_text_apply import real_path

    import hwp_review as r

    if type(schema) is not int or schema not in (1, 3, 4):
        raise ValueError("Only schema1/3/4 Native evidence is accepted")
    evidence = real_path(evidence).resolve(strict=True)
    lab = evidence.parent
    prefix, filename = (
        ("toy-text-apply-", "TOY-ONLY-text-apply-verification.json")
        if schema == 1
        else ("toy-contract-", "TOY-ONLY-contract-verification.json")
    )
    if (
        lab.parent != ROOT / "work"
        or not lab.name.startswith(prefix)
        or evidence.name != filename
        or evidence.stat().st_size > 1024 * 1024
    ):
        raise ValueError("Promotion requires a bounded actual NEW-TOY test report")
    data = r.read_json(evidence)
    true_fields = (
        "test_only_not_deliverable",
        "human_visual_review_required",
        "actual_parent_engine_and_http",
        "test_consent_only_new_unsigned_fixture",
        "source_unchanged",
        "approval_and_baselines_unchanged",
        "duplicate_blocked",
        "unapproved_blocked_before_native",
        "stale_plan_blocked_before_native",
        "download_bytes_verified",
    )
    if (
        not isinstance(data, dict)
        or any(data.get(name) is not True for name in true_fields)
        or data.get("real_report_modified") is not False
        or data.get("status") != "checks_passed_needs_human_visual_review"
        or data.get("actual_worker") != "frozen"
        or data.get("executable_sha256") != build_source.sha256(executable)
        or (
            schema != 1
            and (type(data.get("schema")) is not int or data["schema"] != schema)
        )
        or data.get("error")
    ):
        raise ValueError(
            "Native toy evidence is failed, unbound, source-only or for another EXE"
        )
    workspace = lab / ("작업 폴더" if schema == 1 else "owned-project") / "_review"
    if not isinstance(data.get("workspace"), str) or Path(data["workspace"]) != workspace:
        raise ValueError("Toy workspace escapes the owned lab")
    real_path(workspace)
    declared = r.result_json(workspace, "manifest.json") or {}
    source_name = declared.get("source")
    allowed_sources = (
        {"TOY-ONLY-unsigned-text.hwp"} if schema == 1
        else {"TOY-ONLY-unsigned-contract.hwp", "TOY-ONLY-reference-source.hwp"}
    )
    source = Path(source_name) if isinstance(source_name, str) else Path()
    if source.parent != workspace.parent or source.name not in allowed_sources:
        raise ValueError("Native evidence must use the owned unsigned TOY source copy")
    for path in (source, workspace / "baseline.hwpx", workspace / "baseline.pdf"):
        real_path(path)
    manifest = r.verify_baseline(workspace)
    if (
        manifest["source_audit"]["sha256"] != data.get("source_sha256_before")
        or manifest["protected_pages"]
        or manifest["objects"].get("pic", 0)
        or len(manifest["pages"]) != 1
    ):
        raise ValueError("Native TOY source or allowed page count changed")
    plan = r.result_json(workspace, "proposal.json" if schema == 1 else PROPOSALS[schema])
    if (
        not isinstance(plan, dict)
        or type(plan.get("schema")) is not int
        or plan["schema"] != schema
        or r.plan_hash(plan) != data.get("plan_sha256")
    ):
        raise ValueError("Toy current exact plan changed")
    selected = r.approved_plan(
        workspace, plan, manifest
    )  # synthetic TOY UI consent ONLY
    if r.apply_state(workspace, manifest)["locked"]:
        raise ValueError("Native toy still has unresolved ownership")
    run = Path(data["run"]) if isinstance(data.get("run"), str) else Path()
    if run.parent != workspace / "runs" or not r.RUN_ID.fullmatch(run.name):
        raise ValueError("Toy run escapes workspace")
    real_path(run)
    directory, version = r.review_version(workspace, manifest, run.name)
    if (
        directory != run
        or version["status"] != "checks_passed_needs_human_visual_review"
        or version.get("artifact_integrity") != "verified"
        or r.plan_hash(r.result_json(run, "plan.json")) != data["plan_sha256"]
    ):
        raise ValueError(
            "Native toy artifacts are not a current integrity-confirmed pass"
        )
    verification = r.result_json(run, "verification.json") or {}
    layout = verification.get("layout_gate")
    if (
        verification.get("status") != "checks_passed_needs_human_visual_review"
        or verification.get("structural_gate") is not True
        or verification.get("rendered_page_text_exact") is not True
        or verification.get("embedded_binaries_equal") is not True
        or verification.get("source_unchanged") is not True
        or not isinstance(layout, dict)
        or layout.get("passed") is not True
    ):
        raise ValueError("Incomplete actual Native gates")
    # Recheck both owned Native phases without reopening COM or resealing results.
    kind = "text" if schema == 1 else "contract"
    scope_sha = r.plan_hash(r.native_approval_scope(plan)) if schema != 1 else None
    for phase in (0, 1):
        reply = r.result_json(run, f"{kind}-export-reply-{phase}.json") or {}
        native = reply.get("native")
        owner = native.get("owned_session") if isinstance(native, dict) else None
        if (
            reply.get("ok") is not True
            or type(reply.get("phase")) is not int
            or reply["phase"] != phase
            or reply.get("plan_sha256") != data["plan_sha256"]
            or not isinstance(native, dict)
            or type(native.get("native_pages")) is not int
            or native["native_pages"] != len(manifest["pages"])
            or not isinstance(owner, dict)
            or owner.get("exit_confirmed") is not True
            or owner.get("status") != "owned_exit_confirmed"
            or (
                schema != 1
                and (
                    type(reply.get("schema")) is not int
                    or reply["schema"] != schema
                    or reply.get("approval_scope_sha256") != scope_sha
                )
            )
        ):
            raise ValueError("Missing exact owned Native worker completion")
        edit_owner = native.get("edit_owned_session")
        if schema == 3 and phase == 0 and (
            native.get("native_verification_origin") != "saved_hwp_copy"
            or not isinstance(edit_owner, dict)
            or edit_owner.get("exit_confirmed") is not True
            or edit_owner.get("status") != "owned_exit_confirmed"
            or native.get("saved_export_copy_sha256") != r.file_hash(run / "candidate.hwp")
        ):
            raise ValueError("Missing original row-minima saved-HWP evidence")

    baseline, pdf = workspace / "baseline.pdf", run / "candidate.pdf"
    masks = {}
    if schema == 1:
        r.structural_gate(
            workspace / "baseline.hwpx", run / "candidate.hwpx", plan["edits"]
        )
    else:
        reopened = real_path(run / "candidate-reopened.hwpx")
        saved = verification.get("saved_hwp_reopen_export")
        if (
            not isinstance(saved, dict)
            or not reopened.is_file()
            or native.get("reopened_hwpx_sha256") != r.file_hash(reopened)
            or saved.get("reopened_hwpx_sha256") != native["reopened_hwpx_sha256"]
        ):
            raise ValueError("Missing exact saved-HWP reopened HWPX")
        initial = contract_gate(workspace, run / "candidate.hwpx", plan, selected)
        replayed = contract_gate(workspace, reopened, plan, selected)
        if (
            initial != verification.get("native_contract")
            or replayed != native.get("saved_hwp_contract")
            or replayed != saved.get("parent_saved_hwp_contract")
        ):
            raise ValueError("Saved-HWP Native structure proof no longer matches")
    if schema == 4:
        for field in selected:
            masks.setdefault(field["page_hint"], []).extend(field["boxes"])
        text_exact = r.hwp_fill_plan.rendered_text_gate(baseline, pdf, selected)
    else:
        for edit, anchor in zip(plan["edits"], selected):
            masks.setdefault(anchor["page_hint"], []).extend(
                [edit["region"]] if schema == 3 else anchor["boxes"]
            )
        text_exact = r.rendered_text_gate(baseline, pdf, plan["edits"], selected)
    if (
        not text_exact
        or not r.compare_pdfs(baseline, pdf, masks)["passed"]
        or r.hwp_audit(run / "candidate.hwp")["embedded_binary_hashes"]
        != manifest["source_audit"]["embedded_binary_hashes"]
    ):
        raise ValueError("Read-only Native gates no longer pass")
    return data


def validate_checked_candidate(release, evidence, layout_evidence, fill_evidence):
    """Verify all enabled Native scopes for one exact candidate without promotion."""
    from hwp_contract_apply import QUALIFIED_SCHEMAS
    from hwp_text_apply import real_path

    release = real_path(release).resolve(strict=True)
    if release.name != "checked-release" or release.parent.parent != ROOT / "build":
        raise ValueError("Select an existing staged candidate inside build")
    executable = release / "HWPReview.exe"
    metadata = build_record(real_path(release / "BUILD.json"))
    if metadata.get("version") != APP_VERSION or build_source.sha256(
        real_path(executable)
    ) != metadata.get("executable_sha256"):
        raise ValueError("Candidate executable/version no longer matches its build")
    validate_frozen_report(metadata.get("self_test", {}))
    if set(metadata.get("source_sha256", {})) != set(SOURCE_FILES) or set(
        metadata.get("assets_sha256", {})
    ) != set(ASSETS):
        raise ValueError("Candidate input snapshot is incomplete")
    expected_inputs = (
        set(SOURCE_FILES)
        | set(ASSETS)
        | {"packaging/licenses/" + name for name in LEGAL_FILES}
    )
    inputs = metadata.get("input_sha256", {})
    if set(inputs) != expected_inputs or any(
        inputs[name] != value
        for name, value in {
            **metadata["source_sha256"],
            **metadata["assets_sha256"],
        }.items()
    ):
        raise ValueError("Candidate full build-input snapshot is incomplete")
    build_source.verify_inputs(ROOT, inputs)
    source_record = metadata.get("source_archive", {})
    source_name = f"HWPReview-source-{APP_VERSION}.zip"
    if source_record.get("file") != source_name:
        raise ValueError("Candidate exact source archive is missing")
    source_archive = real_path(release / source_name)
    if build_source.sha256(source_archive) != source_record.get(
        "sha256"
    ) or source_archive.stat().st_size != source_record.get("bytes"):
        raise ValueError("Candidate source archive changed")
    build_source.verify_source_archive(source_archive, inputs, version=APP_VERSION)
    install_bundle.verify_payload(release, version=APP_VERSION)
    assert_external_vc_archive(executable)
    if (
        any(path is None for path in (evidence, layout_evidence, fill_evidence))
        or QUALIFIED_SCHEMAS != (3, 4)
        or metadata.get("qualified_native_schemas") != list(QUALIFIED_SCHEMAS)
    ):
        raise ValueError("Exact frozen evidence for schemas 1, 3 and 4 is required")
    validate_native_evidence(evidence, executable, schema=1)
    validate_native_evidence(layout_evidence, executable, schema=3)
    validate_native_evidence(fill_evidence, executable, schema=4)
    build_source.verify_inputs(ROOT, inputs)
    expected_set = ROOT / "dist" / "releases" / APP_VERSION / release.parent.name
    if metadata.get("artifact_set") != str(
        expected_set.relative_to(ROOT / "dist")
    ).replace("\\", "/"):
        raise ValueError("Candidate artifact-set identity changed")
    return release, expected_set, metadata["executable_sha256"]


def promote_checked_candidate(release, evidence, layout_evidence=None, fill_evidence=None):
    """Explicit local promotion AFTER exact candidate tests, never automatic build."""
    release, expected_set, digest = validate_checked_candidate(
        release, evidence, layout_evidence, fill_evidence
    )
    promote_release(release, ROOT / "dist", expected_set)
    return {
        "exe": "dist/HWPReview.exe",
        "version": APP_VERSION,
        "sha256": digest,
        "native_evidence": str(Path(evidence)),
        "layout_evidence": str(Path(layout_evidence)),
        "fill_evidence": str(Path(fill_evidence)),
        "published": False,
        "local_promoted": True,
    }


def build():
    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64"):
        raise RuntimeError(
            "Build this EXE using Windows x64 Python. No automatic cross-build or installation."
        )
    for package, expected in EXPECTED.items():
        if version(package) != expected:
            raise RuntimeError("Use the pinned build/runtime dependencies: " + package)
    stamp = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    work = ROOT / "build" / stamp
    dist = ROOT / "dist"
    for path in (ROOT / "build", dist):
        if path.is_symlink():
            raise ValueError("Generated build/dist folders must not be symlinks")
    work.mkdir(parents=True)
    dist.mkdir(exist_ok=True)
    for path in dist.iterdir():
        if path.is_symlink():
            raise ValueError("Generated distribution outputs must not be symlinks")
    if any(is_external_vc_component(name) for name in (*SOURCE_FILES, *ASSETS, *LEGAL_FILES)):
        raise RuntimeError("External Visual C++ components cannot be public build inputs")
    source = work / "source-snapshot"
    inputs = build_source.snapshot_inputs(
        ROOT,
        source,
        (
            *SOURCE_FILES,
            *ASSETS,
            *("packaging/licenses/" + name for name in LEGAL_FILES),
        ),
    )
    legal = work / "licenses"
    license_bundle.write_licenses(source, legal, LEGAL_FILES, app_version=APP_VERSION)
    versions = work / "windows-version.txt"
    version_file(versions)
    native_ui = work / "native-ui"
    native_manifest = build_native.build_native(source, native_ui)
    if native_manifest["source_sha256"] != {
        name: inputs[name] for name in native_payload.NATIVE_SOURCE_FILES
    }:
        raise ValueError("Native UI does not match the exact application input snapshot")
    makespec = [
        sys.executable,
        "-m",
        "PyInstaller.utils.cliutils.makespec",
        "--onefile",
        "--windowed",
        "--noupx",
        "--name",
        "HWPReview",
        "--specpath",
        str(work),
        "--paths",
        str(source / "scripts"),
        "--version-file",
        str(versions),
    ]
    for asset, destination in assets(source):
        makespec += ["--add-data", str(asset) + os.pathsep + destination]
    makespec += ["--add-data", str(legal) + os.pathsep + "licenses"]
    makespec += ["--add-data", str(native_ui) + os.pathsep + "native-ui"]
    for name in HIDDEN:
        makespec += ["--hidden-import", name]
    for name in EXCLUDED:
        makespec += ["--exclude-module", name]
    makespec += [str(source / "scripts/hwp_desktop.py")]
    log = work / "build.log"
    with log.open("w", encoding="utf-8") as stream:
        environment = clean_environment()
        subprocess.run(
            makespec,
            cwd=source,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=True,
        )
        spec = work / "HWPReview.spec"
        spec.write_text(
            spec_with_external_vc_runtime(spec.read_text(encoding="utf-8")),
            encoding="utf-8",
            newline="\n",
        )
        subprocess.run(
            [
                sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--distpath", str(work / "candidate"), "--workpath", str(work / "pyinstaller"),
                str(spec),
            ],
            cwd=source,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=True,
        )
    executable = work / "candidate/HWPReview.exe"
    import pefile

    with pefile.PE(str(executable), fast_load=True) as image:
        header, optional = image.FILE_HEADER, image.OPTIONAL_HEADER
        if header is None or optional is None:
            raise RuntimeError("Candidate PE headers are missing")
        pe = {
            "machine": hex(getattr(header, "Machine")),
            "subsystem": getattr(optional, "Subsystem"),
            "certificate_bytes": getattr(optional, "DATA_DIRECTORY")[4].Size,
        }
    if pe != {"machine": "0x8664", "subsystem": 2, "certificate_bytes": 0}:
        raise RuntimeError("Expected unsigned x64 Windows GUI executable")
    assert_external_vc_archive(executable)
    # The archive tool provides a bounded module/data inventory. Never bundle
    # whole source/work/example folders. Only a narrow test module is embedded.
    inventory = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller.utils.cliutils.archive_viewer",
            "-r",
            "-l",
            str(executable),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    ).stdout
    (work / "archive-inventory.txt").write_text(inventory, encoding="utf-8")
    forbidden = (
        "native_hwp",
        "approved-plan.json",
        "FilePathCheckerModuleExample.dll",
        "보안모듈",
    )
    if any(name.casefold() in inventory.casefold() for name in forbidden):
        raise RuntimeError(
            "Forbidden module/data appears in frozen archive; do not ship"
        )
    report = frozen_self_test(executable, work / "frozen-self-test")
    native_payload.verify_payload(native_ui, source_root=source)
    if report["native_ui_payload"]["files_sha256"] != native_manifest["files_sha256"]:
        raise RuntimeError("Embedded native UI differs from the exact compiled source payload")
    build_source.verify_inputs(ROOT, inputs)
    artifact_set = dist / "releases" / APP_VERSION / stamp
    from hwp_contract_apply import QUALIFIED_SCHEMAS

    info = {
        "version": APP_VERSION,
        "artifact_set": str(artifact_set.relative_to(dist)).replace("\\", "/"),
        "purpose": "unsigned versioned candidate; not qualified or published",
        "license": APP_LICENSE,
        "source_url": SOURCE_URL,
        "source_provenance": "exact hash-pinned working-tree build snapshot; not a published Git tag",
        "input_sha256": inputs,
        "redistribution_ready": False,
        "qualified_native_schemas": list(QUALIFIED_SCHEMAS),
        "platform_tested": "Windows 11 x64",
        "architecture": platform.machine(),
        "python": sys.version.split()[0],
        "tools": {name: version(name) for name in EXPECTED},
        "code_signed": False,
        "pe": pe,
        "hancom_native_tested": False,
        "external_upload": False,
        "preparation_enabled": True,
        "bounded_text_apply_enabled": True,
        "generic_native_export_enabled": False,
        "registered_security_module_required": True,
        "security_install_enabled": True,  # Conditional, consent-based prerequisite setup only.
        "report_harness_enabled": True,
        "report_runtime_contract": "hwp-report-runtime-v2",
        "native_ui": {
            "contract": native_payload.CONTRACT,
            "manifest": native_manifest,
            "manifest_sha256": build_source.sha256(native_ui / "manifest.json"),
            "embedded_in_parent_executable": True,
            "framework_dependent": True,
            "runtime_bundled": False,
            "runtime_install_enabled": False,
            "ui_exercised": False,
            "hancom_qualification": False,
        },
        "other_pc_tested": False,
        "executable_sha256": build_source.sha256(executable),
        "executable_bytes": executable.stat().st_size,
        "source_sha256": {name: inputs[name] for name in SOURCE_FILES},
        "assets_sha256": {name: inputs[name] for name in ASSETS},
        "licenses_sha256": {path.name: build_source.sha256(path) for path in legal.iterdir()},
        "self_test": report,
    }
    # Prepare ALL release files before promotion: ZIP/disk failures during
    # preparation leave the last working distribution untouched.
    release = work / "checked-release"
    release.mkdir()
    shutil.copyfile(executable, release / "HWPReview.exe")
    executable = release / "HWPReview.exe"
    source_archive = release / f"HWPReview-source-{APP_VERSION}.zip"
    info["source_archive"] = build_source.write_source_archive(
        source, inputs, source_archive, version=APP_VERSION
    )
    manifest = release / "BUILD.json"
    manifest.write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    payload = install_bundle.write_payload(release, source, legal, version=APP_VERSION)
    payload_names = sorted([*payload["files"], install_bundle.MANIFEST])
    checksums = release / "SHA256SUMS.txt"
    with checksums.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(
            build_source.sha256(release / name) + "  " + name + "\n"
            for name in payload_names
        ))
    archive = release / f"HWPReview-win-x64-{APP_VERSION}.zip"
    with zipfile.ZipFile(
        archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as package:
        for name in [*payload_names, "SHA256SUMS.txt"]:
            package.write(release / name, name)
    (release / (archive.name + ".sha256")).write_text(
        build_source.sha256(archive) + "  " + archive.name + "\n", encoding="utf-8"
    )
    install_bundle.verify_payload(release, version=APP_VERSION)
    build_source.verify_inputs(ROOT, inputs)
    result = {
        "candidate_dir": str(release.relative_to(ROOT)),
        "exe": str(executable.relative_to(ROOT)),
        "zip": str(archive.relative_to(ROOT)),
        "source_zip": str(source_archive.relative_to(ROOT)),
        "size_mb": round(executable.stat().st_size / 1024 / 1024, 1),
        "sha256": build_source.sha256(executable),
        "frozen_self_test": "passed",
        "published": False,
        "local_promoted": False,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--candidate-only",
        action="store_true",
        help="Default: stage and self-test, never promote",
    )
    modes.add_argument(
        "--promote",
        type=Path,
        help="Explicit checked-release folder; exact frozen TOY evidence required",
    )
    modes.add_argument(
        "--verify-release",
        type=Path,
        help="Verify an exact candidate and its Native evidence without writing or promoting",
    )
    parser.add_argument(
        "--native-evidence",
        type=Path,
        help="Existing actual frozen toy report, never a real report",
    )
    parser.add_argument(
        "--layout-evidence",
        type=Path,
        help="Exact frozen schema3 NEW-TOY report; never a real report",
    )
    parser.add_argument(
        "--fill-evidence",
        type=Path,
        help="Exact frozen schema4 NEW-TOY report; never a real report",
    )
    args = parser.parse_args()
    evidence = (args.native_evidence, args.layout_evidence, args.fill_evidence)
    candidate = args.promote if args.promote is not None else args.verify_release
    if candidate is None:
        if any(path is not None for path in evidence):
            parser.error("Native evidence requires --verify-release or --promote")
        build()
    else:
        if any(path is None for path in evidence):
            parser.error("Exact frozen evidence for schemas 1, 3 and 4 is required")
        if args.promote is not None:
            result = promote_checked_candidate(candidate, *evidence)
        else:
            release, _, digest = validate_checked_candidate(candidate, *evidence)
            result = {
                "version": APP_VERSION,
                "candidate_dir": str(release.relative_to(ROOT)),
                "sha256": digest,
                "native_schemas_verified": [1, 3, 4],
                "published": False,
                "local_promoted": False,
            }
        print(json.dumps(result, ensure_ascii=False))
