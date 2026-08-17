# 面向 AI 智能体的操作系统内核：运行方法

本页给出从构建到交互使用的一套完整步骤。固定 replay 用于重复运行 QEMU、Guest 程序和 Host 控制链，在线 provider 则通过同一接口提供实时模型回复。AgentOS-uCore 的所有 Guest 程序都运行在 RISC-V64 QEMU `virt` 中；Agent 身份、Context、Execution Contract、Task Channel 和工作流调度均由 uCore 内核管理。

## 文档索引

- [1. 准备环境并构建](#1-准备环境并构建)
- [2. 运行可重复回归](#2-运行可重复回归)
- [3. 使用单 Agent Console](#3-使用单-agent-console)
- [4. 使用 Nexus 多智能体 Harness](#4-使用-nexus-多智能体-harness)
- [5. 接入 DeepSeek](#5-接入-deepseek)
- [6. 观察运行状态并退出](#6-观察运行状态并退出)
- [7. 运行双平台对照测试](#7-运行双平台对照测试)
- [8. 运行 Live Query 配对测试](#8-运行-live-query-配对测试)
- [9. 在 MSYS2 中运行](#9-在-msys2-中运行)

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

## 2. 运行可重复回归

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

### 2.2 Harness 原生 Task Channel

```bash
make agentos-nexus-check
make agentos-harness-native-test TOOLPREFIX=riscv64-linux-gnu-
```

`agentos-nexus-check` 检查 8 项模型动作、7 项 Registry broker、动态 Agent 配置、Context Artifact Store、开发 broker、完成门和开发 replay。`agentos-harness-native-test` 会启动一个长期运行的 `agentharness_ucore` Guest，把 Host Agent 映射为 Guest runtime 进程，并通过原生 Task Channel 提交 root Task 和嵌套子 Task。通过时会输出本次生命周期、Agent 和 Task 数量：

```text
agentos-native-task-channel: PASS lifecycle=<id/generation> agents=2,3 tasks=2 nested=1 terminal=ok
agentos-native-harness: PASS lifecycle=<id/generation> agents=2 tasks=2 generic_loop=1
```

Console 的 `PASS` 由检查程序在核对模型请求、工具结果、Context sequence、审批记录、observer 快照和关闭顺序后给出。Harness 的 `PASS` 则要求 Host 配置与 Guest identity 对齐，两个 Task 完成 claim、complete 和 terminal CQE，并在同一生命周期中正常关闭。

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
| `/reset` | 保留 Console QEMU 和会话，清空 Context 摘要、审批记录及 provider 绑定 |
| `/quit` | 让 Guest 正常结束会话 |

一次审批只对当前会话、轮次和请求生效，同时绑定 correlation、参数摘要、nonce 和有效期。模型修改参数后，系统会重新发起审批。

## 4. 使用 Nexus 多 Agent Harness

通用 Nexus Harness 只接收用户目标、工作区、资源限制、允许工具和可选 Agent 配置。system policy 提供与具体任务无关的安全和完成规则；拥有 `ORCHESTRATE` capability 的 Agent 自行决定是否拆分任务、需要多少子 Agent、每个 Task 的 capability 与工具、并行关系和汇总时点。所有 Agent 使用同一套 Loop，名称只用于日志。详细过程见[Nexus 通用多 Agent Harness Runtime](modules/workflow-runtime.md#七nexus-通用多-agent-harness-runtime)。

每个 Agent 保留 private Context active path，完整 USER、TOOL、FINAL、文件、搜索结果、补丁、编译诊断、运行日志、测试结果和子任务报告进入 Context Artifact Store。内核为正文封存 handle、类型、长度、SHA-256、producer、Task、来源 sequence 和 lifecycle；workflow 共享索引只引用成功结算且明确共享的 Artifact。达到高水位后，Harness 生成 private summary 和 team summary，仍在运行的 Task、未解决错误和未合并修改继续保留。

Tool Registry 包含 7 项 brokered 条目；模型侧的通用动作集合为 `search_files`、`read_file`、`write_file`、`apply_patch`、`build_ucore_program`、`run_ucore_program`、`delegate_task` 和 `complete_task`。同步 `STATUS` 轮询提供 Guest 状态，不消耗模型工具轮次。Harness 不开放 Shell、任意路径写入或任意构建目标。父 Agent 使用 128 字节动态 Task descriptor 描述目标 Artifact、输入 Artifact、所需 capability、允许工具、workspace revision、预算、deadline 和结果类型；Provider 先封存结果 Artifact，再完成 Task。

| 工具 | 行为与限制 |
| --- | --- |
| `search_files` | 分页取得当前 Host 工作区 manifest，由 Guest Metadata Catalog 与 Live Query 选择候选，再在这些候选中匹配路径或正文；查询为空时列出候选文件，可用 `path_prefix` 缩小范围，每次最多返回 8 项 |
| `read_file` | 先由 Guest Catalog 精确选中 manifest 对象，再从当前 revision 读取 1 至 64 行，并说明返回范围及后面是否还有内容 |
| `inspect_system` | Registry 中保留的内部 Guest 状态投影；产品 Harness 使用同步 `STATUS` 轮询，不把它作为模型动作 |
| `write_file` | 创建或替换 `user/src/nexus_*_ucore.c`；要求 `expected_revision` 与当前 SHA-256 完全一致，新文件使用 `missing`；同目录临时文件写完并同步后原子替换 |
| `apply_patch` | 对同一路径应用一份有界 unified diff；补丁目标和 revision 均须匹配，失败时文件保持原状 |
| `build_ucore_program` | 在独立临时工作树中以固定 `riscv64-linux-gnu-` 工具链构建同名目标，最多运行 180 秒，返回 source revision、build id 和有界诊断 |
| `run_ucore_program` | 使用成功 build 的独立镜像启动新的单 Hart、128 MiB Guest，30 秒内传入最多 512 字节串口输入，并核对实际输出与退出状态；用例类型为 `normal`、`invalid` 或 `failure` |

Host workspace broker 将 `.` 作为默认工作区根目录。它把这个显式 root 固定在已打开的目录句柄上，只接受根目录内的规范相对路径，拒绝绝对路径、父目录跳转和通过链接离开工作区。broker 分页返回 manifest 与 generation；只有调用 Agent 提供经过核验的 object/path/revision 后，才在这些候选中匹配正文或返回指定版本的分段 UTF-8 字节。开发 broker 只处理模型已经选择且 Task 授权允许的写入、构建和运行请求，不能自行扩展路径或目标集合。

Guest 每次用 1 个 control inode 和 manifest 当前页面最多 32 个 data-stub inode 建立 Metadata Catalog 窗口，并按 4 个 stage、每组最多 8 项执行 Live Query。每个工具 correlation 的第一次 MANIFEST 请求使用空 generation，后续请求携带本次已校验的 generation；每次工具调用都会重新获取、解析、摘要并校验 manifest。

同一生命周期内，lifecycle、cursor、entry count、EOF、workspace generation 和有序对象摘要全部匹配，且 control stub 仍为 `READY` 时，Guest 复用现有窗口。需要更新时，control stub 先进入 `BUILDING`，旧 data stub 随即失效，新页面以每批最多 16 项登记，全部完成后再进入 `READY`。构建失败或 Host stale 会将窗口置为 `STALE` 并清理，清理失败执行完整 reset。完整路径由 Guest 的有界运行时内存再次复核，正文匹配仍由 Host 在 Guest 已选定的候选中完成。实际搜索或读取正文先封存为 FILE/SEARCH Artifact，再进入 TOOL Context 和下一轮模型历史。

先运行 Host 合约与原生 Guest 集成测试：

```bash
make agentos-nexus-check
make agentos-harness-native-test TOOLPREFIX=riscv64-linux-gnu-
```

通用 Harness 直接接收目标；可选 JSON 配置可以为多个 Agent 指定 capability、允许工具、资源额度、提示词 Artifact 与摘要高水位。没有配置时，根 Agent 根据 workflow policy 自主选择单 Agent 或动态子任务方案。整个模型会话共用一个长期运行的 Guest，固定 Relay、Coordinator、System、Research 角色已经退出产品命令。

```bash
make agentos-nexus-harness \
  AGENTOS_NEXUS_HARNESS_GOAL='开发一个简易计算器，并在真实 Guest 中验证正常、无效和关键失败输入' \
  TOOLPREFIX=riscv64-linux-gnu-

# 可选：AGENTOS_NEXUS_HARNESS_CONFIG=/absolute/path/to/agents.json
```

该入口默认使用 `AGENTOS_NEXUS_HARNESS_PROGRESS=auto`。交互式终端显示单窗口仪表板，持续汇总 QEMU、Guest lifecycle、内核 tick、Task Channel pending/claimed/terminal 状态与 SQ/CQ、Context、workflow 调度、Agent、模型轮次、工具、构建、测试和 Artifact 状态。仪表板通过同一条 Native Task Channel 串口发送同步 `STATUS` 查询，默认每秒取得一次只读内核快照；所有串口请求继续由同一把锁串行处理，不会与 `SPAWN`、`DELEGATE`、`COMPLETE` 或 `CLOSE` 响应交错。

构建输出和实时面板写入标准错误，任务结束后的完整 workflow JSON 单独写入标准输出。因此下面的命令会在当前窗口显示进度，同时把最终结果保存为机器可读文件：

```bash
make agentos-nexus-harness \
  AGENTOS_NEXUS_HARNESS_GOAL="$GOAL" \
  AGENTOS_NEXUS_API_KEY_FILE=../deepseek_api.txt \
  TOOLPREFIX=riscv64-linux-gnu- \
  > nexus-result.json
```

可以按使用场景选择显示方式：

| 变量值 | 行为 |
| --- | --- |
| `auto` | 交互式终端使用仪表板，非交互输出使用逐行进度；这是 `make agentos-nexus-harness` 的默认值 |
| `dashboard` | 在同一终端原地刷新 Agent、Task、工具和内核状态；终端较窄或高度不足时自动改用紧凑布局 |
| `plain` | 为每个关键阶段输出带时间戳的普通文本，内核快照最多每 5 秒显示一次 |
| `ndjson` | 把每个结构化事件作为一行 JSON 写入标准错误 |
| `off` | 关闭实时显示，保留最终 workflow JSON |

下面的运行同时保留完整结构化事件文件。事件文件与终端面板使用同一个事件总线，包含 Host、模型、Agent、Task、工具、build、run、kernel 和 QEMU 来源；长参数只记录长度与 SHA-256，API key、模型私有推理和无长度限制的日志不会进入事件。

```bash
make agentos-nexus-harness \
  AGENTOS_NEXUS_HARNESS_GOAL="$GOAL" \
  AGENTOS_NEXUS_API_KEY_FILE=../deepseek_api.txt \
  AGENTOS_NEXUS_HARNESS_PROGRESS=dashboard \
  AGENTOS_NEXUS_HARNESS_STATUS_INTERVAL=1.0 \
  AGENTOS_NEXUS_HARNESS_TRACE_FILE=/tmp/nexus-harness.ndjson \
  TOOLPREFIX=riscv64-linux-gnu-
```

这次实际 DeepSeek 验收由模型选择单 Agent 完成，共 5 个模型轮次和 4 次产品工具调用。读取、写入、构建和运行分别绑定 native Task；最新 build 在 5 个独立 Guest 中通过正常表达式、非法字符、空输入、运算符错误和除零用例。运行证据见 [`ci/agentos-nexus-multiagent-evidence.json`](../ci/agentos-nexus-multiagent-evidence.json)。该结果验证通用工具与完成门，不要求固定 Agent 数量。

每轮都有一个 root Task。拥有 `ORCHESTRATE` capability 的 Agent 可以建立子 Task；128 字节 descriptor 绑定 parent task、目标描述 Artifact、输入 Artifact、所需 capability、允许工具、workspace revision、资源预算、deadline 和预期结果类型。子 Agent claim 后读取输入，先封存结果 Artifact，再通过 complete 提交 terminal 状态。任务完成结算时，父 Agent 从至多一条 terminal CQE 取得状态和 handle，并复核 producer、Task id、Context sequence、lifecycle 与 SHA-256。内核拒绝 self delegation 和任务图中的真实环路，同时允许多个独立子任务并行。

`agent_context_prefetch()` 可为每个 Agent 启用固定容量的查询转移预测。只有 active path 上成功结算的只读查询进入训练；默认单次预取最多 4 KiB、同时最多 2 项。Guest VFS 目标进入低优先级队列，Host 目标产生 `PREFETCH_HINT`。rollback、Context clear、退出、lifecycle 或 revision 变化都会使相关状态失效，正式读取继续执行权限与版本检查。

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

通用 Harness 使用仓库外的密钥文件：

```bash
make agentos-nexus-harness \
  AGENTOS_NEXUS_HARNESS_GOAL='检查一个 AgentOS-uCore 模块并给出经过构建和运行验证的修改' \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_NEXUS_API_KEY_FILE=/absolute/path/to/deepseek-key.txt
```

也可以把密钥保存在仓库外的文件中，并传入绝对路径：

```bash
make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE=/absolute/path/to/deepseek-key.txt
```

Harness 对应的变量为 `AGENTOS_NEXUS_API_KEY_FILE`。Host 读取密钥并发起 `HTTPS` 请求；每个模型 Task 同时绑定 Guest Agent identity 和原生 Task Channel 状态。DeepSeek V4 请求显式使用 `thinking.type=enabled` 与 `reasoning_effort=max`。工具轮次之间需要的 provider-private `reasoning_content` 由 Host 向 provider 原样回传，但不进入 Guest 或 telemetry。在线 provider 只改变模型回复的来源，工具执行和结果结算仍由本次运行完成。

## 6. 观察运行状态并退出

Nexus Harness 的 Host 与内核进展已经合并到启动命令所在的终端，不需要另开 observer。仪表板关闭或输出被重定向时，可改用 `plain` 或 `ndjson`；`Ctrl-C` 会恢复终端光标、停止状态轮询、关闭开发 broker，并终止长期运行的 Harness Guest。

Console 运行时，可以另开一个终端，接入只读 observer：

```bash
make agentos-observe
```

observer 可以查看状态、任务和内核时间线，但不能发送控制命令。当前会话信息保存在仅当前用户可读写的 Host 运行目录中。重新连接 controller 时使用：

```bash
make agentos-cli
```

Console 中等待模型回复或工具执行时按 `Ctrl-C`，只会取消当前轮次，会话仍可继续使用。Harness 工具任务已经进入 Task Channel 时，同一生命周期内具备取消权限的 Agent 使用预先绑定的 syscall 568 `REQUEST_CANCEL`；内核确认后，已 claim 的执行 Agent 会撤销预绑定结果并完成终态 ACK，父 Agent 取得唯一 CQE 并把 Contract 收敛到 `RECLAIMED`。Console 需要退出时，在 controller 输入 `/quit`。Guest 完成收尾后会显示：

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

## 8. 运行 Live Query 配对测试

`labdemo_ucore` 根据预计使用次数选择查询路径：`K=1` 直接遍历目录，`K=2/4/8` 以每批最多 16 条构建 Catalog，并在同一生命周期内复用。下面的命令执行 16 次独立 QEMU 启动，以 8 次 AB、8 次 BA 顺序比较 `K=4` 的索引复用路径与目录遍历路径：

```bash
CONTEST_DEMO_SAMPLES=16 \
CONTEST_DEMO_QUERY_USES=4 \
CONTEST_DEMO_CASE_TIMEOUT=180s \
CONTEST_DEMO_OUTPUT=results/contest-demo-k4 \
bash scripts/run-contest-demo.sh
```

脚本检查两条路径的恢复状态与结果摘要一致，并输出核心耗时、完整流程耗时、检查记录数、I/O 计数、批次登记数和 Catalog 复用次数。当前保存的 16 次结果位于 [`one_shot_metrics/data/20260815_catalog_batch`](../one_shot_metrics/data/20260815_catalog_batch/)；采集方法和图表见[性能测试](performance.md)。

## 9. 在 MSYS2 中运行

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
