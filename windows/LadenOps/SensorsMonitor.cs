using System.Text.Json;
using LibreHardwareMonitor.Hardware;

namespace LadenOps;

/// <summary>
/// Polls CPU/GPU temps (and GPU load) via LibreHardwareMonitor and writes
/// %APPDATA%\laden-ops\sensors.json for the Python host to read.
/// Bundled with the app so a fresh install works with no extra setup.
/// </summary>
internal sealed class SensorsMonitor : IDisposable
{
    private readonly string _path;
    private readonly object _gate = new();
    private Computer? _computer;
    private CancellationTokenSource? _cts;
    private Task? _loop;
    private bool _opened;

    public SensorsMonitor(string configDir)
    {
        Directory.CreateDirectory(configDir);
        _path = Path.Combine(configDir, "sensors.json");
    }

    public void Start()
    {
        if (_cts != null) return;
        _cts = new CancellationTokenSource();
        _loop = Task.Run(() => RunAsync(_cts.Token));
    }

    public void Dispose()
    {
        try { _cts?.Cancel(); } catch { }
        try { _loop?.Wait(1500); } catch { }
        lock (_gate)
        {
            try { _computer?.Close(); } catch { }
            _computer = null;
            _opened = false;
        }
        _cts?.Dispose();
        _cts = null;
    }

    private async Task RunAsync(CancellationToken cancel)
    {
        WriteSample();
        while (!cancel.IsCancellationRequested)
        {
            try { await Task.Delay(2000, cancel); }
            catch (OperationCanceledException) { break; }
            WriteSample();
        }
    }

    private void WriteSample()
    {
        float? cpuTemp = null;
        float? gpuTemp = null;
        float? gpuLoad = null;
        string source = "none";
        try
        {
            EnsureOpen();
            lock (_gate)
            {
                if (_computer == null) return;
                // Multiple updates help AMD SMU/ADL populate after Open().
                for (var pass = 0; pass < 3; pass++)
                {
                    foreach (var hw in _computer.Hardware)
                        UpdateHardware(hw);
                }

                var cpuTemps = new List<(string name, float value)>();
                var gpuTemps = new List<(string name, float value)>();
                var gpuLoads = new List<(string name, float value)>();
                var hasGpuHw = false;

                foreach (var hw in _computer.Hardware)
                {
                    if (hw.HardwareType is HardwareType.GpuAmd or HardwareType.GpuNvidia or HardwareType.GpuIntel)
                        hasGpuHw = true;
                    Collect(hw, cpuTemps, gpuTemps, gpuLoads);
                }

                cpuTemp = PickCpuTemp(cpuTemps);
                gpuTemp = PickGpuTemp(gpuTemps);
                gpuLoad = PickGpuLoad(gpuLoads);
                if (cpuTemp != null || gpuTemp != null || gpuLoad != null)
                    source = "lhm";

                // AMD APU: if GPU temp missing, mirror CPU package (same die).
                if (gpuTemp == null && cpuTemp != null)
                {
                    gpuTemp = cpuTemp;
                    if (source == "lhm") source = "lhm-apu";
                }
            }
        }
        catch
        {
            source = "error";
        }

        // One-shot debug dump of every temp/load sensor (helps diagnose missing AMD readings).
        try { WriteDebugDump(); } catch { }

        try
        {
            var payload = new Dictionary<string, object?>
            {
                ["ts"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
                ["cpuTempC"] = Round1(cpuTemp),
                ["gpuTempC"] = Round1(gpuTemp),
                ["gpuPct"] = Round1(gpuLoad),
                ["source"] = source,
            };
            var json = JsonSerializer.Serialize(payload);
            var tmp = _path + ".tmp";
            File.WriteAllText(tmp, json);
            File.Copy(tmp, _path, overwrite: true);
            try { File.Delete(tmp); } catch { }
        }
        catch { }
    }


    private void WriteDebugDump()
    {
        var debugPath = Path.Combine(Path.GetDirectoryName(_path)!, "sensors-debug.json");
        try
        {
            if (File.Exists(debugPath) && DateTime.UtcNow - File.GetLastWriteTimeUtc(debugPath) < TimeSpan.FromSeconds(30))
                return;
        }
        catch { }

        var rows = new List<Dictionary<string, object?>>();
        lock (_gate)
        {
            if (_computer == null) return;
            void Walk(IHardware hw)
            {
                foreach (var s in hw.Sensors)
                {
                    if (s.SensorType is not (SensorType.Temperature or SensorType.Load or SensorType.Power))
                        continue;
                    rows.Add(new Dictionary<string, object?>
                    {
                        ["hw"] = hw.Name,
                        ["hwType"] = hw.HardwareType.ToString(),
                        ["sensor"] = s.Name,
                        ["type"] = s.SensorType.ToString(),
                        ["value"] = s.Value,
                    });
                }
                foreach (var sub in hw.SubHardware) Walk(sub);
            }
            foreach (var hw in _computer.Hardware) Walk(hw);
        }
        var json = JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["ts"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
            ["count"] = rows.Count,
            ["sensors"] = rows,
        });
        File.WriteAllText(debugPath, json);
    }

