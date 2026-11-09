using System.Text;
using System.Text.Json;

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
            var body = Json.Serialize(ApiClient.Body(s, [new() { Role = "assistant", Content = "answer", Reasoning = "secret" }], true));
            using var d = JsonDocument.Parse(body);
            Assert(d.RootElement.GetProperty("parse_reasoning").GetBoolean() && !body.Contains("secret") && d.RootElement.GetProperty("messages")[0].GetProperty("role").GetString() == "system", "Friendli/history mismatch");
            Assert(!ApiClient.Body(Settings("check"), [], true).ContainsKey("parse_reasoning"), "Non-Friendli parse_reasoning leaked");
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
        var report = Path.Combine(AppContext.BaseDirectory, "tools", "selftest-results.json");
        AtomicFile.Write(report, new { passed = failures == 0, tests = results });
        Directory.Delete(root, true);
        return failures == 0 ? 0 : 1;
    }
}
