"""Validate documentation locally without importing integration or network code."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
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
    if re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", url, re.I):
        return None
    path, _, fragment = unquote(url).partition("#")
    target = (source.parent / path).resolve() if path else source
    if not target.is_relative_to(ROOT):
        fail(source, "local link escapes the repository")
        return None
    if not target.exists():
        fail(source, f"missing target: {url}")
    elif fragment and target.suffix == ".md":
        if fragment not in anchors(target.read_text()):
            fail(source, f"missing anchor: {url}")
    elif fragment and re.fullmatch(r"L\d+(?:-L\d+)?", fragment):
        last = int(fragment.split("-L")[-1].lstrip("L"))
        if last > len(target.read_text().splitlines()):
            fail(source, "source line anchor exceeds file length")
    return target


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
    markdown = [ROOT / "README.md", ROOT / "CHANGELOG.md"]
    markdown += sorted((ROOT / "docs").rglob("*.md"))
    markdown += sorted((ROOT / "custom_components").rglob("*.md"))
    for path in markdown:
        check_markdown(path)
        if SECRET.search(path.read_text()):
            fail(path, "possible credential material (value suppressed)")
    for path in (ROOT / "tests/fixtures").glob("*.json"):
        try:
            parse_json(path)
        except ValueError:
            fail(path, "invalid fixture JSON")
        if SECRET.search(path.read_text()):
            fail(path, "possible credential material (value suppressed)")
    tracker_id_count = 0
    for name, prefix in (("cloud", "c"), ("homekit", "hk")):
        tracker = ROOT / f"docs/implementation/{name}.md"
        tracker_text = tracker.read_text()
        item_ids = re.findall(rf'<a id="({prefix}-[iapod]\d+)"', tracker_text)
        tracker_id_count += len(item_ids)
        if len(item_ids) != len(set(item_ids)):
            fail(tracker, "duplicate tracker item IDs")
        for identifier in item_ids:
            if identifier.startswith((f"{prefix}-a", f"{prefix}-p")) and (
                identifier + "-criteria" not in anchors(tracker_text)
            ):
                fail(tracker, f"missing completion criteria: {identifier}")
    for error in ERRORS:
        print(error)
    if ERRORS:
        print(f"FAILED: {len(ERRORS)} documentation errors")
        return 1
    print(
        f"OK: {len(markdown)} Markdown files, "
        f"{len(list((ROOT / 'tests/fixtures').glob('*.json')))} strict fixtures, "
        f"{tracker_id_count} cloud/HomeKit tracker IDs; all checks offline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
