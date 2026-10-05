using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace LadenOps;

internal static class Program
{
    private const int WmHotkey = 0x0312;
    private const int WmDisplayChange = 0x007E;
    private const uint ModAlt = 0x0001;
    private const uint ModControl = 0x0002;
    private const uint ModShift = 0x0004;
    private const uint ModWin = 0x0008;
    private const string MutexName = "Local\\LadenOps.Single";
    private const string ShowEventName = "Local\\LadenOps.Show";

    [STAThread]
    private static void Main(string[] args)
    {
        if (SensorDriverOnce.TryHandle(args))
            return;

        using var mutex = new Mutex(true, MutexName, out var created);
        if (!created)
        {
            try
            {
                using var ev = EventWaitHandle.OpenExisting(ShowEventName);
                ev.Set();
            }
            catch (WaitHandleCannotBeOpenedException)
            {
            }
            return;
        }

        using var showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName);
        ApplicationConfiguration.Initialize();
        Application.Run(new ShellForm(showEvent));
    }
}

internal sealed class ShellForm : Form
{
    private const int WmHotkey = 0x0312;
    private const int WmDisplayChange = 0x007E;
    private const int WmNcHitTest = 0x84;
    private const int WmNcLButtonDown = 0xA1;
    private const int HtCaption = 2;
    private const int HtLeft = 10;
    private const int HtRight = 11;
    private const int HtTop = 12;
    private const int HtTopLeft = 13;
    private const int HtTopRight = 14;
    private const int HtBottom = 15;
    private const int HtBottomLeft = 16;
    private const int HtBottomRight = 17;
    private const uint ModAlt = 0x0001;
    private const uint ModControl = 0x0002;
    private const uint ModShift = 0x0004;
    private const uint ModWin = 0x0008;
    private const uint VkOem5 = 0xDC;
    private const uint VkOem102 = 0xE2;
    private const byte VkMediaPlayPause = 0xB3;
    private const uint KeyeventfExtendedkey = 0x0001;
    private const uint KeyeventfKeyup = 0x0002;

    private readonly WebView2 _view = new() { Dock = DockStyle.Fill };
    private readonly EventWaitHandle _showEvent;
    private readonly string _configDir;
    private readonly string _shortcutFile;
    private readonly string _pauseModeFile;
    private readonly string _windowFile;
    private readonly List<int> _hotkeyIds = new();
    private Process? _backend;
    private HttpClient? _http;
    private CancellationTokenSource? _loop;
    private string _origin = "";
    private int _nextHotkeyId = 1;
    private bool _quitting;
    private bool _weMuted;
    private bool _pauseArmed;
    private FileSystemWatcher? _shortcutWatcher;
    private System.Windows.Forms.Timer? _shortcutTimer;
    private SensorsMonitor? _sensors;

