from __future__ import annotations


class TranscribeError(Exception):
    """A user-facing failure with a stable machine code and exit status."""

    def __init__(self, code: str, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class PayloadTooLargeError(TranscribeError):
    def __init__(self, message: str = "backend rejected the audio chunk as too large") -> None:
        super().__init__("payload_too_large", message, 6)


class AuthenticationError(TranscribeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 5)


class BackendError(TranscribeError):
    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(code, message, 6)
        self.status = status

