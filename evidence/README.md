# 可复现验收证据

`evidence/releases/` 保存与一个已提交 Git `HEAD` 绑定的最终验收包。`results/latest/`
仍是可覆盖的本地预览，不能替代发布证据。

## 信任边界

默认产物只是绑定已提交 Git 对象的本地验收包，不声称已经过远程 CI。采集器只接受干净
工作区，在 detached `HEAD` worktree 中执行唯一生产入口 `make full-verify`；它不试图防御
已经控制本机、Git 配置或工具链的攻击者。各测试 runner 继续负责 Guest 故障识别、完整行
成功标记和 profile 验证；采集器不会建立第二套 Guest 语义。

`full-verify` 根据实际执行的步骤记录步骤名、耗时和产物，并在全部步骤成功后原子发布
schema v2 的 `verification-summary.json`。summary 不存在就没有 ready 证据；发布失败只会
留下旁路 `.failed` 诊断目录，不会留下目标 release 目录。

远程 CI 是独立、可选的 provenance。`bind-remote-ci` 通过 GitLab API 现场读取同一 commit
的 project、pipeline、job/runner、trace 和 artifact，校验最终 `main` push 及普通/QEMU
Runner 均成功，并把原始 API JSON、trace 和 artifact 连同逐字节 SHA256 写入新的组合包。
此时 manifest 只标为 `provenance-attached`，不声称 provider attestation、密码学不可伪造或
CI artifact 本身不可变；未绑定的本地包固定标为 `not-attached`。

## 采集与验证

代码冻结、提交且工作区干净后执行：

```bash
python3 scripts/capture-final-evidence.py collect \
  --output "evidence/releases/$(date -u +%Y%m%d)-$(git rev-parse --short=12 HEAD)"
```

离线验证文件集合、引用和 SHA256：

```bash
python3 scripts/capture-final-evidence.py verify evidence/releases/<bundle>
```

远程 CI 完成后，现场抓取并绑定到一个新目录，不改写本地原包，也不保存 token：

```bash
python3 scripts/capture-final-evidence.py bind-remote-ci \
  --bundle evidence/releases/<local-bundle> \
  --output evidence/releases/<combined-bundle> \
  --gitlab-url https://gitlab.example \
  --project-id <project-id> --pipeline-id <pipeline-id> \
  --token-file /secure/path/gitlab-token.txt
```

无 QEMU 自测入口：

```bash
make evidence-capture-selftest
```

Agent suite 的迟发故障观察窗口固定为 `2s`；proc、syscall fairness、file、thread、
workflow teardown 和 ENOSPC 六类机制回归固定为 `5s`。普通执行、证据执行和 GitLab
QEMU job 使用相同设置。

## 产物

成功包包含：

- `manifest.json`：提交、唯一命令 argv/环境、返回码、总耗时和来源引用；
- `verification-summary.json`：由实际 full-verify 编排生成的步骤、耗时及原始产物清单；
- `logs/full-verify.log` 与 `logs/raw/`：完整控制台、Reader E2E manifest 及每轮原始
  build/Guest/summary、双 QEMU、Agent Guest、资源机制 Guest 及 dual compare/timing 原文；
- `environment/versions/`：Git、交叉编译器、QEMU、Python、Make、Bash 和 host C
  compiler 的版本输出；
- `configuration/kernel-budgets.json`：本轮提交使用的预算快照；
- `metrics/measurements.csv`：代码量、ELF/raw、text/data/BSS/total、`struct proc`、
  栈及 Agent suite 总耗时，均含 actual、baseline、limit、usage 和来源哈希；
- `metrics/agent-case-timings.csv`、`metrics/commands.csv`：逐 case 计时和唯一验收命令；
- `charts/budget-usage.svg`：同一组 actual/baseline/limit/usage 的可复现图表，直接绑定
  `measurements.csv` 哈希，并保留完整日志与 Agent timing 的来源哈希；
- `checksums.sha256`：除自身外的全部普通文件。

仓库的 `.gitignore` 对 `evidence/releases/**` 有专用负例，因此包内 `.log` 可以正常纳入
版本控制，不需要 `git add -f`。`.gitattributes` 同时将该目录标为 `-text`，避免不同平台的
自动换行转换破坏证据包内逐字节 SHA256。

正式发布前还必须确认普通 Runner 的 plain action/Reader E2E，以及校准 Agent Runner
和机制 QEMU Runner 都在同一最终提交上实际成功。本目录当前不声称已有最终验收包。
