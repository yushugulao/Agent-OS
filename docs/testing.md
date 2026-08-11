# AgentOS-uCore 测试

AgentOS-uCore 的测试沿着产品调用链展开：先确认公开 ABI 与模块接线，再验证 Host 协议和状态机，最后在 RISC-V64 QEMU Guest 中执行真实 syscall、VFS、调度、资源与 workflow 生命周期。故障矩阵和双目标运行继续检查恢复能力与业务结果。

## 文档索引

- [1. 测试目标与入口](#1-测试目标与入口)
- [2. ABI 与源码检查](#2-abi-与源码检查)
- [3. Host 自测](#3-host-自测)
- [4. QEMU Guest 回归](#4-qemu-guest-回归)
- [5. 权限与故障恢复](#5-权限与故障恢复)
- [6. Console 与 Nexus](#6-console-与-nexus)
- [7. 双目标业务测试](#7-双目标业务测试)
- [8. 性能活动数据](#8-性能活动数据)
- [9. 完整验证](#9-完整验证)

## 1. 测试目标与入口

| 测试目标 | 方法 | 产品行为 | 入口 |
| --- | --- | --- | --- |
| ABI 稳定 | RISC-V probe、静态断言、冻结布局清单 | kernel/user 共享结构、syscall 与 manifest digest 一致 | `make agent-uapi-check` |
| 模块接线 | 源码所有权、调用关系与 fence 检查 | Context、Live Query、workflow fence 等生产路径保持连接 | `make agent-module-check` |
| Host 控制面 | Python/shell 状态机与协议测试 | Console、Nexus、合同、资源账户、校验器正确处理输入 | `make local-host-selftests` |
| Guest 功能 | fresh QEMU boot、真实 RISC-V syscall、精确 marker | 身份、工具、Context、VFS、调度和 Task Channel 完成 lifecycle | `make agentos-test` |
| 权限与恢复 | 越权输入、容量耗尽、故障注入和重启 | 请求被拒绝或恢复后状态保持一致，资源可回收 | 专项 Guest 与故障目标 |
| 长驻会话 | controller、observer 与固定 provider 回放 | 多 turn、审批、任务委派、工件交接和关闭顺序完整 | Console/Nexus replay |
| 业务等价 | 两套镜像、同一输入与 outcome oracle | Plain uCore 与 AgentOS-uCore 生成相同业务结果 | `make dual-platform-run` |
| 性能机制 | 多次独立启动、boot 内配对与参数网格 | 保存逐样本 latency、I/O、wakeup 与 fairness | `one_shot_metrics/data/20260811` |

普通 Guest 功能场景由 [`scripts/agent_test_runner.py`](../scripts/agent_test_runner.py) 监视。Runner 检查完成 marker、退出状态、panic、意外 fault、输出上限、空闲时间和总 timeout；场景脚本再核对能够表明机制确实运行的精确日志行。Console 与 Nexus replay 由各自 Make 入口直接启动 Host daemon、QEMU 与专用验证器。

## 2. ABI 与源码检查

### 2.1 构建与布局

```bash
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
make agent-uapi-check TOOLPREFIX=riscv64-linux-gnu-
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`agent-uapi-check` 编译 [`scripts/probes/agent-uapi-layout.c`](../scripts/probes/agent-uapi-layout.c)，读取符号大小与偏移，并和 [`ci/agent-uapi-layout.json`](../ci/agent-uapi-layout.json) 对照。检查范围包含 lifecycle、execution contract、provenance、Task Channel、tool、workflow fence、performance 与 resource ABI。

`kernel-stack-check` 使用 GCC 生成的 `.ci` 调用图，从 syscall 与中断入口计算最深调用路径，同时计入中断帧、guard 和安全余量。这样可以在链接前定位 AgentOS 生产调用链上的栈预算变化。

### 2.2 模块关系

```bash
make agent-module-check TOOLPREFIX=riscv64-linux-gnu-
```

该入口依次执行模块边界、Live Query 文件系统接线和 workflow fence 检查。测试直接定位生产符号，确认状态所有者、generation 索引、catalog mutation fence、resync 交付和 teardown drain 仍处于实际调用路径中。

## 3. Host 自测

```bash
make local-host-selftests
```

Host 自测通过 [`scripts/run-parallel-tests.py`](../scripts/run-parallel-tests.py) 并行运行协议、模型和静态契约测试，单项超时为 900 秒。主要覆盖：

| 组件 | 测试结果 |
| --- | --- |
| Execution Contract | DAG 前驱、attempt、deadline、retry、effect 与 evidence 字段保持一致 |
| Context 与 provenance | 提交原子性、active path、snapshot reader、来源单调性与 syscall 归因正确 |
| Live Query | generation、inode version、索引接线、mutation fence 与 scan/index 结果一致 |
| Task Channel | SQ/CQ 协议、transport、single issuer、cancel、backpressure 与 resync 状态机正确 |
| Workflow 调度 | credit domain、scheduler model、阻塞唤醒与资源计账保持一致 |
| Console 与 Nexus | 串口 envelope、本地 socket、controller/observer、任务与工件协议可解析 |
| 双目标工具 | Plain/AgentOS 状态提取、outcome 比较和来源清单可复核 |

自测的输入由测试自身构造，失败时直接给出缺失字段、非法状态迁移或源码接线位置，便于回到相应模块修复。

## 4. QEMU Guest 回归

### 4.1 完整 Guest 套件

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

完整套件先构建包含 21 个 Guest 程序的用户镜像，再为各场景重建内核并启动独立 QEMU。结束时输出：

```text
[agent-tests] all Agent-OS uCore checks passed
```

主要场景与内核行为如下。

| 产品路径 | Guest 程序 | 通过时确认的行为 |
| --- | --- | --- |
| 身份与 lifecycle | `agentsecurity_ucore`、`agenttrust_ucore`、`agentscope_ucore` | 映像信任、角色创建、capability 衰减、scope 隔离、generation 与 teardown |
| Context 与证据链 | `agentfinal_ucore` | commit lane、snapshot/detail、active path、rollback、FIFO 淘汰、timeline 与 provenance graph |
| 工具与合同 | `agenttoolabi_ucore`、`agentcontract_ucore`、`agentllm_ucore` | schema、V1/V2/V3 ABI、DAG 前驱、attempt、deadline、retry、effect 与 LLM envelope |
| Live Query | `agentfs_ucore`、`agentscan_ucore`、`agentbench_ucore` | inode incarnation、metadata mutation、scan/index 等价、typed watch 与 workload 计数 |
| Event Loop | `agentloop_ucore`、`blocking_semantics_ucore` | 原子 wait publication、heartbeat、广播隔离、cancel 与无惊群唤醒 |
| Workflow 调度 | `agentsched_ucore`、`agent_eevdf_ucore`、`agentconflict_ucore` | 多 workflow 进展、EEVDF service、exact wake probe 与冲突处理 |
| Task Channel | `agenttask_ucore` | Batch、Scalar V3、SQ/CQ、terminal CQE、backpressure、resync、retained-terminal 幂等 cancel 与 hard deadline |
| VFS 与资源 | `agentvfs_ucore`、`iobudget_ucore`、`usersafety_ucore` | fstat 重授权、I/O lineage、用户指针和 exec 参数边界 |
| 综合运行 | `labdemo_ucore`、`ch8_cow_ucore` | 三 Agent 协作、metadata/Context/timeline 组合与基础 COW 行为 |

例如，`agentfinal_ucore` 只有同时观察到 `context_commit_lane=1 sequence=1..3 hash=1`、rollback、active path、FIFO 和只读映射等 marker 后才会通过；`agentcontract_ucore` 还要求 DAG、来源、planned effect、deadline 和零泄漏 marker 全部出现。完成行只负责确认进程收尾，机制行负责确认测试主体已经执行。

### 4.2 定位单个场景

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

单场景仍会构建 fresh image、执行精确 marker 校验，并输出 `[agent-tests] targeted case passed`。保存原始串口日志时可以同时设置 `AGENT_TEST_GUEST_LOG_FILE=/absolute/path/guest.log`。

综合 Task 1-5 评测使用独立入口：

```bash
AGENT_TEST_CASE=agenteval_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

[`scripts/run-agent-tests.sh`](../scripts/run-agent-tests.sh) 为该 Guest 选择 `CHAPTER=agent_eval`、生成一次非零 challenge，并调用 [`host_tools/evaluation_contract.py`](../host_tools/evaluation_contract.py) 核对输出 schema、workload fingerprint、result fingerprint 和 suite 合同。

## 5. 权限与故障恢复

### 5.1 权限路径

```bash
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttrust_ucore    make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentscope_ucore    make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=usersafety_ucore    make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore     make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

这些场景构造普通进程冒用身份、错误 generation、跨 scope 路由、过期合同、非法用户指针、共享队列饱和和重复请求。测试继续运行并检查明确的拒绝状态，同时在 lifecycle 结束后比较资源快照。

### 5.2 故障矩阵

```bash
make fs-enospc-test TOOLPREFIX=riscv64-linux-gnu-
make fs-allocator-fault-test TOOLPREFIX=riscv64-linux-gnu-
make workflow-teardown-race-test TOOLPREFIX=riscv64-linux-gnu-
make virtio-disk-test TOOLPREFIX=riscv64-linux-gnu-
```

| 入口 | 注入方法 | 验证结果 |
| --- | --- | --- |
| `fs-enospc-test` | 文件系统耗尽、domain quota 与持久 principal 场景 | 分配失败可报告，孤儿块可回收，重启后配额状态一致 |
| `fs-allocator-fault-test` | 从 alloc/free/ialloc/ifree、intent/bitmap/owner/refund 与 busy/eio/crash 中筛选 36 组有效组合，随后重启 | 镜像前后状态符合事务阶段；delete-FLUSH mutation 会被校验器捕获 |
| `workflow-teardown-race-test` | Agent 活动期间并发 teardown | fence drain 完成，引用与 workflow 资源回到基线 |
| `virtio-disk-test` | VirtIO 磁盘错误矩阵 | bio、VFS 与 Guest 接收一致的失败状态，系统完成收尾 |

## 6. Console 与 Nexus

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-

make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

Console replay 检查 7 个请求摘要、3 个有序 turn、`query_file`/`echo`/`send_message` 工具结果、一次拒绝、一次批准、fresh kernel timeline 与 clean close。Nexus replay 检查 11 个请求摘要、4 个 Agent 身份、N1 task 生命周期、错误句柄后的重规划、3 个来源工件、拒绝发布和 controller/observer 同步。

两个回放均启动真实 QEMU Guest。固定响应仅替换在线模型返回，工具执行、Context sequence、审批绑定、任务状态和 session 关闭仍由本次运行产生。具体操作见[运行指南](usage.md)。

## 7. 双目标业务测试

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

Plain uCore 和 AgentOS-uCore 使用相同的输入文件、程序顺序、阶段名称和 outcome oracle。Host 从两套 Guest 镜像提取规范状态，比较阶段、文件字节、摘要、行数、程序顺序和退出状态。两侧全部通过后输出：

```text
[dual-platform] plain and AgentOS platforms both passed
```

该测试回答业务结果是否一致；Live Query、Task Channel 和 EEVDF 专项 benchmark 则测量完成同一内核工作时的路径差异。

## 8. 性能活动数据

性能活动在源码提交 `2b14fb1f74b9bd093e6de939a16554620835699e` 上完成 30 次 fresh QEMU boot。原始串口输出、逐样本表、manifest 与校验报告保存在 [`one_shot_metrics/data/20260811`](../one_shot_metrics/data/20260811/)，完成标志为 [`COMPLETED`](../one_shot_metrics/data/20260811/COMPLETED)。

已有数据可以重新校验：

```bash
python3 one_shot_metrics/validate.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output ../agentos-validation.json
```

`manifest.json` 当前记录 19 个 CSV 数据表和 7,498 行；`validation.json` 检查 10 类图表输入。校验内容包括 schema、AB/BA 平衡、配对指纹、参数网格、逐操作完整性、wake probe 与 I/O 计数作用域。实验设计、统计单位和图表见[性能测试](performance.md)。

## 9. 完整验证

```bash
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

`full-verify` 依次执行依赖检查、Host 自测、UAPI 与模块检查、用户/内核栈检查、构建、兼容性 trace、完整 Guest 套件、Task 1-5 评测、资源回归和文件系统断电测试。全部完成后输出：

```text
[full-verify] configured build, QEMU, and resource checks passed
```

构建结束后可以预览并清理白名单中的生成物：

```bash
make clean-workspace-dry-run
make clean-workspace
```

状态码与 ABI 字段见 [API](api.md)，权限和失败语义见[安全机制](security.md)。
