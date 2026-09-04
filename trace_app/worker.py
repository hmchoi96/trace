"""Background job runner. Hunts take minutes, so no request waits on them."""

from __future__ import annotations

import threading
import time

from . import db, service


class Worker:
    def __init__(self, db_path: str | None = None, interval: float = 1.0) -> None:
        self.db_path = db_path
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="trace-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        conn = db.connect(self.db_path)
        while not self._stop.is_set():
            try:
                job = service.claim_next_job(conn)
            except Exception:
                job = None
            if not job:
                self._stop.wait(self.interval)
                continue
            try:
                service.run_job(conn, job)
            except Exception as exc:  # a job must never kill the worker
                try:
                    service.finish_job(conn, job["id"], error=str(exc))
                except Exception:
                    pass


def run_forever(db_path: str | None = None) -> None:
    conn = db.connect(db_path)
    from . import profiles

    profiles.seed_builtin_profiles(conn)
    print("Trace worker running. Ctrl-C to stop.")
    while True:
        job = service.claim_next_job(conn)
        if not job:
            time.sleep(1)
            continue
        print(f"  job {job['type']} {job['id']}")
        service.run_job(conn, job)


if __name__ == "__main__":
    run_forever()
