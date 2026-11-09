using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace ExaoneChat;

internal sealed class ApiException(string message, int? status = null) : Exception(message)
{
    public int? Status { get; } = status;
    public ChatError ToError() => new(Message, Status);
}

internal sealed class ApiClient : IDisposable
{
    private readonly HttpClient http = new(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = Timeout.InfiniteTimeSpan };
    internal static Dictionary<string, object> Body(AppSettings s, IEnumerable<ChatMessage> messages, bool stream)
    {
        var history = new List<object>();
        if (stream && !string.IsNullOrWhiteSpace(s.SystemPrompt)) history.Add(new { role = "system", content = s.SystemPrompt });
        history.AddRange(messages.Where(m => m.Role is "user" or "assistant" && !string.IsNullOrEmpty(m.Content))
            .Select(m => (object)new { role = m.Role, content = m.Content }));
        var body = new Dictionary<string, object> { ["model"] = s.Model, ["messages"] = history,
            ["stream"] = stream, ["temperature"] = s.Temperature, ["top_p"] = s.TopP,
            ["max_tokens"] = stream ? s.MaxTokens : 8,
            ["chat_template_kwargs"] = new { enable_thinking = stream && s.Thinking } };
        if (new Uri(s.BaseUrl).Host.Equals("api.friendli.ai", StringComparison.OrdinalIgnoreCase)) body["parse_reasoning"] = true;
        return body;
    }
    private async Task<HttpResponseMessage> Request(AppSettings s, string key, IEnumerable<ChatMessage> messages, bool stream, CancellationToken ct)
    {
        SettingsStore.Validate(s);
        if (string.IsNullOrWhiteSpace(key)) throw new ApiException("API 키를 확인하세요. 설정에서 키를 입력하세요.", 401);
        using var req = new HttpRequestMessage(HttpMethod.Post, s.BaseUrl.TrimEnd('/') + "/chat/completions");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", key);
        req.Content = new StringContent(Json.Serialize(Body(s, messages, stream)), Encoding.UTF8, "application/json");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(120));
        HttpResponseMessage response;
        try { response = await http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, timeout.Token); }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested) { throw new ApiException("서버 응답 시간이 초과되었습니다. 다시 시도하세요."); }
        if (!response.IsSuccessStatusCode)
        {
            using (response)
            {
                var body = await response.Content.ReadAsStringAsync(timeout.Token);
                string detail;
                try { using var doc = JsonDocument.Parse(body); detail = doc.RootElement.GetProperty("error").GetProperty("message").GetString() ?? body; }
                catch { detail = body[..Math.Min(body.Length, 300)]; }
                detail = detail.Replace(key, "[키 숨김]", StringComparison.Ordinal);
                throw new ApiException($"HTTP {(int)response.StatusCode} · {detail}" + ((int)response.StatusCode is 401 or 403 ? " · API 키를 확인하세요." : ""), (int)response.StatusCode);
            }
        }
        return response;
    }
    public async Task Check(AppSettings s, string key, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try
        {
            using var res = await Request(s, key, [new() { Content = "hi" }], false, timeout.Token);
            using var doc = JsonDocument.Parse(await res.Content.ReadAsStringAsync(timeout.Token));
            if (!doc.RootElement.TryGetProperty("choices", out var choices) || choices.GetArrayLength() == 0)
                throw new ApiException("응답 형식이 올바르지 않습니다.");
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested) { throw new ApiException("연결 확인 시간이 초과되었습니다."); }
    }
    public async Task Stream(AppSettings s, string key, IEnumerable<ChatMessage> messages, Action<string, string> emit, CancellationToken ct)
    {
        var splitter = new ReasoningSplitter(emit);
        try
        {
            using var res = await Request(s, key, messages, true, ct);
            using var stream = await res.Content.ReadAsStreamAsync(ct);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            var data = new StringBuilder();
            var done = false;
            void Dispatch()
            {
                if (data.Length == 0 || done) return;
                var payload = data.ToString().Trim(); data.Clear();
                if (payload == "[DONE]") { done = true; return; }
                try
                {
                    using var doc = JsonDocument.Parse(payload);
                    var root = doc.RootElement;
                    if (root.TryGetProperty("error", out var error))
                        throw new ApiException(error.GetProperty("message").GetString()?.Replace(key, "[키 숨김]", StringComparison.Ordinal) ?? "스트림 오류");
                    if (!root.TryGetProperty("choices", out var choices) || choices.GetArrayLength() == 0) return;
                    if (!choices[0].TryGetProperty("delta", out var delta)) return;
                    if (delta.TryGetProperty("reasoning_content", out var reason) && reason.ValueKind == JsonValueKind.String && reason.GetString() is { Length: > 0 } r) emit("reasoning", r);
                    if (delta.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.String && content.GetString() is { Length: > 0 } c) splitter.Push(c);
                }
                catch (JsonException) { throw new ApiException("스트리밍 응답 JSON을 읽을 수 없습니다."); }
            }
            while (!done)
            {
                using var idle = CancellationTokenSource.CreateLinkedTokenSource(ct);
                idle.CancelAfter(TimeSpan.FromSeconds(120));
                string? line;
                try { line = await reader.ReadLineAsync(idle.Token); }
                catch (OperationCanceledException) when (!ct.IsCancellationRequested) { throw new ApiException("응답 대기 시간이 초과되었습니다. 받은 내용은 저장됩니다."); }
                if (line is null) { Dispatch(); if (!done) throw new ApiException("연결이 중간에 종료되었습니다. 받은 내용은 저장됩니다."); break; }
                if (line.Length == 0) { Dispatch(); continue; }
                if (line.StartsWith("data:", StringComparison.Ordinal))
                {
                    if (data.Length > 0) data.Append('\n');
                    data.Append(line.AsSpan(5).TrimStart());
                }
            }
        }
        catch (HttpRequestException) { throw new ApiException("네트워크에 연결할 수 없습니다. 주소와 연결 상태를 확인하세요."); }
        catch (IOException) when (!ct.IsCancellationRequested) { throw new ApiException("네트워크 연결이 끊겼습니다. 받은 내용은 저장됩니다."); }
        finally { splitter.Finish(); }
    }
    public void Dispose() => http.Dispose();
}
