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

// This surface only consumes immutable authenticated bytes. It has no document,
// process, OMP, approval, shell, or file-action message handlers.
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
    private readonly TextBlock status = new() { Text = "문서를 선택하면 여기에 표시됩니다.", FontSize = 11, TextWrapping = TextWrapping.NoWrap,
        TextTrimming = TextTrimming.CharacterEllipsis, Margin = new Thickness(8, 3, 8, 3) };
    private readonly string profile = Path.Combine(Path.GetTempPath(), "HWPReview-document-" + Guid.NewGuid().ToString("N"));
    private WebView2? view;
    private TaskCompletionSource? pending;
    private int generation;
    private bool disposed;
    public int RenderedPageCount { get; private set; }
    public string DocumentIdentity { get; private set; } = "";
    public event EventHandler<string>? ErrorRaised;
    public event EventHandler<DocumentPassageTarget>? PassageDoubleClicked;

    public HwpxDocumentPane()
    {
        layout.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var footer = new Border { Child = new ScrollViewer { Content = status, MaxHeight = 96,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto },
            BorderThickness = new Thickness(0, 1, 0, 0), BorderBrush = SystemColors.ControlLightBrush };
        Grid.SetRow(footer, 1);
        layout.Children.Add(footer);
        status.SetResourceReference(TextBlock.ForegroundProperty, "MutedTextBrush");
        Content = layout;
        MinHeight = 200;
        AutomationProperties.SetName(this, "현재 HWPX 로컬 쪽 미리보기");
        AutomationProperties.SetName(status, "문서 미리보기 상태");
        AutomationProperties.SetAutomationId(status, "DocumentRenderStatus");
        AutomationProperties.SetLiveSetting(status, AutomationLiveSetting.Polite);
    }

    public void Clear(string message)
    {
        Dispatcher.VerifyAccess();
        generation++;
        pending?.TrySetCanceled();
        pending = null;
        RenderedPageCount = 0;
        DocumentIdentity = "";
        if (view is not null) { layout.Children.Remove(view); view.Dispose(); view = null; }
        status.Text = message;
        status.TextWrapping = TextWrapping.NoWrap;
        status.TextTrimming = TextTrimming.None;
        AutomationProperties.SetHelpText(status, "");
        status.ToolTip = message;
        AutomationProperties.SetName(status, message);
    }

    public async Task ShowAsync(ReadOnlyMemory<byte> bytes, string expectedSha256, string identity, string label)
    {
        Dispatcher.VerifyAccess();
        ObjectDisposedException.ThrowIf(disposed, this);
        Clear("HWPX를 표시하는 중…");
        int current = generation;
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        pending = completion;
        WebView2? browser = null;
        bool failed = false;
        try
        {
            if (bytes.Length == 0 || bytes.Length > MaximumBytes || identity.Length is 0 or > 1024 || label.Length > 1024 || expectedSha256.Length != 64 || expectedSha256.Any(c => !(c is >= '0' and <= '9' or >= 'a' and <= 'f')))
                throw new InvalidDataException("문서 크기·식별자·SHA256 형식이 올바르지 않습니다.");
            // Snapshot before the first await: callers cannot mutate a queued render.
            byte[] snapshot = bytes.ToArray();
            if (Convert.ToHexStringLower(SHA256.HashData(snapshot)) != expectedSha256)
                throw new InvalidDataException("현재 HWPX 바이트와 SHA256이 다릅니다.");
            string assets = Path.Combine(AppContext.BaseDirectory, "viewer");
            LaunchDescriptor.RequirePath(assets, true);
            foreach (string path in AssetPaths) LaunchDescriptor.RequirePath(Path.Combine(assets, path.TrimStart('/').Replace('/', Path.DirectorySeparatorChar)), false);
            browser = new WebView2();
            view = browser;
            layout.Children.Add(browser);
            var environment = await CoreWebView2Environment.CreateAsync(null, profile, new CoreWebView2EnvironmentOptions { AllowSingleSignOnUsingOSPrimaryAccount = false, AreBrowserExtensionsEnabled = false }).WaitAsync(TimeSpan.FromSeconds(30));
            if (current != generation || disposed) return;
            await browser.EnsureCoreWebView2Async(environment).WaitAsync(TimeSpan.FromSeconds(30));
            if (current != generation || disposed) return;
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
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += (_, args) =>
            {
                if (current != generation || disposed || failed ||
                    !Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out Uri? uri) ||
                    uri.Scheme != "https" || uri.Host != "hwp-document.invalid" || !uri.IsDefaultPort ||
                    uri.UserInfo.Length != 0 || uri.Query.Length != 0 || uri.Fragment.Length != 0 || args.Request.Method != "GET")
                {
                    args.Response = environment.CreateWebResourceResponse(Stream.Null, 403, "Forbidden", "Content-Type: text/plain\r\nCache-Control: no-store");
                    return;
                }
                if (args.Request.Uri == Origin + "/document.hwpx")
                {
                    args.Response = environment.CreateWebResourceResponse(new MemoryStream(snapshot, false), 200, "OK", "Content-Type: application/octet-stream\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff");
                    return;
                }
                if (!AssetPaths.Contains(uri.AbsolutePath))
                {
                    args.Response = environment.CreateWebResourceResponse(Stream.Null, 403, "Forbidden", "Content-Type: text/plain\r\nCache-Control: no-store");
                    return;
                }
                // Folder mapping resolves missing files before dynamic requests
                // reach this handler. Serve both fixed assets and the captured
                // document here; no request ever falls through to the network.
                string mime = uri.AbsolutePath switch
                {
                    "/index.html" => "text/html; charset=utf-8",
                    "/viewer.css" => "text/css; charset=utf-8",
                    "/vendor/rhwp/rhwp_bg.wasm" => "application/wasm",
                    _ => "text/javascript; charset=utf-8"
                };
                var content = new FileStream(Path.Combine(assets, uri.AbsolutePath.TrimStart('/').Replace('/', Path.DirectorySeparatorChar)),
                    FileMode.Open, FileAccess.Read, FileShare.Read);
                args.Response = environment.CreateWebResourceResponse(content, 200, "OK", "Content-Type: " + mime + "\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff");
            };
            bool navigated = false;
            core.NavigationStarting += (_, args) => { if (args.Uri != Page || navigated) args.Cancel = true; else navigated = true; };
            core.NavigationCompleted += (_, args) => { if (!args.IsSuccess) Fail(new IOException("로컬 미리보기 문서를 불러오지 못했습니다: " + args.WebErrorStatus)); };
            core.FrameCreated += (_, args) => args.Frame.NavigationStarting += (frameSender, navigation) => navigation.Cancel = true;
            core.NewWindowRequested += (_, args) => args.Handled = true;
            core.PermissionRequested += (_, args) => { args.State = CoreWebView2PermissionState.Deny; args.SavesInProfile = false; };
            core.DownloadStarting += (_, args) => args.Cancel = true;
            core.BasicAuthenticationRequested += (_, args) => args.Cancel = true;
            core.ClientCertificateRequested += (_, args) => { args.Cancel = true; args.Handled = true; };
            core.ServerCertificateErrorDetected += (_, args) => args.Action = CoreWebView2ServerCertificateErrorAction.Cancel;
            core.ProcessFailed += (_, args) => Fail(new IOException("로컬 미리보기 표시 프로세스 오류: " + args.ProcessFailedKind));
            bool started = false;
            core.WebMessageReceived += (_, args) =>
            {
                if (disposed || failed || current != generation || args.Source != Page) return;
                try
                {
                    string json = args.WebMessageAsJson;
                    if (json.Length > 32768) throw new InvalidDataException("미리보기 메시지가 너무 큽니다.");
                    using var parsed = JsonDocument.Parse(json);
                    JsonElement message = parsed.RootElement;
                    string? packetType = message.GetProperty("type").GetString();
                    // Only bounded passage data gets a larger envelope. Normal
                    // renderer status/errors retain the original strict limit.
                    if (packetType != "passage" && json.Length > 4096) throw new InvalidDataException("미리보기 상태 메시지가 너무 큽니다.");
                    if (message.GetProperty("type").GetString() == "ready" && !started)
                    {
                        started = true;
                        core.PostWebMessageAsJson(JsonSerializer.Serialize(new { type = "show", generation = current, identity, sha256 = expectedSha256 }));
                        return;
                    }
                    if (!started || message.GetProperty("generation").GetInt32() != current || message.GetProperty("identity").GetString() != identity || message.GetProperty("sha256").GetString() != expectedSha256) return;
                    string? type = message.GetProperty("type").GetString();
                    if (type == "passage")
                    {
                        // No action capability: the consumer must separately
                        // revalidate this immutable source-bound UI memo target.
                        if (!completion.Task.IsCompletedSuccessfully || DocumentIdentity != identity) return;
                        DocumentPassageTarget target = ReadPassage(message, identity, expectedSha256, RenderedPageCount);
                        PassageDoubleClicked?.Invoke(this, target);
                        return;
                    }
                    if (type == "error") throw new InvalidDataException("HWPX 로컬 쪽 렌더링에 실패했습니다. " + message.GetProperty("message").GetString());
                    if (type != "rendered") throw new InvalidDataException("허용되지 않은 미리보기 상태입니다.");
                    int count = message.GetProperty("pageCount").GetInt32();
                    int page = message.GetProperty("page").GetInt32();
                    if (count is < 1 or > 10000 || page < 1 || page > count) throw new InvalidDataException("미리보기 쪽 정보가 올바르지 않습니다.");
                    if (!completion.Task.IsCompleted && page != 1) throw new InvalidDataException("첫 쪽 표시가 확인되지 않았습니다.");
                    RenderedPageCount = count;
                    DocumentIdentity = identity;
                    status.Text = $"{label} · {page}/{count}쪽 · 로컬 미리보기";
                    status.TextWrapping = TextWrapping.NoWrap;
                    status.TextTrimming = TextTrimming.CharacterEllipsis;
                    status.ToolTip = status.Text + "\n한컴 검수와 다를 수 있습니다.";
                    AutomationProperties.SetHelpText(status, identity);
                    AutomationProperties.SetName(status, status.Text);
                    completion.TrySetResult();
                }
                catch (Exception error) { Fail(error); }
            };
            core.Navigate(Page);
            await completion.Task.WaitAsync(TimeSpan.FromSeconds(90));
        }
        catch (Exception error)
        {
            if (current != generation || disposed) return;
            Fail(error);
            throw;
        }
        finally
        {
            if (current == generation && ReferenceEquals(pending, completion)) pending = null;
        }

        void Fail(Exception error)
        {
            if (disposed || failed || current != generation) return;
            failed = true;
            RenderedPageCount = 0;
            DocumentIdentity = "";
            status.Text = "미리보기를 표시하지 못했습니다. 원본은 변경되지 않았습니다. " + error.Message;
            status.TextWrapping = TextWrapping.Wrap;
            status.ToolTip = status.Text;
            status.TextTrimming = TextTrimming.None;
            AutomationProperties.SetName(status, status.Text);
            AutomationProperties.SetHelpText(status, identity);
            completion.TrySetException(error);
            if (browser is not null) browser.Visibility = Visibility.Collapsed;
            ErrorRaised?.Invoke(this, status.Text);
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
        Clear("문서 미리보기가 닫혔습니다.");
        disposed = true;
        // The uniquely owned profile is never shared with OMP or existing auth.
        // Do not erase browser evidence or interfere with asynchronous process exit.
    }
}
