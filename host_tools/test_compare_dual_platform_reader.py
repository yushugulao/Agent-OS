#!/usr/bin/env python3
"""Unit checks for Host Reader summary comparison."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import compare_dual_platform_reader as compare


def write_summary(path: Path, pages: int, state_files: int, api_json_files: int, status: str = "ready") -> None:
    path.write_text(
        json.dumps(
            {
                "pages": pages,
                "state_files": state_files,
                "api_json_files": api_json_files,
                "status": status,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_reader_files(root: Path, pages: list[str], api_json: list[str]) -> None:
    api_dir = root / "api"
    api_dir.mkdir(exist_ok=True)
    for page in pages:
        (root / page).write_text("<html></html>\n", encoding="utf-8")
    for name in api_json:
        (api_dir / name).write_text("{}\n", encoding="utf-8")


def expect_failure(plain: Path, agentos: Path, expected: str) -> None:
    try:
        compare.compare_reader(plain, agentos)
    except ValueError as exc:
        assert expected in str(exc), str(exc)
        return
    raise AssertionError("comparison unexpectedly passed")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plain_root = root / "plain"
        agentos_root = root / "agentos"
        plain_root.mkdir()
        agentos_root.mkdir()
        plain = plain_root / "reader-summary.json"
        agentos = agentos_root / "reader-summary.json"

        write_summary(plain, pages=41, state_files=256, api_json_files=256)
        write_summary(agentos, pages=41, state_files=268, api_json_files=268)
        write_reader_files(plain_root, ["index.html", "compare.html"], ["rp_backend.json", "rp_agentcmp.json"])
        write_reader_files(agentos_root, ["index.html", "compare.html"], ["rp_backend.json", "rp_agentcmp.json", "rp_agentos_kernel.json"])
        summary = compare.compare_reader(plain, agentos)
        assert summary["plain_pages"] == 41, summary
        assert summary["agentos_state_files"] == 268, summary
        assert summary["agentos_extra_state_files"] == 12, summary
        assert summary["agentos_extra_api_json"] == 12, summary
        assert summary["checked_pages"] == 2, summary
        assert summary["checked_api_json"] == 2, summary

        write_summary(agentos, pages=40, state_files=268, api_json_files=268)
        expect_failure(plain, agentos, "page count differs")

        write_summary(agentos, pages=41, state_files=200, api_json_files=268)
        expect_failure(plain, agentos, "fewer state files")

        write_summary(agentos, pages=41, state_files=268, api_json_files=200)
        expect_failure(plain, agentos, "fewer API JSON files")

        write_summary(agentos, pages=41, state_files=268, api_json_files=268)
        (agentos_root / "compare.html").unlink()
        expect_failure(plain, agentos, "page set differs")

        (agentos_root / "compare.html").write_text("<html></html>\n", encoding="utf-8")
        (agentos_root / "api" / "rp_agentcmp.json").unlink()
        expect_failure(plain, agentos, "missing plain files")

    print("test_compare_dual_platform_reader: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
