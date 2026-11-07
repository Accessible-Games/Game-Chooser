"""Tracing UI and recorder messages, copied exactly from the plan."""

PROCMON_NOT_READY = "Process Monitor didn't start capturing within 60 seconds."
PROCMON_STOPPED = "Process Monitor stopped unexpectedly."
DISK_LOW = "The disk holding the traces folder has less than 2 GB free, so recording stopped."
SPLIT_FAILED = ("The capture couldn't be split into play sessions ({error}). "
                "It was kept; run the tracing build with --trace-split to try again.")
RECORDER_ERROR = "The recorder hit an unexpected error ({error}), so recording stopped."
PROCMON_STOP_SLOW = "Process Monitor didn't stop within 5 minutes, so the end of the capture may be missing."

TITLE_STARTING = " (tracing starting)"
TITLE_ON = " (tracing)"
TITLE_OFF = " (tracing off)"
DIALOG_CAPTION = "Tracing"
PROCMON_MISSING = ("Tracing is off: Process Monitor wasn't found at {path}. Games will run untraced. "
                   "Make the tracing build again; it fetches Process Monitor.")
DECLINED = ("Tracing is off: the administrator prompt was declined. "
            "Games will run untraced until Game Chooser is started again.")
START_FAILED = "Tracing is off: the recorder couldn't start ({error}). Games will run untraced."
RECORDER_FAILED = "Tracing has stopped: {error} Games launched from now on run untraced."
RECORDER_GONE = ("Tracing has stopped: the recorder closed unexpectedly. "
                 "Games launched from now on run untraced.")
LAUNCH_STARTING = "Tracing is still starting, so {title} would not be recorded. Launch it anyway, untraced?"
LAUNCH_OFF = "Tracing is off, so {title} would not be recorded. Launch it anyway, untraced?"
HOTKEY_FAILED = ("The mark key, Ctrl+Alt+Shift+M, couldn't be set up; another program may be using it. "
                 "Games are still recorded, but marks can't be added.")
