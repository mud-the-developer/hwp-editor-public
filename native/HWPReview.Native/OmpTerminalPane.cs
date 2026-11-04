using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace HWPReview.Native;

internal sealed class OmpTerminalPane : UserControl, IAsyncDisposable
{
    private const string Origin = "https://hwp-omp.invalid";
    private const string Page = Origin + "/index.html";
    private static readonly UTF8Encoding Utf8 = new(false, true);
    private static readonly OmpAgentState DisconnectedState = new(false, "", "disconnected", false, "", "", "실제 OMP 공개 연결이 없습니다.");
    private static readonly HashSet<string> AssetPaths = new(StringComparer.Ordinal)
    {
        "/index.html", "/terminal.js", "/terminal.css", "/vendor/xterm/xterm.js", "/vendor/xterm/xterm.css", "/vendor/addon-fit/addon-fit.js", "/vendor/unicode-graphemes/addon-unicode-graphemes.js"
    };
    private readonly Grid layout = new();
    private readonly TextBlock diagnostic = new() { Text = "실제 OMP 터미널을 시작하려면 실행을 선택하세요.", TextWrapping = TextWrapping.Wrap, Margin = new Thickness(8), Foreground = SystemColors.ControlTextBrush };
    private readonly Channel<(int Generation, JsonElement Message)> incoming = Channel.CreateBounded<(int Generation, JsonElement Message)>(new BoundedChannelOptions(64) { SingleReader = true, SingleWriter = true, FullMode = BoundedChannelFullMode.Wait });
    private readonly CancellationTokenSource lifetime = new();
    private readonly Task messageTask;
    private WebView2? view;
    private ConPtySession? session;
    private OmpSessionBridge? bridge;
    private Task? outputTask;
    private Task? inputDelivery;
    private CancellationTokenSource? outputLifetime;
    private CancellationTokenSource? startupCancellation;
    private TaskCompletionSource<(short Columns, short Rows)>? ready;
    private TaskCompletionSource? outputAcknowledged;
    private TaskCompletionSource? pasteAcknowledged;
    private long incomingSequence;
    private long outgoingSequence;
    private long outputId;
    private long pasteId;
    private long nextId;
    private bool starting;
    private bool rendererHealthy;
    private bool inputBlocked;
    private bool disposed;
    private int generation;
    public bool IsRunning => session?.IsRunning == true;
    public bool IsStarting => starting;
    public int? OwnedProcessId => session?.OwnedProcessId;
    public int? LastExitCode => session?.LastExitCode;
    public OmpAgentState AgentState
    {
        get
        {
            OmpAgentState state = bridge?.State ?? DisconnectedState;
            return state.CanSend && (starting || !IsRunning || !rendererHealthy || inputBlocked)
                ? state with { CanSend = false } : state;
        }
    }
    // HwndHost/WebView focus is not always reflected by WPF's focus tree.
    public bool OwnsKeyboardFocus
    {
        get
        {
            if (IsKeyboardFocusWithin || view?.IsKeyboardFocusWithin == true) return true;
            if (view is null || view.Handle == 0) return false;
            var focus = new GuiThreadInfo { Size = Marshal.SizeOf<GuiThreadInfo>() };
            return GetGUIThreadInfo(0, ref focus) && (focus.Focus == view.Handle || IsChild(view.Handle, focus.Focus));
        }
    }
    public event EventHandler? StatusChanged;
    public event EventHandler<string>? ErrorRaised;
    public event EventHandler<OmpAgentEvent>? AgentEvent;

    public OmpTerminalPane()
    {
        layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        layout.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        layout.Children.Add(diagnostic);
        Background = SystemColors.WindowBrush;
        Content = layout;
        MinHeight = 200;
        System.Windows.Automation.AutomationProperties.SetName(this, "실제 OMP 터미널");
        messageTask = Dispatcher.InvokeAsync(ProcessMessagesAsync).Task.Unwrap();
    }

