using System.ComponentModel;
using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace HWPReview.Native;

// Local filename discovery only. The explicit choice callback owns all material admission.
internal sealed class SourceMentionCompletion : IDisposable
{
    private const int MaxFiles = 8000, MaxRoots = 16, MaxRows = 30;
    private const FileAttributes Excluded = FileAttributes.ReparsePoint | FileAttributes.Hidden | FileAttributes.System;
    private static readonly HashSet<string> Extensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
        ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log", ".sql",
        ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".html", ".htm",
        ".css", ".scss", ".sass", ".less", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
        ".cs", ".csx", ".fs", ".fsx", ".vb", ".java", ".kt", ".kts", ".go", ".rs",
        ".rb", ".php", ".swift", ".m", ".mm", ".sh", ".bash", ".zsh", ".ps1", ".psm1",
        ".psd1", ".bat", ".cmd", ".r", ".lua", ".pl", ".dart", ".vue", ".svelte", ".ex", ".exs",
        ".xaml", ".csproj", ".fsproj", ".vbproj", ".props", ".targets", ".sln", ".tex", ".bib",
        ".cmake", ".gradle", ".properties", ".editorconfig", ".gitignore", ".gitattributes", ".hwpx"
    };
    private static readonly HashSet<string> TextNames = new(StringComparer.OrdinalIgnoreCase)
    { "README", "LICENSE", "NOTICE", "CHANGELOG", "Makefile", "Dockerfile", "Gemfile", "Rakefile", "CMakeLists.txt" };
    private static readonly DependencyPropertyDescriptor ReadOnlyDescriptor =
        DependencyPropertyDescriptor.FromProperty(TextBoxBase.IsReadOnlyProperty, typeof(TextBox));
    private readonly Window owner;
    private readonly Func<string, Task<string?>> attachSourceAsync;
    private readonly Func<bool> canAttach;
    private readonly Action<Exception> showError;
    private readonly Dictionary<TextBox, EditorState> editors = new();
    private readonly HashSet<string> roots = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, FileEntry> files = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<Match> matches = new(MaxRows);
    private readonly Row[] rows = new Row[MaxRows];
    private readonly Popup popup;
    private readonly ListBox list;
    private readonly TextBlock status;
    private readonly Button folderButton, fileButton;
    private readonly CancellationTokenSource lifetime = new();
    private readonly SemaphoreSlim discoveryGate = new(1, 1);
    private TextBox? active;
    private Token token;
    private string cachedQuery = "";
    private int catalogVersion, matchedVersion = -1, epoch, pendingScans, workspaceGeneration;
    private bool disposed, choosing, picking, suppressEvents, truncated;

    public SourceMentionCompletion(Window owner, Func<string, Task<string?>> attachSourceAsync,
        Func<bool> canAttach, Action<Exception> showError)
    {
        this.owner = owner;
        this.attachSourceAsync = attachSourceAsync;
        this.canAttach = canAttach;
        this.showError = showError;
        list = new ListBox
        {
            Focusable = false, IsTabStop = false, BorderThickness = new Thickness(0),
            MaxHeight = 330, HorizontalContentAlignment = HorizontalAlignment.Stretch
        };
        ScrollViewer.SetHorizontalScrollBarVisibility(list, ScrollBarVisibility.Disabled);
        ScrollViewer.SetVerticalScrollBarVisibility(list, ScrollBarVisibility.Auto);
        Name(list, "SourceMentionList", "자료 파일 후보");
        AutomationProperties.SetHelpText(list, "위아래 화살표로 이동, Tab 또는 Enter로 자료 추가, Esc로 닫기");
        for (int i = 0; i < rows.Length; i++)
        {
            var row = new Row();
            rows[i] = row;
            Name(row.Item, "SourceMentionCandidate" + i, "자료 후보");
            row.Item.PreviewMouseLeftButtonDown += async (_, args) =>
            {
                args.Handled = true;
                if (row.Entry is not null) await ChooseAsync(row.Entry);
            };
        }
        status = new TextBlock { Margin = new Thickness(8, 5, 8, 3), FontSize = 11, TextWrapping = TextWrapping.Wrap };
        Name(status, "SourceMentionStatus", "자료 자동 완성 안내");
        AutomationProperties.SetLiveSetting(status, AutomationLiveSetting.Polite);
        folderButton = PickerButton("폴더 연결", "SourceMentionAddRoot", "폴더 연결 · Ctrl+Shift+O");
        fileButton = PickerButton("파일 선택", "SourceMentionChooseFile", "파일 선택 · Ctrl+Shift+F");
        folderButton.Click += (_, _) => PickFolder();
        fileButton.Click += async (_, _) => await PickFileAsync();
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(6, 0, 6, 4) };
        actions.Children.Add(folderButton); actions.Children.Add(fileButton);
        var body = new DockPanel();
        DockPanel.SetDock(actions, Dock.Bottom); body.Children.Add(actions);
        DockPanel.SetDock(status, Dock.Bottom); body.Children.Add(status);
        body.Children.Add(list);
        var border = new Border
        {
            Width = 460, MaxWidth = Math.Max(220, SystemParameters.WorkArea.Width - 24),
            BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8), Padding = new Thickness(3),
            Child = body, UseLayoutRounding = true, SnapsToDevicePixels = true
        };
        Name(border, "SourceMentionPopup", "자료 파일 자동 완성");
        popup = new Popup
        {
            Child = border, Placement = PlacementMode.Bottom, StaysOpen = true,
            AllowsTransparency = true, Focusable = false, PopupAnimation = PopupAnimation.None
        };
        ApplyColors();
        owner.Deactivated += OwnerDeactivated;
        owner.Closed += OwnerClosed;
        owner.LocationChanged += OwnerMoved;
        owner.SizeChanged += OwnerResized;
        owner.PreviewMouseDown += OwnerMouseDown;
        SystemParameters.StaticPropertyChanged += SystemSettingsChanged;
    }

    public void Attach(TextBox editor)
    {
        owner.Dispatcher.VerifyAccess();
        if (disposed || editors.ContainsKey(editor)) return;
        editors.Add(editor, new EditorState());
        editor.PreviewTextInput += EditorTextInput;
        editor.PreviewKeyDown += EditorKeyDown;
        editor.TextChanged += EditorTextChanged;
        editor.SelectionChanged += EditorSelectionChanged;
        editor.LostKeyboardFocus += EditorLostFocus;
        editor.IsEnabledChanged += EditorAvailabilityChanged;
        editor.IsVisibleChanged += EditorAvailabilityChanged;
        editor.Unloaded += EditorUnloaded;
        editor.AddHandler(ScrollViewer.ScrollChangedEvent, new ScrollChangedEventHandler(EditorScrolled));
        ReadOnlyDescriptor.AddValueChanged(editor, EditorReadOnlyChanged);
        DataObject.AddPastingHandler(editor, EditorPasting);
    }

    public void Detach(TextBox editor)
    {
        owner.Dispatcher.VerifyAccess();
        if (!editors.Remove(editor)) return;
        if (active == editor) Close();
        editor.PreviewTextInput -= EditorTextInput;
        editor.PreviewKeyDown -= EditorKeyDown;
        editor.TextChanged -= EditorTextChanged;
        editor.SelectionChanged -= EditorSelectionChanged;
        editor.LostKeyboardFocus -= EditorLostFocus;
        editor.IsEnabledChanged -= EditorAvailabilityChanged;
        editor.IsVisibleChanged -= EditorAvailabilityChanged;
        editor.Unloaded -= EditorUnloaded;
        editor.RemoveHandler(ScrollViewer.ScrollChangedEvent, new ScrollChangedEventHandler(EditorScrolled));
        ReadOnlyDescriptor.RemoveValueChanged(editor, EditorReadOnlyChanged);
        DataObject.RemovePastingHandler(editor, EditorPasting);
    }

    public void AddRoot(string directory)
    {
        owner.Dispatcher.VerifyAccess();
        if (disposed) return;
        try
        {
            string path = Path.TrimEndingDirectorySeparator(Path.GetFullPath(directory));
            if (string.Equals(path, Path.TrimEndingDirectorySeparator(Path.GetPathRoot(path)!), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("드라이브 전체 대신 자료가 있는 폴더를 선택하세요.");
            if (roots.Contains(path)) return;
            if (roots.Count == MaxRoots) throw new InvalidOperationException("자료 폴더는 한 번에 16개까지 연결할 수 있습니다.");
            roots.Add(path);
            _ = DiscoverAsync(path, true);
        }
        catch (Exception error) when (IsPathError(error)) { showError(error); }
    }

    public void RegisterFile(string path)
    {
        owner.Dispatcher.VerifyAccess();
        if (disposed) return;
        try
        {
            string fullPath = Path.GetFullPath(path);
            if (!Supported(fullPath, explicitFile: true) || files.ContainsKey(fullPath)) return;
            _ = DiscoverAsync(fullPath, false);
        }
        catch (Exception error) when (IsPathError(error)) { showError(error); }
    }

    public bool IsFileDialogOpen => picking;

    // Close invalidates pending insertion, but keeps explicitly selected roots for this app run.
    public void Close()
    {
        epoch++;
        popup.IsOpen = false;
        popup.PlacementTarget = null;
        active = null;
    }

    public void SetWorkspace(string directory)
    {
        owner.Dispatcher.VerifyAccess();
        Close(); workspaceGeneration++;
        roots.Clear(); files.Clear(); matches.Clear(); list.Items.Clear();
        truncated = false; catalogVersion++; matchedVersion = -1;
        AddRoot(directory);
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        Close(); lifetime.Cancel();
        foreach (var editor in editors.Keys.ToArray()) Detach(editor);
        owner.Deactivated -= OwnerDeactivated;
        owner.Closed -= OwnerClosed;
        owner.LocationChanged -= OwnerMoved;
        owner.SizeChanged -= OwnerResized;
        owner.PreviewMouseDown -= OwnerMouseDown;
        SystemParameters.StaticPropertyChanged -= SystemSettingsChanged;
        files.Clear(); roots.Clear(); list.Items.Clear();
        // Discovery may still be unwinding: do not dispose synchronization primitives under it.
    }

    private void MarkUserEdit(TextBox editor)
    {
        if (!editors.TryGetValue(editor, out var state)) return;
        state.UserEdit = true;
        _ = owner.Dispatcher.InvokeAsync(() => state.UserEdit = false, DispatcherPriority.Background);
    }

    private void EditorTextInput(object sender, TextCompositionEventArgs args) => MarkUserEdit((TextBox)sender);
    private void EditorPasting(object sender, DataObjectPastingEventArgs args) => MarkUserEdit((TextBox)sender);
    private void EditorTextChanged(object sender, TextChangedEventArgs args)
    {
        var editor = (TextBox)sender;
        if (!editors.TryGetValue(editor, out var state)) return;
        state.Revision++;
        if (suppressEvents) return;
        // Programmatic document/memo loading never opens completion.
        if (state.UserEdit) Update(editor, true);
        else if (active == editor) Close();
    }

    private async void EditorKeyDown(object sender, KeyEventArgs args)
    {
        var editor = (TextBox)sender;
        if (args.Key is Key.Back or Key.Delete) MarkUserEdit(editor);
        if (!popup.IsOpen || active != editor || choosing) return;
        if (!Available(editor)) { Close(); return; }
        var modifiers = Keyboard.Modifiers;
        if (args.Key == Key.Escape) { Close(); args.Handled = true; return; }
        if (modifiers == (ModifierKeys.Control | ModifierKeys.Shift))
        {
            if (args.Key == Key.O) { args.Handled = true; PickFolder(); }
            else if (args.Key == Key.F) { args.Handled = true; await PickFileAsync(); }
            return;
        }
        if (modifiers != ModifierKeys.None) return;
        if (list.Items.Count > 0 && args.Key is Key.Up or Key.Down)
        {
            int offset = args.Key == Key.Up ? -1 : 1;
            list.SelectedIndex = (list.SelectedIndex + offset + list.Items.Count) % list.Items.Count;
            list.ScrollIntoView(list.SelectedItem);
            args.Handled = true;
        }
        else if (args.Key is Key.Tab or Key.Enter && list.SelectedItem is ListBoxItem { Tag: FileEntry entry })
        {
            args.Handled = true;
            await ChooseAsync(entry);
        }
    }

    private void EditorSelectionChanged(object sender, RoutedEventArgs args)
    {
        if (editors.TryGetValue((TextBox)sender, out var state)) state.SelectionRevision++;
        if (!suppressEvents && !picking && active == sender && popup.IsOpen) Update((TextBox)sender, false);
    }
    private void EditorLostFocus(object sender, KeyboardFocusChangedEventArgs args)
    { if (active == sender && !picking) Close(); }
    private void EditorAvailabilityChanged(object sender, DependencyPropertyChangedEventArgs args) => CheckAvailability(sender);
    private void EditorReadOnlyChanged(object? sender, EventArgs args) => CheckAvailability(sender);
    private void CheckAvailability(object? sender)
    { if (!picking && active == sender && !Available((TextBox)sender!)) Close(); }
    private void EditorUnloaded(object sender, RoutedEventArgs args) { if (active == sender) Close(); }
    private void EditorScrolled(object sender, ScrollChangedEventArgs args) { if (active == sender) PositionPopup(); }
    private void OwnerDeactivated(object? sender, EventArgs args)
    { if (picking) popup.IsOpen = false; else Close(); }
    private void OwnerClosed(object? sender, EventArgs args) => Dispose();
    private void OwnerMoved(object? sender, EventArgs args) => Close();
    private void OwnerResized(object sender, SizeChangedEventArgs args) => Close();
    private void OwnerMouseDown(object sender, MouseButtonEventArgs args) { if (active is not null && !active.IsMouseOver) Close(); }
    private void SystemSettingsChanged(object? sender, PropertyChangedEventArgs args)
    {
        if (disposed) return;
        if (owner.Dispatcher.CheckAccess()) ApplyColors();
        else _ = owner.Dispatcher.InvokeAsync(() => { if (!disposed) ApplyColors(); });
    }

    private bool Available(TextBox editor) => !disposed && !choosing && owner.IsActive && editor.IsEnabled &&
        editor.IsVisible && !editor.IsReadOnly && editor.IsKeyboardFocused && editors.ContainsKey(editor) && canAttach();

    private void Update(TextBox editor, bool allowOpen)
    {
        if (picking || choosing) return;
        if (!Available(editor) || (!allowOpen && active != editor) || !TryToken(editor, out var current))
        { if (active == editor) Close(); return; }
        active = editor; token = current;
        var query = editor.Text.AsSpan(current.Start + 1, current.Length - 1);
        bool queryChanged = !query.Equals(cachedQuery.AsSpan(), StringComparison.OrdinalIgnoreCase);
        if (matchedVersion != catalogVersion || queryChanged)
        {
            if (queryChanged) cachedQuery = query.ToString();
            RefreshMatches();
            UpdateStatus();
        }
        PositionPopup();
    }

    private void PositionPopup()
    {
        if (active is null || picking) return;
        if (!Available(active)) { Close(); return; }
        Rect caret = active.GetRectFromCharacterIndex(active.CaretIndex, true);
        if (caret.IsEmpty || caret.Bottom < 0 || caret.Top > active.ActualHeight) { Close(); return; }
        // TextBox character rectangles and Popup target rectangles are both WPF DIPs.
        popup.PlacementTarget = active;
        popup.PlacementRectangle = new Rect(caret.X, caret.Y, Math.Max(1, caret.Width), Math.Max(1, caret.Height));
        popup.HorizontalOffset = 0;
        popup.VerticalOffset = 3;
        popup.IsOpen = true;
    }

    private static bool TryToken(TextBox editor, out Token result)
    {
        result = default;
        if (editor.SelectionLength != 0) return false;
        string text = editor.Text;
        int caret = editor.CaretIndex, start = caret;
        while (start > 0 && !TokenBoundary(text[start - 1])) start--;
        if (start >= text.Length || text[start] != '@' || caret <= start) return false;
        if (start > 0 && text[start - 1] is '"' or '\'') return false;
        bool quoted = false;
        for (int i = start - 1; i >= 0 && text[i] is not '\r' and not '\n'; i--)
            if (text[i] == '"' && (i == 0 || text[i - 1] != '\\')) quoted = !quoted;
        if (quoted) return false;
        int end = caret;
        while (end < text.Length && !TokenBoundary(text[end])) end++;
        if (end < text.Length && text[end] == '"') return false;
        result = new Token(start, end - start);
        return true;
    }
    private static bool TokenBoundary(char c) => char.IsWhiteSpace(c) || char.IsControl(c) || c is '"' or '\'' or '(' or ')' or '[' or ']' or '{' or '}' or ',' or ';';

    private void RefreshMatches()
    {
        FileEntry? previous = (list.SelectedItem as ListBoxItem)?.Tag as FileEntry;
        matches.Clear();
        foreach (var entry in files.Values)
        {
            int score = Score(entry.Name, cachedQuery);
            if (score >= 0) score += 1000;
            else score = Score(entry.Path, cachedQuery);
            if (score < 0) continue;
            int position = 0;
            while (position < matches.Count && Compare(matches[position], entry, score) <= 0) position++;
            if (position >= MaxRows) continue;
            matches.Insert(position, new Match(entry, score));
            if (matches.Count > MaxRows) matches.RemoveAt(MaxRows);
        }
        list.Items.Clear();
        int selected = 0;
        for (int i = 0; i < matches.Count; i++)
        {
            var row = rows[i]; var entry = matches[i].Entry;
            row.Entry = entry; row.Title.Text = entry.Name; row.Folder.Text = entry.Folder;
            row.Item.Tag = entry; row.Item.ToolTip = entry.Path;
            AutomationProperties.SetName(row.Item, entry.Name + " · " + entry.Folder);
            list.Items.Add(row.Item);
            if (entry == previous) selected = i;
        }
        list.SelectedIndex = list.Items.Count == 0 ? -1 : selected;
        matchedVersion = catalogVersion;
    }

    private static int Compare(Match match, FileEntry entry, int score)
    {
        int order = score.CompareTo(match.Score);
        return order != 0 ? order : StringComparer.OrdinalIgnoreCase.Compare(match.Entry.Path, entry.Path);
    }

    private static int Score(string value, string query)
    {
        if (query.Length == 0) return 0;
        int next = 0, first = -1, previous = -2, score = 0;
        for (int i = 0; i < value.Length && next < query.Length; i++)
        {
            if (char.ToUpperInvariant(value[i]) != char.ToUpperInvariant(query[next])) continue;
            if (first < 0) first = i;
            score += previous == i - 1 ? 8 : 1;
            if (i == 0 || value[i - 1] is '/' or '\\' or '_' or '-' or ' ') score += 6;
            previous = i; next++;
        }
        return next == query.Length ? 100000 + score - first - (value.Length - query.Length) / 4 : -1;
    }

    private void UpdateStatus()
    {
        status.Text = (pendingScans > 0 ? "파일 이름을 찾는 중 · " : "") +
            (list.Items.Count == 0 ? "후보 없음 · 폴더 연결 또는 파일 선택" : list.Items.Count + "개 후보 · ↑↓ 이동 · Tab/Enter 추가 · Esc 닫기") +
            (truncated ? "\n탐색 한도에 도달했습니다. 더 작은 폴더를 연결하거나 파일을 직접 선택하세요." : "") +
            "\nCtrl+Shift+O 폴더 · Ctrl+Shift+F 파일 · 선택은 로컬 자료 준비만 하며 전송하지 않습니다.";
    }

    private Choice? CaptureChoice()
    {
        if (active is null || !Available(active) || !TryToken(active, out var current) || current != token) return null;
        var state = editors[active];
        return new Choice(active, state, current, active.Text.Substring(current.Start, current.Length),
            state.Revision, state.SelectionRevision, active.CaretIndex, epoch);
    }

    private bool StillValid(Choice choice) => !disposed && choice.Epoch == epoch && editors.TryGetValue(choice.Editor, out var state) &&
        ReferenceEquals(state, choice.State) && state.Revision == choice.Revision && state.SelectionRevision == choice.SelectionRevision &&
        choice.Editor.IsEnabled && choice.Editor.IsVisible && !choice.Editor.IsReadOnly &&
        choice.Editor.CaretIndex == choice.Caret && choice.Editor.SelectionLength == 0 &&
        choice.Token.Start + choice.Token.Length <= choice.Editor.Text.Length &&
        choice.Editor.Text.AsSpan(choice.Token.Start, choice.Token.Length).SequenceEqual(choice.Original.AsSpan());

    private async Task ChooseAsync(FileEntry entry)
    {
        var choice = CaptureChoice();
        if (choice is null) return;
        await AttachChoiceAsync(choice, entry.Path);
    }

    private async Task AttachChoiceAsync(Choice choice, string path)
    {
        if (!StillValid(choice) || choosing || !canAttach()) return;
        choosing = true;
        popup.IsOpen = false;
        active = null;
        popup.PlacementTarget = null;
        try
        {
            string? displayName = await attachSourceAsync(path);
            if (displayName is null || !StillValid(choice)) return;
            if (displayName.Length == 0 || displayName.Any(c => c is '"' or '\'' || char.IsControl(c)) ||
                displayName.IndexOf('/') >= 0 || displayName.IndexOf('\\') >= 0)
                throw new InvalidOperationException("자료 이름에 따옴표, 제어 문자 또는 경로 구분자가 있어 삽입할 수 없습니다.");
            string mention = "@\"" + displayName + "\"";
            suppressEvents = true;
            try
            {
                choice.Editor.Select(choice.Token.Start, choice.Token.Length);
                choice.Editor.SelectedText = mention;
                choice.Editor.CaretIndex = choice.Token.Start + mention.Length;
                choice.Editor.SelectionLength = 0;
            }
            finally { suppressEvents = false; }
        }
        catch (Exception error) { if (!disposed) showError(error); }
        finally { choosing = false; Close(); }
    }

    private void PickFolder()
    {
        var choice = CaptureChoice();
        if (choice is null || picking) return;
        popup.IsOpen = false;
        var dialog = new OpenFolderDialog { Title = "자료가 있는 폴더 연결", Multiselect = false };
        bool selected;
        picking = true;
        try { selected = dialog.ShowDialog(owner) == true; }
        catch (Exception error) { if (!disposed) showError(error); return; }
        finally { picking = false; }
        if (disposed) return;
        if (selected) AddRoot(dialog.FolderName);
        ResumeAfterDialog(choice);
    }

    private async Task PickFileAsync()
    {
        var choice = CaptureChoice();
        if (choice is null || picking) return;
        popup.IsOpen = false;
        var dialog = new OpenFileDialog
        {
            Title = "자료 파일 선택 · UTF-8 텍스트/코드 또는 HWPX", Multiselect = false, CheckFileExists = true,
            Filter = "UTF-8 텍스트/코드 및 HWPX|" + string.Join(";", Extensions.Select(ext => "*" + ext)) + "|모든 파일|*.*"
        };
        bool selected;
        picking = true;
        try { selected = dialog.ShowDialog(owner) == true; }
        catch (Exception error) { if (!disposed) showError(error); return; }
        finally { picking = false; }
        if (disposed) return;
        var resumed = ResumeAfterDialog(choice);
        if (!selected || resumed is null) return;
        if (!Supported(dialog.FileName, explicitFile: true))
        { showError(new InvalidOperationException("UTF-8 텍스트/코드 또는 HWPX 파일만 자료로 선택할 수 있습니다.")); return; }
        RegisterFile(dialog.FileName);
        await AttachChoiceAsync(resumed, dialog.FileName);
    }

    private Choice? ResumeAfterDialog(Choice choice)
    {
        if (!StillValid(choice) || !owner.IsActive || !canAttach()) return null;
        choice.Editor.Focus();
        Update(choice.Editor, true);
        return CaptureChoice();
    }

    private async Task DiscoverAsync(string path, bool directory)
    {
        pendingScans++; UpdateStatus();
        int generation = workspaceGeneration;
        CancellationToken cancellation = lifetime.Token;
        try
        {
            await discoveryGate.WaitAsync(cancellation);
            List<FileEntry> found;
            bool limited;
            try
            {
                (found, limited) = await Task.Run(() => Discover(path, directory, cancellation), cancellation);
            }
            finally { discoveryGate.Release(); }
            if (disposed || generation != workspaceGeneration) return;
            foreach (var entry in found)
            {
                if (files.Count >= MaxFiles) { truncated = true; break; }
                if (files.TryAdd(entry.Path, entry)) catalogVersion++;
            }
            truncated |= limited;
            if (active is not null) Update(active, false);
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (IsPathError(error))
        {
            if (generation != workspaceGeneration) return;
            if (directory) roots.Remove(path);
            if (!disposed) showError(new InvalidOperationException("자료 파일 이름을 읽을 수 없습니다: " + path, error));
        }
        finally { pendingScans--; if (!disposed) UpdateStatus(); }
    }

    private static (List<FileEntry> Files, bool Limited) Discover(string path, bool directory, CancellationToken cancellation)
    {
        EnsurePlainAncestors(path);
        var found = new List<FileEntry>();
        if (!directory)
        {
            if ((File.GetAttributes(path) & (Excluded | FileAttributes.Directory)) == 0 && Supported(path, explicitFile: true)) found.Add(new FileEntry(path));
            return (found, false);
        }
        var options = new EnumerationOptions
        {
            RecurseSubdirectories = false, IgnoreInaccessible = true, AttributesToSkip = Excluded,
            ReturnSpecialDirectories = false
        };
        var pending = new Stack<(string Path, int Depth)>();
        pending.Push((path, 0));
        int inspected = 0, directories = 0;
        bool limited = false;
        while (pending.Count > 0)
        {
            cancellation.ThrowIfCancellationRequested();
            var current = pending.Pop();
            if (++directories > 1000) { limited = true; break; }
            try
            {
                EnsurePlainAncestors(current.Path);
                foreach (string child in Directory.EnumerateFileSystemEntries(current.Path, "*", options))
                {
                    cancellation.ThrowIfCancellationRequested();
                    if (Path.GetFileName(child.AsSpan()).Equals(".hwpreview", StringComparison.OrdinalIgnoreCase)) continue;
                    if (++inspected > 40000 || found.Count == MaxFiles) return (found, true);
                    FileAttributes attributes;
                    try { attributes = File.GetAttributes(child); }
                    catch (Exception error) when (IsPathError(error)) { continue; }
                    if ((attributes & Excluded) != 0) continue;
                    if ((attributes & FileAttributes.Directory) != 0)
                    {
                        if (current.Depth < 12 && pending.Count < 1000) pending.Push((child, current.Depth + 1));
                        else limited = true;
                    }
                    else if (Supported(child)) found.Add(new FileEntry(child));
                }
            }
            catch (Exception error) when (IsPathError(error))
            {
                if (current.Path == path) throw;
            }
        }
        return (found, limited);
    }

    private static void EnsurePlainAncestors(string path)
    {
        for (string? current = path; current is not null; current = Path.GetDirectoryName(current))
        {
            // A visible selected folder may live below hidden AppData. Keep
            // filtering its entries, but reject traversal links in every ancestor.
            var excluded = current == path && Path.GetDirectoryName(current) is not null
                ? Excluded : FileAttributes.ReparsePoint;
            if ((File.GetAttributes(current) & excluded) != 0)
                throw new InvalidOperationException("숨김·시스템·링크 경로는 자료 탐색에 사용할 수 없습니다.");
        }
    }

    private static bool Supported(string path, bool explicitFile = false)
    {
        string name = Path.GetFileName(path), extension = Path.GetExtension(name);
        return name.Length > 0 && !name.Any(c => c is '"' or '\'' || char.IsControl(c)) &&
            (Extensions.Contains(extension) || TextNames.Contains(name) || Extensions.Contains(name) ||
                (explicitFile && extension.Length == 0));
    }
    private static bool IsPathError(Exception error) => error is IOException or UnauthorizedAccessException or
        ArgumentException or NotSupportedException or System.Security.SecurityException or InvalidOperationException;

    private static void Name(DependencyObject element, string id, string name)
    { AutomationProperties.SetAutomationId(element, id); AutomationProperties.SetName(element, name); }

    private static Button PickerButton(string label, string id, string name)
    {
        var button = new Button { Content = label, Height = 30, MinHeight = 30, Focusable = false, IsTabStop = false, FontSize = 12 };
        Name(button, id, name); button.ToolTip = name;
        return button;
    }

    private void ApplyColors()
    {
        bool contrast = SystemParameters.HighContrast;
        var border = (Border)popup.Child;
        border.SetResourceReference(Border.BackgroundProperty, contrast ? SystemColors.WindowBrushKey : "SurfaceBrush");
        border.SetResourceReference(Border.BorderBrushProperty, contrast ? SystemColors.WindowTextBrushKey : "PanelBorderBrush");
        list.SetResourceReference(Control.BackgroundProperty, contrast ? SystemColors.WindowBrushKey : "SurfaceBrush");
        list.SetResourceReference(Control.ForegroundProperty, contrast ? SystemColors.WindowTextBrushKey : "BodyTextBrush");
        status.SetResourceReference(TextBlock.ForegroundProperty, contrast ? SystemColors.WindowTextBrushKey : "MutedTextBrush");
        ApplyPickerColors(folderButton, contrast);
        ApplyPickerColors(fileButton, contrast);
        object selectionBrush = contrast ? SystemColors.HighlightBrush : owner.TryFindResource("SoftAccentBrush") ?? Brushes.LightCyan;
        object selectionText = contrast ? SystemColors.HighlightTextBrush : owner.TryFindResource("BodyTextBrush") ?? Brushes.Black;
        list.Resources[SystemColors.HighlightBrushKey] = selectionBrush;
        list.Resources[SystemColors.HighlightTextBrushKey] = selectionText;
        list.Resources[SystemColors.InactiveSelectionHighlightBrushKey] = selectionBrush;
        list.Resources[SystemColors.InactiveSelectionHighlightTextBrushKey] = selectionText;
    }

    private static void ApplyPickerColors(Button button, bool contrast)
    {
        button.SetResourceReference(Control.BackgroundProperty, contrast ? SystemColors.ControlBrushKey : "SurfaceBrush");
        button.SetResourceReference(Control.ForegroundProperty, contrast ? SystemColors.ControlTextBrushKey : "BodyTextBrush");
        button.SetResourceReference(Control.BorderBrushProperty, contrast ? SystemColors.WindowTextBrushKey : "PanelBorderBrush");
    }

    private sealed class EditorState { public int Revision, SelectionRevision; public bool UserEdit; }
    private readonly record struct Token(int Start, int Length);
    private sealed record Choice(TextBox Editor, EditorState State, Token Token, string Original, int Revision,
        int SelectionRevision, int Caret, int Epoch);
    private readonly record struct Match(FileEntry Entry, int Score);
    private sealed class FileEntry
    {
        public string Path { get; }
        public string Name { get; }
        public string Folder { get; }
        public FileEntry(string path) { Path = path; Name = System.IO.Path.GetFileName(path); Folder = System.IO.Path.GetDirectoryName(path) ?? ""; }
    }
    private sealed class Row
    {
        public FileEntry? Entry;
        public TextBlock Title { get; } = new() { FontSize = 12, FontWeight = FontWeights.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis };
        public TextBlock Folder { get; } = new() { FontSize = 10, TextTrimming = TextTrimming.CharacterEllipsis };
        public ListBoxItem Item { get; }
        public Row()
        {
            var content = new StackPanel(); content.Children.Add(Title); content.Children.Add(Folder);
            Item = new ListBoxItem
            {
                Content = content, Focusable = false, IsTabStop = false, Padding = new Thickness(7, 3, 7, 3),
                HorizontalContentAlignment = HorizontalAlignment.Stretch
            };
        }
    }
}
