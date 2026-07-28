#ifndef AGENT_OBSERVE_TEST_PHASE_ABI_H
#define AGENT_OBSERVE_TEST_PHASE_ABI_H

#include "agent_lifecycle_abi.h"

#define AGENT_OBSERVE_TEST_PHASE_MAGIC 0x4f425350U
#define AGENT_OBSERVE_TEST_PHASE_STATE_BYTES 168U

/*
 * Host-owned boot control for the observation power-cut profile.  This is
 * test data, not evidence: the Runner advances it only after the preceding
 * boot has satisfied its independently observed completion marker.
 */
struct agent_observe_test_evidence_identity {
	unsigned int scope_id;
	unsigned int agent_id;
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long max_sequence;
	unsigned long long max_span_id;
	unsigned long long max_event_id;
	unsigned long long actor_control_id;
	unsigned long long receipt_sequence;
	unsigned long long receipt_record_hash;
	unsigned long long receipt_id;
};

struct agent_observe_test_phase_state {
	unsigned int magic;
	unsigned int phase;
	struct agent_observe_test_evidence_identity evidence;
	struct agent_observe_test_evidence_identity successor;
};

_Static_assert(sizeof(struct agent_observe_test_phase_state) ==
	       AGENT_OBSERVE_TEST_PHASE_STATE_BYTES,
	       "observation test phase ABI drifted");

#endif
