# AgentOS-uCore 性能测试

性能实验围绕 Live Query、Agent Task 和 workflow EEVDF 三条内核路径展开。我们保存每次 QEMU 启动的串口原始输出和逐样本表，以配对统计观察同一 workload 的路径差异，以参数网格观察目录规模、命中数和并发度变化后的性能趋势。

## 文档索引

- [1. 实验环境与数据](#1-实验环境与数据)
- [2. 采集设计与统计单位](#2-采集设计与统计单位)
- [3. Live Query 核心路径](#3-live-query-核心路径)
- [4. Catalog 参数网格](#4-catalog-参数网格)
- [5. 首次扫描、重复扫描与索引](#5-首次扫描重复扫描与索引)
- [6. Agent Task 传输路径](#6-agent-task-传输路径)
- [7. Workflow EEVDF](#7-workflow-eevdf)
- [8. 内核 I/O 与工作量](#8-内核-io-与工作量)
- [9. 逐样本数据与重绘](#9-逐样本数据与重绘)

## 1. 实验环境与数据

| 项目 | 配置 |
| --- | --- |
| Host | WSL2，x86_64 |
| Guest | QEMU RISC-V64 `virt`，单 Hart |
| 编译器 | `riscv64-linux-gnu-gcc 15.2.0` |
| QEMU | `10.2.1` |
| 采集时间 | 2026-08-11 00:27:57-01:03:08 UTC |
| 源码提交 | `2b14fb1f74b9bd093e6de939a16554620835699e` |
| 数据规模 | 30 次 fresh boot，33 个原始文件，19 张 CSV 表，7,498 行 |

[`manifest.json`](../one_shot_metrics/data/20260811/manifest.json) 保存源码提交、工具版本、Guest 源码摘要、构建产物摘要、采集计划和文件清单。[`validation.json`](../one_shot_metrics/data/20260811/validation.json) 核对 schema、配对关系、参数网格、逐操作行数和绘图输入，当前结果为 `valid=true`、`ready=true`。

## 2. 采集设计与统计单位

fresh QEMU boot 是实验 block；同一 boot 内的路径共享 workload、文件系统初始状态和 Guest 构建。配对实验先比较同一 block 中的两条路径，再汇总配对差值或加速比。boot 内的重复轮次保留为嵌套样本，用于展示分布和中位数。

| 内核路径 | Block | 配对或重复单位 | 原始样本 |
| --- | ---: | --- | ---: |
| Live Query workflow | 16 次 boot | 每个 boot 内 traversal/indexed 组成 1 对，AB/BA 各 8 对 | 16 个 core 对、16 个 E2E 对 |
| Catalog 参数网格 | 4 次 boot | 每个 catalog size × hit count 单元 15 对，单元内 AB/BA 交替 | 180 对、360 条路径样本 |
| Agent Task | 4 次 boot | 每条 sequence 含 16 个等价 ECHO；每 boot、每路径 8 轮 | 96 条 sequence、1,536 条 operation |
| Workflow EEVDF | 6 次 boot | 并发度 1-4 的 workflow 组与 exact wake probe | 180 条 workflow、504 条 wake probe |

Live Query 的 workflow core 和 end-to-end 使用两套计时窗口：

| 计时窗口 | 起止范围 | 回答的问题 |
| --- | --- | --- |
| workflow core | query、recovery write、`fsync`、结果复核 | 索引是否缩短 Agent 工作核心路径 |
| end-to-end | 文件准备、metadata 登记、完整 workflow、清理 | 一次完整运行在当前启动方式下耗时多少 |

这两个窗口分别统计并保持配对，正文结论也按窗口给出。

## 3. Live Query 核心路径

我们在同一份 96-record catalog 上运行 traversal 与 indexed。两条路径得到相同结果 hash；traversal 每次检查 97 条记录，indexed 检查 2 条。路径次序在 16 个 boot 中按 AB/BA 平衡。

| 指标 | Traversal | Indexed | 同 boot 配对结果 |
| --- | ---: | ---: | ---: |
| core duration 中位数 | 34,712.5 us | 13,293.5 us | `indexed - traversal` 中位数 -23,441.5 us |
| core 加速比 | - | - | `traversal / indexed` 中位数 3.118x |
| indexed 更快的 core 对 | - | - | 16/16 |
| end-to-end 中位数 | 711,283.5 us | 723,928.0 us | 配对差值中位数 +13,452 us |
| indexed 更快的 E2E 对 | - | - | 3/16 |

[![Live Query core 与 end-to-end](figures/performance/07_core_end_to_end_scope.png)](figures/performance/07_core_end_to_end_scope.pdf)

core 配对全部指向 indexed，说明 metadata 索引已经把查询、恢复写入和复核组成的核心路径缩短。当前 end-to-end 中，文件创建、metadata 登记、workflow 建立和清理占据了更多时间，indexed 的配对中位差为 `+13.452 ms`。因此，长驻 workflow 复用 catalog 与 lifecycle 时更容易把 core 收益转化为整体收益。

[![Live Query 配对时延分布](figures/performance/01_paired_core_performance.png)](figures/performance/01_paired_core_performance.pdf)

图中的哑铃连接同一次 boot 的 traversal 与 indexed；雨云图保留 16 组 core latency；ECDF 使用同 boot 的 `indexed - traversal` 差值。AB 与 BA 两组 core 加速比中位数分别为 `1.454x` 和 `5.481x`，次序效应较明显，因而主结果采用同 boot 配对差值，并在采集时保持 8/8 的顺序平衡。

## 4. Catalog 参数网格

参数实验将 catalog size 设为 24、64、96，将 hit count 设为 1、2、4、8。每个单元运行 15 个 scan/index 对，先核对 workload fingerprint 与 result fingerprint，再计算 `scan / index` 加速比中位数。

| Catalog size | 1 hit | 2 hits | 4 hits | 8 hits |
| ---: | ---: | ---: | ---: | ---: |
| 24 | 1.164x | 1.207x | 1.231x | 1.371x |
| 64 | 2.032x | 1.995x | 2.534x | 2.361x |
| 96 | 2.283x | 2.808x | 2.704x | 2.772x |

[![Catalog size 与 hit count 加速比](figures/performance/02_catalog_speedup_landscape.png)](figures/performance/02_catalog_speedup_landscape.pdf)

热力图给出 12 个实测单元，三维曲面使用同一组单元中位数。最小值出现在 `24 × 1`，为 `1.164x`；最大值出现在 `96 × 2`，为 `2.808x`。catalog 扩大后，scan 的候选处理量随目录增长，索引路径把候选集合保持在命中附近。当前数据支持在中大型 catalog 上优先建立索引，并把 hit count 作为选择性参数保留在查询计划中。

## 5. 首次扫描、重复扫描与索引

采集程序先执行显式 warmup，再记录每个参数组的 first retained scan、后续 scan 和 ready index。这里的“首次”指 warmup 后保留的第一条计时样本。每个 catalog 的 first retained scan 有 4 条，后续 scan 有 56 条，ready index 有 60 条。

| Catalog size | First retained scan | 后续 scan | Ready index |
| ---: | ---: | ---: | ---: |
| 24 | 133.1 us/query | 127.8 us/query | 98.3 us/query |
| 64 | 187.5 us/query | 218.8 us/query | 100.5 us/query |
| 96 | 388.5 us/query | 255.1 us/query | 108.3 us/query |

[![扫描状态分组](figures/performance/03_scan_state_groups.png)](figures/performance/03_scan_state_groups.pdf)

ready index 的中位时延从 24 条目录的 `98.3 us/query` 增至 96 条目录的 `108.3 us/query`；同期 first retained scan 增至 `388.5 us/query`。索引路径对 catalog size 的变化更平缓，适合作为多轮 Agent 查询的常驻数据结构。

## 6. Agent Task 传输路径

每条 sequence 发送 16 个语义等价的 ECHO 操作，三条路径的结果 fingerprint 均为 `31`。4 次 boot 各执行 8 轮，每条路径得到 32 个 sequence 样本。一次性测量 Guest 使用微秒计时保存 sequence 起止点，并为 1,536 个 operation 保存 service-start interval。

| 路径 | Sequence 样本 | 中位数 | IQR | 路径 syscall/sequence |
| --- | ---: | ---: | ---: | ---: |
| Batch | 32 | 561.0 us | 533.75-663.0 us | 1 |
| Scalar V3 | 32 | 2,051.0 us | 1,833.0-2,226.0 us | 16 |
| SQ/CQ | 32 | 1,620.5 us | 1,472.0-1,755.5 us | 2 |

[![Batch、Scalar V3 与 SQ-CQ 延迟](figures/performance/04_task_latency_distributions.png)](figures/performance/04_task_latency_distributions.pdf)

左图用雨云图展示 16-operation sequence 的全部样本，右图用对数横轴 ECDF 比较尾部。Batch 把 16 个描述符合并为一次进入，在当前同步 ECHO 负载中取得最低中位时延；SQ/CQ 用两次 enter 完成提交和收割，低于逐操作进入的 Scalar V3。Task Channel 的 setup、lifecycle info 和 contract 创建在 sequence 窗口外单独计数，适合由长驻 channel 在多轮任务间摊销。

## 7. Workflow EEVDF

6 次 boot 分别运行并发度 1、2、3、4 的 workflow 组，同时记录 16 次逻辑到达和 4 路线程放大场景。exact wake probe 跟踪 workflow 从进入 runnable 到实际 dispatch 的等待；公平性使用同一 boot、同一并发组内各 workflow 的原始 `service_cycles` 计算：

\[
J(x_1,\ldots,x_n)=\frac{(\sum_i x_i)^2}{n\sum_i x_i^2}.
\]

| 并发 workflow | Boot 数 | Jain fairness 中位数 |
| ---: | ---: | ---: |
| 1 | 6 | 1.000000 |
| 2 | 6 | 0.999985 |
| 3 | 6 | 0.999993 |
| 4 | 6 | 0.999985 |

[![EEVDF 唤醒延迟与 Jain 公平性](figures/performance/05_eevdf_latency_fairness.png)](figures/performance/05_eevdf_latency_fairness.pdf)

504 条 exact wake probe 中，425 条为 0 tick，79 条为 1 tick，没有右删失样本。并发参数组的 Jain 中位数均不低于 `0.99998`，所有单 boot 结果中的最小值为 `0.999918`。这组结果表明，当前单 Hart 负载下，被唤醒的 workflow 从进入 runnable 到获得 dispatch 的延迟为 0-1 tick，workflow service 在并发度 1-4 时保持接近均分。

## 8. 内核 I/O 与工作量

I/O 图把不同路径的工作量换算到可比较的业务单位。Live Query 的 lane counter 使用 workflow-core 窗口，目录、块和 VirtIO counter 使用全局 end-to-end 增量；两类 counter 都保留 owner、scope、window 与原始串口证据路径。Agent Task 则按 completed operation 归一化 syscall、描述符字节、control 字节和调度计数。

[![归一化内核 I/O 与工作量](figures/performance/06_normalized_io_heatmaps.png)](figures/performance/06_normalized_io_heatmaps.pdf)

热力图每一项在自身指标内比较路径，色值按该指标的最大绝对值归一化。Live Query core 中，traversal 每对检查 97 条记录并读取 7,811 bytes，indexed 检查 2 条记录且该 lane 的 `bytes_read` 为 0。Agent Task 的 syscall 强度分别为 Batch `0.0625`、Scalar V3 `1.0`、SQ/CQ `0.125` 次/operation，与 sequence 延迟的路径排序一致。工程上可以通过复用索引、批量提交和长驻 Task Channel 减少每项业务操作进入内核的固定成本。

## 9. 逐样本数据与重绘

| 数据 | 路径 | 用途 |
| --- | --- | --- |
| Live Query 配对 | [`contest_paired.csv`](../one_shot_metrics/data/20260811/tables/contest_paired.csv) | core/E2E 配对差值、加速比与 AB/BA |
| Catalog 参数网格 | [`agenteval_pairs.csv`](../one_shot_metrics/data/20260811/tables/agenteval_pairs.csv) | 12 单元热力图与三维曲面 |
| 扫描状态样本 | [`agenteval_samples.csv`](../one_shot_metrics/data/20260811/tables/agenteval_samples.csv) | first retained、repeat、ready index 分组 |
| Task sequence | [`task_sequences.csv`](../one_shot_metrics/data/20260811/tables/task_sequences.csv) | 16-operation latency 分布 |
| Task operation | [`task_operations.csv`](../one_shot_metrics/data/20260811/tables/task_operations.csv) | 逐 operation service-start interval |
| EEVDF wake | [`eevdf_wakeups.csv`](../one_shot_metrics/data/20260811/tables/eevdf_wakeups.csv) | exact wakeup latency ECDF |
| EEVDF service | [`eevdf_samples.csv`](../one_shot_metrics/data/20260811/tables/eevdf_samples.csv) | Jain fairness 重算 |
| 归一化工作量 | [`contest_io_normalized.csv`](../one_shot_metrics/data/20260811/tables/contest_io_normalized.csv)、[`task_perf_normalized.csv`](../one_shot_metrics/data/20260811/tables/task_perf_normalized.csv) | kernel I/O 与 Task 路径热力图 |
| QEMU 串口输出 | [`raw/`](../one_shot_metrics/data/20260811/raw/) | CSV 字段的原始 marker 与来源摘要 |

重新校验冻结表：

```bash
python3 one_shot_metrics/validate.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output ../agentos-validation.json
```

把文档图重绘到仓库外目录：

```bash
python3 docs/figures/performance/make_doc_figures.py \
  --output-dir ../agentos-figures
```

绘图脚本从 CSV 表读取数据，生成 PNG、PDF 与 SVG，并在结束前再次核对每张输入表的 SHA-256。日常 Guest 回归与一次性性能活动的关系见[测试说明](testing.md)。
