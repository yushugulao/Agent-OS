# 构建与产品验证

本文说明如何验证 plain uCore 对照目标与 AgentOS-uCore 增强目标。验证只保留能发现产品问题的四类工作：构建、功能、安全和性能测试。

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

完整的 WSL/MSYS2、工具链前缀和 QEMU 说明见 [Windows 快速开始](windows-quickstart.md)。

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

赛题任务一至五的集中验收 Guest 需显式运行；它使用独立的 `agent_eval` 构建章节：

```bash
AGENT_TEST_CASE=agenteval_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
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

题面硬门槛与代表入口如下。Context 的前 6 页是内核维护、用户只读的可信区；第 7 页是用户态可直接读写但不参与授权判断的 cache，二者共同满足直接访问需求，同时避免可信历史被伪造。

| 任务 | 必须复核的门槛 | 代表 Guest / 命令 |
| --- | --- | --- |
| 1 | Agent 创建与 Context 区；普通进程和 Agent 共存 | `agenteval_ucore`、`agentfinal_ucore`、`agenttrust_ucore` |
| 2 | 至少 3 个结构化工具；发现、调用、返回与错误处理 | `agenttoolabi_ucore`、`agentcontract_ucore` |
| 3 | 至少 5 轮连续调用；直接读取；超长自动淘汰且不 OOM | `agentfinal_ucore` |
| 4 | 至少 2 类文件查询扩展；结构化结果；索引与遍历对比 | `agentfs_ucore`、`agentbench_ucore` |
| 5 | 至少 2 类 Agent Loop 机制；heartbeat、事件休眠、多 Agent 稳定 | `agentloop_ucore`、`agentsched_ucore`、`agent_eevdf_ucore` |
| 6 | 综合至少 3 个已实现模块；QEMU 程序；至少 1 组性能对比 | `make contest-demo TOOLPREFIX=riscv-none-elf-` |

逐项实现、源码和能力边界见[要求追踪表](agentos/requirements-traceability.md)。

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
- metadata 只接受显式登记并绑定当前 workflow/lifecycle/incarnation，普通目录不会自动进入 catalog；
- fence receipt 只覆盖当前启动周期的内存 Evidence Ring，并明确标出覆盖范围；
- 用户指针、VFS 对象与资源配额的失败路径不留下半提交状态。

文件系统、资源与并发故障还可以运行：

```bash
make fs-enospc-test TOOLPREFIX=riscv-none-elf-
make fs-allocator-fault-test TOOLPREFIX=riscv-none-elf-
make workflow-teardown-race-test TOOLPREFIX=riscv-none-elf-
make virtio-disk-test TOOLPREFIX=riscv-none-elf-
```

## 6. 综合演示与结果

任务六的主演示入口为：

```bash
make contest-demo TOOLPREFIX=riscv-none-elf-
```

脚本默认运行 4 个等量 AB/BA QEMU 样本，验证 traversal 与 indexed 路径得到相同结果，并把逐样本日志、`summary.json`、`measurements.csv` 和 `report.md` 写入 `results/contest-demo/`。文档不预填尚未实跑的数值；对外引用时应一并保留本次环境、样本数、单位、失败样本和原始日志。主演示的讲解顺序见[现场演示脚本](agentos/scenario-script.md)。

长驻交互产品另有独立入口，不替代上述固定性能场景：

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv-none-elf-
make agentos-console-deepseek TOOLPREFIX=riscv-none-elf-
```

第一条只运行 Host 单测与 Guest 静态合同；第二条在一次真实 QEMU boot 中同时连接 controller 和 observer，以结构化 NDJSON 验收七个 digest-bound 模型请求、三回合工具路径、`waiting_llm`、fresh Context timeline 和正常关闭；第三条是人工 live provider 入口，不进入默认 CI。observer 只提供有测量扰动的 high-signal live snapshots，不是全量实时 trace 或独立安全边界。完整的双窗口步骤、25 秒动态审批、`/context` 字段和 Guest/Host 责任边界见[交互控制台说明](agentos/interactive-console.md)。

