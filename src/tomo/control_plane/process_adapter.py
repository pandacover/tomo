from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from tomo.config import settings
from tomo.cross_gateway_bridge import process_is_running

CONTROL_API_PID_FILENAME = "control_api.pid"
CONTROL_API_LOG_FILENAME = "control_api.log"


def pid_path() -> Path:
    return settings.data_dir / CONTROL_API_PID_FILENAME


def log_path() -> Path:
    return settings.data_dir / CONTROL_API_LOG_FILENAME


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def remove_pid(path: Path) -> None:
    if path.exists():
        path.unlink()


def stop_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], check=False, capture_output=True)
        return
    os.kill(pid, signal.SIGTERM)


def run_control_api(*, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        "tomo.control_plane.http_adapter:app",
        host=host or settings.control_api_host,
        port=port or settings.control_api_port,
        log_level="info",
    )


def start_control_api(*, host: str | None = None, port: int | None = None) -> None:
    path = pid_path()
    existing = read_pid(path)
    if existing and process_is_running(existing):
        print(f"Tomo control API is already running with PID {existing}.")
        return

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path(), "a", encoding="utf-8")
    host_value = host or settings.control_api_host
    port_value = port or settings.control_api_port
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "tomo.control_plane.http_adapter:app",
        "--host",
        host_value,
        "--port",
        str(port_value),
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=Path.cwd(),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    write_pid(path, process.pid)
    print(f"Tomo control API started with PID {process.pid}.")
    print(f"Listening on http://{host_value}:{port_value}")


def stop_control_api() -> None:
    path = pid_path()
    pid = read_pid(path)
    if pid is None:
        print("Tomo control API is not running.")
        return
    if not process_is_running(pid):
        remove_pid(path)
        print("Tomo control API was not running; removed stale PID file.")
        return
    stop_process(pid)
    time.sleep(0.5)
    remove_pid(path)
    print("Tomo control API stopped.")


def restart_control_api(*, host: str | None = None, port: int | None = None) -> None:
    stop_control_api()
    start_control_api(host=host, port=port)
