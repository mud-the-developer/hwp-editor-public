using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using System.Windows.Threading;

namespace HWPReview.Native;

internal sealed record OmpAgentState(bool Connected, string SessionId, string Phase, bool IsIdle, string Provider, string Model, string Detail);
internal sealed record OmpAgentEvent(string Kind, string SessionId, string RunId);

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

// Lightweight lifecycle bridge for the owned native OMP TUI:
// observes provider/model metadata, known-idle state, abort, and clean shutdown.
internal sealed class OmpSessionBridge : IAsyncDisposable
{
    private const int LineLimit = 1_048_576;
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
    private string runId = "";
    private long lastRun;
    private PendingCommand? pending;
    private sealed record PendingCommand(long Sequence, string Type, TaskCompletionSource<string> Completion);
    public OmpAgentState State { get; private set; } = new(false, "", "starting", false, "", "", "OMP 라이프사이클 연결을 시작합니다.");
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
        PublishState(State with { Connected = false, IsIdle = false, Phase = "closed", Detail = "OMP 프로세스가 종료되었습니다." });
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
        if (!authenticated || failed || uncertain || pending is not null || !nativeRunning() || !State.IsIdle || runId.Length != 0)
            throw new InvalidOperationException("알려진 대기 상태에서만 정상 종료를 요청합니다. 실제 터미널에서 실행·대기 메시지를 확인하세요.");
        PublishState(State with { Phase = "settling", IsIdle = false, Detail = "OMP 정상 종료를 요청합니다. 실제 프로세스 종료 전에는 해제하지 않습니다." });
        return CommandAsync("shutdown");
    }

    private async Task CommandAsync(string type)
    {
        if (pending is not null) throw new InvalidOperationException("이전 OMP 제어 요청의 확인을 기다리고 있습니다. 자동 재시도하지 않습니다.");
        if (!authenticated || failed || !nativeRunning()) throw new InvalidOperationException("정상 OMP 제어 연결이 없습니다.");
        long sequence = NextSequence();
        var completion = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var command = new PendingCommand(sequence, type, completion);
        pending = command;
        try
        {
            Queue(new { v = 1, seq = sequence, type, session_id = State.SessionId });
            string status = await completion.Task.WaitAsync(TimeSpan.FromSeconds(15));
            if (status == "rejected") throw new InvalidOperationException("OMP가 요청을 수락하지 않았습니다. 실제 터미널에서 상태를 확인하세요.");
        }
        catch
        {
            Fault("OMP 제어 요청의 결과를 확인하지 못했습니다. 재연결·강제 종료 없이 터미널을 보존합니다.", true);
            throw;
        }
        finally { if (ReferenceEquals(pending, command)) pending = null; }
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
                if (!nativeExited && !disposed && !shutdownObserved) Fault("OMP 라이프사이클 연결이 끊겼습니다. 실제 터미널은 종료하지 않습니다.", true);
                else
                {
                    StopTransport();
                    if (shutdownObserved && !nativeExited && !disposed) PublishState(State with { Connected = false, IsIdle = false });
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
            PublishState(State with { Connected = true, SessionId = session, Phase = "starting", IsIdle = false });
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
        LaunchDescriptor.RequireKeys(frame, "v", "seq", "type", "session_id", "phase", "is_idle", "provider", "model", "detail");
        string phase = Text(frame, "phase", 16), provider = Text(frame, "provider", 128), model = Text(frame, "model", 256), detail = Text(frame, "detail", 1024);
        if (phase is not ("starting" or "idle" or "submitting" or "running" or "settling" or "uncertain") || frame.GetProperty("is_idle").ValueKind is not (JsonValueKind.True or JsonValueKind.False)) throw new InvalidDataException("state");
        bool isIdle = frame.GetProperty("is_idle").GetBoolean();
        if (isIdle && phase != "idle") throw new InvalidDataException("readiness");
        if (phase == "uncertain" && !uncertain) Fault("OMP 세션 상태가 불확실해졌습니다. 실제 터미널을 직접 확인하세요.", false);
        if (uncertain) phase = "uncertain";
        else if (runId.Length != 0 && phase == "idle") throw new InvalidDataException("settlement");
        PublishState(new(authenticated && !failed, State.SessionId, phase,
            isIdle && !uncertain && nativeRunning() && runId.Length == 0, provider, model,
            uncertain ? State.Detail : detail));
    }

    private void ReceiveAck(JsonElement frame)
    {
        LaunchDescriptor.RequireKeys(frame, "v", "seq", "type", "session_id", "command_seq", "status");
        if (!frame.GetProperty("command_seq").TryGetInt64(out long sequence)) throw new InvalidDataException("ack");
        string status = Text(frame, "status", 16);
        if (initialStatePending && sequence == 2 && status == "observed") { initialStatePending = false; return; }
        if (pending is null || pending.Sequence != sequence || pending.Completion.Task.IsCompleted) throw new InvalidDataException("ack");
        if (status is not ("requested" or "rejected")) throw new InvalidDataException("ack");
        pending.Completion.TrySetResult(status);
    }

    private void ReceiveEvent(JsonElement frame)
    {
        LaunchDescriptor.RequireKeys(frame, "v", "seq", "type", "session_id", "kind", "run_id");
        string kind = Text(frame, "kind", 32), run = Text(frame, "run_id", 128);
        if (run.Length != 0 && !Identifier(run)) throw new InvalidDataException("binding");
        if (kind is not ("run_start" or "run_end" or "fault" or "shutdown")) throw new InvalidDataException("event");
        if (kind is "fault" or "shutdown")
        {
            if (kind == "fault") Fault("OMP 라이프사이클 연결이 불확실해졌습니다. 실제 터미널을 보존합니다.", false);
            else
            {
                shutdownObserved = true;
                if (pending?.Type == "shutdown") pending.Completion.TrySetResult("requested");
                PublishState(State with { IsIdle = false, Phase = uncertain ? "uncertain" : "settling", Detail = "OMP 정상 종료 이벤트를 관찰했습니다. 실제 프로세스 종료를 기다립니다." });
                Emit(new(kind, State.SessionId, run));
            }
            return;
        }
        if (kind == "run_start")
        {
            if (run != "run-" + (lastRun + 1) || runId.Length != 0) throw new InvalidDataException("run");
            lastRun++;
            runId = run;
            PublishState(State with { Phase = uncertain ? "uncertain" : "running", IsIdle = false });
        }
        else if (run.Length == 0 || run != runId) throw new InvalidDataException("run");
        Emit(new(kind, State.SessionId, run));
        if (kind == "run_end")
        {
            runId = "";
            PublishState(State with { IsIdle = false, Phase = uncertain ? "uncertain" : "settling" });
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
        PublishState(State with { Connected = authenticated && !disconnect && !failed, IsIdle = false, Phase = "uncertain", Detail = detail });
        if (announce) Emit(new("fault", State.SessionId, runId));
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
        State = state with { IsIdle = state.IsIdle && nativeRunning() && !uncertain && !failed && !nativeExited };
        Emit(new("state", State.SessionId, ""));
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
        if (nativeRunning()) throw new InvalidOperationException("실제 OMP 종료 전에는 라이프사이클 연결의 소유권을 해제하지 않습니다.");
        if (disposed) return;
        disposed = true; StopTransport();
        await readerTask;
        if (writerTask is not null) await writerTask;
        lifetime.Dispose();
    }
}
