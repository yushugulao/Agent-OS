# AgentOS-uCore 测试

AgentOS 测试从公开 ABI 与模块关系开始，随后进入 RISC-V64 QEMU Guest，检查身份、Context、工具执行、文件查询、事件循环、调度、资源和 workflow 回收。Console、Nexus 与双目标测试继续覆盖上层运行路径。

## 验证层次

| 层次 | 入口 | 检查内容 |
| --- | --- | --- |
| ABI 与源码契约 | `agent-uapi-check`、`agent-module-check` | 结构布局、syscall、模块依赖与状态机接线 |
| Host 状态机 | `local-host-selftests` | 合同、资源、文件查询、调度、串口协议与校验器 |
| QEMU Guest | `agentos-test` | 真实 RISC-V syscall、VFS、调度、内存和生命周期 |
| 长驻应用 | Console/Nexus replay | 多轮工具、审批、任务委派、工件与会话关闭 |
| 系统对照 | `dual-platform-run` | Plain/AgentOS 两侧业务结果与端到端耗时 |
| 性能活动 | [`one_shot_metrics/data/20260811`](../one_shot_metrics/data/20260811/) | 逐样本时延、I/O、工作量、唤醒与公平性 |

## 构建检查

```bash
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
make agent-uapi-check TOOLPREFIX=riscv64-linux-gnu-
make agent-module-check TOOLPREFIX=riscv64-linux-gnu-
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`agent-uapi-check` 使用 RISC-V64 probe、静态断言与冻结清单核对 kernel/user 共享结构。`agent-module-check` 检查 AgentOS 生产模块之间的所有权和调用关系。`kernel-stack-check` 根据 GCC 生成的编译期 `.ci` 调用图计算内核栈预算。

Host 自测入口为：

```bash
make local-host-selftests
```

它并行运行资源账户、Context、Live Query、合同、Task Channel、调度模型、Console/Nexus 协议和数据校验器等 Python/shell 测试。

## QEMU Guest 回归

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

Runner 为每个场景构建对应 Guest，启动 QEMU 并检查 pass marker、进程退出状态、panic、输出上限和 timeout。

| 产品模块 | Guest 程序 | 主要检查 |
| --- | --- | --- |
| 身份与 lifecycle | `agenttrust_ucore`、`agentscope_ucore` | 可信映像、权限衰减、generation、scope 与 teardown |
| Context | `agentfinal_ucore` | snapshot、detail、active path、rollback、FIFO 淘汰与 hash 关系 |
| 工具与合同 | `agenttoolabi_ucore`、`agentcontract_ucore` | schema、capability、DAG 前驱、attempt、deadline、retry 与资源 |
| Live Query | `agentfs_ucore`、`agentbench_ucore` | inode incarnation、scan/index 等价性、typed watch、resync 与工作量 |
| Event Loop | `agentloop_ucore` | 原子 wait/wakeup、heartbeat、route、correlation 与 cancel |
| Workflow 调度 | `agentsched_ucore`、`agent_eevdf_ucore` | 多 workflow 进展、service cycles、wakeup latency 与 Jain fairness |
| Task Channel | `agenttask_ucore` | single issuer、CQ 只读、backpressure、resync、cancel 与 deadline |
| 综合运行 | `labdemo_ucore`、`agenteval_ucore` | 六组 AgentOS 能力在同一 lifecycle 中组合 |

定位单个场景：

```bash
AGENT_TEST_CASE=agentfinal_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentfs_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

## 权限与恢复

以下 Guest 检查 capability、scope、generation、用户指针、合同、共享 SQ 页和资源回收：

```bash
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttrust_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentscope_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=usersafety_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

文件系统耗尽、分配故障、并发 teardown 和 VirtIO 磁盘故障使用独立入口：

```bash
make fs-enospc-test TOOLPREFIX=riscv64-linux-gnu-
make fs-allocator-fault-test TOOLPREFIX=riscv64-linux-gnu-
make workflow-teardown-race-test TOOLPREFIX=riscv64-linux-gnu-
make virtio-disk-test TOOLPREFIX=riscv64-linux-gnu-
```

## Console 与 Nexus

两条 replay 都启动真实 QEMU Guest，固定 response 按请求 digest 绑定。Guest 内的工具调用、Context、审批和任务状态由本次 workflow 执行。

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-

make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

Console replay 检查连续三轮请求、工具结果回灌、副作用审批和有序关闭。Nexus replay 检查四个身份、N1 TASK 生命周期、失败后重规划、来源工件、Analyst 汇总和 controller/observer 状态同步。

## 双目标测试

Plain uCore 与 AgentOS-uCore 使用相同输入、程序顺序和 outcome oracle：

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

Host 从两套文件系统镜像提取规范状态，核对阶段、文件字节、摘要、行数、程序顺序和退出状态。专项 benchmark 观察内核服务，双目标测试观察服务进入完整 uCore workflow 后的系统行为。

## 性能数据

一次性性能活动在源码提交 `2b14fb1f74b9bd093e6de939a16554620835699e` 上完成 30 次 fresh QEMU boot，原始数据位于 [`one_shot_metrics/data/20260811`](../one_shot_metrics/data/20260811/)。活动按 boot 保存串口输出，再提取 19 个 CSV 数据表。它独立于日常 `agentos-test`，正常构建与回归不会重新采集这组数据。

已有数据可以重新校验：

```bash
python3 one_shot_metrics/validate.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output ../agentos-validation.json
```

实验设计、样本数量和图表见[性能结果](performance.md)。

## 完整检查

```bash
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

构建与测试结束后可以预览并清理白名单中的生成物：

```bash
make clean-workspace-dry-run
make clean-workspace
```

测试涉及的状态码见 [API](api.md)，权限与失败语义见[安全机制](security.md)。
