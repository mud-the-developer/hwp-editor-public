using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace HWPReview.Native;

internal sealed record DocumentPassageTarget(string Identity, string Sha256, int Page, int SectionIndex, int ParagraphIndex, string Text, bool Truncated);

// Resident local HWPX preview surface consuming immutable authenticated bytes only.
// No document, process, OMP, approval, shell, or file-action message handlers exist here.
internal sealed class HwpxDocumentPane : UserControl, IDisposable
{
    private const string Origin = "https://hwp-document.invalid";
    private const string Page = Origin + "/index.html";
    private const int MaximumBytes = 64 * 1024 * 1024;
    private static readonly HashSet<string> AssetPaths = new(StringComparer.Ordinal)
    {
        "/index.html", "/viewer.js", "/viewer.css", "/svg-safety.js",
        "/vendor/rhwp/rhwp.js", "/vendor/rhwp/rhwp_bg.wasm"
    };

    private readonly Grid layout = new();
    private readonly Border placeholder = new() { Padding = new Thickness(24) };
    private readonly TextBlock placeholderText = new()
    {
        Text = "작업 폴더를 열고 .hwpx 문서를 선택하면 여기에 라이브 미리보기가 표시됩니다.",
        FontSize = 13,
        TextWrapping = TextWrapping.Wrap,
        TextAlignment = TextAlignment.Center,
        HorizontalAlignment = HorizontalAlignment.Center,
        VerticalAlignment = VerticalAlignment.Center,
        MaxWidth = 420
    };
    private readonly string profile = Path.Combine(Path.GetTempPath(), "HWPReview-document-" + Guid.NewGuid().ToString("N"));
    private WebView2? view;
    private CoreWebView2Environment? environment;
    private Task? initializeTask;
    private TaskCompletionSource? readySource;
    private TaskCompletionSource? pending;
    private byte[]? currentBytes;
    private string currentSha256 = "";
    private string currentIdentity = "";
    private string currentLabel = "";
    private bool currentPreserveView;
    private bool currentFailed;
    private string viewerAssetsPath = "";
    private int generation;
    private bool disposed;

    public int RenderedPageCount { get; private set; }
    public int CurrentPage { get; private set; }
    public string DocumentIdentity { get; private set; } = "";
    public string CurrentSha256 => currentSha256;
    public string StatusText { get; private set; } = "문서를 선택하세요.";
    public event EventHandler<string>? StatusChanged;
    public event EventHandler<string>? ErrorRaised;
    public event EventHandler<DocumentPassageTarget>? PassageDoubleClicked;

    public HwpxDocumentPane()
    {
        placeholder.SetResourceReference(Border.BackgroundProperty, "CanvasBrush");
        placeholderText.SetResourceReference(TextBlock.ForegroundProperty, "MutedTextBrush");
        placeholder.Child = placeholderText;
        layout.Children.Add(placeholder);
        Content = layout;
        MinHeight = 200;
        AutomationProperties.SetName(this, "HWPX 라이브 미리보기");
        AutomationProperties.SetAutomationId(this, "HwpxDocumentPane");
    }

    public void Clear(string message)
    {
        Dispatcher.VerifyAccess();
        generation++;
        pending?.TrySetCanceled();
        pending = null;
        currentBytes = null;
        currentSha256 = "";
        currentIdentity = "";
        currentLabel = "";
        currentFailed = false;
        RenderedPageCount = 0;
        CurrentPage = 0;
        DocumentIdentity = "";
        SetStatus(message);
        placeholderText.Text = message;
        placeholder.Visibility = Visibility.Visible;
        if (view?.CoreWebView2 is CoreWebView2 core && readySource?.Task.IsCompletedSuccessfully == true)
        {
            try
            {
                core.PostWebMessageAsJson(JsonSerializer.Serialize(new { type = "clear", generation }));
            }
            catch { }
        }
        if (view is not null) view.Visibility = Visibility.Collapsed;
    }

