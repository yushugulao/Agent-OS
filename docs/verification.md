# 构建与验证说明

## 依赖

已验证环境：

- WSL2 Ubuntu 26.04
- `riscv64-linux-gnu-gcc`
- `riscv64-linux-gnu-objdump`
- `qemu-system-riscv64`
- `make`

如果安装的是 `riscv64-unknown-elf-` 工具链，可以把命令中的 `TOOLPREFIX=riscv64-linux-gnu-` 替换为 `TOOLPREFIX=riscv64-unknown-elf-`。

## 构建命令

```bash
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

## 最终验收命令

建议用 `INIT_PROC` 分别启动三个最终程序，避免手动 shell 输入影响复现。

可直接运行：

```bash
bash scripts/run-agent-tests.sh
```

也可以手动运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
```

## 通过标准

构建通过：

- `make user nfs/fs.img`
- `make build`

运行通过：

- 没有 kernel panic。
- `agentfinal_ucore` 输出 `agentfinal_ucore: passed`。
- `agentbench_ucore` 输出 `agentbench_ucore: passed`。
- `labdemo_ucore` 输出 `labdemo_ucore: passed`。

## 关键检查点

`agentfinal_ucore` 应展示：

- Context 大小 16384 字节。
- Context 容量 128 条。
- 批量调用 sequence 连续。
- 短文本历史可查询。
- 用户态篡改 Context 镜像后，snapshot 仍返回内核权威内容。
- FIFO 淘汰后的 oldest/latest/dropped 正确。
- 文件查询使用索引。
- Agent wait/wake 成功。

`agentbench_ucore` 应展示：

- batch run 比 scalar run 吞吐更高。
- direct context read 比 syscall 查询更快。
- context snapshot 比逐条 query 更适合批量读取历史。
- index query 有明确的统计输出。
- event wait/wake 能稳定完成。

`labdemo_ucore` 应展示：

- 三个 Agent 创建成功。
- sentinel 注册文件失败事件监听。
- 注入失败后收到事件。
- investigator 完成原因和依赖查询。
- recovery 经过权限检查后执行恢复。
- 重复恢复被识别为 duplicate。
- 最终状态为 recovered。

## 关于性能数字

`agentbench_ucore` 使用内核 tick 计时。QEMU、宿主机负载和日志等级都会影响绝对数字，因此文档和答辩中不应承诺固定 tick 阈值。更稳妥的说法是：

- 批量 syscall 减少内核入口次数。
- 直接读 Context 减少查询 syscall。
- snapshot 一次返回多条记录，避免逐条查询。
- 文件索引路径减少不必要扫描。

最终以 `agentbench_ucore: passed` 和输出中的相对趋势作为性能证据。
