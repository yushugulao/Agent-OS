# AgentOS-uCore 测试说明

项目测试按实际调用顺序分三步进行：先检查公开 `ABI` 和模块调用关系，再检查宿主机协议与状态机，最后在 RISC-V64 QEMU 客户机中执行真实的系统调用、文件系统、调度、资源管理和工作流。这些检查通过后，我们再运行故障注入和双平台对照，检查异常恢复与最终结果。

## 文档索引

- [1. 测试内容与入口](#1-测试内容与入口)
- [2. ABI 和源码检查](#2-abi-和源码检查)
- [3. 宿主机自测](#3-宿主机自测)
- [4. QEMU 客户机测试](#4-qemu-客户机测试)
- [5. 权限检查与故障恢复](#5-权限检查与故障恢复)
- [6. 交互会话测试](#6-交互会话测试)
- [7. 双平台对照测试](#7-双平台对照测试)
- [8. 性能测试数据](#8-性能测试数据)
- [9. 运行全部检查](#9-运行全部检查)

## 1. 测试内容与入口

| 检查内容 | 检查方法 | 通过条件 | 入口 |
| --- | --- | --- | --- |
| `ABI` 一致性 | RISC-V 探针、静态断言、固定的布局清单 | 内核态和用户态共用的结构、系统调用号和清单摘要一致 | `make agent-uapi-check` |
| 模块调用关系 | 检查源码归属、调用关系和屏障 | 上下文、实时查询、工作流屏障等代码仍在实际调用链中 | `make agent-module-check` |
| 宿主机控制程序 | 测试 Python 和 Shell 协议处理程序 | 控制台、Nexus、执行约定、资源账户和检查程序能正确处理输入 | `make local-host-selftests` |
| 客户机功能 | 每个场景独立启动 QEMU，并执行真实的 RISC-V 系统调用 | 身份、工具、上下文、VFS、调度和任务通道完成整个生命周期 | `make agentos-test` |
| 权限与恢复 | 越权输入、容量耗尽、故障注入和重启 | 非法请求被拒绝；恢复后状态一致，资源能够回收 | 各专项客户机测试和故障测试 |
| 长时间会话 | 控制端、观察端和固定模型回复配合运行 | 多个轮次、审批、任务分派、结果交接和关闭顺序正确 | 单智能体控制台、Nexus 固定回放 |
| 业务结果 | 两套镜像使用同一输入和同一结果判定程序 | 普通 uCore 与 AgentOS-uCore 得到相同结果 | `make dual-platform-run` |
| 性能测试 | 多次独立启动；在同一测试批次内配对；遍历参数组合 | 保存每个样本的用时、I/O、唤醒等待和公平性 | `one_shot_metrics/data/20260811` |

普通客户机测试由 [`scripts/agent_test_runner.py`](../scripts/agent_test_runner.py) 监视。程序会检查完成标志、退出状态、`panic`、意外错误、输出大小、空闲时间和总超时。每个测试场景还会核对指定的标志行，确认相关内核代码确实执行。单智能体控制台和 Nexus 的固定回放则由各自的 Make 命令启动宿主机后台程序、QEMU 和检查程序。

## 2. ABI 和源码检查

### 2.1 构建和结构布局

```bash
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
make agent-uapi-check TOOLPREFIX=riscv64-linux-gnu-
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`agent-uapi-check` 会编译 [`scripts/probes/agent-uapi-layout.c`](../scripts/probes/agent-uapi-layout.c)，读取各个结构的大小和字段偏移，再与 [`ci/agent-uapi-layout.json`](../ci/agent-uapi-layout.json) 对照。检查内容包括生命周期、执行约定、来源记录、任务通道、工具、工作流屏障、性能计数和资源管理 `ABI`。

`kernel-stack-check` 读取 GCC 生成的 `.ci` 调用图，从系统调用和中断入口计算最深调用路径，并计入中断帧、保护区和预留空间。这样可以在链接前发现 AgentOS 调用链的栈空间变化。

### 2.2 模块调用关系

```bash
make agent-module-check TOOLPREFIX=riscv64-linux-gnu-
```

这条命令依次检查模块边界、实时查询与文件系统的连接，以及工作流屏障。检查程序直接查找实际使用的函数和调用点，确认状态由指定模块管理，生命周期代次索引仍然有效，目录修改会经过屏障，重新同步时仍能送达通知，销毁工作流时也会等到引用清理完成。

## 3. 宿主机自测

```bash
make local-host-selftests
```

[`scripts/run-parallel-tests.py`](../scripts/run-parallel-tests.py) 会并行运行宿主机协议测试、模型测试和源码约定检查，每项最长运行 900 秒。主要检查内容如下：

| 组件 | 检查内容 |
| --- | --- |
| 执行约定 | DAG 前驱关系、尝试次数、截止时间、重试、副作用和来源字段 |
| 上下文与来源记录 | 原子提交、当前路径、快照读取、来源序号，以及系统调用的来源记录 |
| 实时查询 | 生命周期代次、inode 版本、索引调用、修改屏障，以及遍历查询和索引查询的结果是否一致 |
| 任务通道 | `SQ/CQ` 协议、传输、唯一提交者、取消、背压和重新同步流程 |
| 工作流调度 | 资源账户、调度器模型、阻塞唤醒和资源记账 |
| 控制台与 Nexus | 串口消息、本地套接字、控制端、观察端、任务和结果协议 |
| 双平台工具 | 普通 uCore 与 AgentOS-uCore 的状态提取、结果比较和来源清单 |

每项自测都会自行构造输入。测试失败时会直接列出缺失字段、错误的状态变化或有问题的源码调用点。

## 4. QEMU 客户机测试

### 4.1 完整客户机测试

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

完整测试先把 21 个客户机程序编入用户镜像。随后，每个场景都会重新构建内核并独立启动 QEMU。全部通过后输出：

```text
[agent-tests] all Agent-OS uCore checks passed
```

主要场景和对应的内核行为如下：

| 内核功能 | 客户机程序 | 检查内容 |
| --- | --- | --- |
| 身份与生命周期 | `agentsecurity_ucore`、`agenttrust_ucore`、`agentscope_ucore` | 映像信任、角色创建、能力逐级收紧、访问范围隔离、生命周期代次和销毁过程 |
| 上下文与来源记录 | `agentfinal_ucore` | 提交通道、快照和详情、当前路径、回滚、FIFO 淘汰、时间线和来源关系图 |
| 工具与执行约定 | `agenttoolabi_ucore`、`agentcontract_ucore`、`agentllm_ucore` | 参数格式、`V1/V2/V3 ABI`、DAG 前驱关系、尝试次数、截止时间、重试、副作用和 `LLM` 消息 |
| 实时查询 | `agentfs_ucore`、`agentscan_ucore`、`agentbench_ucore` | inode 代次、元数据修改、遍历查询与索引查询结果一致、按类型订阅变动和测试负载计数 |
| 事件循环 | `agentloop_ucore`、`blocking_semantics_ucore` | 等待条件的原子发布、心跳、广播隔离、取消和定点唤醒 |
| 工作流调度 | `agentsched_ucore`、`agent_eevdf_ucore`、`agentconflict_ucore` | 多个工作流并行推进、`EEVDF` 服务量、唤醒采样和冲突处理 |
| 任务通道 | `agenttask_ucore` | 批量提交、逐项 V3 提交、`SQ/CQ`、最终 `CQE`、背压、重新同步、任务结束后的重复取消和不可延后的截止时间 |
| VFS 与资源 | `agentvfs_ucore`、`iobudget_ucore`、`usersafety_ucore` | `fstat` 后重新授权、I/O 来源、用户指针和 `exec` 参数边界 |
| 综合运行 | `labdemo_ucore`、`ch8_cow_ucore` | 三个智能体协作、元数据、上下文、时间线和基础 `COW` 行为 |

以 `agentfinal_ucore` 为例，测试必须同时看到 `context_commit_lane=1 sequence=1..3 hash=1`、回滚、当前路径、FIFO 和只读映射等标志行。`agentcontract_ucore` 还要检查 DAG、来源、预先声明的副作用、截止时间和资源归零标志。完成行只表明进程已经收尾，前面的标志行才表明待测功能已经运行。

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

AGENT_TEST_CASE=agent_eevdf_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

单独运行时仍会重新生成文件系统镜像，并核对该场景要求的标志行。通过后输出 `[agent-tests] targeted case passed`。如需保存串口原始输出，可同时设置 `AGENT_TEST_GUEST_LOG_FILE=/absolute/path/guest.log`。

五项综合评测（Task 1-5）使用单独入口：

```bash
AGENT_TEST_CASE=agenteval_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

[`scripts/run-agent-tests.sh`](../scripts/run-agent-tests.sh) 会为该程序选择 `CHAPTER=agent_eval`，生成一次非零随机挑战值，再调用 [`host_tools/evaluation_contract.py`](../host_tools/evaluation_contract.py) 检查输出字段、测试负载指纹、结果指纹和整套测试约定。

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

这些场景会尝试用普通进程冒用智能体身份，并构造错误的生命周期代次、超出访问范围的消息、过期契约、非法用户指针、共享队列满载和重复请求。系统拒绝这些输入后，测试会继续运行，并在生命周期结束后核对资源是否回到原值。

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
| `workflow-teardown-race-test` | 在智能体仍活动时并发销毁工作流 | 等待屏障完成后，引用和工作流资源回到原值 |
| `virtio-disk-test` | 注入 VirtIO 磁盘错误 | `bio`、VFS 和客户机收到相同的失败状态，系统能够完成收尾 |

## 6. 交互会话测试

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-

make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

单智能体固定回放检查 7 个请求摘要、3 个连续轮次、`query_file`、`echo` 和 `send_message` 的工具结果，以及一次拒绝、一次批准、本次启动的内核时间线和正常关闭。多智能体固定回放检查 11 个请求摘要、4 个智能体身份、`N1` 任务生命周期、错误句柄后的重新安排、3 份结果文件及其来源记录、拒绝发布，以及控制端和观察端的状态是否一致。

两项测试都会真正启动 QEMU 客户机。固定数据只替换在线模型回复；工具执行、上下文序号、审批记录、任务状态和会话关闭都由本次运行产生。具体操作见[运行方法](usage.md)。

## 7. 双平台对照测试

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

普通 uCore 和 AgentOS-uCore 使用相同的输入文件，按相同顺序运行程序，并采用同一套结果判定程序。宿主机从两套客户机镜像中提取状态，比较各阶段、文件内容、摘要、行数、程序顺序和退出状态。两边全部通过后输出：

```text
[dual-platform] plain and AgentOS platforms both passed
```

这项测试用来确认两套平台得到相同的业务结果。实时查询、任务通道和 `EEVDF` 的专项性能测试，则比较两种内核路径完成相同测试负载所需的时间和工作量。

## 8. 性能测试数据

本轮性能数据在源码提交 `2b14fb1f74b9bd093e6de939a16554620835699e` 上采集，共独立启动 QEMU 30 次。串口原始输出、逐样本表、数据清单和检查结果保存在 [`one_shot_metrics/data/20260811`](../one_shot_metrics/data/20260811/)；[`COMPLETED`](../one_shot_metrics/data/20260811/COMPLETED) 表示本轮采集已经结束。

已有数据可以重新检查：

```bash
python3 one_shot_metrics/validate.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output ../agentos-validation.json
```

`manifest.json` 记录了 19 个 CSV 文件，共 7,498 行。`validation.json` 检查 10 类绘图输入，包括字段格式、`AB/BA` 顺序是否平衡、配对指纹、参数组合、逐操作数据是否齐全、唤醒采样和 I/O 计数范围。采集方法、统计单位和七张图表见[性能测试](performance.md)。

## 9. 运行全部检查

```bash
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

`full-verify` 会依次检查依赖环境，运行宿主机自测、`UAPI` 和模块检查、用户栈和内核栈检查、构建、兼容性检查、完整客户机测试、五项综合评测、资源回归测试和文件系统断电测试。全部完成后输出：

```text
[full-verify] configured build, QEMU, and resource checks passed
```

测试结束后，可以先查看待清理文件，再清理白名单中的生成物：

```bash
make clean-workspace-dry-run
make clean-workspace
```

状态码和 `ABI` 字段见 [API](api.md)，权限和失败处理见[安全机制](security.md)。
