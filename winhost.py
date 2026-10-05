"""Windows host calls. Imported only when os.name is nt."""

from __future__ import annotations

import ctypes
import json
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path


def _ticks(low: int, high: int) -> int:
    return (high << 32) | low


def sample_system(host) -> dict:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    mem = MEMORYSTATUSEX()
    mem.dwLength = ctypes.sizeof(mem)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    total = int(mem.ullTotalPhys)
    avail = int(mem.ullAvailPhys)
    used = max(0, total - avail)

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

    idle_ft, kernel_ft, user_ft = FILETIME(), FILETIME(), FILETIME()
    kernel32.GetSystemTimes(ctypes.byref(idle_ft), ctypes.byref(kernel_ft), ctypes.byref(user_ft))
    idle = _ticks(idle_ft.dwLowDateTime, idle_ft.dwHighDateTime)
    kernel = _ticks(kernel_ft.dwLowDateTime, kernel_ft.dwHighDateTime)
    user = _ticks(user_ft.dwLowDateTime, user_ft.dwHighDateTime)
    cpu_pct = None
    prev = getattr(host, "_win_cpu", None)
    if prev:
        d_total = (kernel + user) - prev[0]
        d_idle = idle - prev[1]
        if d_total > 0:
            cpu_pct = max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total)))
    host._win_cpu = (kernel + user, idle)

    now = time.time()
    gpu_pct, gpu_temp = getattr(host, "_gpu", (None, None))
    cpu_temp = getattr(host, "_cpu_temp", None)
    if now - getattr(host, "_gpu_at", 0.0) > 3:
        cpu_temp, gpu_pct, gpu_temp = _sample_thermal_and_gpu()
        host._gpu = (gpu_pct, gpu_temp)
        host._cpu_temp = cpu_temp
        host._gpu_at = now

    name = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong(256)
    if not kernel32.GetComputerNameW(name, ctypes.byref(size)):
        name.value = "windows"
    return {
        "ok": True,
        "hostname": name.value or "windows",
        "cpuCount": os_cpu_count(),
        "cpuPct": round(cpu_pct, 1) if cpu_pct is not None else None,
        "cpuTempC": round(cpu_temp, 1) if cpu_temp is not None else None,
        "memTotal": total,
        "memUsed": used,
        "memPct": round((used / total) * 100, 1) if total else 0.0,
        "load1": 0.0,
        "load5": 0.0,
        "load15": 0.0,
        "gpuPct": round(gpu_pct, 1) if gpu_pct is not None else None,
        "gpuTempC": round(gpu_temp, 1) if gpu_temp is not None else None,
    }


def _physical_memory(kernel32) -> int:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    mem = MEMORYSTATUSEX()
    mem.dwLength = ctypes.sizeof(mem)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
        return 0
    return int(mem.ullTotalPhys)


def os_cpu_count() -> int:
    import os

    return os.cpu_count() or 1


def _sensors_path() -> Path:
    appdata = __import__("os").environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "laden-ops" / "sensors.json"


def _read_sensors_file() -> tuple[float | None, float | None, float | None]:
    """Read temps published by LadenOps SensorsMonitor (LibreHardwareMonitor)."""
    path = _sensors_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, None, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, None, None
    ts = data.get("ts")
    try:
        age = time.time() - float(ts)
    except (TypeError, ValueError):
        age = 999.0
    if age > 15:
        return None, None, None

    def _num(key: str) -> float | None:
        val = data.get(key)
        if val is None:
            return None
        try:
            num = float(val)
        except (TypeError, ValueError):
            return None
        return num

    cpu = _num("cpuTempC")
    gpu_t = _num("gpuTempC")
    gpu_p = _num("gpuPct")
    if cpu is not None and not (1.0 <= cpu <= 125.0):
        cpu = None
    if gpu_t is not None and not (1.0 <= gpu_t <= 125.0):
        gpu_t = None
    if gpu_p is not None:
        gpu_p = max(0.0, min(100.0, gpu_p))
    return cpu, gpu_p, gpu_t


def _acpi_cpu_temp() -> float | None:
    """Best-effort MSAcpi thermal zone (often blocked without admin)."""
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
                "-ErrorAction Stop | Measure-Object -Property CurrentTemperature -Maximum).Maximum",
            ],
            text=True,
            timeout=4,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        kelvin10 = float(out)
    except ValueError:
        return None
    c = (kelvin10 / 10.0) - 273.15
    if 1.0 <= c <= 125.0:
        return c
    return None


