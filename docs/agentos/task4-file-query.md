# 任务四：Agent Live-Query FS

当前任务四实现是显式、volatile、事件驱动的文件属性查询。它不再扫描普通目录，也不维护 metadata 双 bank、journal 或 crash-recovery catalog。

## 1. 对象模型

一条 `agent_file_meta` 把高层属性绑定到真实文件身份：

```text
physical/logical path
project + workflow + run_id
stage + kind + status + summary
dependency_mask
dev + inum + incarnation + size
fs_generation
```

完整物理身份是 `dev + inum + incarnation`。inode 号被复用后，旧 metadata、内容 receipt、edit lease 或 pending tombstone 都不能命中新对象。

## 2. 显式登记

只有具备 `AGENT_CAP_META_WRITE` 的当前 workflow Agent 能调用 `agent_file_meta_set()`。

- 普通 set：`flags == 0`；
- 删除 metadata：`flags == AGENT_FILE_META_F_DELETE`；
- 更新字段由 `update_mask` 指定；
- 物理文件必须在调用者可见 scope 内，并绑定当前 incarnation；
- `PERSIST` 与 `AUTOSCAN` 为历史 ABI 名称，当前调用携带任一标志都返回 `AGENT_STATUS_BAD_PARAM`。

普通文件 syscall 的 create/rename 不会自动加入 Agent catalog。用户态决定哪些文件值得作为 Agent 属性对象，并在 workflow 启动时重新登记。

## 3. volatile catalog 与索引

catalog 有界驻留内存，按 SYSTEM/动态 scope 约束可见性。选择性索引只覆盖最常用的等值字段：

- `status`；
- `stage`；
- `kind`。

查询可以强制全表扫描，也可以请求索引计划。索引没有合适 key、无效或不能保证语义时退化为 scan。索引路径每次仍遍历候选链并重新应用完整谓词，不缓存上一条 query result。

结果返回 total hits、实际复制数、扫描/候选数、plan/reason、索引 bucket、重建访问量、是否截断、ticks 和当前 `fs_generation`。最多复制 8 条 hit。

## 4. VFS 增量投影

VFS 不负责发现新 metadata，只维护已显式绑定对象的实时性：

- unlink/deferred reclaim 产生带完整 lifecycle/scope/dev/inum/incarnation 的 tombstone；
- write/truncate 的 content receipt 更新 size/generation；
- incarnation 或 scope 不一致时拒绝更新，避免旧 pending 污染新对象；
- 后台每轮用有界 budget 排空 tombstone/content pending；
- workflow fence 在 metadata transaction 内排空本 scope 全部 pending。

这条路径没有目录轮询，没有把每个普通 inode 强制物化为 Agent metadata。

## 5. 查询谓词

`struct agent_file_query` 支持固定字段等值和 `summary_contains`：

| 字段 | 含义 |
| --- | --- |
| `physical_name` / `logical_path` | 真实名称与逻辑路径 |
| `project` / `workflow` / `run_id` | workflow 高层归属 |
| `stage` / `kind` / `status` | 选择性索引候选字段 |
| `summary_contains` | 有界 substring 过滤，不能使用索引代替完整匹配 |
| `max_hits` | 0 到 8 |

空字符串表示该字段不限制。查询结果只反映调用时可见的显式 catalog，不声称覆盖文件系统中的全部普通文件。

## 6. Typed live watch

### 6.1 安装

`agent_live_watch(struct agent_file_live_watch *)` 安装完整 query 谓词。成功后内核回填：

- `watch_id`；
- `initial_generation`；
- `catalog_generation`；
- 未确认缺口的 `resync_generation`；
- 必要时 `RESYNC_REQUIRED` flag。

typed watch 复用 watch slot 数量预算，但 token 与 query 保存在 generation-safe subscription 表中。filter 不会回退到 legacy 字符串 substring 解析。

### 6.2 transition

每次显式 metadata 或已绑定文件内容变化都比较 before/after：

| before | after | change |
| --- | --- | --- |
| 不匹配 | 匹配 | `ENTER` |
| 匹配 | 匹配且记录变化 | `UPDATE` |
| 匹配 | 不匹配/删除 | `LEAVE` |

