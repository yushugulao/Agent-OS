# 双目标 uCore 科研 Agent 平台设计

本文档说明当前分支的总体设计。技术文档以中文为主要语言；命令、文件名、函数名、结构体名、状态字段和运行输出保持原文，便于和源码、测试、Host Reader 页面相互核对。

## 总体定位

当前分支包含两个目标：

- 根目录 AgentOS-uCore 目标：`os/` 是增强内核，科研 Agent 平台在关键阶段使用内核 Agent 服务。
- `baseline_ucore/` plain uCore 目标：保留未改动 uCore 内核，科研 Agent 平台运行在普通用户态进程和普通文件之上。

这种结构用于回答两个问题：

1. 不改内核时，一个复杂科研 Agent 平台可以依靠普通用户态机制完成到什么程度。
2. 加入通用 Agent 内核服务后，同一流程在哪些地方变得更可信、更直接、更容易恢复和审计。

## 目录和目标分离

AgentOS-uCore 目标位于仓库根目录：

```text
os/          增强内核
nfs/         文件系统镜像构建
scripts/     启动和辅助脚本
user/        AgentOS 测试程序和科研平台程序
host_tools/  Host Reader、动作运行器、文件系统提取器、LLM Relay
```

plain uCore 对照目标位于：

```text
baseline_ucore/os/       未改动 uCore 内核
baseline_ucore/user/     普通用户态科研 Agent 平台
baseline_ucore/nfs/      文件系统镜像构建
baseline_ucore/scripts/  启动辅助脚本
```

`baseline_ucore/` 不加入 Agent syscall、Agent Context、内核 metadata 服务或 Agent 事件队列。增强能力放在根目录目标中，这样点开仓库即可看到主要作品，同时仍保留普通 uCore 对照目标。

## 运行形态

plain target 的运行由五部分组成：

1. uCore 原始内核。
2. 恢复后的 uCore 用户库和程序构建流程。
3. `rp_plain`，用于展示平台目录、能力组、成熟平台映射和基础自检。
4. `rp_orch`，通过普通 `fork`、`exec`、`waitpid` 运行多角色科研平台程序。
5. `rp_seed_orch`，在 Host action runner 放入紧凑 `rp_host_action_seed` 后运行 seeded 程序集。

AgentOS target 使用 `rp_agentos_orch` 作为入口。它创建 orchestrator Agent，初始化 `rp_agentos_mainflow`，再运行与 plain target 可比较的科研流程。关键阶段会把内核事实写入 `rp_agentos_mainflow` 和相关 `rp_agentos_*` 文件。runbook 服务会读取事件通知、timeline、metadata 查询和恢复工具的内核状态后再生成事故处理记录；项目交付审查会读取文件 metadata、事件、Context 和 provenance 状态后再生成交付审查记录；研究协议服务会读取 metadata 索引、Context 记录、事件队列和批量工具状态后再生成协议启动、复现实验包和数据集操作记录；控制面会读取 capability、事件投递、Agent 角色启动和工具调用账本后再生成控制面记录；运营面板会读取事件队列、Context 记录、capability 拒绝和通用恢复动作后再生成执行队列和交接记录；正式评审面板会读取 Agent 角色创建、Context 记录、事件投递、文件 metadata 索引和 provenance 状态后再生成签核记录；完整性检查会读取 Context、metadata、事件和 provenance 状态，连贯性检查会读取运行状态、工具表、交付 metadata 和 Agent 协作事件，出版工作流会读取投稿 metadata、评审事件、响应 Context 和发布控制状态；成熟平台映射程序 `rp_mature` 在 AgentOS target 中会先读取这些真实状态文件，再把 Context Path、metadata 索引、事件队列、批量工具运行、capability 检查和证据投影标记为 observed。

## 操作系统内核机制

当前分支涉及的内核机制集中在启动、进程、内存、文件、系统调用、同步和 QEMU 适配上。plain target 和 AgentOS target 使用同一套 uCore 基础路径，只是在 AgentOS target 中增加 Agent 相关内核服务。

