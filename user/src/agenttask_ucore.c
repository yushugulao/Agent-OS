#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TEST_NAME "agenttask_ucore"
#define TASK_NODE_COUNT 23U
#define TASK_RESOURCE_BORROW_NODE 21U
#define TASK_RESOURCE_OWNED_NODE  22U
#define CHARGE_RESERVED 1U
#define PERF_OPERATION_COUNT 16U
#define PERF_REQUEST_BASE 91000ULL
#define ECHO_PARAM_COUNT 3U
#define TOOL_V3_DISPATCH_HEADER_BYTES (2U * sizeof(unsigned int))
#define TASK_RESOURCE_FIRST_PATH  "taskr1"
#define TASK_RESOURCE_SECOND_PATH "taskr2"
#define TASK_RESOURCE_NUL_PATH    "tasknul"
#define TASK_RESOURCE_UTF8_PATH   "taskbad"
#define TASK_RESOURCE_RACE_PATH   "taskrace"
#define TASK_DELEGATE_NORMAL_PATH "taskd1"
#define TASK_DELEGATE_CANCEL_PATH "taskdc"
#define TASK_DELEGATE_DEADLINE_PATH "taskd2"
#define TASK_DELEGATE_LEASE_PATH "tasklease"
#define TASK_DELEGATE_RECLAIM_PATH "taskgap"
#define TASK_DELEGATE_LEASE_HEADER "lease-v1"
#define TASK_DELEGATE_LEASE_PAYLOAD "delegated-lease"
#define TASK_DELEGATE_RECLAIM_PAYLOAD "issuer-reclaimed"
#define TASK_DELEGATE_DEADLINE_SLACK 128ULL
#define TASK_DELEGATE_PROVIDER_ROLE AGENT_ROLE_ARTIFACT
#define TASK_DELEGATE_CONTROLLER_ROLE AGENT_ROLE_ORCHESTRATOR
#define TASK_DELEGATE_NORMAL_TASK_ID 610001ULL
#define TASK_DELEGATE_CANCEL_TASK_ID 610002ULL
#define TASK_DELEGATE_DEADLINE_TASK_ID 610003ULL
#define TASK_DELEGATE_NORMAL_CORRELATION_ID 611001ULL
#define TASK_DELEGATE_CANCEL_CORRELATION_ID 611002ULL
#define TASK_DELEGATE_DEADLINE_CORRELATION_ID 611003ULL
#define TASK_DELEGATE_CONTROLLER_CANCEL_DELAY 32ULL
#define TASK_DELEGATE_PROVIDER_CANCEL_DELAY 64ULL
#define TASK_DELEGATE_SIDE_EFFECTS \
	(AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA | \
	 AGENT_SIDE_EFFECT_IPC | AGENT_SIDE_EFFECT_PROCESS | \
	 AGENT_SIDE_EFFECT_PERMISSION | AGENT_SIDE_EFFECT_ARTIFACT)
#define TASK_DELEGATE_LEASE_EFFECTS \
	(AGENT_SIDE_EFFECT_PROCESS | AGENT_SIDE_EFFECT_METADATA | \
	 AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_ARTIFACT)

static const unsigned char task_resource_embedded_nul[] = { 'a', 0, 'b' };
static const unsigned char task_resource_invalid_utf8[] = { 0xc0, 0x80 };
static const char task_resource_first_payload[] = "task-resource-borrow";
static const char task_resource_second_payload[] = "task-resource-owned";

#define SEMANTIC_STATUS_OK       (1U << 0)
#define SEMANTIC_TOOL_ECHO       (1U << 1)
#define SEMANTIC_CONTEXT_PROOF   (1U << 2)
#define SEMANTIC_EVIDENCE_PROOF  (1U << 3)
#define SEMANTIC_ZERO_PAYLOAD    (1U << 4)
#define SEMANTIC_EXPECTED        (SEMANTIC_STATUS_OK | \
				  SEMANTIC_TOOL_ECHO | \
				  SEMANTIC_CONTEXT_PROOF | \
				  SEMANTIC_EVIDENCE_PROOF | \
				  SEMANTIC_ZERO_PAYLOAD)

struct task_sq_page_view {
	struct agent_task_ring_header header;
	struct agent_task_sqe entries[AGENT_TASK_CHANNEL_CAPACITY];
};

struct task_cq_page_view {
	struct agent_task_ring_header header;
	struct agent_task_cqe entries[AGENT_TASK_CHANNEL_CAPACITY];
};

struct task_channel_view {
	volatile struct task_sq_page_view *sq;
	volatile const struct task_cq_page_view *cq;
	uint64 generation;
	uint64 sq_tail;
	uint64 cq_head;
};

struct task_resource_inputs {
	int first_fd;
	int second_fd;
	int embedded_nul_fd;
	int invalid_utf8_fd;
	int write_only_fd;
	int pipe_read_fd;
	int pipe_write_fd;
	int race_fd;
};

struct task_resource_close_race {
	volatile int start;
	volatile int close_status;
	int fd;
};

struct task_delegate_hello {
	int pid;
	int agent_id;
	int role;
	uint64 capability_mask;
	struct agent_workflow_lifecycle_key lifecycle;
};

struct task_delegate_lease_helper {
	int context_status;
	int publish_status;
	int open_status;
	int read_status;
	int eof_status;
	int close_status;
	int unlink_status;
	int done;
};

struct task_delegate_controller_command {
	struct agent_task_delegate_complete cancel;
};

struct ablation_metrics {
	uint64 start_tick;
	uint64 last_service_start_tick;
	uint64 service_start_span_ticks;
	uint64 sequence_elapsed_ticks;
	uint64 start_dispatch_count;
	uint64 dispatch_delta;
	uint64 service_start_tick_intervals[PERF_OPERATION_COUNT];
};

static struct agent_execution_contract_node task_nodes[TASK_NODE_COUNT];
static struct agent_execution_contract_node queried_nodes[TASK_NODE_COUNT];
static struct agent_op batch_ops[PERF_OPERATION_COUNT];
static struct agent_result batch_results[PERF_OPERATION_COUNT];
static struct agent_request_v3 scalar_requests[PERF_OPERATION_COUNT];
static struct agent_response_v3 scalar_responses[PERF_OPERATION_COUNT];
static struct agent_param_v2
	scalar_params[PERF_OPERATION_COUNT][ECHO_PARAM_COUNT];
static struct agent_task_sqe task_perf_sqes[PERF_OPERATION_COUNT];
static struct agent_task_cqe task_perf_cqes[PERF_OPERATION_COUNT];
static uint64 next_request_id = 100000;

_Static_assert(PERF_OPERATION_COUNT == AGENT_TASK_CHANNEL_CAPACITY,
	       "performance sequence must exactly fill one CQ generation");
_Static_assert(sizeof(struct agent_op) == 104,
	       "legacy batch operation ABI size");
_Static_assert(sizeof(struct agent_result) == 120,
	       "legacy batch result ABI size");
_Static_assert(AGENT_TOOL_DELEGATE_TASK == 26,
	       "delegated Task tool number");
_Static_assert(AGENT_TASK_DELEGATE_CLAIM_SYSCALL == 567U &&
	       AGENT_TASK_DELEGATE_COMPLETE_SYSCALL == 568U,
	       "delegated Task syscall numbers");
_Static_assert(sizeof(struct agent_task_delegate_descriptor) == 128,
	       "delegated Task descriptor ABI size");
_Static_assert(sizeof(struct agent_task_delegate_claim_result) == 200,
	       "delegated Task claim result ABI size");
_Static_assert(sizeof(struct agent_task_delegate_complete) == 96,
	       "delegated Task completion request ABI size");
_Static_assert(sizeof(struct agent_task_delegate_complete_result) == 64,
	       "delegated Task completion result ABI size");
_Static_assert(AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL == (1U << 1) &&
	       AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL !=
		       AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL,
	       "delegated controller cancellation flag");
_Static_assert(AGENT_TASK_DELEGATE_COMPLETE_F_QUERY_TERMINAL == (1U << 2) &&
	       AGENT_TASK_DELEGATE_COMPLETE_F_QUERY_TERMINAL !=
		       AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL,
	       "delegated provider terminal query flag");
_Static_assert(sizeof(struct task_delegate_controller_command) == 96,
	       "controller receives one exact cancellation capability binding");
_Static_assert((TASK_DELEGATE_SIDE_EFFECTS & TASK_DELEGATE_LEASE_EFFECTS) ==
	       TASK_DELEGATE_LEASE_EFFECTS,
	       "delegated lease covers helper process/metadata/file/artifact effects");
_Static_assert(TASK_DELEGATE_PROVIDER_ROLE == AGENT_ROLE_ARTIFACT &&
	       AGENT_CAP_ARTIFACT_WRITE != AGENT_CAP_TASK_ACCEPT,
	       "delegated provider is the artifact specialist");
_Static_assert(TASK_DELEGATE_CONTROLLER_ROLE == AGENT_ROLE_ORCHESTRATOR,
	       "delegated controller is a separate orchestrator");
_Static_assert(sizeof(TASK_DELEGATE_LEASE_HEADER) - 1U +
	       sizeof(TASK_DELEGATE_LEASE_PAYLOAD) - 1U <= 32U,
	       "delegated lease artifact remains bounded");
_Static_assert(sizeof(TASK_DELEGATE_RECLAIM_PAYLOAD) - 1U <= 32U,
	       "reclaimed issuer file effect remains bounded");

/* SHA-256 of the frozen inline-input domain and an empty ECHO operation. */
static const unsigned char empty_echo_fingerprint[32] = {
	0x29, 0xa8, 0xc2, 0x2d, 0xa8, 0x60, 0x5e, 0x54,
	0xac, 0x35, 0x2d, 0x39, 0x41, 0xa8, 0xb6, 0x21,
	0x05, 0x90, 0xee, 0x3c, 0x20, 0x8c, 0xa5, 0x11,
	0x5e, 0xfe, 0x9d, 0x74, 0x90, 0xeb, 0x47, 0xce,
};

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agenttask_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static int handle_is_null(struct agent_task_resource_handle handle)
{
	return handle.slot == 0 && handle.type == 0 && handle.flags == 0 &&
	       handle.generation == 0;
}

static int bytes_equal(const void *left, const void *right, uint size)
{
	const unsigned char *a = left;
	const unsigned char *b = right;

	for (uint i = 0; i < size; i++)
		if (a[i] != b[i])
			return 0;
	return 1;
}

static void volatile_write(void *destination, const void *source, uint size)
{
	volatile unsigned char *out = destination;
	const unsigned char *in = source;

	for (uint i = 0; i < size; i++)
		out[i] = in[i];
	__sync_synchronize();
}

static void volatile_read(void *destination, const void *source, uint size)
{
	unsigned char *out = destination;
	const volatile unsigned char *in = source;

	__sync_synchronize();
	for (uint i = 0; i < size; i++)
		out[i] = in[i];
	__sync_synchronize();
}

static void ablation_begin(struct ablation_metrics *metrics)
{
	struct agent_info info;

	memset(metrics, 0, sizeof(*metrics));
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0,
	      "sample performance sequence start boundary tick");
	metrics->start_tick = info.current_tick;
	metrics->last_service_start_tick = info.current_tick;
	metrics->start_dispatch_count = info.sched_dispatch_count;
}

static void ablation_end(struct ablation_metrics *metrics)
{
	struct agent_info info;

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 &&
	      info.current_tick >= metrics->start_tick &&
	      info.sched_dispatch_count >= metrics->start_dispatch_count,
	      "sample monotonic performance sequence end counters");
	metrics->sequence_elapsed_ticks = info.current_tick - metrics->start_tick;
	metrics->dispatch_delta =
		info.sched_dispatch_count - metrics->start_dispatch_count;
}

static void ablation_record_service_start(struct ablation_metrics *metrics,
					  uint index,
					  uint64 service_start_tick)
{
	check(index < PERF_OPERATION_COUNT &&
	      service_start_tick >= metrics->last_service_start_tick,
	      "record monotonic pre-effect Context service-start tick");
	metrics->service_start_tick_intervals[index] =
		service_start_tick - metrics->last_service_start_tick;
	metrics->last_service_start_tick = service_start_tick;
	metrics->service_start_span_ticks =
		service_start_tick - metrics->start_tick;
}

static uint64 nearest_rank(const uint64 *samples, uint count,
			   uint numerator, uint denominator)
{
	uint64 sorted[PERF_OPERATION_COUNT];
	uint rank;

	check(count > 0 && count <= PERF_OPERATION_COUNT && numerator > 0 &&
	      numerator <= denominator && denominator != 0,
	      "nearest-rank quantile arguments");
	for (uint i = 0; i < count; i++)
		sorted[i] = samples[i];
	for (uint i = 1; i < count; i++) {
		uint64 value = sorted[i];
		uint j = i;

		while (j > 0 && sorted[j - 1] > value) {
			sorted[j] = sorted[j - 1];
			j--;
		}
		sorted[j] = value;
	}
	rank = (count * numerator + denominator - 1) / denominator;
	return sorted[rank - 1];
}

static void report_ablation(const char *path, uint syscalls,
			    uint abi_descriptor_bytes,
			    uint copied_descriptor_bytes,
			    uint dispatch_header_bytes,
			    uint control_abi_bytes,
			    uint control_copied_bytes,
			    const struct ablation_metrics *metrics)
{
	uint64 p50 = nearest_rank(metrics->service_start_tick_intervals,
				  PERF_OPERATION_COUNT, 50, 100);
	uint64 p99 = nearest_rank(metrics->service_start_tick_intervals,
				  PERF_OPERATION_COUNT, 99, 100);

	check(metrics->service_start_span_ticks <=
	      metrics->sequence_elapsed_ticks,
	      "service-start span fits inside sequence boundary elapsed time");
	printf("agenttask_ucore: perf path=%s operations=%u syscalls=%u "
	       "abi_descriptor_bytes=%u copied_descriptor_bytes=%u "
	       "dispatch_header_bytes=%u control_abi_bytes=%u "
	       "control_copied_bytes=%u "
	       "service_start_interval_tick_p50=%llu "
	       "service_start_interval_tick_p99=%llu "
	       "service_start_span_ticks=%llu sequence_elapsed_ticks=%llu "
	       "sched_dispatch_delta=%llu\n",
	       path, PERF_OPERATION_COUNT, syscalls, abi_descriptor_bytes,
	       copied_descriptor_bytes, dispatch_header_bytes,
	       control_abi_bytes, control_copied_bytes, p50, p99,
	       metrics->service_start_span_ticks,
	       metrics->sequence_elapsed_ticks, metrics->dispatch_delta);
}

static uint semantic_fingerprint(int status, int tool_id, uint64 request_id,
				 uint64 sequence, uint64 evidence_ticket,
				 int zero_payload, uint64 *service_tick)
{
	struct agent_context_record record;
	uint fingerprint = 0;
	int context_ok;

	memset(&record, 0, sizeof(record));
	context_ok = sequence != 0 &&
		context_query(sequence, &record, 1) == 1 &&
		record.sequence == sequence && record.request_id == request_id &&
		record.tool_id == tool_id && record.status == status &&
		record.record_hash != 0;
	if (status == AGENT_STATUS_OK)
		fingerprint |= SEMANTIC_STATUS_OK;
	if (tool_id == AGENT_TOOL_ECHO)
		fingerprint |= SEMANTIC_TOOL_ECHO;
	if (context_ok)
		fingerprint |= SEMANTIC_CONTEXT_PROOF;
	/* Legacy batch exposes the chained Context hash; bound paths add a ticket. */
	if ((context_ok && record.record_hash != 0) || evidence_ticket != 0)
		fingerprint |= SEMANTIC_EVIDENCE_PROOF;
	if (zero_payload)
		fingerprint |= SEMANTIC_ZERO_PAYLOAD;
	if (service_tick != 0)
		*service_tick = context_ok ? record.tick : 0;
	return fingerprint;
}

