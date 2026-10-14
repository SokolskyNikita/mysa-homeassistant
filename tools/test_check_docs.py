"""Offline regression tests for documentation migration safeguards."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest


class DocumentationChecks(unittest.TestCase):
    """Exercise failures that ordinary successful link checks cannot reveal."""

    def setUp(self):
        """Load the checker without any integration dependencies."""
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        namespace = runpy.run_path(str(Path(__file__).with_name("check_docs.py")))
        self.scope = namespace["check_link"].__globals__
        self.scope["ROOT"] = self.root
        self.scope["ERRORS"].clear()

    def page(self, name, text):
        """Write one isolated documentation fixture."""
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_same_repository_urls_are_checked(self):
        """Absolute GitHub main links must not bypass anchor validation."""
        target = self.page("docs/guide.md", "# Guide\n## Pair a device\n")
        source = self.page("README.md", "# Start\n")
        url = self.scope["REPO_URL"] + "blob/main/docs/guide.md#pair-a-device"
        assert self.scope["check_link"](source, url) == target
        assert self.scope["ERRORS"] == []
        self.scope["check_link"](source, url.replace("pair-a-device", "missing"))
        assert "missing anchor" in self.scope["ERRORS"][0]

    def test_directories_and_external_urls(self):
        """Directory navigation resolves a README; other hosts remain external."""
        target = self.page("docs/README.md", "# Docs\n")
        source = self.page("README.md", "# Start\n")
        assert self.scope["check_link"](source, "docs/") == target
        assert self.scope["check_link"](source, "https://example.org/unknown") is None
        assert self.scope["ERRORS"] == []

    def test_encoded_escape_is_rejected(self):
        """Repository URL normalization must retain the checkout boundary."""
        source = self.page("README.md", "# Start\n")
        url = self.scope["REPO_URL"] + "blob/main/%2e%2e/outside.md"
        assert self.scope["check_link"](source, url) is None
        assert "escapes the repository" in self.scope["ERRORS"][0]

    def test_client_checkout_is_optional_and_checked_when_supplied(self):
        """Paired repositories validate locally without a network dependency."""
        source = self.page("README.md", "# Start\n")
        client = self.root / "client"
        target = self.page("client/docs/guide.md", "# Client guide\n")
        url = self.scope["CLIENT_URL"] + "blob/main/docs/guide.md#client-guide"
        assert self.scope["check_link"](source, url) is None
        self.scope["CLIENT_ROOT"] = client
        assert self.scope["check_link"](source, url) == target
        self.scope["check_link"](source, url.replace("client-guide", "missing"))
        assert "missing anchor" in self.scope["ERRORS"][0]

    def test_orphan_and_compatibility_navigation(self):
        """Only explicitly marked forwarding pages may be unreachable."""
        readme = self.page("README.md", "# Start\n")
        index = self.page("docs/README.md", "# Docs\n")
        orphan = self.page("docs/orphan.md", "# Lost guide\n")
        forwarder = self.page("old.md", "<!-- compatibility-forwarder -->\n")
        self.scope["check_navigation"](
            {readme: {index}, index: {readme}, orphan: set(), forwarder: {index}}
        )
        assert len(self.scope["ERRORS"]) == 2
        assert all("orphan.md" in error for error in self.scope["ERRORS"])

    def test_preserved_anchor_and_page(self):
        """A compatibility ID cannot vanish even with no remaining inbound links."""
        self.page("tools/docs_anchors.json", json.dumps({"old.md": ["public-anchor"]}))
        page = self.page("old.md", '<a id="public-anchor"></a>\n')
        self.scope["check_preserved_anchors"]()
        assert self.scope["ERRORS"] == []
        page.write_text("# Moved\n")
        self.scope["check_preserved_anchors"]()
        assert "published anchor was removed" in self.scope["ERRORS"][0]
        self.scope["ERRORS"].clear()
        page.unlink()
        self.scope["check_preserved_anchors"]()
        assert "published page was removed" in self.scope["ERRORS"][0]

    def test_version_context_requires_release_review(self):
        """Historical evidence can remain dated while current release context is checked."""
        self.page("custom_components/mysa_local/manifest.json", '{"version": "0.8.3"}')
        page = self.page(
            "docs/implementation/progress.md",
            "Integration version: **Mysa Local 0.8.2**\n",
        )
        self.scope["check_inventory_version"]()
        assert "version must match" in self.scope["ERRORS"][0]
        self.scope["ERRORS"].clear()
        page.write_text(
            "Integration version: **Mysa Local 0.8.3**\nHardware checked on 0.5.2.\n"
        )
        self.scope["check_inventory_version"]()
        assert self.scope["ERRORS"] == []


if __name__ == "__main__":
    unittest.main()
