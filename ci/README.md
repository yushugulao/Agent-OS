# Kernel growth budgets

`kernel-budgets.json` is the reviewable source of truth for kernel growth
limits. Production size maxima leave no more than 5% headroom over their
recorded baseline; a reviewed limit may be tighter. The calibrated QEMU
duration gate alone may use up to 10% because it must cover observed runner
variance.

Kernel measurements are normalized under the versioned local toolchain profile:

- source size counts physical lines in committed production kernel C, headers,
  assembly, linker scripts, and top-level policy headers. It excludes generated
  `os/initproc.S` and the standalone profile owners registered under
  `test_only_sources`; each excluded owner has its own LOC ratchet, while
  profile hooks embedded in production units remain charged to production;
- ELF size is measured after removing all symbols and non-runtime toolchain
  notes, and raw size is produced with the target `objcopy`;
- target `size` separately limits text, data, BSS, and their total runtime
  footprint so a large zero-initialized pool cannot bypass file-size checks;
- `struct proc`, total kernel-stack virtual capacity, and the distinct
  boot-reserved physical stack pool are measured by cross-compiled, unlinked
  probe symbols;
- the complete on-demand Agent state allocation is charged atomically as 21
  `RESOURCE_AGENT_STATE_PAGE` pages: nine private detail/attribution sidecar
  pages, six user mirror pages, and six trusted shadow pages. Both that total
  and the nine-page sidecar detail are capped per process, across the global
  ordinary/reserved pools, and per ordinary/reserved resource domain, so
  moving state out of BSS cannot hide worst-case physical growth;
- all 35 registered Agent/security translation units have individual LOC and
  exact no-growth BSS caps, code-only export namespaces, an exact
  registered-module dependency graph, and a reviewed maximum strongly
  connected component size of three. Twenty registered modules, including
  `metadata_catalog`, are in the checked `-Os` allowlist;
- every other `build/os/*.o` that defines or references the controlled
  `agent_`, `sys_agent_`, `sys_context_`, `resource_`, or
  `workflow_lifecycle_` namespaces, or references the exact `agentinit`
  entry point, must appear in the exact
  `integration_bridges` inventory. Bridge exports are exact and code-only,
  and the combined 35-module-plus-bridge controlled-symbol graph has its
  own exact edge and SCC policy. The eleven reviewed bridges are `bio`, `file`,
  `fs`, `kalloc`, `loader`, `main`, `pipe`, `proc`, `syscall`, `trap`, and
  `vfs_security`.
  The maximum SCC size of three is a hard checker contract, not a threshold
  that JSON can relax. This controlled-symbol integration graph intentionally
  makes no claim about dependencies carried solely by ordinary uCore symbols,
  so it is not a complete uCore call graph;
- stack usage is computed from the complete GCC call graph for both the 16 KiB
  thread stack and the 64 KiB boot/scheduler stack. Both paths include a nested
  `kerneltrap`, while linked `boot_stack` symbols verify the latter capacity.
  The checker receives the exact production translation-unit inventory, rejects
  missing or unknown callgraphs, and resolves every function-pointer edge at its
  compiled callback owner; only the exact registered profile owners may leave
  inactive `.ci` files behind;
- the Agent suite sums only monotonic QEMU case durations, excluding
  host-toolchain compilation; targeted `AGENT_TEST_CASE` runs do not claim to
  satisfy the full-suite budget.

Agent state consumes no pages while idle. Materializing one Agent atomically
charges 21 pages, or 84 KiB: nine pages for the private Context detail sidecar,
six for the user mirror, and six for the trusted shadow. The six audited total
state budgets are 86,016 bytes per process, 11,010,048 bytes globally,
8,257,536 bytes in the ordinary pool, 2,752,512 bytes in the reserved pool,
5,505,024 bytes per ordinary domain, and 688,128 bytes per reserved domain.
The sidecar-only detail budget remains separately visible: 36 KiB per active
Agent and 4.5 MiB across all 128 process slots, partitioned into 3.375 MiB
ordinary and 1.125 MiB reserved capacity. Both are logical admission budgets
over the general page allocator, not physically pinned reserves.

A JSON `baseline_*` or `max_*` value is a frozen review ratchet, not a duplicate
of the current measurement. Current source, image, runtime, `struct proc`, stack,
and metadata-control-plane measurements are intentionally not copied into this
document: they must come from the candidate's canonical `make local-check` log,
the same versioned JSON, and the selected release bundle. A measurement below
its frozen maximum passes without raising the baseline merely to match it.
The source baseline therefore remains 47,922 lines. The reviewed ceiling is
50,066 lines: the additional 232 lines over the previous candidate close the
work-conserving block-I/O path, physical-transfer receipts, and the complete
inode-owner prepare/commit boundary used by allocator rollback verification.
The ceiling equals the measured candidate, so later growth still requires an
explicit review instead of inheriting spare capacity. The allocator profile is
separately rebaselined at its measured 288 lines with no growth headroom; it is
excluded from the production kernel and cannot conceal production growth.

