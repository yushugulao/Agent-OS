#!/usr/bin/env python3
"""Extract plain uCore rp_* text state files from an xv6-style fs image."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


BSIZE = 1024
FSMAGIC_LEGACY = 0x10203040
FSMAGIC_EXEC_POLICY = 0x10203041
FSMAGIC_VFS_POLICY = 0x10203042
FSMAGIC_BASELINE_QUOTA = 0x10203043
FSMAGIC_AGENT_QUOTA = 0x10203044
FSMAGIC_SCOPED_WORKFLOW = 0x10203045
FSMAGIC_BASELINE_PRINCIPAL = 0x10203046
FSMAGIC_AGENT_PRINCIPAL = 0x10203047
FS_STORAGE_POLICY_VERSION_LEGACY = 1
FS_STORAGE_POLICY_VERSION = 2
FS_WORKFLOW_SCOPE_SLOTS = 4
FS_PUBLIC_PRINCIPAL_ID = 2
ROOTINO = 1
NDIRECT = 12
NINDIRECT = BSIZE // 4
QPB = BSIZE // 4
DINODE_SIZE_LEGACY = 64
DINODE_SIZE_EXEC_POLICY = 128
DINODE_SIZE_BY_MAGIC = {
    FSMAGIC_LEGACY: DINODE_SIZE_LEGACY,
    FSMAGIC_EXEC_POLICY: DINODE_SIZE_EXEC_POLICY,
    FSMAGIC_VFS_POLICY: DINODE_SIZE_EXEC_POLICY,
    FSMAGIC_BASELINE_QUOTA: DINODE_SIZE_LEGACY,
    FSMAGIC_AGENT_QUOTA: DINODE_SIZE_EXEC_POLICY,
    FSMAGIC_SCOPED_WORKFLOW: DINODE_SIZE_EXEC_POLICY,
    FSMAGIC_BASELINE_PRINCIPAL: DINODE_SIZE_LEGACY,
    FSMAGIC_AGENT_PRINCIPAL: DINODE_SIZE_EXEC_POLICY,
}
BASELINE_QUOTA_MAGICS = {
    FSMAGIC_BASELINE_QUOTA,
    FSMAGIC_BASELINE_PRINCIPAL,
}
AGENT_QUOTA_MAGICS = {
    FSMAGIC_AGENT_QUOTA,
    FSMAGIC_SCOPED_WORKFLOW,
    FSMAGIC_AGENT_PRINCIPAL,
}
SCOPED_MAGICS = {
    FSMAGIC_SCOPED_WORKFLOW,
    FSMAGIC_AGENT_PRINCIPAL,
}
QUOTA_MAGICS = {
    *BASELINE_QUOTA_MAGICS,
    *AGENT_QUOTA_MAGICS,
}
VFS_POLICY_MAGICS = {
    FSMAGIC_VFS_POLICY,
    *AGENT_QUOTA_MAGICS,
}
DIRSIZ = 14
T_FILE = 2
STATE_NAME_RE = re.compile(r"rp_[A-Za-z0-9_]+\Z")
SCOPE_DIRECTORY_RE = re.compile(r"scope-[0-9]+\Z")

EXEC_MANIFEST_VERSION = 2
EXEC_LAYOUT_VERSION = 1
EXEC_FLAG_IMMUTABLE = 0x2
EXEC_FLAG_DOMAIN_SAFE = 0x8

VFS_LABEL_MAGIC = 0x56465331
VFS_LABEL_VERSION_LEGACY = 1
VFS_LABEL_VERSION_QUOTA = 2
VFS_LABEL_VERSION = 3
VFS_LABEL_F_PUBLIC = 0x1
VFS_LABEL_F_PROTECTED = 0x2
VFS_LABEL_F_KERNEL_PRIVATE = 0x4
VFS_LABEL_F_ROOT = 0x8
VFS_LABEL_F_FREE = 0x10
VFS_LABEL_F_KNOWN = 0x1F
# Magics through 0x10203044 stored a two-value domain in this word.
VFS_DOMAIN_PUBLIC = 0
VFS_DOMAIN_WORKFLOW = 1
# Scoped formats store a namespace scope identifier in the same word.
VFS_SCOPE_NONE = 0
VFS_SCOPE_SYSTEM = 1
VFS_SCOPE_FIRST_DYNAMIC_LEGACY = 2
VFS_SCOPE_FIRST_DYNAMIC = 3
VFS_POLICY_PUBLIC = 1
VFS_POLICY_WORKFLOW = 2
VFS_POLICY_KERNEL_PRIVATE = 3
VFS_POLICY_ROOT = 4
VFS_POLICY_FREE = 5
VFS_POLICY_GENERATION_LEGACY = 1
VFS_POLICY_GENERATION = 2
VFS_EXEC_PROFILE_NONE = 0
VFS_EXEC_PROFILE_WORKFLOW = 1
VFS_EXEC_PROFILE_CONTENT_READ = 2
VFS_EXEC_PROFILE_ARTIFACT_WRITE = 3
FS_OWNER_VERSION_LEGACY = 1
FS_OWNER_VERSION_SCOPED_LEGACY = 2
FS_OWNER_VERSION = 3
FS_OWNER_NONE = 0
FS_OWNER_SYSTEM = 1
FS_OWNER_PUBLIC = FS_PUBLIC_PRINCIPAL_ID
FS_OWNER_SCOPE_FLAG = 0x80000000
FS_OWNER_ID_MASK = FS_OWNER_SCOPE_FLAG - 1


def fs_owner_is_public_object(owner_domain: int) -> bool:
    return owner_domain in {FS_OWNER_SYSTEM, FS_OWNER_PUBLIC}


@dataclass
class Superblock:
    magic: int
    size: int
    nblocks: int
    ninodes: int
    inodestart: int
    bmapstart: int
    dinode_size: int
    qmapstart: int | None = None
    datastart: int | None = None
    storage_policy_version: int | None = None
    storage_scope_slots: int | None = None
    workflow_block_guarantee: int | None = None
    workflow_inode_guarantee: int | None = None
    system_block_reserve: int | None = None
    system_inode_reserve: int | None = None
    public_principal_id: int | None = None
    storage_policy_checksum: int | None = None

    @property
    def ipb(self) -> int:
        return BSIZE // self.dinode_size


@dataclass
class Dinode:
    type: int
    size: int
    addrs: list[int]
    vfs_policy: int | None = None
    vfs_scope_id: int | None = None
    fs_owner_domain: int | None = None
    fs_owner_version: int | None = None


def u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def storage_policy_checksum(
    version: int,
    scope_slots: int,
    public_principal: int,
    workflow_blocks: int,
    workflow_inodes: int,
    system_blocks: int,
    system_inodes: int,
) -> int:
    value = 2166136261
    for item in (
        version,
        scope_slots,
        public_principal,
        workflow_blocks,
        workflow_inodes,
        system_blocks,
        system_inodes,
    ):
        value = ((value ^ item) * 16777619) & 0xFFFFFFFF
    return value


def legacy_storage_policy_checksum(
    version: int,
    scope_slots: int,
    workflow_blocks: int,
    workflow_inodes: int,
    system_blocks: int,
    system_inodes: int,
) -> int:
    value = 2166136261
    for item in (
        version,
        scope_slots,
        workflow_blocks,
        workflow_inodes,
        system_blocks,
        system_inodes,
    ):
        value = ((value ^ item) * 16777619) & 0xFFFFFFFF
    return value


def u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def vfs_label_checksum(inum: int, words: list[int]) -> int:
    value = (2166136261 ^ inum) & 0xFFFFFFFF
    for word in words:
        value ^= word
        value = (value * 16777619) & 0xFFFFFFFF
        value ^= word >> 16
        value &= 0xFFFFFFFF
    return value or 1


def fs_owner_is_scope(owner_domain: int) -> bool:
    return (owner_domain & FS_OWNER_SCOPE_FLAG) != 0


def fs_owner_scope_id(owner_domain: int) -> int:
    return owner_domain & FS_OWNER_ID_MASK


def fs_owner_valid(
    file_type: int,
    owner_domain: int,
    owner_version: int,
    expected_version: int = FS_OWNER_VERSION,
    first_dynamic: int = VFS_SCOPE_FIRST_DYNAMIC,
) -> bool:
    if file_type == 0:
        return owner_domain == FS_OWNER_NONE and owner_version == 0
    if owner_domain < FS_OWNER_SYSTEM or owner_version != expected_version:
        return False
    if fs_owner_is_scope(owner_domain):
        scope_id = fs_owner_scope_id(owner_domain)
        return first_dynamic <= scope_id < FS_OWNER_SCOPE_FLAG
    return owner_domain < FS_OWNER_SCOPE_FLAG


def validate_vfs_label(
    raw: bytes,
    inum: int,
    file_type: int,
    fs_magic: int = FSMAGIC_VFS_POLICY,
) -> int:
    exec_flags = u32(raw, 64)
    exec_generation = u32(raw, 68)
    words = [u32(raw, 84 + index * 4) for index in range(10)]
    checksum = u32(raw, 124)
    (
        magic,
        version,
        flags,
        domain,
        policy,
        exec_profile,
        generation,
        incarnation,
        fs_owner_domain,
        fs_owner_version,
    ) = words

    quota_format = fs_magic in AGENT_QUOTA_MAGICS
    scoped_format = fs_magic in SCOPED_MAGICS
    current_format = fs_magic == FSMAGIC_AGENT_PRINCIPAL
    if scoped_format:
        expected_version = VFS_LABEL_VERSION
        expected_generation = VFS_POLICY_GENERATION
        expected_owner_version = (
            FS_OWNER_VERSION
            if current_format
            else FS_OWNER_VERSION_SCOPED_LEGACY
        )
    elif quota_format:
        expected_version = VFS_LABEL_VERSION_QUOTA
        expected_generation = VFS_POLICY_GENERATION_LEGACY
        expected_owner_version = FS_OWNER_VERSION_LEGACY
    else:
        expected_version = VFS_LABEL_VERSION_LEGACY
        expected_generation = VFS_POLICY_GENERATION_LEGACY
        expected_owner_version = 0
    owner_valid = fs_owner_valid(
        file_type,
        fs_owner_domain,
        fs_owner_version,
        expected_owner_version,
        (
            VFS_SCOPE_FIRST_DYNAMIC
            if current_format
            else VFS_SCOPE_FIRST_DYNAMIC_LEGACY
        ),
    )

    if (
        magic != VFS_LABEL_MAGIC
        or version != expected_version
        or generation != expected_generation
        or incarnation == 0
        or (quota_format and not owner_valid)
        or (not quota_format and (fs_owner_domain != 0 or fs_owner_version != 0))
        or flags & ~VFS_LABEL_F_KNOWN
        or checksum != vfs_label_checksum(inum, words)
        or exec_profile
        not in (
            VFS_EXEC_PROFILE_NONE,
            VFS_EXEC_PROFILE_WORKFLOW,
            VFS_EXEC_PROFILE_CONTENT_READ,
            VFS_EXEC_PROFILE_ARTIFACT_WRITE,
        )
    ):
        raise ValueError(f"invalid VFS label on inode {inum}")
    if exec_profile != VFS_EXEC_PROFILE_NONE and (
        file_type != T_FILE
        or exec_flags & (EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE)
        != EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE
        or exec_generation != EXEC_MANIFEST_VERSION
    ):
        raise ValueError(f"invalid VFS executable profile on inode {inum}")

    valid_shape = False
    if scoped_format:
        exec_role_mask = u32(raw, 72)
        exec_layout_version = u32(raw, 76)
        if policy == VFS_POLICY_PUBLIC:
            valid_shape = (
                flags == VFS_LABEL_F_PUBLIC
                and domain == VFS_SCOPE_NONE
                and (
                    fs_owner_is_public_object(fs_owner_domain)
                    if current_format
                    else not fs_owner_is_scope(fs_owner_domain)
                )
                and exec_profile == VFS_EXEC_PROFILE_NONE
            )
        elif policy == VFS_POLICY_WORKFLOW:
            if flags == VFS_LABEL_F_PROTECTED and domain == VFS_SCOPE_SYSTEM:
                valid_shape = (
                    file_type == T_FILE
                    and fs_owner_domain == FS_OWNER_SYSTEM
                    and (
                        exec_flags
                        & (EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE)
                    )
                    == (EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE)
                    and exec_generation == EXEC_MANIFEST_VERSION
                    and exec_layout_version == EXEC_LAYOUT_VERSION
                )
            elif (
                flags == VFS_LABEL_F_PROTECTED
                and (
                    VFS_SCOPE_FIRST_DYNAMIC
                    if current_format
                    else VFS_SCOPE_FIRST_DYNAMIC_LEGACY
                )
                <= domain < FS_OWNER_SCOPE_FLAG
            ):
                valid_shape = (
                    fs_owner_is_scope(fs_owner_domain)
                    and fs_owner_scope_id(fs_owner_domain) == domain
                    and exec_profile == VFS_EXEC_PROFILE_NONE
                    and exec_flags == 0
                    and exec_generation == 0
                    and exec_role_mask == 0
                )
        elif policy == VFS_POLICY_KERNEL_PRIVATE:
            valid_shape = (
                flags == VFS_LABEL_F_KERNEL_PRIVATE
                and domain == VFS_SCOPE_NONE
                and fs_owner_domain == FS_OWNER_SYSTEM
                and exec_profile == VFS_EXEC_PROFILE_NONE
            )
        elif policy == VFS_POLICY_ROOT:
            valid_shape = (
                flags == VFS_LABEL_F_ROOT
                and domain == VFS_SCOPE_NONE
                and fs_owner_domain == FS_OWNER_SYSTEM
                and file_type == 1
                and exec_profile == VFS_EXEC_PROFILE_NONE
            )
        elif policy == VFS_POLICY_FREE:
            valid_shape = (
                flags == VFS_LABEL_F_FREE
                and domain == VFS_SCOPE_NONE
                and file_type == 0
                and exec_profile == VFS_EXEC_PROFILE_NONE
            )
    elif policy == VFS_POLICY_PUBLIC:
        valid_shape = (
            flags == VFS_LABEL_F_PUBLIC
            and domain == VFS_DOMAIN_PUBLIC
            and exec_profile == VFS_EXEC_PROFILE_NONE
        )
    elif policy == VFS_POLICY_WORKFLOW:
        valid_shape = (
            flags == VFS_LABEL_F_PROTECTED and domain == VFS_DOMAIN_WORKFLOW
        )
    elif policy == VFS_POLICY_KERNEL_PRIVATE:
        valid_shape = (
            flags == VFS_LABEL_F_KERNEL_PRIVATE
            and domain == VFS_DOMAIN_PUBLIC
            and exec_profile == VFS_EXEC_PROFILE_NONE
        )
    elif policy == VFS_POLICY_ROOT:
        valid_shape = (
            flags == VFS_LABEL_F_ROOT
            and domain == VFS_DOMAIN_PUBLIC
            and file_type == 1
            and exec_profile == VFS_EXEC_PROFILE_NONE
        )
    elif policy == VFS_POLICY_FREE:
        valid_shape = (
            flags == VFS_LABEL_F_FREE
            and domain == VFS_DOMAIN_PUBLIC
            and file_type == 0
            and exec_profile == VFS_EXEC_PROFILE_NONE
        )
    if not valid_shape:
        raise ValueError(f"invalid VFS policy shape on inode {inum}")
    return policy


def read_superblock(image: bytes) -> Superblock:
    if len(image) < 2 * BSIZE:
        raise ValueError("filesystem image is too small")
    offset = BSIZE
    magic = u32(image, offset)
    dinode_size = DINODE_SIZE_BY_MAGIC.get(magic)
    if dinode_size is None:
        raise ValueError(f"bad fs magic: 0x{magic:08x}")
    size = u32(image, offset + 4)
    nblocks = u32(image, offset + 8)
    ninodes = u32(image, offset + 12)
    inodestart = u32(image, offset + 16)
    bmapstart = u32(image, offset + 20)
    qmapstart = u32(image, offset + 24) if magic in QUOTA_MAGICS else None
    datastart = u32(image, offset + 28) if magic in QUOTA_MAGICS else None
    scoped_format = magic in SCOPED_MAGICS
    current_agent_format = magic == FSMAGIC_AGENT_PRINCIPAL
    current_baseline_format = magic == FSMAGIC_BASELINE_PRINCIPAL
    storage_policy_version = u32(image, offset + 32) if scoped_format else None
    storage_scope_slots = u32(image, offset + 36) if scoped_format else None
    workflow_block_guarantee = u32(image, offset + 40) if scoped_format else None
    workflow_inode_guarantee = u32(image, offset + 44) if scoped_format else None
    system_block_reserve = u32(image, offset + 48) if scoped_format else None
    system_inode_reserve = u32(image, offset + 52) if scoped_format else None
    public_principal_id = (
        u32(image, offset + 56)
        if current_agent_format
        else u32(image, offset + 32)
        if current_baseline_format
        else None
    )
    policy_checksum = (
        u32(image, offset + 60)
        if current_agent_format
        else u32(image, offset + 56)
        if scoped_format
        else None
    )
    sb = Superblock(
        magic=magic,
        size=size,
        nblocks=nblocks,
        ninodes=ninodes,
        inodestart=inodestart,
        bmapstart=bmapstart,
        dinode_size=dinode_size,
        qmapstart=qmapstart,
        datastart=datastart,
        storage_policy_version=storage_policy_version,
        storage_scope_slots=storage_scope_slots,
        workflow_block_guarantee=workflow_block_guarantee,
        workflow_inode_guarantee=workflow_inode_guarantee,
        system_block_reserve=system_block_reserve,
        system_inode_reserve=system_inode_reserve,
        public_principal_id=public_principal_id,
        storage_policy_checksum=policy_checksum,
    )
    if size < 1 or len(image) < size * BSIZE:
        raise ValueError("filesystem image is shorter than its superblock size")
    if ninodes <= ROOTINO or inodestart < 2:
        raise ValueError("invalid inode table geometry")
    if magic in QUOTA_MAGICS:
        assert qmapstart is not None and datastart is not None
        inode_blocks = (ninodes + sb.ipb - 1) // sb.ipb
        bitmap_blocks = (size + BSIZE * 8 - 1) // (BSIZE * 8)
        owner_blocks = (size + QPB - 1) // QPB
        inode_end = inodestart + inode_blocks
        if not (
            inodestart == 2
            and bmapstart == inode_end
            and qmapstart == bmapstart + bitmap_blocks
            and datastart == qmapstart + owner_blocks
            and datastart < size
            and nblocks == size - datastart
        ):
            raise ValueError("invalid quota filesystem geometry")
    if scoped_format:
        assert storage_policy_version is not None
        assert storage_scope_slots is not None
        assert workflow_block_guarantee is not None
        assert workflow_inode_guarantee is not None
        assert system_block_reserve is not None
        assert system_inode_reserve is not None
        assert policy_checksum is not None
        total_inodes = ninodes - 1
        if current_agent_format:
            assert public_principal_id is not None
            expected_checksum = storage_policy_checksum(
                storage_policy_version,
                storage_scope_slots,
                public_principal_id,
                workflow_block_guarantee,
                workflow_inode_guarantee,
                system_block_reserve,
                system_inode_reserve,
            )
        else:
            expected_checksum = legacy_storage_policy_checksum(
                storage_policy_version,
                storage_scope_slots,
                workflow_block_guarantee,
                workflow_inode_guarantee,
                system_block_reserve,
                system_inode_reserve,
            )
        valid_contract = (
            storage_policy_version
            == (
                FS_STORAGE_POLICY_VERSION
                if current_agent_format
                else FS_STORAGE_POLICY_VERSION_LEGACY
            )
            and storage_scope_slots == FS_WORKFLOW_SCOPE_SLOTS
            and (
                public_principal_id == FS_PUBLIC_PRINCIPAL_ID
                if current_agent_format
                else True
            )
            and workflow_block_guarantee > 0
            and workflow_inode_guarantee > 0
            and system_block_reserve > 0
            and system_inode_reserve > 0
            and workflow_block_guarantee <= nblocks // storage_scope_slots
            and workflow_inode_guarantee <= total_inodes // storage_scope_slots
            and system_block_reserve
            <= nblocks - workflow_block_guarantee * storage_scope_slots
            and system_inode_reserve
            <= total_inodes - workflow_inode_guarantee * storage_scope_slots
            and policy_checksum == expected_checksum
        )
        if not valid_contract:
            raise ValueError("invalid scoped storage policy contract")
    if (
        current_baseline_format
        and public_principal_id != FS_PUBLIC_PRINCIPAL_ID
    ):
        raise ValueError("invalid baseline storage principal contract")
    return sb


def block(image: bytes, blockno: int) -> bytes:
    start = blockno * BSIZE
    return image[start : start + BSIZE]


def read_inode(image: bytes, sb: Superblock, inum: int) -> Dinode:
    offset = (inum // sb.ipb + sb.inodestart) * BSIZE + (inum % sb.ipb) * sb.dinode_size
    raw = image[offset : offset + sb.dinode_size]
    file_type = u16(raw, 0)
    vfs_policy = None
    vfs_scope_id = None
    fs_owner_domain = None
    fs_owner_version = None
    if sb.magic in BASELINE_QUOTA_MAGICS:
        fs_owner_version = u16(raw, 2)
        fs_owner_domain = u32(raw, 4)
        if sb.magic == FSMAGIC_BASELINE_PRINCIPAL:
            owner_valid = (
                (file_type == 0 and fs_owner_domain == FS_OWNER_NONE
                 and fs_owner_version == 0)
                or (file_type != 0
                    and fs_owner_domain in {FS_OWNER_SYSTEM, FS_OWNER_PUBLIC}
                    and fs_owner_version == FS_OWNER_VERSION_SCOPED_LEGACY)
            )
        else:
            owner_valid = fs_owner_valid(
                file_type,
                fs_owner_domain,
                fs_owner_version,
                FS_OWNER_VERSION_LEGACY,
                VFS_SCOPE_FIRST_DYNAMIC_LEGACY,
            )
        if not owner_valid:
            raise ValueError(f"invalid filesystem owner on inode {inum}")
    if sb.magic in VFS_POLICY_MAGICS:
        vfs_policy = validate_vfs_label(raw, inum, file_type, sb.magic)
        vfs_scope_id = u32(raw, 96)
        if sb.magic in AGENT_QUOTA_MAGICS:
            fs_owner_domain = u32(raw, 116)
            fs_owner_version = u32(raw, 120)
    addrs = [u32(raw, 12 + i * 4) for i in range(NDIRECT + 1)]
    return Dinode(
        type=file_type,
        size=u32(raw, 8),
        addrs=addrs,
        vfs_policy=vfs_policy,
        vfs_scope_id=vfs_scope_id,
        fs_owner_domain=fs_owner_domain,
        fs_owner_version=fs_owner_version,
    )


def read_file(image: bytes, inode: Dinode) -> bytes:
    data = bytearray()
    remaining = inode.size
    for addr in inode.addrs[:NDIRECT]:
        if remaining <= 0:
            break
        if addr == 0:
            break
        chunk = block(image, addr)
        take = min(remaining, BSIZE)
        data.extend(chunk[:take])
        remaining -= take
    if remaining > 0 and inode.addrs[NDIRECT] != 0:
        indirect = block(image, inode.addrs[NDIRECT])
        for i in range(NINDIRECT):
            if remaining <= 0:
                break
            addr = u32(indirect, i * 4)
            if addr == 0:
                break
            chunk = block(image, addr)
            take = min(remaining, BSIZE)
            data.extend(chunk[:take])
            remaining -= take
    return bytes(data)


def dir_name(raw: bytes) -> str:
    name = raw.split(b"\0", 1)[0]
    return name.decode("utf-8", errors="replace")


def root_entries(image: bytes, sb: Superblock) -> list[tuple[int, str]]:
    root = read_inode(image, sb, ROOTINO)
    data = read_file(image, root)
    entries: list[tuple[int, str]] = []
    for offset in range(0, len(data) - 15, 16):
        inum = u16(data, offset)
        if inum == 0:
            continue
        name = dir_name(data[offset + 2 : offset + 2 + DIRSIZ])
        if name:
            entries.append((inum, name))
    return entries


def discover_name_map(repo_dir: Path | None) -> dict[str, str]:
    if repo_dir is None:
        return {}
    roots = [repo_dir / "user" / "src", repo_dir / "user" / "include", repo_dir / "README.md"]
    candidates: set[str] = set()
    for root in roots:
        if root.is_dir():
            for path in root.rglob("*"):
                if path.suffix not in (".c", ".h", ".md"):
                    continue
                candidates.update(re.findall(r"rp_[A-Za-z0-9_]+", path.read_text(encoding="utf-8", errors="ignore")))
        elif root.is_file():
            candidates.update(re.findall(r"rp_[A-Za-z0-9_]+", root.read_text(encoding="utf-8", errors="ignore")))
    by_short: dict[str, list[str]] = {}
    for name in candidates:
        by_short.setdefault(name[:DIRSIZ], []).append(name)
    result: dict[str, str] = {}
    for short, names in by_short.items():
        unique = sorted(set(names))
        if len(unique) == 1:
            result[short] = unique[0]
    return result


def looks_like_state_text(data: bytes) -> bool:
    if not data or b"\0" in data:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(1 for ch in text if ch == "\n" or ch == "\t" or 32 <= ord(ch) < 127)
    if printable * 100 < len(text) * 90:
        return False
    return "=" in text and "\n" in text


def validate_state_name(name: str) -> str:
    if not STATE_NAME_RE.fullmatch(name):
        raise ValueError(f"unsafe state filename: {name!r}")
    return name


def clear_extracted_state(out_dir: Path) -> None:
    for item in out_dir.iterdir():
        owned = (
            item.name == "extract-summary.json"
            or STATE_NAME_RE.fullmatch(item.name) is not None
            or SCOPE_DIRECTORY_RE.fullmatch(item.name) is not None
        )
        if not owned:
            continue
        if item.is_symlink() or not item.is_dir():
            item.unlink()
        else:
            shutil.rmtree(item)


def contained_destination(out_dir: Path, relative: Path) -> Path:
    root = out_dir.resolve()
    destination = (out_dir / relative).resolve()
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"state output escapes extraction directory: {relative}"
        ) from error
    return destination


def extract_state_files(
    image_path: Path,
    out_dir: Path,
    repo_dir: Path | None = None,
    *,
    scope_id: int | None = None,
    require_single_scope: bool = False,
) -> dict[str, object]:
    if scope_id is not None and require_single_scope:
        raise ValueError("scope_id and require_single_scope are mutually exclusive")
    image = image_path.read_bytes()
    sb = read_superblock(image)
    name_map = discover_name_map(repo_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    workflow_names: set[tuple[int, str]] = set()
    state_entries: list[tuple[int, str, bytes]] = []
    scanned = 0
    skipped_binary = 0
    for inum, short_name in root_entries(image, sb):
        if not short_name.startswith("rp_"):
            continue
        scanned += 1
        inode = read_inode(image, sb, inum)
        if inode.type != T_FILE:
            continue
        if (
            sb.magic in VFS_POLICY_MAGICS
            and inode.vfs_policy != VFS_POLICY_WORKFLOW
        ):
            continue
        if sb.magic in VFS_POLICY_MAGICS:
            entry_scope_id = (
                inode.vfs_scope_id
                if sb.magic in SCOPED_MAGICS
                and inode.vfs_scope_id is not None
                else VFS_SCOPE_SYSTEM
            )
            name_key = (entry_scope_id, short_name)
            if name_key in workflow_names:
                raise ValueError(
                    "duplicate workflow directory entry "
                    f"scope={entry_scope_id} name={short_name}"
                )
            workflow_names.add(name_key)
        else:
            entry_scope_id = VFS_SCOPE_SYSTEM
        data = read_file(image, inode)
        if not looks_like_state_text(data):
            skipped_binary += 1
            continue
        validate_state_name(short_name)
        full_name = validate_state_name(name_map.get(short_name, short_name))
        state_entries.append((entry_scope_id, full_name, data))
    scoped_format = sb.magic in SCOPED_MAGICS
    available_scope_ids = sorted(
        {entry_scope for entry_scope, _, _ in state_entries}
    ) if scoped_format else []
    selected_scope_id: int | None = None
    if scoped_format:
        if require_single_scope:
            if len(available_scope_ids) != 1:
                raise ValueError(
                    "expected exactly one workflow scope, found "
                    f"{available_scope_ids}"
                )
            selected_scope_id = available_scope_ids[0]
        elif scope_id is not None:
            first_dynamic = (
                VFS_SCOPE_FIRST_DYNAMIC
                if sb.magic == FSMAGIC_AGENT_PRINCIPAL
                else VFS_SCOPE_FIRST_DYNAMIC_LEGACY
            )
            if scope_id < first_dynamic:
                raise ValueError(f"invalid workflow scope: {scope_id}")
            if scope_id not in available_scope_ids:
                raise ValueError(
                    f"workflow scope {scope_id} is absent; "
                    f"available={available_scope_ids}"
                )
            selected_scope_id = scope_id
    clear_extracted_state(out_dir)
    for entry_scope_id, full_name, data in state_entries:
        if (selected_scope_id is not None and
                entry_scope_id != selected_scope_id):
            continue
        relative = Path(full_name)
        if scoped_format and selected_scope_id is None:
            relative = Path(f"scope-{entry_scope_id}") / full_name
        destination = contained_destination(out_dir, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        extracted.append(relative.as_posix())
    summary = {
        "image": str(image_path),
        "scanned_rp_entries": scanned,
        "extracted_state_files": len(extracted),
        "skipped_binary_entries": skipped_binary,
        "available_scope_ids": available_scope_ids,
        "selected_scope_id": selected_scope_id,
        "scope_layout": (
            "selected" if selected_scope_id is not None else
            "partitioned" if scoped_format else "legacy"
        ),
        "files": sorted(extracted),
        "status": "ready",
    }
    (out_dir / "extract-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract rp_* text state files from a plain uCore fs image.")
    parser.add_argument("--image", type=Path, required=True, help="Path to nfs/fs-copy.img.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for extracted rp_* state files.")
    parser.add_argument("--repo-dir", type=Path, default=None, help="Repository root for restoring long rp_* names.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--scope-id", type=int, help="Extract one explicit workflow scope at the output root.")
    selection.add_argument(
        "--require-single-scope",
        action="store_true",
        help="Require exactly one workflow scope and extract it at the output root.",
    )
    args = parser.parse_args()
    summary = extract_state_files(
        args.image,
        args.out_dir,
        args.repo_dir,
        scope_id=args.scope_id,
        require_single_scope=args.require_single_scope,
    )
    print(
        "plain_ucore_fs_extract: scanned={scanned_rp_entries} extracted={extracted_state_files} skipped_binary={skipped_binary_entries} status={status}".format(
            **summary
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
