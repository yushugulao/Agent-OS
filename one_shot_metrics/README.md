# AgentOS one-shot figure campaign

本目录保存一次性科研绘图采集活动。它不属于日常回归测试，不接入顶层
`Makefile`、CI、`full-verify` 或默认 QEMU 入口。活动完成后，仓库只保留采集
定义、逐样本原始数据、运行清单和可重绘脚本；不会在常规开发流程中再次运行。

规范数据集由 [`CANONICAL_RUN`](CANONICAL_RUN) 指向。正式绘图应从该运行的
`tables/` 读取长表，而不是从文档中的聚合数字反推。`raw/` 保留串口输出，
`manifest.json` 记录源码提交、Guest 变体哈希、负载矩阵、工具版本和每个 boot
的状态。

## 为什么是独立活动

生产测试首先验证语义和安全合同，通常只输出 p50/p99 或很少的固定样本。
绘图需要不同的数据形状：配对原始值、独立 boot、参数网格、每操作间隔、
调度窗口内各 workflow 的服务量，以及归一化之前的 I/O 计数。因此本目录用
外置 `app_dir` 生成只服务本次活动的 Guest 源，不修改 `user/src` 中的生产测试。

`run_once.py` 只接受显式的 `--acknowledge-one-shot`，拒绝 CI 环境和已存在的
规范结果目录。`COMPLETED` 写入后，普通调用会拒绝再次采集。需要设计下一次
活动时应新建日期目录和新计划，而不是覆盖本次数据。

## 采集设计

| 数据族 | 设计 | 独立单位 | 主要原始表 |
| --- | --- | --- | --- |
| workflow traversal/indexed | 16 个独立 QEMU boot，平衡 AB/BA | boot | `contest_paired.csv`, `contest_paths.csv`, `contest_io_normalized.csv` |
| file-query catalog grid | catalog `24/64/96` x total exact hits `1/2/4/8`，每格 15 个 AB/BA block | boot 为推断单位，inner pair 为重复测量 | `agenteval_samples.csv`, `agenteval_pairs.csv`, `agenteval_diagnostics.csv` |
| batch/scalar/SQ-CQ | 4 个 boot，每 boot 8 个轮换顺序 block，每路径 16 op | boot/block | `task_sequences.csv`, `task_operations.csv`, `task_perf.csv` |
| workflow EEVDF | 6 个 boot；物理并发 `1/2/3/4`；fresh waiter 每次 timeout 后读取 scheduler trace | boot/window 或 wake probe | `eevdf_samples.csv`, `eevdf_wakeups.csv`, `eevdf_jain.csv` |

同一 boot 内的 inner pair 不是新的独立机器实验。置信区间或显著性分析应先按
各表的 source file/sample/boot-round 标识聚合，或采用明确的分层模型。此次
样本量定位为工程探索和文档绘图，
不是跨硬件的总体推断。

## 图表合同

1. traversal/indexed 配对哑铃图：同一 `sample_id`（一份独立 QEMU boot）的
   两端，单位为 workflow `core_duration_us`。
2. core latency 雨云/小提琴图：同一批 boot 的 workflow core 区间。该区间
   包含查询、恢复写入、`fsync` 和复核，不称为纯查询 syscall latency。
3. 配对差值 ECDF：直接读取 `indexed_minus_traversal_core_us`（即
   `indexed_core_duration_us - traversal_core_duration_us`），负值表示 indexed 更快。
4. catalog size x hit count 热力图：每格先在 boot 内按 pair 聚合
   `scan_us / indexed_us`，保留原始 block duration。
5. 三维性能曲面：与热力图使用同一网格和同一聚合，不增加伪造采样点。
6. first/repeat/indexed 分组图：使用显式 warmup 后保留的第一个 timed scan、
   后续 repeat scan 和 ready index。`first` 不等同于可证明的物理存储冷缓存，
   因而图中不会把它标成 cold cache。
