# 面向 AI 智能体的操作系统内核：系统架构

AgentOS-uCore 建立在 RISC-V uCore 之上。进程、虚拟内存、文件系统、IPC、调度器和设备 I/O 仍由 uCore 负责。我们在这些原有路径中加入 Agent 进程创建与地址空间、结构化工具接口、上下文路径、文件查询和 Agent Loop 所需的状态。同一次任务中的多个进程由此共用一套身份、事件、资源和调度机制，而模型协议与具体任务策略仍留在用户态。

## 文档索引

- [一、要解决的问题](#一要解决的问题)
- [二、总体架构](#二总体架构)
- [三、uCore 中的接入位置](#三ucore-中的接入位置)
- [四、AgentOS 对象模型](#四agentos-对象模型)
- [五、主要功能模块](#五主要功能模块)
- [六、一次工具请求如何执行](#六一次工具请求如何执行)
- [七、Guest Runtime 与应用](#七guest-runtime-与应用)
- [八、源码位置与检查方法](#八源码位置与检查方法)

## 一、要解决的问题

一个 Agent 工作流通常包含多个进程、多轮模型调用、Structured Tool、共享文件和后台事件。这类程序给内核带来了三个新问题：

1. 委派任务时，身份和能力位会逐步收紧。内核必须确认执行进程、可信映像、VFS 文件访问范围和本次工作流确实相互对应。
2. 工具输出会继续成为后续工具、文件操作或跨 Agent 消息的输入。系统需要记录每一步的起因、调用跨度、分支和 provenance。
3. 进程退出、工作流关闭和槽位复用会涉及进程表、页表、VFS、等待队列和调度器。各子系统必须按同一个生命周期停止新操作并回收旧对象。

普通 uCore 应用可以在用户态编写这些逻辑，但每个应用都要重复维护，内核也无法替应用保证跨子系统的原子性。AgentOS 把身份检查、Context、Typed Watch、资源记账和工作流调度做成通用内核功能。模型协议和交互界面仍留在用户态与 Host。当前系统运行于单 Hart RISC-V64 Guest，同时最多维护 4 个活动工作流。相关容量定义在 [`os/workflow_lifecycle.h`](../os/workflow_lifecycle.h) 和 [`os/agent.h`](../os/agent.h)。

同一份 Agent 任务在普通 uCore 与 AgentOS-uCore 中的两条执行路径如图 1 所示。图的上方固定了相同的任务要求与输入，左、右两侧分别展开由应用自行维护 Agent 运行状态的路径，以及由内核提供通用 Agent 机制的路径。

<p align="center">
  <img src="figures/architecture/plain_agentos_comparison.jpg" alt="普通 uCore 与 AgentOS-uCore 的智能体任务实现对比" width="940">
</p>

**图 1　普通 uCore 与 AgentOS-uCore 的智能体任务实现对比**

**普通 uCore 路径。** 应用先自行保存 Agent 身份与 Context，再分别实现工具参数检查、执行规则、文件轮询、等待和资源统计，最后调用通用的系统调用、VFS、IPC 与进程调度能力。Agent 语义散落在各个应用模块中，内核只能看到彼此独立的进程和系统调用。

**AgentOS-uCore 路径。** 内核先用身份、生命周期和 Context 统一标识本轮工作，再由 Typed Tool、Live Query/Watch 和 Workflow Credit Domain 分别处理工具检查、文件事件与资源记账，外层 EEVDF 按工作流分配 CPU 服务量。具体系统调用、文件访问和线程运行仍落到 uCore 原有机制，应用可以把主要精力放在任务拆分与工具逻辑上。

**共同结果。** 两条路径接收相同输入，也能产生相同任务结果；区别在于 AgentOS-uCore 把需要跨进程、跨轮次保持一致的状态交给内核维护，使身份检查、事件唤醒和资源结算沿同一条执行链完成。原生图源见 [`plain_agentos_comparison.drawio`](figures/architecture/plain_agentos_comparison.drawio)。

## 二、总体架构

AgentOS-uCore 按职责分为五部分。uCore 基础内核提供进程、VFS、等待队列和调度器，AgentOS 内核模块在这些路径中维护工作流状态。Guest Runtime 封装版本化 UAPI 并收发串口帧，Host Relay 负责 TLS 和模型 Provider 适配，Agent Live、Nexus 与科研工作流应用则实现具体任务。主架构图见项目 [README](../README.md#23-总体架构)，本篇按图中层次说明各模块的关系。

| 层次 | 主要组成 | 作用 |
| --- | --- | --- |
| Agent 应用 | `agentlive_ucore`、`agentnexus_ucore`、科研工作流 | 拆分目标、安排角色、调用工具并汇总结果 |
| Guest Runtime | Guest UAPI、Task Channel（SQ/CQ）、有界串口帧 | 把应用动作编码成内核请求，并在 Guest 内收发模型消息 |
| Host Relay | 串口中继、TLS、Provider、Replay 与工作区 broker | 连接外部模型服务，并在显式配置的工作区 root 范围内提供 manifest 与指定版本字节，不决定文件候选或业务任务 |
| AgentOS 内核模块 | Agent identity、生命周期、Context、Execution Contract、Live Query、事件、Workflow Credit Domain、EEVDF、Workflow Fence | 检查工作流操作，保存状态，负责等待唤醒，并生成 terminal record |
| uCore 基础内核 | 进程与线程、页表、VFS、IPC、时钟、调度、VirtIO | 提供 RISC-V 操作系统的基础对象和运行环境 |

五层之间通过带版本号的结构体或有界帧传递数据。Guest 封装集中在 [`user/include/agent.h`](../user/include/agent.h)。Execution Contract、生命周期、provenance、资源、Task Channel、Structured Tool 和 Workflow Fence 的固定布局位于 [`include/`](../include/)。内核收到用户指针后会重新复制，并检查结构长度和版本号。

## 三、uCore 中的接入位置

AgentOS 沿 uCore 对象原有的生命周期接入：进程创建或执行新映像时更新 Agent identity，建立页表时映射 Context，VFS 修改 inode 时更新文件状态，线程睡眠或唤醒时更新等待状态，上下文切换时结算 CPU 服务量。

| uCore 路径 | AgentOS 加入的处理 | 主要源码 |
| --- | --- | --- |
| 进程创建、`fork`、`exec`、`exit` | 创建工作流根进程和工作进程，发布 Agent identity，转交控制关系，映射 Context，退出时清理 | [`os/proc.c`](../os/proc.c)、[`os/vm.c`](../os/vm.c)、[`os/exec_policy.c`](../os/exec_policy.c) |
| 系统调用入口 | 分派 UAPI，检查用户内存，登记生命周期操作，执行工具和 Workflow Fence | [`os/syscall.c`](../os/syscall.c)、[`os/agent_core.c`](../os/agent_core.c) |
| VFS 与 inode | 检查动态文件访问范围和可信映像，区分 inode incarnation，维护元数据和编辑租约，并以未命名 inode 发布完整结果文件 | [`os/file.c`](../os/file.c)、[`os/fs.c`](../os/fs.c)、[`os/vfs_security.c`](../os/vfs_security.c)、[`os/agent_metadata_actions.c`](../os/agent_metadata_actions.c) |
| IPC 与等待队列 | 消息路由、事件队列、心跳，以及按线程 generation 进行的无丢失唤醒 | [`os/agent_ipc.c`](../os/agent_ipc.c)、[`os/wait.c`](../os/wait.c)、[`os/timer.c`](../os/timer.c) |
| 调度与时钟 | 判断工作流是否可运行，比较虚拟截止时间，统计 CPU 周期和唤醒延迟 | [`os/proc.c`](../os/proc.c)、[`os/workflow_scheduler.c`](../os/workflow_scheduler.c)、[`os/trap.c`](../os/trap.c) |
| 内存与存储资源 | 以 reserve/commit 管理额度，失败时退还，记录工具执行期间的 Phase Lease，关闭账户并取得精确快照 | [`os/resource_controller.c`](../os/resource_controller.c)、[`os/workflow_credit_domain.c`](../os/workflow_credit_domain.c)、[`os/fs.c`](../os/fs.c) |

uCore 仍负责对象的创建和运行。AgentOS 为这些对象补充工作流身份、能力位、因果记录、资源用量和 terminal state。

## 四、AgentOS 对象模型

### 4.1 生命周期键

AgentOS 使用生命周期键 `{id, generation}` 标识一次工作流。`id` 是有界表中的槽位，槽位再次投入使用时 `generation` 递增。Agent、Context、Typed Watch、Execution Contract、Workflow Credit Domain、Task Channel、调度对象和 Workflow Fence 都保存完整键，旧句柄因此不会误指向下一次运行。

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

该结构定义在 [`os/workflow_lifecycle.h`](../os/workflow_lifecycle.h)。查找槽位时，内核同时比较 `used`、`id` 和 `generation`。公开 ABI 中的对应结构固定为 16 字节，见 [`include/agent_lifecycle_abi.h`](../include/agent_lifecycle_abi.h)。

### 4.2 进程状态与工作流状态

| 对象 | 保存的主要状态 | 保留时间 |
| --- | --- | --- |
| `struct proc` | Agent 编号、角色、能力位、控制关系、文件访问范围、Context 页、资源账户和事件队列 | 随进程创建、`exec` 和 `exit` 变化 |
| `workflow_lifecycle_record` | 文件访问范围、generation、控制进程、成员数、操作/退出计数和 fence 阻断状态 | 从工作流根进程创建持续到最后成员与后台工作结束 |
| `agent_context_record` | 序号、请求号、起因、调用跨度、分支、工具、状态、哈希和 provenance label | 每个 Agent 保留最近 128 条 |
| Metadata Catalog 条目 | inode 身份、incarnation、业务字段、索引项和 catalog generation | 随 VFS 更新，在文件访问范围或生命周期回收时删除 |
| `workflow_scheduler_entity` | `vruntime`、虚拟截止时间、CPU 服务量、可运行线程数和唤醒指标 | 随工作流建立和释放 |
| 工作流记录环区段 | 普通/关键槽、缺口、前段根摘要和封存根摘要 | 按工作流保留，由 Workflow Fence 封存 |

生命周期记录分别统计普通操作和退出操作。工具、`fork` 和元数据更新开始时增加 `active_operations`，进程退出和异步清理则增加 `departing_operations`。生成 Workflow Fence 前，内核暂停两类新操作，并等待已有操作完成。最后一个成员离开后，记录进入 `closing`/`retiring`。各模块用同一个生命周期键释放对象，全部结束后槽位才能再次使用。

## 五、主要功能模块

### 5.1 Agent 进程创建与地址空间设计

可信引导进程调用 `agent_workflow_create()` 创建工作流根进程。同一工作流中，具有 `AGENT_CAP_ORCHESTRATE` 能力位的 Agent 可以调用 `agent_worker_create()`，指定工作进程映像和能力上限。最终能力集合取角色策略、映像清单、父进程委派、目标工作流和 VFS 文件访问范围的交集。控制关系使用不可转让的 `control_id`；控制进程退出时先进入 `QUIESCING`，再转交或撤销子控制关系。普通进程沿原有 `fork/exec` 路径运行，只有受控创建并通过映像策略校验的进程才取得 Agent identity。

低频且需要内核保护的角色、能力、Loop 状态、资源账户和 Context 元信息保存在 PCB 或生命周期对象中；高频读取的路径记录、最近响应和查询缓存放在 Agent Context 区。每个 Agent 固定映射 7 页 Context。前 6 页由内核写入，包含表头、最近响应和记录区，用户态只能读取；第 7 页由 Guest 缓存派生数据。另有 9 页内核 sidecar，保存规范化操作与结果、路径索引、执行者和 provenance 状态。commit 时，内核先填好 sidecar，再把发布序号改为奇数，随后更新公开记录、最近响应和表头，最后恢复为偶数。直接读取程序据此判断是否遇到并发 commit。详细设计见 [Agent 进程与 Context](modules/identity-context.md)。

### 5.2 Agent-OS 内核结构化交互接口与工具调用协议

工具调用协议用工具编号、typed 参数、状态码和结构化结果表达一次交互，不让内核解释自然语言。公开接口包括 V1、带类型信息的 V2、ENFORCE V3、批处理和 16 槽 Task Channel（SQ/CQ）。V2 按 Tool Registry 的参数 schema 检查请求；V3 继续绑定冻结的 DAG、前驱、尝试次数、截止时间、输入指纹和资源上限。入口先核对 Agent identity 与生命周期，再解析工具和 schema。登记普通操作后，内核继续检查能力位、Execution Contract、provenance 和 Phase Lease。文件写入在 VFS 路径真正执行时还会再查一次当前文件访问范围。

Task Channel 的 `delegate_task` 把 56 字节不可变描述符作为 `AGENT_ARTIFACT_TASK` 输入，进入同一 Execution Contract 后停留在内核 pending 队列。取得 TASK route 且具有 `AGENT_CAP_TASK_ACCEPT` 的目标 Agent 通过 claim/complete 接口领取和完成；发起者只从 CQ 取得一条 terminal CQE。描述符只绑定目标身份、任务编号、关联编号和 capsule handle，大段输入与结果由 Guest artifact 承载。首版拒绝 self delegation，也不允许同一活动端点同时成为 owner 与 target。取消、截止时间、目标退出和迟到完成由内核按同一个 Task Channel 状态处理；同一生命周期 controller 可用 syscall 568 的 `REQUEST_CANCEL` 和完整任务绑定请求取消。若任务已被 claim，执行者仍要清理预绑定输出并确认内核选定的最新终态，首版不强制终止永久无响应的执行者。Task CQE 结算后，Contract 还要从 `RETIRING` 收敛到 `RECLAIMED`，普通作用和下一代 Contract 才恢复。

这样，应用中的一次工具调用被拆成内核能够逐项核对的数据。实现与调用示例见[工具执行](modules/tool-execution.md)，公共布局见 [`include/agent_tool_abi.h`](../include/agent_tool_abi.h)、[`include/agent_execution_contract_abi.h`](../include/agent_execution_contract_abi.h) 和 [`include/agent_task_channel_abi.h`](../include/agent_task_channel_abi.h)。

### 5.3 面向 Agent 查询优化的文件系统扩展

Metadata Catalog 只收录应用显式登记的文件，并为 `status`、`stage`、`kind` 建立等值索引。查询器依次检查三个字段并选择第一个可用索引。返回前再核对完整查询条件、文件访问范围、生命周期、inode incarnation 和 catalog generation。Typed Watch 保存查询条件及其 generation；目录更新时，内核比较修改前后的结果集合，产生 `ENTER`、`UPDATE` 或 `LEAVE`。增量事件出现 generation 缺口后，应用先安装替代订阅，再读取未截断的有界快照，以此重建基线。

这套机制让工作流能够按业务状态查找文件，并在文件进入或离开结果集合时收到通知。Metadata Catalog、索引、事务和 Typed Watch 的实现见 [Live Query](modules/live-query.md)。

工作流应用发布结果时，`agent_file_publish()` 先把 header 与 payload 复制进内核快照，再写入未命名 inode。第一阶段 checkpoint 固定数据与 inode，VFS 随后只做一次正式目录接入，再用 attach-only checkpoint 固定目录项；已有同名文件时返回 `DUPLICATE`，不执行覆盖。目录接入结果不确定时返回 `INDETERMINATE`，Guest 只在正式路径的 header、payload 和 EOF 与本次请求逐字节相同时收敛为幂等成功。

### 5.4 Agent Loop 内核运行机制

Agent Loop 的决策策略仍在 Guest 中，内核承担需要跨轮保存和统一调度的部分。文件变化、跨 Agent 消息、心跳、截止时间和模型完成通知都进入事件队列，并由 `agent_wait()` 取出。内核在同一次关中断期间复查队列并登记等待者，等待键使用线程 `identity_generation`。事件接力标记每次只唤醒一个实际接收者。队列总容量为 16，其中一部分槽位留给内核事件和不同来源的事件；队列为空时，线程在等待队列中休眠。

Workflow Credit Domain 以 `free`、`pending`、`used` 三种状态记录资源。外层工作流 EEVDF 使用固定等权调度对象，延迟等级与实际截止时间决定 1、2、4 或 8 tick 的 CPU 时间申请；当前可运行的工作流中，虚拟截止时间最早者先执行。工具与 Context 的 terminal state 写入工作流记录环，控制进程可通过 Workflow Fence 取得 metadata generation、精确资源快照和记录环根摘要。完整流程见 [Workflow Runtime](modules/workflow-runtime.md)。

## 六、一次工具请求如何执行

以 ENFORCE V3 请求为例，Guest 从 [`user/include/agent.h`](../user/include/agent.h) 调用 `tool_call_v3()`。syscall 由 [`os/syscall.c`](../os/syscall.c) 分派，再由 [`os/agent_core.c`](../os/agent_core.c) 执行 Tool。主要步骤如下：

```text
Guest 提交请求
  -> copyin 固定头和变长字段
  -> 检查 version 和 struct_size
  -> 匹配 Agent identity 与工作流 {id, generation}
  -> 解析 tool id/name，按 schema 解码参数
  -> 在 workflow 中登记本次普通操作
  -> 匹配 Execution Contract 和前驱
  -> 检查能力位、provenance 和副作用掩码
  -> 取得本次 Tool 执行的 Phase Lease
  -> 执行工具，VFS 副作用路径继续检查文件访问范围
  -> 停用并结算 Phase Lease
  -> 将输出 provenance、Context 和 terminal record 一并 commit
  -> 将 Execution Contract 节点标记为完成，结束普通操作
  -> 通过 copyout 将响应写回 Guest
```

操作尚未生效便失败时，状态码直接说明原因，此前 reserve 的资源额度和工作流记录槽位会退还或丢弃。操作生效后，Context terminal record 与工作流记录票号在同一次 commit 中发布，避免出现“文件已改、记录未写”的中间状态。

一条完整工作流按以下顺序运行：

1. 可信引导进程创建工作流根进程，内核分配生命周期键、动态 VFS 文件访问范围和 Workflow Credit Domain。
2. 具有 `AGENT_CAP_ORCHESTRATE` 能力位的 Agent，按可信映像和能力上限创建同一生命周期内的工作进程。
3. 各 Agent 发布 Context，登记元数据，安装 Typed Watch 或 Execution Contract。
4. Agent 通过 V2、V3、batch 或 Task Channel（SQ/CQ）执行工具，并处理文件、消息和定时器事件。
5. 工作流 EEVDF 在多个工作流之间分配 CPU 时间；选中工作流后，仍由 uCore 选择具体线程。
6. 控制进程在阶段结束时生成 Workflow Fence 回执，随后关闭各成员。
7. 最后一个成员和后台任务结束后，生命周期收尾程序回收订阅、队列、Workflow Credit Domain 和调度对象，普通文件访问范围随之回收。标记 `preserve_on_retire` 的 artifact 会保留元数据、文件和存储计费，同时解除与旧生命周期 generation 的绑定。

## 七、Guest Runtime 与应用

内核 ABI 只传递结构化状态和系统操作，具体 Provider 协议由 Host 处理。`agentlive_ucore` 通过串口帧发送模型请求、接收工具调用，并把 terminal state 写入 Context。[`host_tools/agentos_relayd.py`](../host_tools/agentos_relayd.py) 负责 TLS、Provider JSON 和本地运行目录。更换模型 Provider 时无需修改内核 ABI。

`agentnexus_ucore` 以任意非空用户输入建立每轮 root Task，并把模型放在一组通用的 Harness 规则与工具中运行。公开工具只有三个：`search_files` 和 `read_file` 只读访问当前 Host 工作区，`inspect_system` 查看当前 Guest 的系统状态。Coordinator 通过内核 Task Channel 的 `delegate_task` 把 child Task 交给 Research 或 System；目标 claim 后读取 capsule 所绑定的 Guest artifact，完成时发布结果 artifact，并由唯一的 terminal CQE 通知 Coordinator 结算。Coordinator 等待 Contract 到达 `RECLAIMED` 后才恢复 observer/Host 事件投影、读取结果 artifact 并创建下一代。

Host workspace broker 只在显式配置的 root 内返回版本化 manifest 页面，并为 Guest 已通过 Metadata Catalog/Live Query 选定的候选执行正文匹配或返回 revision 绑定的分段字节。Guest 以 1 个 control inode 和最多 32 个 data-stub inode 维护当前 Catalog 窗口，按 4 个 stage 查询并复核完整路径；control stub 的 Typed Watch 负责在 generation 变化时使旧窗口失效。搜索和读取正文返回 Guest 后依次成为 Research 输入、结果 artifact、TOOL Context 与模型历史，Host 不私建、补写或替换 Provider 请求中的这段工具历史。Metadata Catalog 只负责有界候选选择，全文搜索仍由 Host 在候选内执行。Task ledger 记录 root/child Task 的关系、执行者身份和状态迁移。首版不提供编辑文件或执行 Shell 的工具，Research 与 System 是使用内核 Task Channel 协作的工作进程，不是独立子模型。Guest 主程序见 [`user/src/agentnexus_ucore.c`](../user/src/agentnexus_ucore.c)，Host 工作区访问见 [`host_tools/agentos_workspace.py`](../host_tools/agentos_workspace.py)，运行流程与命令见 [Workflow Runtime](modules/workflow-runtime.md)和[运行指南](usage.md)。

## 八、源码位置与检查方法

| 目录 | 内容 |
| --- | --- |
| [`os/`](../os/) | AgentOS 内核模块及其 uCore 接入点 |
| [`include/`](../include/) | 内核与 Guest 共用的版本化 ABI 和资源策略 |
| [`user/`](../user/) | Guest 封装、回归程序、Agent Loop 和 Nexus 应用 |
| [`host_tools/`](../host_tools/) | 串口协议、模型中继、控制台和 Host 测试 |
| [`scripts/`](../scripts/) | 静态结构检查、QEMU 回归、故障测试和日志校验 |
| [`one_shot_metrics/`](../one_shot_metrics/) | 性能测量的逐样本数据、提取器、校验器和绘图入口 |

模块依赖、UAPI 布局和完整 Guest 回归分别由以下命令检查：

```bash
make agent-module-check
make agent-uapi-check
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

Agent identity 与 Context 的建立过程见 [Agent 进程、地址空间与上下文路径](modules/identity-context.md)。工具、文件和 Agent Loop 分别见[结构化交互与工具调用协议](modules/tool-execution.md)、[面向 Agent 查询优化的文件系统扩展](modules/live-query.md)和 [Agent Loop 内核运行机制](modules/workflow-runtime.md)。
