# 同内核性能实验

## 实验问题

实验比较同一 AgentOS 内核中的两条等价实现路径：`compat` 使用传统文件扫描和用户态协调，`native` 使用 Agent metadata 查询和内核协作接口。两条路径处理相同语料并产生相同 outcome hash。这里测量的是完成同一恢复任务的实际成本，不把 AgentOS 与原始 uCore 的差异混入路径对照。

## 运行协议

一组正式结果由 schema 6 的 `agentos-contest-showcase` 记录给出，默认执行 8 次串行 QEMU 启动：

1. 固定内核提交、Guest 程序、语料规模和输入字节；每轮仅按预注册的样本编号和 AB/BA 顺序重新构建工作负载镜像。
2. 同一时刻只运行一个 QEMU，不并发采样。
3. 奇数次按 Compat→Native 执行，偶数次按 Native→Compat 执行，两种顺序各 4 次。
4. 每条 lane 都从静稳栅栏开始，并在清理和后台提交收敛后结束。
5. 每次启动校验两条 lane 的 outcome hash，并跨 8 次启动校验结果一致。

首条 lane 和次条 lane 的分组用于观察顺序效应。它们不能被直接解释为单一的冷缓存与热缓存因果效应，因为顺序还可能影响 buffer cache、metadata 状态和设备队列。

实验同时记录两个时间范围：

| 范围 | 起止点 | 单位 |
| --- | --- | --- |
| Core | 故障进入到结果完成持久化验证 | us |
| End-to-end | 静稳状态下开始构造语料，到清理完成并再次静稳 | us |

QEMU 总墙钟、构建耗时和宿主脚本耗时不代替 Guest 时间。

## 可信计数

延迟和机制数据均由 Guest 输出。对每条 lane，Guest 在区间前后读取 ABI v2 性能快照；报告中的机制值是 `after - before`，宿主程序只负责严格解析、校验单调性和聚合，不生成内核计数。

快照覆盖以下主要机制：

| 机制 | 原始计数 |
| --- | --- |
| 文件系统 | epoch commit、暂存 buffer、去重 stage、物理读写、持久化 flush、覆盖写跳过预读 |
| 目录扫描 | 目录块探测、检查目录项 |
| VirtIO | 通知、提交请求、读写批次、批量请求、间接写批次 |
| Metadata | 请求、合并请求、提交、dirty 与 durable 序列 |
| 内存与执行 | COW 共享页、复制页、fault promotion、exec cache 命中、未命中、共享页和淘汰 |
| 工作量 | 观察进程实际执行的系统调用、检查记录和读取字节 |

`before_tick` 和 `after_tick` 是 raw cycle 值，只用来证明快照先后顺序和区间非空。它们的单位写为 `raw_cycle_order_token`，不换算成微秒，也不参与延迟、吞吐或倍率计算。

`workload_syscalls` 来自当前观察进程的 syscall 计数，口径为 `observer_process`；其余内核机制计数的口径为 `global`。两种口径都只在静稳栅栏收敛后计算区间差分，报告和 Dashboard 必须逐字段展示口径，不能把观察进程计数写成全局计数。COW 与 exec cache 若未在 Compat、Native 两条 lane 中对称触发，只作为 workflow 活动量展示，不用于解释两条路径的性能差异。

## 统计与展示

每项对照指标保留 8 个 Guest 原始配对。Dashboard 和报告同时给出：

| 字段 | 含义 |
| --- | --- |
| Compat / Native 原始值 | 每次启动的前后快照差分或 Guest 延迟 |
| Compat p50、Native p50 | 两条路径的绝对量级 |
| Native - Compat | 配对差值的均值及 95% 置信区间 |
| Compat / Native | 配对比值的均值及 95% 置信区间 |
| Unit | `us`、`request`、`block`、`entry`、`%` 等真实单位 |
| Direction | 差值置信区间完全小于 0 时为 `lower`，完全大于 0 时为 `higher`，跨 0 时为 `uncertain` |

95% 置信区间使用同一次 Guest 启动内的 Compat / Native 配对样本计算。比值的分母为 0 时，该配对不进入比值统计；报告仍保留原始值和差值。

Dashboard 展示数值，不用“通过”“优秀”等状态卡替代测量结果。尚未执行正式 8 启动实验时，结果位置保持为空，不填示例数字，也不据此写性能结论。

## 数据产物

`host_tools/contest_demo.py` 校验 8 份 Guest 日志，生成：

```text
summary.json
dashboard-data.json
report.md
index.html
```

其中 `summary.json` 保留提交 ID、运行 ID、8 个原始样本、AB/BA 次数、静稳协议、原始机制差分和配对统计。`index.html` 直接消费同一份数据，不另设手工填写的数据源。

需要导出便于复核的统计表时，使用 schema 6 campaign 作为输入：

```powershell
python host_tools/same_kernel_performance.py `
  --input results/showcase/summary.json `
  --output-dir results/same-kernel
```

输出为 `same-kernel-metrics.json` 和 `same-kernel-metrics.csv`。两者按提交、语料规模、计时范围和实验协议分组，不跨协议合并样本。
