#ifndef AGENT_EXECUTION_CONTRACT_H
#define AGENT_EXECUTION_CONTRACT_H

#include "../include/agent_execution_contract_abi.h"
#include "agent_tool_protocol.h"
#include "resource_controller.h"
#include "types.h"
#include "workflow_lifecycle.h"

struct agent_op;
struct agent_result;
struct proc;

struct agent_execution_outcome {
	uint decision_reason;
	uint completion_flags;
	uint output_artifact_type;
	uint output_provenance_labels;
	uint64 evidence_ticket;
	uint64 phase_lease_generation;
	uint64 terminal_tick;
};

enum agent_execution_admission {
	AGENT_EXECUTION_ADMISSION_DENIED = 0,
	AGENT_EXECUTION_ADMISSION_EXECUTE,
	AGENT_EXECUTION_ADMISSION_CACHED,
	AGENT_EXECUTION_ADMISSION_CANCEL_PENDING,
};

enum agent_execution_effect_admission {
	AGENT_EXECUTION_EFFECT_STALE = -1,
	AGENT_EXECUTION_EFFECT_CANCELLED = 0,
	AGENT_EXECUTION_EFFECT_ALLOWED = 1,
};

#define AGENT_EXECUTION_CANCEL_SYNC_ERROR    (-1)
#define AGENT_EXECUTION_CANCEL_SYNC_PENDING  0
#define AGENT_EXECUTION_CANCEL_SYNC_COMPLETE 1
#define AGENT_EXECUTION_CANCEL_SYNC_DENIED   2

#define AGENT_EXECUTION_PREFLIGHT_F_HARD_DEADLINE (1U << 0)
#define AGENT_EXECUTION_PREFLIGHT_F_OUTPUT_NONE_ONLY (1U << 1)
#define AGENT_EXECUTION_PREFLIGHT_F_ALL \
	(AGENT_EXECUTION_PREFLIGHT_F_HARD_DEADLINE | \
	 AGENT_EXECUTION_PREFLIGHT_F_OUTPUT_NONE_ONLY)

#define AGENT_EXECUTION_PREFLIGHT_ERROR    (-1)
#define AGENT_EXECUTION_PREFLIGHT_ALLOW    0
#define AGENT_EXECUTION_PREFLIGHT_TERMINAL 1

#define AGENT_EXECUTION_FORCE_CANCEL_ERROR    (-1)
#define AGENT_EXECUTION_FORCE_CANCEL_PENDING  0
#define AGENT_EXECUTION_FORCE_CANCEL_COMPLETE 1
#define AGENT_EXECUTION_FORCE_CANCEL_CACHED   2
#define AGENT_EXECUTION_FORCE_CANCEL_DENIED   3

#define AGENT_EXECUTION_TIMEOUT_SYNC_ERROR    (-1)
#define AGENT_EXECUTION_TIMEOUT_SYNC_PENDING  0
#define AGENT_EXECUTION_TIMEOUT_SYNC_COMPLETE 1
#define AGENT_EXECUTION_TIMEOUT_SYNC_CACHED   2
#define AGENT_EXECUTION_TIMEOUT_SYNC_DENIED   3

#define AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL (1U << 0)
#define AGENT_EXECUTION_BINDING_INTERNAL_F_ALL \
	AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL

struct agent_execution_binding {
	struct workflow_lifecycle_key lifecycle;
	uint64 contract_generation;
	uint node_id;
	uint attempt_id;
	uchar input_fingerprint[AGENT_EXECUTION_DIGEST_SIZE];
	uint64 source_context_sequence;
	uchar schema_digest[AGENT_EXECUTION_DIGEST_SIZE];
	uint input_artifact_type;
	uint source_node_id;
	uint input_mode;
	uint input_flags;
	uint resource_slot;
	uint internal_flags;
	uint64 resource_generation;
	uint64 input_provenance_labels;
	uint64 source_control_id;
	int source_pid;
	uint source_reserved;
};

#define AGENT_EXECUTION_INPUT_INLINE   1U
#define AGENT_EXECUTION_INPUT_RESOURCE 2U
#define AGENT_EXECUTION_INPUT_F_OWNED    (1U << 0)
#define AGENT_EXECUTION_INPUT_F_BORROWED (1U << 1)
#define AGENT_EXECUTION_INPUT_F_ALL \
	(AGENT_EXECUTION_INPUT_F_OWNED | AGENT_EXECUTION_INPUT_F_BORROWED)

