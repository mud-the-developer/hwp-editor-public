"""Private, single-attempt installed smoke. This is not Native/schema or release approval."""

from contextlib import ExitStack
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import uuid
import zipfile

import hwp_install_bundle as bundle
import hwp_omp_tui as omp
import hwp_setup as setup
from hwp_blank_document import blank_document
from hwp_native_payload import locate_dotnet
from hwp_resources import APP_VERSION

ROOT = Path(__file__).absolute().parents[1]
MIB = 1024 * 1024
KIT_LIMIT = 256 * MIB
EXPANDED_LIMIT = 384 * MIB
HASH = re.compile(r"[0-9a-f]{64}\Z")
KIT_NAME = f"HWPReview-win-x64-{APP_VERSION}.zip"
SOURCE_NAME = f"HWPReview-source-{APP_VERSION}.zip"


def _path(value):
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("Parent traversal in smoke path")
    path = path.absolute()
    if os.name == "nt" and (not re.fullmatch(r"[A-Za-z]:", path.drive) or any(
            os.path.isreserved(part) for part in path.parts[1:])):
        raise ValueError("A plain local drive path is required")
    if os.name == "nt" and ctypes.windll.kernel32.GetDriveTypeW(path.anchor) != 3:
        raise ValueError("Use a fixed local drive, not a network/removable volume")
    return setup._unlinked(path)


def _digest(path):
    with setup._locked_read(path) as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(raw):
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=setup._unique_object)


def _read_json(path):
    with setup._locked_read(path) as stream:
        if os.fstat(stream.fileno()).st_size > 2 * MIB:
            raise ValueError("Oversized smoke record")
        return _json(stream.read())


