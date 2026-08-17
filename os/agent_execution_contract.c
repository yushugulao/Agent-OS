#include "agent_execution_contract.h"
#include "agent_evidence_ring.h"
#include "agent_internal.h"
#include "agent_provenance.h"
#include "agent_sha256.h"
#include "agent_task_bridge.h"
#include "defs.h"
#include "exec_policy.h"
#include "timer.h"
#include "vfs_security.h"

#define AGENT_EXECUTION_COMPLETION_CACHE AGENT_EXECUTION_CONTRACT_MAX_ATTEMPTS
#define AGENT_EXECUTION_CACHE_NONE       0xffU
#define AGENT_EXECUTION_STATE_BUILDING   4U
#define AGENT_EXECUTION_KNOWN_CAPABILITIES \
	(AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ | \
	 AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH | \
	 AGENT_CAP_ACTION_WRITE | AGENT_CAP_ARTIFACT_WRITE | \
	 AGENT_CAP_AUDIT_WRITE | AGENT_CAP_META_WRITE | \
	 AGENT_CAP_ORCHESTRATE | AGENT_CAP_LLM_RELAY | \
	 AGENT_CAP_WAIT_CANCEL | AGENT_CAP_ROUTE_MANAGE | \
	 AGENT_CAP_TASK_ACCEPT | AGENT_CAP_WORKSPACE_WRITE)

struct agent_execution_node_internal {
	uint64 predecessor_mask;
	uint64 required_capabilities;
	uint64 deadline_tick;
	ushort exec_envelope[RESOURCE_KIND_COUNT];
	ushort storage_envelope[RESOURCE_KIND_COUNT];
	ushort tool_id;
	uchar accepted_input_labels;
	uchar output_add_labels;
	uchar side_effect_mask;
	uchar input_artifact_type;
	uchar output_artifact_type;
	uchar max_attempts;
	uchar retry_policy;
	uchar cancel_policy;
	uchar charge_class;
	uchar reserved;
};

struct agent_execution_node_runtime {
	uchar request_digest[AGENT_EXECUTION_DIGEST_SIZE];
	uint64 request_id;
	uint64 sequence;
	int status;
	uint decision_reason;
	uchar state;
	uchar attempt_id;
	uchar cache_index;
	uchar flags;
};

#define AGENT_EXECUTION_RUNTIME_F_DEPENDENCY_TERMINAL (1U << 0)
#define AGENT_EXECUTION_RUNTIME_F_RETRY_FORBIDDEN     (1U << 1)
#define AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED    (1U << 2)
#define AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED      (1U << 3)
#define AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED   (1U << 4)
#define AGENT_EXECUTION_RUNTIME_F_FORCE_CANCEL        (1U << 5)

struct agent_execution_completion {
	uint valid;
	ushort node_id;
	uchar attempt_id;
	uchar producer_valid;
	uchar request_digest[AGENT_EXECUTION_DIGEST_SIZE];
	uint64 producer_control_id;
	uint64 producer_context_sequence;
	uint64 producer_provenance_labels;
	int producer_pid;
	uint reserved;
	struct agent_result result;
	struct agent_execution_outcome outcome;
};

struct agent_execution_producer_metadata {
	uint64 control_id;
	uint64 context_sequence;
	uint64 provenance_labels;
	int pid;
	uint valid;
};

struct agent_execution_contract_record {
	struct workflow_lifecycle_key lifecycle;
	uint64 generation;
	uchar fingerprint[AGENT_EXECUTION_DIGEST_SIZE];
	uint64 deadline_tick;
	uint64 created_tick;
	uint64 completed_mask;
	uint64 failed_mask;
	uint64 running_mask;
	uint state;
	uint flags;
	uint node_count;
	uint total_attempts;
	uint bare_inflight;
	uint running_count;
	uint64 create_request_id;
	uint64 denial_count;
	uint64 replay_count;
	struct agent_execution_node_internal
		nodes[AGENT_EXECUTION_CONTRACT_MAX_NODES];
	struct agent_execution_node_runtime
		runtime[AGENT_EXECUTION_CONTRACT_MAX_NODES];
};

static struct agent_execution_contract_record
	agent_execution_contracts[WORKFLOW_LIFECYCLE_CAP];
static uint64 agent_execution_contract_generations[WORKFLOW_LIFECYCLE_CAP];
static struct agent_execution_completion agent_execution_completion_caches
	[WORKFLOW_LIFECYCLE_CAP][AGENT_EXECUTION_COMPLETION_CACHE];
static uchar agent_execution_schema_digests[WORKFLOW_LIFECYCLE_CAP]
	[AGENT_EXECUTION_CONTRACT_MAX_NODES][AGENT_EXECUTION_DIGEST_SIZE];
static struct agent_execution_producer_metadata agent_execution_producers
	[WORKFLOW_LIFECYCLE_CAP][AGENT_EXECUTION_CONTRACT_MAX_NODES];

static int agent_execution_controller_authorized(
	struct proc *, struct workflow_lifecycle_key);

_Static_assert(sizeof(struct agent_execution_contract_record) <= 4096,
	       "one execution contract must fit in one kernel page");
_Static_assert(AGENT_EXECUTION_CONTRACT_MAX_NODES <=
	       RESOURCE_PHASE_MAX_LEASES_PER_LIFECYCLE,
	       "contract concurrency must fit the phase-lease pool");

static int
agent_execution_digest_zero(const uchar digest[AGENT_EXECUTION_DIGEST_SIZE])
{
	uchar aggregate = 0;

	for (uint i = 0; i < AGENT_EXECUTION_DIGEST_SIZE; i++)
		aggregate |= digest[i];
	return aggregate == 0;
}

static int
agent_execution_digest_equal(
	const uchar a[AGENT_EXECUTION_DIGEST_SIZE],
	const uchar b[AGENT_EXECUTION_DIGEST_SIZE])
{
	return memcmp(a, b, AGENT_EXECUTION_DIGEST_SIZE) == 0;
}

static void
agent_execution_hash_u64(struct agent_sha256_ctx *ctx, uint64 value)
{
	uchar encoded[8];

	for (uint i = 0; i < sizeof(encoded); i++)
		encoded[i] = (uchar)(value >> (i * 8));
	agent_sha256_update(ctx, encoded, sizeof(encoded));
}

static void
agent_execution_hash_bytes(struct agent_sha256_ctx *ctx, const void *bytes,
			   uint size)
{
	agent_execution_hash_u64(ctx, size);
	agent_sha256_update(ctx, bytes, size);
}

static struct workflow_lifecycle_key
agent_execution_abi_lifecycle(struct agent_workflow_lifecycle_key key)
{
	struct workflow_lifecycle_key lifecycle = {
		.id = key.id,
		.generation = key.generation,
	};

	return lifecycle;
}

static struct agent_workflow_lifecycle_key
agent_execution_export_lifecycle(struct workflow_lifecycle_key lifecycle)
{
	struct agent_workflow_lifecycle_key key = {
		.id = lifecycle.id,
		.reserved = 0,
		.generation = lifecycle.generation,
	};

	return key;
}

static int
agent_execution_slot(struct workflow_lifecycle_key lifecycle)
{
	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    lifecycle.id > WORKFLOW_LIFECYCLE_CAP)
		return -1;
	return (int)lifecycle.id - 1;
}

static int
agent_execution_record_matches(
	const struct agent_execution_contract_record *record,
	struct workflow_lifecycle_key lifecycle)
{
	return workflow_lifecycle_key_equal(record->lifecycle, lifecycle);
}

static int
agent_execution_record_enforced(
	const struct agent_execution_contract_record *record,
	struct workflow_lifecycle_key lifecycle)
{
	return agent_execution_record_matches(record, lifecycle) &&
	       (record->state == AGENT_EXECUTION_CONTRACT_FROZEN ||
		record->state == AGENT_EXECUTION_CONTRACT_RETIRING);
}

static void
agent_execution_result_error(struct agent_result *result, int status,
			     const char *message, uint reason,
			     struct agent_execution_claim *claim)
{
	result->status = status;
	safestrcpy(result->result, message, sizeof(result->result));
	claim->decision_reason = reason;
}

static void
agent_execution_request_digest(
	const struct agent_execution_binding *binding, const struct agent_op *op,
	uint64 request_deadline_tick,
	uchar digest[AGENT_EXECUTION_DIGEST_SIZE])
{
	struct agent_sha256_ctx ctx;
	static const char domain[] = "agentos.execution.request.v1";

	agent_sha256_init(&ctx);
	agent_execution_hash_bytes(&ctx, domain, sizeof(domain));
	agent_execution_hash_u64(&ctx, binding->lifecycle.id);
	agent_execution_hash_u64(&ctx, binding->lifecycle.generation);
	agent_execution_hash_u64(&ctx, binding->contract_generation);
	agent_execution_hash_u64(&ctx, binding->node_id);
	agent_execution_hash_u64(&ctx, binding->attempt_id);
	agent_execution_hash_bytes(&ctx, binding->input_fingerprint,
				   sizeof(binding->input_fingerprint));
	agent_execution_hash_u64(&ctx, binding->source_context_sequence);
	agent_execution_hash_bytes(&ctx, binding->schema_digest,
				   sizeof(binding->schema_digest));
	agent_execution_hash_u64(&ctx, binding->input_artifact_type);
	agent_execution_hash_u64(&ctx, binding->source_node_id);
	agent_execution_hash_u64(&ctx, binding->input_mode);
	agent_execution_hash_u64(&ctx, binding->input_flags);
	agent_execution_hash_u64(&ctx, binding->resource_slot);
	agent_execution_hash_u64(&ctx, binding->resource_generation);
	agent_execution_hash_u64(&ctx, binding->input_provenance_labels);
	agent_execution_hash_u64(&ctx, binding->source_control_id);
	agent_execution_hash_u64(&ctx, (uint)binding->source_pid);
	agent_execution_hash_u64(&ctx, request_deadline_tick);
	agent_execution_hash_u64(&ctx, (uint)op->version);
	agent_execution_hash_u64(&ctx, (uint)op->tool_id);
	agent_execution_hash_u64(&ctx, op->arg0);
	agent_execution_hash_u64(&ctx, op->arg1);
	agent_execution_hash_u64(&ctx, op->flags);
	agent_execution_hash_bytes(&ctx, op->payload, sizeof(op->payload));
	agent_sha256_final(&ctx, digest);
}

static void
agent_execution_inline_input_digest(
	const struct agent_op *op,
	uchar digest[AGENT_EXECUTION_DIGEST_SIZE])
{
	struct agent_sha256_ctx ctx;
	static const char domain[] = "agentos.execution.inline-input.v1";
	uint payload_length = strlen(op->payload);

	agent_sha256_init(&ctx);
	agent_sha256_update(&ctx, domain, sizeof(domain) - 1);
	agent_execution_hash_u64(&ctx, (uint)op->tool_id);
	agent_execution_hash_u64(&ctx, op->arg0);
	agent_execution_hash_u64(&ctx, op->arg1);
	agent_execution_hash_u64(&ctx, op->flags);
	agent_execution_hash_u64(&ctx, payload_length);
	agent_sha256_update(&ctx, op->payload, payload_length);
	agent_sha256_final(&ctx, digest);
}

void
agent_execution_inline_input_fingerprint(
	const struct agent_op *op,
	uchar digest[AGENT_EXECUTION_DIGEST_SIZE])
{
	if (op == 0 || digest == 0)
		return;
	agent_execution_inline_input_digest(op, digest);
}

static int
agent_execution_binding_input_valid(
	const struct agent_execution_binding *binding,
	const struct agent_op *op, uint expected_artifact_type,
	const uchar inline_digest[AGENT_EXECUTION_DIGEST_SIZE])
{
	if (binding == 0 || op == 0 ||
	    (binding->internal_flags &
	     ~AGENT_EXECUTION_BINDING_INTERNAL_F_ALL) != 0 ||
	    binding->source_reserved != 0 ||
	    binding->input_artifact_type != expected_artifact_type ||
	    op->request_id == 0 ||
	    (binding->input_provenance_labels & ~AGENT_PROVENANCE_ALL) != 0)
		return 0;
	if (binding->input_mode == AGENT_EXECUTION_INPUT_INLINE)
		return binding->input_flags == 0 &&
		       binding->resource_slot == 0 &&
		       binding->resource_generation == 0 &&
		       binding->input_provenance_labels == 0 &&
		       agent_execution_digest_equal(binding->input_fingerprint,
					    inline_digest);
	if (binding->input_mode != AGENT_EXECUTION_INPUT_RESOURCE)
		return 0;
	return binding->resource_slot != 0 &&
	       binding->resource_generation != 0 &&
	       binding->source_control_id != 0 && binding->source_pid > 0 &&
	       binding->source_context_sequence != 0 &&
	       binding->input_provenance_labels != 0 &&
	       binding->input_artifact_type != AGENT_ARTIFACT_NONE &&
	       (binding->input_flags & ~AGENT_EXECUTION_INPUT_F_ALL) == 0 &&
	       (binding->input_flags == AGENT_EXECUTION_INPUT_F_OWNED ||
		binding->input_flags == AGENT_EXECUTION_INPUT_F_BORROWED) &&
	       !agent_execution_digest_zero(binding->input_fingerprint);
}

