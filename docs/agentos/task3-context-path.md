# 任务三：Context Path

## 场景与约束

多轮 Agent 调用需要保留“谁在什么原因下调用了什么、得到了什么结果”。用户态又必须能够快速读取当前上下文。若把身份、因果字段和历史全部交给用户维护，进程可以伪造来源；若发布顺序不完整，并发 reader 会看到 torn record。

## 方案

Context 将可信历史、直接读视图和 Guest cache 分开：

| 部分 | 内容 | 信任级别 |
| --- | --- | --- |
| 6 页内核区 | header、latest、record ring、active path | 内核发布，用户只读 |
| 第 7 页 | Guest 自管 cache | 用户读写，不进入授权或 evidence |
| 内核 sidecar | 完整 request/response、source identity 和 provenance | 内核私有 |

每条 record 保存单调 sequence、request、tool/status、tick、cause/span、branch、parent、短 payload/result 和 hash 链。内核从当前调用、事件或 IPC 路由归因 cause 与 source，用户不能通过填写 PID 或 sequence 建立跨 workflow 因果边。

### 发布与读取

同一 Agent 的工具结果、手工记录、事件消费和 rollback 共用 FIFO commit lane。writer 先完成范围预检，再写 record、detail 和 latest，最后发布 header。奇偶 publication sequence 包围整个过程；直接读 helper 只接受前后相同的偶数 sequence，竞争过强时退回 syscall。

公开读取路径包括：

- `context_direct_header_snapshot()` 和 `context_direct_active_query()`：固定映射直接读；
- `context_query()`：返回有界 active path；
- `context_snapshot()`：同时返回 header 与当前路径；
- `context_detail()`：读取窗口内保留的完整详情；
- timeline wait/read：在一个 syscall 内等待并读取，消除 wait/query 间隙。

### 回滚与淘汰

rollback 验证目标仍在可信窗口，然后建立新的 branch generation，并把目标设为 active anchor。旧记录保持原 sequence 和 hash，直到 FIFO 容量淘汰。clear 和失败回滚同样先预检；失败不会移动 active head 或改变链尾。

## 关键实现

| 职责 | 源码 |
| --- | --- |
| Context 存储、提交和查询 | [os/agent_context.c](../../os/agent_context.c)、[os/agent_context.h](../../os/agent_context.h) |
| active path 与直接读 | [os/agent_context_path.c](../../os/agent_context_path.c) |
| 因果与来源标签 | [os/agent_provenance.c](../../os/agent_provenance.c) |
| Evidence canonical event | [os/agent_evidence_ring.c](../../os/agent_evidence_ring.c) |
| timeline 投影 | [os/agent_observe_timeline.c](../../os/agent_observe_timeline.c) |
| 用户 ABI | [user/include/agent.h](../../user/include/agent.h)、[agent_provenance_abi.h](../../agent_provenance_abi.h) |

## 验证

`agentfinal_ucore` 在一次 batch 中提交 64 个顺序调用，然后检查 latest、snapshot、active path、rollback、分支、hash 链和 FIFO 淘汰。同步故障 profile 还验证失败发布保留旧记录，后续提交可以继续。`agentscope_ucore` 覆盖跨 workflow 查询裁剪，`agentbench_ucore` 记录 mirror 与 syscall 查询的样本、tick 和工作量。

```bash
python -B scripts/test-context-active-path-wiring.py
python -B scripts/test-context-evidence-atomicity.py
python -B scripts/test-context-snapshot-reader-atomicity.py
AGENT_TEST_CASE=agentfinal_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

## 当前边界

- Context 和 detail 窗口容量固定，旧记录按 FIFO 淘汰。
- rollback 只改变 active path；已经发生的文件、IPC 和外部工具效果保持不变。
- 第 7 页 cache 可直接读写，其内容不能扩大 capability、scope 或可信 provenance。
- 直接读 helper 可能因持续写入返回重试，syscall 查询提供一致性回退路径。
- Context、timeline 和 evidence 只覆盖当前启动周期内仍保留的窗口。
