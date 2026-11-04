"""Pure exact build-input snapshots and source archives; no build orchestration."""

import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_inputs(root, destination, names):
    """Freeze ONLY the caller's allowlisted inputs before compilation."""
    root = root.resolve(strict=True)
    destination.mkdir(exist_ok=False)
    hashes = {}
    for name in dict.fromkeys(names):
        source = root / name
        if (
            source.is_symlink()
            or not source.resolve(strict=True).is_relative_to(root)
            or not source.is_file()
        ):
            raise ValueError(
                "Build input must be a regular file inside the source root: " + name
            )
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[name] = sha256(target)
    return hashes


def verify_inputs(root, hashes):
    for name, expected in hashes.items():
        if sha256(root / name) != expected:
            raise RuntimeError(
                "Source changed during build; candidate was not published: " + name
            )


def write_source_archive(snapshot, inputs, destination, *, version):
    """Archive only the exact frozen inputs, never the live working tree."""
    snapshot = Path(snapshot).resolve(strict=True)
    for name in inputs:
        if (
            "\\" in name
            or ":" in name
            or Path(name).is_absolute()
            or ".." in name.split("/")
            or (snapshot / name).is_symlink()
            or not (snapshot / name).resolve().is_relative_to(snapshot)
        ):
            raise ValueError("Unsafe build-source member")
    verify_inputs(snapshot, inputs)
    manifest = {
        "kind": "hwp-exact-build-source-v1",
        "version": version,
        "inputs_sha256": inputs,
        "provenance": "hash-pinned build input snapshot, not a published Git commit",
    }
    with zipfile.ZipFile(
        destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for name in sorted(inputs):
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (snapshot / name).read_bytes())
        archive.writestr(
            "SOURCE-MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        )
        archive.writestr(
            "BUILD-SOURCE.txt",
            "Exact application build inputs. Not a complete repository or Native qualification.\n"
            "Prepare Windows x64 Python with requirements.txt and requirements-build.txt, and an already installed .NET SDK 10.0.401 with its Windows Desktop reference packs. Builds are offline SDK-only with cleared NuGet package sources; no automatic SDK, runtime, workload or dependency installation/download is performed.\n"
            "Then run: python scripts/build_exe.py --candidate-only\n"
            "The single builder freezes these exact Python/C#/WPF/config inputs, compiles a fresh framework-dependent native UI from that snapshot, and embeds its assembly, dependency/runtime configuration, manifest and pinned local WebView2 SDK/terminal/HWPX-renderer/icon assets in HWPReview.exe. The per-user PowerShell5.1/CMD installer delegates missing mandatory external .NET Desktop/WebView2/Bun/OMP/PowerShell7/font preparation to this same exact EXE. Existing compatible installations and user documents/auth are preserved; human UAC/license/login boundaries remain. External runtime binaries, apphost and SDK toolchains are not bundled. AI-INSTALL.md describes this same route, not an alternate executor.\n"
            "No credentials, user reports, security DLL, Hancom or Node/Pi are included. Third-party corresponding-source requirements are recorded in packaging/third-party-sources.json. Redistribution clearance is not implied.\n",
        )
    verify_source_archive(destination, inputs, version=version)
    return {
        "file": Path(destination).name,
        "sha256": sha256(destination),
        "bytes": Path(destination).stat().st_size,
    }


def verify_source_archive(archive, inputs, *, version):
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if (
            len(names) != len(set(names))
            or set(names) != set(inputs) | {"SOURCE-MANIFEST.json", "BUILD-SOURCE.txt"}
            or sum(item.file_size for item in package.infolist()) > 512 * 1024 * 1024
            or package.testzip() is not None
        ):
            raise ValueError("Build-source archive membership or CRC changed")
        raw = package.read("SOURCE-MANIFEST.json")
        if len(raw) > 1048576:
            raise ValueError("Build-source manifest exceeds supported size")
        try:
            manifest = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid build-source manifest JSON") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("kind") != "hwp-exact-build-source-v1"
            or manifest.get("version") != version
            or manifest.get("inputs_sha256") != inputs
        ):
            raise ValueError(
                "Build-source manifest differs from the exact frozen inputs"
            )
        for name, expected in inputs.items():
            if (
                "\\" in name
                or ":" in name
                or name.startswith("/")
                or ".." in name.split("/")
                or hashlib.sha256(package.read(name)).hexdigest() != expected
            ):
                raise ValueError("Build-source content changed")
    return manifest
