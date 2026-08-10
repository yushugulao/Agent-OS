# 任务三：Context Path

任务三维护 Agent 多轮调用的可信历史、当前 active path 与回滚分支。结构体和
系统调用的完整定义见 [api.md](api.md)，观测架构见 [design.md](design.md)。

## 数据模型

Context 由三个不同职责的部分组成：

- 前 6 页是内核直接更新、用户只读的单拷贝可信视图；
- 第 7 页是用户自管 cache，不能作为授权或 provenance 输入；
- 私有 sidecar 保存完整请求、响应及可信来源归属，按活跃 Agent 分配。

固定容量 archive 保存单调 sequence、分支父节点、cause/span 和完整性 hash。
header 记录窗口边界、active head、丢弃计数和链尾。用户 cache 位于独立区域，
snapshot 不覆盖它，也不把它当作可信历史。

## 提交与回滚

同一 Agent 的 Context 修改进入 FIFO commit lane。append 先预检完整发布范围，
再写 record、详情和 latest，最后发布 header；奇偶 publication sequence 以
release/acquire 包围发布，直接读 helper 只接受前后相同的偶数。工具
调用、手工记录、事件消费和 rollback 共享这条提交顺序。

rollback 不改写或截断旧记录。它验证目标仍在可信窗口内，然后创建新的 branch
generation，并把目标作为新 active path 的锚点。旧分支继续作为不可变 provenance
保留，直到容量淘汰；sequence 永不复用。clear 和失败回滚同样遵守先预检、后
发布，失败不会改变 branch、hash 或窗口边界。

## 查询路径

- `context_direct_header_snapshot()` 和 `context_direct_active_query()` 提供带有界重试的
  并发一致直接读；竞争过强时调用者退回 syscall。
- `context_query()` 有界返回 active path，`context_snapshot()` 一次返回 header
  与当前路径，`context_detail()` 读取仍保留的完整详情。
- timeline 将 Context、审计和调度记录按统一游标归并；filter 只能缩小
  当前 workflow/owner 可见集合。
- wait-and-read 在同一 syscall 内完成条件等待和读取，避免 wait 与 query 之间的
  竞态。

查询扫描受 kernel-work 预算约束，并使用按 sequence/tick 排序的有界索引；计数
查询也不能无预算重扫全表。跨 Agent cause 通过私有 source identity 解释，不能
仅凭公开 pid 或 sequence 混接到另一个 workflow。

## 验证

- `agentfinal_ucore` 验证只读映射隔离、hash 链、提交顺序、回滚和 FIFO 淘汰。
- `agentfinal_ucore` 的同步故障 profile 验证失败发布不覆盖旧记录且后续提交可继续。
- `agentscope_ucore` 验证 scope 裁剪、有界查询和跨 workflow 隔离。
- Evaluation v5 的多轮 workload 记录 rollback 正确率、吞吐、等待分位数和公平性。

实际 Host 与 QEMU 测试命令见 [verification.md](verification.md) 与
[顶层验证说明](../verification.md)。
