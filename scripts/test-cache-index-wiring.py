#!/usr/bin/env python3
"""有界 inode 与 buffer-cache 键查找的变异防护。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FS = (ROOT / "os/fs.c").read_text(encoding="utf-8")
BIO = (ROOT / "os/bio.c").read_text(encoding="utf-8")
BIO_H = (ROOT / "os/bio.h").read_text(encoding="utf-8")


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    marker = f"{name}("
    search = 0
    while True:
        start = source.find(marker, search)
        if start < 0:
            raise ContractError(f"missing function {name}")
        opening = source.find("(", start)
        depth = 0
        closing = -1
        for index in range(opening, len(source)):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        brace = source.find("{", closing)
        semicolon = source.find(";", closing)
        if closing >= 0 and brace >= 0 and (semicolon < 0 or brace < semicolon):
            depth = 0
            for index in range(brace, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[brace + 1:index]
            raise ContractError(f"unterminated function {name}")
        search = max(closing + 1, start + len(marker))


def compact(value: str) -> str:
    return " ".join(value.split())


def ordered(body: str, *tokens: str) -> bool:
    positions = [body.find(token) for token in tokens]
    return all(position >= 0 for position in positions) and positions == sorted(positions)


def validate(fs: str, bio: str, bio_h: str) -> None:
    if "int hash_head[FS_ICACHE_HASH_BUCKETS];" not in fs:
        raise ContractError("inode cache has no key index")
    if "int free_head;" not in fs:
        raise ContractError("inode cache has no constant-time free list")

    iget = compact(function_body(fs, "iget"))
    for token in (
        "inode_cache_init_once();",
        "bucket = inode_cache_bucket(dev, inum);",
        "index = itable.hash_head[bucket]",
        "index = itable.free_head;",
        "itable.free_head = itable.next[index];",
        "memset(ip, 0, sizeof(*ip));",
        "itable.next[index] = itable.hash_head[bucket];",
        "itable.hash_head[bucket] = index;",
    ):
        if token not in iget:
            raise ContractError(f"inode index lookup missing {token}")
    if "itable.inode[FS_ICACHE_SIZE]" in iget:
        raise ContractError("iget regressed to a capacity-sized linear scan")
    if "if (scanned++ >= FS_ICACHE_SIZE)" not in iget:
        raise ContractError("inode hash lookup has no corruption bound")

    drop = compact(function_body(fs, "inode_cache_drop_ref"))
    if not ordered(
        drop,
        "ip->ref--;",
        "if (ip->ref != 0) return;",
        "*link = itable.next[index];",
        "itable.next[index] = itable.free_head;",
        "itable.free_head = index;",
    ):
        raise ContractError("inode release does not atomically unlink and recycle")
    if fs.count("ip->ref--;") != 1:
        raise ContractError("an inode reference release bypasses the cache index")
    if "if (scanned++ >= FS_ICACHE_SIZE)" not in drop:
        raise ContractError("inode hash unlink has no corruption bound")

    if "struct buf *hash_next;" not in bio_h:
        raise ContractError("buffer cache entries have no hash linkage")
    for name in (
        "bio_cache_hash_find",
        "bio_cache_hash_insert",
        "bio_cache_hash_remove",
    ):
        function_body(bio, name)
    for name in ("bio_cache_hash_find", "bio_cache_hash_remove"):
        if "if (scanned++ >= NBUF)" not in compact(function_body(bio, name)):
            raise ContractError(f"{name} has no corruption bound")

    bget = compact(function_body(bio, "bget"))
    if "b = bio_cache_hash_find(dev, blockno);" not in bget:
        raise ContractError("buffer hits still require an LRU-list scan")
    if "bcache.head.next" in bget:
        raise ContractError("buffer hit lookup regressed to a linear scan")
    if not ordered(
        bget,
        "bio_cache_hash_remove(b);",
        "b->dev = dev;",
        "b->blockno = blockno;",
        "bio_cache_hash_insert(b);",
    ):
        raise ContractError("buffer victim rekey is not index-atomic")

    for name, clear in (
        ("bio_background_reserve_buffers", "candidate->dev = 0;"),
        ("bio_cache_invalidate", "b->dev = 0;"),
    ):
        body = compact(function_body(bio, name))
        subject = "candidate" if name == "bio_background_reserve_buffers" else "b"
        if not ordered(body, f"bio_cache_hash_remove({subject});", clear):
            raise ContractError(f"{name} clears a key before unlinking its index")


validate(FS, BIO, BIO_H)

MUTATIONS = (
    (FS.replace("index = itable.hash_head[bucket];", "index = FS_ICACHE_INDEX_NONE;", 1), BIO, BIO_H),
    (FS.replace("itable.free_head = itable.next[index];", "", 1), BIO, BIO_H),
    (FS.replace("inode_cache_drop_ref(ip);", "ip->ref--;", 1), BIO, BIO_H),
    (FS, BIO.replace("b = bio_cache_hash_find(dev, blockno);", "b = 0;", 1), BIO_H),
    (FS, BIO.replace("bio_cache_hash_remove(candidate);", "", 1), BIO_H),
    (FS, BIO.replace("bio_cache_hash_remove(b);", "", 1), BIO_H),
    (FS, BIO, BIO_H.replace("struct buf *hash_next;", "", 1)),
    (FS.replace("if (scanned++ >= FS_ICACHE_SIZE)", "if (0)", 1), BIO, BIO_H),
    (FS, BIO.replace("if (scanned++ >= NBUF)", "if (0)", 1), BIO_H),
)

for mutated_fs, mutated_bio, mutated_bio_h in MUTATIONS:
    try:
        validate(mutated_fs, mutated_bio, mutated_bio_h)
    except ContractError:
        continue
    raise SystemExit("cache-index mutation survived")

print(f"[cache-index-wiring] {len(MUTATIONS)} mutations passed")
