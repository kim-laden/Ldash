"""macOS host bridge. Imported only when sys.platform is darwin."""

from __future__ import annotations

import ctypes
import json
import os
import shlex
import shutil
import subprocess
import time


def sample_system(host: object) -> dict:
    """CPU, memory, load, and autodected CPU/GPU temperatures."""
    if not getattr(host, "_sensors_warmed", False):
        warm_sensors()
        host._sensors_warmed = True
    total_ticks, idle_ticks = _cpu_ticks()
    cpu_pct = None
    prev = getattr(host, "_cpu_prev", None)
    if prev and total_ticks:
        dt = total_ticks - prev[0]
        di = idle_ticks - prev[1]
        if dt > 0:
            cpu_pct = max(0.0, min(100.0, 100.0 * (1 - di / dt)))
    if total_ticks:
        host._cpu_prev = (total_ticks, idle_ticks)

    mem_total, mem_used = _memory()
    mem_pct = (mem_used / mem_total * 100) if mem_total else 0.0
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0
    # Same path as _sample_temps_and_gpu(): it caches for a couple of seconds
    # and holds the last good reading across transient sensor misses.
    cpu_temp, gpu_pct, gpu_temp = _sample_temps_and_gpu()
    host._temps = (cpu_temp, gpu_pct, gpu_temp)
    host._gpu = (gpu_pct, gpu_temp)
    host._gpu_at = time.time()
    return {
        "ok": True,
        "hostname": os.uname().nodename,
        "cpuCount": os.cpu_count() or 1,
        "cpuPct": round(cpu_pct, 1) if cpu_pct is not None else None,
        "cpuTempC": round(cpu_temp, 1) if cpu_temp is not None else None,
        "memTotal": mem_total,
        "memUsed": mem_used,
        "memPct": round(mem_pct, 1),
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
        "gpuPct": gpu_pct,
        "gpuTempC": round(gpu_temp, 1) if gpu_temp is not None else None,
        "sensorSource": _mac_sensors().sources(),
    }


def scan_processes(host: object) -> list[dict]:
    """Process rows for the monitor. Does not sample CPU totals or the GPU."""
    libproc = _libproc()
    now = time.time()
    mem_total = _sysctl_u64("hw.memsize") or 1
    buf = (ctypes.c_int * 8192)()
    nbytes = libproc.proc_listpids(1, 0, ctypes.byref(buf), ctypes.sizeof(buf))
    if nbytes <= 0:
        return []
    count = nbytes // ctypes.sizeof(ctypes.c_int)
    found: list[dict] = []
    seen: set[int] = set()
    prev_map: dict[int, tuple[int, float]] = getattr(host, "_proc_prev", {})
    cpus = max(1, os.cpu_count() or 1)
    for index in range(count):
        pid = int(buf[index])
        if pid <= 0:
            continue
        info = _task_info(libproc, pid)
        if info is None:
            continue
        comm = _proc_name(libproc, pid)
        if not comm:
            continue
        cmdline = _proc_path(libproc, pid) or comm
        ticks = int(info.pti_total_user + info.pti_total_system)
        prev = prev_map.get(pid)
        cpu = 0.0
        if prev:
            dt = now - prev[1]
            if dt > 0.05:
                cpu = max(0.0, ((ticks - prev[0]) / 1_000_000_000 / dt) * 100 / cpus)
        prev_map[pid] = (ticks, now)
        seen.add(pid)
        rss = int(info.pti_resident_size)
        found.append(
            {
                "pid": pid,
                "comm": comm,
                "cmdline": cmdline[:180],
                "state": "R",
                "threads": int(info.pti_threadnum),
                "cpuPct": round(cpu, 1),
                "rssMb": round(rss / (1024 * 1024), 1),
                "memPct": round((rss / mem_total) * 100, 2) if mem_total else 0.0,
            }
        )
    host._proc_prev = {pid: val for pid, val in prev_map.items() if pid in seen}
    return found


def collect_windows(host: object) -> None:
    """Visible windows for the window monitor."""
    try:
        rows = _window_rows()
    except OSError:
        raise
    except Exception as exc:
        raise OSError(str(exc)) from exc
    host.set_windows(json.dumps(rows[:80]))


