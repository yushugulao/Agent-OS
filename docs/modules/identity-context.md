# 身份、生命周期与 Context

AgentOS 把一次 workflow 视为由多个受控进程共同完成的内核活动。身份确定“谁在执行”，lifecycle 确定“属于哪一次运行”，Context 记录“这一步由什么信息和动作产生”。三者共同为工具、文件、事件、资源和调度提供稳定的主键。

## 文档索引

- [一、模块目标](#一模块目标)
- [二、Agent 身份模型](#二agent-身份模型)
- [三、Workflow 生命周期](#三workflow-生命周期)
- [四、Context 地址空间](#四context-地址空间)
- [五、提交、一致读取与回滚](#五提交一致读取与回滚)
- [六、来源传播](#六来源传播)
- [七、创建到退出的代码路径](#七创建到退出的代码路径)
- [八、测试结果](#八测试结果)

## 一、模块目标

PID 能标识当前进程，却不能回答一个 Agent 是否由可信映像启动、拥有什么业务角色、由谁控制、属于哪次 workflow，也不能阻止槽位复用后的旧句柄访问新对象。多轮 Context 还需要同时支持低开销读取、完整因果记录、分支回滚和退出回收。

为此，我们在 uCore `struct proc` 中保存 Agent 身份和 workflow key，在 VFS 中保存动态 scope 与映像绑定，在固定虚拟地址映射 Context，并用 lifecycle gate 串联创建、普通操作、退出和 fence。核心实现分别位于 [`os/agent_identity.c`](../../os/agent_identity.c)、[`os/workflow_lifecycle.c`](../../os/workflow_lifecycle.c)、[`os/vfs_security.c`](../../os/vfs_security.c) 和 [`os/agent_context.c`](../../os/agent_context.c)。

## 二、Agent 身份模型

### 2.1 身份字段

Agent 身份直接附着在 uCore 进程对象上。一次受控创建完成后，内核维护以下状态：

| 字段组 | 内容 | 用途 |
| --- | --- | --- |
| Agent 身份 | `agent_id`、`role`、`control_id`、`controller_id` | 标识执行者并建立控制关系 |
| Workflow 身份 | `workflow_lifecycle_id`、`generation` | 隔离槽位复用前后的两次运行 |
| 权限 | `agent_capability_mask`、VFS effective/inheritable capability | 检查内核工具与文件操作 |
| 文件域 | `vfs_scope_id`、storage principal、绑定映像 identity | 限制可访问的 workflow 文件与可执行映像 |
| 运行状态 | loop state、heartbeat、Context、事件队列、资源账户 | 驱动等待、观测、资源与调度模块 |

`agent_id` 由内核分配；`control_id` 用于控制关系判断，并不会作为用户可转让的凭据。跨模块长期引用使用完整 lifecycle key：

```c
struct workflow_lifecycle_key {
    uint id;
    uint64 generation;
};
```

槽位编号 `id` 的取值为 `1..WORKFLOW_LIFECYCLE_CAP`，当前最多 4 个 active workflow，生命周期表保留 8 个槽位用于 active/retiring 状态。每个空槽位再次投入使用时 `generation` 递增，查找函数同时比较 `used` 和 generation。定义见 [`os/workflow_lifecycle.h`](../../os/workflow_lifecycle.h)。

### 2.2 Role 与 capability

Role 给出能力上限和 Agent 内部调度初值。当前策略定义在 [`os/agent_identity.c`](../../os/agent_identity.c)：

| Role | 主要 capability | Agent 调度 weight |
| --- | --- | ---: |
| Sentinel | metadata/process read、message、watch、audit write | 70 |
| Investigator | metadata/content read、message、watch、audit write | 90 |
| Recovery | metadata/content read、message、watch、action/artifact/audit write | 120 |
| Artifact | metadata/content read、message、watch、artifact/audit write | 100 |
| Orchestrator | 全部 13 项 Agent capability，可创建和管理协作者 | 110 |

这里的 weight 作用于同一 workflow 内的 per-Agent 策略；多个 workflow 之间的外层 EEVDF 使用固定等权实体。权限不会因子进程或 `exec` 扩张，实际 capability 是 role policy、映像 profile、父进程 effective capability 和请求掩码的交集。

### 2.3 可信创建与映像绑定

AgentOS 提供三条受控创建路径：

| 入口 | 调用者 | 结果 |
| --- | --- | --- |
| `agent_workflow_create(role)` | 可信 bootstrap、resource-domain admin | 新建动态 VFS scope、lifecycle、resource domain 和 root Agent |
| `agent_create_role(role)` | 已有 workflow 中具备 role grant 的 Agent | 在同一 workflow 内创建 Agent 子进程 |
| `agent_worker_create(image, caps)` | 同一 workflow 中具备 `AGENT_CAP_ORCHESTRATE` 的 Agent | 生成待 exec worker；caps 必须非零，只能取 `CONTENT_READ | ARTIFACT_WRITE` 的调用者子集 |

`agent_worker_create()` 先检查请求 capability 非零，且只包含 `AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE` 中调用者已经持有的位；随后在 system scope 查找目标 inode，确认执行权限和 immutable/domain-safe profile，再把 `dev + inum + incarnation` 写入 child 的 pending identity。真正 `exec` 时，[`vfs_proc_exec_prepare()`](../../os/vfs_security.c) 用当前加载映像重新比较这三个字段，并将请求 capability 与映像 ceiling 取交集。映像对象发生变化时 incarnation 不再匹配，进程转入 public identity；受控 Agent 身份不会发布到新的映像上。

`fork` 发布子进程时，[`agent_identity_spawn_publish_locked()`](../../os/agent_identity.c) 在同一关中断窗口内核对父子 lifecycle 并写入 controller edge。controller 退出先经历 `OPEN -> QUIESCING -> RETIRED`，可交接的子控制器转给有效继任者，其余后代随旧控制边撤销。

## 三、Workflow 生命周期

### 3.1 Lifecycle record

每个槽位保存一条 `workflow_lifecycle_record`：

```c
struct workflow_lifecycle_record {
    int used;
    uint scope_id;
    uint64 generation;
    uint64 controller_control_id;
    uint64 next_context_branch;
    uint64 fence_sequence;
    uint members;
    uint active_operations;
    uint departing_operations;
    int fence_gate;
    int closing;
};
```

`members` 表示仍持有 lifecycle 的进程；`active_operations` 统计正在执行的普通操作；`departing_operations` 统计退出和异步释放；`fence_gate` 在取得一致性切片时阻止两类操作进入。实现见 [`os/workflow_lifecycle.c`](../../os/workflow_lifecycle.c)。

### 3.2 三类 gate

| Gate | 进入者 | 接纳条件 | 退出条件 |
| --- | --- | --- | --- |
| operation | 工具调用、fork、metadata 更新等 | lifecycle active，未 closing，未持有 fence | 操作终态已提交并释放临时资源 |
| departure | `exit`、controller departure、异步 teardown | members 仍大于 0，未持有 fence | 该阶段清理完成 |
| fence | controller 的一致性切片 | 未 closing，operation/departure 均为 0 | receipt 提交或 fence 中止 |

operation gate 的实现保持得很小，使它可以嵌入系统调用和创建路径：

```c
record = workflow_lifecycle_find_locked(key);
if (record != 0 && !record->closing && record->members > 0 &&
    !record->fence_gate && record->active_operations != (uint)-1) {
    record->active_operations++;
    result = 0;
}
```

workflow close 将 `closing` 置位，停止接纳新 operation。最后一个成员离开时 `members` 变为 0，lifecycle 进入 retiring。只有 operation、departure 和 fence 全部清空后，`workflow_lifecycle_reclaim()` 才清除槽位；新 workflow 得到相同 `id` 时会使用下一代 generation。

### 3.3 统一回收

生命周期 finalizer 按同一 key 回收 watch、metadata catalog、execution contract、Task Channel、执行记录环、执行资源账户和调度实体。普通 VFS scope 随后删除文件并从 registry 移除；标记 `preserve_on_retire` 的持久输出 scope 保留 registry、文件与 storage charge，并清除旧 lifecycle 绑定。各模块在释放前都比较完整 key，因此旧 watch、request、contract 或 handle 会得到 `STALE`、`CONFLICT` 或 `NOT_FOUND`，不会落入下一轮 workflow。

进程退出使用 departure gate 覆盖可能跨越 syscall 的清理；系统调用入口使用 operation gate 覆盖普通 Agent 操作；fence 不在持锁状态睡眠，暂时无法取得无活动切片时返回 `RETRY`。

## 四、Context 地址空间

### 4.1 映射布局

每个 Agent 的 Context 位于 trapframe 下方的固定地址 `AGENT_CONTEXT_BASE`。布局由 [`os/agent.h`](../../os/agent.h) 定义：

| 区域 | 页数 | 用户权限 | 内容 |
| --- | ---: | --- | --- |
| Public header/latest | 第 1 页 | 只读 | magic、版本、窗口、active head、latest response、publication sequence |
| Record ring | 第 2-6 页 | 只读 | 128 条 `agent_context_record` |
| Guest cache | 第 7 页 | 读写 | 用户态可维护的派生 cache |
| Kernel sidecar | 9 页 | 不映射 | 规范化 operation/result、actor、cause、path index、来源状态 |
| IPC/observe cold state | 1 页 | 不映射 | event、watch、route 和观测冷数据 |

[`agent_context_map()`](../../os/agent_context.c) 为前 6 页设置 `PTE_R | PTE_U`，只给第 7 页增加 `PTE_W`。`exec` 保留 Agent identity 时，[`agent_alias_exec_context()`](../../os/agent_context.c) 把同一组物理页按原权限映射进新页表；身份降为 public 时不再暴露该映射。

### 4.2 Record 与 detail

一条 public record 保存 sequence、request id、cause sequence、span、branch、active-path parent、tool、status、短 payload/result 和哈希链。固定记录尺寸支持直接映射遍历。同槽位 sidecar 保存固定大小的规范化 `agent_op`、`agent_result` 与归因字段，通过 `context_detail(sequence)` 按保留窗口读取；typed V2 参数数组、V3 binding 与 Task SQE 不在其中完整保留。

| 字段 | 含义 |
| --- | --- |
| `sequence` | 同一 clear epoch 内单调递增的 Context 记录号，FIFO 淘汰后不复用；`context_clear()` 后从 1 重新开始 |
| `cause_sequence` / `span_id` | 当前动作的直接原因与跨模块执行跨度 |
| `branch_generation` / `path_parent_sequence` | rollback 后的新分支和 active path 前驱 |
| `prev_hash` / `record_hash` | archive 顺序的哈希链 |
| `flags` | system/manual/truncated、来源标签和 security denial |

Context 容量固定为 128 条。窗口满后覆盖最旧槽位，header 的 `oldest_sequence`、`latest_sequence` 和 `dropped_records` 同步更新。

## 五、提交、一致读取与回滚

### 5.1 FIFO commit lane

工具结果、系统事件、手工记录、rollback 和 clear 共用每进程 Context lane。追加记录时，writer 完成以下步骤：

```text
预检 sequence、span 与 lane owner
  -> 写入 sidecar detail
  -> publication sequence 变为奇数
  -> 写 public record，更新 path index 与内核计数
  -> 写 latest response 和 header
  -> reserved terminal 路径提交预留的 evidence ticket
  -> publication sequence 变为偶数
```

[`agent_context_publish_begin()`](../../os/agent_context.c) 用 acquire-release 原子加法把 publication sequence 从偶数变为奇数，`agent_context_publish_end()` 用 release 加法恢复偶数。reserved terminal record 保持奇数状态直到 canonical evidence ticket 已提交，因此直接 reader 不会看到只有 Context、没有 evidence 的中间状态。手工 push 使用普通追加路径，不预留 evidence；rollback 和 clear 只在奇偶发布窗口中更新 active path 或重置 Context，不追加新 record，也不提交 evidence ticket。

### 5.2 读取接口

| 接口 | 读取方式 | 适用场景 |
| --- | --- | --- |
| `context_direct_header_snapshot()` | 映射读取，前后比较 publication sequence | 高频读取 header |
| `context_direct_active_query()` | 映射读取 active path，前后比较 publication sequence | Guest 热路径查询 |
| `context_query()` | syscall 复制 active path | 直接读取连续冲突后的稳定后备路径 |
| `context_snapshot()` | syscall 同时返回 header 与 records | 调试和完整快照 |
| `context_detail()` | syscall 读取 sidecar 中规范化的 `agent_op` 与 `agent_result` | 查看单条记录详情 |

Guest 的 direct-reader 实现在 [`user/lib/syscall.c`](../../user/lib/syscall.c)。reader 仅接受前后相同的偶数 publication sequence；持续写入时返回 retry，由调用方切换到 syscall 查询。

### 5.3 分支与回滚

`context_rollback(sequence)` 先确认目标仍在 FIFO 窗口并可沿 parent 链重建 active path，然后从 workflow lifecycle 分配新的 branch generation。目标记录成为新的 visible head，archive 中已有记录继续保持原 sequence 和 hash。后续提交从新 branch 继续，rollback 不会复用 sequence。

rollback 只改变 Agent 的决策上下文。已经完成的文件写入、IPC 和工具副作用继续存在；应用需要通过新的补偿动作处理这些结果。`context_clear()` 分配新 branch，清空 public record 和 path index，并把本地 Context sequence 重新置零；evidence ticket 仍由 workflow evidence ring 独立保持单调。clear 与普通提交使用相同 publication 协议。

## 六、来源传播

AgentOS 使用六种固定标签表示输入来源，定义见 [`include/agent_provenance_abi.h`](../../include/agent_provenance_abi.h)：

| 标签 | 数据来源 |
| --- | --- |
| `KERNEL_FACT` | 内核直接观测 |
| `TRUSTED_USER_CONTROL` | 控制面绑定的用户决定 |
| `AGENT_DERIVED` | Agent 计算或汇总结果 |
| `UNTRUSTED_FILE_DATA` | 文件内容或 metadata 派生数据 |
| `UNTRUSTED_TOOL_OUTPUT` | 工具输出 |
| `CROSS_AGENT_DATA` | 跨 Agent 消息或结果 |

标签沿 Context、文件读取、工具终态与 IPC 做按位 OR 传播。选择 execution-contract 前驱时可以增加前驱记录的标签，不能删除当前标签。工具 manifest 声明接受的输入标签、输出新增标签、required capability 和真实 side-effect mask；[`agent_provenance_authorize_tool()`](../../os/agent_provenance.c) 在副作用前复核 lifecycle、contract generation、DAG edge、来源集合和 effect mask。拒绝记录写入 critical evidence 分区。

这种固定词汇适合内核快速判断，粒度为来源类别，不表达自然语言事实级关系。事实之间的具体语义仍由 Context record、artifact 和上层应用保存。

## 七、创建到退出的代码路径

一次典型 Agent 的身份与 Context 路径如下：

1. 可信 bootstrap 调用 `agent_workflow_create()`；[`agent_workflow_create_proc()`](../../os/proc.c) 检查 bootstrap 与 resource-domain admin，并使用 fresh scope 创建 root；
2. VFS 创建动态 scope 和 lifecycle key，resource controller 建立执行与存储账户；
3. root Agent 发布 role、capability、control id，并分配 Context、sidecar 与 cold-state 页；
4. 具备 `AGENT_CAP_ORCHESTRATE` 的 Agent 调用 `agent_worker_create()`，内核记录目标映像 `dev/inum/incarnation`，worker 在匹配的 `exec` 上发布 scoped identity；
5. 工具结果、事件消费和消息通过 Context lane 追加 record，来源标签同步推进；
6. rollback 分配新 branch generation 并重建 active path；
7. `exec` 重新计算映像和 capability，`exit` 进入 departure gate；
8. 最后成员与后台工作排空后，lifecycle finalizer 释放 Context 和关联对象；普通 scope 回收，持久输出 scope 留存文件并解除旧 lifecycle 绑定。

公开调用原型集中在 [`user/include/agent.h`](../../user/include/agent.h)，状态码与工具基础 ABI 位于 [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)。

## 八、测试结果

身份与 Context 使用静态契约测试和 QEMU Guest 回归共同验证：

| 测试 | 关键验证项 |
| --- | --- |
| `agenttrust_ucore` | 可信 Agent exec、immutable image、role/image binding、bootstrap role grant |
| `agentfinal_ucore` | 前 6 页只读映射、commit lane、snapshot、rollback、新 branch、FIFO 淘汰、direct query |
| `agentscope_ucore` | workflow scope 隔离、controller exit 撤销、lifecycle reclamation |
| Context 原子性脚本 | terminal record/evidence ticket、并发 snapshot reader、wait publication |
| Workflow syscall cut | operation gate 与 close/fence 并发关系 |

保留在 Guest 输出校验器中的代表性标记包括 `context_commit_lane=1 sequence=1..3 hash=1`、`context_rollback_branch=1 sequence_reuse=0 provenance_bound=1`、`fifo oldest=66 latest=193 dropped=65` 和 `lifecycle_reclamation=1`。运行入口如下：

```bash
python3 -B scripts/test-exec-image-policy.py
python3 -B scripts/test-context-evidence-atomicity.py
python3 -B scripts/test-context-snapshot-reader-atomicity.py
python3 -B scripts/test-workflow-syscall-cut.py

AGENT_TEST_CASE=agenttrust_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentfinal_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentscope_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

身份和 Context 建立了可复用的 lifecycle 主键与因果记录。下一步，[工具执行](tool-execution.md)使用这组状态检查结构化请求、execution contract、来源与资源，[Live Query](live-query.md)再把文件状态接入同一个 workflow。
