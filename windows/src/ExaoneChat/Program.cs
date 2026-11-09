namespace ExaoneChat;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--selftest") return SelfTest.Run(args[1]).GetAwaiter().GetResult();
        ApplicationConfiguration.Initialize();
        try
        {
            var root = Path.Combine(AppContext.BaseDirectory, "data");
            using var window = new MainForm(root);
            Application.Run(window);
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show("앱을 시작할 수 없습니다.\n" + ex.Message, "EXAONE", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
