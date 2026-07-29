#!/usr/bin/env python3
"""Check generated SVG charts for readable text placement."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import summarize_dual_platform_results as summary
from test_summarize_dual_platform_results import fixture


SVG_NS = "{http://www.w3.org/2000/svg}"
TEXT_MARGIN = 8.0


@dataclass(frozen=True)
class TextBox:
    text: str
    css_class: str
    left: float
    top: float
    right: float
    bottom: float


def font_size(css_class: str) -> float:
    if "title" in css_class:
        return 20.0
    if "subtitle" in css_class:
        return 13.0
    return 12.0


def text_width(text: str, size: float) -> float:
    total = 0.0
    for char in text:
        if ord(char) < 128:
            total += size * 0.58
        else:
            total += size * 0.88
    return total


def parse_number(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def text_box(element: ET.Element) -> TextBox:
    text = "".join(element.itertext()).strip()
    css_class = element.attrib.get("class", "")
    size = font_size(css_class)
    x = parse_number(element.attrib.get("x"))
    y = parse_number(element.attrib.get("y"))
    width = text_width(text, size)
    anchor = element.attrib.get("text-anchor", "start")
    if anchor == "middle":
        left = x - width / 2.0
        right = x + width / 2.0
    elif anchor == "end":
        left = x - width
        right = x
    else:
        left = x
        right = x + width
    return TextBox(
        text=text,
        css_class=css_class,
        left=left,
        top=y - size * 0.82,
        right=right,
        bottom=y + size * 0.24,
    )


def intersects(left: TextBox, right: TextBox) -> bool:
    overlap_w = min(left.right, right.right) - max(left.left, right.left)
    overlap_h = min(left.bottom, right.bottom) - max(left.top, right.top)
    return overlap_w > 2.0 and overlap_h > 2.0 and overlap_w * overlap_h > 18.0


def validate_chart(path: Path) -> None:
    root = ET.parse(path).getroot()
    width = parse_number(root.attrib.get("width"))
    height = parse_number(root.attrib.get("height"))
    boxes = [text_box(element) for element in root.iter(f"{SVG_NS}text") if "".join(element.itertext()).strip()]
    assert boxes, f"{path.name} has no text labels"
    for box in boxes:
        assert box.left >= -TEXT_MARGIN, (path.name, "left", box)
        assert box.right <= width + TEXT_MARGIN, (path.name, "right", width, box)
        assert box.top >= -TEXT_MARGIN, (path.name, "top", box)
        assert box.bottom <= height + TEXT_MARGIN, (path.name, "bottom", height, box)
    for index, left in enumerate(boxes):
        for right in boxes[index + 1 :]:
            assert not intersects(left, right), (path.name, left, right)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
        work_dir = Path(work_tmp)
        out_dir = Path(out_tmp)
        fixture(work_dir, measured=True)
        summary.summarize(work_dir, out_dir, require_measured_experiments=True)
        charts = sorted((out_dir / "charts").glob("*.svg"))
        assert len(charts) == 3, charts
        for chart in charts:
            validate_chart(chart)
    doc_charts = [
        repo_root / "docs" / "assets" / "verification-charts" / name
        for name in (
            "cost-replacement.svg",
            "runtime-observation.svg",
        )
    ]
    assert all(chart.is_file() for chart in doc_charts), doc_charts
    for chart in doc_charts:
        validate_chart(chart)
    print("test_chart_svg_layout_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
