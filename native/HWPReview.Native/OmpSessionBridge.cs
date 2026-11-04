using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using System.Windows.Threading;

namespace HWPReview.Native;

internal sealed record OmpAgentState(bool Connected, string SessionId, string Phase, bool CanSend, string Provider, string Model, string Detail);
internal sealed record OmpAgentEvent(string Kind, string SessionId, string RequestId, string ContextSha256, string RunId, string Text, string StopReason);

internal sealed record OmpBridgeDescriptor(string PipeName, string Token, string ExtensionPath, string ExtensionSha256)
{
    internal static OmpBridgeDescriptor Parse(JsonElement value, string launchId, string root)
    {
        LaunchDescriptor.RequireKeys(value, "pipe_name", "token", "extension_path", "extension_sha256");
        string pipe = OmpSessionBridge.Text(value, "pipe_name", 128), token = OmpSessionBridge.Text(value, "token", 64);
        string extension = OmpSessionBridge.Text(value, "extension_path", 32760), hash = OmpSessionBridge.Text(value, "extension_sha256", 64);
        if (pipe != "HwpReview.Omp." + launchId || !OmpSessionBridge.LowerHex(token, 64) || !OmpSessionBridge.LowerHex(hash, 64) ||
            !string.Equals(Path.GetFileName(root), launchId, StringComparison.Ordinal) ||
            !string.Equals(extension, Path.Combine(root, "omp-bridge.mjs"), StringComparison.Ordinal))
            throw new InvalidDataException("앱 소유 OMP 연결 식별자와 파일 배치가 다릅니다.");
        LaunchDescriptor.RequirePath(extension, false);
        return new(pipe, token, extension, hash);
    }
}

// This is a control/public-text projection of the already-owned native TUI,
// never an AgentSession, document executor, terminal emulator, or process owner.
internal sealed class OmpSessionBridge : IAsyncDisposable
{
    private const int LineLimit = 1_048_576;
    private const int PublicLimit = 262_144;
    private static readonly UTF8Encoding Utf8 = new(false, true);
    private readonly LaunchDescriptor launch;
    private readonly Dispatcher dispatcher;
    private readonly Func<bool> nativeRunning;
    private readonly NamedPipeServerStream pipe;
    private readonly CancellationTokenSource lifetime = new();
    private readonly Channel<byte[]> outgoing = Channel.CreateBounded<byte[]>(new BoundedChannelOptions(32) { SingleReader = true, SingleWriter = true, FullMode = BoundedChannelFullMode.Wait });
    private readonly Task readerTask;
    private Task? writerTask;
    private long incomingSequence, outgoingSequence;
    private bool authenticated, failed, uncertain, shutdownObserved, nativeExited, disposed, initialStatePending;
    private string requestId = "", contextHash = "", runId = "", messageId = "";
    private int publicBytes, streamedBytes, messageCount;
    private long lastRun, lastMessage;
    private long submissionGeneration;
    private PendingCommand? pending;
    private sealed record PendingCommand(long Sequence, string Type, string RequestId, string ContextHash, TaskCompletionSource<string> Completion);
    public OmpAgentState State { get; private set; } = new(false, "", "starting", false, "", "", "실제 OMP와 앱 소유 연결을 시작합니다.");
    public event EventHandler<OmpAgentEvent>? AgentEvent;

    public OmpSessionBridge(LaunchDescriptor launch, Dispatcher dispatcher, Func<bool> nativeRunning)
    {
        this.launch = launch; this.dispatcher = dispatcher; this.nativeRunning = nativeRunning;
        // The endpoint exists before CreateProcess/ConPTY child creation. Exactly
        // one same-user peer is accepted; there is no reconnect or fallback port.
        pipe = new NamedPipeServerStream(launch.Bridge.PipeName, PipeDirection.InOut, 1, PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly, 8192, 8192);
        readerTask = ReadAsync();
    }

