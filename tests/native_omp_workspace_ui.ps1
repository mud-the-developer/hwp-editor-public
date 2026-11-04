#requires -Version 5.1
<#
Opt-in driver for a NEW unsigned actual-OMP fixture only. Workspace/Layout
opens one fresh library project with zero model sends. Open prepares context;
Core/Journey continue that successfully opened scope once. Physical input is
explicit; accessibility layout inspection never requires foreground or keys.
Real UI consent/compiler; inert provider/OS. No HTTP, hand-written authority,
real provider/Hancom, process launch/kill, fallback or stopped-scope replay.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $HostReceipt,
    [Parameter(Mandatory = $true)][string] $Output,
    [ValidateSet('Workspace', 'Layout', 'Open', 'Core', 'Journey', 'OmpIntegration', 'WorkspaceFolder', 'WorkspaceChat')][string] $Scenario = 'Open',
    [ValidateSet('Accessibility', 'Physical')][string] $InputMode = 'Physical'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -ne 'Desktop' -or [Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Installed x64 Windows PowerShell 5.1 is required.' }
$references = @('System', 'System.Core', 'System.Xml', 'System.Drawing', 'System.IO.Compression', 'System.IO.Compression.FileSystem', 'UIAutomationClient', 'UIAutomationTypes', 'WindowsBase') | ForEach-Object {
    $assembly = [Reflection.Assembly]::LoadWithPartialName($_)
    if ($null -eq $assembly) { throw "Missing installed framework assembly: $_" }
    $assembly.Location
}
Add-Type -Path (Join-Path $PSScriptRoot 'native_workspace_uia.cs') -ReferencedAssemblies $references
$HostReceipt = [HwpNativeProof.SyntheticFiles]::FullPath($HostReceipt)
$Output = [HwpNativeProof.SyntheticFiles]::FullPath($Output)
if ([IO.Path]::GetFileName($HostReceipt) -cne 'HOST.json' -or (Test-Path -LiteralPath $Output)) { throw 'Existing HOST.json and NEW output directory are required.' }
$fixture = [IO.Path]::GetDirectoryName($HostReceipt)
if ([HwpNativeProof.SyntheticFiles]::Under($HostReceipt, $Output) -or $Output -eq $fixture) { throw 'Output must not contain or replace its fixture.' }
$script:ownerPid = 0
function ReadReceiptJson([string]$path) {
    $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)
    try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
}
function Receipt {
    $index = ReadReceiptJson $HostReceipt
    if ($index.contract -cne 'hwp-native-omp-host-index-v1' -or $index.records -cne 'HOST-records' -or
        @($index.PSObject.Properties).Count -ne 2) { throw 'Expected the immutable owned OMP status index; no legacy fallback.' }
    $records = [HwpNativeProof.SyntheticFiles]::FullPath((Join-Path $fixture 'HOST-records'))
    $published = @(Get-ChildItem -LiteralPath $records -Filter '*.ready' -Directory | Sort-Object Name)
    if ($published.Count -eq 0) { throw 'No complete owned OMP status is published.' }
    $marker = $published[-1]
    [void][HwpNativeProof.SyntheticFiles]::FullPath($marker.FullName)
    if ($marker.Name -cnotmatch '^[0-9]{8}\.ready$') { throw 'Invalid owned status generation.' }
    $stem = $marker.Name.Substring(0, 8)
    $snapshot = [HwpNativeProof.SyntheticFiles]::FullPath((Join-Path $records ($stem + '.json')))
    $r = ReadReceiptJson $snapshot
    if ([long]$r.receipt_generation -ne [long]$stem) { throw 'Owned status generation and publication marker differ.' }
    if ($r.contract -cne 'hwp-native-omp-gui-host-v1' -or $r.actual_omp_required -ne $true -or $r.model_boundary_mocked -ne $true -or
        $r.os_handoff_mocked -ne $true -or $r.automatic_permission_or_acceptance -ne $false -or $r.provider_external -ne $false -or
        $r.status -eq 'failed_preserved' -or [int]$r.sdk_calls -ne 0) { throw 'Not a healthy exact actual-OMP unsigned fixture.' }
    if ([int]$r.ui_pid -le 0 -or ($script:ownerPid -ne 0 -and [int]$r.ui_pid -ne $script:ownerPid)) { throw 'Owned UI PID changed.' }
    foreach ($p in @($r.source, $r.material, $r.base)) { if (![HwpNativeProof.SyntheticFiles]::Under([string]$p, $fixture)) { throw 'Fixture path escaped.' } }
    return $r
}
$r = Receipt
$script:ownerPid = [int]$r.ui_pid
$source = [HwpNativeProof.SyntheticFiles]::FullPath([string]$r.source)
$base = [HwpNativeProof.SyntheticFiles]::FullPath([string]$r.base)
if ([HwpNativeProof.SyntheticFiles]::Under($Output, $base) -or $Output -eq $base) { throw 'Evidence must remain outside projects.' }
[HwpNativeProof.SyntheticFiles]::CreateNewDirectory($Output)
$utf8 = New-Object Text.UTF8Encoding($false)
$ui = New-Object -TypeName HwpNativeProof.Workspace -ArgumentList $script:ownerPid
$events = New-Object 'System.Collections.Generic.List[object]'
$evidence = [ordered]@{ contract = 'hwp-native-omp-ui-proof-v1'; scenario = $Scenario; input_mode = $InputMode; status = 'running'; ui_pid = $script:ownerPid
    host_receipt = $HostReceipt; source_sha256 = [HwpNativeProof.SyntheticFiles]::Sha256($source)
    driver_sha256 = [HwpNativeProof.SyntheticFiles]::Sha256($PSCommandPath); events = @()
    helper_sha256 = [HwpNativeProof.SyntheticFiles]::Sha256((Join-Path $PSScriptRoot 'native_workspace_uia.cs'))
    native_dll_sha256 = [string]$r.native_dll_sha256
    limits = 'Actual owned OMP and production UI/compiler; inert local model/OS. Source opt-in proof only; no real model, Hancom, physical input or layout qualification unless explicitly observed. Known accessibility Save filename commit remains unverified; do not replay stopped scopes.' }
