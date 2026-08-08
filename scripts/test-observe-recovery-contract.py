#!/usr/bin/env python3
"""观测容量、恢复持久化与等待窗口的独立失效关闭约束。"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def c_active_source(source: str) -> str:
    """清空注释、字面量和明确禁用的条件块，同时保持字符偏移。"""
    noise = re.compile(
        r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        re.DOTALL,
    )
    clean = noise.sub(
        lambda match: "".join("\n" if c == "\n" else " " for c in match.group()),
        source,
    )
    lines = clean.splitlines(keepends=True)
    stack: list[tuple[bool, bool]] = []
    active = True
    for index, line in enumerate(lines):
        if re.match(r"^\s*#\s*if\s+0\b", line):
            stack.append((active, True))
            active = False
        elif re.match(r"^\s*#\s*if(?:def|ndef)?\b", line):
            stack.append((active, False))
        elif re.match(r"^\s*#\s*else\b", line) and stack:
            parent, known_zero = stack[-1]
            if known_zero:
                active = parent
        elif re.match(r"^\s*#\s*endif\b", line) and stack:
            parent, _ = stack.pop()
            active = parent
        if not active or re.match(r"^\s*#\s*(?:if|else|endif)\b", line):
            lines[index] = "".join("\n" if c == "\n" else " " for c in line)
    return "".join(lines)


def c_function_span(source: str, name: str) -> tuple[int, int]:
    clean = c_active_source(source)
    assert not re.search(
        rf"^\s*#\s*define\s+{re.escape(name)}\b", clean, re.MULTILINE
    ), f"函数被宏遮蔽: {name}"
    definitions: list[tuple[int, int]] = []
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", clean):
        depth = 1
        cursor = match.end()
        while cursor < len(clean) and depth:
            depth += (clean[cursor] == "(") - (clean[cursor] == ")")
            cursor += 1
        while cursor < len(clean) and clean[cursor].isspace():
            cursor += 1
        if depth or cursor >= len(clean) or clean[cursor] != "{":
            continue
        depth = 0
        for end in range(cursor, len(clean)):
            depth += (clean[end] == "{") - (clean[end] == "}")
            if depth == 0:
                definitions.append((match.start(), end + 1))
                break
    assert len(definitions) == 1, f"函数定义数量异常: {name}={len(definitions)}"
    return definitions[0]


def c_function(source: str, name: str) -> str:
    """提取唯一生效的 C 函数定义。"""
    start, end = c_function_span(source, name)
    return c_active_source(source)[start:end]


def c_tokens(source: str) -> list[str]:
    clean = re.sub(r"//[^\n]*|/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.findall(
        r"[A-Za-z_]\w*|0[xX][0-9A-Fa-f]+|\d+|->|==|!=|<=|>=|&&|\|\||"
        r"\+\+|--|[{}()\[\];,.=*+!<>?:&|~-]",
        clean,
    )


def token_pos(tokens: list[str], pattern: tuple[str, ...], start: int = 0) -> int:
    for index in range(start, len(tokens) - len(pattern) + 1):
        if tuple(tokens[index : index + len(pattern)]) == pattern:
            return index
    raise AssertionError(f"缺少词法序列: {' '.join(pattern)}")


def token_count(tokens: list[str], pattern: tuple[str, ...]) -> int:
    count = 0
    cursor = 0
    while cursor <= len(tokens) - len(pattern):
        try:
            cursor = token_pos(tokens, pattern, cursor)
        except AssertionError:
            break
        count += 1
        cursor += len(pattern)
    return count


def token_order(tokens: list[str], *patterns: tuple[str, ...]) -> None:
    cursor = 0
    for pattern in patterns:
        cursor = token_pos(tokens, pattern, cursor) + len(pattern)


def text_order(source: str, *fragments: str) -> None:
    cursor = 0
    for fragment in fragments:
        found = source.find(fragment, cursor)
        assert found >= 0, f"缺少文本序列: {fragment}"
        cursor = found + len(fragment)


def require(source: str, *fragments: str) -> None:
    for fragment in fragments:
        assert fragment in source, f"缺少约束片段: {fragment}"


def mutate_function(source: str, name: str, old: str, new: str) -> str:
    """只在指定函数体内制造变异，拒绝跨函数的同形锚点。"""
    start, end = c_function_span(source, name)
    body = source[start:end]
    assert body.count(old) == 1, f"{name} 的变异锚点不唯一: {old}"
    mutant = body.replace(old, new, 1)
    return source[:start] + mutant + source[end:]


def forbid_macro(source: str, *names: str) -> None:
    clean = c_active_source(source)
    for name in names:
        assert not re.search(
            rf"^\s*#\s*define\s+{re.escape(name)}\b", clean, re.MULTILINE
        ), f"关键调用被宏遮蔽: {name}"


def forbid_conditionals(source: str, name: str) -> None:
    start, end = c_function_span(source, name)
    assert not re.search(
        r"^\s*#\s*(?:if|ifdef|ifndef|elif|else|endif)\b",
        source[start:end], re.MULTILINE,
    ), f"关键函数被条件编译: {name}"


def require_unconditional(source: str, name: str, fragment: str) -> None:
    start, end = c_function_span(source, name)
    body = source[start:end]
    clean = c_active_source(source)[start:end]
    positions = [match.start() for match in re.finditer(re.escape(fragment), clean)]
    assert positions, f"关键调用缺失: {fragment}"
    directives = re.compile(
        r"^\s*#\s*(if|ifdef|ifndef|endif)\b", re.MULTILINE
    )
    for position in positions:
        depth = 0
        for directive in directives.finditer(body, 0, position):
            depth += -1 if directive.group(1) == "endif" else 1
        assert depth == 0, f"关键调用被条件编译: {fragment}"


def mutate_once(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, f"全局变异锚点不唯一: {old}"
    return source.replace(old, new, 1)


def expect_rejected(checker, mutant, label: str) -> None:
    try:
        checker(mutant)
    except AssertionError:
        return
    raise AssertionError(f"观测恢复约束未拒绝变异: {label}")


store = read("os/agent_observe_store.c")
store_header = read("os/agent_observe_store.h")
ledger = read("os/agent_observe_ledger.c")
capacity = read("os/agent_observe_capacity.c")
recovery = read("os/agent_observe_recovery.c")
metadata_store = read("os/agent_metadata_store.c")
lease = read("os/agent_identity_lease.c")
timeline = read("os/agent_observe_timeline.c")
proc_source = read("os/proc.c")
core_source = read("os/agent_core.c")
kernel_contract_source = "\n".join(
    path.read_text(encoding="utf-8")
    for path in sorted((*ROOT.glob("*.h"), *(ROOT / "os").glob("*.[ch]")))
)


def validate_capacity(source: str) -> None:
    available = c_tokens(c_function(source, "agent_observe_slot_available"))
    assert token_count(available, ("return",)) == 2
    ordinary = (
        "slot", "<", "AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS", "&&",
        "disk", "->", "flags", "==", "0",
    )
    recovery_slot = (
        "slot", "==", "AGENT_OBSERVE_RECOVERY_SCOPE_SLOT", "&&",
    )
    sealed = ("disk", "->", "sealed", "&&")
    successor = (
        "AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR", "|",
        "AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED", ")", ")", "==",
        "AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR",
    )
    token_order(available, ordinary, recovery_slot, sealed, successor)

    snapshot = c_function(source, "agent_observe_capacity_snapshot")
    require(
        snapshot,
        "!workflow_lifecycle_active(slots[i].lifecycle)",
        "!workflow_lifecycle_closing(slots[i].lifecycle)",
        "!workflow_lifecycle_retiring(slots[i].lifecycle)",
        "(i == AGENT_OBSERVE_RECOVERY_SCOPE_SLOT) !=",
        "AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR",
    )
    admit = c_tokens(c_function(source, "agent_observe_capacity_admit"))
    assert token_count(
        admit,
        ("agent_observe_slot_available", "(", "class", ",", "i", ",", "&", "slots", "[", "i", "]"),
    ) == 1
    token_order(
        admit,
        ("state", "->", "phase", "!=", "AGENT_OBSERVE_SLOT_FREE"),
        ("agent_observe_slot_available", "("),
        ("state", "->", "phase", "=", "AGENT_OBSERVE_SLOT_ADMITTED"),
    )
    require(
        c_function(source, "agent_observe_capacity_admit"),
        "slot_recovery !=\n\t\t\t\t    (i == AGENT_OBSERVE_RECOVERY_SCOPE_SLOT)",
        "(recovery && !slot_recovery)",
    )
    pressure_source = c_function(source, "agent_observe_capacity_admission_status")
    pressure = c_tokens(pressure_source)
    assert token_count(pressure, ("agent_observe_slot_available", "(")) == 1
    require(
        pressure_source,
        "if (agent_observe_capacity_snapshot(slots, 0) < 0)",
        "agent_background_request();\n\t\treturn AGENT_STATUS_RETRY;",
    )
    token_order(
        pressure,
        ("agent_observe_capacity_snapshot", "("),
        ("agent_observe_slot_available", "(", "class", ",", "i"),
        ("return", "AGENT_STATUS_OK", ";"),
        ("class", "==", "AGENT_OBSERVE_CAPACITY_RECOVERY", "?"),
        ("i", "==", "AGENT_OBSERVE_RECOVERY_SCOPE_SLOT", ":"),
        ("i", "<", "AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS"),
        ("agent_observe_slot_reaping", "("),
        ("return", "AGENT_STATUS_RETRY", ";"),
        ("return", "AGENT_STATUS_NO_SPACE", ";"),
    )


def validate_workflow_preflight(source: str) -> None:
    body = c_tokens(c_function(source, "agent_workflow_admission_status"))
    token_order(
        body,
        ("agent_identity_authority_check", "(", "p", ",", "role", ")"),
        ("exec_policy_process_bootstrap", "(", "p", ")"),
        ("vfs_scope_fresh_admission_status", "(", ")"),
        ("agent_observe_capacity_admission_status", "("),
        ("role", "==", "AGENT_ROLE_RECOVERY", "?", "AGENT_OBSERVE_CAPACITY_RECOVERY", ":", "AGENT_OBSERVE_CAPACITY_ORDINARY"),
    )


def validate_recovery_capacity(source: str) -> None:
    body = c_tokens(c_function(source, "agent_make_role"))
    assert token_count(body, ("return", "0", ";")) == 1
    token_order(
        body,
        ("recovery_bound", "=", "agent_observe_recovery_bind", "("),
        ("p", "->", "agent_role", "==", "AGENT_ROLE_RECOVERY", "&&", "recovery_bound", "<=", "0"),
        ("goto", "fail", ";"),
        ("agent_observe_capacity_admit", "("),
        ("p", "->", "agent_role", "==", "AGENT_ROLE_RECOVERY", "?", "AGENT_OBSERVE_CAPACITY_RECOVERY"),
        ("capacity_reserved", "<", "0"),
        ("goto", "fail", ";"),
        ("return", "0", ";"),
        ("fail", ":"),
        ("recovery_bound", ">", "0"),
        ("agent_observe_recovery_unbind_proc", "(", "p", ")"),
        ("capacity_reserved", ">", "0"),
        ("agent_observe_capacity_release", "("),
    )
    forbid_conditionals(source, "agent_make_role")


def validate_recovery_binding(source: str) -> None:
    bind = c_tokens(c_function(source, "agent_observe_recovery_bind"))
    token_order(
        bind,
        ("p", "==", "0", "||", "factory", "==", "0"),
        ("p", "->", "agent_role", "!=", "AGENT_ROLE_RECOVERY"),
        ("factory", "->", "is_agent", "||", "!", "factory", "->", "resource_domain_admin"),
        ("!", "exec_policy_process_bootstrap", "(", "factory", ")"),
        ("!", "exec_policy_process_bootstrap", "(", "p", ")"),
        ("p", "->", "agent_control_id", "==", "0"),
        ("!", "vfs_proc_lifecycle_active", "(", "p", ")"),
        ("agent_observe_recovery_binding", ".", "control_id", "!=", "0"),
        ("return", "-", "1", ";"),
        ("agent_observe_recovery_binding", ".", "control_id", "=", "p", "->", "agent_control_id"),
        ("agent_observe_recovery_binding", ".", "lifecycle", "=", "vfs_proc_lifecycle", "(", "p", ")"),
    )
    authorized = c_tokens(c_function(source, "agent_observe_recovery_authorized"))
    token_order(
        authorized,
        ("p", "!=", "0", "&&", "proc_teardown_live", "(", "p", ")"),
        ("p", "->", "agent_role", "==", "AGENT_ROLE_RECOVERY"),
        ("p", "->", "agent_control_id", "==", "agent_observe_recovery_binding", ".", "control_id"),
        ("workflow_lifecycle_key_equal", "(", "vfs_proc_lifecycle", "(", "p", ")"),
    )
    forbid_conditionals(source, "agent_observe_recovery_bind")
    forbid_conditionals(source, "agent_observe_recovery_authorized")


require(
    store_header,
    "#define AGENT_OBSERVE_RESERVED_SCOPE_SLOTS 1U",
    "(WORKFLOW_LIFECYCLE_MAX_ACTIVE + AGENT_OBSERVE_RESERVED_SCOPE_SLOTS)",
    "(AGENT_OBSERVE_CHECKPOINT_SCOPES - AGENT_OBSERVE_RESERVED_SCOPE_SLOTS)",
    "#define AGENT_OBSERVE_RECOVERY_SCOPE_SLOT AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS",
)
validate_capacity(capacity)
validate_workflow_preflight(core_source)
token_order(
    c_tokens(c_function(core_source, "sys_agent_workflow_create")),
    ("agent_workflow_admission_status", "(", "curr_proc", "(", ")", ",", "role", ")"),
    ("agent_workflow_create_proc", "(", "role", ")"),
)
validate_recovery_capacity(core_source)
validate_recovery_binding(recovery)
forbid_macro(
    kernel_contract_source,
    "agent_observe_recovery_bind",
    "agent_observe_capacity_tick",
    "agent_observe_capacity_recover_reap",
)
expect_rejected(
    validate_capacity,
    mutate_function(
        capacity,
        "agent_observe_slot_available",
        "slot < AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS",
        "slot < AGENT_OBSERVE_CHECKPOINT_SCOPES",
    ),
    "普通准入侵占 Recovery 保留槽",
)
expect_rejected(
    validate_capacity,
    mutate_function(
        capacity,
        "agent_observe_slot_available",
        "{\n\tif (class == AGENT_OBSERVE_CAPACITY_ORDINARY)",
        "{\n\treturn 1;\n\tif (class == AGENT_OBSERVE_CAPACITY_ORDINARY)",
    ),
    "容量分类器提前无条件接纳",
)
expect_rejected(
    validate_workflow_preflight,
    mutate_function(
        core_source,
        "agent_workflow_admission_status",
        "AGENT_OBSERVE_CAPACITY_ORDINARY",
        "AGENT_OBSERVE_CAPACITY_RECOVERY",
    ),
    "普通工作流预检使用 Recovery 容量类别",
)
for old, new, label in (
    ("p->agent_role == AGENT_ROLE_RECOVERY && recovery_bound <= 0", "0", "Recovery 绑定失败仍被接纳"),
    ("p->agent_role == AGENT_ROLE_RECOVERY ?", "recovery_bound > 0 ?", "Recovery 容量类别依赖失败的绑定结果"),
    ("if (capacity_reserved < 0)\n\t\tgoto fail;", "", "观测容量接纳失败仍发布 Agent"),
):
    expect_rejected(validate_recovery_capacity, mutate_function(core_source, "agent_make_role", old, new), label)
expect_rejected(
    validate_recovery_capacity,
    mutate_function(
        core_source,
        "agent_make_role",
        "{\n\tconst struct agent_role_policy",
        "{\n\tif (p != 0)\n\t\treturn 0;\n\tconst struct agent_role_policy",
    ),
    "角色创建提前绕过真实接纳",
)
for function, old, new, label in (
    (
        "agent_observe_recovery_bind",
        "\tif (agent_observe_recovery_binding.control_id != 0)\n\t\treturn -1;\n",
        "",
        "Recovery 绑定覆盖已有控制者",
    ),
    (
        "agent_observe_recovery_authorized",
        "p->agent_control_id == agent_observe_recovery_binding.control_id",
        "p->agent_control_id != 0",
        "Recovery 授权未绑定精确控制者",
    ),
):
    expect_rejected(validate_recovery_binding, mutate_function(recovery, function, old, new), label)
expect_rejected(
    validate_recovery_capacity,
    mutate_function(
        core_source,
        "agent_make_role",
        "\tif (recovery_bound > 0)\n\t\tagent_observe_recovery_unbind_proc(p);\n",
        "",
    ),
    "角色创建失败遗留 Recovery 绑定",
)
for macro, replacement in (
    ("agent_observe_recovery_bind", "1"),
    ("agent_observe_capacity_tick", "((void)0)"),
    ("agent_observe_capacity_recover_reap", "0"),
):
    expect_rejected(
        lambda source, name=macro: forbid_macro(source, name),
        kernel_contract_source + f"\n#define {macro}(...) {replacement}\n",
        f"头文件宏伪造关键调用 {macro}",
    )
expect_rejected(
    validate_capacity,
    mutate_function(
        capacity,
        "agent_observe_slot_available",
        "disk->sealed &&",
        "1 &&",
    ),
    "Recovery 继任者覆盖未密封证据",
)

USED = 1
RECOVERY_SUCCESSOR = 2
REAP_AUTHORIZED = 4


def capacity_model(slots, recovery_class: bool):
    candidates = [4] if recovery_class else range(4)
    for index in candidates:
        state, flags = slots[index]
        if state == "empty":
            return index
        if recovery_class and state == "sealed" and flags == USED | RECOVERY_SUCCESSOR:
            return index
    return None


# 四个普通槽加一个 Recovery 保留槽；只有密封且仅带继任授权的证据可被替换。
five_slots = [("sealed", USED)] * 4 + [("sealed", USED | RECOVERY_SUCCESSOR)]
assert capacity_model(five_slots, False) is None
assert capacity_model(five_slots, True) == 4
assert capacity_model(five_slots[:4] + [("active", USED | RECOVERY_SUCCESSOR)], True) is None
assert capacity_model(
    five_slots[:4] + [("sealed", USED | RECOVERY_SUCCESSOR | REAP_AUTHORIZED)], True
) is None


def validate_reap(source: str) -> None:
    begin = c_function(source, "agent_observe_capacity_reap_begin")
    require(
        begin,
        "!slots[exact_slot].sealed",
        "AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED",
        "state->phase = AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING",
        "current_admission ?\n\t\tscope_id : VFS_SCOPE_SYSTEM",
        "state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING",
        "state->slot_or_persist_scope = scope_id",
    )
    replicated = c_function(source, "agent_observe_reap_replicated")
    text_order(
        replicated,
        "agent_durable_section_replicated(",
        "state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING",
        "state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING",
        "state->slot_or_persist_scope = VFS_SCOPE_SYSTEM",
        "state->phase == AGENT_OBSERVE_SLOT_ERASE_PENDING",
        "state->phase = AGENT_OBSERVE_SLOT_DONE",
    )
    require(replicated, "if (state->detail.reap.token == 0)\n\t\t\tmemset(state, 0, sizeof(*state))")

    maintain = c_tokens(c_function(source, "agent_observe_capacity_maintain"))
    assert token_count(maintain, ("agent_observe_reap_replicated", "(")) == 1
    assert token_count(maintain, ("agent_observe_reap_start", "(")) == 1
    token_order(
        maintain,
        ("int", "started", "=", "0", ";"),
        ("state", "->", "detail", ".", "reap", ".", "target", "!=", "0"),
        ("agent_observe_reap_replicated", "(", "state", ")"),
        ("continue", ";"),
        ("!", "started", "&&", "agent_observe_reap_start", "("),
    )
    recover = c_function(source, "agent_observe_capacity_recover_reap")
    require(
        recover,
        "state->phase >= AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING",
        "state->phase <= AGENT_OBSERVE_SLOT_DONE",
        "if (same && state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING)",
        "state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING",
        "state->slot_or_persist_scope = VFS_SCOPE_SYSTEM",
        "return same ? 0 : -1",
    )
    tick = c_tokens(c_function(source, "agent_observe_capacity_tick"))
    token_order(
        tick,
        ("for", "(", "uint", "i", "=", "0", ";", "i", "<", "AGENT_OBSERVE_CHECKPOINT_SCOPES"),
        ("agent_observe_slot_reaping", "(", "&", "agent_observe_slots", "[", "i", "]"),
        ("agent_background_request", "(", ")"),
        ("break", ";"),
        ("intr_restore", "(", "enabled", ")"),
    )


validate_reap(capacity)
tick_core = c_tokens(c_function(core_source, "agent_core_tick"))
assert token_count(tick_core, ("agent_observe_capacity_tick", "(", ")")) == 1
forbid_macro(core_source, "agent_observe_capacity_tick")
forbid_conditionals(core_source, "agent_core_tick")
update_scope = c_function(store, "agent_observe_store_update_scope")
text_order(
    update_scope,
    "agent_observe_capacity_reap_action(",
    "action == AGENT_OBSERVE_REAP_AUTHORIZE",
    "scope->used |= AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED",
    "action == AGENT_OBSERVE_REAP_ERASE",
    "memset(scope, 0, sizeof(*scope))",
)
assert "agent_observe_capacity_replicated(scope_id)" in c_function(
    store, "agent_observe_store_replicated_scope"
)
store_recover = c_tokens(c_function(store, "agent_observe_store_recover"))
token_order(
    store_recover,
    ("scope", "->", "used", "&", "AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED"),
    ("agent_observe_capacity_recover_reap", "(", "i", ",", "scope", "->", "scope_id", ",", "key"),
    ("return", "-", "1", ";"),
    ("continue", ";"),
)
forbid_macro(store, "agent_observe_capacity_recover_reap")
forbid_conditionals(store, "agent_observe_store_recover")
expect_rejected(
    validate_reap,
    mutate_function(
        capacity,
        "agent_observe_capacity_maintain",
        "\t\t\t(void)agent_observe_reap_replicated(state);\n\t\t\tcontinue;",
        "\t\t\t(void)agent_observe_reap_replicated(state);\n\t\t\tbreak;",
    ),
    "丢失回调后的自校准只检查第一个槽",
)
expect_rejected(
    validate_reap,
    mutate_function(
        capacity,
        "agent_observe_reap_replicated",
        "state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING;",
        "state->phase = AGENT_OBSERVE_SLOT_DONE;",
    ),
    "REAP 跳过持久授权与系统擦除之间的阶段",
)
expect_rejected(
    lambda source: token_order(
        c_tokens(c_function(source, "agent_observe_store_recover")),
        ("scope", "->", "used", "&", "AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED"),
        ("agent_observe_capacity_recover_reap", "("),
        ("return", "-", "1", ";"),
        ("continue", ";"),
    ),
    mutate_function(
        store,
        "agent_observe_store_recover",
        "\t\t\tif (agent_observe_capacity_recover_reap(\n"
        "\t\t\t\t    i, scope->scope_id, key) < 0)\n"
        "\t\t\t\treturn -1;\n",
        "",
    ),
    "恢复时遗失已授权 REAP",
)
expect_rejected(
    lambda source: (
        forbid_macro(source, "agent_observe_capacity_tick"),
        token_pos(
            c_tokens(c_function(source, "agent_core_tick")),
            ("agent_observe_capacity_tick", "(", ")"),
        ),
    ),
    core_source.replace(
        "agent_observe_capacity_tick();",
        "/* agent_observe_capacity_tick(); */",
        1,
    ),
    "容量回收 tick 仅留在注释",
)


def validate_reap_delivery(store_source: str, recovery_source: str) -> None:
    begin = c_tokens(c_function(store_source, "agent_obsstore_recovery_reap"))
    token_order(
        begin,
        ("agent_observe_capacity_reap_begin", "("),
        ("return", "agent_observe_capacity_reap_resume", "("),
        ("==", "1", "?", "0", ":", "-", "1", ";"),
    )
    resume = c_tokens(c_function(store_source, "agent_obsstore_recovery_reap_resume"))
    assert token_count(resume, ("agent_observe_capacity_reap_resume", "(")) == 1
    forbid_conditionals(store_source, "agent_obsstore_recovery_reap")
    forbid_conditionals(store_source, "agent_obsstore_recovery_reap_resume")
    syscall = c_tokens(c_function(recovery_source, "sys_agent_observe_recovery"))
    assert token_count(syscall, ("agent_obsstore_reap_consume", "(")) == 1
    token_order(
        syscall,
        ("agent_obsstore_reap_query", "("),
        ("reap_delivery", "=", "status", "==", "AGENT_STATUS_OK"),
        ("request", ".", "completion_token", "==", "0", "&&", "(",
         "reap_resume", "=", "agent_obsstore_recovery_reap_resume", "("),
        ("reap_resume", ">", "0", "?", "AGENT_STATUS_OK"),
        ("agent_observe_recovery_find_scope", "("),
        ("request", ".", "returned", "=", "returned", ";"),
        ("copyout", "(", "p", "->", "pagetable", ",", "requestaddr", ",", "(", "char", "*", ")", "&", "request"),
        ("reap_delivery", "&&", "agent_obsstore_reap_consume", "(", "&", "reap_cookie", ")"),
        ("agent_metadata_txn_unlock", "(", ")"),
    )
    for fragment in (
        "agent_obsstore_recovery_reap_resume(",
        "copyout(p->pagetable, requestaddr, (char *)&request,",
        "agent_obsstore_reap_consume(&reap_cookie)",
    ):
        require_unconditional(recovery_source, "sys_agent_observe_recovery", fragment)


validate_reap_delivery(store, recovery)
expect_rejected(
    lambda mutant: validate_reap_delivery(store, mutant),
    mutate_function(
        recovery,
        "sys_agent_observe_recovery",
        "(reap_resume = agent_obsstore_recovery_reap_resume(",
        "(0 && (reap_resume = agent_obsstore_recovery_reap_resume(",
    ),
    "Recovery REAP 不再重放未完成任务",
)
expect_rejected(
    lambda mutant: validate_reap_delivery(store, mutant),
    mutate_function(
        recovery,
        "sys_agent_observe_recovery",
        "(reap_resume = agent_obsstore_recovery_reap_resume(",
        "#ifdef NEVER_DEFINED\n\t\t   (reap_resume = agent_obsstore_recovery_reap_resume(",
    ).replace("\n\t\tstatus = reap_resume > 0", "\n#endif\n\t\tstatus = reap_resume > 0", 1),
    "Recovery REAP 重放被未定义条件包裹",
)
expect_rejected(
    lambda mutant: validate_reap_delivery(mutant, recovery),
    mutate_function(
        store,
        "agent_obsstore_recovery_reap",
        "return agent_observe_capacity_reap_resume(",
        "return 1 || agent_observe_capacity_reap_resume(",
    ),
    "Recovery REAP 包装器固定成功",
)
expect_rejected(
    lambda mutant: validate_reap_delivery(store, mutant),
    mutate_function(
        recovery,
        "sys_agent_observe_recovery",
        "\trequest.returned = returned;",
        "\tif (reap_delivery)\n\t\t(void)agent_obsstore_reap_consume(&reap_cookie);\n"
        "\trequest.returned = returned;",
    ),
    "Recovery REAP 在响应交付前消费",
)


def reap_maintain_model(states: list[dict], durable: set[tuple[int, int]]) -> None:
    """通知可丢；维护轮询持久目标，并且每轮最多发布一个新目标。"""
    started = False
    for state in states:
        if state["phase"] not in ("authorize", "erase"):
            continue
        target = state["target"]
        if target:
            if (state["persist_scope"], target) in durable:
                if state["phase"] == "authorize":
                    state.update(phase="erase", persist_scope=0, target=0)
                else:
                    state.update(phase="done", target=0)
            continue
        if not started:
            state["target"] = state["next_target"]
            started = True


# 两个通知同时丢失时仍须在一轮内全部自校准，随后串行发布 SYSTEM 擦除。
lost = [
    {"phase": "authorize", "persist_scope": 10 + i, "target": 20 + i, "next_target": 30 + i}
    for i in range(2)
]
reap_maintain_model(lost, {(10, 20), (11, 21)})
assert [state["phase"] for state in lost] == ["erase", "erase"]
reap_maintain_model(lost, set())
assert [state["target"] for state in lost] == [30, 0]
reap_maintain_model(lost, {(0, 30)})
assert lost[0]["phase"] == "done" and lost[1]["phase"] == "erase"


def validate_sparse_chain(store_source: str, ledger_source: str) -> None:
    validate = c_function(store_source, "agent_observe_store_validate")
    text_order(
        validate,
        "scope->admission_drops >",
        "successful_records =",
        "hashed_omitted = successful_records - scope->record_count",
        "scope->record_count == 0",
        "agent_observe_checkpoint_entry_validate(",
        "scope->ledger_hash !=",
        "hashed_omitted == 0 ?",
    )
    require(
        validate,
        "scope->total_records - scope->admission_drops",
        "(gap || scope->records[0].record.prev_hash != 0)",
        "prior->records[pj].record.sequence ==",
    )
    entry = c_function(ledger_source, "agent_observe_checkpoint_entry_validate")
    text_order(
        entry,
        "record->prev_hash == prior->record.record_hash",
        "AGENT_OBSERVE_LINK_PREV_RETAINED",
        "if ((index == 0 && record->prev_hash != 0)",
        "*gap = 1",
    )
    require(
        entry,
        "entry->link_flags & ~AGENT_OBSERVE_LINK_FLAGS_ALL",
        "AGENT_OBSERVE_LINK_LATEST_TAIL",
        "!!(entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED) !=\n\t\t     direct",
        "record->record_hash != agent_observe_checkpoint_record_hash(record)",
    )
    capture = c_function(ledger_source, "agent_observe_checkpoint_capture_scope")
    text_order(
        capture,
        "saved->admission_drops = state->admission_drops",
        "agent_observe_checkpoint_select(state, selected",
        "entry->link_flags |= AGENT_OBSERVE_LINK_LATEST_TAIL",
        "record->prev_hash ==",
        "entry->link_flags |= AGENT_OBSERVE_LINK_PREV_RETAINED",
    )


validate_sparse_chain(store, ledger)
expect_rejected(
    lambda mutant: validate_sparse_chain(mutant, ledger),
    mutate_function(
        store,
        "agent_observe_store_validate",
        "scope->total_records - scope->admission_drops",
        "scope->total_records",
    ),
    "稀疏链把准入丢失误算为已哈希缺口",
)
expect_rejected(
    lambda mutant: validate_sparse_chain(store, mutant),
    mutate_function(
        ledger,
        "agent_observe_checkpoint_entry_validate",
        "!!(entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED) !=\n\t\t     direct",
        "!!(entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED) == direct",
    ),
    "稀疏链侧带不再认证直接前驱",
)

LINK_PREV = 1
LINK_TAIL = 2


def sparse_chain_model(total: int, dropped: int, records, ledger_hash: int) -> bool:
    count = len(records)
    if total == 0 or count > 6 or total < count or dropped > total - count:
        return False
    successful = total - dropped
    if count == 0:
        return successful == 0 and ledger_hash == 0
    if successful < count or ledger_hash == 0:
        return False
    gap = False
    tail_start = max(0, count - 4)
    for index, (previous, current, flags) in enumerate(records):
        if flags & ~(LINK_PREV | LINK_TAIL) or current == 0:
            return False
        if bool(flags & LINK_TAIL) != (index >= tail_start):
            return False
        direct = index > 0 and previous == records[index - 1][1]
        if (index == 0 and flags & LINK_PREV) or (index > 0 and bool(flags & LINK_PREV) != direct):
            return False
        if index > 0 and previous == 0:
            return False
        gap |= (index == 0 and previous != 0) or (index > 0 and not direct)
    omitted = successful - count
    return records[-1][1] == ledger_hash and (gap if omitted else not gap and records[0][0] == 0)


full = [(0, 11, LINK_TAIL), (11, 22, LINK_PREV | LINK_TAIL), (22, 33, LINK_PREV | LINK_TAIL)]
sparse = [
    (0, 11, 0),
    (11, 22, LINK_PREV),
    (99, 33, LINK_TAIL),
    (33, 44, LINK_PREV | LINK_TAIL),
    (44, 55, LINK_PREV | LINK_TAIL),
    (55, 66, LINK_PREV | LINK_TAIL),
]
assert sparse_chain_model(3, 0, full, 33)
assert sparse_chain_model(5, 2, full, 33)
assert sparse_chain_model(4, 4, [], 0)
assert sparse_chain_model(8, 0, sparse, 66)
assert not sparse_chain_model(8, 0, full, 33)
assert not sparse_chain_model(6, 0, sparse, 66)


def validate_replication(source: str) -> None:
    target = c_function(source, "agent_meta_persist_target_locked")
    text_order(
        target,
        "agent_meta_store_set_replicated_generation(0)",
        "agent_meta_persist.phase = AGENT_META_PERSIST_INVALIDATE",
    )
    require(
        c_function(source, "agent_file_load_snapshot"),
        "repair_mode == AGENT_META_REPAIR_NONE ? selected_generation : 0",
    )
    active = c_function(source, "agent_meta_durable_active_replicated")
    require(
        active,
        "generation != 0",
        "generation == agent_meta_store_generation",
        "generation == agent_meta_store_replicated_generation",
    )
    commit = c_function(source, "agent_meta_persist_step_locked")
    text_order(
        commit,
        "state->phase == AGENT_META_PERSIST_COMMIT",
        "if (!state->mirroring)",
        "uint64 replicated_generation = state->expected_generation",
        "agent_meta_store_set_replicated_generation(replicated_generation)",
        "agent_meta_persist_release_locked(0)",
    )


validate_replication(metadata_store)
expect_rejected(
    validate_replication,
    mutate_function(
        metadata_store,
        "agent_meta_persist_target_locked",
        "agent_meta_store_set_replicated_generation(0);",
        "(void)agent_meta_store_replicated_generation;",
    ),
    "覆写目标保留旧复制证明",
)

# primary 发布不能替代 mirror 提交，修复态和下一次覆写也必须撤销证明。
active_generation = replicated_generation = 7
assert active_generation == replicated_generation
replicated_generation = 0
active_generation = 8
assert active_generation != replicated_generation
replicated_generation = 8
assert active_generation == replicated_generation
replicated_generation = 0
assert active_generation != replicated_generation


def validate_receipt(store_source: str, ledger_source: str) -> None:
    durable = c_tokens(c_function(store_source, "agent_obsstore_receipt_record_status"))
    token_order(
        durable,
        ("replicated", "=", "agent_obsstore_receipt_replicated", "("),
        ("enabled", "=", "intr_save", "(", ")"),
        ("agent_durable_section_active_view", "("),
        ("replicated", "=", "agent_durable_section_active_replicated", "(", "generation", ")"),
        ("workflow_lifecycle_id", "==", "lifecycle", ".", "id"),
        ("sequence", "==", "sequence"),
        ("record_hash", "==", "record_hash"),
        ("receipt_id", "==", "receipt_id"),
        ("result", "=", "1", ";"),
        ("intr_restore", "(", "enabled", ")"),
    )
    assert token_count(durable, ("agent_durable_section_active_view", "(")) == 1
    assert token_count(durable, ("agent_durable_section_active_read", "(")) == 0
    status = c_tokens(c_function(ledger_source, "agent_observe_receipt_status"))
    token_order(
        status,
        ("*", "receipt_id", "=", "0", ";"),
        ("*", "durability", "=", "AGENT_AUDIT_DURABILITY_NOT_FOUND", ";"),
        ("supplied_receipt", "!=", "0", "&&", "agent_obsstore_receipt_record_status", "("),
        ("supplied_receipt", ",", "0", ")", ">", "0"),
        ("*", "receipt_id", "=", "supplied_receipt", ";"),
        ("*", "durability", "=", "AGENT_AUDIT_DURABILITY_DURABLE", ";"),
        ("persisted", "=", "agent_obsstore_receipt_record_status", "("),
        ("persisted", ">", "0", "?", "AGENT_AUDIT_DURABILITY_DURABLE"),
    )
    snapshot = c_function(ledger_source, "agent_observe_receipt_snapshot")
    require(
        snapshot,
        "supplied_receipt == 0 ? AGENT_STATUS_NOT_FOUND :\n\t\t\t\t\t      AGENT_STATUS_STALE",
    )


validate_receipt(store, ledger)
expect_rejected(
    lambda mutant: validate_receipt(mutant, ledger),
    mutate_function(
        store,
        "agent_obsstore_receipt_record_status",
        "entry->record.record_hash == record_hash",
        "entry->record.record_hash != record_hash",
    ),
    "持久回执接受不同记录哈希",
)
expect_rejected(
    lambda mutant: validate_receipt(store, mutant),
    mutate_function(
        ledger,
        "agent_observe_receipt_status",
        "persisted > 0 ? AGENT_AUDIT_DURABILITY_DURABLE",
        "persisted >= 0 ? AGENT_AUDIT_DURABILITY_DURABLE",
    ),
    "待提交回执被提升为已持久",
)


def validate_recovery_receipt(store_source: str, recovery_source: str) -> None:
    record = c_tokens(c_function(store_source, "agent_obsstore_snapshot_record"))
    token_order(
        record,
        ("agent_observe_active_copy", "("),
        ("observed_generation", "!=", "bank_generation"),
        ("entry", ".", "receipt_id", "==", "0"),
        ("agent_observe_checkpoint_record_hash", "("),
        ("view", "->", "record", "=", "entry", ".", "record", ";"),
    )
    recovery_body = c_tokens(c_function(recovery_source, "sys_agent_observe_recovery"))
    token_order(
        recovery_body,
        ("agent_obsstore_snapshot_record", "("),
        ("result", "==", "AGENT_OBSSTORE_SNAPSHOT_RETRY"),
        ("result", "!=", "AGENT_OBSSTORE_SNAPSHOT_READY"),
        ("tail", ".", "receipt_id", "=", "agent_observe_recovery_entry", ".", "receipt_id"),
        ("tail", ".", "bank_generation", "=", "bank_generation"),
        ("tail", ".", "durability", "=", "AGENT_AUDIT_DURABILITY_DURABLE"),
        ("agent_obsstore_snapshot_confirm", "("),
    )


validate_recovery_receipt(store, recovery)
observe_abi = read("agent_observe_abi.h")
require(
    observe_abi,
    "#define AGENT_OBSERVE_RECOVERY_VERSION_V1 1U",
    "#define AGENT_OBSERVE_RECOVERY_VERSION    2U",
    "unsigned long long receipt_id;",
    "unsigned long long bank_generation;",
    "unsigned int durability;",
)
expect_rejected(
    lambda mutant: validate_recovery_receipt(store, mutant),
    mutate_function(
        recovery,
        "sys_agent_observe_recovery",
        "tail.receipt_id =\n\t\t\t\t\t\t\tagent_observe_recovery_entry.receipt_id;",
        "tail.receipt_id = 0;",
    ),
    "Recovery 读取丢失精确回执身份",
)


def receipt_model(active: int, replicated: int, entry: tuple, wanted: tuple) -> bool:
    """只有复制代次中的精确生命周期、序列、哈希和回执四元组可证明持久。"""
    return active != 0 and active == replicated and entry == wanted


exact = ((7, 3), 41, 0xAA, 0xBB)
assert receipt_model(9, 9, exact, exact)
assert not receipt_model(9, 0, exact, exact)
assert not receipt_model(9, 8, exact, exact)
for index in range(4):
    changed = list(exact)
    changed[index] = (8, 3) if index == 0 else changed[index] + 1
    assert not receipt_model(9, 9, exact, tuple(changed))


def validate_lease(source: str) -> None:
    progress = c_tokens(c_function(source, "agent_identity_lease_progress"))
    token_order(
        progress,
        ("agent_identity_lease_prepare_locked", "(", ")"),
        ("intr_restore", "(", "enabled", ")"),
        ("result", "=", "persist", "("),
        ("if", "(", "result", ">", "0", ")"),
        ("agent_identity_lease_publish_locked", "(", ")"),
    )
    assert token_count(progress, ("persist", "(", "&", "serial")) == 1
    assert token_count(progress, ("agent_identity_lease_publish_locked", "(")) == 1
    contains = c_tokens(c_function(source, "agent_identity_lease_allocator_contains"))
    assert token_count(contains, ("admission_ready", "&&", "end", "!=", "0")) == 1
    assert token_count(contains, ("id", "<", "end")) == 1
    for name in ("agent_identity_lease_allocator_renew", "agent_identity_lease_lifecycle_renew"):
        renew = c_tokens(c_function(source, name))
        assert token_count(renew, ("renew_requested", "=", "1", ";")) == 1
        assert token_count(renew, ("agent_identity_lease_progress", "(")) == 0
        assert token_count(renew, ("agent_background_request", "(")) == 0
    assert "agent_background_request" not in source
    assert '#include "proc.h"' not in source


validate_lease(lease)
expect_rejected(
    validate_lease,
    mutate_function(
        lease,
        "agent_identity_lease_progress",
        "if (result > 0)",
        "if (result >= 0)",
    ),
    "未复制的租约发布了身份区间",
)
expect_rejected(
    validate_lease,
    mutate_function(
        lease,
        "agent_identity_lease_allocator_contains",
        "admission_ready &&\n\t\t    end != 0 && id < end",
        "admission_ready && id < end",
    ),
    "零上界租约未失效关闭",
)

# 租约只在复制成功后发布，零上界永远不包含新身份。
assert not (0 > 0)
assert 1 > 0
assert not (True and 0 != 0 and 0 < 0)


def validate_timeline_wait(source: str) -> None:
    wait = c_tokens(c_function(source, "agent_timeline_wait_for_match"))
    token_order(
        wait,
        ("agent_timeline_export", "(", "p", ",", "filter", ",", "0", ",", "0", ",", "&", "scan_epoch"),
        ("AGENT_OBSERVE_TEST_TIMELINE_WINDOW",),
        ("agent_timeline_wait_enqueue_atomic", "("),
    )
    assert token_count(wait, ("agent_observe_scope_epoch", "(")) == 0
    enqueue_body = c_function(source, "agent_timeline_wait_enqueue_atomic")
    require(
        enqueue_body,
        "if (current_epoch != scan_epoch)",
        "if (expired && *deadline_rescan_used)",
    )
    enqueue = c_tokens(enqueue_body)
    assert token_count(enqueue, ("intr_save", "(", ")")) == 1
    assert token_count(enqueue, ("agent_timeline_export", "(")) == 0
    token_order(
        enqueue,
        ("enabled", "=", "intr_save", "(", ")"),
        ("current_epoch", "=", "agent_observe_scope_epoch", "(", "scope_id", ")"),
        ("current_epoch", "!=", "scan_epoch"),
        ("expired", "&&", "*", "deadline_rescan_used"),
        ("*", "deadline_rescan_used", "=", "1", ";"),
        ("intr_restore", "(", "enabled", ")", ";", "return", "AGENT_TIMELINE_WAIT_RETRY"),
        ("if", "(", "expired", ")", "goto", "timeline_timeout"),
        ("memmove", "(", "&", "state", "->", "filter"),
        ("agent_observe_timeline_waiter_publish", "(", "t", ",", "state", ")"),
        ("wait_queue_sleep_key_irq", "(", "&", "p", "->", "agent_timeline_waiters", ",", "state", "->", "thread_generation"),
        ("agent_observe_timeline_waiter_unpublish", "(", "t", ",", "state", ")"),
        ("intr_restore", "(", "enabled", ")"),
        ("timeline_timeout", ":"),
        ("intr_restore", "(", "enabled", ")", ";", "return", "AGENT_STATUS_TIMEOUT"),
    )
    export = c_function(source, "agent_timeline_export")
    text_order(
        export,
        "for (;;) {",
        "candidate_epoch = scan_epoch_out != 0 ?",
        "context_visible =",
        "scan_visible <= reserved",
        "*scan_epoch_out = candidate_epoch",
        "agent_observe_query_reserve_to",
        "span_id = p->agent_current_span_id",
    )
    publish = c_tokens(c_function(ledger, "agent_observe_timeline_publish_locked"))
    token_order(
        publish,
        ("observe_epoch", "=", "agent_observe_scope_epoch_advance_locked", "(", "scope_id", ")"),
        ("agent_observe_timeline_match", "("),
        ("agent_observe_timeline_waiter_wake", "("),
    )


validate_timeline_wait(timeline)
expect_rejected(
    validate_timeline_wait,
    mutate_function(
        timeline,
        "agent_timeline_wait_enqueue_atomic",
        "if (current_epoch != scan_epoch)",
        "if (0 && current_epoch != scan_epoch)",
    ),
    "等待入队前的最终代次复检被关闭",
)
expect_rejected(
    validate_timeline_wait,
    mutate_function(
        timeline,
        "agent_timeline_wait_enqueue_atomic",
        "if (expired && *deadline_rescan_used)",
        "if (0 && expired && *deadline_rescan_used)",
    ),
    "截止期代次抖动可无限重试",
)
expect_rejected(
    validate_timeline_wait,
    mutate_function(
        timeline,
        "agent_timeline_wait_enqueue_atomic",
        "wait_queue_sleep_key_irq(\n\t\t&p->agent_timeline_waiters, state->thread_generation)",
        "wait_queue_sleep_irq(&p->agent_timeline_waiters)",
    ),
    "等待者丢失线程代次键",
)


def wait_window_model(publish_phase: str) -> str:
    epoch, record, queued, woken = 1, False, False, False

    def publish() -> None:
        nonlocal epoch, record, woken
        epoch += 1
        record = True
        woken |= queued

    if publish_phase == "before_snapshot":
        publish()
    scan_epoch = epoch
    if publish_phase == "after_snapshot":
        publish()
    matched = record
    if publish_phase == "export_miss":
        matched = False
        publish()
    if matched:
        return "matched"
    if publish_phase == "after_export":
        publish()
    if epoch != scan_epoch:
        return "retry"
    queued = True
    if publish_phase in ("atomic_window", "after_enqueue"):
        # 关中断窗口内的发布延迟到等待者完成发布后处理。
        publish()
    return "woken" if woken else "sleeping"


assert wait_window_model("before_snapshot") == "matched"
assert wait_window_model("after_snapshot") == "matched"
assert wait_window_model("export_miss") == "retry"
assert wait_window_model("after_export") == "retry"
assert wait_window_model("atomic_window") == "woken"
assert wait_window_model("after_enqueue") == "woken"
assert wait_window_model("none") == "sleeping"


def validate_profile_isolation(parts: tuple[str, str, str, str]) -> None:
    makefile, recovery_source, owner, runner = parts
    assert owner.startswith('#include "agent_observe_test.h"\n#ifdef AGENT_OBSERVE_TEST_PROFILE\n')
    assert owner.rstrip().endswith("#endif")
    blocks = re.findall(
        r"#ifdef AGENT_OBSERVE_TEST_PROFILE\n(.*?)#endif",
        recovery_source,
        flags=re.DOTALL,
    )
    assert len(blocks) == 3
    assert "agent_observe_test_operation(" in blocks[0]
    assert "agent_observe_test_execute(" in blocks[1]
    unguarded = re.sub(
        r"#ifdef AGENT_OBSERVE_TEST_PROFILE\n.*?#endif",
        "",
        recovery_source,
        flags=re.DOTALL,
    )
    assert "agent_observe_test_operation(" not in unguarded
    assert "agent_observe_test_execute(" not in unguarded
    require(
        makefile,
        "C_SRCS := $(filter-out $K/agent_observe_test.c,$(C_SRCS))",
        "ifeq ($(AGENT_OBSERVE_TEST_PROFILE),1)\nCFLAGS += -DAGENT_OBSERVE_TEST_PROFILE",
        "AGENT_OBSERVE_TEST_PROFILE= \\",
    )
    require(
        runner,
        '"${TMPDIR_OBSERVE}/prod-build/os/agent_observe_store.o"',
        '"${TMPDIR_OBSERVE}/prod-build/os/agent_observe_recovery.o"',
        '"${TMPDIR_OBSERVE}/prod-build/os/agent_observe_timeline.o"',
        '"${TMPDIR_OBSERVE}/prod-build/os/agent_observe_test.o"',
        "test owner leaked into production build",
        "grep -q 'agent_observe_test_'",
    )


profile_parts = (
    read("Makefile"),
    recovery,
    read("os/agent_observe_test.c"),
    read("scripts/run-observe-recovery-tests.sh"),
)
validate_profile_isolation(profile_parts)
expect_rejected(
    validate_profile_isolation,
    (
        mutate_once(
            profile_parts[0],
            "C_SRCS := $(filter-out $K/agent_observe_test.c,$(C_SRCS))",
            "C_SRCS := $(C_SRCS)",
        ),
        *profile_parts[1:],
    ),
    "生产构建纳入观测测试所有者",
)

print("[observe-recovery-contract] exhaustion, hook and wait window: valid")
