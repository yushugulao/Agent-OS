# Windows / WSL 快速开始

我们推荐在 Windows Terminal + WSL2 Ubuntu 中构建和运行 AgentOS-uCore。编译、QEMU 和 Python 全部留在同一 WSL 环境，避免 Windows、Git Bash 与 MSYS2 路径混用。

## 安装依赖

在 Ubuntu 中运行：

```bash
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu
```

需要的核心工具为：

| 工具 | 用途 |
| --- | --- |
| Git、Bash、GNU Make | 源码与构建脚本 |
| Python 3 | Host runner、validator 和结果整理 |
| RISC-V GCC/binutils | 编译 RISC-V64 Guest |
| `qemu-system-riscv64` | 运行 uCore Guest |

Ubuntu 工具链前缀通常为 `riscv64-linux-gnu-`。xPack 等裸机工具链使用 `riscv-none-elf-`。

## Windows 侧预检

在仓库根目录打开 PowerShell：

```powershell
.\scripts\check-windows-prereqs.ps1
```

脚本检查 `wsl.exe`、可用 distribution，以及 WSL 内的 Bash、Git、Make、Python、QEMU 和 RISC-V 工具链。它只报告环境，不修改系统设置。

## 构建

打开 WSL，进入仓库实际路径：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make doctor
make build TOOLPREFIX=riscv64-linux-gnu-
```

随后运行 ABI、模块和栈检查：

```bash
make agent-uapi-check
make agent-module-check
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`make doctor` 只检查依赖；`make build` 证明当前目标编译链接；静态 checker 不替代 QEMU 行为测试。

## QEMU 验证

完整 AgentOS Guest 回归：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

定向运行单个场景：

```bash
AGENT_TEST_CASE=agentcontract_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agenttask_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

AGENT_TEST_CASE=agentsecurity_ucore \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

runner 检查 QEMU 退出状态、pass marker、panic、输出上限和 timeout。测试失败时先保留串口日志，再用 `AGENT_TEST_CASE` 缩小范围。

## 主演示与性能

```bash
make contest-demo TOOLPREFIX=riscv64-linux-gnu-
```

当前主演示启动 4 个隔离 Guest，按 AB/BA 顺序比较 traversal 与 indexed workflow core path，并把逐样本日志、CSV、JSON 和报告写入 `results/contest-demo/`。两个路径必须得到相同业务结果和 hash。

文档中的正式统计来自 [`one_shot_metrics/data/20260811`](../one_shot_metrics/data/20260811/)：30 次 fresh QEMU boot 和 7,498 行逐样本数据。`3.118x` 是包含 query、recovery write、`fsync` 和 verify 的 workflow core interval 配对中位加速比；同一活动的端到端 paired delta 中位数为 indexed `+13.452 ms`。

plain uCore 与 AgentOS-uCore 的同合同对照入口为：

```bash
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

每次新测量都要保留 Host CPU、WSL/Linux、QEMU、编译器、boot 数、负载、单位和原始日志。不要用本机少量样本覆盖仓库冻结的 canonical 活动。

## 离线产品演示

控制台和 Nexus 都提供不访问网络的固定 QEMU replay：

```bash
make agentos-console-check
make agentos-console-replay TOOLPREFIX=riscv64-linux-gnu-

make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

console replay 验证多轮工具调用、Context 和审批；Nexus replay 验证四 Agent 委派、失败重规划、工件校验与发布拒绝。response 来自 digest-bound fixture，因此两者不能称为 live 模型实测。

## 双窗口 Live 会话

live provider 是可选人工入口。第一个 Windows Terminal WSL 窗格运行：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make agentos-console-deepseek TOOLPREFIX=riscv64-linux-gnu-
```

第二个同 distribution、同 Linux 用户的窗格运行：

```bash
cd /mnt/e/path/to/project61-agentOS-happylegend-uCore
make agentos-observe
```

左侧输入自然语言，常用命令为 `/tools`、`/context`、`/status`、`/approve`、`/deny`、`/reset` 和 `/quit`。副作用提示为 `Approve? [y/N/a]`，等待超过 25 秒或 controller 断开即拒绝。执行中 `Ctrl-C` 取消当前 turn，不关闭 QEMU。controller 断开后可用 `make agentos-cli` 重新连接。

Nexus 使用独立 Guest profile：

```bash
# 左侧
make agentos-nexus-deepseek TOOLPREFIX=riscv64-linux-gnu-

# 右侧
make agentos-nexus-observe
```

Nexus 额外提供 `/agents`、`/tasks` 和 `/artifacts`；重新连接使用 `make agentos-nexus-cli`。

observer 只是 Guest 读取并转发的高信号快照，不是完整 trace、性能计时器或独立安全边界。

## API key

DeepSeek key 从 Host 环境读取：

```bash
export DEEPSEEK_API_KEY='...'
make agentos-console-deepseek TOOLPREFIX=riscv64-linux-gnu-
```

也可以使用仓库外文件：

```bash
make agentos-console-deepseek \
  TOOLPREFIX=riscv64-linux-gnu- \
  AGENTOS_CONSOLE_API_KEY_FILE=/absolute/path/to/key.txt
```

Nexus 对应变量为 `AGENTOS_NEXUS_API_KEY_FILE`。key 内容不会进入 Guest、argv、Context 或日志。网络或 provider 失败不会静默切换 replay；结束失败会话后显式运行对应 replay 目标。

仓库保存的正式验收不包含 live provider 成功声明。只有当次实际 API 往返、Guest 工具执行和正常 session 关闭全部出现，才能记录为 live 演示。

## MSYS2 备选

WSL 不可用时，可以在完整 MSYS2 环境中运行：

```bash
export TOOLPREFIX=/opt/xpack-riscv/bin/riscv-none-elf-
export QEMU=/opt/qemu/qemu-system-riscv64.exe
export PYTHON_BIN=/usr/bin/python3
export BASH_BIN=/usr/bin/bash
make doctor
make build
make agentos-test
```

同一命令中不要混用 Windows Python、Git Bash 和 MSYS2 路径。仓库位于中文路径时，优先使用 WSL 的 `/mnt/...` 映射；MSYS2 需要确认 UTF-8 locale。

完整测试顺序见 [AgentOS 验证说明](agentos/verification.md)，现场流程见[演示脚本](agentos/scenario-script.md)。
