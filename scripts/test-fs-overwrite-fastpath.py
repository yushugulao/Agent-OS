#!/usr/bin/env python3
"""文件系统整块覆盖集成的变异契约。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FS = (ROOT / "os/fs.c").read_text(encoding="utf-8")


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
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            depth = 0
            for index in range(brace, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[brace + 1:index]
        search = closing + 1


def compact(value: str) -> str:
    return " ".join(value.split())


def require(body: str, token: str, context: str) -> None:
    if token not in body:
        raise ContractError(f"{context} missing {token}")


def ordered(body: str, *tokens: str) -> None:
    cursor = 0
    for token in tokens:
        position = body.find(token, cursor)
        if position < 0:
            raise ContractError(f"ordering contract missing {tokens}")
        cursor = position + len(token)


def replace_in_function(source: str, name: str, old: str, new: str) -> str:
    body = function_body(source, name)
    if old not in body:
        raise ContractError(f"mutation anchor drift: {name}: {old}")
    return source.replace(body, body.replace(old, new, 1), 1)


def validate(source: str) -> None:
    zero = compact(function_body(source, "bzero"))
    for token in (
        "struct bio_overwrite_receipt overwrite = BIO_OVERWRITE_RECEIPT_INIT",
        "if (result == BIO_OVERWRITE_FALLBACK)",
        "else if (result == VIRTIO_DISK_OK) { bp = overwrite.buf",
        "if (overwrite.active) bcancel_overwrite(&overwrite); else brelse(bp)",
        "bpublish_overwrite(&overwrite, BSIZE, &bp)",
    ):
        require(zero, token, "bzero overwrite path")
    ordered(
        zero,
        "bprepare_overwrite(dev, bno, &overwrite)",
        "if (result == BIO_OVERWRITE_FALLBACK)",
        "fs_read_block(dev, bno, &bp)",
        "bclaim(bp)",
        "memset(bp->data, 0, BSIZE)",
        "bpublish_overwrite(&overwrite, BSIZE, &bp)",
        "fs_write_data_block(bp)",
    )
    require(
        zero,
        "if (result == VIRTIO_DISK_ERR_BUSY) { bcancel_overwrite(&overwrite); result = fs_read_block(dev, bno, &bp)",
        "bzero publish-race fallback",
    )
    if zero.count("bclaim(bp)") < 2 or zero.count("memset(bp->data, 0, BSIZE)") < 2:
        raise ContractError("bzero fallback does not reclaim and reinitialize ownership")
    if zero.count("bcancel_overwrite(&overwrite)") < 3:
        raise ContractError("bzero does not cancel every unpublished failure path")

    write = compact(function_body(source, "writei_charged_locked"))
    for token in (
        "m = MIN(n - tot, BSIZE - off % BSIZE)",
        "full_overwrite = ip->type != T_DIR",
        "(off % BSIZE) == 0",
        "m == BSIZE",
        "if (full_overwrite)",
        "if (failure_result == BIO_OVERWRITE_FALLBACK) failure_result = fs_read_block(ip->dev, addr, &bp)",
        "else if (failure_result == VIRTIO_DISK_OK) bp = overwrite.buf",
        "} else { failure_result = fs_read_block(ip->dev, addr, &bp); }",
        "if (overwrite.active) bcancel_overwrite(&overwrite); else brelse(bp)",
        "bpublish_overwrite(&overwrite, m, &bp)",
    ):
        require(write, token, "writei full-block path")
    ordered(
        write,
        "m = MIN(n - tot, BSIZE - off % BSIZE)",
        "full_overwrite =",
        "bprepare_overwrite(ip->dev, addr, &overwrite)",
        "either_copyin(user_src, src",
        "bpublish_overwrite(&overwrite, m, &bp)",
        "fs_write_data_block(bp)",
    )
    require(
        write,
        "if (failure_result == VIRTIO_DISK_ERR_BUSY) { bcancel_overwrite(&overwrite); failure_result = fs_read_block(ip->dev, addr, &bp)",
        "writei publish-race fallback",
    )
    require(
        write,
        "failure_result >= 0 && either_copyin(user_src, src, (char *)bp->data, m) == -1",
        "writei publish-race recopy",
    )
    if write.count("bcancel_overwrite(&overwrite)") < 4:
        raise ContractError("writei does not cancel every unpublished failure path")
    if "kernel_performance_overwrite_preread_skipped" in zero + write:
        raise ContractError("filesystem, rather than BIO publication, reports a skip")
    for forbidden in ("AGENT_ROLE", "ORCHESTRATOR", "RECOVERY"):
        if forbidden in zero + write:
            raise ContractError(f"overwrite path contains role special-case: {forbidden}")
    if source.count("bprepare_overwrite(") != 2:
        raise ContractError("filesystem overwrite entry points are not narrowly scoped")


validate(FS)

MUTATIONS = (
    ("bzero", "result = bprepare_overwrite(dev, bno, &overwrite);", "result = BIO_OVERWRITE_FALLBACK;", "bzero prepare removed"),
    ("bzero", "if (result == BIO_OVERWRITE_FALLBACK)", "if (0)", "bzero cache fallback removed"),
    ("bzero", "result = bclaim(bp);", "result = VIRTIO_DISK_OK;", "bzero sponsorship claim removed"),
    ("bzero", "memset(bp->data, 0, BSIZE);", "", "bzero initialization removed"),
    ("bzero", "result = bpublish_overwrite(&overwrite, BSIZE, &bp);", "result = VIRTIO_DISK_OK;", "bzero publication removed"),
    ("bzero", "if (overwrite.active)\n\t\t\tbcancel_overwrite(&overwrite);", "if (overwrite.active)\n\t\t\t;", "bzero claim-failure cancellation removed"),
    ("writei_charged_locked", "ip->type != T_DIR", "1", "directory exclusion removed"),
    ("writei_charged_locked", "(off % BSIZE) == 0", "1", "alignment gate removed"),
    ("writei_charged_locked", "m == BSIZE", "m != 0", "full-coverage gate removed"),
    ("writei_charged_locked", "if (failure_result == BIO_OVERWRITE_FALLBACK)", "if (0)", "write cache fallback removed"),
    ("writei_charged_locked", "} else {\n\t\t\tfailure_result = fs_read_block(ip->dev, addr, &bp);\n\t\t}", "} else {\n\t\t\tfailure_result = VIRTIO_DISK_OK;\n\t\t}", "partial-write bread removed"),
    ("writei_charged_locked", "bpublish_overwrite(&overwrite, m, &bp)", "bpublish_overwrite(&overwrite, 0, &bp)", "partial publication accepted"),
    ("writei_charged_locked", "if (overwrite.active)\n\t\t\t\tbcancel_overwrite(&overwrite);", "if (overwrite.active)\n\t\t\t\t;", "copy failure cancellation removed"),
    ("writei_charged_locked", "failure_result = fs_read_block(ip->dev, addr, &bp);", "failure_result = VIRTIO_DISK_OK;", "ordinary bread fallback removed"),
    ("writei_charged_locked", "failure_result = bpublish_overwrite(&overwrite, m, &bp);", "failure_result = VIRTIO_DISK_OK;", "write publication removed"),
    ("writei_charged_locked", "either_copyin(user_src, src,\n\t\t\t\t\t\t  (char *)bp->data, m) == -1", "0", "publish-race recopy removed"),
)

for function, old, new, label in MUTATIONS:
    mutated = replace_in_function(FS, function, old, new)
    try:
        validate(mutated)
    except ContractError:
        continue
    raise SystemExit(f"filesystem overwrite mutation survived: {label}")

print(f"[fs-overwrite-fastpath] {len(MUTATIONS)} mutations passed")
