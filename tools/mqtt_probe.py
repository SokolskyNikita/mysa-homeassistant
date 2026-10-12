#!/usr/bin/env python3
"""Inspect Mysa MQTT authorization and test CONNECT/SUBACK without publishing.

Requires aiohttp, boto3, and paho-mqtt>=2 (diagnostic-only dependencies). Supply
private JSON containing refresh_token and/or email/password on stdin or via
--credentials FILE. Never pass credentials as command-line values. This tool
does not attach policies, publish commands, or alter thermostat settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlsplit
import uuid

import aiohttp
import boto3
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import ClientError
from mysa_client.cloud import MysaAuth, MysaClient
import paho.mqtt.client as mqtt

REGION = "us-east-1"
POOL = "us-east-1:e27f246e-ad23-41ce-ac80-eda86f8be424"
PROVIDER = "cognito-idp.us-east-1.amazonaws.com/us-east-1_GUFWfhI7g"
HOST = "a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com"
AWS_CONFIG = Config(connect_timeout=10, read_timeout=15, retries={"max_attempts": 0})
LIMITED_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Action": ["iot:Connect", "iot:Subscribe"], "Resource": "*"}
    ],
}


def report(stage, **fields):
    """Print only selected diagnostic fields, never raw AWS errors or tokens."""
    print(json.dumps({"stage": stage, **fields}), flush=True)


def signed_path(creds):
    """Sign without the session token, then append it as the official app does."""
    request = AWSRequest(method="GET", url=f"https://{HOST}/mqtt")
    signer = SigV4QueryAuth(
        Credentials(creds["AccessKeyId"], creds["SecretKey"]),
        "iotdevicegateway",
        REGION,
        expires=300,
    )
    signer.add_auth(request)
    query = urlsplit(request.url).query
    return (
        "/mqtt?"
        + query
        + "&X-Amz-Security-Token="
        + quote(creds["SessionToken"], safe="")
    )


def inspect_policy(iot, identity):
    """Recognize only the exact observed limited policy; do not emulate IAM."""
    try:
        names = []
        for page in iot.get_paginator("list_attached_policies").paginate(
            target=identity
        ):
            names.extend(policy["policyName"] for policy in page.get("policies", []))
        report("attached_policies", names=names)
        if names != ["default"]:
            report("policy", result="inconclusive: not the single default policy")
            return False
        document = json.loads(iot.get_policy(policyName="default")["policyDocument"])
        if document == LIMITED_POLICY:
            report(
                "policy",
                result="missing_publish_and_receive",
                allowed=["iot:Connect", "iot:Subscribe"],
            )
            return True
        report(
            "policy", result="inconclusive: differs from the observed limited policy"
        )
    except ClientError as err:
        report(
            "policy",
            result="inspection_unavailable",
            error_code=err.response["Error"]["Code"],
        )
    return False


def probe_connection(creds, devices, duration):
    """Use Paho for MQTT framing and listen only to account-owned devices."""
    pending, accepted = {}, set()
    observations = {"connected": False, "messages": 0, "disconnected": False}
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=str(uuid.uuid4()),
        protocol=mqtt.MQTTv311,
        transport="websockets",
        reconnect_on_failure=False,
    )

    def on_connect(client, userdata, flags, reason, properties):
        observations["connected"] = not reason.is_failure
        report("connack", reason=reason.value)
        if not reason.is_failure:
            for device in devices:
                status, mid = client.subscribe(f"/v1/dev/{device}/out", qos=1)
                if status == mqtt.MQTT_ERR_SUCCESS:
                    pending[mid] = device

    def on_subscribe(client, userdata, mid, reasons, properties):
        device = pending.pop(mid, None)
        if device is not None:
            granted = bool(reasons) and all(not reason.is_failure for reason in reasons)
            if granted:
                accepted.add(device)
            report("suback", device_suffix=device[-6:], granted=granted)

    def on_message(client, userdata, message):
        # Do not log payloads or full device identifiers.
        observations["messages"] += 1

    def on_disconnect(client, userdata, flags, reason, properties):
        observations["disconnected"] = True

    client.on_connect, client.on_subscribe = on_connect, on_subscribe
    client.on_message, client.on_disconnect = on_message, on_disconnect
    client.tls_set()
    client.ws_set_options(path=signed_path(creds))
    client.connect_timeout = 15
    try:
        client.connect(HOST, 443, keepalive=60)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and not observations["disconnected"]:
            if (
                client.loop(timeout=min(1, max(0.01, deadline - time.monotonic())))
                != mqtt.MQTT_ERR_SUCCESS
            ):
                break
    finally:
        client.disconnect()
    report(
        "mqtt",
        connack_ok=observations["connected"],
        subscriptions=len(accepted),
        messages=observations["messages"],
    )
    return observations["connected"] and len(accepted) == len(devices)


async def run(args, saved):
    """Load private credentials, obtain scoped AWS credentials, and diagnose."""
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=25)
    ) as session:
        auth = MysaAuth(
            session,
            saved.get("refresh_token"),
            user_agent="Mysa-MQTT-probe/1.0",
            email=saved.get("email"),
            password=saved.get("password"),
        )
        token = await auth.async_get_id_token()
        api = MysaClient(session, auth, user_agent="Mysa-MQTT-probe/1.0")
        devices = await api.get_devices()
    if not devices:
        report("devices", count=0)
        return 1
    identity_client = boto3.client(
        "cognito-identity", region_name=REGION, config=AWS_CONFIG
    )
    logins = {PROVIDER: token}
    identity = identity_client.get_id(IdentityPoolId=POOL, Logins=logins)["IdentityId"]
    result = identity_client.get_credentials_for_identity(
        IdentityId=identity, Logins=logins
    )
    creds = result["Credentials"]
    iot = boto3.client(
        "iot",
        region_name=REGION,
        config=AWS_CONFIG,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretKey"],
        aws_session_token=creds["SessionToken"],
    )
    limited = inspect_policy(iot, result["IdentityId"])
    connected = probe_connection(creds, devices, args.duration)
    report(
        "result",
        authorization="blocked" if limited else "inconclusive",
        explanation="Publish/Receive absent"
        if limited
        else "CONNECT/SUBACK alone do not prove Publish/Receive",
    )
    return (
        2 if connected else 1
    )  # No end-to-end MQTT success is claimed by this passive probe.


def main():
    """Run the bounded CLI without printing exception details containing secrets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials", metavar="FILE", help="Private JSON; defaults to stdin"
    )
    parser.add_argument(
        "--duration", type=int, choices=range(1, 56), metavar="1..55", default=20
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        saved = (
            json.loads(Path(args.credentials).read_text())
            if args.credentials
            else json.load(sys.stdin)
        )
        return asyncio.run(run(args, saved))
    except Exception as err:  # noqa: BLE001 -- raw errors can expose tokens/URLs
        report("error", error_type=type(err).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
