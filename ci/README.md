# Kernel growth budgets

`kernel-budgets.json` is the reviewable source of truth for kernel growth
limits. Each maximum intentionally leaves 5% to 10% headroom over its recorded
baseline.

Kernel measurements are normalized and deterministic under the pinned Ubuntu
26.04 compiler and binutils packages:

- source size counts physical lines in committed kernel C, headers, assembly,
  linker scripts, and top-level policy headers, excluding generated
  `os/initproc.S`;
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
- all twelve registered Agent/security translation units have individual LOC,
  code-only export namespaces, an exact registered-module dependency graph,
  and a reviewed maximum strongly connected component size of three;
- every other `build/os/*.o` that defines or references the controlled
  `agent_`, `sys_agent_`, `sys_context_`, `resource_`, or
  `workflow_lifecycle_` namespaces, or references the exact `agentinit`
  entry point, must appear in the exact
  `integration_bridges` inventory. Bridge exports are exact and code-only,
  and the combined twelve-module-plus-bridge controlled-symbol graph has its
  own exact edge and SCC policy. The nine reviewed bridges are `bio`, `file`,
  `fs`, `loader`, `main`, `proc`, `syscall`, `trap`, and `vfs_security`.
  The maximum SCC size of three is a hard checker contract, not a threshold
  that JSON can relax. This controlled-symbol integration graph intentionally
  makes no claim about dependencies carried solely by ordinary uCore symbols,
  so it is not a complete uCore call graph;
- stack usage is computed from the complete GCC call graph for both the 16 KiB
  thread stack and the 64 KiB boot/scheduler stack. Both paths include a nested
  `kerneltrap`, while linked `boot_stack` symbols verify the latter capacity;
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

A JSON `baseline_*` is a frozen review ratchet, not a duplicate of the current
measurement. For example, the current `struct proc` probe is 28,808 bytes while
the retained baseline/max pair is 28,776/30,215 bytes. The current value passes
because it remains below the maximum; raising the baseline merely to match it
would weaken the ratchet and is not required.

The full-suite duration gate is calibrated on the
`agentos-qemu-calibrated` WSL2 runner: Intel Core Ultra 9 275HX and QEMU
10.2.1. Three complete 16-case samples measured 261.343281873,
237.948978492, and 255.370930671 seconds. `ci/kernel-budgets.json` records the
`bounded-runner-final-01/02/03` samples and the
runner fingerprint as durable calibration evidence; raw per-case timing files
are temporary calibration artifacts and are not CI inputs. The median
255.370930671 seconds is the median baseline and 268.14 seconds is the limit,
providing about 5% headroom over the median while still covering the largest
sample.

The GitLab duration job is both serialized with a resource group and bound to
that calibrated runner tag. It also pins QEMU and OpenSBI. A runner hardware,
virtualization, or QEMU change must first return the status to provisional and
collect at least three new full-suite samples. During that process,
`REQUIRE_FULL_SUITE=1`, `AGENT_TEST_CALIBRATE=1`, and an explicit
`AGENT_TEST_TIMING_FILE` preserve all completed per-case rows without treating
the old threshold as authoritative. Regular CI sets calibration mode to zero
and requires `calibrated_full_suite`.

The duration checker rejects the summary-only `--agent-test-seconds` and
`--agent-test-start-ns` inputs. It accepts only
`--agent-test-timing-file`, whose positive finite rows must exactly match all
16 expected cases in order; a targeted, missing, duplicated, or reordered set
cannot satisfy the duration gate.

Repository maintainers should protect `.gitlab-ci.yml`, `Makefile`,
`ci/kernel-budgets.json`, and `scripts/check-*` with `CODEOWNERS` plus a GitLab
approval rule. Those owners are deployment-specific and cannot be named
portably here; without protected review, the same change could weaken a gate
while growing the kernel.

`make ci-check` always rebuilds the fixed `agentfinal_ucore`, `LOG=warn` profile
with the versioned `riscv64-linux-gnu` toolchain. `make full-verify` invokes the
same target before starting the long QEMU regression. GitLab runs both the
budget target and the unsharded Agent suite from `.gitlab-ci.yml`.

The budget checker currently carries 31 fail-closed unit regressions. The
common QEMU monitor has 24 host-side regressions, and five production-profile
validator cases ensure the shell entry points select the intended natural,
checkpoint, and expected-fault contracts. The monitor drains binary output
through process exit and detects registered failure patterns, including panic,
case-insensitively even when fragmented. Each monitor turn reads at most one
64 KiB chunk before rechecking case and marker deadlines, so continuous output
cannot starve timeout enforcement. Each case is capped at 16 MiB of total
output and 64 KiB of unterminated-record state; retained diagnostic lines are
capped at 4 KiB. Output/record overflow fails closed and diagnostic copies are
bounded by truncation. The case deadline is checked before checkpoint success
and rechecked after scanner feeds and notices, so late markers cannot pass.
Every ordinary case must terminate naturally with return code zero. Marker
grace exists only to stop an already
failed/hung case and remains
a failure whether it ends through `SIGTERM` or escalates to `SIGKILL`;
post-marker output, timeouts, nonzero exits, and late panic all fail. The only
signal-terminated success contract is the explicitly selected checkpoint mode
used by two persistence checkpoint phases: after its expected checkpoint
marker it may accept one runner-issued `SIGTERM`; escalation to `SIGKILL` still
fails.
Expected guest faults are likewise marker-scoped: every exemption requires one
explicit arm marker and consumes exactly one matching `bad addr`; an unarmed or
missing fault fails closed.