function Save { $evidence.events = @($events.ToArray()); [IO.File]::WriteAllText((Join-Path $Output 'evidence.json'), ($evidence | ConvertTo-Json -Depth 12), $utf8) }
function Record([string]$kind, $data) { $events.Add([ordered]@{ utc = [DateTime]::UtcNow.ToString('o'); kind = $kind; data = $data }); Save }
function Assert([bool]$condition, [string]$message) { if (!$condition) { throw $message } }
function Wait([scriptblock]$observe, [string]$description) {
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        [void](Receipt)
        $failure = $ui.Find('BlockingError')
        if ($null -ne $failure -and $ui.Visible('BlockingError') -and $ui.Read($failure).Length -gt 0) {
            throw ('Native GUI error: ' + $ui.Read($failure))
        }
        $value = & $observe
        if ($value) { return $value }
        Start-Sleep -Milliseconds 120
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Observation timed out: $description. Scope preserved without replay or termination."
}
$documentActions = @('OpenReviewCopy', 'ImportOpenedCopy', 'ImportExternal', 'AcceptVersion', 'ExportArtifact')
function MenuFor([string]$id) {
    if ($id -in $documentActions) { return 'DocumentActionsButton' }
    if ($id -in @('OpenReport', 'RefreshState', 'SetupRequirements', 'LegalNotice', 'LegacyTools')) { return 'ToolsMenuButton' }
    return $null
}
function Enabled([string]$id) {
    [void](Receipt)
    $menu = MenuFor $id
    if ($menu) {
        $header = $ui.Find($menu)
        if ($null -eq $header -or !$ui.Enabled($header)) { return $false }
        $ui.SetMenuExpanded($menu, $true)
        try { return $ui.Enabled($ui.MenuAction($menu, $id)) }
        finally { $ui.SetMenuExpanded($menu, $false) }
    }
    $element = $ui.Find($id); return $null -ne $element -and $ui.Enabled($element)
}
function Action([string]$id) {
    Assert (!$ui.InspectorVisible()) 'Close the inspector before ordinary document/model actions.'
    $menu = MenuFor $id
    if ($menu) {
        $ui.SetMenuExpanded($menu, $true)
        return $ui.MenuAction($menu, $id)
    }
    Assert ($ui.Visible($id)) ('Ordinary action is not currently visible: ' + $id)
    return $ui.RequireRole($id, [Windows.Automation.ControlType]::Button)
}
function Invoke([string]$id) {
    [void](Receipt); $element = Action $id
    Assert ($ui.Enabled($element)) "Disabled action: $id"
    Record 'invoke-once' @{ id = $id; handler_completion = 'Observed separately; Invoked is dispatch, not async completion.' }
    [void]$ui.InvokeObserved($element).Finish(15000)
    $menu = MenuFor $id
    if ($menu -and (Enabled $menu)) { $ui.SetMenuExpanded($menu, $false) }
}
function SelectDocument([string]$id) {
    [void](Receipt); Assert (!$ui.InspectorVisible()) 'Close inspector before document tree selection.'
    Record 'tree-select-once' @{ id = $id; pattern = 'SelectionItemPattern'; reimport = $false }
    [void]$ui.SelectDocument($id).Finish(15000)
    if (!$id.StartsWith('LibraryOpen_', [StringComparison]::Ordinal) -and !$id.StartsWith('WorkspaceDocument_', [StringComparison]::Ordinal)) {
        [void](Wait { $ui.TreeSelected($id) } 'exact immutable document tree selection observed')
    }
}
function OpenContext {
    [void](Receipt); $ui.OpenInspector(0)
    Record 'explicit-context-inspector' @{ tab = 0; model_send = $false }
}
function CloseContext {
    [void](Receipt); $ui.CloseInspector()
    Assert (!$ui.InspectorVisible()) 'Context inspector did not close.'
}
function Text([string]$id, [string]$value) { [void](Receipt); Record 'set-public-text' @{ id = $id; characters = $value.Length }; $ui.SetValue($ui.Require($id), $value) }
function Check([string]$id, [bool]$value) { [void](Receipt); $item = $ui.Require($id); if (($ui.ToggleState($item) -eq 1) -ne $value) { Record 'checkbox' @{ id = $id; value = $value }; $ui.Toggle($item) }; Assert (($ui.ToggleState($item) -eq 1) -eq $value) 'Checkbox transition failed.' }
function Capture([string]$name) { [void](Receipt); $result = $ui.Capture((Join-Path $Output ($name + '.png')), $ui.Root()).Finish(10000); Record 'owned-window-capture' @{ path = $result.Path; width = $result.Width; height = $result.Height; printwindow = $result.PrintWindowSucceeded; visual_assessment = 'Parent must inspect pixels; no automatic visual PASS.' } }
function CaptureConversation([string]$name) {
    Authoring
    Record 'present-document-and-conversation' @{ authority_or_selection_changed = $false; physical_keyboard = $false }
    Capture $name
}
function State {
    [void](Receipt); $text = $ui.ReportStateJson()
    if (!$text) { return $null }
    $state = $text | ConvertFrom-Json
    if ($null -eq $state.PSObject.Properties['versions'] -or $null -eq $state.PSObject.Properties['active']) { throw 'Unexpected report-state schema.' }
    return $state
}
function Authoring {
    [void](Receipt); CloseContext
    if (!$ui.Visible('OmpConversation')) { Invoke 'ShowOmpConversation' }
    [void](Wait { $ui.Visible('OmpConversation') -and $ui.Visible('Instruction') } 'same session conversation visible')
}
function Terminal {
    [void](Receipt); CloseContext
    if (!$ui.Visible('ShutdownOmp')) { Invoke 'FocusOmpTerminal' }
    [void](Wait { $ui.Visible('ShutdownOmp') -and (Enabled 'ShutdownOmp') } 'same actual session terminal header and observed idle')
}
function ObserveState { return (Wait { State } 'current public production report state through temporary inspector') }
function Rendered([string]$label) {
    [void](Wait {
        $item = $ui.Find('DocumentRenderStatus')
        if ($null -eq $item) { return $false }
        $text = $ui.Read($item)
        return $text.StartsWith($label, [StringComparison]::Ordinal) -and $text -match '[1-9][0-9]*/[1-9][0-9]*'
    } 'actual selected HWPX first page rendered')
    Record 'actual-current-hwpx-page' @{ label = $label; status = $ui.Read($ui.Require('DocumentRenderStatus')); native_layout_qualified = $false }
}
function Box($element) { $ui.Verify($element); $b = $element.Current.BoundingRectangle; return @($b.X, $b.Y, $b.Width, $b.Height) }
function Layout {
    return [ordered]@{ window = (Box ($ui.Root())); tree = (Box ($ui.RequireRole('DocumentTree', [Windows.Automation.ControlType]::Tree)))
        document = (Box ($ui.Require('HwpxDocumentPreview'))); render_status = (Box ($ui.Require('DocumentRenderStatus')))
        conversation = (Box ($ui.Require('OmpConversation'))); instruction = (Box ($ui.Require('Instruction')))
        add = (Box ($ui.Require('NewReport'))); context = (Box ($ui.Require('ContextOptions')))
        document_actions = (Box ($ui.Require('DocumentActionsButton'))); tools = (Box ($ui.Require('ToolsMenuButton'))) }
}
function SameLayout($before) {
    Assert (($before | ConvertTo-Json -Compress) -ceq ((Layout) | ConvertTo-Json -Compress)) 'Secondary inspector/menu moved or resized the three-pane workspace.'
}
function DefaultControls {
    Assert (!$ui.InspectorVisible()) 'Inspector must be hidden by default.'
    foreach ($id in @('InspectorTabs', 'CloseContextOptions', 'RequestBase', 'AddMaterial', 'PreviewRequest', 'ReportTechnicalState', 'OmpProposalText')) {
        Assert (!$ui.Visible($id)) ('Secondary control leaked into the default workspace: ' + $id)
    }
    foreach ($item in @($ui.Prefix('Scope_')) + @($ui.Prefix('MaterialOwn_'))) {
        Assert (!$ui.Visible($ui.Id($item))) 'Scope/material choices leaked into the default workspace.'
    }
    $actions = @($ui.VisibleWorkspaceActions())
    Assert ($actions.Count -le 12) 'Default workspace exposes more than twelve app action controls.'
    $scale = $ui.DpiScale()
    $rows = @($actions | ForEach-Object {
        $box = Box $_
        Assert ($box[3] -le (44 * $scale + 1)) ('Oversized default action: ' + $ui.Id($_))
        [ordered]@{ id = $ui.Id($_); role = $_.Current.ControlType.ProgrammaticName; box = $box; enabled = $ui.Enabled($_) }
    })
    return $rows
}
function ThreePanes($layout) {
    $scale = $ui.DpiScale()
    foreach ($name in @('tree', 'document', 'conversation', 'instruction')) {
        $box = $layout[$name]
        Assert ($box[2] -gt 0 -and $box[3] -gt 0) ('Missing visible workspace area: ' + $name)
        Assert ($box[0] -ge $layout.window[0] -and $box[1] -ge $layout.window[1] -and
            $box[0] + $box[2] -le $layout.window[0] + $layout.window[2] + 1 -and
            $box[1] + $box[3] -le $layout.window[1] + $layout.window[3] + 1) ('Pane clipped outside owned window: ' + $name)
    }
    Assert ($layout.tree[0] + $layout.tree[2] -le $layout.document[0] + 2 * $scale) 'Document tree is not far left of rendered HWPX.'
    Assert ($layout.document[0] + $layout.document[2] -le $layout.conversation[0] + 2 * $scale) 'Rendered HWPX is not between tree and conversation.'
    Assert ($layout.instruction[0] -ge $layout.conversation[0] - 2 * $scale -and
        $layout.instruction[0] + $layout.instruction[2] -le $layout.conversation[0] + $layout.conversation[2] + 2 * $scale) 'Composer is not retained in the right conversation pane.'
}
function InspectorRoundTrip($baseline, [string]$project, [string]$original) {
    $stateBefore = (ObserveState | ConvertTo-Json -Depth 30 -Compress)
    $actionsBefore = (DefaultControls | ConvertTo-Json -Depth 8 -Compress)
    $renderBefore = $ui.Read($ui.Require('DocumentRenderStatus'))
    $callsBefore = [int](Receipt).provider_calls
    OpenContext
    Assert ($ui.InspectorVisible()) 'Explicit context inspector did not open.'
    Capture '01-explicit-context-inspector'
    CloseContext
    $stateAfter = (ObserveState | ConvertTo-Json -Depth 30 -Compress)
    Assert ($stateBefore -ceq $stateAfter) 'Inspector navigation changed document state.'
    Assert ($actionsBefore -ceq (DefaultControls | ConvertTo-Json -Depth 8 -Compress)) 'Inspector navigation changed action availability or bounds.'
    Assert ($renderBefore -ceq $ui.Read($ui.Require('DocumentRenderStatus')) -and $ui.TreeSelected('OriginalDocument')) 'Inspector navigation changed current original document.'
    Assert ([string](Receipt).project -ceq $project -and [int](Receipt).provider_calls -eq $callsBefore) 'Inspector navigation changed project or model calls.'
    SameLayout $baseline; Rendered $original
    Record 'inspector-roundtrip-no-mutation' @{ tabs_observed = @(0, 2); state_unchanged = $true; actions_unchanged = $true; model_calls = $callsBefore; default_hidden = $true }
}
function OpenTools {
    [void](Receipt); Record 'native-menu-expand' @{ id = 'ToolsMenuButton'; replay = $false }
    $ui.SetMenuExpanded('ToolsMenuButton', $true)
    return (Wait { $ui.ToolsMenu() } 'exact owned tools popover')
}
function MenuRow($menu, [string]$id) {
    $item = $ui.MenuAction('ToolsMenuButton', $id)
    Assert ($null -ne $item) ('Missing actual tools action: ' + $id)
    return $item
}
function CaptureTools([string]$name, $menu) {
    $result = $ui.Capture((Join-Path $Output ($name + '.png')), $menu).Finish(10000)
    Record 'owned-popup-capture' @{ path = $result.Path; width = $result.Width; height = $result.Height
        printwindow = $result.PrintWindowSucceeded; visual_assessment = 'Inspect actual pixels separately; no automatic visual PASS.' }
}
function NoAuthority {
    $permissions = @(Get-ChildItem -LiteralPath $base -Filter 'draft-permission.json' -Recurse -File -ErrorAction SilentlyContinue)
    Assert ($permissions.Count -eq 0) 'Model request/proposal created document authority before UI consent.'
    $state = ObserveState
    Assert (@($state.versions).Count -eq 0 -and !$state.active) 'Model response created a document before consent.'
}
function Dialog([string]$action, [string]$path) {
    [void](Receipt); $path = [HwpNativeProof.SyntheticFiles]::FullPath($path)
    if ($action -eq 'NewReport') { Assert ($path -eq $source) 'Source mismatch.' }
    elseif ($action -eq 'AddMaterial') { Assert ($path -eq [string](Receipt).material) 'Material mismatch.' }
    elseif ($action -eq 'OpenReport') { Assert ([HwpNativeProof.SyntheticFiles]::Under($path, $base)) 'Project escaped.' }
    elseif ($action -eq 'ExportArtifact') {
        Assert ([HwpNativeProof.SyntheticFiles]::Under($path, $Output) -and !(Test-Path -LiteralPath $path)) 'Export requires a new owned evidence path.'
    }
    else { throw 'Unknown fixed dialog action.' }
    Assert (!$ui.InspectorVisible() -or $action -eq 'AddMaterial') 'Close inspector before ordinary document dialogs.'
    Record 'owned-native-dialog' @{ action = $action; path = $path; filename_commit = 'Unverified until exact chosen output exists and hashes match; no setter fallback or retry.' }
    $pending = $null
    if ($InputMode -eq 'Physical' -and $action -eq 'NewReport') { [void](Action $action); $ui.Shortcut($action) }
    else {
        $trigger = if ($action -eq 'AddMaterial') { $ui.RequireRole($action, [Windows.Automation.ControlType]::Button) } else { Action $action }
        Assert ($ui.Enabled($trigger)) ('Disabled dialog action: ' + $action)
        $pending = $ui.Invoke($trigger)
    }
    $modal = Wait { $ui.Modal() } 'exact owned common dialog'
    Record 'native-dialog-input' @{ mode = $InputMode; physical_keyboard = ($InputMode -eq 'Physical') }
    if ($InputMode -eq 'Physical') { $ui.TypeDialogPath($modal, $path) }
    else { $ui.SetDialogValue($modal, $path) }
    [void]$ui.DialogConfirm($modal).Finish(10000)
    if ($null -ne $pending) { [void]$pending.Finish(10000) }
    [void](Wait { $null -eq $ui.Modal() } 'owned common dialog closed')
    $ready = switch ($action) {
        'AddMaterial' { 'AddMaterial' }
        'ExportArtifact' { 'ExportArtifact' }
        default { 'OpenOmpTerminal' }
    }
    [void](Wait { Enabled $ready } 'native dialog action and final GUI state refresh settled')
}
function Release([int]$call) {
    $r = Receipt
    Assert ([int]$r.provider_calls -eq $call -and [int]$r.provider_waiting -eq $call) 'Exact once-only local response wait required.'
    $path = Join-Path $fixture ('PROVIDER-RELEASE-' + $call + '.json')
    $body = @{ contract = 'hwp-native-synthetic-provider-release-v1'; call = $call } | ConvertTo-Json -Compress
    $pendingPath = $path + '.pending'
    $stream = [IO.File]::Open($pendingPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $bytes = $utf8.GetBytes($body); $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    [IO.File]::Move($pendingPath, $path)
    Record 'release-inert-provider-response' @{ call = $call; document_authority = $false }
}
function WaitProposal([int]$call) {
    [void](Wait { [int](Receipt).provider_waiting -eq $call } 'explicit actual OMP request reached local synthetic provider')
    Assert (!(Enabled 'NewReport') -and !(Enabled 'StartDraft')) 'Busy request did not block document creation/switch.'
    Release $call
    [void](Wait { Enabled 'StartDraft' } 'matching final response and actual idle automatically inspected')
    Authoring
    Assert ($ui.ReadInspectorText('OmpProposalText').Contains('hwp-report-proposal-v1')) 'No inspected production proposal text.'
    Record 'proposal-inspected-awaiting-explicit-create' @{ call = $call }
}
function SettledVersions([int]$count) {
    # Publishing an authenticated version precedes the worker's return. A row
    # alone is not completion; keep the same bounded observation until idle.
    $s = Wait {
        $current = State
        if ($null -eq $current) { return $null }
        if ($current.error) { throw ('Report generation failed: ' + [string]$current.error) }
        Assert (@($current.versions).Count -le $count) 'Generation produced unexpected extra versions.'
        if (!$current.active -and $current.gate_passed -and @($current.versions).Count -eq $count -and
            (Enabled 'OpenReviewCopy')) { return $current }
        return $null
    } 'authenticated immutable result/history and settled worker'
    Assert (!$s.active -and !$s.error -and @($s.versions).Count -eq $count) 'Report generation did not settle.'
    Rendered ([string](@($s.versions)[-1].label))
    foreach ($version in @($s.versions)) {
        Assert ($ui.Enabled($ui.TreeItem('Version_' + $version.id))) 'Settled immutable document tree entry is not available.'
    }
    return $s
}
function Generate([int]$count) {
    Invoke 'StartDraft'
    Authoring
    return (SettledVersions $count)
}
function Artifact($version, [string]$project) {
    $directory = Join-Path (Join-Path $project 'versions') ([string]$version.id)
    $path = [HwpNativeProof.SyntheticFiles]::FullPath((Join-Path $directory ([string]$version.filename)))
    Assert ([HwpNativeProof.SyntheticFiles]::Under($path, $project)) 'Canonical escaped project.'
    Assert ([HwpNativeProof.SyntheticFiles]::Sha256($path) -ceq [string]$version.sha256) 'Canonical bytes differ from selected artifact.'
    $metadata = [IO.File]::ReadAllText((Join-Path $directory 'version.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
    Assert ($metadata.version_id -ceq $version.id -and $metadata.sha256 -ceq $version.sha256) 'Version metadata differs from selected exact artifact.'
    Record 'canonical-artifact' @{ id = $version.id; sha256 = $version.sha256; path = $path; previous_version = $metadata.previous_version }
    return [pscustomobject]@{ path = $path; metadata = $metadata }
}
try {
    Save
    if ($Scenario -in @('Workspace', 'Layout')) {
        Assert ($r.status -eq 'ready' -and [int]$r.provider_calls -eq 0 -and $null -eq $r.PSObject.Properties['project']) 'Workspace requires a fresh never-opened, never-sent unsigned fixture.'
        Assert ([HwpNativeProof.SyntheticFiles]::Under([string]$r.prepared_project, $base)) 'Exact fresh metadata-only library project required.'
        $project = [HwpNativeProof.SyntheticFiles]::FullPath([string]$r.prepared_project)
        $projectsBefore = @(Get-ChildItem -LiteralPath $base -Directory | ForEach-Object { $_.FullName } | Sort-Object)
        Assert (!$ui.InspectorVisible()) 'Inspector was open before the first library selection.'
        [void](Wait { $null -ne $ui.Find('LibraryOpen_0') } 'fresh actual document tree library item')
        Assert (@($ui.Prefix('LibraryOpen_')).Count -eq 1) 'Fresh fixture must expose exactly one actual library project.'
        SelectDocument 'LibraryOpen_0'
        [void](Wait {
            $current = Receipt
            $opened = $current.PSObject.Properties['project']
            return $null -ne $opened -and [string]$opened.Value -ceq $project -and (Enabled 'OpenOmpTerminal')
        } 'exact library project intake completed once')
        $ui.ExpandTreeNode('LibraryOpen_0')
        SelectDocument 'OriginalDocument'
        $original = -join [char[]]@(0xC6D0, 0xBCF8)
        Rendered $original
        $baseline = Layout; ThreePanes $baseline
        $controls = DefaultControls
        Record 'three-pane-default-workspace' @{ bounds = $baseline; controls = $controls; physical_keyboard = $false; foreground_required = $false }
        Capture '00-normal-workspace'
        InspectorRoundTrip $baseline $project $original
        if ($Scenario -eq 'Layout') {
            $menu = OpenTools; SameLayout $baseline
            $rows = @('OpenReport', 'RefreshState', 'SetupRequirements', 'LegalNotice', 'LegacyTools') | ForEach-Object {
                $item = MenuRow $menu $_
                [ordered]@{ id = $_; name = $ui.Name($item); help = $ui.Help($item); enabled = $ui.Enabled($item); box = (Box $item) }
            }
            Record 'popover-layout-stable' @{ before = $baseline; after = (Layout); menu_header = (Box $menu); rows = $rows }
            CaptureTools '02-tools-popover' $menu
            $ui.SetMenuExpanded('ToolsMenuButton', $false)
            [void](Wait { $null -eq $ui.ToolsMenu() } 'standard menu collapsed without focus or physical input')
            SameLayout $baseline
            Record 'menu-keyboard-not-exercised' @{ reason = 'Layout inspection intentionally uses UIA expansion only; known focus refusal is not bypassed or called keyboard proof.' }
            $beforeRefresh = (ObserveState | ConvertTo-Json -Depth 30 -Compress)
            Invoke 'RefreshState'
            [void](Wait { (Enabled 'NewReport') -and $null -eq $ui.ToolsMenu() } 'read-only menu refresh and GUI idle settled')
            Assert ($beforeRefresh -ceq (ObserveState | ConvertTo-Json -Depth 30 -Compress)) 'Read-only refresh changed report state.'
            Rendered $original; SameLayout $baseline
            Record 'menu-readonly-refresh' @{ model_calls = [int](Receipt).provider_calls; invocation = 'Actual owned MenuItem InvokePattern, no key/focus claim.' }
        } else {
            Record 'popup-and-menu-keys-not-exercised' @{ reason = 'Workspace is the separate tree/render/inspector/compact/resize smoke. Layout retains full popup inspection; known inactive-desktop menu focus is not retried or bypassed.' }
        }
        $pattern = $null
        if ($ui.Root().TryGetCurrentPattern([Windows.Automation.TransformPattern]::Pattern, [ref]$pattern) -and $pattern.Current.CanResize) {
            $scale = $ui.DpiScale(); $width = [Math]::Min($baseline.window[2], 1040 * $scale)
            $height = [Math]::Min($baseline.window[3], 760 * $scale)
            if ($width -lt $baseline.window[2] -or $height -lt $baseline.window[3]) {
                $pattern.Resize($width, $height)
                [void](Wait { $b = Box ($ui.Root()); [Math]::Abs($b[2] - $width) -le 2 -and [Math]::Abs($b[3] - $height) -le 2 } 'single narrow resize settled')
                $narrow = Layout; ThreePanes $narrow; $narrowControls = DefaultControls
                Rendered $original; Capture '03-narrow-workspace'
                Record 'narrow-three-pane-workspace' @{ bounds = $narrow; controls = $narrowControls; physical_input = $false }
            } else { Record 'narrow-resize-not-exercised' @{ reason = 'Initial owned window is already at or below narrow target.' } }
        } else { Record 'narrow-resize-not-exercised' @{ reason = 'Exact owned window does not expose a resizable TransformPattern.' } }
        Assert ($ui.TreeSelected('OriginalDocument') -and [string](Receipt).project -ceq $project) 'Workspace navigation lost the current tree document.'
        Assert (($projectsBefore | ConvertTo-Json -Compress) -ceq (@(Get-ChildItem -LiteralPath $base -Directory | ForEach-Object { $_.FullName } | Sort-Object) | ConvertTo-Json -Compress)) 'Workspace inspection re-imported or created a project.'
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256($source) -ceq $evidence.source_sha256) 'Original source changed during layout inspection.'
        NoAuthority
        Assert ([int](Receipt).provider_calls -eq 0 -and $null -eq (Receipt).PSObject.Properties['omp_launch_id']) 'Workspace launched OMP or sent a model request.'
        Record 'single-library-intake-retained-original' @{ project = $project; original_sha256 = $evidence.source_sha256; reimport = $false; model_calls = 0 }
        [void]$ui.Close().Finish(10000)
        [void](Wait { !$ui.MainExists } 'normal zero-model native window close')
        $evidence.status = 'workspace-layout-tree-inspector-zero-model-complete-parent-exit-check-required'
        $evidence.limits = 'Actual original HWPX render, tree selection, three-pane bounds/compact app controls and public inspector roundtrip. Popup only in Layout; resize only when recorded. No physical keyboard/foreground/menu-key proof, OMP/model/draft/Hancom/other-PC qualification; parent must check host/process exit and inspect pixels.'
    } elseif ($Scenario -eq 'WorkspaceChat') {
        Assert ($r.status -eq 'ready' -and $r.workspace_chat -eq $true -and [int]$r.provider_calls -eq 0) 'New never-driven workspace chat fixture required.'
        $workFolder = [string]$r.work_folder
        $secondFolder = [string]$r.second_workspace
        $pending = $ui.Invoke((Action 'OpenWorkspace'))
        $modal = Wait { $ui.Modal() } 'initial selected workspace picker'
        [void]$ui.DialogConfirm($modal).Finish(10000)
        [void]$pending.Finish(10000)
        [void](Wait {
            $current = Receipt
            $selected = $current.PSObject.Properties['selected_workspace']
            return $null -ne $selected -and [string]::Equals([string]$selected.Value, $workFolder, [StringComparison]::OrdinalIgnoreCase) -and (Enabled 'StartOmpLogin')
        } 'selected workspace admitted without model calls')
        Invoke 'StartOmpLogin'
        [void](Wait { Enabled 'ContinueOmpSetup' } 'actual OMP with only inert model configured')
        $firstLaunch = [string](Receipt).omp_launch_id
        Assert ([string]::Equals([string](Receipt).omp_working_directory, $workFolder, [StringComparison]::OrdinalIgnoreCase)) 'Actual OMP cwd differs from GUI work folder.'
        Invoke 'ContinueOmpSetup'
        Text 'Instruction' 'TOY ONLY: inspect current workspace; run harmless shell and create two synthetic notes'
        [void](Wait { Enabled 'SendOmpRequest' } 'ordinary conversation enabled before any report')
        Invoke 'SendOmpRequest'
        foreach ($call in @(1, 2)) {
            [void](Wait { [int](Receipt).provider_waiting -eq $call } 'actual builtin shell/read/write roundtrip')
            Release $call
        }
        [void](Wait { (Enabled 'Instruction') -and $ui.Read($ui.Require('OmpLiveResponse')).Contains('SYNTHETIC CHAT A') } 'actual native shell/read/write and natural GUI answer')
        Assert (!(Enabled 'StartDraft') -and !$ui.Visible('RequestReportChanges')) 'Ordinary chat acquired document authority.'
        Assert ((Receipt).native_shell_verified -eq $true -and @((Receipt).shell_actions).Count -eq 1) 'Actual native tool results were not verified.'
        Assert (Test-Path -LiteralPath (Join-Path $workFolder 'HWPReview.md') -PathType Leaf) 'Project Markdown guidance was not created.'
        CaptureConversation '01-no-document-native-shell-and-files'
        $pending = $ui.Invoke((Action 'NewBlankHwpx'))
        $modal = Wait { $ui.Modal() } 'first blank default-path save'
        [void]$ui.DialogConfirm($modal).Finish(10000)
        [void]$pending.Finish(10000)
        [void](Wait { $null -ne (Receipt).PSObject.Properties['blank_project'] -and (Enabled 'Instruction') } 'first document intake kept live workspace session')
        Assert ([string](Receipt).omp_launch_id -ceq $firstLaunch) 'First document unnecessarily restarted OMP.'
        $firstBlankHash = [HwpNativeProof.SyntheticFiles]::Sha256([string](Receipt).blank_destination)
        OpenContext
        Dialog 'AddMaterial' ([string](Receipt).material)
        CloseContext
        Text 'Instruction' 'TOY ONLY: selected reference'
        Invoke 'SendOmpRequest'
        [void](Wait { [int](Receipt).provider_waiting -eq 3 } 'explicit captured source reached ordinary chat')
        Release 3
        [void](Wait { (Enabled 'Instruction') -and $ui.Read($ui.Require('OmpLiveResponse')).Contains('SYNTHETIC SELECTED') } 'ordinary same-session followup using selected source')
        Assert ((Receipt).selected_chat_material_verified -eq $true -and !(Enabled 'StartDraft')) 'Selected source chat became a document proposal.'
        CaptureConversation '02-selected-source-natural-chat'
        $pending = $ui.Invoke((Action 'OpenWorkspace'))
        $modal = Wait { $ui.Modal() } 'explicit second workspace picker'
        $ui.SetDialogValue($modal, $secondFolder)
        [void]$ui.DialogConfirm($modal).Finish(10000)
        [void]$pending.Finish(10000)
        [void](Wait {
            $current = Receipt
            return [string]::Equals([string]$current.selected_workspace, $secondFolder, [StringComparison]::OrdinalIgnoreCase) -and [string]$current.omp_launch_id -cne $firstLaunch -and (Enabled 'ContinueOmpSetup')
        } 'old OMP exited normally and new OMP owns selected workspace')
        Assert ([string]::Equals([string](Receipt).omp_working_directory, $secondFolder, [StringComparison]::OrdinalIgnoreCase) -and @((Receipt).omp_launches).Count -eq 2) 'New OMP retained the old cwd or overlapped launches.'
        Invoke 'ContinueOmpSetup'
        Text 'Instruction' 'TOY ONLY: inspect switched workspace; run harmless shell and create two synthetic notes'
        Invoke 'SendOmpRequest'
        foreach ($call in @(4, 5)) {
            [void](Wait { [int](Receipt).provider_waiting -eq $call } 'switched-workspace actual native tools and answer')
            Release $call
        }
        [void](Wait { (Enabled 'Instruction') -and $ui.Read($ui.Require('OmpLiveResponse')).Contains('SYNTHETIC CHAT B') } 'new workspace content answered without old history')
        Assert ((Receipt).switched_workspace_verified -eq $true) 'Old workspace content crossed the session boundary.'
        CaptureConversation '03-new-workspace-new-cwd-and-chat'
        $oldProject = [string](Receipt).blank_project
        $pending = $ui.Invoke((Action 'NewBlankHwpx'))
        $modal = Wait { $ui.Modal() } 'second workspace blank default-path save'
        [void]$ui.DialogConfirm($modal).Finish(10000)
        [void]$pending.Finish(10000)
        [void](Wait { [string](Receipt).blank_project -cne $oldProject -and (Enabled 'Instruction') } 'new workspace blank report intake')
        OpenContext
        $boxes = @($ui.Prefix('Scope_'))
        $body = @($boxes | Where-Object { $ui.Enabled($_) })
        Assert ($body.Count -eq 1) 'Expected exactly one editable synthetic blank body.'
        foreach ($box in $boxes) { Check ($ui.Id($box)) ($ui.Id($box) -eq $ui.Id($body[0])) }
        CloseContext
        Text 'Instruction' ([string](Receipt).gui_request)
        [void](Wait { Enabled 'RequestReportChanges' } 'separate document-change action enabled')
        Invoke 'RequestReportChanges'
        WaitProposal 6
        NoAuthority
        $generated = Generate 1
        [void](Artifact (@($generated.versions)[0]) ([string](Receipt).blank_project))
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256([string](Receipt).blank_destination) -ceq $firstBlankHash) 'Earlier workspace original was modified.'
        CaptureConversation '04-separate-report-proposal-and-ui-created-version'
        Terminal; Invoke 'ShutdownOmp'
        [void](Wait { (Receipt).omp_released -eq $true -and (Enabled 'OpenWorkspace') } 'normal second OMP release')
        [void]$ui.Close().Finish(10000)
        [void](Wait { !$ui.MainExists } 'normal owned WPF close')
        $evidence.status = 'workspace-chat-complete-parent-host-exit-check-required'
        $evidence.limits = 'Actual WPF and two sequential owned OMP sessions: default native shell/read/write, project Markdown loaded, ordinary chat before report, selected material followup, normal workspace cutover/new cwd and native shell files, old history exclusion, separate proposal/UI consent/compiler and normal closure. Six interactive loopback inert provider calls; native skill-routing calls are separately counted and bounded. Fake-model-only profile suppresses title/recap/cache/prediction work, not normal tools or discovered skills. No real account/provider/Hancom or cold-PC proof.'
    } elseif ($Scenario -eq 'WorkspaceFolder') {
        Assert ($r.status -eq 'ready' -and $r.workspace_first -eq $true -and [int]$r.provider_calls -eq 0) 'Workspace scenario requires a new never-driven workspace fixture.'
        $workFolder = [string]$r.work_folder
        $blankPath = [string]$r.blank_destination
        $templatePath = [string]$r.template_destination
        Assert ([HwpNativeProof.SyntheticFiles]::Under($workFolder, $fixture) -and
            [HwpNativeProof.SyntheticFiles]::Under($blankPath, $workFolder) -and
            [HwpNativeProof.SyntheticFiles]::Under($templatePath, $workFolder)) 'Owned workspace paths escaped.'
        [void](Wait { Enabled 'OpenWorkspace' } 'initial workspace action')
        Assert ($ui.Visible('WorkspaceStartOpen') -and $ui.Visible('WorkspaceStartBlank') -and $ui.Visible('WorkspaceStartDocument')) 'Initial user workspace actions are hidden.'
        $pending = $ui.Invoke((Action 'OpenWorkspace'))
        $modal = Wait { $ui.Modal() } 'default owned work-folder picker'
        [void]$ui.DialogConfirm($modal).Finish(10000)
        [void]$pending.Finish(10000)
        [void](Wait { $null -eq $ui.Modal() -and $ui.Visible('WorkspaceRoot') -and (Enabled 'NewBlankHwpx') } 'empty folder admitted')
        Assert ([string]::Equals([string](Receipt).selected_workspace, $workFolder, [StringComparison]::OrdinalIgnoreCase)) 'Folder picker selected a different path.'
        Assert (!(Test-Path -LiteralPath (Join-Path $workFolder '.hwpreview'))) 'Opening an empty folder wrote project metadata.'
        Assert (@($ui.Prefix('WorkspaceDocument_')).Count -eq 0 -and $ui.Visible('WorkspaceStartBlank')) 'Empty folder did not expose new-document choices.'
        Capture '01-empty-work-folder'
        Record 'empty-folder-opened-without-writes' @{ root = $workFolder; provider_calls = 0; metadata_created = $false }
        $pending = $ui.Invoke((Action 'NewBlankHwpx'))
        $modal = Wait { $ui.Modal() } 'new HWPX save dialog with exact default new path'
        [void]$ui.DialogConfirm($modal).Finish(10000)
        [void]$pending.Finish(10000)
        [void](Wait {
            $current = Receipt
            return $null -ne $current.PSObject.Properties['blank_project'] -and (Enabled 'Instruction')
        } 'real blank created and existing document intake complete')
        Assert ([IO.File]::Exists($blankPath)) 'New HWPX source was not created at the exact selected default path.'
        $blankProject = [string](Receipt).blank_project
        $blankSha = [HwpNativeProof.SyntheticFiles]::Sha256($blankPath)
        Assert ([HwpNativeProof.SyntheticFiles]::Under($blankProject, (Join-Path $workFolder '.hwpreview/Projects'))) 'Blank project escaped workspace-owned metadata.'
        Rendered (-join [char[]]@(0xC6D0, 0xBCF8))
        $before = ObserveState
        Assert ($before.gate_passed -and @($before.versions).Count -eq 0) 'Blank creation generated or accepted a revision.'
        Assert (@(Get-ChildItem -LiteralPath $workFolder -Filter 'draft-permission.json' -Recurse -File).Count -eq 0) 'Blank creation wrote draft authority.'
        CaptureConversation '02-real-blank-hwpx'
        OpenContext
        $boxes = @($ui.Prefix('Scope_'))
        $body = @($boxes | Where-Object { $ui.Enabled($_) })
        Assert ($body.Count -eq 1) 'Real blank does not expose one editable empty body.'
        foreach ($box in $boxes) { Check ($ui.Id($box)) ($ui.Id($box) -eq $ui.Id($body[0])) }
        CloseContext
        Text 'Instruction' ([string](Receipt).gui_request)
        Invoke 'OpenOmpTerminal'
        [void](Wait { Enabled 'ContinueOmpSetup' } 'inert model ready in the same actual OMP')
        Invoke 'ContinueOmpSetup'
        [void](Wait { Enabled 'RequestReportChanges' } 'empty body context ready')
        Assert ([int](Receipt).provider_calls -eq 0) 'Folder/open/blank setup sent a model automatically.'
        Invoke 'RequestReportChanges'; WaitProposal 1
        $proposalState = ObserveState
        Assert (@($proposalState.versions).Count -eq 0) 'Proposal created a document before separate consent.'
        $generated = Generate 1
        $version = @($generated.versions)[0]
        [void](Artifact $version $blankProject)
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256($blankPath) -ceq $blankSha) 'First authored body overwrote the blank original.'
        CaptureConversation '03-first-body-authored-through-existing-engine'
        Terminal; Invoke 'ShutdownOmp'
        [void](Wait { (Receipt).omp_released -eq $true -and (Enabled 'OpenWorkspace') } 'actual owned OMP normal exit')
        [IO.File]::Copy($source, $templatePath, $false)
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256($templatePath) -ceq $evidence.source_sha256) 'Explicit owned template fixture copy changed bytes.'
        $pending = $ui.Invoke((Action 'OpenWorkspace'))
        $modal = Wait { $ui.Modal() } 'same work-folder explicit reopen'
        [void]$ui.DialogConfirm($modal).Finish(10000)
        [void]$pending.Finish(10000)
        [void](Wait { $null -eq $ui.Modal() -and $ui.Visible('WorkspaceFolder_0') -and (Enabled 'NewBlankHwpx') } 'new workspace folder discovered')
        $ui.ExpandTreeNode('WorkspaceFolder_0')
        [void](Wait { $ui.Visible('WorkspaceDocument_1') } 'nested template realized after explicit folder expansion')
        $reopened = ObserveState
        Assert (@($reopened.versions).Count -eq 1 -and [string](@($reopened.versions)[0].sha256) -ceq [string]$version.sha256) 'Reopening the same work folder lost the current immutable revision.'
        SelectDocument 'WorkspaceDocument_1'
        [void](Wait {
            $current = Receipt
            return [string]$current.project -cne $blankProject -and (Enabled 'Instruction')
        } 'existing nested template opened through workspace intake')
        $templateProject = [string](Receipt).project
        Rendered (-join [char[]]@(0xC6D0, 0xBCF8))
        $projectsBefore = @(Get-ChildItem -LiteralPath (Join-Path $workFolder '.hwpreview/Projects') -Directory | ForEach-Object { $_.FullName } | Sort-Object)
        SelectDocument 'WorkspaceDocument_1'
        Assert ([string](Receipt).project -ceq $templateProject) 'Reselecting current template changed projects.'
        Assert (($projectsBefore | ConvertTo-Json -Compress) -ceq (@(Get-ChildItem -LiteralPath (Join-Path $workFolder '.hwpreview/Projects') -Directory | ForEach-Object { $_.FullName } | Sort-Object) | ConvertTo-Json -Compress)) 'Repeated file selection duplicated project intake.'
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256($templatePath) -ceq $evidence.source_sha256 -and
            [HwpNativeProof.SyntheticFiles]::Sha256($blankPath) -ceq $blankSha) 'Workspace switching modified an original.'
        Assert ([int](Receipt).provider_calls -eq 1 -and [int](Receipt).sdk_calls -eq 0) 'Extra provider/SDK request occurred.'
        CaptureConversation '04-nested-template-selected-original-preserved'
        Record 'workspace-first-flow-complete' @{ workspace = $workFolder; blank_source_sha256 = $blankSha; template_source_sha256 = $evidence.source_sha256; blank_project = $blankProject; template_project = $templateProject; provider_calls = 1; created_revisions = 1; originals_preserved = $true; duplicate_intake = $false }
        [void]$ui.Close().Finish(10000)
        [void](Wait { !$ui.MainExists } 'owned workspace window normal close')
        $evidence.status = 'workspace-first-complete-parent-host-exit-check-required'
        $evidence.limits = 'Actual WPF folder picker/default-path save, real blank renderer, one actual OMP/inert proposal, existing UI draft consent/compiler/one authored version, readonly same-folder reopen, nested HWPX template/reuse and normal closure. No chosen-path typing/physical keyboard, real provider/login, Hancom or cold-PC proof.'
    } elseif ($Scenario -eq 'OmpIntegration') {
        Assert ($r.status -eq 'ready' -and [int]$r.provider_calls -eq 0 -and $null -eq $r.PSObject.Properties['project']) 'OMP integration requires a fresh rootless fixture.'
        [void](Wait { Enabled 'StartOmpLogin' } 'initial login action')
        Invoke 'StartOmpLogin'
        [void](Wait { $ui.Visible('ContinueOmpSetup') -and (Enabled 'ContinueOmpSetup') } 'visible next action while the same terminal remains open')
        $launch = [string](Receipt).omp_launch_id
        Assert ($ui.Visible('OmpSetupStatus') -and [int](Receipt).provider_calls -eq 0) 'Readiness hid its next-step guidance or sent a model request.'
        Record 'rootless-ready-visible-next' @{ launch_id = $launch; guidance = $ui.Read($ui.Require('OmpSetupStatus')); physical_focus = 'not claimed'; provider_calls = 0 }
        Invoke 'ContinueOmpSetup'
        [void](Wait { $ui.Visible('OmpConversation') -and (Enabled 'Instruction') } 'rootless ordinary conversation without document picker')
        Assert ([string](Receipt).omp_launch_id -ceq $launch -and [int](Receipt).provider_calls -eq 0) 'Continue restarted or sent to OMP.'
        Record 'next-action-opens-conversation' @{ launch_id = $launch; document_created = $false; provider_calls = 0 }
        SelectDocument 'LibraryOpen_0'
        [void](Wait {
            $current = Receipt
            $opened = $current.PSObject.Properties['project']
            return $null -ne $opened -and [string]$opened.Value -ceq [string]$current.prepared_project -and (Enabled 'Instruction')
        } 'first owned library intake retained the same OMP')
        $ui.ExpandTreeNode('LibraryOpen_0')
        SelectDocument 'OriginalDocument'
        OpenContext
        $boxes = @($ui.Prefix('Scope_'))
        $body = @($boxes | Where-Object { $ui.Enabled($_) })
        Assert ($body.Count -eq 1) 'Expected one editable TOY body.'
        foreach ($box in $boxes) { Check ($ui.Id($box)) ($ui.Id($box) -eq $ui.Id($body[0])) }
        CloseContext
        Text 'Instruction' ([string](Receipt).gui_request)
        Terminal
        [void](Wait { Enabled 'ContinueOmpSetup' } 'existing-document terminal next action')
        Invoke 'ContinueOmpSetup'
        [void](Wait { $ui.Visible('OmpConversation') -and (Enabled 'RequestReportChanges') } 'continue returned to the same-session composer')
        NoAuthority
        Assert ([string](Receipt).omp_launch_id -ceq $launch -and [int](Receipt).provider_calls -eq 0) 'Login continuation changed launch or sent automatically.'
        Invoke 'RequestReportChanges'
        WaitProposal 1
        $answer = $ui.Read($ui.Require('OmpLiveResponse'))
        Assert ($answer.Contains('SYNTHETIC REPORT BODY REVISED') -and !$answer.Contains('hwp-report-proposal-v1') -and !$answer.Contains('context_sha256')) 'Normal conversation leaked machine proposal JSON instead of reviewed body text.'
        Assert ($ui.ReadInspectorText('OmpProposalText').Contains('hwp-report-proposal-v1')) 'Raw proposal was not preserved in read-only advanced details.'
        NoAuthority
        Record 'human-readable-proposal-same-session' @{ launch_id = $launch; provider_calls = [int](Receipt).provider_calls; display = $answer; authority_created = $false; raw_in_advanced_only = $true }
        CaptureConversation 'omp-integration-readable-proposal'
        Terminal; Invoke 'ShutdownOmp'
        [void](Wait { (Receipt).omp_released -eq $true -and (Enabled 'NewReport') } 'actual held OMP normal exit and release')
        [void]$ui.Close().Finish(10000)
        [void](Wait { !$ui.MainExists } 'owned native window normal close')
        $evidence.status = 'omp-integration-complete-parent-host-exit-check-required'
        $evidence.limits = 'Actual production WPF/one owned OMP/inert provider: visible readiness next action, ordinary composer without document intake, first intake, same-session continuation, readable proposal, zero document generation and normal closure. No real login/provider, physical focus/keyboard, Hancom or cold-PC proof.'
    } elseif ($Scenario -eq 'Open') {
        Assert ($r.status -eq 'ready' -and [int]$r.provider_calls -eq 0 -and $null -eq $r.PSObject.Properties['project']) 'Open requires a completely fresh ready fixture.'
        [void](Wait { Enabled 'NewReport' } 'initial native UI ready')
        Assert ([HwpNativeProof.SyntheticFiles]::Under([string]$r.prepared_project, $base)) 'Exact fresh metadata-only library project required.'
        SelectDocument 'LibraryOpen_0'
        [void](Wait {
            $failure = $ui.Find('BlockingError')
            if ($null -ne $failure -and $ui.Read($failure).Length -gt 0) {
                throw ('Native intake error: ' + $ui.Read($failure) + ' | ' + $ui.Help($failure))
            }
            return (Enabled 'OpenOmpTerminal')
        } 'production report intake ready')
        Rendered (-join [char[]]@(0xC6D0, 0xBCF8))
        $ui.ExpandTreeNode('LibraryOpen_0')
        SelectDocument 'OriginalDocument'
        OpenContext
        $boxes = @($ui.Prefix('Scope_'))
        $body = @($boxes | Where-Object { $ui.Enabled($_) })
        Assert ($body.Count -eq 1) 'This fresh unsigned TOY must expose exactly one selectable body and protected boundaries.'
        foreach ($box in $boxes) { Check ($ui.Id($box)) ($ui.Id($box) -eq $ui.Id($body[0])) }
        CloseContext
        Text 'Instruction' ([string](Receipt).gui_request)
        Invoke 'OpenOmpTerminal'
        [void](Wait { $ui.Visible('ShutdownOmp') -and (Enabled 'ShutdownOmp') } 'actual native session handshake and observed idle')
        Authoring
        [void](Wait { Enabled 'RequestReportChanges' } 'configured inert model and exact public context ready')
        Assert ([int](Receipt).provider_calls -eq 0) 'Opening or editing automatically called a model.'
        NoAuthority
        CaptureConversation '01-actual-omp-composer-and-selected-context'
        Record 'actual-session-ready-no-send' @{ launch_id = (Receipt).omp_launch_id; native_dll_sha256 = (Receipt).native_dll_sha256; provider_calls = 0 }
        $evidence.status = 'open-complete-no-model-send'
    } elseif ($Scenario -eq 'Core') {
        Assert ($r.status -eq 'project_ready' -and [int]$r.provider_calls -eq 0 -and (Enabled 'RequestReportChanges')) 'Core requires the successfully opened never-sent current scope.'
        $project = [HwpNativeProof.SyntheticFiles]::FullPath([string]$r.project)
        $launch = [string]$r.omp_launch_id
        NoAuthority
        Invoke 'RequestReportChanges'
        WaitProposal 1
        NoAuthority
        CaptureConversation '02-current-gui-proposal-separate-consent'
        $state = Generate 1
        $first = @($state.versions)[0]
        $firstArtifact = Artifact $first $project
        Authoring
        OpenContext; $ui.SelectBase(1); CloseContext
        Text 'Instruction' 'TOY ONLY: follow up from the selected generated body; preserve all original boundaries and objects.'
        OpenContext
        $preview = $ui.RequireRole('PreviewRequest', [Windows.Automation.ControlType]::Button)
        Assert ($ui.Enabled($preview)) 'Context preview is disabled.'
        [void]$ui.InvokeObserved($preview).Finish(15000)
        CloseContext
        [void](Wait { Enabled 'RequestReportChanges' } 'current followup context ready')
        Invoke 'RequestReportChanges'
        WaitProposal 2
        CaptureConversation '03-current-gui-followup-proposal'
        $state = Generate 2
        $followup = @($state.versions)[1]
        $followupArtifact = Artifact $followup $project
        Assert ($followupArtifact.metadata.previous_version -eq $first.id) 'Followup lost the selected version lineage.'
        Assert ([string](Receipt).omp_launch_id -ceq $launch -and [int](Receipt).provider_calls -eq 2) 'Extra native session or provider dispatch occurred.'
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256($source) -ceq $evidence.source_sha256) 'Original source changed.'
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256($firstArtifact.path) -ceq [string]$first.sha256) 'Prior canonical changed.'
        SelectDocument 'OriginalDocument'; Rendered (-join [char[]]@(0xC6D0, 0xBCF8))
        SelectDocument ('Version_' + $first.id); Rendered ([string]$first.label)
        SelectDocument ('Version_' + $followup.id); Rendered ([string]$followup.label)
        Assert ([string](Receipt).project -ceq $project -and [int](Receipt).provider_calls -eq 2) 'Tree history navigation re-imported or dispatched a model.'
        CaptureConversation '04-current-gui-immutable-versions'
        Terminal; Invoke 'ShutdownOmp'
        [void](Wait { (Receipt).omp_released -eq $true -and (Enabled 'NewReport') } 'actual held OMP exit and release')
        CaptureConversation '05-current-gui-normal-omp-exit'
        Record 'normal-native-window-close' @{ forced = $false }
        [void]$ui.Close().Finish(10000)
        [void](Wait { !$ui.MainExists } 'normal native window close')
        $evidence.status = 'core-gui-complete-parent-host-exit-check-required'
        $evidence.limits = 'Production WPF accessibility actions: library intake, two actual OMP/local-inert proposals, UI draft consent, real HWPX generation/followup, immutable versions and normal exit. No desktop keyboard, file dialogs, external edit/import/export, real model, Hancom or other-PC qualification.'
    } else {
        Assert ($r.status -eq 'project_ready' -and [int]$r.provider_calls -eq 0 -and (Enabled 'RequestReportChanges')) 'Journey requires the successfully opened, never-sent current scope.'
        $project = [HwpNativeProof.SyntheticFiles]::FullPath([string]$r.project)
        $launch = [string]$r.omp_launch_id
        NoAuthority
        Invoke 'RequestReportChanges'
        [void](Wait { [int](Receipt).provider_waiting -eq 1 } 'first local provider wait')
        Assert (!(Enabled 'NewReport') -and !(Enabled 'StartDraft')) 'Unsettled model request did not block writes.'
        Record 'busy-close-once' @{ expected = 'blocked'; forced = $false }
        [void]$ui.Close().Finish(10000)
        Assert ($ui.MainExists) 'Busy close abandoned actual OMP.'
        [void](Wait { $ui.Read($ui.Require('BlockingError')).Length -gt 0 } 'busy close visible blocking reason')
        CaptureConversation '02-model-active-busy-close-blocked'
        WaitProposal 1
        NoAuthority
        CaptureConversation '03-matching-proposal-separate-draft-consent'
        $state = Generate 1
        $first = @($state.versions)[0]
        $firstArtifact = Artifact $first $project
        Invoke 'OpenReviewCopy'
        [void](Wait { @((Receipt).opens).Count -eq 1 -and (Enabled 'ImportOpenedCopy') } 'owned review-copy handoff and final GUI refresh settled')
        $opened = @((Receipt).opens)[0]
        Assert ([string]$opened.sha256 -ceq [string]$first.sha256) 'Review handoff differs from selected canonical.'
        $mutation = [HwpNativeProof.SyntheticFiles]::EditRecordedReview([string]$opened.path, [string]$opened.sha256,
            'SYNTHETIC REPORT BODY REVISED', 'SYNTHETIC REPORT BODY EDITED IN OWNED REVIEW COPY')
        Record 'fixed-unsigned-review-edit' @{ path = $opened.path; before = $mutation.BeforeSha256; after = $mutation.AfterSha256 }
        Invoke 'ImportOpenedCopy'
        Authoring
        $state = SettledVersions 2
        $imported = @($state.versions)[1]
        $importedArtifact = Artifact $imported $project
        Assert ($imported.external -and $importedArtifact.metadata.previous_version -eq $first.id -and $imported.sha256 -ceq $mutation.AfterSha256) 'Imported revision lineage or exact edited bytes differ.'
        Authoring
        OpenContext; $ui.SelectBase(1); CloseContext
        Text 'Instruction' 'TOY ONLY: follow up from the selected imported body, using the attached reference without claiming group achievements.'
        OpenContext
        Dialog 'AddMaterial' ([string](Receipt).material)
        $materials = @(Wait {
            $items = @($ui.Prefix('MaterialOwn_'))
            Assert ($items.Count -le 1) 'Material intake created unexpected extra entries.'
            if ($items.Count -eq 1 -and $ui.Enabled($items[0])) { return $items[0] }
            return $null
        } 'exact attached reference and material options refresh settled')
        Assert ($materials.Count -eq 1 -and $ui.ToggleState($materials[0]) -eq 0) 'Own-achievement declaration did not default to false.'
        $preview = $ui.RequireRole('PreviewRequest', [Windows.Automation.ControlType]::Button)
        Assert ($ui.Enabled($preview)) 'Context preview is disabled.'
        [void]$ui.InvokeObserved($preview).Finish(15000)
        CloseContext
        [void](Wait { Enabled 'RequestReportChanges' } 'new explicit followup context ready')
        Invoke 'RequestReportChanges'
        WaitProposal 2
        CaptureConversation '04-same-session-followup-proposal'
        $state = Generate 3
        $followup = @($state.versions)[2]
        $followupArtifact = Artifact $followup $project
        Assert ($followupArtifact.metadata.previous_version -eq $imported.id) 'Followup did not use the selected imported revision.'
        Assert ([string](Receipt).omp_launch_id -ceq $launch -and [int](Receipt).provider_calls -eq 2) 'A second launch or duplicate model dispatch occurred.'
        Invoke 'AcceptVersion'
        [void](Wait { $current = State; @($current.accepted).Count -eq 1 -and $current.accepted[0] -ceq $followup.id -and (Enabled 'ExportArtifact') } 'explicit exact-artifact adoption and GUI refresh settled')
        $export = Join-Path $Output 'selected-followup-export.hwpx'
        Dialog 'ExportArtifact' $export
        Assert (Test-Path -LiteralPath $export -PathType Leaf) 'Chosen Save filename was not committed. Export remains unverified; preserve default output/dialog/scope without rename, fallback or retry.'
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256($export) -ceq $followup.sha256) 'Export differs from selected immutable HWPX.'
        Record 'exact-artifact-adopted-and-exported' @{ version_id = $followup.id; sha256 = $followup.sha256; path = $export; existing_overwrite = $false }
        SelectDocument 'OriginalDocument'; Rendered (-join [char[]]@(0xC6D0, 0xBCF8))
        SelectDocument ('Version_' + $first.id); Rendered ([string]$first.label)
        SelectDocument ('Version_' + $followup.id); Rendered ([string]$followup.label)
        $expectedCalls = 2
        Authoring
        if ($InputMode -eq 'Physical') {
            Invoke 'FocusOmpTerminal'
            Record 'fixed-synthetic-terminal-input-and-enter' @{ request = (Receipt).tui_request; clipboard = $false; replay = $false }
            [void](Wait { $ui.Visible('ShowOmpConversation') } 'same actual terminal view visible before physical input')
            $ui.SubmitWorkspaceTerminalPrompt()
            [void](Wait { [int](Receipt).provider_waiting -eq 3 } 'actual TUI request in the same native session')
            Release 3
            [void](Wait { $ui.Visible('ShutdownOmp') -and (Enabled 'ShutdownOmp') } 'actual TUI response run settled in terminal')
            Authoring
            [void](Wait { $ui.Read($ui.Require('OmpLiveResponse')) -ceq [string](Receipt).tui_answer } 'exact public advisory answer in conversation')
            Assert (!(Enabled 'StartDraft')) 'Unbound TUI answer gained document authority.'
            $state = ObserveState
            Assert (@($state.versions).Count -eq 3) 'Terminal free chat created a document.'
            Capture '05-real-tui-unicode-and-gui-advisory'
            $expectedCalls = 3
        } else {
            Record 'physical-terminal-input-not-exercised' @{ input_mode = 'Accessibility'; no_input_fallback = $true; gui_model_calls = 2 }
        }
        Terminal; Invoke 'ShutdownOmp'
        [void](Wait { (Receipt).omp_released -eq $true -and (Enabled 'NewReport') } 'actual owned OMP exit and release')
        CaptureConversation '06-owned-omp-normal-exit'
        Assert ([HwpNativeProof.SyntheticFiles]::Sha256($source) -ceq $evidence.source_sha256) 'Original source bytes changed.'
        foreach ($entry in @(@{ version = $first; artifact = $firstArtifact }, @{ version = $imported; artifact = $importedArtifact }, @{ version = $followup; artifact = $followupArtifact })) {
            Assert ([HwpNativeProof.SyntheticFiles]::Sha256($entry.artifact.path) -ceq [string]$entry.version.sha256) 'Immutable canonical was changed.'
        }
        Dialog 'NewReport' $source
        [void](Wait { (Receipt).project -ne $project -and (Enabled 'OpenOmpTerminal') } 'fresh project switch without auto OMP')
        Dialog 'OpenReport' $project
        [void](Wait { [string](Receipt).project -ceq $project -and (Enabled 'OpenOmpTerminal') } 'exact project reopened without model call')
        $state = ObserveState
        Assert (@($state.versions).Count -eq 3 -and [int](Receipt).provider_calls -eq $expectedCalls -and $state.accepted -contains $followup.id) 'Reopen lost history/adoption or called a model.'
        CaptureConversation '07-reopened-report-history'
        Record 'normal-native-window-close' @{ forced = $false }
        [void]$ui.Close().Finish(10000)
        [void](Wait { !$ui.MainExists } 'normal native window close')
        $evidence.status = 'journey-complete-parent-host-exit-check-required'
        $evidence.limits = 'Actual production WPF library/context/proposals, UI-only draft/adoption authority, real HWPX/import/followup/history/export/reopen/normal exit. Model/OS handoff inert. Physical keyboard/TUI are exercised only when input_mode=Physical. No real provider/Hancom/other-PC proof.'
    }
    Save
    Write-Output (Join-Path $Output 'evidence.json')
} catch {
    $evidence.status = 'failed_preserved'; $evidence.error = $_.Exception.Message
    $evidence.error_detail = $_.Exception.ToString(); $evidence.script_stack = $_.ScriptStackTrace; Save
    throw "Single-session UI proof stopped; preserve host/process/files and do not replay: $(Join-Path $Output 'evidence.json'). $($_.Exception.Message)"
}
