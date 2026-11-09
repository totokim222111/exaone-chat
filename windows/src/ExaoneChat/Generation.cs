namespace ExaoneChat;

internal sealed class Generation(Conversation conversation, AppSettings requestSettings, CancellationTokenSource cancellation)
{
    public Conversation Conversation { get; } = conversation;
    public AppSettings RequestSettings { get; } = requestSettings;
    public CancellationTokenSource Cancellation { get; } = cancellation;
    public Task Completion { get; set; } = Task.CompletedTask;
    public bool Stopped { get; private set; }
    public ChatError? Error { get; private set; }

    public async Task Run(ApiClient api, ConversationStore store, Action<string, string?, ChatError?> emit, string? testKey = null)
    {
        var c = Conversation;
        var history = c.Messages.ToList();
        var answer = new ChatMessage { Role = "assistant", Reasoning = "", ReasoningEffort =
            new Uri(RequestSettings.BaseUrl).Host.Equals("api.exaone.ai", StringComparison.OrdinalIgnoreCase) ? RequestSettings.ReasoningEffort : null };
        c.Model = RequestSettings.Model; c.Messages.Add(answer);
        try
        {
            store.Save(c); emit("start", null, null);
            var lastSave = DateTime.UtcNow;
            await api.Stream(RequestSettings, testKey ?? SettingsStore.Decrypt(RequestSettings), history, (kind, text) =>
            {
                if (kind == "reasoning") answer.Reasoning += text; else answer.Content += text;
                emit(kind, text, null);
                if (DateTime.UtcNow - lastSave > TimeSpan.FromSeconds(1)) { store.Save(c); lastSave = DateTime.UtcNow; }
            }, Cancellation.Token, usage => answer.Usage = usage);
        }
        catch (OperationCanceledException) when (Cancellation.IsCancellationRequested) { Stopped = true; }
        catch (Exception ex)
        {
            Error = ex is ApiException a ? a.ToError() : new(ex.Message);
            answer.Error = Error; emit("error", null, Error);
        }
        finally
        {
            answer.Stopped = Stopped;
            try { store.Save(c); }
            catch (Exception ex) { Error = new("대화를 저장할 수 없습니다. " + ex.Message); answer.Error = Error; emit("error", null, Error); }
            emit("done", null, Error);
        }
    }
}
