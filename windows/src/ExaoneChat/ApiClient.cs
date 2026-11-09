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
    internal static Dictionary<string, object> Body(AppSettings s, IEnumerable<ChatMessage> messages, bool stream, string host, string? searchContext = null)
    {
        var official = host.Equals("api.exaone.ai", StringComparison.OrdinalIgnoreCase);
        var history = new List<object>();
        if (stream && !string.IsNullOrWhiteSpace(s.SystemPrompt)) history.Add(new { role = "system", content = s.SystemPrompt });
        if (stream && searchContext is not null) history.Add(new { role = "system", content = searchContext });
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
    private async Task<HttpResponseMessage> Request(AppSettings s, string key, IEnumerable<ChatMessage> messages, bool stream, CancellationToken ct, Dictionary<string, object>? customBody = null, string? searchContext = null, KeyTrace? trace = null)
    {
        SettingsStore.Validate(s); trace ??= new(); trace.KeyIndex = null;
        var keys = KeyRotation.Keys(s, key);
        if (keys.Count == 0) throw new ApiException(AuthGuidance, 401);
        var json = Json.Serialize(customBody ?? Body(s, messages, stream, new Uri(s.BaseUrl).Host, searchContext));
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(120));
        foreach (var entry in keys)
        {
            var remaining = KeyRotation.Remaining(entry.Identity);
            if (remaining > 0) { trace.Record(entry.Index, remaining); continue; }
            using var req = new HttpRequestMessage(HttpMethod.Post, s.BaseUrl.TrimEnd('/') + "/chat/completions");
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", entry.Key);
            req.Content = new StringContent(json, Encoding.UTF8, "application/json");
            HttpResponseMessage response;
            try { response = await http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, timeout.Token); }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested) { throw new ApiException("서버 응답 시간이 초과되었습니다. 다시 시도하세요."); }
            if ((int)response.StatusCode == 429)
            {
                var seconds = KeyRotation.RetrySeconds(response.Headers.TryGetValues("Retry-After", out var values) ? values.FirstOrDefault() : null);
                response.Dispose(); KeyRotation.Pause(entry.Identity, seconds); trace.Record(entry.Index, seconds); continue;
            }
            trace.KeyIndex = entry.Index;
            if (!response.IsSuccessStatusCode)
            {
                using (response)
                {
                    var detail = ErrorDetail(await response.Content.ReadAsStringAsync(timeout.Token));
                    foreach (var secret in keys) detail = detail.Replace(secret.Key, "[키 숨김]", StringComparison.Ordinal);
                    throw new ApiException($"HTTP {(int)response.StatusCode} · {detail}" + ((int)response.StatusCode is 401 or 403 ? " · " + AuthGuidance : ""), (int)response.StatusCode);
                }
            }
            return response;
        }
        throw new ApiException($"등록된 키 {keys.Count}개가 모두 한도 초과 · 가장 빠른 재시도 {keys.Min(k => KeyRotation.Remaining(k.Identity))}초 후", 429);
    }
    public async Task Check(AppSettings s, string key, CancellationToken ct, KeyTrace? trace = null)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try
        {
            using var res = await Request(s, key, [new() { Content = "hi" }], false, timeout.Token, trace: trace);
            // A successful HTTP response verifies key/model, even when all tokens went to reasoning.
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested) { throw new ApiException("연결 확인 시간이 초과되었습니다."); }
    }
    internal async Task<string> Complete(AppSettings s, string key, Dictionary<string, object> body, CancellationToken ct, KeyTrace? trace = null)
    {
        using var res = await Request(s, key, [], false, ct, body, trace: trace);
        using var doc = JsonDocument.Parse(await res.Content.ReadAsStringAsync(ct));
        return doc.RootElement.GetProperty("choices")[0].GetProperty("message").GetProperty("content").GetString() ?? "";
    }
    public async Task Stream(AppSettings s, string key, IEnumerable<ChatMessage> messages, Action<string, string> emit, CancellationToken ct, Action<ChatUsage>? usage = null, string? searchContext = null, KeyTrace? trace = null)
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
        ChatUsage? latestUsage = null;
        try
        {
            using var res = await Request(s, key, messages, true, ct, searchContext: searchContext, trace: trace);
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
                    if (root.TryGetProperty("usage", out var used) && used.ValueKind == JsonValueKind.Object)
                    {
                        static long? Count(JsonElement node, string name) => node.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var count) && count >= 0 ? count : null;
                        var prompt = Count(used, "prompt_tokens");
                        var completion = Count(used, "completion_tokens");
                        var reasoning = Count(used, "reasoning_tokens");
                        if (reasoning is null && used.TryGetProperty("completion_tokens_details", out var details) && details.ValueKind == JsonValueKind.Object) reasoning = Count(details, "reasoning_tokens");
                        if (prompt is not null || completion is not null || reasoning is not null)
                        {
                            latestUsage = new(prompt ?? latestUsage?.PromptTokens, completion ?? latestUsage?.CompletionTokens, reasoning ?? latestUsage?.ReasoningTokens);
                            usage?.Invoke(latestUsage);
                        }
                    }
                    if (root.TryGetProperty("error", out _) || (!root.TryGetProperty("choices", out _) && root.TryGetProperty("result_code", out _)))
                        {
                        var detail = ErrorDetail(payload);
                        foreach (var secret in KeyRotation.Keys(s, key)) detail = detail.Replace(secret.Key, "[키 숨김]", StringComparison.Ordinal);
                        throw new ApiException(detail);
                    }
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