static int
agent_execution_cache_index(
	const struct agent_execution_contract_record *record, uint node_id,
	uint attempt_id)
{
	uint index = 0;

	if (record == 0 || node_id >= record->node_count || attempt_id == 0 ||
	    attempt_id > record->nodes[node_id].max_attempts)
		return -1;
	for (uint i = 0; i < node_id; i++)
		index += record->nodes[i].max_attempts;
	index += attempt_id - 1;
	return index < AGENT_EXECUTION_COMPLETION_CACHE ? (int)index : -1;
}

static int
agent_execution_predecessor_snapshot(
	struct proc *p, const struct agent_execution_contract_record *record,
	int slot, const struct agent_execution_node_internal *node,
	const struct agent_execution_binding *binding, int dependency_failed,
	uint64 *labels_out, uint *reason_out)
{
	const struct agent_execution_producer_metadata *source;
	uint64 labels = binding->input_provenance_labels;
	uint64 source_control_id = binding->source_control_id;
	int source_pid = binding->source_pid;

	if (labels_out == 0 || reason_out == 0)
		return 0;
	*labels_out = labels;
	*reason_out = AGENT_EXECUTION_REASON_NONE;
	if (dependency_failed) {
		for (uint i = 0; i < record->node_count; i++) {
			const struct agent_execution_producer_metadata *producer;

			if ((node->predecessor_mask & (1ULL << i)) == 0)
				continue;
			producer = &agent_execution_producers[slot][i];
			if (producer->valid)
				labels |= producer->provenance_labels;
		}
		*labels_out = labels;
		return 1;
	}
	if ((node->predecessor_mask & ~record->completed_mask) != 0) {
		*reason_out = AGENT_EXECUTION_REASON_PREDECESSOR_PENDING;
		return 0;
	}
	if (node->predecessor_mask == 0) {
		if (binding->source_node_id != AGENT_EXECUTION_NODE_NONE) {
			*reason_out = AGENT_EXECUTION_REASON_ILLEGAL_PREDECESSOR;
			return 0;
		}
		if (binding->input_mode == AGENT_EXECUTION_INPUT_INLINE &&
		    (binding->source_context_sequence != 0 ||
		     source_control_id != 0 || source_pid != 0)) {
			*reason_out = AGENT_EXECUTION_REASON_SOURCE_SEQUENCE;
			return 0;
		}
		return 1;
	}
	if (binding->source_node_id >= record->node_count ||
	    (node->predecessor_mask &
	     (1ULL << binding->source_node_id)) == 0) {
		*reason_out = AGENT_EXECUTION_REASON_ILLEGAL_PREDECESSOR;
		return 0;
	}
	source = &agent_execution_producers[slot][binding->source_node_id];
	/* Scalar inline callers may abbreviate an exact same-Agent producer. */
	if (source_control_id == 0 && source_pid == 0 &&
	    binding->input_mode == AGENT_EXECUTION_INPUT_INLINE) {
		source_control_id = p->agent_control_id;
		source_pid = p->pid;
	}
	if (!source->valid || source->context_sequence == 0 ||
	    source->control_id == 0 || source->pid <= 0 ||
	    binding->source_context_sequence != source->context_sequence ||
	    source_control_id != source->control_id || source_pid != source->pid ||
	    (binding->input_mode == AGENT_EXECUTION_INPUT_RESOURCE &&
	     binding->input_provenance_labels != source->provenance_labels)) {
		*reason_out = AGENT_EXECUTION_REASON_SOURCE_SEQUENCE;
		return 0;
	}
	for (uint i = 0; i < record->node_count; i++) {
		const struct agent_execution_producer_metadata *producer;

		if ((node->predecessor_mask & (1ULL << i)) == 0)
			continue;
		producer = &agent_execution_producers[slot][i];
		if (!producer->valid || producer->control_id == 0 ||
		    producer->pid <= 0 || producer->context_sequence == 0 ||
		    producer->provenance_labels == 0) {
			*reason_out = AGENT_EXECUTION_REASON_SOURCE_SEQUENCE;
			return 0;
		}
		labels |= producer->provenance_labels;
	}
	*labels_out = labels;
	return 1;
}

static int
agent_execution_retry_allowed(
	const struct agent_execution_node_internal *node, int status)
{
	uint policy = status == AGENT_STATUS_TIMEOUT ?
		AGENT_EXECUTION_RETRY_TIMEOUT :
		status == AGENT_STATUS_CANCELLED ?
			AGENT_EXECUTION_RETRY_CANCELLED :
			AGENT_EXECUTION_RETRY_FAILURE;

	return (node->retry_policy & policy) != 0;
}

static int
agent_execution_predecessors_complete(
	const struct agent_execution_contract_record *record,
	const struct agent_execution_node_internal *node)
{
	return (node->predecessor_mask & ~record->completed_mask) == 0;
}

static void
agent_execution_propagate_dependency_failure(
	struct agent_execution_contract_record *record)
{
	uint64 poisoned = record->failed_mask;
	int changed;

	for (uint i = 0; i < record->node_count; i++)
		if ((record->runtime[i].flags &
		     AGENT_EXECUTION_RUNTIME_F_DEPENDENCY_TERMINAL) != 0)
			poisoned |= 1ULL << i;
	do {
		changed = 0;
		for (uint i = 0; i < record->node_count; i++) {
			struct agent_execution_node_runtime *runtime =
				&record->runtime[i];

			if ((runtime->state != AGENT_EXECUTION_NODE_BLOCKED &&
			     runtime->state != AGENT_EXECUTION_NODE_READY) ||
			    (record->nodes[i].predecessor_mask & poisoned) == 0 ||
			    (runtime->flags &
			     AGENT_EXECUTION_RUNTIME_F_DEPENDENCY_TERMINAL) != 0)
				continue;
			/* Public failed_mask changes only with this node's Evidence. */
			runtime->state = AGENT_EXECUTION_NODE_READY;
			runtime->status = AGENT_STATUS_CANCELLED;
			runtime->decision_reason =
				AGENT_EXECUTION_REASON_DEPENDENCY_FAILED;
			runtime->flags |=
				AGENT_EXECUTION_RUNTIME_F_DEPENDENCY_TERMINAL |
				AGENT_EXECUTION_RUNTIME_F_RETRY_FORBIDDEN;
			poisoned |= 1ULL << i;
			changed = 1;
		}
	} while (changed);
}

static int
agent_execution_cache_copy(
	struct agent_execution_contract_record *record, uint node_id,
	uint attempt_id,
	const uchar request_digest[AGENT_EXECUTION_DIGEST_SIZE],
	struct agent_result *result, struct agent_execution_outcome *outcome)
{
	int cache_index = agent_execution_cache_index(
		record, node_id, attempt_id);

	if (cache_index >= 0) {
		struct agent_execution_completion *cached =
			&agent_execution_completion_caches
				[record - agent_execution_contracts]
				[cache_index];

		if (cached->valid &&
		    cached->node_id == node_id &&
		    cached->attempt_id == attempt_id &&
		    agent_execution_digest_equal(cached->request_digest,
					 request_digest)) {
			memmove(result, &cached->result, sizeof(*result));
			if (outcome != 0)
				memmove(outcome, &cached->outcome,
					sizeof(*outcome));
			return 1;
		}
	}
	return 0;
}

void
agent_execution_contract_init(void)
{
	memset(agent_execution_contracts, 0,
	       sizeof(agent_execution_contracts));
	memset(agent_execution_contract_generations, 0,
	       sizeof(agent_execution_contract_generations));
	memset(agent_execution_completion_caches, 0,
	       sizeof(agent_execution_completion_caches));
	memset(agent_execution_schema_digests, 0,
	       sizeof(agent_execution_schema_digests));
	memset(agent_execution_producers, 0,
	       sizeof(agent_execution_producers));
}

int
agent_execution_contract_enforced(struct workflow_lifecycle_key lifecycle)
{
	int slot = agent_execution_slot(lifecycle);
	int result = 0;
	int enabled;

	if (slot < 0)
		return 0;
	enabled = intr_save();
	result = agent_execution_record_enforced(
		&agent_execution_contracts[slot], lifecycle);
	intr_restore(enabled);
	return result;
}

uint64
agent_execution_contract_generation(struct workflow_lifecycle_key lifecycle)
{
	int slot = agent_execution_slot(lifecycle);
	uint64 generation = 0;
	int enabled;

	if (slot < 0)
		return 0;
	enabled = intr_save();
	if (agent_execution_record_enforced(
		    &agent_execution_contracts[slot], lifecycle))
		generation = agent_execution_contracts[slot].generation;
	intr_restore(enabled);
	return generation;
}

int
agent_execution_contract_direct_enter(
	struct proc *p, uint64 side_effect_mask,
	struct agent_execution_direct_guard *guard)
{
	struct agent_execution_contract_record *record;
	struct workflow_lifecycle_key lifecycle;
	int slot;
	int enabled;

	if (guard == 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(guard, 0, sizeof(*guard));
	guard->lifecycle = workflow_lifecycle_none();
	guard->slot = -1;
	guard->delegated_slot = -1;
	if (p == 0 || side_effect_mask == 0 ||
	    (side_effect_mask & ~AGENT_SIDE_EFFECT_ALL) != 0)
		return AGENT_STATUS_BAD_PARAM;
	/* The measured bootstrap remains the sole workflow factory escape. */
	if (!p->is_agent && p->resource_domain_admin &&
	    exec_policy_process_bootstrap(p))
		return AGENT_STATUS_OK;
	lifecycle = vfs_proc_lifecycle(p);
	slot = agent_execution_slot(lifecycle);
	if (slot < 0)
		return AGENT_STATUS_OK;
	enabled = intr_save();
	record = &agent_execution_contracts[slot];
	if (!agent_execution_record_matches(record, lifecycle)) {
		if (record->bare_inflight != 0 || record->running_count != 0) {
			intr_restore(enabled);
			return AGENT_STATUS_RETRY;
		}
		memset(record, 0, sizeof(*record));
		record->lifecycle = lifecycle;
	}
	if (record->state == AGENT_EXECUTION_STATE_BUILDING) {
		intr_restore(enabled);
		return AGENT_STATUS_RETRY;
	}
	if (agent_execution_record_enforced(record, lifecycle)) {
		if (agent_task_bridge_effect_pin_locked(
			    p, curr_thread(), side_effect_mask,
			    &guard->delegated_slot,
			    &guard->delegated_generation)) {
			guard->lifecycle = lifecycle;
			guard->active = 1;
			guard->delegated_active = 1;
			intr_restore(enabled);
			return AGENT_STATUS_OK;
		}
		record->denial_count++;
		intr_restore(enabled);
		return AGENT_STATUS_DENIED;
	}
	if (record->bare_inflight == (uint)-1) {
		intr_restore(enabled);
		return AGENT_STATUS_RETRY;
	}
	record->bare_inflight++;
	guard->lifecycle = lifecycle;
	guard->slot = slot;
	guard->active = 1;
	intr_restore(enabled);
	return AGENT_STATUS_OK;
}

void
agent_execution_contract_direct_leave(
	struct agent_execution_direct_guard *guard)
{
	struct agent_execution_contract_record *record;
	int enabled;

	if (guard == 0 || !guard->active)
		return;
	if (guard->delegated_active) {
		enabled = intr_save();
		agent_task_bridge_effect_unpin_locked(
			guard->delegated_slot, guard->delegated_generation);
		guard->active = 0;
		guard->delegated_active = 0;
		intr_restore(enabled);
		return;
	}
	if (guard->slot < 0 || guard->slot >= WORKFLOW_LIFECYCLE_CAP)
		panic("execution direct gate slot");
	enabled = intr_save();
	record = &agent_execution_contracts[guard->slot];
	if (!agent_execution_record_matches(record, guard->lifecycle) ||
	    record->bare_inflight == 0)
		panic("execution direct gate owner");
	record->bare_inflight--;
	guard->active = 0;
	intr_restore(enabled);
}

int
agent_execution_contract_file_pin_enter(
	struct proc *p, struct agent_execution_direct_guard *guard)
{
	struct agent_execution_contract_record *record;
	struct workflow_lifecycle_key lifecycle;
	int slot;
	int enabled;

	if (guard == 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(guard, 0, sizeof(*guard));
	guard->lifecycle = workflow_lifecycle_none();
	guard->slot = -1;
	if (p == 0)
		return AGENT_STATUS_BAD_PARAM;
	lifecycle = vfs_proc_lifecycle(p);
	slot = agent_execution_slot(lifecycle);
	if (slot < 0)
		return AGENT_STATUS_OK;
	enabled = intr_save();
	record = &agent_execution_contracts[slot];
	if (!agent_execution_record_matches(record, lifecycle)) {
		if (record->bare_inflight != 0 || record->running_count != 0) {
			intr_restore(enabled);
			return AGENT_STATUS_RETRY;
		}
		memset(record, 0, sizeof(*record));
		record->lifecycle = lifecycle;
	}
	/* BUILDING owns the publication cut.  Once enforcement is visible, new
	 * read-only pins may proceed without changing contract authority. */
	if (record->state == AGENT_EXECUTION_STATE_BUILDING) {
		intr_restore(enabled);
		return AGENT_STATUS_RETRY;
	}
	if (agent_execution_record_enforced(record, lifecycle)) {
		intr_restore(enabled);
		return AGENT_STATUS_OK;
	}
	if (record->bare_inflight == (uint)-1) {
		intr_restore(enabled);
		return AGENT_STATUS_RETRY;
	}
	record->bare_inflight++;
	guard->lifecycle = lifecycle;
	guard->slot = slot;
	guard->active = 1;
	intr_restore(enabled);
	return AGENT_STATUS_OK;
}

void
agent_execution_contract_file_pin_leave(
	struct agent_execution_direct_guard *guard)
{
	agent_execution_contract_direct_leave(guard);
}

static void
agent_execution_preflight_decide(
	struct agent_execution_preflight_result *result, int status,
	uint reason)
{
	result->status = status;
	result->decision_reason = reason;
}

int
agent_execution_contract_preflight(
	struct proc *p, const struct agent_execution_binding *binding,
	const struct agent_op *op, uint64 request_deadline_tick, uint flags,
	uint64 now, struct agent_execution_preflight_result *result)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_internal node;
	struct agent_execution_node_runtime runtime;
	struct agent_provenance_manifest manifest;
	struct agent_provenance_manifest authorize_manifest;
	struct agent_provenance_request provenance_request;
	struct agent_provenance_decision provenance_decision;
	struct workflow_lifecycle_key current;
	uchar input_digest[AGENT_EXECUTION_DIGEST_SIZE];
	uchar request_digest[AGENT_EXECUTION_DIGEST_SIZE];
	uint64 contract_deadline;
	uint64 labels;
	uint predecessor_reason = AGENT_EXECUTION_REASON_NONE;
	int dependency_failed = 0;
	int cache_index;
	int slot;
	int enabled;
	int status;