def _record(path, data):
    setup._write_new(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def _transport(stream, destination, exe_sha256):
    # The shipped non-ZIP64 archive has no comment. Bound the central directory
    # before ZipFile allocates ZipInfo objects, not merely after infolist().
    size = os.fstat(stream.fileno()).st_size
    if not 22 <= size <= KIT_LIMIT:
        raise ValueError("Installer transport size outside smoke limit")
    stream.seek(-22, os.SEEK_END)
    sig, disk, start, local, count, cd_size, offset, comment = struct.unpack("<4s4H2IH", stream.read(22))
    if (sig != b"PK\x05\x06" or disk or start or local != count or not 1 <= count <= 514
            or not 0 < cd_size <= MIB or offset + cd_size + 22 != size or comment):
        raise ValueError("Not a bounded flat installer transport")
    with zipfile.ZipFile(stream) as archive:
        members = archive.infolist()
        if len(members) != count or sum(info.file_size for info in members) > EXPANDED_LIMIT:
            raise ValueError("Expanded installer transport outside smoke limit")
        names = set()
        for info in members:
            name = info.filename
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if (info.orig_filename != name or info.extra or info.comment or info.flag_bits & ~0x800
                    or file_type not in (0, stat.S_IFREG) or info.external_attr & 0x410
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or name.casefold() in names or len(name) > 220):
                raise ValueError("Linked, ambiguous or duplicate installer member")
            names.add(name.casefold())
            limit = 192 * MIB if name in ("HWPReview.exe", SOURCE_NAME) else 8 * MIB
            if name in (bundle.MANIFEST, "BUILD.json", "SHA256SUMS.txt"):
                limit = 2 * MIB
            if not 0 < info.file_size <= limit:
                raise ValueError("Installer member size outside smoke limit: " + name)
        manifest = _json(archive.read(bundle.MANIFEST))
        build = _json(archive.read("BUILD.json"))
        if (set(build) != {"version", "code_signed", "redistribution_ready", "input_sha256",
                          "licenses_sha256", "executable_sha256", "source_archive"}
                or build["version"] != APP_VERSION or build["executable_sha256"] != exe_sha256
                or build["redistribution_ready"] is not False or build["code_signed"] is not False
                or not isinstance(build["licenses_sha256"], dict)):
            raise ValueError("Not the sanitized public build of the requested executable")
        expected = bundle.payload_names(APP_VERSION, build["licenses_sha256"])
        files = manifest["files"]
        if (manifest != {"contract": bundle.CONTRACT, "architecture": "x64", "version": APP_VERSION,
                         "code_signed": False, "redistribution_ready": False, "files": files}
                or manifest["code_signed"] is not False or manifest["redistribution_ready"] is not False
                or set(files) != expected or set(archive.namelist()) != expected | {bundle.MANIFEST, "SHA256SUMS.txt"}):
            raise ValueError("Installer membership or manifest differs from public contract")
        if (files["HWPReview.exe"]["sha256"] != exe_sha256
                or build["source_archive"] != {"file": SOURCE_NAME, **files[SOURCE_NAME]}
                or build["input_sha256"] != {src: files[name]["sha256"] for name, src in bundle.ROOT_INPUTS.items()}
                or any(digest != files["licenses/" + name]["sha256"] for name, digest in build["licenses_sha256"].items())):
            raise ValueError("Public build is not bound to the executable, source, scripts and notices")
        # Only names admitted by the closed contract above ever reach the filesystem.
        destination = _path(destination)
        destination.mkdir(mode=0o700)
        (destination / "licenses").mkdir()
        hashes = {}
        for info in members:
            name = info.filename
            sha = hashlib.sha256()
            with archive.open(info) as source, setup._new_file(destination / name) as target:
                while block := source.read(MIB):
                    target.write(block)
                    sha.update(block)
                written = target.tell()
            hashes[name] = sha.hexdigest()
            if (written != info.file_size or (name in files and (type(files[name].get("bytes")) is not int
                    or files[name] != {"bytes": written, "sha256": hashes[name]}))):
                raise ValueError("Installer member differs from manifest: " + name)
        sums = "".join(hashes[name] + "  " + name + "\n" for name in sorted(expected | {bundle.MANIFEST}))
        if hashes["SHA256SUMS.txt"] != hashlib.sha256(sums.encode()).hexdigest():
            raise ValueError("Installer checksum inventory differs")
        return {name: hashes[name] for name in expected | {bundle.MANIFEST}}


def _extract(zip_path, destination, kit_sha256, exe_sha256):
    if not HASH.fullmatch(kit_sha256) or not HASH.fullmatch(exe_sha256):
        raise ValueError("Two exact lowercase SHA-256 identities are required")
    path = _path(zip_path)
    if path.name != KIT_NAME:
        raise ValueError("Expected the versioned public installer ZIP")
    with setup._locked_read(path) as stream:
        if (os.fstat(stream.fileno()).st_size > KIT_LIMIT
                or hashlib.file_digest(stream, "sha256").hexdigest() != kit_sha256):
            raise ValueError("Installer transport SHA-256/size differs")
        return _transport(stream, destination, exe_sha256)


def _interactive():
    """Read desktop identities only. Never switch, unlock or enumerate user windows."""
    from ctypes import wintypes
    ui = ctypes.WinDLL("user32", use_last_error=True)
    ui.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    ui.OpenInputDesktop.restype = wintypes.HANDLE
    ui.GetThreadDesktop.argtypes = (wintypes.DWORD,)
    ui.GetThreadDesktop.restype = wintypes.HANDLE
    ui.GetUserObjectInformationW.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    ui.CloseDesktop.argtypes = (wintypes.HANDLE,)

    def name(desktop):
        buf, used = ctypes.create_unicode_buffer(256), wintypes.DWORD()
        if not ui.GetUserObjectInformationW(desktop, 2, buf, ctypes.sizeof(buf), ctypes.byref(used)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.value

    active = ui.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS, no switching rights
    if not active:
        raise OSError("An unlocked interactive desktop is required")
    try:
        thread = ui.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
        if name(active).casefold() != "default" or name(thread) != name(active):
            raise OSError("The smoke host must already be on its active default desktop")
    finally:
        ui.CloseDesktop(active)


def _environment(output, system):
    # Known Folder resolution needs the actual account profile, not a fabricated
    # USERPROFILE. The application and its managed OMP keep credentials/config in
    # separate LocalAppData/HOME roots. Do not inherit provider keys or shell hooks.
    profile = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, profile) != 0:
        raise OSError("Cannot resolve the genuine Windows account profile")
    user = _path(profile.value)
    if not user.is_dir():
        raise ValueError("Missing Windows profile")
    keys = ("OS", "SystemDrive", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
            "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "USERNAME", "USERDOMAIN", "SESSIONNAME",
            "DOTNET_ROOT_X64", "DOTNET_ROOT")
    env = {name: os.environ[name] for name in keys if name in os.environ}
    ps_home = system / "WindowsPowerShell/v1.0"
    env.update(USERPROFILE=str(user), HOMEDRIVE=user.drive, HOMEPATH=str(user)[len(user.drive):],
               SystemRoot=str(system.parent), WINDIR=str(system.parent), COMSPEC=str(system / "cmd.exe"),
               PATH=os.pathsep.join(map(str, (system, system.parent, ps_home))),
               PSModulePath=str(ps_home / "Modules"), HWP_REVIEW_REQUIRE_PREPARED="1", OMP_SKIP_SETUP="1")
    for key in ("LOCALAPPDATA", "APPDATA", "HOME", "TEMP"):
        root = output / key
        root.mkdir()
        env[key] = str(root)
    env["TMP"] = env["TEMP"]
    return env


def _child(output, env, tag, command, timeout):
    with setup._new_file(output / (tag + ".stdout")) as out, setup._new_file(output / (tag + ".stderr")) as err:
        child = subprocess.Popen(command, cwd=output, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        _record(output / (tag + "-started.json"), {"pid": child.pid})
        # Popen.wait never kills on timeout. Do not use subprocess.run(timeout=...).
        if code := child.wait(timeout):
            raise RuntimeError(f"{tag} exited {code}; preserve the new install, logs and processes")


def _ready(report):
    if (report.get("contract") != "hwp-requirements-status-v2" or report.get("ready") is not True
            or report.get("reboot_required") is not False or "error" in report
            or not setup.statuses_ready(report.get("components"))):
        raise ValueError("The exact EXE must report all applicable prerequisites already ready")


def _operation(report, operation, install):
    check = operation == "Check"
    if (report.get("contract") != "hwp-install-operation-v1" or report.get("operation") != operation.lower()
            or report.get("status") != ("payload_valid_no_mutation" if check else "installed")
            or type(report.get("exit_code")) is not int or report["exit_code"] != 0
            or report.get("prerequisites_checked") is not (not check) or report.get("app_launched") is not False
            or report.get("retained") != [] or Path(report["install_directory"]) != install):
        raise ValueError("Shipped installer did not perform the exact requested " + operation)


def _registry_absent(key):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
            raise FileExistsError("Smoke registration already exists")
    except FileNotFoundError:
        pass


def _installed(install, shortcuts, logs, subkey, hashes):
    import winreg
    receipt_path = install / "INSTALL-RECEIPT.json"
    receipt = _read_json(receipt_path)
    link = shortcuts / "HWP Editor.lnk"
    if (receipt.get("contract") != "hwp-user-install-receipt-v2" or receipt.get("version") != APP_VERSION
            or not re.fullmatch("[a-f0-9]{32}", receipt["install_id"]) or receipt.get("app_launch_enabled") is not False
            or Path(receipt["install_directory"]) != install or Path(receipt["shortcut_directory"]) != shortcuts
            or Path(receipt["log_directory"]) != logs or receipt.get("registry_subkey") != subkey
            or receipt.get("manifest_sha256") != hashes[bundle.MANIFEST]
            or list(shortcuts.iterdir()) != [link] or receipt.get("shortcut_sha256") != _digest(link)
            or type(receipt.get("shortcut_bytes")) is not int or receipt["shortcut_bytes"] != link.stat().st_size):
        raise ValueError("Installed receipt does not own this kit, shortcut or registration")
    root = {p.name for p in _path(install).iterdir()}
    notices = {"licenses/" + p.name for p in _path(install / "licenses").iterdir()}
    if root != {name.split("/")[0] for name in hashes} | {receipt_path.name} or notices != {
            name for name in hashes if name.startswith("licenses/")}:
        raise ValueError("Installed payload membership differs")
    for name, digest in hashes.items():
        if _digest(install / name) != digest:
            raise ValueError("Installed kit bytes differ: " + name)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
        for name, value in {"HWPReviewInstallId": receipt["install_id"], "DisplayVersion": APP_VERSION,
                            "InstallLocation": str(install), "DisplayIcon": str(install / "HWPReview.exe")}.items():
            if winreg.QueryValueEx(key, name) != (value, winreg.REG_SZ):
                raise ValueError("Installed registry identity differs: " + name)
    return _digest(receipt_path)


def _checkpoint(fixture, sha):
    root = fixture.parent / ".hwpreview"
    key = hashlib.sha256(os.path.normcase(fixture.name).encode()).hexdigest()[:16]
    history = root / "history" / key
    original = root / "originals" / (sha + ".hwpx")
    index = _read_json(history / "index.json")
    if (index.get("schema") != "hwp-live-history-v1" or index.get("relative_path") != fixture.name
            or index.get("original_sha256") != sha or Path(index["original_path"]) != original
            or len(index["checkpoints"]) != 1):
        raise ValueError("No new matching original and initial checkpoint")
    row = index["checkpoints"][0]
    if (row.get("sha256") != sha or row.get("reason") != "initial" or row.get("size") != fixture.stat().st_size
            or not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]{6}-" + sha[:12] + r"\.hwpx", row["file"])
            or any(_digest(path) != sha for path in (original, history / row["file"], fixture))):
        raise ValueError("Original/working/checkpoint bytes differ")
    return row["file"]


def _window(report, exe, workspace, managed):
    roles = {"app", "host", "wpf", "omp", "bun"}
    pins = {r["relative_path"]: r["sha256"] for r in omp._baseline()[0]["files"]}
    pids, codes = report["pids"], report["exit_codes"]
    if (report.get("contract") != "hwp-owned-installed-window-v1" or Path(report["exe"]) != exe
            or Path(report["managed"]) != managed or report["title"] != "HWP Editor — " + workspace.name
            or report["sync"] != "실시간 동기화 중" or not report["idle"].startswith("OMP · ")
            or type(report["hwnd"]) is not int or report["hwnd"] <= 0
            or set(pids) != roles or any(type(p) is not int or p <= 0 for p in pids.values())
            or len(set(pids.values())) != len(roles) or set(codes) != roles
            or any(type(code) is not int or code != 0 for code in codes.values())
            or type(report["remaining_descendants"]) is not int or report["remaining_descendants"] != 0
            or type(report.get("normal_descendant_exits")) is not int or report["normal_descendant_exits"] < 5
            or any(report[name + "_sha256"] != pins["bin/" + name + ".exe"] for name in ("omp", "bun"))):
        raise ValueError("Incomplete or foreign normal-launch observation")


def run(zip_path, output, *, kit_sha256, exe_sha256):
    """Test once on a prepared interactive Windows x64 host. Output is private and new.

    No install/launch is retried or force-closed. Keep the custom install, exact
    registration and all evidence even on success; this is not an uninstall test.
    """
    if os.name != "nt" or sys.version_info < (3, 13) or not setup._windows_x64():
        raise OSError("Interactive Windows x64 and Python 3.13+ (private mkdir ACL) required")
    output = _path(output)
    local = setup._local_app_data()
    if any(output.is_relative_to(blocked) for blocked in (local / "Programs/HWPReview", local / "HWPReview",
                                                          _path(zip_path).parent)):
        raise ValueError("Private smoke output must be outside the public kit directory and production app data")
    if output.is_relative_to(ROOT) or not output.parent.is_dir():
        raise ValueError("Use a fresh private evidence directory outside the checkout")
    output.mkdir(mode=0o700)
    kit = output / "kit"
    install, shortcuts, logs = (output / name for name in ("install", "shortcut", "install-logs"))
    sequence = uuid.uuid4().hex
    subkey = "Software\\HWPReview\\InstallerSmoke\\" + sequence
    try:
        _interactive()
        hashes = _extract(zip_path, kit, kit_sha256, exe_sha256)
        system = setup._system_directory()
        powershell = setup._system_executable(system / "WindowsPowerShell/v1.0/powershell.exe")
        env = _environment(output, system)
        args = [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
        installer = kit / "Install-HWPReview.ps1"
        options = ["-SourceDirectory", kit, "-InstallDirectory", install, "-ShortcutDirectory", shortcuts,
                   "-LogDirectory", logs, "-RegistrySubKey", subkey]
        _record(output / "scope.json", {"kit_sha256": kit_sha256, "exe_sha256": exe_sha256, "registry_subkey": subkey})
        with ExitStack() as locks:
            for name in ("Install-HWPReview.ps1", "HWPReview.exe"):
                stream = locks.enter_context(setup._locked_read(kit / name))
                if hashlib.file_digest(stream, "sha256").hexdigest() != hashes[name]:
                    raise ValueError("Extracted executable/script changed")
            if hashes[installer.name] != _digest(ROOT / "packaging" / installer.name):
                raise ValueError("Installer script differs from this public checkout")
            for op in ("Check", "Install"):
                if any(os.path.lexists(path) for path in (install, shortcuts, logs)):
                    raise FileExistsError("Smoke installation scope already exists")
                _registry_absent(subkey)
                _child(output, env, op, [*args, installer, "-Operation", op, *options], 180)
                result = _read_json(output / (op + ".stdout"))
                _operation(result, op, install)
                if op == "Check":
                    report = output / "prerequisites.json"
                    _child(output, env, "prerequisites", [kit / "HWPReview.exe", "--check-requirements", report], 120)
                    _ready(_read_json(report))
        record_dir = _path(result["log_directory"])
        if (record_dir.parent != logs or Path(result["receipt"]) != install / "INSTALL-RECEIPT.json"
                or _read_json(record_dir / "operation.json") != result
                or {p.name for p in record_dir.iterdir()} != {"operation.json", "HWP Editor.lnk", "requirements-before.json",
                                                             "requirements-before.stdout.txt", "requirements-before.stderr.txt"}):
            raise ValueError("Expected only one prepared install; no setup run or retry is allowed")
        _ready(_read_json(record_dir / "requirements-before.json"))
        receipt_sha = _installed(install, shortcuts, logs, subkey, hashes)
        workspace = output / ("Synthetic-" + sequence)
        workspace.mkdir()
        fixture = workspace / "document.hwpx"
        setup._write_new(fixture, blank_document())
        fixture_sha = _digest(fixture)
        installed_exe = install / "HWPReview.exe"
        observer = Path(__file__).with_name("release_install_observe.ps1").absolute()
        managed = omp.managed_runtime_root()
        with setup._locked_read(installed_exe) as executable, setup._locked_read(observer) as driver:
            observer_sha = hashlib.file_digest(driver, "sha256").hexdigest()
            if hashlib.file_digest(executable, "sha256").hexdigest() != exe_sha256:
                raise ValueError("Installed executable changed before launch")
            _child(output, env, "window", [*args, observer, "-Exe", installed_exe, "-Workspace", workspace,
                                          "-Managed", managed, "-Dotnet", locate_dotnet(), "-OutputRoot", output], 240)
        window = _read_json(output / "observed-window.json")
        _window(window, installed_exe, workspace, managed)
        checkpoint = _checkpoint(fixture, fixture_sha)
        receipt = {"contract": "hwp-installed-smoke-v1", "kit_sha256": kit_sha256, "exe_sha256": exe_sha256,
                   "installed_exe": str(installed_exe), "install_receipt_sha256": receipt_sha,
                   "window_png_sha256": _digest(output / "installed-window.png"),
                   "fixture_sha256": fixture_sha, "checkpoint": checkpoint, "window": window,
                   "observer_sha256": observer_sha, "registry_subkey": subkey}
        pending = output / "completed.pending"
        _record(pending, receipt)
        pending.rename(output / "installed-smoke.json")  # No replace on Windows; never expose a partial success.
        return receipt
    except Exception as error:
        _record(output / "stopped.json", {"error": type(error).__name__, "detail": str(error)})
        raise
