#ifndef AGENT_OBSERVE_CAPACITY_H
#define AGENT_OBSERVE_CAPACITY_H

#include "types.h"
#include "workflow_lifecycle.h"

enum agent_observe_capacity_class {
	AGENT_OBSERVE_CAPACITY_ORDINARY = 0,
	AGENT_OBSERVE_CAPACITY_RECOVERY,
};

struct agent_observe_capacity_claim {
	uint slot;
	uint replace;
	uint recovery;
	uint expected_scope_id;
	struct workflow_lifecycle_key expected_lifecycle;
};

struct agent_observe_reap_cookie {
	uint slot;
	uint scope_id;
	uint reserved;
	uint reserved2;
	uint64 token;
	uint64 source_generation;
	uint64 bank_generation;
	struct workflow_lifecycle_key lifecycle;
};

enum agent_observe_reap_action {
	AGENT_OBSERVE_REAP_NONE = 0,
	AGENT_OBSERVE_REAP_AUTHORIZE,
	AGENT_OBSERVE_REAP_ERASE,
};

void agent_observe_capacity_init(void);
int agent_observe_capacity_admit(
	uint, struct workflow_lifecycle_key,
	enum agent_observe_capacity_class);
void agent_observe_capacity_abort(
	uint, struct workflow_lifecycle_key);
int agent_observe_capacity_claim(
	uint, struct workflow_lifecycle_key,
	struct agent_observe_capacity_claim *);
void agent_observe_capacity_release(
	uint, struct workflow_lifecycle_key);
int agent_observe_capacity_reap_begin(
	uint, struct workflow_lifecycle_key, uint64 *);
int agent_observe_capacity_reap_resume(
	struct workflow_lifecycle_key, uint64 *, uint64 *);
int agent_observe_capacity_reap_action(
	uint, uint, struct workflow_lifecycle_key, uint);
int agent_observe_capacity_suppresses_capture(
	uint, struct workflow_lifecycle_key, uint);
void agent_observe_capacity_replicated(uint);
void agent_observe_capacity_maintain(void);
int agent_observe_capacity_reap_query(
	struct workflow_lifecycle_key, uint64, int *, uint64 *,
	struct agent_observe_reap_cookie *);
int agent_observe_capacity_reap_consume(
	const struct agent_observe_reap_cookie *);
int agent_observe_capacity_recover_reap(
	uint, uint, struct workflow_lifecycle_key);

#endif
