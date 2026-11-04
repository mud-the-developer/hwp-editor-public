"""Missing-only external runtime preparation; imports and status checks never install."""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile

RUNTIME_ASSETS = {
    "dotnet10": {
        "name": ".NET 10 Desktop Runtime x64", "version": "10.0.14", "kind": "exe",
        "url": "https://download.microsoft.com/download/9b53b153-e93a-4c89-8049-c7381505cf80/8de343c4-257a-40b1-9af3-3d7782328bc5/windowsdesktop-runtime-10.0.14-win-x64.exe",
        "size": 60035296,
        "sha256": "91c2795a7b1e2480caa83fce9514dc2e2d21e277d9a297311faba6040bad7330",
        "release_url": "https://dotnet.microsoft.com/en-us/download/dotnet/10.0",
    },
    "webview2": {
        "name": "Microsoft Edge WebView2 Runtime", "version": "156.0.4314.58", "kind": "exe",
        "url": "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/d5cc3c1c-bb59-4158-aedc-db0035e44466/MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
        "size": 215017168,
        "sha256": "62863f0f0611a16ccf124b4f15650c3b66624504eba74d7cde1e2ae5d951249b",
        "release_url": "https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution",
    },
    "omp_runtime": {
        "name": "Bun / oh-my-pi", "version": "1.4.4 / 18.15.0", "kind": "bun_omp",
        "url": "https://registry.npmjs.org/@oh-my-pi/pi-coding-agent/-/pi-coding-agent-18.15.0.tgz",
        "size": 14978439,
        "sha256": "c935c4dd3b7a88acffdcd698f2499df90f869534e9b43fba8885ed60739bf465",
        "release_url": "https://github.com/can1357/oh-my-pi/releases/tag/v18.15.0",
        "runtime_assets": [
            {"id": "bun", "url": "https://github.com/oven-sh/bun/releases/download/bun-v1.4.4/bun-windows-x64.zip",
             "size": 54731706, "sha256": "cd2c0fccc78f3ede1f50ca827a15f063eda13d4d150a3d0674f4f6e323d864a5"},
            {"id": "natives", "url": "https://registry.npmjs.org/@oh-my-pi/pi-natives/-/pi-natives-18.15.0.tgz",
             "size": 129772, "sha256": "49eaf550b7660fc4f72ab234b532d1006a8380122a22c729019042620f8f5428"},
            {"id": "win32", "url": "https://registry.npmjs.org/@oh-my-pi/pi-natives-win32-x64/-/pi-natives-win32-x64-18.15.0.tgz",
             "size": 46414765, "sha256": "1b110b2e1444338308a1a47ff840357ecb8d0d9da45e693904f38c9634590b6b"},
        ],
    },
}


def _setup():
    import hwp_setup
    return hwp_setup


def _omp():
    import hwp_omp_tui
    return hwp_omp_tui


def _webview_status():
    import winreg
    setup = _setup()
    key = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive, view in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        values = {name.casefold(): (value, kind) for name, value, kind in setup._registry_values(hive, key, view)}
        value, kind = values.get("pv", (None, None))
        if (kind == winreg.REG_SZ and isinstance(value, str)
                and re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", value)
                and any(int(part) for part in value.split("."))):
            return {"installed": True, "detail": f"기존 WebView2 Runtime {value}를 유지합니다. 브라우저·업데이트 정책을 바꾸지 않습니다."}
    return {"installed": False, "detail": "WebView2 Runtime이 없습니다. Microsoft 원문 약관·SmartScreen 및 업데이트 안내를 확인한 뒤 준비합니다."}


def component_status(component):
    """Read installed metadata and exact OMP files; never launch a candidate."""
    identifier = component["id"]
    if identifier == "dotnet10":
        from hwp_native_payload import locate_dotnet
        try:
            host = locate_dotnet()
        except (OSError, ValueError) as error:
            if os.environ.get("DOTNET_ROOT_X64") or os.environ.get("DOTNET_ROOT"):
                raise ValueError("명시된 DOTNET_ROOT가 .NET 10 Desktop x64 요구와 다릅니다. 기존 환경을 자동 수정하거나 다른 실행기로 우회하지 않습니다.") from error
            return {"installed": False, "detail": ".NET 10 Desktop Runtime x64가 없습니다. Microsoft 설치기는 관리자 UAC 확인이 필요할 수 있습니다."}
        return {"installed": True, "detail": f"기존 호환 .NET 10 Desktop x64를 유지합니다: {host}. 새 버전으로 강제 교체하지 않습니다."}
    if identifier == "webview2":
        return _webview_status()
    if identifier != "omp_runtime":
        raise ValueError("지원하지 않는 필수 런타임입니다.")
    setup, omp = _setup(), _omp()
    home = setup._local_app_data() / "HWPReview/NativeOmp/home"
    managed = omp.managed_runtime_root()
    if os.path.lexists(managed):
        # A partial/corrupt managed install is evidence, not a repair/fallback cue.
        omp._runtime(managed / "bin/omp.exe", home)
        return {"installed": True, "detail": "앱 전용 Bun 1.4.4 / OMP 18.15.0의 정확한 21개 런타임 파일을 확인했습니다. 기존 로그인은 유지합니다."}
    existing = Path.home() / ".bun/bin/omp.exe"
    if os.path.lexists(existing):
        try:
            omp._runtime(omp._unlinked(existing), home)
        except (OSError, ValueError):
            return {"installed": False, "detail": "기존 사용자 Bun/OMP는 고정 기준과 달라 그대로 둡니다. 별도 앱 전용 경로에 검증한 런타임을 준비합니다."}
        return {"installed": True, "detail": "기존 사용자 Bun 1.4.4 / OMP 18.15.0의 정확한 런타임을 확인했습니다. 업그레이드·인증 복사를 하지 않습니다."}
    return {"installed": False, "detail": "Bun 1.4.4 / OMP 18.15.0이 없습니다. 사용자 .bun·PATH·인증과 분리한 앱 전용 경로에 준비합니다."}


