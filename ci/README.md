# Test Configuration

`ci/` contains machine-readable layouts and workload definitions used by
product tests. These files are ordinary test inputs, not an independent
acceptance system.

The useful checks are deliberately narrow:

- UAPI and retained disk-format sizes/offsets;
- the dual-target state-file allowlist; and
- actual performance experiment loads and operation counts.

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
machine-specific performance claim. Expected-fault and persistence cases must
still terminate with their documented marker and status; unexpected panic,
output overflow or forced termination fails the test.
