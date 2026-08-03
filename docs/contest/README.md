# AgentOS-uCore 竞赛评审入口

本页面向首次接触项目的评委和复现人员。它只提供可快速核对的导航，不替代完整设计文档，
也不把历史结果或待采集数据写成当前提交的证据。

## 一分钟了解项目

AgentOS-uCore 在 RISC-V 64 uCore 教学内核中加入 Agent 身份与 Context、结构化工具调用、
Context Path、文件对象语义查询、事件驱动 Agent Loop、可信 workflow 生命周期和统一资源控制。
同一科研 Agent 工作流分别运行在根目录增强内核和 `baseline_ucore/` 对照目标上；后者是
共享通用安全加固的对照组，不是未经修改的上游 uCore。

本地赛题说明要求系统在 QEMU 上运行，并交付内核源码、用户态测试、综合示例、设计文档和
运行说明。任务一至三为必做，任务四至六提供进阶和创新评分。实现与要求的逐条对应关系见
[赛题要求追踪表](../agentos/requirements-traceability.md)。

## 建议审阅顺序

| 时间 | 内容 | 入口 |
| --- | --- | --- |
| 3 分钟 | 项目定位、任务一至六完成面 | [根 README](../../README.md)、[要求追踪表](../agentos/requirements-traceability.md) |
| 8 分钟 | 内核边界、关键抽象和机制/策略分离 | [系统设计](../agentos/design.md)、[系统调用与 ABI](../agentos/api.md) |
| 5 分钟 | 科研 Agent 综合场景 | [演示脚本](../agentos/scenario-script.md) |
| 8 分钟 | 双目标实验、统计口径和禁止外推边界 | [评价方法](../evaluation.md)、[验证说明](../verification.md) |
| 3 分钟 | 第三方来源、原创增量和 AI 辅助开发 | [第三方与原创说明](third-party-and-originality.md)、[AI 工具使用披露](ai-usage-disclosure.md) |

## 最短复现路径

在已安装 Bash、Python 3.10+、RISC-V GCC/binutils 和 `qemu-system-riscv64` 的 POSIX 环境中：

```bash
make doctor
make target-readiness
make ci-check
```

这些命令分别检查环境、Host/Reader 合同和静态内核预算，不等于 QEMU 动态验收。典型动态入口为：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

完整发布验收入口是：

```bash
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

Windows/WSL 和 MSYS2 的工具配置见 [Windows 快速开始](../windows-quickstart.md)。同一工作树内
不要并发运行多个 QEMU 测试，因为它们可能访问同一文件系统镜像。

## 当前证据状态

截至本页所在提交，源码、测试合同和评价框架已经进入仓库，但以下事实必须区分：

- [正式证据索引](../../evidence/releases/INDEX.md) 尚未登记当前提交的 release bundle；
- `04c1e6652324` 的三轮未签名本地 E3 校准包只属于该历史提交；当前 18-case 时长状态为 `provisional_requires_full_suite`，冻结候选后必须重新校准；
- 历史提交的日志和校准只证明其绑定提交，不能自动证明当前提交；
- `results/latest/` 和 development Dashboard 是可覆盖预览，不是提交证据；
- 没有可用远程 Runner 时可以形成绑定干净提交的本地 E3，但不能宣称远程 CI/E4；
- 新评价体系已定义真实路径对照和正式科研场景统计，但必须在同一冻结提交上实际运行、复验并
  打包后，才能引用由数据支持的性能结论。

因此，当前正确表述是“机制/静态验收完成，候选动态复验待生成；候选校准与正式发布证据待生成”，而不是“最终验收已通过”。
最新状态应始终以 `evidence/releases/INDEX.md` 及其指向 bundle 的 manifest 为准。

## 提交材料

- [提交前检查清单](submission-checklist.md)
- [AI 工具使用披露](ai-usage-disclosure.md)
- [第三方与原创增量说明](third-party-and-originality.md)
- [材料版本与 SHA-256 清单模板](materials-manifest-template.md)
- [现有视频和幻灯片下载入口](../../项目介绍视频和ppt网盘链接.txt)
- [源码许可证](../../LICENSE)、[文档许可证](../../DOCUMENTATION_LICENSE.md)、[第三方通知](../../NOTICE)

外部网盘链接不能代替版本绑定。最终提交时应下载实际材料、计算 SHA-256，并把文件名、大小、
版本和下载位置写入材料清单；无法随仓库交付的大文件至少要提供稳定链接和校验值。
