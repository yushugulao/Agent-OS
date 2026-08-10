# AgentOS 用户接口与 ABI

本文描述当前生产构建的公开接口。精确布局以 `user/include/agent.h`、`agent_*_abi.h`、`ci/agent-uapi-layout.json` 和 `scripts/check-agent-uapi-layout.py` 为准。

## 1. 基本约定

- 所有跨 workflow 身份使用 `id + generation`，不能只保存 slot/id。
- 用户指针错误通常返回 `-1`；Agent 协议错误使用 `AGENT_STATUS_*`。
- 字符串字段必须在固定数组内以 NUL 终止；结构体版本和大小不匹配分别返回 `BAD_VERSION`、`BAD_SIZE`。
- 普通进程不能通过构造 Agent 结构体获得 capability、scope 或 controller 身份。
- fence、live watch 和 metadata 都只接受当前 lifecycle；PID、slot 或 inode number 单独出现不构成可信身份。

常用状态码：

| 状态 | 值 | 含义 |
| --- | ---: | --- |
| `AGENT_STATUS_OK` | 0 | 成功 |
| `BAD_REQUEST` | -1 | 请求形状错误 |
| `BAD_PARAM` | -4 | 参数、标志或当前接口不支持 |
| `NOT_FOUND` | -5 | 对象/selector 不存在 |
| `NO_SPACE` | -6 | 有界容量不足 |
| `DENIED` | -8 | capability、scope 或路由拒绝 |
| `CONFLICT` | -11 | 同一身份的内容冲突 |
| `STALE` | -12 | generation/request id 已过期 |
| `RETRY` | -17 | 当前 cut 未达到 quiescence，可重试 |
| `IO_ERROR` | -18 | 文件系统 cut 失败 |
| `INDETERMINATE` | -20 | 无法肯定证明成功或失败 |

历史状态名 `DURABILITY` 仍在枚举中，不代表当前 Evidence Ring 提供磁盘持久性。

## 2. Agent 与 lifecycle

### 2.1 创建和关闭

```c
int agent_create(void);
int agent_create_role(int role);
int agent_workflow_create(int role);
int agent_workflow_close(uint64 scope_id);
```

`agent_workflow_create()` 建立动态 VFS scope、exec/storage 资源账户和 lifecycle generation，并创建首个 member/controller。后续受信 Agent/worker 通过内核受控路径 join。close 只把同一 generation 置为 closing 并阻止新 join/operation；实际资源回收等待 member 与 gate 清空。

### 2.2 self-only lifecycle 查询

```c
int agent_workflow_lifecycle_info(
    struct agent_workflow_lifecycle_info *info,
    const struct agent_workflow_lifecycle_key *expected);
```

当前 ABI 版本为 3，结构体 216 字节。前 64 字节与 V2 完全一致，返回当前进程是否 charged、`id + generation`、Context lane/metadata transaction 状态以及 exec resource account handle；旧调用者仍可提交 `version=2, struct_size=64`。V3 追加 workflow EEVDF 的 mode/flags、latency class、weight、runnable、request/remaining、lag、vruntime/virtual deadline、dispatch/service、sleep decay、eligibility miss、fallback、deadline miss、最大唤醒延迟和四档唤醒直方图。字段描述当前被查询 workflow；评价中的直方图和 p50/p99 汇总只纳入 fresh-agent 样本，不把复用的 bootstrap participant 混入。它只是观测数据，不是可转让凭据。传入 `expected` 时执行 generation-safe 比较。

### 2.3 全局资源快照

```c
int agent_resource_snapshot(struct agent_resource_snapshot *snapshot);
```

版本 1，结构体 488 字节，覆盖 8 类全局 policy 的 capacity、used、pending 和 ordinary/reserved 分类。它是管理/验证视图，不直接导出某个 workflow 的 F credit；workflow cut 的精确 U 由 fence receipt 提供。

### 2.4 Execution Contract

```c
int agent_execution_contract(
    const struct agent_execution_contract_control *control,
    struct agent_execution_contract_result *result);
```

版本 1 支持 `CREATE/QUERY/RETIRE`。每个 lifecycle generation 最多冻结一份合同，最多 24 个 `struct agent_execution_contract_node`。创建时：

