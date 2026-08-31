import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "start-android-emulator.sh"


class AndroidEmulatorReadinessTests(unittest.TestCase):
    def run_clocks(self, clocks, boot="1", timeout="5"):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "clocks.json").write_text(json.dumps(clocks))
            adb = work / "adb"
            adb.write_text("""#!/usr/bin/python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['TEST_CLOCK_ROOT'])
args = sys.argv[1:]
if args[:1] == ['-s']:
    args = args[2:]
if args == ['get-state']:
    print('device')
elif args == ['shell', 'getprop', 'ro.product.cpu.abi']:
    print('arm64-v8a')
elif args in (['shell', 'getprop', 'sys.boot_completed'], ['shell', 'getprop', 'dev.bootcomplete']):
    print(os.environ['TEST_BOOT'])
elif args == ['shell', 'date', '+%s']:
    count_path = root / 'count'
    count = int(count_path.read_text()) if count_path.exists() else 0
    values = json.loads((root / 'clocks.json').read_text())
    value = values[min(count, len(values) - 1)]
    count_path.write_text(str(count + 1))
    if value is None:
        sys.exit(1)
    print(value)
else:
    sys.exit('unexpected adb command: ' + repr(args))
""")
            adb.chmod(0o755)
            for name, body in (("date", "printf '1000\\n'"), ("sleep", ":")):
                script = work / name
                script.write_text("#!/usr/bin/env bash\n" + body + "\n")
                script.chmod(0o755)
            env = dict(os.environ, PATH=str(work) + os.pathsep + os.environ["PATH"],
                       TEST_CLOCK_ROOT=str(work), TEST_BOOT=boot,
                       NAIVEFOX_ADB=str(adb), NAIVEFOX_ANDROID_BOOT_TIMEOUT=timeout,
                       XDG_DATA_HOME=str(work), ANDROID_SDK_ROOT=str(work),
                       ANDROID_AVD_HOME=str(work))
            result = subprocess.run(["bash", str(LAUNCHER)], env=env, capture_output=True,
                                    text=True, timeout=10)
            count = int((work / "count").read_text()) if (work / "count").exists() else 0
            return result, count

    def test_minutes_of_lag_and_nonconsecutive_good_samples_do_not_pass(self):
        result, count = self.run_clocks([820, 1000, 820, 1000, 1001])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 5)
        self.assertIn("ready clock", result.stdout)

    def test_clock_ahead_and_invalid_or_failed_queries_restart_the_guard(self):
        result, count = self.run_clocks([1180, "invalid", None, 1000, 1000])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 5)

    def test_one_second_epoch_rounding_is_allowed(self):
        result, count = self.run_clocks([999, 1001])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 2)

    def test_clock_cannot_replace_the_real_boot_completed_property(self):
        result, count = self.run_clocks([1000], boot="0", timeout="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 0)
        self.assertIn("boot and wall-clock readiness", result.stderr)

    def test_unsynchronized_clock_exhausts_the_existing_boot_budget(self):
        result, count = self.run_clocks([820], timeout="1")
        self.assertEqual(result.returncode, 1)
        self.assertGreater(count, 0)
        self.assertIn("boot and wall-clock readiness", result.stderr)


if __name__ == "__main__":
    unittest.main()
