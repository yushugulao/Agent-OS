# Windows 克隆后的运行说明

本文说明一台新的 Windows 电脑 clone 仓库后，怎样检查依赖、构建目标并打开演示页面。

## 需要安装的系统组件

推荐环境：

- Windows 10/11。
- WSL2 Ubuntu。
- Git。
- Ubuntu 内的 `bash`、`make`、`python3`。
- Ubuntu 内的 RISC-V 交叉工具链：`riscv64-linux-gnu-gcc`、`riscv64-linux-gnu-ld`、`riscv64-linux-gnu-objcopy`、`riscv64-linux-gnu-objdump`。
- Ubuntu 内的 QEMU：`qemu-system-riscv64`。

Ubuntu 中可以用以下命令安装主要依赖：

```bash
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu
```

仓库不放置 QEMU、交叉编译器和云端模型密钥。这些内容体积大、和本机环境绑定，放进仓库会增加克隆成本，也不利于评审环境复现。仓库内置的是可运行源码、测试脚本、页面生成工具、依赖检查脚本和默认的离线 LLM Relay 路径。

## Windows 侧检查

在仓库根目录打开 PowerShell：

```powershell
.\scripts\check-windows-prereqs.ps1
```

这个脚本会检查：

- Windows 是否能调用 `wsl.exe`。
- 是否存在可用 WSL 发行版。
- WSL 内是否能找到 `bash`、`git`、`make`、`python3`、QEMU 和 RISC-V 工具链。

如果缺少依赖，脚本会打印 Ubuntu 中需要执行的安装命令。

## WSL 内检查

进入 WSL 后，切到仓库目录，例如：

```bash
cd /mnt/e/计算机操作系统能力竞赛/project61-agentOS-happylegend-uCore
make doctor
```

`make doctor` 只做依赖检查，不构建内核，不启动 QEMU。它适合在录屏前或换机器后先跑一遍。

## 两条主演示命令

依赖检查通过后，推荐使用两条命令完成演示准备：

```bash
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
make demo-reader
```

第一条命令会运行普通 uCore 对照目标和 AgentOS-uCore 目标，提取状态文件，生成结果页面、CSV 和 SVG 图表。第二条命令会启动本地页面服务，并打印浏览器 URL。录屏时优先打开：

- `http://127.0.0.1:8767/`
- `http://127.0.0.1:8767/dual-results.html`
- `http://127.0.0.1:8767/dual-results/monitor.html`
- `http://127.0.0.1:8767/dual-results/demo-guide.html`
- `http://127.0.0.1:8767/demo-url-list.txt`

## 快速检查命令

如果只想确认源码、脚本、Host Reader 和图表生成契约没有明显问题，可以运行：

```bash
make target-readiness
```

如果只想构建两个目标：

```bash
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
```

如果只想运行 AgentOS 内核专项测试：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

## LLM Relay 默认行为

默认测试不访问云端模型。Host LLM Relay 会使用模板模式生成稳定输出，适合评审环境复现。

如果本机需要测试真实云端模型，应只在本机环境变量中指定外部密钥文件路径，例如：

```bash
AGENT_PLATFORM_LLM_API_KEY_FILE=/path/to/local/key-file \
AGENT_PLATFORM_LLM_MODE=cloud \
python3 host_tools/plain_ucore_llm_relay.py --mode cloud
```

密钥文件不要放入仓库目录。运行脚本和结果文件不会写入密钥内容。
