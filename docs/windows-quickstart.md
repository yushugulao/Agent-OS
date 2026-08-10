# Windows 克隆后的运行说明

本文说明在新的 Windows 电脑上检查依赖、构建 AgentOS-uCore 并运行实际 QEMU 测试。推荐使用 WSL2 Ubuntu；常规开发环境即可完成全部产品验证。

## 1. 安装依赖

推荐环境：

- Windows 10/11 与 WSL2 Ubuntu；
- Git、Bash、GNU Make 和 Python 3；
- RISC-V GCC/binutils；
- `qemu-system-riscv64`。

Ubuntu 中可安装：

```bash
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu
```

仓库不分发 QEMU 和交叉编译器。使用发行版工具链时，命令中的前缀通常是 `riscv64-linux-gnu-`；使用 xPack 等裸机工具链时可传入 `riscv-none-elf-`。

## 2. Windows 侧预检

在仓库根目录打开 PowerShell：

```powershell
.\scripts\check-windows-prereqs.ps1
```

脚本检查 `wsl.exe`、可用发行版以及 WSL 中的 Bash、Git、Make、Python、QEMU 和 RISC-V 工具链。缺少依赖时会打印安装建议。

## 3. WSL 构建

进入 WSL 后切到 clone 的实际目录，例如：

```bash
cd /mnt/<盘符>/<你的目录>/<仓库名>
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
```

`make doctor` 只检查依赖。`make build` 编译并链接当前 AgentOS 内核，最适合确认工具链和源码状态。

再运行有直接产品价值的快速检查：

```bash
make agent-uapi-check TOOLPREFIX=riscv64-linux-gnu-
make agent-module-check TOOLPREFIX=riscv64-linux-gnu-
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

这些检查关注 ABI、生产模块边界与真实调用图栈安全，不采集工具哈希，也不要求绑定某个 Git 提交。

## 4. QEMU 功能与安全测试

完整 AgentOS Guest 回归：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

修改单一功能时，先跑定向场景会更快：

```bash
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttrust_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

测试 runner 会检查 QEMU 退出状态、预期 marker、panic、输出上限与超时。无需安装时长 profile；超时只是实际测试失败，不形成平台认证结论。

## 5. 性能与双目标

产品性能场景可以直接运行：

```bash
AGENT_TEST_CASE=agentbench_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentsched_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

性能数字应与本次运行的工具链、QEMU 版本、Host CPU、样本数和负载一起记录。原始串口输出和比较摘要已经足够定位产品回归和复核测量。

## 6. Windows Terminal 双窗口交互演示

交互控制台使用 WSL 内 owner-only Unix socket；两个窗口必须打开同一个 WSL distribution、使用同一 Linux 用户。先在 Windows Terminal 新建一个 WSL 窗格作为左侧控制台，并进入仓库，例如：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make agentos-console TOOLPREFIX=riscv64-linux-gnu-
```

该命令只构建一次 `agentlive_ucore` 镜像，随后启动长驻 QEMU/Host daemon 并显示 `agentos>`。看到提示符后，在 Windows Terminal 再拆分一个使用同一 WSL profile 的右侧窗格：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make agentos-observe
```

左侧直接输入自然语言。`/tools`、`/context`、`/status`、`/reset` 和 `/quit` 分别查询工具、查看 Context、查看状态、重置对话状态和正常关闭 session；`/context` 的真实 Guest 响应包含 count、最旧/最新 sequence、dropped、provenance 和最新 tool/status/result 摘要。副作用工具显示 `Approve? [y/N/a]`，也可使用 `/approve` 或 `/deny`；Host 最多等待 25 秒，超时或 controller 断开即 deny。执行中按 `Ctrl-C` 只取消当前 turn，不应关闭 QEMU。左侧终端断开但 daemon 仍存活时，在相同 WSL 用户下运行 `make agentos-cli` 重新连接；右侧随时可以重新运行 `make agentos-observe`。

左侧 `agentos-cli` 只负责终端呈现以及收集用户、slash command、取消和审批输入。后台 daemon 独占 QEMU 串口，保管 API key，完成 TLS/provider 翻译并路由 owner-only 本地连接；Agent Loop、历史、工具选择和工具执行仍由 Guest 拥有。右侧不是全量实时内核 trace，而是 Guest observer 线程读取并转发的 high-signal live snapshots；它存在测量扰动，也不构成独立安全边界。

无网络演示先验收固定 replay：

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-
```

使用 DeepSeek 进行自由交互时显式运行：

```bash
make agentos-console-deepseek TOOLPREFIX=riscv64-linux-gnu-
```

Make 在 Host 侧探测仓库外的 `../deepseek_api.txt` 和 `../计算机操作系统能力竞赛/deepseek_api.txt`，不会把 key 内容放进 argv、Guest 或日志。其他路径用 `AGENTOS_CONSOLE_API_KEY_FILE=/absolute/path/to/key.txt` 指定。现场网络不可用时，不会静默切 provider；结束 live session 后显式运行 `make agentos-console-replay`。replay 经过同一 Guest/内核路径但只接受 fixture 预设脚本，不能称为自由问答或 DeepSeek 实测。详细的审批绑定、observer 边界和 slash 命令见 [AgentOS 交互控制台](agentos/interactive-console.md)。

四业务 Agent 的 Nexus 演示使用独立 Guest profile。Windows Terminal 左侧 WSL 窗口先运行：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make agentos-nexus TOOLPREFIX=riscv64-linux-gnu-
```

同一 distribution、同一 Linux 用户的右侧窗口运行：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make agentos-nexus-observe
```

左侧可连续输入自然语言，并使用 `/tools`、`/agents`、`/tasks`、`/artifacts`、`/context`、`/status`、`/reset` 和 `/quit`；重新 attach 使用 `make agentos-nexus-cli`。副作用审批显示 `y/N/a` 或接受 `/approve`、`/deny`，最多等待 25 秒；`Ctrl-C` 取消当前 turn 而保留 session。Host key 自动探测与 console 相同，其他位置可用 `AGENTOS_NEXUS_API_KEY_FILE=/absolute/path/to/key.txt`。网络失败不会静默改用 replay；离线场景必须显式运行：

```bash
make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

`check` 仅是本地合同，`replay` 通过固定 digest-bound fixture 走真实 QEMU；两者都不是 DeepSeek 实测或性能 benchmark。四角色长驻/动态委派、Guest 工件、kernel audit/snapshot 和历史 measurement capsule 的边界见 [AgentOS Nexus](agentos/nexus.md)。

## 7. MSYS2 备选

WSL 不可用时可以在完整 MSYS2 环境中运行，但必须提供 `/usr/bin/python3`、Bash、Git、Make、QEMU 和同一套 RISC-V GCC/binutils。示例：

```bash
export TOOLPREFIX=/opt/xpack-riscv/bin/riscv-none-elf-
export QEMU=/opt/qemu/qemu-system-riscv64.exe
export PYTHON_BIN=/usr/bin/python3
export BASH_BIN=/usr/bin/bash
make doctor
make build
make agentos-test
```

不要在同一次命令中混用 Windows Python、Git Bash 工具和 MSYS2 路径。若仓库位于中文路径，优先使用 WSL 的 `/mnt/...` 映射或确认 MSYS2 使用 UTF-8 locale。长驻双窗口产品入口以 WSL 为推荐环境；MSYS2 仍适合现有构建和一次性测试。

更完整的测试选择见 [验证说明](verification.md) 与 [AgentOS 内核验证](agentos/verification.md)。
