#requires -Version 5.1
<#
Opt-in, offline proof driver for an already-running NEW unsigned native fixture.
  powershell.exe -NoProfile -File tests\native_workspace_ui.ps1 `
    -HostReceipt C:\owned-fixture\HOST.json -Output C:\new-evidence -Scenario Inspect
Journey performs real native UI consent/actions and one fixed recorded review-copy edit.
It never launches/kills processes, calls HTTP, reads authority contents, or captures the desktop.
On the first failure leave everything in place; do not rerun Journey on that fixture.
This is the preserved Pi-SDK-era WPF candidate driver: ConnectModel/profile/counter
assumptions apply to exact pre-OMP candidate fixtures, NOT the current OMP MainWindow.
Its historical Journey is not native-OMP proof or permission to resume a stopped fixture.
Keep its behavior for those immutable candidate contracts; use current status for new TUI scope.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $HostReceipt,
    [Parameter(Mandatory = $true)][string] $Output,
    [ValidateSet('Inspect', 'Journey')][string] $Scenario = 'Inspect',
    [ValidateRange(10, 180)][int] $ObservationSeconds = 60
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -ne 'Desktop' -or [Environment]::OSVersion.Platform -ne 'Win32NT') {
    throw 'Use installed Windows PowerShell 5.1/.NET Framework on Windows; no installation is performed.'
}
$references = @('System', 'System.Core', 'System.Xml', 'System.Drawing', 'System.IO.Compression',
    'System.IO.Compression.FileSystem', 'UIAutomationClient', 'UIAutomationTypes', 'WindowsBase') | ForEach-Object {
    $assembly = [Reflection.Assembly]::LoadWithPartialName($_)
    if ($null -eq $assembly) { throw "Required installed framework assembly missing: $_" }
    $assembly.Location
}
if ($null -eq ('HwpNativeProof.Workspace' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'native_workspace_uia.cs') -ReferencedAssemblies $references
}
$HostReceipt = [HwpNativeProof.SyntheticFiles]::FullPath($HostReceipt)
$Output = [HwpNativeProof.SyntheticFiles]::FullPath($Output)
if ([IO.Path]::GetFileName($HostReceipt) -cne 'HOST.json' -or !(Test-Path -LiteralPath $HostReceipt -PathType Leaf)) {
    throw 'An existing absolute fixture HOST.json is required.'
}
if (Test-Path -LiteralPath $Output) { throw 'Output must be a NEW nonexistent absolute owned evidence directory.' }
$fixture = [IO.Path]::GetDirectoryName($HostReceipt)
if ([HwpNativeProof.SyntheticFiles]::Under($HostReceipt, $Output) -or $Output -eq $fixture) {
    throw 'Evidence output cannot replace or contain the fixture.'
}
$script:ownerPid = 0
$script:fixtureSource = ''
$script:fixtureMaterial = ''
$script:fixtureBase = ''
function Read-HostReceipt {
    # The parent publishes this file by atomic replacement. No token/origin is retained in evidence.
    $record = [IO.File]::ReadAllText($HostReceipt, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($record.contract -cne 'hwp-native-gui-host-v1' -or $record.model_boundary_mocked -isnot [bool] -or
        $record.os_handoff_mocked -isnot [bool] -or $record.automatic_permission_or_acceptance -isnot [bool] -or
        $record.model_boundary_mocked -ne $true -or $record.os_handoff_mocked -ne $true -or $record.automatic_permission_or_acceptance -ne $false) {
        throw 'Receipt does not authorize this unsigned synthetic native fixture.'
    }
    if ($record.status -eq 'failed_preserved') { throw 'Host reports failed_preserved; no further action is allowed.' }
    if ($null -eq $record.PSObject.Properties['ui_pid'] -or [int]$record.ui_pid -le 0) { throw 'Receipt has no actual owned UI child PID.' }
    if ($script:ownerPid -ne 0 -and [int]$record.ui_pid -ne $script:ownerPid) { throw 'Receipt UI PID changed; refusing attachment.' }
    $source = [HwpNativeProof.SyntheticFiles]::FullPath([string]$record.source)
    $material = [HwpNativeProof.SyntheticFiles]::FullPath([string]$record.material)
    $base = [HwpNativeProof.SyntheticFiles]::FullPath([string]$record.base)
    if (![HwpNativeProof.SyntheticFiles]::Under($source, $fixture) -or
        ![HwpNativeProof.SyntheticFiles]::Under($material, $fixture) -or
        ![HwpNativeProof.SyntheticFiles]::Under($base, $fixture) -or
        [HwpNativeProof.SyntheticFiles]::Under($source, $base) -or
        [IO.Path]::GetExtension($source) -ine '.hwpx' -or !(Test-Path -LiteralPath $source -PathType Leaf) -or
        !(Test-Path -LiteralPath $material -PathType Leaf)) { throw 'Synthetic receipt paths are outside the fresh fixture.' }
    if ($script:fixtureSource -ne '' -and ($source -ne $script:fixtureSource -or $material -ne $script:fixtureMaterial -or $base -ne $script:fixtureBase)) {
        throw 'Fixture path binding changed.'
    }
    if ($null -ne $record.PSObject.Properties['project'] -and $record.project) {
        if (![HwpNativeProof.SyntheticFiles]::Under([string]$record.project, $base)) { throw 'Receipt project is outside the synthetic base.' }
    }
    foreach ($opened in @($record.opens)) {
        $path = [HwpNativeProof.SyntheticFiles]::FullPath([string]$opened.path)
        if (![HwpNativeProof.SyntheticFiles]::Under($path, $base) -or [IO.Path]::GetFileName($path) -cne 'review.hwpx' -or
            [string]$opened.sha256 -cnotmatch '^[a-f0-9]{64}$') { throw 'Receipt contains an out-of-scope review handoff.' }
    }
    return $record
}
$initial = Read-HostReceipt
$script:ownerPid = [int]$initial.ui_pid
$script:fixtureSource = [HwpNativeProof.SyntheticFiles]::FullPath([string]$initial.source)
$script:fixtureMaterial = [HwpNativeProof.SyntheticFiles]::FullPath([string]$initial.material)
$script:fixtureBase = [HwpNativeProof.SyntheticFiles]::FullPath([string]$initial.base)
if ([HwpNativeProof.SyntheticFiles]::Under($Output, $script:fixtureBase) -or $Output -eq $script:fixtureBase) {
    throw 'Evidence/export output must be outside the project base.'
}
if ($Scenario -eq 'Journey' -and ($initial.status -ne 'ready' -or [int]$initial.provider_calls -ne 0 -or
    [int]$initial.profile_calls -ne 0 -or @($initial.opens).Count -ne 0 -or
    ($null -ne $initial.PSObject.Properties['project'] -and $initial.project))) {
    throw 'Journey requires a fresh ready fixture without any previous project/profile/model/handoff actions.'
}
[HwpNativeProof.SyntheticFiles]::CreateNewDirectory($Output)
$utf8 = New-Object Text.UTF8Encoding($false)
$script:phase = 'attach'
$script:sequence = 0
$script:events = New-Object 'System.Collections.Generic.List[object]'
$script:captures = New-Object 'System.Collections.Generic.List[object]'
$script:lastState = $null
$script:ui = $null
$script:evidence = [ordered]@{
    contract = 'hwp-native-workspace-ui-proof-v1'; scenario = $Scenario; status = 'running'
    started_utc = [DateTime]::UtcNow.ToString('o'); ui_pid = $script:ownerPid
    host_receipt = $HostReceipt; source = $script:fixtureSource; base = $script:fixtureBase
    driver_ps_sha256 = [HwpNativeProof.SyntheticFiles]::Sha256($PSCommandPath)
    driver_cs_sha256 = [HwpNativeProof.SyntheticFiles]::Sha256((Join-Path $PSScriptRoot 'native_workspace_uia.cs'))
    visual_claim = 'No visual PASS. Owned HWND PrintWindow captures require human inspection.'
    actions = @(); captures = @(); limits = @('Synthetic provider and OS handoff only; no real model/Hancom/layout/other-PC qualification.',
        'Parent owns compiler/source validation and final process/server shutdown checks.')
}
function Save-Evidence {
    $script:evidence.actions = @($script:events.ToArray())
    $script:evidence.captures = @($script:captures.ToArray())
    [IO.File]::WriteAllText((Join-Path $Output 'evidence.json'), ($script:evidence | ConvertTo-Json -Depth 16), $utf8)
}
function Record([string]$kind, [string]$target, $data) {
    $script:sequence++
    $script:events.Add([ordered]@{ sequence = $script:sequence; utc = [DateTime]::UtcNow.ToString('o')
        phase = $script:phase; kind = $kind; target = $target; data = $data })
    Save-Evidence
}
function Assert-True([bool]$condition, [string]$message) { if (!$condition) { throw $message } }
function Wait-Observed([scriptblock]$readOnly, [string]$description, [int]$seconds = $ObservationSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($seconds)
    do {
        $value = & $readOnly
        if ($value) { return $value }
        Start-Sleep -Milliseconds 120
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Observation timed out: $description. No action replay, cancellation, or process termination was performed."
}
function Check-Owner {
    [void](Read-HostReceipt)
    [void]$script:ui.Root()
}
function Capture-Phase([string]$name, $window = $null) {
    Check-Owner
    if ($null -eq $window) { $window = $script:ui.Root() }
    $path = Join-Path $Output ($name + '.png')
    Record 'capture-request' $name @{ hwnd_scope = 'exact-receipt-pid-owned-window'; path = $path }
    $pending = $script:ui.Capture($path, $window)
    $result = $pending.Finish(10000)
    $script:captures.Add([ordered]@{ phase = $script:phase; path = $result.Path; width = $result.Width; height = $result.Height
        printwindow_succeeded = $result.PrintWindowSucceeded; dark_sample_fraction = $result.DarkSampleFraction
        visual_assessment = $result.VisualAssessment })
    Save-Evidence
}
function Invoke-Once([string]$id) {
    Check-Owner
    $element = $script:ui.Require($id)
    Assert-True ($script:ui.Enabled($element)) "Action is not enabled: $id"
    Record 'invoke' $id @{ replay = $false }
    $pending = $script:ui.InvokeObserved($element)
    [void]$pending.Finish(15000)
}
function Wait-Ready {
    [void](Wait-Observed { $item = $script:ui.Find('NewReport'); $null -ne $item -and $script:ui.Enabled($item) } 'owned UI idle actions enabled')
}
function Select-Tab([int]$index) {
    Check-Owner
    Record 'selection' 'WorkspaceTabs' @{ index = $index }
    $script:ui.SelectTab($index)
}
function Expand-Details {
    Check-Owner
    Record 'expand-native-groups' 'selected-tab' @{}
    $script:ui.ExpandGroups()
}
function Set-Text([string]$id, [string]$value) {
    Check-Owner
    Record 'value' $id @{ characters = $value.Length; synthetic_only = $true }
    $script:ui.SetValue($script:ui.Require($id), $value)
}
function Set-Check([string]$id, [bool]$checked) {
    Check-Owner
    $element = $script:ui.Require($id)
    $state = $script:ui.ToggleState($element)
    Assert-True ($state -ne 2) "Indeterminate checkbox: $id"
    if (($state -eq 1) -ne $checked) {
        Record 'toggle' $id @{ checked = $checked }
        $script:ui.Toggle($element)
    }
    Assert-True (($script:ui.ToggleState($element) -eq 1) -eq $checked) "Checkbox did not reach intended state: $id"
}
function Set-Base([int]$index) {
    Check-Owner
    Record 'selection' 'RequestBase' @{ index = $index }
    $script:ui.SelectBase($index)
}
function Assert-Base([int]$index) {
    Check-Owner
    Record 'expand-for-observation' 'RequestBase' @{}
    $script:ui.ExpandBase()
    $actual = $script:ui.BaseIndex()
    $script:ui.CollapseBase()
    Assert-True ($actual -eq $index) 'Request base does not match the expected original/current revision choice.'
    Record 'base-observed' 'RequestBase' @{ index = $actual }
}
function Read-State {
    $json = $script:ui.TechnicalJson()
    if (!$json) { return $null }
    $state = $json | ConvertFrom-Json
    if ($null -eq $state.PSObject.Properties['revision'] -or $null -eq $state.PSObject.Properties['active'] -or
        $null -eq $state.PSObject.Properties['versions']) { throw 'Native technical state is not the report-state schema.' }
    $script:lastState = $state
    return $state
}
function Observe-State {
    Select-Tab 1
    Expand-Details
    return (Wait-Observed { Read-State } 'actual native report-state display')
}
function Record-State($state, [string]$label) {
    # Whitelist actual consumer state. Never serialize full technical JSON, authority, token, nonce, or credentials.
    $versions = @($state.versions | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; sha256 = $_.sha256
        accepted = $_.accepted; external = $_.external; request_id = $_.request_id } })
    Record 'state' $label @{ revision = $state.revision; active = $state.active; phase = $state.phase
        model_status = $state.model.status; model_operation_active = $state.model.operation_active
        version_count = $versions.Count; versions = $versions; request_count = @($state.requests).Count
        accepted = @($state.accepted); selection_ids = @($state.selection_ids)
        backend_error_present = [bool]$state.error; progress_present = ($null -ne $state.progress) }
}
function Assert-NoError($state) {
    Assert-True (!$state.error) 'Actual backend reports an error; preserved without replay.'
    Assert-True (@($state.issues).Count -eq 0) 'Actual report state has blocking integrity issues.'
}
function Assert-NoConsent {
    Assert-True ($script:ui.ToggleState($script:ui.Require('DraftConsent')) -eq 0) 'Consent unexpectedly inherited after an edit or switch.'
    Assert-True (!$script:ui.Enabled($script:ui.Require('StartDraft'))) 'Draft action enabled without current explicit consent.'
}
function Counters([int]$provider, [int]$profile = -1) {
    $receipt = Read-HostReceipt
    Assert-True ([int]$receipt.provider_calls -eq $provider) 'Unexpected provider call count; do not resend.'
    if ($profile -ge 0) { Assert-True ([int]$receipt.profile_calls -eq $profile) 'Unexpected profile boundary call count.' }
    Record 'boundary-counts' 'HOST.json' @{ provider_calls = [int]$receipt.provider_calls; profile_calls = [int]$receipt.profile_calls }
}
function Release-Provider([int]$call) {
    $record = Read-HostReceipt
    Assert-True ($record.provider_release_required -eq $true -and [int]$record.provider_waiting -eq $call) 'Exact synthetic provider wait is required.'
    $path = Join-Path $fixture ("PROVIDER-RELEASE-" + $call + '.json')
    Record 'release-synthetic-provider' 'HOST.json' @{ call = $call; authority = $false }
    $body = @{ contract = 'hwp-native-synthetic-provider-release-v1'; call = $call } | ConvertTo-Json -Compress
    $bytes = $utf8.GetBytes($body)
    $stream = New-Object IO.FileStream($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
function Permission-Count([string]$project) {
    $tasks = Join-Path $project 'tasks'
    if (!(Test-Path -LiteralPath $tasks -PathType Container)) { return 0 }
    # Count names only; never read/log authority JSON.
    return @(Get-ChildItem -LiteralPath $tasks -Directory | ForEach-Object {
        $path = Join-Path $_.FullName 'draft-permission.json'
        [void][HwpNativeProof.SyntheticFiles]::FullPath($path)
        if (Test-Path -LiteralPath $path -PathType Leaf) { $path }
    }).Count
}
function Inspect-Inventory {
    $projects = 0
    $permissions = 0
    $acceptances = 0
    if (Test-Path -LiteralPath $script:fixtureBase -PathType Container) {
        foreach ($directory in @(Get-ChildItem -LiteralPath $script:fixtureBase -Directory)) {
            [void][HwpNativeProof.SyntheticFiles]::FullPath($directory.FullName)
            $projects++
            $permissions += Permission-Count $directory.FullName
            $versions = Join-Path $directory.FullName 'versions'
            [void][HwpNativeProof.SyntheticFiles]::FullPath($versions)
            if (Test-Path -LiteralPath $versions -PathType Container) {
                foreach ($version in @(Get-ChildItem -LiteralPath $versions -Directory)) {
                    $acceptance = Join-Path $version.FullName 'acceptance.json'
                    [void][HwpNativeProof.SyntheticFiles]::FullPath($acceptance)
                    if (Test-Path -LiteralPath $acceptance -PathType Leaf) { $acceptances++ }
                }
            }
        }
    }
    return [pscustomobject]@{ projects = $projects; permissions = $permissions; acceptances = $acceptances }
}
function Native-Dialog([string]$trigger, [string]$path, [string]$name) {
    Check-Owner
    $path = [HwpNativeProof.SyntheticFiles]::FullPath($path)
    if ($trigger -eq 'NewReport') { Assert-True ($path -eq $script:fixtureSource) 'NewReport source must be the exact receipt original.' }
    elseif ($trigger -eq 'AddMaterial') { Assert-True ($path -eq $script:fixtureMaterial) 'Material must be the exact receipt synthetic material.' }
    elseif ($trigger -eq 'OpenReport') { Assert-True ([HwpNativeProof.SyntheticFiles]::Under($path, $script:fixtureBase)) 'Reopen path is outside the synthetic base.' }
    elseif ($trigger -eq 'ExportArtifact') {
        Assert-True ([HwpNativeProof.SyntheticFiles]::Under($path, $Output) -and !(Test-Path -LiteralPath $path)) 'Export requires a new owned evidence path.'
    }
    else { throw 'Dialog trigger is not in this fixed journey.' }
    $start = $null
    if ($trigger -eq 'NewReport' -or $trigger -eq 'OpenReport') {
        Record 'native-keyboard-shortcut' $trigger @{ control = $true; replay = $false }
        $script:ui.Shortcut($trigger)
    } else {
        Record 'invoke-modal' $trigger @{ replay = $false }
        $start = $script:ui.Invoke($script:ui.Require($trigger))
    }
    $dialog = Wait-Observed { $script:ui.Modal() } 'owned native common dialog'
    Capture-Phase ($name + '-dialog') $dialog
    Check-Owner
    Record 'dialog-value' $trigger @{ path = $path }
    $editor = $script:ui.DialogPath($dialog)
    Record 'dialog-editor' $trigger @{ id = $script:ui.Id($editor); name = $script:ui.Name($editor) }
    $script:ui.TypeDialogPath($dialog, $path)
    [void](Wait-Observed { $script:ui.Read($editor) -ceq $path } 'native dialog filename value observed')
    Record 'dialog-confirm' $trigger @{ replay = $false }
    $confirm = $script:ui.DialogConfirm($dialog)
    [void]$confirm.Finish(10000)
    if ($null -ne $start) { [void]$start.Finish(10000) }
    [void](Wait-Observed { $null -eq $script:ui.Modal() } 'native common dialog closed')
    Wait-Ready
}
function Artifact($version, [string]$project) {
    Assert-True ([string]$version.id -cmatch '^v-[a-f0-9]{32}$') 'Invalid actual version identifier.'
    $directory = Join-Path (Join-Path $project 'versions') ([string]$version.id)
    $metadataPath = [HwpNativeProof.SyntheticFiles]::FullPath((Join-Path $directory 'version.json'))
    Assert-True ([HwpNativeProof.SyntheticFiles]::Under($metadataPath, $project)) 'Version metadata escaped the owned project.'
    $metadata = [IO.File]::ReadAllText($metadataPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    Assert-True ($metadata.contract -ceq 'hwp-report-version-v1' -and $metadata.version_id -ceq $version.id -and $metadata.sha256 -ceq $version.sha256) 'Immutable output record differs from native state.'
    Assert-True ([IO.Path]::GetFileName([string]$version.filename) -ceq [string]$version.filename) 'Native artifact filename is not a leaf name.'
    $path = [HwpNativeProof.SyntheticFiles]::FullPath((Join-Path $directory ([string]$version.filename)))
    Assert-True (([HwpNativeProof.SyntheticFiles]::Sha256($path)) -ceq [string]$version.sha256) 'Actual canonical HWPX SHA differs from selected metadata.'
    Record 'canonical-artifact' ([string]$version.id) @{ path = $path; sha256 = $version.sha256; kind = $metadata.kind
        previous_version = $metadata.previous_version; request_id = $metadata.request_id }
    return [pscustomobject]@{ Path = $path; Metadata = $metadata }
}
function Assert-Selected($version) {
    # Match the actual artifact label supplied by native state, not translated UI sentences.
    [void](Wait-Observed {
        $title = $script:ui.Find('SelectedArtifactTitle')
        $null -ne $title -and $script:ui.Read($title).StartsWith([string]$version.label, [StringComparison]::Ordinal) -and
            $null -ne $script:ui.Find(('Version_' + $version.id))
    } 'requested exact native artifact selection')
    Record 'selected-artifact' 'SelectedArtifactTitle' @{ version_id = $version.id; sha256 = $version.sha256 }
}
function Check-Preserved {
    Assert-True (([HwpNativeProof.SyntheticFiles]::Sha256($script:fixtureSource)) -ceq $script:sourceSha) 'Synthetic original source bytes changed.'
    Assert-True (([HwpNativeProof.SyntheticFiles]::Sha256($script:projectOriginal)) -ceq $script:sourceSha) 'Captured project original source bytes changed.'
    foreach ($item in $script:canonicalRecords) {
        Assert-True (([HwpNativeProof.SyntheticFiles]::Sha256($item.path)) -ceq $item.sha256) 'Previously recorded immutable canonical bytes changed.'
    }
    Record 'preserved-files' 'original-and-canonicals' @{ source_sha256 = $script:sourceSha; canonical_count = $script:canonicalRecords.Count }
}
function Wait-Generated([int]$versionCount, [int]$providerCount) {
    [void](Wait-Observed {
        $receipt = Read-HostReceipt
        if ([int]$receipt.provider_calls -gt $providerCount) { throw 'Duplicate provider request observed; preserving failure.' }
        @($script:ui.Prefix('Version_')).Count -eq $versionCount -and $script:ui.Enabled($script:ui.Require('NewReport'))
    } 'actual generated native result/history and idle UI')
    $state = Observe-State
    Assert-NoError $state
    Assert-True (!$state.active -and @($state.versions).Count -eq $versionCount) 'Generated result is not settled.'
    Counters $providerCount
    Record-State $state 'settled-generation'
    return $state
}
try {
    Save-Evidence
    $beforeInventory = Inspect-Inventory
    if ($Scenario -eq 'Journey') {
        Assert-True ($beforeInventory.projects -eq 0 -and $beforeInventory.permissions -eq 0 -and $beforeInventory.acceptances -eq 0) 'Journey fixture base is not empty/fresh.'
    }
    $script:ui = New-Object -TypeName HwpNativeProof.Workspace -ArgumentList $script:ownerPid
    Record 'attached-owned-window' 'HwpNativeWorkspace' @{ hwnd = $script:ui.MainHandle }
    Capture-Phase '00-startup'
    $script:sourceSha = [HwpNativeProof.SyntheticFiles]::Sha256($script:fixtureSource)
    $script:canonicalRecords = New-Object 'System.Collections.Generic.List[object]'
    if ($Scenario -eq 'Inspect') {
        $script:phase = 'inspect-read-only'
        $controls = @('NewReport', 'OpenReport', 'RefreshState', 'ConnectModel', 'DraftConsent', 'StartDraft',
            'ActualBackendProgress', 'SelectedArtifactTitle', 'BlockingError') | ForEach-Object {
            $item = $script:ui.Find($_)
            [ordered]@{ id = $_; present = ($null -ne $item); enabled = $(if ($null -ne $item) { $script:ui.Enabled($item) } else { $false }) }
        }
        $after = Read-HostReceipt
        Assert-True ([int]$after.provider_calls -eq [int]$initial.provider_calls -and [int]$after.profile_calls -eq [int]$initial.profile_calls) 'Read-only inspection changed boundary counters.'
        $initialProject = if ($null -ne $initial.PSObject.Properties['project']) { [string]$initial.project } else { '' }
        $afterProject = if ($null -ne $after.PSObject.Properties['project']) { [string]$after.project } else { '' }
        Assert-True ($initialProject -eq $afterProject) 'Read-only inspection changed the active project.'
        $afterInventory = Inspect-Inventory
        Assert-True ($beforeInventory.projects -eq $afterInventory.projects -and $beforeInventory.permissions -eq $afterInventory.permissions -and
            $beforeInventory.acceptances -eq $afterInventory.acceptances -and @($initial.opens).Count -eq @($after.opens).Count) 'Read-only inspection changed project/authority/handoff inventory.'
        Assert-True (([HwpNativeProof.SyntheticFiles]::Sha256($script:fixtureSource)) -ceq $script:sourceSha) 'Read-only inspection changed the original source.'
        Record 'inspect-read-only' 'owned-window' @{ controls = @($controls); provider_calls = $after.provider_calls; profile_calls = $after.profile_calls
            project_changed = $false; invoked_actions = 0; inventory_before = $beforeInventory; inventory_after = $afterInventory }
        $script:evidence.status = 'inspect-complete-no-product-actions'
    }
    else {
        $script:phase = 'create-and-connect'
        Wait-Ready
        Counters 0 0
        Native-Dialog 'NewReport' $script:fixtureSource '01-new-report'
        $receipt = Read-HostReceipt
        $project = [HwpNativeProof.SyntheticFiles]::FullPath([string]$receipt.project)
        $script:projectOriginal = [HwpNativeProof.SyntheticFiles]::FullPath((Join-Path $project 'source.hwpx'))
        Assert-True (([HwpNativeProof.SyntheticFiles]::Sha256($script:projectOriginal)) -ceq $script:sourceSha) 'New report did not preserve the exact synthetic source bytes.'
        $state = Observe-State
        Assert-NoError $state
        Assert-True (@($state.versions).Count -eq 0 -and (Permission-Count $project) -eq 0) 'Opening a source created a version or task permission.'
        Counters 0 0
        $scopeBoxes = @($script:ui.Prefix('Scope_'))
        $bodyBoxes = @($scopeBoxes | Where-Object { $script:ui.Name($_).Contains('REPORT BODY BEFORE') })
        Assert-True ($bodyBoxes.Count -eq 1) 'Exactly one synthetic REPORT BODY BEFORE checkbox must exist.'
        $bodyId = $script:ui.Id($bodyBoxes[0])
        foreach ($box in $scopeBoxes) { Set-Check ($script:ui.Id($box)) ($script:ui.Id($box) -eq $bodyId) }
        $firstInstruction = 'SYNTHETIC UI JOURNEY: revise only the selected body; preserve every boundary, object, and measured value.'
        Set-Base 0
        Set-Text 'Instruction' $firstInstruction
        Invoke-Once 'ConnectModel'
        [void](Wait-Observed { $s = Read-State; $null -ne $s -and $s.model.status -eq 'ready' -and !$s.model.operation_active } 'actual synthetic profile connection ready')
        Wait-Ready
        Counters 0 1
        Assert-True ((Permission-Count $project) -eq 0) 'Profile connection created a draft permission.'
        Assert-NoConsent
        Capture-Phase '02-connected-no-permission'

        $script:phase = 'one-consent-generation-and-busy-guard'
        Set-Check 'DraftConsent' $true
        # Deliberately no PreviewRequest invocation: StartDraft owns the read-only preview.
        Check-Owner
        Record 'native-keyboard-shortcut' 'StartDraft' @{ control = $true; key = 'Enter'; replay = $false }
        $script:ui.Shortcut('StartDraft')
        [void](Wait-Observed { $s = Read-State; $null -ne $s -and $s.active -and [int](Read-HostReceipt).provider_calls -eq 1 } 'actual provider active/progress' 10)
        Assert-True (![string]::IsNullOrWhiteSpace($script:ui.Read($script:ui.Require('ActualBackendProgress')))) 'Active backend progress is not exposed.'
        Assert-True (!$script:ui.Enabled($script:ui.Require('NewReport')) -and !$script:ui.Enabled($script:ui.Require('OpenReport'))) 'Switch/new-report is not blocked while provider is active.'
        Check-Owner
        Record 'disabled-duplicate-gesture' 'StartDraft' @{ expected = 'ElementNotEnabled'; replay = $false }
        Assert-True ($script:ui.AttemptDisabledInvoke($script:ui.Require('StartDraft'))) 'Duplicate draft gesture was not rejected.'
        Record 'close-while-active' 'HwpNativeWorkspace' @{ expected = 'blocked'; replay = $false }
        $busyClose = $script:ui.Close()
        [void]$busyClose.Finish(10000)
        [void](Wait-Observed { $e = $script:ui.Find('BlockingError'); $null -ne $e -and ![string]::IsNullOrWhiteSpace($script:ui.Read($e)) } 'busy close blocking error' 10)
        Assert-True ($script:ui.MainExists) 'Busy close closed the owned UI.'
        $activeState = Read-State
        Assert-True ($null -ne $activeState -and $activeState.active -and $null -ne $activeState.progress) 'Provider finished before active guard evidence; preserve this run without replay.'
        Record-State $activeState 'observed-active-after-blocked-close'
        Capture-Phase '03-active-close-guard'
        Release-Provider 1
        $state = Wait-Generated 1 1
        $first = @($state.versions)[0]
        Assert-True (!$first.accepted -and !$first.external -and @($state.accepted).Count -eq 0) 'First generated HWPX is not an unaccepted generated version.'
        Assert-True ((Permission-Count $project) -eq 1) 'Single consent did not produce exactly one task permission.'
        $firstArtifact = Artifact $first $project
        $script:canonicalRecords.Add(@{ path = $firstArtifact.Path; sha256 = $first.sha256 })
        Select-Tab 2
        Assert-Selected $first
        Capture-Phase '04-first-generated-unaccepted'

        $script:phase = 'owned-review-copy-save-import-exact-adoption'
        Invoke-Once 'OpenReviewCopy'
        [void](Wait-Observed { @((Read-HostReceipt).opens).Count -eq 1 } 'actual recorded OS review-copy handoff')
        Wait-Ready
        $opened = @((Read-HostReceipt).opens)[0]
        $review = [HwpNativeProof.SyntheticFiles]::FullPath([string]$opened.path)
        $expectedParent = Join-Path (Join-Path $project 'review-copies') ([string]$first.id)
        Assert-True ([IO.Path]::GetDirectoryName($review) -eq $expectedParent -and $opened.sha256 -ceq $first.sha256) 'OS handoff is not this exact version review copy.'
        $copyManifestPath = [HwpNativeProof.SyntheticFiles]::FullPath((Join-Path $expectedParent 'review-copy.json'))
        $copyManifest = [IO.File]::ReadAllText($copyManifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
        Assert-True ($copyManifest.contract -ceq 'hwp-report-review-copy-v1' -and $copyManifest.version_id -ceq $first.id -and $copyManifest.initial_sha256 -ceq $first.sha256) 'Editable-copy manifest does not match exact selected canonical.'
        Record 'external-save-fixed-synthetic-edit' ([string]$first.id) @{ path = $review; original_sha256 = $opened.sha256 }
        $mutation = [HwpNativeProof.SyntheticFiles]::EditRecordedReview($review, [string]$opened.sha256,
            'SYNTHETIC REPORT BODY REVISED', 'SYNTHETIC REPORT BODY EDITED IN OWNED REVIEW COPY')
        Record 'external-save-complete' ([string]$first.id) @{ entry = $mutation.Entry; before_sha256 = $mutation.BeforeSha256
            after_sha256 = $mutation.AfterSha256; preserved_other_entries = $mutation.PreservedEntries }
        Check-Preserved
        Invoke-Once 'AcceptVersion'
        Wait-Ready
        [void](Wait-Observed { $blocking = $script:ui.Find('BlockingError'); $null -ne $blocking -and
            ![string]::IsNullOrWhiteSpace($script:ui.Read($blocking)) } 'old canonical visibly rejected after external save')
        $state = Observe-State
        Assert-True (@($state.accepted).Count -eq 0 -and (-not (@($state.versions)[0].accepted))) 'Rejected old-version action created an acceptance.'
        Assert-True (!(Test-Path -LiteralPath (Join-Path ([IO.Path]::GetDirectoryName($firstArtifact.Path)) 'acceptance.json'))) 'Old canonical acceptance authority unexpectedly exists.'
        Counters 1 1
        Select-Tab 2
        Capture-Phase '05-changed-copy-old-acceptance-rejected'
        Invoke-Once 'ImportOpenedCopy'
        Wait-Ready
        $state = Observe-State
        $state = Wait-Observed { $s = Read-State; if ($null -ne $s -and @($s.versions).Count -eq 2) { $s } } 'new immutable imported version in native state'
        Assert-NoError $state
        Assert-True (@($state.versions).Count -eq 2) 'Saved review copy did not become one new immutable version.'
        $imported = @($state.versions)[1]
        Assert-True ($imported.external -and !$imported.accepted -and $imported.id -ne $first.id) 'Imported artifact is not a distinct unaccepted external version.'
        $importedArtifact = Artifact $imported $project
        Assert-True ($importedArtifact.Metadata.previous_version -ceq $first.id -and $imported.sha256 -ceq $mutation.AfterSha256) 'Imported canonical is not the exact saved copy based on the selected version.'
        $script:canonicalRecords.Add(@{ path = $importedArtifact.Path; sha256 = $imported.sha256 })
        Select-Tab 2
        Assert-Selected $imported
        Invoke-Once 'AcceptVersion'
        Wait-Ready
        $state = Observe-State
        $importedId = [string]$imported.id
        $state = Wait-Observed {
            $s = Read-State
            if ($null -ne $s -and @($s.accepted) -contains $importedId) { $s }
        } 'exact imported artifact acceptance in native state'
        $imported = @($state.versions | Where-Object { $_.id -eq $imported.id })[0]
        Assert-True ($imported.accepted -and @($state.accepted).Count -eq 1 -and $state.accepted[0] -ceq $imported.id) 'Exact imported artifact was not the sole accepted artifact.'
        Counters 1 1
        Select-Tab 2
        Assert-Selected $imported
        Capture-Phase '06-imported-exact-artifact-adopted'
        Check-Preserved

        $script:phase = 'followup-materials-consent-and-history'
        Select-Tab 1
        Assert-Base 1
        Assert-NoConsent
        Set-Text 'Instruction' 'SYNTHETIC UI JOURNEY: follow up from the selected imported body with the explicitly attached synthetic evidence.'
        Set-Check 'DraftConsent' $true
        Native-Dialog 'AddMaterial' $script:fixtureMaterial '07-add-material'
        $materialBoxes = @($script:ui.Prefix('MaterialOwn_'))
        Assert-True ($materialBoxes.Count -eq 1 -and $script:ui.ToggleState($materialBoxes[0]) -eq 0) 'Uploaded material own-achievement must default to false.'
        Assert-NoConsent
        $materialOwnId = $script:ui.Id($materialBoxes[0])
        Set-Check 'DraftConsent' $true
        Set-Check $materialOwnId $true
        Assert-NoConsent
        Set-Check $materialOwnId $false
        Set-Check 'DraftConsent' $true
        Set-Text 'Instruction' 'SYNTHETIC UI JOURNEY: follow up from the selected imported body; use the synthetic reference without claiming group achievement.'
        Assert-NoConsent
        Set-Check 'DraftConsent' $true
        Invoke-Once 'StartDraft'
        [void](Wait-Observed { [int](Read-HostReceipt).provider_waiting -eq 2 } 'exact followup synthetic provider wait')
        Release-Provider 2
        $state = Wait-Generated 3 2
        $followup = @($state.versions)[2]
        Assert-True (!$followup.accepted -and !$followup.external) 'Followup is not a new unaccepted generated HWPX.'
        $followupArtifact = Artifact $followup $project
        Assert-True ($followupArtifact.Metadata.previous_version -ceq $imported.id) 'Followup did not use the exact selected imported revision.'
        $script:canonicalRecords.Add(@{ path = $followupArtifact.Path; sha256 = $followup.sha256 })
        Assert-True ((Permission-Count $project) -eq 2) 'Followup consent did not create exactly one additional permission.'
        Select-Tab 2
        Assert-Selected $followup
        Expand-Details
        Assert-True (@($followup.comparison).Count -gt 0) 'Actual followup does not expose before/after comparison.'
        $comparison = @($followup.comparison)[0]
        Assert-True ($script:ui.ContainsExtractedText([string]$comparison.before) -and
            $script:ui.ContainsExtractedText([string]$comparison.after)) 'Expanded native comparison does not show the actual artifact before/after text.'
        Check-Owner
        Record 'scroll-native-viewport' 'actual-comparison' @{ percent = 50; comparison_count = @($followup.comparison).Count }
        [void]$script:ui.ScrollViewport(50)
        Capture-Phase '08-followup-actual-comparison'
        Check-Owner
        Record 'scroll-native-viewport' 'actual-comparison-and-history' @{ percent = 100 }
        [void]$script:ui.ScrollViewport(100)
        Capture-Phase '08-followup-comparison-history-lower'
        Invoke-Once ('Version_' + $first.id)
        Assert-Selected $first
        Invoke-Once 'RefreshState'
        Wait-Ready
        Assert-Selected $first
        Counters 2 1
        Capture-Phase '09-explicit-history-survives-refresh'

        $script:phase = 'duplicate-import-selects-exact-existing-artifact'
        Invoke-Once 'ImportOpenedCopy'
        Wait-Ready
        $state = Observe-State
        Assert-NoError $state
        Assert-True (@($state.versions).Count -eq 3) 'Repeated identical import created an extra version.'
        Counters 2 1
        Assert-NoConsent
        Select-Tab 2
        Assert-Selected $imported
        Capture-Phase '09-existing-import-selected-not-latest'
        Check-Preserved
        Invoke-Once ('Version_' + $followup.id)
        Assert-Selected $followup

        $script:phase = 'native-export-exact-selected-bytes'
        $export = Join-Path $Output 'selected-followup-export.hwpx'
        Native-Dialog 'ExportArtifact' $export '10-export'
        Assert-True (([HwpNativeProof.SyntheticFiles]::Sha256($export)) -ceq $followup.sha256) 'Native export bytes do not match the exact selected canonical SHA.'
        Record 'exported-exact-bytes' ([string]$followup.id) @{ path = $export; sha256 = $followup.sha256; existing_overwrite = $false }
        Capture-Phase '11-export-complete'
        Check-Preserved

        $script:phase = 'fresh-project-switch-reopen-current-base'
        Native-Dialog 'NewReport' $script:fixtureSource '12-switch-fresh-project'
        $otherProject = [string](Read-HostReceipt).project
        Assert-True ($otherProject -ne $project) 'Explicit new report did not create a distinct fresh synthetic project.'
        $state = Observe-State
        Assert-True (@($state.versions).Count -eq 0 -and (Permission-Count $otherProject) -eq 0) 'Fresh project switch inherited versions or authority.'
        Assert-NoConsent
        Assert-True (@($script:ui.Prefix('MaterialOwn_')).Count -eq 0 -and $script:ui.Read($script:ui.Require('Instruction')) -eq '') 'Fresh project inherited request/material intent.'
        Native-Dialog 'OpenReport' $project '13-reopen-original-project'
        Assert-True ([string](Read-HostReceipt).project -eq $project) 'Native folder dialog did not reopen the exact owned project.'
        $state = Observe-State
        Assert-NoError $state
        Assert-True (@($state.versions).Count -eq 3) 'Reopen lost immutable report history.'
        Assert-Base 1
        Assert-NoConsent
        Assert-True (@($script:ui.Prefix('MaterialOwn_')).Count -eq 0) 'Reopen inherited expiring material IDs.'
        Select-Tab 2
        Assert-Selected $followup
        Capture-Phase '14-reopened-latest-current-base'
        Counters 2

        $script:phase = 'deterministic-existing-result-no-resend'
        Select-Tab 1
        Expand-Details
        Set-Base 0
        # Same source/scoped body/instruction and NO materials as the first request.
        $scopeBoxes = @($script:ui.Prefix('Scope_'))
        $bodyBoxes = @($scopeBoxes | Where-Object { $script:ui.Name($_).Contains('REPORT BODY BEFORE') })
        Assert-True ($bodyBoxes.Count -eq 1) 'Reopen did not retain the same explicit synthetic body scope.'
        $bodyId = $script:ui.Id($bodyBoxes[0])
        foreach ($box in $scopeBoxes) { Set-Check ($script:ui.Id($box)) ($script:ui.Id($box) -eq $bodyId) }
        Set-Text 'Instruction' $firstInstruction
        # A new report controller may require explicit existing-profile connection; never assume readiness.
        $state = Read-State
        if ($state.model.status -ne 'ready') {
            Invoke-Once 'ConnectModel'
            [void](Wait-Observed { $s = Read-State; $null -ne $s -and $s.model.status -eq 'ready' -and !$s.model.operation_active } 'explicit reopened profile ready')
            Wait-Ready
        }
        Counters 2
        Assert-NoConsent
        Set-Check 'DraftConsent' $true
        Invoke-Once 'StartDraft'
        Wait-Ready
        # Selection of an existing result is a UI outcome, not a fabricated provider response.
        [void](Wait-Observed { $null -ne $script:ui.Find('SelectedArtifactTitle') -and $null -ne $script:ui.Find(('Version_' + $first.id)) } 'requested existing result selected in native results')
        Assert-Selected $first
        $state = Observe-State
        Assert-NoError $state
        Assert-True (!$state.active -and @($state.versions).Count -eq 3 -and @($state.requests).Count -eq 2 -and (Permission-Count $project) -eq 2) 'Identical original request created another task/version instead of resolving existing output.'
        Counters 2
        Select-Tab 2
        Assert-Selected $first
        Invoke-Once 'RefreshState'
        Wait-Ready
        Assert-Selected $first
        Counters 2
        Capture-Phase '15-existing-result-selected-no-provider-resend'
        Check-Preserved
        Record-State $state 'final-idle-before-owner-shutdown'

        $script:phase = 'normal-owner-idle-shutdown'
        Check-Owner
        Record 'normal-close' 'HwpNativeWorkspace' @{ replay = $false; kill = $false; expected = 'owner-idle-shutdown' }
        $close = $script:ui.Close()
        [void]$close.Finish(10000)
        [void](Wait-Observed { !$script:ui.MainExists } 'normal owned native window close')
        # The parent independently checks actual process exit and owned_server_stopped.
        $finalReceipt = Read-HostReceipt
        Assert-True ([int]$finalReceipt.provider_calls -eq 2) 'Unexpected provider send during normal shutdown.'
        Record 'normal-close-observed' 'HOST.json' @{ window_absent = $true; host_status = $finalReceipt.status
            provider_calls = $finalReceipt.provider_calls; profile_calls = $finalReceipt.profile_calls
            owned_server_stopped = $(if ($null -ne $finalReceipt.PSObject.Properties['owned_server_stopped']) { $finalReceipt.owned_server_stopped } else { $null }) }
        $script:evidence.status = 'journey-complete-parent-shutdown-check-required'
    }
    $script:evidence.completed_utc = [DateTime]::UtcNow.ToString('o')
    Save-Evidence
    Write-Output (Join-Path $Output 'evidence.json')
}
catch {
    $script:evidence.status = 'failed-preserved-no-replay'
    $script:evidence.failed_phase = $script:phase
    # Driver diagnostics only; do not dump arbitrary application/technical/authority JSON or receipt internals.
    $message = [string]$_.Exception.Message
    $message = [regex]::Replace($message, '(?i)(token|nonce|authorization|credential)[^\r\n]*', '[sensitive detail omitted]')
    $script:evidence.failure = @{ type = $_.Exception.GetType().FullName; message = $message }
    $script:evidence.preservation = 'No new UI action, capture, retry, rollback, cleanup, kill, or file mutation after first failure. Already-dispatched native actions may still settle.'
    Save-Evidence
    throw "Native UI proof failed in phase '$script:phase'. Evidence/UI/process/files preserved: $(Join-Path $Output 'evidence.json'). $message"
}
