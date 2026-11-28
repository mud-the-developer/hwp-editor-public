using System.ComponentModel;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using Microsoft.Win32;

namespace HWPReview.Native;

internal sealed record WorkspaceDocumentItem(string Path, string Name, string RelativePath, bool Protected)
{
    public override string ToString() => Protected ? RelativePath + " [보호]" : RelativePath;
}

internal sealed record CheckpointItem(string Id, string Sha256, string CreatedAt, string Reason, long Size, bool IsOriginal);

internal sealed class MainWindow : Window
{
    private const string Protocol = "hwp-native-ui-v1";

    private readonly OwnedHostClient host;
    private readonly string nonce;
    private readonly HwpxDocumentPane documentView = new();
    private readonly OmpTerminalPane ompTerminal = new();
    private WorkspaceDocumentWatcher? liveWatcher;
    private readonly MenuItem followSaves = new() { Header = "다른 문서 저장 시 자동 전환", IsCheckable = true, IsChecked = true };

    private readonly ColumnDefinition drawerColumn = new() { Width = new GridLength(0) };
    private readonly ColumnDefinition drawerSplitterColumn = new() { Width = new GridLength(0) };
    private readonly Border drawerPanel = new() { Visibility = Visibility.Collapsed };
    private readonly GridSplitter drawerSplitter = new() { Visibility = Visibility.Collapsed };

    private readonly Button drawerToggleButton = new();
    private readonly Button openWorkspaceButton = new();
    private readonly ComboBox documentSwitcher = new();
    private readonly Button openInHancomButton = new();
    private readonly Button refreshButton = new();
    private readonly Button exportButton = new();
    private readonly Border syncBadge = new();
    private readonly TextBlock syncBadgeText = new();
    private readonly TextBlock ompStateText = new();
    private readonly Button ompActionButton = new();

    private readonly TextBlock drawerWorkspaceLabel = new();
    private readonly ListBox documentListBox = new();
    private readonly TextBlock checkpointCountLabel = new();
    private readonly ListBox checkpointListBox = new();
    private readonly Button manualCheckpointButton = new();

    private readonly Border previewBanner = new() { Visibility = Visibility.Collapsed };
    private readonly TextBlock previewBannerText = new();
    private readonly Button restoreBannerButton = new();
    private readonly Button returnLiveBannerButton = new();
    private readonly Button dismissBannerButton = new();

    private readonly List<WorkspaceDocumentItem> documents = [];
    private readonly Dictionary<string, WorkspaceDocumentItem> documentsByPath = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<CheckpointItem> checkpoints = [];

    private string workspaceRoot = "";
    private string activeDocumentPath = "";
    private string activeDocumentName = "";
    private string activeLiveSha256 = "";
    private string originalSha256 = "";
    private string previewingCheckpointId = "";
    private string? activeOmpLaunchId;
    private bool drawerOpen;
    private bool updatingSelectors;
    private bool busy;
    private bool closing;
    private bool closingConfirmed;

    public MainWindow(OwnedHostClient host, string nonce)
    {
        this.host = host;
        this.nonce = nonce;

        Title = "HWP Editor";
        Width = 1480;
        Height = 900;
        MinWidth = 960;
        MinHeight = 600;
        SetResourceReference(BackgroundProperty, "CanvasBrush");

        Content = BuildLayout();
        WireEvents();
        UpdateUiState();
    }

