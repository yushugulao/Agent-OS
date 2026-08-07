#include "agent_identity_lease.h"
#include "defs.h"
#include "riscv.h"

enum agent_identity_lease_phase {
	AGENT_IDENTITY_LEASE_IDLE = 0,
	AGENT_IDENTITY_LEASE_PREPARED,
	AGENT_IDENTITY_LEASE_FAILED,
};

struct agent_identity_lease_state {
	uint64 published[AGENT_IDENTITY_ALLOCATOR_COUNT];
	uint64 prepared[AGENT_IDENTITY_ALLOCATOR_COUNT];
	uint64 lifecycle_published[WORKFLOW_LIFECYCLE_CAP];
	uint64 lifecycle_prepared[WORKFLOW_LIFECYCLE_CAP];
	uint64 serial;
	uint64 target;
	uint phase;
	uchar allocator_recovered[AGENT_IDENTITY_ALLOCATOR_COUNT];
	uchar lifecycle_recovered[WORKFLOW_LIFECYCLE_CAP];
	int storage_ready;
	int admission_ready;
	int progressing;
	int renew_requested;
	agent_identity_lease_persist_fn persist;
};

static struct agent_identity_lease_state agent_identity_leases;

static uint64
agent_identity_lease_advance(uint kind, uint64 end)
{
	uint64 limit = kind == AGENT_IDENTITY_ALLOCATOR_AGENT ?
		0x7fffffffULL : ~0ULL;

	if (end == 0 || end >= limit)
		return 0;
	if (limit - end <= AGENT_IDENTITY_LEASE_CHUNK)
		return limit;
	return end + AGENT_IDENTITY_LEASE_CHUNK;
}

static uint64
agent_identity_lifecycle_lease_advance(uint64 end)
{
	if (end == 0 || end == ~0ULL)
		return 0;
	if (~0ULL - end <= AGENT_IDENTITY_LEASE_CHUNK)
		return ~0ULL;
	return end + AGENT_IDENTITY_LEASE_CHUNK;
}

static int
agent_identity_lease_prepare_locked(void)
{
	int changed = 0;

	if (agent_identity_leases.phase == AGENT_IDENTITY_LEASE_FAILED)
		return -1;
	if (agent_identity_leases.phase == AGENT_IDENTITY_LEASE_PREPARED)
		return 0;
	for (uint i = 0; i < AGENT_IDENTITY_ALLOCATOR_COUNT; i++) {
		agent_identity_leases.prepared[i] = agent_identity_lease_advance(
			i, agent_identity_leases.published[i]);
		if (agent_identity_leases.prepared[i] !=
		    agent_identity_leases.published[i])
			changed = 1;
	}
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++) {
		agent_identity_leases.lifecycle_prepared[i] =
			agent_identity_lifecycle_lease_advance(
				agent_identity_leases.lifecycle_published[i]);
		if (agent_identity_leases.lifecycle_prepared[i] !=
		    agent_identity_leases.lifecycle_published[i])
			changed = 1;
	}
	if (!changed) {
		agent_identity_leases.phase = AGENT_IDENTITY_LEASE_FAILED;
		return -1;
	}
	agent_identity_leases.phase = AGENT_IDENTITY_LEASE_PREPARED;
	agent_identity_leases.serial = 0;
	agent_identity_leases.target = 0;
	agent_identity_leases.renew_requested = 0;
	return 0;
}

static void
agent_identity_lease_publish_locked(void)
{
	for (uint i = 0; i < AGENT_IDENTITY_ALLOCATOR_COUNT; i++)
		agent_identity_leases.published[i] =
			agent_identity_leases.prepared[i];
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++)
		agent_identity_leases.lifecycle_published[i] =
			agent_identity_leases.lifecycle_prepared[i];
	agent_identity_leases.phase = AGENT_IDENTITY_LEASE_IDLE;
	agent_identity_leases.serial = 0;
	agent_identity_leases.target = 0;
	agent_identity_leases.progressing = 0;
	agent_identity_leases.admission_ready = 1;
	agent_identity_leases.renew_requested = 0;
}

