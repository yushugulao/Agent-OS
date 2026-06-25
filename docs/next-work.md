# Next Work For Plain uCore Migration

The current branch has a working native baseline. The remaining work is to turn the compact native catalog into an active user-space platform without changing the kernel.

## Required Direction

The plain uCore platform should keep the same object names and output contract as the Python platform and the future Agent-OS platform. New work should add behavior in user space, not kernel hooks.

## Planned Increments

1. Expand the current reusable object collections into larger workflow, evidence, dataset, report, release, LLM packet, sample, protocol, experiment, telemetry, and review records.
2. Expand catalog, query, object query, lineage, site export, evidence, package, release, dossier, compare, planner, retriever, analyst, sample, quality, protocol, SOP, experiment, telemetry, reviewer, writer, repair, auditor, LLM packet, privacy, and AgentCompare programs beyond the current fixed records.
3. Add a simple message protocol using files, pipes, or process exit/status patterns available in unchanged uCore.
4. Move selected checks from static counters into active operations over stored records.
5. Add an AgentCompare plain-kernel runner that emits the same high-level result fields as the future Agent-OS runner.
6. Add a host-side LLM relay that consumes the prepared ordinary request files and writes ordinary response files.

## Non-Goals For This Branch

- No Agent syscall implementation.
- No kernel Agent Context.
- No kernel file metadata service.
- No kernel Agent event queue.
- No kernel LLM networking.

Those belong to the enhanced kernel version.
