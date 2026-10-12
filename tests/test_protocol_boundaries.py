"""Keep protocol implementations in the standalone client distribution."""

import ast
from pathlib import Path
import subprocess
import sys

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "mysa_local"


def test_protocol_implementations_are_not_bundled() -> None:
    """The integration delegates protocol behavior to the client package."""
    assert not (COMPONENT / "protocols").exists()


def test_homekit_implementation_is_encapsulated() -> None:
    """Integration runtime callers must use the client facade, not aiohomekit."""
    for path in COMPONENT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("aiohomekit"), path
            elif isinstance(node, ast.Import):
                assert all(
                    not name.name.startswith("aiohomekit") for name in node.names
                ), path


def test_protocol_modules_load_without_home_assistant() -> None:
    """The installed client imports its cloud and HomeKit APIs outside HA."""
    script = """
import sys
from mysa_client.cloud import MysaClient
from mysa_client.homekit import HomeKitDevice, HomeKitProtocol
assert "homeassistant" not in sys.modules
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local import check
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
