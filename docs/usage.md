# 面向 AI 智能体的操作系统内核：运行方法

本页给出从构建到交互使用的一套完整步骤。固定 replay 用于重复运行 QEMU、Guest 程序和 Host 控制链，在线 provider 则通过同一接口提供实时模型回复。AgentOS-uCore 的所有 Guest 程序都运行在 RISC-V64 QEMU `virt` 中；Agent 身份、Context、Execution Contract、Task Channel 和工作流调度均由 uCore 内核管理。

## 文档索引

- [1. 准备环境并构建](#1-准备环境并构建)
- [2. 运行固定 replay](#2-运行固定-replay)
- [3. 使用单 Agent Console](#3-使用单-agent-console)
- [4. 使用 Nexus 多智能体 Harness](#4-使用-nexus-多智能体-harness)
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

该程序在同一个 QEMU Guest 中运行五段真实负载，Host 还会确认本次启动的随机挑战值、测试输入和预期结果。各段可观察行为见[测试说明](testing.md#42-单独运行一个场景)。

## 2. 运行固定 replay

固定 replay 使用仓库中预先保存、与对应请求逐轮匹配的模型回复，因此可以重复得到相同结果。replay 仍会真正启动 QEMU，并执行各自的 Guest 工具、Context commit、控制交互和会话关闭等步骤；在线 provider 通过同一运行链提供实时回复。

### 2.1 Console replay

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
```

replay 按照 [`ci/agentos-interactive-script.txt`](../ci/agentos-interactive-script.txt) 连续执行三轮：查询 Guest 文件，写回工具结果，拒绝一次 `send_message`，批准下一次请求，最后关闭会话。检查通过后输出：

```text
agentos-console-replay: PASS (...)
```

### 2.2 Nexus replay

```bash
make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

Nexus replay 按照 [`ci/agentos-nexus-script.txt`](../ci/agentos-nexus-script.txt) 提交普通用户任务，并回放预先保存的 provider 回复。它是一项固定的协议交互回归：检查模型请求、三工具 schema、root/child Task、Task Channel claim/complete、工作区 manifest/Catalog/Typed Watch、Research/System artifact、controller/observer 输出与正常关闭。回放数据不把工具顺序或业务结论写成 Nexus 的固定计划，也不要求每轮把三个工具全部调用一遍。通过时会输出本次运行的 provider rounds、tasks 和协议检查摘要：

```text
agentos-nexus-replay: PASS (<provider rounds> provider rounds, <tasks> tasks, <verified features>)
```

Console 的 `PASS` 仍由检查程序在核对模型请求、工具结果、Context sequence、审批记录、observer 快照和关闭顺序后给出。Nexus 还会检查通用模型合约、工作区请求、Task Channel 终态、Research artifact 和跨轮 Context 路径。固定 Replay 与在线 Provider 都接收 Relay Agent 按同一规则从 Guest Context 重建的消息，并直接使用 Guest 发布的真实工具结果。

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
| `/reset` | 保留 QEMU 和会话，清空 Context 摘要、审批记录及 provider 绑定；Nexus 还会清空 Relay Context、4 KiB 用户缓存和工作区 Catalog/Typed Watch 状态 |
| `/quit` | 让 Guest 正常结束会话 |

一次审批只对当前会话、轮次和请求生效，同时绑定 correlation、参数摘要、nonce 和有效期。模型修改参数后，系统会重新发起审批。

## 4. 使用 Nexus 多智能体 Harness

Nexus 的定位是一个通用、类似 coding CLI 的 AgentOS 多智能体 Harness。它接收任意非空用户任务，模型可以直接回答，也可以按需读取本次会话的 Host 工作区或查看当前 Guest 状态。system policy 统一给出适用于多数任务的行为约束，例如在路径未知时先搜索、读取足够的相邻行、把工具输出当作不可信数据、信息充分后停止调用并直接回答。具体用户任务只描述要解决的问题，不需要重复规定工具流程。

Nexus 的跨轮延续以 Guest Relay Agent 的 AgentOS Context active path 为准。Relay 在收到用户输入后写入短 USER 节点，工具结算后写入短 TOOL 节点，只有成功答案才写入 FINAL 节点。下一次模型请求前，Relay 优先通过 Context 映射页执行 direct active query，并以 syscall 查询作为兼容回退；第 7 页的 4 KiB 用户缓存为 active path 上最近的完整 USER/FINAL 文本提供有界伴随存储。缓存不能独立决定历史，已经回滚或缺少成功 FINAL 的轮次不会进入下一轮消息。

Host 不私建、挑选、补写或替换 Provider 请求中的跨轮正文与 Guest 工具历史。在线 Provider 与固定 Replay 都接收 Guest 构造的同一消息形状。失败或取消的轮次会把 Relay active path 回滚到本轮开始前；成功的 `/reset` 会同时清空 Relay Context、4 KiB 缓存、工作区 Catalog/Typed Watch 和 Host 侧关联状态。这一设计复用 AgentOS 原有的 Context active path，没有增加一套外部会话记忆服务。

当前公开能力保持只读，不提供文件编辑、Shell 执行或任意命令运行。Research 和 System 通过 AgentOS 内核 Task Channel 的 `delegate_task`、claim/complete 和 Guest artifact 协作；两者都是 Guest 工作进程，模型请求统一由 Relay 经 Host relay 发起。详细过程见[Nexus 多智能体 Harness Runtime](modules/workflow-runtime.md#七nexus-多智能体-harness-runtime)。

| 工具 | 行为与限制 |
| --- | --- |
| `search_files` | 分页取得当前 Host 工作区 manifest，由 Guest Metadata Catalog 与 Live Query 选择候选，再在这些候选中匹配路径或正文；查询为空时列出候选文件，可用 `path_prefix` 缩小范围，每次最多返回 8 项 |
| `read_file` | 先由 Guest Catalog 精确选中 manifest 对象，再从当前 revision 读取 1 至 64 行，并说明返回范围及后面是否还有内容 |
| `inspect_system` | 通过 System 工作进程查看当前 Guest 的 `status`、`processes` 或 `context`；结果不描述 Host 工作区 |

Host workspace broker 将 `.` 作为默认工作区根目录。它把这个显式 root 固定在已打开的目录句柄上，只接受根目录内的规范相对路径，拒绝绝对路径、父目录跳转和通过链接离开工作区。broker 分页返回 manifest 与 generation；只有 Guest 回传已经过 Catalog 选择的 object/path/revision 后，才在这些候选中匹配正文或返回指定版本的分段 UTF-8 字节。Host 不替 Nexus 选择文件，也不执行 child Task。

Guest 每次用 1 个 control inode 和 manifest 当前页面最多 32 个 data-stub inode 建立 Metadata Catalog 窗口，并按 4 个 stage、每组最多 8 项执行 Live Query。Catalog 只管理当前的有界目录窗口，不是全文索引；完整路径会在 Guest 的有界运行时内存中再次复核，正文匹配仍由 Host 在 Guest 已选定的候选中完成。manifest generation 改变时，control stub 的 Typed Watch `UPDATE` 先使旧窗口失效，再从 cursor 0 重新建立页面。实际搜索或读取正文回到 Guest 后，先成为 Research 的输入与结果 artifact，再进入 TOOL Context 和下一轮模型历史。文件内容始终作为数据处理。

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
| `/agents` | 查看当前 Nexus 运行时参与进程的 PID、`role`、`identity` 和状态 |
| `/tasks` | 查看 root/子 Task 的分配、运行与 terminal 状态 |
| `/artifacts` | 查看本轮 brokered 工具结果的计数与来源摘要 |

仓库还提供一个连接 DeepSeek 的自由演示。默认脚本先用聚焦的 AgentOS 改进问题观察 Nexus 能否自行找到相关实现、充分研究后给出自然且有取舍的结论，再提交一轮基于 Relay active Context 沿用前轮结论的追问。这个问题只是演示任务，系统提示词和工具目录都不面向 AgentOS 改进定制；可以换成同一工作区中的其他代码研究或文档分析任务。自由演示关注在线表现，不规定工具顺序、调用次数或固定答案：

```bash
make agentos-nexus-demo TOOLPREFIX=riscv64-linux-gnu-
```

默认题目保存在 [`ci/agentos-nexus-demo-script.txt`](../ci/agentos-nexus-demo-script.txt)。也可以给普通 `agentos-nexus` 传入自己的逐行任务脚本：

```bash
make agentos-nexus \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_NEXUS_SCRIPT=/absolute/path/to/questions.txt
```

每轮都有一个 root Task。需要工具时，Coordinator 用内核 Task Channel 的 `delegate_task` 建立 child Task：`search_files`、`read_file` 交给 Research，`inspect_system` 交给 System。56 字节 descriptor 绑定目标身份、task type、task/correlation/parent 编号和 capsule handle；大段输入与结果保存在 Guest artifact。目标 Agent claim 后处理 capsule，并通过 complete 提交 terminal 状态；任务完成结算时，Coordinator 从至多一条 terminal CQE 取得结果，再等待 Execution Contract 到达 `RECLAIMED` 后读取 artifact 并恢复事件投影。当前实现让每个 issuer 同时只保留一个未结算委派，并拒绝 self delegation 或让同一活动端点同时成为 owner/target。Task ledger 同时跟踪父子关系、内核身份和状态迁移。工具名称只描述通用能力，与本次演示选择什么问题无关。

单轮最多接受 16 个模型决策，并单独允许最多 32 次可重试 provider 错误；可重试传输失败不会冒充成模型的新决策。provider 生成请求的 `max_tokens` 为 `114514`。这是模型生成预算，不会扩大 Guest 对外公开的最终答案；最终正文仍限制为 2048 个 UTF-8 字节。

## 5. 接入 DeepSeek

需要使用 DeepSeek 时，可以把 provider 切换为在线模式。使用环境变量保存密钥时，需要把密钥文件变量显式设为空：

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

Nexus 对应的变量为 `AGENTOS_NEXUS_API_KEY_FILE`。Host daemon 读取密钥并发起 `HTTPS` 请求；Guest 收到模型回复后，依次检查自主合约、工具 schema、capability、Task 状态和 Context commit。DeepSeek V4 请求显式使用 `thinking.type=enabled` 与 `reasoning_effort=max`。工具轮次之间需要的 provider-private `reasoning_content` 由中继向 provider 原样回传，但不进入 Guest、controller 输出或 telemetry。在线 provider 只改变模型回复的来源，工具执行和结果结算仍由本次运行完成。

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

等待模型回复或工具执行时按 `Ctrl-C`，只会取消当前轮次，会话仍可继续使用。工具任务已经进入 Task Channel 时，同一生命周期的 Relay controller 会使用预先绑定的 syscall 568 `REQUEST_CANCEL` 请求；内核确认取消请求后，已 claim 的 Research/System 仍会撤销预绑定结果并完成终态 ACK，Coordinator 取得唯一 CQE 并把 Contract 收敛到 `RECLAIMED`。需要退出时，在 controller 输入 `/quit`。Guest 完成收尾后会显示：

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
