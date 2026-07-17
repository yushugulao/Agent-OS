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


def put_inode(
    image: bytearray,
    inum: int,
    file_type: int,
    size: int,
    addrs: list[int],
    dinode_size: int,
) -> None:
    ipb = fsx.BSIZE // dinode_size
    offset = (inum // ipb + 2) * fsx.BSIZE + (inum % ipb) * dinode_size
    put_u16(image, offset, file_type)
    put_u32(image, offset + 8, size)
    for index, addr in enumerate(addrs[: fsx.NDIRECT + 1]):
        put_u32(image, offset + 12 + index * 4, addr)


def put_vfs_label(
    image: bytearray,
    inum: int,
    file_type: int,
    policy: int,
    dinode_size: int,
) -> None:
    ipb = fsx.BSIZE // dinode_size
    offset = (inum // ipb + 2) * fsx.BSIZE + (inum % ipb) * dinode_size
    if policy == fsx.VFS_POLICY_ROOT:
        flags = fsx.VFS_LABEL_F_ROOT
        domain = fsx.VFS_DOMAIN_PUBLIC
    elif policy == fsx.VFS_POLICY_WORKFLOW:
        flags = fsx.VFS_LABEL_F_PROTECTED
        domain = fsx.VFS_DOMAIN_WORKFLOW
    elif policy == fsx.VFS_POLICY_PUBLIC:
        flags = fsx.VFS_LABEL_F_PUBLIC
        domain = fsx.VFS_DOMAIN_PUBLIC
    else:
        raise AssertionError(f"unsupported test policy {policy}")
    words = [
        fsx.VFS_LABEL_MAGIC,
        fsx.VFS_LABEL_VERSION,
        flags,
        domain,
        policy,
        fsx.VFS_EXEC_PROFILE_NONE,
        fsx.VFS_POLICY_GENERATION,
        1,
        0,
        0,
    ]
    for index, value in enumerate(words):
        put_u32(image, offset + 84 + index * 4, value)
    put_u32(image, offset + 124, fsx.vfs_label_checksum(inum, words))


def put_dirent(block: bytearray, slot: int, inum: int, name: str) -> None:
    offset = slot * 16
    put_u16(block, offset, inum)
    raw = name.encode("utf-8")[: fsx.DIRSIZ]
    block[offset + 2 : offset + 2 + len(raw)] = raw


def run_case(
    root: Path,
    magic: int,
    dinode_size: int,
    tag: str,
    public_first: bool = False,
) -> None:
    image = bytearray(fsx.BSIZE * 1000)

    # superblock
    put_u32(image, fsx.BSIZE, magic)
    put_u32(image, fsx.BSIZE + 4, 1000)
    put_u32(image, fsx.BSIZE + 8, 980)
    put_u32(image, fsx.BSIZE + 12, 200)
    put_u32(image, fsx.BSIZE + 16, 2)
    put_u32(image, fsx.BSIZE + 20, 15)

    dir_block = bytearray(fsx.BSIZE)
    entries = [
        (2, "rp_input"),
        (3, "rp_artifact_ma"),
        (4, "rp_orch"),
        (5, "rp_agentos_col"),
    ]
    if magic == fsx.FSMAGIC_VFS_POLICY:
        if public_first:
            entries.insert(0, (6, "rp_input"))
        else:
            entries.append((6, "rp_input"))
    for slot, (inum, name) in enumerate(entries):
        put_dirent(dir_block, slot, inum, name)
    image[20 * fsx.BSIZE : 21 * fsx.BSIZE] = dir_block
    put_inode(image, 1, 1, fsx.BSIZE, [20], dinode_size)
    if magic == fsx.FSMAGIC_VFS_POLICY:
        put_vfs_label(
            image, 1, 1, fsx.VFS_POLICY_ROOT, dinode_size
        )

    input_text = b"status=ready\ncustom_run=usable-run:RUN-900\n"
    image[21 * fsx.BSIZE : 21 * fsx.BSIZE + len(input_text)] = input_text
    put_inode(image, 2, fsx.T_FILE, len(input_text), [21], dinode_size)
    if magic == fsx.FSMAGIC_VFS_POLICY:
        put_vfs_label(
            image, 2, fsx.T_FILE, fsx.VFS_POLICY_WORKFLOW, dinode_size
        )

    manifest_text = b"manifest_records=4\nstatus=ready\n"
    image[22 * fsx.BSIZE : 22 * fsx.BSIZE + len(manifest_text)] = manifest_text
    put_inode(image, 3, fsx.T_FILE, len(manifest_text), [22], dinode_size)
    if magic == fsx.FSMAGIC_VFS_POLICY:
        put_vfs_label(
            image, 3, fsx.T_FILE, fsx.VFS_POLICY_WORKFLOW, dinode_size
        )

    binary_text = b"\x7fELF\x00rp_fake_program"
    image[23 * fsx.BSIZE : 23 * fsx.BSIZE + len(binary_text)] = binary_text
    put_inode(image, 4, fsx.T_FILE, len(binary_text), [23], dinode_size)
    if magic == fsx.FSMAGIC_VFS_POLICY:
        put_vfs_label(
            image, 4, fsx.T_FILE, fsx.VFS_POLICY_WORKFLOW, dinode_size
        )

    ack_text = b"delivery=kernel_event_queue\nstatus=ready\n"
    image[24 * fsx.BSIZE : 24 * fsx.BSIZE + len(ack_text)] = ack_text
    put_inode(image, 5, fsx.T_FILE, len(ack_text), [24], dinode_size)
    if magic == fsx.FSMAGIC_VFS_POLICY:
        put_vfs_label(
            image, 5, fsx.T_FILE, fsx.VFS_POLICY_WORKFLOW, dinode_size
        )

        public_text = b"status=public\n"
        image[25 * fsx.BSIZE : 25 * fsx.BSIZE + len(public_text)] = public_text
        put_inode(image, 6, fsx.T_FILE, len(public_text), [25], dinode_size)
        put_vfs_label(
            image, 6, fsx.T_FILE, fsx.VFS_POLICY_PUBLIC, dinode_size
        )

    case_dir = root / tag
    image_path = case_dir / "fs-copy.img"
    out_dir = case_dir / "state"
    repo_dir = case_dir / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "README.md").write_text(
        "rp_artifact_manifest\nrp_agentos_collab_ack\n",
        encoding="utf-8",
    )
    image_path.write_bytes(image)

    summary = fsx.extract_state_files(image_path, out_dir, repo_dir)
    assert summary["extracted_state_files"] == 3, summary
    assert (out_dir / "rp_input").read_text(encoding="utf-8") == input_text.decode("utf-8")
    assert (out_dir / "rp_artifact_manifest").read_text(encoding="utf-8") == manifest_text.decode("utf-8")
    assert (out_dir / "rp_agentos_collab_ack").read_text(encoding="utf-8") == ack_text.decode("utf-8")
    assert not (out_dir / "rp_orch").exists()
    assert (out_dir / "extract-summary.json").exists()

    if magic == fsx.FSMAGIC_VFS_POLICY:
        corrupt = bytearray(image)
        ipb = fsx.BSIZE // dinode_size
        inode_offset = (2 // ipb + 2) * fsx.BSIZE + (2 % ipb) * dinode_size
        put_u32(corrupt, inode_offset + 124, 0)
        corrupt_path = case_dir / "corrupt.img"
        corrupt_path.write_bytes(corrupt)
        try:
            fsx.extract_state_files(
                corrupt_path, case_dir / "corrupt-state", repo_dir
            )
        except ValueError as error:
            assert "VFS label" in str(error)
        else:
            raise AssertionError("corrupt VFS label accepted")

        duplicate = bytearray(image)
        put_vfs_label(
            duplicate,
            6,
            fsx.T_FILE,
            fsx.VFS_POLICY_WORKFLOW,
            dinode_size,
        )
        duplicate_path = case_dir / "duplicate.img"
        duplicate_path.write_bytes(duplicate)
        try:
            fsx.extract_state_files(
                duplicate_path, case_dir / "duplicate-state", repo_dir
            )
        except ValueError as error:
            assert "duplicate workflow" in str(error)
        else:
            raise AssertionError("duplicate workflow name accepted")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_case(root, fsx.FSMAGIC_LEGACY, fsx.DINODE_SIZE_LEGACY, "legacy")
        run_case(
            root,
            fsx.FSMAGIC_EXEC_POLICY,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "exec-policy",
        )
        run_case(
            root,
            fsx.FSMAGIC_VFS_POLICY,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "vfs-policy-workflow-first",
        )
        run_case(
            root,
            fsx.FSMAGIC_VFS_POLICY,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "vfs-policy-public-first",
            public_first=True,
        )

    print("test_plain_ucore_fs_extract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
