using System.Runtime.InteropServices;
using System.Windows;

namespace HWPReview.Native;

internal static class Program
{
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetProcessDpiAwarenessContext(nint context);

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Length != 2 || args[0] != "--owned-host" || !OwnedHostClient.ValidNonce(args[1]))
        {
            MessageBox.Show("이 창은 HWPReview의 소유 백엔드에서만 열 수 있습니다. HWPReview 실행 파일로 시작하세요.", "HWPX 보고서 작성", MessageBoxButton.OK, MessageBoxImage.Warning);
            return 1;
        }
        SetProcessDpiAwarenessContext(new nint(-4));
        var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
        app.Resources.MergedDictionaries.Add(new ResourceDictionary
        {
            Source = new Uri("pack://application:,,,/HWPReview.Native;component/Styles.xaml", UriKind.Absolute)
        });
        using var host = new OwnedHostClient();
        var window = new MainWindow(host, args[1]);
        return app.Run(window);
    }
}
