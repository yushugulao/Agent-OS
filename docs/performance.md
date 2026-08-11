# AgentOS-uCore 性能测试

性能实验覆盖 Live Query、工具传输和 workflow 调度三条内核路径。我们在 30 次独立 QEMU 启动中保存逐样本记录，用配对统计观察同一 workload 的路径差异，用参数网格观察负载变化后的性能趋势。

## 实验环境

| 项目 | 配置 |
| --- | --- |
| Host | WSL2，x86_64 |
| Guest | QEMU RISC-V64 `virt`，单 Hart |
| 编译器 | `riscv64-linux-gnu-gcc 15.2.0` |
| QEMU | `10.2.1` |
| 采集时间 | 2026-08-11 00:27:57-01:03:08 UTC |
| 源码提交 | `2b14fb1f74b9bd093e6de939a16554620835699e` |
| 数据规模 | 33 个原始文件、19 个 CSV 数据表、7,498 行记录 |

[`manifest.json`](../one_shot_metrics/data/20260811/manifest.json)记录源码提交、工具版本、采集计划、Guest 与构建摘要以及文件清单，[`validation.json`](../one_shot_metrics/data/20260811/validation.json)检查 schema、配对关系、参数网格和绘图输入。

## 实验设计

| 内核路径 | 独立启动 | 负载 | 样本 |
| --- | ---: | --- | --- |
| Live Query workflow | 16 | traversal/indexed 在同一 96-record corpus 内按 AB/BA 顺序配对 | 16 个 core 与 end-to-end 配对 |
| Live Query 参数网格 | 4 | catalog size `24/64/96`，hit count `1/2/4/8` | 每格 15 个内部配对 |
| Agent Task | 4 | Batch、Scalar V3、SQ/CQ 各 8 轮，每轮 16 op | 96 条 sequence、1,536 条 operation |
| Workflow EEVDF | 6 | 并发度 1-4 与 exact wake probe | 180 条 workflow、504 条 wake probe |

boot 是独立采集单位，boot 内的重复样本保留嵌套关系。配对实验在同一 corpus 和相同结果 hash 上比较路径，参数网格在每个单元内执行 AB/BA 顺序平衡。

## Live Query 核心路径

同一 96-record corpus 中，traversal 每次检查 97 条记录，indexed 检查 2 条。workflow core 计时覆盖 query、recovery write、`fsync` 和结果复核。

| 指标 | Traversal | Indexed | 配对结果 |
| --- | ---: | ---: | ---: |
| core duration 中位数 | 34,712.5 us | 13,293.5 us | 16/16 次 indexed 更快 |
| `indexed - traversal` 中位数 | - | - | -23,441.5 us |
| `traversal / indexed` 中位数 | - | - | 3.118x |
| end-to-end 中位数 | 711,283.5 us | 723,928.0 us | 配对差值 +13,452 us |

[![Live Query 核心与端到端计时](figures/performance/07_core_end_to_end_scope.png)](figures/performance/07_core_end_to_end_scope.pdf)

图中分别给出 core 与 end-to-end 配对差值。索引缩短了查询、恢复写入和复核组成的核心路径；端到端还包含文件创建、metadata 登记、完整 workflow 和清理，其配对中位差为 `+13.452 ms`。

[![Live Query 配对分布](figures/performance/01_paired_core_performance.png)](figures/performance/01_paired_core_performance.pdf)

配对哑铃连接同一次启动中的 traversal 与 indexed，雨云图保留 core latency 的全部样本，ECDF 展示 `indexed - traversal` 的配对差值。AB/BA 两种顺序下加速比中位数分别为 `1.454x` 和 `5.481x`，16 个配对的方向保持一致。

## Catalog 规模与命中数

我们把 catalog size 设为 24、64、96，把 hit count 设为 1、2、4、8。每个参数单元保留 15 个内部配对。

| Catalog size | 1 hit | 2 hits | 4 hits | 8 hits |
| ---: | ---: | ---: | ---: | ---: |
| 24 | 1.164x | 1.207x | 1.231x | 1.371x |
| 64 | 2.032x | 1.995x | 2.534x | 2.361x |
| 96 | 2.283x | 2.808x | 2.704x | 2.772x |

[![Catalog 参数网格](figures/performance/02_catalog_speedup_landscape.png)](figures/performance/02_catalog_speedup_landscape.pdf)

