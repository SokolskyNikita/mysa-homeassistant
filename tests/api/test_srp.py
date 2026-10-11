"""Tests for the Cognito SRP math."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import hmac
from unittest.mock import MagicMock

import pytest

from custom_components.mysa_local.protocols.cloud import srp
from custom_components.mysa_local.protocols.cloud.srp import (
    BIG_N,
    CognitoSrp,
    G,
    K,
    cognito_timestamp,
    pad_hex,
)

POOL_ID = "us-east-1_TestPool1"
USER_ID = "3f2c0d4e-0000-4000-8000-000000000001"
PASSWORD = "correct horse battery staple"


def _server_key(large_a: int, salt_hex: str, small_b: int) -> tuple[int, bytes]:
    """Play the Cognito side: return B and the key the server derives."""
    identity = f"{POOL_ID.split('_')[1]}{USER_ID}:{PASSWORD}"
    x = int(
        hashlib.sha256(
            bytes.fromhex(
                pad_hex(salt_hex) + hashlib.sha256(identity.encode()).hexdigest()
            )
        ).hexdigest(),
        16,
    )
    verifier = pow(G, x, BIG_N)
    large_b = (K * verifier + pow(G, small_b, BIG_N)) % BIG_N
    u = int(
        hashlib.sha256(bytes.fromhex(pad_hex(large_a) + pad_hex(large_b))).hexdigest(),
        16,
    )
    s = pow(large_a * pow(verifier, u, BIG_N), small_b, BIG_N)
    prk = hmac.new(
        bytes.fromhex(pad_hex(f"{u:x}")), bytes.fromhex(pad_hex(s)), hashlib.sha256
    ).digest()
    key = hmac.new(prk, b"Caldera Derived Key\x01", hashlib.sha256).digest()[:16]
    return large_b, key


def test_pad_hex() -> None:
    """Odd lengths get a zero, a set top bit gets a 00 byte."""
    assert pad_hex(0xABC) == "0abc"
    assert pad_hex(0x7F) == "7f"
    assert pad_hex(0x80) == "0080"
    assert pad_hex("ff") == "00ff"


def test_timestamp_has_no_leading_zero_day() -> None:
    """Cognito rejects 'Jan 02'; it wants 'Jan 2'."""
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert cognito_timestamp(when) == "Fri Jan 2 03:04:05 UTC 2026"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_client_and_server_agree(seed: int) -> None:
    """The signature we send verifies with the key the server derives."""
    client = CognitoSrp(USER_ID, PASSWORD, POOL_ID, small_a=1234567 * seed + 99)
    salt_hex = f"{(seed * 0xDEADBEEF) ^ 0x1F2E3D4C:x}"
    large_b, server_key = _server_key(client.large_a, salt_hex, 7654321 + seed)
    secret_block = base64.b64encode(b"secret-block-" + bytes([seed])).decode()
    now = datetime(2026, 10, 10, 21, 0, 0, tzinfo=UTC)

    answer = client.challenge_responses(
        {
            "USER_ID_FOR_SRP": USER_ID,
            "SALT": salt_hex,
            "SRP_B": f"{large_b:x}",
            "SECRET_BLOCK": secret_block,
        },
        now,
    )

    message = (
        b"TestPool1"
        + USER_ID.encode()
        + base64.b64decode(secret_block)
        + answer["TIMESTAMP"].encode()
    )
    expected = base64.b64encode(
        hmac.new(server_key, message, hashlib.sha256).digest()
    ).decode()
    assert answer["PASSWORD_CLAIM_SIGNATURE"] == expected
    assert answer["USERNAME"] == USER_ID
    assert answer["TIMESTAMP"] == "Sat Oct 10 21:00:00 UTC 2026"


def test_matches_pycognito() -> None:
    """Same inputs give the same key as pycognito's reference implementation."""
    aws_srp = pytest.importorskip("pycognito.aws_srp")
    reference = aws_srp.AWSSRP(
        username=USER_ID,
        password=PASSWORD,
        pool_id=POOL_ID,
        client_id="client",
        client=MagicMock(),
    )
    small_a = 0x1234567890ABCDEF
    reference.small_a_value = small_a
    reference.large_a_value = pow(G, small_a, BIG_N)
    ours = CognitoSrp(USER_ID, PASSWORD, POOL_ID, small_a=small_a)
    assert ours.large_a == reference.large_a_value
    assert reference.val_k == K

    salt = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
    server_b, _ = _server_key(ours.large_a, salt, 424242)
    assert ours.password_authentication_key(
        USER_ID, server_b, salt
    ) == reference.get_password_authentication_key(USER_ID, PASSWORD, server_b, salt)


def test_module_constants() -> None:
    """N is the 3072-bit RFC 5054 group."""
    assert BIG_N.bit_length() == 3072
    assert srp.G_HEX == "2"
