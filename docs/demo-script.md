<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 演示脚本

本文用于现场评审或录制演示视频。当前主线是“夜间实验批量复测故障诊断与受控恢复”，覆盖任务一至五，并为最终 LLM Gateway 和可视化大屏保留 `agentos:event` 输出。

## 1. 开场说明

说明项目定位：

- 基于 xv6-riscv 的 Agent-OS 内核扩展。
- Agent 是内核可识别的进程类型，有 metadata、Context 区、Context Path、结构化工具调用和事件等待能力。
- 当前综合场景模拟科研实验 `lab-gene-x` 的 `RUN-042` 夜间批量复测失败：`align` 阶段内存不足，系统唤醒 Agent，定位失败，执行受控恢复并更新报告工件元数据。

建议强调：

- 这不是聊天程序，而是操作系统为 Agent 工作流提供身份、工具、上下文、文件查询、事件和权限控制。
- LLM 和大屏是最终成品增强层，当前阶段已输出稳定 `agentos:event` 便于后续接入。

## 2. 构建和启动

在 Linux/WSL2 Ubuntu 中执行：

```bash
cd project61-agentOS-happylegend
make fs.img
make kernel/kernel
make qemu
```

进入 xv6 shell 后看到 `$` 提示符。

## 3. 综合场景演示

执行：

```sh
labdemo
```

讲解顺序：

1. 普通父进程只引导 Orchestrator Agent。
2. Orchestrator Agent 初始化 `lab-gene-x / nightly-regression / RUN-042` 文件元数据，并创建 Sentinel、Investigator、Recovery 三个工作 Agent。
3. Sentinel 注册 `status=failed` watch，并进入 waiting 状态。
4. Orchestrator 注入 `align` 阶段失败。
5. 内核投递 `AGENT_EVENT_FILE_STATUS`，Sentinel 被唤醒。
6. Sentinel 通过 `query_file` 按 `project/run_id/status` 查询失败工件。
7. Sentinel 尝试恢复动作被拒绝，展示权限限制。
8. Sentinel 发送消息给 Investigator。
9. Investigator 查询摘要和依赖，得到 `align+analyze+report+archive` 影响范围，同时确认 `prepare` 不受影响。
10. Investigator 发送恢复建议给 Recovery。
11. Recovery 通过 capability check 后重跑 `align` 和 `report`。
12. 重复 `corr_id` 被幂等表拒绝。
13. Recovery 写恢复报告并输出最终 `RECOVERED`。

关键输出：

```text
labdemo: sentinel state=WAITING
labdemo: inject failure stage=align reason=OOM
labdemo: sentinel event type=FILE_STATUS payload=fid=4;status=failed;stage=align;run_id=RUN-042;truncated=0
labdemo: query_file project=lab-gene-x run=RUN-042 status=failed hits=1 scanned=1 used_index=1 first=lab_RUN042_align_err
labdemo: unauthorized rerun by sentinel status=DENIED
labdemo: affected stages=align+analyze+report+archive
labdemo: skip stage=prepare reason=unaffected
labdemo: duplicate corr_id=RUN-042-align-rerun-1 status=DUPLICATE
labdemo: final status=RECOVERED
agentos:event type=FINAL status=RECOVERED
labdemo: passed
```

讲解点：

- `used_index=1 scanned=1` 说明失败工件不是靠遍历全部文件定位，而是走属性索引。
- `fid=4` 说明事件只携带短摘要时，Agent 仍可通过 `query_file(fid=...)` 回查完整元数据。
- `sentinel state=WAITING` 和 `FILE_STATUS` 事件说明 Agent Loop 已经进入等待/唤醒路径。
- `DENIED` 和 `DUPLICATE` 说明恢复动作有权限限制和幂等要求。
- `agentos:event` 是后续可视化大屏和 LLM Gateway 的稳定输入。

## 4. 性能演示

执行：

```sh
labbench
```

讲解点：

- `file_scan_query` vs `file_index_query`：任务四属性索引降低候选扫描量。
- `busy_poll_query` 与 `event_wait_wake`：前者是轮询查询基线，后者验证任务五内核事件等待路径。
- `scalar_tool_call` vs `batch_agent_run`：任务一至三底座保留批量工具调用优势。
- `context_query` vs `context_snapshot`：Context Path 可批量导出，适合报告和可视化。
- `capability_check` 和 `duplicate_reject`：恢复动作安全限制可测。
- `heartbeat_stop` 和 `unwatch`：Agent Loop 支持停止心跳和删除关注条件。
- `fid` 查询和元数据删除：任务四支持从短事件回查完整记录，也支持删除 Agent 内核元数据表记录。

样例输出：

```text
labbench: file_semantics scan_hits=2 index_hits=2 scan_scanned=112 index_scanned=2 report_scanned=9 empty=0 truncated=1 fid=1
labbench: metadata_dependency=1 scoped_rerun=1 scoped_report=1 history_preserved=1 single_report=1 mask_text=1 dep_clear=1 insert=1 delete=1
labbench: loop_timeout=1 heartbeat_timer=1 heartbeat_stop=1 unwatch=1 heartbeat_interval=0 last_heartbeat=42
labbench: permission_denied self_escalation=1 wake=1 meta=1 rerun=1 report=1
labbench: file_status_partial_payload fid=4 stage=align run_id=RUN-042 full_lookup=1
labbench: file_scan_query ops=32768 ticks=36 ops_per_tick=910 speedup_x100=100
labbench: file_index_query ops=32768 ticks=22 ops_per_tick=1489 speedup_x100=163
labbench: event_wait_wake ops=512 ticks=2 ops_per_tick=256 speedup_x100=100
labbench: event_fifo queued=8 dropped=1 ordered=1
labbench: send_message_overflow queued=8 dropped=1 rollback=1
labbench: file_status_overflow queued=8 dropped=1 no_space=1
labbench: batch_agent_run ops=8192 ticks=0 ops_per_tick=8192 speedup_x100=300
labbench: context_snapshot ops=65536 ticks=1 ops_per_tick=65536 speedup_x100=12800
labbench: passed
```

说明：xv6 tick 粒度较粗，具体数字会波动；演示重点是每个对比项都有可复现输出，且程序稳定通过。

## 5. 底座复测

如需展示任务一至三高性能底座，继续运行：

```sh
agentfinal
agentbench
```

如需展示错误路径和压力测试，继续运行：

```sh
agentcall
contexttest
agentstress
agentexec
```

`agentcall` 中普通进程写 Agent Context 的负向测试会触发一次用户态 page fault 输出，这是预期行为；最终仍以 `agentcall: strict validation passed` 为通过标志。

## 6. 当前实现限制

- 当前文件查询使用 Agent 子系统内核元数据表，不直接修改 xv6 inode 主结构。
- 当前事件队列是每 Agent 8 槽 FIFO，满队列返回 `NO_SPACE` 并记录 dropped；最终可扩展优先级和更大容量。
- 当前 LLM 为模板预留字段，未接云端 API。
- 当前可视化大屏未实现，但 `agentos:event` 已作为后续解析契约。

## 7. 退出 QEMU

按：

```text
Ctrl-a x
```