static void run_batch_ablation(void)
{
	struct ablation_metrics metrics;
	uint fingerprint = 0;

	memset(batch_ops, 0, sizeof(batch_ops));
	memset(batch_results, 0, sizeof(batch_results));
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		batch_ops[i].version = AGENT_OP_VERSION;
		batch_ops[i].tool_id = AGENT_TOOL_ECHO;
		batch_ops[i].request_id = PERF_REQUEST_BASE + i + 1;
	}
	ablation_begin(&metrics);
	check(agent_run(batch_ops, batch_results, PERF_OPERATION_COUNT, 0) ==
	      PERF_OPERATION_COUNT,
	      "legacy batch accepts the deterministic empty ECHO sequence");
	ablation_end(&metrics);
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		uint64 service_tick = 0;
		uint current;

		check(batch_results[i].request_id == batch_ops[i].request_id,
		      "legacy batch preserves request identity");
		current = semantic_fingerprint(
			batch_results[i].status, batch_results[i].tool_id,
			batch_results[i].request_id, batch_results[i].sequence,
			0, 1, &service_tick);
		check(current == SEMANTIC_EXPECTED,
		      "legacy batch has status/context/evidence semantics");
		if (i == 0)
			fingerprint = current;
		check(current == fingerprint,
		      "legacy batch repeats one semantic fingerprint");
		ablation_record_service_start(&metrics, i, service_tick);
	}
	report_ablation(
		"batch", 1,
		PERF_OPERATION_COUNT *
			(sizeof(struct agent_op) + sizeof(struct agent_result)),
		PERF_OPERATION_COUNT *
			(sizeof(struct agent_op) + sizeof(struct agent_result)),
		0, 0, 0, &metrics);
	printf("agenttask_ucore: perf_fp path=batch value=%u\n", fingerprint);
}

static void task_node_init(uint node_id)
{
	struct agent_execution_contract_node *node = &task_nodes[node_id];

	memset(node, 0, sizeof(*node));
	node->version = AGENT_EXECUTION_CONTRACT_NODE_VERSION;
	node->size = sizeof(*node);
	node->node_id = node_id;
	node->tool_id = AGENT_TOOL_ECHO;
	node->accepted_input_labels = AGENT_PROVENANCE_ALL;
	node->output_add_labels = AGENT_PROVENANCE_AGENT_DERIVED;
	node->input_artifact_type =
		node_id >= TASK_RESOURCE_BORROW_NODE ?
			AGENT_ARTIFACT_UTF8 : AGENT_ARTIFACT_NONE;
	node->output_artifact_type = AGENT_ARTIFACT_NONE;
	node->max_attempts = 1;
	node->cancel_policy = AGENT_EXECUTION_CANCEL_ALLOW;
	node->charge_class = CHARGE_RESERVED;
	node->exec_envelope[AGENT_RESOURCE_PROCESS] = 1;
}

static struct agent_execution_contract_key create_task_contract(
	struct agent_workflow_lifecycle_key lifecycle)
{
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;

	for (uint i = 0; i < TASK_NODE_COUNT; i++)
		task_node_init(i);
	memset(&control, 0, sizeof(control));
	control.version = AGENT_EXECUTION_CONTRACT_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_EXECUTION_CONTRACT_CREATE;
	control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
	control.key.lifecycle = lifecycle;
	control.request_id = ++next_request_id;
	control.nodes = (uint64)task_nodes;
	control.node_count = TASK_NODE_COUNT;
	control.node_size = sizeof(task_nodes[0]);
	memset(&result, 0, sizeof(result));
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_OK &&
	      result.state == AGENT_EXECUTION_CONTRACT_FROZEN &&
	      result.key.generation != 0 &&
	      result.node_count == TASK_NODE_COUNT,
	      "create enforced mixed-resource Task contract");

	memset(&control, 0, sizeof(control));
	control.version = AGENT_EXECUTION_CONTRACT_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_EXECUTION_CONTRACT_QUERY;
	control.key = result.key;
	control.request_id = ++next_request_id;
	control.nodes = (uint64)queried_nodes;
	control.node_count = TASK_NODE_COUNT;
	control.node_size = sizeof(queried_nodes[0]);
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_OK &&
	      result.node_count == TASK_NODE_COUNT,
	      "query canonical Task schemas");
	for (uint i = 0; i < TASK_NODE_COUNT; i++) {
		unsigned char aggregate = 0;

		for (uint j = 0; j < sizeof(queried_nodes[i].schema_digest); j++)
			aggregate |= queried_nodes[i].schema_digest[j];
		check(queried_nodes[i].node_id == i && aggregate != 0 &&
		      queried_nodes[i].input_artifact_type ==
			      (i >= TASK_RESOURCE_BORROW_NODE ?
				       AGENT_ARTIFACT_UTF8 : AGENT_ARTIFACT_NONE) &&
		      queried_nodes[i].output_artifact_type == AGENT_ARTIFACT_NONE,
		      "query preserves typed Task nodes and canonical schema");
	}
	return result.key;
}

static void run_scalar_ablation(
	const struct agent_execution_contract_key *key)
{
	struct ablation_metrics metrics;
	uint fingerprint = 0;

	memset(scalar_requests, 0, sizeof(scalar_requests));
	memset(scalar_responses, 0, sizeof(scalar_responses));
	memset(scalar_params, 0, sizeof(scalar_params));
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		struct agent_request_v3 *request = &scalar_requests[i];
		struct agent_param_v2 *params = scalar_params[i];

		params[0].version = AGENT_PARAM_VERSION;
		params[0].size = sizeof(params[0]);
		params[0].type = AGENT_PARAM_STRING;
		params[0].value_size = 1;
		memcpy(params[0].key, "payload", sizeof("payload"));
		params[0].value.string_value[0] = 0;
		params[1].version = AGENT_PARAM_VERSION;
		params[1].size = sizeof(params[1]);
		params[1].type = AGENT_PARAM_UINT64;
		params[1].value_size = sizeof(params[1].value.uint64_value);
		memcpy(params[1].key, "arg0", sizeof("arg0"));
		params[2].version = AGENT_PARAM_VERSION;
		params[2].size = sizeof(params[2]);
		params[2].type = AGENT_PARAM_UINT64;
		params[2].value_size = sizeof(params[2].value.uint64_value);
		memcpy(params[2].key, "arg1", sizeof("arg1"));

		request->version = AGENT_CALL_VERSION_V3;
		request->size = sizeof(*request);
		request->tool_id = AGENT_TOOL_ECHO;
		request->param_count = ECHO_PARAM_COUNT;
		request->request_id = PERF_REQUEST_BASE + i + 1;
		request->params = (uint64)params;
		request->contract = *key;
		request->node_id = i;
		request->attempt_id = 1;
		memcpy(request->input_fingerprint, empty_echo_fingerprint,
		       sizeof(request->input_fingerprint));
		memcpy(request->schema_digest, queried_nodes[i].schema_digest,
		       sizeof(request->schema_digest));
		request->input_artifact_type = AGENT_ARTIFACT_NONE;
		request->source_node_id = AGENT_EXECUTION_NODE_NONE;
	}
	ablation_begin(&metrics);
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		int rc = tool_call_v3(&scalar_requests[i], &scalar_responses[i]);
		int ok = rc == 0 &&
			scalar_responses[i].status == AGENT_STATUS_OK &&
			scalar_responses[i].request_id ==
				scalar_requests[i].request_id &&
			scalar_responses[i].output_artifact_type ==
				AGENT_ARTIFACT_NONE &&
			scalar_responses[i].evidence_ticket != 0;

		if (!ok)
			printf("agenttask_ucore: diagnostic point=scalar_v3 index=%u rc=%d "
			       "status=%d reason=%u evidence=%llu sequence=%llu "
			       "request=%llu response_request=%llu tool=%u node=%u "
			       "attempt=%u lifecycle=%u:%llu contract=%llu "
			       "input_artifact=%u output_artifact=%u flags=%u "
			       "output_labels=%llu result=%s\n",
			       i, rc, scalar_responses[i].status,
			       scalar_responses[i].decision_reason,
			       scalar_responses[i].evidence_ticket,
			       scalar_responses[i].sequence,
			       scalar_requests[i].request_id,
			       scalar_responses[i].request_id,
			       scalar_responses[i].tool_id,
			       scalar_responses[i].node_id,
			       scalar_responses[i].attempt_id,
			       scalar_responses[i].contract.lifecycle.id,
			       scalar_responses[i].contract.lifecycle.generation,
			       scalar_responses[i].contract.generation,
			       scalar_requests[i].input_artifact_type,
			       scalar_responses[i].output_artifact_type,
			       scalar_responses[i].completion_flags,
			       scalar_responses[i].output_provenance_labels,
			       scalar_responses[i].result);
		check(ok, "scalar v3 completes typed empty ECHO with Evidence");
	}
	ablation_end(&metrics);
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		uint64 service_tick = 0;
		uint current = semantic_fingerprint(
			scalar_responses[i].status, scalar_responses[i].tool_id,
			scalar_responses[i].request_id,
			scalar_responses[i].sequence,
			scalar_responses[i].evidence_ticket, 1, &service_tick);

		check(current == SEMANTIC_EXPECTED,
		      "scalar v3 has status/context/evidence semantics");
		if (i == 0)
			fingerprint = current;
		check(current == fingerprint,
		      "scalar v3 repeats one semantic fingerprint");
		ablation_record_service_start(&metrics, i, service_tick);
	}
	report_ablation(
		"scalar_v3", PERF_OPERATION_COUNT,
		PERF_OPERATION_COUNT *
			(sizeof(struct agent_request_v3) +
			 sizeof(struct agent_response_v3) +
			 ECHO_PARAM_COUNT * sizeof(struct agent_param_v2)),
		PERF_OPERATION_COUNT *
			(sizeof(struct agent_request_v3) +
			 sizeof(struct agent_response_v3) +
			 ECHO_PARAM_COUNT * sizeof(struct agent_param_v2)),
		PERF_OPERATION_COUNT * TOOL_V3_DISPATCH_HEADER_BYTES,
		0, 0, &metrics);
	printf("agenttask_ucore: perf_fp path=scalar_v3 value=%u\n",
	       fingerprint);
}

static void enter_channel(uint flags, uint max_submit, uint min_complete,
			  uint64 generation, uint64 sq_tail, uint64 cq_head,
			  struct agent_task_channel_enter_result *result)
{
	struct agent_task_channel_enter enter;

	memset(&enter, 0, sizeof(enter));
	memset(result, 0, sizeof(*result));
	enter.version = AGENT_TASK_CHANNEL_VERSION;
	enter.size = sizeof(enter);
	enter.flags = flags;
	enter.max_submit = max_submit;
	enter.generation = generation;
	enter.sq_tail = sq_tail;
	enter.cq_head = cq_head;
	enter.min_complete = min_complete;
	check(agent_task_channel_enter(&enter, result) == 0,
	      "Task Channel enter syscall framing");
}

static struct agent_task_sqe make_submit_sqe(
	const struct agent_execution_contract_key *key, uint node_id,
	uint64 request_id, uint64 generation, uint64 sq_position, uint flags,
	uint64 deadline_tick)
{
	struct agent_task_sqe sqe;

	memset(&sqe, 0, sizeof(sqe));
	sqe.version = AGENT_TASK_CHANNEL_ENTRY_VERSION;
	sqe.size = sizeof(sqe);
	sqe.opcode = AGENT_TASK_CHANNEL_OP_SUBMIT;
	sqe.flags = flags;
	sqe.request_id = request_id;
	sqe.ring_generation = generation;
	sqe.slot_generation =
		sq_position / AGENT_TASK_CHANNEL_CAPACITY + 1;
	sqe.contract = *key;
	sqe.node_id = node_id;
	sqe.attempt_id = 1;
	sqe.tool_id = AGENT_TOOL_ECHO;
	sqe.deadline_tick = deadline_tick;
	memcpy(sqe.schema_digest, queried_nodes[node_id].schema_digest,
	       sizeof(sqe.schema_digest));
	return sqe;
}

static struct agent_task_sqe make_cancel_sqe(
	const struct agent_task_sqe *target, uint64 request_id,
	uint64 generation, uint64 sq_position)
{
	struct agent_task_sqe sqe;

	memset(&sqe, 0, sizeof(sqe));
	sqe.version = AGENT_TASK_CHANNEL_ENTRY_VERSION;
	sqe.size = sizeof(sqe);
	sqe.opcode = AGENT_TASK_CHANNEL_OP_CANCEL;
	sqe.flags = AGENT_TASK_SQE_F_CANCEL;
	sqe.request_id = request_id;
	sqe.ring_generation = generation;
	sqe.slot_generation =
		sq_position / AGENT_TASK_CHANNEL_CAPACITY + 1;
	sqe.contract = target->contract;
	sqe.node_id = target->node_id;
	sqe.attempt_id = target->attempt_id;
	sqe.tool_id = target->tool_id;
	sqe.link_request_id = target->request_id;
	memcpy(sqe.schema_digest, target->schema_digest,
	       sizeof(sqe.schema_digest));
	return sqe;
}

static void write_sqe(volatile struct task_sq_page_view *sq, uint64 position,
		      const struct agent_task_sqe *entry)
{
	volatile_write((void *)&sq->entries[position %
					       AGENT_TASK_CHANNEL_CAPACITY],
		       entry, sizeof(*entry));
}

static struct agent_task_cqe read_cqe(
	volatile const struct task_cq_page_view *cq, uint64 position)
{
	struct agent_task_cqe entry;

	volatile_read(&entry,
		      (const void *)&cq->entries[position %
						AGENT_TASK_CHANNEL_CAPACITY],
		      sizeof(entry));
	return entry;
}

static void check_normal_cqe(const struct agent_task_cqe *cqe,
			     const struct agent_task_sqe *sqe)
{
	check(cqe->version == AGENT_TASK_CHANNEL_ENTRY_VERSION &&
	      cqe->size == sizeof(*cqe) && cqe->flags == 0 &&
	      cqe->status == AGENT_STATUS_OK &&
	      cqe->decision_reason == AGENT_EXECUTION_REASON_NONE &&
	      cqe->request_id == sqe->request_id &&
	      cqe->ring_generation == sqe->ring_generation &&
	      cqe->slot_generation == sqe->slot_generation &&
	      cqe->contract.generation == sqe->contract.generation &&
	      cqe->node_id == sqe->node_id &&
	      cqe->attempt_id == sqe->attempt_id &&
	      cqe->tool_id == sqe->tool_id && handle_is_null(cqe->result) &&
	      cqe->context_sequence != 0 && cqe->evidence_ticket != 0 &&
	      (cqe->provenance_labels & AGENT_PROVENANCE_AGENT_DERIVED) != 0 &&
	      cqe->completion_tick != 0 && cqe->reserved == 0,
	      "Task CQE is canonical, typed-null, and evidence linked");
}

static void setup_task_channel(
	const struct agent_execution_contract_key *key,
	struct task_channel_view *view)
{
	struct agent_task_channel_setup setup;
	struct agent_task_channel_setup_result result;
	struct agent_task_ring_header sq_header, cq_header;