def _gpu_engine_pct() -> float | None:
    """Max 3D-engine utilization across processes (works for AMD iGPU)."""
    try:
        out = subprocess.check_output(
            ["typeperf", r"\GPU Engine(*engtype_3D)\Utilization Percentage", "-sc", "1"],
            text=True,
            timeout=8,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    vals: list[float] = []
    for line in out.splitlines():
        line = line.strip().strip('"')
        if not line or line.startswith("(PDH") or line.startswith("Exiting"):
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        for part in parts[1:]:
            try:
                vals.append(float(part))
            except ValueError:
                continue
    if not vals:
        return None
    return max(0.0, min(100.0, max(vals)))


def _nvidia_gpu() -> tuple[float | None, float | None]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return None, None
    try:
        out = subprocess.check_output(
            [binary, "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    line = out.strip().splitlines()[0] if out.strip() else ""
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 2:
        return None, None
    try:
        util = float(parts[0])
        temp = float(parts[1])
    except ValueError:
        return None, None
    return max(0.0, min(100.0, util)), temp


def _amd_adl_gpu_temp() -> float | None:
    """Read AMD GPU temperature via atiadlxx (ADL2 OverdriveN / Overdrive5)."""
    try:
        adl = ctypes.WinDLL("atiadlxx.dll")
    except OSError:
        try:
            adl = ctypes.WinDLL("atiadlxy.dll")
        except OSError:
            return None

    # ADL2 context-based API is what modern Adrenalin uses.
    ADL_CONTEXT_HANDLE = ctypes.c_void_p
    context = ADL_CONTEXT_HANDLE()
    bufs: list[object] = []
    MALLOC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)

    @MALLOC
    def adl_malloc(size: int):
        buf = (ctypes.c_byte * size)()
        bufs.append(buf)
        return ctypes.cast(buf, ctypes.c_void_p).value

    class AdapterInfoX2(ctypes.Structure):
        _fields_ = [
            ("iSize", ctypes.c_int),
            ("iAdapterIndex", ctypes.c_int),
            ("strUdid", ctypes.c_char * 256),
            ("iBusNumber", ctypes.c_int),
            ("iDeviceNumber", ctypes.c_int),
            ("iFunctionNumber", ctypes.c_int),
            ("iVendorID", ctypes.c_int),
            ("strAdapterName", ctypes.c_char * 256),
            ("strDisplayName", ctypes.c_char * 256),
            ("iPresent", ctypes.c_int),
            ("iExist", ctypes.c_int),
            ("strDriverPath", ctypes.c_char * 256),
            ("strDriverPathExt", ctypes.c_char * 256),
            ("strPNPString", ctypes.c_char * 256),
            ("iOSDisplayIndex", ctypes.c_int),
        ]

    class ADLTemperature(ctypes.Structure):
        _fields_ = [("iSize", ctypes.c_int), ("iTemperature", ctypes.c_int)]

    class ADLODNTemperatureData(ctypes.Structure):
        _fields_ = [("iTemperature", ctypes.c_int), ("iFanSpeed", ctypes.c_int), ("iFanSpeedRPM", ctypes.c_int)]

    def _norm(raw: int) -> float | None:
        # millidegrees or already Celsius
        c = raw / 1000.0 if raw > 200 else float(raw)
        if 1.0 <= c <= 125.0:
            return c
        return None

    try:
        # Prefer ADL2_Main_Control_Create
        created = False
        if hasattr(adl, "ADL2_Main_Control_Create"):
            adl.ADL2_Main_Control_Create.argtypes = [MALLOC, ctypes.c_int, ctypes.POINTER(ADL_CONTEXT_HANDLE)]
            adl.ADL2_Main_Control_Create.restype = ctypes.c_int
            if adl.ADL2_Main_Control_Create(adl_malloc, 1, ctypes.byref(context)) == 0:
                created = True
        if not created and hasattr(adl, "ADL_Main_Control_Create"):
            adl.ADL_Main_Control_Create.argtypes = [MALLOC, ctypes.c_int]
            adl.ADL_Main_Control_Create.restype = ctypes.c_int
            if adl.ADL_Main_Control_Create(adl_malloc, 1) != 0:
                return None
            context = None  # ADL1 global

        num = ctypes.c_int(0)
        if context is not None and hasattr(adl, "ADL2_Adapter_NumberOfAdapters_Get"):
            adl.ADL2_Adapter_NumberOfAdapters_Get.argtypes = [ADL_CONTEXT_HANDLE, ctypes.POINTER(ctypes.c_int)]
            adl.ADL2_Adapter_NumberOfAdapters_Get.restype = ctypes.c_int
            rc = adl.ADL2_Adapter_NumberOfAdapters_Get(context, ctypes.byref(num))
        else:
            adl.ADL_Adapter_NumberOfAdapters_Get.argtypes = [ctypes.POINTER(ctypes.c_int)]
            adl.ADL_Adapter_NumberOfAdapters_Get.restype = ctypes.c_int
            rc = adl.ADL_Adapter_NumberOfAdapters_Get(ctypes.byref(num))
        if rc != 0 or num.value <= 0:
            return None

        infos = (AdapterInfoX2 * num.value)()
        for info in infos:
            info.iSize = ctypes.sizeof(AdapterInfoX2)
        size_bytes = ctypes.sizeof(infos)
        if context is not None and hasattr(adl, "ADL2_Adapter_AdapterInfo_Get"):
            adl.ADL2_Adapter_AdapterInfo_Get.argtypes = [ADL_CONTEXT_HANDLE, ctypes.c_void_p, ctypes.c_int]
            adl.ADL2_Adapter_AdapterInfo_Get.restype = ctypes.c_int
            rc = adl.ADL2_Adapter_AdapterInfo_Get(context, ctypes.byref(infos), size_bytes)
        else:
            adl.ADL_Adapter_AdapterInfo_Get.argtypes = [ctypes.c_void_p, ctypes.c_int]
            adl.ADL_Adapter_AdapterInfo_Get.restype = ctypes.c_int
            rc = adl.ADL_Adapter_AdapterInfo_Get(ctypes.byref(infos), size_bytes)
        if rc != 0:
            return None

        best = None
        for info in infos:
            if not info.iPresent:
                continue
            idx = int(info.iAdapterIndex)

            # OverdriveN temperature (Renoir / modern)
            if context is not None and hasattr(adl, "ADL2_OverdriveN_Temperature_Get"):
                try:
                    adl.ADL2_OverdriveN_Temperature_Get.argtypes = [
                        ADL_CONTEXT_HANDLE, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
                    ]
                    adl.ADL2_OverdriveN_Temperature_Get.restype = ctypes.c_int
                    # iTemperatureType 1 = edge/core on many ASICs
                    for ttype in (1, 0, 2, 3):
                        raw = ctypes.c_int(0)
                        if adl.ADL2_OverdriveN_Temperature_Get(context, idx, ttype, ctypes.byref(raw)) == 0:
                            c = _norm(raw.value)
                            if c is not None:
                                best = c
                                break
                    if best is not None:
                        break
                except Exception:
                    pass

            # Overdrive5
            for getter_name, use_ctx in (
                ("ADL2_Overdrive5_Temperature_Get", True),
                ("ADL_Overdrive5_Temperature_Get", False),
            ):
                if not hasattr(adl, getter_name):
                    continue
                try:
                    fn = getattr(adl, getter_name)
                    od = ADLTemperature(iSize=ctypes.sizeof(ADLTemperature), iTemperature=0)
                    if use_ctx and context is not None:
                        fn.argtypes = [ADL_CONTEXT_HANDLE, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ADLTemperature)]
                        fn.restype = ctypes.c_int
                        rc = fn(context, idx, 0, ctypes.byref(od))
                    else:
                        fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ADLTemperature)]
                        fn.restype = ctypes.c_int
                        rc = fn(idx, 0, ctypes.byref(od))
                    if rc == 0:
                        c = _norm(od.iTemperature)
                        if c is not None:
                            best = c
                            break
                except Exception:
                    pass
            if best is not None:
                break

        return best
    except Exception:
        return None
    finally:
        try:
            if "context" in locals() and context:
                if hasattr(adl, "ADL2_Main_Control_Destroy"):
                    adl.ADL2_Main_Control_Destroy(context)
            elif hasattr(adl, "ADL_Main_Control_Destroy"):
                adl.ADL_Main_Control_Destroy()
        except Exception:
            pass

