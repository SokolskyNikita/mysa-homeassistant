"""Shared test helpers: fixtures, a fake Mysa cloud and a fake HomeKit thermostat."""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
import time
from typing import Any

from aiohomekit.controller.abstract import FinishPairing
from aiohomekit.model import Accessories
from aiohomekit.model.status_flags import StatusFlags
from aiohomekit.testing import FakeController, FakeDiscovery, FakePairing
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.mysa_local.api.const import (
    API_BASE,
    COGNITO_URL,
    COGNITO_USER_POOL_ID,
)
from custom_components.mysa_local.api.srp import BIG_N, G, K, pad_hex
from custom_components.mysa_local.const import (
    CONF_CLOUD_ENABLED,
    CONF_CLOUD_RUNTIME,
    CONF_DEVICE_ID,
    CONF_HEATER_WATTS,
    CONF_MODEL,
    CONF_PAIRING,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    SUBENTRY_THERMOSTAT,
)

FIXTURES = Path(__file__).parent / "fixtures"
DEVICE_ID = "a1b2c3d4e5f6"
HKID = "00:00:00:00:00:00"
USER_ID = "00000000-0000-4000-8000-0000000000b1"
HOME_ID = "00000000-0000-4000-8000-0000000000a1"
EMAIL = "user@example.com"
PASSWORD = "hunter2-but-longer"
REFRESH_TOKEN = "refresh-token-1"
PAIRING_CODE = "111-22-333"

PAIRING_DATA = {
    "AccessoryPairingID": HKID,
    "AccessoryLTPK": "00" * 32,
    "iOSPairingId": "11111111-2222-4333-8444-555555555555",
    "iOSDeviceLTSK": "11" * 32,
    "iOSDeviceLTPK": "22" * 32,
    "AccessoryIP": "192.0.2.10",
    "AccessoryIPs": ["192.0.2.10"],
    "AccessoryPort": 57959,
    "Connection": "IP",
}


def load_json(name: str) -> Any:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text())


def make_jwt(exp: float | None = None, sub: str = USER_ID) -> str:
    """An unsigned JWT with the claims the client reads."""

    def b64(data: dict[str, Any]) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    claims = {"sub": sub, "exp": int(exp or time.time() + 3600), "email": EMAIL}
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def thermostat_subentry(
    *, pairing: dict[str, Any] | None = PAIRING_DATA, watts: float = 0
) -> dict[str, Any]:
    """Subentry data for the test thermostat."""
    data: dict[str, Any] = {
        CONF_DEVICE_ID: DEVICE_ID,
        CONF_MODEL: "BB-V2-0-L",
        CONF_HEATER_WATTS: watts,
        CONF_CLOUD_RUNTIME: True,
    }
    if pairing is not None:
        data[CONF_PAIRING] = dict(pairing)
    return {
        "data": data,
        "subentry_type": SUBENTRY_THERMOSTAT,
        "title": "Living Room",
        "unique_id": DEVICE_ID,
    }


def hub_entry(
    *,
    signed_in: bool = True,
    cloud_enabled: bool = True,
    subentries: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    password: str | None = None,
    refresh_token: str = REFRESH_TOKEN,
) -> MockConfigEntry:
    """The Mysa Local entry with one thermostat (and a saved password if given)."""
    data: dict[str, Any] = {}
    if signed_in:
        data = {
            "email": EMAIL,
            CONF_REFRESH_TOKEN: refresh_token,
            CONF_USER_ID: USER_ID,
        }
        if password is not None:
            data["password"] = password
    return MockConfigEntry(
        domain=DOMAIN,
        title="Mysa",
        unique_id=DOMAIN,
        data=data,
        options={
            CONF_CLOUD_ENABLED: cloud_enabled,
            "scan_interval": 60,
            **(options or {}),
        },
        subentries_data=subentries
        if subentries is not None
        else [thermostat_subentry()],
    )


# ---- HomeKit --------------------------------------------------------------


def lite_accessories() -> Accessories:
    """The Baseboard Lite's accessory database."""
    return Accessories.from_list(load_json("homekit_bb_v2_0_l.json"))


@dataclass
class LiteDescription:
    """What zeroconf would tell us about an unpaired Lite."""

    name: str = "Mysa-d4e5f6"
    id: str = HKID
    model: str = "BB-V2-0-L"
    status_flags: StatusFlags = StatusFlags.UNPAIRED
    config_num: int = 1
    state_num: int = 1
    category: int = 9


