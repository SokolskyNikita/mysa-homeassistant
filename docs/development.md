# Development

[Documentation index](README.md)

## Environment

Use Python 3.14.2 or newer in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
scripts/setup
```

Run commands from the repository root:

```bash
scripts/test     # pytest with coverage; accepts extra pytest arguments
scripts/lint     # Ruff formatting and fixes, then strict mypy
scripts/develop  # development Home Assistant using config/
```

CI also runs HACS validation and hassfest. The cloud and HomeKit protocols are provided by the standalone [`mysa-client` package](https://github.com/SokolskyNikita/mysa-client); this integration owns lifecycle, polling, persistence, and command ordering. See [Architecture](architecture.md) for the public interfaces, ownership rules, and boundary tests. Cloud endpoints and device protocols are documented in [the client documentation index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md); the [Bluetooth protocol reference](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/reference/protocol/bluetooth.md) covers Wi-Fi provisioning, network scanning, and newer-model credential transfer. Release history is in [CHANGELOG.md](../CHANGELOG.md).
