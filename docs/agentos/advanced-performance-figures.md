# 性能图与原始数据

本页集中给出决赛文档使用的性能图。图表读取
[`one_shot_metrics/data/20260811`](../../one_shot_metrics/data/20260811/)
中的逐样本表，由
[`make_doc_figures.py`](../figures/performance/make_doc_figures.py)
一次生成 PNG、PDF 和 SVG。PDF 用于 LaTeX，PNG 用于 Markdown，SVG 便于后续编辑。

## 数据规模

2026-08-11 活动完成 30 次 fresh QEMU boot，保留 33 个原始文件、19 张长表和
7,498 行记录。验证结果为 `valid=true`、`ready=true`、0 个错误和 1 个已记录的
串口拼接告警。运行清单、文件摘要和失败记录见
[manifest.json](../../one_shot_metrics/data/20260811/manifest.json) 与
[validation.json](../../one_shot_metrics/data/20260811/validation.json)。

| 实验 | 独立启动 | 逐样本结构 | 主要用途 |
| --- | ---: | --- | --- |
| traversal / indexed | 16 | 每个 boot 一组 AB/BA 配对 | 核心窗口与端到端对照 |
| file-query grid | 4 | `24/64/96 x 1/2/4/8`，每格 15 个内部配对 | 规模与命中数交互 |
| Task transport | 4 | 每路径 8 轮，每轮 16 op | Batch、Scalar V3、SQ/CQ 分布 |
| EEVDF | 6 | 504 条 exact wake probe | 唤醒时延与公平性 |

## 正文图组

### 核心窗口与端到端窗口

[![核心窗口与端到端窗口](../figures/performance/07_core_end_to_end_scope.png)](../figures/performance/07_core_end_to_end_scope.pdf)

16 个配对的核心窗口全部支持 indexed，配对加速比中位数为 `3.118x`。端到端窗口
只有 3/16 个配对支持 indexed，`indexed - traversal` 中位数为 `+13.452 ms`。
这张图将两种测量范围并列，正文的性能结论限定在 workflow core path。

### 配对分布与稳健性

[![配对哑铃、云雨分布与差值 ECDF](../figures/performance/01_paired_core_performance.png)](../figures/performance/01_paired_core_performance.pdf)

哑铃图保留同一 boot 内的配对关系；云雨图显示原始点和中位数；ECDF 展示
`indexed - traversal` 的完整差值分布。两个 AB/BA 顺序分层的加速比中位数分别为
`1.454x` 和 `5.481x`，因此图中同时保留执行顺序。

### 目录规模与命中数

[![查询加速比热力图与实测曲面](../figures/performance/02_catalog_speedup_landscape.png)](../figures/performance/02_catalog_speedup_landscape.pdf)

热力图覆盖 12 个实测单元，每格 15 个内部 AB/BA 配对，没有插值补点。单元加速比
中位数为 `1.164x` 到 `2.808x`。右侧三维曲面使用真实 catalog size 与 hit count
坐标，黑点标出采样位置；精确读数以热力图为准。

### 首次扫描、重复扫描与就绪索引

[![扫描状态分组图](../figures/performance/03_scan_state_groups.png)](../figures/performance/03_scan_state_groups.pdf)

分组图比较显式 warmup 后的 first retained scan、repeat scan 和 ready index。
catalog size 为 24、64、96 时，ready index 的中位时延约为
`98.3/100.5/108.3 us/query`。样本来自 4 次 boot 内的重复块，图中保留嵌套来源。

### Task 三条传输路径

[![Task 延迟与服务启动间隔](../figures/performance/04_task_latency_distributions.png)](../figures/performance/04_task_latency_distributions.pdf)

96 条 16-op sequence 中，Batch、Scalar V3、SQ/CQ 的中位耗时为
`561/2051/1620.5 us`。右侧面板给出 1,536 个服务启动间隔；调度 tick 为 10 ms，
该面板只解释离散调度位置。

### EEVDF 唤醒与公平性

[![EEVDF 唤醒 ECDF 与 Jain 公平性](../figures/performance/05_eevdf_latency_fairness.png)](../figures/performance/05_eevdf_latency_fairness.pdf)

504 条 exact wake probe 中，425 条为 0 tick、79 条为 1 tick，最大值为 1 tick。
基于原始 `service_cycles` 重算的 Jain 中位数在并发 1/2/3/4 下为
`1.000000/0.999985/0.999993/0.999985`。公平性面板使用聚焦纵轴展示接近 1 的差异。

## 附录图

[![归一化 I/O 工作画像](../figures/performance/06_normalized_io_heatmaps.png)](../figures/performance/06_normalized_io_heatmaps.pdf)

上半图的 `bytes_read` 是 workflow actor 的 core-window lane work；其余文件查询分子
来自 shared kernel 的 end-to-end delta，再除以 actor 的 core-window syscall。
下半图按 completed operation 归一化 Task transport 成本。颜色只在同一 metric 内
比较，用于识别工作构成。

规范活动还保留 10 张单用途图，位于
[`one_shot_metrics/data/20260811/figures`](../../one_shot_metrics/data/20260811/figures/)。
它们适合在附录中单独引用，包括哑铃图、小提琴图、配对差值 ECDF、热力图、三维曲面、
分组图、Task 分布、EEVDF ECDF、Jain 折线和 I/O 热力图。

## 复现

规范目录出现 `COMPLETED` 后保持只读。离线校验和重绘写入仓库外目录：

```powershell
python one_shot_metrics/validate.py `
  --tables one_shot_metrics/data/20260811/tables `
  --output ../agentos-20260811-reproduced/validation.json

python docs/figures/performance/make_doc_figures.py `
  --output-dir ../agentos-20260811-reproduced/figures
```

图表生成脚本记录输入表和输出文件 SHA-256，并标注采用的 MathModel 模板方向：
`paired-raincloud`、`rf-tpe-surface` 和 `urban-park-cooling-combo`。
