# Kernel growth budgets

`kernel-budgets.json` is the reviewable source of truth for kernel growth
limits. Production size maxima leave no more than 5% headroom over their
recorded baseline; a reviewed limit may be tighter. The calibrated QEMU
duration gate alone may use up to 10% because it must cover observed runner
variance.

Kernel measurements are normalized and deterministic under the pinned Ubuntu
26.04 compiler and binutils packages:

- source size counts physical lines in committed production kernel C, headers,
  assembly, linker scripts, and top-level policy headers. It excludes generated
  `os/initproc.S` and the exact five standalone profile owners registered under
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
- all 34 registered Agent/security translation units have individual LOC and
  exact no-growth BSS caps, code-only export namespaces, an exact
  registered-module dependency graph, and a reviewed maximum strongly
  connected component size of three. Twenty registered modules, including
  `metadata_catalog`, are in the checked `-Os` allowlist;
- every other `build/os/*.o` that defines or references the controlled
  `agent_`, `sys_agent_`, `sys_context_`, `resource_`, or
  `workflow_lifecycle_` namespaces, or references the exact `agentinit`
  entry point, must appear in the exact
  `integration_bridges` inventory. Bridge exports are exact and code-only,
  and the combined 34-module-plus-bridge controlled-symbol graph has its
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

A JSON `baseline_*` is a frozen review ratchet, not necessarily a duplicate of
the current measurement. The `struct proc` baseline is 25,936 bytes, the
current probe is 26,448 bytes, and the reviewed maximum is 27,233 bytes. The
production kernel source baseline is 47,922 lines; the current measurement and
reviewed maximum are both 49,705 lines. A future measurement below its frozen
maximum still passes without raising the baseline merely to match it.

The metadata control-plane aggregate currently measures 11,719 source lines,
357,485 source bytes, 77,337 loaded-text bytes, and 1,118,596 BSS bytes. Its
loaded-text baseline/maximum remains frozen at 77,896/77,896 bytes, while its
BSS baseline/maximum is frozen at 1,118,596/1,118,596 bytes. Observation v7
re-baselines the ledger at 2,372 lines and 223,232 BSS bytes; both maxima are
set to those measured values, so later work must first remove code or state
before adding more to that owner.

The frozen catalog-capacity contract is deliberately static: 512 slots split
into 64 SYSTEM slots and four 112-slot workflow partitions. ACTIVE, CLOSING,
and RETIRING scopes share the four admission positions, and a RETIRING scope
keeps its position until catalog reclaim completes. The workflow filesystem
inode account is also capped at 112. Capacity checks reject elastic cross-scope
borrowing, a separate catalog resource kind, global union/max approximations,
or a metadata-envelope ledger; scoped reload instead binds and revalidates the
immutable lifecycle id and generation. The superblock's current 342-inode
guarantee is raw filesystem capacity, not the effective Agent catalog limit.

The full-suite duration gate is currently `calibrated_full_suite`. Frozen commit
`814021ab9dac` produced three serial clean-detached 18-case samples of
327.098196563, 310.491647311, and 279.293840369 seconds on the pinned runner.
The reviewed median is 310.491647311 seconds and the deterministic limit is
327.10 seconds. Timing files, deterministic compressed Guest/runner logs,
environment, validation, and hashes are stored under
`evidence/calibrations/814021ab9dac/`. The older `31d4ddf53695` calibration is
retained only as historical evidence for its own source commit.

A calibrated policy also carries `source_fingerprint_sha256`. The checker
recomputes a length-framed SHA-256 over the expected cases, canonical
toolchain, runner profile/tag, root build contract, production kernel inputs,
Agent user programs, NFS image inputs, and the Agent runner's direct scripts.
Generated images/build output, `os/initproc.S`, documentation, release evidence,
and `ci/kernel-budgets.json` itself are excluded. Any relevant byte or contract
change invalidates the duration policy before QEMU; provisional configuration
must omit the fingerprint, baseline, maximum, and calibration samples.