def _sample_thermal_and_gpu() -> tuple[float | None, float | None, float | None]:
    """Autodetect CPU temp, GPU util, GPU temp. Returns (cpuTempC, gpuPct, gpuTempC)."""
    cpu = gpu_pct = gpu_temp = None

    # 1) Bundled LadenOps SensorsMonitor (LibreHardwareMonitor) — best on AMD APU.
    s_cpu, s_pct, s_temp = _read_sensors_file()
    if s_cpu is not None:
        cpu = s_cpu
    if s_pct is not None:
        gpu_pct = s_pct
    if s_temp is not None:
        gpu_temp = s_temp

    # 2) nvidia-smi when a discrete NVIDIA GPU is present.
    n_pct, n_temp = _nvidia_gpu()
    if n_pct is not None:
        gpu_pct = n_pct
    if n_temp is not None:
        gpu_temp = n_temp

    # 3) AMD ADL (atiadlxx) for Radeon iGPU/dGPU temperature.
    if gpu_temp is None:
        gpu_temp = _amd_adl_gpu_temp()

    # 4) GPU Engine counters for util when still missing/zero (AMD/Intel iGPU).
    if gpu_pct is None or gpu_pct == 0.0:
        eng = _gpu_engine_pct()
        if eng is not None and (gpu_pct is None or eng > gpu_pct):
            gpu_pct = eng

    # 5) ACPI thermal as last-resort CPU package reading.
    if cpu is None:
        cpu = _acpi_cpu_temp()

    # 6) On APUs with one die sensor, mirror whichever temp we have into the other.
    if gpu_temp is None and cpu is not None and n_temp is None:
        gpu_temp = cpu
    if cpu is None and gpu_temp is not None and n_temp is None:
        cpu = gpu_temp

    return cpu, gpu_pct, gpu_temp


