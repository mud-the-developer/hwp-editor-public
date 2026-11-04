using System.Buffers;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using Microsoft.Win32.SafeHandles;

namespace HWPReview.Native;

// The process handle, never a serialized PID, is the lifetime authority.
internal sealed class ConPtySession : IAsyncDisposable
{
    private const uint Infinite = 0xffffffff;
    private static readonly UTF8Encoding Utf8 = new(false, true);
    private readonly Channel<(string Text, bool Binary)> input = Channel.CreateBounded<(string Text, bool Binary)>(new BoundedChannelOptions(32) { SingleReader = true, SingleWriter = true, FullMode = BoundedChannelFullMode.Wait });
    private readonly Channel<string> output = Channel.CreateBounded<string>(new BoundedChannelOptions(32) { SingleReader = true, SingleWriter = true, FullMode = BoundedChannelFullMode.Wait });
    private readonly CancellationTokenSource disposed = new();
    private readonly object lifecycle = new();
    private SafeFileHandle? process;
    private List<SafeFileHandle>? runtimeReservations;
    private FileStream? inputPipe;
    private FileStream? outputPipe;
    private nint pseudoConsole;
    private Task? inputTask;
    private Task? outputTask;
    private Task? waitTask;
    private readonly TaskCompletionSource processExited = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private volatile bool running;
    private bool started;
    public bool IsRunning => running;
    public int? OwnedProcessId { get; private set; }
    public int? LastExitCode { get; private set; }
    public ChannelReader<string> Output => output.Reader;
    public event EventHandler? StatusChanged;
    public event EventHandler<string>? ErrorRaised;

    public async Task WaitForExitAsync(TimeSpan timeout)
    {
        if (process is null) throw new InvalidOperationException("소유한 OMP 프로세스가 없습니다.");
        await processExited.Task.WaitAsync(timeout);
    }