    public ShellForm(EventWaitHandle showEvent)
    {
        _showEvent = showEvent;
        var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        _configDir = Path.Combine(appData, "laden-ops");
        Directory.CreateDirectory(_configDir);
        _shortcutFile = Path.Combine(_configDir, "shortcut");
        _pauseModeFile = Path.Combine(_configDir, "pause-mode");
        _windowFile = Path.Combine(_configDir, "window.json");

        Text = "Laden Ops";
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(680, 460);
        Padding = new Padding(8);
        var (w, h) = ReadSize();
        ClientSize = new Size(w, h);
        BackColor = Color.FromArgb(5, 7, 10);
        var iconPath = Path.Combine(AppContext.BaseDirectory, "laden.ico");
        if (File.Exists(iconPath)) Icon = new Icon(iconPath);

        Controls.Add(_view);
        Load += OnLoad;
        FormClosing += OnClosing;
        Resize += (_, _) => RoundFrame();
        ResizeEnd += (_, _) => SaveSize();
        RoundFrame();
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WmHotkey)
        {
            Toggle();
        }
        if (m.Msg == WmDisplayChange)
        {
            BeginInvoke(ApplyWindowSnap);
        }
        if (m.Msg == WmNcHitTest)
        {
            base.WndProc(ref m);
            var screen = new Point(SignedLow(m.LParam), SignedHigh(m.LParam));
            var point = PointToClient(screen);
            const int grip = 8;
            var left = point.X < grip;
            var right = point.X >= ClientSize.Width - grip;
            var top = point.Y < grip;
            var bottom = point.Y >= ClientSize.Height - grip;
            if (top && left) m.Result = (IntPtr)HtTopLeft;
            else if (top && right) m.Result = (IntPtr)HtTopRight;
            else if (bottom && left) m.Result = (IntPtr)HtBottomLeft;
            else if (bottom && right) m.Result = (IntPtr)HtBottomRight;
            else if (left) m.Result = (IntPtr)HtLeft;
            else if (right) m.Result = (IntPtr)HtRight;
            else if (top) m.Result = (IntPtr)HtTop;
            else if (bottom) m.Result = (IntPtr)HtBottom;
            return;
        }
        base.WndProc(ref m);
    }

    private void RoundFrame()
    {
        if (Width < 20 || Height < 20) return;
        Region = Region.FromHrgn(CreateRoundRectRgn(0, 0, Width, Height, 28, 28));
    }

    private void BeginDrag()
    {
        ReleaseCapture();
        SendMessage(Handle, WmNcLButtonDown, (IntPtr)HtCaption, IntPtr.Zero);
    }

    private static int SignedLow(IntPtr value) => (short)((long)value & 0xFFFF);
    private static int SignedHigh(IntPtr value) => (short)(((long)value >> 16) & 0xFFFF);

    private async void OnLoad(object? sender, EventArgs e)
    {
        _sensors = new SensorsMonitor(_configDir);
        _sensors.Start();
        // Optional: run "LadenOps.exe sensors-once" elevated once if you want LHM Ryzen Tctl
        // (WinRing0). AMD ADL in winhost.py already fills CPU/GPU temps without admin.

        try
        {
            var (port, token) = await StartBackend();
            _origin = $"http://127.0.0.1:{port}";
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(40) };
            _http.DefaultRequestHeaders.Add("X-Laden-Token", token);
            _loop = new CancellationTokenSource();
            _ = Task.Run(() => ControlLoop(_loop.Token));
            _ = Task.Run(WatchShowEvent);
            BindHotkey();
            WatchShortcutFile();

            var userData = Path.Combine(_configDir, "webview");
            Directory.CreateDirectory(userData);
            var env = await CreateEnvironment(userData);
            await _view.EnsureCoreWebView2Async(env);
            var settings = _view.CoreWebView2.Settings;
            settings.AreDevToolsEnabled = false;
            settings.AreDefaultContextMenusEnabled = false;
            settings.IsStatusBarEnabled = false;
            settings.AreBrowserAcceleratorKeysEnabled = false;
            try { settings.IsNonClientRegionSupportEnabled = true; } catch (NotSupportedException) { }
            _view.CoreWebView2.NavigationStarting += (_, args) =>
            {
                if (!args.Uri.StartsWith("http://127.0.0.1:", StringComparison.OrdinalIgnoreCase))
                    args.Cancel = true;
            };
            _view.CoreWebView2.NewWindowRequested += (_, args) =>
            {
                args.Handled = true;
                if (Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri) &&
                    (uri.Scheme == "http" || uri.Scheme == "https"))
                {
                    Process.Start(new ProcessStartInfo(uri.ToString()) { UseShellExecute = true });
                }
            };
            _view.Source = new Uri(_origin + "/");
            BeginInvoke(ApplyWindowSnap);
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, "Laden Ops", MessageBoxButtons.OK, MessageBoxIcon.Error);
            _quitting = true;
            Close();
        }
    }

    private async Task<(string port, string token)> StartBackend()
    {
        var baseDir = AppContext.BaseDirectory;
        var python = Path.Combine(baseDir, "runtime", "python.exe");
        var serve = Path.Combine(baseDir, "runtime", "serve.py");
        if (!File.Exists(python) || !File.Exists(serve))
            throw new FileNotFoundException("The bundled runtime is missing. Reinstall Laden Ops.");

        var psi = new ProcessStartInfo(python, $"-u \"{serve}\"")
        {
            WorkingDirectory = baseDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        psi.Environment["PYTHONUNBUFFERED"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        _backend = Process.Start(psi) ?? throw new InvalidOperationException("Could not start the dashboard runtime.");
        _backend.ErrorDataReceived += (_, args) =>
        {
            if (string.IsNullOrEmpty(args.Data)) return;
            try
            {
                File.AppendAllText(Path.Combine(_configDir, "serve.log"), args.Data + Environment.NewLine);
            }
            catch (IOException)
            {
            }
        };
        _backend.BeginErrorReadLine();

        string? port = null;
        string? token = null;
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while ((port == null || token == null) && DateTime.UtcNow < deadline)
        {
            var line = await _backend.StandardOutput.ReadLineAsync();
            if (line == null) break;
            if (line.StartsWith("PORT ", StringComparison.Ordinal)) port = line[5..].Trim();
            if (line.StartsWith("TOKEN ", StringComparison.Ordinal)) token = line[6..].Trim();
        }
        if (port == null || token == null)
            throw new InvalidOperationException("The dashboard runtime did not start. See %APPDATA%\\laden-ops\\serve.log.");
        return (port, token);
    }

    private async Task ControlLoop(CancellationToken cancel)
    {
        while (!cancel.IsCancellationRequested && _http != null)
        {
            try
            {
                using var res = await _http.GetAsync(_origin + "/ctl", cancel);
                var json = await res.Content.ReadAsStringAsync(cancel);
                using var doc = JsonDocument.Parse(json);
                var ev = doc.RootElement.GetProperty("event").GetString();
                if (ev == "hide") BeginInvoke(HideWindow);
                else if (ev == "drag") BeginInvoke(BeginDrag);
                else if (ev == "snap") BeginInvoke(ApplyWindowSnap);
                else if (ev == "quit")
                {
                    BeginInvoke(() =>
                    {
                        _quitting = true;
                        Close();
                    });
                    return;
                }
            }
            catch (OperationCanceledException)
            {
                return;
            }
            catch (Exception)
            {
                await Task.Delay(400, cancel);
            }
        }
    }

    private static async Task<CoreWebView2Environment> CreateEnvironment(string userData)
    {
        try
        {
            return await CoreWebView2Environment.CreateAsync(null, userData);
        }
        catch (Exception first)
        {
            var setup = Path.Combine(AppContext.BaseDirectory, "redist", "MicrosoftEdgeWebview2Setup.exe");
            if (!File.Exists(setup)) throw;
            using var proc = Process.Start(new ProcessStartInfo(setup, "/silent /install") { UseShellExecute = true });
            if (proc != null) await proc.WaitForExitAsync();
            try
            {
                return await CoreWebView2Environment.CreateAsync(null, userData);
            }
            catch (Exception)
            {
                throw first;
            }
        }
    }

    private void WatchShowEvent()
    {
        while (!_quitting)
        {
            if (_showEvent.WaitOne(500))
                BeginInvoke(ShowWindow);
        }
    }

    private void Toggle()
    {
        if (!Visible || WindowState == FormWindowState.Minimized)
            ShowWindow();
        else
            HideWindow();
    }

    private void ShowWindow()
    {
        ApplyPauseOnShow();
        Show();
        WindowState = FormWindowState.Normal;
        ApplyWindowSnap();
        Activate();
    }

    private void HideWindow()
    {
        RestorePauseOnHide();
        Hide();
    }

    private void OnClosing(object? sender, FormClosingEventArgs e)
    {
        try { _sensors?.Dispose(); } catch { }
        _sensors = null;

        if (_quitting)
        {
            RestorePauseOnHide();
            return;
        }
        e.Cancel = true;
        HideWindow();
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        _quitting = true;
        RestorePauseOnHide();
        try { _loop?.Cancel(); } catch (ObjectDisposedException) { }
        try { UnbindHotkey(); } catch (Exception) { }
        if (_backend is { HasExited: false })
        {
            try { _backend.Kill(entireProcessTree: true); } catch (Exception) { }
        }
        base.OnFormClosed(e);
    }

    private void WatchShortcutFile()
    {
        _shortcutWatcher = new FileSystemWatcher(Path.GetDirectoryName(_shortcutFile)!, Path.GetFileName(_shortcutFile))
        {
            NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        _shortcutWatcher.Changed += (_, _) => BeginInvoke(QueueShortcutRebind);
        _shortcutWatcher.Created += (_, _) => BeginInvoke(QueueShortcutRebind);
        _shortcutWatcher.Renamed += (_, _) => BeginInvoke(QueueShortcutRebind);
    }

    private void QueueShortcutRebind()
    {
        _shortcutTimer ??= new System.Windows.Forms.Timer { Interval = 200 };
        _shortcutTimer.Tick -= OnShortcutTick;
        _shortcutTimer.Tick += OnShortcutTick;
        _shortcutTimer.Stop();
        _shortcutTimer.Start();
    }

    private void OnShortcutTick(object? sender, EventArgs e)
    {
        _shortcutTimer?.Stop();
        BindHotkey();
    }

    private void BindHotkey()
    {
        if (!TryReadShortcut(out var text)) return;
        UnbindHotkey();
        foreach (var combo in ExpandShortcuts(text))
        {
            var (mods, vk) = ParseShortcut(combo);
            if (vk == 0) continue;
            TryRegister(mods, vk);
            // Norwegian ISO key often carries | ; register as companion for pipe/backslash.
            if (vk == VkOem5)
                TryRegister(mods, VkOem102);
        }
    }

    private void TryRegister(uint mods, uint vk)
    {
        var id = _nextHotkeyId++;
        if (RegisterHotKey(Handle, id, mods, vk))
            _hotkeyIds.Add(id);
    }

    private void UnbindHotkey()
    {
        foreach (var id in _hotkeyIds)
        {
            try { UnregisterHotKey(Handle, id); } catch (Exception) { }
        }
        _hotkeyIds.Clear();
    }

    private static IEnumerable<string> ExpandShortcuts(string text)
    {
        var parts = text.Split(',')
            .Select(p => p.Trim())
            .Where(p => p.Length > 0)
            .ToList();
        if (parts.Count == 0)
            parts.Add("Ctrl+`");
        if (parts.Count == 1 && parts[0].Equals("Ctrl+`", StringComparison.OrdinalIgnoreCase))
            return new[] { "Ctrl+`", "Ctrl+|" };
        return parts;
    }

    private bool TryReadShortcut(out string text)
    {
        text = "Ctrl+`";
        for (var attempt = 0; attempt < 4; attempt++)
        {
            try
            {
                if (!File.Exists(_shortcutFile)) return true;
                var raw = File.ReadAllText(_shortcutFile).Trim();
                var line = raw.Split('\n')[0].Trim();
                if (line.Length == 0)
                {
                    Thread.Sleep(40);
                    continue;
                }
                text = line;
                return true;
            }
            catch (IOException)
            {
                Thread.Sleep(40);
            }
            catch (UnauthorizedAccessException)
            {
                Thread.Sleep(40);
            }
        }
        return !File.Exists(_shortcutFile);
    }

    private bool PauseModeEnabled()
    {
        try
        {
            if (!File.Exists(_pauseModeFile)) return false;
            var raw = File.ReadAllText(_pauseModeFile).Trim().ToLowerInvariant();
            return raw is "1" or "true" or "on" or "yes";
        }
        catch (IOException)
        {
            return false;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
    }

    private void ApplyPauseOnShow()
    {
        if (!PauseModeEnabled()) return;
        try
        {
            _pauseArmed = true;
            _weMuted = false;
            if (AudioMute.TryGetMute(out var muted) && !muted)
            {
                if (AudioMute.TrySetMute(true))
                    _weMuted = true;
            }
            SendMediaPlayPause();
        }
        catch (Exception)
        {
        }
    }

    private void RestorePauseOnHide()
    {
        if (!_pauseArmed) return;
        _pauseArmed = false;
        try
        {
            if (_weMuted)
            {
                AudioMute.TrySetMute(false);
                _weMuted = false;
            }
            SendMediaPlayPause();
        }
        catch (Exception)
        {
        }
    }

    private static void SendMediaPlayPause()
    {
        keybd_event(VkMediaPlayPause, 0, KeyeventfExtendedkey, UIntPtr.Zero);
        keybd_event(VkMediaPlayPause, 0, KeyeventfExtendedkey | KeyeventfKeyup, UIntPtr.Zero);
    }

    private (int w, int h) ReadSize()
    {
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(_windowFile));
            var w = doc.RootElement.GetProperty("w").GetInt32();
            var h = doc.RootElement.GetProperty("h").GetInt32();
            return (Math.Clamp(w, 680, 3840), Math.Clamp(h, 460, 2160));
        }
        catch (Exception)
        {
            return (1180, 780);
        }
    }

    private string ReadSnap()
    {
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(_windowFile));
            if (doc.RootElement.TryGetProperty("snap", out var snap))
            {
                var mode = (snap.GetString() ?? "right").Trim().ToLowerInvariant();
                if (mode == "left" || mode == "right" || mode == "off") return mode;
            }
        }
        catch (Exception)
        {
        }
        return "right";
    }

    private void SaveSize()
    {
        if (WindowState != FormWindowState.Normal) return;
        try
        {
            var snap = ReadSnap();
            File.WriteAllText(_windowFile, "{\"w\": " + ClientSize.Width + ", \"h\": " + ClientSize.Height + ", \"snap\": \"" + snap + "\"}\n");
        }
        catch (IOException)
        {
        }
    }

    private void ApplyWindowSnap()
    {
        if (WindowState != FormWindowState.Normal) return;
        var snap = ReadSnap();
        if (snap != "left" && snap != "right") return;
        var screen = Screen.FromControl(this);
        var wa = screen.WorkingArea;
        var half = Math.Max(680, wa.Width / 2);
        var height = Math.Max(460, wa.Height);
        var left = snap == "left" ? wa.Left : wa.Right - half;
        Bounds = new Rectangle(left, wa.Top, half, height);
        RoundFrame();
        try
        {
            File.WriteAllText(_windowFile, "{\"w\": " + ClientSize.Width + ", \"h\": " + ClientSize.Height + ", \"snap\": \"" + snap + "\"}\n");
        }
        catch (IOException)
        {
        }
    }

    private static (uint mods, uint vk) ParseShortcut(string text)
    {
        uint mods = 0;
        uint vk = 0;
        foreach (var raw in text.Split('+'))
        {
            var part = raw.Trim();
            if (part.Length == 0) continue;
            if (part.Equals("Ctrl", StringComparison.OrdinalIgnoreCase) ||
                part.Equals("Control", StringComparison.OrdinalIgnoreCase))
                mods |= ModControl;
            else if (part.Equals("Alt", StringComparison.OrdinalIgnoreCase))
                mods |= ModAlt;
            else if (part.Equals("Shift", StringComparison.OrdinalIgnoreCase))
                mods |= ModShift;
            else if (part.Equals("Super", StringComparison.OrdinalIgnoreCase) ||
                     part.Equals("Win", StringComparison.OrdinalIgnoreCase) ||
                     part.Equals("Meta", StringComparison.OrdinalIgnoreCase))
                mods |= ModWin;
            else
                vk = KeyToVk(part);
        }
        if (mods == 0) mods = ModControl;
        if (vk == 0) vk = 0xC0;
        return (mods, vk);
    }

    private static uint KeyToVk(string part)
    {
        if (part.Length == 1)
        {
            var ch = char.ToUpperInvariant(part[0]);
            if (ch is >= 'A' and <= 'Z' or >= '0' and <= '9') return ch;
            if (part[0] == '`') return 0xC0;
            if (ch == '-') return 0xBD;
            if (ch == '=') return 0xBB;
            if (part[0] == '|' || part[0] == '\\') return VkOem5;
        }
        if (part.Equals("Space", StringComparison.OrdinalIgnoreCase)) return 0x20;
        if (part.Equals("Tab", StringComparison.OrdinalIgnoreCase)) return 0x09;
        if (part.Equals("Esc", StringComparison.OrdinalIgnoreCase) || part.Equals("Escape", StringComparison.OrdinalIgnoreCase)) return 0x1B;
        if (part.Length is >= 2 and <= 3 && (part[0] == 'F' || part[0] == 'f') && int.TryParse(part[1..], out var n) && n is >= 1 and <= 12)
            return (uint)(0x70 + n - 1);
        return 0;
    }

    [DllImport("user32.dll")]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll")]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateRoundRectRgn(int left, int top, int right, int bottom, int widthEllipse, int heightEllipse);
}

