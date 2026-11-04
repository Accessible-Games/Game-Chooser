"""Exceptions raised by the install engine."""


class EngineError(Exception):
    """Base class for every error the engine raises on purpose."""


class Cancelled(EngineError):
    pass


class BlockedHost(EngineError):
    pass


class DownloadError(EngineError):
    pass


class NoVerifiedSource(EngineError):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class ExtractError(EngineError):
    pass


class StepError(EngineError):
    pass


class ElevationDeclined(EngineError):
    pass


class DependencyError(EngineError):
    pass


class UnsafePath(ValueError, EngineError):
    pass


class InstallFailed(EngineError):
    """An install that stopped; `message` is the plain-words text shown to the player."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
