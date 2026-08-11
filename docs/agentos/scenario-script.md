# 决赛现场演示脚本

主演示用一条确定性科研恢复 workflow 串起六项赛题能力，再用保存的逐样本图表解释性能。全程不依赖外部模型，网络状态不会影响核心验收。

## 演示前准备

```bash
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
make contest-demo-check
```

确认 QEMU、RISC-V 工具链和 Python 可用。现场预先打开以下文档：

- [项目总览](../../README.md)
- [要求追踪表](requirements-traceability.md)
- [实测性能结果](../contest/performance-results.md)
- [高级性能图](advanced-performance-figures.md)

## 15 分钟主流程

| 时间 | 操作 | 讲解重点 |
| ---: | --- | --- |
| 0–3 分钟 | 阅读 README 与总体架构图 | 内核负责可信边界，Guest 负责策略，Host 只采集或转发 |
| 3–8 分钟 | 运行 `make contest-demo` | 一条真实 QEMU workflow 同时覆盖身份、Context、工具、文件查询、事件与证据 |
| 8–12 分钟 | 打开配对图、Task 分布和 EEVDF 图 | 说明逐样本、配对、参数网格和统计单位 |
| 12–15 分钟 | 展示 capability 拒绝和 core/end-to-end 对照 | 副作用前拒绝、结果等价与计时窗口边界 |

主演示命令：

```bash
make contest-demo TOOLPREFIX=riscv64-linux-gnu-
```

该目标运行 4 个隔离 QEMU boot，按 AB/BA 顺序比较 traversal 和 indexed，输出到 `results/contest-demo/`：

- `measurements.csv`：逐 boot、逐路径记录；
- `summary.json`：配对汇总与环境；
- `report.md`：本次运行的可读结论；
- 串口日志：Guest marker、工作量与错误检查。

现场只读取本次输出，不手工填入预期速度。两个路径必须返回相同 recovered 结果和相同结果 hash。

## Workflow 讲解

`labdemo_ucore` 围绕 prepare、align、analyze、report、archive 五阶段状态与依赖完成 align 故障恢复：

1. Orchestrator 建立 workflow，创建 Sentinel、Investigator 和 Recovery；
2. 各 Agent 获得独立 identity、Context 和最小 capability；
3. Orchestrator 登记文件 metadata 和依赖；
4. Sentinel 安装 typed watch 后进入内核等待，不忙轮询；
5. align 状态变化产生结构化 file-query 事件；
6. Sentinel 的越权恢复被拒绝，并把调查请求发送给 Investigator；
7. Investigator 读取摘要和依赖，写入带 provenance 的恢复计划；
8. Recovery 在 capability 与 scope 同时匹配后提交动作；
9. 相同 correlation 的重复动作不再次产生副作用；
10. Orchestrator 读取 Context、timeline、audit 和 provenance，复核恢复结果。

串口出现 `labdemo_ucore: passed` 与 `labdemo_ucore: parent passed`，表示该次 Guest 场景完成。Host runner 还会检查 panic、超时、输出上限和结果一致性。

`labdemo_ucore` 输出的 `kind=fence` 表示性能计数器稳定快照。320 字节 workflow fence receipt 由 `rp_agentos_orch` 在 `make dual-platform-run` 路径中生成。

## 图表讲解

正式图表来自 2026-08-11 一次性活动，共 30 次 fresh QEMU boot 和 7,498 行逐样本数据。建议按以下顺序展示：

1. 配对哑铃与差值 ECDF：indexed workflow core interval 在 16/16 个配对中更短，中位 speedup 为 `3.118x`；该 interval 包含 query、recovery write、`fsync` 和 verify；
2. core 与 end-to-end 对照：端到端中位差值为 indexed 慢 `13,452 us`，明确结论范围；
3. `catalog × hit` 热力图与三维曲面：12 个实测格为 `1.164x–2.808x`；
4. batch/scalar V3/SQ-CQ 分布：16-op sequence 中位数为 `561/2051/1620.5 us`；
5. EEVDF ECDF 与 Jain fairness：504 条 exact wake probe 为 0–1 tick，1–4 workflow fairness 中位数均不低于 `0.99998`。

每张图同时说明平台、boot 数、实验单位和计时窗口。I/O 归一化热力图放在附录，只用于同口径描述。

## 定向场景

评委追问单一机制时，运行对应 Guest：

| 问题 | 场景 |
| --- | --- |
| Agent 身份与 Context 权限 | `agentfinal_ucore` |
| V2/V3 schema 与执行合同 | `agenttoolabi_ucore`、`agentcontract_ucore` |
| metadata、索引和 typed watch | `agentfs_ucore`、`agentbench_ucore` |
| event wait、heartbeat 与取消 | `agentloop_ucore` |
| workflow 公平与唤醒 | `agent_eevdf_ucore` |
| Task SQ/CQ 与 resync | `agenttask_ucore` |
| scope、额度与 teardown | `agentscope_ucore` |
| workflow fence receipt | `rp_agentos_orch`，入口为 `make dual-platform-run` |

示例：

```bash
AGENT_TEST_CASE=agentcontract_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

## 可选产品演示

时间充足时，优先展示固定 replay：

```bash
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

console replay 展示多轮工具结果回灌与审批；Nexus replay 展示四 Agent 委派、失败重规划、工件校验和发布拒绝。两者都走真实 QEMU Guest，但模型响应来自 digest-bound fixture。

live provider 只作为人工加演。没有实际 API 往返和完整 Guest 终态时，不把该次运行写成 live 验证，也不让它替代固定主演示。

## 现场故障处理

| 情况 | 处理 |
| --- | --- |
| 首次编译较慢 | 使用已经构建的同一源码产物，保留构建日志 |
| live 网络不可用 | 结束 live 会话，显式运行 replay；不静默切换 |
| 单个 QEMU 场景超时 | 保存串口日志，运行对应 `AGENT_TEST_CASE` 定位 |
| 本机新性能与文档不同 | 保留本机环境和逐样本输出，不覆盖 canonical 活动 |
| observer 未显示低层事件 | 查询 Context/timeline 或原始串口，不把缺席当作未发生 |

完整判定标准见[验证说明](verification.md)，Windows/WSL 命令见[快速开始](../windows-quickstart.md)。
