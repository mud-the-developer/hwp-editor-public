using System.IO;

namespace HWPReview.Native;

internal sealed record WorkspaceChanges(string[] SavedPaths, bool RefreshList, bool Reconcile, string? Warning);

// Notifications are hints, never permission to open a path. The host admits files and
// captures their immutable bytes. A busy UI takes the pending batch when it is ready.
internal sealed class WorkspaceDocumentWatcher : IDisposable
{
    private const int DebounceMs = 400;
    private const int MaxPaths = 500; // Same bound as workspace discovery.
    private readonly object gate = new();
    private readonly Dictionary<string, long> saved = new(StringComparer.OrdinalIgnoreCase);
    private readonly FileSystemWatcher watcher;
    private readonly Timer timer;
    private long sequence;
    private long firstTick;
    private bool scheduled, ready, refreshList, reconcile, disposed;
    private string? warning;

    public WorkspaceDocumentWatcher(string root, Action signal)
    {
        watcher = new FileSystemWatcher(root)
        {
            IncludeSubdirectories = true,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size
        };
        watcher.Filters.Add("*.hwp");
        watcher.Filters.Add("*.hwpx");
        timer = new Timer(_ =>
        {
            lock (gate)
            {
                if (disposed || !scheduled) return;
                ready = true;
            }
            signal();
        });
        watcher.Changed += OnFile;
        watcher.Created += OnFile;
        watcher.Deleted += OnFile;
        watcher.Renamed += (_, e) =>
        {
            bool incoming = Visible(e.Name);
            if (incoming || Visible(e.OldName)) Hint(incoming ? e.FullPath : null, listing: true);
        };
        watcher.Error += (_, e) => Hint(null, listing: true, error: e.GetException().Message);
        try { watcher.EnableRaisingEvents = true; }
        catch { Dispose(); throw; }
    }

    private static bool Visible(string? relative)
    {
        if (string.IsNullOrEmpty(relative)) return false;
        ReadOnlySpan<char> name = relative;
        foreach (Range range in name.SplitAny("/\\"))
        {
            ReadOnlySpan<char> part = name[range];
            if (part.StartsWith(".") || part.StartsWith("~") ||
                part.Equals("example", StringComparison.OrdinalIgnoreCase)) return false;
        }
        return name.EndsWith(".hwpx", StringComparison.OrdinalIgnoreCase) ||
               name.EndsWith(".hwp", StringComparison.OrdinalIgnoreCase);
    }

    private void OnFile(object sender, FileSystemEventArgs e)
    {
        if (Visible(e.Name))
            Hint(e.ChangeType == WatcherChangeTypes.Deleted ? null : e.FullPath, e.ChangeType != WatcherChangeTypes.Changed);
    }

    private void Hint(string? path, bool listing, string? error = null)
    {
        lock (gate)
        {
            if (disposed) return;
            refreshList |= listing;
            if (error is not null) { reconcile = true; warning = error; }
            if (path is not null)
            {
                if (saved.Count < MaxPaths || saved.ContainsKey(path)) saved[path] = ++sequence;
                else { reconcile = true; refreshList = true; }
            }
            if (ready) return;
            long now = Environment.TickCount64;
            if (!scheduled) { scheduled = true; firstTick = now; }
            // A writer doing repeated saves must not postpone the preview indefinitely.
            timer.Change(Math.Clamp(1200 - (now - firstTick), 1, DebounceMs), Timeout.Infinite);
        }
    }

    public WorkspaceChanges? TakePending()
    {
        lock (gate)
        {
            if (disposed || !ready) return null;
            var paths = saved.OrderByDescending(pair => pair.Value).Select(pair => pair.Key).ToArray();
            var batch = new WorkspaceChanges(paths, refreshList, reconcile, warning);
            saved.Clear();
            scheduled = ready = refreshList = reconcile = false;
            warning = null;
            return batch;
        }
    }

    public void Dispose()
    {
        lock (gate) { disposed = true; }
        watcher.Dispose();
        timer.Dispose();
    }
}