四业务 Agent 的 Nexus 产品合同使用独立目标，不改变上述 console 或 `contest-demo`：

```bash
make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv-none-elf-
make agentos-nexus-deepseek TOOLPREFIX=riscv-none-elf-
```

`agentos-nexus-check` 不启动 QEMU。`agentos-nexus-replay` 只有在真实单 boot 脚本完成且 strict validator 通过后，才证明该固定 fixture 下的三回合闭环：四个独立业务 identity、TASK-over-MESSAGE 委派、successful terminal 后的 brokered artifact、Research/Analyst handle readback、报告事件中的两份来源完整 digest、实际 System process/context/file 数值与本 boot `sched_budget`，以及 final 中对应的稳定 System/budget 投影、历史 measurement canonical 数值和两份来源 handle；完整 System 工件与 kernel snapshot 仍保留会随调度变化的 dispatch、used 和 vruntime 事实。验收还包括失败后新任务重规划、精确发布审批拒绝零副作用，以及 Guest-origin `kernel_audit`/`kernel_snapshot` 中的等待唤醒、identity-bound control/capability、Context、payload-byte resource 和 scheduler account 对应。Research/Analyst artifact 按所需 provenance bit subset/superset 验证；内核 MESSAGE audit 没有 provenance，严格投影保留零值而不合成标签。fixture 每个模型响应必须绑定实际请求 SHA-256，controller 的 request/response、tool/TASK correlation、canonical arguments 和跨类型原始时序还要逐项匹配；ready 必须先于会话数据，active request ID 在 turn 内稳定且跨 turn 不复用，delegated TASK batch 先于并绑定对应 tool result，single-inflight 轮次必须满足 `request_i < response_i < 同 correlation effects < request_(i+1)`，final response 先于 `turn_complete`，close 后不得追加输出。空摘要或通配响应直接失败。`agentos-nexus-deepseek` 是人工 live 入口，不是 CI 或性能基准。历史 `nexus_meas` capsule 也不是本次 boot benchmark；完整解释见 [AgentOS Nexus](agentos/nexus.md)。

## 7. 性能测试

专项性能测试直接运行产品负载，不依赖预置结果或 Host receipt：

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

## 8. plain 与 AgentOS 双目标

```bash
make plain-platform-build TOOLPREFIX=riscv-none-elf-
make agentos-platform-build TOOLPREFIX=riscv-none-elf-
make dual-platform-run TOOLPREFIX=riscv-none-elf-
```

`baseline_ucore/` 是共享安全修复但不包含 AgentOS 子系统的对照目标，不是未经修改的上游镜像。两侧运行同一用户态科研 action/state 合同，Host 从各自文件系统镜像和 Guest 日志提取规范状态并比较。

复核一次双目标运行时，至少查看两侧退出状态、Guest 日志、状态字段一致性、缺失文件和比较摘要。调试重跑是正常开发行为，不需要维护发布次数账本；引用性能数字时只需清楚标明本次实际运行的环境与负载。

## 9. Workflow fence 边界

fence 通过 `agent_workflow_fence()`，即 `agent_run(count=0, AGENT_RUN_F_FENCE)` 发起。相关 Guest/Host 测试检查 request、controller 权限、lifecycle key、320 字节 receipt、exact credit cut、event/gap 范围、root 链以及 retry 稳定性。

receipt 只描述当前启动周期内该次 cut，不表示：

- 普通文件内容已进入 root；
- 所有 scheduler 或 Host 行为都被覆盖；
- receipt 已由外部密钥签名。

## 10. 清理

```bash
make clean-workspace-dry-run
make clean-workspace
```

`clean-workspace-dry-run` 先显示将清理的构建和运行输出。测试日志可以按排障需要保留或删除。