struct agent_execution_cancel_request {
	struct workflow_lifecycle_key lifecycle;
	uint64 contract_generation;
	uint64 target_request_id;
	uint node_id;
	uint attempt_id;
	int tool_id;
	uchar schema_digest[AGENT_EXECUTION_DIGEST_SIZE];
};

struct agent_execution_direct_guard {
	struct workflow_lifecycle_key lifecycle;
	int slot;
	int active;
};

struct agent_execution_preflight_result {
	int status;
	uint decision_reason;
	uint output_artifact_type;
	uint reserved;
	uint64 input_provenance_labels;
	uint64 output_provenance_labels;
	uint64 effective_deadline_tick;
	uint64 context_sequence;
	uint64 evidence_ticket;
};

struct agent_execution_claim {
	struct workflow_lifecycle_key lifecycle;
	uint64 contract_generation;
	uint64 request_id;
	uint64 source_context_sequence;
	uint64 source_control_id;
	uint64 input_provenance_labels;
	uint64 producer_control_id;
	uint node_id;
	uint source_node_id;
	uint attempt_id;
	int source_pid;
	int producer_pid;
	uint input_artifact_type;
	uint output_artifact_type;
	uint charge_class;
	uint decision_reason;
	uint64 deadline_tick;
	int slot;
	int legacy;
	int active;
	int cached;
	int edge_authorized;
	int deadline_expired;
	int dependency_failed;
	int retry_forbidden;
	uint prior_state;
	uint prior_flags;
	uint prior_attempt_id;
	uint prior_cache_index;
	int prior_status;
	uint prior_decision_reason;
	uint64 prior_request_id;
	uint64 prior_sequence;
	uchar prior_request_digest[AGENT_EXECUTION_DIGEST_SIZE];
	uchar input_fingerprint[AGENT_EXECUTION_DIGEST_SIZE];
	uchar request_digest[AGENT_EXECUTION_DIGEST_SIZE];
	struct agent_provenance_manifest manifest;
	uint64 exec_amounts[RESOURCE_KIND_COUNT];
	uint64 storage_amounts[RESOURCE_KIND_COUNT];
};

void agent_execution_contract_init(void);
void agent_execution_inline_input_fingerprint(
	const struct agent_op *, uchar[AGENT_EXECUTION_DIGEST_SIZE]);
int agent_execution_contract_enforced(struct workflow_lifecycle_key);
uint64 agent_execution_contract_generation(struct workflow_lifecycle_key);
int agent_execution_contract_direct_enter(
	struct proc *, struct agent_execution_direct_guard *);
void agent_execution_contract_direct_leave(
	struct agent_execution_direct_guard *);
int agent_execution_contract_file_pin_enter(
	struct proc *, struct agent_execution_direct_guard *);
void agent_execution_contract_file_pin_leave(
	struct agent_execution_direct_guard *);
enum agent_execution_admission agent_execution_contract_admit(
	struct proc *, const struct agent_execution_binding *,
	const struct agent_op *, uint64, uint, uint64,
	struct agent_execution_claim *, struct agent_result *,
	struct agent_execution_outcome *);
int agent_execution_contract_preflight(
	struct proc *, const struct agent_execution_binding *,
	const struct agent_op *, uint64, uint, uint64,
	struct agent_execution_preflight_result *);
void agent_execution_contract_complete(struct agent_execution_claim *,
				       const struct agent_result *,
				       const struct agent_execution_outcome *);
void agent_execution_contract_abort(struct agent_execution_claim *);
enum agent_execution_admission agent_execution_contract_cancel(
	struct proc *, const struct agent_execution_cancel_request *, uint64,
	struct agent_execution_claim *, struct agent_result *,
	struct agent_execution_outcome *);
enum agent_execution_admission agent_execution_contract_force_cancel(
	struct proc *, const struct agent_execution_cancel_request *,
	uint64, struct agent_execution_claim *, struct agent_result *,
	struct agent_execution_outcome *);
enum agent_execution_admission agent_execution_contract_timeout(
	struct proc *, const struct agent_execution_cancel_request *, uint64,
	uint64, struct agent_execution_claim *, struct agent_result *,
	struct agent_execution_outcome *);
int agent_execution_contract_claim_cached(
	struct agent_execution_claim *, struct agent_result *,
	struct agent_execution_outcome *);
enum agent_execution_effect_admission agent_execution_contract_effect_begin(
	struct agent_execution_claim *);
void agent_execution_contract_release(struct agent_execution_claim *);

int sys_agent_execution_contract(uint64 controladdr, uint64 resultaddr);

#endif