internal static class AudioMute
{
    private const int EDataFlowRender = 0;
    private const int ERoleMultimedia = 1;

    public static bool TryGetMute(out bool muted)
    {
        muted = false;
        try
        {
            var volume = GetVolume();
            if (volume == null) return false;
            try
            {
                volume.GetMute(out var value);
                muted = value;
                return true;
            }
            finally
            {
                Marshal.ReleaseComObject(volume);
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    public static bool TrySetMute(bool mute)
    {
        try
        {
            var volume = GetVolume();
            if (volume == null) return false;
            try
            {
                var ctx = Guid.Empty;
                volume.SetMute(mute, ref ctx);
                return true;
            }
            finally
            {
                Marshal.ReleaseComObject(volume);
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static IAudioEndpointVolume? GetVolume()
    {
        var enumeratorType = Type.GetTypeFromCLSID(new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E"));
        if (enumeratorType == null) return null;
        object? raw = Activator.CreateInstance(enumeratorType);
        if (raw is not IMMDeviceEnumerator enumerator) return null;
        try
        {
            enumerator.GetDefaultAudioEndpoint(EDataFlowRender, ERoleMultimedia, out var device);
            if (device == null) return null;
            try
            {
                var iid = typeof(IAudioEndpointVolume).GUID;
                device.Activate(ref iid, 0, IntPtr.Zero, out var obj);
                return obj as IAudioEndpointVolume;
            }
            finally
            {
                Marshal.ReleaseComObject(device);
            }
        }
        finally
        {
            Marshal.ReleaseComObject(enumerator);
        }
    }

    [ComImport]
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceEnumerator
    {
        [PreserveSig]
        int EnumAudioEndpoints(int dataFlow, int dwStateMask, out IntPtr ppDevices);
        [PreserveSig]
        int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice);
    }

    [ComImport]
    [Guid("D666063F-1587-4E43-81F1-B948E807363F")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDevice
    {
        [PreserveSig]
        int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
    }

    [ComImport]
    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IAudioEndpointVolume
    {
        [PreserveSig] int RegisterControlChangeNotify(IntPtr pNotify);
        [PreserveSig] int UnregisterControlChangeNotify(IntPtr pNotify);
        [PreserveSig] int GetChannelCount(out uint pnChannelCount);
        [PreserveSig] int SetMasterVolumeLevel(float fLevelDB, ref Guid pguidEventContext);
        [PreserveSig] int SetMasterVolumeLevelScalar(float fLevel, ref Guid pguidEventContext);
        [PreserveSig] int GetMasterVolumeLevel(out float pfLevelDB);
        [PreserveSig] int GetMasterVolumeLevelScalar(out float pfLevel);
        [PreserveSig] int SetChannelVolumeLevel(uint nChannel, float fLevelDB, ref Guid pguidEventContext);
        [PreserveSig] int SetChannelVolumeLevelScalar(uint nChannel, float fLevel, ref Guid pguidEventContext);
        [PreserveSig] int GetChannelVolumeLevel(uint nChannel, out float pfLevelDB);
        [PreserveSig] int GetChannelVolumeLevelScalar(uint nChannel, out float pfLevel);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, ref Guid pguidEventContext);
        [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool pbMute);
    }
}
