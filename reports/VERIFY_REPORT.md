# 验证和验收报告

## 结论

**PASS。** 决赛文档、图表、数据证据和当前实现边界已经完成最终验收。命名交付物为
`docs/final-report/AgentOS-uCore-final-report.pdf`，共 72 页，SHA-256 为
`92f18709034d7f4d8886b7167c0c55a18c56b8ff48c6d02b6586ff7d1feeb298`。

## 检查项

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| 章节结构 | PASS | `main.tex` 的 19 个 `input` 全部存在；任务一至任务六、Nexus、验证、限制、交付、总结和两篇附录顺序正确 |
| 图表引用 | PASS | 3 张架构图和 7 张性能复合图全部存在并被正文引用；共 10 个 `ReportFigure`，缺图会触发 `PackageError` |
| 数值一致性 | PASS | Task 中位数顺序、core/E2E、catalog grid、EEVDF probe 与 Jain 数值均与规范长表一致 |
| 文本门禁 | PASS | 无 TODO、TBD、FIXME、PLACEHOLDER、待补充、待续写和示例数据；源码路径与职责已复核 |
| Markdown 链接 | PASS | 33 个 Markdown 文件、283 个本地链接，缺失 0，绝对本机链接 0 |
| LaTeX 编译 | PASS | `latexmk -xelatex -interaction=nonstopmode -halt-on-error main.tex` 成功；undefined reference/citation、overfull 和 BibTeX warning 均为 0 |
| PDF 结构 | PASS | 72 页 A4、PDF 1.5、未加密；`qpdf --check` 无语法或流编码错误 |
| PDF 视觉检查 | PASS | 72 页全部以 144 dpi 渲染；无空白页、裁切、重叠、缺字、乱码、黑块或异常页尺寸 |

## 结构与事实

- Task 5 章节标签正确，任务一至任务六的编号和入口顺序一致。
- `contest-demo` 是同一 AgentOS Guest 内的 traversal/indexed 固定配对基准；`dual-platform-run` 负责 Plain uCore 与 AgentOS-uCore 双目标合同对齐。
- Nexus specialist 拥有独立 PID、Agent/control identity、Context 和 role/capability，并共享 workflow lifecycle、scope、资源域、账户与调度实体。
- `labdemo_ucore` 的 `kind=fence` 是性能计数稳定快照；真实 320-byte workflow fence receipt 由 `rp_agentos_orch` 在双目标路径中生成。
- `agentos-nexus` 默认走 DeepSeek/live provider；strict replay 使用独立的 `agentos-nexus-replay`，observer 也由单独入口启动。

## 数据与图表

规范数据集包含 30 次 fresh QEMU boot、33 个 raw 文件、19 张长表和 7,498 行记录。
`manifest.json` 的最终 SHA-256 为
`0d82db3537300545b45259b492240893dc9c4768745cdc5be3d3566215c12704`；
145/145 个 inventory 文件的尺寸和 SHA-256 已逐项复核，`COMPLETED` 与 manifest 匹配。
公开发布脱敏只移除了 hostname、绝对 workspace 和临时 staging 标识，raw 文件未修改，
raw set SHA-256 保持为
`846c4a9b12f5779ac8dd8e51c3650b4cddc6c04c6d80bae0fdf9a8ca19f36306`。

## 编译与视觉检查

当前 Windows 环境先将 TeX Live 自带 Perl 置于 `PATH` 首位，再运行 `latexmk`。最终日志中
undefined reference/citation、overfull box 和 BibTeX warning 均为 0，仅保留 2 条不影响
版面的 underfull vbox 提示。命名 PDF 的 72 页均为 `595.28 x 841.89 pt` A4；144 dpi
栅格页统一为 `1191 x 1684 px`，程序检查未发现空白页、内容触边或替换字符。

视觉检查覆盖封面、阅读提示、目录、插图/表格目录、三张架构图、七张性能复合图、
安全威胁表、验证矩阵、附录源码长表、复验命令、核心数字表和参考文献。全页 contact
sheet 与重点页原尺寸复核结果一致，原有两张仅含页眉页码的空页已消除。

## 测试证据

本次仓库收口已通过 `make agent-module-check`、RV64 `make build`、
`make local-host-selftests`（93/93）和 `make agentos-nexus-check`（31+15+59+9）。相关专项
门禁包括 execution contract 90/90、Task Channel 70/70、generation-index 18、
file-version/sparse 12、live-query 29，共 158 项相关回归通过。严格 Nexus QEMU replay
完成 11 个 request digest、3 个 turn、4 个业务 identity、失败后重规划、发布拒绝零副作用
和正常关闭后的静默检查。

## 证据边界

严格 replay 证明固定 fixture 沿真实 QEMU、Guest、内核、串口和 Host 协议路径闭环，
不等于 live provider 成功，也不评价云模型质量。本文没有声称本轮完成 DeepSeek live
调用。性能结论只适用于记录的源码提交、QEMU/Host 环境、负载、样本和测量窗口。

## 仍需处理的问题

无硬错误。live provider 仍依赖现场网络、API key、供应商服务和模型行为；这属于已披露
运行条件，不影响 strict replay、确定性基准和当前决赛文档的可复验性。
