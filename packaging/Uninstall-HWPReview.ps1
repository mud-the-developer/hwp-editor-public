[CmdletBinding()]
param(
    [string]$InstallDirectory,
    [string]$LogDirectory = (Join-Path $env:LOCALAPPDATA 'HWPReview\InstallRuns')
)
# Dispatch only to the exact receipted engine; the engine owns every mutation.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Resolve this automatic variable in the script body, not during parameter defaults.
if (-not $PSBoundParameters.ContainsKey('InstallDirectory')) { $InstallDirectory = $PSScriptRoot }
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$result = [ordered]@{contract='hwp-install-operation-v1'; operation='uninstall'; status='failed'; exit_code=3; install_directory=$InstallDirectory; log_directory=$null; receipt=$null; prerequisites_checked=$false; app_launched=$false; retained=@(); detail=$null}
$HwpUninstallGuard = @{contract='hwp-uninstall-verification-v1'; engine_path=$null; install_directory=$null; files=@{}; directories=@{}}
function Wrapper-SafePath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^[A-Za-z]:\\' -or $Path -match '[\x00-\x1f<>"|?*]' -or $Path.Substring(2).Contains(':')) { throw '안전한 로컬 절대 경로가 필요합니다.' }
    foreach ($part in $Path.Substring(3).Split('\')) {
        if ($part -eq '.' -or $part -eq '..' -or $part.EndsWith('.') -or $part.EndsWith(' ') -or $part -match '^(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)') { throw '모호하거나 예약된 경로는 사용하지 않습니다.' }
    }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full.Length -le 3 -or (New-Object IO.DriveInfo($full.Substring(0, 3))).DriveType -ne [IO.DriveType]::Fixed) { throw '고정 로컬 드라이브의 하위 폴더가 필요합니다.' }
    $node = $full
    while ($node) {
        $attributes = $null
        try { $attributes = [IO.File]::GetAttributes($node) }
        catch [IO.FileNotFoundException] { }
        catch [IO.DirectoryNotFoundException] { }
        if ($null -ne $attributes -and ($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw '연결/재분석 지점 경로는 사용하지 않습니다.' }
        $parent = [IO.Directory]::GetParent($node)
        if ($null -eq $parent) { break }
        $node = $parent.FullName
    }
    return $full
}
function Wrapper-InitializeInspection {
    # In-memory P/Invoke only: never compile a temporary helper or load an unverified engine.
    if ($null -eq ('HwpUninstall.NativeFile' -as [type])) {
        $assembly = [AppDomain]::CurrentDomain.DefineDynamicAssembly((New-Object Reflection.AssemblyName('HwpUninstall.Native')), [Reflection.Emit.AssemblyBuilderAccess]::Run)
        $module = $assembly.DefineDynamicModule('HwpUninstall.Native')
        $type = $module.DefineType('HwpUninstall.NativeFile', ([Reflection.TypeAttributes]::Public -bor [Reflection.TypeAttributes]::Abstract -bor [Reflection.TypeAttributes]::Sealed))
        $attributes = [Reflection.MethodAttributes]::Public -bor [Reflection.MethodAttributes]::Static -bor [Reflection.MethodAttributes]::PinvokeImpl
        $method = $type.DefinePInvokeMethod('CreateFileW', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [Microsoft.Win32.SafeHandles.SafeFileHandle], [type[]]@([string], [uint32], [uint32], [IntPtr], [uint32], [uint32], [IntPtr]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('GetFileInformationByHandle', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [bool], [type[]]@([Microsoft.Win32.SafeHandles.SafeFileHandle], [IntPtr]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('GetFileInformationByHandleEx', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [bool], [type[]]@([Microsoft.Win32.SafeHandles.SafeFileHandle], [int], [IntPtr], [uint32]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('GetFinalPathNameByHandleW', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [uint32], [type[]]@([Microsoft.Win32.SafeHandles.SafeFileHandle], [Text.StringBuilder], [uint32], [uint32]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $null = $type.CreateType()
    }
}
function Wrapper-AssertHandlePath($Handle, [string]$Path) {
    $resolved = New-Object Text.StringBuilder(32768)
    $length = [HwpUninstall.NativeFile]::GetFinalPathNameByHandleW($Handle, $resolved, $resolved.Capacity, 0)
    if ($length -eq 0 -or $length -ge $resolved.Capacity -or -not $resolved.ToString().Equals(('\\?\' + $Path), [StringComparison]::OrdinalIgnoreCase)) { throw '열린 제거 엔진/기록/폴더의 실제 경로가 다릅니다.' }
}
function Wrapper-ProtectDirectory([string]$Path) {
    $nodes = New-Object 'Collections.Generic.List[string]'
    $node = $Path
    while ($node) {
        $nodes.Insert(0, $node)
        $parent = [IO.Directory]::GetParent($node)
        if ($null -eq $parent) { break }
        $node = $parent.FullName
    }
    foreach ($directory in $nodes) {
        if ($script:HwpUninstallGuard.directories.ContainsKey($directory)) { continue }
        $handle = [HwpUninstall.NativeFile]::CreateFileW($directory, 128, 3, [IntPtr]::Zero, 3, 35651584, [IntPtr]::Zero)
        if ($handle.IsInvalid) { $handle.Dispose(); throw '제거 엔진/기록의 상위 폴더를 고정할 수 없습니다.' }
        try {
            Wrapper-AssertHandlePath $handle $directory
            $info = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
            try {
                if (-not [HwpUninstall.NativeFile]::GetFileInformationByHandle($handle, $info) -or ([Runtime.InteropServices.Marshal]::ReadInt32($info, 0) -band 1040) -ne 16) { throw '일반 폴더 경로가 아닙니다.' }
                if (-not [HwpUninstall.NativeFile]::GetFileInformationByHandleEx($handle, 23, $info, 4) -or ([Runtime.InteropServices.Marshal]::ReadInt32($info, 0) -band 1) -ne 0) { throw '대소문자 구분 폴더 또는 확인 불가한 경로는 제거 대상으로 지원하지 않습니다.' }
            } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info) }
            # Share read/write, never delete: parents cannot be replaced before the invocation.
            $script:HwpUninstallGuard.directories[$directory] = $handle
        } catch { $handle.Dispose(); throw }
    }
}
function Wrapper-Open([string]$Path) {
    $Path = Wrapper-SafePath $Path
    Wrapper-ProtectDirectory ([IO.Path]::GetDirectoryName($Path))
    $handle = [HwpUninstall.NativeFile]::CreateFileW($Path, [uint32]2147483648, 1, [IntPtr]::Zero, 3, 2097152, [IntPtr]::Zero)
    if ($handle.IsInvalid) { $handle.Dispose(); throw '정확한 제거 엔진/소유권 기록을 고정할 수 없습니다.' }
    try { $stream = New-Object IO.FileStream($handle, [IO.FileAccess]::Read) }
    catch { $handle.Dispose(); throw }
    try {
        Wrapper-AssertHandlePath $stream.SafeFileHandle $Path
        $info = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
        try {
            if (-not [HwpUninstall.NativeFile]::GetFileInformationByHandle($stream.SafeFileHandle, $info) -or [Runtime.InteropServices.Marshal]::ReadInt32($info, 40) -ne 1 -or ([Runtime.InteropServices.Marshal]::ReadInt32($info, 0) -band 1040) -ne 0 -or $stream.Length -gt 2097152) { throw '정확한 제거 엔진/소유권 기록의 일반 파일을 확인할 수 없습니다.' }
        } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info) }
        return $stream
    } catch { $stream.Dispose(); throw }
}
function Wrapper-Hash($Stream) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $Stream.Position = 0; return ([BitConverter]::ToString($hash.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant() }
    finally { $Stream.Position = 0; $hash.Dispose() }
}
function Wrapper-ReadJson($Stream) {
    $Stream.Position = 0
    $reader = New-Object IO.StreamReader($Stream, (New-Object Text.UTF8Encoding($false, $true)), $true, 4096, $true)
    try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
    finally { $reader.Dispose(); $Stream.Position = 0 }
}
try {
    if ($env:OS -cne 'Windows_NT') { throw 'Windows 전용 제거 도구입니다.' }
    $InstallDirectory = Wrapper-SafePath $InstallDirectory
    $result.install_directory = $InstallDirectory
    if (-not (Test-Path -LiteralPath $InstallDirectory)) {
        $result.status = 'already_absent'; $result.exit_code = 0
        $result | ConvertTo-Json -Depth 16
        exit 0
    }
    $receiptPath = Join-Path $InstallDirectory 'INSTALL-RECEIPT.json'
    $manifestPath = Join-Path $InstallDirectory 'INSTALL-MANIFEST.json'
    $enginePath = Wrapper-SafePath (Join-Path $PSScriptRoot 'Install-HWPReview.ps1')
    $result.receipt = $receiptPath
    Wrapper-InitializeInspection
    $HwpUninstallGuard.install_directory = $InstallDirectory
    $HwpUninstallGuard.engine_path = $enginePath
    $HwpUninstallGuard.files['receipt'] = Wrapper-Open $receiptPath
    $HwpUninstallGuard.files['manifest'] = Wrapper-Open $manifestPath
    $HwpUninstallGuard.files['engine'] = Wrapper-Open $enginePath
    $receipt = Wrapper-ReadJson $HwpUninstallGuard.files['receipt']
    $manifest = Wrapper-ReadJson $HwpUninstallGuard.files['manifest']
    if ($receipt.contract -cne 'hwp-user-install-receipt-v2' -or $manifest.contract -cne 'hwp-install-payload-v1' -or $receipt.manifest_sha256 -cnotmatch '^[a-f0-9]{64}$') { throw '설치 소유권 기록이 다릅니다.' }
    $receiptedInstallDirectory = Wrapper-SafePath $receipt.install_directory
    Wrapper-ProtectDirectory $receiptedInstallDirectory
    if (-not $receiptedInstallDirectory.Equals($InstallDirectory, [StringComparison]::OrdinalIgnoreCase)) { throw '설치 영수증의 폴더가 다릅니다.' }
    $InstallDirectory = $receiptedInstallDirectory
    $result.install_directory = $InstallDirectory
    $HwpUninstallGuard.install_directory = $InstallDirectory
    $receiptedLogDirectory = Wrapper-SafePath $receipt.log_directory
    if (-not $PSBoundParameters.ContainsKey('LogDirectory')) { $LogDirectory = $receiptedLogDirectory }
    $LogDirectory = Wrapper-SafePath $LogDirectory
    if ((Wrapper-Hash $HwpUninstallGuard.files['manifest']) -cne $receipt.manifest_sha256) { throw '설치 manifest가 변경되었습니다. 파일을 보존합니다.' }
    $expected = $manifest.files.'Install-HWPReview.ps1'
    if ($expected.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $expected.bytes -isnot [ValueType] -or $expected.bytes -is [bool] -or [decimal]$expected.bytes -ne [decimal]$HwpUninstallGuard.files['engine'].Length -or (Wrapper-Hash $HwpUninstallGuard.files['engine']) -cne $expected.sha256) { throw '제거 엔진이 변경/누락되었습니다. 실행하거나 우회하지 않습니다.' }
    # All three verified files deny writes/deletes and all ancestors deny replacement while & reopens.
    # The already-loaded exact engine releases this data-only guard immediately before core self-removal.
    & $enginePath -Operation Uninstall -InstallDirectory $InstallDirectory -LogDirectory $LogDirectory
    exit $LASTEXITCODE
} catch {
    $result.detail = $_.Exception.Message
    $result.retained = @($InstallDirectory)
    $result | ConvertTo-Json -Depth 16
    exit 3
} finally {
    foreach ($stream in $HwpUninstallGuard.files.Values) { $stream.Dispose() }
    foreach ($handle in $HwpUninstallGuard.directories.Values) { $handle.Dispose() }
}
