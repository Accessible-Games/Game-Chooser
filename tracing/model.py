"""Frozen records shared by the tracing modules."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    parent_pid: int
    start: int
    end: int | None
    image_path: str
    command_line: str
    virtualized: bool
    is_64bit: bool

    @property
    def key(self) -> tuple[int, int]:
        return (self.pid, self.start)


@dataclass(frozen=True)
class RawEvent:
    time: int
    pid: int
    proc_start: int
    operation: str
    path: str
    result: str
    details: dict

    @property
    def proc_key(self) -> tuple[int, int]:
        return (self.pid, self.proc_start)


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    root_pid: int
    root_start: int | None
    launch_before: int
    launch_after: int
    game: dict  # title, catalog_id, library_id, launch_path, exe, game_dir
    env: dict  # windows, local_appdata, user_profile, app_exe, app_exe_mtime, compiled
    marks: tuple[int, ...]

    def to_json(self) -> dict:
        return {
            "session_id": self.session_id, "root_pid": self.root_pid, "root_start": self.root_start,
            "launch_before": self.launch_before, "launch_after": self.launch_after,
            "game": dict(self.game), "env": dict(self.env), "marks": list(self.marks),
        }

    @classmethod
    def from_json(cls, d: dict) -> "SessionInfo":
        return cls(d["session_id"], d["root_pid"], d["root_start"], d["launch_before"], d["launch_after"],
                   dict(d["game"]), dict(d["env"]), tuple(d["marks"]))
