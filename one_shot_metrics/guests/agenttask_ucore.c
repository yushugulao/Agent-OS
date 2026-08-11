#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TEST_NAME "agenttask_ucore"
#define TASK_NODE_COUNT 21U
#define CHARGE_RESERVED 1U
#define PERF_OPERATION_COUNT 16U
#define PERF_REQUEST_BASE 91000ULL
#define ECHO_PARAM_COUNT 3U
#define TOOL_V3_DISPATCH_HEADER_BYTES (2U * sizeof(unsigned int))
#define FIGURE_ROUNDS 8U
#define FIGURE_PATH_COUNT 3U

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

struct ablation_metrics {
	uint64 start_us;
	uint64 end_us;
	uint64 sequence_elapsed_us;
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
static uint figure_round;
static uint figure_order_slot;

_Static_assert(PERF_OPERATION_COUNT == AGENT_TASK_CHANNEL_CAPACITY,
	       "performance sequence must exactly fill one CQ generation");
_Static_assert(sizeof(struct agent_op) == 104,
	       "legacy batch operation ABI size");
_Static_assert(sizeof(struct agent_result) == 120,
	       "legacy batch result ABI size");

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

static uint64 figure_now_us(void)
{
	TimeVal now;

	check(sys_get_time(&now, 0) == 0,
	      "sample performance sequence microsecond clock");
	return now.sec * 1000000ULL + now.usec;
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
	metrics->start_us = figure_now_us();
}

static void ablation_end(struct ablation_metrics *metrics)
{
	struct agent_info info;

	metrics->end_us = figure_now_us();
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 &&
	      info.current_tick >= metrics->start_tick &&
	      info.sched_dispatch_count >= metrics->start_dispatch_count &&
	      metrics->end_us >= metrics->start_us,
	      "sample monotonic performance sequence end counters");
	metrics->sequence_elapsed_us = metrics->end_us - metrics->start_us;
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
	printf("agenttask_ucore: one_shot_sequence schema=1 boot_round=%u "
	       "order=%u path=%s operations=%u syscalls=%u "
	       "start_us=%llu end_us=%llu "
	       "duration_us=%llu service_start_span_ticks=%llu "
	       "sequence_elapsed_ticks=%llu sched_dispatch_delta=%llu "
	       "status=measured\n",
	       figure_round, figure_order_slot, path, PERF_OPERATION_COUNT, syscalls,
	       metrics->start_us, metrics->end_us, metrics->sequence_elapsed_us,
	       metrics->service_start_span_ticks,
	       metrics->sequence_elapsed_ticks, metrics->dispatch_delta);
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++)
		printf("agenttask_ucore: one_shot_op schema=1 boot_round=%u "
		       "order=%u path=%s operation_index=%u "
		       "service_start_interval_tick=%llu "
		       "status=measured\n",
		       figure_round, figure_order_slot, path, i,
		       metrics->service_start_tick_intervals[i]);
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
		batch_ops[i].version = AGENT_CALL_VERSION;
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
	node->input_artifact_type = AGENT_ARTIFACT_NONE;
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
	      "create enforced null-resource Task contract");

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
		      queried_nodes[i].input_artifact_type == AGENT_ARTIFACT_NONE &&
		      queried_nodes[i].output_artifact_type == AGENT_ARTIFACT_NONE,
		      "query preserves typed null nodes and canonical schema");
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

static void check_resource_import_denied(uint64 generation)
{
	struct agent_task_channel_resource resource;
	struct agent_task_channel_resource_result result;

	memset(&resource, 0, sizeof(resource));
	memset(&result, 0, sizeof(result));
	resource.version = AGENT_TASK_CHANNEL_VERSION;
	resource.size = sizeof(resource);
	resource.operation = AGENT_TASK_RESOURCE_IMPORT;
	resource.resource_type = AGENT_ARTIFACT_BYTES;
	resource.resource_flags = AGENT_TASK_HANDLE_F_OWNED;
	resource.source_handle = 1;
	resource.length = 1;
	resource.channel_generation = generation;
	check(agent_task_channel_resource(&resource, &result) == 0 &&
	      result.status == AGENT_TASK_CHANNEL_DENIED &&
	      result.state == AGENT_TASK_RESOURCE_STATE_NONE &&
	      handle_is_null(result.handle),
	      "RESOURCE_IMPORT fails closed without an external backend");
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
	check_resource_import_denied(view.generation);
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

#define CHILD_BATCH       1
#define CHILD_SCALAR      2
#define CHILD_TASK_PERF   3
#define CHILD_TASK_STRESS 4

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

static void run_child(int mode, uint round, uint order_slot)
{
	struct agent_execution_contract_key key;
	int pid;
	int status = 0;

	figure_round = round;
	figure_order_slot = order_slot;
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
	for (uint round = 0; round < FIGURE_ROUNDS; round++) {
		const int modes[FIGURE_PATH_COUNT] = {
			CHILD_BATCH, CHILD_SCALAR, CHILD_TASK_PERF
		};

		for (uint slot = 0; slot < FIGURE_PATH_COUNT; slot++)
			run_child(modes[(round + slot) % FIGURE_PATH_COUNT],
				  round + 1U, slot);
	}
	run_child(CHILD_TASK_STRESS, 0, 0);
	printf("agenttask_ucore: setup=1 single_issuer=1 resource_import_denied=1\n");
	printf("agenttask_ucore: submit=1 cq_ack=1 monotonic=1 resync=1\n");
	printf("agenttask_ucore: target_cancel_exactly_once=1 hard_deadline=1\n");
	printf("agenttask_ucore: batch_fp=%u scalar_v3_fp=%u task_fp=%u\n",
	       SEMANTIC_EXPECTED, SEMANTIC_EXPECTED, SEMANTIC_EXPECTED);
	printf("agenttask_ucore: parent passed\n");
	return 0;
}