    private UIElement BuildLayout()
    {
        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(42) });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        UIElement topBar = BuildTopBar();
        Grid.SetRow(topBar, 0);
        root.Children.Add(topBar);

        var body = new Grid();
        body.ColumnDefinitions.Add(drawerColumn);
        body.ColumnDefinitions.Add(drawerSplitterColumn);
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(58, GridUnitType.Star), MinWidth = 360 });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(6) });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(42, GridUnitType.Star), MinWidth = 320 });
        Grid.SetRow(body, 1);

        BuildDrawer();
        Grid.SetColumn(drawerPanel, 0);
        body.Children.Add(drawerPanel);

        drawerSplitter.HorizontalAlignment = HorizontalAlignment.Stretch;
        drawerSplitter.VerticalAlignment = VerticalAlignment.Stretch;
        drawerSplitter.ResizeBehavior = GridResizeBehavior.PreviousAndNext;
        drawerSplitter.SetResourceReference(BackgroundProperty, "CanvasBrush");
        Grid.SetColumn(drawerSplitter, 1);
        body.Children.Add(drawerSplitter);

        UIElement previewPane = BuildPreviewPane();
        Grid.SetColumn(previewPane, 2);
        body.Children.Add(previewPane);

        var mainSplitter = new GridSplitter
        {
            HorizontalAlignment = HorizontalAlignment.Stretch,
            VerticalAlignment = VerticalAlignment.Stretch,
            ResizeBehavior = GridResizeBehavior.PreviousAndNext
        };
        mainSplitter.SetResourceReference(BackgroundProperty, "CanvasBrush");
        AutomationProperties.SetName(mainSplitter, "미리보기와 OMP 터미널 너비 조절");
        Grid.SetColumn(mainSplitter, 3);
        body.Children.Add(mainSplitter);

        var terminalHost = new Border
        {
            BorderThickness = new Thickness(1, 0, 0, 0),
            Child = ompTerminal
        };
        terminalHost.SetResourceReference(Border.BorderBrushProperty, "PanelBorderBrush");
        terminalHost.SetResourceReference(Border.BackgroundProperty, "TerminalBgBrush");
        Grid.SetColumn(terminalHost, 4);
        body.Children.Add(terminalHost);

        root.Children.Add(body);
        return root;
    }

    private UIElement BuildTopBar()
    {
        var bar = new Border
        {
            BorderThickness = new Thickness(0, 0, 0, 1),
            Padding = new Thickness(8, 0, 8, 0)
        };
        bar.SetResourceReference(Border.BackgroundProperty, "SurfaceBrush");
        bar.SetResourceReference(Border.BorderBrushProperty, "PanelBorderBrush");

        var dock = new DockPanel { LastChildFill = false, VerticalAlignment = VerticalAlignment.Center };

        // Left action cluster
        var left = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };

        drawerToggleButton.Style = (Style)FindResource("TopBarIconButton");
        drawerToggleButton.Content = AppIcons.Create(AppIconKind.Files, 16, "BodyTextBrush");
        drawerToggleButton.ToolTip = "문서·체크포인트 서랍 열기/닫기 (Ctrl+B)";
        AutomationProperties.SetName(drawerToggleButton, "서랍 열기 또는 닫기");
        AutomationProperties.SetAutomationId(drawerToggleButton, "DrawerToggleButton");
        left.Children.Add(drawerToggleButton);

        openWorkspaceButton.Content = IconLabel(AppIconKind.FolderOpen, "폴더", "BodyTextBrush");
        openWorkspaceButton.ToolTip = "작업 폴더 열기 (Ctrl+O)";
        AutomationProperties.SetAutomationId(openWorkspaceButton, "OpenWorkspaceButton");
        left.Children.Add(openWorkspaceButton);

        documentSwitcher.MinWidth = 180;
        documentSwitcher.MaxWidth = 300;
        documentSwitcher.Height = 28;
        documentSwitcher.Margin = new Thickness(2, 0, 6, 0);
        documentSwitcher.FontSize = 12;
        documentSwitcher.VerticalContentAlignment = VerticalAlignment.Center;
        documentSwitcher.ToolTip = "작업 폴더의 HWP·HWPX 문서 전환";
        AutomationProperties.SetName(documentSwitcher, "활성 문서 선택");
        AutomationProperties.SetAutomationId(documentSwitcher, "DocumentSwitcher");
        left.Children.Add(documentSwitcher);

        openInHancomButton.Style = (Style)FindResource("PrimaryButton");
        openInHancomButton.Content = "한글에서 열기";
        openInHancomButton.ToolTip = "활성 작업 .hwpx 파일을 한글에서 직접 엽니다 (Ctrl+S 저장 시 자동 반영)";
        AutomationProperties.SetAutomationId(openInHancomButton, "OpenInHancomButton");
        left.Children.Add(openInHancomButton);

        refreshButton.Content = "↻ 새로고침";
        refreshButton.ToolTip = "현재 문서를 다시 읽고 미리보기를 갱신합니다 (F5)";
        AutomationProperties.SetAutomationId(refreshButton, "RefreshButton");
        left.Children.Add(refreshButton);

        exportButton.Content = "내보내기";
        exportButton.ToolTip = "현재 문서 또는 미리보기 중인 체크포인트를 새 .hwpx로 내보냅니다";
        AutomationProperties.SetAutomationId(exportButton, "ExportButton");
        left.Children.Add(exportButton);

        DockPanel.SetDock(left, Dock.Left);
        dock.Children.Add(left);

        // Right status + OMP controls + overflow menu
        var right = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };

        syncBadge.CornerRadius = new CornerRadius(10);
        syncBadge.Padding = new Thickness(8, 2, 8, 2);
        syncBadge.Margin = new Thickness(0, 0, 8, 0);
        syncBadge.VerticalAlignment = VerticalAlignment.Center;
        syncBadge.SetResourceReference(Border.BackgroundProperty, "SoftAccentBrush");
        syncBadgeText.FontSize = 11.5;
        syncBadgeText.MaxWidth = 175;
        syncBadgeText.TextTrimming = TextTrimming.CharacterEllipsis;
        syncBadgeText.Text = "폴더를 열어 시작하세요";
        syncBadgeText.SetResourceReference(TextBlock.ForegroundProperty, "AccentBrush");
        syncBadge.Child = syncBadgeText;
        AutomationProperties.SetAutomationId(syncBadgeText, "SyncStatusText");
        right.Children.Add(syncBadge);

        ompStateText.FontSize = 11.5;
        ompStateText.VerticalAlignment = VerticalAlignment.Center;
        ompStateText.Margin = new Thickness(0, 0, 6, 0);
        ompStateText.MaxWidth = 220;
        ompStateText.TextTrimming = TextTrimming.CharacterEllipsis;
        ompStateText.Text = "OMP 준비 중";
        ompStateText.SetResourceReference(TextBlock.ForegroundProperty, "MutedTextBrush");
        AutomationProperties.SetAutomationId(ompStateText, "OmpStateText");
        right.Children.Add(ompStateText);

        ompActionButton.Height = 26;
        ompActionButton.Padding = new Thickness(8, 0, 8, 0);
        ompActionButton.Margin = new Thickness(0, 0, 4, 0);
        ompActionButton.Visibility = Visibility.Collapsed;
        right.Children.Add(ompActionButton);

        var menu = new Menu { Background = Brushes.Transparent, VerticalAlignment = VerticalAlignment.Center };
        var overflow = new MenuItem
        {
            Style = (Style)FindResource("ToolsMenuButton"),
            Header = AppIcons.Create(AppIconKind.Ellipsis, 16, "BodyTextBrush"),
            ToolTip = "추가 도구"
        };
        AutomationProperties.SetName(overflow, "추가 도구 메뉴");
        overflow.Items.Add(CreateMenuItem("새 빈 HWPX 만들기", "Ctrl+N", async () => await NewBlankDocumentAsync()));
        AutomationProperties.SetAutomationId(followSaves, "FollowSavedDocuments");
        followSaves.ToolTip = "끄면 현재 문서만 자동 갱신합니다. 다른 파일은 문서 목록에서 선택할 수 있습니다.";
        overflow.Items.Add(followSaves);
        overflow.Items.Add(CreateMenuItem("수동 체크포인트 저장", "", async () => await RecordManualCheckpointAsync()));
        overflow.Items.Add(new Separator());
        overflow.Items.Add(CreateMenuItem("OMP 시작 / 다시 시작", "", async () => await RestartOmpAsync()));
        overflow.Items.Add(CreateMenuItem("OMP 정상 종료", "", async () => await StopOmpAsync()));
        overflow.Items.Add(new Separator());
        overflow.Items.Add(CreateMenuItem("기존 HWP 도구", "", LaunchLegacyToolsAsync));
        overflow.Items.Add(CreateMenuItem("필수 도구 상태", "", async () => await LaunchSetupRequirementsAsync()));
        overflow.Items.Add(CreateMenuItem("법적 고지", "", async () => await ShowLegalNoticeAsync()));
        menu.Items.Add(overflow);
        right.Children.Add(menu);

        DockPanel.SetDock(right, Dock.Right);
        dock.Children.Add(right);

        bar.Child = dock;
        return bar;
    }

    private void BuildDrawer()
    {
        drawerPanel.BorderThickness = new Thickness(0, 0, 1, 0);
        drawerPanel.SetResourceReference(Border.BackgroundProperty, "SurfaceBrush");
        drawerPanel.SetResourceReference(Border.BorderBrushProperty, "PanelBorderBrush");

        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        // Section 1 header: Workspace documents
        var docHeader = new DockPanel { Margin = new Thickness(10, 10, 8, 4) };
        var newDocBtn = new Button
        {
            Style = (Style)FindResource("QuietButton"),
            Content = IconLabel(AppIconKind.Plus, "새 HWPX", "AccentBrush"),
            ToolTip = "작업 폴더에 새 빈 HWPX 만들기 (Ctrl+N)"
        };
        newDocBtn.Click += async (_, _) => await NewBlankDocumentAsync();
        DockPanel.SetDock(newDocBtn, Dock.Right);
        docHeader.Children.Add(newDocBtn);

        drawerWorkspaceLabel.Text = "작업 폴더 문서";
        drawerWorkspaceLabel.FontSize = 12;
        drawerWorkspaceLabel.FontWeight = FontWeights.SemiBold;
        drawerWorkspaceLabel.VerticalAlignment = VerticalAlignment.Center;
        drawerWorkspaceLabel.TextTrimming = TextTrimming.CharacterEllipsis;
        drawerWorkspaceLabel.SetResourceReference(TextBlock.ForegroundProperty, "BodyTextBrush");
        docHeader.Children.Add(drawerWorkspaceLabel);
        Grid.SetRow(docHeader, 0);
        grid.Children.Add(docHeader);

        documentListBox.Style = (Style)FindResource("DrawerListBox");
        documentListBox.ItemContainerStyle = (Style)FindResource("DrawerListItem");
        AutomationProperties.SetName(documentListBox, "작업 폴더 문서 목록");
        Grid.SetRow(documentListBox, 1);
        grid.Children.Add(documentListBox);

        // Section 2 header: Auto-saved checkpoint timeline
        var cpHeaderBorder = new Border
        {
            BorderThickness = new Thickness(0, 1, 0, 0),
            Padding = new Thickness(10, 8, 8, 4)
        };
        cpHeaderBorder.SetResourceReference(Border.BorderBrushProperty, "PanelBorderBrush");
        var cpHeader = new DockPanel();
        manualCheckpointButton.Style = (Style)FindResource("QuietButton");
        manualCheckpointButton.Content = "저장";
        manualCheckpointButton.ToolTip = "현재 상태를 체크포인트로 저장";
        DockPanel.SetDock(manualCheckpointButton, Dock.Right);
        cpHeader.Children.Add(manualCheckpointButton);

        var cpTitleStack = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        cpTitleStack.Children.Add(AppIcons.Create(AppIconKind.History, 14, "MutedTextBrush"));
        checkpointCountLabel.Text = "체크포인트 타임라인";
        checkpointCountLabel.FontSize = 12;
        checkpointCountLabel.FontWeight = FontWeights.SemiBold;
        checkpointCountLabel.Margin = new Thickness(6, 0, 0, 0);
        checkpointCountLabel.SetResourceReference(TextBlock.ForegroundProperty, "BodyTextBrush");
        cpTitleStack.Children.Add(checkpointCountLabel);
        cpHeader.Children.Add(cpTitleStack);
        cpHeaderBorder.Child = cpHeader;
        Grid.SetRow(cpHeaderBorder, 2);
        grid.Children.Add(cpHeaderBorder);

        checkpointListBox.Style = (Style)FindResource("DrawerListBox");
        checkpointListBox.ItemContainerStyle = (Style)FindResource("DrawerListItem");
        AutomationProperties.SetName(checkpointListBox, "자동 저장 체크포인트 타임라인");
        Grid.SetRow(checkpointListBox, 3);
        grid.Children.Add(checkpointListBox);

        drawerPanel.Child = grid;
    }

    private UIElement BuildPreviewPane()
    {
        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        previewBanner.BorderThickness = new Thickness(0, 0, 0, 1);
        previewBanner.Padding = new Thickness(10, 6, 10, 6);
        previewBanner.SetResourceReference(Border.BorderBrushProperty, "PanelBorderBrush");
        previewBanner.SetResourceReference(Border.BackgroundProperty, "WarningBgBrush");

        var bannerDock = new DockPanel { LastChildFill = true };
        var actions = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };

        restoreBannerButton.Style = (Style)FindResource("PrimaryButton");
        restoreBannerButton.Height = 24;
        restoreBannerButton.Content = "이 시점으로 복원";
        actions.Children.Add(restoreBannerButton);

        returnLiveBannerButton.Height = 24;
        returnLiveBannerButton.Content = "현재 파일로 돌아가기";
        actions.Children.Add(returnLiveBannerButton);

        dismissBannerButton.Style = (Style)FindResource("QuietButton");
        dismissBannerButton.Content = "닫기";
        dismissBannerButton.Visibility = Visibility.Collapsed;
        actions.Children.Add(dismissBannerButton);

        DockPanel.SetDock(actions, Dock.Right);
        bannerDock.Children.Add(actions);

        previewBannerText.FontSize = 12;
        previewBannerText.VerticalAlignment = VerticalAlignment.Center;
        previewBannerText.TextWrapping = TextWrapping.Wrap;
        previewBannerText.Margin = new Thickness(0, 0, 8, 0);
        previewBannerText.SetResourceReference(TextBlock.ForegroundProperty, "WarningTextBrush");
        bannerDock.Children.Add(previewBannerText);

        previewBanner.Child = bannerDock;
        Grid.SetRow(previewBanner, 0);
        grid.Children.Add(previewBanner);

        Grid.SetRow(documentView, 1);
        grid.Children.Add(documentView);
        return grid;
    }

    private void WireEvents()
    {
        ContentRendered += async (_, _) => await InitializeAsync();
        Closing += OnWindowClosing;
        PreviewKeyDown += OnPreviewKeyDown;

        drawerToggleButton.Click += (_, _) => SetDrawerOpen(!drawerOpen);
        openWorkspaceButton.Click += async (_, _) => await PickAndOpenWorkspaceAsync();
        openInHancomButton.Click += async (_, _) => await OpenInHancomAsync();
        refreshButton.Click += async (_, _) => await RefreshActiveDocumentAsync();
        exportButton.Click += async (_, _) => await ExportDocumentAsync();
        manualCheckpointButton.Click += async (_, _) => await RecordManualCheckpointAsync();
        ompActionButton.Click += async (_, _) => await HandleOmpActionButtonAsync();
        restoreBannerButton.Click += async (_, _) => await RestorePreviewedCheckpointAsync();
        returnLiveBannerButton.Click += async (_, _) => await ReturnToLiveDocumentAsync();
        dismissBannerButton.Click += (_, _) => HideBanner();

        documentSwitcher.SelectionChanged += async (_, _) =>
        {
            if (updatingSelectors) return;
            if (documentSwitcher.SelectedItem is WorkspaceDocumentItem selected)
                await OpenDocumentItemAsync(selected);
        };
        documentListBox.SelectionChanged += async (_, _) =>
        {
            if (updatingSelectors) return;
            if (documentListBox.SelectedItem is ListBoxItem { Tag: WorkspaceDocumentItem selected })
                await OpenDocumentItemAsync(selected);
        };
        checkpointListBox.SelectionChanged += async (_, _) =>
        {
            if (updatingSelectors) return;
            if (checkpointListBox.SelectedItem is ListBoxItem { Tag: CheckpointItem item })
                await PreviewCheckpointAsync(item);
        };

        documentView.PassageDoubleClicked += async (_, target) => await ForwardPassageToOmpAsync(target);
        documentView.ErrorRaised += (_, message) => ShowAlertBanner(message, isError: true);

        ompTerminal.StatusChanged += (_, _) => Dispatcher.BeginInvoke(UpdateOmpStatus);
        ompTerminal.ErrorRaised += (_, message) => Dispatcher.BeginInvoke(() => ShowAlertBanner("OMP: " + message, isError: true));
    }

    private async Task InitializeAsync()
    {
        try
        {
            JsonElement hello = await host.CallAsync("hello", new { protocol = Protocol, nonce });
            if (hello.Text("protocol") != Protocol || hello.Text("nonce") != nonce)
                throw new InvalidDataException("백엔드 핸드셰이크가 일치하지 않습니다.");
            string suggestedRoot = hello.Text("suggested_root");
            if (!string.IsNullOrWhiteSpace(suggestedRoot) && Directory.Exists(suggestedRoot))
                await OpenWorkspaceAsync(suggestedRoot);
            else
                await StartOmpAsync();
        }
        catch (Exception error)
        {
            ShowAlertBanner("초기화 오류: " + error.Message, isError: true);
        }
    }

    private async void OnPreviewKeyDown(object sender, KeyEventArgs args)
    {
        if (args.Key == Key.F5)
        {
            args.Handled = true;
            await RefreshActiveDocumentAsync();
            return;
        }
        if ((Keyboard.Modifiers & ModifierKeys.Control) == 0) return;
        if (args.Key == Key.B)
        {
            args.Handled = true;
            SetDrawerOpen(!drawerOpen);
        }
        else if (args.Key == Key.O)
        {
            args.Handled = true;
            await PickAndOpenWorkspaceAsync();
        }
        else if (args.Key == Key.N)
        {
            args.Handled = true;
            await NewBlankDocumentAsync();
        }
        else if (args.Key == Key.R)
        {
            args.Handled = true;
            await RefreshActiveDocumentAsync();
        }
    }

    private void SetDrawerOpen(bool open)
    {
        drawerOpen = open;
        drawerPanel.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
        drawerSplitter.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
        drawerColumn.Width = open ? new GridLength(260) : new GridLength(0);
        drawerSplitterColumn.Width = open ? new GridLength(5) : new GridLength(0);
        if (open)
            drawerToggleButton.SetResourceReference(Button.BackgroundProperty, "SoftAccentBrush");
        else
            drawerToggleButton.Background = Brushes.Transparent;
    }

    private async Task PickAndOpenWorkspaceAsync()
    {
        var dialog = new OpenFolderDialog { Title = "작업 폴더 선택" };
        if (!string.IsNullOrWhiteSpace(workspaceRoot) && Directory.Exists(workspaceRoot))
            dialog.InitialDirectory = workspaceRoot;
        if (dialog.ShowDialog(this) != true || string.IsNullOrWhiteSpace(dialog.FolderName)) return;
        await OpenWorkspaceAsync(dialog.FolderName);
    }

    private async Task OpenWorkspaceAsync(string folderPath)
    {
        if (busy) return;
        busy = true;
        UpdateUiState();
        try
        {
            string fullFolder = Path.GetFullPath(folderPath);
            bool changingFolder = !string.Equals(workspaceRoot, fullFolder, StringComparison.OrdinalIgnoreCase);

            // Cleanly stop OMP before changing workspace root so OMP always runs in the active folder.
            if (changingFolder && (ompTerminal.IsRunning || activeOmpLaunchId is not null))
            {
                if (ompTerminal.IsRunning && !ompTerminal.AgentState.IsIdle)
                {
                    ShowAlertBanner("OMP 작업이 실행 중입니다. 먼저 중단하거나 완료된 뒤 작업 폴더를 전환하세요.", isError: false);
                    return;
                }
                await StopOmpCoreAsync();
            }

            // Keep watching the current workspace until the host admits the new one.

            JsonElement listing = changingFolder
                ? await host.CallAsync("open_workspace", new { root = fullFolder })
                : await host.CallAsync("list_workspace", new { });
            workspaceRoot = listing.Text("root", fullFolder);
            liveWatcher?.Dispose();
            liveWatcher = null;
            ClearActiveDocument("작업 폴더의 문서를 확인하고 있습니다.");
            liveWatcher = new WorkspaceDocumentWatcher(workspaceRoot,
                () => Dispatcher.BeginInvoke(new Action(() => _ = ProcessWorkspaceChangesAsync())));
            // Subscribe before the definitive listing so even the first OMP-created file is seen.
            listing = await host.CallAsync("list_workspace", new { });
            string folderName = listing.Text("name", Path.GetFileName(workspaceRoot));
            drawerWorkspaceLabel.Text = folderName;
            Title = $"HWP Editor — {folderName}";
            PopulateWorkspaceDocuments(listing);

            // The document side works independently of OMP readiness or authentication.
            Task terminalStartup = StartOmpAsync();
            try
            {
                WorkspaceDocumentItem? first = documents.FirstOrDefault(item => !item.Protected);
                if (first is not null) await OpenLiveDocumentCoreAsync(first);
                else SetEmptyWorkspace();
            }
            finally
            {
                await terminalStartup;
            }
        }
        catch (Exception error)
        {
            ShowAlertBanner("작업 폴더 열기 실패: " + error.Message, isError: true);
        }
        finally
        {
            busy = false;
            UpdateUiState();
        }
    }

    private void ClearActiveDocument(string message)
    {
        activeDocumentPath = activeDocumentName = activeLiveSha256 = previewingCheckpointId = "";
        PopulateCheckpoints(default);
        HideBanner();
        documentView.Clear(message);
    }

    private void SetEmptyWorkspace()
    {
        bool protectedOnly = documents.Count != 0;
        ClearActiveDocument(protectedOnly
            ? "보호 경로의 파일은 자동으로 열지 않습니다. 작업용 사본을 이 폴더에 저장하세요."
            : "이 폴더에 HWP·HWPX를 저장하면 여기에 표시됩니다. Ctrl+N으로 빈 HWPX를 만들 수도 있습니다.");
        SetSyncBadge(protectedOnly ? "보호 경로만 있음" : "첫 문서 저장 대기", isWarning: protectedOnly);
    }

    private void PopulateWorkspaceDocuments(JsonElement listing)
    {
        updatingSelectors = true;
        try
        {
            documents.Clear();
            documentsByPath.Clear();
            foreach (JsonElement item in listing.Part("documents").Items())
            {
                var document = new WorkspaceDocumentItem(
                    item.Text("path"),
                    item.Text("name"),
                    item.Text("relative_path", item.Text("name")),
                    item.Flag("protected"));
                documents.Add(document);
                documentsByPath.Add(document.Path, document);
            }
            documentSwitcher.ItemsSource = null;
            documentSwitcher.ItemsSource = documents;
            documentListBox.Items.Clear();
            foreach (WorkspaceDocumentItem doc in documents)
            {
                var panel = new StackPanel { Orientation = Orientation.Horizontal };
                panel.Children.Add(AppIcons.Create(doc.Protected ? AppIconKind.ShieldCheck : AppIconKind.FileText, 14,
                    doc.Protected ? "MutedTextBrush" : "AccentBrush"));
                var label = new TextBlock
                {
                    Text = doc.Protected ? doc.RelativePath + " [보호]" : doc.RelativePath,
                    Margin = new Thickness(6, 0, 0, 0),
                    TextTrimming = TextTrimming.CharacterEllipsis,
                    ToolTip = doc.Path
                };
                if (doc.Protected) label.SetResourceReference(TextBlock.ForegroundProperty, "MutedTextBrush");
                panel.Children.Add(label);
                documentListBox.Items.Add(new ListBoxItem { Content = panel, Tag = doc });
            }
        }
        finally
        {
            updatingSelectors = false;
        }
    }

    private async Task OpenDocumentItemAsync(WorkspaceDocumentItem item)
    {
        if (busy) return;
        if (item.Protected)
        {
            ShowAlertBanner($"보호 경로({item.RelativePath})는 직접 편집·덮어쓰기가 차단되어 있습니다. 별도 작업 위치로 복사한 뒤 여세요.", isError: false);
            SyncDocumentSelectors();
            return;
        }
        if (string.Equals(item.Path, activeDocumentPath, StringComparison.OrdinalIgnoreCase) && string.IsNullOrEmpty(previewingCheckpointId))
            return;

        busy = true;
        UpdateUiState();
        try
        {
            await OpenLiveDocumentCoreAsync(item);
        }
        catch (Exception error)
        {
            SyncDocumentSelectors();
            ShowAlertBanner("문서 열기 실패: " + error.Message, isError: true);
        }
        finally
        {
            busy = false;
            UpdateUiState();
        }
    }

    private async Task OpenLiveDocumentCoreAsync(WorkspaceDocumentItem item)
    {
        JsonElement opened = await host.CallAsync("open_live_document", new { path = item.Path });
        await documentView.ShowAsync(opened.Text("file_path"), opened.Text("sha256"), item.Path, item.Name);
        previewingCheckpointId = "";
        HideBanner();
        activeDocumentPath = opened.Text("path", item.Path);
        activeDocumentName = opened.Text("name", item.Name);
        activeLiveSha256 = opened.Text("sha256");
        PopulateCheckpoints(opened);
        SyncDocumentSelectors();

        SetSyncBadge(IsEditableDocument ? "실시간 동기화 중" : "HWP 읽기 전용 · 실시간", isWarning: false);
    }

    private void SyncDocumentSelectors()
    {
        updatingSelectors = true;
        try
        {
            WorkspaceDocumentItem? match = documents.FirstOrDefault(d => string.Equals(d.Path, activeDocumentPath, StringComparison.OrdinalIgnoreCase));
            documentSwitcher.SelectedItem = match;
            documentListBox.SelectedItem = documentListBox.Items.OfType<ListBoxItem>()
                .FirstOrDefault(li => li.Tag is WorkspaceDocumentItem d && string.Equals(d.Path, activeDocumentPath, StringComparison.OrdinalIgnoreCase));
        }
        finally
        {
            updatingSelectors = false;
        }
    }

    private void PopulateCheckpoints(JsonElement payload)
    {
        updatingSelectors = true;
        try
        {
            originalSha256 = payload.Text("original_sha256");
            checkpoints.Clear();
            JsonElement[] rows = payload.Part("checkpoints").Items();
            for (int i = rows.Length - 1; i >= 0; i--)
            {
                JsonElement row = rows[i];
                long size = row.Part("size").ValueKind == JsonValueKind.Number ? row.Part("size").GetInt64() : 0;
                checkpoints.Add(new CheckpointItem(
                    row.Text("id"),
                    row.Text("sha256"),
                    row.Text("created_at"),
                    row.Text("reason", "auto"),
                    size,
                    IsOriginal: false));
            }
            if (!string.IsNullOrEmpty(originalSha256))
            {
                checkpoints.Add(new CheckpointItem(
                    "original",
                    originalSha256,
                    "최초 불변 백업",
                    "original",
                    0,
                    IsOriginal: true));
            }

            checkpointListBox.Items.Clear();
            foreach (CheckpointItem cp in checkpoints)
            {
                var stack = new StackPanel();
                var top = new DockPanel();
                var badge = new TextBlock
                {
                    Text = ReasonLabel(cp.Reason),
                    FontSize = 10.5,
                    FontWeight = FontWeights.SemiBold,
                    Margin = new Thickness(6, 0, 0, 0)
                };
                badge.SetResourceReference(TextBlock.ForegroundProperty, cp.IsOriginal ? "AccentBrush" : "MutedTextBrush");
                DockPanel.SetDock(badge, Dock.Right);
                top.Children.Add(badge);

                var timeText = new TextBlock
                {
                    Text = FormatTimestamp(cp.CreatedAt),
                    FontSize = 11.5,
                    FontWeight = FontWeights.Medium,
                    TextTrimming = TextTrimming.CharacterEllipsis
                };
                top.Children.Add(timeText);
                stack.Children.Add(top);

                string shortSha = cp.Sha256.Length >= 10 ? cp.Sha256[..10] : cp.Sha256;
                string suffix = string.Equals(cp.Sha256, activeLiveSha256, StringComparison.Ordinal) ? " · 현재" : "";
                var meta = new TextBlock
                {
                    Text = shortSha + suffix,
                    FontSize = 10.5,
                    Margin = new Thickness(0, 2, 0, 0)
                };
                meta.SetResourceReference(TextBlock.ForegroundProperty, "MutedTextBrush");
                stack.Children.Add(meta);

                var item = new ListBoxItem { Content = stack, Tag = cp };
                if (cp.Id == previewingCheckpointId) item.IsSelected = true;
                checkpointListBox.Items.Add(item);
            }
            int count = checkpoints.Count(c => !c.IsOriginal);
            checkpointCountLabel.Text = count > 0 ? $"체크포인트 ({count})" : "체크포인트 타임라인";
        }
        finally
        {
            updatingSelectors = false;
        }
    }

    private bool IsEditableDocument => activeDocumentPath.EndsWith(".hwpx", StringComparison.OrdinalIgnoreCase);

    private async Task ProcessWorkspaceChangesAsync()
    {
        if (busy || closing || host.Broken || liveWatcher?.TakePending() is not { } batch) return;
        busy = true;
        UpdateUiState();
        try
        {
            if (batch.RefreshList || batch.SavedPaths.Any(path => !documentsByPath.ContainsKey(path)))
            {
                PopulateWorkspaceDocuments(await host.CallAsync("list_workspace", new { }));
                SyncDocumentSelectors();
            }
            WorkspaceDocumentItem? newest = null;
            foreach (string path in batch.SavedPaths)
                if (documentsByPath.TryGetValue(path, out var item) && !item.Protected) { newest = item; break; }

            bool history = previewingCheckpointId.Length != 0;
            bool hasActive = documentsByPath.TryGetValue(activeDocumentPath, out var active) && !active.Protected;
            bool switchToSave = newest is not null && !history && (followSaves.IsChecked || activeDocumentPath.Length == 0) &&
                !string.Equals(newest.Path, activeDocumentPath, StringComparison.OrdinalIgnoreCase);
            if (hasActive && (batch.Reconcile || batch.SavedPaths.Contains(activeDocumentPath, StringComparer.OrdinalIgnoreCase)))
                await UpdateLiveSnapshotAsync(display: !switchToSave);
            if (switchToSave)
                await OpenLiveDocumentCoreAsync(newest!);
            else if (!hasActive && activeDocumentPath.Length != 0)
            {
                if (!history) ClearActiveDocument("작업 파일이 이동되었거나 삭제되었습니다. 문서 목록에서 다시 선택하세요.");
                SetSyncBadge("작업 파일 없음", isWarning: true);
            }
            else if (!hasActive && batch.Reconcile && documents.FirstOrDefault(d => !d.Protected) is { } first)
                await OpenLiveDocumentCoreAsync(first);
            else if (newest is not null && !string.Equals(newest.Path, activeDocumentPath, StringComparison.OrdinalIgnoreCase))
                SetSyncBadge("다른 문서 저장 · " + newest.Name, isWarning: false);

            if (batch.Warning is not null)
                SetSyncBadge("감시 알림 · 목록 재확인: " + batch.Warning, isWarning: true);
        }
        catch (Exception error)
        {
            SetSyncBadge("저장본 확인 필요", isWarning: true);
            if (!string.IsNullOrEmpty(previewingCheckpointId)) ShowCheckpointBanner("과거 시점 표시 중 · " + error.Message);
            else ShowAlertBanner("저장본을 표시하지 못했습니다: " + error.Message, isError: true);
        }
        finally
        {
            busy = false;
            UpdateUiState();
        }
    }

    private async Task UpdateLiveSnapshotAsync(bool display = true, bool force = false)
    {
        JsonElement captured = await host.CallAsync("record_live_checkpoint", new { path = activeDocumentPath, reason = "auto" });
        string sha = captured.Text("sha256");
        bool changed = sha != activeLiveSha256;
        activeLiveSha256 = sha;
        if (changed || force) PopulateCheckpoints(captured);
        if (previewingCheckpointId.Length != 0)
        {
            if (changed) SetSyncBadge("작업 파일 갱신 · 과거 시점 표시 중", isWarning: true);
            return;
        }
        if (!display || (!changed && !force && documentView.RenderedPageCount > 0 && documentView.CurrentSha256 == sha)) return;
        await documentView.ShowAsync(captured.Text("file_path"), sha, activeDocumentPath, activeDocumentName, preserveView: true);
        HideBanner();
        SetSyncBadge("자동 갱신 · " + DateTime.Now.ToString("HH:mm:ss"), isWarning: false);
    }

    private async Task RefreshActiveDocumentAsync()
    {
        if (busy || string.IsNullOrWhiteSpace(workspaceRoot)) return;
        busy = true;
        UpdateUiState();
        try
        {
            JsonElement listing = await host.CallAsync("list_workspace", new { });
            PopulateWorkspaceDocuments(listing);
            SyncDocumentSelectors();

            if (documentsByPath.ContainsKey(activeDocumentPath))
            {
                previewingCheckpointId = "";
                HideBanner();
                await UpdateLiveSnapshotAsync(force: true);
                SetSyncBadge("새로고침 · " + DateTime.Now.ToString("HH:mm:ss"), isWarning: false);
            }
            else if (documents.FirstOrDefault(d => !d.Protected) is { } first) await OpenLiveDocumentCoreAsync(first);
            else SetEmptyWorkspace();
        }
        catch (Exception error)
        {
            ShowAlertBanner("새로고침 실패: " + error.Message, isError: true);
        }
        finally
        {
            busy = false;
            UpdateUiState();
        }
    }

    private async Task PreviewCheckpointAsync(CheckpointItem item)
    {
        if (busy || string.IsNullOrEmpty(activeDocumentPath)) return;
        busy = true;
        UpdateUiState();
        try
        {
            JsonElement resolved = await host.CallAsync("read_live_checkpoint", new { path = activeDocumentPath, checkpoint_id = item.Id });
            string sha = resolved.Text("sha256");
            await documentView.ShowAsync(resolved.Text("file_path"), sha, activeDocumentPath,
                $"{activeDocumentName} ({ReasonLabel(item.Reason)})", preserveView: true);

            previewingCheckpointId = item.Id;
            string shortSha = sha[..8];
            ShowCheckpointBanner($"체크포인트 미리보기 중 · {FormatTimestamp(item.CreatedAt)} · {ReasonLabel(item.Reason)} ({shortSha})");
            SetSyncBadge("체크포인트 미리보기", isWarning: true);
        }
        catch (Exception error)
        {
            ShowAlertBanner("체크포인트 미리보기 실패: " + error.Message, isError: true);
        }
        finally
        {
            busy = false;
            UpdateUiState();
        }
    }

    private async Task RestorePreviewedCheckpointAsync()
    {
        if (busy || !IsEditableDocument || string.IsNullOrEmpty(previewingCheckpointId)) return;
        MessageBoxResult answer = MessageBox.Show(
            this,
            "선택한 체크포인트 시점으로 현재 작업 .hwpx 파일을 복원하시겠습니까?\n복원 직전 상태는 자동으로 체크포인트에 백업됩니다.",
            "체크포인트 복원",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question);
        if (answer != MessageBoxResult.Yes) return;

        busy = true;
        UpdateUiState();
        try
        {
            JsonElement restored = await host.CallAsync("restore_live_checkpoint", new { path = activeDocumentPath, checkpoint_id = previewingCheckpointId });
            activeLiveSha256 = restored.Text("sha256");
            previewingCheckpointId = "";
            HideBanner();
            PopulateCheckpoints(restored);
            await documentView.ShowAsync(restored.Text("file_path"), activeLiveSha256, activeDocumentPath, activeDocumentName, preserveView: true);
            SetSyncBadge("복원 완료 · " + DateTime.Now.ToString("HH:mm:ss"), isWarning: false);
        }
        catch (Exception error)
        {
            ShowAlertBanner("체크포인트 복원 실패: " + error.Message, isError: true);
        }
        finally
        {
            busy = false;
            UpdateUiState();
        }
    }

    private async Task ReturnToLiveDocumentAsync()
    {
        if (busy || string.IsNullOrEmpty(activeDocumentPath)) return;
        previewingCheckpointId = "";
        HideBanner();
        updatingSelectors = true;
        checkpointListBox.SelectedItem = null;
        updatingSelectors = false;
        await RefreshActiveDocumentAsync();
    }

    private async Task RecordManualCheckpointAsync()
    {
        if (busy || string.IsNullOrEmpty(activeDocumentPath)) return;
        busy = true;
        UpdateUiState();
        try
        {
            JsonElement result = await host.CallAsync("record_live_checkpoint", new { path = activeDocumentPath, reason = "manual" });
            PopulateCheckpoints(result);
            bool recorded = result.Flag("recorded");
            SetSyncBadge(recorded ? "체크포인트 저장됨" : "변경 없음 (최신 상태)", isWarning: false);
        }
        catch (Exception error)
        {
            ShowAlertBanner("체크포인트 저장 실패: " + error.Message, isError: true);
        }
        finally
        {
            busy = false;
            UpdateUiState();
        }
    }

    private async Task NewBlankDocumentAsync()
    {
        if (busy || string.IsNullOrWhiteSpace(workspaceRoot))
        {
            ShowAlertBanner("먼저 작업 폴더(Ctrl+O)를 여세요.", isError: false);
            return;
        }
        var dialog = new SaveFileDialog
        {
            Title = "새 빈 HWPX 만들기",
            Filter = "HWPX 문서 (*.hwpx)|*.hwpx",
            DefaultExt = ".hwpx",
            InitialDirectory = workspaceRoot,
            FileName = "새문서.hwpx",
            OverwritePrompt = false
        };
        if (dialog.ShowDialog(this) != true || string.IsNullOrWhiteSpace(dialog.FileName)) return;

        busy = true;
        UpdateUiState();
        try
        {
            JsonElement created = await host.CallAsync("new_blank_live_document", new { destination = dialog.FileName });
            JsonElement listing = await host.CallAsync("list_workspace", new { });
            PopulateWorkspaceDocuments(listing);
            string createdPath = created.Text("path", dialog.FileName);
            WorkspaceDocumentItem? match = documents.FirstOrDefault(d => string.Equals(d.Path, createdPath, StringComparison.OrdinalIgnoreCase));
            if (match is not null) await OpenLiveDocumentCoreAsync(match);
        }
        catch (Exception error)
        {
            ShowAlertBanner("새 HWPX 생성 실패: " + error.Message, isError: true);
        }
        finally
        {
            busy = false;
            UpdateUiState();
        }
    }

    private async Task OpenInHancomAsync()
    {
        if (busy || !IsEditableDocument) return;
        try
        {
            await host.CallAsync("open_in_hancom", new { path = activeDocumentPath });
            SetSyncBadge("한글에서 열림 · 저장 시 자동 갱신", isWarning: false);
        }
        catch (Exception error)
        {
            ShowAlertBanner("한글에서 열기 실패: " + error.Message, isError: true);
        }
    }

    private async Task ExportDocumentAsync()
    {
        if (busy || !IsEditableDocument) return;
        string stem = Path.GetFileNameWithoutExtension(activeDocumentName);
        string suffix = string.IsNullOrEmpty(previewingCheckpointId) ? "-export" : "-" + previewingCheckpointId;
        var dialog = new SaveFileDialog
        {
            Title = "HWPX 내보내기 (새 파일)",
            Filter = "HWPX 문서 (*.hwpx)|*.hwpx",
            DefaultExt = ".hwpx",
            InitialDirectory = workspaceRoot,
            FileName = stem + suffix + ".hwpx",
            OverwritePrompt = false
        };
        if (dialog.ShowDialog(this) != true || string.IsNullOrWhiteSpace(dialog.FileName)) return;
        try
        {
            JsonElement exported = await host.CallAsync("export_live_document", new
            {
                path = activeDocumentPath,
                destination = dialog.FileName,
                checkpoint_id = previewingCheckpointId
            });
            SetSyncBadge("내보내기 완료: " + Path.GetFileName(exported.Text("path", dialog.FileName)), isWarning: false);
        }
        catch (Exception error)
        {
            ShowAlertBanner("내보내기 실패: " + error.Message, isError: true);
        }
    }

    private async Task ForwardPassageToOmpAsync(DocumentPassageTarget target)
    {
        string excerpt = target.Text.Replace("\r", " ").Replace("\n", " ").Trim();
        if (excerpt.Length > 240) excerpt = excerpt[..240] + "…";
        string docLabel = string.IsNullOrEmpty(activeDocumentName) ? "문서" : activeDocumentName;
        string reference = $"[{docLabel} · {target.Page}쪽 구역 {target.SectionIndex} 문단 {target.ParagraphIndex}] \"{excerpt}\"";

        if (ompTerminal.IsRunning)
        {
            try
            {
                await ompTerminal.PasteDraftAsync(reference);
                SetSyncBadge("문단 참조를 OMP로 전달함", isWarning: false);
                return;
            }
            catch
            {
                // Fall through to clipboard copy if terminal bracketed paste is not ready.
            }
        }
        try
        {
            Clipboard.SetText(reference);
            SetSyncBadge("문단 참조를 클립보드에 복사함", isWarning: false);
        }
        catch (Exception error)
        {
            ShowAlertBanner("문단 참조 전달 실패: " + error.Message, isError: true);
        }
    }

    private async Task StartOmpAsync()
    {
        if (ompTerminal.IsRunning || ompTerminal.IsStarting) return;
        try
        {
            await StartOmpCoreAsync();
        }
        catch (Exception error)
        {
            ShowAlertBanner("OMP 시작 실패: " + error.Message, isError: true);
        }
        finally
        {
            UpdateOmpStatus();
        }
    }

    private async Task StartOmpCoreAsync()
    {
        if (ompTerminal.IsRunning || ompTerminal.IsStarting) return;
        if (activeOmpLaunchId is not null)
        {
            await host.CallAsync("release_omp", new { launch_id = activeOmpLaunchId });
            activeOmpLaunchId = null;
        }
        ompStateText.Text = "OMP 시작 중…";
        JsonElement launch = await host.CallAsync("prepare_omp", new { });
        activeOmpLaunchId = launch.Text("launch_id");
        try
        {
            await ompTerminal.StartAsync(launch);
        }
        catch
        {
            if (!ompTerminal.IsRunning && activeOmpLaunchId is not null)
            {
                try { await host.CallAsync("release_omp", new { launch_id = activeOmpLaunchId }); } catch { }
                activeOmpLaunchId = null;
            }
            throw;
        }
        UpdateOmpStatus();
    }

    private async Task StopOmpAsync()
    {
        try
        {
            await StopOmpCoreAsync();
        }
        catch (Exception error)
        {
            ShowAlertBanner("OMP 종료 실패: " + error.Message, isError: true);
        }
        finally
        {
            UpdateOmpStatus();
        }
    }

    private async Task StopOmpCoreAsync()
    {
        if (ompTerminal.IsRunning)
            await ompTerminal.ShutdownAgentAsync();
        if (activeOmpLaunchId is not null && !ompTerminal.IsRunning)
        {
            await host.CallAsync("release_omp", new { launch_id = activeOmpLaunchId });
            activeOmpLaunchId = null;
        }
    }

    private async Task RestartOmpAsync()
    {
        if (ompTerminal.IsRunning)
        {
            if (!ompTerminal.AgentState.IsIdle)
            {
                ShowAlertBanner("OMP 작업이 실행 중입니다. 먼저 중단한 뒤 다시 시작하세요.", isError: false);
                return;
            }
            await StopOmpAsync();
        }
        await StartOmpAsync();
    }

    private async Task HandleOmpActionButtonAsync()
    {
        if (ompTerminal.IsRunning && !ompTerminal.AgentState.IsIdle)
        {
            try { await ompTerminal.AbortAgentAsync(); }
            catch (Exception error) { ShowAlertBanner("OMP 중단 요청 실패: " + error.Message, isError: true); }
        }
        else if (!ompTerminal.IsRunning && !ompTerminal.IsStarting)
        {
            await StartOmpAsync();
        }
    }

    private void UpdateOmpStatus()
    {
        OmpAgentState state = ompTerminal.AgentState;
        if (ompTerminal.IsStarting)
        {
            ompStateText.Text = "OMP 시작 중…";
            ompActionButton.Visibility = Visibility.Collapsed;
            return;
        }
        if (!ompTerminal.IsRunning)
        {
            ompStateText.Text = "OMP 종료됨";
            ompActionButton.Content = "OMP 시작";
            ompActionButton.Visibility = Visibility.Visible;
            if (activeOmpLaunchId is not null)
            {
                string releaseId = activeOmpLaunchId;
                activeOmpLaunchId = null;
                _ = ReleaseOmpQuietlyAsync(releaseId);
            }
            return;
        }
        string model = string.IsNullOrWhiteSpace(state.Model) ? "OMP" : state.Model;
        ompStateText.Text = state.IsIdle ? $"OMP · {model}" : $"OMP 실행 중 · {model}";
        ompStateText.ToolTip = string.IsNullOrWhiteSpace(state.Detail) ? ompStateText.Text : state.Detail;
        if (!state.IsIdle && state.Connected)
        {
            ompActionButton.Content = "중단";
            ompActionButton.Visibility = Visibility.Visible;
        }
        else
        {
            ompActionButton.Visibility = Visibility.Collapsed;
        }
    }

    private async Task ReleaseOmpQuietlyAsync(string launchId)
    {
        try { await host.CallAsync("release_omp", new { launch_id = launchId }); } catch { }
    }

    private async Task LaunchLegacyToolsAsync()
    {
        try { await host.CallAsync("legacy_tools", new { }); }
        catch (Exception error) { ShowAlertBanner("기존 HWP 도구를 열지 못했습니다: " + error.Message, isError: true); }
    }

    private async Task LaunchSetupRequirementsAsync()
    {
        try { await host.CallAsync("setup_requirements", new { }); }
        catch (Exception error) { ShowAlertBanner("필수 도구 창 열기 실패: " + error.Message, isError: true); }
    }

    private async Task ShowLegalNoticeAsync()
    {
        try
        {
            JsonElement legal = await host.CallAsync("legal", new { });
            var dialog = new Window
            {
                Title = "법적 고지 — HWP Editor " + legal.Text("version"),
                Owner = this,
                Width = 680,
                Height = 520,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Content = new TextBox
                {
                    Text = legal.Text("text"),
                    IsReadOnly = true,
                    TextWrapping = TextWrapping.Wrap,
                    VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                    Padding = new Thickness(12),
                    BorderThickness = new Thickness(0)
                }
            };
            dialog.ShowDialog();
        }
        catch (Exception error)
        {
            ShowAlertBanner("법적 고지 로드 실패: " + error.Message, isError: true);
        }
    }

    private async void OnWindowClosing(object? sender, CancelEventArgs args)
    {
        if (closingConfirmed) return;
        args.Cancel = true;
        if (closing || busy) return;
        closing = true;
        try
        {
            await StopOmpCoreAsync();
            await ompTerminal.DisposeAsync();
            documentView.Dispose();
            liveWatcher?.Dispose();
            liveWatcher = null;
            if (!host.Broken) await host.CallAsync("shutdown", new { });
        }
        catch (Exception error)
        {
            MessageBoxResult choice = MessageBox.Show(
                this,
                "종료 정리 중 확인이 필요합니다:\n" + error.Message + "\n\n창을 그대로 닫으시겠습니까? (실행 중인 외부 프로세스는 강제 종료하지 않습니다.)",
                "종료 확인",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning);
            if (choice != MessageBoxResult.Yes)
            {
                closing = false;
                UpdateUiState();
                return;
            }
        }
        closingConfirmed = true;
        Close();
    }

    private void UpdateUiState()
    {
        bool hasWorkspace = !string.IsNullOrWhiteSpace(workspaceRoot);
        bool hasDoc = !string.IsNullOrWhiteSpace(activeDocumentPath);
        openWorkspaceButton.IsEnabled = !busy;
        documentSwitcher.IsEnabled = !busy && documents.Count > 0;
        openInHancomButton.IsEnabled = !busy && IsEditableDocument;
        refreshButton.IsEnabled = !busy && hasWorkspace;
        exportButton.IsEnabled = !busy && IsEditableDocument;
        manualCheckpointButton.IsEnabled = !busy && hasDoc;
        UpdateOmpStatus();
        if (!busy && !closing) _ = ProcessWorkspaceChangesAsync();
    }

    private void SetSyncBadge(string text, bool isWarning)
    {
        syncBadgeText.Text = text;
        syncBadgeText.ToolTip = text;
        syncBadge.SetResourceReference(Border.BackgroundProperty, isWarning ? "WarningBgBrush" : "SoftAccentBrush");
        syncBadgeText.SetResourceReference(TextBlock.ForegroundProperty, isWarning ? "WarningTextBrush" : "AccentBrush");
    }

    private void ShowCheckpointBanner(string message)
    {
        previewBannerText.Text = message;
        previewBanner.SetResourceReference(Border.BackgroundProperty, "WarningBgBrush");
        previewBannerText.SetResourceReference(TextBlock.ForegroundProperty, "WarningTextBrush");
        restoreBannerButton.Visibility = IsEditableDocument ? Visibility.Visible : Visibility.Collapsed;
        returnLiveBannerButton.Visibility = Visibility.Visible;
        dismissBannerButton.Visibility = Visibility.Collapsed;
        previewBanner.Visibility = Visibility.Visible;
    }

    private void ShowAlertBanner(string message, bool isError)
    {
        previewBannerText.Text = message;
        previewBanner.SetResourceReference(Border.BackgroundProperty, isError ? "ErrorBgBrush" : "WarningBgBrush");
        previewBannerText.SetResourceReference(TextBlock.ForegroundProperty, isError ? "ErrorTextBrush" : "WarningTextBrush");
        restoreBannerButton.Visibility = Visibility.Collapsed;
        returnLiveBannerButton.Visibility = Visibility.Collapsed;
        dismissBannerButton.Visibility = Visibility.Visible;
        previewBanner.Visibility = Visibility.Visible;
    }

    private void HideBanner()
    {
        previewBanner.Visibility = Visibility.Collapsed;
    }

    private MenuItem CreateMenuItem(string header, string gesture, Func<Task> action)
    {
        var item = new MenuItem
        {
            Style = (Style)FindResource("ToolsMenuItem"),
            Header = header,
            InputGestureText = gesture
        };
        item.Click += async (_, _) => await action();
        return item;
    }

    private static StackPanel IconLabel(AppIconKind kind, string text, string brushKey)
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        panel.Children.Add(AppIcons.Create(kind, 15, brushKey));
        panel.Children.Add(new TextBlock { Text = text, Margin = new Thickness(5, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center });
        return panel;
    }

    private static string ReasonLabel(string reason) => reason switch
    {
        "original" => "원본",
        "initial" => "최초",
        "auto" => "자동",
        "manual" => "수동",
        "pre-restore" => "복원 전",
        "restore" => "복원",
        _ => reason
    };

    private static string FormatTimestamp(string value)
    {
        if (DateTimeOffset.TryParse(value, out DateTimeOffset dto))
            return dto.ToLocalTime().ToString("MM-dd HH:mm:ss");
        return value;
    }
}