    public async Task StartAsync(JsonElement launchDescriptor)
    {
        Dispatcher.VerifyAccess();
        ObjectDisposedException.ThrowIf(disposed, this);
        if (starting || IsRunning) throw new InvalidOperationException("이미 소유한 OMP가 실행 중이거나 시작 중입니다.");
        LaunchDescriptor launch = LaunchDescriptor.Parse(launchDescriptor);
        starting = true;
        try
        {
            if (session is not null)
            {
                outputLifetime?.Cancel();
                await session.DisposeAsync();
                if (bridge is not null) { await bridge.DisposeAsync(); bridge = null; }
                if (outputTask is not null) await outputTask;
                if (inputDelivery is not null) await inputDelivery;
                outputLifetime?.Dispose();
                session = null;
            }
            await InitializeRendererAsync(launch.WebViewUserDataDirectory);
            (short columns, short rows) = await ready!.Task.WaitAsync(TimeSpan.FromSeconds(30));
            if (!rendererHealthy || inputBlocked) throw new InvalidOperationException("터미널 표시 연결이 시작 전에 끊어졌습니다.");
            session = new ConPtySession();
            session.StatusChanged += SessionStatusChanged;
            session.ErrorRaised += SessionErrorRaised;
            // The same-user control listener is created before the actual child.
            // It controls only the extension attached to this native TUI session.
            bridge = new OmpSessionBridge(launch, Dispatcher, () => session?.IsRunning == true);
            bridge.AgentEvent += BridgeAgentEvent;
            // Start the output consumer before any native process is created.
            outputLifetime = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            outputTask = PumpOutputAsync(session, outputLifetime.Token);
            SetStatus("OMP를 시작합니다. 계정·모델 설정은 이 격리된 OMP 안에서 직접 확인하세요.");
            startupCancellation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            ConPtySession owned = session;
            await Task.Run(() => owned.Start(launch, columns, rows, startupCancellation.Token));
            bridge.NativeStatusChanged();
            if (!Post(new { v = 1, seq = NextSequence(), type = "state", running = owned.IsRunning })) throw new IOException("OMP 표시 연결이 시작 중 끊어졌습니다.");
            FocusTerminal();
        }
        catch (Exception error)
        {
            ReportError("OMP 터미널을 시작하지 못했습니다. 자동으로 재실행하지 않습니다. " + error.Message + (IsRunning ? " 소유한 자식은 계속 실행 중입니다. OMP 안에서 종료해야 합니다." : " 실행 전 준비 파일은 삭제하지 않습니다."));
            if (!IsRunning && bridge is not null) { await bridge.DisposeAsync(); bridge = null; }
            throw;
        }
        finally
        {
            startupCancellation?.Dispose(); startupCancellation = null;
            starting = false; StatusChanged?.Invoke(this, EventArgs.Empty);
        }
    }
    public Task SendPromptAsync(string requestId, string contextSha256, string text)
    {
        Dispatcher.VerifyAccess();
        if (starting || !IsRunning || !rendererHealthy || inputBlocked || bridge is null)
            throw new InvalidOperationException("실제 터미널·공개 연결이 정상일 때만 명시적으로 OMP 요청을 전송합니다.");
        return bridge.SendPromptAsync(requestId, contextSha256, text);
    }

    public Task AbortAgentAsync()
    {
        Dispatcher.VerifyAccess();
        if (bridge is null) throw new InvalidOperationException("소유한 OMP 공개 연결이 없습니다.");
        return bridge.AbortAsync();
    }

    public async Task ShutdownAgentAsync()
    {
        Dispatcher.VerifyAccess();
        if (bridge is null || session is null) throw new InvalidOperationException("소유한 OMP 공개 연결이 없습니다.");
        Task requested = bridge.ShutdownAsync();
        // Pipe ACK/event/EOF and native exit notifications can arrive in either
        // order. Only this held process's exit settles shutdown; never retry/kill.
        Task exited = session.WaitForExitAsync(TimeSpan.FromSeconds(15));
        try { await requested; }
        catch { await exited; return; }
        await exited;
    }


    private void BridgeAgentEvent(object? sender, OmpAgentEvent value)
    {
        Dispatcher.VerifyAccess();
        if (disposed || !ReferenceEquals(sender, bridge)) return;
        AgentEvent?.Invoke(this, value);
        if (value.Kind == "state") StatusChanged?.Invoke(this, EventArgs.Empty);
    }