def _run_microsoft_exe(component, payload, run, record):
    """ShellExecute owns its normal installer/UAC; no shell script, retry or kill."""
    import ctypes
    from ctypes import wintypes
    setup = _setup()
    if component["id"] == "dotnet10":
        log = setup._unlinked(run / "dotnet10-installer.log")
        if os.path.lexists(log):
            raise FileExistsError("기존 .NET 설치 로그를 덮어쓰지 않습니다.")
        arguments = ["/install", "/passive", "/norestart", "/log", str(log)]
        record["installer_log"] = str(log)
    elif component["id"] == "webview2":
        arguments = ["/silent", "/install"]
    else:
        raise ValueError("검토하지 않은 EXE 설치기는 실행할 수 없습니다.")
    record.update(arguments=arguments, automatic_reboot=False,
                  policy="Microsoft installer owns UAC, scope and shared update behavior; no force/repair")

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", wintypes.LPVOID),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel.GetProcessId.restype = wintypes.DWORD
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    info = ShellExecuteInfo(cbSize=ctypes.sizeof(ShellExecuteInfo), fMask=0x40 | 0x100,
                            lpVerb="open", lpFile=str(payload),
                            lpParameters=subprocess.list2cmdline(arguments),
                            lpDirectory=str(run), nShow=1)
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        record["shell_error"] = error
        if error == 1223:
            raise setup.SetupCancelled("사용자가 설치기/UAC를 취소했습니다.")
        raise ctypes.WinError(error)
    if not info.hProcess:
        raise setup.SetupError("설치기 프로세스 소유 정보를 받지 못했습니다. 재실행하지 않습니다.")
    try:
        record["installer_process_id"] = kernel.GetProcessId(info.hProcess)
        if kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF) != 0:
            raise setup.SetupError("설치기 종료 확인이 불명확합니다. 프로세스를 강제 종료하지 않습니다.")
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        record["exit_code"] = code.value
    finally:
        kernel.CloseHandle(info.hProcess)
    if code.value in (1602, 1223):
        raise setup.SetupCancelled("사용자가 Microsoft 설치기를 취소했습니다.")
    if code.value not in (0, 3010):
        raise setup.SetupError(f"Microsoft 설치기가 실패했습니다 (코드 {code.value}). 다른 설치 방식으로 재실행하지 않습니다.")
    return code.value == 3010


