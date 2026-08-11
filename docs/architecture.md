# AgentOS-uCore 产品架构

AgentOS-uCore 在 RISC-V uCore 内核中加入面向 Agent workflow 的系统功能。uCore 提供进程、虚拟内存、VFS、IPC、调度与设备 I/O，AgentOS 在这些基础能力上建立身份、Context、工具执行、文件状态、资源控制和 workflow 调度，再由用户态运行库组合为多轮 Agent 应用。

![AgentOS-uCore 产品架构](figures/architecture/agentos_overview.png)

[打开 DrawIO 源文件](figures/architecture/agentos_overview.drawio)

## 系统层次

| 层次 | 组成 | 作用 |
| --- | --- | --- |
| Agent 应用 | `agentlive_ucore`、`agentnexus_ucore` 及科研 workflow | 组织目标、任务、工具和阶段结果 |
| AgentOS 用户态 | 版本化 UAPI、Guest 运行库、typed request、Task SQ/CQ 映射 | 把应用操作转换为稳定的内核请求 |
| AgentOS 内核模块 | 身份、Context、工具合同、Live Query、事件、资源、EEVDF | 管理 workflow 状态并执行系统级检查 |
| uCore 基础内核 | 进程、页表、VFS、IPC、调度、时钟中断、VirtIO | 提供 RISC-V 操作系统基础能力 |

AgentOS 的核心状态直接接入 uCore 对象生命周期。Agent 身份随进程创建与 `exec` 发布，Context 映射随地址空间建立，文件 metadata 随 VFS 操作更新，workflow 调度状态随 enqueue、dispatch、sleep 和 tick 推进，退出路径统一释放订阅、队列、资源和生命周期槽位。

## 内核功能模块

### 身份与生命周期

受控创建路径为 Agent 分配 id、controller、role、capability 和 VFS scope。每个 workflow 使用 `{id, generation}` 标识本次生命周期，Agent、Context、watch、合同、资源账户、Task Channel 和 fence 都绑定这组 key。槽位复用后，旧请求根据对象状态返回 `STALE`、`CONFLICT` 或 `NOT_FOUND`。最后一个成员退出且 operation、departure 与后台任务排空后，内核回收 lifecycle 槽位。

主要实现为 `os/agent_identity.c`、`os/agent_lifecycle.c`、`os/workflow_lifecycle.c` 和 `os/vfs_security.c`。

### Context 与来源传播

每个 Agent 映射 7 页 Context 区，前 6 页由内核发布并保持用户只读，第 7 页供 Guest cache 使用。记录保存 sequence、cause、span、branch、tool、status 与 provenance。工具结果、文件读取和跨 Agent 消息把来源标签带入后续请求，使副作用检查能够沿多轮执行追踪输入来源。

主要实现为 `os/agent_context.c`、`os/agent_context_path.c` 和 `os/agent_provenance.c`。

### 结构化工具与执行合同

内核工具目录提供 V1、typed V2、ENFORCE V3 和 compact batch。V2 按 schema 校验参数，V3 进一步绑定冻结 DAG、前驱、attempt、deadline、输入 fingerprint 和资源包络。Task Channel 以 16 槽 SQ/CQ 处理高频提交，并通过 request id 与 channel、ring、slot generation 处理队列复用。

主要实现为 `os/agent_core.c`、`os/agent_tool_protocol.c`、`os/agent_execution_contract.c` 和 `os/agent_task_channel.c`。

### Live Query 与事件

AgentOS 为显式登记的文件 metadata 建立 catalog。`status`、`stage` 和 `kind` 等值索引用于缩小候选集，返回前继续复核完整谓词、lifecycle、scope 和 catalog generation。inode incarnation 由 VFS 增量路径维护并随结果返回。typed watch 根据查询集合的变化产生 `ENTER`、`UPDATE` 和 `LEAVE` 事件，队列缺口通过 generation resync 恢复完整视图。

