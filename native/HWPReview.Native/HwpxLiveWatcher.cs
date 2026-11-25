using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Xml;

namespace HWPReview.Native;

internal sealed record HwpxFileSnapshot(string FilePath, byte[] Bytes, string Sha256);

// Watches the active workspace .hwpx parent directory for Hancom Ctrl+S or OMP edits.
// Debounces 400ms, waits for Hancom file locks to release, validates ZIP + Contents/section0.xml,
// and emits DocumentChanged only when the verified SHA-256 actually changes.
internal sealed class HwpxLiveWatcher : IDisposable
{
    public const int MaximumBytes = 64 * 1024 * 1024;
    private static readonly TimeSpan DebounceWindow = TimeSpan.FromMilliseconds(400);
    private static readonly TimeSpan RetryDelay = TimeSpan.FromMilliseconds(120);
    private const int MaxReadAttempts = 12;

    private readonly object gate = new();
    private FileSystemWatcher? watcher;
    private Timer? debounceTimer;
    private CancellationTokenSource? activeReadCts;
    private string targetPath = "";
    private string targetFileName = "";
    private string lastSha256 = "";
    private bool disposed;

    public string WatchedPath
    {
        get { lock (gate) return targetPath; }
    }

    public event EventHandler<HwpxFileSnapshot>? DocumentChanged;
    public event EventHandler<string>? WatchWarning;

    public void Start(string filePath, string initialSha256)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        if (string.IsNullOrWhiteSpace(filePath)) throw new ArgumentException("감시할 HWPX 파일 경로가 비어 있습니다.", nameof(filePath));
        string fullPath = Path.GetFullPath(filePath);
        string? parentDir = Path.GetDirectoryName(fullPath);
        string fileName = Path.GetFileName(fullPath);
        if (string.IsNullOrEmpty(parentDir) || string.IsNullOrEmpty(fileName) || !Directory.Exists(parentDir))
            throw new DirectoryNotFoundException("감시할 문서의 상위 폴더를 찾을 수 없습니다.");

