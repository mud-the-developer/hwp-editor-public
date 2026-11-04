// Opt-in synthetic fixture driver. Windows PowerShell/.NET Framework only.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Automation;
using System.Xml;

namespace HwpNativeProof
{
    public sealed class PendingAction
    {
        private readonly ManualResetEvent done = new ManualResetEvent(false);
        private Exception error;
        private object result;
        internal PendingAction(Func<object> operation)
        {
            Thread thread = new Thread(delegate()
            {
                try { result = operation(); }
                catch (Exception exception) { error = exception; }
                finally { done.Set(); }
            });
            thread.IsBackground = true;
            thread.SetApartmentState(ApartmentState.MTA);
            thread.Start();
        }
        public bool IsComplete { get { return done.WaitOne(0); } }
        public object Finish(int milliseconds)
        {
            if (!done.WaitOne(milliseconds))
                throw new TimeoutException("Native action observation timed out; action was not replayed or cancelled.");
            if (error != null) throw new InvalidOperationException("Native action failed; action was not replayed.", error);
            return result;
        }
    }

    public sealed class CaptureResult
    {
        public string Path;
        public bool PrintWindowSucceeded;
        public int Width;
        public int Height;
        public double DarkSampleFraction;
        public string VisualAssessment;
    }

    public sealed class MutationResult
    {
        public string Entry;
        public string BeforeSha256;
        public string AfterSha256;
        public int PreservedEntries;
    }

