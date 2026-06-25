# Plain uCore Platform Design

## Positioning

This branch keeps the uCore kernel unchanged and places the research Agent platform in ordinary user space.

The kernel does not know about Agent roles, Agent Context, tool batches, file metadata indexes, or Agent event queues. Those mechanisms belong to the later Agent-OS enhanced kernel branch.

## Runtime Shape

The current runtime has three layers:

1. Upstream uCore kernel.
2. Restored uCore user library and program build flow.
3. `research_platform_ucore_plain`, a native user process that carries the research platform catalog, feature groups, mature platform mappings, and self-check logic.
4. `research_platform_orchestrator`, a native user process that runs seven role programs through ordinary `fork`, `exec`, and `waitpid`.

The user process deliberately uses ordinary C data structures and ordinary uCore process execution. This makes the result a baseline for later comparison with the Agent-OS kernel-enhanced version.

## Preserved Platform Concepts

The native program preserves the platform vocabulary and scale from the pure user-space platform:

- Workflow templates and invocations.
- Workflow portability and migration planning.
- Execution control and worker operations.
- Agent roles, task assignments, handoffs, and deliberation.
- Data ingestion, dataset snapshots, samples, cohorts, annotations, studies, ethics, lab operations, and analysis results.
- Visualization, publication, FAIR data, literature, knowledge, semantic graph, prompt/model operations, LLM bridge, secrets, collaboration, observability, release governance, protocol compliance, risk handling, provenance, search, dashboard data, and AgentCompare.

The first native version stores these as compact static tables. This is intentionally simple: it proves that a large platform-shaped program can run as a normal process on unchanged uCore before deeper user-space services are added.

The role programs add an executable multi-process shape:

- `rp_planner`
- `rp_retriever`
- `rp_analyst`
- `rp_reviewer`
- `rp_writer`
- `rp_repair`
- `rp_auditor`

These programs do not require Agent syscalls. They are ordinary uCore processes that make the plain-kernel baseline closer to the original multi-role research Agent platform.

## Upstream Kernel Guarantee

The `os`, `nfs`, and `scripts` directories are copied from the upstream uCore 2025S source. Kernel verification uses directory comparison rather than source comments.

The only implementation changes needed for the first native platform step are in ordinary user-space files:

- `user/src/research_platform_ucore_plain.c`
- `user/src/research_platform_orchestrator.c`
- `user/src/rp_*.c`
- `user/Makefile`
- `user/src/usershell.c`

`usershell.c` only changes character constants so it builds with the available GNU RISC-V toolchain.

## Compatibility With Later Agent-OS Version

The native program keeps stable object names, role names, capability names, and output lines. The later Agent-OS version can replace in-process tables with kernel-assisted Context Path, tool calls, file metadata, Agent events, and LLM gateway requests while preserving the same demonstration contract.
