# AgentOS 验证说明

AgentOS 按“源码合同、交叉构建、QEMU 行为、产品闭环、性能数据”五层验证。每一层只回答对应问题：静态 checker 不能替代 Guest 运行，offline replay 不能替代 live provider，单项 benchmark 也不能推出整个系统的端到端加速。

## 验证层次

| 层次 | 入口 | 主要证据 |
| --- | --- | --- |
| 源码合同 | `agent-uapi-check`、`agent-module-check` | ABI size/offset、syscall、模块边界、静态不变量 |
| 交叉构建 | `build`、`kernel-stack-check` | RISC-V 编译链接、真实调用图栈上界 |
| QEMU Guest | `agentos-test` 与 `AGENT_TEST_CASE` | fork/exec、VFS、IPC、Context、错误码、teardown |
| 产品闭环 | `contest-demo`、console/Nexus replay | 真实 Guest workflow、Host 采集、结构化 validator |
| 性能活动 | `one_shot_metrics/data/20260811` | fresh boot、逐样本表、manifest、validation 和图表 |

## 依赖与构建

```bash
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`make doctor` 只检查环境。`make build` 证明当前目标可以编译链接。`kernel-stack-check` 分析真实调用图上的内核栈上界，不是运行时内存或延迟测量。

Windows/WSL 环境见 [Windows 快速开始](../windows-quickstart.md)。使用 xPack 等裸机工具链时，将前缀改为 `riscv-none-elf-`。

## 源码合同

```bash
make agent-uapi-check
make agent-module-check
make contest-demo-check
make agentos-console-check
make agentos-nexus-check
```

这些目标验证：

- 公开 ABI 的 version、size、offset、syscall 号和兼容前缀；
- production module 没有测试替身或越层实现；
- Live Query 只接受显式 metadata，typed watch 有 generation resync；
- workflow fence 的 quiescence、credit exact 和 evidence seal 顺序；
- contest、console 与 Nexus 的 Host 协议、参数和 validator；
- Guest loop 源码中的有界 frame、round、approval 和状态机合同。

本层不启动 QEMU，也不说明 provider、VFS 或调度在真实 Guest 中已经运行。

## QEMU Guest 回归

完整回归入口为：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

修改单一模块时优先运行定向场景：

| 场景 | 验证重点 |
| --- | --- |
| `agenttrust_ucore` | 可信映像、身份继承、exec policy 和权限拒绝 |
| `agentfinal_ucore` | Agent 创建、Context、branch/rollback、canonical evidence 与普通进程共存 |
| `agenttoolabi_ucore` | 25 项目录、V1/V2 参数、状态码和错误形状 |
| `agentcontract_ucore` | ENFORCE V3、前驱、attempt、deadline、重试和 Phase Lease |
| `agentfs_ucore`、`agentbench_ucore` | inode 绑定、metadata、query plan、typed watch 与 scan/index 对照 |
| `agentloop_ucore` | event wait、route、heartbeat、LLM correlation 和 cancel |
| `agenttask_ucore` | SQ/CQ、terminal、cancel、deadline、backpressure 和 resync |
| `agent_eevdf_ucore` | workflow 服务量、公平性和 wakeup probes |
| `agentscope_ucore` | workflow 隔离、资源硬额度和回收 |

示例：

```bash
AGENT_TEST_CASE=agentcontract_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

runner 检查 QEMU 退出状态、预期 pass marker、panic、输出上限和 timeout。超时表示该场景失败，不形成平台认证结论。

## 主演示

```bash
make contest-demo TOOLPREFIX=riscv64-linux-gnu-
```

当前目标运行 4 个隔离 QEMU boot，按 AB/BA 顺序比较 traversal 与 indexed。每个配对要求相同输入 corpus、相同 recovered 结果、相同结果 hash，并保存：

- 每条路径的 core 与 end-to-end 时间；
- traversal/indexed 实际检查记录数；
- I/O 和工作量计数；
- 原始串口日志、`measurements.csv`、`summary.json` 与 `report.md`。

主演示用于本机复现。文档中的正式统计来自更大的 2026-08-11 一次性活动，不用 4 boot 快照覆盖 30 boot canonical 数据。

## Console 与 Nexus