事件类型为 `AGENT_EVENT_FILE_QUERY`，payload 带 `change=...` 和 lifecycle `id:generation`。事件进入 Agent Queue/Context，从而直接驱动 Agent Loop；它不是 Host 轮询通知。

### 6.3 可见性

订阅目标必须是活跃 Agent，并绑定 control id、scope 和 lifecycle generation。动态 scope 只能看到 SYSTEM 及同 workflow 可见对象；跨 scope transition 不投递。exec/exit/proc slot reuse 会清除 subscription。

## 7. RESYNC_REQUIRED

有界队列无法可靠承诺每条增量时，系统不伪装为完整：

1. 为受影响 domain 记录单调 `resync_generation`；
2. 合并同一目标的 pending resync；
3. 尝试投递 `RESYNC_REQUIRED`；
4. 用户重新执行完整 query/snapshot；
5. 用户以 `ACK_RESYNC` 和该 generation 安装/删除 watch；
6. ACK 只清除 `<= generation` 的缺口，新缺口不会被旧 ACK 擦除。

domain 表耗尽时升级为 global resync generation，而不是静默丢失。workflow fence 若仍发现本 scope resync pending，则返回 `RETRY`，不发布不完整 metadata cut。

## 8. 与 Agent Context 的关系

live-query 事件沿 Agent IPC 路径进入 Context，继承内核分配的 event id、tick、source/target 和 workflow generation。Agent 在收到 `ENTER/UPDATE/LEAVE` 后仍应按需要调用 `agent_file_query()` 读取当前对象；事件只表示集合 transition，不携带完整 metadata 快照。

对于 `RESYNC_REQUIRED`，事件本身不能作为“当前集合完整”的证明。只有重新 query 并 ACK 对应 generation 后，才能继续依赖增量通知。

## 9. 内容摘要与编辑租约

现有内容摘要和编辑租约仍绑定 `dev + inum + incarnation`：

- digest 是有界文件内容读取/指纹工具，不是全文搜索索引；
- edit begin/commit/abort/state 提供冲突检测和过期处理；
- metadata update 不把预先创建的 public inode 自动升级为 workflow 文件；
- file capability 与 VFS scope 检查仍在每次敏感操作执行。

这些能力与 volatile metadata 配合，但不会把 metadata catalog 写盘。

## 10. fence 语义

workflow fence 对任务四只承诺：

- metadata transaction 已取得 quiescent generation；
- 本 scope tombstone、content pending 已被尝试完全排空；
- 已标记的 resync 已投递/确认到可切割状态，否则返回 `RETRY`；
- receipt 设置 `METADATA_VOLATILE`。

它不承诺 metadata 重启恢复，也不把普通文件内容纳入 evidence root。

## 11. 明确停产项

| 历史机制 | 当前状态 |
| --- | --- |
| `.agentmeta` / `.agentmeta1` 双 bank | 不在生产构建合同中 |
| metadata journal/checkpoint/mirror | 不在生产写路径中 |
| root directory autoscan | 不执行 |
| crash-recovery catalog | 不提供 |
| `PERSIST` / `AUTOSCAN` set | `BAD_PARAM` |
| 普通文件自动加入 metadata | 不发生 |

仓库中若保留历史源码或 ABI 名称，仅用于迁移参考/编号兼容，不能作为当前能力证据。

## 12. 设计来源

Haiku BFS 的显式属性、选择性属性索引和 live query 提供了概念启发。AgentOS 的变化在于把 typed transition 直接送入 Agent Context，并以 workflow generation、scope 可见性、bounded queue 和 generation resync 形成内核合同。实现为 clean-room 代码，没有复制 BFS 源码、数据或磁盘格式。

## 13. 验证

```bash
python -B scripts/check-agent-live-query-fs.py
python -B scripts/test-agent-live-query-fs.py
python -B scripts/test-workflow-fence.py
make agent-module-check TOOLPREFIX=riscv-none-elf-
```

性能结论必须来自 `agentbench_ucore` 等真实 Guest 测量，不能由索引结构或静态 checker 推导。
