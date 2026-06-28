#!/usr/bin/env python3
"""Unit checks for rendered Host Reader output validation."""

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


def valid_html(title: str) -> str:
    body = "\n".join(f"<p>line-{index}</p>" for index in range(120))
    return f"""<html>
<body>
  <div class="app">
    <aside class="sidebar"><p class="brand">Plain uCore Research</p></aside>
    <main>
      <header><div><h1>{title}</h1><p>Rendered from plain uCore state files.</p></div></header>
      <section class="panel">{body}</section>
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

    print("test_check_reader_output: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