def launch(command: str) -> dict:
    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "empty command"}
    try:
        if command.startswith("http://") or command.startswith("https://"):
            subprocess.Popen(["open", command], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(command, shell=True, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def open_path(target: str) -> dict:
    try:
        subprocess.Popen(["open", target], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "path": target}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def open_terminal(cwd: str | None, command: str | None) -> dict:
    work = str(os.path.expanduser("~"))
    cwd_text = str(cwd or "").strip()
    if cwd_text:
        cand = os.path.expanduser(cwd_text)
        if os.path.isdir(cand):
            work = cand
    extra = " ".join(str(command or "").split())[:400]
    inner = "cd " + shlex.quote(work)
    if extra:
        inner += " && " + extra
    script = 'tell application "Terminal" to do script ' + _apple_string(inner)
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "cwd": work}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def check_password(user: str, password: str) -> dict:
    """Check the macOS login password with PAM. The password is not logged."""
    try:
        pam = ctypes.CDLL("/usr/lib/libpam.dylib")
    except OSError:
        try:
            pam = ctypes.CDLL("/usr/lib/libpam.2.dylib")
        except OSError:
            return {"ok": password == user, "method": "username-fallback"}
    for service in (b"authorization", b"login"):
        try:
            ok = _pam_auth(pam, service, user, password)
        except OSError:
            continue
        return {"ok": ok, "method": "pam"}
    return {"ok": False, "method": "pam"}


def _libc() -> ctypes.CDLL:
    lib = getattr(_libc, "_lib", None)
    if lib is None:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        _libc._lib = lib  # type: ignore[attr-defined]
    return lib


def _libproc() -> ctypes.CDLL:
    lib = getattr(_libproc, "_lib", None)
    if lib is None:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        lib.proc_listpids.restype = ctypes.c_int
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        lib.proc_name.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        lib.proc_name.restype = ctypes.c_int
        lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        lib.proc_pidpath.restype = ctypes.c_int
        _libproc._lib = lib  # type: ignore[attr-defined]
    return lib


def _sysctl_u64(name: str) -> int:
    libc = _libc()
    libc.sysctlbyname.argtypes = [
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    libc.sysctlbyname.restype = ctypes.c_int
    val = ctypes.c_uint64()
    size = ctypes.c_size_t(ctypes.sizeof(val))
    if libc.sysctlbyname(name.encode("utf-8"), ctypes.byref(val), ctypes.byref(size), None, 0) != 0:
        return 0
    return int(val.value)


class _HostCpuLoad(ctypes.Structure):
    _fields_ = [("ticks", ctypes.c_uint * 4)]


class _VmStat64(ctypes.Structure):
    _fields_ = [
        ("free_count", ctypes.c_uint),
        ("active_count", ctypes.c_uint),
        ("inactive_count", ctypes.c_uint),
        ("wire_count", ctypes.c_uint),
        ("zero_fill_count", ctypes.c_uint64),
        ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64),
        ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64),
        ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64),
        ("purgeable_count", ctypes.c_uint),
        ("speculative_count", ctypes.c_uint),
        ("decompressions", ctypes.c_uint64),
        ("compressions", ctypes.c_uint64),
        ("swapins", ctypes.c_uint64),
        ("swapouts", ctypes.c_uint64),
        ("compressor_page_count", ctypes.c_uint),
        ("throttled_count", ctypes.c_uint),
        ("external_page_count", ctypes.c_uint),
        ("internal_page_count", ctypes.c_uint),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
    ]


class _ProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32),
        ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32),
        ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32),
        ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32),
        ("pti_priority", ctypes.c_int32),
    ]


def _host_port() -> int:
    libc = _libc()
    libc.mach_host_self.argtypes = []
    libc.mach_host_self.restype = ctypes.c_uint
    return int(libc.mach_host_self())


def _cpu_ticks() -> tuple[int, int]:
    libc = _libc()
    libc.host_statistics.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    libc.host_statistics.restype = ctypes.c_int
    info = _HostCpuLoad()
    count = ctypes.c_uint(4)
    if libc.host_statistics(_host_port(), 3, ctypes.byref(info), ctypes.byref(count)) != 0:
        return 0, 0
    ticks = [int(info.ticks[i]) for i in range(4)]
    return sum(ticks), ticks[2]


