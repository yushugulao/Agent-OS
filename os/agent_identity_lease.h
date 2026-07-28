#ifndef AGENT_IDENTITY_LEASE_H
#define AGENT_IDENTITY_LEASE_H

#include "types.h"
#include "workflow_lifecycle.h"

#define AGENT_IDENTITY_LEASE_CHUNK 256ULL
/* Leave half of each durable window available while its successor replicates. */
#define AGENT_IDENTITY_LEASE_LOW_WATER \
	(AGENT_IDENTITY_LEASE_CHUNK / 2ULL)

enum agent_identity_allocator_kind {
	AGENT_IDENTITY_ALLOCATOR_AUDIT = 0,
	AGENT_IDENTITY_ALLOCATOR_SPAN,
	AGENT_IDENTITY_ALLOCATOR_EVENT,
	AGENT_IDENTITY_ALLOCATOR_CONTROL,
	AGENT_IDENTITY_ALLOCATOR_AGENT,
	AGENT_IDENTITY_ALLOCATOR_COUNT,
};

struct agent_identity_lease_snapshot {
	uint64 ends[AGENT_IDENTITY_ALLOCATOR_COUNT];
	uint64 lifecycle_ends[WORKFLOW_LIFECYCLE_CAP];
};

/* Return 1 only after the candidate image is replicated, 0 while pending. */
typedef int (*agent_identity_lease_persist_fn)(uint64 *, uint64 *);

void agent_identity_lease_init(void);
void agent_identity_lease_set_persist(agent_identity_lease_persist_fn);
int agent_identity_lease_storage_ready(void);
void agent_identity_lease_maintain(void);
int agent_identity_lease_maintenance_pending(void);
int agent_identity_lease_allocator_contains(uint, uint64);
int agent_identity_lease_allocator_admit(uint, uint64, uint64);
int agent_identity_lease_allocator_renew(uint);
void agent_identity_lease_allocator_note_next(uint, uint64);
void agent_identity_lease_allocator_force_exhausted(uint);
int agent_identity_lease_lifecycle_contains(uint, uint64);
int agent_identity_lease_lifecycle_renew(void);
void agent_identity_lease_lifecycle_note_next(uint, uint64);
void agent_identity_lease_snapshot(struct agent_identity_lease_snapshot *);
void agent_identity_lease_recover_allocator(uint, uint64);
void agent_identity_lease_recover_lifecycle(uint, uint64);
int agent_identity_lease_admission_ready(void);

#endif
