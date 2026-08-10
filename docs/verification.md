# 构建与产品验证

本文说明如何验证 plain uCore 对照目标与 AgentOS-uCore 增强目标。验证只保留能发现产品问题的四类工作：构建、功能、安全和性能测试，不再叠加与产品行为无关的发布门。

## 1. 验证原则

1. 静态检查用于尽早发现 ABI、模块边界和栈安全问题，不替代实际运行。
2. QEMU Guest 测试验证真实 RISC-V 内核路径，是功能与安全回归的主要依据。
3. 性能结论必须来自实际负载的计数或测量，不从代码行数、公式或固定常量推导。
4. plain/AgentOS 双目标只回答完整系统路径的差异；单个机制的贡献要用同内核消融或专项 benchmark 验证。

内核中的 `Fence-Sealed Evidence Ring` 是产品安全机制。它记录当前启动周期内的有序事件，并由 workflow fence 生成 challenge-bound root。它不是 Host 发布材料，也不要求仓库生成 manifest 或文件校验包。

## 2. 环境检查

Windows：

```powershell
.\scripts\check-windows-prereqs.ps1
```

Linux、WSL 或项目工具链环境：

```bash
make doctor
```

默认示例使用 `riscv-none-elf-`。本机使用其他前缀时通过 `TOOLPREFIX` 显式传入。

## 3. 构建与快速检查

```bash
make build TOOLPREFIX=riscv-none-elf-
make agent-uapi-check TOOLPREFIX=riscv-none-elf-
make agent-module-check TOOLPREFIX=riscv-none-elf-
make kernel-stack-check TOOLPREFIX=riscv-none-elf-
```

这些命令分别发现编译/链接错误、内核与用户态 ABI 漂移、生产模块依赖错误以及真实调用图上的栈超限。它们不对源码行数、工具可执行文件哈希或发布工件做门禁。

核心状态机的 Host 测试可以单独运行：

```bash
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-agent-live-query-fs.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
python -B scripts/test-agent-execution-contract.py
python -B scripts/test-agent-task-channel.py
python -B host_tools/test_workflow_scheduler_model.py
python -B host_tools/test_agent_task_transport.py
python -B host_tools/test_mcp_a2a_gateway.py
```

这些模型和变异测试适合快速定位不变量问题；改动涉及 syscall、调度、VFS、并发或设备路径时，仍应继续运行 QEMU Guest。

## 4. QEMU 功能测试

完整 AgentOS Guest 回归：

```bash
make agentos-test TOOLPREFIX=riscv-none-elf-
```

迭代时可以只运行与改动最相关的程序：

```bash
AGENT_TEST_CASE=agentfinal_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentfs_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentloop_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

功能套件覆盖 Agent 身份与 lifecycle、tool call、Context push/query/rollback、显式 metadata 与 live query、IPC/wait、workflow fence、执行合同、Task SQ/CQ、资源回收和 teardown。每个程序必须自然退出，并由 runner 检查预期 marker、panic、超时和退出状态；单纯打印 `passed` 不能绕过这些检查。

## 5. 安全测试

下面的 Guest 程序直接验证权限边界和拒绝路径：

```bash
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agenttrust_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentscope_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=usersafety_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

重点检查：

- 非可信映像不能取得 Agent 身份或提升 capability；
- lifecycle、scope、generation 和 owner 不匹配时 fail closed；
- 计划外工具调用在副作用前被执行合同与 provenance gate 拒绝；
- 关键拒绝写入内核 Evidence Ring，critical 区不足时受保护操作不继续；
- 共享 SQ 页按 copy-before-validate 处理，stale handle 和重复 completion 不产生双终态；
- `PERSIST/AUTOSCAN` 兼容标志和 observe recovery tombstone 返回 `BAD_PARAM`；
- 用户指针、VFS 对象与资源配额的失败路径不留下半提交状态。

文件系统、资源与并发故障还可以运行：

```bash
make fs-enospc-test TOOLPREFIX=riscv-none-elf-
make fs-allocator-fault-test TOOLPREFIX=riscv-none-elf-
make workflow-teardown-race-test TOOLPREFIX=riscv-none-elf-
make virtio-disk-test TOOLPREFIX=riscv-none-elf-
```

## 6. 性能测试

性能测试直接运行产品负载，不要求先生成测量 receipt 或打包结果：

```bash
AGENT_TEST_CASE=agentbench_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentsched_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

读取结果时应同时记录工作量、样本数、单位和失败样本：

- scalar/batch/Task SQ-CQ 的调用点、ABI 复制记账与 Context service-start tick；
- scan/index 的候选量、扫描量和 query ticks；
- EEVDF 的 Jain fairness、wakeup p50/p99、deadline miss 和普通进程进展；
- Credit Domain 的 reserve/trim/fence 次数与资源峰值；
- 完整科研负载的完成状态和端到端耗时。

固定 ABI 字节数是结构记账，不是实测内存流量；Guest tick 不是 Host wall clock；完整系统差异也不能自动归因给某一个内核机制。

## 7. plain 与 AgentOS 双目标

```bash
make plain-platform-build TOOLPREFIX=riscv-none-elf-
make agentos-platform-build TOOLPREFIX=riscv-none-elf-
make dual-platform-run TOOLPREFIX=riscv-none-elf-
```

`baseline_ucore/` 是共享安全修复但不包含 AgentOS 子系统的对照目标，不是未经修改的上游镜像。两侧运行同一用户态科研 action/state 合同，Host 从各自文件系统镜像和 Guest 日志提取规范状态并比较。

复核一次双目标运行时，至少查看两侧退出状态、Guest 日志、状态字段一致性、缺失文件和比较摘要。调试重跑是正常开发行为，不需要维护发布次数账本；引用性能数字时只需清楚标明本次实际运行的环境与负载。

## 8. Workflow fence 边界

fence 通过 `agent_workflow_fence()`，即 `agent_run(count=0, AGENT_RUN_F_FENCE)` 发起。相关 Guest/Host 测试检查 request、controller 权限、lifecycle key、320 字节 receipt、exact credit cut、event/gap 范围、root 链以及 retry 稳定性。

receipt 只描述当前运行期 cut，不表示：

- Evidence 已持久化到磁盘；
- metadata catalog 可在重启后恢复；
- 普通文件内容已进入 root；
- 所有 scheduler 或 Host 行为都被覆盖；
- receipt 已由外部密钥签名。

## 9. 清理

```bash
make clean-workspace-dry-run
make clean-workspace
```

`clean-workspace-dry-run` 先显示将清理的构建和运行输出。测试日志可以按排障需要保留或删除。
