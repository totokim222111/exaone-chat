using System.Collections.Concurrent;
using System.Globalization;

namespace ExaoneChat;

internal sealed class ApiKeyEntry
{
    public string Key { get; set; } = ""; // DPAPI CurrentUser ciphertext.
    public string Label { get; set; } = "";
}
internal sealed record KeyFailover(int Index, int Status, int RetryAfterSec);
internal sealed class KeyTrace
{
    public int? KeyIndex { get; set; }
    public List<KeyFailover> KeyFailover { get; } = [];
    public void Record(int index, int seconds) { if (!KeyFailover.Any(x => x.Index == index)) KeyFailover.Add(new(index, 429, seconds)); }
}
internal static class KeyRotation
{
    private static readonly ConcurrentDictionary<string, DateTimeOffset> Cooling = new();
    internal static int Remaining(string identity) => Cooling.TryGetValue(identity, out var until) ? Math.Max(0, (int)Math.Ceiling((until - DateTimeOffset.UtcNow).TotalSeconds)) : 0;
    internal static void Pause(string identity, int seconds)
    {
        var now = DateTimeOffset.UtcNow;
        foreach (var pair in Cooling) if (pair.Value <= now) Cooling.TryRemove(pair.Key, out _);
        Cooling.AddOrUpdate(identity, now.AddSeconds(seconds), (_, previous) => previous > now.AddSeconds(seconds) ? previous : now.AddSeconds(seconds));
    }
    internal static int RetrySeconds(string? value, DateTimeOffset? now = null)
    {
        if (double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var seconds) && double.IsFinite(seconds) && seconds >= 0) return (int)Math.Min(int.MaxValue, Math.Ceiling(seconds));
        if (DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var date)) return (int)Math.Clamp(Math.Ceiling((date - (now ?? DateTimeOffset.UtcNow)).TotalSeconds), 0, int.MaxValue);
        return 60;
    }
    internal static List<(string Identity, string Key, int Index)> Keys(AppSettings s, string fallback) => s.ApiKeys.Count > 0
        ? s.ApiKeys.Select((entry, i) => (entry.Key, SettingsStore.Reveal(entry.Key), i + 1)).ToList()
        : string.IsNullOrWhiteSpace(fallback) ? [] : [("fallback:" + fallback, fallback, 1)];
}