class LiteDiscovery(FakeDiscovery):
    """An unpaired Lite on the network; pairing returns realistic data."""

    def __init__(self, controller: FakeController, accessories: Accessories) -> None:
        """Create the discovery."""
        super().__init__(controller, HKID, accessories)
        self.description = LiteDescription()  # type: ignore[assignment]

    async def async_start_pairing(self, alias: str) -> FinishPairing:
        """Return a finisher that checks the code and makes a FakePairing."""

        async def finish(code: str) -> FakePairing:
            if code != self.pairing_code:
                from aiohomekit.exceptions import AuthenticationError

                raise AuthenticationError("M4")
            pairing = FakePairing(self.controller, dict(PAIRING_DATA), self.accessories)
            pairing.pairing_data = dict(PAIRING_DATA)
            self.controller.aliases[HKID] = pairing
            self.controller.pairings[HKID] = pairing
            return pairing

        return finish


async def make_controller(
    *, paired: bool = True, unpaired: bool = False
) -> FakeController:
    """A FakeController with the Lite either paired, discoverable, or both."""
    controller = FakeController()
    await controller.async_start()
    if paired:
        pairing = await controller.add_paired_device(lite_accessories(), alias=HKID)
        pairing.pairing_data = dict(PAIRING_DATA)
        controller.aliases[HKID] = pairing
    if unpaired:
        controller.discoveries[HKID] = LiteDiscovery(controller, lite_accessories())
    return controller


# ---- Mysa cloud -----------------------------------------------------------