- `node_id` 必须等于数组下标，predecessor bit 只能引用更小下标；
- tool id、32 字节 schema digest、capability、accepted/output provenance 和 side-effect mask 必须与内核 manifest 相容；
- exec/storage envelope 必须非零且 charge class 必须与进程资源 class 一致；
- deadline、input/output artifact、max attempts、retry/cancel policy 必须在固定枚举内；
- `ENFORCE` 合同冻结后，合同外工具和受保护 direct syscall 在副作用前拒绝。

result 返回 contract key/fingerprint、状态、node masks、denial/replay 计数。合同 generation、lifecycle generation 或 request id 过期返回 `STALE/CONFLICT`，不会自动替换现有合同。

### 2.5 Workflow EEVDF 边界

EEVDF 没有向用户暴露“直接选择下一个 workflow”的控制 syscall。event、heartbeat/wait deadline 和进程状态由内核归并为 latency/deadline 输入；公平份额按 workflow resource domain 而不是线程记账。当前同时跟踪的总上限为 4，固定包含 1 个 `BOOT_SEALED` bootstrap participant，因此最多还能容纳 3 个 fresh workflow；异常回退旧调度器。4-way 测量是 bootstrap+3 fresh，线程放大测量是 1 个 fresh 4-thread workflow 对 2 个 fresh single-thread workflow 加 bootstrap peer。所谓 16-workflow 场景实际是 16 个逻辑样本：四波复用同一 bootstrap，并创建合计 12 个 fresh lifecycle；它不是 16 个实体并发、每波 4 个 fresh，也不代表 16 个彼此独立的 lifecycle。

## 3. Workflow fence

### 3.1 调用

```c
int agent_workflow_fence(
    const struct agent_workflow_fence_request *request,
    struct agent_workflow_fence_receipt *receipt);
```

wrapper 复用 `SYS_agent_run`：`count == 0` 且 flags 只有 `AGENT_RUN_F_FENCE`。普通 `agent_run()` 的批处理语义不变；零 count 但没有 fence flag 不是 fence。

只有当前 workflow 的 orchestrator/controller 可调用。request 可为空；非空 request 必须满足：

- `version == AGENT_WORKFLOW_FENCE_VERSION`；
- `struct_size == 56`；
- `flags == 0`、`reserved == 0`；
- `request_id != 0`；
- challenge 固定 32 字节。

### 3.2 receipt

`struct agent_workflow_fence_receipt` 固定 320 字节，重要字段如下：

| 字段 | 合同 |
| --- | --- |
| `key` | 被切割的 lifecycle id/generation |
| `request_id` | 非空请求的幂等键；空 request 为 0 |
| `fence_sequence` | lifecycle 内成功 fence 的单调序列 |
| `metadata_generation` | 本启动周期内存 catalog 的 quiescent generation |
| `credit_epoch` | exec/storage trim 后资源快照 epoch |
| `evidence_first/last_sequence` | 本 fence 覆盖的 ticket 范围，空段可以为 0 |
| `evidence_event_count` | 范围内的 canonical event/gap 计数 |
| `evidence_dropped_success` | ordinary discard/不可保留事件的 gap 数 |
| `resource_used[8]` | fence cut 的精确合并 U，不含已 trim 的 F，且 P 为 0 |
| exec/storage account key | 参与 digest 的 account slot/generation |
| `credit_digest` | lifecycle、epoch、account keys 与 U 向量的 SHA-256 |
| `challenge` | 原样绑定请求 challenge |
| `previous_root` | 上一个公开 workflow fence 根 |
| `evidence_root` | 本次 challenge-bound SHA-256 根 |

成功 receipt 设置四个标志：

| 标志 | 精确含义 |
| --- | --- |
| `PARTIAL_COVERAGE` | 根只覆盖当前 Evidence Ring 合同，不覆盖所有内核/Host 事实 |
| `CREDIT_EXACT` | exec/storage 已 trim，所有 P 为 0，导出的 U 对该 cut 精确 |
| `EVIDENCE_SEALED` | 事件段、gap、challenge、metadata/credit 信息已进入根 |
| `METADATA_VOLATILE` | metadata generation 只在当前启动周期有效 |

`EVIDENCE_SEALED` 不是 disk durable。重启后没有可恢复的 ring/catalog。

