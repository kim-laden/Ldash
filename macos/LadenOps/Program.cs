using System.Diagnostics;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Photino.NET;

namespace LadenOps;

internal static class Program
{
    internal static Shell? Current;

    private static void Main()
    {
        var config = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            "Library", "Application Support", "laden-ops");
        Directory.CreateDirectory(config);
        if (!SingleInstance.Acquire(Path.Combine(config, "show.sock"), () => Current?.ShowFromOutside()))
            return;
        Current = new Shell(config);
        Current.Run();
    }
}

internal sealed class Shell
{
    private const uint ControlKey = 0x1000;
    private const uint OptionKey = 0x0800;
    private const uint ShiftKey = 0x0200;
    private const uint CommandKey = 0x0100;
    private const uint EventClassKeyboard = 0x6B657962;
    private const uint EventHotKeyPressed = 5;

    private readonly string _configDir;
    private readonly string _shortcutFile;
    private readonly string _windowFile;
    private PhotinoWindow? _window;
    private Process? _backend;
    private HttpClient? _http;
    private CancellationTokenSource? _loop;
    private string _origin = "";
    private IntPtr _hotkey;
    private bool _quitting;
    private bool _pendingShow;
    private bool _handlerInstalled;
    private int _width = 1180;
    private int _height = 780;
    private bool _frameReady;
    private FileSystemWatcher? _shortcutWatcher;
    private Timer? _shortcutDebounce;

    private static readonly CarbonHandler HotkeyThunk = OnHotKey;
    private static readonly ReopenHandler ReopenThunk = OnReopen;
    private static readonly DispatchFn MainThunk = OnMainThunk;
    private static readonly DragHandler DragThunk = OnDrag;
    private static readonly AcceptHandler AcceptThunk = OnAcceptFirstMouse;
    private static IntPtr _dragClass;

    public Shell(string configDir)
    {
        _configDir = configDir;
        _shortcutFile = Path.Combine(configDir, "shortcut");
        _windowFile = Path.Combine(configDir, "window.json");
        (_width, _height) = ReadSize();
    }

