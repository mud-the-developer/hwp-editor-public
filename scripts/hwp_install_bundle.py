"""Closed per-user installer payloads, not signing or redistribution clearance."""

import json
from pathlib import Path
import re
import shutil
import stat
import zipfile

from hwp_build_source import sha256

CONTRACT = "hwp-install-payload-v1"
MANIFEST = "INSTALL-MANIFEST.json"
SCRIPTS = ("Install-HWPReview.cmd", "Install-HWPReview.ps1", "Uninstall-HWPReview.ps1", "Setup-Requirements.cmd")
FIXED = {"HWPReview.exe", "BUILD.json", "README.txt", "AI-INSTALL.md", "사용법.txt", "LICENSE", *SCRIPTS}
ROOT_INPUTS = {
    **{name: "packaging/" + name for name in SCRIPTS},
    "README.txt": "packaging/README-release.txt",
    "AI-INSTALL.md": "packaging/AI-INSTALL.md",
    "사용법.txt": "packaging/Quick-Start-ko.txt",
    "LICENSE": "LICENSE",
}


def regular(path):
    """Reject linked files and reparse ancestors before reading build artifacts."""
    path = Path(path).absolute()
    for node in (path, *path.parents):
        info = node.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked installer input: " + str(node))
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Non-regular/hard-linked installer input: " + str(path))
    return path


def payload_names(version, licenses):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("Unsafe installer version")
    names = set(FIXED) | {f"HWPReview-source-{version}.zip"}
    seen = set()
    for name in licenses:
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
                or name.endswith(".") or name.casefold() in seen
                or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])", name.split(".")[0])):
            raise ValueError("Unsafe/duplicate installer notice name")
        seen.add(name.casefold())
        names.add("licenses/" + name)
    if len(names) == len(FIXED) + 1 or len(names) > 512:
        raise ValueError("Missing/oversized installer notices")
    return names


def write_payload(release, snapshot, legal, *, version):
    """Populate ONLY a fresh checked-release; all copy destinations are exclusive."""
    release, snapshot, legal = map(Path, (release, snapshot, legal))
    notices = sorted(path.name for path in legal.iterdir())
    names = payload_names(version, notices)
    for name, source_name in ROOT_INPUTS.items():
        with regular(snapshot / source_name).open("rb") as source:
            with (release / name).open("xb") as target:
                shutil.copyfileobj(source, target)
    (release / "licenses").mkdir()
    for name in notices:
        with regular(legal / name).open("rb") as stream, (release / "licenses" / name).open("xb") as target:
            shutil.copyfileobj(stream, target)
    files = {name: {"sha256": sha256(regular(release / name)), "bytes": (release / name).stat().st_size} for name in sorted(names)}
    manifest = {"contract": CONTRACT, "version": version, "architecture": "x64", "files": files,
                "redistribution_ready": False, "code_signed": False}
    with (release / MANIFEST).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return manifest


def verify_payload(release, *, version):
    """Check exact candidate membership and bytes, including its transport ZIP."""
    release = Path(release)
    manifest = json.loads(regular(release / MANIFEST).read_text(encoding="utf-8"))
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        raise ValueError("Invalid installer files")
    build = json.loads(regular(release / "BUILD.json").read_text(encoding="utf-8"))
    notices = build.get("licenses_sha256")
    if (not isinstance(notices, dict) or not all(
            isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)
            for digest in notices.values())):
        raise ValueError("Missing/invalid captured installer notices")
    expected = payload_names(version, notices)
    if (manifest.get("contract") != CONTRACT or manifest.get("version") != version
            or manifest.get("architecture") != "x64" or manifest.get("redistribution_ready") is not False
            or manifest.get("code_signed") is not False or set(files) != expected):
        raise ValueError("Installer manifest differs from the closed payload")
    for name, record in files.items():
        path = regular(release / name)
        if (not isinstance(record, dict) or type(record.get("bytes")) is not int
                or record != {"sha256": sha256(path), "bytes": path.stat().st_size}):
            raise ValueError("Installer payload changed: " + name)
    source_name = f"HWPReview-source-{version}.zip"
    if (build.get("version") != version
            or build.get("executable_sha256") != files["HWPReview.exe"]["sha256"]
            or build.get("source_archive") != {"file": source_name, **files[source_name]}
            or build.get("redistribution_ready") is not False):
        raise ValueError("Installer application/source/build identity differs")
    inputs = build.get("input_sha256")
    if not isinstance(inputs, dict):
        raise ValueError("Missing captured installer build inputs")
    for name, source_name in ROOT_INPUTS.items():
        if inputs.get(source_name) != files[name]["sha256"]:
            raise ValueError("Installer input differs from captured source: " + name)
    if any(files["licenses/" + name]["sha256"] != digest for name, digest in notices.items()):
        raise ValueError("Installer notices differ from captured build")
    members = sorted([*files, MANIFEST])
    sums = "".join(sha256(release / name) + "  " + name + "\n" for name in members)
    if regular(release / "SHA256SUMS.txt").read_text(encoding="utf-8") != sums:
        raise ValueError("Installer checksum inventory changed")
    archive = release / f"HWPReview-win-x64-{version}.zip"
    transport = {archive.name, archive.name + ".sha256", "SHA256SUMS.txt"}
    actual = set()
    for path in release.iterdir():
        if path.name == "licenses" and path.is_dir() and not path.is_symlink():
            for notice in path.iterdir():
                regular(notice)
                actual.add("licenses/" + notice.name)
        else:
            regular(path)
            actual.add(path.name)
    if actual != set(members) | transport:
        raise ValueError("Unexpected candidate file/directory")
    with zipfile.ZipFile(regular(archive)) as package:
        names = package.namelist()
        if len(names) != len(set(names)) or set(names) != set(members) | {"SHA256SUMS.txt"} or package.testzip() is not None:
            raise ValueError("Installer ZIP membership/CRC changed")
        for name in names:
            with package.open(name) as source, (release / name).open("rb") as target:
                while True:
                    block = source.read(1024 * 1024)
                    if block != target.read(1024 * 1024):
                        raise ValueError("Installer ZIP content changed: " + name)
                    if not block:
                        break
    if regular(release / (archive.name + ".sha256")).read_text(encoding="utf-8") != sha256(archive) + "  " + archive.name + "\n":
        raise ValueError("Installer ZIP checksum changed")
    return manifest