	if (result == 0)
		return -1;
	memset(result, 0, sizeof(*result));
	agent_execution_preflight_decide(
		result, AGENT_STATUS_BAD_PARAM,
		AGENT_EXECUTION_REASON_CONTRACT_INVALID);
	if (p == 0 || binding == 0 || op == 0)
		return -1;
	result->input_provenance_labels =
		agent_provenance_current_labels(p) |
		binding->input_provenance_labels |
		AGENT_PROVENANCE_AGENT_DERIVED;
	result->output_provenance_labels =
		result->input_provenance_labels;
	if (!p->is_agent) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_NOT_AGENT,
			AGENT_EXECUTION_REASON_CONTRACT_INVALID);
		return 0;
	}
	if ((flags & ~AGENT_EXECUTION_PREFLIGHT_F_ALL) != 0 ||
	    (((flags & AGENT_EXECUTION_PREFLIGHT_F_HARD_DEADLINE) != 0) !=
	     (request_deadline_tick != 0)) || op->request_id == 0 ||
	    op->tool_id <= 0) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			op->tool_id <= 0 ? AGENT_EXECUTION_REASON_TOOL_MISMATCH :
				AGENT_EXECUTION_REASON_CONTRACT_INVALID);
		return 0;
	}
	agent_execution_request_digest(
		binding, op, request_deadline_tick, request_digest);
	if (binding->input_mode == AGENT_EXECUTION_INPUT_INLINE)
		agent_execution_inline_input_digest(op, input_digest);
	else
		memset(input_digest, 0, sizeof(input_digest));
	current = vfs_proc_lifecycle(p);
	slot = agent_execution_slot(current);
	if (slot < 0) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_STALE,
			AGENT_EXECUTION_REASON_STALE_LIFECYCLE);
		return 0;
	}

	enabled = intr_save();
	record = &agent_execution_contracts[slot];
	if (!workflow_lifecycle_key_equal(binding->lifecycle, current) ||
	    !agent_execution_record_matches(record, current)) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_STALE,
			AGENT_EXECUTION_REASON_STALE_LIFECYCLE);
		goto out_locked;
	}
	if (!agent_execution_record_enforced(record, current)) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_CONTRACT_REQUIRED);
		goto out_locked;
	}
	if (record->state == AGENT_EXECUTION_CONTRACT_RETIRING) {
		if ((binding->internal_flags &
		     AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) != 0) {
			agent_execution_preflight_decide(
				result, AGENT_STATUS_OK,
				AGENT_EXECUTION_REASON_NONE);
			goto out_locked;
		}
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_CONTRACT_RETIRING);
		goto out_locked;
	}
	if (binding->contract_generation == 0 ||
	    binding->contract_generation != record->generation) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_STALE,
			AGENT_EXECUTION_REASON_STALE_CONTRACT);
		goto out_locked;
	}
	if (binding->node_id >= record->node_count) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_UNKNOWN_NODE);
		goto out_locked;
	}
	node = record->nodes[binding->node_id];
	runtime = record->runtime[binding->node_id];
	result->output_artifact_type = node.output_artifact_type;
	manifest.accepted_input_labels = node.accepted_input_labels;
	manifest.output_add_labels = node.output_add_labels;
	manifest.required_capabilities = node.required_capabilities;
	manifest.side_effect_mask = node.side_effect_mask;
	contract_deadline = node.deadline_tick != 0 &&
		(record->deadline_tick == 0 ||
		 node.deadline_tick < record->deadline_tick) ?
			node.deadline_tick : record->deadline_tick;
	result->effective_deadline_tick = request_deadline_tick != 0 ?
		request_deadline_tick : contract_deadline;
	if ((flags & AGENT_EXECUTION_PREFLIGHT_F_OUTPUT_NONE_ONLY) != 0 &&
	    node.output_artifact_type != AGENT_ARTIFACT_NONE) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_CONTRACT_INVALID);
		goto out_locked;
	}
	if (node.tool_id != (uint)op->tool_id) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_TOOL_MISMATCH);
		goto out_locked;
	}
	if (!agent_execution_digest_equal(
		    binding->schema_digest,
		    agent_execution_schema_digests[slot][binding->node_id])) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_STALE,
			AGENT_EXECUTION_REASON_SCHEMA_MISMATCH);
		goto out_locked;
	}
	if (!agent_execution_binding_input_valid(
		    binding, op, node.input_artifact_type, input_digest)) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_CONTRACT_INVALID);
		goto out_locked;
	}
	if ((flags & AGENT_EXECUTION_PREFLIGHT_F_HARD_DEADLINE) == 0 &&
	    contract_deadline != 0) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_CONTRACT_INVALID);
		goto out_locked;
	}
	if (request_deadline_tick != 0 && contract_deadline != 0 &&
	    request_deadline_tick > contract_deadline) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_DEADLINE_EXPIRED);
		goto out_locked;
	}
	cache_index = agent_execution_cache_index(
		record, binding->node_id, binding->attempt_id);
	if (cache_index < 0) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_ATTEMPT_INVALID);
		goto out_locked;
	}
	if (agent_execution_completion_caches[slot][cache_index].valid) {
		struct agent_execution_completion *cached =
			&agent_execution_completion_caches[slot][cache_index];

		if (!agent_execution_digest_equal(
			    cached->request_digest, request_digest)) {
			agent_execution_preflight_decide(
				result, AGENT_STATUS_DENIED,
				AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT);
			goto out_locked;
		}
		result->input_provenance_labels =
			cached->producer_provenance_labels;
		result->output_provenance_labels =
			cached->outcome.output_provenance_labels;
		agent_execution_preflight_decide(
			result, AGENT_STATUS_OK,
			AGENT_EXECUTION_REASON_NONE);
		goto out_locked;
	}
	dependency_failed =
		(runtime.flags &
		 AGENT_EXECUTION_RUNTIME_F_DEPENDENCY_TERMINAL) != 0;
	if (!agent_execution_predecessor_snapshot(
		    p, record, slot, &node, binding, dependency_failed,
		    &labels, &predecessor_reason)) {
		agent_execution_preflight_decide(
			result,
			predecessor_reason ==
					AGENT_EXECUTION_REASON_PREDECESSOR_PENDING ?
				AGENT_STATUS_RETRY : AGENT_STATUS_DENIED,
			predecessor_reason);
		goto out_locked;
	}
	if (!dependency_failed &&
	    !agent_identity_has_cap(p, node.required_capabilities)) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_CAPABILITY_MISSING);
		goto out_locked;
	}
	if (runtime.state == AGENT_EXECUTION_NODE_RUNNING) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_RETRY,
			AGENT_EXECUTION_REASON_NODE_BUSY);
		goto out_locked;
	}
	if (runtime.state == AGENT_EXECUTION_NODE_SUCCEEDED) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_NODE_COMPLETE);
		goto out_locked;
	}
	if (runtime.state == AGENT_EXECUTION_NODE_FAILED ||
	    runtime.state == AGENT_EXECUTION_NODE_CANCELLED) {
		if ((runtime.flags &
		     AGENT_EXECUTION_RUNTIME_F_RETRY_FORBIDDEN) != 0 ||
		    binding->attempt_id != (uint)runtime.attempt_id + 1 ||
		    !agent_execution_retry_allowed(&node, runtime.status)) {
			agent_execution_preflight_decide(
				result, AGENT_STATUS_DENIED,
				AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT);
			goto out_locked;
		}
	} else if (binding->attempt_id != 1) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_DENIED,
			AGENT_EXECUTION_REASON_ATTEMPT_INVALID);
		goto out_locked;
	}
	if (dependency_failed) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_CANCELLED,
			AGENT_EXECUTION_REASON_DEPENDENCY_FAILED);
		goto out_locked;
	}
	if (result->effective_deadline_tick != 0 &&
	    now >= result->effective_deadline_tick) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_TIMEOUT,
			AGENT_EXECUTION_REASON_DEADLINE_EXPIRED);
		goto out_locked;
	}
	if (record->running_count >= AGENT_EXECUTION_CONTRACT_MAX_NODES) {
		agent_execution_preflight_decide(
			result, AGENT_STATUS_RETRY,
			AGENT_EXECUTION_REASON_NODE_BUSY);
		goto out_locked;
	}
	memset(&provenance_request, 0, sizeof(provenance_request));
	provenance_request.lifecycle = current;
	provenance_request.contract_generation = binding->contract_generation;
	provenance_request.request_id = op->request_id;
	/* Contract metadata already qualified every producer identity and label. */
	provenance_request.source_context_sequence = 0;
	provenance_request.source_node_id = binding->source_node_id;
	provenance_request.target_node_id = binding->node_id;
	provenance_request.attempt_id = binding->attempt_id;
	memmove(provenance_request.input_fingerprint,
		binding->input_fingerprint,
		sizeof(provenance_request.input_fingerprint));
	provenance_request.declared_side_effect_mask =
		manifest.side_effect_mask;
	provenance_request.tool_id = op->tool_id;
	provenance_request.flags =
		AGENT_PROVENANCE_AUTH_F_BOUND_CONTRACT |
		AGENT_PROVENANCE_AUTH_F_EDGE_AUTHORIZED;
	authorize_manifest = manifest;
	authorize_manifest.accepted_input_labels = AGENT_PROVENANCE_ALL;
	intr_restore(enabled);

	status = agent_provenance_authorize_tool(
		p, &provenance_request, &authorize_manifest,
		&provenance_decision);
	labels |= provenance_decision.input_labels |
		  AGENT_PROVENANCE_AGENT_DERIVED;
	result->input_provenance_labels = labels;
	result->output_provenance_labels = labels;
	if (status != AGENT_STATUS_OK ||
	    (labels & ~manifest.accepted_input_labels) != 0) {
		agent_execution_preflight_decide(
			result,
			status == AGENT_STATUS_STALE ? AGENT_STATUS_STALE :
				AGENT_STATUS_DENIED,
			status == AGENT_STATUS_STALE ?
				AGENT_EXECUTION_REASON_STALE_LIFECYCLE :
			provenance_decision.reason ==
				AGENT_PROVENANCE_DENY_ILLEGAL_PREDECESSOR ?
				AGENT_EXECUTION_REASON_ILLEGAL_PREDECESSOR :
			provenance_decision.reason ==
				AGENT_PROVENANCE_DENY_CAPABILITY_MISSING ?
				AGENT_EXECUTION_REASON_CAPABILITY_MISSING :
				AGENT_EXECUTION_REASON_CONTRACT_INVALID);
		return 0;
	}
	result->output_provenance_labels =
		labels | manifest.output_add_labels |
		AGENT_PROVENANCE_AGENT_DERIVED;
	agent_execution_preflight_decide(
		result, AGENT_STATUS_OK, AGENT_EXECUTION_REASON_NONE);
	return 0;

out_locked:
	intr_restore(enabled);
	return 0;
}

