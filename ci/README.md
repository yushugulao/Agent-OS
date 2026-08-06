# Kernel budgets

`kernel-budgets.json` is the only source of truth for growth limits. It records
reviewed baselines and maxima; this document intentionally does not duplicate
values that change with the candidate.

The budget gate covers:

- production kernel source lines and test-only profile owners;
- stripped ELF, raw image, text, data and BSS size;
- `struct proc`, thread-stack capacity and boot-stack capacity;
- lazy Agent-state pages, ordinary/reserved pools and resource domains;
- module ownership, exported namespaces, dependency edges and SCC size;
- GCC call-graph stack usage, including registered indirect calls; and
- the complete Agent QEMU suite when a calibrated duration profile is used.

Measurements use the pinned local toolchain profile. Generated files, build
outputs and documentation are excluded from source size. Test-only kernel
owners are measured separately and cannot hide production growth. A value
below its maximum passes without moving the baseline; changing a limit requires
normal code review.

## Verification

Run the fast structural gate while developing:

```sh
make kernel-budget-check
```

Run the complete local acceptance campaign before publishing evidence:

```sh
make full-verify
```

The checker output and the selected evidence bundle are authoritative for the
candidate's current values. `make local-check` and `make full-verify` choose
bounded build, Host-test and QEMU concurrency from available CPU and memory.
Nested Make and QEMU lanes share the outer budget instead of opening independent
worker pools.

## Duration calibration

Wall-clock limits are platform-specific. A calibrated profile is accepted only
from repeated complete runs of one clean commit under the pinned compiler,
QEMU, Python, Bash, Make and Git identities. The collector binds the commit
tree, executable hashes, case inventory, Guest logs and monotonic timings into
content-addressed attestations. Source or tool changes invalidate the profile.

Use `AGENT_TEST_DURATION_PROFILE=none` outside the calibrated platform. This
keeps all functional cases and semantic checks but makes no wall-clock claim.
Targeted development cases likewise never satisfy the full-suite duration gate.

## Delivery

GitLab is used for source and committed evidence; the project assumes no remote
Runner. Release acceptance is based on the local clean evidence bundle, offline
semantic verification and the append-only release index. The compatibility
field `remote_ci.status` therefore remains `not-attached`.

Special persistence, power-cut and expected-fault profiles are explicit in the
runner contract. Ordinary cases must terminate naturally with status zero;
timeouts, output overflow, unarmed faults, late panic and forced termination
fail closed.
