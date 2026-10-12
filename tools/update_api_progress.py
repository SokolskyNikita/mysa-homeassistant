"""Render the consumer-device API coverage map in the implementation tracker.

Run ``python tools/update_api_progress.py`` after editing
``docs/implementation/progress.md``. The command updates the marked Markdown
summary and writes ``docs/implementation/api-progress.svg``. Run with
``--check`` in CI or before committing to verify that both generated artifacts
match the current feature rows without changing files.

The map reports direct API method support and integration/management support as
separate percentages. Each inventory row becomes one colored tile; partial and
missing rows do not earn implementation coverage credit.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from html import escape
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "docs/implementation/progress.md"
SVG_PATH = ROOT / "docs/implementation/api-progress.svg"
START_MARKER = "<!-- api-progress:start -->"
END_MARKER = "<!-- api-progress:end -->"

GROUPS = (
    ("API-AUTH", "Authentication"),
    ("API-READ", "State & history"),
    ("API-WRITE", "Device controls"),
    ("API-SCHED", "Schedules & templates"),
    ("API-DEVICE", "Device services"),
    ("API-LOCAL", "HomeKit"),
    ("API-SETUP", "Provisioning"),
    ("MGT-STATE", "State selection"),
    ("MGT-COMMAND", "Command handling"),
    ("MGT-LIFECYCLE", "Lifecycle & recovery"),
    ("MGT-WORKFLOW", "Managed workflows"),
    ("MGT-QUALITY", "Verification"),
)
STATUSES = ("Implemented", "Partial", "Missing")
PANELS = {
    "API": {
        "number": "01",
        "title": "RAW API METHODS",
        "label": "Raw API methods",
        "subtitle": "Documented operations and subscriptions the package can invoke directly",
        "description": "Direct methods, requests, and subscriptions exposed by the device APIs",
    },
    "MGT": {
        "number": "02",
        "title": "INTEGRATION & MANAGEMENT",
        "label": "Integration & management",
        "subtitle": "How those operations become stable, useful device behavior",
        "description": "State selection, command handling, lifecycle, workflows, and verification",
    },
}
GROUP_LABELS = dict(GROUPS)
SHORT_GROUP_LABELS = {
    "API-AUTH": "Auth",
    "API-SETUP": "Setup",
    "MGT-QUALITY": "Verify",
}
GROUP_ORDER = {group: index for index, (group, _label) in enumerate(GROUPS)}
GROUP_LABEL_AREA = 20_000.0
ROW_ID = re.compile(
    r"^(API-(?:AUTH|READ|WRITE|SCHED|DEVICE|LOCAL|SETUP)|MGT-(?:STATE|COMMAND|LIFECYCLE|WORKFLOW|QUALITY))-(\d{2})$"
)
DATE_LINE = re.compile(r"^Last reviewed: \*\*(\d{4}-\d{2}-\d{2})\*\*$", re.M)
POSSIBLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*[-_]\d+")
GROUP_PREFIX = re.compile(r"^(?:API|MGT)(?:\b|[-_])", re.I)

PANEL_LAYOUT = {
    "width": 1440,
    "height": 744,
    "margin_x": 19,
    "gap": 16,
    "panel_y": 96,
    "panel_height": 566,
    "map_inset_x": 14,
    "map_width": 665,
    "map_y_offset": 82,
    "map_height": 466,
}
GROUP_HEADER_HEIGHT = 48.0


@dataclass(frozen=True)
class Feature:
    """One inventory row to be represented by exactly one tile."""

    group: str
    identifier: str
    name: str
    status: str


@dataclass(frozen=True)
class Rect:
    """A positioned rectangle in the SVG viewBox."""

    x: float
    y: float
    width: float
    height: float


def fail(message: str) -> ValueError:
    """Build a concise input or generated-artifact diagnostic."""
    return ValueError(message)


def table_cells(line: str) -> list[str] | None:
    """Split a pipe table row while allowing escaped literal pipes in cells."""
    candidate = line.strip()
    if not candidate.startswith("|") or not candidate.endswith("|"):
        return None
    cells = re.split(r"(?<!\\)\|", candidate[1:-1])
    return [cell.replace(r"\|", "|").strip() for cell in cells]


def marked_region(text: str) -> tuple[int, int]:
    """Return the offsets inside the unique generated-content markers."""
    if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise fail("progress.md must contain each API progress marker exactly once")
    start = text.index(START_MARKER) + len(START_MARKER)
    end = text.index(END_MARKER)
    if end < start:
        raise fail("API progress end marker appears before its start marker")
    return start, end


def reviewed_date(text: str) -> str:
    """Read and validate the review date displayed in the SVG."""
    matches = DATE_LINE.findall(text)
    if len(matches) != 1:
        raise fail("progress.md must contain one 'Last reviewed: **YYYY-MM-DD**' line")
    try:
        date.fromisoformat(matches[0])
    except ValueError as error:
        raise fail("Last reviewed date must be a real YYYY-MM-DD date") from error
    return matches[0]


def parse_features(text: str) -> list[Feature]:
    """Read recognized feature rows and reject malformed or duplicate entries."""
    start, end = marked_region(text)
    source = text[:start] + "\n" + text[end:]
    features: list[Feature] = []
    seen: set[str] = set()

    for line_number, line in enumerate(source.splitlines(), start=1):
        cells = table_cells(line)
        if cells is None or not cells:
            continue
        identifier = cells[0]
        # Reject malformed IDs from either inventory family while ignoring
        # headings, separators, and the other protocol references in prose.
        if not GROUP_PREFIX.match(identifier) and not POSSIBLE_ID.match(identifier):
            continue
        match = ROW_ID.fullmatch(identifier)
        if not match:
            raise fail(f"invalid API feature ID in progress.md near line {line_number}")
        if len(cells) != 4:
            raise fail(f"API feature row {identifier} must have exactly four columns")
        if identifier in seen:
            raise fail(f"duplicate API feature ID: {identifier}")
        if not cells[1]:
            raise fail(f"API feature row {identifier} must have a name")
        status = cells[2]
        if status not in STATUSES:
            raise fail(
                f"invalid status for {identifier}; use Implemented, Partial, or Missing"
            )
        group = match.group(1)
        seen.add(identifier)
        features.append(Feature(group, identifier, cells[1], status))

    if not features:
        raise fail("progress.md contains no API feature rows")
    if not any(feature.identifier.startswith("API-") for feature in features):
        raise fail("progress.md must contain at least one raw API method row")
    if not any(feature.identifier.startswith("MGT-") for feature in features):
        raise fail("progress.md must contain at least one integration/management row")
    return features


def tally(features: list[Feature]) -> dict[str, dict[str, int]]:
    """Count feature statuses for every inventory group."""
    result = {
        group: {**dict.fromkeys(STATUSES, 0), "Total": 0} for group, _label in GROUPS
    }
    for feature in features:
        result[feature.group][feature.status] += 1
        result[feature.group]["Total"] += 1
    return result


def panel_tallies(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Aggregate the API and management groups into independent panels."""
    result = {panel: {**dict.fromkeys(STATUSES, 0), "Total": 0} for panel in PANELS}
    for group, row in counts.items():
        panel = group.split("-", maxsplit=1)[0]
        for status in STATUSES:
            result[panel][status] += row[status]
        result[panel]["Total"] += row["Total"]
    return result


