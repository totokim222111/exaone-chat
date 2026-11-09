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
    internal const string AuthGuidance = "API 키나 주소를 확인하세요. EXAONE API 키는 https://api.exaone.ai/v1 에서 씁니다.";
    private readonly HttpClient http = new(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = Timeout.InfiniteTimeSpan };
    // Pure request builder: host is explicit so provider behavior can be tested offline.
    internal static Dictionary<string, object> Body(AppSettings s, IEnumerable<ChatMessage> messages, bool stream, string host)
    {
        var official = host.Equals("api.exaone.ai", StringComparison.OrdinalIgnoreCase);
        var history = new List<object>();
        if (stream && !string.IsNullOrWhiteSpace(s.SystemPrompt)) history.Add(new { role = "system", content = s.SystemPrompt });
        history.AddRange(messages.Where(m => m.Role is "user" or "assistant" && !string.IsNullOrEmpty(m.Content))
            .Select(m => (object)new { role = m.Role, content = m.Content }));
        var body = new Dictionary<string, object> { ["model"] = s.Model, ["messages"] = history,
            ["stream"] = stream, ["temperature"] = s.Temperature, ["top_p"] = s.TopP,
            ["max_tokens"] = stream ? s.MaxTokens : official ? 64 : 8 };
        if (official)
        {
            body["reasoning_effort"] = stream ? s.ReasoningEffort : "low";
            if (stream) body["stream_options"] = new { include_usage = true };
        }
        else body["chat_template_kwargs"] = new { enable_thinking = stream && s.Thinking };
        if (host.Equals("api.friendli.ai", StringComparison.OrdinalIgnoreCase)) body["parse_reasoning"] = true;
        return body;
    }
    internal static string ErrorDetail(string body)
    {
        try
        {
            using var doc = JsonDocument.Parse(body);
            var root = doc.RootElement;
            static string? Text(JsonElement node, string property) => node.ValueKind == JsonValueKind.Object &&
                node.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String &&
                !string.IsNullOrWhiteSpace(value.GetString()) ? value.GetString() : null;
            if (root.ValueKind == JsonValueKind.Object && root.TryGetProperty("error", out var error) && Text(error, "message") is { } first) return first;
            if (Text(root, "description") is { } description) return description;
            if (Text(root, "message") is { } message) return message;
        }
        catch (JsonException) { }
        return body[..Math.Min(body.Length, 300)];
    }
    private async Task<HttpResponseMessage> Request(AppSettings s, string key, IEnumerable<ChatMessage> messages, bool stream, CancellationToken ct)
    {
        SettingsStore.Validate(s);
        if (string.IsNullOrWhiteSpace(key)) throw new ApiException(AuthGuidance, 401);
        using var req = new HttpRequestMessage(HttpMethod.Post, s.BaseUrl.TrimEnd('/') + "/chat/completions");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", key);
        req.Content = new StringContent(Json.Serialize(Body(s, messages, stream, new Uri(s.BaseUrl).Host)), Encoding.UTF8, "application/json");
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
                var detail = ErrorDetail(body);
                detail = detail.Replace(key, "[키 숨김]", StringComparison.Ordinal);
                throw new ApiException($"HTTP {(int)response.StatusCode} · {detail}" + ((int)response.StatusCode is 401 or 403 ? " · " + AuthGuidance : ""), (int)response.StatusCode);
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
            // A successful HTTP response verifies key/model, even when all tokens went to reasoning.
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested) { throw new ApiException("연결 확인 시간이 초과되었습니다."); }
    }
    public async Task Stream(AppSettings s, string key, IEnumerable<ChatMessage> messages, Action<string, string> emit, CancellationToken ct)
    {
        var contentStarted = false;
        void Emit(string kind, string value)
        {
            if (kind == "content" && !contentStarted)
            {
                value = value.TrimStart();
                if (value.Length == 0) return;
                contentStarted = true;
            }
            emit(kind, value);
        }
        var splitter = new ReasoningSplitter(Emit);
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
                    if (root.TryGetProperty("error", out _) || (!root.TryGetProperty("choices", out _) && root.TryGetProperty("result_code", out _)))
                        throw new ApiException(ErrorDetail(payload).Replace(key, "[키 숨김]", StringComparison.Ordinal));
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
