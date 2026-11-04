using System.Windows;
using System.Windows.Media;
using System.Windows.Shapes;

namespace HWPReview.Native;

internal enum AppIconKind
{
    Files, Folder, FolderOpen, FileText, History, CircleCheck, Plus, Ellipsis,
    ChevronRight, ChevronDown, ShieldCheck
}

// Lucide 1.66.0 originals and complete ISC/MIT notice are retained in IconAssets/lucide.
// Public only for same-assembly x:Static access from the WPF resource dictionary.
public static class AppIcons
{
    private static readonly Geometry[] Geometries =
    {
        Freeze("M15 2h-4a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V8 M16.706 2.706A2.4 2.4 0 0 0 15 2v5a1 1 0 0 0 1 1h5a2.4 2.4 0 0 0-.706-1.706z M5 7a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h8a2 2 0 0 0 1.732-1"),
        Freeze("M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"),
        Freeze("m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"),
        Freeze("M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z M14 2v5a1 1 0 0 0 1 1h5 M10 9H8 M16 13H8 M16 17H8"),
        Freeze("M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8 M3 3v5h5 M12 7v5l4 2"),
        Freeze("M2,12 A10,10 0 1 0 22,12 A10,10 0 1 0 2,12 Z M16 9l-5.5 5.5L8 12"),
        Freeze("M5 12h14 M12 5v14"),
        Freeze("M11,12 A1,1 0 1 0 13,12 A1,1 0 1 0 11,12 Z M18,12 A1,1 0 1 0 20,12 A1,1 0 1 0 18,12 Z M4,12 A1,1 0 1 0 6,12 A1,1 0 1 0 4,12 Z"),
        Freeze("m9 18 6-6-6-6"),
        Freeze("m6 9 6 6 6-6"),
        Freeze("M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z M9 12l2 2 4-4"),
    };
    private static readonly ScaleTransform SmallScale = FrozenScale(16);
    private static readonly ScaleTransform TreeScale = FrozenScale(18);

    private static ScaleTransform FrozenScale(double size)
    {
        var scale = new ScaleTransform(size / 24, size / 24);
        scale.Freeze();
        return scale;
    }

    public static Geometry ChevronRight => Geometries[(int)AppIconKind.ChevronRight];
    public static Geometry ChevronDown => Geometries[(int)AppIconKind.ChevronDown];

    private static Geometry Freeze(string data)
    {
        // Fixed, trusted path literals only; no XAML, files, SVG or network at runtime.
        var geometry = Geometry.Parse(data);
        geometry.Freeze();
        return geometry;
    }

    internal static FrameworkElement Create(AppIconKind kind, double size = 16,
        string brushKey = "MutedTextBrush")
    {
        var path = new System.Windows.Shapes.Path
        {
            Data = Geometries[(int)kind],
            Width = 24, Height = 24,
            StrokeThickness = 2,
            StrokeStartLineCap = PenLineCap.Round,
            StrokeEndLineCap = PenLineCap.Round,
            StrokeLineJoin = PenLineJoin.Round,
            Stretch = Stretch.None,
            Focusable = false,
            IsHitTestVisible = false,
            LayoutTransform = size == 16 ? SmallScale : size == 18 ? TreeScale : FrozenScale(size)
        };
        path.SetResourceReference(Shape.StrokeProperty, brushKey);
        return path;
    }
}