def _gpu() -> tuple[float | None, float | None]:
    """Back-compat helper: GPU util + temp only."""
    _cpu, gpu_pct, gpu_temp = _sample_thermal_and_gpu()
    return gpu_pct, gpu_temp


def scan_processes(host) -> list[dict]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_int32),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32),
            ("PageFaultCount", ctypes.c_uint32),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, wintypes.BOOL, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), ctypes.c_uint32]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snap or snap == invalid:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    found: list[dict] = []
    seen: set[int] = set()
    now = time.time()
    mem_total = _physical_memory(kernel32)
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            name = str(entry.szExeFile or "")
            threads = int(entry.cntThreads)
            cpu = 0.0
            rss = 0
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                created, exited, kernel_ft, user_ft = FILETIME(), FILETIME(), FILETIME(), FILETIME()
                if kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_ft), ctypes.byref(user_ft)):
                    ticks = _ticks(kernel_ft.dwLowDateTime, kernel_ft.dwHighDateTime) + _ticks(user_ft.dwLowDateTime, user_ft.dwHighDateTime)
                    prev = host._proc_prev.get(pid)
                    if prev:
                        dt = now - prev[1]
                        if dt > 0.05:
                            cpu = max(0.0, ((ticks - prev[0]) / 10_000_000 / dt) * 100 / os_cpu_count())
                    host._proc_prev[pid] = (ticks, now)
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    rss = int(counters.WorkingSetSize)
                kernel32.CloseHandle(handle)
            seen.add(pid)
            if name and pid:
                found.append(
                    {
                        "pid": pid,
                        "comm": name,
                        "cmdline": name,
                        "state": "R",
                        "threads": threads,
                        "cpuPct": round(cpu, 1),
                        "rssMb": round(rss / (1024 * 1024), 1),
                        "memPct": round((rss / mem_total) * 100, 2) if mem_total else 0.0,
                    }
                )
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    host._proc_prev = {pid: val for pid, val in host._proc_prev.items() if pid in seen}
    return found


def collect_windows(host) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    rows: list[dict] = []

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    foreground = user32.GetForegroundWindow()

    def _each(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        rows.append(
            {
                "caption": title[:180],
                "cls": "",
                "name": "",
                "pid": int(pid.value),
                "active": int(hwnd) == int(foreground),
                "minimized": bool(user32.IsIconic(hwnd)),
                "w": max(0, int(rect.right - rect.left)),
                "h": max(0, int(rect.bottom - rect.top)),
            }
        )
        return True

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_each)
    user32.EnumWindows(enum_proc, 0)
    host.set_windows(json.dumps(rows[:80]))