	memset(view, 0, sizeof(*view));
	memset(&setup, 0, sizeof(setup));
	memset(&result, 0, sizeof(result));
	setup.version = AGENT_TASK_CHANNEL_VERSION;
	setup.size = sizeof(setup);
	setup.flags = AGENT_TASK_CHANNEL_SETUP_F_SINGLE_ISSUER;
	setup.lifecycle = key->lifecycle;
	check(agent_task_channel_setup(&setup, &result) == 0 &&
	      result.status == AGENT_TASK_CHANNEL_OK &&
	      result.generation != 0 &&
	      result.sq_capacity == AGENT_TASK_CHANNEL_CAPACITY &&
	      result.cq_capacity == AGENT_TASK_CHANNEL_CAPACITY &&
	      result.sqe_size == sizeof(struct agent_task_sqe) &&
	      result.cqe_size == sizeof(struct agent_task_cqe) &&
	      result.mapped_page_count == 2 &&
	      result.private_page_count == 2,
	      "setup single-issuer typed Task Channel");
	view->generation = result.generation;
	view->sq = (volatile struct task_sq_page_view *)(unsigned long)
		result.sq_base;
	view->cq = (volatile const struct task_cq_page_view *)(unsigned long)
		result.cq_base;
	volatile_read(&sq_header, (const void *)&view->sq->header,
		      sizeof(sq_header));
	volatile_read(&cq_header, (const void *)&view->cq->header,
		      sizeof(cq_header));
	check(sq_header.magic == AGENT_TASK_CHANNEL_SQ_MAGIC &&
	      cq_header.magic == AGENT_TASK_CHANNEL_CQ_MAGIC &&
	      sq_header.generation == view->generation &&
	      cq_header.generation == view->generation &&
	      sq_header.entry_size == sizeof(struct agent_task_sqe) &&
	      cq_header.entry_size == sizeof(struct agent_task_cqe) &&
	      (sq_header.flags & AGENT_TASK_CHANNEL_RING_F_ACTIVE) != 0,
	      "mapped SQ/CQ headers are frozen and active");
}

static int resource_handle_equal(struct agent_task_resource_handle left,
				 struct agent_task_resource_handle right)
{
	return left.slot == right.slot && left.type == right.type &&
	       left.flags == right.flags && left.generation == right.generation;
}

static void task_resource_call(
	const struct task_channel_view *view, uint operation,
	struct agent_task_resource_handle handle, uint resource_type,
	uint resource_flags, uint64 source_handle, uint64 length,
	struct agent_task_channel_resource_result *result)
{
	struct agent_task_channel_resource resource;

	memset(&resource, 0, sizeof(resource));
	resource.version = AGENT_TASK_CHANNEL_VERSION;
	resource.size = sizeof(resource);
	resource.operation = operation;
	resource.handle = handle;
	resource.resource_type = resource_type;
	resource.resource_flags = resource_flags;
	resource.source_handle = source_handle;
	resource.length = length;
	resource.channel_generation = view->generation;
	memset(result, 0, sizeof(*result));
	check(agent_task_channel_resource(&resource, result) == 0,
	      "Task resource syscall framing");
}

static void task_resource_bad_result_probe(
	const struct task_channel_view *view, int fd, uint64 length)
{
	struct agent_task_channel_resource resource;

	memset(&resource, 0, sizeof(resource));
	resource.version = AGENT_TASK_CHANNEL_VERSION;
	resource.size = sizeof(resource);
	resource.operation = AGENT_TASK_RESOURCE_IMPORT;
	resource.resource_type = AGENT_ARTIFACT_UTF8;
	resource.resource_flags = AGENT_TASK_HANDLE_F_OWNED;
	resource.source_handle = (uint64)(uint)fd;
	resource.length = length;
	resource.channel_generation = view->generation;
	check(agent_task_channel_resource(
		      &resource,
		      (struct agent_task_channel_resource_result *)(unsigned long)1) ==
		      -1,
	      "invalid result pointer is rejected before import commit");
}

static void task_resource_create(const char *path, const void *content,
				 uint length)
{
	int fd;

	(void)unlink(path);
	fd = open(path, O_CREATE | O_TRUNC | O_WRONLY);
	check(fd >= 0, "create Task resource input file");
	check(write(fd, content, length) == (ssize_t)length,
	      "write exact Task resource input file");
	check(close(fd) == 0, "close Task resource writer");
}

static void task_resource_close_worker(void *arg)
{
	struct task_resource_close_race *race = arg;

	while (!race->start)
		(void)sched_yield();
	/* Give the importing thread one turn to acquire its transaction pin. */
	(void)sched_yield();
	race->close_status = close(race->fd);
	exit(0);
}

static void prime_task_resource_context(void)
{
	static struct agent_op op;
	static struct agent_result result;

	memset(&op, 0, sizeof(op));
	memset(&result, 0, sizeof(result));
	op.version = AGENT_OP_VERSION;
	op.tool_id = AGENT_TOOL_ECHO;
	op.request_id = ++next_request_id;
	check(agent_run(&op, &result, 1, 0) == 1 &&
	      result.status == AGENT_STATUS_OK && result.sequence != 0,
	      "prime Context for pre-contract resource race");
}

static void prepare_task_resource_inputs(struct task_resource_inputs *inputs)
{
	unsigned char first_byte = 0;
	int pipe_fd[2];

	memset(inputs, 0, sizeof(*inputs));
	task_resource_create(
		TASK_RESOURCE_FIRST_PATH, task_resource_first_payload,
		sizeof(task_resource_first_payload) - 1U);
	task_resource_create(
		TASK_RESOURCE_SECOND_PATH, task_resource_second_payload,
		sizeof(task_resource_second_payload) - 1U);
	task_resource_create(
		TASK_RESOURCE_NUL_PATH, task_resource_embedded_nul,
		sizeof(task_resource_embedded_nul));
	task_resource_create(
		TASK_RESOURCE_UTF8_PATH, task_resource_invalid_utf8,
		sizeof(task_resource_invalid_utf8));
	task_resource_create(
		TASK_RESOURCE_RACE_PATH, task_resource_first_payload,
		sizeof(task_resource_first_payload) - 1U);

	(void)close(0);
	inputs->first_fd = open(TASK_RESOURCE_FIRST_PATH, O_RDONLY);
	check(inputs->first_fd == 0,
	      "ordinary Task input uses valid descriptor zero");
	check(read(inputs->first_fd, &first_byte, 1) == 1 &&
	      first_byte == (unsigned char)task_resource_first_payload[0],
	      "advance the caller offset before the immutable snapshot");
	inputs->second_fd = open(TASK_RESOURCE_SECOND_PATH, O_RDONLY);
	inputs->embedded_nul_fd = open(TASK_RESOURCE_NUL_PATH, O_RDONLY);
	inputs->invalid_utf8_fd = open(TASK_RESOURCE_UTF8_PATH, O_RDONLY);
	inputs->write_only_fd = open(TASK_RESOURCE_FIRST_PATH, O_WRONLY);
	inputs->race_fd = open(TASK_RESOURCE_RACE_PATH, O_RDONLY);
	check(inputs->second_fd >= 0 && inputs->embedded_nul_fd >= 0 &&
	      inputs->invalid_utf8_fd >= 0 && inputs->write_only_fd >= 0 &&
	      inputs->race_fd >= 0,
	      "pre-open ordinary Task resource descriptors");
	check(unlink(TASK_RESOURCE_RACE_PATH) == 0,
	      "unlink race input while its read descriptor remains live");
	check(pipe(pipe_fd) == 0, "pre-open non-file import probe");
	inputs->pipe_read_fd = pipe_fd[0];
	inputs->pipe_write_fd = pipe_fd[1];
	printf("agenttask_ucore: resource_inputs_prepared=1 scope_local=1\n");
}

static void exercise_task_resource_close_race(
	const struct task_channel_view *view,
	const struct task_resource_inputs *inputs)
{
	struct agent_task_channel_resource_result result;
	struct task_resource_close_race race;
	int tid;

	memset(&race, 0, sizeof(race));
	race.close_status = -1;
	race.fd = inputs->race_fd;
	tid = thread_create(task_resource_close_worker, &race);
	check(tid > 0, "start sibling close for unlinked Task input");
	race.start = 1;
	task_resource_call(
		view, AGENT_TASK_RESOURCE_IMPORT,
		(struct agent_task_resource_handle){ 0 }, AGENT_ARTIFACT_UTF8,
		AGENT_TASK_HANDLE_F_OWNED, (uint64)(uint)race.fd,
		sizeof(task_resource_first_payload) - 1U, &result);
	check(waittid(tid) == 0 && race.close_status == 0,
	      "sibling closes the unlinked descriptor during import race");
	check(result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_RESOURCE_STATE_LIVE,
	      "transaction pin preserves the unlinked file snapshot");
	task_resource_call(view, AGENT_TASK_RESOURCE_RELEASE, result.handle, 0, 0,
			   0, 0, &result);
	check(result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_RESOURCE_STATE_NONE,
	      "release snapshot after sibling close race");
	printf("agenttask_ucore: resource_unlinked_close_race=1 "
	       "transaction_pin=1 launched_concurrently=1\n");
}

static void check_resource_echo(const struct agent_task_cqe *cqe,
				const struct agent_task_sqe *sqe,
				const char *expected)
{
	struct agent_context_detail detail;
	uint length = strlen(expected);

	check(cqe->version == AGENT_TASK_CHANNEL_ENTRY_VERSION &&
	      cqe->size == sizeof(*cqe) && cqe->status == AGENT_STATUS_OK &&
	      cqe->request_id == sqe->request_id &&
	      cqe->node_id == sqe->node_id &&
	      cqe->tool_id == AGENT_TOOL_ECHO && handle_is_null(cqe->result) &&
	      (cqe->provenance_labels &
	       AGENT_PROVENANCE_UNTRUSTED_FILE_DATA) != 0,
	      "resource ECHO completes with file provenance");
	memset(&detail, 0, sizeof(detail));
	check(context_detail(cqe->context_sequence, &detail) == 0 &&
	      detail.sequence == cqe->context_sequence &&
	      detail.op.tool_id == AGENT_TOOL_ECHO &&
	      detail.op.request_id == sqe->request_id &&
	      detail.result.status == AGENT_STATUS_OK &&
	      detail.result.request_id == sqe->request_id &&
	      detail.result.value0 == length &&
	      strcmp(detail.op.payload, expected) == 0 &&
	      strcmp(detail.result.result, expected) == 0,
	      "resource ECHO passes the immutable snapshot to the tool");
}

static void exercise_task_resources(
	const struct agent_execution_contract_key *key,
	const struct task_resource_inputs *inputs,
	const struct task_channel_view *prepared_view)
{
	static struct agent_info context_before, context_after;
	struct task_channel_view view = *prepared_view;
	struct agent_task_channel_resource_result resource_result;
	struct agent_task_channel_enter_result enter_result;
	struct agent_task_resource_handle first;
	struct agent_task_resource_handle second;
	struct agent_task_resource_handle borrowed;
	struct agent_task_sqe sqe;
	struct agent_task_cqe cqe;
	unsigned char second_byte = 0;
	int fd;

	sqe = make_submit_sqe(key, 0, ++next_request_id, view.generation,
			     view.sq_tail, 0, 0);
	write_sqe(view.sq, view.sq_tail++, &sqe);
	enter_channel(0, 1, 1, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.submitted == 1 &&
	      enter_result.cq_tail == view.cq_head + 1,
	      "prime an existing Context before importing a resource");
	cqe = read_cqe(view.cq, view.cq_head);
	check_normal_cqe(&cqe, &sqe);
	view.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	memset(&context_before, 0, sizeof(context_before));
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      agent_info(&context_before) == 0 &&
	      context_before.context_path_latest == cqe.context_sequence &&
	      context_before.context_path_count != 0 &&
	      (context_before.filesystem_capability_mask &
	       (AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE)) ==
		      (AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE),
	      "resource import binds an already-existing latest Context");

	fd = inputs->embedded_nul_fd;
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd, sizeof(task_resource_embedded_nul),
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "embedded NUL import is rejected");

	fd = inputs->invalid_utf8_fd;
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd, sizeof(task_resource_invalid_utf8),
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "invalid UTF-8 import is rejected");

	fd = inputs->write_only_fd;
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd,
			   sizeof(task_resource_first_payload) - 1U,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "write-only descriptor is rejected as an invalid import source");

	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)inputs->pipe_read_fd, 1,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "non-file descriptor is rejected as an invalid import source");

	fd = inputs->first_fd;
	check(fd == 0, "valid Task input retains descriptor zero");
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_BYTES, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd,
			   sizeof(task_resource_first_payload) - 1U,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "non-UTF8 import type is rejected");
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_BORROWED,
			   (uint64)(uint)fd,
			   sizeof(task_resource_first_payload) - 1U,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "IMPORT cannot create a borrowed handle");
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd, 0, &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "zero-length UTF-8 import is rejected");
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd, AGENT_TASK_RESOURCE_UTF8_MAX + 1U,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "oversized UTF-8 import is rejected");
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd,
			   sizeof(task_resource_first_payload) - 2U,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_BAD_REQUEST,
	      "prefix-only UTF-8 import is rejected by the EOF probe");
	task_resource_bad_result_probe(
		&view, fd, sizeof(task_resource_first_payload) - 1U);
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.resource_count == 0,
	      "invalid result pointer commits no resource");
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd,
			   sizeof(task_resource_first_payload) - 1U,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_OK &&
	      resource_result.state == AGENT_TASK_RESOURCE_STATE_LIVE &&
	      resource_result.source_handle == 0 &&
	      resource_result.length == sizeof(task_resource_first_payload) - 1U &&
	      resource_result.references == 0 &&
	      resource_result.handle.slot != 0 &&
	      resource_result.handle.type == AGENT_ARTIFACT_UTF8 &&
	      resource_result.handle.flags == AGENT_TASK_HANDLE_F_OWNED,
	      "fd-zero UTF-8 snapshot imports as an owned live resource");
	first = resource_result.handle;
	memset(&context_after, 0, sizeof(context_after));
	check(agent_info(&context_after) == 0 &&
	      context_after.context_path_count == context_before.context_path_count &&
	      context_after.context_path_latest ==
		      context_before.context_path_latest,
	      "IMPORT rejections, result preflight, and success do not append Context");
	check(read(fd, &second_byte, 1) == 1 &&
	      second_byte == (unsigned char)task_resource_first_payload[1],
	      "snapshot read at offset zero preserves the caller file offset");

	task_resource_call(&view, AGENT_TASK_RESOURCE_QUERY, first, 0, 0, 0, 0,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_OK &&
	      resource_result.state == AGENT_TASK_RESOURCE_STATE_LIVE &&
	      resource_handle_equal(resource_result.handle, first) &&
	      resource_result.references == 0,
	      "QUERY observes the copied resource after caller I/O advances");

	borrowed = first;
	borrowed.flags = AGENT_TASK_HANDLE_F_BORROWED;
	sqe = make_submit_sqe(key, TASK_RESOURCE_BORROW_NODE,
			       ++next_request_id, view.generation, view.sq_tail, 0,
			       0);
	sqe.input = borrowed;
	write_sqe(view.sq, view.sq_tail++, &sqe);
	enter_channel(0, 1, 1, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.submitted == 1 &&
	      enter_result.cq_tail == view.cq_head + 1,
	      "borrowed resource ECHO publishes one completion");
	cqe = read_cqe(view.cq, view.cq_head);
	check_resource_echo(&cqe, &sqe, task_resource_first_payload);
	view.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	task_resource_call(&view, AGENT_TASK_RESOURCE_QUERY, first, 0, 0, 0, 0,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_OK &&
	      resource_result.state == AGENT_TASK_RESOURCE_STATE_LIVE &&
	      resource_result.references == 0,
	      "borrowed completion leaves the owned base resource live");

	task_resource_call(&view, AGENT_TASK_RESOURCE_RELEASE, first, 0, 0, 0,
			   0, &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_OK &&
	      resource_result.state == AGENT_TASK_RESOURCE_STATE_NONE,
	      "RELEASE consumes the live owned resource");
	task_resource_call(&view, AGENT_TASK_RESOURCE_QUERY, first, 0, 0, 0, 0,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_STALE,
	      "released generation is stale");

	fd = inputs->second_fd;
	task_resource_call(&view, AGENT_TASK_RESOURCE_IMPORT,
			   (struct agent_task_resource_handle){ 0 },
			   AGENT_ARTIFACT_UTF8, AGENT_TASK_HANDLE_F_OWNED,
			   (uint64)(uint)fd,
			   sizeof(task_resource_second_payload) - 1U,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_OK &&
	      resource_result.handle.slot == first.slot &&
	      resource_result.handle.generation > first.generation,
	      "resource slot reuse advances generation");
	second = resource_result.handle;

	sqe = make_submit_sqe(key, TASK_RESOURCE_OWNED_NODE,
			       ++next_request_id, view.generation, view.sq_tail, 0,
			       0);
	sqe.input = second;
	write_sqe(view.sq, view.sq_tail++, &sqe);
	enter_channel(0, 1, 1, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.submitted == 1 &&
	      enter_result.cq_tail == view.cq_head + 1,
	      "owned resource ECHO publishes one completion");
	cqe = read_cqe(view.cq, view.cq_head);
	check_resource_echo(&cqe, &sqe, task_resource_second_payload);
	task_resource_call(&view, AGENT_TASK_RESOURCE_QUERY, second, 0, 0, 0, 0,
			   &resource_result);
	check(resource_result.status == AGENT_TASK_CHANNEL_STALE,
	      "owned completion automatically consumes the input resource");
	view.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.resource_count == 0 &&
	      enter_result.in_flight == 0 && enter_result.terminal_pending == 0,
	      "resource Task Channel finishes drained without retained objects");
}

