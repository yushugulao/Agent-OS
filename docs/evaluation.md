# AgentOS 评价方法

评价体系同时回答四个问题：赛题功能是否真实可用，Agent workload 是否从内核机制
获益，高负载下是否保持公平和隔离，以及这些能力带来了多少内核成本。原始 Guest
观测、环境指纹和逐样本 CSV 是结论来源，Dashboard 只负责呈现。

## Workload

正式套件为 Evaluation v5。它在同一提交上运行功能闭包、微基准、并发负载和科研
流程，不用公式生成测量值，也不接受用户程序硬编码的“通过证据”。

| Workload | 主要内核路径 | 结果判定 |
| --- | --- | --- |
| Agent 创建与隔离 | trusted exec、workflow lifecycle、resource domain | 身份正确、跨 scope 污染为零 |
| 结构化工具调用 | V1/V2 decoder、`agent_run` batch、Context commit | 输出等价、sequence 连续、错误类型准确 |
| 多轮 Context | append、query、rollback、timeline | active path 正确、历史不可改写、hash 连续 |
| 文件与工件 | VFS policy、metadata scan/index、digest | 结果集合等价、候选记录数和 I/O 可测 |
| 事件驱动循环 | IPC route、wait/wake、heartbeat、scheduler | 投递正确、等待分位数、公平性和 goodput |
| 科研恢复流程 | provenance、action、artifact、teardown | 最终状态、幂等恢复、资源回收一致 |