    public async Task PasteDraftAsync(string text)
    {
        Dispatcher.VerifyAccess();
        if (starting || !IsRunning || !rendererHealthy || inputBlocked) throw new InvalidOperationException("OMP 터미널이 입력을 받을 수 없습니다.");
        if (pasteAcknowledged is not null) throw new InvalidOperationException("이전 붙여넣기 확인을 기다리고 있습니다. 자동 재전송하지 않습니다.");
        if (string.IsNullOrEmpty(text) || Utf8.GetByteCount(text) > 65_536 || text.Any(character => char.IsControl(character) && character is not ('\t' or '\r' or '\n'))) throw new InvalidDataException("붙여넣기는 64KiB 이하의 일반 텍스트만 허용합니다.");
        pasteId = ++nextId;
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        pasteAcknowledged = completion;
        bridge?.ObserveTerminalInput();
        Post(new { v = 1, seq = NextSequence(), type = "paste", id = pasteId, text });
        try
        {
            await completion.Task.WaitAsync(TimeSpan.FromSeconds(15));
            SetStatus("초안을 OMP 입력란에 붙여넣었습니다. 전송하려면 OMP 안에서 직접 Enter를 누르세요.");
            FocusTerminal();
        }
        catch (TimeoutException)
        {
            ReportError("붙여넣기 완료 여부를 확인하지 못했습니다. 중복 전송을 막기 위해 자동 재시도하지 않습니다. OMP 입력란을 직접 확인하세요.");
            throw;
        }
    }

    public void FocusTerminal()
    {
        Dispatcher.VerifyAccess();
        if (disposed || view?.CoreWebView2 is null || !rendererHealthy) return;
        view.Focus();
        Post(new { v = 1, seq = NextSequence(), type = "focus" });
    }