1. 启动流程、异常处理、中断处理、系统调用处理、上下文切换。

   `os/entry.S` 建立早期执行环境，进入 C 内核初始化后由 `os/proc.c` 初始化进程和线程表，再通过 `usershell` 或指定 `INIT_PROC` 进入用户程序。异常、中断和用户态陷入由 `os/trap.c`、`os/trap.h`、trapframe 和 trampoline 约定处理。系统调用在用户态把编号放入 `a7`，参数放入 `a0..a5`，进入 `os/syscall.c` 后分发处理，返回值写回 `a0`。上下文切换由 `scheduler()`、`sched()`、`yield()` 和线程 `context` 完成。

   AgentOS-uCore 没有绕开这些基础路径。Agent syscall 仍走同一 trap/syscall 入口，Agent wait、timeline wait、事件唤醒仍依赖进程状态切换和调度器。

2. 进程、线程、调度、`fork`、`exec`、`wait` 等进程管理机制。

   plain target 中 `rp_orch` 启动多个普通程序并等待结果，覆盖 `fork()`、`exec()`、`wait()`、`exit()`、`allocthread()`、trapframe 复制、用户栈布置和文件描述符继承等路径。AgentOS target 在同一进程模型上增加 role、capability、Agent Context、批量工具调用和 Agent 调度证据。Agent 进程仍是普通进程体系上的增强形态，而不是独立运行时。

3. 虚拟内存、地址空间、地址翻译、页表、缺页处理、权限检查。

   `os/vm.c` 提供 `uvmcreate()`、`mappages()`、`uvmcopy()`、`uvmunmap()`、`copyin()`、`copyout()`、`copyinstr()` 等能力；`os/loader.c` 负责用户程序加载；系统调用必须通过 copy 系列函数访问用户地址。AgentOS target 在此基础上加入固定 Agent Context 映射、内核 shadow 权威页、用户可读镜像页和 user-owned cache 区。可信历史由内核维护，用户直接写镜像不能伪造可信 Context。

4. 文件系统、目录、文件描述符、pipe、设备文件和文件抽象。

   plain target 通过普通文件保存科研平台状态，实际覆盖 inode 分配、目录查找、文件描述符分配、读写、关闭、pipe、console 和 virtio block 设备路径。相关源码包括 `os/fs.c`、`os/file.c`、`os/pipe.c`、`os/console.c`、`os/virtio_disk.c`。AgentOS target 把文件 metadata 绑定到真实 `dev/inum`，使用 `.agentmeta` 作为内核私有后端，并在创建、写入、截断、删除时维护 metadata、digest cache 和编辑租约。

5. Linux syscall 功能、ABI 兼容、参数传递、返回值、错误码和极端输入。

   `os/syscall_ids.h` 保留 Linux/RISC-V syscall 编号体系；`os/syscall.c` 实现教学内核范围内的 `read`、`write`、`openat`、`close`、`clone`、`execve`、`wait4`、`sched_yield`、线程和同步接口。AgentOS syscall 遵循相同 ABI 原则：先校验用户输入和输出缓冲，再执行会产生副作用的工具；错误参数、坏用户指针、未知工具、无权限操作不能污染 Context、mailbox、metadata 或 ledger。

6. 并发同步、资源管理、死锁处理、竞态处理、内核态与用户态隔离。

   `os/sync.c`、`os/sync.h` 提供 mutex、semaphore、condvar 等同步接口。进程、线程、文件、inode、pipe 和 buffer cache 都有明确的生命周期和引用管理。plain target 故意把 Agent 状态放在普通文件中，以展示用户态约定的局限；AgentOS target 则把 role/capability、Context、事件队列、wait/wake、heartbeat、metadata、edit lease、timeline、ledger 放入内核状态，并由内核检查真实权限和对象状态。

7. QEMU 与 RISC-V 平台适配和行为一致性。

   两个目标都运行在 RISC-V QEMU 环境。`os/riscv.h` 封装 CSR、页表项和特权级操作；`os/sbi.c`、`os/sbi.h` 通过 SBI 访问平台服务；`os/timer.c`、`os/trap.c` 处理 timer 和 trap；`os/virtio_disk.c`、`os/virtio.h`、`os/bio.c` 支撑 virtio block 设备和文件系统镜像。因此比较结果主要反映内核机制差异，而不是平台差异。

## 宿主机服务与 uCore 内程序的分工

Host 侧负责浏览器页面、动作提交、可选云端 LLM Relay 和文件系统镜像提取。uCore 内程序负责生成普通 `rp_*` 状态文件。两者之间通过文件协议连接：

