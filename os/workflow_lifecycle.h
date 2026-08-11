#ifndef WORKFLOW_LIFECYCLE_H
#define WORKFLOW_LIFECYCLE_H

#include "types.h"

#define WORKFLOW_LIFECYCLE_ID_NONE 0U
#define WORKFLOW_LIFECYCLE_MAX_ACTIVE 4
#define WORKFLOW_LIFECYCLE_CAP (WORKFLOW_LIFECYCLE_MAX_ACTIVE * 2)
#define WORKFLOW_EVIDENCE_PAGE_COUNT 4U

struct workflow_lifecycle_key {
	uint id;
	uint64 generation;
};

static inline struct workflow_lifecycle_key workflow_lifecycle_none(void)
{
	struct workflow_lifecycle_key key = {
		.id = WORKFLOW_LIFECYCLE_ID_NONE,
		.generation = 0,
	};

	return key;
}

static inline int
workflow_lifecycle_key_valid(struct workflow_lifecycle_key key)
{
	return key.id != WORKFLOW_LIFECYCLE_ID_NONE && key.generation != 0;
}

static inline int
workflow_lifecycle_key_equal(struct workflow_lifecycle_key a,
			     struct workflow_lifecycle_key b)
{
	return a.id == b.id && a.generation == b.generation;
}

int workflow_lifecycle_create(uint scope_id,
			      struct workflow_lifecycle_key *key);
int workflow_lifecycle_prepare_create(void);
int workflow_lifecycle_join(struct workflow_lifecycle_key key);
int workflow_lifecycle_leave(struct workflow_lifecycle_key key);
int workflow_lifecycle_bind_controller(struct workflow_lifecycle_key key,
				       uint scope_id, uint64 control_id);
int workflow_lifecycle_unbind_controller(struct workflow_lifecycle_key key,
					 uint scope_id, uint64 control_id);
int workflow_lifecycle_controller_matches(struct workflow_lifecycle_key key,
					  uint scope_id, uint64 control_id);
int workflow_lifecycle_close_owned(uint scope_id,
				   struct workflow_lifecycle_key expected,
				   uint64 control_id,
				   struct workflow_lifecycle_key *closed);
int workflow_lifecycle_close_trusted(uint scope_id,
				     struct workflow_lifecycle_key *closed);
int workflow_lifecycle_active(struct workflow_lifecycle_key key);
int workflow_lifecycle_closing(struct workflow_lifecycle_key key);
int workflow_lifecycle_retiring(struct workflow_lifecycle_key key);
int workflow_lifecycle_scope(struct workflow_lifecycle_key key,
			     uint *scope_id);
int workflow_lifecycle_alloc_context_branch(struct workflow_lifecycle_key key,
					    uint64 *branch_generation);
/*
 * Workflow-wide operation gate.  Unlike the per-process Context lane, this
 * gate spans every process carrying the same immutable lifecycle key.
 */
int workflow_lifecycle_operation_enter(struct workflow_lifecycle_key key);
void workflow_lifecycle_operation_leave(struct workflow_lifecycle_key key);
/* Teardown is a cut-visible operation whose completion may outlive a syscall. */
int workflow_lifecycle_departure_enter(struct workflow_lifecycle_key key);
void workflow_lifecycle_departure_leave(struct workflow_lifecycle_key key);
int workflow_lifecycle_fence_begin(struct workflow_lifecycle_key key,
				   uint64 *fence_sequence);
int workflow_lifecycle_fence_end(struct workflow_lifecycle_key key,
				 uint64 fence_sequence, int committed);
int workflow_lifecycle_reclaim(struct workflow_lifecycle_key key);

#endif
