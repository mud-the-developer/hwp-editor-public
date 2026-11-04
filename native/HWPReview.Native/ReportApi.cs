using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;

namespace HWPReview.Native;

internal sealed class ReportApi : IDisposable
{
    public const int MaxArtifact = 64 * 1024 * 1024;
    private readonly HttpClient http;
    // ResponseHeadersRead returns before the body is consumed. Keep the whole
    // exchange single-flight, not merely the underlying connection pool.
    private readonly SemaphoreSlim exchange = new(1, 1);
    private readonly string origin;
    private readonly string token;
    public string Root { get; }
    public string Name { get; }

    public ReportApi(JsonElement descriptor)
    {
        origin = descriptor.Text("origin");
        token = descriptor.Text("token");
        if (descriptor.Text("kind") != "document" || descriptor.Text("product") != "hwpx-authoring" ||
            !Uri.TryCreate(origin, UriKind.Absolute, out var uri) || uri.Scheme != "http" || uri.Host != "127.0.0.1" ||
            uri.Port <= 0 || uri.IsDefaultPort || uri.AbsolutePath != "/" || uri.Query.Length != 0 || uri.Fragment.Length != 0 ||
            uri.UserInfo.Length != 0 || origin != $"http://127.0.0.1:{uri.Port}" || !OwnedHostClient.ValidNonce(token))
            throw new InvalidDataException("보고서 연결의 소유권과 주소를 확인할 수 없습니다.");
        Root = descriptor.Text("root");
        Name = descriptor.Text("name");
        http = new HttpClient(new SocketsHttpHandler
        {
            UseProxy = false, AllowAutoRedirect = false, UseCookies = false,
            ConnectTimeout = TimeSpan.FromSeconds(10), MaxConnectionsPerServer = 1
        }) { BaseAddress = new Uri(origin), Timeout = TimeSpan.FromSeconds(45) };
    }

    private HttpRequestMessage Request(HttpMethod method, string path, string? revision = null)
    {
        if (!path.StartsWith("/api/report-", StringComparison.Ordinal) || path.Contains('\\') || path.Contains('#') || path.Contains('?'))
            throw new InvalidOperationException("허용하지 않은 보고서 경로입니다.");
        var request = new HttpRequestMessage(method, path);
        request.Headers.Add("X-Review-Token", token);
        if (method == HttpMethod.Post)
        {
            if (!OwnedHostClient.ValidNonce(revision ?? "")) throw new InvalidOperationException("최신 보고서 상태를 먼저 확인하세요.");
            request.Headers.Add("Origin", origin);
            request.Headers.TryAddWithoutValidation("If-Match", revision);
        }
        return request;
    }

    public Task<JsonElement> StateAsync() => JsonAsync(Request(HttpMethod.Get, "/api/report-state"));

    public Task<JsonElement> PostAsync(string path, object body, string revision)
    {
        var request = Request(HttpMethod.Post, path, revision);
        request.Content = new ByteArrayContent(JsonSerializer.SerializeToUtf8Bytes(body));
        request.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
        return JsonAsync(request);
    }

    public Task<JsonElement> UploadAsync(string path, byte[] bytes, string name, string revision, bool material)
    {
        var request = Request(HttpMethod.Post, path, revision);
        request.Headers.Add("X-Report-Name", Uri.EscapeDataString(name));
        request.Content = new ByteArrayContent(bytes);
        request.Content.Headers.ContentType = MediaTypeHeaderValue.Parse(material ? "text/plain; charset=utf-8" : "application/octet-stream");
        return JsonAsync(request);
    }

