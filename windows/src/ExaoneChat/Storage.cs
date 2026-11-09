using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ExaoneChat;

internal static class AtomicFile
{
    public static void Write<T>(string path, T value)
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

internal sealed class SettingsStore
{
    private readonly string path;
    public AppSettings Value { get; private set; }
    public SettingsStore(string root)
    {
        Directory.CreateDirectory(root);
        path = Path.Combine(root, "settings.json");
        Value = File.Exists(path) ? Json.Read<AppSettings>(File.ReadAllText(path)) : new();
    }
    public string Key()
    {
        if (Value.ApiKey.Length == 0) return "";
        try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(Value.ApiKey), null, DataProtectionScope.CurrentUser)); }
        catch (Exception ex) when (ex is CryptographicException or FormatException)
        { throw new InvalidOperationException("저장된 키를 복호화할 수 없습니다. 설정에서 키를 다시 입력하세요."); }
    }
    public object Public(AppSettings? settings = null)
    {
        var s = settings ?? Value;
        string key;
        try { key = Key(); } catch { key = ""; }
        return new { hasKey = key.Length > 0, keySuffix = key.Length > 0 ? key[^Math.Min(4, key.Length)..] : "",
            s.BaseUrl, s.Model, s.SystemPrompt, s.Temperature, s.TopP, s.MaxTokens, s.Thinking };
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
        s.Thinking = input.GetProperty("thinking").GetBoolean();
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
        if (!Uri.TryCreate(s.BaseUrl, UriKind.Absolute, out var uri) ||
            (uri.Scheme != "https" && !(uri.Scheme == "http" && uri.IsLoopback)) ||
            uri.UserInfo.Length > 0 || uri.Query.Length > 0 || uri.Fragment.Length > 0)
            throw new InvalidOperationException("Base URL은 HTTPS 주소여야 합니다. 로컬 검증은 HTTP를 사용할 수 있습니다.");
        if (string.IsNullOrWhiteSpace(s.Model)) throw new InvalidOperationException("모델을 입력하세요.");
        if (!double.IsFinite(s.Temperature) || s.Temperature is < 0 or > 2) throw new InvalidOperationException("Temperature는 0–2 사이여야 합니다.");
        if (!double.IsFinite(s.TopP) || s.TopP is <= 0 or > 1) throw new InvalidOperationException("Top P는 0 초과 1 이하여야 합니다.");
        if (s.MaxTokens is < 1 or > 1048576) throw new InvalidOperationException("최대 토큰은 1–1048576 사이여야 합니다.");
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
    public Conversation Get(string id) => Json.Read<Conversation>(File.ReadAllText(PathFor(id)));
    public List<object> List()
    {
        var items = new List<Conversation>();
        var damaged = 0;
        foreach (var file in Directory.EnumerateFiles(folder, "*.json"))
        {
            try { var c = Json.Read<Conversation>(File.ReadAllText(file)); PathFor(c.Id); items.Add(c); }
            catch (Exception ex) when (ex is JsonException or IOException or InvalidOperationException) { damaged++; }
        }
        if (damaged > 0) throw new InvalidDataException($"읽을 수 없는 대화 파일 {damaged}개가 있습니다. data/conversations를 확인하세요. 원본은 보존되었습니다.");
        return items.OrderByDescending(c => c.UpdatedAt).Select(c => (object)new { c.Id, c.Title, c.UpdatedAt, c.Model }).ToList();
    }
    public void Save(Conversation c) { c.UpdatedAt = DateTimeOffset.Now; AtomicFile.Write(PathFor(c.Id), c); }
    public void Delete(string id) => File.Delete(PathFor(id));
    public void Rename(string id, string title)
    {
        var c = Get(id); c.Title = string.IsNullOrWhiteSpace(title) ? "새 대화" : title.Trim()[..Math.Min(title.Trim().Length, 200)]; Save(c);
    }
}