enum agent_execution_admission
agent_execution_contract_admit(
	struct proc *p, const struct agent_execution_binding *binding,
	const struct agent_op *op, uint64 request_deadline_tick,
	uint admission_policy_flags, uint64 now,
	struct agent_execution_claim *claim, struct agent_result *result,
	struct agent_execution_outcome *outcome)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_internal *node;
	struct agent_execution_node_runtime *runtime;
	struct agent_tool_manifest tool_manifest;
	struct workflow_lifecycle_key current;
	uchar request_digest[AGENT_EXECUTION_DIGEST_SIZE];
	uchar input_digest[AGENT_EXECUTION_DIGEST_SIZE];
	uint64 contract_deadline;
	uint64 input_labels = 0;
	uint predecessor_reason = AGENT_EXECUTION_REASON_NONE;
	int cache_index;
	int slot;
	int enabled;

	memset(claim, 0, sizeof(*claim));
	if (outcome != 0)
		memset(outcome, 0, sizeof(*outcome));
	claim->slot = -1;
	claim->delegated_slot = -1;
	if (p == 0 || op == 0 || result == 0 || !p->is_agent) {
		agent_execution_result_error(
			result, AGENT_STATUS_NOT_AGENT, "not_agent",
			AGENT_EXECUTION_REASON_CONTRACT_INVALID, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	current = vfs_proc_lifecycle(p);
	slot = agent_execution_slot(current);
	claim->lifecycle = current;
	claim->slot = slot;
	claim->request_id = op->request_id;
	claim->producer_control_id = p->agent_control_id;
	claim->producer_pid = p->pid;
	if (binding != 0) {
		claim->contract_generation = binding->contract_generation;
		claim->node_id = binding->node_id;
		claim->source_node_id = binding->source_node_id;
		claim->attempt_id = binding->attempt_id;
		claim->source_context_sequence =
			binding->source_context_sequence;
		claim->source_control_id = binding->source_control_id;
		claim->source_pid = binding->source_pid;
		claim->input_artifact_type = binding->input_artifact_type;
		memmove(claim->input_fingerprint, binding->input_fingerprint,
			sizeof(claim->input_fingerprint));
	}
	if (slot < 0) {
		agent_execution_result_error(
			result, AGENT_STATUS_STALE, "stale_lifecycle",
			AGENT_EXECUTION_REASON_STALE_LIFECYCLE, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	if (agent_tool_protocol_manifest_query(op->tool_id, &tool_manifest) !=
		    AGENT_STATUS_OK) {
		agent_execution_result_error(
			result, AGENT_STATUS_UNKNOWN_TOOL, "unknown_tool",
			AGENT_EXECUTION_REASON_TOOL_MISMATCH, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	if (tool_manifest.flags == AGENT_TOOL_F_DEPRECATED) {
		agent_execution_result_error(
			result, AGENT_STATUS_DEPRECATED, "deprecated_tool",
			AGENT_EXECUTION_REASON_TOOL_MISMATCH, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	if (tool_manifest.flags == AGENT_TOOL_F_BROKERED &&
	    (binding == 0 ||
	     (binding->internal_flags &
	      AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) == 0 ||
	     binding->input_artifact_type != AGENT_ARTIFACT_TASK)) {
		agent_execution_result_error(
			result, AGENT_STATUS_BROKER_REQUIRED, "broker_required",
			AGENT_EXECUTION_REASON_TOOL_MISMATCH, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	if (binding == 0)
		claim->manifest = tool_manifest.provenance;

	if (binding != 0) {
		agent_execution_request_digest(
			binding, op, request_deadline_tick, request_digest);
		if (binding->input_mode == AGENT_EXECUTION_INPUT_INLINE)
			agent_execution_inline_input_digest(op, input_digest);
		else
			memset(input_digest, 0, sizeof(input_digest));
	}

	enabled = intr_save();
	record = &agent_execution_contracts[slot];
	if (!agent_execution_record_matches(record, current)) {
		if (record->bare_inflight != 0 || record->running_count != 0) {
			agent_execution_result_error(
				result, AGENT_STATUS_RETRY, "contract_gate_busy",
				AGENT_EXECUTION_REASON_NODE_BUSY, claim);
			goto denied;
		}
		memset(record, 0, sizeof(*record));
		record->lifecycle = current;
	}

	if (binding == 0) {
		if (admission_policy_flags != 0) {
			agent_execution_result_error(
				result, AGENT_STATUS_DENIED,
				"legacy_admission_policy",
				AGENT_EXECUTION_REASON_CONTRACT_INVALID, claim);
			goto denied_counted;
		}
		if (agent_execution_record_enforced(record, current)) {
			if (agent_task_bridge_effect_pin_locked(
				    p, curr_thread(),
				    tool_manifest.provenance.side_effect_mask,
				    &claim->delegated_slot,
				    &claim->delegated_generation)) {
				claim->legacy = 1;
				claim->delegated_active = 1;
				claim->active = 1;
				intr_restore(enabled);
				return AGENT_EXECUTION_ADMISSION_EXECUTE;
			}
			record->denial_count++;
			agent_execution_result_error(
				result, AGENT_STATUS_DENIED,
				"execution_contract_required",
				AGENT_EXECUTION_REASON_CONTRACT_REQUIRED, claim);
			goto denied;
		}
		if (record->state == AGENT_EXECUTION_STATE_BUILDING) {
			record->denial_count++;
			agent_execution_result_error(
				result, AGENT_STATUS_DENIED,
				"execution_contract_required",
				AGENT_EXECUTION_REASON_CONTRACT_REQUIRED, claim);
			goto denied;
		}
		if (record->bare_inflight == (uint)-1) {
			agent_execution_result_error(
				result, AGENT_STATUS_RETRY, "legacy_gate_full",
				AGENT_EXECUTION_REASON_NODE_BUSY, claim);
			goto denied;
		}
		record->bare_inflight++;
		claim->legacy = 1;
		claim->active = 1;
		/* Legacy callers retain their compatibility provenance policy. */
		claim->manifest.accepted_input_labels = AGENT_PROVENANCE_ALL;
		intr_restore(enabled);
		return AGENT_EXECUTION_ADMISSION_EXECUTE;
	}

	memmove(claim->request_digest, request_digest,
		sizeof(claim->request_digest));

	if (!workflow_lifecycle_key_equal(binding->lifecycle, current)) {
		agent_execution_result_error(
			result, AGENT_STATUS_STALE, "stale_lifecycle",
			AGENT_EXECUTION_REASON_STALE_LIFECYCLE, claim);
		goto denied_counted;
	}
	if (!agent_execution_record_enforced(record, current)) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "execution_contract_missing",
			AGENT_EXECUTION_REASON_CONTRACT_REQUIRED, claim);
		goto denied_counted;
	}
	if (record->state == AGENT_EXECUTION_CONTRACT_RETIRING) {
		agent_execution_result_error(
			result,
			(binding->internal_flags &
			 AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) != 0 ?
				AGENT_STATUS_DENIED : AGENT_STATUS_CANCELLED,
			"contract_retiring",
			AGENT_EXECUTION_REASON_CONTRACT_RETIRING, claim);
		goto denied_counted;
	}
	if (binding->contract_generation == 0 ||
	    binding->contract_generation != record->generation) {
		agent_execution_result_error(
			result, AGENT_STATUS_STALE, "stale_contract",
			AGENT_EXECUTION_REASON_STALE_CONTRACT, claim);
		goto denied_counted;
	}
	if (binding->node_id >= record->node_count) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "unknown_contract_node",
			AGENT_EXECUTION_REASON_UNKNOWN_NODE, claim);
		goto denied_counted;
	}
	node = &record->nodes[binding->node_id];
	runtime = &record->runtime[binding->node_id];
	claim->output_artifact_type = node->output_artifact_type;
	claim->charge_class = node->charge_class;
	contract_deadline = node->deadline_tick != 0 &&
		(record->deadline_tick == 0 ||
		 node->deadline_tick < record->deadline_tick) ?
			node->deadline_tick : record->deadline_tick;
	claim->deadline_tick = request_deadline_tick != 0 ?
		request_deadline_tick : contract_deadline;
	claim->manifest.accepted_input_labels = node->accepted_input_labels;
	claim->manifest.output_add_labels = node->output_add_labels;
	claim->manifest.required_capabilities = node->required_capabilities;
	claim->manifest.side_effect_mask = node->side_effect_mask;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		claim->exec_amounts[kind] = node->exec_envelope[kind];
		claim->storage_amounts[kind] = node->storage_envelope[kind];
	}
	if ((binding->internal_flags &
	     AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) != 0) {
		uint expected_policy =
			AGENT_EXECUTION_PREFLIGHT_F_OUTPUT_NONE_ONLY |
			(request_deadline_tick != 0 ?
				 AGENT_EXECUTION_PREFLIGHT_F_HARD_DEADLINE : 0);

		if (admission_policy_flags != expected_policy ||
		    (contract_deadline != 0 &&
		     (admission_policy_flags &
		      AGENT_EXECUTION_PREFLIGHT_F_HARD_DEADLINE) == 0) ||
		    node->output_artifact_type != AGENT_ARTIFACT_NONE) {
			/* A rejected Task request never publishes a typed output. */
			claim->output_artifact_type = AGENT_ARTIFACT_NONE;
			agent_execution_result_error(
				result, AGENT_STATUS_DENIED,
				"task_admission_policy",
				AGENT_EXECUTION_REASON_CONTRACT_INVALID, claim);
			goto denied_counted;
		}
	} else if (admission_policy_flags != 0) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED,
			"unexpected_admission_policy",
			AGENT_EXECUTION_REASON_CONTRACT_INVALID, claim);
		goto denied_counted;
	}
	if ((runtime->flags &
	     AGENT_EXECUTION_RUNTIME_F_DEPENDENCY_TERMINAL) != 0) {
		claim->dependency_failed = 1;
		claim->retry_forbidden = 1;
		claim->decision_reason =
			AGENT_EXECUTION_REASON_DEPENDENCY_FAILED;
	}
	if (node->tool_id != (uint)op->tool_id) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "contract_tool_mismatch",
			AGENT_EXECUTION_REASON_TOOL_MISMATCH, claim);
		goto denied_counted;
	}
	if (!agent_execution_digest_equal(binding->schema_digest,
					  agent_execution_schema_digests[slot]
						[binding->node_id])) {
		agent_execution_result_error(
			result, AGENT_STATUS_STALE, "schema_digest_mismatch",
			AGENT_EXECUTION_REASON_SCHEMA_MISMATCH, claim);
		goto denied_counted;
	}
	if (!agent_execution_binding_input_valid(
		    binding, op, node->input_artifact_type, input_digest)) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "bad_execution_binding",
			AGENT_EXECUTION_REASON_CONTRACT_INVALID, claim);
		goto denied_counted;
	}
	if (request_deadline_tick != 0 && contract_deadline != 0 &&
	    request_deadline_tick > contract_deadline) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "request_deadline_outside_contract",
			AGENT_EXECUTION_REASON_DEADLINE_EXPIRED, claim);
		goto denied_counted;
	}
	cache_index = agent_execution_cache_index(
		record, binding->node_id, binding->attempt_id);
	if (cache_index < 0) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "attempt_invalid",
			AGENT_EXECUTION_REASON_ATTEMPT_INVALID, claim);
		goto denied_counted;
	}
	if (agent_execution_completion_caches[slot][cache_index].valid) {
		if (agent_execution_cache_copy(
			    record, binding->node_id, binding->attempt_id,
			    request_digest, result, outcome)) {
			record->replay_count++;
			claim->cached = 1;
			intr_restore(enabled);
			return AGENT_EXECUTION_ADMISSION_CACHED;
		}
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "attempt_replay_conflict",
			AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT, claim);
		goto denied_counted;
	}
	if (!agent_execution_predecessor_snapshot(
		    p, record, slot, node, binding, claim->dependency_failed,
		    &input_labels, &predecessor_reason)) {
		agent_execution_result_error(
			result,
			predecessor_reason ==
					AGENT_EXECUTION_REASON_PREDECESSOR_PENDING ?
				AGENT_STATUS_RETRY : AGENT_STATUS_DENIED,
			predecessor_reason ==
					AGENT_EXECUTION_REASON_PREDECESSOR_PENDING ?
				"predecessor_pending" : "predecessor_binding",
			predecessor_reason, claim);
		goto denied_counted;
	}
	claim->input_provenance_labels = input_labels;
	claim->edge_authorized = !claim->dependency_failed;
	if (!claim->dependency_failed &&
	    !agent_identity_has_cap(p, node->required_capabilities)) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "capability_missing",
			AGENT_EXECUTION_REASON_CAPABILITY_MISSING, claim);
		goto denied_counted;
	}
	if (runtime->state == AGENT_EXECUTION_NODE_RUNNING) {
		agent_execution_result_error(
			result,
			agent_execution_digest_equal(runtime->request_digest,
						     request_digest) ?
				AGENT_STATUS_RETRY : AGENT_STATUS_DENIED,
				"node_running", AGENT_EXECUTION_REASON_NODE_BUSY,
				claim);
		goto denied_counted;
	}
	if (runtime->state == AGENT_EXECUTION_NODE_SUCCEEDED ||
	    runtime->state == AGENT_EXECUTION_NODE_FAILED ||
	    runtime->state == AGENT_EXECUTION_NODE_CANCELLED) {
		if (runtime->state == AGENT_EXECUTION_NODE_SUCCEEDED) {
			agent_execution_result_error(
				result, AGENT_STATUS_DENIED, "node_complete",
				AGENT_EXECUTION_REASON_NODE_COMPLETE, claim);
			goto denied_counted;
		}
		if ((runtime->flags &
		     AGENT_EXECUTION_RUNTIME_F_RETRY_FORBIDDEN) != 0 ||
		    binding->attempt_id != (uint)runtime->attempt_id + 1 ||
		    !agent_execution_retry_allowed(node, runtime->status)) {
			agent_execution_result_error(
				result, AGENT_STATUS_DENIED, "retry_not_allowed",
				AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT, claim);
			goto denied_counted;
		}
	} else if (binding->attempt_id != 1) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "first_attempt_must_be_one",
			AGENT_EXECUTION_REASON_ATTEMPT_INVALID, claim);
		goto denied_counted;
	}
	if (claim->deadline_tick != 0 && now >= claim->deadline_tick) {
		claim->deadline_expired = 1;
		claim->retry_forbidden = 1;
		claim->decision_reason =
			AGENT_EXECUTION_REASON_DEADLINE_EXPIRED;
	}
	if (record->running_count >= AGENT_EXECUTION_CONTRACT_MAX_NODES) {
		agent_execution_result_error(
			result, AGENT_STATUS_RETRY, "contract_concurrency_full",
			AGENT_EXECUTION_REASON_NODE_BUSY, claim);
		goto denied_counted;
	}

	claim->prior_state = runtime->state;
	claim->prior_flags = runtime->flags;
	claim->prior_attempt_id = runtime->attempt_id;
	claim->prior_cache_index = runtime->cache_index;
	claim->prior_status = runtime->status;
	claim->prior_decision_reason = runtime->decision_reason;
	claim->prior_request_id = runtime->request_id;
	claim->prior_sequence = runtime->sequence;
	memmove(claim->prior_request_digest, runtime->request_digest,
		sizeof(claim->prior_request_digest));
	memmove(runtime->request_digest, request_digest,
		sizeof(runtime->request_digest));
	runtime->request_id = op->request_id;
	runtime->attempt_id = binding->attempt_id;
	runtime->state = AGENT_EXECUTION_NODE_RUNNING;
	runtime->cache_index = AGENT_EXECUTION_CACHE_NONE;
	runtime->flags = 0;
	record->running_mask |= 1ULL << binding->node_id;
	record->running_count++;
	claim->active = 1;
	intr_restore(enabled);
	return AGENT_EXECUTION_ADMISSION_EXECUTE;

