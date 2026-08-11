#ifndef AGENT_IDENTITY_LEASE_H
#define AGENT_IDENTITY_LEASE_H

#include "types.h"
#include "workflow_lifecycle.h"

enum agent_identity_allocator_kind {
	AGENT_IDENTITY_ALLOCATOR_AUDIT = 0,
	AGENT_IDENTITY_ALLOCATOR_SPAN,
	AGENT_IDENTITY_ALLOCATOR_EVENT,
	AGENT_IDENTITY_ALLOCATOR_CONTROL,
	AGENT_IDENTITY_ALLOCATOR_AGENT,
	AGENT_IDENTITY_ALLOCATOR_COUNT,
};

void agent_identity_lease_init(void);
int agent_identity_lease_allocator_contains(uint, uint64);
int agent_identity_lease_allocator_admit(uint, uint64, uint64);
int agent_identity_lease_allocator_renew(uint);
void agent_identity_lease_allocator_note_next(uint, uint64);
int agent_identity_lease_lifecycle_contains(uint, uint64);
int agent_identity_lease_lifecycle_renew(void);
void agent_identity_lease_lifecycle_note_next(uint, uint64);

#endif
