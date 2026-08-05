#!/usr/bin/env python3
"""Verify that tracked content updates stay off the catalog transaction path."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


FORBIDDEN_CONTENT_CALLS = (
    "agent_metadata_txn_",
    "agent_metadata_catalog_",
    "agent_metadata_scan_index_inode(",
    "agent_metadata_note_catalog_changes(",
    "agent_file_store_load(",
    "safestrcpy(",
)


def compact(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def function(text: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\([^;{{}}]*\)\{{", text)
    if match is None:
        raise ValueError(f"missing function {name}")
    brace = text.find("{", match.start())
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[match.start() : index + 1]
    raise ValueError(f"unterminated function {name}")


def require(text: str, fragment: str, message: str) -> None:
    if fragment not in text:
        raise ValueError(message)


def reject(text: str, fragment: str, message: str) -> None:
    if fragment in text:
        raise ValueError(message)


def check(root: Path) -> None:
    source = compact(root / "os/agent_metadata_directory.c")
    header = compact(root / "os/agent_metadata_directory.h")
    state = compact(root / "os/agent_file_state.c")
    state_header = compact(root / "os/agent_file_state_internal.h")
    catalog = compact(root / "os/agent_metadata_catalog.c")
    catalog_header = compact(root / "os/agent_metadata_catalog.h")
    store = compact(root / "os/agent_metadata_store.c")
    publish = function(source, "agent_fs_publish_content")
    state_publish = function(state, "agent_file_state_content_publish")
    receipt_note = function(catalog, "agent_metadata_catalog_journal_note_content")
    journal_capture = function(catalog, "agent_metadata_catalog_journal_capture")
    journal_commit = function(catalog, "agent_metadata_catalog_journal_commit")

    for name in ("agent_fs_note_write", "agent_fs_note_truncate"):
        body = function(source, name)
        require(
            body,
            "{agent_fs_publish_content(ip);}",
            f"{name} bypasses the shared content fast path",
        )
        for forbidden in FORBIDDEN_CONTENT_CALLS:
            reject(body, forbidden, f"{name} enters catalog transaction work")

    if publish.count("agent_file_state_content_publish(ip,&receipt)") != 1:
        raise ValueError("content hook does not use one file-state transaction")
    reject(
        publish,
        "agent_file_state_content_bump(ip)",
        "content hook retained a separate version transaction",
    )

    allowed_note = "agent_metadata_catalog_journal_note_content(&receipt)"
    if publish.count(allowed_note) != 1:
        raise ValueError("persistent content does not enqueue one exact receipt")
    guarded_publish = publish.replace(allowed_note, "")
    for forbidden in FORBIDDEN_CONTENT_CALLS:
        reject(
            guarded_publish,
            forbidden,
            "tracked write/truncate fast path enters catalog transaction work",
        )
    reject(publish, "summary", "content fast path rewrites summary text")

    if state_publish.count("agent_edit_lock();") != 1 or state_publish.count(
        "agent_edit_unlock(enabled);"
    ) != 1:
        raise ValueError("content publication is not one edit-lock transaction")
    require(
        state_publish,
        "entry=file_version_inode_locked(ip,1);",
        "content publication bypasses the inode sidecar",
    )
    require(
        state_publish,
        "entry->content_version="
        "agent_file_counter_next(&agent_file_content_generation);",
        "content publication does not bump content generation",
    )
    for fragment, label in (
        ("entry->published_size_valid=1;", "valid size overlay"),
        ("entry->published_size_dirty=1;", "dirty size overlay"),
        ("entry->published_size=ip->size;", "inode size snapshot"),
        (
            "entry->published_size_sequence="
            "agent_file_counter_next(&agent_file_size_sequence);",
            "size sequence",
        ),
        (
            "entry->published_size_generation="
            "agent_file_state_generation_next_capture("
            "ip->vfs_scope_id,&lifecycle);",
            "scope generation",
        ),
        ("entry->published_size_tick=agent_file_state_now();", "update tick"),
        (
            "entry->published_meta_slot=ip->agent_meta_slot-1;",
            "exact catalog slot binding",
        ),
        (
            "entry->published_lifecycle=lifecycle;",
            "exact workflow lifecycle binding",
        ),
        (
            "receipt->sequence=entry->published_size_sequence;",
            "exact content sequence receipt",
        ),
    ):
        require(state_publish, fragment, f"content publication lost {label}")
    require(
        state_publish,
        "agent_edit_unlock(enabled);returnentry!=0;",
        "content publication does not report success after releasing its lock",
    )
    reject(
        state,
        "agent_file_state_size_publish(",
        "obsolete split size-publication implementation remains",
    )
    require(
        state_header,
        "intagent_file_state_content_publish(structinode*,"
        "structagent_file_content_receipt*);",
        "content publication API is not declared",
    )
    reject(
        state_header,
        "agent_file_state_size_publish(",
        "obsolete split size-publication API remains declared",
    )

    require(
        publish,
        "if(ip->agent_meta_flags&AGENT_FILE_META_F_PERSIST){"
        "if(agent_metadata_catalog_journal_note_content(&receipt)<0)"
        "reconcile=1;"
        "agent_metadata_store_mark_dirty(ip->vfs_scope_id);",
        "persistent content does not queue its receipt before dirty marking",
    )
    require(
        publish,
        "if(reconcile||(ip->agent_meta_flags&AGENT_FILE_META_F_AUTOSCAN))"
        "agent_file_request_scan();",
        "autoscan content does not enqueue background reconciliation",
    )
    require(
        catalog,
        "meta->flags&(AGENT_FILE_META_F_PERSIST|AGENT_FILE_META_F_AUTOSCAN)",
        "inode sidecar drops a hot-path metadata flag",
    )
    for fragment, label in (
        (
            "!agent_file_state_content_publish(ip,&receipt)",
            "overlay publication failure",
        ),
        ("ip->agent_meta_slot<=0", "missing metadata sidecar"),
        (
            "ip->agent_meta_version!=AGENT_INODE_META_VERSION",
            "stale metadata sidecar",
        ),
    ):
        require(publish, fragment, f"content fast path ignores {label}")
    require(
        publish,
        "AGENT_INODE_META_VERSION){agent_file_request_scan();return;}",
        "content fast-path anomalies do not defer to the background scanner",
    )

    for forbidden in ("agent_metadata_txn_", "agent_catalog_require_txn()"):
        reject(
            receipt_note,
            forbidden,
            "content receipt enqueue enters the metadata transaction gate",
        )
    for fragment, label in (
        ("receipt->sequence==0", "content sequence"),
        ("receipt->slot>=AGENT_FILE_META_MAX", "catalog slot"),
        ("receipt->scope_id", "scope"),
        ("receipt->lifecycle", "lifecycle"),
        ("receipt->dev!=ROOTDEV", "inode device identity"),
        ("receipt->incarnation==0", "inode incarnation"),
        (
            "agent_catalog_journal_note_sequence(receipt->scope_id,"
            "receipt->lifecycle,receipt->slot,"
            "agent_catalog_journal_content_sequence(receipt->sequence));",
            "bounded journal ledger enqueue",
        ),
    ):
        require(receipt_note, fragment, f"content receipt lost exact {label}")
    require(
        catalog_header,
        "intagent_metadata_catalog_journal_note_content("
        "conststructagent_file_content_receipt*);",
        "content receipt enqueue API is not declared",
    )

    require(
        journal_capture,
        "agent_file_state_snapshot_overlay_receipt("
        "&change->record.meta,scope_id,slot,lifecycle,&change->content);",
        "journal capture does not bind overlay and receipt atomically",
    )
    require(
        journal_commit,
        "agent_file_state_content_settle(&receipt->changes[captured].content);",
        "journal commit does not settle the exact captured overlay",
    )
    journal_publish = function(store, "agent_meta_journal_primary_publish_locked")
    reject(
        journal_publish,
        "agent_file_state_sizes_persisted(",
        "journal commit still clears uncaptured scope-wide overlays",
    )
    full_publish = function(store, "agent_meta_persist_primary_publish_locked")
    require(
        full_publish,
        "agent_file_state_sizes_persisted(",
        "full-scope snapshot lost its bounded overlay settlement",
    )

    create = function(source, "agent_fs_note_create")
    remove = function(source, "agent_fs_remove_inode")
    require(
        create,
        "agent_metadata_txn_try_external()",
        "create lost its catalog transaction boundary",
    )
    require(
        remove,
        "agent_metadata_txn_try_external()",
        "delete lost its catalog transaction boundary",
    )
    require(
        remove,
        "agent_metadata_catalog_clear_slot(slot)",
        "delete no longer removes its catalog identity",
    )

    for declaration in (
        "voidagent_fs_note_create(structinode*,char*);",
        "voidagent_fs_note_write(structinode*);",
        "voidagent_fs_note_truncate(structinode*);",
        "voidagent_fs_note_delete(structinode*);",
    ):
        require(header, declaration, "directory metadata API is incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"metadata content fast-path check failed: {error}", file=sys.stderr)
        return 1
    print("metadata content fast-path check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