class FakeCloud:
    """A small simulation of Mysa's backend and Cognito on aioclient_mock."""

    def __init__(self, mock: AiohttpClientMocker) -> None:
        """Register every route the integration uses."""
        self.mock = mock
        self.state: dict[str, Any] = copy.deepcopy(
            load_json("state_batch.json")[DEVICE_ID]["data"]
        )
        self.devices = load_json("devices.json")
        self.bootstrap = load_json("bootstrap.json")
        self.capabilities = load_json("capabilities_bb_v2_0_l.json")
        self.firmware = {
            "update": False,
            "installedVersion": "3.17.5.13",
            "allowedVersion": "3.17.5.13",
        }
        self.usage = load_json("usage.json")
        self.hold: dict[str, Any] | None = None
        self.schedules: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.settings: list[dict[str, Any]] = []
        self.posts: list[tuple[str, Any]] = []
        self.backend_status: int | None = None
        self.bootstrap_status: int | None = None
        self.live_mode: list[Any] = []
        self.claims: list[dict[str, Any]] = []
        self.claim_status: int | None = None
        self.deleted: list[str] = []
        self.shortcut_updates: list[tuple[str, Any]] = []
        self.scenes: list[dict[str, Any]] = [
            {"Id": f"{HOME_ID}_{name}", "DeviceActions": {}, "AllDevices": True}
            for name in ("home", "away", "vacation")
        ]
        self.refresh_ok = True
        self.refresh_calls = 0
        self.password = PASSWORD
        self._srp: dict[str, Any] = {}
        self.apply_updates = True

        b = API_BASE
        d = DEVICE_ID
        mock.post(COGNITO_URL, side_effect=self._cognito)
        mock.get(
            f"{b}/mobile/bootstrap?versionNumber=4.20.1",
            side_effect=self._bootstrap,
        )
        mock.get(f"{b}/devices", side_effect=self._json(lambda: self.devices))
        mock.post(f"{b}/state/batch", side_effect=self._batch)
        mock.post(f"{b}/mysa-legacy/stream/home/{HOME_ID}", side_effect=self._live_mode)
        mock.get(f"{b}/state/{d}", side_effect=self._json(lambda: self.state))
        mock.post(f"{b}/state/{d}/update", side_effect=self._update)
        mock.post(f"{b}/devices/{d}/", side_effect=self._settings)
        mock.get(
            f"{b}/capabilities/{d}", side_effect=self._json(lambda: self.capabilities)
        )
        mock.get(
            f"{b}/devices/update_available/{d}",
            side_effect=self._json(lambda: self.firmware),
        )
        mock.post(f"{b}/devices/{d}/firmware-update", side_effect=self._record_post)
        mock.get(f"{b}/schedule/hold/{d}", side_effect=self._get_hold)
        mock.delete(f"{b}/schedule/hold/{d}", side_effect=self._delete_hold)
        mock.post(f"{b}/schedule/hold", side_effect=self._set_hold_checked)
        mock.get(f"{b}/schedule", side_effect=self._get_schedules)
        mock.post(f"{b}/schedule", side_effect=self._create_schedule)
        mock.delete(
            re.compile(r".*/schedule/[^/]+/attach/[^/]+$"),
            side_effect=self._delete_schedule,
        )
        mock.post(
            f"{b}/telemetry/usage/{d}", side_effect=self._json(lambda: self.usage)
        )
        mock.post(f"{b}/devices", side_effect=self._claim)
        mock.delete(f"{b}/devices/{d}", side_effect=self._unclaim)
        mock.get(
            f"{b}/homes/{HOME_ID}/scenes",
            side_effect=self._json(lambda: {"Scenes": self.scenes}),
        )
        mock.patch(re.compile(r".*/scene/[^/]+$"), side_effect=self._update_scene)

    # -- account --

    @property
    def on_account(self) -> bool:
        """True while the test thermostat is on the account."""
        return DEVICE_ID in self.devices["DevicesObj"]

    def remove_from_account(self) -> None:
        """Take the test thermostat off the account (as if removed elsewhere)."""
        self.devices["DevicesObj"].pop(DEVICE_ID, None)
        self.bootstrap["data"]["devices"].pop(DEVICE_ID, None)
        self.bootstrap["data"]["user"]["AllowedDevices"] = []

    def add_unsupported(self) -> str:
        """Put a Baseboard V3 (not supported) on the account; return its ID."""
        device_id = "0a0b0c0d0e0f"
        record = {
            **self.devices["DevicesObj"][DEVICE_ID],
            "Id": device_id,
            "Model": "BB-V3-0",
            "Name": "Hallway",
        }
        self.devices["DevicesObj"][device_id] = record
        self.bootstrap["data"]["devices"][device_id] = dict(record)
        self.bootstrap["data"]["user"]["AllowedDevices"].append(device_id)
        return device_id

    async def _claim(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        body = json.loads(data)
        self.claims.append(body)
        if self.claim_status is not None:
            return AiohttpClientMockResponse(
                method, url, status=self.claim_status, text='{"message": "no"}'
            )
        if body["Id"] in self.devices["DevicesObj"]:
            return self._resp(
                method, url, status=409, body=self.devices["DevicesObj"][body["Id"]]
            )
        record = {**load_json("devices.json")["DevicesObj"][DEVICE_ID], **body}
        self.devices["DevicesObj"][body["Id"]] = record
        self.bootstrap["data"]["devices"][body["Id"]] = record
        self.bootstrap["data"]["user"]["AllowedDevices"] = [body["Id"]]
        return self._resp(method, url, status=201, body={})

    async def _unclaim(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        self.deleted.append(url.path.rsplit("/", 1)[-1])
        self.remove_from_account()
        return self._resp(method, url, status=204)

    async def _update_scene(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        scene_id = url.path.rsplit("/", 1)[-1]
        body = json.loads(data)
        self.shortcut_updates.append((scene_id, body))
        for scene in self.scenes:
            if scene["Id"] == scene_id:
                scene.update(body)
        return self._resp(method, url, status=202, body={})

    # -- helpers --

    def _resp(
        self, method: str, url: URL, status: int = 200, body: Any = None
    ) -> AiohttpClientMockResponse:
        if self.backend_status is not None and "cognito" not in str(url):
            return AiohttpClientMockResponse(
                method, url, status=self.backend_status, text="error"
            )
        if body is None:
            return AiohttpClientMockResponse(method, url, status=status, text="")
        return AiohttpClientMockResponse(method, url, status=status, json=body)

    def _json(self, getter: Any) -> Any:
        async def handler(
            method: str, url: URL, data: Any
        ) -> AiohttpClientMockResponse:
            return self._resp(method, url, body=getter())

        return handler

    def calls(self, method: str, path: str) -> list[Any]:
        """Bodies of requests to a path."""
        return [
            json.loads(data) if isinstance(data, str) and data else data
            for m, url, data, _ in self.mock.mock_calls
            if m.lower() == method.lower() and url.path == path
        ]

    # -- Cognito --

    async def _cognito(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        body = json.loads(data)
        if "AuthFlow" in body:
            large_a = int(body["AuthParameters"]["SRP_A"], 16)
            salt = "a1b2c3d4e5f60718"
            identity = f"{COGNITO_USER_POOL_ID.split('_')[1]}{USER_ID}:{self.password}"
            x = int(
                hashlib.sha256(
                    bytes.fromhex(
                        pad_hex(salt) + hashlib.sha256(identity.encode()).hexdigest()
                    )
                ).hexdigest(),
                16,
            )
            verifier = pow(G, x, BIG_N)
            small_b = 987654321
            large_b = (K * verifier + pow(G, small_b, BIG_N)) % BIG_N
            u = int(
                hashlib.sha256(
                    bytes.fromhex(pad_hex(large_a) + pad_hex(large_b))
                ).hexdigest(),
                16,
            )
            s = pow(large_a * pow(verifier, u, BIG_N), small_b, BIG_N)
            prk = hmac.new(
                bytes.fromhex(pad_hex(f"{u:x}")),
                bytes.fromhex(pad_hex(s)),
                hashlib.sha256,
            ).digest()
            self._srp["key"] = hmac.new(
                prk, b"Caldera Derived Key\x01", hashlib.sha256
            ).digest()[:16]
            self._srp["secret"] = base64.b64encode(b"secret-block").decode()
            return AiohttpClientMockResponse(
                method,
                url,
                json={
                    "ChallengeName": "PASSWORD_VERIFIER",
                    "ChallengeParameters": {
                        "USER_ID_FOR_SRP": USER_ID,
                        "USERNAME": USER_ID,
                        "SALT": salt,
                        "SRP_B": f"{large_b:x}",
                        "SECRET_BLOCK": self._srp["secret"],
                    },
                },
            )
        if "ChallengeName" in body:
            answers = body["ChallengeResponses"]
            message = (
                COGNITO_USER_POOL_ID.split("_")[1].encode()
                + USER_ID.encode()
                + base64.b64decode(self._srp["secret"])
                + answers["TIMESTAMP"].encode()
            )
            expected = base64.b64encode(
                hmac.new(self._srp["key"], message, hashlib.sha256).digest()
            ).decode()
            if answers["PASSWORD_CLAIM_SIGNATURE"] != expected:
                return AiohttpClientMockResponse(
                    method,
                    url,
                    status=400,
                    json={
                        "__type": "NotAuthorizedException",
                        "message": "Incorrect username or password.",
                    },
                )
            return AiohttpClientMockResponse(
                method,
                url,
                json={
                    "AuthenticationResult": {
                        "IdToken": make_jwt(),
                        "AccessToken": "access",
                        "RefreshToken": REFRESH_TOKEN,
                        "ExpiresIn": 3600,
                    }
                },
            )
        self.refresh_calls += 1
        if not self.refresh_ok:
            return AiohttpClientMockResponse(
                method,
                url,
                status=400,
                json={
                    "__type": "NotAuthorizedException",
                    "message": "Refresh Token has expired",
                },
            )
        return AiohttpClientMockResponse(
            method,
            url,
            json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
        )

    # -- backend --

    async def _live_mode(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        self.live_mode.append(json.loads(data))
        return self._resp(
            method, url, body={"streamedDeviceIds": [DEVICE_ID], "skippedDeviceIds": []}
        )

    async def _bootstrap(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        if self.bootstrap_status is not None:
            return self._resp(
                method, url, status=self.bootstrap_status, body={"error": "x"}
            )
        return self._resp(method, url, body=self.bootstrap)

    async def _batch(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        if not self.on_account:
            error = {
                "message": "User does not have access",
                "code": "PERMISSION_DENIED",
            }
            return self._resp(method, url, body={DEVICE_ID: {"error": error}})
        return self._resp(method, url, body={DEVICE_ID: {"data": self.state}})

    async def _update(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        body = json.loads(data)
        self.updates.append(body)
        if self.apply_updates:
            for section, fields in body.items():
                if section == "source":
                    continue
                node = self.state.setdefault(section, {})
                for part in ("desired", "reported"):
                    node.setdefault(part, {}).update(fields)
        return self._resp(method, url, body={"message": "ok"})

    async def _settings(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        body = json.loads(data)
        self.settings.append(body)
        self.devices["DevicesObj"][DEVICE_ID].update(body)
        return self._resp(method, url, body=body)

    async def _record_post(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        self.posts.append((url.path, data))
        return self._resp(method, url, status=200, body={"ok": True})

    async def _get_hold(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        if self.hold is None:
            return self._resp(
                method, url, status=404, body={"message": "No active hold"}
            )
        return self._resp(method, url, body=self.hold)

    async def _set_hold_checked(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        if not self.schedules:
            return self._resp(
                method,
                url,
                status=404,
                body={
                    "message": "Device is not following any schedule. Cannot create a hold."
                },
            )
        return await self._set_hold(method, url, data)

    async def _set_hold(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        self.hold = json.loads(data)
        return self._resp(method, url, status=201, body={"holdId": "h1"})

    async def _delete_hold(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        self.hold = None
        return self._resp(method, url, body={"message": "restored"})

    async def _get_schedules(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        if not self.schedules:
            return self._resp(
                method, url, status=404, body={"message": "Schedule not found"}
            )
        return self._resp(method, url, body=self.schedules)

    async def _create_schedule(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        body = json.loads(data)
        body["id"] = f"s{len(self.schedules) + 1}"
        self.schedules.append(body)
        return self._resp(method, url, status=201, body={"scheduleId": body["id"]})

    async def _delete_schedule(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        schedule_id = url.path.split("/")[2]
        self.schedules = [s for s in self.schedules if s["id"] != schedule_id]
        return self._resp(method, url, body={"message": "deleted"})


def utc(*args: int) -> datetime:
    """Shorthand for an aware UTC datetime."""
    return datetime(*args, tzinfo=UTC)
