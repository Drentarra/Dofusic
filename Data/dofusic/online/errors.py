from __future__ import annotations

from enum import Enum


class MediaErrorCode(str, Enum):
    AGE_RESTRICTED = 'age_restricted'
    BOT_CHALLENGE = 'bot_challenge'
    AUTH_FAILED = 'auth_failed'
    NETWORK = 'network'
    UNAVAILABLE = 'unavailable'
    TOOLING = 'tooling'
    TOO_LONG = 'too_long'
    UNKNOWN = 'unknown'


class MediaCacheError(RuntimeError):
    def __init__(self, message: str, *, code: MediaErrorCode = MediaErrorCode.UNKNOWN) -> None:
        super().__init__(message)
        self.code = code
