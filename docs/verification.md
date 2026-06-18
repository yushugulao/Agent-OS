# 验证与性能评估

本文档给出最终成品的评审可复现验证入口。逐项测试说明见 [testing-details.md](testing-details.md)，当前输出摘要保存在 [test-record.md](test-record.md)。

## 验证环境

| 项目 | 内容 |
| --- | --- |
| 分支 | `Wang` |
| 开发环境 | WSL2 Ubuntu 26.04 |
| 通用要求 | Linux、RISC-V GCC/binutils、QEMU riscv64、make、git |
| 构建命令 | `make fs.img`、`make kernel/kernel` |
| 运行命令 | `make qemu` |

## 最终验收命令

进入 xv6 shell 后依次运行最终验收程序：

```sh
agentfinal
agentbench
```

完整回归建议继续运行：

```sh
agentexec
agentcall
contexttest
agentstress
```

通过标准：

| 标准 | 期望 |
| --- | --- |
| 构建 | `make fs.img` 和 `make kernel/kernel` 成功 |
| 运行 | QEMU 正常启动并进入 xv6 shell |
| 稳定性 | 无 kernel panic |
| 功能 | `agentfinal: passed` |
| 性能 | `agentbench: passed`，输出吞吐对比表 |

## 测试覆盖表

| 测试程序 | 覆盖范围 | 关键通过输出 |
| --- | --- | --- |
| `agentfinal` | Agent 创建、4 页 Context、批量工具调用、短文本历史、直接读 Context 镜像、Context Snapshot、FIFO 淘汰、篡改边界 | `agentfinal: passed` |
| `agentbench` | scalar run、batch run、direct Context、context_query、context_snapshot 吞吐 | `agentbench: passed` |
| `agentexec` | shell 直跑 wrapper 和 Agent exec 成功路径 | `agentexec: wrapper status=0` |
| `agentcall` | legacy 工具调用、参数键/类型错误、长 payload、坏输出指针无副作用、lazy 输出缓冲、多 Agent mailbox、历史 wrap | `agentcall: strict validation passed` |
| `contexttest` | 手动 push/query/rollback/clear、短文本历史、rollback 不存在返回码和 128 条 FIFO | `contexttest: passed` |
| `agentstress` | exec 成功/失败、连续创建退出、sbrk 边界、普通进程隔离、父进程堆越过 Context 后拒绝创建 Agent | `agentstress: passed` |

性能成品主入口是 `agentfinal` 和 `agentbench`。

## 详细测试内容索引

| 测试程序 | 详细说明 |
| --- | --- |
| `agentfinal` | [testing-details.md](testing-details.md) 中的 Agent 创建、Context 映射、64 路批量工具调用、Context Snapshot、FIFO 淘汰和 direct/snapshot 一致性检查 |
| `agentbench` | [testing-details.md](testing-details.md) 中的 scalar run、batch run、direct context read、context_query、context_snapshot 和性能表字段解释 |

## 最新功能输出

```text
agentfinal: context size=16384 capacity=128
agentfinal: batch first_seq=1 last_seq=64
agentfinal: short_text_history=1 payload=final result=final
agentfinal: snapshot count=64 latest=64
agentfinal: direct_dirty_before_snapshot=1
agentfinal: tamper_protected=1
agentfinal: fifo oldest=65 latest=192 dropped=64
agentfinal: direct_context_match=1
agentfinal: passed
```

## 最新性能数据

```text
agentbench: case ops ticks ops_per_tick speedup_x100
agentbench: scalar_run ops=65536 ticks=16 ops_per_tick=4096 speedup_x100=100
agentbench: batch_run ops=65536 ticks=2 ops_per_tick=32768 speedup_x100=800
agentbench: direct_context ops=1000000 ticks=0 ops_per_tick=1000000 speedup_x100=24414
agentbench: context_query ops=2048 ticks=0 ops_per_tick=2048 speedup_x100=50
agentbench: context_snapshot ops=262144 ticks=3 ops_per_tick=87381 speedup_x100=4266
agentbench: latest_sequence=131072 dropped=130944 capacity=128
agentbench: passed
```

说明：

- 上述性能数字是一次样例输出，xv6 tick 粒度较粗，复跑时具体 tick 和 speedup 会波动。
- `batch_run` 与 `scalar_run` 执行同样数量的 echo 工具操作，前者将 64 个 op 合并为一次 syscall。
- `direct_context` 的 tick 为 0，表示本轮测试中 1000000 次直接读低于一个 xv6 tick；`speedup_x100` 使用 1 tick 作为保守下界计算。
- `context_snapshot` 一次返回最多 128 条可见记录，按返回记录数计算吞吐。

## 覆盖到的赛题验收项

| 赛题验收项 | 证据 |
| --- | --- |
| Agent 进程能成功创建，PCB 扩展字段正确初始化 | `agentfinal` |
| Agent Context 区正确分配，Agent 可直接读写 | `agentfinal` 直接读取 header/latest/record |
| 用户态篡改 Context 镜像不影响内核权威历史 | `agentfinal: direct_dirty_before_snapshot=1` 说明 direct read 可见脏镜像，`agentfinal: tamper_protected=1` 说明 snapshot 刷新后恢复权威历史 |
| 父进程堆越过 Agent Context 起点时拒绝创建 Agent | `agentstress: parent_over_context_rejected=1` |
| 普通进程和 Agent 进程共存 | `agent_create` 由普通父进程创建 Agent 子进程 |
| 用户态 Agent 能调用至少 3 个内核工具 | `agentfinal` 批量调用 echo、pid_info、ctx_stat、read_context |
| 请求和响应为结构化格式 | `agent_op`、`agent_result` |
| legacy 参数键名和类型错误返回明确状态 | `agentcall` 输出 `bad_payload_key`、`bad_payload_type`、`unexpected_param` |
| 坏输出指针失败不产生工具副作用 | `agentcall` 输出 `agent bad_output: no_side_effect`，receiver 仍读到 `hello-agent` |
| 合法 lazy sbrk 输出页可作为 Agent 输出缓冲 | `agentcall: agent lazy_output: legacy=1 batch=1` |
| 5 轮以上连续工具调用并维护路径 | `agentfinal` 连续 192 次 op |
| Context Path 保存 128 条短文本摘要路径 | `agentfinal: short_text_history=1`、`contexttest: short_text_history=1` |
| Agent 直接从 Context 高速读取路径数据 | `agentfinal` 和 `agentbench` |
| 路径超长自动淘汰且不 OOM | `agentfinal` 验证 `oldest=65 latest=192 dropped=64` |
| 历史节点不存在返回可区分状态码 | `contexttest: rollback_not_found=-5` |
| Agent exec 失败后 Context 指针仍有效 | `agentstress: exec_failure_preserved=1` |

## 仍需补充的验证

| 方向 | 当前缺口 |
| --- | --- |
| 任务四 | 缺文件索引、属性过滤、内容查询或语义查询测试 |
| 任务五 | 缺心跳、等待、唤醒和内核调度循环测试 |
| 任务六 | 缺综合场景测试、演示视频和现场脚本输出记录 |
| 性能可信度 | xv6 tick 粒度粗，后续可补更细粒度计数 |