    public async Task ShowAsync(ReadOnlyMemory<byte> bytes, string expectedSha256, string identity, string label, bool preserveView = false)
    {
        Dispatcher.VerifyAccess();
        ObjectDisposedException.ThrowIf(disposed, this);
        if (bytes.Length == 0 || bytes.Length > MaximumBytes || identity.Length is 0 or > 1024 || label.Length > 1024 ||
            expectedSha256.Length != 64 || expectedSha256.Any(c => !(c is >= '0' and <= '9' or >= 'a' and <= 'f')))
            throw new InvalidDataException("문서 크기·식별자·SHA256 형식이 올바르지 않습니다.");

        // Snapshot before any await so callers cannot mutate queued bytes.
        byte[] snapshot = bytes.ToArray();
        if (Convert.ToHexStringLower(SHA256.HashData(snapshot)) != expectedSha256)
            throw new InvalidDataException("현재 HWPX 바이트와 SHA256이 다릅니다.");

        int current = ++generation;
        pending?.TrySetCanceled();
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        pending = completion;
        currentBytes = snapshot;
        currentSha256 = expectedSha256;
        currentIdentity = identity;
        currentLabel = label;
        currentPreserveView = preserveView;
        currentFailed = false;
        if (!preserveView) SetStatus("HWPX를 표시하는 중…");

        try
        {
            await EnsureInitializedAsync();
            if (current != generation || disposed) return;
            if (view?.CoreWebView2 is null) throw new IOException("미리보기 WebView2를 초기화하지 못했습니다.");
            view.Visibility = Visibility.Visible;
            placeholder.Visibility = Visibility.Collapsed;
            view.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new
            {
                type = "show",
                generation = current,
                identity,
                sha256 = expectedSha256,
                preserveView
            }));
            await completion.Task.WaitAsync(TimeSpan.FromSeconds(90));
        }
        catch (OperationCanceledException) when (current != generation || disposed)
        {
            return;
        }
        catch (Exception error)
        {
            if (current != generation || disposed) return;
            FailCurrent(current, error);
            throw;
        }
        finally
        {
            if (current == generation && ReferenceEquals(pending, completion)) pending = null;
        }
    }

    private Task EnsureInitializedAsync()
    {
        if (initializeTask is not null && !initializeTask.IsFaulted && !initializeTask.IsCanceled && view is not null)
            return initializeTask;
        initializeTask = InitializeResidentWebViewAsync();
        return initializeTask;
    }

    private async Task InitializeResidentWebViewAsync()
    {
        TearDownWebView();
        string assets = Path.Combine(AppContext.BaseDirectory, "viewer");
        LaunchDescriptor.RequirePath(assets, true);
        foreach (string path in AssetPaths)
            LaunchDescriptor.RequirePath(Path.Combine(assets, path.TrimStart('/').Replace('/', Path.DirectorySeparatorChar)), false);
        viewerAssetsPath = assets;

        var browser = new WebView2 { Visibility = Visibility.Collapsed };
        view = browser;
        layout.Children.Insert(0, browser);

        environment ??= await CoreWebView2Environment.CreateAsync(null, profile, new CoreWebView2EnvironmentOptions
        {
            AllowSingleSignOnUsingOSPrimaryAccount = false,
            AreBrowserExtensionsEnabled = false
        }).WaitAsync(TimeSpan.FromSeconds(30));
        if (disposed || !ReferenceEquals(view, browser)) return;

        await browser.EnsureCoreWebView2Async(environment).WaitAsync(TimeSpan.FromSeconds(30));
        if (disposed || !ReferenceEquals(view, browser)) return;

        CoreWebView2 core = browser.CoreWebView2;
        var settings = core.Settings;
        settings.AreDevToolsEnabled = false;
        settings.AreDefaultContextMenusEnabled = false;
        settings.AreHostObjectsAllowed = false;
        settings.AreDefaultScriptDialogsEnabled = false;
        settings.AreBrowserAcceleratorKeysEnabled = false;
        settings.IsStatusBarEnabled = false;
        settings.IsBuiltInErrorPageEnabled = false;
        settings.IsScriptEnabled = true;
        settings.IsZoomControlEnabled = false;
        settings.IsPinchZoomEnabled = false;
        settings.IsSwipeNavigationEnabled = false;
        settings.IsWebMessageEnabled = true;
        settings.IsPasswordAutosaveEnabled = false;
        settings.IsGeneralAutofillEnabled = false;

        readySource = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += (_, args) => HandleWebResourceRequested(args);
        bool navigated = false;
        core.NavigationStarting += (_, args) => { if (args.Uri != Page || navigated) args.Cancel = true; else navigated = true; };
        core.NavigationCompleted += (_, args) =>
        {
            if (!args.IsSuccess)
            {
                var error = new IOException("로컬 미리보기 문서를 불러오지 못했습니다: " + args.WebErrorStatus);
                readySource?.TrySetException(error);
                FailCurrent(generation, error);
            }
        };
        core.FrameCreated += (_, args) => args.Frame.NavigationStarting += (_, navigation) => navigation.Cancel = true;
        core.NewWindowRequested += (_, args) => args.Handled = true;
        core.PermissionRequested += (_, args) => { args.State = CoreWebView2PermissionState.Deny; args.SavesInProfile = false; };
        core.DownloadStarting += (_, args) => args.Cancel = true;
        core.BasicAuthenticationRequested += (_, args) => args.Cancel = true;
        core.ClientCertificateRequested += (_, args) => { args.Cancel = true; args.Handled = true; };
        core.ServerCertificateErrorDetected += (_, args) => args.Action = CoreWebView2ServerCertificateErrorAction.Cancel;
        core.ProcessFailed += (_, args) =>
        {
            var error = new IOException("로컬 미리보기 표시 프로세스 오류: " + args.ProcessFailedKind);
            readySource?.TrySetException(error);
            TearDownWebView();
            initializeTask = null;
            FailCurrent(generation, error);
        };
        core.WebMessageReceived += (_, args) => HandleWebMessage(args);
        core.Navigate(Page);
        await readySource.Task.WaitAsync(TimeSpan.FromSeconds(30));
    }

    private void HandleWebResourceRequested(CoreWebView2WebResourceRequestedEventArgs args)
    {
        if (environment is null || disposed ||
            !Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out Uri? uri) ||
            uri.Scheme != "https" || uri.Host != "hwp-document.invalid" || !uri.IsDefaultPort ||
            uri.UserInfo.Length != 0 || uri.Query.Length != 0 || uri.Fragment.Length != 0 || args.Request.Method != "GET")
        {
            args.Response = environment?.CreateWebResourceResponse(Stream.Null, 403, "Forbidden", "Content-Type: text/plain\r\nCache-Control: no-store");
            return;
        }
        if (args.Request.Uri == Origin + "/document.hwpx")
        {
            byte[]? snapshot = currentBytes;
            if (snapshot is null)
            {
                args.Response = environment.CreateWebResourceResponse(Stream.Null, 404, "Not Found", "Content-Type: text/plain\r\nCache-Control: no-store");
                return;
            }
            args.Response = environment.CreateWebResourceResponse(new MemoryStream(snapshot, false), 200, "OK",
                "Content-Type: application/octet-stream\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff");
            return;
        }
        if (!AssetPaths.Contains(uri.AbsolutePath))
        {
            args.Response = environment.CreateWebResourceResponse(Stream.Null, 403, "Forbidden", "Content-Type: text/plain\r\nCache-Control: no-store");
            return;
        }
        string mime = uri.AbsolutePath switch
        {
            "/index.html" => "text/html; charset=utf-8",
            "/viewer.css" => "text/css; charset=utf-8",
            "/vendor/rhwp/rhwp_bg.wasm" => "application/wasm",
            _ => "text/javascript; charset=utf-8"
        };
        var content = new FileStream(Path.Combine(viewerAssetsPath, uri.AbsolutePath.TrimStart('/').Replace('/', Path.DirectorySeparatorChar)),
            FileMode.Open, FileAccess.Read, FileShare.Read);
        args.Response = environment.CreateWebResourceResponse(content, 200, "OK",
            "Content-Type: " + mime + "\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff");
    }

    private void HandleWebMessage(CoreWebView2WebMessageReceivedEventArgs args)
    {
        if (disposed || args.Source != Page) return;
        try
        {
            string json = args.WebMessageAsJson;
            if (json.Length > 32768) throw new InvalidDataException("미리보기 메시지가 너무 큽니다.");
            using var parsed = JsonDocument.Parse(json);
            JsonElement message = parsed.RootElement;
            string? packetType = message.GetProperty("type").GetString();
            if (packetType == "ready")
            {
                if (message.EnumerateObject().Count() != 1) throw new InvalidDataException("예상하지 못한 미리보기 준비 메시지입니다.");
                readySource?.TrySetResult();
                return;
            }
            if (packetType != "passage" && json.Length > 4096) throw new InvalidDataException("미리보기 메시지가 너무 큽니다.");
            if (!message.TryGetProperty("generation", out JsonElement genEl) || genEl.GetInt32() != generation)
                return; // Stale message from a superseded generation; ignore cleanly.
            if (currentFailed ||
                message.GetProperty("identity").GetString() != currentIdentity ||
                message.GetProperty("sha256").GetString() != currentSha256)
                return;

            if (packetType == "error")
            {
                if (message.EnumerateObject().Count() != 5) throw new InvalidDataException("예상하지 못한 미리보기 오류 메시지입니다.");
                throw new InvalidDataException(message.GetProperty("message").GetString() ?? "로컬 쪽 미리보기 오류");
            }
            if (packetType == "passage")
            {
                if (pending is not null && !pending.Task.IsCompleted) return;
                PassageDoubleClicked?.Invoke(this, ReadPassage(message, currentIdentity, currentSha256, RenderedPageCount));
                return;
            }
            if (packetType != "rendered" || message.EnumerateObject().Count() != 6)
                throw new InvalidDataException("예상하지 못한 미리보기 메시지입니다.");
            int page = message.GetProperty("page").GetInt32();
            int count = message.GetProperty("pageCount").GetInt32();
            if (count < 1 || count > 10000 || page < 1 || page > count)
                throw new InvalidDataException("쪽 수 응답이 올바르지 않습니다.");
            if (!currentPreserveView && pending is not null && !pending.Task.IsCompleted && page != 1)
                throw new InvalidDataException("첫 쪽 표시가 확인되지 않았습니다.");
            RenderedPageCount = count;
            CurrentPage = page;
            DocumentIdentity = currentIdentity;
            SetStatus($"{currentLabel} · {page}/{count}쪽");
            pending?.TrySetResult();
        }
        catch (Exception error)
        {
            FailCurrent(generation, error);
        }
    }

    private void FailCurrent(int targetGeneration, Exception error)
    {
        if (disposed || targetGeneration != generation || currentFailed) return;
        currentFailed = true;
        RenderedPageCount = 0;
        CurrentPage = 0;
        DocumentIdentity = "";
        string message = "미리보기를 표시하지 못했습니다. " + error.Message;
        SetStatus(message);
        placeholderText.Text = message;
        placeholder.Visibility = Visibility.Visible;
        if (view is not null) view.Visibility = Visibility.Collapsed;
        pending?.TrySetException(error);
        ErrorRaised?.Invoke(this, message);
    }

    private void SetStatus(string text)
    {
        StatusText = text;
        StatusChanged?.Invoke(this, text);
    }

    private void TearDownWebView()
    {
        readySource?.TrySetCanceled();
        readySource = null;
        if (view is not null)
        {
            layout.Children.Remove(view);
            view.Dispose();
            view = null;
        }
    }

    private static DocumentPassageTarget ReadPassage(JsonElement message, string identity, string sha256, int pageCount)
    {
        int fields = 0;
        foreach (JsonProperty property in message.EnumerateObject())
        {
            int bit = property.Name switch
            {
                "type" => 1, "generation" => 2, "identity" => 4, "sha256" => 8,
                "page" => 16, "sectionIndex" => 32, "paragraphIndex" => 64, "text" => 128, "truncated" => 256,
                _ => 0
            };
            if (bit == 0 || (fields & bit) != 0) throw new InvalidDataException("허용되지 않은 본문 위치 메시지입니다.");
            fields |= bit;
        }
        if (fields != 511) throw new InvalidDataException("본문 위치 메시지 필드가 올바르지 않습니다.");
        int page = message.GetProperty("page").GetInt32();
        int section = message.GetProperty("sectionIndex").GetInt32();
        int paragraph = message.GetProperty("paragraphIndex").GetInt32();
        string? text = message.GetProperty("text").GetString();
        bool truncated = message.GetProperty("truncated").GetBoolean();
        if (page < 1 || page > pageCount || section is < 0 or > 1000000 || paragraph is < 0 or > 1000000 ||
            string.IsNullOrWhiteSpace(text) || text.Length > 2048)
            throw new InvalidDataException("본문 위치·발췌 한도가 올바르지 않습니다.");
        for (int index = 0; index < text.Length; index++)
        {
            char value = text[index];
            if ((value < ' ' && value is not '\t' and not '\r' and not '\n') || value == '\u007f' ||
                (char.IsSurrogate(value) && (!char.IsHighSurrogate(value) || index + 1 == text.Length || !char.IsLowSurrogate(text[++index]))))
                throw new InvalidDataException("본문 발췌 문자가 올바르지 않습니다.");
        }
        return new DocumentPassageTarget(identity, sha256, page, section, paragraph, text, truncated);
    }

    public void Dispose()
    {
        if (disposed) return;
        Dispatcher.VerifyAccess();
        pending?.TrySetCanceled();
        pending = null;
        TearDownWebView();
        disposed = true;
    }
}
