#ifndef AGENT_EXECUTION_CONTRACT_ABI_H
#define AGENT_EXECUTION_CONTRACT_ABI_H

#include "agent_lifecycle_abi.h"
#include "agent_resource_abi.h"
#include "agent_tool_abi.h"

/* Compact, deterministic contract ABI shared by the kernel and user space. */
#define AGENT_EXECUTION_CONTRACT_VERSION      1U
#define AGENT_EXECUTION_CONTRACT_NODE_VERSION 1U
#define AGENT_EXECUTION_CONTRACT_MAX_NODES    24U
#define AGENT_EXECUTION_NODE_MAX_ATTEMPTS      4U
#define AGENT_EXECUTION_CONTRACT_MAX_ATTEMPTS 48U
#define AGENT_EXECUTION_DIGEST_SIZE           32U
#define AGENT_EXECUTION_NODE_NONE             0xffffffffU

#define AGENT_EXECUTION_CONTRACT_CREATE 1U
#define AGENT_EXECUTION_CONTRACT_QUERY  2U
#define AGENT_EXECUTION_CONTRACT_RETIRE 3U

#define AGENT_EXECUTION_CONTRACT_F_ENFORCE (1U << 0)

#define AGENT_EXECUTION_CONTRACT_EMPTY     0U
#define AGENT_EXECUTION_CONTRACT_FROZEN    1U
#define AGENT_EXECUTION_CONTRACT_RETIRING  2U
#define AGENT_EXECUTION_CONTRACT_RECLAIMED 3U

#define AGENT_EXECUTION_NODE_BLOCKED   1U
#define AGENT_EXECUTION_NODE_READY     2U
#define AGENT_EXECUTION_NODE_RUNNING   3U
#define AGENT_EXECUTION_NODE_SUCCEEDED 4U
#define AGENT_EXECUTION_NODE_FAILED    5U
#define AGENT_EXECUTION_NODE_CANCELLED 6U

#define AGENT_EXECUTION_RETRY_FAILURE   (1U << 0)
#define AGENT_EXECUTION_RETRY_TIMEOUT   (1U << 1)
#define AGENT_EXECUTION_RETRY_CANCELLED (1U << 2)
#define AGENT_EXECUTION_RETRY_ALL \
	(AGENT_EXECUTION_RETRY_FAILURE | AGENT_EXECUTION_RETRY_TIMEOUT | \
	 AGENT_EXECUTION_RETRY_CANCELLED)

#define AGENT_EXECUTION_CANCEL_DENY  0U
#define AGENT_EXECUTION_CANCEL_ALLOW 1U

#define AGENT_ARTIFACT_NONE          0U
#define AGENT_ARTIFACT_BYTES         1U
#define AGENT_ARTIFACT_UTF8          2U
#define AGENT_ARTIFACT_JSON          3U
#define AGENT_ARTIFACT_FILE          4U
#define AGENT_ARTIFACT_MESSAGE       5U
#define AGENT_ARTIFACT_TASK          6U
#define AGENT_ARTIFACT_OPAQUE_HANDLE 7U
#define AGENT_ARTIFACT_WORKSPACE_MUTATION 8U
#define AGENT_ARTIFACT_TYPE_COUNT    9U

/* Stable structural decision reasons, suitable for Context/Evidence records. */
#define AGENT_EXECUTION_REASON_NONE                 0U
#define AGENT_EXECUTION_REASON_CONTRACT_REQUIRED    1U
#define AGENT_EXECUTION_REASON_STALE_LIFECYCLE      2U
#define AGENT_EXECUTION_REASON_STALE_CONTRACT       3U
#define AGENT_EXECUTION_REASON_CONTRACT_RETIRING    4U
#define AGENT_EXECUTION_REASON_UNKNOWN_NODE         5U
#define AGENT_EXECUTION_REASON_TOOL_MISMATCH        6U
#define AGENT_EXECUTION_REASON_SCHEMA_MISMATCH      7U
#define AGENT_EXECUTION_REASON_ILLEGAL_PREDECESSOR  8U
#define AGENT_EXECUTION_REASON_PREDECESSOR_PENDING  9U
#define AGENT_EXECUTION_REASON_CAPABILITY_MISSING  10U
#define AGENT_EXECUTION_REASON_DEADLINE_EXPIRED    11U
#define AGENT_EXECUTION_REASON_ATTEMPT_INVALID     12U
#define AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT    13U
#define AGENT_EXECUTION_REASON_NODE_BUSY           14U
#define AGENT_EXECUTION_REASON_NODE_COMPLETE       15U
#define AGENT_EXECUTION_REASON_PHASE_CREDIT        16U
#define AGENT_EXECUTION_REASON_CANCEL_DISALLOWED   17U
#define AGENT_EXECUTION_REASON_CONTRACT_INVALID    18U
#define AGENT_EXECUTION_REASON_SOURCE_SEQUENCE     19U
#define AGENT_EXECUTION_REASON_DEPENDENCY_FAILED   20U
#define AGENT_EXECUTION_REASON_CANCEL_REQUESTED    21U
#define AGENT_EXECUTION_REASON_CANCEL_TOO_LATE     22U

#define AGENT_CALL_VERSION_V3 3U
#define AGENT_RESPONSE_V3_F_CACHED (1U << 0)

/* A workflow owns at most one immutable contract; generation prevents ABA. */
struct agent_execution_contract_key {
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long generation;
};

