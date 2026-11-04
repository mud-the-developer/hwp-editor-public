[CmdletBinding()]
param(
    [ValidateSet('Install', 'Check', 'Uninstall')][string]$Operation = 'Install',
    [string]$SourceDirectory,
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\HWPReview'),
    [string]$ShortcutDirectory = (Join-Path ([Environment]::GetFolderPath('Programs')) 'HWPReview'),
    [string]$RegistrySubKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\HWPReview',
    [string]$LogDirectory = (Join-Path $env:LOCALAPPDATA 'HWPReview\InstallRuns')
)
# Windows PowerShell 5.1 entrypoint. No elevation, app launch, PATH or association writes.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# PowerShell 5.1 populates PSScriptRoot after parameter defaults are evaluated.
if (-not $PSBoundParameters.ContainsKey('SourceDirectory')) { $SourceDirectory = $PSScriptRoot }
$script:LogDirectoryExplicit = $PSBoundParameters.ContainsKey('LogDirectory')
$script:ExitCode = 1
$script:Run = $null
$script:Locks = New-Object 'Collections.Generic.List[IDisposable]'
$script:Retained = New-Object 'Collections.Generic.List[string]'
$script:DirectoryLocks = @{}
$script:OwnershipLocks = New-Object 'Collections.Generic.List[IDisposable]'
$script:WrapperGuard = $null
$result = [ordered]@{contract='hwp-install-operation-v1'; operation=$Operation.ToLowerInvariant(); status='failed'; exit_code=1; install_directory=$null; log_directory=$null; receipt=$null; prerequisites_checked=$false; app_launched=$false; retained=@(); detail=$null}

