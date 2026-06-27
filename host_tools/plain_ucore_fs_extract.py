#!/usr/bin/env python3
"""Extract plain uCore rp_* text state files from an xv6-style fs image."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


BSIZE = 1024
FSMAGIC = 0x10203040
ROOTINO = 1
NDIRECT = 12
NINDIRECT = BSIZE // 4
DINODE_SIZE = 64
IPB = BSIZE // DINODE_SIZE
DIRSIZ = 14
T_FILE = 2


@dataclass
class Superblock:
    magic: int
    size: int
    nblocks: int
    ninodes: int
    inodestart: int
    bmapstart: int


@dataclass
class Dinode:
    type: int
    size: int
    addrs: list[int]


def u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def read_superblock(image: bytes) -> Superblock:
    offset = BSIZE
    sb = Superblock(
        magic=u32(image, offset),
        size=u32(image, offset + 4),
        nblocks=u32(image, offset + 8),
        ninodes=u32(image, offset + 12),
        inodestart=u32(image, offset + 16),
        bmapstart=u32(image, offset + 20),
    )
    if sb.magic != FSMAGIC:
        raise ValueError(f"bad fs magic: 0x{sb.magic:08x}")
    return sb


def block(image: bytes, blockno: int) -> bytes:
    start = blockno * BSIZE
    return image[start : start + BSIZE]


def read_inode(image: bytes, sb: Superblock, inum: int) -> Dinode:
    offset = (inum // IPB + sb.inodestart) * BSIZE + (inum % IPB) * DINODE_SIZE
    raw = image[offset : offset + DINODE_SIZE]
    addrs = [u32(raw, 12 + i * 4) for i in range(NDIRECT + 1)]
    return Dinode(type=u16(raw, 0), size=u32(raw, 8), addrs=addrs)


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
    scanned = 0
    skipped_binary = 0
    for inum, short_name in root_entries(image, sb):
        if not short_name.startswith("rp_"):
            continue
        scanned += 1
        inode = read_inode(image, sb, inum)
        if inode.type != T_FILE:
            continue
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