7. batch/scalar/SQ-CQ 延迟分布：主值是 16-op sequence 的微秒耗时；逐操作
   `service_start_interval_tick` 只有 10 ms 粒度，单独作为调度 trace 展示，
   不均摊成伪造的逐请求微秒值。
8. EEVDF wakeup ECDF：来自 `agent_sched_record.ready_age` 的逐 probe 原始 tick，
   不是从四桶直方图插值；`ready_age=200` 标记为右删失。
9. Jain fairness 折线：在同一个测量窗口按各 workflow 的原始
   `service_cycles` 计算 `(sum s)^2 / (n * sum s^2)`。并发 1 的 Jain=1 是平凡
   基线；scenario 16 的四波不得标成 16-way concurrency。
10. 内核 I/O 与工作量归一化热力图：规范长表同时保留 raw numerator、scope、
    window、owner 和 denominator；before/after 的完整证据保留在对应 raw serial。
    `bytes_read` 是 workflow lane/core reported total，其余 contest numerator 才是
    shared global/end-to-end delta；Task 是直接 work count/bytes。不同所有者和
    window 不能混成同一个进程归因。

## 已完成的规范运行

`data/20260811/` 已完成一次正式采集和冻结：共 30 个 fresh QEMU boot，保留
33 个 raw 文件，提取为 19 张长表。主要样本包括 16 个 workflow 配对、
`3 x 4 x 15 = 180` 个 file-query 配对、96 个 Task sequence、1,536 个 Task
operation interval、504 个 exact EEVDF wake probe。验证结果为 `valid=true`、
`ready=true`，10 张图均同时生成 PNG 和 PDF。

EEVDF boot 05 有 3 条并发串口输出发生拼接。提取器保留并标注该事实，排除不完整
的 sample summary；504 条带完整前缀的 exact wake probe 均已保留。这个 warning
记录在 [`validation.json`](data/20260811/validation.json)，没有被静默修补或插值。

公开提交前，我们仅脱敏了 `manifest.json` 与运行日志中的主机名、绝对工作区和临时
staging 标识。`publication_redaction` 保存修改前哈希；33 个 raw 文件和数据表均未改变。

## 长表清单

规范运行包含以下 19 张 CSV：

`contest_paired`, `contest_paths`, `contest_io_normalized`, `agenteval_samples`,
`agenteval_pairs`, `agenteval_diagnostics`, `agenteval_concurrency_samples`,
`agenteval_concurrency`, `task_sequences`, `task_operations`, `task_perf`,
`task_perf_normalized`, `task_fingerprints`, `eevdf_samples`, `eevdf_cohorts`,
`eevdf_jain`, `eevdf_wakeups`, `eevdf_wake_histogram`, `eevdf_amplification`。

每行都保留稳定的 `raw/...` 来源相对路径、来源 SHA-256 和来源内序号；实验特有的
boot、sample、pair、scenario、workflow 标识则留在相应表中。原始计数不会只以
归一化数值替代。

## 重绘

在装有 `matplotlib`、`pandas` 和 `numpy` 的 Python 环境中运行：

```text
python one_shot_metrics/plot.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output-dir ../agentos-20260811-reproduced/figures \
  --format png,pdf
```

绘图脚本只读取 CSV，不启动 QEMU。示例把 PNG/PDF 写到仓库外的 scratch 目录；
规范运行的 `data/20260811` 及其 145-entry manifest inventory 是只读冻结证据，
不得用重绘结果覆盖。
数据完整性检查是：

```text
python one_shot_metrics/validate.py \
  --tables one_shot_metrics/data/20260811/tables \
  --output ../agentos-20260811-reproduced/validation.json
```

这些命令只做离线校验或重绘，不运行 Guest、构建镜像或启动 QEMU。原始采集入口
仍需 `--acknowledge-one-shot`，且规范目录已有 `COMPLETED` 后会 fail closed。

详细口径和文档引用见
[`docs/agentos/advanced-performance-figures.md`](../docs/agentos/advanced-performance-figures.md)。