主要实现为 `os/agent_metadata.c`、`os/agent_metadata_catalog.c`、`os/agent_metadata_query.c`、`os/agent_metadata_objects.c`、`os/agent_live_query_events.c` 和 `os/agent_ipc.c`。

### 资源与 workflow 调度

Workflow Credit Domain 使用 free、reserved、charged 三态 credit 管理内核对象准入与回收。workflow EEVDF 的外层 workflow 使用固定等权服务；latency class 与 wall deadline 决定 service request，睡眠衰减调整 vruntime，调度器从 eligible 集合中选择 virtual deadline 最早的 workflow。同一 workflow 的多个成员共享服务量账户，实体内部继续使用 per-Agent 调度策略。

主要实现为 `os/workflow_credit_domain.c`、`os/resource_controller.c` 和 `os/workflow_scheduler.c`。

## 一次 workflow 的运行过程

1. bootstrap 进程创建 workflow root，内核分配 lifecycle generation、scope 与资源账户。
2. controller 按可信映像、role 和 capability 创建 worker。
3. Agent 发布 Context，登记文件 metadata，并安装 typed watch 或 execution contract。
4. Agent 通过 V2、V3、compact batch 或 Task SQ/CQ 提交工具请求。
5. 内核完成 request copyin 与布局校验、Agent 身份与 lifecycle 检查以及工具解析和参数 schema，再进入 lifecycle operation gate，依次执行包含 required capability 匹配的 contract admission、来源授权、resource phase lease 和 effect gate；VFS 副作用在 owner 路径继续检查 scope。
6. 工具结果写入 Context；文件变化、IPC、heartbeat 和模型完成进入事件队列。
7. 等待中的 Agent 由 `agent_wait` 休眠，workflow EEVDF 在事件到达后重新分配 CPU 服务。
8. controller 生成 workflow 一致性 receipt 并关闭成员，生命周期模块回收关联对象与资源账户。

这条主线让进程、文件、工具和调度共享同一个 lifecycle。各模块保存自己的私有状态，通过版本化结构和受控 helper 交换信息。

## uCore 接入点

| uCore 路径 | AgentOS 接入内容 | 主要源码 |
| --- | --- | --- |
| 进程创建、`exec`、退出 | 身份发布、Context 映射、成员变更与 teardown | `os/proc.c`、`os/vm.c`、`os/exec_policy.c` |
| 系统调用 | request copyin、权限链、合同检查与结果发布 | `os/syscall.c`、`os/agent_core.c` |
| VFS | scope、inode incarnation、metadata 更新与编辑租约 | `os/file.c`、`os/vfs_security.c`、`os/agent_metadata_actions.c` |
| IPC 与等待 | route、event queue、heartbeat、原子 sleep/wakeup | `os/agent_ipc.c`、`os/agent_background.c` |
| 调度与时钟 | workflow eligibility、service accounting、deadline | `os/workflow_scheduler.c`、`os/trap.c`、`os/timer.c` |
| 资源分配与释放 | credit reservation、publish、refund 与 destroy | `os/workflow_credit_domain.c`、`os/resource_controller.c` |

## 公开接口

内核通过 [`user/include/agent.h`](../user/include/agent.h) 向 Guest 暴露系统调用封装。执行合同、生命周期、来源、资源、Task Channel、工具和 workflow fence 使用根目录的 `agent_*_abi.h` 固定布局。版本化结构体携带 `version`，并按各自定义使用 `size` 或 `struct_size`；内核对用户指针和变长字段重新 copyin。

接口和状态码见 [API](api.md)，身份与副作用检查见[安全机制](security.md)。四组核心模块的内部设计见：

- [身份与 Context](modules/identity-context.md)
- [工具执行](modules/tool-execution.md)
- [Live Query](modules/live-query.md)
- [Workflow 运行时](modules/workflow-runtime.md)
