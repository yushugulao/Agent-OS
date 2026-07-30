#include <agent.h>
#include <research_platform_state.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_info orch_info;
static struct agent_op orch_op;
static struct agent_result orch_result;
static struct agent_op orch_echo_op;
static struct agent_op orch_followup_op;
static struct agent_result orch_echo_result;
static struct agent_result orch_followup_result;
static struct agent_context_header orch_header;
static struct agent_context_record orch_records[4];
static struct agent_timeline_record orch_timeline_records[4];
static struct agent_provenance_edge orch_provenance_edges[4];
static struct agent_ledger_summary orch_ledger;
static struct agent_file_query orch_file_query;
static struct agent_file_query_result orch_file_query_result;
static struct agent_file_hit orch_metadata_hit;
static struct agent_file_prefetch_hint orch_prefetch_hints[AGENT_FILE_PREFETCH_MAX_HINTS];
static char orch_acceptance_body[2048];

#define RP_WORKFLOW_HANDOFF_MAGIC 0x52505754U
#define RP_WORKFLOW_COMPLETION_MAGIC 0x52505743U
#define RP_WORKFLOW_HANDOFF_VERSION 1U
#define RP_WORKFLOW_HANDOFF_PREFIX "--rp-workflow-timing-fd="
#define RP_WORKFLOW_COMPLETION_PREFIX "--rp-workflow-completion-fd="
#define RP_WORKFLOW_PHASE_AGENT_CREATE   (1ULL << 0)
#define RP_WORKFLOW_PHASE_AGENT_INFO     (1ULL << 1)
#define RP_WORKFLOW_PHASE_METADATA       (1ULL << 2)
#define RP_WORKFLOW_PHASE_TOOL           (1ULL << 3)
#define RP_WORKFLOW_PHASE_CONTEXT        (1ULL << 4)
#define RP_WORKFLOW_PHASE_OBSERVATION    (1ULL << 5)
#define RP_WORKFLOW_PHASE_FILE_QUERY     (1ULL << 6)
#define RP_WORKFLOW_PHASE_RECEIPT        (1ULL << 7)
#define RP_WORKFLOW_PHASE_ALL            ((1ULL << 8) - 1)

struct rp_workflow_handoff {
	uint magic;
	uint version;
	uint64 start_ms;
	uint64 ready_ms;
	uint64 steady_start_ms;
	uint64 phase_mask;
	uint64 guard;
};

static uint64 workflow_handoff_mix(uint64 hash, uint64 value)
{
	for (int i = 0; i < 8; i++) {
		hash ^= value & 0xff;
		hash *= 1099511628211ULL;
		value >>= 8;
	}
	return hash;
}

static uint64 workflow_handoff_guard(const struct rp_workflow_handoff *record)
{
	uint64 hash = 1469598103934665603ULL;

	hash = workflow_handoff_mix(hash, record->magic);
	hash = workflow_handoff_mix(hash, record->version);
	hash = workflow_handoff_mix(hash, record->start_ms);
	hash = workflow_handoff_mix(hash, record->ready_ms);
	hash = workflow_handoff_mix(hash, record->steady_start_ms);
	return workflow_handoff_mix(hash, record->phase_mask);
}

static int write_workflow_handoff(int fd, uint64 start_ms, uint64 ready_ms,
				  uint64 phase_mask)
{
	struct rp_workflow_handoff record;
	const char *bytes = (const char *)&record;
	int written = 0;

	memset(&record, 0, sizeof(record));
	record.magic = RP_WORKFLOW_HANDOFF_MAGIC;
	record.version = RP_WORKFLOW_HANDOFF_VERSION;
	record.start_ms = start_ms;
	record.ready_ms = ready_ms;
	record.steady_start_ms = 0;
	record.phase_mask = phase_mask;
	record.guard = workflow_handoff_guard(&record);
	while (written < (int)sizeof(record)) {
		int n = write(fd, bytes + written, sizeof(record) - written);

		if (n <= 0)
			return 0;
		written += n;
	}
	return 1;
}

