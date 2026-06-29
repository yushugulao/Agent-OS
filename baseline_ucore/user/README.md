# 用户程序目录说明：uCore

本目录保留 uCore 教学内核的用户态测试和本项目 plain target 的科研 Agent 平台程序。根目录 plain target 不修改 `os/` 内核，科研平台通过这里的普通用户程序运行。

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

- `BASE=1`：只生成未改动 uCore 即可处理的基础测试，名称通常带有 `chxb_` 前缀。
- `BASE=0`：生成该章节的全部测试程序。
- 默认值为 `0`。

## 输出目录

- `target/bin`：生成的 `.bin` 文件。
- `target/elf`：生成的 `.elf` 文件，扩展实验可能使用。
- `asm`：用户程序反汇编输出。

## 本项目相关入口

plain target 的科研 Agent 平台程序位于 `user/src/`，常见入口包括：

- `rp_plain`
- `rp_orch`
- `rp_seed_orch`
- `rp_compare_plain`

这些程序通过普通文件写出 `rp_*` 状态记录，由本地结果阅读器渲染为网页和 API JSON。