    public void Start(LaunchDescriptor launch, short columns, short rows, CancellationToken cancellation)
    {
        if (started) throw new InvalidOperationException("이 터미널 세션은 이미 시작되었습니다.");
        started = true;
        SafeFileHandle? childInput = null, parentInput = null, parentOutput = null, childOutput = null;
        nint attributes = 0, environment = 0;
        bool attributesInitialized = false;
        try
        {
            runtimeReservations = OpenBoundRuntime(launch.Runtime);
            ReserveBridgeFiles(launch.Bridge);
            Check(CreatePipe(out childInput, out parentInput, 0, 0), "입력 파이프 만들기");
            Check(CreatePipe(out parentOutput, out childOutput, 0, 0), "출력 파이프 만들기");
            int result = CreatePseudoConsole(new Coord(columns, rows), childInput, childOutput, 0, out pseudoConsole);
            if (result < 0) { pseudoConsole = 0; Marshal.ThrowExceptionForHR(result); }
            childInput.Dispose(); childInput = null;
            childOutput.Dispose(); childOutput = null;
            inputPipe = new FileStream(parentInput, FileAccess.Write, 1, false); parentInput = null;
            outputPipe = new FileStream(parentOutput, FileAccess.Read, 8192, false); parentOutput = null;
            nuint size = 0;
            InitializeProcThreadAttributeList(0, 1, 0, ref size);
            if (size == 0) throw new Win32Exception(Marshal.GetLastWin32Error(), "ConPTY 시작 정보 크기를 확인하지 못했습니다.");
            attributes = Marshal.AllocHGlobal(checked((nint)size));
            Check(InitializeProcThreadAttributeList(attributes, 1, 0, ref size), "ConPTY 시작 정보 만들기");
            attributesInitialized = true;
            Check(UpdateProcThreadAttribute(attributes, 0, (nuint)0x00020016, pseudoConsole, (nuint)nint.Size, 0, 0), "ConPTY 연결");
            // Console attachment replaces explicit NULL slots; otherwise redirected
            // supervisor stdhandles survive even with handle inheritance disabled.
            var startup = new StartupInfoEx
            {
                StartupInfo = new StartupInfo
                {
                    Size = Marshal.SizeOf<StartupInfoEx>(),
                    Flags = 0x00000100, // STARTF_USESTDHANDLES
                    StandardInput = 0,
                    StandardOutput = 0,
                    StandardError = 0
                },
                AttributeList = attributes
            };
            string block = string.Join('\0', launch.Environment.OrderBy(pair => pair.Key, StringComparer.OrdinalIgnoreCase).Select(pair => pair.Key + "=" + pair.Value)) + "\0\0";
            environment = Marshal.StringToHGlobalUni(block);
            var command = new StringBuilder(Quote(launch.Executable));
            foreach (string argument in launch.Arguments) command.Append(' ').Append(Quote(argument));
            if (command.Length >= 32767) throw new InvalidDataException("OMP 실행 인수가 Windows 제한을 초과했습니다.");
            // No inherited supervisor handles, shell, suspended child, or fallback executable.
            cancellation.ThrowIfCancellationRequested();
            Check(CreateProcessW(launch.Executable, command, 0, 0, false, 0x00080000 | 0x00000400, environment, launch.WorkingDirectory, ref startup, out ProcessInformation info), "OMP 프로세스 시작");
            process = new SafeFileHandle(info.Process, true);
            running = true;
            OwnedProcessId = unchecked((int)info.ProcessId);
            using (var thread = new SafeFileHandle(info.Thread, true)) { }
            // Synchronous anonymous pipes get independent dedicated workers; neither uses host stdio.
            outputTask = Task.Factory.StartNew(DrainOutput, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
            inputTask = Task.Factory.StartNew(WriteInput, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
            waitTask = Task.Factory.StartNew(WaitForExit, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
            StatusChanged?.Invoke(this, EventArgs.Empty);
        }
        catch
        {
            if (process is null)
            {
                if (pseudoConsole != 0) { ClosePseudoConsole(pseudoConsole); pseudoConsole = 0; }
                inputPipe?.Dispose(); outputPipe?.Dispose();
                output.Writer.TryComplete(); input.Writer.TryComplete();
                ReleaseRuntimeReservations();
            }
            // A created child remains owned, even when startup notification/worker setup fails.
            else EnsureExitObserver();
            throw;
        }
        finally
        {
            childInput?.Dispose(); childOutput?.Dispose(); parentInput?.Dispose(); parentOutput?.Dispose();
            if (attributesInitialized) DeleteProcThreadAttributeList(attributes);
            if (attributes != 0) Marshal.FreeHGlobal(attributes);
            if (environment != 0) Marshal.FreeHGlobal(environment);
        }
    }

    public async ValueTask SendInputAsync(string text, bool binary)
    {
        if (!running) throw new InvalidOperationException("실행 중인 OMP가 없습니다.");
        if ((binary ? text.Length : Utf8.GetByteCount(text)) > 131_072) throw new InvalidDataException("터미널 입력 크기 제한을 초과했습니다.");
        if (binary && text.Any(character => character > byte.MaxValue))
            throw new InvalidDataException("이진 터미널 입력 값이 바이트 범위를 초과했습니다.");
        await input.Writer.WriteAsync((text, binary), disposed.Token);
    }

    public void Resize(short columns, short rows)
    {
        lock (lifecycle)
        {
            if (!running || pseudoConsole == 0) return;
            int result = ResizePseudoConsole(pseudoConsole, new Coord(columns, rows));
            if (result < 0) Marshal.ThrowExceptionForHR(result);
        }
    }

    private void DrainOutput()
    {
        // Decoder state crosses ReadFile boundaries; VT sequences are never interpreted or stripped.
        Decoder decoder = new UTF8Encoding(false, false).GetDecoder();
        byte[] bytes = new byte[8192];
        char[] characters = new char[8193];
        bool discardAfterExit = false;
        try
        {
            while (true)
            {
                int count = outputPipe!.Read(bytes, 0, bytes.Length);
                if (!discardAfterExit)
                {
                    int length = decoder.GetChars(bytes, 0, count, characters, 0, count == 0);
                    if (length != 0)
                    {
                        try { output.Writer.WriteAsync(new string(characters, 0, length), disposed.Token).AsTask().GetAwaiter().GetResult(); }
                        catch (OperationCanceledException) when (disposed.IsCancellationRequested)
                        {
                            // Explicit disposal is permitted only after the held child exited.
                            // Continue draining the pipe so ClosePseudoConsole cannot deadlock.
                            discardAfterExit = true;
                        }
                    }
                }
                if (count == 0) break;
            }
        }
        catch (IOException error)
        {
            // ERROR_BROKEN_PIPE is normal after the held child exits/ConPTY closes.
            if ((error.HResult & 0xffff) != 109) RaiseError("OMP 출력 파이프를 읽지 못했습니다. 실행 상태와 파일은 보존됩니다. " + error.Message);
        }
        catch (Exception error) { RaiseError("OMP 출력 처리가 중단되었습니다. 자식 프로세스를 강제 종료하지 않습니다. " + error.Message); }
        finally
        {
            output.Writer.TryComplete();
            if (!running && process is not null) outputPipe?.Dispose();
        }
    }

    private void WriteInput()
    {
        Exception? failure = null;
        byte[] buffer = ArrayPool<byte>.Shared.Rent(131_072);
        try
        {
            while (input.Reader.WaitToReadAsync(disposed.Token).AsTask().GetAwaiter().GetResult())
                while (input.Reader.TryRead(out var frame))
                {
                    int length;
                    if (frame.Binary)
                    {
                        length = frame.Text.Length;
                        for (int index = 0; index < length; index++) buffer[index] = (byte)frame.Text[index];
                    }
                    else length = Utf8.GetBytes(frame.Text.AsSpan(), buffer.AsSpan());
                    inputPipe!.Write(buffer, 0, length);
                }
        }
        catch (OperationCanceledException) when (disposed.IsCancellationRequested) { }
        catch (Exception error)
        {
            failure = error;
            if (running) RaiseError("OMP 입력 파이프를 쓰지 못했습니다. 입력을 자동 재전송하지 않습니다. " + error.Message);
        }
        finally
        {
            input.Writer.TryComplete(failure);
            ArrayPool<byte>.Shared.Return(buffer, clearArray: true);
        }
    }

    private void EnsureExitObserver()
    {
        outputTask ??= Task.Factory.StartNew(DrainOutput, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
        inputTask ??= Task.Factory.StartNew(WriteInput, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
        waitTask ??= Task.Factory.StartNew(WaitForExit, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
    }

    private void WaitForExit()
    {
        uint result = WaitForSingleObject(process!, Infinite);
        if (result != 0 || !GetExitCodeProcess(process!, out uint code))
        {
            RaiseError("소유한 OMP 프로세스의 종료를 확인하지 못했습니다. 실행 중으로 유지하며 창을 닫지 않습니다.");
            return;
        }
        LastExitCode = unchecked((int)code);
        // ClosePseudoConsole may wait for its output drain. It runs off the UI/input threads,
        // and only after the exact held child has signalled termination.
        nint closedConsole;
        lock (lifecycle)
        {
            running = false;
            input.Writer.TryComplete();
            closedConsole = pseudoConsole;
            pseudoConsole = 0;
        }
        processExited.TrySetResult();
        if (outputTask?.IsCompleted == true) outputPipe?.Dispose();
        try { StatusChanged?.Invoke(this, EventArgs.Empty); }
        finally { if (closedConsole != 0) ClosePseudoConsole(closedConsole); }
    }

    private void RaiseError(string message) => ErrorRaised?.Invoke(this, message);

    public async ValueTask DisposeAsync()
    {
        if (running) throw new InvalidOperationException("OMP 안에서 먼저 종료하세요. 실행 중인 프로세스나 ConPTY는 닫지 않습니다.");
        disposed.Cancel();
        input.Writer.TryComplete();
        if (waitTask is not null) await waitTask;
        if (inputTask is not null) await inputTask;
        if (outputTask is not null) await outputTask;
        inputPipe?.Dispose(); outputPipe?.Dispose(); process?.Dispose();
        ReleaseRuntimeReservations();
        disposed.Dispose();
    }

    private static List<SafeFileHandle> OpenBoundRuntime(OmpInstalledRuntime runtime)
    {
        runtime.ValidateNamespace();
        var held = new List<SafeFileHandle>(runtime.Files.Length + 3);
        byte[] buffer = ArrayPool<byte>.Shared.Rent(65_536);
        var finalPath = new StringBuilder(32768);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        try
        {
            foreach (OmpRuntimeFile file in runtime.Files) held.Add(OpenBoundRuntimeFile(file, buffer, hash, finalPath));
            foreach (OmpNativeCandidate candidate in runtime.NativeCandidates)
                if (candidate.Present && !string.Equals(candidate.File.Path, runtime.Files[^1].Path, StringComparison.OrdinalIgnoreCase))
                    held.Add(OpenBoundRuntimeFile(candidate.File, buffer, hash, finalPath));
            runtime.ValidateNamespace();
            return held;
        }
        catch
        {
            foreach (SafeFileHandle file in held) file.Dispose();
            throw;
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
    }

    private static SafeFileHandle OpenBoundRuntimeFile(OmpRuntimeFile file, byte[] buffer, IncrementalHash hash, StringBuilder finalPath)
    {
        LaunchDescriptor.RequirePath(file.Path, false);
        // This installed-runtime-only reservation also excludes writes through
        // package-manager hardlink aliases. Owned staged files still require one link.
        SafeFileHandle handle = CreateFileW(file.Path, 0x80000000, 1, 0, 3, 0x00200000 | 0x08000000, 0);
        try
        {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "OMP 런타임 파일을 고정하지 못했습니다.");
            Check(GetFileInformationByHandle(handle, out FileInformation info), "OMP 런타임 파일 식별");
            long length = ((long)info.SizeHigh << 32) | info.SizeLow;
            if ((info.Attributes & (0x10u | 0x400u)) != 0 || GetFileType(handle) != 1 || length != file.Size || info.Links < 1 || (!file.Installed && info.Links != 1))
                throw new InvalidDataException("OMP 런타임 파일의 형식/크기/연결 수가 다릅니다.");
            finalPath.Clear();
            uint count = GetFinalPathNameByHandleW(handle, finalPath, (uint)finalPath.Capacity, 0);
            if (count == 0 || count >= finalPath.Capacity) throw new InvalidDataException("OMP 런타임 파일의 실제 경로를 확인하지 못했습니다.");
            string actualPath = finalPath.ToString();
            if (actualPath.StartsWith("\\\\?\\", StringComparison.Ordinal)) actualPath = actualPath[4..];
            if (!string.Equals(actualPath, file.Path, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("OMP 런타임 파일의 실제 경로가 다릅니다.");
            long offset = 0;
            int read;
            while ((read = RandomAccess.Read(handle, buffer, offset)) != 0)
            {
                offset += read;
                if (offset > file.Size) throw new InvalidDataException("OMP 런타임 파일 크기가 달라졌습니다.");
                hash.AppendData(buffer, 0, read);
            }
            Span<byte> digest = stackalloc byte[32];
            if (offset != file.Size || !hash.TryGetHashAndReset(digest, out int written) || written != 32 || !OmpInstalledRuntime.HashMatches(digest, file.Sha256))
                throw new InvalidDataException("OMP 런타임 파일의 SHA-256이 달라졌습니다.");
            return handle;
        }
        catch { handle.Dispose(); throw; }
    }

    private void ReserveBridgeFiles(OmpBridgeDescriptor bridge)
    {
        string source = Path.Combine(AppContext.BaseDirectory, "bridge", "omp-bridge.mjs");
        LaunchDescriptor.RequirePath(source, false);
        long size = new FileInfo(source).Length;
        if (size is <= 0 or > 1_048_576) throw new InvalidDataException("앱 소유 OMP 브리지 크기가 잘못되었습니다.");
        byte[] buffer = ArrayPool<byte>.Shared.Rent(65_536);
        var finalPath = new StringBuilder(32768);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        try
        {
            // Both exact app source and launch copy remain no-write/no-delete held
            // until the owned child has exited and its session is disposed.
            runtimeReservations!.Add(OpenBoundRuntimeFile(new(source, size, bridge.ExtensionSha256, false), buffer, hash, finalPath));
            runtimeReservations.Add(OpenBoundRuntimeFile(new(bridge.ExtensionPath, size, bridge.ExtensionSha256, false), buffer, hash, finalPath));
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
    }

    private void ReleaseRuntimeReservations()
    {
        if (runtimeReservations is null) return;
        foreach (SafeFileHandle file in runtimeReservations) file.Dispose();
        runtimeReservations = null;
    }

    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { slashes++; continue; }
            result.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
            result.Append(character); slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }

    private static void Check(bool success, string operation)
    {
        if (!success) throw new Win32Exception(Marshal.GetLastWin32Error(), operation);
    }

    [StructLayout(LayoutKind.Sequential)] private readonly struct Coord(short x, short y) { public readonly short X = x; public readonly short Y = y; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] private struct StartupInfo
    {
        public int Size; public nint Reserved; public nint Desktop; public nint Title;
        public int X; public int Y; public int XSize; public int YSize; public int XCountChars; public int YCountChars; public int FillAttribute; public int Flags;
        public short ShowWindow; public short ReservedSize; public nint ReservedBytes; public nint StandardInput; public nint StandardOutput; public nint StandardError;
    }
    [StructLayout(LayoutKind.Sequential)] private struct StartupInfoEx { public StartupInfo StartupInfo; public nint AttributeList; }
    [StructLayout(LayoutKind.Sequential)] private struct ProcessInformation { public nint Process; public nint Thread; public uint ProcessId; public uint ThreadId; }
    [StructLayout(LayoutKind.Sequential)] private struct FileInformation { public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Creation; public System.Runtime.InteropServices.ComTypes.FILETIME Access; public System.Runtime.InteropServices.ComTypes.FILETIME Write; public uint Volume; public uint SizeHigh; public uint SizeLow; public uint Links; public uint IndexHigh; public uint IndexLow; }
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreatePipe(out SafeFileHandle read, out SafeFileHandle write, nint attributes, uint size);
    [DllImport("kernel32.dll")] private static extern int CreatePseudoConsole(Coord size, SafeFileHandle input, SafeFileHandle output, uint flags, out nint console);
    [DllImport("kernel32.dll")] private static extern int ResizePseudoConsole(nint console, Coord size);
    [DllImport("kernel32.dll")] private static extern void ClosePseudoConsole(nint console);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool InitializeProcThreadAttributeList(nint list, int count, uint flags, ref nuint size);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool UpdateProcThreadAttribute(nint list, uint flags, nuint attribute, nint value, nuint size, nint previous, nint returnSize);
    [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(nint list);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreateProcessW(string application, StringBuilder command, nint processAttributes, nint threadAttributes, [MarshalAs(UnmanagedType.Bool)] bool inherit, uint flags, nint environment, string directory, ref StartupInfoEx startup, out ProcessInformation process);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(SafeFileHandle handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetExitCodeProcess(SafeFileHandle handle, out uint code);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern SafeFileHandle CreateFileW(string path, uint access, uint sharing, nint attributes, uint disposition, uint flags, nint template);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(SafeFileHandle file);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern uint GetFinalPathNameByHandleW(SafeFileHandle file, StringBuilder path, uint capacity, uint flags);
}

internal sealed record LaunchDescriptor(string LaunchId, string Executable, string ExecutableSha256, string[] Arguments, Dictionary<string, string> Environment, string LaunchDirectory, string WorkingDirectory, string ProfileDirectory, string SessionDirectory, string WebViewUserDataDirectory, string RuntimeSha256, OmpInstalledRuntime Runtime, OmpBridgeDescriptor Bridge)
{
    public static LaunchDescriptor Parse(JsonElement value)
    {
        RequireKeys(value, "contract", "launch_id", "executable", "executable_sha256", "arguments", "environment", "launch_directory", "working_directory", "profile_directory", "session_directory", "webview_user_data_directory", "runtime_sha256", "runtime", "bridge");
        string Read(string name, int max = 32760)
        {
            JsonElement part = value.GetProperty(name);
            if (part.ValueKind != JsonValueKind.String) throw new InvalidDataException("OMP 실행 설명의 문자열 형식이 잘못되었습니다: " + name);
            string text = part.GetString()!;
            if (text.Length == 0 || text.Length > max || text.Any(character => char.IsControl(character))) throw new InvalidDataException("OMP 실행 설명의 문자열 범위가 잘못되었습니다: " + name);
            return text;
        }
        if (Read("contract") != "hwp-native-omp-launch-v6") throw new InvalidDataException("지원하지 않는 OMP 실행 계약입니다.");
        string id = Read("launch_id", 32), hash = Read("executable_sha256", 64), runtime = Read("runtime_sha256", 64);
        if (!LowerHex(id, 32) || !LowerHex(hash, 64) || !LowerHex(runtime, 64)) throw new InvalidDataException("OMP 실행 식별자 형식이 잘못되었습니다.");
        string executable = Read("executable"), launchRoot = Read("launch_directory"), working = Read("working_directory"), profile = Read("profile_directory"), session = Read("session_directory"), webview = Read("webview_user_data_directory");
        RequirePath(executable, false);
        foreach (string directory in new[] { launchRoot, working, profile, session, webview }) RequirePath(directory, true);
        string[] directories = [working, profile, session, webview];
        for (int i = 0; i < directories.Length; i++)
            for (int j = i + 1; j < directories.Length; j++)
                if (Within(directories[i], directories[j]) || Within(directories[j], directories[i])) throw new InvalidDataException("OMP 작업·프로필·세션·WebView 디렉터리는 분리되어야 합니다.");
        JsonElement args = value.GetProperty("arguments");
        if (args.ValueKind != JsonValueKind.Array || args.GetArrayLength() > 64) throw new InvalidDataException("OMP 실행 인수 형식이 잘못되었습니다.");
        string[] arguments = args.EnumerateArray().Select(item => item.ValueKind == JsonValueKind.String ? item.GetString()! : throw new InvalidDataException("OMP 인수는 문자열이어야 합니다.")).ToArray();
        if (arguments.Any(argument => argument.Length > 16384 || argument.Contains('\0'))) throw new InvalidDataException("OMP 실행 인수 범위를 초과했습니다.");
        JsonElement env = value.GetProperty("environment");
        if (env.ValueKind != JsonValueKind.Object) throw new InvalidDataException("OMP 환경 형식이 잘못되었습니다.");
        var environment = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (JsonProperty property in env.EnumerateObject())
        {
            if (property.Value.ValueKind != JsonValueKind.String || !environment.TryAdd(property.Name, property.Value.GetString()!) || property.Name.Length > 32760 || property.Name.Length == 0 || property.Name.Contains('=') || property.Name.Any(char.IsControl) || environment[property.Name].Length > 32760 || environment[property.Name].Contains('\0')) throw new InvalidDataException("OMP 환경 항목 형식이 잘못되었습니다.");
        }
        OmpBridgeDescriptor bridge = OmpBridgeDescriptor.Parse(value.GetProperty("bridge"), id, launchRoot);
        ValidateLaunchPolicy(arguments, environment, launchRoot, working, profile, session, webview, id, bridge);
        OmpInstalledRuntime installed = OmpInstalledRuntime.Parse(value.GetProperty("runtime"), executable, hash, environment["HOME"]);
        string installedRoot = installed.BunRoot;
        if (Within(working, installedRoot) || Within(installedRoot, working)) throw new InvalidDataException("OMP 실행 저장소를 작업 폴더로 사용할 수 없습니다.");
        return new(id, executable, hash, arguments, environment, launchRoot, working, profile, session, webview, runtime, installed, bridge);
    }

    internal static void RequireKeys(JsonElement value, params string[] keys)
    {
        if (value.ValueKind != JsonValueKind.Object) throw new InvalidDataException("객체 형식이 필요합니다.");
        var remaining = new HashSet<string>(keys, StringComparer.Ordinal);
        foreach (JsonProperty property in value.EnumerateObject())
            if (!remaining.Remove(property.Name)) throw new InvalidDataException("알 수 없거나 중복된 필드입니다: " + property.Name);
        if (remaining.Count != 0) throw new InvalidDataException("필수 필드가 없습니다.");
    }

    private static bool LowerHex(string value, int length) => value.Length == length && value.All(character => character is >= '0' and <= '9' or >= 'a' and <= 'f');
    private static bool Within(string path, string root) => string.Equals(Path.TrimEndingDirectorySeparator(path), Path.TrimEndingDirectorySeparator(root), StringComparison.OrdinalIgnoreCase) || path.StartsWith(Path.TrimEndingDirectorySeparator(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);

    internal static void RequirePath(string path, bool directory)
    {
        if (!Path.IsPathFullyQualified(path) || path.StartsWith("\\\\", StringComparison.Ordinal) || path.IndexOf(':', 2) >= 0 || !string.Equals(Path.GetFullPath(path), path, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("로컬 정규 절대 경로가 필요합니다.");
        FileAttributes attributes = File.GetAttributes(path);
        if ((attributes & FileAttributes.ReparsePoint) != 0 || ((attributes & FileAttributes.Directory) != 0) != directory) throw new InvalidDataException("일반 파일/디렉터리만 사용할 수 있습니다.");
        string? parent = Path.GetDirectoryName(path);
        while (parent is not null)
        {
            if ((File.GetAttributes(parent) & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException("재분석 경로는 사용할 수 없습니다.");
            parent = Path.GetDirectoryName(parent);
        }
    }

    private static void ValidateLaunchPolicy(string[] arguments, Dictionary<string, string> environment, string launchRoot, string working, string profile, string session, string webview, string launchId, OmpBridgeDescriptor bridge)
    {
        if (!environment.TryGetValue("HOME", out string? home) || string.IsNullOrWhiteSpace(home))
            throw new InvalidDataException("OMP 전용 홈 위치가 없습니다.");
        string[] expected = ["--profile", "hwp-report", "--cwd", working, "--session-dir", session, "--extension", bridge.ExtensionPath, "--append-system-prompt", Path.Combine(working, "HWPReview.md")];
        if (!arguments.SequenceEqual(expected, StringComparer.Ordinal)) throw new InvalidDataException("고정된 OMP 실행 인수와 일치하지 않습니다.");
        string appRoot = Path.GetDirectoryName(home) ?? throw new InvalidDataException("OMP 전용 저장소의 부모 경로가 없습니다.");
        if (!string.Equals(home, Path.Combine(appRoot, "home"), StringComparison.OrdinalIgnoreCase) ||
            !string.Equals(launchRoot, Path.Combine(appRoot, "launches", launchId), StringComparison.OrdinalIgnoreCase) ||
            !string.Equals(webview, Path.Combine(appRoot, "webview"), StringComparison.OrdinalIgnoreCase) ||
            !string.Equals(profile, Path.Combine(home, ".omp", "profiles", "hwp-report", "agent"), StringComparison.OrdinalIgnoreCase) ||
            !string.Equals(session, Path.Combine(launchRoot, "sessions"), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("OMP 실행 디렉터리 배치가 잘못되었습니다.");
        bool fallback = string.Equals(working, Path.Combine(launchRoot, "workspace"), StringComparison.OrdinalIgnoreCase);
        if (!fallback && (Within(working, appRoot) || Within(appRoot, working))) throw new InvalidDataException("앱 전용 저장소는 작업 폴더로 사용할 수 없습니다.");
        for (DirectoryInfo? folder = new(working); folder is not null; folder = folder.Parent)
            if (string.Equals(folder.Name, ".hwpreview", StringComparison.OrdinalIgnoreCase) ||
                File.Exists(Path.Combine(folder.FullName, "report-project.json")))
                throw new InvalidDataException("앱 관리 자료는 작업 폴더로 사용할 수 없습니다.");
        foreach (string file in new[] { Path.Combine(working, "HWPReview.md"), bridge.ExtensionPath }) RequirePath(file, false);
        var required = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["HOME"] = home, ["USERPROFILE"] = home, ["APPDATA"] = Path.Combine(home, "AppData", "Roaming"), ["LOCALAPPDATA"] = Path.Combine(home, "AppData", "Local"),
            ["XDG_CONFIG_HOME"] = Path.Combine(home, ".config"), ["PI_CONFIG_DIR"] = ".omp", ["TEMP"] = Path.Combine(launchRoot, "tmp"), ["TMP"] = Path.Combine(launchRoot, "tmp"),
            ["TERM"] = "xterm-256color", ["COLORTERM"] = "truecolor",
            ["HWP_OMP_PIPE"] = @"\\.\pipe\" + bridge.PipeName, ["HWP_OMP_TOKEN"] = bridge.Token, ["HWP_OMP_LAUNCH_ID"] = launchId
        };
        foreach (var pair in required)
            if (!environment.TryGetValue(pair.Key, out string? text) || !string.Equals(text, pair.Value, StringComparison.Ordinal)) throw new InvalidDataException("OMP 앱 환경 값이 일치하지 않습니다: " + pair.Key);
        foreach (string path in new[] { home, required["APPDATA"], required["LOCALAPPDATA"], required["XDG_CONFIG_HOME"], required["TEMP"] }) RequirePath(path, true);
    }
}
