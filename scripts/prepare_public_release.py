"""Create a new data-only public distribution from an exact local candidate."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import zipfile

import build_exe
import hwp_build_source as source
import hwp_install_bundle as bundle

ROOT = Path(__file__).resolve().parents[1]


def directory(path):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("Parent traversal is forbidden")
    for node in (path, *path.parents):
        info = node.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked directory is forbidden")
    if not path.is_dir():
        raise ValueError("Expected a directory")
    return path


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def git(*args):
    return subprocess.run(["git", "--no-replace-objects", "-C", str(ROOT), *args],
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def validate(candidate, commit, repository):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full lowercase Git commit SHA is required")
    if repository != "https://github.com/mud-the-developer/hwp-editor-public":
        raise ValueError("Select the public source repository")
    directory(ROOT)
    candidate = directory(candidate)
    build = json.loads(bundle.regular(candidate / "BUILD.json").read_text(encoding="utf-8"))
    version = build_exe.APP_VERSION
    bundle.verify_payload(candidate, version=version)
    expected = set(build_exe.SOURCE_FILES) | set(build_exe.ASSETS) | {
        "packaging/licenses/" + name for name in build_exe.LEGAL_FILES}
    inputs = build.get("input_sha256")
    folded = set()
    for path in candidate.rglob("*"):
        relative = path.relative_to(candidate).as_posix()
        if relative.casefold() in folded:
            raise ValueError("Case-colliding candidate paths")
        folded.add(relative.casefold())
        if path.is_dir():
            directory(path)
        else:
            bundle.regular(path)
    if not isinstance(inputs, dict) or set(inputs) != expected:
        raise ValueError("Candidate exact source inventory differs")
    if (build.get("source_sha256") != {name: inputs[name] for name in build_exe.SOURCE_FILES}
            or build.get("assets_sha256") != {name: inputs[name] for name in build_exe.ASSETS}
            or build.get("code_signed") is not False):
        raise ValueError("Candidate source/assets/signing identity differs")
    if git("rev-parse", "--verify", commit + "^{commit}").decode().strip() != commit:
        raise ValueError("Commit object identity differs")
    tree = git("ls-tree", "-r", "-z", commit)
    records = {}
    for row in tree.split(b"\0"):
        if row:
            header, name = row.split(b"\t", 1)
            mode, kind, oid = header.split()
            records[name.decode("utf-8")] = (mode, kind, oid)
    for name, digest in inputs.items():
        path = bundle.regular(ROOT / name)
        if source.sha256(path) != digest:
            raise ValueError("Current source differs from candidate: " + name)
        record = records.get(name)
        if record is None or record[0] not in (b"100644", b"100755") or record[1] != b"blob":
            raise ValueError("Commit lacks regular source: " + name)
        if hashlib.sha256(git("cat-file", "blob", record[2].decode())).hexdigest() != digest:
            raise ValueError("Commit source differs from candidate: " + name)
    source.verify_inputs(ROOT, inputs)
    archive = candidate / build["source_archive"]["file"]
    source.verify_source_archive(bundle.regular(archive), inputs, version=version)
    # Every captured source notice must retain its original bytes in the payload.
    for name, digest in inputs.items():
        if name.startswith("packaging/licenses/"):
            if build["licenses_sha256"].get(Path(name).name) != digest:
                raise ValueError("Original root notice differs: " + name)
    index = json.loads(bundle.regular(candidate / "licenses/THIRD-PARTY-LICENSES.json").read_text(encoding="utf-8"))
    if index.get("project", {}).get("source_repository") != repository:
        raise ValueError("Notice repository differs from public source")
    for name, digest in index.get("files_sha256", {}).items():
        if build["licenses_sha256"].get(name) != digest:
            raise ValueError("Notice index differs from captured notices")
    with zipfile.ZipFile(archive) as package:
        folded_members = set()
        for info in package.infolist():
            if (info.filename.casefold() in folded_members
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("Linked/colliding source archive members")
            folded_members.add(info.filename.casefold())
    return candidate, build


def prepare(candidate, output, commit, repository):
    candidate, original = validate(candidate, commit, repository)
    output = Path(output).absolute()
    if (".." in output.parts or output.name.endswith((".", " "))
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", output.name)
            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])", output.name.split(".")[0])):
        raise ValueError("Unsafe output directory name")
    directory(output.parent)
    if output.exists() or output.is_symlink():
        raise ValueError("Output must be a new directory")
    if output.is_relative_to(candidate) or candidate.is_relative_to(output) or output.is_relative_to(ROOT):
        raise ValueError("Output must be separate from candidate and source")
    output.mkdir()
    payload = output / "installer"
    payload.mkdir()
    version = original["version"]
    names = bundle.payload_names(version, original["licenses_sha256"])
    for name in sorted(names - {"BUILD.json"}):
        target = payload / name
        target.parent.mkdir(exist_ok=True)
        with bundle.regular(candidate / name).open("rb") as src, target.open("xb") as dst:
            shutil.copyfileobj(src, dst)
    build = {key: original[key] for key in ("version", "executable_sha256", "source_archive", "licenses_sha256")}
    build.update(input_sha256={name: original["input_sha256"][name] for name in bundle.ROOT_INPUTS.values()},
                 code_signed=False, redistribution_ready=False)
    write_json(payload / "BUILD.json", build)
    files = {name: {"sha256": source.sha256(payload / name), "bytes": (payload / name).stat().st_size}
             for name in sorted(names)}
    write_json(payload / bundle.MANIFEST, {"contract": bundle.CONTRACT, "version": version,
               "architecture": "x64", "files": files, "redistribution_ready": False, "code_signed": False})
    members = sorted([*names, bundle.MANIFEST])
    with (payload / "SHA256SUMS.txt").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(source.sha256(payload / name) + "  " + name + "\n" for name in members))
    archive = payload / f"HWPReview-win-x64-{version}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name in [*members, "SHA256SUMS.txt"]:
            package.write(payload / name, name)
    with (payload / (archive.name + ".sha256")).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(source.sha256(archive) + "  " + archive.name + "\n")
    bundle.verify_payload(payload, version=version)
    source.verify_source_archive(payload / original["source_archive"]["file"], original["input_sha256"], version=version)
    # Re-read the original inputs after copying; failures leave the new set unpublished.
    validate(candidate, commit, repository)
    manifest = {"contract": "hwp-public-distribution-v1", "commit": commit, "repository": repository,
                "version": version, "code_signed": False, "redistribution_ready": False,
                "tested": ["closed payload hashes", "exact source archive", "Git commit build-input bytes"],
                "unqualified": ["other-PC execution", "Native layout certification", "legal source-delivery clearance", "code signing"],
                "other_pc_tested": False,
                "artifacts": {name: {"sha256": source.sha256(payload / name), "bytes": (payload / name).stat().st_size}
                              for name in ("HWPReview.exe", original["source_archive"]["file"], archive.name)}}
    write_json(output / "RELEASE-MANIFEST.json", manifest)
    outer = sorted(path for path in output.rglob("*") if path.is_file())
    with (output / "SHA256SUMS.txt").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(source.sha256(path) + "  " + path.relative_to(output).as_posix() + "\n" for path in outer))
    return manifest


def main():
    parser = argparse.ArgumentParser(description="새 공개 배포 자료를 생성합니다. 설치·실행·업로드하지 않습니다.")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    prepare(args.candidate, args.output, args.commit, args.repository)


if __name__ == "__main__":
    main()
