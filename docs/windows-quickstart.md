# Windows 克隆后的运行说明

本文说明一台新的 Windows 电脑 clone 仓库后，怎样检查依赖、构建目标并打开结果页面。

## 需要安装的系统组件

推荐环境：

- Windows 10/11。
- WSL2 Ubuntu。
- Git。
- Ubuntu 内的 `bash`、`make`、`python3`。
- Ubuntu 内的绘图 Python 包：`pandas`、`seaborn`、`matplotlib`。缺少这些包时结果生成器仍能走内置 SVG 路径，但推荐安装它们以使用主绘图路径。
- Ubuntu 内的 RISC-V 交叉工具链：`riscv64-linux-gnu-gcc`、`riscv64-linux-gnu-ld`、`riscv64-linux-gnu-objcopy`、`riscv64-linux-gnu-objdump`。
- Ubuntu 内的 QEMU：`qemu-system-riscv64`。

Ubuntu 中可以用以下命令安装主要依赖：

```bash
sudo apt update
sudo apt install -y git build-essential make python3 qemu-system-misc \
  gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu \
  python3-pandas python3-seaborn python3-matplotlib
```

仓库不放置 QEMU、交叉编译器和云端模型密钥。这些内容体积大、和本机环境绑定，放进仓库会增加克隆成本，也不利于复现实验环境复现。仓库内置的是可运行源码、测试脚本、状态查看工具、依赖检查脚本和默认的离线 LLM Relay 路径。

## Windows 侧检查

在仓库根目录打开 PowerShell：

```powershell
.\scripts\check-windows-prereqs.ps1
```

这个脚本会检查：

- Windows 是否能调用 `wsl.exe`。
- 是否存在可用 WSL 发行版。
- WSL 内是否能找到 `bash`、`git`、`make`、`python3`、QEMU 和 RISC-V 工具链。
- WSL 内是否已安装推荐绘图包 `pandas`、`seaborn`、`matplotlib`。

如果缺少依赖，脚本会打印 Ubuntu 中需要执行的安装命令。

## WSL 内检查

进入 WSL 后，切到仓库目录，例如：

```bash
cd /mnt/e/计算机操作系统能力竞赛/project61-agentOS-happylegend-uCore
make doctor
```

`make doctor` 只做依赖检查，不构建内核，不启动 QEMU。它适合在运行前或换机器后先跑一遍。

## 两条主运行命令

依赖检查通过后，推荐使用两条命令完成示例准备：

```bash
make dual-platform-run TOOLPREFIX=<你的 RISC-V 工具链前缀>
make reader
```

第一条命令会运行普通 uCore 对照目标和 AgentOS-uCore 目标，提取状态文件，生成结果页面、CSV 和 SVG 图表。第二条命令会启动本地页面服务，并打印浏览器 URL。查看时优先打开：

- `http://127.0.0.1:8767/`
- `http://127.0.0.1:8767/dual-results.html`
- `http://127.0.0.1:8767/dual-results/monitor.html`
- `http://127.0.0.1:8767/dual-results/reader-guide.html`
- `http://127.0.0.1:8767/reader-url-list.txt`

## 快速检查命令

如果只想确认源码、脚本、状态查看工具和图表生成契约没有明显问题，可以运行：

```bash
make target-readiness
```

构建、专项测试和双目标验证的完整命令见 [verification.md](verification.md) 与 [agentos/verification.md](agentos/verification.md)。

## LLM Relay 运行方式

默认测试不访问云端模型。Host LLM Relay 会使用模板模式生成稳定输出，适合复现实验环境复现。

本机示例可以使用 cloud 模式访问 DeepSeek。密钥文件放在仓库外，通过环境变量传入，例如：

```bash
AGENT_PLATFORM_LLM_API_KEY_FILE=/path/to/local/key-file \
python3 host_tools/plain_ucore_llm_relay.py \
  --state-dir /tmp/agentos-dual-platform/agentos-state \
  --out-dir /tmp/agentos-dual-platform/agentos-state \
  --mode cloud
```

`make reader` 启动的本地查看服务已经带上 `--auto-run-llm-relay --llm-relay-mode cloud`。当 LLM 相关 action 触发时，Relay 会生成复核摘要、方法检查、恢复说明、写作摘要、项目复核意见和最终报告摘要，并刷新相关状态文件。密钥文件不要放入仓库目录。运行脚本和结果文件不会写入密钥内容。
