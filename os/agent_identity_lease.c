#include "agent_identity_lease.h"
#include "defs.h"
#include "riscv.h"

/*
 * Identity uniqueness is scoped to one boot.  Counters never wrap or renew
 * from disk: reaching an end value closes that allocator until reboot.
 */
struct agent_identity_lease_state {
	uint64 ends[AGENT_IDENTITY_ALLOCATOR_COUNT];
	uint64 lifecycle_ends[WORKFLOW_LIFECYCLE_CAP];
};

static struct agent_identity_lease_state agent_identity_leases;

static uint64
agent_identity_boot_end(uint kind)
{
	return kind == AGENT_IDENTITY_ALLOCATOR_AGENT ? 0x7fffffffULL : ~0ULL;
}

void
agent_identity_lease_init(void)
{
	memset(&agent_identity_leases, 0, sizeof(agent_identity_leases));
	for (uint i = 0; i < AGENT_IDENTITY_ALLOCATOR_COUNT; i++)
		agent_identity_leases.ends[i] = agent_identity_boot_end(i);
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++)
		agent_identity_leases.lifecycle_ends[i] = ~0ULL;
}

int
agent_identity_lease_allocator_contains(uint kind, uint64 id)
{
	uint64 end;
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT || id == 0)
		return 0;
	enabled = intr_save();
	end = agent_identity_leases.ends[kind];
	intr_restore(enabled);
	return end != 0 && id < end;
}

int
agent_identity_lease_allocator_admit(uint kind, uint64 id, uint64 reserve)
{
	uint64 end;
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT || id == 0)
		return 0;
	enabled = intr_save();
	end = agent_identity_leases.ends[kind];
	intr_restore(enabled);
	return end != 0 && id < end && reserve < end - id;
}

int
agent_identity_lease_allocator_renew(uint kind)
{
	(void)kind;
	return -1;
}

void
agent_identity_lease_allocator_note_next(uint kind, uint64 next)
{
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT || next == 0)
		return;
	enabled = intr_save();
	if (next >= agent_identity_leases.ends[kind])
		agent_identity_leases.ends[kind] = 0;
	intr_restore(enabled);
}

int
agent_identity_lease_lifecycle_contains(uint slot, uint64 generation)
{
	uint64 end;
	int enabled;

	if (slot >= WORKFLOW_LIFECYCLE_CAP || generation == 0)
		return 0;
	enabled = intr_save();
	end = agent_identity_leases.lifecycle_ends[slot];
	intr_restore(enabled);
	return end != 0 && generation < end;
}

int
agent_identity_lease_lifecycle_renew(void)
{
	return -1;
}

void
agent_identity_lease_lifecycle_note_next(uint slot, uint64 next)
{
	int enabled;

	if (slot >= WORKFLOW_LIFECYCLE_CAP || next == 0)
		return;
	enabled = intr_save();
	if (next >= agent_identity_leases.lifecycle_ends[slot])
		agent_identity_leases.lifecycle_ends[slot] = 0;
	intr_restore(enabled);
}
