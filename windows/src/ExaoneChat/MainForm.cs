using System.Reflection;
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
    private CancellationTokenSource? generation;
    private readonly CancellationTokenSource lifetime = new();
    private Task? activeTask;
    private Conversation? activeConversation;
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
        BackColor = Color.FromArgb(236, 240, 237);
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
        FormClosed += (_, _) => { lifetime.Cancel(); generation?.Dispose(); lifetime.Dispose(); api.Dispose(); web.Dispose(); };
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
                { ".html" => "text/html; charset=utf-8", ".css" => "text/css; charset=utf-8", ".js" => "application/javascript; charset=utf-8", _ => "application/octet-stream" };
                e.Response = env.CreateWebResourceResponse(new MemoryStream(bytes, false), 200, "OK", $"Content-Type: {type}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff");
            };
            core.NavigationStarting += (_, e) => { if (!e.Uri.StartsWith("https://app.exaone/", StringComparison.Ordinal)) e.Cancel = true; };
            core.NewWindowRequested += (_, e) => e.Handled = true;
            core.PermissionRequested += (_, e) => e.State = CoreWebView2PermissionState.Deny;
            core.DownloadStarting += (_, e) => e.Cancel = true;
            core.WebMessageReceived += Receive;
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
    private void GuardIdle()
    {
        if (generation is not null) throw new InvalidOperationException("응답이 끝나거나 중지한 뒤 다시 시도하세요.");
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
                case "settings.get": Reply(reqId, settings.Public()); break;
                case "settings.save":
                    var next = settings.Draft(p); settings.Save(next); Reply(reqId, settings.Public()); break;
                case "connection.check":
                    var draft = settings.Draft(p);
                    await api.Check(draft, SettingsStore.Decrypt(draft), lifetime.Token);
                    Reply(reqId, new { message = "연결됨" }); break;
                case "conversations.list": Reply(reqId, conversations.List()); break;
                case "conversations.get": Reply(reqId, activeConversation?.Id == p.GetProperty("id").GetString() ? activeConversation : conversations.Get(p.GetProperty("id").GetString()!)); break;
                case "conversations.rename":
                    GuardIdle(); conversations.Rename(p.GetProperty("id").GetString()!, p.GetProperty("title").GetString()!); Reply(reqId, conversations.List()); break;
                case "conversations.delete":
                    GuardIdle(); conversations.Delete(p.GetProperty("id").GetString()!); Reply(reqId, conversations.List()); break;
                case "conversations.save":
                    GuardIdle();
                    var c = Json.Read<Conversation>(p.GetRawText());
                    if (c.Messages.Any(x => x.Role is not ("user" or "assistant"))) throw new InvalidOperationException("메시지 역할이 올바르지 않습니다.");
                    conversations.Save(c); Reply(reqId, c); break;
                case "chat.stop": generation?.Cancel(); Reply(reqId, new { stopped = true }); break;
                case "chat.send":
                    GuardIdle();
                    generation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
                    // Clone before this handler's JsonDocument is disposed.
                    var sendPayload = p.Clone();
                    activeTask = Generate(reqId, sendPayload, generation.Token);
                    await activeTask; break;
                default: throw new InvalidOperationException("지원하지 않는 요청입니다.");
            }
        }
        catch (Exception ex) { Fail(reqId, ex); }
    }

    private async Task Generate(string reqId, JsonElement p, CancellationToken ct)
    {
        Conversation? c = null;
        ChatMessage? answer = null;
        var stopped = false;
        ChatError? failure = null;
        try
        {
            var s = Json.Read<AppSettings>(Json.Serialize(settings.Value));
            var text = p.GetProperty("content").GetString()?.Trim() ?? "";
            var retry = p.TryGetProperty("retry", out var r) && r.GetBoolean();
            var id = p.TryGetProperty("id", out var i) ? i.GetString() : null;
            c = string.IsNullOrEmpty(id) ? new() : conversations.Get(id);
            if (retry)
            {
                if (c.Messages.LastOrDefault()?.Role == "assistant") c.Messages.RemoveAt(c.Messages.Count - 1);
                if (c.Messages.LastOrDefault()?.Role != "user") throw new InvalidOperationException("다시 시도할 메시지가 없습니다.");
            }
            else
            {
                if (text.Length == 0) throw new InvalidOperationException("메시지를 입력하세요.");
                if (c.Messages.Count == 0) c.Title = string.Concat(System.Globalization.StringInfo.GetTextElementEnumerator(text).AsEnumerable().Take(30));
                c.Messages.Add(new() { Role = "user", Content = text });
            }
            c.Model = s.Model;
            var history = c.Messages.ToList();
            answer = new() { Role = "assistant", Reasoning = "" };
            c.Messages.Add(answer);
            activeConversation = c;
            conversations.Save(c);
            Post(new { reqId, @event = "start", conversation = c });
            var lastSave = DateTime.UtcNow;
            await api.Stream(s, SettingsStore.Decrypt(s), history, (kind, value) =>
            {
                if (kind == "reasoning") answer.Reasoning += value; else answer.Content += value;
                Post(new { reqId, @event = kind, text = value, conversationId = c.Id });
                if (DateTime.UtcNow - lastSave > TimeSpan.FromSeconds(1)) { conversations.Save(c); lastSave = DateTime.UtcNow; }
            }, ct);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { stopped = true; }
        catch (Exception ex)
        {
            failure = ex is ApiException a ? a.ToError() : new(ex.Message);
            if (answer is not null) answer.Error = failure;
            Post(new { reqId, @event = "error", error = failure, conversationId = c?.Id });
        }
        finally
        {
            try { if (c is not null) conversations.Save(c); }
            catch (Exception ex) { failure = new("대화를 저장할 수 없습니다. " + ex.Message); Post(new { reqId, @event = "error", error = failure }); }
            generation?.Dispose(); generation = null; activeConversation = null;
            Post(new { reqId, @event = "done", ok = true, result = new { conversation = c, stopped, error = failure } });
        }
    }

    private async void OnAppClosing(object? sender, FormClosingEventArgs e)
    {
        if (finalClose) return;
        e.Cancel = true;
        if (shuttingDown) return;
        shuttingDown = true;
        generation?.Cancel(); lifetime.Cancel();
        if (activeTask is not null) await activeTask;
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
