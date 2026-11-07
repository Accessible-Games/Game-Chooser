import re
import time

from tracing.clock import TICKS_PER_SECOND, filetime_to_iso, filetime_to_local_stamp, now_filetime


def test_iso_and_stamp():
    assert filetime_to_iso(116444736000000000) == "1970-01-01T00:00:00.000000Z"
    assert re.match(r"^\d{8}-\d{6}$", filetime_to_local_stamp(116444736000000000))


def test_now_is_close_to_time_time():
    expected = time.time_ns() // 100 + 116444736000000000
    assert abs(now_filetime() - expected) < 5 * TICKS_PER_SECOND
