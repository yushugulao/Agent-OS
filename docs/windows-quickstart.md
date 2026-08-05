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

进入 WSL 后，切到实际 clone 的仓库根目录，不要照抄某台开发机的绝对路径，例如：

```bash
cd /mnt/<盘符>/<你的目录>/<仓库名>
make doctor
```

`make doctor` 只做依赖检查，不构建内核，不启动 QEMU。它适合在运行前或换机器后先跑一遍。

竞赛评价另有更严格的执行域检查。在 Windows PowerShell 或 Git Bash 启动评价前运行：

```bash
make evaluation-doctor EVALUATION_WSL_DISTRO=Ubuntu AGENT_TEST_DURATION_PROFILE=none
```

它只检查指定发行版，不使用另一个默认 WSL 发行版代替；同时验证 `wslpath` 对当前仓库
的真实映射、Python 3.10+、交叉工具链（含 `size`）、QEMU `virt` 和构建/摘要工具。
通过后，正式评价的所有阶段都会整体进入该 WSL，禁止 Windows 原生 micro 与 WSL
科研场景混跑。若发行版中的工具名不同，可设置 `EVALUATION_WSL_TOOLPREFIX` 和
`EVALUATION_WSL_QEMU`。`wsl --version` 在旧 Windows 上不可用不会单独导致失败；指定
发行版本身无法执行或工具不完整仍会 fail closed。

## WSL 服务不可用时的 MSYS2 正式域

若 WSL 服务本身持续返回 `Wsl/Service/E_ACCESSDENIED`，可以改用完整安装的 MSYS2，
但不能直接换成 Git Bash、Cygwin、MINGW shell 或 Windows Python。MSYS2 shell 内需要
提供自身的 `/usr/bin/python3`、Bash、Git、Make、env、timeout、readlink、sha256sum、
uname、cygpath 和 Host objdump，并在同一 POSIX namespace 中暴露完整 RISC-V GCC/
binutils、GNU size 与 QEMU。工具链前缀建议使用规范 POSIX 绝对值，例如：

```bash
export MSYSTEM=MSYS
export TOOLPREFIX=/opt/xpack-riscv/bin/riscv-none-elf-
export QEMU=/opt/qemu/qemu-system-riscv64.exe
export PYTHON_BIN=/usr/bin/python3
export BASH_BIN=/usr/bin/bash
export TMPDIR=/tmp
export AGENT_TEST_DURATION_PROFILE=local-e3
make evaluation-doctor
```

本地 `kernel-budget-check` 与 Ubuntu CI 共用同一构建参数和预算，但工具身份分开证明。
Ubuntu profile 校验固定的 `/usr/bin` 路径、`dpkg` 归属与包完整性；MSYS2 profile 校验
gcc、cc1、as、ld、objcopy、objdump、nm、size 八个可执行文件的版本和逐文件 SHA-256，
并要求它与本地三轮时长校准使用同一 profile 及全部共有组件版本。不能通过改名、wrapper
或混搭工具进入该 profile。预算检查先进入显式的仓库根目录，再把仓库内文件以相对路径
交给原生工具，因此从仓库外调用或工作区含中文时都不会泄漏不兼容的绝对路径。

预检会把结果明确标为 `domain=native-msys2`，绑定 Windows build、uname、
`msys-2.0.dll` 和所有工具哈希，并验证控制面程序确实使用该 runtime。仓库、工具和临时
目录必须通过 cygpath 双向映射；中文路径会在受控域中固定使用 `C.UTF-8`。正式
`run`、`verify`、`kernel-cost`、`dashboard`、`package` 随后全部由已绑定的绝对
`env -i` 和 Bash 重新启动，不能把 MSYS2 micro 与 WSL 科研场景混用。campaign 清单的
`run.execution_domain` 为 `native-msys2`，科研场景为
`native-msys2-clean-shell`。任何 runtime/tool 哈希变化、伪造 re-entry marker 或继承
`BASH_ENV`、`PYTHONPATH`、`MAKEFLAGS`、`GIT_*` 等变量都会 fail closed。

同一 platform proof v2 还会从 MSYS2 的 `/proc/cpuinfo` 和 `/proc/meminfo` 记录稳定的
CPU model、logical CPU count 与总内存，并在每个 Guest boot 前复验；动态 MHz 不作为
身份。硬件字段包含在 campaign 哈希内，scenario plan schema v5 也绑定同一 proof 并在每轮
pair 前后复验。公开证据不记录计算机名，uname 证明只保留
Windows build、kernel release/version 与 machine。

`local-e3` 只用于与校准记录逐项一致的本地 MSYS2 E3。当前配置绑定 `14607e825f06`
的三轮完整 18-case；若受管输入或 profile 漂移，doctor/full-verify 会在 QEMU 前 fail closed。
不得复用历史提交的基线，也不能把 `none` 冒充本地 E3 时长证明。

## 正式竞赛评价流程

普通 Linux/WSL 或普通 Runner 在同一受控 POSIX 执行域中依次运行：

```bash
export AGENT_TEST_DURATION_PROFILE=none
make evaluation-doctor
make evaluation-smoke
make evaluation-run
make evaluation-verify
make evaluation-kernel-cost
make evaluation-full-verify
make evaluation-dashboard
make evaluation-package
```

`none` 仍强制完整 18-case、语义、Guest 日志和 timing 行清单，只把本地 wall-time
baseline/limit/ratio 记为不适用。受信且已完成当前校准的 MSYS2 E3 改用
`export AGENT_TEST_DURATION_PROFILE=local-e3` 后执行同一组命令；不得让 Makefile 默认值替代
这两个显式选择。

formal run id 固定为 `formal-<源码提交 C 的完整 40 位提交号>`。challenge 和 AB/BA 顺序
由 C 确定性派生，所以不同 clone 的计划一致；失败目录会保留且同一输出根拒绝覆盖。
本地机制不能替代受保护远端 Runner，也不声称能证明其他 clone 从未丢弃尝试。首个 QEMU
前生成的 run plan schema v2、scenario plan schema v5 和
`measurement-source-receipt.json` 绑定计划、六份 Guest 测量源码及评价控制面策略清单；
package 中的全部策略快照还要与 C 的 Git blob 一致。

`evaluation-package` 接受有完整有效数据的 `file_query_path_index` 结论为 `supported` 或
`not_supported`，但拒绝 `unavailable`、`failed` 和缺失数据；`file_query_table_ablation`
只作 metadata 固定表消融，不能替代这项竞赛主对照。生成包不等于已有性能优势：
只有真实 campaign、合同复验和 C→E 提交完成后，仓库才有可引用证据；后续 D 只能修改
`README.md`、`docs/**` 与 `evidence/README.md`，不得改包或 INDEX。当前仓库尚未随本文
预先声明任何未产生的正式结果。

## 可选的旧版演示页面

旧入口适合交互演示，不是上述 formal 评价和证据交付的替代品：

```bash
make dual-platform-run TOOLPREFIX=<你的 RISC-V 工具链前缀>
make reader
```

第一条命令运行普通 uCore 对照目标和 AgentOS-uCore 目标并生成预览材料；第二条命令启动
本地页面服务。查看时可打开：

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

`make reader` 默认带上 `--auto-run-llm-relay --llm-relay-mode template`，因此现场演示不依赖网络或密钥。需要验证云端适配时，可显式设置 `LLM_RELAY_MODE=cloud`；LLM 相关 action 会生成复核摘要、方法检查、恢复说明、写作摘要、项目复核意见和最终报告摘要，并刷新相关状态文件。密钥文件不要放入仓库目录，运行脚本和结果文件不会写入密钥内容。
