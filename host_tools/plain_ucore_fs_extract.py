#!/usr/bin/env python3
"""Extract plain uCore rp_* text state files from an xv6-style fs image."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


BSIZE = 1024
FSMAGIC_LEGACY = 0x10203040
FSMAGIC_EXEC_POLICY = 0x10203041
FSMAGIC_VFS_POLICY = 0x10203042
FSMAGIC_BASELINE_QUOTA = 0x10203043
FSMAGIC_AGENT_QUOTA = 0x10203044
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
}
QUOTA_MAGICS = {FSMAGIC_BASELINE_QUOTA, FSMAGIC_AGENT_QUOTA}
VFS_POLICY_MAGICS = {FSMAGIC_VFS_POLICY, FSMAGIC_AGENT_QUOTA}
DIRSIZ = 14
T_FILE = 2

EXEC_MANIFEST_VERSION = 2
EXEC_FLAG_IMMUTABLE = 0x2
EXEC_FLAG_DOMAIN_SAFE = 0x8

VFS_LABEL_MAGIC = 0x56465331
VFS_LABEL_VERSION = 1
VFS_LABEL_VERSION_QUOTA = 2
VFS_LABEL_F_PUBLIC = 0x1
VFS_LABEL_F_PROTECTED = 0x2
VFS_LABEL_F_KERNEL_PRIVATE = 0x4
VFS_LABEL_F_ROOT = 0x8
VFS_LABEL_F_FREE = 0x10
VFS_LABEL_F_KNOWN = 0x1F
VFS_DOMAIN_PUBLIC = 0
VFS_DOMAIN_WORKFLOW = 1
VFS_POLICY_PUBLIC = 1
VFS_POLICY_WORKFLOW = 2
VFS_POLICY_KERNEL_PRIVATE = 3
VFS_POLICY_ROOT = 4
VFS_POLICY_FREE = 5
VFS_POLICY_GENERATION = 1
VFS_EXEC_PROFILE_NONE = 0
VFS_EXEC_PROFILE_WORKFLOW = 1
VFS_EXEC_PROFILE_CONTENT_READ = 2
VFS_EXEC_PROFILE_ARTIFACT_WRITE = 3
FS_OWNER_VERSION = 1


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

    @property
    def ipb(self) -> int:
        return BSIZE // self.dinode_size


@dataclass
class Dinode:
    type: int
    size: int
    addrs: list[int]
    vfs_policy: int | None = None
    fs_owner_domain: int | None = None
    fs_owner_version: int | None = None


def u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


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


def fs_owner_valid(file_type: int, owner_domain: int, owner_version: int) -> bool:
    if file_type == 0:
        return owner_domain == 0 and owner_version == 0
    return owner_domain >= 1 and owner_version == FS_OWNER_VERSION


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

    quota_format = fs_magic == FSMAGIC_AGENT_QUOTA
    expected_version = (
        VFS_LABEL_VERSION_QUOTA if quota_format else VFS_LABEL_VERSION
    )
    owner_valid = fs_owner_valid(
        file_type, fs_owner_domain, fs_owner_version
    )

    if (
        magic != VFS_LABEL_MAGIC
        or version != expected_version
        or generation != VFS_POLICY_GENERATION
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
    if policy == VFS_POLICY_PUBLIC:
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
    return sb


def block(image: bytes, blockno: int) -> bytes:
    start = blockno * BSIZE
    return image[start : start + BSIZE]


def read_inode(image: bytes, sb: Superblock, inum: int) -> Dinode:
    offset = (inum // sb.ipb + sb.inodestart) * BSIZE + (inum % sb.ipb) * sb.dinode_size
    raw = image[offset : offset + sb.dinode_size]
    file_type = u16(raw, 0)
    vfs_policy = None
    fs_owner_domain = None
    fs_owner_version = None
    if sb.magic == FSMAGIC_BASELINE_QUOTA:
        fs_owner_version = u16(raw, 2)
        fs_owner_domain = u32(raw, 4)
        if not fs_owner_valid(
            file_type, fs_owner_domain, fs_owner_version
        ):
            raise ValueError(f"invalid filesystem owner on inode {inum}")
    if sb.magic in VFS_POLICY_MAGICS:
        vfs_policy = validate_vfs_label(raw, inum, file_type, sb.magic)
        if sb.magic == FSMAGIC_AGENT_QUOTA:
            fs_owner_domain = u32(raw, 116)
            fs_owner_version = u32(raw, 120)
    addrs = [u32(raw, 12 + i * 4) for i in range(NDIRECT + 1)]
    return Dinode(
        type=file_type,
        size=u32(raw, 8),
        addrs=addrs,
        vfs_policy=vfs_policy,
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


def extract_state_files(image_path: Path, out_dir: Path, repo_dir: Path | None = None) -> dict[str, object]:
    image = image_path.read_bytes()
    sb = read_superblock(image)
    name_map = discover_name_map(repo_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    workflow_short_names: set[str] = set()
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
            if short_name in workflow_short_names:
                raise ValueError(
                    f"duplicate workflow directory entry {short_name}"
                )
            workflow_short_names.add(short_name)
        data = read_file(image, inode)
        if not looks_like_state_text(data):
            skipped_binary += 1
            continue
        full_name = name_map.get(short_name, short_name)
        (out_dir / full_name).write_bytes(data)
        extracted.append(full_name)
    summary = {
        "image": str(image_path),
        "scanned_rp_entries": scanned,
        "extracted_state_files": len(extracted),
        "skipped_binary_entries": skipped_binary,
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
    args = parser.parse_args()
    summary = extract_state_files(args.image, args.out_dir, args.repo_dir)
    print(
        "plain_ucore_fs_extract: scanned={scanned_rp_entries} extracted={extracted_state_files} skipped_binary={skipped_binary_entries} status={status}".format(
            **summary
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
