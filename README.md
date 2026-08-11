<p align="center">
  <img src="docs/assets/agentos_logo.png" alt="AgentOS-uCore" width="680">
</p>

# AgentOS-uCore：面向 AI 智能体的 uCore 内核扩展

## 一、基本信息

### 1.1 项目信息

| **项目** | **内容** |
| --- | --- |
| **比赛** | 2026 年全国大学生计算机系统能力大赛操作系统设计赛（全国）OS 功能挑战赛道 |
| **选题编号** | project61 |
| **赛题名称** | 面向 AI 智能体的操作系统内核（Agent-OS） |
| **作品名称** | AgentOS-uCore |
| **队伍名称** | happy-legend |
| **代码仓库** | [project3136859-388870](https://gitlab.eduxiji.net/T2026106149911107/project3136859-388870) |

### 1.2 摘要

**AgentOS-uCore 是一组面向 AI 智能体的 uCore 内核功能。我们从 RISC-V uCore 出发，在进程、文件系统、等待队列和调度器中加入智能体身份、运行上下文、结构化工具、文件状态、事件处理、资源管理和工作流调度。上层程序可以直接使用这些内核能力组织多轮任务和多智能体协作。**

内核用工作流编号和代次号（`{id, generation}`）区分每次运行。创建智能体时，系统会分配角色、能力位和文件访问范围。上下文记录每一步的起因、调用跨度、分支和数据来源。执行约定列出工具、前置任务、输入指纹、截止时间和资源上限。文件实时查询借助元数据索引和带类型信息的订阅，及时报告文件集合的变化。工作流资源账户和 EEVDF 调度器分别管理跨进程资源与 CPU 时间。Agent Live 控制台和 Nexus 把这些功能组合成可在 QEMU RISC-V64 中直接运行的应用。

我们共完成 30 次独立的 QEMU 启动，保存 33 份原始输出、19 个 CSV 数据表和 7,498 条样本。在 96 条文件记录上的 16 组配对测试中，索引查询的核心阶段每次都快于逐项扫描，中位加速比为 3.118 倍。504 次工作流唤醒从进入可运行状态到真正获得 CPU，等待时间均为 0 至 1 个时钟滴答。

### 1.3 完成情况

| **赛题任务** | **完成情况** | **对应成果** |
| --- | :---: | --- |
| 智能体进程创建与地址空间设计 | **已完成** | 受控创建工作流与智能体，校验可信映像，分配角色、能力位和访问范围，映射 7 页上下文，并用代次隔离前后两次运行 |
| 智能体与内核的结构化交互 | **已完成** | 25 项工具目录、带类型信息的 V2 请求、ENFORCE V3、最多 64 项的紧凑批处理、16 槽任务通道（SQ/CQ） |
| 上下文路径管理 | **已完成** | 记录序号、起因、调用跨度和分支，支持快照、详情查询、回滚、来源标记和活动路径检查 |
| 面向智能体查询的文件系统扩展 | **已完成** | 容量为 512 条的元数据目录、`status/stage/kind` 索引、inode 实例代次、带类型信息的订阅、重新同步和编辑租约 |
| 智能体循环的内核运行机制 | **已完成** | 事件队列、无丢失唤醒的等待接口、可信 IPC、心跳、资源账户和工作流 EEVDF |
| 综合演示与创新 | **已完成** | Agent Live 多轮工具调用、Nexus 四智能体协作、普通 uCore 与 AgentOS-uCore 对照测试，以及逐样本性能测试 |

### 1.4 主要创新

1. **用一套生命周期管理整条工作流。** 智能体、上下文、文件订阅、执行约定、任务通道、资源账户和阶段快照都带有 `{id, generation}`。系统据此识别同一次运行，并在关闭后按顺序回收各类对象。
2. **让内核检查每次工具调用。** 工具真正修改系统状态之前，内核会检查参数类型、能力位、任务前置关系、尝试次数、截止时间、输入指纹、数据来源和资源额度。单次调用、批处理和 SQ/CQ 最终走同一条工具执行路径。
3. **按文件状态查询并接收变化通知。** 文件的业务字段与 VFS 中的实际身份一同登记到元数据目录。索引先缩小查找范围，带类型信息的订阅再把 `ENTER`、`UPDATE`、`LEAVE` 等集合变化送入智能体事件循环。
4. **以工作流为单位管理资源和 CPU 时间。** 资源额度分为空闲、预留、已用三种状态。EEVDF 调度器把同一工作流的多个进程合在一起记账，再按虚拟截止时间选择下一个工作流。
5. **保留可追查的上下文与阶段记录。** 工具结果、文件内容和跨智能体消息都携带来源标记。控制进程可在阶段结束时取得上下文最终状态、文件元数据代次、资源用量和执行记录摘要。

### 1.5 团队成员

| **成员** | **联系方式** | **项目工作** |
| --- | --- | --- |
| 王浩沣 | QQ 2091576055 | AgentOS-uCore 内核、应用、测试与文档 |
| 康俊豪 | QQ 488718235 | AgentOS-uCore 内核、应用、测试与文档 |

### 1.6 文档索引

- [AgentOS-uCore：面向 AI 智能体的 uCore 内核扩展](#agentos-ucore面向-ai-智能体的-ucore-内核扩展)
  - [一、基本信息](#一基本信息)
    - [1.1 项目信息](#11-项目信息)
    - [1.2 摘要](#12-摘要)
    - [1.3 完成情况](#13-完成情况)
    - [1.4 主要创新](#14-主要创新)
    - [1.5 团队成员](#15-团队成员)
    - [1.6 文档索引](#16-文档索引)
  - [二、项目概述](#二项目概述)
    - [2.1 背景与意义](#21-背景与意义)
    - [2.2 核心挑战](#22-核心挑战)
    - [2.3 总体架构](#23-总体架构)
    - [2.4 核心模块](#24-核心模块)
  - [三、系统设计与实现](#三系统设计与实现)
    - [3.1 身份、生命周期与上下文](#31-身份生命周期与上下文)
    - [3.2 结构化工具与执行约定](#32-结构化工具与执行约定)
    - [3.3 文件实时查询与文件事件](#33-文件实时查询与文件事件)
    - [3.4 事件、资源与工作流调度](#34-事件资源与工作流调度)
    - [3.5 Agent Live 与 Nexus](#35-agent-live-与-nexus)
  - [四、测试结果](#四测试结果)
    - [4.1 测试体系](#41-测试体系)
    - [4.2 文件实时查询性能](#42-文件实时查询性能)
    - [4.3 智能体任务延迟](#43-智能体任务延迟)
    - [4.4 工作流 EEVDF](#44-工作流-eevdf)
  - [五、总结与展望](#五总结与展望)
    - [5.1 工作总结](#51-工作总结)
    - [5.2 现有不足](#52-现有不足)
    - [5.3 后续工作](#53-后续工作)
  - [六、运行与文档](#六运行与文档)
    - [6.1 构建与回归](#61-构建与回归)
    - [6.2 文档体系](#62-文档体系)
    - [6.3 视频与 PPT](#63-视频与-ppt)
  - [七、参考说明与许可](#七参考说明与许可)
    - [7.1 基础项目与设计参考](#71-基础项目与设计参考)
    - [7.2 开源许可](#72-开源许可)
  - [八、项目目录索引](#八项目目录索引)

## 二、项目概述

### 2.1 背景与意义

传统操作系统主要管理进程、地址空间、文件和系统调用。智能体程序还要记住每个执行者的身份、多轮对话和工具权限，追踪输入来自哪里、文件处理到哪个阶段，并协调多个智能体之间的任务。如果这些工作全部留给应用层，各进程会各自保存一份状态，工作流重启后也很难判断哪些旧对象需要回收。

uCore 已经提供了进程、虚拟内存、VFS、IPC、调度器、时钟中断和 VirtIO。我们把 AgentOS 接到这些现有路径上：内核可以认出同一工作流中的成员，在工具或文件操作生效前检查权限，并把文件变化、工具结果和协作消息送入同一个智能体事件循环。原有 uCore 程序仍使用原来的接口，智能体程序则通过新增 ABI 调用 AgentOS。

### 2.2 核心挑战

我们结合赛题要求、智能体程序的运行特点和 uCore 的现有机制，把实现工作分成五项：

1. **建立可以回收的智能体身份。** 内核需要保存角色、能力位、文件访问范围和控制关系。工作流槽位再次使用后，旧句柄和迟到请求必须失效。
2. **保存多轮任务的上下文。** 工具结果、文件来源和跨智能体消息都要留下记录。并发写入、分支回滚和定长记录区不能破坏读取结果。
3. **在工具执行前完成检查。** 内核要先检查请求参数、权限、前置任务、截止时间、数据来源和资源额度，再允许工具修改系统状态。不同调用方式应得到相同的执行结果。
4. **让智能体及时看到文件变化。** 查询条件既包含业务字段，也要跟随 inode 的创建、写入、截断、删除和复用。文件集合发生变化后，内核把变化转换为事件。
5. **按工作流统计资源并分配 CPU。** 一个任务可能包含多个智能体进程。资源用量、等待时间和 CPU 服务量都应合并到所属工作流，避免多建线程就获得额外份额。

### 2.3 总体架构

<p align="center">
  <a href="docs/figures/architecture/agentos_overview.png">
    <img src="docs/figures/architecture/agentos_overview.png" alt="AgentOS-uCore 总体架构" width="1000">
  </a>
</p>

AgentOS-uCore 自下而上分为四层。uCore 基础内核负责进程、内存、VFS、IPC、调度和设备 I/O。AgentOS 内核模块负责身份、上下文、工具执行、文件实时查询、事件、资源和工作流调度。用户态 UAPI 与客户机运行库将这些内核接口封装起来，供应用程序调用。Agent Live、Nexus 和科研工作流负责拆分目标、选择工具并安排各阶段协作。

这些状态都跟随 uCore 对象一起变化。进程创建并完成 `exec` 后，内核发布智能体身份。建立地址空间时映射上下文，VFS 修改文件时同步更新元数据。进程进入队列、获得 CPU、睡眠或经历时钟中断时，调度器更新工作流状态。进程退出后，系统依次回收订阅、队列、资源账户和生命周期槽位。各模块的接入位置见[系统架构](docs/architecture.md)，原生图源见 [`agentos_overview.drawio`](docs/figures/architecture/agentos_overview.drawio)。

### 2.4 核心模块

| **模块** | **关键机制** | **产品能力** |
| --- | --- | --- |
| 身份与上下文 | 可信映像、角色和能力位、文件访问范围、工作流代次、7 页上下文 | 受控创建智能体，记录每一步的起因、调用跨度、分支和数据来源 |
| 工具执行 | 25 项工具目录、V2/V3、紧凑批处理、任务通道（SQ/CQ） | 工具生效前检查请求，并按任务特点选择调用方式 |
| 文件实时查询 | 元数据目录、三类等值索引、带类型信息的订阅、重新同步 | 按业务状态查找文件，并根据文件集合变化触发后续工作 |
| 工作流运行时 | 事件队列、可信 IPC、心跳、资源账户、工作流 EEVDF | 让智能体休眠等待事件，按工作流管理资源与 CPU 时间 |
| 阶段快照 | 执行记录环、上下文提交通道、工作流阶段快照 | 汇总执行记录、文件元数据代次和资源用量，并返回快照回执 |

各模块的设计见[身份、生命周期与上下文](docs/modules/identity-context.md)、[结构化工具与执行约定](docs/modules/tool-execution.md)、[文件实时查询](docs/modules/live-query.md)和[工作流运行时](docs/modules/workflow-runtime.md)。

## 三、系统设计与实现

### 3.1 身份、生命周期与上下文

可信引导进程创建工作流根进程，内核为这次运行分配新的 VFS 访问范围、资源账户和生命周期代次。同一工作流中，具有 `AGENT_CAP_ORCHESTRATE` 能力位的智能体可以创建工作进程。工作进程申请的能力位不能为空，只能从 `AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE` 中选择，而且不能超出调用者已有的能力。内核还会检查目标映像的 VFS 执行权限和可信策略。

```text
agent_workflow_create()
    -> SYS_agent_workflow_create
    -> sys_agent_workflow_create()
    -> agent_workflow_create_proc()
    -> fork_common(... PROC_ADMIT_WORKFLOW, VFS_SPAWN_SCOPE_FRESH)

agent_worker_create(image, capabilities)
    -> sys_agent_worker_create()
    -> namei_scope_status() + vfs_inode_authorize()
    -> agent_worker_create_proc()
    -> fork_common(... PROC_ADMIT_WORKER, VFS_SPAWN_SCOPE_DROP)
```

每个智能体映射 7 页上下文：前 6 页由内核写入并向用户态只读开放，最后 1 页留给客户机程序缓存派生数据。记录区固定保存 128 条记录，每条记录包含序号、起因、调用跨度、分支、工具、状态和来源标记。读取快照时，程序比较发布序号，发现写入并发便重新读取。回滚只移动当前分支的可见头。工具、文件和 IPC 统一通过同一条上下文提交通道写入结果。

实现代码见 [`os/proc.c`](os/proc.c)、[`os/agent_identity.c`](os/agent_identity.c)、[`os/workflow_lifecycle.c`](os/workflow_lifecycle.c)、[`os/agent_context.c`](os/agent_context.c)、[`os/agent_context_path.c`](os/agent_context_path.c) 和 [`os/agent_provenance.c`](os/agent_provenance.c)。

### 3.2 结构化工具与执行约定

工具目录为每项工具登记名称、编号、参数类型、所需能力位和可能产生的系统改动。V2 请求最多带 8 个参数。V3 在 V2 的基础上增加约定编号、任务节点、尝试次数、参数摘要、输入指纹、来源上下文和结果类型。每份执行约定最多包含 24 个节点，每个节点最多尝试 4 次。

```text
tool_call_v3()
    -> SYS_tool_call
    -> sys_tool_call_v3()
    -> copyin 与版本/长度检查
    -> 智能体身份与生命周期绑定检查
    -> agent_tool_protocol_resolve() + agent_tool_protocol_decode_v2()
    -> workflow_lifecycle_operation_enter()
    -> agent_execute_one()
    -> 检查执行约定、能力位、来源、本次执行额度和系统改动
    -> 提交上下文、执行记录与响应
```

紧凑批处理一次可同步提交 64 项操作。任务通道使用 16 槽 SQ/CQ。SQ 和 CQ 各占一页，映射给客户机程序。请求状态和资源状态另占两页，只由内核访问。请求编号、队列代次和槽位代次共同判断队列是否已经复用。每个被接纳的目标只产生一个最终 CQE，取消命令不会再产生第二个 CQE。测试覆盖了保留最终记录的幂等取消和硬截止时间。CQ 写满后，内核暂停提交。协议出错后保持重新同步状态，直到新代次建立并显式重置。四种调用方式最后都进入同一工具执行函数，因此更换传输方式不会改变工具行为。

实现代码见 [`os/agent_core.c`](os/agent_core.c)、[`os/agent_tool_protocol.c`](os/agent_tool_protocol.c)、[`os/agent_execution_contract.c`](os/agent_execution_contract.c)、[`os/agent_task_channel.c`](os/agent_task_channel.c) 和 [`os/agent_task_bridge.c`](os/agent_task_bridge.c)。公开结构见 [`include/agent_tool_abi.h`](include/agent_tool_abi.h)、[`include/agent_execution_contract_abi.h`](include/agent_execution_contract_abi.h) 与 [`include/agent_task_channel_abi.h`](include/agent_task_channel_abi.h)。

### 3.3 文件实时查询与文件事件

智能体调用 `agent_file_meta_set()` 登记文件所属项目、工作流、运行批次、处理阶段、类别、状态和摘要。内核同时保存 `dev + inum + incarnation`。inode 再次使用时，新实例代次会隔开旧元数据、内容回执和编辑租约。元数据目录为 `status`、`stage` 和 `kind` 建立等值索引。索引只负责缩小范围，返回结果前仍会逐项核对完整条件、访问范围、生命周期和目录代次。

```text
agent_file_query(query, result)
    -> sys_agent_file_query()
    -> 登记生命周期中的普通操作并检查 META_READ
    -> agent_file_query_internal()
    -> agent_metadata_query_execute_snapshot()
    -> 查询器选择逐项扫描或索引
    -> 逐个核对候选项，返回代次、查询方式与检查数量

VFS metadata/write/truncate/unlink
    -> 比较带类型信息的订阅集合
    -> ENTER / UPDATE / LEAVE / RESYNC_REQUIRED
    -> 智能体事件队列
    -> agent_wait()
```

`agent_wait()` 在同一次关中断期间复查队列并登记等待者，事件不会落在“刚看完空队列、尚未睡眠”的空隙中。队列出现缺口或增量事件投递失败后，智能体进入重新同步。它先保留旧订阅，安装一份条件相同的新订阅，再取得一份未截断的有界快照，随后确认并移除旧订阅。这样，切换期间发生的变化仍由新订阅接住。单次查询最多返回 8 项。若结果被截断，系统保持重新同步状态，调用者需要收紧查询条件，或等待以后加入分页接口。

实现代码见 [`os/agent_metadata_catalog.c`](os/agent_metadata_catalog.c)、[`os/agent_metadata_query.c`](os/agent_metadata_query.c)、[`os/agent_metadata_objects.c`](os/agent_metadata_objects.c)、[`os/agent_live_query_events.c`](os/agent_live_query_events.c)、[`os/agent_metadata_actions.c`](os/agent_metadata_actions.c) 和 [`os/agent_ipc.c`](os/agent_ipc.c)。

### 3.4 事件、资源与工作流调度

每个智能体有一个 16 槽事件队列，其中一部分槽位专门留给控制事件和不同来源的事件。跨智能体 `MESSAGE` 与 `LLM_DONE` 会检查消息路由、完整生命周期、发送方能力、接收方代次和关联编号。心跳、截止时间、文件变化和策略拒绝都通过 `agent_wait()` 交给智能体循环。

工作流资源账户管理进程、线程、文件对象、文件系统块、inode、缓冲区、智能体状态页和物理页。每份额度分为空闲、预留、已用三种状态。创建对象时先从空闲额度中预留，成功发布后记为已用。创建失败则退回空闲，对象销毁后也归还空闲额度。工具运行时，内核还会暂时锁定本次执行所需的资源，结束后再统一结算。

工作流 EEVDF 把同一工作流中的进程合成一个外层调度对象。延迟等级和实际截止时间决定每次申请的 CPU 时间。进程睡眠后，调度器按规则修正 `vruntime`。选择任务时，系统从当前可以运行的工作流中挑选虚拟截止时间最早者。同一工作流的成员共用 CPU 周期账户。选中工作流后，再由 uCore 原有策略挑选具体线程。

```text
事件入队 -> 唤醒等待队列 -> 进程进入可运行状态
    -> workflow_scheduler_on_enqueue()
    -> workflow_scheduler_select()
    -> workflow_scheduler_on_dispatch()
    -> workflow_scheduler_charge()
    -> 睡眠或离队时更新 vruntime 和可运行条件
```

实现代码见 [`os/agent_ipc.c`](os/agent_ipc.c)、[`os/agent_background.c`](os/agent_background.c)、[`os/workflow_credit_domain.c`](os/workflow_credit_domain.c)、[`os/resource_controller.c`](os/resource_controller.c)、[`os/workflow_scheduler.c`](os/workflow_scheduler.c) 和 [`os/proc.c`](os/proc.c)。

### 3.5 Agent Live 与 Nexus

`agentlive_ucore` 运行一个常驻的智能体循环。客户机保存轮次、关联编号、工具目录、上下文和审批状态。宿主机中继负责转发串口帧、建立 TLS 连接，并与模型服务交换 JSON。固定回放和真实模型使用同一套串口协议，便于用固定输入检查多轮工具调用。

`agentnexus_ucore` 在同一工作流中创建协调、系统观察、资料检索和分析四个智能体。协调智能体建立任务并分派工作。各工作进程通过内核 `MESSAGE` 和工作流内的结果文件传递信息。节点失败时重新安排任务，分析智能体负责汇总最终报告。核心应用代码位于 [`user/src/agentlive_ucore.c`](user/src/agentlive_ucore.c)、[`user/src/agentnexus_ucore.c`](user/src/agentnexus_ucore.c) 和 [`user/lib/agent_nexus.c`](user/lib/agent_nexus.c)，宿主机接入代码位于 [`host_tools/agentos_console.py`](host_tools/agentos_console.py) 与 [`host_tools/agentos_relayd.py`](host_tools/agentos_relayd.py)。

## 四、测试结果

### 4.1 测试体系

我们从 ABI、宿主机状态机、RISC-V64 客户机、常驻应用和双系统对照五个层次检查 AgentOS-uCore。

| **层次** | **入口** | **覆盖内容** |
| --- | --- | --- |
| ABI 与模块契约 | `agent-uapi-check`、`agent-module-check` | 系统调用号、结构大小与字段偏移、模块依赖和状态转换 |
| 宿主机自测 | `local-host-selftests` | 上下文、执行约定、资源、查询、调度、串口协议和校验器 |
| QEMU 客户机 | `agentos-test` | 身份、VFS、工具、事件、调度、内存、故障和生命周期 |
| 常驻应用 | 控制台与 Nexus 回放 | 多轮工具、审批、任务分派、重新规划、结果文件和会话关闭 |
| 系统对照 | `dual-platform-run` | 普通 uCore 与 AgentOS-uCore 的业务结果和端到端耗时 |

性能测试在 WSL2 宿主机上运行，客户机为 QEMU RISC-V64 `virt` 单 Hart，使用 `riscv64-linux-gnu-gcc 15.2.0` 编译，QEMU 版本为 `10.2.1`。30 次独立启动覆盖文件实时查询、智能体任务和工作流 EEVDF。测试清单、校验结果、原始串口输出和逐样本 CSV 均保存在 [`one_shot_metrics/data/20260811`](one_shot_metrics/data/20260811/)。测试入口和故障检查项见[测试文档](docs/testing.md)。

### 4.2 文件实时查询性能

我们在同一组 96 条文件记录上做了 16 组 AB/BA 配对。逐项扫描每次检查 97 条记录，索引查询只检查 2 条。核心耗时从发起查询开始，包含恢复写入、`fsync` 和结果复核。

| **指标** | **逐项扫描** | **索引查询** | **配对结果** |
| --- | ---: | ---: | ---: |
| 核心阶段中位耗时 | 34,712.5 微秒 | 13,293.5 微秒 | 16 组中索引查询全部更快 |
| “索引查询减逐项扫描”的中位差值 | - | - | -23,441.5 微秒 |
| “逐项扫描耗时除以索引查询耗时”的中位数 | - | - | 3.118 倍 |
| 完整流程中位耗时 | 711,283.5 微秒 | 723,928.0 微秒 | 配对差值 +13,452 微秒 |
| 索引查询在完整流程中更快的配对数 | - | - | 3 组（共 16 组） |

我们又组合了 24、64、96 三种目录规模和 1、2、4、8 四种命中数量，每种组合保留 15 个内部配对。12 种组合的中位加速比为 1.164 至 2.808 倍。在三种目录规模下，索引已经建立时的单次查询中位耗时分别约为 98.3、100.5 和 108.3 微秒。配对分布和不同规模下的结果见[性能测试](docs/performance.md#3-实时查询的核心阶段)。

### 4.3 智能体任务延迟

每组任务连续执行 16 个等价的 `ECHO` 操作。4 次独立启动各运行 8 轮，批处理、逐项 V3 调用和 SQ/CQ 分别得到 32 组样本，并保留每个操作的记录。

| **路径** | **样本数** | **中位数** | **四分位距（IQR）** |
| --- | ---: | ---: | ---: |
| 批处理 | 32 | 561.0 微秒 | 533.75 至 663.0 微秒 |
| 逐项 V3 调用 | 32 | 2,051.0 微秒 | 1,833.0 至 2,226.0 微秒 |
| SQ/CQ | 32 | 1,620.5 微秒 | 1,472.0 至 1,755.5 微秒 |

在这组同步 `ECHO` 任务中，批处理把连续操作合并提交，中位耗时最低。SQ/CQ 通过共享队列返回最终 CQE，逐项 V3 调用则为每个操作单独执行完整约定检查。各组时延分布见[性能测试](docs/performance.md#6-智能体任务传输)。

### 4.4 工作流 EEVDF

6 次独立启动共记录 504 次准确唤醒，其中 425 次没有等待额外时钟滴答，79 次等待了 1 个滴答。并发工作流数量从 1 增加到 4 时，根据各工作流 `service_cycles` 计算出的 Jain 公平性指数中位数依次为 1.000000、0.999985、0.999993 和 0.999985。唤醒分布、公平性图、完整实验设计和逐样本数据入口见[性能测试](docs/performance.md#7-工作流-eevdf-调度)。

## 五、总结与展望

### 5.1 工作总结

我们在 uCore 中完成了智能体从创建到退出所需的主要内核功能。身份和生命周期负责管理成员及其关联对象，上下文保存多轮任务的起因与数据来源。工具协议和执行约定在操作生效前完成检查，文件实时查询把状态变化送入事件循环。资源账户和工作流 EEVDF 负责跨进程资源与 CPU 时间。Agent Live 与 Nexus 已经使用这些接口连续运行多轮任务。

项目保留了稳定 ABI、细分的客户机测试、宿主机状态机测试、故障注入、普通 uCore 与 AgentOS-uCore 对照测试，以及逐样本性能数据。每项主要功能都能从文档找到调用入口、实现文件和测试结果。

### 5.2 现有不足

任务通道的内核桥接目前同步处理 `null` 输入，完成结果的类型为 `NONE`，带类型信息的资源还没有覆盖更多真实工具输入。RISC-V64 客户机使用单 Hart，工作流 EEVDF 只测试了 1 至 4 个并发工作流。多 Hart 下的调度对象迁移和并行记账仍需补充测试。文件元数据目录最多保存 512 条记录，单次查询最多返回 8 项，更大目录还需要分页和复合索引。

### 5.3 后续工作

下一阶段将为任务通道加入异步工具桥接，并补充更多带类型信息的资源。文件实时查询需要增加分页、复合索引和多核并行处理。工作流调度还要继续调整延迟等级、截止时间和资源压力处理。Nexus 也会增加新的角色分工模板，并进行更长时间的真实模型测试。新负载仍按现有方法保存逐样本数据。

## 六、运行与文档

### 6.1 构建与回归

Linux 或 WSL 环境需要 Bash、Git、GNU Make、本机 C 编译器、Python 3、QEMU RISC-V 和 RISC-V GNU 工具链。Ubuntu 中的交叉编译工具通常使用 `riscv64-linux-gnu-` 前缀。

```bash
make doctor
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

Agent Live 与 Nexus 的固定回放会启动真实的 QEMU 客户机，并通过产品串口协议完成多轮交互：

```bash
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

交互式控制台、真实模型、Nexus、双系统运行和单项测试方法见[运行指南](docs/usage.md)。

### 6.2 文档体系

| **文档** | **主要内容** |
| --- | --- |
| [决赛文档](决赛文档.pdf) | AgentOS-uCore 的完整设计、实现与测试文档 |
| [系统架构](docs/architecture.md) | uCore 基础、AgentOS 内核模块、UAPI 与运行过程 |
| [系统调用与 ABI](docs/api.md) | 系统调用、版本化结构、状态码与调用顺序 |
| [安全机制](docs/security.md) | 身份、访问范围、代次、执行约定、数据来源和资源检查 |
| [运行指南](docs/usage.md) | 环境、构建、QEMU、控制台、Nexus 和双系统运行 |
| [测试体系](docs/testing.md) | 静态契约、客户机回归、故障测试、回放和复现入口 |
| [性能测试](docs/performance.md) | 实验设计、统计结果、图表与逐样本数据 |
| [身份、生命周期与上下文](docs/modules/identity-context.md) | 智能体身份、工作流生命周期、上下文路径和来源标记 |
| [结构化工具与执行约定](docs/modules/tool-execution.md) | 工具目录、V2/V3、批处理、任务通道和工具执行资源记录 |
| [文件实时查询](docs/modules/live-query.md) | 元数据目录、索引、带类型信息的订阅、重新同步和编辑租约 |
| [工作流运行时](docs/modules/workflow-runtime.md) | 事件、IPC、资源、EEVDF、执行记录和阶段快照 |

### 6.3 视频与 PPT

| **材料** | **下载入口** |
| --- | --- |
| AgentOS 项目介绍视频 | [百度网盘](https://pan.baidu.com/s/1JQKpght9NQuLC5d4VH_9ZQ?pwd=agos)，提取码 `agos` |
| AgentOS-uCore 展示幻灯片 | [百度网盘](https://pan.baidu.com/s/1odSO5Z_3zVGITqAJRSzdgQ?pwd=8s7c)，提取码 `8s7c` |
| 仓库内链接记录 | [项目介绍视频和ppt网盘链接.txt](项目介绍视频和ppt网盘链接.txt) |

## 七、参考说明与许可

### 7.1 基础项目与设计参考

AgentOS-uCore 基于 LearningOS 的 [uCore-Tutorial-Code-2025S](https://github.com/LearningOS/uCore-Tutorial-Code-2025S) 与 [uCore-Tutorial-Test-2025S](https://github.com/LearningOS/uCore-Tutorial-Test-2025S) 开发。系统设计还参考了 Linux EEVDF 和等待队列、Haiku BFS 的文件属性与即时查询、BPF 环形缓冲区、io_uring SQ/CQ、WebAssembly 组件模型、MCP 和 A2A。具体来源、用途与链接见 [`NOTICE`](NOTICE)。

### 7.2 开源许可

仓库源代码采用 [GNU General Public License v3.0](LICENSE)。技术文档、架构说明和展示材料采用 [Creative Commons Attribution-ShareAlike 4.0 International](LICENSE-DOCS)。各上游项目及外部材料继续遵循其原有许可与声明。

## 八、项目目录索引

```bash
.
├── baseline_ucore/          # 普通 uCore 对照实现
├── ci/                      # 回放数据、ABI 冻结清单与验收配置
├── docs/                    # 产品文档、模块说明、架构图与性能图
├── host_tools/              # 控制台、模型中继、观察器与宿主机校验器
├── include/                 # 内核和客户机共用的 ABI 与资源策略头文件
├── nfs/                     # uCore 文件系统镜像生成工具
├── one_shot_metrics/        # 一次性性能负载、原始数据、表格与图表
├── os/                      # uCore 基础内核与 AgentOS 功能模块
├── scripts/                 # 构建检查、客户机运行器与故障测试脚本
├── user/                    # 客户机运行库、智能体应用与产品测试
├── 决赛文档.pdf             # 决赛完整产品文档
├── 项目介绍视频和ppt网盘链接.txt # 视频与答辩材料入口
├── LICENSE                  # GPL-3.0 源码许可
├── LICENSE-DOCS             # CC BY-SA 4.0 文档许可
├── Makefile                 # 构建、运行、测试与双目标入口
├── NOTICE                   # 上游来源与设计参考说明
└── README.md                # 项目总览与文档索引
```