denied_counted:
	record->denial_count++;
denied:
	intr_restore(enabled);
	return AGENT_EXECUTION_ADMISSION_DENIED;
}

static enum agent_execution_admission
agent_execution_contract_cancel_common(
	struct proc *p, const struct agent_execution_cancel_request *request,
	uint64 now, struct agent_execution_claim *claim,
	struct agent_result *result, struct agent_execution_outcome *outcome,
	int force)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_internal *node;
	struct agent_execution_node_runtime *runtime;
	struct agent_tool_manifest manifest;
	struct workflow_lifecycle_key current;
	int slot;
	int enabled;

	memset(claim, 0, sizeof(*claim));
	if (outcome != 0) {
		memset(outcome, 0, sizeof(*outcome));
		outcome->terminal_tick = now;
	}
	claim->slot = -1;
	if (p == 0 || request == 0 || result == 0 || !p->is_agent ||
	    request->target_request_id == 0 || request->tool_id <= 0) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "bad_cancel_request",
			AGENT_EXECUTION_REASON_CONTRACT_INVALID, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	current = vfs_proc_lifecycle(p);
	slot = agent_execution_slot(current);
	claim->lifecycle = current;
	claim->slot = slot;
	claim->contract_generation = request->contract_generation;
	claim->request_id = request->target_request_id;
	claim->node_id = request->node_id;
	claim->source_node_id = AGENT_EXECUTION_NODE_NONE;
	claim->attempt_id = request->attempt_id;
	if (slot < 0 ||
	    agent_tool_protocol_manifest_query(request->tool_id, &manifest) !=
		    AGENT_STATUS_OK) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "bad_cancel_tool",
			AGENT_EXECUTION_REASON_TOOL_MISMATCH, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	claim->manifest = manifest.provenance;

	enabled = intr_save();
	record = &agent_execution_contracts[slot];
	if (!workflow_lifecycle_key_equal(request->lifecycle, current) ||
	    !agent_execution_record_enforced(record, current)) {
		agent_execution_result_error(
			result, AGENT_STATUS_STALE, "stale_cancel_contract",
			AGENT_EXECUTION_REASON_STALE_CONTRACT, claim);
		goto denied_counted;
	}
	if (!force && !agent_execution_controller_authorized(p, current)) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "cancel_not_controller",
			AGENT_EXECUTION_REASON_CAPABILITY_MISSING, claim);
		goto denied_counted;
	}
	if (record->state == AGENT_EXECUTION_CONTRACT_RETIRING) {
		agent_execution_result_error(
			result, AGENT_STATUS_CANCELLED, "contract_retiring",
			AGENT_EXECUTION_REASON_CONTRACT_RETIRING, claim);
		goto denied_counted;
	}
	if (request->contract_generation == 0 ||
	    request->contract_generation != record->generation) {
		agent_execution_result_error(
			result, AGENT_STATUS_STALE, "stale_cancel_generation",
			AGENT_EXECUTION_REASON_STALE_CONTRACT, claim);
		goto denied_counted;
	}
	if (request->node_id >= record->node_count || request->attempt_id == 0) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "bad_cancel_node",
			AGENT_EXECUTION_REASON_UNKNOWN_NODE, claim);
		goto denied_counted;
	}
	node = &record->nodes[request->node_id];
	runtime = &record->runtime[request->node_id];
	claim->output_artifact_type = node->output_artifact_type;
	claim->manifest.accepted_input_labels = node->accepted_input_labels;
	claim->manifest.output_add_labels = node->output_add_labels;
	claim->manifest.required_capabilities = node->required_capabilities;
	claim->manifest.side_effect_mask = node->side_effect_mask;
	if (outcome != 0)
		outcome->output_artifact_type = node->output_artifact_type;
	if (node->tool_id != (uint)request->tool_id ||
	    !agent_execution_digest_equal(
		    request->schema_digest,
		    agent_execution_schema_digests[slot][request->node_id])) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "cancel_binding_mismatch",
			AGENT_EXECUTION_REASON_SCHEMA_MISMATCH, claim);
		goto denied_counted;
	}
	if (!force && node->cancel_policy != AGENT_EXECUTION_CANCEL_ALLOW) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "cancel_disallowed",
			AGENT_EXECUTION_REASON_CANCEL_DISALLOWED, claim);
		goto denied_counted;
	}
	if (runtime->state == AGENT_EXECUTION_NODE_RUNNING) {
		if (runtime->request_id != request->target_request_id ||
		    runtime->attempt_id != request->attempt_id) {
			agent_execution_result_error(
				result, AGENT_STATUS_DENIED,
				"cancel_target_mismatch",
				AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT, claim);
			goto denied_counted;
		}
		if ((runtime->flags &
		     AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED) != 0) {
			agent_execution_result_error(
				result, AGENT_STATUS_RETRY,
				"cancel_timeout_claimed",
				AGENT_EXECUTION_REASON_CANCEL_TOO_LATE, claim);
			if (force) {
				intr_restore(enabled);
				return AGENT_EXECUTION_ADMISSION_CANCEL_PENDING;
			}
			goto denied_counted;
		}
		if ((runtime->flags &
		     AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED) != 0) {
			agent_execution_result_error(
				result, AGENT_STATUS_RETRY,
				"cancel_effect_started",
				AGENT_EXECUTION_REASON_CANCEL_TOO_LATE, claim);
			if (force) {
				intr_restore(enabled);
				return AGENT_EXECUTION_ADMISSION_CANCEL_PENDING;
			}
			goto denied_counted;
		}
		runtime->flags |=
			AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED;
		if (force)
			runtime->flags |=
				AGENT_EXECUTION_RUNTIME_F_FORCE_CANCEL;
		claim->decision_reason =
			AGENT_EXECUTION_REASON_CANCEL_REQUESTED;
		result->status = AGENT_STATUS_CANCELLED;
		result->tool_id = request->tool_id;
		result->request_id = request->target_request_id;
		safestrcpy(result->result, "cancel_pending",
			   sizeof(result->result));
		intr_restore(enabled);
		return AGENT_EXECUTION_ADMISSION_CANCEL_PENDING;
	}
	{
		int cache_index = agent_execution_cache_index(
			record, request->node_id, request->attempt_id);

		if (cache_index >= 0) {
			struct agent_execution_completion *cached =
				&agent_execution_completion_caches[slot][cache_index];

			if (cached->valid && cached->node_id == request->node_id &&
			    cached->attempt_id == request->attempt_id &&
			    cached->result.request_id == request->target_request_id) {
				memmove(result, &cached->result, sizeof(*result));
				if (outcome != 0)
					memmove(outcome, &cached->outcome,
						sizeof(*outcome));
			record->replay_count++;
			claim->cached = 1;
			intr_restore(enabled);
			return AGENT_EXECUTION_ADMISSION_CACHED;
			}
		}
	}
	agent_execution_result_error(
		result, AGENT_STATUS_DENIED, "cancel_target_not_accepted",
		AGENT_EXECUTION_REASON_ATTEMPT_INVALID, claim);
	goto denied_counted;

denied_counted:
	record->denial_count++;
	intr_restore(enabled);
	return AGENT_EXECUTION_ADMISSION_DENIED;
}

enum agent_execution_admission
agent_execution_contract_cancel(
	struct proc *p, const struct agent_execution_cancel_request *request,
	uint64 now, struct agent_execution_claim *claim,
	struct agent_result *result, struct agent_execution_outcome *outcome)
{
	return agent_execution_contract_cancel_common(
		p, request, now, claim, result, outcome, 0);
}

enum agent_execution_admission
agent_execution_contract_force_cancel(
	struct proc *p, const struct agent_execution_cancel_request *request,
	uint64 now, struct agent_execution_claim *claim,
	struct agent_result *result, struct agent_execution_outcome *outcome)
{
	return agent_execution_contract_cancel_common(
		p, request, now, claim, result, outcome, 1);
}

enum agent_execution_admission
agent_execution_contract_timeout(
	struct proc *p, const struct agent_execution_cancel_request *request,
	uint64 request_deadline_tick, uint64 now,
	struct agent_execution_claim *claim, struct agent_result *result,
	struct agent_execution_outcome *outcome)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_internal *node;
	struct agent_execution_node_runtime *runtime;
	struct workflow_lifecycle_key current;
	uint64 contract_deadline;
	int slot;
	int enabled;

	memset(claim, 0, sizeof(*claim));
	if (outcome != 0) {
		memset(outcome, 0, sizeof(*outcome));
		outcome->terminal_tick = now;
	}
	claim->slot = -1;
	if (p == 0 || request == 0 || result == 0 || !p->is_agent ||
	    request->target_request_id == 0 || request->tool_id <= 0 ||
	    request_deadline_tick == 0 || now < request_deadline_tick) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "bad_timeout_request",
			AGENT_EXECUTION_REASON_CONTRACT_INVALID, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	current = vfs_proc_lifecycle(p);
	slot = agent_execution_slot(current);
	claim->lifecycle = current;
	claim->slot = slot;
	claim->contract_generation = request->contract_generation;
	claim->request_id = request->target_request_id;
	claim->node_id = request->node_id;
	claim->source_node_id = AGENT_EXECUTION_NODE_NONE;
	claim->attempt_id = request->attempt_id;
	claim->producer_control_id = p->agent_control_id;
	claim->producer_pid = p->pid;
	if (slot < 0) {
		agent_execution_result_error(
			result, AGENT_STATUS_STALE, "stale_timeout_lifecycle",
			AGENT_EXECUTION_REASON_STALE_LIFECYCLE, claim);
		return AGENT_EXECUTION_ADMISSION_DENIED;
	}
	enabled = intr_save();
	record = &agent_execution_contracts[slot];
	if (!workflow_lifecycle_key_equal(request->lifecycle, current) ||
	    !agent_execution_record_enforced(record, current) ||
	    request->contract_generation == 0 ||
	    request->contract_generation != record->generation) {
		agent_execution_result_error(
			result, AGENT_STATUS_STALE, "stale_timeout_contract",
			AGENT_EXECUTION_REASON_STALE_CONTRACT, claim);
		goto denied_counted;
	}
	if (request->node_id >= record->node_count || request->attempt_id == 0) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "bad_timeout_node",
			AGENT_EXECUTION_REASON_UNKNOWN_NODE, claim);
		goto denied_counted;
	}
	node = &record->nodes[request->node_id];
	runtime = &record->runtime[request->node_id];
	claim->output_artifact_type = node->output_artifact_type;
	claim->manifest.accepted_input_labels = node->accepted_input_labels;
	claim->manifest.output_add_labels = node->output_add_labels;
	claim->manifest.required_capabilities = node->required_capabilities;
	claim->manifest.side_effect_mask = node->side_effect_mask;
	if (outcome != 0)
		outcome->output_artifact_type = node->output_artifact_type;
	if (node->tool_id != (uint)request->tool_id ||
	    !agent_execution_digest_equal(
		    request->schema_digest,
		    agent_execution_schema_digests[slot][request->node_id])) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "timeout_binding_mismatch",
			AGENT_EXECUTION_REASON_SCHEMA_MISMATCH, claim);
		goto denied_counted;
	}
	contract_deadline = node->deadline_tick != 0 &&
		(record->deadline_tick == 0 ||
		 node->deadline_tick < record->deadline_tick) ?
			node->deadline_tick : record->deadline_tick;
	if ((contract_deadline != 0 &&
	     request_deadline_tick > contract_deadline) ||
	    now < (contract_deadline != 0 &&
		   contract_deadline < request_deadline_tick ?
			   contract_deadline : request_deadline_tick)) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "timeout_not_due",
			AGENT_EXECUTION_REASON_DEADLINE_EXPIRED, claim);
		goto denied_counted;
	}
	{
		int cache_index = agent_execution_cache_index(
			record, request->node_id, request->attempt_id);

		if (cache_index >= 0) {
			struct agent_execution_completion *cached =
				&agent_execution_completion_caches[slot][cache_index];

			if (cached->valid && cached->result.request_id ==
					     request->target_request_id) {
				memmove(result, &cached->result, sizeof(*result));
				if (outcome != 0)
					memmove(outcome, &cached->outcome,
						sizeof(*outcome));
				record->replay_count++;
				claim->cached = 1;
				intr_restore(enabled);
				if (cached->result.status == AGENT_STATUS_TIMEOUT)
					return AGENT_EXECUTION_ADMISSION_CACHED;
				agent_execution_result_error(
					result, AGENT_STATUS_DENIED,
					"timeout_effect_won",
					AGENT_EXECUTION_REASON_CANCEL_TOO_LATE,
					claim);
				return AGENT_EXECUTION_ADMISSION_DENIED;
			}
		}
	}
	if (runtime->state != AGENT_EXECUTION_NODE_RUNNING ||
	    runtime->request_id != request->target_request_id ||
	    runtime->attempt_id != request->attempt_id) {
		agent_execution_result_error(
			result, AGENT_STATUS_DENIED, "timeout_target_not_running",
			AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT, claim);
		goto denied_counted;
	}
	if ((runtime->flags &
	     (AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED |
	      AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED)) != 0) {
		claim->decision_reason = AGENT_EXECUTION_REASON_CANCEL_TOO_LATE;
		result->status = AGENT_STATUS_RETRY;
		intr_restore(enabled);
		return AGENT_EXECUTION_ADMISSION_CANCEL_PENDING;
	}
	runtime->flags |= AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED;
	claim->deadline_tick = request_deadline_tick;
	claim->decision_reason = AGENT_EXECUTION_REASON_DEADLINE_EXPIRED;
	claim->retry_forbidden = 1;
	claim->input_provenance_labels = agent_provenance_current_labels(p);
	memmove(claim->request_digest, runtime->request_digest,
		sizeof(claim->request_digest));
	claim->active = 1;
	intr_restore(enabled);
	return AGENT_EXECUTION_ADMISSION_EXECUTE;

