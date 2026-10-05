"""Mocked tests for the macOS sensor sampling in machost.py (runs on any OS)."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import machost  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class ScriptedSMC:
    """SMC reader whose read(key) follows a per-key script of values (None = miss)."""

    def __init__(self, scripts: dict[str, list], delay: float = 0.0) -> None:
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.delay = delay
        self.calls: dict[str, int] = {}

    def read(self, key: str):
        self.calls[key] = self.calls.get(key, 0) + 1
        if self.delay:
            time.sleep(self.delay)
        script = self.scripts.get(key)
        if not script:
            return None
        return script.pop(0) if len(script) > 1 else script[0]

    def close(self) -> None:
        pass


def intel(reader, clock=None, runner=None, **kw) -> machost.MacSensors:
    s = machost.MacSensors(
        machine="x86_64",
        smc_factory=lambda: reader,
        hid_factory=mock.Mock(side_effect=OSError("no hid")),
        runner=runner or mock.Mock(side_effect=OSError("no ioreg")),
        clock=clock or FakeClock(),
        sleep=lambda _s: None,
        **kw,
    )
    s.TIMEOUT_S = 0.3
    return s


class TempsTests(unittest.TestCase):
    def test_probe_picks_working_keys_and_caches_method(self):
        reader = ScriptedSMC({"TC0E": [66.9], "TCGC": [67.0]})
        s = intel(reader)
        self.assertEqual(s.temps(), (66.9, 67.0))
        self.assertEqual(s.method, "smc")
        self.assertEqual(s.sources()["cpu"], "smc:TC0E")
        self.assertEqual(s.sources()["gpu"], "smc:TCGC")

    def test_retry_within_call_covers_a_transient_miss(self):
        # probe reads once per key, then: miss, hit for CPU; miss, miss, hit for GPU.
        reader = ScriptedSMC({"TC0E": [60.0, None, 57.0], "TCGC": [61.0, None, None, 58.0]})
        s = intel(reader)
        s.probe()
        self.assertEqual(s.temps(), (57.0, 58.0))
        self.assertEqual(s._fails, 0)

    def test_last_good_value_held_for_hold_window_then_none(self):
        clock = FakeClock()
        reader = ScriptedSMC({"TC0E": [66.9], "TCGC": [67.0]})
        s = intel(reader, clock=clock)
        self.assertEqual(s.temps(), (66.9, 67.0))
        reader.scripts = {"TC0E": [None], "TCGC": [None]}  # sensor goes quiet
        clock.advance(5)
        self.assertEqual(s.temps(), (66.9, 67.0))  # held, not null
        clock.advance(6)  # 11 s since the last good value > HOLD_S
        self.assertEqual(s.temps(), (None, None))
        self.assertEqual(s.method, "smc")  # transient misses keep the method

    def test_new_good_value_replaces_held_value(self):
        clock = FakeClock()
        reader = ScriptedSMC({"TC0E": [66.9], "TCGC": [67.0]})
        s = intel(reader, clock=clock)
        s.temps()
        reader.scripts = {"TC0E": [57.2], "TCGC": [None]}
        clock.advance(2)
        self.assertEqual(s.temps(), (57.2, 67.0))

    def test_concurrent_callers_wait_instead_of_getting_none(self):
        reader = ScriptedSMC({"TC0E": [55.0], "TCGC": [56.0]})
        s = intel(reader)
        s.probe()
        reader.delay = 0.05  # every native read is slow
        results = []

        def call():
            results.append(s.temps())

        threads = [threading.Thread(target=call) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(results, [(55.0, 56.0)] * 6)

    def test_caller_during_first_probe_is_not_blocked(self):
        gate = threading.Event()
        reader = ScriptedSMC({"TC0E": [50.0], "TCGC": [51.0]})

        def slow_factory():
            gate.wait(2)
            return reader

        s = intel(reader)
        s._smc_factory = slow_factory
        s.TIMEOUT_S = 3
        warm = threading.Thread(target=s.temps)
        warm.start()
        for _ in range(100):
            if s._probing:
                break
            time.sleep(0.01)
        t0 = time.monotonic()
        self.assertEqual(s.temps(), (None, None))
        self.assertLess(time.monotonic() - t0, 0.5)
        gate.set()
        warm.join(5)
        self.assertEqual(s.temps(), (50.0, 51.0))

    def test_hung_read_drops_method_then_reprobes_later(self):
        clock = FakeClock()
        reader = ScriptedSMC({"TC0E": [66.0], "TCGC": [67.0]})
        s = intel(reader, clock=clock)
        self.assertEqual(s.temps(), (66.0, 67.0))
        reader.delay = 1.0  # longer than TIMEOUT_S: looks hung
        self.assertEqual(s.temps(), (66.0, 67.0))  # held value while it hangs
        self.assertEqual(s.method, "none")
        reader.delay = 0.0
        clock.advance(20)
        self.assertEqual(s.temps(), (None, None))  # hold expired, no re-probe yet
        clock.advance(machost.MacSensors.REPROBE_AFTER_S)
        self.assertEqual(s.temps(), (66.0, 67.0))
        self.assertEqual(s.method, "smc")

    def test_nothing_works_stays_none_without_errors(self):
        s = machost.MacSensors(
            machine="x86_64",
            smc_factory=mock.Mock(side_effect=OSError("no smc")),
            hid_factory=mock.Mock(side_effect=OSError("no hid")),
            runner=mock.Mock(side_effect=OSError),
            clock=FakeClock(),
            sleep=lambda _s: None,
        )
        self.assertEqual(s.temps(), (None, None))
        self.assertEqual(s.method, "none")
        self.assertIsNone(s.gpu_util())

    def test_apple_silicon_hid_path(self):
        hid = mock.Mock()
        hid.readings.return_value = [("pACC MTR Temp Sensor0", 48.0), ("pACC MTR Temp Sensor1", 50.0), ("GPU MTR Temp Sensor1", 44.0)]
        s = machost.MacSensors(
            machine="arm64",
            hid_factory=lambda: hid,
            smc_factory=mock.Mock(side_effect=OSError),
            runner=mock.Mock(side_effect=OSError),
            clock=FakeClock(),
            sleep=lambda _s: None,
        )
        self.assertEqual(s.temps(), (49.0, 44.0))
        hid.readings.return_value = []  # transient empty read
        self.assertEqual(s.temps(), (49.0, 44.0))
        self.assertEqual(s.method, "iohid")


class GpuUtilTests(unittest.TestCase):
    IOREG = '"PerformanceStatistics" = {"Device Utilization %"=12,"Renderer Utilization %"=3}'

    def test_util_held_across_a_transient_ioreg_failure(self):
        clock = FakeClock()
        runner = mock.Mock(side_effect=[self.IOREG, OSError("busy"), OSError("busy")])
        s = intel(ScriptedSMC({}), clock=clock, runner=runner)
        self.assertEqual(s.gpu_util(), 12.0)
        clock.advance(3)
        self.assertEqual(s.gpu_util(), 12.0)
        self.assertTrue(s._util_ok)

    def test_util_retries_once_inside_the_call(self):
        runner = mock.Mock(side_effect=[OSError("busy"), self.IOREG])
        s = intel(ScriptedSMC({}), runner=runner)
        self.assertEqual(s.gpu_util(), 12.0)

    def test_util_never_disabled_after_it_has_worked(self):
        clock = FakeClock()
        runner = mock.Mock(side_effect=[self.IOREG] + [OSError("x")] * 20 + [self.IOREG.replace("12", "30")])
        s = intel(ScriptedSMC({}), clock=clock, runner=runner)
        s.gpu_util()
        for _ in range(10):
            clock.advance(3)
            s.gpu_util()
        self.assertTrue(s._util_ok)
        self.assertEqual(s.gpu_util(), 30.0)

    def test_util_disabled_when_ioreg_never_works(self):
        s = intel(ScriptedSMC({}), runner=mock.Mock(side_effect=OSError("no")))
        for _ in range(3):
            self.assertIsNone(s.gpu_util())
        self.assertFalse(s._util_ok)
        self.assertEqual(s.sources()["gpuLoad"], "none")


class SharedPathTests(unittest.TestCase):
    def setUp(self):
        machost._SAMPLE["at"] = None
        machost._SAMPLE["value"] = (None, None, None)

    def test_sample_temps_and_gpu_shares_one_cached_sample(self):
        fake = mock.Mock(return_value=(66.9, 1.0, 67.0))
        with mock.patch.object(machost, "_sample_temps_and_gpu_uncached", fake):
            self.assertEqual(machost._sample_temps_and_gpu(), (66.9, 1.0, 67.0))
            self.assertEqual(machost._sample_temps_and_gpu(), (66.9, 1.0, 67.0))
        self.assertEqual(fake.call_count, 1)

    def test_uncached_uses_the_singleton_sensors(self):
        sensors = mock.Mock()
        sensors.temps.return_value = (57.0, 58.0)
        sensors.gpu_util.return_value = 4.0
        with mock.patch.object(machost, "_mac_sensors", return_value=sensors), \
                mock.patch.object(machost.shutil, "which", return_value=None):
            self.assertEqual(machost._sample_temps_and_gpu_uncached(), (57.0, 4.0, 58.0))

    def test_sample_system_uses_sample_temps_and_gpu_every_call(self):
        host = type("H", (), {})()
        host._sensors_warmed = True
        sensors = mock.Mock()
        sensors.sources.return_value = {"cpu": "smc:TC0E", "gpu": "smc:TCGC", "gpuLoad": "ioreg"}
        values = iter([(66.9, 1.0, 67.0), (57.1, 2.0, 57.9)])
        with mock.patch.object(machost, "_sample_temps_and_gpu", side_effect=lambda: next(values)) as sampler, \
                mock.patch.object(machost, "_cpu_ticks", return_value=(1000, 500)), \
                mock.patch.object(machost, "_memory", return_value=(16 << 30, 8 << 30)), \
                mock.patch.object(machost, "_mac_sensors", return_value=sensors):
            first = machost.sample_system(host)
            second = machost.sample_system(host)
        self.assertEqual(sampler.call_count, 2)
        self.assertEqual((first["cpuTempC"], first["gpuPct"], first["gpuTempC"]), (66.9, 1.0, 67.0))
        self.assertEqual((second["cpuTempC"], second["gpuPct"], second["gpuTempC"]), (57.1, 2.0, 57.9))
        self.assertEqual(first["sensorSource"]["cpu"], "smc:TC0E")

    def test_sample_system_end_to_end_with_flaky_smc_never_nulls(self):
        clock = FakeClock()
        # Pattern seen on the Intel Mac: good, then whole-call misses.
        reader = ScriptedSMC({"TC0E": [66.9, 66.9, None, None, None, 57.0, None, None, None],
                              "TCGC": [67.0, 67.0, None, None, None, 57.5, None, None, None]})
        runner = mock.Mock(return_value='"Device Utilization %"=1')
        sensors = intel(reader, clock=clock, runner=runner)
        host = type("H", (), {})()
        host._sensors_warmed = True
        seen = []
        with mock.patch.object(machost, "_mac_sensors", return_value=sensors), \
                mock.patch.object(machost, "_cpu_ticks", return_value=(1000, 500)), \
                mock.patch.object(machost, "_memory", return_value=(16 << 30, 8 << 30)), \
                mock.patch.object(machost.shutil, "which", return_value=None), \
                mock.patch.object(machost, "SAMPLE_MAX_AGE_S", 0.0):
            for _ in range(4):
                out = machost.sample_system(host)
                seen.append((out["cpuTempC"], out["gpuTempC"], out["gpuPct"]))
                clock.advance(2)
        self.assertEqual(seen[0], (66.9, 67.0, 1.0))
        self.assertNotIn(None, [v for row in seen for v in row])


class ColdStartTests(unittest.TestCase):
    def setUp(self):
        machost._SAMPLE["at"] = None
        machost._SAMPLE["value"] = (None, None, None)

    def test_probe_readings_seed_the_hold_cache(self):
        # Probe sees values; the first real read right after misses everything.
        reader = ScriptedSMC({"TC0E": [54.6, None], "TCGC": [55.0, None]})
        s = intel(reader)
        self.assertEqual(s.temps(), (54.6, 55.0))

    def test_warm_then_first_sample_has_values(self):
        reader = ScriptedSMC({"TC0E": [56.4], "TCGC": [57.0]})
        sensors = intel(reader, runner=mock.Mock(return_value='"Device Utilization %"=3'))
        with mock.patch.object(machost, "_mac_sensors", return_value=sensors), \
                mock.patch.object(machost, "_WARM_STARTED", False), \
                mock.patch.object(machost.shutil, "which", return_value=None):
            machost.warm_sensors()
            for _ in range(200):
                if sensors._probed and not sensors._probing and sensors._last["util"][0] is not None:
                    break
                time.sleep(0.01)
            self.assertEqual(machost._sample_temps_and_gpu(), (56.4, 3.0, 57.0))

    def test_warm_sensors_starts_only_once(self):
        with mock.patch.object(machost, "_WARM_STARTED", False), \
                mock.patch.object(machost._threading, "Thread") as thread:
            machost.warm_sensors()
            machost.warm_sensors()
        self.assertEqual(thread.call_count, 1)

    def test_empty_sample_is_not_cached(self):
        values = iter([(None, None, None), (55.0, 2.0, 56.0)])
        with mock.patch.object(machost, "_sample_temps_and_gpu_uncached", side_effect=lambda: next(values)):
            self.assertEqual(machost._sample_temps_and_gpu(), (None, None, None))
            self.assertEqual(machost._sample_temps_and_gpu(), (55.0, 2.0, 56.0))


class WatchdogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows"))
        import serve  # noqa: E402

        cls.serve = serve

    def run_watchdog(self, ppids, alive=True):
        seq = iter(ppids)
        stopped = []
        with mock.patch("time.sleep"):
            self.serve.parent_watchdog(
                4242,
                interval=0,
                getppid=lambda: next(seq),
                alive=(lambda pid: alive) if not callable(alive) else alive,
                stop=lambda: stopped.append(True),
            )
        return stopped

    def test_exits_when_reparented_to_launchd(self):
        self.assertEqual(self.run_watchdog([4242, 4242, 1]), [True])

    def test_exits_when_parent_pid_changes(self):
        self.assertEqual(self.run_watchdog([4242, 777]), [True])

    def test_exits_when_parent_is_gone(self):
        calls = iter([True, True, False])
        self.assertEqual(self.run_watchdog([4242] * 5, alive=lambda pid: next(calls)), [True])

    def test_only_started_with_parent_pid_on_posix(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("LADEN_PARENT_PID", None)
            self.assertFalse(self.serve.start_parent_watchdog())
            os.environ["LADEN_PARENT_PID"] = "1"
            self.assertFalse(self.serve.start_parent_watchdog())
            os.environ["LADEN_PARENT_PID"] = "abc"
            self.assertFalse(self.serve.start_parent_watchdog())
            with mock.patch.object(os, "name", "nt"):
                os.environ["LADEN_PARENT_PID"] = str(os.getpid())
                self.assertFalse(self.serve.start_parent_watchdog())
            os.environ.pop("LADEN_PARENT_PID", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
