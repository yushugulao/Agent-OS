#!/usr/bin/env python3
"""无预读整块缓存收据的变异契约。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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


def validate(bio: str, bio_h: str) -> None:
    for token in (
        "#define BIO_OVERWRITE_FALLBACK 1",
        "struct bio_overwrite_receipt {",
        "struct buf *buf;",
        "uint active;",
        "uint skipped_preread;",
        "bprepare_overwrite(uint, uint, struct bio_overwrite_receipt *)",
        "bpublish_overwrite(struct bio_overwrite_receipt *, uint, struct buf **)",
        "void bcancel_overwrite(struct bio_overwrite_receipt *);",
    ):
        if token not in bio_h:
            raise ContractError(f"overwrite receipt ABI missing {token}")
    if "overwrite" in bio_h[bio_h.index("struct buf {"):
                            bio_h.index("};", bio_h.index("struct buf {"))]:
        raise ContractError("overwrite state enlarged every cache line")

    bget = compact(function_body(bio, "bget"))
    for token in (
        "if (fresh_line != 0) *fresh_line = 0",
        "if (fresh_line != 0) *fresh_line = 1",
    ):
        if token not in bget:
            raise ContractError(f"bget miss receipt missing {token}")
    ordered(
        bget,
        "*fresh_line = 0",
        "b = bio_cache_hash_find(dev, blockno)",
        "bio_cache_hash_insert(b)",
        "*fresh_line = 1",
    )

    prepare = compact(function_body(bio, "bprepare_overwrite"))
    ordered(
        prepare,
        "memset(receipt, 0, sizeof(*receipt))",
        "if (fs_epoch_buffer_dirty(dev, blockno)) return BIO_OVERWRITE_FALLBACK",
        "b = bget(dev, blockno, &result, &fresh_line)",
        "if (!fresh_line || b->valid || fs_epoch_buffer_dirty(dev, blockno))",
        "brelse(b)",
        "return BIO_OVERWRITE_FALLBACK",
        "receipt->buf = b",
        "receipt->active = 1",
        "receipt->skipped_preread = 1",
    )
    if "virtio_disk_rw" in prepare or "memset(b->data" in prepare:
        raise ContractError("prepare performs I/O or pretends to initialize data")

    publish = compact(function_body(bio, "bpublish_overwrite"))
    ordered(
        publish,
        "initialized != BSIZE",
        "b->holder != bio_cache_holder_token()",
        "b->dev != receipt->dev || b->blockno != receipt->blockno",
        "if (fs_epoch_buffer_dirty(receipt->dev, receipt->blockno))",
        "b->valid = 1",
        "b->disk_result = VIRTIO_DISK_OK",
        "kernel_performance_overwrite_preread_skipped(1)",
        "*out = b",
        "memset(receipt, 0, sizeof(*receipt))",
    )
    if "brelse(b)" in publish:
        raise ContractError("publish drops the caller's held buffer")

    cancel = compact(function_body(bio, "bcancel_overwrite"))
    ordered(
        cancel,
        "b->holder != bio_cache_holder_token()",
        "b->dev != receipt->dev || b->blockno != receipt->blockno",
        "memset(b->data, 0, sizeof(b->data))",
        "b->disk_result = VIRTIO_DISK_ERR_IO",
        "memset(receipt, 0, sizeof(*receipt))",
        "brelse(b)",
    )
    if "kernel_performance_overwrite_preread_skipped" in cancel:
        raise ContractError("cancel reports a skipped preread as completed work")
    if bio.count("kernel_performance_overwrite_preread_skipped(1);") != 1:
        raise ContractError("skipped preread evidence has multiple producers")


validate(BIO, BIO_H)

MUTATIONS = (
    (replace_in_function(BIO, "bget", "*fresh_line = 1;", ""), BIO_H,
     "fresh miss publication removed"),
    (replace_in_function(BIO, "bprepare_overwrite",
                         "if (fs_epoch_buffer_dirty(dev, blockno))",
                         "if (0)"), BIO_H,
     "dirty epoch gate removed"),
    (replace_in_function(BIO, "bprepare_overwrite",
                         "!fresh_line || b->valid", "b->valid"), BIO_H,
     "valid/invalid hit fallback removed"),
    (replace_in_function(BIO, "bprepare_overwrite",
                         "receipt->skipped_preread = 1;", ""), BIO_H,
     "miss receipt evidence removed"),
    (replace_in_function(BIO, "bpublish_overwrite",
                         "initialized != BSIZE", "0"), BIO_H,
     "partial initialization accepted"),
    (replace_in_function(BIO, "bpublish_overwrite",
                         "b->holder != bio_cache_holder_token()", "0"), BIO_H,
     "publish holder authority removed"),
    (replace_in_function(BIO, "bpublish_overwrite",
                         "if (fs_epoch_buffer_dirty(receipt->dev, receipt->blockno))",
                         "if (0)"), BIO_H,
     "publish epoch recheck removed"),
    (replace_in_function(BIO, "bpublish_overwrite",
                         "kernel_performance_overwrite_preread_skipped(1);", ""), BIO_H,
     "skipped preread evidence removed"),
    (replace_in_function(BIO, "bcancel_overwrite",
                         "memset(b->data, 0, sizeof(b->data));", ""), BIO_H,
     "cancel stale data clearing removed"),
    (replace_in_function(BIO, "bcancel_overwrite", "brelse(b);", ""), BIO_H,
     "cancel hash recycle and wake removed"),
)

for mutated_bio, mutated_header, label in MUTATIONS:
    try:
        validate(mutated_bio, mutated_header)
    except ContractError:
        continue
    raise SystemExit(f"overwrite cache mutation survived: {label}")

print(f"[bio-overwrite-cache] {len(MUTATIONS)} mutations passed")
