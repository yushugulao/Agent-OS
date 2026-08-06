# 可复现验收证据

`evidence/releases/` 保存与一个已提交 Git `HEAD` 绑定的最终验收包。`results/latest/`
仍是可覆盖的本地预览，不能替代发布证据。

正式竞赛评价包也统一发布到 `evidence/releases/evaluation-<run-id>/`，复用本目录唯一的
append-only `INDEX.md`，不再建立第二套 release 根或索引。评价包验证器允许当前 HEAD 是
唯一证据提交 E 的文档后代：它从历史定位唯一引入该包的 E，仍要求 E 的唯一父提交为 C、
C..E 只新增该包普通文件并精确追加 INDEX。后续 D 只允许修改 `README.md`、`docs/**`
和 `evidence/README.md`，INDEX 必须与 E 完全一致；其他代码后代、INDEX 变化或包字节改写
都会失败。传统
`capture-final-evidence.py` 包继续保持原有“当前 HEAD 就是 E”的严格合同。

赛题要求、实现边界与证据等级口径见
[要求追踪表](../docs/agentos/requirements-traceability.md)和
[验证说明](../docs/agentos/verification.md)。代码提交 C 本身
不得预置声称由 C 生成的发布包；实际发布状态以 append-only `releases/INDEX.md` 和各包内
manifest 为准。通过 C→E committed delivery 合同及完整离线复验的本地包是唯一
正式交付。GitLab 只托管源码和已提交证据，不配置 Runner；包内兼容字段
`remote_ci.status` 固定为 `not-attached`。

## 评价包存储合同

评价包的 raw 工件按 micro boot、scenario boot 控制面和 scenario boot/target 封装为独立的
确定性 `gzip+USTAR` 分片；formal 与 development 包使用相同机制，不使用 Git LFS。manifest
同时绑定每片的 stored path/SHA256/bytes、压缩协议、成员数与 raw/stored 总量，并为每个成员
绑定 logical path/raw SHA256/bytes。写入器固定词典序、canonical gzip header、
`mtime/uid/gid=0`、USTAR 普通文件元数据和 `0644` mode；stored SHA256 固定实际提交的
压缩字节，logical SHA256 固定解压后的成员内容。

验证时归档始终是不可信输入，只能在权限收紧的私有临时目录逐成员物化。绝对路径、`..`、
重复成员、拼接 gzip member、symlink/hardlink、设备、目录等非规范类型，以及超过深度、
成员数、单成员、总展开量或压缩比预算的输入均 fail closed。验证器检查 canonical gzip
header、USTAR 语义、stored archive hash 和逐成员 logical hash，再重放完整验收合同；它不要求
当前 zlib 重新压缩后逐字节复现原 DEFLATE 数据流，因而不同合规压缩器不会制造伪失败。
scenario raw 只接受 plan/report raw-source receipt 明确绑定的 canonical inventory，未知或临时文件
不能自动升级成 generic evidence。最终 committed delivery 另从 Git tree/blob 实测并限制为最多
1000 个 tracked 普通文件、单 blob 64 MiB、总 blob bytes 256 MiB，不信任 manifest/worktree
报告的尺寸。

formal 评价包携带版本化 `measurement-source-receipt.json` 和策略清单覆盖的源码快照，
并在 portable 复验时重放微基准、任务六源码合同和评价控制面清单；committed verifier
再要求 receipt、快照和源码提交 C 中相应 Git blob 一致。formal run id 固定为
`formal-<C 的完整 40 位提交号>`，challenge 和执行顺序由 C 确定性派生；同一输出根保留
失败目录且拒绝覆盖。本地机制不声称能证明操作者或其他 clone 的诚实性。题面必做的 Task 4 性能门由 suite 的
`competition_claims.task4` 显式绑定到 `file_query_path_index`：它必须有完整有效数据，
claim 可以诚实地是 `supported` 或 `not_supported`，但 `unavailable`、`failed` 或缺失状态
不能发布为 formal 证据。`file_query_table_ablation` 只作机制消融，不能替代这项逐路径对照。
证据完整性与“结果支持优势”是两个独立判断。

## 信任边界

