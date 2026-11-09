using System.Text.Json;
using System.Text.Json.Serialization;

namespace ExaoneChat;

internal static class Json
{
    public static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    public static string Serialize<T>(T value) => JsonSerializer.Serialize(value, Options);
    public static T Read<T>(string value) => JsonSerializer.Deserialize<T>(value, Options) ?? throw new InvalidDataException("파일이 비어 있습니다.");
}

internal sealed class AppSettings
{
    public const int CurrentSchemaVersion = 1;
    public const string DefaultBaseUrl = "https://api.exaone.ai/v1";
    public const string DefaultModel = "k-exaone_v2.5";
    // Missing in legacy JSON: leave at zero so SettingsStore can migrate once.
    public int SchemaVersion { get; set; }
    public string ApiKey { get; set; } = ""; // DPAPI ciphertext only.
    public string BaseUrl { get; set; } = DefaultBaseUrl;
    public string Model { get; set; } = DefaultModel;
    public string SystemPrompt { get; set; } = "";
    public double Temperature { get; set; } = 1.0;
    public double TopP { get; set; } = 0.95;
    public int MaxTokens { get; set; } = 8192;
    public bool Thinking { get; set; } = true;
    public string ReasoningEffort { get; set; } = "max";
    public bool WebSearch { get; set; }
    public string SearchProvider { get; set; } = "auto";
    public string SearxngUrl { get; set; } = "http://127.0.0.1:8888";
    public string TavilyKey { get; set; } = ""; // DPAPI ciphertext, shared with the local MCP process.
    public string BraveKey { get; set; } = "";
    public WindowPlacement? Window { get; set; }
}
internal sealed record WindowPlacement(int X, int Y, int Width, int Height, bool Maximized);
internal sealed class ChatMessage
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Role { get; set; } = "user";
    public string Content { get; set; } = "";
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Reasoning { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.Now;
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public ChatError? Error { get; set; }
    public ChatUsage? Usage { get; set; }
    public bool Stopped { get; set; }
    public string? ReasoningEffort { get; set; }
    public SearchMetadata? Search { get; set; }
    public MessageTiming? Timing { get; set; }
}
internal sealed record ChatUsage(long? PromptTokens, long? CompletionTokens, long? ReasoningTokens);
internal sealed record SearchSource(int N, string Title, string Url, string Domain, string Snippet);
internal sealed class SearchMetadata
{
    public List<string> Queries { get; set; } = [];
    public string? Provider { get; set; }
    public List<SearchSource> Sources { get; set; } = [];
    public string? Error { get; set; }
}
internal sealed record MessageTiming(double? FirstTokenMs, double? ReasoningMs, double? AnswerMs, double TotalMs, double? SearchMs);

internal static class ChatHistory
{
    public static void Prepare(Conversation conversation, string content, bool retry, int? editIndex = null)
    {
        var messages = conversation.Messages;
        if (editIndex is int index)
        {
            if (index < 0 || index >= messages.Count || messages[index].Role != "user") throw new InvalidOperationException("편집할 사용자 메시지가 없습니다.");
            if (string.IsNullOrWhiteSpace(content)) throw new InvalidOperationException("메시지를 입력하세요.");
            messages[index].Content = content.Trim();
            messages.RemoveRange(index + 1, messages.Count - index - 1);
        }
        else if (retry)
        {
            if (messages.Count < 2 || messages[^1].Role != "assistant" || messages[^2].Role != "user") throw new InvalidOperationException("다시 생성할 답변이 없습니다.");
            messages.RemoveAt(messages.Count - 1);
        }
        else
        {
            if (string.IsNullOrWhiteSpace(content)) throw new InvalidOperationException("메시지를 입력하세요.");
            if (messages.Count == 0) conversation.Title = string.Concat(System.Globalization.StringInfo.GetTextElementEnumerator(content.Trim()).AsEnumerable().Take(30));
            messages.Add(new() { Role = "user", Content = content.Trim() });
        }
    }
}
internal sealed record ChatError(string Message, int? Status = null)
{
    public bool Auth => Status is 401 or 403;
}
internal sealed class Conversation
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Title { get; set; } = "새 대화";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.Now;
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.Now;
    public string Model { get; set; } = "";
    public string? ReasoningEffort { get; set; }
    public bool? Thinking { get; set; }
    public bool? WebSearch { get; set; }
    public List<ChatMessage> Messages { get; set; } = [];
}

internal static class ConversationOptions
{
    public static AppSettings Apply(Conversation c, AppSettings defaults, string? effort = null, bool? thinking = null, bool? webSearch = null)
    {
        var selected = effort ?? c.ReasoningEffort ?? defaults.ReasoningEffort;
        if (selected is not ("low" or "high" or "max")) throw new InvalidOperationException("추론 강도를 확인하세요.");
        c.ReasoningEffort = selected; c.Thinking = thinking ?? c.Thinking ?? defaults.Thinking;
        defaults.ReasoningEffort = selected; defaults.Thinking = c.Thinking.Value;
        c.WebSearch = webSearch ?? c.WebSearch ?? defaults.WebSearch; defaults.WebSearch = c.WebSearch.Value;
        var snapshot = Json.Read<AppSettings>(Json.Serialize(defaults));
        return snapshot;
    }
}
