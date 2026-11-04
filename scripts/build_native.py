"""Build a fresh offline framework-dependent WPF payload using a prepared SDK.

Requires Windows x64 and an already installed .NET SDK 10.0.401. Never installs
or downloads an SDK, runtime, package, workload or global configuration.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

import hwp_native_payload as payload
from hwp_resources import APP_VERSION

SDK_VERSION = "10.0.401"
PROJECT = "native/HWPReview.Native/HWPReview.Native.csproj"


def _owned_environment(work: Path) -> dict:
    environment = {
        key: os.environ[key]
        for key in (
            "SystemRoot", "WINDIR", "OS", "PROCESSOR_ARCHITECTURE",
            "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS",
        )
        if key in os.environ
    }
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
    environment["COMSPEC"] = str(windows / "System32/cmd.exe")
    environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    for key, name in (
        ("DOTNET_CLI_HOME", "cli-home"),
        ("NUGET_PACKAGES", "packages"),
        ("NUGET_HTTP_CACHE_PATH", "http-cache"),
        ("NUGET_PLUGINS_CACHE_PATH", "plugin-cache"),
        ("TEMP", "temp"),
        ("TMP", "temp"),
        ("HOME", "profile"),
        ("USERPROFILE", "profile"),
        ("APPDATA", "profile/appdata"),
        ("LOCALAPPDATA", "profile/localappdata"),
        # NuGet's Windows static defaults require these paths even when an
        # explicit offline config is used. Keep machine-wide defaults isolated.
        ("PROGRAMFILES", "profile/programfiles"),
        ("PROGRAMFILES(X86)", "profile/programfiles-x86"),
        ("PROGRAMDATA", "profile/programdata"),
        ("ALLUSERSPROFILE", "profile/programdata"),
    ):
        directory = work / name
        directory.mkdir(parents=True, exist_ok=True)
        environment[key] = str(directory)
    environment.update({
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "true",
        "DOTNET_CLI_WORKLOAD_UPDATE_ADVERTISING_DISABLE": "true",
        "DOTNET_CLI_WORKLOAD_UPDATE_ADVERTISING_INTERVAL": "2147483647",
        "DOTNET_MULTILEVEL_LOOKUP": "0",
        "DOTNET_CLI_UI_LANGUAGE": "en-US",
        "NUGET_XMLDOC_MODE": "skip",
        "MSBUILDDISABLENODEREUSE": "1",
        "MSBuildEnableWorkloadResolver": "false",
    })
    return environment


def _run(command, *, work, environment):
    stage = {"--version": "sdk-version", "restore": "restore", "publish": "publish"}[command[1]]
    log = work.parent / (stage + ".log")
    try:
        result = subprocess.run(
            command, cwd=work, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=600, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        output = error.output or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        with log.open("x", encoding="utf-8") as stream:
            stream.write(output)
        raise
    with log.open("x", encoding="utf-8") as stream:
        stream.write(result.stdout)
    return result


def _require_offline_config(path: Path):
    try:
        config = ET.fromstring(path.read_bytes())
    except ET.ParseError as exc:
        raise ValueError("Invalid offline NuGet configuration") from exc
    sources = config.findall("packageSources")
    if (
        config.tag != "configuration"
        or len(sources) != 1
        or len(sources[0]) != 1
        or sources[0][0].tag != "clear"
        or sources[0][0].attrib
        or set(child.tag for child in config) != {"packageSources"}
    ):
        raise ValueError("Native NuGet configuration must clear every package source")


def _prepare_output_parent(parent: Path):
    missing = []
    existing = parent
    while not existing.exists() and not existing.is_symlink():
        if existing.parent == existing:
            raise FileNotFoundError("Native output volume does not exist")
        missing.append(existing)
        existing = existing.parent
    payload._plain_path(existing, directory=True)
    for directory in reversed(missing):
        directory.mkdir(exist_ok=False)
    payload._plain_path(parent, directory=True)


def build_native(source_root, destination) -> dict:
    """Compile only exact allowlisted inputs into a NEW caller-owned directory."""
    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64"):
        raise RuntimeError("Use Windows x64 with the already prepared .NET SDK 10.0.401")
    source_root = payload._plain_path(Path(source_root), directory=True)
    destination = Path(os.path.abspath(destination))
    if destination.name != "native-ui":
        raise ValueError("Native output must be a new directory named native-ui")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Refusing to replace an existing native UI payload")
    payload.verify_renderer_dependencies(source_root)
    original = payload.source_hashes(source_root)
    # The imported recipe/validator/version must themselves be the frozen inputs,
    # not a second live recipe paired with an older source snapshot.
    for module_path in (Path(__file__), Path(payload.__file__)):
        name = "scripts/" + module_path.name
        if payload._sha256(payload._plain_path(module_path)) != original[name]:
            raise ValueError("Native build recipe differs from the exact source snapshot")
    resources = Path(payload.__file__).with_name("hwp_resources.py")
    if payload._sha256(payload._plain_path(resources)) != original["scripts/hwp_resources.py"]:
        raise ValueError("Native build application version differs from the source snapshot")
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise RuntimeError("Prepare .NET SDK 10.0.401 before building; no automatic installation")
    dotnet = str(payload._plain_path(Path(dotnet)))
    _prepare_output_parent(destination.parent)
    destination.mkdir(exist_ok=False)
    work = destination.parent / (".native-build-" + uuid.uuid4().hex)
    work.mkdir(exist_ok=False)
    environment = _owned_environment(work)
    source = work / "source"
    for name in payload.NATIVE_SOURCE_FILES:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / name, target)
    if payload.source_hashes(source) != original:
        raise ValueError("Native source changed while the build snapshot was copied")
    (source / "global.json").write_text(json.dumps({
        "sdk": {"version": SDK_VERSION, "rollForward": "disable", "allowPrerelease": False}
    }), encoding="utf-8")
    actual = _run([dotnet, "--version"], work=source, environment=environment).stdout.strip()
    if actual != SDK_VERSION:
        raise RuntimeError("Use the already installed .NET SDK " + SDK_VERSION)
    project = source / PROJECT
    config = source / "native/NuGet.Config"
    _require_offline_config(config)
    properties = [
        "-p:Configuration=Release", "-p:RuntimeIdentifier=win-x64",
        "-p:SelfContained=false", "-p:UseAppHost=false",
        "-p:PublishSingleFile=false", "-p:PublishAot=false",
        "-p:PublishReadyToRun=false", "-p:PublishTrimmed=false",
        "-p:DebugType=None", "-p:DebugSymbols=false",
        "-p:Deterministic=true", "-p:ContinuousIntegrationBuild=true",
        "-p:EnableSourceControlManagerQueries=false",
        "-p:EnableSourceLink=false", "-p:EmbedUntrackedSources=false",
        "-p:PathMap=" + str(source) + "=/_/",
        "-p:Version=" + APP_VERSION,
        "-p:RuntimeFrameworkVersion=10.0.0", "-p:TargetLatestRuntimePatch=false",
        "-p:RollForward=LatestPatch", "-p:NuGetAudit=false",
        "-p:RestoreSources=", "-p:RestoreAdditionalProjectSources=",
        "-p:RestoreFallbackFolders=", "-p:RestoreAdditionalProjectFallbackFolders=",
        "-p:DisableImplicitNuGetFallbackFolder=true",
        "-p:RestoreIgnoreFailedSources=false",
        "-p:ImportDirectoryBuildProps=false", "-p:ImportDirectoryBuildTargets=false",
        "-p:ImportDirectoryPackagesProps=false",
        "-p:CustomBeforeMicrosoftCommonProps=", "-p:CustomAfterMicrosoftCommonProps=",
        "-p:CustomBeforeMicrosoftCommonTargets=", "-p:CustomAfterMicrosoftCommonTargets=",
        "-p:CustomBeforeMicrosoftCommonCrossTargetingTargets=",
        "-p:CustomAfterMicrosoftCommonCrossTargetingTargets=",
        "-p:ImportByWildcardBeforeMicrosoftCommonProps=false",
        "-p:ImportByWildcardAfterMicrosoftCommonProps=false",
        "-p:ImportUserLocationsByWildcardBeforeMicrosoftCommonProps=false",
        "-p:ImportUserLocationsByWildcardAfterMicrosoftCommonProps=false",
        "-p:ImportByWildcardBeforeMicrosoftCommonTargets=false",
        "-p:ImportByWildcardAfterMicrosoftCommonTargets=false",
        "-p:ImportUserLocationsByWildcardBeforeMicrosoftCommonTargets=false",
        "-p:ImportUserLocationsByWildcardAfterMicrosoftCommonTargets=false",
    ]
    _run([
        dotnet, "restore", str(project), "--configfile", str(config),
        "--no-http-cache", "--disable-parallel", *properties,
    ], work=source, environment=environment)
    published = work / "publish"
    _run([
        dotnet, "publish", str(project), "--no-restore", "--output", str(published),
        "--disable-build-servers", *properties,
    ], work=source, environment=environment)
    payload.verify_inventory(published, payload.PAYLOAD_FILES)
    for name in payload.PAYLOAD_FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(payload._plain_path(published / name), target)
    if payload.source_hashes(source) != original or payload.source_hashes(source_root) != original:
        raise ValueError("Native source changed during compilation")
    manifest = {
        "contract": payload.CONTRACT, "version": APP_VERSION,
        "framework_dependent": True, "target_framework": "net10.0-windows",
        "runtime_requirement": dict(payload.RUNTIME_REQUIREMENT),
        "files_sha256": {name: payload._sha256(destination / name) for name in payload.PAYLOAD_FILES},
        "source_sha256": original,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return payload.verify_payload(destination, source_root=source_root)


def _register_source(source_root: Path, destination: Path):
    root = payload._plain_path(source_root, directory=True)
    build = payload._plain_path(root / "build", directory=True)
    destination = payload._plain_path(destination, directory=True)
    if not destination.is_relative_to(build) or destination.name != "native-ui":
        raise ValueError("Source registration is restricted to repo/build/.../native-ui")
    payload.verify_payload(destination, source_root=root)
    pointer = build / "native-current.json"
    if pointer.exists() or pointer.is_symlink():
        payload._plain_path(pointer)
    temporary = build / (".native-current-" + uuid.uuid4().hex + ".json")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump({
                "contract": payload.CURRENT_CONTRACT,
                "path": destination.relative_to(root).as_posix(),
            }, stream, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, pointer)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="NEW native-ui directory; never overwritten")
    parser.add_argument("--register-source", action="store_true", help="Register only a verified repo/build payload for source launch")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = build_native(root, args.output)
    if args.register_source:
        _register_source(root, args.output)
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
