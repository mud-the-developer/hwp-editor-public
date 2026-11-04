using System.IO;
using System.Security.Cryptography;
using System.Text.Json;

namespace HWPReview.Native;

internal sealed record OmpRuntimeFile(string Path, long Size, string Sha256, bool Installed);
internal sealed record OmpNativeCandidate(OmpRuntimeFile File, bool Present);

// Admission of the reviewed installed CLI baseline, not an OS sandbox or a lock
// on future native staging/optional interactive dependency resolution.
internal sealed record OmpInstalledRuntime(string BunRoot, string Home, string ManifestSha256, OmpRuntimeFile[] Files, OmpNativeCandidate[] NativeCandidates)
{
    private const string ManifestHash = "4eeada4a2dc83c5ca3596bd6ff24631a804cec23f738de3ecf9d486ee8413a2a";
    private const string Version = "18.15.0";
    private const string CliRoot = "install/global/node_modules/@oh-my-pi/pi-coding-agent";
    private const string NativesRoot = "install/global/node_modules/@oh-my-pi/pi-natives";
    private const string LeafRoot = "install/global/node_modules/@oh-my-pi/pi-natives-win32-x64";
    private const string Addon = "pi_natives.win32-x64-baseline.node";
    private static readonly string[] Notices = ["LICENSE", "README.md", "THIRD-PARTY-NOTICES.txt"];
    private static readonly string[] CliFiles = ["package.json", "CHANGELOG.md", .. Notices];
    private static readonly string[] CliDirectories = ["dist", "src", "scripts", "examples"];
    private static readonly string[] DistFiles = ["CHANGELOG-a7paazjm.md", "cli.js", "docs-index.generated.txt", "models.json-3tsbab79.zst", "rules.json-6b4dpgdf.zst"];
    private static readonly string[] DistDirectories = ["types"];
    private static readonly string[] NativeRootFiles = ["package.json", .. Notices];
    private static readonly string[] NativeRootDirectories = ["native"];
    private static readonly string[] LeafFiles = ["package.json", Addon, .. Notices];
    private static readonly string[] NativeMembers =
    [
        "clipboard.js", "clipboard.d.ts", "desktop-adapter.js", "desktop-adapter.d.ts",
        "desktop.js", "desktop.d.ts", "embedded-addon.js", "index.js", "index.d.ts",
        "loader-state.js", "loader-state.d.ts", "path.js", "path.d.ts", "vcs.js", "vcs.d.ts",
        "version-sentinel.js", "version-sentinel.d.ts"
    ];

