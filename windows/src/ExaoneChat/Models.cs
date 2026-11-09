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
    public WindowPlacement? Window { get; set; }
}
internal sealed record WindowPlacement(int X, int Y, int Width, int Height, bool Maximized);
internal sealed class ChatMessage
{
    public string Role { get; set; } = "user";
    public string Content { get; set; } = "";
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Reasoning { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.Now;
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public ChatError? Error { get; set; }
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
    public List<ChatMessage> Messages { get; set; } = [];
}
