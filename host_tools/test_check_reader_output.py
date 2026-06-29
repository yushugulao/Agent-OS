#!/usr/bin/env python3
"""Unit checks for rendered 本地结果阅读器 output validation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import check_reader_output as check


def write_summary(root: Path, pages: int, api_json_files: int, state_files: int, status: str = "ready") -> None:
    (root / "reader-summary.json").write_text(
        json.dumps(
            {
                "pages": pages,
                "api_json_files": api_json_files,
                "state_files": state_files,
                "status": status,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def valid_html(title: str, extra: str = "") -> str:
    body = "\n".join(f"<p>line-{index}</p>" for index in range(120))
    return f"""<html>
<body>
  <div class="app">
    <aside class="sidebar"><p class="brand">Plain uCore Research</p></aside>
    <main>
      <header><div><h1>{title}</h1><p>Rendered from plain uCore state files.</p></div></header>
      <section class="panel">{body}</section>
      <section class="panel">{extra}</section>
    </main>
  </div>
</body>
</html>
"""


def write_reader(root: Path) -> None:
    api_dir = root / "api"
    api_dir.mkdir()
    for page, title in sorted(check.EXPECTED_PAGE_TITLES.items()):
        (root / page).write_text(valid_html(title), encoding="utf-8")
    for name in ("rp_backend", "rp_agentcmp"):
        (api_dir / f"{name}.json").write_text(
            json.dumps({"name": name, "values": {}, "lines": []}) + "\n",
            encoding="utf-8",
        )
    write_summary(root, pages=len(check.EXPECTED_PAGE_TITLES), api_json_files=2, state_files=2)


def add_agentos_api(root: Path) -> None:
    api_dir = root / "api"
    for api_name in sorted(check.AGENTOS_COMPARE_API):
        name = api_name[:-5]
        (api_dir / api_name).write_text(
            json.dumps({"name": name, "values": {}, "lines": []}) + "\n",
            encoding="utf-8",
        )
    total_api = 2 + len(check.AGENTOS_COMPARE_API)
    total_state = 2 + len(check.AGENTOS_COMPARE_API)
    write_summary(root, pages=len(check.EXPECTED_PAGE_TITLES), api_json_files=total_api, state_files=total_state)


def agentos_compare_html() -> str:
    return valid_html("Compare", "\n".join(check.AGENTOS_COMPARE_MARKERS))


def expect_failure(root: Path, expected: str) -> None:
    try:
        check.check_reader_output(root)
    except ValueError as exc:
        assert expected in str(exc), str(exc)
        return
    raise AssertionError("reader output unexpectedly passed")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_reader(root)
        summary = check.check_reader_output(root)
        assert summary["pages"] == len(check.EXPECTED_PAGE_TITLES), summary
        assert summary["api_json"] == 2, summary
        assert summary["spec_pages"] == len(check.EXPECTED_PAGE_TITLES), summary
        assert summary["agentos_compare_markers"] == 0, summary

        (root / "compare.html").write_text("<html>broken</html>\n", encoding="utf-8")
        expect_failure(root, "unexpectedly small")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_reader(root)
        (root / "compare.html").write_text(valid_html("Wrong Title"), encoding="utf-8")
        expect_failure(root, "missing expected title")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_reader(root)
        (root / "api" / "rp_agentcmp.json").write_text(
            json.dumps({"name": "wrong", "values": {}, "lines": []}) + "\n",
            encoding="utf-8",
        )
        expect_failure(root, "name does not match")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_reader(root)
        (root / "api" / "host_alignment.json").write_text(
            json.dumps({"name": "host_alignment", "values": {}, "lines": []}) + "\n",
            encoding="utf-8",
        )
        write_summary(root, pages=len(check.EXPECTED_PAGE_TITLES), api_json_files=3, state_files=2)
        summary = check.check_reader_output(root)
        assert summary["api_json"] == 3, summary

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_reader(root)
        write_summary(root, pages=len(check.EXPECTED_PAGE_TITLES), api_json_files=2, state_files=3)
        expect_failure(root, "API count is smaller")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_reader(root)
        add_agentos_api(root)
        expect_failure(root, "missing kernel evidence marker")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_reader(root)
        add_agentos_api(root)
        (root / "compare.html").write_text(agentos_compare_html(), encoding="utf-8")
        summary = check.check_reader_output(root)
        assert summary["agentos_compare_markers"] == len(check.AGENTOS_COMPARE_MARKERS), summary

    print("test_check_reader_output: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
