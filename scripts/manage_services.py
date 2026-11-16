from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

import psutil


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINTS = {"platform": "scripts/run.py", "ocr": "scripts/run_ocr.py"}


@dataclass(frozen=True)
class Service:
    name: str
    python: str
    port: int
    health_path: str
    health_host: str = "127.0.0.1"


def python_in(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def select_ocr_python(root: Path, environment: dict[str, str], fallback: str) -> str:
    explicit = environment.get("OCR_PYTHON", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        candidate = candidate if candidate.is_absolute() else root / candidate
        if not candidate.is_file():
            raise RuntimeError(f"OCR_PYTHON not found: {candidate}")
        return str(candidate.absolute())
    for folder in (".venv", "venv"):
        candidate = python_in(root / "ocr_service" / folder)
        if candidate.is_file():
            return str(candidate.absolute())
    return fallback


def service_plan(root: Path, environment: dict[str, str], python: str) -> list[Service]:
    app_port = int(environment.get("APP_PORT", "9010"))
    ocr_port = int(environment.get("OCR_PORT", "9024"))
    mode = environment.get("START_LOCAL_OCR", "auto").strip().lower()
    if mode not in {"auto", "true", "false"}:
        raise RuntimeError("START_LOCAL_OCR must be auto, true or false")
    endpoint = urlsplit(environment.get("OCR_SERVICE_BASE_URL", "http://127.0.0.1:9024"))
    local = endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
    start_ocr = mode == "true" or (mode == "auto" and local)
    services = []
    if start_ocr:
        if not local or endpoint.scheme != "http" or endpoint.path not in {"", "/"} or endpoint.query:
            raise RuntimeError("Local OCR requires OCR_SERVICE_BASE_URL=http://127.0.0.1:<OCR_PORT>; use START_LOCAL_OCR=false for a remote endpoint")
        if (endpoint.port or 80) != ocr_port:
            raise RuntimeError("OCR_SERVICE_BASE_URL port does not match OCR_PORT")
        bind_host = environment.get("OCR_HOST", "127.0.0.1")
        if bind_host not in {"127.0.0.1", "localhost", "0.0.0.0", "::1", "::"}:
            raise RuntimeError("Local OCR must bind a loopback or wildcard address (OCR_HOST)")
        health_host = "::1" if bind_host in {"::1", "::"} else "127.0.0.1"
        services.append(Service("ocr", select_ocr_python(root, environment, python), ocr_port, "/health", health_host))
    if app_port == ocr_port and start_ocr:
        raise RuntimeError("APP_PORT and OCR_PORT must be different")
    for port in (app_port, ocr_port):
        if not 1 <= port <= 65535:
            raise RuntimeError("Service ports must be between 1 and 65535")
    app_host = environment.get("APP_HOST", "0.0.0.0")
    health_host = "127.0.0.1" if app_host in {"0.0.0.0", "localhost"} else "::1" if app_host == "::" else app_host
    services.append(Service("platform", python, app_port, "/api/v1/health", health_host))
    return services


@contextmanager
def lifecycle_lock(root: Path):
    directory = root / "logs" / "services"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "lifecycle.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another start/stop command is running; retry after it finishes") from error
        try:
            yield directory
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class ServiceManager:
    def __init__(self, root: Path, timeout: float = 60):
        self.root = root.resolve()
        self.directory = self.root / "logs" / "services"
        self.timeout = timeout
        self.launched: dict[int, subprocess.Popen] = {}

    def state_file(self, name: str) -> Path:
        return self.directory / f"{name}.json"

    def owns(self, process: psutil.Process, name: str) -> bool:
        try:
            command = process.cmdline()
            if len(command) < 2 or "-c" in command or "-m" in command:
                return False
            entry = Path(command[1])
            if not entry.is_absolute():
                entry = Path(process.cwd()) / entry
            return entry.resolve() == (self.root / ENTRY_POINTS[name]).resolve()
        except psutil.NoSuchProcess:
            return False

    def remember(self, process: psutil.Process, name: str) -> None:
        state = {"pid": process.pid, "created": process.create_time(), "root": str(self.root), "entry": ENTRY_POINTS[name]}
        destination = self.state_file(name)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(destination)

    def find(self, name: str) -> psutil.Process | None:
        path = self.state_file(name)
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                process = psutil.Process(int(saved["pid"]))
            except psutil.NoSuchProcess:
                path.unlink()
            except (ValueError, KeyError, TypeError) as error:
                raise RuntimeError(f"Invalid service state; inspect {path}") from error
            else:
                if saved.get("root") != str(self.root) or abs(process.create_time() - float(saved["created"])) > 0.01 or not self.owns(process, name):
                    raise RuntimeError(f"PID ownership mismatch; refusing to touch PID {process.pid}. Inspect {path}")
                if process.status() != psutil.STATUS_ZOMBIE:
                    return process
                path.unlink()
        matches = []
        for process in psutil.process_iter():
            try:
                if self.owns(process, name):
                    matches.append(process)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        matching_ids = {process.pid for process in matches}
        matches = [process for process in matches if not any(parent.pid in matching_ids for parent in process.parents())]
        if len(matches) > 1:
            raise RuntimeError(f"Multiple {name} processes found for this project; inspect them before starting/stopping")
        if matches:
            self.remember(matches[0], name)
            return matches[0]
        return None

    def health(self, service: Service) -> bool:
        connection = http.client.HTTPConnection(service.health_host, service.port, timeout=2)
        try:
            connection.request("GET", service.health_path)
            response = connection.getresponse()
            payload = json.loads(response.read(65536))
            return response.status == 200 and str(payload.get("status", "")).upper() == "READY" and (service.name != "ocr" or payload.get("engine") == "PaddleOCR")
        except (OSError, ValueError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def wait_ready(self, process: psutil.Process, service: Service) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    raise psutil.NoSuchProcess(process.pid)
                listeners = [connection for member in [process, *process.children(recursive=True)] for connection in member.net_connections(kind="inet")]
            except psutil.NoSuchProcess as error:
                raise RuntimeError(f"{service.name} exited during startup; see {self.directory / (service.name + '.log')}") from error
            bound = any(connection.status == psutil.CONN_LISTEN and connection.laddr.port == service.port for connection in listeners)
            if bound and self.health(service):
                return
            time.sleep(0.3)
        raise RuntimeError(f"{service.name} health check timed out ({self.timeout}s); see {self.directory / (service.name + '.log')}")

    def port_available(self, service: Service) -> bool:
        family = socket.AF_INET6 if ":" in service.health_host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((service.health_host, service.port))
                return True
            except OSError:
                return False

    def preflight(self, service: Service) -> None:
        modules = ["uvicorn", "fastapi", "dotenv", "paddle", "paddleocr"] if service.name == "ocr" else ["uvicorn", "fastapi", "sqlalchemy", "pydantic_settings", "psutil"]
        check = "import importlib.util,sys; missing=[name for name in sys.argv[1:] if importlib.util.find_spec(name) is None]; print(', '.join(missing)); sys.exit(bool(missing))"
        result = subprocess.run([service.python, "-c", check, *modules], cwd=self.root, capture_output=True, text=True, timeout=30)
        if result.returncode:
            dependency_file = "ocr_service/requirements.txt" if service.name == "ocr" else "requirements.txt"
            raise RuntimeError(f"{service.name} environment is missing dependencies ({result.stdout.strip() or 'Python import failed'}). Install {dependency_file} using {service.python}")

    def spawn(self, service: Service) -> psutil.Process:
        environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1", APP_DEBUG="false")
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with (self.directory / f"{service.name}.log").open("ab", buffering=0) as log:
            log.write(f"\n[{datetime.now().astimezone().isoformat()}] START {service.name}\n".encode())
            child = subprocess.Popen([service.python, str(self.root / ENTRY_POINTS[service.name])], cwd=self.root, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        self.launched[child.pid] = child
        process = psutil.Process(child.pid)
        try:
            self.remember(process, service.name)
        except Exception:
            process.terminate()
            process.wait(timeout=10)
            raise
        return process

    def stop_process(self, process: psutil.Process, name: str) -> None:
        if not self.owns(process, name):
            raise RuntimeError(f"Refusing to stop an unrelated process: {process.pid}")
        children = process.children(recursive=True)
        process.terminate()
        _, alive = psutil.wait_procs([process, *children], timeout=self.timeout)
        for child in alive:
            if child != process:
                child.terminate()
        _, alive = psutil.wait_procs(alive, timeout=5)
        if alive:
            raise RuntimeError(f"{name} did not stop; remaining PIDs: {[item.pid for item in alive]}. State retained; inspect logs and retry")
        self.state_file(name).unlink(missing_ok=True)
        launched = self.launched.pop(process.pid, None)
        if launched:
            launched.wait(timeout=5)
        legacy = self.root / "logs" / "platform.pid"
        if name == "platform" and legacy.exists() and legacy.read_text().strip() == str(process.pid):
            legacy.unlink()
        print(f"STOPPED {name} (PID {process.pid})")

    def start(self, services: list[Service]) -> None:
        existing = {service.name: self.find(service.name) for service in services}
        for service in services:
            if existing[service.name] is None:
                if not self.port_available(service):
                    raise RuntimeError(f"Port {service.port} is occupied by an unmanaged service; no process was stopped")
                self.preflight(service)
        started = []
        try:
            for service in services:
                process = existing[service.name]
                if process is None:
                    print(f"STARTING {service.name}: {service.python}", flush=True)
                    process = self.spawn(service)
                    started.append((process, service.name))
                self.wait_ready(process, service)
                print(f"READY {service.name} (PID {process.pid}, port {service.port})")
        except BaseException:
            for process, name in reversed(started):
                try:
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        self.stop_process(process, name)
                    else:
                        self.state_file(name).unlink(missing_ok=True)
                        launched = self.launched.pop(process.pid, None)
                        if launched:
                            launched.wait(timeout=5)
                except Exception as error:
                    print(f"Cleanup failed: {error}", file=sys.stderr)
            raise

    def stop(self) -> None:
        errors = []
        for name in ("platform", "ocr"):
            try:
                process = self.find(name)
                if process:
                    self.stop_process(process, name)
                else:
                    print(f"STOPPED {name} (not running)")
            except Exception as error:
                errors.append(f"{name}: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Start/stop this project's platform and local OCR service")
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        with lifecycle_lock(PROJECT_ROOT):
            manager = ServiceManager(PROJECT_ROOT, args.timeout)
            if args.action == "stop":
                manager.stop()
            else:
                from dotenv import load_dotenv
                load_dotenv(PROJECT_ROOT / ".env", override=False)
                services = service_plan(PROJECT_ROOT, dict(os.environ), sys.executable)
                if not any(service.name == "ocr" for service in services):
                    print("Local OCR not managed (remote endpoint or START_LOCAL_OCR=false)")
                manager.start(services)
                print("All configured local services are ready. OCR weights load on first recognition request.")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"Service command failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
