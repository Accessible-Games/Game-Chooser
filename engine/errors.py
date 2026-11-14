"""Exceptions raised by the install engine."""


class EngineError(Exception):
    """Base class for every error the engine raises on purpose."""


class Cancelled(EngineError):
    pass


def check_cancel(cancel) -> None:
    if cancel():
        raise Cancelled()


class BlockedHost(EngineError):
    pass


class DownloadError(EngineError):
    pass


class DownloadGone(DownloadError):
    """The server answered that the file doesn't exist (HTTP 404 or 410): trying again won't help."""


class NoVerifiedSource(EngineError):
    """No source gave a file that verified.

    `downloaded` is True when at least one file did download but didn't match; False means every
    attempt failed to download at all (unreachable, blocked or gone). `gone` is True when at least one
    address answered that the file doesn't exist and none failed in a way that may only be for now.
    """

    def __init__(self, reasons: list[str], downloaded: bool = False, gone: bool = False):
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.downloaded = downloaded
        self.gone = gone


class ExtractError(EngineError):
    pass


class StepError(EngineError):
    pass


class ElevationDeclined(EngineError):
    pass


class UnsafePath(ValueError, EngineError):
    pass


class InstallFailed(EngineError):
    """An install that stopped; `message` is the plain-words text shown to the player."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