    public void Run()
    {
        var window = new PhotinoWindow()
            .SetTitle("Laden Ops")
            .SetUseOsDefaultSize(false)
            .SetUseOsDefaultLocation(false)
            .SetSize(_width, _height)
            .SetMinSize(680, 460)
            .SetResizable(true)
            .SetChromeless(true)
            .SetContextMenuEnabled(false)
            .SetDevToolsEnabled(false)
            .SetGrantBrowserPermissions(false)
            .SetLogVerbosity(0)
            .SetTemporaryFilesPath(Path.Combine(_configDir, "webview"))
            .Center()
            .RegisterWindowCreatedHandler((_, _) =>
            {
                OnMain(InstallFrame);
                _ = Task.Run(Boot);
            })
            .RegisterWindowClosingHandler((_, _) =>
            {
                if (_quitting) return true;
                HideWindow();
                return false;
            })
            .RegisterSizeChangedHandler((_, size) => SaveSize(size.Width, size.Height));
        var icon = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "Resources", "laden.icns"));
        if (File.Exists(icon)) window.SetIconFile(icon);
        _window = window;
        window.WaitForClose();
        Shutdown();
    }

    public void ShowFromOutside()
    {
        _pendingShow = true;
        OnMain(ShowPending);
    }

    private async Task Boot()
    {
        try
        {
            var (port, token) = await StartBackend();
            _origin = $"http://127.0.0.1:{port}";
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(40) };
            _http.DefaultRequestHeaders.Add("X-Laden-Token", token);
            _loop = new CancellationTokenSource();
            _ = Task.Run(() => ControlLoop(_loop.Token));
            OnMain(() =>
            {
                InstallReopen();
                BindHotkey();
                WatchShortcutFile();
                _window?.Load(_origin + "/");
                if (_pendingShow) ShowWindow();
            });
        }
        catch (Exception ex)
        {
            Log(ex.ToString());
            OnMain(() =>
            {
                try
                {
                    _window?.ShowMessage("Laden Ops", ex.Message, PhotinoDialogButtons.Ok, PhotinoDialogIcon.Error);
                }
                catch (Exception)
                {
                }
                _quitting = true;
                _window?.Close();
            });
        }
    }

    private async Task<(string port, string token)> StartBackend()
    {
        var baseDir = AppContext.BaseDirectory;
        var arch = RuntimeInformation.ProcessArchitecture == Architecture.Arm64 ? "arm64" : "x64";
        var prefix = Path.Combine(baseDir, "runtime", arch);
        var python = new[] { "python3", "python3.12" }
            .Select(name => Path.Combine(prefix, "bin", name))
            .FirstOrDefault(File.Exists);
        var serve = Path.Combine(baseDir, "runtime", "serve.py");
        if (python == null || !File.Exists(serve))
            throw new FileNotFoundException("The bundled runtime is missing. Reinstall Laden Ops.");

        var psi = new ProcessStartInfo(python)
        {
            WorkingDirectory = baseDir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        psi.ArgumentList.Add("-u");
        psi.ArgumentList.Add(serve);
        psi.Environment["PYTHONUNBUFFERED"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONNOUSERSITE"] = "1";
        psi.Environment["PYTHONHOME"] = prefix;
        var cert = Path.Combine(baseDir, "runtime", "cacert.pem");
        if (File.Exists(cert))
        {
            psi.Environment["SSL_CERT_FILE"] = cert;
            psi.Environment["REQUESTS_CA_BUNDLE"] = cert;
        }
        _backend = Process.Start(psi) ?? throw new InvalidOperationException("Could not start the dashboard runtime.");
        _backend.ErrorDataReceived += (_, args) =>
        {
            if (!string.IsNullOrEmpty(args.Data)) Log(args.Data);
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
        _ = Task.Run(async () =>
        {
            try
            {
                while (await _backend.StandardOutput.ReadLineAsync() != null)
                {
                }
            }
            catch (Exception)
            {
            }
        });
        if (port == null || token == null)
            throw new InvalidOperationException("The dashboard runtime did not start. See ~/Library/Application Support/laden-ops/serve.log.");
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
                if (ev == "hide") OnMain(HideWindow);
                else if (ev == "quit")
                {
                    OnMain(() =>
                    {
                        _quitting = true;
                        _window?.Close();
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
                try { await Task.Delay(400, cancel); }
                catch (OperationCanceledException) { return; }
            }
        }
    }

    private void Shutdown()
    {
        _quitting = true;
        try { _loop?.Cancel(); } catch (ObjectDisposedException) { }
        try
        {
            if (_hotkey != IntPtr.Zero) UnregisterEventHotKey(_hotkey);
        }
        catch (Exception)
        {
        }
        if (_backend is { HasExited: false })
        {
            try { _backend.Kill(entireProcessTree: true); } catch (Exception) { }
        }
    }

    private void WatchShortcutFile()
    {
        try
        {
            _shortcutWatcher = new FileSystemWatcher(Path.GetDirectoryName(_shortcutFile)!, Path.GetFileName(_shortcutFile))
            {
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size | NotifyFilters.CreationTime,
                EnableRaisingEvents = true,
            };
            _shortcutWatcher.Changed += (_, _) => QueueShortcutRebind();
            _shortcutWatcher.Created += (_, _) => QueueShortcutRebind();
            _shortcutWatcher.Renamed += (_, _) => QueueShortcutRebind();
        }
        catch (Exception ex)
        {
            Log(ex.Message);
        }
    }

    private void QueueShortcutRebind()
    {
        _shortcutDebounce?.Dispose();
        _shortcutDebounce = new Timer(_ => OnMain(BindHotkey), null, 200, Timeout.Infinite);
    }

    private void BindHotkey()
    {
        try
        {
            if (!_handlerInstalled)
            {
                var spec = new[] { new EventTypeSpec { EventClass = EventClassKeyboard, EventKind = EventHotKeyPressed } };
                var installed = InstallEventHandler(GetApplicationEventTarget(), HotkeyThunk, 1, spec, IntPtr.Zero, out _);
                _handlerInstalled = installed == 0;
            }
            if (!TryReadShortcut(out var text)) return;
            if (_hotkey != IntPtr.Zero)
            {
                UnregisterEventHotKey(_hotkey);
                _hotkey = IntPtr.Zero;
            }
            var (mods, code) = ParseShortcut(text);
            var id = new EventHotKeyId { Signature = 0x6C646F70, Id = 1 };
            if (RegisterEventHotKey(code, mods, id, GetApplicationEventTarget(), 0, out var hotkey) == 0)
                _hotkey = hotkey;
        }
        catch (Exception ex)
        {
            Log(ex.Message);
        }
    }

    private static int OnHotKey(IntPtr next, IntPtr evt, IntPtr user)
    {
        Program.Current?.Toggle();
        return 0;
    }

    private void Toggle()
    {
        if (IsHidden()) ShowWindow();
        else HideWindow();
    }

    private void ShowPending()
    {
        if (_pendingShow) ShowWindow();
        _pendingShow = false;
    }

    private static void ShowWindow()
    {
        var app = SharedApp();
        var win = MainWindow(app);
        if (win == IntPtr.Zero) return;
        if (SendBool(win, Sel("isMiniaturized")))
            Send(win, Sel("deminiaturize:"), IntPtr.Zero);
        Send(win, Sel("makeKeyAndOrderFront:"), IntPtr.Zero);
        SendBoolArg(app, Sel("activateIgnoringOtherApps:"), 1);
    }

    private static void HideWindow()
    {
        var win = MainWindow(SharedApp());
        if (win == IntPtr.Zero) return;
        Send(win, Sel("orderOut:"), IntPtr.Zero);
    }

    private static bool IsHidden()
    {
        var win = MainWindow(SharedApp());
        if (win == IntPtr.Zero) return true;
        if (SendBool(win, Sel("isMiniaturized"))) return true;
        return !SendBool(win, Sel("isVisible"));
    }

    // WKWebView ignores app-region, and a drag started after the HTTP round trip
    // has already lost the mouse-down event. The strip calls performWindowDragWithEvent
    // from mouseDown. The right inset leaves the Control switch clickable.
    private void InstallFrame()
    {
        if (_frameReady) return;
        try
        {
            var win = MainWindow(SharedApp());
            if (win == IntPtr.Zero) return;
            SendBoolArg(win, Sel("setTitlebarAppearsTransparent:"), 1);
            SendNUInt(win, Sel("setTitleVisibility:"), 1);
            SendBoolArg(win, Sel("setOpaque:"), 0);
            SendBoolArg(win, Sel("setHasShadow:"), 1);
            var clear = SendId(objc_getClass("NSColor"), Sel("clearColor"));
            if (clear != IntPtr.Zero) Send(win, Sel("setBackgroundColor:"), clear);
            for (nuint button = 0; button < 3; button++)
            {
                var control = SendNUIntRet(win, Sel("standardWindowButton:"), button);
                if (control != IntPtr.Zero) SendBoolArg(control, Sel("setHidden:"), 1);
            }

            var content = SendId(win, Sel("contentView"));
            if (content == IntPtr.Zero) return;
            SendBoolArg(content, Sel("setWantsLayer:"), 1);
            var layer = SendId(content, Sel("layer"));
            if (layer != IntPtr.Zero)
            {
                SendDouble(layer, Sel("setCornerRadius:"), 14);
                SendBoolArg(layer, Sel("setMasksToBounds:"), 1);
            }

            var strip = SendId(SendId(DragClass(), Sel("alloc")), Sel("init"));
            if (strip == IntPtr.Zero) return;
            SendBoolArg(strip, Sel("setTranslatesAutoresizingMaskIntoConstraints:"), 0);
            SendPlace(content, Sel("addSubview:positioned:relativeTo:"), strip, 1, IntPtr.Zero);
            var views = DictOne("strip", strip);
            var horizontal = Constraints("H:|-0-[strip]-56-|", views);
            var vertical = Constraints("V:|-0-[strip(22)]", views);
            if (horizontal != IntPtr.Zero) Send(content, Sel("addConstraints:"), horizontal);
            if (vertical != IntPtr.Zero) Send(content, Sel("addConstraints:"), vertical);
            _frameReady = true;
        }
        catch (Exception ex)
        {
            Log(ex.Message);
        }
    }

    private static IntPtr DragClass()
    {
        if (_dragClass != IntPtr.Zero) return _dragClass;
        var existing = objc_getClass("LadenDragStrip");
        if (existing != IntPtr.Zero)
        {
            _dragClass = existing;
            return existing;
        }
        var cls = objc_allocateClassPair(objc_getClass("NSView"), "LadenDragStrip", 0);
        if (cls == IntPtr.Zero) return IntPtr.Zero;
        class_addMethod(cls, Sel("mouseDown:"), Marshal.GetFunctionPointerForDelegate(DragThunk), "v@:@");
        class_addMethod(cls, Sel("acceptsFirstMouse:"), Marshal.GetFunctionPointerForDelegate(AcceptThunk), "B@:@");
        objc_registerClassPair(cls);
        _dragClass = cls;
        return cls;
    }

    private static void OnDrag(IntPtr self, IntPtr cmd, IntPtr evt)
    {
        var window = SendId(self, Sel("window"));
        if (window != IntPtr.Zero) Send(window, Sel("performWindowDragWithEvent:"), evt);
    }

    private static byte OnAcceptFirstMouse(IntPtr self, IntPtr cmd, IntPtr evt) => 1;

    private static IntPtr NsString(string text)
    {
        var bytes = Encoding.UTF8.GetBytes(text + "\0");
        var mem = Marshal.AllocHGlobal(bytes.Length);
        try
        {
            Marshal.Copy(bytes, 0, mem, bytes.Length);
            return SendPtr(objc_getClass("NSString"), Sel("stringWithUTF8String:"), mem);
        }
        finally
        {
            Marshal.FreeHGlobal(mem);
        }
    }

    private static IntPtr DictOne(string key, IntPtr value)
    {
        return SendId2(objc_getClass("NSDictionary"), Sel("dictionaryWithObject:forKey:"), value, NsString(key));
    }

    private static IntPtr Constraints(string format, IntPtr views)
    {
        return SendFormat(
            objc_getClass("NSLayoutConstraint"),
            Sel("constraintsWithVisualFormat:options:metrics:views:"),
            NsString(format),
            0,
            IntPtr.Zero,
            views);
    }

    private static void InstallReopen()
    {
        try
        {
            var app = SharedApp();
            var del = SendId(app, Sel("delegate"));
            if (del == IntPtr.Zero) return;
            var cls = object_getClass(del);
            class_addMethod(cls, Sel("applicationShouldHandleReopen:hasVisibleWindows:"),
                Marshal.GetFunctionPointerForDelegate(ReopenThunk), "B@:@B");
        }
        catch (Exception ex)
        {
            Program.Current?.Log(ex.Message);
        }
    }

    private static byte OnReopen(IntPtr self, IntPtr cmd, IntPtr app, byte hasVisible)
    {
        ShowWindow();
        return 1;
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

    private void SaveSize(int w, int h)
    {
        if (w < 680 || h < 460) return;
        _width = w;
        _height = h;
        try
        {
            File.WriteAllText(_windowFile, "{\"w\": " + w + ", \"h\": " + h + "}\n");
        }
        catch (IOException)
        {
        }
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

    private static (uint mods, uint code) ParseShortcut(string text)
    {
        uint mods = 0;
        uint code = 0;
        foreach (var raw in text.Split('+'))
        {
            var part = raw.Trim();
            if (part.Equals("Ctrl", StringComparison.OrdinalIgnoreCase) ||
                part.Equals("Control", StringComparison.OrdinalIgnoreCase))
                mods |= ControlKey;
            else if (part.Equals("Alt", StringComparison.OrdinalIgnoreCase) ||
                     part.Equals("Option", StringComparison.OrdinalIgnoreCase))
                mods |= OptionKey;
            else if (part.Equals("Shift", StringComparison.OrdinalIgnoreCase))
                mods |= ShiftKey;
            else if (part.Equals("Super", StringComparison.OrdinalIgnoreCase) ||
                     part.Equals("Win", StringComparison.OrdinalIgnoreCase) ||
                     part.Equals("Meta", StringComparison.OrdinalIgnoreCase) ||
                     part.Equals("Cmd", StringComparison.OrdinalIgnoreCase) ||
                     part.Equals("Command", StringComparison.OrdinalIgnoreCase))
                mods |= CommandKey;
            else
                code = KeyCode(part);
        }
        if (mods == 0) mods = ControlKey;
        if (code == 0) code = 0x32;
        return (mods, code);
    }

    private static uint KeyCode(string part)
    {
        if (part.Length == 1)
        {
            var ch = char.ToUpperInvariant(part[0]);
            if (ch is >= 'A' and <= 'Z')
            {
                uint[] letters =
                {
                    0x00, 0x0B, 0x08, 0x02, 0x0E, 0x03, 0x05, 0x04, 0x22, 0x26,
                    0x28, 0x25, 0x2E, 0x2D, 0x1F, 0x23, 0x0C, 0x0F, 0x01, 0x11,
                    0x20, 0x09, 0x0D, 0x07, 0x10, 0x06,
                };
                return letters[ch - 'A'];
            }
            uint[] digits = { 0x1D, 0x12, 0x13, 0x14, 0x15, 0x17, 0x16, 0x1A, 0x1C, 0x19 };
            if (ch is >= '0' and <= '9') return digits[ch - '0'];
            if (ch == '`') return 0x32;
            if (ch == '-') return 0x1B;
            if (ch == '=') return 0x18;
        }
        if (part.Equals("Space", StringComparison.OrdinalIgnoreCase)) return 0x31;
        if (part.Equals("Tab", StringComparison.OrdinalIgnoreCase)) return 0x30;
        if (part.Equals("Esc", StringComparison.OrdinalIgnoreCase) || part.Equals("Escape", StringComparison.OrdinalIgnoreCase)) return 0x35;
        uint[] fn = { 0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D, 0x67, 0x6F };
        if (part.Length is >= 2 and <= 3 && (part[0] == 'F' || part[0] == 'f') &&
            int.TryParse(part[1..], out var n) && n is >= 1 and <= 12)
            return fn[n - 1];
        return 0;
    }

    private void Log(string line)
    {
        try
        {
            File.AppendAllText(Path.Combine(_configDir, "serve.log"), line + Environment.NewLine);
        }
        catch (IOException)
        {
        }
    }

    private static void OnMain(Action action)
    {
        var handle = GCHandle.Alloc(action);
        dispatch_async_f(dispatch_get_main_queue(), GCHandle.ToIntPtr(handle), Marshal.GetFunctionPointerForDelegate(MainThunk));
    }

    private static void OnMainThunk(IntPtr ctx)
    {
        var handle = GCHandle.FromIntPtr(ctx);
        try
        {
            if (handle.Target is Action action) action();
        }
        finally
        {
            handle.Free();
        }
    }

    private static IntPtr SharedApp() => SendId(objc_getClass("NSApplication"), Sel("sharedApplication"));

    private static IntPtr MainWindow(IntPtr app)
    {
        if (app == IntPtr.Zero) return IntPtr.Zero;
        var win = SendId(app, Sel("keyWindow"));
        if (win != IntPtr.Zero) return win;
        var wins = SendId(app, Sel("windows"));
        if (wins == IntPtr.Zero || SendULong(wins, Sel("count")) == 0) return IntPtr.Zero;
        return SendIndex(wins, Sel("objectAtIndex:"), 0);
    }

    private static IntPtr Sel(string name) => sel_registerName(name);

    private static IntPtr SendId(IntPtr recv, IntPtr sel) => objc_msgSend(recv, sel);

    private static void Send(IntPtr recv, IntPtr sel, IntPtr arg) => objc_msgSend_id(recv, sel, arg);

    private static bool SendBool(IntPtr recv, IntPtr sel) => objc_msgSend_bool(recv, sel) != 0;

    private static void SendBoolArg(IntPtr recv, IntPtr sel, byte arg) => objc_msgSend_boolarg(recv, sel, arg);

    private static ulong SendULong(IntPtr recv, IntPtr sel) => objc_msgSend_ulong(recv, sel);

    private static IntPtr SendIndex(IntPtr recv, IntPtr sel, ulong index) => objc_msgSend_index(recv, sel, index);

    private static void SendNUInt(IntPtr recv, IntPtr sel, nuint arg) => objc_msgSend_nuint(recv, sel, arg);

    private static IntPtr SendNUIntRet(IntPtr recv, IntPtr sel, nuint arg) => objc_msgSend_nuint(recv, sel, arg);

    private static void SendDouble(IntPtr recv, IntPtr sel, double arg) => objc_msgSend_double(recv, sel, arg);

    private static void SendPlace(IntPtr recv, IntPtr sel, IntPtr view, nint place, IntPtr other) =>
        objc_msgSend_place(recv, sel, view, place, other);

    private static IntPtr SendPtr(IntPtr recv, IntPtr sel, IntPtr arg) => objc_msgSend_ptr(recv, sel, arg);

    private static IntPtr SendId2(IntPtr recv, IntPtr sel, IntPtr a, IntPtr b) => objc_msgSend_id2(recv, sel, a, b);

    private static IntPtr SendFormat(IntPtr recv, IntPtr sel, IntPtr format, nuint options, IntPtr metrics, IntPtr views) =>
        objc_msgSend_format(recv, sel, format, options, metrics, views);

    private delegate int CarbonHandler(IntPtr next, IntPtr evt, IntPtr user);
    private delegate byte ReopenHandler(IntPtr self, IntPtr cmd, IntPtr app, byte hasVisible);
    private delegate void DispatchFn(IntPtr ctx);
    private delegate void DragHandler(IntPtr self, IntPtr cmd, IntPtr evt);
    private delegate byte AcceptHandler(IntPtr self, IntPtr cmd, IntPtr evt);

    [StructLayout(LayoutKind.Sequential)]
    private struct EventTypeSpec
    {
        public uint EventClass;
        public uint EventKind;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct EventHotKeyId
    {
        public uint Signature;
        public uint Id;
    }

    private const string ObjC = "/usr/lib/libobjc.A.dylib";
    private const string Carbon = "/System/Library/Frameworks/Carbon.framework/Carbon";

    [DllImport(ObjC, EntryPoint = "objc_getClass")]
    private static extern IntPtr objc_getClass(string name);

    [DllImport(ObjC, EntryPoint = "sel_registerName")]
    private static extern IntPtr sel_registerName(string name);

    [DllImport(ObjC, EntryPoint = "object_getClass")]
    private static extern IntPtr object_getClass(IntPtr obj);

    [DllImport(ObjC, EntryPoint = "class_addMethod")]
    private static extern byte class_addMethod(IntPtr cls, IntPtr sel, IntPtr imp, string types);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend(IntPtr recv, IntPtr sel);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend_id(IntPtr recv, IntPtr sel, IntPtr arg);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern byte objc_msgSend_bool(IntPtr recv, IntPtr sel);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern void objc_msgSend_boolarg(IntPtr recv, IntPtr sel, byte arg);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern ulong objc_msgSend_ulong(IntPtr recv, IntPtr sel);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend_index(IntPtr recv, IntPtr sel, ulong index);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend_nuint(IntPtr recv, IntPtr sel, nuint arg);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern void objc_msgSend_double(IntPtr recv, IntPtr sel, double arg);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern void objc_msgSend_place(IntPtr recv, IntPtr sel, IntPtr view, nint place, IntPtr other);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend_ptr(IntPtr recv, IntPtr sel, IntPtr arg);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend_id2(IntPtr recv, IntPtr sel, IntPtr a, IntPtr b);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend_format(IntPtr recv, IntPtr sel, IntPtr format, nuint options, IntPtr metrics, IntPtr views);

    [DllImport(ObjC, EntryPoint = "objc_allocateClassPair")]
    private static extern IntPtr objc_allocateClassPair(IntPtr superclass, string name, nuint extraBytes);

    [DllImport(ObjC, EntryPoint = "objc_registerClassPair")]
    private static extern void objc_registerClassPair(IntPtr cls);

    [DllImport("/usr/lib/libSystem.B.dylib")]
    private static extern IntPtr dispatch_get_main_queue();

    [DllImport("/usr/lib/libSystem.B.dylib")]
    private static extern void dispatch_async_f(IntPtr queue, IntPtr context, IntPtr work);

    [DllImport(Carbon)]
    private static extern IntPtr GetApplicationEventTarget();

    [DllImport(Carbon)]
    private static extern int InstallEventHandler(IntPtr target, CarbonHandler handler, uint count, EventTypeSpec[] types, IntPtr user, out IntPtr installed);

    [DllImport(Carbon)]
    private static extern int RegisterEventHotKey(uint keyCode, uint modifiers, EventHotKeyId id, IntPtr target, uint options, out IntPtr hotKey);

    [DllImport(Carbon)]
    private static extern int UnregisterEventHotKey(IntPtr hotKey);
}

internal static class SingleInstance
{
    public static bool Acquire(string path, Action onShow)
    {
        if (TrySignal(path)) return false;
        try { File.Delete(path); } catch (IOException) { }
        var listener = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        listener.Bind(new UnixDomainSocketEndPoint(path));
        listener.Listen(4);
        _ = Task.Run(() => AcceptLoop(listener, onShow));
        return true;
    }

    private static bool TrySignal(string path)
    {
        if (!File.Exists(path)) return false;
        try
        {
            using var client = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
            client.Connect(new UnixDomainSocketEndPoint(path));
            client.Send(Encoding.UTF8.GetBytes("show\n"));
            return true;
        }
        catch (SocketException)
        {
            return false;
        }
    }

    private static void AcceptLoop(Socket listener, Action onShow)
    {
        while (true)
        {
            try
            {
                using var client = listener.Accept();
                var buf = new byte[16];
                client.Receive(buf);
                onShow();
            }
            catch (SocketException)
            {
                return;
            }
            catch (ObjectDisposedException)
            {
                return;
            }
        }
    }
}
