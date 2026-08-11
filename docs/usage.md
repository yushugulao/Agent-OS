# AgentOS-uCore 运行方法

本页给出从构建到交互使用的一套完整步骤。建议先跑固定回放，确认 QEMU、客户机程序和宿主机控制程序能够正常配合，再接入在线模型服务。AgentOS-uCore 的所有客户机程序都运行在 RISC-V64 QEMU `virt` 中；智能体身份、上下文、执行约定、任务通道和工作流调度均由 uCore 内核管理。

## 文档索引

- [1. 准备环境并构建](#1-准备环境并构建)
- [2. 运行固定回放](#2-运行固定回放)
- [3. 使用单智能体控制台](#3-使用单智能体控制台)
- [4. 使用多智能体工作流](#4-使用多智能体工作流)
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
| Python 3 | 运行宿主机控制程序、协议检查和数据处理脚本 |
| RISC-V GCC、binutils | 编译 uCore 内核和客户机程序 |
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

`agentos-build` 先编译智能体客户机程序和文件系统镜像，再生成以 `agentfinal_ucore` 为入口的内核。后三条命令分别检查内核态和用户态共用的 `ABI`、AgentOS 模块之间的调用关系，以及实际内核调用链的栈空间。构建完成时会看到 `Build kernel done`，其余检查通过时退出码为 `0`。

## 2. 运行固定回放

固定回放使用仓库中预先保存的模型回复，每条回复都绑定请求的 `SHA-256` 摘要，因此可以重复得到相同结果。回放仍会真正启动 QEMU，并执行客户机工具、上下文提交、审批和会话关闭等步骤。我们先用它检查整条运行链，再接入在线模型服务。

### 2.1 单智能体回放

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
```

回放按照 [`ci/agentos-interactive-script.txt`](../ci/agentos-interactive-script.txt) 连续执行三个轮次：查询客户机文件，写回工具结果，拒绝一次 `send_message`，批准下一次请求，最后关闭会话。检查通过后输出：

```text
agentos-console-replay: PASS (7 digests, 3 turns, governed tools, fresh kernel timeline, clean close)
```

### 2.2 多智能体回放

```bash
make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

回放按照 [`ci/agentos-nexus-script.txt`](../ci/agentos-nexus-script.txt) 启动协调、系统、研究和分析四个智能体。系统智能体采集系统状态，研究智能体先提交一个错误句柄，协调智能体随后重新安排任务；分析智能体收到两类结果后整理报告。流程还会拒绝一次发布请求，并在最后关闭会话。检查通过后输出：

```text
agentos-nexus-replay: PASS (11 digests, 3 turns, 4 roles, replan, denied publish, clean close)
```

这两条 `PASS` 由检查程序在核对模型请求、工具结果、上下文序号、审批记录、观察端快照和关闭顺序后给出。

## 3. 使用单智能体控制台

单智能体控制台由三个程序配合运行：`agentlive_ucore` 在客户机中保存轮次和上下文，宿主机后台程序独占 QEMU 串口并连接模型服务，命令行程序负责接收输入和控制命令。离线使用时可选用固定回放数据：

```bash
make agentos-console \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_PROVIDER=replay
```

连接成功后，命令行会显示：

```text
AgentOS session <session-id> ready
```

输入普通文本即可开始一个轮次。每轮结束后会显示 `[turn N completed]`。常用命令如下：

| 命令 | 作用 |
| --- | --- |
| `/tools` | 查看客户机当前提供的工具及其参数格式 |
| `/context` | 查看上下文序号、来源标记和最近一次结果 |
| `/status` | 查看客户机时钟、循环状态、调用次数、等待次数和能力掩码 |
| `/approve` | 批准当前这一次有副作用的请求 |
| `/approve session` | 在当前会话内记住同名工具的批准结果 |
| `/deny` | 拒绝当前请求，并按协议把失败结果写回本轮上下文 |
| `/reset` | 保留 QEMU 和会话，清空上下文摘要、审批记录及模型服务绑定 |
| `/quit` | 让客户机正常结束会话 |

一次审批只对当前会话、轮次和请求生效，同时绑定关联号、参数摘要、一次性编号和有效期。模型修改参数后，系统会重新发起审批。

## 4. 使用多智能体工作流

Nexus 会在同一次运行中建立四个彼此独立的智能体。协调智能体（Coordinator）拆分任务，系统智能体（System）采集本次启动的内核状态，研究智能体（Research）读取来源数据，分析智能体（Analyst）汇总前两者交付的结果。各智能体如何传递任务和结果，见[多智能体工作流](modules/workflow-runtime.md)。

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

Nexus 支持单智能体控制台的全部命令，并增加三个工作流查询命令：

| 命令 | 作用 |
| --- | --- |
| `/agents` | 查看四个智能体的 PID、角色、身份和运行状态 |
| `/tasks` | 查看 `N1` 任务的分配、运行、完成和失败状态 |
| `/artifacts` | 查看当前工作流产生的结果句柄、来源和摘要 |

四个智能体各自拥有 PID、身份和上下文。它们通过内核 `MESSAGE` 和 Nexus `N1` 消息传递任务。结果句柄带有生命周期代次；任务失败后可以重新分派，最后由协调智能体汇总结果。

## 5. 接入 DeepSeek

固定回放通过后，可以把模型服务切换为 DeepSeek。使用环境变量保存密钥时，需要把密钥文件变量显式设为空：

```bash
export DEEPSEEK_API_KEY='...'

make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE= \
  AGENTOS_CONSOLE_API_KEY_ENV=DEEPSEEK_API_KEY
```

多智能体工作流使用对应变量：

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

多智能体工作流对应的变量为 `AGENTOS_NEXUS_API_KEY_FILE`。宿主机后台程序读取密钥并发起 `HTTPS` 请求；客户机收到模型回复后，继续检查工具选择、参数格式和能力，等待用户审批，提交上下文并推进工作流。在线模型服务只改变模型回复的来源，后续步骤仍由本次运行完成。

## 6. 观察运行状态并退出

单智能体控制台运行时，可以另开一个终端，接入只读观察端：

```bash
make agentos-observe
```

多智能体工作流使用：

```bash
make agentos-nexus-observe
```

观察端可以查看状态、任务和内核时间线，但不能发送控制命令。当前会话信息保存在仅当前用户可读写的宿主机运行目录中。重新连接控制端时使用：

```bash
make agentos-cli
# Nexus 会话
make agentos-nexus-cli
```

等待模型回复或工具执行时按 `Ctrl-C`，只会取消当前轮次，会话仍可继续使用。需要退出时，在控制端输入 `/quit`。客户机完成收尾后会显示：

```text
AgentOS session closed
```

随后，宿主机后台程序关闭 QEMU，观察端收到最后一条 `session_closed` 状态后退出。

## 7. 运行双平台对照测试

仓库分别为普通 uCore 和 AgentOS-uCore 实现了同一组科研任务。两边使用同一份输入，按相同顺序运行程序，并由同一套结果判定程序检查输出。测试步骤和比较项目见[双平台对照测试](testing.md#7-双平台对照测试)。

```bash
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-platform-build TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

两边的客户机标志行、状态清单、文件摘要和最终结果全部通过后，命令会输出：

```text
[dual-platform] plain and AgentOS platforms both passed
```

AgentOS-uCore 还会保存生命周期、上下文、元数据、调度和任务通道状态，便于从内核记录中查清最终结果是怎样一步步得到的。

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