The frozen catalog-capacity contract is deliberately static: 512 slots split
into 64 SYSTEM slots and four 112-slot workflow partitions. Each workflow may
grow to at most 96 live AUTOSCAN entries, leaving 16 slots for explicit
metadata. That soft growth policy is not part of the v7 disk-validity contract:
same-version snapshots with 97 through 112 AUTOSCAN entries load intact, while
113 entries remain corrupt. An over-limit live scope may keep or reduce its
AUTOSCAN count and may grow again only after reaching 95; exact receipt rollback
uses hard partition and uniqueness checks rather than the newer soft policy.
ACTIVE, CLOSING, and RETIRING scopes share the four admission positions, and a
RETIRING scope keeps its position until catalog reclaim completes. The 112-slot
number is an index-capacity contract, not a filesystem inode quota. The earlier
candidate that also capped a workflow inode account at 112 is retained only as
a historical failed baseline. Current workflow inode admission uses the
independent STORAGE policy: its hard floor is 320 and the current image records
about 342 inodes per workflow. Capacity checks reject elastic cross-scope
borrowing, a separate catalog resource kind, global union/max approximations,
or a metadata-envelope ledger; scoped reload instead binds and revalidates the
immutable lifecycle id and generation.

The current candidate's full-suite duration gate is
`provisional_requires_full_suite`. Resource-adaptive local build parallelism and the runtime fast-path
changes invalidate the previous source fingerprint before any QEMU timing is
accepted. Historical calibration artifacts are kept in Git history rather than
the current contest tree. A new `calibrated_full_suite` threshold requires three complete
18-case runs from the final clean commit on the recorded
`local-e3-msys2-xpack-qemu11-v1` profile. Until then, local E3 timing claims
remain disabled; the `none` profile may execute functional local coverage but
cannot establish a wall-clock threshold.

A production calibration is collected only by
`scripts/agent_test_calibration.py collect`. The harness requires a real Git
commit in a clean detached worktree, proves `HEAD` and the commit tree, and
compares every tracked worktree byte directly with its commit blob. Git clean
filters, ignored/untracked source, repository-redirection variables,
Windows-equivalent path collisions, unsafe Windows path spellings, symlinks,
and junctions cannot substitute different execution bytes. Every round starts
from an empty extra-file inventory after cleanup and ends with only the exact
generated-output roots admitted. Native POSIX collection also checks the committed
executable bit; MSYS2 uses the raw-byte contract because its POSIX mode bits are
emulated. The harness binds that tree plus the complete source fingerprint into
a predeclared schema-1 plan and publishes a schema-3 manifest. It then runs
exactly three complete, serialized 18-case rounds.
The campaign, rounds, and all 57 prelude/case executions receive distinct
256-bit random nonces.

Every execution is published by `agent_test_runner.py` as an exclusive-create
schema-2 attestation. It binds the runner and executable path/hash/version,
exact invocation and QEMU argv, kernel and pre/post filesystem-image hashes,
the exact Guest log section, exit/marker result, and monotonic start, finish,
and elapsed values. Calibration mode forbids the runner's independent timing
file. After a complete round, the harness reconstructs all 18 timing rows only
from the validated attestations; package verification repeats that derivation,
checks continuous session and round bounds, and rejects missing, duplicated,
overlapping, reordered, or reused executions.

The manifest and every package member are content-addressed. The duration limit
is derived rather than chosen manually: the greater of the largest sample and
105% of the median, rounded upward to one millisecond. Samples more than 10%
above the median still reject calibration. Synthetic fixtures remain valid for
static mutation and relocation tests but are never admissible calibration
evidence. A production package is explicitly `local_e3_unsigned`; it is never
described as third-party execution or signed evidence. Content addressing proves
internal replay and provenance, not the honesty of a local operator who
controls the checkout, tools, and output files.

The checker
recomputes a length-framed SHA-256 over the expected cases, canonical local
toolchain, the versioned local E3 profile, root build contract, production kernel inputs,
Agent user programs, NFS image inputs, and the Agent runner's direct scripts.
Generated images/build output, `os/initproc.S`, documentation, release evidence,
and `ci/kernel-budgets.json` itself are excluded. Any relevant byte or contract
change invalidates the duration policy before QEMU; provisional configuration
must omit every calibrated field, including the commit and manifest binding.

