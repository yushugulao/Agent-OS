# AgentOS-uCore 系统架构

AgentOS-uCore 是构建在 RISC-V uCore 上的 Agent 操作系统功能模块。我们保留 uCore 的进程、虚拟内存、文件系统、IPC、调度和设备 I/O 主体，在这些内核对象的生命周期中加入 Agent 身份、Context、结构化工具、文件语义、事件、资源账户和 workflow 调度。上层 Agent 应用因此可以围绕同一组内核对象组织多轮执行，而不必分别维护一套进程状态、一套文件状态和一套任务状态。

## 文档索引

- [一、设计问题](#一设计问题)
- [二、总体架构](#二总体架构)
- [三、uCore 基础与接入点](#三ucore-基础与接入点)
- [四、AgentOS 对象模型](#四agentos-对象模型)
- [五、核心功能模块](#五核心功能模块)
- [六、一次请求的内核路径](#六一次请求的内核路径)
- [七、用户态运行时与应用](#七用户态运行时与应用)
- [八、源码组织与验证](#八源码组织与验证)

## 一、设计问题

一个 Agent workflow 往往同时包含多个进程、多轮模型调用、结构化工具、共享文件和后台事件。它们有三个共同特征：

1. 身份和权限会随委派逐步收紧，执行主体还要与可信映像、文件 scope 和本次 workflow 生命周期对应；
2. 一轮工具输出会成为后续轮次、文件操作或跨 Agent 消息的输入，需要保存 cause、span、branch 和来源标签；
3. 进程退出、workflow 关闭和对象槽位复用会跨越多个内核子系统，需要统一停止新操作并完成资源回收。

普通用户态运行库可以编排这些步骤，却无法在进程表、页表、VFS、等待队列和调度器之间建立原子关系。基于对上述状态流转的拆解，我们把必须依赖内核对象和内核时序的能力放入 AgentOS，把模型协议和交互界面保留在用户态与 Host 侧。当前实现运行于单 Hart RISC-V64 Guest，同时维护最多 4 个 active workflow；这些规模参数直接定义在 [`os/workflow_lifecycle.h`](../os/workflow_lifecycle.h) 和 [`os/agent.h`](../os/agent.h) 中。

<p align="center">
  <img src="figures/architecture/plain_agentos_comparison.jpg" alt="普通 uCore 与 AgentOS-uCore 的能力对比" width="940">
</p>

图中以同一 Agent 工作负载对照两条路径：普通 uCore 由应用维护身份、Context、字符串工具参数和轮询状态；AgentOS-uCore 将身份、Context、Typed Tool、Live Query、资源域和 workflow 调度接入同一生命周期。[查看原生 DrawIO 源文件](figures/architecture/plain_agentos_comparison.drawio)。

## 二、总体架构

<p align="center">
  <img src="figures/architecture/agentos_overview.jpg" alt="AgentOS-uCore 产品架构" width="980">
</p>

[查看原生 DrawIO 源文件](figures/architecture/agentos_overview.drawio)

整体架构自下而上分为四层。底层是 uCore 内核主体；其上是直接嵌入进程、VFS、等待与调度路径的 AgentOS 内核模块；再上是版本化 UAPI、Guest 运行库和 Host relay；最上层是单 Agent Loop、Nexus 多 Agent workflow 以及科研工作流应用。

| 层次 | 主要组成 | 向上一层提供的能力 |
| --- | --- | --- |
| Agent 应用 | `agentlive_ucore`、`agentnexus_ucore`、科研 workflow | 目标分解、角色协作、工具调用和结果汇总 |
| 用户态运行时 | Guest UAPI、Task SQ/CQ、串口 frame、TLS/provider adapter | 把应用动作编码为稳定的内核请求，并连接外部模型服务 |
| AgentOS 内核模块 | 身份、lifecycle、Context、工具合同、Live Query、事件、资源、EEVDF、fence | 为一次 workflow 提供受控执行、状态传播、等待唤醒和一致性切片 |
| uCore 基础内核 | 进程与线程、页表、VFS、IPC、时钟、调度、VirtIO | 提供 RISC-V 操作系统基础对象与执行环境 |

这四层通过版本化结构体连接。Guest 公共封装集中在 [`user/include/agent.h`](../user/include/agent.h)，执行合同、生命周期、来源、资源、Task Channel、工具和 workflow fence 的冻结布局位于 [`include/`](../include/)；内核在接收用户指针后重新执行 copyin、长度和版本检查。

## 三、uCore 基础与接入点

AgentOS 没有另起一套微内核服务，而是沿 uCore 已有对象的真实生命周期接入。这样，Agent 身份随进程创建与 `exec` 改变，Context 随页表建立，文件状态随 inode 更新，等待状态随线程睡眠和唤醒推进，资源服务量随调度切换结算。

| uCore 路径 | AgentOS 接入内容 | 主要源码 |
| --- | --- | --- |
| 进程创建、`fork`、`exec`、`exit` | workflow root/worker 创建、身份发布、控制边转交、Context 映射与 teardown | [`os/proc.c`](../os/proc.c)、[`os/vm.c`](../os/vm.c)、[`os/exec_policy.c`](../os/exec_policy.c) |
| 系统调用入口 | UAPI 分派、用户内存校验、operation gate、工具与 fence 执行 | [`os/syscall.c`](../os/syscall.c)、[`os/agent_core.c`](../os/agent_core.c) |
| VFS 与 inode | 动态 scope、可信映像绑定、inode incarnation、metadata 与编辑租约 | [`os/file.c`](../os/file.c)、[`os/vfs_security.c`](../os/vfs_security.c)、[`os/agent_metadata_actions.c`](../os/agent_metadata_actions.c) |
| IPC 与等待队列 | route、event queue、heartbeat、按线程 generation 的原子 sleep/wakeup | [`os/agent_ipc.c`](../os/agent_ipc.c)、[`os/wait.c`](../os/wait.c)、[`os/timer.c`](../os/timer.c) |
| 调度与时钟 | workflow eligibility、virtual deadline、service-cycle 记账与 wakeup probe | [`os/proc.c`](../os/proc.c)、[`os/workflow_scheduler.c`](../os/workflow_scheduler.c)、[`os/trap.c`](../os/trap.c) |
| 内存与存储资源 | reservation、publish/refund、phase lease、账户关闭与精确快照 | [`os/resource_controller.c`](../os/resource_controller.c)、[`os/workflow_credit_domain.c`](../os/workflow_credit_domain.c)、[`os/fs.c`](../os/fs.c) |

这些接入点共同形成一条产品主线：uCore 负责对象的存在和运行，AgentOS 为对象附加 workflow 身份、权限、因果、资源和一致性语义。

## 四、AgentOS 对象模型

### 4.1 Lifecycle key

AgentOS 使用 `{id, generation}` 标识一次 workflow 生命周期。`id` 对应有界槽位，`generation` 在槽位再次使用时递增。任何长期对象只保存 `id` 都不足以确认归属，因此 Agent、Context、watch、execution contract、资源账户、Task Channel、调度实体和 fence receipt 都携带完整 key。

```c
struct workflow_lifecycle_key {
    uint id;
    uint64 generation;
};

static inline int key_equal(struct workflow_lifecycle_key a,
                            struct workflow_lifecycle_key b)
{
    return a.id == b.id && a.generation == b.generation;
}
```

该结构来自 [`os/workflow_lifecycle.h`](../os/workflow_lifecycle.h)。槽位查找同时比较 `used`、`id` 和 `generation`，旧句柄因此不会命中新一轮 workflow。公开 ABI 中的对应结构固定为 16 字节，定义见 [`include/agent_lifecycle_abi.h`](../include/agent_lifecycle_abi.h)。

### 4.2 进程状态与 workflow 状态

| 对象 | 保存的核心状态 | 生命周期 |
| --- | --- | --- |
| `struct proc` | Agent id、role、capability、controller、scope、Context 页、资源账户和事件队列 | 随进程创建、exec 与 exit 变化 |
| `workflow_lifecycle_record` | scope、generation、controller、members、operation/departure 计数、fence gate | 从 workflow root 创建持续到最后成员和后台工作排空 |
| `agent_context_record` | sequence、request、cause、span、branch、tool、status、hash 与来源标签 | 按 Agent 保留最近 128 条记录 |
| metadata catalog entry | inode identity、incarnation、业务字段、索引项与 catalog generation | 随 VFS 更新并在 scope/lifecycle 回收时删除 |
| `workflow_scheduler_entity` | vruntime、virtual deadline、service cycles、runnable 和 wakeup 指标 | 随 workflow resource domain 建立与释放 |
| evidence segment | ordinary/critical ticket、gap、previous root 与 sealed root | 按 workflow 保存并由 fence 封存 |

`workflow_lifecycle_record` 设置三类门控状态。operation gate 覆盖工具、fork 和 metadata 等普通操作；departure gate 覆盖进程退出和异步清理；fence gate 在两类计数均为零时取得一致性切片。最后一个成员离开后，记录进入 closing/retiring，相关模块按同一个 key 释放对象，随后槽位才允许复用。

## 五、核心功能模块

### 5.1 身份、生命周期与 Context

可信 bootstrap 通过 `agent_workflow_create()` 创建新的 workflow root；同一 workflow 中具备 `AGENT_CAP_ORCHESTRATE` 的 Agent 可以通过 `agent_worker_create()` 指定映像和 capability 上限。实际权限取 role policy、映像 manifest、父级委派、目标 workflow 和 VFS scope 的交集。控制关系使用不可转让的 `control_id`，退出时先从 `OPEN` 进入 `QUIESCING`，再转交或撤销子控制边。

每个 Agent 固定映射 7 页 Context：前 6 页为用户只读的 header、latest response 和记录区，第 7 页为用户可写 cache；9 页 sidecar 保存规范化 operation/result、path index、actor 与来源状态。writer 先写 sidecar detail，再把 publication sequence 置为奇数并发布 record、latest 和 header，最后恢复偶数，使直接映射读取能够识别并发写入。具体实现见[身份、生命周期与 Context](modules/identity-context.md)。

### 5.2 结构化工具与数据路径

工具目录提供 V1、typed V2、ENFORCE V3、compact batch 和 16 槽 Task SQ/CQ。V2 依据 schema 检查参数；V3 继续绑定冻结 DAG、前驱、attempt、deadline、输入 fingerprint 和资源包络。调用入口先核对 Agent 身份与 lifecycle，再解析工具并按 schema 解码参数；进入 operation gate 后，执行链继续检查 required capability、execution contract、来源标签与资源 phase lease。文件写入还会在 VFS owner 路径复核动态 scope。

结构化协议把应用中的“调用一个工具”拆成可以由内核逐项检查的数据对象。实现与调用示例见[工具执行](modules/tool-execution.md)，公共布局见 [`include/agent_tool_abi.h`](../include/agent_tool_abi.h)、[`include/agent_execution_contract_abi.h`](../include/agent_execution_contract_abi.h) 和 [`include/agent_task_channel_abi.h`](../include/agent_task_channel_abi.h)。

### 5.3 Live Query

文件 metadata catalog 只收录显式登记的对象，并为 `status`、`stage`、`kind` 等字段维护等值索引。planner 按 `status -> stage -> kind` 的固定优先级选择首个可用索引，返回前再复核完整谓词、scope、lifecycle、inode incarnation 和 catalog generation。typed watch 保存 query、predicate 与 generation 元数据；catalog 更新时重算变更前后的成员关系，产生 `ENTER`、`UPDATE` 或 `LEAVE`。事件缺口通过 replacement watch 与未截断的有界 snapshot 重建基线。

这一层把 VFS 对象转换为 workflow 可以查询和订阅的数据源。目录、索引、事务和 watch 的实现见 [Live Query](modules/live-query.md)。

### 5.4 事件、资源、EEVDF 与 fence

事件队列把文件变化、跨 Agent 消息、heartbeat、deadline 和模型完成统一送入 `agent_wait()`。谓词复查与等待者发布位于同一关中断窗口，等待 key 使用线程的 identity generation；event baton 每次只唤醒一个实际接收者。队列总容量为 16，并为内核事件和不同来源类别保留容量。

Workflow Credit Domain 用 `free`、`pending`、`used` 三态记录已预充、预留和已发布资源。外层 workflow EEVDF 使用固定等权实体，latency class 和 wall deadline 决定 1/2/4/8 tick 的 service request；eligible 集合中 virtual deadline 最早的 workflow 先运行。工具和 Context 的终态进入按 workflow 划分的执行记录环，controller 可以用 fence 取得 metadata generation、精确 credit 快照和记录根哈希。完整流程见 [Workflow 运行时](modules/workflow-runtime.md)。

## 六、一次请求的内核路径

以 ENFORCE V3 工具请求为例，Guest 从 [`user/include/agent.h`](../user/include/agent.h) 调用 `tool_call_v3()`，系统调用分派进入 [`os/syscall.c`](../os/syscall.c)，随后由 [`os/agent_core.c`](../os/agent_core.c) 完成工具执行。内核路径可以概括为：

```text
Guest request
  -> copyin 固定头与变长字段
  -> 校验 version / struct_size
  -> 匹配 Agent identity + workflow {id, generation}
  -> 解析 tool id/name，按 schema 解码参数
  -> 进入 workflow operation gate
  -> 匹配 execution contract 与 DAG predecessor
  -> 校验 required capability、provenance 与 side-effect mask
  -> 取得 resource phase lease
  -> 执行工具；VFS 副作用继续检查 scope
  -> deactivate / settle phase lease
  -> 提交 provenance output 与 Context/Evidence terminal
  -> 完成合同节点，离开 operation gate
  -> copyout response
```

失败发生在副作用前时，状态码直接描述失败原因；已经预留的资源和 evidence 槽位会走 refund/discard 路径。副作用完成后的 terminal record 与 evidence ticket 通过同一提交过程发布，避免结果已生效而记录缺失。

一次 workflow 的完整运行则由以下步骤组成：

1. bootstrap 创建 workflow root，内核分配 lifecycle key、动态 scope 与资源账户；
2. 具备 `AGENT_CAP_ORCHESTRATE` 的 Agent 按可信映像与 capability ceiling 创建同 lifecycle worker；
3. 各 Agent 发布 Context，登记 metadata，安装 typed watch 或 execution contract；
4. Agent 通过 V2、V3、batch 或 Task SQ/CQ 执行工具，并消费文件、消息和 timer 事件；
5. workflow EEVDF 在多个 workflow 之间分配 CPU 服务量，同一 workflow 内仍由 uCore 选择具体线程；
6. controller 在阶段结束时生成 fence receipt，随后关闭成员；
7. 最后一个成员和后台任务排空后，lifecycle finalizer 回收订阅、队列、执行资源和调度实体；普通 scope 同步回收。标记 `preserve_on_retire` 的持久输出 scope 保留 registry、文件与 storage charge，并与已回收的 lifecycle generation 解绑定。

## 七、用户态运行时与应用

AgentOS 的内核 ABI 只承载结构化状态和系统动作，模型供应商协议保持在 Host 侧。`agentlive_ucore` 通过串口 frame 提交模型请求、接收工具调用并把终态写回 Context；[`host_tools/agentos_relayd.py`](../host_tools/agentos_relayd.py) 负责 TLS、provider JSON 与本地运行目录。这样，模型服务更换不会改变内核 ABI。

`agentnexus_ucore` 在一个 workflow 中创建 Coordinator、System、Research 和 Analyst 四类产品角色。它们使用独立 PID、身份和 Context，通过内核 `MESSAGE` route、typed task 与 workflow-scoped artifact 协作；Coordinator 读取 specialist artifact 后推进下一阶段。Guest 主程序见 [`user/src/agentnexus_ucore.c`](../user/src/agentnexus_ucore.c)，运行流程图与命令见 [Workflow 运行时](modules/workflow-runtime.md) 和[运行指南](usage.md)。

## 八、源码组织与验证

| 目录 | 内容 |
| --- | --- |
| [`os/`](../os/) | AgentOS 内核模块及其 uCore 接入点 |
| [`include/`](../include/) | 跨内核/Guest 的版本化 ABI 与资源策略契约 |
| [`user/`](../user/) | Guest 封装、回归程序、Agent Loop 与 Nexus 应用 |
| [`host_tools/`](../host_tools/) | 串口协议、外部模型 relay、Console 与 Host 测试 |
| [`scripts/`](../scripts/) | 静态结构检查、QEMU 回归、故障测试与日志验证 |
| [`one_shot_metrics/`](../one_shot_metrics/) | 冻结的逐样本数据、提取器、校验器和绘图入口 |

架构相关的模块依赖、UAPI 布局与完整 Guest 回归分别通过以下入口检查：

```bash
make agent-module-check
make agent-uapi-check
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

接下来可从[身份、生命周期与 Context](modules/identity-context.md)进入内核对象的建立过程，再沿[工具执行](modules/tool-execution.md)、[Live Query](modules/live-query.md)和 [Workflow 运行时](modules/workflow-runtime.md)阅读一条完整的 Agent workflow。