/*
 * Nodes are submitted in topological order with node_id equal to their array
 * index. Bit i directly names node i, so all set bits must precede node_id.
 * Resource envelopes use
 * exact resource-controller units and are widened to uint64 inside the kernel.
 */
struct agent_execution_contract_node {
	unsigned int version;
	unsigned int size;
	unsigned int node_id;
	int tool_id;
	unsigned long long predecessor_mask;
	unsigned long long required_capabilities;
	unsigned long long accepted_input_labels;
	unsigned long long output_add_labels;
	unsigned long long side_effect_mask;
	unsigned char schema_digest[AGENT_EXECUTION_DIGEST_SIZE];
	unsigned long long deadline_tick;
	unsigned int input_artifact_type;
	unsigned int output_artifact_type;
	unsigned int max_attempts;
	unsigned int retry_policy;
	unsigned int cancel_policy;
	unsigned int charge_class;
	unsigned int flags;
	unsigned int reserved;
	unsigned short exec_envelope[AGENT_RESOURCE_KIND_COUNT];
	unsigned short storage_envelope[AGENT_RESOURCE_KIND_COUNT];
	unsigned int reserved_tail;
};

struct agent_execution_contract_control {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	struct agent_execution_contract_key key;
	unsigned long long request_id;
	unsigned char contract_fingerprint[AGENT_EXECUTION_DIGEST_SIZE];
	unsigned long long deadline_tick;
	unsigned long long nodes;
	unsigned int node_count;
	unsigned int node_size;
	unsigned long long reserved[2];
};

struct agent_execution_contract_result {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int state;
	struct agent_execution_contract_key key;
	unsigned long long request_id;
	unsigned char contract_fingerprint[AGENT_EXECUTION_DIGEST_SIZE];
	unsigned long long deadline_tick;
	unsigned long long created_tick;
	unsigned long long completed_mask;
	unsigned long long failed_mask;
	unsigned long long running_mask;
	unsigned int node_count;
	unsigned int flags;
	unsigned long long denial_count;
	unsigned long long replay_count;
	unsigned long long reserved[2];
};

/* V3 keeps the complete V2 request prefix and appends a frozen-node binding. */
struct agent_request_v3 {
	unsigned int version;
	unsigned int size;
	int tool_id;
	unsigned int param_count;
	unsigned long long request_id;
	unsigned long long flags;
	unsigned long long params;
	char tool_name[AGENT_TOOL_NAME_SIZE];
	struct agent_execution_contract_key contract;
	unsigned int node_id;
	unsigned int attempt_id;
	/*
	 * SHA-256 of the kernel-canonical inline input: the domain string
	 * "agentos.execution.inline-input.v1", followed by little-endian u64
	 * tool_id/arg0/arg1/flags, then payload length and payload bytes.
	 */
	unsigned char input_fingerprint[AGENT_EXECUTION_DIGEST_SIZE];
	unsigned long long source_context_sequence;
	unsigned char schema_digest[AGENT_EXECUTION_DIGEST_SIZE];
	unsigned int input_artifact_type;
	unsigned int source_node_id;
	/*
	 * A zero identity is valid only for a root inline input.  Cross-Agent
	 * predecessors and every resource input carry the kernel-issued producer
	 * identity in addition to the producer-local Context sequence.
	 */
	union {
		struct {
			unsigned long long source_control_id;
			int source_pid;
			unsigned int source_reserved;
		};
		/* Retain the V3 reserved-tail name and offset for old probes. */
		unsigned long long reserved[2];
	};
};

/* V3 likewise preserves the V2 response prefix byte-for-byte. */
struct agent_response_v3 {
	unsigned int version;
	unsigned int size;
	int status;
	int tool_id;
	unsigned long long request_id;
	unsigned long long sequence;
	unsigned long long value0;
	unsigned long long value1;
	unsigned long long value2;
	char tool_name[AGENT_TOOL_NAME_SIZE];
	char result[AGENT_RESULT_SIZE];
	struct agent_execution_contract_key contract;
	unsigned char input_fingerprint[AGENT_EXECUTION_DIGEST_SIZE];
	unsigned long long source_context_sequence;
	unsigned long long evidence_ticket;
	unsigned int node_id;
	unsigned int attempt_id;
	unsigned int output_artifact_type;
	unsigned int decision_reason;
	unsigned int completion_flags;
	unsigned int output_provenance_labels;
};

_Static_assert(sizeof(unsigned short) == 2,
	       "execution contract ABI requires 16-bit unsigned short");
_Static_assert(sizeof(struct agent_execution_contract_key) == 24,
	       "execution contract key ABI layout");
_Static_assert(sizeof(struct agent_execution_contract_node) == 168,
	       "execution contract node ABI layout");
_Static_assert(sizeof(struct agent_execution_contract_control) == 120,
	       "execution contract control ABI layout");
_Static_assert(sizeof(struct agent_execution_contract_result) == 160,
	       "execution contract result ABI layout");
_Static_assert(__builtin_offsetof(struct agent_request_v3, contract) == 72,
	       "tool request v3 must retain the v2 prefix");
_Static_assert(sizeof(struct agent_request_v3) == 200,
	       "tool request v3 ABI layout");
_Static_assert(__builtin_offsetof(struct agent_response_v3, contract) == 184,
	       "tool response v3 must retain the v2 prefix");
_Static_assert(sizeof(struct agent_response_v3) == 280,
	       "tool response v3 ABI layout");

#endif
