using System.Diagnostics;
using LibreHardwareMonitor.Hardware;

namespace LadenOps;

/// <summary>
/// One-shot elevated helper: opens LHM (installs kernel driver if needed) and writes sensors.json.
/// Invoked with a "sensors-once" arg from an elevation prompt.
/// </summary>
internal static class SensorDriverOnce
{
    public static bool TryHandle(string[] args)
    {
        if (args.All(a => !string.Equals(a, "sensors-once", StringComparison.OrdinalIgnoreCase)))
            return false;

        var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        var configDir = Path.Combine(appData, "laden-ops");
        Directory.CreateDirectory(configDir);
        using var monitor = new SensorsMonitor(configDir);
        // Run a few samples synchronously then exit.
        monitor.Start();
        Thread.Sleep(6000);
        monitor.Dispose();
        // Mark driver bootstrap attempted.
        try { File.WriteAllText(Path.Combine(configDir, "sensors-driver"), "1"); } catch { }
        return true;
    }

    public static void RequestIfNeeded(string configDir)
    {
        try
        {
            using (var id = System.Security.Principal.WindowsIdentity.GetCurrent())
            {
                var principal = new System.Security.Principal.WindowsPrincipal(id);
                if (principal.IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator))
                    return; // already elevated; SensorsMonitor in this process can open Ring0
            }
            var marker = Path.Combine(configDir, "sensors-driver");
            if (File.Exists(marker)) return;
            // If sensors already have a real CPU temp, skip.
            var sensors = Path.Combine(configDir, "sensors.json");
            if (File.Exists(sensors))
            {
                var txt = File.ReadAllText(sensors);
                if (txt.Contains("\"cpuTempC\":") && !txt.Contains("\"cpuTempC\":null") && !txt.Contains("\"cpuTempC\":0"))
                {
                    File.WriteAllText(marker, "1");
                    return;
                }
            }
            var exe = Environment.ProcessPath ?? throw new InvalidOperationException("no process path");
            var psi = new ProcessStartInfo(exe, "sensors-once")
            {
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            Process.Start(psi);
        }
        catch
        {
            // User canceled UAC — board keeps working, temps may stay empty until allowed.
            try { File.WriteAllText(Path.Combine(configDir, "sensors-driver"), "0"); } catch { }
        }
    }
}
