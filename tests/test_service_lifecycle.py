import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import psutil


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("service_lifecycle", ROOT / "scripts/manage_services.py")
lifecycle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lifecycle
SPEC.loader.exec_module(lifecycle)


def available_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


class ServicePlanTest(unittest.TestCase):
    def test_current_python_is_reused_without_ocr_venv(self):
        with TemporaryDirectory() as directory:
            plan = lifecycle.service_plan(Path(directory), {}, sys.executable)
        self.assertEqual([service.name for service in plan], ["ocr", "platform"])
        self.assertTrue(all(service.python == sys.executable for service in plan))

    def test_ocr_venv_has_priority_over_current_conda_and_explicit_override_wins(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            local = lifecycle.python_in(root / "ocr_service/.venv")
            local.parent.mkdir(parents=True)
            local.touch()
            self.assertEqual(lifecycle.select_ocr_python(root, {}, "current-conda"), str(local))
            alternate = root / "custom-python"
            alternate.touch()
            self.assertEqual(lifecycle.select_ocr_python(root, {"OCR_PYTHON": "custom-python"}, "current-conda"), str(alternate))
            with self.assertRaisesRegex(RuntimeError, "not found"):
                lifecycle.select_ocr_python(root, {"OCR_PYTHON": "missing"}, "current-conda")

    def test_remote_ocr_is_not_launched_and_mismatches_fail_early(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = lifecycle.service_plan(root, {"OCR_SERVICE_BASE_URL": "http://192.0.2.162:9024"}, sys.executable)
            self.assertEqual([service.name for service in plan], ["platform"])
            plan = lifecycle.service_plan(root, {"START_LOCAL_OCR": "false"}, sys.executable)
            self.assertEqual([service.name for service in plan], ["platform"])
            for environment in ({"START_LOCAL_OCR": "invalid"}, {"OCR_PORT": "9100"}, {"APP_PORT": "9024"}, {"START_LOCAL_OCR": "true", "OCR_SERVICE_BASE_URL": "https://remote.invalid"}):
                with self.assertRaises((RuntimeError, ValueError)):
                    lifecycle.service_plan(root, environment, sys.executable)

    def test_powershell_environment_priority(self):
        if os.name != "nt":
            self.skipTest("PowerShell environment selection runs on Windows")
        with TemporaryDirectory(prefix="startup env ") as directory:
            root = Path(directory)
            active = root / "active conda"
            configured = root / "configured conda"
            local = root / ".venv/Scripts/python.exe"
            alternate = root / "venv/Scripts/python.exe"
            candidates = [local, alternate, active / "python.exe", configured / "python.exe"]
            for candidate in candidates:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.touch()
            environment = dict(os.environ, CONDA_PREFIX=str(active), VISION_CONDA_PREFIX=str(configured))
            command = f". '{ROOT / 'scripts/python_environment.ps1'}'; Get-PlatformPython -ProjectRoot '{root}'"
            for candidate in candidates:
                result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(Path(result.stdout.strip()).resolve(), candidate.resolve())
                candidate.unlink()
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], env=environment, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)

    def test_shell_environment_priority(self):
        bash = os.getenv("VISION_TEST_BASH") or (shutil.which("bash") if os.name != "nt" else None)
        if not bash:
            self.skipTest("Set VISION_TEST_BASH or run under Bash on Linux")
        with TemporaryDirectory(prefix="startup env ") as directory:
            root = Path(directory)
            candidates = [root / ".venv/bin/python", root / "venv/bin/python", root / "active/bin/python", root / "configured/bin/python"]
            for candidate in candidates:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
                candidate.chmod(0o755)
            environment = dict(os.environ, CONDA_PREFIX=(root / "active").as_posix(), VISION_CONDA_PREFIX=(root / "configured").as_posix(), project_root=root.as_posix())
            command = f'source "{(ROOT / "scripts/python_environment.sh").as_posix()}"; select_platform_python || exit 1; printf "%s" "$python_bin"'
            for candidate in candidates:
                result = subprocess.run([bash, "-c", command], env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, candidate.as_posix())
                candidate.unlink()
            self.assertNotEqual(subprocess.run([bash, "-c", command], env=environment, capture_output=True, timeout=10).returncode, 0)

    def test_shell_wrappers_pass_action_and_exit_status(self):
        bash = os.getenv("VISION_TEST_BASH") or (shutil.which("bash") if os.name != "nt" else None)
        if not bash:
            self.skipTest("Set VISION_TEST_BASH or run under Bash on Linux")
        with TemporaryDirectory(prefix="script wrapper ") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts/python_environment.sh", root / "scripts/python_environment.sh")
            fake_python = root / ".venv/bin/python"
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_LOG"\nexit 17\n', encoding="utf-8", newline="\n")
            fake_python.chmod(0o755)
            call_log = root / "calls.txt"
            environment = dict(os.environ, TEST_LOG=call_log.as_posix())
            for action in ("start", "stop"):
                script = root / f"{action}.sh"
                shutil.copyfile(ROOT / f"{action}.sh", script)
                result = subprocess.run([bash, "-c", 'export PATH="/usr/bin:$PATH"; source "$1" "${@:2}"', "--", script.as_posix(), "--timeout", "8"], env=environment, cwd=ROOT.parent, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 17, result.stderr)
                values = call_log.read_text().splitlines()
                self.assertTrue(values[0].endswith("scripts/manage_services.py"))
                self.assertEqual(values[1:], [action, "--timeout", "8"])


class ServiceLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="service lifecycle ")
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        self.manager = lifecycle.ServiceManager(self.root, timeout=4)
        self.manager.directory.mkdir(parents=True)
        self.services = []
        self.processes = []
        for name in ("ocr", "platform"):
            port = available_port()
            service = lifecycle.Service(name, sys.executable, port, "/health")
            self.services.append(service)
            code = f'''from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{{"status":"READY","engine":"PaddleOCR"}}')
HTTPServer(("127.0.0.1", {port}), Handler).serve_forever()
'''
            (self.root / lifecycle.ENTRY_POINTS[name]).write_text(code, encoding="utf-8")
        self.preflight = patch.object(self.manager, "preflight")
        self.preflight.start()

    def tearDown(self):
        self.preflight.stop()
        for process in psutil.process_iter():
            try:
                if any(self.manager.owns(process, name) for name in lifecycle.ENTRY_POINTS):
                    process.kill()
                    process.wait(timeout=5)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self.temporary.cleanup()

    def test_start_both_repeat_without_duplicates_then_stop_and_repeat(self):
        with lifecycle.lifecycle_lock(self.root):
            self.manager.start(self.services)
            original = {name: self.manager.find(name).pid for name in lifecycle.ENTRY_POINTS}
            self.manager.start(self.services)
            self.assertEqual(original, {name: self.manager.find(name).pid for name in lifecycle.ENTRY_POINTS})
            self.manager.stop()
            self.manager.stop()
            self.assertTrue(all(not self.manager.state_file(name).exists() for name in lifecycle.ENTRY_POINTS))
            self.assertTrue(all(not psutil.pid_exists(pid) for pid in original.values()))

    def test_failure_rolls_back_only_services_started_by_this_command(self):
        (self.root / "scripts/run.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        with self.assertRaises((RuntimeError, psutil.NoSuchProcess)):
            self.manager.start(self.services)
        self.assertIsNone(self.manager.find("ocr"))
        self.manager.start([self.services[0]])
        old_pid = self.manager.find("ocr").pid
        with self.assertRaises((RuntimeError, psutil.NoSuchProcess)):
            self.manager.start(self.services)
        self.assertEqual(self.manager.find("ocr").pid, old_pid)
        self.assertTrue(psutil.pid_exists(old_pid))
        self.manager.stop()

    def test_port_conflict_does_not_start_any_service(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", self.services[1].port))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                self.manager.start(self.services)
            self.assertIsNone(self.manager.find("ocr"))

    def test_missing_dependencies_prevent_partial_start(self):
        with patch.object(self.manager, "preflight", side_effect=RuntimeError("missing dependencies")):
            with self.assertRaisesRegex(RuntimeError, "missing dependencies"):
                self.manager.start(self.services)
        self.assertIsNone(self.manager.find("ocr"))
        self.assertIsNone(self.manager.find("platform"))

    def test_health_timeout_stops_new_process(self):
        self.manager.timeout = 0.5
        with patch.object(self.manager, "health", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.manager.start([self.services[0]])
        self.assertIsNone(self.manager.find("ocr"))

    def test_wrong_pid_or_reused_pid_is_not_stopped(self):
        own_process = psutil.Process()
        self.manager.remember(own_process, "platform")
        with self.assertRaisesRegex(RuntimeError, "ownership mismatch"):
            self.manager.stop()
        self.assertTrue(own_process.is_running())
        self.assertTrue(self.manager.state_file("platform").exists())

    def test_stale_state_is_removed_and_legacy_project_process_is_adopted(self):
        self.manager.state_file("platform").write_text(json.dumps({"pid": 2147483647}), encoding="utf-8")
        self.assertIsNone(self.manager.find("platform"))
        service = self.services[1]
        child = subprocess.Popen([sys.executable, str(self.root / "scripts/run.py")], cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.manager.wait_ready(psutil.Process(child.pid), service)
            self.assertEqual(self.manager.find("platform").pid, child.pid)
            self.manager.stop()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_lock_prevents_overlapping_lifecycle_commands(self):
        with lifecycle.lifecycle_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "Another start/stop"):
                with lifecycle.lifecycle_lock(self.root):
                    self.fail("Concurrent lock should not succeed")


if __name__ == "__main__":
    unittest.main()