        Stop();
        lock (gate)
        {
            if (disposed) return;
            targetPath = fullPath;
            targetFileName = fileName;
            lastSha256 = initialSha256 ?? "";
            debounceTimer = new Timer(_ => _ = ProcessPendingChangeAsync(), null, Timeout.Infinite, Timeout.Infinite);
            var fsw = new FileSystemWatcher(parentDir)
            {
                IncludeSubdirectories = false,
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size | NotifyFilters.CreationTime
            };
            fsw.Changed += OnFileEvent;
            fsw.Created += OnFileEvent;
            fsw.Renamed += OnRenamedEvent;
            fsw.Error += OnWatcherError;
            fsw.EnableRaisingEvents = true;
            watcher = fsw;
        }
    }

    public void UpdateKnownSha(string sha256)
    {
        lock (gate)
        {
            if (!string.IsNullOrWhiteSpace(sha256)) lastSha256 = sha256;
        }
    }

    public void Stop()
    {
        FileSystemWatcher? oldWatcher;
        Timer? oldTimer;
        CancellationTokenSource? oldCts;
        lock (gate)
        {
            oldWatcher = watcher;
            watcher = null;
            oldTimer = debounceTimer;
            debounceTimer = null;
            oldCts = activeReadCts;
            activeReadCts = null;
            targetPath = "";
            targetFileName = "";
            lastSha256 = "";
        }
        try { oldCts?.Cancel(); } catch { }
        try { oldCts?.Dispose(); } catch { }
        try { oldTimer?.Dispose(); } catch { }
        if (oldWatcher is not null)
        {
            try { oldWatcher.EnableRaisingEvents = false; } catch { }
            try { oldWatcher.Dispose(); } catch { }
        }
    }

    public async Task<HwpxFileSnapshot?> ReloadNowAsync(bool forceEmit = false, CancellationToken cancellationToken = default)
    {
        string path;
        lock (gate)
        {
            if (disposed || string.IsNullOrEmpty(targetPath)) return null;
            path = targetPath;
        }
        HwpxFileSnapshot snapshot = await ReadValidatedWithRetryAsync(path, cancellationToken);
        bool changed;
        lock (gate)
        {
            if (disposed || !string.Equals(targetPath, path, StringComparison.OrdinalIgnoreCase)) return null;
            changed = forceEmit || !string.Equals(lastSha256, snapshot.Sha256, StringComparison.Ordinal);
            lastSha256 = snapshot.Sha256;
        }
        if (changed) DocumentChanged?.Invoke(this, snapshot);
        return snapshot;
    }

    private void OnFileEvent(object sender, FileSystemEventArgs args)
    {
        if (MatchesTarget(args.Name, args.FullPath)) ScheduleDebounce();
    }

    private void OnRenamedEvent(object sender, RenamedEventArgs args)
    {
        if (MatchesTarget(args.Name, args.FullPath) || MatchesTarget(args.OldName, args.OldFullPath)) ScheduleDebounce();
    }

    private void OnWatcherError(object sender, ErrorEventArgs args)
    {
        string detail = args.GetException()?.Message ?? "파일 감시 이벤트 버퍼 오류";
        WatchWarning?.Invoke(this, "파일 변경 감시 알림: " + detail);
        ScheduleDebounce();
    }

    private bool MatchesTarget(string? name, string? fullPath)
    {
        lock (gate)
        {
            if (disposed || string.IsNullOrEmpty(targetPath)) return false;
            if (!string.IsNullOrEmpty(name) && string.Equals(name, targetFileName, StringComparison.OrdinalIgnoreCase)) return true;
            if (!string.IsNullOrEmpty(fullPath) && string.Equals(fullPath, targetPath, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }
    }

    private void ScheduleDebounce()
    {
        lock (gate)
        {
            if (disposed || debounceTimer is null) return;
            try { debounceTimer.Change(DebounceWindow, Timeout.InfiniteTimeSpan); } catch { }
        }
    }

    private async Task ProcessPendingChangeAsync()
    {
        string path;
        CancellationTokenSource cts;
        lock (gate)
        {
            if (disposed || string.IsNullOrEmpty(targetPath)) return;
            path = targetPath;
            try { activeReadCts?.Cancel(); } catch { }
            try { activeReadCts?.Dispose(); } catch { }
            cts = new CancellationTokenSource();
            activeReadCts = cts;
        }
        try
        {
            HwpxFileSnapshot snapshot = await ReadValidatedWithRetryAsync(path, cts.Token);
            bool changed;
            lock (gate)
            {
                if (disposed || cts.IsCancellationRequested || !string.Equals(targetPath, path, StringComparison.OrdinalIgnoreCase)) return;
                changed = !string.Equals(lastSha256, snapshot.Sha256, StringComparison.Ordinal);
                lastSha256 = snapshot.Sha256;
            }
            if (changed) DocumentChanged?.Invoke(this, snapshot);
        }
        catch (OperationCanceledException)
        {
            // Superseded by a newer file event or Stop/Dispose.
        }
        catch (Exception error)
        {
            WatchWarning?.Invoke(this, "자동 미리보기 갱신 대기 실패: " + error.Message);
        }
    }

    public static async Task<HwpxFileSnapshot> ReadValidatedWithRetryAsync(string filePath, CancellationToken cancellationToken = default)
    {
        Exception? lastError = null;
        for (int attempt = 0; attempt < MaxReadAttempts; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                byte[] bytes = ReadSharedBytes(filePath);
                string sha = ValidateHwpxBytes(bytes);
                return new HwpxFileSnapshot(filePath, bytes, sha);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidDataException or XmlException)
            {
                lastError = error;
                if (attempt + 1 >= MaxReadAttempts) break;
                await Task.Delay(RetryDelay, cancellationToken);
            }
        }
        throw new IOException("HWPX 파일 저장 완료 또는 구조 검증에 실패했습니다: " + (lastError?.Message ?? "알 수 없는 오류"), lastError);
    }

    private static byte[] ReadSharedBytes(string filePath)
    {
        if (!File.Exists(filePath)) throw new FileNotFoundException("HWPX 파일을 찾을 수 없습니다.", filePath);
        FileInfo info = new(filePath);
        if (info.Attributes.HasFlag(FileAttributes.ReparsePoint))
            throw new InvalidDataException("연결된 파일(심볼릭 링크·재분석 지점)은 감시하지 않습니다.");
        if (info.Length <= 0 || info.Length > MaximumBytes)
            throw new InvalidDataException("HWPX 파일 크기가 허용 범위(1B~64MiB)를 벗어났습니다.");

        using var stream = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        using var buffer = new MemoryStream((int)Math.Min(info.Length, MaximumBytes));
        byte[] chunk = new byte[65536];
        int total = 0;
        while (true)
        {
            int read = stream.Read(chunk, 0, chunk.Length);
            if (read <= 0) break;
            total += read;
            if (total > MaximumBytes) throw new InvalidDataException("HWPX 파일 크기가 허용 한도를 넘었습니다.");
            buffer.Write(chunk, 0, read);
        }
        if (total == 0) throw new InvalidDataException("HWPX 파일이 비어 있습니다.");
        return buffer.ToArray();
    }

    public static string ValidateHwpxBytes(byte[] bytes)
    {
        if (bytes.Length == 0 || bytes.Length > MaximumBytes)
            throw new InvalidDataException("HWPX 파일 크기가 허용 범위 밖입니다.");
        using var stream = new MemoryStream(bytes, writable: false);
        using var archive = new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: false);
        ZipArchiveEntry? section0 = null;
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            if (string.Equals(entry.FullName, "Contents/section0.xml", StringComparison.Ordinal))
                section0 = entry;
            // Verify ZIP entry stream is readable (catches truncated/in-progress writes).
            using Stream entryStream = entry.Open();
            byte[] probe = new byte[4096];
            long entryBytes = 0;
            while (true)
            {
                int read = entryStream.Read(probe, 0, probe.Length);
                if (read <= 0) break;
                entryBytes += read;
                if (entryBytes > MaximumBytes) throw new InvalidDataException("압축 해제 크기가 허용 한도를 넘었습니다.");
            }
        }
        if (section0 is null || section0.Length <= 0)
            throw new InvalidDataException("HWPX에 Contents/section0.xml이 없습니다.");

        using (Stream xmlStream = section0.Open())
        {
            var settings = new XmlReaderSettings
            {
                DtdProcessing = DtdProcessing.Prohibit,
                XmlResolver = null,
                MaxCharactersInDocument = MaximumBytes
            };
            using var reader = XmlReader.Create(xmlStream, settings);
            while (reader.Read()) { }
        }
        return Convert.ToHexStringLower(SHA256.HashData(bytes));
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        Stop();
    }
}
