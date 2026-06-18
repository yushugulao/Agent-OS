# Agent-OS API 说明

## 公共常量

核心常量定义在 `os/agent.h` 和 `user/include/agent.h`。

| 名称 | 当前值 | 含义 |
| --- | ---: | --- |
| `AGENT_CONTEXT_PAGES` | 4 | 每个 Agent 的 Context 页数 |
| `AGENT_CONTEXT_SIZE` | 16384 | 每个 Agent 的 Context 总字节数 |
| `AGENT_CONTEXT_MAX_RECORDS` | 128 | Context Path 可见记录数 |
| `AGENT_BATCH_MAX` | 64 | `agent_run()` 单次最大操作数 |
| `AGENT_PAYLOAD_SIZE` | 64 | 工具 payload 字节数 |
| `AGENT_RESULT_SIZE` | 96 | 工具结果文本字节数 |
| `AGENT_CONTEXT_TEXT_SIZE` | 16 | Context record 中短文本摘要长度 |

## 返回码

| 返回码 | 含义 |
| ---: | --- |
| `AGENT_STATUS_OK` | 成功 |
| `AGENT_STATUS_BAD_REQUEST` | 请求整体格式错误 |
| `AGENT_STATUS_UNKNOWN_TOOL` | 工具 ID 或名称不存在 |
| `AGENT_STATUS_NOT_AGENT` | 当前进程不是 Agent |
| `AGENT_STATUS_BAD_PARAM` | 参数键、类型、数量或取值错误 |
| `AGENT_STATUS_NOT_FOUND` | 历史节点、文件或事件不存在 |
| `AGENT_STATUS_NO_SPACE` | Context 布局或空间不可用 |
| `AGENT_STATUS_TIMEOUT` | 等待事件超时 |
| `AGENT_STATUS_DENIED` | 权限检查拒绝 |
| `AGENT_STATUS_DUPLICATE` | 重复恢复动作 |

## Agent 生命周期

```c
int agent_create(void);
int agent_info(struct agent_info *info);
```

`agent_create()` 创建一个 Agent 子进程，父进程获得子进程 pid，子进程从返回点继续运行并带有 Agent 元数据。

`agent_info()` 返回当前进程是否为 Agent、Context 基址、Context 大小、调用次数、Context 记录数、最新 sequence、事件等待统计等信息。普通进程调用时会得到 `is_agent = 0`。

## 批量工具调用

```c
int agent_run(struct agent_op *ops,
              struct agent_result *results,
              int count,
              uint64 flags);
```

`agent_run()` 是当前主接口。`count` 必须在 `1..AGENT_BATCH_MAX` 范围内。返回值等于实际处理的操作数；如果用户指针错误或 batch 参数整体非法，返回 `-1`。

每个 `agent_op` 至少包含：

- `tool_id`
- `arg0`
- `arg1`
- `payload`

每个 `agent_result` 至少包含：

- `status`
- `sequence`
- `value0`
- `value1`
- `result`

工具执行失败通常只影响对应 result，不会让整个 batch 失败。这样可以在一个 batch 中同时看到成功和失败的结构化结果。

## Context Path

```c
int context_push(struct agent_context_record *record);
int context_query(uint64 start_sequence,
                  struct agent_context_record *out,
                  int max);
int context_snapshot(struct agent_context_header *header,
                     struct agent_context_record *records,
                     int max);
int context_rollback(uint64 sequence);
int context_clear(void);
```

`context_push()` 手动追加一条记录。工具调用自动追加记录。

`context_query()` 从指定 sequence 开始按时间顺序返回可见记录。`start_sequence = 0` 表示从当前最早可见记录开始。

`context_snapshot()` 一次返回 header 和有序 records，是推荐查询接口。调用前内核会先把 kernel shadow 同步到用户镜像。

`context_rollback()` 将 Context Path 回滚到指定可见 sequence。找不到 sequence 时返回 `AGENT_STATUS_NOT_FOUND`。

`context_clear()` 清空当前 Agent 的 Context Path。

## 文件元数据接口

```c
int agent_file_meta_init(void);
int agent_file_meta_set(struct agent_file_meta *meta);
int agent_file_query(struct agent_file_query *query,
                     struct agent_file_query_result *result);
```

`agent_file_meta_init()` 安装演示用文件元数据，并重建索引。

`agent_file_meta_set()` 新增或更新一条文件元数据。文件状态变化会触发匹配的 Agent watch。

`agent_file_query()` 支持按 status、stage、kind、project、workflow 等条件过滤。命中 status、stage 或 kind 条件时优先使用索引链。

## Agent Loop 接口

```c
int agent_watch(int event_type, const char *filter);
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_heartbeat(int interval_ticks);
int agent_wake(int pid, struct agent_event *event);
```

`agent_watch()` 注册监听条件。`filter` 是简单包含匹配字符串。

`agent_wait()` 等待一个匹配事件。成功返回 `AGENT_STATUS_OK`，超时返回 `AGENT_STATUS_TIMEOUT`。

`agent_heartbeat()` 设置心跳间隔。时钟中断会更新 Agent 心跳统计。

`agent_wake()` 向指定 Agent 投递事件。

## Legacy 接口

```c
int agent_call(struct agent_request *req,
               struct agent_response *resp);
int agent_tool_list(struct agent_tool_desc *out, int max);
```

`agent_call()` 保留为兼容接口，内部转换到当前工具执行路径。最终测试和性能展示使用 `agent_run()`。

`agent_tool_list()` 返回工具表，用于展示工具名称、参数说明和描述。
