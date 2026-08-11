# Plain uCore 对照程序

本目录包含 uCore 教学测试和 Plain uCore 对照 workflow。对照程序通过普通进程、文件与 IPC 完成同一业务流程，用于和 AgentOS-uCore 的内核服务路径比较。

## 基础用法

生成指定章节用户程序：

```shell
make CHAPTER=5 BASE=1
```

`CHAPTER` 表示要构建的章节程序，可选值包括：

```text
1, 2, 3, 3_2, 3t, 4, 4_3, 5, 6, 7
```

通常不需要手动设置 `CHAPTER`，uCore 构建系统会根据分支或上层 Makefile 指定默认值。本项目运行科研 Agent 平台时，常用 `CHAPTER=platform_plain` 或 `CHAPTER=platform_seeded`。

`BASE` 表示是否只生成基础测试：

- `BASE=1`：只生成基础 uCore ABI 可处理的教学测试，名称通常带有 `chxb_` 前缀。
- `BASE=0`：生成该章节的全部测试程序。
- 默认值为 `0`。

## 输出目录

- `target/bin`：生成的 `.bin` 文件。
- `target/elf`：生成的 `.elf` 文件，扩展实验可能使用。
- `asm`：用户程序反汇编输出。

## 对照 workflow

Plain target 程序位于 `user/src/`：

- `rp_plain`
- `rp_orch`
- `rp_seed_orch`
- `rp_compare_plain`

这些程序通过普通文件写出 `rp_*` 状态记录。`make dual-platform-run` 使用 `host_tools/plain_ucore_fs_extract.py` 按清单提取文件，再由 `host_tools/compare_dual_platform_state.py` 核对 Plain 与 AgentOS 的阶段、文件内容、摘要、程序顺序和退出状态。运行方法见[产品运行指南](../../docs/usage.md)。
