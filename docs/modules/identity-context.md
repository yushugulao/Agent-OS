# Agent 进程创建、地址空间与上下文路径

一次工作流可能由多个受控进程共同完成。Agent identity 说明当前由谁执行，生命周期说明进程属于哪一次运行，Context 则记录每一步用了什么信息、产生了什么结果。我们把 Agent 进程创建与地址空间设计、上下文路径管理放在同一条进程生命周期中：需要保护的低频状态留在内核，Agent Loop 高频读取的路径和缓存映射到用户地址空间。

## 文档索引

- [一、模块目标](#一模块目标)
- [二、Agent identity](#二agent-identity)
- [三、工作流生命周期](#三工作流生命周期)
- [四、Context 地址空间](#四context-地址空间)
- [五、Context commit、读取与回滚](#五context-commit读取与回滚)
- [六、Provenance 传播](#六provenance-传播)
- [七、从创建到退出](#七从创建到退出)
- [八、测试结果](#八测试结果)

## 一、模块目标

PID 只能指出当前进程，无法说明进程是否从可信映像启动、担任什么角色、由谁控制、属于哪次工作流。进程槽位再次使用后，单靠 PID 也无法挡住旧句柄访问新进程。与此同时，多轮任务的 Context 既要便于用户态读取，也要保留因果关系、支持分支回滚，在超过配额时有界淘汰，并在进程退出后及时回收。

为此，我们在 uCore 的 `struct proc` 中保存 Agent identity、Loop 状态和生命周期键，在 VFS 中保存动态文件访问范围与映像绑定，并将 Context 映射到固定虚拟地址。前者用于授权、回收和内核调度，后者供 Guest 直接读取路径、最近响应和查询缓存。生命周期记录分别统计普通操作与退出操作，生成 Workflow Fence 时暂停接纳新操作。主要实现位于 [`os/agent_identity.c`](../../os/agent_identity.c)、[`os/workflow_lifecycle.c`](../../os/workflow_lifecycle.c)、[`os/vfs_security.c`](../../os/vfs_security.c) 和 [`os/agent_context.c`](../../os/agent_context.c)。

## 二、Agent identity

### 2.1 身份字段

受控创建完成后，Agent identity 直接附着在 uCore 进程对象上：

| 字段组 | 内容 | 作用 |
| --- | --- | --- |
| Agent identity | `agent_id`、`role`、`control_id`、`controller_id` | 标识执行者并保存控制关系 |
| Workflow identity | `workflow_lifecycle_id`、`generation` | 隔开槽位复用前后的两次运行 |
| 权限 | `agent_capability_mask`、VFS 有效/可继承能力位 | 检查工具调用和文件操作 |
| 文件访问范围 | `vfs_scope_id`、存储主体、映像身份 | 限制工作流文件和可执行映像 |
| 运行状态 | 循环状态、心跳、Context、事件队列、资源账户 | 供等待、观测、资源和调度模块使用 |

`agent_id` 由内核分配。`control_id` 只用于判断控制关系，用户态不能把它转交给其他进程。跨模块的长期引用都使用完整生命周期键：

```c
struct workflow_lifecycle_key {
    uint id;
    uint64 generation;
};
```

槽位编号的范围为 `1..WORKFLOW_LIFECYCLE_CAP`。系统当前最多允许 4 个活动工作流，生命周期表共有 8 个槽位，用来容纳活动记录与待回收记录。空槽位再次使用时 `generation` 递增，查找函数同时比较 `used` 和 `generation`。定义见 [`os/workflow_lifecycle.h`](../../os/workflow_lifecycle.h)。

### 2.2 角色与能力位

角色决定能力上限，也给出同一工作流内的初始调度权重。策略定义在 [`os/agent_identity.c`](../../os/agent_identity.c)：

| 角色 | 主要能力位 | Agent 调度权重 |
| --- | --- | ---: |
| `Sentinel` | 读取元数据和进程状态、发送消息、订阅文件、写结果和审计记录、接收 delegated task | 70 |
| `Investigator` | 读取元数据和文件内容、发送消息、订阅文件、写结果和审计记录、接收 delegated task | 90 |
| `Recovery` | 读取元数据和文件内容、发送消息、订阅文件、执行动作、写结果和审计记录、接收 delegated task | 120 |
| `Artifact` | 读取元数据和文件内容、发送消息、订阅文件、写结果和审计记录、接收 delegated task | 100 |
| `Orchestrator` | 14 项 Agent 能力位，可创建并管理协作者 | 110 |

表中的权重只用于同一工作流内部选择 Agent。多个工作流之间的 EEVDF 调度对象保持等权。子进程或 `exec` 都不能扩大权限。进程最终拥有的能力集合，是角色策略、映像配置、父进程有效能力和请求掩码的交集。

`Sentinel` 与 `Investigator` 具有 `AGENT_CAP_ARTIFACT_WRITE`，因为 Nexus 的 System 与 Research 是结果 artifact 的实际生产者。这项角色能力不等于任意文件写入：发布仍要通过 artifact manifest permission、当前 VFS 文件访问范围以及精确绑定到 delegated task 的 effect lease。

### 2.3 受控创建与映像绑定

AgentOS 提供三条受控创建路径：

| 入口 | 调用者 | 创建结果 |
| --- | --- | --- |
| `agent_workflow_create(role)` | 可信引导进程、资源管理员 | 新建动态 VFS 文件访问范围、生命周期、Workflow Credit Domain 和根 Agent |
| `agent_create_role(role)` | 同一工作流中可以授予角色的 Agent | 创建同一工作流内的 Agent 子进程 |
| `agent_worker_create(image, caps)` | 同一工作流中具有 `AGENT_CAP_ORCHESTRATE` 的 Agent | 创建等待 `exec` 的工作进程；`caps` 必须非零，且只能从调用者已有的 `CONTENT_READ | ARTIFACT_WRITE` 能力中选择 |

`agent_worker_create()` 先检查申请的能力集合不为空，也没有超出 `AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE` 和调用者已有能力。随后，内核在系统文件访问范围中查找目标 inode，检查执行权限以及映像是否不可修改、是否允许跨资源账户使用，再把 `dev + inum + incarnation` 写入子进程的待发布身份。真正执行 `exec` 时，[`vfs_proc_exec_prepare()`](../../os/vfs_security.c) 会用当前映像重新核对这三个字段，并把请求能力与映像能力上限取交集。映像内容变化后，`incarnation` 不再匹配，进程会降为公共身份，受控身份不会发布到新映像上。

发布 `fork` 子进程时，[`agent_identity_spawn_publish_locked()`](../../os/agent_identity.c) 在同一次关中断期间核对父子生命周期并写入控制关系。控制进程退出时依次经过 `OPEN -> QUIESCING -> RETIRED`；可以交接的子控制关系转给有效继任者，其余关系随原控制进程一起撤销。

## 三、工作流生命周期

### 3.1 生命周期记录

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

`members` 是仍持有该生命周期的进程数。`active_operations` 统计正在执行的普通操作，`departing_operations` 统计进程退出和异步释放。代码字段 `fence_gate` 表示 Workflow Fence 正在暂停前两类新操作。实现见 [`os/workflow_lifecycle.c`](../../os/workflow_lifecycle.c)。

### 3.2 普通操作、退出操作与 Workflow Fence 协调

| 操作类型 | 涉及操作 | 开始条件 | 完成条件 |
| --- | --- | --- | --- |
| 普通操作 | 工具调用、`fork`、元数据更新等 | 生命周期处于活动状态、尚未关闭，且 Workflow Fence 未暂停新操作 | terminal state 已 commit，临时资源已释放 |
| 退出操作 | `exit`、控制进程离开、异步清理 | `members > 0`，且 Workflow Fence 未暂停新操作 | 本阶段清理完成 |
| Workflow Fence | 控制进程生成 Workflow Fence | 生命周期尚未关闭，普通操作与退出操作计数均为 0 | 回执已 commit，或本次 Workflow Fence 中止 |

普通操作的进入检查很短，可以直接放进系统调用和进程创建路径：

```c
record = workflow_lifecycle_find_locked(key);
if (record != 0 && !record->closing && record->members > 0 &&
    !record->fence_gate && record->active_operations != (uint)-1) {
    record->active_operations++;
    result = 0;
}
```

关闭工作流时，内核将 `closing` 置位，不再接纳新的普通操作。最后一个成员离开后，`members` 变为 0，生命周期进入待回收状态。普通操作、退出操作和 Workflow Fence 全部结束后，`workflow_lifecycle_reclaim()` 才清空槽位。下一次工作流即使取得相同 `id`，也会使用新的 `generation`。

### 3.3 按生命周期回收

生命周期收尾程序按同一个键回收 Typed Watch、Metadata Catalog、Execution Contract、Task Channel、delegated task、工作流记录环、Workflow Credit Domain 和调度对象。随后回收普通 VFS 文件访问范围，删除其中的文件，并从 Metadata Catalog 移除相应记录。标记 `preserve_on_retire` 的 artifact 会保留元数据、文件和存储计费，只解除与旧生命周期的绑定。各模块在释放前都比较完整键，因此旧订阅、请求、Contract 和句柄只会得到 `STALE`、`CONFLICT` 或 `NOT_FOUND`，不会落入下一次工作流。若 delegated task 已经被目标 claim，关闭会生成协作式终态 offer 并唤醒目标；目标清理预绑定结果并确认最新 offer 后，owner 才收到唯一 terminal CQE。当前实现不能强制收敛永久无响应的已 claim 执行者。

进程退出可能跨越一次系统调用，因而全程计入退出操作。普通 Agent 系统调用计入普通操作。Workflow Fence 不会持锁睡眠，暂时无法取得静止时点时返回 `RETRY`。

## 四、Context 地址空间

### 4.1 映射布局

每个 Agent 的 Context 位于 trapframe 下方固定地址 `AGENT_CONTEXT_BASE`。布局定义在 [`os/agent.h`](../../os/agent.h)：

| 区域 | 页数 | 用户权限 | 内容 |
| --- | ---: | --- | --- |
| 公开表头与最近响应 | 第 1 页 | 只读 | magic、version、记录窗口、活动头、最近响应、发布序号 |
| 记录区 | 第 2-6 页 | 只读 | 128 条 `agent_context_record` |
| Guest cache | 第 7 页 | 读写 | 用户态保存的派生数据 |
| 内核 sidecar | 9 页 | 不映射 | 规范化操作/结果、执行者、起因、路径索引和 provenance 状态 |
| IPC/观测冷状态 | 1 页 | 不映射 | 事件、订阅、路由和观测数据 |

[`agent_context_map()`](../../os/agent_context.c) 为前 6 页设置 `PTE_R | PTE_U`，只给第 7 页增加 `PTE_W`。`exec` 保留 Agent identity 时，[`agent_alias_exec_context()`](../../os/agent_context.c) 按原权限把同一组物理页映射进新页表；身份降为公共身份后，内核不再映射这些页面。

### 4.2 公开记录与详情

公开记录保存序号、请求号、起因序号、调用跨度、分支、活动路径父节点、工具、状态、短输入输出和哈希链。每条记录大小固定，用户态可以直接遍历。同一槽位的内核 sidecar 保存定长、规范化的 `agent_op`、`agent_result` 和归属字段。`context_detail(sequence)` 可以读取这些信息。V2 的完整参数数组、V3 绑定信息和 Task SQE 不会原样保存在详情中。

| 字段 | 含义 |
| --- | --- |
| `sequence` | 同一次 clear 周期内递增的记录编号；FIFO 淘汰后不复用，`context_clear()` 后从 1 重新开始 |
| `cause_sequence`、`span_id` | 当前动作的直接起因和跨模块调用跨度 |
| `branch_generation`、`path_parent_sequence` | 回滚后新分支的 generation，以及活动路径中的前一条记录 |
| `prev_hash`、`record_hash` | 按 commit 顺序形成的哈希链 |
| `flags` | 系统/手工、截断、provenance 和安全拒绝标记 |

Context 固定保存 128 条记录。写满后覆盖最旧槽位，并同步更新表头中的 `oldest_sequence`、`latest_sequence` 和 `dropped_records`。

delegated task 的 terminal Context 仍由 owner 的 commit lane 发布，但会单独保存实际执行者：`result.value0 = (agent_id << 32) | (uint32_t)pid`，`value1` 是执行者 `control_id`，`value2` 是执行者 Context sequence。这样，Execution Contract 的 issuer/terminalizer 与真正运行任务的 Agent 不会混成同一个身份；应用结果正文继续保存在 capsule 绑定的 Guest artifact 中。

## 五、Context commit、读取与回滚

### 5.1 FIFO commit lane

工具结果、系统事件、手工记录、回滚和清空操作共用每个进程的 Context commit lane。追加记录时，内核依次完成：

```text
预检序号、调用跨度和当前写入者
  -> 写入内核 sidecar 详情
  -> 发布序号变为奇数
  -> 写入公开记录，更新路径索引和内核计数
  -> 写入最近响应和表头
  -> commit 预留的工作流记录票号
  -> 发布序号恢复为偶数
```

[`agent_context_publish_begin()`](../../os/agent_context.c) 通过 acquire-release 原子加法，把发布序号从偶数改为奇数；`agent_context_publish_end()` 再用 release 语义恢复偶数。reserve terminal record 后，发布序号会一直保持为奇数，直到对应工作流记录票号也 commit 完成。直接读取程序因此不会看到只有 Context、没有终态记录的中间状态。手工追加不预留 terminal record；回滚和清空只更新活动路径或重置 Context，不增加新记录。

### 5.2 读取接口

| 接口 | 读取方法 | 用途 |
| --- | --- | --- |
| `context_direct_header_snapshot()` | 直接读取映射页，前后比较发布序号 | 高频读取表头 |
| `context_direct_active_query()` | 直接读取活动路径，前后比较发布序号 | Guest 常用查询 |
| `context_query()` | 通过系统调用复制活动路径 | 直接读取连续冲突时的备用路径 |
| `context_snapshot()` | 通过系统调用同时返回表头和记录 | 调试与完整快照 |
| `context_detail()` | 通过系统调用读取规范化的 `agent_op` 和 `agent_result` | 查看单条记录详情 |

Guest 直接读取程序的实现位于 [`user/lib/syscall.c`](../../user/lib/syscall.c)。只有前后两次发布序号相同且为偶数时，本次读取才有效。若 commit 持续发生，接口返回 `RETRY`，调用方改用系统调用读取。

每个通用 Agent Loop 都用自己的 active path 保持 private Context。USER、已经结算的 TOOL 和成功 FINAL 进入短 Context 节点；节点或 V2 detail 同时关联已经封存的正文 Artifact。下一轮优先直接读取映射页，必要时回退到 Context 系统调用，再按 Artifact handle 分页投影所需正文。失败与取消只回滚当前 Agent 的 active path，其他 Agent 已经接纳的结果和 workflow 共享索引保持原状。

### 5.3 Context Artifact Store

Context Artifact Store 的正文位于用户态受控存储中，覆盖 USER、TOOL、FINAL、文件、搜索结果、补丁、编译诊断、运行日志、测试结果、子任务报告、private summary 和 team summary。单项正文最大 64 KiB；Harness 默认最多保存 128 项、workflow 总计 2 MiB，每个 Agent 还受独立数量、字节和读取额度约束。模型投影单次最多 12 KiB，可以按 offset 分页读取。

内核通过 syscall 571 管理 Artifact seal metadata。`SEAL` 在正文文件完成写入后核对类型、长度、UTF-8、调用者身份与 SHA-256，记录 handle、producer、Task id、来源 Context sequence、workflow lifecycle 和保留时间；`BIND` 将已经封存的对象关联到 Context，`SHARE` 使已经结算且允许共享的对象进入 workflow 索引，`RELEASE` 只减少当前分支引用。Task 完成必须先封存结果，再提交 terminal 状态。父 Agent 收到成功 CQE 后复核 producer、Task id、Context sequence、lifecycle 和内容 hash，随后才能接纳该结果。

private Context 达到配置的高水位时，Nexus 生成结构化摘要，记录当前目标、完成工作、工具、修改文件与 revision、build、测试、错误、待办和下一步计划。workflow 共享摘要保存任务图、Agent 配置、公共 Artifact、已合并 revision、build id、测试结果、冲突和未完成 Task。摘要保留原 sequence、Artifact handle、Task id、producer、revision 和 hash；仍在运行的 Task、未解决错误、未合并修改以及其他 Agent 正在引用的 Artifact 不会被移除。

### 5.4 分支与回滚

`context_rollback(sequence)` 先确认目标仍在 FIFO 窗口中，并能沿父记录重建活动路径，再从工作流生命周期取得新的分支 generation。目标记录成为新的可见头；原有记录的序号和哈希都不变，后续结果从新分支继续 commit，序号不会复用。

回滚只改变 Agent 接下来看到的决策 Context。此前已经完成的文件写入、IPC 和工具副作用仍然存在，需要由应用另行执行补偿动作。当前分支独占且失去引用的 Artifact 按引用计数和 lifecycle 延迟回收；已经被父任务接纳、被其他 Agent 引用或已经修改共享工作区的结果继续保留。文件协作依靠 revision 检查、原子替换和补偿 Task 处理冲突。`context_clear()` 会分配新分支，清空公开记录和路径索引，并把本地序号重新置零。工作流记录票号仍然递增；清空操作与普通 commit 使用相同的发布序号规则。

## 六、Provenance 传播

AgentOS 使用六种 provenance label 表示数据来源，定义见 [`include/agent_provenance_abi.h`](../../include/agent_provenance_abi.h)：

| 标记 | 数据来源 |
| --- | --- |
| `KERNEL_FACT` | 内核直接观测 |
| `TRUSTED_USER_CONTROL` | 用户在控制接口中明确作出的决定 |
| `AGENT_DERIVED` | Agent 计算或汇总的结果 |
| `UNTRUSTED_FILE_DATA` | 文件内容或元数据派生的数据 |
| `UNTRUSTED_TOOL_OUTPUT` | 工具输出 |
| `CROSS_AGENT_DATA` | 跨 Agent 消息、delegated task artifact 或结果 |

provenance label 跟随 Context、文件读取、工具 terminal state 和 IPC 传播，合并时只增不减。选择 Execution Contract 前驱时，可以加入前置记录已有的 label，但不能删除当前 label。Tool Registry 声明允许的输入 label、输出 label、所需能力位和实际副作用掩码。[`agent_provenance_authorize_tool()`](../../os/agent_provenance.c) 在工具生效前核对生命周期、Contract generation、DAG 边、provenance 集合和副作用掩码。拒绝结果写入工作流关键记录通道。

这些 label 便于内核快速判断数据类别，并不描述自然语言事实之间的细节。具体含义仍由 Context 记录、artifact 和上层应用保存。

## 七、从创建到退出

一条典型路径如下：

1. 可信引导进程调用 `agent_workflow_create()`；[`agent_workflow_create_proc()`](../../os/proc.c) 检查引导进程身份和资源管理能力位，并创建新的 VFS 文件访问范围。
2. VFS 创建动态文件访问范围和生命周期键，资源控制器建立执行/存储账户。
3. 根 Agent 取得角色、能力位和 `control_id`，内核分配 Context、sidecar 与冷状态页。
4. 具有 `AGENT_CAP_ORCHESTRATE` 能力位的 Agent 调用 `agent_worker_create()`；内核记录目标映像的 `dev`、`inum` 和 `incarnation`，工作进程只在 `exec` 匹配后发布受限身份。
5. 工具结果、事件、消息和 delegated task 终态按顺序 commit 到 Context，provenance label 随之传播；大段跨 Agent 结果由封存的 Context Artifact 关联。
6. 回滚取得新的分支 generation，并重建活动路径。
7. `exec` 重新计算映像绑定和能力位，`exit` 开始登记退出操作。
8. 最后一个成员和后台工作结束后，生命周期收尾程序释放 Context 与关联对象。普通 VFS 文件访问范围被回收；仍有共享引用或保留期的 Artifact 继续存在，其余对象由 lifecycle 回收。

公开调用原型集中在 [`user/include/agent.h`](../../user/include/agent.h)，状态码与工具基础 ABI 位于 [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)。

## 八、测试结果

Agent identity 和 Context 同时接受静态契约检查与 QEMU Guest 回归：

| 测试 | 主要检查项 |
| --- | --- |
| `agenttrust_ucore` | 可信 `exec`、不可修改映像、角色与映像绑定、引导进程授予角色 |
| `agentfinal_ucore` | 前 6 页只读、commit lane、快照、回滚、新分支、FIFO 淘汰、直接查询 |
| `agentscope_ucore` | 工作流文件访问范围隔离、控制进程退出后撤销权限、生命周期回收 |
| Context 原子性脚本 | Context terminal record、工作流记录票号、并发快照读取、等待发布 |
| 工作流系统调用并发检查 | 普通操作、关闭与 fence 的并发关系 |

Guest 输出校验器保留的代表性标记包括 `context_commit_lane=1 sequence=1..3 hash=1`、`context_rollback_branch=1 sequence_reuse=0 provenance_bound=1`、`fifo oldest=66 latest=193 dropped=65` 和 `lifecycle_reclamation=1`。运行命令如下：

```bash
python3 -B scripts/test-exec-image-policy.py
python3 -B scripts/test-context-snapshot-reader-atomicity.py
python3 -B scripts/test-workflow-syscall-cut.py

AGENT_TEST_CASE=agenttrust_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make local-host-selftests
AGENT_TEST_CASE=agentfinal_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentscope_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

`agentfinal_ucore` 连续提交并直接读取 6 轮 Context，系统调用快照与映射页内容保持一致；继续写入 133 条记录后，公开区稳定保留最近 128 条并按 FIFO 淘汰旧记录，没有出现 OOM。这个结果说明地址空间划分确实减少了读取 Context 时的 syscall 往返，同时固定容量和发布序号也能支撑持续运行的 Agent Loop。回滚测试还确认旧序号不会被新分支复用，但它只改变后续可见的上下文路径，不会把已经发生的文件或工具副作用倒退。

这组 Agent identity 与 Context 信息还会供[工具执行](tool-execution.md)检查请求、Execution Contract、provenance 和资源，并供 [Live Query](live-query.md)判断文件属于哪一次工作流。