默认产物是绑定已提交 Git 对象的本地验收包。采集器只接受干净
工作区，在 detached `HEAD` worktree 中执行唯一生产入口 `make full-verify`；它不试图防御
已经控制本机、Git 配置或工具链的攻击者。各测试 runner 继续负责 Guest 故障识别、完整行
成功标记和 profile 验证；采集和离线复验还会通过共享语义注册表重放同一批日志 validator，
核对精确产物清单、runner/Guest 分段、事实 marker、多启动顺序以及二进制证据。因而只有
`passed` 字样、缺少分段 Guest 原文或事实 marker 的伪日志不能成为 ready 证据。

metadata genesis 的信任根是受控 mkfs 生成的 raw 文件系统镜像，以及普通进程不可访问的
`KERNEL_PRIVATE/SYSTEM` 存储路径。raw genesis parser、checksum 和 hash 用于检测布局/内容
损坏并绑定对象身份；它们不是带密钥的 MAC、数字签名或工具链与供应链认证。已经控制构建机、
mkfs 输入、镜像发布链或宿主工具链的攻击者仍处于本证据模型之外。

runner 持久化的 `*.guest.log` 是 UTF-8、LF 换行的 canonical Guest transcript；串口产生的
CRLF 或裸 CR 在写入该文件前归一化。终端 stdout/raw console 是传输诊断流，可能保留 CR、
ANSI 控制序列或宿主显示差异，不能替代 canonical transcript。exact-line marker、日志 SHA256、
派生 CSV 和 manifest 引用均绑定 canonical 文件；需要复盘传输时才另外查看 raw console。

`full-verify` 根据实际执行的步骤记录步骤名、耗时和产物，并在全部步骤成功后原子发布
schema v8 的 `verification-summary.json`。summary 内的步骤契约使用规范 JSON 重算哈希，
离线验证不依赖未交付的临时文件。summary 不存在就没有 ready 证据；发布失败只会留下
旁路 `.failed` 诊断目录，不会留下目标 release 目录。

GitLab 不执行验收、不产生 pipeline 证明或第三方签名。正式信任链止于本地干净 C 上的
运行日志、内容寻址包、离线语义复验与 C→E Git 交付关系。

## 采集与验证

发布采用两个提交。代码提交 C 先冻结内核、用户程序、runner、预算和验收文档；采集器只在
干净 C 上运行并让 manifest 绑定 C。证据提交 E 必须是以 C 为唯一父提交的直接子提交；
`git diff C..E` 只允许新增一个 `evidence/releases/<bundle>/` 中的普通文件，并对
`evidence/releases/INDEX.md` 追加采集器生成的唯一一行。E 不允许顺带修改其他文档、内核、
用户程序、runner 或测试，也不允许 rename/copy/delete、symlink 或子模块。manifest 的
`delivery` 将 `source_commit` 固定为 C，并以 `SELF` 表示必须解析为实际承载该包的 E；任何
越界改动都必须形成新的 C 并重新采集。

代码冻结、提交且工作区干净后，优先以
`AGENT_TEST_DURATION_PROFILE=local-e3 make evaluation-full-verify` 建立并复核受信原生 MSYS2
E3 child domain。下面的直接 collector 命令只应在该已验证 child domain 中执行：

```bash
python3 -I -S scripts/trusted-python-entry.py scripts/capture-final-evidence.py collect \
  --agent-test-duration-profile local-e3 \
  --output "evidence/releases/$(date -u +%Y%m%d)-$(git rev-parse --short=12 HEAD)"
```

当前校准状态若仍为 `provisional_requires_full_suite`，`local-e3` 必须在 QEMU 前 fail closed，
不得借用历史 baseline/limit。普通 Linux 或 WSL 本地环境应显式选择
`--agent-test-duration-profile none`；它仍保存完整 18-case、语义、Guest 日志和 timing 行清单，
但 manifest 中的本地 duration baseline/limit/ratio 明确为不适用，不构成本地 E3 时长证明。

聚合命令有 5 小时的进程组级硬上限；各 case 和 runner 仍使用更短的独立 deadline。
该总上限只为容纳串行 recovery/allocator 矩阵，不能把无期限挂起误当成慢速成功；实际采用的
上限同时写入 manifest 和 command CSV，离线复验要求两处一致。

历史提交 `04c1e6652324` 曾由 `scripts/agent_test_calibration.py collect` 在对应 clean detached
worktree 上串行执行三轮完整 18-case。样本为 `287.9945528s`、`283.0201263s`、
`280.9651484s`，中位基线 `283.0201263s`，确定性上限 `297.172s`；71 个文件保存在
`evidence/calibrations/04c1e6652324/`，这些数值仅适用于该提交和源码指纹。

