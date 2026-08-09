#ifndef WORKFLOW_LIFECYCLE_H
#define WORKFLOW_LIFECYCLE_H

#include "types.h"

#define WORKFLOW_LIFECYCLE_ID_NONE 0U
#define WORKFLOW_LIFECYCLE_MAX_ACTIVE 4
#define WORKFLOW_LIFECYCLE_CAP (WORKFLOW_LIFECYCLE_MAX_ACTIVE * 2)

struct workflow_lifecycle_key {
	uint id;
	uint64 generation;
};

enum workflow_lifecycle_state {
	WORKFLOW_LIFECYCLE_FREE = 0,
	WORKFLOW_LIFECYCLE_ACTIVE,
	WORKFLOW_LIFECYCLE_CLOSING,
	WORKFLOW_LIFECYCLE_RETIRING,
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
int workflow_lifecycle_generation_floor(struct workflow_lifecycle_key key);
int workflow_lifecycle_generation_lease_floor(uint, uint64);
#ifdef AGENT_OBSERVE_TEST_PROFILE
int workflow_lifecycle_test_consume_generation(uint *, uint64 *);
#endif
int workflow_lifecycle_alloc_context_branch(struct workflow_lifecycle_key key,
					    uint64 *branch_generation);
int workflow_lifecycle_reclaim(struct workflow_lifecycle_key key);

#endif
