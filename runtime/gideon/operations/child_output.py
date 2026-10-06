"""Bounded, masked output of supervised app processes."""

from __future__ import annotations

import logging
import os
import selectors
import threading
import time
from collections import deque
from datetime import datetime, timezone

from gideon.core.log_sinks import sanitize

logger = logging.getLogger(__name__)
STDOUT, STDERR = "stdout", "stderr"
TAIL_LINES, LINE_MAX_CHARS = 40, 1000
_READ_LIMIT = 8192


class ChildOutput:
    def __init__(self, *, app: str, process: str, pid: int, env=None):
        self.app, self.process, self.pid = app, process, pid
        from gideon.automation.workflows.secrets import matches_secret_hint

        self._secrets = tuple(
            value
            for name, value in (env or {}).items()
            if isinstance(value, str) and len(value) >= 8 and matches_secret_hint(name)
        )
        self._lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=TAIL_LINES)
        self._tokens, self._last = 200.0, time.monotonic()
        self._omitted = 0
        self._stopped = False
        self._exit: dict[str, object] | None = None
        self.finished = threading.Event()

    def line(self, stream: str, text: str) -> None:
        if not text.strip():
            return
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        text = sanitize(text)
        text = "".join(
            char if char == "\t" or ord(char) >= 32 else f"\\x{ord(char):02x}"
            for char in text
        )
        if len(text) > LINE_MAX_CHARS:
            text = text[:LINE_MAX_CHARS] + " [… cut]"
        with self._lock:
            self._lines.append(text)
            now = time.monotonic()
            self._tokens = min(200.0, self._tokens + (now - self._last) * 2)
            self._last = now
            if self._tokens < 1:
                self._omitted += 1
                return
            self._tokens -= 1
        logger.log(
            logging.WARNING if stream == STDERR else logging.INFO,
            "app %s %s (pid %d) %s: %s",
            self.app,
            self.process,
            self.pid,
            stream,
            text,
        )

    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def stopping(self) -> None:
        with self._lock:
            self._stopped = True

    def ended(self, code: int) -> None:
        with self._lock:
            if self._exit is not None:
                return
            self._exit = {
                "pid": self.pid,
                "exitCode": code,
                "ended": (
                    f"ended by signal {-code}"
                    if code < 0
                    else f"exited with code {code}"
                ),
                "endedAt": datetime.now(timezone.utc).isoformat(),
            }
            omitted, self._omitted = self._omitted, 0
            stopped = self._stopped
        if omitted:
            logger.warning(
                "app %s %s (pid %d): %d output lines omitted from log",
                self.app,
                self.process,
                self.pid,
                omitted,
            )
        if not stopped:
            logger.log(
                logging.WARNING if code else logging.INFO,
                "app %s %s (pid %d) exited with code %d",
                self.app,
                self.process,
                self.pid,
                code,
            )

    def report(self) -> dict | None:
        with self._lock:
            if self._exit is None or self._stopped:
                return None
            result = dict(self._exit)
            result["lines"] = list(self._lines)
        result["cause"] = self._lines[-1] if self._lines else ""
        return result


def relay(proc, output: ChildOutput, *, streams=(STDOUT, STDERR)) -> None:
    pipes = {name: getattr(proc, name, None) for name in streams}
    pipes = {name: pipe for name, pipe in pipes.items() if pipe is not None}
    if pipes:
        threading.Thread(
            target=_drain,
            args=(proc, output, pipes),
            daemon=True,
            name=f"app-output-{output.app}-{output.pid}",
        ).start()


def _drain(proc, output, pipes) -> None:
    pending = {name: bytearray() for name in pipes}
    skipped = {name: False for name in pipes}
    selector = selectors.DefaultSelector()
    ended_at = None
    try:
        for name, pipe in pipes.items():
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe.fileno(), selectors.EVENT_READ, name)
        while selector.get_map():
            for key, _event in selector.select(0.1):
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                name = key.data
                parts = chunk.split(b"\n")
                for index, part in enumerate(parts):
                    room = max(0, _READ_LIMIT - len(pending[name]))
                    pending[name].extend(part[:room])
                    skipped[name] = skipped[name] or len(part) > room
                    if index < len(parts) - 1 or not chunk:
                        raw = bytes(pending[name]).decode("utf-8", "replace")
                        if skipped[name]:
                            raw = (
                                raw.rsplit(" ", 1)[0]
                                if " " in raw
                                else "[long output line withheld]"
                            )
                            raw += " [… cut]"
                        output.line(name, raw.rstrip("\r"))
                        pending[name].clear()
                        skipped[name] = False
                if not chunk:
                    selector.unregister(key.fd)
            code = proc.poll()
            if code is not None:
                if ended_at is None:
                    ended_at = time.monotonic()
                if time.monotonic() - ended_at > 1:
                    break
        code = proc.poll()
        deadline = time.monotonic() + 5
        while code is None and time.monotonic() < deadline:
            time.sleep(0.05)
            code = proc.poll()
        if code is not None:
            output.ended(code)
    except (OSError, ValueError):
        logger.warning(
            "app %s %s output stream could not be read", output.app, output.process
        )
    finally:
        selector.close()
        for pipe in pipes.values():
            try:
                pipe.close()
            except OSError:
                pass
        output.finished.set()
