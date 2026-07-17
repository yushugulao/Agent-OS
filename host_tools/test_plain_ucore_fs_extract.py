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
    fs_magic: int | None = None,
) -> None:
    ipb = fsx.BSIZE // dinode_size
    offset = (inum // ipb + 2) * fsx.BSIZE + (inum % ipb) * dinode_size
    put_u16(image, offset, file_type)
    if fs_magic == fsx.FSMAGIC_BASELINE_QUOTA:
        owner_domain = 0 if file_type == 0 else 1
        owner_version = 0 if file_type == 0 else fsx.FS_OWNER_VERSION
        put_u16(image, offset + 2, owner_version)
        put_u32(image, offset + 4, owner_domain)
    put_u32(image, offset + 8, size)
    for index, addr in enumerate(addrs[: fsx.NDIRECT + 1]):
        put_u32(image, offset + 12 + index * 4, addr)


def put_vfs_label(
    image: bytearray,
    inum: int,
    file_type: int,
    policy: int,
    dinode_size: int,
    fs_magic: int = fsx.FSMAGIC_VFS_POLICY,
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
    elif policy == fsx.VFS_POLICY_FREE:
        flags = fsx.VFS_LABEL_F_FREE
        domain = fsx.VFS_DOMAIN_PUBLIC
    else:
        raise AssertionError(f"unsupported test policy {policy}")
    quota_format = fs_magic == fsx.FSMAGIC_AGENT_QUOTA
    owner_domain = 0 if file_type == 0 or not quota_format else 1
    owner_version = 0 if owner_domain == 0 else fsx.FS_OWNER_VERSION
    words = [
        fsx.VFS_LABEL_MAGIC,
        (
            fsx.VFS_LABEL_VERSION_QUOTA
            if quota_format
            else fsx.VFS_LABEL_VERSION
        ),
        flags,
        domain,
        policy,
        fsx.VFS_EXEC_PROFILE_NONE,
        fsx.VFS_POLICY_GENERATION,
        1,
        owner_domain,
        owner_version,
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
    vfs_format = magic in fsx.VFS_POLICY_MAGICS
    quota_format = magic in fsx.QUOTA_MAGICS

    # superblock
    put_u32(image, fsx.BSIZE, magic)
    put_u32(image, fsx.BSIZE + 4, 1000)
    put_u32(image, fsx.BSIZE + 12, 64)
    put_u32(image, fsx.BSIZE + 16, 2)
    if quota_format:
        inode_blocks = (64 + fsx.BSIZE // dinode_size - 1) // (
            fsx.BSIZE // dinode_size
        )
        bmapstart = 2 + inode_blocks
        bitmap_blocks = (1000 + fsx.BSIZE * 8 - 1) // (fsx.BSIZE * 8)
        qmapstart = bmapstart + bitmap_blocks
        owner_blocks = (1000 + fsx.QPB - 1) // fsx.QPB
        datastart = qmapstart + owner_blocks
        put_u32(image, fsx.BSIZE + 8, 1000 - datastart)
        put_u32(image, fsx.BSIZE + 20, bmapstart)
        put_u32(image, fsx.BSIZE + 24, qmapstart)
        put_u32(image, fsx.BSIZE + 28, datastart)
    else:
        put_u32(image, fsx.BSIZE + 8, 980)
        put_u32(image, fsx.BSIZE + 20, 15)

    dir_block = bytearray(fsx.BSIZE)
    entries = [
        (2, "rp_input"),
        (3, "rp_artifact_ma"),
        (4, "rp_orch"),
        (5, "rp_agentos_col"),
    ]
    if vfs_format:
        if public_first:
            entries.insert(0, (6, "rp_input"))
        else:
            entries.append((6, "rp_input"))
    for slot, (inum, name) in enumerate(entries):
        put_dirent(dir_block, slot, inum, name)
    image[20 * fsx.BSIZE : 21 * fsx.BSIZE] = dir_block
    put_inode(image, 1, 1, fsx.BSIZE, [20], dinode_size, magic)
    if vfs_format:
        put_vfs_label(
            image, 1, 1, fsx.VFS_POLICY_ROOT, dinode_size, magic
        )

    input_text = b"status=ready\ncustom_run=usable-run:RUN-900\n"
    image[21 * fsx.BSIZE : 21 * fsx.BSIZE + len(input_text)] = input_text
    put_inode(
        image, 2, fsx.T_FILE, len(input_text), [21], dinode_size, magic
    )
    if vfs_format:
        put_vfs_label(
            image,
            2,
            fsx.T_FILE,
            fsx.VFS_POLICY_WORKFLOW,
            dinode_size,
            magic,
        )

    manifest_text = b"manifest_records=4\nstatus=ready\n"
    image[22 * fsx.BSIZE : 22 * fsx.BSIZE + len(manifest_text)] = manifest_text
    put_inode(
        image, 3, fsx.T_FILE, len(manifest_text), [22], dinode_size, magic
    )
    if vfs_format:
        put_vfs_label(
            image,
            3,
            fsx.T_FILE,
            fsx.VFS_POLICY_WORKFLOW,
            dinode_size,
            magic,
        )

    binary_text = b"\x7fELF\x00rp_fake_program"
    image[23 * fsx.BSIZE : 23 * fsx.BSIZE + len(binary_text)] = binary_text
    put_inode(
        image, 4, fsx.T_FILE, len(binary_text), [23], dinode_size, magic
    )
    if vfs_format:
        put_vfs_label(
            image,
            4,
            fsx.T_FILE,
            fsx.VFS_POLICY_WORKFLOW,
            dinode_size,
            magic,
        )

    ack_text = b"delivery=kernel_event_queue\nstatus=ready\n"
    image[24 * fsx.BSIZE : 24 * fsx.BSIZE + len(ack_text)] = ack_text
    put_inode(
        image, 5, fsx.T_FILE, len(ack_text), [24], dinode_size, magic
    )
    if vfs_format:
        put_vfs_label(
            image,
            5,
            fsx.T_FILE,
            fsx.VFS_POLICY_WORKFLOW,
            dinode_size,
            magic,
        )

        public_text = b"status=public\n"
        image[25 * fsx.BSIZE : 25 * fsx.BSIZE + len(public_text)] = public_text
        put_inode(
            image,
            6,
            fsx.T_FILE,
            len(public_text),
            [25],
            dinode_size,
            magic,
        )
        put_vfs_label(
            image,
            6,
            fsx.T_FILE,
            fsx.VFS_POLICY_PUBLIC,
            dinode_size,
            magic,
        )

        if magic == fsx.FSMAGIC_AGENT_QUOTA:
            put_inode(image, 7, 0, 0, [], dinode_size, magic)
            put_vfs_label(
                image, 7, 0, fsx.VFS_POLICY_FREE, dinode_size, magic
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

    superblock = fsx.read_superblock(image)
    if quota_format:
        assert superblock.qmapstart == qmapstart
        assert superblock.datastart == datastart
        root_inode = fsx.read_inode(image, superblock, fsx.ROOTINO)
        assert root_inode.fs_owner_domain == 1
        assert root_inode.fs_owner_version == fsx.FS_OWNER_VERSION
    else:
        assert superblock.qmapstart is None
        assert superblock.datastart is None

    summary = fsx.extract_state_files(image_path, out_dir, repo_dir)
    assert summary["extracted_state_files"] == 3, summary
    assert (out_dir / "rp_input").read_text(encoding="utf-8") == input_text.decode("utf-8")
    assert (out_dir / "rp_artifact_manifest").read_text(encoding="utf-8") == manifest_text.decode("utf-8")
    assert (out_dir / "rp_agentos_collab_ack").read_text(encoding="utf-8") == ack_text.decode("utf-8")
    assert not (out_dir / "rp_orch").exists()
    assert (out_dir / "extract-summary.json").exists()

    if quota_format:
        bad_geometry = bytearray(image)
        put_u32(bad_geometry, fsx.BSIZE + 24, bmapstart)
        try:
            fsx.read_superblock(bad_geometry)
        except ValueError as error:
            assert "quota filesystem geometry" in str(error)
        else:
            raise AssertionError("overlapping quota map accepted")

    if magic == fsx.FSMAGIC_BASELINE_QUOTA:
        ownerless = bytearray(image)
        ipb = fsx.BSIZE // dinode_size
        root_offset = (
            (fsx.ROOTINO // ipb + 2) * fsx.BSIZE
            + (fsx.ROOTINO % ipb) * dinode_size
        )
        put_u16(ownerless, root_offset + 2, 0)
        put_u32(ownerless, root_offset + 4, 0)
        try:
            fsx.read_inode(ownerless, superblock, fsx.ROOTINO)
        except ValueError as error:
            assert "filesystem owner" in str(error)
        else:
            raise AssertionError("baseline ownerless inode accepted")

    if vfs_format:
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
            magic,
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

        if magic == fsx.FSMAGIC_AGENT_QUOTA:
            allocated_without_owner = bytearray(image)
            words = [
                fsx.u32(allocated_without_owner, inode_offset + 84 + i * 4)
                for i in range(10)
            ]
            words[8] = 0
            words[9] = 0
            for index, value in enumerate(words):
                put_u32(
                    allocated_without_owner,
                    inode_offset + 84 + index * 4,
                    value,
                )
            put_u32(
                allocated_without_owner,
                inode_offset + 124,
                fsx.vfs_label_checksum(2, words),
            )
            owner_path = case_dir / "ownerless.img"
            owner_path.write_bytes(allocated_without_owner)
            try:
                fsx.extract_state_files(
                    owner_path, case_dir / "ownerless-state", repo_dir
                )
            except ValueError as error:
                assert "VFS label" in str(error)
            else:
                raise AssertionError("allocated ownerless inode accepted")

            bad_owner_version = bytearray(image)
            words = [
                fsx.u32(bad_owner_version, inode_offset + 84 + i * 4)
                for i in range(10)
            ]
            words[9] = fsx.FS_OWNER_VERSION + 1
            for index, value in enumerate(words):
                put_u32(
                    bad_owner_version,
                    inode_offset + 84 + index * 4,
                    value,
                )
            put_u32(
                bad_owner_version,
                inode_offset + 124,
                fsx.vfs_label_checksum(2, words),
            )
            owner_version_path = case_dir / "bad-owner-version.img"
            owner_version_path.write_bytes(bad_owner_version)
            try:
                fsx.extract_state_files(
                    owner_version_path,
                    case_dir / "bad-owner-version-state",
                    repo_dir,
                )
            except ValueError as error:
                assert "VFS label" in str(error)
            else:
                raise AssertionError("invalid inode owner version accepted")

            legacy_label = bytearray(image)
            words = [
                fsx.u32(legacy_label, inode_offset + 84 + i * 4)
                for i in range(10)
            ]
            words[1] = fsx.VFS_LABEL_VERSION
            put_u32(legacy_label, inode_offset + 88, words[1])
            put_u32(
                legacy_label,
                inode_offset + 124,
                fsx.vfs_label_checksum(2, words),
            )
            legacy_label_path = case_dir / "legacy-label.img"
            legacy_label_path.write_bytes(legacy_label)
            try:
                fsx.extract_state_files(
                    legacy_label_path,
                    case_dir / "legacy-label-state",
                    repo_dir,
                )
            except ValueError as error:
                assert "VFS label" in str(error)
            else:
                raise AssertionError("legacy VFS label accepted by quota format")

            free_inode = fsx.read_inode(image, superblock, 7)
            assert free_inode.vfs_policy == fsx.VFS_POLICY_FREE


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
        run_case(
            root,
            fsx.FSMAGIC_BASELINE_QUOTA,
            fsx.DINODE_SIZE_LEGACY,
            "baseline-quota",
        )
        run_case(
            root,
            fsx.FSMAGIC_AGENT_QUOTA,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "agent-quota-workflow-first",
        )
        run_case(
            root,
            fsx.FSMAGIC_AGENT_QUOTA,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "agent-quota-public-first",
            public_first=True,
        )

    print("test_plain_ucore_fs_extract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
