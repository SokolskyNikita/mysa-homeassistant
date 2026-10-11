"""Secure Remote Password (SRP) math for AWS Cognito's USER_SRP_AUTH flow.

Mysa signs users in through Cognito with SRP, so the password itself never
leaves this machine. This module only does the math; `auth.py` talks to Cognito.
It follows the algorithm of AWS's amazon-cognito-identity-js AuthenticationHelper.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import hmac
import secrets

# 3072-bit group from RFC 5054 / RFC 3526, as used by Cognito.
N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64"
    "ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6B"
    "F12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB31"
    "43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
G_HEX = "2"
_INFO_BITS = b"Caldera Derived Key\x01"
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

BIG_N = int(N_HEX, 16)
G = int(G_HEX, 16)


def _hash_hex(data: bytes) -> str:
    """SHA-256 as a 64-character hex string."""
    return hashlib.sha256(data).hexdigest().rjust(64, "0")


def _hex_hash(hex_string: str) -> str:
    """SHA-256 of the bytes a hex string encodes."""
    return _hash_hex(bytes.fromhex(hex_string))


def pad_hex(value: int | str) -> str:
    """Hex-encode so the value reads as a positive number with whole bytes.

    Matches AuthenticationHelper.padHex: an odd length gets one leading zero and
    a value whose top bit is set gets a leading 00 byte.
    """
    hex_str = value if isinstance(value, str) else f"{value:x}"
    if len(hex_str) % 2 == 1:
        return f"0{hex_str}"
    if hex_str[0] in "89ABCDEFabcdef":
        return f"00{hex_str}"
    return hex_str


def _hkdf(ikm: bytes, salt: bytes) -> bytes:
    """Single-block HKDF-SHA256, truncated to 16 bytes."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, _INFO_BITS, hashlib.sha256).digest()[:16]


K = int(_hex_hash("00" + N_HEX + "0" + G_HEX), 16)


def cognito_timestamp(now: datetime) -> str:
    """Format a UTC time the way Cognito wants it: 'Mon Jan 2 03:04:05 UTC 2026'."""
    now = now.astimezone(UTC)
    return (
        f"{_WEEKDAYS[now.weekday()]} {_MONTHS[now.month - 1]} {now.day} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d} UTC {now.year}"
    )


class CognitoSrp:
    """One SRP sign-in attempt. Create a new instance for every attempt."""

    def __init__(
        self,
        username: str,
        password: str,
        pool_id: str,
        small_a: int | None = None,
    ) -> None:
        """Pick the random secret `a` and compute the public value `A`."""
        self._username = username
        self._password = password
        self._pool_name = pool_id.split("_", 1)[1]
        self._small_a = (
            small_a if small_a is not None else secrets.randbits(1024) % BIG_N
        )
        self.large_a = pow(G, self._small_a, BIG_N)
        if self.large_a % BIG_N == 0:  # pragma: no cover - astronomically unlikely
            msg = "Safety check for A failed"
            raise ValueError(msg)

    def auth_parameters(self) -> dict[str, str]:
        """AuthParameters for InitiateAuth with USER_SRP_AUTH."""
        return {"USERNAME": self._username, "SRP_A": f"{self.large_a:x}"}

    def password_authentication_key(
        self, user_id_for_srp: str, server_b: int, salt_hex: str
    ) -> bytes:
        """Derive the 16-byte key that signs the PASSWORD_VERIFIER response."""
        u = int(_hex_hash(pad_hex(self.large_a) + pad_hex(server_b)), 16)
        if u == 0:
            msg = "SRP u value is zero"
            raise ValueError(msg)
        identity = f"{self._pool_name}{user_id_for_srp}:{self._password}"
        x = int(_hex_hash(pad_hex(salt_hex) + _hash_hex(identity.encode())), 16)
        s = pow(server_b - K * pow(G, x, BIG_N), self._small_a + u * x, BIG_N)
        return _hkdf(bytes.fromhex(pad_hex(s)), bytes.fromhex(pad_hex(f"{u:x}")))

    def challenge_responses(
        self, challenge: dict[str, str], now: datetime | None = None
    ) -> dict[str, str]:
        """Answer Cognito's PASSWORD_VERIFIER challenge."""
        user_id_for_srp = challenge["USER_ID_FOR_SRP"]
        secret_block = challenge["SECRET_BLOCK"]
        timestamp = cognito_timestamp(now or datetime.now(UTC))
        key = self.password_authentication_key(
            user_id_for_srp, int(challenge["SRP_B"], 16), challenge["SALT"]
        )
        message = (
            self._pool_name.encode()
            + user_id_for_srp.encode()
            + base64.b64decode(secret_block)
            + timestamp.encode()
        )
        signature = base64.b64encode(
            hmac.new(key, message, hashlib.sha256).digest()
        ).decode()
        return {
            "TIMESTAMP": timestamp,
            "USERNAME": challenge.get("USERNAME", user_id_for_srp),
            "PASSWORD_CLAIM_SECRET_BLOCK": secret_block,
            "PASSWORD_CLAIM_SIGNATURE": signature,
        }