function Stop-Install([int]$Code, [string]$Message) {
    $script:ExitCode = $Code
    throw $Message
}
function Safe-Path([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^[A-Za-z]:\\' -or $Path -match '[\x00-\x1f<>"|?*]' -or $Path.Substring(2).Contains(':')) {
        Stop-Install 3 '로컬 절대 경로만 사용할 수 있습니다. UNC/장치/상대 경로와 특수 경로는 허용하지 않습니다.'
    }
    foreach ($part in $Path.Substring(3).Split('\')) {
        if ($part -eq '.' -or $part -eq '..' -or $part.EndsWith('.') -or $part.EndsWith(' ') -or $part -match '^(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)') {
            Stop-Install 3 '모호하거나 예약된 경로 이름은 허용하지 않습니다.'
        }
    }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full.Length -le 3 -or (New-Object IO.DriveInfo($full.Substring(0, 3))).DriveType -ne [IO.DriveType]::Fixed) {
        Stop-Install 3 '고정 로컬 드라이브의 하위 폴더가 필요합니다.'
    }
    $node = $full
    while ($node) {
        $attributes = $null
        try { $attributes = [IO.File]::GetAttributes($node) }
        catch [IO.FileNotFoundException] { }
        catch [IO.DirectoryNotFoundException] { }
        if ($null -ne $attributes -and ($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Stop-Install 3 '연결/재분석 지점 경로는 사용하지 않습니다.' }
        $parent = [IO.Directory]::GetParent($node)
        if ($null -eq $parent) { break }
        $node = $parent.FullName
    }
    return $full
}
function Under([string]$Child, [string]$Parent) {
    return $Child.Equals($Parent, [StringComparison]::OrdinalIgnoreCase) -or $Child.StartsWith($Parent + '\', [StringComparison]::OrdinalIgnoreCase)
}
function Ensure-Directory([string]$Path) {
    $null = Safe-Path $Path
    Protect-DirectoryPath $Path -AllowMissing
    if ([IO.File]::Exists($Path)) { Stop-Install 3 '파일과 폴더 경로가 충돌합니다.' }
    if (-not [IO.Directory]::Exists($Path)) { $null = [IO.Directory]::CreateDirectory($Path) }
    $null = Safe-Path $Path
    Protect-DirectoryPath $Path
}
function Initialize-FileInspection {
    # Emit only an in-memory P/Invoke type: Check must not invoke a compiler or write temp assemblies.
    if ($null -eq ('HwpInstall.NativeFile' -as [type])) {
        $assembly = [AppDomain]::CurrentDomain.DefineDynamicAssembly((New-Object Reflection.AssemblyName('HwpInstall.Native')), [Reflection.Emit.AssemblyBuilderAccess]::Run)
        $module = $assembly.DefineDynamicModule('HwpInstall.Native')
        $type = $module.DefineType('HwpInstall.NativeFile', ([Reflection.TypeAttributes]::Public -bor [Reflection.TypeAttributes]::Abstract -bor [Reflection.TypeAttributes]::Sealed))
        $attributes = [Reflection.MethodAttributes]::Public -bor [Reflection.MethodAttributes]::Static -bor [Reflection.MethodAttributes]::PinvokeImpl
        $method = $type.DefinePInvokeMethod('GetFileInformationByHandle', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [bool], [type[]]@([Microsoft.Win32.SafeHandles.SafeFileHandle], [IntPtr]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('GetFileInformationByHandleEx', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [bool], [type[]]@([Microsoft.Win32.SafeHandles.SafeFileHandle], [int], [IntPtr], [uint32]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('RegCreateKeyExW', 'advapi32.dll', $attributes, [Reflection.CallingConventions]::Standard, [int], [type[]]@([IntPtr], [string], [int], [string], [int], [int], [IntPtr], [IntPtr].MakeByRefType(), [int].MakeByRefType()), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('RegOpenKeyExW', 'advapi32.dll', $attributes, [Reflection.CallingConventions]::Standard, [int], [type[]]@([IntPtr], [string], [int], [int], [IntPtr].MakeByRefType()), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('CreateDirectoryW', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [bool], [type[]]@([string], [IntPtr]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('CreateFileW', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [Microsoft.Win32.SafeHandles.SafeFileHandle], [type[]]@([string], [uint32], [uint32], [IntPtr], [uint32], [uint32], [IntPtr]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('GetFinalPathNameByHandleW', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [uint32], [type[]]@([Microsoft.Win32.SafeHandles.SafeFileHandle], [Text.StringBuilder], [uint32], [uint32]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('NtDeleteKey', 'ntdll.dll', $attributes, [Reflection.CallingConventions]::Standard, [int], [type[]]@([Microsoft.Win32.SafeHandles.SafeRegistryHandle]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $method = $type.DefinePInvokeMethod('SetFileInformationByHandle', 'kernel32.dll', $attributes, [Reflection.CallingConventions]::Standard, [bool], [type[]]@([Microsoft.Win32.SafeHandles.SafeFileHandle], [int], [IntPtr], [uint32]), [Runtime.InteropServices.CallingConvention]::Winapi, [Runtime.InteropServices.CharSet]::Unicode)
        $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
        $null = $type.CreateType()
    }
}
function Assert-HandlePath($Handle, [string]$Path) {
    $resolved = New-Object Text.StringBuilder(32768)
    # Normal Windows names ignore case; ancestry guards reject case-sensitive directories.
    $length = [HwpInstall.NativeFile]::GetFinalPathNameByHandleW($Handle, $resolved, $resolved.Capacity, 0)
    if ($length -eq 0 -or $length -ge $resolved.Capacity -or -not $resolved.ToString().Equals(('\\?\' + $Path), [StringComparison]::OrdinalIgnoreCase)) {
        Stop-Install 3 ('열린 파일/폴더의 실제 경로가 다릅니다: ' + $Path)
    }
}
function Protect-DirectoryPath([string]$Path, [switch]$AllowMissing) {
    $nodes = New-Object 'Collections.Generic.List[string]'
    $node = $Path
    while ($node) {
        $nodes.Insert(0, $node)
        $parent = [IO.Directory]::GetParent($node)
        if ($null -eq $parent) { break }
        $node = $parent.FullName
    }
    foreach ($directory in $nodes) {
        if ($script:DirectoryLocks.ContainsKey($directory)) { continue }
        # FILE_READ_ATTRIBUTES; share read/write, never delete. Pin every ancestor identity.
        $handle = [HwpInstall.NativeFile]::CreateFileW($directory, 128, 3, [IntPtr]::Zero, 3, 35651584, [IntPtr]::Zero)
        if ($handle.IsInvalid) {
            $handle.Dispose()
            if ($AllowMissing -and -not [IO.Directory]::Exists($directory)) { return }
            Stop-Install 3 ('폴더 경로를 고정할 수 없습니다: ' + $directory)
        }
        try {
            Assert-HandlePath $handle $directory
            $info = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
            try {
                if (-not [HwpInstall.NativeFile]::GetFileInformationByHandle($handle, $info) -or ([Runtime.InteropServices.Marshal]::ReadInt32($info, 0) -band 1040) -ne 16) { Stop-Install 3 '일반 폴더 경로가 아닙니다.' }
                if (-not [HwpInstall.NativeFile]::GetFileInformationByHandleEx($handle, 23, $info, 4) -or ([Runtime.InteropServices.Marshal]::ReadInt32($info, 0) -band 1) -ne 0) { Stop-Install 3 '대소문자 구분 폴더 또는 확인 불가한 경로는 설치 대상으로 지원하지 않습니다.' }
            } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info) }
            $script:DirectoryLocks[$directory] = $handle
        } catch { $handle.Dispose(); throw }
    }
}
function Release-DirectoryPath([string]$Path) {
    if ($script:DirectoryLocks.ContainsKey($Path)) {
        $script:DirectoryLocks[$Path].Dispose()
        $script:DirectoryLocks.Remove($Path)
    }
}
function Open-Regular([string]$Path, [switch]$DeleteAccess, [switch]$CreateNew) {
    $Path = Safe-Path $Path
    Protect-DirectoryPath ([IO.Path]::GetDirectoryName($Path))
    $access = [uint32]2147483648
    $share = [uint32]1
    $creation = [uint32]3
    $mode = [IO.FileAccess]::Read
    if ($DeleteAccess) { $access = [uint32]2147549184 }
    if ($CreateNew) { $access = [uint32]1073741824; $share = 0; $creation = 1; $mode = [IO.FileAccess]::Write }
    # OPEN_REPARSE_POINT checks the leaf; pinned ancestors plus the final handle name close the path race.
    $handle = [HwpInstall.NativeFile]::CreateFileW($Path, $access, $share, [IntPtr]::Zero, $creation, 2097152, [IntPtr]::Zero)
    if ($handle.IsInvalid) { $handle.Dispose(); Stop-Install 3 ('파일을 독점 확인/생성할 수 없습니다: ' + $Path) }
    try { $stream = New-Object IO.FileStream($handle, $mode) }
    catch { $handle.Dispose(); throw }
    try {
        Assert-HandlePath $stream.SafeFileHandle $Path
        $info = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
        try {
            if (-not [HwpInstall.NativeFile]::GetFileInformationByHandle($stream.SafeFileHandle, $info) -or [Runtime.InteropServices.Marshal]::ReadInt32($info, 40) -ne 1 -or ([Runtime.InteropServices.Marshal]::ReadInt32($info, 0) -band 1040) -ne 0) {
                Stop-Install 3 ('하드 링크 또는 확인할 수 없는 파일입니다: ' + $Path)
            }
        } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info) }
        return $stream
    } catch { $stream.Dispose(); throw }
}
function Stream-Hash($Stream) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $Stream.Position = 0; return ([BitConverter]::ToString($hash.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant() }
    finally { $Stream.Position = 0; $hash.Dispose() }
}
function File-Hash([string]$Path) {
    $stream = Open-Regular $Path
    try { return Stream-Hash $stream } finally { $stream.Dispose() }
}
function Read-JsonStream($Stream) {
    if ($Stream.Length -gt 2097152) { Stop-Install 3 'JSON 기록이 너무 큽니다.' }
    $Stream.Position = 0
    $reader = New-Object IO.StreamReader($Stream, (New-Object Text.UTF8Encoding($false, $true)), $true, 4096, $true)
    try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
    finally { $reader.Dispose(); $Stream.Position = 0 }
}
function Read-Json([string]$Path) {
    $stream = Open-Regular $Path
    try { return Read-JsonStream $stream } finally { $stream.Dispose() }
}
function Write-NewJson([string]$Path, $Value) {
    $null = Safe-Path $Path
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($Value | ConvertTo-Json -Depth 16) + "`n")
    $stream = Open-Regular $Path -CreateNew
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
function New-OwnedDirectory([string]$Path) {
    $null = Safe-Path $Path
    Protect-DirectoryPath ([IO.Path]::GetDirectoryName($Path))
    if (-not [HwpInstall.NativeFile]::CreateDirectoryW($Path, [IntPtr]::Zero)) { Stop-Install 3 ('새 소유 폴더 생성 실패/충돌입니다: ' + $Path) }
    Protect-DirectoryPath $Path
}
function New-Run {
    Ensure-Directory $LogDirectory
    $script:Run = Join-Path $LogDirectory ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $script:Run) { Stop-Install 3 '새 로그 경로가 이미 있습니다.' }
    New-OwnedDirectory $script:Run
    $result.log_directory = $script:Run
}
function Validate-RegistryPath([string]$Name) {
    if ($Name -cne 'Software\Microsoft\Windows\CurrentVersion\Uninstall\HWPReview' -and $Name -notmatch '^Software\\HWPReview\\InstallerSmoke\\[a-fA-F0-9]{32}$') {
        Stop-Install 3 '허용된 현재 사용자 제거 등록 위치가 아닙니다.'
    }
}
function Assert-RegistryPlain {
    $prefix = ''
    foreach ($part in $RegistrySubKey.Split('\')) {
        if ($prefix) { $prefix += '\' }
        $prefix += $part
        $handle = [IntPtr]::Zero
        # OPEN_LINK inspects the link key itself instead of following its destination.
        $code = [HwpInstall.NativeFile]::RegOpenKeyExW([IntPtr](-2147483647), $prefix, 8, 131097, [ref]$handle)
        if ($code -eq 2) { return }
        if ($code -ne 0) { Stop-Install 3 '현재 사용자 등록 경로를 안전하게 확인할 수 없습니다.' }
        $safe = New-Object Microsoft.Win32.SafeHandles.SafeRegistryHandle($handle, $true)
        $key = [Microsoft.Win32.RegistryKey]::FromHandle($safe)
        try {
            if ($key.GetValueNames() -icontains 'SymbolicLinkValue') { Stop-Install 3 '레지스트리 연결 경로는 사용하지 않습니다.' }
        } finally { $key.Dispose() }
    }
}
function Expected-Registry($Version, $Id, [string]$RegisteredLogDirectory) {
    $powershell = Safe-Path (Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe')
    return [ordered]@{
        DisplayName='HWP Editor'; DisplayVersion=[string]$Version; Publisher='HWP Editor'; InstallLocation=$InstallDirectory;
        DisplayIcon=(Join-Path $InstallDirectory 'HWPReview.exe');
        UninstallString=('"' + $powershell + '" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $InstallDirectory 'Uninstall-HWPReview.ps1') + '" -LogDirectory "' + $RegisteredLogDirectory + '"');
        NoModify=1; NoRepair=1; HWPReviewInstallId=[string]$Id
    }
}
function Test-RegistryKey($Key, $Expected) {
    $names = $Key.GetValueNames()
    if ($names -icontains 'SymbolicLinkValue' -or $Key.GetSubKeyNames().Count -ne 0 -or $names.Count -ne $Expected.Count) { return $false }
    foreach ($name in $Expected.Keys) {
        $kind = if ($name -in @('NoModify', 'NoRepair')) { [Microsoft.Win32.RegistryValueKind]::DWord } else { [Microsoft.Win32.RegistryValueKind]::String }
        if ($names -cnotcontains $name -or $Key.GetValueKind($name) -ne $kind -or $Key.GetValue($name) -cne $Expected[$name]) { return $false }
    }
    return $true
}
function Registry-Matches($Expected) {
    Assert-RegistryPlain
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($RegistrySubKey, $false)
    if ($null -eq $key) { return $false }
    try { return Test-RegistryKey $key $Expected } finally { $key.Dispose() }
}
function Remove-OwnedRegistry($Expected) {
    $key = $null
    $safe = $null
    try {
        Assert-RegistryPlain
        $handle = [IntPtr]::Zero
        # KEY_READ | DELETE, OPEN_LINK: validate and delete this object, not a second name lookup.
        $code = [HwpInstall.NativeFile]::RegOpenKeyExW([IntPtr](-2147483647), $RegistrySubKey, 8, 196633, [ref]$handle)
        if ($code -eq 2) { return }
        if ($code -ne 0) { throw '제거할 등록 키를 안전하게 열 수 없습니다.' }
        $safe = New-Object Microsoft.Win32.SafeHandles.SafeRegistryHandle($handle, $true)
        $key = [Microsoft.Win32.RegistryKey]::FromHandle($safe)
        if (-not (Test-RegistryKey $key $Expected)) { throw '등록 키의 값/형식/하위 키가 다릅니다.' }
        # NtDeleteKey returns NTSTATUS; any nonzero status retains ownership metadata.
        # Registry values have no file-style deny-write sharing: a same-user value race remains possible.
        if ([HwpInstall.NativeFile]::NtDeleteKey($safe) -ne 0) { throw '확인한 등록 키를 제거하지 못했습니다.' }
    } catch {
        $script:Retained.Add('HKCU\' + $RegistrySubKey)
    } finally {
        if ($null -ne $key) { $key.Dispose() }
        if ($null -ne $safe) { $safe.Dispose() }
    }
}
function Registry-Exists {
    Assert-RegistryPlain
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($RegistrySubKey, $false)
    if ($null -eq $key) { return $false }
    $key.Dispose(); return $true
}
function Load-Manifest([string]$Root, [switch]$Hold, [switch]$HoldOwnership) {
    $manifestPath = Join-Path $Root 'INSTALL-MANIFEST.json'
    $manifestStream = Open-Regular $manifestPath
    if ($HoldOwnership) { $script:OwnershipLocks.Add($manifestStream) }
    else { $script:Locks.Add($manifestStream) }
    $manifest = Read-JsonStream $manifestStream
    if ($manifest.contract -cne 'hwp-install-payload-v1' -or $manifest.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$' -or $manifest.architecture -cne 'x64' -or $manifest.redistribution_ready -cne $false -or $manifest.code_signed -cne $false) { Stop-Install 3 '지원하지 않는 설치 manifest입니다.' }
    $required = @('HWPReview.exe', 'BUILD.json', 'README.txt', 'AI-INSTALL.md', '사용법.txt', 'LICENSE', 'Install-HWPReview.cmd', 'Install-HWPReview.ps1', 'Uninstall-HWPReview.ps1', 'Setup-Requirements.cmd', ('HWPReview-source-' + $manifest.version + '.zip'))
    $files = [ordered]@{}
    $seen = @{}
    foreach ($property in $manifest.files.PSObject.Properties) {
        $name = [string]$property.Name
        $record = $property.Value
        if (($required -cnotcontains $name -and $name -cnotmatch '^licenses/[A-Za-z0-9][A-Za-z0-9_.-]*$') -or $name.EndsWith('.') -or $seen.ContainsKey($name) -or $record.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $record.bytes -isnot [ValueType] -or $record.bytes -is [bool] -or $record.bytes -lt 0 -or $record.bytes -gt 2147483648 -or [decimal]$record.bytes -ne [decimal][long]$record.bytes) { Stop-Install 3 '안전하지 않은 설치 파일 목록입니다.' }
        $seen[$name] = $true
        $files[$name] = [ordered]@{sha256=[string]$record.sha256; bytes=[long]$record.bytes}
    }
    if ($files.Count -le $required.Count -or $files.Count -gt 512) { Stop-Install 3 '고지 파일이 없거나 파일 목록이 너무 큽니다.' }
    foreach ($name in $required) { if (-not $files.Contains($name)) { Stop-Install 3 ('필수 설치 파일 누락: ' + $name) } }
    $manifestHash = Stream-Hash $manifestStream
    $files['INSTALL-MANIFEST.json'] = [ordered]@{sha256=$manifestHash; bytes=$manifestStream.Length}
    $streams = @{}
    if ($Hold) {
        foreach ($name in $files.Keys) {
            $stream = Open-Regular (Join-Path $Root $name.Replace('/', '\'))
            $script:Locks.Add($stream)
            if ($stream.Length -ne $files[$name].bytes -or (Stream-Hash $stream) -cne $files[$name].sha256) { Stop-Install 3 ('배포 파일 해시/크기 불일치: ' + $name) }
            $streams[$name] = $stream
        }
    }
    return @{manifest=$manifest; hash=$manifestHash; files=$files; streams=$streams}
}
function Validate-Source($Payload) {
    $transport = @('SHA256SUMS.txt', ('HWPReview-win-x64-' + $Payload.manifest.version + '.zip'), ('HWPReview-win-x64-' + $Payload.manifest.version + '.zip.sha256'))
    foreach ($item in Get-ChildItem -LiteralPath $SourceDirectory -Force) {
        $null = Safe-Path $item.FullName
        if ($item.PSIsContainer) {
            if ($item.Name -cne 'licenses') { Stop-Install 3 '배포 폴더에 허용되지 않은 하위 폴더가 있습니다.' }
            foreach ($notice in Get-ChildItem -LiteralPath $item.FullName -Force) {
                if ($notice.PSIsContainer -or -not $Payload.files.Contains('licenses/' + $notice.Name)) { Stop-Install 3 '허용되지 않은 고지 파일이 있습니다.' }
                $stream = Open-Regular $notice.FullName; $stream.Dispose()
            }
        } elseif (-not $Payload.files.Contains($item.Name)) {
            if ($transport -cnotcontains $item.Name) { Stop-Install 3 ('배포 폴더의 예기치 않은 파일: ' + $item.Name) }
            $stream = Open-Regular $item.FullName; $stream.Dispose()
        }
    }
    $build = Read-Json (Join-Path $SourceDirectory 'BUILD.json')
    $sourceName = 'HWPReview-source-' + $Payload.manifest.version + '.zip'
    if ($build.version -cne $Payload.manifest.version -or $build.executable_sha256 -cne $Payload.files['HWPReview.exe'].sha256 -or $build.source_archive.file -cne $sourceName -or $build.source_archive.sha256 -cne $Payload.files[$sourceName].sha256 -or $build.source_archive.bytes -isnot [ValueType] -or $build.source_archive.bytes -is [bool] -or [decimal]$build.source_archive.bytes -ne [decimal]$Payload.files[$sourceName].bytes -or $build.redistribution_ready -cne $false) { Stop-Install 3 '앱/소스/빌드 기록이 서로 다릅니다.' }
    # Fixed root-to-source bindings; a manifest cannot choose an input or provide a command.
    $rootInputs = [ordered]@{
        'Install-HWPReview.cmd'='packaging/Install-HWPReview.cmd'
        'Install-HWPReview.ps1'='packaging/Install-HWPReview.ps1'
        'Uninstall-HWPReview.ps1'='packaging/Uninstall-HWPReview.ps1'
        'Setup-Requirements.cmd'='packaging/Setup-Requirements.cmd'
        'README.txt'='packaging/README-release.txt'
        'AI-INSTALL.md'='packaging/AI-INSTALL.md'
        '사용법.txt'='packaging/Quick-Start-ko.txt'
        'LICENSE'='LICENSE'
    }
    foreach ($name in $rootInputs.Keys) {
        $inputRecord = $build.input_sha256.PSObject.Properties[$rootInputs[$name]]
        if ($null -eq $inputRecord -or $inputRecord.Value -isnot [string] -or $inputRecord.Value -cnotmatch '^[a-f0-9]{64}$' -or $Payload.files[$name].sha256 -cne $inputRecord.Value) { Stop-Install 3 ('빌드 입력과 배포 루트 파일이 다릅니다: ' + $name) }
    }
    $notices = @($build.licenses_sha256.PSObject.Properties)
    $manifestNotices = @($Payload.files.Keys | Where-Object { $_.StartsWith('licenses/', [StringComparison]::Ordinal) })
    if ($notices.Count -eq 0 -or $notices.Count -ne $manifestNotices.Count) { Stop-Install 3 '빌드와 배포 고지 파일 목록이 다릅니다.' }
    $seenNotices = @{}
    foreach ($notice in $notices) {
        $name = [string]$notice.Name
        $payloadName = 'licenses/' + $name
        if ($name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$' -or $name.EndsWith('.') -or $seenNotices.ContainsKey($name) -or $notice.Value -isnot [string] -or $notice.Value -cnotmatch '^[a-f0-9]{64}$' -or $manifestNotices -cnotcontains $payloadName -or $Payload.files[$payloadName].sha256 -cne $notice.Value) { Stop-Install 3 ('빌드와 배포 고지 파일이 다릅니다: ' + $name) }
        $seenNotices[$name] = $true
    }
}
function Load-Receipt {
    $receiptStream = Open-Regular (Join-Path $InstallDirectory 'INSTALL-RECEIPT.json')
    $script:OwnershipLocks.Add($receiptStream)
    $receipt = Read-JsonStream $receiptStream
    if ($receipt.contract -cne 'hwp-user-install-receipt-v2' -or $receipt.install_id -cnotmatch '^[a-f0-9]{32}$' -or $receipt.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$') { Stop-Install 3 '이 설치를 소유하는 정확한 영수증이 없습니다.' }
    $receiptedInstallDirectory = Safe-Path $receipt.install_directory
    Protect-DirectoryPath $receiptedInstallDirectory
    if (-not $receiptedInstallDirectory.Equals($InstallDirectory, [StringComparison]::OrdinalIgnoreCase)) { Stop-Install 3 '설치 영수증의 폴더가 다릅니다.' }
    $script:InstallDirectory = $receiptedInstallDirectory
    $result.install_directory = $InstallDirectory
    $receiptedLogDirectory = Safe-Path $receipt.log_directory
    Protect-DirectoryPath $receiptedLogDirectory -AllowMissing
    if (-not $script:LogDirectoryExplicit) { $script:LogDirectory = $receiptedLogDirectory }
    $script:LogDirectory = Safe-Path $LogDirectory
    $script:ShortcutDirectory = Safe-Path $receipt.shortcut_directory
    $script:RegistrySubKey = [string]$receipt.registry_subkey
    Validate-RegistryPath $RegistrySubKey
    foreach ($logPath in @($receiptedLogDirectory, $LogDirectory)) {
        if ((Under $logPath $InstallDirectory) -or (Under $InstallDirectory $logPath) -or (Under $logPath $ShortcutDirectory) -or (Under $ShortcutDirectory $logPath)) { Stop-Install 3 '설치/바로가기/로그 경로가 겹칩니다.' }
    }
    if ((Under $ShortcutDirectory $InstallDirectory) -or (Under $InstallDirectory $ShortcutDirectory)) { Stop-Install 3 '설치/바로가기 경로가 겹칩니다.' }
    $payload = Load-Manifest $InstallDirectory -HoldOwnership
    if ($payload.hash -cne $receipt.manifest_sha256 -or $payload.manifest.version -cne $receipt.version -or $receipt.shortcut_sha256 -cnotmatch '^[a-f0-9]{64}$') { Stop-Install 3 '설치 영수증과 manifest가 다릅니다.' }
    if ($receipt.shortcut_bytes -isnot [ValueType] -or $receipt.shortcut_bytes -is [bool] -or $receipt.shortcut_bytes -le 0 -or $receipt.shortcut_bytes -gt 1048576) { Stop-Install 3 '바로가기 영수증 크기가 잘못되었습니다.' }
    $receiptFile = @{sha256=(Stream-Hash $receiptStream); bytes=$receiptStream.Length}
    return @{receipt=$receipt; receipt_file=$receiptFile; payload=$payload; registry=(Expected-Registry $receipt.version $receipt.install_id $receiptedLogDirectory)}
}
function File-Identity([string]$Path) {
    $stream = Open-Regular $Path
    try { return Handle-Identity $stream.SafeFileHandle }
    finally { $stream.Dispose() }
}
function Assert-NotRunning {
    $executable = Join-Path $InstallDirectory 'HWPReview.exe'
    $ownedIdentity = $null
    if ([IO.File]::Exists($executable)) {
        try { $ownedIdentity = File-Identity $executable }
        catch { Stop-Install 4 '설치된 실행 파일의 상태를 확인할 수 없어 중단합니다.' }
    }
    try { $processes = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop) }
    catch { Stop-Install 4 '실행 중인 프로세스 상태를 확인할 수 없어 중단합니다.' }
    foreach ($process in $processes) {
        if ($process.ExecutablePath -and (Under ([string]$process.ExecutablePath) $InstallDirectory)) { Stop-Install 4 '설치된 앱이 실행 중입니다. 앱을 직접 정상 종료한 다음 다시 요청하세요.' }
        if ($process.Name -ieq 'HWPReview.exe' -and -not $process.ExecutablePath) { Stop-Install 4 '실행 중인 HWPReview의 경로를 확인할 수 없어 중단합니다.' }
        if ($process.Name -ieq 'HWPReview.exe' -and $ownedIdentity) {
            try { $identity = File-Identity ([string]$process.ExecutablePath) }
            catch { Stop-Install 4 '실행 중인 HWPReview의 실제 파일을 확인할 수 없어 중단합니다.' }
            if ($identity -ceq $ownedIdentity) { Stop-Install 4 '설치된 앱이 다른 경로 표기로 실행 중입니다. 직접 정상 종료하세요.' }
        }
    }
}
function Is-ExactFile([string]$Path, $Record) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try { $stream = Open-Regular $Path; try { return $stream.Length -eq $Record.bytes -and (Stream-Hash $stream) -ceq $Record.sha256 } finally { $stream.Dispose() } }
    catch { return $false }
}
function Assert-ExistingExact($Owned, $Source) {
    if ($Owned.payload.hash -cne $Source.hash) { Stop-Install 3 '기존 설치와 새 배포가 다릅니다. 자동 업그레이드/덮어쓰기를 하지 않습니다.' }
    foreach ($name in $Owned.payload.files.Keys) {
        if (-not (Is-ExactFile (Join-Path $InstallDirectory $name.Replace('/', '\')) $Owned.payload.files[$name])) { Stop-Install 3 '기존 설치 파일이 누락/변경되어 자동 수리하지 않습니다.' }
    }
    $expectedNames = @($Owned.payload.files.Keys) + @('INSTALL-RECEIPT.json')
    foreach ($item in Get-ChildItem -LiteralPath $InstallDirectory -Force) {
        if ($item.Name -ceq 'licenses' -and $item.PSIsContainer) {
            foreach ($notice in Get-ChildItem -LiteralPath $item.FullName -Force) { if ($notice.PSIsContainer -or $expectedNames -cnotcontains ('licenses/' + $notice.Name)) { Stop-Install 3 '기존 설치에 예기치 않은 파일이 있습니다.' } }
        } elseif ($expectedNames -cnotcontains $item.Name) { Stop-Install 3 '기존 설치에 예기치 않은 파일이 있습니다.' }
    }
    if (-not (Registry-Matches $Owned.registry) -or -not (Is-ExactFile (Join-Path $ShortcutDirectory 'HWP Editor.lnk') @{bytes=$Owned.receipt.shortcut_bytes; sha256=$Owned.receipt.shortcut_sha256})) { Stop-Install 3 '기존 등록/바로가기가 다릅니다. 자동 수리하지 않습니다.' }
}
function Run-Exe([string]$Arguments, [string]$Tag) {
    $stdout = $null
    $stderr = $null
    $process = $null
    $started = $false
    try {
        # CREATE_NEW cannot truncate a precreated hardlink; retain both verified handles before starting.
        $stdout = Open-Regular (Join-Path $script:Run ($Tag + '.stdout.txt')) -CreateNew
        $stderr = Open-Regular (Join-Path $script:Run ($Tag + '.stderr.txt')) -CreateNew
        $process = New-Object Diagnostics.Process
        $process.StartInfo.FileName = Join-Path $SourceDirectory 'HWPReview.exe'
        $process.StartInfo.Arguments = $Arguments
        $process.StartInfo.WorkingDirectory = $SourceDirectory
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $started = $process.Start()
        if (-not $started) { Stop-Install 5 '필수 항목 실행을 시작하지 못했습니다.' }
        $pending = New-Object 'Collections.Generic.List[Threading.Tasks.Task]'
        $pending.Add($process.StandardOutput.BaseStream.CopyToAsync($stdout))
        $pending.Add($process.StandardError.BaseStream.CopyToAsync($stderr))
        # Drain both pipes concurrently. Observe a failed copy immediately, closing both pipes on error.
        while ($pending.Count -gt 0) {
            $index = [Threading.Tasks.Task]::WaitAny($pending.ToArray())
            $copy = $pending[$index]
            $pending.RemoveAt($index)
            $null = $copy.GetAwaiter().GetResult()
        }
        $process.WaitForExit()
        $stdout.Flush($true)
        $stderr.Flush($true)
        return $process.ExitCode
    } finally {
        if ($null -ne $process) {
            if ($started) { $process.StandardOutput.Dispose(); $process.StandardError.Dispose() }
            $process.Dispose()
        }
        if ($null -ne $stderr) { $stderr.Dispose() }
        if ($null -ne $stdout) { $stdout.Dispose() }
    }
}
function Requirements-Check([string]$Tag) {
    $reportPath = Join-Path $script:Run ($Tag + '.json')
    if (Test-Path -LiteralPath $reportPath) { Stop-Install 3 '새 필수 항목 확인 기록 경로가 이미 있습니다.' }
    $code = Run-Exe ('--check-requirements "' + $reportPath + '"') $Tag
    if ($code -ne 0 -and $code -ne 2) { Stop-Install 5 '필수 항목 읽기 전용 확인이 실패했습니다. 로그를 보존합니다.' }
    $report = Read-Json $reportPath
    if ($report.contract -cne 'hwp-requirements-status-v1' -or $report.ready -isnot [bool] -or $report.reboot_required -isnot [bool] -or $null -eq $report.components -or @($report.components.PSObject.Properties).Count -eq 0) { Stop-Install 5 '필수 항목 확인 기록 형식이 다릅니다.' }
    if ($report.reboot_required) { Stop-Install 3010 '재부팅이 필요합니다. 자동 재부팅하거나 앱을 시작하지 않습니다.' }
    $componentIds = @('powershell7', 'd2coding_propo', 'dotnet10', 'webview2', 'omp_runtime')
    $reportedIds = @($report.components.PSObject.Properties.Name)
    if ($reportedIds.Count -ne $componentIds.Count) { Stop-Install 5 '필수 항목 목록이 완전하지 않습니다.' }
    foreach ($id in $componentIds) { if ($reportedIds -cnotcontains $id) { Stop-Install 5 '필수 항목 확인 결과에 누락이 있습니다.' } }
    $allReady = $true
    foreach ($component in $report.components.PSObject.Properties) {
        if ($component.Value.installed -isnot [bool]) { Stop-Install 5 '필수 항목 설치 상태를 확인할 수 없습니다.' }
        if (-not $component.Value.installed) { $allReady = $false }
    }
    if ($report.ready -ne $allReady -or (($code -eq 0) -ne $report.ready)) { Stop-Install 5 '필수 항목 확인 종료 코드와 기록이 다릅니다.' }
    $result.prerequisites_checked = $true
    return $report.ready
}
function Copy-New([string]$Name, $Payload) {
    $target = Join-Path $InstallDirectory $Name.Replace('/', '\')
    $null = Safe-Path $target
    $stream = $Payload.streams[$Name]
    $stream.Position = 0
    $output = Open-Regular $target -CreateNew
    try { $stream.CopyTo($output); $output.Flush($true) } finally { $output.Dispose() }
    if (-not (Is-ExactFile $target $Payload.files[$Name])) { Stop-Install 1 '복사한 파일의 검증이 실패했습니다. 부분 설치를 보존합니다.' }
}
function Remove-Exact([string]$Path, $Record) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $stream = $null
    try {
        $stream = Open-Regular $Path -DeleteAccess
        if ($stream.Length -ne $Record.bytes -or (Stream-Hash $stream) -cne $Record.sha256) { $script:Retained.Add($Path); return }
        $disposition = [Runtime.InteropServices.Marshal]::AllocHGlobal(1)
        try {
            [Runtime.InteropServices.Marshal]::WriteByte($disposition, 1)
            # FILE_DISPOSITION_INFO marks THIS verified handle, never a newly resolved path.
            if (-not [HwpInstall.NativeFile]::SetFileInformationByHandle($stream.SafeFileHandle, 4, $disposition, 1)) { $script:Retained.Add($Path) }
        } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($disposition) }
    } catch { if (-not $script:Retained.Contains($Path)) { $script:Retained.Add($Path) } }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
}
function Remove-ExactCore($Records) {
    $streams = [ordered]@{}
    $paths = @($Records.Keys)
    try {
        # The engine is loaded before this call. Drop only the wrapper/metadata
        # read reservations, then reserve every core object before marking any.
        Release-UninstallVerification
        foreach ($path in $paths) {
            $stream = Open-Regular $path -DeleteAccess
            $streams[$path] = $stream
            $record = $Records[$path]
            if ($stream.Length -ne $record.bytes -or (Stream-Hash $stream) -cne $record.sha256) {
                throw '제거할 핵심 파일의 바이트가 다릅니다.'
            }
            $info = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
            try {
                if (-not [HwpInstall.NativeFile]::GetFileInformationByHandle($stream.SafeFileHandle, $info) -or
                    [Runtime.InteropServices.Marshal]::ReadInt32($info, 40) -ne 1 -or
                    ([Runtime.InteropServices.Marshal]::ReadInt32($info, 0) -band 1041) -ne 0) {
                    throw '핵심 파일이 읽기 전용이거나 일반 단일 링크 파일이 아닙니다.'
                }
            } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info) }
        }
        $disposition = [Runtime.InteropServices.Marshal]::AllocHGlobal(1)
        try {
            [Runtime.InteropServices.Marshal]::WriteByte($disposition, 1)
            foreach ($path in $paths) {
                # No path reopen: all four verified DELETE+read handles remain held.
                # Preflight is not a transaction; a late kernel failure stops further marks.
                if (-not [HwpInstall.NativeFile]::SetFileInformationByHandle($streams[$path].SafeFileHandle, 4, $disposition, 1)) {
                    throw '확인한 핵심 파일을 제거하지 못했습니다.'
                }
            }
        } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($disposition) }
    } catch {
        # On preflight failure none were marked. On a late failure conservatively
        # report the whole core; already accepted deletion marks are not rolled back.
        foreach ($path in $paths) {
            if (-not $script:Retained.Contains($path)) { $script:Retained.Add($path) }
        }
    } finally {
        foreach ($stream in $streams.Values) { $stream.Dispose() }
    }
}
function Handle-Identity($Handle) {
    $info = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
    try {
        if (-not [HwpInstall.NativeFile]::GetFileInformationByHandle($Handle, $info)) { Stop-Install 3 '열린 파일/폴더의 식별 정보를 읽을 수 없습니다.' }
        return ([Runtime.InteropServices.Marshal]::ReadInt32($info, 28).ToString() + ':' + [Runtime.InteropServices.Marshal]::ReadInt64($info, 44).ToString())
    } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info) }
}
function Remove-Empty([string]$Path) {
    if (-not [IO.Directory]::Exists($Path)) { return }
    $Path = Safe-Path $Path
    Protect-DirectoryPath $Path
    if (@(Get-ChildItem -LiteralPath $Path -Force).Count -ne 0) { return }
    $identity = Handle-Identity $script:DirectoryLocks[$Path]
    Release-DirectoryPath $Path
    # Reopening for DELETE is unavoidable after a deny-delete guard; reject any replacement identity.
    $handle = [HwpInstall.NativeFile]::CreateFileW($Path, 65664, 3, [IntPtr]::Zero, 3, 35651584, [IntPtr]::Zero)
    try {
        if ($handle.IsInvalid) { throw '빈 소유 폴더를 제거할 수 없습니다.' }
        Assert-HandlePath $handle $Path
        if ((Handle-Identity $handle) -cne $identity) { throw '소유 폴더가 교체되었습니다.' }
        $disposition = [Runtime.InteropServices.Marshal]::AllocHGlobal(1)
        try {
            [Runtime.InteropServices.Marshal]::WriteByte($disposition, 1)
            # The kernel rejects a now-nonempty directory; never recurse or delete by a new path lookup.
            if (-not [HwpInstall.NativeFile]::SetFileInformationByHandle($handle, 4, $disposition, 1)) { throw '소유 폴더를 제거하지 못했습니다.' }
        } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($disposition) }
    } catch {
        if (-not $script:Retained.Contains($Path)) { $script:Retained.Add($Path) }
    } finally { $handle.Dispose() }
}
function Release-UninstallVerification {
    foreach ($lock in $script:OwnershipLocks) { $lock.Dispose() }
    $script:OwnershipLocks.Clear()
    if ($null -ne $script:WrapperGuard) {
        # Only held streams/handles cross the wrapper boundary, never executable delegates or commands.
        foreach ($stream in $script:WrapperGuard['files'].Values) {
            if ($stream -is [IO.FileStream]) { $stream.Dispose() }
        }
        foreach ($handle in $script:WrapperGuard['directories'].Values) {
            if ($handle -is [Microsoft.Win32.SafeHandles.SafeFileHandle]) { $handle.Dispose() }
        }
        $script:WrapperGuard = $null
    }
}

try {
    if ($env:OS -cne 'Windows_NT') { Stop-Install 3 'Windows 전용 설치 도구입니다.' }
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    $InstallDirectory = Safe-Path $InstallDirectory
    Initialize-FileInspection
    $owned = $null
    if (Test-Path -LiteralPath $InstallDirectory) { $owned = Load-Receipt }
    $SourceDirectory = Safe-Path $SourceDirectory
    $ShortcutDirectory = Safe-Path $ShortcutDirectory
    $LogDirectory = Safe-Path $LogDirectory
    $result.install_directory = $InstallDirectory
    Validate-RegistryPath $RegistrySubKey
    if ((Under $InstallDirectory $SourceDirectory) -or (Under $SourceDirectory $InstallDirectory)) {
        if ($Operation -ne 'Uninstall' -or -not $SourceDirectory.Equals($InstallDirectory, [StringComparison]::OrdinalIgnoreCase)) { Stop-Install 3 '배포 원본과 설치 폴더가 겹칩니다.' }
    }
    foreach ($path in @($ShortcutDirectory, $LogDirectory)) {
        if ((Under $path $InstallDirectory) -or (Under $InstallDirectory $path) -or (Under $path $SourceDirectory) -or (Under $SourceDirectory $path)) { Stop-Install 3 '설치/배포/바로가기/로그 경로가 겹칩니다.' }
    }
    if ((Under $ShortcutDirectory $LogDirectory) -or (Under $LogDirectory $ShortcutDirectory)) { Stop-Install 3 '바로가기와 로그 경로가 겹칩니다.' }
    if ($Operation -eq 'Uninstall') {
        $guard = Get-Variable -Name HwpUninstallGuard -Scope 1 -ErrorAction SilentlyContinue
        if ($null -ne $guard -and $guard.Value -is [Collections.IDictionary] -and $guard.Value['contract'] -ceq 'hwp-uninstall-verification-v1' -and $guard.Value['engine_path'] -ieq $PSCommandPath -and ([string]$guard.Value['install_directory']).Equals($InstallDirectory, [StringComparison]::OrdinalIgnoreCase)) { $script:WrapperGuard = $guard.Value }
    }
    if ($Operation -ne 'Check') { New-Run }
    if ($Operation -eq 'Uninstall') {
        if (-not (Test-Path -LiteralPath $InstallDirectory)) {
            $result.status = 'already_absent'; $script:ExitCode = 0
        } else {
            $result.receipt = Join-Path $InstallDirectory 'INSTALL-RECEIPT.json'
            Assert-NotRunning
            # Copy the exact ownership record to logs before any deletion.
            Write-NewJson (Join-Path $script:Run 'receipt-before-uninstall.json') $owned.receipt
            Remove-OwnedRegistry $owned.registry
            Remove-Exact (Join-Path $ShortcutDirectory 'HWP Editor.lnk') @{bytes=$owned.receipt.shortcut_bytes; sha256=$owned.receipt.shortcut_sha256}
            foreach ($name in $owned.payload.files.Keys) {
                if ($name -notin @('INSTALL-MANIFEST.json', 'Install-HWPReview.ps1', 'Uninstall-HWPReview.ps1')) { Remove-Exact (Join-Path $InstallDirectory $name.Replace('/', '\')) $owned.payload.files[$name] }
            }
            Remove-Empty (Join-Path $InstallDirectory 'licenses')
            Remove-Empty $ShortcutDirectory
            if ([IO.Directory]::Exists($ShortcutDirectory)) {
                foreach ($item in Get-ChildItem -LiteralPath $ShortcutDirectory -Force) {
                    if (-not $script:Retained.Contains($item.FullName)) { $script:Retained.Add($item.FullName) }
                }
            }
            # Keep the receipt/core if anything changed, so a later explicit uninstall remains bounded.
            $remaining = @(Get-ChildItem -LiteralPath $InstallDirectory -Force | Where-Object { $_.Name -cnotin @('INSTALL-RECEIPT.json', 'INSTALL-MANIFEST.json', 'Install-HWPReview.ps1', 'Uninstall-HWPReview.ps1') })
            foreach ($item in $remaining) { if (-not $script:Retained.Contains($item.FullName)) { $script:Retained.Add($item.FullName) } }
            if ($script:Retained.Count -eq 0) {
                $core = [ordered]@{}
                foreach ($name in @('Install-HWPReview.ps1', 'Uninstall-HWPReview.ps1', 'INSTALL-MANIFEST.json')) {
                    $core[(Join-Path $InstallDirectory $name)] = $owned.payload.files[$name]
                }
                $core[$result.receipt] = $owned.receipt_file
                Remove-ExactCore $core
                if ($script:Retained.Count -eq 0) { Remove-Empty $InstallDirectory }
                if ($script:Retained.Count -eq 0) { $result.status = 'uninstalled'; $script:ExitCode = 0 }
                else { $result.status = 'uninstalled_with_retained'; $script:ExitCode = 3 }
            } else { $result.status = 'uninstalled_with_retained'; $script:ExitCode = 3 }
        }
    } else {
        $payload = Load-Manifest $SourceDirectory -Hold
        Validate-Source $payload
        if (Test-Path -LiteralPath $InstallDirectory) {
            Assert-ExistingExact $owned $payload
            $result.receipt = Join-Path $InstallDirectory 'INSTALL-RECEIPT.json'
            $result.status = 'already_installed'; $script:ExitCode = 0
        } else {
            if (Test-Path -LiteralPath $ShortcutDirectory) { Stop-Install 3 '바로가기 폴더가 이미 있습니다. 기존 사용자 파일은 덮어쓰지 않습니다.' }
            if (Registry-Exists) { Stop-Install 3 '현재 사용자 앱 등록이 이미 있습니다. 덮어쓰지 않습니다.' }
            if ($Operation -eq 'Check') {
                $result.status = 'payload_valid_no_mutation'; $script:ExitCode = 0
                $result.detail = '파일/경로/충돌만 확인했습니다. 필수 항목 설치 상태는 실행하지 않았습니다.'
            } else {
                if (-not (Requirements-Check 'requirements-before')) {
                    $setupCode = Run-Exe '--setup-requirements' 'setup-requirements'
                    if ($setupCode -eq 1602 -or $setupCode -eq 3010) { Stop-Install $setupCode '필수 항목 준비가 취소되었거나 재부팅이 필요합니다. 로그를 보존하고 중단합니다.' }
                    if ($setupCode -ne 0) { Stop-Install 5 '필수 항목 준비가 실패/취소되었거나 재부팅이 필요합니다. 자동 재시도하지 않습니다.' }
                    if (-not (Requirements-Check 'requirements-after')) { Stop-Install 5 '필수 항목을 모두 확인하지 못했습니다. 앱을 시작하지 않습니다.' }
                }
                # Preflight is repeated only for collision admission, not an install retry.
                if ((Test-Path -LiteralPath $InstallDirectory) -or (Test-Path -LiteralPath $ShortcutDirectory) -or (Registry-Exists)) { Stop-Install 3 '준비 중 설치 대상이 생겼습니다. 덮어쓰지 않습니다.' }
                $id = [Guid]::NewGuid().ToString('N')
                $registry = Expected-Registry $payload.manifest.version $id $LogDirectory
                $shell = New-Object -ComObject WScript.Shell
                $temporaryShortcut = Join-Path $script:Run 'HWP Editor.lnk'
                $link = $shell.CreateShortcut($temporaryShortcut)
                $link.TargetPath = Join-Path $InstallDirectory 'HWPReview.exe'
                $link.WorkingDirectory = $InstallDirectory
                $link.IconLocation = (Join-Path $InstallDirectory 'HWPReview.exe') + ',0'
                $link.Description = 'HWP Editor'
                $link.Save()
                $shortcutHash = File-Hash $temporaryShortcut
                $receipt = [ordered]@{contract='hwp-user-install-receipt-v2'; install_id=$id; version=$payload.manifest.version; install_directory=$InstallDirectory; log_directory=$LogDirectory; manifest_sha256=$payload.hash; shortcut_directory=$ShortcutDirectory; shortcut_sha256=$shortcutHash; shortcut_bytes=(Get-Item -LiteralPath $temporaryShortcut).Length; registry_subkey=$RegistrySubKey; app_launch_enabled=$false}
                Ensure-Directory ([IO.Path]::GetDirectoryName($InstallDirectory))
                if (Test-Path -LiteralPath $InstallDirectory) { Stop-Install 3 '설치 폴더 충돌입니다.' }
                New-OwnedDirectory $InstallDirectory
                $result.receipt = Join-Path $InstallDirectory 'INSTALL-RECEIPT.json'
                Write-NewJson $result.receipt $receipt
                Copy-New 'INSTALL-MANIFEST.json' $payload
                Ensure-Directory (Join-Path $InstallDirectory 'licenses')
                foreach ($name in $payload.files.Keys) { if ($name -cne 'INSTALL-MANIFEST.json') { Copy-New $name $payload } }
                Ensure-Directory ([IO.Path]::GetDirectoryName($ShortcutDirectory))
                if (Test-Path -LiteralPath $ShortcutDirectory) { Stop-Install 3 '바로가기 폴더 충돌입니다.' }
                New-OwnedDirectory $ShortcutDirectory
                $shortcutInput = Open-Regular $temporaryShortcut
                try {
                    $shortcutOutput = Open-Regular (Join-Path $ShortcutDirectory 'HWP Editor.lnk') -CreateNew
                    try { $shortcutInput.CopyTo($shortcutOutput); $shortcutOutput.Flush($true) } finally { $shortcutOutput.Dispose() }
                } finally { $shortcutInput.Dispose() }
                if (Registry-Exists) { Stop-Install 3 '현재 사용자 등록 충돌입니다.' }
                # RegCreateKeyEx disposition makes ownership admission atomic, even for an empty foreign key.
                $handle = [IntPtr]::Zero
                $disposition = 0
                $registryCode = [HwpInstall.NativeFile]::RegCreateKeyExW([IntPtr](-2147483647), $RegistrySubKey, 0, $null, 0, 131103, [IntPtr]::Zero, [ref]$handle, [ref]$disposition)
                if ($registryCode -ne 0) { Stop-Install 1 '현재 사용자 등록 키를 만들 수 없습니다.' }
                $safeHandle = New-Object Microsoft.Win32.SafeHandles.SafeRegistryHandle($handle, $true)
                $key = [Microsoft.Win32.RegistryKey]::FromHandle($safeHandle)
                try {
                    if ($disposition -ne 1) { Stop-Install 3 '현재 사용자 등록 키가 이미 있습니다. 빈 키도 덮어쓰지 않습니다.' }
                    if ($key.GetValueNames().Count -ne 0 -or $key.GetSubKeyNames().Count -ne 0) { Stop-Install 3 '현재 사용자 등록이 비어 있지 않습니다.' }
                    foreach ($name in $registry.Keys) {
                        $kind = if ($name -in @('NoModify', 'NoRepair')) { [Microsoft.Win32.RegistryValueKind]::DWord } else { [Microsoft.Win32.RegistryValueKind]::String }
                        $key.SetValue($name, $registry[$name], $kind)
                    }
                    $key.Flush()
                } finally { $key.Dispose() }
                if (-not (Registry-Matches $registry)) { Stop-Install 1 '현재 사용자 등록 검증에 실패했습니다. 부분 설치/로그를 보존합니다.' }
                $result.status = 'installed'; $script:ExitCode = 0
            }
        }
    }
} catch {
    $result.detail = $_.Exception.Message
} finally {
    foreach ($lock in $script:Locks) { $lock.Dispose() }
    foreach ($lock in $script:OwnershipLocks) { $lock.Dispose() }
    $result.exit_code = $script:ExitCode
    $result.retained = @($script:Retained.ToArray())
    if ($script:Run) {
        try { Write-NewJson (Join-Path $script:Run 'operation.json') $result }
        catch { $result.detail = [string]$result.detail + ' 작업 결과 기록 실패: ' + $_.Exception.Message; $result.exit_code = 1; $script:ExitCode = 1 }
    }
    foreach ($handle in $script:DirectoryLocks.Values) { $handle.Dispose() }
    $result | ConvertTo-Json -Depth 16
}
exit $script:ExitCode
