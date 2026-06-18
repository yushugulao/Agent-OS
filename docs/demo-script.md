# 演示脚本

本文用于现场评审或录制演示视频。当前脚本覆盖任务一至三最终高性能成品，任务四至六完成后需要继续扩展。

## 1. 开场说明

说明项目定位：

- 基于 xv6-riscv 的 Agent-OS 内核扩展。
- 当前重点完成任务一至三：Agent 进程、批量结构化内核工具调用、Context Path。
- 设计目标是让 Agent 进程通过 `agent_run` 批量调用内核工具，直接读取 Context 镜像，并在需要可信历史时使用 `context_snapshot` 读取内核 shadow 权威记录。

## 2. 构建和启动

在 Linux/WSL2 Ubuntu 中执行：

```bash
cd project61-agentOS-happylegend
make fs.img
make qemu
```

进入 xv6 shell 后看到 `$` 提示符。

## 3. 最终功能演示

执行：

```sh
agentfinal
```

讲解点：

- 普通父进程通过 `agent_create` 创建 Agent 子进程。
- Agent Context 为 4 页，大小 16384 字节，Context Path 容量 128 条。
- `agent_run` 一次批量执行 64 个结构化工具 op，sequence 连续。
- Context Path 保存 128 条短文本摘要路径，record 中包含 payload/result 摘要。
- `context_snapshot` 一次返回 header 和可见路径记录，并刷新被用户态改脏的 Context 镜像。
- `direct_dirty_before_snapshot=1` 表示直接读路径是高速镜像，`tamper_protected=1` 表示可信历史不被镜像篡改污染。
- 192 次 op 后 FIFO 淘汰正确：`oldest=65 latest=192 dropped=64`。
- snapshot 和用户态直接读 Context 的记录一致。

## 4. 性能演示

执行：

```sh
agentbench
```

讲解点：

- `scalar_run` 是一次 syscall 执行一个 op。
- `batch_run` 是一次 syscall 执行 64 个 op，展示端到端吞吐提升。
- `direct_context` 展示用户态直接读 Context 的低开销。
- `context_snapshot` 展示一次 syscall 批量返回 128 条路径记录。

## 5. 当前边界和后续计划

- 现在是参照任务一、任务二、任务三做了一个比较高性能的demo，之后我们要实现任务四以后的几个任务，回过头来也要改改当前成果，为后续任务作相应适配。
- Context Path 当前是固定容量短文本摘要历史，不是完整 raw 请求/响应日志。
- `query_file` 只是文件元数据查询，不等于任务四完整文件系统查询优化。
- 任务五 Agent Loop 目前只有字段和调用状态预留，还没有完整心跳/等待/唤醒。
- 任务六综合场景尚待设计，后续可围绕系统管理员 Agent 或文件检索 Agent 展开。

## 6. 退出 QEMU

按：

```text
Ctrl-a x
```