static void run_task_ablation(
	const struct agent_execution_contract_key *key)
{
	struct task_channel_view view;
	struct agent_task_channel_enter_result enter_result;
	struct ablation_metrics metrics;
	uint fingerprint = 0;

	setup_task_channel(key, &view);
	memset(task_perf_sqes, 0, sizeof(task_perf_sqes));
	memset(task_perf_cqes, 0, sizeof(task_perf_cqes));
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++)
		task_perf_sqes[i] = make_submit_sqe(
			key, i, PERF_REQUEST_BASE + i + 1, view.generation,
			i, 0, 0);
	ablation_begin(&metrics);
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++)
		write_sqe(view.sq, i, &task_perf_sqes[i]);
	view.sq_tail = PERF_OPERATION_COUNT;
	enter_channel(0, PERF_OPERATION_COUNT, PERF_OPERATION_COUNT,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.submitted == PERF_OPERATION_COUNT &&
	      enter_result.sq_head == PERF_OPERATION_COUNT &&
	      enter_result.cq_tail == PERF_OPERATION_COUNT &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_CQ_FULL) != 0,
	      "Task ablation submits one full deterministic ECHO sequence");
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		struct agent_task_cqe *cqe = &task_perf_cqes[i];

		*cqe = read_cqe(view.cq, i);
		check_normal_cqe(cqe, &task_perf_sqes[i]);
	}
	view.cq_head = PERF_OPERATION_COUNT;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.cq_head == PERF_OPERATION_COUNT &&
	      enter_result.cq_tail == PERF_OPERATION_COUNT &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_CQ_FULL) == 0,
	      "Task ablation acknowledges the full completion sequence");
	ablation_end(&metrics);
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		uint64 service_start_tick = 0;
		uint current;

		current = semantic_fingerprint(
			task_perf_cqes[i].status, task_perf_cqes[i].tool_id,
			task_perf_cqes[i].request_id,
			task_perf_cqes[i].context_sequence,
			task_perf_cqes[i].evidence_ticket, 1,
			&service_start_tick);
		check(current == SEMANTIC_EXPECTED,
		      "Task ablation has status/context/evidence semantics");
		if (i == 0)
			fingerprint = current;
		check(current == fingerprint,
		      "Task ablation repeats one semantic fingerprint");
		ablation_record_service_start(
			&metrics, i, service_start_tick);
	}
	report_ablation(
		"sq_cq", 2,
		PERF_OPERATION_COUNT *
			(sizeof(struct agent_task_sqe) +
			 sizeof(struct agent_task_cqe)),
		PERF_OPERATION_COUNT *
			(sizeof(struct agent_task_sqe) +
			 sizeof(struct agent_task_cqe)),
		0, 2 * (sizeof(struct agent_task_channel_enter) +
			 sizeof(struct agent_task_channel_enter_result)),
		2 * (sizeof(struct agent_task_channel_enter) +
			 2 * sizeof(struct agent_task_channel_enter_result)),
		&metrics);
	printf("agenttask_ucore: perf_fp path=sq_cq value=%u\n", fingerprint);
}

static void exercise_task_channel(
	const struct agent_execution_contract_key *key)
{
	struct task_channel_view view;
	static struct agent_task_channel_enter_result enter_result, before_stale;
	static struct agent_task_sqe pending, invalid, recovery, target, cancel;
	static struct agent_task_sqe acked_cancel, deadline, continued;
	static struct agent_task_cqe cqe, before_cancel, after_cancel;
	static struct agent_info info, cancel_begin, cancel_end;
	uint64 request_id = 0;

	setup_task_channel(key, &view);
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		request_id = ++next_request_id;
		task_perf_sqes[i] = make_submit_sqe(
			key, i, request_id, view.generation, view.sq_tail, 0, 0);
		write_sqe(view.sq, view.sq_tail, &task_perf_sqes[i]);
		view.sq_tail++;
	}
	enter_channel(0, PERF_OPERATION_COUNT, PERF_OPERATION_COUNT,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.submitted == PERF_OPERATION_COUNT &&
	      enter_result.cq_tail == PERF_OPERATION_COUNT &&
	      enter_result.backpressure == 0 &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_CQ_FULL) != 0,
	      "sixteen synchronous ECHOs fill the CQ exactly");
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++) {
		cqe = read_cqe(view.cq, i);
		check_normal_cqe(&cqe, &task_perf_sqes[i]);
	}

	request_id = ++next_request_id;
	pending = make_submit_sqe(key, 16, request_id, view.generation,
				  view.sq_tail, 0, 0);
	write_sqe(view.sq, view.sq_tail, &pending);
	view.sq_tail++;
	enter_channel(0, 1, 0, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.submitted == 0 &&
	      enter_result.sq_head == PERF_OPERATION_COUNT &&
	      enter_result.cq_tail == PERF_OPERATION_COUNT &&
	      enter_result.backpressure == 1 &&
	      enter_result.last_accepted_request_id ==
		      task_perf_sqes[PERF_OPERATION_COUNT - 1].request_id &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_CQ_FULL) != 0,
	      "CQ-full preserves the seventeenth SQE and reports backpressure");
	view.cq_head = 1;
	enter_channel(0, 1, 1, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.submitted == 1 &&
	      enter_result.sq_head == PERF_OPERATION_COUNT + 1 &&
	      enter_result.cq_head == 1 &&
	      enter_result.cq_tail == PERF_OPERATION_COUNT + 1 &&
	      enter_result.backpressure == 1 &&
	      enter_result.last_accepted_request_id == pending.request_id &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_CQ_FULL) != 0,
	      "one CQ ack admits the preserved seventeenth SQE");
	cqe = read_cqe(view.cq, PERF_OPERATION_COUNT);
	check_normal_cqe(&cqe, &pending);
	view.cq_head = PERF_OPERATION_COUNT + 1;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.cq_head == view.cq_head &&
	      enter_result.cq_tail == view.cq_head &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_CQ_FULL) == 0,
	      "CQ drain clears full state after preserved-work recovery");

	/* Reusing the accepted id is a protocol fault and sticks until resync. */
	invalid = make_submit_sqe(key, 17, request_id, view.generation,
				  view.sq_tail, 0, 0);
	write_sqe(view.sq, view.sq_tail, &invalid);
	view.sq_tail++;
	enter_channel(0, 1, 0, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_RESYNC_REQUIRED &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_RESYNC) != 0 &&
	      enter_result.protocol_faults == 1 &&
	      enter_result.resync_count == 1 &&
	      enter_result.generation != view.generation &&
	      enter_result.sq_head == 0,
	      "non-monotonic request id enters sticky resync");
	view.generation = enter_result.generation;
	view.sq_tail = 0;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_RESYNC, 0, 0,
		      view.generation, 0, view.cq_head, &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_RESYNC) == 0 &&
	      enter_result.generation == view.generation,
	      "explicit zero-tail resync recovers the channel");

	request_id = ++next_request_id;
	recovery = make_submit_sqe(key, 17, request_id, view.generation,
				   view.sq_tail, 0, 0);
	write_sqe(view.sq, view.sq_tail, &recovery);
	view.sq_tail++;
	enter_channel(0, 1, 1, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.last_accepted_request_id == request_id,
	      "post-resync monotonic request completes");
	cqe = read_cqe(view.cq, view.cq_head);
	check_normal_cqe(&cqe, &recovery);
	view.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK,
	      "acknowledge post-resync completion");

	request_id = ++next_request_id;
	target = make_submit_sqe(key, 18, request_id, view.generation,
				 view.sq_tail, 0, 0);
	write_sqe(view.sq, view.sq_tail, &target);
	view.sq_tail++;
	enter_channel(0, 1, 1, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK,
	      "retain target terminal for idempotent cancellation");
	before_cancel = read_cqe(view.cq, view.cq_head);
	check_normal_cqe(&before_cancel, &target);
	request_id = ++next_request_id;
	cancel = make_cancel_sqe(&target, request_id, view.generation,
				 view.sq_tail);
	memset(&cancel_begin, 0, sizeof(cancel_begin));
	memset(&cancel_end, 0, sizeof(cancel_end));
	check(agent_info(&cancel_begin) == 0,
	      "sample retained-terminal cancel start tick");
	write_sqe(view.sq, view.sq_tail, &cancel);
	view.sq_tail++;
	enter_channel(0, 1, 0, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(agent_info(&cancel_end) == 0 &&
	      cancel_end.current_tick >= cancel_begin.current_tick,
	      "sample retained-terminal cancel end tick");
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.submitted == 1 &&
	      enter_result.cq_tail == view.cq_head + 1 &&
	      enter_result.last_accepted_request_id == request_id,
	      "target-only cancel consumes without a cancel CQE");
	after_cancel = read_cqe(view.cq, view.cq_head);
	check(bytes_equal(&before_cancel, &after_cancel,
			  sizeof(before_cancel)),
	      "retained target has exactly one immutable terminal CQE");
	printf("agenttask_ucore: cancel_latency scope=retained_terminal "
	       "metric=service_tick ticks=%llu enter_calls=1 "
	       "pending_provider=unavailable observer_syscalls=2\n",
	       cancel_end.current_tick - cancel_begin.current_tick);
	view.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK,
	      "acknowledge the sole target completion");
	before_stale = enter_result;
	request_id = ++next_request_id;
	acked_cancel = make_cancel_sqe(&target, request_id, view.generation,
				      view.sq_tail);
	write_sqe(view.sq, view.sq_tail, &acked_cancel);
	view.sq_tail++;
	enter_channel(0, 1, 0, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_STALE &&
	      enter_result.submitted == 1 && enter_result.completed == 0 &&
	      enter_result.sq_head == before_stale.sq_head + 1 &&
	      enter_result.sq_head == view.sq_tail &&
	      request_id > before_stale.last_accepted_request_id &&
	      enter_result.last_accepted_request_id == request_id &&
	      enter_result.cq_head == before_stale.cq_head &&
	      enter_result.cq_tail == before_stale.cq_tail &&
	      enter_result.generation == before_stale.generation &&
	      enter_result.protocol_faults == before_stale.protocol_faults &&
	      enter_result.resync_count == before_stale.resync_count &&
	      enter_result.backpressure == before_stale.backpressure &&
	      enter_result.in_flight == 0 &&
	      enter_result.terminal_pending == 0 &&
	      enter_result.flags == before_stale.flags &&
	      (enter_result.flags & AGENT_TASK_CHANNEL_RING_F_RESYNC) == 0,
	      "ACKed target cancel is consumed stale without resync or CQE");

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "query tick for hard deadline");
	if (info.current_tick == 0) {
		sleep(1);
		check(agent_info(&info) == 0 && info.current_tick != 0,
		      "advance to a nonzero hard deadline tick");
	}
	request_id = ++next_request_id;
	deadline = make_submit_sqe(
		key, 19, request_id, view.generation, view.sq_tail,
		AGENT_TASK_SQE_F_HARD_DEADLINE, info.current_tick);
	write_sqe(view.sq, view.sq_tail, &deadline);
	view.sq_tail++;
	enter_channel(0, 1, 1, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK,
	      "hard deadline reaches a schedulable Task safe point");
	cqe = read_cqe(view.cq, view.cq_head);
	check(cqe.version == AGENT_TASK_CHANNEL_ENTRY_VERSION &&
	      cqe.size == sizeof(cqe) &&
	      cqe.flags == AGENT_TASK_CQE_F_DEADLINE &&
	      cqe.status == AGENT_STATUS_TIMEOUT &&
	      cqe.decision_reason == AGENT_EXECUTION_REASON_DEADLINE_EXPIRED &&
	      cqe.request_id == deadline.request_id &&
	      cqe.ring_generation == view.generation &&
	      cqe.node_id == deadline.node_id &&
	      cqe.tool_id == AGENT_TOOL_ECHO && handle_is_null(cqe.result) &&
	      cqe.context_sequence != 0 && cqe.evidence_ticket != 0 &&
	      cqe.reserved == 0,
	      "hard deadline has one typed TIMEOUT Context/Evidence terminal");
	view.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK,
	      "acknowledge hard-deadline terminal");

	request_id = ++next_request_id;
	continued = make_submit_sqe(key, 20, request_id, view.generation,
				    view.sq_tail, 0, 0);
	write_sqe(view.sq, view.sq_tail, &continued);
	view.sq_tail++;
	enter_channel(0, 1, 1, view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK,
	      "channel remains usable after resync, cancel, and deadline");
	cqe = read_cqe(view.cq, view.cq_head);
	check_normal_cqe(&cqe, &continued);
	view.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head,
		      &enter_result);
	check(enter_result.status == AGENT_TASK_CHANNEL_OK &&
	      enter_result.sq_head == view.sq_tail &&
	      enter_result.cq_head == view.cq_head &&
	      enter_result.cq_tail == view.cq_head &&
	      enter_result.in_flight == 0 &&
	      enter_result.terminal_pending == 0 &&
	      enter_result.resource_count == 0 &&
	      (enter_result.flags & (AGENT_TASK_CHANNEL_RING_F_RESYNC |
				     AGENT_TASK_CHANNEL_RING_F_CQ_FULL |
				     AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE)) == 0,
	      "final Task Channel state is drained and stable");
	printf("agenttask_ucore: cq_full=1 backpressure=1 pending_preserved=1 "
	       "recovery_enter_calls=2 resync_recovery=1\n");
}

static struct agent_workflow_lifecycle_key current_lifecycle(void)
{
	struct agent_workflow_lifecycle_info lifecycle;

