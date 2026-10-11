"""Keep protocol implementations independent of Home Assistant and each other."""

import ast
from pathlib import Path
import subprocess
import sys

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "mysa_local"


@pytest.mark.parametrize("protocol", ["cloud", "homekit"])
def test_protocol_import_boundary(protocol: str) -> None:
    """A protocol only imports its own modules and external dependencies."""
    for path in (COMPONENT / "protocols" / protocol).glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1, f"{path.name} imports outside its protocol"
                module = node.module or ""
                assert not module.startswith(("homeassistant", "custom_components"))
                if protocol == "cloud":
                    assert not module.startswith("aiohomekit")
            elif isinstance(node, ast.Import):
                for name in node.names:
                    assert not name.name.startswith(
                        ("homeassistant", "custom_components")
                    )
                    if protocol == "cloud":
                        assert not name.name.startswith("aiohomekit")


def test_homekit_implementation_is_encapsulated() -> None:
    """Integration callers must use the facade, never aiohomekit directly."""
    for path in COMPONENT.rglob("*.py"):
        if "protocols" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("aiohomekit"), path
            elif isinstance(node, ast.Import):
                assert all(not n.name.startswith("aiohomekit") for n in node.names), (
                    path
                )


def test_protocol_modules_load_without_home_assistant() -> None:
    """Bundled protocol implementations can load without the HA parent package."""
    # The deployed integration's parent initializer needs HA. Load just its
    # bundled protocols in an isolated process to enforce the implementation boundary.
    # Register only the protocol package. Adding the component directory to
    # sys.path would let HA's select.py shadow Python's standard-library select.
    script = f"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "protocols", {str(COMPONENT / "protocols" / "__init__.py")!r}
)
package = importlib.util.module_from_spec(spec)
sys.modules["protocols"] = package
spec.loader.exec_module(package)
from protocols.cloud import MysaClient
from protocols.homekit import HomeKitProtocol, HomeKitDevice
assert "homeassistant" not in sys.modules
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local import check
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