    private static double? Round1(float? v) => v is null ? null : Math.Round(v.Value, 1);

    private void EnsureOpen()
    {
        lock (_gate)
        {
            if (_opened && _computer != null) return;
            var computer = new Computer
            {
                IsCpuEnabled = true,
                IsGpuEnabled = true,
                IsMotherboardEnabled = true,
                IsMemoryEnabled = false,
                IsControllerEnabled = true,
                IsNetworkEnabled = false,
                IsStorageEnabled = false,
                IsBatteryEnabled = false,
                IsPsuEnabled = false,
            };
            computer.Open();
            _computer = computer;
            _opened = true;
        }
    }

    private static void UpdateHardware(IHardware hardware)
    {
        hardware.Update();
        foreach (var sub in hardware.SubHardware)
            UpdateHardware(sub);
    }

    private static void Collect(
        IHardware hardware,
        List<(string name, float value)> cpuTemps,
        List<(string name, float value)> gpuTemps,
        List<(string name, float value)> gpuLoads)
    {
        var isCpu = hardware.HardwareType == HardwareType.Cpu;
        var isGpu = hardware.HardwareType is HardwareType.GpuAmd or HardwareType.GpuNvidia or HardwareType.GpuIntel;
        var isBoard = hardware.HardwareType == HardwareType.Motherboard;

        foreach (var sensor in hardware.Sensors)
        {
            if (sensor.Value is not float value) continue;
            var name = sensor.Name ?? "";

            if (sensor.SensorType == SensorType.Temperature)
            {
                if (value is < 1f or > 125f) continue;
                if (isCpu || LooksLikeCpuTemp(name))
                    cpuTemps.Add((name, value));
                if (isGpu || LooksLikeGpuTemp(name))
                    gpuTemps.Add((name, value));
                // Catch-all for unnamed package sensors hanging off motherboard/superIO.
                if (!isCpu && !isGpu && !LooksLikeCpuTemp(name) && !LooksLikeGpuTemp(name) && isBoard)
                    cpuTemps.Add((name, value));
            }
            else if (sensor.SensorType == SensorType.Load && isGpu && LooksLikeGpuLoad(name))
            {
                if (value is >= 0f and <= 100f)
                    gpuLoads.Add((name, value));
            }
        }

        foreach (var sub in hardware.SubHardware)
            Collect(sub, cpuTemps, gpuTemps, gpuLoads);
    }

    private static bool LooksLikeCpuTemp(string name)
    {
        var n = name.ToLowerInvariant();
        return n.Contains("cpu") || n.Contains("tctl") || n.Contains("tdie") || n.Contains("package") || n.Contains("core");
    }

    private static bool LooksLikeGpuTemp(string name)
    {
        var n = name.ToLowerInvariant();
        return n.Contains("gpu") || n.Contains("radeon") || n.Contains("hot spot") || n.Contains("hotspot");
    }

    private static bool LooksLikeGpuLoad(string name)
    {
        var n = name.ToLowerInvariant();
        if (n.Contains("memory") || n.Contains("video") || n.Contains("bus")) return false;
        return n.Contains("core") || n.Contains("gpu") || n.Contains("d3d") || n.Contains("graphics") || n == "load";
    }

    private static float? PickCpuTemp(List<(string name, float value)> temps)
    {
        if (temps.Count == 0) return null;
        foreach (var key in new[] { "package", "tctl", "tdie", "average", "cpu" })
        {
            var hit = temps.Where(t => t.name.Contains(key, StringComparison.OrdinalIgnoreCase)).ToList();
            if (hit.Count > 0) return hit.Max(t => t.value);
        }
        return temps.Max(t => t.value);
    }

    private static float? PickGpuTemp(List<(string name, float value)> temps)
    {
        if (temps.Count == 0) return null;
        foreach (var key in new[] { "core", "gpu", "hot" })
        {
            var hit = temps.Where(t => t.name.Contains(key, StringComparison.OrdinalIgnoreCase)).ToList();
            if (hit.Count > 0) return hit.Max(t => t.value);
        }
        return temps.Max(t => t.value);
    }

    private static float? PickGpuLoad(List<(string name, float value)> loads)
    {
        if (loads.Count == 0) return null;
        // Prefer "GPU Core" / graphics core; otherwise max across 3D engines.
        var core = loads.Where(t => t.name.Equals("GPU Core", StringComparison.OrdinalIgnoreCase)
            || t.name.Contains("Graphics", StringComparison.OrdinalIgnoreCase)).ToList();
        if (core.Count > 0) return core.Max(t => t.value);
        var d3d = loads.Where(t => t.name.Contains("D3D 3D", StringComparison.OrdinalIgnoreCase)
            || t.name.Contains("3D", StringComparison.OrdinalIgnoreCase)).ToList();
        if (d3d.Count > 0) return d3d.Max(t => t.value);
        return loads.Max(t => t.value);
    }
}
