using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace HWPReview.Native;

internal sealed class OwnedHostClient : IDisposable
{
    private const int MaxLineBytes = 1_048_576;
    private static readonly byte[] Newline = [10];
    private readonly Stream input = Console.OpenStandardInput();
    private readonly Stream output = Console.OpenStandardOutput();
    private readonly SemaphoreSlim gate = new(1, 1);
    private long sequence;
    public bool Broken { get; private set; }

    public async Task<JsonElement> CallAsync(string method, object parameters)
    {
        if (Broken) throw new IOException("백엔드 연결이 끊어졌습니다. 요청을 다시 보내지 않습니다.");
        await gate.WaitAsync();
        try
        {
            if (Broken) throw new IOException("백엔드 연결이 끊어졌습니다.");
            string id = Interlocked.Increment(ref sequence).ToString(System.Globalization.CultureInfo.InvariantCulture);
            byte[] request = JsonSerializer.SerializeToUtf8Bytes(new { id, method, @params = parameters });
            if (request.Length > MaxLineBytes) throw new InvalidOperationException("요청 크기 제한을 초과했습니다.");
            try
            {
                await output.WriteAsync(request);
                await output.WriteAsync(Newline);
                await output.FlushAsync();
                using var bytes = new MemoryStream();
                byte[] buffer = new byte[4096];
                using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(45));
                while (true)
                {
                    int count = await input.ReadAsync(buffer, deadline.Token);
                    if (count == 0) throw new EndOfStreamException();
                    int newline = Array.IndexOf(buffer, (byte)10, 0, count);
                    int length = newline < 0 ? count : newline;
                    if (bytes.Length + length > MaxLineBytes) throw new InvalidDataException("백엔드 응답 크기 제한을 초과했습니다.");
                    bytes.Write(buffer, 0, length);
                    if (newline < 0) continue;
                    if (newline != count - 1) throw new InvalidDataException("요청하지 않은 백엔드 응답을 받았습니다.");
                    break;
                }
                using var document = JsonDocument.Parse(bytes.GetBuffer().AsMemory(0, (int)bytes.Length));
                JsonElement envelope = document.RootElement;
                if (envelope.ValueKind != JsonValueKind.Object || envelope.Text("id") != id ||
                    !envelope.TryGetProperty("ok", out var ok) || ok.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                    throw new InvalidDataException("백엔드 응답의 소유권을 확인할 수 없습니다.");
                if (ok.GetBoolean())
                {
                    var result = envelope.Part("result");
                    if (result.ValueKind != JsonValueKind.Object) throw new InvalidDataException("백엔드 응답 형식이 잘못되었습니다.");
                    return result.Clone();
                }
                throw new HostCommandException(envelope.Text("error", "요청이 거부되었습니다. 현재 상태를 확인하세요."));
            }
            catch (HostCommandException) { throw; }
            catch (Exception error) when (error is IOException or JsonException or ObjectDisposedException or OperationCanceledException)
            {
                Broken = true;
                throw new IOException("백엔드 응답을 확인하지 못했습니다. 이미 보낸 요청은 자동 재전송하지 않습니다. 창을 닫아도 실행 중인 작업을 강제 종료하지 않습니다.", error);
            }
        }
        finally { gate.Release(); }
    }

    public static bool ValidNonce(string value) => Regex.IsMatch(value, "\\A[0-9a-f]{64}\\z", RegexOptions.CultureInvariant);
    public void Dispose() { input.Dispose(); output.Dispose(); gate.Dispose(); }
}

internal sealed class HostCommandException(string message) : Exception(message);

internal static class JsonValue
{
    public static JsonElement Part(this JsonElement value, string key) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out var part) ? part : default;
    public static string Text(this JsonElement value, string key, string fallback = "") =>
        value.Part(key).ValueKind == JsonValueKind.String ? value.Part(key).GetString()! : fallback;
    public static bool Flag(this JsonElement value, string key) => value.Part(key).ValueKind == JsonValueKind.True;
    public static JsonElement[] Items(this JsonElement value) => value.ValueKind == JsonValueKind.Array ? value.EnumerateArray().ToArray() : [];
    public static string Display(this JsonElement value) => value.ValueKind == JsonValueKind.String ? value.GetString()! : value.ValueKind == JsonValueKind.Undefined ? "" : value.GetRawText();
}
