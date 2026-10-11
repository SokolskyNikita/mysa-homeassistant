"""Exceptions raised by the Mysa cloud client."""

from __future__ import annotations


class MysaError(Exception):
    """Base class for all Mysa cloud errors."""


class MysaConnectionError(MysaError):
    """The request didn't reach Mysa, timed out, or got a server error."""


class MysaRateLimitError(MysaConnectionError):
    """Mysa answered 429; back off before the next request."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Store the server's Retry-After hint, if it gave one."""
        super().__init__(message)
        self.retry_after = retry_after


class MysaAuthError(MysaError):
    """Sign-in or token refresh was refused; the user must sign in again."""


class MysaInvalidCredentialsError(MysaAuthError):
    """Wrong email or password."""


class MysaUnsupportedChallengeError(MysaAuthError):
    """Cognito asked for a step we don't support (MFA, new password, ...)."""

    def __init__(self, challenge: str) -> None:
        """Remember which challenge Cognito returned."""
        super().__init__(f"Unsupported sign-in challenge: {challenge}")
        self.challenge = challenge


class MysaNotFoundError(MysaError):
    """The resource doesn't exist (404), e.g. no schedule or no active hold."""


class MysaApiError(MysaError):
    """Mysa returned an unexpected status or body."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Keep the HTTP status for callers that care."""
        super().__init__(message)
        self.status = status
