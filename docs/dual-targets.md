# Dual uCore Research Agent Targets

This branch contains two comparable targets for the research Agent platform.

## Target A: Plain uCore

The repository root is the plain target.

- Kernel: `os/`
- File-system builder: `nfs/`
- Boot helpers: `scripts/`
- User-space research platform: `user/`
- Host reader, action runner, file-system extractor, and LLM relay: `host_tools/`

This target keeps the uCore kernel unchanged. The research Agent platform runs through ordinary user programs and ordinary files. It is the comparison target for showing what a large user-space Agent workflow can do without kernel help.

Primary commands:

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
python host_tools/test_plain_ucore_reader_e2e.py
```

## Target B: Agent-OS uCore

The enhanced target is stored under `agentos_ucore/`.

- Kernel: `agentos_ucore/os/`
- File-system builder: `agentos_ucore/nfs/`
- Boot helpers: `agentos_ucore/scripts/`
- Agent-OS user programs: `agentos_ucore/user/`
- Agent-OS design and verification documents: `agentos_ucore/docs/`

This target contains the Agent-OS kernel services imported from the existing enhanced uCore work: Agent process roles, Agent Context, batched tool execution, Context Path, file metadata service, event wait/wake, heartbeat, prefetch hints, timeline records, and Agent-focused verification programs.

Primary commands from the repository root:

```bash
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

Equivalent commands inside `agentos_ucore/`:

```bash
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
bash scripts/run-agent-tests.sh
```

## Required Parity Direction

The final branch state should let a reviewer run the same research scenario on both targets:

1. Plain uCore runs the platform through ordinary user-space files and host-side orchestration.
2. Agent-OS uCore runs an equivalent research platform that uses kernel Agent services for process roles, context, tool calls, file metadata, events, scheduling evidence, and recovery.
3. Both targets expose comparable run records, artifact records, project review records, delivery records, LLM relay records, Agent collaboration records, and comparison records.
4. The enhanced target may add kernel-visible evidence and faster paths, but it should not reduce the research workflow complexity.

## Current Status

The plain target already provides the host-viewable research platform, Web/API reader, action runner, artifact records, workflow records, project review page, Host LLM Relay, and end-to-end QEMU test.

The enhanced target now exists in the same branch under `agentos_ucore/` and contains the Agent-OS kernel service layer plus Agent verification and demonstration programs. The next development stage is to adapt the research platform workflow to this enhanced target so the same scenario can be run against both kernels.

## Development Rule

Keep plain-target changes in the repository root. Keep enhanced-target kernel changes under `agentos_ucore/`. Shared concepts should be documented in this file or in target-specific design documents, but the plain target must remain runnable without Agent-OS kernel services.
