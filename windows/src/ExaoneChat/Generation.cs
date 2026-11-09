namespace ExaoneChat;

internal sealed class Generation(Conversation conversation, AppSettings requestSettings, CancellationTokenSource cancellation)
{
    public Conversation Conversation { get; } = conversation;
    public AppSettings RequestSettings { get; } = requestSettings;
    public CancellationTokenSource Cancellation { get; } = cancellation;
    public Task Completion { get; set; } = Task.CompletedTask;
    public bool Stopped { get; private set; }
    public ChatError? Error { get; private set; }
    public event Action<object>? SearchEvent;

    public async Task Run(ApiClient api, ConversationStore store, Action<string, string?, ChatError?> emit, string? testKey = null)
    {
        var clock = System.Diagnostics.Stopwatch.StartNew();
        var trace = new KeyTrace();
        double? first = null, reasonAt = null, answerAt = null, searchMs = null, searchAtMs = null;
        var c = Conversation;
        var history = c.Messages.ToList();
        var answer = new ChatMessage { Role = "assistant", Reasoning = "", ReasoningEffort =
            new Uri(RequestSettings.BaseUrl).Host.Equals("api.exaone.ai", StringComparison.OrdinalIgnoreCase) ? RequestSettings.ReasoningEffort : null };
        c.Model = RequestSettings.Model; c.Messages.Add(answer);
        try
        {
            store.Save(c); emit("start", null, null);
            var key = testKey ?? SettingsStore.Decrypt(RequestSettings);
            if (string.IsNullOrWhiteSpace(key)) throw new ApiException(ApiClient.AuthGuidance, 401);
            string? context = null;
            if (RequestSettings.WebSearch)
            {
                answer.Search = new();
                var searchAt = clock.Elapsed.TotalMilliseconds;
                searchAtMs = searchAt;
                var question = history.LastOrDefault(m => m.Role == "user")?.Content ?? "";
                answer.Search.Queries = await SearchService.Plan(api, RequestSettings, key, history, question, Cancellation.Token, trace);
                SearchEvent?.Invoke(new { stage = "queries", queries = answer.Search.Queries });
                using var service = new SearchService();
                answer.Search = await service.Search(RequestSettings, answer.Search.Queries, Cancellation.Token);
                searchMs = clock.Elapsed.TotalMilliseconds - searchAt;
                if (answer.Search.Error is not null) SearchEvent?.Invoke(new { stage = "error", message = answer.Search.Error });
                else SearchEvent?.Invoke(new { stage = "results", provider = answer.Search.Provider, sources = answer.Search.Sources });
                context = SearchService.Context(answer.Search);
            }
            var lastSave = DateTime.UtcNow;
            await api.Stream(RequestSettings, key, history, (kind, text) =>
            {
                var elapsed = clock.Elapsed.TotalMilliseconds;
                first ??= elapsed;
                if (kind == "reasoning") reasonAt ??= elapsed; else answerAt ??= elapsed;
                if (kind == "reasoning") answer.Reasoning += text; else answer.Content += text;
                emit(kind, text, null);
                if (DateTime.UtcNow - lastSave > TimeSpan.FromSeconds(1)) { store.Save(c); lastSave = DateTime.UtcNow; }
            }, Cancellation.Token, usage => answer.Usage = usage, context, trace);
        }
        catch (OperationCanceledException) when (Cancellation.IsCancellationRequested) { Stopped = true; if (answer.Search is { Sources.Count: 0, Error: null }) answer.Search.Error = "검색이 완료되기 전에 중지되었습니다."; }
        catch (Exception ex)
        {
            Error = ex is ApiException a ? a.ToError() : new(ex.Message);
            answer.Error = Error; emit("error", null, Error);
        }
        finally
        {
            answer.Stopped = Stopped; answer.KeyIndex = trace.KeyIndex; answer.KeyFailover = trace.KeyFailover.Count > 0 ? trace.KeyFailover : null;
            var total = clock.Elapsed.TotalMilliseconds;
            if (searchAtMs is double searchStart) searchMs ??= total - searchStart;
            answer.Timing = new(first, reasonAt is double reason ? Math.Max(0, (answerAt ?? total) - reason) : null,
                answerAt is double started ? Math.Max(0, total - started) : null, total, searchMs);
            try { store.Save(c); }
            catch (Exception ex) { Error = new("대화를 저장할 수 없습니다. " + ex.Message); answer.Error = Error; emit("error", null, Error); }
            emit("done", null, Error);
        }
    }
}
