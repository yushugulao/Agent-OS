# AgentOS 测试配置

`ci/` 保存 AgentOS 自动测试使用的结构化输入：

- kernel/user UAPI 的 size 与 offset 清单；
- Plain/AgentOS 双目标状态文件白名单；
- 性能负载与操作次数；
- Console 和 Nexus 的确定性 replay 响应及会话脚本。

快速检查 ABI 与模块接线：

```sh
make agent-uapi-check
make agent-module-check
make kernel-stack-check
```

运行 RISC-V64 QEMU Guest 回归：

```sh
make agentos-test
```

定向运行一个产品模块：

```sh
AGENT_TEST_CASE=agentcontract_ucore make agentos-test
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test
```

Runner 检查退出状态、pass marker、panic、输出上限和 timeout。故障注入场景还会检查结构化错误状态与资源回收结果。

Live Query 配对性能活动使用：

```sh
make contest-demo
```

该入口在 `results/contest-demo/` 写入串口输出、`summary.json`、`measurements.csv` 和 `report.md`。完整测试层次见[测试文档](../docs/testing.md)。
