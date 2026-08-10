# 实测性能结果

本页记录 2026-08-10 在当前源码上直接运行 QEMU 得到的结果。它不是发布证据包，也不使用源码哈希或材料签名；数值只用于回答赛题要求的实际性能对比，并可用仓库命令重新测量。

## 环境与命令

- Host：Windows NT 10.0.26200.0，Intel64 Family 6 Model 198，单实例串行运行。
- Guest：RISC-V64 `virt`，1 Hart。
- 编译器：xPack GNU RISC-V Embedded GCC 15.2.0。
- QEMU：11.0.0 (`v11.0.0-12122-ga4bb4b10c9`)。
- 主演示：`make contest-demo`，4 个独立 QEMU boot，顺序为 AB/BA/AB/BA。
- 专项查询：默认 Guest 回归中的 `agentbench_ucore`。

## 综合场景

`labdemo_ucore` 在同一内核、同一 Guest 和同一 96 条记录语料上执行目录遍历路径与索引控制路径。两条路径都完成同一个 `recovered` 结果，结果哈希均为 `1457873431608088591`。

| Boot | 顺序 | 遍历 core (us) | 索引 core (us) | 遍历记录数 | 索引记录数 |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | traversal -> indexed | 56,443 | 12,434 | 97 | 2 |
| 2 | indexed -> traversal | 42,644 | 15,279 | 97 | 2 |
| 3 | traversal -> indexed | 62,748 | 13,120 | 97 | 2 |
| 4 | indexed -> traversal | 66,492 | 14,613 | 97 | 2 |

中位数为遍历 `59,595.5 us`、索引 `13,866.5 us`，遍历/索引比为 `4.298x`；4/4 个配对 boot 中索引路径更快。索引路径检查 2 条记录，遍历路径检查 97 条，工作量比为 `48.5x`。两条路径的端到端中位数分别为 `747,576.5 us` 和 `756,043.5 us`，说明文件创建、登记、清理和综合工作流开销会掩盖查询阶段收益，因此这里只把 core 区间用于查询路径对比，不把端到端差异归因给索引。

## 专项查询

最近一次完整 Guest 回归中的 `agentbench_ucore` 使用 104 条显式 metadata，执行 64 次目录遍历和 64 次 warm indexed query：

| 指标 | 目录遍历 | Warm index |
| --- | ---: | ---: |
| 操作数 | 64 | 64 |
| 检查记录数 | 104 | 6 |
| 总耗时 | 25,412 us | 8,746 us |

该专项负载中遍历/索引耗时比为 `2.906x`。冷索引单次查询为 `281 us`；本次 catalog 已就绪，没有把重建成本混入 warm-index 结果。

## 复测

```bash
make contest-demo
AGENT_TEST_CASE=agentbench_ucore make agentos-test
```

`contest-demo` 会把本机原始串口日志和表格写入忽略的 `results/contest-demo/`，便于现场检查而不把运行产物变成仓库负担。不同主机和 QEMU 版本的绝对时间会变化；验收重点是相同结果、平衡顺序、逐 boot 配对，以及记录数和耗时都显示索引路径的实际收益。
