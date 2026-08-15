# 面向 AI 智能体的操作系统内核：测试说明

项目测试按实际调用顺序分三步进行：先检查公开 `ABI` 和模块调用关系，再检查 Host 协议与状态机，最后在 RISC-V64 QEMU Guest 中执行真实的系统调用、文件系统、调度、资源管理和工作流。随后运行故障注入和双平台对照，检查异常恢复与最终结果。

## 文档索引

- [1. 测试内容与入口](#1-测试内容与入口)
- [2. ABI 和源码检查](#2-abi-和源码检查)
- [3. Host 自测](#3-host-自测)
- [4. QEMU Guest 测试](#4-qemu-guest-测试)
- [5. 权限检查与故障恢复](#5-权限检查与故障恢复)
- [6. 交互会话测试](#6-交互会话测试)
- [7. 双平台对照测试](#7-双平台对照测试)
- [8. 专项性能结果与数据](#8-专项性能结果与数据)
- [9. 运行全部检查](#9-运行全部检查)

## 1. 测试内容与入口

| 检查内容 | 检查方法 | 预期结果 | 入口 |
| --- | --- | --- | --- |
| `ABI` 一致性 | RISC-V 探针、静态断言、固定的布局清单 | 内核态和用户态共用的结构、系统调用号和清单摘要一致 | `make agent-uapi-check` |
| 模块调用关系 | 检查源码归属、调用关系和 workflow fence | Context、Live Query、workflow fence 等代码仍在实际调用链中 | `make agent-module-check` |
| Host 控制程序 | 测试 Python 和 Shell 协议处理程序 | Console、Nexus 通用合约、Host 工作区 broker、Task ledger、Execution Contract 和资源账户能正确处理输入 | `make local-host-selftests` |
| Guest 功能 | 每个场景独立启动 QEMU，并执行真实的 RISC-V 系统调用 | Agent 身份、工具、Context、VFS、调度和 Task Channel 完成整个生命周期 | `make agentos-test` |
| 权限与恢复 | 越权输入、容量耗尽、故障注入和重启 | 非法请求被拒绝；恢复后状态一致，资源能够回收 | 各专项 Guest 测试和故障测试 |
| 交互会话 | controller、observer、固定回复或在线 Provider 配合运行 | Console 的工具/审批路径与 Nexus 的自主决策、工作区读取、多 Agent Task 和关闭顺序正确；在线自由演示能形成自然合理的回答 | Console replay、Nexus replay、Nexus 自由演示 |
| 业务结果 | 两套镜像使用同一输入和同一结果判定程序 | 普通 uCore 与 AgentOS-uCore 得到相同结果 | `make dual-platform-run` |
| 性能测试 | 多次独立启动；在同一次 QEMU 启动内配对；遍历参数组合 | 保存每个样本的用时、I/O、唤醒等待和公平性 | `one_shot_metrics/data/20260811` |

普通 Guest 测试由 [`scripts/agent_test_runner.py`](../scripts/agent_test_runner.py) 监视。程序会检查完成标志、退出状态、`panic`、意外错误、输出大小、空闲时间和总超时。每个测试场景还会核对指定的标志行，确认相关内核代码确实执行。Console 和 Nexus 的固定 replay 则由各自的 Make 命令启动 Host daemon、QEMU 和检查程序。

## 2. ABI 和源码检查

### 2.1 构建和结构布局

```bash
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
make agent-uapi-check TOOLPREFIX=riscv64-linux-gnu-
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`agent-uapi-check` 会编译 [`scripts/probes/agent-uapi-layout.c`](../scripts/probes/agent-uapi-layout.c)，读取各个结构的大小和字段偏移，再与 [`ci/agent-uapi-layout.json`](../ci/agent-uapi-layout.json) 对照。检查内容包括 lifecycle、Execution Contract、provenance、Task Channel、tool、workflow fence、性能计数和资源管理 `ABI`。

`kernel-stack-check` 读取 GCC 生成的 `.ci` 调用图，从系统调用和中断入口计算最深调用路径，并计入中断帧、保护区和预留空间。这样可以在链接前发现 AgentOS 调用链的栈空间变化。

### 2.2 模块调用关系

```bash
make agent-module-check TOOLPREFIX=riscv64-linux-gnu-
```

这条命令依次检查模块职责、Live Query 与文件系统的连接，以及 workflow fence。检查程序直接查找实际使用的函数和调用点，确认状态由指定模块管理，lifecycle generation 索引仍然有效，目录修改会经过 mutation barrier，resync 时仍能送达通知，销毁 workflow 时也会等到引用清理完成。

## 3. Host 自测

```bash
make local-host-selftests
```

[`scripts/run-parallel-tests.py`](../scripts/run-parallel-tests.py) 会并行运行 Host 协议测试、模型测试和源码约定检查，每项最长运行 900 秒。主要检查内容如下：

| 组件 | 检查内容 |
| --- | --- |
| Execution Contract | DAG 前驱关系、attempt、deadline、retry、effect 和 provenance 字段 |
| Context 与 provenance | 原子 commit、active path、snapshot 读取、provenance sequence，以及系统调用的来源记录 |
| Live Query | lifecycle generation、inode incarnation、索引调用、mutation barrier，以及 traversal/indexed 结果是否一致 |
| Task Channel | `SQ/CQ` 协议、single issuer、cancel、backpressure、resync、TASK route、claim/complete、终态 offer 确认和唯一 CQE |
| 工作流调度 | 资源账户、scheduler model、阻塞唤醒和资源记账 |
| Console 与 Nexus | 串口消息、本地 socket、controller/observer、任意用户任务、三工具自主选择、Task Channel 委派、Guest artifact、工作区结果与运行记录协议 |
| Nexus Host 工作区 | 显式 root、版本化 manifest、候选对象约束、stale 重启、有界搜索与分段读取，以及在线/replay 使用相同 Guest 工具历史 |
| 双平台工具 | 普通 uCore 与 AgentOS-uCore 的状态提取、结果比较和来源清单 |

每项自测都会自行构造输入。测试失败时会直接列出缺失字段、错误的状态变化或有问题的源码调用点。

## 4. QEMU Guest 测试

### 4.1 完整 Guest 测试

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

完整测试先把 22 个 Guest 程序编入用户镜像。随后，每个场景都会重新构建内核并独立启动 QEMU。全部通过后输出：

```text
[agent-tests] all Agent-OS uCore checks passed
```

主要场景和对应的内核行为如下：

| 内核功能 | Guest 程序 | 检查内容 |
| --- | --- | --- |
| Agent 身份与 lifecycle | `agentsecurity_ucore`、`agenttrust_ucore`、`agentscope_ucore` | 可信映像、角色创建、能力逐级收紧、文件访问范围隔离、lifecycle generation 和退出清理 |
| Context 与 provenance | `agentfinal_ucore` | commit lane、snapshot/detail、active path、rollback、FIFO 淘汰、timeline 和 provenance graph |
| Tool 与 Execution Contract | `agenttoolabi_ucore`、`agentcontract_ucore`、`agentllm_ucore` | schema、`V1/V2/V3 ABI`、DAG 前驱、attempt、deadline、retry、effect 和 `LLM` envelope |
| Live Query | `agentfs_ucore`、`agentscan_ucore`、`agentbench_ucore` | inode incarnation、metadata mutation、traversal/indexed 结果一致、typed watch 和 workload 计数 |
| Event Loop | `agentloop_ucore`、`blocking_semantics_ucore` | wait publication 原子性、heartbeat、广播隔离、cancel 和定点唤醒 |
| 工作流调度 | `agentsched_ucore`、`agent_eevdf_ucore`、`agentconflict_ucore` | 多个工作流并行推进、`EEVDF` 服务量、唤醒探针和冲突处理 |
| Task Channel | `agenttask_ucore` | Batch、Scalar V3、`SQ/CQ`、terminal `CQE`、backpressure、resync、UTF-8 快照导入、OWNED/BORROWED 生命周期、fd transaction pin、重复 `cancel` 和 hard deadline |
| VFS、结果发布与资源 | `agentvfs_ucore`、`agentpublish_ucore`、`iobudget_ucore`、`usersafety_ucore` | `fstat` 后重新授权、I/O 来源、结果文件原子接入、同名不覆盖、非法发布零副作用和资源回收 |
| 综合运行 | `labdemo_ucore`、`ch8_cow_ucore` | 三个 Agent 协作、元数据、Context、时间线和基础 `COW` 行为 |
| 赛题五项综合测试 | `agenteval_ucore` | 在同一 Guest 中依次走过 Agent 创建与 Context、结构化工具、上下文路径、文件查询和 Agent Loop，并由 Host 确认挑战值与预期输出 |

以 `agentfinal_ucore` 为例，测试必须同时看到 `context_commit_lane=1 sequence=1..3 hash=1`、rollback、active path、FIFO 和只读映射等标志行。`agentcontract_ucore` 还要检查 DAG、provenance、planned effect、deadline 和资源归零标志。完成行只表明进程已经收尾，前面的标志行才表明待测功能已经运行。

### 4.2 单独运行一个场景

```bash
AGENT_TEST_CASE=agentfinal_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agentfs_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agentcontract_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agenttask_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agentpublish_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agent_eevdf_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

单独运行时仍会重新生成文件系统镜像，并核对该场景要求的标志行。通过后输出 `[agent-tests] targeted case passed`。如需保存串口原始输出，可同时设置 `AGENT_TEST_GUEST_LOG_FILE=/absolute/path/guest.log`。

`agentpublish_ucore` 的 6 条校验标志均已通过。程序读回 32 字节 header、96 字节 payload 和紧随其后的 EOF；两个同 scope 进程竞争同名文件时，结果恰好为一个 `OK` 和一个 `DUPLICATE`，正式文件不被覆盖。错误的 pointer、path、size、version 或保留字段不会留下正式文件名。发布调用方对相同字节通过正式路径回读收敛，对不同内容保持失败；非法请求与重复发布不增加资源计数，删除两份测试结果后 inode 和 block 回到基线。

五项综合评测（Task 1-5）使用单独入口：

```bash
AGENT_TEST_CASE=agenteval_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

[`scripts/run-agent-tests.sh`](../scripts/run-agent-tests.sh) 会为该程序选择 `CHAPTER=agent_eval`，生成一次非零随机挑战值，再调用 [`host_tools/evaluation_contract.py`](../host_tools/evaluation_contract.py) 检查输出字段、测试负载指纹、结果指纹和整套测试约定。

综合程序不是只打印五个“通过”标志，而是把每项能力对应到可以从 Guest 日志复核的系统行为：

| 赛题关注点 | `agenteval_ucore` 中的操作 | 可观察结果与判断 |
| --- | --- | --- |
| Agent 进程创建与地址空间设计 | 受控创建 Agent，读取 identity 与 Context header，直接读内核发布页并写用户缓存页 | identity、角色、Context 基址与容量符合本次启动；前 6 页由内核发布，第 7 页可由 Guest 直接写入，普通进程与 Agent 进程可以同时运行 |
| Agent-OS 内核结构化交互接口与工具调用协议 | 枚举 Tool Registry，调用 `echo`、`query_process`、`capability_check`，再提交未知工具、编号与名称不匹配、重复参数和错误类型 | 正常请求得到版本化结构结果，错误请求被区分为明确状态；Tool Registry 与 schema 真正参与了内核解析，而不是由测试程序自行拼出结果 |
| 上下文路径管理 | 连续执行 6 轮工具调用，分别用 syscall 与映射页读取，随后回滚、清空，并追加 133 条记录 | 两种读取路径逐条一致；回滚产生新分支，清空后可见路径归零；超过 128 条后按 FIFO 保留后缀且不发生 OOM，说明定长 Context 可以支撑持续 Agent Loop |
| 面向 Agent 查询优化的文件系统扩展 | 创建真实文件并登记属性，执行多条件 AND、summary 模糊匹配、内容摘要/preview 和属性删除 | 返回项绑定真实 inode 且顺序、去重和摘要一致；删除一个或全部属性后结果集合随之改变，说明查询走的是 VFS 文件身份与 Metadata Catalog，而不是固定样例表 |
| Agent Loop 内核运行机制 | 建立受信消息路由，让等待线程先睡眠再由另一 Agent 延迟唤醒；动态调整并停止 heartbeat | 等待期间 wall tick 前进且进程 sleep/wake 计数增加，消息到达后及时返回；heartbeat 周期调整生效，停止后由测试程序显式取走已入队的 timer 事件，再次等待得到 `TIMEOUT`，说明无事件时不会忙轮询 |

这一组测试把五项机制放在同一个 Agent 生命周期中，可以看到前一阶段产生的 identity、Context 和文件状态如何继续供下一阶段使用。它不能替代针对各模块职责和异常路径的测试，但能排除“各模块单独通过、合在一起却无法运行”的情况。

## 5. 权限检查与故障恢复

### 5.1 权限检查

```bash
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttrust_ucore    make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentscope_ucore    make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=usersafety_ucore    make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore     make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

这些场景会尝试用普通进程冒用 Agent 身份，并构造错误的 lifecycle generation、跨 scope 消息、过期 Execution Contract、非法用户指针、共享队列满载和重复请求。Task resource 还会提交嵌入 NUL、非法 UTF-8、长度与 EOF 不符、不可读 fd 和过期 generation。系统拒绝这些输入后，测试会继续运行，并在生命周期结束后核对资源是否回到原值。

`agenttask_ucore` 的资源标志已在定向 QEMU 中通过。合法输入取自当前文件访问范围，ECHO 返回导入时保存的 UTF-8 快照；BORROWED 完成后保持 `LIVE`，OWNED 完成后自动消费，显式释放和槽位复用前的旧 generation 都返回 `STALE`。测试还让一个 sibling 关闭已经 unlink 的 fd，同时由另一条路径完成导入，两边都能正常收尾；descriptor transaction 的静态检查继续核对 pin、读取与结算顺序。结果表明资源快照、所有权和 ABA 防护没有只停留在资源接口上，而是贯穿了真实的 SQ/CQ 提交与完成过程。

### 5.2 故障测试

```bash
make fs-enospc-test TOOLPREFIX=riscv64-linux-gnu-
make fs-allocator-fault-test TOOLPREFIX=riscv64-linux-gnu-
make workflow-teardown-race-test TOOLPREFIX=riscv64-linux-gnu-
make virtio-disk-test TOOLPREFIX=riscv64-linux-gnu-
```

| 入口 | 如何制造故障 | 检查结果 |
| --- | --- | --- |
| `fs-enospc-test` | 耗尽文件系统空间，并检查资源额度和重启后仍保留的账户 | 分配失败能够返回给调用方；孤儿块可以回收；重启后额度记录一致 |
| `fs-allocator-fault-test` | 从 `alloc/free/ialloc/ifree`、`intent/bitmap/owner/refund` 和 `busy/eio/crash` 中选取 36 组有效组合，再重新启动 | 重启前后的镜像状态符合事务所处阶段；检查程序能够发现 `delete-FLUSH` 阶段的异常修改 |
| `workflow-teardown-race-test` | 在 Agent 仍活动时并发执行 workflow teardown | fence drain 完成后，引用和工作流资源回到原值 |
| `virtio-disk-test` | 注入 VirtIO 磁盘错误 | `bio`、VFS 和 Guest 收到相同的失败状态，系统能够完成收尾 |

结果文件发布遵循“先写未命名 inode 并 checkpoint，再接入正式目录项”的两阶段 fs epoch 顺序。`agentpublish_ucore` 验证正式路径的完整内容、并发不覆盖和失败零副作用，静态顺序检查固定两次 checkpoint 与单次目录接入的先后关系。36 组文件系统故障回归不直接调用发布接口，而是验证两条路径共用的块与 inode 分配、回收和重启恢复机制。三类结果分别覆盖接口行为、发布顺序和底层持久化基础。

## 6. 交互会话测试

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-

make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

Console replay 检查脚本化多轮会话中 `query_file`、`echo` 和 `send_message` 的工具结果，以及审批记录、本次启动的内核时间线和正常关闭。

Nexus replay 是固定的协议交互回归。它接受“不用工具直接回答”和“模型自行选择工具”两类路径；对后者逐轮检查模型只返回一个工具调用或最终答案，不把回放数据中的顺序、调用次数或业务结论当成 runtime 策略。公开表面只包含 `search_files`、`read_file` 和 `inspect_system` 三个通用只读工具，也不要求在同一次回归中巡游全部工具。

child Task 由 Coordinator 通过真正的内核 Task Channel `delegate_task` 交给 Research 或 System。回归检查 56 字节 descriptor、`AGENT_ARTIFACT_TASK` resource、目标 claim/complete、结果 artifact 和唯一 terminal CQE，也确认 self delegation 与让同一活动端点同时成为 owner/target 的组合被拒绝。任务在 claim 后遇到 cancel、deadline、owner 退出或生命周期关闭时，测试覆盖 `RETRY/CLAIMED` offer、清理预绑定结果、`ACK_TERMINAL` 准确回传，以及更高 generation 的 `TIMEOUT` offer 不会重跑业务。

controller 取消回归使用 syscall 568 的 `REQUEST_CANCEL`，检查同一生命周期、`ORCHESTRATE`/`WAIT_CANCEL`、caller 到 owner 的 TASK route 和 owner/channel/request/slot/task/correlation 完整绑定；`OK` 只代表控制请求线性化。QUEUED 由 owner lane 终结，CLAIMED 仍需 provider cleanup ACK，PREPARING/CLAIMING 返回 `RETRY`，READY 的先到结果不被迟到取消覆盖，同一绑定可恢复丢失的 copyout。任务正文不经 `MESSAGE` 传递；Task descriptor 只绑定目标身份、任务关联和 capsule handle。首版每个 issuer 同时只允许一个未结算委派，也不承诺永久无响应的已 claim 执行者会自动收敛。

Nexus 还检查 Contract `RETIRE` 从 `RETRY/RETIRING` 到 `OK/RECLAIMED` 的两阶段收敛：只有直接调用与运行引用归零后，Coordinator 才恢复 observer/Host event 输出、读取结果 artifact 并发布任务投影。CREATE 发布时会为普通 inode 操作固定引用，但不会计入阻塞的 pipe/device 控制读取；活动 Contract 中的普通 pipe write 仍须通过 IPC 副作用检查。System/Sentinel 和 Research/Investigator 的结果发布同时要求 `AGENT_CAP_ARTIFACT_WRITE`、artifact manifest permission、VFS 文件访问范围与 delegated effect lease。

工作区回归从 Host 的版本化 manifest 开始：Guest 用 1 个 control inode 和最多 32 个 data-stub inode 建立 Metadata Catalog 窗口，按 4 个 stage 执行返回不截断的 Live Query，并在有界运行时内存中再次核对完整路径。control stub 的 Typed Watch 必须收到 generation `UPDATE`，旧窗口才会失效并从 cursor 0 重建。Host 搜索只能接收 Guest Catalog 选出的候选，读取必须绑定 object/path/revision；stale 结果要清空累积并重试。真实正文回到 Guest 后依次成为 Research 输入 artifact、Research 结果 artifact、TOOL Context 和模型历史。回归还覆盖 Host 对路径跳转、链接逃逸、二进制文件、缺失文件和输出上限的处理。

跨轮测试以 Relay Agent 的 Context active path 为主线：USER、已结算 TOOL 和成功 FINAL 必须形成短 Context 节点；4 KiB 用户缓存只为仍在 active path 上的成功 USER/FINAL 对补充完整正文。测试分别覆盖映射页 direct active query、syscall 回退、缓存容量不足时按整轮淘汰、失败或取消后的路径回滚，以及 `/reset` 同时清空 Relay Context、缓存和工作区 Catalog/watch 状态。Host 测试确认中继不私建、补写或替换 Provider 请求中的对话与工具结果；在线 Provider 和固定 Replay 都直接接收 Guest 构造的消息及真实工具 artifact 投影。

会话协商的上限为每轮 16 个模型决策与 32 次可重试 provider 错误。Nexus 生成请求必须保持 `max_tokens=114514`；DeepSeek V4 还要求 `thinking.type=enabled` 和 `reasoning_effort=max`。测试确认工具轮次间的 provider-private `reasoning_content` 只原样回传给 provider，不出现在 Guest、controller 或 telemetry。生成预算与公开输出界限分开：Guest 返回的最终正文仍不得超过 2048 个 UTF-8 字节。

两项 replay 都会真正启动 QEMU Guest。固定数据只替换在线 provider 回复；工具执行、Context sequence、Task Channel、Metadata Catalog、Typed Watch、artifact、运行记录、controller/observer 投影和会话关闭都由本次运行产生。Nexus 的跨轮消息仍由本次 Guest 的 Context active path 和 4 KiB 用户缓存重建，Host 不额外补写历史正文或工具结果。具体操作见[运行方法](usage.md)。

Nexus 自由演示也属于交互会话测试，但目的不同。它连接在线 DeepSeek，先用 AgentOS 改进问题作为一类示例观察模型能否自己选择通用工具、读到相关实现并给出自然、有道理的结论，再用一轮基于 active Context 的追问观察跨轮延续。这里不要求答案的每个细节完全一致，也不检查预设的工具顺序；如果回答持续偏离问题，应优先调整通用 system policy、工作区读取或消息协作能力，而不是增加只服务该题目的专用模块。

```bash
make agentos-nexus-demo TOOLPREFIX=riscv64-linux-gnu-
```

## 7. 双平台对照测试

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

普通 uCore 和 AgentOS-uCore 使用相同的输入文件，按相同顺序运行程序，并采用同一套结果判定程序。Host 从两套 Guest 镜像中提取状态，比较各阶段、文件内容、摘要、输出记录数、程序顺序和退出状态。两边全部通过后输出：

```text
[dual-platform] plain and AgentOS platforms both passed
```

这项测试用来确认两套平台得到相同的业务结果。实时查询、任务通道和 `EEVDF` 的专项性能测试，则比较两种内核路径完成相同测试负载所需的时间和工作量。

## 8. 专项性能结果与数据

本节汇总 Live Query、Task Channel 和 Agent Loop 三类机制的测量结果与数据入口。性能数据来自 30 次 QEMU 独立启动，串口原始输出、逐样本表、数据清单和检查结果保存在 [`one_shot_metrics/data/20260811`](../one_shot_metrics/data/20260811/)。

| 对应机制 | 主要结果 | 对系统设计的评价 |
| --- | --- | --- |
| Live Query | 96 条记录上，索引核心阶段 16/16 次更快，中位加速 3.118 倍；完整流程仅 3/16 次更快，中位差值 `+13.452 毫秒` | 位图索引确实减少了核心查询的候选项，但核心窗口之外的聚合路径抵消了这部分收益；现有计时尚不能定位具体环节，需要补充分段计时 |
| Agent Task | 16 个同步 `ECHO` 的中位耗时：Batch `561.0 微秒`、`SQ/CQ` `1,620.5 微秒`、Scalar V3 `2,051.0 微秒` | 短同构调用优先使用 Batch；`SQ/CQ` 的设计目标是长期队列、backpressure、cancel 和唯一 terminal CQE，不是压低这组短调用的最低延迟 |
| 工作流 EEVDF | 504 次唤醒均为 0–1 tick，1 至 4 个并发工作流的 Jain 指数中位数均不低于 0.99998 | 事件等待能够让 Agent 休眠后及时恢复，工作流级记账基本抑制线程放大；0 tick 只表示短于 10 ms 粒度，不能解释成零开销 |

已有数据可以重新检查：

```bash
python3 one_shot_metrics/validate.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output ../agentos-validation.json
```

`manifest.json` 记录了 19 个 CSV 文件，共 7,498 条数据记录。`validation.json` 检查 10 类绘图输入，包括字段格式、`AB/BA` 顺序是否平衡、配对指纹、参数组合、逐操作数据是否齐全、唤醒采样和 I/O 计数范围。采集方法、统计单位和七张图表见[性能测试](performance.md)。

## 9. 运行全部检查

```bash
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

`full-verify` 会依次检查依赖环境，运行 Host 自测、`UAPI` 和模块检查、用户栈和内核栈检查、构建、兼容性检查、完整 Guest 测试、五项综合评测、资源回归测试和文件系统断电测试。全部完成后输出：

```text
[full-verify] configured build, QEMU, and resource checks passed
```

测试结束后，可以先查看待清理文件，再清理白名单中的生成物：

```bash
make clean-workspace-dry-run
make clean-workspace
```

状态码和 `ABI` 字段见 [API](api.md)，权限和失败处理见[安全机制](security.md)。