static int
agent_identity_lease_progress(void)
{
	agent_identity_lease_persist_fn persist;
	uint64 serial;
	uint64 target;
	int enabled;
	int result;

	enabled = intr_save();
	if (!agent_identity_leases.storage_ready ||
	    agent_identity_leases.persist == 0 ||
	    agent_identity_lease_prepare_locked() < 0 ||
	    agent_identity_leases.progressing) {
		intr_restore(enabled);
		return -1;
	}
	agent_identity_leases.progressing = 1;
	persist = agent_identity_leases.persist;
	serial = agent_identity_leases.serial;
	target = agent_identity_leases.target;
	intr_restore(enabled);

	result = persist(&serial, &target);
	enabled = intr_save();
	if (agent_identity_leases.phase != AGENT_IDENTITY_LEASE_PREPARED) {
		agent_identity_leases.progressing = 0;
		intr_restore(enabled);
		return -1;
	}
	agent_identity_leases.serial = serial;
	agent_identity_leases.target = target;
	if (result > 0) {
		agent_identity_lease_publish_locked();
		intr_restore(enabled);
		return 0;
	}
	if (result < 0) {
		agent_identity_leases.phase = AGENT_IDENTITY_LEASE_FAILED;
		agent_identity_leases.progressing = 0;
		intr_restore(enabled);
		return -1;
	}
	agent_identity_leases.progressing = 0;
	agent_identity_leases.renew_requested = 1;
	intr_restore(enabled);
	/* 已分派线程的检查点会再次轮询此锁存位。 */
	return -1;
}

static int
agent_identity_lease_pending_locked(void)
{
	return agent_identity_leases.storage_ready &&
	       agent_identity_leases.phase != AGENT_IDENTITY_LEASE_FAILED &&
	       (agent_identity_leases.renew_requested ||
		agent_identity_leases.phase == AGENT_IDENTITY_LEASE_PREPARED);
}

void
agent_identity_lease_init(void)
{
	memset(&agent_identity_leases, 0, sizeof(agent_identity_leases));
	for (uint i = 0; i < AGENT_IDENTITY_ALLOCATOR_COUNT; i++) {
		agent_identity_leases.published[i] = 1;
		agent_identity_leases.prepared[i] = 1;
	}
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++) {
		agent_identity_leases.lifecycle_published[i] = 1;
		agent_identity_leases.lifecycle_prepared[i] = 1;
	}
}

void
agent_identity_lease_set_persist(agent_identity_lease_persist_fn persist)
{
	int enabled = intr_save();

	if (agent_identity_leases.persist != 0 &&
	    agent_identity_leases.persist != persist) {
		intr_restore(enabled);
		panic("identity lease persistence owner");
	}
	agent_identity_leases.persist = persist;
	intr_restore(enabled);
}

int
agent_identity_lease_storage_ready(void)
{
	int enabled = intr_save();
	int ready;

	agent_identity_leases.storage_ready = 1;
	agent_identity_leases.renew_requested = 1;
	intr_restore(enabled);
	if (agent_identity_lease_progress() < 0)
		return -1;
	enabled = intr_save();
	ready = agent_identity_leases.admission_ready;
	intr_restore(enabled);
	return ready ? 0 : -1;
}

void
agent_identity_lease_maintain(void)
{
	int pending;
	int enabled = intr_save();

	/* 核心仅允许存活的已分派线程进入此处。 */
	pending = agent_identity_lease_pending_locked();
	intr_restore(enabled);
	if (pending)
		(void)agent_identity_lease_progress();
}

int
agent_identity_lease_maintenance_pending(void)
{
	int enabled = intr_save();
	int pending = agent_identity_lease_pending_locked();

	intr_restore(enabled);
	return pending;
}

int
agent_identity_lease_allocator_contains(uint kind, uint64 id)
{
	uint64 end;
	int contained;
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT || id == 0)
		return 0;
	enabled = intr_save();
	end = agent_identity_leases.published[kind];
	contained = agent_identity_leases.admission_ready &&
		    end != 0 && id < end;
	intr_restore(enabled);
	return contained;
}

int
agent_identity_lease_allocator_admit(uint kind, uint64 id, uint64 reserve)
{
	uint64 end;
	int admitted;
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT || id == 0)
		return 0;
	enabled = intr_save();
	end = agent_identity_leases.published[kind];
	admitted = agent_identity_leases.admission_ready && end != 0 &&
		   id < end && reserve < end - id;
	intr_restore(enabled);
	return admitted;
}

int
agent_identity_lease_allocator_renew(uint kind)
{
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT)
		return -1;
	enabled = intr_save();
	if (agent_identity_leases.storage_ready &&
	    agent_identity_leases.phase != AGENT_IDENTITY_LEASE_FAILED)
		agent_identity_leases.renew_requested = 1;
	intr_restore(enabled);
	/* 分配路径绝不进入可能睡眠的持久化所有者。 */
	return -1;
}

void
agent_identity_lease_allocator_note_next(uint kind, uint64 next)
{
	uint64 end;
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT || next == 0)
		return;
	enabled = intr_save();
	end = agent_identity_leases.published[kind];
	if (end != 0 && next <= end &&
	    end - next <= AGENT_IDENTITY_LEASE_LOW_WATER &&
	    agent_identity_leases.phase != AGENT_IDENTITY_LEASE_FAILED)
		agent_identity_leases.renew_requested = 1;
	intr_restore(enabled);
}