### 3.3 重试规则

每个 lifecycle slot 保留一条 retry-stable receipt：

- 同 request id、同 challenge：返回完全相同的 receipt；
- 同 request id、不同 challenge：`CONFLICT`；
- 小于已缓存 id：`STALE`；
- 前一 receipt 已提交但尚未成功 copyout：新 id 返回 `RETRY`；
- metadata/live-query/credit 未达到 cut：`RETRY`，不推进 fence root；
- 文件系统 epoch cut 失败：`IO_ERROR`。

## 4. Context 与兼容观测视图

主要接口：

```c
int context_push(struct agent_context_record *record);
int context_query(uint64 start_sequence,
                  struct agent_context_record *out, int max);
int context_snapshot(struct agent_context_header *header,
                     struct agent_context_record *records, int max);
int context_detail(uint64 sequence, struct agent_context_detail *detail);
int context_rollback(uint64 sequence);
int context_clear(void);

int agent_audit_snapshot(struct agent_audit_record *records, int max);
int agent_audit_query(struct agent_audit_filter *filter,
                      struct agent_audit_record *records, int max);
int agent_timeline_query(struct agent_timeline_filter *filter,
                         struct agent_timeline_record *records, int max);
int agent_timeline_wait(struct agent_timeline_filter *filter,
                        int timeout_ticks);
int agent_timeline_read(struct agent_timeline_filter *filter,
                        struct agent_timeline_record *records, int max,
                        int timeout_ticks);
int agent_provenance_snapshot(struct agent_provenance_edge *edges, int max);
int agent_ledger_snapshot(struct agent_ledger_summary *summary);
```

Context Path 仍是进程级可信历史。普通成功 Context 的 workflow 级 canonical evidence 只写 Evidence Ring 一次。audit/timeline/provenance/ledger API 仍存在，并合并 Context、ring、critical/fallback legacy ledger 及有界 sched/event 记录，避免同一 ring Context 被重复计数。

### 4.1 audit receipt 兼容接口

```c
int agent_audit_receipt(struct agent_audit_receipt_request *request);
```

只有与 legacy 关键投影关联且 ticket 已被显式 workflow fence 覆盖时，肯定状态为 `(OK, FENCE_SEALED, receipt)`。宏 `AGENT_AUDIT_DURABILITY_DURABLE` 仅为源码兼容别名，值等同 `FENCE_SEALED`，绝不表示落盘。普通成功 event 没有必要产生一条 legacy receipt。

### 4.2 已停产 observe recovery

```c
int agent_observe_recovery(
    struct agent_observe_recovery_request *request, void *records);
```

请求结构和 syscall 编号只为 ABI tombstone 保留。当前内核不调用历史 recovery 实现，固定返回 `AGENT_STATUS_BAD_PARAM`。不得用该接口宣称 observation crash recovery、bank enumeration 或 reap。

## 5. 显式文件 metadata

### 5.1 设置与删除

```c
int agent_file_meta_init(void);
int agent_file_meta_set(struct agent_file_meta *meta);
int agent_file_query(struct agent_file_query *query,
                     struct agent_file_query_result *result);
```

当前 catalog 是内存状态。`agent_file_meta_init()` 初始化/确认当前运行时视图，不从磁盘加载 catalog。

`agent_file_meta_set()` 要求 `META_WRITE` capability 和当前 workflow scope：

- 普通 set 使用 `flags == 0`；
- 删除使用 `AGENT_FILE_META_F_DELETE`；
- `AGENT_FILE_META_F_PERSIST` 和 `AGENT_FILE_META_F_AUTOSCAN` 为 legacy 常量，任何显式 set 携带它们都返回 `BAD_PARAM`；
- set 把 metadata 绑定到真实 `dev + inum + incarnation`，更新字段由 `update_mask` 限制；
- catalog/索引/generation 重启后不恢复，用户态需重新登记。

普通文件不会因 create/rename 自动加入 catalog。只有已经显式绑定的 metadata 会接收 VFS 内容大小、unlink tombstone 和 incarnation 变化投影。

### 5.2 查询

`struct agent_file_query` 可按 physical/logical path、project/workflow/run、stage/kind/status 和 summary substring 过滤，`max_hits` 范围为 0 到 8。