The local threshold applies only to
`local-e3-msys2-xpack-qemu11-v1`. The configured CPU, MSYS2 runtime, xPack
GCC/ld/objcopy/objdump/as, host C compiler, QEMU, Python, Bash, Make, and Git
versions must match; their
resolved files are hashed before and after execution. The collector feeds those
same absolute QEMU, Python, Bash, Make, Git, and toolchain-prefix paths into
every child and locks the search path. The child environment is rebuilt from a
minimal OS/runtime allowlist, so ambient Bash, Make, Python, GCC, test-profile,
or logging variables cannot alter the calibrated build. A hardware, runtime, executable, or
case-set change first returns the status to provisional. Collection runs from
outside the source worktree, for example:

```sh
QEMU=/opt/qemu/qemu-system-riscv64.exe \
TOOLPREFIX=/opt/xpack-riscv/bin/riscv-none-elf- \
python3 -I -S -B scripts/agent_test_calibration.py collect \
  --root . --source-commit "$(git rev-parse HEAD)" \
  --output /var/tmp/agentos-calibration-"$(git rev-parse --short=12 HEAD)"
```

An uncalibrated local environment explicitly sets
`AGENT_TEST_DURATION_PROFILE=none`: every case registered by
`ci/kernel-budgets.json`, its semantic checks, Guest logs, and the exact
timing-row inventory remain mandatory, but the calibrated wall-time limit is
not applied. Targeted `AGENT_TEST_CASE` development runs likewise do not claim
it.

The duration checker rejects the summary-only `--agent-test-seconds` and
`--agent-test-start-ns` inputs. A normal gate accepts only
`--agent-test-timing-file`, whose positive finite rows must exactly match the
registered case inventory in order. For calibration, that file is accepted only after
the schema-3 verifier has independently reconstructed the same bytes and totals
from the per-execution attestations.

Changes to `Makefile`, `ci/kernel-budgets.json`, and `scripts/check-*` require
normal code review because the same change could weaken a gate while growing
the kernel.

`make local-check` always rebuilds the fixed `agentfinal_ucore`, `LOG=warn` profile.
Independent Python contract programs run through a resource-adaptive bounded
local worker pool with per-test logs and inventory-ordered output. Kernel and user
compilation use the same bounded build setting and reuse an outer GNU make
jobserver instead of creating nested worker pools. The top-level `local-check`
phases remain serial because they share build and evidence artifacts. Formal
collection starts from its closed environment, so the computed worker limit
cannot be replaced by ambient variables.
The local E3 identity uses the versioned MSYS2 xPack profile only when the six
direct build/measurement tools plus GCC's `cc1` and
assembler subprogram match the committed SHA-256 inventory and the duration-
calibration profile id, prefix, and all shared versions. The build receipt binds
the compiler, `cc1`, assembler, linker, and objdump actually used; a mixed
toolchain or a changed executable fails closed. `make full-verify` invokes the
same target before starting the long QEMU regression.

## Repository delivery

GitLab stores source commits and committed evidence only. The project does not
configure a Runner or create pipelines. A release is accepted through the local
clean C-to-E bundle, offline semantic verification, and the append-only release
index. The compatibility manifest field `remote_ci.status` is always
`not-attached`; it is not an incomplete upgrade step.

The filesystem allocator regression publishes one canonical
`fs-allocator-evidence.tar`. Its transient source directory is kept outside the
archive; both the local regression runner and the final bundle collector invoke
`fs-allocator-evidence.py verify-archive`, so a combined text log alone cannot
satisfy that mechanism gate.

The budget checker, common QEMU monitor, and production-profile validator each
carry fail-closed host regressions; their exact counts follow the source rather
than being duplicated here. The profile tests ensure shell entry points select
the intended natural, checkpoint, powercut, and expected-fault contracts. The
monitor drains binary output
through process exit and detects registered failure patterns, including panic,
case-insensitively even when fragmented. Each monitor turn reads at most one
64 KiB chunk before rechecking case and marker deadlines, so continuous output
cannot starve timeout enforcement. Each case is capped at 16 MiB of total
output and 64 KiB of unterminated-record state; retained diagnostic lines are
capped at 4 KiB. Output/record overflow fails closed and diagnostic copies are
bounded by truncation. The case deadline is checked before checkpoint success
and rechecked after scanner feeds and notices, so late markers cannot pass.
Every ordinary case must terminate naturally with return code zero. Marker
grace exists only to stop an already failed or hung ordinary case and remains
a failure whether it ends through `SIGTERM` or escalates to `SIGKILL`;
post-marker output, timeouts, nonzero exits, and late panic all fail. An
explicit persistence checkpoint profile may accept one runner-issued
`SIGTERM` after its exact marker; escalation still fails. A distinct, explicit
powercut profile accepts only one authenticated supervisor-issued `SIGKILL`
against the stable QEMU leader and requires matching nonce, PID/starttime,
image exit status, and complete descendant cleanup. It models abrupt VM
termination but does not flush host page cache and is not physical machine
power loss.
Expected guest faults are likewise marker-scoped: every exemption requires one
explicit arm marker and consumes exactly one matching `bad addr`; an unarmed or
missing fault fails closed.
