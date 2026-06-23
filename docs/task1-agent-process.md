<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 任务一：Agent 进程创建与地址空间设计

本文是 [design.md](design.md) 的任务一细节附录，重点展开 Agent 进程生命周期和地址空间设计。总体架构、关键决策和质量要求以主设计文档为准。

## 目标

任务一的目标是在 xv6-riscv 中加入 Agent 进程机制，使内核能够区分普通进程和 Agent 进程，并为 Agent 进程提供独立的 Agent Context 用户虚拟地址区。

## 当前实现

当前实现包含三个系统调用：

| 系统调用 | 作用 |
| --- | --- |
| `agent_create()` | 与赛题 `sys_agent_create` 对应的创建入口，当前与 `agent_fork()` 行为一致 |
| `agent_fork()` | 创建一个子进程，并将子进程标记为 Agent 进程 |
| `agent_info(struct agent_info *)` | 查询当前进程的 Agent 状态、Agent ID、Agent Context、配额、Loop 状态和路径元信息 |

当前任务一能力由 `agentfinal` 和 `agentstress` 共同验证，覆盖 Agent 创建、Context 映射、生命周期和地址空间限制。

## 进程元数据

在 `struct proc` 中新增以下字段：

| 字段 | 说明 |
| --- | --- |
| `is_agent` | 是否为 Agent 进程 |
| `agent_type` | Agent 类型，普通进程为 `AGENT_TYPE_NONE`，Agent 进程为 `AGENT_TYPE_AGENT` |
| `agent_role` | 当前 Agent 角色；默认创建为 Sentinel。普通进程只能创建 Sentinel，或在没有存活 Orchestrator 时引导一个 Orchestrator；Recovery、Investigator 等工作 Agent 由 Orchestrator 创建。`agent_set_role()` 只能确认当前角色 |
| `agent_id` | Agent 进程 ID，启动周期内递增分配 |
| `agent_ctx_base` | Agent Context 用户虚拟地址起点 |
| `agent_ctx_size` | Agent Context 大小 |
| `heartbeat_interval` | Agent 心跳周期，`agent_heartbeat()` 可设置 |
| `resource_quota` | Agent Context Path 记录配额，当前为 128 条 |
| `loop_state` | Agent Loop 状态，支持 `IDLE`、`RUNNING`、`WAITING` |
| `context_path_count` | 当前有效 Context Path 记录数 |
| `context_path_capacity` | Context Path 最大记录数 |
| `context_path_head` | 下一条 Context Path 写入槽位 |
| `context_path_oldest` | 当前仍可查询的最早 Context Path 序号 |
| `context_path_latest` | 当前最新 Context Path 序号 |
| `context_path_dropped` | 因 FIFO 覆盖淘汰的历史记录数 |
| `context_path_rollback_count` | 成功回滚 Context Path 的次数 |
| `latest_response_offset` | 最近一次结构化响应在 Agent Context 中的偏移 |
| `records_offset` | Context Path 记录数组在 Agent Context 中的偏移 |

普通进程的 `is_agent` 为 0，`agent_id` 为 0，且不会安装 Agent metadata 或 Agent Context 特殊映射。当前严格验证程序还会让普通进程在未申请该地址的情况下尝试写入 Agent Context 地址，预期被内核 page fault 杀死，从而证明普通进程不能直接访问 Agent Context 特殊页。普通进程仍可按 xv6 规则使用普通用户地址空间；如果父进程堆已经越过 `AGENT_CONTEXT_BASE`，内核会拒绝从该父进程直接创建 Agent，避免子进程页表中普通堆页和 Agent Context 映射重叠。

## 地址空间设计

Agent Context 使用固定高地址用户虚拟区：

| 项目 | 值 |
| --- | --- |
| 起始地址 | `AGENT_CONTEXT_BASE = TRAPFRAME - AGENT_CONTEXT_SIZE` |
| 大小 | `AGENT_CONTEXT_SIZE = 4 * PGSIZE` |
| 当前实测大小 | 16384 字节 |
| 权限 | 用户态可读写，不可执行 |

该区域位于 `TRAPFRAME` 下方，仅 Agent 进程在创建或 exec 重建时安装 Agent Context 特殊映射。普通进程仍按普通 xv6 用户地址空间规则管理，不把这段虚拟地址全局保留为空洞。内核在 `struct proc` 中保存 4 个用户镜像页地址和 4 个 shadow 权威页地址，写 header、latest result 和 Context Path record 时先写内核 shadow 页，再同步到用户镜像页。Agent 进程执行 eager 或 lazy `sbrk` 增长堆时，内核会限制其用户堆不能越过 `AGENT_CONTEXT_BASE`，避免堆与 Agent Context 重叠。`agent_create()` / `agent_fork()` 在复制父进程页表前检查父进程 `sz`，若 `sz > AGENT_CONTEXT_BASE` 直接返回失败；`agent_make()` 也保留同样防御性检查。

进程退出或 `exec` 替换页表时，内核会检查并释放或重建 Agent Context 映射，避免页表残留映射导致释放失败。`exec` 构造新页表时使用局部数组保存新 Context 页，只有在提交新页表时才安装到 PCB；若 argv 复制等步骤失败，旧 Context 指针保持有效。`agentexec` 可直接运行，也可作为 Agent `exec("agentexec")` 的目标程序；`agentstress` 覆盖 exec 失败后继续调用 Agent syscall 的场景。

## 演示路径

1. 普通父进程调用 `agent_info()`，确认自身不是 Agent。
2. 父进程调用 `agent_create()` 或 `agent_fork()` 创建 Agent 子进程。
3. 子进程调用 `agent_info()`，确认自身是 Agent，并获取 Agent ID 和 Context 区信息。
4. 子进程向 Agent Context 写入 `AGT` 并读回，验证地址区可访问。
5. 父进程等待 Agent 子进程退出。

压力复测还覆盖一个地址限制场景：普通进程先用 lazy `sbrk` 把堆扩展到 `AGENT_CONTEXT_BASE` 以上，分别在未触碰页和已触碰并映射页两种情况下调用 `agent_create()`，预期均返回失败而不是 kernel panic。对应输出为 `agentstress: parent_over_context_rejected=1`。

## 当前扩展

当前实现已经不只是最小 Agent 身份标记，还补充了赛题命名兼容入口 `agent_create()`、父进程地址空间限制检查、配额、Loop 状态、Context Path 元信息、事件统计、capability mask 和 4 页高性能 Context 镜像。`agentfinal` 会验证 Context 大小、容量和直接读取一致性；`agentstress` 验证 exec 生命周期、sbrk 增长上限和父进程越界创建拒绝；`labdemo` 和 `labbench` 验证 `WAITING` 状态、事件唤醒、心跳和 Agent Loop 统计。