历史提交 `14607e825f06c5ffe4a69dd992dbe79b210ab8a4` 已采集三轮完整
18-case，样本为 `278.6982115s`、`294.053138s`、`296.7989493s`，中位基线
`294.053138s`，确定性上限 `308.756s`。71 个文件保存在
`evidence/calibrations/14607e825f06/`，其中包含 57 份执行 attestation；manifest SHA-256 为
`52c5a80d5fc5e3c230922e1cbbd6fc3522cdabe3dfac443f71e4a4c429fbc788`，受管源码指纹为
`58bc32576b47f776ec49325b71bb7c5c04e1c8c6a8ccd44a446becee57bf458d`。timing 从 commit/tree、随机 nonce、可执行文件身份、
镜像/Guest 日志哈希、真实 monotonic 区间和退出结果绑定的 attestations 重建。校准包只标记为未签名本地 E3 复现证据，
不是 release bundle，也不是第三方执行证明；它证明包内字节可重放和来源绑定，不能
证明掌控本机 checkout、工具与输出的操作者诚实。production collector 没有公式/fixture 通道，
测试 fixture 也不得进入正式证据。采集会逐 blob 核对 clean detached worktree，并严格匹配独立的
`local-e3-msys2-xpack-qemu11-v1` 工具与主机 profile；更换 case、提交、硬件、虚拟化层、QEMU
或工具链可执行文件后必须重新校准。当前预算配置为 `provisional_requires_full_suite`，不复用该历史包的
baseline、limit、samples 或源码指纹；最终冻结提交必须重新完成三轮完整校准。`ef0f77edee83`、`814021ab9dac`
等旧包也只保留为历史记录。

离线验证文件集合、引用和 SHA256：

```bash
python3 -I -S scripts/trusted-python-entry.py scripts/capture-final-evidence.py \
  verify evidence/releases/<bundle> --contract-root <clean-checkout-of-C>
```

上式验证独立包的字节、引用和语义，但尚不能证明它已经按 C→E 规则提交。提交 E 并保持
工作区干净后，还必须验证 Git 图、树对象和精确 diff allowlist：

```bash
python3 -I -S scripts/trusted-python-entry.py \
  host_tools/evidence_delivery_contract.py verify-committed \
  --bundle evidence/releases/<bundle> --repo-root .
```

当前 formal producer 和 portable verifier 固定使用 full-evidence v8；portable 入口不会把旧包
升级为当前证据。`evaluation-package` 另行使用 evaluation-bundle format v6。committed verifier
为复验已经提交的历史证据，兼容 evaluation-bundle v5/v6 与完整的 full-evidence v6/v7/v8，并按各自
格式执行严格字段与 Git 关系校验。浮点、布尔、字符串版本以及未发布版本一律拒绝，避免生产
入口和复验入口再次发生版本漂移。

无 QEMU 自测入口：

```bash
make evidence-capture-selftest
```

Agent suite 的迟发故障观察窗口固定为 `2s`；proc、syscall fairness、file、thread、
physical page、observation recovery、VirtIO fault、workflow teardown 和 ENOSPC 等自然完成的
机制回归使用 `5s` 观察窗口；metadata powercut 在完整 marker 后立即执行信号合约，grace
固定为 `0s`。普通执行和证据执行对同一 profile 使用相同设置。
metadata recovery 的完整 suite 计划执行 45 次 Guest 启动：primary/mirror 各八个 COW phase 的
powercut/recovery 配对、authority 基线、双目标 `BUSY/EIO/INTERRUPTED` 同启动重探、
header-flush EIO，以及超过 background burst 的 32-record bank 在三类 terminal peer 下的
同启动恢复；每次恢复前先保存并校验原始 bank。动态 metadata 覆盖不能由该计划、源码接线或
工作区外日志推断；只有 `releases/INDEX.md` 选中包的 recovery step 成功，且包内完整日志、
逐 boot 身份和恢复前 raw-bank 均通过共享语义及字节级复验，才能据此授予 E2/E3。
crash case 还必须通过独立日志验证器，证明 quiet baseline 与显式 armed/bound/fire
事务目标身份唯一、有序且一致；全局提交次数不再作为目标选择依据。
observation recovery 的三次启动等多启动用例也在一个有序 step 中保留 runner stdout 与逐
boot Guest 日志。checkpoint 是 runner 在完整 marker 后以 `SIGTERM` 建立的重挂载契约；
powercut 是认证 supervisor 以 `SIGKILL` 突然终止 VM 的模型。两者都不描述为整机物理断电。

