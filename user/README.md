# 用户程序目录说明：AgentOS-uCore

本目录包含 AgentOS-uCore 目标的用户态程序。它保留 uCore 教学测试的构建方式，同时加入 AgentOS 专项测试、综合演示程序和科研 Agent 平台程序。

## 基础用法

生成指定章节或目标的用户程序：

```shell
make CHAPTER=agent
make CHAPTER=platform_agentos
```

`CHAPTER` 常用取值：

- `agent`：构建 AgentOS 专项测试程序，例如 `agentfinal_ucore`、`agentfs_ucore`、`agentloop_ucore`、`agentbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore`。
- `platform_agentos`：构建接入增强内核服务的科研 Agent 平台程序，例如 `rp_agentos_orch` 和完整 `rp_*` 平台程序集。
- uCore 原有章节值仍可用于基础教学测试。

`BASE` 参数含义与 uCore 原始用户程序目录一致：

- `BASE=1`：只生成基础测试。
- `BASE=0`：生成完整测试集合。

## 输出目录

- `target/bin`：生成的 `.bin` 文件。
- `target/elf`：生成的 `.elf` 文件。
- `asm`：用户程序反汇编输出。

## 本项目相关入口

AgentOS 专项验证入口：

```text
agentfinal_ucore
agentfs_ucore
agentscan_ucore
agentloop_ucore
agentsched_ucore
agentbench_ucore
labbench_ucore
labdemo_ucore
agentsecurity_ucore
```

科研 Agent 平台增强入口：

```text
rp_agentos_orch
```

`rp_agentos_orch` 会创建 orchestrator Agent，初始化 `rp_agentos_mainflow`，再运行与 plain target 可比较的 RUN-042 科研流程，并把关键内核证据写入 `rp_agentos_*` 状态文件。
