# Development

[Documentation index](README.md)

## Environment

Use Python 3.14.2 or newer in a virtual environment. With [uv](https://docs.astral.sh/uv/) a fresh environment takes about a second:

```bash
uv venv --python 3.14 && source .venv/bin/activate   # or: python3 -m venv .venv
scripts/setup
```

`scripts/setup` installs `mysa-client` from PyPI at the pinned version, as CI does. To work against a local client checkout, run `MYSA_CLIENT_PATH=../mysa-client scripts/setup`.

Run commands from the repository root:

```bash
scripts/test     # pytest on 6 workers; accepts extra pytest arguments
scripts/lint     # Ruff formatting and fixes, then strict mypy
scripts/develop  # development Home Assistant using config/
```

- **One file:** `scripts/test -n 0 tests/test_holds.py` skips the worker start-up.
- **Worker count:** set `MYSA_TEST_WORKERS`. The default is fixed, not one per core, so several checkouts can test at once.
- **Coverage:** CI measures it. Locally, add `--cov --cov-report=term-missing`.
- **Waits:** `tests/conftest.py` sets the spacing between cloud writes to zero. Keep real sleeps and timeouts out of tests; patch the constant or raise the error directly.

CI also runs HACS validation and hassfest. The cloud and HomeKit protocols are provided by the standalone [`mysa-client` package](https://github.com/SokolskyNikita/mysa-client); this integration owns lifecycle, polling, persistence, and command ordering. See [Architecture](architecture.md) for the public interfaces, ownership rules, and boundary tests. Cloud endpoints and device protocols are documented in [the client documentation index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md); the [Bluetooth protocol reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/bluetooth.md) covers Wi-Fi provisioning, network scanning, and newer-model credential transfer. Release history is in [CHANGELOG.md](../CHANGELOG.md).
