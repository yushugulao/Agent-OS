# AgentOS-uCore 决赛产品文档

本目录是可维护的 XeLaTeX 工程。最终产物固定为：

```text
docs/final-report/AgentOS-uCore-final-report.pdf
```

## 构建

在仓库根目录执行：

```powershell
cd docs/final-report
latexmk -xelatex -interaction=nonstopmode -halt-on-error main.tex
Copy-Item main.pdf AgentOS-uCore-final-report.pdf -Force
```

若本机 `latexmk` 的 Perl 模块不可用，可执行等价的显式流程：

```powershell
xelatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
xelatex -interaction=nonstopmode -halt-on-error main.tex
xelatex -interaction=nonstopmode -halt-on-error main.tex
Copy-Item main.pdf AgentOS-uCore-final-report.pdf -Force
```

清理中间文件：

```powershell
latexmk -C main.tex
```

文档只读取已审核交付的 `../figures/architecture/*.pdf` 和
`../figures/performance/*.pdf`。任一必需图件缺失时 LaTeX 构建直接失败，不生成占位图。

## 证据边界

- 产品与 ABI 事实以当前仓库源码、`docs/agentos/` 和版本化 checker 为准。
- 性能数字只来自 `one_shot_metrics/CANONICAL_RUN` 指向的冻结数据。
- Replay、真实 provider、静态检查、QEMU 功能验证和性能实验在正文中分开陈述。
- 封面使用“AgentOS-uCore 团队”作为团队署名。