固定、无网络的 QEMU 验收为：

```bash
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

console replay 在同一 Guest session 中完成三个用户回合、真实工具调用、一次审批拒绝、一次批准、Context 读取和关闭。Nexus replay 创建四个业务 Agent，完成委派、失败重规划、两份来源工件、Analyst 汇总和发布拒绝。

两者都使用 digest-bound fixture。validator 检查 model request/response 对应、single-inflight 顺序、task terminal、artifact digest、审批绑定和 close 后零输出。replay 经过真实 QEMU、串口和内核工具路径，但响应来自固定文件。

`agentos-console-deepseek` 和 `agentos-nexus-deepseek` 是可选人工入口。只有当次实际 API 往返、Guest 工具执行和最终 session marker 全部出现，才可记录为 live 验证。仓库现有 canonical 结果不包含这一声明。

## 一次性性能活动

冻结数据位于 [`one_shot_metrics/data/20260811`](../../one_shot_metrics/data/20260811/)。活动在源码提交 `2b14fb1f74b9bd093e6de939a16554620835699e` 上完成，记录 WSL2、QEMU 10.2.1、RISC-V64 单 Hart 和交叉编译器版本。

| 实验 | 独立 boot | 原始样本 |
| --- | ---: | ---: |
| traversal/indexed 综合配对 | 16 | 16 个 AB/BA 配对 |
| AgentEval 参数网格 | 4 | 1,560 条 suite 样本、180 个 grid 配对 |
| Agent Task | 4 | 96 条 16-op sequence、1,536 条 operation |
| workflow EEVDF | 6 | 180 条 workflow、504 条 exact wake probe |

合计 30 次 fresh QEMU boot、33 个 raw 文件、19 张 CSV 表和 7,498 行记录。[`validation.json`](../../one_shot_metrics/data/20260811/validation.json) 为 `valid=true`、`ready=true`，包含 0 error 和 1 项已说明的串口拼接 warning。

核心结果为：

- indexed workflow core interval 在 16/16 个配对中更短，paired speedup 中位数为 `3.118x`；该 interval 包含 query、recovery write、`fsync` 和 verify；
- end-to-end 仅在 3/16 个配对中 indexed 更快，paired delta 中位数为 `+13,452 us`；
- 12 个 `catalog × hit` 实测格的中位 speedup 为 `1.164x–2.808x`；
- 16-op batch、scalar V3、SQ/CQ 中位延迟为 `561 us`、`2,051 us`、`1,620.5 us`；
- 504 条 exact wake probe 全部为 0–1 tick；并发度 1–4 的 Jain fairness 中位数均不低于 `0.99998`。

完整方法和图表见[实测性能结果](../contest/performance-results.md)。

## 数据复核

冻结目录已经写入 `COMPLETED`，不再启动 QEMU 重新采集。校验与重绘输出写到仓库外目录：

```bash
python one_shot_metrics/validate.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output ../agentos-20260811-reproduced/validation.json

python one_shot_metrics/plot.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output-dir ../agentos-20260811-reproduced/figures \
  --format png,pdf
```

`manifest.json` 保存环境、命令、Guest 镜像 hash 和输入清单；`validation.json` 校验 schema、配对、参数网格与图表输入。评审时优先保留原始表和日志，不从 PNG 反推数值。

## 结论边界

1. 全部正式数据来自一套 WSL2 + QEMU 单 Hart 环境，绝对时间不能外推到裸机或 SMP。
2. 16 个综合配对来自独立 boot；AgentEval 单格 15 个 AB/BA 配对属于 boot 内重复；Task 的 32 条路径样本来自 4 boot × 8 轮。
3. workflow core 与 end-to-end 是两个时间窗口。`3.118x` 只属于包含 query、recovery write、`fsync` 和 verify 的 workflow core path；纯查询块属于 AgentEval 专项。
4. observer、timeline 和日志行数不是性能计时器。
5. MCP/A2A 单测只验证 in-memory prototype；console/Nexus replay 不证明 live provider。
6. 通过 checker、构建或单一 Guest 场景，只能报告其覆盖的合同。

赛题任务到源码和测试的映射见[要求追踪表](requirements-traceability.md)，现场顺序见[演示脚本](scenario-script.md)。
