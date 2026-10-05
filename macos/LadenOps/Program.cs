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
        // Never leave serve.py running on 127.0.0.1 when the shell goes away.
        AppDomain.CurrentDomain.ProcessExit += (_, _) =>
        {
            Current?.LogLine($"{DateTime.Now:yyyy-MM-dd HH:mm:ss} process exit ({Current?.ExitReason ?? "unknown"})");
            Current?.KillBackendNow();
        };
        // A SIGTERM/SIGHUP/SIGINT leaves no crash report: record who ended us.
        var signals = new List<PosixSignalRegistration>();
        foreach (var sig in new[] { PosixSignal.SIGTERM, PosixSignal.SIGHUP, PosixSignal.SIGINT })
        {
            try
            {
                signals.Add(PosixSignalRegistration.Create(sig, ctx =>
                {
                    if (Current != null) Current.ExitReason = "signal " + ctx.Signal;
                    Current?.LogLine($"{DateTime.Now:yyyy-MM-dd HH:mm:ss} received {ctx.Signal}");
                }));
            }
            catch (Exception)
            {
            }
        }
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
        {
            Current?.LogLine("unhandled: " + e.ExceptionObject);
            Current?.KillBackendNow();
        };
        try
        {
            Current.Run();
        }
        finally
        {
            Current.KillBackendNow();
            GC.KeepAlive(signals);
        }
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
    private static readonly BoolIdHandler TerminateAfterLastThunk = OnShouldTerminateAfterLastWindowClosed;
    private static readonly BoolIdHandler ShouldCloseThunk = OnWindowShouldClose;
    private static readonly MenuActionHandler MenuCloseThunk = OnMenuClose;
    private static readonly MenuActionHandler MenuQuitThunk = OnMenuQuit;
    private static IntPtr _menuTarget;
    private static IntPtr _activity;
    private static IntPtr _nsWindow;
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

    private static readonly TimeSpan BackendTimeout = TimeSpan.FromSeconds(45);
    private readonly System.Collections.Concurrent.ConcurrentQueue<string> _stderrTail = new();
    private bool _backendOk;
    private bool _servicesStarted;
    private int _retrying;
    internal string ExitReason { get; set; } = "window closed";

    private sealed record BootResult(bool Ok, string Port, string Token, string Error);

    public void Run()
    {
        // Photino needs StartUrl or StartString before WaitForClose creates the
        // native window. Start the backend first and wait for its PORT/TOKEN
        // lines, then open the window on the real URL, or on an error page.
        // App Nap off first: a hidden app must keep its control loop, timers and
        // backend wait running at full speed.
        DisableAppNap();
        var boot = StartBackendBlocking();
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
                // Photino raises this on the main (AppKit) thread.
                try
                {
                    RememberWindow();
                    InstallLifecycle();
                    InstallMenu();
                    InstallFrame();
                    ApplyWindowSnap();
                    if (_backendOk) StartServices();
                }
                catch (Exception ex)
                {
                    Log("window created: " + ex);
                }
            })
            .RegisterWindowClosingHandler((_, _) =>
            {
                // On macOS Photino raises this from windowWillClose (too late to
                // cancel); windowShouldClose: below is what turns close into hide.
                if (_quitting || !_backendOk) return true;
                HideWindow();
                return false;
            })
            .RegisterWebMessageReceivedHandler((_, message) =>
            {
                try
                {
                    OnWebMessage(message);
                }
                catch (Exception ex)
                {
                    Log("web message: " + ex);
                }
            })
            .RegisterSizeChangedHandler((_, size) => SaveSize(size.Width, size.Height));
        var icon = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "Resources", "laden.icns"));
        if (File.Exists(icon)) window.SetIconFile(icon);

        var startSet = false;
        if (boot.Ok && Uri.TryCreate($"http://127.0.0.1:{boot.Port}/", UriKind.Absolute, out var start))
        {
            ApplyBoot(boot);
            window.Load(start);
            startSet = true;
        }
        if (!startSet)
        {
            _backendOk = false;
            window.LoadRawString(ErrorPage(boot.Ok ? "The dashboard runtime reported an invalid port." : boot.Error));
        }
        _window = window;
        window.WaitForClose();
        Log($"{DateTime.Now:yyyy-MM-dd HH:mm:ss} shell exiting: {ExitReason} (quitting={_quitting}, backendOk={_backendOk})");
        Shutdown();
    }

    public void ShowFromOutside()
    {
        Log($"{DateTime.Now:HH:mm:ss} show (second launch)");
        _pendingShow = true;
        OnMain(ShowPending);
    }

    private void ApplyBoot(BootResult boot)
    {
        _origin = $"http://127.0.0.1:{boot.Port}";
        _http?.Dispose();
        // Per-request deadlines are set in ControlLoop (the /ctl poll is long).
        _http = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        _http.DefaultRequestHeaders.Add("X-Laden-Token", boot.Token);
        _backendOk = true;
    }

    // Runs on the main thread once the window exists and the backend is up.
    private void StartServices()
    {
        if (_servicesStarted || !_backendOk) return;
        _servicesStarted = true;
        _loop = new CancellationTokenSource();
        var token = _loop.Token;
        _ = Task.Run(() => ControlLoop(token));
        InstallReopen();
        BindHotkey();
        WatchShortcutFile();
        if (_pendingShow) ShowWindow();
    }

    private void OnWebMessage(string? message)
    {
        var text = (message ?? "").Trim();
        if (text == "laden:quit")
        {
            OnMain(() => RequestQuit("error page Quit"));
        }
        else if (text == "laden:retry" && !_backendOk && Interlocked.Exchange(ref _retrying, 1) == 0)
        {
            _ = Task.Run(() =>
            {
                try
                {
                    var boot = StartBackendBlocking();
                    OnMain(() =>
                    {
                        if (boot.Ok && Uri.TryCreate($"http://127.0.0.1:{boot.Port}/", UriKind.Absolute, out var url))
                        {
                            ApplyBoot(boot);
                            _window?.Load(url);
                            StartServices();
                        }
                        else
                        {
                            _window?.LoadRawString(ErrorPage(boot.Ok ? "The dashboard runtime reported an invalid port." : boot.Error));
                        }
                    });
                }
                finally
                {
                    Interlocked.Exchange(ref _retrying, 0);
                }
            });
        }
    }

    private BootResult StartBackendBlocking()
    {
        try
        {
            return StartBackend(BackendTimeout).GetAwaiter().GetResult();
        }
        catch (Exception ex)
        {
            Log("backend start failed: " + ex);
            StopBackend();
            return new BootResult(false, "", "", ex.Message);
        }
    }

    internal void KillBackendNow() => StopBackend();

    internal void LogLine(string line) => Log(line);

    private void StopBackend()
    {
        var old = _backend;
        _backend = null;
        if (old == null) return;
        try
        {
            if (!old.HasExited) old.Kill(entireProcessTree: true);
        }
        catch (Exception)
        {
        }
    }

    internal static string? FindPython(string baseDir, string arch)
    {
        var prefix = Path.Combine(baseDir, "runtime", arch);
        return new[] { "python3", "python3.12" }
            .Select(name => Path.Combine(prefix, "bin", name))
            .FirstOrDefault(File.Exists);
    }

    private async Task<BootResult> StartBackend(TimeSpan timeout)
    {
        StopBackend();
        while (_stderrTail.TryDequeue(out string? _))
        {
        }
        // AppContext.BaseDirectory is Contents/MacOS/ inside the bundle, also
        // when launchd starts us with cwd "/" and a minimal environment.
        var baseDir = AppContext.BaseDirectory;
        var arch = RuntimeInformation.ProcessArchitecture == Architecture.Arm64 ? "arm64" : "x64";
        var prefix = Path.Combine(baseDir, "runtime", arch);
        var python = FindPython(baseDir, arch);
        var serve = Path.Combine(baseDir, "runtime", "serve.py");
        if (python == null)
            return new BootResult(false, "", "", $"The bundled Python runtime is missing ({prefix}/bin). Reinstall Laden Ops.");
        if (!File.Exists(serve))
            return new BootResult(false, "", "", $"The dashboard server is missing ({serve}). Reinstall Laden Ops.");

        var psi = new ProcessStartInfo(python)
        {
            WorkingDirectory = baseDir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = false,
        };
        psi.ArgumentList.Add("-u");
        psi.ArgumentList.Add(serve);
        psi.Environment.Remove("PYTHONPATH");
        psi.Environment.Remove("PYTHONSTARTUP");
        psi.Environment["PYTHONUNBUFFERED"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUTF8"] = "1";
        psi.Environment["PYTHONNOUSERSITE"] = "1";
        psi.Environment["PYTHONHOME"] = prefix;
        // serve.py exits by itself if this process disappears (crash, SIGKILL).
        psi.Environment["LADEN_PARENT_PID"] = Environment.ProcessId.ToString();
        // A LaunchAgent gets no shell profile: make sure the tools host.py and
        // machost.py call (ioreg, sysctl, open, osascript) resolve.
        var path = psi.Environment.TryGetValue("PATH", out var p) ? p ?? "" : "";
        foreach (var dir in new[] { "/usr/bin", "/bin", "/usr/sbin", "/sbin" })
        {
            if (!path.Split(':').Contains(dir)) path = path.Length == 0 ? dir : path + ":" + dir;
        }
        psi.Environment["PATH"] = path;
        if (!psi.Environment.TryGetValue("HOME", out var home) || string.IsNullOrEmpty(home))
            psi.Environment["HOME"] = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var cert = Path.Combine(baseDir, "runtime", "cacert.pem");
        if (File.Exists(cert))
        {
            psi.Environment["SSL_CERT_FILE"] = cert;
            psi.Environment["REQUESTS_CA_BUNDLE"] = cert;
        }

        Log($"{DateTime.Now:yyyy-MM-dd HH:mm:ss} starting backend: {python} -u {serve} (cwd {baseDir})");
        var proc = Process.Start(psi) ?? throw new InvalidOperationException("Could not start the dashboard runtime.");
        _backend = proc;
        proc.ErrorDataReceived += (_, args) =>
        {
            if (string.IsNullOrEmpty(args.Data)) return;
            Log(args.Data);
            _stderrTail.Enqueue(args.Data);
            while (_stderrTail.Count > 12 && _stderrTail.TryDequeue(out string? _))
            {
            }
        };
        proc.BeginErrorReadLine();

        var reader = Task.Run(async () =>
        {
            string? port = null;
            string? token = null;
            while (port == null || token == null)
            {
                var line = await proc.StandardOutput.ReadLineAsync();
                if (line == null) break;
                line = line.Trim();
                if (line.StartsWith("PORT ", StringComparison.Ordinal)) port = line[5..].Trim();
                else if (line.StartsWith("TOKEN ", StringComparison.Ordinal)) token = line[6..].Trim();
            }
            return (port, token);
        });
        var finished = await Task.WhenAny(reader, Task.Delay(timeout));
        if (finished != reader)
        {
            StopBackend();
            return new BootResult(false, "", "", $"The dashboard runtime did not report its port within {(int)timeout.TotalSeconds} s.{StderrHint()}");
        }
        var (portText, tokenText) = await reader;
        // Keep draining stdout so the backend never blocks on a full pipe.
        _ = Task.Run(async () =>
        {
            try
            {
                while (await proc.StandardOutput.ReadLineAsync() != null)
                {
                }
            }
            catch (Exception)
            {
            }
        });
        if (portText == null || tokenText == null)
        {
            string code = "";
            try
            {
                if (proc.WaitForExit(2000)) code = $" (exit code {proc.ExitCode})";
            }
            catch (Exception)
            {
            }
            StopBackend();
            return new BootResult(false, "", "", $"The dashboard runtime stopped before it was ready{code}.{StderrHint()}");
        }
        if (!int.TryParse(portText, out var portNum) || portNum is < 1 or > 65535 || tokenText.Length == 0)
        {
            StopBackend();
            return new BootResult(false, "", "", $"The dashboard runtime sent an invalid port \"{portText}\".");
        }
        Log($"backend ready on 127.0.0.1:{portNum}");
        return new BootResult(true, portNum.ToString(), tokenText, "");
    }

    private string StderrHint()
    {
        var lines = _stderrTail.ToArray();
        return lines.Length == 0 ? "" : "\n\n" + string.Join("\n", lines.TakeLast(8));
    }

    private static string ErrorPage(string reason)
    {
        var text = System.Net.WebUtility.HtmlEncode(string.IsNullOrWhiteSpace(reason) ? "Unknown error." : reason.Trim());
        return """
<!doctype html><html><head><meta charset="utf-8"><title>Laden Ops</title><style>
html,body{margin:0;height:100%;background:#101418;color:#e8edf2;font:14px -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif}
main{box-sizing:border-box;height:100%;padding:48px 44px;display:flex;flex-direction:column;gap:14px}
h1{margin:0;font-size:20px;font-weight:600}
pre{margin:0;padding:14px;background:#1a2027;border-radius:10px;white-space:pre-wrap;word-break:break-word;font:12px ui-monospace,Menlo,monospace;max-height:50vh;overflow:auto}
.row{display:flex;gap:10px}
button{font:inherit;padding:8px 18px;border-radius:8px;border:1px solid #3a4652;background:#24303b;color:#e8edf2;cursor:pointer}
button.primary{background:#2f6fdf;border-color:#2f6fdf}
small{color:#9aa7b4}
</style></head><body><main>
<h1>Laden Ops could not start its dashboard</h1>
<pre>
""" + text + """
</pre>
<small>Details are in ~/Library/Application Support/laden-ops/serve.log</small>
<div class="row"><button class="primary" onclick="send('laden:retry')">Try again</button><button onclick="send('laden:quit')">Quit</button></div>
</main><script>
function send(m){try{window.external.sendMessage(m)}catch(e){}}
</script></body></html>
""";
    }

    // /ctl is a 20 s long poll. Each request gets its own 60 s deadline; a
    // timeout (slow, napped or busy backend) only retries. In v4 the HttpClient
    // timeout surfaced as TaskCanceledException, which the loop took for
    // "shutting down": it returned and the board's Hide/Quit stopped working.
    // The poll passes ?after=<last event id> so the backend never hands an
    // event to an abandoned (timed-out) request and loses it.
    internal static TimeSpan ControlRequestTimeout = TimeSpan.FromSeconds(60);

    private async Task ControlLoop(CancellationToken cancel)
    {
        var failures = 0;
        long after = 0;
        string boot = "";
        while (!cancel.IsCancellationRequested && _http != null)
        {
            try
            {
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel);
                deadline.CancelAfter(ControlRequestTimeout);
                using var res = await _http.GetAsync($"{_origin}/ctl?after={after}", deadline.Token);
                var json = await res.Content.ReadAsStringAsync(deadline.Token);
                failures = 0;
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;
                var ev = root.GetProperty("event").GetString();
                if (root.TryGetProperty("boot", out var b) && b.GetString() is { } bootId && bootId != boot)
                {
                    // A new backend (Try again) numbers its events from 1 again.
                    if (boot.Length > 0 && ev == "timeout") after = 0;
                    boot = bootId;
                }
                if (root.TryGetProperty("id", out var id) && id.TryGetInt64(out var eventId) && ev != "timeout")
                    after = Math.Max(after, eventId);
                if (ev == "hide")
                {
                    Log($"{DateTime.Now:HH:mm:ss} hide (board)");
                    OnMain(HideWindow);
                }
                else if (ev == "snap")
                {
                    OnMain(ApplyWindowSnap);
                }
                else if (ev == "quit")
                {
                    OnMain(() => RequestQuit("board Quit"));
                    return;
                }
            }
            catch (OperationCanceledException) when (cancel.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                // Includes a request timeout. Keep polling; log the first few.
                if (++failures <= 3 || failures % 50 == 0)
                    Log($"{DateTime.Now:HH:mm:ss} control poll: {ex.GetType().Name} ({failures})");
                try { await Task.Delay(failures > 5 ? 2000 : 400, cancel); }
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
        StopBackend();
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
        try
        {
            Program.Current?.Toggle();
        }
        catch (Exception ex)
        {
            Program.Current?.LogLine("hotkey: " + ex.Message);
        }
        return 0;
    }

    private void Toggle()
    {
        var hidden = IsHidden();
        Log($"{DateTime.Now:HH:mm:ss} {(hidden ? "show" : "hide")} (hotkey)");
        if (hidden) ShowWindow();
        else HideWindow();
    }

    private void ShowPending()
    {
        if (_pendingShow) ShowWindow();
        _pendingShow = false;
    }

    // Hide/show the whole app ([NSApp hide:] / [NSApp unhide:]) instead of
    // ordering the only window out. Photino's AppDelegate answers YES to
    // applicationShouldTerminateAfterLastWindowClosed:, so AppKit ended the run
    // loop shortly after orderOut: of the last visible window and the app quit.
    private static void ShowWindow()
    {
        var app = SharedApp();
        if (app == IntPtr.Zero) return;
        if (SendBool(app, Sel("isHidden")))
            Send(app, Sel("unhide:"), IntPtr.Zero);
        var win = MainWindow(app);
        if (win != IntPtr.Zero)
        {
            if (SendBool(win, Sel("isMiniaturized")))
                Send(win, Sel("deminiaturize:"), IntPtr.Zero);
            Send(win, Sel("makeKeyAndOrderFront:"), IntPtr.Zero);
        }
        Program.Current?.ApplyWindowSnap();
        SendBoolArg(app, Sel("activateIgnoringOtherApps:"), 1);
    }

    private static void HideWindow()
    {
        var app = SharedApp();
        if (app == IntPtr.Zero) return;
        Send(app, Sel("hide:"), IntPtr.Zero);
    }

    // "Hidden" for the toggle: app hidden, window not on screen, minimized, or
    // the app is not frontmost (then Ctrl+` brings it forward instead of hiding).
    private static bool IsHidden()
    {
        var app = SharedApp();
        if (app == IntPtr.Zero) return true;
        if (SendBool(app, Sel("isHidden"))) return true;
        if (!SendBool(app, Sel("isActive"))) return true;
        var win = MainWindow(app);
        if (win == IntPtr.Zero) return true;
        if (SendBool(win, Sel("isMiniaturized"))) return true;
        return !SendBool(win, Sel("isVisible"));
    }

    private static void RememberWindow()
    {
        if (_nsWindow != IntPtr.Zero) return;
        var app = SharedApp();
        var wins = app == IntPtr.Zero ? IntPtr.Zero : SendId(app, Sel("windows"));
        if (wins != IntPtr.Zero && SendULong(wins, Sel("count")) > 0)
            _nsWindow = SendIndex(wins, Sel("objectAtIndex:"), 0);
    }

    // Keep the app alive when its window is hidden or closed while the board is
    // up; quit only via the board's Quit, Cmd+Q, or from the error page.
    private static void InstallLifecycle()
    {
        try
        {
            var app = SharedApp();
            var del = SendId(app, Sel("delegate"));
            if (del != IntPtr.Zero)
            {
                var cls = object_getClass(del);
                class_replaceMethod(cls, Sel("applicationShouldTerminateAfterLastWindowClosed:"),
                    Marshal.GetFunctionPointerForDelegate(TerminateAfterLastThunk), "c@:@");
            }
            var win = _nsWindow != IntPtr.Zero ? _nsWindow : MainWindow(app);
            var wdel = win == IntPtr.Zero ? IntPtr.Zero : SendId(win, Sel("delegate"));
            if (wdel != IntPtr.Zero)
            {
                class_replaceMethod(object_getClass(wdel), Sel("windowShouldClose:"),
                    Marshal.GetFunctionPointerForDelegate(ShouldCloseThunk), "c@:@");
            }
        }
        catch (Exception ex)
        {
            Program.Current?.Log("lifecycle: " + ex.Message);
        }
    }

    internal bool KeepAlive => !_quitting && _backendOk;

    private static byte OnShouldTerminateAfterLastWindowClosed(IntPtr self, IntPtr cmd, IntPtr sender)
    {
        try
        {
            return Program.Current is { KeepAlive: true } ? (byte)0 : (byte)1;
        }
        catch (Exception)
        {
            return 0;
        }
    }

    // Close button / performClose: hides while the board is up.
    private static byte OnWindowShouldClose(IntPtr self, IntPtr cmd, IntPtr sender)
    {
        try
        {
            if (Program.Current is { KeepAlive: true } shell)
            {
                shell.Log($"{DateTime.Now:HH:mm:ss} hide (close button)");
                HideWindow();
                return 0;
            }
        }
        catch (Exception)
        {
        }
        return 1;
    }

    // Board Quit, error page Quit, the /ctl quit event and Cmd+Q all end here.
    internal void RequestQuit(string source)
    {
        MarkQuitting(source);
        _window?.Close();
    }

    internal void MarkQuitting(string source)
    {
        _quitting = true;
        ExitReason = source;
        Log($"{DateTime.Now:yyyy-MM-dd HH:mm:ss} quit requested ({source})");
    }

    // NSActivityUserInitiatedAllowingIdleSystemSleep: no App Nap and no automatic
    // or sudden termination while the app runs (also while it is hidden). The Mac
    // may still sleep. Info.plist sets LSAppNapIsDisabled as well.
    private const ulong ActivityUserInitiatedAllowingIdleSystemSleep = 0x00FFFFFFUL & ~(1UL << 20);

    private void DisableAppNap()
    {
        try
        {
            if (_activity != IntPtr.Zero) return;
            var info = SendId(objc_getClass("NSProcessInfo"), Sel("processInfo"));
            if (info == IntPtr.Zero) return;
            var token = objc_msgSend_activity(info, Sel("beginActivityWithOptions:reason:"),
                ActivityUserInitiatedAllowingIdleSystemSleep, NsString("Laden Ops dashboard and backend"));
            // The token is autoreleased; keep it for the life of the process.
            if (token != IntPtr.Zero) _activity = SendId(token, Sel("retain"));
            Log($"{DateTime.Now:yyyy-MM-dd HH:mm:ss} app nap disabled: {_activity != IntPtr.Zero}");
        }
        catch (Exception ex)
        {
            Log("app nap: " + ex.Message);
        }
    }

    // Standard menu bar: Laden Ops (About, Hide, Hide Others, Show All, Quit),
    // Edit (copy/paste for the web view) and Window (Minimize, Close). Close
    // (Cmd+W) hides like the close button; Quit (Cmd+Q) quits like the board.
    private const nuint ModCommand = 1 << 20;
    private const nuint ModOption = 1 << 19;
    private const nuint ModShift = 1 << 17;

    private static void InstallMenu()
    {
        try
        {
            var app = SharedApp();
            if (app == IntPtr.Zero) return;
            var target = MenuTarget();
            var bar = NewMenu("");

            var appMenu = NewMenu("Laden Ops");
            AddItem(appMenu, "About Laden Ops", "orderFrontStandardAboutPanel:", "", 0, IntPtr.Zero);
            AddSeparator(appMenu);
            AddItem(appMenu, "Hide Laden Ops", "hide:", "h", ModCommand, IntPtr.Zero);
            AddItem(appMenu, "Hide Others", "hideOtherApplications:", "h", ModCommand | ModOption, IntPtr.Zero);
            AddItem(appMenu, "Show All", "unhideAllApplications:", "", 0, IntPtr.Zero);
            AddSeparator(appMenu);
            AddItem(appMenu, "Quit Laden Ops", "ladenQuit:", "q", ModCommand, target);
            AddSubmenu(bar, "Laden Ops", appMenu);

            var edit = NewMenu("Edit");
            AddItem(edit, "Undo", "undo:", "z", ModCommand, IntPtr.Zero);
            AddItem(edit, "Redo", "redo:", "z", ModCommand | ModShift, IntPtr.Zero);
            AddSeparator(edit);
            AddItem(edit, "Cut", "cut:", "x", ModCommand, IntPtr.Zero);
            AddItem(edit, "Copy", "copy:", "c", ModCommand, IntPtr.Zero);
            AddItem(edit, "Paste", "paste:", "v", ModCommand, IntPtr.Zero);
            AddItem(edit, "Select All", "selectAll:", "a", ModCommand, IntPtr.Zero);
            AddSubmenu(bar, "Edit", edit);

            var window = NewMenu("Window");
            AddItem(window, "Minimize", "miniaturize:", "m", ModCommand, IntPtr.Zero);  // performMiniaturize: beeps without a title-bar button
            AddSeparator(window);
            AddItem(window, "Close", "ladenClose:", "w", ModCommand, target);
            AddSubmenu(bar, "Window", window);

            Send(app, Sel("setMainMenu:"), bar);
            Send(app, Sel("setWindowsMenu:"), window);
        }
        catch (Exception ex)
        {
            Program.Current?.Log("menu: " + ex.Message);
        }
    }

    private static IntPtr MenuTarget()
    {
        if (_menuTarget != IntPtr.Zero) return _menuTarget;
        var cls = objc_getClass("LadenMenuTarget");
        if (cls == IntPtr.Zero)
        {
            cls = objc_allocateClassPair(objc_getClass("NSObject"), "LadenMenuTarget", 0);
            if (cls == IntPtr.Zero) return IntPtr.Zero;
            class_addMethod(cls, Sel("ladenClose:"), Marshal.GetFunctionPointerForDelegate(MenuCloseThunk), "v@:@");
            class_addMethod(cls, Sel("ladenQuit:"), Marshal.GetFunctionPointerForDelegate(MenuQuitThunk), "v@:@");
            objc_registerClassPair(cls);
        }
        // Never released: the menu items only hold a weak reference to their target.
        _menuTarget = SendId(SendId(cls, Sel("alloc")), Sel("init"));
        return _menuTarget;
    }

    private static IntPtr NewMenu(string title) =>
        SendPtr(SendId(objc_getClass("NSMenu"), Sel("alloc")), Sel("initWithTitle:"), NsString(title));

    private static IntPtr AddItem(IntPtr menu, string title, string? action, string key, nuint mods, IntPtr target)
    {
        var item = objc_msgSend_id3(SendId(objc_getClass("NSMenuItem"), Sel("alloc")),
            Sel("initWithTitle:action:keyEquivalent:"), NsString(title),
            action == null ? IntPtr.Zero : Sel(action), NsString(key));
        if (item == IntPtr.Zero) return IntPtr.Zero;
        if (key.Length > 0) SendNUInt(item, Sel("setKeyEquivalentModifierMask:"), mods);
        if (target != IntPtr.Zero) Send(item, Sel("setTarget:"), target);
        Send(menu, Sel("addItem:"), item);
        return item;
    }

    private static void AddSeparator(IntPtr menu) =>
        Send(menu, Sel("addItem:"), SendId(objc_getClass("NSMenuItem"), Sel("separatorItem")));

    private static void AddSubmenu(IntPtr bar, string title, IntPtr submenu)
    {
        var item = AddItem(bar, title, null, "", 0, IntPtr.Zero);
        if (item != IntPtr.Zero) Send(item, Sel("setSubmenu:"), submenu);
    }

    // Window > Close (Cmd+W): hide while the board is up, like the close button.
    private static void OnMenuClose(IntPtr self, IntPtr cmd, IntPtr sender)
    {
        try
        {
            var shell = Program.Current;
            if (shell is { KeepAlive: true })
            {
                shell.Log($"{DateTime.Now:HH:mm:ss} hide (Cmd+W)");
                HideWindow();
            }
            else
            {
                shell?.RequestQuit("Cmd+W on the error page");
            }
        }
        catch (Exception)
        {
        }
    }

    // Quit (Cmd+Q): mark the quit (so nothing turns it into a hide), then
    // terminate: exactly like Photino's own Quit item did in v4.
    private static void OnMenuQuit(IntPtr self, IntPtr cmd, IntPtr sender)
    {
        try
        {
            Program.Current?.MarkQuitting("Cmd+Q");
        }
        catch (Exception)
        {
        }
        try
        {
            var app = SharedApp();
            if (app != IntPtr.Zero) Send(app, Sel("terminate:"), IntPtr.Zero);
        }
        catch (Exception)
        {
        }
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
            // Opaque dark fill — clear/transparent windows smear into banding over RustDesk.
            SendBoolArg(win, Sel("setOpaque:"), 1);
            SendBoolArg(win, Sel("setHasShadow:"), 1);
            var fill = SendId(objc_getClass("NSColor"), Sel("blackColor"));
            if (fill != IntPtr.Zero) Send(win, Sel("setBackgroundColor:"), fill);
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
        try
        {
            var window = SendId(self, Sel("window"));
            if (window != IntPtr.Zero) Send(window, Sel("performWindowDragWithEvent:"), evt);
        }
        catch (Exception)
        {
        }
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
        try
        {
            Program.Current?.Log($"{DateTime.Now:HH:mm:ss} show (Dock)");
            ShowWindow();
        }
        catch (Exception)
        {
        }
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

    private void SaveSize(int w, int h)
    {
        if (w < 680 || h < 460) return;
        _width = w;
        _height = h;
        try
        {
            var snap = ReadSnap();
            File.WriteAllText(_windowFile, "{\"w\": " + w + ", \"h\": " + h + ", \"snap\": \"" + snap + "\"}\n");
        }
        catch (IOException)
        {
        }
    }

    private void ApplyWindowSnap()
    {
        var snap = ReadSnap();
        if (snap != "left" && snap != "right") return;
        try
        {
            RememberWindow();
            var win = _nsWindow != IntPtr.Zero ? _nsWindow : MainWindow(SharedApp());
            if (win == IntPtr.Zero || _window == null) return;
            var screen = SendId(win, Sel("screen"));
            if (screen == IntPtr.Zero)
                screen = SendId(objc_getClass("NSScreen"), Sel("mainScreen"));
            if (screen == IntPtr.Zero) return;
            var vis = VisibleFrame(screen);
            // Cocoa origin is bottom-left. Photino SetTop uses top-left style coordinates
            // matching window Location; Photino SetLeft/SetTop map to the content position.
            var half = Math.Max(680, (int)(vis.W / 2));
            var height = Math.Max(460, (int)vis.H);
            var left = snap == "left" ? (int)vis.X : (int)(vis.X + vis.W - half);
            // Convert Cocoa Y (bottom-left) to top-left for Photino SetTop.
            var screenHeight = vis.Y + vis.H; // top of visible frame in Cocoa coords is vis.Y+vis.H from bottom of screen... 
            // Photino's SetTop is the distance from the top of the screen.
            // visibleFrame.Y is dock height from bottom; top offset from screen top:
            // screen.frame height - (vis.Y + vis.H) = menu bar region.
            var full = FrameOf(screen);
            var topFromScreenTop = (int)((full.Y + full.H) - (vis.Y + vis.H));
            _window.SetSize(half, height);
            _window.SetLeft(left);
            _window.SetTop(Math.Max(0, topFromScreenTop));
            _width = half;
            _height = height;
            try
            {
                File.WriteAllText(_windowFile, "{\"w\": " + half + ", \"h\": " + height + ", \"snap\": \"" + snap + "\"}\n");
            }
            catch (IOException)
            {
            }
        }
        catch (Exception ex)
        {
            Log("snap: " + ex.Message);
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NsRect
    {
        public double X;
        public double Y;
        public double W;
        public double H;
    }

    private static NsRect VisibleFrame(IntPtr screen) => MsgSendRect(screen, Sel("visibleFrame"));
    private static NsRect FrameOf(IntPtr screen) => MsgSendRect(screen, Sel("frame"));

    private static NsRect MsgSendRect(IntPtr recv, IntPtr sel)
    {
        // arm64 and modern x64 return CGRect in registers; use a typed objc_msgSend.
        return objc_msgSend_rect(recv, sel);
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

    // dispatch_get_main_queue() is a macro for &_dispatch_main_q; it is not an
    // exported function on any macOS. Resolve the data symbol once instead.
    private static IntPtr _mainQueue;

    private static IntPtr MainQueue()
    {
        if (_mainQueue != IntPtr.Zero) return _mainQueue;
        var lib = NativeLibrary.Load(LibSystem);
        _mainQueue = NativeLibrary.GetExport(lib, "_dispatch_main_q");
        return _mainQueue;
    }

    internal static Action<Action>? TestDispatcher = null;

    private static void OnMain(Action action)
    {
        if (TestDispatcher != null)
        {
            TestDispatcher(() => RunSafely(action));
            return;
        }
        try
        {
            if (pthread_main_np() == 1)
            {
                RunSafely(action);
                return;
            }
            var handle = GCHandle.Alloc(action);
            try
            {
                dispatch_async_f(MainQueue(), GCHandle.ToIntPtr(handle), Marshal.GetFunctionPointerForDelegate(MainThunk));
            }
            catch
            {
                handle.Free();
                throw;
            }
        }
        catch (Exception ex)
        {
            Program.Current?.LogLine("main-thread dispatch failed: " + ex.Message);
            try
            {
                Program.Current?.InvokeViaPhotino(action);
            }
            catch (Exception)
            {
            }
        }
    }

    internal void InvokeViaPhotino(Action action)
    {
        if (_window != null && !_quitting) _window.Invoke(() => RunSafely(action));
    }

    private static void RunSafely(Action action)
    {
        try
        {
            action();
        }
        catch (Exception ex)
        {
            Program.Current?.LogLine("main-thread action: " + ex);
        }
    }

    private static void OnMainThunk(IntPtr ctx)
    {
        try
        {
            var handle = GCHandle.FromIntPtr(ctx);
            try
            {
                if (handle.Target is Action action) RunSafely(action);
            }
            finally
            {
                handle.Free();
            }
        }
        catch (Exception)
        {
            // Never let an exception unwind into libdispatch.
        }
    }

    private static IntPtr SharedApp() => SendId(objc_getClass("NSApplication"), Sel("sharedApplication"));

    private static IntPtr MainWindow(IntPtr app)
    {
        if (_nsWindow != IntPtr.Zero) return _nsWindow;
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
    private delegate byte BoolIdHandler(IntPtr self, IntPtr cmd, IntPtr arg);
    private delegate void MenuActionHandler(IntPtr self, IntPtr cmd, IntPtr sender);
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

    [DllImport(ObjC, EntryPoint = "class_replaceMethod")]
    private static extern IntPtr class_replaceMethod(IntPtr cls, IntPtr sel, IntPtr imp, string types);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend(IntPtr recv, IntPtr sel);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern NsRect objc_msgSend_rect(IntPtr recv, IntPtr sel);

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

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend_id3(IntPtr recv, IntPtr sel, IntPtr a, IntPtr b, IntPtr c);

    [DllImport(ObjC, EntryPoint = "objc_msgSend")]
    private static extern IntPtr objc_msgSend_activity(IntPtr recv, IntPtr sel, ulong options, IntPtr reason);

    [DllImport(ObjC, EntryPoint = "objc_allocateClassPair")]
    private static extern IntPtr objc_allocateClassPair(IntPtr superclass, string name, nuint extraBytes);

    [DllImport(ObjC, EntryPoint = "objc_registerClassPair")]
    private static extern void objc_registerClassPair(IntPtr cls);

    private const string LibSystem = "/usr/lib/libSystem.B.dylib";

    [DllImport(LibSystem, EntryPoint = "dispatch_async_f")]
    private static extern void dispatch_async_f(IntPtr queue, IntPtr context, IntPtr work);

    [DllImport(LibSystem, EntryPoint = "pthread_main_np")]
    private static extern int pthread_main_np();

    [DllImport(Carbon)]
    private static extern IntPtr GetApplicationEventTarget();

    [DllImport(Carbon)]
    private static extern int InstallEventHandler(IntPtr target, CarbonHandler handler, nuint count, EventTypeSpec[] types, IntPtr user, out IntPtr installed);

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