| flags | 行为 |
| --- | --- |
| `AGENT_FILE_QUERY_SCAN` | 强制遍历当前可见 catalog |
| `AGENT_FILE_QUERY_USE_INDEX` | 优先选择 status/stage/kind 索引；没有合适 key 时退化为 scan |

结果报告 total/returned/scanned/candidates、plan、reason、是否截断、query ticks 和 `fs_generation`。索引路径仍遍历候选链；不存在跨请求结果 cache。`CACHE_HIT` 等 legacy plan reason 名称不应解释为当前有结果缓存。

## 6. Typed live query

### 6.1 安装

```c
int agent_live_watch(struct agent_file_live_watch *watch);
int agent_live_unwatch(struct agent_file_live_watch *watch);
```

wrapper 复用 `agent_watch/unwatch` 的 syscall，但事件类型固定为 `AGENT_EVENT_FILE_QUERY`，参数是 typed 结构而不是字符串。

新安装要求：

- `version == AGENT_FILE_LIVE_WATCH_VERSION`；
- `watch_id == initial_generation == catalog_generation == 0`；
- flags 只能包含可选 `ACK_RESYNC`；
- query flags 和 `max_hits` 合法；
- 若 ACK，则 `resync_generation != 0`。

成功后内核回填 `watch_id`、安装时 `initial_generation`、`catalog_generation` 和仍待确认的 `resync_generation`。若存在缺口，返回 flags 包含 `RESYNC_REQUIRED`。

### 6.2 通知语义

匹配集合发生变化时，事件类型为 `AGENT_EVENT_FILE_QUERY`，payload 以以下形式之一开头，并绑定 lifecycle id/generation：

```text
change=ENTER;lc=<id>:<generation>
change=UPDATE;lc=<id>:<generation>
change=LEAVE;lc=<id>:<generation>
change=RESYNC_REQUIRED;lc=<id>:<generation>
```

`ENTER/UPDATE/LEAVE` 是谓词的 before/after 变化，不是目录扫描结果。事件仍服从 queue capacity、scope 可见性和进程 control id。增量可能丢失时，内核合并并发送 `RESYNC_REQUIRED`，调用者必须重新 query/snapshot，再用返回 generation ACK。ACK 只清除不晚于该 generation 的缺口。

`agent_live_unwatch()` 需要同一 `watch_id`；进程 exec/exit/reuse 会清除订阅，旧 token 不能控制新进程。

### 6.3 legacy string watch

```c
int agent_watch(int event_type, const char *filter);
int agent_unwatch(int event_type, const char *filter);
```

传统 `FILE_STATUS` 字符串 filter 仍作为兼容接口存在。新 live-query 功能应使用 typed API；typed watch 不会回退到字符串 substring 分支。

## 7. Agent Loop 与 IPC

```c
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_wait_cancel(int pid, const char *reason);
int agent_wake(int pid, struct agent_event *event);
int agent_route_config(int source_pid, int target_pid,
                       uint64 event_mask, int operation);
int agent_heartbeat_set(uint64 interval_ticks);
int agent_heartbeat_stop(void);
```

事件队列总容量、内核保留和来源限额在 `user/include/agent.h` 定义。跨 Agent message/LLM delivery 必须命中同 active workflow 的定向路由。file-query、policy-denied、timer 等内核事件不能通过 `agent_wake()` 伪造。

`agent_wait()` 为线程级等待；timeout 不延长，cancel 与进程/thread generation 绑定。heartbeat 只安排内核 timer 事件，不启动用户逻辑。

## 8. 结构化工具与批处理

```c
int agent_run(struct agent_op *ops, struct agent_result *results,
              int count, uint64 flags);
int tool_call(struct agent_request_v2 *req,
              struct agent_response_v2 *resp);
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp);
int tool_list(struct agent_tool_desc_v2 *out, int max);
```

普通批处理要求 count 和 flags 符合 `agent_run` 合同。工具目录、typed KV 版本、参数类型、必要字段和 capability 都由内核校验。workflow fence 是 count 0 的独立控制路径，不会被工具 dispatcher 当作空批次成功。

### 8.1 Contract-bound V3

V3 请求的前 72 字节与 V2 完全一致，完整结构为 200 字节；V3 响应的前 184 字节与 V2 一致，完整结构为 280 字节。新增字段绑定：

