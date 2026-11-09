using System.Reflection;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace ExaoneChat;

internal sealed class MainForm : Form
{
    private readonly WebView2 web = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(236, 240, 237) };
    private readonly SettingsStore settings;
    private readonly ConversationStore conversations;
    private readonly ApiClient api = new();
    private readonly string dataRoot;
    private readonly Dictionary<string, Generation> generations = [];
    private readonly CancellationTokenSource lifetime = new();
    private bool finalClose;
    private bool shuttingDown;
    private readonly Dictionary<string, byte[]> assets = new(StringComparer.OrdinalIgnoreCase);

    public MainForm(string root)
    {
        dataRoot = root;
        settings = new(root); conversations = new(root);
        Text = "EXAONE";
        Size = new(1200, 800); MinimumSize = new(760, 560);
        StartPosition = FormStartPosition.CenterScreen;
        var dark = settings.Value.Theme == "dark";
        if (settings.Value.Theme == "system")
        {
            try { dark = Microsoft.Win32.Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", 1) is int useLight && useLight == 0; }
            catch { dark = false; }
        }
        BackColor = dark ? Color.FromArgb(14, 23, 18) : Color.FromArgb(236, 240, 237);
        web.DefaultBackgroundColor = BackColor;
        using var icon = Assembly.GetExecutingAssembly().GetManifestResourceStream("app.ico");
        if (icon is not null) Icon = new Icon(icon);
        if (settings.Value.Window is { } p)
        {
            var bounds = new Rectangle(p.X, p.Y, Math.Max(760, p.Width), Math.Max(560, p.Height));
            if (Screen.AllScreens.Any(s => Rectangle.Intersect(s.WorkingArea, bounds) is { Width: >= 100, Height: >= 100 }))
            { StartPosition = FormStartPosition.Manual; Bounds = bounds; }
            if (p.Maximized) WindowState = FormWindowState.Maximized;
        }
        Controls.Add(web);
        Shown += async (_, _) => await Initialize();
        FormClosing += OnAppClosing;
        FormClosed += (_, _) => { lifetime.Cancel(); foreach (var run in generations.Values) run.Cancellation.Dispose(); lifetime.Dispose(); api.Dispose(); web.Dispose(); };
    }

    private async Task Initialize()
    {
        try
        {
            var asm = Assembly.GetExecutingAssembly();
            foreach (var name in asm.GetManifestResourceNames().Where(n => n.StartsWith("wwwroot/", StringComparison.Ordinal)))
            {
                using var resource = asm.GetManifestResourceStream(name)!;
                using var copy = new MemoryStream(); resource.CopyTo(copy);
                assets["/" + name[8..].Replace('\\', '/')] = copy.ToArray();
            }
            var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(dataRoot, "webview2"));
            await web.EnsureCoreWebView2Async(env);
            var core = web.CoreWebView2;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.IsZoomControlEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.AddWebResourceRequestedFilter("https://app.exaone/*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += (_, e) =>
            {
                var path = Uri.UnescapeDataString(new Uri(e.Request.Uri).AbsolutePath);
                if (path == "/") path = "/index.html";
                if (!assets.TryGetValue(path, out var bytes))
                { e.Response = env.CreateWebResourceResponse(new MemoryStream(), 404, "Not Found", "Content-Type: text/plain"); return; }
                var type = Path.GetExtension(path).ToLowerInvariant() switch
                { ".html" => "text/html; charset=utf-8", ".css" => "text/css; charset=utf-8", ".js" => "application/javascript; charset=utf-8", ".woff2" => "font/woff2", ".svg" => "image/svg+xml", _ => "application/octet-stream" };
                e.Response = env.CreateWebResourceResponse(new MemoryStream(bytes, false), 200, "OK", $"Content-Type: {type}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff");
            };
            core.NavigationStarting += (_, e) => { if (!e.Uri.StartsWith("https://app.exaone/", StringComparison.Ordinal)) e.Cancel = true; };
            core.NewWindowRequested += (_, e) =>
            {
                e.Handled = true;
                if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri) && uri.Scheme is "https" or "http")
                {
                    try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
                    catch (Exception ex) { MessageBox.Show(this, ex.Message, "링크를 열 수 없습니다."); }
                }
            };
            core.PermissionRequested += (_, e) => e.State = CoreWebView2PermissionState.Deny;
            core.DownloadStarting += (_, e) => e.Cancel = true;
            core.WebMessageReceived += Receive;
            await core.AddScriptToExecuteOnDocumentCreatedAsync("window.__exaInitialTheme = " + Json.Serialize(settings.Value.Theme) + ";");
            core.Navigate("https://app.exaone/");
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "화면을 열 수 없습니다. WebView2 런타임과 data 폴더 쓰기 권한을 확인하세요.\n" + ex.Message, "EXAONE", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }

    private void Post(object value)
    {
        if (!web.IsDisposed && web.CoreWebView2 is not null) web.CoreWebView2.PostWebMessageAsJson(Json.Serialize(value));
    }
    private void Reply(string reqId, object? result) => Post(new { reqId, ok = true, result });
    private void Fail(string reqId, Exception ex)
    {
        var error = ex is ApiException a ? a.ToError() : new ChatError(ex is HttpRequestException ? "네트워크 연결을 확인하세요." : ex.Message);
        Post(new { reqId, ok = false, error });
    }
    private void GuardIdle(string id)
    {
        if (generations.ContainsKey(id)) throw new InvalidOperationException("응답이 끝나거나 중지한 뒤 다시 시도하세요.");
    }
    private async void Receive(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (shuttingDown || !e.Source.StartsWith("https://app.exaone/", StringComparison.Ordinal)) return;
        var reqId = "";
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            var m = doc.RootElement;
            reqId = m.GetProperty("reqId").GetString()!;
            var type = m.GetProperty("type").GetString();
            var p = m.TryGetProperty("payload", out var payload) ? payload : default;
            switch (type)
            {
                case "saveFile":
                    var filename = Path.GetFileName(p.GetProperty("name").GetString() ?? "document.md");
                    var mime = p.GetProperty("mime").GetString();
                    var text = p.GetProperty("text").GetString() ?? "";
                    using (var dialog = new SaveFileDialog { FileName = filename, AddExtension = true, OverwritePrompt = true,
                        Filter = mime == "image/svg+xml" ? "SVG 이미지 (*.svg)|*.svg" : "Markdown 문서 (*.md)|*.md", DefaultExt = mime == "image/svg+xml" ? "svg" : "md" })
                    {
                        if (dialog.ShowDialog(this) != DialogResult.OK) { Reply(reqId, new { saved = false, cancelled = true }); break; }
                        await File.WriteAllTextAsync(dialog.FileName, text, new UTF8Encoding(false), lifetime.Token);
                        Reply(reqId, new { saved = true, cancelled = false });
                    }
                    break;
                case "settings.get": Reply(reqId, await PublicSettings()); break;
                case "theme.set":
                    var theme = p.GetProperty("theme").GetString();
                    if (theme is not ("light" or "dark" or "system")) throw new InvalidOperationException("화면 모드를 확인하세요.");
                    settings.Value.Theme = theme; settings.Save(); Reply(reqId, new { theme }); break;
                case "search.test":
                    var searchSettings = settings.Draft(p);
                    var startedSearch = System.Diagnostics.Stopwatch.StartNew();
                    using (var service = new SearchService())
                    {
                        var found = await service.Search(searchSettings, ["EXAONE"], lifetime.Token);
                        Reply(reqId, new { ok = found.Sources.Count > 0, provider = found.Provider, resultCount = found.Sources.Count,
                            latencyMs = Math.Round(startedSearch.Elapsed.TotalMilliseconds), message = found.Error ?? "웹 검색을 확인했습니다." });
                    }
                    break;
                case "settings.save":
                    var next = settings.Draft(p); settings.Save(next); Reply(reqId, await PublicSettings()); break;
                case "connection.check":
                    var draft = settings.Draft(p);
                    await api.Check(draft, SettingsStore.Decrypt(draft), lifetime.Token);
                    Reply(reqId, new { message = "연결됨" }); break;
                case "conversations.list": Reply(reqId, conversations.List()); break;
                case "conversations.get": Reply(reqId, Current(p.GetProperty("id").GetString()!)); break;
                case "conversations.rename":
                    GuardIdle(p.GetProperty("id").GetString()!); conversations.Rename(p.GetProperty("id").GetString()!, p.GetProperty("title").GetString()!); Reply(reqId, conversations.List()); break;
                case "conversations.delete":
                    var deleteId = p.GetProperty("id").GetString()!;
                    if (generations.TryGetValue(deleteId, out var deleting)) { deleting.Cancellation.Cancel(); await deleting.Completion; }
                    conversations.Delete(deleteId); Reply(reqId, conversations.List()); break;
                case "conversations.save":
                    var c = Json.Read<Conversation>(p.GetRawText());
                    GuardIdle(c.Id);
                    if (c.Messages.Any(x => x.Role is not ("user" or "assistant"))) throw new InvalidOperationException("메시지 역할이 올바르지 않습니다.");
                    conversations.Save(c); Reply(reqId, c); break;
                case "conversations.create":
                    var created = new Conversation();
                    if (p.TryGetProperty("title", out var title)) created.Title = title.GetString() ?? "새 대화";
                    SelectOptions(created, p); conversations.Save(created); Reply(reqId, created); break;
                case "chat.selection":
                    var selectionId = p.TryGetProperty("id", out var selectedId) ? selectedId.GetString() : null;
                    var selected = string.IsNullOrEmpty(selectionId) ? new Conversation() : Current(selectionId);
                    SelectOptions(selected, p);
                    if (!string.IsNullOrEmpty(selectionId)) conversations.Save(selected);
                    Reply(reqId, new { selected.ReasoningEffort, selected.Thinking, selected.WebSearch }); break;
                case "chat.stop":
                    var stopId = p.GetProperty("id").GetString()!;
                    if (generations.TryGetValue(stopId, out var stopping)) { stopping.Cancellation.Cancel(); await stopping.Completion; }
                    Reply(reqId, new { stopped = true, conversationId = stopId }); break;
                case "chat.send": await Generate(reqId, p.Clone()); break;
                default: throw new InvalidOperationException("지원하지 않는 요청입니다.");
            }
        }
        catch (Exception ex) { Fail(reqId, ex); }
    }

    private Conversation Current(string id) => generations.TryGetValue(id, out var run) ? run.Conversation : conversations.Get(id);
    private async Task<object> PublicSettings()
    {
        using var service = new SearchService();
        return settings.Public(serverReady: await service.ServerReady(settings.Value.SearxngUrl, lifetime.Token));
    }

    private AppSettings SelectOptions(Conversation c, JsonElement p)
    {
        var effort = p.TryGetProperty("reasoningEffort", out var e) ? e.GetString() : null;
        bool? thinking = p.TryGetProperty("thinking", out var t) ? t.GetBoolean() : null;
        bool? webSearch = p.TryGetProperty("webSearch", out var web) ? web.GetBoolean() : null;
        var snapshot = ConversationOptions.Apply(c, settings.Value, effort, thinking, webSearch);
        settings.Save(); return snapshot;
    }
    private async Task Generate(string reqId, JsonElement p)
    {
        var id = p.TryGetProperty("id", out var item) ? item.GetString() : null;
        var c = string.IsNullOrEmpty(id) ? new Conversation() : Current(id);
        GuardIdle(c.Id);
        if (generations.Count >= 8) throw new InvalidOperationException("동시에 8개까지 답변을 받을 수 있습니다.");
        var content = p.GetProperty("content").GetString()?.Trim() ?? "";
        var retry = p.TryGetProperty("retry", out var r) && r.GetBoolean();
        int? editIndex = p.TryGetProperty("editIndex", out var edit) && edit.ValueKind == JsonValueKind.Number ? edit.GetInt32() : null;
        var snapshot = SelectOptions(c, p);
        ChatHistory.Prepare(c, content, retry, editIndex);
        var run = new Generation(c, snapshot, CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token));
        generations.Add(c.Id, run);
        run.SearchEvent += data => Post(new { reqId, @event = "search", conversationId = c.Id, data });
        try
        {
            run.Completion = run.Run(api, conversations, (kind, text, error) =>
            {
                if (kind == "start") Post(new { reqId, @event = kind, conversationId = c.Id, conversation = c });
                else if (kind == "done") Post(new { reqId, @event = kind, conversationId = c.Id, ok = true,
                    result = new { conversation = c, stopped = run.Stopped, error = run.Error } });
                else Post(new { reqId, @event = kind, conversationId = c.Id, text, error });
            });
            await run.Completion;
        }
        finally { generations.Remove(c.Id); run.Cancellation.Dispose(); }
    }

    private async void OnAppClosing(object? sender, FormClosingEventArgs e)
    {
        if (finalClose) return;
        e.Cancel = true;
        if (shuttingDown) return;
        shuttingDown = true;
        var pendingRuns = generations.Values.ToArray();
        foreach (var run in pendingRuns) run.Cancellation.Cancel();
        lifetime.Cancel();
        await Task.WhenAll(pendingRuns.Select(run => run.Completion));
        try
        {
            var b = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
            settings.Value.Window = new(b.X, b.Y, b.Width, b.Height, WindowState == FormWindowState.Maximized);
            settings.Save();
        }
        catch (Exception ex) { MessageBox.Show(this, "창 위치를 저장할 수 없습니다.\n" + ex.Message, "EXAONE"); }
        finalClose = true; Close();
    }
}

internal static class TextElements
{
    public static IEnumerable<string> AsEnumerable(this System.Globalization.TextElementEnumerator e) { while (e.MoveNext()) yield return e.GetTextElement(); }
}
