using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Collections.Concurrent;

namespace ExaoneChat;

internal static class AtomicFile
{
    private static readonly ConcurrentDictionary<string, object> Gates = new(StringComparer.OrdinalIgnoreCase);
    public static object Gate(string path) => Gates.GetOrAdd(Path.GetFullPath(path), _ => new());
    public static void Write<T>(string path, T value)
    {
        lock (Gate(path))
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var temp = path + ".tmp";
            using (var file = new FileStream(temp, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                JsonSerializer.Serialize(file, value, Json.Options);
                file.Flush(true);
            }
            File.Move(temp, path, true);
        }
    }
}

internal sealed class SettingsStore
{
    private readonly string path;
    public AppSettings Value { get; private set; }
    public SettingsStore(string root)
    {
        Directory.CreateDirectory(root);
        path = Path.Combine(root, "settings.json");
        Value = File.Exists(path) ? Json.Read<AppSettings>(File.ReadAllText(path)) : new() { SchemaVersion = AppSettings.CurrentSchemaVersion };
        if (Migrate(Value)) Save();
    }
    internal static bool Migrate(AppSettings value)
    {
        if (value.SchemaVersion >= AppSettings.CurrentSchemaVersion) return false;
        if (value.BaseUrl == "https://api.friendli.ai/serverless/v1" && value.Model == "LGAI-EXAONE/K-EXAONE-2.0-750B-A37B")
        {
            value.BaseUrl = AppSettings.DefaultBaseUrl;
            value.Model = AppSettings.DefaultModel;
        }
        value.SchemaVersion = AppSettings.CurrentSchemaVersion;
        return true;
    }
    public string Key()
    {
        if (Value.ApiKey.Length == 0) return "";
        try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(Value.ApiKey), null, DataProtectionScope.CurrentUser)); }
        catch (Exception ex) when (ex is CryptographicException or FormatException)
        { throw new InvalidOperationException("저장된 키를 복호화할 수 없습니다. 설정에서 키를 다시 입력하세요."); }
    }
    public object Public(AppSettings? settings = null, bool? serverReady = null)
    {
        var s = settings ?? Value;
        string key;
        try { key = Key(); } catch { key = ""; }
        return new { hasKey = key.Length > 0, keySuffix = key.Length > 0 ? key[^Math.Min(4, key.Length)..] : "",
            s.BaseUrl, s.Model, s.SystemPrompt, s.Temperature, s.TopP, s.MaxTokens, s.Thinking, s.ReasoningEffort,
            s.WebSearch, s.SearchProvider, s.SearxngUrl, s.Theme,
            searchKeys = new { tavily = KeySummary(s.TavilyKey), brave = KeySummary(s.BraveKey) },
            search = new { available = SearchService.ChooseProvider(s.SearchProvider, s.TavilyKey.Length > 0, s.BraveKey.Length > 0, serverReady == false ? "" : s.SearxngUrl) is not null,
                serverProvider = serverReady != false && s.SearxngUrl.Length > 0 ? "searxng" : null, providers = new[] { "searxng", "tavily", "brave" } } };
    }
    public AppSettings Draft(JsonElement input)
    {
        var s = Json.Read<AppSettings>(Json.Serialize(Value));
        s.BaseUrl = input.GetProperty("baseUrl").GetString()?.Trim().TrimEnd('/') ?? "";
        s.Model = input.GetProperty("model").GetString()?.Trim() ?? "";
        s.SystemPrompt = input.GetProperty("systemPrompt").GetString() ?? "";
        s.Temperature = input.GetProperty("temperature").GetDouble();
        s.TopP = input.GetProperty("topP").GetDouble();
        s.MaxTokens = input.GetProperty("maxTokens").GetInt32();
        if (input.TryGetProperty("thinking", out var thinking)) s.Thinking = thinking.GetBoolean();
        if (input.TryGetProperty("theme", out var theme)) s.Theme = theme.GetString() ?? "system";
        if (input.TryGetProperty("reasoningEffort", out var effort)) s.ReasoningEffort = effort.GetString() ?? "max";
        if (input.TryGetProperty("webSearch", out var search)) s.WebSearch = search.GetBoolean();
        if (input.TryGetProperty("searchProvider", out var provider)) s.SearchProvider = provider.GetString() ?? "auto";
        if (input.TryGetProperty("searxngUrl", out var searchUrl)) s.SearxngUrl = searchUrl.GetString()?.Trim().TrimEnd('/') ?? "";
        if (input.TryGetProperty("tavilyKey", out var tavily)) s.TavilyKey = Protect(tavily.GetString() ?? "");
        if (input.TryGetProperty("braveKey", out var brave)) s.BraveKey = Protect(brave.GetString() ?? "");
        Validate(s);
        if (input.TryGetProperty("deleteKey", out var del) && del.GetBoolean()) s.ApiKey = "";
        if (input.TryGetProperty("newApiKey", out var value) && !string.IsNullOrWhiteSpace(value.GetString()))
        {
            var key = value.GetString()!.Trim();
            if (key.Any(char.IsControl)) throw new InvalidOperationException("API 키 형식을 확인하세요.");
            s.ApiKey = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(key), null, DataProtectionScope.CurrentUser));
        }
        return s;
    }
    public static void Validate(AppSettings s)
    {
        if (s.Theme is not ("light" or "dark" or "system")) throw new InvalidOperationException("화면 모드를 확인하세요.");
        if (!Uri.TryCreate(s.BaseUrl, UriKind.Absolute, out var uri) ||
            (uri.Scheme != "https" && !(uri.Scheme == "http" && uri.IsLoopback)) ||
            uri.UserInfo.Length > 0 || uri.Query.Length > 0 || uri.Fragment.Length > 0)
            throw new InvalidOperationException("Base URL은 HTTPS 주소여야 합니다. 로컬 검증은 HTTP를 사용할 수 있습니다.");
        if (string.IsNullOrWhiteSpace(s.Model)) throw new InvalidOperationException("모델을 입력하세요.");
        if (!double.IsFinite(s.Temperature) || s.Temperature is < 0 or > 2) throw new InvalidOperationException("Temperature는 0–2 사이여야 합니다.");
        if (!double.IsFinite(s.TopP) || s.TopP is <= 0 or > 1) throw new InvalidOperationException("Top P는 0 초과 1 이하여야 합니다.");
        if (s.MaxTokens is < 1 or > 1048576) throw new InvalidOperationException("최대 토큰은 1–1048576 사이여야 합니다.");
        if (uri.Host.Equals("api.exaone.ai", StringComparison.OrdinalIgnoreCase) && s.MaxTokens > 524288)
            throw new InvalidOperationException("EXAONE API의 입력과 최대 토큰 합계는 524288 이하여야 합니다.");
        if (s.ReasoningEffort is not ("low" or "high" or "max")) throw new InvalidOperationException("추론 강도를 확인하세요.");
        if (s.SearchProvider is not ("auto" or "searxng" or "tavily" or "brave")) throw new InvalidOperationException("검색 제공자를 확인하세요.");
        if (s.SearxngUrl.Length > 0 && (!Uri.TryCreate(s.SearxngUrl, UriKind.Absolute, out var searchUri) || searchUri.Scheme is not ("http" or "https") || searchUri.UserInfo.Length > 0 || searchUri.Query.Length > 0 || searchUri.Fragment.Length > 0)) throw new InvalidOperationException("SearXNG 주소는 인증정보 없는 http(s) 주소여야 합니다.");
    }
    public static string Protect(string key)
    {
        if (key.Any(c => c < 33 || c > 126) || key.Length > 8192) throw new InvalidOperationException("검색 키 형식을 확인하세요.");
        return key.Length == 0 ? "" : Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(key), null, DataProtectionScope.CurrentUser));
    }
    public static string Reveal(string ciphertext) => ciphertext.Length == 0 ? "" : Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(ciphertext), null, DataProtectionScope.CurrentUser));
    private static object KeySummary(string ciphertext)
    {
        try { var key = Reveal(ciphertext); return new { hasKey = key.Length > 0, last4 = key[^Math.Min(4, key.Length)..] }; }
        catch { return new { hasKey = false, last4 = "" }; }
    }
    public static string Decrypt(AppSettings s) => s.ApiKey.Length == 0 ? "" : Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(s.ApiKey), null, DataProtectionScope.CurrentUser));
    public void Save(AppSettings? value = null)
    {
        var next = value ?? Value;
        AtomicFile.Write(path, next);
        Value = next;
    }
}

