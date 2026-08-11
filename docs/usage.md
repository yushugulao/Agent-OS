# AgentOS-uCore 运行指南

AgentOS-uCore 在 Linux 环境中完成交叉编译，并通过 QEMU 运行 RISC-V64 Guest。Windows 推荐使用 WSL2 Ubuntu，使 Make、Python、QEMU 和工具链处于同一个 Linux 环境。

## 环境准备

WSL2 Ubuntu 可以直接安装所需软件包：

```bash
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu
```

| 工具 | 用途 |
| --- | --- |
| Bash、GNU Make | 执行构建和运行入口 |
| Python 3 | 运行 Host 控制面、校验器和数据工具 |
| RISC-V GCC/binutils | 编译 uCore 内核与 Guest 程序 |
| `qemu-system-riscv64` | 运行 RISC-V64 `virt` Guest |

Ubuntu 软件包通常提供 `riscv64-linux-gnu-` 前缀。xPack 等裸机工具链通常使用 `riscv-none-elf-`。

Windows PowerShell 可以先运行环境检查：

```powershell
.\scripts\check-windows-prereqs.ps1
```

随后在 WSL 中进入仓库映射路径：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make doctor
```

## 构建内核

```bash
make build TOOLPREFIX=riscv64-linux-gnu-
make agent-uapi-check TOOLPREFIX=riscv64-linux-gnu-
make agent-module-check TOOLPREFIX=riscv64-linux-gnu-
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`build` 生成 AgentOS-uCore 内核；其余三项检查 UAPI 布局、AgentOS 模块依赖与真实调用图上的内核栈预算。

## 运行 Guest

完整 AgentOS Guest 回归使用：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

定位单个功能时指定 Guest 程序：

```bash
AGENT_TEST_CASE=agentfinal_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agentfs_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agentcontract_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

Runner 检查退出状态、预期 marker、panic、输出上限和 timeout。各 Guest 与产品模块的对应关系见[测试说明](testing.md)。

## 交互 Agent Loop

`agentlive_ucore` 在 Guest 内保存 turn、Context、工具目录和 correlation。Host daemon 独占 QEMU 串口，负责本地控制面、TLS 和 provider JSON；内核继续处理身份、schema、capability、scope、来源和资源检查。

先运行协议检查与固定 replay：

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
```

Console replay 在同一个 Guest session 中运行三个连续回合，覆盖工具调用、Context 回灌、副作用审批、状态查询和会话关闭。

连接 DeepSeek：

```bash
export DEEPSEEK_API_KEY='...'
make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE= \
  AGENTOS_CONSOLE_API_KEY_ENV=DEEPSEEK_API_KEY
```

同一 WSL 用户可以在另一个终端打开只读 observer：

```bash
make agentos-observe
```

daemon 仍在运行且 `latest` session 状态存在时，使用 `make agentos-cli` 重新连接 controller。常用命令如下：

| 命令 | 作用 |
| --- | --- |
| `/tools` | 查看 Guest 工具目录 |
| `/context` | 查看 Context sequence、来源和最近结果 |
| `/status` | 查看 Guest tick、loop state、call count、wait 计数与 capability mask |
| `/approve` | 批准当前副作用请求 |
| `/approve session` | 在当前 session 中批准后续同名请求 |
| `/deny` | 拒绝当前请求并写入结构化结果 |
| `/reset` | 保留 QEMU 与 session，清理 Context 摘要、审批和 provider binding |
| `/quit` | 关闭 session、daemon 与 QEMU |

`Ctrl-C` 取消当前 turn 并保留 session。副作用批准绑定 session、turn、request、correlation、规范化参数 digest、nonce 和有效期。

## Nexus 多 Agent Workflow

Nexus 在一个 lifecycle 中运行 Coordinator、System、Research 和 Analyst 四个 Agent。Coordinator 拆分目标，System 读取本次启动状态，Research 整理来源数据，Analyst 消费两类工件并生成汇总结果。

![Nexus 多 Agent workflow](figures/architecture/nexus_runtime_flow.png)

[打开 DrawIO 源文件](figures/architecture/nexus_runtime_flow.drawio)

四个 Agent 使用独立 PID、identity 与 Context，并共享 workflow 资源账户。任务通过内核 `MESSAGE` 与 Nexus `N1` envelope 传递，阶段结果通过 workflow-scoped artifact 交接。

运行协议检查与固定 replay：

```bash
make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

replay 完成任务委派、失败后重规划、System/Research 工件、Analyst 汇总、副作用审批和 session 关闭。连接 DeepSeek 时，在第一个终端启动 controller：

```bash
make agentos-nexus-deepseek TOOLPREFIX=riscv64-linux-gnu-
```

daemon 启动后，在同一 WSL 用户的第二个终端连接 observer：

```bash
make agentos-nexus-observe
```

daemon 仍在运行且 `latest` session 状态存在时，使用 `make agentos-nexus-cli` 重新连接 controller。Nexus 额外提供 `/agents`、`/tasks` 与 `/artifacts` 命令。

## API key 文件

API key 可以保存在仓库外的 owner-only 文件中。显式 `AGENTOS_CONSOLE_API_KEY_FILE` 优先于环境变量；变量未设置时，Makefile 会检查仓库相邻位置的默认 key 文件：

```bash
make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE=/absolute/path/to/key.txt
```

Nexus 对应变量为 `AGENTOS_NEXUS_API_KEY_FILE` 和 `AGENTOS_NEXUS_API_KEY_ENV`。使用环境变量时同时把 file 变量设为空。Host relay 读取 key 并完成 HTTPS 请求，Guest 接收协议封装后的模型响应。

## Plain uCore 与 AgentOS-uCore

仓库保留 plain uCore 与 AgentOS-uCore 两套目标，用同一科研 workflow 检查业务结果并观察系统级耗时。

![Plain uCore 与 AgentOS-uCore](figures/architecture/plain_agentos_comparison.png)

[打开 DrawIO 源文件](figures/architecture/plain_agentos_comparison.drawio)

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

`dual-platform-run` 构建两套镜像、启动 Guest、提取规范状态并比较结果。两侧共享输入、程序顺序、阶段名称和 outcome oracle；AgentOS 路径继续输出 lifecycle、Context、metadata、调度和 Task Channel 状态。

## MSYS2

完整 MSYS2 环境也可以运行本项目：

```bash
export TOOLPREFIX=/opt/xpack-riscv/bin/riscv-none-elf-
export QEMU=/opt/qemu/qemu-system-riscv64.exe
export PYTHON_BIN=/usr/bin/python3
export BASH_BIN=/usr/bin/bash
make doctor
make build
make agentos-test
```

Make、Bash、Python、QEMU 和工具链需要使用同一套 MSYS2 路径。仓库位于中文路径时应启用 UTF-8 locale，并确认 QEMU 可以读取生成的镜像。

运行涉及的内核模块见[产品架构](architecture.md)，接口字段见 [API](api.md)，性能采集和逐样本数据见[性能结果](performance.md)。