## 产物

成功包包含：

- `manifest.json`：C→E delivery 绑定、提交、唯一命令 argv/环境、返回码、总耗时、来源引用
  及本地/远程真实性边界；
- `verification-summary.json`：由 profile v5 的实际 full-verify 编排生成的有序步骤、耗时及
  原始产物清单；
- `logs/full-verify.log` 与 `logs/raw/`：完整 runner 控制台、Reader E2E manifest 及每轮未汇总
  build/Guest/summary、双 QEMU、Agent Guest、dual compare/timing，以及 proc、syscall、file、
  thread、physical、metadata recovery、observation recovery、VirtIO、workflow teardown、
  ENOSPC、文件系统分配器故障一致性的 runner stdout 与 canonical LF Guest transcript 合并记录。
  目录名 `raw/` 表示进入证据包前未做结论性汇总的来源产物，不表示逐字节串口 raw console；
  后者仅作传输诊断，不能替代具名 `*.guest.log`。dual step 还保存内部
  targeted agentbench 的 Guest log、manifest 和原始 CSV；采集与复核都会重放 marker 并核对
  source path、commit 及 CSV 行，Reader 派生结果不再悬空；
- `logs/raw/observe-recovery-before-reap.img`：boot1 checkpoint 后、boot2 reap 前保存的原始
  文件系统镜像；独立离线 verifier 按版本化、由生产结构编译探测得到的布局，解析当前 uCore
  文件系统、两份 metadata bank、durable arena 与 observation section，逐层复算 image、section、
  record、ledger 哈希，并把 scope、lifecycle generation、agent、receipt sequence/hash/id 与同一
  observation 日志中的 boot1 durable identity marker 精确绑定；任一层损坏或身份替换都会拒绝；
- `logs/raw/fs-allocator-evidence.tar`：36 个 allocator fault/reboot case 的固定 USTAR
  归档；采集与复核都调用同一 verifier 检查 backend、flush receipt、raw-image 状态、
  manifest 哈希、成员清单和 canonical archive bytes；
- Reader 证据包还必须包含共享 state manifest contract 的通过记录；该契约同时约束
  双目标 inventory、14 字节短名恢复、`/api/state/` allowlist 和 fixture，未知、缺失、
  重复或冲突条目均不得进入发布包；
- `environment/versions/`：Git、交叉编译器、QEMU、Python、Make、Bash 和 host C
  compiler 的版本输出；
- `configuration/kernel-budgets.json`：本轮提交使用的预算快照；
- `metrics/measurements.csv`：代码量、ELF/raw、text/data/BSS/total、`struct proc`、
  栈、Agent suite 总耗时，以及 `metadata_control_plane` 聚合的 source lines、source bytes、
  loaded text 和 BSS；均含 actual、baseline、limit、usage、原始日志行号和来源哈希。离线验证
  会从严格的 kernel/agent-modules 日志块与预算快照重新计算全部行，拒绝增删、重复和重绑伪造；
- `metrics/file-query-benchmark.csv` 与 `.json`：从 Agent suite Guest 完整日志行提取的
  强制遍历、冷索引和热索引原始测量；每行绑定日志 SHA256、marker SHA256、行号、命令、
  commit 和 run ID。缺少 marker、完整通过行或任一来源文件时，采集直接失败；
- `metrics/agent-case-timings.csv`、`metrics/commands.csv`：逐 case 计时和唯一验收命令；
- `charts/budget-usage.svg`：同一组 actual/baseline/limit/usage 的可复现图表，直接绑定
  `measurements.csv` 哈希，并保留完整日志与 Agent timing 的来源哈希；
- `checksums.sha256`：除自身外的全部普通文件。

仓库的 `.gitignore` 对 `evidence/releases/**` 有专用负例，因此包内 `.log` 可以正常纳入
版本控制，不需要 `git add -f`。`.gitattributes` 同时将该目录标为 `-text`，避免不同平台的
自动换行转换破坏证据包内逐字节 SHA256。

E3 由 `releases/INDEX.md` 的有效记录、对应包内 manifest、C→E committed delivery 验证及
本地离线语义复验共同决定。这是仓库的最高且唯一正式证据等级；
`remote_ci.status=not-attached` 是固定的兼容值，不表示存在待完成的远程升级。
