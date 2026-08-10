# Test Configuration

`ci/` contains machine-readable layouts and workload definitions used by
product tests. These files are ordinary test inputs, not an independent
acceptance system.

The useful inputs are deliberately narrow:

- current kernel/user UAPI sizes and offsets;
- the dual-target state-file allowlist; and
- actual performance experiment loads and operation counts;
- deterministic Agent Live relay responses; and
- scripted console and Nexus replay conversations.

Run the inexpensive checks while developing:

```sh
make agent-uapi-check
make agent-module-check
make kernel-stack-check
```

Run real Guest behavior when a change can affect kernel semantics:

```sh
make agentos-test
```

For a focused iteration, select the relevant Guest program directly:

```sh
AGENT_TEST_CASE=agentcontract_ucore make agentos-test
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test
```

A timeout remains a test failure, but it is an operational bound rather than a
machine-specific performance claim. Expected-fault, filesystem-capacity and
device-fault cases must still terminate with their documented marker and
status; unexpected panic, output overflow or forced termination fails the
test.

The contest demonstration is a real QEMU campaign, separate from these static
inputs:

```sh
make contest-demo
```

It writes the current run's raw logs, `summary.json`, `measurements.csv` and
`report.md` under `results/contest-demo/`; this directory contains run output,
not CI configuration or predeclared performance values.
