# 传统接口性能矩阵

## 目标

这组实验回答一个独立于 Agent workflow 的问题：在保留身份、资源、持久化和 I/O 治理后，普通程序使用传统 uCore 接口时还要承担多少额外成本。AgentOS 与 `baseline_ucore` 编译同一份 Guest workload，使用同一挑战值、操作次数和结果判定，只改变内核目标。

实验不把两侧的持久化实现说成同一种机制。AgentOS 的 tiny-write workload 在每批写入后调用 `fsync`；baseline 的原始文件写路径在 `write` 返回前同步完成块写，因此其边界记为 `sync_write_completion`。两者只在“边界返回后数据可从文件重新读得”的最终语义上对齐，机制计数和耗时分别展示。

## 工作负载

| 项目 | 计时区间 | 操作量 | 结果校验 |
| --- | --- | ---: | --- |
| `cache_read_4k` | 已预热并预打开的描述符执行 4 KiB 读取 | 256 read，1 MiB | 对实际读回字节计算 challenge-bound hash |
| `open_close` | 打开同一热文件并立即关闭 | 256 对 | 每次成功完成后混入迭代序号 |
| `tiny_write_fsync` | 16 B 小写入与每批持久化边界 | 128 write，8 个逻辑批次 | 边界后重新打开并校验 2 KiB 内容 |
| `fork_wait` | 创建子进程、退出并回收 | 32 对 | 混入实际 `waitpid` 状态 |
| `warm_exec` | 预热后 fork、exec、wait | 16 次 exec | 混入被执行程序的实际退出状态 |

Guest 同时输出 `duration_us` 和 `duration_ticks`。微秒来自 RISC-V time counter 的 `gettimeofday` 换算；tick 是同一实测区间规范化后的毫秒投影，用于严格检查日志一致性。每项还输出 open、close、read、write、fork、exec、wait、持久化边界、字节数等实际操作计数。Host 不补写或推导 Guest 延迟。

## 采样顺序

正式实验默认包含 8 个 pair，共 16 次独立启动：

1. 奇数 pair 按 AgentOS、baseline 顺序启动。
2. 偶数 pair 按 baseline、AgentOS 顺序启动。
3. 构建使用有界并行 worker；正式 QEMU 测量只占用一个槽，前一次退出后才开始下一次。
4. 每个 pair 的两侧镜像嵌入相同 `run nonce` 和 sample id，order slot 相反。
5. 两侧五项 workload 的操作计数、结果 hash 和 aggregate hash 必须一致，否则该 campaign 不生成报告。

AB/BA 只能抵消一部分宿主顺序偏差。Dashboard 中的 p95 标为 8 对样本的经验分位数，不给出显著性或硬件外推结论。

## 证据链

`scripts/run-traditional-performance.sh` 在启动 QEMU 前完成两侧构建，并生成只读输入清单。报告器复用现有 contest demo 的 Git 身份、Guest 故障分类、文件哈希和原子发布逻辑，逐项核验：

- HEAD commit、tree 和实验相关 Git blob；
- make、编译器、QEMU、Python 与 runner 的路径、版本和 SHA-256；
- 两侧 kernel、每个 sample 的输入 `fs.img`、Guest bin/ELF 和 exec probe bin/ELF；
- 从 `fs.img` 反解出的 `tradperf`、`tradexec` 与归档 bin 是否逐字节相同；
- 16 份原始 Guest 日志及 `agent_test_runner.py` v2 execution attestation；
- attestation 的单调时钟区间是否符合声明的 AB/BA 顺序；
- 文件在读取、重放和发布前后是否保持同一身份。

Guest 的 outcome hash 由随机 run nonce 和实际结果共同生成。Host 按协议独立重算，不接受固定 hash、缺失 workload、重复行、另一轮日志或重写后的统计汇总。

## 运行与输出

```bash
make -j8 traditional-performance
```

可通过 `TRADPERF_BUILD_JOBS` 调整每侧构建并行度，通过 `TRADPERF_SAMPLES` 选择 8 至 32 的偶数 pair。正式结果位于 `results/traditional-performance/report/`：

| 文件 | 内容 |
| --- | --- |
| `traditional-performance.json` | 原始 pair、证据摘要与聚合值 |
| `traditional-performance.csv` | 每项每 pair 的两侧值、差值、比率和 I/O 计数 |
| `index.html` | 可离线打开的竞赛展示页 |

Dashboard 展示两侧 p50、经验 p95、AgentOS - baseline 配对差值、AgentOS / baseline 比率，以及可以直接取得的逻辑 I/O 绝对计数。页面不使用“测试通过”作为性能结论。