    public void NativeStatusChanged()
    {
        dispatcher.VerifyAccess();
        if (nativeRunning()) { PublishState(State); return; }
        nativeExited = true;
        StopTransport();
        if (pending?.Type == "shutdown") pending.Completion.TrySetResult("exited");
        else pending?.Completion.TrySetException(new IOException("소유한 OMP가 종료되어 요청을 재전송하지 않습니다."));
        PublishState(State with { Connected = false, CanSend = false, Phase = "closed", Detail = "소유한 OMP 프로세스의 실제 종료를 확인했습니다." });
    }

    public void ObserveTerminalInput()
    {
        dispatcher.VerifyAccess();
        // Even editor keystrokes are conservative evidence: public OMP events
        // have no request IDs and cannot prove exclusive concurrent origin.
        if (requestId.Length != 0) Fault("GUI 요청 중 실제 터미널 입력이 관찰되어 답변 연결을 해제했습니다. 자동 재전송하지 않습니다.", false);
    }

    public async Task SendPromptAsync(string id, string hash, string text)
    {
        dispatcher.VerifyAccess();
        if (!State.CanSend || pending is not null || !nativeRunning() || requestId.Length != 0 || runId.Length != 0) throw new InvalidOperationException("OMP가 알려진 대기 상태가 아니거나 계정·모델 설정이 필요합니다.");
        if (!Identifier(id) || !LowerHex(hash, 64) || !IsPublic(text, 32768, false)) throw new InvalidDataException("OMP 요청 식별자·문맥 해시·32KiB 공개 텍스트 범위를 확인하세요.");
        requestId = id; contextHash = hash;
        long generation = ++submissionGeneration;
        PublishState(State with { Phase = "submitting", CanSend = false, Detail = "명시적 GUI 요청을 한 번 전송합니다. 실행 수락 여부는 아직 확인하지 않았습니다." });
        _ = ObserveAdmissionAsync(generation);
        await CommandAsync("send", id, hash, text);
    }

    public Task AbortAsync()
    {
        dispatcher.VerifyAccess();
        if (!authenticated || failed || !nativeRunning()) throw new InvalidOperationException("정상 OMP 제어 연결이 없어 중단을 요청할 수 없습니다. 실제 터미널을 확인하세요.");
        if (pending is not null || State.Phase == "idle") throw new InvalidOperationException("현재 중단을 요청할 실행이 없거나 이전 제어 요청을 기다리고 있습니다.");
        return CommandAsync("abort");
    }

    public Task ShutdownAsync()
    {
        dispatcher.VerifyAccess();
        if (!authenticated || failed || uncertain || pending is not null || !nativeRunning() || State.Phase != "idle" || requestId.Length != 0 || runId.Length != 0)
            throw new InvalidOperationException("알려진 대기 상태에서만 정상 종료를 요청합니다. 실제 터미널에서 실행·대기 메시지를 확인하세요.");
        PublishState(State with { Phase = "settling", CanSend = false, Detail = "OMP 정상 종료를 요청합니다. 실제 프로세스 종료 전에는 해제하지 않습니다." });
        return CommandAsync("shutdown");
    }

    private async Task CommandAsync(string type, string id = "", string hash = "", string text = "")
    {
        if (pending is not null) throw new InvalidOperationException("이전 OMP 제어 요청의 확인을 기다리고 있습니다. 자동 재시도하지 않습니다.");
        if (!authenticated || failed || !nativeRunning()) throw new InvalidOperationException("정상 OMP 제어 연결이 없습니다.");
        long sequence = NextSequence();
        var completion = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var command = new PendingCommand(sequence, type, id, hash, completion);
        pending = command;
        try
        {
            if (type == "send") Queue(new { v = 1, seq = sequence, type, session_id = State.SessionId, request_id = id, context_sha256 = hash, text });
            else Queue(new { v = 1, seq = sequence, type, session_id = State.SessionId });
            string status = await completion.Task.WaitAsync(TimeSpan.FromSeconds(15));
            if (status == "rejected") throw new InvalidOperationException("OMP가 요청을 수락하지 않았습니다. 실행 여부를 실제 터미널에서 확인하고 자동 재전송하지 마세요.");
        }
        catch
        {
            Fault("OMP 요청의 전달·수락 결과가 불확실합니다. 재연결·재전송 없이 실제 터미널과 파일을 보존합니다.", true);
            throw;
        }
        finally { if (ReferenceEquals(pending, command)) pending = null; }
    }

