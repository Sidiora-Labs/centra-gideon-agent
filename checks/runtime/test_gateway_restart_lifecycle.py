import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def test_restart_runs_shutdown_hooks_and_stop_wins(tmp_path):
    home = tmp_path / "gideon-home"
    observer = tmp_path / "observer"
    observer.mkdir()
    trace = tmp_path / "restart-events.jsonl"
    observer.joinpath("sitecustomize.py").write_text(
        """
import dis
import json
import os
import signal
import sys

trace = os.environ["GIDEON_RESTART_TRACE"]

def emit(event, **fields):
    row = json.dumps({"event": event, **fields}, separators=(",", ":")).encode()
    fd = os.open(trace, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, row + b"\\n")
    finally:
        os.close(fd)

try:
    with open(trace, "rb") as stream:
        boot = sum(1 for line in stream if b'"event":"start"' in line) + 1
except FileNotFoundError:
    boot = 1
emit("start", pid=os.getpid(), argv=sys.argv)

def audit(event, args):
    if event == "os.exec":
        path, argv, env = args
        emit(
            "exec",
            pid=os.getpid(),
            executable=os.fsdecode(path),
            argv=[os.fsdecode(value) for value in argv],
            auth_mode=(env or {}).get("GIDEON_AUTH_MODE"),
        )

sys.addaudithook(audit)
stop_sent = False

def observe(frame, event, arg):
    global stop_sent
    module = frame.f_globals.get("__name__")
    name = frame.f_code.co_name
    if event != "return":
        return
    if module == "gideon.engine.lifecycle" and name == "retire":
        opcode = dis.opname[frame.f_code.co_code[frame.f_lasti]]
        if opcode in {"RETURN_VALUE", "RETURN_CONST"}:
            emit("retire-finished", pid=os.getpid(), boot=boot)
    if module == "gideon.engine.gateway_base" and name == "unpublish":
        emit("unpublished", pid=os.getpid(), boot=boot)
        if boot == 2 and not stop_sent:
            stop_sent = True
            emit("stop-sent", pid=os.getpid(), boot=boot)
            os.kill(os.getpid(), signal.SIGTERM)

def enable_observer(signum, frame):
    sys.setprofile(observe)

signal.signal(signal.SIGUSR1, enable_observer)
""",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[2]
    runtime = repo_root / "runtime"
    child_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "GIDEON_HOME": str(home),
        "GIDEON_AUTH_MODE": "none",
        "GIDEON_RESTART_TRACE": str(trace),
        "PYTHONPATH": os.pathsep.join((str(observer), str(runtime))),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    command = [sys.executable, "-m", "gideon", "gateway", "--test-mode", "--no-crons"]
    process = subprocess.Popen(
        command,
        cwd=tmp_path,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    output: list[str] = []
    lines: queue.Queue[str | None] = queue.Queue()
    ready: list[dict] = []
    eof = threading.Event()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if line.startswith("GIDEON_READY:"):
                try:
                    ready.append(json.loads(line.removeprefix("GIDEON_READY:")))
                except json.JSONDecodeError:
                    output.append("GIDEON_READY:<invalid payload>")
                lines.put("GIDEON_READY:<redacted>\n")
            else:
                output.append(line)
                lines.put(line)
        eof.set()
        lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def wait_ready(count: int, timeout: float = 90.0) -> dict:
        deadline = time.monotonic() + timeout
        while len(ready) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"gateway did not emit readiness {count}; output={''.join(output)}"
                )
            try:
                line = lines.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                if process.poll() is not None:
                    raise AssertionError(
                        f"gateway exited {process.returncode} before readiness {count}; "
                        f"output={''.join(output)}"
                    )
                continue
            if line is None and eof.is_set():
                raise AssertionError(
                    f"gateway output closed before readiness {count}; "
                    f"output={''.join(output)}"
                )
        return ready[count - 1]

    def post_restart(port: int) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/restart",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Gideon-API-Version": "1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.load(response)
                assert response.status == 200
                assert result == {"ok": True, "status": "restarting"}
        except urllib.error.URLError as error:
            raise AssertionError(f"restart endpoint was unavailable: {error}") from error

    def trace_rows() -> list[dict]:
        if not trace.exists():
            return []
        return [json.loads(row) for row in trace.read_text().splitlines()]

    try:
        first = wait_ready(1)
        assert first["pid"] == process.pid
        assert first["home"] == str(home)
        os.kill(process.pid, signal.SIGUSR1)
        post_restart(first["port"])

        second = wait_ready(2)
        assert second["pid"] == first["pid"] == process.pid
        assert second["home"] == first["home"] == str(home)
        assert second["port"] > 0
        os.kill(process.pid, signal.SIGUSR1)

        rows = trace_rows()
        assert [row["event"] for row in rows].count("retire-finished") >= 1
        assert [row["event"] for row in rows].count("exec") == 1
        first_retire = next(
            index for index, row in enumerate(rows) if row["event"] == "retire-finished"
        )
        first_exec = next(index for index, row in enumerate(rows) if row["event"] == "exec")
        second_start = [
            index for index, row in enumerate(rows) if row["event"] == "start"
        ][1]
        assert first_retire < first_exec < second_start
        executed = rows[first_exec]
        assert executed["pid"] == process.pid
        assert executed["executable"] == sys.executable
        assert executed["argv"] == command
        assert executed["auth_mode"] == "none"

        proc_argv = [
            os.fsdecode(value)
            for value in Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")
            if value
        ]
        assert proc_argv == command

        post_restart(second["port"])
        return_code = process.wait(timeout=30)
        assert return_code == 0, f"gateway exited {return_code}; output={''.join(output)}"
        rows = trace_rows()
        assert [row["event"] for row in rows].count("stop-sent") == 1
        assert [row["event"] for row in rows].count("exec") == 1
        assert [row["event"] for row in rows].count("start") == 2
        assert [row["event"] for row in rows].count("retire-finished") >= 2
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        reader.join(timeout=2)