	memset(&lifecycle, 0, sizeof(lifecycle));
	check(agent_workflow_lifecycle_info(&lifecycle, 0) == AGENT_STATUS_OK &&
	      lifecycle.charged && lifecycle.key.id != 0 &&
	      lifecycle.key.generation != 0,
	      "query full workflow lifecycle key");
	return lifecycle.key;
}

static int task_lifecycle_equal(struct agent_workflow_lifecycle_key left,
				struct agent_workflow_lifecycle_key right)
{
	return left.id == right.id && left.reserved == right.reserved &&
	       left.generation == right.generation;
}

static int task_pipe_read_exact(int fd, void *buffer, uint size)
{
	unsigned char *out = buffer;
	uint offset = 0;

	while (offset < size) {
		ssize_t got = read(fd, out + offset, size - offset);

		if (got <= 0)
			return -1;
		offset += (uint)got;
	}
	return 0;
}

static int task_pipe_write_exact(int fd, const void *buffer, uint size)
{
	const unsigned char *input = buffer;
	uint offset = 0;

	while (offset < size) {
		ssize_t wrote = write(fd, input + offset, size - offset);

		if (wrote <= 0)
			return -1;
		offset += (uint)wrote;
	}
	return 0;
}

static int task_delegate_identity_lookup(int pid, int expected_agent_id,
					 int expected_role, uint64 *control_id)
{
	static struct agent_audit_record records[32];
	struct agent_audit_filter filter;
	int count;

	memset(&filter, 0, sizeof(filter));
	memset(records, 0, sizeof(records));
	filter.flags = AGENT_AUDIT_FILTER_PID;
	filter.pid = pid;
	count = agent_audit_query(&filter, records, 32);
	if (count < 0)
		return -1;
	for (int i = count - 1; i >= 0; i--)
		if (records[i].pid == pid &&
		    records[i].agent_id == expected_agent_id &&
		    records[i].role == expected_role &&
		    records[i].actor_control_id != 0) {
			*control_id = records[i].actor_control_id;
			return 0;
		}
	return -1;
}

static struct agent_execution_contract_key create_delegate_contract(
	struct agent_workflow_lifecycle_key lifecycle,
	struct agent_execution_contract_node *queried)
{
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;
	struct agent_execution_contract_node node;
	struct agent_execution_contract_key key;
	unsigned char schema = 0;

	memset(&node, 0, sizeof(node));
	node.version = AGENT_EXECUTION_CONTRACT_NODE_VERSION;
	node.size = sizeof(node);
	node.node_id = 0;
	node.tool_id = AGENT_TOOL_DELEGATE_TASK;
	node.required_capabilities = AGENT_CAP_ORCHESTRATE;
	node.accepted_input_labels = AGENT_PROVENANCE_ALL;
	node.output_add_labels = AGENT_PROVENANCE_AGENT_DERIVED;
	node.side_effect_mask = TASK_DELEGATE_SIDE_EFFECTS;
	node.input_artifact_type = AGENT_ARTIFACT_TASK;
	node.output_artifact_type = AGENT_ARTIFACT_NONE;
	node.max_attempts = 1;
	node.cancel_policy = AGENT_EXECUTION_CANCEL_ALLOW;
	node.charge_class = CHARGE_RESERVED;
	node.exec_envelope[AGENT_RESOURCE_PROCESS] = 1;

	memset(&control, 0, sizeof(control));
	memset(&result, 0, sizeof(result));
	control.version = AGENT_EXECUTION_CONTRACT_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_EXECUTION_CONTRACT_CREATE;
	control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
	control.key.lifecycle = lifecycle;
	control.request_id = ++next_request_id;
	control.nodes = (uint64)&node;
	control.node_count = 1;
	control.node_size = sizeof(node);
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_OK &&
	      result.state == AGENT_EXECUTION_CONTRACT_FROZEN &&
	      result.key.generation != 0 && result.node_count == 1,
	      "create delegated Task contract");
	key = result.key;

	memset(queried, 0, sizeof(*queried));
	memset(&control, 0, sizeof(control));
	memset(&result, 0, sizeof(result));
	control.version = AGENT_EXECUTION_CONTRACT_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_EXECUTION_CONTRACT_QUERY;
	control.key = key;
	control.request_id = ++next_request_id;
	control.nodes = (uint64)queried;
	control.node_count = 1;
	control.node_size = sizeof(*queried);
	check(agent_execution_contract(&control, &result) == 0 &&
	      result.status == AGENT_STATUS_OK && result.node_count == 1,
	      "query delegated Task contract");
	for (uint j = 0; j < sizeof(queried->schema_digest); j++)
		schema |= queried->schema_digest[j];
	check(queried->node_id == 0 && queried->predecessor_mask == 0 &&
	      queried->tool_id == AGENT_TOOL_DELEGATE_TASK &&
	      queried->required_capabilities == AGENT_CAP_ORCHESTRATE &&
	      queried->accepted_input_labels == AGENT_PROVENANCE_ALL &&
	      queried->output_add_labels == AGENT_PROVENANCE_AGENT_DERIVED &&
	      queried->side_effect_mask == TASK_DELEGATE_SIDE_EFFECTS &&
	      queried->input_artifact_type == AGENT_ARTIFACT_TASK &&
	      queried->output_artifact_type == AGENT_ARTIFACT_NONE && schema != 0,
	      "delegated Task contract has Tool26 TASK-to-NONE schema");
	return key;
}

static void retire_delegate_contract(
	const struct agent_execution_contract_key *key)
{
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;

	for (uint retry = 0; retry < 128; retry++) {
		memset(&control, 0, sizeof(control));
		memset(&result, 0, sizeof(result));
		control.version = AGENT_EXECUTION_CONTRACT_VERSION;
		control.size = sizeof(control);
		control.operation = AGENT_EXECUTION_CONTRACT_RETIRE;
		control.key = *key;
		control.request_id = ++next_request_id;
		check(agent_execution_contract(&control, &result) == 0,
		      "frame delegated Task contract retirement");
		if (result.status == AGENT_STATUS_OK &&
		    result.state == AGENT_EXECUTION_CONTRACT_RECLAIMED)
			return;
		check(result.status == AGENT_STATUS_RETRY &&
		      result.state == AGENT_EXECUTION_CONTRACT_RETIRING,
		      "delegated contract retirement remains strictly bounded");
		sleep(1);
	}
	check(0, "delegated Task contract reaches strict RECLAIMED state");
}

static int task_delegate_open_descriptor(
	const char *path, const struct agent_task_delegate_descriptor *descriptor)
{
	int fd;

	task_resource_create(path, descriptor, sizeof(*descriptor));
	fd = open(path, O_RDONLY);
	check(fd >= 0, "open delegated Task descriptor");
	return fd;
}

static struct agent_task_resource_handle task_delegate_import_descriptor_fd(
	const struct task_channel_view *view, int fd,
	const struct agent_task_delegate_descriptor *descriptor)
{
	struct agent_task_channel_resource_result result;

	task_resource_call(
		view, AGENT_TASK_RESOURCE_IMPORT,
		(struct agent_task_resource_handle){ 0 }, AGENT_ARTIFACT_TASK,
		AGENT_TASK_HANDLE_F_OWNED, (uint64)(uint)fd,
		sizeof(*descriptor), &result);
	check(result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_RESOURCE_STATE_LIVE &&
	      result.handle.slot != 0 &&
	      result.handle.type == AGENT_ARTIFACT_TASK &&
	      result.handle.flags == AGENT_TASK_HANDLE_F_OWNED &&
	      result.handle.generation != 0 &&
	      result.length == sizeof(*descriptor),
	      "import exact 128-byte delegated TASK descriptor");
	return result.handle;
}

static struct agent_task_resource_handle task_delegate_import_descriptor(
	const struct task_channel_view *view, const char *path,
	const struct agent_task_delegate_descriptor *descriptor)
{
	struct agent_task_resource_handle handle;
	int fd = task_delegate_open_descriptor(path, descriptor);

	handle = task_delegate_import_descriptor_fd(view, fd, descriptor);
	check(close(fd) == 0, "close delegated Task descriptor after import");
	return handle;
}

static void task_delegate_read_descriptor_fd(
	int fd, const struct agent_task_delegate_descriptor *descriptor)
{
	struct agent_task_delegate_descriptor readback;
	char trailing;

	memset(&readback, 0, sizeof(readback));
	check(task_pipe_read_exact(fd, &readback, sizeof(readback)) == 0 &&
	      bytes_equal(&readback, descriptor, sizeof(readback)) &&
	      read(fd, &trailing, 1) == 0,
	      "regular inode read observes the exact descriptor and EOF");
}

static void task_delegate_release_descriptor(
	const struct task_channel_view *view,
	struct agent_task_resource_handle handle)
{
	struct agent_task_channel_resource_result result;

	task_resource_call(view, AGENT_TASK_RESOURCE_RELEASE, handle, 0, 0, 0,
			   0, &result);
	check(result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_RESOURCE_STATE_NONE,
	      "release borrowed delegated TASK descriptor after completion");
}

static struct agent_task_sqe task_delegate_submit(
	struct task_channel_view *view,
	const struct agent_execution_contract_key *contract,
	const struct agent_execution_contract_node *node,
	uint node_id, struct agent_task_resource_handle input, uint flags,
	uint64 deadline_tick, uint64 reserved_request_id)
{
	struct agent_task_channel_enter_result result;
	struct agent_task_sqe sqe;

	memset(&sqe, 0, sizeof(sqe));
	sqe.version = AGENT_TASK_CHANNEL_ENTRY_VERSION;
	sqe.size = sizeof(sqe);
	sqe.opcode = AGENT_TASK_CHANNEL_OP_SUBMIT;
	sqe.flags = flags;
	sqe.request_id = reserved_request_id != 0 ?
		reserved_request_id : ++next_request_id;
	sqe.ring_generation = view->generation;
	sqe.slot_generation =
		view->sq_tail / AGENT_TASK_CHANNEL_CAPACITY + 1;
	sqe.contract = *contract;
	sqe.node_id = node_id;
	sqe.attempt_id = 1;
	sqe.tool_id = AGENT_TOOL_DELEGATE_TASK;
	sqe.deadline_tick = deadline_tick;
	sqe.input = input;
	sqe.input.flags = AGENT_TASK_HANDLE_F_BORROWED;
	memcpy(sqe.schema_digest, node->schema_digest,
	       sizeof(sqe.schema_digest));
	write_sqe(view->sq, view->sq_tail, &sqe);
	view->sq_tail++;
	enter_channel(0, 1, 0, view->generation, view->sq_tail,
		      view->cq_head, &result);
	check(result.status == AGENT_TASK_CHANNEL_OK &&
	      result.submitted == 1 && result.completed == 0 &&
	      result.sq_head == view->sq_tail &&
	      result.cq_tail == view->cq_head && result.in_flight == 1 &&
	      result.last_accepted_request_id == sqe.request_id,
	      "submit one pending directed Tool26 TASK request");
	return sqe;
}

static void task_delegate_check_cqe(const struct agent_task_cqe *cqe,
				    const struct agent_task_sqe *sqe,
				    int status, uint flags, uint decision_reason,
				    int provider_pid, int provider_agent_id,
				    uint64 provider_control_id,
				    int cleared_executor_context)
{
	struct agent_context_detail detail;
	uint64 executor =
		((uint64)(uint)provider_agent_id <<
		 AGENT_TASK_DELEGATE_EXECUTOR_AGENT_SHIFT) |
		((uint64)(uint)provider_pid & AGENT_TASK_DELEGATE_EXECUTOR_PID_MASK);

	check(cqe->version == AGENT_TASK_CHANNEL_ENTRY_VERSION &&
	      cqe->size == sizeof(*cqe) && cqe->status == status &&
	      cqe->flags == flags && cqe->decision_reason == decision_reason &&
	      cqe->request_id == sqe->request_id &&
	      cqe->ring_generation == sqe->ring_generation &&
	      cqe->slot_generation == sqe->slot_generation &&
	      cqe->contract.lifecycle.id == sqe->contract.lifecycle.id &&
	      cqe->contract.lifecycle.generation ==
		      sqe->contract.lifecycle.generation &&
	      cqe->contract.generation == sqe->contract.generation &&
	      cqe->node_id == sqe->node_id && cqe->attempt_id == 1 &&
	      cqe->tool_id == AGENT_TOOL_DELEGATE_TASK &&
	      handle_is_null(cqe->result) && cqe->context_sequence != 0 &&
	      cqe->evidence_ticket != 0 && cqe->provenance_labels != 0 &&
	      (cqe->provenance_labels & AGENT_PROVENANCE_CROSS_AGENT_DATA) != 0 &&
	      cqe->completion_tick != 0 && cqe->reserved == 0,
	      "owner receives canonical delegated Task CQE with NONE output");
	memset(&detail, 0, sizeof(detail));
	check(context_detail(cqe->context_sequence, &detail) == 0 &&
	      detail.sequence == cqe->context_sequence &&
	      detail.op.tool_id == AGENT_TOOL_DELEGATE_TASK &&
	      detail.op.request_id == sqe->request_id &&
	      detail.result.status == status &&
	      detail.result.value0 == executor &&
	      detail.result.value1 == provider_control_id &&
	      (cleared_executor_context ? detail.result.value2 == 0 :
				    detail.result.value2 != 0),
	      "owner terminal Context attributes the provider executor");
}

static struct agent_task_cqe task_delegate_wait_owner_cqe(
	struct task_channel_view *view, const struct agent_task_sqe *sqe,
	int status, uint flags, uint decision_reason, int provider_pid,
	int provider_agent_id, uint64 provider_control_id,
	int cleared_executor_context)
{
	struct agent_task_channel_enter_result result;
	struct agent_task_cqe cqe;
	int ready = 0;

	for (uint retry = 0; retry < 1024; retry++) {
		enter_channel(0, 0, 0, view->generation, view->sq_tail,
			      view->cq_head, &result);
		check(result.status == AGENT_TASK_CHANNEL_OK &&
		      result.sq_head == view->sq_tail &&
		      result.cq_tail >= view->cq_head &&
		      result.cq_tail <= view->cq_head + 1,
		      "poll bounded delegated Task completion");
		if (result.cq_tail == view->cq_head + 1) {
			ready = 1;
			break;
		}
		sleep(1);
	}
	check(ready && result.in_flight == 0,
	      "owner observes exactly one delegated Task completion");
	cqe = read_cqe(view->cq, view->cq_head);
	task_delegate_check_cqe(
		&cqe, sqe, status, flags, decision_reason, provider_pid,
		provider_agent_id, provider_control_id, cleared_executor_context);
	view->cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view->generation, view->sq_tail, view->cq_head, &result);
	check(result.status == AGENT_TASK_CHANNEL_OK &&
	      result.sq_head == view->sq_tail &&
	      result.cq_head == view->cq_head &&
	      result.cq_tail == view->cq_head && result.in_flight == 0 &&
	      result.terminal_pending == 0,
	      "owner acknowledges the sole delegated Task CQE");
	return cqe;
}

static void task_delegate_descriptor_init(
	struct agent_task_delegate_descriptor *descriptor, int target_pid,
	uint target_agent_id, uint64 target_control_id, uint task_type,
	uint64 task_id, uint64 correlation_id)
{
	memset(descriptor, 0, sizeof(*descriptor));
	descriptor->version = AGENT_TASK_DELEGATE_DESCRIPTOR_VERSION;
	descriptor->size = sizeof(*descriptor);
	descriptor->target_pid = target_pid;
	descriptor->target_agent_id = target_agent_id;
	descriptor->task_type = task_type;
	descriptor->target_control_id = target_control_id;
	descriptor->task_id = task_id;
	descriptor->correlation_id = correlation_id;
	descriptor->parent_task_id = task_id + 1000ULL;
	descriptor->capsule_handle = task_id + 2000ULL;
	descriptor->allowed_tools = AGENT_TOOL_GRANT_BIT(AGENT_TOOL_ECHO);
	descriptor->resource_budget = 1;
	descriptor->read_budget = 1;
}

