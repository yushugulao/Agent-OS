#!/usr/bin/env python3
"""验证受跟踪内容的更新不会进入 catalog 事务路径。"""

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


def guarded_block(text: str, conditions: tuple[str, ...]) -> tuple[str, str]:
    """返回用于保护全部 token 的带花括号 if 条件及其主体。"""
    for match in re.finditer(r"(?<![A-Za-z0-9_])if\(", text):
        opening = match.end() - 1
        depth = 0
        closing = -1
        for index in range(opening, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            raise ValueError("unterminated if condition")
        condition = text[opening + 1 : closing]
        if not all(token in condition for token in conditions):
            continue
        if closing + 1 >= len(text) or text[closing + 1] != "{":
            raise ValueError("content anomaly guard must use one compound block")
        body_opening = closing + 1
        depth = 0
        for index in range(body_opening, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return condition, text[body_opening + 1 : index]
        raise ValueError("unterminated if body")
    raise ValueError("content anomalies do not share one failure guard")


def require(text: str, fragment: str, message: str) -> None:
    if fragment not in text:
        raise ValueError(message)


def reject(text: str, fragment: str, message: str) -> None:
    if fragment in text:
        raise ValueError(message)


def check(root: Path) -> None:
    source = compact(root / "os/agent_metadata_directory.c")
    header = compact(root / "os/agent_metadata_directory.h")
    file_source = compact(root / "os/file.c")
    state = compact(root / "os/agent_file_state.c")
    state_header = compact(root / "os/agent_file_state_internal.h")
    catalog = compact(root / "os/agent_metadata_catalog.c")
    catalog_header = compact(root / "os/agent_metadata_catalog.h")
    store = compact(root / "os/agent_metadata_store.c")
    publish = function(source, "agent_fs_publish_content")
    state_publish = function(state, "agent_file_state_content_publish")
    generation_capture = function(
        state, "agent_file_state_generation_next_capture_locked"
    )
    receipt_note = function(catalog, "agent_metadata_catalog_journal_note_content")
    journal_capture = function(catalog, "agent_metadata_catalog_journal_capture")
    journal_commit = function(catalog, "agent_metadata_catalog_journal_commit")
    content_commit = function(catalog, "agent_metadata_catalog_content_commit")

    for name in ("agent_fs_note_write", "agent_fs_note_truncate"):
        body = function(source, name)
        require(
            body,
            "{if(FS_META_UNBOUND(ip))return;agent_fs_publish_content(ip);}",
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
    reject(
        state_publish,
        "agent_file_state_generation_next(",
        "content publication re-enters the edit guard through the public API",
    )
    for lock_call in ("agent_edit_lock(", "agent_edit_unlock("):
        reject(
            generation_capture,
            lock_call,
            "locked generation capture unexpectedly re-enters the edit guard",
        )
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
        ("entry->published_size=ip->size;", "inode size snapshot"),
        (
            "entry->published_size_sequence="
            "agent_file_counter_next(&agent_file_size_sequence);",
            "size sequence",
        ),
        (
            "entry->published_size_generation="
            "agent_file_state_generation_next_capture_locked("
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
    reject(
        state,
        "published_size_dirty",
        "content overlay retained a redundant dirty-state flag",
    )
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
    if publish.count("reconcile=1;") != 1:
        raise ValueError("仅持久化回执失败可以请求内容协调扫描")
    require(
        catalog,
        "meta->flags&(AGENT_FILE_META_F_PERSIST|AGENT_FILE_META_F_AUTOSCAN)",
        "inode sidecar drops a hot-path metadata flag",
    )
    anomaly_conditions = (
        "ip->agent_meta_slot<=0",
        "ip->agent_meta_version!=AGENT_INODE_META_VERSION",
        "!agent_file_state_content_publish(ip,&receipt)",
    )
    for fragment, label in (
        (
            anomaly_conditions[2],
            "overlay publication failure",
        ),
        (anomaly_conditions[0], "missing metadata sidecar"),
        (
            anomaly_conditions[1],
            "stale metadata sidecar",
        ),
    ):
        require(publish, fragment, f"content fast path ignores {label}")
    anomaly_condition, anomaly_body = guarded_block(
        publish, anomaly_conditions
    )
    if anomaly_condition.count("||") < len(anomaly_conditions) - 1:
        raise ValueError("content anomalies are not fail-closed alternatives")
    scan = anomaly_body.find("agent_file_request_scan();")
    leave = anomaly_body.find("return;", scan + 1)
    if scan < 0 or leave < 0:
        raise ValueError(
            "content fast-path anomalies do not defer to the background scanner"
        )
    require(
        source,
        "#defineFS_META_UNBOUND(ip)((ip)&&!(ip)->agent_meta_slot&&"
        "!(ip)->agent_meta_flags&&!(ip)->agent_meta_version)",
        "普通文件快路不是精确的全零未绑定状态",
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
        "agent_metadata_catalog_content_commit("
        "&receipt->changes[captured].content);",
        "journal commit does not settle the exact captured overlay",
    )
    for fragment, label in (
        ("agent_catalog_files[slot].dev!=receipt->dev", "设备号"),
        ("agent_catalog_files[slot].inum!=receipt->inum", "inode 号"),
        ("agent_catalog_files[slot].incarnation!=receipt->incarnation",
         "inode incarnation"),
        ("agent_file_state_content_settle(receipt,"
         "&agent_catalog_files[slot])", "精确覆盖吸收"),
    ):
        require(content_commit, fragment, f"目录内容提交缺少{label}")
    absorb = function(state, "file_version_size_absorb_locked")
    require(
        absorb,
        "meta->size=entry->published_size;"
        "if(entry->published_size_generation>meta->fs_generation)"
        "meta->fs_generation=entry->published_size_generation;"
        "if(entry->published_size_tick>meta->updated_tick)"
        "meta->updated_tick=entry->published_size_tick;"
        "entry->published_size_valid=0;",
        "已吸收覆盖未原子写回目录并释放驻留保护",
    )
    journal_publish = function(store, "agent_meta_journal_primary_publish_locked")
    reject(
        journal_publish,
        "agent_metadata_catalog_sizes_persisted(",
        "journal commit still clears uncaptured scope-wide overlays",
    )
    full_publish = function(store, "agent_meta_persist_primary_publish_locked")
    require(
        full_publish,
        "agent_metadata_catalog_sizes_persisted(",
        "full-scope snapshot lost its bounded overlay settlement",
    )
    scan_source = compact(root / "os/agent_metadata_scan.c")
    scan_index = function(scan_source, "agent_metadata_scan_index_inode")
    require(
        scan_index,
        "if(!persist)agent_file_state_content_absorb_volatile(ip,slot);",
        "易失目录扫描没有释放已吸收的大小覆盖",
    )

    remove = function(source, "agent_fs_remove_inode")
    reject(source + header + file_source, "agent_fs_note_create",
           "普通文件创建仍保留 Agent 元数据钩子")
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
    _, ordinary_remove = guarded_block(remove, ("FS_META_UNBOUND(ip)",))
    require(
        ordinary_remove,
        "agent_file_version_reclaim(ip);return;",
        "普通未绑定文件删除没有回收易失版本状态",
    )
    reject(
        ordinary_remove,
        "agent_file_request_scan(",
        "普通未绑定文件删除仍请求全目录扫描",
    )

    for declaration in (
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