void
agent_identity_lease_allocator_force_exhausted(uint kind)
{
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT)
		return;
	enabled = intr_save();
	agent_identity_leases.published[kind] = 0;
	agent_identity_leases.prepared[kind] = 0;
	intr_restore(enabled);
}

int
agent_identity_lease_lifecycle_contains(uint slot, uint64 generation)
{
	uint64 end;
	int contained;
	int enabled;

	if (slot >= WORKFLOW_LIFECYCLE_CAP || generation == 0)
		return 0;
	enabled = intr_save();
	end = agent_identity_leases.lifecycle_published[slot];
	contained = agent_identity_leases.admission_ready &&
		    end != 0 && generation < end;
	intr_restore(enabled);
	return contained;
}

int
agent_identity_lease_lifecycle_renew(void)
{
	int enabled = intr_save();

	if (agent_identity_leases.storage_ready &&
	    agent_identity_leases.phase != AGENT_IDENTITY_LEASE_FAILED)
		agent_identity_leases.renew_requested = 1;
	intr_restore(enabled);
	/* 生命周期接纳在可调度后台维护后重试。 */
	return -1;
}

void
agent_identity_lease_lifecycle_note_next(uint slot, uint64 next)
{
	uint64 end;
	int enabled;

	if (slot >= WORKFLOW_LIFECYCLE_CAP || next == 0)
		return;
	enabled = intr_save();
	end = agent_identity_leases.lifecycle_published[slot];
	if (end != 0 && next <= end &&
	    end - next <= AGENT_IDENTITY_LEASE_LOW_WATER &&
	    agent_identity_leases.phase != AGENT_IDENTITY_LEASE_FAILED)
		agent_identity_leases.renew_requested = 1;
	intr_restore(enabled);
}

void
agent_identity_lease_snapshot(struct agent_identity_lease_snapshot *snapshot)
{
	const uint64 *ids;
	const uint64 *lifecycles;
	int enabled;

	if (snapshot == 0)
		return;
	enabled = intr_save();
	ids = agent_identity_leases.phase == AGENT_IDENTITY_LEASE_PREPARED ?
		agent_identity_leases.prepared : agent_identity_leases.published;
	lifecycles = agent_identity_leases.phase ==
		AGENT_IDENTITY_LEASE_PREPARED ?
		agent_identity_leases.lifecycle_prepared :
		agent_identity_leases.lifecycle_published;
	for (uint i = 0; i < AGENT_IDENTITY_ALLOCATOR_COUNT; i++)
		snapshot->ends[i] = ids[i];
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++)
		snapshot->lifecycle_ends[i] = lifecycles[i];
	intr_restore(enabled);
}

void
agent_identity_lease_recover_allocator(uint kind, uint64 end)
{
	int enabled;

	if (kind >= AGENT_IDENTITY_ALLOCATOR_COUNT)
		return;
	enabled = intr_save();
	if (!agent_identity_leases.allocator_recovered[kind]) {
		agent_identity_leases.published[kind] = end;
		agent_identity_leases.prepared[kind] = end;
		agent_identity_leases.allocator_recovered[kind] = 1;
	} else if (agent_identity_leases.published[kind] != 0 &&
		   (end == 0 || end > agent_identity_leases.published[kind])) {
		agent_identity_leases.published[kind] = end;
		if (agent_identity_leases.prepared[kind] != 0 &&
		    (end == 0 || end > agent_identity_leases.prepared[kind]))
			agent_identity_leases.prepared[kind] = end;
	}
	intr_restore(enabled);
}

void
agent_identity_lease_recover_lifecycle(uint slot, uint64 end)
{
	int enabled;

	if (slot >= WORKFLOW_LIFECYCLE_CAP)
		return;
	enabled = intr_save();
	if (!agent_identity_leases.lifecycle_recovered[slot]) {
		agent_identity_leases.lifecycle_published[slot] = end;
		agent_identity_leases.lifecycle_prepared[slot] = end;
		agent_identity_leases.lifecycle_recovered[slot] = 1;
	} else if (agent_identity_leases.lifecycle_published[slot] != 0 &&
		   (end == 0 ||
		    end > agent_identity_leases.lifecycle_published[slot])) {
		agent_identity_leases.lifecycle_published[slot] = end;
		if (agent_identity_leases.lifecycle_prepared[slot] != 0 &&
		    (end == 0 ||
		     end > agent_identity_leases.lifecycle_prepared[slot]))
			agent_identity_leases.lifecycle_prepared[slot] = end;
	}
	intr_restore(enabled);
}

int
agent_identity_lease_admission_ready(void)
{
	int enabled = intr_save();
	int ready = agent_identity_leases.admission_ready;

	intr_restore(enabled);
	return ready;
}