denied_counted:
	record->denial_count++;
	intr_restore(enabled);
	return AGENT_EXECUTION_ADMISSION_DENIED;
}

int
agent_execution_contract_claim_cached(
	struct agent_execution_claim *claim, struct agent_result *result,
	struct agent_execution_outcome *outcome)
{
	struct agent_execution_contract_record *record;
	int copied = 0;
	int enabled;

	if (claim == 0 || result == 0 || !claim->active || claim->legacy ||
	    claim->slot < 0 || claim->slot >= WORKFLOW_LIFECYCLE_CAP)
		return 0;
	enabled = intr_save();
	record = &agent_execution_contracts[claim->slot];
	if (agent_execution_record_matches(record, claim->lifecycle) &&
	    record->generation == claim->contract_generation)
		copied = agent_execution_cache_copy(
			record, claim->node_id, claim->attempt_id,
			claim->request_digest, result, outcome);
	if (copied)
		claim->active = 0;
	intr_restore(enabled);
	return copied;
}

enum agent_execution_effect_admission
agent_execution_contract_effect_begin(struct agent_execution_claim *claim)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_runtime *runtime;
	int enabled;

	if (claim == 0 || !claim->active)
		return AGENT_EXECUTION_EFFECT_STALE;
	if (claim->legacy)
		return AGENT_EXECUTION_EFFECT_ALLOWED;
	if (claim->slot < 0 || claim->slot >= WORKFLOW_LIFECYCLE_CAP ||
	    claim->node_id >= AGENT_EXECUTION_CONTRACT_MAX_NODES)
		return AGENT_EXECUTION_EFFECT_STALE;
	enabled = intr_save();
	record = &agent_execution_contracts[claim->slot];
	if (!agent_execution_record_matches(record, claim->lifecycle) ||
	    record->generation != claim->contract_generation ||
	    claim->node_id >= record->node_count) {
		intr_restore(enabled);
		return AGENT_EXECUTION_EFFECT_STALE;
	}
	runtime = &record->runtime[claim->node_id];
	if (runtime->state != AGENT_EXECUTION_NODE_RUNNING ||
	    runtime->request_id != claim->request_id ||
	    runtime->attempt_id != claim->attempt_id ||
	    !agent_execution_digest_equal(runtime->request_digest,
					  claim->request_digest)) {
		intr_restore(enabled);
		return AGENT_EXECUTION_EFFECT_STALE;
	}
	if ((runtime->flags &
	     AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED) != 0) {
		claim->decision_reason =
			AGENT_EXECUTION_REASON_DEADLINE_EXPIRED;
		intr_restore(enabled);
		return AGENT_EXECUTION_EFFECT_STALE;
	}
	if ((runtime->flags &
	     AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED) != 0) {
		claim->decision_reason =
			AGENT_EXECUTION_REASON_CANCEL_REQUESTED;
		if ((runtime->flags &
		     AGENT_EXECUTION_RUNTIME_F_FORCE_CANCEL) != 0)
			claim->retry_forbidden = 1;
		intr_restore(enabled);
		return AGENT_EXECUTION_EFFECT_CANCELLED;
	}
	runtime->flags |= AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED;
	intr_restore(enabled);
	return AGENT_EXECUTION_EFFECT_ALLOWED;
}

enum agent_execution_delegate_cancel_admission
agent_execution_contract_delegate_cancel_preflight_locked(
	struct agent_execution_claim *claim, uint64 now)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_internal *node;
	struct agent_execution_node_runtime *runtime;

	if (intr_get())
		panic("execution delegated cancel unlocked");
	if (claim == 0 || !claim->active || claim->legacy ||
	    claim->slot < 0 || claim->slot >= WORKFLOW_LIFECYCLE_CAP ||
	    claim->node_id >= AGENT_EXECUTION_CONTRACT_MAX_NODES)
		return AGENT_EXECUTION_DELEGATE_CANCEL_STALE;
	record = &agent_execution_contracts[claim->slot];
	if (!agent_execution_record_matches(record, claim->lifecycle) ||
	    record->generation != claim->contract_generation ||
	    claim->node_id >= record->node_count)
		return AGENT_EXECUTION_DELEGATE_CANCEL_STALE;
	node = &record->nodes[claim->node_id];
	runtime = &record->runtime[claim->node_id];
	if (runtime->state != AGENT_EXECUTION_NODE_RUNNING ||
	    runtime->request_id != claim->request_id ||
	    runtime->attempt_id != claim->attempt_id ||
	    !agent_execution_digest_equal(runtime->request_digest,
				  claim->request_digest))
		return AGENT_EXECUTION_DELEGATE_CANCEL_STALE;
	if ((runtime->flags & AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED) != 0 ||
	    (claim->deadline_tick != 0 && now >= claim->deadline_tick)) {
		claim->decision_reason = AGENT_EXECUTION_REASON_DEADLINE_EXPIRED;
		claim->retry_forbidden = 1;
		return AGENT_EXECUTION_DELEGATE_CANCEL_TIMEOUT;
	}
	if (node->cancel_policy != AGENT_EXECUTION_CANCEL_ALLOW)
		return AGENT_EXECUTION_DELEGATE_CANCEL_DENIED;
	return AGENT_EXECUTION_DELEGATE_CANCEL_ALLOWED;
}

void
agent_execution_contract_delegate_cancel_commit_locked(
	struct agent_execution_claim *claim)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_runtime *runtime;

	if (intr_get())
		panic("execution delegated cancel commit unlocked");
	/*
	 * The bridge calls this only after an ALLOWED policy preflight and exact
	 * Task request commit in the same interrupts-disabled transaction.  The
	 * claimed provider's cleanup/ACK boundary keeps the owner CQE pending
	 * until that provider has removed its prebound output.
	 */
	record = &agent_execution_contracts[claim->slot];
	runtime = &record->runtime[claim->node_id];
	runtime->flags |= AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED;
	claim->decision_reason = AGENT_EXECUTION_REASON_CANCEL_REQUESTED;
	claim->retry_forbidden = 1;
}

void
agent_execution_contract_complete(struct agent_execution_claim *claim,
				  const struct agent_result *result,
				  const struct agent_execution_outcome *outcome)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_internal *node;
	struct agent_execution_node_runtime *runtime;
	struct agent_execution_completion *cached;
	struct agent_execution_producer_metadata *producer;
	int cache_index;
	int terminal_failure;
	int enabled;

	if (claim == 0 || result == 0 || !claim->active || claim->legacy ||
	    claim->slot < 0 ||
	    claim->slot >= WORKFLOW_LIFECYCLE_CAP ||
	    claim->node_id >= AGENT_EXECUTION_CONTRACT_MAX_NODES)
		return;
	enabled = intr_save();
	record = &agent_execution_contracts[claim->slot];
	if (!agent_execution_record_matches(record, claim->lifecycle) ||
	    record->generation != claim->contract_generation ||
	    claim->node_id >= record->node_count) {
		intr_restore(enabled);
		return;
	}
	runtime = &record->runtime[claim->node_id];
	node = &record->nodes[claim->node_id];
	if (runtime->state != AGENT_EXECUTION_NODE_RUNNING ||
	    runtime->attempt_id != claim->attempt_id ||
	    !agent_execution_digest_equal(runtime->request_digest,
					  claim->request_digest)) {
		intr_restore(enabled);
		return;
	}
	if ((runtime->flags &
	     AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED) != 0 &&
	    result->status != AGENT_STATUS_CANCELLED)
		panic("execution contract cancel winner");
	if ((runtime->flags &
	     AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED) != 0 &&
	    result->status != AGENT_STATUS_TIMEOUT)
		panic("execution contract timeout winner");
	if (result->sequence == 0 || outcome == 0 ||
	    outcome->evidence_ticket == 0 ||
	    outcome->output_provenance_labels == 0)
		panic("execution contract terminal without evidence");
	runtime->sequence = result->sequence;
	runtime->status = result->status;
	runtime->decision_reason = claim->decision_reason;
	record->running_mask &= ~(1ULL << claim->node_id);
	if (record->running_count == 0)
		panic("execution contract running underflow");
	record->running_count--;
	producer = &agent_execution_producers[claim->slot][claim->node_id];
	if (claim->producer_control_id == 0 || claim->producer_pid <= 0)
		panic("execution contract producer identity");
	producer->control_id = claim->producer_control_id;
	producer->pid = claim->producer_pid;
	producer->context_sequence = result->sequence;
	producer->provenance_labels = outcome->output_provenance_labels;
	producer->valid = 1;
	if (result->status == AGENT_STATUS_OK) {
		runtime->state = AGENT_EXECUTION_NODE_SUCCEEDED;
		record->completed_mask |= 1ULL << claim->node_id;
		record->failed_mask &= ~(1ULL << claim->node_id);
	} else {
		runtime->state = result->status == AGENT_STATUS_CANCELLED ?
			AGENT_EXECUTION_NODE_CANCELLED :
			AGENT_EXECUTION_NODE_FAILED;
		record->completed_mask &= ~(1ULL << claim->node_id);
		terminal_failure = claim->retry_forbidden ||
			claim->attempt_id >= node->max_attempts ||
			!agent_execution_retry_allowed(node, result->status);
		if (terminal_failure) {
			record->failed_mask |= 1ULL << claim->node_id;
			runtime->flags |=
				AGENT_EXECUTION_RUNTIME_F_RETRY_FORBIDDEN;
			agent_execution_propagate_dependency_failure(record);
		} else {
			record->failed_mask &= ~(1ULL << claim->node_id);
			runtime->flags &=
				~AGENT_EXECUTION_RUNTIME_F_RETRY_FORBIDDEN;
		}
	}
	cache_index = agent_execution_cache_index(
		record, claim->node_id, claim->attempt_id);
	if (cache_index < 0 || cache_index >= AGENT_EXECUTION_COMPLETION_CACHE)
		panic("execution contract cache index");
	cached = &agent_execution_completion_caches[claim->slot][cache_index];
	if (cached->valid)
		panic("execution contract cache overwrite");
	memset(cached, 0, sizeof(*cached));
	cached->valid = 1;
	cached->node_id = claim->node_id;
	cached->attempt_id = claim->attempt_id;
	cached->producer_valid = 1;
	cached->producer_control_id = claim->producer_control_id;
	cached->producer_pid = claim->producer_pid;
	cached->producer_context_sequence = result->sequence;
	cached->producer_provenance_labels =
		outcome->output_provenance_labels;
	memmove(cached->request_digest, claim->request_digest,
		sizeof(cached->request_digest));
	memmove(&cached->result, result, sizeof(cached->result));
	if (outcome != 0)
		memmove(&cached->outcome, outcome, sizeof(cached->outcome));
	runtime->cache_index = cache_index;
	for (uint i = 0; i < record->node_count; i++)
		if (record->runtime[i].state == AGENT_EXECUTION_NODE_BLOCKED &&
		    agent_execution_predecessors_complete(record,
						  &record->nodes[i]))
			record->runtime[i].state = AGENT_EXECUTION_NODE_READY;
	claim->active = 0;
	intr_restore(enabled);
}

