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


def is_scoped(magic: int) -> bool:
    return magic in fsx.SCOPED_MAGICS


def first_dynamic_scope(magic: int) -> int:
    return (
        fsx.VFS_SCOPE_FIRST_DYNAMIC
        if magic == fsx.FSMAGIC_AGENT_PRINCIPAL
        else fsx.VFS_SCOPE_FIRST_DYNAMIC_LEGACY
    )


def expected_owner_version(magic: int) -> int:
    if magic == fsx.FSMAGIC_AGENT_PRINCIPAL:
        return fsx.FS_OWNER_VERSION
    if magic in {
        fsx.FSMAGIC_SCOPED_WORKFLOW,
        fsx.FSMAGIC_BASELINE_PRINCIPAL,
    }:
        return fsx.FS_OWNER_VERSION_SCOPED_LEGACY
    return fsx.FS_OWNER_VERSION_LEGACY


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
    if fs_magic in fsx.BASELINE_QUOTA_MAGICS:
        owner_domain = 0 if file_type == 0 else 1
        owner_version = 0 if file_type == 0 else expected_owner_version(fs_magic)
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
    scope_id: int | None = None,
) -> None:
    ipb = fsx.BSIZE // dinode_size
    offset = (inum // ipb + 2) * fsx.BSIZE + (inum % ipb) * dinode_size
    scoped_format = is_scoped(fs_magic)
    if policy == fsx.VFS_POLICY_ROOT:
        flags = fsx.VFS_LABEL_F_ROOT
        domain = fsx.VFS_SCOPE_NONE if scoped_format else fsx.VFS_DOMAIN_PUBLIC
    elif policy == fsx.VFS_POLICY_WORKFLOW:
        flags = fsx.VFS_LABEL_F_PROTECTED
        if scoped_format:
            domain = (
                scope_id
                if scope_id is not None
                else first_dynamic_scope(fs_magic)
            )
        else:
            domain = fsx.VFS_DOMAIN_WORKFLOW
    elif policy == fsx.VFS_POLICY_PUBLIC:
        flags = fsx.VFS_LABEL_F_PUBLIC
        domain = fsx.VFS_SCOPE_NONE if scoped_format else fsx.VFS_DOMAIN_PUBLIC
    elif policy == fsx.VFS_POLICY_FREE:
        flags = fsx.VFS_LABEL_F_FREE
        domain = fsx.VFS_SCOPE_NONE if scoped_format else fsx.VFS_DOMAIN_PUBLIC
    else:
        raise AssertionError(f"unsupported test policy {policy}")
    quota_format = fs_magic in fsx.AGENT_QUOTA_MAGICS
    if file_type == 0 or not quota_format:
        owner_domain = fsx.FS_OWNER_NONE
        owner_version = 0
    elif scoped_format and policy == fsx.VFS_POLICY_WORKFLOW:
        if domain == fsx.VFS_SCOPE_SYSTEM:
            owner_domain = fsx.FS_OWNER_SYSTEM
        else:
            owner_domain = fsx.FS_OWNER_SCOPE_FLAG | domain
        owner_version = expected_owner_version(fs_magic)
    else:
        owner_domain = fsx.FS_OWNER_SYSTEM
        owner_version = expected_owner_version(fs_magic)
    if scoped_format:
        label_version = fsx.VFS_LABEL_VERSION
        policy_generation = fsx.VFS_POLICY_GENERATION
    elif quota_format:
        label_version = fsx.VFS_LABEL_VERSION_QUOTA
        policy_generation = fsx.VFS_POLICY_GENERATION_LEGACY
    else:
        label_version = fsx.VFS_LABEL_VERSION_LEGACY
        policy_generation = fsx.VFS_POLICY_GENERATION_LEGACY
    exec_profile = fsx.VFS_EXEC_PROFILE_NONE
    if (
        scoped_format
        and policy == fsx.VFS_POLICY_WORKFLOW
        and domain == fsx.VFS_SCOPE_SYSTEM
    ):
        put_u32(
            image,
            offset + 64,
            fsx.EXEC_FLAG_IMMUTABLE | fsx.EXEC_FLAG_DOMAIN_SAFE,
        )
        put_u32(image, offset + 68, fsx.EXEC_MANIFEST_VERSION)
        put_u32(image, offset + 76, fsx.EXEC_LAYOUT_VERSION)
        put_u32(image, offset + 80, fsx.USER_PAGE_SIZE)
        exec_profile = fsx.VFS_EXEC_PROFILE_WORKFLOW
    words = [
        fsx.VFS_LABEL_MAGIC,
        label_version,
        flags,
        domain,
        policy,
        exec_profile,
        policy_generation,
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


def replace_vfs_label_word(
    image: bytearray,
    inode_offset: int,
    inum: int,
    word_index: int,
    value: int,
) -> None:
    words = [
        fsx.u32(image, inode_offset + 84 + index * 4) for index in range(10)
    ]
    words[word_index] = value
    put_u32(image, inode_offset + 84 + word_index * 4, value)
    put_u32(
        image,
        inode_offset + 124,
        fsx.vfs_label_checksum(inum, words),
    )


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
        nblocks = 1000 - datastart
        put_u32(image, fsx.BSIZE + 8, nblocks)
        put_u32(image, fsx.BSIZE + 20, bmapstart)
        put_u32(image, fsx.BSIZE + 24, qmapstart)
        put_u32(image, fsx.BSIZE + 28, datastart)
        if magic == fsx.FSMAGIC_BASELINE_PRINCIPAL:
            put_u32(
                image,
                fsx.BSIZE + 32,
                fsx.FS_PUBLIC_PRINCIPAL_ID,
            )
        if is_scoped(magic):
            workflow_blocks = 8
            workflow_inodes = 8
            system_blocks = 4
            system_inodes = 4
            current_agent = magic == fsx.FSMAGIC_AGENT_PRINCIPAL
            policy_version = (
                fsx.FS_STORAGE_POLICY_VERSION
                if current_agent
                else fsx.FS_STORAGE_POLICY_VERSION_LEGACY
            )
            if current_agent:
                policy_checksum = fsx.storage_policy_checksum(
                    policy_version,
                    fsx.FS_WORKFLOW_SCOPE_SLOTS,
                    fsx.FS_PUBLIC_PRINCIPAL_ID,
                    workflow_blocks,
                    workflow_inodes,
                    system_blocks,
                    system_inodes,
                )
            else:
                policy_checksum = fsx.legacy_storage_policy_checksum(
                    policy_version,
                    fsx.FS_WORKFLOW_SCOPE_SLOTS,
                    workflow_blocks,
                    workflow_inodes,
                    system_blocks,
                    system_inodes,
                )
            put_u32(image, fsx.BSIZE + 32, policy_version)
            put_u32(image, fsx.BSIZE + 36, fsx.FS_WORKFLOW_SCOPE_SLOTS)
            put_u32(image, fsx.BSIZE + 40, workflow_blocks)
            put_u32(image, fsx.BSIZE + 44, workflow_inodes)
            put_u32(image, fsx.BSIZE + 48, system_blocks)
            put_u32(image, fsx.BSIZE + 52, system_inodes)
            if current_agent:
                put_u32(
                    image,
                    fsx.BSIZE + 56,
                    fsx.FS_PUBLIC_PRINCIPAL_ID,
                )
                put_u32(image, fsx.BSIZE + 60, policy_checksum)
            else:
                put_u32(image, fsx.BSIZE + 56, policy_checksum)
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

    binary_text = b"\x7fELF\x00rp_fake_program" + bytes(fsx.USER_PAGE_SIZE)
    binary_blocks = [30, 31, 32, 33, 34]
    for index, block_number in enumerate(binary_blocks):
        chunk = binary_text[index * fsx.BSIZE : (index + 1) * fsx.BSIZE]
        image[
            block_number * fsx.BSIZE : block_number * fsx.BSIZE + len(chunk)
        ] = chunk
    put_inode(
        image, 4, fsx.T_FILE, len(binary_text), binary_blocks, dinode_size, magic
    )
    if vfs_format:
        put_vfs_label(
            image,
            4,
            fsx.T_FILE,
            fsx.VFS_POLICY_WORKFLOW,
            dinode_size,
            magic,
            (
                fsx.VFS_SCOPE_SYSTEM
                if is_scoped(magic)
                else None
            ),
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

        if magic in fsx.AGENT_QUOTA_MAGICS:
            put_inode(image, 7, 0, 0, [], dinode_size, magic)
            put_vfs_label(
                image, 7, 0, fsx.VFS_POLICY_FREE, dinode_size, magic
            )

    case_dir = root / tag
    image_path = case_dir / "fs-copy.img"
    out_dir = case_dir / "state"
    repo_dir = case_dir / "repo"
    source_dir = repo_dir / "user" / "src"
    include_dir = repo_dir / "user" / "include"
    source_dir.mkdir(parents=True)
    include_dir.mkdir(parents=True)
    (source_dir / "state_fixture.c").write_text(
        'rp_write_file("rp_input", "status=ready\\n");\n'
        'rp_write_file("rp_artifact_manifest", "status=ready\\n");\n'
        'rp_write_file("rp_agentos_collab_ack", "status=ready\\n");\n',
        encoding="utf-8",
    )
    (include_dir / "state_fixture.h").write_text("/* state fixture */\n", encoding="utf-8")
    image_path.write_bytes(image)

    superblock = fsx.read_superblock(image)
    if quota_format:
        assert superblock.qmapstart == qmapstart
        assert superblock.datastart == datastart
        root_inode = fsx.read_inode(image, superblock, fsx.ROOTINO)
        assert root_inode.fs_owner_domain == fsx.FS_OWNER_SYSTEM
        assert root_inode.fs_owner_version == expected_owner_version(magic)
        if magic == fsx.FSMAGIC_BASELINE_PRINCIPAL:
            assert (
                superblock.public_principal_id
                == fsx.FS_PUBLIC_PRINCIPAL_ID
            )
        if is_scoped(magic):
            assert superblock.storage_policy_version == (
                fsx.FS_STORAGE_POLICY_VERSION
                if magic == fsx.FSMAGIC_AGENT_PRINCIPAL
                else fsx.FS_STORAGE_POLICY_VERSION_LEGACY
            )
            assert superblock.storage_scope_slots == 4
            assert superblock.workflow_block_guarantee == 8
            assert superblock.workflow_inode_guarantee == 8
            assert superblock.system_block_reserve == 4
            assert superblock.system_inode_reserve == 4
            workflow_inode = fsx.read_inode(image, superblock, 2)
            dynamic_scope = first_dynamic_scope(magic)
            assert workflow_inode.vfs_scope_id == dynamic_scope
            assert workflow_inode.fs_owner_domain == (
                fsx.FS_OWNER_SCOPE_FLAG | dynamic_scope
            )
            assert fsx.fs_owner_is_scope(workflow_inode.fs_owner_domain)
            assert (
                fsx.fs_owner_scope_id(workflow_inode.fs_owner_domain)
                == workflow_inode.vfs_scope_id
            )
            assert superblock.public_principal_id == (
                fsx.FS_PUBLIC_PRINCIPAL_ID
                if magic == fsx.FSMAGIC_AGENT_PRINCIPAL
                else None
            )
            if magic == fsx.FSMAGIC_AGENT_PRINCIPAL:
                public_inode = fsx.read_inode(image, superblock, 6)
                assert public_inode.fs_owner_domain == fsx.FS_OWNER_SYSTEM
                public_offset = (
                    (6 // (fsx.BSIZE // dinode_size) + 2) * fsx.BSIZE
                    + (6 % (fsx.BSIZE // dinode_size)) * dinode_size
                )
                public_owned = bytearray(image)
                replace_vfs_label_word(
                    public_owned,
                    public_offset,
                    6,
                    8,
                    fsx.FS_OWNER_PUBLIC,
                )
                assert (
                    fsx.read_inode(public_owned, superblock, 6)
                    .fs_owner_domain
                    == fsx.FS_OWNER_PUBLIC
                )
                invalid_public_owner = bytearray(image)
                replace_vfs_label_word(
                    invalid_public_owner,
                    public_offset,
                    6,
                    8,
                    fsx.FS_OWNER_PUBLIC + 1,
                )
                try:
                    fsx.read_inode(invalid_public_owner, superblock, 6)
                except ValueError as error:
                    assert "VFS policy shape" in str(error)
                else:
                    raise AssertionError("untrusted PUBLIC sponsor accepted")
    else:
        assert superblock.qmapstart is None
        assert superblock.datastart is None

    summary = fsx.extract_state_files(
        image_path, out_dir, repo_dir, require_single_scope=True
    )
    assert summary["extracted_state_files"] == 3, summary
    assert (out_dir / "rp_input").read_text(encoding="utf-8") == input_text.decode("utf-8")
    assert (out_dir / "rp_artifact_manifest").read_text(encoding="utf-8") == manifest_text.decode("utf-8")
    assert (out_dir / "rp_agentos_collab_ack").read_text(encoding="utf-8") == ack_text.decode("utf-8")
    assert not (out_dir / "rp_orch").exists()
    assert (out_dir / "extract-summary.json").exists()

    unknown = bytearray(image)
    artifact_slot = 2 if vfs_format and public_first else 1
    unknown_offset = 20 * fsx.BSIZE + artifact_slot * 16 + 2
    unknown[unknown_offset : unknown_offset + fsx.DIRSIZ] = b"\0" * fsx.DIRSIZ
    unknown_name = b"rp_unknown"
    unknown[unknown_offset : unknown_offset + len(unknown_name)] = unknown_name
    unknown_path = case_dir / "unknown-state.img"
    unknown_path.write_bytes(unknown)
    try:
        fsx.extract_state_files(
            unknown_path,
            case_dir / "unknown-state",
            repo_dir,
            require_single_scope=True,
        )
    except ValueError as error:
        assert "unmanifested state filename" in str(error), error
    else:
        raise AssertionError("unmanifested guest state file was extracted")

    if is_scoped(magic):
        stale_file = out_dir / "rp_stale"
        stale_scope = out_dir / "scope-999"
        unrelated_file = out_dir / "keep.txt"
        stale_file.write_text("stale\n", encoding="utf-8")
        stale_scope.mkdir()
        (stale_scope / "rp_stale").write_text("stale\n", encoding="utf-8")
        unrelated_file.write_text("keep\n", encoding="utf-8")
        fsx.extract_state_files(
            image_path, out_dir, repo_dir, require_single_scope=True
        )
        assert not stale_file.exists()
        assert not stale_scope.exists()
        assert unrelated_file.read_text(encoding="utf-8") == "keep\n"
        assert (out_dir / "rp_input").read_bytes() == input_text

        traversal = bytearray(image)
        malicious_name = b"rp_/../../../x"
        assert len(malicious_name) == fsx.DIRSIZ
        workflow_slot = 1 if public_first else 0
        name_offset = 20 * fsx.BSIZE + workflow_slot * 16 + 2
        traversal[name_offset : name_offset + fsx.DIRSIZ] = malicious_name
        traversal_path = case_dir / "traversal.img"
        traversal_out = case_dir / "traversal-state"
        escape_target = root / "x"
        escape_target.write_text("sentinel\n", encoding="utf-8")
        traversal_path.write_bytes(traversal)
        try:
            fsx.extract_state_files(
                traversal_path,
                traversal_out,
                repo_dir,
                require_single_scope=True,
            )
        except ValueError as error:
            assert "unsafe state filename" in str(error)
        else:
            raise AssertionError("guest path traversal name accepted")
        assert escape_target.read_text(encoding="utf-8") == "sentinel\n"
        escape_target.unlink()

    if quota_format:
        bad_geometry = bytearray(image)
        put_u32(bad_geometry, fsx.BSIZE + 24, bmapstart)
        try:
            fsx.read_superblock(bad_geometry)
        except ValueError as error:
            assert "quota filesystem geometry" in str(error)
        else:
            raise AssertionError("overlapping quota map accepted")

    if is_scoped(magic):
        bad_contract = bytearray(image)
        put_u32(
            bad_contract,
            fsx.BSIZE
            + (60 if magic == fsx.FSMAGIC_AGENT_PRINCIPAL else 56),
            superblock.storage_policy_checksum ^ 1,
        )
        try:
            fsx.read_superblock(bad_contract)
        except ValueError as error:
            assert "storage policy contract" in str(error)
        else:
            raise AssertionError("corrupt scoped storage contract accepted")

        if magic == fsx.FSMAGIC_AGENT_PRINCIPAL:
            bad_principal = bytearray(image)
            wrong_principal = fsx.FS_PUBLIC_PRINCIPAL_ID + 1
            put_u32(
                bad_principal,
                fsx.BSIZE + 56,
                wrong_principal,
            )
            put_u32(
                bad_principal,
                fsx.BSIZE + 60,
                fsx.storage_policy_checksum(
                    superblock.storage_policy_version,
                    superblock.storage_scope_slots,
                    wrong_principal,
                    superblock.workflow_block_guarantee,
                    superblock.workflow_inode_guarantee,
                    superblock.system_block_reserve,
                    superblock.system_inode_reserve,
                ),
            )
            try:
                fsx.read_superblock(bad_principal)
            except ValueError as error:
                assert "storage policy contract" in str(error)
            else:
                raise AssertionError("wrong AgentOS public principal accepted")

    if magic in fsx.BASELINE_QUOTA_MAGICS:
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

        if magic == fsx.FSMAGIC_BASELINE_PRINCIPAL:
            bad_principal = bytearray(image)
            put_u32(
                bad_principal,
                fsx.BSIZE + 32,
                fsx.FS_PUBLIC_PRINCIPAL_ID + 1,
            )
            try:
                fsx.read_superblock(bad_principal)
            except ValueError as error:
                assert "storage principal contract" in str(error)
            else:
                raise AssertionError("wrong baseline public principal accepted")

            bad_owner = bytearray(image)
            put_u32(bad_owner, root_offset + 4, fsx.FS_OWNER_PUBLIC + 1)
            try:
                fsx.read_inode(bad_owner, superblock, fsx.ROOTINO)
            except ValueError as error:
                assert "filesystem owner" in str(error)
            else:
                raise AssertionError("unknown baseline principal accepted")

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

        # The scoped format permits the same physical name in independent
        # workflow namespaces and must not overwrite either extracted object.
        if is_scoped(magic):
            dynamic_scope = first_dynamic_scope(magic)
            cross_scope = bytearray(image)
            put_vfs_label(
                cross_scope,
                6,
                fsx.T_FILE,
                fsx.VFS_POLICY_WORKFLOW,
                dinode_size,
                magic,
                dynamic_scope + 1,
            )
            cross_scope_path = case_dir / "cross-scope.img"
            cross_scope_out = case_dir / "cross-scope-state"
            cross_scope_path.write_bytes(cross_scope)
            cross_scope_summary = fsx.extract_state_files(
                cross_scope_path, cross_scope_out, repo_dir
            )
            assert cross_scope_summary["extracted_state_files"] == 4
            assert cross_scope_summary["available_scope_ids"] == [
                dynamic_scope,
                dynamic_scope + 1,
            ]
            assert cross_scope_summary["selected_scope_id"] is None
            assert cross_scope_summary["scope_layout"] == "partitioned"
            assert (
                cross_scope_out
                / f"scope-{dynamic_scope}"
                / "rp_input"
            ).read_bytes() == input_text
            assert (
                cross_scope_out
                / f"scope-{dynamic_scope + 1}"
                / "rp_input"
            ).read_bytes() == public_text
            assert (
                cross_scope_out
                / f"scope-{dynamic_scope}"
                / "rp_artifact_manifest"
            ).read_bytes() == manifest_text
            assert not (cross_scope_out / "rp_artifact_manifest").exists()

            try:
                fsx.extract_state_files(
                    cross_scope_path,
                    case_dir / "ambiguous-scope-state",
                    repo_dir,
                    require_single_scope=True,
                )
            except ValueError as error:
                assert "exactly one workflow scope" in str(error)
            else:
                raise AssertionError("ambiguous workflow scope was selected")

            selected_out = case_dir / "selected-scope-state"
            selected_summary = fsx.extract_state_files(
                cross_scope_path,
                selected_out,
                repo_dir,
                scope_id=dynamic_scope,
            )
            assert selected_summary["selected_scope_id"] == (
                dynamic_scope
            )
            assert selected_summary["scope_layout"] == "selected"
            assert selected_summary["extracted_state_files"] == 3
            assert (selected_out / "rp_input").read_bytes() == input_text
            assert not (selected_out / f"scope-{dynamic_scope}").exists()

        if magic in fsx.AGENT_QUOTA_MAGICS:
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
            words[9] = (
                fsx.FS_OWNER_VERSION
                if expected_owner_version(magic) != fsx.FS_OWNER_VERSION
                else fsx.FS_OWNER_VERSION_SCOPED_LEGACY
            )
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
            words[1] = (
                fsx.VFS_LABEL_VERSION_LEGACY
                if magic == fsx.FSMAGIC_AGENT_QUOTA
                else fsx.VFS_LABEL_VERSION_QUOTA
            )
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
                raise AssertionError("legacy VFS label accepted by newer format")

            if is_scoped(magic):
                invalid_scoped_labels = (
                    (
                        "untagged-workflow-owner",
                        8,
                        fsx.FS_OWNER_SYSTEM,
                    ),
                    (
                        "mismatched-workflow-owner",
                        8,
                        fsx.FS_OWNER_SCOPE_FLAG
                        | (first_dynamic_scope(magic) + 1),
                    ),
                    (
                        "legacy-policy-generation",
                        6,
                        fsx.VFS_POLICY_GENERATION_LEGACY,
                    ),
                )
                for label, word_index, value in invalid_scoped_labels:
                    invalid_label = bytearray(image)
                    replace_vfs_label_word(
                        invalid_label,
                        inode_offset,
                        2,
                        word_index,
                        value,
                    )
                    invalid_path = case_dir / f"{label}.img"
                    invalid_path.write_bytes(invalid_label)
                    try:
                        fsx.extract_state_files(
                            invalid_path,
                            case_dir / f"{label}-state",
                            repo_dir,
                        )
                    except ValueError as error:
                        assert "VFS" in str(error)
                    else:
                        raise AssertionError(f"{label} accepted")

                if magic == fsx.FSMAGIC_AGENT_PRINCIPAL:
                    retired_scope = bytearray(image)
                    replace_vfs_label_word(
                        retired_scope,
                        inode_offset,
                        2,
                        3,
                        fsx.VFS_SCOPE_FIRST_DYNAMIC_LEGACY,
                    )
                    replace_vfs_label_word(
                        retired_scope,
                        inode_offset,
                        2,
                        8,
                        fsx.FS_OWNER_SCOPE_FLAG
                        | fsx.VFS_SCOPE_FIRST_DYNAMIC_LEGACY,
                    )
                    retired_scope_path = case_dir / "retired-scope.img"
                    retired_scope_path.write_bytes(retired_scope)
                    try:
                        fsx.extract_state_files(
                            retired_scope_path,
                            case_dir / "retired-scope-state",
                            repo_dir,
                        )
                    except ValueError as error:
                        assert "VFS" in str(error)
                    else:
                        raise AssertionError("retired workflow scope accepted")

                    public_ipb = fsx.BSIZE // dinode_size
                    public_offset = (
                        (6 // public_ipb + 2) * fsx.BSIZE
                        + (6 % public_ipb) * dinode_size
                    )
                    wrong_public_owner = bytearray(image)
                    replace_vfs_label_word(
                        wrong_public_owner,
                        public_offset,
                        6,
                        8,
                        fsx.FS_OWNER_PUBLIC + 1,
                    )
                    wrong_public_path = case_dir / "wrong-public-owner.img"
                    wrong_public_path.write_bytes(wrong_public_owner)
                    try:
                        fsx.extract_state_files(
                            wrong_public_path,
                            case_dir / "wrong-public-owner-state",
                            repo_dir,
                        )
                    except ValueError as error:
                        assert "VFS" in str(error)
                    else:
                        raise AssertionError("untrusted PUBLIC sponsor accepted")

            free_inode = fsx.read_inode(image, superblock, 7)
            assert free_inode.vfs_policy == fsx.VFS_POLICY_FREE


def main() -> int:
    assert len(fsx.DINODE_SIZE_BY_MAGIC) == 8
    assert (
        fsx.FSMAGIC_BASELINE_PRINCIPAL
        != fsx.FSMAGIC_AGENT_PRINCIPAL
    )
    sealed = (
        fsx.EXEC_FLAG_TRUSTED
        | fsx.EXEC_FLAG_IMMUTABLE
        | fsx.EXEC_FLAG_DOMAIN_SAFE
    )
    worker = fsx.EXEC_FLAG_IMMUTABLE | fsx.EXEC_FLAG_DOMAIN_SAFE

    def classify(flags: int, profile: int, roles: int = 0) -> int:
        return fsx.exec_image_protected_classify(
            fsx.T_FILE,
            fsx.USER_PAGE_SIZE + 1,
            flags,
            fsx.EXEC_MANIFEST_VERSION,
            roles,
            fsx.EXEC_LAYOUT_VERSION,
            fsx.USER_PAGE_SIZE,
            profile,
        )

    assert classify(sealed, fsx.VFS_EXEC_PROFILE_NONE) == fsx.EXEC_IMAGE_COMPAT
    assert (
        classify(sealed, fsx.VFS_EXEC_PROFILE_NONE, fsx.EXEC_MANIFEST_ROLE_ALL)
        == fsx.EXEC_IMAGE_COMPAT
    )
    assert (
        classify(worker, fsx.VFS_EXEC_PROFILE_WORKFLOW)
        == fsx.EXEC_IMAGE_WORKER
    )
    assert classify(
        sealed | fsx.EXEC_FLAG_BOOTSTRAP,
        fsx.VFS_EXEC_PROFILE_WORKFLOW,
        1 << 4,
    ) == fsx.EXEC_IMAGE_TRUSTED_AGENT
    assert (
        classify(sealed, fsx.VFS_EXEC_PROFILE_CONTENT_READ)
        == fsx.EXEC_IMAGE_TRUSTED_ENDPOINT
    )
    assert (
        classify(fsx.EXEC_FLAG_DOMAIN_SAFE, fsx.VFS_EXEC_PROFILE_WORKFLOW)
        == fsx.EXEC_IMAGE_INVALID
    )
    assert classify(
        sealed | fsx.EXEC_FLAG_BOOTSTRAP,
        fsx.VFS_EXEC_PROFILE_NONE,
        1 << 4,
    ) == fsx.EXEC_IMAGE_INVALID
    assert (
        classify(worker, fsx.VFS_EXEC_PROFILE_WORKFLOW, 1 << 4)
        == fsx.EXEC_IMAGE_INVALID
    )
    assert (
        classify(worker, fsx.VFS_EXEC_PROFILE_NONE)
        == fsx.EXEC_IMAGE_INVALID
    )
    assert (
        classify(worker, fsx.VFS_EXEC_PROFILE_WORKFLOW, 1 << 31)
        == fsx.EXEC_IMAGE_INVALID
    )
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
            "legacy-baseline-quota",
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
        run_case(
            root,
            fsx.FSMAGIC_SCOPED_WORKFLOW,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "legacy-scoped-workflow-first",
        )
        run_case(
            root,
            fsx.FSMAGIC_SCOPED_WORKFLOW,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "legacy-scoped-public-first",
            public_first=True,
        )
        run_case(
            root,
            fsx.FSMAGIC_BASELINE_PRINCIPAL,
            fsx.DINODE_SIZE_LEGACY,
            "persistent-baseline-principal",
        )
        run_case(
            root,
            fsx.FSMAGIC_AGENT_PRINCIPAL,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "persistent-agent-workflow-first",
        )
        run_case(
            root,
            fsx.FSMAGIC_AGENT_PRINCIPAL,
            fsx.DINODE_SIZE_EXEC_POLICY,
            "persistent-agent-public-first",
            public_first=True,
        )

    print("test_plain_ucore_fs_extract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