static void task_delegate_claim_one(
	struct agent_workflow_lifecycle_key lifecycle, int provider_pid,
	int provider_agent_id, uint64 task_id, uint64 correlation_id,
	struct agent_task_delegate_claim_result *claim)
{
	struct agent_task_delegate_claim request;
	int syscall_status = -1;
	int valid = 0;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_TASK_DELEGATE_VERSION;
	request.size = sizeof(request);
	request.flags = AGENT_TASK_DELEGATE_CLAIM_F_WAIT;
	request.lifecycle = lifecycle;
	for (uint retry = 0; retry < 16; retry++) {
		memset(claim, 0, sizeof(*claim));
		syscall_status = agent_task_delegate_claim(&request, claim);
		if (syscall_status == 0 &&
		    claim->status == AGENT_TASK_CHANNEL_STALE &&
		    claim->state == AGENT_TASK_DELEGATE_STATE_NONE) {
			(void)sched_yield();
			continue;
		}
		break;
	}
	valid = syscall_status == 0 &&
	      claim->version == AGENT_TASK_DELEGATE_VERSION &&
	      claim->size == sizeof(*claim) &&
	      claim->status == AGENT_TASK_CHANNEL_OK &&
	      claim->state == AGENT_TASK_DELEGATE_STATE_CLAIMED &&
	      task_lifecycle_equal(claim->lifecycle, lifecycle) &&
	      claim->descriptor.version ==
		      AGENT_TASK_DELEGATE_DESCRIPTOR_VERSION &&
	      claim->descriptor.size == sizeof(claim->descriptor) &&
	      claim->descriptor.target_pid == provider_pid &&
	      claim->descriptor.target_agent_id == (uint)provider_agent_id &&
	      claim->descriptor.target_control_id != 0 &&
	      claim->descriptor.task_id == task_id &&
	      claim->descriptor.correlation_id == correlation_id &&
	      claim->descriptor.capsule_handle != 0 &&
	      claim->owner_pid == getppid() && claim->owner_agent_id != 0 &&
	      claim->owner_control_id != 0 && claim->channel_generation != 0 &&
	      claim->request_id != 0 && claim->slot_generation != 0;
	check(valid,
	      "provider claims exact directed TASK descriptor via syscall 567");
}

static struct agent_task_delegate_complete task_delegate_completion(
	const struct agent_task_delegate_claim_result *claim, int status)
{
	struct agent_task_delegate_complete complete;

	memset(&complete, 0, sizeof(complete));
	complete.version = AGENT_TASK_DELEGATE_VERSION;
	complete.size = sizeof(complete);
	complete.lifecycle = claim->lifecycle;
	complete.owner_pid = claim->owner_pid;
	complete.terminal_status = status;
	complete.owner_control_id = claim->owner_control_id;
	complete.channel_generation = claim->channel_generation;
	complete.request_id = claim->request_id;
	complete.slot_generation = claim->slot_generation;
	complete.task_id = claim->descriptor.task_id;
	complete.correlation_id = claim->descriptor.correlation_id;
	return complete;
}

static int task_delegate_complete_result_matches(
	const struct agent_task_delegate_complete *complete,
	const struct agent_task_delegate_complete_result *result)
{
	return result->version == AGENT_TASK_DELEGATE_VERSION &&
	       result->size == sizeof(*result) &&
	       result->channel_generation == complete->channel_generation &&
	       result->request_id == complete->request_id &&
	       result->slot_generation == complete->slot_generation &&
	       result->task_id == complete->task_id &&
	       result->correlation_id == complete->correlation_id;
}

static void task_delegate_manual_context(
	struct agent_context_record *record, uint64 request_id,
	const char *payload, const char *result)
{
	memset(record, 0, sizeof(*record));
	record->tool_id = AGENT_TOOL_CONTEXT_PUSH;
	record->request_id = request_id;
	record->status = AGENT_STATUS_OK;
	strcpy(record->payload, payload);
	strcpy(record->result, result);
}

static void task_delegate_wait_for_enforcement(void)
{
	struct agent_context_record record;
	int status = AGENT_STATUS_RETRY;

	for (uint retry = 0; retry < 1024; retry++) {
		task_delegate_manual_context(
			&record, 612000ULL + retry, "delegate-probe",
			"await-enforce");
		status = context_push(&record);
		if (status == AGENT_STATUS_DENIED)
			return;
		check(status == AGENT_STATUS_OK || status == AGENT_STATUS_RETRY,
		      "provider observes only pre-enforcement or publication state");
		sleep(1);
	}
	check(0, "bounded provider wait observes contract enforcement");
}

static void task_delegate_wait_until(uint64 target_tick)
{
	struct agent_info info;

	for (uint retry = 0; retry < 8192; retry++) {
		memset(&info, 0, sizeof(info));
		check(agent_info(&info) == 0,
		      "sample delegated provider deadline tick");
		if (info.current_tick >= target_tick)
			return;
		sleep(1);
	}
	check(0, "bounded provider wait reaches delegated hard deadline");
}

static struct agent_task_delegate_complete task_delegate_cancel_request(
	struct agent_workflow_lifecycle_key lifecycle, int owner_pid,
	uint64 owner_control_id, uint64 channel_generation, uint64 request_id,
	uint64 slot_generation,
	const struct agent_task_delegate_descriptor *descriptor)
{
	struct agent_task_delegate_complete request;

	memset(&request, 0, sizeof(request));
	request.version = AGENT_TASK_DELEGATE_VERSION;
	request.size = sizeof(request);
	request.flags = AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL;
	request.lifecycle = lifecycle;
	request.owner_pid = owner_pid;
	request.terminal_status = AGENT_STATUS_CANCELLED;
	request.owner_control_id = owner_control_id;
	request.channel_generation = channel_generation;
	request.request_id = request_id;
	request.slot_generation = slot_generation;
	request.task_id = descriptor->task_id;
	request.correlation_id = descriptor->correlation_id;
	return request;
}

static void task_delegate_wait_child(int pid, const char *message)
{
	int status = 0;

	for (uint retry = 0; retry < 8192; retry++) {
		int waited = waitpid(pid, &status);

		if (waited == pid) {
			check(status == 0, message);
			return;
		}
		check(waited == -1,
		      "delegated child wait is interrupted only by Task readiness");
		(void)sched_yield();
	}
	check(0, message);
}

static void run_task_delegate_controller(
	int hello_fd, int command_fd, int signal_fd)
{
	static struct task_delegate_controller_command command;
	static struct agent_task_delegate_complete_result first, replay;
	static struct task_delegate_hello hello;
	static struct agent_info info;
	struct agent_workflow_lifecycle_key lifecycle;
	char ready = 'B';
	char signal = 0;
	uint64 signal_tick;

	lifecycle = current_lifecycle();
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 && info.is_agent == 1 &&
	      info.agent_role == TASK_DELEGATE_CONTROLLER_ROLE &&
	      info.agent_id > 0 &&
	      (info.capability_mask &
	       (AGENT_CAP_ORCHESTRATE | AGENT_CAP_WAIT_CANCEL)) ==
		      (AGENT_CAP_ORCHESTRATE | AGENT_CAP_WAIT_CANCEL),
	      "delegated cancellation controller has exact control capabilities");
	memset(&hello, 0, sizeof(hello));
	hello.pid = getpid();
	hello.agent_id = info.agent_id;
	hello.role = info.agent_role;
	hello.capability_mask = info.capability_mask;
	hello.lifecycle = lifecycle;
	check(task_pipe_write_exact(hello_fd, &hello, sizeof(hello)) == 0 &&
	      task_pipe_read_exact(command_fd, &command, sizeof(command)) == 0,
	      "controller receives its exact cancellation capability binding");
	check(command.cancel.version == AGENT_TASK_DELEGATE_VERSION &&
	      command.cancel.size == sizeof(command.cancel) &&
	      command.cancel.flags ==
		      AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL &&
	      task_lifecycle_equal(command.cancel.lifecycle, lifecycle) &&
	      command.cancel.owner_pid == getppid() &&
	      command.cancel.terminal_status == AGENT_STATUS_CANCELLED &&
	      command.cancel.owner_control_id != 0 &&
	      command.cancel.channel_generation != 0 &&
	      command.cancel.request_id != 0 &&
	      command.cancel.slot_generation != 0 &&
	      command.cancel.task_id == TASK_DELEGATE_CANCEL_TASK_ID &&
	      command.cancel.correlation_id ==
		      TASK_DELEGATE_CANCEL_CORRELATION_ID &&
	      command.cancel.ack_terminal_status == 0 &&
	      command.cancel.terminal_generation == 0,
	      "controller validates the frozen REQUEST_CANCEL shape");
	check(task_pipe_write_exact(hello_fd, &ready, 1) == 0,
	      "controller is ready to block before Contract CREATE");
	check(read(signal_fd, &signal, 1) == 1 && signal == 'C',
	      "pre-CREATE pipe reader wakes only after provider claim");
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "sample cancellation controller wake tick");
	signal_tick = info.current_tick;
	task_delegate_wait_until(
		signal_tick + TASK_DELEGATE_CONTROLLER_CANCEL_DELAY);

	memset(&first, 0, sizeof(first));
	check(agent_task_delegate_complete(&command.cancel, &first) == 0 &&
	      task_delegate_complete_result_matches(&command.cancel, &first) &&
	      first.status == AGENT_TASK_CHANNEL_OK &&
	      first.state == AGENT_TASK_DELEGATE_STATE_CLAIMED &&
	      first.terminal_status == AGENT_STATUS_CANCELLED &&
	      first.terminal_generation != 0,
	      "controller overrides denied SQ cancel with REQUEST_CANCEL");
	memset(&replay, 0, sizeof(replay));
	check(agent_task_delegate_complete(&command.cancel, &replay) == 0 &&
	      task_delegate_complete_result_matches(&command.cancel, &replay) &&
	      replay.status == AGENT_TASK_CHANNEL_OK &&
	      replay.state == first.state &&
	      replay.terminal_status == first.terminal_status &&
	      replay.terminal_generation == first.terminal_generation,
	      "exact controller cancellation receipt replay is idempotent");
	exit(0);
}

static void task_delegate_lease_worker(void *arg)
{
	struct task_delegate_lease_helper *helper = arg;
	struct agent_context_record record;
	static const char expected[] =
		TASK_DELEGATE_LEASE_HEADER TASK_DELEGATE_LEASE_PAYLOAD;
	char readback[sizeof(expected)];
	char trailing;
	uint total = sizeof(expected) - 1U;
	int fd = -1;

	task_delegate_manual_context(
		&record, 613001ULL, "delegate-helper", "lease-active");
	helper->context_status = context_push(&record);
	if (helper->context_status == AGENT_STATUS_OK)
		helper->publish_status = agent_file_publish(
			TASK_DELEGATE_LEASE_PATH, TASK_DELEGATE_LEASE_HEADER,
			sizeof(TASK_DELEGATE_LEASE_HEADER) - 1U,
			TASK_DELEGATE_LEASE_PAYLOAD,
			sizeof(TASK_DELEGATE_LEASE_PAYLOAD) - 1U);
	if (helper->publish_status == AGENT_STATUS_OK) {
		fd = open(TASK_DELEGATE_LEASE_PATH, O_RDONLY);
		helper->open_status = fd >= 0 ? AGENT_STATUS_OK : -1;
	}
	if (fd >= 0) {
		memset(readback, 0, sizeof(readback));
		helper->read_status =
			task_pipe_read_exact(fd, readback, total) == 0 &&
				bytes_equal(readback, expected, total) ?
				AGENT_STATUS_OK : -1;
		helper->eof_status = read(fd, &trailing, 1) == 0 ?
			AGENT_STATUS_OK : -1;
		helper->close_status = close(fd);
		fd = -1;
	}
	if (helper->publish_status == AGENT_STATUS_OK)
		helper->unlink_status = unlink(TASK_DELEGATE_LEASE_PATH);
	helper->done = 1;
	exit(0);
}

static void task_delegate_exercise_lease(void)
{
	struct task_delegate_lease_helper helper;
	int tid;

	memset(&helper, 0, sizeof(helper));
	helper.context_status = -1;
	helper.publish_status = -1;
	helper.open_status = -1;
	helper.read_status = -1;
	helper.eof_status = -1;
	helper.close_status = -1;
	helper.unlink_status = -1;
	tid = thread_create(task_delegate_lease_worker, &helper);
	check(tid > 0 && waittid(tid) == 0 && helper.done == 1 &&
	      helper.context_status == AGENT_STATUS_OK &&
	      helper.publish_status == AGENT_STATUS_OK &&
	      helper.open_status == AGENT_STATUS_OK &&
	      helper.read_status == AGENT_STATUS_OK &&
	      helper.eof_status == AGENT_STATUS_OK &&
	      helper.close_status == 0 && helper.unlink_status == 0,
	      "claimed provider helper exercises the exact delegated effect lease");
}

static void task_delegate_exercise_reclaimed_issuer(void)
{
	static const char payload[] = TASK_DELEGATE_RECLAIM_PAYLOAD;
	char readback[sizeof(payload)];
	char pipe_token = 'R';
	char observed = 0;
	char trailing;
	int effect_pipe[2];
	int fd;

	check(pipe(effect_pipe) == 0 &&
	      write(effect_pipe[1], &pipe_token, 1) == 1 &&
	      read(effect_pipe[0], &observed, 1) == 1 && observed == pipe_token &&
	      close(effect_pipe[0]) == 0 && close(effect_pipe[1]) == 0,
	      "strictly reclaimed issuer performs a direct pipe effect");
	task_resource_create(
		TASK_DELEGATE_RECLAIM_PATH, payload, sizeof(payload) - 1U);
	fd = open(TASK_DELEGATE_RECLAIM_PATH, O_RDONLY);
	memset(readback, 0, sizeof(readback));
	check(fd >= 0 &&
	      task_pipe_read_exact(fd, readback, sizeof(payload) - 1U) == 0 &&
	      bytes_equal(readback, payload, sizeof(payload) - 1U) &&
	      read(fd, &trailing, 1) == 0 && close(fd) == 0 &&
	      unlink(TASK_DELEGATE_RECLAIM_PATH) == 0,
	      "strictly reclaimed issuer performs a bounded direct file effect");
}