internal sealed class ConversationStore
{
    private readonly string folder;
    public ConversationStore(string root) { folder = Path.Combine(root, "conversations"); Directory.CreateDirectory(folder); }
    private string PathFor(string id)
    {
        if (!Guid.TryParseExact(id, "N", out _)) throw new InvalidOperationException("대화 ID가 올바르지 않습니다.");
        return Path.Combine(folder, id + ".json");
    }
    public Conversation Get(string id) { var path = PathFor(id); lock (AtomicFile.Gate(path)) return Json.Read<Conversation>(File.ReadAllText(path)); }
    public List<object> List()
    {
        var items = new List<Conversation>();
        var damaged = 0;
        foreach (var file in Directory.EnumerateFiles(folder, "*.json"))
        {
            try { lock (AtomicFile.Gate(file)) { var c = Json.Read<Conversation>(File.ReadAllText(file)); PathFor(c.Id); items.Add(c); } }
            catch (Exception ex) when (ex is JsonException or IOException or InvalidOperationException) { damaged++; }
        }
        if (damaged > 0) throw new InvalidDataException($"읽을 수 없는 대화 파일 {damaged}개가 있습니다. data/conversations를 확인하세요. 원본은 보존되었습니다.");
        return items.OrderByDescending(c => c.UpdatedAt).Select(c => (object)new { c.Id, c.Title, c.UpdatedAt, c.Model, c.ReasoningEffort, c.Thinking, c.WebSearch }).ToList();
    }
    public void Save(Conversation c) { var path = PathFor(c.Id); lock (AtomicFile.Gate(path)) { c.UpdatedAt = DateTimeOffset.Now; AtomicFile.Write(path, c); } }
    public void Delete(string id) { var path = PathFor(id); lock (AtomicFile.Gate(path)) File.Delete(path); }
    public void Rename(string id, string title)
    {
        var c = Get(id); c.Title = string.IsNullOrWhiteSpace(title) ? "새 대화" : title.Trim()[..Math.Min(title.Trim().Length, 200)]; Save(c);
    }
}
