#!/usr/bin/env python3
"""检查 Agent 文件版本表的固定 bank、稠密索引和失效语义。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def compact(text: str) -> str:
    text = re.sub(r"\\\r?\n", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def braced(text: str, start: int, label: str) -> str:
    brace = text.find("{", start)
    if brace < 0:
        raise ValueError(f"缺少{label}定义")
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError(f"{label}未闭合")


def function(text: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\([^;{{}}]*\)\{{", text)
    if match is None:
        raise ValueError(f"缺少函数 {name}")
    return braced(text, match.start(), f"函数 {name}")


def structure(text: str, name: str) -> str:
    marker = f"struct{name}{{"
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"缺少结构 {name}")
    return braced(text, start, f"结构 {name}")


def numeric_define(text: str, name: str) -> int:
    match = re.search(
        rf"^\s*#define\s+{re.escape(name)}\s+([0-9]+)[uUlL]*\b",
        text,
        flags=re.M,
    )
    if match is None:
        raise ValueError(f"缺少数值常量 {name}")
    return int(match.group(1))


def require(text: str, fragment: str, message: str) -> None:
    if fragment not in text:
        raise ValueError(message)


def require_member(
    text: str, name: str, types: tuple[str, ...], message: str
) -> None:
    type_pattern = "|".join(re.escape(item) for item in types)
    pattern = rf"(?:(?:{type_pattern}){re.escape(name)}|,{re.escape(name)})(?=[,;\[])"
    if re.search(pattern, text) is None:
        raise ValueError(message)


def reject(text: str, fragment: str, message: str) -> None:
    if fragment in text:
        raise ValueError(message)


def check(root: Path) -> None:
    raw_source = (root / "os/agent_file_state.c").read_text(encoding="utf-8")
    source = compact(raw_source)
    meta_max = numeric_define(
        (root / "os/agent.h").read_text(encoding="utf-8"),
        "AGENT_FILE_META_MAX",
    )
    active_scopes = numeric_define(
        (root / "os/workflow_lifecycle.h").read_text(encoding="utf-8"),
        "WORKFLOW_LIFECYCLE_MAX_ACTIVE",
    )

    if meta_max != 512 or active_scopes != 4:
        raise ValueError("版本 bank 的外部容量不再是 512 项和 4 个活动工作流")
    system_capacity = meta_max // 8
    workflow_capacity = (meta_max - system_capacity) // active_scopes
    if system_capacity != 64 or workflow_capacity != 112:
        raise ValueError("版本 bank 容量不再是 SYSTEM=64、工作流=112")
    if system_capacity + active_scopes * workflow_capacity != meta_max:
        raise ValueError("版本 bank 没有完整覆盖 512 个版本项")

    for fragment, label in (
        ("#defineAGENT_FILE_VERSION_MAXAGENT_FILE_META_MAX", "512 项总上界"),
        (
            "#defineAGENT_FILE_VERSION_SYSTEM_RESIDENT"
            "(AGENT_FILE_VERSION_MAX/8U)",
            "64 项 SYSTEM bank",
        ),
        (
            "#defineAGENT_FILE_VERSION_SCOPE_RESIDENT"
            "((AGENT_FILE_VERSION_MAX-AGENT_FILE_VERSION_SYSTEM_RESIDENT)/"
            "VFS_SCOPE_MAX_ACTIVE)",
            "四个 112 项工作流 bank",
        ),
        (
            "#defineAGENT_FILE_CACHE_SCOPE_MAX(VFS_SCOPE_MAX_ACTIVE+1U)",
            "一个 SYSTEM 加四个工作流 bank",
        ),
        (
            "_Static_assert(AGENT_FILE_VERSION_SYSTEM_RESIDENT+"
            "VFS_SCOPE_MAX_ACTIVE*AGENT_FILE_VERSION_SCOPE_RESIDENT=="
            "AGENT_FILE_VERSION_MAX,",
            "bank 总容量断言",
        ),
        (
            "_Static_assert(AGENT_FILE_CACHE_SCOPE_MAX=="
            "VFS_SCOPE_MAX_ACTIVE+1U,",
            "bank 数量断言",
        ),
    ):
        require(source, fragment, f"版本表缺少{label}")
    reject(
        source,
        "#defineAGENT_FILE_CACHE_SCOPE_MAX(VFS_SCOPE_LIFECYCLE_CAP+1U)",
        "版本 bank 仍按历史 lifecycle 槽静态扩张",
    )
    reject(
        source,
        "#defineAGENT_FILE_VERSION_MAXNINODE",
        "文件版本池仍按整个 inode 空间静态分配",
    )

    scope_state = structure(source, "agent_file_cache_scope_state")
    for field, types, label in (
        ("used", ("int",), "bank 占用标记"),
        ("scope_id", ("uint",), "scope 所有权"),
        (
            "lifecycle",
            ("structworkflow_lifecycle_key",),
            "lifecycle 所有权",
        ),
        ("version_count", ("uint",), "稠密项计数"),
        ("version_cursor", ("uint",), "逐 bank 驱逐游标"),
    ):
        require_member(scope_state, field, types, f"版本 bank 状态缺少{label}")

    version_entry = structure(source, "file_version")
    for field, types, label in (
        ("published_size_valid", ("int",), "待落盘发布状态"),
        ("scope_id", ("uint",), "scope 身份"),
        ("dev", ("uint64",), "设备身份"),
        ("inum", ("uint64",), "inode 身份"),
        ("incarnation", ("uint64",), "inode incarnation"),
        (
            "identity_lifecycle",
            ("structworkflow_lifecycle_key",),
            "lifecycle 代际身份",
        ),
    ):
        require_member(version_entry, field, types, f"版本项缺少{label}")
    reject(
        version_entry,
        "published_size_dirty",
        "发布状态重新拆成可能漂移的双标记",
    )

    owner = function(source, "file_version_scope_state_locked")
    for fragment, label in (
        (
            "scope_id==VFS_SCOPE_SYSTEM",
            "SYSTEM 分支",
        ),
        (
            "workflow_lifecycle_key_equal(lifecycle,"
            "workflow_lifecycle_none())",
            "SYSTEM 空 lifecycle 限制",
        ),
        (
            "state=&agent_file_cache_scopes[AGENT_FILE_CACHE_SYSTEM_SLOT];",
            "SYSTEM 固定 bank",
        ),
        ("scope_id<VFS_SCOPE_FIRST_DYNAMIC", "动态 scope 下界"),
        ("scope_id>=FS_OWNER_SCOPE_FLAG", "动态 scope 上界"),
        ("!workflow_lifecycle_key_valid(lifecycle)", "动态 lifecycle 有效性"),
        (
            "for(uintslot=1;slot<AGENT_FILE_CACHE_SCOPE_MAX;slot++)",
            "工作流 bank 有界扫描",
        ),
        ("candidate->used", "工作流 bank 占用检查"),
        ("candidate->scope_id==scope_id", "精确 scope 所有权"),
        (
            "workflow_lifecycle_key_equal(candidate->lifecycle,lifecycle)",
            "精确 lifecycle 代际所有权",
        ),
        ("!candidate->used&&free_state==0", "空闲 bank 分配"),
        ("state->used&&state->scope_id==scope_id", "已绑定 bank 复核"),
        (
            "workflow_lifecycle_key_equal(state->lifecycle,lifecycle)",
            "已绑定代际复核",
        ),
        ("if(!create||state->used)return0;", "禁止覆盖仍占用的 bank"),
        ("state->scope_id=scope_id;", "scope 绑定"),
        ("state->lifecycle=lifecycle;", "lifecycle 绑定"),
        (
            "state->cache_generation=scope_id==VFS_SCOPE_SYSTEM?"
            "agent_file_system_generation:agent_file_generation;",
            "bank 重用的单调代际基线",
        ),
    ):
        require(owner, fragment, f"版本 bank 所有权缺少{label}")
    reject(owner, "agent_file_cache_scopes[lifecycle", "lifecycle 被误作 bank 下标")
    reject(owner, "lifecycle.id", "bank 所有权仍只按可复用 lifecycle id")

    bounds = function(source, "file_version_bank_bounds")
    for fragment, label in (
        ("bank=state-agent_file_cache_scopes;", "bank 编号推导"),
        ("bank>=AGENT_FILE_CACHE_SCOPE_MAX", "bank 边界检查"),
        (
            "bank==AGENT_FILE_CACHE_SYSTEM_SLOT?0:"
            "AGENT_FILE_VERSION_SYSTEM_RESIDENT+"
            "(bank-1)*AGENT_FILE_VERSION_SCOPE_RESIDENT",
            "固定 bank 起点",
        ),
        (
            "bank==AGENT_FILE_CACHE_SYSTEM_SLOT?"
            "AGENT_FILE_VERSION_SYSTEM_RESIDENT:"
            "AGENT_FILE_VERSION_SCOPE_RESIDENT",
            "固定 bank 容量",
        ),
    ):
        require(bounds, fragment, f"版本 bank 布局缺少{label}")

    compare = function(source, "file_version_compare")
    for fragment, label in (
        ("if(entry->dev!=dev)", "设备号"),
        ("returnentry->dev<dev?-1:1;", "设备号排序"),
        ("if(entry->inum!=inum)", "inode 号"),
        ("returnentry->inum<inum?-1:1;", "inode 号排序"),
        ("if(entry->incarnation!=incarnation)", "inode incarnation"),
        ("returnentry->incarnation<incarnation?-1:1;", "incarnation 排序"),
    ):
        require(compare, fragment, f"版本项比较缺少{label}")

    lookup = function(source, "file_version_search_locked")
    for fragment, label in (
        ("file_version_bank_bounds(state,&start,&capacity);", "bank 边界"),
        ("low=0,high=state->version_count", "稠密前缀边界"),
        ("if(!state->used||high>capacity)", "bank 计数校验"),
        ("while(low<high)", "二分循环"),
        ("middle=low+(high-low)/2", "无溢出中点"),
        (
            "file_version_compare(&agent_file_versions[start+middle],"
            "dev,inum,incarnation)",
            "完整 inode 身份比较",
        ),
        ("if(order<0)low=middle+1;elsehigh=middle;", "下界二分推进"),
        ("*position=low;", "稠密插入位置"),
        (
            "low<state->version_count&&file_version_compare("
            "&agent_file_versions[start+low],dev,inum,incarnation)==0",
            "精确命中确认",
        ),
    ):
        require(lookup, fragment, f"版本 bank 查找缺少{label}")
    reject(lookup, "AGENT_FILE_VERSION_MAX", "版本查找退化为全表扫描")

    identity = function(source, "file_version_identity_locked")
    require(
        identity,
        "file_version_identity_valid(dev,inum,incarnation,scope_id,lifecycle)",
        "版本查找没有校验完整身份",
    )
    require(
        identity,
        "file_version_scope_state_locked(scope_id,lifecycle,0)",
        "版本查找没有用 scope+lifecycle 精确选择 bank",
    )
    require(
        identity,
        "file_version_search_locked(state,dev,inum,incarnation,0)",
        "版本查找没有在所选 bank 内二分完整 inode 身份",
    )

    allocate = function(source, "file_version_allocate_locked")
    for fragment, label in (
        (
            "file_version_scope_state_locked(scope_id,lifecycle,1)",
            "精确 scope+lifecycle bank 分配",
        ),
        ("file_version_bank_bounds(scope_state,&start,&capacity);", "bank 容量"),
        (
            "if(file_version_search_locked(scope_state,dev,inum,incarnation,"
            "&position))return0;",
            "分配前重复身份检查",
        ),
        (
            "scope_state->version_count>=capacity&&"
            "file_version_evict_locked(scope_state)<0",
            "逐 bank 容量和回收",
        ),
        (
            "memmove(&agent_file_versions[start+position+1],"
            "&agent_file_versions[start+position],"
            "(scope_state->version_count-position)*sizeof(*entry));",
            "稠密有序插入",
        ),
        (
            "if(scope_state->version_count!=0&&"
            "position<=scope_state->version_cursor)"
            "scope_state->version_cursor++;",
            "有序插入后的游标修正",
        ),
        ("entry=&agent_file_versions[start+position];", "bank 内插入槽"),
        ("entry->scope_id=scope_id;", "scope 身份写入"),
        ("entry->dev=dev;", "设备身份写入"),
        ("entry->inum=inum;", "inode 身份写入"),
        ("entry->incarnation=incarnation;", "incarnation 写入"),
        ("entry->identity_lifecycle=lifecycle;", "lifecycle 代际写入"),
        ("scope_state->version_count++;", "bank 计数"),
        (
            "if(scope_state->version_cursor>=scope_state->version_count)"
            "scope_state->version_cursor=0;",
            "插入后的游标归一化",
        ),
    ):
        require(allocate, fragment, f"版本分配缺少{label}")
    search_call = (
        "file_version_search_locked(scope_state,dev,inum,incarnation,&position)"
    )
    first_search = allocate.find(search_call)
    eviction = allocate.find("file_version_evict_locked(scope_state)")
    second_search = allocate.find(search_call, first_search + len(search_call))
    if not 0 <= first_search < eviction < second_search:
        raise ValueError("版本分配没有在逐 bank 驱逐后重新计算有序插入位置")
    reject(allocate, "AGENT_FILE_SCOPE_LIMIT", "版本容量仍与目录配额耦合")
    reject(allocate, "agent_file_version_active", "版本容量仍使用全局计数")

    evict = function(source, "file_version_evict_locked")
    for fragment, label in (
        ("count=state->version_count", "bank 驻留计数"),
        ("walked<count", "有界 bank 扫描"),
        (
            "position=(state->version_cursor+walked)%count",
            "逐 bank 时钟游标",
        ),
        ("&agent_file_versions[start+position]", "bank 内候选"),
        ("entry->published_size_valid", "待发布状态保护"),
        ("file_version_has_edit_locked(entry)", "活动编辑保护"),
        ("state->version_cursor=(position+1)%count;", "逐 bank 游标推进"),
        (
            "file_version_clear_locked((int)(start+position))",
            "bank 内冷项回收",
        ),
    ):
        require(evict, fragment, f"版本驻留回收缺少{label}")
    reject(evict, "AGENT_FILE_VERSION_MAX", "驱逐仍全表扫描")

    clear = function(source, "file_version_clear_locked")
    for fragment, label in (
        (
            "file_version_scope_state_locked(entry->scope_id,"
            "entry->identity_lifecycle,0)",
            "精确 bank 反查",
        ),
        ("file_version_bank_bounds(scope_state,&start,&capacity);", "bank 边界"),
        (
            "(uint)slot<start||(uint)slot>=start+scope_state->version_count",
            "稠密前缀范围校验",
        ),
        ("position=(uint)slot-start;", "bank 内位置"),
        ("file_version_digest_clear_locked(entry);", "摘要撤销"),
        ("scope_state->version_count--;", "bank 退款"),
        (
            "memmove(entry,entry+1,"
            "(scope_state->version_count-position)*sizeof(*entry));",
            "稠密向左删除",
        ),
        (
            "memset(&agent_file_versions[start+scope_state->version_count],0,",
            "稠密尾槽清零",
        ),
        ("scope_state->version_cursor>position", "删除前游标修正"),
        (
            "scope_state->version_cursor>=scope_state->version_count",
            "删除后游标归一化",
        ),
    ):
        require(clear, fragment, f"版本删除缺少{label}")
    reject(
        clear,
        "memset(scope_state",
        "单项回收错误释放仍属于当前 lifecycle 的 bank",
    )
    reject(
        clear,
        "scope_state->used=0",
        "单项回收错误解除 bank 的 lifecycle 所有权",
    )

    inode = function(source, "file_version_inode_locked")
    require(
        inode,
        "file_version_current_lifecycle(ip->vfs_scope_id,&lifecycle)",
        "inode 查找没有捕获当前 lifecycle 代际",
    )
    require(
        inode,
        "file_version_identity_locked(ip->dev,ip->inum,ip->vfs_incarnation,"
        "ip->vfs_scope_id,lifecycle)",
        "inode 查找没有使用完整身份",
    )
    require(
        inode,
        "file_version_allocate_locked(ip->dev,ip->inum,ip->vfs_incarnation,"
        "ip->vfs_scope_id,lifecycle)",
        "inode 分配没有使用完整身份",
    )
    reject(
        inode,
        "file_version_retire_inode_locked",
        "版本分配仍在关中断热路径全表清扫",
    )

    unbind = function(source, "agent_file_state_unbind_catalog_identity")
    for fragment, label in (
        ("i<AGENT_FILE_CACHE_SCOPE_MAX", "有界 bank 扫描"),
        ("state->scope_id!=scope_id", "scope 隔离"),
        (
            "file_version_search_locked(state,dev,inum,incarnation,0)",
            "完整 inode 身份二分",
        ),
        (
            "agent_file_counter_next(&agent_file_content_generation)",
            "内容代际撤销",
        ),
        ("entry->published_size_valid=0", "待发布状态撤销"),
        ("entry->published_meta_slot=AGENT_FILE_META_MAX", "目录槽撤销"),
        (
            "entry->published_lifecycle=workflow_lifecycle_none()",
            "发布 lifecycle 撤销",
        ),
        ("file_version_digest_clear_locked(entry)", "摘要撤销"),
    ):
        require(unbind, fragment, f"目录解绑缺少{label}")
    reject(unbind, "file_version_clear_locked", "目录解绑错误销毁 inode 版本")
    reject(unbind, "agent_file_edits", "目录解绑错误撤销活动编辑租约")

    reclaim = function(source, "agent_file_version_reclaim")
    for fragment, label in (
        ("i<AGENT_FILE_CACHE_SCOPE_MAX", "有界 bank 扫描"),
        ("state->scope_id!=ip->vfs_scope_id", "scope 隔离"),
        (
            "file_version_search_locked(state,ip->dev,ip->inum,"
            "ip->vfs_incarnation,0)",
            "完整 inode 身份二分",
        ),
        (
            "file_version_clear_locked((int)(entry-agent_file_versions))",
            "物理 inode 完整删除",
        ),
    ):
        require(reclaim, fragment, f"inode 回收缺少{label}")
    reject(
        reclaim,
        "file_version_retire_inode_locked(ip->dev,ip->inum)",
        "回收旧 inode 时可能误删新 incarnation",
    )

    scope_reclaim = function(source, "agent_file_state_scope_reclaim")
    for fragment, label in (
        ("agent_file_edits[i].scope_id==scope_id", "编辑租约撤销"),
        ("agent_file_digest_cache[i].scope_id==scope_id", "摘要撤销"),
        ("i<AGENT_FILE_CACHE_SCOPE_MAX", "有界 bank 扫描"),
        ("state->scope_id!=scope_id", "scope 精确匹配"),
        ("file_version_bank_bounds(state,&start,&capacity);", "bank 边界"),
        (
            "memset(&agent_file_versions[start],0,"
            "capacity*sizeof(agent_file_versions[0]));",
            "完整 bank 清零",
        ),
        ("memset(state,0,sizeof(*state));", "bank 所有权释放"),
    ):
        require(scope_reclaim, fragment, f"scope 回收缺少{label}")

    for obsolete, message in (
        ("file_version_hash", "版本表仍保留全局哈希"),
        ("file_version_probe_locked", "版本表仍保留开放寻址探测"),
        ("file_version_matches", "版本表仍保留全局哈希等值器"),
        ("agent_file_version_active", "版本表仍保留全局驻留计数"),
        ("agent_file_version_evict_cursor", "版本表仍保留全局驱逐游标"),
        ("AGENT_FILE_VERSION_TOMBSTONE", "版本表仍保留墓碑状态"),
        ("agent_file_versions[inum]", "版本查找退化为裸 inum 下标"),
        ("file_version_clear_locked(ip->inum)", "inode 号被误作版本槽"),
    ):
        reject(source, obsolete, message)

    require(
        source,
        "entry->edit_version=agent_file_edit_version_generation",
        "驻留项重建会让编辑版本倒退",
    )
    reject(
        source,
        "uint64 edit_authority_generation;",
        "全局撤销 epoch 仍按 inode 重复占用版本 bank",
    )
    require(
        source,
        "entry->content_version="
        "agent_file_counter_next(&agent_file_content_generation)",
        "驻留项重建没有隔离旧异步摘要",
    )

    edit_next = function(source, "file_version_edit_next_locked")
    require(
        edit_next,
        "next=agent_file_counter_next(&entry->edit_version);",
        "文件编辑版本没有从该文件自己的当前版本递增",
    )
    require(
        edit_next,
        "if(next>agent_file_edit_version_generation)"
        "agent_file_edit_version_generation=next;",
        "逐文件递增没有维护冷重建所需的全局高水位",
    )
    reject(
        edit_next,
        "agent_file_counter_next(&agent_file_edit_version_generation)",
        "文件编辑版本错误地从全局高水位递增",
    )
    reject(
        source,
        "agent_file_counter_next(&agent_file_edit_version_generation)",
        "无关文件提交会推动当前文件版本跳号",
    )
    if source.count("edit_version=agent_file_edit_version_generation") != 1:
        raise ValueError("全局编辑版本高水位只能用于新建或冷重建项初始化")

    release = function(source, "agent_edit_release_locked")
    require(
        release,
        "if(version&&publish_dirty&&e->dirty)"
        "(void)file_version_edit_next_locked(version);",
        "脏租约释放没有按该文件 base_version + 1 发布",
    )
    note = function(source, "agent_edit_note")
    require(
        note,
        "if(version)(void)file_version_edit_next_locked(version);",
        "无租约修改没有按该文件当前版本递增",
    )
    commit = function(source, "sys_agent_file_edit_commit")
    for fragment, label in (
        (
            "version->edit_version!=call.entry->base_version",
            "当前版本与租约 base_version 的一致性检查",
        ),
        (
            "expected_version!=call.entry->base_version",
            "调用者 expected_version 的一致性检查",
        ),
        (
            "if(call.entry->dirty)"
            "(void)file_version_edit_next_locked(version);",
            "脏提交的逐文件 base_version + 1",
        ),
    ):
        require(commit, fragment, f"编辑提交缺少{label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"文件版本固定 bank 检查失败: {error}", file=sys.stderr)
        return 1
    print("文件版本固定 bank 检查完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
