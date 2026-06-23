<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# LLM Gateway 与宿主机可视化大屏

本文记录最终成品阶段任务六的宿主机侧方案。当前 Phase 1 已完成事件契约和解析器，Phase 2 已完成云端 LLM Gateway 和离线 fallback，Phase 3 已完成 Node + Vite replay 可视化大屏，Phase 4 已接入 live QEMU 串口事件源。

## 设计目标

当前阶段围绕已有 `labdemo` / `labbench` 输出建立稳定宿主机输入，并在宿主机侧生成结构化 LLM 分析：

- 复用 xv6 串口中的 `agentos:event type=... key=value` 行。
- 普通日志行保留为 raw log，不影响结构化事件解析。
- 解析器输出 JSON event，后续 LLM Gateway 和 Vite 大屏只消费该结构化事件流。
- LLM Gateway 支持 OpenAI-compatible Chat Completions API；DeepSeek、GLM、OpenAI 等兼容服务通过配置切换。
- 缺少 API key、网络失败、云端返回非 JSON 或字段不完整时，Gateway 自动输出 deterministic fallback 分析。
- Vite 大屏通过 Gateway `/api/replay` 和 `/events` 消费 replay 或 live 事件流，展示 Agent 状态、时间线、LLM 分析、恢复报告和性能指标。
- live 模式由宿主机启动 QEMU，自动向 xv6 shell 输入 `labdemo` 和可选 `labbench`，串口输出实时进入 Gateway。
- 不修改 xv6 内核 ABI，也不改变当前用户态演示程序语义。

## 事件契约

`agentos:event` 行采用短文本键值格式：

```text
agentos:event type=TOOL_CALL role=sentinel tool=query_file status=OK seq=1 hits=1 used_index=1
```

Phase 1 解析器支持以下事件类型：

| type | 用途 |
| --- | --- |
| `LAB_INIT` | 初始化演示项目、workflow 和 run id |
| `AGENT_CREATED` | Agent 角色和 pid 创建事件 |
| `WATCH_REGISTERED` | Agent Loop watch 注册事件 |
| `AGENT_STATE` | Agent WAITING/RUNNING 等状态变化 |
| `INCIDENT_CREATED` | 故障注入或异常事件 |
| `TOOL_CALL` | 结构化工具调用结果 |
| `MESSAGE` | Agent 间消息事件 |
| `AUDIT` | 权限拒绝、允许或幂等审计 |
| `ACTION` | Recovery 等 Agent 执行动作 |
| `CONTEXT_SNAPSHOT` | Context Path 快照摘要 |
| `REPORT` | 恢复报告元数据更新 |
| `FINAL` | 综合场景最终状态 |
| `BENCH` | 性能或可靠性指标 |
| `LLM_ANALYSIS` | 宿主机 LLM Gateway 生成的诊断、建议和证据摘要 |

字段值允许包含内部 `=`，例如：

```text
payload=fid=4;status=failed;stage=align;run_id=RUN-042;truncated=0
```

解析器只把空格后出现的新 `key=` 识别为下一个字段，因此上述 payload 会作为一个完整字符串保留。字段值中的普通空格也会被保留到下一个 `key=` 之前，例如 summary 文本。

## LLM Gateway

Phase 2 新增宿主机 LLM Gateway。它读取 parser 输出的事件窗口，汇总项目、故障、Agent 状态、文件查询、依赖分析、恢复动作、报告和最终状态，然后输出统一的 `LLM_ANALYSIS` event：

| 字段 | 含义 |
| --- | --- |
| `mode` | `cloud` 或 `fallback` |
| `provider` | 当前配置的 provider 名称 |
| `status` | Gateway 结果状态，当前成功输出为 `OK` |
| `reason` | fallback 原因，如 `missing_api_key` 或 `cloud_error` |
| `summary` | 面向评审的恢复过程摘要 |
| `root_cause` | 故障根因摘要 |
| `recommended_action` | 建议动作 |
| `risk` | 风险提示 |
| `evidence_refs` | 使用的事件证据引用 |

配置文件示例位于 [../.env.example](../.env.example)：

```text
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MODEL=gpt-4o-mini
LLM_PROVIDER_NAME=openai-compatible
LLM_OFFLINE_FALLBACK=1
```

OpenAI-compatible 的含义是 Gateway 按 `/chat/completions`、`model`、`messages`、`Authorization: Bearer ...` 这一套格式请求模型。只要 DeepSeek、GLM 或其他厂商提供兼容 endpoint，就可以通过 `LLM_API_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL` 接入。

## 宿主机结构

新增宿主机目录：

