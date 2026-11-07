"""Tracing UI and recorder messages, copied exactly from the plan."""

PROCMON_NOT_READY = "Process Monitor didn't start capturing within 60 seconds."
PROCMON_STOPPED = "Process Monitor stopped unexpectedly."
DISK_LOW = "The disk holding the traces folder has less than 2 GB free, so recording stopped."
SPLIT_FAILED = ("The capture couldn't be split into play sessions ({error}). "
                "It was kept; run the tracing build with --trace-split to try again.")
RECORDER_ERROR = "The recorder hit an unexpected error ({error}), so recording stopped."
PROCMON_STOP_SLOW = "Process Monitor didn't stop within 5 minutes, so the end of the capture may be missing."
