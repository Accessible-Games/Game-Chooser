"""Closing the traced games for --trace-stop --close-games: ask a process to close, or end it.

The recorder decides which processes (only those its live tree attributes to a session); this module only acts on
one process, named by pid and creation time so a reused pid is never touched.
"""
from typing import Protocol


class GameCloser(Protocol):
    def ask_close(self, pid: int, start: int) -> bool:
        """Ask the process to close, as closing its windows would; True when it had a window to ask."""

    def end(self, pid: int, start: int) -> bool:
        """End the process at once (unsaved work is lost); True when it was ended."""