| 路径 | 说明 |
| --- | --- |
| `host/gateway/parser.mjs` | `agentos:event` 解析器 |
| `host/gateway/llm.mjs` | 云端 LLM Gateway 和离线 fallback |
| `host/gateway/server.mjs` | replay Gateway，提供 JSON API 和 SSE |
| `host/gateway/live-source.mjs` | live QEMU 子进程、串口解析和事件缓存 |
| `host/gateway/live.mjs` | 启动 live Gateway、可选 Vite 大屏和 QEMU |
| `host/gateway/dev.mjs` | 同时启动 replay/live Gateway 和 Vite 大屏 |
| `host/gateway/parser.test.mjs` | parser 单元测试 |
| `host/gateway/llm.test.mjs` | LLM Gateway 单元测试 |
| `host/gateway/dashboard.test.mjs` | Gateway replay API 和 SSE 测试 |
| `host/gateway/live.test.mjs` | live source 模拟串口测试 |
| `host/gateway/replay.mjs` | fixture 回放入口 |
| `host/dashboard/` | Node + Vite replay 可视化大屏 |
| `host/fixtures/labdemo.log` | `labdemo` 样例日志 |
| `host/fixtures/labbench.log` | `labbench` 样例日志 |

根目录 `package.json` 提供当前宿主机命令：

```bash
npm run host:test
npm run host:replay
npm run host:dashboard:build
npm run host:dev
npm run host:live
```

`host:dev` 默认启动两个本地服务：

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| Gateway | `http://127.0.0.1:8787` | `/api/replay` 返回完整 replay JSON，`/events` 提供 SSE 事件流 |
| Dashboard | `http://127.0.0.1:5173` | Vite 大屏页面 |

`host:live` 默认启动 live QEMU 数据源。Windows PowerShell 下默认命令为通过 WSL 进入当前仓库后执行 `make qemu`；Linux/WSL shell 下默认命令为 `make qemu`。可通过环境变量调整：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `HOST_LIVE_COMMAND` | Windows: WSL `make qemu`；Linux: `make qemu` | 自定义 QEMU 启动命令 |
| `HOST_LIVE_RUN_BENCH` | `1` | 是否在 `labdemo` 后继续自动运行 `labbench` |
| `HOST_LIVE_DASHBOARD` | `1` | 是否随 live Gateway 启动 Vite 大屏 |
| `HOST_LIVE_TIMEOUT_MS` | `120000` | live 流程超时时间 |
| `HOST_LIVE_AUTO_EXIT_QEMU` | `1` | 完成后是否发送 `Ctrl-a x` 退出 QEMU |

## 大屏视图

Phase 3/4 大屏在 replay 和 live 两种数据源上使用同一套视图。当前 UI 采用 macOS 式浅色玻璃面板，首屏优先展示 Agent 协作关系，再展示证据和分析：

- 顶部项目、run id、数据源模式、Gateway 状态、事件流状态、LLM 模式和最终状态。
- Agent 协作拓扑：以 `INCIDENT_CREATED` 或 `FINAL` 为中心节点，四个 Agent 节点围绕中心排列。
- 拓扑连线由事件流推导：`MESSAGE` 表示 Agent 间交接，`TOOL_CALL` 表示工具查询，`AUDIT` 表示权限或幂等检查，`ACTION` 表示恢复动作。
- 拓扑旁的最近协作列表保留 3 到 5 条关键动作，减少纯文本日志堆叠。
- 4 个 Agent 角色卡片：Orchestrator、Sentinel、Investigator、Recovery。
- 关键事件流，展示 `INCIDENT_CREATED`、`TOOL_CALL`、`MESSAGE`、`AUDIT`、`ACTION`、`REPORT`、`FINAL` 等事件。
- LLM 分析面板，展示 `summary`、`root_cause`、`recommended_action`、`risk` 和 `evidence_refs`。
- 恢复报告面板，展示恢复报告 artifact、refs 和 seq。
- BENCH 指标面板，展示 `file_scan_query` 和 `duplicate_reject` 等性能/可靠性信号。

## 验证证据

当前测试目标：

- `labdemo.log` 中所有 `agentos:event` 均能解析为 event。
- `labbench.log` 中 `BENCH` 指标能解析为 event。
- 普通 `labdemo:` / `labbench:` 行保留为 raw log。
- 未知 `type` 不导致解析失败，输出 `known=false`，便于后续兼容新增事件。
- fixture 回放能看到 `FINAL status=RECOVERED`。
- 无 API key 时输出 `LLM_ANALYSIS mode=fallback reason=missing_api_key`。
- mock 云端返回合法 JSON 时输出 `LLM_ANALYSIS mode=cloud`。
- mock 网络失败或非 JSON 响应时 fallback，不中断 replay。
- Gateway `/api/replay` 能返回 28 条事件，包含 4 个 Agent、`LLM_ANALYSIS`、`REPORT`、`FINAL` 和 `BENCH`。
- Gateway `/events` SSE 能推送 replay 事件并以 `done` 事件结束。
- Vite 大屏可以成功构建。
- live source 模拟串口测试能解析事件、生成 `FINAL status=RECOVERED` 和 `LLM_ANALYSIS`。
- `npm run host:live` 可启动 QEMU 并捕获 `labdemo` 的 `FINAL status=RECOVERED`。

当前命令：

```bash
npm run host:test
npm run host:replay
npm run host:dashboard:build
npm run host:live
```