| 字段 | 验证 |
| --- | --- |
| `contract` | lifecycle + immutable contract generation |
| `node_id/attempt_id` | 合同节点、attempt 上限、retry/cancel policy |
| `source_node_id` | 必须是 declared predecessor；root 使用 `NODE_NONE` |
| `source_context_sequence` | 必须精确等于该 predecessor 已提交的 Context sequence；root 为 0 |
| `schema_digest` | 完整 32 字节，与冻结 manifest 相同 |
| `input_fingerprint` | domain-separated SHA-256，覆盖 tool/arg/flags/payload |
| artifact type | 必须与合同节点输入/输出声明一致 |

响应追加 evidence ticket、decision reason、completion flags 和 output provenance labels。已完成节点的同内容重试返回 `CACHED` 原终态，不重新执行副作用。deadline/dependency/cancel/phase/provenance 拒绝均在工具效果前形成 Context/Evidence 终态。

### 8.2 Provenance manifest

固定来源位为：

```text
KERNEL_FACT, TRUSTED_USER_CONTROL, AGENT_DERIVED,
UNTRUSTED_FILE_DATA, UNTRUSTED_TOOL_OUTPUT, CROSS_AGENT_DATA
```

manifest 固定 accepted input、output-added labels、required capabilities 和 file/metadata/IPC/process/permission/artifact/watch side effects。Context flags hash-bind 标签，file query、tool output 和 IPC 保守传播。外部副作用必须同时通过完整 lifecycle、contract edge、capability、manifest 和 provenance 检查；非法调用返回 `DENIED` 并写 critical Evidence。该接口不接受自由字符串标签，也不执行 prompt 内容分类。

### 8.3 异步 Task Channel

公共 ABI 在 `agent_task_channel_abi.h`，版本 1 使用三个独立 syscall：

| syscall | 号 | 功能 |
| --- | ---: | --- |
| `agent_task_channel_setup()` | 563 | 按需建立 single-issuer channel，返回 SQ/CQ 地址和 generation |
| `agent_task_channel_enter()` | 564 | 提交 SQ tail、确认 CQ head、drain 或 sticky resync |
| `agent_task_channel_resource()` | 565 | import/release/query typed resource handle |

setup 必须只携带 `SINGLE_ISSUER`，并绑定当前 Agent main thread 和完整 lifecycle。成功返回两个 mapped page（SQ read/write、CQ read-only）和两个 private page，共 4 页；SQ/CQ 容量均为 16，SQE/CQE 均为 128 字节。

SQE 必须使用严格递增 request id，并携带 ring/slot generation、contract/node/attempt/tool、deadline、link target、input handle 和 32 字节 schema digest。内核只读取共享 entry 一次，复制完整 128 字节后才验证。CQE 返回唯一 target terminal status、result handle、Context sequence、Evidence ticket、provenance 和 completion tick；公共 `completion_tick` 在执行结果确定后采样，不表示 pre-effect service start。

`CANCEL` 是 target-only SQ command：它有自己的单调 request id，但只引用 target `link_request_id`，不产生第二个 CQE。accepted target 在成功/失败/取消/timeout 中只能发布一个 terminal CQE；这不等于对远程副作用提供分布式 exactly-once。timer IRQ 只设置 `DEADLINE_DUE` 并尽力唤醒 generation-matched issuer，TIMEOUT/Context/Evidence 到该进程第一个可调度 safe point 才结算；不可中断睡眠或 provider 停滞没有 wall-clock completion bound。CQ full 只造成 backpressure。协议水位异常设置 sticky `RESYNC`，issuer 必须显式 enter 恢复。

typed handle 固定 16 字节 `{slot, type, flags, generation}`，私有 capacity 为 8。ABI 与 generation/producer/digest/provenance/owner 重验已实现，但当前发布切片不提供用户态资源导入或结果 payload backend：`RESOURCE_IMPORT` fail closed，provider 只接受 null input，CQE `result` 为 null；`BORROWED` 是保留 ABI。

### 8.4 MCP/A2A gateway

