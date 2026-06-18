# project61-agentOS-happylegend

Agent-OS is an operating-system design project for the OS function challenge track. This branch ports the project to a uCore-based RISC-V kernel and implements Agent process management, structured tool calls, context history, file metadata indexing, Agent wait/wake loops, and a multi-Agent recovery demo.

The current delivery target is the `uCore` branch. The project-specific implementation is concentrated in `os/agent.c`, `os/agent.h`, `os/proc.c`, `os/syscall.c`, `os/trap.c`, and the Agent user tests under `user/src/`.

## What Is Implemented

- Agent process creation with per-process metadata, role-independent Agent identity, and a fixed user-visible Agent Context area.
- Four-page Agent Context mapped below the trapframe, with kernel-private shadow pages as the authoritative history and user pages as a fast read-only-by-convention mirror.
- Batched structured tool execution through `agent_run(ops, results, count, flags)`, supporting up to 64 operations per syscall.
- Context Path v2 with 128 visible records, FIFO replacement, short payload/result summaries, rollback, clear, point query, and full snapshot.
- Tool registry with ID-based fast dispatch and name-based legacy compatibility.
- File metadata service with indexed query by status, stage, and kind, used by the demo and benchmark.
- Agent Loop primitives: watch, wait, wake, heartbeat, and event delivery.
- Multi-Agent laboratory recovery demo covering monitoring, diagnosis, authorization check, recovery action, duplicate detection, and final report query.

## Repository Layout

- `os/`: uCore kernel source and Agent kernel implementation.
- `user/include/agent.h`: user-space Agent ABI definitions.
- `user/lib/syscall.c`: user-space syscall wrappers.
- `user/src/agentfinal_ucore.c`: final correctness verification for Agent-OS on uCore.
- `user/src/agentbench_ucore.c`: performance benchmark for batched calls, direct context reads, snapshots, index queries, and event wait/wake.
- `user/src/labdemo_ucore.c`: end-to-end multi-Agent demonstration.
- `docs/`: design, API, verification, testing, traceability, and demo documents.

## Environment

Recommended environment:

- WSL2 Ubuntu 26.04 or a recent Linux distribution.
- `make`
- `qemu-system-riscv64`
- `riscv64-linux-gnu-gcc`
- `riscv64-linux-gnu-binutils`

The Makefile also accepts `riscv64-unknown-elf-` if that toolchain is installed. In the current WSL environment the verified toolchain is `riscv64-linux-gnu-`.

## Build

```bash
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

`CHAPTER=agent` builds the Agent-OS verification programs and the user shell.

## Final Verification

For deterministic verification, run each final program as the init process:

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
```

Or run the scripted sequence:

```bash
bash scripts/run-agent-tests.sh
```

Expected success markers:

- `agentfinal_ucore: passed`
- `agentbench_ucore: passed`
- `labdemo_ucore: passed`

The benchmark prints a table in the form:

```text
case ops ticks ops_per_tick speedup_x100
```

Tick values vary across QEMU runs. The important evidence is successful execution, stable relative trends, and no kernel panic.

## Documentation

Start with:

- `docs/design.md`
- `docs/api.md`
- `docs/verification.md`
- `docs/testing-details.md`
- `docs/demo-script.md`
- `docs/requirements-traceability.md`
- `docs/test-record.md`

## License

Source code is distributed under GPL-3.0. Technical documents, demo scripts, and presentation-oriented materials are distributed under CC-BY-SA 4.0. See `LICENSE`, `DOCUMENTATION_LICENSE.md`, and `NOTICE`.
