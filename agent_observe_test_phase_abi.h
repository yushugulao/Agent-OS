#ifndef AGENT_OBSERVE_TEST_PHASE_ABI_H
#define AGENT_OBSERVE_TEST_PHASE_ABI_H

#include "agent_lifecycle_abi.h"

#define AGENT_OBSERVE_TEST_PHASE_MAGIC 0x4f425350U
#define AGENT_OBSERVE_TEST_PHASE_STATE_BYTES 168U

/*
 * 观测断电配置使用的 Host 启动控制。这是测试数据而非证据；仅当前一轮
 * 启动满足 Runner 独立观测的完成标记后，Runner 才推进它。
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