    internal static OmpInstalledRuntime Parse(JsonElement value, string executable, string launcherHash, string home)
    {
        LaunchDescriptor.RequireKeys(value, "contract", "bun_root", "manifest_sha256", "files", "native_candidates");
        if (Text(value, "contract") != "hwp-native-omp-runtime-v1" || Text(value, "manifest_sha256") != ManifestHash)
            throw new InvalidDataException("OMP 설치 런타임 계약/기준 해시가 다릅니다.");
        string root = Text(value, "bun_root");
        LaunchDescriptor.RequirePath(root, true);
        if (!Same(executable, Member(root, "bin/omp.exe"))) throw new InvalidDataException("OMP 설치 실행기 배치가 다릅니다.");
        string manifestPath = System.IO.Path.Combine(AppContext.BaseDirectory, "omp-runtime.json");
        LaunchDescriptor.RequirePath(manifestPath, false);
        using var manifestStream = new FileStream(manifestPath, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (manifestStream.Length is <= 0 or > 65536) throw new InvalidDataException("OMP 런타임 기준 파일 크기가 잘못되었습니다.");
        byte[] bytes = new byte[checked((int)manifestStream.Length)];
        manifestStream.ReadExactly(bytes);
        Span<byte> digest = stackalloc byte[32];
        SHA256.HashData(bytes, digest);
        if (!HashMatches(digest, ManifestHash)) throw new InvalidDataException("OMP 런타임 기준 파일 바이트가 달라졌습니다.");
        using JsonDocument document = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 8 });
        JsonElement baseline = document.RootElement;
        LaunchDescriptor.RequireKeys(baseline, "schema", "package_version", "files", "native_addon_relative_path");
        if (Text(baseline, "schema") != "hwp-omp-runtime-baseline-v1" || Text(baseline, "package_version") != Version ||
            Text(baseline, "native_addon_relative_path") != LeafRoot + "/" + Addon)
            throw new InvalidDataException("OMP 패키지 기준이 다릅니다.");
        JsonElement expected = baseline.GetProperty("files"), supplied = value.GetProperty("files");
        if (expected.ValueKind != JsonValueKind.Array || expected.GetArrayLength() != 21 || supplied.ValueKind != JsonValueKind.Array || supplied.GetArrayLength() != expected.GetArrayLength())
            throw new InvalidDataException("OMP 런타임 파일 목록이 다릅니다.");
        var files = new OmpRuntimeFile[expected.GetArrayLength()];
        var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        for (int index = 0; index < files.Length; index++)
        {
            JsonElement pin = expected[index], row = supplied[index];
            LaunchDescriptor.RequireKeys(pin, "relative_path", "size", "sha256");
            LaunchDescriptor.RequireKeys(row, "relative_path", "path", "size", "sha256");
            string relative = Text(pin, "relative_path"), path = Member(root, relative), hash = Text(pin, "sha256");
            long size = Size(pin);
            if (!LowerHash(hash) || Text(row, "relative_path") != relative || !Same(Text(row, "path"), path) || Text(row, "sha256") != hash || Size(row) != size || !paths.Add(path))
                throw new InvalidDataException("OMP 런타임 파일의 경로/크기/해시가 다릅니다.");
            files[index] = new(path, size, hash, true);
        }
        if (!Same(files[0].Path, executable) || files[0].Sha256 != launcherHash || !Same(files[^1].Path, Member(root, LeafRoot + "/" + Addon)))
            throw new InvalidDataException("OMP 실행기/네이티브 기준 항목이 다릅니다.");
        JsonElement candidates = value.GetProperty("native_candidates");
        if (candidates.ValueKind != JsonValueKind.Array || candidates.GetArrayLength() != 12)
            throw new InvalidDataException("OMP 네이티브 후보 목록이 다릅니다.");
        string[] directories = [System.IO.Path.Combine(home, ".omp", "natives", Version), Member(root, LeafRoot), Member(root, NativesRoot + "/native"), Member(root, "bin")];
        string[] names = [Addon, "pi_natives.win32-x64-modern.node", "pi_natives.win32-x64.node"];
        var native = new OmpNativeCandidate[12];
        int position = 0;
        for (int directory = 0; directory < directories.Length; directory++)
            for (int variant = 0; variant < names.Length; variant++)
            {
                JsonElement row = candidates[position];
                LaunchDescriptor.RequireKeys(row, "path", "present", "size", "sha256");
                string path = System.IO.Path.Combine(directories[directory], names[variant]);
                bool supported = variant == 0;
                long size = supported ? files[^1].Size : 0;
                string hash = supported ? files[^1].Sha256 : "";
                JsonElement present = row.GetProperty("present");
                if (present.ValueKind is not (JsonValueKind.True or JsonValueKind.False) || !Same(Text(row, "path"), path) ||
                    !row.GetProperty("size").TryGetInt64(out long actualSize) || actualSize != size || String(row, "sha256") != hash || (!supported && present.GetBoolean()))
                    throw new InvalidDataException("OMP 네이티브 후보 경로/상태/해시가 다릅니다.");
                native[position++] = new(new(path, size, hash, directory != 0), present.GetBoolean());
            }
        var runtime = new OmpInstalledRuntime(root, home, ManifestHash, files, native);
        runtime.ValidateNamespace();
        return runtime;
    }

    internal void ValidateNamespace()
    {
        ClosedDirectory(Member(BunRoot, CliRoot), CliFiles, CliDirectories);
        ClosedDirectory(Member(BunRoot, CliRoot + "/dist"), DistFiles, DistDirectories);
        ClosedDirectory(Member(BunRoot, NativesRoot), NativeRootFiles, NativeRootDirectories);
        ClosedDirectory(Member(BunRoot, NativesRoot + "/native"), NativeMembers, [], NativeCandidates[6].Present ? Addon : null);
        ClosedDirectory(Member(BunRoot, LeafRoot), LeafFiles, []);
        if (CandidateExists(Member(BunRoot, "install/global/node_modules/@oh-my-pi/node_modules")))
            throw new InvalidDataException("OMP 설치 패키지 탐색 경로가 가려졌습니다.");
        foreach (OmpNativeCandidate candidate in NativeCandidates)
        {
            bool present = CandidateExists(candidate.File.Path);
            if (present != candidate.Present) throw new InvalidDataException("OMP 네이티브 후보 상태가 준비 이후 바뀌었습니다.");
            if (present) LaunchDescriptor.RequirePath(candidate.File.Path, false);
        }
    }

    internal static bool HashMatches(ReadOnlySpan<byte> bytes, string expected)
    {
        const string alphabet = "0123456789abcdef";
        if (bytes.Length != 32 || expected.Length != 64) return false;
        for (int index = 0; index < bytes.Length; index++)
            if (expected[index * 2] != alphabet[bytes[index] >> 4] || expected[index * 2 + 1] != alphabet[bytes[index] & 15]) return false;
        return true;
    }

    private static void ClosedDirectory(string path, IEnumerable<string> files, IEnumerable<string> directories, string? optionalFile = null)
    {
        LaunchDescriptor.RequirePath(path, true);
        var expected = new Dictionary<string, bool>(StringComparer.Ordinal);
        foreach (string name in files) expected.Add(name, false);
        foreach (string name in directories) expected.Add(name, true);
        if (optionalFile is not null) expected.Add(optionalFile, false);
        foreach (string member in Directory.EnumerateFileSystemEntries(path))
        {
            if (!expected.Remove(System.IO.Path.GetFileName(member), out bool directory)) throw new InvalidDataException("OMP 런타임 디렉터리에 추가 항목이 있습니다.");
            LaunchDescriptor.RequirePath(member, directory);
        }
        if (expected.Count != 0) throw new InvalidDataException("OMP 런타임 디렉터리 항목이 없습니다.");
    }

    private static bool CandidateExists(string path)
    {
        string? current = path;
        bool present = true;
        while (current is not null)
        {
            try
            {
                FileAttributes attributes = File.GetAttributes(current);
                if ((attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException("OMP 후보의 재분석 경로는 사용할 수 없습니다.");
                if (current != path && (attributes & FileAttributes.Directory) == 0) throw new InvalidDataException("OMP 후보의 상위 경로는 디렉터리여야 합니다.");
            }
            catch (FileNotFoundException) { if (current == path) present = false; }
            catch (DirectoryNotFoundException) { if (current == path) present = false; }
            current = System.IO.Path.GetDirectoryName(current);
        }
        return present;
    }

    private static string Member(string root, string relative)
    {
        if (relative.Length is 0 or > 512 || relative.Contains('\\') || relative.Contains(':') || relative.StartsWith('/'))
            throw new InvalidDataException("OMP 기준 상대 경로가 잘못되었습니다.");
        ReadOnlySpan<char> parts = relative;
        foreach (Range range in parts.Split('/'))
            if (parts[range] is "" or "." or "..") throw new InvalidDataException("OMP 기준 상대 경로가 잘못되었습니다.");
        return System.IO.Path.GetFullPath(System.IO.Path.Combine(root, relative.Replace('/', System.IO.Path.DirectorySeparatorChar)));
    }
    private static string String(JsonElement value, string name)
    {
        JsonElement part = value.GetProperty(name);
        return part.ValueKind == JsonValueKind.String ? part.GetString()! : throw new InvalidDataException("OMP 런타임 문자열 형식이 잘못되었습니다.");
    }
    private static string Text(JsonElement value, string name)
    {
        string text = String(value, name);
        if (text.Length is 0 or > 32760) throw new InvalidDataException("OMP 런타임 문자열 범위가 잘못되었습니다.");
        foreach (char character in text)
            if (char.IsControl(character)) throw new InvalidDataException("OMP 런타임 문자열 범위가 잘못되었습니다.");
        return text;
    }
    private static long Size(JsonElement value) => value.GetProperty("size").TryGetInt64(out long size) && size is > 0 and <= 268435456 ? size : throw new InvalidDataException("OMP 런타임 크기 형식이 잘못되었습니다.");
    private static bool LowerHash(string value)
    {
        if (value.Length != 64) return false;
        foreach (char character in value)
            if (character is not (>= '0' and <= '9' or >= 'a' and <= 'f')) return false;
        return true;
    }
    private static bool Same(string left, string right) => string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
}
