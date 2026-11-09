namespace ExaoneChat;

// Retains the longest possible tag prefix across SSE chunks.
internal sealed class ReasoningSplitter(Action<string, string> emit)
{
    private string pending = "";
    private string? closing;
    public void Push(string text)
    {
        pending += text;
        while (pending.Length > 0)
        {
            string[] tags = closing is null ? ["<think>", "<thought>"] : [closing];
            var found = tags.Select(tag => (tag, at: pending.IndexOf(tag, StringComparison.OrdinalIgnoreCase)))
                .Where(x => x.at >= 0).OrderBy(x => x.at).FirstOrDefault();
            if (found.tag is not null)
            {
                Emit(pending[..found.at]);
                pending = pending[(found.at + found.tag.Length)..];
                closing = closing is not null ? null : found.tag.Equals("<think>", StringComparison.OrdinalIgnoreCase) ? "</think>" : "</thought>";
                continue;
            }
            var keep = 0;
            foreach (var tag in tags)
                for (var n = 1; n < tag.Length && n <= pending.Length; n++)
                    if (pending.EndsWith(tag[..n], StringComparison.OrdinalIgnoreCase)) keep = Math.Max(keep, n);
            Emit(pending[..(pending.Length - keep)]);
            pending = pending[(pending.Length - keep)..];
            break;
        }
    }
    private void Emit(string value) { if (value.Length > 0) emit(closing is null ? "content" : "reasoning", value); }
    public void Finish() { Emit(pending); pending = ""; }
}