void
agent_execution_contract_abort(struct agent_execution_claim *claim)
{
	struct agent_execution_contract_record *record;
	struct agent_execution_node_runtime *runtime;
	int enabled;

	if (claim == 0 || !claim->active || claim->legacy || claim->slot < 0 ||
	    claim->slot >= WORKFLOW_LIFECYCLE_CAP ||
	    claim->node_id >= AGENT_EXECUTION_CONTRACT_MAX_NODES)
		return;
	enabled = intr_save();
	record = &agent_execution_contracts[claim->slot];
	if (!agent_execution_record_matches(record, claim->lifecycle) ||
	    record->generation != claim->contract_generation ||
	    claim->node_id >= record->node_count)
		panic("execution contract abort owner");
	runtime = &record->runtime[claim->node_id];
	if (runtime->state != AGENT_EXECUTION_NODE_RUNNING ||
	    runtime->attempt_id != claim->attempt_id ||
	    !agent_execution_digest_equal(runtime->request_digest,
					  claim->request_digest) ||
	    record->running_count == 0)
		panic("execution contract abort state");
	record->running_count--;
	record->running_mask &= ~(1ULL << claim->node_id);
	runtime->state = claim->prior_state;
	runtime->flags = claim->prior_flags;
	runtime->attempt_id = claim->prior_attempt_id;
	runtime->cache_index = claim->prior_cache_index;
	runtime->status = claim->prior_status;
	runtime->decision_reason = claim->prior_decision_reason;
	runtime->request_id = claim->prior_request_id;
	runtime->sequence = claim->prior_sequence;
	memmove(runtime->request_digest, claim->prior_request_digest,
		sizeof(runtime->request_digest));
	claim->active = 0;
	intr_restore(enabled);
}

void
agent_execution_contract_release(struct agent_execution_claim *claim)
{
	struct agent_execution_contract_record *record;
	int enabled;

	if (claim == 0 || !claim->active || !claim->legacy || claim->slot < 0 ||
	    claim->slot >= WORKFLOW_LIFECYCLE_CAP)
		return;
	enabled = intr_save();
	if (claim->delegated_active) {
		agent_task_bridge_effect_unpin_locked(
			claim->delegated_slot, claim->delegated_generation);
		claim->delegated_active = 0;
		claim->active = 0;
		intr_restore(enabled);
		return;
	}
	record = &agent_execution_contracts[claim->slot];
	if (!agent_execution_record_matches(record, claim->lifecycle) ||
	    record->bare_inflight == 0)
		panic("execution contract legacy gate");
	record->bare_inflight--;
	claim->active = 0;
	intr_restore(enabled);
}

static int
agent_execution_controller_authorized(
	struct proc *p, struct workflow_lifecycle_key lifecycle)
{
	return p != 0 && p->is_agent &&
	       workflow_lifecycle_key_equal(vfs_proc_lifecycle(p), lifecycle) &&
	       agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE) &&
	       p->agent_control_id != 0 &&
	       workflow_lifecycle_controller_matches(
		       lifecycle, p->vfs_scope_id, p->agent_control_id);
}

static void
agent_execution_contract_hash_node(
	struct agent_sha256_ctx *ctx,
	const struct agent_execution_contract_node *node)
{
	agent_execution_hash_u64(ctx, node->node_id);
	agent_execution_hash_u64(ctx, (uint)node->tool_id);
	agent_execution_hash_u64(ctx, node->predecessor_mask);
	agent_execution_hash_u64(ctx, node->required_capabilities);
	agent_execution_hash_u64(ctx, node->accepted_input_labels);
	agent_execution_hash_u64(ctx, node->output_add_labels);
	agent_execution_hash_u64(ctx, node->side_effect_mask);
	agent_execution_hash_bytes(ctx, node->schema_digest,
				   sizeof(node->schema_digest));
	agent_execution_hash_u64(ctx, node->deadline_tick);
	agent_execution_hash_u64(ctx, node->input_artifact_type);
	agent_execution_hash_u64(ctx, node->output_artifact_type);
	agent_execution_hash_u64(ctx, node->max_attempts);
	agent_execution_hash_u64(ctx, node->retry_policy);
	agent_execution_hash_u64(ctx, node->cancel_policy);
	agent_execution_hash_u64(ctx, node->charge_class);
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		agent_execution_hash_u64(ctx, node->exec_envelope[kind]);
		agent_execution_hash_u64(ctx, node->storage_envelope[kind]);
	}
}

static int
agent_execution_contract_build_node(
	struct proc *p, struct agent_execution_contract_record *record,
	uint index, struct agent_execution_contract_node *node,
	struct agent_sha256_ctx *fingerprint)
{
	struct agent_execution_node_internal *target = &record->nodes[index];
	struct agent_tool_manifest manifest;
	uint64 lower_mask = index == 0 ? 0 : (1ULL << index) - 1;
	uint expected_class = p->resource_slot_reserved ?
		RESOURCE_CHARGE_RESERVED : RESOURCE_CHARGE_ORDINARY;
	uint envelope_nonzero = 0;
	uint delegated_task_node;

	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		envelope_nonzero |= node->exec_envelope[kind] != 0 ||
			node->storage_envelope[kind] != 0;
	delegated_task_node =
		node->tool_id == AGENT_TOOL_DELEGATE_TASK &&
		node->input_artifact_type == AGENT_ARTIFACT_TASK &&
		node->output_artifact_type == AGENT_ARTIFACT_NONE;

	if (node->version != AGENT_EXECUTION_CONTRACT_NODE_VERSION ||
	    node->size != sizeof(*node) || node->node_id != index ||
	    node->tool_id <= 0 || node->flags != 0 || node->reserved != 0 ||
	    node->reserved_tail != 0 ||
	    (node->predecessor_mask & ~lower_mask) != 0 ||
	    node->input_artifact_type >= AGENT_ARTIFACT_TYPE_COUNT ||
	    node->output_artifact_type >= AGENT_ARTIFACT_TYPE_COUNT ||
	    node->max_attempts == 0 ||
	    node->max_attempts > AGENT_EXECUTION_NODE_MAX_ATTEMPTS ||
	    record->total_attempts + node->max_attempts >
		    AGENT_EXECUTION_CONTRACT_MAX_ATTEMPTS ||
	    (!envelope_nonzero && !delegated_task_node) ||
	    (node->retry_policy & ~AGENT_EXECUTION_RETRY_ALL) != 0 ||
	    (node->max_attempts == 1 && node->retry_policy != 0) ||
	    node->cancel_policy > AGENT_EXECUTION_CANCEL_ALLOW ||
	    node->charge_class != expected_class ||
	    (node->required_capabilities &
	     ~AGENT_EXECUTION_KNOWN_CAPABILITIES) != 0 ||
	    (record->deadline_tick != 0 && node->deadline_tick != 0 &&
	     node->deadline_tick > record->deadline_tick) ||
	    agent_tool_protocol_manifest_query(node->tool_id, &manifest) !=
		    AGENT_STATUS_OK ||
	    manifest.flags == AGENT_TOOL_F_DEPRECATED ||
	    ((manifest.flags & AGENT_TOOL_F_CALLABLE) == 0 &&
	     !(delegated_task_node &&
	       manifest.flags == AGENT_TOOL_F_SYSCALL_ONLY &&
	       node->input_artifact_type == AGENT_ARTIFACT_TASK) &&
	     !(manifest.flags == AGENT_TOOL_F_BROKERED &&
	       node->input_artifact_type != AGENT_ARTIFACT_NONE &&
	       (node->output_artifact_type != AGENT_ARTIFACT_NONE ||
	        node->input_artifact_type == AGENT_ARTIFACT_TASK))) ||
	    (node->required_capabilities &
	     manifest.provenance.required_capabilities) !=
		    manifest.provenance.required_capabilities ||
	    (node->accepted_input_labels &
	     ~manifest.provenance.accepted_input_labels) != 0 ||
	    (node->accepted_input_labels &
	     (AGENT_PROVENANCE_TRUSTED_USER_CONTROL |
	      AGENT_PROVENANCE_AGENT_DERIVED)) !=
		    (AGENT_PROVENANCE_TRUSTED_USER_CONTROL |
		     AGENT_PROVENANCE_AGENT_DERIVED) ||
	    node->output_add_labels !=
		    manifest.provenance.output_add_labels ||
	    node->side_effect_mask != manifest.provenance.side_effect_mask)
		return AGENT_STATUS_BAD_PARAM;
	if (agent_execution_digest_zero(node->schema_digest))
		memmove(node->schema_digest, manifest.schema_digest,
			sizeof(node->schema_digest));
	else if (!agent_execution_digest_equal(node->schema_digest,
					       manifest.schema_digest))
		return AGENT_STATUS_STALE;
	memset(target, 0, sizeof(*target));
	target->predecessor_mask = node->predecessor_mask;
	target->required_capabilities = node->required_capabilities;
	target->deadline_tick = node->deadline_tick;
	memmove(agent_execution_schema_digests
			[record - agent_execution_contracts][index],
		node->schema_digest, sizeof(node->schema_digest));
	target->tool_id = node->tool_id;
	target->accepted_input_labels = node->accepted_input_labels;
	target->output_add_labels = node->output_add_labels;
	target->side_effect_mask = node->side_effect_mask;
	target->input_artifact_type = node->input_artifact_type;
	target->output_artifact_type = node->output_artifact_type;
	target->max_attempts = node->max_attempts;
	record->total_attempts += node->max_attempts;
	target->retry_policy = node->retry_policy;
	target->cancel_policy = node->cancel_policy;
	target->charge_class = node->charge_class;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		target->exec_envelope[kind] = node->exec_envelope[kind];
		target->storage_envelope[kind] = node->storage_envelope[kind];
	}
	record->runtime[index].state = node->predecessor_mask == 0 ?
		AGENT_EXECUTION_NODE_READY : AGENT_EXECUTION_NODE_BLOCKED;
	record->runtime[index].cache_index = AGENT_EXECUTION_CACHE_NONE;
	agent_execution_contract_hash_node(fingerprint, node);
	return AGENT_STATUS_OK;
}

static void
agent_execution_contract_result_fill(
	const struct agent_execution_contract_record *record,
	const struct agent_execution_contract_control *control,
	struct agent_execution_contract_result *result, int status)
{
	memset(result, 0, sizeof(*result));
	result->version = AGENT_EXECUTION_CONTRACT_VERSION;
	result->size = sizeof(*result);
	result->status = status;
	result->request_id = control->request_id;
	if (record == 0)
		return;
	result->state = record->state == AGENT_EXECUTION_STATE_BUILDING ?
		AGENT_EXECUTION_CONTRACT_EMPTY : record->state;
	result->key.lifecycle =
		agent_execution_export_lifecycle(record->lifecycle);
	result->key.generation = record->generation;
	memmove(result->contract_fingerprint, record->fingerprint,
		sizeof(result->contract_fingerprint));
	result->deadline_tick = record->deadline_tick;
	result->created_tick = record->created_tick;
	result->completed_mask = record->completed_mask;
	result->failed_mask = record->failed_mask;
	result->running_mask = record->running_mask;
	result->node_count = record->node_count;
	result->flags = record->flags;
	result->denial_count = record->denial_count;
	result->replay_count = record->replay_count;
	if (control->operation == AGENT_EXECUTION_CONTRACT_CREATE &&
	    record->create_request_id == control->request_id) {
		/* CREATE is an idempotent receipt, not a moving QUERY snapshot. */
		result->completed_mask = 0;
		result->failed_mask = 0;
		result->running_mask = 0;
		result->denial_count = 0;
		result->replay_count = 0;
	}
}

static void
agent_execution_contract_node_export(
	const struct agent_execution_contract_record *record, uint index,
	struct agent_execution_contract_node *node)
{
	const struct agent_execution_node_internal *source =
		&record->nodes[index];
	memset(node, 0, sizeof(*node));
	node->version = AGENT_EXECUTION_CONTRACT_NODE_VERSION;
	node->size = sizeof(*node);
	node->node_id = index;
	node->tool_id = source->tool_id;
	node->predecessor_mask = source->predecessor_mask;
	node->required_capabilities = source->required_capabilities;
	node->accepted_input_labels = source->accepted_input_labels;
	node->output_add_labels = source->output_add_labels;
	node->side_effect_mask = source->side_effect_mask;
	memmove(node->schema_digest,
		agent_execution_schema_digests
			[record - agent_execution_contracts][index],
		sizeof(node->schema_digest));
	node->deadline_tick = source->deadline_tick;
	node->input_artifact_type = source->input_artifact_type;
	node->output_artifact_type = source->output_artifact_type;
	node->max_attempts = source->max_attempts;
	node->retry_policy = source->retry_policy;
	node->cancel_policy = source->cancel_policy;
	node->charge_class = source->charge_class;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		node->exec_envelope[kind] = source->exec_envelope[kind];
		node->storage_envelope[kind] = source->storage_envelope[kind];
	}
}