- Host action runner 生成 `rp_host_action_seed`。
- `rp_seed_orch` 读取 seed，运行对应平台程序。
- 平台程序写入 `rp_input`、`rp_runner`、`rp_report_text`、`rp_artifact`、`rp_stage_state`、`rp_package`、`rp_agentcmp` 等状态文件。
- `host_tools/plain_ucore_fs_extract.py` 从 `nfs/fs-copy.img` 提取 `rp_*` 文件。
- `host_tools/plain_ucore_reader.py` 渲染 HTML 页面和 API JSON。

这种分工让 plain target 不需要修改内核，也能承载较复杂的平台表面；同时让 AgentOS target 可以复用同一状态文件协议进行对照。

## 核心状态文件

状态文件使用短文件名和 `key=value` 文本格式，便于 uCore 根目录和 Host Reader 共同处理。主要类别如下：

| 类别 | 代表文件 | 作用 |
| --- | --- | --- |
| 输入和研究任务 | `rp_input`、`rp_realtask` | 记录 RUN-042、CSV 数据、真实任务和报告输入 |
| 工作流 | `rp_stage_dag`、`rp_stage_state`、`rp_retry_plan`、`rp_cache_index`、`rp_run_events` | 表示 stage DAG、依赖、失败、重试、缓存和事件 |
| Artifact | `rp_artifact`、`rp_artifact_manifest`、`rp_stage_log`、`rp_chart_data` | 表示输入、派生产物、日志、图表和 manifest |
| Agent 协作 | `rp_planner`、`rp_agent_collab`、`rp_reviewer`、`rp_auditor` | 表示角色消息、决策、确认和审计 |
| LLM Relay | `rp_llm_req`、`rp_llmq`、`rp_llm_resp`、`rp_llm_packets`、`rp_llm_guard` | 表示请求、路由、响应、packet 检查和 guard |
| Workbench/Project | `rp_workbench`、`rp_usable`、`rp_usableproj`、`rp_projectrel` | 表示工作台、项目空间、交付和项目评审 |
| Review/Delivery | `rp_review_dashboard`、`rp_review_pack`、`rp_package`、`rp_nbexec` | 表示评审材料、交付包、notebook 和证据包 |
| Compare | `rp_backend`、`rp_backend_exec`、`rp_study`、`rp_agentcmp` | 表示 plain 成本、AgentOS 替代路径和比较结果 |
| AgentOS 证据 | `rp_agentos_mainflow`、`rp_agentos_query`、`rp_agentos_recovery`、`rp_agentos_timeline`、`rp_agentos_audit`、`rp_agentos_conflict` | 表示增强内核主流程事实和专项输出，包括 Context、通用依赖图、metadata 查询、事件、恢复、审计、provenance 和文件编辑租约 |

## 页面查看器：Host Reader

Host Reader 应让评委不用手工打开状态文件就能查看证据：

- `index.html`：`Dual Target Overview`，展示 plain target、AgentOS target、共享 run 和 Compare 入口。
- `run.html`：RUN-042 报告、backend case narrative、report source map。
- `workflow.html`：stage、cache、retry、worker、event、artifact 链路。
- `workbench.html`：任务状态、写作输出、manifest、文件校验和 bundle。
- `project.html`、`project-review.html`：项目空间、release、snapshot、reproducibility、delivery。
- `compare.html`：plain 成本、AgentOS 替代路径、`AgentOS Main Flow Kernel Stages`、`AgentOS Kernel Output Files`。
- `llm.html`：请求、路由、响应、quality、guard、fallback。
- `delivery.html`、`artifacts.html`、`review.html`：证据包、artifact、评审包和交付记录。

## 设计取舍

- plain target 只使用用户态文件和普通 syscall，因此能展示用户态 Agent 平台的上限，也暴露重复扫描、状态可信性弱、恢复步骤长等问题。
- AgentOS target 不把科研平台写死进内核，而是提供可复用的 Agent 原语：Context、metadata、event、timeline、ledger、capability、batch tool。
- 两个目标共享状态文件名和评审页面，使比较结果能被同一 Host Reader 呈现。
- 云端 LLM 只在 Host Relay 中处理，密钥不进入 uCore 镜像。
- 许可证文本和命令输出保持原文；技术解释使用中文。
