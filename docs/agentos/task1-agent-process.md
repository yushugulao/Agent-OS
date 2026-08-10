# 任务一：Agent 进程、地址空间与 workflow 域

本文展开任务一当前实现。设计重点不是新增一个可伪造的“Agent 标志”，而是把可信映像、角色、capability、workflow generation、Context 映射和资源账户一起发布。

## 1. 身份建立

Agent 身份只能通过内核受控创建/exec 路径建立。可信映像注册给出允许的 role 与 capability 上限；调用者请求还必须受父身份、目标 workflow 和 VFS profile 衰减。普通进程直接执行同名文件不会获得 Agent 身份。

内核身份包含：

- Agent id、role、control id；
- capability mask；
- 动态 VFS scope；
- workflow lifecycle `id + generation`；
- exec resource account handle；
- Context Path 和当前 span/cause/branch。

PID、role 数值或用户态结构体都不是凭据。

## 2. 地址空间

Agent 地址空间在 trapframe 下方保留 7 页 Context 区域：

- 6 页由内核维护，包含 header、latest response、record ring 与因果归因状态；
- 1 页为用户态 cache，不参与可信 Context/evidence；
- 用户映射只能按 ABI 读取内核发布的可信区域，修改通过 syscall 进入校验路径。

Context record 包含 sequence、request id、cause/span、branch、parent、tool/status、数值字段、短 payload/result 和 hash 链字段。用户不能用非零 cause/span 自铸跨 Agent 因果关系。

## 3. lifecycle 状态

workflow 使用固定容量 slot，但身份始终携带 generation。每条 lifecycle 的核心状态为：

```text
scope + generation + controller_control_id
members + closing
active_operations + departing_operations + fence_gate
```

创建时首个 controller 占一个 member。受控 Agent/worker join 增加 member；exit/exec 离开时减少。显式 close 置 `closing`，新 join 和普通 operation 随即被拒绝。最后成员离开也自动置 closing。

`retiring()` 仅是 `closing && members == 0` 的内部回收谓词。当前设计没有对外多阶段 retirement 状态机，也没有为每种资源分别推进 workflow retirement phase。

## 4. gate 与 cut

| gate | 进入者 | 退出条件 |
| --- | --- | --- |
| operation | tool、fork、metadata 等普通 workflow 操作 | 操作完成；closing/fence 后不能新进入 |
| departure | exit、controller departure 等释放动作 | 资源离开完成 |
| fence | controller 的 workflow fence | 当前 operation/departure 为 0；未清空时返回 `RETRY` |

fence gate 不在持有自旋/IRQ 临界区时睡眠。成功 cut 后才发布新的 fence sequence；失败 cut 释放 gate，不改变公开 evidence root。

回收要求 member、operation、departure 和 fence 全部 quiescent。之后按同一 generation 清除 live-query watch/pending、Evidence Ring、scope 与资源账户，最后 slot 才可由新 generation 复用。

## 5. Workflow Credit Domain

每个进程使用 exec account，文件/块/inode/buffer 等由 workflow storage account 计量。资源控制器把每类资源分为 `used/pending/free`：

- 建立短 reservation 时 `F -> P`；
- 对象发布时 `P -> U`；
- 取消时 `P -> F`；
- 对象真正死亡后 `U -> F`；
- context switch 离开 workflow、压力、close/advance 或 fence 才 trim `F` 到全局。

全局和账户硬准入按 `U+P+F` 检查，因此批量预充不允许超卖。workflow fence 同时 trim exec/storage，要求所有 P 为 0，再把精确 U 和 account generation 绑定到 credit digest。

## 6. fork、exec、worker 与 fd

- fork 进入 workflow operation gate，先完成 VM/资源准入，再发布 child member；失败按相反顺序撤销。
- 普通 fork 不自动扩大 capability；跨 scope inode fd 会撤销。
- worker 委派绑定目标映像的 `dev + inum + incarnation` 和请求 capability，执行错误映像会清除 pending 委派。
- exec 更换身份前清理 watch、等待、Context 归因和旧 generation 资源；新身份只有在全部前置检查通过后发布。
- exit 使用 departure gate，先停止新操作，再释放对象、member 和账户；最后成员触发 finalizer eligibility。

## 7. 自身状态查询

`agent_workflow_lifecycle_info()` 只返回调用者自己的 lifecycle key、Context lane/metadata transaction 统计和 resource account handle。该结构不是可转让 token，另一进程不能凭它 join、close 或 fence。

## 8. 已知边界

- 内核仍为单 Hart；Host 并行运行多个 QEMU lane 不代表 Guest SMP。
- active workflow 与 lifecycle slot 数量有界；容量不足时 fail closed。
- generation 绑定当前启动周期，用于阻止 slot、PID 或 workflow id 复用命中旧身份。
- closing workflow 不接受新普通操作；用户态应在 close 前完成所需的显式 fence。

## 9. 验证入口

```bash
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
make agent-module-check TOOLPREFIX=riscv-none-elf-
```

动态 fork/exec/exit、scope 与资源行为由 AgentOS Guest 专项直接验证；运行入口见 [verification.md](verification.md)。
