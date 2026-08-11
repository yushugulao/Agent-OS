# 任务一：Agent 进程与 workflow 域

## 场景与约束

普通进程可以伪造 PID、role 数值和用户态结构，因此这些字段不能充当 Agent 凭据。Agent 创建、fork、exec 和 exit 还会同时改变地址空间、文件可见范围、workflow 成员和资源归属，任何半完成状态都会留下越权或复用窗口。

任务一把可信身份、Context 映射、workflow lifecycle 和资源账户作为同一次受控状态转换处理。

## 方案

### 可信身份

内核只在受控 create/exec 路径发布 Agent 身份。可信映像 profile 给出 role 与 capability 上限，父身份、目标 workflow 和 VFS scope 继续收紧权限。最终身份包含：

- Agent id、role、control id 与 capability mask；
- 动态 VFS scope；
- workflow lifecycle `id + generation`；
- exec/storage resource account；
- 当前 Context cause、span、branch 与 provenance 状态。

普通进程执行同名文件不会获得这些字段。worker 委派还绑定目标映像的 `dev + inum + incarnation`，exec 到其他映像会撤销待发布身份。

### 地址空间

Agent 在 trapframe 下方固定映射 7 页 Context：

| 区域 | 权限 | 用途 |
| --- | --- | --- |
| 前 6 页 | 内核写、用户读 | header、latest response、record ring 和可信因果状态 |
| 第 7 页 | 用户读写 | Guest 自管 cache，不参与授权、scope 或 evidence |

用户通过 syscall 修改可信 Context。直接读 helper 使用 publication sequence 检查一致性，避免读取到半发布记录。

### lifecycle 与 cut

每个 workflow 以完整 generation 区分复用后的 slot。状态机维护 `members`、`closing`、controller 和三类 gate：

| gate | 保护的操作 | 规则 |
| --- | --- | --- |
| operation | tool、fork、metadata 等普通动作 | closing 或 fence 后停止新进入 |
| departure | exit、controller departure 与资源释放 | 释放完成后退出 |
| fence | controller 的可验证 cut | operation/departure 未清空时返回 `RETRY` |

最后一个成员离开后，内核等待所有 gate 静止，再按同一 generation 清理订阅、Evidence Ring、scope 和资源账户。slot 最后才进入复用。

### Workflow Credit Domain

exec 与 storage 账户把每类资源分为 used、pending 和 free credit：

```text
reserve: F -> P
publish: P -> U
cancel:  P -> F
destroy: U -> F
```

硬准入按 `U + P + F` 计算，批量预充不能超卖。context switch、压力、close/advance 和 fence 会把本账户多余 F 归还全局。fence 要求 P 为 0，并把精确 U、账户 generation 和 digest 写入 receipt。

## 关键实现

| 职责 | 源码 |
| --- | --- |
| 身份建立与 role/capability | [os/agent_identity.c](../../os/agent_identity.c)、[os/agent_core.c](../../os/agent_core.c) |
| lifecycle、成员和 gate | [os/workflow_lifecycle.c](../../os/workflow_lifecycle.c)、[os/agent_lifecycle.c](../../os/agent_lifecycle.c) |
| fork/exec/exit 状态转换 | [os/proc.c](../../os/proc.c)、[os/vm.c](../../os/vm.c) |
| VFS scope 与映像约束 | [os/vfs_security.c](../../os/vfs_security.c)、[os/exec_policy.c](../../os/exec_policy.c) |
| U/P/F 资源账户 | [os/workflow_credit_domain.c](../../os/workflow_credit_domain.c)、[os/resource_controller.c](../../os/resource_controller.c) |
| 公开布局 | [user/include/agent.h](../../user/include/agent.h)、[agent_lifecycle_abi.h](../../agent_lifecycle_abi.h) |

`agent_workflow_lifecycle_info()` 返回调用者自身的 lifecycle key，以及 Context、metadata、resource account 和 workflow EEVDF 快照。这些字段是只读状态，不构成 join、close、fence 或资源授权凭据。

## 验证

| 证据 | 覆盖范围 |
| --- | --- |
| `agenttrust_ucore` | 普通进程伪装、映像和身份衰减 |
| `agentfinal_ucore` | Context 映射、fork/exec/exit 和资源转移 |
| `agentscope_ucore` | workflow/scope 隔离和跨域拒绝 |
| `test-workflow-credit-domain.py` | U/P/F 准入、结算和 generation |
| `test-workflow-fence.py`、`test-workflow-syscall-cut.py` | gate、quiescent cut 和失败不发布 |

```bash
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
AGENT_TEST_CASE=agenttrust_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

## 当前边界

- Context 总计 7 页，可信区固定为前 6 页。
- 生产 lifecycle 同时最多保留 4 个 active workflow，容量耗尽时拒绝新建。
- generation 只覆盖当前启动周期；重启后不会恢复旧身份或 receipt。
- Guest 内核为单 Hart，调度并发结论不能外推到 SMP。
- closing workflow 停止新普通操作；controller 应在 close 前完成需要的 fence。