热力图给出 12 个实测单元的配对加速比中位数，三维曲面呈现目录规模与命中数共同变化时的趋势。catalog 从 24 增至 96 后，遍历处理的 metadata 数量增加，选择性索引把候选范围收缩到命中集合。

## Cold、Warm 与 Indexed

采集程序先完成显式 warmup，再记录 first retained scan、repeat scan 和 ready index。分组数据包含 12 条 first retained scan、168 条 repeat scan 和 180 条 ready index 样本。

[![查询状态分组](figures/performance/03_scan_state_groups.png)](figures/performance/03_scan_state_groups.pdf)

catalog size 为 24、64、96 时，ready index 的中位时延约为 `98.3/100.5/108.3 us/query`。分组图同时展示首次保留扫描、重复扫描和就绪索引的样本分布。

## Agent Task

每条 sequence 包含 16 个等价 ECHO 操作。4 次独立启动各运行 8 轮，三条传输路径各得到 32 个 sequence 样本。

| 路径 | 样本数 | 中位数 | IQR |
| --- | ---: | ---: | ---: |
| Batch | 32 | 561.0 us | 533.75-663.0 us |
| Scalar V3 | 32 | 2,051.0 us | 1,833.0-2,226.0 us |
| SQ/CQ | 32 | 1,620.5 us | 1,472.0-1,755.5 us |

[![Agent Task 延迟](figures/performance/04_task_latency_distributions.png)](figures/performance/04_task_latency_distributions.pdf)

图中并列展示 sequence 和逐 operation 的延迟分布。Batch 合并一组顺序操作，在当前同步负载中取得最低中位时延；SQ/CQ 使用固定队列与 terminal CQE，Scalar V3 保留逐次合同检查。

## Workflow EEVDF

6 次启动记录 504 条 exact wake probe，其中 425 条为 0 tick，79 条为 1 tick。公平性由同一 boot、同一并发场景下各 workflow 的 `service_cycles` 计算：

\[
J(x_1,\ldots,x_n)=\frac{(\sum_i x_i)^2}{n\sum_i x_i^2}.
\]

| 并发 workflow | Boot 数 | Jain fairness 中位数 |
| ---: | ---: | ---: |
| 1 | 6 | 1.000000 |
| 2 | 6 | 0.999985 |
| 3 | 6 | 0.999993 |
| 4 | 6 | 0.999985 |

[![EEVDF 唤醒与公平性](figures/performance/05_eevdf_latency_fairness.png)](figures/performance/05_eevdf_latency_fairness.pdf)

ECDF 展示 Agent 从事件等待恢复到可运行状态的 tick 分布，折线图展示并发度变化下的 Jain 指数。504 次唤醒均在 0 至 1 tick 内完成，并发参数组的 Jain 中位数均不低于 `0.99998`。

## 内核工作画像

[![归一化内核工作量](figures/performance/06_normalized_io_heatmaps.png)](figures/performance/06_normalized_io_heatmaps.pdf)

上半图把 workflow-core 的 `bytes_read` 和 end-to-end 的全局 I/O 计数分别除以 core-window syscall；下半图按 completed operation 归一化 Agent Task 传输成本。每一行使用独立量纲与色标，用于比较同一指标在不同路径下的相对变化。

## 逐样本数据

- [Live Query 配对](../one_shot_metrics/data/20260811/tables/contest_paired.csv)
- [Catalog 参数网格](../one_shot_metrics/data/20260811/tables/agenteval_pairs.csv)
- [Agent Task sequence](../one_shot_metrics/data/20260811/tables/task_sequences.csv)
- [Agent Task operation](../one_shot_metrics/data/20260811/tables/task_operations.csv)
- [EEVDF wake probe](../one_shot_metrics/data/20260811/tables/eevdf_wakeups.csv)
- [EEVDF service](../one_shot_metrics/data/20260811/tables/eevdf_samples.csv)
- [QEMU 原始输出](../one_shot_metrics/data/20260811/raw/)

## 校验与重绘

```bash
python3 one_shot_metrics/validate.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output ../agentos-figures/validation.json

python3 docs/figures/performance/make_doc_figures.py \
  --output-dir ../agentos-figures
```

校验和绘图结果写入仓库外目录，原始数据保持冻结。性能负载的 Guest 入口和日常回归关系见[测试说明](testing.md)。
