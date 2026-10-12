"""Validate documentation locally without importing integration or network code."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs/examples"
ERRORS: list[str] = []
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
KINDS = {"live-sanitized", "app-derived", "constructed"}
REPRESENTATIONS = {"complete", "projection", "fixture", "unknown"}
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


def check_body(path: Path, part: dict[str, object]) -> None:
    """Validate payload references without mutating existing fixture shapes."""
    if "body" in part and "bodyFile" in part:
        fail(path, "body and bodyFile are mutually exclusive")
    if "bodyFile" in part:
        target = check_link(path, str(part["bodyFile"]))
        if target and target.is_file():
            try:
                parse_json(target)
            except (ValueError, OSError):
                fail(path, "referenced payload is not strict JSON")


def check_example(path: Path, ids: set[str]) -> None:
    """Validate the documented exchange format and required evidence fields."""
    try:
        data = parse_json(path)
    except (ValueError, OSError):
        fail(path, "invalid strict JSON")
        return
    if not isinstance(data, dict):
        fail(path, "example must be an object")
        return
    identifier = data.get("id")
    if not isinstance(identifier, str) or identifier != path.stem or identifier in ids:
        fail(path, "missing, duplicate, or filename-mismatched example ID")
    else:
        ids.add(identifier)
    if data.get("kind") not in KINDS:
        fail(path, "unsupported evidence kind")
    evidence = data.get("evidence")
    required = {"source", "observationDate", "model", "firmware", "confirmation"}
    if not isinstance(evidence, dict) or not required <= evidence.keys():
        fail(path, "missing evidence metadata")
        return
    check_link(path, str(evidence["source"]))
    if (
        data["kind"] == "app-derived"
        and not {"appVersion", "sourceReference"} <= evidence.keys()
    ):
        fail(path, "app-derived evidence needs version and source reference")
    if data["kind"] == "live-sanitized" and not evidence["observationDate"]:
        fail(path, "live capture needs an observation date")
    if path.parent.name == "cloud":
        request, response = data.get("request"), data.get("response")
        if not isinstance(request, dict) or not isinstance(response, dict):
            fail(path, "HTTP example needs request and response objects")
            return
        if (
            not {"method", "baseUrl", "path", "authentication", "headers"}
            <= request.keys()
        ):
            fail(path, "incomplete HTTP request metadata")
        if request.get("method") not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            fail(path, "unknown HTTP method")
        if not str(request.get("path", "")).startswith("/"):
            fail(path, "HTTP path must begin with slash")
        if response.get("representation") not in REPRESENTATIONS:
            fail(path, "unknown response representation")
        status = response.get("status")
        if "status" not in response or (
            status is not None and (type(status) is not int or not 100 <= status <= 599)
        ):
            fail(path, "status must be null or a valid HTTP status integer")
        check_body(path, request)
        check_body(path, response)
        if "host" not in evidence:
            fail(path, "HTTP evidence needs host metadata")
    else:
        if not {"direction", "topic", "qos", "retain", "payload"} <= data.keys():
            fail(path, "incomplete MQTT metadata")
        if data.get("qos") not in {0, 1, 2, None}:
            fail(path, "invalid MQTT QoS")
        if data.get("retain") is not None and type(data["retain"]) is not bool:
            fail(path, "MQTT retain must be boolean or unknown")
    serialized = path.read_text()
    if SECRET.search(serialized):
        fail(path, "possible credential material (value suppressed)")
    device_ids = re.findall(r"\b[0-9a-fA-F]{12}\b", serialized)
    if any(
        value != "a1b2c3d4e5f6" and not value.startswith("000000")
        for value in device_ids
    ):
        fail(path, "non-placeholder device/identifier value (value suppressed)")
    if re.search(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b", serialized):
        fail(path, "possible household MAC address (value suppressed)")


def main() -> int:
    """Run all offline documentation checks."""
    markdown = [ROOT / "README.md", ROOT / "CHANGELOG.md"]
    markdown += sorted((ROOT / "docs").rglob("*.md"))
    markdown += sorted((ROOT / "custom_components").rglob("*.md"))
    targets = set()
    for path in markdown:
        targets.update(check_markdown(path))
        if SECRET.search(path.read_text()):
            fail(path, "possible credential material (value suppressed)")
    ids: set[str] = set()
    examples = sorted(EXAMPLES.rglob("*.json"))
    for path in examples:
        check_example(path, ids)
        if path not in targets:
            fail(path, "example has no incoming documentation link")
    for path in (ROOT / "tests/fixtures").glob("*.json"):
        try:
            parse_json(path)
        except ValueError:
            fail(path, "invalid fixture JSON")
    tracker = ROOT / "docs/implementation/cloud.md"
    tracker_text = tracker.read_text()
    item_ids = re.findall(r'<a id="(c-[iapo]\d+)"', tracker_text)
    if len(item_ids) != len(set(item_ids)):
        fail(tracker, "duplicate tracker item IDs")
    for identifier in item_ids:
        if identifier.startswith(("c-a", "c-p")) and (
            identifier + "-criteria" not in anchors(tracker_text)
        ):
            fail(tracker, f"missing completion criteria: {identifier}")
    for error in ERRORS:
        print(error)
    if ERRORS:
        print(f"FAILED: {len(ERRORS)} documentation errors")
        return 1
    print(
        f"OK: {len(markdown)} Markdown files, {len(examples)} examples, "
        f"{len(item_ids)} tracker IDs; all checks offline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