    public sealed class Workspace
    {
        private readonly int processId;
        private readonly IntPtr main;
        private const uint GW_OWNER = 4;
        private const uint PW_RENDERFULLCONTENT = 2;
        private static readonly string[] ToolsActionIds = { "RefreshState", "SetupRequirements", "LegalNotice", "LegacyTools" };
        private static readonly string[] CurrentToolsActionIds = { "OpenReport", "RefreshState", "SetupRequirements", "LegalNotice", "LegacyTools" };
        private delegate bool EnumWindowProc(IntPtr window, IntPtr parameter);
        [StructLayout(LayoutKind.Sequential)]
        private struct Rect { public int Left, Top, Right, Bottom; }
        [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowProc callback, IntPtr parameter);
        [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
        [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr window, uint command);
        [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr window, uint flags);
        [DllImport("user32.dll")] private static extern bool IsWindow(IntPtr window);
        [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr window, out Rect rectangle);
        [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr window);
        [DllImport("user32.dll")] private static extern bool PrintWindow(IntPtr window, IntPtr hdc, uint flags);
        [DllImport("user32.dll")] private static extern IntPtr GetDlgItem(IntPtr dialog, int id);
        [DllImport("user32.dll")] private static extern int GetDlgCtrlID(IntPtr control);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr control, StringBuilder name, int maximum);
        [DllImport("user32.dll", SetLastError = true)] private static extern IntPtr SendMessageTimeout(
            IntPtr control, uint message, UIntPtr wparam, IntPtr lparam, uint flags, uint milliseconds, out UIntPtr result);
        [StructLayout(LayoutKind.Explicit, Size = 40)]
        private struct NativeInput
        {
            [FieldOffset(0)] public uint Type;
            [FieldOffset(8)] public ushort VirtualKey;
            [FieldOffset(10)] public ushort Scan;
            [FieldOffset(12)] public uint Flags;
            [FieldOffset(16)] public uint Time;
            [FieldOffset(24)] public UIntPtr Extra;
        }
        [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
        [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
        [DllImport("user32.dll", SetLastError = true)] private static extern uint SendInput(uint count, NativeInput[] inputs, int size);

        public Workspace(int ownedProcessId)
        {
            if (ownedProcessId <= 0) throw new ArgumentException("An explicit owned child PID is required.");
            processId = ownedProcessId;
            List<IntPtr> matches = new List<IntPtr>();
            EnumWindows(delegate(IntPtr window, IntPtr unused)
            {
                uint pid;
                GetWindowThreadProcessId(window, out pid);
                if (pid == processId && IsWindowVisible(window))
                {
                    AutomationElement element = AutomationElement.FromHandle(window);
                    if (element.Current.ProcessId == processId && element.Current.AutomationId == "HwpNativeWorkspace")
                        matches.Add(window);
                }
                return true;
            }, IntPtr.Zero);
            if (matches.Count != 1) throw new InvalidOperationException("Expected exactly one owned HwpNativeWorkspace window.");
            main = matches[0];
            VerifyWindow(main);
        }

        public long MainHandle { get { return main.ToInt64(); } }
        public bool MainExists
        {
            get
            {
                if (!IsWindow(main)) return false;
                VerifyWindow(main);
                return true;
            }
        }
        private void VerifyWindow(IntPtr window)
        {
            uint pid;
            if (!IsWindow(window)) throw new InvalidOperationException("Owned window no longer exists.");
            GetWindowThreadProcessId(window, out pid);
            if (pid != processId) throw new InvalidOperationException("Window PID is not the receipt child PID.");
            IntPtr owner = window;
            for (int depth = 0; depth < 16; depth++)
            {
                if (owner == main) return;
                owner = GetWindow(owner, GW_OWNER);
                if (owner == IntPtr.Zero) break;
                GetWindowThreadProcessId(owner, out pid);
                if (pid != processId) break;
            }
            throw new InvalidOperationException("Window is not the exact workspace or its owned modal window.");
        }
        public void Verify(AutomationElement element)
        {
            VerifyWindow(main);
            if (element == null || element.Current.ProcessId != processId)
                throw new InvalidOperationException("Control PID is not the receipt child PID.");
            AutomationElement parent = element;
            for (int depth = 0; parent != null && depth < 100; depth++)
            {
                int handle = parent.Current.NativeWindowHandle;
                if (handle != 0)
                {
                    VerifyWindow(GetAncestor(new IntPtr(handle), 2));
                    return;
                }
                parent = TreeWalker.RawViewWalker.GetParent(parent);
            }
            throw new InvalidOperationException("Control cannot be bound to an owned HWND.");
        }
        public AutomationElement Root()
        {
            VerifyWindow(main);
            AutomationElement root = AutomationElement.FromHandle(main);
            Verify(root);
            if (root.Current.AutomationId != "HwpNativeWorkspace")
                throw new InvalidOperationException("Workspace identity changed.");
            return root;
        }
        public AutomationElement Find(string id)
        {
            AutomationElement value = Root().FindFirst(TreeScope.Descendants,
                new AndCondition(new PropertyCondition(AutomationElement.ProcessIdProperty, processId),
                    new PropertyCondition(AutomationElement.AutomationIdProperty, id)));
            if (value != null) Verify(value);
            return value;
        }
        public AutomationElement Require(string id)
        {
            AutomationElement result = Find(id);
            if (result == null) throw new InvalidOperationException("Missing native control: " + id);
            return result;
        }

        // Current workspace only. Legacy Pi-SDK tab/group methods below retain
        // their original behavior for immutable-candidate proof scripts.
        public AutomationElement RequireRole(string id, ControlType role)
        {
            AutomationElement element = Require(id);
            if (element.Current.ControlType != role)
                throw new InvalidOperationException("Unexpected native control role: " + id);
            return element;
        }
        public bool Visible(string id)
        {
            AutomationElement element = Find(id);
            return element != null && !element.Current.IsOffscreen && !element.Current.BoundingRectangle.IsEmpty;
        }
        public double DpiScale()
        {
            VerifyWindow(main);
            uint dpi = GetDpiForWindow(main);
            if (dpi == 0) throw new InvalidOperationException("Owned window DPI is unavailable.");
            return dpi / 96.0;
        }
        public AutomationElement[] VisibleWorkspaceActions()
        {
            AutomationElementCollection all = Root().FindAll(TreeScope.Descendants,
                new AndCondition(new PropertyCondition(AutomationElement.ProcessIdProperty, processId),
                    new OrCondition(new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button),
                        new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.MenuItem))));
            List<AutomationElement> actions = new List<AutomationElement>();
            foreach (AutomationElement element in all)
            {
                Verify(element);
                // Scrollbar/embedded native chrome without app IDs is not an app action.
                if (!String.IsNullOrEmpty(element.Current.AutomationId) &&
                    !element.Current.IsOffscreen && !element.Current.BoundingRectangle.IsEmpty)
                    actions.Add(element);
            }
            return actions.ToArray();
        }
        public AutomationElement TreeItem(string id)
        {
            if (id != "OriginalDocument" && !id.StartsWith("LibraryOpen_", StringComparison.Ordinal) &&
                !id.StartsWith("Version_", StringComparison.Ordinal) &&
                !id.StartsWith("WorkspaceDocument_", StringComparison.Ordinal) &&
                !id.StartsWith("WorkspaceFolder_", StringComparison.Ordinal))
                throw new InvalidOperationException("Selection is outside the fixed document tree.");
            AutomationElement tree = RequireRole("DocumentTree", ControlType.Tree);
            AutomationElementCollection matches = tree.FindAll(TreeScope.Descendants,
                new AndCondition(new PropertyCondition(AutomationElement.ProcessIdProperty, processId),
                    new PropertyCondition(AutomationElement.AutomationIdProperty, id),
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.TreeItem)));
            if (matches.Count != 1) throw new InvalidOperationException("Expected one exact document tree item: " + id);
            Verify(matches[0]);
            return matches[0];
        }
        public bool TreeSelected(string id)
        {
            AutomationElement item = TreeItem(id);
            return ((SelectionItemPattern)item.GetCurrentPattern(SelectionItemPattern.Pattern)).Current.IsSelected;
        }
        public PendingAction SelectDocument(string id)
        {
            AutomationElement item = TreeItem(id);
            if (!item.Current.IsEnabled) throw new InvalidOperationException("Document tree selection is disabled.");
            return new PendingAction(delegate()
            {
                Verify(item);
                SelectionItemPattern pattern = (SelectionItemPattern)item.GetCurrentPattern(SelectionItemPattern.Pattern);
                // Selection is a single gesture, not Invoke or a retry of intake.
                if (!pattern.Current.IsSelected) pattern.Select();
                return null;
            });
        }
        public void ExpandTreeNode(string id)
        {
            if (!id.StartsWith("LibraryOpen_", StringComparison.Ordinal) &&
                !id.StartsWith("WorkspaceFolder_", StringComparison.Ordinal) &&
                !id.StartsWith("WorkspaceDocument_", StringComparison.Ordinal))
                throw new InvalidOperationException("Only a fixed document/workspace tree branch can expand.");
            AutomationElement item = TreeItem(id);
            ExpandCollapsePattern pattern = (ExpandCollapsePattern)item.GetCurrentPattern(ExpandCollapsePattern.Pattern);
            if (pattern.Current.ExpandCollapseState == ExpandCollapseState.Collapsed) pattern.Expand();
            else if (pattern.Current.ExpandCollapseState != ExpandCollapseState.Expanded)
                throw new InvalidOperationException("Document project expansion is uncertain.");
        }
        public bool InspectorVisible() { return Visible("InspectorTabs"); }
        private void WaitVisible(string id, bool visible)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(10);
            do
            {
                VerifyWindow(main);
                if (Visible(id) == visible) return;
                Thread.Sleep(80);
            } while (DateTime.UtcNow < deadline);
            throw new TimeoutException("Native visibility did not settle: " + id + "; no replay.");
        }
        public void OpenInspector(int index)
        {
            if (index < 0 || index > 2) throw new ArgumentOutOfRangeException("index");
            if (!InspectorVisible())
            {
                AutomationElement button = RequireRole("ContextOptions", ControlType.Button);
                if (!button.Current.IsEnabled) throw new InvalidOperationException("Context inspector is disabled.");
                InvokeObserved(button).Finish(15000);
                WaitVisible("InspectorTabs", true);
            }
            AutomationElement tabs = RequireRole("InspectorTabs", ControlType.Tab);
            AutomationElementCollection items = tabs.FindAll(TreeScope.Children,
                new AndCondition(new PropertyCondition(AutomationElement.ProcessIdProperty, processId),
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.TabItem)));
            if (items.Count != 3) throw new InvalidOperationException("Expected three explicit inspector tabs.");
            AutomationElement item = items[index];
            Verify(item);
            SelectionItemPattern selection = (SelectionItemPattern)item.GetCurrentPattern(SelectionItemPattern.Pattern);
            if (!selection.Current.IsSelected) selection.Select();
            if (!selection.Current.IsSelected) throw new InvalidOperationException("Inspector tab selection was not observed.");
        }
        public void CloseInspector()
        {
            if (!InspectorVisible()) return;
            AutomationElement button = RequireRole("CloseContextOptions", ControlType.Button);
            if (!button.Current.IsEnabled) throw new InvalidOperationException("Context inspector close is disabled.");
            InvokeObserved(button).Finish(15000);
            WaitVisible("InspectorTabs", false);
        }
        public string ReadInspectorText(string id)
        {
            if (id != "ReportTechnicalState" && id != "OmpProposalText")
                throw new InvalidOperationException("Only public readonly technical displays may be inspected.");
            if (InspectorVisible()) throw new InvalidOperationException("Close explicit context choices before temporary technical inspection.");
            OpenInspector(2);
            try
            {
                AutomationElement element = RequireRole(id, ControlType.Edit);
                ValuePattern pattern = (ValuePattern)element.GetCurrentPattern(ValuePattern.Pattern);
                if (!pattern.Current.IsReadOnly || element.Current.IsOffscreen)
                    throw new InvalidOperationException("Public inspector display is not visible and readonly: " + id);
                return pattern.Current.Value;
            }
            finally { CloseInspector(); }
        }
        public void SetMenuExpanded(string id, bool expanded)
        {
            if (id != "DocumentActionsButton" && id != "ToolsMenuButton")
                throw new InvalidOperationException("Unknown current workspace menu.");
            AutomationElement menu = RequireRole(id, ControlType.MenuItem);
            if (!menu.Current.IsEnabled || menu.Current.IsOffscreen || menu.Current.BoundingRectangle.IsEmpty)
                throw new InvalidOperationException("Owned menu is not visibly enabled: " + id);
            ExpandCollapsePattern pattern = (ExpandCollapsePattern)menu.GetCurrentPattern(ExpandCollapsePattern.Pattern);
            ExpandCollapseState state = pattern.Current.ExpandCollapseState;
            // UIA expansion does not require physical keyboard or foreground focus.
            if (expanded && state == ExpandCollapseState.Collapsed) pattern.Expand();
            else if (!expanded && state == ExpandCollapseState.Expanded) pattern.Collapse();
            else if (state != ExpandCollapseState.Collapsed && state != ExpandCollapseState.Expanded)
                throw new InvalidOperationException("Owned menu expansion is uncertain; no fallback.");
        }
        public AutomationElement MenuAction(string header, string id)
        {
            AutomationElement menu = RequireRole(header, ControlType.MenuItem);
            if (((ExpandCollapsePattern)menu.GetCurrentPattern(ExpandCollapsePattern.Pattern)).Current.ExpandCollapseState != ExpandCollapseState.Expanded)
                throw new InvalidOperationException("Expand the exact owned menu before selecting its action.");
            AutomationElementCollection matches = menu.FindAll(TreeScope.Descendants,
                new AndCondition(new PropertyCondition(AutomationElement.ProcessIdProperty, processId),
                    new PropertyCondition(AutomationElement.AutomationIdProperty, id),
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.MenuItem)));
            if (matches.Count != 1) throw new InvalidOperationException("Expected one exact owned menu action: " + id);
            Verify(matches[0]);
            if (matches[0].Current.IsOffscreen || matches[0].Current.BoundingRectangle.IsEmpty)
                throw new InvalidOperationException("Owned menu action is not visible: " + id);
            return matches[0];
        }
        public AutomationElement ToolsMenu()
        {
            AutomationElement menu = Require("ToolsMenuButton");
            if (menu.Current.ControlType != ControlType.MenuItem)
                throw new InvalidOperationException("Owned tools header is not a standard menu item.");
            ExpandCollapsePattern pattern = (ExpandCollapsePattern)menu.GetCurrentPattern(ExpandCollapsePattern.Pattern);
            return pattern.Current.ExpandCollapseState == ExpandCollapseState.Expanded ? menu : null;
        }
        public void SetToolsMenuExpanded(bool expanded)
        {
            AutomationElement menu = Require("ToolsMenuButton");
            Verify(menu);
            ExpandCollapsePattern pattern = (ExpandCollapsePattern)menu.GetCurrentPattern(ExpandCollapsePattern.Pattern);
            ExpandCollapseState state = pattern.Current.ExpandCollapseState;
            if (expanded && state == ExpandCollapseState.Collapsed)
            { menu.SetFocus(); Verify(menu); pattern.Expand(); }
            else if (!expanded && state == ExpandCollapseState.Expanded) pattern.Collapse();
            else if (state != ExpandCollapseState.Collapsed && state != ExpandCollapseState.Expanded)
                throw new InvalidOperationException("Owned tools expansion state is uncertain; no fallback.");
        }
        private IntPtr ToolsMenuWindow(AutomationElement menu)
        {
            Verify(menu);
            if (menu.Current.AutomationId != "ToolsMenuButton" || menu.Current.ControlType != ControlType.MenuItem ||
                menu.Current.IsOffscreen || !menu.Equals(ToolsMenu()))
                throw new InvalidOperationException("Only the exact currently expanded owned tools menu is permitted.");
            System.Windows.Rect bounds = System.Windows.Rect.Empty;
            bool currentWorkspace = Find("DocumentTree") != null;
            foreach (string id in currentWorkspace ? CurrentToolsActionIds : ToolsActionIds)
            {
                AutomationElement row = currentWorkspace ? MenuAction("ToolsMenuButton", id) :
                    menu.FindFirst(TreeScope.Descendants,
                        new PropertyCondition(AutomationElement.AutomationIdProperty, id));
                Verify(row);
                if (row.Current.IsOffscreen || row.Current.BoundingRectangle.IsEmpty)
                    throw new InvalidOperationException("A tools row is not visibly bound to the expanded menu.");
                bounds.Union(row.Current.BoundingRectangle);
            }
            IntPtr found = IntPtr.Zero;
            int matches = 0;
            EnumWindows(delegate(IntPtr window, IntPtr unused)
            {
                uint pid; Rect rectangle;
                GetWindowThreadProcessId(window, out pid);
                if (window == main || pid != processId || !IsWindowVisible(window) || !GetWindowRect(window, out rectangle) ||
                    rectangle.Left > bounds.Left || rectangle.Top > bounds.Top ||
                    rectangle.Right < bounds.Right || rectangle.Bottom < bounds.Bottom)
                    return true;
                double chrome = 32.0 * Math.Max(1.0, GetDpiForWindow(window) / 96.0);
                if (bounds.Left - rectangle.Left > chrome || bounds.Top - rectangle.Top > chrome ||
                    rectangle.Right - bounds.Right > chrome || rectangle.Bottom - bounds.Bottom > chrome)
                    return true;
                VerifyWindow(window);
                found = window; matches++;
                return true;
            }, IntPtr.Zero);
            if (matches != 1)
                throw new InvalidOperationException("Expected one exact owned popup HWND tightly enclosing every expanded menu row.");
            return found;
        }
        public void ToolsMenuKey(AutomationElement item, int key)
        {
            Verify(item);
            string id = item.Current.AutomationId;
            if (id != "RefreshState" && id != "SetupRequirements" && id != "LegalNotice" && id != "LegacyTools")
                throw new InvalidOperationException("Key target is outside the fixed tools menu.");
            if (key != 0x26 && key != 0x28 && key != 0x1B && !(key == 0x0D && id == "RefreshState"))
                throw new InvalidOperationException("Only menu navigation, Escape or read-only Refresh Enter is permitted.");
            AutomationElement menu = ToolsMenu();
            if (menu == null || !item.Equals(menu.FindFirst(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.AutomationIdProperty, id))))
                throw new InvalidOperationException("Key target is not in the exact open tools popup.");
            IntPtr handle = ToolsMenuWindow(menu);
            item.SetFocus(); VerifyWindow(handle);
            UIntPtr result;
            if (SendMessageTimeout(handle, 0x0100, new UIntPtr((uint)key), new IntPtr(1), 2, 3000, out result) == IntPtr.Zero)
                throw new InvalidOperationException("Owned routed menu key failed; no replay: " + Marshal.GetLastWin32Error());
        }
        public AutomationElement[] Prefix(string prefix)
        {
            AutomationElementCollection all = Root().FindAll(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.ProcessIdProperty, processId));
            List<AutomationElement> found = new List<AutomationElement>();
            foreach (AutomationElement element in all)
            {
                if (element.Current.AutomationId.StartsWith(prefix, StringComparison.Ordinal))
                { Verify(element); found.Add(element); }
            }
            return found.ToArray();
        }
        public string Id(AutomationElement element) { Verify(element); return element.Current.AutomationId; }
        public string Name(AutomationElement element) { Verify(element); return element.Current.Name; }
        public bool Enabled(AutomationElement element) { Verify(element); return element.Current.IsEnabled; }
        public string Help(AutomationElement element) { Verify(element); return element.Current.HelpText; }
        public string Read(AutomationElement element)
        {
            Verify(element);
            object pattern;
            if (element.TryGetCurrentPattern(ValuePattern.Pattern, out pattern))
                return ((ValuePattern)pattern).Current.Value;
            return element.Current.Name;
        }
        public int ToggleState(AutomationElement element)
        { Verify(element); return (int)((TogglePattern)element.GetCurrentPattern(TogglePattern.Pattern)).Current.ToggleState; }
        public void Toggle(AutomationElement element)
        { Verify(element); ((TogglePattern)element.GetCurrentPattern(TogglePattern.Pattern)).Toggle(); }
        public void SetValue(AutomationElement element, string value)
        {
            Verify(element);
            ValuePattern pattern = (ValuePattern)element.GetCurrentPattern(ValuePattern.Pattern);
            if (pattern.Current.IsReadOnly) throw new InvalidOperationException("Target is read-only.");
            pattern.SetValue(value);
            if (pattern.Current.Value != value) throw new InvalidOperationException("Native editor did not observe the requested value; no confirmation was sent.");
        }
        public PendingAction Invoke(AutomationElement element)
        {
            Verify(element);
            return new PendingAction(delegate()
            {
                Verify(element);
                ((InvokePattern)element.GetCurrentPattern(InvokePattern.Pattern)).Invoke();
                return null;
            });
        }
        public PendingAction InvokeObserved(AutomationElement element)
        {
            Verify(element);
            return new PendingAction(delegate()
            {
                using (ManualResetEvent invoked = new ManualResetEvent(false))
                {
                    AutomationEventHandler handler = delegate(object sender, AutomationEventArgs args) { invoked.Set(); };
                    Verify(element);
                    Automation.AddAutomationEventHandler(InvokePattern.InvokedEvent, element, TreeScope.Element, handler);
                    try
                    {
                        Verify(element);
                        ((InvokePattern)element.GetCurrentPattern(InvokePattern.Pattern)).Invoke();
                        if (!invoked.WaitOne(10000))
                            throw new TimeoutException("Native WPF Invoke event was not observed; no action replay.");
                        // The subsequent property observation crosses the UI dispatcher after OnClick dispatch.
                        Root();
                    }
                    finally { Automation.RemoveAutomationEventHandler(InvokePattern.InvokedEvent, element, handler); }
                }
                return null;
            });
        }
        public bool AttemptDisabledInvoke(AutomationElement element)
        {
            Verify(element);
            if (element.Current.IsEnabled)
                throw new InvalidOperationException("Duplicate gesture target unexpectedly enabled; no duplicate action sent.");
            try
            {
                Verify(element);
                ((InvokePattern)element.GetCurrentPattern(InvokePattern.Pattern)).Invoke();
            }
            catch (ElementNotEnabledException) { return true; }
            throw new InvalidOperationException("Disabled Invoke did not reject the duplicate gesture.");
        }
        public PendingAction Close()
        {
            AutomationElement root = Root();
            return new PendingAction(delegate()
            {
                Verify(root);
                // WM_CLOSE is the normal caption-close path; WindowPattern.Close
                // may return without delivering it on a non-input desktop.
                UIntPtr result;
                if (SendMessageTimeout(main, 0x0010, UIntPtr.Zero, IntPtr.Zero, 2, 10000, out result) == IntPtr.Zero)
                    throw new TimeoutException("Owned WM_CLOSE delivery was not observed; no replay or termination.");
                return null;
            });
        }
        public void SelectTab(int index)
        {
            AutomationElement tabs = Require("WorkspaceTabs");
            AutomationElementCollection items = tabs.FindAll(TreeScope.Children,
                new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.TabItem));
            if (index < 0 || index >= items.Count) throw new InvalidOperationException("Native workspace tab is absent.");
            AutomationElement item = items[index];
            Verify(item);
            SelectionItemPattern pattern = (SelectionItemPattern)item.GetCurrentPattern(SelectionItemPattern.Pattern);
            if (!pattern.Current.IsSelected) { Verify(item); pattern.Select(); }
        }
        public int BaseIndex()
        {
            AutomationElement combo = Require("RequestBase");
            object value = combo.GetCurrentPattern(SelectionPattern.Pattern);
            AutomationElement[] selected = ((SelectionPattern)value).Current.GetSelection();
            if (selected.Length != 1) throw new InvalidOperationException("Request base selection is ambiguous.");
            AutomationElementCollection items = combo.FindAll(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.ListItem));
            for (int index = 0; index < items.Count; index++) if (items[index].Equals(selected[0])) return index;
            // Collapsed ComboBox often exposes only the selected child; do not guess its index from translated labels.
            throw new InvalidOperationException("Expand RequestBase before observing its selected index.");
        }
        public void ExpandBase()
        {
            AutomationElement combo = Require("RequestBase");
            Verify(combo);
            ExpandCollapsePattern pattern = (ExpandCollapsePattern)combo.GetCurrentPattern(ExpandCollapsePattern.Pattern);
            if (pattern.Current.ExpandCollapseState == ExpandCollapseState.Collapsed) { Verify(combo); pattern.Expand(); }
        }
        public void SelectBase(int index)
        {
            ExpandBase();
            AutomationElement combo = Require("RequestBase");
            AutomationElementCollection items = combo.FindAll(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.ListItem));
            if (items.Count != 2 || index < 0 || index > 1) throw new InvalidOperationException("Expected two native request bases.");
            Verify(items[index]);
            ((SelectionItemPattern)items[index].GetCurrentPattern(SelectionItemPattern.Pattern)).Select();
            CollapseBase();
        }
        public void CollapseBase()
        {
            AutomationElement combo = Require("RequestBase");
            Verify(combo);
            ExpandCollapsePattern pattern = (ExpandCollapsePattern)combo.GetCurrentPattern(ExpandCollapsePattern.Pattern);
            if (pattern.Current.ExpandCollapseState == ExpandCollapseState.Expanded) { Verify(combo); pattern.Collapse(); }
        }
        public void SetGroupExpanded(string id, bool expanded)
        {
            AutomationElement group = Require(id);
            Verify(group);
            ExpandCollapsePattern pattern = (ExpandCollapsePattern)group.GetCurrentPattern(ExpandCollapsePattern.Pattern);
            if (expanded && pattern.Current.ExpandCollapseState == ExpandCollapseState.Collapsed)
            { Verify(group); pattern.Expand(); }
            else if (!expanded && pattern.Current.ExpandCollapseState == ExpandCollapseState.Expanded)
            { Verify(group); pattern.Collapse(); }
        }
        public void ExpandGroups()
        {
            AutomationElementCollection groups = Root().FindAll(TreeScope.Descendants,
                new AndCondition(new PropertyCondition(AutomationElement.ProcessIdProperty, processId),
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Group)));
            foreach (AutomationElement group in groups)
            {
                object value;
                if (group.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out value))
                {
                    ExpandCollapsePattern pattern = (ExpandCollapsePattern)value;
                    if (pattern.Current.ExpandCollapseState == ExpandCollapseState.Collapsed)
                    { Verify(group); pattern.Expand(); }
                }
            }
        }
        public bool ScrollViewport(double percent)
        {
            if (percent < 0 || percent > 100) throw new ArgumentOutOfRangeException("percent");
            AutomationElementCollection all = Require("WorkspaceTabs").FindAll(TreeScope.Descendants, Condition.TrueCondition);
            foreach (AutomationElement element in all)
            {
                object value;
                if (element.TryGetCurrentPattern(ScrollPattern.Pattern, out value))
                {
                    ScrollPattern pattern = (ScrollPattern)value;
                    if (pattern.Current.VerticallyScrollable)
                    {
                        Verify(element);
                        pattern.SetScrollPercent(ScrollPattern.NoScroll, percent);
                        return true;
                    }
                }
            }
            return false;
        }
        public bool ContainsExtractedText(string text)
        {
            if (String.IsNullOrEmpty(text)) return false;
            AutomationElementCollection all = Root().FindAll(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Text));
            foreach (AutomationElement element in all)
            {
                Verify(element);
                if (element.Current.Name.IndexOf(text, StringComparison.Ordinal) >= 0) return true;
            }
            return false;
        }
        public string ReportStateJson()
        {
            string text = ReadInspectorText("ReportTechnicalState").TrimStart();
            string json = FirstObject(text);
            if (json.IndexOf("\"revision\"", StringComparison.Ordinal) < 0 ||
                json.IndexOf("\"versions\"", StringComparison.Ordinal) < 0)
                throw new InvalidOperationException("Current report state display has an unexpected shape.");
            return json;
        }
        public string TechnicalJson()
        {
            AutomationElementCollection edits = Root().FindAll(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Edit));
            string found = null;
            foreach (AutomationElement edit in edits)
            {
                object value;
                if (!edit.TryGetCurrentPattern(ValuePattern.Pattern, out value)) continue;
                ValuePattern pattern = (ValuePattern)value;
                if (!pattern.Current.IsReadOnly) continue;
                Verify(edit);
                string text = pattern.Current.Value.TrimStart();
                if (!text.StartsWith("{", StringComparison.Ordinal)) continue;
                string json = FirstObject(text);
                if (json.IndexOf("\"revision\"", StringComparison.Ordinal) < 0 ||
                    json.IndexOf("\"versions\"", StringComparison.Ordinal) < 0) continue;
                if (found != null) throw new InvalidOperationException("Multiple native state displays are ambiguous.");
                found = json;
            }
            return found;
        }
        private static string FirstObject(string text)
        {
            int depth = 0;
            bool quoted = false, escape = false;
            for (int i = 0; i < text.Length; i++)
            {
                char ch = text[i];
                if (quoted)
                {
                    if (escape) escape = false;
                    else if (ch == '\\') escape = true;
                    else if (ch == '"') quoted = false;
                }
                else if (ch == '"') quoted = true;
                else if (ch == '{') depth++;
                else if (ch == '}' && --depth == 0) return text.Substring(0, i + 1);
            }
            throw new InvalidOperationException("Native state display is incomplete.");
        }
        public AutomationElement Modal()
        {
            VerifyWindow(main);
            List<AutomationElement> found = new List<AutomationElement>();
            StringBuilder windowClass = new StringBuilder(64);
            StringBuilder buttonClass = new StringBuilder(64);
            EnumWindows(delegate(IntPtr window, IntPtr unused)
            {
                uint pid;
                GetWindowThreadProcessId(window, out pid);
                if (pid == processId && window != main && IsWindowVisible(window))
                {
                    GetClassName(window, windowClass, 64);
                    if (windowClass.ToString() != "#32770") return true;
                    IntPtr confirm = GetDlgItem(window, 1);
                    if (confirm == IntPtr.Zero || GetDlgCtrlID(confirm) != 1) return true;
                    GetClassName(confirm, buttonClass, 64);
                    if (buttonClass.ToString() != "Button" || !IsWindowVisible(confirm)) return true;
                    VerifyWindow(window);
                    AutomationElement element = AutomationElement.FromHandle(window);
                    object value;
                    if (element.TryGetCurrentPattern(WindowPattern.Pattern, out value) && ((WindowPattern)value).Current.IsModal)
                        found.Add(element);
                    else if (GetWindow(window, GW_OWNER) == main) found.Add(element);
                }
                return true;
            }, IntPtr.Zero);
            if (found.Count > 1) throw new InvalidOperationException("Multiple owned modal windows; refusing ambiguous dialog.");
            return found.Count == 1 ? found[0] : null;
        }
        public AutomationElement DialogPath(AutomationElement dialog)
        {
            Verify(dialog);
            AutomationElementCollection all = dialog.FindAll(TreeScope.Descendants, Condition.TrueCondition);
            List<AutomationElement> matches = new List<AutomationElement>();
            foreach (AutomationElement item in all)
            {
                string id = item.Current.AutomationId;
                if (item.Current.ControlType != ControlType.Edit ||
                    (id != "1001" && id != "1148" && id != "1152")) continue;
                object value;
                if (item.TryGetCurrentPattern(ValuePattern.Pattern, out value) && !((ValuePattern)value).Current.IsReadOnly)
                    matches.Add(item);
            }
            if (matches.Count != 1)
            {
                AutomationElement host = dialog.FindFirst(TreeScope.Descendants,
                    new PropertyCondition(AutomationElement.AutomationIdProperty, "FileNameControlHost"));
                if (host != null)
                {
                    matches.Clear();
                    foreach (AutomationElement edit in host.FindAll(TreeScope.Descendants,
                        new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Edit)))
                    {
                        object value;
                        if (edit.TryGetCurrentPattern(ValuePattern.Pattern, out value) && !((ValuePattern)value).Current.IsReadOnly)
                            matches.Add(edit);
                    }
                }
            }
            if (matches.Count != 1) throw new InvalidOperationException("Native dialog filename/folder edit is absent or ambiguous.");
            Verify(matches[0]);
            return matches[0];
        }
        public void SetDialogValue(AutomationElement dialog, string value)
        {
            Verify(dialog);
            SyntheticFiles.FullPath(value);
            AutomationElement editor = DialogPath(dialog);
            Verify(editor);
            ValuePattern pattern = (ValuePattern)editor.GetCurrentPattern(ValuePattern.Pattern);
            if (pattern.Current.IsReadOnly)
                throw new InvalidOperationException("Owned native dialog path is read-only.");
            pattern.SetValue(value);
            if (pattern.Current.Value != value)
                throw new InvalidOperationException("Native dialog did not observe the exact accessibility value; no confirmation or fallback.");
        }
        private static NativeInput Key(ushort key, ushort scan, uint flags)
        { return new NativeInput { Type = 1, VirtualKey = key, Scan = scan, Flags = flags }; }

        public void TypeDialogPath(AutomationElement dialog, string value)
        {
            Verify(dialog);
            if (IntPtr.Size != 8) throw new InvalidOperationException("Use the installed x64 proof process.");
            SyntheticFiles.FullPath(value);
            IntPtr expected = new IntPtr(dialog.Current.NativeWindowHandle);
            if (GetForegroundWindow() != expected && !SetForegroundWindow(expected))
                throw new InvalidOperationException("Cannot activate the exact owned native dialog; no keyboard input sent.");
            AutomationElement editor = DialogPath(dialog);
            Verify(editor);
            editor.SetFocus();
            IntPtr foreground = GetForegroundWindow();
            VerifyWindow(foreground);
            if (GetAncestor(foreground, 2) != expected)
                throw new InvalidOperationException("Native dialog is not the exact foreground owner; no keyboard input sent.");
            foreach (int modifier in new int[] { 0x10, 0x11, 0x12 })
                if ((GetAsyncKeyState(modifier) & 0x8000) != 0)
                    throw new InvalidOperationException("A user modifier is held; no keyboard input sent.");
            NativeInput[] inputs = new NativeInput[checked(value.Length * 2 + 6)];
            int at = 0;
            inputs[at++] = Key(0x11, 0, 0);
            inputs[at++] = Key(0x41, 0, 0);
            inputs[at++] = Key(0x41, 0, 2);
            inputs[at++] = Key(0x11, 0, 2);
            foreach (char character in value)
            {
                inputs[at++] = Key(0, character, 4);
                inputs[at++] = Key(0, character, 6);
            }
            inputs[at++] = Key(0x09, 0, 0);
            inputs[at++] = Key(0x09, 0, 2);
            if (SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(NativeInput))) != (uint)inputs.Length)
                throw new InvalidOperationException("Native keyboard batch was incomplete; no replay or further input.");
            VerifyWindow(GetForegroundWindow());
        }
        public void Shortcut(string action)
        {
            AutomationElement trigger = Require(action);
            if (!trigger.Current.IsEnabled) throw new InvalidOperationException("Owned shortcut action is disabled.");
            ushort key;
            if (action == "NewBlankHwpx") key = 0x4E;
            else if (action == "OpenWorkspace" || action == "NewReport") key = 0x4F;
            else if (action == "StartDraft") key = 0x0D;
            else throw new InvalidOperationException("Shortcut is not in the fixed synthetic journey.");
            if (GetForegroundWindow() != main && !SetForegroundWindow(main))
                throw new InvalidOperationException("Cannot activate exact owned shortcut window.");
            if (action == "StartDraft") Require("Instruction").SetFocus();
            else Root().SetFocus();
            VerifyWindow(GetForegroundWindow());
            if (GetAncestor(GetForegroundWindow(), 2) != main)
                throw new InvalidOperationException("Owned main window is not foreground; no shortcut sent.");
            if ((GetAsyncKeyState(0x10) & 0x8000) != 0 || (GetAsyncKeyState(0x11) & 0x8000) != 0 || (GetAsyncKeyState(0x12) & 0x8000) != 0)
                throw new InvalidOperationException("A user modifier is held; no shortcut sent.");
            NativeInput[] inputs = action == "NewReport"
                ? new NativeInput[] { Key(0x11, 0, 0), Key(0x10, 0, 0), Key(key, 0, 0), Key(key, 0, 2), Key(0x10, 0, 2), Key(0x11, 0, 2) }
                : new NativeInput[] { Key(0x11, 0, 0), Key(key, 0, 0), Key(key, 0, 2), Key(0x11, 0, 2) };
            if (SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(NativeInput))) != (uint)inputs.Length)
                throw new InvalidOperationException("Owned shortcut batch was incomplete; no replay.");
        }

        public void SubmitSyntheticTerminalPrompt()
        {
            VerifyWindow(main);
            if (IntPtr.Size != 8 || !Require("FocusOmpTerminal").Current.IsEnabled)
                throw new InvalidOperationException("The owned actual terminal must be ready.");
            SubmitTerminalPrompt();
        }
        public void SubmitWorkspaceTerminalPrompt()
        {
            VerifyWindow(main);
            RequireRole("DocumentTree", ControlType.Tree);
            AutomationElement back = RequireRole("ShowOmpConversation", ControlType.Button);
            AutomationElement shutdown = RequireRole("ShutdownOmp", ControlType.Button);
            if (IntPtr.Size != 8 || InspectorVisible() || !Visible("ShowOmpConversation") ||
                !Visible("ShutdownOmp") || !back.Current.IsEnabled || !shutdown.Current.IsEnabled)
                throw new InvalidOperationException("The same owned visible terminal must be idle before physical input.");
            SubmitTerminalPrompt();
        }
        private void SubmitTerminalPrompt()
        {
            const string text = "TOY ONLY \uD55C\uAE00 \uD83D\uDE00 terminal advisory";
            if (GetForegroundWindow() != main && !SetForegroundWindow(main))
                throw new InvalidOperationException("Cannot activate the exact owned terminal window.");
            VerifyWindow(GetForegroundWindow());
            if (GetAncestor(GetForegroundWindow(), 2) != main)
                throw new InvalidOperationException("Owned terminal window is not foreground; no input sent.");
            if ((GetAsyncKeyState(0x10) & 0x8000) != 0 || (GetAsyncKeyState(0x11) & 0x8000) != 0 || (GetAsyncKeyState(0x12) & 0x8000) != 0)
                throw new InvalidOperationException("A user modifier is held; no terminal input sent.");
            NativeInput[] inputs = new NativeInput[checked(text.Length * 2 + 2)];
            int at = 0;
            foreach (char character in text)
            {
                inputs[at++] = Key(0, character, 4);
                inputs[at++] = Key(0, character, 6);
            }
            inputs[at++] = Key(0x0D, 0, 0);
            inputs[at++] = Key(0x0D, 0, 2);
            if (SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(NativeInput))) != (uint)inputs.Length)
                throw new InvalidOperationException("Synthetic terminal input batch was incomplete; no replay.");
            VerifyWindow(GetForegroundWindow());
        }


        public PendingAction DialogConfirm(AutomationElement dialog)
        {
            Verify(dialog);
            IntPtr dialogHandle = new IntPtr(dialog.Current.NativeWindowHandle);
            IntPtr control = GetDlgItem(dialogHandle, 1);
            StringBuilder name = new StringBuilder(256);
            GetClassName(control, name, 256);
            if (control == IntPtr.Zero || GetDlgCtrlID(control) != 1 || name.ToString() != "Button")
                throw new InvalidOperationException("Owned native dialog IDOK button is absent.");
            VerifyWindow(GetAncestor(control, 2));
            return new PendingAction(delegate()
            {
                VerifyWindow(dialogHandle);
                VerifyWindow(GetAncestor(control, 2));
                if (GetDlgItem(dialogHandle, 1) != control || GetDlgCtrlID(control) != 1)
                    throw new InvalidOperationException("Owned dialog confirmation control changed.");
                UIntPtr result;
                if (SendMessageTimeout(control, 0x00F5, UIntPtr.Zero, IntPtr.Zero, 2, 10000, out result) == IntPtr.Zero)
                    throw new TimeoutException("Native dialog click observation timed out; no replay or termination.");
                return null;
            });
        }
        public PendingAction DialogCancel(AutomationElement dialog)
        {
            Verify(dialog);
            VerifyWindow(new IntPtr(dialog.Current.NativeWindowHandle));
            AutomationElementCollection buttons = dialog.FindAll(TreeScope.Descendants,
                new AndCondition(new PropertyCondition(AutomationElement.AutomationIdProperty, "2"),
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button)));
            if (buttons.Count != 1 || !buttons[0].Current.IsEnabled)
                throw new InvalidOperationException("Exact owned dialog IDCANCEL button is absent or ambiguous.");
            return Invoke(buttons[0]);
        }

        public PendingAction Capture(string path, AutomationElement element)
        {
            Verify(element);
            IntPtr handle = new IntPtr(element.Current.NativeWindowHandle);
            bool toolsPopup = handle == IntPtr.Zero;
            if (toolsPopup) handle = ToolsMenuWindow(element);
            VerifyWindow(handle);
            return new PendingAction(delegate()
            {
                VerifyWindow(handle);
                if (toolsPopup && ToolsMenuWindow(element) != handle)
                    throw new InvalidOperationException("Owned popup changed before capture; preserving it without retry.");
                Rect rectangle;
                if (!GetWindowRect(handle, out rectangle)) throw new InvalidOperationException("Cannot read owned window bounds.");
                int width = rectangle.Right - rectangle.Left, height = rectangle.Bottom - rectangle.Top;
                if (width <= 0 || height <= 0 || width > 16384 || height > 16384)
                    throw new InvalidOperationException("Owned capture bounds are invalid.");
                CaptureResult result = new CaptureResult();
                result.Path = path; result.Width = width; result.Height = height;
                using (Bitmap image = new Bitmap(width, height, PixelFormat.Format32bppArgb))
                {
                    using (Graphics graphics = Graphics.FromImage(image))
                    {
                        IntPtr hdc = graphics.GetHdc();
                        try { VerifyWindow(handle); result.PrintWindowSucceeded = PrintWindow(handle, hdc, PW_RENDERFULLCONTENT); }
                        finally { graphics.ReleaseHdc(hdc); }
                    }
                    int samples = 0, dark = 0;
                    for (int y = 0; y < height; y += Math.Max(1, height / 64))
                        for (int x = 0; x < width; x += Math.Max(1, width / 64))
                        { Color color = image.GetPixel(x, y); samples++; if (color.R < 12 && color.G < 12 && color.B < 12) dark++; }
                    result.DarkSampleFraction = (double)dark / samples;
                    result.VisualAssessment = !result.PrintWindowSucceeded ? "unsupported-printwindow-no-visual-pass" :
                        result.DarkSampleFraction > 0.99 ? "black-capture-no-visual-pass" : "captured-owned-window-unassessed-no-visual-pass";
                    using (FileStream target = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                        image.Save(target, ImageFormat.Png);
                }
                return result;
            });
        }
    }

    public static class SyntheticFiles
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct FileInformation
        {
            public uint Attributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime, LastAccessTime, LastWriteTime;
            public uint VolumeSerialNumber, FileSizeHigh, FileSizeLow, NumberOfLinks, FileIndexHigh, FileIndexLow;
        }
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(Microsoft.Win32.SafeHandles.SafeFileHandle handle, out FileInformation info);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateDirectory(string path, IntPtr securityAttributes);
        public static void CreateNewDirectory(string path)
        {
            path = FullPath(path);
            if (!CreateDirectory(path, IntPtr.Zero))
                throw new IOException("Evidence directory must be new, with an existing owned parent; nothing is reused.");
        }
        public static string FullPath(string path)
        {
            if (String.IsNullOrEmpty(path) || !Regex.IsMatch(path, "^[A-Za-z]:[\\\\/]") ||
                path.IndexOf(':', 2) >= 0) throw new InvalidOperationException("Only absolute local non-ADS paths are permitted.");
            string full = Path.GetFullPath(path);
            string next = full;
            while (!String.IsNullOrEmpty(next))
            {
                if ((File.Exists(next) || Directory.Exists(next)) && (File.GetAttributes(next) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("Reparse-point paths are outside this driver scope.");
                next = Path.GetDirectoryName(next);
            }
            return full;
        }
        public static bool Under(string path, string directory)
        {
            string full = FullPath(path), root = FullPath(directory).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            return full.StartsWith(root, StringComparison.OrdinalIgnoreCase);
        }
        private static void SingleLink(FileStream file)
        {
            FileInformation info;
            if (!GetFileInformationByHandle(file.SafeFileHandle, out info) || info.NumberOfLinks != 1)
                throw new InvalidOperationException("Only a single-link owned synthetic file is permitted.");
        }
        public static string Sha256(string path)
        {
            using (FileStream stream = new FileStream(FullPath(path), FileMode.Open, FileAccess.Read, FileShare.Read))
            { SingleLink(stream); return Hash(stream); }
        }
        private static string Hash(Stream stream)
        { using (SHA256 algorithm = SHA256.Create()) return BitConverter.ToString(algorithm.ComputeHash(stream)).Replace("-", "").ToLowerInvariant(); }
        private static string Hash(byte[] bytes)
        { using (MemoryStream stream = new MemoryStream(bytes, false)) return Hash(stream); }
        private static byte[] ReadEntry(ZipArchiveEntry entry)
        {
            if (entry.Length > 64 * 1024 * 1024) throw new InvalidOperationException("Synthetic ZIP entry is too large.");
            using (Stream input = entry.Open())
            using (MemoryStream output = new MemoryStream()) { input.CopyTo(output); return output.ToArray(); }
        }
        public static MutationResult EditRecordedReview(string path, string expectedSha256, string before, string after)
        {
            path = FullPath(path);
            DirectoryInfo parent = Directory.GetParent(path);
            if (Path.GetFileName(path) != "review.hwpx" || parent == null || parent.Parent == null || parent.Parent.Name != "review-copies" ||
                !Regex.IsMatch(parent.Name, "^v-[a-f0-9]{32}$") || before != "SYNTHETIC REPORT BODY REVISED" ||
                after != "SYNTHETIC REPORT BODY EDITED IN OWNED REVIEW COPY")
                throw new InvalidOperationException("Only the fixed unsigned recorded review-copy edit is allowed.");
            using (FileStream file = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            {
                SingleLink(file);
                string original = Hash(file);
                if (!String.Equals(original, expectedSha256, StringComparison.Ordinal)) throw new InvalidOperationException("Recorded review bytes changed before synthetic edit.");
                file.Position = 0;
                Dictionary<string, string> unchanged = new Dictionary<string, string>(StringComparer.Ordinal);
                string selected = null;
                byte[] replacement = null;
                UTF8Encoding utf8 = new UTF8Encoding(false, true);
                using (ZipArchive archive = new ZipArchive(file, ZipArchiveMode.Update, true))
                {
                    if (archive.Entries.Count == 0 || archive.Entries.Count > 2048) throw new InvalidOperationException("Synthetic ZIP entry count is invalid.");
                    long total = 0;
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        string name = entry.FullName;
                        if (unchanged.ContainsKey(name) || name.Contains("..") || name.StartsWith("/", StringComparison.Ordinal) || name.Contains("\\") ||
                            name.IndexOf("signature", StringComparison.OrdinalIgnoreCase) >= 0)
                            throw new InvalidOperationException("Review ZIP is signed, ambiguous, or unsafe; preserving it unchanged.");
                        total += entry.Length;
                        if (total > 64 * 1024 * 1024) throw new InvalidOperationException("Expanded review ZIP is too large.");
                        byte[] bytes = ReadEntry(entry);
                        unchanged.Add(name, Hash(bytes));
                        if (!Regex.IsMatch(name, "^Contents/section[0-9]+\\.xml$")) continue;
                        string xml = utf8.GetString(bytes);
                        XmlReaderSettings settings = new XmlReaderSettings();
                        settings.DtdProcessing = DtdProcessing.Prohibit; settings.XmlResolver = null;
                        XmlDocument document = new XmlDocument(); document.PreserveWhitespace = true; document.XmlResolver = null;
                        using (XmlReader reader = XmlReader.Create(new StringReader(xml), settings)) document.Load(reader);
                        int matches = 0;
                        foreach (XmlNode node in document.SelectNodes("//*[local-name()='t']"))
                            if (node.InnerText == before && node.ChildNodes.Count == 1 && node.FirstChild.NodeType == XmlNodeType.Text) matches++;
                        if (matches == 0) continue;
                        int at = xml.IndexOf(before, StringComparison.Ordinal);
                        if (matches != 1 || selected != null || at < 0 || xml.IndexOf(before, at + before.Length, StringComparison.Ordinal) >= 0)
                            throw new InvalidOperationException("Expected exactly one exact synthetic text node; no ZIP change made.");
                        selected = name;
                        replacement = utf8.GetBytes(xml.Substring(0, at) + after + xml.Substring(at + before.Length));
                    }
                    if (selected == null) throw new InvalidOperationException("Exact synthetic body text is missing; no ZIP change made.");
                    ZipArchiveEntry changed = archive.GetEntry(selected);
                    using (Stream target = changed.Open()) { target.SetLength(0); target.Write(replacement, 0, replacement.Length); }
                }
                file.Flush(true);
                file.Position = 0;
                using (ZipArchive archive = new ZipArchive(file, ZipArchiveMode.Read, true))
                {
                    if (archive.Entries.Count != unchanged.Count) throw new InvalidOperationException("ZIP entry inventory changed; preserve failure evidence.");
                    foreach (ZipArchiveEntry entry in archive.Entries)
                        if (Hash(ReadEntry(entry)) != (entry.FullName == selected ? Hash(replacement) : unchanged[entry.FullName]))
                            throw new InvalidOperationException("Unexpected ZIP entry change; preserve failure evidence.");
                }
                file.Position = 0;
                MutationResult result = new MutationResult(); result.Entry = selected;
                result.BeforeSha256 = original; result.AfterSha256 = Hash(file); result.PreservedEntries = unchanged.Count - 1;
                return result;
            }
        }
    }
}
