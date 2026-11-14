"""FILETIME clock: 100 ns ticks since 1601-01-01 UTC, the same clock Process Monitor stamps its events with."""
import time
from datetime import datetime, timedelta, timezone

TICKS_PER_SECOND = 10_000_000
_EPOCH_DIFF = 116444736000000000  # 1601-01-01 to 1970-01-01, in ticks


def now_filetime() -> int:
    # Python 3.13+ reads time on Windows with GetSystemTimePreciseAsFileTime, the clock Process Monitor uses
    return (time.time_ns() // 100) + _EPOCH_DIFF


def _to_utc(ft: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=(ft - _EPOCH_DIFF) // 10)


def filetime_to_iso(ft: int) -> str:
    return _to_utc(ft).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def filetime_to_local_stamp(ft: int) -> str:
    return _to_utc(ft).astimezone().strftime("%Y%m%d-%H%M%S")
