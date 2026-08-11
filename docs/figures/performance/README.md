# 性能图表

本目录从冻结实验 `one_shot_metrics/data/20260811/tables/` 生成论文图表。PNG 使用 300 dpi，PDF 与 SVG 为矢量输出。`figure_manifest.json` 记录输入表和输出文件的 SHA-256、样本口径与图中读数。

## 复现

在仓库根目录运行：

```powershell
python docs\figures\performance\make_doc_figures.py
```

脚本只读取 CSV，拒绝向冻结 campaign 写入输出，并在绘图前后复核全部输入哈希。依赖为 `matplotlib`、`numpy` 和 `pandas`。

## 图表与数据

| 文件 | 内容 | 真实源表 | 样本口径 |
|---|---|---|---|
| `01_paired_core_performance` | 遍历/索引逐启动哑铃、配对云雨与差值 ECDF | `contest_paired.csv` | 16 对 fresh boot |
| `02_catalog_speedup_landscape` | catalog size × hit count 加速比热力图与实测三维曲面 | `agenteval_pairs.csv` | 每格 15 对组内重复；每个 hit count 来自 1 次 fresh boot |
| `03_scan_state_groups` | 首次 scan、后续 scan、ready index 分组图 | `agenteval_samples.csv` | 360 个调用样本，按 query 次数归一化 |
| `04_task_latency_distributions` | batch、scalar V3、SQ-CQ 延迟云雨图与 ECDF | `task_sequences.csv` | 每条路径 4 次 fresh boot × 8 轮，共 32 个序列 |
| `05_eevdf_latency_fairness` | EEVDF wakeup latency ECDF 与 Jain fairness | `eevdf_wakeups.csv`、`eevdf_samples.csv` | 504 个精确 wakeup 探针；每个并发度 6 次 fresh boot |
| `06_normalized_io_heatmaps` | contest 与 task 的工作量归一化内核 I/O 热力图 | `contest_io_normalized.csv`、`task_perf_normalized.csv` | contest 每格 16 样本；task 每格 32 样本 |
| `07_core_end_to_end_scope` | core 与端到端计时边界的配对对照 | `contest_paired.csv` | 同一批 16 对 fresh boot |

## 模板参考

版式设计前已实际调用 Mathmodel Figure Templates renderer，分别渲染
`paired-raincloud`、`rf-tpe-surface` 和 `urban-park-cooling-combo`。最终图采用这些
模板的半云雨分布、热力图/三维联排和多指标组合布局；模板名称和产物摘要记录在
`figure_manifest.json`，数据全部取自上表列出的冻结 CSV。
