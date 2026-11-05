"""A single-worker queue that runs install jobs one at a time and reports to a listener.

Two threads serve it: a worker that runs the engine, and a notifier that makes every listener call in order.
So a listener is always called from one thread (never the caller's, never the engine's), and a slow listener
can't stall an install. A UI listener marshals to its own thread once.
"""

import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from engine import messages
from engine.errors import Cancelled, InstallFailed
from engine.jobs import InstallEngine, InstallRequest, JobResult
from engine.messages import Progress


@dataclass
class Job:
    id: int
    request: InstallRequest
    title: str
    state: str = "queued"  # queued, running, done, failed or cancelled
    cancel_event: threading.Event = field(default_factory=threading.Event)


class QueueListener(Protocol):
    def job_started(self, job: Job) -> None: ...
    def job_progress(self, job: Job, progress: Progress) -> None: ...
    def job_finished(self, job: Job, result: JobResult) -> None: ...
    def job_failed(self, job: Job, message: str) -> None: ...
    def job_cancelled(self, job: Job) -> None: ...
    def queue_changed(self, waiting: int) -> None: ...


class InstallQueue:
    def __init__(self, engine_factory: Callable[[], InstallEngine], listener: QueueListener, log_path: Path):
        self._factory = engine_factory
        self._listener = listener
        self._log_path = Path(log_path)
        self._cond = threading.Condition(threading.Lock())
        self._waiting: deque[Job] = deque()
        self._events: deque[tuple[str, tuple]] = deque()  # listener calls waiting for the notifier
        self._worker_done = False
        self._current: Job | None = None
        self._next_id = 1
        self._stopping = False
        self._notifier = threading.Thread(target=self._run_notifier, name="install-queue-listener", daemon=True)
        self._worker = threading.Thread(target=self._run_worker, name="install-queue", daemon=True)
        self._notifier.start()
        self._worker.start()

    # Called from any thread.

    def submit(self, request: InstallRequest, title: str) -> Job:
        """Queue a job. A game already queued or running (whatever the action) returns its existing job."""
        with self._cond:
            if self._stopping:
                raise RuntimeError("The install queue has been shut down.")
            for job in (self._current, *self._waiting):
                # A job that is finishing or being cancelled no longer counts: the caller wants a new one.
                if (job is not None and job.request.catalog_id == request.catalog_id
                        and job.state in ("queued", "running") and not job.cancel_event.is_set()):
                    return job
            job = Job(self._next_id, request, title)
            self._next_id += 1
            self._waiting.append(job)
            self._events.append(("queue_changed", (len(self._waiting),)))
            self._cond.notify_all()
            return job

    def cancel(self, job_id: int) -> None:
        with self._cond:
            if self._current is not None and self._current.id == job_id:
                self._current.cancel_event.set()
                return
            for job in self._waiting:
                if job.id == job_id:
                    self._cancel_waiting(job)
                    self._cond.notify_all()
                    return

    def cancel_all(self) -> None:
        with self._cond:
            self._cancel_everything()
            self._cond.notify_all()

    def busy(self) -> bool:
        with self._cond:
            return self._current is not None

    def current(self) -> Job | None:
        with self._cond:
            return self._current

    def job_for(self, catalog_id: str) -> Job | None:
        """The game's queued or running job, or None. Unlike `submit`, a running job that is being
        cancelled still counts: it may still be changing the game's files and record."""
        with self._cond:
            for job in (self._current, *self._waiting):
                if job is not None and job.request.catalog_id == catalog_id and job.state in ("queued", "running"):
                    return job
            return None

    def waiting_count(self) -> int:
        with self._cond:
            return len(self._waiting)

    def shutdown(self, timeout: float) -> bool:
        """Cancel everything, stop both threads and wait up to `timeout` seconds. True if both stopped."""
        deadline = time.monotonic() + timeout
        with self._cond:
            self._stopping = True
            self._cancel_everything()
            self._cond.notify_all()
        self._worker.join(timeout)
        self._notifier.join(max(0.0, deadline - time.monotonic()))
        return not (self._worker.is_alive() or self._notifier.is_alive())

    # Callers hold the lock.

    def _cancel_waiting(self, job: Job) -> None:
        self._waiting.remove(job)
        job.state = "cancelled"
        job.cancel_event.set()
        self._events.append(("job_cancelled", (job,)))
        self._events.append(("queue_changed", (len(self._waiting),)))

    def _cancel_everything(self) -> None:
        for job in list(self._waiting):
            self._cancel_waiting(job)
        if self._current is not None:
            self._current.cancel_event.set()

    # The worker thread runs the engine; the notifier thread calls the listener.

    def _emit(self, method: str, *args) -> None:
        with self._cond:
            self._events.append((method, args))
            self._cond.notify_all()

    def _run_notifier(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._events or self._worker_done)
                if not self._events:
                    return
                method, args = self._events.popleft()
            try:
                getattr(self._listener, method)(*args)
            except Exception:
                self._log(f"The listener's {method} raised:\n" + traceback.format_exc())

    def _run_worker(self) -> None:
        try:
            while True:
                with self._cond:
                    self._cond.wait_for(lambda: self._waiting or self._stopping)
                    if not self._waiting:
                        return
                    job = self._waiting.popleft()
                    job.state = "running"
                    self._current = job
                    self._events.append(("queue_changed", (len(self._waiting),)))
                    self._events.append(("job_started", (job,)))
                    self._cond.notify_all()
                self._run_job(job)
        finally:
            with self._cond:
                self._worker_done = True
                self._cond.notify_all()

    def _run_job(self, job: Job) -> None:
        """Run `job`, then in one step set its final state, report it and free the worker slot."""
        state, event = "failed", None  # what a BaseException leaves behind
        try:
            engine = self._factory()
            result = engine.run(job.request, lambda p: self._emit("job_progress", job, p), job.cancel_event.is_set)
            state, event = "done", ("job_finished", (job, result))
        except Cancelled:
            state, event = "cancelled", ("job_cancelled", (job,))
        except InstallFailed as e:
            state, event = "failed", ("job_failed", (job, e.message))
        except Exception:
            self._log(f"Unexpected error in {job.request.action} of {job.request.catalog_id!r}:\n"
                      + traceback.format_exc())
            state, event = "failed", ("job_failed", (job, messages.UNEXPECTED_ERROR.format(title=job.title)))
        finally:
            with self._cond:
                job.state = state
                self._current = None
                if event is not None:
                    self._events.append(event)
                self._cond.notify_all()

    def _log(self, text: str) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {text.rstrip()}\n")
        except OSError:
            pass
