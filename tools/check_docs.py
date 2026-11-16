"""Validate documentation locally without importing integration or network code."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
REPO_URL = "https://github.com/SokolskyNikita/mysa-homeassistant/"
CLIENT_URL = "https://github.com/SokolskyNikita/mysa-client/"
CLIENT_ROOT: Path | None = None
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|X-Amz-Signature=[0-9a-fA-F]{32,}"
)


def fail(path: Path, message: str) -> None:
    """Record a diagnostic without exposing suspect payload values."""
    ERRORS.append(f"{path.relative_to(ROOT)}: {message}")


def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys instead of silently dropping evidence."""
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(_value: str) -> None:
    """Reject nonstandard JSON numbers."""
    raise ValueError("non-finite JSON number")


def parse_json(path: Path) -> object:
    """Read a strict JSON document."""
    return json.loads(
        path.read_text(), object_pairs_hook=pairs, parse_constant=invalid_constant
    )


def prose(text: str) -> str:
    """Omit fenced examples when interpreting Markdown links and headings."""
    return re.sub(r"^```[^\n]*\n.*?^```\s*$", "", text, flags=re.M | re.S)


def anchors(text: str) -> set[str]:
    """Collect GitHub-style heading slugs and explicit HTML IDs."""
    text = prose(text)
    result = set(re.findall(r'<a\s+(?:id|name)="([^"]+)"', text))
    counts: Counter[str] = Counter()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        number = counts[slug]
        counts[slug] += 1
        result.add(f"{slug}-{number}" if number else slug)
    return result


def check_link(source: Path, url: str) -> Path | None:
    """Resolve a local target, prohibit checkout escapes, and check its anchor."""
    url = url.strip("<>")
    base = source.parent
    boundary = ROOT
    for prefix, checkout in ((REPO_URL, ROOT), (CLIENT_URL, CLIENT_ROOT)):
        if checkout is None:
            continue
        for kind in ("blob/main/", "tree/main/"):
            if url.startswith(prefix + kind):
                url = url.removeprefix(prefix + kind)
                base = boundary = checkout
    if re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", url, re.I):
        return None
    path, _, fragment = unquote(url).partition("#")
    target = (base / path).resolve() if path else source
    if not target.is_relative_to(boundary):
        fail(source, "local link escapes the repository")
        return None
    if target.is_dir() and (target / "README.md").exists():
        target /= "README.md"
    if not target.exists():
        fail(source, f"missing target: {url}")
    elif fragment and target.suffix == ".md":
        if fragment not in anchors(target.read_text()):
            fail(source, f"missing anchor: {url}")
    elif fragment and re.fullmatch(r"L\d+(?:-L\d+)?", fragment):
        last = int(fragment.split("-L")[-1].lstrip("L"))
        if last > len(target.read_text().splitlines()):
            fail(source, "source line anchor exceeds file length")
        elif (
            target.suffix == ".py"
            and target.relative_to(boundary).parts[0] == "tests"
            and int(fragment.split("-")[0].lstrip("L")) not in definition_lines(target)
        ):
            fail(source, f"test line link is not on a def, class or decorator: {url}")
    return target


def definition_lines(path: Path) -> set[int]:
    """Lines a test link may land on: top-level or method defs and their decorators."""
    lines: set[int] = set()
    tree = ast.parse(path.read_text())
    nodes = list(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            nodes.extend(node.body)
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lines.add(node.lineno)
            lines.update(decorator.lineno for decorator in node.decorator_list)
    return lines


def check_navigation(graph: dict[Path, set[Path]]) -> None:
    """Require maintained pages to be reachable from both public entry points."""
    for entry in (ROOT / "README.md", ROOT / "docs/README.md"):
        visited: set[Path] = set()
        pending = [entry]
        while pending:
            path = pending.pop()
            if path not in visited:
                visited.add(path)
                pending.extend(graph.get(path, set()) - visited)
        for path in graph.keys() - visited:
            if "<!-- compatibility-forwarder -->" not in path.read_text():
                fail(path, f"unreachable from {entry.relative_to(ROOT)}")


def check_preserved_anchors() -> None:
    """Prevent moved pages and legacy section bookmarks from disappearing."""
    manifest = ROOT / "tools/docs_anchors.json"
    for name, expected in json.loads(manifest.read_text()).items():
        path = ROOT / name
        if not path.is_file():
            fail(manifest, f"published page was removed: {name}")
            continue
        missing = set(expected) - anchors(path.read_text())
        for anchor in sorted(missing):
            fail(path, f"published anchor was removed: {anchor}")


def check_inventory_version() -> None:
    """Require release updates to revisit the inventory's version context."""
    manifest = json.loads(
        (ROOT / "custom_components/mysa_local/manifest.json").read_text()
    )
    path = ROOT / "docs/implementation/progress.md"
    expected = f"Integration version: **Mysa Local {manifest['version']}**"
    if expected not in path.read_text():
        fail(path, "integration version must match manifest; review status context")


def check_markdown(path: Path) -> set[Path]:
    """Check navigation, fences, and table row widths."""
    text = path.read_text()
    if len(re.findall(r"^```", text, re.M)) % 2:
        fail(path, "unbalanced code fence")
    targets = set()
    for url in LINK.findall(prose(text)):
        target = check_link(path, url)
        if target:
            targets.add(target)
    width = None
    for line in prose(text).splitlines():
        if not line.startswith("|"):
            width = None
            continue
        # GFM requires escaped pipes even inside inline code.
        cells = len(re.findall(r"(?<!\\)\|", line))
        if width is not None and width != cells:
            fail(path, "inconsistent Markdown table width")
        width = cells
    return targets


def main() -> int:
    """Run all offline documentation checks."""
    global CLIENT_ROOT  # noqa: PLW0603
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client-root",
        type=Path,
        help="optionally validate client main links against this checkout (not publication)",
    )
    args = parser.parse_args()
    if args.client_root is not None:
        CLIENT_ROOT = args.client_root.resolve()
        if not (CLIENT_ROOT / "src/mysa_client").is_dir():
            parser.error("--client-root must be a mysa-client checkout")
    ERRORS.clear()
    markdown = sorted(
        path for path in ROOT.glob("*.md") if not path.name.startswith(".")
    )
    markdown += sorted((ROOT / "docs").rglob("*.md"))
    markdown += sorted((ROOT / "custom_components").rglob("*.md"))
    graph = {}
    for path in markdown:
        graph[path] = check_markdown(path)
        if SECRET.search(path.read_text()):
            fail(path, "possible credential material (value suppressed)")
    check_navigation(graph)
    check_preserved_anchors()
    check_inventory_version()
    for path in (ROOT / "tests/fixtures").glob("*.json"):
        try:
            parse_json(path)
        except ValueError:
            fail(path, "invalid fixture JSON")
        if SECRET.search(path.read_text()):
            fail(path, "possible credential material (value suppressed)")
    for error in ERRORS:
        print(error)
    if ERRORS:
        print(f"FAILED: {len(ERRORS)} documentation errors")
        return 1
    print(
        f"OK: {len(markdown)} Markdown files, "
        f"{len(list((ROOT / 'tests/fixtures').glob('*.json')))} strict fixtures; "
        "all checks offline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
