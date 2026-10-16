"""긴 CLI 작업의 현재 단계와 경과 시간을 즉시 출력한다."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from math import ceil
from threading import Event, Thread
from time import monotonic

HEARTBEAT_SECONDS = 30


def log(message: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def format_duration(seconds: float) -> str:
    minutes, seconds = divmod(max(0, ceil(seconds)), 60)
    return f"{minutes:,}분 {seconds:02d}초"


@contextmanager
def progress(label: str) -> Iterator[Callable[[str], None]]:
    started, stopped = monotonic(), Event()
    current = label
    current_started = started

    def report(message: str) -> None:
        nonlocal current, current_started
        current = message
        current_started = monotonic()
        log(message)

    def heartbeat() -> None:
        while not stopped.wait(HEARTBEAT_SECONDS):
            log(f"{current} · 실행 중 · 현재 작업 경과 {format_duration(monotonic() - current_started)}"
                f" · 전체 경과 {format_duration(monotonic() - started)}")

    report(f"{label}: 시작")
    worker = Thread(target=heartbeat, daemon=True)
    worker.start()
    completed = False
    try:
        yield report
        completed = True
    finally:
        stopped.set()
        worker.join()
        status = "완료" if completed else "중단"
        log(f"{label}: {status} · 경과 {int(monotonic() - started):,}초")
