# AgentOS-uCore 运行方法

本页给出从构建到交互使用的一套完整步骤。建议先跑固定 replay，确认 QEMU、Guest 程序和 Host 控制程序能够正常配合，再接入在线 provider。AgentOS-uCore 的所有 Guest 程序都运行在 RISC-V64 QEMU `virt` 中；Agent 身份、Context、Execution Contract、Task Channel 和工作流调度均由 uCore 内核管理。

## 文档索引

- [1. 准备环境并构建](#1-准备环境并构建)
- [2. 运行固定 replay](#2-运行固定-replay)
- [3. 使用单 Agent Console](#3-使用单-agent-console)
- [4. 使用多 Agent 工作流](#4-使用多-agent-工作流)
- [5. 接入 DeepSeek](#5-接入-deepseek)
- [6. 观察运行状态并退出](#6-观察运行状态并退出)
- [7. 运行双平台对照测试](#7-运行双平台对照测试)
- [8. 在 MSYS2 中运行](#8-在-msys2-中运行)

## 1. 准备环境并构建

### 1.1 安装依赖

我们建议在 WSL2 Ubuntu 中构建和运行项目，这样 Make、Python、QEMU 和交叉编译工具链都处在同一个 Linux 环境中：

```bash
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu
```

| 工具 | 用途 |
| --- | --- |
| Bash、GNU Make | 组织内核、文件系统镜像和测试命令 |
| Python 3 | 运行 Host 控制程序、协议检查和数据处理脚本 |
| RISC-V GCC、binutils | 编译 uCore 内核和 Guest 程序 |
| `qemu-system-riscv64` | 启动 RISC-V64 `virt` 虚拟机 |

Ubuntu 软件包提供的工具链前缀一般为 `riscv64-linux-gnu-`。如果使用 xPack 裸机工具链，请把下文的 `TOOLPREFIX` 改为实际的 `riscv-none-elf-` 路径。

在 Windows 中可先检查 WSL、QEMU 和工具链：

```powershell
.\scripts\check-windows-prereqs.ps1
```

然后进入 WSL，在仓库目录中检查构建环境：

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

`agentos-build` 先编译 Guest 中的 Agent 程序和文件系统镜像，再生成以 `agentfinal_ucore` 为入口的内核。后三条命令分别检查内核态和用户态共用的 `ABI`、AgentOS 模块之间的调用关系，以及实际内核调用链的栈空间。构建完成时会看到 `Build kernel done`，其余检查通过时退出码为 `0`。

需要按赛题五项机制串联检查 Agent 创建与地址空间、结构化工具、Context 路径、文件查询和 Agent Loop 时，运行：

```bash
AGENT_TEST_CASE=agenteval_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

该程序在同一个 QEMU Guest 中运行五段真实负载，Host 还会核对本次启动的随机挑战值、负载指纹和结果指纹。各段可观察行为见[测试说明](testing.md#42-单独运行一个场景)。

## 2. 运行固定 replay

固定 replay 使用仓库中预先保存的模型回复，每条回复都绑定请求的 `SHA-256` 摘要，因此可以重复得到相同结果。replay 仍会真正启动 QEMU，并执行 Guest 工具、Context commit、审批和会话关闭等步骤。我们先用它检查整条运行链，再接入在线 provider。

### 2.1 Console replay

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
```

replay 按照 [`ci/agentos-interactive-script.txt`](../ci/agentos-interactive-script.txt) 连续执行三轮：查询 Guest 文件，写回工具结果，拒绝一次 `send_message`，批准下一次请求，最后关闭会话。检查通过后输出：

```text
agentos-console-replay: PASS (7 digests, 3 turns, governed tools, fresh kernel timeline, clean close)
```

### 2.2 Nexus replay

```bash
make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

replay 按照 [`ci/agentos-nexus-script.txt`](../ci/agentos-nexus-script.txt) 启动 Coordinator、System、Research 和 Analyst 四个 Agent。System Agent 采集系统状态，Research Agent 先提交一个错误句柄，Coordinator 随后重新安排任务；Analyst 收到两类 artifact 后整理报告。流程还会拒绝一次发布请求，并在最后关闭会话。检查通过后输出：

```text
agentos-nexus-replay: PASS (11 digests, 3 turns, 4 roles, replan, denied publish, clean close)
```

这两条 `PASS` 由检查程序在核对模型请求、工具结果、Context sequence、审批记录、observer 快照和关闭顺序后给出。

## 3. 使用单 Agent Console

Console 由三个程序配合运行：`agentlive_ucore` 在 Guest 中保存轮次和 Context，Host daemon 独占 QEMU 串口并连接 provider，CLI 负责接收输入和控制命令。离线使用时可选用固定 replay 数据：

```bash
make agentos-console \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_PROVIDER=replay
```

连接成功后，命令行会显示：

```text
AgentOS session <session-id> ready
```

输入普通文本即可开始一轮交互。每轮结束后会显示 `[turn N completed]`。常用命令如下：

| 命令 | 作用 |
| --- | --- |
| `/tools` | 查看 Guest 当前提供的工具及其 schema |
| `/context` | 查看 Context sequence、来源标记和最近一次结果 |
| `/status` | 查看 Guest tick、循环状态、调用次数、等待次数和 capability mask |
| `/approve` | 批准当前这一次有副作用的请求 |
| `/approve session` | 在当前会话内记住同名工具的批准结果 |
| `/deny` | 拒绝当前请求，并按协议把失败结果写回本轮 Context |
| `/reset` | 保留 QEMU 和会话，清空 Context 摘要、审批记录及 provider 绑定 |
| `/quit` | 让 Guest 正常结束会话 |

一次审批只对当前会话、轮次和请求生效，同时绑定 correlation、参数 digest、nonce 和有效期。模型修改参数后，系统会重新发起审批。

## 4. 使用多 Agent 工作流

Nexus 会在同一次运行中建立四个彼此独立的 Agent。Coordinator 拆分任务，System 采集本次启动的内核状态，Research 读取来源数据，Analyst 汇总前两者交付的 artifact。各 Agent 如何传递任务和结果，见[多 Agent 工作流](modules/workflow-runtime.md)。

离线运行命令如下：

```bash
make agentos-nexus \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_NEXUS_PROVIDER=replay
```

命令行准备完成后会显示：

```text
AgentOS session <session-id> ready profile=nexus
```

Nexus 支持 Console 的全部命令，并增加三个工作流查询命令：

| 命令 | 作用 |
| --- | --- |
| `/agents` | 查看四个 Agent 的 PID、`role`、`identity` 和运行状态 |
| `/tasks` | 查看 `N1` 任务的分配、运行、完成和失败状态 |
| `/artifacts` | 查看当前工作流产生的 artifact handle、来源和摘要 |

四个 Agent 各自拥有 PID、`identity` 和 Context。它们通过内核 `MESSAGE` 和 Nexus `N1` 消息传递任务。generation-safe artifact handle 与 lifecycle generation 绑定，访问时可以排除跨 generation 的旧句柄；任务失败后可以重新分派，最后由 Coordinator 汇总结果。

每份 artifact 的 header 与 payload 由 `agent_file_publish()` 一次提交。内核写完整内容后才接入正式文件名，所以读取方不会遇到只写了一半的结果；同名文件已经存在时也不会被覆盖。若调用返回 `DUPLICATE` 或 `INDETERMINATE`，Nexus 会回读正式路径，只有内容与本次提交逐字节相同且文件恰好在 payload 后结束，才按幂等发布继续处理。

## 5. 接入 DeepSeek

固定 replay 通过后，可以把 provider 切换为 DeepSeek。使用环境变量保存密钥时，需要把密钥文件变量显式设为空：

```bash
export DEEPSEEK_API_KEY='...'

make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE= \
  AGENTOS_CONSOLE_API_KEY_ENV=DEEPSEEK_API_KEY
```

Nexus 工作流使用对应变量：

```bash
make agentos-nexus-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_NEXUS_API_KEY_FILE= \
  AGENTOS_NEXUS_API_KEY_ENV=DEEPSEEK_API_KEY
```

也可以把密钥保存在仓库外的文件中，并传入绝对路径：

```bash
make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE=/absolute/path/to/deepseek-key.txt
```

Nexus 对应的变量为 `AGENTOS_NEXUS_API_KEY_FILE`。Host daemon 读取密钥并发起 `HTTPS` 请求；Guest 收到模型回复后，继续检查工具选择、schema 和 capability，等待用户审批，完成 Context commit 并推进工作流。在线 provider 只改变模型回复的来源，后续步骤仍由本次运行完成。

## 6. 观察运行状态并退出

Console 运行时，可以另开一个终端，接入只读 observer：

```bash
make agentos-observe
```

Nexus 工作流使用：

```bash
make agentos-nexus-observe
```

observer 可以查看状态、任务和内核时间线，但不能发送控制命令。当前会话信息保存在仅当前用户可读写的 Host 运行目录中。重新连接 controller 时使用：

```bash
make agentos-cli
# Nexus 会话
make agentos-nexus-cli
```

等待模型回复或工具执行时按 `Ctrl-C`，只会取消当前轮次，会话仍可继续使用。需要退出时，在 controller 输入 `/quit`。Guest 完成收尾后会显示：

```text
AgentOS session closed
```

随后，Host daemon 关闭 QEMU，observer 收到最后一条 `session_closed` 状态后退出。

## 7. 运行双平台对照测试

仓库分别为普通 uCore 和 AgentOS-uCore 实现了同一组科研任务。两边使用同一份输入，按相同顺序运行程序，并由同一套结果判定程序检查输出。测试步骤和比较项目见[双平台对照测试](testing.md#7-双平台对照测试)。

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

两边的 Guest 标志行、状态清单、文件摘要和最终结果全部通过后，命令会输出：

```text
[dual-platform] plain and AgentOS platforms both passed
```

AgentOS-uCore 还会保存 lifecycle、Context、元数据、调度和 Task Channel 状态，便于从内核记录中查清最终结果是怎样一步步得到的。

## 8. 在 MSYS2 中运行

项目也可以在完整的 MSYS2 环境中运行：

```bash
export TOOLPREFIX=/opt/xpack-riscv/bin/riscv-none-elf-
export QEMU=/opt/qemu/qemu-system-riscv64.exe
export PYTHON_BIN=/usr/bin/python3
export BASH_BIN=/usr/bin/bash
make doctor
make agentos-build
make agentos-test
```

Make、Bash、Python、QEMU 和工具链应全部使用 MSYS2 中的路径。仓库位于中文路径时，请启用 UTF-8 locale，并确认 QEMU 能够读取生成的内核和文件系统镜像。

相关内核模块见[产品架构](architecture.md)，接口字段见 [API](api.md)，测试入口见[测试说明](testing.md)，性能原始数据和图表见[性能测试](performance.md)。
