"""Sign-in and token handling for Mysa's AWS Cognito user pool.

Normally only the refresh token is stored. ID tokens last an hour and are
renewed shortly before they expire. Mysa doesn't rotate refresh tokens, so a
refresh token stops working after its lifetime (about a month); callers that
were given the email and password sign in again by themselves at that point.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    COGNITO_CLIENT_ID,
    COGNITO_URL,
    COGNITO_USER_POOL_ID,
    REQUEST_TIMEOUT,
    TOKEN_REFRESH_MARGIN,
)
from .exceptions import (
    MysaAuthError,
    MysaConnectionError,
    MysaInvalidCredentialsError,
    MysaRateLimitError,
    MysaUnsupportedChallengeError,
)
from .srp import CognitoSrp

_LOGGER = logging.getLogger(__name__)

_INVALID_CREDENTIAL_ERRORS = frozenset(
    {"NotAuthorizedException", "UserNotFoundException"}
)
_RATE_LIMIT_ERRORS = frozenset({"TooManyRequestsException", "LimitExceededException"})


@dataclass(slots=True)
class Tokens:
    """A set of Cognito tokens."""

    id_token: str
    refresh_token: str
    expires_at: float


def jwt_claims(token: str) -> dict[str, Any]:
    """Read a JWT's claims without verifying it (the token is only sent back)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as err:
        msg = "Malformed token"
        raise MysaAuthError(msg) from err
    if not isinstance(claims, dict):
        msg = "Malformed token"
        raise MysaAuthError(msg)
    return claims


class MysaAuth:
    """Holds the tokens for one Mysa account and keeps the ID token fresh."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str | None = None,
        *,
        user_agent: str,
        on_refresh_token: Callable[[str], None] | None = None,
        email: str | None = None,
        password: str | None = None,
    ) -> None:
        """Start from a stored refresh token, or from nothing before sign-in.

        With `email` and `password`, a refresh token that stops working (expired
        or revoked) is replaced by signing in again instead of failing.
        """
        self._session = session
        self._user_agent = user_agent
        self._on_refresh_token = on_refresh_token
        self._tokens: Tokens | None = None
        self._refresh_token = refresh_token
        self._credentials = (email, password) if email and password else None
        self._lock = asyncio.Lock()

    @property
    def refresh_token(self) -> str | None:
        """The current refresh token (the only thing worth storing)."""
        return self._refresh_token

    @property
    def claims(self) -> dict[str, Any]:
        """Claims of the current ID token (empty before the first token)."""
        return jwt_claims(self._tokens.id_token) if self._tokens else {}

    async def _cognito(self, target: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "X-Amz-Target": f"AWSCognitoIdentityProviderService.{target}",
            "Content-Type": "application/x-amz-json-1.1",
            "User-Agent": self._user_agent,
        }
        try:
            async with self._session.post(
                COGNITO_URL,
                data=json.dumps(body),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (TimeoutError, aiohttp.ClientError) as err:
            msg = f"Cognito {target} failed: {err}"
            raise MysaConnectionError(msg) from err
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        if status == 200 and isinstance(data, dict):
            return data
        error = str(data.get("__type", "")).rsplit("#", 1)[-1] if data else ""
        message = data.get("message", text[:200]) if data else text[:200]
        if status >= 500:
            msg = f"Cognito {target} returned {status}"
            raise MysaConnectionError(msg)
        if error in _RATE_LIMIT_ERRORS:
            msg = f"Cognito {target}: {message}"
            raise MysaRateLimitError(msg)
        if error in _INVALID_CREDENTIAL_ERRORS:
            msg = f"Cognito {target}: {message}"
            raise MysaInvalidCredentialsError(msg)
        msg = f"Cognito {target} failed ({status} {error}): {message}"
        raise MysaAuthError(msg)

    def _store(self, result: dict[str, Any], refresh_token: str | None) -> Tokens:
        id_token = result.get("IdToken")
        if not isinstance(id_token, str):
            msg = "Cognito returned no ID token"
            raise MysaAuthError(msg)
        new_refresh = result.get("RefreshToken")
        if isinstance(new_refresh, str) and new_refresh:
            refresh_token = new_refresh
        if not refresh_token:
            msg = "Cognito returned no refresh token"
            raise MysaAuthError(msg)
        expires_in = result.get("ExpiresIn")
        exp = jwt_claims(id_token).get("exp")
        if isinstance(exp, int | float):
            expires_at = float(exp)
        elif isinstance(expires_in, int | float):
            expires_at = time.time() + float(expires_in)
        else:
            expires_at = time.time() + 3600
        self._tokens = Tokens(id_token, refresh_token, expires_at)
        if refresh_token != self._refresh_token:
            self._refresh_token = refresh_token
            if self._on_refresh_token is not None:
                self._on_refresh_token(refresh_token)
        return self._tokens

    async def sign_in(self, email: str, password: str) -> Tokens:
        """Sign in with email and password (SRP; the password never leaves here)."""
        result = await self._srp_sign_in(email, password)
        async with self._lock:
            return self._store(result, None)

    async def _srp_sign_in(self, email: str, password: str) -> dict[str, Any]:
        """Run the SRP exchange; return Cognito's AuthenticationResult."""
        srp = CognitoSrp(email, password, COGNITO_USER_POOL_ID)
        start = await self._cognito(
            "InitiateAuth",
            {
                "AuthFlow": "USER_SRP_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": srp.auth_parameters(),
                "ClientMetadata": {},
            },
        )
        challenge = start.get("ChallengeName")
        if challenge != "PASSWORD_VERIFIER":
            raise MysaUnsupportedChallengeError(str(challenge))
        body: dict[str, Any] = {
            "ChallengeName": "PASSWORD_VERIFIER",
            "ClientId": COGNITO_CLIENT_ID,
            "ChallengeResponses": srp.challenge_responses(
                start.get("ChallengeParameters") or {}
            ),
        }
        if start.get("Session"):
            body["Session"] = start["Session"]
        answer = await self._cognito("RespondToAuthChallenge", body)
        result = answer.get("AuthenticationResult")
        if not isinstance(result, dict):
            raise MysaUnsupportedChallengeError(str(answer.get("ChallengeName")))
        return result

    async def _refresh(self) -> Tokens:
        if not self._refresh_token:
            if self._credentials is None:
                msg = "Not signed in"
                raise MysaAuthError(msg)
            return self._store(await self._srp_sign_in(*self._credentials), None)
        _LOGGER.debug("Refreshing the Mysa ID token")
        try:
            result = await self._cognito(
                "GetTokensFromRefreshToken",
                {"ClientId": COGNITO_CLIENT_ID, "RefreshToken": self._refresh_token},
            )
        except MysaAuthError as err:
            if self._credentials is None:
                raise
            _LOGGER.info(
                "The Mysa sign-in stopped working (%s); signing in again with the "
                "saved password",
                err,
            )
            return self._store(await self._srp_sign_in(*self._credentials), None)
        auth = result.get("AuthenticationResult")
        if not isinstance(auth, dict):
            msg = "Token refresh returned no tokens"
            raise MysaAuthError(msg)
        return self._store(auth, self._refresh_token)

    async def async_get_id_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid ID token, refreshing it only when it's about to expire."""
        async with self._lock:
            tokens = self._tokens
            if (
                force_refresh
                or tokens is None
                or tokens.expires_at - TOKEN_REFRESH_MARGIN <= time.time()
            ):
                tokens = await self._refresh()
            return tokens.id_token