评价维度参考 [AIOS 论文](https://openreview.net/pdf?id=L4HHkCDz2x) 的任务成功率、
syscall throughput、等待时间和并发扩展问题，但使用为本项目重新实现的内核
workload。没有复制 AIOS 当前仓库中许可不明确的代码或结果，也不重新分发受限
数据集。

四类应用语义被映射到现有内核场景：

- HumanEval 风格：创建代码工件、编译、读取结果和清理生命周期；
- MINT 风格：多轮工具调用、Context 切换、rollback 后继续执行；
- GAIA 风格：文件、IPC、工具和权限共同参与一个 workflow；
- SWE-bench 风格：跨工件更新、失败恢复、provenance 和幂等提交。

这些名称描述 workload 形态，不宣称运行了原数据集。外部 benchmark 只有在许可、
版本和输入清单一起进入 evidence bundle 后才能以原名称计分。

## 对照设计

### 机制消融

同一个 AgentOS Guest、同一个输入和同一个结果检查器，对比 scalar/batch、
scan/index、syscall/安全只读映射、busy-poll/blocking-wait 等成对路径。每对样本必须具有
相同 operation count、workload fingerprint 和 result fingerprint。

这种对照用来回答机制是否减少工作量。例如索引优势同时报告耗时和
`records_examined`，批量调用同时报告耗时和 syscall 数，不能只展示一个缓存命中
后的最佳数字。

### Plain uCore 对照

Plain 目标只实现完成同一科研任务所需的传统进程、文件和 pipe 路径；AgentOS 目标
使用内核 Agent 机制。两侧使用相同输入工件和最终状态检查。该对照展示完整场景的
工程价值，不把 Plain 缺少的安全/观测功能计成“零成本实现”。

### 兼容路径

传统 `open/read/write/close`、pipe 和进程操作单独测量。结果同时给出绝对耗时、
操作数和相对开销，防止 Agent 功能改善掩盖传统接口退化。冷缓存与热缓存分栏，
缓存效果不混入机制对照。

传统项目选择对齐系统能力赛官方测试中的
[lmbench syscall/进程延迟](https://github.com/oscomp/testsuits-for-oskernel/blob/2371216841401172a535765b9541b629c99081c4/scripts/lmbench/lmbench_testcode.sh)、
[UnixBench 1/8/16 并发](https://github.com/oscomp/testsuits-for-oskernel/blob/2371216841401172a535765b9541b629c99081c4/scripts/unixbench/unixbench_testcode.sh)
和 [IOzone I/O 组合](https://github.com/oscomp/testsuits-for-oskernel/blob/2371216841401172a535765b9541b629c99081c4/scripts/iozone/iozone_testcode.sh)。
当前内核仍是单 Hart，Host 并行 QEMU 只用于缩短验收时间，不作为 Guest SMP 加速证据。

### 内核成本

每个候选记录 production source、stripped text/data/BSS、raw image、`struct proc`、
最大用户/中断/boot 栈路径和按需 Agent 状态。预算值以 `ci/kernel-budgets.json` 和
`make kernel-budget-check` 为准，文档不复制易过期的数字。

## 指标

| 类别 | 指标 |
| --- | --- |
| 正确性 | 完成率、结果 fingerprint、rollback 一致率、污染/回退次数 |
| 延迟 | queue wait、service、turnaround 的 p50/p90/p99 |
| 吞吐 | syscall/s、completed workflow/s、deadline goodput |
| 公平 | Jain fairness、max/min progress、跨 workflow bounded progress |
| 工作量 | syscall 数、records examined、cache hit、disk transfer、MMIO notify |
| 资源 | text/data/BSS、页数、stack、峰值 file/proc/thread/I/O account |

吞吐和延迟必须来自 Guest 单调时钟围住的真实 workload。结果校验、hash 和 marker
打印发生在计时区间外。提交到开始、开始到完成、提交到接收分别形成 wait、service
和 turnaround，避免把服务时间误写成排队时间。

## 竞赛性能门槛

这些门槛是项目的工程目标，不是题面给出的统一分数线。每项都在相同工具链、QEMU、
Guest vCPU、输入和操作数下比较；冷缓存与热缓存分别判定，不能用平均值掩盖单项退化。

| 场景 | 采样点 | 主要数据 | 项目门槛 |
| --- | --- | --- | --- |
| 传统接口 | `fork/exec/wait`、pipe 往返、`open/read/close`、4 KiB `write` | cycles/op、ops/s、p50、p99 | AgentOS 相对 Plain uCore：每项 p50 退化不超过 5%，p99 不超过 10% |
| 工具批量 | batch 1/8/64，scalar 与 batch 成对 | cycles/op、syscall/op、p50、p99 | batch=1 退化不超过 5%；batch=64 的 cycles/op 至少降低 50% |
| Context | 5/20/50 轮 push/query/rollback；带 seqlock 的只读映射与 syscall 成对 | p50/p99、重试次数、copy 次数、峰值页数、结果 fingerprint | 安全直接读取快于 syscall 路径，历史与 active path 错误数为 0 |
| 文件查询 | 64/256/512 条 metadata，scan/index，冷/热缓存分栏 | p50/p99、records examined、块 I/O | index 的耗时和检查记录数都低于同组 scan |
| 多 Agent | 1/4/16 个确定性 workflow | makespan、goodput、wait/turnaround p50/p90/p99、Jain fairness | 所有 workflow 有界进展；16 Agent 时 Jain fairness 不低于 0.95 |
| 空闲与唤醒 | 无事件窗口、消息、heartbeat | polling iterations、dispatch 增量、唤醒 p50/p99 | 无事件时 polling 与 Agent runnable dispatch 增量均为 0；唤醒延迟报告实测值 |
| 内核成本 | production ELF 与预算探针 | text/data/BSS、raw image、`struct proc`、栈、Agent 按需页 | 每项同时报告相对 Plain 和上一提交的绝对值与增量，不允许无说明增长 |

传统路径门槛按 workload 逐项执行，不用一个总分抵消回退。Agent 场景只有在结果
fingerprint 一致时才计算加速比；fallback、超时或污染样本仍进入完成率和尾延迟。
所有表格只读取原始 CSV，缺少 Guest 日志、环境指纹或样本 hash 时显示“无数据”，
不得用公式、常量或插值补齐。

## 采样纪律

1. 正式 campaign 只接受干净提交和固定工具身份。
2. 对照两侧使用相同 challenge、输入、操作数和结果检查。
3. 正式门只运行一个预注册 Guest boot；微基准在该 boot 内保留 AB 与 BA 配对，平台对照使用预注册顺序。
4. Guest boot 仍是统计独立单位；单轮结果只作描述性观察，不能据此声明跨启动统计显著。
5. 冷、热缓存分开登记，正式结论不能挑选其中较好的一组。
6. 保留每个样本，不删除失败、污染、fallback 或慢样本。
7. 预注册 load 和停止规则；采集后不能通过改阈值重解释结果。

headline claim 使用 boot 内配对结果和预注册的绝对/相对 MCID。置信区间用于描述
波动，功能状态和性能状态分开呈现：任务完成不等同于性能提升，负结果也保留为
有效实验结果。单轮发布中，缺少跨启动样本的性能 claim 保持 `not_supported` 或
`inconclusive`，但功能回执、原始性能数据和回归结果仍进入正式证据包。

## 自适应并发

Host 测试、构建和独立 QEMU lane 根据可用 CPU 与内存选择 worker 数。QEMU 并发
还受独立磁盘镜像和 build slot 约束；嵌套 Make 复用外层 jobserver。正式证据报告
requested/effective jobs、lane inventory 和每步耗时，因此并行缩短验收时间不会被
误写为 Guest 性能提升。

多个会修改同一磁盘状态的实验保持串行。并行度的目的只是更快完成彼此独立的
采集，不改变单个 Guest 的虚拟 CPU 配置或样本定义。

## 运行

先检查环境和快速合同：

```bash
export AGENT_TEST_DURATION_PROFILE=local-e3
make evaluation-doctor
make evaluation-smoke
```

正式采集要求工作树干净：

```bash
make evaluation-run
make evaluation-verify
make evaluation-kernel-cost
make evaluation-full-verify
```

生成页面和交付包：

```bash
make evaluation-dashboard
make evaluation-package
```

`evaluation-package` 会新增 `evidence/releases/<bundle>` 并更新索引。随后只提交这两项
作为证据提交 E；在包含 E 的干净 checkout 中执行：

```bash
make evaluation-package-verify \
  EVALUATION_BUNDLE_DIR=evidence/releases/<bundle>
```

`make evaluation-full-verify` 将最终 evaluation 与整套内核验收绑定，是正式打包的
前置证据，不会由 `evaluation-package` 自动补跑。远端默认没有 Runner；这些命令在
本地固定工具链环境执行，发布的 bundle 可离线复验。

正式采集在一次性私有 staging 目录重新运行 Guest workload，不读取普通调试 run 的
临时结果。双目标测量以随机 generation 区分；Guest log、manifest 和 CSV 全部通过
校验后才原子发布完成 receipt，启动失败或缺少 receipt 的目录不能进入 bundle。

## 证据与 Dashboard

Evidence bundle 保存：

- commit、tree、工具链与 QEMU/Python/Bash/Make 身份；
- versioned suite、run plan、case inventory 和执行顺序；
- 原始 Guest log、逐样本 CSV、功能 receipt 与文件 hash；
- 配对统计、kernel cost、兼容路径和科研场景结果；
- Dashboard 文件及其验证回执。

Dashboard 首屏展示候选与对照的具体数值：传统路径开销、batch/scan-index 加速、
等待分位数、goodput、公平性、BSS/text/stack 成本和 workload 完成率。状态标签用于
说明数据是否可用，不能替代数字。每个图表都能回到 CSV 和原始 Guest 证据。

当前验证器只接受 v5。仓库没有已发布的旧 v2-v4 bundle，因此不为未交付的开发
格式保留兼容分支；需要比较历史提交时应使用当时提交中的验证器。