def _stage_bun_omp(component, payloads, run, record):
    """Stage a NEW exact runtime, with isolated package configuration and no hooks."""
    setup, omp = _setup(), _omp()
    stage = setup._unlinked(run / "omp-runtime")
    stage.mkdir(exist_ok=False)
    binary = setup._directory(stage / "bin")
    baseline, _ = omp._baseline()
    bun_pin = next(row for row in baseline["files"] if row["relative_path"] == "bin/bun.exe")
    assets = {"primary": component, **{row["id"]: row for row in component["runtime_assets"]}}
    with ExitStack() as locks:
        for identifier, asset in assets.items():
            source = locks.enter_context(setup._locked_read(payloads[identifier]))
            setup._verify_stream(source, asset)
        with zipfile.ZipFile(payloads["bun"]) as archive:
            entries = archive.infolist()
            if len(entries) > 32 or sum(entry.file_size for entry in entries) > 256 * 1024 * 1024:
                raise ValueError("Bun ZIP의 구성원 또는 해제 크기가 제한을 넘습니다.")
            names = [setup._safe_member(entry.filename) for entry in entries]
            if len(names) != len(set(names)) or any(entry.flag_bits & 1 or stat.S_ISLNK(entry.external_attr >> 16) for entry in entries):
                raise ValueError("Bun ZIP의 중복·암호화·연결 구성원은 허용하지 않습니다.")
            if archive.testzip() is not None:
                raise ValueError("Bun ZIP의 CRC가 일치하지 않습니다.")
            entry = archive.getinfo("bun-windows-x64/bun.exe")
            if entry.file_size != bun_pin["size"] or entry.is_dir():
                raise ValueError("Bun 실행 파일 크기가 정확한 기준과 다릅니다.")
            with archive.open(entry) as source, setup._new_file(binary / "bun.exe") as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
        bun = binary / "bun.exe"
        source = locks.enter_context(setup._locked_read(bun))
        setup._verify_stream(source, bun_pin)
        system = setup._system_directory()
        environment = setup._child_environment(system)
        home = setup._directory(run / "package-home")
        config = setup._directory(run / "package-config")
        working = setup._directory(run / "package-work")
        cache = setup._directory(run / "package-cache")
        global_root = setup._directory(stage / "install/global")
        npm_config, bun_config = config / "empty.npmrc", config / "empty.bunfig.toml"
        setup._write_new(npm_config, b"")
        setup._write_new(bun_config, b"")
        environment.update({"HOME": str(home), "USERPROFILE": str(home),
                            "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
                            "XDG_CONFIG_HOME": str(config), "NPM_CONFIG_USERCONFIG": str(npm_config),
                            "NPM_CONFIG_GLOBALCONFIG": str(npm_config),
                            "BUN_INSTALL": str(stage), "BUN_INSTALL_GLOBAL_DIR": str(global_root),
                            "BUN_INSTALL_BIN": str(binary), "BUN_INSTALL_CACHE_DIR": str(cache),
                            "BUN_CONFIG_HTTP_RETRY_COUNT": "0", "TEMP": str(run), "TMP": str(run)})
        # Registry specs match the reviewed installed layout; local tarball specs
        # add Bun provenance markers inside package roots and are not admitted.
        packages = [f"@oh-my-pi/{name}@{omp.VERSION}" for name in
                    ("pi-coding-agent", "pi-natives", "pi-natives-win32-x64")]
        arguments = [str(bun), "--config=" + str(bun_config), "add", "--global", "--exact",
                     "--ignore-scripts", "--backend=copyfile", "--no-env-file",
                     "--registry=https://registry.npmjs.org", "--minimum-release-age=1",
                     *packages]
        record.update(runtime_stage=str(stage), runtime_arguments=arguments,
                      runtime_policy={"user_global_mutation": False, "auth_copied": False,
                                      "lifecycle_scripts": False, "backend": "copyfile",
                                      "http_retry_count": 0, "minimum_release_age_seconds": 1,
                                      "offline_claimed": False})
        # The positive age also avoids Bun's exhausted-budget extended-manifest fallback.
        with setup._new_file(run / "bun-install.stdout.txt") as stdout, setup._new_file(run / "bun-install.stderr.txt") as stderr:
            child = subprocess.Popen(arguments, executable=str(bun), cwd=working, env=environment,
                                     stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            record["runtime_process_id"] = child.pid
            code = child.wait()
            record["runtime_exit_code"] = code
        if code != 0:
            raise setup.SetupError(f"분리된 Bun 패키지 준비가 실패했습니다 (코드 {code}). 자동 재시도·사용자 설치 수정·대체 실행은 하지 않습니다.")
    verified = omp._runtime(stage / "bin/omp.exe", home)
    record["runtime_manifest_sha256"] = verified["manifest_sha256"]
    record["verified_runtime_files"] = len(verified["files"])
    return stage


def install_component(component, payloads, run, record):
    setup = _setup()
    if component["id"] in ("dotnet10", "webview2"):
        return _run_microsoft_exe(component, payloads["primary"], run, record)
    if component["id"] != "omp_runtime":
        raise ValueError("지원하지 않는 런타임 설치 요청입니다.")
    omp = _omp()
    destination = omp.managed_runtime_root()
    if os.path.lexists(destination):
        raise FileExistsError("기존 앱 전용 Bun/OMP를 덮어쓰거나 복구하지 않습니다.")
    stage = _stage_bun_omp(component, payloads, run, record)
    setup._directory(destination.parent)
    if os.path.lexists(destination):
        raise FileExistsError("준비 중 앱 전용 Bun/OMP 경로가 생겼습니다. 기존 경로를 보존합니다.")
    os.rename(stage, destination)  # Same-volume exclusive publication; never overwrite/copy fallback.
    record["installed_runtime_root"] = str(destination)
    omp._runtime(destination / "bin/omp.exe", setup._local_app_data() / "HWPReview/NativeOmp/home")
    record["detail"] = "별도 앱 전용 경로에 정확한 Bun / OMP 런타임을 준비했습니다. 기존 .bun·PATH·로그인은 바꾸지 않았습니다."
    return False
