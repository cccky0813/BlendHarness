"""Sandbox for untrusted bpy code: one long-lived headless Blender process per session.

- Speed: Blender starts once (~8s) instead of once per execution.
- Reproducibility: every exec resets to an empty scene and runs the FULL script.
- Safety: a watchdog timeout kills a hung Blender (e.g. `while True:`) and starts a fresh one;
  a crashed Blender is detected and restarted too. The agent just sees an error message.
"""

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SERVER_SCRIPT = Path(__file__).resolve().parent / "server.py"
DEFAULT_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"


@dataclass
class ExecResult:
    ok: bool
    output: str = ""
    error: str = ""
    traceback: str = ""
    duration_s: float = 0.0
    restarted: bool = False   # True if Blender had to be killed/restarted during this call


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))  # port 0: the OS picks any free port
        return s.getsockname()[1]


class BlenderSession:
    def __init__(
        self,
        log_path: str | Path,
        exec_timeout_s: float = 120,
        startup_timeout_s: float = 90,
        blender_path: Optional[str] = None,
    ) -> None:
        self.blender_path = blender_path or os.environ.get("BLENDER_PATH", DEFAULT_BLENDER)
        self.log_path = Path(log_path)
        self.exec_timeout_s = exec_timeout_s
        self.startup_timeout_s = startup_timeout_s
        self.proc: Optional[subprocess.Popen] = None
        self.sock: Optional[socket.socket] = None
        self.stream = None
        self.startup_s = 0.0
        self.restarts = 0

    # ---------- lifecycle ----------
    def start(self) -> None:
        port = _free_port()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "ab")
        t0 = time.perf_counter()
        # Blender's output goes to a log file, NOT a pipe: an unread pipe fills up and
        # blocks Blender forever once its buffer (~64KB) is full.
        self.proc = subprocess.Popen(
            [self.blender_path, "-b", "--factory-startup", "--python", str(SERVER_SCRIPT), "--", "--port", str(port)],
            stdout=log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + self.startup_timeout_s
        while True:  # the server needs a few seconds before it accepts connections
            if self.proc.poll() is not None:
                raise RuntimeError(f"Blender exited during startup (code {self.proc.returncode}); see {self.log_path}")
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    self._kill()
                    raise TimeoutError(f"Blender did not start within {self.startup_timeout_s}s")
                time.sleep(0.2)
        self.stream = self.sock.makefile("rwb")
        self.startup_s = round(time.perf_counter() - t0, 2)

    def restart(self) -> None:
        self._kill()
        self.restarts += 1
        self.start()

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self._request({"cmd": "shutdown"}, timeout_s=5)
                self.proc.wait(timeout=5)
            except Exception:
                pass
        self._kill()

    def _kill(self) -> None:
        for closable in (self.stream, self.sock):
            try:
                if closable:
                    closable.close()
            except OSError:
                pass
        self.stream = self.sock = None
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.proc = None

    def __enter__(self) -> "BlenderSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- execution ----------
    def execute(self, code: str, reset: bool = True, timeout_s: Optional[float] = None) -> ExecResult:
        """Run bpy code. reset=True: start from an empty scene (full-script semantics)."""
        if self.proc is None or self.proc.poll() is not None:
            self.restart() if self.proc is not None else self.start()
        timeout = timeout_s or self.exec_timeout_s
        t0 = time.perf_counter()
        try:
            resp = self._request({"cmd": "exec", "code": code, "reset": reset}, timeout_s=timeout)
        except socket.timeout:
            self.restart()  # the only way to stop code stuck in Blender's main thread
            return ExecResult(False, error=f"TIMEOUT: code ran longer than {timeout}s; Blender was restarted "
                                           "with an empty scene. Avoid infinite loops and very heavy operations.",
                              duration_s=round(time.perf_counter() - t0, 2), restarted=True)
        except (ConnectionError, OSError, json.JSONDecodeError) as exc:
            self.restart()
            return ExecResult(False, error=f"BLENDER_CRASHED: {type(exc).__name__}; Blender was restarted "
                                           "with an empty scene.",
                              duration_s=round(time.perf_counter() - t0, 2), restarted=True)
        return ExecResult(
            ok=resp.get("ok", False),
            output=resp.get("output", ""),
            error=resp.get("error", ""),
            traceback=resp.get("traceback", ""),
            duration_s=round(time.perf_counter() - t0, 2),
        )

    def ping(self) -> str:
        return self._request({"cmd": "ping"}, timeout_s=10).get("output", "")

    def _request(self, payload: dict, timeout_s: float) -> dict:
        self.sock.settimeout(timeout_s)
        self.stream.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.stream.flush()
        line = self.stream.readline()
        if not line:  # empty read = the other side closed the connection (Blender died)
            raise ConnectionError("Blender closed the connection")
        return json.loads(line)