static void run_task_delegate_provider(
	int hello_fd, int cancel_signal_fd, int owner_claim_fd)
{
	static struct agent_task_delegate_claim_result normal_claim;
	static struct agent_task_delegate_claim_result cancel_claim;
	static struct agent_task_delegate_claim_result deadline_claim;
	static struct agent_task_delegate_complete normal, changed, cancel;
	static struct agent_task_delegate_complete deadline, ack;
	static struct agent_task_delegate_complete_result result, offer;
	static struct agent_context_record cleanup_context;
	static struct agent_info info;
	static struct task_delegate_hello hello;
	struct agent_workflow_lifecycle_key lifecycle;
	char cancel_signal = 'C';
	char owner_claim_signal = 'Q';
	uint64 claim_tick;
	int ack_ready = 0;

	lifecycle = current_lifecycle();
	prime_task_resource_context();
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 && info.is_agent == 1 &&
	      info.agent_role == TASK_DELEGATE_PROVIDER_ROLE &&
	      info.agent_id > 0 &&
	      (info.capability_mask &
	       (AGENT_CAP_TASK_ACCEPT | AGENT_CAP_ARTIFACT_WRITE)) ==
		      (AGENT_CAP_TASK_ACCEPT | AGENT_CAP_ARTIFACT_WRITE) &&
	      (info.capability_mask & AGENT_CAP_ORCHESTRATE) == 0,
	      "delegated artifact provider has accept/write but not orchestrate");
	memset(&hello, 0, sizeof(hello));
	hello.pid = getpid();
	hello.agent_id = info.agent_id;
	hello.role = info.agent_role;
	hello.capability_mask = info.capability_mask;
	hello.lifecycle = lifecycle;
	check(task_pipe_write_exact(hello_fd, &hello, sizeof(hello)) == 0,
	      "publish pre-enforcement provider identity hello");

	/* A role capability is insufficient without the exact CLAIMED lease. */
	task_delegate_wait_for_enforcement();
	check(agent_file_publish(
		      TASK_DELEGATE_LEASE_PATH, TASK_DELEGATE_LEASE_HEADER,
		      sizeof(TASK_DELEGATE_LEASE_HEADER) - 1U,
		      TASK_DELEGATE_LEASE_PAYLOAD,
		      sizeof(TASK_DELEGATE_LEASE_PAYLOAD) - 1U) ==
		      AGENT_STATUS_DENIED,
	      "artifact provider publish is denied before delegated claim");

	task_delegate_claim_one(
		lifecycle, getpid(), info.agent_id, TASK_DELEGATE_NORMAL_TASK_ID,
		TASK_DELEGATE_NORMAL_CORRELATION_ID, &normal_claim);
	task_delegate_exercise_lease();
	normal = task_delegate_completion(&normal_claim, AGENT_STATUS_OK);
	memset(&result, 0, sizeof(result));
	check(agent_task_delegate_complete(&normal, &result) == 0 &&
	      task_delegate_complete_result_matches(&normal, &result) &&
	      result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
	      result.terminal_status == AGENT_STATUS_OK &&
	      result.terminal_generation == 0,
	      "provider completes normal delegated Task via syscall 568");

	/* The cancel contract is submitted only after the normal result is receipted. */
	task_delegate_claim_one(
		lifecycle, getpid(), info.agent_id,
		TASK_DELEGATE_CANCEL_TASK_ID,
		TASK_DELEGATE_CANCEL_CORRELATION_ID, &cancel_claim);
	memset(&result, 0, sizeof(result));
	check(agent_task_delegate_complete(&normal, &result) == 0 &&
	      task_delegate_complete_result_matches(&normal, &result) &&
	      result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
	      result.terminal_status == AGENT_STATUS_OK &&
	      result.terminal_generation == 0,
	      "exact delegated completion receipt replay is idempotent");
	changed = normal;
	changed.terminal_status = AGENT_STATUS_BAD_REQUEST;
	memset(&result, 0, sizeof(result));
	check(agent_task_delegate_complete(&changed, &result) == 0 &&
	      task_delegate_complete_result_matches(&changed, &result) &&
	      result.status == AGENT_TASK_CHANNEL_STALE &&
	      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
	      result.terminal_status == AGENT_STATUS_OK &&
	      result.terminal_generation == 0,
	      "changed delegated completion receipt replay is stale");

	task_delegate_manual_context(
		&cleanup_context, 613501ULL, "cancel-result", "cleanup-required");
	check(context_push(&cleanup_context) == AGENT_STATUS_OK,
	      "cancelled provider mutates Context while lease is active");
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 && info.context_path_latest != 0 &&
	      write(cancel_signal_fd, &cancel_signal, 1) == 1 &&
	      write(owner_claim_fd, &owner_claim_signal, 1) == 1,
	      "claimed provider signals controller and owner cancellation readers");
	claim_tick = info.current_tick;
	task_delegate_wait_until(
		claim_tick + TASK_DELEGATE_PROVIDER_CANCEL_DELAY);

	cancel = task_delegate_completion(&cancel_claim, AGENT_STATUS_OK);
	memset(&offer, 0, sizeof(offer));
	check(agent_task_delegate_complete(&cancel, &offer) == 0 &&
	      task_delegate_complete_result_matches(&cancel, &offer) &&
	      offer.status == AGENT_TASK_CHANNEL_RETRY &&
	      offer.state == AGENT_TASK_DELEGATE_STATE_CLAIMED &&
	      offer.terminal_status == AGENT_STATUS_CANCELLED &&
	      offer.terminal_generation != 0,
	      "provider observes controller CANCELLED offer on business completion");
	check(context_clear() == AGENT_STATUS_OK,
	      "provider cleans cancelled result Context before terminal ACK");
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 && info.context_path_latest == 0,
	      "cancel cleanup leaves no provider result Context to publish");

	ack = cancel;
	ack.flags = AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL;
	ack.ack_terminal_status = offer.terminal_status;
	ack.terminal_generation = offer.terminal_generation;
	memset(&result, 0, sizeof(result));
	check(agent_task_delegate_complete(&ack, &result) == 0 &&
	      task_delegate_complete_result_matches(&ack, &result) &&
	      result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
	      result.terminal_status == AGENT_STATUS_CANCELLED &&
	      result.terminal_generation == offer.terminal_generation,
	      "provider cleanup ACK echoes exact CANCELLED offer");
	memset(&result, 0, sizeof(result));
	check(agent_task_delegate_complete(&ack, &result) == 0 &&
	      task_delegate_complete_result_matches(&ack, &result) &&
	      result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
	      result.terminal_status == AGENT_STATUS_CANCELLED &&
	      result.terminal_generation == ack.terminal_generation,
	      "exact cancelled terminal ACK replay is idempotent");
	memset(&result, 0, sizeof(result));
	check(agent_task_delegate_complete(&cancel, &result) == 0 &&
	      task_delegate_complete_result_matches(&cancel, &result) &&
	      result.status == AGENT_TASK_CHANNEL_STALE &&
	      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
	      result.terminal_status == AGENT_STATUS_CANCELLED &&
	      result.terminal_generation == ack.terminal_generation,
	      "late business completion after cancel ACK is stale");

	/* The deadline contract follows the fully receipted cancellation. */
	task_delegate_claim_one(
		lifecycle, getpid(), info.agent_id,
		TASK_DELEGATE_DEADLINE_TASK_ID,
		TASK_DELEGATE_DEADLINE_CORRELATION_ID, &deadline_claim);

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "sample claimed deadline Task tick");
	claim_tick = info.current_tick;
	task_delegate_manual_context(
		&cleanup_context, 614001ULL, "deadline-result", "cleanup-required");
	check(context_push(&cleanup_context) == AGENT_STATUS_OK,
	      "deadline provider mutates Context while lease is active");
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 && info.context_path_latest != 0,
	      "deadline provider has result Context before cleanup");
	task_delegate_wait_until(
		claim_tick + TASK_DELEGATE_DEADLINE_SLACK + 32ULL);

	deadline = task_delegate_completion(&deadline_claim, AGENT_STATUS_OK);
	memset(&offer, 0, sizeof(offer));
	check(agent_task_delegate_complete(&deadline, &offer) == 0 &&
	      task_delegate_complete_result_matches(&deadline, &offer) &&
	      offer.status == AGENT_TASK_CHANNEL_RETRY &&
	      offer.state == AGENT_TASK_DELEGATE_STATE_CLAIMED &&
	      offer.terminal_status == AGENT_STATUS_TIMEOUT &&
	      offer.terminal_generation != 0,
	      "due claimed Task returns authoritative terminal offer");
	check(context_clear() == AGENT_STATUS_OK,
	      "provider cleans deadline result Context before terminal ACK");
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 && info.context_path_latest == 0,
	      "provider cleanup leaves no result Context to publish");

	for (uint retry = 0; retry < 4; retry++) {
		ack = deadline;
		ack.flags = AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL;
		ack.ack_terminal_status = offer.terminal_status;
		ack.terminal_generation = offer.terminal_generation;
		memset(&result, 0, sizeof(result));
		check(agent_task_delegate_complete(&ack, &result) == 0 &&
		      task_delegate_complete_result_matches(&ack, &result),
		      "provider sends framed terminal cleanup ACK");
		if (result.status == AGENT_TASK_CHANNEL_RETRY) {
			check(result.state == AGENT_TASK_DELEGATE_STATE_CLAIMED &&
			      result.terminal_status == AGENT_STATUS_TIMEOUT &&
			      result.terminal_generation > offer.terminal_generation,
			      "provider observes only a newer authoritative offer");
			offer = result;
			continue;
		}
		check(result.status == AGENT_TASK_CHANNEL_OK &&
		      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
		      result.terminal_status == offer.terminal_status &&
		      result.terminal_generation == offer.terminal_generation,
		      "provider cleanup ACK echoes exact terminal offer");
		ack_ready = 1;
		break;
	}
	check(ack_ready, "bounded provider terminal ACK reaches READY");

	memset(&result, 0, sizeof(result));
	check(agent_task_delegate_complete(&ack, &result) == 0 &&
	      task_delegate_complete_result_matches(&ack, &result) &&
	      result.status == AGENT_TASK_CHANNEL_OK &&
	      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
	      result.terminal_status == AGENT_STATUS_TIMEOUT &&
	      result.terminal_generation == ack.terminal_generation,
	      "exact terminal ACK replay is idempotent before owner pump");
	memset(&result, 0, sizeof(result));
	check(agent_task_delegate_complete(&deadline, &result) == 0 &&
	      task_delegate_complete_result_matches(&deadline, &result) &&
	      result.status == AGENT_TASK_CHANNEL_STALE &&
	      result.state == AGENT_TASK_DELEGATE_STATE_READY &&
	      result.terminal_status == AGENT_STATUS_TIMEOUT &&
	      result.terminal_generation == ack.terminal_generation,
	      "late normal completion after terminal ACK is stale");
	exit(0);
}

static void exercise_delegated_task_channel(
	struct agent_workflow_lifecycle_key lifecycle)
{
	static struct agent_execution_contract_node normal_node, cancel_node;
	static struct agent_execution_contract_node deadline_node;
	static struct agent_execution_contract_key normal_key, cancel_key;
	static struct agent_execution_contract_key deadline_key;
	static struct agent_execution_contract_key setup_key;
	static struct agent_task_delegate_descriptor normal_descriptor;
	static struct agent_task_delegate_descriptor cancel_descriptor;
	static struct agent_task_delegate_descriptor deadline_descriptor;
	static struct agent_task_resource_handle normal_resource;
	static struct agent_task_resource_handle cancel_resource;
	static struct agent_task_resource_handle deadline_resource;
	static struct task_delegate_hello provider_hello, controller_hello;
	static struct task_delegate_controller_command controller_command;
	static struct task_channel_view view;
	static struct agent_task_channel_enter_result cancel_enter, final_result;
	static struct agent_task_sqe normal_sqe, cancel_sqe, owner_cancel_sqe;
	static struct agent_task_sqe deadline_sqe;
	static struct agent_info owner_info, tick_info;
	uint64 owner_control_id = 0;
	uint64 provider_control_id = 0;
	uint64 controller_control_id = 0;
	uint64 cancel_request_id;
	uint64 deadline_tick;
	int provider_hello_pipe[2];
	int controller_hello_pipe[2];
	int controller_command_pipe[2];
	int cancel_signal_pipe[2];
	int owner_claim_pipe[2];
	int provider_pid;
	int controller_pid;
	int cancel_descriptor_fd;
	char controller_ready = 0;
	char denied_signal = 'O';
	char owner_claim_signal = 0;