static int
agent_execution_contract_create_replay(
	struct proc *p, struct agent_execution_contract_record *record,
	const struct agent_execution_contract_control *control)
{
	static const char domain[] = "agentos.execution.contract.v1";
	struct agent_sha256_ctx fingerprint;
	uchar computed[AGENT_EXECUTION_DIGEST_SIZE];
	struct workflow_lifecycle_key lifecycle =
		agent_execution_abi_lifecycle(control->key.lifecycle);
	int enabled;

	if (record->state != AGENT_EXECUTION_CONTRACT_FROZEN ||
	    record->create_request_id != control->request_id ||
	    record->flags != control->flags ||
	    record->node_count != control->node_count ||
	    record->deadline_tick != control->deadline_tick ||
	    control->node_size != sizeof(struct agent_execution_contract_node) ||
	    (!agent_execution_digest_zero(control->contract_fingerprint) &&
	     !agent_execution_digest_equal(control->contract_fingerprint,
					 record->fingerprint)))
		return AGENT_STATUS_CONFLICT;
	agent_sha256_init(&fingerprint);
	agent_execution_hash_bytes(&fingerprint, domain, sizeof(domain));
	agent_execution_hash_u64(&fingerprint, lifecycle.id);
	agent_execution_hash_u64(&fingerprint, lifecycle.generation);
	agent_execution_hash_u64(&fingerprint, control->flags);
	agent_execution_hash_u64(&fingerprint, control->node_count);
	agent_execution_hash_u64(&fingerprint, control->deadline_tick);
	for (uint i = 0; i < control->node_count; i++) {
		struct agent_execution_contract_node node;

		if (copyin(p->pagetable, (char *)&node,
			   control->nodes + (uint64)i * control->node_size,
			   sizeof(node)) < 0)
			return -1;
		if (node.version != AGENT_EXECUTION_CONTRACT_NODE_VERSION ||
		    node.size != sizeof(node) || node.node_id != i ||
		    node.flags != 0 || node.reserved != 0 ||
		    node.reserved_tail != 0)
			return AGENT_STATUS_CONFLICT;
		if (agent_execution_digest_zero(node.schema_digest))
			memmove(node.schema_digest,
				agent_execution_schema_digests
					[record - agent_execution_contracts][i],
				sizeof(node.schema_digest));
		else if (!agent_execution_digest_equal(
				 node.schema_digest,
				 agent_execution_schema_digests
					 [record - agent_execution_contracts][i]))
			return AGENT_STATUS_CONFLICT;
		agent_execution_contract_hash_node(&fingerprint, &node);
	}
	agent_sha256_final(&fingerprint, computed);
	if (!agent_execution_digest_equal(computed, record->fingerprint))
		return AGENT_STATUS_CONFLICT;
	enabled = intr_save();
	if (!agent_execution_record_matches(record, lifecycle) ||
	    record->state != AGENT_EXECUTION_CONTRACT_FROZEN ||
	    record->create_request_id != control->request_id ||
	    !agent_execution_digest_equal(computed, record->fingerprint)) {
		intr_restore(enabled);
		return AGENT_STATUS_RETRY;
	}
	intr_restore(enabled);
	return AGENT_STATUS_OK;
}

int
sys_agent_execution_contract(uint64 controladdr, uint64 resultaddr)
{
	struct proc *p = curr_proc();
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;
	struct agent_execution_contract_record *record = 0;
	struct workflow_lifecycle_key lifecycle;
	struct agent_sha256_ctx fingerprint;
	static const char domain[] = "agentos.execution.contract.v1";
	uchar computed[AGENT_EXECUTION_DIGEST_SIZE];
	uint64 now = get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
	int slot;
	int status = AGENT_STATUS_OK;
	int enabled;
	int query_pinned = 0;

	if (copyin(p->pagetable, (char *)&control, controladdr,
		   sizeof(control)) < 0 ||
	    user_range_check(p->pagetable, resultaddr, sizeof(result), PTE_W) < 0)
		return -1;
	memset(&result, 0, sizeof(result));
	result.version = AGENT_EXECUTION_CONTRACT_VERSION;
	result.size = sizeof(result);
	result.request_id = control.request_id;
	if (control.version != AGENT_EXECUTION_CONTRACT_VERSION ||
	    control.size != sizeof(control) || control.request_id == 0 ||
	    control.key.lifecycle.reserved != 0 || control.reserved[0] != 0 ||
	    control.reserved[1] != 0) {
		result.status = AGENT_STATUS_BAD_PARAM;
		goto copy_result;
	}
	lifecycle = agent_execution_abi_lifecycle(control.key.lifecycle);
	slot = agent_execution_slot(lifecycle);
	if (slot < 0 || !agent_execution_controller_authorized(p, lifecycle)) {
		result.status = slot < 0 ? AGENT_STATUS_BAD_PARAM :
			AGENT_STATUS_DENIED;
		goto copy_result;
	}

	if (control.operation == AGENT_EXECUTION_CONTRACT_CREATE) {
		if (control.flags != AGENT_EXECUTION_CONTRACT_F_ENFORCE ||
		    control.key.generation != 0 || control.node_count == 0 ||
		    control.node_count > AGENT_EXECUTION_CONTRACT_MAX_NODES ||
		    control.node_size != sizeof(struct agent_execution_contract_node) ||
		    control.nodes == 0 ||
		    (control.deadline_tick != 0 && control.deadline_tick <= now) ||
		    user_range_check(
			    p->pagetable, control.nodes,
			    (uint64)control.node_count * control.node_size,
			    PTE_R) < 0) {
			result.status = AGENT_STATUS_BAD_PARAM;
			goto copy_result;
		}
		enabled = intr_save();
		record = &agent_execution_contracts[slot];
		if (!agent_execution_record_matches(record, lifecycle)) {
			if (record->bare_inflight != 0 ||
			    record->running_count != 0) {
				status = AGENT_STATUS_RETRY;
				intr_restore(enabled);
				goto fill_result;
			}
			memset(record, 0, sizeof(*record));
			record->lifecycle = lifecycle;
		}
		if (record->bare_inflight != 0 || record->running_count != 0) {
			status = AGENT_STATUS_RETRY;
			intr_restore(enabled);
			goto fill_result;
		}
		/* A quiescent RECLAIMED record is an exact RETIRE receipt until the
		 * controller deliberately starts the next contract generation. */
		if (record->state == AGENT_EXECUTION_CONTRACT_RECLAIMED) {
			memset(record, 0, sizeof(*record));
			record->lifecycle = lifecycle;
		}
		if (record->state != AGENT_EXECUTION_CONTRACT_EMPTY) {
			int replay = record->state ==
					AGENT_EXECUTION_CONTRACT_FROZEN &&
				record->create_request_id == control.request_id;

			intr_restore(enabled);
			status = replay ? agent_execution_contract_create_replay(
						  p, record, &control) :
					  AGENT_STATUS_CONFLICT;
			if (status == -1)
				return -1;
			goto fill_result;
		}
		record->state = AGENT_EXECUTION_STATE_BUILDING;
		memset(agent_execution_completion_caches[slot], 0,
		       sizeof(agent_execution_completion_caches[slot]));
		memset(agent_execution_schema_digests[slot], 0,
		       sizeof(agent_execution_schema_digests[slot]));
		memset(agent_execution_producers[slot], 0,
		       sizeof(agent_execution_producers[slot]));
		record->flags = control.flags;
		record->node_count = control.node_count;
		record->total_attempts = 0;
		record->deadline_tick = control.deadline_tick;
		intr_restore(enabled);

		agent_sha256_init(&fingerprint);
		agent_execution_hash_bytes(&fingerprint, domain, sizeof(domain));
		agent_execution_hash_u64(&fingerprint, lifecycle.id);
		agent_execution_hash_u64(&fingerprint, lifecycle.generation);
		agent_execution_hash_u64(&fingerprint, control.flags);
		agent_execution_hash_u64(&fingerprint, control.node_count);
		agent_execution_hash_u64(&fingerprint, control.deadline_tick);
		for (uint i = 0; i < control.node_count; i++) {
			struct agent_execution_contract_node node;

			if (copyin(p->pagetable, (char *)&node,
				   control.nodes + (uint64)i * control.node_size,
				   sizeof(node)) < 0) {
				status = -1;
				goto build_failed;
			}
			status = agent_execution_contract_build_node(
				p, record, i, &node, &fingerprint);
			if (status != AGENT_STATUS_OK)
				goto build_failed;
		}
		agent_sha256_final(&fingerprint, computed);
		if (!agent_execution_digest_zero(control.contract_fingerprint) &&
		    !agent_execution_digest_equal(control.contract_fingerprint,
					  computed)) {
			status = AGENT_STATUS_CONFLICT;
			goto build_failed;
		}
		if (agent_evidence_prepare_direct_denials(p, lifecycle) < 0) {
			status = AGENT_STATUS_RETRY;
			goto build_failed;
		}
		enabled = intr_save();
		if (!workflow_lifecycle_active(lifecycle) ||
		    !agent_execution_controller_authorized(p, lifecycle)) {
			status = AGENT_STATUS_DENIED;
			intr_restore(enabled);
			goto build_failed;
		}
		if (!agent_execution_record_matches(record, lifecycle) ||
		    record->state != AGENT_EXECUTION_STATE_BUILDING ||
		    record->bare_inflight != 0 || record->running_count != 0 ||
		    agent_execution_contract_generations[slot] == ~0ULL) {
			status = AGENT_STATUS_RETRY;
			intr_restore(enabled);
			goto build_failed;
		}
		record->generation =
			++agent_execution_contract_generations[slot];
		if (record->generation == 0)
			panic("execution contract generation");
		memmove(record->fingerprint, computed,
			sizeof(record->fingerprint));
		record->create_request_id = control.request_id;
		record->created_tick = now;
		record->state = AGENT_EXECUTION_CONTRACT_FROZEN;
		intr_restore(enabled);
		status = AGENT_STATUS_OK;
		goto fill_result;

build_failed:
		enabled = intr_save();
		if (agent_execution_record_matches(record, lifecycle) &&
		    record->state == AGENT_EXECUTION_STATE_BUILDING &&
		    record->bare_inflight == 0 && record->running_count == 0) {
			memset(record, 0, sizeof(*record));
			record->lifecycle = lifecycle;
		}
		intr_restore(enabled);
		if (status == -1)
			return -1;
		goto fill_result;
	}

	if ((control.operation != AGENT_EXECUTION_CONTRACT_QUERY &&
	     control.operation != AGENT_EXECUTION_CONTRACT_RETIRE) ||
	    control.flags != 0 || control.key.generation == 0 ||
	    control.deadline_tick != 0 ||
	    !agent_execution_digest_zero(control.contract_fingerprint)) {
		result.status = AGENT_STATUS_BAD_PARAM;
		goto copy_result;
	}
	enabled = intr_save();
	record = &agent_execution_contracts[slot];
	if (!agent_execution_record_matches(record, lifecycle) ||
	    record->state == AGENT_EXECUTION_CONTRACT_EMPTY ||
	    record->state == AGENT_EXECUTION_STATE_BUILDING)
		status = AGENT_STATUS_NOT_FOUND;
	else if (record->generation != control.key.generation)
		status = AGENT_STATUS_STALE;
	else if (control.operation == AGENT_EXECUTION_CONTRACT_RETIRE) {
		if (control.nodes != 0 || control.node_count != 0 ||
		    control.node_size != 0)
			status = AGENT_STATUS_BAD_PARAM;
		else if (record->state == AGENT_EXECUTION_CONTRACT_RECLAIMED)
			status = AGENT_STATUS_OK;
		else if (!agent_execution_record_enforced(record, lifecycle))
			status = AGENT_STATUS_NOT_FOUND;
		else {
			/* RETIRING closes admission before observing the last direct or
			 * admitted execution reference.  The same request can poll until
			 * both counters drain, then receives the durable RECLAIMED receipt. */
			record->state = AGENT_EXECUTION_CONTRACT_RETIRING;
			if (record->bare_inflight != 0 ||
			    record->running_count != 0)
				status = AGENT_STATUS_RETRY;
			else
				record->state =
					AGENT_EXECUTION_CONTRACT_RECLAIMED;
		}
	} else if (record->state == AGENT_EXECUTION_CONTRACT_RECLAIMED) {
		/* QUERY preserves the exact terminal snapshot until CREATE
		 * intentionally recycles this lifecycle slot. */
		status = AGENT_STATUS_OK;
	} else if ((control.nodes == 0 &&
		    (control.node_count != 0 || control.node_size != 0)) ||
		   (control.nodes != 0 &&
		    (control.node_size !=
			    sizeof(struct agent_execution_contract_node) ||
		     control.node_count < record->node_count)))
		status = AGENT_STATUS_BAD_SIZE;
	if (status == AGENT_STATUS_OK &&
	    control.operation == AGENT_EXECUTION_CONTRACT_QUERY &&
	    control.nodes != 0) {
		if (record->bare_inflight == (uint)-1)
			status = AGENT_STATUS_RETRY;
		else {
			record->bare_inflight++;
			query_pinned = 1;
		}
	}
	agent_execution_contract_result_fill(record, &control, &result, status);
	intr_restore(enabled);
	if (status == AGENT_STATUS_OK &&
	    control.operation == AGENT_EXECUTION_CONTRACT_QUERY &&
	    control.nodes != 0) {
		if (user_range_check(
			    p->pagetable, control.nodes,
			    (uint64)record->node_count * control.node_size,
			    PTE_W) < 0)
			status = -1;
		for (uint i = 0; i < record->node_count; i++) {
			struct agent_execution_contract_node node;

			if (status == -1)
				break;
			agent_execution_contract_node_export(record, i, &node);
			if (copyout(p->pagetable,
				    control.nodes + (uint64)i * control.node_size,
				    (char *)&node, sizeof(node)) < 0)
				status = -1;
		}
	}
	if (query_pinned) {
		enabled = intr_save();
		if (!agent_execution_record_matches(record, lifecycle) ||
		    record->generation != control.key.generation ||
		    record->bare_inflight == 0)
			panic("execution contract query pin");
		record->bare_inflight--;
		intr_restore(enabled);
	}
	if (status == -1)
		return -1;
	goto copy_result;

fill_result:
	enabled = intr_save();
	agent_execution_contract_result_fill(record, &control, &result, status);
	intr_restore(enabled);
copy_result:
	return copyout(p->pagetable, resultaddr, (char *)&result,
		       sizeof(result));
}
