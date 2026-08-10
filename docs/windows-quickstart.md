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

## 6. MSYS2 备选

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

不要在同一次命令中混用 Windows Python、Git Bash 工具和 MSYS2 路径。若仓库位于中文路径，优先使用 WSL 的 `/mnt/...` 映射或确认 MSYS2 使用 UTF-8 locale。

更完整的测试选择见 [验证说明](verification.md) 与 [AgentOS 内核验证](agentos/verification.md)。