    private async Task<JsonElement> JsonAsync(HttpRequestMessage request)
    {
        bool post = request.Method == HttpMethod.Post;
        using (request)
        {
            await exchange.WaitAsync();
            try
            {
                using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead);
                ReadOnlyMemory<byte> bytes = await ReadBoundedAsync(response.Content, 8 * 1024 * 1024);
                var result = JsonSerializer.Deserialize<JsonElement>(bytes.Span);
                if (!response.IsSuccessStatusCode)
                    throw new ReportApiException(result.Text("error", "보고서 요청이 거부되었습니다. 최신 상태를 확인하세요."), post && (int)response.StatusCode >= 500);
                if (result.ValueKind != JsonValueKind.Object) throw new InvalidDataException("보고서 응답 형식이 잘못되었습니다.");
                return result;
            }
            catch (ReportApiException) { throw; }
            catch (Exception error) when (error is HttpRequestException or IOException or TaskCanceledException or JsonException)
            {
                string detail = post
                    ? "요청의 처리 여부를 확인하지 못했습니다. 같은 요청을 다시 보내지 않습니다. 읽기 전용 상태 확인으로 결과를 확인하세요."
                    : "현재 상태를 읽지 못했습니다. 자료와 요청은 유지됩니다. ‘상태 다시 읽기’로 확인할 수 있습니다.";
                Exception cause = error.GetBaseException();
                throw new ReportApiException(detail + "\n연결 상세: " + cause.GetType().Name + ": " + cause.Message, post, error);
            }
            finally { exchange.Release(); }
        }
    }

    public Task<ReadOnlyMemory<byte>> SourceAsync(string expectedSha) => ReadArtifactAsync("/api/report-source", expectedSha);

    public Task<ReadOnlyMemory<byte>> ArtifactAsync(string version, string expectedSha) =>
        ReadArtifactAsync("/api/report-artifact/" + Uri.EscapeDataString(version), expectedSha);

    private async Task<ReadOnlyMemory<byte>> ReadArtifactAsync(string path, string expectedSha)
    {
        if (!OwnedHostClient.ValidNonce(expectedSha)) throw new InvalidDataException("문서의 정확한 SHA-256을 확인하지 못했습니다.");
        await exchange.WaitAsync();
        try
        {
            using var request = Request(HttpMethod.Get, path);
            using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead);
            if (!response.IsSuccessStatusCode) throw new IOException("문서를 읽을 수 없습니다. 최신 상태와 파일 검사를 확인하세요.");
            if (!response.Headers.TryGetValues("X-Artifact-Sha256", out var values) ||
                !string.Equals(values.SingleOrDefault(), expectedSha, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("수정본의 서버 SHA-256이 선택한 버전과 다릅니다. 파일을 저장하지 않았습니다.");
            ReadOnlyMemory<byte> bytes = await ReadBoundedAsync(response.Content, MaxArtifact);
            Span<byte> digest = stackalloc byte[32];
            SHA256.HashData(bytes.Span, digest);
            Span<char> hex = stackalloc char[64];
            if (!Convert.TryToHexString(digest, hex, out int written) || written != 64 ||
                !MemoryExtensions.Equals(hex, expectedSha.AsSpan(), StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("받은 HWPX의 SHA-256이 선택한 버전과 다릅니다. 파일을 저장하지 않았습니다.");
            return bytes;
        }
        finally { exchange.Release(); }
    }

    internal static async Task<ReadOnlyMemory<byte>> ReadBoundedAsync(HttpContent content, int max)
    {
        if (content.Headers.ContentLength is long size && (size < 0 || size > max)) throw new InvalidDataException("파일/응답 크기 제한을 초과했습니다.");
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(45));
        using var source = await content.ReadAsStreamAsync(deadline.Token);
        using var result = new MemoryStream(content.Headers.ContentLength is long known ? (int)known : 0);
        byte[] buffer = new byte[81920];
        while (true)
        {
            int count = await source.ReadAsync(buffer, deadline.Token);
            if (count == 0) break;
            if (result.Length + count > max) throw new InvalidDataException("파일/응답 크기 제한을 초과했습니다.");
            result.Write(buffer, 0, count);
        }
        return result.GetBuffer().AsMemory(0, checked((int)result.Length));
    }

    public void Dispose() => http.Dispose();
}

internal sealed class ReportApiException(string message, bool uncertain, Exception? inner = null) : Exception(message, inner)
{
    public bool Uncertain { get; } = uncertain;
}