static int read_workflow_completion(int fd,
				    struct rp_workflow_handoff *record)
{
	char *bytes = (char *)record;
	char extra;
	int received = 0;

	memset(record, 0, sizeof(*record));
	while (received < (int)sizeof(*record)) {
		int n = read(fd, bytes + received, sizeof(*record) - received);

		if (n <= 0) {
			close(fd);
			return 0;
		}
		received += n;
	}
	if (read(fd, &extra, 1) != 0) {
		close(fd);
		return 0;
	}
	close(fd);
	return record->magic == RP_WORKFLOW_COMPLETION_MAGIC &&
	       record->version == RP_WORKFLOW_HANDOFF_VERSION &&
	       record->phase_mask == RP_WORKFLOW_PHASE_ALL &&
	       record->guard == workflow_handoff_guard(record) &&
	       record->ready_ms >= record->start_ms &&
	       record->steady_start_ms >= record->ready_ms;
}

static int record_workflow_timing(
	const struct rp_workflow_handoff *record, uint64 workflow_end)
{
	char line[640];
	uint64 setup_elapsed;
	uint64 exec_elapsed;
	uint64 steady_elapsed;
	uint64 workflow_elapsed;

	if (workflow_end < record->steady_start_ms)
		return 0;
	setup_elapsed = record->ready_ms - record->start_ms;
	exec_elapsed = record->steady_start_ms - record->ready_ms;
	steady_elapsed = workflow_end - record->steady_start_ms;
	workflow_elapsed = workflow_end - record->start_ms;
	rp_copy_text(line, sizeof(line),
		     "schema=guest_workflow_timing_v3;clock=monotonic_mtime_ms;entry=rp_agentos_orch;handoff=delegated_pipe_v1;init_phase_mask=");
	rp_append_uint_text(line, sizeof(line), record->phase_mask);
	rp_append_text(line, sizeof(line),
		       ";completion=parent_wait_final_validation;completion_phase_mask=3;start_ms=");
	rp_append_uint_text(line, sizeof(line), record->start_ms);
	rp_append_text(line, sizeof(line), ";ready_ms=");
	rp_append_uint_text(line, sizeof(line), record->ready_ms);
	rp_append_text(line, sizeof(line), ";steady_start_ms=");
	rp_append_uint_text(line, sizeof(line), record->steady_start_ms);
	rp_append_text(line, sizeof(line), ";end_ms=");
	rp_append_uint_text(line, sizeof(line), workflow_end);
	rp_append_text(line, sizeof(line), ";setup_elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), setup_elapsed);
	rp_append_text(line, sizeof(line), ";exec_elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), exec_elapsed);
	rp_append_text(line, sizeof(line), ";steady_elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), steady_elapsed);
	rp_append_text(line, sizeof(line), ";workflow_elapsed_ms=");
	rp_append_uint_text(line, sizeof(line), workflow_elapsed);
	rp_append_text(line, sizeof(line), "\n");
	return rp_write_file("rp_workflow_timing", line);
}

static void make_echo(struct agent_op *op, uint64 request_id,
		      const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = AGENT_TOOL_ECHO;
	op->request_id = request_id;
	op->arg0 = request_id;
	op->arg1 = request_id + 1;
	strcpy(op->payload, payload);
}

static void make_kernel_op(struct agent_op *op, int tool_id,
			   uint64 request_id, const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool_id;
	op->request_id = request_id;
	if (payload)
		strcpy(op->payload, payload);
}

static int text_contains(const char *text, const char *needle)
{
	int n = strlen(needle);

	if (n == 0)
		return 1;
	for (int i = 0; text[i]; i++)
		if (strncmp(text + i, needle, n) == 0)
			return 1;
	return 0;
}

static int seed_meta(int fid, const char *physical, const char *label,
		     const char *type, const char *state,
		     const char *summary, uint64 deps)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = fid;
	strcpy(meta.physical_name, physical);
	strcpy(meta.logical_path, physical);
	strcpy(meta.project, "lab-gene-x");
	strcpy(meta.workflow, "nightly-regression");
	strcpy(meta.run_id, "RUN-042");
	strcpy(meta.stage, label);
	strcpy(meta.kind, type);
	strcpy(meta.status, state);
	strcpy(meta.summary, summary);
	meta.dependency_mask = deps;
	meta.flags = AGENT_FILE_META_F_PERSIST;
	return agent_file_meta_set(&meta);
}

static int seed_research_metadata(void)
{
	if (seed_meta(1, "r42align", "align", "artifact", "ok",
		      "align output is ready",
		      agent_dependency_label_bit("analyze") |
			      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(2, "r42anlz", "analyze", "status", "ok",
		      "analysis completed from align",
		      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(3, "r42report", "report", "report", "ok",
		      "report artifact ready",
		      agent_dependency_label_bit("archive")) < 0)
		return -1;
	if (seed_meta(4, "r42archive", "archive", "artifact", "pending",
		      "archive waits for report", 0) < 0)
		return -1;
	return 0;
}

static int verify_kernel_dependency_path(void)
{
	int hint_count;
	int target_hits = 0;

	make_kernel_op(&orch_op, AGENT_TOOL_DEPENDENCY_UPDATE, 9011,
		       "source=align;target=report;namespace=lab-gene-x;run_id=RUN-042");
	if (agent_run(&orch_op, &orch_result, 1, 0) != 1 ||
	    orch_result.status != AGENT_STATUS_OK) {
		printf("rp_agentos_orch: dependency_update_failed status=%d\n",
		       orch_result.status);
		return -1;
	}

	make_kernel_op(&orch_op, AGENT_TOOL_DEPENDENCY_QUERY, 9012,
		       "label=align;namespace=lab-gene-x;run_id=RUN-042");
	if (agent_run(&orch_op, &orch_result, 1, 0) != 1 ||
	    orch_result.status != AGENT_STATUS_OK ||
	    !text_contains(orch_result.result, "report")) {
		printf("rp_agentos_orch: dependency_query_failed status=%d result=%s\n",
		       orch_result.status, orch_result.result);
		return -1;
	}

	memset(&orch_file_query, 0, sizeof(orch_file_query));
	orch_file_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	orch_file_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(orch_file_query.project, "lab-gene-x");
	strcpy(orch_file_query.run_id, "RUN-042");
	strcpy(orch_file_query.stage, "align");
	if (agent_file_query(&orch_file_query, &orch_file_query_result) < 1 ||
	    orch_file_query_result.returned < 1 ||
	    orch_file_query_result.used_index != 1 ||
	    orch_file_query_result.plan != AGENT_FILE_QUERY_PLAN_STAGE_INDEX) {
		printf("rp_agentos_orch: metadata_query_failed hits=%d index=%d plan=%d\n",
		       orch_file_query_result.returned,
		       orch_file_query_result.used_index,
		       orch_file_query_result.plan);
		return -1;
	}
	memset(&orch_metadata_hit, 0, sizeof(orch_metadata_hit));
	for (int i = 0; i < orch_file_query_result.returned; i++) {
		struct agent_file_hit *hit = &orch_file_query_result.hits[i];

		if (hit->fid == 1 &&
		    strcmp(hit->physical_name, "r42align") == 0 &&
		    strcmp(hit->logical_path, "r42align") == 0 &&
		    strcmp(hit->stage, "align") == 0 &&
		    strcmp(hit->kind, "artifact") == 0 &&
		    strcmp(hit->status, "ok") == 0 &&
		    strcmp(hit->summary, "align output is ready") == 0) {
			orch_metadata_hit = *hit;
			target_hits++;
		}
	}
	if (target_hits != 1) {
		printf("rp_agentos_orch: metadata_target_failed matches=%d\n",
		       target_hits);
		return -1;
	}

	hint_count = agent_file_prefetch_snapshot(
		orch_prefetch_hints, AGENT_FILE_PREFETCH_MAX_HINTS);
	if (hint_count < 1 ||
	    (orch_prefetch_hints[0].reason &
	     AGENT_FILE_PREFETCH_REASON_DEPENDENCY) == 0) {
		printf("rp_agentos_orch: prefetch_hint_failed count=%d reason=%d\n",
		       hint_count, (int)orch_prefetch_hints[0].reason);
		return -1;
	}

	if (!rp_append_file("rp_agentos_kernel",
			    "dependency_update=generic_record\n"
			    "dependency_query=generic_record\n"
			    "metadata_query=stage_index\n"
			    "prefetch_hint=dependency_driven\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=entry_dependency;dependency_graph=kernel_records;status=ready")) {
		return -1;
	}
	if (!rp_append_file("rp_tool", "tool=agentos.dependency_update"))
		return -1;
	if (!rp_append_file("rp_tool", "tool=agentos.dependency_query"))
		return -1;
	if (!rp_append_file("rp_tool", "tool=agentos.file_prefetch"))
		return -1;
	return 0;
}

static int echo_result_matches(const struct agent_op *op,
			       const struct agent_result *result)
{
	return result->version == AGENT_CALL_VERSION &&
	       result->status == AGENT_STATUS_OK &&
	       result->tool_id == op->tool_id &&
	       result->request_id == op->request_id && result->sequence != 0 &&
	       result->value0 == (uint64)strlen(op->payload) &&
	       result->value1 == op->arg0 && result->value2 == op->arg1 &&
	       strcmp(result->result, op->payload) == 0;
}

static struct agent_context_record *find_echo_context(
	int context_records, const struct agent_op *op,
	const struct agent_result *result)
{
	for (int i = 0; i < context_records; i++) {
		struct agent_context_record *record = &orch_records[i];

		if (record->sequence == result->sequence &&
		    record->request_id == op->request_id &&
		    record->tool_id == op->tool_id &&
		    record->status == result->status &&
		    record->arg0 == op->arg0 &&
		    record->value0 == result->value0 &&
		    record->value1 == result->value1 &&
		    record->value2 == result->value2 && record->record_hash != 0 &&
		    strcmp(record->payload, op->payload) == 0 &&
		    strcmp(record->result, result->result) == 0)
			return record;
	}
	return 0;
}

static struct agent_provenance_edge *find_echo_edge(
	int edge_count, const struct agent_context_record *source,
	const struct agent_context_record *target)
{
	int self_pid = getpid();

	for (int i = 0; i < edge_count; i++) {
		struct agent_provenance_edge *edge = &orch_provenance_edges[i];

		if (edge->kind == AGENT_PROVENANCE_EDGE_CONTEXT &&
		    edge->source_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->target_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->source_pid == self_pid && edge->target_pid == self_pid &&
		    edge->source_sequence == source->sequence &&
		    edge->target_sequence == target->sequence &&
		    edge->source_record_hash == source->record_hash &&
		    edge->target_record_hash == target->record_hash &&
		    edge->source_record_hash != 0 && edge->target_record_hash != 0 &&
		    edge->tool_id == AGENT_TOOL_ECHO &&
		    edge->status == AGENT_STATUS_OK)
			return edge;
	}
	return 0;
}

static int record_functional_acceptance(
	int context_records, const struct agent_op *echo_op,
	const struct agent_result *echo_result,
	const struct agent_context_record *echo_context,
	const struct agent_context_record *followup_context,
	int timeline_count, int edge_count,
	const struct agent_provenance_edge *echo_edge)
{
	char *body = orch_acceptance_body;

	rp_copy_text(body, sizeof(orch_acceptance_body),
		     "schema=agentos_task6_acceptance_v2;module_count=4\n"
		     "module=context;operation=context_snapshot;status=verified;records=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), context_records);
	rp_append_text(body, sizeof(orch_acceptance_body), ";latest_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_header.latest_sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";request_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_context->request_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";tool_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_context->tool_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";record_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_context->sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";record_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_context->record_hash);
	rp_append_text(body, sizeof(orch_acceptance_body), ";payload=");
	rp_append_text(body, sizeof(orch_acceptance_body), echo_context->payload);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result=");
	rp_append_text(body, sizeof(orch_acceptance_body), echo_context->result);
	rp_append_text(body, sizeof(orch_acceptance_body), ";followup_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), followup_context->sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";followup_record_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), followup_context->record_hash);
	rp_append_text(body, sizeof(orch_acceptance_body),
		       "\nmodule=structured_tool;operation=agent_run_echo;status=verified;request_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->request_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";tool_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->tool_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";request_payload=");
	rp_append_text(body, sizeof(orch_acceptance_body), echo_op->payload);
	rp_append_text(body, sizeof(orch_acceptance_body), ";arg0=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->arg0);
	rp_append_text(body, sizeof(orch_acceptance_body), ";arg1=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_op->arg1);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_version=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->version);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_status=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->status);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_tool_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->tool_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_request_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->request_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_payload=");
	rp_append_text(body, sizeof(orch_acceptance_body), echo_result->result);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_value0=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->value0);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_value1=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->value1);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_value2=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->value2);
	rp_append_text(body, sizeof(orch_acceptance_body), ";result_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_result->sequence);
	rp_append_text(body, sizeof(orch_acceptance_body),
		       "\nmodule=metadata_query;operation=file_query_stage_index;status=verified;project=lab-gene-x;run_id=RUN-042;stage=align;returned=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_file_query_result.returned);
	rp_append_text(body, sizeof(orch_acceptance_body), ";used_index=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_file_query_result.used_index);
	rp_append_text(body, sizeof(orch_acceptance_body), ";plan=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_file_query_result.plan);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_fid=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.fid);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_physical=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.physical_name);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_stage=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.stage);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_kind=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.kind);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_status=");
	rp_append_text(body, sizeof(orch_acceptance_body), orch_metadata_hit.status);
	rp_append_text(body, sizeof(orch_acceptance_body),
		       "\nmodule=observation;operation=timeline_provenance_ledger;status=verified;timeline_records=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), timeline_count);
	rp_append_text(body, sizeof(orch_acceptance_body), ";provenance_edges=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), edge_count);
	rp_append_text(body, sizeof(orch_acceptance_body), ";ledger_records=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_ledger.visible_records);
	rp_append_text(body, sizeof(orch_acceptance_body), ";ledger_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), orch_ledger.ledger_hash);
	rp_append_text(body, sizeof(orch_acceptance_body), ";edge_kind=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->kind);
	rp_append_text(body, sizeof(orch_acceptance_body), ";edge_tool_id=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->tool_id);
	rp_append_text(body, sizeof(orch_acceptance_body), ";edge_status=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->status);
	rp_append_text(body, sizeof(orch_acceptance_body), ";source_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->source_sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_sequence=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->target_sequence);
	rp_append_text(body, sizeof(orch_acceptance_body), ";source_record_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->source_record_hash);
	rp_append_text(body, sizeof(orch_acceptance_body), ";target_record_hash=");
	rp_append_uint_text(body, sizeof(orch_acceptance_body), echo_edge->target_record_hash);
	rp_append_text(body, sizeof(orch_acceptance_body), "\n");
	return rp_write_file("rp_agentos_acceptance", body);
}

static int run_research_orchestrator(int timing_read_fd, int timing_write_fd,
				     int completion_write_fd,
				     uint64 workflow_start)
{
	int timeline_count;
	int edge_count;
	int info_ret;
	struct agent_context_record *echo_context;
	struct agent_context_record *followup_context;
	struct agent_provenance_edge *echo_edge;
	uint64 phase_mask = RP_WORKFLOW_PHASE_AGENT_CREATE;

	info_ret = agent_info(&orch_info);
	if (info_ret < 0 || !orch_info.is_agent ||
	    orch_info.agent_role != AGENT_ROLE_ORCHESTRATOR) {
		printf("rp_agentos_orch: not_orchestrator_agent ret=%d is_agent=%d role=%d caps=%p\n",
		       info_ret, orch_info.is_agent, orch_info.agent_role,
		       (void *)orch_info.capability_mask);
		return 1;
	}
	phase_mask |= RP_WORKFLOW_PHASE_AGENT_INFO;

	if (agent_file_meta_init() < 0) {
		printf("rp_agentos_orch: file_meta_init_failed\n");
		return 1;
	}
	if (seed_research_metadata() < 0) {
		printf("rp_agentos_orch: seed_metadata_failed\n");
		return 1;
	}
	phase_mask |= RP_WORKFLOW_PHASE_METADATA;

	make_echo(&orch_echo_op, 9001, "rp-agentos-orch");
	make_echo(&orch_followup_op, 9002, "rp-agentos-orch-confirm");
	if (agent_run(&orch_echo_op, &orch_echo_result, 1, 0) != 1 ||
	    !echo_result_matches(&orch_echo_op, &orch_echo_result) ||
	    agent_run(&orch_followup_op, &orch_followup_result, 1, 0) != 1 ||
	    !echo_result_matches(&orch_followup_op, &orch_followup_result) ||
	    orch_followup_result.sequence <= orch_echo_result.sequence) {
		printf("rp_agentos_orch: agent_run_semantics_failed status=%d/%d\n",
		       orch_echo_result.status, orch_followup_result.status);
		return 1;
	}
	phase_mask |= RP_WORKFLOW_PHASE_TOOL;

	int snapshot = context_snapshot(&orch_header, orch_records, 4);
	echo_context = find_echo_context(snapshot, &orch_echo_op,
					 &orch_echo_result);
	followup_context = find_echo_context(snapshot, &orch_followup_op,
					     &orch_followup_result);
	if (snapshot < 2 || echo_context == 0 || followup_context == 0 ||
	    followup_context->cause_sequence != echo_context->sequence ||
	    orch_header.latest_sequence < followup_context->sequence) {
		printf("rp_agentos_orch: context_snapshot_failed n=%d\n",
		       snapshot);
		return 1;
	}
	phase_mask |= RP_WORKFLOW_PHASE_CONTEXT;
	timeline_count = agent_timeline_snapshot(orch_timeline_records, 4);
	edge_count = agent_provenance_snapshot(orch_provenance_edges, 4);
	echo_edge = find_echo_edge(edge_count, echo_context, followup_context);
	if (timeline_count < 1 || edge_count < 1 || echo_edge == 0 ||
	    agent_ledger_snapshot(&orch_ledger) < 0 ||
	    orch_ledger.visible_records < 2 || orch_ledger.ledger_hash == 0) {
		printf("rp_agentos_orch: provenance_snapshot_failed timeline=%d edges=%d\n",
		       timeline_count, edge_count);
		return 1;
	}
	phase_mask |= RP_WORKFLOW_PHASE_OBSERVATION;

	if (!rp_write_file("rp_agentos_kernel",
			   "target=agentos_ucore\n"
			   "mode=kernel_agent_orchestrated\n"
			   "agent_role=orchestrator\n"
			   "agent_context=present\n"
			   "agent_run=echo\n"
			   "context_snapshot=present\n"
			   "agent_timeline=observed\n"
			   "agent_provenance=observed\n"
			   "agent_ledger=observed\n"
			   "provenance_kernel=observed\n"
			   "file_meta_service=initialized\n"
			   "research_platform=rp_orch\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_agentos_mainflow",
			   "stage=entry;context_trusted=kernel_shadow;status=ready\n")) {
		return 1;
	}
	if (!rp_append_status("agentos_kernel=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.agent_run.echo")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.context_snapshot")) return 1;
	if (!rp_append_file("rp_tool", "tool=agentos.file_meta_init")) return 1;
	if (verify_kernel_dependency_path() < 0) return 1;
	phase_mask |= RP_WORKFLOW_PHASE_FILE_QUERY;
	if (!record_functional_acceptance(
		    snapshot, &orch_echo_op, &orch_echo_result, echo_context,
		    followup_context, timeline_count, edge_count, echo_edge)) {
		printf("rp_agentos_orch: acceptance_receipt_failed\n");
		return 1;
	}
	phase_mask |= RP_WORKFLOW_PHASE_RECEIPT;

	printf("rp_agentos_orch: agent role=%d context=%p size=%p latest=%d\n",
	       orch_info.agent_role, (void *)orch_info.context_base,
	       (void *)orch_info.context_size, (int)orch_header.latest_sequence);

	char timing_arg[48];
	char completion_arg[48];
	uint64 ready_ms = get_mtime();

	if (phase_mask != RP_WORKFLOW_PHASE_ALL || ready_ms < workflow_start ||
	    !write_workflow_handoff(timing_write_fd, workflow_start, ready_ms,
				    phase_mask)) {
		printf("rp_agentos_orch: workflow_handoff_failed\n");
		return 1;
	}
	close(timing_write_fd);
	rp_copy_text(timing_arg, sizeof(timing_arg), RP_WORKFLOW_HANDOFF_PREFIX);
	rp_append_uint_text(timing_arg, sizeof(timing_arg), timing_read_fd);
	rp_copy_text(completion_arg, sizeof(completion_arg),
		     RP_WORKFLOW_COMPLETION_PREFIX);
	rp_append_uint_text(completion_arg, sizeof(completion_arg),
			    completion_write_fd);
	char *argv[] = {
		"rp_orch",
		timing_arg,
		completion_arg,
		0,
	};
	if (exec("rp_orch", argv) < 0) {
		printf("rp_agentos_orch: exec_failed program=rp_orch\n");
		return 1;
	}
	return 1;
}

int main(void)
{
	int64 workflow_start = get_mtime();
	int timing_pipe[2];
	int completion_pipe[2];
	struct rp_workflow_handoff completion;

	if (workflow_start < 0 || pipe(timing_pipe) < 0) {
		printf("rp_agentos_orch: workflow_pipe_failed\n");
		return 1;
	}
	if (pipe(completion_pipe) < 0) {
		close(timing_pipe[0]);
		close(timing_pipe[1]);
		printf("rp_agentos_orch: completion_pipe_failed\n");
		return 1;
	}
	if (agent_scope_delegate_fd(timing_pipe[0]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(timing_pipe[1]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(completion_pipe[0]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(completion_pipe[1]) != AGENT_STATUS_OK) {
		close(timing_pipe[0]);
		close(timing_pipe[1]);
		close(completion_pipe[0]);
		close(completion_pipe[1]);
		printf("rp_agentos_orch: workflow_pipe_delegate_failed\n");
		return 1;
	}
	int pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	if (pid < 0) {
		close(timing_pipe[0]);
		close(timing_pipe[1]);
		close(completion_pipe[0]);
		close(completion_pipe[1]);
		printf("rp_agentos_orch: create_orchestrator_failed\n");
		return 1;
	}
	if (pid == 0) {
		close(completion_pipe[0]);
		return run_research_orchestrator(
			timing_pipe[0], timing_pipe[1], completion_pipe[1],
			(uint64)workflow_start);
	}
	close(timing_pipe[0]);
	close(timing_pipe[1]);
	close(completion_pipe[1]);

	int code = -1;
	int got = waitpid(pid, &code);
	if (got != pid) {
		close(completion_pipe[0]);
		printf("rp_agentos_orch: wait_failed pid=%d got=%d\n", pid, got);
		return 1;
	}
	if (code != 0) {
		close(completion_pipe[0]);
		printf("rp_agentos_orch: child_failed code=%d\n", code);
		return 1;
	}
	if (!read_workflow_completion(completion_pipe[0], &completion) ||
	    completion.start_ms != (uint64)workflow_start) {
		printf("rp_agentos_orch: completion_handoff_failed\n");
		return 1;
	}
	if (!rp_file_contains("rp_agentos_kernel", "status=ready") ||
	    !rp_file_contains("rp_agentcmp",
			      "evidence_role=runtime_verified") ||
	    !rp_file_contains("rp_agentcmp", "status=verified") ||
	    !rp_file_contains("rp_agentos_acceptance",
			      "schema=agentos_task6_acceptance_v2")) {
		printf("rp_agentos_orch: state_check_failed\n");
		return 1;
	}
	uint64 workflow_end = get_mtime();
	if (workflow_end < completion.steady_start_ms ||
	    !record_workflow_timing(&completion, workflow_end)) {
		printf("rp_agentos_orch: workflow_timing_failed\n");
		return 1;
	}
	printf("rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready\n");
	printf("rp_agentos_orch: passed\n");
	return 0;
}
