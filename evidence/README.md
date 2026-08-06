# 验收证据

本目录只保存与当前参赛版本直接关联的正式证据。`releases/INDEX.md` 当前没有 release 记录，因此本工作树没有已发布的最终动态结果或性能数据。

## 发布入口

- `releases/INDEX.md`：正式证据包索引。索引为空表示当前提交还没有发布数据。
- `results/latest/`：本地可覆盖的预览，不属于正式证据，也不提交到仓库。

正式包由干净的源码提交 C 生成，再由紧随其后的证据提交 E 加入 `releases/` 和索引。包内保存源码提交、工具版本、原始 Guest 日志、测试命令、指标和离线 Dashboard。GitLab 只负责托管；远端没有 Runner，验收在本地完成。

## 生成与复核

```bash
make full-verify
make evaluation-full-verify
make evaluation-package
make evaluation-package-verify EVALUATION_BUNDLE_DIR=evidence/releases/<bundle>
```

需要先调试评价链时可生成 development 包：

```bash
make evaluation-package-development \
  EVALUATION_RUN_DIR=<run-dir> \
  EVALUATION_BUNDLE_DIR=<output-dir>
```

development 包不会进入正式索引，也不能作为性能结论。

## 发布内容

正式包至少包含：

- 完整测试命令、退出码和工具版本；
- 原始 QEMU Guest 日志及其校验值；
- 任务一至任务六的动态验收数据；
- 延迟、吞吐、尾延迟、内核体积、栈和结构体指标；
- 从同一份数据生成的 CSV、图表和离线 Dashboard；
- 包内文件校验清单。

Dashboard 展示实际数值和样本，不用“通过”卡片代替测量结果。Agent case 与时长 profile 以 [`ci/kernel-budgets.json`](../ci/kernel-budgets.json) 为准，正式实验与 claim 映射以 [`ci/evaluation-suite.json`](../ci/evaluation-suite.json) 为准；证据包必须封存与其源码提交一致的版本。

详细方法见 [评价方法](../docs/evaluation.md) 和 [验证说明](../docs/agentos/verification.md)。
