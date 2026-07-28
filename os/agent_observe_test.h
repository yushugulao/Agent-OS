#ifndef AGENT_OBSERVE_TEST_H
#define AGENT_OBSERVE_TEST_H

#include "agent.h"

#ifdef AGENT_OBSERVE_TEST_PROFILE
int agent_observe_test_operation(uint);
int agent_observe_test_execute(
	struct agent_observe_recovery_request *, uint64, uint64 *, uint *, int *);
int agent_observe_test_drop_audit(struct proc *, uint, int, int, int, int);
void agent_observe_test_drop_only_captured(uint, uint64, uint64);
#endif

#endif
