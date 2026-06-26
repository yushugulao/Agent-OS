#!/usr/bin/env python3
"""Unit checks for plain_ucore_fs_extract."""

from __future__ import annotations

import tempfile
from pathlib import Path

import plain_ucore_fs_extract as fsx


def put_u16(buf: bytearray, offset: int, value: int) -> None:
    buf[offset : offset + 2] = value.to_bytes(2, "little")


def put_u32(buf: bytearray, offset: int, value: int) -> None:
    buf[offset : offset + 4] = value.to_bytes(4, "little")


def put_inode(image: bytearray, inum: int, file_type: int, size: int, addrs: list[int]) -> None:
    offset = (inum // fsx.IPB + 2) * fsx.BSIZE + (inum % fsx.IPB) * fsx.DINODE_SIZE
    put_u16(image, offset, file_type)
    put_u32(image, offset + 8, size)
    for index, addr in enumerate(addrs[: fsx.NDIRECT + 1]):
        put_u32(image, offset + 12 + index * 4, addr)


def put_dirent(block: bytearray, slot: int, inum: int, name: str) -> None:
    offset = slot * 16
    put_u16(block, offset, inum)
    raw = name.encode("utf-8")[: fsx.DIRSIZ]
    block[offset + 2 : offset + 2 + len(raw)] = raw


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        image = bytearray(fsx.BSIZE * 1000)

        # superblock
        put_u32(image, fsx.BSIZE, fsx.FSMAGIC)
        put_u32(image, fsx.BSIZE + 4, 1000)
        put_u32(image, fsx.BSIZE + 8, 980)
        put_u32(image, fsx.BSIZE + 12, 200)
        put_u32(image, fsx.BSIZE + 16, 2)
        put_u32(image, fsx.BSIZE + 20, 15)

        dir_block = bytearray(fsx.BSIZE)
        put_dirent(dir_block, 0, 2, "rp_input")
        put_dirent(dir_block, 1, 3, "rp_artifact_ma")
        put_dirent(dir_block, 2, 4, "rp_orch")
        image[20 * fsx.BSIZE : 21 * fsx.BSIZE] = dir_block
        put_inode(image, 1, 1, fsx.BSIZE, [20])

        input_text = b"status=ready\ncustom_run=usable-run:RUN-900\n"
        image[21 * fsx.BSIZE : 21 * fsx.BSIZE + len(input_text)] = input_text
        put_inode(image, 2, fsx.T_FILE, len(input_text), [21])

        manifest_text = b"manifest_records=4\nstatus=ready\n"
        image[22 * fsx.BSIZE : 22 * fsx.BSIZE + len(manifest_text)] = manifest_text
        put_inode(image, 3, fsx.T_FILE, len(manifest_text), [22])

        binary_text = b"\x7fELF\x00rp_fake_program"
        image[23 * fsx.BSIZE : 23 * fsx.BSIZE + len(binary_text)] = binary_text
        put_inode(image, 4, fsx.T_FILE, len(binary_text), [23])

        image_path = root / "fs-copy.img"
        out_dir = root / "state"
        repo_dir = root / "repo"
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("rp_artifact_manifest\n", encoding="utf-8")
        image_path.write_bytes(image)

        summary = fsx.extract_state_files(image_path, out_dir, repo_dir)
        assert summary["extracted_state_files"] == 2, summary
        assert (out_dir / "rp_input").read_text(encoding="utf-8") == input_text.decode("utf-8")
        assert (out_dir / "rp_artifact_manifest").read_text(encoding="utf-8") == manifest_text.decode("utf-8")
        assert not (out_dir / "rp_orch").exists()
        assert (out_dir / "extract-summary.json").exists()

    print("test_plain_ucore_fs_extract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
