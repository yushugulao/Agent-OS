# 身份、生命周期与 Context

AgentOS 以 workflow lifecycle 组织 Agent 的创建、委派、执行和退出。身份保存在内核进程对象中，Context 保存多轮执行的因果关系，两者共同使用 `{workflow id, generation}` 隔离前后两次运行。

## Agent 身份

内核通过受控 create/exec 路径发布 Agent 身份。映像 profile 给出 role 与 capability 上限，父 Agent、目标 workflow 和 VFS scope 继续收紧实际权限。每个 Agent 保存以下状态：

- Agent id、controller id、role 和 capability mask；
- workflow id 与 lifecycle generation；
- 动态 VFS scope 与委派文件描述符；
- exec、storage resource account；
- 当前 cause、span、branch 与 provenance。

worker 委派绑定目标映像的 `dev + inum + incarnation`。映像对象变化后，待发布身份失效。`fork`、`exec` 和 `exit` 分别进入 AgentOS 的身份转换路径，使进程状态、地址空间、workflow 成员关系和资源归属同步更新。

## Workflow 生命周期

生命周期槽位记录 controller、members、closing 状态和三类 gate：

| Gate | 管理对象 | 状态变化 |
| --- | --- | --- |
| operation | 工具、fork、metadata 等操作 | close 或 fence 开始后停止新操作进入 |
| departure | exit、controller departure、资源释放 | 清理完成后允许成员离开 |
| fence | controller 发起的一致性切片 | operation 与 departure 清空后发布 receipt |

最后一个成员退出且 operation、departure 与后台任务排空后，内核按 generation 清理订阅、执行记录、VFS scope、Task Channel 和资源账户，再回收 lifecycle slot。旧 watch、request、合同与 handle 根据对象状态返回 `STALE`、`CONFLICT` 或 `NOT_FOUND`。

常用生命周期接口如下：

```c
int agent_workflow_create(int role);
int agent_worker_create(const char *image, uint64 capabilities);
int agent_info(struct agent_info *info);
int agent_workflow_lifecycle_info(
    struct agent_workflow_lifecycle_info *info,
    const struct agent_workflow_lifecycle_key *expected);
int agent_scope_delegate_fd(int fd);
int agent_workflow_close(uint64 scope_id);
```

## Context 地址空间

每个 Agent 在 trapframe 下方固定映射 7 页 Context：

| 区域 | 权限 | 内容 |
| --- | --- | --- |
| 前 6 页 | 内核写、用户读 | header、latest response、record ring、active path |
| 第 7 页 | 用户读写 | Guest cache |
| 内核 sidecar | 内核访问 | 完整 request/response、source identity、provenance |

记录容量为 128。每条记录包含单调 sequence、tool/status、tick、cause/span、branch、parent、短 payload/result 和 hash 关系。内核根据当前调用、事件或 IPC 路由确定 cause 与 source。

## 提交与一致读取

工具结果、手工记录、事件消费和 rollback 共用 FIFO commit lane。writer 完成范围预检、record 写入、detail 写入和 latest 更新后发布 header。奇偶 publication sequence 包围整个提交过程，reader 仅接收前后相同的偶数 sequence。

| 接口 | 返回内容 |
| --- | --- |
| `context_direct_header_snapshot()` | 固定映射中的 header 快照 |
| `context_direct_active_query()` | 固定映射中的 active path |
| `context_query()` / `context_snapshot()` | 有界 active path，以及 header 与路径的组合快照 |
| `context_detail()` | 保留窗口中的完整 request/response |
| timeline wait/read | 在一次系统调用中等待并读取新记录 |

直接读取遇到持续写入时返回重试，调用方随后使用 syscall 获取一致快照。

## 分支与回滚

`context_rollback()` 先确认目标仍在可信窗口，再创建新的 branch generation，并把目标设为 active anchor。旧记录保持原 sequence 与 hash，随 FIFO 窗口自然淘汰。已经完成的文件、IPC 和工具效果继续保留，新的 Context 分支记录后续决策。

clear、rollback 与普通提交使用相同的预检和 publication 协议。失败操作不会移动 active head 或链尾。

## 来源传播

AgentOS 使用固定标签描述输入来源：

| 标签 | 来源 |
| --- | --- |
| `KERNEL_FACT` | 内核直接观测 |
| `TRUSTED_USER_CONTROL` | 控制面绑定的用户决定 |
| `AGENT_DERIVED` | Agent 计算或汇总 |
| `UNTRUSTED_FILE_DATA` | 文件内容或 metadata 派生数据 |
| `UNTRUSTED_TOOL_OUTPUT` | 工具输出 |
| `CROSS_AGENT_DATA` | 跨 Agent 传递的数据 |

标签沿 Context、工具结果、文件读取和 IPC 采用 OR 传播。执行策略可在副作用前要求指定的标签组合，SHA-256 用于固定 payload 和 input fingerprint。

## 实现位置

| 职责 | 源码 |
| --- | --- |
| 身份建立与权限衰减 | [`os/agent_identity.c`](../../os/agent_identity.c)、[`os/agent_core.c`](../../os/agent_core.c) |
| lifecycle、成员与 gate | [`os/workflow_lifecycle.c`](../../os/workflow_lifecycle.c)、[`os/agent_lifecycle.c`](../../os/agent_lifecycle.c) |
| fork、exec、exit 转换 | [`os/proc.c`](../../os/proc.c)、[`os/vm.c`](../../os/vm.c) |
| VFS scope 与映像约束 | [`os/vfs_security.c`](../../os/vfs_security.c)、[`os/exec_policy.c`](../../os/exec_policy.c) |
| Context 存储与查询 | [`os/agent_context.c`](../../os/agent_context.c)、[`os/agent_context_path.c`](../../os/agent_context_path.c) |
| 来源与 timeline | [`os/agent_provenance.c`](../../os/agent_provenance.c)、[`os/agent_observe_timeline.c`](../../os/agent_observe_timeline.c) |

## 测试入口

`agenttrust_ucore` 检查身份、映像绑定和权限衰减；`agentfinal_ucore` 检查 Context 映射、snapshot、rollback、FIFO 淘汰及 fork/exec/exit 转换；`agentscope_ucore` 检查 workflow 与 VFS scope 隔离。

```bash
python3 -B scripts/test-context-evidence-atomicity.py
python3 -B scripts/test-context-snapshot-reader-atomicity.py
python3 -B scripts/test-workflow-syscall-cut.py
AGENT_TEST_CASE=agentfinal_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

当前配置为每个 Agent 7 页 Context、128 条记录和最多 4 个 active workflow。完整系统调用定义见 [API](../api.md)。
