# 智能体身份、生命周期与上下文

一次工作流可能由多个受控进程共同完成。智能体身份说明当前由谁执行，生命周期说明进程属于哪一次运行，上下文则记录每一步用了什么信息、产生了什么结果。这三类状态共同为工具、文件、事件、资源和调度提供可靠的归属信息。

## 文档索引

- [一、模块目标](#一模块目标)
- [二、智能体身份](#二智能体身份)
- [三、工作流生命周期](#三工作流生命周期)
- [四、上下文地址空间](#四上下文地址空间)
- [五、提交、读取与回滚](#五提交读取与回滚)
- [六、数据来源的传播](#六数据来源的传播)
- [七、从创建到退出](#七从创建到退出)
- [八、测试结果](#八测试结果)

## 一、模块目标

PID 只能指出当前进程，无法说明进程是否从可信映像启动、担任什么角色、由谁控制、属于哪次工作流。进程槽位再次使用后，单靠 PID 也无法挡住旧句柄访问新进程。与此同时，多轮任务的上下文既要便于用户态读取，也要保留因果关系、支持分支回滚，并在进程退出后及时回收。

为此，我们在 uCore 的 `struct proc` 中保存智能体身份和工作流键，在 VFS 中保存动态文件访问范围与映像绑定，并将上下文映射到固定虚拟地址。生命周期记录分别统计创建、普通操作和退出，生成阶段快照时暂停接纳新操作。主要实现位于 [`os/agent_identity.c`](../../os/agent_identity.c)、[`os/workflow_lifecycle.c`](../../os/workflow_lifecycle.c)、[`os/vfs_security.c`](../../os/vfs_security.c) 和 [`os/agent_context.c`](../../os/agent_context.c)。

## 二、智能体身份

### 2.1 身份字段

受控创建完成后，智能体身份直接附着在 uCore 进程对象上：

| 字段组 | 内容 | 作用 |
| --- | --- | --- |
| 智能体身份 | `agent_id`、`role`、`control_id`、`controller_id` | 标识执行者并保存控制关系 |
| 工作流身份 | `workflow_lifecycle_id`、`generation` | 隔开槽位复用前后的两次运行 |
| 权限 | `agent_capability_mask`、VFS 中的有效能力与可继承能力 | 检查工具调用和文件操作 |
| 文件范围 | `vfs_scope_id`、存储主体、绑定映像身份 | 限制工作流文件和可执行映像 |
| 运行状态 | 循环状态、心跳、上下文、事件队列、资源账户 | 供等待、观测、资源和调度模块使用 |

`agent_id` 由内核分配。`control_id` 只用于判断控制关系，用户态不能把它转交给其他进程。跨模块的长期引用都使用完整生命周期键：

```c
struct workflow_lifecycle_key {
    uint id;
    uint64 generation;
};
```

槽位编号 `id` 的范围为 `1..WORKFLOW_LIFECYCLE_CAP`。系统当前最多允许 4 个工作流同时运行，生命周期表共有 8 个槽位，用来容纳正在运行和正在回收的记录。空槽再次使用时，`generation` 会递增。查找函数同时比较 `used` 和 `generation`。定义见 [`os/workflow_lifecycle.h`](../../os/workflow_lifecycle.h)。

### 2.2 角色与能力位

角色决定能力上限，也给出同一工作流内的初始调度权重。策略定义在 [`os/agent_identity.c`](../../os/agent_identity.c)：

| 角色 | 主要能力 | 智能体调度权重 |
| --- | --- | ---: |
| `Sentinel` | 读取元数据和进程状态、发送消息、订阅文件、写审计记录 | 70 |
| `Investigator` | 读取元数据和文件内容、发送消息、订阅文件、写审计记录 | 90 |
| `Recovery` | 读取元数据和文件内容、发送消息、订阅文件、执行动作、写结果和审计记录 | 120 |
| `Artifact` | 读取元数据和文件内容、发送消息、订阅文件、写结果和审计记录 | 100 |
| `Orchestrator` | 13 项智能体能力，可创建并管理协作者 | 110 |

表中的权重只用于同一工作流内部选择智能体。多个工作流之间的 EEVDF 调度对象保持等权。子进程或 `exec` 都不能扩大权限。进程最终拥有的能力，是角色策略、映像配置、父进程现有能力和请求掩码的交集。

### 2.3 受控创建与映像绑定

AgentOS 提供三条受控创建路径：

| 入口 | 调用者 | 创建结果 |
| --- | --- | --- |
| `agent_workflow_create(role)` | 可信引导进程、资源管理员 | 新建动态文件访问范围、生命周期、资源账户和根智能体 |
| `agent_create_role(role)` | 同一工作流中可以授予角色的智能体 | 创建同一工作流内的智能体子进程 |
| `agent_worker_create(image, caps)` | 同一工作流中具有 `AGENT_CAP_ORCHESTRATE` 的智能体 | 创建等待 `exec` 的工作进程。`caps` 必须非零，且只能从调用者已有的 `CONTENT_READ | ARTIFACT_WRITE` 能力中选择 |

`agent_worker_create()` 先检查申请的能力位不为空，也没有超出 `AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE` 和调用者已有能力。随后，内核在系统访问范围中查找目标 inode，检查执行权限以及映像是否不可修改、是否允许跨资源账户使用，再把 `dev + inum + incarnation` 写入子进程的待发布身份。真正执行 `exec` 时，[`vfs_proc_exec_prepare()`](../../os/vfs_security.c) 会用当前映像重新核对这三个字段，并把申请能力与映像能力上限取交集。映像内容变化后，inode 实例代次不再匹配，进程会降为普通身份，受控身份不会发布到新映像上。

发布 `fork` 子进程时，[`agent_identity_spawn_publish_locked()`](../../os/agent_identity.c) 在同一次关中断期间核对父子生命周期并写入控制关系。控制进程退出时依次经过 `OPEN -> QUIESCING -> RETIRED`。可以交接的子控制关系转给有效继任者，其余关系随原控制进程一起撤销。

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

`members` 是仍持有该生命周期的进程数。`active_operations` 统计正在执行的普通操作，`departing_operations` 统计进程退出和异步释放。`fence_gate` 在生成阶段快照时阻止前两类操作进入。实现见 [`os/workflow_lifecycle.c`](../../os/workflow_lifecycle.c)。

### 3.2 操作计数与阶段快照

| 操作类型 | 涉及操作 | 开始条件 | 完成条件 |
| --- | --- | --- | --- |
| 普通操作 | 工具调用、`fork`、元数据更新等 | 生命周期正在运行，尚未关闭，也没有生成阶段快照 | 操作最终状态已经提交，临时资源已经释放 |
| 退出操作 | `exit`、控制进程退出、异步清理 | 成员数仍大于 0，也没有生成阶段快照 | 本阶段清理完成 |
| 阶段快照 | 控制进程生成阶段快照 | 生命周期尚未关闭，普通操作与退出操作都为 0 | 回执已提交或本次快照中止 |

普通操作计数的实现很短，可以直接放进系统调用和进程创建路径：

```c
record = workflow_lifecycle_find_locked(key);
if (record != 0 && !record->closing && record->members > 0 &&
    !record->fence_gate && record->active_operations != (uint)-1) {
    record->active_operations++;
    result = 0;
}
```

关闭工作流时，内核将 `closing` 置位，不再接纳新的普通操作。最后一个成员离开后，`members` 变为 0，生命周期开始回收。普通操作、退出操作和阶段快照全部结束后，`workflow_lifecycle_reclaim()` 才清空槽位。下一次工作流即使取得相同 `id`，也会使用新的 `generation`。

### 3.3 按生命周期回收

生命周期收尾程序按同一个键回收文件订阅、元数据目录、执行约定、任务通道、执行记录环、执行资源账户和调度对象。随后回收普通 VFS 访问范围，删除其中的文件，并从登记表中移除相应记录。标记 `preserve_on_retire` 的持久输出会保留登记信息、文件和存储费用，只解除与旧生命周期的绑定。各模块在释放前都比较完整键，因此旧订阅、请求、约定和句柄只会得到 `STALE`、`CONFLICT` 或 `NOT_FOUND`，不会落入下一次工作流。

进程退出可能跨越一次系统调用，因而全程计入退出操作。普通智能体系统调用计入普通操作。阶段快照不会持锁睡眠，暂时无法取得静止状态时返回 `RETRY`。

## 四、上下文地址空间

### 4.1 映射布局

每个智能体的上下文位于 trapframe 下方固定地址 `AGENT_CONTEXT_BASE`。布局定义在 [`os/agent.h`](../../os/agent.h)：

| 区域 | 页数 | 用户权限 | 内容 |
| --- | ---: | --- | --- |
| 公开页头与最近响应 | 第 1 页 | 只读 | 魔数、版本、记录窗口、当前分支头、最近响应、发布序号 |
| 记录环 | 第 2-6 页 | 只读 | 128 条 `agent_context_record` |
| 客户机缓存 | 第 7 页 | 读写 | 用户态保存的派生数据 |
| 内核附加区 | 9 页 | 不映射 | 规范化操作与结果、执行者、起因、路径索引和来源状态 |
| IPC 与观测冷数据 | 1 页 | 不映射 | 事件、订阅、消息路由和观测数据 |

[`agent_context_map()`](../../os/agent_context.c) 为前 6 页设置 `PTE_R | PTE_U`，只给第 7 页增加 `PTE_W`。`exec` 保留智能体身份时，[`agent_alias_exec_context()`](../../os/agent_context.c) 按原权限把同一组物理页映射进新页表。身份降为普通进程后，内核不再映射这些页面。

### 4.2 公开记录与详细记录

公开记录保存序号、请求编号、起因序号、调用跨度、分支、活动路径中的父记录、工具、状态、短载荷、短结果和哈希链。每条记录大小固定，用户态可以直接遍历。同一槽位的内核附加区保存定长、规范化的 `agent_op`、`agent_result` 和归因字段。`context_detail(sequence)` 可以读取这些信息。V2 的完整参数数组、V3 绑定和 Task SQE 不会原样保存在详细记录中。

| 字段 | 含义 |
| --- | --- |
| `sequence` | 同一次清空周期内递增的记录号。被定长队列淘汰后不复用，`context_clear()` 后从 1 重新开始 |
| `cause_sequence`、`span_id` | 当前动作的直接原因和跨模块调用跨度 |
| `branch_generation`、`path_parent_sequence` | 回滚后新分支的代次，以及活动路径中的前一条记录 |
| `prev_hash`、`record_hash` | 按写入顺序形成的哈希链 |
| `flags` | 系统或手工记录、截断标记、来源标记和安全拒绝 |

上下文固定保存 128 条记录。写满后覆盖最旧槽位，并同步更新页头中的 `oldest_sequence`、`latest_sequence` 和 `dropped_records`。

## 五、提交、读取与回滚

### 5.1 顺序提交通道

工具结果、系统事件、手工记录、回滚和清空共用每个进程的上下文提交通道。追加记录时，内核依次完成：

```text
预检记录序号、调用跨度和当前写入者
  -> 写入内核附加区的详细记录
  -> 把发布序号改为奇数
  -> 写入公开记录，更新路径索引和内核计数
  -> 写入最近响应和页头
  -> 对预留的最终状态记录提交执行记录槽位
  -> 把发布序号恢复为偶数
```

[`agent_context_publish_begin()`](../../os/agent_context.c) 通过带获取与释放语义的原子加法，把发布序号从偶数改为奇数。`agent_context_publish_end()` 再用释放语义恢复偶数。预留最终状态记录后，发布序号会一直保持为奇数，直到对应执行记录也提交完成。直接读取者因此不会看到只有上下文、没有执行记录的中间状态。手工追加不预留执行记录。回滚和清空只更新活动路径或重置上下文，不增加新记录。

### 5.2 读取接口

| 接口 | 读取方法 | 用途 |
| --- | --- | --- |
| `context_direct_header_snapshot()` | 直接读取映射页，前后比较发布序号 | 高频读取页头 |
| `context_direct_active_query()` | 直接读取活动路径，前后比较发布序号 | 客户机常用查询 |
| `context_query()` | 通过系统调用复制活动路径 | 直接读取连续发生冲突时使用的备用读取路径 |
| `context_snapshot()` | 通过系统调用同时返回页头和记录 | 调试与完整快照 |
| `context_detail()` | 通过系统调用读取规范化的 `agent_op` 和 `agent_result` | 查看单条记录详情 |

客户机直接读取的实现位于 [`user/lib/syscall.c`](../../user/lib/syscall.c)。只有前后两次发布序号相同且为偶数时，本次读取才有效。若写入持续发生，接口返回重试，调用方改用系统调用读取。

### 5.3 分支与回滚

`context_rollback(sequence)` 先确认目标仍在定长记录窗口中，并能沿父记录重建活动路径，再从工作流生命周期取得新的分支代次。目标记录成为新的可见头。原有记录的序号和哈希都不变，后续结果从新分支继续写入，记录序号不会复用。

回滚只改变智能体接下来看到的决策上下文。此前已经完成的文件写入、IPC 和工具操作仍然存在，需要由应用另行执行补偿动作。`context_clear()` 会分配新分支，清空公开记录和路径索引，并把本地记录序号重新置零。工作流执行记录环中的编号仍然递增。清空与普通提交使用相同的发布序号规则。

## 六、数据来源的传播

AgentOS 使用六种固定标记表示数据来自哪里，定义见 [`include/agent_provenance_abi.h`](../../include/agent_provenance_abi.h)：

| 标记 | 数据来源 |
| --- | --- |
| `KERNEL_FACT` | 内核直接观测 |
| `TRUSTED_USER_CONTROL` | 用户在控制接口中明确作出的决定 |
| `AGENT_DERIVED` | 智能体计算或汇总的结果 |
| `UNTRUSTED_FILE_DATA` | 文件内容或元数据派生的数据 |
| `UNTRUSTED_TOOL_OUTPUT` | 工具输出 |
| `CROSS_AGENT_DATA` | 跨智能体消息或结果 |

标记跟随上下文、文件读取、工具最终状态和 IPC 传播，合并时只增不减。选择执行约定的前置节点时，可以加入前置记录已有的标记，但不能删除当前标记。工具清单声明可以接受哪些来源、输出会增加哪些来源、需要什么能力位，以及实际会改动哪些系统对象。[`agent_provenance_authorize_tool()`](../../os/agent_provenance.c) 在工具生效前核对生命周期、约定代次、任务边、来源集合和改动范围。拒绝结果写入执行记录环的关键分区。

这些标记便于内核快速判断数据类别，并不描述自然语言事实之间的细节。具体含义仍由上下文记录、结果文件和上层应用保存。

## 七、从创建到退出

一条典型路径如下：

1. 可信引导进程调用 `agent_workflow_create()`。随后，[`agent_workflow_create_proc()`](../../os/proc.c) 检查引导身份和资源管理权限，并创建新的文件访问范围。
2. VFS 创建动态访问范围和生命周期键，资源控制器建立执行账户与存储账户。
3. 根智能体取得角色、能力位和 `control_id`，内核分配上下文、附加区与冷数据页面。
4. 具有 `AGENT_CAP_ORCHESTRATE` 能力位的智能体调用 `agent_worker_create()`。内核记录目标映像的 `dev`、`inum` 和 `incarnation`，工作进程只在 `exec` 匹配后发布受限身份。
5. 工具结果、事件和消息按顺序写入上下文，来源标记随之传播。
6. 回滚取得新分支代次，并重建活动路径。
7. `exec` 重新计算映像绑定和能力位，`exit` 开始登记退出操作。
8. 最后一个成员和后台工作结束后，生命周期收尾程序释放上下文与关联对象。普通文件访问范围被回收；持久输出对应的文件继续保留，并解除与旧生命周期的绑定。

公开调用原型集中在 [`user/include/agent.h`](../../user/include/agent.h)，状态码与工具基础 ABI 位于 [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)。

## 八、测试结果

智能体身份和上下文同时接受静态契约检查与 QEMU 客户机回归：

| 测试 | 主要检查项 |
| --- | --- |
| `agenttrust_ucore` | 可信 `exec`、不可修改映像、角色与映像绑定、引导进程授予角色 |
| `agentfinal_ucore` | 前 6 页只读、顺序提交、快照、回滚、新分支、定长队列淘汰、直接查询 |
| `agentscope_ucore` | 工作流访问范围隔离、控制进程退出后撤销权限、生命周期回收 |
| 上下文原子性脚本 | 最终上下文记录与执行记录、并发快照读取、等待发布 |
| 工作流系统调用并发检查 | 普通操作与关闭、阶段快照之间的并发关系 |

客户机输出校验器保留的代表性标记包括 `context_commit_lane=1 sequence=1..3 hash=1`、`context_rollback_branch=1 sequence_reuse=0 provenance_bound=1`、`fifo oldest=66 latest=193 dropped=65` 和 `lifecycle_reclamation=1`。运行命令如下：

```bash
python3 -B scripts/test-exec-image-policy.py
python3 -B scripts/test-context-evidence-atomicity.py
python3 -B scripts/test-context-snapshot-reader-atomicity.py
python3 -B scripts/test-workflow-syscall-cut.py

AGENT_TEST_CASE=agenttrust_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentfinal_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentscope_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

这组身份和上下文信息还会供[工具执行](tool-execution.md)检查请求、执行约定、数据来源和资源，并供[文件实时查询](live-query.md)判断文件属于哪一次工作流。
