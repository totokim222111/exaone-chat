using System.Text;
using System.Text.Json;
using System.Security.Cryptography;

namespace ExaoneChat;

internal static class SelfTest
{
    public static async Task<int> Run(string url)
    {
        var results = new List<object>();
        var failures = 0;
        using var api = new ApiClient();
        var root = Path.Combine(AppContext.BaseDirectory, "tools", ".selftest-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        async Task Test(string name, Func<Task> run)
        {
            try { await run(); results.Add(new { name, passed = true }); }
            catch (Exception ex) { failures++; results.Add(new { name, passed = false, error = ex.Message }); }
        }
        static void Assert(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
        AppSettings Settings(string mode) => new() { BaseUrl = url.TrimEnd('/') + "/" + mode + "/v1", Model = "mock-exaone", SystemPrompt = "test-system" };
        async Task<(string content, string reasoning)> Stream(string mode, CancellationToken ct = default)
        {
            var content = new StringBuilder(); var reasoning = new StringBuilder();
            await api.Stream(Settings(mode), "mock-key", [new() { Content = "test" }], (kind, value) => (kind == "content" ? content : reasoning).Append(value), ct);
            return (content.ToString(), reasoning.ToString());
        }
        await Test("reasoning_content + content + DONE", async () => { var r = await Stream("separate"); Assert(r == ("최종 답변", "분리된 생각"), "Separate deltas mismatch"); });
        await Test("think tag across SSE chunks", async () => { var r = await Stream("think"); Assert(r == ("앞뒤 답변", "경계 생각"), "Think split mismatch"); });
        await Test("thought tag across SSE chunks", async () => { var r = await Stream("thought"); Assert(r == ("답변", "다른 생각"), "Thought split mismatch"); });
        await Test("all tag boundaries", () =>
        {
            foreach (var tag in new[] { "think", "thought", "THINK", "THOUGHT" })
            {
                var raw = $"A<{tag}>비밀</{tag}>B";
                for (var n = 1; n < raw.Length; n++)
                {
                    var a = ""; var b = ""; var split = new ReasoningSplitter((kind, s) => { if (kind == "content") a += s; else b += s; });
                    split.Push(raw[..n]); split.Push(raw[n..]); split.Finish(); Assert(a == "AB" && b == "비밀", $"Boundary {n}");
                }
            }
            return Task.CompletedTask;
        });
        await Test("HTTP 401 error.message + key redaction", async () =>
        {
            try { await Stream("unauthorized"); throw new Exception("Expected HTTP failure"); }
            catch (ApiException ex) { Assert(ex.Status == 401 && ex.Message.Contains("invalid key") && ex.ToError().Auth && !ex.Message.Contains("mock-key"), "401 mismatch"); }
        });
        await Test("HTTP 403 fallback body", async () =>
        {
            try { await Stream("forbidden"); throw new Exception("Expected HTTP failure"); }
            catch (ApiException ex) { Assert(ex.Status == 403 && ex.Message.Contains("denied") && ex.Message.Length < 400, "403 fallback mismatch"); }
        });
        await Test("midstream cancellation retains partial + persistence", async () =>
        {
            using var cts = new CancellationTokenSource();
            var c = new Conversation(); var m = new ChatMessage { Role = "assistant" }; c.Messages.Add(m);
            var canceled = false;
            try { await api.Stream(Settings("slow"), "mock-key", [new() { Content = "test" }], (kind, text) => { if (kind == "content") { m.Content += text; cts.Cancel(); } }, cts.Token); }
            catch (OperationCanceledException) { canceled = true; }
            var store = new ConversationStore(root); store.Save(c);
            Assert(canceled && store.Get(c.Id).Messages[0].Content == "부분 응답", "Cancel/persistence mismatch");
        });
        await Test("unexpected EOF retains content", async () =>
        {
            var text = "";
            try { await api.Stream(Settings("disconnect"), "mock-key", [new() { Content = "test" }], (_, v) => text += v, default); throw new Exception("Expected EOF error"); }
            catch (ApiException) { Assert(text == "받은 내용", "EOF lost data"); }
        });
        await Test("nonstream connection check request", () => api.Check(Settings("check"), "mock-key", default));
        await Test("request options + assistant history excludes reasoning", () =>
        {
            var s = Settings("check"); s.BaseUrl = "https://api.friendli.ai/serverless/v1";
            var body = Json.Serialize(ApiClient.Body(s, [new() { Role = "assistant", Content = "answer", Reasoning = "secret" }], true, "api.friendli.ai"));
            using var d = JsonDocument.Parse(body);
            Assert(d.RootElement.GetProperty("parse_reasoning").GetBoolean() && !body.Contains("secret") && d.RootElement.GetProperty("messages")[0].GetProperty("role").GetString() == "system", "Friendli/history mismatch");
            Assert(!ApiClient.Body(Settings("check"), [], true, "127.0.0.1").ContainsKey("parse_reasoning"), "Non-Friendli parse_reasoning leaked");
            return Task.CompletedTask;
        });
        await Test("DPAPI ciphertext + safe JS settings + replace/delete", () =>
        {
            var store = new SettingsStore(root);
            var draft = JsonSerializer.SerializeToElement(new { baseUrl = Settings("check").BaseUrl, model = "mock-exaone", systemPrompt = "", temperature = 1, topP = .95, maxTokens = 8192, thinking = true, newApiKey = "fake-selftest-1234", deleteKey = false });
            store.Save(store.Draft(draft));
            var raw = File.ReadAllText(Path.Combine(root, "settings.json"));
            Assert(!raw.Contains("fake-selftest") && new SettingsStore(root).Key() == "fake-selftest-1234" && !Json.Serialize(store.Public()).Contains("fake-selftest"), "Key protection mismatch");
            var empty = JsonSerializer.SerializeToElement(new { baseUrl = Settings("check").BaseUrl, model = "mock-exaone", systemPrompt = "", temperature = 1, topP = .95, maxTokens = 8192, thinking = true, newApiKey = "", deleteKey = false });
            store.Save(store.Draft(empty)); Assert(store.Key() == "fake-selftest-1234", "Empty key did not preserve");
            var del = JsonSerializer.SerializeToElement(new { baseUrl = Settings("check").BaseUrl, model = "mock-exaone", systemPrompt = "", temperature = 1, topP = .95, maxTokens = 8192, thinking = true, newApiKey = "", deleteKey = true });
            store.Save(store.Draft(del)); Assert(store.Key() == "", "Delete failed"); return Task.CompletedTask;
        });
        await Test("EXAONE HTTP 401 description + auth guidance", async () =>
        {
            try { await Stream("exaone401"); throw new Exception("Expected HTTP failure"); }
            catch (ApiException ex)
            {
                Assert(ex.Status == 401 && ex.ToError().Auth && ex.Message.Contains("공식 키 오류") &&
                    ex.Message.Contains(ApiClient.AuthGuidance) && !ex.Message.Contains("fallback") && !ex.Message.Contains("result_code"), "EXAONE error extraction mismatch");
            }
        });
        await Test("error extraction priority + fallback", () =>
        {
            Assert(ApiClient.ErrorDetail("""{"error":{"message":"first"},"description":"second","message":"third"}""") == "first", "error.message priority");
            Assert(ApiClient.ErrorDetail("""{"error":null,"description":"second","message":"third"}""") == "second", "description priority");
            Assert(ApiClient.ErrorDetail("""{"description":"","message":"third"}""") == "third", "message fallback");
            Assert(ApiClient.ErrorDetail(new string('x', 400)) == new string('x', 300), "raw body limit");
            Assert(ApiClient.ErrorDetail("[]") == "[]", "non-object body");
            return Task.CompletedTask;
        });
        await Test("empty choices usage chunks ignored", async () =>
        {
            var result = await Stream("usage");
            Assert(result == ("사용량 답변", "사용량 생각"), "Usage chunk parsing mismatch");
        });
        await Test("fragmented UTF8 SSE lines + DONE", async () =>
        {
            var result = await Stream("fragmented");
            Assert(result == ("잘린 줄 답변", "잘린 줄 생각"), "Fragmented line parsing mismatch");
        });
        await Test("leading answer whitespace trimmed before display + save", async () =>
        {
            var c = new Conversation(); var answer = new ChatMessage { Role = "assistant", Reasoning = "" }; c.Messages.Add(answer);
            await api.Stream(Settings("leading"), "mock-key", [new() { Content = "test" }], (kind, text) =>
            {
                if (kind == "content")
                {
                    if (answer.Content.Length == 0) Assert(text.Length > 0 && !char.IsWhiteSpace(text[0]), "Leading whitespace emitted to UI");
                    answer.Content += text;
                }
                else answer.Reasoning += text;
            }, default);
            var store = new ConversationStore(root); store.Save(c);
            Assert(store.Get(c.Id).Messages[0].Content == "첫 줄\n\n  둘째 줄  " && answer.Reasoning == "생각", "Trim affected content or persistence");
        });
        await Test("EXAONE request body for all reasoning efforts", () =>
        {
            var s = new AppSettings { Thinking = false };
            foreach (var effort in new[] { "low", "high", "max" })
            {
                s.ReasoningEffort = effort;
                using var doc = JsonDocument.Parse(Json.Serialize(ApiClient.Body(s, [new() { Content = "test" }], true, "API.EXAONE.AI")));
                var b = doc.RootElement;
                Assert(b.GetProperty("reasoning_effort").GetString() == effort && b.GetProperty("model").GetString() == "k-exaone_v2.5" &&
                    !b.TryGetProperty("chat_template_kwargs", out _) && !b.TryGetProperty("parse_reasoning", out _) &&
                    b.GetProperty("stream_options").GetProperty("include_usage").GetBoolean(), "EXAONE body mismatch");
            }
            Assert(!s.Thinking, "Builder mutated settings");
            return Task.CompletedTask;
        });
        await Test("EXAONE connection body uses 64 tokens + low effort", () =>
        {
            var s = new AppSettings { SystemPrompt = "not for connection check", ReasoningEffort = "max", MaxTokens = 8192 };
            using var doc = JsonDocument.Parse(Json.Serialize(ApiClient.Body(s, [new() { Content = "hi" }], false, "api.exaone.ai")));
            var b = doc.RootElement;
            Assert(b.GetProperty("max_tokens").GetInt32() == 64 && b.GetProperty("reasoning_effort").GetString() == "low" &&
                !b.GetProperty("stream").GetBoolean() && !b.TryGetProperty("chat_template_kwargs", out _) && !b.TryGetProperty("parse_reasoning", out _) &&
                b.GetProperty("messages").GetArrayLength() == 1 && b.GetProperty("messages")[0].GetProperty("content").GetString() == "hi", "EXAONE check body mismatch");
            return Task.CompletedTask;
        });
        await Test("vLLM request preserves thinking toggle without provider fields", () =>
        {
            foreach (var thinking in new[] { true, false })
            {
                using var doc = JsonDocument.Parse(Json.Serialize(ApiClient.Body(new() { Thinking = thinking }, [], true, "localhost")));
                var b = doc.RootElement;
                Assert(b.GetProperty("chat_template_kwargs").GetProperty("enable_thinking").GetBoolean() == thinking &&
                    !b.TryGetProperty("reasoning_effort", out _) && !b.TryGetProperty("parse_reasoning", out _), "vLLM body mismatch");
            }
            return Task.CompletedTask;
        });
        await Test("HTTP 200 empty answer connection check succeeds", () => api.Check(Settings("checkempty"), "mock-key", default));
        await Test("legacy settings migration preserves encrypted key + runs once", () =>
        {
            var folder = Path.Combine(root, "migration"); var file = Path.Combine(folder, "settings.json");
            var encrypted = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes("migration-fake-key"), null, DataProtectionScope.CurrentUser));
            AtomicFile.Write(file, new { apiKey = encrypted, baseUrl = "https://api.friendli.ai/serverless/v1", model = "LGAI-EXAONE/K-EXAONE-2.0-750B-A37B", temperature = .7, topP = .8, maxTokens = 4096, thinking = false, systemPrompt = "keep prompt", window = new WindowPlacement(10, 20, 1200, 800, false) });
            var store = new SettingsStore(folder);
            var migrated = Json.Read<AppSettings>(File.ReadAllText(file));
            Assert(migrated.SchemaVersion == AppSettings.CurrentSchemaVersion && migrated.BaseUrl == AppSettings.DefaultBaseUrl && migrated.Model == AppSettings.DefaultModel &&
                migrated.ApiKey == encrypted && store.Key() == "migration-fake-key" && migrated.ReasoningEffort == "max" &&
                migrated.Temperature == .7 && migrated.TopP == .8 && migrated.MaxTokens == 4096 && !migrated.Thinking && migrated.SystemPrompt == "keep prompt" && migrated.Window?.X == 10, "Migration lost settings/key");
            store.Value.BaseUrl = "https://api.friendli.ai/serverless/v1"; store.Value.Model = "LGAI-EXAONE/K-EXAONE-2.0-750B-A37B"; store.Save();
            var reopened = new SettingsStore(folder);
            Assert(reopened.Value.BaseUrl == store.Value.BaseUrl && reopened.Value.Model == store.Value.Model, "Migration repeated");
            return Task.CompletedTask;
        });
        await Test("migration preserves customized URL or model", () =>
        {
            var cases = new[] {
                new AppSettings { BaseUrl = "https://api.friendli.ai/serverless/v1", Model = "custom-model" },
                new AppSettings { BaseUrl = "http://localhost:8000/v1", Model = "LGAI-EXAONE/K-EXAONE-2.0-750B-A37B" } };
            foreach (var s in cases)
            {
                var urlBefore = s.BaseUrl; var modelBefore = s.Model;
                Assert(SettingsStore.Migrate(s) && s.SchemaVersion == AppSettings.CurrentSchemaVersion && s.BaseUrl == urlBefore && s.Model == modelBefore && !SettingsStore.Migrate(s), "Custom settings changed");
            }
            return Task.CompletedTask;
        });
        await Test("reasoning effort settings roundtrip + validation", () =>
        {
            var store = new SettingsStore(Path.Combine(root, "effort"));
            Assert(store.Value.BaseUrl == AppSettings.DefaultBaseUrl && store.Value.Model == AppSettings.DefaultModel && store.Value.ReasoningEffort == "max", "Fresh defaults mismatch");
            var input = JsonSerializer.SerializeToElement(new { baseUrl = AppSettings.DefaultBaseUrl, model = AppSettings.DefaultModel, systemPrompt = "", temperature = 1, topP = .95, maxTokens = 8192, thinking = false, reasoningEffort = "high" });
            store.Save(store.Draft(input));
            Assert(new SettingsStore(Path.Combine(root, "effort")).Value.ReasoningEffort == "high" && Json.Serialize(store.Public()).Contains("\"reasoningEffort\": \"high\""), "Effort did not roundtrip");
            var invalid = new AppSettings { ReasoningEffort = "invalid" };
            try { SettingsStore.Validate(invalid); throw new Exception("Expected validation failure"); }
            catch (InvalidOperationException) { }
            return Task.CompletedTask;
        });
        var report = Path.Combine(AppContext.BaseDirectory, "tools", "selftest-results.json");
        AtomicFile.Write(report, new { passed = failures == 0, tests = results });
        Directory.Delete(root, true);
        return failures == 0 ? 0 : 1;
    }
}