    private async Task InitializeRendererAsync(string userDataDirectory)
    {
        generation++;
        int currentGeneration = generation;
        if (view is not null) { layout.Children.Remove(view); view.Dispose(); }
        incomingSequence = 0; outgoingSequence = 0;
        rendererHealthy = false; inputBlocked = false;
        outputAcknowledged?.TrySetCanceled(); outputAcknowledged = null;
        pasteAcknowledged?.TrySetException(new IOException("터미널 문서가 변경되었습니다. 붙여넣기를 재전송하지 않습니다.")); pasteAcknowledged = null;
        ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
        string assets = Path.Combine(AppContext.BaseDirectory, "terminal");
        LaunchDescriptor.RequirePath(assets, true);
        foreach (string path in AssetPaths) LaunchDescriptor.RequirePath(Path.Combine(assets, path.TrimStart('/').Replace('/', Path.DirectorySeparatorChar)), false);
        var browser = new WebView2();
        view = browser;
        Grid.SetRow(browser, 1);
        layout.Children.Add(browser);
        CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, userDataDirectory, new CoreWebView2EnvironmentOptions { AllowSingleSignOnUsingOSPrimaryAccount = false, AreBrowserExtensionsEnabled = false }).WaitAsync(TimeSpan.FromSeconds(30));
        await browser.EnsureCoreWebView2Async(environment).WaitAsync(TimeSpan.FromSeconds(30));
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
        core.SetVirtualHostNameToFolderMapping("hwp-omp.invalid", assets, CoreWebView2HostResourceAccessKind.DenyCors);
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += (_, args) =>
        {
            bool permitted = Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out Uri? uri) && uri.Scheme == "https" && uri.Host == "hwp-omp.invalid" && uri.IsDefaultPort && uri.UserInfo.Length == 0 && uri.Query.Length == 0 && uri.Fragment.Length == 0 && AssetPaths.Contains(uri.AbsolutePath) && args.Request.Method == "GET";
            if (!permitted) args.Response = environment.CreateWebResourceResponse(Stream.Null, 403, "Forbidden", "Content-Type: text/plain\r\nCache-Control: no-store");
        };
        bool navigated = false;
        core.NavigationStarting += (_, args) =>
        {
            if (args.Uri != Page || navigated) { args.Cancel = true; return; }
            navigated = true;
        };
        core.NavigationCompleted += (_, args) =>
        {
            if (!disposed && currentGeneration == generation && !args.IsSuccess) RendererFailure("로컬 터미널 문서를 불러오지 못했습니다: " + args.WebErrorStatus);
        };
        core.FrameCreated += (_, args) => args.Frame.NavigationStarting += (frameSender, navigation) => navigation.Cancel = true;
        core.NewWindowRequested += (_, args) => args.Handled = true;
        core.PermissionRequested += (_, args) => { args.State = CoreWebView2PermissionState.Deny; args.SavesInProfile = false; };
        core.DownloadStarting += (_, args) => args.Cancel = true;
        core.BasicAuthenticationRequested += (_, args) => args.Cancel = true;
        core.ClientCertificateRequested += (_, args) => { args.Cancel = true; args.Handled = true; };
        core.ServerCertificateErrorDetected += (_, args) => args.Action = CoreWebView2ServerCertificateErrorAction.Cancel;
        core.ProcessFailed += (_, args) =>
        {
            if (!disposed && currentGeneration == generation) RendererFailure("터미널 표시 프로세스 오류: " + args.ProcessFailedKind + ". OMP 프로세스를 강제 종료하지 않습니다.");
        };
        core.WebMessageReceived += (_, args) => ReceiveMessage(args, currentGeneration);
        core.Navigate(Page);
    }

    private void ReceiveMessage(CoreWebView2WebMessageReceivedEventArgs args, int currentGeneration)
    {
        if (currentGeneration != generation || disposed) return;
        try
        {
            string json = args.WebMessageAsJson;
            if (args.Source != Page || json.Length > 150_000 || Utf8.GetByteCount(json) > 150_000) throw new InvalidDataException("터미널 메시지 출처 또는 크기가 잘못되었습니다.");
            using JsonDocument document = JsonDocument.Parse(json, new JsonDocumentOptions { MaxDepth = 6 });
            JsonElement message = document.RootElement;
            if (message.ValueKind != JsonValueKind.Object || !message.TryGetProperty("type", out JsonElement kind) || kind.ValueKind != JsonValueKind.String || !message.TryGetProperty("v", out JsonElement version) || !version.TryGetInt32(out int v) || v != 1 || !message.TryGetProperty("seq", out JsonElement order) || !order.TryGetInt64(out long sequence) || sequence != incomingSequence + 1) throw new InvalidDataException("터미널 메시지 순서 또는 형식이 잘못되었습니다.");
            string type = kind.GetString()!;
            switch (type)
            {
                case "ready": LaunchDescriptor.RequireKeys(message, "v", "seq", "type", "cols", "rows"); Dimensions(message); break;
                case "resize": LaunchDescriptor.RequireKeys(message, "v", "seq", "type", "cols", "rows"); Dimensions(message); break;
                case "input":
                    LaunchDescriptor.RequireKeys(message, "v", "seq", "type", "id", "text", "encoding"); PositiveId(message);
                    if (message.GetProperty("encoding").ValueKind != JsonValueKind.String || message.GetProperty("encoding").GetString() is not ("utf8" or "binary") || message.GetProperty("text").ValueKind != JsonValueKind.String || Utf8.GetByteCount(message.GetProperty("text").GetString()!) > 131_072 || (message.GetProperty("encoding").GetString() == "binary" && message.GetProperty("text").GetString()!.Any(character => character > 255))) throw new InvalidDataException("터미널 입력 형식 또는 크기가 잘못되었습니다.");
                    break;
                case "output-ack": LaunchDescriptor.RequireKeys(message, "v", "seq", "type", "id"); PositiveId(message); break;
                case "paste-result":
                    LaunchDescriptor.RequireKeys(message, "v", "seq", "type", "id", "ok", "reason"); PositiveId(message);
                    if (message.GetProperty("ok").ValueKind is not (JsonValueKind.True or JsonValueKind.False) || message.GetProperty("reason").ValueKind != JsonValueKind.String || message.GetProperty("reason").GetString() is not ("" or "not-ready" or "invalid" or "blocked")) throw new InvalidDataException("붙여넣기 응답 형식이 잘못되었습니다.");
                    break;
                case "diagnostic":
                    LaunchDescriptor.RequireKeys(message, "v", "seq", "type", "code");
                    if (message.GetProperty("code").ValueKind != JsonValueKind.String || message.GetProperty("code").GetString() is not ("input-overflow" or "protocol" or "paste-not-ready")) throw new InvalidDataException("진단 코드가 잘못되었습니다.");
                    break;
                default: throw new InvalidDataException("허용되지 않는 터미널 메시지입니다.");
            }
            incomingSequence = sequence;
            if (type == "input")
            {
                if (inputDelivery is { IsCompleted: false }) throw new InvalidDataException("이전 터미널 입력의 확인을 기다려야 합니다.");
                inputDelivery = DeliverInputAsync(message.Clone());
            }
            else if (!incoming.Writer.TryWrite((currentGeneration, message.Clone()))) throw new InvalidDataException("터미널 메시지 대기열이 가득 찼습니다. 입력을 자동 재전송하지 않습니다.");
        }
        catch (Exception error) when (error is JsonException or InvalidDataException or InvalidOperationException or EncoderFallbackException or FormatException)
        {
            BlockInput(error.Message);
        }
    }

    private async Task DeliverInputAsync(JsonElement message)
    {
        try
        {
            if (!IsRunning || inputBlocked) throw new InvalidOperationException("OMP 입력이 현재 차단되어 있습니다.");
            bridge?.ObserveTerminalInput();
            await session!.SendInputAsync(message.GetProperty("text").GetString()!, message.GetProperty("encoding").GetString() == "binary");
            Post(new { v = 1, seq = NextSequence(), type = "input-ack", id = PositiveId(message) });
        }
        catch (Exception error) { BlockInput(error.Message); }
    }

    private async Task ProcessMessagesAsync()
    {
        try
        {
            await foreach (var entry in incoming.Reader.ReadAllAsync(lifetime.Token))
            {
                if (entry.Generation != generation) continue;
                JsonElement message = entry.Message;
                try
                {
                    switch (message.GetProperty("type").GetString())
                    {
                        case "ready":
                            if (ready is null || ready.Task.IsCompleted) throw new InvalidDataException("터미널 준비 메시지가 중복되었습니다.");
                            rendererHealthy = true;
                            ready.TrySetResult(Dimensions(message));
                            break;
                        case "resize":
                            var size = Dimensions(message); session?.Resize(size.Columns, size.Rows); break;
                        case "output-ack":
                            if (outputAcknowledged is null || outputAcknowledged.Task.IsCompleted || PositiveId(message) != outputId) throw new InvalidDataException("출력 확인 식별자가 일치하지 않습니다.");
                            outputAcknowledged.TrySetResult(); break;
                        case "paste-result":
                            if (pasteAcknowledged is null || PositiveId(message) != pasteId) throw new InvalidDataException("붙여넣기 확인 식별자가 일치하지 않습니다.");
                            var pending = pasteAcknowledged; pasteAcknowledged = null;
                            if (message.GetProperty("ok").GetBoolean()) pending.TrySetResult();
                            else pending.TrySetException(new InvalidOperationException("OMP의 bracketed-paste 입력란이 아직 준비되지 않았습니다. 직접 입력란으로 이동한 뒤 다시 선택하세요. Enter는 보내지 않았습니다."));
                            break;
                        case "diagnostic":
                            if (message.GetProperty("code").GetString() == "paste-not-ready") ReportError("OMP 입력란이 안전한 붙여넣기를 받을 준비가 되지 않았습니다. Enter를 보내지 않았습니다.");
                            else BlockInput("터미널 입력 또는 메시지 경계를 확인하지 못했습니다. 입력은 자동 재전송하지 않습니다.");
                            break;
                    }
                }
                catch (Exception error) when (error is IOException or InvalidDataException or InvalidOperationException or System.ComponentModel.Win32Exception or ChannelClosedException or EncoderFallbackException)
                {
                    BlockInput(error.Message);
                }
            }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
    }

    private async Task PumpOutputAsync(ConPtySession owned, CancellationToken cancellation)
    {
        try
        {
            bool announced = false;
            await foreach (string text in owned.Output.ReadAllAsync(cancellation))
            {
                outputId = ++nextId;
                var ack = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                outputAcknowledged = ack;
                if (!rendererHealthy) { ReportError("터미널 표시가 응답하지 않습니다. 출력은 유한 대기열에서 보존되며 OMP는 계속 소유합니다."); await Task.Delay(Timeout.InfiniteTimeSpan, cancellation); }
                if (!announced) { Post(new { v = 1, seq = NextSequence(), type = "state", running = owned.IsRunning }); announced = true; }
                Post(new { v = 1, seq = NextSequence(), type = "output", id = outputId, text });
                try { await ack.Task.WaitAsync(TimeSpan.FromSeconds(20), cancellation); }
                catch (TimeoutException)
                {
                    ReportError("터미널 출력 확인이 지연됩니다. 출력을 재전송하거나 OMP를 종료하지 않고 흐름을 대기합니다.");
                    // Do not replay output (it may already have affected terminal state).
                    await ack.Task.WaitAsync(cancellation);
                }
                outputAcknowledged = null;
            }
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception error) { RendererFailure("터미널 출력 전달이 중단되었습니다. 소유한 OMP와 파일은 보존됩니다. " + error.Message); }
    }


    private long NextSequence() => ++outgoingSequence;
    private bool Post(object message)
    {
        Dispatcher.VerifyAccess();
        try
        {
            if (view?.CoreWebView2 is null) throw new InvalidOperationException("터미널 표시 연결이 없습니다.");
            view.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(message));
            return true;
        }
        catch (Exception error) when (error is COMException or InvalidOperationException or ObjectDisposedException)
        {
            RendererFailure("터미널 표시 연결에 쓰지 못했습니다. 자식·파일을 보존하며 내용을 재전송하지 않습니다. " + error.Message);
            return false;
        }
    }
    private static (short Columns, short Rows) Dimensions(JsonElement message)
    {
        if (!message.GetProperty("cols").TryGetInt16(out short columns) || !message.GetProperty("rows").TryGetInt16(out short rows) || columns is < 2 or > 500 || rows is < 1 or > 300) throw new InvalidDataException("터미널 셀 크기 범위가 잘못되었습니다.");
        return (columns, rows);
    }
    private static long PositiveId(JsonElement message)
    {
        if (!message.GetProperty("id").TryGetInt64(out long id) || id <= 0 || id > 9_007_199_254_740_991) throw new InvalidDataException("터미널 메시지 식별자가 잘못되었습니다.");
        return id;
    }
    private void SessionStatusChanged(object? sender, EventArgs args) => Dispatcher.BeginInvoke(() =>
    {
        if (disposed || !ReferenceEquals(sender, session)) return;
        bridge?.NativeStatusChanged();
        if (session?.IsRunning == true)
        {
            if (!inputBlocked && rendererHealthy) SetStatus("실제 OMP 실행 중 (PID " + OwnedProcessId + "). 창을 닫기 전에 OMP 안에서 직접 종료하세요. 저장된 답변은 문서 생성·승인이 아닙니다.");
        }
        else
        {
            SetStatus("소유한 OMP가 종료되었습니다. 종료 코드: " + LastExitCode + ". 준비·세션 파일은 보존됩니다.");
            if (rendererHealthy) Post(new { v = 1, seq = NextSequence(), type = "state", running = false });
        }
        StatusChanged?.Invoke(this, EventArgs.Empty);
    });
    private void SessionErrorRaised(object? sender, string message) => Dispatcher.BeginInvoke(() =>
    {
        if (!disposed && ReferenceEquals(sender, session)) BlockInput(message);
    });
    private void SetStatus(string message) { diagnostic.Text = message; }
    private void ReportError(string message)
    {
        diagnostic.Text = message;
        ErrorRaised?.Invoke(this, message);
        StatusChanged?.Invoke(this, EventArgs.Empty);
    }
    private void BlockInput(string message)
    {
        if (disposed) return;
        inputBlocked = true;
        startupCancellation?.Cancel();
        if (rendererHealthy) Post(new { v = 1, seq = NextSequence(), type = "blocked" });
        ReportError(message + (IsRunning ? " 소유한 OMP는 종료하지 않았습니다. 실행 상태가 불확실하면 창을 닫거나 보고서를 바꾸지 마세요." : " 실행을 자동 재시도하지 않습니다."));
    }
    private void RendererFailure(string message)
    {
        if (disposed) return;
        rendererHealthy = false;
        inputBlocked = true;
        startupCancellation?.Cancel();
        ready?.TrySetException(new IOException(message));
        pasteAcknowledged?.TrySetException(new IOException(message + " 붙여넣기를 재전송하지 않습니다."));
        ReportError(message);
    }

    public async ValueTask DisposeAsync()
    {
        Dispatcher.VerifyAccess();
        if (disposed) return;
        if (starting || IsRunning) throw new InvalidOperationException("실행 중이거나 시작 중인 OMP 터미널은 닫지 않습니다. OMP 안에서 먼저 종료하세요.");
        disposed = true;
        lifetime.Cancel();
        outputAcknowledged?.TrySetCanceled(); pasteAcknowledged?.TrySetCanceled();
        incoming.Writer.TryComplete();
        if (session is not null) await session.DisposeAsync();
        if (bridge is not null) await bridge.DisposeAsync();
        if (outputTask is not null) await outputTask;
        await messageTask;
        if (inputDelivery is not null) await inputDelivery;
        outputLifetime?.Dispose();
        view?.Dispose(); lifetime.Dispose();
    }

    [StructLayout(LayoutKind.Sequential)] private struct GuiThreadInfo
    {
        public int Size; public uint Flags; public nint Active; public nint Focus; public nint Capture; public nint MenuOwner; public nint MoveSize; public nint Caret;
        public int Left; public int Top; public int Right; public int Bottom;
    }
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetGUIThreadInfo(uint thread, ref GuiThreadInfo information);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsChild(nint parent, nint child);
}