    private async Task ObserveAdmissionAsync(long generation)
    {
        try
        {
            await Task.Delay(TimeSpan.FromSeconds(15), lifetime.Token);
            await dispatcher.InvokeAsync(() =>
            {
                if (generation == submissionGeneration && requestId.Length != 0 && runId.Length == 0 && !nativeExited)
                    Fault("전송 확인 후 OMP 실행 수락을 관찰하지 못했습니다. 비용·완료를 추정하거나 요청을 재시도하지 않습니다.", false);
            });
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
    }

    private async Task ReadAsync()
    {
        try
        {
            await pipe.WaitForConnectionAsync(lifetime.Token).WaitAsync(TimeSpan.FromSeconds(30));
            writerTask = WriteAsync();
            byte[] line = new byte[LineLimit];
            byte[] chunk = new byte[8192];
            int used = 0;
            long lastFrameAt = Stopwatch.GetTimestamp();
            while (!lifetime.IsCancellationRequested)
            {
                TimeSpan remaining = TimeSpan.FromSeconds(15) - Stopwatch.GetElapsedTime(lastFrameAt);
                if (remaining <= TimeSpan.Zero) throw new TimeoutException();
                int count = await pipe.ReadAsync(chunk, lifetime.Token).AsTask().WaitAsync(remaining, lifetime.Token);
                if (count == 0) throw new EndOfStreamException();
                int start = 0;
                for (int index = 0; index < count; index++)
                {
                    if (chunk[index] != 10) continue;
                    int length = index - start;
                    if (used + length >= LineLimit) throw new InvalidDataException("frame");
                    chunk.AsSpan(start, length).CopyTo(line.AsSpan(used)); used += length;
                    if (used == 0) throw new InvalidDataException("frame");
                    string raw = Utf8.GetString(line, 0, used);
                    using JsonDocument document = JsonDocument.Parse(raw, new JsonDocumentOptions { MaxDepth = 3 });
                    JsonElement frame = document.RootElement;
                    await dispatcher.InvokeAsync(() => Receive(frame));
                    lastFrameAt = Stopwatch.GetTimestamp();
                    used = 0; start = index + 1;
                }
                int remainder = count - start;
                if (used + remainder >= LineLimit) throw new InvalidDataException("frame");
                chunk.AsSpan(start, remainder).CopyTo(line.AsSpan(used)); used += remainder;
            }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
        catch (ObjectDisposedException) when (lifetime.IsCancellationRequested) { }
        catch (Exception)
        {
            await dispatcher.InvokeAsync(() =>
            {
                if (!nativeExited && !disposed && !shutdownObserved) Fault("OMP 공개 연결이 끊겼거나 식별자·순서·크기 경계를 확인하지 못했습니다. 실제 터미널은 종료하지 않습니다.", true);
                else
                {
                    StopTransport();
                    if (shutdownObserved && !nativeExited && !disposed) PublishState(State with { Connected = false, CanSend = false });
                }
            });
        }
    }

    private async Task WriteAsync()
    {
        try
        {
            await foreach (byte[] frame in outgoing.Reader.ReadAllAsync(lifetime.Token))
                await pipe.WriteAsync(frame, lifetime.Token).AsTask().WaitAsync(TimeSpan.FromSeconds(15), lifetime.Token);
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
        catch (Exception)
        {
            await dispatcher.InvokeAsync(() => { if (!nativeExited && !disposed && !shutdownObserved) Fault("OMP 제어 요청을 쓰지 못했습니다. 자동 재전송하지 않습니다.", true); });
        }
    }

    private void Receive(JsonElement frame)
    {
        dispatcher.VerifyAccess();
        if (failed || disposed || nativeExited) return;
        if (frame.ValueKind != JsonValueKind.Object || !frame.TryGetProperty("v", out JsonElement version) || !version.TryGetInt32(out int v) || v != 1 ||
            !frame.TryGetProperty("seq", out JsonElement order) || !order.TryGetInt64(out long sequence) || sequence != incomingSequence + 1 || sequence > 9_007_199_254_740_991)
            throw new InvalidDataException("sequence");
        string type = Text(frame, "type", 32);
        if (!authenticated)
        {
            LaunchDescriptor.RequireKeys(frame, "v", "seq", "type", "launch_id", "token", "session_id", "cwd");
            string session = Text(frame, "session_id", 128), cwd = Text(frame, "cwd", 32760);
            if (type != "hello" || Text(frame, "launch_id", 32) != launch.LaunchId || Text(frame, "token", 64) != launch.Bridge.Token || !Identifier(session) ||
                !string.Equals(cwd, launch.WorkingDirectory, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("identity");
            incomingSequence = sequence; authenticated = true;
            PublishState(State with { Connected = true, SessionId = session, Phase = "starting", CanSend = false });
            Queue(new { v = 1, seq = NextSequence(), type = "welcome", launch_id = launch.LaunchId, session_id = session, cwd = launch.WorkingDirectory });
            initialStatePending = true;
            Queue(new { v = 1, seq = NextSequence(), type = "state", session_id = session });
            return;
        }
        if (Text(frame, "session_id", 128) != State.SessionId) throw new InvalidDataException("session");
        switch (type)
        {
            case "state": ReceiveState(frame); break;
            case "ack": ReceiveAck(frame); break;
            case "event": ReceiveEvent(frame); break;
            default: throw new InvalidDataException("type");
        }
        incomingSequence = sequence;
    }

    private void ReceiveState(JsonElement frame)
    {
        LaunchDescriptor.RequireKeys(frame, "v", "seq", "type", "session_id", "phase", "can_send", "provider", "model", "detail");
        string phase = Text(frame, "phase", 16), provider = Text(frame, "provider", 128), model = Text(frame, "model", 256), detail = Text(frame, "detail", 1024);
        if (phase is not ("starting" or "idle" or "submitting" or "running" or "settling" or "uncertain") || frame.GetProperty("can_send").ValueKind is not (JsonValueKind.True or JsonValueKind.False)) throw new InvalidDataException("state");
        bool canSend = frame.GetProperty("can_send").GetBoolean();
        if (canSend && (phase != "idle" || provider.Length == 0 || model.Length == 0)) throw new InvalidDataException("readiness");
        if (phase == "uncertain" && !uncertain) Fault("OMP가 요청·실행 연결을 확정하지 못했습니다. 자동 재시도하지 않습니다.", false);
        if (uncertain) phase = "uncertain";
        else if (requestId.Length != 0 && runId.Length == 0) phase = "submitting";
        else if (runId.Length != 0 && phase == "idle") throw new InvalidDataException("settlement");
        PublishState(new(authenticated && !failed, State.SessionId, phase,
            canSend && !uncertain && nativeRunning() && requestId.Length == 0 && runId.Length == 0, provider, model,
            uncertain ? State.Detail : detail));
    }

    private void ReceiveAck(JsonElement frame)
    {
        LaunchDescriptor.RequireKeys(frame, "v", "seq", "type", "session_id", "command_seq", "request_id", "context_sha256", "status");
        if (!frame.GetProperty("command_seq").TryGetInt64(out long sequence)) throw new InvalidDataException("ack");
        string id = Text(frame, "request_id", 128), hash = Text(frame, "context_sha256", 64), status = Text(frame, "status", 16);
        // The one startup state query has no user operation to complete.
        if (initialStatePending && sequence == 2 && id.Length == 0 && hash.Length == 0 && status == "observed") { initialStatePending = false; return; }
        if (pending is null || pending.Sequence != sequence || pending.RequestId != id || pending.ContextHash != hash || pending.Completion.Task.IsCompleted) throw new InvalidDataException("ack");
        if (pending.Type == "send" ? status is not ("dispatched" or "rejected") : status is not ("requested" or "rejected")) throw new InvalidDataException("ack");
        pending.Completion.TrySetResult(status);
    }

    private void ReceiveEvent(JsonElement frame)
    {
        LaunchDescriptor.RequireKeys(frame, "v", "seq", "type", "session_id", "kind", "request_id", "context_sha256", "run_id", "message_id", "text", "stop_reason");
        string kind = Text(frame, "kind", 32), id = Text(frame, "request_id", 128), hash = Text(frame, "context_sha256", 64);
        string run = Text(frame, "run_id", 128), message = Text(frame, "message_id", 128), text = Text(frame, "text", PublicLimit), reason = Text(frame, "stop_reason", 16);
        if ((id.Length == 0) != (hash.Length == 0) || id.Length != 0 && (!Identifier(id) || !LowerHex(hash, 64) || id != requestId || hash != contextHash) ||
            run.Length != 0 && !Identifier(run) || message.Length != 0 && !Identifier(message)) throw new InvalidDataException("binding");
        if (kind is not ("run_start" or "assistant_start" or "assistant_delta" or "assistant_end" or "run_end" or "fault" or "shutdown")) throw new InvalidDataException("event");
        if (kind is not ("assistant_delta" or "assistant_end") && (text.Length != 0 || reason.Length != 0) || kind == "assistant_delta" && reason.Length != 0) throw new InvalidDataException("projection");
        if (kind is "fault" or "shutdown")
        {
            if (message.Length != 0) throw new InvalidDataException("message");
            if (kind == "fault") Fault("OMP 공개 실행 연결이 불확실해졌습니다. 자동 검사·재전송을 중지하고 실제 터미널을 보존합니다.", false);
            else
            {
                shutdownObserved = true;
                if (pending?.Type == "shutdown") pending.Completion.TrySetResult("requested");
                PublishState(State with { CanSend = false, Phase = uncertain ? "uncertain" : "settling", Detail = "OMP 정상 종료 이벤트를 관찰했습니다. 실제 프로세스 종료를 기다립니다." });
                Emit(new(kind, State.SessionId, "", "", run, "", ""));
            }
            return;
        }
        if (kind == "run_start")
        {
            if (run != "run-" + (lastRun + 1) || runId.Length != 0 || message.Length != 0) throw new InvalidDataException("run");
            lastRun++;
            runId = run; publicBytes = 0; messageCount = 0;
            if (requestId.Length != 0 && id.Length == 0) Fault("GUI 요청과 실제 터미널 실행의 연결이 일치하지 않아 자동 검사를 중지합니다.", false);
            PublishState(State with { Phase = uncertain ? "uncertain" : "running", CanSend = false });
        }
        else if (run.Length == 0 || run != runId) throw new InvalidDataException("run");
        if (kind == "assistant_start")
        {
            if (message != "message-" + (lastMessage + 1) || messageId.Length != 0 || ++messageCount > 128) throw new InvalidDataException("message");
            lastMessage++;
            messageId = message; streamedBytes = 0;
        }
        else if (kind is "assistant_delta" or "assistant_end")
        {
            if (messageId.Length == 0 || message != messageId || kind == "assistant_delta" && Utf8.GetByteCount(text) > 16384) throw new InvalidDataException("message");
            int bytes = Utf8.GetByteCount(text);
            if (kind == "assistant_delta") streamedBytes = checked(streamedBytes + bytes);
            else publicBytes = checked(publicBytes + bytes);
            if (checked(publicBytes + (kind == "assistant_delta" ? streamedBytes : 0)) > PublicLimit ||
                kind == "assistant_end" && reason is not ("stop" or "length" or "toolUse" or "error" or "aborted")) throw new InvalidDataException("public bound");
            if (kind == "assistant_end") { messageId = ""; streamedBytes = 0; }
        }
        else if (kind == "run_end" && (message.Length != 0 || messageId.Length != 0)) throw new InvalidDataException("settlement");
        if (uncertain) { id = ""; hash = ""; }
        Emit(new(kind, State.SessionId, id, hash, run, text, reason));
        if (kind == "run_end")
        {
            requestId = ""; contextHash = ""; runId = ""; ++submissionGeneration;
            PublishState(State with { CanSend = false, Phase = uncertain ? "uncertain" : "settling" });
        }
    }

    private long NextSequence()
    {
        if (outgoingSequence >= 9_007_199_254_740_991) throw new InvalidDataException("OMP 제어 순서 범위를 초과했습니다.");
        return ++outgoingSequence;
    }


    private void Queue(object value)
    {
        byte[] frame = Utf8.GetBytes(JsonSerializer.Serialize(value) + "\n");
        if (frame.Length > LineLimit || !outgoing.Writer.TryWrite(frame)) throw new IOException("OMP 제어 대기열·메시지 범위를 초과했습니다. 자동 재전송하지 않습니다.");
    }

    private void Fault(string detail, bool disconnect)
    {
        dispatcher.VerifyAccess();
        if (nativeExited || disposed) return;
        bool announce = !uncertain;
        uncertain = true;
        PublishState(State with { Connected = authenticated && !disconnect && !failed, CanSend = false, Phase = "uncertain", Detail = detail });
        if (announce) Emit(new("fault", State.SessionId, "", "", runId, "", ""));
        if (disconnect)
        {
            failed = true;
            pending?.Completion.TrySetException(new IOException(detail));
            StopTransport();
        }
    }

    private void StopTransport()
    {
        lifetime.Cancel(); outgoing.Writer.TryComplete(); pipe.Dispose();
    }
    private void PublishState(OmpAgentState state)
    {
        State = state with { CanSend = state.CanSend && nativeRunning() && !uncertain && !failed && !nativeExited };
        Emit(new("state", State.SessionId, "", "", "", "", ""));
    }
    private void Emit(OmpAgentEvent value) => AgentEvent?.Invoke(this, value);

    internal static bool LowerHex(string value, int length) => value.Length == length && value.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f');
    private static bool Identifier(string value) => value.Length is > 0 and <= 128 && char.IsAsciiLetterOrDigit(value[0]) && value.All(c => char.IsAsciiLetterOrDigit(c) || c is '.' or '_' or '-');
    private static bool IsPublic(string text, int max, bool empty = true)
    {
        if (!empty && text.Length == 0 || text.Any(c => (char.IsControl(c) && c is not ('\r' or '\n' or '\t')))) return false;
        try { return Utf8.GetByteCount(text) <= max; } catch (EncoderFallbackException) { return false; }
    }
    internal static string Text(JsonElement value, string key, int max)
    {
        if (!value.TryGetProperty(key, out JsonElement item) || item.ValueKind != JsonValueKind.String) throw new InvalidDataException("string");
        string text = item.GetString()!;
        if (!IsPublic(text, max)) throw new InvalidDataException("text");
        return text;
    }

    public async ValueTask DisposeAsync()
    {
        dispatcher.VerifyAccess();
        if (nativeRunning()) throw new InvalidOperationException("실제 OMP 종료 전에는 공개 연결의 소유권을 해제하지 않습니다.");
        if (disposed) return;
        disposed = true; StopTransport();
        await readerTask;
        if (writerTask is not null) await writerTask;
        lifetime.Dispose();
    }
}
