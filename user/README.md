# AgentOS-uCore Guest 程序

本目录包含 AgentOS 用户态运行库、产品测试、交互 Agent Loop、Nexus 多 Agent workflow 和性能负载。

## 基础用法

生成指定章节或目标的用户程序：

```shell
make CHAPTER=agent
make CHAPTER=platform_agentos
```

`CHAPTER` 常用取值：

- `agent`：构建 AgentOS 功能、安全、性能和稳健性专项程序；实际清单以 `user/Makefile` 的 `AGENT_TESTS` 为准。
- `agent_eval`：构建将赛题任务一至任务五串联验收，并包含 Live Query、工具传输和并发性能负载的 `agenteval_ucore`。
- `platform_agentos`：构建接入增强内核服务的科研 Agent 平台程序，例如 `rp_agentos_orch` 和完整 `rp_*` 平台程序集。
- uCore 原有章节值仍可用于基础教学测试。

`BASE` 参数含义与 uCore 原始用户程序目录一致：

- `BASE=1`：只生成基础测试。
- `BASE=0`：生成完整测试集合。

## 输出目录

- `target/bin`：生成的 `.bin` 文件。
- `target/elf`：生成的 `.elf` 文件。
- `asm`：用户程序反汇编输出。

## 主要程序

内核模块回归：

```text
agentfinal_ucore
agentfs_ucore
agentscan_ucore
agentloop_ucore
agentsched_ucore
agentconflict_ucore
agentllm_ucore
agentbench_ucore
agentcontract_ucore
agent_eevdf_ucore
agenttask_ucore
agentpublish_ucore
labdemo_ucore
agentsecurity_ucore
agenttoolabi_ucore
agentscope_ucore
agenttrust_ucore
agentvfs_ucore
iobudget_ucore
usersafety_ucore
blocking_semantics_ucore
```

任务一至任务五的组合验收与性能负载位于 `agent_eval` 章节：

```text
agenteval_ucore
```

`agenteval_ucore` 在同一个 RV64 Guest 中串联 Agent 创建与 Context、结构化工具、上下文路径、文件查询和 Agent Loop 五段验收，并运行 Live Query、Agent Task 与工作流 EEVDF 性能负载。

科研 workflow 入口：

```text
rp_agentos_orch
```

`rp_agentos_orch` 创建 orchestrator Agent，初始化 `rp_agentos_mainflow`，运行与 plain target 相同的确定性流程，并把规范状态写入 `rp_agentos_*` 文件。该流程包含数据准备、比对处理、结果分析、报告生成和归档。

构建与运行见[运行指南](../docs/usage.md)，Guest 与产品模块的对应关系见[测试文档](../docs/testing.md)。