用户态 `host_tools/mcp_a2a_gateway.py` 映射 MCP `2026-07-28` 的 tools/list/call、Tasks get/update/cancel，以及 A2A v1 的 Task、Context、Message/Part、Artifact、stream/cancel。`mcp_task_notifications()` 单独生成通知，外层仍需实现 subscriptions/listen binding。当前 gateway 使用 deterministic in-memory transport 保存 lifecycle/contract/channel/request 绑定，尚无到内核 SQ/CQ 的 binary adapter；它不把 JSON、HTTP、OAuth、JWS 或远程持久化放入内核，也不是内核 MCP/A2A server。

### 8.5 当前 Task Guest 测量字段

`agenttask_ucore` 让 batch、scalar V3 和 SQ/CQ 各执行 16 次空 `ECHO` 并要求同一 OK/tool/Context-proof/evidence-proof/zero-result fingerprint，但不声称三者使用相同线格式。batch 使用清零 `agent_op`；scalar V3 必须显式携带 `payload=""` string、`arg0=0` uint64、`arg1=0` uint64 三个 typed params；SQ/CQ 使用 null input handle，三者的 output artifact 均为 `NONE`。legacy batch 以 Context record hash 满足 evidence-proof，contract-bound 路径使用 Evidence ticket。

三者的 Guest 调用点 `syscalls` 分别为 1、16、2；边界 observer、事后 Context 查询和 setup 不计入这些数值。描述符 ABI/已知复制字节分别为 batch `3584 = 16 * (104 + 120)`、scalar V3 `12288 = 16 * (200 + 280 + 3 * 96)`、SQ/CQ `4096 = 16 * (128 + 128)`；scalar 另报 128 字节 dispatch header，SQ/CQ 另报 336 字节 control ABI 和 544 字节已知 control copy。它们由调用点与冻结 ABI 大小计算，不是内核路径 counter、实测总复制量或全部内存流量。

Context record `tick` 是内核在工具效果前采样的 service start，不是 completion 或 CPU service 量。`service_start_interval_tick_p50/p99` 是这些 pre-effect tick 间隔的 nearest-rank 分位数；`sequence_elapsed_ticks` 来自两个 `agent_info` 边界 tick，包含边界开销。序列后的 Context 查询只读取既有记录且不计入 elapsed，公共 CQE `completion_tick` 也不参与 service-start 分位数。当前不报告 raw cycles 或 wall clock；cancel latency 仅测量对 retained terminal 的一次幂等 cancel，不代表同步 provider 已支持 running/pending cancel。

## 9. 安全兼容清单

| 名称 | 当前状态 |
| --- | --- |
| audit/timeline/provenance/ledger | 支持，作为内存兼容聚合视图 |
| audit `DURABLE` 宏 | 兼容别名，实际为 `FENCE_SEALED` |
| observe recovery request/syscall | tombstone，固定 `BAD_PARAM` |
| metadata `PERSIST` | legacy flag，显式 set 拒绝 |
| metadata `AUTOSCAN` | legacy flag，显式 set 拒绝 |
| `.agentmeta` bank/journal/recovery | 不属于当前生产合同 |
| typed live query | 支持，volatile，带 generation resync |
| workflow fence | 支持，challenge-bound，320 字节 receipt |
| scalar V2 / `agent_run()` batch | 保留；未启用 enforcement contract 时语义不变 |
| V3 contract-bound call | 支持；保留 V2 prefix，绑定 24-node frozen contract |
| lifecycle info V2 | 保留 64 字节 prefix；当前 V3 为 216 字节 |
| Task Channel | 按需支持；16 槽、4 页、single issuer、sticky resync；当前 null payload，resource import fail closed |
| MCP/A2A gateway | 用户态协议形状/in-memory 映射；尚无内核 binary adapter |
| Wasm/WASI runtime | 不提供；typed handle 只参考 WIT ownership 语义 |

## 10. 验证

```bash
python -B scripts/check-agent-uapi-layout.py
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-agent-live-query-fs.py
python -B scripts/test-agent-execution-contract.py
python -B scripts/test-agent-task-channel.py
python -B host_tools/test_workflow_scheduler_model.py
python -B host_tools/test_agent_task_transport.py
python -B host_tools/test_mcp_a2a_gateway.py
make agent-module-check TOOLPREFIX=riscv-none-elf-
```

这些检查验证接口形状和实现合同，不自动形成发布性能证据。动态结果只从正式 bundle 读取。
