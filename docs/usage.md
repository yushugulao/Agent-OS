# AgentOS-uCore 运行指南

本指南从可重复的固定回放开始，再进入单 Agent Console、多 Agent Nexus 和在线模型接入。所有 Guest 都运行在 RISC-V64 QEMU `virt` 上，Agent 身份、Context、工具合同、Task Channel 与 workflow 调度由 uCore 内核执行。

## 文档索引

- [1. 环境与构建](#1-环境与构建)
- [2. 固定回放](#2-固定回放)
- [3. Console 单 Agent 会话](#3-console-单-agent-会话)
- [4. Nexus 多 Agent workflow](#4-nexus-多-agent-workflow)
- [5. 接入 DeepSeek](#5-接入-deepseek)
- [6. Observer 与会话关闭](#6-observer-与会话关闭)
- [7. Plain uCore 对照运行](#7-plain-ucore-对照运行)
- [8. MSYS2 运行](#8-msys2-运行)

## 1. 环境与构建

### 1.1 安装依赖

推荐在 WSL2 Ubuntu 中完成构建和运行，使 Make、Python、QEMU 与交叉工具链处于同一 Linux 环境：

```bash
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu
```

| 工具 | 用途 |
| --- | --- |
| Bash、GNU Make | 组织内核、文件系统镜像与测试入口 |
| Python 3 | 运行 Host 控制面、协议校验器与数据工具 |
| RISC-V GCC/binutils | 编译 uCore 内核与 Guest 程序 |
| `qemu-system-riscv64` | 启动 RISC-V64 `virt` Guest |

Ubuntu 软件包提供的工具链前缀通常为 `riscv64-linux-gnu-`。使用 xPack 裸机工具链时，把下文的 `TOOLPREFIX` 改为实际的 `riscv-none-elf-` 路径。

Windows 侧可以先检查 WSL、QEMU 和工具链：

```powershell
.\scripts\check-windows-prereqs.ps1
```

随后在 WSL 中进入仓库并运行环境诊断：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make doctor
```

### 1.2 构建 AgentOS-uCore

```bash
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
make agent-uapi-check TOOLPREFIX=riscv64-linux-gnu-
make agent-module-check TOOLPREFIX=riscv64-linux-gnu-
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`agentos-build` 先构建 Agent Guest 与文件系统镜像，再生成以 `agentfinal_ucore` 为入口的内核。后三个入口依次核对 kernel/user ABI、AgentOS 模块依赖和真实调用图上的内核栈预算。完成标志为 `Build kernel done`，其余检查成功时以退出码 `0` 结束。

## 2. 固定回放

固定回放使用预先绑定请求 SHA-256 的响应，在一次 QEMU 启动中走完整串口、Guest 工具、Context、审批和关闭路径。我们先运行它，确认环境与产品链路一致，再接入在线 provider。

### 2.1 Console 回放

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
```

回放依照 [`ci/agentos-interactive-script.txt`](../ci/agentos-interactive-script.txt) 完成三个连续 turn：查询 Guest 文件、回灌工具结果、拒绝一次 `send_message`、批准下一次请求并关闭 session。校验器同时读取 controller 与 observer 的 NDJSON，成功时输出：

```text
agentos-console-replay: PASS (7 digests, 3 turns, governed tools, fresh kernel timeline, clean close)
```

### 2.2 Nexus 回放

```bash
make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

Nexus 回放依照 [`ci/agentos-nexus-script.txt`](../ci/agentos-nexus-script.txt) 运行 Coordinator、System、Research、Analyst 四个身份。流程包含 System 状态采集、Research 错误句柄失败、重新规划、两类工件交给 Analyst、发布请求拒绝和 session 关闭。成功标志为：

```text
agentos-nexus-replay: PASS (11 digests, 3 turns, 4 roles, replan, denied publish, clean close)
```

这两个 PASS 行由验证器在核对模型请求、工具结果、Context sequence、审批绑定、observer 快照和关闭顺序后生成。

## 3. Console 单 Agent 会话

Console 由三个部分组成：`agentlive_ucore` 保存 turn 与 Context，Host daemon 独占 QEMU 串口并连接 provider，CLI 负责输入和控制命令。离线体验可以复用固定 fixture，并按照回放脚本逐行输入：

```bash
make agentos-console \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_PROVIDER=replay
```

CLI 建立连接后显示：

```text
AgentOS session <session-id> ready
```

输入普通文本会启动一个 turn；每轮完成后显示 `[turn N completed]`。常用控制命令如下：

| 命令 | 内核与运行时结果 |
| --- | --- |
| `/tools` | 读取当前 Guest 工具目录与 schema |
| `/context` | 读取 Context sequence、来源标签和最近结果 |
| `/status` | 读取 Guest tick、loop state、call count、wait 计数和 capability mask |
| `/approve` | 对当前副作用请求授权一次 |
| `/approve session` | 在当前 session 中保存同名工具的授权 |
| `/deny` | 拒绝当前请求，并把结构化失败结果写回 turn |
| `/reset` | 保留 QEMU 与 session，重置 Context 摘要、审批和 provider binding |
| `/quit` | 请求 Guest 有序关闭 session |

一次副作用批准同时绑定 session、turn、request、correlation、规范化参数摘要、nonce 和有效期。模型改变参数后会产生新的审批请求。

## 4. Nexus 多 Agent workflow

Nexus 在同一 lifecycle 中建立四个独立 Agent。Coordinator 负责拆分目标，System 采集本次启动的内核状态，Research 读取来源数据，Analyst 消费两类工件并形成汇总。

![Nexus 多 Agent workflow](figures/architecture/nexus_runtime_flow.jpg)

[打开 DrawIO 源文件](figures/architecture/nexus_runtime_flow.drawio)

离线交互入口为：

```bash
make agentos-nexus \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_NEXUS_PROVIDER=replay
```

CLI 就绪时显示：

```text
AgentOS session <session-id> ready profile=nexus
```

Nexus 继承 Console 的控制命令，并增加三个 workflow 视图：

| 命令 | 返回内容 |
| --- | --- |
| `/agents` | 四个 Agent 的 PID、role、identity 与运行状态 |
| `/tasks` | N1 task 的 assigned、running、completed、failed 等状态 |
| `/artifacts` | workflow-scoped 工件句柄、来源与摘要 |

四个 Agent 使用独立 PID、identity 和 Context，任务通过内核 `MESSAGE` 与 Nexus `N1` envelope 传递。工件句柄携带 lifecycle generation，失败任务可以重新委派，最终结果由 Coordinator 收集。

## 5. 接入 DeepSeek

完成固定回放后，可以把 provider 切换为 DeepSeek。使用环境变量时，需要把 key file 变量显式设为空：

```bash
export DEEPSEEK_API_KEY='...'

make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE= \
  AGENTOS_CONSOLE_API_KEY_ENV=DEEPSEEK_API_KEY
```

Nexus 使用对应变量：

```bash
make agentos-nexus-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_NEXUS_API_KEY_FILE= \
  AGENTOS_NEXUS_API_KEY_ENV=DEEPSEEK_API_KEY
```

也可以把 key 保存到仓库外的文件，并传入绝对路径：

```bash
make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE=/absolute/path/to/deepseek-key.txt
```

Nexus 对应变量为 `AGENTOS_NEXUS_API_KEY_FILE`。Host daemon 读取 key 并发起 HTTPS 请求；Guest 接收串口协议封装的模型响应，继续执行工具选择、schema 与能力检查、用户审批、Context 提交和 workflow 状态推进。

## 6. Observer 与会话关闭

当 Console 在第一个终端运行时，同一 WSL 用户可以在第二个终端连接只读 observer：

```bash
make agentos-observe
```

Nexus 使用带 profile 校验的入口：

```bash
make agentos-nexus-observe
```

observer 接收状态、任务和内核 timeline 快照，不取得 controller 权限。活动 session 保存在 owner-only Host runtime 目录中；需要重新连接 controller 时使用：

```bash
make agentos-cli
# Nexus session
make agentos-nexus-cli
```

等待模型响应或工具执行时按 `Ctrl-C` 会发送当前 turn 的 cancel，session 保持可用。结束运行应在 controller 输入 `/quit`，CLI 收到 Guest 关闭事件后显示：

```text
AgentOS session closed
```

随后 daemon 关闭 QEMU，observer 收到最后一个 `session_closed` 快照并退出。

## 7. Plain uCore 对照运行

仓库保留 Plain uCore 与 AgentOS-uCore 两套科研 workflow。两侧使用相同输入、程序顺序、阶段名称和 outcome oracle，Host 从两套文件系统镜像提取规范状态并比较结果。

![Plain uCore 与 AgentOS-uCore](figures/architecture/plain_agentos_comparison.jpg)

[打开 DrawIO 源文件](figures/architecture/plain_agentos_comparison.drawio)

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

两侧均通过 Guest marker、状态清单、文件摘要和 outcome 检查后，最后输出：

```text
[dual-platform] plain and AgentOS platforms both passed
```

AgentOS 路径还会保存 lifecycle、Context、metadata、调度和 Task Channel 状态，用于定位同一业务结果在内核运行时中的形成过程。

## 8. MSYS2 运行

完整 MSYS2 环境也可以运行本项目：

```bash
export TOOLPREFIX=/opt/xpack-riscv/bin/riscv-none-elf-
export QEMU=/opt/qemu/qemu-system-riscv64.exe
export PYTHON_BIN=/usr/bin/python3
export BASH_BIN=/usr/bin/bash
make doctor
make agentos-build
make agentos-test
```

Make、Bash、Python、QEMU 和工具链应使用同一套 MSYS2 路径。仓库位于中文路径时，需要启用 UTF-8 locale，并确认 QEMU 能读取生成的内核与文件系统镜像。

运行涉及的内核模块见[产品架构](architecture.md)，接口字段见 [API](api.md)，测试矩阵见[测试说明](testing.md)，逐样本性能结果见[性能测试](performance.md)。