	memset(&owner_info, 0, sizeof(owner_info));
	check(agent_info(&owner_info) == 0 && owner_info.is_agent == 1 &&
	      owner_info.agent_role == AGENT_ROLE_ORCHESTRATOR &&
	      (owner_info.capability_mask &
	       (AGENT_CAP_ORCHESTRATE | AGENT_CAP_ROUTE_MANAGE)) ==
		      (AGENT_CAP_ORCHESTRATE | AGENT_CAP_ROUTE_MANAGE),
	      "delegated Task owner capabilities");
	for (uint retry = 0; retry < 128 && owner_control_id == 0; retry++) {
		(void)task_delegate_identity_lookup(
			getpid(), owner_info.agent_id, owner_info.agent_role,
			&owner_control_id);
		if (owner_control_id == 0)
			(void)sched_yield();
	}
	check(owner_control_id != 0,
	      "resolve owner control identity for cancellation binding");
	check(pipe(provider_hello_pipe) == 0 && pipe(cancel_signal_pipe) == 0 &&
	      pipe(owner_claim_pipe) == 0,
	      "create bounded delegated Task provider hello pipe");
	check(agent_scope_delegate_fd(provider_hello_pipe[1]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(cancel_signal_pipe[1]) == AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(owner_claim_pipe[1]) == AGENT_STATUS_OK,
	      "delegate provider hello and cancellation signal endpoints");
	provider_pid = agent_create_role(TASK_DELEGATE_PROVIDER_ROLE);
	check(provider_pid >= 0, "create delegated artifact provider Agent");
	if (provider_pid == 0)
		run_task_delegate_provider(
			provider_hello_pipe[1], cancel_signal_pipe[1],
			owner_claim_pipe[1]);
	check(close(provider_hello_pipe[1]) == 0 &&
	      close(owner_claim_pipe[1]) == 0 &&
	      task_pipe_read_exact(provider_hello_pipe[0], &provider_hello,
				   sizeof(provider_hello)) == 0 &&
	      close(provider_hello_pipe[0]) == 0,
	      "read and close pre-enforcement provider hello");
	check(provider_hello.pid == provider_pid && provider_hello.agent_id > 0 &&
	      provider_hello.role == TASK_DELEGATE_PROVIDER_ROLE &&
	      task_lifecycle_equal(provider_hello.lifecycle, lifecycle) &&
	      (provider_hello.capability_mask &
	       (AGENT_CAP_TASK_ACCEPT | AGENT_CAP_ARTIFACT_WRITE)) ==
		      (AGENT_CAP_TASK_ACCEPT | AGENT_CAP_ARTIFACT_WRITE) &&
	      (provider_hello.capability_mask & AGENT_CAP_ORCHESTRATE) == 0,
	      "provider preserves the delegated Task capability split");
	check(agent_route_config(getpid(), provider_pid, AGENT_IPC_ROUTE_TASK,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant directed TASK route");
	for (uint retry = 0; retry < 128 && provider_control_id == 0; retry++) {
		(void)task_delegate_identity_lookup(
			provider_pid, provider_hello.agent_id, provider_hello.role,
			&provider_control_id);
		if (provider_control_id == 0)
			(void)sched_yield();
	}
	check(provider_control_id != 0,
	      "resolve provider control identity from kernel audit");

	prime_task_resource_context();
	memset(&setup_key, 0, sizeof(setup_key));
	setup_key.lifecycle = lifecycle;
	setup_task_channel(&setup_key, &view);
	(void)unlink(TASK_DELEGATE_LEASE_PATH);
	(void)unlink(TASK_DELEGATE_RECLAIM_PATH);
	task_delegate_descriptor_init(
		&normal_descriptor, provider_pid, (uint)provider_hello.agent_id,
		provider_control_id, 1U, TASK_DELEGATE_NORMAL_TASK_ID,
		TASK_DELEGATE_NORMAL_CORRELATION_ID);
	normal_resource = task_delegate_import_descriptor(
		&view, TASK_DELEGATE_NORMAL_PATH, &normal_descriptor);
	check(unlink(TASK_DELEGATE_NORMAL_PATH) == 0,
	      "unlink normal descriptor source before contract enforcement");

	normal_key = create_delegate_contract(lifecycle, &normal_node);
	normal_sqe = task_delegate_submit(
		&view, &normal_key, &normal_node, 0, normal_resource, 0, 0, 0);
	(void)task_delegate_wait_owner_cqe(
		&view, &normal_sqe, AGENT_STATUS_OK, 0,
		AGENT_EXECUTION_REASON_NONE, provider_pid,
		provider_hello.agent_id,
		provider_control_id, 0);
	task_delegate_release_descriptor(&view, normal_resource);
	retire_delegate_contract(&normal_key);
	task_delegate_exercise_reclaimed_issuer();

	check(pipe(controller_hello_pipe) == 0 &&
	      pipe(controller_command_pipe) == 0,
	      "create bounded cancellation controller pipes");
	check(agent_scope_delegate_fd(controller_hello_pipe[1]) ==
		      AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(controller_command_pipe[0]) ==
		      AGENT_STATUS_OK &&
	      agent_scope_delegate_fd(cancel_signal_pipe[0]) == AGENT_STATUS_OK,
	      "delegate controller command, hello, and blocking reader endpoints");
	controller_pid = agent_create_role(TASK_DELEGATE_CONTROLLER_ROLE);
	check(controller_pid >= 0,
	      "create separate delegated cancellation controller Agent");
	if (controller_pid == 0)
		run_task_delegate_controller(
			controller_hello_pipe[1], controller_command_pipe[0],
			cancel_signal_pipe[0]);
	check(close(controller_hello_pipe[1]) == 0 &&
	      close(controller_command_pipe[0]) == 0 &&
	      task_pipe_read_exact(controller_hello_pipe[0], &controller_hello,
				   sizeof(controller_hello)) == 0,
	      "read cancellation controller identity hello");
	check(controller_hello.pid == controller_pid &&
	      controller_hello.agent_id > 0 &&
	      controller_hello.role == TASK_DELEGATE_CONTROLLER_ROLE &&
	      task_lifecycle_equal(controller_hello.lifecycle, lifecycle) &&
	      (controller_hello.capability_mask &
	       (AGENT_CAP_ORCHESTRATE | AGENT_CAP_WAIT_CANCEL)) ==
		      (AGENT_CAP_ORCHESTRATE | AGENT_CAP_WAIT_CANCEL),
	      "third Agent is the same-lifecycle cancellation controller");
	for (uint retry = 0; retry < 128 && controller_control_id == 0; retry++) {
		(void)task_delegate_identity_lookup(
			controller_pid, controller_hello.agent_id,
			controller_hello.role, &controller_control_id);
		if (controller_control_id == 0)
			(void)sched_yield();
	}
	check(controller_control_id != 0 &&
	      agent_route_config(controller_pid, getpid(), AGENT_IPC_ROUTE_TASK,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant controller-to-owner TASK cancellation route");
	task_delegate_descriptor_init(
		&cancel_descriptor, provider_pid, (uint)provider_hello.agent_id,
		provider_control_id, 2U, TASK_DELEGATE_CANCEL_TASK_ID,
		TASK_DELEGATE_CANCEL_CORRELATION_ID);
	cancel_descriptor_fd = task_delegate_open_descriptor(
		TASK_DELEGATE_CANCEL_PATH, &cancel_descriptor);
	cancel_request_id = ++next_request_id;
	memset(&controller_command, 0, sizeof(controller_command));
	controller_command.cancel = task_delegate_cancel_request(
		lifecycle, getpid(), owner_control_id, view.generation,
		cancel_request_id,
		view.sq_tail / AGENT_TASK_CHANNEL_CAPACITY + 1,
		&cancel_descriptor);
	check(task_pipe_write_exact(
		      controller_command_pipe[1], &controller_command,
		      sizeof(controller_command)) == 0 &&
	      close(controller_command_pipe[1]) == 0 &&
	      task_pipe_read_exact(
		      controller_hello_pipe[0], &controller_ready, 1) == 0 &&
	      controller_ready == 'B' && close(controller_hello_pipe[0]) == 0,
	      "controller receives binding and begins its pre-CREATE pipe read");
	memset(&tick_info, 0, sizeof(tick_info));
	check(agent_info(&tick_info) == 0,
	      "sample pre-CREATE controller blocking tick");
	task_delegate_wait_until(tick_info.current_tick + 2ULL);

	cancel_key = create_delegate_contract(lifecycle, &cancel_node);
	check(cancel_key.generation > normal_key.generation,
	      "blocked pipe reader permits the next Contract generation");
	check(write(cancel_signal_pipe[1], &denied_signal, 1) ==
		      AGENT_STATUS_DENIED,
	      "owner pipe write is denied while Contract enforcement is active");
	cancel_resource = task_delegate_import_descriptor_fd(
		&view, cancel_descriptor_fd, &cancel_descriptor);
	task_delegate_read_descriptor_fd(
		cancel_descriptor_fd, &cancel_descriptor);
	cancel_sqe = task_delegate_submit(
		&view, &cancel_key, &cancel_node, 0, cancel_resource, 0, 0,
		cancel_request_id);
	check(read(owner_claim_pipe[0], &owner_claim_signal, 1) == 1 &&
	      owner_claim_signal == 'Q',
	      "owner observes exact provider CLAIMED effect boundary");
	owner_cancel_sqe = make_cancel_sqe(
		&cancel_sqe, ++next_request_id, view.generation, view.sq_tail);
	write_sqe(view.sq, view.sq_tail, &owner_cancel_sqe);
	view.sq_tail++;
	enter_channel(0, 1, 0, view.generation, view.sq_tail, view.cq_head,
		      &cancel_enter);
	check(cancel_enter.status == AGENT_TASK_CHANNEL_DENIED &&
	      cancel_enter.submitted == 1 &&
	      cancel_enter.sq_head == view.sq_tail &&
	      cancel_enter.cq_tail == view.cq_head &&
	      cancel_enter.in_flight == 1,
	      "owner SQ CANCEL is denied after provider effect claim");
	memset(&tick_info, 0, sizeof(tick_info));
	check(agent_info(&tick_info) == 0,
	      "sample controller override settlement tick");
	task_delegate_wait_until(
		tick_info.current_tick + TASK_DELEGATE_PROVIDER_CANCEL_DELAY + 32ULL);
	(void)task_delegate_wait_owner_cqe(
		&view, &cancel_sqe, AGENT_STATUS_CANCELLED,
		AGENT_TASK_CQE_F_CANCELLED,
		AGENT_EXECUTION_REASON_CANCEL_REQUESTED, provider_pid,
		provider_hello.agent_id, provider_control_id, 1);
	task_delegate_release_descriptor(&view, cancel_resource);
	retire_delegate_contract(&cancel_key);
	check(close(cancel_descriptor_fd) == 0 &&
	      unlink(TASK_DELEGATE_CANCEL_PATH) == 0 &&
	      close(owner_claim_pipe[0]) == 0 &&
	      close(cancel_signal_pipe[0]) == 0 &&
	      close(cancel_signal_pipe[1]) == 0,
	      "reclaimed cancel contract releases inode and pipe endpoints");
	task_delegate_wait_child(
		controller_pid,
		"controller exits after exact cancellation receipt replay");

	task_delegate_descriptor_init(
		&deadline_descriptor, provider_pid, (uint)provider_hello.agent_id,
		provider_control_id, 3U, TASK_DELEGATE_DEADLINE_TASK_ID,
		TASK_DELEGATE_DEADLINE_CORRELATION_ID);
	deadline_resource = task_delegate_import_descriptor(
		&view, TASK_DELEGATE_DEADLINE_PATH, &deadline_descriptor);
	check(unlink(TASK_DELEGATE_DEADLINE_PATH) == 0,
	      "unlink deadline descriptor source before contract enforcement");
	deadline_key = create_delegate_contract(lifecycle, &deadline_node);
	check(deadline_key.generation > cancel_key.generation,
	      "third delegated contract advances the reclaimed generation");

	memset(&tick_info, 0, sizeof(tick_info));
	check(agent_info(&tick_info) == 0,
	      "sample delegated hard-deadline start tick");
	deadline_tick = tick_info.current_tick + TASK_DELEGATE_DEADLINE_SLACK;
	deadline_sqe = task_delegate_submit(
		&view, &deadline_key, &deadline_node, 0, deadline_resource,
		AGENT_TASK_SQE_F_HARD_DEADLINE, deadline_tick, 0);
	task_delegate_wait_child(
		provider_pid,
		"provider validates receipt replay and deadline ACK before owner pump");
	(void)task_delegate_wait_owner_cqe(
		&view, &deadline_sqe, AGENT_STATUS_TIMEOUT,
		AGENT_TASK_CQE_F_DEADLINE,
		AGENT_EXECUTION_REASON_DEADLINE_EXPIRED, provider_pid,
		provider_hello.agent_id, provider_control_id, 1);

	task_delegate_release_descriptor(&view, deadline_resource);
	retire_delegate_contract(&deadline_key);
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0,
		      view.generation, view.sq_tail, view.cq_head, &final_result);
	check(final_result.status == AGENT_TASK_CHANNEL_OK &&
	      final_result.sq_head == view.sq_tail &&
	      final_result.cq_head == view.cq_head &&
	      final_result.cq_tail == view.cq_head &&
	      final_result.in_flight == 0 &&
	      final_result.terminal_pending == 0 &&
	      final_result.resource_count == 0,
	      "delegated Task channel is fully drained with no replay CQE");
}

#define CHILD_BATCH       1
#define CHILD_SCALAR      2
#define CHILD_TASK_PERF   3
#define CHILD_TASK_STRESS 4
#define CHILD_TASK_RESOURCE 5
#define CHILD_TASK_DELEGATE 6

static int create_isolated_workflow(void)
{
	for (int attempt = 0; attempt < 2000; attempt++) {
		int pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);

		if (pid >= 0)
			return pid;
		sleep(1);
	}
	return -1;
}

static void run_child(int mode)
{
	struct agent_execution_contract_key key;
	struct task_resource_inputs resource_inputs;
	struct task_channel_view resource_view;
	struct agent_workflow_lifecycle_key lifecycle;
	int pid;
	int status = 0;

	pid = create_isolated_workflow();
	check(pid >= 0, "create isolated workflow orchestrator");
	if (pid == 0) {
		if (mode == CHILD_BATCH) {
			(void)current_lifecycle();
			run_batch_ablation();
		} else if (mode == CHILD_SCALAR) {
			key = create_task_contract(current_lifecycle());
			run_scalar_ablation(&key);
		} else if (mode == CHILD_TASK_PERF) {
			key = create_task_contract(current_lifecycle());
			run_task_ablation(&key);
		} else if (mode == CHILD_TASK_STRESS) {
			key = create_task_contract(current_lifecycle());
			exercise_task_channel(&key);
		} else if (mode == CHILD_TASK_RESOURCE) {
			prepare_task_resource_inputs(&resource_inputs);
			lifecycle = current_lifecycle();
			prime_task_resource_context();
			memset(&key, 0, sizeof(key));
			key.lifecycle = lifecycle;
			setup_task_channel(&key, &resource_view);
			exercise_task_resource_close_race(
				&resource_view, &resource_inputs);
			key = create_task_contract(lifecycle);
			exercise_task_resources(
				&key, &resource_inputs, &resource_view);
		} else if (mode == CHILD_TASK_DELEGATE) {
			lifecycle = current_lifecycle();
			exercise_delegated_task_channel(lifecycle);
		} else {
			check(0, "known isolated orchestrator mode");
		}
		exit(0);
	}
	check(waitpid(pid, &status) == pid && status == 0,
	      "wait isolated orchestrator");
}

int main(void)
{
	printf("agenttask_ucore: asynchronous typed Task Channel vertical test\n");
	printf("agenttask_ucore: perf_contract=steady_state_n16 "
	       "quantiles=nearest_rank "
	       "sample_semantics=pre_effect_context_service_start "
	       "interval_origin=sequence_start_boundary "
	       "service_metric=service_start_tick_intervals "
	       "sequence_metric=agent_info_boundary_elapsed_ticks "
	       "wall_clock=unavailable raw_cycles=not_claimed "
	       "syscall_source=guest_call_sites\n");
	printf("agenttask_ucore: perf_observers=agent_info:2 "
	       "boundary_overhead=start_return+end_entry_included "
	       "context_query:16 post_sequence_excluded=1 "
	       "kernel_path_syscall_counter=unavailable\n");
	printf("agenttask_ucore: perf_excluded batch=lifecycle_info:1 "
	       "scalar_v3=lifecycle_info:1+contract:2 "
	       "sq_cq=lifecycle_info:1+contract:2+channel_setup:1\n");
	printf("agenttask_ucore: sq_cq_copy_scope=sqe_private_copy+cqe_publish "
	       "ack_clear_bytes=2048 user_ring_descriptor_bytes=4096 "
	       "setup_abi_control_bytes=160 setup_copied_control_bytes=256\n");
	printf("agenttask_ucore: provider=synchronous_echo "
	       "running_cancel_latency=unavailable "
	       "terminal_pending_saturation=unavailable\n");
	run_child(CHILD_BATCH);
	run_child(CHILD_SCALAR);
	run_child(CHILD_TASK_PERF);
	run_child(CHILD_TASK_STRESS);
	run_child(CHILD_TASK_RESOURCE);
	run_child(CHILD_TASK_DELEGATE);
	printf("agenttask_ucore: delegated_runtime agents=3 provider=artifact "
	       "controller=orchestrator task_route=1 task_accept=1 "
	       "artifact_write=1 descriptor_bytes=128 claim567=1 complete568=1\n");
	printf("agenttask_ucore: delegated_contracts=3 strict_reclaimed=1 "
	       "reclaimed_generation_advance=1 issuer_gap_effects=pipe+file\n");
	printf("agenttask_ucore: delegated_lease preclaim_publish_denied=1 "
	       "thread_helper=1 context_mutation=1 bounded_publish_read=1 "
	       "effect_gates=process+metadata+file+artifact\n");
	printf("agenttask_ucore: delegated_normal=1 receipt_replay=1 "
	       "changed_replay_stale=1 sole_owner_cqe=1 output_none=1\n");
	printf("agenttask_ucore: delegated_cancel_after_claim=1 agents=3 "
	       "controller=orchestrator owner_sq_cancel_denied=1 "
	       "request_cancel568=1 cancelled_offer=1 cleanup_ack=1 "
	       "cancel_receipt_replay=1 late_complete_stale=1 "
	       "sole_owner_cqe=1\n");
	printf("agenttask_ucore: contract_create_blocked_pipe_reader=1 "
	       "enforce_pipe_write_denied=1 regular_inode_import_read_cut=1\n");
	printf("agenttask_ucore: delegated_deadline_claimed=1 "
	       "terminal_offer_timeout=1 cleanup_ack=1 ack_replay=1 "
	       "late_complete_stale=1 sole_owner_cqe=1\n");
	printf("agenttask_ucore: setup=1 single_issuer=1 "
	       "resource_utf8_snapshot=1 borrowed_live=1 owned_consumed=1 "
	       "release_stale=1 generation_aba=1\n");
	printf("agenttask_ucore: submit=1 cq_ack=1 monotonic=1 resync=1\n");
	printf("agenttask_ucore: target_cancel_exactly_once=1 hard_deadline=1\n");
	printf("agenttask_ucore: batch_fp=%u scalar_v3_fp=%u task_fp=%u\n",
	       SEMANTIC_EXPECTED, SEMANTIC_EXPECTED, SEMANTIC_EXPECTED);
	printf("agenttask_ucore: parent passed\n");
	return 0;
}