def percentage(implemented: int, total: int) -> str:
    """Format whole percentages cleanly and retain one decimal when needed."""
    value = implemented * 100 / total
    return f"{value:.1f}".removesuffix(".0")


def render_markdown(counts: dict[str, dict[str, int]]) -> str:
    """Build the two-panel summary inserted between the source markers."""
    panels = panel_tallies(counts)
    lines: list[str] = []
    image_alt = []
    for panel, info in PANELS.items():
        row = panels[panel]
        percent = percentage(row["Implemented"], row["Total"])
        image_alt.append(
            f"{info['label']}: {row['Implemented']} of {row['Total']} implemented ({percent} percent)"
        )
    lines.extend(
        [
            f"![{' ; '.join(image_alt)}](api-progress.svg)",
            "",
        ]
    )
    for panel, info in PANELS.items():
        row = panels[panel]
        percent = percentage(row["Implemented"], row["Total"])
        lines.extend(
            [
                f"**{info['number']} {info['label']}:** {row['Implemented']} of {row['Total']} known rows fully implemented ({percent}%). Partial: {row['Partial']}; missing: {row['Missing']}.",
                "",
            ]
        )
    lines.extend(
        [
            "| Layer | Category | Implemented | Partial | Missing | Total |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for group, label in GROUPS:
        panel = group.split("-", maxsplit=1)[0]
        row = counts[group]
        lines.append(
            f"| {PANELS[panel]['label']} | {label} | {row['Implemented']} | {row['Partial']} | {row['Missing']} | {row['Total']} |"
        )
    return "\n".join(lines)


def _rect_area(rect: Rect) -> float:
    return rect.width * rect.height


def _worst_aspect(areas: list[float], side: float) -> float:
    """Return the worst rectangle aspect ratio for one proposed treemap row."""
    if not areas or min(areas) <= 0:
        return math.inf
    total = sum(areas)
    return max(side**2 * max(areas) / total**2, total**2 / (side**2 * min(areas)))


def _layout_row(areas: list[float], remaining: Rect) -> tuple[list[Rect], Rect]:
    """Lay out a strip of treemap cells and return the unused rectangle."""
    row_area = sum(areas)
    if remaining.width >= remaining.height:
        strip_width = row_area / remaining.height
        boxes: list[Rect] = []
        y = remaining.y
        for area in areas:
            height = area / strip_width
            boxes.append(Rect(remaining.x, y, strip_width, height))
            y += height
        return boxes, Rect(
            remaining.x + strip_width,
            remaining.y,
            max(0.0, remaining.width - strip_width),
            remaining.height,
        )

    strip_height = row_area / remaining.width
    boxes = []
    x = remaining.x
    for area in areas:
        width = area / strip_height
        boxes.append(Rect(x, remaining.y, width, strip_height))
        x += width
    return boxes, Rect(
        remaining.x,
        remaining.y + strip_height,
        remaining.width,
        max(0.0, remaining.height - strip_height),
    )


def squarified_treemap(
    weights: list[tuple[str, float]], bounds: Rect
) -> dict[str, Rect]:
    """Assign proportional group rectangles with a squarified treemap."""
    total_weight = sum(weight for _key, weight in weights)
    if not weights or total_weight <= 0 or bounds.width <= 0 or bounds.height <= 0:
        return {}
    scale = _rect_area(bounds) / total_weight
    remaining_items = [
        (key, weight * scale)
        for key, weight in sorted(weights, key=lambda item: -item[1])
    ]
    remaining = bounds
    result: dict[str, Rect] = {}
    while remaining_items:
        row: list[tuple[str, float]] = []
        side = min(remaining.width, remaining.height)
        while remaining_items:
            candidate = remaining_items[0]
            before = _worst_aspect([area for _key, area in row], side)
            after = _worst_aspect([area for _key, area in (*row, candidate)], side)
            if row and after > before:
                break
            row.append(remaining_items.pop(0))
        boxes, remaining = _layout_row([area for _key, area in row], remaining)
        for (key, _area), box in zip(row, boxes, strict=True):
            result[key] = box
    return result


def _grid_for_group(
    feature_count: int,
    tile_side: float,
    available_width: float,
    available_height: float,
    gap: float,
) -> tuple[int, int, float, float] | None:
    """Choose a fitting rows/columns arrangement for equal-size group tiles."""
    if feature_count <= 0 or available_width <= 0 or available_height <= 0:
        return None
    choices: list[tuple[float, int, int, float, float]] = []
    for columns in range(1, feature_count + 1):
        rows = math.ceil(feature_count / columns)
        grid_width = columns * tile_side + (columns - 1) * gap
        grid_height = rows * tile_side + (rows - 1) * gap
        if grid_width > available_width + 0.01 or grid_height > available_height + 0.01:
            continue
        aspect_penalty = abs(
            math.log((grid_width / grid_height) / (available_width / available_height))
        )
        blank_slot_penalty = (columns * rows - feature_count) / feature_count
        score = aspect_penalty + 0.04 * blank_slot_penalty
        choices.append((score, columns, rows, grid_width, grid_height))
    if not choices:
        return None
    _score, columns, rows, grid_width, grid_height = min(choices)
    return columns, rows, grid_width, grid_height


def _wrap_label(label: str, max_characters: int) -> list[str]:
    """Wrap a short category heading at word boundaries for small blocks."""
    words = label.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_characters:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [label]


def _group_tile_side(
    group_rects: dict[str, Rect], counts: dict[str, dict[str, int]], panel: str
) -> float:
    """Find one uniform tile size that fits every group in a panel."""
    panel_groups = [group for group, _label in GROUPS if group.startswith(f"{panel}-")]
    feature_count = sum(counts[group]["Total"] for group in panel_groups)
    group_count = sum(counts[group]["Total"] > 0 for group in panel_groups)
    map_area = PANEL_LAYOUT["map_width"] * PANEL_LAYOUT["map_height"]
    label_area = GROUP_LABEL_AREA
    item_area = (map_area - group_count * label_area) / feature_count
    tile_side = min(58.0, math.sqrt(max(144.0, item_area * 0.63)))
    gap = 2.5
    while tile_side >= 12:
        fits = True
        for group in panel_groups:
            count = counts[group]["Total"]
            if count == 0:
                continue
            rect = group_rects[group]
            inner_width = rect.width - 8
            inner_height = rect.height - 8
            available_height = inner_height - GROUP_HEADER_HEIGHT
            if (
                _grid_for_group(
                    count, tile_side, inner_width - 8, available_height - 3, gap
                )
                is None
            ):
                fits = False
                break
        if fits:
            return tile_side
        tile_side -= 1
    return max(12.0, tile_side)


def _render_group_tiles(
    group: str,
    group_rect: Rect,
    features: list[Feature],
    tile_side: float,
) -> str:
    """Render one category block and its real inventory-row tiles."""
    label = GROUP_LABELS[group]
    inset = 3.0
    box = Rect(
        group_rect.x + inset,
        group_rect.y + inset,
        max(0.0, group_rect.width - 2 * inset),
        max(0.0, group_rect.height - 2 * inset),
    )
    if box.width < 26 or box.height < 40:
        raise fail(f"layout could not fit the {label} feature group")
    header_height = GROUP_HEADER_HEIGHT
    content_x = box.x + 4
    content_y = box.y + header_height + 2
    available_width = box.width - 8
    available_height = box.height - header_height - 5
    tile_gap = 2.5
    layout = _grid_for_group(
        len(features), tile_side, available_width, available_height, tile_gap
    )
    if layout is None:
        raise fail(f"layout could not fit every {label} inventory tile")
    columns, _rows, grid_width, grid_height = layout
    start_x = content_x + (available_width - grid_width) / 2
    start_y = content_y + (available_height - grid_height) / 2

    safe_group = re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-")
    category_clip = f"category-{safe_group}"
    header_font = 13.0 if box.width > 135 else 11.4
    category_width = max(0.0, box.width - 12)
    max_characters = max(11, int(category_width / (header_font * 0.53)))
    display_label = label
    if any(len(word) > max_characters for word in label.split()):
        display_label = SHORT_GROUP_LABELS.get(group, label)
    label_lines = _wrap_label(display_label, max_characters)
    count_kind = "METHODS" if group.startswith("API-") else "CHECKS"
    implemented = sum(feature.status == "Implemented" for feature in features)
    count_label = f"{implemented}/{len(features)} {count_kind}"
    lower_kind = count_kind.lower()
    count_font = min(10.0, category_width / (len(count_label) * 0.62))
    count_font = max(8.0, count_font)
    label_text = (
        f'<text class="group-name" clip-path="url(#{category_clip})" x="{box.x + 5:.2f}" y="{box.y + (14 if len(label_lines) > 1 else 18):.2f}" font-size="{header_font:.1f}">'
        + "".join(
            f'<tspan x="{box.x + 5:.2f}" dy="{0 if index == 0 else 13:.2f}">{escape(line)}</tspan>'
            for index, line in enumerate(label_lines)
        )
        + "</text>"
    )
    group_svg = [
        f'<g class="feature-group" role="group" aria-label="{escape(label, quote=True)}, {implemented} of {len(features)} {lower_kind} implemented">',
        f"<title>{escape(label)} — {implemented}/{len(features)} {lower_kind} implemented</title>",
        f'<rect class="group-backdrop" x="{box.x:.2f}" y="{box.y:.2f}" width="{box.width:.2f}" height="{box.height:.2f}" rx="2" />',
        f'<defs><clipPath id="{category_clip}"><rect x="{box.x + 3:.2f}" y="{box.y:.2f}" width="{category_width:.2f}" height="{header_height:.2f}" /></clipPath></defs>',
        label_text,
        f'<text class="group-count" x="{box.x + 5:.2f}" y="{box.y + 43:.2f}" font-size="{count_font:.1f}">{count_label}</text>',
    ]
    for index, feature in enumerate(features):
        column = index % columns
        row = index // columns
        x = start_x + column * (tile_side + tile_gap)
        y = start_y + row * (tile_side + tile_gap)
        suffix = feature.identifier.rsplit("-", maxsplit=1)[1]
        group_svg.extend(
            [
                f'<rect class="tile status-{feature.status.lower()}" data-feature-id="{escape(feature.identifier, quote=True)}" x="{x:.2f}" y="{y:.2f}" width="{tile_side:.2f}" height="{tile_side:.2f}" rx="1.5">',
                f"<title>{escape(feature.identifier)} — {escape(feature.name)} — {escape(feature.status)}</title>",
                "</rect>",
                f'<text class="tile-id" x="{x + tile_side / 2:.2f}" y="{y + tile_side / 2 + 3.3:.2f}" text-anchor="middle">{escape(suffix)}</text>',
            ]
        )
    group_svg.append("</g>")
    return "".join(group_svg)


def _render_panel(
    panel: str,
    features: list[Feature],
    counts: dict[str, dict[str, int]],
) -> str:
    """Render one independent panel with its count-proportional group map."""
    info = PANELS[panel]
    panel_counts = panel_tallies(counts)[panel]
    panel_x = PANEL_LAYOUT["margin_x"] + (
        0 if panel == "API" else 693 + PANEL_LAYOUT["gap"]
    )
    panel_width = 693.0
    panel_y = PANEL_LAYOUT["panel_y"]
    panel_height = PANEL_LAYOUT["panel_height"]
    map_x = panel_x + PANEL_LAYOUT["map_inset_x"]
    map_y = PANEL_LAYOUT["panel_y"] + PANEL_LAYOUT["map_y_offset"]
    map_width = panel_width - 2 * PANEL_LAYOUT["map_inset_x"]
    map_height = PANEL_LAYOUT["map_height"]
    map_bounds = Rect(map_x, map_y, map_width, map_height)

    groups = [
        group
        for group, _label in GROUPS
        if group.startswith(f"{panel}-") and counts[group]["Total"] > 0
    ]
    if not groups:
        raise fail(f"progress.md must contain at least one {info['label']} feature")
    group_count = len(groups)
    feature_count = sum(counts[group]["Total"] for group in groups)
    label_area = GROUP_LABEL_AREA
    item_area = (map_width * map_height - group_count * label_area) / feature_count
    weights = [
        (group, item_area * counts[group]["Total"] + label_area) for group in groups
    ]
    group_rects = squarified_treemap(weights, map_bounds)
    tile_side = _group_tile_side(group_rects, counts, panel)

    parts = [
        f'<g class="panel" role="group" aria-label="{escape(info["label"], quote=True)}">',
        f'<rect class="panel-frame" x="{panel_x}" y="{panel_y}" width="{panel_width}" height="{panel_height}" rx="8" />',
        f'<text class="panel-index" x="{panel_x + 18}" y="{panel_y + 29}">{info["number"]}</text>',
        f'<text class="panel-title" x="{panel_x + 49}" y="{panel_y + 29}">{escape(info["title"])}</text>',
        f'<text class="panel-percent" x="{panel_x + panel_width - 18}" y="{panel_y + 30}" text-anchor="end">{percentage(panel_counts["Implemented"], panel_counts["Total"])}% <tspan class="percent-caption">implemented</tspan></text>',
        f'<text class="panel-counts" x="{panel_x + 18}" y="{panel_y + 51}">Implemented {panel_counts["Implemented"]}  ·  Partial {panel_counts["Partial"]}  ·  Missing {panel_counts["Missing"]}</text>',
        f'<text class="panel-subtitle" x="{panel_x + 18}" y="{panel_y + 69}">{escape(info["subtitle"])}</text>',
    ]
    features_by_group = {
        group: [feature for feature in features if feature.group == group]
        for group in groups
    }
    parts.extend(
        _render_group_tiles(
            group,
            group_rects[group],
            features_by_group[group],
            tile_side,
        )
        for group in sorted(groups, key=lambda item: GROUP_ORDER[item])
    )
    parts.append("</g>")
    return "".join(parts)


def render_svg(
    features: list[Feature], counts: dict[str, dict[str, int]], reviewed: str
) -> str:
    """Build the standalone two-panel, accessible tiled coverage map."""
    panels = panel_tallies(counts)
    summary = (
        f"Raw API methods: {panels['API']['Implemented']} of {panels['API']['Total']} implemented, "
        f"{panels['API']['Partial']} partial, {panels['API']['Missing']} missing. "
        f"Integration and management: {panels['MGT']['Implemented']} of {panels['MGT']['Total']} implemented, "
        f"{panels['MGT']['Partial']} partial, {panels['MGT']['Missing']} missing. "
        f"Last reviewed {reviewed}. Each tile represents one inventory row."
    )
    panel_svgs = "".join(_render_panel(panel, features, counts) for panel in PANELS)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{PANEL_LAYOUT["width"]}" height="{PANEL_LAYOUT["height"]}" viewBox="0 0 {PANEL_LAYOUT["width"]} {PANEL_LAYOUT["height"]}" role="img" aria-labelledby="coverage-title coverage-description">
  <title id="coverage-title">Mysa consumer API coverage map</title>
  <desc id="coverage-description">{escape(summary)}</desc>
  <style>
    text {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .canvas {{ fill: #0b1117; }}
    .page-title {{ fill: #edf2f6; font-size: 20px; font-weight: 650; letter-spacing: -.25px; }}
    .page-subtitle {{ fill: #9aabba; font-size: 12px; }}
    .reviewed {{ fill: #8190a0; font-size: 11px; }}
    .panel-frame {{ fill: #101821; stroke: #26313d; stroke-width: 1; }}
    .panel-index {{ fill: #6f8797; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; font-weight: 700; }}
    .panel-title {{ fill: #e8eef3; font-size: 16px; font-weight: 700; letter-spacing: .7px; }}
    .panel-percent {{ fill: #dfe8ed; font-size: 14px; font-weight: 650; }}
    .percent-caption {{ fill: #92a2ae; font-size: 9px; font-weight: 500; }}
    .panel-counts {{ fill: #9aabb7; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10px; }}
    .panel-subtitle {{ fill: #788a98; font-size: 12px; }}
    .group-backdrop {{ fill: #141e28; stroke: #30404e; stroke-width: 1; }}
    .group-name {{ fill: #dbe4e9; font-weight: 620; }}
    .group-count {{ fill: #7f919e; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10px; letter-spacing: .7px; }}
    .tile {{ stroke: #0b1117; stroke-width: 1.4; }}
    .swatch {{ stroke: #0b1117; stroke-width: 1.4; }}
    .status-implemented {{ fill: #19734f; }}
    .status-partial {{ fill: url(#partial-pattern); }}
    .status-missing {{ fill: url(#missing-pattern); stroke: #566574; stroke-width: 1.1; }}
    .tile-id {{ fill: #f1f5f7; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; font-weight: 650; pointer-events: none; }}
    .legend-title {{ fill: #94a5b1; font-size: 10px; font-weight: 650; letter-spacing: .8px; }}
    .legend-label {{ fill: #c6d0d7; font-size: 11px; }}
    .legend-note {{ fill: #7f909d; font-size: 11px; }}
  </style>
  <defs>
    <pattern id="partial-pattern" width="8" height="8" patternUnits="userSpaceOnUse">
      <rect width="8" height="8" fill="#865b1b" />
      <path d="M-2 2L2-2 M0 8L8 0 M6 10L10 6" fill="none" stroke="#f1bf5c" stroke-width="1.6" opacity=".55" />
    </pattern>
    <pattern id="missing-pattern" width="8" height="8" patternUnits="userSpaceOnUse">
      <rect width="8" height="8" fill="#344251" />
      <circle cx="2" cy="2" r=".8" fill="#a5b2be" opacity=".48" />
      <circle cx="6" cy="6" r=".8" fill="#a5b2be" opacity=".48" />
    </pattern>
  </defs>
  <rect class="canvas" width="100%" height="100%" />
  <text class="page-title" x="28" y="38">Mysa consumer API coverage</text>
  <text class="page-subtitle" x="28" y="60">Direct API support and the integration logic that turns it into dependable device behavior</text>
  <text class="reviewed" x="1412" y="38" text-anchor="end">LAST REVIEWED  {escape(reviewed)}</text>
  {panel_svgs}
  <g role="group" aria-label="Coverage status legend">
    <text class="legend-title" x="28" y="691">STATUS</text>
    <rect class="swatch status-implemented" x="86" y="679" width="16" height="16" rx="1.5" />
    <text class="legend-label" x="109" y="691">Implemented</text>
    <rect class="swatch status-partial" x="211" y="679" width="16" height="16" rx="1.5" />
    <text class="legend-label" x="234" y="691">Partial · diagonal hatch</text>
    <rect class="swatch status-missing" x="390" y="679" width="16" height="16" rx="1.5" />
    <text class="legend-label" x="413" y="691">Missing · dotted field</text>
    <text class="legend-note" x="1412" y="691" text-anchor="end">Each colored tile is one inventory row; group background is spacing and labeling only.</text>
  </g>
  <text class="legend-note" x="28" y="722">Percentages are calculated independently within each panel; Partial rows remain visible and do not earn completion credit.</text>
</svg>
'''


def expected_outputs(text: str) -> tuple[str, str]:
    """Derive the marked Markdown block and SVG from the current source rows."""
    reviewed = reviewed_date(text)
    features = parse_features(text)
    counts = tally(features)
    return render_markdown(counts), render_svg(features, counts, reviewed)


def update_markdown(text: str, generated: str) -> str:
    """Replace only the content within the unique generated-content markers."""
    start, end = marked_region(text)
    return text[:start] + "\n" + generated + "\n" + text[end:]


def run(check: bool) -> int:
    """Validate generated files or write their current rendered versions."""
    try:
        source = TRACKER.read_text(encoding="utf-8")
        generated_markdown, generated_svg = expected_outputs(source)
        expected_tracker = update_markdown(source, generated_markdown)

        if check:
            stale: list[str] = []
            if source != expected_tracker:
                stale.append(
                    "docs/implementation/progress.md generated section is stale"
                )
            try:
                current_svg = SVG_PATH.read_text(encoding="utf-8")
            except OSError:
                stale.append("docs/implementation/api-progress.svg is missing")
            else:
                if current_svg != generated_svg:
                    stale.append("docs/implementation/api-progress.svg is stale")
            if stale:
                for message in stale:
                    print(message, file=sys.stderr)
                return 1
            print("API progress Markdown and SVG are up to date.")
            return 0

        SVG_PATH.write_text(generated_svg, encoding="utf-8")
        TRACKER.write_text(expected_tracker, encoding="utf-8")
        print(
            "Updated docs/implementation/progress.md and docs/implementation/api-progress.svg."
        )
    except (OSError, ValueError) as error:
        print(f"update_api_progress.py: {error}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Parse the optional read-only check mode and run the renderer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the generated Markdown section or SVG is stale; do not write files",
    )
    arguments = parser.parse_args()
    return run(arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
