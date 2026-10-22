"""U36: the chat probe in its own OS process, so the load generator's threads cannot steal its GIL.

The driver's 16 starter threads, the sampler and the drain poller share one interpreter. A chat probe in that same
interpreter measured the generator's GIL contention along with the stack. This module runs the probe (baseline and
loaded sampling, with the same schedule as before) in a child process and sends the numbers back over a pipe.

Protocol (parent -> child, child -> parent), one message each way:
  ready                          child imported everything and is idle
  ("baseline", n)                -> ("baseline", [ms, ...])   n sequential turns, no load
  ("start_load", interval_s)     -> ("started",)              a thread: one turn every interval_s until stopped
  ("stop_load",)                 -> ("loaded", samples, errors, submitted)   samples are (monotonic start, ms)
  ("close",)                     child exits
Any failure in the child comes back as ("error", text) and is raised in the parent. ``time.monotonic`` is one
system-wide clock on Linux and macOS, so the child's start stamps compare with the parent's phase boundaries.
"""
import multiprocessing
import threading
import time
from dataclasses import dataclass
from typing import Callable


class ChatLoad:
    """A steady chat load: one turn at a time, one every ``interval`` seconds, until stopped."""

    def __init__(self, measure: Callable[[], float], interval: float):
        self.measure, self.interval = measure, interval
        self.samples: list[tuple[float, float]] = []  # (monotonic start, first-token ms)
        self.errors: list[str] = []
        self.submitted = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.submitted += 1
                self.samples.append((started, self.measure()))
            except Exception as e:  # noqa: BLE001 - recorded; the gate fails on any chat error (a dropped turn would hide a stall)
                self.errors.append(repr(e)[:200])
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - started)))

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=90)


@dataclass
class LoadedResult:
    samples: list[tuple[float, float]]
    errors: list[str]
    submitted: int


def _child(conn, make_measure: Callable, arg) -> None:
    """The probe process: build the measure function (imports happen here), then serve commands."""
    load = None
    try:
        measure = make_measure(arg)
        conn.send(("ready",))
        while True:
            msg = conn.recv()
            cmd = msg[0]
            if cmd == "baseline":
                conn.send(("baseline", [measure() for _ in range(msg[1])]))
            elif cmd == "start_load":
                load = ChatLoad(measure, msg[1])
                load.start()
                conn.send(("started",))
            elif cmd == "stop_load":
                load.stop()
                conn.send(("loaded", load.samples, load.errors, load.submitted))
                load = None
            elif cmd == "close":
                return
    except (EOFError, KeyboardInterrupt):
        return
    except BaseException as e:  # noqa: BLE001 - reported to the parent, which raises it
        try:
            conn.send(("error", repr(e)[:500]))
        except Exception:  # noqa: BLE001
            pass


class ChatProbe:
    """Parent-side handle. ``make_measure(arg)`` runs in the child and returns a ``() -> ms`` callable; both it and
    ``arg`` must be picklable (a module-level function and plain data)."""

    def __init__(self, make_measure: Callable, arg, start_timeout: float = 120.0, reply_timeout: float = 600.0):
        ctx = multiprocessing.get_context("spawn")  # not fork: a clean interpreter, no inherited threads or sockets
        self._conn, child_conn = ctx.Pipe()
        self._proc = ctx.Process(target=_child, args=(child_conn, make_measure, arg), daemon=True, name="chat-probe")
        self._reply_timeout = reply_timeout
        self._proc.start()
        child_conn.close()
        self._expect("ready", start_timeout)

    def _expect(self, kind: str, timeout: float | None = None) -> tuple:
        deadline = time.monotonic() + (timeout or self._reply_timeout)
        while True:
            if self._conn.poll(0.2):
                try:
                    msg = self._conn.recv()
                except EOFError:
                    raise RuntimeError("chat probe process closed its pipe") from None
                if msg[0] == "error":
                    raise RuntimeError(f"chat probe failed: {msg[1]}")
                assert msg[0] == kind, f"chat probe sent {msg[0]!r}, expected {kind!r}"
                return msg
            if not self._proc.is_alive() and not self._conn.poll(0):
                raise RuntimeError(f"chat probe process died (exit code {self._proc.exitcode})")
            if time.monotonic() > deadline:
                raise TimeoutError(f"chat probe did not answer {kind!r} in time")

    def baseline(self, n: int) -> list[float]:
        self._conn.send(("baseline", n))
        return self._expect("baseline")[1]

    def start_load(self, interval: float) -> None:
        self._conn.send(("start_load", interval))
        self._expect("started")

    def stop_load(self) -> LoadedResult:
        self._conn.send(("stop_load",))
        _, samples, errors, submitted = self._expect("loaded")
        return LoadedResult(samples, errors, submitted)

    def close(self) -> None:
        try:
            self._conn.send(("close",))
        except Exception:  # noqa: BLE001
            pass
        self._proc.join(timeout=10)
        if self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(timeout=5)
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