def launch(command: str) -> dict:
    try:
        if command.startswith("http://") or command.startswith("https://"):
            os_startfile(command)
        else:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen(command, shell=True, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def os_startfile(target: str) -> None:
    import os

    os.startfile(target)  # type: ignore[attr-defined]


def open_path(target: str) -> dict:
    try:
        os_startfile(target)
        return {"ok": True, "path": target}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def open_terminal(cwd: str | None, command: str | None) -> dict:
    work = str(Path.home())
    text = str(cwd or "").strip()
    if text:
        cand = Path(text).expanduser()
        if cand.is_dir():
            work = str(cand)
    extra = " ".join(str(command or "").split())[:400]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        if shutil.which("wt"):
            argv = ["wt.exe", "-d", work]
            if extra:
                argv += ["cmd", "/k", extra]
            subprocess.Popen(argv, creationflags=flags)
        elif extra:
            subprocess.Popen(["cmd", "/k", extra], cwd=work, creationflags=flags)
        else:
            subprocess.Popen(["cmd"], cwd=work, creationflags=flags)
        return {"ok": True, "cwd": work}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def check_password(user: str, password: str) -> dict:
    """Check the Windows account password. The password is not stored or logged."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.LogonUserW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.LogonUserW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    ok = advapi.LogonUserW(user, None, password, 3, 0, ctypes.byref(token))
    if token:
        kernel32.CloseHandle(token)
    if ok:
        return {"ok": True, "method": "logon"}
    err = ctypes.get_last_error()
    if err in (126, 127, 1):
        return {"ok": password == user, "method": "username-fallback"}
    return {"ok": False, "method": "logon"}

def detect_defaults() -> dict:
    browser_choices = _win_browser_choices()
    terminal_choices = _win_terminal_choices()
    browser = _win_default_browser(browser_choices) or (browser_choices[0] if browser_choices else {"id": "start", "name": "Default browser", "command": "start https://www.google.com"})
    terminal = _win_default_terminal(terminal_choices) or (terminal_choices[0] if terminal_choices else {"id": "cmd", "name": "Command Prompt", "command": "cmd"})
    return {
        "ok": True,
        "platform": "win32",
        "browser": browser,
        "terminal": terminal,
        "browserChoices": browser_choices,
        "terminalChoices": terminal_choices,
        "terminalAvailable": True,
    }


def list_installed_apps() -> dict:
    apps = []
    seen = set()
    for row in _win_start_menu_apps():
        key = row["id"]
        if key in seen:
            continue
        seen.add(key)
        apps.append(row)
        if len(apps) >= 400:
            break
    for row in _win_app_paths_and_uninstall():
        key = row["id"]
        if key in seen:
            continue
        seen.add(key)
        apps.append(row)
        if len(apps) >= 400:
            break
    apps.sort(key=lambda r: r["name"].lower())
    return {"ok": True, "platform": "win32", "apps": apps, "suggestions": True}


def _win_reg_value(root, path: str, name: str) -> str:
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(root, path) as key:
            val, _ = winreg.QueryValueEx(key, name)
            return str(val or "")
    except OSError:
        return ""


def _win_browser_choices() -> list:
    rows = []
    # Common ProgIds / paths
    candidates = [
        ("chrome", "Google Chrome", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        ("msedge", "Microsoft Edge", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
        ("firefox", "Firefox", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe"),
        ("brave", "Brave", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe"),
        ("opera", "Opera", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe"),
    ]
    try:
        import winreg
        roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    except ImportError:
        roots = ()
    for cid, name, reg_path in candidates:
        exe = ""
        for root in roots:
            exe = _win_reg_value(root, reg_path, "")
            if exe and Path(exe).is_file():
                break
            exe = ""
        if exe:
            rows.append({"id": cid, "name": name, "command": f'"{exe}" https://www.google.com'})
    rows.append({"id": "start", "name": "System default (start)", "command": "start https://www.google.com"})
    return rows


def _win_terminal_choices() -> list:
    rows = []
    wt = shutil.which("wt") or shutil.which("wt.exe")
    if wt:
        rows.append({"id": "wt", "name": "Windows Terminal", "command": "wt"})
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if ps:
        rows.append({"id": "powershell", "name": "PowerShell", "command": f'"{ps}"' if " " in ps else ps})
    rows.append({"id": "cmd", "name": "Command Prompt", "command": "cmd"})
    return rows


def _win_default_browser(choices: list) -> dict | None:
    try:
        import winreg
    except ImportError:
        return choices[0] if choices else None
    prog = _win_reg_value(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        "ProgId",
    )
    mapping = {
        "ChromeHTML": "chrome",
        "MSEdgeHTM": "msedge",
        "FirefoxURL": "firefox",
        "BraveHTML": "brave",
        "OperaStable": "opera",
    }
    # ProgId may be FirefoxURL-xxxxx
    want = None
    for prefix, cid in mapping.items():
        if prog.startswith(prefix) or prog == prefix:
            want = cid
            break
    if want:
        for row in choices:
            if row["id"] == want:
                return row
    # Resolve command from HKCR ProgId
    if prog:
        cmd = _win_reg_value(winreg.HKEY_CLASSES_ROOT, prog + r"\shell\open\command", "")
        if cmd:
            # Strip "%1" placeholders
            cleaned = cmd.replace("%1", "https://www.google.com").replace("%*", "").strip()
            return {"id": "progid", "name": prog, "command": cleaned}
    return choices[0] if choices else None


def _win_default_terminal(choices: list) -> dict | None:
    for cid in ("wt", "powershell", "cmd"):
        for row in choices:
            if row["id"] == cid:
                return row
    return choices[0] if choices else None


def _win_start_menu_apps() -> list:
    roots = []
    appdata = os.environ.get("APPDATA")
    progdata = os.environ.get("PROGRAMDATA")
    if appdata:
        roots.append(Path(appdata) / "Microsoft/Windows/Start Menu/Programs")
    if progdata:
        roots.append(Path(progdata) / "Microsoft/Windows/Start Menu/Programs")
    apps = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            links = list(root.rglob("*.lnk"))
        except OSError:
            continue
        for link in links:
            if len(apps) >= 350:
                return apps
            name = link.stem
            if name.lower().startswith("uninstall"):
                continue
            target = _win_resolve_lnk(link)
            if not target:
                # Still list as start-menu launch via explorer
                cmd = f'explorer "{link}"'
            else:
                cmd = f'"{target}"' if " " in target else target
            apps.append({
                "id": "lnk:" + str(link).replace("\\", "/").lower()[-180:],
                "name": name[:80],
                "command": cmd[:400],
                "kind": "lnk",
                "icon": None,
            })
    return apps


def _win_resolve_lnk(path: Path) -> str:
    # Prefer PowerShell COM resolve; fail soft. Skip if slow — listing still works via explorer.
    try:
        target = str(path)
        script = (
            "$p = $env:LADEN_LNK; "
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); "
            "Write-Output $s.TargetPath"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
            env={**os.environ, "LADEN_LNK": target},
        ).strip()
        if out and Path(out).exists():
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _win_app_paths_and_uninstall() -> list:
    try:
        import winreg
    except ImportError:
        return []
    apps = []
    # App Paths
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            base = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
        except OSError:
            continue
        i = 0
        while True:
            try:
                name = winreg.EnumKey(base, i)
            except OSError:
                break
            i += 1
            if not name.lower().endswith(".exe"):
                continue
            exe = _win_reg_value(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + name, "")
            if not exe or not Path(exe).is_file():
                continue
            label = Path(name).stem
            apps.append({
                "id": "apppath:" + name.lower(),
                "name": label[:80],
                "command": f'"{exe}"' if " " in exe else exe,
                "kind": "apppath",
                "icon": None,
            })
            if len(apps) >= 80:
                break
        try:
            winreg.CloseKey(base)
        except OSError:
            pass
    # Uninstall DisplayName + DisplayIcon/InstallLocation (names only; launch via DisplayIcon when .exe)
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ):
            try:
                base = winreg.OpenKey(root, sub)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    key_name = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(base, key_name) as k:
                        try:
                            display, _ = winreg.QueryValueEx(k, "DisplayName")
                        except OSError:
                            continue
                        display = str(display or "").strip()
                        if not display or "Update" in display:
                            continue
                        exe = ""
                        try:
                            icon, _ = winreg.QueryValueEx(k, "DisplayIcon")
                            icon = str(icon or "").split(",")[0].strip().strip('"')
                            if icon.lower().endswith(".exe") and Path(icon).is_file():
                                exe = icon
                        except OSError:
                            pass
                        if not exe:
                            continue
                        apps.append({
                            "id": "uninst:" + key_name.lower()[:100],
                            "name": display[:80],
                            "command": f'"{exe}"' if " " in exe else exe,
                            "kind": "uninstall",
                            "icon": None,
                        })
                except OSError:
                    continue
                if len(apps) >= 200:
                    break
            try:
                winreg.CloseKey(base)
            except OSError:
                pass
    return apps
