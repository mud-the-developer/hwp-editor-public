[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Exe,
    [Parameter(Mandatory)][string]$Workspace,
    [Parameter(Mandatory)][string]$Managed,
    [Parameter(Mandatory)][string]$Dotnet,
    [Parameter(Mandatory)][string]$OutputRoot
)
# Only the smoke driver calls this, after exact kit, install and readiness checks.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
if (-not [Environment]::UserInteractive -or $env:HWP_REVIEW_REQUIRE_PREPARED -cne '1') {
    throw 'A prepared interactive user session is required'
}
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Drawing
Add-Type -ReferencedAssemblies System.dll,System.Drawing.dll -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
public static class SmokeJob {
    // Anonymous job, no limits, no breakaway and NO kill-on-close. Enroll the
    // observer before creating the EXE so even short-lived helpers are accounted for.
    [StructLayout(LayoutKind.Sequential)] struct Accounting {
        public long User, Kernel, PeriodUser, PeriodKernel;
        public uint Faults, Total, Active, Terminated;
    }
    [StructLayout(LayoutKind.Sequential)] struct PortInfo { public IntPtr Key, Port; }
    static IntPtr job, port;
    [DllImport("kernel32", SetLastError=true)] static extern IntPtr CreateIoCompletionPort(IntPtr file, IntPtr existing, IntPtr key, uint concurrency);
    [DllImport("kernel32", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr j, int kind, ref PortInfo info, uint length);
    [DllImport("kernel32", SetLastError=true)] static extern bool GetQueuedCompletionStatus(IntPtr port, out uint message, out IntPtr key, out IntPtr pid, uint timeout);
    [DllImport("kernel32", SetLastError=true)] static extern IntPtr CreateJobObjectW(IntPtr sa, IntPtr name);
    [DllImport("kernel32", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr j, IntPtr p);
    [DllImport("kernel32", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr j, int kind, out Accounting a, uint size, IntPtr used);
    [DllImport("kernel32", SetLastError=true)] static extern bool IsProcessInJob(IntPtr p, IntPtr j, out bool member);
    [DllImport("kernel32", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr p, out uint code);
    [DllImport("user32")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32")] static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32")] static extern bool IsWindowEnabled(IntPtr hwnd);
    [DllImport("user32", SetLastError=true)] static extern bool PostMessageW(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam);
    public static void Start() {
        job = CreateJobObjectW(IntPtr.Zero, IntPtr.Zero);
        port = CreateIoCompletionPort(new IntPtr(-1), IntPtr.Zero, IntPtr.Zero, 1);
        PortInfo info = new PortInfo { Key = new IntPtr(1), Port = port };
        if(job == IntPtr.Zero || port == IntPtr.Zero ||
           !SetInformationJobObject(job, 7, ref info, (uint)Marshal.SizeOf(typeof(PortInfo))) ||
           !AssignProcessToJobObject(job, Process.GetCurrentProcess().Handle))
            throw new Win32Exception();
    }
    public static bool Member(Process p) {
        bool member; if(!IsProcessInJob(p.Handle, job, out member)) throw new Win32Exception();
        return member && !p.HasExited;
    }
    public static int NormalExits() {
        Accounting a; if(!QueryInformationJobObject(job, 1, out a, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero)) throw new Win32Exception();
        if(a.Active != 1 || a.Terminated != 0 || a.Total < 6 || a.Total > 512) throw new InvalidOperationException("Unexpected job lifetime");
        // Normal exits must account for *every* descendant, including ephemeral
        // native helpers. Missing job notifications fail closed, not guessed away.
        int exits = 0;
        for(;;) {
            uint message; IntPtr key, pid;
            if(!GetQueuedCompletionStatus(port, out message, out key, out pid, exits == a.Total - 1 ? 0u : 1500u)) {
                if(Marshal.GetLastWin32Error() == 258 && exits == a.Total - 1) return exits;
                throw new Win32Exception();
            }
            if(key != new IntPtr(1) || message == 8) throw new InvalidOperationException("Abnormal owned process exit");
            if(message == 7) ++exits;
        }
    }
    public static uint Active() {
        Accounting a; if(!QueryInformationJobObject(job, 1, out a, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero)) throw new Win32Exception();
        return a.Active;
    }
    public static uint ExitCode(Process p) {
        uint code; if(!p.HasExited || !GetExitCodeProcess(p.Handle, out code)) throw new InvalidOperationException("Exit not observed");
        return code;
    }
    public static void Window(IntPtr hwnd, Process wpf) {
        uint pid; if(!Member(wpf) || GetWindowThreadProcessId(hwnd, out pid) == 0 || pid != wpf.Id || !IsWindowVisible(hwnd) || !IsWindowEnabled(hwnd))
            throw new InvalidOperationException("WPF owner changed, disabled or not visible");
    }
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int left, top, right, bottom; }
    [DllImport("user32")] static extern bool GetWindowRect(IntPtr h, out Rect r);
    [DllImport("user32")] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
    public static void Capture(IntPtr hwnd, Process wpf, string path) {
        Window(hwnd, wpf);
        Rect r; if (!GetWindowRect(hwnd, out r)) throw new Win32Exception();
        using (var image = new System.Drawing.Bitmap(r.right - r.left, r.bottom - r.top)) {
            using (var g = System.Drawing.Graphics.FromImage(image)) {
                var dc = g.GetHdc();
                try { if (!PrintWindow(hwnd, dc, 2)) throw new InvalidOperationException("Owned window capture failed"); }
                finally { g.ReleaseHdc(dc); }
            }
            using (var f = new System.IO.FileStream(path, System.IO.FileMode.CreateNew))
                image.Save(f, System.Drawing.Imaging.ImageFormat.Png);
        }
    }
    public static void CloseOnce(IntPtr hwnd, Process wpf) {
        Window(hwnd, wpf);
        if(!PostMessageW(hwnd, 0x10, IntPtr.Zero, IntPtr.Zero)) throw new Win32Exception();
    }
}
'@
function Save-New($name, $object) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($object | ConvertTo-Json -Depth 6) + "`n")
    $file = [IO.File]::Open((Join-Path $OutputRoot $name), 'CreateNew', 'Write', 'Read')
    try { $file.Write($bytes, 0, $bytes.Length); $file.Flush($true) } finally { $file.Dispose() }
}
function Child-At($parent, [string]$image) {
    if (-not [SmokeJob]::Member($parent)) { throw 'Expected owned parent is no longer live' }
    $name = [IO.Path]::GetFileName($image)
    $rows = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($parent.Id) AND Name='$name'" -OperationTimeoutSec 4)
    if ($rows.Count -eq 0) { return }
    if ($rows.Count -ne 1) { throw "Ambiguous owned $name child" }
    $p = Get-Process -Id $rows[0].ProcessId
    # Both process handles remain held. Do not inspect any window until the image,
    # parent, session and lifetime membership are established on this exact child.
    if (-not [SmokeJob]::Member($p) -or $p.Path -ine $image -or $p.SessionId -ne $parent.SessionId -or
        $p.StartTime -lt $parent.StartTime -or $rows[0].ExecutablePath -ine $image) { throw "Unexpected $name identity" }
    return $p
}
function Observe([string]$stage, [ScriptBlock]$probe) {
    do {
        if ($app.HasExited) { throw "Application exited before $stage" }
        $found = & $probe
        if ($found) { return $found }
        Start-Sleep -Milliseconds 180
    } while ($timer.Elapsed.TotalSeconds -lt 135)
    throw "Not observed: $stage. No action replayed or process terminated."
}
function Element($root, [string]$id) {
    $condition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty, $id)
    $e = $root.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
    if ($null -ne $e -and $e.Current.ProcessId -ne $wpf.Id) { throw "Foreign UIA target: $id" }
    return $e
}
function Ready-Window {
    [SmokeJob]::Window($hwnd, $wpf)
    $root = [Windows.Automation.AutomationElement]::FromHandle($hwnd)
    if ($root.Current.ProcessId -ne $wpf.Id -or $root.Current.Name -cne $title -or $root.Current.FrameworkId -cne 'WPF') {
        throw 'Wrong window owner, framework or synthetic workspace title'
    }
    $sync = Element $root 'SyncStatusText'
    $idle = Element $root 'OmpStateText'
    $ready = Element $root 'RefreshButton'
    if ($sync -and $idle -and $ready -and $ready.Current.IsEnabled -and
        -not $sync.Current.IsOffscreen -and -not $idle.Current.IsOffscreen -and
        $sync.Current.Name -ceq '실시간 동기화 중' -and $idle.Current.Name.StartsWith('OMP · ', [StringComparison]::Ordinal)) {
        return @{sync=$sync.Current.Name; idle=$idle.Current.Name}
    }
}
if ([IO.Path]::GetFullPath($Exe) -ine (Join-Path $OutputRoot 'install\HWPReview.exe') -or
    [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Workspace)) -ine $OutputRoot -or
    [IO.Path]::GetFileName($Workspace) -cnotmatch '^Synthetic-[a-f0-9]{32}$' -or
    $env:LOCALAPPDATA -ine (Join-Path $OutputRoot 'LOCALAPPDATA') -or $env:HOME -ine (Join-Path $OutputRoot 'HOME')) {
    throw 'Executable, workspace and isolated profile must belong to the same new smoke scope'
}
# Read the *installed* shortcut without invoking, creating or saving a new link.
$linkFile = Join-Path $OutputRoot 'shortcut\HWP Editor.lnk'
if (-not [IO.File]::Exists($linkFile)) { throw 'Missing receipted shortcut' }
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($linkFile)
if ($link.TargetPath -ine $Exe -or $link.WorkingDirectory -ine [IO.Path]::GetDirectoryName($Exe) -or $link.Arguments) {
    throw 'Shortcut does not point directly to the installed executable'
}
[SmokeJob]::Start()
$info = New-Object Diagnostics.ProcessStartInfo
$info.FileName = $Exe
$info.Arguments = '--workspace "' + $Workspace + '"'
$info.WorkingDirectory = [IO.Path]::GetDirectoryName($Exe)
$info.UseShellExecute = $false
$app = [Diagnostics.Process]::Start($info)
if (-not [SmokeJob]::Member($app) -or $app.Path -ine $Exe) { throw 'Installed bootloader identity differs' }
Save-New 'launch.json' @{pid=$app.Id; exe=$app.Path; workspace=$Workspace}
$timer = [Diagnostics.Stopwatch]::StartNew()
# A PyInstaller one-file EXE unpacks and starts a second copy of its exact
# installed image. dotnet is a direct child of that host, not a machine-wide search.
$hostChild = Observe 'installed unpacked host' { Child-At $app $Exe }
$wpf = Observe 'owned WPF process' { Child-At $hostChild $Dotnet }
$title = 'HWP Editor — ' + [IO.Path]::GetFileName($Workspace)
$hwnd = Observe 'visible owned WPF window' {
    $wpf.Refresh()
    if (-not $wpf.HasExited -and $wpf.MainWindowHandle -ne [IntPtr]::Zero -and $wpf.MainWindowTitle -ceq $title) {
        return $wpf.MainWindowHandle
    }
}
$null = Observe 'rendered HWPX and known idle OMP' { Ready-Window }
$ompPath = Join-Path $Managed 'bin\omp.exe'
$bunPath = Join-Path $Managed 'bin\bun.exe'
$omp = Observe 'app-managed OMP' { Child-At $wpf $ompPath }
$bun = Observe 'app-managed Bun' { Child-At $omp $bunPath }
$observed = @{
    contract='hwp-owned-installed-window-v1'; exe=$Exe; title=$title; hwnd=$hwnd.ToInt64();
    pids=@{app=$app.Id; host=$hostChild.Id; wpf=$wpf.Id; omp=$omp.Id; bun=$bun.Id};
    managed=$Managed; omp_sha256=(Get-FileHash -LiteralPath $ompPath -Algorithm SHA256).Hash.ToLowerInvariant();
    bun_sha256=(Get-FileHash -LiteralPath $bunPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
[SmokeJob]::Capture($hwnd, $wpf, (Join-Path $OutputRoot 'installed-window.png'))
$state = Ready-Window
if (-not $state -or -not [SmokeJob]::Member($bun) -or -not [SmokeJob]::Member($omp)) { throw 'Owned idle state changed before close' }
$observed.sync = $state.sync; $observed.idle = $state.idle
Save-New 'before-close.json' $observed
[SmokeJob]::CloseOnce($hwnd, $wpf)
$closing = [Diagnostics.Stopwatch]::StartNew()
$codes = @{}
foreach ($pair in @{app=$app; host=$hostChild; wpf=$wpf; omp=$omp; bun=$bun}.GetEnumerator()) {
    $remaining = [Math]::Max(0, 50000 - [int]$closing.ElapsedMilliseconds)
    if (-not $pair.Value.WaitForExit($remaining)) { throw "Owned $($pair.Key) did not exit on the single normal close" }
    $codes[$pair.Key] = [SmokeJob]::ExitCode($pair.Value)
    if ($codes[$pair.Key] -ne 0) { throw "Owned $($pair.Key) exited with code $($codes[$pair.Key])" }
}
foreach ($p in $app,$hostChild,$wpf,$omp,$bun) { $p.Dispose() }
# Do not mistake the five core processes for the whole tree: also wait for
# WebView2, ConPTY and any other helper inherited into this exact job.
while ([SmokeJob]::Active() -gt 1 -and $closing.ElapsedMilliseconds -lt 50000) { Start-Sleep -Milliseconds 120 }
if ([SmokeJob]::Active() -ne 1) { throw 'Other owned descendants remain; leave them untouched' }
$observed.exit_codes = $codes
$observed.normal_descendant_exits = [SmokeJob]::NormalExits()
$observed.remaining_descendants = 0
Save-New 'observed-window.json' $observed