def _memory() -> tuple[int, int]:
    total = _sysctl_u64("hw.memsize")
    page = _sysctl_u64("hw.pagesize") or 4096
    libc = _libc()
    libc.host_statistics64.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    libc.host_statistics64.restype = ctypes.c_int
    stats = _VmStat64()
    count = ctypes.c_uint(ctypes.sizeof(stats) // 4)
    if libc.host_statistics64(_host_port(), 4, ctypes.byref(stats), ctypes.byref(count)) != 0:
        return total, 0
    used_pages = int(stats.active_count) + int(stats.wire_count) + int(stats.compressor_page_count)
    used = min(total, used_pages * page) if total else used_pages * page
    return total, used


# --- Temperature / GPU autodetection (macOS) -------------------------------
# No sudo, no powermetrics, no extra installs. Tried once at startup, then the
# working method is cached:
#   Apple Silicon: IOHID temperature services (IOHIDEventSystemClient), the
#                  same sensors Activity-style tools read without root.
#   Intel Macs:    AppleSMC keys through IOKit (TC0P/TC0D... for the CPU,
#                  TG0P/TG0D... for the GPU).
#   GPU load:      "Device Utilization %" from IOAccelerator (via ioreg).
# Every native call is wrapped, and each read runs with a timeout. If nothing
# works the values stay None and the board shows "–".

import platform as _platform
import re as _re
import struct as _struct
import threading as _threading

_TEMP_MIN_C = 1.0
_TEMP_MAX_C = 125.0
_IOKIT_PATH = "/System/Library/Frameworks/IOKit.framework/IOKit"
_CF_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_UTF8 = 0x08000100
_HID_TEMP_EVENT = 15  # kIOHIDEventTypeTemperature
_HID_PAGE_VENDOR = 0xFF00  # kHIDPage_AppleVendor
_HID_USAGE_TEMP = 0x0005  # kHIDUsage_AppleVendor_TemperatureSensor

# SMC keys, best first. Intel Macs use sp78 values; flt is handled too.
SMC_CPU_KEYS = ("TC0D", "TC0E", "TC0F", "TC0P", "TC0H", "TCXC", "TCAD", "TC1C", "TC2C")
SMC_GPU_KEYS = ("TG0D", "TG0P", "TG0E", "TG0F", "TGDD", "TG0H", "TCGC")


def _valid_temp(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return round(value, 1) if _TEMP_MIN_C <= value <= _TEMP_MAX_C else None


def fourcc(text: str) -> int:
    raw = text.encode("ascii")
    if len(raw) != 4:
        raise ValueError("SMC keys are four characters")
    return int.from_bytes(raw, "big")


def fourcc_text(value: int) -> str:
    return int(value).to_bytes(4, "big").decode("ascii", "replace")


def decode_smc(data_type: str, raw: bytes) -> float | None:
    """Decode an SMC value. Returns None for types that are not temperatures."""
    try:
        if data_type == "sp78" and len(raw) >= 2:
            return _struct.unpack(">h", raw[:2])[0] / 256.0
        if data_type == "flt " and len(raw) >= 4:
            return _struct.unpack("<f", raw[:4])[0]
        if data_type == "fpe2" and len(raw) >= 2:
            return _struct.unpack(">H", raw[:2])[0] / 4.0
        if data_type == "ui8 " and len(raw) >= 1:
            return float(raw[0])
        if data_type == "ui16" and len(raw) >= 2:
            return float(_struct.unpack(">H", raw[:2])[0])
    except Exception:
        return None
    return None


def classify_hid_name(name: str) -> str | None:
    """Map an IOHID temperature sensor name to "cpu", "gpu", or None."""
    n = (name or "").strip().lower()
    if not n:
        return None
    if n.startswith("gpu") or " gpu " in f" {n} ":
        return "gpu"
    if n.startswith(("pacc", "eacc", "acc ", "pcpu", "ecpu", "cpu")):
        return "cpu"
    if n.startswith("pmu tdie"):
        return "cpu-die"
    if n.startswith("soc mtr") or n.startswith("pmu2 tdie"):
        return "soc"
    return None


def pick_hid(readings: list[tuple[str, float]]) -> tuple[float | None, float | None]:
    """(cpu, gpu) from named HID readings. CPU: mean of cluster sensors, then die, then SoC."""
    groups: dict[str, list[float]] = {"cpu": [], "gpu": [], "cpu-die": [], "soc": []}
    for name, value in readings:
        kind = classify_hid_name(name)
        temp = _valid_temp(value)
        if kind and temp is not None:
            groups[kind].append(temp)
    cpu = None
    for key in ("cpu", "cpu-die", "soc"):
        if groups[key]:
            cpu = round(sum(groups[key]) / len(groups[key]), 1)
            break
    gpu = round(sum(groups["gpu"]) / len(groups["gpu"]), 1) if groups["gpu"] else None
    return cpu, gpu


def parse_ioreg_gpu_util(text: str) -> float | None:
    """Highest "Device Utilization %" in `ioreg -r -c IOAccelerator -d 1` output."""
    vals = []
    for match in _re.finditer(r'"Device Utilization %"\s*=\s*(\d+(?:\.\d+)?)', text or ""):
        try:
            vals.append(float(match.group(1)))
        except ValueError:
            continue
    if not vals:
        return None
    return max(0.0, min(100.0, max(vals)))


def _call_with_timeout(fn, timeout: float):
    """Run fn() in a daemon thread. Returns (finished, value)."""
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - every failure means "no reading"
            box["error"] = exc

    worker = _threading.Thread(target=run, name="ldash-sensor", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return False, None
    if "error" in box:
        return True, None
    return True, box.get("value")


class _SMCKeyDataVers(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint8), ("minor", ctypes.c_uint8), ("build", ctypes.c_uint8),
                ("reserved", ctypes.c_uint8), ("release", ctypes.c_uint16)]


class _SMCKeyDataPLimit(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16), ("cpuPLimit", ctypes.c_uint32),
                ("gpuPLimit", ctypes.c_uint32), ("memPLimit", ctypes.c_uint32)]


class _SMCKeyDataKeyInfo(ctypes.Structure):
    _fields_ = [("dataSize", ctypes.c_uint32), ("dataType", ctypes.c_uint32), ("dataAttributes", ctypes.c_uint8)]


class SMCKeyData(ctypes.Structure):
    """SMCKeyData_t from AppleSMC. 80 bytes on 64-bit macOS."""

    _fields_ = [
        ("key", ctypes.c_uint32),
        ("vers", _SMCKeyDataVers),
        ("pLimitData", _SMCKeyDataPLimit),
        ("keyInfo", _SMCKeyDataKeyInfo),
        ("result", ctypes.c_uint8),
        ("status", ctypes.c_uint8),
        ("data8", ctypes.c_uint8),
        ("data32", ctypes.c_uint32),
        ("bytes", ctypes.c_uint8 * 32),
    ]


_SMC_HANDLE_YPC_EVENT = 2
_SMC_READ_KEY = 5
_SMC_GET_KEY_INFO = 9


class _SMCReader:
    """Read AppleSMC temperature keys via IOKit. Unprivileged on Intel Macs."""

    def __init__(self) -> None:
        self.iokit = ctypes.CDLL(_IOKIT_PATH)
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        io = self.iokit
        io.IOServiceMatching.argtypes = [ctypes.c_char_p]
        io.IOServiceMatching.restype = ctypes.c_void_p
        io.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        io.IOServiceGetMatchingService.restype = ctypes.c_uint32
        io.IOServiceOpen.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        io.IOServiceOpen.restype = ctypes.c_int
        io.IOServiceClose.argtypes = [ctypes.c_uint32]
        io.IOServiceClose.restype = ctypes.c_int
        io.IOObjectRelease.argtypes = [ctypes.c_uint32]
        io.IOObjectRelease.restype = ctypes.c_int
        io.IOConnectCallStructMethod.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
                                                 ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        io.IOConnectCallStructMethod.restype = ctypes.c_int
        task = ctypes.c_uint32.in_dll(libc, "mach_task_self_").value
        matching = io.IOServiceMatching(b"AppleSMC")
        if not matching:
            raise OSError("no AppleSMC matching dict")
        service = io.IOServiceGetMatchingService(0, matching)  # consumes `matching`
        if not service:
            raise OSError("AppleSMC not found")
        conn = ctypes.c_uint32(0)
        try:
            rc = io.IOServiceOpen(service, task, 0, ctypes.byref(conn))
        finally:
            io.IOObjectRelease(service)
        if rc != 0 or not conn.value:
            raise OSError(f"IOServiceOpen AppleSMC failed ({rc})")
        self.conn = conn.value
        self._info: dict[str, tuple[int, int]] = {}

    def close(self) -> None:
        try:
            self.iokit.IOServiceClose(self.conn)
        except Exception:
            pass

    def _call(self, inp: SMCKeyData) -> SMCKeyData | None:
        out = SMCKeyData()
        size = ctypes.c_size_t(ctypes.sizeof(out))
        rc = self.iokit.IOConnectCallStructMethod(
            self.conn, _SMC_HANDLE_YPC_EVENT, ctypes.byref(inp), ctypes.sizeof(inp), ctypes.byref(out), ctypes.byref(size)
        )
        if rc != 0 or out.result != 0:
            return None
        return out

    def read(self, key: str) -> float | None:
        info = self._info.get(key)
        if info is None:
            inp = SMCKeyData()
            inp.key = fourcc(key)
            inp.data8 = _SMC_GET_KEY_INFO
            out = self._call(inp)
            if out is None or not out.keyInfo.dataSize:
                return None
            info = (int(out.keyInfo.dataSize), int(out.keyInfo.dataType))
            self._info[key] = info
        size, dtype = info
        inp = SMCKeyData()
        inp.key = fourcc(key)
        inp.keyInfo.dataSize = size
        inp.data8 = _SMC_READ_KEY
        out = self._call(inp)
        if out is None:
            return None
        raw = bytes(out.bytes)[: min(size, 32)]
        return _valid_temp(decode_smc(fourcc_text(dtype), raw))


def smc_pick(read, keys: tuple[str, ...]) -> tuple[str | None, float | None]:
    """First key in `keys` that gives a sane temperature."""
    for key in keys:
        try:
            value = _valid_temp(read(key))
        except Exception:
            value = None
        if value is not None:
            return key, value
    return None, None


class _HIDReader:
    """IOHID temperature services (Apple Silicon). Private but unprivileged API."""

    def __init__(self) -> None:
        io = ctypes.CDLL(_IOKIT_PATH)
        cf = ctypes.CDLL(_CF_PATH)
        self.io, self.cf = io, cf
        vp = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = vp
        cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFNumberCreate.argtypes = [vp, ctypes.c_int, vp]
        cf.CFNumberCreate.restype = vp
        cf.CFDictionaryCreate.argtypes = [vp, ctypes.POINTER(vp), ctypes.POINTER(vp), ctypes.c_long, vp, vp]
        cf.CFDictionaryCreate.restype = vp
        cf.CFArrayGetCount.argtypes = [vp]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [vp, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = vp
        cf.CFGetTypeID.argtypes = [vp]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFStringGetTypeID.argtypes = []
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFRelease.argtypes = [vp]
        cf.CFRelease.restype = None
        io.IOHIDEventSystemClientCreate.argtypes = [vp]
        io.IOHIDEventSystemClientCreate.restype = vp
        io.IOHIDEventSystemClientSetMatching.argtypes = [vp, vp]
        io.IOHIDEventSystemClientSetMatching.restype = ctypes.c_int
        io.IOHIDEventSystemClientCopyServices.argtypes = [vp]
        io.IOHIDEventSystemClientCopyServices.restype = vp
        io.IOHIDServiceClientCopyProperty.argtypes = [vp, vp]
        io.IOHIDServiceClientCopyProperty.restype = vp
        io.IOHIDServiceClientCopyEvent.argtypes = [vp, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]
        io.IOHIDServiceClientCopyEvent.restype = vp
        io.IOHIDEventGetFloatValue.argtypes = [vp, ctypes.c_int32]
        io.IOHIDEventGetFloatValue.restype = ctypes.c_double
        self._key_cb = ctypes.byref(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
        self._val_cb = ctypes.byref(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
        self.client = io.IOHIDEventSystemClientCreate(None)
        if not self.client:
            raise OSError("IOHIDEventSystemClientCreate failed")
        made = []
        try:
            k_page = self._cfstr("PrimaryUsagePage")
            k_usage = self._cfstr("PrimaryUsage")
            made += [k_page, k_usage]
            page = ctypes.c_int32(_HID_PAGE_VENDOR)
            usage = ctypes.c_int32(_HID_USAGE_TEMP)
            v_page = cf.CFNumberCreate(None, 3, ctypes.byref(page))  # kCFNumberSInt32Type
            v_usage = cf.CFNumberCreate(None, 3, ctypes.byref(usage))
            made += [v_page, v_usage]
            if not all(made):
                raise OSError("CoreFoundation allocation failed")
            keys = (vp * 2)(k_page, k_usage)
            vals = (vp * 2)(v_page, v_usage)
            match = cf.CFDictionaryCreate(None, keys, vals, 2, self._key_cb, self._val_cb)
            if not match:
                raise OSError("CFDictionaryCreate failed")
            made.append(match)
            io.IOHIDEventSystemClientSetMatching(self.client, match)
        finally:
            for ref in made:
                if ref:
                    cf.CFRelease(ref)
        self._product = self._cfstr("Product")
        if not self._product:
            raise OSError("CoreFoundation allocation failed")

    def _cfstr(self, text: str):
        return self.cf.CFStringCreateWithCString(None, text.encode("utf-8"), _UTF8)

    def _name(self, ref) -> str:
        if not ref:
            return ""
        try:
            if self.cf.CFGetTypeID(ref) != self.cf.CFStringGetTypeID():
                return ""
            buf = ctypes.create_string_buffer(256)
            if not self.cf.CFStringGetCString(ref, buf, 256, _UTF8):
                return ""
            return buf.value.decode("utf-8", "replace")
        finally:
            self.cf.CFRelease(ref)

    def readings(self) -> list[tuple[str, float]]:
        services = self.io.IOHIDEventSystemClientCopyServices(self.client)
        if not services:
            return []
        rows: list[tuple[str, float]] = []
        field = _HID_TEMP_EVENT << 16  # IOHIDEventFieldBase(kIOHIDEventTypeTemperature)
        try:
            count = max(0, min(int(self.cf.CFArrayGetCount(services)), 256))
            for index in range(count):
                svc = self.cf.CFArrayGetValueAtIndex(services, index)
                if not svc:
                    continue
                name = self._name(self.io.IOHIDServiceClientCopyProperty(svc, self._product))
                event = self.io.IOHIDServiceClientCopyEvent(svc, _HID_TEMP_EVENT, 0, 0)
                if not event:
                    continue
                try:
                    value = float(self.io.IOHIDEventGetFloatValue(event, field))
                finally:
                    self.cf.CFRelease(event)
                if name and _valid_temp(value) is not None:
                    rows.append((name, value))
        finally:
            self.cf.CFRelease(services)
        return rows


class MacSensors:
    """Probe once, cache the method that works, and keep readings steady.

    - Reads are serialized: a second caller waits for the read in flight
      instead of getting None back.
    - Each call retries the cached method a few times before giving up.
    - A sensor that answers without a value: the last good value is held for
      HOLD_S seconds.
    - A read we could not finish in time (slow native call under heavy load,
      a read still in flight, re-probing, lock contention, an exception): the
      last good value is held for LOAD_HOLD_S seconds. A slow read that ends
      late still refreshes the held value.
    - Only MAX_TIMEOUTS slow reads in a row (or one stuck for longer than that)
      count as a hung method; it is then re-probed after REPROBE_AFTER_HANG_S.
      One slow read under load no longer drops a working method for a minute.
    """

    TIMEOUT_S = 2.0
    MAX_FAILS = 3
    HOLD_S = 10.0
    LOAD_HOLD_S = 45.0
    RETRIES = 3
    RETRY_DELAY_S = 0.05
    REPROBE_AFTER_S = 60.0
    REPROBE_AFTER_HANG_S = 10.0
    MAX_TIMEOUTS = 3
    MAX_PROBES = 8

    def __init__(self, machine: str | None = None, hid_factory=None, smc_factory=None, runner=None,
                 ioreg: str | None = "/usr/sbin/ioreg", clock=None, sleep=None) -> None:
        self.machine = (machine if machine is not None else _safe_machine()).lower()
        self._hid_factory = hid_factory or _HIDReader
        self._smc_factory = smc_factory or _SMCReader
        self._run = runner or subprocess.check_output
        self._ioreg = ioreg
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._lock = _threading.Lock()
        self._read_lock = _threading.Lock()
        self._probed = False
        self._probing = False
        self._probes = 0
        self._next_probe_at = 0.0
        self.method = "none"
        self.reader = None
        self.smc_cpu_key: str | None = None
        self.smc_gpu_key: str | None = None
        self._expect = {"cpu": False, "gpu": False}
        self._fails = 0
        self._timeouts = 0
        self._inflight: _threading.Thread | None = None
        self._inflight_at = 0.0
        self._last: dict[str, tuple[float | None, float]] = {"cpu": (None, 0.0), "gpu": (None, 0.0), "util": (None, 0.0)}
        self._util_ok = True
        self._util_seen = False
        self._util_fails = 0

    @property
    def apple_silicon(self) -> bool:
        return self.machine.startswith("arm") or self.machine == "aarch64"

    def _try_hid(self) -> bool:
        done, reader = _call_with_timeout(self._hid_factory, self.TIMEOUT_S)
        if not done or reader is None:
            return False
        done, rows = _call_with_timeout(reader.readings, self.TIMEOUT_S)
        if not done or not rows:
            return False
        cpu, gpu = pick_hid(rows)
        if cpu is None and gpu is None:
            return False
        self.method, self.reader = "iohid", reader
        self._expect = {"cpu": cpu is not None, "gpu": gpu is not None}
        self._seed(cpu, gpu)
        return True

    def _try_smc(self) -> bool:
        done, reader = _call_with_timeout(self._smc_factory, self.TIMEOUT_S)
        if not done or reader is None:
            return False

        def find():
            return smc_pick(reader.read, SMC_CPU_KEYS), smc_pick(reader.read, SMC_GPU_KEYS)

        done, picked = _call_with_timeout(find, self.TIMEOUT_S * 2)
        if not done or not picked:
            return False
        (cpu_key, cpu_val), (gpu_key, gpu_val) = picked
        if cpu_key is None and gpu_key is None:
            try:
                reader.close()
            except Exception:
                pass
            return False
        self.method, self.reader = "smc", reader
        self.smc_cpu_key, self.smc_gpu_key = cpu_key, gpu_key
        self._expect = {"cpu": cpu_key is not None, "gpu": gpu_key is not None}
        self._seed(cpu_val, gpu_val)
        return True

    def probe(self) -> str:
        self._probed = True
        self._probes += 1
        self._next_probe_at = self._clock() + self.REPROBE_AFTER_S
        self.method, self.reader = "none", None
        self._timeouts = 0
        self._inflight = None
        order = (self._try_hid, self._try_smc) if self.apple_silicon else (self._try_smc, self._try_hid)
        for attempt in order:
            try:
                if attempt():
                    break
            except Exception:
                continue
        self._fails = 0
        return self.method

    def _read_once(self) -> tuple[float | None, float | None]:
        if self.method == "iohid":
            return pick_hid(self.reader.readings())
        if self.method == "smc":
            cpu = self.reader.read(self.smc_cpu_key) if self.smc_cpu_key else None
            gpu = self.reader.read(self.smc_gpu_key) if self.smc_gpu_key else None
            return _valid_temp(cpu), _valid_temp(gpu)
        return None, None

    def _seed(self, cpu: float | None, gpu: float | None) -> None:
        """The probe's own readings count as good values (no cold null after it)."""
        with self._lock:
            for kind, value in (("cpu", _valid_temp(cpu)), ("gpu", _valid_temp(gpu))):
                if value is not None:
                    self._last[kind] = (value, self._clock())

    def _hold(self, kind: str, value: float | None, hold_s: float | None = None) -> float | None:
        """Remember a good value, or return the last good one for hold_s (default HOLD_S) seconds."""
        now = self._clock()
        if value is not None:
            self._last[kind] = (value, now)
            return value
        last, at = self._last[kind]
        if last is not None and now - at <= (self.HOLD_S if hold_s is None else hold_s):
            return last
        return None

    def _held(self, cpu: float | None, gpu: float | None, load: bool = False) -> tuple[float | None, float | None]:
        hold_s = self.LOAD_HOLD_S if load else self.HOLD_S
        with self._lock:
            return self._hold("cpu", cpu, hold_s), self._hold("gpu", gpu, hold_s)

    def _late(self, value) -> None:
        """A read that timed out finished after all: keep its values fresh."""
        try:
            cpu, gpu = value if value else (None, None)
            self._seed(cpu, gpu)
            if _valid_temp(cpu) is not None or _valid_temp(gpu) is not None:
                self._timeouts = 0
        except Exception:
            pass

    def _timed_read(self) -> tuple[str, tuple | None]:
        """One native read with TIMEOUT_S. Returns ("ok"|"timeout"|"busy", value).

        Never starts a second native read while an earlier one is still stuck.
        """
        if self._inflight is not None and self._inflight.is_alive():
            return "busy", None
        box: dict = {}
        box_lock = _threading.Lock()

        def run() -> None:
            try:
                value = self._read_once()
            except Exception:  # noqa: BLE001 - every failure means "no reading"
                value = None
            with box_lock:
                box["value"] = value
                late = box.get("late", False)
            if late:
                self._late(value)

        worker = _threading.Thread(target=run, name="ldash-sensor", daemon=True)
        self._inflight, self._inflight_at = worker, self._clock()
        worker.start()
        worker.join(self.TIMEOUT_S)
        with box_lock:
            if "value" in box:
                self._inflight = None
                return "ok", box["value"]
            box["late"] = True
        return "timeout", None

    def _drop_hung_method(self) -> None:
        self.method = "none"
        self._timeouts = 0
        self._next_probe_at = self._clock() + self.REPROBE_AFTER_HANG_S

    def _wants_probe(self) -> bool:
        if not self._probed:
            return True
        return (self.method == "none" and self._probes < self.MAX_PROBES
                and self._clock() >= self._next_probe_at)

    def _read_with_retry(self) -> tuple[float | None, float | None, bool]:
        """(cpu, gpu, load_miss). load_miss: no read finished in time."""
        cpu = gpu = None
        load_miss = False
        for attempt in range(self.RETRIES):
            if self.method == "none":
                break
            status, value = self._timed_read()
            if status != "ok":
                load_miss = True
                stuck_for = self._clock() - self._inflight_at
                if status == "timeout":
                    self._timeouts += 1
                if self._timeouts >= self.MAX_TIMEOUTS or (
                        status == "busy" and stuck_for > self.TIMEOUT_S * self.MAX_TIMEOUTS):
                    # Really hung, not just slow: stop using it (no thread pile-up)
                    # and re-probe soon; the held value covers the gap.
                    self._drop_hung_method()
                # Slow under load: keep the method, hold the last value.
                break
            self._timeouts = 0
            if value:
                if cpu is None:
                    cpu = _valid_temp(value[0])
                if gpu is None:
                    gpu = _valid_temp(value[1])
            if (cpu is not None or not self._expect["cpu"]) and (gpu is not None or not self._expect["gpu"]):
                break
            if attempt + 1 < self.RETRIES:
                self._sleep(self.RETRY_DELAY_S)
        if cpu is None and gpu is None and self.method != "none":
            self._fails += 1
            if self._fails >= self.MAX_FAILS * 10:
                # Long dead streak: probe again (maybe another key/sensor works now).
                self.method = "none"
                self._next_probe_at = self._clock()
        elif cpu is not None or gpu is not None:
            self._fails = 0
        if self.method == "none" and self._probed and not load_miss and cpu is None and gpu is None:
            # Waiting for a re-probe after a hang: still a "could not read" gap.
            load_miss = self._next_probe_at > self._clock()
        return cpu, gpu, load_miss

    def temps(self) -> tuple[float | None, float | None]:
        """(cpuTempC, gpuTempC), steady across transient misses."""
        try:
            return self._temps()
        except Exception:
            # Never let an error (e.g. no thread could be started under load)
            # bypass the hold and reach the UI as a null.
            return self._held(None, None, load=True)

    def _temps(self) -> tuple[float | None, float | None]:
        with self._lock:
            probing_elsewhere = self._probing
        if probing_elsewhere:
            # The first probe can take a few seconds; never block an HTTP call on it.
            return self._held(None, None, load=True)
        if not self._read_lock.acquire(timeout=self.TIMEOUT_S * (self.RETRIES + 1)):
            return self._held(None, None, load=True)
        try:
            if self._wants_probe():
                with self._lock:
                    self._probing = True
                try:
                    self.probe()
                except Exception:
                    self.method = "none"
                finally:
                    with self._lock:
                        self._probing = False
            cpu, gpu, load_miss = self._read_with_retry()
        finally:
            self._read_lock.release()
        return self._held(cpu, gpu, load=load_miss)

    def _ioreg_util(self) -> float | None:
        self._util_slow = False
        try:
            out = self._run([self._ioreg, "-r", "-c", "IOAccelerator", "-d", "1"], text=True, timeout=2, stderr=subprocess.DEVNULL)
            return parse_ioreg_gpu_util(out)
        except subprocess.TimeoutExpired:
            self._util_slow = True  # ioreg starved under load, not "no GPU"
            return None
        except Exception:
            return None

    def gpu_util(self) -> float | None:
        if not self._util_ok or not self._ioreg:
            return None
        try:
            util = self._ioreg_util()
            if util is None:
                util = self._ioreg_util()  # one quick retry
            slow = getattr(self, "_util_slow", False)
        except Exception:
            util, slow = None, True
        with self._lock:
            if util is None:
                self._util_fails += 1
                # Only give up on a Mac where ioreg never reported a value.
                if not self._util_seen and self._util_fails >= self.MAX_FAILS:
                    self._util_ok = False
            else:
                self._util_seen = True
                self._util_fails = 0
            return self._hold("util", util, self.LOAD_HOLD_S if slow else None)

    def sources(self) -> dict:
        cpu = self.method
        if self.method == "smc":
            cpu = f"smc:{self.smc_cpu_key or '-'}"
            gpu = f"smc:{self.smc_gpu_key or '-'}"
        else:
            gpu = self.method
        return {"cpu": cpu, "gpu": gpu, "gpuLoad": "ioreg" if self._util_ok else "none"}


def _safe_machine() -> str:
    try:
        return _platform.machine() or ""
    except Exception:
        return ""


_MAC_SENSORS: MacSensors | None = None
_MAC_SENSORS_LOCK = _threading.Lock()


def _mac_sensors() -> MacSensors:
    global _MAC_SENSORS
    with _MAC_SENSORS_LOCK:
        if _MAC_SENSORS is None:
            _MAC_SENSORS = MacSensors()
        return _MAC_SENSORS


_WARM_STARTED = False


def _warm() -> None:
    sensors = _mac_sensors()
    try:
        sensors.temps()
    except Exception:
        pass
    try:
        sensors.gpu_util()
    except Exception:
        pass


def warm_sensors() -> None:
    """Start the one-time probe in the background (once) so the first sample has values.

    serve.py calls this at backend start-up, before the window opens."""
    global _WARM_STARTED
    with _MAC_SENSORS_LOCK:
        if _WARM_STARTED:
            return
        _WARM_STARTED = True
    try:
        _threading.Thread(target=_warm, name="ldash-sensor-probe", daemon=True).start()
    except Exception:
        pass


SAMPLE_MAX_AGE_S = 2.0
_SAMPLE_LOCK = _threading.Lock()
_SAMPLE: dict = {"at": None, "value": (None, None, None)}


def _sample_temps_and_gpu_uncached() -> tuple[float | None, float | None, float | None]:
    sensors = _mac_sensors()
    try:
        cpu, gpu_temp = sensors.temps()
    except Exception:
        cpu = gpu_temp = None
    try:
        gpu_pct = sensors.gpu_util()
    except Exception:
        gpu_pct = None
    n_pct, n_temp = (None, None)
    if shutil.which("nvidia-smi"):
        n_pct, n_temp = _nvidia()
    if n_pct is not None:
        gpu_pct = n_pct
    if n_temp is not None:
        gpu_temp = n_temp
    return cpu, gpu_pct, gpu_temp


def _sample_temps_and_gpu() -> tuple[float | None, float | None, float | None]:
    """(cpuTempC, gpuPct, gpuTempC). nvidia-smi still wins if someone has it.

    The single sampling path for the Mac: sample_system() calls this too.
    Concurrent callers share one sample taken at most SAMPLE_MAX_AGE_S ago.
    """
    with _SAMPLE_LOCK:
        now = time.monotonic()
        at = _SAMPLE["at"]
        if at is not None and now - at < SAMPLE_MAX_AGE_S:
            return _SAMPLE["value"]
        value = _sample_temps_and_gpu_uncached()
        if value[0] is not None or value[2] is not None:
            _SAMPLE["at"] = time.monotonic()
            _SAMPLE["value"] = value
        return value


def _nvidia() -> tuple[float | None, float | None]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return None, None
    try:
        out = subprocess.check_output(
            [binary, "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    line = out.strip().splitlines()
    if not line:
        return None, None
    parts = [part.strip() for part in line[0].split(",")]
    util = temp = None
    try:
        util = float(parts[0])
    except (ValueError, IndexError):
        util = None
    try:
        temp = float(parts[1])
    except (ValueError, IndexError):
        temp = None
    return util, temp


def _task_info(libproc: ctypes.CDLL, pid: int) -> _ProcTaskInfo | None:
    info = _ProcTaskInfo()
    size = libproc.proc_pidinfo(pid, 4, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size < ctypes.sizeof(info):
        return None
    return info


def _proc_name(libproc: ctypes.CDLL, pid: int) -> str:
    buf = ctypes.create_string_buffer(256)
    if libproc.proc_name(pid, buf, 256) <= 0:
        return ""
    return buf.value.decode("utf-8", "replace")


def _proc_path(libproc: ctypes.CDLL, pid: int) -> str:
    buf = ctypes.create_string_buffer(4096)
    if libproc.proc_pidpath(pid, buf, 4096) <= 0:
        return ""
    return buf.value.decode("utf-8", "replace")


def _window_rows() -> list[dict]:
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFNumberGetTypeID.restype = ctypes.c_ulong
    cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
    cf.CFStringGetTypeID.restype = ctypes.c_ulong
    cf.CFDictionaryGetTypeID.restype = ctypes.c_ulong
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
    cf.CFBooleanGetValue.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    utf8 = 0x08000100
    array = cg.CGWindowListCopyWindowInfo(0, 0)
    if not array:
        return []
    front_pid = _front_pid()
    made: list[int] = []

    def cf_key(text: str) -> int:
        ref = cf.CFStringCreateWithCString(None, text.encode("utf-8"), utf8)
        if not ref:
            raise OSError("CFString")
        made.append(ref)
        return ref

    keys = {
        "name": _cf_symbol(cg, "kCGWindowName"),
        "owner": _cf_symbol(cg, "kCGWindowOwnerName"),
        "pid": _cf_symbol(cg, "kCGWindowOwnerPID"),
        "bounds": _cf_symbol(cg, "kCGWindowBounds"),
        "layer": _cf_symbol(cg, "kCGWindowLayer"),
        "onscreen": _cf_symbol(cg, "kCGWindowIsOnscreen"),
    }
    w_key, h_key = cf_key("Width"), cf_key("Height")
    rows: list[dict] = []
    try:
        n = cf.CFArrayGetCount(array)
        for i in range(n):
            item = cf.CFArrayGetValueAtIndex(array, i)
            if not item:
                continue
            layer = _cf_int(cf, cf.CFDictionaryGetValue(item, keys["layer"]))
            if layer != 0:
                continue
            title = _cf_string(cf, cf.CFDictionaryGetValue(item, keys["name"]))
            owner = _cf_string(cf, cf.CFDictionaryGetValue(item, keys["owner"]))
            if not title and not owner:
                continue
            pid = _cf_int(cf, cf.CFDictionaryGetValue(item, keys["pid"]))
            onscreen = _cf_bool(cf, cf.CFDictionaryGetValue(item, keys["onscreen"]))
            bounds = cf.CFDictionaryGetValue(item, keys["bounds"])
            width = height = 0
            if bounds and cf.CFGetTypeID(bounds) == cf.CFDictionaryGetTypeID():
                width = int(_cf_float(cf, cf.CFDictionaryGetValue(bounds, w_key)))
                height = int(_cf_float(cf, cf.CFDictionaryGetValue(bounds, h_key)))
            rows.append(
                {
                    "caption": (title or owner)[:180],
                    "cls": owner[:120],
                    "name": owner[:120],
                    "pid": pid,
                    "active": bool(front_pid) and pid == front_pid and onscreen,
                    "minimized": not onscreen,
                    "w": width,
                    "h": height,
                }
            )
    finally:
        for ref in made:
            cf.CFRelease(ref)
        cf.CFRelease(array)
    return rows


def _cf_symbol(lib: ctypes.CDLL, name: str) -> int:
    slot = ctypes.c_void_p.in_dll(lib, name)
    return int(ctypes.cast(slot, ctypes.POINTER(ctypes.c_void_p)).contents.value or 0)


def _cf_int(cf: ctypes.CDLL, ref: int) -> int:
    if not ref:
        return 0
    if cf.CFGetTypeID(ref) != cf.CFNumberGetTypeID():
        return 0
    val = ctypes.c_int64()
    if not cf.CFNumberGetValue(ref, 4, ctypes.byref(val)):
        return 0
    return int(val.value)


def _cf_float(cf: ctypes.CDLL, ref: int) -> float:
    if not ref:
        return 0.0
    if cf.CFGetTypeID(ref) != cf.CFNumberGetTypeID():
        return 0.0
    val = ctypes.c_double()
    if not cf.CFNumberGetValue(ref, 13, ctypes.byref(val)):
        return 0.0
    return float(val.value)


def _cf_bool(cf: ctypes.CDLL, ref: int) -> bool:
    if not ref:
        return False
    if cf.CFGetTypeID(ref) == cf.CFBooleanGetTypeID():
        return bool(cf.CFBooleanGetValue(ref))
    return _cf_int(cf, ref) != 0


def _cf_string(cf: ctypes.CDLL, ref: int) -> str:
    if not ref or cf.CFGetTypeID(ref) != cf.CFStringGetTypeID():
        return ""
    buf = ctypes.create_string_buffer(512)
    if not cf.CFStringGetCString(ref, buf, 512, 0x08000100):
        return ""
    return buf.value.decode("utf-8", "replace")


def _front_pid() -> int:
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    except OSError:
        return 0
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_msgSend.restype = ctypes.c_void_p
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    workspace = objc.objc_getClass(b"NSWorkspace")
    shared = objc.objc_msgSend(workspace, objc.sel_registerName(b"sharedWorkspace"))
    if not shared:
        return 0
    front = objc.objc_msgSend(shared, objc.sel_registerName(b"frontmostApplication"))
    if not front:
        return 0
    objc.objc_msgSend.restype = ctypes.c_int
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    return int(objc.objc_msgSend(front, objc.sel_registerName(b"processIdentifier")))


def _apple_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pam_auth(pam: ctypes.CDLL, service: bytes, user: str, password: str) -> bool:
    libc = _libc()
    libc.malloc.restype = ctypes.c_void_p
    libc.malloc.argtypes = [ctypes.c_size_t]
    pam_success = 0
    echo_off = 1

    class PamMessage(ctypes.Structure):
        _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]

    class PamResponse(ctypes.Structure):
        _fields_ = [("resp", ctypes.c_char_p), ("resp_retcode", ctypes.c_int)]

    conv_func = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.POINTER(PamMessage)),
        ctypes.POINTER(ctypes.POINTER(PamResponse)),
        ctypes.c_void_p,
    )
    secret = password.encode("utf-8")

    def alloc(data: bytes) -> int:
        buf = libc.malloc(len(data) + 1)
        if not buf:
            raise OSError("malloc failed")
        ctypes.memmove(buf, data + b"\0", len(data) + 1)
        return buf

    @conv_func
    def converse(count, messages, responses, _app):
        if count < 1:
            return 19
        raw = libc.malloc(count * ctypes.sizeof(PamResponse))
        if not raw:
            return 19
        ctypes.memset(raw, 0, count * ctypes.sizeof(PamResponse))
        rows = ctypes.cast(raw, ctypes.POINTER(PamResponse))
        for i in range(count):
            style = messages[i].contents.msg_style
            if style == echo_off:
                rows[i].resp = ctypes.cast(alloc(secret), ctypes.c_char_p)
            elif style == 2:
                rows[i].resp = ctypes.cast(alloc(user.encode("utf-8")), ctypes.c_char_p)
            else:
                rows[i].resp = None
            rows[i].resp_retcode = 0
        responses[0] = rows
        return pam_success

    class PamConv(ctypes.Structure):
        _fields_ = [("conv", conv_func), ("appdata_ptr", ctypes.c_void_p)]

    pam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(PamConv), ctypes.POINTER(ctypes.c_void_p)]
    pam.pam_start.restype = ctypes.c_int
    pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_authenticate.restype = ctypes.c_int
    pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_end.restype = ctypes.c_int
    handle = ctypes.c_void_p()
    conv = PamConv(converse, None)
    started = pam.pam_start(service, user.encode("utf-8"), ctypes.byref(conv), ctypes.byref(handle))
    if started != pam_success:
        raise OSError("pam_start failed")
    try:
        return pam.pam_authenticate(handle, 0) == pam_success
    finally:
        pam.pam_end(handle, 0)