The GitLab duration job is both serialized with a resource group and bound to
that calibrated runner tag. It also pins QEMU and OpenSBI. A runner hardware,
virtualization, QEMU, or case-set change must first return the status to
provisional, remove the stale thresholds/samples, and collect at least three
new full-suite samples. During that process,
`REQUIRE_FULL_SUITE=1`, `AGENT_TEST_CALIBRATE=1`, and an explicit
`AGENT_TEST_TIMING_FILE` preserve all completed per-case rows without treating
an old threshold as authoritative. Regular CI sets calibration mode to zero;
the runner invokes `--check agent-test-policy` before QEMU and refuses a
provisional configuration. Targeted `AGENT_TEST_CASE` development runs remain
available and do not claim the full-suite duration gate.

The duration checker rejects the summary-only `--agent-test-seconds` and
`--agent-test-start-ns` inputs. It accepts only
`--agent-test-timing-file`, whose positive finite rows must exactly match all
18 expected cases in order; a targeted, missing, duplicated, or reordered set
cannot satisfy the duration gate.

Repository maintainers should protect `.gitlab-ci.yml`, `Makefile`,
`ci/kernel-budgets.json`, and `scripts/check-*` with `CODEOWNERS` plus a GitLab
approval rule. Those owners are deployment-specific and cannot be named
portably here; without protected review, the same change could weaken a gate
while growing the kernel.

`make ci-check` always rebuilds the fixed `agentfinal_ucore`, `LOG=warn` profile
with the versioned `riscv64-linux-gnu` toolchain. `make full-verify` invokes the
same target before starting the long QEMU regression. `.gitlab-ci.yml` defines
an exact remote evidence set of one Host-class job and eight QEMU-class jobs.

## Remote CI execution evidence

The Host-class job is `kernel-budgets`. The QEMU-class jobs are `reader-e2e`,
`agent-regression`, `kernel-mechanism-regression`,
`physical-resource-regression`, `metadata-recovery-regression`,
`observe-recovery-regression`, `virtio-disk-regression`, and
`fs-allocator-fault-regression`. This is a job inventory, not a claim that nine
separate Runner machines exist; QEMU jobs may be serialized by their resource
group.

Each successful job candidate runs `remote_ci_evidence.py attest` last. The
attester requires the CI checkout HEAD to equal `CI_COMMIT_SHA`, rejects tracked
checkout changes and dangerous environment overrides, checks the required
Runner tag, validates the exact artifact inventory and job semantics, and then
publishes canonical `remote-ci-attestation.json`. It emits one complete trace
marker binding the job name, commit, and attestation SHA256. QEMU artifacts are
projected into the same semantic registry used by schema v6 final-evidence
collection and offline verification; the Host budget artifact uses an exact
inventory and exact completion markers.

`capture-final-evidence.py bind-remote-ci` live-fetches the GitLab project,
final `main` push pipeline, all nine job records, traces, and artifact ZIPs. The
offline verifier binds attestation identity to the API project/pipeline/job,
commit/ref, Runner id/tag, and verifier checkout. It requires exactly one trace
marker, exact artifact hashes, and a matching source-contract hash set, then
replays job semantics locally. ZIP input is bounded by archive, entry, expanded
size, and compression-ratio limits and rejects path escape, duplicate paths,
encryption, symlinks, special files, and unsupported compression.

These contracts and their mutation tests are E1 evidence only. The remote
project currently has no available Runner, so there is no successful remote
execution record and no E4 claim. A local bundle must remain `not-attached`
until every required job for the same source commit passes these checks. The
result is an execution/provenance attestation, not a cryptographic GitLab
provider signature or a guarantee that an already controlled Runner is honest.

The filesystem allocator regression publishes one canonical
`fs-allocator-evidence.tar`. Its transient source directory is kept outside
`ci-artifacts/`; both the job and the final bundle collector invoke
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
