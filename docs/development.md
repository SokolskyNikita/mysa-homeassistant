# Development

[Documentation index](README.md)

## Environment

Use Python 3.14.2 or newer in a virtual environment. With [uv](https://docs.astral.sh/uv/) a fresh environment takes about a second:

```bash
uv venv --python 3.14 && source .venv/bin/activate   # or: python3 -m venv .venv
scripts/setup
```

`scripts/setup` installs `mysa-client` from PyPI at the pinned version, as CI does. To work against a local client checkout, run `MYSA_CLIENT_PATH=../mysa-client scripts/setup`.

### Home Assistant versions

The tests run against the oldest and the newest Home Assistant this integration supports. Each version has its own dependency group in `pyproject.toml`:

| Group | Home Assistant | Harness | Used by |
| --- | --- | --- | --- |
| `test` (and `dev`) | 2026.11.1 | `pytest-homeassistant` | `.venv`: `scripts/test`, `scripts/develop` |
| `test-min` (and `lint`) | 2026.9.4 | `pytest-homeassistant-custom-component` | `.venv-min`: `MYSA_TEST_HA=min scripts/test`, `scripts/lint` |

`scripts/setup` fills both environments. The two harnesses can't share one: the old one stops at 2026.10 and fails to import 2026.11 with `httpx was already imported`. Tests import the harness helpers from `tests/harness.py`, which picks whichever is installed.

mypy checks against the minimum. On 2026.11 it reports errors this code can't fix while it still supports 2026.9: flow schemas are typed as probatio rather than voluptuous, and the climate names from before `native_` are marked `Final`.

When the minimum moves to 2026.11 or later, drop `test-min` and `tests/harness.py` and import from `pytest_homeassistant` directly. When a new Home Assistant ships, raise the `test` pin to the matching `pytest-homeassistant` release.

Run commands from the repository root:

```bash
scripts/test     # pytest on 6 workers, newest Home Assistant; accepts extra pytest arguments
MYSA_TEST_HA=min scripts/test   # the same on the oldest supported Home Assistant
scripts/lint     # Ruff formatting and fixes, then strict mypy (oldest Home Assistant)
scripts/develop  # development Home Assistant using config/
```

- **One file:** `scripts/test -n 0 tests/test_holds.py` skips the worker start-up.
- **Worker count:** set `MYSA_TEST_WORKERS`. The default is fixed, not one per core, so several checkouts can test at once.
- **Coverage:** CI measures it. Locally, add `--cov --cov-report=term-missing`.
- **Waits:** `tests/conftest.py` sets the spacing between cloud writes to zero. Keep real sleeps and timeouts out of tests; patch the constant or raise the error directly.

CI runs the tests on both versions, each with coverage. It also runs HACS validation and hassfest. The cloud and HomeKit protocols are provided by the standalone [`mysa-client` package](https://github.com/SokolskyNikita/mysa-client); this integration owns lifecycle, polling, persistence, and command ordering. See [Architecture](architecture.md) for the public interfaces, ownership rules, and boundary tests. Cloud endpoints and device protocols are documented in [the client documentation index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md); the [Bluetooth protocol reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/bluetooth.md) covers Wi-Fi provisioning, network scanning, and newer-model credential transfer. Release history is in [CHANGELOG.md](../CHANGELOG.md).
